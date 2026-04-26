"""Health check routers."""
from fastapi import APIRouter

from app.core.config import settings
from app.schemas.common import HealthResponse

router = APIRouter(tags=["Health"])


@router.get("/", response_model=HealthResponse, summary="Root health check")
def root() -> HealthResponse:
    return HealthResponse(status="ok", app=settings.APP_NAME)


@router.get("/health", response_model=HealthResponse, summary="Health check")
def health() -> HealthResponse:
    return HealthResponse(status="ok", app=settings.APP_NAME)
