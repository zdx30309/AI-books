import json
from types import SimpleNamespace

import pytest

from app.models import TaskStatus
from app.services import ai_or_finalizer as finalizer, human_delivery_acceptance as acceptance


def create_review(tmp_path, monkeypatch):
    monkeypatch.setattr(finalizer.settings, "AI_OR_SEVERE_BLOCKS_DELIVERY", True)
    source = "原稿内容已明确收紧。\n"
    monkeypatch.setattr(finalizer, "call_rewrite", lambda text: {"outputText": text.replace("收紧", "放宽")})
    monkeypatch.setattr(finalizer, "call_correction", lambda *args: {"text": json.dumps({"issues": [{
        "original": "放宽", "replacement": "收紧", "remark": "对照原稿修复严重反义错误", "severe": True,
    }]}, ensure_ascii=False)})
    with pytest.raises(finalizer.FinalizerHold):
        finalizer.finalize_manuscript(source, tmp_path)
    return tmp_path / "ai_or_finalizer", source


def test_saved_corrections_can_be_verified_without_paid_calls(tmp_path, monkeypatch):
    directory, source = create_review(tmp_path, monkeypatch)
    monkeypatch.setattr(finalizer, "call_rewrite", lambda *args: pytest.fail("no paid request allowed"))
    state, final, issues = acceptance.verify_saved_delivery(directory, finalizer.digest(source), "social_science_core_conflict")
    assert state["status"] == "manual_review"
    assert final == source
    assert len(issues) == 1 and issues[0]["severe"]


def test_changed_review_cannot_use_previous_approval(tmp_path, monkeypatch):
    directory, source = create_review(tmp_path, monkeypatch)
    (directory / "final.md").write_text(source + "篡改")
    with pytest.raises(finalizer.FinalizerHold):
        acceptance.verify_saved_delivery(directory, finalizer.digest(source), "social_science_core_conflict")


def test_human_acceptance_records_approval_preserves_red_flags_and_commits_once(tmp_path, monkeypatch):
    from app.tasks import tasks
    output = tmp_path / "backend/output/task_example"
    output.mkdir(parents=True)
    directory, source = create_review(output, monkeypatch)
    monkeypatch.setattr(acceptance, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(tasks, "_persist_chapters_from_markdown", lambda *args, **kwargs: 1 if kwargs["commit"] is False else pytest.fail("must not commit early"))
    commits = []
    task = SimpleNamespace(id="example", status=TaskStatus.MANUAL_REVIEW, config={"generation_hold": True}, error_message="hold")
    db = SimpleNamespace(commit=lambda: commits.append(1))
    assert acceptance.accept_saved_delivery(db, task, finalizer.digest(source), "explicit-human-confirmation") == 1
    state = json.loads((directory / "manifest.json").read_text())
    assert state["status"] == "completed" and "error" not in state
    assert state["manual_approval"]["severe_count"] == 1
    assert task.status == TaskStatus.COMPLETED and task.progress == 100
    assert not task.config.get("generation_hold")
    assert commits == [1]
    assert (directory / "正式书稿.docx").exists()
    assert json.loads((directory / "issues.json").read_text())[0]["severe"] is True


def test_approval_rejects_running_task_before_file_access():
    task = SimpleNamespace(status=TaskStatus.PROCESSING, config={})
    with pytest.raises(finalizer.FinalizerHold):
        acceptance.accept_saved_delivery(None, task, "hash", "human")
