"""
Gravity Singularity - Celery 流水线任务

当前主任务 process_video 已切换为“新引擎为主、旧工作流做外壳”的融合模式：
- 轻抖解析仍保留为输入层
- 新 content_engine 负责蓝图 + 正文 + 洗稿 + 终审
- Task / Chapter 仍用于状态回写与可观测性
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.workflow import step1_generate_blueprint
from app.core.quality_metrics import measure_quality, format_report
from app.db.session import SessionLocal
from app.models import Chapter, Task, TaskStatus
from app.services.content_engine import generate_full_book
from app.services.qingdou import extract_video_text_with_qingdou
from celery_app import celery_app

logger = logging.getLogger(__name__)

_CHAPTER_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


# ============================================================
# 内部工具
# ============================================================


def _update_progress(
    db: Session,
    task: Task,
    *,
    progress: int,
    step: str,
    status: str | None = None,
) -> None:
    """统一的进度回写入口。每次都 commit 以保证 API 端 GET 立即可见。"""
    task.progress = max(0, min(100, int(progress)))
    task.current_step = step[:255]
    if status is not None:
        task.status = status
    db.commit()
    logger.info(
        "[task %s] progress=%d step=%s status=%s",
        task.id, task.progress, task.current_step, task.status,
    )


def _fetch_video_text(source_url: str, config: dict[str, Any]) -> str:
    """Step 1: 获取视频纯文本文案。"""
    for key in ("raw_text", "video_text", "transcript"):
        val = config.get(key)
        if val and isinstance(val, str) and val.strip():
            logger.info("使用 config['%s'] 提供的预解析文案 (%d 字)", key, len(val))
            return val.strip()

    max_duration = int(config.get("max_video_duration") or settings.QINGDOU_MAX_VIDEO_DURATION)
    poll_interval = float(config.get("poll_interval_seconds") or settings.QINGDOU_POLL_INTERVAL_SECONDS)
    poll_timeout = int(config.get("poll_timeout_seconds") or settings.QINGDOU_POLL_TIMEOUT_SECONDS)

    logger.info("调用轻抖 API 解析视频: source_url=%s", source_url)
    text, duration, result = extract_video_text_with_qingdou(
        source_url,
        timeout_seconds=poll_timeout,
        poll_interval_seconds=poll_interval,
        max_video_duration=max_duration,
    )
    logger.info(
        "轻抖解析完成: batch_id=%s task_id=%s duration=%s",
        result.batch_id, result.task_id, duration,
    )
    return text


def _patch_config(task: Task, **kv: Any) -> None:
    cfg = dict(task.config or {})
    cfg.update(kv)
    task.config = cfg


def _persist_chapters_from_markdown(db: Session, task: Task, markdown: str) -> int:
    """从新引擎生成的 Markdown 中拆出章节并落库，维持旧 API 可观测性。"""
    matches = list(_CHAPTER_HEADING_RE.finditer(markdown))
    if not matches:
        chapter = Chapter(task_id=task.id, chapter_num=1, title="全文", content=markdown, word_count=len(markdown))
        db.add(chapter)
        db.commit()
        return 1

    for idx, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown)
        content = markdown[start:end].strip()
        chapter = Chapter(
            task_id=task.id,
            chapter_num=idx + 1,
            title=title,
            content=content or markdown,
            word_count=len(content or markdown),
        )
        db.add(chapter)
    db.commit()
    return len(matches)


# ============================================================
# 主任务: 视频转网文全流程
# ============================================================


@celery_app.task(
    bind=True,
    name="tasks.process_video",
    max_retries=0,
)
def process_video(self, task_id: str) -> dict:
    """主流水线 - 由 POST /api/v1/tasks 触发，task_id 为已建好的 Task.id。"""
    db: Session = SessionLocal()
    task: Task | None = None

    try:
        task = db.get(Task, uuid.UUID(task_id))
        if task is None:
            raise RuntimeError(f"Task {task_id} 不存在 (可能在入队前被删除)")

        task.status = TaskStatus.PROCESSING
        task.error_message = None
        _update_progress(db, task, progress=1, step="任务启动")

        _update_progress(db, task, progress=8, step="正在获取视频文案")
        video_text = _fetch_video_text(task.source_url, task.config or {})
        _patch_config(task, raw_text_chars=len(video_text))
        _update_progress(db, task, progress=12, step=f"文案就绪 ({len(video_text)} 字符)")

        anxiety_point = str((task.config or {}).get("anxiety_point") or "用户关心的核心议题")
        category = str((task.config or {}).get("category") or "social_science")

        _update_progress(db, task, progress=18, step="新引擎生成蓝图")
        output_dir = settings.PROJECT_ROOT / "backend" / "output" / f"task_{task.id}"
        output_dir.mkdir(parents=True, exist_ok=True)
        blueprint_path = asyncio.run(
            step1_generate_blueprint(
                anxiety_point,
                video_text,
                output_dir,
                category=category,
            )
        )
        _patch_config(task, blueprint_path=str(blueprint_path))
        _update_progress(db, task, progress=32, step="蓝图就绪，进入正文生成")

        blueprint = json.loads(Path(blueprint_path).read_text(encoding="utf-8"))
        markdown = asyncio.run(
            generate_full_book(
                anxiety_point,
                blueprint,
                category=category,
                output_dir=output_dir,
            )
        )
        _patch_config(task, markdown_chars=len(markdown))

        chapter_count = _persist_chapters_from_markdown(db, task, markdown)
        _patch_config(task, markdown=markdown)

        report = measure_quality(markdown)
        report_text = format_report(report)
        report_path = output_dir / "quality_report.txt"
        report_path.write_text(report_text, encoding="utf-8")
        _patch_config(task, quality_report=str(report_path), quality_report_summary=report.total_violations)

        _update_progress(
            db,
            task,
            progress=100,
            step=f"全部完成: {chapter_count} 章已入库",
            status=TaskStatus.COMPLETED,
        )

        return {
            "task_id": str(task.id),
            "status": TaskStatus.COMPLETED,
            "chapters": chapter_count,
        }

    except Exception as e:
        logger.exception("[task %s] 流水线失败", task_id)
        try:
            if task is None:
                task = db.get(Task, uuid.UUID(task_id))
            if task is not None:
                task.status = TaskStatus.FAILED
                task.error_message = f"{type(e).__name__}: {e}"[:2000]
                db.commit()
        except Exception:
            logger.exception("[task %s] 写入失败状态时再次出错", task_id)
        raise
    finally:
        db.close()
