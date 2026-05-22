"""Database-backed standalone cover batch jobs."""
from __future__ import annotations

import logging
import random
import shutil
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from app.core.config import PROJECT_ROOT, settings
from app.models.cover_batch import (
    CoverArtifact,
    CoverArtifactKind,
    CoverBatchItem,
    CoverBatchItemStatus,
    CoverBatchJob,
    CoverBatchStatus,
    ProviderRequest,
    ProviderRequestStatus,
)
from app.services.cover_jobs import (
    normalize_cover_category,
    row_author,
    row_title,
    safe_archive_image_name,
    safe_image_name,
)

logger = logging.getLogger(__name__)

COVER_BATCH_ROOT = PROJECT_ROOT / "backend" / "output" / "cover_batch_jobs"
TERMINAL_ITEM_STATUSES = {
    CoverBatchItemStatus.COMPLETED,
    CoverBatchItemStatus.FAILED,
    CoverBatchItemStatus.UNCERTAIN,
    CoverBatchItemStatus.DELETED,
    CoverBatchItemStatus.CANCELLED,
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def create_job_id() -> str:
    return uuid.uuid4().hex


def _owner_uuid(owner_id: str) -> uuid.UUID:
    return owner_id if isinstance(owner_id, uuid.UUID) else uuid.UUID(str(owner_id))


def job_dir(job_id: str) -> Path:
    return COVER_BATCH_ROOT / job_id


def image_path(job_id: str, index: int, title: str) -> Path:
    return job_dir(job_id) / "images" / safe_image_name(index, title)


def _unique_archive_name(filename: str, used: set[str]) -> str:
    if filename not in used:
        used.add(filename)
        return filename
    if "." in filename:
        stem, ext = filename.rsplit(".", 1)
        suffix = f".{ext}"
    else:
        stem, suffix = filename, ""
    counter = 2
    while True:
        candidate = f"{stem}_{counter}{suffix}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        counter += 1


def _item_public(item: CoverBatchItem) -> dict[str, Any]:
    return {
        "index": int(item.item_index),
        "title": item.title,
        "author": item.author,
        "category": item.category,
        "category_label": item.category_label,
        "status": item.status,
        "image_url": item.image_url,
        "error": item.error,
        "metadata": dict(item.source_metadata or {}),
        "current_stage": item.current_stage,
        "retry_count": int(item.retry_count or 0),
    }


def public_cover_batch_job_status(job: CoverBatchJob) -> dict[str, Any]:
    items = [item for item in (job.items or []) if item.status != CoverBatchItemStatus.DELETED]
    total = len(items)
    completed = sum(1 for item in items if item.status == CoverBatchItemStatus.COMPLETED)
    failed = sum(1 for item in items if item.status == CoverBatchItemStatus.FAILED)
    uncertain = sum(1 for item in items if item.status == CoverBatchItemStatus.UNCERTAIN)
    processed = completed + failed + uncertain
    progress = 100 if total <= 0 else min(100, int(processed * 100 / total))
    return {
        "job_id": job.id,
        "status": job.status,
        "progress": progress,
        "total": total,
        "completed": completed,
        "failed": failed,
        "uncertain": uncertain,
        "current_step": job.current_step,
        "source_filename": job.source_filename,
        "default_category": job.default_category,
        "default_category_label": job.default_category_label,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "items": [_item_public(item) for item in sorted(items, key=lambda value: value.item_index)],
        "engine": "db_v2",
    }


def get_cover_batch_job(db: Session, job_id: str) -> CoverBatchJob | None:
    return (
        db.query(CoverBatchJob)
        .options(selectinload(CoverBatchJob.items))
        .filter(CoverBatchJob.id == job_id)
        .one_or_none()
    )


def get_owned_cover_batch_job(db: Session, job_id: str, owner_id: str) -> CoverBatchJob | None:
    return (
        db.query(CoverBatchJob)
        .options(selectinload(CoverBatchJob.items))
        .filter(CoverBatchJob.id == job_id, CoverBatchJob.owner_id == _owner_uuid(owner_id))
        .one_or_none()
    )


def list_cover_batch_jobs(db: Session, owner_id: str, *, limit: int = 50) -> list[CoverBatchJob]:
    return (
        db.query(CoverBatchJob)
        .options(selectinload(CoverBatchJob.items))
        .filter(CoverBatchJob.owner_id == _owner_uuid(owner_id), CoverBatchJob.status != CoverBatchStatus.DELETED)
        .order_by(CoverBatchJob.updated_at.desc(), CoverBatchJob.created_at.desc())
        .limit(max(1, int(limit or 50)))
        .all()
    )


def create_cover_batch_job(
    db: Session,
    owner_id: str,
    filename: str,
    rows: list[dict[str, str]],
    *,
    default_category: str | None = None,
) -> CoverBatchJob:
    job_id = create_job_id()
    default_category_id, default_category_label = normalize_cover_category(default_category)
    directory = job_dir(job_id)
    directory.mkdir(parents=True, exist_ok=True)
    job = CoverBatchJob(
        id=job_id,
        owner_id=_owner_uuid(owner_id),
        status=CoverBatchStatus.PENDING,
        progress=0,
        total=len(rows),
        completed=0,
        failed=0,
        uncertain=0,
        current_step="Queued for resilient cover generation",
        source_filename=filename,
        default_category=default_category_id,
        default_category_label=default_category_label,
        config={
            "engine": "db_v2",
            "official_direct_only": bool(settings.OFFICIAL_API_DIRECT_ONLY),
            "openai_batch_requested": bool(settings.COVER_BATCH_USE_OPENAI_BATCH),
            "openai_batch_enabled": False,
            "openai_batch_reason": "image edit/generation batch is feature-gated; sync queue fallback active",
        },
    )
    db.add(job)
    db.flush()
    for idx, row in enumerate(rows, start=1):
        title = row_title(row) or f"cover_{idx}"
        author = row_author(row) or None
        category_id, category_label = normalize_cover_category(str(row.get("category") or ""), default_category_id)
        item_path = image_path(job_id, idx, title)
        db.add(
            CoverBatchItem(
                job_id=job_id,
                item_index=idx,
                title=title,
                author=author,
                category=category_id,
                category_label=str(row.get("category_label") or category_label),
                status=CoverBatchItemStatus.PENDING,
                image_path=str(item_path),
                source_metadata=dict(row),
            )
        )
    aggregate_cover_batch_job(db, job_id)
    db.commit()
    return get_cover_batch_job(db, job_id) or job


def aggregate_cover_batch_job(db: Session, job_id: str) -> CoverBatchJob | None:
    job = (
        db.query(CoverBatchJob)
        .options(selectinload(CoverBatchJob.items))
        .filter(CoverBatchJob.id == job_id)
        .one_or_none()
    )
    if job is None:
        return None
    items = [item for item in job.items if item.status != CoverBatchItemStatus.DELETED]
    total = len(items)
    completed = sum(1 for item in items if item.status == CoverBatchItemStatus.COMPLETED)
    failed = sum(1 for item in items if item.status == CoverBatchItemStatus.FAILED)
    uncertain = sum(1 for item in items if item.status == CoverBatchItemStatus.UNCERTAIN)
    processing = sum(1 for item in items if item.status == CoverBatchItemStatus.PROCESSING)
    queued = sum(
        1
        for item in items
        if item.status in {CoverBatchItemStatus.PENDING, CoverBatchItemStatus.QUEUED}
    )
    processed = completed + failed + uncertain
    job.total = total
    job.completed = completed
    job.failed = failed
    job.uncertain = uncertain
    job.progress = 100 if total <= 0 else min(100, int(processed * 100 / total))
    if job.status == CoverBatchStatus.PAUSED:
        job.current_step = "Paused"
    elif total <= 0:
        job.status = CoverBatchStatus.COMPLETED
        job.current_step = "No covers remain"
        job.progress = 100
        job.completed_at = now_utc()
    elif processed >= total:
        job.status = CoverBatchStatus.COMPLETED if failed == 0 and uncertain == 0 else CoverBatchStatus.FAILED
        job.progress = 100
        job.completed_at = now_utc()
        if failed or uncertain:
            job.current_step = f"Completed {completed}/{total}; failed {failed}, uncertain {uncertain}"
        else:
            job.current_step = f"All covers completed: {completed}/{total}"
    elif processing:
        job.status = CoverBatchStatus.PROCESSING
        job.current_step = f"Generating covers: {completed}/{total} done, {processing} running, {queued} queued"
    else:
        job.status = CoverBatchStatus.PENDING
        job.current_step = f"Queued covers: {completed}/{total} done, {queued} waiting"
    db.add(job)
    return job


def pause_cover_batch_job(db: Session, job_id: str) -> CoverBatchJob:
    job = get_cover_batch_job(db, job_id)
    if job is None:
        raise FileNotFoundError(job_id)
    if job.status in {CoverBatchStatus.COMPLETED, CoverBatchStatus.DELETED}:
        return job
    now = now_utc()
    for item in job.items:
        if item.status == CoverBatchItemStatus.PROCESSING:
            item.status = CoverBatchItemStatus.PENDING
            item.leased_by = None
            item.lease_expires_at = None
            item.current_stage = None
            item.error = None
            item.updated_at = now
    job.status = CoverBatchStatus.PAUSED
    job.active_run_id = None
    job.current_step = "Paused"
    db.commit()
    return get_cover_batch_job(db, job_id) or job


def resume_cover_batch_job(db: Session, job_id: str) -> CoverBatchJob:
    job = get_cover_batch_job(db, job_id)
    if job is None or job.status == CoverBatchStatus.DELETED:
        raise FileNotFoundError(job_id)
    if job.status == CoverBatchStatus.COMPLETED:
        return job
    job.status = CoverBatchStatus.PENDING
    job.current_step = "Queued for resume"
    db.commit()
    return get_cover_batch_job(db, job_id) or job


def delete_cover_batch_job(db: Session, job_id: str) -> None:
    job = get_cover_batch_job(db, job_id)
    if job is None:
        raise FileNotFoundError(job_id)
    directory = job_dir(job_id)
    db.delete(job)
    db.commit()
    if directory.exists():
        shutil.rmtree(directory)


def delete_cover_batch_items(db: Session, job_id: str, indexes: list[int]) -> CoverBatchJob:
    job = get_cover_batch_job(db, job_id)
    if job is None:
        raise FileNotFoundError(job_id)
    requested = {int(index) for index in indexes if int(index) > 0}
    for item in job.items:
        if item.item_index not in requested or item.status == CoverBatchItemStatus.DELETED:
            continue
        path = Path(item.image_path or image_path(job_id, item.item_index, item.title))
        if path.exists():
            path.unlink()
        work_dir = path.parent / f".{path.stem}_work"
        if work_dir.exists():
            shutil.rmtree(work_dir)
        item.status = CoverBatchItemStatus.DELETED
        item.image_url = None
        item.error = None
        item.leased_by = None
        item.lease_expires_at = None
        item.current_stage = None
    aggregate_cover_batch_job(db, job_id)
    db.commit()
    return get_cover_batch_job(db, job_id) or job


def retry_cover_batch_items(
    db: Session,
    job_id: str,
    indexes: list[int] | None = None,
) -> CoverBatchJob:
    job = get_cover_batch_job(db, job_id)
    if job is None:
        raise FileNotFoundError(job_id)
    requested = {int(index) for index in indexes or [] if int(index) > 0} if indexes is not None else None
    retried = 0
    for item in job.items:
        if requested is not None and item.item_index not in requested:
            continue
        if item.status in {CoverBatchItemStatus.DELETED, CoverBatchItemStatus.COMPLETED}:
            continue
        if item.status not in {
            CoverBatchItemStatus.FAILED,
            CoverBatchItemStatus.UNCERTAIN,
            CoverBatchItemStatus.PENDING,
            CoverBatchItemStatus.QUEUED,
        }:
            continue
        path = Path(item.image_path or image_path(job_id, item.item_index, item.title))
        if path.exists():
            path.unlink()
        item.status = CoverBatchItemStatus.PENDING
        item.error = None
        item.image_url = None
        item.current_stage = None
        item.retry_after = None
        item.retry_count = 0
        item.leased_by = None
        item.lease_expires_at = None
        retried += 1
    if retried:
        job.status = CoverBatchStatus.PENDING
        job.current_step = f"Requeued {retried} cover item(s)"
    aggregate_cover_batch_job(db, job_id)
    db.commit()
    return get_cover_batch_job(db, job_id) or job


def claim_ready_cover_items(
    db: Session,
    job_id: str,
    *,
    worker_id: str,
    limit: int,
) -> list[CoverBatchItem]:
    now = now_utc()
    lease_until = now + timedelta(seconds=max(300, int(settings.COVER_BATCH_LEASE_SECONDS or 1800)))
    query = (
        db.query(CoverBatchItem)
        .filter(
            CoverBatchItem.job_id == job_id,
            CoverBatchItem.status.in_([CoverBatchItemStatus.PENDING, CoverBatchItemStatus.QUEUED]),
            or_(CoverBatchItem.retry_after.is_(None), CoverBatchItem.retry_after <= now),
        )
        .order_by(CoverBatchItem.item_index.asc())
        .limit(max(1, int(limit or 1)))
    )
    try:
        if db.bind and db.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
    except Exception:
        pass
    items = query.all()
    for item in items:
        item.status = CoverBatchItemStatus.PROCESSING
        item.current_stage = "preparing"
        item.leased_by = worker_id
        item.lease_expires_at = lease_until
        item.started_at = item.started_at or now
        item.error = None
    job = db.get(CoverBatchJob, job_id)
    if job is not None:
        job.status = CoverBatchStatus.PROCESSING
        job.active_run_id = worker_id
        job.started_at = job.started_at or now
        job.current_step = f"Leased {len(items)} cover item(s)"
    db.commit()
    return items


def has_ready_cover_items(db: Session, job_id: str) -> bool:
    now = now_utc()
    return (
        db.query(CoverBatchItem.id)
        .filter(
            CoverBatchItem.job_id == job_id,
            CoverBatchItem.status.in_([CoverBatchItemStatus.PENDING, CoverBatchItemStatus.QUEUED]),
            or_(CoverBatchItem.retry_after.is_(None), CoverBatchItem.retry_after <= now),
        )
        .first()
        is not None
    )


def next_retry_countdown(db: Session, job_id: str) -> int | None:
    now = now_utc()
    retry_after = (
        db.query(func.min(CoverBatchItem.retry_after))
        .filter(
            CoverBatchItem.job_id == job_id,
            CoverBatchItem.status.in_([CoverBatchItemStatus.PENDING, CoverBatchItemStatus.QUEUED]),
            CoverBatchItem.retry_after > now,
        )
        .scalar()
    )
    if retry_after is None:
        return None
    if retry_after.tzinfo is None:
        retry_after = retry_after.replace(tzinfo=timezone.utc)
    return max(10, int((retry_after - now).total_seconds()))


def release_expired_cover_leases(db: Session, *, limit: int = 200) -> int:
    now = now_utc()
    rows = (
        db.query(CoverBatchItem)
        .filter(
            CoverBatchItem.status == CoverBatchItemStatus.PROCESSING,
            CoverBatchItem.lease_expires_at.is_not(None),
            CoverBatchItem.lease_expires_at < now,
        )
        .order_by(CoverBatchItem.updated_at.asc())
        .limit(max(1, int(limit or 1)))
        .all()
    )
    for item in rows:
        item.status = CoverBatchItemStatus.PENDING
        item.error = "Previous worker lease expired; returned to queue."
        item.current_stage = None
        item.leased_by = None
        item.lease_expires_at = None
        aggregate_cover_batch_job(db, item.job_id)
    db.commit()
    return len(rows)


def mark_cover_item_progress(
    db: Session,
    item_id: uuid.UUID,
    worker_id: str,
    stage: str,
) -> bool:
    item = db.get(CoverBatchItem, item_id)
    if item is None or item.status != CoverBatchItemStatus.PROCESSING or item.leased_by != worker_id:
        return False
    item.current_stage = stage
    item.lease_expires_at = now_utc() + timedelta(seconds=max(300, int(settings.COVER_BATCH_LEASE_SECONDS or 1800)))
    db.commit()
    return True


def should_abort_cover_item(db: Session, item_id: uuid.UUID, worker_id: str) -> bool:
    item = db.get(CoverBatchItem, item_id)
    if item is None:
        return True
    job = item.job
    if job is None or job.status in {CoverBatchStatus.PAUSED, CoverBatchStatus.DELETED}:
        return True
    if item.status == CoverBatchItemStatus.DELETED:
        return True
    return item.leased_by != worker_id or item.status != CoverBatchItemStatus.PROCESSING


def mark_cover_item_completed(db: Session, item_id: uuid.UUID, worker_id: str, path: Path) -> None:
    item = db.get(CoverBatchItem, item_id)
    if item is None or item.status == CoverBatchItemStatus.DELETED:
        return
    item.status = CoverBatchItemStatus.COMPLETED
    item.error = None
    item.image_path = str(path)
    item.image_url = f"/api/v1/covers/jobs/{item.job_id}/items/{item.item_index}/image"
    item.current_stage = None
    item.leased_by = None
    item.lease_expires_at = None
    item.completed_at = now_utc()
    db.add(
        CoverArtifact(
            job_id=item.job_id,
            item_id=item.id,
            kind=CoverArtifactKind.FINAL,
            path=str(path),
            meta={"worker_id": worker_id},
        )
    )
    aggregate_cover_batch_job(db, item.job_id)
    db.commit()


def mark_cover_item_retryable(db: Session, item_id: uuid.UUID, exc: BaseException) -> str:
    item = db.get(CoverBatchItem, item_id)
    if item is None or item.status == CoverBatchItemStatus.DELETED:
        return "skipped"
    retry_count = int(item.retry_count or 0) + 1
    item.retry_count = retry_count
    item.image_url = None
    item.current_stage = None
    item.leased_by = None
    item.lease_expires_at = None
    if retry_count > max(0, int(settings.COVER_BATCH_MAX_AUTO_RETRIES or 0)):
        item.status = CoverBatchItemStatus.FAILED
        item.error = f"Auto retry limit reached: {str(exc)[:900]}"
        item.retry_after = None
        result = "failed"
    else:
        base = max(10, int(settings.COVER_BATCH_RETRY_BASE_SECONDS or 60))
        max_delay = max(base, int(settings.COVER_BATCH_RETRY_MAX_SECONDS or 900))
        delay = min(max_delay, base * (2 ** max(0, retry_count - 1)))
        delay = int(delay + random.uniform(0, min(30, delay * 0.25)))
        item.status = CoverBatchItemStatus.PENDING
        item.error = f"Retryable provider error; retry {retry_count}: {str(exc)[:900]}"
        item.retry_after = now_utc() + timedelta(seconds=delay)
        result = "retry"
    aggregate_cover_batch_job(db, item.job_id)
    db.commit()
    return result


def mark_cover_item_uncertain(db: Session, item_id: uuid.UUID, exc: BaseException) -> None:
    item = db.get(CoverBatchItem, item_id)
    if item is None or item.status == CoverBatchItemStatus.DELETED:
        return
    item.status = CoverBatchItemStatus.UNCERTAIN
    item.error = f"Provider result uncertain; automatic retry stopped to avoid duplicate billing: {str(exc)[:900]}"
    item.image_url = None
    item.current_stage = None
    item.leased_by = None
    item.lease_expires_at = None
    item.retry_after = None
    item.completed_at = now_utc()
    aggregate_cover_batch_job(db, item.job_id)
    db.commit()


def mark_cover_item_failed(db: Session, item_id: uuid.UUID, exc: BaseException) -> None:
    item = db.get(CoverBatchItem, item_id)
    if item is None or item.status == CoverBatchItemStatus.DELETED:
        return
    item.status = CoverBatchItemStatus.FAILED
    item.error = str(exc)[:1000] or exc.__class__.__name__
    item.image_url = None
    item.current_stage = None
    item.leased_by = None
    item.lease_expires_at = None
    item.retry_after = None
    item.completed_at = now_utc()
    aggregate_cover_batch_job(db, item.job_id)
    db.commit()


def record_provider_request_event(
    db: Session,
    *,
    job_id: str,
    item_id: uuid.UUID,
    event: dict[str, Any],
) -> None:
    stage = str(event.get("stage") or "unknown")
    endpoint = str(event.get("endpoint") or "")
    endpoint_name = endpoint.rstrip("/").rsplit("/", 1)[-1] if endpoint else "unknown"
    request_key = f"{job_id}:{item_id}:{stage}:{endpoint_name}"
    record = (
        db.query(ProviderRequest)
        .filter(ProviderRequest.request_key == request_key)
        .one_or_none()
    )
    now = now_utc()
    if record is None:
        record = ProviderRequest(
            request_key=request_key,
            job_id=job_id,
            item_id=item_id,
            provider=str(event.get("provider") or "openai_image"),
            model=str(event.get("model") or settings.IMAGE_MODEL),
            stage=stage,
            endpoint=endpoint,
            started_at=now,
        )
        db.add(record)
    event_name = str(event.get("event") or "start")
    if event_name == "start":
        if record.started_at is not None and record.ended_at is not None:
            record.attempts = int(record.attempts or 1) + 1
        record.status = ProviderRequestStatus.STARTED
        record.started_at = now
        record.ended_at = None
    elif event_name == "success":
        record.status = ProviderRequestStatus.SUCCEEDED
        record.ended_at = now
        record.billing_risk = False
        record.error_type = None
        record.error = None
        record.response_summary = dict(event.get("response_summary") or {})
    elif event_name == "error":
        billing_risk = bool(event.get("billing_risk"))
        record.status = ProviderRequestStatus.UNCERTAIN if billing_risk else ProviderRequestStatus.FAILED
        record.billing_risk = billing_risk
        record.ended_at = now
        record.error_type = str(event.get("error_type") or "provider_error")[:80]
        record.error = str(event.get("error") or "")[:1000]
    db.commit()


def build_zip(job_id: str, job: CoverBatchJob) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        used_names: set[str] = set()
        written = 0
        for item in sorted(job.items or [], key=lambda value: value.item_index):
            if item.status != CoverBatchItemStatus.COMPLETED:
                continue
            path = Path(item.image_path or image_path(job_id, item.item_index, item.title))
            if path.exists() and path.stat().st_size > 0:
                archive.write(
                    path,
                    arcname=_unique_archive_name(
                        safe_archive_image_name(item.title, item.item_index),
                        used_names,
                    ),
                )
                written += 1
        if written == 0:
            raise FileNotFoundError("No completed covers are available for download")
    return buffer.getvalue()


def provider_queue_status(db: Session) -> dict[str, Any]:
    counts = {
        "pending": db.query(CoverBatchItem).filter(CoverBatchItem.status.in_([CoverBatchItemStatus.PENDING, CoverBatchItemStatus.QUEUED])).count(),
        "processing": db.query(CoverBatchItem).filter(CoverBatchItem.status == CoverBatchItemStatus.PROCESSING).count(),
        "completed": db.query(CoverBatchItem).filter(CoverBatchItem.status == CoverBatchItemStatus.COMPLETED).count(),
        "failed": db.query(CoverBatchItem).filter(CoverBatchItem.status == CoverBatchItemStatus.FAILED).count(),
        "uncertain": db.query(CoverBatchItem).filter(CoverBatchItem.status == CoverBatchItemStatus.UNCERTAIN).count(),
    }
    return {
        "engine": "db_v2",
        "official_direct_only": bool(settings.OFFICIAL_API_DIRECT_ONLY),
        "openai_image": {
            "model": settings.IMAGE_MODEL,
            "max_concurrent": settings.IMAGE_MAX_CONCURRENT_CALLS,
            "batch_enabled": False,
            "batch_reason": "sync queue fallback active for image workflow",
        },
        "limits": {
            "cover_batch_max_concurrent_items": settings.COVER_BATCH_MAX_CONCURRENT_ITEMS,
            "cover_batch_max_items_per_run": settings.COVER_BATCH_MAX_ITEMS_PER_RUN,
            "gemini_max_concurrent_calls": settings.GEMINI_MAX_CONCURRENT_CALLS,
            "deepseek_format_max_concurrent_calls": settings.DEEPSEEK_FORMAT_MAX_CONCURRENT_CALLS,
        },
        "item_counts": counts,
    }
