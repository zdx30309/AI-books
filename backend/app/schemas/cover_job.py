"""Standalone cover generation schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from typing import Any

from pydantic import BaseModel, Field


class CoverJobItem(BaseModel):
    index: int
    title: str
    author: str | None = None
    category: str | None = None
    category_label: str | None = None
    status: str
    image_url: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CoverJobResponse(BaseModel):
    job_id: str
    status: str
    progress: int
    total: int
    completed: int
    failed: int
    current_step: str
    source_filename: str | None = None
    default_category: str | None = None
    default_category_label: str | None = None
    created_at: datetime
    updated_at: datetime
    items: list[CoverJobItem]


class CoverJobListResponse(BaseModel):
    items: list[CoverJobResponse]


class CoverJobBatchRequest(BaseModel):
    job_ids: list[str]
    action: Literal["pause", "resume", "delete"]


class CoverJobBatchItem(BaseModel):
    job_id: str
    status: str
    reason: str | None = None


class CoverJobBatchResponse(BaseModel):
    items: list[CoverJobBatchItem]
    total: int


class CoverJobItemBatchDeleteRequest(BaseModel):
    indexes: list[int] = Field(default_factory=list)


class CoverJobItemBatchRetryRequest(BaseModel):
    indexes: list[int] = Field(default_factory=list)
