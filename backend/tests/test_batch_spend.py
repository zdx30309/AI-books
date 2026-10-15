from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import sys
from types import SimpleNamespace
import uuid

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services import batch_spend, content_engine, gemini


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


@pytest.mark.parametrize("field,value", (("generation_batch_budget_cny", float("nan")), ("category", "healing_comic")))
def test_unpriced_batch_parameters_fail_closed(batch, field, value):
    config, output, owner, _ledger = batch
    with pytest.raises(batch_spend.BatchBudgetStop):
        with batch_spend.scope({**config, field: value}, output, owner):
            raise AssertionError("must not reach paid work")
