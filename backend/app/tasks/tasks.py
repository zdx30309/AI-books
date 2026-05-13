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

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT, settings
from app.core.quality_metrics import measure_quality, format_report
from app.db.session import SessionLocal
from app.models import Chapter, Task, TaskStatus
from app.prompts.goal_driven_templates import get_category_meta, normalize_category
from app.services.content_engine import generate_full_book, normalize_final_markdown
from app.services.qingdou import extract_video_text_with_qingdou
from celery_app import celery_app

logger = logging.getLogger(__name__)

_CHAPTER_HEADING_RE = re.compile(r"^@\s*(\d+)\.\s*(.+?)\s*$", re.MULTILINE)
_BODY_SECTION_RE = re.compile(r"^@?\s*正文\s*$", re.MULTILINE)
_BOOK_TITLE_SECTION_RE = re.compile(
    r"^@?\s*(?:最终书名|三个备选书名)\s*$([\s\S]*?)(?=^@?\s*(?:最终简介|三个备选简介|目录)\s*$|^@|\Z)",
    re.MULTILINE,
)
_BOOK_TITLE_LINE_RE = re.compile(r"^\s*(?:\d+[\.、]\s*|[-*]\s*)?(.+?)\s*$")


class StaleTaskRun(RuntimeError):
    pass


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
    run_id: str | None = None,
) -> None:
    """统一的进度回写入口。每次都 commit 以保证 API 端 GET 立即可见。"""
    if run_id is not None:
        _ensure_active_run(db, task, run_id)
    task.progress = max(0, min(100, int(progress)))
    task.current_step = step[:255]
    if status is not None:
        task.status = status
    db.commit()
    logger.info(
        "[task %s] progress=%d step=%s status=%s",
        task.id, task.progress, task.current_step, task.status,
    )


def _read_task_run_state(db: Session, task: Task) -> tuple[str, dict[str, Any]]:
    with db.no_autoflush:
        row = db.execute(
            select(Task.status, Task.config).where(Task.id == task.id)
        ).one()
    return str(row[0]), dict(row[1] or {})


def _ensure_active_run(db: Session, task: Task, run_id: str) -> None:
    current_status, current_config = _read_task_run_state(db, task)
    if current_status == TaskStatus.COMPLETED:
        raise StaleTaskRun(f"task {task.id} already completed")
    active_run_id = current_config.get("active_run_id")
    if active_run_id and active_run_id != run_id:
        raise StaleTaskRun(
            f"task {task.id} owned by newer run {active_run_id}, current run {run_id} is stale"
        )


def _fetch_video_text(source_url: str, config: dict[str, Any]) -> str:
    """Step 1: 获取视频纯文本文案。"""
    category = str(config.get("category") or "").strip()
    for key in ("raw_text", "video_text", "transcript"):
        val = config.get(key)
        if val and isinstance(val, str) and val.strip():
            logger.info("使用 config['%s'] 提供的预解析文案 (%d 字)", key, len(val))
            return val.strip()

    if category == "classical_chinese":
        raise ValueError("古文白话题材请直接粘贴文言文原文，不需要填写视频链接")

    raw_max_duration = config.get("max_video_duration")
    if raw_max_duration is None:
        raw_max_duration = settings.QINGDOU_MAX_VIDEO_DURATION
    max_duration = int(raw_max_duration) if raw_max_duration not in ("", None) else 0
    max_duration_arg = max_duration if max_duration > 0 else None
    poll_interval = float(config.get("poll_interval_seconds") or settings.QINGDOU_POLL_INTERVAL_SECONDS)
    poll_timeout = int(config.get("poll_timeout_seconds") or settings.QINGDOU_POLL_TIMEOUT_SECONDS)

    logger.info("调用轻抖 API 解析视频: source_url=%s max_duration=%s", source_url, max_duration_arg or "unlimited")
    text, duration, result = extract_video_text_with_qingdou(
        source_url,
        timeout_seconds=poll_timeout,
        poll_interval_seconds=poll_interval,
        max_video_duration=max_duration_arg,
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


def _user_facing_generation_error(exc: Exception) -> str:
    raw = f"{type(exc).__name__}: {exc}"
    normalized = raw.lower()
    if "insufficient_quota" in normalized or "exceeded your current quota" in normalized:
        return "OpenAI 额度不足或账单不可用，请检查当前 API Key 的余额、账单状态或更换可用 Key 后重试。"
    if "rate_limit" in normalized or "429" in normalized:
        return "OpenAI 请求被限流，请稍后重试，或降低并发/更换可用模型配置。"
    if "timeout" in normalized or "timed out" in normalized:
        return "模型调用超时，任务已停止。可以稍后点击继续生成重试。"
    if "api key" in normalized or "unauthorized" in normalized or "401" in normalized:
        return "模型 API Key 无效或未授权，请检查服务器环境变量后重试。"
    return raw[:2000]


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
    """Cover generation is temporarily disabled by product request."""
    task.cover_url = None
    _patch_config(task, cover_disabled=True, cover_error=None)


def _persist_chapters_from_markdown(db: Session, task: Task, markdown: str) -> int:
    """从新引擎生成的 Markdown 中拆出章节并落库，维持旧 API 可观测性。"""
    db.query(Chapter).filter(Chapter.task_id == task.id).delete(synchronize_session=False)
    db.flush()

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
    run_id = str(getattr(getattr(_self, "request", None), "id", "") or uuid.uuid4())

    try:
        task = db.get(Task, uuid.UUID(task_id))
        if task is None:
            raise RuntimeError(f"Task {task_id} 不存在 (可能在入队前被删除)")

        if task.status == TaskStatus.COMPLETED:
            return {"task_id": str(task.id), "status": TaskStatus.COMPLETED, "chapters": len(task.chapters)}

        _patch_config(task, active_run_id=run_id)
        task.status = TaskStatus.PROCESSING
        task.error_message = None
        _update_progress(db, task, progress=1, step="任务启动", run_id=run_id)

        _update_progress(db, task, progress=8, step="正在获取视频文案", run_id=run_id)
        db.flush()
        video_text = _fetch_video_text(task.source_url, task.config or {})
        _patch_config(task, raw_text_chars=len(video_text))
        _update_progress(db, task, progress=15, step=f"文案就绪 ({len(video_text)} 字符)", run_id=run_id)

        category = normalize_category(str((task.config or {}).get("category") or "social_science"))
        category_meta = get_category_meta(category)
        _patch_config(
            task,
            category=category,
            category_label=category_meta["label"],
            category_prompt_status=category_meta["prompt_status"],
        )

        output_dir = PROJECT_ROOT / "backend" / "output" / f"task_{task.id}"
        output_dir.mkdir(parents=True, exist_ok=True)

        _update_progress(db, task, progress=25, step="即将启动主模型", run_id=run_id)

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
                        run_id=run_id,
                    )
                    return
                mapped = _STAGE_TO_PROGRESS.get(stage)
                if mapped is None:
                    return
                prog, step = mapped
                chars = info.get("chars")
                if chars:
                    step = f"{step} ({chars:,} 字)"
                _update_progress(db, task, progress=prog, step=step, run_id=run_id)
            except StaleTaskRun:
                raise
            except Exception:
                logger.exception("on_stage 回调写库失败 stage=%s", stage)

        db.flush()
        _ensure_active_run(db, task, run_id)
        markdown = asyncio.run(
            generate_full_book(
                video_text,
                category=category,
                output_dir=output_dir,
                on_stage=_on_stage,
                style_reference=str((task.config or {}).get("style_reference") or "").strip() or None,
                style_notes=str((task.config or {}).get("style_notes") or "").strip() or None,
            )
        )
        _ensure_active_run(db, task, run_id)
        markdown = normalize_final_markdown(markdown, category=category)
        _patch_config(task, markdown_chars=len(markdown))
        _update_progress(db, task, progress=92, step="全文生成完成，正在写入章节", run_id=run_id)

        book_title = _extract_book_title(markdown)
        _ensure_active_run(db, task, run_id)
        chapter_count = _persist_chapters_from_markdown(db, task, markdown)
        _patch_config(task, markdown=markdown, title=book_title)

        _update_progress(db, task, progress=94, step="正在写入质量报告", run_id=run_id)
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
            run_id=run_id,
        )

        return {
            "task_id": str(task.id),
            "status": TaskStatus.COMPLETED,
            "chapters": chapter_count,
        }

    except StaleTaskRun as e:
        logger.warning("[task %s] stale run skipped: %s", task_id, e)
        db.rollback()
        latest = db.get(Task, uuid.UUID(task_id))
        return {
            "task_id": task_id,
            "status": latest.status if latest is not None else "stale",
            "chapters": len(latest.chapters) if latest is not None else 0,
        }

    except Exception as e:
        logger.exception("[task %s] 流水线失败", task_id)
        try:
            if task is None:
                task = db.get(Task, uuid.UUID(task_id))
            if task is not None:
                message = _user_facing_generation_error(e)
                task.status = TaskStatus.FAILED
                task.error_message = message[:2000]
                task.current_step = f"失败：{message}"[:255]
                db.commit()
        except Exception:
            logger.exception("[task %s] 写入失败状态时再次出错", task_id)
        raise
    finally:
        db.close()
