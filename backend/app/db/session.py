"""
Gravity Singularity - 数据库连接 / 会话 / Base
被 FastAPI (sync get_db 依赖) 和 Celery worker (with SessionLocal()) 共用。
"""
from typing import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.core.config import settings


engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    future=True,
    expire_on_commit=False,
)

Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：每请求一个独立 Session。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """开发环境快速建表；生产请用 Alembic 迁移。"""
    from app import models  # noqa: F401  确保所有模型类被注册到 metadata

    Base.metadata.create_all(bind=engine)
    _ensure_owner_id_column()


def _ensure_owner_id_column() -> None:
    """给旧数据库补齐 tasks.owner_id，保持启动时自动修复。"""
    inspector = inspect(engine)
    if not inspector.has_table("tasks"):
        return
    columns = {c["name"] for c in inspector.get_columns("tasks")}
    if "owner_id" in columns:
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS owner_id UUID REFERENCES users(id) ON DELETE CASCADE"
            )
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tasks_owner_id ON tasks(owner_id)"))
