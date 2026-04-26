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
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

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
    raw: dict[str, Any]


class QingdouError(RuntimeError):
    pass


class QingdouTimeoutError(QingdouError):
    pass


class QingdouDurationExceededError(QingdouError):
    pass


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

    def commit_get_text_task(self, source_url: str) -> int:
        payload = {
            "userInputList": [
                {
                    "numberIndex": 0,
                    "url": source_url,
                }
            ]
        }
        resp = self._client.post(self.base_url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        self._assert_ok(data, endpoint="commitGetTextTask")
        batch_id = self._extract_batch_id(data)
        if batch_id is None:
            raise QingdouError(f"创建任务成功但未返回 batchId: {data}")
        return batch_id

    def get_task_result(self, batch_id: int) -> QingdouTaskResult:
        url = self._result_url()
        resp = self._client.get(url, params={"batchId": batch_id})
        resp.raise_for_status()
        data = resp.json()
        self._assert_ok(data, endpoint="getTaskResult")
        result = data.get("result") or {}
        items = result.get("list") or []
        first = items[0] if items else {}
        return QingdouTaskResult(
            batch_id=int(result.get("batchId") or batch_id),
            task_id=self._safe_int(first.get("taskId")),
            status_code=self._safe_int((data.get("status") or {}).get("code"), default=-1),
            status_msg=str((data.get("status") or {}).get("msg") or ""),
            video_time=self._safe_int(first.get("videoTime")),
            video_content=(first.get("videoContent") or None),
            raw=data,
        )

    def poll_text_result(
        self,
        batch_id: int,
        *,
        timeout_seconds: int | None = None,
        poll_interval_seconds: float = 3.0,
        max_video_duration: int | None = None,
    ) -> QingdouTaskResult:
        timeout_seconds = timeout_seconds or settings.LLM_REQUEST_TIMEOUT
        deadline = time.monotonic() + timeout_seconds

        while True:
            result = self.get_task_result(batch_id)
            status = self._extract_item_status(result.raw)

            if result.video_time is not None and max_video_duration is not None:
                if result.video_time > max_video_duration:
                    raise QingdouDurationExceededError(
                        f"视频时长 {result.video_time}s 超过限制 {max_video_duration}s"
                    )

            if status == 0:
                if time.monotonic() >= deadline:
                    raise QingdouTimeoutError(
                        f"batchId={batch_id} 轮询超时，仍处于处理中"
                    )
                time.sleep(poll_interval_seconds)
                continue

            if status == 1000:
                return result

            if status is None:
                raise QingdouError(f"无法识别任务状态: {result.raw}")

            raise QingdouError(
                f"轻抖任务失败 batchId={batch_id}, status={status}, raw={result.raw}"
            )

    def extract_text(
        self,
        source_url: str,
        *,
        timeout_seconds: int | None = None,
        poll_interval_seconds: float = 3.0,
        max_video_duration: int | None = None,
    ) -> QingdouTaskResult:
        batch_id = self.commit_get_text_task(source_url)
        return self.poll_text_result(
            batch_id,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            max_video_duration=max_video_duration,
        )

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
            raise QingdouError(f"{endpoint} 返回失败: {data}")


def extract_video_text_with_qingdou(
    source_url: str,
    *,
    timeout_seconds: int | None = None,
    poll_interval_seconds: float = 3.0,
    max_video_duration: int | None = None,
) -> tuple[str, int | None, QingdouTaskResult]:
    """一站式获取视频文案，返回 (text, duration, raw_result)。"""
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
