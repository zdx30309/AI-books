"""Standalone cover generation schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CoverJobItem(BaseModel):
    index: int
    title: str
    author: str | None = None
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
    created_at: datetime
    updated_at: datetime
    items: list[CoverJobItem]
