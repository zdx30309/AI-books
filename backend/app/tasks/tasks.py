"""
Gravity Singularity - Celery 流水线任务

主任务 process_video 流水线：
- 轻抖解析获取视频文案
- content_engine.generate_full_book 直出全文（writer → polish → refine → format）
- Task / Chapter 用于状态回写与可观测性
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT, settings
from app.core.quality_metrics import measure_quality, format_report
from app.db.session import SessionLocal
from app.models import Chapter, Task, TaskStatus
from app.services.content_engine import generate_full_book
from app.services.cover import generate_cover_file
from app.services.qingdou import extract_video_text_with_qingdou
from celery_app import celery_app

logger = logging.getLogger(__name__)

_CHAPTER_HEADING_RE = re.compile(r"^@\s*(\d+)\.\s*(.+?)\s*$", re.MULTILINE)
_BODY_SECTION_RE = re.compile(r"^@?\s*正文\s*$", re.MULTILINE)
_BOOK_TITLE_SECTION_RE = re.compile(
    r"^@?\s*三个备选书名\s*$([\s\S]*?)(?=^@?\s*三个备选简介\s*$|^@?\s*目录\s*$|^@|\Z)",
    re.MULTILINE,
)
_BOOK_TITLE_LINE_RE = re.compile(r"^\s*(?:\d+[\.、]\s*|[-*]\s*)?(.+?)\s*$")


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


def _extract_book_title(markdown: str) -> str:
    """Pick the first generated title candidate as the book title."""
    section = _BOOK_TITLE_SECTION_RE.search(markdown or "")
    if section:
        for line in section.group(1).splitlines():
            line = line.strip()
            if not line:
                continue
            match = _BOOK_TITLE_LINE_RE.match(line)
            if match:
                title = match.group(1).strip(" -_*　")
                if title:
                    return title[:120]

    first_chapter = _CHAPTER_HEADING_RE.search(markdown or "")
    if first_chapter:
        return first_chapter.group(2).strip()[:120]
    return "未命名书稿"


def _generate_cover_safely(title: str, markdown: str, output_dir: Path, task: Task) -> None:
    """Generate cover as a best-effort side effect; never fail the book workflow."""
    try:
        cover_path = generate_cover_file(title, markdown, output_dir)
        task.cover_url = f"/api/v1/tasks/{task.id}/cover"
        _patch_config(task, cover_path=str(cover_path), cover_model=settings.IMAGE_MODEL)
    except Exception as exc:
        logger.exception("[task %s] 封面生成失败，保留书稿继续完成: %s", task.id, exc)
        _patch_config(task, cover_error=f"{type(exc).__name__}: {exc}"[:1000])


def _persist_chapters_from_markdown(db: Session, task: Task, markdown: str) -> int:
    """从新引擎生成的 Markdown 中拆出章节并落库，维持旧 API 可观测性。"""
    body_match = _BODY_SECTION_RE.search(markdown or "")
    body_offset = body_match.end() if body_match else 0
    body_text = markdown[body_offset:] if body_match else markdown
    matches = list(_CHAPTER_HEADING_RE.finditer(body_text))
    if not matches:
        chapter = Chapter(task_id=task.id, chapter_num=1, title="全文", content=markdown, word_count=len(markdown))
        db.add(chapter)
        db.commit()
        return 1

    for idx, match in enumerate(matches):
        chapter_num = int(match.group(1))
        title = match.group(2).strip()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body_text)
        content = body_text[start:end].strip()
        chapter = Chapter(
            task_id=task.id,
            chapter_num=chapter_num or idx + 1,
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
def process_video(_self, task_id: str) -> dict:
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
        db.flush()
        video_text = _fetch_video_text(task.source_url, task.config or {})
        _patch_config(task, raw_text_chars=len(video_text))
        _update_progress(db, task, progress=15, step=f"文案就绪 ({len(video_text)} 字符)")

        category = str((task.config or {}).get("category") or "social_science")

        output_dir = PROJECT_ROOT / "backend" / "output" / f"task_{task.id}"
        output_dir.mkdir(parents=True, exist_ok=True)

        _update_progress(db, task, progress=25, step="即将启动主模型")

        _STAGE_TO_PROGRESS: dict[str, tuple[int, str]] = {
            "writer_start": (28, "OpenAI 主模型生成初稿中"),
            "writer_done": (45, "主模型初稿完成"),
            "polish_start": (50, "Gemini 一润启动"),
            "polish_done": (70, "Gemini 一润完成"),
            "refine_start": (72, "Gemini 二润中"),
            "refine_done": (85, "二润完成"),
            "format_start": (87, "DeepSeek 终稿格式化中"),
            "format_done": (90, "终稿就绪"),
        }

        def _on_stage(stage: str, info: dict[str, Any]) -> None:
            try:
                if stage == "polish_chunk":
                    idx = int(info.get("idx", 0))
                    total = max(int(info.get("total", 1)), 1)
                    prog = 50 + int(20 * idx / total)
                    _update_progress(
                        db, task,
                        progress=min(prog, 70),
                        step=f"Gemini 一润 {idx}/{total} 块",
                    )
                    return
                mapped = _STAGE_TO_PROGRESS.get(stage)
                if mapped is None:
                    return
                prog, step = mapped
                chars = info.get("chars")
                if chars:
                    step = f"{step} ({chars:,} 字)"
                _update_progress(db, task, progress=prog, step=step)
            except Exception:
                logger.exception("on_stage 回调写库失败 stage=%s", stage)

        db.flush()
        markdown = asyncio.run(
            generate_full_book(
                video_text,
                category=category,
                output_dir=output_dir,
                on_stage=_on_stage,
            )
        )
        _patch_config(task, markdown_chars=len(markdown))
        _update_progress(db, task, progress=92, step="全文生成完成，正在写入章节")

        book_title = _extract_book_title(markdown)
        chapter_count = _persist_chapters_from_markdown(db, task, markdown)
        _patch_config(task, markdown=markdown, title=book_title)

        _update_progress(db, task, progress=94, step="正在生成图书封面")
        _generate_cover_safely(book_title, markdown, output_dir, task)

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
