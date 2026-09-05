from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models import TaskStatus
from app.tasks import recovery, system_tasks
from app.tasks import tasks as task_module


def _manuscript(*, heading: str = "@1. 第一章", body: str = "这是一个足够长的章节正文。") -> str:
    return (
        "@最终书名\n测试书\n\n"
        "@最终简介\n测试简介\n\n"
        "@目录\n1. 第一章\n\n"
        "@正文\n"
        f"{heading}\n{body}\n"
    )


def test_strict_manuscript_parts_accepts_supported_markers() -> None:
    for body_heading, body_marker in (
        ("@1. 第一章", "@正文"),
        ("！！！@1. 第一章", "！！！@正文"),
    ):
        text = _manuscript(heading=body_heading).replace("@正文", body_marker)
        normalized, title, chapters, errors = recovery._strict_manuscript_parts(
            text,
            expected_chapters=1,
            minimum_chapter_chars=1,
        )
        assert normalized
        assert title == "测试书"
        assert [(number, chapter_title) for number, chapter_title, _ in chapters] == [(1, "第一章")]
        assert errors == []


def test_strict_manuscript_parts_rejects_gaps_and_empty_chapters() -> None:
    text = _manuscript(heading="@2. 第二章", body="")
    _, _, chapters, errors = recovery._strict_manuscript_parts(
        text,
        expected_chapters=2,
        minimum_chapter_chars=10,
    )
    assert chapters
    assert any("not contiguous" in error for error in errors)
    assert any("empty or truncated" in error for error in errors)


def test_prepare_task_for_retry_clears_safety_hold_but_keeps_response_id(monkeypatch: pytest.MonkeyPatch) -> None:
    task_id = uuid4()
    task = SimpleNamespace(
        id=task_id,
        config={
            "generation_hold": True,
            "paid_call_delivery_uncertain": True,
            "paid_call_response_id": "resp_saved",
            "paid_api_backoff": True,
            "auto_retry_not_before": "later",
        },
        status=TaskStatus.FAILED,
        progress=0,
        current_step="失败",
        error_message="old",
    )
    # Importing the router here avoids making the entire API router a fixture
    # dependency for the pure task-module tests above.
    from app.api.routers import tasks as router

    monkeypatch.setattr(router, "_retry_resume_state", lambda _task_id: (87, "从断点继续"))
    router._prepare_task_for_retry(task)
    assert task.status == TaskStatus.PENDING
    assert task.config["paid_call_response_id"] == "resp_saved"
    for key in ("generation_hold", "paid_call_delivery_uncertain", "paid_api_backoff", "auto_retry_not_before"):
        assert key not in task.config


def test_maintenance_passes_run_in_required_order(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def make(name: str, result: int):
        def run() -> int:
            calls.append(name)
            return result

        return run

    monkeypatch.setattr(system_tasks.settings, "STALE_TASK_WATCHDOG_ENABLED", True)
    monkeypatch.setattr(task_module, "requeue_stale_processing_tasks", make("stale", 1))
    monkeypatch.setattr(task_module, "revive_recoverable_terminal_input_reviews", make("review", 9))
    monkeypatch.setattr(task_module, "requeue_due_pending_retry_tasks", make("due", 2))
    monkeypatch.setattr(task_module, "requeue_orphan_pending_tasks", make("orphan", 3))
    monkeypatch.setattr(task_module, "recover_retryable_failed_tasks", make("failed", 4))

    from app.tasks import cover_tasks

    monkeypatch.setattr(cover_tasks, "requeue_stale_cover_jobs", make("cover", 7))
    monkeypatch.setattr(recovery, "recover_completed_integrity_tasks", make("integrity", 8))
    monkeypatch.setattr(recovery, "recover_completed_public_domain_tasks", make("public", 5))
    monkeypatch.setattr(recovery, "recover_deterministic_final_format_tasks", make("format", 6))

    assert system_tasks.requeue_stale_processing_task.run() == 45
    assert calls == ["integrity", "public", "format", "stale", "review", "due", "orphan", "failed", "cover"]


def test_deterministic_recovery_promotes_only_a_valid_local_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_id = uuid4()
    output_dir = tmp_path / "backend" / "output" / f"task_{task_id}"
    output_dir.mkdir(parents=True)
    (output_dir / "03_refine.md").write_text(
        _manuscript(
            body=(
                "这是一个足够长的章节正文，用于验证确定性恢复不会调用任何模型，"
                "并且会在写入章节前完成结构检查、质量报告和状态更新。"
            )
        ),
        encoding="utf-8",
    )

    task = SimpleNamespace(
        id=task_id,
        status=TaskStatus.FAILED,
        progress=89,
        current_step="终稿硬伤质检失败：# 标题残留",
        error_message="终稿硬伤质检失败：# 标题残留",
        config={"category": "social_science"},
    )

    class Query:
        def filter(self, *args, **kwargs):
            return self

        def delete(self, **kwargs):
            return 0

    class DB:
        def execute(self, _statement):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [task]))

        def query(self, _model):
            return Query()

        def add(self, _row):
            pass

        def flush(self):
            pass

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(recovery, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(recovery.settings, "STALE_TASK_WATCHDOG_ENABLED", True)
    monkeypatch.setattr(recovery, "SessionLocal", lambda: DB())
    monkeypatch.setattr(recovery, "clear_task_dispatch_guard", lambda _task_id: None)
    monkeypatch.setattr(recovery, "detect_final_hard_errors", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(recovery, "normalize_final_markdown", lambda value, **_kwargs: value)

    assert recovery.recover_deterministic_final_format_tasks(limit=5) == 1
    assert task.status == TaskStatus.COMPLETED
    assert task.progress == 100
    assert (output_dir / "04_format.md").exists()
    assert (output_dir / "deterministic_recovery_report.json").exists()


def test_orphan_pending_terminal_input_is_quarantined_instead_of_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The orphan pass must handle the same terminal source failures as stale pass."""

    task = SimpleNamespace(
        id=uuid4(),
        status=TaskStatus.PENDING,
        progress=8,
        current_step="视频解析失败",
        error_message="视频解析失败：轻抖未返回可用文稿",
        created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        config={},
    )

    class DB:
        def execute(self, _statement):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [task]))

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(recovery.settings, "STALE_TASK_WATCHDOG_ENABLED", True)
    monkeypatch.setattr(task_module, "SessionLocal", lambda: DB())
    monkeypatch.setattr(task_module, "clear_task_dispatch_guard", lambda _task_id: None)
    monkeypatch.setattr(task_module, "_dispatch_task_ids", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(task_module.settings, "PENDING_TASK_ORPHAN_REQUEUE_SECONDS", 180)

    assert task_module.requeue_orphan_pending_tasks(limit=5) == 0
    assert task.status == TaskStatus.MANUAL_REVIEW
    assert task.config["terminal_input_failure"] is True


def _finance_manuscript(count: int, *, formal: bool = True, with_list: bool = False) -> str:
    catalog = "\n".join(f"{i}. 第{i}章" for i in range(1, count + 1))
    chapters: list[str] = []
    for i in range(1, count + 1):
        marker = f"@{i}. 第{i}章" if formal else f"{i}. 第{i}章"
        chapters.append(
            f"{marker}\n这是第{i}章的完整正文，用于验证章节序列和断点恢复逻辑。"
            + ("\n1. 这是章节内部的普通列表\n2. 仍然是普通列表。" if with_list else "")
        )
    return (
        "@最终书名\n财经测试书\n\n"
        "@最终简介\n这是用于完整性测试的简介。\n\n"
        f"@目录\n{catalog}\n\n"
        "@导言\n这是用于测试的导言内容。\n\n"
        "@正文\n"
        + "\n\n".join(chapters)
    )


def test_finance_plain_numbered_prose_is_not_counted_as_chapters() -> None:
    text = (
        "@目录\n1. 第一章\n2. 第二章\n\n"
        "@正文\n这里是正文。\n1. 这是普通列表\n2. 这也是普通列表\n继续正文。"
    )
    assert task_module._finance_chapter_titles(text) == []


def test_finance_formal_markers_ignore_internal_numbered_lists() -> None:
    text = _finance_manuscript(11, formal=True, with_list=True)
    titles = task_module._finance_chapter_titles(text)
    assert [number for number, _title in titles] == list(range(1, 12))


def test_completed_finance_writer38_final26_is_queued_from_checkpoint(tmp_path: Path) -> None:
    task_id = uuid4()
    output_dir = tmp_path / f"task_{task_id}"
    output_dir.mkdir()
    (output_dir / "01_writer.md").write_text(_finance_manuscript(38), encoding="utf-8")
    (output_dir / "04_format.md").write_text(_finance_manuscript(26), encoding="utf-8")
    task = SimpleNamespace(
        id=task_id,
        status=TaskStatus.COMPLETED,
        progress=100,
        current_step="完成",
        error_message=None,
        config={"category": "finance_technology"},
    )
    decision = recovery._finance_integrity_decision(task, output_dir, db_chapters=[])
    assert decision["action"] == "queue"
    assert "differs" in decision["reason"]
    assert decision["source_stage"] == "01_writer.md"


def test_completed_finance_empty_final_is_queued_from_writer(tmp_path: Path) -> None:
    task_id = uuid4()
    output_dir = tmp_path / f"task_{task_id}"
    output_dir.mkdir()
    (output_dir / "01_writer.md").write_text(_finance_manuscript(3), encoding="utf-8")
    (output_dir / "04_format.md").write_text("provider returned an empty body", encoding="utf-8")
    task = SimpleNamespace(id=task_id, config={"category": "finance_technology"})
    decision = recovery._finance_integrity_decision(task, output_dir, db_chapters=[])
    assert decision["action"] == "queue"
    assert "no strict chapter" in decision["reason"] or "final chapter sequence differs" in decision["reason"]


def test_completed_finance_valid_final_with_db_gap_requests_db_only_repair(tmp_path: Path) -> None:
    task_id = uuid4()
    output_dir = tmp_path / f"task_{task_id}"
    output_dir.mkdir()
    final = _finance_manuscript(2)
    (output_dir / "04_format.md").write_text(final, encoding="utf-8")
    task = SimpleNamespace(id=task_id, config={"category": "finance_technology"})
    decision = recovery._finance_integrity_decision(task, output_dir, db_chapters=[])
    assert decision["action"] == "db_repair"
    assert decision["db_mismatch"] is True


def test_finance_integrity_attempt_cap_moves_repeated_failure_to_manual_review(tmp_path: Path) -> None:
    task_id = uuid4()
    output_dir = tmp_path / f"task_{task_id}"
    output_dir.mkdir()
    task = SimpleNamespace(
        id=task_id,
        config={
            "category": "finance_technology",
            "integrity_recovery_attempts": 3,
        },
        status=TaskStatus.COMPLETED,
        progress=100,
        current_step="完成",
        error_message=None,
    )
    decision = {
        "reason": "still incomplete",
        "source_stage": "01_writer.md",
        "final_text": None,
        "final_parts": [],
    }
    class DB:
        def query(self, _model):
            return self

        def filter(self, *args, **kwargs):
            return self

        def delete(self, **kwargs):
            return 0

        def add(self, _row):
            pass

        def flush(self):
            pass

    db = DB()
    recovery._queue_finance_integrity_recovery(db, task, decision, output_dir)
    assert task.status == TaskStatus.MANUAL_REVIEW
    assert task.config["integrity_recovery_state"] == "manual_review"
