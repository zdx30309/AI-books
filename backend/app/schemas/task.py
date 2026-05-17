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


class BatchCreateTaskRequest(BaseModel):
    urls: list[str] = Field(..., min_length=1, max_length=500, description="候选链接列表，最多 500 条")
    config: dict[str, Any] = Field(default_factory=dict, description="批量任务共享配置")


class BatchCreateTaskItem(BaseModel):
    url: str
    task_id: UUID | None = None
    status: str
    reason: str | None = None


class BatchCreateTaskResponse(BaseModel):
    created: list[BatchCreateTaskItem]
    skipped: list[BatchCreateTaskItem]
    total_created: int
    total_skipped: int


class BatchRetryTaskRequest(BaseModel):
    task_ids: list[UUID] | None = Field(default=None, max_length=1000, description="指定要继续生成的失败任务；为空时继续全部失败任务")


class BatchRetryTaskItem(BaseModel):
    task_id: UUID
    status: str
    reason: str | None = None


class BatchRetryTaskResponse(BaseModel):
    retried: list[BatchRetryTaskItem]
    skipped: list[BatchRetryTaskItem]
    total_retried: int
    total_skipped: int


class BatchRegenerateTaskRequest(BaseModel):
    task_ids: list[UUID] = Field(..., min_length=1, max_length=500, description="指定要从头重新生成的任务")


class BatchRegenerateTaskItem(BaseModel):
    task_id: UUID
    status: str
    reason: str | None = None


class BatchRegenerateTaskResponse(BaseModel):
    regenerated: list[BatchRegenerateTaskItem]
    skipped: list[BatchRegenerateTaskItem]
    total_regenerated: int
    total_skipped: int


class BatchDownloadRequest(BaseModel):
    task_ids: list[UUID] = Field(..., min_length=1, max_length=500, description="要打包下载的任务 ID")
    fmt: str = Field(default="docx", pattern="^(md|docx)$", description="下载格式")


class TaskCategoryOption(BaseModel):
    id: str
    label: str
    core_feature: str
    directions: str
    prompt_status: str


class TaskCategoryListResponse(BaseModel):
    items: list[TaskCategoryOption]


class TaskStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_url: str
    status: str                    # pending / processing / completed / failed
    progress: int                  # 0-100
    current_step: str
    cover_url: str | None = None
    cover_status: str | None = None
    cover_error: str | None = None
    title: str | None = None
    bookshelf: str | None = None
    category: str | None = None
    category_label: str | None = None
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
    bookshelf: str | None = None   # 用户手动维护的书架 / 分类
    category: str | None = None
    category_label: str | None = None
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


class TaskMetadataUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    bookshelf: str | None = Field(default=None, max_length=80)
    category: str | None = Field(default=None, max_length=80)


class RegenerateSelectionRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=20000, description="待重新生成的选中文本")


class RegenerateSelectionResponse(BaseModel):
    original: str
    regenerated: str
    original_chars: int
    regenerated_chars: int
