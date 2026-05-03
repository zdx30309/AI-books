"""聚合统计接口，用于前端工作台头部数据卡。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.routers.auth import get_current_user
from app.db.session import get_db
from app.models import Chapter, Task, TaskStatus, User
from app.schemas.task import StatsResponse

router = APIRouter(prefix="/api/v1/stats", tags=["Stats"])

_HUMAN_WORDS_PER_HOUR = 1500


@router.get(
    "",
    response_model=StatsResponse,
    summary="工作台聚合统计",
)
def get_stats(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StatsResponse:
    completed = (
        db.query(func.count(Task.id))
        .filter(Task.owner_id == user.id, Task.status == TaskStatus.COMPLETED)
        .scalar() or 0
    )
    in_progress = (
        db.query(func.count(Task.id))
        .filter(
            Task.owner_id == user.id,
            Task.status.in_(
                [
                    TaskStatus.PENDING,
                    TaskStatus.PROCESSING,
                    TaskStatus.FETCHING_TOPICS,
                    TaskStatus.FILTERING_TOPICS,
                    TaskStatus.GENERATING_OUTLINE,
                    TaskStatus.WRITING_CHAPTERS,
                    TaskStatus.FACT_CHECKING,
                ]
            )
        )
        .scalar() or 0
    )
    failed = (
        db.query(func.count(Task.id))
        .filter(Task.owner_id == user.id, Task.status == TaskStatus.FAILED)
        .scalar() or 0
    )
    total_words = (
        db.query(func.coalesce(func.sum(Chapter.word_count), 0))
        .join(Task, Chapter.task_id == Task.id)
        .filter(Task.owner_id == user.id, Task.status == TaskStatus.COMPLETED)
        .scalar() or 0
    )

    saved_hours = round(total_words / _HUMAN_WORDS_PER_HOUR, 1) if total_words else 0.0

    return StatsResponse(
        total_books=int(completed),
        total_words=int(total_words),
        saved_hours=float(saved_hours),
        in_progress=int(in_progress),
        failed=int(failed),
    )
