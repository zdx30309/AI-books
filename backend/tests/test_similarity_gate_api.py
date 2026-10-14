import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routers import tasks as router
from app.api.routers.auth import get_current_user
from app.db.session import get_db
from app.models import Chapter, Task, TaskStatus, User
from app.services.editor_v05 import SimilarityGateHold, similarity_gate
from app.services.generation_spend import GenerationBudgetExceeded, generation_budget, reserve


@compiles(JSONB, "sqlite")
def compile_jsonb_for_sqlite(type_, compiler, **kwargs):
    return "JSON"


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    database = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for model in (User, Task, Chapter):
        model.__table__.create(database)
    session = Session(database, expire_on_commit=False)
    owner = SimpleNamespace(id=uuid4())
    try:
        similarity_gate({}, "第一稿", {"max_composite": 78.7})
    except SimilarityGateHold as exc:
        config = {**exc.config_patch, "category": "social_science", "raw_text": "原始素材",
                  "generation_budget_cny": 20, "generation_hold": True,
                  "generation_hold_reason": str(exc), "generation_hold_step": "人工审核"}
    current = Task(id=uuid4(), owner_id=owner.id, source_url="https://example.org/video",
                   status=TaskStatus.MANUAL_REVIEW, progress=94, config=config)
    session.add(current)
    session.commit()
    directory = tmp_path / f"task_{current.id}"
    directory.mkdir()
    (directory / "04_format.md").write_text("第一稿")
    (directory / "report.docx").write_bytes(b"saved report")
    ledger = json.dumps({"reserved_cny": 19, "calls": [{"upper_bound_cny": 19}]})
    (directory / "generation_spend.json").write_text(ledger)
    dispatched = []
    monkeypatch.setattr(router, "_task_output_dir", lambda task_id: directory)
    monkeypatch.setattr(router, "_dispatch_task", lambda value: dispatched.append(value.id))
    monkeypatch.setattr(router, "_ensure_generation_capacity", lambda *args, **kwargs: None)
    monkeypatch.setattr(router, "_stamp_aigc_policy", lambda cfg: cfg)
    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: owner
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, session=session, task=current, owner=owner,
                              directory=directory, dispatched=dispatched, ledger=ledger)
    session.close()
    database.dispose()


def test_human_regeneration_releases_only_similarity_hold_and_keeps_budget(fixture):
    current = fixture
    current.task.config = {**current.task.config, "approved_length_repair_sha256": "old-generation-hash"}
    current.session.commit()
    result = current.client.post("/api/v1/tasks/regenerate", json={"task_ids": [str(current.task.id)]})
    assert result.status_code == 200
    assert result.json()["total_regenerated"] == 1
    assert current.dispatched == [current.task.id]
    assert current.task.status == TaskStatus.PENDING
    assert not current.task.config.get("generation_hold")
    assert current.task.config["editor_v05_generation_attempt"] == 2
    assert "approved_length_repair_sha256" not in current.task.config
    assert current.task.config["raw_text"] == "原始素材"
    assert len(current.task.config["editor_v05_similarity_history"]) == 1
    assert (current.directory / "generation_spend.json").read_text() == current.ledger
    assert not (current.directory / "04_format.md").exists()
    from pathlib import Path

    archive = Path(current.task.config["editor_v05_previous_generation_path"])
    assert (archive / "04_format.md").read_text() == "第一稿"
    assert (archive / "report.docx").read_bytes() == b"saved report"
    with generation_budget(current.task.config, current.directory):
        with pytest.raises(GenerationBudgetExceeded):
            reserve("test", 2, "deepseek-flash")
    with pytest.raises(SimilarityGateHold) as caught:
        similarity_gate(current.task.config, "第一稿", {"max_composite": 78.7})
    assert caught.value.rejected


@pytest.mark.parametrize("suffix", ["retry", "retry-aigc"])
def test_ordinary_retry_cannot_bypass_audit(fixture, suffix):
    current = fixture
    result = current.client.post(f"/api/v1/tasks/{current.task.id}/{suffix}")
    assert result.status_code == 409
    assert "相似度" in result.json()["detail"]
    assert not current.dispatched


def test_batch_retry_skips_similarity_hold(fixture):
    current = fixture
    result = current.client.post("/api/v1/tasks/retry-failed", json={"task_ids": [str(current.task.id)]})
    assert result.status_code == 200
    assert result.json()["total_retried"] == 0
    assert result.json()["total_skipped"] == 1
    assert not current.dispatched


def test_rejected_video_cannot_regenerate_or_retry(fixture):
    current = fixture
    current.task.config = {**current.task.config, "editor_v05_video_rejected": True,
                           "editor_v05_status": "video_rejected"}
    current.session.commit()
    result = current.client.post("/api/v1/tasks/regenerate", json={"task_ids": [str(current.task.id)]})
    assert result.json()["total_regenerated"] == 0
    assert "此视频不可用" in result.json()["skipped"][0]["reason"]
    assert current.client.post(f"/api/v1/tasks/{current.task.id}/retry").status_code == 409
    assert (current.directory / "04_format.md").read_text() == "第一稿"
    assert not current.dispatched


def test_unknown_paid_result_is_not_released_by_regeneration(fixture):
    current = fixture
    current.task.config = {**current.task.config, "paid_call_delivery_uncertain": True}
    current.session.commit()
    result = current.client.post("/api/v1/tasks/regenerate", json={"task_ids": [str(current.task.id)]})
    assert result.json()["total_regenerated"] == 0
    assert current.task.config["generation_hold"]
    assert (current.directory / "04_format.md").exists()
    assert not current.dispatched


def test_gate_fields_cannot_be_forged_by_client():
    cfg = router._normalize_task_config({"category": "general_short", "editor_v05_generation_attempt": 99,
                                         "editor_v05_similarity_history": [], "editor_v05_video_rejected": False})
    assert "editor_v05_generation_attempt" not in cfg
    assert "editor_v05_similarity_history" not in cfg
    assert "editor_v05_video_rejected" not in cfg


def test_task_status_exposes_gate_to_frontend(fixture):
    from app.schemas.task import TaskStatusResponse

    result = TaskStatusResponse.model_validate(fixture.task)
    assert result.editor_v05_status == "similarity_blocked"
    assert result.editor_v05_video_rejected is False


def test_worker_persists_gate_after_database_rollback(fixture, monkeypatch):
    from unittest.mock import MagicMock
    from app.tasks import tasks as worker

    current = fixture
    current.task.config = {"category": "social_science", "raw_text": "原始素材"}
    current.session.commit()
    session = MagicMock(wraps=current.session)
    session.close = lambda: None
    monkeypatch.setattr(worker, "SessionLocal", lambda: session)
    monkeypatch.setattr(worker, "editor_v05_applies", lambda task: True)

    def blocked(*args):
        similarity_gate(current.task.config, "第一稿", {"max_composite": 78.7})

    monkeypatch.setattr(worker, "_claim_task_run", blocked)
    result = worker.process_video.run(str(current.task.id))
    current.session.refresh(current.task)
    assert result["status"] == TaskStatus.MANUAL_REVIEW
    assert current.task.config["editor_v05_status"] == "similarity_blocked"
    assert current.task.config["generation_hold"]
    assert current.task.config["editor_v05_similarity"] == 78.7
    assert "人工确认" in current.task.current_step
