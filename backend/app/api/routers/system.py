"""系统级健康检查：探测 Celery worker 是否能消费队列。"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from app.tasks.system_tasks import ping_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/system", tags=["System"])


class SystemHealth(BaseModel):
    celery_alive: bool
    celery_workers: list[str]
    celery_error: str | None = None
    queue_lengths: dict[str, int] | None = None


@router.get(
    "/health",
    response_model=SystemHealth,
    summary="探测 Celery worker / 队列状态（前端在任务卡 0% 时可调用以定位问题）",
)
def system_health() -> SystemHealth:
    workers: list[str] = []
    err: str | None = None
    try:
        result = ping_task.delay("health")
        payload = result.get(timeout=6, propagate=True)
        if isinstance(payload, str) and payload.startswith("Pong!"):
            workers.append("celery")
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        logger.warning("celery health task 失败: %s", err)

    return SystemHealth(
        celery_alive=bool(workers),
        celery_workers=workers,
        celery_error=err,
    )
