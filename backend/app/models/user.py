"""User ORM - 邮箱 / 手机号 + 密码 注册的账户。"""
import uuid

from sqlalchemy import Boolean, Column, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.db.session import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # 邮箱 / 手机号至少有一个，两者都建唯一索引（NULL 不参与唯一性约束）
    email = Column(String(255), nullable=True, unique=True, index=True)
    phone = Column(String(32), nullable=True, unique=True, index=True)

    password_hash = Column(String(255), nullable=False)

    name = Column(String(64), nullable=False, default="墨语者")
    avatar_url = Column(String(2048), nullable=True)
    is_vip = Column(Boolean, nullable=False, default=False)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id} email={self.email} phone={self.phone}>"
