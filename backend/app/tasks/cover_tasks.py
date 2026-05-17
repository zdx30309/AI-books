"""Celery tasks for standalone Excel cover jobs."""
from __future__ import annotations

import logging
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Literal

from app.core.config import settings
from app.services.cover import (
    ImageApiPermanentError,
    ImageApiTransientError,
    generate_cover_file_from_metadata,
)
from app.services.cover_jobs import image_path, read_rows, read_status, recount_status, write_status
from app.utils.distributed_limiter import DistributedLimitError
from celery_app import celery_app

logger = logging.getLogger(__name__)

CoverItemResult = Literal["completed", "failed", "retry", "paused", "skipped", "superseded"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _recount(status: dict[str, Any]) -> None:
    recount_status(status)


def _visible_items(status: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in status.get("items") or [] if item.get("status") != "deleted"]


def _has_pending_work(status: dict[str, Any]) -> bool:
    return any(item.get("status") in {"pending", "processing"} for item in _visible_items(status))


def _has_incomplete_work(status: dict[str, Any]) -> bool:
    return any(item.get("status") in {"pending", "processing", "failed"} for item in _visible_items(status))


def _auto_retry_limit() -> int:
    return max(0, int(settings.COVER_ITEM_MAX_AUTO_RETRIES or 0))


def _short_error(exc: BaseException) -> str:
    return str(exc).strip()[:1000] or exc.__class__.__name__


def _summarize_processing(status: dict[str, Any], *, note: str | None = None) -> None:
    _recount(status)
    items = _visible_items(status)
    processing = sum(1 for item in items if item.get("status") == "processing")
    pending = sum(1 for item in items if item.get("status") == "pending")
    failed = sum(1 for item in items if item.get("status") == "failed")
    completed = int(status.get("completed") or 0)
    total = int(status.get("total") or 0)
    parts = [f"{completed}/{total} 完成"]
    if processing:
        parts.append(f"{processing} 张生成中")
    if pending:
        parts.append(f"{pending} 张排队")
    if failed:
        parts.append(f"{failed} 张失败")
    prefix = note or "正在并发生成封面"
    status["current_step"] = f"{prefix}：" + "，".join(parts)


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
        status["status"] = "pending"
        _summarize_processing(status, note="仍有封面等待自动重试")
        return
    status["progress"] = 100
    status["status"] = "completed" if failed == 0 else "failed"
    if failed:
        status["current_step"] = f"已完成 {completed} 张，失败 {failed} 张，可点击重试失败项"
    else:
        status["current_step"] = f"全部完成：{completed} 张封面"


def _stop_if_paused(job_id: str, status_lock: Lock) -> dict[str, Any] | None:
    with status_lock:
        latest = read_status(job_id)
        if latest.get("status") != "paused":
            return None
        for item in latest.get("items") or []:
            if item.get("status") == "processing":
                item["status"] = "pending"
                item["error"] = None
                item.pop("run_id", None)
        latest["active_run_id"] = None
        latest["current_step"] = "已暂停"
        _recount(latest)
        write_status(job_id, latest)
    return {"job_id": job_id, "status": "paused"}


def _prepare_job_run(job_id: str, run_id: str, total: int, status_lock: Lock) -> dict[str, Any]:
    with status_lock:
        status = read_status(job_id)
        if status.get("status") == "paused":
            return status
        for item in status.get("items") or []:
            if item.get("status") == "processing":
                item["status"] = "pending"
                item["error"] = None
                item.pop("run_id", None)
        status["active_run_id"] = run_id
        status["status"] = "processing"
        status["current_step"] = f"开始并发生成封面（最多 {total} 张同时进行）"
        _recount(status)
        write_status(job_id, status)
        return status


def _mark_processing(
    job_id: str,
    *,
    index: int,
    row: dict[str, Any],
    run_id: str,
    total: int,
    status_lock: Lock,
) -> tuple[CoverItemResult | None, str, Any]:
    with status_lock:
        latest = read_status(job_id)
        if latest.get("active_run_id") != run_id:
            return "superseded", "", None
        if latest.get("status") == "paused":
            return "paused", "", None
        items = list(latest.get("items") or [])
        if index > len(items):
            return "skipped", "", None
        item = items[index - 1]
        title = str(item.get("title") or row.get("title") or f"cover_{index}")
        path = image_path(job_id, index, title)
        if item.get("status") == "deleted":
            return "skipped", title, path
        if item.get("status") == "failed":
            return "skipped", title, path
        if item.get("status") == "completed" and path.exists() and path.stat().st_size > 0:
            return "skipped", title, path
        item.update(
            status="processing",
            error=None,
            started_at=_now_iso(),
            image_url=None,
            run_id=run_id,
        )
        latest["items"] = items
        latest["status"] = "processing"
        _summarize_processing(latest, note=f"正在并发生成第 {index}/{total} 张《{title}》")
        write_status(job_id, latest)
        return None, title, path


def _mark_retryable(
    job_id: str,
    *,
    index: int,
    run_id: str,
    exc: BaseException,
    status_lock: Lock,
) -> CoverItemResult:
    with status_lock:
        latest = read_status(job_id)
        items = list(latest.get("items") or [])
        if index > len(items):
            return "skipped"
        item = items[index - 1]
        if item.get("status") == "deleted":
            return "skipped"
        retry_count = int(item.get("auto_retry_count") or 0) + 1
        item["auto_retry_count"] = retry_count
        item["completed_at"] = None
        item["image_url"] = None
        item.pop("run_id", None)
        if retry_count > _auto_retry_limit():
            item["status"] = "failed"
            item["error"] = f"自动重试已达上限：{_short_error(exc)}"
            result: CoverItemResult = "failed"
            note = f"第 {index} 张自动重试达到上限"
        else:
            item["status"] = "pending"
            item["error"] = f"临时错误，等待自动重试（第 {retry_count} 次）：{_short_error(exc)}"
            result = "retry"
            note = f"第 {index} 张遇到临时错误，已回队列"
        latest["items"] = items
        if latest.get("active_run_id") == run_id and latest.get("status") != "paused":
            latest["status"] = "processing"
        _summarize_processing(latest, note=note)
        write_status(job_id, latest)
        return result


def _mark_failed(
    job_id: str,
    *,
    index: int,
    run_id: str,
    exc: BaseException,
    status_lock: Lock,
) -> CoverItemResult:
    with status_lock:
        latest = read_status(job_id)
        items = list(latest.get("items") or [])
        if index > len(items):
            return "skipped"
        item = items[index - 1]
        if item.get("status") == "deleted":
            return "skipped"
        item.update(
            status="failed",
            error=_short_error(exc),
            completed_at=_now_iso(),
            image_url=None,
        )
        item.pop("run_id", None)
        latest["items"] = items
        if latest.get("active_run_id") == run_id and latest.get("status") != "paused":
            latest["status"] = "processing"
        _summarize_processing(latest, note=f"第 {index} 张生成失败，继续其他封面")
        write_status(job_id, latest)
        return "failed"


def _mark_completed_or_deleted(
    job_id: str,
    *,
    index: int,
    title: str,
    path: Any,
    run_id: str,
    total: int,
    status_lock: Lock,
) -> CoverItemResult:
    with status_lock:
        latest = read_status(job_id)
        if latest.get("active_run_id") != run_id:
            return "superseded"
        latest_items = list(latest.get("items") or [])
        if index > len(latest_items):
            return "skipped"
        item = latest_items[index - 1]
        if item.get("status") == "deleted":
            if path.exists():
                path.unlink()
            work_dir = path.parent / f".{path.stem}_work"
            if work_dir.exists():
                shutil.rmtree(work_dir)
            return "skipped"
        item.update(
            status="completed",
            error=None,
            completed_at=_now_iso(),
            image_url=f"/api/v1/covers/jobs/{job_id}/items/{index}/image",
        )
        item.pop("run_id", None)
        latest["items"] = latest_items
        if latest.get("status") == "paused":
            _summarize_processing(latest, note="已暂停")
            write_status(job_id, latest)
            return "paused"
        latest["status"] = "processing"
        _summarize_processing(latest, note=f"第 {index}/{total} 张《{title}》已完成")
        write_status(job_id, latest)
        return "completed"


def _generate_one_cover(
    job_id: str,
    *,
    index: int,
    row: dict[str, Any],
    run_id: str,
    total: int,
    status_lock: Lock,
) -> CoverItemResult:
    result, title, path = _mark_processing(
        job_id,
        index=index,
        row=row,
        run_id=run_id,
        total=total,
        status_lock=status_lock,
    )
    if result is not None:
        return result

    try:
        generate_cover_file_from_metadata(row, path)
    except (DistributedLimitError, ImageApiTransientError) as exc:
        logger.warning("[cover job %s] item %s will retry: %s", job_id, index, exc)
        return _mark_retryable(job_id, index=index, run_id=run_id, exc=exc, status_lock=status_lock)
    except ImageApiPermanentError as exc:
        logger.exception("[cover job %s] item %s permanent image error", job_id, index)
        return _mark_failed(job_id, index=index, run_id=run_id, exc=exc, status_lock=status_lock)
    except Exception as exc:
        logger.exception("[cover job %s] item %s failed", job_id, index)
        return _mark_failed(job_id, index=index, run_id=run_id, exc=exc, status_lock=status_lock)

    return _mark_completed_or_deleted(
        job_id,
        index=index,
        title=title,
        path=path,
        run_id=run_id,
        total=total,
        status_lock=status_lock,
    )


@celery_app.task(
    bind=True,
    name="tasks.generate_cover_job",
    max_retries=settings.COVER_TASK_MAX_RETRIES,
)
def generate_cover_job(_self, job_id: str) -> dict[str, Any]:
    """Generate covers from one uploaded spreadsheet.

    One job now advances multiple covers in parallel, while each paid image
    request is still protected by the global Redis image semaphore. Transient
    image/API failures are returned to the pending queue so draft artifacts can
    be reused instead of burning the whole three-step workflow again.
    """
    status = read_status(job_id)
    rows = read_rows(job_id)
    if status.get("status") == "paused":
        return {"job_id": job_id, "status": "paused"}
    if status.get("status") == "completed" and not _has_pending_work(status):
        return {"job_id": job_id, "status": "completed"}

    run_id = uuid.uuid4().hex
    status_lock = Lock()
    max_workers = max(
        1,
        min(
            int(settings.COVER_JOB_MAX_CONCURRENT_ITEMS or 1),
            int(settings.IMAGE_MAX_CONCURRENT_CALLS or 1),
            len(rows) or 1,
        ),
    )
    _prepare_job_run(job_id, run_id, max_workers, status_lock)

    paused_result = _stop_if_paused(job_id, status_lock)
    if paused_result is not None:
        return paused_result

    work: list[tuple[int, dict[str, Any]]] = []
    with status_lock:
        status = read_status(job_id)
        items = list(status.get("items") or [])
        for index, row in enumerate(rows, start=1):
            if index > len(items):
                break
            item = items[index - 1]
            title = str(item.get("title") or row.get("title") or f"cover_{index}")
            path = image_path(job_id, index, title)
            if item.get("status") == "deleted":
                continue
            if item.get("status") == "failed":
                continue
            if item.get("status") == "completed" and path.exists() and path.stat().st_size > 0:
                continue
            work.append((index, row))

    if not work:
        with status_lock:
            status = read_status(job_id)
            if status.get("active_run_id") == run_id:
                status["active_run_id"] = None
                _finish_status(status)
                write_status(job_id, status)
        return {"job_id": job_id, "status": read_status(job_id).get("status")}

    retry_needed = False
    paused = False
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=f"cover-{job_id[:6]}") as executor:
        futures = [
            executor.submit(
                _generate_one_cover,
                job_id,
                index=index,
                row=row,
                run_id=run_id,
                total=len(rows),
                status_lock=status_lock,
            )
            for index, row in work
        ]
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as exc:
                logger.exception("[cover job %s] worker thread failed", job_id)
                result = "retry" if isinstance(exc, (DistributedLimitError, ImageApiTransientError)) else "failed"
            if result == "retry":
                retry_needed = True
            elif result == "paused":
                paused = True

    with status_lock:
        status = read_status(job_id)
        if status.get("active_run_id") == run_id:
            status["active_run_id"] = None
        for item in status.get("items") or []:
            if item.get("status") == "processing":
                item["status"] = "pending"
                item.pop("run_id", None)
        if status.get("status") == "paused" or paused:
            status["status"] = "paused"
            status["current_step"] = "已暂停"
            _recount(status)
            write_status(job_id, status)
            return {"job_id": job_id, "status": "paused"}

        pending_exists = _has_pending_work(status)
        if pending_exists:
            status["status"] = "pending"
            _summarize_processing(
                status,
                note="临时错误或新重试项已回队列，等待自动继续",
            )
            write_status(job_id, status)
        else:
            _finish_status(status)
            write_status(job_id, status)

    if retry_needed or read_status(job_id).get("status") == "pending":
        current_retries = int(getattr(getattr(_self, "request", None), "retries", 0) or 0)
        max_retries = max(0, int(settings.COVER_TASK_MAX_RETRIES or 0))
        if current_retries >= max_retries:
            with status_lock:
                status = read_status(job_id)
                for item in status.get("items") or []:
                    if item.get("status") == "pending":
                        item["status"] = "failed"
                        item["error"] = "封面自动重试达到任务上限，请手动重试失败项"
                _finish_status(status)
                write_status(job_id, status)
            return {"job_id": job_id, "status": read_status(job_id).get("status"), "reason": "retry_exhausted"}
        raise _self.retry(
            countdown=max(10, int(settings.COVER_TASK_RETRY_SECONDS or 60)),
            exc=RuntimeError("cover job has pending retry items"),
        )

    final = read_status(job_id)
    return {
        "job_id": job_id,
        "status": final.get("status"),
        "completed": final.get("completed"),
        "failed": final.get("failed"),
        "incomplete": _has_incomplete_work(final),
    }


__all__ = ["generate_cover_job"]
