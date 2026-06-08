"""Long-form family novel ORM models."""
from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.session import Base


class LongNovelProjectStatus:
    DRAFT = "draft"
    ACTIVE = "active"
    GENERATING = "generating"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class LongNovelChapterStatus:
    PLANNED = "planned"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_REPAIR = "needs_repair"


class LongNovelIssueStatus:
    OPEN = "open"
    REPAIRED = "repaired"
    IGNORED = "ignored"


class LongNovelProject(Base):
    __tablename__ = "long_novel_projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    title = Column(String(255), nullable=False, default="未命名妯娌长篇")
    type = Column(String(64), nullable=False, default="family_sisters_in_law_longform")
    genre_label = Column(String(64), nullable=False, default="家长里短·妯娌")
    premise = Column(Text, nullable=False)
    family_setup = Column(Text, nullable=True)
    conflict_tags = Column(JSONB, nullable=False, default=list)
    source_type = Column(String(64), nullable=False, default="manual")
    source_text = Column(Text, nullable=True)
    target_words = Column(Integer, nullable=False, default=200000)
    target_chapters = Column(Integer, nullable=False, default=50)
    generation_mode = Column(String(32), nullable=False, default="standard")
    style = Column(String(128), nullable=False, default="强情节现实家长里短")
    forbidden_rules = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default=LongNovelProjectStatus.DRAFT, index=True)
    current_chapter = Column(Integer, nullable=False, default=0)
    total_words = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    owner = relationship("User")
    bible = relationship(
        "LongNovelBible",
        back_populates="project",
        cascade="all, delete-orphan",
        uselist=False,
    )
    family_members = relationship(
        "LongNovelFamilyMember",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="LongNovelFamilyMember.family_role",
    )
    family_relations = relationship(
        "LongNovelFamilyRelation",
        back_populates="project",
        cascade="all, delete-orphan",
    )
    conflict_lines = relationship(
        "LongNovelConflictLine",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="LongNovelConflictLine.id",
    )
    chapter_plans = relationship(
        "LongNovelChapterPlan",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="LongNovelChapterPlan.chapter_number",
    )
    chapters = relationship(
        "LongNovelChapter",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="LongNovelChapter.chapter_number",
    )
    events = relationship("LongNovelEvent", back_populates="project", cascade="all, delete-orphan")
    canon_facts = relationship("LongNovelCanonFact", back_populates="project", cascade="all, delete-orphan")
    character_states = relationship("LongNovelCharacterState", back_populates="project", cascade="all, delete-orphan")
    object_states = relationship("LongNovelObjectState", back_populates="project", cascade="all, delete-orphan")
    issues = relationship("LongNovelContinuityIssue", back_populates="project", cascade="all, delete-orphan")
    logs = relationship("LongNovelGenerationLog", back_populates="project", cascade="all, delete-orphan")


class LongNovelBible(Base):
    __tablename__ = "long_novel_bibles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("long_novel_projects.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    theme = Column(Text, nullable=True)
    core_promise = Column(Text, nullable=True)
    emotional_hook = Column(Text, nullable=True)
    family_background = Column(Text, nullable=True)
    world_rules = Column(JSONB, nullable=False, default=list)
    style_rules = Column(JSONB, nullable=False, default=list)
    forbidden_rules = Column(JSONB, nullable=False, default=list)
    ending_direction = Column(Text, nullable=True)
    main_conflict = Column(Text, nullable=True)
    raw_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    project = relationship("LongNovelProject", back_populates="bible")


class LongNovelFamilyMember(Base):
    __tablename__ = "long_novel_family_members"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("long_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    family_role = Column(String(128), nullable=True)
    public_image = Column(Text, nullable=True)
    private_desire = Column(Text, nullable=True)
    grievance = Column(Text, nullable=True)
    leverage = Column(Text, nullable=True)
    weakness = Column(Text, nullable=True)
    secret = Column(Text, nullable=True)
    relationship_to_heroine = Column(Text, nullable=True)
    relationship_map = Column(JSONB, nullable=False, default=dict)
    voice_rules = Column(JSONB, nullable=False, default=dict)
    arc_start = Column(Text, nullable=True)
    arc_end = Column(Text, nullable=True)
    ending = Column(Text, nullable=True)
    raw_json = Column(JSONB, nullable=False, default=dict)

    project = relationship("LongNovelProject", back_populates="family_members")

    __table_args__ = (Index("ix_long_novel_member_project_name", "project_id", "name"),)


class LongNovelFamilyRelation(Base):
    __tablename__ = "long_novel_family_relations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("long_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    from_member = Column(String(128), nullable=False)
    to_member = Column(String(128), nullable=False)
    relation_type = Column(String(128), nullable=True)
    surface_relation = Column(Text, nullable=True)
    hidden_conflict = Column(Text, nullable=True)
    interest_conflict = Column(Text, nullable=True)
    emotional_debt = Column(Text, nullable=True)
    current_tension_level = Column(Integer, nullable=False, default=1)
    raw_json = Column(JSONB, nullable=False, default=dict)

    project = relationship("LongNovelProject", back_populates="family_relations")


class LongNovelConflictLine(Base):
    __tablename__ = "long_novel_conflict_lines"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("long_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    conflict_key = Column(String(128), nullable=False)
    name = Column(String(255), nullable=False)
    conflict_type = Column(String(128), nullable=True)
    description = Column(Text, nullable=True)
    introduced_chapter = Column(Integer, nullable=True)
    last_escalated_chapter = Column(Integer, nullable=True)
    planned_explosion_chapter = Column(Integer, nullable=True)
    planned_resolution_chapter = Column(Integer, nullable=True)
    status = Column(String(32), nullable=False, default="open")
    importance = Column(String(32), nullable=False, default="normal")

    project = relationship("LongNovelProject", back_populates="conflict_lines")

    __table_args__ = (
        Index("ix_long_novel_conflict_project_key", "project_id", "conflict_key", unique=True),
    )


class LongNovelChapterPlan(Base):
    __tablename__ = "long_novel_chapter_plans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("long_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_number = Column(Integer, nullable=False)
    volume_number = Column(Integer, nullable=True)
    title = Column(String(255), nullable=False)
    target_words = Column(Integer, nullable=False, default=4000)
    chapter_goal = Column(Text, nullable=True)
    domestic_conflict = Column(Text, nullable=True)
    emotional_hook = Column(Text, nullable=True)
    required_events = Column(JSONB, nullable=False, default=list)
    character_arc_move = Column(JSONB, nullable=False, default=list)
    conflict_lines_to_advance = Column(JSONB, nullable=False, default=list)
    forbidden_reveals = Column(JSONB, nullable=False, default=list)
    ending_hook = Column(Text, nullable=True)
    scenes = Column(JSONB, nullable=False, default=list)
    status = Column(String(32), nullable=False, default=LongNovelChapterStatus.PLANNED)
    raw_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    project = relationship("LongNovelProject", back_populates="chapter_plans")

    __table_args__ = (
        Index("ix_long_novel_plan_project_chapter", "project_id", "chapter_number", unique=True),
    )


class LongNovelChapter(Base):
    __tablename__ = "long_novel_chapters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("long_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_number = Column(Integer, nullable=False)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False, default="")
    summary = Column(Text, nullable=True)
    word_count = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default=LongNovelChapterStatus.PLANNED)
    version = Column(Integer, nullable=False, default=1)
    raw_model_output = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    project = relationship("LongNovelProject", back_populates="chapters")

    __table_args__ = (
        Index("ix_long_novel_chapter_project_number", "project_id", "chapter_number", unique=True),
    )


class LongNovelEvent(Base):
    __tablename__ = "long_novel_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("long_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_number = Column(Integer, nullable=False, index=True)
    event_type = Column(String(128), nullable=True)
    actors = Column(JSONB, nullable=False, default=list)
    location = Column(String(255), nullable=True)
    time_label = Column(String(255), nullable=True)
    cause = Column(Text, nullable=True)
    effect = Column(Text, nullable=True)
    family_impact = Column(Text, nullable=True)
    raw_json = Column(JSONB, nullable=False, default=dict)

    project = relationship("LongNovelProject", back_populates="events")


class LongNovelCanonFact(Base):
    __tablename__ = "long_novel_canon_facts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("long_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_number = Column(Integer, nullable=False, index=True)
    fact = Column(Text, nullable=False)
    fact_type = Column(String(128), nullable=True)
    entities = Column(JSONB, nullable=False, default=list)
    source = Column(String(255), nullable=True)
    confidence = Column(Float, nullable=False, default=1.0)

    project = relationship("LongNovelProject", back_populates="canon_facts")


class LongNovelCharacterState(Base):
    __tablename__ = "long_novel_character_states"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("long_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    member_name = Column(String(128), nullable=False)
    chapter_number = Column(Integer, nullable=False)
    goal = Column(Text, nullable=True)
    emotion = Column(Text, nullable=True)
    grievance_level = Column(Integer, nullable=True)
    tension_with_others = Column(JSONB, nullable=False, default=dict)
    knowledge = Column(JSONB, nullable=False, default=list)
    secrets = Column(JSONB, nullable=False, default=list)
    current_leverage = Column(Text, nullable=True)
    raw_json = Column(JSONB, nullable=False, default=dict)

    project = relationship("LongNovelProject", back_populates="character_states")

    __table_args__ = (
        Index("ix_long_novel_state_project_member_chapter", "project_id", "member_name", "chapter_number"),
    )


class LongNovelObjectState(Base):
    __tablename__ = "long_novel_object_states"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("long_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    object_name = Column(String(255), nullable=False)
    chapter_number = Column(Integer, nullable=False)
    holder = Column(String(128), nullable=True)
    location = Column(String(255), nullable=True)
    known_by = Column(JSONB, nullable=False, default=list)
    status = Column(String(128), nullable=True)

    project = relationship("LongNovelProject", back_populates="object_states")

    __table_args__ = (
        Index("ix_long_novel_object_project_name_chapter", "project_id", "object_name", "chapter_number"),
    )


class LongNovelContinuityIssue(Base):
    __tablename__ = "long_novel_continuity_issues"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("long_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_number = Column(Integer, nullable=False, index=True)
    severity = Column(String(32), nullable=False, default="low")
    issue_type = Column(String(128), nullable=True)
    description = Column(Text, nullable=False)
    evidence = Column(Text, nullable=True)
    suggested_fix = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default=LongNovelIssueStatus.OPEN)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    project = relationship("LongNovelProject", back_populates="issues")


class LongNovelGenerationLog(Base):
    __tablename__ = "long_novel_generation_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("long_novel_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_number = Column(Integer, nullable=True, index=True)
    action = Column(String(128), nullable=False)
    provider = Column(String(64), nullable=True)
    model = Column(String(255), nullable=True)
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    estimated_cost = Column(Float, nullable=False, default=0.0)
    status = Column(String(32), nullable=False, default="started")
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    project = relationship("LongNovelProject", back_populates="logs")
