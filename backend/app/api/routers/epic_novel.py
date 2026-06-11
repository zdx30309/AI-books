"""Epic Novel Engine API routes."""
from __future__ import annotations

from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.routers.auth import get_current_user
from app.db.session import get_db
from app.models import ReferenceDownloadTask, User
from app.schemas.epic_novel import (
    EpicBatchGenerationRequest,
    EpicBatchGenerationResponse,
    EpicChapterContractResponse,
    EpicChapterResponse,
    EpicDashboardResponse,
    EpicNovelCreateRequest,
    EpicNovelProjectDetailResponse,
    EpicNovelProjectListResponse,
    EpicNovelProjectResponse,
    ReferenceBookResponse,
    ReferenceBooklistImportRequest,
    ReferenceBooklistItemResponse,
    ReferenceBooksImportRequest,
    ReferenceBooksIngestRequest,
    ReferenceDownloaderDownloadRequest,
    ReferenceDownloaderSearchRequest,
    ReferenceDownloadTaskResponse,
    ReferenceMechanismProfileResponse,
    ReferencePatternCardResponse,
    ReferencePatternSearchRequest,
)
from app.services.epic_novel import (
    EpicNovelError,
    build_chapter_context_pack,
    check_chapter,
    compile_chapter_contracts,
    create_epic_project,
    dashboard,
    execute_chapter,
    export_bible,
    export_bible_file_metadata,
    export_novel,
    export_novel_file_metadata,
    forge_power_system,
    generate_arc_ledgers,
    generate_batch,
    generate_bible_manifest,
    generate_bible_section,
    generate_ensemble_cast,
    generate_global_spine,
    generate_volume,
    generate_volume_contracts,
    generate_world_atlas,
    generate_world_bible,
    list_epic_projects,
    project_detail,
    repair_bible_coverage,
    repair_chapter,
    repair_outline,
    run_bible_coverage_ci,
    run_outline_ci,
    run_worldview_tournament,
)
from app.services.epic_reference import (
    EpicReferenceError,
    build_reference_mechanism_profile,
    create_reference_search_task,
    detect_sonovel,
    download_reference_task,
    import_downloaded_references,
    import_reference_file,
    ingest_reference_book,
    list_reference_download_tasks,
    latest_reference_profile,
    normalize_booklist,
    scan_sonovel_downloads,
    search_reference_download_task,
    search_pattern_cards,
)


router = APIRouter(prefix="/api/v1/epic-novel", tags=["EpicNovel"])


def _bad_request(exc: Exception) -> None:
    raise HTTPException(status_code=400, detail=str(exc)) from exc


def _row(row: object) -> dict[str, object]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


@router.post("/projects", response_model=EpicNovelProjectResponse)
def create_project(
    req: EpicNovelCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EpicNovelProjectResponse:
    try:
        return create_epic_project(db, req, user)
    except (EpicNovelError, EpicReferenceError) as exc:
        _bad_request(exc)


@router.get("/projects", response_model=EpicNovelProjectListResponse)
def list_projects(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EpicNovelProjectListResponse:
    total, items = list_epic_projects(db, user, limit=page_size, offset=(page - 1) * page_size)
    return EpicNovelProjectListResponse(total=total, items=items)


@router.get("/projects/{project_id}", response_model=EpicNovelProjectDetailResponse)
def get_project(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EpicNovelProjectDetailResponse:
    try:
        return EpicNovelProjectDetailResponse(**project_detail(db, project_id, user))
    except EpicNovelError as exc:
        _bad_request(exc)


@router.post("/reference-booklist/import", response_model=list[ReferenceBooklistItemResponse])
def import_reference_booklist(
    req: ReferenceBooklistImportRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ReferenceBooklistItemResponse]:
    del user
    try:
        return normalize_booklist(db, seed_path=req.seed_path, raw_markdown=req.raw_markdown, create_tasks=False)
    except EpicReferenceError as exc:
        _bad_request(exc)


@router.post("/reference-booklist/normalize", response_model=list[ReferenceBooklistItemResponse])
def normalize_reference_booklist(
    req: ReferenceBooklistImportRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ReferenceBooklistItemResponse]:
    del user
    try:
        return normalize_booklist(db, seed_path=req.seed_path, raw_markdown=req.raw_markdown, create_tasks=True)
    except EpicReferenceError as exc:
        _bad_request(exc)


@router.post("/reference-downloader/detect")
def detect_reference_downloader(user: User = Depends(get_current_user)) -> dict[str, object]:
    del user
    return detect_sonovel()


@router.post("/reference-downloader/search", response_model=ReferenceDownloadTaskResponse)
def search_reference_book(
    req: ReferenceDownloaderSearchRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReferenceDownloadTaskResponse:
    del user
    return create_reference_search_task(db, title=req.title, author=req.author)


@router.post("/reference-downloader/download", response_model=ReferenceDownloadTaskResponse)
def download_reference_book(
    req: ReferenceDownloaderDownloadRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReferenceDownloadTaskResponse:
    del user
    task = db.get(ReferenceDownloadTask, req.task_id) if req.task_id else None
    if task is None:
        task = create_reference_search_task(db, title=req.title or "unknown", author=req.author)
    return download_reference_task(db, task, selected_result=req.selected_result)


@router.post("/reference-downloader/batch-download", response_model=list[ReferenceDownloadTaskResponse])
def batch_download_reference_books(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ReferenceDownloadTaskResponse]:
    del user
    tasks = list_reference_download_tasks(db)
    for task in tasks:
        if task.status in {"pending", "searching", "not_found", "manual_required"} or not task.search_results:
            search_reference_download_task(db, task)
    return tasks


@router.post("/reference-downloader/scan-downloads")
def scan_reference_downloads(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict[str, object]]:
    del user
    return scan_sonovel_downloads(db)


@router.post("/reference-downloader/import-downloaded", response_model=list[ReferenceBookResponse])
def import_downloaded(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ReferenceBookResponse]:
    del user
    try:
        return import_downloaded_references(db)
    except EpicReferenceError as exc:
        _bad_request(exc)


@router.get("/reference-download-tasks", response_model=list[ReferenceDownloadTaskResponse])
def get_reference_download_tasks(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ReferenceDownloadTaskResponse]:
    del user
    return list_reference_download_tasks(db)


@router.get("/reference-download-tasks/{task_id}", response_model=ReferenceDownloadTaskResponse)
def get_reference_download_task(
    task_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReferenceDownloadTaskResponse:
    del user
    task = db.get(ReferenceDownloadTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="ReferenceDownloadTask not found")
    return task


@router.post("/reference-books/import", response_model=list[ReferenceBookResponse])
def import_reference_books(
    req: ReferenceBooksImportRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ReferenceBookResponse]:
    del user
    paths = [*req.paths]
    if req.path:
        paths.append(req.path)
    if not paths:
        raise HTTPException(status_code=400, detail="path or paths required")
    try:
        return [import_reference_file(db, file_path=path) for path in paths]
    except EpicReferenceError as exc:
        _bad_request(exc)


@router.post("/reference-books/ingest")
def ingest_reference_books(
    req: ReferenceBooksIngestRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, object]:
    del user
    try:
        return ingest_reference_book(db, book_id=req.book_id, source_path=req.source_path)
    except EpicReferenceError as exc:
        _bad_request(exc)


@router.post("/reference-patterns/search", response_model=list[ReferencePatternCardResponse])
def search_reference_patterns(
    req: ReferencePatternSearchRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ReferencePatternCardResponse]:
    del user
    return search_pattern_cards(db, query=req.query, category=req.category, limit=req.limit)


@router.post("/reference-mechanism-profile/build", response_model=ReferenceMechanismProfileResponse)
def build_reference_profile(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReferenceMechanismProfileResponse:
    del user
    return build_reference_mechanism_profile(db)


@router.get("/reference-mechanism-profile", response_model=ReferenceMechanismProfileResponse | None)
def get_reference_profile(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReferenceMechanismProfileResponse | None:
    del user
    return latest_reference_profile(db)


@router.post("/projects/{project_id}/worldview-tournament")
def worldview_tournament(project_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict[str, object]]:
    try:
        return [_row(row) for row in run_worldview_tournament(db, project_id, user)]
    except EpicNovelError as exc:
        _bad_request(exc)


@router.post("/projects/{project_id}/world-bible")
def world_bible(project_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, object]:
    try:
        return _row(generate_world_bible(db, project_id, user))
    except EpicNovelError as exc:
        _bad_request(exc)


@router.post("/projects/{project_id}/world-atlas")
def world_atlas(project_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, object]:
    try:
        return _row(generate_world_atlas(db, project_id, user))
    except EpicNovelError as exc:
        _bad_request(exc)


@router.post("/projects/{project_id}/power-system")
def power_system(project_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, object]:
    try:
        return _row(forge_power_system(db, project_id, user))
    except EpicNovelError as exc:
        _bad_request(exc)


@router.post("/projects/{project_id}/ensemble-cast")
def ensemble_cast(project_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict[str, object]]:
    try:
        return [_row(row) for row in generate_ensemble_cast(db, project_id, user)]
    except EpicNovelError as exc:
        _bad_request(exc)


@router.post("/projects/{project_id}/global-spine")
def global_spine(project_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, object]:
    try:
        return _row(generate_global_spine(db, project_id, user))
    except EpicNovelError as exc:
        _bad_request(exc)


@router.post("/projects/{project_id}/bible/manifest")
def bible_manifest(project_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, object]:
    try:
        result = generate_bible_manifest(db, project_id, user)
        return {"bible": _row(result["bible"]), "sections": [_row(section) for section in result["sections"]]}
    except EpicNovelError as exc:
        _bad_request(exc)


@router.post("/projects/{project_id}/bible/sections/{section_key}/generate")
def bible_section(project_id: UUID, section_key: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, object]:
    try:
        return _row(generate_bible_section(db, project_id, section_key, user))
    except EpicNovelError as exc:
        _bad_request(exc)


@router.post("/projects/{project_id}/bible/coverage-ci")
def bible_coverage_ci(project_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict[str, object]]:
    try:
        return [_row(row) for row in run_bible_coverage_ci(db, project_id, user)]
    except EpicNovelError as exc:
        _bad_request(exc)


@router.post("/projects/{project_id}/bible/repair")
def bible_repair(project_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict[str, object]]:
    try:
        return [_row(row) for row in repair_bible_coverage(db, project_id, user)]
    except EpicNovelError as exc:
        _bad_request(exc)


@router.get("/projects/{project_id}/bible/export", response_model=None)
def bible_export(
    project_id: UUID,
    format: str = Query(default="markdown", pattern="^(markdown|md|json|docx)$"),
    mode: str = Query(default="download", pattern="^(download|metadata)$"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response | dict[str, object]:
    fmt = "md" if format in {"markdown", "docx"} else format
    try:
        if mode == "metadata":
            return export_bible_file_metadata(db, project_id, fmt, user)
        content, media_type, filename = export_bible(db, project_id, fmt, user)
    except EpicNovelError as exc:
        _bad_request(exc)
    quoted = quote(filename)
    return Response(content=content.encode("utf-8"), media_type=media_type, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"})


@router.post("/projects/{project_id}/volume-contracts")
def volume_contracts(project_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict[str, object]]:
    try:
        return [_row(row) for row in generate_volume_contracts(db, project_id, user)]
    except EpicNovelError as exc:
        _bad_request(exc)


@router.post("/projects/{project_id}/arc-ledgers")
def arc_ledgers(project_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, object]:
    try:
        return generate_arc_ledgers(db, project_id, user)
    except EpicNovelError as exc:
        _bad_request(exc)


@router.post("/projects/{project_id}/outline-ci")
def outline_ci(project_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, object]:
    try:
        return run_outline_ci(db, project_id, user)
    except EpicNovelError as exc:
        _bad_request(exc)


@router.post("/projects/{project_id}/outline-repair")
def outline_repair(project_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, object]:
    try:
        return repair_outline(db, project_id, user)
    except EpicNovelError as exc:
        _bad_request(exc)


@router.post("/projects/{project_id}/volumes/{volume_number}/compile-chapters", response_model=list[EpicChapterContractResponse])
def compile_chapters(project_id: UUID, volume_number: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[EpicChapterContractResponse]:
    try:
        return compile_chapter_contracts(db, project_id, volume_number, user)
    except EpicNovelError as exc:
        _bad_request(exc)


@router.get("/projects/{project_id}/chapters/{chapter_number}/context-pack")
def chapter_context_pack(project_id: UUID, chapter_number: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, object]:
    try:
        return build_chapter_context_pack(db, project_id, chapter_number, user)
    except EpicNovelError as exc:
        _bad_request(exc)


@router.post("/projects/{project_id}/chapters/{chapter_number}/execute", response_model=EpicChapterResponse)
def execute_project_chapter(project_id: UUID, chapter_number: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> EpicChapterResponse:
    try:
        return execute_chapter(db, project_id, chapter_number, user)
    except EpicNovelError as exc:
        _bad_request(exc)


@router.post("/projects/{project_id}/chapters/{chapter_number}/check")
def check_project_chapter(project_id: UUID, chapter_number: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict[str, object]]:
    try:
        return [_row(row) for row in check_chapter(db, project_id, chapter_number, user)]
    except EpicNovelError as exc:
        _bad_request(exc)


@router.post("/projects/{project_id}/chapters/{chapter_number}/repair", response_model=EpicChapterResponse)
def repair_project_chapter(project_id: UUID, chapter_number: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> EpicChapterResponse:
    try:
        return repair_chapter(db, project_id, chapter_number, user)
    except EpicNovelError as exc:
        _bad_request(exc)


@router.post("/projects/{project_id}/volumes/{volume_number}/generate", response_model=list[EpicChapterResponse])
def generate_project_volume(project_id: UUID, volume_number: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[EpicChapterResponse]:
    try:
        return generate_volume(db, project_id, volume_number, user)
    except EpicNovelError as exc:
        _bad_request(exc)


@router.post("/projects/{project_id}/generate-batch", response_model=EpicBatchGenerationResponse)
def generate_project_batch(project_id: UUID, req: EpicBatchGenerationRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> EpicBatchGenerationResponse:
    try:
        return generate_batch(db, project_id, req, user)
    except EpicNovelError as exc:
        _bad_request(exc)


@router.get("/projects/{project_id}/dashboard", response_model=EpicDashboardResponse)
def project_dashboard(project_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> EpicDashboardResponse:
    try:
        return dashboard(db, project_id, user)
    except EpicNovelError as exc:
        _bad_request(exc)


@router.get("/projects/{project_id}/export", response_model=None)
def project_export(
    project_id: UUID,
    format: str = Query(default="markdown", pattern="^(markdown|md|txt|json)$"),
    mode: str = Query(default="download", pattern="^(download|metadata)$"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response | dict[str, object]:
    fmt = "md" if format == "markdown" else format
    try:
        if mode == "metadata":
            return export_novel_file_metadata(db, project_id, fmt, user)
        content, media_type, filename = export_novel(db, project_id, fmt, user)
    except EpicNovelError as exc:
        _bad_request(exc)
    quoted = quote(filename)
    return Response(content=content.encode("utf-8"), media_type=media_type, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"})
