"""Schemas for family long-form novel generation."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FamilyLongNovelCreateRequest(BaseModel):
    premise: str = Field(..., min_length=1, description="核心创意")
    family_setup: str | None = Field(default=None, description="家庭关系设定")
    conflict_tags: list[str] = Field(default_factory=list, max_length=30)
    source_type: str = Field(default="manual", max_length=64)
    source_text: str | None = None
    target_words: int = Field(default=200000, ge=1000, le=500000)
    target_chapters: int = Field(default=50, ge=1, le=80)
    generation_mode: Literal["cheap", "standard", "high_quality"] = "standard"
    style: str = Field(default="强情节现实家长里短", max_length=128)
    forbidden_rules: str | None = None


class LongNovelAssistRequest(BaseModel):
    module_type: str = Field(default="family_sisters_in_law_longform", max_length=80)
    field: Literal["premise", "family_setup", "conflict_tags", "source_text", "forbidden_rules"] = "premise"
    current_values: dict[str, Any] = Field(default_factory=dict)


class LongNovelAssistResponse(BaseModel):
    field: str
    content: str = ""
    conflict_tags: list[str] = Field(default_factory=list)


class FamilyLongNovelBatchRequest(BaseModel):
    from_chapter: int = Field(default=1, ge=1, le=80)
    to_chapter: int = Field(default=3, ge=1, le=80)
    retry_failed: bool = True


class FamilyLongNovelExtendRequest(BaseModel):
    target_words: int = Field(default=200000, ge=1000, le=500000)
    future_chapter_words: int = Field(default=4000, ge=1200, le=12000)
    expansion_notes: str | None = Field(default=None, max_length=2000)


class LongNovelActionResponse(BaseModel):
    project_id: UUID
    status: str
    message: str


class LongNovelBatchResponse(BaseModel):
    project_id: UUID
    status: str
    generated: list[int]
    failed: list[dict[str, Any]]
    skipped: list[int]


class LongNovelProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID | None = None
    title: str
    type: str
    genre_label: str
    premise: str
    family_setup: str | None = None
    conflict_tags: list[str] = Field(default_factory=list)
    source_type: str
    source_text: str | None = None
    target_words: int
    target_chapters: int
    generation_mode: str
    style: str
    forbidden_rules: str | None = None
    status: str
    current_chapter: int
    total_words: int
    created_at: datetime
    updated_at: datetime


class LongNovelBibleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    theme: str | None = None
    core_promise: str | None = None
    emotional_hook: str | None = None
    family_background: str | None = None
    world_rules: list[Any] = Field(default_factory=list)
    style_rules: list[Any] = Field(default_factory=list)
    forbidden_rules: list[Any] = Field(default_factory=list)
    ending_direction: str | None = None
    main_conflict: str | None = None
    raw_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class LongNovelFamilyMemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    name: str
    family_role: str | None = None
    public_image: str | None = None
    private_desire: str | None = None
    grievance: str | None = None
    leverage: str | None = None
    weakness: str | None = None
    secret: str | None = None
    relationship_to_heroine: str | None = None
    relationship_map: dict[str, Any] = Field(default_factory=dict)
    voice_rules: dict[str, Any] = Field(default_factory=dict)
    arc_start: str | None = None
    arc_end: str | None = None
    ending: str | None = None
    raw_json: dict[str, Any] = Field(default_factory=dict)


class LongNovelFamilyRelationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    from_member: str
    to_member: str
    relation_type: str | None = None
    surface_relation: str | None = None
    hidden_conflict: str | None = None
    interest_conflict: str | None = None
    emotional_debt: str | None = None
    current_tension_level: int
    raw_json: dict[str, Any] = Field(default_factory=dict)


class LongNovelConflictLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    conflict_key: str
    name: str
    conflict_type: str | None = None
    description: str | None = None
    introduced_chapter: int | None = None
    last_escalated_chapter: int | None = None
    planned_explosion_chapter: int | None = None
    planned_resolution_chapter: int | None = None
    status: str
    importance: str


class LongNovelChapterPlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    chapter_number: int
    volume_number: int | None = None
    title: str
    target_words: int
    chapter_goal: str | None = None
    domestic_conflict: str | None = None
    emotional_hook: str | None = None
    required_events: list[Any] = Field(default_factory=list)
    character_arc_move: list[Any] = Field(default_factory=list)
    conflict_lines_to_advance: list[Any] = Field(default_factory=list)
    forbidden_reveals: list[Any] = Field(default_factory=list)
    ending_hook: str | None = None
    scenes: list[Any] = Field(default_factory=list)
    status: str
    raw_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class LongNovelChapterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    chapter_number: int
    title: str
    content: str
    summary: str | None = None
    word_count: int
    status: str
    version: int
    raw_model_output: str | None = None
    created_at: datetime
    updated_at: datetime


class LongNovelContinuityIssueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    chapter_number: int
    severity: str
    issue_type: str | None = None
    description: str
    evidence: str | None = None
    suggested_fix: str | None = None
    status: str
    created_at: datetime


class LongNovelGenerationLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    chapter_number: int | None = None
    action: str
    provider: str | None = None
    model: str | None = None
    prompt_tokens: int
    cached_prompt_tokens: int = 0
    completion_tokens: int
    reasoning_tokens: int = 0
    estimated_cost: float
    service_tier: str | None = None
    prompt_cache_key: str | None = None
    prompt_prefix_hash: str | None = None
    response_id: str | None = None
    status: str
    error: str | None = None
    created_at: datetime


class LongNovelProjectDetailResponse(BaseModel):
    project: LongNovelProjectResponse
    bible: LongNovelBibleResponse | None = None
    members: list[LongNovelFamilyMemberResponse] = Field(default_factory=list)
    relations: list[LongNovelFamilyRelationResponse] = Field(default_factory=list)
    conflict_lines: list[LongNovelConflictLineResponse] = Field(default_factory=list)
    chapter_plans: list[LongNovelChapterPlanResponse] = Field(default_factory=list)
    chapters: list[LongNovelChapterResponse] = Field(default_factory=list)
    issues: list[LongNovelContinuityIssueResponse] = Field(default_factory=list)
    logs: list[LongNovelGenerationLogResponse] = Field(default_factory=list)


class LongNovelProjectListResponse(BaseModel):
    total: int
    items: list[LongNovelProjectResponse]
