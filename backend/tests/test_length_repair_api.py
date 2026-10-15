import json

import pytest

from test_similarity_gate_api import fixture
from test_approved_length_repair import book
from app.models import TaskStatus
from app.services.length_repair_approval import prepare_candidate
from app.services.general_short_length import digest


def candidate(current):
    current.task.config = {"category": "general_short", "raw_text": "参考资料", "generation_budget_cny": 10,
                           "generation_hold": True}
    current.session.commit()
    (current.directory / "04_format.md").unlink(missing_ok=True)
    (current.directory / "01_writer.md").write_text(book([700] * 9))
    directory = current.directory / "approved_length_repair"
    directory.mkdir()
    (directory / "base.md").write_text(book([2550] * 7 + [3380, 1119]))
    return prepare_candidate(current.task, current.directory, "压" * 2400)


def test_review_hash_approval_and_resume_preserve_budget_and_dispatch_once(fixture):
    current = fixture
    record = candidate(current)
    info = current.client.get(f"/api/v1/tasks/{current.task.id}/length-repair")
    assert info.json()["status"] == "ready" and info.json()["result_sha256"] == record["result_sha256"]
    body = {"result_sha256": record["result_sha256"]}
    for _ in range(2):
        response = current.client.post(f"/api/v1/tasks/{current.task.id}/length-repair", json=body)
        assert response.status_code == 200
    assert current.task.config["approved_length_repair_sha256"] == record["result_sha256"]
    assert not current.task.config.get("generation_hold")
    assert current.task.status == TaskStatus.PENDING
    assert current.dispatched == [current.task.id]
    assert (current.directory / "generation_spend.json").read_text() == current.ledger
    approval = json.loads((current.directory / "approved_length_repair/approval.json").read_text())
    assert approval["human_approval"] == str(current.owner.id)


@pytest.mark.parametrize("mutation", ["result", "source", "chapter", "active", "unknown", "rejected"])
def test_invalid_or_stale_approval_never_resumes(fixture, mutation):
    current = fixture
    record = candidate(current)
    directory = current.directory / "approved_length_repair"
    if mutation == "result":
        (directory / "result.md").write_text("篡改")
    elif mutation == "source":
        current.task.config = {**current.task.config, "raw_text": "资料变化"}
    elif mutation == "chapter":
        (directory / "accepted_response.md").write_text("超" * 3015)
    else:
        key = {"active": "active_run_id", "unknown": "paid_call_delivery_uncertain", "rejected": "editor_v05_video_rejected"}[mutation]
        current.task.config = {**current.task.config, key: True}
    current.session.commit()
    response = current.client.post(f"/api/v1/tasks/{current.task.id}/length-repair", json={"result_sha256": record["result_sha256"]})
    assert response.status_code == 409
    assert not current.dispatched
    assert "approved_length_repair_sha256" not in current.task.config


def test_client_hash_is_not_trusted(fixture):
    current = fixture
    candidate(current)
    response = current.client.post(f"/api/v1/tasks/{current.task.id}/length-repair", json={"result_sha256": "f" * 64})
    assert response.status_code == 409 and not current.dispatched


def test_explicit_retry_releases_exhausted_pending_once_without_resetting_budget(fixture):
    current = fixture
    current.task.status = TaskStatus.PENDING
    current.task.config = {"category": "social_science", "generation_budget_cny": 10,
                           "auto_retry_exhausted_at": "2026-09-02T18:01:54+00:00", "auto_retry_attempts": 7}
    current.session.commit()
    for _ in range(2):
        response = current.client.post(f"/api/v1/tasks/{current.task.id}/retry")
        assert response.status_code == 200
    assert current.dispatched == [current.task.id]
    assert "auto_retry_exhausted_at" not in current.task.config
    assert current.task.config["generation_budget_cny"] == 10
    assert (current.directory / "generation_spend.json").read_text() == current.ledger


def test_failed_compression_is_not_a_ready_candidate(fixture):
    current = fixture
    current.task.config = {"category": "general_short", "raw_text": "资料"}
    (current.directory / "approved_length_repair").mkdir()
    response = current.client.get(f"/api/v1/tasks/{current.task.id}/length-repair")
    assert response.json()["status"] == "invalid"


def test_other_owner_cannot_read_or_approve(fixture):
    from uuid import uuid4
    current = fixture
    candidate(current)
    current.task.owner_id = uuid4()
    current.session.commit()
    assert current.client.get(f"/api/v1/tasks/{current.task.id}/length-repair").status_code == 404
    assert current.client.post(f"/api/v1/tasks/{current.task.id}/length-repair", json={"result_sha256": "a" * 64}).status_code == 404


@pytest.mark.parametrize("request_record", [{}, {"status": "unrecognized"}, [], {"status": "response_saved"}])
def test_unknown_or_unverifiable_paid_response_cannot_approve(fixture, request_record):
    current = fixture
    record = candidate(current)
    (current.directory / "approved_length_repair/extra_request.json").write_text(json.dumps(request_record))
    response = current.client.post(f"/api/v1/tasks/{current.task.id}/length-repair", json={"result_sha256": record["result_sha256"]})
    assert response.status_code == 409 and not current.dispatched


@pytest.mark.parametrize("stage", ["02_polish.md", "03_refine.md", "04_format.md", "editor_v05"])
def test_existing_downstream_paid_cache_cannot_bypass_new_approved_draft(fixture, stage):
    current = fixture
    record = candidate(current)
    (current.directory / stage).write_text("旧稿，必须原样保留")
    response = current.client.post(f"/api/v1/tasks/{current.task.id}/length-repair", json={"result_sha256": record["result_sha256"]})
    assert response.status_code == 409 and not current.dispatched
    assert (current.directory / stage).read_text() == "旧稿，必须原样保留"
