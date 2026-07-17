"""Dedicated, on-demand Zhuque PDF review routes."""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.routers.auth import get_current_user
from app.core.config import PROJECT_ROOT
from app.db.session import get_db
from app.models import Chapter, Task, TaskStatus, User
from app.services.aigc_report_review import (
    AigcReviewModelError,
    ZhuqueReportError,
    analyze_paired_reports,
    compare_zhuque_reports,
    create_calibration_sample,
    get_calibration_sample,
    list_calibration_samples,
    model_strategy,
    normalize_manuscript_text,
    optimize_report_segments,
    parse_zhuque_report,
    record_calibration_result,
    record_calibration_report_result,
    sha256_text,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/aigc-review", tags=["AIGC Report Review"])

SupportedContentType = Literal["article", "classical", "public_domain"]
SupportedRewriteProvider = Literal["grok", "deepseek"]
AIGC_REVIEW_BOOKSHELF = "AI审稿书架"
_REPORT_UPLOAD_DIR = PROJECT_ROOT / "backend" / "output" / "_aigc_review" / "reports"
_CALIBRATION_REPORT_DIR = (
    PROJECT_ROOT / "backend" / "output" / "_aigc_review" / "calibration_reports"
)


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
    rewrite_provider: SupportedRewriteProvider = "deepseek"
    rewrite_reasoning_effort: Literal["low", "medium", "high"] = "low"
    experiment_type: Literal["layout_only", "syntax_constrained", "combined", "detector_probe"] = "syntax_constrained"
    budget_cny: float = Field(0.30, ge=0, le=20)
    title: str | None = Field(None, max_length=200)
    baseline_report: dict[str, Any] | None = None


class CompareAigcReviewRequest(BaseModel):
    baseline_report: dict[str, Any]
    recheck_report: dict[str, Any]
    optimize_result: dict[str, Any]


class CalibrationSampleRequest(BaseModel):
    text: str = Field(..., min_length=350, max_length=2500)
    content_type: SupportedContentType = "article"
    source_group: str = Field("", max_length=80)
    variant_type: Literal[
        "original",
        "fact_reauthor",
        "human_directed",
        "human_edited",
        "human_control",
        "external_saas",
        "layout_only",
        "syntax_constrained",
        "combined",
        "detector_probe",
    ]
    parent_sha256: str | None = Field(None, min_length=64, max_length=64)
    strategy_id: Literal["syntax_rebuild", "evidence_first", "rhythm_recompose"] | None = None
    quality_gate_passed: bool | None = None


class CalibrationResultRequest(BaseModel):
    text_sha256: str = Field(..., min_length=64, max_length=64)
    aigc_value: float = Field(..., ge=0, le=1)
    report_sha256: str | None = Field(None, min_length=64, max_length=64)


@router.get("/strategy", summary="查看最低成本的当前模型路由")
def get_aigc_review_strategy(user: User = Depends(get_current_user)) -> dict:
    return model_strategy(user_id=str(user.id))


@router.post("/report", summary="本地 OCR 解析真实朱雀 PDF 并映射原稿片段")
async def parse_aigc_report(
    file: UploadFile = File(...),
    original_text: str = Form(...),
    content_type: SupportedContentType = Form("article"),
    user: User = Depends(get_current_user),
) -> dict:
    _ = content_type  # Explicit allow-list excludes all novel workflows.
    filename = file.filename or "zhuque-report.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="请上传朱雀打印导出的 PDF 文件。")
    pdf_bytes = await file.read()
    try:
        parsed = await asyncio.to_thread(
            parse_zhuque_report,
            pdf_bytes,
            original_text,
            filename=filename,
        )
        parsed["model_strategy"] = model_strategy(user_id=str(user.id))
        parsed["report_storage_id"] = _store_report_pdf(user.id, pdf_bytes)
        return parsed
    except ZhuqueReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("朱雀报告解析失败 filename=%s", filename)
        raise HTTPException(status_code=500, detail=f"朱雀报告解析失败: {exc}") from exc


@router.post("/optimize", summary="按选中风险片段生成质量候选或隔离实验稿")
async def optimize_aigc_report(
    req: OptimizeAigcReviewRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    original = normalize_manuscript_text(req.original_text)
    if sha256_text(original) != req.original_sha256:
        raise HTTPException(
            status_code=409,
            detail="原稿在报告解析后发生了变化。请重新解析报告，避免片段坐标错位。",
        )
    try:
        result = await asyncio.to_thread(
            optimize_report_segments,
            original,
            [item.model_dump() for item in req.segments],
            req.selected_segment_ids,
            content_type=req.content_type,
            iteration=req.iteration,
            preferred_strategy=req.preferred_strategy,
            rewrite_provider=req.rewrite_provider,
            rewrite_reasoning_effort=req.rewrite_reasoning_effort,
            experiment_type=req.experiment_type,
            cache_namespace=str(user.id),
            budget_cny=req.budget_cny,
        )
        archive = _save_review_archive(
            db,
            user,
            original=original,
            result=result,
            baseline_report=req.baseline_report or {},
            title=req.title,
            content_type=req.content_type,
        )
        result["archive_task_id"] = str(archive.id)
        return result
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
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        result = await asyncio.to_thread(
            compare_zhuque_reports,
            req.baseline_report,
            req.recheck_report,
            req.optimize_result,
        )
        archive_task_id = req.optimize_result.get("archive_task_id")
        if archive_task_id:
            _update_review_archive(
                db,
                user,
                archive_task_id=archive_task_id,
                optimize_result=req.optimize_result,
                recheck_report=req.recheck_report,
                comparison=result,
            )
            result["archive_task_id"] = str(archive_task_id)
        return result
    except ZhuqueReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("朱雀真实复检归因失败")
        raise HTTPException(status_code=500, detail=f"朱雀真实复检归因失败: {exc}") from exc


@router.post("/calibrations", summary="登记精确哈希的朱雀隔离校准样本")
async def create_aigc_calibration(
    req: CalibrationSampleRequest,
    user: User = Depends(get_current_user),
) -> dict:
    try:
        return await asyncio.to_thread(
            create_calibration_sample,
            user_id=str(user.id),
            text=req.text,
            content_type=req.content_type,
            source_group=req.source_group,
            variant_type=req.variant_type,
            parent_sha256=req.parent_sha256,
            strategy_id=req.strategy_id,
            quality_gate_passed=req.quality_gate_passed,
        )
    except ZhuqueReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/calibrations", summary="查看当前用户的朱雀隔离校准样本")
async def get_aigc_calibrations(
    limit: int = 100,
    user: User = Depends(get_current_user),
) -> list[dict]:
    return await asyncio.to_thread(
        list_calibration_samples,
        user_id=str(user.id),
        limit=limit,
    )


@router.post("/calibrations/{sample_id}/result", summary="登记精确哈希样本的真实朱雀结果")
async def save_aigc_calibration_result(
    sample_id: str,
    req: CalibrationResultRequest,
    user: User = Depends(get_current_user),
) -> dict:
    try:
        return await asyncio.to_thread(
            record_calibration_result,
            user_id=str(user.id),
            sample_id=sample_id,
            submitted_text_sha256=req.text_sha256,
            aigc_value=req.aigc_value,
            report_sha256=req.report_sha256,
        )
    except ZhuqueReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/calibrations/{sample_id}/report", summary="上传隔离样本的真实朱雀 PDF")
async def upload_aigc_calibration_report(
    sample_id: str,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
) -> dict:
    filename = file.filename or "zhuque-calibration.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="请上传朱雀打印导出的 PDF 文件。")
    try:
        sample = await asyncio.to_thread(
            get_calibration_sample,
            user_id=str(user.id),
            sample_id=sample_id,
        )
        pdf_bytes = await file.read()
        parsed = await asyncio.to_thread(
            parse_zhuque_report,
            pdf_bytes,
            str(sample.get("text") or ""),
            filename=filename,
        )
        result = await asyncio.to_thread(
            record_calibration_report_result,
            user_id=str(user.id),
            sample_id=sample_id,
            parsed_report=parsed,
        )
        target = _CALIBRATION_REPORT_DIR / str(user.id) / f"{UUID(sample_id)}.pdf"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(pdf_bytes)
        return result
    except ZhuqueReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/paired-analysis", summary="对照前后正文与两份真实朱雀报告")
async def paired_aigc_analysis(
    baseline_file: UploadFile = File(...),
    modified_file: UploadFile = File(...),
    baseline_text: str = Form(...),
    modified_text: str = Form(...),
    content_type: SupportedContentType = Form("article"),
    hashes_confirmed: bool = Form(False),
    user: User = Depends(get_current_user),
) -> dict:
    for upload in (baseline_file, modified_file):
        if not (upload.filename or "").lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="前后报告都必须是朱雀打印导出的 PDF。")
    baseline_pdf, modified_pdf = await asyncio.gather(
        baseline_file.read(),
        modified_file.read(),
    )
    try:
        baseline_report = await asyncio.to_thread(
            parse_zhuque_report,
            baseline_pdf,
            baseline_text,
            filename=baseline_file.filename or "baseline.pdf",
        )
        modified_report = await asyncio.to_thread(
            parse_zhuque_report,
            modified_pdf,
            modified_text,
            filename=modified_file.filename or "modified.pdf",
        )
        baseline_report["report_storage_id"] = _store_report_pdf(user.id, baseline_pdf)
        modified_report["report_storage_id"] = _store_report_pdf(user.id, modified_pdf)
        result = await asyncio.to_thread(
            analyze_paired_reports,
            baseline_text,
            modified_text,
            baseline_report,
            modified_report,
            content_type=content_type,
            hashes_confirmed=hashes_confirmed,
        )
        result["baseline_report"] = baseline_report
        result["modified_report"] = modified_report
        result["detector_efficacy"] = model_strategy(user_id=str(user.id)).get(
            "detector_efficacy"
        )
        return result
    except ZhuqueReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("朱雀前后报告差分分析失败")
        raise HTTPException(status_code=500, detail=f"朱雀前后报告差分分析失败: {exc}") from exc


def _store_report_pdf(user_id: UUID, pdf_bytes: bytes) -> str:
    storage_id = str(uuid4())
    directory = _REPORT_UPLOAD_DIR / str(user_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{storage_id}.pdf").write_bytes(pdf_bytes)
    return storage_id


def _copy_stored_report(user_id: UUID, report: dict[str, Any], target: Path) -> bool:
    raw = str(report.get("report_storage_id") or "").strip()
    try:
        storage_id = str(UUID(raw))
    except (TypeError, ValueError):
        return False
    source = _REPORT_UPLOAD_DIR / str(user_id) / f"{storage_id}.pdf"
    if not source.is_file():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return True


def _archive_title(value: str | None, original: str) -> str:
    if value and value.strip():
        return value.strip()[:200]
    for line in original.splitlines():
        candidate = line.strip().lstrip("#@！! ")
        if candidate and len(candidate) <= 80:
            return candidate
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    return f"AI审稿 {stamp}"


def _save_review_archive(
    db: Session,
    user: User,
    *,
    original: str,
    result: dict[str, Any],
    baseline_report: dict[str, Any],
    title: str | None,
    content_type: str,
) -> Task:
    reviewed = str(
        result.get("candidate_text")
        or result.get("guarded_candidate_text")
        or original
    ).strip()
    probe_ready = result.get("status") == "probe_ready"
    experimental_quality_bypassed = bool(
        result.get("experimental_quality_bypassed")
        if "experimental_quality_bypassed" in result
        else (probe_ready and not result.get("quality_audit"))
    )
    quality_passed = result.get("status") == "candidate_ready" or (
        probe_ready
        and bool(result.get("quality_audit"))
        and not experimental_quality_bypassed
    )
    task = Task(
        source_url="manual://aigc-review",
        owner_id=user.id,
        status=TaskStatus.MANUAL_REVIEW,
        progress=96 if quality_passed else (94 if probe_ready else 92),
        current_step=(
            "质量已过，等待真实朱雀复检"
            if quality_passed
            else (
                "朱雀探索稿已生成，等待真实复检"
                if probe_ready
                else "已保留审稿候选，等待人工复核"
            )
        ),
        error_message=(
            None
            if quality_passed or probe_ready
            else str(result.get("next_action") or "")[:2000]
        ),
        config={
            "title": _archive_title(title, original),
            "bookshelf": AIGC_REVIEW_BOOKSHELF,
            "category": "aigc_review",
            "category_label": AIGC_REVIEW_BOOKSHELF,
            "aigc_review_archive": True,
            "cover_disabled": True,
            "content_type": content_type,
            "aigc_policy_version": "report-driven-v5",
            "aigc_required": True,
            "aigc_status": (
                "detector_probe_ready"
                if probe_ready
                else ("candidate_ready" if quality_passed else "manual_review")
            ),
            "aigc_passed": False,
            "aigc_quality_passed": quality_passed,
            "aigc_report_available": bool(baseline_report),
            "aigc_initial_human_percent": baseline_report.get("human_percent"),
            "aigc_initial_official_human_percent": baseline_report.get(
                "official_human_percent"
            ),
            "aigc_initial_derived_human_share_percent": baseline_report.get(
                "derived_human_share_percent"
            ),
            "aigc_final_human_percent": None,
            "aigc_rewrite_rounds": int(result.get("iteration") or 1),
            "aigc_manual_review_reason": (
                None if quality_passed else str(result.get("next_action") or "")[:4000]
            ),
            "aigc_experimental_quality_bypassed": experimental_quality_bypassed,
            "aigc_review_request_id": result.get("request_id"),
            "aigc_original_sha256": result.get("original_sha256"),
            "aigc_candidate_sha256": result.get("candidate_sha256") or sha256_text(reviewed),
            "aigc_prompt_version": result.get("prompt_version"),
            "aigc_experiment_type": result.get("experiment_type"),
            "aigc_models": result.get("models") or {},
            "aigc_cost": result.get("cost") or {},
            "aigc_budget": result.get("budget") or {},
            "aigc_editorial_unit_count": result.get("editorial_unit_count") or 0,
            "aigc_audit_batch_count": result.get("audit_batch_count") or 0,
        },
    )
    chapter = Chapter(
        task=task,
        chapter_num=1,
        title=task.config["title"][:512],
        content=reviewed,
        word_count=len(reviewed),
    )
    try:
        db.add(task)
        db.add(chapter)
        db.flush()
        output_dir = PROJECT_ROOT / "backend" / "output" / f"task_{task.id}"
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "04_format.md").write_text(original, encoding="utf-8")
        (output_dir / "05_aigc_candidate.md").write_text(reviewed, encoding="utf-8")
        (output_dir / "aigc_baseline_report.json").write_text(
            json.dumps(baseline_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        report_path = output_dir / "aigc_quality_report.json"
        report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        _copy_stored_report(user.id, baseline_report, output_dir / "zhuque_baseline.pdf")
        cfg = dict(task.config or {})
        cfg["aigc_quality_report"] = str(report_path)
        cfg["aigc_report_available"] = True
        task.config = cfg
        db.commit()
        db.refresh(task)
        return task
    except Exception:
        db.rollback()
        logger.exception("AI 审稿书架归档失败")
        raise HTTPException(status_code=500, detail="审稿已经完成，但写入 AI 审稿书架失败。")


def _update_review_archive(
    db: Session,
    user: User,
    *,
    archive_task_id: str,
    optimize_result: dict[str, Any],
    recheck_report: dict[str, Any],
    comparison: dict[str, Any],
) -> None:
    try:
        task_uuid = UUID(str(archive_task_id))
    except (TypeError, ValueError) as exc:
        raise ZhuqueReportError("审稿书架记录编号无效。") from exc
    task = (
        db.query(Task)
        .filter(Task.id == task_uuid, Task.owner_id == user.id)
        .first()
    )
    if task is None or not bool((task.config or {}).get("aigc_review_archive")):
        raise ZhuqueReportError("没有找到当前用户的审稿书架记录。")
    cfg = dict(task.config or {})
    stored_candidate_hash = str(cfg.get("aigc_candidate_sha256") or "")
    supplied_candidate_hash = str(optimize_result.get("candidate_sha256") or "")
    if stored_candidate_hash != supplied_candidate_hash:
        raise ZhuqueReportError("复检候选与审稿书架中保存的候选不一致。")
    candidate = normalize_manuscript_text(str(optimize_result.get("candidate_text") or ""))
    if not candidate or sha256_text(candidate) != stored_candidate_hash:
        raise ZhuqueReportError("复检候选正文哈希校验失败。")
    probe_mode = optimize_result.get("experiment_type") == "detector_probe"
    probe_hit = probe_mode and bool(comparison.get("detector_pass"))
    passed = bool(comparison.get("strict_pass")) and not probe_mode
    cfg.update(
        {
            "aigc_status": (
                "passed"
                if passed
                else (
                    "detector_probe_hit"
                    if probe_hit
                    else ("detector_probe_no_hit" if probe_mode else "manual_review")
                )
            ),
            "aigc_passed": passed,
            "aigc_final_human_percent": comparison.get("recheck_human_percent"),
            "aigc_final_official_human_percent": comparison.get(
                "recheck_official_human_percent"
            ),
            "aigc_final_derived_human_share_percent": comparison.get(
                "recheck_derived_human_share_percent"
            ),
            "aigc_human_percent_delta": comparison.get("human_percent_delta"),
            "aigc_selected_humanized_share_percent": comparison.get(
                "selected_humanized_share_percent"
            ),
            "aigc_manual_review_reason": None if passed else comparison.get("recommendation_reason"),
            "aigc_comparison_id": comparison.get("comparison_id"),
        }
    )
    if passed:
        cfg["markdown"] = candidate
        cfg["markdown_chars"] = len(candidate)
    else:
        cfg.pop("markdown", None)
        cfg.pop("markdown_chars", None)
    task.config = cfg
    task.status = TaskStatus.COMPLETED if passed else TaskStatus.MANUAL_REVIEW
    task.progress = 100 if passed else (98 if probe_mode else 97)
    task.current_step = (
        "朱雀与质量双达标"
        if passed
        else (
            "探索实验命中 25%，仍需质量重建"
            if probe_hit
            else (
                "探索实验未命中，已停止追加费用"
                if probe_mode
                else "真实复检未过，原稿与候选均已保留"
            )
        )
    )
    task.error_message = (
        None
        if passed
        else str(comparison.get("recommendation_reason") or "")[:2000]
    )
    chapter = db.query(Chapter).filter(Chapter.task_id == task.id).order_by(Chapter.chapter_num).first()
    if chapter is not None:
        chapter.content = candidate
        chapter.word_count = len(candidate)
    output_dir = PROJECT_ROOT / "backend" / "output" / f"task_{task.id}"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "aigc_recheck_report.json").write_text(
        json.dumps(recheck_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "aigc_comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    quality_report_path = output_dir / "aigc_quality_report.json"
    quality_report_path.write_text(
        json.dumps(
            {
                **optimize_result,
                "recheck_report": recheck_report,
                "comparison": comparison,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    cfg = dict(task.config or {})
    cfg["aigc_quality_report"] = str(quality_report_path)
    cfg["aigc_report_available"] = True
    task.config = cfg
    if passed:
        (output_dir / "05_aigc_final.md").write_text(candidate, encoding="utf-8")
    _copy_stored_report(user.id, recheck_report, output_dir / "zhuque_recheck.pdf")
    db.commit()
