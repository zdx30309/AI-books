"""Task routers."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import zipfile
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4
from xml.sax.saxutils import escape

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import PROJECT_ROOT, settings
from app.api.routers.auth import get_current_user
from app.core.security import JWTDecodeError, decode_token
from app.db.session import get_db
from app.models import Chapter, Task, TaskStatus, User
from app.prompts.goal_driven_templates import (
    get_category_meta,
    list_categories,
    normalize_category,
)
from app.services.content_engine import normalize_final_markdown
from app.services.aigc_quality_gate import (
    AIGC_POLICY_VERSION,
    aigc_category_eligible,
)
from app.services.comic_series import (
    comic_raw_text_from_phrases,
    image_filename_from_markdown,
    parse_comic_input_parts,
    parse_comic_phrases,
    parse_comic_preface_phrase,
    parse_comic_theme_requirement,
    parse_comic_title,
    parse_healing_comic_character_prompt,
    parse_healing_comic_panel_count,
    parse_healing_comic_source_content,
    validate_comic_phrases,
)
from app.services.comic_variants import is_healing_comic_family
from app.services.classical_chinese import (
    BUDDHIST_SCRIPTURE_STORY_CATEGORY,
    CLASSICAL_CHINESE_CATEGORY_IDS,
    CLASSICAL_REFERENCE_REWRITE_CATEGORY,
    get_classical_variant_meta,
)
from app.schemas.chapter import (
    ChapterResponse,
    ChapterUpdateRequest,
    ChaptersListResponse,
)
from app.schemas.task import (
    BatchCreateTaskItem,
    BatchCreateTaskRequest,
    BatchCreateTaskResponse,
    BatchDownloadRequest,
    BatchRegenerateTaskItem,
    BatchRegenerateTaskRequest,
    BatchRegenerateTaskResponse,
    BatchRetryTaskItem,
    BatchRetryTaskRequest,
    BatchRetryTaskResponse,
    CreateTaskRequest,
    CreateTaskResponse,
    LengthRepairApprovalRequest,
    ClassicalReferenceTemplateResponse,
    MarkdownResponse,
    RegenerateCoverRequest,
    RegenerateSelectionRequest,
    RegenerateSelectionResponse,
    ReferenceArticleBatchTaskRequest,
    ReferenceArticleTaskRequest,
    StageContentResponse,
    TaskMetadataUpdateRequest,
    TaskListItem,
    TaskListResponse,
    TaskCategoryListResponse,
    TaskStatusResponse,
)
from app.services.gemini import regenerate_selected_text
from app.services.editor_v05 import VIDEO_REJECTED, similarity_retry_reason
from app.services.video_sources import (
    canonical_source_url,
    detect_video_source_platform,
    platform_label,
    resolve_bilibili_source_url,
)
from app.services.classical_reference_templates import (
    DEFAULT_REFERENCE_STYLE_GUIDE,
    default_reference_template_status,
)
from app.services.wechat_article import WeChatArticle, WeChatArticleError, extract_wechat_article
from app.tasks.tasks import enqueue_cover_generation, process_video
from app.utils.docx_text import DocxTextError, extract_classical_document_text
from app.utils.task_dispatch_guard import acquire_task_dispatch_guard, clear_task_dispatch_guard

_STAGE_FILES = {
    "writer": "01_writer.md",
    "writer_repaired": "01b_writer_repaired.md",
    "polish": "02_polish.md",
    "refine": "03_refine.md",
    "format": "04_format.md",
}
_FRONT_SECTION_NAMES = ("最终书名", "最终简介", "三个备选书名", "三个备选简介", "目录", "导言", "文前", "正文")
_BODY_SECTION_RE = re.compile(r"^(?:！！！)?@?\s*正文\s*$", re.MULTILINE)
_FINAL_TITLE_SECTION_RE = re.compile(
    r"^(?:！！！)?@?\s*(最终书名|三个备选书名)\s*$([\s\S]*?)(?=^(?:！！！)?@?\s*(?:"
    + "|".join(map(re.escape, _FRONT_SECTION_NAMES))
    + r")\s*$|\Z)",
    re.MULTILINE,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tasks", tags=["Tasks"])

STYLE_NOTES_INLINE_LIMIT = 4_000

_ACTIVE_TASK_STATUSES = (
    TaskStatus.PENDING,
    TaskStatus.FETCHING_TOPICS,
    TaskStatus.FILTERING_TOPICS,
    TaskStatus.GENERATING_OUTLINE,
    TaskStatus.WRITING_CHAPTERS,
    TaskStatus.FACT_CHECKING,
    TaskStatus.PROCESSING,
)
_TASK_RETENTION_DAYS = 7
_RETRY_STAGE_PROGRESS: tuple[tuple[str, int, str], ...] = (
    ("04_format.md", 90, "排队中（断点续跑：终稿已生成，准备入库）"),
    ("03_refine.md", 87, "排队中（断点续跑：从终稿格式化继续）"),
    ("02_polish.md", 72, "排队中（断点续跑：从二润继续）"),
    ("01_writer.md", 50, "排队中（断点续跑：从一润继续）"),
)
_REGENERATE_CONFIG_DROP_KEYS = {
    "approved_length_repair_sha256",
    "active_run_id",
    "active_run_started_at",
    "active_run_heartbeat_at",
    "active_run_heartbeat_source",
    "auto_retry_attempts",
    "last_auto_retry_error",
    "auto_retry_not_before",
    "auto_retry_scheduled_by",
    "auto_retry_exhausted_at",
    "auto_requeued_at",
    "auto_requeue_reason",
    "terminal_input_failure",
    "terminal_input_manual_review_at",
    "terminal_input_manual_review_reason",
    "failed_auto_recovery_attempts",
    "failed_auto_recovered_at",
    "markdown",
    "markdown_chars",
    "title",
    "quality_report",
    "quality_report_summary",
    "aigc_quality_report",
    "aigc_max_score",
    "aigc_passed",
    "aigc_required",
    "aigc_policy_version",
    "aigc_status",
    "aigc_initial_max_score",
    "aigc_final_max_score",
    "aigc_rewrite_rounds",
    "aigc_quality_passed",
    "aigc_report_available",
    "cover_disabled",
    "cover_status",
    "cover_error",
    "cover_path",
    "cover_queued_at",
    "cover_started_at",
    "cover_completed_at",
    "cover_isolated",
    "cover_isolation_reason",
    "cover_style_brief_path",
    "draft_cover_path",
}


def _stamp_aigc_policy(config: dict) -> dict:
    """Snapshot the gate decision so future flag changes never touch old tasks."""
    cfg = dict(config or {})
    category = str(cfg.get("category") or "social_science").strip()
    required = bool(category != "general_longform" and settings.AIGC_GATE_ENABLED and aigc_category_eligible(category))
    cfg.update(
        aigc_policy_version=AIGC_POLICY_VERSION,
        aigc_required=required,
        aigc_status="pending" if required else "not_required",
        aigc_report_available=False,
    )
    return cfg


def _active_generation_count(db: Session) -> int:
    return int(
        db.query(func.count(Task.id))
        .filter(Task.status.in_(_ACTIVE_TASK_STATUSES))
        .scalar()
        or 0
    )


def _generation_capacity(db: Session) -> tuple[int, int, int | None]:
    limit = max(0, int(settings.MAX_ACTIVE_GENERATION_TASKS or 0))
    active = _active_generation_count(db)
    if limit <= 0:
        return limit, active, None
    return limit, active, max(limit - active, 0)


def _ensure_generation_capacity(db: Session, *, requested: int = 1) -> None:
    limit, active, remaining = _generation_capacity(db)
    if limit > 0 and (remaining or 0) < max(1, requested):
        raise HTTPException(
            status_code=429,
            detail=(
                f"当前已有 {active} 本书稿正在生成或排队，系统已开启费用保护，"
                f"最多同时允许 {limit} 本，本次还需要 {requested} 个名额。"
            ),
        )


#: Keys the server owns. A client-supplied value would let a request read files
#: on the server (``*_path``), override worker limits or forge run/hold state.
_SERVER_OWNED_CONFIG_KEYS = frozenset(
    {
        "source_text_path",
        "style_notes_path",
        "poll_timeout_seconds",
        "max_video_duration",
        "public_domain_longform_v2",
        "generation_hold",
        "generation_complete",
        "pause_requested",
        "editor_v05_status",
        "editor_v05_similarity",
        "editor_v05_verdict",
        "editor_v05_similarity_history",
        "editor_v05_similarity_attempt",
        "editor_v05_generation_attempt",
        "editor_v05_video_rejected",
        "editor_v05_previous_generation_path",
        "approved_length_repair_sha256",
    }
)
_SERVER_OWNED_CONFIG_PREFIXES = (
    "active_run_",
    "paid_api_",
    "paid_call_",
    "generation_hold",
    "auto_retry_",
    "dispatch_",
    "pending_wakeup_",
    "failed_auto_recovery_",
    "orphan_requeue_",
    "terminal_input_",
    "aigc_",
)


def _strip_server_owned_config(cfg: dict) -> dict:
    return {
        key: value
        for key, value in cfg.items()
        if key not in _SERVER_OWNED_CONFIG_KEYS and not str(key).startswith(_SERVER_OWNED_CONFIG_PREFIXES)
    }


def _normalize_task_config(config: dict | None) -> dict:
    cfg = _strip_server_owned_config(dict(config or {}))
    try:
        category = normalize_category(str(cfg.get("category") or "social_science"))
        category_meta = get_category_meta(category)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    cfg.update(
        category=category,
        category_label=category_meta["label"],
        category_prompt_status=category_meta["prompt_status"],
    )
    if category == "general_longform":
        if len(str(cfg.get("raw_text") or "")) > 250000:
            raise HTTPException(status_code=400, detail="通用长篇单份素材最多250000字符；请在工作区分文件导入，原文不会截断")
        cfg.pop("general_longform_request", None)
        cfg["general_longform_phase"] = "needs_outline"
        from app.prompts.general_longform_prompts import get_manifest as get_general_longform_manifest
        cfg["general_longform_policy"] = get_general_longform_manifest()["version"]
    if not str(cfg.get("bookshelf") or "").strip():
        cfg["bookshelf"] = category_meta["label"]
    if category in {"web_short_story", "social_science_test", CLASSICAL_REFERENCE_REWRITE_CATEGORY}:
        style_reference = str(cfg.get("style_reference") or "").strip()[:200]
        style_notes = str(cfg.get("style_notes") or "").strip()[:STYLE_NOTES_INLINE_LIMIT]
        if style_reference:
            cfg["style_reference"] = style_reference
        else:
            cfg.pop("style_reference", None)
        if style_notes:
            cfg["style_notes"] = style_notes
        else:
            cfg.pop("style_notes", None)
    else:
        cfg.pop("style_reference", None)
        cfg.pop("style_notes", None)
    if category in CLASSICAL_CHINESE_CATEGORY_IDS:
        variant_meta = get_classical_variant_meta(cfg.get("classical_variant"))
        cfg["classical_variant"] = variant_meta["id"]
        cfg["classical_variant_label"] = variant_meta["label"]
        original_title = str(
            cfg.get("classical_original_title")
            or cfg.get("original_title")
            or cfg.get("book_title")
            or cfg.get("title")
            or ""
        ).strip()
        if original_title:
            cfg["classical_original_title"] = original_title[:120]
            cfg["title"] = original_title[:120]
    else:
        cfg.pop("classical_variant", None)
        cfg.pop("classical_variant_label", None)
        cfg.pop("classical_original_title", None)
    if is_healing_comic_family(category):
        title = parse_comic_title(
            cfg.get("comic_title")
            or cfg.get("title")
            or cfg.get("book_title")
            or cfg.get("raw_text")
            or ""
        )
        if not title:
            raise HTTPException(status_code=400, detail="漫画需要输入书名")
        panel_count = parse_healing_comic_panel_count(
            cfg.get("comic_panel_count")
            or cfg.get("healing_comic_panel_count")
            or cfg.get("chapter_count")
            or cfg.get("panel_count")
        )
        character_prompt = parse_healing_comic_character_prompt(
            cfg.get("comic_character_prompt")
            or cfg.get("character_prompt")
            or cfg.get("protagonist_prompt")
            or cfg.get("comic_theme_requirement")
            or cfg.get("comic_requirement")
            or cfg.get("theme_requirement")
            or ""
        )
        source_content = parse_healing_comic_source_content(
            cfg.get("comic_source_content")
            or cfg.get("source_content")
            or cfg.get("article_content")
            or cfg.get("custom_content")
            or ""
        )
        cfg["comic_title"] = title
        cfg["comic_panel_count"] = panel_count
        if character_prompt:
            cfg["comic_character_prompt"] = character_prompt
        else:
            cfg.pop("comic_character_prompt", None)
        if source_content:
            cfg["comic_source_content"] = source_content
        else:
            cfg.pop("comic_source_content", None)
        cfg["title"] = title
        cfg.pop("comic_preface_phrase", None)
        cfg.pop("comic_phrases", None)
        cfg.pop("comic_requirement", None)
        raw_lines = [f"书名：{title}"]
        if character_prompt:
            raw_lines.append(f"主角形象：{character_prompt}")
        if source_content:
            raw_lines.extend(["自定义内容：", source_content])
        cfg["raw_text"] = "\n".join(raw_lines)
        cfg["raw_text_chars"] = len(cfg["raw_text"])
    if category == "comic_series":
        comic_label = category_meta["label"]
        title = parse_comic_title(cfg.get("comic_title") or cfg.get("title") or cfg.get("book_title") or "")
        raw_title, raw_preface, raw_phrases = parse_comic_input_parts(cfg.get("raw_text") or "")
        if not title:
            title = raw_title
        if not title:
            raise HTTPException(status_code=400, detail=f"{comic_label}需要输入书名")
        theme_requirement = parse_comic_theme_requirement(
            cfg.get("comic_theme_requirement")
            or cfg.get("theme_requirement")
            or cfg.get("animal_theme")
            or ""
        )
        phrases = (
            parse_comic_phrases(cfg.get("comic_phrases"))
            if cfg.get("comic_phrases")
            else raw_phrases
        )
        preface_phrase = parse_comic_preface_phrase(
            cfg.get("comic_preface_phrase") or cfg.get("preface_phrase") or raw_preface or ""
        )
        if not preface_phrase:
            raise HTTPException(status_code=400, detail=f"{comic_label}需要输入 1 条文前短语")
        try:
            validate_comic_phrases(phrases)
        except ValueError as exc:
            detail = str(exc).replace("插画", comic_label, 1)
            raise HTTPException(status_code=400, detail=detail) from exc
        cfg["comic_title"] = title
        cfg["title"] = title
        if theme_requirement:
            cfg["comic_theme_requirement"] = theme_requirement
        else:
            cfg.pop("comic_theme_requirement", None)
        cfg["comic_preface_phrase"] = preface_phrase
        cfg["comic_phrases"] = phrases
        cfg["raw_text"] = comic_raw_text_from_phrases(
            phrases,
            preface_phrase=preface_phrase,
            title=title,
            theme_requirement=theme_requirement,
        )
        cfg["raw_text_chars"] = len(cfg["raw_text"])
    return cfg


def _create_and_dispatch_task(
    *,
    source_url: str,
    config: dict,
    db: Session,
    user: User,
    dispatch: bool = True,
) -> CreateTaskResponse:
    task = Task(
        source_url=source_url,
        owner_id=user.id,
        config=_stamp_aigc_policy(config),
        status=TaskStatus.PENDING,
        progress=0,
        current_step="排队中",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    logger.info("[task %s] 建库完成, 准备投 Celery", task.id)

    if not dispatch:
        return CreateTaskResponse(task_id=task.id, status=task.status)

    try:
        _dispatch_task(task)
    except Exception as e:
        logger.exception("[task %s] Celery 投递失败", task.id)
        _mark_dispatch_failure_pending(task, e)
        db.commit()
        raise HTTPException(
            status_code=503,
            detail=f"任务已建库但 Celery 投递失败 (task_id={task.id}): {e}",
        ) from e

    return CreateTaskResponse(task_id=task.id, status=task.status)


def _create_classical_text_task(
    *,
    source_url: str,
    config: dict,
    db: Session,
    user: User,
) -> CreateTaskResponse:
    raw_text = str(config.get("raw_text") or "").strip()
    if not raw_text:
        raise HTTPException(status_code=400, detail="古文白话请直接粘贴文言文原文，不需要填写视频链接")

    cfg = _stamp_aigc_policy(config)
    cfg.pop("raw_text", None)
    cfg["raw_text_chars"] = len(raw_text)
    style_notes_full = str(cfg.get("style_notes") or "").strip()
    should_externalize_style_notes = (
        cfg.get("category") == CLASSICAL_REFERENCE_REWRITE_CATEGORY
        and len(style_notes_full) > STYLE_NOTES_INLINE_LIMIT
        and not str(cfg.get("style_notes_path") or "").strip()
    )
    if should_externalize_style_notes:
        cfg["style_notes"] = style_notes_full[:STYLE_NOTES_INLINE_LIMIT]
        cfg["style_notes_chars"] = len(style_notes_full)
        cfg["style_notes_preview_chars"] = len(cfg["style_notes"])
    task = Task(
        source_url=source_url,
        owner_id=user.id,
        config=cfg,
        status=TaskStatus.PENDING,
        progress=0,
        current_step="排队中",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    logger.info("[task %s] 古文白话文本建库完成, 准备外置原文并投 Celery", task.id)

    try:
        source_text_path = _persist_uploaded_source_text(task.id, raw_text)
        cfg.update(source_text_path=source_text_path, raw_text_chars=len(raw_text))
        if should_externalize_style_notes:
            cfg["style_notes_path"] = _persist_uploaded_source_text(
                task.id,
                style_notes_full,
                suffix="style_reference",
            )
        task.config = cfg
        db.commit()
    except Exception as exc:
        logger.exception("[task %s] 古文白话原文落盘失败", task.id)
        task.status = TaskStatus.FAILED
        task.error_message = f"Classical source text persist failed: {exc}"[:2000]
        db.commit()
        raise HTTPException(status_code=500, detail=f"古文白话原文保存失败: {exc}") from exc

    try:
        _dispatch_task(task)
    except Exception as e:
        logger.exception("[task %s] Celery 投递失败", task.id)
        _mark_dispatch_failure_pending(task, e)
        db.commit()
        raise HTTPException(
            status_code=503,
            detail=f"任务已建库但 Celery 投递失败 (task_id={task.id}): {e}",
        ) from e

    return CreateTaskResponse(task_id=task.id, status=task.status)


def _persist_uploaded_source_text(task_id: UUID, text: str, *, suffix: str = "") -> str:
    input_dir = PROJECT_ROOT / "backend" / "output" / "task_inputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    clean_suffix = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(suffix or "").strip()).strip("_")
    filename = f"{task_id}_{clean_suffix}.txt" if clean_suffix else f"{task_id}.txt"
    path = input_dir / filename
    path.write_text(text, encoding="utf-8")
    return str(path.relative_to(PROJECT_ROOT))


def _apply_default_classical_reference_template(cfg: dict, *, manual_reference_provided: bool = False) -> dict:
    if cfg.get("category") != CLASSICAL_REFERENCE_REWRITE_CATEGORY or manual_reference_provided:
        return cfg
    if str(cfg.get("style_notes_path") or "").strip():
        return cfg

    status_info = default_reference_template_status()
    if not status_info.enabled:
        return cfg

    current_notes = str(cfg.get("style_notes") or "").strip()
    cfg["style_notes"] = "\n\n".join(
        part for part in (current_notes, DEFAULT_REFERENCE_STYLE_GUIDE.strip()) if part
    )
    cfg["style_notes_path"] = status_info.text_path
    cfg["style_notes_chars"] = len(cfg["style_notes"]) + status_info.chars
    cfg["default_style_reference_enabled"] = True
    cfg["default_style_reference_name"] = status_info.name
    cfg["default_style_reference_filename"] = status_info.source_filename
    if not str(cfg.get("style_reference") or "").strip():
        cfg["style_reference"] = status_info.name[:200]
    return cfg


def _dispatch_task(task: Task) -> None:
    try:
        if not acquire_task_dispatch_guard(str(task.id)):
            return
        if bool((task.config or {}).get("public_domain_longform_v2")):
            from app.tasks.tasks import process_public_domain_longform

            cfg = task.config or {}
            if (
                cfg.get("generation_mode") == "classical_vernacular"
                or cfg.get("public_domain_classical_vernacular")
            ):
                # A complete classical translation can legitimately run for
                # days.  Preserve the long task limit on every manual retry,
                # not only on the original public-domain endpoint.
                process_public_domain_longform.apply_async(
                    args=[str(task.id)],
                    time_limit=7 * 24 * 60 * 60,
                    soft_time_limit=7 * 24 * 60 * 60 - 300,
                )
            else:
                process_public_domain_longform.delay(str(task.id))
        else:
            process_video.delay(str(task.id))
    except Exception as e:
        # A broker outage must never leave a newly-created task hidden behind
        # the Redis dispatch lease.  The caller keeps the row pending and the
        # maintenance worker can safely publish it again later.
        clear_task_dispatch_guard(str(task.id))
        logger.exception("[task %s] Celery 投递失败", task.id)
        raise RuntimeError(f"Celery dispatch failed: {e}") from e


def _mark_dispatch_failure_pending(task: Task, exc: Exception, *, prefix: str = "Celery dispatch failed") -> None:
    """Record a publish failure without converting a runnable task to terminal failure."""

    clear_task_dispatch_guard(str(task.id))
    cfg = dict(task.config or {})
    cfg.update(
        dispatch_last_error=f"{prefix}: {exc}"[:1000],
        dispatch_last_failed_at=datetime.now(timezone.utc).isoformat(),
    )
    task.config = cfg
    task.status = TaskStatus.PENDING
    task.error_message = f"{prefix}: {exc}"[:2000]
    task.current_step = "任务已保存，后台队列投递失败，维护队列将自动重试"[:255]


def _task_output_dir(task_id: UUID) -> Path:
    return PROJECT_ROOT / "backend" / "output" / f"task_{task_id}"


def _safe_task_output_file(task_id: UUID, raw_path: object) -> Path | None:
    if not raw_path:
        return None
    output_dir = _task_output_dir(task_id).resolve()
    candidate = Path(str(raw_path))
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if resolved != output_dir and output_dir not in resolved.parents:
        return None
    return resolved


def _task_cover_path(task: Task) -> Path | None:
    configured = _safe_task_output_file(task.id, (task.config or {}).get("cover_path"))
    if configured and configured.exists():
        return configured
    fallback = _task_output_dir(task.id) / "cover.png"
    if fallback.exists():
        return fallback
    comic_dir = _task_output_dir(task.id) / "comic_images"
    for name in ("comic_cover.png", "comic_preface.png", "comic_section_01.png", "comic_section_01_01.png"):
        candidate = comic_dir / name
        if candidate.exists():
            return candidate
    return next(iter(sorted(comic_dir.glob("comic_section_*.png"))), None) if comic_dir.exists() else None


def _task_comic_image_path(task_id: UUID, filename: str) -> Path | None:
    if not re.fullmatch(r"(?:comic_cover|comic_preface|comic_section_\d+(?:_\d+)?)\.png", filename):
        return None
    path = (_task_output_dir(task_id) / "comic_images" / filename).resolve()
    output_dir = _task_output_dir(task_id).resolve()
    if output_dir not in path.parents:
        return None
    return path if path.exists() else None


_COMIC_PREVIEW_MAX_SIZE = (700, 1000)
_COMIC_PREVIEW_QUALITY = 82


def _task_comic_image_preview_path(task_id: UUID, filename: str) -> Path | None:
    return _comic_image_preview_path_from_output_dir(_task_output_dir(task_id), filename)


def _comic_image_preview_path_from_output_dir(output_dir: Path, filename: str) -> Path | None:
    if not re.fullmatch(r"(?:comic_cover|comic_preface|comic_section_\d+(?:_\d+)?)\.png", filename):
        return None
    resolved_output_dir = output_dir.resolve()
    original = (output_dir / "comic_images" / filename).resolve()
    if resolved_output_dir not in original.parents or not original.exists():
        return None
    preview_dir = output_dir / "comic_previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    preview_path = (preview_dir / f"{original.stem}.jpg").resolve()
    if resolved_output_dir not in preview_path.parents:
        return None
    if preview_path.exists() and preview_path.stat().st_mtime >= original.stat().st_mtime:
        return preview_path
    try:
        from PIL import Image, ImageOps

        with Image.open(original) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail(_COMIC_PREVIEW_MAX_SIZE, Image.Resampling.LANCZOS)
            if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                rgba = image.convert("RGBA")
                canvas = Image.new("RGB", rgba.size, "white")
                canvas.paste(rgba, mask=rgba.getchannel("A"))
                preview = canvas
            else:
                preview = image.convert("RGB")
            preview.save(
                preview_path,
                "JPEG",
                quality=_COMIC_PREVIEW_QUALITY,
                optimize=True,
                progressive=True,
            )
    except Exception:
        logger.exception("[task %s] 漫画预览图生成失败: %s", task_id, filename)
        return None
    return preview_path


def _build_task_image_download_response(
    task_id: UUID,
    filename: str,
    *,
    preview: bool = False,
) -> FileResponse:
    if preview:
        image_path = _task_comic_image_preview_path(task_id, filename)
        if image_path is None:
            raise HTTPException(status_code=404, detail=f"image preview {filename} of task {task_id} not found")
        download_name = f"{Path(filename).stem}_preview.jpg"
        media_type = "image/jpeg"
    else:
        image_path = _task_comic_image_path(task_id, filename)
        if image_path is None:
            raise HTTPException(status_code=404, detail=f"image {filename} of task {task_id} not found")
        download_name = image_path.name
        media_type = "image/png"
    return FileResponse(
        image_path,
        media_type=media_type,
        filename=download_name,
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
        },
    )


def _retry_resume_state(task_id: UUID) -> tuple[int, str]:
    output_dir = _task_output_dir(task_id)
    for filename, progress, step in _RETRY_STAGE_PROGRESS:
        path = output_dir / filename
        if path.exists() and path.stat().st_size > 0:
            return progress, step
    return 0, "排队中（重试）"


def _parse_config_datetime(raw: object) -> datetime | None:
    if not raw:
        return None
    try:
        value = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _auto_retry_not_before(task: Task) -> datetime | None:
    return _parse_config_datetime((task.config or {}).get("auto_retry_not_before"))


def _has_active_run(task: Task) -> bool:
    return bool(str((task.config or {}).get("active_run_id") or "").strip())


def _locked_owned_task(db: Session, task_id: UUID, owner_id: UUID):
    with db.no_autoflush:
        return db.execute(select(Task).where(Task.id == task_id, Task.owner_id == owner_id)
                          .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()


def _prepare_task_for_retry(task: Task) -> None:
    reason = similarity_retry_reason(task.config or {})
    if reason:
        raise HTTPException(status_code=409, detail=reason)
    cfg = dict(task.config or {})
    cfg.pop("active_run_id", None)
    cfg.pop("active_run_started_at", None)
    cfg.pop("active_run_heartbeat_at", None)
    cfg.pop("auto_retry_attempts", None)
    cfg.pop("last_auto_retry_error", None)
    cfg.pop("auto_retry_not_before", None)
    cfg.pop("auto_retry_scheduled_by", None)
    cfg.pop("auto_retry_exhausted_at", None)
    cfg.pop("pause_requested", None)
    cfg.pop("pause_requested_at", None)
    cfg.pop("paused_at", None)
    cfg.pop("paused_run_id", None)
    cfg.pop("last_pause_message", None)
    # A deliberate user retry is the only automatic-safety boundary that may
    # clear a paid-call hold/uncertainty.  Keep ``paid_call_response_id`` so a
    # provider response that did arrive can still be resumed rather than
    # submitted again.
    for key in (
        "generation_hold",
        "generation_hold_reason",
        "generation_hold_step",
        "generation_hold_at",
        "paid_call_delivery_uncertain",
        "paid_call_manual_resume_required",
        "paid_api_backoff",
        "paid_api_backoff_reason",
        "paid_api_backoff_at",
        "paid_api_backoff_error",
        "paid_api_backoff_until",
        "paid_api_backoff_attempts",
        "paid_api_backoff_last_due_at",
        "failed_auto_recovery_attempts",
        "failed_auto_recovered_at",
        "failed_auto_recovery_suppressed_at",
        "terminal_input_failure",
        "terminal_input_manual_review_at",
        "terminal_input_manual_review_reason",
        "dispatch_last_error",
        "dispatch_last_failed_at",
    ):
        cfg.pop(key, None)
    if cfg.get("public_domain_longform_v2"):
        # A retry starts a new validation run.  Do not keep rendering the
        # previous manual-review reason while the corrected pipeline is
        # already processing from its checkpoints.
        cfg["public_domain_quality_blocked"] = False
        cfg.pop("public_domain_quality_reason", None)
    if cfg.get("aigc_required"):
        cfg["aigc_status"] = "pending"
        cfg["aigc_passed"] = False
    task.config = cfg
    progress, step = _retry_resume_state(task.id)
    task.status = TaskStatus.PENDING
    task.progress = progress
    task.current_step = step
    task.error_message = None


def _clear_task_output_dir(task_id: UUID) -> None:
    output_dir = _task_output_dir(task_id)
    if not output_dir.exists():
        return
    import shutil

    shutil.rmtree(output_dir)
    if output_dir.exists():
        raise RuntimeError(f"旧阶段产物目录未清理完成: {output_dir}")


def _prepare_task_for_regeneration(db: Session, task: Task) -> None:
    if task.category == "general_longform":
        raise HTTPException(status_code=409, detail="通用长篇保留版本与验收状态，请在专属工作区局部修订或恢复断点")
    cfg = dict(task.config or {})
    if cfg.get("editor_v05_video_rejected"):
        raise HTTPException(status_code=409, detail=VIDEO_REJECTED)
    similarity_regeneration = cfg.get("editor_v05_status") == "similarity_blocked"
    if similarity_regeneration:
        if cfg.get("paid_call_delivery_uncertain") or cfg.get("paid_call_manual_resume_required"):
            raise HTTPException(status_code=409, detail="付费结果尚未核对，不能重新生成")
        import shutil

        output_dir = _task_output_dir(task.id)
        if output_dir.exists():
            archive = output_dir.parent / "_regeneration_history" / str(task.id) / str(uuid4())
            archive.parent.mkdir(parents=True, exist_ok=True)
            output_dir.rename(archive)
            try:
                output_dir.mkdir()
                ledger = archive / "generation_spend.json"
                if ledger.exists():
                    shutil.copy2(ledger, output_dir / ledger.name)
            except Exception:
                shutil.rmtree(output_dir, ignore_errors=True)
                archive.rename(output_dir)
                raise
            cfg["editor_v05_previous_generation_path"] = str(archive)
        for key in ("generation_hold", "generation_hold_reason", "generation_hold_step", "generation_hold_at"):
            cfg.pop(key, None)
        cfg["editor_v05_generation_attempt"] = int(cfg.get("editor_v05_generation_attempt") or
                                                    cfg.get("editor_v05_similarity_attempt") or 1) + 1
        cfg["editor_v05_status"] = "pending"
        cfg.pop("ai_or_finalizer_status", None)
    else:
        _clear_task_output_dir(task.id)
    db.query(Chapter).filter(Chapter.task_id == task.id).delete(synchronize_session=False)

    for key in _REGENERATE_CONFIG_DROP_KEYS:
        cfg.pop(key, None)
    cfg = _stamp_aigc_policy(cfg)
    cfg["regenerated_at"] = datetime.now(timezone.utc).isoformat()
    cfg["regenerated_from_bookshelf"] = True
    task.config = cfg
    task.status = TaskStatus.PENDING
    task.progress = 0
    task.current_step = "重新生成排队中"
    task.error_message = None
    task.cover_url = None


def _clean_candidate_url(raw: str) -> str:
    return (raw or "").strip().rstrip("，。；;、！？!?.,)）]】")


def _normalize_candidate_urls(urls: list[str]) -> tuple[list[str], list[str]]:
    seen: set[str] = set()
    unique: list[str] = []
    duplicates: list[str] = []
    for raw in urls:
        url = _clean_candidate_url(raw)
        if not url:
            continue
        if url in seen:
            duplicates.append(url)
            continue
        seen.add(url)
        unique.append(url)
    return unique, duplicates


def _config_with_source_platform(config: dict, source_url: str) -> dict:
    cfg = dict(config or {})
    if str(source_url or "").strip().startswith("manual://") or str(cfg.get("raw_text") or "").strip():
        return cfg
    source_input = source_url
    detected_source_platform = detect_video_source_platform(source_input)
    if detected_source_platform == "bilibili":
        source_input = resolve_bilibili_source_url(source_input)
    canonical_url = canonical_source_url(source_input)
    platform = detect_video_source_platform(canonical_url or source_url)
    if canonical_url:
        cfg["source_original_input"] = source_url
        cfg["source_canonical_url"] = canonical_url
    cfg["source_platform"] = platform
    cfg["source_platform_label"] = platform_label(platform)
    return cfg


def _render_chapters_markdown(chapters: list[Chapter]) -> str:
    lines: list[str] = ["@正文"]
    for chapter in sorted(chapters, key=lambda c: c.chapter_num):
        title = chapter.title.strip() or f"第{chapter.chapter_num}章"
        content = (chapter.content or "").strip()
        lines.extend([f"@{chapter.chapter_num}. {title}", content])
    return "\n".join(line for line in lines if line is not None).strip()


def _merge_chapters_into_markdown(markdown: str, chapters: list[Chapter]) -> str:
    body = _render_chapters_markdown(chapters)
    if not markdown:
        return normalize_final_markdown(body)
    match = _BODY_SECTION_RE.search(markdown)
    if not match:
        return normalize_final_markdown(body)
    front = markdown[: match.start()].rstrip()
    return normalize_final_markdown(f"{front}\n{body}")


def _sync_markdown_from_chapters(db: Session, task: Task) -> None:
    chapters = (
        db.query(Chapter)
        .filter(Chapter.task_id == task.id)
        .order_by(Chapter.chapter_num.asc())
        .all()
    )
    markdown = _merge_chapters_into_markdown(str((task.config or {}).get("markdown") or ""), chapters)
    cfg = dict(task.config or {})
    cfg["markdown"] = markdown
    cfg["markdown_chars"] = len(markdown)
    task.config = cfg


def _replace_title_in_markdown(markdown: str, title: str) -> str:
    clean_title = title.strip()
    if not markdown.strip():
        return f"@最终书名\n{clean_title}\n"

    match = _FINAL_TITLE_SECTION_RE.search(markdown)
    if match:
        return normalize_final_markdown(
            markdown[: match.start()]
            + f"@{match.group(1)}\n{clean_title}\n"
            + markdown[match.end() :]
        )
    return normalize_final_markdown(f"@最终书名\n{clean_title}\n{markdown}")


@router.post(
    "",
    response_model=CreateTaskResponse,
    status_code=status.HTTP_201_CREATED,
    summary="建任务并入 Celery 队列",
)
def create_task(
    req: CreateTaskRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CreateTaskResponse:
    _ensure_generation_capacity(db)
    raw_config = dict(req.config or {})
    raw_style_reference = str(raw_config.get("style_reference") or "").strip()
    raw_style_notes = str(raw_config.get("style_notes") or "").strip()
    cfg = _config_with_source_platform(_normalize_task_config(raw_config), req.url)
    if cfg.get("category") == CLASSICAL_REFERENCE_REWRITE_CATEGORY:
        if raw_style_reference:
            cfg["style_reference"] = raw_style_reference[:200]
        if raw_style_notes:
            cfg["style_notes"] = raw_style_notes
        cfg = _apply_default_classical_reference_template(cfg)
    if cfg.get("category") in CLASSICAL_CHINESE_CATEGORY_IDS:
        return _create_classical_text_task(source_url=req.url, config=cfg, db=db, user=user)
    return _create_and_dispatch_task(source_url=req.url, config=cfg, db=db, user=user)


def _reference_article_config(
    article: WeChatArticle,
    *,
    title: str | None = None,
) -> tuple[dict, str]:
    source_text = article.text.strip()
    source_title = (title or article.title or "").strip()[:120]
    base_config: dict[str, object] = {
        "category": "emotion_relationship",
        "input_source": "reference_article",
        "reference_source": "wechat_article",
        "wechat_article_url": article.source_url,
        "raw_text_chars": len(source_text),
    }
    if article.author:
        base_config["source_author"] = article.author[:120]
    if source_title:
        base_config["source_title"] = source_title
        base_config["title"] = source_title
    return _normalize_task_config(base_config), source_text


def _create_reference_article_task_from_article(
    article: WeChatArticle,
    *,
    db: Session,
    user: User,
    title: str | None = None,
) -> Task:
    cfg, source_text = _reference_article_config(article, title=title)
    task = Task(
        source_url=article.source_url,
        owner_id=user.id,
        config=cfg,
        status=TaskStatus.PENDING,
        progress=0,
        current_step="排队中",
    )
    db.add(task)
    db.flush()
    try:
        source_text_path = _persist_uploaded_source_text(task.id, source_text)
        cfg = dict(task.config or {})
        cfg.update(
            source_text_path=source_text_path,
            raw_text_chars=len(source_text),
            input_source="reference_article",
            reference_source="wechat_article",
            wechat_article_url=article.source_url,
        )
        if article.author:
            cfg["source_author"] = article.author[:120]
        source_title = (title or article.title or "").strip()[:120]
        if source_title:
            cfg["source_title"] = source_title
            cfg["title"] = source_title
        task.config = cfg
        db.commit()
        db.refresh(task)
    except Exception:
        db.rollback()
        raise
    return task


@router.post(
    "/reference-article/emotion-relationship",
    response_model=CreateTaskResponse,
    status_code=status.HTTP_201_CREATED,
    summary="参考文章生成心理情感长文",
)
def create_emotion_reference_article_task(
    req: ReferenceArticleTaskRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CreateTaskResponse:
    _ensure_generation_capacity(db)
    try:
        article = extract_wechat_article(req.url)
    except WeChatArticleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    source_text = article.text.strip()
    source_title = (req.title or article.title or "").strip()[:120]
    base_config: dict[str, object] = {
        "category": "emotion_relationship",
        "input_source": "reference_article",
        "reference_source": "wechat_article",
        "wechat_article_url": article.source_url,
        "raw_text_chars": len(source_text),
    }
    if article.author:
        base_config["source_author"] = article.author[:120]
    if source_title:
        base_config["source_title"] = source_title
        base_config["title"] = source_title
    cfg = _normalize_task_config(base_config)
    response = _create_and_dispatch_task(
        source_url=article.source_url,
        config=cfg,
        db=db,
        user=user,
        dispatch=False,
    )
    task = db.query(Task).filter(Task.id == response.task_id, Task.owner_id == user.id).first()
    if task is None:
        raise HTTPException(status_code=500, detail="任务创建后未能写入参考文章正文")
    try:
        source_text_path = _persist_uploaded_source_text(response.task_id, source_text)
        cfg = dict(task.config or {})
        cfg.update(
            source_text_path=source_text_path,
            raw_text_chars=len(source_text),
            input_source="reference_article",
            reference_source="wechat_article",
            wechat_article_url=article.source_url,
        )
        if article.author:
            cfg["source_author"] = article.author[:120]
        if source_title:
            cfg["source_title"] = source_title
        task.config = cfg
        db.commit()
        _dispatch_task(task)
    except Exception as exc:
        logger.exception("[task %s] 微信文章落盘或 Celery 投递失败", response.task_id)
        _mark_dispatch_failure_pending(task, exc, prefix="WeChat article dispatch failed")
        db.commit()
        raise HTTPException(
            status_code=503,
            detail=f"微信文章已读取，但任务投递失败 (task_id={response.task_id}): {exc}",
        ) from exc
    return response


@router.post(
    "/reference-article/emotion-relationship/batch",
    response_model=BatchCreateTaskResponse,
    status_code=status.HTTP_201_CREATED,
    summary="批量抓取公众号文章并生成心理情感长文",
)
def create_emotion_reference_article_task_batch(
    req: ReferenceArticleBatchTaskRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BatchCreateTaskResponse:
    urls, duplicate_urls = _normalize_candidate_urls(req.urls)
    if not urls:
        raise HTTPException(status_code=400, detail="公众号文章链接不能为空")
    if len(urls) > 100:
        raise HTTPException(status_code=400, detail="公众号文章一次最多批量提交 100 条")

    skipped: list[BatchCreateTaskItem] = [
        BatchCreateTaskItem(url=url, status="skipped", reason="批量列表内重复")
        for url in duplicate_urls
    ]
    existing_input_urls = {
        row[0]
        for row in db.query(Task.source_url)
        .filter(Task.owner_id == user.id, Task.source_url.in_(urls))
        .all()
    }

    fetched: list[tuple[str, WeChatArticle]] = []
    for url in urls:
        if url in existing_input_urls:
            skipped.append(BatchCreateTaskItem(url=url, status="skipped", reason="已存在任务"))
            continue
        try:
            article = extract_wechat_article(url)
        except WeChatArticleError as exc:
            skipped.append(BatchCreateTaskItem(url=url, status="skipped", reason=str(exc)[:300]))
            continue
        fetched.append((url, article))

    unique_articles: list[tuple[str, WeChatArticle]] = []
    seen_article_urls: set[str] = set()
    for submitted_url, article in fetched:
        if article.source_url in seen_article_urls:
            skipped.append(
                BatchCreateTaskItem(
                    url=submitted_url,
                    status="skipped",
                    reason="解析后与本批次其他公众号文章重复",
                )
            )
            continue
        seen_article_urls.add(article.source_url)
        unique_articles.append((submitted_url, article))

    canonical_urls = [article.source_url for _, article in unique_articles]
    existing_article_urls = {
        row[0]
        for row in db.query(Task.source_url)
        .filter(Task.owner_id == user.id, Task.source_url.in_(canonical_urls))
        .all()
    }
    createable = [
        (submitted_url, article)
        for submitted_url, article in unique_articles
        if article.source_url not in existing_article_urls
    ]
    for submitted_url, article in unique_articles:
        if article.source_url in existing_article_urls:
            skipped.append(BatchCreateTaskItem(url=submitted_url, status="skipped", reason="已存在任务"))

    if createable:
        _ensure_generation_capacity(db, requested=len(createable))

    created: list[BatchCreateTaskItem] = []
    tasks_to_dispatch: list[Task] = []
    for submitted_url, article in createable:
        try:
            task = _create_reference_article_task_from_article(article, db=db, user=user)
        except Exception as exc:
            logger.exception("批量公众号文章任务创建失败 url=%s", submitted_url)
            skipped.append(BatchCreateTaskItem(url=submitted_url, status="skipped", reason=str(exc)[:300]))
            continue
        tasks_to_dispatch.append(task)
        created.append(BatchCreateTaskItem(url=article.source_url, task_id=task.id, status=task.status))

    for task in tasks_to_dispatch:
        try:
            _dispatch_task(task)
        except Exception as exc:
            _mark_dispatch_failure_pending(task, exc, prefix="WeChat article batch dispatch failed")
            db.commit()
            for item in created:
                if item.task_id == task.id:
                    item.status = TaskStatus.PENDING
                    item.reason = str(exc)[:300]
                    break

    return BatchCreateTaskResponse(
        created=created,
        skipped=skipped,
        total_created=len(created),
        total_skipped=len(skipped),
    )


@router.post(
    "/classical-chinese/docx",
    response_model=CreateTaskResponse,
    status_code=status.HTTP_201_CREATED,
    summary="上传 Word/PDF 并创建古文白话任务",
)
async def create_classical_chinese_from_docx(
    file: UploadFile = File(...),
    reference_file: UploadFile | None = File(default=None),
    classical_variant: str = Form(default="plain"),
    category: str = Form(default="classical_chinese"),
    title: str = Form(default=""),
    style_reference: str = Form(default=""),
    style_notes: str = Form(default=""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CreateTaskResponse:
    _ensure_generation_capacity(db)
    filename = file.filename or "classical_chinese.docx"
    if Path(filename).suffix.lower() not in {".docx", ".pdf"}:
        raise HTTPException(status_code=400, detail="请上传 .docx 或 .pdf 格式的古文原文文件")
    content = await file.read()
    try:
        raw_text = extract_classical_document_text(content, filename)
    except DocxTextError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    try:
        normalized_category = normalize_category(category)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if normalized_category not in CLASSICAL_CHINESE_CATEGORY_IDS:
        raise HTTPException(status_code=400, detail="Word/PDF 上传只支持古文白话相关分类")
    reference_text = ""
    reference_filename = ""
    if reference_file is not None and (reference_file.filename or "").strip():
        if normalized_category != CLASSICAL_REFERENCE_REWRITE_CATEGORY:
            raise HTTPException(status_code=400, detail="参考译文文件只支持古文白话（参照译写版）")
        reference_filename = (reference_file.filename or "reference_translation.docx")[:255]
        if Path(reference_filename).suffix.lower() not in {".docx", ".pdf"}:
            raise HTTPException(status_code=400, detail="参考译文请上传 .docx 或 .pdf 格式的文件")
        reference_content = await reference_file.read()
        try:
            reference_text = extract_classical_document_text(reference_content, reference_filename).strip()
        except DocxTextError as exc:
            raise HTTPException(status_code=exc.status_code, detail=f"参考译文读取失败：{exc}") from exc
    cfg = _normalize_task_config(
        {
            "category": normalized_category,
            "classical_variant": classical_variant,
            "classical_original_title": (title.strip() or Path(filename).stem.strip())[:120],
            "title": (title.strip() or Path(filename).stem.strip())[:120],
            "raw_text_chars": len(raw_text),
            "source_filename": filename[:255],
            "input_source": "document_upload",
        }
    )
    style_notes_parts = [style_notes.strip()]
    if reference_text:
        style_notes_parts.append(f"【参考译文文件：{reference_filename}】\n{reference_text}")
        cfg["style_reference_filename"] = reference_filename
    style_notes_full = "\n\n".join(part for part in style_notes_parts if part.strip())
    if style_notes_full:
        cfg["style_notes"] = style_notes_full
    if not reference_text:
        cfg = _apply_default_classical_reference_template(cfg)
        style_notes_full = str(cfg.get("style_notes") or "").strip()
    should_externalize_style_notes = (
        normalized_category == CLASSICAL_REFERENCE_REWRITE_CATEGORY
        and len(style_notes_full) > STYLE_NOTES_INLINE_LIMIT
        and not str(cfg.get("style_notes_path") or "").strip()
    )
    effective_style_reference = style_reference.strip() or reference_filename or str(cfg.get("style_reference") or "").strip()
    if effective_style_reference:
        cfg["style_reference"] = effective_style_reference[:200]
    if style_notes_full:
        cfg["style_notes"] = (
            style_notes_full[:STYLE_NOTES_INLINE_LIMIT]
            if should_externalize_style_notes
            else style_notes_full
        )
        cfg["style_notes_chars"] = len(style_notes_full)
        if should_externalize_style_notes:
            cfg["style_notes_preview_chars"] = len(cfg["style_notes"])
    response = _create_and_dispatch_task(
        source_url=f"manual://{normalized_category}_document/{filename[:120]}",
        config=_stamp_aigc_policy(cfg),
        db=db,
        user=user,
        dispatch=False,
    )
    task = db.query(Task).filter(Task.id == response.task_id, Task.owner_id == user.id).first()
    if task is None:
        raise HTTPException(status_code=500, detail="任务创建后未能写入 Word 正文")
    try:
        source_text_path = _persist_uploaded_source_text(response.task_id, raw_text)
        cfg = dict(task.config or {})
        cfg.update(source_text_path=source_text_path, raw_text_chars=len(raw_text))
        if should_externalize_style_notes:
            cfg["style_notes_path"] = _persist_uploaded_source_text(
                response.task_id,
                style_notes_full,
                suffix="style_reference",
            )
        task.config = cfg
        db.commit()
        _dispatch_task(task)
    except Exception as exc:
        logger.exception("[task %s] Word 正文落盘或 Celery 投递失败", response.task_id)
        _mark_dispatch_failure_pending(task, exc, prefix="Word upload dispatch failed")
        db.commit()
        raise HTTPException(
            status_code=503,
            detail=f"Word/PDF 文档已读取，但任务投递失败 (task_id={response.task_id}): {exc}",
        ) from exc
    return response


@router.get(
    "/classical-reference-rewrite/default-reference",
    response_model=ClassicalReferenceTemplateResponse,
    summary="获取古文白话参照译写默认参考范本状态",
)
def get_classical_reference_rewrite_default_reference() -> ClassicalReferenceTemplateResponse:
    status_info = default_reference_template_status()
    return ClassicalReferenceTemplateResponse(
        enabled=status_info.enabled,
        name=status_info.name,
        source_filename=status_info.source_filename,
        chars=status_info.chars,
        updated_at=status_info.updated_at,
    )


@router.get(
    "/categories",
    response_model=TaskCategoryListResponse,
    summary="获取可选书籍方向分类",
)
def get_task_categories() -> TaskCategoryListResponse:
    return TaskCategoryListResponse(items=list_categories())


@router.post(
    "/batch",
    response_model=BatchCreateTaskResponse,
    status_code=status.HTTP_201_CREATED,
    summary="候选自动生成库：批量去重建任务并逐个入队",
)
def create_task_batch(
    req: BatchCreateTaskRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BatchCreateTaskResponse:
    urls, duplicate_urls = _normalize_candidate_urls(req.urls)
    if not urls:
        raise HTTPException(status_code=400, detail="候选链接不能为空")
    if len(urls) > 500:
        raise HTTPException(status_code=400, detail="一次最多放入 500 个链接")

    cfg = _normalize_task_config(req.config)
    if cfg.get("category") in CLASSICAL_CHINESE_CATEGORY_IDS:
        raise HTTPException(status_code=400, detail="古文白话不支持候选链接库，请单条粘贴文言文原文")
    if cfg.get("category") == "comic_series" or is_healing_comic_family(cfg.get("category")):
        label = str(cfg.get("category_label") or "插画")
        raise HTTPException(status_code=400, detail=f"{label}不支持候选链接库，请使用{label}入口输入 5 或 6 条短语")
    if cfg.get("category") == "general_longform":
        raise HTTPException(status_code=400, detail="通用长篇请创建独立工作区，确认大纲后逐章执行")
    existing_urls = {
        row[0]
        for row in db.query(Task.source_url)
        .filter(Task.owner_id == user.id, Task.source_url.in_(urls))
        .all()
    }
    createable_count = sum(1 for url in urls if url not in existing_urls)
    if createable_count:
        _ensure_generation_capacity(db, requested=createable_count)

    created: list[BatchCreateTaskItem] = []
    skipped: list[BatchCreateTaskItem] = [
        BatchCreateTaskItem(url=url, status="skipped", reason="候选列表内重复")
        for url in duplicate_urls
    ]
    tasks_to_dispatch: list[Task] = []
    batch_capacity = createable_count

    for url in urls:
        if url in existing_urls:
            skipped.append(BatchCreateTaskItem(url=url, status="skipped", reason="已存在任务"))
            continue
        if len(tasks_to_dispatch) >= batch_capacity:
            skipped.append(
                BatchCreateTaskItem(
                    url=url,
                    status="skipped",
                    reason="已达到当前并发保护上限，未入队",
                )
            )
            continue
        task = Task(
            source_url=url,
            owner_id=user.id,
            config=_stamp_aigc_policy(_config_with_source_platform(cfg, url)),
            status=TaskStatus.PENDING,
            progress=0,
            current_step="排队中",
        )
        db.add(task)
        db.flush()
        tasks_to_dispatch.append(task)
        created.append(BatchCreateTaskItem(url=url, task_id=task.id, status=task.status))

    db.commit()

    for task in tasks_to_dispatch:
        try:
            _dispatch_task(task)
        except Exception as exc:
            _mark_dispatch_failure_pending(task, exc, prefix="Batch dispatch failed")
            db.commit()
            for item in created:
                if item.task_id == task.id:
                    item.status = TaskStatus.PENDING
                    item.reason = str(exc)[:300]
                    break

    return BatchCreateTaskResponse(
        created=created,
        skipped=skipped,
        total_created=len(created),
        total_skipped=len(skipped),
    )


@router.post(
    "/retry-failed",
    response_model=BatchRetryTaskResponse,
    summary="一键继续生成失败任务（断点续跑）",
)
def retry_failed_tasks(
    req: BatchRetryTaskRequest = Body(default_factory=BatchRetryTaskRequest),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BatchRetryTaskResponse:
    _ensure_generation_capacity(db)
    requested_ids = list(dict.fromkeys(req.task_ids or []))

    base_query = db.query(Task).filter(Task.owner_id == user.id)
    skipped: list[BatchRetryTaskItem] = []
    if requested_ids:
        locked = (
            base_query.filter(
                Task.id.in_(requested_ids),
                Task.status.in_((TaskStatus.FAILED, TaskStatus.MANUAL_REVIEW)),
            )
            .with_for_update(skip_locked=True)
            .all()
        )
        found_by_id = {task.id: task for task in locked}
        tasks = [found_by_id[task_id] for task_id in requested_ids if task_id in found_by_id]
        for task_id in requested_ids:
            if task_id not in found_by_id:
                skipped.append(
                    BatchRetryTaskItem(
                        task_id=task_id,
                        status="skipped",
                        reason="任务不存在、无权限、已在继续生成或不是失败状态",
                    )
                )
    else:
        tasks = (
            base_query.filter(Task.status.in_((TaskStatus.FAILED, TaskStatus.MANUAL_REVIEW)))
            .with_for_update(skip_locked=True)
            .order_by(Task.created_at.desc())
            .all()
        )

    retried: list[BatchRetryTaskItem] = []
    tasks_to_dispatch: list[Task] = []
    for task in tasks:
        if task.status not in {TaskStatus.FAILED, TaskStatus.MANUAL_REVIEW}:
            skipped.append(
                BatchRetryTaskItem(
                    task_id=task.id,
                    status="skipped",
                    reason=f"当前状态为 {task.status}，无需继续生成",
                )
            )
            continue
        reason = similarity_retry_reason(task.config or {})
        if reason:
            skipped.append(BatchRetryTaskItem(task_id=task.id, status="skipped", reason=reason))
            continue
        if bool((task.config or {}).get("public_domain_longform_v2")):
            if bool((task.config or {}).get("generation_complete")):
                skipped.append(
                    BatchRetryTaskItem(
                        task_id=task.id,
                        status="skipped",
                        reason="公版长篇正文已完成，请在单本详情仅重跑朱雀阶段",
                    )
                )
                continue
            _prepare_task_for_retry(task)
            tasks_to_dispatch.append(task)
            retried.append(
                BatchRetryTaskItem(
                    task_id=task.id,
                    status=TaskStatus.PENDING,
                    reason="从公版证据/规划/合格章节断点继续生成",
                )
            )
            continue
        if bool((task.config or {}).get("public_domain_rewrite")):
            skipped.append(
                BatchRetryTaskItem(
                    task_id=task.id,
                    status="skipped",
                    reason="公版融合任务必须仅重跑朱雀阶段，已跳过以避免重复消耗整套生成 API",
                )
            )
            continue
        _prepare_task_for_retry(task)
        tasks_to_dispatch.append(task)
        retried.append(
            BatchRetryTaskItem(
                task_id=task.id,
                status=TaskStatus.PENDING,
                reason=task.current_step,
            )
        )

    db.commit()

    for task in tasks_to_dispatch:
        try:
            _dispatch_task(task)
        except Exception as exc:
            logger.exception("[task %s] Celery 批量重试投递失败", task.id)
            _mark_dispatch_failure_pending(task, exc)
            db.commit()
            for item in retried:
                if item.task_id == task.id:
                    item.status = TaskStatus.PENDING
                    item.reason = str(exc)[:300]
                    break

    return BatchRetryTaskResponse(
        retried=retried,
        skipped=skipped,
        total_retried=len(retried),
        total_skipped=len(skipped),
    )


@router.post(
    "/regenerate",
    response_model=BatchRegenerateTaskResponse,
    summary="批量从头重新生成选中书稿",
)
def regenerate_tasks(
    req: BatchRegenerateTaskRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BatchRegenerateTaskResponse:
    requested_ids = list(dict.fromkeys(req.task_ids))
    base_query = db.query(Task).filter(Task.owner_id == user.id)
    locked = (
        base_query.filter(Task.id.in_(requested_ids))
        .with_for_update(skip_locked=True)
        .all()
    )
    found_by_id = {task.id: task for task in locked}
    tasks = [found_by_id[task_id] for task_id in requested_ids if task_id in found_by_id]
    skipped: list[BatchRegenerateTaskItem] = []
    for task_id in requested_ids:
        if task_id not in found_by_id:
            skipped.append(
                BatchRegenerateTaskItem(
                    task_id=task_id,
                    status="skipped",
                    reason="任务不存在、无权限或正在被其他操作锁定",
                )
            )

    regeneratable: list[Task] = []
    for task in tasks:
        if task.status in _ACTIVE_TASK_STATUSES or _has_active_run(task):
            skipped.append(
                BatchRegenerateTaskItem(
                    task_id=task.id,
                    status="skipped",
                    reason="任务正在排队或生成中，不能重复重新生成",
                )
            )
            continue
        if task.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.MANUAL_REVIEW}:
            skipped.append(
                BatchRegenerateTaskItem(
                    task_id=task.id,
                    status="skipped",
                    reason=f"当前状态为 {task.status}，不能从头重新生成",
                )
            )
            continue
        if (task.config or {}).get("editor_v05_video_rejected"):
            skipped.append(
                BatchRegenerateTaskItem(
                    task_id=task.id,
                    status="skipped",
                    reason=f"{VIDEO_REJECTED}：两次生成与原视频相似度均不低于50%，不再重新生成",
                )
            )
            continue
        regeneratable.append(task)

    if regeneratable:
        _ensure_generation_capacity(db, requested=len(regeneratable))

    regenerated: list[BatchRegenerateTaskItem] = []
    tasks_to_dispatch: list[Task] = []
    for task in regeneratable:
        try:
            _prepare_task_for_regeneration(db, task)
        except Exception as exc:
            logger.exception("[task %s] 批量重新生成清理旧产物失败", task.id)
            skipped.append(
                BatchRegenerateTaskItem(
                    task_id=task.id,
                    status="skipped",
                    reason=f"旧产物清理失败: {exc}",
                )
            )
            continue
        tasks_to_dispatch.append(task)
        regenerated.append(
            BatchRegenerateTaskItem(
                task_id=task.id,
                status=TaskStatus.PENDING,
                reason=task.current_step,
            )
        )

    db.commit()

    for task in tasks_to_dispatch:
        try:
            _dispatch_task(task)
        except Exception as exc:
            logger.exception("[task %s] 批量重新生成投递失败", task.id)
            _mark_dispatch_failure_pending(task, exc)
            db.commit()
            for item in regenerated:
                if item.task_id == task.id:
                    item.status = TaskStatus.PENDING
                    item.reason = str(exc)[:300]
                    break

    return BatchRegenerateTaskResponse(
        regenerated=regenerated,
        skipped=skipped,
        total_regenerated=len(regenerated),
        total_skipped=len(skipped),
    )


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除任务及其所有章节（仅任务主人可操作）",
)
def delete_task(
    task_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    try:
        db.delete(task)  # chapters 走 cascade
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("[task %s] 删除失败", task_id)
        raise HTTPException(status_code=500, detail=f"删除失败: {exc}") from exc

    # 顺手清掉中间产物目录
    output_dir = PROJECT_ROOT / "backend" / "output" / f"task_{task_id}"
    if output_dir.exists():
        import shutil

        shutil.rmtree(output_dir, ignore_errors=True)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{task_id}/pause-comic",
    response_model=TaskStatusResponse,
    summary="暂停漫画生成任务",
)
def pause_comic_task(
    task_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TaskStatusResponse:
    task = _locked_owned_task(db, task_id, user.id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    cfg = dict(task.config or {})
    category = str(cfg.get("category") or "").strip()
    if not is_healing_comic_family(category):
        raise HTTPException(status_code=409, detail="当前任务不是漫画分类，不能使用漫画暂停")
    if task.status == TaskStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="任务已完成，不能暂停")
    if task.status == TaskStatus.FAILED:
        raise HTTPException(status_code=409, detail="任务已失败，请直接继续生成")
    if task.status == TaskStatus.PAUSED:
        return TaskStatusResponse.model_validate(task)

    now = datetime.now(timezone.utc).isoformat()
    cfg["pause_requested"] = True
    cfg["pause_requested_at"] = now
    if task.status == TaskStatus.PENDING and not _has_active_run(task):
        cfg["pause_requested"] = False
        cfg["paused_at"] = now
        task.status = TaskStatus.PAUSED
        task.current_step = "已暂停，可点击继续生成从断点恢复"
    else:
        task.current_step = "暂停请求已发送，当前图片完成后停止"
    task.config = cfg
    task.error_message = None
    db.commit()
    db.refresh(task)
    return TaskStatusResponse.model_validate(task)


@router.post(
    "/{task_id}/retry",
    response_model=CreateTaskResponse,
    summary="重新投递任务（卡 0% 或失败时使用，仅任务主人可操作）",
)
def retry_task(
    task_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CreateTaskResponse:
    _ensure_generation_capacity(db)
    task = _locked_owned_task(db, task_id, user.id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    reason = similarity_retry_reason(task.config or {})
    if reason:
        raise HTTPException(status_code=409, detail=reason)
    if task.status == TaskStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="任务已完成，不能重新投递，避免重复消耗 API。")
    if task.status == TaskStatus.PROCESSING or _has_active_run(task):
        raise HTTPException(status_code=409, detail="任务正在生成中，系统会自动续跑，请勿重复投递。")
    not_before = _auto_retry_not_before(task)
    if task.status == TaskStatus.PENDING:
        cfg = dict(task.config or {})
        # A user clicking retry on a held pending row explicitly releases the
        # hold.  Without this branch such rows were returned unchanged forever
        # because the endpoint treated every pending task as already queued.
        if any(
            cfg.get(key)
            for key in (
                "generation_hold",
                "paid_call_delivery_uncertain",
                "paid_call_manual_resume_required",
                "paid_api_backoff",
            )
        ):
            _prepare_task_for_retry(task)
            db.commit()
            db.refresh(task)
            try:
                _dispatch_task(task)
            except Exception as exc:
                _mark_dispatch_failure_pending(task, exc)
                db.commit()
                raise HTTPException(status_code=503, detail=f"重试投递失败 (task_id={task.id}): {exc}") from exc
            return CreateTaskResponse(task_id=task.id, status=task.status)
        if not_before and not_before > datetime.now(timezone.utc):
            return CreateTaskResponse(task_id=task.id, status=task.status)
        return CreateTaskResponse(task_id=task.id, status=task.status)
    if task.status not in {TaskStatus.FAILED, TaskStatus.PAUSED, TaskStatus.MANUAL_REVIEW}:
        raise HTTPException(status_code=409, detail=f"当前状态为 {task.status}，不需要重新投递。")

    if bool((task.config or {}).get("public_domain_longform_v2")):
        cfg = dict(task.config or {})
        if bool(cfg.get("generation_complete")):
            raise HTTPException(
                status_code=409,
                detail="公版长篇正文已完成；若仅朱雀未通过，请使用“仅重跑朱雀阶段”。",
            )
        _prepare_task_for_retry(task)
        task.current_step = "从公版证据、规划和已通过章节断点继续生成"
        db.commit()
        db.refresh(task)
        try:
            _dispatch_task(task)
        except Exception as e:
            _mark_dispatch_failure_pending(task, e, prefix="Public-domain dispatch failed")
            db.commit()
            raise HTTPException(status_code=503, detail=f"公版长篇重试投递失败: {e}") from e
        return CreateTaskResponse(task_id=task.id, status=task.status)
    if bool((task.config or {}).get("public_domain_rewrite")):
        raise HTTPException(
            status_code=409,
            detail="公版融合任务请使用“仅重跑朱雀阶段”，避免重新消耗整套生成 API。",
        )

    _prepare_task_for_retry(task)
    db.commit()
    db.refresh(task)

    try:
        _dispatch_task(task)
    except Exception as e:
        logger.exception("[task %s] Celery 重新投递失败", task.id)
        _mark_dispatch_failure_pending(task, e)
        db.commit()
        raise HTTPException(
            status_code=503,
            detail=f"重试失败 (task_id={task.id}): {e}",
        )

    return CreateTaskResponse(task_id=task.id, status=task.status)


@router.post(
    "/{task_id}/retry-aigc",
    response_model=CreateTaskResponse,
    summary="仅从不可变 04_format.md 原稿重跑朱雀终稿阶段",
)
def retry_task_aigc(
    task_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CreateTaskResponse:
    """Resume at the v2 gate without regenerating paid upstream stages."""
    task = _locked_owned_task(db, task_id, user.id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    cfg = dict(task.config or {})
    reason = similarity_retry_reason(cfg)
    if reason:
        raise HTTPException(status_code=409, detail=reason)
    if bool(cfg.get("public_domain_rewrite")):
        raise HTTPException(
            status_code=409,
            detail="公版融合任务请在公版接口重跑朱雀阶段。",
        )
    if not bool(cfg.get("aigc_required")) or cfg.get("aigc_policy_version") != AIGC_POLICY_VERSION:
        raise HTTPException(status_code=409, detail="该任务没有绑定朱雀 v2 策略，不能使用 AIGC-only 重试。")
    if task.status not in {TaskStatus.MANUAL_REVIEW, TaskStatus.FAILED}:
        raise HTTPException(status_code=409, detail=f"当前状态为 {task.status}，不能仅重跑朱雀阶段。")
    original_path = _task_output_dir(task.id) / "04_format.md"
    if not original_path.exists() or original_path.stat().st_size <= 0:
        raise HTTPException(status_code=409, detail="不可变 04_format.md 原稿不存在，无法安全执行 AIGC-only 重试。")
    if _has_active_run(task):
        raise HTTPException(status_code=409, detail="任务已有运行实例，请勿重复投递。")

    _ensure_generation_capacity(db)
    _prepare_task_for_retry(task)
    task.progress = max(91, int(task.progress or 0))
    task.current_step = "仅重跑朱雀终稿阶段（已复用 04_format.md 原稿与可用检测缓存）"
    db.commit()
    db.refresh(task)
    try:
        _dispatch_task(task)
    except Exception as exc:
        logger.exception("[task %s] AIGC-only dispatch failed", task.id)
        _mark_dispatch_failure_pending(task, exc, prefix="AIGC-only dispatch failed")
        db.commit()
        raise HTTPException(status_code=503, detail=f"朱雀阶段重试投递失败: {exc}") from exc
    return CreateTaskResponse(task_id=task.id, status=task.status)


@router.get(
    "/{task_id}/aigc-report",
    summary="读取不含密钥的朱雀 v2 报告与人工复核候选",
)
def get_task_aigc_report(
    task_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, object]:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    output_dir = _task_output_dir(task.id).resolve()
    report_path = output_dir / "aigc_quality_report.json"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="朱雀报告尚未生成")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=500, detail=f"朱雀报告读取失败: {exc}") from exc
    if not isinstance(report, dict):
        raise HTTPException(status_code=500, detail="朱雀报告格式不正确")

    review_candidates: list[dict[str, object]] = []
    for item in report.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        candidate = {key: value for key, value in item.items() if key != "artifactPath"}
        artifact = item.get("artifactPath")
        candidate["artifactFile"] = Path(str(artifact)).name if artifact else None
        text = ""
        if artifact:
            try:
                artifact_path = Path(str(artifact)).resolve()
                if artifact_path.is_relative_to(output_dir) and artifact_path.exists():
                    text = artifact_path.read_text(encoding="utf-8")
            except (OSError, ValueError):
                logger.warning("[task %s] invalid AIGC candidate artifact: %s", task.id, artifact)
        candidate["text"] = text
        review_candidates.append(candidate)

    safe_candidates = [
        item
        for item in review_candidates
        if item.get("deterministicPassed") is True and isinstance(item.get("detection"), dict)
    ]
    safe_candidates.sort(
        key=lambda item: (
            int((item.get("detection") or {}).get("score") or 101),
            0 if item.get("selected") else 1,
            -int(item.get("round") or 0),
        )
    )
    sanitized = dict(report)
    sanitized["candidates"] = review_candidates
    sanitized["selectedCandidates"] = [item for item in review_candidates if item.get("selected")]
    sanitized["bestSafeCandidate"] = safe_candidates[0] if safe_candidates else None
    sanitized["originalAvailable"] = (output_dir / "04_format.md").exists()
    if bool((task.config or {}).get("aigc_review_archive")):
        candidate_path = output_dir / "05_aigc_candidate.md"
        try:
            report_candidate = (
                candidate_path.read_text(encoding="utf-8")
                if candidate_path.exists()
                else ""
            )
        except OSError:
            logger.warning("[task %s] failed to read report-driven candidate", task.id)
            report_candidate = ""
        sanitized["reportDrivenCandidateText"] = report_candidate
        sanitized["reportDrivenExperimentType"] = (task.config or {}).get(
            "aigc_experiment_type"
        )
        sanitized["reportDrivenQualityBypassed"] = bool(
            (task.config or {}).get("aigc_experimental_quality_bypassed")
        )
    return sanitized


def _build_list_item(db: Session, task: Task) -> TaskListItem:
    """把 Task 包装成列表卡片视图，含字数与章节数。"""
    cfg = task.config or {}
    raw_category = str(cfg.get("category") or "").strip() or None
    category = None
    category_label = str(cfg.get("category_label") or "").strip() or None
    if raw_category:
        try:
            category = normalize_category(raw_category)
            category_meta = get_category_meta(category)
            category_label = category_label or category_meta["label"]
        except ValueError:
            category = raw_category
    word_count = int(cfg.get("markdown_chars") or 0)
    chapter_count = (
        db.query(func.count(Chapter.id)).filter(Chapter.task_id == task.id).scalar() or 0
    )
    if not word_count and chapter_count:
        word_count = (
            db.query(func.coalesce(func.sum(Chapter.word_count), 0))
            .filter(Chapter.task_id == task.id)
            .scalar() or 0
        )
    title = cfg.get("title")
    if not title and chapter_count:
        first_chapter = (
            db.query(Chapter)
            .filter(Chapter.task_id == task.id)
            .order_by(Chapter.chapter_num.asc())
            .first()
        )
        if first_chapter is not None:
            title = first_chapter.title

    return TaskListItem(
        id=task.id,
        source_url=task.source_url,
        status=task.status,
        progress=task.progress,
        current_step=task.current_step,
        cover_url=task.cover_url,
        created_at=task.created_at,
        title=title,
        bookshelf=str(cfg.get("bookshelf")).strip() if cfg.get("bookshelf") else category_label,
        category=category,
        category_label=category_label,
        word_count=int(word_count),
        chapter_count=int(chapter_count),
    )


_LIST_CONFIG_DROP_KEYS = ("markdown", "raw_text", "video_text", "transcript")


def _task_list_config_expr():
    expr = Task.config
    for key in _LIST_CONFIG_DROP_KEYS:
        expr = expr.op("-")(key)
    return expr.label("config")


def _build_list_item_from_row(row) -> TaskListItem:
    """Build a bookshelf card without loading full manuscript JSON."""
    cfg = dict(row.config or {})
    raw_category = str(cfg.get("category") or "").strip() or None
    category = None
    category_label = str(cfg.get("category_label") or "").strip() or None
    if raw_category:
        try:
            category = normalize_category(raw_category)
            category_meta = get_category_meta(category)
            category_label = category_label or category_meta["label"]
        except ValueError:
            category = raw_category

    chapter_count = int(row.chapter_count or 0)
    word_count = int(cfg.get("markdown_chars") or row.chapter_words or 0)
    title = cfg.get("title") or row.first_chapter_title
    bookshelf = str(cfg.get("bookshelf")).strip() if cfg.get("bookshelf") else category_label

    return TaskListItem(
        id=row.id,
        source_url=row.source_url,
        status=row.status,
        progress=row.progress,
        current_step=row.current_step,
        cover_url=row.cover_url,
        created_at=row.created_at,
        title=title,
        bookshelf=bookshelf,
        category=category,
        category_label=category_label,
        word_count=word_count,
        chapter_count=chapter_count,
    )


def _cleanup_expired_tasks(db: Session, user: User) -> int:
    """Delete terminal tasks older than the retention window without touching active runs."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=_TASK_RETENTION_DAYS)
    expired = (
        db.query(Task)
        .filter(
            Task.owner_id == user.id,
            Task.created_at < cutoff,
            ~Task.status.in_((*_ACTIVE_TASK_STATUSES, TaskStatus.PAUSED, TaskStatus.MANUAL_REVIEW)),
        )
        .all()
    )
    if not expired:
        return 0

    expired_ids = [task.id for task in expired]
    try:
        for task in expired:
            db.delete(task)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("自动清理超过 %d 天的书籍失败", _TASK_RETENTION_DAYS)
        return 0

    import shutil

    for task_id in expired_ids:
        output_dir = PROJECT_ROOT / "backend" / "output" / f"task_{task_id}"
        if output_dir.exists():
            shutil.rmtree(output_dir, ignore_errors=True)

    logger.info("自动清理超过 %d 天的书籍 %d 本", _TASK_RETENTION_DAYS, len(expired_ids))
    return len(expired_ids)


def _read_task_markdown(db: Session, task: Task) -> str:
    cfg = task.config or {}
    md = cfg.get("markdown")
    if md:
        return _normalize_download_markdown(str(md), category=str(cfg.get("category") or ""))

    chapters = (
        db.query(Chapter)
        .filter(Chapter.task_id == task.id)
        .order_by(Chapter.chapter_num.asc())
        .all()
    )
    return _normalize_download_markdown(
        "\n".join(c.content if task.category == "general_longform" else f"@{c.title}\n{c.content}" for c in chapters),
        category=str(cfg.get("category") or ""),
    )


def _safe_download_name(task: Task, fmt: str, *, suffix: str = "") -> str:
    title = task.title or f"book_{task.id}"
    safe = re.sub(r'[\\/:*?"<>|\r\n]+', "_", title).strip(" ._") or f"book_{task.id}"
    clean_suffix = re.sub(r'[\\/:*?"<>|\r\n]+', "_", suffix).strip(" ._")
    base = f"{safe[:80]}_{clean_suffix}" if clean_suffix else safe[:80]
    return f"{base}.{fmt}"


def _unique_zip_name(filename: str, used: set[str]) -> str:
    if filename not in used:
        used.add(filename)
        return filename
    if "." in filename:
        stem, ext = filename.rsplit(".", 1)
        suffix = f".{ext}"
    else:
        stem, suffix = filename, ""
    idx = 2
    while True:
        candidate = f"{stem}_{idx}{suffix}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        idx += 1


def _docx_text_runs(line: str, *, allow_markdown_bold: bool = False) -> str:
    if not allow_markdown_bold or "**" not in line:
        text = escape(line, {'"': "&quot;"})
        return f"<w:r><w:t xml:space=\"preserve\">{text}</w:t></w:r>"
    runs: list[str] = []
    cursor = 0
    for match in re.finditer(r"\*\*([^*\n]+)\*\*", line):
        if match.start() > cursor:
            text = escape(line[cursor : match.start()], {'"': "&quot;"})
            runs.append(f"<w:r><w:t xml:space=\"preserve\">{text}</w:t></w:r>")
        bold_text = escape(match.group(1), {'"': "&quot;"})
        runs.append(
            "<w:r><w:rPr><w:b/></w:rPr>"
            f"<w:t xml:space=\"preserve\">{bold_text}</w:t></w:r>"
        )
        cursor = match.end()
    if cursor < len(line):
        text = escape(line[cursor:], {'"': "&quot;"})
        runs.append(f"<w:r><w:t xml:space=\"preserve\">{text}</w:t></w:r>")
    return "".join(runs) or "<w:r><w:t></w:t></w:r>"


def _docx_text_paragraph(line: str, *, allow_markdown_bold: bool = False) -> str:
    runs = _docx_text_runs(line, allow_markdown_bold=allow_markdown_bold)
    return (
        "<w:p><w:pPr><w:spacing w:before=\"0\" w:after=\"0\" "
        "w:line=\"240\" w:lineRule=\"auto\"/></w:pPr>"
        f"{runs}</w:p>"
    )


_DOCX_EMU_PER_PIXEL = 9525
_DOCX_FALLBACK_IMAGE_SIZE_EMU = (5486400, 3291840)


def _docx_image_size_emu(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            width, height = image.size
    except Exception:
        return _DOCX_FALLBACK_IMAGE_SIZE_EMU
    return max(1, int(width)) * _DOCX_EMU_PER_PIXEL, max(1, int(height)) * _DOCX_EMU_PER_PIXEL


def _docx_media_extension(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    if suffix in {"jpg", "jpeg"}:
        return "jpg"
    return "png"


def _docx_image_paragraph(
    *,
    rel_id: str,
    filename: str,
    alt: str,
    doc_pr_id: int,
    cx: int,
    cy: int,
) -> str:
    safe_filename = escape(filename, {'"': "&quot;"})
    safe_alt = escape(alt or filename, {'"': "&quot;"})
    return (
        "<w:p><w:pPr><w:jc w:val=\"center\"/></w:pPr><w:r><w:drawing>"
        "<wp:inline distT=\"0\" distB=\"0\" distL=\"0\" distR=\"0\">"
        f"<wp:extent cx=\"{cx}\" cy=\"{cy}\"/>"
        "<wp:effectExtent l=\"0\" t=\"0\" r=\"0\" b=\"0\"/>"
        f"<wp:docPr id=\"{doc_pr_id}\" name=\"Picture {doc_pr_id}\" descr=\"{safe_alt}\"/>"
        "<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect=\"1\"/>"
        "</wp:cNvGraphicFramePr>"
        "<a:graphic><a:graphicData uri=\"http://schemas.openxmlformats.org/drawingml/2006/picture\">"
        "<pic:pic>"
        f"<pic:nvPicPr><pic:cNvPr id=\"{doc_pr_id}\" name=\"{safe_filename}\" descr=\"{safe_alt}\"/>"
        "<pic:cNvPicPr/></pic:nvPicPr>"
        f"<pic:blipFill><a:blip r:embed=\"{rel_id}\"/>"
        "<a:stretch><a:fillRect/></a:stretch></pic:blipFill>"
        f"<pic:spPr><a:xfrm><a:off x=\"0\" y=\"0\"/><a:ext cx=\"{cx}\" cy=\"{cy}\"/>"
        "</a:xfrm><a:prstGeom prst=\"rect\"><a:avLst/></a:prstGeom></pic:spPr>"
        "</pic:pic></a:graphicData></a:graphic>"
        "</wp:inline></w:drawing></w:r></w:p>"
    )


_DOCX_FRONT_SECTION_RE = re.compile(
    r"^(?:！！！)?@?\s*(最终书名|最终简介|三个备选书名|三个备选简介|目录|导言|文前|正文)\s*$"
)
_DOCX_NUMBERED_LINE_RE = re.compile(r"^((?:！！！)?@?\s*)?\d+[.．、]\s*(.+?)\s*$")


def _docx_category(markdown_category: str | None) -> str:
    try:
        return normalize_category(str(markdown_category or ""))
    except ValueError:
        return ""


def _normalize_download_markdown(markdown: str, *, category: str | None = None) -> str:
    """Normalize exports while allowing independent task categories.

    Public-domain fusion and other standalone workflows share the bookshelf
    downloader but are intentionally not prompt-registry categories.  They use
    the generic delivery normalizer instead of failing the download.
    """
    normalized_category = _docx_category(category)
    if normalized_category == "general_longform":
        return markdown
    return normalize_final_markdown(markdown, category=normalized_category or None)


def _classical_chinese_docx_lines(markdown: str, *, category: str = "classical_chinese") -> list[str]:
    lines: list[str] = []
    skipping_intro = False
    for raw_line in normalize_final_markdown(markdown, category=category).splitlines():
        stripped = raw_line.strip()
        section = _DOCX_FRONT_SECTION_RE.match(stripped)
        if section:
            section_name = section.group(1)
            if section_name == "导言":
                skipping_intro = True
                continue
            skipping_intro = False
            lines.append(raw_line)
            continue
        if skipping_intro:
            continue
        numbered = _DOCX_NUMBERED_LINE_RE.match(raw_line)
        if numbered:
            raw_line = (numbered.group(2) or "").strip()
            if not raw_line:
                continue
        lines.append(raw_line)
    return lines


def _markdown_to_docx(
    markdown: str,
    *,
    image_base_dir: Path | None = None,
    category: str | None = None,
    comic_preview_images: bool = False,
) -> bytes:
    """Build a minimal .docx, embedding comic images when markdown references them."""
    paragraphs: list[str] = []
    image_entries: list[tuple[str, Path, str]] = []
    normalized_category = _docx_category(category)
    if (
        normalized_category in CLASSICAL_CHINESE_CATEGORY_IDS
        and normalized_category != BUDDHIST_SCRIPTURE_STORY_CATEGORY
    ):
        docx_lines = _classical_chinese_docx_lines(markdown, category=normalized_category)
    else:
        docx_lines = _normalize_download_markdown(markdown, category=category).splitlines()
    for line in docx_lines:
        image_filename = image_filename_from_markdown(line)
        image_path = None
        if image_filename and image_base_dir is not None:
            candidate = image_base_dir / "comic_images" / image_filename
            if candidate.exists():
                image_path = candidate
            if comic_preview_images and is_healing_comic_family(normalized_category):
                preview_path = _comic_image_preview_path_from_output_dir(image_base_dir, image_filename)
                if preview_path is not None:
                    image_path = preview_path
        if image_filename and image_path is not None:
            rel_id = f"rIdImage{len(image_entries) + 1}"
            alt_match = re.match(r"\s*!\[([^\]]*)\]", line)
            alt = alt_match.group(1).strip() if alt_match else image_filename
            cx, cy = _docx_image_size_emu(image_path)
            image_ext = _docx_media_extension(image_path)
            image_entries.append((image_filename, image_path, image_ext))
            paragraphs.append(
                _docx_image_paragraph(
                    rel_id=rel_id,
                    filename=image_filename,
                    alt=alt,
                    doc_pr_id=len(image_entries),
                    cx=cx,
                    cy=cy,
                )
            )
            continue
        paragraphs.append(
            _docx_text_paragraph(
                line,
                allow_markdown_bold=normalized_category in CLASSICAL_CHINESE_CATEGORY_IDS,
            )
        )

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        "<w:body>"
        + "".join(paragraphs)
        + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
        'w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
        "</w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="png" ContentType="image/png"/>'
        '<Default Extension="jpg" ContentType="image/jpeg"/>'
        '<Default Extension="jpeg" ContentType="image/jpeg"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    document_rels = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
    ]
    for idx, (filename, _, image_ext) in enumerate(image_entries, start=1):
        document_rels.append(
            f'<Relationship Id="rIdImage{idx}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            f'Target="media/image{idx}.{image_ext}"/>'
        )
    document_rels.append("</Relationships>")
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as docx:
        docx.writestr("[Content_Types].xml", content_types)
        docx.writestr("_rels/.rels", rels)
        docx.writestr("word/document.xml", document_xml)
        if image_entries:
            docx.writestr("word/_rels/document.xml.rels", "".join(document_rels))
        for idx, (_, path, image_ext) in enumerate(image_entries, start=1):
            docx.writestr(f"word/media/image{idx}.{image_ext}", path.read_bytes())
    return buffer.getvalue()


@router.get(
    "",
    response_model=TaskListResponse,
    summary="任务列表 (分页, 按创建时间倒序)",
)
def list_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=0),
    status_filter: str | None = Query(None, alias="status"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TaskListResponse:
    chapter_stats = (
        db.query(
            Chapter.task_id.label("task_id"),
            func.count(Chapter.id).label("chapter_count"),
            func.coalesce(func.sum(Chapter.word_count), 0).label("chapter_words"),
        )
        .group_by(Chapter.task_id)
        .subquery()
    )
    first_chapter = (
        db.query(
            Chapter.task_id.label("task_id"),
            Chapter.title.label("first_chapter_title"),
            func.row_number()
            .over(partition_by=Chapter.task_id, order_by=Chapter.chapter_num.asc())
            .label("rn"),
        )
        .subquery()
    )

    filters = [Task.owner_id == user.id]
    if status_filter:
        filters.append(Task.status == status_filter)

    total = db.query(func.count(Task.id)).filter(*filters).scalar() or 0
    query = (
        db.query(
            Task.id.label("id"),
            Task.source_url.label("source_url"),
            Task.status.label("status"),
            Task.progress.label("progress"),
            Task.current_step.label("current_step"),
            Task.cover_url.label("cover_url"),
            Task.created_at.label("created_at"),
            _task_list_config_expr(),
            func.coalesce(chapter_stats.c.chapter_count, 0).label("chapter_count"),
            func.coalesce(chapter_stats.c.chapter_words, 0).label("chapter_words"),
            first_chapter.c.first_chapter_title.label("first_chapter_title"),
        )
        .outerjoin(chapter_stats, chapter_stats.c.task_id == Task.id)
        .outerjoin(first_chapter, and_(first_chapter.c.task_id == Task.id, first_chapter.c.rn == 1))
        .filter(*filters)
        .order_by(Task.created_at.desc())
    )
    if page_size > 0:
        query = query.offset((page - 1) * page_size).limit(page_size)
    rows = query.all()
    items = [_build_list_item_from_row(row) for row in rows]
    return TaskListResponse(total=int(total), page=page, page_size=page_size, items=items)


@router.get(
    "/{task_id}",
    response_model=TaskStatusResponse,
    summary="查询任务状态 / 进度 / 封面",
)
def get_task(
    task_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TaskStatusResponse:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return TaskStatusResponse.model_validate(task)


@router.patch(
    "/{task_id}/metadata",
    response_model=TaskStatusResponse,
    summary="更新书稿书名和书架",
)
def update_task_metadata(
    task_id: UUID,
    body: TaskMetadataUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TaskStatusResponse:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    cfg = dict(task.config or {})
    fields = body.model_fields_set
    if (task.category == "general_longform" and fields & {"title", "category"}) or ("category" in fields and str(body.category or "").strip() in {"general_longform", "通用长篇"}):
        raise HTTPException(status_code=409, detail="通用长篇的分类与书名绑定确认大纲，请在专属工作区管理；书架可单独调整")
    if "title" in fields:
        title = (body.title or "").strip()
        if title:
            cfg["title"] = title
            if cfg.get("markdown"):
                cfg["markdown"] = _replace_title_in_markdown(str(cfg["markdown"]), title)
                cfg["markdown_chars"] = len(str(cfg["markdown"]))
        else:
            cfg.pop("title", None)
    if "bookshelf" in fields:
        bookshelf = (body.bookshelf or "").strip()
        if bookshelf:
            cfg["bookshelf"] = bookshelf
        else:
            cfg.pop("bookshelf", None)
    if "category" in fields:
        raw_category = (body.category or "").strip()
        if raw_category:
            try:
                category = normalize_category(raw_category)
                category_meta = get_category_meta(category)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            cfg["category"] = category
            cfg["category_label"] = category_meta["label"]
            cfg["category_prompt_status"] = category_meta["prompt_status"]
            cfg["bookshelf"] = category_meta["label"]
        else:
            cfg.pop("category", None)
            cfg.pop("category_label", None)
            cfg.pop("category_prompt_status", None)
            cfg.pop("bookshelf", None)

    task.config = cfg
    db.commit()
    db.refresh(task)
    return TaskStatusResponse.model_validate(task)


@router.post(
    "/{task_id}/regenerate-selection",
    response_model=RegenerateSelectionResponse,
    summary="重新生成编辑器中选中的内容",
)
async def regenerate_selection(
    task_id: UUID,
    body: RegenerateSelectionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RegenerateSelectionResponse:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    if task.category == "general_longform":
        raise HTTPException(status_code=409, detail="请使用通用长篇工作区的指定章修订，保留来源与重新验收")

    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="选中文本不能为空")

    try:
        regenerated = await asyncio.to_thread(regenerate_selected_text, text)
    except Exception as exc:
        logger.exception("[task %s] 选区重新生成失败", task_id)
        raise HTTPException(status_code=502, detail=f"重新生成失败: {exc}") from exc

    regenerated = (regenerated or "").strip()
    if not regenerated:
        raise HTTPException(status_code=502, detail="模型返回为空")
    return RegenerateSelectionResponse(
        original=text,
        regenerated=regenerated,
        original_chars=len(text),
        regenerated_chars=len(regenerated),
    )


@router.get(
    "/{task_id}/cover",
    summary="返回任务封面图",
)
def get_cover(
    task_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FileResponse:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    cover_path = _task_cover_path(task)
    if cover_path is None:
        raise HTTPException(status_code=404, detail=f"cover of task {task_id} not found")
    return FileResponse(
        cover_path,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Vary": "Authorization",
        },
    )


@router.get(
    "/{task_id}/cover/download",
    summary="下载任务封面图 (png)",
)
def download_cover(
    task_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FileResponse:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    cover_path = _task_cover_path(task)
    if cover_path is None:
        raise HTTPException(status_code=404, detail=f"cover of task {task_id} not found")
    return FileResponse(
        cover_path,
        media_type="image/png",
        filename=_safe_download_name(task, "png"),
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Vary": "Authorization",
        },
    )


@router.get(
    "/{task_id}/cover/download/direct",
    summary="下载任务封面图 (png, browser-native)",
)
def download_cover_direct(
    task_id: UUID,
    token: str | None = Query(None),
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
) -> FileResponse:
    user = _get_current_user_from_download_token(
        db,
        authorization=authorization,
        token=token,
    )
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    cover_path = _task_cover_path(task)
    if cover_path is None:
        raise HTTPException(status_code=404, detail=f"cover of task {task_id} not found")
    return FileResponse(
        cover_path,
        media_type="image/png",
        filename=_safe_download_name(task, "png"),
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
        },
    )


@router.get(
    "/{task_id}/images/{filename}/preview",
    summary="返回漫画系列章节配图预览图",
)
def get_task_image_preview(
    task_id: UUID,
    filename: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FileResponse:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    category = str((task.config or {}).get("category") or "")
    if not is_healing_comic_family(category):
        raise HTTPException(status_code=404, detail=f"image preview of task {task_id} not found")
    image_path = _task_comic_image_preview_path(task_id, filename)
    if image_path is None:
        raise HTTPException(status_code=404, detail=f"image preview {filename} of task {task_id} not found")
    return FileResponse(
        image_path,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "private, max-age=3600",
            "Vary": "Authorization",
        },
    )


@router.get(
    "/{task_id}/images/{filename}/preview/download",
    summary="下载漫画系列章节配图预览图",
)
def download_task_image_preview(
    task_id: UUID,
    filename: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FileResponse:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    category = str((task.config or {}).get("category") or "")
    if not is_healing_comic_family(category):
        raise HTTPException(status_code=404, detail=f"image preview of task {task_id} not found")
    return _build_task_image_download_response(task_id, filename, preview=True)


@router.get(
    "/{task_id}/images/{filename}/preview/download/direct",
    summary="下载漫画系列章节配图预览图 (browser-native)",
)
def download_task_image_preview_direct(
    task_id: UUID,
    filename: str,
    token: str | None = Query(None),
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
) -> FileResponse:
    user = _get_current_user_from_download_token(
        db,
        authorization=authorization,
        token=token,
    )
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    category = str((task.config or {}).get("category") or "")
    if not is_healing_comic_family(category):
        raise HTTPException(status_code=404, detail=f"image preview of task {task_id} not found")
    return _build_task_image_download_response(task_id, filename, preview=True)


@router.get(
    "/{task_id}/images/{filename}/download",
    summary="下载漫画系列章节配图原图",
)
def download_task_image(
    task_id: UUID,
    filename: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FileResponse:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return _build_task_image_download_response(task_id, filename, preview=False)


@router.get(
    "/{task_id}/images/{filename}/download/direct",
    summary="下载漫画系列章节配图原图 (browser-native)",
)
def download_task_image_direct(
    task_id: UUID,
    filename: str,
    token: str | None = Query(None),
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
) -> FileResponse:
    user = _get_current_user_from_download_token(
        db,
        authorization=authorization,
        token=token,
    )
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return _build_task_image_download_response(task_id, filename, preview=False)


@router.get(
    "/{task_id}/images/{filename}",
    summary="返回漫画系列章节配图",
)
def get_task_image(
    task_id: UUID,
    filename: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FileResponse:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    image_path = _task_comic_image_path(task_id, filename)
    if image_path is None:
        raise HTTPException(status_code=404, detail=f"image {filename} of task {task_id} not found")
    return FileResponse(
        image_path,
        media_type="image/png",
        headers={
            "Cache-Control": "private, max-age=3600",
            "Vary": "Authorization",
        },
    )


@router.post(
    "/{task_id}/cover/regenerate",
    response_model=TaskStatusResponse,
    summary="重新生成三段式出版封面",
)
def regenerate_cover(
    task_id: UUID,
    req: RegenerateCoverRequest | None = Body(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TaskStatusResponse:
    task = (
        db.query(Task)
        .filter(Task.id == task_id, Task.owner_id == user.id)
        .with_for_update()
        .one_or_none()
    )
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    markdown = _read_task_markdown(db, task)
    if not markdown:
        raise HTTPException(status_code=404, detail="任务尚未产出可用书稿，无法生成封面")

    cfg = dict(task.config or {})
    if not settings.COVER_WORKFLOW_ENABLED:
        cfg.update(
            cover_disabled=True,
            cover_isolated=True,
            cover_isolation_reason="COVER_WORKFLOW_ENABLED=false",
            cover_error=None,
        )
        task.config = cfg
        task.cover_url = None
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="封面 workflow 已暂时隔离：COVER_WORKFLOW_ENABLED=false",
        )

    if not settings.COVER_BOOK_TASK_GENERATION_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="封面生成已迁移到书架下的 Excel 独立入口",
        )

    if cfg.get("cover_status") in {"queued", "generating"}:
        return TaskStatusResponse.model_validate(task)

    reference_mode = req.reference_mode if req is not None else "refresh"
    enqueue_cover_generation(db, task, force=True, reference_mode=reference_mode)
    db.refresh(task)
    return TaskStatusResponse.model_validate(task)


@router.get(
    "/{task_id}/chapters",
    response_model=ChaptersListResponse,
    summary="返回该任务下所有已生成的章节",
)
def get_chapters(
    task_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChaptersListResponse:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    chapters = (
        db.query(Chapter)
        .filter(Chapter.task_id == task_id)
        .order_by(Chapter.chapter_num.asc())
        .all()
    )
    return ChaptersListResponse(
        task_id=task_id,
        total=len(chapters),
        chapters=[ChapterResponse.model_validate(c) for c in chapters],
    )


@router.put(
    "/{task_id}/chapters/{chapter_num}",
    response_model=ChapterResponse,
    summary="更新指定章节内容（编辑器保存）",
)
def update_chapter(
    task_id: UUID,
    chapter_num: int,
    body: ChapterUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChapterResponse:
    chapter = (
        db.query(Chapter)
        .filter(Chapter.task_id == task_id, Chapter.chapter_num == chapter_num)
        .first()
    )
    if chapter is None or chapter.task.owner_id != user.id:
        raise HTTPException(
            status_code=404,
            detail=f"chapter {chapter_num} of task {task_id} not found",
        )
    if chapter.task.category == "general_longform":
        raise HTTPException(status_code=409, detail="通用长篇正文需经计划实绩对比与验收，请从专属工作区修订")
    if body.title is not None and body.title.strip():
        chapter.title = body.title.strip()
    chapter.content = body.content
    chapter.word_count = len(body.content)
    _sync_markdown_from_chapters(db, chapter.task)
    db.commit()
    db.refresh(chapter)
    return ChapterResponse.model_validate(chapter)


@router.get(
    "/{task_id}/markdown",
    response_model=MarkdownResponse,
    summary="返回该任务的最终全文 Markdown",
)
def get_markdown(
    task_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> MarkdownResponse:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    md = _read_task_markdown(db, task)
    if not md:
        raise HTTPException(status_code=404, detail="任务尚未产出任何内容")

    return MarkdownResponse(task_id=task_id, markdown=md, char_count=len(md))


def _general_longform_book_file(task: Task) -> tuple[Path, str]:
    from hashlib import sha256
    from app.services.general_longform import load_state, work_dir
    directory = work_dir(task.id)
    state = load_state(directory) or {}
    artifact = next((item for item in reversed(state.get("artifacts", [])) if item.get("kind") == "book"), None)
    if state.get("phase") != "completed" or not artifact:
        raise HTTPException(status_code=409, detail="通用长篇尚无通过全书验收的 Word")
    path = (directory / "exports" / artifact["filename"]).resolve()
    if not path.is_relative_to((directory / "exports").resolve()) or not path.is_file() or sha256(path.read_bytes()).hexdigest() != artifact["sha256"]:
        raise HTTPException(status_code=409, detail="通用长篇全书 Word 完整性校验失败")
    return path, artifact["filename"]


def _build_batch_download_response(
    task_ids: list[UUID],
    *,
    fmt: str,
    db: Session,
    user: User,
) -> Response:
    task_ids = list(dict.fromkeys(task_ids))
    rows = (
        db.query(Task)
        .filter(Task.owner_id == user.id, Task.id.in_(task_ids))
        .all()
    )
    task_map = {task.id: task for task in rows}
    buffer = BytesIO()
    used_names: set[str] = set()
    written = 0

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for task_id in task_ids:
            task = task_map.get(task_id)
            if task is None or task.status != TaskStatus.COMPLETED:
                continue
            md = _read_task_markdown(db, task)
            if not md:
                continue
            category = str((task.config or {}).get("category") or "")
            if fmt == "docx" and category == "general_longform":
                path, filename = _general_longform_book_file(task)
                content = path.read_bytes()
            elif fmt == "docx":
                content = _markdown_to_docx(
                    md,
                    image_base_dir=_task_output_dir(task.id),
                    category=category,
                )
                filename = _safe_download_name(task, "docx")
            elif fmt == "txt":
                content = _normalize_download_markdown(md, category=category)
                filename = _safe_download_name(task, "txt")
            else:
                content = _normalize_download_markdown(md, category=category)
                filename = _safe_download_name(task, "md")
            archive.writestr(_unique_zip_name(filename, used_names), content)
            written += 1

    if written == 0:
        raise HTTPException(status_code=404, detail="所选书稿尚未产出可下载内容")

    today = datetime.now().strftime("%Y%m%d")
    filename = f"books_{today}_{written}_{fmt}.zip"
    encoded_filename = quote(filename)
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{encoded_filename}"; '
                f"filename*=UTF-8''{encoded_filename}"
            )
        },
    )


@router.post(
    "/download-batch",
    summary="批量打包下载最终稿 (zip)",
)
def download_batch(
    req: BatchDownloadRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    return _build_batch_download_response(req.task_ids, fmt=req.fmt, db=db, user=user)


def _get_current_user_from_download_token(
    db: Session,
    *,
    authorization: str | None,
    token: str | None,
) -> User:
    raw_token = ""
    if authorization and authorization.lower().startswith("bearer "):
        raw_token = authorization.split(" ", 1)[1].strip()
    elif token:
        raw_token = token.strip()
    if not raw_token:
        raise HTTPException(status_code=401, detail="缺少或非法的 Authorization 头")
    try:
        payload = decode_token(raw_token)
    except JWTDecodeError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="token 缺少 sub")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


@router.get(
    "/download-batch/direct",
    summary="批量打包下载最终稿 (zip, browser-native)",
)
def download_batch_direct(
    task_ids: str = Query(..., min_length=1),
    fmt: str = Query("docx", pattern="^(md|docx|txt)$"),
    token: str | None = Query(None),
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
) -> Response:
    user = _get_current_user_from_download_token(
        db,
        authorization=authorization,
        token=token,
    )
    try:
        ids = [UUID(item.strip()) for item in task_ids.split(",") if item.strip()]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="task_ids 参数格式不正确") from exc
    if not ids:
        raise HTTPException(status_code=400, detail="请选择要下载的书稿")
    if len(ids) > 500:
        raise HTTPException(status_code=400, detail="一次最多下载 500 本书稿")
    return _build_batch_download_response(ids, fmt=fmt, db=db, user=user)


def _build_similarity_report_response(task: Task) -> Response:
    """Serve the newest valid Word similarity report for a task."""
    from urllib.parse import quote

    report_dir = _task_output_dir(task.id) / "04_文本相似度审查报告"
    if not report_dir.is_dir():
        raise HTTPException(
            status_code=404,
            detail="该任务未生成文本相似度审查报告（0.5版或通用短篇需要完整原稿）",
        )
    reports = sorted(report_dir.glob("*.docx"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not reports:
        raise HTTPException(status_code=404, detail="审查报告目录中没有 .docx 文件")
    path = reports[0]
    return FileResponse(
        path,
        filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(path.name)}",
        },
    )


def _build_single_download_response(
    db: Session,
    task: Task,
    fmt: str,
    *,
    comic_preview_images: bool = False,
) -> Response:
    if task.status != TaskStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail="任务尚未通过全部终稿闸门，人工复核原稿不可作为终稿下载",
        )
    md = _read_task_markdown(db, task)
    if not md:
        raise HTTPException(status_code=404, detail="任务尚未产出任何内容")

    category = str((task.config or {}).get("category") or "")
    if ((task.config or {}).get("ai_or_finalizer_status") == "completed" and fmt == "docx"
            and category != "comic_series" and not is_healing_comic_family(category)):
        directory = _task_output_dir(task.id) / "ai_or_finalizer"
        if (task.config or {}).get("editor_v05_status") == "completed":
            directory = _task_output_dir(task.id) / "editor_v05/ai_or_finalizer"
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        filename = manifest["artifacts"]["docx"]
        return FileResponse(directory / filename, filename=filename,
                            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            headers={"Cache-Control": "no-store"})
    if category == "general_longform" and fmt == "docx":
        path, filename = _general_longform_book_file(task)
        return FileResponse(path, filename=filename, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", headers={"Cache-Control": "no-store"})
    use_preview_docx = bool(comic_preview_images and is_healing_comic_family(category) and fmt == "docx")
    output_dir = _task_output_dir(task.id)
    download_dir = output_dir / "downloads"
    download_dir.mkdir(parents=True, exist_ok=True)
    if fmt == "docx":
        content = _markdown_to_docx(
            md,
            image_base_dir=output_dir,
            category=category,
            comic_preview_images=use_preview_docx,
        )
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        filename = _safe_download_name(task, "docx", suffix="预览") if use_preview_docx else _safe_download_name(task, "docx")
        file_path = download_dir / ("final_preview.docx" if use_preview_docx else "final.docx")
        file_path.write_bytes(content)
    elif fmt == "txt":
        content = _normalize_download_markdown(md, category=category)
        media_type = "text/plain; charset=utf-8"
        filename = _safe_download_name(task, "txt")
        file_path = download_dir / "final.txt"
        # UTF-8 BOM keeps Chinese text readable in Windows Notepad and older
        # publishing tools while remaining valid UTF-8 everywhere else.
        file_path.write_text(content, encoding="utf-8-sig")
    else:
        content = _normalize_download_markdown(md, category=category)
        media_type = "text/markdown; charset=utf-8"
        filename = _safe_download_name(task, "md")
        file_path = download_dir / "final.md"
        file_path.write_text(content, encoding="utf-8")
    return FileResponse(
        file_path,
        media_type=media_type,
        filename=filename,
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
        },
    )


@router.get(
    "/{task_id}/download",
    summary="下载最终稿 (md/docx/txt)",
)
def download_markdown(
    task_id: UUID,
    fmt: str = Query("md", pattern="^(md|docx|txt)$"),
    preview: bool = Query(False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return _build_single_download_response(db, task, fmt, comic_preview_images=preview)


@router.get("/{task_id}/ai-or-delivery", summary="下载降AI与双稿纠错交付包")
def download_ai_or_delivery(task_id: UUID, db: Session = Depends(get_db),
                           user: User = Depends(get_current_user)):
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status != TaskStatus.COMPLETED or (task.config or {}).get("ai_or_finalizer_status") != "completed":
        raise HTTPException(status_code=409, detail="降AI纠错交付尚未完成")
    directory = _task_output_dir(task.id) / "ai_or_finalizer"
    if (task.config or {}).get("editor_v05_status") == "completed":
        directory = _task_output_dir(task.id) / "editor_v05/ai_or_finalizer"
    path = directory / "delivery.zip"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="交付文件不存在")
    return FileResponse(path, filename=f"降AI纠错_{task.id}.zip", media_type="application/zip")


@router.get("/{task_id}/corrections.xlsx", summary="请求头鉴权下载纠错Excel")
def download_corrections_excel(task_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    root = _task_output_dir(task.id)
    directories = (root / "editor_v05/ai_or_finalizer", root / "ai_or_finalizer")
    for directory in directories:
        manifest_path = directory / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
            filename = manifest.get("artifacts", {}).get("xlsx")
            if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith(".xlsx"):
                continue
            path = (directory / filename).resolve()
            if not path.is_relative_to(directory.resolve()) or not path.is_file():
                continue
        except (OSError, ValueError, TypeError):
            continue
        return FileResponse(path, filename=filename,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    raise HTTPException(status_code=404, detail="该任务尚无可下载的纠错Excel")


@router.get("/{task_id}/length-repair", summary="核验并读取局部篇幅待批准稿")
def get_length_repair(task_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.services.length_repair_approval import candidate_summary

    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    return candidate_summary(task, _task_output_dir(task.id))


@router.post("/{task_id}/length-repair", response_model=CreateTaskResponse, summary="人工核验hash批准局部稿并恢复")
def approve_length_repair(task_id: UUID, body: LengthRepairApprovalRequest,
                         db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.services.general_short_length import LengthHold
    from app.services.general_longform_runtime import atomic_json
    from app.services.length_repair_approval import verified_candidate

    with db.no_autoflush:
        task = db.execute(select(Task).where(Task.id == task_id, Task.owner_id == user.id)
                          .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    cfg = dict(task.config or {})
    if (cfg.get("approved_length_repair_sha256") == body.result_sha256
            and task.status in {TaskStatus.PENDING, TaskStatus.PROCESSING}):
        return CreateTaskResponse(task_id=task.id, status=task.status)
    if task.status not in {TaskStatus.MANUAL_REVIEW, TaskStatus.FAILED, TaskStatus.PAUSED} or _has_active_run(task):
        raise HTTPException(status_code=409, detail="任务仍在执行或已完成，不能批准旧稿")
    reason = similarity_retry_reason(cfg)
    if reason:
        raise HTTPException(status_code=409, detail=reason)
    if cfg.get("paid_call_delivery_uncertain") or cfg.get("paid_call_manual_resume_required"):
        raise HTTPException(status_code=409, detail="付费结果尚未核对，不能恢复")
    try:
        record = verified_candidate(task, _task_output_dir(task.id))
    except (OSError, ValueError, KeyError, TypeError, LengthHold) as exc:
        raise HTTPException(status_code=409, detail="局部修复稿无效或hash变化，不能批准") from exc
    if record["result_sha256"] != body.result_sha256:
        raise HTTPException(status_code=409, detail="页面hash已过期，请重新查看修复稿")
    _ensure_generation_capacity(db)
    approval = {**record, "status": "approved", "human_approval": str(user.id),
                "approved_at": datetime.now(timezone.utc).isoformat()}
    atomic_json(_task_output_dir(task.id) / "approved_length_repair/approval.json", approval)
    task.config = {**cfg, "approved_length_repair_sha256": body.result_sha256}
    _prepare_task_for_retry(task)
    task.current_step = "人工批准局部篇幅稿，按原累计预算从断点继续"
    db.commit()
    db.refresh(task)
    try:
        _dispatch_task(task)
    except Exception as exc:
        _mark_dispatch_failure_pending(task, exc)
        db.commit()
        raise HTTPException(status_code=503, detail="批准已保存，任务投递暂时失败；请勿重复生成压缩稿") from exc
    return CreateTaskResponse(task_id=task.id, status=task.status)


@router.get(
    "/{task_id}/download/direct",
    summary="下载最终稿 (md/docx/txt, browser-native)",
)
def download_markdown_direct(
    task_id: UUID,
    fmt: str = Query("md", pattern="^(md|docx|txt)$"),
    preview: bool = Query(False),
    token: str | None = Query(None),
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
) -> Response:
    user = _get_current_user_from_download_token(
        db,
        authorization=authorization,
        token=token,
    )
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return _build_single_download_response(db, task, fmt, comic_preview_images=preview)


@router.get(
    "/{task_id}/similarity-report",
    summary="下载文本相似度审查报告 Word（通用短篇）",
)
def download_similarity_report(
    task_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    """Return the v09 section-ten Word report for this task, if one exists."""
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return _build_similarity_report_response(task)


@router.get(
    "/{task_id}/similarity-report/direct",
    summary="下载文本相似度审查报告 Word（浏览器直下）",
)
def download_similarity_report_direct(
    task_id: UUID,
    token: str | None = Query(None),
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
) -> Response:
    user = _get_current_user_from_download_token(
        db, authorization=authorization, token=token
    )
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return _build_similarity_report_response(task)


@router.get(
    "/{task_id}/similarity-report/data",
    summary="读取文本相似度审查数据 JSON（通用短篇）",
)
def get_similarity_report_data(
    task_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    path = _task_output_dir(task.id) / "05_differentiation_report.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="该任务未生成相似度审查数据")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"审查数据无法解析：{exc}") from exc


@router.get(
    "/{task_id}/stages/{stage}",
    response_model=StageContentResponse,
    summary="返回某一中间产物 (writer / polish / refine / format)",
)
def get_stage(
    task_id: UUID,
    stage: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StageContentResponse:
    if stage not in _STAGE_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"未知 stage: {stage}，合法值: {list(_STAGE_FILES)}",
        )
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    file_path: Path = PROJECT_ROOT / "backend" / "output" / f"task_{task_id}" / _STAGE_FILES[stage]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"stage {stage} 文件未生成")

    content = file_path.read_text(encoding="utf-8")
    return StageContentResponse(
        task_id=task_id,
        stage=stage,
        content=content,
        char_count=len(content),
    )
