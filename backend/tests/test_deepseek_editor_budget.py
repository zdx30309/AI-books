import asyncio
import json

import pytest

from app.core.config import settings
from app.services import content_engine, deepseek, gemini, polish_provider
from app.services.generation_spend import (
    GenerationBudgetExceeded,
    active,
    generation_budget,
    reserve,
    reserve_deepseek,
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
