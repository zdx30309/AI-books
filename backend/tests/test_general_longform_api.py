from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
import pytest

from app.api.routers import general_longform as router
from app.api.routers import tasks as tasks_router
from app.api.routers.auth import get_current_user
from app.db.session import get_db
from app.models import Chapter, Task, TaskStatus, User
from app.services import general_longform as engine
from app.services import general_longform_documents as documents
from app.services import general_longform_task as bridge
from app.services.general_longform_runtime import atomic_json
from general_longform_helpers import SOURCE, new_state, ready, run


@compiles(JSONB, "sqlite")
def compile_jsonb_for_tests(type_, compiler, **kwargs):
    return "JSON"


@pytest.fixture
def api_fixture(tmp_path, monkeypatch):
    database = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for model in (User, Task, Chapter): model.__table__.create(database)
    session = Session(database, expire_on_commit=False)
    owner = SimpleNamespace(id=uuid4())
    task = Task(id=uuid4(), owner_id=owner.id, source_url="manual://general_longform", status=TaskStatus.PAUSED,
                progress=0, current_step="等待策划", config={"category": "general_longform"})
    session.add(task); session.commit()
    monkeypatch.setattr(router, "work_dir", lambda task_id: tmp_path / str(task_id))
    monkeypatch.setattr(engine, "work_dir", lambda task_id: tmp_path / str(task_id))
    dispatched = []
    monkeypatch.setattr(tasks_router, "_dispatch_task", lambda value: dispatched.append(str(value.id)))
    monkeypatch.setattr(tasks_router, "_ensure_generation_capacity", lambda *args, **kwargs: None)
    app = FastAPI()
    app.include_router(router.router)
    app.include_router(tasks_router.router)
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: owner
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, session=session, owner=owner, task=task, app=app,
                              root=tmp_path, directory=tmp_path / str(task.id), dispatched=dispatched,
                              url=f"/api/v1/general-longform/{task.id}")
    session.close(); database.dispose()


def test_manifest_available_and_owner_isolation(api_fixture):
    f = api_fixture
    manifest = f.client.get("/api/v1/general-longform/prompt-manifest")
    assert manifest.status_code == 200 and manifest.json()["paragraph_count"] == 840
    assert manifest.json()["version"] == "2026-09-14-v02"
    legacy = f.client.get("/api/v1/general-longform/prompt-manifest", params={"version": "2026-09-14-v16"})
    assert legacy.status_code == 200 and legacy.json()["paragraph_count"] == 571
    assert f.client.get("/api/v1/general-longform/prompt-manifest", params={"version": "../../"}).status_code == 404
    assert f.client.get(f.url).status_code == 200
    f.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4())
    assert f.client.get(f.url).status_code == 404
    assert f.client.post(f.url + "/actions", json={"action": "plan"}).status_code == 404
    assert f.client.get(f.url + "/artifacts/missing").status_code == 404


def test_existing_config_policy_pins_state_but_new_config_defaults_v02(api_fixture):
    f = api_fixture
    normalized = tasks_router._normalize_task_config({"category": "general_longform", "general_longform_policy": "2026-09-14-v16"})
    assert normalized["general_longform_policy"] == "2026-09-14-v02"
    f.task.config = {"category": "general_longform", "general_longform_policy": "2026-09-14-v16"}
    f.session.commit()
    result = f.client.get(f.url).json()
    assert result["prompt_version"] == "2026-09-14-v16"
    assert result["prompt_source_sha256"] == router.get_manifest("2026-09-14-v16")["source_sha256"]
    f.task.config = {"category": "general_longform", "general_longform_policy": "missing"}
    f.session.commit()
    assert f.client.get(f.url).status_code == 409


def test_action_dispatch_is_serial_and_idempotent(api_fixture):
    f = api_fixture
    request_id = str(uuid4())
    response = f.client.post(f.url + "/actions", json={"action": "plan", "request_id": request_id})
    assert response.status_code == 200, response.text
    assert f.task.status == "pending" and len(f.dispatched) == 1
    assert f.client.post(f.url + "/actions", json={"action": "plan"}).status_code == 409
    duplicate = f.client.post(f.url + "/actions", json={"action": "plan", "request_id": request_id})
    assert duplicate.status_code == 200 and duplicate.json()["request_id"] == request_id
    assert len(f.dispatched) == 1


def test_outline_version_and_confirmation_guards(api_fixture):
    f = api_fixture
    assert f.client.post(f.url + "/actions", json={"action": "next_chapter"}).status_code == 409
    assert f.client.post(f.url + "/actions", json={"action": "confirm_outline", "outline_version": 99}).status_code == 409
    assert f.client.post(f.url + "/actions", json={"action": "revise_outline", "feedback": ""}).status_code == 400
    assert not f.dispatched


def test_pending_pause_and_resume_original_operation(api_fixture):
    f = api_fixture
    f.client.post(f.url + "/actions", json={"action": "plan"})
    old_request = f.task.config["general_longform_request"]["id"]
    assert f.client.post(f.url + "/pause").status_code == 200
    assert f.task.status == "paused"
    response = f.client.post(f.url + "/actions", json={"action": "resume"})
    assert response.status_code == 200 and response.json()["request_id"] == old_request


def test_resume_completed_request_cannot_start_next_chapter(api_fixture):
    f = api_fixture
    state = engine.initial_state(str(f.task.id))
    state["last_request"] = {"action": "plan", "id": str(uuid4())}
    state["completed_requests"] = [state["last_request"]["id"]]
    atomic_json(f.directory / "state.json", state)
    response = f.client.post(f.url + "/actions", json={"action": "resume"})
    assert response.status_code == 409 and not f.dispatched


def test_material_upload_limits_roles_dedupe_and_no_canon_writes(api_fixture):
    f = api_fixture
    data = documents.create_docx([("原始素材", "Normal")])
    response = f.client.post(f.url + "/materials", data={"role": "source"}, files={"file": ("材料.docx", data)})
    assert response.status_code == 200 and response.json()["status"] == "uploaded"
    second = f.client.post(f.url + "/materials", data={"role": "outline", "confirmed": "true"}, files={"file": ("材料.docx", data)})
    assert second.json()["status"] == "duplicate"
    state = engine.load_state(f.directory)
    assert len(state["materials"]) == 1 and state["materials"][0]["role"] == "outline"
    assert state["materials"][0]["confirmed"] and not state["accepted_state"]
    assert f.session.query(Chapter).count() == 0
    for name, content in [("bad.docx", b"not zip"), ("bad.exe", b"abc"), ("big.txt", b"a" * 10_000_001), ("long.txt", b"a" * 250001)]:
        assert f.client.post(f.url + "/materials", files={"file": (name, content)}).status_code == 400


def test_upload_blocked_during_active_operation(api_fixture):
    f = api_fixture
    f.task.status = "processing"; f.session.commit()
    assert f.client.post(f.url + "/materials", files={"file": ("new.txt", "素材".encode())}).status_code == 409


def test_chapter_and_artifact_download_verify_hash_and_traversal(api_fixture):
    f = api_fixture
    state = engine.initial_state(str(f.task.id))
    state["chapters"] = {"1": {"accepted": {"acceptedContent": "../outside.txt", "version": 1}}}
    state["artifacts"] = [{"id": "evil", "filename": "../../outside.docx", "sha256": "a" * 64}]
    atomic_json(f.directory / "state.json", state)
    assert f.client.get(f.url + "/chapters/1/content").status_code == 404
    assert f.client.get(f.url + "/artifacts/evil").status_code == 404
    path = f.directory / "chapter.txt"; path.write_text("正文", encoding="utf-8")
    state["chapters"]["1"]["accepted"].update(acceptedContent="chapter.txt", content_sha256="wrong")
    atomic_json(f.directory / "state.json", state)
    assert f.client.get(f.url + "/chapters/1/content").status_code == 409


def test_uncertain_paid_operation_blocks_new_actions_but_can_resume_held_pending(api_fixture):
    f = api_fixture
    request_id = str(uuid4())
    state = engine.initial_state(str(f.task.id))
    state["last_request"] = {"id": request_id, "action": "plan"}
    atomic_json(f.directory / "state.json", state)
    atomic_json(f.directory / "calls" / request_id / "research.json", {"status": "uncertain", "request_id": request_id, "action": "research"})
    assert f.client.post(f.url + "/actions", json={"action": "plan"}).status_code == 409
    f.task.status = "pending"; f.task.config = {**f.task.config, "generation_hold": True}; f.session.commit()
    response = f.client.post(f.url + "/actions", json={"action": "resume", "retry_uncertain": True})
    assert response.status_code == 200 and response.json()["request_id"] == request_id
    assert "generation_hold" not in f.task.config


@pytest.mark.parametrize("response_id,response_status", [(None, None), ("existing-response", "incomplete"), ("existing-response", "failed")])
def test_uncertain_resume_without_explicit_retry_does_not_enqueue(api_fixture, response_id, response_status):
    f = api_fixture
    request_id = str(uuid4())
    state = engine.initial_state(str(f.task.id))
    state["last_request"] = {"id": request_id, "action": "plan"}
    atomic_json(f.directory / "state.json", state)
    atomic_json(f.directory / "calls" / request_id / "research.json", {"status": "uncertain", "request_id": request_id, "action": "research", "response_id": response_id, "response_status": response_status})
    f.task.status = "pending"; f.task.config = {**f.task.config, "generation_hold": True}; f.session.commit()
    config_before = deepcopy(f.task.config)
    response = f.client.post(f.url + "/actions", json={"action": "resume"})
    assert response.status_code == 409 and not f.dispatched
    assert f.task.config == config_before


def test_incomplete_outline_uses_local_revision_not_new_plan(api_fixture):
    f = api_fixture
    state = engine.initial_state(str(f.task.id))
    state["outline_candidate"] = {"chapters": []}
    atomic_json(f.directory / "state.json", state)
    response = f.client.post(f.url + "/actions", json={"action": "plan"})
    assert response.status_code == 409 and not f.dispatched


def test_generic_mutation_and_regeneration_cannot_bypass_general_longform_gate(api_fixture):
    f = api_fixture
    for data in ({"title": "new"}, {"category": "social_science"}):
        assert f.client.patch(f"/api/v1/tasks/{f.task.id}/metadata", json=data).status_code == 409
    assert f.client.patch(f"/api/v1/tasks/{f.task.id}/metadata", json={"bookshelf": "整理书架"}).status_code == 200
    chapter = Chapter(task_id=f.task.id, chapter_num=1, title="原标题", content="验收正文", word_count=4)
    f.session.add(chapter); f.session.commit()
    response = f.client.put(f"/api/v1/tasks/{f.task.id}/chapters/1", json={"content": "未验收修改"})
    assert response.status_code == 409 and chapter.content == "验收正文"
    from fastapi import HTTPException
    with pytest.raises(HTTPException): tasks_router._prepare_task_for_regeneration(f.session, f.task)
    assert f.session.query(Chapter).count() == 1
    unchanged = "##第1章 标题\n#小节\n正文"
    assert tasks_router._normalize_download_markdown(unchanged, category="general_longform") == unchanged


def test_task_bridge_import_first_has_no_paid_work(api_fixture, monkeypatch):
    f = api_fixture
    f.task.status = "processing"
    f.task.config = {"category": "general_longform", "general_longform_import_first": True, "active_run_id": "run-1"}
    f.session.commit()
    def unexpected(*args, **kwargs): raise AssertionError("no paid call when waiting for uploads")
    monkeypatch.setattr(engine.ModelGateway, "call", unexpected)
    result = bridge.process_claimed_task(f.session, f.task, "run-1", f.root / "output")
    assert result["phase"] == "awaiting_materials" and result["status"] == "paused"
    assert f.session.query(Chapter).count() == 0


def test_task_bridge_persists_only_accepted_rows(api_fixture, monkeypatch):
    f = api_fixture
    output = f.root / "output"
    state, gateway = ready(output / "general_longform", monkeypatch)
    state["project_id"] = str(f.task.id)
    run(output / "general_longform", state, gateway, "next_chapter")
    state["chapters"]["2"] = {"candidate": {"number": 2, "draftContent": "not-accepted.txt", "status": "needs_review"}}
    atomic_json(output / "general_longform/state.json", state)
    f.task.status = "processing"
    f.task.config = {"category": "general_longform", "general_longform_request": state["last_request"], "active_run_id": "run-2"}
    f.session.commit()
    result = bridge.process_claimed_task(f.session, f.task, "run-2", output)
    rows = f.session.query(Chapter).all()
    assert result["chapters"] == 1 and len(rows) == 1
    assert rows[0].chapter_num == 1 and rows[0].word_count == 8750
    assert result["status"] == "paused"
