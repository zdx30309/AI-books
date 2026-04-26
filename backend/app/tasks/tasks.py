"""
Gravity Singularity - Celery 流水线任务

核心任务 process_video:  Task.id -> 完整生成视频转网文的全流程
共 5 步, 每步实时回写 Task.progress 与 Task.current_step:

   Step 1 [ 0%- 10%]  获取视频文案
   Step 2 [10%- 30%]  剧本清洗 + 大纲 (JSON)
   Step 3 [30%- 40%]  封面海报
   Step 4 [40%- 90%]  遍历每章, 5000 字切片闭环 (services.generate_chapter)
   Step 5 [100%]      completed
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services import services as svc
from app.services.qingdou import extract_video_text_with_qingdou
from celery_app import celery_app
from app.db.session import SessionLocal
from app.models import Chapter, Task, TaskStatus

logger = logging.getLogger(__name__)


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
    """
    Step 1: 获取视频纯文本文案。

    优先级:
      1. config["raw_text"] / "video_text" / "transcript" - 直接使用 (MVP / 测试)
      2. 轻抖 API 异步解析 source_url，并按视频时长做熔断
    """
    for key in ("raw_text", "video_text", "transcript"):
        val = config.get(key)
        if val and isinstance(val, str) and val.strip():
            logger.info("使用 config['%s'] 提供的预解析文案 (%d 字)", key, len(val))
            return val.strip()

    max_duration = int(config.get("max_video_duration") or settings.QINGDOU_MAX_VIDEO_DURATION)
    poll_interval = float(config.get("poll_interval_seconds") or settings.QINGDOU_POLL_INTERVAL_SECONDS)
    poll_timeout = int(config.get("poll_timeout_seconds") or settings.QINGDOU_POLL_TIMEOUT_SECONDS)

    logger.info("调用轻抖 API 解析视频: source_url=%s", source_url)
    try:
        text, duration, result = extract_video_text_with_qingdou(
            source_url,
            timeout_seconds=poll_timeout,
            poll_interval_seconds=poll_interval,
            max_video_duration=max_duration,
        )
    except Exception:
        logger.exception("轻抖 API 解析失败: source_url=%s", source_url)
        raise
    logger.info(
        "轻抖解析完成: batch_id=%s task_id=%s duration=%s",
        result.batch_id, result.task_id, duration,
    )
    return text


def _patch_config(task: Task, **kv: Any) -> None:
    """安全地往 JSONB config 里追加字段 (整体重赋值确保 SQLAlchemy 检测到变更)。"""
    cfg = dict(task.config or {})
    cfg.update(kv)
    task.config = cfg


# ============================================================
# 主任务: 视频转网文全流程
# ============================================================


@celery_app.task(
    bind=True,
    name="tasks.process_video",
    max_retries=0,        # 业务流水线不自动重试 (LLM 已在内部 tenacity 重试)
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

        # ====================================================
        # Step 1: 获取文案 [0-10%]
        # ====================================================
        _update_progress(db, task, progress=5, step="正在获取视频文案")
        video_text = _fetch_video_text(task.source_url, task.config or {})
        _patch_config(task, raw_text_chars=len(video_text))
        _update_progress(db, task, progress=10, step=f"文案就绪 ({len(video_text)} 字符)")

        # ====================================================
        # Step 2: 剧本清洗 + 大纲 [10-30%]
        # ====================================================
        _update_progress(db, task, progress=15, step="DeepSeek 生成大纲与子场景")
        outline = svc.generate_outline(video_text)
        n_chapters = len(outline["chapters"])

        # 落库 outline 便于排错 / 重放
        _patch_config(task, outline=outline)
        _update_progress(
            db, task,
            progress=30,
            step=f"大纲完成: {n_chapters} 章, 共 {sum(len(c.get('sub_scenes') or []) for c in outline['chapters'])} 个子场景",
        )

        # ====================================================
        # Step 3: 视觉海报 [30-40%]
        # ====================================================
        _update_progress(db, task, progress=33, step="提取海报描述词")
        poster_summary = svc.summarize_outline_for_poster(outline)
        image_prompt = svc.generate_cover_prompt(poster_summary)
        _patch_config(task, image_prompt=image_prompt)

        _update_progress(db, task, progress=37, step="GPT Image 生成竖版封面")
        try:
            cover_url = svc.generate_cover_image(image_prompt)
            task.cover_url = cover_url
        except Exception as e:
            # 封面失败不阻断正文生成 (商业 demo 可降级)
            logger.warning("[task %s] 封面生成失败, 跳过: %s", task.id, e)
            task.cover_url = None
            _patch_config(task, cover_error=f"{type(e).__name__}: {e}")

        _update_progress(db, task, progress=40, step="封面阶段完成")

        # ====================================================
        # Step 4 [核心攻坚]: 5000 字章节群 [40-90%]
        # ====================================================
        global_setting = svc.build_global_setting(outline)
        chapters_meta = outline["chapters"]
        progress_span = 50  # 40% -> 90%

        for idx, ch_meta in enumerate(chapters_meta):
            ch_num = ch_meta.get("chapter_num", idx + 1)
            ch_title = ch_meta.get("title", f"第{ch_num}章")

            # 进入该章: 进度按 idx 比例
            _update_progress(
                db, task,
                progress=40 + int(idx / n_chapters * progress_span),
                step=f"撰写第 {ch_num}/{n_chapters} 章: 《{ch_title}》",
            )

            content, word_count = svc.generate_chapter(
                global_setting=global_setting,
                chapter=ch_meta,
            )

            # 落 Chapter 行 (单独事务, 一章一保存, 失败不丢已成功的章)
            chapter_row = Chapter(
                task_id=task.id,
                chapter_num=ch_num,
                title=ch_title,
                content=content,
                word_count=word_count,
            )
            db.add(chapter_row)
            db.commit()

            _update_progress(
                db, task,
                progress=40 + int((idx + 1) / n_chapters * progress_span),
                step=f"第 {ch_num}/{n_chapters} 章入库 ({word_count} 字)",
            )

        # ====================================================
        # Step 5: 完结 [100%]
        # ====================================================
        _update_progress(
            db, task,
            progress=100,
            step=f"全部完成: {n_chapters} 章已入库",
            status=TaskStatus.COMPLETED,
        )

        return {
            "task_id": str(task.id),
            "status": TaskStatus.COMPLETED,
            "chapters": n_chapters,
        }

    except Exception as e:
        logger.exception("[task %s] 流水线失败", task_id)
        # 故障落库 (尽力而为, 不影响异常向上抛出)
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
