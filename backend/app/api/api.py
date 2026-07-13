"""API router aggregation."""
from fastapi import APIRouter

from app.api.routers import (
    aigc_review_router,
    auth_router,
    covers_router,
    data_analysis_router,
    epic_novel_router,
    health_router,
    long_novel_family_router,
    long_novel_router,
    polish_router,
    public_domain_rewrite_router,
    stats_router,
    system_router,
    tasks_router,
    weread_router,
)

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(aigc_review_router)
api_router.include_router(long_novel_router)
api_router.include_router(long_novel_family_router)
api_router.include_router(auth_router)
api_router.include_router(covers_router)
api_router.include_router(data_analysis_router)
api_router.include_router(epic_novel_router)
api_router.include_router(tasks_router)
api_router.include_router(stats_router)
api_router.include_router(polish_router)
api_router.include_router(public_domain_rewrite_router)
api_router.include_router(system_router)
api_router.include_router(weread_router)
