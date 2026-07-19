"""Schemas for the OpenAI-only Testing Endpoint."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class TestingAnalysisResponse(BaseModel):
    task_id: str
    status: str = "analysis_complete"
    message: str = "参考书全分片压缩完成"
    reference_book_count: int
    reference_chars: int
    reference_chunk_count: int
    compressed_chars: int
    reference_input_mode: str = "chunk_compressed"


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


class TestingRewriteCompletedArticleRequest(BaseModel):
    original_text: str = Field(min_length=500, max_length=100_000)
    transfer_strength: Literal["strong", "maximum"] = "maximum"


class TestingRewriteCompletedArticleResponse(BaseModel):
    task_id: str
    status: str = "rewrite_complete"
    audit_notes: str
    candidate_text: str
    original_sha256: str
    candidate_sha256: str
    organization_policy_version: str
    surface_change_percent: float
    surface_change_method: str
    model: str
    openai_calls: list[dict[str, Any]] = Field(default_factory=list)


class TestingZhuqueFeedbackRequest(BaseModel):
    baseline_report: dict[str, Any]
    candidate_report: dict[str, Any]


class TestingZhuqueFeedbackResponse(BaseModel):
    task_id: str
    status: str = "verified_feedback_recorded"
    baseline_human_percent: float
    candidate_human_percent: float
    human_delta_percent_points: float
    experiment_candidate_sha256: str
    verified_observation_count: int


class TestingTaskResponse(BaseModel):
    task_id: str
    status: str
    current_step: str
    message: str | None = None
    article: str | None = None
    articles: list[TestingGeneratedArticle] = Field(default_factory=list)
    error_message: str | None = None
