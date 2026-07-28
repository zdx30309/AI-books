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
import re
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
from app.services.content_engine import generate_full_book, normalize_final_markdown
from app.services.aigc_quality_gate import (
    AigcManualReviewRequired,
    AigcTechnicalError,
    enforce_aigc_quality_gate,
)
from app.services.qingdou import (
    QingdouDurationExceededError,
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
from app.utils.task_dispatch_guard import acquire_task_dispatch_guard, clear_task_dispatch_guard
from celery_app import celery_app

logger = logging.getLogger(__name__)

_CHAPTER_HEADING_RE = re.compile(r"^(?:！！！@?|@)\s*(\d+)\.\s*(.+?)\s*$", re.MULTILINE)
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
_RESUME_STAGE_FILES = (
    "04_format.md",
    "03_refine.md",
    "02_polish.md",
    "01_writer.md",
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
    "轻抖任务失败",
    "未返回 videocontent",
    "youtubetranscripterror",
    "youtube transcript",
    "youtube 字幕",
)
_NON_RETRYABLE_ERROR_TOKENS = (
    "imageapiuncertainerror",
    "automatic retry stopped to avoid duplicate billing",
    "duplicate billing",
    "insufficient_quota",
    "exceeded your current quota",
    "duration exceeded",
    "超过限制",
)

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

    if cfg.get("generation_hold"):
        task.status = TaskStatus.FAILED
        task.error_message = str(
            cfg.get("generation_hold_reason") or "generation is held by cost protection"
        )[:2000]
        task.current_step = str(cfg.get("generation_hold_step") or "费用保护暂停，未投递模型调用")[:255]
        db.commit()
        raise StaleTaskRun(f"task {task.id} held by cost protection")

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
    candidates = source_url_candidates(source_url, platform)
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
            and not isinstance(exc, QingdouDurationExceededError)
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


def _should_try_bilibili_fallback(exc: Exception) -> bool:
    text = str(exc).lower()
    failed_batch = (
        "batchstatus=2" in text
        or "batchstatus': 2" in text
        or '"batchstatus": 2' in text
    )
    failed_item = (
        "status=1100" in text
        or "'status': 1100" in text
        or '"status": 1100' in text
    )
    no_content = (
        "videocontent: none" in text
        or "videocontent': none" in text
        or '"videocontent": none' in text
        or "未返回 videocontent" in text
        or "未返回videocontent" in text
    )
    # Qingdou uses batchStatus=2 + item status=1100 for a completed platform
    # parse failure.  It is not an in-progress/busy result, so the native
    # Bilibili subtitle/audio fallback must be allowed to run.
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


def _is_retryable_generation_error(exc: Exception) -> bool:
    if isinstance(exc, AigcTechnicalError):
        return True
    normalized = f"{type(exc).__name__}: {exc}".lower()
    if _is_terminal_qingdou_parse_error_message(normalized):
        return False
    if _is_auth_error_message(normalized):
        return False
    if any(token in normalized for token in _NON_RETRYABLE_ERROR_TOKENS):
        return False
    return any(token in normalized for token in _RETRYABLE_ERROR_TOKENS)


def _retry_delay_seconds(retries: int) -> int:
    base = max(1, int(settings.GENERATION_TASK_RETRY_BACKOFF_SECONDS or 60))
    return min(900, base * (2 ** max(0, retries)))


def _is_retryable_error_message(message: str | None) -> bool:
    normalized = (message or "").lower()
    if not normalized:
        return False
    if _is_terminal_qingdou_parse_error_message(normalized):
        return False
    if _is_auth_error_message(normalized):
        return False
    if any(token in normalized for token in _NON_RETRYABLE_ERROR_TOKENS):
        return False
    return any(token in normalized for token in _RETRYABLE_ERROR_TOKENS)


def _is_auth_error_message(normalized: str) -> bool:
    return "api key" in normalized or "unauthorized" in normalized or bool(_AUTH_ERROR_RE.search(normalized))


def _is_terminal_qingdou_parse_error_message(normalized: str) -> bool:
    if "qingdouerror" not in normalized and "轻抖任务失败" not in normalized:
        return False
    batch_failed = any(token in normalized for token in _QINGDOU_TERMINAL_BATCH_TOKENS)
    empty_content = any(token in normalized for token in _QINGDOU_EMPTY_CONTENT_TOKENS)
    return batch_failed and empty_content


def _retry_exhausted_step(message: str) -> str:
    return f"自动重试已用尽：{message}。后台会按恢复策略重排；也可稍后点击继续生成。"[:255]


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


def _dispatch_task_ids(task_ids: list[str], *, label: str) -> None:
    for task_id in task_ids:
        try:
            if not acquire_task_dispatch_guard(task_id):
                continue
            with SessionLocal() as dispatch_db:
                dispatch_task = dispatch_db.get(Task, uuid.UUID(task_id))
                is_public_longform = bool(
                    dispatch_task and (dispatch_task.config or {}).get("public_domain_longform_v2")
                )
            if is_public_longform:
                process_public_domain_longform.delay(task_id)
            else:
                process_video.delay(task_id)
        except Exception:
            logger.exception("[task %s] %s dispatch failed", task_id, label)
    if task_ids:
        logger.warning("%s requeued %d tasks", label, len(task_ids))


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
            if cfg.get("generation_hold"):
                continue
            heartbeat = _parse_iso_datetime(cfg.get("active_run_heartbeat_at")) or _parse_iso_datetime(
                cfg.get("active_run_started_at")
            )
            if heartbeat is not None and heartbeat > cutoff:
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

    _dispatch_task_ids(tasks_to_dispatch, label="stale task watchdog")
    return len(tasks_to_dispatch)


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
            if cfg.get("generation_hold"):
                continue
            attempts = int(cfg.get("failed_auto_recovery_attempts") or 0)
            last_recovered_at = _parse_iso_datetime(cfg.get("failed_auto_recovered_at"))
            if attempts >= max_attempts:
                continue
            if last_recovered_at is not None and last_recovered_at > cutoff:
                continue
            message = task.error_message or cfg.get("last_auto_retry_error")
            if not _is_retryable_error_message(str(message or "")):
                continue
            cfg["failed_auto_recovery_attempts"] = attempts + 1
            cfg["failed_auto_recovered_at"] = datetime.now(timezone.utc).isoformat()
            task.config = cfg
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

    _dispatch_task_ids(tasks_to_dispatch, label="failed task auto recovery")
    return len(tasks_to_dispatch)


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
            if cfg.get("generation_hold") or str(cfg.get("active_run_id") or "").strip():
                continue
            not_before = _parse_iso_datetime(cfg.get("auto_retry_not_before"))
            if not_before is None or not_before > now:
                continue
            message = task.error_message or cfg.get("last_auto_retry_error")
            if message and not _is_retryable_error_message(str(message)):
                continue
            cfg["auto_retry_redispatched_at"] = now.isoformat()
            task.config = cfg
            tasks_to_dispatch.append(str(task.id))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("pending retry watchdog failed")
        return 0
    finally:
        db.close()

    _dispatch_task_ids(tasks_to_dispatch, label="pending retry watchdog")
    return len(tasks_to_dispatch)


def _patch_config(task: Task, **kv: Any) -> None:
    cfg = dict(task.config or {})
    cfg.update(kv)
    task.config = cfg


def _user_facing_generation_error(exc: Exception) -> str:
    raw = f"{type(exc).__name__}: {exc}"
    normalized = raw.lower()
    if _is_terminal_qingdou_parse_error_message(normalized):
        return "视频解析失败：轻抖未返回可用文稿，请粘贴文稿或更换可解析链接后重试。"
    if "insufficient_quota" in normalized or "exceeded your current quota" in normalized:
        return "OpenAI 额度不足或账单不可用，请检查当前 API Key 的余额、账单状态或更换可用 Key 后重试。"
    if "rate_limit" in normalized or "429" in normalized:
        return "OpenAI 请求被限流，请稍后重试，或降低并发/更换可用模型配置。"
    if any(token in normalized for token in ("connection", "connecterror", "apiconnectionerror", "ssl", "eof", "remoteprotocol", "readerror")):
        return "上游接口连接中断，系统会自动断点重试；若重试耗尽仍失败，请稍后继续生成。"
    if "timeout" in normalized or "timed out" in normalized:
        return "模型调用超时，任务已停止。可以稍后点击继续生成重试。"
    if _is_auth_error_message(normalized):
        return "模型 API Key 无效或未授权，请检查服务器环境变量后重试。"
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


def _persist_chapters_from_markdown(db: Session, task: Task, markdown: str) -> int:
    """从新引擎生成的 Markdown 中拆出章节并落库，维持旧 API 可观测性。"""
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
            "comic_jokes_selected": (38, "谐音笑点候选已完成六维筛选"),
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
        chapter_count = _persist_chapters_from_markdown(db, task, markdown)
        _patch_config(task, markdown=markdown, title=book_title)

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
                message = _user_facing_generation_error(e)
                _clear_task_run(task, run_id)
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
                if _is_retryable_generation_error(e) and current_retries < max_retries:
                    retry_countdown = _retry_delay_seconds(current_retries)
                    cfg = dict(task.config or {})
                    cfg["last_auto_retry_error"] = message[:1000]
                    cfg["auto_retry_attempts"] = current_retries + 1
                    cfg["auto_retry_not_before"] = (
                        datetime.now(timezone.utc) + timedelta(seconds=retry_countdown)
                    ).isoformat()
                    cfg["auto_retry_scheduled_by"] = run_id
                    task.config = cfg
                    task.status = TaskStatus.PENDING
                    task.error_message = message[:2000]
                    task.current_step = f"自动重试中（{current_retries + 1}/{max_retries}）：{message}"[:255]
                    task.progress = max(0, int(task.progress or 0))
                else:
                    if _is_retryable_generation_error(e):
                        cfg = dict(task.config or {})
                        cfg["last_auto_retry_error"] = message[:1000]
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
    return cleaned


@celery_app.task(
    bind=True,
    name="tasks.process_public_domain_longform",
    max_retries=0,
)
def process_public_domain_longform(_self, task_id: str) -> dict:
    """Serial, resumable 8–10-source public-domain long-form pipeline."""
    from app.services.public_domain_longform import (
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
        task.current_step = (
            "正在隔离现代材料并建立匿名叙事参数；佛教事实只允许使用古代原典证据"
            if generation_mode == "research_preview"
            else "正在验证 5–10 本来源并建立逐章串行检查点"
        )
        db.commit()

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
            public_domain_confirmed=bool(cfg.get("public_domain_confirmed")),
            generation_mode=generation_mode,
            research_use_confirmed=bool(cfg.get("research_use_confirmed")),
            on_progress=lambda progress, step: update(progress, step, status=TaskStatus.PROCESSING),
            on_chapter_completed=persist_chapter_checkpoint,
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
        )
        task.config = cfg
        pre_aigc_markdown = result.markdown
        final_markdown = pre_aigc_markdown
        final_chapters = list(result.chapters)
        update(94, "长篇质量终审通过，正在执行最终交付闸门")

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
            else f"全部完成：{len(final_chapters)} 章已串行落盘并通过整书质量终检"
        )
        task.error_message = None
        db.commit()
        return {"task_id": task_id, "status": TaskStatus.COMPLETED, "chapters": len(final_chapters)}
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
            task.current_step = "公版长篇本地质量终检未通过，已保留规划和合格章节断点"
            task.error_message = str(exc)[:2000]
            db.commit()
        return {"task_id": task_id, "status": TaskStatus.MANUAL_REVIEW}
    except Exception as exc:
        logger.exception("[task %s] public-domain long-form pipeline failed", task_id)
        db.rollback()
        task = db.get(Task, uuid.UUID(task_id))
        if task is not None:
            clear_run()
            task.status = TaskStatus.FAILED
            task.current_step = "技术故障；已保存证据、规划和合格章节断点，可继续生成"
            task.error_message = f"{type(exc).__name__}: {exc}"[:2000]
            db.commit()
        return {"task_id": task_id, "status": TaskStatus.FAILED}
    finally:
        if owns_run:
            clear_task_dispatch_guard(task_id)
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
