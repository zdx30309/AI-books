"""Public-domain fusion rewrite API."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.api.routers.auth import get_current_user
from app.models import User
from app.services.public_domain_rewrite import (
    PublicDomainRewriteError,
    PublicDomainSource,
    rewrite_public_domain_fusion,
)
from app.utils.docx_text import DocxTextError, extract_docx_text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/public-domain-rewrite", tags=["Public Domain Rewrite"])


class PublicDomainSourcePayload(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    text: str = Field(..., min_length=1)
    author: str | None = Field(default=None, max_length=120)
    publication_year: int | None = Field(default=None, ge=0, le=2100)
    rights_note: str | None = Field(default=None, max_length=500)


class PublicDomainRewriteRequest(BaseModel):
    sources: list[PublicDomainSourcePayload] = Field(..., min_length=2, max_length=3)
    target_title: str | None = Field(default=None, max_length=200)
    target_direction: str | None = Field(default=None, max_length=2000)
    target_length: int | None = Field(default=None, ge=800, le=20000)
    output_style: str | None = Field(default=None, max_length=1000)
    public_domain_confirmed: bool = False


class SourceReportPayload(BaseModel):
    title: str
    chars: int
    public_domain_basis: str
    sampled_chars: int


class PublicDomainRewriteResponse(BaseModel):
    title: str
    manuscript: str
    blueprint: str
    quality_report: str
    source_reports: list[SourceReportPayload]
    warnings: list[str]
    status: str
    source_count: int
    manuscript_chars: int


@router.post(
    "/generate",
    response_model=PublicDomainRewriteResponse,
    summary="2-3 本公版/公共领域图书融合改写",
)
async def generate_public_domain_rewrite(
    req: PublicDomainRewriteRequest,
    _user: User = Depends(get_current_user),
) -> PublicDomainRewriteResponse:
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
        raise HTTPException(status_code=502, detail=f"公版融合改写失败: {exc}") from exc
    return _to_response(result)


@router.post(
    "/generate-from-files",
    response_model=PublicDomainRewriteResponse,
    summary="上传 2-3 个 txt/md/docx 文件并做公版融合改写",
)
async def generate_public_domain_rewrite_from_files(
    files: list[UploadFile] = File(...),
    metadata_json: str = Form("{}"),
    _user: User = Depends(get_current_user),
) -> PublicDomainRewriteResponse:
    try:
        metadata = json.loads(metadata_json or "{}")
        if not isinstance(metadata, dict):
            raise ValueError("metadata_json must be an object")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"metadata_json 格式错误: {exc}") from exc

    if len(files) < 2 or len(files) > 3:
        raise HTTPException(status_code=400, detail="请上传 2 到 3 个来源文件。")

    source_meta = metadata.get("sources") or []
    if source_meta and not isinstance(source_meta, list):
        raise HTTPException(status_code=400, detail="metadata_json.sources 必须是数组。")

    sources: list[PublicDomainSourcePayload] = []
    for idx, file in enumerate(files):
        meta = source_meta[idx] if idx < len(source_meta) and isinstance(source_meta[idx], dict) else {}
        text = await _read_uploaded_text(file)
        filename_title = Path(file.filename or f"source-{idx + 1}").stem
        sources.append(
            PublicDomainSourcePayload(
                title=str(meta.get("title") or filename_title),
                author=meta.get("author"),
                publication_year=meta.get("publication_year"),
                rights_note=meta.get("rights_note"),
                text=text,
            )
        )

    request = PublicDomainRewriteRequest(
        sources=sources,
        target_title=metadata.get("target_title"),
        target_direction=metadata.get("target_direction"),
        target_length=_optional_int(metadata.get("target_length")),
        output_style=metadata.get("output_style"),
        public_domain_confirmed=bool(metadata.get("public_domain_confirmed")),
    )
    return await generate_public_domain_rewrite(request, _user)


def _to_service_source(item: PublicDomainSourcePayload) -> PublicDomainSource:
    return PublicDomainSource(
        title=item.title,
        text=item.text,
        author=item.author,
        publication_year=item.publication_year,
        rights_note=item.rights_note,
    )


def _to_response(result) -> PublicDomainRewriteResponse:
    return PublicDomainRewriteResponse(
        title=result.title,
        manuscript=result.manuscript,
        blueprint=result.blueprint,
        quality_report=result.quality_report,
        source_reports=[
            SourceReportPayload(
                title=item.title,
                chars=item.chars,
                public_domain_basis=item.public_domain_basis,
                sampled_chars=item.sampled_chars,
            )
            for item in result.source_reports
        ],
        warnings=list(result.warnings),
        status=result.status,
        source_count=len(result.source_reports),
        manuscript_chars=len(result.manuscript or ""),
    )


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="target_length 必须是数字。") from exc


async def _read_uploaded_text(file: UploadFile) -> str:
    content = await file.read()
    suffix = Path(file.filename or "").suffix.lower()
    try:
        if suffix == ".docx":
            return extract_docx_text(content)
        if suffix not in {".txt", ".md", ".markdown", ""}:
            raise HTTPException(status_code=400, detail=f"暂不支持 {suffix or '未知'} 文件格式，请上传 txt/md/docx。")
        return _decode_text(content)
    except DocxTextError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _decode_text(content: bytes) -> str:
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空。")
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise HTTPException(status_code=400, detail="文本编码无法识别，请保存为 UTF-8 后重试。")
