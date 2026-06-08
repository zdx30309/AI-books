"""API router package."""
from app.api.routers.auth import router as auth_router
from app.api.routers.covers import router as covers_router
from app.api.routers.data_analysis import router as data_analysis_router
from app.api.routers.health import router as health_router
from app.api.routers.long_novel import router as long_novel_router
from app.api.routers.polish import router as polish_router
from app.api.routers.stats import router as stats_router
from app.api.routers.system import router as system_router
from app.api.routers.tasks import router as tasks_router
from app.api.routers.weread import router as weread_router

__all__ = [
    "auth_router",
    "covers_router",
    "data_analysis_router",
    "health_router",
    "long_novel_router",
    "polish_router",
    "stats_router",
    "system_router",
    "tasks_router",
    "weread_router",
]
