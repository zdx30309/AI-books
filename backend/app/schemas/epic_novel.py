"""Pydantic schemas for the Epic Novel Engine."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EpicNovelCreateRequest(BaseModel):
    title: str = Field(default="Untitled Epic Novel", max_length=255)
    premise: str = Field(..., min_length=1)
    genre: str = Field(default="ancient_transmigration_mystery_xianxia", max_length=255)
    core_pleasures: list[str] = Field(default_factory=list)
    target_words: int = Field(default=3_000_000, ge=1_000)
    target_volumes: int = Field(default=12, ge=1)
    target_chapters: int = Field(default=700, ge=1)
    bible_target_words: int = Field(default=100_000, ge=10_000)
    reference_booklist_path: str | None = None
    sonovel_home: str | None = None
    sonovel_download_path: str | None = None
    reference_raw_path: str | None = None
    generation_mode: Literal["cheap", "standard", "high_quality"] = "standard"
    style: str | None = None
    forbidden_rules: str | None = None


class ReferenceBooklistImportRequest(BaseModel):
    seed_path: str | None = None
    raw_markdown: str | None = None


class ReferenceDownloaderSearchRequest(BaseModel):
    title: str
    author: str | None = None


class ReferenceDownloaderDownloadRequest(BaseModel):
    task_id: UUID | None = None
    title: str | None = None
    author: str | None = None
    selected_result: dict[str, Any] | None = None


class ReferenceBooksImportRequest(BaseModel):
    path: str | None = None
    paths: list[str] = Field(default_factory=list)


class ReferenceBooksIngestRequest(BaseModel):
    book_id: UUID | None = None
    source_path: str | None = None


class ReferencePatternSearchRequest(BaseModel):
    query: str = ""
    category: str | None = None
    limit: int = Field(default=0, ge=0)


class EpicBatchGenerationRequest(BaseModel):
    from_chapter: int = Field(default=1, ge=1)
    to_chapter: int = Field(default=3, ge=1)


class EpicModelBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class EpicNovelProjectResponse(EpicModelBase):
    id: UUID
    owner_id: UUID | None = None
    title: str
    genre: str
    premise: str
    target_words: int
    target_volumes: int
    target_chapters: int
    bible_target_words: int
    style: str | None = None
    forbidden_rules: str | None = None
    status: str
    current_volume: int
    current_chapter: int
    total_words: int
    generation_mode: str
    editor_model: str | None = None
    author_model: str | None = None
    cheap_model: str | None = None
    reference_profile_id: UUID | None = None
    reference_booklist_path: str | None = None
    sonovel_home: str | None = None
    sonovel_download_path: str | None = None
    reference_raw_path: str | None = None
    raw_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class ReferenceBooklistItemResponse(EpicModelBase):
    id: UUID
    title: str
    author: str | None = None
    tier: int
    category: str
    genre: str | None = None
    source_type: str
    source_path: str | None = None
    source_url: str | None = None
    corpus_role: list[Any] = Field(default_factory=list)
    priority: int
    tags: list[Any] = Field(default_factory=list)
    notes: str | None = None
    likely_use: list[Any] = Field(default_factory=list)
    confidence: float
    ingest_allowed: bool
    needs_verification: bool
    download_status: str
    local_raw_path: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class ReferenceDownloadTaskResponse(EpicModelBase):
    id: UUID
    booklist_item_id: UUID | None = None
    provider: str
    title: str
    author: str | None = None
    query: str
    status: str
    search_results: list[Any] = Field(default_factory=list)
    selected_result: dict[str, Any] | None = None
    local_download_path: str | None = None
    imported_raw_path: str | None = None
    file_hash: str | None = None
    generated_card_count: int = 0
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class ReferenceBookResponse(EpicModelBase):
    id: UUID
    booklist_item_id: UUID | None = None
    download_task_id: UUID | None = None
    title: str
    author: str | None = None
    genre: str | None = None
    tier: int
    category: str
    source_path: str
    storage_type: str
    file_hash: str
    language: str
    status: str
    created_at: datetime
    updated_at: datetime


class ReferencePatternCardResponse(EpicModelBase):
    id: UUID
    source_book_id: UUID | None = None
    pattern_key: str
    category: str
    name: str
    description: str
    genre: str | None = None
    function: str | None = None
    conditions: list[Any] = Field(default_factory=list)
    beats: list[Any] = Field(default_factory=list)
    common_pitfalls: list[Any] = Field(default_factory=list)
    best_use: list[Any] = Field(default_factory=list)
    required_global_link: str | None = None
    originality_notes: str | None = None
    tags: list[Any] = Field(default_factory=list)
    source_section_ids: list[Any] = Field(default_factory=list)
    source_analysis_ids: list[Any] = Field(default_factory=list)
    source_book_ids: list[Any] = Field(default_factory=list)
    source_card_ids: list[Any] = Field(default_factory=list)
    evidence_count: int = 1
    raw_text_included: bool = False
    extraction_model_provider: str | None = None
    extraction_model_name: str | None = None
    output_hash: str | None = None
    raw_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ReferenceMechanismProfileResponse(EpicModelBase):
    id: UUID
    project_id: UUID | None = None
    corpus_id: UUID | None = None
    profile_name: str
    source_book_count: int
    source_card_count: int
    genre: str | None = None
    tier_weights: dict[str, Any] = Field(default_factory=dict)
    core_pleasure_mechanisms: list[Any] = Field(default_factory=list)
    case_structure_patterns: list[Any] = Field(default_factory=list)
    court_power_patterns: list[Any] = Field(default_factory=list)
    bureaucracy_conflict_patterns: list[Any] = Field(default_factory=list)
    heroine_agency_patterns: list[Any] = Field(default_factory=list)
    character_arc_patterns: list[Any] = Field(default_factory=list)
    foreshadow_patterns: list[Any] = Field(default_factory=list)
    power_progression_patterns: list[Any] = Field(default_factory=list)
    longform_pacing_patterns: list[Any] = Field(default_factory=list)
    anti_cliche_rules: list[Any] = Field(default_factory=list)
    common_pitfalls: list[Any] = Field(default_factory=list)
    originality_guidance: list[Any] = Field(default_factory=list)
    raw_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class EpicChapterContractResponse(EpicModelBase):
    id: UUID
    project_id: UUID
    volume_number: int
    chapter_number: int
    title: str
    target_words: int
    chapter_goal: str | None = None
    plot_function: str | None = None
    global_spine_function: str | None = None
    world_atlas_function: str | None = None
    bureaucracy_function: str | None = None
    local_arc_function: str | None = None
    core_pleasure: str | None = None
    reference_pattern_cards_used: list[Any] = Field(default_factory=list)
    must_execute: list[Any] = Field(default_factory=list)
    must_not_reveal: list[Any] = Field(default_factory=list)
    ending_hook: str | None = None
    status: str
    raw_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class EpicChapterResponse(EpicModelBase):
    id: UUID
    project_id: UUID
    volume_number: int
    chapter_number: int
    title: str
    draft_content: str | None = None
    repaired_content: str | None = None
    accepted_content: str | None = None
    summary: str | None = None
    ending_hook: str | None = None
    word_count: int
    status: str
    version: int
    repair_count: int
    raw_model_output: str | None = None
    created_at: datetime
    updated_at: datetime


class EpicAcceptanceReportResponse(EpicModelBase):
    id: UUID
    project_id: UUID
    chapter_number: int
    outline_execution_score: float
    character_arc_score: float
    power_legality_score: float
    foreshadow_score: float
    pleasure_score: float
    continuity_score: float
    anti_cliche_score: float
    overall_status: str
    issues_json: list[Any] = Field(default_factory=list)
    created_at: datetime


class EpicGenerationLogResponse(EpicModelBase):
    id: UUID
    project_id: UUID | None = None
    action: str
    provider: str | None = None
    model: str | None = None
    bible_id: UUID | None = None
    section_key: str | None = None
    volume_number: int | None = None
    chapter_number: int | None = None
    prompt_tokens: int
    completion_tokens: int
    estimated_cost: float
    status: str
    error: str | None = None
    created_at: datetime


class EpicNovelProjectDetailResponse(BaseModel):
    project: EpicNovelProjectResponse
    world_bible: dict[str, Any] | None = None
    world_atlas: dict[str, Any] | None = None
    bureaucracy_system: dict[str, Any] | None = None
    power_system: dict[str, Any] | None = None
    master_bible: dict[str, Any] | None = None
    worldview_candidates: list[dict[str, Any]] = Field(default_factory=list)
    nations: list[dict[str, Any]] = Field(default_factory=list)
    map_regions: list[dict[str, Any]] = Field(default_factory=list)
    institutions: list[dict[str, Any]] = Field(default_factory=list)
    factions: list[dict[str, Any]] = Field(default_factory=list)
    characters: list[dict[str, Any]] = Field(default_factory=list)
    character_arcs: list[dict[str, Any]] = Field(default_factory=list)
    global_spine: dict[str, Any] | None = None
    bible_sections: list[dict[str, Any]] = Field(default_factory=list)
    volume_contracts: list[dict[str, Any]] = Field(default_factory=list)
    foreshadows: list[dict[str, Any]] = Field(default_factory=list)
    reader_questions: list[dict[str, Any]] = Field(default_factory=list)
    pleasure_beats: list[dict[str, Any]] = Field(default_factory=list)
    chapter_contracts: list[EpicChapterContractResponse] = Field(default_factory=list)
    chapters: list[EpicChapterResponse] = Field(default_factory=list)
    acceptance_reports: list[EpicAcceptanceReportResponse] = Field(default_factory=list)
    issues: list[dict[str, Any]] = Field(default_factory=list)
    retrieval_traces: list[dict[str, Any]] = Field(default_factory=list)
    logs: list[EpicGenerationLogResponse] = Field(default_factory=list)


class EpicNovelProjectListResponse(BaseModel):
    total: int
    items: list[EpicNovelProjectResponse]


class EpicBatchGenerationResponse(BaseModel):
    project_id: UUID
    generated: list[int] = Field(default_factory=list)
    failed: list[dict[str, Any]] = Field(default_factory=list)
    skipped: list[int] = Field(default_factory=list)


class EpicDashboardResponse(BaseModel):
    project_id: UUID
    current_words: int
    completed_chapters: int
    completed_volumes: int
    bible_coverage_score: float
    outline_execution_rate: float
    world_atlas_usage_rate: float
    bureaucracy_consistency_issues: int
    power_violations: int
    character_arc_gaps: int
    heroine_appendage_risk: int
    a_tier_disappearance_warnings: int
    unpaid_foreshadows: int
    pleasure_gap_warnings: int
    side_quest_warnings: int
    continuity_issues: int
    anti_cliche_issues: int
    storage_state_errors: int
    reference_safety_warnings: int
    reference_overfit_warnings: int
    download_import_status: dict[str, int] = Field(default_factory=dict)
    generated_pattern_card_count: int
    repair_count: int
    cost_estimate: float
    current_job: dict[str, Any] | None = None
    job_progress: float = 0.0
    estimated_remaining_cost: float = 0.0
    job_errors: list[str] = Field(default_factory=list)
    retry_status: str | None = None
