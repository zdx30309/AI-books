import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app.services import editor_v05 as service
from app.services import ai_or_finalizer
from app.services.generation_spend import generation_budget


@pytest.mark.parametrize("size,expected", [(5000, 16384), (12000, 16384), (18000, 24576), (25000, 32768), (200000, 32768)])
def test_audit_cap_scales_with_full_manuscript_not_by_truncating_it(monkeypatch, size, expected):
    monkeypatch.setattr(service.settings, "EDITOR_V05_AUDIT_MAX_TOKENS", 16384)
    monkeypatch.setattr(service.settings, "EDITOR_V05_AUDIT_TOKEN_CEILING", 32768)
    assert service.audit_token_limit("原" * 5000, "新" * size) == expected


def test_25k_request_preserves_both_complete_texts_and_reserves_increased_budget(tmp_path, monkeypatch):
    from app.utils import distributed_limiter, paid_api_guard

    source = "来源文案。" * 2400
    draft = "正文内容。" * 5000
    captured = []

    def create(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"units": []}'), finish_reason="stop")],
                               usage=None, id="offline-25k", model="deepseek-flash")

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    client.with_options = lambda **kwargs: client
    monkeypatch.setattr(ai_or_finalizer, "get_deepseek_client", lambda: client)
    monkeypatch.setattr(distributed_limiter, "distributed_semaphore", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(paid_api_guard, "guard", lambda *args: None)
    monkeypatch.setattr(service.settings, "AI_OR_CORRECTION_MODEL", "deepseek-flash")
    with generation_budget({"generation_budget_cny": 10}, tmp_path):
        response = service.call_audit(source, draft)
    request = captured[0]
    assert request["max_tokens"] == response["max_output_tokens"] == 32768
    assert source in request["messages"][0]["content"] and draft in request["messages"][0]["content"]
    ledger = json.loads((tmp_path / "generation_spend.json").read_text())
    assert len(ledger["calls"]) == 1 and ledger["reserved_cny"] >= 32768 * 1.2 / 100000


def test_existing_saved_audit_is_not_replayed_when_cap_changes(tmp_path, monkeypatch):
    from test_editor_v05 import payload, SOURCE, DRAFT

    response = {"text": json.dumps(payload()), "finish_reason": "stop", "model": "deepseek-flash"}
    monkeypatch.setattr(service, "call_audit", lambda *args: response)
    first = service.audit_manuscript(SOURCE, DRAFT, tmp_path / "editor_v05", "测试")
    monkeypatch.setattr(service.settings, "EDITOR_V05_AUDIT_MAX_TOKENS", 32768)
    monkeypatch.setattr(service, "call_audit", lambda *args: pytest.fail("saved paid response must not be replayed"))
    second = service.audit_manuscript(SOURCE, DRAFT, tmp_path / "editor_v05", "测试")
    assert first["data"] == second["data"]
