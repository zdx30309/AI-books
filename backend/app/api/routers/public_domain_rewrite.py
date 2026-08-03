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
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
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
from app.services.public_domain_longform import (
    validate_sources as validate_longform_sources,
)
from app.services.public_domain_rewrite import (
    MAX_SOURCES,
    PublicDomainRewriteError,
    PublicDomainSource,
    rewrite_public_domain_fusion,
)
from app.utils.docx_text import DocxTextError, extract_docx_text, extract_pdf_text
from app.utils.epub_text import EpubTextError, extract_epub_text

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/public-domain-rewrite", tags=["Public Domain Rewrite"])

PUBLIC_DOMAIN_BOOKSHELF = "公版融合长篇"
PUBLIC_DOMAIN_CLASSICAL_BOOKSHELF = "公版古文白话"


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
    work_type: str = Field(default="古典小说", max_length=200)
    era_background: str = Field(default="保持原文时代背景，不擅自现代化", max_length=500)
    narrative_perspective: str = Field(default="保持原文叙事视角", max_length=200)
    target_audience: str = Field(default="现代汉语读者", max_length=300)
    vernacular_level: str = Field(default="准确、清楚、完整的现代白话", max_length=300)
    adaptation_intensity: str = Field(default="仅完整翻译", max_length=100)
    web_fiction_ratio: str = Field(default="0%", max_length=100)
    language_tendency: str = Field(
        default="忠实原意，不做文学改写或风格润色", max_length=500
    )
    must_preserve: str = Field(
        default="原文全部信息、顺序、人物、称谓、专名、数字、事件、因果与语气",
        max_length=3000,
    )
    focus_issues: str = Field(
        default="漏译、错译、增译、缩写、扩写、重排、解释和续写",
        max_length=3000,
    )
    special_requirements: str = Field(
        default="只做完整翻译；不得改写、润色、扩写、缩写、重组、评论、总结或续写",
        max_length=3000,
    )


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
            f"已接收 {len(sources)} 份公版古典原文，等待按原章节顺序逐段完整翻译"
            if classical
            else f"已接收 {len(sources)} 本来源，等待整书规划"
        ),
        config={
            "title": (
                req.target_title
                or (sources[0].title if classical else "公版融合长篇")
            )[:120],
            "bookshelf": (
                PUBLIC_DOMAIN_CLASSICAL_BOOKSHELF if classical else PUBLIC_DOMAIN_BOOKSHELF
            ),
            "category": "public_domain_rewrite",
            "category_label": (
                PUBLIC_DOMAIN_CLASSICAL_BOOKSHELF if classical else PUBLIC_DOMAIN_BOOKSHELF
            ),
            "public_domain_rewrite": True,
            "public_domain_longform_v2": True,
            "public_domain_serial_story": not classical,
            "public_domain_classical_vernacular": classical,
            "public_domain_policy_version": (
                CLASSICAL_VERNACULAR_POLICY_VERSION if classical else LONGFORM_POLICY_VERSION
            ),
            "generation_mode": generation_mode,
            "source_snapshot": str(snapshot_path),
            "source_count": len(sources),
            "source_reports": source_reports,
            "target_title": req.target_title,
            "target_direction": req.target_direction,
            "target_length": dimensions.target_chars,
            "target_chapters": dimensions.target_chapters,
            "median_source_chars": dimensions.median_source_chars,
            "median_source_chapters": dimensions.median_source_chapters,
            "output_style": req.output_style,
            "adaptation_profile": (
                req.adaptation_profile.model_dump() if req.adaptation_profile else None
            ),
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
        from app.tasks.tasks import process_public_domain_longform

        if classical:
            # A multi-million-character book may legitimately run for days.
            # Every successful segment is checkpointed, so worker loss resumes
            # from disk rather than resending completed paid model calls.
            process_public_domain_longform.apply_async(
                args=[str(task.id)],
                time_limit=7 * 24 * 60 * 60,
                soft_time_limit=7 * 24 * 60 * 60 - 300,
            )
        else:
            process_public_domain_longform.delay(str(task.id))
    except Exception as exc:
        db.rollback()
        persisted = db.get(Task, task_id)
        if persisted is not None:
            persisted.status = TaskStatus.FAILED
            persisted.error_message = f"Celery dispatch failed: {exc}"[:2000]
            persisted.current_step = "任务已保存，但后台队列投递失败，可点击继续生成"
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
