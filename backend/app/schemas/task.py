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
    title: str | None = None
    error_message: str | None = None
    created_at: datetime


class TaskListItem(BaseModel):
    """任务列表卡片视图。"""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_url: str
    status: str
    progress: int
    current_step: str
    cover_url: str | None = None
    created_at: datetime
    title: str | None = None       # 取自 config.title 或第一章标题
    word_count: int = 0            # 全文字数 (config.markdown_chars 或章节累加)
    chapter_count: int = 0


class TaskListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[TaskListItem]


class StatsResponse(BaseModel):
    total_books: int = Field(..., description="已创作书稿数 (status=completed)")
    total_words: int = Field(..., description="累计字数")
    saved_hours: float = Field(..., description="估算节省时间 (按 1500 字/人/小时折算)")
    in_progress: int = Field(..., description="正在处理中的任务数")
    failed: int = Field(..., description="失败任务数")


class MarkdownResponse(BaseModel):
    task_id: UUID
    markdown: str
    char_count: int


class StageContentResponse(BaseModel):
    task_id: UUID
    stage: str                     # writer / writer_repaired / polish / refine / format
    content: str
    char_count: int
