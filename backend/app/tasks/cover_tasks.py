"""Celery tasks for standalone Excel cover jobs."""
from __future__ import annotations

import logging
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Literal

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.cover_batch import CoverBatchItem, CoverBatchItemStatus, CoverBatchStatus
from app.services.cover import (
    CoverGenerationCancelled,
    ImageApiPermanentError,
    ImageApiTransientError,
    ImageApiUncertainError,
    generate_cover_file_from_metadata,
    observe_image_requests,
)
from app.services import cover_batch_jobs
from app.services.cover_batch_jobs import (
    aggregate_cover_batch_job,
    claim_ready_cover_items,
    has_ready_cover_items,
    mark_cover_item_completed,
    mark_cover_item_failed,
    mark_cover_item_progress,
    mark_cover_item_retryable,
    mark_cover_item_uncertain,
    next_retry_countdown,
    record_provider_request_event,
    release_expired_cover_leases,
    should_abort_cover_item,
)
from app.services.cover_jobs import (
    COVER_JOBS_ROOT,
    image_path,
    read_rows,
    read_status,
    recount_status,
    write_status,
)
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


def _retry_delay_seconds() -> int:
    return max(10, int(settings.COVER_TASK_RETRY_SECONDS or 60))


def _retry_after(item: dict[str, Any]) -> datetime | None:
    return _parse_iso_datetime(item.get("retry_after"))


def _is_retry_waiting(item: dict[str, Any], now: datetime) -> bool:
    retry_after = _retry_after(item)
    return item.get("status") == "pending" and retry_after is not None and retry_after > now


def _has_ready_pending_work(status: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    for item in _visible_items(status):
        if item.get("status") == "processing":
            return True
        if item.get("status") == "pending" and not _is_retry_waiting(item, now):
            return True
    return False


def _next_retry_countdown(status: dict[str, Any], now: datetime | None = None) -> int | None:
    now = now or datetime.now(timezone.utc)
    retry_times = [
        retry_after
        for item in _visible_items(status)
        if item.get("status") == "pending"
        for retry_after in [_retry_after(item)]
        if retry_after is not None and retry_after > now
    ]
    if not retry_times:
        return None
    return max(10, int((min(retry_times) - now).total_seconds()))


def _has_incomplete_work(status: dict[str, Any]) -> bool:
    return any(item.get("status") in {"pending", "processing", "failed"} for item in _visible_items(status))


def _auto_retry_limit() -> int:
    return max(0, int(settings.COVER_ITEM_MAX_AUTO_RETRIES or 0))


def _short_error(exc: BaseException) -> str:
    return str(exc).strip()[:1000] or exc.__class__.__name__


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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
                item.pop("current_stage", None)
                item.pop("stage_started_at", None)
                item.pop("last_heartbeat_at", None)
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
                item.pop("current_stage", None)
                item.pop("stage_started_at", None)
                item.pop("last_heartbeat_at", None)
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
) -> tuple[CoverItemResult | None, str, Any, bool]:
    with status_lock:
        latest = read_status(job_id)
        if latest.get("active_run_id") != run_id:
            return "superseded", "", None, False
        if latest.get("status") == "paused":
            return "paused", "", None, False
        items = list(latest.get("items") or [])
        if index > len(items):
            return "skipped", "", None, False
        item = items[index - 1]
        title = str(item.get("title") or row.get("title") or f"cover_{index}")
        path = image_path(job_id, index, title)
        if item.get("status") == "deleted":
            return "skipped", title, path, False
        if item.get("status") == "failed":
            return "skipped", title, path, False
        if item.get("status") == "completed" and path.exists() and path.stat().st_size > 0:
            return "skipped", title, path, False
        force_regenerate = bool(item.pop("force_regenerate", False))
        item.pop("retry_after", None)
        item.update(
            status="processing",
            error=None,
            started_at=_now_iso(),
            stage_started_at=_now_iso(),
            last_heartbeat_at=_now_iso(),
            current_stage="preparing",
            image_url=None,
            run_id=run_id,
        )
        latest["items"] = items
        latest["status"] = "processing"
        _summarize_processing(latest, note=f"正在并发生成第 {index}/{total} 张《{title}》")
        write_status(job_id, latest)
        return None, title, path, force_regenerate


def _mark_item_progress(
    job_id: str,
    *,
    index: int,
    run_id: str,
    stage: str,
    status_lock: Lock,
) -> None:
    with status_lock:
        try:
            latest = read_status(job_id)
        except FileNotFoundError:
            return
        if latest.get("active_run_id") != run_id:
            return
        items = list(latest.get("items") or [])
        if index > len(items):
            return
        item = items[index - 1]
        if item.get("status") != "processing" or item.get("run_id") != run_id:
            return
        now = _now_iso()
        item["current_stage"] = stage
        item["stage_started_at"] = now
        item["last_heartbeat_at"] = now
        latest["items"] = items
        stage_label = {
            "preparing": "准备封面参考与提示词",
            "draft": "OpenAI 生成初稿",
            "refine": "OpenAI 精修封面",
            "copy": "保存最终封面",
        }.get(stage, stage)
        _summarize_processing(latest, note=f"第 {index} 张正在{stage_label}")
        write_status(job_id, latest)


def _mark_retryable(
    job_id: str,
    *,
    index: int,
    run_id: str,
    exc: BaseException,
    status_lock: Lock,
) -> CoverItemResult:
    with status_lock:
        try:
            latest = read_status(job_id)
        except FileNotFoundError:
            logger.info("[cover job %s] item %s skipped because job was deleted", job_id, index)
            return "skipped"
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
        item.pop("current_stage", None)
        item.pop("stage_started_at", None)
        item.pop("last_heartbeat_at", None)
        if retry_count > _auto_retry_limit():
            item["status"] = "failed"
            item["error"] = f"自动重试已达上限：{_short_error(exc)}"
            item.pop("retry_after", None)
            result: CoverItemResult = "failed"
            note = f"第 {index} 张自动重试达到上限"
        else:
            item["status"] = "pending"
            item["retry_after"] = (datetime.now(timezone.utc) + timedelta(seconds=_retry_delay_seconds())).isoformat()
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
        try:
            latest = read_status(job_id)
        except FileNotFoundError:
            logger.info("[cover job %s] item %s skipped because job was deleted", job_id, index)
            return "skipped"
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
        item.pop("current_stage", None)
        item.pop("stage_started_at", None)
        item.pop("last_heartbeat_at", None)
        item.pop("retry_after", None)
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
        try:
            latest = read_status(job_id)
        except FileNotFoundError:
            logger.info("[cover job %s] item %s completed late after job deletion; skipping", job_id, index)
            return "skipped"
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
        item.pop("current_stage", None)
        item.pop("stage_started_at", None)
        item.pop("last_heartbeat_at", None)
        item.pop("retry_after", None)
        latest["items"] = latest_items
        if latest.get("status") == "paused":
            _summarize_processing(latest, note="已暂停")
            write_status(job_id, latest)
            return "paused"
        latest["status"] = "processing"
        _summarize_processing(latest, note=f"第 {index}/{total} 张《{title}》已完成")
        write_status(job_id, latest)
        return "completed"


def _should_abort_cover_item(job_id: str, *, index: int, run_id: str, status_lock: Lock) -> bool:
    with status_lock:
        try:
            latest = read_status(job_id)
        except FileNotFoundError:
            return True
        if latest.get("active_run_id") != run_id:
            return True
        if latest.get("status") == "paused":
            return True
        items = list(latest.get("items") or [])
        if index > len(items):
            return True
        item = items[index - 1]
        if item.get("status") == "deleted":
            return True
        if item.get("run_id") != run_id:
            return True
        return item.get("status") != "processing"


def _generate_one_cover(
    job_id: str,
    *,
    index: int,
    row: dict[str, Any],
    run_id: str,
    total: int,
    status_lock: Lock,
) -> CoverItemResult:
    result, title, path, force_regenerate = _mark_processing(
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
        generate_cover_file_from_metadata(
            row,
            path,
            force=force_regenerate,
            should_abort=lambda: _should_abort_cover_item(
                job_id,
                index=index,
                run_id=run_id,
                status_lock=status_lock,
            ),
            on_progress=lambda stage: _mark_item_progress(
                job_id,
                index=index,
                run_id=run_id,
                stage=stage,
                status_lock=status_lock,
            ),
        )
    except CoverGenerationCancelled as exc:
        logger.info("[cover job %s] item %s cancelled: %s", job_id, index, exc)
        return "paused" if "暂停" in str(exc) else "skipped"
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
        now = datetime.now(timezone.utc)
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
            if _is_retry_waiting(item, now):
                continue
            work.append((index, row))

    if not work:
        schedule_countdown: int | None = None
        with status_lock:
            status = read_status(job_id)
            if status.get("active_run_id") == run_id:
                status["active_run_id"] = None
                if _has_pending_work(status) and not _has_ready_pending_work(status):
                    schedule_countdown = _next_retry_countdown(status) or _retry_delay_seconds()
                    status["status"] = "pending"
                    _summarize_processing(status, note="临时错误封面等待自动重试")
                else:
                    _finish_status(status)
                write_status(job_id, status)
        if schedule_countdown is not None:
            generate_cover_job.apply_async(args=(job_id,), countdown=schedule_countdown)
            return {
                "job_id": job_id,
                "status": read_status(job_id).get("status"),
                "incomplete": True,
                "reason": "retry_wait_scheduled",
                "countdown": schedule_countdown,
            }
        return {"job_id": job_id, "status": read_status(job_id).get("status")}

    max_items_per_run = max(
        max_workers,
        int(settings.COVER_JOB_MAX_ITEMS_PER_RUN or max_workers),
    )
    work = work[:max_items_per_run]

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
            if result == "paused":
                paused = True

    with status_lock:
        try:
            status = read_status(job_id)
        except FileNotFoundError:
            return {"job_id": job_id, "status": "deleted"}
        if status.get("active_run_id") == run_id:
            status["active_run_id"] = None
        for item in status.get("items") or []:
            if item.get("status") == "processing":
                item["status"] = "pending"
                item.pop("run_id", None)
                item.pop("current_stage", None)
                item.pop("stage_started_at", None)
                item.pop("last_heartbeat_at", None)
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

    latest = read_status(job_id)
    if latest.get("status") == "pending":
        if _has_ready_pending_work(latest):
            generate_cover_job.delay(job_id)
            return {
                "job_id": job_id,
                "status": read_status(job_id).get("status"),
                "incomplete": True,
                "reason": "next_batch_dispatched",
            }
        schedule_countdown = _next_retry_countdown(latest) or _retry_delay_seconds()
        generate_cover_job.apply_async(args=(job_id,), countdown=schedule_countdown)
        return {
            "job_id": job_id,
            "status": latest.get("status"),
            "incomplete": True,
            "reason": "retry_wait_scheduled",
            "countdown": schedule_countdown,
        }

    final = read_status(job_id)
    return {
        "job_id": job_id,
        "status": final.get("status"),
        "completed": final.get("completed"),
        "failed": final.get("failed"),
        "incomplete": _has_incomplete_work(final),
    }


def _cover_batch_item_path(item: CoverBatchItem) -> Any:
    return cover_batch_jobs.image_path(item.job_id, int(item.item_index), item.title)


def _generate_one_cover_batch_item(job_id: str, item_id: str, worker_id: str) -> CoverItemResult:
    db = SessionLocal()
    item_uuid = uuid.UUID(str(item_id))
    try:
        item = db.get(CoverBatchItem, item_uuid)
        if item is None or item.status == CoverBatchItemStatus.DELETED:
            return "skipped"
        metadata = dict(item.source_metadata or {})
        force_regenerate = bool(metadata.pop("__force_regenerate_once", False))
        if force_regenerate:
            item.source_metadata = metadata
            db.commit()
        output_path = _cover_batch_item_path(item)

        def _abort() -> bool:
            check_db = SessionLocal()
            try:
                return should_abort_cover_item(check_db, item_uuid, worker_id)
            finally:
                check_db.close()

        def _progress(stage: str) -> None:
            progress_db = SessionLocal()
            try:
                mark_cover_item_progress(progress_db, item_uuid, worker_id, stage)
            finally:
                progress_db.close()

        def _provider_event(event: dict[str, Any]) -> None:
            event_db = SessionLocal()
            try:
                record_provider_request_event(
                    event_db,
                    job_id=job_id,
                    item_id=item_uuid,
                    event=event,
                )
            finally:
                event_db.close()

        with observe_image_requests(_provider_event):
            generate_cover_file_from_metadata(
                metadata,
                output_path,
                force=force_regenerate,
                should_abort=_abort,
                on_progress=_progress,
            )
    except CoverGenerationCancelled as exc:
        logger.info("[cover batch %s] item %s cancelled: %s", job_id, item_id, exc)
        return "paused" if "paused" in str(exc).lower() else "skipped"
    except ImageApiUncertainError as exc:
        logger.warning("[cover batch %s] item %s uncertain image result: %s", job_id, item_id, exc)
        mark_db = SessionLocal()
        try:
            mark_cover_item_uncertain(mark_db, item_uuid, exc)
        finally:
            mark_db.close()
        return "failed"
    except (DistributedLimitError, ImageApiTransientError) as exc:
        logger.warning("[cover batch %s] item %s will retry: %s", job_id, item_id, exc)
        mark_db = SessionLocal()
        try:
            return mark_cover_item_retryable(mark_db, item_uuid, exc)
        finally:
            mark_db.close()
    except ImageApiPermanentError as exc:
        logger.exception("[cover batch %s] item %s permanent image error", job_id, item_id)
        mark_db = SessionLocal()
        try:
            mark_cover_item_failed(mark_db, item_uuid, exc)
        finally:
            mark_db.close()
        return "failed"
    except Exception as exc:
        logger.exception("[cover batch %s] item %s failed", job_id, item_id)
        mark_db = SessionLocal()
        try:
            mark_cover_item_failed(mark_db, item_uuid, exc)
        finally:
            mark_db.close()
        return "failed"
    finally:
        db.close()

    done_db = SessionLocal()
    try:
        done_item = done_db.get(CoverBatchItem, item_uuid)
        if done_item is None:
            return "skipped"
        mark_cover_item_completed(done_db, done_item.id, worker_id, _cover_batch_item_path(done_item))
    finally:
        done_db.close()
    return "completed"


@celery_app.task(
    bind=True,
    name="tasks.generate_cover_batch_job",
    max_retries=settings.COVER_TASK_MAX_RETRIES,
)
def generate_cover_batch_job(_self, job_id: str) -> dict[str, Any]:
    """Advance one DB-backed cover batch in bounded, resumable slices."""
    db = SessionLocal()
    try:
        job = cover_batch_jobs.get_cover_batch_job(db, job_id)
        if job is None:
            return {"job_id": job_id, "status": "deleted"}
        if job.status == CoverBatchStatus.PAUSED:
            return {"job_id": job_id, "status": "paused"}
        if job.status == CoverBatchStatus.COMPLETED and not has_ready_cover_items(db, job_id):
            return {"job_id": job_id, "status": "completed"}

        worker_id = uuid.uuid4().hex
        max_workers = max(
            1,
            min(
                int(settings.COVER_BATCH_MAX_CONCURRENT_ITEMS or settings.COVER_JOB_MAX_CONCURRENT_ITEMS or 1),
                int(settings.IMAGE_MAX_CONCURRENT_CALLS or 1),
            ),
        )
        max_items = max(max_workers, int(settings.COVER_BATCH_MAX_ITEMS_PER_RUN or max_workers))
        items = claim_ready_cover_items(db, job_id, worker_id=worker_id, limit=max_items)
    finally:
        db.close()

    if not items:
        finish_db = SessionLocal()
        try:
            job = aggregate_cover_batch_job(finish_db, job_id)
            countdown = next_retry_countdown(finish_db, job_id)
            if job is not None and job.status == CoverBatchStatus.PENDING and countdown is not None:
                generate_cover_batch_job.apply_async(args=(job_id,), countdown=countdown)
                finish_db.commit()
                return {
                    "job_id": job_id,
                    "status": job.status,
                    "reason": "retry_wait_scheduled",
                    "countdown": countdown,
                }
            finish_db.commit()
            return {"job_id": job_id, "status": job.status if job else "deleted"}
        finally:
            finish_db.close()

    paused = False
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=f"cover-db-{job_id[:6]}") as executor:
        futures = [
            executor.submit(_generate_one_cover_batch_item, job_id, str(item.id), worker_id)
            for item in items
        ]
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception:
                logger.exception("[cover batch %s] worker thread failed", job_id)
                result = "failed"
            if result == "paused":
                paused = True

    db = SessionLocal()
    try:
        job = cover_batch_jobs.get_cover_batch_job(db, job_id)
        if job is None:
            return {"job_id": job_id, "status": "deleted"}
        if paused or job.status == CoverBatchStatus.PAUSED:
            job.status = CoverBatchStatus.PAUSED
            job.current_step = "Paused"
            db.commit()
            return {"job_id": job_id, "status": "paused"}
        aggregate_cover_batch_job(db, job_id)
        if has_ready_cover_items(db, job_id):
            db.commit()
            generate_cover_batch_job.delay(job_id)
            return {"job_id": job_id, "status": "pending", "reason": "next_batch_dispatched"}
        countdown = next_retry_countdown(db, job_id)
        if countdown is not None:
            db.commit()
            generate_cover_batch_job.apply_async(args=(job_id,), countdown=countdown)
            return {
                "job_id": job_id,
                "status": "pending",
                "reason": "retry_wait_scheduled",
                "countdown": countdown,
            }
        job = aggregate_cover_batch_job(db, job_id)
        db.commit()
        return {
            "job_id": job_id,
            "status": job.status if job else "deleted",
            "completed": job.completed if job else 0,
            "failed": job.failed if job else 0,
            "uncertain": job.uncertain if job else 0,
        }
    finally:
        db.close()


def requeue_stale_cover_jobs(*, limit: int = 200) -> int:
    if not settings.STALE_TASK_WATCHDOG_ENABLED:
        return 0
    recovered_db = 0
    if settings.COVER_BATCH_V2_ENABLED:
        db = SessionLocal()
        try:
            try:
                recovered_db = release_expired_cover_leases(db, limit=limit)
                if recovered_db:
                    stale_jobs = (
                        db.query(CoverBatchItem.job_id)
                        .filter(CoverBatchItem.status.in_([CoverBatchItemStatus.PENDING, CoverBatchItemStatus.QUEUED]))
                        .distinct()
                        .limit(max(1, int(limit or 1)))
                        .all()
                    )
                    for (job_id,) in stale_jobs:
                        generate_cover_batch_job.delay(job_id)
            except SQLAlchemyError as exc:
                db.rollback()
                logger.warning("cover batch lease recovery skipped because DB schema is not ready: %s", exc)
        finally:
            db.close()
    if not COVER_JOBS_ROOT.exists():
        return recovered_db

    stale_after = max(
        300,
        int(
            settings.COVER_PROCESSING_STALE_SECONDS
            or settings.STALE_PROCESSING_REQUEUE_SECONDS
            or 900
        ),
    )
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after)
    dispatched = 0

    for path in sorted(COVER_JOBS_ROOT.glob("*/status.json")):
        if dispatched >= max(1, int(limit or 1)):
            break
        job_id = path.parent.name
        try:
            status = read_status(job_id)
        except Exception:
            continue
        if status.get("status") != "processing" or not status.get("active_run_id"):
            continue

        fallback_started = _parse_iso_datetime(status.get("updated_at") or status.get("created_at"))
        items = list(status.get("items") or [])
        stale_items = []
        for item in items:
            if item.get("status") != "processing":
                continue
            started_at = (
                _parse_iso_datetime(item.get("last_heartbeat_at"))
                or _parse_iso_datetime(item.get("stage_started_at"))
                or _parse_iso_datetime(item.get("started_at"))
                or fallback_started
            )
            if started_at is None or started_at <= cutoff:
                stale_items.append(item)
        if not stale_items:
            continue

        old_run_id = status.get("active_run_id")
        for item in items:
            if item.get("status") != "processing":
                continue
            item.update(
                status="pending",
                error="Previous image request timed out and was returned to the queue.",
                image_url=None,
            )
            item.pop("run_id", None)
            item.pop("current_stage", None)
            item.pop("stage_started_at", None)
            item.pop("last_heartbeat_at", None)
        status["items"] = items
        status["status"] = "pending"
        status["active_run_id"] = None
        status["current_step"] = "Timed-out cover requests were returned to the queue."
        _recount(status)
        write_status(job_id, status)
        generate_cover_job.delay(job_id)
        dispatched += 1
        logger.warning(
            "[cover job %s] stale run %s returned to queue after %ss; stale_items=%s",
            job_id,
            old_run_id,
            stale_after,
            [item.get("index") for item in stale_items],
        )

    return recovered_db + dispatched


__all__ = ["generate_cover_job", "generate_cover_batch_job", "requeue_stale_cover_jobs"]
