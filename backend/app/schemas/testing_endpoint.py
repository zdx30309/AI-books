"""Schemas for the OpenAI-only Testing Endpoint."""
from __future__ import annotations

from pydantic import BaseModel, Field


class TestingAnalysisResponse(BaseModel):
    task_id: str
    status: str = "analysis_complete"
    message: str = "Analysis Complete"


class TestingGenerateArticleRequest(BaseModel):
    video_url: str | None = Field(default=None, max_length=4096)
    video_urls: list[str] | None = Field(default=None, max_length=50)
    raw_video_text: str | None = Field(default=None, max_length=1_000_000)


class TestingGeneratedArticle(BaseModel):
    video_url: str
    article: str
    status: str = "completed"


class TestingArticleResponse(BaseModel):
    task_id: str
    status: str
    article: str | None = None
    articles: list[TestingGeneratedArticle] = Field(default_factory=list)


class TestingTaskResponse(BaseModel):
    task_id: str
    status: str
    current_step: str
    message: str | None = None
    article: str | None = None
    articles: list[TestingGeneratedArticle] = Field(default_factory=list)
    error_message: str | None = None
