"""Background API for evidence-grounded public-domain long-form fusion."""
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
from app.core.config import PROJECT_ROOT, settings
from app.db.session import get_db
from app.models import Chapter, Task, TaskStatus, User
from app.schemas.task import CreateTaskResponse
from app.services.aigc_quality_gate import (
    AIGC_POLICY_VERSION,
    AigcManualReviewRequired,
    AigcTechnicalError,
    enforce_aigc_quality_gate,
)
from app.services.image_ocr import ImageOcrError, is_supported_ocr_image_suffix, ocr_image_bytes
from app.services.public_domain_longform import (
    FULL_MODE,
    MAX_SOURCES,
    MIN_SOURCES,
    POLICY_VERSION,
    PREVIEW_MAX_SOURCES,
    PREVIEW_MIN_SOURCES,
    RESEARCH_PREVIEW_MODE,
    PublicDomainSource,
    PublicDomainValidationError,
    audit_edited_final_manuscript,
    build_source_reports,
    derive_dimensions,
    parse_final_markdown,
    source_from_dict,
    source_to_dict,
    validate_sources,
)
from app.utils.docx_text import DocxTextError, extract_docx_text, extract_pdf_text
from app.utils.epub_text import EpubTextError, extract_epub_text


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/public-domain-rewrite", tags=["Public Domain Rewrite"])

PUBLIC_DOMAIN_BOOKSHELF = "公版融合长篇"


class PublicDomainSourcePayload(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    text: str = Field(..., min_length=1)
    author: str | None = Field(default=None, max_length=120)
    publication_year: int | None = Field(default=None, ge=0, le=2100)
    author_death_year: int | None = Field(default=None, ge=0, le=2100)
    rights_note: str = Field(..., min_length=4, max_length=1000)
    rights_evidence_url: str | None = Field(default=None, max_length=2048)


class PublicDomainRewriteRequest(BaseModel):
    sources: list[PublicDomainSourcePayload] = Field(..., min_length=PREVIEW_MIN_SOURCES, max_length=MAX_SOURCES)
    target_title: str | None = Field(default=None, max_length=200)
    target_direction: str | None = Field(default=None, max_length=3000)
    target_length: int | None = Field(default=None, ge=8_000, le=220_000)
    target_chapters: int | None = Field(default=None, ge=2, le=36)
    output_style: str | None = Field(default=None, max_length=1000)
    public_domain_confirmed: bool = False
    generation_mode: str = FULL_MODE
    research_use_confirmed: bool = False


@router.post(
    "/generate",
    response_model=CreateTaskResponse,
    summary="创建 8–10 本公版来源的长篇融合任务",
)
def generate_public_domain_rewrite(
    req: PublicDomainRewriteRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CreateTaskResponse:
    return _create_task(db, user, req)


@router.post(
    "/generate-from-files",
    response_model=CreateTaskResponse,
    summary="上传 8–10 本公版来源并创建长篇融合任务",
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
            raise ValueError("metadata_json must be an object")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"metadata_json 格式错误: {exc}") from exc
    generation_mode = str(metadata.get("generation_mode") or FULL_MODE)
    min_files = PREVIEW_MIN_SOURCES if generation_mode == RESEARCH_PREVIEW_MODE else MIN_SOURCES
    max_files = PREVIEW_MAX_SOURCES if generation_mode == RESEARCH_PREVIEW_MODE else MAX_SOURCES
    if not min_files <= len(files) <= max_files:
        raise HTTPException(status_code=400, detail=f"请一次上传 {min_files} 到 {max_files} 本来源文件。")
    source_meta = metadata.get("sources") or []
    if not isinstance(source_meta, list) or len(source_meta) != len(files):
        raise HTTPException(status_code=400, detail="metadata_json.sources 必须与上传文件逐一对应。")

    sources: list[PublicDomainSourcePayload] = []
    for index, file in enumerate(files):
        meta = source_meta[index]
        if not isinstance(meta, dict):
            raise HTTPException(status_code=400, detail=f"第 {index + 1} 本来源元数据格式错误。")
        text = await _read_uploaded_text(file)
        filename_title = Path(file.filename or f"source-{index + 1}").stem
        try:
            sources.append(
                PublicDomainSourcePayload(
                    title=str(meta.get("title") or filename_title),
                    author=meta.get("author"),
                    publication_year=_optional_int(meta.get("publication_year")),
                    author_death_year=_optional_int(meta.get("author_death_year")),
                    rights_note=str(meta.get("rights_note") or ""),
                    rights_evidence_url=meta.get("rights_evidence_url"),
                    text=text,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"第 {index + 1} 本来源元数据不完整: {exc}") from exc
    try:
        request = PublicDomainRewriteRequest(
            sources=sources,
            target_title=metadata.get("target_title"),
            target_direction=metadata.get("target_direction"),
            target_length=_optional_int(metadata.get("target_length")),
            target_chapters=_optional_int(metadata.get("target_chapters")),
            output_style=metadata.get("output_style"),
            public_domain_confirmed=bool(metadata.get("public_domain_confirmed")),
            generation_mode=generation_mode,
            research_use_confirmed=bool(metadata.get("research_use_confirmed")),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"任务参数不正确: {exc}") from exc
    return _create_task(db, user, request)


def _create_task(db: Session, user: User, req: PublicDomainRewriteRequest) -> CreateTaskResponse:
    raw_sources = [_to_service_source(item) for item in req.sources]
    preview = req.generation_mode == RESEARCH_PREVIEW_MODE
    try:
        sources = validate_sources(
            raw_sources,
            public_domain_confirmed=req.public_domain_confirmed,
            generation_mode=req.generation_mode,
            research_use_confirmed=req.research_use_confirmed,
        )
        dimensions = derive_dimensions(
            sources,
            requested_chars=req.target_length,
            requested_chapters=req.target_chapters,
            generation_mode=req.generation_mode,
        )
        reports = build_source_reports(sources)
    except PublicDomainValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    task_id = uuid4()
    output_dir = _task_output_dir(task_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = output_dir / "00_public_domain_sources.json"
    snapshot_path.write_text(
        json.dumps([source_to_dict(item) for item in sources], ensure_ascii=False, indent=2),
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
        owner_id=user.id,
        source_url=(
            "manual://public-domain-research-preview"
            if preview
            else "manual://public-domain-longform"
        ),
        status=TaskStatus.PENDING,
        progress=0,
        current_step=(
            f"已接收 {len(sources)} 份研究材料，等待隔离抽取事实/概念线索"
            if preview
            else f"已接收 {len(sources)} 本公版来源，等待证据抽取"
        ),
        config={
            "title": (req.target_title or "公版融合长篇")[:120],
            "bookshelf": PUBLIC_DOMAIN_BOOKSHELF,
            "category": "public_domain_rewrite",
            "category_label": PUBLIC_DOMAIN_BOOKSHELF,
            "public_domain_rewrite": True,
            "public_domain_longform_v2": True,
            "generation_mode": req.generation_mode,
            "research_preview": preview,
            "research_use_confirmed": bool(req.research_use_confirmed),
            "public_domain_policy_version": POLICY_VERSION,
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
            "public_domain_confirmed": bool(req.public_domain_confirmed),
            "generation_complete": False,
            "cover_disabled": True,
            "aigc_policy_version": AIGC_POLICY_VERSION,
            "aigc_required": bool(settings.AIGC_GATE_ENABLED) and not preview,
            "aigc_status": "pending" if settings.AIGC_GATE_ENABLED and not preview else "not_required",
            "aigc_report_available": False,
        },
    )
    try:
        db.add(task)
        db.commit()
        from app.tasks.tasks import process_public_domain_longform

        process_public_domain_longform.delay(str(task.id))
    except Exception as exc:
        db.rollback()
        persisted = db.get(Task, task_id)
        if persisted is not None:
            persisted.status = TaskStatus.FAILED
            persisted.error_message = f"Celery dispatch failed: {exc}"[:2000]
            persisted.current_step = "任务已保存，但后台队列投递失败，可点击继续生成"
            db.commit()
        logger.exception("Failed to dispatch public-domain long-form task %s", task_id)
        raise HTTPException(status_code=503, detail=f"任务已保存但后台投递失败 (task_id={task_id}): {exc}") from exc
    return CreateTaskResponse(task_id=task.id, status=task.status)


@router.post(
    "/tasks/{task_id}/retry-aigc",
    response_model=CreateTaskResponse,
    summary="公版长篇仅重跑朱雀阶段并复做来源 QA",
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
    if not bool(cfg.get("public_domain_longform_v2")):
        raise HTTPException(status_code=409, detail="该任务不是新版公版融合长篇任务。")
    if not bool(cfg.get("generation_complete")):
        raise HTTPException(status_code=409, detail="长篇生成尚未完成，应继续生成而不是仅重跑朱雀。")
    if not bool(cfg.get("aigc_required")) or cfg.get("aigc_policy_version") != AIGC_POLICY_VERSION:
        raise HTTPException(status_code=409, detail="该任务未绑定朱雀 v2 策略。")
    if task.status not in {TaskStatus.MANUAL_REVIEW, TaskStatus.FAILED}:
        raise HTTPException(status_code=409, detail=f"当前状态为 {task.status}，不能重跑朱雀阶段。")
    output_dir = _task_output_dir(task.id)
    original_path = output_dir / "04_format.md"
    if not original_path.exists() or original_path.stat().st_size <= 0:
        raise HTTPException(status_code=409, detail="不可变 04_format.md 原稿不存在。")
    sources = _load_sources(output_dir)
    original = original_path.read_text(encoding="utf-8")
    task.status = TaskStatus.PROCESSING
    task.current_step = "仅重跑朱雀阶段，完成后复做来源重合与章节检查"
    task.error_message = None
    db.commit()
    try:
        result = await enforce_aigc_quality_gate(
            original,
            category="public_domain_rewrite",
            output_dir=output_dir,
            required=True,
        )
        source_quality = await asyncio.to_thread(
            audit_edited_final_manuscript,
            work_dir=output_dir,
            manuscript=result.text,
            sources=sources,
        )
        title, summary, chapters = parse_final_markdown(result.text)
        expected_chapters = int(cfg.get("target_chapters") or 0)
        if source_quality.get("status") != "ok" or len(chapters) != expected_chapters:
            reason = "朱雀候选未通过逐章事实、语言、篇幅、来源重合与全书复检，或章节结构发生变化"
            _mark_aigc_review(task, db, result.report, reason)
            return CreateTaskResponse(task_id=task.id, status=task.status)
        _persist_chapters(db, task, chapters)
        cfg = dict(task.config or {})
        cfg.update(
            title=title,
            summary=summary,
            markdown=result.text,
            markdown_chars=len(result.text),
            aigc_status="passed",
            aigc_passed=True,
            aigc_quality_passed=True,
            aigc_report_available=bool(result.report),
            aigc_initial_max_score=result.initial_max_score,
            aigc_final_max_score=result.max_score,
            aigc_max_score=result.max_score,
            aigc_rewrite_rounds=result.rewrite_rounds,
        )
        task.config = cfg
        task.status = TaskStatus.COMPLETED
        task.progress = 100
        task.current_step = "公版、事实、来源重合、质量与朱雀闸门全部通过"
        task.error_message = None
        (output_dir / "05_aigc_final.md").write_text(result.text, encoding="utf-8")
        if result.report:
            (output_dir / "aigc_quality_report.json").write_text(
                json.dumps(result.report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        db.commit()
    except AigcManualReviewRequired as exc:
        _mark_aigc_review(task, db, exc.result.report if exc.result else None, str(exc))
    except AigcTechnicalError as exc:
        cfg = dict(task.config or {})
        cfg.update(aigc_status="technical_failed", aigc_passed=False, aigc_report_available=bool(exc.report))
        task.config = cfg
        task.status = TaskStatus.FAILED
        task.progress = 96
        task.current_step = "朱雀技术故障；长篇原稿已保留，可仅重试朱雀"
        task.error_message = str(exc)[:2000]
        db.commit()
    return CreateTaskResponse(task_id=task.id, status=task.status)


def _mark_aigc_review(task: Task, db: Session, report: dict | None, reason: str) -> None:
    cfg = dict(task.config or {})
    cfg.update(
        aigc_status="manual_review",
        aigc_passed=False,
        aigc_quality_passed=False,
        aigc_report_available=bool(report),
        aigc_manual_review_reason=reason[:4000],
    )
    if report:
        cfg.update(
            aigc_initial_max_score=int(report.get("initialMaxScore") or 0),
            aigc_final_max_score=int(report.get("finalMaxScore") or 0),
            aigc_max_score=int(report.get("finalMaxScore") or report.get("maxScore") or 0),
            aigc_rewrite_rounds=int(report.get("rewriteRounds") or 0),
        )
    task.config = cfg
    task.status = TaskStatus.MANUAL_REVIEW
    task.progress = 96
    task.current_step = "高质量长篇原稿已保留；朱雀候选未通过全部终稿闸门"
    task.error_message = reason[:2000]
    db.commit()


def _persist_chapters(db: Session, task: Task, chapters) -> None:
    db.query(Chapter).filter(Chapter.task_id == task.id).delete(synchronize_session=False)
    for item in chapters:
        db.add(
            Chapter(
                task_id=task.id,
                chapter_num=item.number,
                title=item.title[:512],
                content=item.text,
                word_count=len(item.text),
            )
        )
    db.flush()


def _load_sources(output_dir: Path) -> list[PublicDomainSource]:
    path = output_dir / "00_public_domain_sources.json"
    if not path.exists():
        raise HTTPException(status_code=409, detail="公版来源快照不存在，不能安全继续。")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("source snapshot must be a list")
        return [source_from_dict(item) for item in payload if isinstance(item, dict)]
    except (OSError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=500, detail=f"公版来源快照读取失败: {exc}") from exc


def _to_service_source(item: PublicDomainSourcePayload) -> PublicDomainSource:
    return PublicDomainSource(
        title=item.title,
        text=item.text,
        author=item.author,
        publication_year=item.publication_year,
        author_death_year=item.author_death_year,
        rights_note=item.rights_note,
        rights_evidence_url=item.rights_evidence_url,
    )


def _task_output_dir(task_id: UUID) -> Path:
    return PROJECT_ROOT / "backend" / "output" / f"task_{task_id}"


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{value!r} 不是有效整数") from exc


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
            return await asyncio.to_thread(ocr_image_bytes, content, filename=file.filename or "")
        if suffix not in {".txt", ".md", ".markdown", ""}:
            raise HTTPException(
                status_code=400,
                detail=f"暂不支持 {suffix or '未知'} 文件格式，请上传 txt/md/docx/pdf/epub/png/jpg/webp。",
            )
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise UnicodeDecodeError("text", content, 0, 1, "unknown encoding")
    except (DocxTextError, EpubTextError, ImageOcrError, UnicodeDecodeError) as exc:
        status_code = getattr(exc, "status_code", 422)
        raise HTTPException(status_code=status_code, detail=f"{file.filename or '文件'} 解析失败: {exc}") from exc


__all__ = [
    "PUBLIC_DOMAIN_BOOKSHELF",
    "PublicDomainRewriteRequest",
    "PublicDomainSourcePayload",
    "_read_uploaded_text",
    "router",
]
