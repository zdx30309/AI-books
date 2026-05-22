"""Database-backed standalone cover batch jobs."""
from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.session import Base


class CoverBatchStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    DELETED = "deleted"


class CoverBatchItemStatus:
    QUEUED = "queued"
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    UNCERTAIN = "uncertain"
    FAILED = "failed"
    DELETED = "deleted"
    CANCELLED = "cancelled"


class ProviderRequestStatus:
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class CoverArtifactKind:
    DRAFT = "draft"
    REFINED = "refined"
    FINAL = "final"
    ZIP = "zip"


class CoverBatchJob(Base):
    __tablename__ = "cover_batch_jobs"

    id = Column(String(32), primary_key=True)
    owner_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    status = Column(String(32), nullable=False, default=CoverBatchStatus.PENDING, index=True)
    progress = Column(Integer, nullable=False, default=0)
    total = Column(Integer, nullable=False, default=0)
    completed = Column(Integer, nullable=False, default=0)
    failed = Column(Integer, nullable=False, default=0)
    uncertain = Column(Integer, nullable=False, default=0)
    current_step = Column(String(255), nullable=False, default="Queued")
    source_filename = Column(String(512), nullable=True)
    default_category = Column(String(80), nullable=True)
    default_category_label = Column(String(120), nullable=True)
    zip_path = Column(String(2048), nullable=True)
    active_run_id = Column(String(64), nullable=True, index=True)
    config = Column(JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    owner = relationship("User")
    items = relationship(
        "CoverBatchItem",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="CoverBatchItem.item_index",
    )
    provider_requests = relationship(
        "ProviderRequest",
        back_populates="job",
        cascade="all, delete-orphan",
    )
    artifacts = relationship(
        "CoverArtifact",
        back_populates="job",
        cascade="all, delete-orphan",
    )


class CoverBatchItem(Base):
    __tablename__ = "cover_batch_items"
    __table_args__ = (UniqueConstraint("job_id", "item_index", name="uq_cover_batch_item_index"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(
        String(32),
        ForeignKey("cover_batch_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_index = Column(Integer, nullable=False)
    title = Column(String(512), nullable=False)
    author = Column(String(255), nullable=True)
    category = Column(String(80), nullable=True)
    category_label = Column(String(120), nullable=True)
    status = Column(String(32), nullable=False, default=CoverBatchItemStatus.QUEUED, index=True)
    current_stage = Column(String(80), nullable=True)
    image_url = Column(String(2048), nullable=True)
    image_path = Column(String(2048), nullable=True)
    error = Column(Text, nullable=True)
    source_metadata = Column("metadata", JSONB, nullable=False, default=dict)
    retry_count = Column(Integer, nullable=False, default=0)
    retry_after = Column(DateTime(timezone=True), nullable=True, index=True)
    leased_by = Column(String(64), nullable=True, index=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    job = relationship("CoverBatchJob", back_populates="items")
    provider_requests = relationship(
        "ProviderRequest",
        back_populates="item",
        cascade="all, delete-orphan",
    )
    artifacts = relationship(
        "CoverArtifact",
        back_populates="item",
        cascade="all, delete-orphan",
    )


class ProviderRequest(Base):
    __tablename__ = "provider_requests"
    __table_args__ = (UniqueConstraint("request_key", name="uq_provider_request_key"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_key = Column(String(255), nullable=False, index=True)
    job_id = Column(
        String(32),
        ForeignKey("cover_batch_jobs.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cover_batch_items.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    provider = Column(String(80), nullable=False)
    model = Column(String(120), nullable=True)
    stage = Column(String(80), nullable=True)
    endpoint = Column(String(255), nullable=True)
    status = Column(String(32), nullable=False, default=ProviderRequestStatus.STARTED)
    billing_risk = Column(Boolean, nullable=False, default=False)
    attempts = Column(Integer, nullable=False, default=1)
    error_type = Column(String(80), nullable=True)
    error = Column(Text, nullable=True)
    response_summary = Column(JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)

    job = relationship("CoverBatchJob", back_populates="provider_requests")
    item = relationship("CoverBatchItem", back_populates="provider_requests")


class CoverArtifact(Base):
    __tablename__ = "cover_artifacts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(
        String(32),
        ForeignKey("cover_batch_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cover_batch_items.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    kind = Column(String(32), nullable=False)
    path = Column(String(2048), nullable=False)
    meta = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    job = relationship("CoverBatchJob", back_populates="artifacts")
    item = relationship("CoverBatchItem", back_populates="artifacts")
