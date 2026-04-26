"""
Gravity Singularity - SQLAlchemy ORM 模型
- Task   : 一个视频转网文任务的全生命周期记录
- Chapter: 单个章节正文 (5000+ 字)
"""
import uuid

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.session import Base


class TaskStatus:
    """Task.status 的合法取值常量。"""

    PENDING = "pending"          # 已入队，未开始
    PROCESSING = "processing"    # Celery 处理中
    COMPLETED = "completed"      # 全流程成功
    FAILED = "failed"            # 任意阶段失败


class Task(Base):
    __tablename__ = "tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_url = Column(String(2048), nullable=False)

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

    chapters = relationship(
        "Chapter",
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="Chapter.chapter_num",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Task id={self.id} status={self.status} progress={self.progress}>"


class Chapter(Base):
    __tablename__ = "chapters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chapter_num = Column(Integer, nullable=False)
    title = Column(String(512), nullable=False)
    content = Column(Text, nullable=False)

    # 5000字硬指标的可观测性: 直接落库便于 SQL 校验
    word_count = Column(Integer, nullable=False, default=0)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    task = relationship("Task", back_populates="chapters")

    __table_args__ = (
        Index("ix_chapter_task_num", "task_id", "chapter_num", unique=True),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Chapter task={self.task_id} num={self.chapter_num} words={self.word_count}>"
