"""Task 相关请求 / 响应 schema。"""
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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
        description="原始视频 URL (抖音/B站/YouTube 等)",
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="可选业务配置 (如 raw_text)",
    )


class CreateTaskResponse(BaseModel):
    task_id: UUID
    status: str


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
