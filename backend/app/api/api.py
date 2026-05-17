"""API router aggregation."""
from fastapi import APIRouter

from app.api.routers import (
    auth_router,
    covers_router,
    health_router,
    polish_router,
    stats_router,
    system_router,
    tasks_router,
    weread_router,
)

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(covers_router)
api_router.include_router(tasks_router)
api_router.include_router(stats_router)
api_router.include_router(polish_router)
api_router.include_router(system_router)
api_router.include_router(weread_router)
