"""
Task ORM - 一次视频转网文任务的全生命周期记录。
"""
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.session import Base


class TaskStatus:
    """Task.status 的合法取值常量。

    Phase 2 状态机约定:
      pending -> fetching_topics -> filtering_topics -> generating_outline
      -> writing_chapters -> fact_checking -> completed / failed

    现有实现仍可能只使用部分状态，但数据库层允许后续工作流直接沿用。
    """

    PENDING = "pending"
    FETCHING_TOPICS = "fetching_topics"
    FILTERING_TOPICS = "filtering_topics"
    GENERATING_OUTLINE = "generating_outline"
    WRITING_CHAPTERS = "writing_chapters"
    FACT_CHECKING = "fact_checking"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    MANUAL_REVIEW = "manual_review"

    # 兼容旧流水线状态名，避免历史代码引用时失效。
    PROCESSING = "processing"


class Task(Base):
    __tablename__ = "tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_url = Column(String(2048), nullable=False)
    owner_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    status = Column(
        String(32),
        nullable=False,
        default=TaskStatus.PENDING,
        index=True,
    )
    progress = Column(Integer, nullable=False, default=0)         # 0-100
    current_step = Column(String(255), nullable=False, default="排队中")

    cover_url = Column(String(2048), nullable=True)
    config = Column(JSONB, nullable=False, default=dict)

    # 故障可观测性 (生产必须): 任务失败时回填错误信息
    error_message = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # 反向关系在 chapter.py 通过 back_populates 配合
    chapters = relationship(
        "Chapter",
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="Chapter.chapter_num",
    )
    owner = relationship("User")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Task id={self.id} status={self.status} progress={self.progress}>"

    @property
    def title(self) -> str | None:
        cfg = self.config or {}
        title = cfg.get("title")
        return title if isinstance(title, str) and title.strip() else None

    @property
    def bookshelf(self) -> str | None:
        cfg = self.config or {}
        bookshelf = cfg.get("bookshelf")
        if isinstance(bookshelf, str) and bookshelf.strip():
            return bookshelf
        category_label = cfg.get("category_label")
        return category_label if isinstance(category_label, str) and category_label.strip() else None

    @property
    def category(self) -> str | None:
        cfg = self.config or {}
        category = cfg.get("category")
        return category if isinstance(category, str) and category.strip() else None

    @property
    def category_label(self) -> str | None:
        cfg = self.config or {}
        category_label = cfg.get("category_label")
        return category_label if isinstance(category_label, str) and category_label.strip() else None

    @property
    def cover_status(self) -> str | None:
        cfg = self.config or {}
        status = cfg.get("cover_status")
        return status if isinstance(status, str) and status.strip() else None

    @property
    def cover_error(self) -> str | None:
        cfg = self.config or {}
        error = cfg.get("cover_error")
        return error if isinstance(error, str) and error.strip() else None

    @property
    def aigc_passed(self) -> bool | None:
        value = (self.config or {}).get("aigc_passed")
        return value if isinstance(value, bool) else None

    @property
    def ai_or_finalizer_status(self) -> str | None:
        value = (self.config or {}).get("ai_or_finalizer_status")
        return value if isinstance(value, str) else None

    @property
    def aigc_max_score(self) -> int | None:
        value = (self.config or {}).get("aigc_max_score")
        return int(value) if isinstance(value, (int, float)) else None

    @property
    def aigc_required(self) -> bool:
        return bool((self.config or {}).get("aigc_required"))

    @property
    def aigc_status(self) -> str:
        value = (self.config or {}).get("aigc_status")
        return str(value) if isinstance(value, str) and value else "not_required"

    @property
    def aigc_initial_max_score(self) -> int | None:
        value = (self.config or {}).get("aigc_initial_max_score")
        return int(value) if isinstance(value, (int, float)) else None

    @property
    def aigc_final_max_score(self) -> int | None:
        value = (self.config or {}).get("aigc_final_max_score")
        return int(value) if isinstance(value, (int, float)) else None

    @property
    def aigc_rewrite_rounds(self) -> int:
        value = (self.config or {}).get("aigc_rewrite_rounds")
        return int(value) if isinstance(value, (int, float)) else 0

    @property
    def aigc_quality_passed(self) -> bool | None:
        value = (self.config or {}).get("aigc_quality_passed")
        return value if isinstance(value, bool) else None

    @property
    def aigc_report_available(self) -> bool:
        return bool((self.config or {}).get("aigc_report_available"))

    @property
    def aigc_manual_review_reason(self) -> str | None:
        value = (self.config or {}).get("aigc_manual_review_reason")
        return str(value) if isinstance(value, str) and value.strip() else None
