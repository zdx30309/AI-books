"""Standalone cover generation router."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app.api.routers.auth import get_current_user
from app.db.session import get_db
from app.models import User
from app.schemas.cover_job import (
    CoverJobBatchItem,
    CoverJobBatchRequest,
    CoverJobBatchResponse,
    CoverJobListResponse,
    CoverJobResponse,
)
from app.services.cover_jobs import (
    build_zip,
    CoverDuplicateError,
    create_cover_job,
    delete_cover_job,
    image_path,
    list_cover_jobs,
    pause_cover_job,
    parse_cover_spreadsheet,
    read_status,
    resume_cover_job,
    write_status,
)
from app.tasks.cover_tasks import generate_cover_job

router = APIRouter(prefix="/api/v1/covers", tags=["Covers"])


def _owned_status(job_id: str, user: User) -> dict:
    try:
        status = read_status(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"cover job {job_id} not found") from exc
    if status.get("owner_id") != str(user.id):
        raise HTTPException(status_code=404, detail=f"cover job {job_id} not found")
    return status


def _response(status: dict) -> CoverJobResponse:
    public = dict(status)
    public.pop("owner_id", None)
    return CoverJobResponse.model_validate(public)


@router.post(
    "/jobs",
    response_model=CoverJobResponse,
    summary="上传 Excel 并创建独立封面生成任务",
)
async def create_cover_job_from_excel(
    file: UploadFile = File(...),
    category: str = Form("social_science"),
    _db: Session = Depends(get_db),
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

    try:
        status = create_cover_job(
            str(user.id),
            file.filename or "covers.xlsx",
            rows,
            default_category=category,
        )
    except CoverDuplicateError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc
    try:
        generate_cover_job.delay(status["job_id"])
    except Exception as exc:
        status["status"] = "failed"
        status["current_step"] = f"Celery 投递失败：{exc}"
        write_status(status["job_id"], status)
        raise HTTPException(status_code=503, detail=f"封面任务投递失败: {exc}") from exc
    return _response(status)


@router.get(
    "/jobs",
    response_model=CoverJobListResponse,
    summary="查询当前用户的独立封面任务列表",
)
def list_my_cover_jobs(
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
) -> CoverJobListResponse:
    return CoverJobListResponse(items=[_response(status) for status in list_cover_jobs(str(user.id), limit=limit)])


@router.post(
    "/jobs/batch",
    response_model=CoverJobBatchResponse,
    summary="批量暂停、继续或删除独立封面任务",
)
def batch_cover_jobs(
    req: CoverJobBatchRequest,
    user: User = Depends(get_current_user),
) -> CoverJobBatchResponse:
    items: list[CoverJobBatchItem] = []
    for job_id in list(dict.fromkeys(req.job_ids)):
        try:
            status = _owned_status(job_id, user)
            if req.action == "pause":
                pause_cover_job(job_id, status)
                items.append(CoverJobBatchItem(job_id=job_id, status="paused"))
            elif req.action == "resume":
                next_status = resume_cover_job(job_id, status)
                generate_cover_job.delay(next_status["job_id"])
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
    user: User = Depends(get_current_user),
) -> CoverJobResponse:
    return _response(pause_cover_job(job_id, _owned_status(job_id, user)))


@router.post(
    "/jobs/{job_id}/resume",
    response_model=CoverJobResponse,
    summary="继续独立封面任务",
)
def resume_my_cover_job(
    job_id: str,
    user: User = Depends(get_current_user),
) -> CoverJobResponse:
    status = resume_cover_job(job_id, _owned_status(job_id, user))
    try:
        generate_cover_job.delay(status["job_id"])
    except Exception as exc:
        status["status"] = "failed"
        status["current_step"] = f"Celery 投递失败：{exc}"
        write_status(status["job_id"], status)
        raise HTTPException(status_code=503, detail=f"封面任务投递失败: {exc}") from exc
    return _response(status)


@router.delete(
    "/jobs/{job_id}",
    status_code=204,
    summary="删除独立封面任务",
)
def delete_my_cover_job(
    job_id: str,
    user: User = Depends(get_current_user),
) -> Response:
    status = _owned_status(job_id, user)
    pause_cover_job(job_id, status)
    delete_cover_job(job_id)
    return Response(status_code=204)


@router.get(
    "/jobs/{job_id}",
    response_model=CoverJobResponse,
    summary="查询独立封面生成任务进度",
)
def get_cover_job(
    job_id: str,
    user: User = Depends(get_current_user),
) -> CoverJobResponse:
    return _response(_owned_status(job_id, user))


@router.get(
    "/jobs/{job_id}/items/{index}/image",
    summary="返回独立封面任务中的某张图片",
)
def get_cover_job_image(
    job_id: str,
    index: int,
    user: User = Depends(get_current_user),
) -> FileResponse:
    status = _owned_status(job_id, user)
    items = status.get("items") or []
    if index < 1 or index > len(items):
        raise HTTPException(status_code=404, detail="cover item not found")
    item = items[index - 1]
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
    user: User = Depends(get_current_user),
) -> Response:
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
