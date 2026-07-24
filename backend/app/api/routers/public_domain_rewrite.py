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
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.routers.auth import get_current_user
from app.db.session import get_db
from app.models import Chapter, Task, TaskStatus, User
from app.schemas.task import CreateTaskResponse
from app.services.image_ocr import (
    ImageOcrError,
    is_supported_ocr_image_suffix,
    ocr_image_bytes,
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

PUBLIC_DOMAIN_BOOKSHELF = "公版融合改写"


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


class PublicDomainRewriteRequest(BaseModel):
    sources: list[PublicDomainSourcePayload] = Field(
        ..., min_length=1, max_length=MAX_SOURCES
    )
    target_title: str | None = Field(default=None, max_length=200)
    target_direction: str | None = Field(default=None, max_length=3000)
    target_length: int | None = Field(default=None, ge=800, le=20_000)
    target_chapters: int | None = Field(default=None, ge=1, le=36)
    output_style: str | None = Field(default=None, max_length=1000)
    public_domain_confirmed: bool = False
    # Kept so old clients do not break.  Both values use the same simple flow.
    generation_mode: str = "legacy_simple"
    research_use_confirmed: bool = False


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
    )
    return await generate_public_domain_rewrite(request, user, db)


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
    task = Task(
        source_url="manual://public-domain-rewrite-simple",
        owner_id=user.id,
        status=TaskStatus.COMPLETED,
        progress=100,
        current_step="公版融合完成（简洁链路；可下载并人工复核）",
        config={
            "title": title,
            "bookshelf": PUBLIC_DOMAIN_BOOKSHELF,
            "category": "public_domain_rewrite",
            "category_label": PUBLIC_DOMAIN_BOOKSHELF,
            "public_domain_rewrite": True,
            "public_domain_longform_v2": False,
            "public_domain_policy_version": "legacy-simple-v3",
            "generation_mode": "legacy_simple",
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
