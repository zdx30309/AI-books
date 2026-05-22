"""Standalone cover generation router."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app.api.routers.auth import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models import User
from app.schemas.task import TaskCategoryListResponse
from app.schemas.cover_job import (
    CoverJobBatchItem,
    CoverJobBatchRequest,
    CoverJobBatchResponse,
    CoverJobItemBatchDeleteRequest,
    CoverJobItemBatchRetryRequest,
    CoverJobListResponse,
    CoverJobResponse,
)
from app.services.cover_categories import list_cover_categories
from app.services.cover_batch_jobs import (
    build_zip as build_cover_batch_zip,
    create_cover_batch_job,
    delete_cover_batch_items,
    delete_cover_batch_job,
    get_owned_cover_batch_job,
    image_path as cover_batch_image_path,
    list_cover_batch_jobs,
    pause_cover_batch_job,
    provider_queue_status,
    public_cover_batch_job_status,
    retry_cover_batch_items,
    resume_cover_batch_job,
)
from app.services.cover_jobs import (
    build_zip,
    create_cover_job,
    delete_cover_job,
    delete_cover_job_items,
    image_path,
    list_cover_jobs,
    pause_cover_job,
    parse_cover_spreadsheet,
    public_cover_job_status,
    read_status,
    retry_cover_job_items,
    resume_cover_job,
    write_status,
)
from app.tasks.cover_tasks import generate_cover_batch_job, generate_cover_job

router = APIRouter(prefix="/api/v1/covers", tags=["Covers"])


def _v2_enabled() -> bool:
    return bool(settings.COVER_BATCH_V2_ENABLED)


def _owned_status(job_id: str, user: User) -> dict:
    try:
        status = read_status(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"cover job {job_id} not found") from exc
    if status.get("owner_id") != str(user.id):
        raise HTTPException(status_code=404, detail=f"cover job {job_id} not found")
    return status


def _response(status: dict) -> CoverJobResponse:
    return CoverJobResponse.model_validate(public_cover_job_status(status))


def _v2_response(job) -> CoverJobResponse:
    return CoverJobResponse.model_validate(public_cover_batch_job_status(job))


def _enqueue_cover_job(status: dict) -> None:
    try:
        generate_cover_job.delay(status["job_id"])
    except Exception as exc:
        status["status"] = "failed"
        status["current_step"] = f"Celery 投递失败：{exc}"
        write_status(status["job_id"], status)
        raise HTTPException(status_code=503, detail=f"封面任务投递失败: {exc}") from exc


def _enqueue_cover_batch_job(job_id: str) -> None:
    try:
        generate_cover_batch_job.delay(job_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"cover batch dispatch failed: {exc}") from exc


def _enqueue_retry_if_needed(job_id: str, status: dict) -> dict:
    if not any(item.get("status") == "pending" for item in status.get("items") or []):
        return status
    if status.get("status") == "paused":
        status = resume_cover_job(job_id, status)
    if status.get("status") != "processing" and not status.get("active_run_id"):
        _enqueue_cover_job(status)
    return status


@router.post(
    "/jobs",
    response_model=CoverJobResponse,
    summary="上传 Excel 并创建独立封面生成任务",
)
async def create_cover_job_from_excel(
    file: UploadFile = File(...),
    category: str = Form("social_science"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CoverJobResponse:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    try:
        rows = parse_cover_spreadsheet(
            file.filename or "covers.xlsx",
            content,
            default_category=category,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if _v2_enabled():
        job = create_cover_batch_job(
            db,
            str(user.id),
            file.filename or "covers.xlsx",
            rows,
            default_category=category,
        )
        _enqueue_cover_batch_job(job.id)
        return _v2_response(job)

    status = create_cover_job(
        str(user.id),
        file.filename or "covers.xlsx",
        rows,
        default_category=category,
    )
    _enqueue_cover_job(status)
    return _response(status)


@router.get(
    "/categories",
    response_model=TaskCategoryListResponse,
    summary="获取封面书架专用分类",
)
def get_cover_categories() -> TaskCategoryListResponse:
    return TaskCategoryListResponse(items=list_cover_categories())


@router.get(
    "/debug/provider-queue",
    summary="Inspect DB-backed cover provider queue and rate-limit settings",
)
def get_cover_provider_queue_status(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict:
    return provider_queue_status(db)


@router.get(
    "/jobs",
    response_model=CoverJobListResponse,
    summary="查询当前用户的独立封面任务列表",
)
def list_my_cover_jobs(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CoverJobListResponse:
    items: list[CoverJobResponse] = []
    if _v2_enabled():
        items.extend(_v2_response(job) for job in list_cover_batch_jobs(db, str(user.id), limit=limit))
    remaining = max(0, int(limit) - len(items))
    if remaining:
        items.extend(_response(status) for status in list_cover_jobs(str(user.id), limit=remaining))
    items.sort(key=lambda item: item.updated_at, reverse=True)
    return CoverJobListResponse(items=items[:limit])


@router.post(
    "/jobs/batch",
    response_model=CoverJobBatchResponse,
    summary="批量暂停、继续或删除独立封面任务",
)
def batch_cover_jobs(
    req: CoverJobBatchRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CoverJobBatchResponse:
    items: list[CoverJobBatchItem] = []
    for job_id in list(dict.fromkeys(req.job_ids)):
        try:
            if _v2_enabled():
                job = get_owned_cover_batch_job(db, job_id, str(user.id))
                if job is not None:
                    if req.action == "pause":
                        pause_cover_batch_job(db, job_id)
                        items.append(CoverJobBatchItem(job_id=job_id, status="paused"))
                    elif req.action == "resume":
                        resume_cover_batch_job(db, job_id)
                        _enqueue_cover_batch_job(job_id)
                        items.append(CoverJobBatchItem(job_id=job_id, status="pending"))
                    elif req.action == "delete":
                        delete_cover_batch_job(db, job_id)
                        items.append(CoverJobBatchItem(job_id=job_id, status="deleted"))
                    continue
            status = _owned_status(job_id, user)
            if req.action == "pause":
                pause_cover_job(job_id, status)
                items.append(CoverJobBatchItem(job_id=job_id, status="paused"))
            elif req.action == "resume":
                next_status = resume_cover_job(job_id, status)
                _enqueue_cover_job(next_status)
                items.append(CoverJobBatchItem(job_id=job_id, status="pending"))
            elif req.action == "delete":
                pause_cover_job(job_id, status)
                delete_cover_job(job_id)
                items.append(CoverJobBatchItem(job_id=job_id, status="deleted"))
        except Exception as exc:
            items.append(
                CoverJobBatchItem(job_id=job_id, status="skipped", reason=str(exc)[:300])
            )
    return CoverJobBatchResponse(items=items, total=len(items))


@router.post(
    "/jobs/{job_id}/pause",
    response_model=CoverJobResponse,
    summary="暂停独立封面任务",
)
def pause_my_cover_job(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CoverJobResponse:
    if _v2_enabled():
        job = get_owned_cover_batch_job(db, job_id, str(user.id))
        if job is not None:
            return _v2_response(pause_cover_batch_job(db, job_id))
    return _response(pause_cover_job(job_id, _owned_status(job_id, user)))


@router.post(
    "/jobs/{job_id}/resume",
    response_model=CoverJobResponse,
    summary="继续独立封面任务",
)
def resume_my_cover_job(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CoverJobResponse:
    if _v2_enabled():
        job = get_owned_cover_batch_job(db, job_id, str(user.id))
        if job is not None:
            next_job = resume_cover_batch_job(db, job_id)
            _enqueue_cover_batch_job(job_id)
            return _v2_response(next_job)
    status = resume_cover_job(job_id, _owned_status(job_id, user))
    _enqueue_cover_job(status)
    return _response(status)


@router.delete(
    "/jobs/{job_id}",
    status_code=204,
    summary="删除独立封面任务",
)
def delete_my_cover_job(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    if _v2_enabled():
        job = get_owned_cover_batch_job(db, job_id, str(user.id))
        if job is not None:
            delete_cover_batch_job(db, job_id)
            return Response(status_code=204)
    status = _owned_status(job_id, user)
    pause_cover_job(job_id, status)
    delete_cover_job(job_id)
    return Response(status_code=204)


@router.post(
    "/jobs/{job_id}/items/batch-delete",
    response_model=CoverJobResponse,
    summary="批量删除独立封面任务中的单张封面",
)
def delete_my_cover_job_items(
    job_id: str,
    req: CoverJobItemBatchDeleteRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CoverJobResponse:
    if not req.indexes:
        raise HTTPException(status_code=400, detail="请选择要删除的封面")
    if _v2_enabled():
        job = get_owned_cover_batch_job(db, job_id, str(user.id))
        if job is not None:
            return _v2_response(delete_cover_batch_items(db, job_id, req.indexes))
    status = _owned_status(job_id, user)
    return _response(delete_cover_job_items(job_id, req.indexes, status))


@router.delete(
    "/jobs/{job_id}/items/{index}",
    response_model=CoverJobResponse,
    summary="删除独立封面任务中的单张封面",
)
def delete_my_cover_job_item(
    job_id: str,
    index: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CoverJobResponse:
    if _v2_enabled():
        job = get_owned_cover_batch_job(db, job_id, str(user.id))
        if job is not None:
            return _v2_response(delete_cover_batch_items(db, job_id, [index]))
    status = _owned_status(job_id, user)
    return _response(delete_cover_job_items(job_id, [index], status))


@router.post(
    "/jobs/{job_id}/retry",
    response_model=CoverJobResponse,
    summary="重试独立封面任务中的全部失败封面",
)
def retry_my_failed_cover_job_items(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CoverJobResponse:
    if _v2_enabled():
        job = get_owned_cover_batch_job(db, job_id, str(user.id))
        if job is not None:
            next_job = retry_cover_batch_items(db, job_id, None)
            _enqueue_cover_batch_job(job_id)
            return _v2_response(next_job)
    status = retry_cover_job_items(job_id, None, _owned_status(job_id, user))
    return _response(_enqueue_retry_if_needed(job_id, status))


@router.post(
    "/jobs/{job_id}/items/batch-retry",
    response_model=CoverJobResponse,
    summary="批量重试独立封面任务中的失败封面",
)
def retry_my_cover_job_items(
    job_id: str,
    req: CoverJobItemBatchRetryRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CoverJobResponse:
    if not req.indexes:
        raise HTTPException(status_code=400, detail="请选择要重试的封面")
    if _v2_enabled():
        job = get_owned_cover_batch_job(db, job_id, str(user.id))
        if job is not None:
            next_job = retry_cover_batch_items(db, job_id, req.indexes)
            _enqueue_cover_batch_job(job_id)
            return _v2_response(next_job)
    status = retry_cover_job_items(job_id, req.indexes, _owned_status(job_id, user))
    return _response(_enqueue_retry_if_needed(job_id, status))


@router.post(
    "/jobs/{job_id}/items/{index}/retry",
    response_model=CoverJobResponse,
    summary="重试独立封面任务中的单张失败封面",
)
def retry_my_cover_job_item(
    job_id: str,
    index: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CoverJobResponse:
    if _v2_enabled():
        job = get_owned_cover_batch_job(db, job_id, str(user.id))
        if job is not None:
            next_job = retry_cover_batch_items(db, job_id, [index])
            _enqueue_cover_batch_job(job_id)
            return _v2_response(next_job)
    status = retry_cover_job_items(job_id, [index], _owned_status(job_id, user))
    return _response(_enqueue_retry_if_needed(job_id, status))


@router.get(
    "/jobs/{job_id}",
    response_model=CoverJobResponse,
    summary="查询独立封面生成任务进度",
)
def get_cover_job(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CoverJobResponse:
    if _v2_enabled():
        job = get_owned_cover_batch_job(db, job_id, str(user.id))
        if job is not None:
            return _v2_response(job)
    return _response(_owned_status(job_id, user))


@router.get(
    "/jobs/{job_id}/items/{index}/image",
    summary="返回独立封面任务中的某张图片",
)
def get_cover_job_image(
    job_id: str,
    index: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FileResponse:
    if _v2_enabled():
        job = get_owned_cover_batch_job(db, job_id, str(user.id))
        if job is not None:
            items = [item for item in job.items if item.item_index == index]
            if not items or items[0].status == "deleted":
                raise HTTPException(status_code=404, detail="cover image not found")
            item = items[0]
            path = Path(item.image_path) if item.image_path else cover_batch_image_path(job_id, index, item.title)
            if not path.exists():
                raise HTTPException(status_code=404, detail="cover image not found")
            return FileResponse(
                path,
                media_type="image/png",
                headers={
                    "Cache-Control": "private, max-age=3600",
                    "Vary": "Authorization",
                },
            )
    status = _owned_status(job_id, user)
    items = status.get("items") or []
    if index < 1 or index > len(items):
        raise HTTPException(status_code=404, detail="cover item not found")
    item = items[index - 1]
    if item.get("status") == "deleted":
        raise HTTPException(status_code=404, detail="cover image not found")
    path = image_path(job_id, index, str(item.get("title") or f"cover_{index}"))
    if not path.exists():
        raise HTTPException(status_code=404, detail="cover image not found")
    return FileResponse(
        path,
        media_type="image/png",
        headers={
            "Cache-Control": "private, max-age=3600",
            "Vary": "Authorization",
        },
    )


@router.get(
    "/jobs/{job_id}/download",
    summary="下载独立封面任务产出的全部封面",
)
def download_cover_job(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    if _v2_enabled():
        job = get_owned_cover_batch_job(db, job_id, str(user.id))
        if job is not None:
            try:
                content = build_cover_batch_zip(job_id, job)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail="No completed covers are available for download") from exc
            filename = f"covers_{job_id[:8]}.zip"
            encoded = quote(filename)
            return Response(
                content=content,
                media_type="application/zip",
                headers={
                    "Content-Disposition": (
                        f'attachment; filename="{encoded}"; '
                        f"filename*=UTF-8''{encoded}"
                    )
                },
            )
    status = _owned_status(job_id, user)
    try:
        content = build_zip(job_id, status)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="还没有可下载的封面") from exc
    filename = f"covers_{job_id[:8]}.zip"
    encoded = quote(filename)
    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{encoded}"; '
                f"filename*=UTF-8''{encoded}"
            )
        },
    )
