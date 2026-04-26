"""
Gravity Singularity - FastAPI 入口

启动:
  uvicorn main:app --reload --host 0.0.0.0 --port 8000

OpenAPI 文档: http://localhost:8000/docs
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db, init_db
from app.models import Chapter, Task, TaskStatus
from app.schemas.schemas import (
    ChapterResponse,
    ChaptersListResponse,
    CreateTaskRequest,
    CreateTaskResponse,
    HealthResponse,
    TaskStatusResponse,
)
from app.tasks.tasks import process_video

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


# ============================================================
# Meta endpoints
# ============================================================


@app.get("/", response_model=HealthResponse, tags=["meta"])
def root() -> HealthResponse:
    return HealthResponse(status="ok", app=settings.APP_NAME)


@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health() -> HealthResponse:
    return HealthResponse(status="ok", app=settings.APP_NAME)


# ============================================================
# 业务接口
# ============================================================


@app.post(
    "/api/v1/tasks",
    response_model=CreateTaskResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["tasks"],
    summary="建任务并入 Celery 队列",
)
def create_task(
    req: CreateTaskRequest,
    db: Session = Depends(get_db),
) -> CreateTaskResponse:
    """
    立即返回 task_id, 不等待生成完成。
    客户端通过轮询 GET /api/v1/tasks/{task_id} 跟进进度。
    """
    task = Task(
        source_url=req.url,
        config=req.config or {},
        status=TaskStatus.PENDING,
        progress=0,
        current_step="排队中",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    logger.info("[task %s] 建库完成, 准备投 Celery", task.id)

    # 投递 Celery (broker 不通时也要把库里状态置 failed, 避免僵尸 pending)
    try:
        process_video.delay(str(task.id))
    except Exception as e:
        logger.exception("[task %s] Celery 投递失败", task.id)
        task.status = TaskStatus.FAILED
        task.error_message = f"Celery dispatch failed: {e}"[:2000]
        db.commit()
        raise HTTPException(
            status_code=503,
            detail=f"任务已建库但 Celery 投递失败 (task_id={task.id}): {e}",
        )

    return CreateTaskResponse(task_id=task.id, status=task.status)


@app.get(
    "/api/v1/tasks/{task_id}",
    response_model=TaskStatusResponse,
    tags=["tasks"],
    summary="查询任务状态 / 进度 / 封面",
)
def get_task(
    task_id: UUID,
    db: Session = Depends(get_db),
) -> TaskStatusResponse:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return TaskStatusResponse.model_validate(task)


@app.get(
    "/api/v1/tasks/{task_id}/chapters",
    response_model=ChaptersListResponse,
    tags=["tasks"],
    summary="返回该任务下所有已生成的章节",
)
def get_chapters(
    task_id: UUID,
    db: Session = Depends(get_db),
) -> ChaptersListResponse:
    # 先确认 task 存在 (与"chapters 为空"区分开)
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    chapters = (
        db.query(Chapter)
        .filter(Chapter.task_id == task_id)
        .order_by(Chapter.chapter_num.asc())
        .all()
    )
    return ChaptersListResponse(
        task_id=task_id,
        total=len(chapters),
        chapters=[ChapterResponse.model_validate(c) for c in chapters],
    )
