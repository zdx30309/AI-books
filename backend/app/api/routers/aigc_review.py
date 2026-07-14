"""Dedicated, on-demand Zhuque PDF review routes."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.api.routers.auth import get_current_user
from app.models import User
from app.services.aigc_report_review import (
    AigcReviewModelError,
    ZhuqueReportError,
    compare_zhuque_reports,
    model_strategy,
    normalize_manuscript_text,
    optimize_report_segments,
    parse_zhuque_report,
    sha256_text,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/aigc-review", tags=["AIGC Report Review"])

SupportedContentType = Literal["article", "classical", "public_domain"]
SupportedRewriteProvider = Literal["grok", "deepseek"]


class ZhuqueSegmentPayload(BaseModel):
    segment_id: int = Field(..., ge=1, le=999)
    share_percent: float = Field(..., ge=0, le=100)
    char_count: int = Field(..., ge=1)
    aigc_value: float = Field(..., ge=0, le=1)
    label: Literal["human", "suspected", "ai"]
    start: int = Field(..., ge=0)
    end: int = Field(..., ge=1)
    mapped_text: str = Field(..., min_length=1)
    mapping_confidence: float = Field(..., ge=0, le=1)
    recommended: bool = False


class OptimizeAigcReviewRequest(BaseModel):
    original_text: str = Field(..., min_length=1, max_length=200_000)
    original_sha256: str = Field(..., min_length=64, max_length=64)
    content_type: SupportedContentType = "article"
    segments: list[ZhuqueSegmentPayload] = Field(..., min_length=1, max_length=200)
    selected_segment_ids: list[int] = Field(..., min_length=1, max_length=200)
    iteration: int = Field(1, ge=1, le=20)
    preferred_strategy: Literal["syntax_rebuild", "evidence_first", "rhythm_recompose"] | None = None
    rewrite_provider: SupportedRewriteProvider = "grok"
    rewrite_reasoning_effort: Literal["low", "medium", "high"] = "low"


class CompareAigcReviewRequest(BaseModel):
    baseline_report: dict[str, Any]
    recheck_report: dict[str, Any]
    optimize_result: dict[str, Any]


@router.get("/strategy", summary="查看最低成本的当前模型路由")
def get_aigc_review_strategy(_user: User = Depends(get_current_user)) -> dict:
    return model_strategy()


@router.post("/report", summary="本地 OCR 解析真实朱雀 PDF 并映射原稿片段")
async def parse_aigc_report(
    file: UploadFile = File(...),
    original_text: str = Form(...),
    content_type: SupportedContentType = Form("article"),
    _user: User = Depends(get_current_user),
) -> dict:
    _ = content_type  # Explicit allow-list excludes all novel workflows.
    filename = file.filename or "zhuque-report.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="请上传朱雀打印导出的 PDF 文件。")
    pdf_bytes = await file.read()
    try:
        return await asyncio.to_thread(
            parse_zhuque_report,
            pdf_bytes,
            original_text,
            filename=filename,
        )
    except ZhuqueReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("朱雀报告解析失败 filename=%s", filename)
        raise HTTPException(status_code=500, detail=f"朱雀报告解析失败: {exc}") from exc


@router.post("/optimize", summary="按选中风险片段生成一个候选并独立终审")
async def optimize_aigc_report(
    req: OptimizeAigcReviewRequest,
    _user: User = Depends(get_current_user),
) -> dict:
    original = normalize_manuscript_text(req.original_text)
    if sha256_text(original) != req.original_sha256:
        raise HTTPException(
            status_code=409,
            detail="原稿在报告解析后发生了变化。请重新解析报告，避免片段坐标错位。",
        )
    try:
        return await asyncio.to_thread(
            optimize_report_segments,
            original,
            [item.model_dump() for item in req.segments],
            req.selected_segment_ids,
            content_type=req.content_type,
            iteration=req.iteration,
            preferred_strategy=req.preferred_strategy,
            rewrite_provider=req.rewrite_provider,
            rewrite_reasoning_effort=req.rewrite_reasoning_effort,
        )
    except ZhuqueReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AigcReviewModelError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("朱雀报告定向审稿失败")
        raise HTTPException(status_code=500, detail=f"朱雀报告定向审稿失败: {exc}") from exc


@router.post("/compare", summary="比较真实复检报告并归因各改写策略")
async def compare_aigc_reports(
    req: CompareAigcReviewRequest,
    _user: User = Depends(get_current_user),
) -> dict:
    try:
        return await asyncio.to_thread(
            compare_zhuque_reports,
            req.baseline_report,
            req.recheck_report,
            req.optimize_result,
        )
    except ZhuqueReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("朱雀真实复检归因失败")
        raise HTTPException(status_code=500, detail=f"朱雀真实复检归因失败: {exc}") from exc
