"""
轻抖 API 客户端封装。

依据已审查文档：
- 鉴权通过 Header: `x-api-key`
- 创建任务: POST /web/api/commitGetTextTask
- 查询结果: GET /web/api/getTaskResult?batchId=...
- 时长字段: result.list[*].videoTime（单位：秒）
- 文案提取为异步模式，需要轮询 task/result
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from ssl import CERT_NONE
from typing import Any
from urllib.parse import urlparse

import httpx
import redis

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class QingdouTaskResult:
    batch_id: int
    task_id: int | None
    status_code: int
    status_msg: str
    video_time: int | None
    video_content: str | None
    content_source: str | None
    raw: dict[str, Any]


class QingdouError(RuntimeError):
    pass


class QingdouBusyError(QingdouError):
    pass


class QingdouTimeoutError(QingdouError):
    pass


class QingdouDurationExceededError(QingdouError):
    pass


_LOCK_KEY = "video-plan:qingdou:extract-lock"
_SLOTS_KEY = "video-plan:qingdou:extract-slots"


def _redis_url() -> str:
    return (settings.REDIS_URL or settings.CELERY_BROKER_URL or "").strip()


@lru_cache(maxsize=1)
def _redis_client() -> redis.Redis:
    url = _redis_url()
    kwargs: dict[str, Any] = {
        "socket_connect_timeout": 10,
        "socket_timeout": 10,
        "decode_responses": True,
    }
    if url.startswith("rediss://"):
        kwargs["ssl_cert_reqs"] = CERT_NONE
    return redis.Redis.from_url(url, **kwargs)


@contextmanager
def _qingdou_serial_lock(*, ttl_seconds: int):
    if not settings.QINGDOU_SERIAL_LOCK_ENABLED:
        yield
        return

    token = str(uuid.uuid4())
    client = _redis_client()
    deadline = time.monotonic() + max(1, settings.QINGDOU_LOCK_WAIT_TIMEOUT_SECONDS)
    acquired = False

    while time.monotonic() < deadline:
        try:
            acquired = bool(client.set(_LOCK_KEY, token, nx=True, ex=max(30, ttl_seconds)))
        except Exception as exc:
            # Never bypass this lock: Qingdou rejects concurrent submissions
            # for the same account with status code 4001.
            logger.error("轻抖串行锁不可用，已阻止本次提交以避免上游并发: %s", exc)
            raise QingdouTimeoutError("轻抖串行锁不可用，任务将自动重试") from exc
        if acquired:
            logger.info("轻抖串行锁已获取")
            break
        time.sleep(2)

    if not acquired:
        raise QingdouTimeoutError("等待轻抖解析锁超时，请稍后重试")

    try:
        yield
    finally:
        try:
            if client.get(_LOCK_KEY) == token:
                client.delete(_LOCK_KEY)
                logger.info("轻抖串行锁已释放")
        except Exception:
            logger.exception("释放轻抖串行锁失败")


@contextmanager
def _qingdou_concurrency_slot(*, ttl_seconds: int):
    max_concurrent = max(1, int(settings.QINGDOU_MAX_CONCURRENT_TASKS or 1))
    if settings.QINGDOU_SERIAL_LOCK_ENABLED:
        with _qingdou_serial_lock(ttl_seconds=ttl_seconds):
            yield
        return

    token = str(uuid.uuid4())
    client = _redis_client()
    deadline = time.monotonic() + max(1, settings.QINGDOU_LOCK_WAIT_TIMEOUT_SECONDS)
    acquired = False

    while time.monotonic() < deadline:
        now = time.time()
        expire_before = now - max(30, ttl_seconds)
        try:
            with client.pipeline() as pipe:
                pipe.watch(_SLOTS_KEY)
                pipe.zremrangebyscore(_SLOTS_KEY, 0, expire_before)
                active_count = pipe.zcard(_SLOTS_KEY)
                if active_count < max_concurrent:
                    pipe.multi()
                    pipe.zadd(_SLOTS_KEY, {token: now})
                    pipe.expire(_SLOTS_KEY, max(30, ttl_seconds))
                    pipe.execute()
                    acquired = True
                    logger.info(
                        "轻抖并发槽已获取 active=%d/%d",
                        active_count + 1,
                        max_concurrent,
                    )
                    break
                pipe.unwatch()
        except redis.WatchError:
            time.sleep(0.2)
            continue
        except Exception as exc:
            logger.error("轻抖并发槽不可用，已阻止本次提交以避免上游并发: %s", exc)
            raise QingdouTimeoutError("轻抖并发槽不可用，任务将自动重试") from exc
        time.sleep(2)

    if not acquired:
        raise QingdouTimeoutError(f"等待轻抖并发槽超时，当前上限 {max_concurrent}，请稍后重试")

    try:
        yield
    finally:
        try:
            client.zrem(_SLOTS_KEY, token)
            logger.info("轻抖并发槽已释放")
        except Exception:
            logger.exception("释放轻抖并发槽失败")


class QingdouClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        self.api_key = (api_key or settings.QINGDOU_API_KEY or "").strip()
        self.base_url = (base_url or settings.QINGDOU_BASE_URL or "").strip().rstrip("/")
        if not self.api_key:
            raise ValueError("QINGDOU_API_KEY 不能为空")
        if not self.base_url:
            raise ValueError("QINGDOU_BASE_URL 不能为空")

        parsed = urlparse(self.base_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"QINGDOU_BASE_URL 非法: {self.base_url}")

        self._client = httpx.Client(
            timeout=httpx.Timeout(settings.LLM_REQUEST_TIMEOUT),
            headers={
                "x-api-key": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "QingdouClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _request_with_retry(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        max_attempts = max(1, int(settings.QINGDOU_HTTP_MAX_RETRIES or 1))
        backoff = max(0.1, float(settings.QINGDOU_HTTP_RETRY_BACKOFF_SECONDS or 0.1))
        retry_statuses = {408, 429, 500, 502, 503, 504}
        last_exc: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                resp = self._client.request(method, url, **kwargs)
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code not in retry_statuses or attempt >= max_attempts:
                    raise
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                if attempt >= max_attempts:
                    raise

            sleep_seconds = min(30.0, backoff * (2 ** (attempt - 1)))
            logger.warning(
                "轻抖 HTTP 调用失败，自动重试 %d/%d: %s",
                attempt,
                max_attempts,
                last_exc,
            )
            time.sleep(sleep_seconds)

        if last_exc:
            raise last_exc
        raise QingdouError("轻抖 HTTP 调用失败")

    def commit_get_text_task(self, source_url: str) -> int:
        payload = {
            "userInputList": [
                {
                    "numberIndex": 0,
                    "url": source_url,
                }
            ]
        }
        resp = self._request_with_retry("POST", self.base_url, json=payload)
        data = resp.json()
        self._assert_ok(data, endpoint="commitGetTextTask")
        batch_id = self._extract_batch_id(data)
        if batch_id is None:
            raise QingdouError(f"创建任务成功但未返回 batchId: {data}")
        return batch_id

    def get_task_result(self, batch_id: int) -> QingdouTaskResult:
        url = self._result_url()
        resp = self._request_with_retry("GET", url, params={"batchId": batch_id})
        data = resp.json()
        self._assert_ok(data, endpoint="getTaskResult")
        result = data.get("result") or {}
        items = result.get("list") or []
        first = items[0] if items else {}
        video_content = first.get("videoContent") or None
        content_source = "videoContent" if video_content else None
        if not video_content:
            fallback_text = self._extract_xiaohongshu_note_text(first)
            if fallback_text:
                video_content = fallback_text
                content_source = "xiaohongshu.videoTitle"
        return QingdouTaskResult(
            batch_id=int(result.get("batchId") or batch_id),
            task_id=self._safe_int(first.get("taskId")),
            status_code=self._safe_int((data.get("status") or {}).get("code"), default=-1),
            status_msg=str((data.get("status") or {}).get("msg") or ""),
            video_time=self._safe_int(first.get("videoTime")),
            video_content=video_content,
            content_source=content_source,
            raw=data,
        )

    def poll_text_result(
        self,
        batch_id: int,
        *,
        timeout_seconds: int | None = None,
        poll_interval_seconds: float = 2.0,
        max_video_duration: int | None = None,
    ) -> QingdouTaskResult:
        timeout_seconds = timeout_seconds or 60
        deadline = time.monotonic() + timeout_seconds

        while True:
            if time.monotonic() >= deadline:
                raise QingdouTimeoutError(f"batchId={batch_id} 轮询超时，仍未返回结果")

            result = self.get_task_result(batch_id)
            batch_status = self._safe_int((result.raw.get("result") or {}).get("batchStatus"))
            items = (result.raw.get("result") or {}).get("list") or []
            first = items[0] if items else None
            item_status = self._safe_int((first or {}).get("status")) if first else None

            if result.video_time is not None and max_video_duration is not None:
                if result.video_time > max_video_duration:
                    raise QingdouDurationExceededError(
                        f"视频时长 {result.video_time}s 超过限制 {max_video_duration}s"
                    )

            if batch_status == 0 or item_status in (None, 0):
                time.sleep(poll_interval_seconds)
                continue

            if result.content_source == "xiaohongshu.videoTitle" and result.video_content:
                logger.info(
                    "轻抖小红书结果未返回 videoContent，已使用 videoTitle 笔记正文兜底 batchId=%s status=%s batchStatus=%s",
                    batch_id,
                    item_status,
                    batch_status,
                )
                return result

            if item_status == 1000 or batch_status == 1000:
                if not result.video_content:
                    raise QingdouError(f"轻抖任务完成但未返回 videoContent: {result.raw}")
                return result

            if batch_status is not None and batch_status not in (0, 1000):
                raise QingdouError(
                    f"轻抖任务失败 batchId={batch_id}, batchStatus={batch_status}, raw={result.raw}"
                )

            if item_status is not None and item_status not in (0, 1000):
                raise QingdouError(
                    f"轻抖任务失败 batchId={batch_id}, status={item_status}, raw={result.raw}"
                )

            time.sleep(poll_interval_seconds)

    def extract_text(
        self,
        source_url: str,
        *,
        timeout_seconds: int | None = None,
        poll_interval_seconds: float = 3.0,
        max_video_duration: int | None = None,
    ) -> QingdouTaskResult:
        batch_id = self._commit_with_busy_retry(source_url)
        return self.poll_text_result(
            batch_id,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            max_video_duration=max_video_duration,
        )

    def _commit_with_busy_retry(self, source_url: str) -> int:
        max_attempts = max(1, settings.QINGDOU_BUSY_MAX_ATTEMPTS)
        sleep_seconds = max(1.0, settings.QINGDOU_BUSY_RETRY_SECONDS)
        for attempt in range(1, max_attempts + 1):
            try:
                return self.commit_get_text_task(source_url)
            except QingdouBusyError:
                if attempt >= max_attempts:
                    raise
                logger.info(
                    "轻抖当前仍有处理中任务，等待后重试 commit (%d/%d)",
                    attempt,
                    max_attempts,
                )
                time.sleep(sleep_seconds)
        raise QingdouBusyError("轻抖任务繁忙，重试后仍无法提交")

    def _result_url(self) -> str:
        if self.base_url.endswith("commitGetTextTask"):
            return self.base_url.replace("commitGetTextTask", "getTaskResult")
        return f"{self.base_url}/../getTaskResult"

    @staticmethod
    def _extract_batch_id(data: dict[str, Any]) -> int | None:
        result = data.get("result")
        if isinstance(result, dict):
            batch_id = result.get("batchId")
            if batch_id is not None:
                try:
                    return int(batch_id)
                except (TypeError, ValueError):
                    return None
        elif result is not None:
            try:
                return int(result)
            except (TypeError, ValueError):
                return None
        for key in ("batchId", "batch_id"):
            if key in data and data[key] is not None:
                try:
                    return int(data[key])
                except (TypeError, ValueError):
                    return None
        return None

    @staticmethod
    def _safe_int(value: Any, default: int | None = None) -> int | None:
        try:
            if value is None:
                return default
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _extract_xiaohongshu_note_text(item: dict[str, Any]) -> str | None:
        platform = str(item.get("platformName") or item.get("platform") or "").strip().lower()
        if "xiaohongshu" not in platform and "小红书" not in platform:
            return None

        raw_text = item.get("videoTitle")
        if not isinstance(raw_text, str):
            return None
        text = raw_text.replace("\r\n", "\n").replace("\r", "\n").replace("||", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        lines: list[str] = []
        seen: set[str] = set()
        for line in text.splitlines():
            normalized = line.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            lines.append(normalized)
        cleaned = "\n".join(lines).strip()
        return cleaned if len(cleaned) >= 10 else None

    @staticmethod
    def _extract_item_status(data: dict[str, Any]) -> int | None:
        result = data.get("result") or {}
        items = result.get("list") or []
        if not items:
            return None
        first = items[0] or {}
        return QingdouClient._safe_int(first.get("status"))

    @staticmethod
    def _assert_ok(data: dict[str, Any], *, endpoint: str) -> None:
        status = data.get("status") or {}
        code = status.get("code")
        if str(code) != "1001" and code != 1001:
            if str(code) == "4001":
                raise QingdouBusyError(f"{endpoint} 返回繁忙: {data}")
            raise QingdouError(f"{endpoint} 返回失败: {data}")


def extract_video_text_with_qingdou(
    source_url: str,
    *,
    timeout_seconds: int | None = None,
    poll_interval_seconds: float = 3.0,
    max_video_duration: int | None = None,
) -> tuple[str, int | None, QingdouTaskResult]:
    """一站式获取视频文案，返回 (text, duration, raw_result)。"""
    lock_ttl = max(
        settings.QINGDOU_LOCK_TTL_SECONDS,
        int(timeout_seconds or settings.QINGDOU_POLL_TIMEOUT_SECONDS) + 120,
    )
    with _qingdou_concurrency_slot(ttl_seconds=lock_ttl):
        with QingdouClient() as client:
            result = client.extract_text(
                source_url,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
                max_video_duration=max_video_duration,
            )
    text = (result.video_content or "").strip()
    if not text:
        raise QingdouError(f"轻抖任务完成但未返回 videoContent: {result.raw}")
    return text, result.video_time, result
