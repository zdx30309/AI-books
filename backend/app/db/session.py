"""
Gravity Singularity - 数据库连接 / 会话 / Base
被 FastAPI (sync get_db 依赖) 和 Celery worker (with SessionLocal()) 共用。
"""
from time import sleep
from typing import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.core.config import settings


engine_kwargs = {
    "pool_pre_ping": True,
    "pool_size": max(1, int(settings.DATABASE_POOL_SIZE or 30)),
    "max_overflow": max(0, int(settings.DATABASE_MAX_OVERFLOW or 20)),
    "pool_timeout": max(1, int(settings.DATABASE_POOL_TIMEOUT or 120)),
    "pool_recycle": max(30, int(settings.DATABASE_POOL_RECYCLE_SECONDS or 300)),
    "pool_use_lifo": True,
    "future": True,
}

if settings.DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://")):
    engine_kwargs["connect_args"] = {
        "connect_timeout": max(1, int(settings.DATABASE_CONNECT_TIMEOUT or 15)),
    }

engine = create_engine(settings.DATABASE_URL, **engine_kwargs)

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

    _run_startup_schema_sync()
    _ensure_owner_id_column()
    _ensure_epic_schema_columns()


def _run_startup_schema_sync() -> None:
    last_error: OperationalError | None = None
    for attempt in range(1, 4):
        try:
            with engine.begin() as conn:
                if engine.dialect.name == "postgresql":
                    conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('video_plan_init_db_schema_sync'))"))
                Base.metadata.create_all(bind=conn)
            return
        except OperationalError as exc:
            last_error = exc
            if attempt >= 3:
                raise
            sleep(attempt * 1.5)
    if last_error is not None:
        raise last_error


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


def _ensure_epic_schema_columns() -> None:
    """Keep early Epic Novel installs compatible with newly added audit fields."""
    inspector = inspect(engine)
    if inspector.has_table("reference_download_tasks"):
        columns = {c["name"] for c in inspector.get_columns("reference_download_tasks")}
        if "generated_card_count" not in columns:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE reference_download_tasks "
                        "ADD COLUMN IF NOT EXISTS generated_card_count INTEGER NOT NULL DEFAULT 0"
                    )
                )
    if inspector.has_table("reference_pattern_cards"):
        columns = {c["name"] for c in inspector.get_columns("reference_pattern_cards")}
        ddl: list[str] = []
        if "source_section_ids" not in columns:
            ddl.append("ADD COLUMN IF NOT EXISTS source_section_ids JSONB NOT NULL DEFAULT '[]'::jsonb")
        if "source_analysis_ids" not in columns:
            ddl.append("ADD COLUMN IF NOT EXISTS source_analysis_ids JSONB NOT NULL DEFAULT '[]'::jsonb")
        if "source_book_ids" not in columns:
            ddl.append("ADD COLUMN IF NOT EXISTS source_book_ids JSONB NOT NULL DEFAULT '[]'::jsonb")
        if "source_card_ids" not in columns:
            ddl.append("ADD COLUMN IF NOT EXISTS source_card_ids JSONB NOT NULL DEFAULT '[]'::jsonb")
        if "evidence_count" not in columns:
            ddl.append("ADD COLUMN IF NOT EXISTS evidence_count INTEGER NOT NULL DEFAULT 1")
        if "raw_text_included" not in columns:
            ddl.append("ADD COLUMN IF NOT EXISTS raw_text_included BOOLEAN NOT NULL DEFAULT FALSE")
        if "extraction_model_provider" not in columns:
            ddl.append("ADD COLUMN IF NOT EXISTS extraction_model_provider VARCHAR(64)")
        if "extraction_model_name" not in columns:
            ddl.append("ADD COLUMN IF NOT EXISTS extraction_model_name VARCHAR(255)")
        if "output_hash" not in columns:
            ddl.append("ADD COLUMN IF NOT EXISTS output_hash VARCHAR(128)")
        if ddl:
            with engine.begin() as conn:
                for fragment in ddl:
                    conn.execute(text(f"ALTER TABLE reference_pattern_cards {fragment}"))
                conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_reference_pattern_cards_output_hash "
                        "ON reference_pattern_cards(output_hash)"
                    )
                )
    if inspector.has_table("reference_sections"):
        columns = {c["name"] for c in inspector.get_columns("reference_sections")}
        ddl = []
        if "token_estimate" not in columns:
            ddl.append("ADD COLUMN IF NOT EXISTS token_estimate INTEGER NOT NULL DEFAULT 0")
        if ddl:
            with engine.begin() as conn:
                for fragment in ddl:
                    conn.execute(text(f"ALTER TABLE reference_sections {fragment}"))
    if inspector.has_table("reference_section_analyses"):
        columns = {c["name"] for c in inspector.get_columns("reference_section_analyses")}
        ddl = []
        if "analysis_hash" not in columns:
            ddl.append("ADD COLUMN IF NOT EXISTS analysis_hash VARCHAR(128)")
        if ddl:
            with engine.begin() as conn:
                for fragment in ddl:
                    conn.execute(text(f"ALTER TABLE reference_section_analyses {fragment}"))
                conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_reference_section_analyses_analysis_hash "
                        "ON reference_section_analyses(analysis_hash)"
                    )
                )
    if inspector.has_table("epic_retrieval_traces"):
        columns = {c["name"] for c in inspector.get_columns("epic_retrieval_traces")}
        ddl = []
        if "used_profile_sections" not in columns:
            ddl.append("ADD COLUMN IF NOT EXISTS used_profile_sections JSONB NOT NULL DEFAULT '[]'::jsonb")
        if "profile_version" not in columns:
            ddl.append("ADD COLUMN IF NOT EXISTS profile_version VARCHAR(255)")
        if "excluded_context" not in columns:
            ddl.append("ADD COLUMN IF NOT EXISTS excluded_context JSONB NOT NULL DEFAULT '[]'::jsonb")
        if "raw_text_included" not in columns:
            ddl.append("ADD COLUMN IF NOT EXISTS raw_text_included BOOLEAN NOT NULL DEFAULT FALSE")
        if "raw_json" not in columns:
            ddl.append("ADD COLUMN IF NOT EXISTS raw_json JSONB NOT NULL DEFAULT '{}'::jsonb")
        if ddl:
            with engine.begin() as conn:
                for fragment in ddl:
                    conn.execute(text(f"ALTER TABLE epic_retrieval_traces {fragment}"))
    if inspector.has_table("epic_generation_logs"):
        columns = {c["name"] for c in inspector.get_columns("epic_generation_logs")}
        if "actual_cost" not in columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE epic_generation_logs ADD COLUMN IF NOT EXISTS actual_cost FLOAT"))
