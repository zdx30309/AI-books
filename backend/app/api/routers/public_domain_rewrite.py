"""Public-domain fusion endpoint.

This is the deliberately simple bookshelf flow used before the experimental
long-form verifier was introduced: one planning call, one writing call, then
an immediate bookshelf entry.  It is isolated from the ordinary article,
classical-text, and Epic Novel routes.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.routers.auth import get_current_user
from app.core.config import PROJECT_ROOT
from app.db.session import get_db
from app.models import Chapter, Task, TaskStatus, User
from app.schemas.task import CreateTaskResponse
from app.services.image_ocr import (
    ImageOcrError,
    is_supported_ocr_image_suffix,
    ocr_image_bytes,
)
from app.services.public_domain_longform import (
    CLASSICAL_VERNACULAR_MODE,
    FULL_MODE,
    PublicDomainValidationError,
)
from app.services.public_domain_classical_vernacular import (
    POLICY_VERSION as CLASSICAL_VERNACULAR_POLICY_VERSION,
)
from app.services.public_domain_classical_vernacular import (
    MAX_PREVIEW_CHARS,
    MIN_PREVIEW_CHARS,
    PROMPT_VERSION as CLASSICAL_VERNACULAR_PROMPT_VERSION,
    SEGMENT_CHARS as CLASSICAL_SEGMENT_CHARS,
    extract_source_sections as extract_classical_source_sections,
    split_source_segments,
)
from app.services.public_domain_longform import (
    POLICY_VERSION as LONGFORM_POLICY_VERSION,
)
from app.services.public_domain_longform import (
    PublicDomainSource as LongformSource,
)
from app.services.public_domain_longform import (
    build_source_reports as build_longform_source_reports,
)
from app.services.public_domain_longform import (
    derive_dimensions as derive_longform_dimensions,
)
from app.services.public_domain_longform import (
    source_to_dict as longform_source_to_dict,
)
from app.services.public_domain_longform import source_from_dict as longform_source_from_dict
from app.services.public_domain_longform import (
    validate_sources as validate_longform_sources,
)
from app.services.public_domain_rewrite import (
    MAX_SOURCES,
    PublicDomainRewriteError,
    PublicDomainSource,
    rewrite_public_domain_fusion,
)
from app.utils.task_dispatch_guard import acquire_task_dispatch_guard, clear_task_dispatch_guard
from app.utils.task_status import public_task_error, public_task_step
from app.utils.docx_text import DocxTextError, extract_docx_text, extract_pdf_text
from app.utils.epub_text import EpubTextError, extract_epub_text

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/public-domain-rewrite", tags=["Public Domain Rewrite"])

PUBLIC_DOMAIN_BOOKSHELF = "公版融合长篇"
PUBLIC_DOMAIN_CLASSICAL_BOOKSHELF = "公版古文白话"
PUBLIC_DOMAIN_CLASSICAL_PREVIEW_BOOKSHELF = "公版古文白话试译"


def _classical_bookshelf_for_scope(scope_mode: str) -> str:
    return (
        PUBLIC_DOMAIN_CLASSICAL_BOOKSHELF
        if str(scope_mode or "complete") == "complete"
        else PUBLIC_DOMAIN_CLASSICAL_PREVIEW_BOOKSHELF
    )


class PublicDomainSourcePayload(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    text: str = Field(..., min_length=1)
    author: str | None = Field(default=None, max_length=120)
    publication_year: int | None = Field(default=None, ge=0, le=2100)
    # Accepted for compatibility with the previous UI.  Rights evidence is
    # recorded, but this lightweight flow intentionally does not run a remote
    # rights verifier.
    author_death_year: int | None = Field(default=None, ge=0, le=2100)
    rights_note: str = Field(default="用户确认公版/公共领域", max_length=1000)
    rights_evidence_url: str | None = Field(default=None, max_length=2048)


class ClassicalVernacularProfilePayload(BaseModel):
    custom_prompt: str = Field(default="", max_length=30_000)
    scope_mode: str = Field(default="complete", pattern="^(complete|characters|chapters)$")
    preview_target_chars: int = Field(default=0, ge=0, le=MAX_PREVIEW_CHARS)
    preview_chapters: int = Field(default=0, ge=0, le=5_000)


class ClassicalTranslationSettingsResponse(BaseModel):
    default_prompt: str
    min_preview_chars: int
    max_preview_chars: int
    max_chapters: int


class ClassicalSourceSectionItem(BaseModel):
    number: int
    title: str
    source_title: str
    char_count: int


class ClassicalSourceSegmentItem(BaseModel):
    number: int
    char_count: int
    preview: str


class ClassicalSourceResponse(BaseModel):
    task_id: UUID
    total: int
    selected_number: int
    selected_title: str
    selected_source_title: str
    selected_text: str
    selected_char_count: int
    offset: int
    next_offset: int | None
    has_more: bool
    sections: list[ClassicalSourceSectionItem]
    segments: list[ClassicalSourceSegmentItem]


class ClassicalWorkspaceSourceItem(BaseModel):
    title: str
    author: str | None = None
    rights_note: str | None = None
    char_count: int
    detected_chapters: int
    sha256: str


class ClassicalWorkspaceStateResponse(BaseModel):
    task_id: UUID
    status: str
    progress: int
    current_step: str
    error_message: str | None = None
    target_title: str
    bookshelf: str
    custom_prompt: str
    scope_mode: str
    preview_target_chars: int
    preview_chapters: int
    public_domain_confirmed: bool
    is_preview: bool
    generation_complete: bool
    source_count: int
    sources: list[ClassicalWorkspaceSourceItem]
    available_chapters: int
    completed_chapters: int
    translated_chars: int
    checkpoint_chapters: int
    checkpoint_chars: int
    translated_segments: int
    policy_version: str
    prompt_version: str
    prompt_hash: str | None = None
    can_resume: bool
    can_continue_complete: bool


class ClassicalContinueRequest(BaseModel):
    scope_mode: str = Field(default="complete", pattern="^(complete|characters|chapters)$")
    preview_target_chars: int = Field(default=0, ge=0, le=MAX_PREVIEW_CHARS)
    preview_chapters: int = Field(default=0, ge=0, le=5_000)
    custom_prompt: str | None = Field(default=None, min_length=1, max_length=30_000)


class ClassicalLocalRetranslateRequest(BaseModel):
    chapter_num: int = Field(..., ge=1)
    segment_num: int | None = Field(default=None, ge=1)
    custom_prompt: str = Field(..., min_length=1, max_length=30_000)


class PublicDomainRewriteRequest(BaseModel):
    sources: list[PublicDomainSourcePayload] = Field(
        ..., min_length=1, max_length=MAX_SOURCES
    )
    target_title: str | None = Field(default=None, max_length=200)
    target_direction: str | None = Field(default=None, max_length=3000)
    target_length: int | None = Field(default=None, ge=800, le=220_000)
    target_chapters: int | None = Field(default=None, ge=1, le=200)
    output_style: str | None = Field(default=None, max_length=1000)
    public_domain_confirmed: bool = False
    # ``full`` is the resumable serial long-book flow.  ``legacy_simple`` is
    # retained only for old short-preview clients.
    generation_mode: str = FULL_MODE
    research_use_confirmed: bool = False
    adaptation_profile: ClassicalVernacularProfilePayload | None = None


@router.get(
    "/classical-translation/settings",
    response_model=ClassicalTranslationSettingsResponse,
    summary="获取古文白话试译范围（无内置 Prompt）",
)
async def get_classical_translation_settings(
    _user: User = Depends(get_current_user),
) -> ClassicalTranslationSettingsResponse:
    return ClassicalTranslationSettingsResponse(
        default_prompt="",
        min_preview_chars=MIN_PREVIEW_CHARS,
        max_preview_chars=MAX_PREVIEW_CHARS,
        max_chapters=5_000,
    )


@router.get(
    "/tasks/{task_id}/classical-source",
    response_model=ClassicalSourceResponse,
    summary="读取古文白话任务的原文章节",
)
async def get_classical_task_source(
    task_id: UUID,
    chapter_num: int = Query(default=1, ge=1),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50_000, ge=1_000, le=100_000),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClassicalSourceResponse:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    cfg = dict(task.config or {})
    if not bool(cfg.get("public_domain_classical_vernacular")):
        raise HTTPException(status_code=400, detail="该任务不是公版古文白话任务")
    snapshot = Path(str(cfg.get("source_snapshot") or ""))
    try:
        payload = json.loads(snapshot.read_text(encoding="utf-8"))
        sources = [
            longform_source_from_dict(item)
            for item in payload
            if isinstance(item, dict)
        ]
        sections = extract_classical_source_sections(sources)
    except (OSError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=409, detail=f"原文快照暂不可读：{exc}") from exc
    if not sections:
        raise HTTPException(status_code=409, detail="原文快照中没有可显示章节")
    selected_index = min(chapter_num, len(sections)) - 1
    selected = sections[selected_index]
    selected_slice = selected.text[offset : offset + limit]
    next_offset = offset + len(selected_slice)
    has_more = next_offset < len(selected.text)
    raw_profile = cfg.get("adaptation_profile") if isinstance(cfg.get("adaptation_profile"), dict) else {}
    segment_limit = int(raw_profile.get("_checkpoint_segment_limit") or CLASSICAL_SEGMENT_CHARS)
    chapter_meta_path = snapshot.parent / "classical_chapters" / f"{selected_index + 1:03d}.json"
    if chapter_meta_path.exists():
        try:
            chapter_meta = json.loads(chapter_meta_path.read_text(encoding="utf-8"))
            chapter_audit = dict((chapter_meta.get("chapter") or {}).get("audit") or {})
            segment_limit = int(chapter_audit.get("segment_limit") or segment_limit)
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    source_segments = split_source_segments(selected.text, segment_limit)
    return ClassicalSourceResponse(
        task_id=task.id,
        total=len(sections),
        selected_number=selected_index + 1,
        selected_title=selected.title,
        selected_source_title=selected.source_title,
        selected_text=selected_slice,
        selected_char_count=len(re.sub(r"\s+", "", selected.text)),
        offset=offset,
        next_offset=next_offset if has_more else None,
        has_more=has_more,
        sections=[
            ClassicalSourceSectionItem(
                number=index,
                title=item.title,
                source_title=item.source_title,
                char_count=len(re.sub(r"\s+", "", item.text)),
            )
            for index, item in enumerate(sections, start=1)
        ],
        segments=[
            ClassicalSourceSegmentItem(
                number=index,
                char_count=len(re.sub(r"\s+", "", item)),
                preview=re.sub(r"\s+", " ", item).strip()[:120],
            )
            for index, item in enumerate(source_segments, start=1)
        ],
    )


def _classical_workspace_state(task: Task) -> ClassicalWorkspaceStateResponse:
    """Rebuild the dedicated workbench state from the task's durable config."""

    cfg = dict(task.config or {})
    if not bool(cfg.get("public_domain_classical_vernacular")):
        raise ValueError("该任务不是公版古文白话任务")
    profile = (
        dict(cfg.get("adaptation_profile") or {})
        if isinstance(cfg.get("adaptation_profile"), dict)
        else {}
    )
    reports = cfg.get("source_reports") if isinstance(cfg.get("source_reports"), list) else []
    sources: list[ClassicalWorkspaceSourceItem] = []
    for item in reports:
        if not isinstance(item, dict):
            continue
        sources.append(
            ClassicalWorkspaceSourceItem(
                title=str(item.get("title") or "未命名原文"),
                author=str(item.get("author") or "").strip() or None,
                rights_note=str(item.get("rights_basis") or "").strip() or None,
                char_count=int(item.get("chars") or 0),
                detected_chapters=int(item.get("detected_chapters") or 0),
                sha256=str(item.get("sha256") or ""),
            )
        )
    chapters = sorted(list(task.chapters or []), key=lambda item: int(item.chapter_num))
    completed_chapters = len(chapters)
    translated_chars = sum(len(re.sub(r"\s+", "", str(item.content or ""))) for item in chapters)
    scope_mode = str(profile.get("scope_mode") or cfg.get("public_domain_classical_scope") or "complete")
    generation_complete = bool(cfg.get("generation_complete"))
    active = (
        task.status in {TaskStatus.PENDING, TaskStatus.PROCESSING, TaskStatus.WRITING_CHAPTERS}
        or bool(str(cfg.get("active_run_id") or "").strip())
    )
    full_complete = scope_mode == "complete" and generation_complete and task.status == TaskStatus.COMPLETED
    saved_prompt = str(profile.get("custom_prompt") or "").strip()
    return ClassicalWorkspaceStateResponse(
        task_id=task.id,
        status=task.status,
        progress=int(task.progress or 0),
        current_step=public_task_step(
            task.current_step,
            status=task.status,
            progress=task.progress,
        ),
        error_message=public_task_error(task.error_message, status=task.status),
        target_title=str(cfg.get("target_title") or cfg.get("title") or "公版古文白话"),
        bookshelf=str(cfg.get("bookshelf") or _classical_bookshelf_for_scope(scope_mode)),
        custom_prompt=saved_prompt,
        scope_mode=scope_mode,
        preview_target_chars=int(profile.get("preview_target_chars") or 0),
        preview_chapters=int(profile.get("preview_chapters") or 0),
        public_domain_confirmed=bool(cfg.get("public_domain_confirmed")),
        is_preview=scope_mode != "complete",
        generation_complete=generation_complete,
        source_count=int(cfg.get("source_count") or len(sources)),
        sources=sources,
        available_chapters=sum(int(item.detected_chapters or 0) for item in sources),
        completed_chapters=completed_chapters,
        translated_chars=translated_chars,
        checkpoint_chapters=max(completed_chapters, int(cfg.get("checkpoint_chapters") or 0)),
        checkpoint_chars=max(translated_chars, int(cfg.get("checkpoint_chars") or 0)),
        translated_segments=int(cfg.get("classical_translated_segments") or 0),
        policy_version=str(cfg.get("public_domain_policy_version") or CLASSICAL_VERNACULAR_POLICY_VERSION),
        prompt_version=str(cfg.get("classical_prompt_version") or CLASSICAL_VERNACULAR_PROMPT_VERSION),
        prompt_hash=str(cfg.get("classical_prompt_hash") or "").strip() or None,
        can_resume=(not active and not full_complete and bool(saved_prompt)),
        can_continue_complete=(not active and scope_mode != "complete"),
    )


def _prepare_classical_continuation(
    task: Task,
    *,
    scope_mode: str,
    preview_target_chars: int = 0,
    preview_chapters: int = 0,
    custom_prompt: str | None = None,
) -> None:
    """Resume or extend a classical task in place without discarding checkpoints."""

    cfg = dict(task.config or {})
    if not bool(cfg.get("public_domain_classical_vernacular")):
        raise ValueError("该任务不是公版古文白话任务")
    profile = (
        dict(cfg.get("adaptation_profile") or {})
        if isinstance(cfg.get("adaptation_profile"), dict)
        else {}
    )
    effective_prompt = str(custom_prompt or profile.get("custom_prompt") or "").strip()
    if not effective_prompt:
        raise ValueError("请输入翻译 Prompt；公版古文白话链路没有内置 Prompt")
    normalized = ClassicalVernacularProfilePayload(
        custom_prompt=effective_prompt,
        scope_mode=scope_mode,
        preview_target_chars=preview_target_chars,
        preview_chapters=preview_chapters,
    )
    old_scope = str(profile.get("scope_mode") or cfg.get("public_domain_classical_scope") or "complete")
    if "_checkpoint_profile" not in profile:
        profile["_checkpoint_profile"] = {
            key: value
            for key, value in profile.items()
            if not str(key).startswith("_")
        }

    if old_scope == "characters" and "_checkpoint_segment_limit" not in profile:
        target = int(profile.get("preview_target_chars") or 0)
        ordered = sorted(list(task.chapters or []), key=lambda item: int(item.chapter_num))
        translated_before_last = sum(
            len(re.sub(r"\s+", "", str(item.content or "")))
            for item in ordered[:-1]
        )
        remaining_at_partial_chapter = max(1, target - translated_before_last)
        profile["_checkpoint_segment_limit"] = min(
            CLASSICAL_SEGMENT_CHARS,
            max(600, int(remaining_at_partial_chapter / 1.35)),
        )
    elif "_checkpoint_segment_limit" not in profile:
        profile["_checkpoint_segment_limit"] = CLASSICAL_SEGMENT_CHARS

    cfg.setdefault(
        "classical_preview_history",
        {
            "scope_mode": old_scope,
            "preview_target_chars": int(profile.get("preview_target_chars") or 0),
            "preview_chapters": int(profile.get("preview_chapters") or 0),
            "completed_chapters": len(task.chapters or []),
        },
    )
    profile.update(
        custom_prompt=normalized.custom_prompt,
        scope_mode=normalized.scope_mode,
        preview_target_chars=normalized.preview_target_chars,
        preview_chapters=normalized.preview_chapters,
    )
    reports = cfg.get("source_reports") if isinstance(cfg.get("source_reports"), list) else []
    cfg.update(
        adaptation_profile=profile,
        public_domain_classical_preview=normalized.scope_mode != "complete",
        public_domain_classical_scope=normalized.scope_mode,
        bookshelf=_classical_bookshelf_for_scope(normalized.scope_mode),
        category_label=_classical_bookshelf_for_scope(normalized.scope_mode),
        generation_complete=False,
        target_length=(
            normalized.preview_target_chars
            if normalized.scope_mode == "characters"
            else sum(int(item.get("chars") or 0) for item in reports if isinstance(item, dict))
        ),
        target_chapters=(
            normalized.preview_chapters
            if normalized.scope_mode == "chapters"
            else sum(
            int(item.get("detected_chapters") or 0)
            for item in reports
            if isinstance(item, dict)
            )
        ),
        public_domain_quality_blocked=False,
    )
    for key in (
        "public_domain_quality_reason",
        "active_run_id",
        "active_run_started_at",
        "active_run_heartbeat_at",
        "pause_requested",
        "auto_requeue_reason",
        "auto_requeued_at",
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
        "failed_auto_recovery_suppressed_at",
        "dispatch_last_error",
        "dispatch_last_failed_at",
    ):
        cfg.pop(key, None)
    task.config = cfg
    task.status = TaskStatus.PENDING
    task.progress = min(92, max(1, int(task.progress or 0)))
    task.current_step = (
        "已恢复公版古文白话任务，正在从分段断点继续全文翻译"
        if normalized.scope_mode == "complete"
        else "已恢复公版古文白话任务，正在按当前试译范围从分段断点继续"
    )
    task.error_message = None


def _mark_dispatch_failure_pending(task: Task, exc: Exception, *, step: str) -> None:
    """Keep a saved public-domain task runnable when broker publish fails."""

    clear_task_dispatch_guard(str(task.id))
    cfg = dict(task.config or {})
    cfg.update(
        dispatch_last_error=f"Celery dispatch failed: {exc}"[:1000],
        dispatch_last_failed_at=datetime.now(timezone.utc).isoformat(),
    )
    task.config = cfg
    task.status = TaskStatus.PENDING
    task.error_message = f"Celery dispatch failed: {exc}"[:2000]
    task.current_step = step[:255]


def _dispatch_classical_task(task: Task) -> None:
    from app.tasks.tasks import process_public_domain_longform

    task_id = str(task.id)
    try:
        if not acquire_task_dispatch_guard(task_id):
            return
        process_public_domain_longform.apply_async(
            args=[task_id],
            time_limit=7 * 24 * 60 * 60,
            soft_time_limit=7 * 24 * 60 * 60 - 300,
        )
    except Exception:
        clear_task_dispatch_guard(task_id)
        raise


def _prepare_classical_complete_continuation(task: Task) -> None:
    """Backward-compatible wrapper retained for existing workers and tests."""

    _prepare_classical_continuation(task, scope_mode="complete")


@router.get(
    "/tasks/{task_id}/classical-workspace",
    response_model=ClassicalWorkspaceStateResponse,
    summary="恢复公版古文白话专属工作台完整状态",
)
async def get_classical_workspace_state(
    task_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClassicalWorkspaceStateResponse:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    try:
        return _classical_workspace_state(task)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/tasks/{task_id}/continue",
    response_model=CreateTaskResponse,
    summary="按字数、按章节或全文从同一任务断点继续翻译",
)
async def continue_classical_translation(
    task_id: UUID,
    req: ClassicalContinueRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CreateTaskResponse:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    cfg = dict(task.config or {})
    if not bool(cfg.get("public_domain_classical_vernacular")):
        raise HTTPException(status_code=400, detail="该任务不是公版古文白话任务")
    if task.status in {TaskStatus.PENDING, TaskStatus.PROCESSING, TaskStatus.WRITING_CHAPTERS}:
        raise HTTPException(status_code=409, detail="任务正在翻译中，请勿重复投递")
    try:
        _prepare_classical_continuation(
            task,
            scope_mode=req.scope_mode,
            preview_target_chars=req.preview_target_chars,
            preview_chapters=req.preview_chapters,
            custom_prompt=req.custom_prompt,
        )
        db.commit()
        db.refresh(task)
        _dispatch_classical_task(task)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        persisted = db.get(Task, task_id)
        if persisted is not None:
            _mark_dispatch_failure_pending(
                persisted,
                exc,
                step="续译状态已保存，但后台投递失败，维护队列将自动重试",
            )
            db.commit()
        raise HTTPException(status_code=503, detail=f"续译投递失败：{exc}") from exc
    return CreateTaskResponse(task_id=task.id, status=task.status)


@router.post(
    "/tasks/{task_id}/continue-complete",
    response_model=CreateTaskResponse,
    summary="在同一任务内从试译或故障断点继续完整翻译",
)
async def continue_classical_complete_translation(
    task_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CreateTaskResponse:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    cfg = dict(task.config or {})
    if not bool(cfg.get("public_domain_classical_vernacular")):
        raise HTTPException(status_code=400, detail="该任务不是公版古文白话任务")
    profile = dict(cfg.get("adaptation_profile") or {}) if isinstance(cfg.get("adaptation_profile"), dict) else {}
    scope_mode = str(profile.get("scope_mode") or cfg.get("public_domain_classical_scope") or "complete")
    if task.status in {TaskStatus.PENDING, TaskStatus.PROCESSING, TaskStatus.WRITING_CHAPTERS}:
        raise HTTPException(status_code=409, detail="任务正在持续翻译中，请勿重复投递")
    if scope_mode == "complete" and bool(cfg.get("generation_complete")) and task.status == TaskStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="该书已经完整翻译完成")
    try:
        _prepare_classical_continuation(task, scope_mode="complete")
        db.commit()
        db.refresh(task)
        _dispatch_classical_task(task)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        persisted = db.get(Task, task_id)
        if persisted is not None:
            _mark_dispatch_failure_pending(
                persisted,
                exc,
                step="续译任务已保存，但后台投递失败，维护队列将自动重试",
            )
            db.commit()
        raise HTTPException(status_code=503, detail=f"续译投递失败：{exc}") from exc
    return CreateTaskResponse(task_id=task.id, status=task.status)


@router.post(
    "/tasks/{task_id}/pause",
    response_model=CreateTaskResponse,
    summary="在完整分段边界暂停古文白话翻译",
)
async def pause_classical_translation(
    task_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CreateTaskResponse:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    cfg = dict(task.config or {})
    if not bool(cfg.get("public_domain_classical_vernacular")):
        raise HTTPException(status_code=400, detail="该任务不是公版古文白话任务")
    if task.status == TaskStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="任务已经完成，无需暂停")
    active_run = str(cfg.get("active_run_id") or "").strip()
    cfg["pause_requested"] = bool(active_run)
    cfg["paused_from_status"] = task.status
    task.config = cfg
    task.status = TaskStatus.PAUSED
    task.current_step = "已请求暂停；当前完整分段落盘后停止，可从同一断点继续"
    task.error_message = None
    db.commit()
    db.refresh(task)
    return CreateTaskResponse(task_id=task.id, status=task.status)


@router.post(
    "/tasks/{task_id}/resume",
    response_model=CreateTaskResponse,
    summary="按暂停前的范围从断点继续古文白话翻译",
)
async def resume_classical_translation(
    task_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CreateTaskResponse:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    cfg = dict(task.config or {})
    if not bool(cfg.get("public_domain_classical_vernacular")):
        raise HTTPException(status_code=400, detail="该任务不是公版古文白话任务")
    if task.status != TaskStatus.PAUSED:
        raise HTTPException(status_code=409, detail="只有已暂停任务可以继续")
    if str(cfg.get("active_run_id") or "").strip():
        raise HTTPException(status_code=409, detail="当前完整分段仍在保存，请稍后再继续")
    profile = dict(cfg.get("adaptation_profile") or {})
    try:
        _prepare_classical_continuation(
            task,
            scope_mode=str(profile.get("scope_mode") or "complete"),
            preview_target_chars=int(profile.get("preview_target_chars") or 0),
            preview_chapters=int(profile.get("preview_chapters") or 0),
            custom_prompt=str(profile.get("custom_prompt") or ""),
        )
        db.commit()
        db.refresh(task)
        _dispatch_classical_task(task)
    except Exception as exc:
        db.rollback()
        persisted = db.get(Task, task_id)
        if persisted is not None and persisted.status == TaskStatus.PENDING:
            _mark_dispatch_failure_pending(
                persisted,
                exc,
                step="继续状态已保存，但后台投递失败，维护队列将自动重试",
            )
            db.commit()
        raise HTTPException(status_code=503, detail=f"继续翻译投递失败：{exc}") from exc
    return CreateTaskResponse(task_id=task.id, status=task.status)


@router.post(
    "/tasks/{task_id}/restart",
    response_model=CreateTaskResponse,
    summary="清除旧译文断点并按当前范围从头重新翻译",
)
async def restart_classical_translation(
    task_id: UUID,
    req: ClassicalContinueRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CreateTaskResponse:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    cfg = dict(task.config or {})
    if not bool(cfg.get("public_domain_classical_vernacular")):
        raise HTTPException(status_code=400, detail="该任务不是公版古文白话任务")
    if task.status in {TaskStatus.PENDING, TaskStatus.PROCESSING, TaskStatus.WRITING_CHAPTERS}:
        raise HTTPException(status_code=409, detail="请先暂停当前任务，再从头重新开始")
    if str(cfg.get("active_run_id") or "").strip():
        raise HTTPException(status_code=409, detail="正在保存当前完整分段，请等待状态显示“已暂停”后再重新开始")
    output_dir = PROJECT_ROOT / "backend" / "output" / f"task_{task.id}"
    state_committed = False
    try:
        chapter_dir = output_dir / "classical_chapters"
        if chapter_dir.exists():
            shutil.rmtree(chapter_dir)
        for name in (
            "01_classical_vernacular_plan.json",
            "04_final.md",
            "quality_report.json",
        ):
            (output_dir / name).unlink(missing_ok=True)
        db.query(Chapter).filter(Chapter.task_id == task.id).delete(synchronize_session=False)
        for key in (
            "checkpoint_chapters",
            "checkpoint_chars",
            "classical_translated_segments",
            "markdown",
            "markdown_chars",
            "generation_complete",
            "public_domain_quality_reason",
        ):
            cfg.pop(key, None)
        profile = dict(cfg.get("adaptation_profile") or {})
        for key in ("_checkpoint_profile", "_checkpoint_segment_limit", "_local_prompt_overrides"):
            profile.pop(key, None)
        profile.update(
            custom_prompt=str(req.custom_prompt or profile.get("custom_prompt") or ""),
            scope_mode=req.scope_mode,
            preview_target_chars=req.preview_target_chars,
            preview_chapters=req.preview_chapters,
        )
        cfg["adaptation_profile"] = profile
        task.config = cfg
        task.progress = 0
        _prepare_classical_continuation(
            task,
            scope_mode=req.scope_mode,
            preview_target_chars=req.preview_target_chars,
            preview_chapters=req.preview_chapters,
            custom_prompt=req.custom_prompt,
        )
        task.progress = 0
        task.current_step = "旧译文断点已清除，正在从原文第一章第一段重新翻译"
        db.commit()
        state_committed = True
        db.refresh(task)
        _dispatch_classical_task(task)
    except Exception as exc:
        db.rollback()
        if state_committed:
            persisted = db.get(Task, task_id)
            if persisted is not None:
                _mark_dispatch_failure_pending(
                    persisted,
                    exc,
                    step="旧译文已清除，但后台投递失败，维护队列将自动重试",
                )
                db.commit()
        raise HTTPException(status_code=503, detail=f"重新开始失败：{exc}") from exc
    return CreateTaskResponse(task_id=task.id, status=task.status)


@router.post(
    "/tasks/{task_id}/local-retranslate",
    response_model=CreateTaskResponse,
    summary="只重译一个已完成章节或分段",
)
async def local_retranslate_classical_translation(
    task_id: UUID,
    req: ClassicalLocalRetranslateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CreateTaskResponse:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    cfg = dict(task.config or {})
    if not bool(cfg.get("public_domain_classical_vernacular")):
        raise HTTPException(status_code=400, detail="该任务不是公版古文白话任务")
    if task.status in {TaskStatus.PENDING, TaskStatus.PROCESSING, TaskStatus.WRITING_CHAPTERS}:
        raise HTTPException(status_code=409, detail="请先暂停主任务，再进行局部 Prompt 调试")
    if str(cfg.get("active_run_id") or "").strip():
        raise HTTPException(status_code=409, detail="正在保存当前完整分段，请稍后再进行局部重译")
    chapter = db.query(Chapter).filter(
        Chapter.task_id == task.id,
        Chapter.chapter_num == req.chapter_num,
    ).one_or_none()
    if chapter is None:
        raise HTTPException(status_code=409, detail="该章节尚未完整生成，暂时不能局部重译")
    profile = dict(cfg.get("adaptation_profile") or {})
    stored_overrides = (
        dict(profile.get("_local_prompt_overrides") or {})
        if isinstance(profile.get("_local_prompt_overrides"), dict)
        else {}
    )
    chapter_key = str(req.chapter_num)
    previous_chapter_overrides = stored_overrides.get(chapter_key)
    chapter_overrides = (
        dict(previous_chapter_overrides)
        if isinstance(previous_chapter_overrides, dict)
        else {}
    )
    if req.segment_num is None:
        chapter_overrides = {"all": req.custom_prompt}
    else:
        chapter_overrides[str(req.segment_num)] = req.custom_prompt
    stored_overrides[chapter_key] = chapter_overrides
    profile["_local_prompt_overrides"] = stored_overrides
    cfg["adaptation_profile"] = profile
    cfg["classical_local_retranslation"] = {
        "chapter_num": req.chapter_num,
        "segment_num": req.segment_num,
        "custom_prompt": req.custom_prompt,
        "status": "queued",
        "previous_chapter_overrides": previous_chapter_overrides,
    }
    cfg["classical_local_previous_status"] = task.status
    task.config = cfg
    task.status = TaskStatus.PROCESSING
    task.current_step = (
        f"正在只重译第 {req.chapter_num} 章第 {req.segment_num} 段"
        if req.segment_num
        else f"正在只重译第 {req.chapter_num} 章"
    )
    task.error_message = None
    db.commit()
    db.refresh(task)
    try:
        from app.tasks.tasks import retranslate_public_domain_classical_selection

        if not acquire_task_dispatch_guard(str(task.id)):
            return CreateTaskResponse(task_id=task.id, status=task.status)
        try:
            retranslate_public_domain_classical_selection.apply_async(args=[str(task.id)])
        except Exception:
            clear_task_dispatch_guard(str(task.id))
            raise
    except Exception as exc:
        clear_task_dispatch_guard(str(task.id))
        previous_status = str((task.config or {}).get("classical_local_previous_status") or TaskStatus.COMPLETED)
        task.status = (
            previous_status
            if previous_status in {TaskStatus.PAUSED, TaskStatus.COMPLETED}
            else TaskStatus.PENDING
        )
        task.current_step = "局部重译投递失败，原译文保持不变，可稍后重新提交"
        task.error_message = f"局部重译投递失败：{exc}"[:2000]
        db.commit()
        raise HTTPException(status_code=503, detail=str(task.error_message)) from exc
    return CreateTaskResponse(task_id=task.id, status=task.status)


@router.post(
    "/generate",
    response_model=CreateTaskResponse,
    summary=f"创建 1–{MAX_SOURCES} 本来源的简洁公版融合任务",
)
async def generate_public_domain_rewrite(
    req: PublicDomainRewriteRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CreateTaskResponse:
    if req.generation_mode in {FULL_MODE, CLASSICAL_VERNACULAR_MODE}:
        return _create_longform_task(db, user, req)
    if req.generation_mode != "legacy_simple":
        raise HTTPException(status_code=400, detail=f"不支持的公版生成模式：{req.generation_mode}")
    try:
        result = await asyncio.to_thread(
            rewrite_public_domain_fusion,
            sources=[_to_service_source(item) for item in req.sources],
            target_title=req.target_title,
            target_direction=req.target_direction,
            target_length=req.target_length,
            output_style=req.output_style,
            public_domain_confirmed=req.public_domain_confirmed,
        )
    except PublicDomainRewriteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Public-domain rewrite generation failed")
        raise HTTPException(status_code=502, detail=f"公版融合改写失败：{exc}") from exc
    return _save_bookshelf_item(db, user, result)


@router.post(
    "/generate-from-files",
    response_model=CreateTaskResponse,
    summary=f"上传 1–{MAX_SOURCES} 个 txt/md/docx/pdf/epub 文件并生成公版融合稿",
)
async def generate_public_domain_rewrite_from_files(
    files: list[UploadFile] = File(...),
    metadata_json: str = Form("{}"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CreateTaskResponse:
    try:
        metadata = json.loads(metadata_json or "{}")
        if not isinstance(metadata, dict):
            raise ValueError("metadata_json 必须是对象")
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"metadata_json 格式错误：{exc}") from exc

    if not 1 <= len(files) <= MAX_SOURCES:
        raise HTTPException(status_code=400, detail=f"请上传 1 到 {MAX_SOURCES} 个来源文件。")
    source_meta = metadata.get("sources") or []
    if not isinstance(source_meta, list):
        raise HTTPException(status_code=400, detail="metadata_json.sources 必须是数组。")

    payloads: list[PublicDomainSourcePayload] = []
    for index, file in enumerate(files):
        meta = source_meta[index] if index < len(source_meta) and isinstance(source_meta[index], dict) else {}
        try:
            text = await _read_uploaded_text(file)
            payloads.append(
                PublicDomainSourcePayload(
                    title=str(meta.get("title") or Path(file.filename or f"source-{index + 1}").stem),
                    text=text,
                    author=meta.get("author"),
                    publication_year=_optional_int(meta.get("publication_year")),
                    author_death_year=_optional_int(meta.get("author_death_year")),
                    rights_note=str(meta.get("rights_note") or "用户确认公版/公共领域"),
                    rights_evidence_url=meta.get("rights_evidence_url"),
                )
            )
        except (ValueError, HTTPException) as exc:
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(status_code=400, detail=f"第 {index + 1} 个来源无效：{exc}") from exc

    request = PublicDomainRewriteRequest(
        sources=payloads,
        target_title=metadata.get("target_title"),
        target_direction=metadata.get("target_direction"),
        target_length=_optional_int(metadata.get("target_length")),
        target_chapters=_optional_int(metadata.get("target_chapters")),
        output_style=metadata.get("output_style"),
        public_domain_confirmed=bool(metadata.get("public_domain_confirmed")),
        generation_mode=str(metadata.get("generation_mode") or FULL_MODE),
        research_use_confirmed=bool(metadata.get("research_use_confirmed")),
        adaptation_profile=metadata.get("adaptation_profile"),
    )
    return await generate_public_domain_rewrite(request, user, db)


def _create_longform_task(
    db: Session,
    user: User,
    req: PublicDomainRewriteRequest,
) -> CreateTaskResponse:
    generation_mode = req.generation_mode
    if generation_mode not in {FULL_MODE, CLASSICAL_VERNACULAR_MODE}:
        raise HTTPException(status_code=400, detail=f"不支持的公版长篇模式：{generation_mode}")
    classical = generation_mode == CLASSICAL_VERNACULAR_MODE
    classical_profile = (
        req.adaptation_profile.model_dump()
        if classical and req.adaptation_profile
        else {}
    )
    if classical and not str(classical_profile.get("custom_prompt") or "").strip():
        raise HTTPException(
            status_code=400,
            detail="请输入翻译 Prompt；公版古文白话链路没有内置 Prompt",
        )
    classical_scope = str(classical_profile.get("scope_mode") or "complete")
    classical_is_preview = classical and classical_scope != "complete"
    classical_preview_chars = int(classical_profile.get("preview_target_chars") or 0)
    classical_preview_chapters = int(classical_profile.get("preview_chapters") or 0)
    raw_sources = [_to_longform_source(item) for item in req.sources]
    try:
        sources = validate_longform_sources(
            raw_sources,
            public_domain_confirmed=req.public_domain_confirmed,
            generation_mode=generation_mode,
        )
        dimensions = derive_longform_dimensions(
            sources,
            requested_chars=req.target_length,
            requested_chapters=req.target_chapters,
            generation_mode=generation_mode,
        )
        reports = build_longform_source_reports(sources)
    except PublicDomainValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    task_id = uuid4()
    output_dir = PROJECT_ROOT / "backend" / "output" / f"task_{task_id}"
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = output_dir / "00_public_domain_sources.json"
    snapshot_path.write_text(
        json.dumps(
            [longform_source_to_dict(item) for item in sources],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    source_reports = [
        {
            "source_id": item.source_id,
            "title": item.title,
            "author": item.author,
            "chars": item.chars,
            "detected_chapters": item.detected_chapters,
            "rights_basis": item.rights_basis,
            "sha256": item.sha256,
        }
        for item in reports
    ]
    task = Task(
        id=task_id,
        source_url=(
            "manual://public-domain-classical-vernacular"
            if classical
            else "manual://public-domain-serial-longform"
        ),
        owner_id=user.id,
        status=TaskStatus.PENDING,
        progress=0,
        current_step=(
            f"已接收 {len(sources)} 份公版古典原文，等待按约 {classical_preview_chars} 字试译"
            if classical and classical_scope == "characters"
            else f"已接收 {len(sources)} 份公版古典原文，等待试译前 {classical_preview_chapters} 章"
            if classical and classical_scope == "chapters"
            else f"已接收 {len(sources)} 份公版古典原文，等待按原章节顺序逐段完整翻译"
            if classical
            else f"已接收 {len(sources)} 本来源，等待整书规划"
        ),
        config={
            "title": (
                req.target_title
                or (sources[0].title if classical else "公版融合长篇")
            )[:120],
            "bookshelf": (
                _classical_bookshelf_for_scope(classical_scope)
                if classical
                else PUBLIC_DOMAIN_BOOKSHELF
            ),
            "category": "public_domain_rewrite",
            "category_label": (
                _classical_bookshelf_for_scope(classical_scope)
                if classical
                else PUBLIC_DOMAIN_BOOKSHELF
            ),
            "public_domain_rewrite": True,
            "public_domain_longform_v2": True,
            "public_domain_serial_story": not classical,
            "public_domain_classical_vernacular": classical,
            "public_domain_classical_preview": classical_is_preview,
            "public_domain_classical_scope": classical_scope if classical else None,
            "public_domain_policy_version": (
                CLASSICAL_VERNACULAR_POLICY_VERSION if classical else LONGFORM_POLICY_VERSION
            ),
            "generation_mode": generation_mode,
            "source_snapshot": str(snapshot_path),
            "source_count": len(sources),
            "source_reports": source_reports,
            "target_title": req.target_title,
            "target_direction": req.target_direction,
            "target_length": (
                classical_preview_chars
                if classical and classical_scope == "characters"
                else dimensions.target_chars
            ),
            "target_chapters": (
                classical_preview_chapters
                if classical and classical_scope == "chapters"
                else dimensions.target_chapters
            ),
            "median_source_chars": dimensions.median_source_chars,
            "median_source_chapters": dimensions.median_source_chapters,
            "output_style": req.output_style,
            "adaptation_profile": classical_profile or None,
            "public_domain_confirmed": True,
            "generation_complete": False,
            "cover_disabled": True,
            "aigc_required": False,
            "aigc_status": "not_required",
            "aigc_passed": True,
            "aigc_quality_passed": True,
            "aigc_report_available": False,
        },
    )
    try:
        db.add(task)
        db.commit()
        # A multi-million-character book may legitimately run for days.  The
        # shared dispatcher gives both modes the same lease cleanup and safe
        # pending-on-publish-failure semantics.
        _dispatch_classical_task(task)
    except Exception as exc:
        db.rollback()
        persisted = db.get(Task, task_id)
        if persisted is not None:
            _mark_dispatch_failure_pending(
                persisted,
                exc,
                step="任务已保存，但后台队列投递失败，维护队列将自动重试",
            )
            db.commit()
        logger.exception("Failed to dispatch serial public-domain long-form task %s", task_id)
        raise HTTPException(
            status_code=503,
            detail=f"任务已保存但后台投递失败 (task_id={task_id}): {exc}",
        ) from exc
    return CreateTaskResponse(task_id=task.id, status=task.status)


@router.post(
    "/tasks/{task_id}/retry-aigc",
    response_model=CreateTaskResponse,
    summary="兼容旧按钮；简洁公版链路不自动重跑朱雀",
)
async def retry_public_domain_aigc(
    task_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CreateTaskResponse:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    raise HTTPException(
        status_code=409,
        detail="当前公版链路已回到简洁模式，不再自动重跑朱雀；如需复检，请下载书稿后单独送检。",
    )


def _to_service_source(item: PublicDomainSourcePayload) -> PublicDomainSource:
    return PublicDomainSource(
        title=item.title,
        text=item.text,
        author=item.author,
        publication_year=item.publication_year,
        rights_note=item.rights_note,
    )


def _to_longform_source(item: PublicDomainSourcePayload) -> LongformSource:
    return LongformSource(
        title=item.title,
        text=item.text,
        author=item.author,
        publication_year=item.publication_year,
        author_death_year=item.author_death_year,
        rights_note=item.rights_note,
        rights_evidence_url=item.rights_evidence_url,
    )


def _save_bookshelf_item(db: Session, user: User, result) -> CreateTaskResponse:
    title = (result.title or PUBLIC_DOMAIN_BOOKSHELF).strip()[:120]
    markdown = (
        f"@最终书名\n{title}\n\n"
        f"@最终简介\n基于用户确认来源独立融合创作的公版新作。\n\n"
        f"@目录\n1. {title}\n\n"
        f"@正文\n@1. {title}\n{result.manuscript.strip()}\n"
    )
    source_reports = [
        {
            "title": item.title,
            "chars": item.chars,
            "public_domain_basis": item.public_domain_basis,
            "sampled_chars": item.sampled_chars,
        }
        for item in result.source_reports
    ]
    review_required = result.status != "ok"
    task = Task(
        source_url="manual://public-domain-rewrite-simple",
        owner_id=user.id,
        status=TaskStatus.MANUAL_REVIEW if review_required else TaskStatus.COMPLETED,
        progress=100,
        current_step=(
            "公版融合已生成，但本地材料检查未通过；仅供人工复核"
            if review_required
            else "公版融合完成（简洁链路；可下载并人工复核）"
        ),
        config={
            "title": title,
            "bookshelf": PUBLIC_DOMAIN_BOOKSHELF,
            "category": "public_domain_rewrite",
            "category_label": PUBLIC_DOMAIN_BOOKSHELF,
            "public_domain_rewrite": True,
            "public_domain_longform_v2": False,
            "public_domain_policy_version": "legacy-simple-v4-source-adaptive",
            "generation_mode": "legacy_simple",
            "source_mode": result.source_mode,
            "source_mode_label": result.source_mode_label,
            "source_mode_evidence": list(result.source_mode_evidence),
            "source_count": len(source_reports),
            "source_reports": source_reports,
            "public_domain_blueprint": result.blueprint[:12_000],
            "quality_report": result.quality_report,
            "quality_report_summary": result.quality_report[:2_000],
            "warnings": list(result.warnings),
            "local_review_status": result.status,
            "markdown": markdown,
            "markdown_chars": len(markdown),
            "generation_complete": True,
            "cover_disabled": True,
            "aigc_required": False,
            "aigc_status": "not_required",
            "aigc_passed": True,
            "aigc_quality_passed": True,
            "aigc_report_available": False,
        },
    )
    chapter = Chapter(
        task=task,
        chapter_num=1,
        title=title[:512],
        content=result.manuscript,
        word_count=len(result.manuscript or ""),
    )
    try:
        db.add(task)
        db.add(chapter)
        db.commit()
        db.refresh(task)
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to save public-domain bookshelf item")
        raise HTTPException(status_code=500, detail=f"公版稿已生成，但写入书架失败：{exc}") from exc
    return CreateTaskResponse(task_id=task.id, status=task.status)


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{value!r} 不是有效数字") from exc


async def _read_uploaded_text(file: UploadFile) -> str:
    content = await file.read()
    suffix = Path(file.filename or "").suffix.lower()
    try:
        if suffix == ".docx":
            return extract_docx_text(content)
        if suffix == ".pdf":
            return extract_pdf_text(content)
        if suffix == ".epub":
            return extract_epub_text(content)
        if is_supported_ocr_image_suffix(suffix):
            return await asyncio.to_thread(
                ocr_image_bytes, content, filename=file.filename or ""
            )
        if suffix not in {".txt", ".md", ".markdown", ""}:
            raise HTTPException(
                status_code=400,
                detail=f"暂不支持 {suffix or '未知'} 文件格式，请上传 txt/md/docx/pdf/epub/图片。",
            )
        if not content:
            raise HTTPException(status_code=400, detail="上传文件为空。")
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise ValueError("文本编码无法识别")
    except (DocxTextError, EpubTextError, ImageOcrError, UnicodeDecodeError) as exc:
        status_code = getattr(exc, "status_code", 422)
        raise HTTPException(status_code=status_code, detail=f"{file.filename or '文件'} 解析失败：{exc}") from exc


__all__ = [
    "MAX_SOURCES",
    "PUBLIC_DOMAIN_BOOKSHELF",
    "PublicDomainRewriteRequest",
    "PublicDomainSourcePayload",
    "_read_uploaded_text",
    "router",
]
