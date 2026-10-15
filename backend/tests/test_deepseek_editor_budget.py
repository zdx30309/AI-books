import asyncio
import json
import time

import pytest

from app.core.config import settings
from app.services import content_engine, deepseek, gemini, polish_provider
from app.services.generation_spend import (
    GenerationBudgetExceeded,
    active,
    generation_budget,
    reserve,
    reserve_deepseek,
    settle_rewrite,
)


def test_gemini_redirect_and_polish_preserve_prompts(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_TEXT_PROVIDER", "deepseek")
    monkeypatch.setattr(settings, "BACKEND_POLISH_PROVIDER", "deepseek")
    monkeypatch.setattr(settings, "BACKEND_REFINE_PROVIDER", "deepseek")
    monkeypatch.setattr(settings, "DEEPSEEK_WRITER_MODEL", "deepseek-flash")
    calls = []
    monkeypatch.setattr(deepseek, "generate_text", lambda prompt, **kwargs: calls.append((prompt, kwargs)) or "完成")
    monkeypatch.setattr(gemini, "get_gemini_client", lambda: pytest.fail("Gemini must not be called"))
    for call in (gemini.generate_text, polish_provider.polish_book_text, polish_provider.refine_book_text):
        assert call("正文", system_instruction="外置要求") == "完成"
    assert len(calls) == 3
    assert all(kwargs["model"] == "deepseek-flash" and kwargs["system_instruction"] == "外置要求"
               and kwargs["thinking"] is False for _, kwargs in calls)
    assert content_engine._refine_stage_identity("social_science_core_conflict")["provider"] == "deepseek"


def test_delivery_uncertainty_is_blocking_without_fallback(monkeypatch):
    monkeypatch.setattr(settings, "BACKEND_POLISH_PROVIDER", "deepseek")
    error = deepseek.DeepSeekDeliveryUncertainError("timeout")
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(deepseek, "generate_text", fail)
    with pytest.raises(deepseek.DeepSeekDeliveryUncertainError):
        polish_provider.polish_book_text("正文")
    assert content_engine._is_paid_polish_blocking_failure(error)


@pytest.mark.parametrize("stage", ["polish_chunk", "refine_chunk"])
@pytest.mark.parametrize("fallback", [False, True])
def test_outer_deepseek_chunk_timeout_preserves_budget_and_blocks_replacement(tmp_path, monkeypatch, stage, fallback):
    monkeypatch.setattr(settings, "BACKEND_POLISH_PROVIDER", "deepseek")
    monkeypatch.setattr(settings, "BACKEND_REFINE_PROVIDER", "deepseek")
    monkeypatch.setattr(settings, "GEMINI_POLISH_CONCURRENCY", 1)
    monkeypatch.setattr(content_engine, "_effective_gemini_chunk_timeout", lambda *args: 0.001)
    calls = []
    def slow_call(text, **kwargs):
        calls.append(text)
        reserve("test", 1, "test")
        time.sleep(0.02)
        return text
    with generation_budget({"generation_budget_cny": 10}, tmp_path):
        with pytest.raises(deepseek.DeepSeekDeliveryUncertainError, match="outer timeout"):
            asyncio.run(content_engine._polish_by_paragraph_chunks_strict(
                "第一块\n\n第二块", output_dir=tmp_path, chunk_func=slow_call,
                splitter=lambda *args, **kwargs: ["第一块", "第二块"],
                progress_stage=stage, allow_chunk_fallback=fallback))
    assert calls == ["第一块"]
    ledger = json.loads((tmp_path / "generation_spend.json").read_text())
    assert ledger["reserved_cny"] == 1 and len(ledger["calls"]) == 1


def test_budget_persists_and_propagates_to_async_thread(tmp_path):
    async def worker():
        await asyncio.to_thread(reserve, "test", 3, "test")
    with generation_budget({"generation_budget_cny": 10}, tmp_path):
        asyncio.run(worker())
        reserve("test", 6, "test")
    assert not active()
    with generation_budget({"generation_budget_cny": 10}, tmp_path):
        with pytest.raises(GenerationBudgetExceeded):
            reserve("test", 2, "test")
    state = json.loads((tmp_path / "generation_spend.json").read_text())
    assert state["reserved_cny"] == 9
    assert len(state["calls"]) == 2


def test_budget_rejects_unpriced_model_before_client_request(tmp_path, monkeypatch):
    monkeypatch.setattr(deepseek, "get_deepseek_client", lambda: pytest.fail("must reject before client creation"))
    with generation_budget({"generation_budget_cny": 10}, tmp_path):
        with pytest.raises(GenerationBudgetExceeded):
            deepseek.generate_text("正文", model="unknown")
        assert reserve_deepseek("正文", None, "deepseek-flash", None) == 16384


def test_budget_error_blocks_quality_fallback(tmp_path):
    with generation_budget({"generation_budget_cny": 1}, tmp_path):
        with pytest.raises(GenerationBudgetExceeded) as caught:
            reserve("test", 2, "test")
    assert content_engine._is_paid_polish_blocking_failure(caught.value)


def test_confirmed_rewrite_invoice_settles_only_its_reservation(tmp_path):
    receipt = tmp_path / "rewrite.json"
    receipt.write_text(json.dumps({"status": 1, "taskId": "provider-task", "consumeAmount": 1}))
    with generation_budget({"generation_budget_cny": 10}, tmp_path):
        index = reserve("ai-or-v1-enhanced", 3, "zhuque-v1")
        reserve("deepseek-text", 1, "deepseek-flash")
        settle_rewrite(index, receipt)
        settle_rewrite(index, receipt)
        reserve("deepseek-text", 8, "deepseek-flash")
        with pytest.raises(GenerationBudgetExceeded):
            reserve("deepseek-text", .01, "deepseek-flash")
    state = json.loads((tmp_path / "generation_spend.json").read_text())
    assert state["reserved_cny"] == 10
    assert state["calls"][0]["actual_cny"] == 1
    assert state["calls"][0]["upper_bound_cny"] == 3
    assert state["calls"][1]["status"] == "reserved_not_actual_invoice"


def test_same_invoice_cannot_release_two_reservations(tmp_path):
    receipt = tmp_path / "rewrite.json"
    receipt.write_text(json.dumps({"status": 1, "taskId": "provider-task", "consumeAmount": 1}))
    with generation_budget({"generation_budget_cny": 10}, tmp_path):
        first = reserve("ai-or-v1-enhanced", 3, "zhuque-v1")
        second = reserve("ai-or-v1-enhanced", 3, "zhuque-v1")
        settle_rewrite(first, receipt)
        with pytest.raises(GenerationBudgetExceeded):
            settle_rewrite(second, receipt)
    assert json.loads((tmp_path / "generation_spend.json").read_text())["reserved_cny"] == 4


def test_invoice_cannot_settle_deepseek_or_exceed_reserved_amount(tmp_path):
    receipt = tmp_path / "rewrite.json"
    receipt.write_text(json.dumps({"status": 1, "taskId": "provider-task", "consumeAmount": 3}))
    with generation_budget({"generation_budget_cny": 10}, tmp_path):
        rewrite = reserve("ai-or-v1-enhanced", 2, "zhuque-v1")
        deepseek_index = reserve("deepseek-text", 4, "deepseek-flash")
        for index in (rewrite, deepseek_index):
            with pytest.raises(GenerationBudgetExceeded):
                settle_rewrite(index, receipt)
    assert json.loads((tmp_path / "generation_spend.json").read_text())["reserved_cny"] == 6
