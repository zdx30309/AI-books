"""API router package."""
from app.api.routers.health import router as health_router
from app.api.routers.stats import router as stats_router
from app.api.routers.tasks import router as tasks_router

__all__ = ["health_router", "stats_router", "tasks_router"]
