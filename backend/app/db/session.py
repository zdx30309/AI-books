"""
Gravity Singularity - 数据库连接 / 会话 / Base
被 FastAPI (sync get_db 依赖) 和 Celery worker (with SessionLocal()) 共用。
"""
from typing import Generator

from sqlalchemy import create_engine
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
