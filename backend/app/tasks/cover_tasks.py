"""Celery tasks for standalone Excel cover jobs."""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.services.cover import generate_cover_file_from_metadata
from app.services.cover_jobs import image_path, read_rows, read_status, recount_status, write_status
from app.utils.distributed_limiter import DistributedLimitError
from celery_app import celery_app

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _recount(status: dict[str, Any]) -> None:
    recount_status(status)


def _finish_status(status: dict[str, Any]) -> None:
    _recount(status)
    total = int(status.get("total") or 0)
    completed = int(status.get("completed") or 0)
    failed = int(status.get("failed") or 0)
    if total <= 0:
        status["progress"] = 100
        status["status"] = "completed"
        status["current_step"] = "没有待生成封面"
        return
    if completed + failed < total:
        status["status"] = "processing"
        return
    status["progress"] = 100
    status["status"] = "completed" if completed > 0 else "failed"
    if failed:
        status["current_step"] = f"已完成 {completed} 张，失败 {failed} 张"
    else:
        status["current_step"] = f"全部完成：{completed} 张封面"


def _stop_if_paused(job_id: str) -> dict[str, Any] | None:
    latest = read_status(job_id)
    if latest.get("status") != "paused":
        return None
    for item in latest.get("items") or []:
        if item.get("status") == "processing":
            item["status"] = "pending"
            item["error"] = None
    latest["current_step"] = "已暂停"
    _recount(latest)
    write_status(job_id, latest)
    return {"job_id": job_id, "status": "paused"}


@celery_app.task(
    bind=True,
    name="tasks.generate_cover_job",
    max_retries=settings.COVER_TASK_MAX_RETRIES,
)
def generate_cover_job(_self, job_id: str) -> dict[str, Any]:
    """Generate covers from one uploaded spreadsheet.

    Paid image API failures are recorded on the item and are not retried here.
    Queue/concurrency-limit waits are retried before a paid request is sent.
    A paused job stops before the next paid call.
    """
    status = read_status(job_id)
    rows = read_rows(job_id)
    if status.get("status") == "completed":
        return {"job_id": job_id, "status": "completed"}
    if status.get("status") == "paused":
        return {"job_id": job_id, "status": "paused"}

    status["status"] = "processing"
    status["current_step"] = "开始生成封面"
    write_status(job_id, status)

    for index, row in enumerate(rows, start=1):
        paused_result = _stop_if_paused(job_id)
        if paused_result is not None:
            return paused_result

        status = read_status(job_id)
        items = list(status.get("items") or [])
        if index > len(items):
            break
        item = items[index - 1]
        title = str(item.get("title") or row.get("title") or f"cover_{index}")
        path = image_path(job_id, index, title)

        if item.get("status") == "deleted":
            continue
        if item.get("status") == "completed" and path.exists() and path.stat().st_size > 0:
            continue
        if item.get("status") == "failed":
            continue

        item.update(
            status="processing",
            error=None,
            started_at=_now_iso(),
            image_url=None,
        )
        status["items"] = items
        status["status"] = "processing"
        status["current_step"] = f"正在生成第 {index}/{len(rows)} 张：{title}"
        _recount(status)
        write_status(job_id, status)

        paused_result = _stop_if_paused(job_id)
        if paused_result is not None:
            return paused_result

        try:
            generate_cover_file_from_metadata(row, path)
        except DistributedLimitError as exc:
            item.update(status="pending", error=None)
            status["items"] = items
            status["status"] = "pending"
            status["current_step"] = "图片并发已满，等待自动重试"
            _recount(status)
            write_status(job_id, status)
            current_retries = int(getattr(getattr(_self, "request", None), "retries", 0) or 0)
            max_retries = max(0, int(settings.COVER_TASK_MAX_RETRIES or 0))
            if current_retries >= max_retries:
                item.update(status="failed", error=f"封面排队超时：{exc}"[:1000])
                status["items"] = items
                _finish_status(status)
                write_status(job_id, status)
                return {"job_id": job_id, "status": status.get("status"), "reason": "queue_timeout"}
            raise _self.retry(exc=exc, countdown=max(10, int(settings.COVER_TASK_RETRY_SECONDS or 60)))
        except Exception as exc:
            logger.exception("[cover job %s] item %s failed", job_id, index)
            item.update(
                status="failed",
                error=str(exc)[:1000],
                completed_at=_now_iso(),
                image_url=None,
            )
            status["items"] = items
            status["current_step"] = f"第 {index}/{len(rows)} 张生成失败，继续下一张"
            _recount(status)
            write_status(job_id, status)
            continue

        latest = read_status(job_id)
        latest_items = list(latest.get("items") or [])
        target_items = latest_items if latest_items else items
        if index <= len(target_items):
            if target_items[index - 1].get("status") == "deleted":
                if path.exists():
                    path.unlink()
                work_dir = path.parent / f".{path.stem}_work"
                if work_dir.exists():
                    shutil.rmtree(work_dir)
            else:
                target_items[index - 1].update(
                    status="completed",
                    error=None,
                    completed_at=_now_iso(),
                    image_url=f"/api/v1/covers/jobs/{job_id}/items/{index}/image",
                )
        latest["items"] = target_items
        if latest.get("status") == "paused":
            latest["current_step"] = "已暂停"
            _recount(latest)
            write_status(job_id, latest)
            return {"job_id": job_id, "status": "paused", "completed_current_item": True}

        latest["current_step"] = f"第 {index}/{len(rows)} 张已完成"
        _recount(latest)
        write_status(job_id, latest)

    status = read_status(job_id)
    if status.get("status") == "paused":
        return {"job_id": job_id, "status": "paused"}
    _finish_status(status)
    write_status(job_id, status)
    return {
        "job_id": job_id,
        "status": status.get("status"),
        "completed": status.get("completed"),
        "failed": status.get("failed"),
    }


__all__ = ["generate_cover_job"]
