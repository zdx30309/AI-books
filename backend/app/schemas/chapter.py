"""Chapter 相关响应 schema。"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


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
