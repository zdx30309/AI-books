"""Regression tests for the 2026-09-26 security and cost-safety fixes."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.api.routers import tasks as tasks_router
from app.core import security
from app.services import content_engine
from app.tasks import tasks as worker


def test_client_cannot_inject_server_owned_config_keys() -> None:
    cfg = tasks_router._normalize_task_config(
        {
            "category": "social_science",
            "source_text_path": "/etc/passwd",
            "style_notes_path": "../../.env",
            "poll_timeout_seconds": 1,
            "generation_hold": True,
            "paid_call_delivery_uncertain": True,
            "active_run_id": "forged",
            "title": "保留的书名",
        }
    )
    for key in ("source_text_path", "style_notes_path", "poll_timeout_seconds", "generation_hold",
                "paid_call_delivery_uncertain", "active_run_id"):
        assert key not in cfg, key
    assert cfg["title"] == "保留的书名"
    assert cfg["category"] == "social_science"


def test_worker_refuses_input_paths_outside_backend(tmp_path) -> None:
    with pytest.raises(ValueError, match="不在允许的目录"):
        worker._task_input_file("../.env")
    with pytest.raises(ValueError, match="不在允许的目录"):
        worker._task_input_file(str(tmp_path / "x.txt"))
    inside = worker._task_input_file("backend/output/task_inputs/example.txt")
    assert inside.is_relative_to((worker.PROJECT_ROOT / "backend").resolve())


class _FakeDb:
    def __init__(self, task):
        self.task = task
        self.commits = 0

    def execute(self, _stmt):
        return SimpleNamespace(scalar_one_or_none=lambda: self.task)

    def commit(self):
        self.commits += 1

    def refresh(self, _obj):
        return None


def _processing_task(run_id: str, heartbeat: datetime):
    return SimpleNamespace(
        id=uuid.uuid4(),
        status=worker.TaskStatus.PROCESSING,
        config={"active_run_id": run_id, "active_run_heartbeat_at": heartbeat.isoformat()},
        error_message=None,
        current_step="",
    )


def test_redelivered_message_for_a_live_run_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(worker, "clear_task_dispatch_guard", lambda *_: None)
    task = _processing_task("run-1", datetime.now(timezone.utc) - timedelta(seconds=30))
    with pytest.raises(worker.StaleTaskRun, match="still heartbeating"):
        worker._claim_task_run(_FakeDb(task), str(task.id), "run-1")


def test_silent_run_can_be_taken_over_by_its_redelivery(monkeypatch) -> None:
    monkeypatch.setattr(worker, "clear_task_dispatch_guard", lambda *_: None)
    task = _processing_task("run-1", datetime.now(timezone.utc) - timedelta(hours=2))
    claimed = worker._claim_task_run(_FakeDb(task), str(task.id), "run-1")
    assert claimed.status == worker.TaskStatus.PROCESSING
    assert claimed.config["active_run_id"] == "run-1"


def test_writer_routes_to_deepseek_when_configured(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_generate_text(prompt, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        return "@最终书名\n测试"

    from app.services import deepseek

    monkeypatch.setattr(deepseek, "generate_text", fake_generate_text)
    monkeypatch.setattr(content_engine.settings, "WRITER_PROVIDER", "deepseek")
    monkeypatch.setattr(content_engine.settings, "DEEPSEEK_WRITER_MODEL", "deepseek-flash")
    monkeypatch.setattr(
        content_engine, "_openai_writer_call", lambda *a, **k: pytest.fail("OpenAI must not be called")
    )
    text = asyncio.run(content_engine._route_writer_call("写一本书"))
    assert text.startswith("@最终书名")
    assert calls[0]["model"] == "deepseek-flash"
    text2, usage = asyncio.run(content_engine._route_writer_call_with_usage("写一本书"))
    assert usage["provider"] == "deepseek" and text2 == text


def test_production_refuses_the_public_default_jwt_secret(monkeypatch) -> None:
    monkeypatch.setattr(security.settings, "APP_ENV", "production")
    monkeypatch.setattr(security.settings, "JWT_SECRET", security._DEFAULT_JWT_SECRET)
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        security.create_access_token("u1")
    monkeypatch.setattr(security.settings, "JWT_SECRET", "x" * 64)
    token = security.create_access_token("u1")
    assert security.decode_token(token)["sub"] == "u1"
