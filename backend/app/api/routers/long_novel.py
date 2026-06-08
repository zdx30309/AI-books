"""Long novel family workflow routes."""
from __future__ import annotations

from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.routers.auth import get_current_user
from app.db.session import get_db
from app.models import LongNovelGenerationLog, User
from app.schemas.long_novel import (
    FamilyLongNovelBatchRequest,
    FamilyLongNovelCreateRequest,
    LongNovelActionResponse,
    LongNovelBatchResponse,
    LongNovelChapterPlanResponse,
    LongNovelChapterResponse,
    LongNovelConflictLineResponse,
    LongNovelContinuityIssueResponse,
    LongNovelProjectDetailResponse,
    LongNovelProjectListResponse,
    LongNovelProjectResponse,
)
from app.services.family_long_novel import (
    FamilyLongNovelError,
    check_family_continuity,
    create_family_long_novel_project,
    export_family_novel,
    generate_family_batch,
    generate_family_chapter,
    generate_family_chapter_plan,
    generate_family_novel_bible,
    generate_family_novel_outline,
    list_family_projects,
    pause_family_project,
    repair_family_chapter,
    resume_family_project,
    _get_project,
)


router = APIRouter(prefix="/api/v1/long-novel/family", tags=["LongNovelFamily"])


def _raise_http(exc: FamilyLongNovelError) -> None:
    raise HTTPException(status_code=400, detail=str(exc)) from exc


def _project_detail(db: Session, project_id: UUID, user: User) -> LongNovelProjectDetailResponse:
    try:
        project = _get_project(db, project_id, user)
    except FamilyLongNovelError as exc:
        _raise_http(exc)
    logs = (
        db.query(LongNovelGenerationLog)
        .filter(LongNovelGenerationLog.project_id == project.id)
        .order_by(LongNovelGenerationLog.created_at.desc())
        .limit(120)
        .all()
    )
    return LongNovelProjectDetailResponse(
        project=project,
        bible=project.bible,
        members=project.family_members,
        relations=project.family_relations,
        conflict_lines=project.conflict_lines,
        chapter_plans=project.chapter_plans,
        chapters=project.chapters,
        issues=project.issues,
        logs=logs,
    )


@router.post("/projects", response_model=LongNovelProjectResponse)
def create_project(
    req: FamilyLongNovelCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> LongNovelProjectResponse:
    try:
        return create_family_long_novel_project(db, req, user)
    except FamilyLongNovelError as exc:
        _raise_http(exc)


@router.get("/projects", response_model=LongNovelProjectListResponse)
def list_projects(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> LongNovelProjectListResponse:
    total, items = list_family_projects(
        db,
        user,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    return LongNovelProjectListResponse(total=total, items=items)


@router.get("/projects/{project_id}", response_model=LongNovelProjectDetailResponse)
def get_project(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> LongNovelProjectDetailResponse:
    return _project_detail(db, project_id, user)


@router.post("/projects/{project_id}/bible", response_model=LongNovelProjectDetailResponse)
def generate_bible(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> LongNovelProjectDetailResponse:
    try:
        generate_family_novel_bible(db, project_id, user)
    except FamilyLongNovelError as exc:
        _raise_http(exc)
    return _project_detail(db, project_id, user)


@router.post("/projects/{project_id}/outline", response_model=LongNovelProjectDetailResponse)
def generate_outline(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> LongNovelProjectDetailResponse:
    try:
        generate_family_novel_outline(db, project_id, user)
    except FamilyLongNovelError as exc:
        _raise_http(exc)
    return _project_detail(db, project_id, user)


@router.post("/projects/{project_id}/chapters/{chapter_number}/plan", response_model=LongNovelChapterPlanResponse)
def generate_chapter_plan(
    project_id: UUID,
    chapter_number: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> LongNovelChapterPlanResponse:
    try:
        return generate_family_chapter_plan(db, project_id, chapter_number, user)
    except FamilyLongNovelError as exc:
        _raise_http(exc)


@router.post("/projects/{project_id}/chapters/{chapter_number}/generate", response_model=LongNovelChapterResponse)
def generate_chapter(
    project_id: UUID,
    chapter_number: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> LongNovelChapterResponse:
    try:
        return generate_family_chapter(db, project_id, chapter_number, user)
    except FamilyLongNovelError as exc:
        _raise_http(exc)


@router.post("/projects/{project_id}/generate-batch", response_model=LongNovelBatchResponse)
def generate_batch(
    project_id: UUID,
    req: FamilyLongNovelBatchRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> LongNovelBatchResponse:
    try:
        return generate_family_batch(db, project_id, req, user)
    except FamilyLongNovelError as exc:
        _raise_http(exc)


@router.post("/projects/{project_id}/pause", response_model=LongNovelProjectResponse)
def pause_project(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> LongNovelProjectResponse:
    try:
        return pause_family_project(db, project_id, user)
    except FamilyLongNovelError as exc:
        _raise_http(exc)


@router.post("/projects/{project_id}/resume", response_model=LongNovelProjectResponse)
def resume_project(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> LongNovelProjectResponse:
    try:
        return resume_family_project(db, project_id, user)
    except FamilyLongNovelError as exc:
        _raise_http(exc)


@router.post("/projects/{project_id}/chapters/{chapter_number}/check", response_model=list[LongNovelContinuityIssueResponse])
def check_chapter(
    project_id: UUID,
    chapter_number: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[LongNovelContinuityIssueResponse]:
    try:
        return check_family_continuity(db, project_id, chapter_number, user)
    except FamilyLongNovelError as exc:
        _raise_http(exc)


@router.post("/projects/{project_id}/chapters/{chapter_number}/repair", response_model=LongNovelChapterResponse)
def repair_chapter(
    project_id: UUID,
    chapter_number: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> LongNovelChapterResponse:
    try:
        return repair_family_chapter(db, project_id, chapter_number, user)
    except FamilyLongNovelError as exc:
        _raise_http(exc)


@router.get("/projects/{project_id}/chapters", response_model=list[LongNovelChapterResponse])
def list_chapters(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[LongNovelChapterResponse]:
    return _project_detail(db, project_id, user).chapters


@router.get("/projects/{project_id}/conflicts", response_model=list[LongNovelConflictLineResponse])
def list_conflicts(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[LongNovelConflictLineResponse]:
    return _project_detail(db, project_id, user).conflict_lines


@router.get("/projects/{project_id}/issues", response_model=list[LongNovelContinuityIssueResponse])
def list_issues(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[LongNovelContinuityIssueResponse]:
    return _project_detail(db, project_id, user).issues


@router.get("/projects/{project_id}/export")
def export_project(
    project_id: UUID,
    format: str = Query(default="markdown", pattern="^(markdown|md|txt|json)$"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    fmt = "md" if format == "markdown" else format
    try:
        content, media_type, filename = export_family_novel(db, project_id, fmt, user)
    except FamilyLongNovelError as exc:
        _raise_http(exc)
    quoted = quote(filename)
    return Response(
        content=content.encode("utf-8"),
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
    )
