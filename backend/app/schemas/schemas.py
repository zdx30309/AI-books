"""
Gravity Singularity - Pydantic v2 请求 / 响应模型
"""
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ============================================================
# 通用
# ============================================================


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str = "1.0.0"


# ============================================================
# POST /api/v1/tasks
# ============================================================


class CreateTaskRequest(BaseModel):
    """建任务请求体。

    config 中可携带:
      - raw_text  : 预解析好的视频纯文本 (推荐, MVP demo 路径)
      - 其它业务自定义键值
    """
    url: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        description="原始视频 URL (YouTube/小红书/微信视频号/抖音/B站等)",
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="可选业务配置 (如 raw_text)",
    )


class CreateTaskResponse(BaseModel):
    task_id: UUID
    status: str


# ============================================================
# GET /api/v1/tasks/{task_id}
# ============================================================


class TaskStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_url: str
    status: str                    # pending / processing / completed / failed
    progress: int                  # 0-100
    current_step: str
    cover_url: str | None = None
    error_message: str | None = None
    created_at: datetime


# ============================================================
# GET /api/v1/tasks/{task_id}/chapters
# ============================================================


class ChapterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    chapter_num: int
    title: str
    content: str
    word_count: int
    created_at: datetime


class ChaptersListResponse(BaseModel):
    task_id: UUID
    total: int
    chapters: list[ChapterResponse]
