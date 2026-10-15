"""Bridge into the existing Celery Task lease, heartbeat and accepted Chapter rows."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Chapter, Task, TaskStatus
from app.services.general_longform import GeneralLongformRunner, initial_state, load_state
from app.services.general_longform_runtime import GeneralLongformPaused, atomic_json, now_iso
from app.services.video_sources import extract_source_url


def _needs_video_source(source_url: str, cfg: dict, state: dict) -> bool:
    """Reuse short-form share-text parsing; never retroactively rewrite a book."""
    request = cfg.get("general_longform_request") or {}
    return (
        not cfg.get("general_longform_import_first")
        and not str(cfg.get("raw_text") or "").strip()
        and extract_source_url(source_url).startswith(("http://", "https://"))
        and request.get("action", "plan") == "plan"
        and not any(state.get(key) for key in (
            "source_text", "source_summary", "outline", "outline_candidate", "chapters", "materials",
        ))
    )


def process_claimed_task(db: Session, task: Task, run_id: str, output_dir: Path) -> dict:
    # Lazy import preserves the original task module and its recovery semantics.
    from app.tasks.tasks import _clear_task_run, _ensure_active_run, _read_task_run_state, _resolve_video_text_for_run, _update_progress

    directory = output_dir / "general_longform"
    state = load_state(directory)
    cfg = dict(task.config or {})
    request = cfg.get("general_longform_request")
    import_first = bool(cfg.get("general_longform_import_first")) and not request and not state
    created_state = state is None
    if state is None:
        source = str(cfg.get("raw_text") or "")
        state = initial_state(str(task.id), source, version=cfg.get("general_longform_policy"))
    source_resolved = False
    if _needs_video_source(task.source_url, cfg, state):
        source, _duration = _resolve_video_text_for_run(task, output_dir)
        if not str(source or "").strip():
            raise ValueError("参考视频尚未提取到有效正文，已停止策划；请查看解析结果或导入逐字稿")
        state["source_text"] = source
        source_resolved = True
    if created_state or source_resolved:
        # This also repairs an early v02 empty-source checkpoint on an explicit
        # planning resume. Once extracted, never pay to fetch the video again.
        atomic_json(directory / "state.json", state)
    if import_first:
        state["phase"] = "awaiting_materials"
        atomic_json(directory / "state.json", state)
    else:
        if not request:
            request = {"id": str(uuid4()), "action": "plan", "created_at": now_iso()}
            cfg["general_longform_request"] = request
            task.config = cfg
            db.commit()
        def checkpoint() -> None:
            _ensure_active_run(db, task, run_id)
            current_status, current_config = _read_task_run_state(db, task)
            if current_status == TaskStatus.PAUSED or current_config.get("pause_requested"):
                raise GeneralLongformPaused("通用长篇已在持久化断点暂停")

        runner = GeneralLongformRunner(
            directory, state, request,
            checkpoint=checkpoint,
            progress=lambda value, label: _update_progress(db, task, progress=value, step=label, run_id=run_id),
        )
        state = runner.run()
    _ensure_active_run(db, task, run_id)
    # Reconcile only accepted, hash-verified text. Drafts and repairs never enter
    # the app's Chapter table, and no Epic/fiction canon table is touched.
    runner = GeneralLongformRunner(directory, state, request or {"id": str(uuid4()), "action": "plan"})
    accepted = runner.accepted()
    rows = {row.chapter_num: row for row in db.query(Chapter).filter(Chapter.task_id == task.id).all()}
    for item in accepted:
        row = rows.get(item["number"])
        if row is None:
            row = Chapter(task_id=task.id, chapter_num=item["number"])
            db.add(row)
        row.title = item["title"]
        row.content = item["content"]
        row.word_count = item["word_count"]
    cfg = dict(task.config or {})
    cfg["pause_requested"] = False
    cfg.update(general_longform_phase=state["phase"], general_longform_outline_version=state["outline_version"],
               general_longform_accepted_chapters=len(accepted), markdown_chars=state["total_words"],
               general_longform_policy=state["prompt_version"])
    if state.get("outline"):
        plan = state["outline"]
        cfg["title"] = plan["packaging"][plan["recommended_index"]]["title"]
    if state["phase"] != "completed":
        cfg.pop("markdown", None)
    if state["phase"] == "completed":
        cfg["markdown"] = (directory / "manuscript.md").read_text(encoding="utf-8")
        # Existing read-only stage/download consumers can find the final book.
        from app.services.general_longform_runtime import atomic_text
        atomic_text(output_dir / "04_format.md", cfg["markdown"])
    cfg["aigc_required"] = False
    cfg["aigc_status"] = "not_required"
    task.config = cfg
    _clear_task_run(task, run_id)
    if state["phase"] == "completed":
        task.status, task.progress, task.current_step = TaskStatus.COMPLETED, 100, "通用长篇：全书终审、字数和Word文件验收完成"
    elif state["phase"] == "needs_review":
        task.status, task.current_step = TaskStatus.MANUAL_REVIEW, "通用长篇：已保留正文与断点，请查看具体待处理项"
    else:
        task.status = TaskStatus.PAUSED
        task.progress = 15 if not accepted else min(95, 15 + int(80 * len(accepted) / len(state["outline"]["chapters"])))
        task.current_step = {
            "awaiting_materials": "通用长篇：请上传已有大纲、正文或配套资料",
            "awaiting_outline_confirmation": "通用长篇：五组包装和大纲已完成，等待确认",
            "ready_for_chapter": f"通用长篇：已验收{len(accepted)}章，等待下一章操作",
            "ready_for_assembly": "通用长篇：全部章节已验收，等待全书终审合稿",
            "paused": "通用长篇：已在断点暂停，可从工作区恢复",
        }.get(state["phase"], "通用长篇：等待下一步操作")
    task.error_message = state.get("last_error")
    db.commit()
    return {"task_id": str(task.id), "status": task.status, "chapters": len(accepted), "phase": state["phase"]}
