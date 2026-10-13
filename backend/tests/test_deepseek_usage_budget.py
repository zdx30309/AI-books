import json

import pytest

from app.services.generation_spend import GenerationBudgetExceeded, generation_budget, reserve_deepseek, settle_deepseek_usage


def receipt(path, response_id="response-1", model="deepseek-flash", output=1000):
    path.write_text(json.dumps({"response_id": response_id, "model": model, "response_model": "deepseek-v4.1-flash",
                                "usage": {"prompt_tokens": 10000, "completion_tokens": output}}))


def test_verified_tokens_reduce_upper_bound_not_actual_invoice(tmp_path):
    path = tmp_path / "response.json"
    receipt(path)
    with generation_budget({"generation_budget_cny": 20}, tmp_path):
        cap, index = reserve_deepseek("中" * 20000, None, "deepseek-flash", 8192, with_index=True)
        assert cap == 8192
        settle_deepseek_usage(index, path)
        settle_deepseek_usage(index, path)
    state = json.loads((tmp_path / "generation_spend.json").read_text())
    assert state["reserved_cny"] == 0.042
    assert state["calls"][0]["status"] == "usage_verified_upper_bound_not_actual_invoice"
    assert "actual_cny" not in state["calls"][0]
    assert state["calls"][0]["upper_bound_cny"] > 0.042


def test_unknown_usage_keeps_original_bound(tmp_path):
    path = tmp_path / "response.json"
    path.write_text('{}')
    with generation_budget({"generation_budget_cny": 20}, tmp_path):
        cap, index = reserve_deepseek("中" * 20000, None, "deepseek-flash", 8192, with_index=True)
        settle_deepseek_usage(index, path)
    state = json.loads((tmp_path / "generation_spend.json").read_text())
    assert state["reserved_cny"] == state["calls"][0]["upper_bound_cny"]


@pytest.mark.parametrize('mode', ['duplicate', 'wrong_model', 'invalid', 'over_bound', 'path_escape'])
def test_usage_receipt_cannot_release_unverified_budget(tmp_path, mode):
    path = tmp_path / "response.json"
    receipt(path, model="unknown" if mode == 'wrong_model' else 'deepseek-flash',
            output=-1 if mode == 'invalid' else 1000000 if mode == 'over_bound' else 1000)
    with generation_budget({"generation_budget_cny": 20}, tmp_path):
        cap, index = reserve_deepseek("中" * 20000, None, "deepseek-flash", 8192, with_index=True)
        if mode == 'duplicate':
            settle_deepseek_usage(index, path)
            cap, index = reserve_deepseek("中" * 20000, None, "deepseek-flash", 8192, with_index=True)
        if mode == 'path_escape':
            path = tmp_path.parent / (tmp_path.name + "_outside.json")
            receipt(path)
        with pytest.raises(GenerationBudgetExceeded):
            settle_deepseek_usage(index, path)
