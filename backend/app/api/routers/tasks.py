"""Task routers."""
from __future__ import annotations

import logging
import re
import zipfile
from io import BytesIO
from pathlib import Path
from urllib.parse import quote
from uuid import UUID
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import PROJECT_ROOT
from app.api.routers.auth import get_current_user
from app.db.session import get_db
from app.models import Chapter, Task, TaskStatus, User
from app.services.content_engine import normalize_final_markdown
from app.schemas.chapter import (
    ChapterResponse,
    ChapterUpdateRequest,
    ChaptersListResponse,
)
from app.schemas.task import (
    CreateTaskRequest,
    CreateTaskResponse,
    MarkdownResponse,
    StageContentResponse,
    TaskListItem,
    TaskListResponse,
    TaskStatusResponse,
)
from app.tasks.tasks import process_video

_STAGE_FILES = {
    "writer": "01_writer.md",
    "writer_repaired": "01b_writer_repaired.md",
    "polish": "02_polish.md",
    "refine": "03_refine.md",
    "format": "04_format.md",
}

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tasks", tags=["Tasks"])


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
    task = Task(
        source_url=req.url,
        owner_id=user.id,
        config=req.config or {},
        status=TaskStatus.PENDING,
        progress=0,
        current_step="排队中",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    logger.info("[task %s] 建库完成, 准备投 Celery", task.id)

    try:
        process_video.delay(str(task.id))
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
        process_video.delay(str(task.id))
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
