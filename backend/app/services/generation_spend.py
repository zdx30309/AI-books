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


def reserve_deepseek(prompt, system, model, max_tokens):
    if not active():
        return max_tokens
    if model not in {"deepseek-flash", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"}:
        raise GenerationBudgetExceeded("当前预算未核定该模型的价格，暂停付费请求")
    cap = int(max_tokens or 16384)
    input_bound = len(prompt.encode()) + len((system or "").encode()) + 1000
    reserve("deepseek-text", (input_bound * 0.3 + cap * 1.2) / 1000000 * 10, model)
    return cap
