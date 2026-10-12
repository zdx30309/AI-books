from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import fcntl
import json
import math

from app.services.aigc_quality_gate import AigcManualReviewRequired
from app.services.general_longform_runtime import atomic_json

_current = ContextVar("generation_spend", default=None)


class GenerationBudgetExceeded(AigcManualReviewRequired):
    pass


@contextmanager
def generation_budget(config, output_dir):
    limit = float(config.get("generation_budget_cny") or 0)
    if not math.isfinite(limit) or limit < 0:
        raise AigcManualReviewRequired("生成预算无效")
    token = _current.set((Path(output_dir), limit) if limit else None)
    try:
        yield
    finally:
        _current.reset(token)


def active():
    return _current.get() is not None


def reserve(action, amount, model):
    context = _current.get()
    if context is None:
        return
    directory, limit = context
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "generation_spend.json"
    with (directory / "generation_spend.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(path.read_text()) if path.exists() else {"reserved_cny": 0, "calls": []}
        reserved = round(float(state["reserved_cny"]) + amount, 6)
        if reserved > limit:
            raise GenerationBudgetExceeded(f"后续调用超过{limit:g}元预算，已保存断点并停止付费请求")
        state.update(limit_cny=limit, reserved_cny=reserved)
        state["calls"].append({"action": action, "model": model, "upper_bound_cny": amount,
                               "status": "reserved_not_actual_invoice"})
        atomic_json(path, state)
        return len(state["calls"]) - 1


def settle_rewrite(call_index, receipt_path):
    context = _current.get()
    if context is None or call_index is None:
        return
    directory, limit = context
    receipt_path = Path(receipt_path).resolve()
    if not receipt_path.is_relative_to(directory.resolve()):
        raise GenerationBudgetExceeded("费用凭据不属于当前任务")
    receipt = json.loads(receipt_path.read_text())
    if receipt.get("status") != 1 or not receipt.get("taskId"):
        raise GenerationBudgetExceeded("降AI账单凭据不完整，不能释放预算")
    amount = float(receipt.get("consumeAmount"))
    if not math.isfinite(amount) or amount < 0:
        raise GenerationBudgetExceeded("降AI实际费用无效")
    path = directory / "generation_spend.json"
    with (directory / "generation_spend.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(path.read_text())
        if type(call_index) is not int or not 0 <= call_index < len(state["calls"]):
            raise GenerationBudgetExceeded("费用凭据对应的预留记录无效")
        call = state["calls"][call_index]
        reference = str(receipt_path.relative_to(directory.resolve()))
        if call.get("status") == "billed_confirmed":
            if call.get("receipt") != reference or call.get("actual_cny") != amount or call.get("provider_task_id") != receipt["taskId"]:
                raise GenerationBudgetExceeded("已结算凭据发生变化")
            return
        if call.get("action") != "ai-or-v1-enhanced" or amount > call["upper_bound_cny"]:
            raise GenerationBudgetExceeded("降AI实际账单超过对应预算，需人工核对")
        if any(item.get("receipt") == reference or item.get("provider_task_id") == receipt["taskId"] for item in state["calls"]):
            raise GenerationBudgetExceeded("同一账单不能用于重复释放预算")
        state["reserved_cny"] = round(state["reserved_cny"] - call["upper_bound_cny"] + amount, 6)
        call.update(status="billed_confirmed", actual_cny=amount, receipt=reference, provider_task_id=receipt["taskId"])
        atomic_json(path, state)


def reserve_deepseek(prompt, system, model, max_tokens):
    if not active():
        return max_tokens
    if model not in {"deepseek-flash", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"}:
        raise GenerationBudgetExceeded("当前预算未核定该模型的价格，暂停付费请求")
    cap = int(max_tokens or 16384)
    input_bound = len(prompt.encode()) + len((system or "").encode()) + 1000
    reserve("deepseek-text", (input_bound * 0.3 + cap * 1.2) / 1000000 * 10, model)
    return cap
