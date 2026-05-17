"""Celery tasks for standalone Excel-driven cover generation."""
from __future__ import annotations

import logging
import json

from app.services.cover import generate_cover_file_from_metadata
from app.services.cover_jobs import image_path, read_status, rows_path, write_status
from celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="tasks.generate_cover_job", max_retries=0)
def generate_cover_job(_self, job_id: str) -> dict:
    status = read_status(job_id)
    rows = json.loads(rows_path(job_id).read_text(encoding="utf-8"))
    total = max(len(rows), 1)
    completed = 0
    failed = 0

    status["status"] = "processing"
    status["current_step"] = "开始生成封面"
    write_status(job_id, status)

    for idx, row in enumerate(rows, start=1):
        title = str(row.get("title") or row.get("书名") or f"cover_{idx}").strip()
        status = read_status(job_id)
        item = status["items"][idx - 1]
        item["status"] = "processing"
        status["current_step"] = f"正在生成第 {idx}/{total} 张：《{title}》"
        status["progress"] = int(((idx - 1) / total) * 100)
        write_status(job_id, status)

        try:
            path = image_path(job_id, idx, title)
            generate_cover_file_from_metadata(row, path)
            completed += 1
            status = read_status(job_id)
            item = status["items"][idx - 1]
            item["status"] = "completed"
            item["image_url"] = f"/api/v1/covers/jobs/{job_id}/items/{idx}/image"
            item["error"] = None
        except Exception as exc:
            logger.exception("[cover job %s] item %s failed", job_id, idx)
            failed += 1
            status = read_status(job_id)
            item = status["items"][idx - 1]
            item["status"] = "failed"
            item["error"] = str(exc)[:1000]

        done = completed + failed
        status["completed"] = completed
        status["failed"] = failed
        status["progress"] = int((done / total) * 100)
        status["current_step"] = f"已完成 {completed} 张，失败 {failed} 张"
        write_status(job_id, status)

    status = read_status(job_id)
    status["status"] = "failed" if completed == 0 else "completed"
    status["progress"] = 100
    status["current_step"] = "封面生成完成" if completed else "封面生成失败"
    write_status(job_id, status)
    return {
        "job_id": job_id,
        "status": status["status"],
        "completed": completed,
        "failed": failed,
    }
