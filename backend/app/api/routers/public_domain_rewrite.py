"""Public-domain fusion rewrite API."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import shutil
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.routers.auth import get_current_user
from app.db.session import get_db
from app.core.config import PROJECT_ROOT
from app.models import Chapter, Task, TaskStatus, User
from app.schemas.task import CreateTaskResponse
from app.services.aigc_quality_gate import (
    AIGC_POLICY_VERSION,
    AigcManualReviewRequired,
    AigcTechnicalError,
    enforce_aigc_quality_gate,
)
from app.services.public_domain_rewrite import (
    PublicDomainRewriteError,
    PublicDomainSource,
    check_public_domain_manuscript,
    rewrite_public_domain_fusion,
)
from app.services.image_ocr import ImageOcrError, is_supported_ocr_image_suffix, ocr_image_bytes
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
    sources: list[PublicDomainSourcePayload] = Field(..., min_length=1, max_length=3)
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
    task_id: str | None = None
    title: str
    manuscript: str
    blueprint: str
    quality_report: str
    source_reports: list[SourceReportPayload]
    warnings: list[str]
    status: str
    source_count: int
    manuscript_chars: int
    aigc_required: bool = False
    aigc_status: str = "not_required"
    aigc_initial_max_score: int | None = None
    aigc_final_max_score: int | None = None
    aigc_rewrite_rounds: int = 0
    aigc_quality_passed: bool | None = None
    aigc_report_available: bool = False


@router.post(
    "/generate",
    response_model=PublicDomainRewriteResponse,
    summary="1-3 个公版/公共领域来源重构改写",
)
async def generate_public_domain_rewrite(
    req: PublicDomainRewriteRequest,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PublicDomainRewriteResponse:
    task_id = uuid4()
    output_dir = PROJECT_ROOT / "backend" / "output" / f"task_{task_id}"
    service_sources = [_to_service_source(item) for item in req.sources]
    try:
        result = await asyncio.to_thread(
            rewrite_public_domain_fusion,
            sources=service_sources,
            target_title=req.target_title,
            target_direction=req.target_direction,
            target_length=req.target_length,
            output_style=req.output_style,
            public_domain_confirmed=req.public_domain_confirmed,
            aigc_output_dir=output_dir,
        )
    except PublicDomainRewriteError as exc:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        shutil.rmtree(output_dir, ignore_errors=True)
        logger.exception("Public-domain rewrite generation failed")
        raise HTTPException(status_code=502, detail=f"公版融合改写失败: {exc}") from exc
    task = _save_public_domain_bookshelf_item(
        db,
        _user,
        result,
        task_id=task_id,
        sources=service_sources,
    )
    return _to_response(result, task_id=str(task.id))


@router.post(
    "/generate-from-files",
    response_model=PublicDomainRewriteResponse,
    summary="上传 1-3 个 txt/md/docx/图片 文件并做公版重构改写",
)
async def generate_public_domain_rewrite_from_files(
    files: list[UploadFile] = File(...),
    metadata_json: str = Form("{}"),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PublicDomainRewriteResponse:
    try:
        metadata = json.loads(metadata_json or "{}")
        if not isinstance(metadata, dict):
            raise ValueError("metadata_json must be an object")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"metadata_json 格式错误: {exc}") from exc

    if len(files) < 1 or len(files) > 3:
        raise HTTPException(status_code=400, detail="请上传 1 到 3 个来源文件。")

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
    return await generate_public_domain_rewrite(request, _user, db)


def _to_service_source(item: PublicDomainSourcePayload) -> PublicDomainSource:
    return PublicDomainSource(
        title=item.title,
        text=item.text,
        author=item.author,
        publication_year=item.publication_year,
        rights_note=item.rights_note,
    )


PUBLIC_DOMAIN_BOOKSHELF = "公版图书洗稿"


def _save_public_domain_bookshelf_item(
    db: Session,
    user: User,
    result,
    *,
    task_id: UUID | None = None,
    sources: list[PublicDomainSource] | None = None,
) -> Task:
    deliverable = result.status == "ok" and bool(result.manuscript.strip())
    preserved = (result.pre_aigc_manuscript or result.manuscript or "").strip()
    markdown = _public_domain_result_markdown(result) if deliverable else ""
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
        id=task_id or uuid4(),
        source_url="manual://public-domain-rewrite",
        owner_id=user.id,
        status=(
            TaskStatus.COMPLETED
            if deliverable
            else TaskStatus.FAILED
            if result.status == "failed"
            else TaskStatus.MANUAL_REVIEW
        ),
        progress=100 if deliverable else 93,
        current_step=(
            "已完成"
            if deliverable
            else "AIGC 技术故障，可仅重试朱雀阶段"
            if result.status == "failed"
            else "原稿已保留，等待人工复核"
        ),
        error_message=(
            str((result.aigc_report or {}).get("manualReviewReason") or "")[:2000]
            if result.status == "manual_review"
            else str((result.aigc_report or {}).get("technicalError") or "")[:2000]
            if result.status == "failed"
            else None
        ),
        config={
            "title": result.title,
            "bookshelf": PUBLIC_DOMAIN_BOOKSHELF,
            "category": "public_domain_rewrite",
            "category_label": PUBLIC_DOMAIN_BOOKSHELF,
            **({"markdown": markdown, "markdown_chars": len(markdown)} if deliverable else {}),
            "public_domain_rewrite": True,
            "source_count": len(source_reports),
            "source_reports": source_reports,
            "warnings": list(result.warnings),
            "status": result.status,
            "quality_report": result.quality_report,
            "quality_report_summary": result.quality_report[:2000],
            "public_domain_blueprint": result.blueprint[:12000],
            "cover_disabled": True,
            "aigc_policy_version": "v2",
            "aigc_required": bool(result.aigc_required),
            "aigc_status": result.aigc_status,
            "aigc_passed": bool(deliverable and (not result.aigc_required or result.aigc_status == "passed")),
            "aigc_max_score": result.aigc_final_max_score,
            "aigc_initial_max_score": result.aigc_initial_max_score,
            "aigc_final_max_score": result.aigc_final_max_score,
            "aigc_rewrite_rounds": result.aigc_rewrite_rounds,
            "aigc_quality_passed": result.aigc_quality_passed,
            "aigc_report_available": bool(result.aigc_report),
            "aigc_manual_review_reason": str(
                (result.aigc_report or {}).get("manualReviewReason")
                or (result.aigc_report or {}).get("technicalError")
                or ""
            )[:4000]
            or None,
        },
    )
    chapter = (
        Chapter(
            task=task,
            chapter_num=1,
            title=(result.title or PUBLIC_DOMAIN_BOOKSHELF)[:512],
            content=result.manuscript,
            word_count=len(result.manuscript or ""),
        )
        if deliverable
        else None
    )
    try:
        db.add(task)
        if chapter is not None:
            db.add(chapter)
        db.commit()
        db.refresh(task)
        output_dir = PROJECT_ROOT / "backend" / "output" / f"task_{task.id}"
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "04_format.md").write_text(preserved, encoding="utf-8")
        if sources:
            (output_dir / "00_public_domain_sources.json").write_text(
                json.dumps(
                    [
                        {
                            "title": item.title,
                            "text": item.text,
                            "author": item.author,
                            "publication_year": item.publication_year,
                            "rights_note": item.rights_note,
                        }
                        for item in sources
                    ],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        if deliverable:
            (output_dir / "05_aigc_final.md").write_text(result.manuscript, encoding="utf-8")
        if result.aigc_report:
            report_path = output_dir / "aigc_quality_report.json"
            report_path.write_text(
                json.dumps(result.aigc_report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            cfg = dict(task.config or {})
            cfg["aigc_quality_report"] = str(report_path)
            cfg["aigc_report_available"] = True
            task.config = cfg
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to persist public-domain rewrite result")
        raise HTTPException(status_code=500, detail="公版洗稿已生成，但写入书架失败。")
    return task


def _load_public_domain_sources(output_dir: Path) -> list[PublicDomainSource]:
    path = output_dir / "00_public_domain_sources.json"
    if not path.exists():
        raise HTTPException(status_code=409, detail="公版来源 QA 快照不存在，不能安全执行 AIGC-only 重试。")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("source snapshot must be a list")
        return [
            PublicDomainSource(
                title=str(item.get("title") or ""),
                text=str(item.get("text") or ""),
                author=item.get("author"),
                publication_year=item.get("publication_year"),
                rights_note=item.get("rights_note"),
            )
            for item in payload
            if isinstance(item, dict)
        ]
    except (OSError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=500, detail=f"公版来源 QA 快照读取失败: {exc}") from exc


def _sync_aigc_task_config(
    task: Task,
    report: dict[str, object] | None,
    *,
    status_value: str,
    reason: str = "",
) -> None:
    cfg = dict(task.config or {})
    cfg.update(
        aigc_status=status_value,
        aigc_passed=status_value == "passed",
        aigc_quality_passed=bool(report and report.get("qualityPassed")) if status_value == "passed" else False,
        aigc_report_available=bool(report),
        aigc_manual_review_reason=reason[:4000] or None,
    )
    if report:
        cfg.update(
            aigc_initial_max_score=int(report.get("initialMaxScore") or 0),
            aigc_final_max_score=int(report.get("finalMaxScore") or 0),
            aigc_max_score=int(report.get("finalMaxScore") or report.get("maxScore") or 0),
            aigc_rewrite_rounds=int(report.get("rewriteRounds") or 0),
        )
    task.config = cfg


@router.post(
    "/tasks/{task_id}/retry-aigc",
    response_model=CreateTaskResponse,
    summary="公版融合仅重跑朱雀阶段并复做来源 QA",
)
async def retry_public_domain_aigc(
    task_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CreateTaskResponse:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    cfg = dict(task.config or {})
    if not bool(cfg.get("public_domain_rewrite")):
        raise HTTPException(status_code=409, detail="该任务不是公版融合任务。")
    if not bool(cfg.get("aigc_required")) or cfg.get("aigc_policy_version") != AIGC_POLICY_VERSION:
        raise HTTPException(status_code=409, detail="该任务未绑定朱雀 v2 策略。")
    if task.status not in {TaskStatus.MANUAL_REVIEW, TaskStatus.FAILED}:
        raise HTTPException(status_code=409, detail=f"当前状态为 {task.status}，不能重跑朱雀阶段。")

    output_dir = PROJECT_ROOT / "backend" / "output" / f"task_{task.id}"
    original_path = output_dir / "04_format.md"
    if not original_path.exists() or original_path.stat().st_size <= 0:
        raise HTTPException(status_code=409, detail="不可变 04_format.md 原稿不存在。")
    sources = _load_public_domain_sources(output_dir)
    original = original_path.read_text(encoding="utf-8")
    task.status = TaskStatus.PROCESSING
    task.current_step = "仅重跑朱雀阶段（复用原稿、报告缓存与公版来源快照）"
    task.error_message = None
    db.commit()

    try:
        result = await enforce_aigc_quality_gate(
            original,
            category="public_domain_rewrite",
            output_dir=output_dir,
            required=True,
        )
        source_quality = check_public_domain_manuscript(result.text, sources)
        if source_quality.get("status") == "needs_repair":
            report = dict(result.report or {})
            reason = "降低 AI 率后的候选未通过公版来源重合/残留复检"
            report.update(
                status="manual_review",
                passed=False,
                qualityPassed=False,
                manualReviewReason=reason,
                finalSha256=None,
            )
            (output_dir / "aigc_quality_report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (output_dir / "05_aigc_final.md").unlink(missing_ok=True)
            _sync_aigc_task_config(task, report, status_value="manual_review", reason=reason)
            task.status = TaskStatus.MANUAL_REVIEW
            task.progress = 93
            task.current_step = "原稿已保留；AIGC 候选未通过公版来源 QA，等待人工复核"
            task.error_message = reason
            db.commit()
            return CreateTaskResponse(task_id=task.id, status=task.status)

        title = str(cfg.get("title") or PUBLIC_DOMAIN_BOOKSHELF)
        markdown = _public_domain_markdown_from_parts(title, result.text)
        db.query(Chapter).filter(Chapter.task_id == task.id).delete(synchronize_session=False)
        db.add(
            Chapter(
                task_id=task.id,
                chapter_num=1,
                title=title[:512],
                content=result.text,
                word_count=len(result.text),
            )
        )
        cfg = dict(task.config or {})
        cfg.update(markdown=markdown, markdown_chars=len(markdown), status="ok")
        task.config = cfg
        _sync_aigc_task_config(task, result.report, status_value="passed")
        task.status = TaskStatus.COMPLETED
        task.progress = 100
        task.current_step = "已完成（朱雀与质量双达标）"
        task.error_message = None
        db.commit()
    except AigcManualReviewRequired as exc:
        report = exc.result.report if exc.result else None
        _sync_aigc_task_config(task, report, status_value="manual_review", reason=str(exc))
        task.status = TaskStatus.MANUAL_REVIEW
        task.progress = 93
        task.current_step = "原稿已保留，朱雀与文稿质量未同时达标"
        task.error_message = str(exc)[:2000]
        db.commit()
    except AigcTechnicalError as exc:
        _sync_aigc_task_config(task, exc.report, status_value="technical_failed", reason=str(exc))
        task.status = TaskStatus.FAILED
        task.progress = 93
        task.current_step = "AIGC 技术故障，可再次仅重跑朱雀阶段"
        task.error_message = str(exc)[:2000]
        db.commit()
    return CreateTaskResponse(task_id=task.id, status=task.status)


def _public_domain_result_markdown(result) -> str:
    title = (result.title or PUBLIC_DOMAIN_BOOKSHELF).strip()
    manuscript = (result.manuscript or "").strip()
    return _public_domain_markdown_from_parts(title, manuscript)


def _public_domain_markdown_from_parts(title: str, manuscript: str) -> str:
    title = (title or PUBLIC_DOMAIN_BOOKSHELF).strip()
    manuscript = (manuscript or "").strip()
    return (
        f"@最终书名\n{title}\n\n"
        f"@最终简介\n由公版/公共领域来源重构生成的改写稿。\n\n"
        f"@目录\n1. {title}\n\n"
        f"@正文\n@1. {title}\n{manuscript}\n"
    )


def _to_response(result, task_id: str | None = None) -> PublicDomainRewriteResponse:
    return PublicDomainRewriteResponse(
        task_id=task_id,
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
        aigc_required=bool(result.aigc_required),
        aigc_status=result.aigc_status,
        aigc_initial_max_score=result.aigc_initial_max_score,
        aigc_final_max_score=result.aigc_final_max_score,
        aigc_rewrite_rounds=result.aigc_rewrite_rounds,
        aigc_quality_passed=result.aigc_quality_passed,
        aigc_report_available=bool(result.aigc_report),
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
        if is_supported_ocr_image_suffix(suffix):
            return await asyncio.to_thread(ocr_image_bytes, content, filename=file.filename or "")
        if suffix not in {".txt", ".md", ".markdown", ""}:
            raise HTTPException(status_code=400, detail=f"暂不支持 {suffix or '未知'} 文件格式，请上传 txt/md/docx/png/jpg/webp。")
        return _decode_text(content)
    except DocxTextError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except ImageOcrError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _decode_text(content: bytes) -> str:
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空。")
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise HTTPException(status_code=400, detail="文本编码无法识别，请保存为 UTF-8 后重试。")
