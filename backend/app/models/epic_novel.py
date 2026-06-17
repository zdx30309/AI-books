"""Epic Novel Engine ORM models."""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.session import Base


class EpicNovelProjectStatus:
    DRAFT = "draft"
    ACTIVE = "active"
    GENERATING = "generating"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class EpicChapterStatus:
    PLANNED = "planned"
    DRAFTING = "drafting"
    CHECKING = "checking"
    REPAIRING = "repairing"
    ACCEPTED = "accepted"
    NEED_REVIEW = "need_review"
    FAILED = "failed"


class EpicBibleStatus:
    PLANNING = "planning"
    GENERATING = "generating"
    CHECKING = "checking"
    COMPLETED = "completed"
    NEED_REVIEW = "need_review"
    FAILED = "failed"


class ReferenceDownloadStatus:
    PENDING = "pending"
    SEARCHING = "searching"
    FOUND = "found"
    NOT_FOUND = "not_found"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    IMPORTED = "imported"
    FAILED = "failed"
    SKIPPED = "skipped"
    MANUAL_REQUIRED = "manual_required"


class ReferenceBooklistItem(Base):
    __tablename__ = "reference_booklist_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(255), nullable=False, index=True)
    author = Column(String(255), nullable=True)
    tier = Column(Integer, nullable=False, default=4)
    category = Column(String(64), nullable=False, default="metadata_only")
    genre = Column(String(255), nullable=True)
    source_type = Column(String(64), nullable=False, default="sonovel_search")
    source_path = Column(Text, nullable=True)
    source_url = Column(Text, nullable=True)
    corpus_role = Column(JSONB, nullable=False, default=list)
    priority = Column(Integer, nullable=False, default=3)
    tags = Column(JSONB, nullable=False, default=list)
    notes = Column(Text, nullable=True)
    likely_use = Column(JSONB, nullable=False, default=list)
    confidence = Column(Float, nullable=False, default=0.5)
    ingest_allowed = Column(Boolean, nullable=False, default=True)
    needs_verification = Column(Boolean, nullable=False, default=True)
    download_status = Column(String(32), nullable=False, default=ReferenceDownloadStatus.PENDING, index=True)
    local_raw_path = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    download_tasks = relationship("ReferenceDownloadTask", back_populates="booklist_item", cascade="all, delete-orphan")
    books = relationship("ReferenceBook", back_populates="booklist_item")

    __table_args__ = (Index("ix_reference_booklist_title_author", "title", "author"),)


class ReferenceDownloadTask(Base):
    __tablename__ = "reference_download_tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booklist_item_id = Column(UUID(as_uuid=True), ForeignKey("reference_booklist_items.id", ondelete="CASCADE"), nullable=True, index=True)
    provider = Column(String(64), nullable=False, default="sonovel")
    title = Column(String(255), nullable=False)
    author = Column(String(255), nullable=True)
    query = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default=ReferenceDownloadStatus.PENDING, index=True)
    search_results = Column(JSONB, nullable=False, default=list)
    selected_result = Column(JSONB, nullable=True)
    local_download_path = Column(Text, nullable=True)
    imported_raw_path = Column(Text, nullable=True)
    file_hash = Column(String(128), nullable=True, index=True)
    generated_card_count = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    booklist_item = relationship("ReferenceBooklistItem", back_populates="download_tasks")
    books = relationship("ReferenceBook", back_populates="download_task")


class ReferenceBook(Base):
    __tablename__ = "reference_books"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booklist_item_id = Column(UUID(as_uuid=True), ForeignKey("reference_booklist_items.id", ondelete="SET NULL"), nullable=True, index=True)
    download_task_id = Column(UUID(as_uuid=True), ForeignKey("reference_download_tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    title = Column(String(255), nullable=False, index=True)
    author = Column(String(255), nullable=True)
    genre = Column(String(255), nullable=True)
    tier = Column(Integer, nullable=False, default=4)
    category = Column(String(64), nullable=False, default="metadata_only")
    source_path = Column(Text, nullable=False)
    storage_type = Column(String(64), nullable=False, default="local")
    file_hash = Column(String(128), nullable=False, index=True)
    language = Column(String(32), nullable=False, default="zh")
    status = Column(String(32), nullable=False, default="uploaded", index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    booklist_item = relationship("ReferenceBooklistItem", back_populates="books")
    download_task = relationship("ReferenceDownloadTask", back_populates="books")
    sections = relationship("ReferenceSection", back_populates="source_book", cascade="all, delete-orphan")
    section_analyses = relationship("ReferenceSectionAnalysis", back_populates="source_book", cascade="all, delete-orphan")
    pattern_cards = relationship("ReferencePatternCard", back_populates="source_book", cascade="all, delete-orphan")


class ReferenceSection(Base):
    __tablename__ = "reference_sections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_book_id = Column(UUID(as_uuid=True), ForeignKey("reference_books.id", ondelete="CASCADE"), nullable=False, index=True)
    source_section_id = Column(String(255), nullable=False, index=True)
    chapter_title = Column(String(500), nullable=False, default="opening")
    section_index = Column(Integer, nullable=False, default=0)
    section_hash = Column(String(128), nullable=False, index=True)
    start_offset = Column(Integer, nullable=True)
    end_offset = Column(Integer, nullable=True)
    text_chars = Column(Integer, nullable=False, default=0)
    token_estimate = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default="parsed", index=True)
    analysis_status = Column(String(32), nullable=False, default="pending", index=True)
    card_count = Column(Integer, nullable=False, default=0)
    raw_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    source_book = relationship("ReferenceBook", back_populates="sections")
    analysis = relationship("ReferenceSectionAnalysis", back_populates="section", uselist=False, cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_reference_section_book_section", "source_book_id", "source_section_id", unique=True),
        Index("ix_reference_section_book_hash", "source_book_id", "section_hash"),
    )


class ReferenceSectionAnalysis(Base):
    __tablename__ = "reference_section_analyses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    section_id = Column(UUID(as_uuid=True), ForeignKey("reference_sections.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    source_book_id = Column(UUID(as_uuid=True), ForeignKey("reference_books.id", ondelete="CASCADE"), nullable=False, index=True)
    section_hash = Column(String(128), nullable=False, index=True)
    model_provider = Column(String(64), nullable=False, default="deepseek")
    model_name = Column(String(255), nullable=False, default="deepseek-v4-pro")
    status = Column(String(32), nullable=False, default="pending", index=True)
    analysis_json = Column(JSONB, nullable=False, default=dict)
    analysis_hash = Column(String(128), nullable=True, index=True)
    output_hash = Column(String(128), nullable=True, index=True)
    raw_text_included = Column(Boolean, nullable=False, default=False)
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    estimated_cost = Column(Float, nullable=False, default=0.0)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    section = relationship("ReferenceSection", back_populates="analysis")
    source_book = relationship("ReferenceBook", back_populates="section_analyses")


class ReferenceIngestionJob(Base):
    __tablename__ = "reference_ingestion_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_path = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    total_files = Column(Integer, nullable=False, default=0)
    processed_files = Column(Integer, nullable=False, default=0)
    generated_cards = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ReferencePatternCard(Base):
    __tablename__ = "reference_pattern_cards"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_book_id = Column(UUID(as_uuid=True), ForeignKey("reference_books.id", ondelete="SET NULL"), nullable=True, index=True)
    pattern_key = Column(String(255), nullable=False, index=True)
    category = Column(String(64), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    genre = Column(String(255), nullable=True)
    function = Column(Text, nullable=True)
    conditions = Column(JSONB, nullable=False, default=list)
    beats = Column(JSONB, nullable=False, default=list)
    common_pitfalls = Column(JSONB, nullable=False, default=list)
    best_use = Column(JSONB, nullable=False, default=list)
    required_global_link = Column(Text, nullable=True)
    originality_notes = Column(Text, nullable=True)
    tags = Column(JSONB, nullable=False, default=list)
    embedding = Column(JSONB, nullable=True)
    source_section_ids = Column(JSONB, nullable=False, default=list)
    source_analysis_ids = Column(JSONB, nullable=False, default=list)
    source_book_ids = Column(JSONB, nullable=False, default=list)
    source_card_ids = Column(JSONB, nullable=False, default=list)
    evidence_count = Column(Integer, nullable=False, default=1)
    raw_text_included = Column(Boolean, nullable=False, default=False)
    extraction_model_provider = Column(String(64), nullable=True)
    extraction_model_name = Column(String(255), nullable=True)
    output_hash = Column(String(128), nullable=True, index=True)
    raw_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    source_book = relationship("ReferenceBook", back_populates="pattern_cards")

    __table_args__ = (Index("ix_reference_pattern_source_key", "source_book_id", "pattern_key", unique=True),)


class ReferenceMechanismProfile(Base):
    __tablename__ = "reference_mechanism_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="SET NULL"), nullable=True, index=True)
    corpus_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    profile_name = Column(String(255), nullable=False, default="Epic Novel Corpus Mechanism Profile")
    source_book_count = Column(Integer, nullable=False, default=0)
    source_card_count = Column(Integer, nullable=False, default=0)
    genre = Column(String(255), nullable=True)
    tier_weights = Column(JSONB, nullable=False, default=dict)
    core_pleasure_mechanisms = Column(JSONB, nullable=False, default=list)
    case_structure_patterns = Column(JSONB, nullable=False, default=list)
    court_power_patterns = Column(JSONB, nullable=False, default=list)
    bureaucracy_conflict_patterns = Column(JSONB, nullable=False, default=list)
    heroine_agency_patterns = Column(JSONB, nullable=False, default=list)
    character_arc_patterns = Column(JSONB, nullable=False, default=list)
    foreshadow_patterns = Column(JSONB, nullable=False, default=list)
    power_progression_patterns = Column(JSONB, nullable=False, default=list)
    longform_pacing_patterns = Column(JSONB, nullable=False, default=list)
    anti_cliche_rules = Column(JSONB, nullable=False, default=list)
    common_pitfalls = Column(JSONB, nullable=False, default=list)
    originality_guidance = Column(JSONB, nullable=False, default=list)
    raw_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class EpicNovelProject(Base):
    __tablename__ = "epic_novel_projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    title = Column(String(255), nullable=False, default="Untitled Epic Novel")
    genre = Column(String(255), nullable=False, default="ancient_transmigration_mystery_xianxia")
    premise = Column(Text, nullable=False)
    target_words = Column(Integer, nullable=False, default=3_000_000)
    target_volumes = Column(Integer, nullable=False, default=12)
    target_chapters = Column(Integer, nullable=False, default=700)
    bible_target_words = Column(Integer, nullable=False, default=100_000)
    style = Column(Text, nullable=True)
    forbidden_rules = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default=EpicNovelProjectStatus.DRAFT, index=True)
    current_volume = Column(Integer, nullable=False, default=0)
    current_chapter = Column(Integer, nullable=False, default=0)
    total_words = Column(Integer, nullable=False, default=0)
    generation_mode = Column(String(32), nullable=False, default="standard")
    editor_model = Column(String(255), nullable=True)
    author_model = Column(String(255), nullable=True)
    cheap_model = Column(String(255), nullable=True)
    reference_profile_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    reference_booklist_path = Column(Text, nullable=True)
    sonovel_home = Column(Text, nullable=True)
    sonovel_download_path = Column(Text, nullable=True)
    reference_raw_path = Column(Text, nullable=True)
    raw_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    owner = relationship("User")


class WorldviewCandidate(Base):
    __tablename__ = "epic_worldview_candidates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    candidate_number = Column(Integer, nullable=False)
    core_concept = Column(Text, nullable=True)
    power_source = Column(Text, nullable=True)
    world_law = Column(Text, nullable=True)
    society_structure = Column(Text, nullable=True)
    main_conflict = Column(Text, nullable=True)
    protagonist_specialness = Column(Text, nullable=True)
    heroine_integration = Column(Text, nullable=True)
    ensemble_potential = Column(Text, nullable=True)
    final_mystery = Column(Text, nullable=True)
    score_json = Column(JSONB, nullable=False, default=dict)
    selected = Column(Boolean, nullable=False, default=False)
    raw_json = Column(JSONB, nullable=False, default=dict)

    __table_args__ = (Index("ix_epic_worldview_project_candidate", "project_id", "candidate_number", unique=True),)


class EpicWorldBible(Base):
    __tablename__ = "epic_world_bibles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    title = Column(String(255), nullable=False)
    core_concept = Column(Text, nullable=True)
    world_law = Column(Text, nullable=True)
    power_source = Column(Text, nullable=True)
    society_structure = Column(Text, nullable=True)
    history = Column(Text, nullable=True)
    taboo_rules = Column(JSONB, nullable=False, default=list)
    style_rules = Column(JSONB, nullable=False, default=list)
    forbidden_rules = Column(JSONB, nullable=False, default=list)
    final_mystery = Column(Text, nullable=True)
    ending_direction = Column(Text, nullable=True)
    raw_json = Column(JSONB, nullable=False, default=dict)


class EpicWorldAtlas(Base):
    __tablename__ = "epic_world_atlases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    nations = Column(JSONB, nullable=False, default=list)
    regions = Column(JSONB, nullable=False, default=list)
    institutions = Column(JSONB, nullable=False, default=list)
    factions = Column(JSONB, nullable=False, default=list)
    trade_routes = Column(JSONB, nullable=False, default=list)
    war_zones = Column(JSONB, nullable=False, default=list)
    secret_realms = Column(JSONB, nullable=False, default=list)
    historical_timeline = Column(JSONB, nullable=False, default=list)
    raw_json = Column(JSONB, nullable=False, default=dict)


class EpicNation(Base):
    __tablename__ = "epic_nations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    inspiration_profile = Column(String(64), nullable=True)
    dynasty_flavor = Column(Text, nullable=True)
    ruling_ideology = Column(Text, nullable=True)
    political_structure = Column(Text, nullable=True)
    capital = Column(String(255), nullable=True)
    provinces = Column(JSONB, nullable=False, default=list)
    military_system = Column(Text, nullable=True)
    fiscal_system = Column(Text, nullable=True)
    legal_system = Column(Text, nullable=True)
    official_selection_system = Column(Text, nullable=True)
    bureaucracy_system_id = Column(UUID(as_uuid=True), nullable=True)
    relationship_to_hero = Column(Text, nullable=True)
    secrets = Column(JSONB, nullable=False, default=list)
    current_crisis = Column(Text, nullable=True)
    raw_json = Column(JSONB, nullable=False, default=dict)


class EpicBureaucracySystem(Base):
    __tablename__ = "epic_bureaucracy_systems"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    nation_name = Column(String(255), nullable=False)
    base_inspiration = Column(String(64), nullable=False, default="Mixed")
    central_government = Column(JSONB, nullable=False, default=dict)
    local_government = Column(JSONB, nullable=False, default=dict)
    rank_system = Column(JSONB, nullable=False, default=dict)
    official_titles = Column(JSONB, nullable=False, default=list)
    departments = Column(JSONB, nullable=False, default=list)
    censorate_system = Column(JSONB, nullable=False, default=dict)
    judicial_system = Column(JSONB, nullable=False, default=dict)
    military_administration = Column(JSONB, nullable=False, default=dict)
    finance_tax_system = Column(JSONB, nullable=False, default=dict)
    examination_system = Column(JSONB, nullable=False, default=dict)
    intelligence_agencies = Column(JSONB, nullable=False, default=list)
    special_agencies = Column(JSONB, nullable=False, default=list)
    court_factions = Column(JSONB, nullable=False, default=list)
    power_checks = Column(JSONB, nullable=False, default=list)
    corruption_channels = Column(JSONB, nullable=False, default=list)
    story_functions = Column(JSONB, nullable=False, default=list)
    raw_json = Column(JSONB, nullable=False, default=dict)


class EpicMapRegion(Base):
    __tablename__ = "epic_map_regions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    region_type = Column(String(64), nullable=False, default="province")
    controlling_faction = Column(String(255), nullable=True)
    geography = Column(Text, nullable=True)
    economy = Column(Text, nullable=True)
    resources = Column(Text, nullable=True)
    strategic_value = Column(Text, nullable=True)
    local_power_structure = Column(Text, nullable=True)
    conflicts = Column(Text, nullable=True)
    secrets = Column(Text, nullable=True)
    related_foreshadows = Column(JSONB, nullable=False, default=list)
    raw_json = Column(JSONB, nullable=False, default=dict)


class EpicInstitution(Base):
    __tablename__ = "epic_institutions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    institution_type = Column(String(64), nullable=False, default="court")
    nation_name = Column(String(255), nullable=True)
    faction_name = Column(String(255), nullable=True)
    public_function = Column(Text, nullable=True)
    hidden_function = Column(Text, nullable=True)
    hierarchy = Column(JSONB, nullable=False, default=list)
    ranks = Column(JSONB, nullable=False, default=list)
    internal_factions = Column(JSONB, nullable=False, default=list)
    secrets = Column(JSONB, nullable=False, default=list)
    relationship_to_hero = Column(Text, nullable=True)
    story_use_cases = Column(JSONB, nullable=False, default=list)
    raw_json = Column(JSONB, nullable=False, default=dict)


class EpicMasterBible(Base):
    __tablename__ = "epic_master_bibles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    title = Column(String(255), nullable=False)
    status = Column(String(32), nullable=False, default=EpicBibleStatus.PLANNING)
    target_word_count = Column(Integer, nullable=False, default=100_000)
    actual_word_count = Column(Integer, nullable=False, default=0)
    version = Column(Integer, nullable=False, default=1)
    coverage_score = Column(Float, nullable=False, default=0.0)
    quality_score = Column(Float, nullable=False, default=0.0)
    export_markdown_path = Column(Text, nullable=True)
    export_json_path = Column(Text, nullable=True)
    export_docx_path = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class EpicBibleSection(Base):
    __tablename__ = "epic_bible_sections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    bible_id = Column(UUID(as_uuid=True), ForeignKey("epic_master_bibles.id", ondelete="CASCADE"), nullable=False, index=True)
    section_key = Column(String(128), nullable=False)
    section_title = Column(String(255), nullable=False)
    section_type = Column(String(64), nullable=False, default="core")
    order_index = Column(Integer, nullable=False, default=0)
    target_words = Column(Integer, nullable=False, default=3000)
    actual_words = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default="planned")
    content_markdown = Column(Text, nullable=True)
    content_json = Column(JSONB, nullable=False, default=dict)
    dependencies = Column(JSONB, nullable=False, default=list)
    coverage_tags = Column(JSONB, nullable=False, default=list)
    issues = Column(JSONB, nullable=False, default=list)
    version = Column(Integer, nullable=False, default=1)
    embedding = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (Index("ix_epic_bible_section_project_key", "project_id", "section_key", unique=True),)


class EpicBibleCoverageItem(Base):
    __tablename__ = "epic_bible_coverage_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    bible_id = Column(UUID(as_uuid=True), ForeignKey("epic_master_bibles.id", ondelete="CASCADE"), nullable=False, index=True)
    item_key = Column(String(128), nullable=False)
    item_name = Column(String(255), nullable=False)
    category = Column(String(64), nullable=False)
    required = Column(Boolean, nullable=False, default=True)
    status = Column(String(32), nullable=False, default="missing")
    related_section_keys = Column(JSONB, nullable=False, default=list)
    issue = Column(Text, nullable=True)
    suggested_fix = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class EpicNamedEntityIndex(Base):
    __tablename__ = "epic_named_entity_index"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    entity_type = Column(String(64), nullable=False)
    name = Column(String(255), nullable=False)
    alias = Column(JSONB, nullable=False, default=list)
    first_defined_section = Column(String(128), nullable=True)
    first_appearance_chapter = Column(Integer, nullable=True)
    importance = Column(String(8), nullable=False, default="C")
    summary = Column(Text, nullable=True)
    embedding = Column(JSONB, nullable=True)
    raw_json = Column(JSONB, nullable=False, default=dict)


class EpicPowerSystem(Base):
    __tablename__ = "epic_power_systems"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    name = Column(String(255), nullable=False)
    source = Column(Text, nullable=True)
    realm_levels = Column(JSONB, nullable=False, default=list)
    progression_rules = Column(JSONB, nullable=False, default=list)
    breakthrough_conditions = Column(JSONB, nullable=False, default=list)
    costs = Column(JSONB, nullable=False, default=list)
    limits = Column(JSONB, nullable=False, default=list)
    professions = Column(JSONB, nullable=False, default=list)
    resources = Column(JSONB, nullable=False, default=list)
    protagonist_path = Column(JSONB, nullable=False, default=list)
    heroine_paths = Column(JSONB, nullable=False, default=list)
    villain_usage = Column(JSONB, nullable=False, default=list)
    final_system_twist = Column(Text, nullable=True)
    raw_json = Column(JSONB, nullable=False, default=dict)


class EpicPowerLedger(Base):
    __tablename__ = "epic_power_ledgers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    character_name = Column(String(255), nullable=False)
    volume_number = Column(Integer, nullable=True)
    chapter_number = Column(Integer, nullable=True, index=True)
    current_realm = Column(String(255), nullable=True)
    abilities = Column(JSONB, nullable=False, default=list)
    techniques = Column(JSONB, nullable=False, default=list)
    artifacts = Column(JSONB, nullable=False, default=list)
    resources = Column(JSONB, nullable=False, default=list)
    injuries = Column(JSONB, nullable=False, default=list)
    trump_cards = Column(JSONB, nullable=False, default=list)
    cannot_defeat = Column(JSONB, nullable=False, default=list)
    next_breakthrough_condition = Column(Text, nullable=True)
    raw_json = Column(JSONB, nullable=False, default=dict)


class EpicCharacter(Base):
    __tablename__ = "epic_characters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    tier = Column(String(8), nullable=False, default="B")
    role = Column(String(128), nullable=True)
    gender = Column(String(32), nullable=True)
    archetype = Column(String(255), nullable=True)
    independent_goal = Column(Text, nullable=True)
    desire = Column(Text, nullable=True)
    fear = Column(Text, nullable=True)
    false_belief = Column(Text, nullable=True)
    true_need = Column(Text, nullable=True)
    core_wound = Column(Text, nullable=True)
    faction = Column(String(255), nullable=True)
    power_path = Column(Text, nullable=True)
    relationship_to_hero = Column(Text, nullable=True)
    active_volumes = Column(JSONB, nullable=False, default=list)
    screen_time_budget = Column(Integer, nullable=False, default=0)
    ending = Column(Text, nullable=True)
    must_not_do = Column(JSONB, nullable=False, default=list)
    raw_json = Column(JSONB, nullable=False, default=dict)


class EpicCharacterArc(Base):
    __tablename__ = "epic_character_arcs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    character_name = Column(String(255), nullable=False)
    tier = Column(String(8), nullable=False, default="B")
    arc_start = Column(Text, nullable=True)
    arc_midpoint = Column(Text, nullable=True)
    low_point = Column(Text, nullable=True)
    key_choice = Column(Text, nullable=True)
    cost = Column(Text, nullable=True)
    arc_end = Column(Text, nullable=True)
    volume_arc_grid = Column(JSONB, nullable=False, default=list)
    raw_json = Column(JSONB, nullable=False, default=dict)


class EpicRelationshipEdge(Base):
    __tablename__ = "epic_relationship_edges"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    from_character = Column(String(255), nullable=False)
    to_character = Column(String(255), nullable=False)
    relation_type = Column(String(128), nullable=True)
    surface_relation = Column(Text, nullable=True)
    hidden_conflict = Column(Text, nullable=True)
    interest_conflict = Column(Text, nullable=True)
    emotional_debt = Column(Text, nullable=True)
    current_tension_level = Column(Integer, nullable=False, default=1)
    future_explosion_volume = Column(Integer, nullable=True)
    possible_resolution = Column(Text, nullable=True)
    raw_json = Column(JSONB, nullable=False, default=dict)


class EpicFaction(Base):
    __tablename__ = "epic_factions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    type = Column(String(128), nullable=True)
    ideology = Column(Text, nullable=True)
    leader = Column(String(255), nullable=True)
    public_goal = Column(Text, nullable=True)
    hidden_goal = Column(Text, nullable=True)
    resources = Column(Text, nullable=True)
    relationship_to_hero = Column(Text, nullable=True)
    enemy_factions = Column(JSONB, nullable=False, default=list)
    secrets = Column(JSONB, nullable=False, default=list)
    status = Column(String(32), nullable=False, default="active")
    raw_json = Column(JSONB, nullable=False, default=dict)


class EpicGlobalSpine(Base):
    __tablename__ = "epic_global_spines"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    surface_story = Column(Text, nullable=True)
    hidden_truth = Column(Text, nullable=True)
    final_conflict = Column(Text, nullable=True)
    hero_final_transformation = Column(Text, nullable=True)
    stages = Column(JSONB, nullable=False, default=list)
    final_reveal = Column(Text, nullable=True)
    raw_json = Column(JSONB, nullable=False, default=dict)


class EpicVolumeContract(Base):
    __tablename__ = "epic_volume_contracts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    volume_number = Column(Integer, nullable=False)
    title = Column(String(255), nullable=False)
    word_budget = Column(Integer, nullable=False, default=250_000)
    chapter_start = Column(Integer, nullable=False)
    chapter_end = Column(Integer, nullable=False)
    local_main_plot = Column(Text, nullable=True)
    global_spine_function = Column(Text, nullable=True)
    world_atlas_function = Column(Text, nullable=True)
    bureaucracy_function = Column(Text, nullable=True)
    map_expansion = Column(Text, nullable=True)
    power_progression = Column(Text, nullable=True)
    hero_arc_function = Column(Text, nullable=True)
    heroine_arc_function = Column(Text, nullable=True)
    major_characters = Column(JSONB, nullable=False, default=list)
    core_pleasures = Column(JSONB, nullable=False, default=list)
    setups = Column(JSONB, nullable=False, default=list)
    echoes = Column(JSONB, nullable=False, default=list)
    payoffs = Column(JSONB, nullable=False, default=list)
    ending_explosion = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="planned")
    raw_json = Column(JSONB, nullable=False, default=dict)

    __table_args__ = (Index("ix_epic_volume_project_number", "project_id", "volume_number", unique=True),)


class EpicChapterContract(Base):
    __tablename__ = "epic_chapter_contracts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    volume_number = Column(Integer, nullable=False)
    chapter_number = Column(Integer, nullable=False)
    title = Column(String(255), nullable=False)
    target_words = Column(Integer, nullable=False, default=4000)
    chapter_goal = Column(Text, nullable=True)
    plot_function = Column(Text, nullable=True)
    global_spine_function = Column(Text, nullable=True)
    world_atlas_function = Column(Text, nullable=True)
    bureaucracy_function = Column(Text, nullable=True)
    local_arc_function = Column(Text, nullable=True)
    character_arc_deltas = Column(JSONB, nullable=False, default=list)
    relationship_changes = Column(JSONB, nullable=False, default=list)
    power_system_change = Column(JSONB, nullable=False, default=dict)
    institution_used = Column(JSONB, nullable=False, default=list)
    map_region_used = Column(JSONB, nullable=False, default=list)
    foreshadows_to_plant = Column(JSONB, nullable=False, default=list)
    foreshadows_to_echo = Column(JSONB, nullable=False, default=list)
    foreshadows_to_payoff = Column(JSONB, nullable=False, default=list)
    reader_question_raised = Column(Text, nullable=True)
    reader_question_answered = Column(Text, nullable=True)
    core_pleasure = Column(Text, nullable=True)
    secondary_pleasure = Column(Text, nullable=True)
    reference_pattern_cards_used = Column(JSONB, nullable=False, default=list)
    must_execute = Column(JSONB, nullable=False, default=list)
    must_not_reveal = Column(JSONB, nullable=False, default=list)
    ending_hook = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="planned")
    raw_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (Index("ix_epic_contract_project_chapter", "project_id", "chapter_number", unique=True),)


class EpicForeshadow(Base):
    __tablename__ = "epic_foreshadows"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    foreshadow_key = Column(String(128), nullable=False)
    name = Column(String(255), nullable=False)
    setup_chapter = Column(Integer, nullable=True)
    surface_meaning = Column(Text, nullable=True)
    hidden_meaning = Column(Text, nullable=True)
    first_echo_chapter = Column(Integer, nullable=True)
    second_echo_chapter = Column(Integer, nullable=True)
    payoff_chapter = Column(Integer, nullable=True)
    payoff_type = Column(String(128), nullable=True)
    related_characters = Column(JSONB, nullable=False, default=list)
    related_world_rules = Column(JSONB, nullable=False, default=list)
    global_spine_link = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="open")
    raw_json = Column(JSONB, nullable=False, default=dict)


class EpicReaderQuestion(Base):
    __tablename__ = "epic_reader_questions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    question_key = Column(String(128), nullable=False)
    question = Column(Text, nullable=False)
    introduced_chapter = Column(Integer, nullable=True)
    current_reader_understanding = Column(Text, nullable=True)
    false_answers = Column(JSONB, nullable=False, default=list)
    partial_reveals = Column(JSONB, nullable=False, default=list)
    final_reveal_chapter = Column(Integer, nullable=True)
    true_answer = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="open")


class EpicPleasureBeat(Base):
    __tablename__ = "epic_pleasure_beats"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    volume_number = Column(Integer, nullable=True)
    chapter_number = Column(Integer, nullable=True, index=True)
    pleasure_type = Column(String(128), nullable=False)
    pleasure_goal = Column(Text, nullable=True)
    setup = Column(Text, nullable=True)
    payoff = Column(Text, nullable=True)
    reader_reward = Column(Text, nullable=True)
    related_pattern_cards = Column(JSONB, nullable=False, default=list)
    status = Column(String(32), nullable=False, default="planned")


class EpicMystery(Base):
    __tablename__ = "epic_mysteries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    mystery_key = Column(String(128), nullable=False)
    surface_mystery = Column(Text, nullable=False)
    true_cause = Column(Text, nullable=True)
    introduced_chapter = Column(Integer, nullable=True)
    planned_reveal_chapter = Column(Integer, nullable=True)
    clues = Column(JSONB, nullable=False, default=list)
    wrong_guesses = Column(JSONB, nullable=False, default=list)
    hero_insight_moment = Column(Text, nullable=True)
    public_reveal_moment = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="open")


class EpicChapter(Base):
    __tablename__ = "epic_chapters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    volume_number = Column(Integer, nullable=False)
    chapter_number = Column(Integer, nullable=False)
    title = Column(String(255), nullable=False)
    draft_content = Column(Text, nullable=True)
    repaired_content = Column(Text, nullable=True)
    accepted_content = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    ending_hook = Column(Text, nullable=True)
    word_count = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default=EpicChapterStatus.PLANNED)
    version = Column(Integer, nullable=False, default=1)
    repair_count = Column(Integer, nullable=False, default=0)
    raw_model_output = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (Index("ix_epic_chapter_project_number", "project_id", "chapter_number", unique=True),)


class EpicStateExtract(Base):
    __tablename__ = "epic_state_extracts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_number = Column(Integer, nullable=False, index=True)
    events = Column(JSONB, nullable=False, default=list)
    canon_facts = Column(JSONB, nullable=False, default=list)
    character_state_deltas = Column(JSONB, nullable=False, default=list)
    relationship_changes = Column(JSONB, nullable=False, default=list)
    power_changes = Column(JSONB, nullable=False, default=list)
    foreshadow_actions = Column(JSONB, nullable=False, default=list)
    reader_question_changes = Column(JSONB, nullable=False, default=list)
    pleasure_execution = Column(JSONB, nullable=False, default=list)
    faction_changes = Column(JSONB, nullable=False, default=list)
    object_state_changes = Column(JSONB, nullable=False, default=list)
    chapter_summary = Column(Text, nullable=True)
    ending_hook = Column(Text, nullable=True)
    raw_json = Column(JSONB, nullable=False, default=dict)

    __table_args__ = (Index("ix_epic_state_extract_project_chapter", "project_id", "chapter_number", unique=True),)


class EpicCanonFact(Base):
    __tablename__ = "epic_canon_facts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_number = Column(Integer, nullable=False, index=True)
    fact = Column(Text, nullable=False)
    fact_type = Column(String(128), nullable=True)
    entities = Column(JSONB, nullable=False, default=list)
    source = Column(String(255), nullable=True)
    confidence = Column(Float, nullable=False, default=1.0)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class EpicCharacterState(Base):
    __tablename__ = "epic_character_states"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    character_name = Column(String(255), nullable=False)
    chapter_number = Column(Integer, nullable=False, index=True)
    current_goal = Column(Text, nullable=True)
    emotion = Column(Text, nullable=True)
    knowledge = Column(JSONB, nullable=False, default=list)
    secrets = Column(JSONB, nullable=False, default=list)
    relationships = Column(JSONB, nullable=False, default=dict)
    power_state = Column(JSONB, nullable=False, default=dict)
    arc_stage = Column(String(255), nullable=True)
    current_location = Column(String(255), nullable=True)
    raw_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (Index("ix_epic_character_state_project_name_chapter", "project_id", "character_name", "chapter_number"),)


class EpicObjectState(Base):
    __tablename__ = "epic_object_states"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    object_name = Column(String(255), nullable=False)
    chapter_number = Column(Integer, nullable=False, index=True)
    holder = Column(String(255), nullable=True)
    location = Column(String(255), nullable=True)
    known_by = Column(JSONB, nullable=False, default=list)
    status = Column(String(128), nullable=True)
    related_foreshadow = Column(String(255), nullable=True)
    raw_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class EpicFactionState(Base):
    __tablename__ = "epic_faction_states"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    faction_name = Column(String(255), nullable=False)
    chapter_number = Column(Integer, nullable=False, index=True)
    current_goal = Column(Text, nullable=True)
    resources = Column(JSONB, nullable=False, default=list)
    allies = Column(JSONB, nullable=False, default=list)
    enemies = Column(JSONB, nullable=False, default=list)
    losses = Column(JSONB, nullable=False, default=list)
    secrets_exposed = Column(JSONB, nullable=False, default=list)
    stance_to_hero = Column(Text, nullable=True)
    raw_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class EpicInstitutionState(Base):
    __tablename__ = "epic_institution_states"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    institution_name = Column(String(255), nullable=False)
    chapter_number = Column(Integer, nullable=False, index=True)
    current_leader = Column(String(255), nullable=True)
    public_status = Column(Text, nullable=True)
    hidden_status = Column(Text, nullable=True)
    active_conflict = Column(Text, nullable=True)
    relationship_to_hero = Column(Text, nullable=True)
    known_secrets = Column(JSONB, nullable=False, default=list)
    raw_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class EpicCharacterKnowledgeLedger(Base):
    __tablename__ = "epic_character_knowledge_ledgers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    character_id = Column(String(255), nullable=True, index=True)
    character_name = Column(String(255), nullable=False, index=True)
    chapter_number = Column(Integer, nullable=False, index=True)
    knows = Column(JSONB, nullable=False, default=list)
    suspects = Column(JSONB, nullable=False, default=list)
    does_not_know = Column(JSONB, nullable=False, default=list)
    allowed_dialogue = Column(JSONB, nullable=False, default=list)
    forbidden_dialogue = Column(JSONB, nullable=False, default=list)
    evidence_source = Column(JSONB, nullable=False, default=list)
    first_learned_chapter = Column(Integer, nullable=True)
    confidence_level = Column(Float, nullable=False, default=1.0)
    last_updated_chapter = Column(Integer, nullable=False, default=0)
    canon_source = Column(String(64), nullable=False, default="acceptedContent", index=True)
    raw_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class EpicSecretExposurePolicy(Base):
    __tablename__ = "epic_secret_exposure_policies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    secret_id = Column(String(255), nullable=False, index=True)
    owner_character_id = Column(String(255), nullable=True, index=True)
    secret_type = Column(String(128), nullable=True)
    true_name = Column(Text, nullable=True)
    public_name = Column(Text, nullable=True)
    current_exposure_level = Column(Integer, nullable=False, default=0)
    exposure_levels = Column(JSONB, nullable=False, default=list)
    per_volume_exposure_limit = Column(Integer, nullable=True)
    who_knows = Column(JSONB, nullable=False, default=list)
    who_suspects = Column(JSONB, nullable=False, default=list)
    forbidden_reveal_before = Column(Integer, nullable=True)
    allowed_reveal_conditions = Column(JSONB, nullable=False, default=list)
    reveal_history = Column(JSONB, nullable=False, default=list)
    canon_source = Column(String(64), nullable=False, default="acceptedContent", index=True)
    raw_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (Index("ix_epic_secret_policy_project_secret", "project_id", "secret_id"),)


class EpicReaderQuestionLedger(Base):
    __tablename__ = "epic_reader_question_ledgers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    question_key = Column(String(255), nullable=True, index=True)
    question = Column(Text, nullable=False)
    introduced_chapter = Column(Integer, nullable=True, index=True)
    introduced_by_event = Column(Text, nullable=True)
    current_status = Column(String(64), nullable=False, default="open", index=True)
    planned_reveal_window = Column(JSONB, nullable=False, default=dict)
    partial_answers = Column(JSONB, nullable=False, default=list)
    forbidden_early_answer = Column(Boolean, nullable=False, default=True)
    payoff_target = Column(Text, nullable=True)
    resolved_chapter = Column(Integer, nullable=True, index=True)
    canon_source = Column(String(64), nullable=False, default="acceptedContent", index=True)
    raw_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class EpicCharacterGrowthLedger(Base):
    __tablename__ = "epic_character_growth_ledgers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    character_id = Column(String(255), nullable=True, index=True)
    character_name = Column(String(255), nullable=False, index=True)
    role_tier = Column(String(16), nullable=False, default="D", index=True)
    chapter_number = Column(Integer, nullable=False, index=True)
    growth_axis = Column(String(128), nullable=False, index=True)
    before = Column(Text, nullable=True)
    after = Column(Text, nullable=True)
    trigger = Column(Text, nullable=True)
    cost = Column(Text, nullable=True)
    evidence_in_text = Column(Text, nullable=True)
    future_impact = Column(Text, nullable=True)
    allowed_by_chapter_contract = Column(Boolean, nullable=False, default=False)
    canon_source = Column(String(64), nullable=False, default="acceptedContent", index=True)
    raw_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class EpicAuthorityLedger(Base):
    __tablename__ = "epic_authority_ledgers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    character_id = Column(String(255), nullable=True, index=True)
    character_name = Column(String(255), nullable=False, index=True)
    chapter_number = Column(Integer, nullable=False, index=True)
    official_role = Column(Text, nullable=True)
    permissions = Column(JSONB, nullable=False, default=list)
    limitations = Column(JSONB, nullable=False, default=list)
    used_authority_correctly = Column(Boolean, nullable=False, default=True)
    evidence_in_text = Column(Text, nullable=True)
    canon_source = Column(String(64), nullable=False, default="acceptedContent", index=True)
    raw_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class EpicRelationshipLedger(Base):
    __tablename__ = "epic_relationship_ledgers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    from_character = Column(String(255), nullable=False, index=True)
    to_character = Column(String(255), nullable=False, index=True)
    chapter_number = Column(Integer, nullable=False, index=True)
    before_state = Column(Text, nullable=True)
    after_state = Column(Text, nullable=True)
    trigger_event = Column(Text, nullable=True)
    trust_delta = Column(Float, nullable=False, default=0.0)
    conflict_delta = Column(Float, nullable=False, default=0.0)
    unresolved_tension = Column(Text, nullable=True)
    canon_source = Column(String(64), nullable=False, default="acceptedContent", index=True)
    raw_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class EpicSecretLedger(Base):
    __tablename__ = "epic_secret_ledgers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    secret_id = Column(String(255), nullable=False, index=True)
    chapter_number = Column(Integer, nullable=False, index=True)
    who_knows = Column(JSONB, nullable=False, default=list)
    who_suspects = Column(JSONB, nullable=False, default=list)
    who_is_wrong = Column(JSONB, nullable=False, default=list)
    who_is_deceived = Column(JSONB, nullable=False, default=list)
    reveal_history = Column(JSONB, nullable=False, default=list)
    canon_source = Column(String(64), nullable=False, default="acceptedContent", index=True)
    raw_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class EpicArtifactRecord(Base):
    __tablename__ = "epic_artifact_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    artifact_type = Column(String(128), nullable=False, index=True)
    filename = Column(Text, nullable=False)
    path = Column(Text, nullable=False)
    media_type = Column(String(255), nullable=False, default="application/octet-stream")
    bytes = Column(Integer, nullable=False, default=0)
    sha256 = Column(String(128), nullable=True, index=True)
    status = Column(String(64), nullable=False, default="ready", index=True)
    error = Column(Text, nullable=True)
    raw_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class EpicRetrievalTrace(Base):
    __tablename__ = "epic_retrieval_traces"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    task_type = Column(String(128), nullable=False)
    volume_number = Column(Integer, nullable=True)
    chapter_number = Column(Integer, nullable=True, index=True)
    section_key = Column(String(128), nullable=True)
    query = Column(Text, nullable=False)
    retrieved_card_ids = Column(JSONB, nullable=False, default=list)
    used_card_ids = Column(JSONB, nullable=False, default=list)
    used_profile_sections = Column(JSONB, nullable=False, default=list)
    profile_version = Column(String(255), nullable=True)
    excluded_context = Column(JSONB, nullable=False, default=list)
    raw_text_included = Column(Boolean, nullable=False, default=False)
    usage_summary = Column(Text, nullable=True)
    raw_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class EpicAcceptanceReport(Base):
    __tablename__ = "epic_acceptance_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_number = Column(Integer, nullable=False, index=True)
    outline_execution_score = Column(Float, nullable=False, default=0.0)
    character_arc_score = Column(Float, nullable=False, default=0.0)
    power_legality_score = Column(Float, nullable=False, default=0.0)
    foreshadow_score = Column(Float, nullable=False, default=0.0)
    pleasure_score = Column(Float, nullable=False, default=0.0)
    continuity_score = Column(Float, nullable=False, default=0.0)
    anti_cliche_score = Column(Float, nullable=False, default=0.0)
    overall_status = Column(String(32), nullable=False, default="pending")
    issues_json = Column(JSONB, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class EpicIssue(Base):
    __tablename__ = "epic_issues"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    issue_scope = Column(String(64), nullable=False)
    volume_number = Column(Integer, nullable=True)
    chapter_number = Column(Integer, nullable=True, index=True)
    section_key = Column(String(128), nullable=True)
    severity = Column(String(32), nullable=False, default="low")
    issue_type = Column(String(128), nullable=True)
    description = Column(Text, nullable=False)
    evidence = Column(Text, nullable=True)
    suggested_fix = Column(Text, nullable=True)
    repair_scope = Column(JSONB, nullable=False, default=dict)
    status = Column(String(32), nullable=False, default="open")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class EpicGenerationLog(Base):
    __tablename__ = "epic_generation_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=True, index=True)
    action = Column(String(128), nullable=False)
    provider = Column(String(64), nullable=True)
    model = Column(String(255), nullable=True)
    bible_id = Column(UUID(as_uuid=True), nullable=True)
    section_key = Column(String(128), nullable=True)
    volume_number = Column(Integer, nullable=True)
    chapter_number = Column(Integer, nullable=True, index=True)
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    estimated_cost = Column(Float, nullable=False, default=0.0)
    actual_cost = Column(Float, nullable=True)
    status = Column(String(32), nullable=False, default="started")
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class EpicProductionJob(Base):
    __tablename__ = "epic_production_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("epic_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    job_type = Column(String(128), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="queued", index=True)
    progress_current = Column(Integer, nullable=False, default=0)
    progress_total = Column(Integer, nullable=False, default=0)
    checkpoint_key = Column(String(255), nullable=True)
    checkpoint_json = Column(JSONB, nullable=False, default=dict)
    retry_failed_only = Column(Boolean, nullable=False, default=False)
    pause_requested = Column(Boolean, nullable=False, default=False)
    stop_requested = Column(Boolean, nullable=False, default=False)
    retry_count = Column(Integer, nullable=False, default=0)
    max_estimated_cost = Column(Float, nullable=True)
    estimated_cost = Column(Float, nullable=False, default=0.0)
    actual_cost = Column(Float, nullable=True)
    provider = Column(String(64), nullable=True)
    model = Column(String(255), nullable=True)
    action = Column(String(128), nullable=True)
    error = Column(Text, nullable=True)
    continuation_chunks = Column(JSONB, nullable=False, default=list)
    raw_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
