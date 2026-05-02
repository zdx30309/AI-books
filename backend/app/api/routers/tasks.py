"""Task routers."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, PlainTextResponse, Response
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import PROJECT_ROOT, settings
from app.api.routers.auth import get_current_user
from app.db.session import get_db
from app.models import Chapter, Task, TaskStatus, User
from app.services.cover import generate_cover_file
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
from app.tasks.tasks import _extract_book_title, _patch_config

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
        return str(md)

    chapters = (
        db.query(Chapter)
        .filter(Chapter.task_id == task.id)
        .order_by(Chapter.chapter_num.asc())
        .all()
    )
    return "\n\n".join(f"@{c.title}\n\n{c.content}" for c in chapters)


def _safe_download_name(task: Task, fmt: str) -> str:
    title = task.title or f"book_{task.id}"
    safe = re.sub(r'[\\/:*?"<>|\r\n]+', "_", title).strip(" ._") or f"book_{task.id}"
    return f"{safe[:80]}.{fmt}"


def _markdown_to_plain_text(markdown: str) -> str:
    """Convert the final delivery markdown to reader-friendly plain text."""
    text = markdown.replace("\r\n", "\n")
    text = re.sub(r"^@\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return text.strip() + "\n"


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
def get_cover(task_id: UUID) -> FileResponse:
    cover_path = PROJECT_ROOT / "backend" / "output" / f"task_{task_id}" / "cover.png"
    if not cover_path.exists():
        raise HTTPException(status_code=404, detail=f"cover of task {task_id} not found")
    return FileResponse(
        cover_path,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.post(
    "/{task_id}/cover/regenerate",
    response_model=TaskStatusResponse,
    summary="重新生成任务封面（仅任务主人可操作）",
)
def regenerate_cover(
    task_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TaskStatusResponse:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    markdown = _read_task_markdown(db, task)
    if not markdown:
        raise HTTPException(status_code=404, detail="任务尚未产出任何内容，无法生成封面")

    title = task.title or _extract_book_title(markdown)
    output_dir = PROJECT_ROOT / "backend" / "output" / f"task_{task.id}"
    try:
        cover_path = generate_cover_file(title, markdown, output_dir)
    except Exception as exc:
        logger.exception("[task %s] 重新生成封面失败", task.id)
        _patch_config(task, cover_error=f"{type(exc).__name__}: {exc}"[:1000])
        db.commit()
        raise HTTPException(status_code=502, detail=f"封面生成失败: {exc}") from exc

    task.cover_url = f"/api/v1/tasks/{task.id}/cover"
    _patch_config(
        task,
        title=title,
        cover_path=str(cover_path),
        cover_model=settings.IMAGE_MODEL,
        cover_error=None,
    )
    db.commit()
    db.refresh(task)
    return TaskStatusResponse.model_validate(task)


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
    summary="下载最终稿 (md/txt)",
    response_class=PlainTextResponse,
)
def download_markdown(
    task_id: UUID,
    fmt: str = Query("md", pattern="^(md|txt)$"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    md = _read_task_markdown(db, task)
    if not md:
        raise HTTPException(status_code=404, detail="任务尚未产出任何内容")

    content = md if fmt == "md" else _markdown_to_plain_text(md)
    media_type = "text/markdown" if fmt == "md" else "text/plain"
    filename = _safe_download_name(task, fmt)
    encoded_filename = quote(filename)
    return Response(
        content=content,
        media_type=f"{media_type}; charset=utf-8",
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
