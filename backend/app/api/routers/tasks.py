"""Task routers."""
from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models import Chapter, Task, TaskStatus
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
) -> CreateTaskResponse:
    task = Task(
        source_url=req.url,
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
) -> TaskListResponse:
    base = db.query(Task)
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
) -> TaskStatusResponse:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return TaskStatusResponse.model_validate(task)


@router.get(
    "/{task_id}/chapters",
    response_model=ChaptersListResponse,
    summary="返回该任务下所有已生成的章节",
)
def get_chapters(
    task_id: UUID,
    db: Session = Depends(get_db),
) -> ChaptersListResponse:
    task = db.get(Task, task_id)
    if task is None:
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
) -> ChapterResponse:
    chapter = (
        db.query(Chapter)
        .filter(Chapter.task_id == task_id, Chapter.chapter_num == chapter_num)
        .first()
    )
    if chapter is None:
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
) -> MarkdownResponse:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    cfg = task.config or {}
    md = cfg.get("markdown")
    if not md:
        # 兜底：从章节拼接
        chapters = (
            db.query(Chapter)
            .filter(Chapter.task_id == task_id)
            .order_by(Chapter.chapter_num.asc())
            .all()
        )
        if not chapters:
            raise HTTPException(status_code=404, detail="任务尚未产出任何内容")
        md = "\n\n".join(f"@{c.title}\n\n{c.content}" for c in chapters)

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
) -> Response:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    cfg = task.config or {}
    md = cfg.get("markdown") or ""
    if not md:
        chapters = (
            db.query(Chapter)
            .filter(Chapter.task_id == task_id)
            .order_by(Chapter.chapter_num.asc())
            .all()
        )
        md = "\n\n".join(f"@{c.title}\n\n{c.content}" for c in chapters)
    if not md:
        raise HTTPException(status_code=404, detail="任务尚未产出任何内容")

    media_type = "text/markdown" if fmt == "md" else "text/plain"
    filename = f"book_{task_id}.{fmt}"
    return Response(
        content=md,
        media_type=f"{media_type}; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
) -> StageContentResponse:
    if stage not in _STAGE_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"未知 stage: {stage}，合法值: {list(_STAGE_FILES)}",
        )
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    file_path: Path = settings.PROJECT_ROOT / "backend" / "output" / f"task_{task_id}" / _STAGE_FILES[stage]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"stage {stage} 文件未生成")

    content = file_path.read_text(encoding="utf-8")
    return StageContentResponse(
        task_id=task_id,
        stage=stage,
        content=content,
        char_count=len(content),
    )
