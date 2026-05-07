"""Task routers."""
from __future__ import annotations

import asyncio
import logging
import re
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import quote
from uuid import UUID
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import PROJECT_ROOT
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
    CreateTaskRequest,
    CreateTaskResponse,
    MarkdownResponse,
    RegenerateSelectionRequest,
    RegenerateSelectionResponse,
    StageContentResponse,
    TaskMetadataUpdateRequest,
    TaskListItem,
    TaskListResponse,
    TaskCategoryListResponse,
    TaskStatusResponse,
)
from app.services.gemini import regenerate_selected_text
from app.tasks.tasks import process_video

_STAGE_FILES = {
    "writer": "01_writer.md",
    "writer_repaired": "01b_writer_repaired.md",
    "polish": "02_polish.md",
    "refine": "03_refine.md",
    "format": "04_format.md",
}
_FRONT_SECTION_NAMES = ("最终书名", "最终简介", "三个备选书名", "三个备选简介", "目录", "导言", "正文")
_BODY_SECTION_RE = re.compile(r"^@?\s*正文\s*$", re.MULTILINE)
_FINAL_TITLE_SECTION_RE = re.compile(
    r"^@?\s*(最终书名|三个备选书名)\s*$([\s\S]*?)(?=^@?\s*(?:"
    + "|".join(map(re.escape, _FRONT_SECTION_NAMES))
    + r")\s*$|\Z)",
    re.MULTILINE,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tasks", tags=["Tasks"])


def _normalize_task_config(config: dict | None) -> dict:
    cfg = dict(config or {})
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
    if not str(cfg.get("bookshelf") or "").strip():
        cfg["bookshelf"] = category_meta["label"]
    return cfg


def _dispatch_task(task: Task) -> None:
    try:
        process_video.delay(str(task.id))
    except Exception as e:
        logger.exception("[task %s] Celery 投递失败", task.id)
        raise RuntimeError(f"Celery dispatch failed: {e}") from e


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
    cfg = _normalize_task_config(req.config)
    task = Task(
        source_url=req.url,
        owner_id=user.id,
        config=cfg,
        status=TaskStatus.PENDING,
        progress=0,
        current_step="排队中",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    logger.info("[task %s] 建库完成, 准备投 Celery", task.id)

    try:
        _dispatch_task(task)
    except Exception as e:
        logger.exception("[task %s] Celery 投递失败", task.id)
        task.status = TaskStatus.FAILED
        task.error_message = f"Celery dispatch failed: {e}"[:2000]
        db.commit()
        raise HTTPException(
            status_code=503,
            detail=f"任务已建库但 Celery 投递失败 (task_id={task.id}): {e}",
        )

    return CreateTaskResponse(task_id=task.id, status=task.status)


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
    if len(urls) > 100:
        raise HTTPException(status_code=400, detail="一次最多放入 100 个链接")

    cfg = _normalize_task_config(req.config)
    existing_urls = {
        row[0]
        for row in db.query(Task.source_url)
        .filter(Task.owner_id == user.id, Task.source_url.in_(urls))
        .all()
    }

    created: list[BatchCreateTaskItem] = []
    skipped: list[BatchCreateTaskItem] = [
        BatchCreateTaskItem(url=url, status="skipped", reason="候选列表内重复")
        for url in duplicate_urls
    ]
    tasks_to_dispatch: list[Task] = []

    for url in urls:
        if url in existing_urls:
            skipped.append(BatchCreateTaskItem(url=url, status="skipped", reason="已存在任务"))
            continue
        task = Task(
            source_url=url,
            owner_id=user.id,
            config=dict(cfg),
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
            task.status = TaskStatus.FAILED
            task.error_message = str(exc)[:2000]
            db.commit()
            for item in created:
                if item.task_id == task.id:
                    item.status = TaskStatus.FAILED
                    item.reason = str(exc)[:300]
                    break

    return BatchCreateTaskResponse(
        created=created,
        skipped=skipped,
        total_created=len(created),
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
    "/{task_id}/retry",
    response_model=CreateTaskResponse,
    summary="重新投递任务（卡 0% 或失败时使用，仅任务主人可操作）",
)
def retry_task(
    task_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CreateTaskResponse:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    # 重置状态
    task.status = TaskStatus.PENDING
    task.progress = 0
    task.current_step = "排队中（重试）"
    task.error_message = None
    db.commit()
    db.refresh(task)

    try:
        _dispatch_task(task)
    except Exception as e:
        logger.exception("[task %s] Celery 重新投递失败", task.id)
        task.status = TaskStatus.FAILED
        task.error_message = f"Celery dispatch failed: {e}"[:2000]
        db.commit()
        raise HTTPException(
            status_code=503,
            detail=f"重试失败 (task_id={task.id}): {e}",
        )

    return CreateTaskResponse(task_id=task.id, status=task.status)


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


def _read_task_markdown(db: Session, task: Task) -> str:
    cfg = task.config or {}
    md = cfg.get("markdown")
    if md:
        return normalize_final_markdown(str(md))

    chapters = (
        db.query(Chapter)
        .filter(Chapter.task_id == task.id)
        .order_by(Chapter.chapter_num.asc())
        .all()
    )
    return normalize_final_markdown("\n".join(f"@{c.title}\n{c.content}" for c in chapters))


def _safe_download_name(task: Task, fmt: str) -> str:
    title = task.title or f"book_{task.id}"
    safe = re.sub(r'[\\/:*?"<>|\r\n]+', "_", title).strip(" ._") or f"book_{task.id}"
    return f"{safe[:80]}.{fmt}"


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


def _markdown_to_docx(markdown: str) -> bytes:
    """Build a minimal .docx while preserving the Markdown delivery text verbatim."""
    paragraphs = []
    for line in normalize_final_markdown(markdown).splitlines():
        text = escape(line, {'"': "&quot;"})
        paragraphs.append(
            "<w:p><w:pPr><w:spacing w:before=\"0\" w:after=\"0\" "
            "w:line=\"240\" w:lineRule=\"auto\"/></w:pPr>"
            f"<w:r><w:t xml:space=\"preserve\">{text}</w:t></w:r></w:p>"
        )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
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
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as docx:
        docx.writestr("[Content_Types].xml", content_types)
        docx.writestr("_rels/.rels", rels)
        docx.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


@router.get(
    "",
    response_model=TaskListResponse,
    summary="任务列表 (分页, 按创建时间倒序)",
)
def list_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TaskListResponse:
    base = db.query(Task).filter(Task.owner_id == user.id)
    if status_filter:
        base = base.filter(Task.status == status_filter)

    total = base.with_entities(func.count(Task.id)).scalar() or 0
    rows = (
        base.order_by(Task.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = [_build_list_item(db, task) for task in rows]
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
    cover_path = PROJECT_ROOT / "backend" / "output" / f"task_{task_id}" / "cover.png"
    if not cover_path.exists():
        raise HTTPException(status_code=404, detail=f"cover of task {task_id} not found")
    return FileResponse(
        cover_path,
        media_type="image/png",
        headers={
            "Cache-Control": "private, max-age=3600",
            "Vary": "Authorization",
        },
    )


@router.post(
    "/{task_id}/cover/regenerate",
    summary="封面生成已暂时停用",
)
def regenerate_cover(
    task_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    raise HTTPException(status_code=410, detail="封面生成已暂时停用")


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
            if task is None:
                continue
            md = _read_task_markdown(db, task)
            if not md:
                continue
            if fmt == "docx":
                content = _markdown_to_docx(md)
                filename = _safe_download_name(task, "docx")
            else:
                content = normalize_final_markdown(md)
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
    fmt: str = Query("docx", pattern="^(md|docx)$"),
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
    if len(ids) > 100:
        raise HTTPException(status_code=400, detail="一次最多下载 100 本书稿")
    return _build_batch_download_response(ids, fmt=fmt, db=db, user=user)


@router.get(
    "/{task_id}/download",
    summary="下载最终稿 (md/docx)",
)
def download_markdown(
    task_id: UUID,
    fmt: str = Query("md", pattern="^(md|docx|txt)$"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    md = _read_task_markdown(db, task)
    if not md:
        raise HTTPException(status_code=404, detail="任务尚未产出任何内容")

    if fmt == "docx" or fmt == "txt":
        content = _markdown_to_docx(md)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        filename = _safe_download_name(task, "docx")
    else:
        content = normalize_final_markdown(md)
        media_type = "text/markdown; charset=utf-8"
        filename = _safe_download_name(task, "md")
    encoded_filename = quote(filename)
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{encoded_filename}"; '
                f"filename*=UTF-8''{encoded_filename}"
            )
        },
    )


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
