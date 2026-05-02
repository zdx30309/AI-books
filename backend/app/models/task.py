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
