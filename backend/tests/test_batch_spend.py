from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
import json
import sys
from types import SimpleNamespace
import uuid

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services import batch_spend, content_engine, deepseek, gemini


@pytest.fixture
def batch(tmp_path, monkeypatch):
    monkeypatch.setattr(batch_spend.settings, "WRITER_PROVIDER", "openai")
    monkeypatch.setattr(batch_spend.settings, "OPENAI_WRITER_STREAM", False)
    monkeypatch.setattr(batch_spend.settings, "OPENAI_WRITER_MODEL", "gpt-5.6-sol")
    config = {"category": "popular_science", "generation_batch_id": str(uuid.uuid4()), "generation_batch_budget_cny": 100}
    owner = uuid.uuid4()
    output = tmp_path / ("task_" + str(uuid.uuid4()))
    ledger = tmp_path / "generation_batches" / str(owner) / config["generation_batch_id"] / "spend.json"
    return config, output, owner, ledger


def test_shared_budget_survives_scope_reentry_and_cannot_reset(batch):
    config, output, owner, ledger = batch
    with batch_spend.scope(config, output, owner):
        batch_spend.reserve("gpt-5.6-sol", "来源", None, 1000)
    previous = json.loads(ledger.read_text())["reserved_cny"]
    with batch_spend.scope(config, output, owner):
        batch_spend.reserve("gpt-5.6-sol", "来源", None, 1000)
    assert json.loads(ledger.read_text())["reserved_cny"] == round(previous * 2, 6)
    with pytest.raises(batch_spend.BatchBudgetStop, match="禁止重置"):
        with batch_spend.scope({**config, "generation_batch_budget_cny": 200}, output, owner):
            pass


def test_budget_blocks_before_paid_request(batch):
    config, output, owner, ledger = batch
    with batch_spend.scope({**config, "generation_batch_budget_cny": 0.01}, output, owner):
        with pytest.raises(batch_spend.BatchBudgetStop, match="超过批次"):
            batch_spend.reserve("gpt-5.6-sol", "来源", None, 65536)
    assert not json.loads(ledger.read_text())["calls"]


def test_usage_refunds_only_unused_upper_bound_and_rejects_double_settlement(batch):
    config, output, owner, ledger = batch
    with batch_spend.scope(config, output, owner):
        index = batch_spend.reserve("gpt-5.6-sol", "来源", None, 65536)
        batch_spend.settle(index, 100, 200, "response-1")
        with pytest.raises(batch_spend.BatchBudgetStop, match="禁止重复"):
            batch_spend.settle(index, 100, 200, "response-1")
    assert json.loads(ledger.read_text())["reserved_cny"] == 0.045


def test_uncertain_request_holds_entire_batch_without_refunding(batch):
    config, output, owner, ledger = batch
    with batch_spend.scope(config, output, owner):
        index = batch_spend.reserve("gpt-5.6-sol", "来源", None, 1000)
        reserved = json.loads(ledger.read_text())["reserved_cny"]
        batch_spend.failed(index, TimeoutError("unknown result"))
        with pytest.raises(batch_spend.BatchBudgetStop, match="避免重复扣费"):
            batch_spend.reserve("gpt-5.6-sol", "来源", None, 1000)
    assert json.loads(ledger.read_text())["reserved_cny"] == reserved


def test_concurrent_books_cannot_overdraw_shared_limit(batch):
    config, output, owner, ledger = batch
    config["generation_batch_budget_cny"] = 1

    def reserve_one(number):
        with batch_spend.scope(config, output.with_name("task_" + str(number)), owner):
            try:
                return batch_spend.reserve("gpt-5.6-sol", "来源", None, 1000)
            except batch_spend.BatchBudgetStop:
                return None

    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(reserve_one, range(20)))
    state = json.loads(ledger.read_text())
    assert state["reserved_cny"] <= 1
    assert len(state["calls"]) == sum(result is not None for result in results)


def test_openai_writer_uses_requested_model_with_no_hidden_sdk_retries(batch, monkeypatch):
    config, output, owner, ledger = batch
    calls = []
    response = SimpleNamespace(id="response-1", model="gpt-5.6-sol", status="completed", output_text="完整新稿", usage=SimpleNamespace(input_tokens=100, output_tokens=200))

    class Client:
        def with_options(self, **kwargs):
            assert kwargs == {"max_retries": 0}
            return self

        @property
        def responses(self):
            return self

        def create(self, **kwargs):
            calls.append(kwargs)
            return response

    monkeypatch.setattr(content_engine, "_get_openai_writer_client", lambda: Client())
    with batch_spend.scope(config, output, owner):
        result = content_engine._create_openai_writer_response("来源", effort="xhigh", verb="medium", max_tok=None)
    assert result is response
    assert calls[0]["model"] == "gpt-5.6-sol"
    assert calls[0]["service_tier"] == "default"
    assert calls[0]["max_output_tokens"] == 65536
    assert json.loads(ledger.read_text())["calls"][0]["model"] == "gpt-5.6-sol"


def test_missing_usage_keeps_full_reservation(batch):
    config, output, owner, ledger = batch
    with batch_spend.scope(config, output, owner):
        index = batch_spend.reserve("gpt-5.6-sol", "来源", None, 1000)
        reserved = json.loads(ledger.read_text())["reserved_cny"]
        batch_spend.settle(index, None, None)
    assert json.loads(ledger.read_text())["reserved_cny"] == reserved


def test_gemini_disables_sdk_retry_and_records_thought_tokens(batch, monkeypatch):
    config, output, owner, ledger = batch
    calls = []

    def generate(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text="润色结果", response_id="gemini-1", candidates=[],
                               usage_metadata=SimpleNamespace(prompt_token_count=100, candidates_token_count=200, thoughts_token_count=50))

    monkeypatch.setattr(gemini.settings, "GOOGLE_GENAI_USE_VERTEXAI", True)
    monkeypatch.setattr(gemini.settings, "GEMINI_MODEL", "gemini-3.1-flash-lite")
    monkeypatch.setattr(gemini, "_get_vertex_client", lambda *args: SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    monkeypatch.setattr(gemini, "_gemini_call_gate", nullcontext)
    monkeypatch.setattr(gemini, "_google_proxy_env", nullcontext)
    with batch_spend.scope(config, output, owner):
        assert gemini.generate_text("来源") == "润色结果"
    assert calls[0]["config"].http_options.retry_options.attempts == 1
    assert json.loads(ledger.read_text())["calls"][0]["output_tokens"] == 250


@pytest.mark.parametrize("text", ("格式化结果", ""))
def test_deepseek_stream_records_usage_without_hidden_retry(batch, monkeypatch, text):
    config, output, owner, ledger = batch
    calls = []

    class Client:
        def with_options(self, **kwargs):
            assert kwargs == {"max_retries": 0}
            return self

        @property
        def chat(self):
            return self

        @property
        def completions(self):
            return self

        def create(self, **kwargs):
            calls.append(kwargs)
            return iter([
                SimpleNamespace(id="deepseek-1", usage=None, choices=[SimpleNamespace(delta=SimpleNamespace(content=text), finish_reason="stop")]),
                SimpleNamespace(id="deepseek-1", usage=SimpleNamespace(prompt_tokens=100, completion_tokens=200), choices=[]),
            ])

    monkeypatch.setattr(deepseek, "get_deepseek_client", lambda: Client())
    monkeypatch.setattr(deepseek, "distributed_semaphore", lambda *args, **kwargs: nullcontext())
    with batch_spend.scope(config, output, owner):
        if text:
            assert deepseek.generate_text("来源", model="deepseek-flash") == text
        else:
            with pytest.raises(batch_spend.BatchBudgetStop, match="空响应"):
                deepseek.generate_text("来源", model="deepseek-flash")
    assert len(calls) == 1
    assert calls[0]["stream_options"] == {"include_usage": True}
    assert json.loads(ledger.read_text())["calls"][0]["output_tokens"] == 200


@pytest.mark.parametrize("field,value", (("generation_batch_budget_cny", float("nan")), ("category", "healing_comic")))
def test_unpriced_batch_parameters_fail_closed(batch, field, value):
    config, output, owner, _ledger = batch
    with pytest.raises(batch_spend.BatchBudgetStop):
        with batch_spend.scope({**config, field: value}, output, owner):
            raise AssertionError("must not reach paid work")
