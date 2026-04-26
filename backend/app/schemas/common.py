"""通用 schema (健康检查等)。"""
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str = "1.0.0"
