import json

import pytest

from app.services.ai_or_finalizer import FinalizerHold, digest
from app.services.editor_evidence import literal_quote, verified_payload


def test_asr_punctuation_anchors_to_exact_source_without_changing_numbers():
    source = "IMF提供了。400亿美元的救助。"
    assert literal_quote("IMF提供了400亿美元的救助", source) == "IMF提供了。400亿美元的救助"
    for invalid in ["IMF提供了40亿美元的救助", "IMF提供了4.00亿美元的救助", "IMF撤回了400亿美元的救助"]:
        with pytest.raises(FinalizerHold):
            literal_quote(invalid, source)


def test_omitted_words_are_not_fuzzy_approved():
    with pytest.raises(FinalizerHold):
        literal_quote("老虎基金他投入30亿美元", "老虎基金这。他投入30亿美元")


def test_scoped_quote_patch_preserves_raw_response_and_scoring(tmp_path):
    source = "老虎基金这。他投入30亿美元。"
    draft = "投入30亿美元。"
    payload = {"units": [{"source_quote": "老虎基金他投入30亿美元", "draft_quote": draft, "level": "完整"}],
               "data_points": [], "cases": []}
    response = tmp_path / "response.json"
    response.write_text(json.dumps(payload, ensure_ascii=False))
    before = response.read_bytes()
    patch = {"source_hash": digest(source), "draft_hash": digest(draft), "response_hash": digest(response.read_text()),
             "edits": [{"table": "units", "index": 0, "from": payload["units"][0]["source_quote"],
                        "to": source, "reason": "对照完整ASR核对漏掉的赘词"}]}
    (tmp_path / "audit_evidence_patch.json").write_text(json.dumps(patch, ensure_ascii=False))
    result, changes = verified_payload(source, draft, payload, tmp_path, response)
    assert result["units"][0]["source_quote"] == source
    assert result["units"][0]["level"] == "完整"
    assert changes and response.read_bytes() == before
    patch["edits"][0]["to"] = "投入40亿美元"
    (tmp_path / "audit_evidence_patch.json").write_text(json.dumps(patch))
    with pytest.raises(FinalizerHold):
        verified_payload(source, draft, payload, tmp_path, response)


def test_evidence_patch_cannot_change_different_response(tmp_path):
    path = tmp_path / "response.json"
    path.write_text('{}')
    (tmp_path / "audit_evidence_patch.json").write_text('{"source_hash": "wrong"}')
    with pytest.raises(FinalizerHold):
        verified_payload("原稿", "新稿", {"units": [], "cases": [], "data_points": []}, tmp_path, path)


def test_active_operator_hold_cannot_be_overwritten(monkeypatch):
    from app.tasks import tasks
    monkeypatch.setattr(tasks, "_read_task_run_state", lambda *args: ("processing", {"generation_hold": True}))
    task = type("Task", (), {"id": "test"})()
    with pytest.raises(tasks.StaleTaskRun):
        tasks._ensure_active_run(None, task, "original-run")
