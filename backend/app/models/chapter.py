"""
Chapter ORM - 单个章节正文 (5000+ 字)。
"""
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.session import Base


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
