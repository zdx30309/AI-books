"""
Gravity Singularity - Celery 流水线任务

主任务 process_video 流水线：
- 轻抖解析获取视频文案
- content_engine.generate_full_book 直出全文（writer → polish → refine → format）
- Task / Chapter 用于状态回写与可观测性
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import shutil
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT, settings
from app.core.quality_metrics import measure_quality, format_report
from app.db.session import SessionLocal
from app.models import Chapter, Task, TaskStatus
from app.prompts.goal_driven_templates import get_category_meta, normalize_category
from app.services.classical_chinese import (
    BUDDHIST_SCRIPTURE_STORY_CATEGORY,
    CLASSICAL_CHINESE_CATEGORY_IDS,
    CLASSICAL_REFERENCE_REWRITE_CATEGORY,
    buddhist_scripture_final_title,
    classical_final_title,
)
from app.services.classical_reference_templates import DEFAULT_REFERENCE_STYLE_GUIDE, default_reference_template_status
from app.services.bilibili import BilibiliTranscriptError, extract_bilibili_text
from app.services.comic_series import (
    ComicGenerationPaused,
    parse_healing_comic_character_prompt,
    parse_healing_comic_panel_count,
    parse_healing_comic_source_content,
)
from app.services.comic_variants import is_healing_comic_family
from app.services.content_engine import (
    OpenAIDeliveryUncertainError,
    _finance_chapter_parts,
    _finance_chapter_titles,
    _finance_chapter_structure_issue,
    _finance_self_chapter_structure_issue,
    generate_full_book,
    normalize_final_markdown,
)
from app.services.claude import ClaudeDeliveryUncertainError, ClaudeRequestRejectedError
from app.services.deepseek import DeepSeekDeliveryUncertainError
from app.services.gemini import GeminiDeliveryUncertainError
from app.services.aigc_quality_gate import (
    AigcManualReviewRequired,
    AigcTechnicalError,
    enforce_aigc_quality_gate,
)
from app.services.qingdou import (
    QingdouBusyError,
    QingdouDeliveryUncertainError,
    QingdouDurationExceededError,
    QingdouQuotaExhaustedError,
    QingdouTimeoutError,
    extract_video_text_with_qingdou as _extract_video_text_with_qingdou_once,
)
from app.services.video_sources import (
    canonical_source_url,
    detect_video_source_platform,
    platform_label,
    resolve_bilibili_source_url,
    source_url_candidates,
)
from app.services.wechat_channels import WeChatChannelsError, extract_wechat_channels_text
from app.services.xiaohongshu import XiaohongshuPageTextError, extract_xiaohongshu_page_text
from app.services.youtube import YouTubeTranscriptError, extract_youtube_text, is_youtube_url
from app.utils.distributed_limiter import DistributedLimitError
from app.utils.paid_api_guard import (
    QUOTA_EXHAUSTED_MARKERS,
    is_quota_exhausted,
    is_transient_rate_limit,
)
from app.utils.task_dispatch_guard import acquire_task_dispatch_guard, clear_task_dispatch_guard
from app.utils.task_status import public_task_step
from celery_app import celery_app

logger = logging.getLogger(__name__)

_CHAPTER_HEADING_RE = re.compile(
    r"^(?:！！！@?|@)\s*(\d+)[.．、]\s*(.+?)\s*$",
    re.MULTILINE,
)
_BODY_SECTION_RE = re.compile(r"^(?:！！！)?@?\s*正文\s*$", re.MULTILINE)
_BOOK_TITLE_SECTION_RE = re.compile(
    r"^(?:！！！)?@?\s*(?:最终书名|三个备选书名)\s*$([\s\S]*?)(?=^(?:！！！)?@?\s*(?:最终简介|三个备选简介|目录)\s*$|^(?:！！！)?@|\Z)",
    re.MULTILINE,
)
_BOOK_TITLE_LINE_RE = re.compile(r"^\s*(?:\d+[\.、]\s*|[-*]\s*)?(.+?)\s*$")


class StaleTaskRun(RuntimeError):
    pass


def _classical_original_title_from_config(
    config: dict[str, Any],
    *,
    source_url: str = "",
    default_title: str = "古文白话",
) -> str:
    for key in ("classical_original_title", "original_title", "book_title", "title"):
        value = str(config.get(key) or "").strip()
        if value:
            return value[:120]
    filename = str(config.get("source_filename") or "").strip()
    if filename:
        stem = Path(filename).stem.strip()
        if stem:
            return stem[:120]
    if (
        source_url.startswith("manual://classical_chinese_docx/")
        or source_url.startswith("manual://classical_chinese_interleaved_docx/")
        or source_url.startswith("manual://classical_chinese_reference_rewrite_docx/")
    ):
        stem = Path(source_url.rsplit("/", 1)[-1]).stem.strip()
        if stem:
            return stem[:120]
    return default_title


_ACTIVE_RUN_STATUSES = {
    TaskStatus.PENDING,
    TaskStatus.FETCHING_TOPICS,
    TaskStatus.FILTERING_TOPICS,
    TaskStatus.GENERATING_OUTLINE,
    TaskStatus.WRITING_CHAPTERS,
    TaskStatus.FACT_CHECKING,
    TaskStatus.PROCESSING,
}
_FINANCE_INTEGRITY_CATEGORIES = frozenset(
    {
        "finance_technology",
        "finance_technology_ai_optimized",
        "finance_technology_recommendation",
    }
)
_FINANCE_INTEGRITY_MAX_ATTEMPTS = 3
_RESUME_STAGE_FILES = (
    "04_format.md",
    "03_refine.md",
    "02_polish.md",
    "01_writer.md",
)
_PAID_DELIVERY_UNCERTAIN_TYPES = (
    OpenAIDeliveryUncertainError,
    GeminiDeliveryUncertainError,
    DeepSeekDeliveryUncertainError,
    ClaudeDeliveryUncertainError,
    QingdouDeliveryUncertainError,
)
_RETRYABLE_ERROR_TOKENS = (
    "connection",
    "connecterror",
    "apiconnectionerror",
    "remoteprotocol",
    "readerror",
    "ssl",
    "eof",
    "timeout",
    "timed out",
    "模型调用超时",
    "上游接口连接中断",
    "openai 初稿生成连接失败",
    "distributedlimiterror",
    "paid api concurrency limiter",
    "concurrency limiter",
    "retryable gemini",
    "quality gate",
    "openai 请求被限流",
    "rate_limit",
    "429",
    "500",
    "502",
    "503",
    "504",
    "520",
    "521",
    "522",
    "524",
    "internalservererror",
    "server error",
    "temporar",
    "qingdouerror",
    "qingdoubusyerror",
    "qingdoutimeouterror",
    "返回繁忙",
    "轻抖串行锁不可用",
    "轻抖并发槽不可用",
    "等待轻抖解析锁超时",
    "轻抖任务失败",
    "未返回 videocontent",
    "youtubetranscripterror",
    "youtube transcript",
    "youtube 字幕",
)
_NON_RETRYABLE_ERROR_TOKENS = (
    "imageapiuncertainerror",
    "openaideliveryuncertainerror",
    "clauderequestrejectederror",
    "claudedeliveryuncertainerror",
    "paid_call_delivery_uncertain",
    "delivery uncertain",
    "automatic retry stopped to avoid duplicate billing",
    "duplicate billing",
    "insufficient_quota",
    "exceeded your current quota",
    "duration exceeded",
    "超过限制",
    "qingdouquotaexhaustederror",
    "轻抖可用次数或资源已用完",
    "轻抖资源不足",
) + QUOTA_EXHAUSTED_MARKERS

_QINGDOU_TERMINAL_BATCH_TOKENS = (
    "batchstatus=2",
    "batchstatus': 2",
    '"batchstatus": 2',
)
_QINGDOU_EMPTY_CONTENT_TOKENS = (
    "videocontent': none",
    '"videocontent": none',
    "videocontent=none",
    "未返回 videocontent",
)

# A task with this evidence cannot be made runnable by replaying the same
# message.  In particular, Qingdou's terminal ``batchStatus=2`` response with
# no ``videoContent`` means that the source itself was not transcribed; an
# automatic loop would only spend queue time and repeatedly surface the same
# failure.  Keep this separate from transport errors, which are safe to resume
# from a durable stage artifact.
_TERMINAL_INPUT_FAILURE_TOKENS = (
    "视频解析失败",
    "video parsing failed",
    "未返回可用文稿",
    "未返回 videocontent",
    "videocontent': none",
    '"videocontent": none',
    "batchstatus=2",
    "batchstatus': 2",
    '"batchstatus": 2',
    "qingdouquotaexhaustederror",
    "轻抖可用次数或资源已用完",
)
_AUTH_ERROR_RE = re.compile(
    r"(?:http|status(?:_code| code)?|error code|code)\s*[:= ]+\s*(?:401|403)\b"
    r"|\b(?:401|403)\s+(?:unauthorized|forbidden)\b"
    r"|\b(?:unauthorized|forbidden)\s+(?:401|403)\b"
)


# ============================================================
# 内部工具
# ============================================================


def _update_progress(
    db: Session,
    task: Task,
    *,
    progress: int,
    step: str,
    status: str | None = None,
    run_id: str | None = None,
) -> None:
    """统一的进度回写入口。每次都 commit 以保证 API 端 GET 立即可见。"""
    if run_id is not None:
        _ensure_active_run(db, task, run_id)
        cfg = dict(task.config or {})
        if str(cfg.get("active_run_id") or "") == run_id:
            cfg["active_run_heartbeat_at"] = datetime.now(timezone.utc).isoformat()
            task.config = cfg
    task.progress = max(0, min(100, int(progress)))
    task.current_step = step[:255]
    if status is not None:
        task.status = status
    db.commit()
    logger.info(
        "[task %s] progress=%d step=%s status=%s",
        task.id, task.progress, task.current_step, task.status,
    )


def _read_task_run_state(db: Session, task: Task) -> tuple[str, dict[str, Any]]:
    with db.no_autoflush:
        row = db.execute(
            select(Task.status, Task.config).where(Task.id == task.id)
        ).one()
    return str(row[0]), dict(row[1] or {})


def _ensure_active_run(db: Session, task: Task, run_id: str) -> None:
    current_status, current_config = _read_task_run_state(db, task)
    if current_status == TaskStatus.COMPLETED:
        raise StaleTaskRun(f"task {task.id} already completed")
    active_run_id = current_config.get("active_run_id")
    if active_run_id and active_run_id != run_id:
        raise StaleTaskRun(
            f"task {task.id} owned by newer run {active_run_id}, current run {run_id} is stale"
        )


def _claim_task_run(db: Session, task_id: str, run_id: str) -> Task:
    task_uuid = uuid.UUID(task_id)
    task = db.execute(
        select(Task).where(Task.id == task_uuid).with_for_update()
    ).scalar_one_or_none()
    if task is None:
        raise RuntimeError(f"Task {task_id} 不存在(可能在入队前被删除)")

    if task.status == TaskStatus.COMPLETED:
        return task

    cfg = dict(task.config or {})
    if task.status == TaskStatus.PAUSED or cfg.get("pause_requested"):
        cfg["pause_requested"] = False
        cfg["paused_at"] = datetime.now(timezone.utc).isoformat()
        task.config = cfg
        task.status = TaskStatus.PAUSED
        task.current_step = "已暂停，可点击继续生成从断点恢复"
        db.commit()
        raise StaleTaskRun(f"task {task.id} paused before run")

    if (
        cfg.get("generation_hold")
        or cfg.get("paid_call_delivery_uncertain")
        or cfg.get("paid_call_manual_resume_required")
    ):
        # A stale queue message may arrive while an operator hold is active.
        # Keep the task pending so the hold remains an intentional pause and
        # the operator can release it later without a manual "retry failed"
        # cycle.  Crucially, this path occurs before any provider call.
        task.status = TaskStatus.PENDING
        task.error_message = str(
            cfg.get("generation_hold_reason") or "generation is held by cost protection"
        )[:2000]
        task.current_step = str(cfg.get("generation_hold_step") or "费用保护暂停，未投递模型调用")[:255]
        db.commit()
        raise StaleTaskRun(f"task {task.id} held by cost protection")

    now = datetime.now(timezone.utc)
    not_before = _parse_iso_datetime(cfg.get("auto_retry_not_before"))
    backoff_until = _parse_iso_datetime(cfg.get("paid_api_backoff_until"))
    wait_until = max(
        (value for value in (not_before, backoff_until) if value is not None),
        default=None,
    )
    if wait_until is not None and wait_until > now:
        task.status = TaskStatus.PENDING
        task.current_step = "等待上游退避窗口结束，系统将自动续跑"
        task.error_message = str(
            cfg.get("last_auto_retry_error") or "上游暂不可用，正在等待自动退避窗口"
        )[:2000]
        db.commit()
        clear_task_dispatch_guard(str(task.id))
        raise StaleTaskRun(f"task {task.id} is inside a retry backoff window")

    if task.status not in _ACTIVE_RUN_STATUSES:
        raise StaleTaskRun(f"task {task.id} status={task.status} is not runnable")

    active_run_id = str(cfg.get("active_run_id") or "").strip()
    if task.status == TaskStatus.PROCESSING and active_run_id and active_run_id != run_id:
        raise StaleTaskRun(
            f"task {task.id} already owned by active run {active_run_id}, current run {run_id} is duplicate"
        )

    cfg["active_run_id"] = run_id
    cfg["active_run_started_at"] = datetime.now(timezone.utc).isoformat()
    cfg["active_run_heartbeat_at"] = cfg["active_run_started_at"]
    cfg.pop("auto_retry_not_before", None)
    cfg.pop("auto_retry_scheduled_by", None)
    # The task is now consuming a fresh provider attempt.  Remove stale
    # quota/backoff markers so a later ordinary transport failure is not
    # misclassified as another quota event.  Keep the attempt counter as
    # audit history.
    for key in (
        "paid_api_backoff",
        "paid_api_backoff_reason",
        "paid_api_backoff_at",
        "paid_api_backoff_error",
        "paid_api_backoff_until",
        "paid_api_backoff_last_due_at",
    ):
        cfg.pop(key, None)
    task.config = cfg
    task.status = TaskStatus.PROCESSING
    task.error_message = None
    db.commit()
    clear_task_dispatch_guard(str(task.id))
    db.refresh(task)
    return task


def _clear_task_run(task: Task, run_id: str) -> None:
    cfg = dict(task.config or {})
    if str(cfg.get("active_run_id") or "") != run_id:
        return
    cfg.pop("active_run_id", None)
    cfg.pop("active_run_started_at", None)
    cfg.pop("active_run_heartbeat_at", None)
    task.config = cfg


def _touch_active_run_heartbeat(task_id: str, run_id: str) -> bool:
    db: Session = SessionLocal()
    try:
        task = db.execute(
            select(Task).where(Task.id == uuid.UUID(task_id)).with_for_update()
        ).scalar_one_or_none()
        if task is None or task.status == TaskStatus.COMPLETED:
            return False

        cfg = dict(task.config or {})
        if str(cfg.get("active_run_id") or "") != run_id:
            return False

        cfg["active_run_heartbeat_at"] = datetime.now(timezone.utc).isoformat()
        cfg["active_run_heartbeat_source"] = "worker"
        task.config = cfg
        db.commit()
        return True
    except Exception:
        db.rollback()
        logger.warning("[task %s] active run heartbeat failed", task_id, exc_info=True)
        return True
    finally:
        db.close()


def _start_active_run_heartbeat(task_id: str, run_id: str) -> tuple[threading.Event, threading.Thread]:
    interval = max(15, int(settings.TASK_RUN_HEARTBEAT_INTERVAL_SECONDS or 60))
    stop_event = threading.Event()

    def _loop() -> None:
        while not stop_event.wait(interval):
            if not _touch_active_run_heartbeat(task_id, run_id):
                break

    thread = threading.Thread(
        target=_loop,
        name=f"task-heartbeat-{task_id[:8]}",
        daemon=True,
    )
    thread.start()
    return stop_event, thread


def _healing_comic_pause_requested(task_id: str, run_id: str) -> bool:
    pause_db: Session = SessionLocal()
    try:
        task = pause_db.get(Task, uuid.UUID(task_id))
        if task is None:
            raise ComicGenerationPaused("漫画任务已被删除")
        cfg = dict(task.config or {})
        if task.status == TaskStatus.PAUSED:
            return True
        if not bool(cfg.get("pause_requested")):
            return False
        active_run_id = str(cfg.get("active_run_id") or "").strip()
        return not active_run_id or active_run_id == run_id
    finally:
        pause_db.close()


def _mark_task_paused(
    db: Session,
    task_id: str,
    run_id: str,
    message: str = "漫画生成已暂停",
) -> Task | None:
    task = db.get(Task, uuid.UUID(task_id))
    if task is None:
        return None
    _clear_task_run(task, run_id)
    cfg = dict(task.config or {})
    cfg["pause_requested"] = False
    cfg["paused_at"] = datetime.now(timezone.utc).isoformat()
    cfg["paused_run_id"] = run_id
    cfg["last_pause_message"] = message[:1000]
    task.config = cfg
    task.status = TaskStatus.PAUSED
    task.current_step = "已暂停，可点击继续生成从断点恢复"
    task.error_message = None
    db.commit()
    db.refresh(task)
    return task


def extract_video_text_with_qingdou(
    source_url: str,
    *,
    timeout_seconds: int | None = None,
    poll_interval_seconds: float = 3.0,
    max_video_duration: int | None = None,
) -> tuple[str, int | None, Any]:
    platform = detect_video_source_platform(source_url)
    raw_candidates = source_url_candidates(source_url, platform)
    # Bilibili's canonical BV URL and its share/tracking variants identify the
    # same Qingdou extraction job. Keep alternate shapes for providers such as
    # WeChat that genuinely expose different parser endpoints, but never pay
    # twice for the same Bilibili video after a terminal provider failure.
    candidates: list[str] = []
    seen_candidate_keys: set[str] = set()
    for candidate_url in raw_candidates:
        candidate_key = (
            canonical_source_url(candidate_url)
            if platform == "bilibili"
            else (candidate_url or "").strip()
        )
        if not candidate_key or candidate_key in seen_candidate_keys:
            continue
        seen_candidate_keys.add(candidate_key)
        candidates.append(candidate_url)
    errors: list[str] = []
    for idx, candidate_url in enumerate(candidates, start=1):
        try:
            logger.info(
                "Qingdou parse candidate %d/%d platform=%s source_url=%s",
                idx,
                len(candidates),
                platform_label(platform),
                candidate_url,
            )
            return _extract_video_text_with_qingdou_once(
                candidate_url,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
                max_video_duration=max_video_duration,
            )
        except QingdouDurationExceededError:
            raise
        except (
            QingdouBusyError,
            QingdouTimeoutError,
            QingdouQuotaExhaustedError,
            QingdouDeliveryUncertainError,
        ):
            # A busy/lock timeout is account-wide. Trying another URL would
            # submit another Qingdou task and amplify status 4001/resource or
            # duplicate-billing failures.
            raise
        except Exception as exc:
            errors.append(f"{candidate_url}: {type(exc).__name__}: {exc}")
            logger.warning(
                "Qingdou parse candidate failed candidate=%d/%d source_url=%s error=%s",
                idx,
                len(candidates),
                candidate_url,
                exc,
            )

    raise RuntimeError(
        f"{platform_label(platform)} video parse failed after {len(candidates)} candidate URL(s): "
        + " | ".join(errors[-4:])
    )


def _duration_seconds_from_config(config: dict[str, Any]) -> int | None:
    for key in (
        "video_duration_seconds",
        "source_duration_seconds",
        "duration_seconds",
        "video_duration",
        "duration",
    ):
        value = config.get(key)
        if value in (None, ""):
            continue
        try:
            seconds = int(float(value))
        except (TypeError, ValueError):
            continue
        if seconds > 0:
            return seconds
    return None


def _fetch_video_text(source_url: str, config: dict[str, Any]) -> str:
    """Step 1: 获取视频纯文本文案。"""
    text, _duration = _fetch_video_text_with_duration(source_url, config)
    return text


def _fetch_video_text_with_duration(source_url: str, config: dict[str, Any]) -> tuple[str, int | None]:
    """Step 1: 获取视频纯文本文案，同时尽量保留视频时长。"""
    category = str(config.get("category") or "").strip()
    source_canonical_url = str(config.get("source_canonical_url") or canonical_source_url(source_url))
    detected_platform = detect_video_source_platform(source_canonical_url or source_url)
    source_platform = str(config.get("source_platform") or detected_platform)
    if source_platform == "unknown" and detected_platform != "unknown":
        source_platform = detected_platform
    source_platform_label = str(config.get("source_platform_label") or platform_label(source_platform))
    config_duration = _duration_seconds_from_config(config)
    cached_text = _source_text_from_config(config, task_label="fetch")
    if cached_text:
        return cached_text, config_duration

    if category in CLASSICAL_CHINESE_CATEGORY_IDS:
        raise ValueError("古文白话题材请直接粘贴文言文原文，不需要填写视频链接")

    raw_max_duration = config.get("max_video_duration")
    if raw_max_duration is None:
        raw_max_duration = settings.QINGDOU_MAX_VIDEO_DURATION
    max_duration = int(raw_max_duration) if raw_max_duration not in ("", None) else 0
    max_duration_arg = max_duration if max_duration > 0 else None
    poll_interval = float(config.get("poll_interval_seconds") or settings.QINGDOU_POLL_INTERVAL_SECONDS)
    poll_timeout = int(config.get("poll_timeout_seconds") or settings.QINGDOU_POLL_TIMEOUT_SECONDS)

    parser_url = source_canonical_url or source_url
    if source_platform == "bilibili":
        parser_url = source_url or parser_url
        if "b23.tv" in parser_url.lower():
            parser_url = resolve_bilibili_source_url(parser_url)
    if settings.YOUTUBE_TRANSCRIPT_ENABLED and (source_platform == "youtube" or is_youtube_url(parser_url)):
        try:
            logger.info("调用 YouTube 多入口字幕解析: source_url=%s", source_url)
            result = extract_youtube_text(parser_url, config=config)
            logger.info(
                "YouTube 字幕解析完成: video_id=%s provider=%s language=%s generated=%s chars=%d",
                result.video_id,
                result.provider,
                result.language,
                result.is_generated,
                len(result.text),
            )
            return result.text, config_duration
        except YouTubeTranscriptError as exc:
            if not settings.QINGDOU_API_KEY or not settings.QINGDOU_BASE_URL:
                raise
            logger.warning("YouTube 字幕解析失败，改用轻抖兜底: %s", exc)

    logger.info(
        "调用轻抖 API 解析%s视频: source_url=%s max_duration=%s",
        source_platform_label,
        parser_url,
        max_duration_arg or "unlimited",
    )
    try:
        text, duration, result = extract_video_text_with_qingdou(
            parser_url,
            timeout_seconds=poll_timeout,
            poll_interval_seconds=poll_interval,
            max_video_duration=max_duration_arg,
        )
    except Exception as exc:
        if source_platform == "wechat_channels" and not isinstance(exc, QingdouDurationExceededError):
            try:
                logger.warning(
                    "WeChat Channels Qingdou parse failed; trying configured media resolvers: %s",
                    exc,
                )
                fallback = extract_wechat_channels_text(parser_url, timeout=min(max(poll_timeout, 5), 60))
                logger.warning(
                    "WeChat Channels media resolver completed: provider=%s source_id=%s chars=%d",
                    fallback.provider,
                    fallback.source_id,
                    len(fallback.text),
                )
                return fallback.text, config_duration
            except WeChatChannelsError as resolver_exc:
                logger.warning("WeChat Channels media resolver fallback failed", exc_info=True)
                raise RuntimeError(
                    "微信视频号解析失败：轻抖未返回视频正文，备用解析源也未能拿到可转写媒体。"
                    "请配置 WECHAT_CHANNELS_TIKHUB_API_KEY、WECHAT_CHANNELS_DOWNLOADER_API_KEY "
                    "或 WECHAT_CHANNELS_GENERIC_RESOLVER_URL 后重试。"
                ) from resolver_exc
        if source_platform == "xiaohongshu" and not isinstance(exc, QingdouDurationExceededError):
            try:
                logger.warning("Xiaohongshu Qingdou parse failed; trying public page subtitle fallback: %s", exc)
                fallback = extract_xiaohongshu_page_text(parser_url, timeout=min(max(poll_timeout, 5), 60))
                logger.warning(
                    "Xiaohongshu public page fallback completed: title=%s chars=%d duration=%s subtitle=%s",
                    fallback.title,
                    len(fallback.text),
                    fallback.duration_seconds,
                    bool(fallback.subtitle_url),
                )
                return fallback.text, fallback.duration_seconds or config_duration
            except XiaohongshuPageTextError as fallback_exc:
                logger.warning("Xiaohongshu public page fallback failed", exc_info=True)
                raise RuntimeError(
                    "小红书解析失败：轻抖未返回视频正文，小红书公开页面也未提取到可用正文。"
                    f"轻抖错误：{exc}；页面兜底错误：{fallback_exc}"
                ) from fallback_exc
            except Exception as fallback_exc:
                logger.warning("Xiaohongshu public page fallback failed", exc_info=True)
                raise RuntimeError(
                    "小红书解析失败：轻抖未返回视频正文，小红书公开页面兜底也失败。"
                    f"轻抖错误：{exc}；页面兜底错误：{fallback_exc}"
                ) from fallback_exc
        if (
            source_platform == "bilibili"
            and not isinstance(exc, (QingdouDurationExceededError, QingdouQuotaExhaustedError))
            and _should_try_bilibili_fallback(exc)
        ):
            try:
                logger.warning("Bilibili Qingdou parse returned no transcript; trying Bilibili fallback: %s", exc)
                fallback = extract_bilibili_text(parser_url, config=config)
                logger.warning(
                    "Bilibili fallback completed: provider=%s bvid=%s chars=%d",
                    fallback.provider,
                    fallback.bvid,
                    len(fallback.text),
                )
                return fallback.text, getattr(fallback, "duration", None) or config_duration
            except BilibiliTranscriptError as fallback_exc:
                logger.warning("Bilibili fallback failed", exc_info=True)
                raise RuntimeError(
                    "B站解析失败：轻抖未返回视频正文，B站字幕/音频兜底也失败。"
                    f"轻抖错误：{exc}；兜底错误：{fallback_exc}"
                ) from fallback_exc
        raise
    logger.info(
        "轻抖解析完成: batch_id=%s task_id=%s duration=%s",
        result.batch_id, result.task_id, duration,
    )
    return text, duration or config_duration


def _has_resume_artifact(output_dir: Path) -> bool:
    return any(
        (output_dir / name).exists() and (output_dir / name).stat().st_size > 0
        for name in _RESUME_STAGE_FILES
    )


def _resolve_video_text_for_run(task: Task, output_dir: Path) -> tuple[str, int | None]:
    config = dict(task.config or {})
    config_duration = _duration_seconds_from_config(config)
    cached_text = _source_text_from_config(config, task_label=str(task.id))
    if cached_text:
        return cached_text, config_duration

    if _has_resume_artifact(output_dir):
        logger.info("[task %s] stage artifact exists; skip video text fetch and resume from disk", task.id)
        return "", config_duration

    return _fetch_video_text_with_duration(task.source_url, config)


def _source_text_from_config(config: dict[str, Any], *, task_label: str) -> str:
    for key in ("raw_text", "video_text", "transcript"):
        val = config.get(key)
        if val and isinstance(val, str) and val.strip():
            if _is_legacy_wechat_preview_metadata(config, val):
                logger.warning(
                    "[%s] ignore legacy WeChat Channels preview metadata cached in config['%s'] (%d chars)",
                    task_label,
                    key,
                    len(val),
                )
                continue
            logger.info("[%s] 使用 config['%s'] 提供的预解析文案 (%d 字)", task_label, key, len(val))
            return val.strip()

    source_text_path = str(config.get("source_text_path") or "").strip()
    if not source_text_path:
        return ""
    path = Path(source_text_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise ValueError(f"上传的 Word 正文文件不存在，请重新上传：{source_text_path}") from exc
    if not text:
        raise ValueError("上传的 Word 正文文件为空，请重新上传")
    logger.info("[%s] 使用 source_text_path 提供的预解析文案 (%d 字): %s", task_label, len(text), source_text_path)
    return text


def _has_usable_cached_source_text(task: Task) -> bool:
    """Return whether a task already has a durable source for generation.

    A Qingdou parse failure can remain in historical config after an operator
    or a fallback resolver has written usable ``raw_text``.  Recovery must use
    the current durable source, not let that stale evidence quarantine the
    task again.
    """

    config = dict(task.config or {})
    for key in ("raw_text", "video_text", "transcript"):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        if _is_legacy_wechat_preview_metadata(config, value):
            continue
        return True

    source_text_path = str(config.get("source_text_path") or "").strip()
    if not source_text_path:
        return False
    path = Path(source_text_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _style_notes_from_config(config: dict[str, Any], *, task_label: str) -> str:
    inline_notes = str(config.get("style_notes") or "").strip()
    style_notes_path = str(config.get("style_notes_path") or "").strip()
    default_reference_used = bool(config.get("default_style_reference_enabled"))
    if not style_notes_path:
        try:
            is_defaultable_reference_rewrite = normalize_category(str(config.get("category") or "")) == CLASSICAL_REFERENCE_REWRITE_CATEGORY
        except Exception:
            is_defaultable_reference_rewrite = False
        if not inline_notes and is_defaultable_reference_rewrite:
            status_info = default_reference_template_status()
            if status_info.enabled and status_info.text_path:
                style_notes_path = status_info.text_path
                inline_notes = DEFAULT_REFERENCE_STYLE_GUIDE.strip()
                default_reference_used = True
            else:
                return ""
        else:
            return inline_notes
    if not style_notes_path:
        return inline_notes
    path = Path(style_notes_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise ValueError(f"参考译文文件不存在，请重新上传：{style_notes_path}") from exc
    if not text:
        raise ValueError("参考译文文件为空，请重新上传")
    logger.info("[%s] 使用 style_notes_path 提供的参考译文 (%d 字): %s", task_label, len(text), style_notes_path)
    if inline_notes and default_reference_used:
        return f"{inline_notes}\n\n{text}".strip()
    return text


def _is_qingdou_quota_exhausted(value: object) -> bool:
    """Recognize Qingdou account-level resource exhaustion after wrapping."""

    normalized = f"{type(value).__name__}: {value}".lower()
    return isinstance(value, QingdouQuotaExhaustedError) or any(
        token in normalized
        for token in (
            "qingdouquotaexhaustederror",
            "轻抖可用次数或资源已用完",
            "轻抖资源不足",
            "batchstatus=-2",
            "batchstatus': -2",
            '"batchstatus": -2',
            "status=1113",
            "'status': 1113",
            '"status": 1113',
        )
    )


def _should_try_bilibili_fallback(exc: Exception) -> bool:
    text = str(exc).lower()
    # Qingdou status 1113 / batchStatus -2 means the account has no
    # extraction resources left. Do not spend another provider call (or
    # invoke the OpenAI audio fallback) while the account is exhausted.
    if _is_qingdou_quota_exhausted(exc):
        return False
    failed_batch = (
        "batchstatus=2" in text
        or "batchstatus': 2" in text
        or '"batchstatus": 2' in text
    )
    # Qingdou has returned both 1100 and 1114 for a terminal Bilibili
    # platform parse with no usable ``videoContent``.  1114 is the status
    # seen in production for older/unsupported BVs; it is still safe to use
    # the native Bilibili subtitle/audio fallback because the Qingdou task
    # has already reached a terminal result and will not be replayed.
    failed_item = any(
        token in text
        for token in (
            "status=1100",
            "'status': 1100",
            '"status": 1100',
            "status=1114",
            "'status': 1114",
            '"status": 1114',
        )
    )
    no_content = (
        "videocontent: none" in text
        or "videocontent': none" in text
        or '"videocontent": none' in text
        or "未返回 videocontent" in text
        or "未返回videocontent" in text
    )
    # Qingdou uses batchStatus=2 + item status=1100/1114 for a completed
    # platform parse failure.  It is not an in-progress/busy result, so the
    # native Bilibili subtitle/audio fallback must be allowed to run.
    if failed_batch and failed_item and no_content:
        return True
    transient_or_uncertain = (
        "qingdoubusyerror",
        "返回繁忙",
        "等待轻抖",
        "timeout",
        "timed out",
        "batchstatus=0",
        "batchstatus': 0",
        "batchstatus=2",
        "batchstatus': 2",
        "connection",
        "connect",
    )
    if any(token in text for token in transient_or_uncertain):
        return False
    return (
        ("batchstatus=-2" in text or "batchstatus': -2" in text or '"batchstatus": -2' in text)
        and ("status=1113" in text or "'status': 1113" in text or '"status": 1113' in text)
        and no_content
    )


def _is_legacy_wechat_preview_metadata(config: dict[str, Any], text: str) -> bool:
    platform = str(config.get("source_platform") or "").strip()
    if platform != "wechat_channels":
        return False
    normalized = text.strip()
    return (
        normalized.startswith("微信视频号公开视频信息")
        and "公开预览接口未返回字幕或可下载音轨" in normalized
    )


def _is_paid_delivery_uncertain(value: object) -> bool:
    """Return true when replaying a provider call could charge it twice."""

    if isinstance(value, _PAID_DELIVERY_UNCERTAIN_TYPES):
        return True
    normalized = f"{type(value).__name__}: {value}".lower()
    return any(
        marker in normalized
        for marker in (
            "paid_call_delivery_uncertain",
            "delivery uncertain",
            "duplicate billing",
            "automatic retry stopped to avoid duplicate billing",
        )
    )


def _is_paid_quota_backoff_error(value: object) -> bool:
    """Return true for a hard quota/resource block, but not an uncertain call."""

    if _is_paid_delivery_uncertain(value) or isinstance(value, ClaudeRequestRejectedError):
        return False
    return is_quota_exhausted(value) or _is_qingdou_quota_exhausted(value)


def _is_retryable_generation_error(exc: Exception) -> bool:
    # A hard quota and an uncertain paid delivery are not ordinary retries.
    # The former enters a provider backoff queue; the latter requires an
    # explicit operator/persisted-response resume.
    if (
        isinstance(exc, ClaudeRequestRejectedError)
        or _is_paid_delivery_uncertain(exc)
        or _is_paid_quota_backoff_error(exc)
    ):
        return False
    normalized = f"{type(exc).__name__}: {exc}".lower()
    if _is_terminal_qingdou_parse_error_message(normalized):
        return False
    if _is_auth_error_message(normalized):
        return False
    if isinstance(exc, AigcTechnicalError):
        return True
    if any(token in normalized for token in _NON_RETRYABLE_ERROR_TOKENS):
        return False
    return any(token in normalized for token in _RETRYABLE_ERROR_TOKENS)


def _retry_delay_seconds(retries: int) -> int:
    base = max(1, int(settings.GENERATION_TASK_RETRY_BACKOFF_SECONDS or 60))
    return min(900, base * (2 ** max(0, retries)))


def _paid_backoff_delay_seconds(retries: int = 0, *, transient_rate_limit: bool = False) -> int:
    """Return a bounded delay for provider quota/resource recovery.

    Quota failures are acknowledged by Celery and resumed by the maintenance
    queue.  Keeping the delay separate from Celery's finite retry countdown
    prevents a book from becoming permanently failed merely because a provider
    was briefly out of capacity.
    """

    if transient_rate_limit:
        configured = int(
            getattr(settings, "PAID_API_RATE_LIMIT_TTL_SECONDS", 60)
            or 60
        )
        return max(60, min(180, configured + 10))
    return max(300, min(1800, _retry_delay_seconds(max(0, int(retries or 0)))))


def _mark_task_pending_after_paid_backoff(
    task: Task,
    *,
    message: str,
    retries: int = 0,
    run_id: str | None = None,
    transient_rate_limit: bool = False,
) -> int:
    """Put a task into a resumable, time-gated pending state.

    This helper deliberately does not clear an uncertainty/hold flag.  Callers
    must classify the exception first; an uncertain paid delivery must remain a
    failed operator hold and can never enter this path.
    """

    cfg = dict(task.config or {})
    if run_id:
        _clear_task_run(task, run_id)
    attempt = int(cfg.get("paid_api_backoff_attempts") or 0) + 1
    delay = _paid_backoff_delay_seconds(
        retries,
        transient_rate_limit=transient_rate_limit or is_transient_rate_limit(message),
    )
    now = datetime.now(timezone.utc)
    cfg.update(
        paid_api_backoff=True,
        paid_api_backoff_attempts=attempt,
        paid_api_backoff_until=(now + timedelta(seconds=delay)).isoformat(),
        last_auto_retry_error=str(message or "")[:1000],
        auto_retry_attempts=int(cfg.get("auto_retry_attempts") or 0) + 1,
        auto_retry_not_before=(now + timedelta(seconds=delay)).isoformat(),
    )
    cfg.pop("auto_retry_scheduled_by", None)
    task.config = cfg
    task.status = TaskStatus.PENDING
    task.current_step = public_task_step(
        "正在继续生成",
        status=TaskStatus.PENDING,
        progress=task.progress,
    )
    # Provider/account details stay in config and logs for recovery/audit.  Do
    # not expose them through the normal task status API.
    task.error_message = None
    return delay


def _mark_task_pending_for_transient_retry(
    task: Task,
    *,
    message: str,
    retries: int = 0,
) -> int:
    """Persist a non-billing transient retry without relying on Celery ETA."""

    cfg = dict(task.config or {})
    delay = max(60, min(900, _retry_delay_seconds(max(0, int(retries or 0)))))
    now = datetime.now(timezone.utc)
    cfg.update(
        last_auto_retry_error=str(message or "")[:1000],
        auto_retry_attempts=int(cfg.get("auto_retry_attempts") or 0) + 1,
        auto_retry_not_before=(now + timedelta(seconds=delay)).isoformat(),
    )
    cfg.pop("auto_retry_scheduled_by", None)
    task.config = cfg
    task.status = TaskStatus.PENDING
    task.current_step = public_task_step(
        "正在继续生成",
        status=TaskStatus.PENDING,
        progress=task.progress,
    )
    task.error_message = None
    return delay


def _is_retryable_error_message(message: str | None) -> bool:
    normalized = (message or "").lower()
    if not normalized:
        return False
    if _is_paid_delivery_uncertain(message) or _is_paid_quota_backoff_error(message):
        return False
    if _is_terminal_qingdou_parse_error_message(normalized):
        return False
    if _is_auth_error_message(normalized):
        return False
    if any(token in normalized for token in _NON_RETRYABLE_ERROR_TOKENS):
        return False
    return any(token in normalized for token in _RETRYABLE_ERROR_TOKENS)


def _is_classical_paid_delivery_uncertain(
    config: dict[str, Any], message: str | None
) -> bool:
    """Return true when watchdog replay could duplicate an already billed call."""

    if not (
        config.get("public_domain_classical_vernacular")
        and str(config.get("generation_mode") or "") == "classical_vernacular"
    ):
        return False
    if _is_paid_delivery_uncertain(message):
        return True
    if _is_paid_quota_backoff_error(message):
        return False
    normalized = (message or "").lower()
    return _is_retryable_error_message(message) or any(
        token in normalized
        for token in (
            "connection",
            "connecterror",
            "timeout",
            "timed out",
            "remoteprotocol",
            "readerror",
            "ssl",
            "eof",
        )
    )


def _is_auth_error_message(normalized: str) -> bool:
    return "api key" in normalized or "unauthorized" in normalized or bool(_AUTH_ERROR_RE.search(normalized))


def _is_terminal_qingdou_parse_error_message(normalized: str) -> bool:
    if "qingdouerror" not in normalized and "轻抖任务失败" not in normalized:
        return _is_qingdou_quota_exhausted(normalized)
    if _is_qingdou_quota_exhausted(normalized):
        return True
    batch_failed = any(token in normalized for token in _QINGDOU_TERMINAL_BATCH_TOKENS)
    empty_content = any(token in normalized for token in _QINGDOU_EMPTY_CONTENT_TOKENS)
    return batch_failed and empty_content


def _task_failure_evidence(task: Task) -> str:
    """Return a bounded, searchable description of historical task failures.

    Older workers sometimes cleared ``error_message`` when claiming a task,
    leaving only the previous failure inside ``config``. Recovery decisions
    must still see that evidence, but must not stringify a potentially huge
    cached transcript into every watchdog query.
    """

    cfg = dict(task.config or {})
    values: list[str] = [str(task.error_message or ""), str(task.current_step or "")]
    for key in (
        "last_auto_retry_error",
        "paid_api_backoff_error",
        "qingdou_last_error",
        "qingdou_error",
        "qingdou_terminal_failure_reason",
        "qingdou_terminal_failure_message",
        "operator_force_restart_all_previous",
        "operator_restart_from_checkpoint_previous",
        "manual_resume_reason",
    ):
        value = cfg.get(key)
        if isinstance(value, (dict, list, tuple)):
            try:
                value = json.dumps(value, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                value = str(value)
        values.append(str(value or ""))
    return " ".join(values).lower()[:20_000]


def _has_terminal_input_failure(task: Task) -> bool:
    """Return true when automatic replay cannot repair the source input."""

    # The original parser result may still be present in historical config,
    # but a recovered/pasted source text is a valid new input.  Never let stale
    # Qingdou evidence quarantine a task that already has usable text.
    if _has_usable_cached_source_text(task):
        return False

    evidence = _task_failure_evidence(task)
    if not evidence or not any(token in evidence for token in _TERMINAL_INPUT_FAILURE_TOKENS):
        return False
    explicit_no_content = any(
        token in evidence
        for token in (
            "未返回可用文稿",
            "未返回 videocontent",
            "videocontent': none",
            '"videocontent": none',
            "batchstatus=2",
            "batchstatus': 2",
            '"batchstatus": 2',
            "qingdouquotaexhaustederror",
            "轻抖可用次数或资源已用完",
        )
    )
    return explicit_no_content or bool((task.config or {}).get("qingdou_terminal_failure_at"))


def _clear_terminal_input_failure_state(task: Task, *, reason: str) -> bool:
    """Clear stale source-quarantine flags after a valid source is recovered."""

    cfg = dict(task.config or {})
    had_state = any(
        cfg.get(key)
        for key in (
            "terminal_input_failure",
            "terminal_input_manual_review_at",
            "terminal_input_manual_review_reason",
        )
    )
    if not had_state:
        return False

    now = datetime.now(timezone.utc).isoformat()
    # Keep the original Qingdou fields as audit evidence, but move the active
    # quarantine markers out of the runnable-state decision.
    cfg.update(
        terminal_input_failure_recovered_at=now,
        terminal_input_failure_recovery_reason=str(reason)[:500],
    )
    for key in (
        "terminal_input_failure",
        "terminal_input_manual_review_at",
        "terminal_input_manual_review_reason",
    ):
        cfg.pop(key, None)
    task.config = cfg
    return True


def _quarantine_terminal_input_task(task: Task) -> None:
    """Move a permanently unparseable source out of the runnable states.

    A terminal Qingdou response is not a worker outage and must not be
    requeued forever.  Keeping it in ``processing`` was especially harmful:
    the stale watchdog correctly refused to replay it, but no later pass could
    explain why it was still active.  Manual review preserves the source and
    the provider evidence while making the required operator action explicit.
    """

    if _has_usable_cached_source_text(task):
        _clear_terminal_input_failure_state(
            task,
            reason="usable cached source text is available",
        )
        return

    cfg = dict(task.config or {})
    active_run_id = str(cfg.get("active_run_id") or "").strip()
    if active_run_id:
        _clear_task_run(task, active_run_id)
        cfg = dict(task.config or {})
    now = datetime.now(timezone.utc).isoformat()
    evidence = str(
        task.error_message
        or cfg.get("qingdou_terminal_failure_message")
        or cfg.get("qingdou_terminal_failure_reason")
        or "视频源没有返回可用文稿"
    )[:2000]
    cfg.update(
        terminal_input_failure=True,
        terminal_input_manual_review_at=now,
        terminal_input_manual_review_reason=evidence[:4000],
    )
    task.config = cfg
    task.status = TaskStatus.MANUAL_REVIEW
    task.progress = max(8, min(99, int(task.progress or 0)))
    task.current_step = "视频源未返回可用文稿，已转人工处理；请粘贴文稿或更换链接"
    task.error_message = evidence


def _retry_exhausted_step(message: str) -> str:
    return "暂时中断，已保留断点；系统会自动继续生成"[:255]


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _resume_progress_from_artifacts(output_dir: Path) -> tuple[int, str]:
    stage_states = (
        ("04_format.md", 90, "自动检测到卡住，断点续跑：终稿入库"),
        ("03_refine.md", 87, "自动检测到卡住，断点续跑：终稿格式化"),
        ("02_polish.md", 72, "自动检测到卡住，断点续跑：二润"),
        ("01_writer.md", 50, "自动检测到卡住，断点续跑：一润"),
    )
    for filename, progress, step in stage_states:
        path = output_dir / filename
        if path.exists() and path.stat().st_size > 0:
            return progress, step
    return 0, "自动检测到卡住，重新排队"


def _mark_task_pending_for_resume(task: Task, *, reason: str) -> None:
    output_dir = PROJECT_ROOT / "backend" / "output" / f"task_{task.id}"
    progress, step = _resume_progress_from_artifacts(output_dir)
    cfg = dict(task.config or {})
    cfg.pop("active_run_id", None)
    cfg.pop("active_run_started_at", None)
    cfg.pop("active_run_heartbeat_at", None)
    cfg.pop("auto_retry_not_before", None)
    cfg.pop("auto_retry_scheduled_by", None)
    cfg["auto_requeued_at"] = datetime.now(timezone.utc).isoformat()
    cfg["auto_requeue_reason"] = reason
    task.config = cfg
    task.status = TaskStatus.PENDING
    task.progress = progress
    task.current_step = step
    task.error_message = None


def _task_output_dir(task_id: str | uuid.UUID) -> Path:
    return PROJECT_ROOT / "backend" / "output" / f"task_{task_id}"


def _record_dispatch_metadata(task_id: str, **values: Any) -> None:
    """Best-effort dispatch telemetry that never changes task ownership."""

    try:
        with SessionLocal() as db:
            task = db.get(Task, uuid.UUID(str(task_id)))
            if task is None:
                return
            cfg = dict(task.config or {})
            cfg.update(values)
            task.config = cfg
            db.commit()
    except Exception:
        logger.warning("[task %s] dispatch telemetry write failed", task_id, exc_info=True)


def _seconds_until(value: datetime, *, now: datetime | None = None) -> int:
    current = now or datetime.now(timezone.utc)
    return max(1, int(math.ceil((value - current).total_seconds())))


def _schedule_pending_task_wakeup(
    task_id: str,
    delay_seconds: int,
    *,
    reason: str,
) -> str | None:
    """Publish a durable, lightweight wake-up for a pending task.

    The maintenance beat remains the broad recovery net.  This per-task wakeup
    is the second net: if beat/maintenance is restarted or briefly absent, the
    task itself still wakes a normal worker and asks the database-backed
    dispatcher to continue it.  The dispatcher guard and the worker run claim
    make simultaneous wakeups idempotent.
    """

    delay = max(1, int(delay_seconds or 1))
    try:
        result = celery_app.send_task(
            "tasks.resume_pending_task",
            args=[str(task_id)],
            countdown=delay,
            expires=delay + 3600,
            queue="celery",
        )
        wakeup_id = str(getattr(result, "id", "") or "") or None
        _record_dispatch_metadata(
            str(task_id),
            pending_wakeup_task_id=wakeup_id,
            pending_wakeup_scheduled_at=datetime.now(timezone.utc).isoformat(),
            pending_wakeup_delay_seconds=delay,
            pending_wakeup_reason=str(reason)[:200],
            pending_wakeup_last_error=None,
        )
        logger.info(
            "[task %s] durable pending wakeup scheduled in %ss reason=%s",
            task_id,
            delay,
            reason,
        )
        return wakeup_id
    except Exception as exc:
        # The maintenance watchdog is still authoritative when available.  A
        # broker failure here must not turn a resumable task into a terminal
        # failure or hold.
        _record_dispatch_metadata(
            str(task_id),
            pending_wakeup_last_error=f"{type(exc).__name__}: {exc}"[:1000],
            pending_wakeup_last_failed_at=datetime.now(timezone.utc).isoformat(),
        )
        logger.warning(
            "[task %s] durable pending wakeup publish failed; maintenance remains fallback",
            task_id,
            exc_info=True,
        )
        return None


def dispatch_pending_task_if_due(task_id: str) -> dict[str, Any]:
    """Wake one pending task and publish its real pipeline when it is due."""

    db: Session = SessionLocal()
    should_dispatch = False
    response: dict[str, Any] = {"task_id": str(task_id), "status": "missing", "published": 0}
    try:
        task = db.execute(
            select(Task).where(Task.id == uuid.UUID(str(task_id))).with_for_update()
        ).scalar_one_or_none()
        if task is None:
            return response
        response["status"] = str(task.status)
        if task.status != TaskStatus.PENDING:
            return response
        cfg = dict(task.config or {})
        if (
            cfg.get("generation_hold")
            or cfg.get("paid_call_delivery_uncertain")
            or cfg.get("paid_call_manual_resume_required")
            or cfg.get("pause_requested")
            or str(cfg.get("aigc_status") or "") == "manual_review"
            or bool(cfg.get("public_domain_quality_blocked"))
            or str(cfg.get("category") or "") == "aigc_review"
        ):
            return response

        now = datetime.now(timezone.utc)
        waits = [
            value
            for value in (
                _parse_iso_datetime(cfg.get("auto_retry_not_before")),
                _parse_iso_datetime(cfg.get("paid_api_backoff_until")),
            )
            if value is not None
        ]
        wait_until = max(waits, default=None)
        if wait_until is not None and wait_until > now:
            db.commit()
            _schedule_pending_task_wakeup(
                str(task.id),
                _seconds_until(wait_until, now=now),
                reason="wake-up arrived before durable retry gate",
            )
            response["wait_until"] = wait_until.isoformat()
            return response

        cfg.pop("pending_wakeup_task_id", None)
        cfg["pending_wakeup_consumed_at"] = now.isoformat()
        task.config = cfg
        db.commit()
        should_dispatch = True
    except Exception:
        db.rollback()
        logger.exception("[task %s] durable pending wakeup inspection failed", task_id)
    finally:
        db.close()

    if should_dispatch:
        response["published"] = _dispatch_task_ids(
            [str(task_id)],
            label="durable pending wakeup",
            force=True,
        )
        response["status"] = "pending"
    return response


def _dispatch_task_ids(
    task_ids: list[str],
    *,
    label: str,
    force: bool = False,
) -> int:
    """Publish pending tasks with a status check and failure-safe lease.

    Redis is only the duplicate-publish guard; the database run claim in the
    worker remains authoritative.  ``force`` is reserved for recovery after a
    stale/orphan decision and may reclaim an old lease, never a fresh one.
    """

    dispatched = 0
    seen: set[str] = set()
    for raw_task_id in task_ids:
        task_id = str(raw_task_id)
        if task_id in seen:
            continue
        seen.add(task_id)
        is_public_longform = False
        is_classical_translation = False
        deferred_wakeup_delay: int | None = None
        try:
            with SessionLocal() as dispatch_db:
                dispatch_task = dispatch_db.execute(
                    select(Task).where(Task.id == uuid.UUID(task_id)).with_for_update()
                ).scalar_one_or_none()
                if dispatch_task is None:
                    clear_task_dispatch_guard(task_id)
                    continue
                cfg = dict(dispatch_task.config or {})
                if dispatch_task.status != TaskStatus.PENDING:
                    # A worker may have claimed it between recovery selection
                    # and publication.  Never publish a second work item.
                    clear_task_dispatch_guard(task_id)
                    continue
                if (
                    cfg.get("generation_hold")
                    or cfg.get("paid_call_delivery_uncertain")
                    or cfg.get("paid_call_manual_resume_required")
                    or cfg.get("pause_requested")
                    or str(cfg.get("active_run_id") or "").strip()
                    or str(cfg.get("aigc_status") or "") == "manual_review"
                    or bool(cfg.get("public_domain_quality_blocked"))
                    or str(cfg.get("category") or "") == "aigc_review"
                ):
                    clear_task_dispatch_guard(task_id)
                    continue
                if _has_terminal_input_failure(dispatch_task):
                    _quarantine_terminal_input_task(dispatch_task)
                    dispatch_db.commit()
                    clear_task_dispatch_guard(task_id)
                    continue
                now_dt = datetime.now(timezone.utc)
                future_gates = [
                    value
                    for value in (
                        _parse_iso_datetime(cfg.get("auto_retry_not_before")),
                        _parse_iso_datetime(cfg.get("paid_api_backoff_until")),
                    )
                    if value is not None and value > now_dt
                ]
                if future_gates:
                    deferred_wakeup_delay = _seconds_until(max(future_gates), now=now_dt)
                if deferred_wakeup_delay is not None:
                    # Leave the DB lock/transaction before talking to the
                    # broker; _record_dispatch_metadata opens its own session.
                    dispatch_db.commit()
                else:
                    now = datetime.now(timezone.utc).isoformat()
                    cfg.update(
                        dispatch_last_attempt_at=now,
                        dispatch_attempts=int(cfg.get("dispatch_attempts") or 0) + 1,
                        dispatch_last_label=str(label)[:120],
                    )
                    cfg.pop("dispatch_last_error", None)
                    dispatch_task.config = cfg
                    is_public_longform = bool(cfg.get("public_domain_longform_v2"))
                    is_classical_translation = bool(
                        cfg.get("generation_mode") == "classical_vernacular"
                    )
                    dispatch_db.commit()

            if deferred_wakeup_delay is not None:
                _schedule_pending_task_wakeup(
                    task_id,
                    deferred_wakeup_delay,
                    reason=f"{label}: retry gate not reached",
                )
                continue

            acquired = acquire_task_dispatch_guard(
                task_id,
                force=force,
                stale_after_seconds=max(
                    60,
                    int(getattr(settings, "DISPATCH_GUARD_STALE_SECONDS", 900) or 900),
                ),
            )
            if not acquired:
                continue

            if is_public_longform:
                if is_classical_translation:
                    process_public_domain_longform.apply_async(
                        args=[task_id],
                        time_limit=7 * 24 * 60 * 60,
                        soft_time_limit=7 * 24 * 60 * 60 - 300,
                    )
                else:
                    process_public_domain_longform.delay(task_id)
            else:
                process_video.delay(task_id)
            dispatched += 1
            _record_dispatch_metadata(
                task_id,
                dispatch_last_published_at=datetime.now(timezone.utc).isoformat(),
                dispatch_last_error=None,
            )
        except Exception as exc:
            # A failed publish must not leave the task hidden behind a long
            # Redis lease.  It remains pending and can be picked up by the
            # next maintenance pass.
            clear_task_dispatch_guard(task_id)
            _record_dispatch_metadata(
                task_id,
                dispatch_last_error=f"{type(exc).__name__}: {exc}"[:1000],
                dispatch_last_failed_at=datetime.now(timezone.utc).isoformat(),
            )
            logger.exception("[task %s] %s dispatch failed", task_id, label)
    if task_ids:
        logger.warning("%s selected=%d published=%d", label, len(seen), dispatched)
    return dispatched


def requeue_stale_processing_tasks(*, limit: int = 500) -> int:
    if not settings.STALE_TASK_WATCHDOG_ENABLED:
        return 0

    stale_after = max(60, int(settings.STALE_PROCESSING_REQUEUE_SECONDS or 14400))
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after)
    db: Session = SessionLocal()
    tasks_to_dispatch: list[str] = []
    try:
        rows = (
            db.execute(
                select(Task)
                .where(Task.status == TaskStatus.PROCESSING)
                .with_for_update(skip_locked=True)
                .limit(max(1, limit))
            )
            .scalars()
            .all()
        )
        for task in rows:
            cfg = dict(task.config or {})
            if str(cfg.get("category") or "") == "aigc_review":
                continue
            heartbeat = _parse_iso_datetime(cfg.get("active_run_heartbeat_at")) or _parse_iso_datetime(
                cfg.get("active_run_started_at")
            )
            if heartbeat is not None and heartbeat > cutoff:
                continue
            if _has_terminal_input_failure(task):
                _quarantine_terminal_input_task(task)
                clear_task_dispatch_guard(str(task.id))
                continue
            if (
                cfg.get("generation_hold")
                or cfg.get("paid_call_delivery_uncertain")
                or cfg.get("paid_call_manual_resume_required")
                or cfg.get("pause_requested")
            ):
                continue
            _mark_task_pending_for_resume(
                task,
                reason=f"stale heartbeat_before={heartbeat.isoformat() if heartbeat else 'missing'}",
            )
            tasks_to_dispatch.append(str(task.id))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("stale task watchdog failed")
        return 0
    finally:
        db.close()

    return _dispatch_task_ids(tasks_to_dispatch, label="stale task watchdog", force=True)


def recover_retryable_failed_tasks(*, limit: int = 500) -> int:
    if not settings.FAILED_TASK_AUTO_RECOVERY_ENABLED:
        return 0

    retry_after = max(60, int(settings.FAILED_TASK_AUTO_RECOVERY_SECONDS or 600))
    max_attempts = max(1, int(settings.FAILED_TASK_AUTO_RECOVERY_MAX_ATTEMPTS or 24))
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=retry_after)
    db: Session = SessionLocal()
    tasks_to_dispatch: list[str] = []
    try:
        rows = (
            db.execute(
                select(Task)
                .where(Task.status == TaskStatus.FAILED)
                .with_for_update(skip_locked=True)
                .limit(max(1, limit))
            )
            .scalars()
            .all()
        )
        for task in rows:
            cfg = dict(task.config or {})
            terminal_input_failure = _has_terminal_input_failure(task)
            if str(cfg.get("category") or "") == "aigc_review":
                continue
            if (
                cfg.get("generation_hold")
                or cfg.get("paid_call_delivery_uncertain")
                or cfg.get("paid_call_manual_resume_required")
                or cfg.get("pause_requested")
            ):
                continue
            attempts = int(cfg.get("failed_auto_recovery_attempts") or 0)
            last_recovered_at = _parse_iso_datetime(cfg.get("failed_auto_recovered_at"))
            if attempts >= max_attempts:
                continue
            if last_recovered_at is not None and last_recovered_at > cutoff:
                continue
            message = task.error_message or cfg.get("last_auto_retry_error")
            message_text = str(message or "")
            if terminal_input_failure:
                _quarantine_terminal_input_task(task)
                clear_task_dispatch_guard(str(task.id))
                continue
            is_classical_paid_delivery = _is_classical_paid_delivery_uncertain(
                cfg, message_text
            )
            delivery_uncertain = _is_paid_delivery_uncertain(message_text) or bool(
                cfg.get("paid_call_delivery_uncertain")
            )
            if is_classical_paid_delivery or delivery_uncertain:
                # A connection error may happen after OpenAI has accepted and
                # billed the background response.  Never create a second paid
                # response from the watchdog; the user continuation route
                # resumes the persisted response id/checkpoint instead.
                cfg["paid_call_delivery_uncertain"] = True
                cfg["paid_call_manual_resume_required"] = True
                cfg["failed_auto_recovery_suppressed_at"] = datetime.now(timezone.utc).isoformat()
                task.config = cfg
                continue
            quota_backoff = _is_paid_quota_backoff_error(message_text) or (
                bool(cfg.get("paid_api_backoff"))
                and str(cfg.get("paid_api_backoff_reason") or "")
                in {"provider_quota", "qingdou_quota", "quota"}
            )
            if not quota_backoff and not _is_retryable_error_message(message_text):
                continue
            cfg["failed_auto_recovery_attempts"] = attempts + 1
            cfg["failed_auto_recovered_at"] = datetime.now(timezone.utc).isoformat()
            task.config = cfg
            if quota_backoff:
                _mark_task_pending_after_paid_backoff(
                    task,
                    message=message_text,
                    retries=attempts,
                    transient_rate_limit=is_transient_rate_limit(message_text),
                )
            else:
                _mark_task_pending_for_resume(
                    task,
                    reason=f"retryable failed auto recovery attempt={attempts + 1}/{max_attempts}",
                )
            tasks_to_dispatch.append(str(task.id))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("failed task auto recovery failed")
        return 0
    finally:
        db.close()

    return _dispatch_task_ids(tasks_to_dispatch, label="failed task auto recovery", force=True)


def revive_recoverable_terminal_input_reviews(*, limit: int = 500) -> int:
    """Return safely recovered source-text tasks to the normal queue.

    Older watchdog runs could mark a task ``manual_review`` after seeing a
    terminal Qingdou error that predated a successful transcript recovery.  A
    task with an explicit durable recovery marker and usable cached text no
    longer needs source review; it must not wait for a human click forever.
    Genuine AIGC/paid-delivery/manual holds remain untouched.
    """

    if not settings.STALE_TASK_WATCHDOG_ENABLED:
        return 0

    db: Session = SessionLocal()
    changed = 0
    tasks_to_dispatch: list[str] = []
    wakeups: list[tuple[str, int]] = []
    now = datetime.now(timezone.utc)
    try:
        rows = (
            db.execute(
                select(Task)
                .where(Task.status == TaskStatus.MANUAL_REVIEW)
                .with_for_update(skip_locked=True)
                .limit(max(1, limit))
            )
            .scalars()
            .all()
        )
        for task in rows:
            cfg = dict(task.config or {})
            if not cfg.get("terminal_input_failure"):
                continue
            if not _has_usable_cached_source_text(task):
                continue
            # Only auto-revive a task for which an operator/fallback explicitly
            # recorded source recovery.  This prevents touching unrelated old
            # manual-review books.
            if not any(
                str(cfg.get(key) or "").strip()
                for key in (
                    "source_text_recovered_at",
                    "operator_transcript_recovery_reason",
                    "operator_direct_dispatch_at",
                )
            ):
                continue
            if (
                cfg.get("generation_hold")
                or cfg.get("paid_call_delivery_uncertain")
                or cfg.get("paid_call_manual_resume_required")
                or cfg.get("pause_requested")
                or str(cfg.get("aigc_status") or "") == "manual_review"
                or str(cfg.get("category") or "") == "aigc_review"
                or bool(cfg.get("public_domain_quality_blocked"))
            ):
                continue

            _clear_terminal_input_failure_state(
                task,
                reason="durable recovered source text is available",
            )
            cfg = dict(task.config or {})
            cfg.update(
                auto_requeued_at=now.isoformat(),
                auto_requeue_reason="recoverable terminal-input review with cached source",
            )
            task.config = cfg
            task.status = TaskStatus.PENDING
            task.progress = max(15, min(95, int(task.progress or 15)))
            task.current_step = public_task_step(
                "正在继续生成",
                status=TaskStatus.PENDING,
                progress=task.progress,
            )
            task.error_message = None
            changed += 1

            waits = [
                value
                for value in (
                    _parse_iso_datetime(cfg.get("auto_retry_not_before")),
                    _parse_iso_datetime(cfg.get("paid_api_backoff_until")),
                )
                if value is not None
            ]
            wait_until = max(waits, default=None)
            if wait_until is not None and wait_until > now:
                wakeups.append((str(task.id), _seconds_until(wait_until, now=now)))
            else:
                tasks_to_dispatch.append(str(task.id))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("recoverable terminal-input review pass failed")
        return 0
    finally:
        db.close()

    for task_id, delay in wakeups:
        _schedule_pending_task_wakeup(
            task_id,
            delay,
            reason="recoverable source review was automatically released",
        )
    dispatched = _dispatch_task_ids(
        tasks_to_dispatch,
        label="recoverable source review watchdog",
        force=True,
    )
    if changed:
        logger.warning(
            "recoverable source review selected=%d published=%d delayed=%d",
            changed,
            dispatched,
            len(wakeups),
        )
    return changed


def requeue_due_pending_retry_tasks(*, limit: int = 500) -> int:
    if not settings.STALE_TASK_WATCHDOG_ENABLED:
        return 0

    now = datetime.now(timezone.utc)
    db: Session = SessionLocal()
    tasks_to_dispatch: list[str] = []
    try:
        rows = (
            db.execute(
                select(Task)
                .where(Task.status == TaskStatus.PENDING)
                .with_for_update(skip_locked=True)
                .limit(max(1, limit))
            )
            .scalars()
            .all()
        )
        for task in rows:
            cfg = dict(task.config or {})
            terminal_input_failure = _has_terminal_input_failure(task)
            if str(cfg.get("category") or "") == "aigc_review":
                continue
            if (
                cfg.get("generation_hold")
                or cfg.get("paid_call_delivery_uncertain")
                or cfg.get("paid_call_manual_resume_required")
                or cfg.get("pause_requested")
                or str(cfg.get("active_run_id") or "").strip()
            ):
                continue
            not_before = _parse_iso_datetime(cfg.get("auto_retry_not_before"))
            if not_before is None or not_before > now:
                continue
            if terminal_input_failure:
                _quarantine_terminal_input_task(task)
                clear_task_dispatch_guard(str(task.id))
                continue
            message = task.error_message or cfg.get("last_auto_retry_error")
            quota_backoff = _is_paid_quota_backoff_error(str(message or "")) or (
                bool(cfg.get("paid_api_backoff"))
                and str(cfg.get("paid_api_backoff_reason") or "")
                in {"provider_quota", "qingdou_quota", "quota"}
            )
            # Quota/resource backoff is deliberately not classified as an
            # ordinary retryable error. Once its durable time gate has elapsed,
            # however, it must be dispatched; otherwise these books remain in
            # pending forever after the provider recovers.
            if not quota_backoff and message and not _is_retryable_error_message(str(message)):
                continue
            cfg["auto_retry_redispatched_at"] = now.isoformat()
            if quota_backoff:
                cfg["paid_api_backoff_last_due_at"] = now.isoformat()
            task.config = cfg
            tasks_to_dispatch.append(str(task.id))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("pending retry watchdog failed")
        return 0
    finally:
        db.close()

    # The durable time gate is the evidence that the task is due.  Reclaim an
    # old lease as well, otherwise a literal six-hour lease left by an older
    # worker can keep an otherwise due task invisible until it expires.
    return _dispatch_task_ids(tasks_to_dispatch, label="pending retry watchdog", force=True)


def _pending_reference_time(task: Task, config: dict[str, Any]) -> datetime | None:
    """Return the newest timestamp that can explain a legitimate pending wait."""

    candidates = [
        _parse_iso_datetime(config.get(key))
        for key in (
            "dispatch_last_attempt_at",
            "dispatch_last_published_at",
            "auto_requeued_at",
            "auto_retry_redispatched_at",
            "manual_resume_at",
            "operator_released_at",
        )
    ]
    created_at = getattr(task, "created_at", None)
    if isinstance(created_at, datetime):
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        candidates.append(created_at.astimezone(timezone.utc))
    usable = [value for value in candidates if value is not None]
    return max(usable) if usable else None


def requeue_orphan_pending_tasks(*, limit: int = 500) -> int:
    """Redispatch old pending rows whose queue message or lease was lost.

    This is deliberately broader than ``requeue_due_pending_retry_tasks``:
    a broker publish can disappear without leaving retry metadata.  The grace
    period avoids racing a newly published message, the Redis force lease only
    replaces an old lease, and the worker's database run claim remains the
    final duplicate-execution barrier.
    """

    if not settings.STALE_TASK_WATCHDOG_ENABLED:
        return 0

    grace = max(60, int(settings.PENDING_TASK_ORPHAN_REQUEUE_SECONDS or 180))
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=grace)
    db: Session = SessionLocal()
    tasks_to_dispatch: list[str] = []
    try:
        rows = (
            db.execute(
                select(Task)
                .where(Task.status == TaskStatus.PENDING)
                .with_for_update(skip_locked=True)
                .limit(max(1, limit))
            )
            .scalars()
            .all()
        )
        for task in rows:
            cfg = dict(task.config or {})
            terminal_input_failure = _has_terminal_input_failure(task)
            if (
                cfg.get("generation_hold")
                or cfg.get("paid_call_delivery_uncertain")
                or cfg.get("paid_call_manual_resume_required")
                or cfg.get("pause_requested")
                or str(cfg.get("active_run_id") or "").strip()
                or str(cfg.get("aigc_status") or "") == "manual_review"
                or bool(cfg.get("public_domain_quality_blocked"))
                or str(cfg.get("category") or "") == "aigc_review"
            ):
                continue
            # A generated public-domain manuscript must be finalized by the
            # artifact recovery path; never start the paid generator again.
            if bool(cfg.get("public_domain_longform_v2")) and bool(cfg.get("generation_complete")):
                continue
            if terminal_input_failure:
                _quarantine_terminal_input_task(task)
                clear_task_dispatch_guard(str(task.id))
                continue
            not_before = _parse_iso_datetime(cfg.get("auto_retry_not_before"))
            if not_before is not None and not_before > now:
                continue
            backoff_until = _parse_iso_datetime(cfg.get("paid_api_backoff_until"))
            if backoff_until is not None and backoff_until > now:
                continue
            reference_time = _pending_reference_time(task, cfg)
            if reference_time is not None and reference_time > cutoff:
                continue
            cfg.update(
                orphan_requeue_selected_at=now.isoformat(),
                orphan_requeue_reason=(
                    f"pending without active run for at least {grace}s; "
                    f"reference={reference_time.isoformat() if reference_time else 'missing'}"
                ),
            )
            task.config = cfg
            tasks_to_dispatch.append(str(task.id))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("orphan pending watchdog failed")
        return 0
    finally:
        db.close()

    return _dispatch_task_ids(tasks_to_dispatch, label="orphan pending watchdog", force=True)


def _patch_config(task: Task, **kv: Any) -> None:
    cfg = dict(task.config or {})
    cfg.update(kv)
    task.config = cfg


def _mark_generation_hold_for_quota(task: Task, exc: object) -> None:
    """Persist the correct safety state for a paid-provider failure.

    A hard quota is resumable after the provider is verified healthy, so it is
    represented as a pending backoff.  An uncertain delivery is different:
    the provider may already have billed the request, therefore it remains an
    explicit hold and the watchdog must never create a replacement request.
    """

    delivery_uncertain = _is_paid_delivery_uncertain(exc)
    quota_exhausted = _is_paid_quota_backoff_error(exc)
    if not delivery_uncertain and not quota_exhausted:
        return

    now = datetime.now(timezone.utc).isoformat()
    cfg = dict(task.config or {})
    cfg.update(
        paid_api_backoff=True,
        paid_api_backoff_reason=(
            "paid_delivery_uncertain"
            if delivery_uncertain
            else "qingdou_quota"
            if _is_qingdou_quota_exhausted(exc)
            else "provider_quota"
        ),
        paid_api_backoff_at=now,
        paid_api_backoff_error=f"{type(exc).__name__}: {exc}"[:1000],
    )

    if delivery_uncertain:
        response_id = str(getattr(exc, "response_id", "") or "").strip()
        cfg.update(
            generation_hold=True,
            generation_hold_reason=(
                "付费模型请求可能已经提交但结果未知，已阻止自动重试；"
                "请核对提供方响应后再继续生成"
            ),
            generation_hold_step="费用保护暂停，未再次投递模型调用",
            generation_hold_at=now,
            paid_call_delivery_uncertain=True,
            paid_call_manual_resume_required=True,
        )
        if response_id:
            cfg["paid_call_response_id"] = response_id
    else:
        # A quota denial is safe to retry only after the breaker/backoff has
        # elapsed.  Do not leave a stale hold from the old implementation on
        # otherwise resumable tasks.
        cfg.pop("generation_hold", None)
        cfg.pop("generation_hold_reason", None)
        cfg.pop("generation_hold_step", None)
        cfg.pop("generation_hold_at", None)
        cfg.pop("paid_call_delivery_uncertain", None)
        cfg.pop("paid_call_manual_resume_required", None)
    task.config = cfg


def _user_facing_generation_error(exc: Exception) -> str:
    raw = f"{type(exc).__name__}: {exc}"
    normalized = raw.lower()
    if _is_qingdou_quota_exhausted(exc):
        return "正在继续生成"
    if isinstance(exc, QingdouDeliveryUncertainError):
        return "任务暂时中断，已保留断点"
    if _is_terminal_qingdou_parse_error_message(normalized):
        return "视频解析失败：轻抖未返回可用文稿，请粘贴文稿或更换可解析链接后重试。"
    if is_quota_exhausted(exc):
        return "正在继续生成"
    if isinstance(exc, ClaudeRequestRejectedError):
        return "任务暂时中断，已保留断点"
    if isinstance(exc, ClaudeDeliveryUncertainError):
        return "任务暂时中断，已保留断点"
    if isinstance(exc, OpenAIDeliveryUncertainError) or "paid_call_delivery_uncertain" in normalized:
        return "任务暂时中断，已保留断点"
    if "rate_limit" in normalized or "429" in normalized:
        return "正在继续生成"
    if any(token in normalized for token in ("connection", "connecterror", "apiconnectionerror", "ssl", "eof", "remoteprotocol", "readerror")):
        return "上游接口连接中断，系统会自动断点重试；若重试耗尽仍失败，请稍后继续生成。"
    if "timeout" in normalized or "timed out" in normalized:
        return "模型调用超时，任务已停止。可以稍后点击继续生成重试。"
    if _is_auth_error_message(normalized):
        return "任务暂时中断，请稍后继续生成"
    return raw[:2000]


def _extract_book_title(markdown: str) -> str:
    """Pick the first generated title candidate as the book title."""
    section = _BOOK_TITLE_SECTION_RE.search(markdown or "")
    if section:
        for line in section.group(1).splitlines():
            line = line.strip()
            if not line:
                continue
            match = _BOOK_TITLE_LINE_RE.match(line)
            if match:
                title = match.group(1).strip(" -_*　")
                if title:
                    return title[:120]

    first_chapter = _CHAPTER_HEADING_RE.search(markdown or "")
    if first_chapter:
        return first_chapter.group(2).strip()[:120]
    return "未命名书稿"


def _cover_dispatch_guard_id(task_id: str) -> str:
    return f"cover:{task_id}"


def enqueue_cover_generation(
    db: Session,
    task: Task,
    *,
    force: bool = False,
    reference_mode: str = "refresh",
) -> bool:
    """Queue cover generation without blocking the completed manuscript."""
    cfg = dict(task.config or {})
    if not settings.COVER_WORKFLOW_ENABLED:
        task.cover_url = None
        cfg.update(
            cover_disabled=True,
            cover_status="disabled",
            cover_isolated=True,
            cover_isolation_reason="COVER_WORKFLOW_ENABLED=false",
            cover_error=None,
        )
        task.config = cfg
        db.commit()
        logger.info("[task %s] cover workflow isolated by COVER_WORKFLOW_ENABLED=false", task.id)
        return False

    output_dir = PROJECT_ROOT / "backend" / "output" / f"task_{task.id}"
    cover_path = output_dir / "cover.png"
    if cover_path.exists() and cover_path.stat().st_size > 0 and not force:
        task.cover_url = f"/api/v1/tasks/{task.id}/cover"
        cfg.update(
            cover_disabled=False,
            cover_status="completed",
            cover_error=None,
            cover_path=str(cover_path),
            draft_cover_path=str(output_dir / "draft_cover.png"),
            cover_style_brief_path=str(output_dir / "cover_style_brief.json"),
        )
        task.config = cfg
        db.commit()
        return False

    if cfg.get("cover_status") in {"queued", "generating"} and not force:
        return False

    cfg.update(
        cover_disabled=False,
        cover_status="queued",
        cover_error=None,
        cover_queued_at=datetime.now(timezone.utc).isoformat(),
    )
    task.config = cfg
    if force:
        task.cover_url = None
        cfg["cover_reference_mode"] = "reuse" if reference_mode == "reuse" else "refresh"
    db.commit()

    guard_id = _cover_dispatch_guard_id(str(task.id))
    try:
        if not acquire_task_dispatch_guard(guard_id, ttl_seconds=86400):
            return False
        generate_cover.delay(str(task.id), force=force)
        return True
    except Exception as exc:
        clear_task_dispatch_guard(guard_id)
        cfg = dict(task.config or {})
        cfg.update(
            cover_status="failed",
            cover_error=f"cover dispatch failed: {exc}"[:2000],
        )
        task.config = cfg
        db.commit()
        logger.exception("[task %s] cover dispatch failed", task.id)
        return False


def _cover_retry_countdown() -> int:
    return max(30, int(settings.COVER_TASK_RETRY_SECONDS or 60))


def _is_finance_integrity_category(category: str | None) -> bool:
    try:
        return normalize_category(category or "") in _FINANCE_INTEGRITY_CATEGORIES
    except Exception:
        return str(category or "").strip() in _FINANCE_INTEGRITY_CATEGORIES


def _finance_completion_integrity_issue(
    task: Task,
    markdown: str,
    output_dir: Path,
    *,
    category: str,
) -> tuple[str | None, list[tuple[int, str, str]]]:
    """Validate a finance manuscript immediately before completion is saved."""

    if not _is_finance_integrity_category(category):
        return None, []

    writer_path = output_dir / "01_writer.md"
    writer = ""
    if writer_path.exists() and writer_path.stat().st_size > 0:
        try:
            writer = writer_path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            writer = ""

    writer_titles = _finance_chapter_titles(writer) if writer else []
    writer_numbers = [number for number, _title in writer_titles]
    if writer_titles and writer_numbers != list(range(1, len(writer_numbers) + 1)):
        return (
            f"01_writer.md chapter sequence is not contiguous: {writer_numbers[:16]}",
            [],
        )

    issue = (
        _finance_chapter_structure_issue(writer, markdown)
        if writer_titles
        else _finance_self_chapter_structure_issue(markdown)
    )
    parts = _finance_chapter_parts(markdown)
    numbers = [number for number, _title, _content in parts]
    if issue:
        return issue, parts
    if not parts:
        if writer_titles:
            return "final manuscript has no strict chapter sequence", []
        # Preserve legacy non-chapter finance fixtures; the ordinary pipeline
        # may legitimately deliver a single unheaded essay when no reliable
        # chapter baseline exists.
        return None, []
    if numbers != list(range(1, len(numbers) + 1)):
        return f"final chapter sequence is not contiguous: {numbers[:16]}", parts
    short = [
        number
        for number, _title, content in parts
        if len(re.sub(r"\s+", "", content or "")) < 20
    ]
    if short:
        return f"final chapters are empty/truncated: {short[:12]}", parts
    target_chapters = int((task.config or {}).get("target_chapters") or 0)
    if target_chapters > 0 and writer_titles and len(parts) != target_chapters:
        return (
            f"final chapter count {len(parts)} does not match target {target_chapters}",
            parts,
        )
    return None, parts


def _reject_finance_completion_for_integrity(
    db: Session,
    task: Task,
    run_id: str,
    output_dir: Path,
    markdown: str,
    reason: str,
) -> dict[str, Any]:
    """Keep an incomplete final as evidence and return the task to the queue."""

    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    output_dir.mkdir(parents=True, exist_ok=True)
    rejected_path = output_dir / f"04_format_integrity_rejected_{stamp}.md"
    suffix = 1
    while rejected_path.exists():
        rejected_path = output_dir / f"04_format_integrity_rejected_{stamp}_{suffix}.md"
        suffix += 1
    try:
        rejected_path.write_text(markdown or "", encoding="utf-8")
    except OSError:
        logger.warning("[task %s] failed to save rejected final artifact", task.id, exc_info=True)

    canonical = output_dir / "04_format.md"
    if canonical.exists() and canonical.stat().st_size > 0:
        backup = output_dir / f"04_format_pre_integrity_gate_{stamp}.md"
        try:
            shutil.copy2(canonical, backup)
        except OSError:
            logger.warning("[task %s] failed to backup final artifact", task.id, exc_info=True)

    cfg = dict(task.config or {})
    attempts = int(cfg.get("integrity_recovery_attempts") or 0) + 1
    cfg.update(
        integrity_recovery_version=1,
        integrity_recovery_attempts=attempts,
        integrity_recovery_reason=str(reason)[:4000],
        integrity_recovery_rejected_at=now.isoformat(),
        integrity_recovery_source_stage=(
            "01_writer.md" if (output_dir / "01_writer.md").exists() else "available_stage"
        ),
    )
    if attempts > _FINANCE_INTEGRITY_MAX_ATTEMPTS:
        cfg["integrity_recovery_state"] = "manual_review"
        task.status = TaskStatus.MANUAL_REVIEW
        task.current_step = "财经稿完整性连续校验失败，已转人工审核"[:255]
        task.progress = max(1, min(99, int(task.progress or 0)))
    else:
        cfg["integrity_recovery_state"] = "queued"
        cfg["auto_requeued_at"] = now.isoformat()
        cfg["auto_requeue_reason"] = "finance completion integrity gate"
        task.status = TaskStatus.PENDING
        task.progress = max(45, min(89, int(task.progress or 45)))
        task.current_step = "财经稿完整性未通过，保留断点后重新排队"[:255]
    task.config = cfg
    task.error_message = str(reason)[:2000]
    _clear_task_run(task, run_id)
    try:
        (output_dir / "integrity_completion_rejection.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "task_id": str(task.id),
                    "reason": reason,
                    "attempts": attempts,
                    "state": cfg["integrity_recovery_state"],
                    "rejected_artifact": rejected_path.name,
                    "created_at": now.isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        logger.warning("[task %s] failed to save integrity rejection report", task.id, exc_info=True)
    db.commit()
    clear_task_dispatch_guard(str(task.id))
    return {
        "task_id": str(task.id),
        "status": task.status,
        "chapters": len(task.chapters),
        "integrity_reason": reason,
    }


def _persist_chapters_from_markdown(
    db: Session,
    task: Task,
    markdown: str,
    *,
    category: str | None = None,
) -> int:
    """从新引擎生成的 Markdown 中拆出章节并落库，维持旧 API 可观测性。"""
    if _is_finance_integrity_category(category):
        parts = _finance_chapter_parts(markdown)
        numbers = [number for number, _title, _content in parts]
        if not parts or numbers != list(range(1, len(numbers) + 1)):
            raise ValueError("finance manuscript has no complete strict chapter sequence")
        db.query(Chapter).filter(Chapter.task_id == task.id).delete(synchronize_session=False)
        db.flush()
        for number, title, content in parts:
            db.add(
                Chapter(
                    task_id=task.id,
                    chapter_num=number,
                    title=title[:512],
                    content=content,
                    word_count=len(content),
                )
            )
        db.commit()
        return len(parts)

    db.query(Chapter).filter(Chapter.task_id == task.id).delete(synchronize_session=False)
    db.flush()

    body_match = _BODY_SECTION_RE.search(markdown or "")
    body_offset = body_match.end() if body_match else 0
    body_text = markdown[body_offset:] if body_match else markdown
    matches = list(_CHAPTER_HEADING_RE.finditer(body_text))
    if matches:
        seen_chapter_nums: set[int] = set()
        deduped_matches = []
        for match in reversed(matches):
            chapter_num = int(match.group(1))
            if chapter_num in seen_chapter_nums:
                continue
            seen_chapter_nums.add(chapter_num)
            deduped_matches.append(match)
        matches = list(reversed(deduped_matches))
    if not matches:
        chapter = Chapter(task_id=task.id, chapter_num=1, title="全文", content=markdown, word_count=len(markdown))
        db.add(chapter)
        db.commit()
        return 1

    for idx, match in enumerate(matches):
        chapter_num = int(match.group(1))
        title = match.group(2).strip()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body_text)
        content = body_text[start:end].strip()
        chapter = Chapter(
            task_id=task.id,
            chapter_num=chapter_num or idx + 1,
            title=title,
            content=content or markdown,
            word_count=len(content or markdown),
        )
        db.add(chapter)
    db.commit()
    return len(matches)


# ============================================================
# 主任务: 视频转网文全流程
# ============================================================


@celery_app.task(
    bind=True,
    name="tasks.process_video",
    max_retries=settings.GENERATION_TASK_MAX_RETRIES,
)
def process_video(_self, task_id: str) -> dict:
    """主流水线 - 由 POST /api/v1/tasks 触发，task_id 为已建好的 Task.id。"""
    db: Session = SessionLocal()
    task: Task | None = None
    run_id = str(getattr(getattr(_self, "request", None), "id", "") or uuid.uuid4())
    heartbeat_stop: threading.Event | None = None
    heartbeat_thread: threading.Thread | None = None

    try:
        task = _claim_task_run(db, task_id, run_id)

        if task.status == TaskStatus.COMPLETED:
            return {"task_id": str(task.id), "status": TaskStatus.COMPLETED, "chapters": len(task.chapters)}

        heartbeat_stop, heartbeat_thread = _start_active_run_heartbeat(task_id, run_id)

        _update_progress(db, task, progress=1, step="任务启动", run_id=run_id)

        output_dir = PROJECT_ROOT / "backend" / "output" / f"task_{task.id}"
        output_dir.mkdir(parents=True, exist_ok=True)

        if str((task.config or {}).get("category") or "") == "general_longform":
            from app.services.general_longform_task import process_claimed_task

            return process_claimed_task(db, task, run_id, output_dir)

        input_source = str((task.config or {}).get("input_source") or "").strip()
        is_reference_article = input_source == "reference_article"
        fetch_start_step = "正在读取参考文章" if is_reference_article else "正在获取视频文案"
        _update_progress(db, task, progress=8, step=fetch_start_step, run_id=run_id)
        db.flush()
        video_text, video_duration_seconds = _resolve_video_text_for_run(task, output_dir)
        if video_text:
            duration_patch = (
                {"video_duration_seconds": int(video_duration_seconds)}
                if video_duration_seconds
                else {}
            )
            if str((task.config or {}).get("source_text_path") or "").strip():
                _patch_config(task, raw_text_chars=len(video_text), **duration_patch)
            else:
                _patch_config(task, raw_text=video_text, raw_text_chars=len(video_text), **duration_patch)
            fetch_step = (
                f"参考文章就绪 ({len(video_text)} 字符)"
                if is_reference_article
                else f"文案就绪 ({len(video_text)} 字符)"
            )
        else:
            fetch_step = "检测到阶段产物，跳过文案抓取，准备断点续跑"
        _update_progress(db, task, progress=15, step=fetch_step, run_id=run_id)

        category = normalize_category(str((task.config or {}).get("category") or "social_science"))
        category_meta = get_category_meta(category)
        classical_original_title = (
            _classical_original_title_from_config(
                task.config or {},
                source_url=task.source_url,
                default_title="" if category == BUDDHIST_SCRIPTURE_STORY_CATEGORY else "古文白话",
            )
            if category in CLASSICAL_CHINESE_CATEGORY_IDS
            else None
        )
        _patch_config(
            task,
            category=category,
            category_label=category_meta["label"],
            category_prompt_status=category_meta["prompt_status"],
        )

        is_comic_category = category == "comic_series" or is_healing_comic_family(category)
        comic_label = category_meta["label"]
        start_step = f"即将生成{comic_label}短语结构" if is_comic_category else "即将启动主模型"
        _update_progress(db, task, progress=25, step=start_step, run_id=run_id)

        _STAGE_TO_PROGRESS: dict[str, tuple[int, str]] = {
            "comic_jokes_selected": (38, "谐音笑点已通过爆笑、嘲讽与反转闸门"),
            "comic_structure_done": (45, f"{comic_label}短语结构已生成"),
            "comic_images_start": (86, f"{comic_label}配图启动"),
            "comic_image": (88, f"{comic_label}配图生成中"),
            "writer_start": (28, "OpenAI 主模型生成初稿中"),
            "writer_done": (45, "主模型初稿完成"),
            "polish_start": (50, "Gemini 一润启动"),
            "polish_done": (70, "Gemini 一润完成"),
            "refine_start": (72, "Gemini 二润中"),
            "refine_done": (85, "二润完成"),
            "format_start": (87, "DeepSeek 终稿格式化中"),
            "format_done": (90, "终稿就绪"),
            "aigc_start": (91, "朱雀终稿检测与低 AI 率质检中"),
            "aigc_done": (93, "朱雀终稿检测已通过"),
        }

        def _on_stage(stage: str, info: dict[str, Any]) -> None:
            try:
                if stage == "writer_progress":
                    chars = max(0, int(info.get("chars", 0) or 0))
                    prog = min(44, 28 + int(chars / 8000))
                    _update_progress(
                        db,
                        task,
                        progress=prog,
                        step=f"OpenAI 主模型生成初稿中（已生成 {chars:,} 字）",
                        run_id=run_id,
                    )
                    return
                if stage == "polish_chunk":
                    idx = int(info.get("idx", 0))
                    total = max(int(info.get("total", 1)), 1)
                    prog = 50 + int(20 * idx / total)
                    _update_progress(
                        db, task,
                        progress=min(prog, 70),
                        step=f"Gemini 一润 {idx}/{total} 块",
                        run_id=run_id,
                    )
                    return
                if stage == "refine_chunk":
                    idx = int(info.get("idx", 0))
                    total = max(int(info.get("total", 1)), 1)
                    prog = 72 + int(13 * idx / total)
                    _update_progress(
                        db, task,
                        progress=min(prog, 85),
                        step=f"Gemini 二润 {idx}/{total} 块",
                        run_id=run_id,
                    )
                    return
                if stage == "format_progress":
                    chars = max(0, int(info.get("chars", 0) or 0))
                    prog = min(89, 87 + int(chars / 20000))
                    _update_progress(
                        db,
                        task,
                        progress=prog,
                        step=f"DeepSeek 终稿格式化中（已生成 {chars:,} 字）",
                        run_id=run_id,
                    )
                    return
                if stage == "comic_image":
                    idx = int(info.get("idx", 0))
                    total = max(int(info.get("total", 1)), 1)
                    prog = 86 + int(4 * idx / total)
                    _update_progress(
                        db,
                        task,
                        progress=min(prog, 90),
                        step=f"{comic_label}配图 {idx}/{total} 张",
                        run_id=run_id,
                    )
                    return
                mapped = _STAGE_TO_PROGRESS.get(stage)
                if mapped is None:
                    return
                prog, step = mapped
                chars = info.get("chars")
                if stage == "format_done" and info.get("skipped_deepseek"):
                    step = f"{comic_label}终稿就绪"
                if chars:
                    step = f"{step} ({chars:,} 字)"
                _update_progress(db, task, progress=prog, step=step, run_id=run_id)
            except StaleTaskRun:
                raise
            except Exception:
                logger.exception("on_stage 回调写库失败 stage=%s", stage)

        db.flush()
        _ensure_active_run(db, task, run_id)
        db.commit()
        markdown = asyncio.run(
            generate_full_book(
                video_text,
                category=category,
                output_dir=output_dir,
                on_stage=_on_stage,
                style_reference=str((task.config or {}).get("style_reference") or "").strip() or None,
                style_notes=_style_notes_from_config(task.config or {}, task_label=str(task.id)) or None,
                classical_variant=str((task.config or {}).get("classical_variant") or "").strip() or None,
                classical_original_title=classical_original_title,
                healing_comic_panel_count=parse_healing_comic_panel_count(
                    (task.config or {}).get("comic_panel_count")
                ),
                healing_comic_character_prompt=parse_healing_comic_character_prompt(
                    (task.config or {}).get("comic_character_prompt")
                    or (task.config or {}).get("character_prompt")
                    or (task.config or {}).get("comic_theme_requirement")
                    or (task.config or {}).get("comic_requirement")
                ),
                healing_comic_source_content=parse_healing_comic_source_content(
                    (task.config or {}).get("comic_source_content")
                    or (task.config or {}).get("source_content")
                    or (task.config or {}).get("article_content")
                    or (task.config or {}).get("custom_content")
                ),
                healing_comic_should_pause=(
                    (lambda: _healing_comic_pause_requested(task_id, run_id))
                    if is_healing_comic_family(category)
                    else None
                ),
                input_source=input_source,
                source_duration_seconds=_duration_seconds_from_config(task.config or {}),
                aigc_required=bool((task.config or {}).get("aigc_required")),
                aigc_policy_version=str((task.config or {}).get("aigc_policy_version") or "") or None,
            )
        )
        _ensure_active_run(db, task, run_id)
        markdown = normalize_final_markdown(markdown, category=category)
        integrity_issue, integrity_parts = _finance_completion_integrity_issue(
            task,
            markdown,
            output_dir,
            category=category,
        )
        if integrity_issue:
            logger.error(
                "[task %s] completion blocked by finance manuscript integrity gate: %s",
                task_id,
                integrity_issue,
            )
            return _reject_finance_completion_for_integrity(
                db,
                task,
                run_id,
                output_dir,
                markdown,
                integrity_issue,
            )
        _patch_config(task, markdown_chars=len(markdown))
        aigc_report_path = output_dir / "aigc_quality_report.json"
        if aigc_report_path.exists():
            try:
                aigc_report = json.loads(aigc_report_path.read_text(encoding="utf-8"))
                _patch_config(
                    task,
                    aigc_quality_report=str(aigc_report_path),
                    aigc_max_score=int(aigc_report.get("maxScore") or 0),
                    aigc_passed=bool(aigc_report.get("passed")),
                    aigc_status=str(aigc_report.get("status") or "unknown"),
                    aigc_initial_max_score=int(aigc_report.get("initialMaxScore") or 0),
                    aigc_final_max_score=int(aigc_report.get("finalMaxScore") or 0),
                    aigc_rewrite_rounds=int(aigc_report.get("rewriteRounds") or 0),
                    aigc_quality_passed=bool(aigc_report.get("qualityPassed")),
                    aigc_report_available=True,
                )
            except (OSError, ValueError, TypeError):
                logger.warning("读取朱雀终稿报告失败: %s", aigc_report_path)
        task_aigc_required = bool((task.config or {}).get("aigc_required"))
        persist_progress = 94 if task_aigc_required else 92
        quality_progress = 96 if task_aigc_required else 94
        _update_progress(
            db,
            task,
            progress=persist_progress,
            step="全文生成完成，正在写入章节",
            run_id=run_id,
        )

        if category == BUDDHIST_SCRIPTURE_STORY_CATEGORY:
            book_title = buddhist_scripture_final_title(classical_original_title)
        elif category in CLASSICAL_CHINESE_CATEGORY_IDS:
            book_title = classical_final_title(classical_original_title)
        else:
            book_title = _extract_book_title(markdown)
        _ensure_active_run(db, task, run_id)
        chapter_count = _persist_chapters_from_markdown(
            db,
            task,
            markdown,
            category=category,
        )
        if _is_finance_integrity_category(category) and integrity_parts:
            persisted_numbers = [
                int(row.chapter_num)
                for row in db.query(Chapter)
                .filter(Chapter.task_id == task.id)
                .order_by(Chapter.chapter_num)
                .all()
            ]
            expected_numbers = [number for number, _title, _content in integrity_parts]
            if persisted_numbers != expected_numbers or chapter_count != len(expected_numbers):
                return _reject_finance_completion_for_integrity(
                    db,
                    task,
                    run_id,
                    output_dir,
                    markdown,
                    "database chapter sequence does not match final artifact",
                )
        _patch_config(task, markdown=markdown, title=book_title)
        if _is_finance_integrity_category(category):
            cfg = dict(task.config or {})
            cfg.update(
                integrity_recovery_version=1,
                integrity_recovery_state="resolved",
                integrity_recovery_resolved_at=datetime.now(timezone.utc).isoformat(),
                integrity_final_chapters=chapter_count,
            )
            task.config = cfg

        _update_progress(
            db,
            task,
            progress=quality_progress,
            step="正在写入质量报告",
            run_id=run_id,
        )
        report = measure_quality(markdown, category=category)
        report_text = format_report(report)
        report_path = output_dir / "quality_report.txt"
        report_path.write_text(report_text, encoding="utf-8")
        _patch_config(task, quality_report=str(report_path), quality_report_summary=report.total_violations)

        _update_progress(
            db,
            task,
            progress=100,
            step=f"全部完成: {chapter_count} 章已入库",
            status=TaskStatus.COMPLETED,
            run_id=run_id,
        )
        _clear_task_run(task, run_id)
        if is_comic_category:
            comic_cover_image = output_dir / "comic_images" / "comic_cover.png"
            if not comic_cover_image.exists():
                comic_cover_image = output_dir / "comic_images" / "comic_preface.png"
            if not comic_cover_image.exists():
                comic_cover_image = output_dir / "comic_images" / "comic_section_01.png"
            if not comic_cover_image.exists():
                comic_cover_image = next(
                    iter(sorted((output_dir / "comic_images").glob("comic_section_*.png"))),
                    comic_cover_image,
                )
            if comic_cover_image.exists():
                _patch_config(
                    task,
                    cover_disabled=False,
                    cover_status="completed",
                    cover_error=None,
                    cover_path=str(comic_cover_image),
                    cover_completed_at=datetime.now(timezone.utc).isoformat(),
                )
                task.cover_url = f"/api/v1/tasks/{task.id}/cover"
        db.commit()
        if not is_comic_category and settings.COVER_BOOK_TASK_GENERATION_ENABLED:
            enqueue_cover_generation(db, task, force=False)

        return {
            "task_id": str(task.id),
            "status": TaskStatus.COMPLETED,
            "chapters": chapter_count,
        }

    except StaleTaskRun as e:
        logger.warning("[task %s] stale run skipped: %s", task_id, e)
        db.rollback()
        latest = db.get(Task, uuid.UUID(task_id))
        return {
            "task_id": task_id,
            "status": latest.status if latest is not None else "stale",
            "chapters": len(latest.chapters) if latest is not None else 0,
        }

    except ComicGenerationPaused as e:
        logger.info("[task %s] healing comic generation paused: %s", task_id, e)
        db.rollback()
        paused_task = _mark_task_paused(db, task_id, run_id, str(e))
        return {
            "task_id": task_id,
            "status": TaskStatus.PAUSED,
            "chapters": len(paused_task.chapters) if paused_task is not None else 0,
        }

    except AigcManualReviewRequired as e:
        logger.warning("[task %s] AIGC quality/manual-review gate stopped delivery: %s", task_id, e)
        db.rollback()
        review_task = db.get(Task, uuid.UUID(task_id))
        if review_task is not None:
            output_dir = PROJECT_ROOT / "backend" / "output" / f"task_{review_task.id}"
            report_path = output_dir / "aigc_quality_report.json"
            cfg = dict(review_task.config or {})
            cfg.update(
                aigc_status="manual_review",
                aigc_passed=False,
                aigc_report_available=report_path.exists(),
                aigc_quality_report=str(report_path) if report_path.exists() else None,
            )
            if report_path.exists():
                try:
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                    cfg.update(
                        aigc_initial_max_score=int(report.get("initialMaxScore") or 0),
                        aigc_final_max_score=int(report.get("finalMaxScore") or 0),
                        aigc_max_score=int(report.get("finalMaxScore") or report.get("maxScore") or 0),
                        aigc_rewrite_rounds=int(report.get("rewriteRounds") or 0),
                        aigc_quality_passed=bool(report.get("qualityPassed")),
                        aigc_manual_review_reason=str(report.get("manualReviewReason") or str(e))[:4000],
                    )
                except (OSError, ValueError, TypeError):
                    logger.exception("[task %s] failed to read AIGC manual-review report", task_id)
            review_task.config = cfg
            _clear_task_run(review_task, run_id)
            review_task.status = TaskStatus.MANUAL_REVIEW
            review_task.progress = max(93, int(review_task.progress or 0))
            review_task.current_step = "朱雀与文稿质量未能同时达标，已保留 04_format.md 原稿，等待人工复核"
            review_task.error_message = str(e)[:2000]
            db.commit()
        return {
            "task_id": task_id,
            "status": TaskStatus.MANUAL_REVIEW,
            "chapters": len(review_task.chapters) if review_task is not None else 0,
        }

    except Exception as e:
        logger.exception("[task %s] 流水线失败", task_id)
        retry_countdown: int | None = None
        try:
            db.rollback()
            if task is None:
                task = db.get(Task, uuid.UUID(task_id))
            if task is not None:
                internal_message = f"{type(e).__name__}: {e}"[:2000]
                message = _user_facing_generation_error(e)
                _clear_task_run(task, run_id)
                _mark_generation_hold_for_quota(task, e)
                generation_hold = bool((task.config or {}).get("generation_hold"))
                paid_api_backoff = bool((task.config or {}).get("paid_api_backoff"))
                quota_backoff = _is_paid_quota_backoff_error(e)
                if isinstance(e, AigcTechnicalError):
                    output_dir = PROJECT_ROOT / "backend" / "output" / f"task_{task.id}"
                    report_path = output_dir / "aigc_quality_report.json"
                    report = e.report if isinstance(e.report, dict) else None
                    if report is None and report_path.exists():
                        try:
                            loaded = json.loads(report_path.read_text(encoding="utf-8"))
                            report = loaded if isinstance(loaded, dict) else None
                        except (OSError, ValueError, TypeError):
                            logger.exception("[task %s] failed to read AIGC technical report", task_id)
                    cfg = dict(task.config or {})
                    cfg.update(
                        aigc_status="technical_failed",
                        aigc_passed=False,
                        aigc_quality_passed=False,
                        aigc_report_available=bool(report or report_path.exists()),
                        aigc_quality_report=str(report_path) if report_path.exists() else None,
                    )
                    if report:
                        cfg.update(
                            aigc_initial_max_score=int(report.get("initialMaxScore") or 0),
                            aigc_final_max_score=int(report.get("finalMaxScore") or 0),
                            aigc_max_score=int(report.get("finalMaxScore") or report.get("maxScore") or 0),
                            aigc_rewrite_rounds=int(report.get("rewriteRounds") or 0),
                            aigc_manual_review_reason=str(report.get("technicalError") or message)[:4000],
                        )
                    task.config = cfg
                current_retries = int(getattr(getattr(_self, "request", None), "retries", 0) or 0)
                max_retries = max(0, int(settings.GENERATION_TASK_MAX_RETRIES or 0))
                if generation_hold:
                    # A quota or paid-delivery hold is an operator boundary,
                    # not a transient generation failure. Do not enqueue a
                    # Celery retry that can only spin on the same provider
                    # block; the task remains explicitly held for resume.
                    task.status = TaskStatus.FAILED
                    task.error_message = message[:2000]
                    task.current_step = "任务暂时中断，已保留断点"
                elif quota_backoff:
                    # A provider quota/resource denial is safe to resume after
                    # the breaker window.  Do not consume Celery's finite retry
                    # budget and do not surface a false terminal failure.
                    delay_seconds = _mark_task_pending_after_paid_backoff(
                        task,
                        message=internal_message,
                        retries=current_retries,
                        transient_rate_limit=is_transient_rate_limit(e),
                    )
                    db.commit()
                    _schedule_pending_task_wakeup(
                        task_id,
                        delay_seconds,
                        reason="paid provider backoff",
                    )
                    return {
                        "task_id": task_id,
                        "status": TaskStatus.PENDING,
                        "chapters": len(task.chapters) if task is not None else 0,
                    }
                elif paid_api_backoff and _is_retryable_generation_error(e):
                    # Provider-side quota/backoff must not consume a finite Celery retry budget.
                    # Keep the book in the normal pending queue and let the watchdog redispatch
                    # from saved artifacts after the breaker TTL expires, instead of surfacing
                    # a terminal failed state that requires manual clicks.
                    backoff_attempt = int((task.config or {}).get("paid_api_backoff_attempts") or 0) + 1
                    cfg = dict(task.config or {})
                    cfg["last_auto_retry_error"] = internal_message[:1000]
                    cfg["paid_api_backoff_attempts"] = backoff_attempt
                    cfg["auto_retry_attempts"] = current_retries + 1
                    delay_seconds = _paid_backoff_delay_seconds(
                        min(current_retries, max_retries),
                        transient_rate_limit=is_transient_rate_limit(e)
                        or is_transient_rate_limit(message),
                    )
                    cfg["paid_api_backoff_until"] = (
                        datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
                    ).isoformat()
                    cfg["auto_retry_not_before"] = (
                        datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
                    ).isoformat()
                    cfg["auto_retry_scheduled_by"] = run_id
                    task.config = cfg
                    task.status = TaskStatus.PENDING
                    task.error_message = None
                    task.current_step = public_task_step(
                        "正在继续生成",
                        status=TaskStatus.PENDING,
                        progress=task.progress,
                    )
                    task.progress = max(0, int(task.progress or 0))
                    db.commit()
                    _schedule_pending_task_wakeup(
                        task_id,
                        delay_seconds,
                        reason="provider backoff retry",
                    )
                    return {"task_id": task_id, "status": TaskStatus.PENDING, "chapters": len(task.chapters) if task is not None else 0}
                elif _is_retryable_generation_error(e) and current_retries < max_retries:
                    retry_countdown = _retry_delay_seconds(current_retries)
                    cfg = dict(task.config or {})
                    cfg["last_auto_retry_error"] = internal_message[:1000]
                    cfg["auto_retry_attempts"] = current_retries + 1
                    cfg["auto_retry_not_before"] = (
                        datetime.now(timezone.utc) + timedelta(seconds=retry_countdown)
                    ).isoformat()
                    cfg["auto_retry_scheduled_by"] = run_id
                    task.config = cfg
                    task.status = TaskStatus.PENDING
                    task.error_message = None
                    task.current_step = public_task_step(
                        "正在继续生成",
                        status=TaskStatus.PENDING,
                        progress=task.progress,
                    )
                    task.progress = max(0, int(task.progress or 0))
                else:
                    if _is_retryable_generation_error(e):
                        cfg = dict(task.config or {})
                        cfg["last_auto_retry_error"] = internal_message[:1000]
                        cfg["auto_retry_exhausted_at"] = datetime.now(timezone.utc).isoformat()
                        task.config = cfg
                    task.status = TaskStatus.FAILED
                    task.error_message = message[:2000]
                    task.current_step = (
                        _retry_exhausted_step(message) if _is_retryable_generation_error(e) else f"失败：{message}"
                    )[:255]
                db.commit()
        except Exception:
            logger.exception("[task %s] 写入失败状态时再次出错", task_id)
        if retry_countdown is not None:
            try:
                acquire_task_dispatch_guard(task_id, ttl_seconds=retry_countdown + 600)
            except Exception:
                logger.warning("[task %s] retry dispatch guard failed", task_id, exc_info=True)
            logger.warning(
                "[task %s] transient failure, auto retry in %ss (%s)",
                task_id,
                retry_countdown,
                e,
            )
            raise _self.retry(exc=e, countdown=retry_countdown)
        raise
    finally:
        if heartbeat_stop is not None:
            heartbeat_stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=2)
        db.close()


def _cover_markdown(task: Task, output_dir: Path) -> str:
    cfg = dict(task.config or {})
    markdown = str(cfg.get("markdown") or "").strip()
    if markdown:
        return markdown
    for filename in ("04_format.md", "03_refine.md", "02_polish.md", "01_writer.md"):
        path = output_dir / filename
        if path.exists() and path.stat().st_size > 0:
            return path.read_text(encoding="utf-8").strip()
    return ""


def _clear_public_domain_failure_state(config: dict[str, Any]) -> dict[str, Any]:
    """Remove stale retry/manual-review flags after a successful final gate."""

    cleaned = dict(config or {})
    cleaned["public_domain_quality_blocked"] = False
    cleaned.pop("public_domain_quality_reason", None)
    cleaned.pop("auto_requeue_reason", None)
    cleaned.pop("auto_requeued_at", None)
    for key in (
        "generation_hold",
        "generation_hold_reason",
        "generation_hold_step",
        "generation_hold_at",
        "paid_api_backoff",
        "paid_api_backoff_reason",
        "paid_api_backoff_at",
        "paid_api_backoff_error",
        "paid_api_backoff_until",
        "paid_api_backoff_last_due_at",
    ):
        cleaned.pop(key, None)
    cleaned.pop("paid_call_delivery_uncertain", None)
    cleaned.pop("paid_call_manual_resume_required", None)
    cleaned.pop("failed_auto_recovery_suppressed_at", None)
    return cleaned


@celery_app.task(
    bind=True,
    name="tasks.process_public_domain_longform",
    max_retries=0,
)
def process_public_domain_longform(_self, task_id: str) -> dict:
    """Serial, resumable 8–10-source public-domain long-form pipeline."""
    from app.services.public_domain_classical_vernacular import ClassicalTranslationPaused
    from app.services.public_domain_longform import (
        CLASSICAL_VERNACULAR_MODE,
        FULL_MODE,
        NARRATIVE_SYNTHESIS_VERSION,
        POLICY_VERSION,
        PublicDomainQualityError,
        PublicDomainValidationError,
        audit_edited_final_manuscript,
        count_text_chars,
        parse_final_markdown,
        run_public_domain_longform,
        source_from_dict,
    )

    db: Session = SessionLocal()
    task: Task | None = None
    owns_run = False
    run_id = str(getattr(getattr(_self, "request", None), "id", "") or uuid.uuid4())
    output_dir = PROJECT_ROOT / "backend" / "output" / f"task_{task_id}"
    heartbeat_stop: threading.Event | None = None
    heartbeat_thread: threading.Thread | None = None

    def classical_pause_requested() -> bool:
        pause_db: Session = SessionLocal()
        try:
            latest = pause_db.get(Task, uuid.UUID(task_id))
            if latest is None:
                return True
            latest_cfg = dict(latest.config or {})
            return latest.status == TaskStatus.PAUSED or bool(latest_cfg.get("pause_requested"))
        finally:
            pause_db.close()

    def update(progress: int, step: str, *, status: str | None = None) -> None:
        if task is None:
            return
        cfg = dict(task.config or {})
        cfg["active_run_id"] = run_id
        cfg["active_run_heartbeat_at"] = datetime.now(timezone.utc).isoformat()
        task.config = cfg
        task.progress = max(0, min(100, int(progress)))
        task.current_step = step[:255]
        if status:
            task.status = status
        db.commit()

    def clear_run() -> None:
        if task is None:
            return
        cfg = dict(task.config or {})
        for key in ("active_run_id", "active_run_started_at", "active_run_heartbeat_at"):
            cfg.pop(key, None)
        task.config = cfg
        clear_task_dispatch_guard(str(task.id))

    def persist_chapter_checkpoint(chapter) -> None:
        """Expose each durable serial chapter on the bookshelf immediately."""

        if task is None:
            return
        existing = (
            db.query(Chapter)
            .filter(
                Chapter.task_id == task.id,
                Chapter.chapter_num == int(chapter.number),
            )
            .one_or_none()
        )
        if existing is None:
            existing = Chapter(task_id=task.id, chapter_num=int(chapter.number))
            db.add(existing)
        existing.title = str(chapter.title or f"第{chapter.number}章")[:512]
        existing.content = str(chapter.text or "")
        existing.word_count = len(existing.content)
        cfg = dict(task.config or {})
        cfg.update(
            checkpoint_chapters=max(
                int(cfg.get("checkpoint_chapters") or 0),
                int(chapter.number),
            ),
            checkpoint_chars=sum(
                int(value or 0)
                for (value,) in db.query(Chapter.word_count)
                .filter(Chapter.task_id == task.id)
                .all()
            )
            + (0 if existing.id is not None else len(existing.content)),
        )
        task.config = cfg
        db.commit()

    try:
        task = db.execute(
            select(Task).where(Task.id == uuid.UUID(task_id)).with_for_update()
        ).scalar_one_or_none()
        if task is None:
            clear_task_dispatch_guard(task_id)
            return {"task_id": task_id, "status": "missing"}
        cfg = dict(task.config or {})
        if not bool(cfg.get("public_domain_longform_v2")):
            clear_task_dispatch_guard(task_id)
            return {"task_id": task_id, "status": "wrong_pipeline"}
        if task.status == TaskStatus.COMPLETED:
            clear_task_dispatch_guard(task_id)
            return {"task_id": task_id, "status": TaskStatus.COMPLETED, "chapters": len(task.chapters)}
        if task.status == TaskStatus.PAUSED:
            clear_task_dispatch_guard(task_id)
            return {"task_id": task_id, "status": TaskStatus.PAUSED, "chapters": len(task.chapters)}
        if (
            cfg.get("generation_hold")
            or cfg.get("paid_call_delivery_uncertain")
            or cfg.get("paid_call_manual_resume_required")
        ):
            task.status = TaskStatus.PENDING
            task.error_message = str(
                cfg.get("generation_hold_reason") or "generation is held by cost protection"
            )[:2000]
            task.current_step = str(
                cfg.get("generation_hold_step") or "费用保护暂停，未投递模型调用"
            )[:255]
            db.commit()
            clear_task_dispatch_guard(task_id)
            return {"task_id": task_id, "status": TaskStatus.PENDING, "chapters": len(task.chapters)}
        not_before = _parse_iso_datetime(cfg.get("auto_retry_not_before"))
        backoff_until = _parse_iso_datetime(cfg.get("paid_api_backoff_until"))
        wait_until = max(
            (value for value in (not_before, backoff_until) if value is not None),
            default=None,
        )
        if wait_until is not None and wait_until > datetime.now(timezone.utc):
            task.status = TaskStatus.PENDING
            task.current_step = "等待上游退避窗口结束，系统将自动续跑"
            db.commit()
            clear_task_dispatch_guard(task_id)
            return {"task_id": task_id, "status": TaskStatus.PENDING, "chapters": len(task.chapters)}
        for key in (
            "paid_api_backoff",
            "paid_api_backoff_reason",
            "paid_api_backoff_at",
            "paid_api_backoff_error",
            "paid_api_backoff_until",
            "paid_api_backoff_last_due_at",
        ):
            cfg.pop(key, None)
        active_run = str(cfg.get("active_run_id") or "")
        if active_run and active_run != run_id and task.status == TaskStatus.PROCESSING:
            return {"task_id": task_id, "status": "already_running"}
        cfg.update(
            active_run_id=run_id,
            active_run_started_at=datetime.now(timezone.utc).isoformat(),
            active_run_heartbeat_at=datetime.now(timezone.utc).isoformat(),
        )
        task.config = cfg
        task.status = TaskStatus.PROCESSING
        owns_run = True
        task.error_message = None
        generation_mode = str(cfg.get("generation_mode") or FULL_MODE)
        classical_profile = (
            dict(cfg.get("adaptation_profile") or {})
            if isinstance(cfg.get("adaptation_profile"), dict)
            else {}
        )
        classical_scope_mode = str(classical_profile.get("scope_mode") or "complete")
        task.current_step = (
            "正在隔离现代材料并建立匿名叙事参数；佛教事实只允许使用古代原典证据"
            if generation_mode == "research_preview"
            else "正在按选定字数试译，每个完整语义段完成即保存"
            if generation_mode == CLASSICAL_VERNACULAR_MODE and classical_scope_mode == "characters"
            else "正在按原文章节边界试译，每章完成即保存"
            if generation_mode == CLASSICAL_VERNACULAR_MODE and classical_scope_mode == "chapters"
            else "正在按原作章节顺序逐段完整翻译，每段完成即保存断点"
            if generation_mode == CLASSICAL_VERNACULAR_MODE
            else "正在验证 5–10 本来源并建立逐章串行检查点"
        )
        db.commit()
        heartbeat_stop, heartbeat_thread = _start_active_run_heartbeat(task_id, run_id)

        snapshot_path = output_dir / "00_public_domain_sources.json"
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise PublicDomainValidationError("公版来源快照格式错误")
        sources = [source_from_dict(item) for item in payload if isinstance(item, dict)]
        cfg = dict(task.config or {})
        result = run_public_domain_longform(
            sources=sources,
            work_dir=output_dir,
            target_title=str(cfg.get("target_title") or "").strip() or None,
            target_direction=str(cfg.get("target_direction") or "").strip() or None,
            target_length=int(cfg.get("target_length") or 0) or None,
            target_chapters=int(cfg.get("target_chapters") or 0) or None,
            output_style=str(cfg.get("output_style") or "").strip() or None,
            adaptation_profile=(
                dict(cfg.get("adaptation_profile") or {})
                if isinstance(cfg.get("adaptation_profile"), dict)
                else None
            ),
            public_domain_confirmed=bool(cfg.get("public_domain_confirmed")),
            generation_mode=generation_mode,
            research_use_confirmed=bool(cfg.get("research_use_confirmed")),
            on_progress=lambda progress, step: update(progress, step, status=TaskStatus.PROCESSING),
            on_chapter_completed=persist_chapter_checkpoint,
            should_pause=(classical_pause_requested if generation_mode == CLASSICAL_VERNACULAR_MODE else None),
        )
        cfg = dict(task.config or {})
        cfg.update(
            generation_complete=True,
            public_domain_policy_version=POLICY_VERSION,
            public_domain_narrative_version=(
                NARRATIVE_SYNTHESIS_VERSION if generation_mode == "research_preview" else None
            ),
            generation_mode=generation_mode,
            title=result.title,
            summary=result.summary,
            target_length=result.dimensions.target_chars,
            target_chapters=result.dimensions.target_chapters,
            actual_length=sum(count_text_chars(item.text) for item in result.chapters),
            actual_chapters=len(result.chapters),
            quality_report=str(output_dir / "quality_report.json"),
            quality_report_summary=(
                "现代材料已隔离；叙事化非虚构、古代原典事实、来源重合与全稿终审通过"
                if generation_mode == "research_preview"
                else "选定试译范围、逐段编号、数字篇名锚点与现代内容检查通过"
                if generation_mode == CLASSICAL_VERNACULAR_MODE and classical_scope_mode != "complete"
                else "原文章节顺序、逐段编号完整翻译、数字篇名锚点与现代内容检查通过"
                if generation_mode == CLASSICAL_VERNACULAR_MODE
                else "逐章串行、来源重合、现代材料、语言结构与破折号终审通过"
            ),
            source_reports=[
                {
                    "source_id": item.source_id,
                    "title": item.title,
                    "author": item.author,
                    "chars": item.chars,
                    "detected_chapters": item.detected_chapters,
                    "rights_basis": item.rights_basis,
                    "sha256": item.sha256,
                }
                for item in result.source_reports
            ],
            primary_verification_status=(
                "passed" if generation_mode == "research_preview" else "not_required"
            ),
            verified_primary_claim_count=int(
                result.quality_report.get("verified_primary_claim_count") or 0
            ),
            classical_prompt_version=(
                result.quality_report.get("prompt_version")
                if generation_mode == CLASSICAL_VERNACULAR_MODE
                else None
            ),
            classical_prompt_hash=(
                result.quality_report.get("prompt_hash")
                if generation_mode == CLASSICAL_VERNACULAR_MODE
                else None
            ),
            classical_prompt_injection_segments=(
                int(result.quality_report.get("prompt_injection_segments") or 0)
                if generation_mode == CLASSICAL_VERNACULAR_MODE
                else 0
            ),
            classical_translated_segments=(
                int(result.quality_report.get("translated_segments") or 0)
                if generation_mode == CLASSICAL_VERNACULAR_MODE
                else 0
            ),
        )
        task.config = cfg
        pre_aigc_markdown = result.markdown
        final_markdown = pre_aigc_markdown
        final_chapters = list(result.chapters)
        update(
            94,
            "古文逐段完整翻译完成，正在写入最终书稿"
            if generation_mode == CLASSICAL_VERNACULAR_MODE
            else "长篇质量终审通过，正在执行最终交付闸门",
        )

        if bool(cfg.get("aigc_required")):
            try:
                gate_result = asyncio.run(
                    enforce_aigc_quality_gate(
                        pre_aigc_markdown,
                        category="public_domain_rewrite",
                        output_dir=output_dir,
                        required=True,
                    )
                )
                source_quality = audit_edited_final_manuscript(
                    work_dir=output_dir,
                    manuscript=gate_result.text,
                    sources=sources,
                )
                _, _, parsed_chapters = parse_final_markdown(gate_result.text)
                if source_quality.get("status") != "ok" or len(parsed_chapters) != result.dimensions.target_chapters:
                    raise AigcManualReviewRequired(
                        "朱雀候选改变了章节结构，或未通过逐章事实、语言、篇幅、来源重合与全书复检",
                        result=gate_result,
                    )
                final_markdown = gate_result.text
                final_chapters = parsed_chapters
                cfg = dict(task.config or {})
                cfg.update(
                    aigc_status="passed",
                    aigc_passed=True,
                    aigc_quality_passed=True,
                    aigc_report_available=bool(gate_result.report),
                    aigc_initial_max_score=gate_result.initial_max_score,
                    aigc_final_max_score=gate_result.max_score,
                    aigc_max_score=gate_result.max_score,
                    aigc_rewrite_rounds=gate_result.rewrite_rounds,
                )
                task.config = cfg
                (output_dir / "05_aigc_final.md").write_text(final_markdown, encoding="utf-8")
            except AigcManualReviewRequired:
                raise
            except AigcTechnicalError:
                raise
        else:
            cfg = dict(task.config or {})
            cfg.update(aigc_status="not_required", aigc_passed=True, aigc_quality_passed=True)
            task.config = cfg

        db.query(Chapter).filter(Chapter.task_id == task.id).delete(synchronize_session=False)
        for chapter in final_chapters:
            db.add(
                Chapter(
                    task_id=task.id,
                    chapter_num=chapter.number,
                    title=chapter.title[:512],
                    content=chapter.text,
                    word_count=len(chapter.text),
                )
            )
        cfg = dict(task.config or {})
        cfg.update(
            markdown=final_markdown,
            markdown_chars=len(final_markdown),
            status="ok",
        )
        cfg = _clear_public_domain_failure_state(cfg)
        task.config = cfg
        clear_run()
        task.status = TaskStatus.COMPLETED
        task.progress = 100
        task.current_step = (
            f"叙事化非虚构新书完成：{len(final_chapters)} 章，人物、事件与观点仅使用已核验古代原典证据"
            if generation_mode == "research_preview"
            else f"公版古文试译完成：已按选定范围生成 {len(final_chapters)} 个结果章节"
            if generation_mode == CLASSICAL_VERNACULAR_MODE and classical_scope_mode != "complete"
            else f"公版古文完整翻译完成：{len(final_chapters)} 个原文章节已按顺序完整翻译"
            if generation_mode == CLASSICAL_VERNACULAR_MODE
            else f"全部完成：{len(final_chapters)} 章已串行落盘并通过整书质量终检"
        )
        task.error_message = None
        db.commit()
        return {"task_id": task_id, "status": TaskStatus.COMPLETED, "chapters": len(final_chapters)}
    except ClassicalTranslationPaused as exc:
        db.rollback()
        task = db.get(Task, uuid.UUID(task_id))
        if task is not None:
            cfg = dict(task.config or {})
            for key in ("active_run_id", "active_run_started_at", "active_run_heartbeat_at"):
                cfg.pop(key, None)
            cfg["pause_requested"] = False
            cfg["paused_at"] = datetime.now(timezone.utc).isoformat()
            task.config = cfg
            task.status = TaskStatus.PAUSED
            task.current_step = "已暂停；当前完整分段与全部历史断点均已保存"
            task.error_message = None
            db.commit()
        return {"task_id": task_id, "status": TaskStatus.PAUSED}
    except AigcManualReviewRequired as exc:
        db.rollback()
        task = db.get(Task, uuid.UUID(task_id))
        if task is not None:
            cfg = dict(task.config or {})
            report = exc.result.report if exc.result else None
            cfg.update(
                generation_complete=True,
                aigc_status="manual_review",
                aigc_passed=False,
                aigc_quality_passed=False,
                aigc_report_available=bool(report),
                aigc_manual_review_reason=str(exc)[:4000],
            )
            if report:
                cfg.update(
                    aigc_initial_max_score=int(report.get("initialMaxScore") or 0),
                    aigc_final_max_score=int(report.get("finalMaxScore") or 0),
                    aigc_max_score=int(report.get("finalMaxScore") or report.get("maxScore") or 0),
                    aigc_rewrite_rounds=int(report.get("rewriteRounds") or 0),
                )
            task.config = cfg
            clear_run()
            task.status = TaskStatus.MANUAL_REVIEW
            task.progress = 96
            task.current_step = "高质量公版长篇原稿已保留；朱雀候选未通过全部终稿闸门"
            task.error_message = str(exc)[:2000]
            db.commit()
        return {"task_id": task_id, "status": TaskStatus.MANUAL_REVIEW}
    except AigcTechnicalError as exc:
        db.rollback()
        task = db.get(Task, uuid.UUID(task_id))
        if task is not None:
            cfg = dict(task.config or {})
            _mark_generation_hold_for_quota(task, exc)
            cfg = dict(task.config or {})
            cfg.update(
                generation_complete=True,
                aigc_status="technical_failed",
                aigc_passed=False,
                aigc_report_available=bool(exc.report),
            )
            task.config = cfg
            clear_run()
            task.status = TaskStatus.FAILED
            task.progress = 96
            task.current_step = "朱雀技术故障；高质量公版长篇原稿已保留"
            task.error_message = str(exc)[:2000]
            db.commit()
        return {"task_id": task_id, "status": TaskStatus.FAILED}
    except (PublicDomainValidationError, PublicDomainQualityError) as exc:
        db.rollback()
        task = db.get(Task, uuid.UUID(task_id))
        if task is not None:
            cfg = dict(task.config or {})
            cfg.update(
                generation_complete=False,
                public_domain_quality_blocked=True,
                public_domain_quality_reason=str(exc)[:4000],
                primary_verification_status=(
                    "failed" if str(cfg.get("generation_mode")) == "research_preview" else "not_required"
                ),
            )
            task.config = cfg
            clear_run()
            task.status = TaskStatus.MANUAL_REVIEW
            task.current_step = (
                "古文完整翻译检查未通过，已保留原稿和所有合格分段断点"
                if str(cfg.get("generation_mode")) == CLASSICAL_VERNACULAR_MODE
                else "公版长篇本地质量终检未通过，已保留规划和合格章节断点"
            )
            task.error_message = str(exc)[:2000]
            db.commit()
        return {"task_id": task_id, "status": TaskStatus.MANUAL_REVIEW}
    except Exception as exc:
        logger.exception("[task %s] public-domain long-form pipeline failed", task_id)
        db.rollback()
        task = db.get(Task, uuid.UUID(task_id))
        if task is not None:
            cfg = dict(task.config or {})
            _mark_generation_hold_for_quota(task, exc)
            cfg = dict(task.config or {})
            internal_message = f"{type(exc).__name__}: {exc}"[:2000]
            message = _user_facing_generation_error(exc)
            delivery_uncertain = _is_paid_delivery_uncertain(exc)
            quota_backoff = _is_paid_quota_backoff_error(exc)
            classical_paid_delivery_uncertain = _is_classical_paid_delivery_uncertain(
                cfg,
                message,
            )
            if delivery_uncertain or classical_paid_delivery_uncertain:
                # A transport failure after a paid request was submitted is an
                # operator boundary.  Keep the checkpoint and persisted
                # response id, but never create a replacement request from the
                # watchdog.
                cfg.update(
                    generation_hold=True,
                    generation_hold_reason=(
                        "付费模型请求可能已经提交但结果未知，已阻止自动重试；"
                        "请核对提供方响应后再继续生成"
                    ),
                    generation_hold_step="费用保护暂停，未再次投递模型调用",
                    generation_hold_at=datetime.now(timezone.utc).isoformat(),
                    paid_call_delivery_uncertain=True,
                    paid_call_manual_resume_required=True,
                    failed_auto_recovery_suppressed_at=datetime.now(timezone.utc).isoformat(),
                )
                response_id = str(getattr(exc, "response_id", "") or "").strip()
                if response_id:
                    cfg["paid_call_response_id"] = response_id
                task.config = cfg
                clear_run()
                task.status = TaskStatus.FAILED
                task.current_step = "任务暂时中断，已保留断点"
                task.error_message = message
            elif quota_backoff:
                # Quota/resource exhaustion is safe to retry after the
                # provider breaker window, and must not consume max_retries.
                delay_seconds = _mark_task_pending_after_paid_backoff(
                    task,
                    message=internal_message,
                    retries=int(cfg.get("auto_retry_attempts") or 0),
                    run_id=run_id,
                    transient_rate_limit=is_transient_rate_limit(exc),
                )
                # The caller commits below; schedule after the commit so the
                # wake-up cannot race an uncommitted pending state.
            elif isinstance(exc, ClaudeRequestRejectedError):
                clear_run()
                task.status = TaskStatus.FAILED
                task.current_step = "任务暂时中断，已保留断点"
                task.error_message = message
            elif _is_retryable_generation_error(exc):
                # Public-domain tasks intentionally use max_retries=0.  Keep a
                # transient failure in the durable pending queue instead of
                # losing it when the Celery message is acknowledged.
                _mark_task_pending_for_transient_retry(
                    task,
                    message=internal_message,
                    retries=int(cfg.get("auto_retry_attempts") or 0),
                )
                _clear_task_run(task, run_id)
            else:
                clear_run()
                task.status = TaskStatus.FAILED
                task.current_step = "技术故障；已保存证据、规划和合格章节断点，可继续生成"
                task.error_message = message
            db.commit()
            if quota_backoff:
                _schedule_pending_task_wakeup(
                    task_id,
                    delay_seconds,
                    reason="public-domain paid provider backoff",
                )
            return {"task_id": task_id, "status": task.status, "chapters": len(task.chapters)}
        return {"task_id": task_id, "status": TaskStatus.FAILED}
    finally:
        if owns_run:
            clear_task_dispatch_guard(task_id)
        if heartbeat_stop is not None:
            heartbeat_stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=2)
        db.close()


@celery_app.task(
    bind=True,
    name="tasks.retranslate_public_domain_classical_selection",
    max_retries=0,
)
def retranslate_public_domain_classical_selection(_self, task_id: str) -> dict:
    """Replace only one completed classical chapter/segment and preserve the book."""

    from app.services import public_domain_longform as public_domain_legacy
    from app.services.public_domain_classical_vernacular import (
        retranslate_classical_selection,
    )
    from app.services.public_domain_longform import source_from_dict

    db: Session = SessionLocal()
    task: Task | None = None
    try:
        task = db.get(Task, uuid.UUID(task_id))
        if task is None:
            return {"task_id": task_id, "status": "missing"}
        cfg = dict(task.config or {})
        request = cfg.get("classical_local_retranslation")
        if not isinstance(request, dict):
            raise RuntimeError("缺少局部重译请求")
        chapter_num = int(request.get("chapter_num") or 0)
        segment_num = int(request.get("segment_num") or 0) or None
        local_prompt = str(request.get("custom_prompt") or "").strip()
        output_dir = PROJECT_ROOT / "backend" / "output" / f"task_{task_id}"
        payload = json.loads((output_dir / "00_public_domain_sources.json").read_text(encoding="utf-8"))
        sources = [source_from_dict(item) for item in payload if isinstance(item, dict)]
        chapter = retranslate_classical_selection(
            sources=sources,
            work_dir=output_dir,
            target_title=str(cfg.get("target_title") or "").strip() or None,
            adaptation_profile=(dict(cfg.get("adaptation_profile") or {})),
            chapter_num=chapter_num,
            segment_num=segment_num,
            custom_prompt=local_prompt,
            on_progress=lambda progress, step: None,
        )
        row = db.query(Chapter).filter(
            Chapter.task_id == task.id,
            Chapter.chapter_num == chapter_num,
        ).one()
        row.title = chapter.title[:512]
        row.content = chapter.text
        row.word_count = len(chapter.text)
        db.flush()

        rows = db.query(Chapter).filter(Chapter.task_id == task.id).order_by(Chapter.chapter_num).all()
        rendered = [
            public_domain_legacy.ChapterResult(
                number=int(item.chapter_num),
                title=str(item.title or f"第{item.chapter_num}章"),
                text=str(item.content or ""),
                summary=public_domain_legacy._fallback_summary(str(item.content or "")),  # noqa: SLF001
                used_claim_ids=(),
                audit={},
            )
            for item in rows
        ]
        title = str(cfg.get("title") or cfg.get("target_title") or sources[0].title)
        summary = str(cfg.get("summary") or "按原文顺序逐段完整翻译为现代白话。")
        markdown = public_domain_legacy.render_final_markdown(
            title=title,
            summary=summary,
            chapters=rendered,
        )
        (output_dir / "04_final.md").write_text(markdown, encoding="utf-8")
        request.update(
            status="completed",
            completed_at=datetime.now(timezone.utc).isoformat(),
            output_chars=len(chapter.text),
        )
        cfg["classical_local_retranslation"] = request
        cfg["markdown"] = markdown
        cfg["markdown_chars"] = len(markdown)
        previous_status = str(cfg.pop("classical_local_previous_status", TaskStatus.COMPLETED))
        task.config = cfg
        task.status = previous_status if previous_status in {TaskStatus.PAUSED, TaskStatus.COMPLETED} else TaskStatus.COMPLETED
        task.current_step = (
            f"第 {chapter_num} 章第 {segment_num} 段局部重译完成；其他内容保持不变"
            if segment_num
            else f"第 {chapter_num} 章局部重译完成；其他章节保持不变"
        )
        task.error_message = None
        db.commit()
        return {"task_id": task_id, "status": task.status, "chapter": chapter_num, "segment": segment_num}
    except Exception as exc:
        logger.exception("[task %s] local classical retranslation failed", task_id)
        db.rollback()
        task = db.get(Task, uuid.UUID(task_id))
        if task is not None:
            cfg = dict(task.config or {})
            request = cfg.get("classical_local_retranslation")
            if isinstance(request, dict):
                request["status"] = "failed"
                request["error"] = str(exc)[:2000]
                profile = dict(cfg.get("adaptation_profile") or {})
                stored_overrides = (
                    dict(profile.get("_local_prompt_overrides") or {})
                    if isinstance(profile.get("_local_prompt_overrides"), dict)
                    else {}
                )
                chapter_key = str(request.get("chapter_num") or "")
                previous = request.get("previous_chapter_overrides")
                if chapter_key:
                    if isinstance(previous, dict):
                        stored_overrides[chapter_key] = previous
                    else:
                        stored_overrides.pop(chapter_key, None)
                profile["_local_prompt_overrides"] = stored_overrides
                cfg["adaptation_profile"] = profile
                cfg["classical_local_retranslation"] = request
            previous_status = str(cfg.pop("classical_local_previous_status", TaskStatus.COMPLETED))
            task.config = cfg
            task.status = previous_status if previous_status in {TaskStatus.PAUSED, TaskStatus.COMPLETED} else TaskStatus.COMPLETED
            task.current_step = "局部重译失败，旧译文保持不变"
            task.error_message = str(exc)[:2000]
            db.commit()
        return {"task_id": task_id, "status": "failed", "error": str(exc)}
    finally:
        db.close()


def _mark_cover_failed(db: Session, task_id: str, message: str) -> None:
    task = db.get(Task, uuid.UUID(task_id))
    if task is None:
        return
    cfg = dict(task.config or {})
    cfg.update(
        cover_disabled=False,
        cover_status="failed",
        cover_error=message[:2000],
    )
    task.config = cfg
    task.cover_url = None
    db.commit()


@celery_app.task(
    bind=True,
    name="tasks.generate_cover",
    max_retries=settings.COVER_TASK_MAX_RETRIES,
)
def generate_cover(_self, task_id: str, force: bool = False) -> dict:
    """Generate covers in the background so manuscripts can complete first."""
    db: Session = SessionLocal()
    guard_id = _cover_dispatch_guard_id(task_id)
    try:
        task_uuid = uuid.UUID(task_id)
        task = db.execute(
            select(Task).where(Task.id == task_uuid).with_for_update()
        ).scalar_one_or_none()
        if task is None:
            clear_task_dispatch_guard(guard_id)
            return {"task_id": task_id, "status": "missing"}

        cfg = dict(task.config or {})
        if not settings.COVER_WORKFLOW_ENABLED:
            task.cover_url = None
            cfg.update(
                cover_disabled=True,
                cover_status="disabled",
                cover_isolated=True,
                cover_isolation_reason="COVER_WORKFLOW_ENABLED=false",
                cover_error=None,
            )
            task.config = cfg
            db.commit()
            clear_task_dispatch_guard(guard_id)
            return {"task_id": task_id, "status": "disabled"}

        if task.status != TaskStatus.COMPLETED:
            cfg.update(cover_status="queued", cover_error=None)
            task.config = cfg
            db.commit()
            current_retries = int(getattr(getattr(_self, "request", None), "retries", 0) or 0)
            max_retries = max(0, int(settings.COVER_TASK_MAX_RETRIES or 0))
            if current_retries >= max_retries:
                _mark_cover_failed(db, task_id, "封面生成排队超时：书稿尚未完成")
                clear_task_dispatch_guard(guard_id)
                return {"task_id": task_id, "status": "failed", "reason": "book_not_completed"}
            raise _self.retry(exc=RuntimeError("book manuscript is not completed yet"), countdown=60)

        output_dir = PROJECT_ROOT / "backend" / "output" / f"task_{task.id}"
        cover_path = output_dir / "cover.png"
        if cover_path.exists() and cover_path.stat().st_size > 0 and not force:
            task.cover_url = f"/api/v1/tasks/{task.id}/cover"
            cfg.update(
                cover_disabled=False,
                cover_status="completed",
                cover_error=None,
                cover_path=str(cover_path),
                draft_cover_path=str(output_dir / "draft_cover.png"),
                cover_style_brief_path=str(output_dir / "cover_style_brief.json"),
            )
            task.config = cfg
            db.commit()
            clear_task_dispatch_guard(guard_id)
            return {"task_id": task_id, "status": "completed", "reused": True}

        markdown = _cover_markdown(task, output_dir)
        if not markdown:
            cfg.update(
                cover_disabled=False,
                cover_status="failed",
                cover_error="封面生成失败：未找到可用书稿内容",
            )
            task.config = cfg
            task.cover_url = None
            db.commit()
            clear_task_dispatch_guard(guard_id)
            return {"task_id": task_id, "status": "failed", "reason": "missing_markdown"}

        title = str(cfg.get("title") or task.title or _extract_book_title(markdown)).strip() or "未命名书稿"
        category = str(cfg.get("category") or "social_science").strip()
        author = str(cfg.get("author") or cfg.get("book_author") or "").strip() or None
        intro = str(cfg.get("intro") or cfg.get("synopsis") or "").strip() or None
        reference_mode = str(cfg.pop("cover_reference_mode", "refresh") or "refresh").strip()
        reference_mode = "reuse" if reference_mode == "reuse" else "refresh"
        cfg.update(
            cover_disabled=False,
            cover_status="generating",
            cover_error=None,
            cover_started_at=datetime.now(timezone.utc).isoformat(),
        )
        task.config = cfg
        db.commit()

        from app.services.cover import generate_cover_file

        try:
            generated_path = generate_cover_file(
                title,
                markdown,
                output_dir,
                category=category,
                author=author,
                intro=intro,
                force=force,
                reference_mode=reference_mode,
            )
        except DistributedLimitError as exc:
            db.rollback()
            task = db.get(Task, task_uuid)
            if task is not None:
                cfg = dict(task.config or {})
                cfg.update(
                    cover_status="queued",
                    cover_error=None,
                    cover_last_queue_wait_at=datetime.now(timezone.utc).isoformat(),
                )
                task.config = cfg
                db.commit()
            current_retries = int(getattr(getattr(_self, "request", None), "retries", 0) or 0)
            max_retries = max(0, int(settings.COVER_TASK_MAX_RETRIES or 0))
            if current_retries >= max_retries:
                _mark_cover_failed(db, task_id, f"封面排队超时：{exc}")
                clear_task_dispatch_guard(guard_id)
                return {"task_id": task_id, "status": "failed", "reason": "queue_exhausted"}
            raise _self.retry(exc=exc, countdown=_cover_retry_countdown())
        except Exception as exc:
            db.rollback()
            logger.exception("[task %s] background cover generation failed", task_id)
            _mark_cover_failed(db, task_id, str(exc))
            clear_task_dispatch_guard(guard_id)
            return {"task_id": task_id, "status": "failed", "reason": str(exc)[:500]}

        task = db.execute(
            select(Task).where(Task.id == task_uuid).with_for_update()
        ).scalar_one_or_none()
        if task is None:
            clear_task_dispatch_guard(guard_id)
            return {"task_id": task_id, "status": "missing_after_generation"}
        cfg = dict(task.config or {})
        cfg.update(
            cover_disabled=False,
            cover_status="completed",
            cover_error=None,
            cover_path=str(generated_path),
            draft_cover_path=str(output_dir / "draft_cover.png"),
            cover_style_brief_path=str(output_dir / "cover_style_brief.json"),
            cover_completed_at=datetime.now(timezone.utc).isoformat(),
        )
        task.config = cfg
        task.cover_url = f"/api/v1/tasks/{task.id}/cover"
        db.commit()
        clear_task_dispatch_guard(guard_id)
        return {"task_id": task_id, "status": "completed"}
    finally:
        db.close()
