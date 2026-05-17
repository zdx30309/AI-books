"""Redis guard that prevents duplicate Celery dispatch for the same task."""
from __future__ import annotations

import logging

from app.utils.distributed_limiter import _redis_client

logger = logging.getLogger(__name__)


def _key(task_id: str) -> str:
    return f"video-plan:task-dispatch:{task_id}"


def acquire_task_dispatch_guard(task_id: str, *, ttl_seconds: int = 21600) -> bool:
    ttl = max(60, int(ttl_seconds or 21600))
    client = _redis_client()
    acquired = bool(client.set(_key(task_id), "queued", nx=True, ex=ttl))
    if not acquired:
        logger.info("[task %s] dispatch skipped: already queued or retry scheduled", task_id)
    return acquired


def clear_task_dispatch_guard(task_id: str) -> None:
    try:
        _redis_client().delete(_key(task_id))
    except Exception:
        logger.warning("[task %s] dispatch guard cleanup failed", task_id, exc_info=True)
