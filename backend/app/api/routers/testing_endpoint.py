"""OpenAI-only Testing Endpoint API."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.routers.auth import get_current_user
from app.db.session import get_db
from app.models import Task, User
from app.schemas.testing_endpoint import (
    TestingAnalysisResponse,
    TestingArticleResponse,
    TestingGenerateArticleRequest,
    TestingTaskResponse,
)
from app.services.testing_endpoint import (
    ANALYSIS_COMPLETE_MESSAGE,
    TestingEndpointError,
    analyze_reference_books,
    generate_testing_article,
    testing_payload,
    uploaded_reference_book_from_bytes,
)

router = APIRouter(prefix="/api/v1/testing", tags=["Testing Endpoint"])


@router.post("/analyze-reference-books", response_model=TestingAnalysisResponse)
async def analyze_testing_reference_books(
    books: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TestingAnalysisResponse:
    try:
        if not books:
            raise TestingEndpointError("请至少上传 1 本参考书。", status_code=400)
        refs = [
            uploaded_reference_book_from_bytes(book.filename or f"book-{index + 1}", await book.read())
            for index, book in enumerate(books)
        ]
        task = analyze_reference_books(db, user=user, books=refs)
        return TestingAnalysisResponse(task_id=str(task.id), message=ANALYSIS_COMPLETE_MESSAGE)
    except TestingEndpointError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/generate-article", response_model=TestingArticleResponse)
def generate_testing_article_endpoint(
    task_id: UUID,
    body: TestingGenerateArticleRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TestingArticleResponse:
    task = _get_owned_testing_task(db, task_id, user)
    try:
        articles = generate_testing_article(
            db,
            task=task,
            video_urls=[*(body.video_urls or []), *([body.video_url] if body.video_url else [])],
            raw_video_text=body.raw_video_text,
        )
        return TestingArticleResponse(
            task_id=str(task.id),
            status=task.status,
            article=articles[0]["article"] if len(articles) == 1 else None,
            articles=articles,
        )
    except TestingEndpointError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/tasks/{task_id}", response_model=TestingTaskResponse)
def get_testing_task(
    task_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TestingTaskResponse:
    task = _get_owned_testing_task(db, task_id, user)
    return TestingTaskResponse(**testing_payload(task))


def _get_owned_testing_task(db: Session, task_id: UUID, user: User) -> Task:
    task = db.query(Task).filter(Task.id == task_id, Task.owner_id == user.id).first()
    if task is None:
        raise HTTPException(status_code=404, detail="Testing Endpoint 任务不存在。")
    if (task.config or {}).get("category") != "testing_endpoint":
        raise HTTPException(status_code=404, detail="Testing Endpoint 任务不存在。")
    return task
