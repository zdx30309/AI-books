"""
Gravity Singularity - FastAPI 入口

启动:
  uvicorn main:app --reload --host 0.0.0.0 --port 8000

OpenAPI 文档: http://localhost:8000/docs
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.core.config import settings
from app.db.session import init_db

# ============================================================
# 日志
# ============================================================

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gravity_singularity")


# ============================================================
# 生命周期: 启动时尝试建表 (MVP 友好, 生产请用 Alembic)
# ============================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 60)
    logger.info("Gravity Singularity 启动中...")
    logger.info("ENV=%s  DB=%s", settings.APP_ENV, settings.DATABASE_URL.split("@")[-1])
    logger.info("=" * 60)
    try:
        init_db()
        logger.info("init_db: 表结构就绪 (create_all 幂等)")
    except Exception as e:
        # 不阻塞启动: /health 仍可用, 业务接口会在 DB 失败时报 500
        logger.warning("init_db 失败 (数据库未就绪?): %s", e)
    yield
    logger.info("Gravity Singularity 关闭")


# ============================================================
# FastAPI 实例 + CORS (蓝图要求: 允许所有来源)
# ============================================================

app = FastAPI(
    title="Gravity Singularity Backend",
    description="视频 -> 5000 字网文章节的 AI 流水线 (DeepSeek V4 + GPT Image)",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,   # 默认 ["*"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

app.include_router(api_router)
