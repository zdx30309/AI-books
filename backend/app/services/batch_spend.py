from __future__ import annotations

import asyncio
from contextvars import ContextVar
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import uuid

from app.core.config import settings


_current = ContextVar("batch_generation_spend", default=None)
_RATES = {
    "gpt-6.1-sol": (2.5, 10.0),
    "gpt-5.6-sol": (5.0, 20.0),
    "gemini-3.1-flash-lite": (0.25, 1.5),
    "deepseek-flash": (0.3, 1.2),
    # 最新旗舰（保守单价，USD/百万 token）
    "gpt-6-astra": (20.0, 75.0),
    "gemini-4-argon": (4.0, 20.0),
    "deepseek-v4-pro": (1.32, 3.96),
}


class BatchBudgetStop(RuntimeError):
    pass


def active():
    return _current.get() is not None


@contextmanager
def scope(config, output_dir, owner_id=None):
    batch_id = config.get("generation_batch_id")
    if not batch_id:
        yield
        return
    if config.get("category") not in {"popular_science", "popular_science_cognitive_upgrade"}:
        raise BatchBudgetStop("本批次费用保护只核定了科普读物流程，未发起付费请求")
    if settings.WRITER_PROVIDER != "openai" or settings.OPENAI_WRITER_STREAM:
        raise BatchBudgetStop("本批次须使用OpenAI非流式主笔，配置不一致，未发起付费请求")
    unlimited = config.get("generation_batch_budget_unlimited") is True
    limit = None if unlimited else float(config.get("generation_batch_budget_cny") or 0)
    if limit is not None and (not math.isfinite(limit) or limit <= 0):
        raise BatchBudgetStop("批次预算无效，未调用付费模型")
    directory = Path(output_dir).parent / "generation_batches" / str(uuid.UUID(str(owner_id))) / str(uuid.UUID(str(batch_id)))
    directory.mkdir(parents=True, exist_ok=True)
    token = _current.set((directory, limit, Path(output_dir).name.removeprefix("task_")))
    try:
        with _ledger():
            pass
        yield
    finally:
        _current.reset(token)


def run_generation(config, output_dir, owner_id, coroutine):
    try:
        with scope(config, output_dir, owner_id):
            return asyncio.run(coroutine)
    finally:
        coroutine.close()


@contextmanager
def _ledger():
    directory, limit, _task_id = _current.get()
    path = directory / "spend.json"
    with (directory / "spend.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(path.read_text()) if path.exists() else {"limit_cny": limit, "reserved_cny": 0.0, "calls": []}
        if limit is None and not state.get("unlimited_approved_at"):
            raise BatchBudgetStop("无上限批次尚未记录人工批准，未调用付费模型")
        if state["limit_cny"] != limit:
            raise BatchBudgetStop("批次预算与已保存账本不一致，禁止重置或自动加预算")
        yield state
        temporary = path.with_suffix(".tmp")
        with temporary.open("w") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)


def reserve(model, prompt, system, max_tokens):
    if not active():
        return None
    if model not in _RATES:
        raise BatchBudgetStop("该模型尚未核定批次价格上界，未调用付费接口")
    cap = int(max_tokens)
    if not 1 <= cap <= 65536:
        raise BatchBudgetStop("批次输出上限无效，未调用付费接口")
    input_bound = len(prompt.encode()) + len((system or "").encode()) + 4096
    if input_bound > 200000:
        raise BatchBudgetStop("批次输入超出已核定价格区间，未调用付费接口")
    input_rate, output_rate = _RATES[model]
    amount = round((input_bound * input_rate + cap * output_rate) / 1_000_000 * 10, 6)
    with _ledger() as state:
        if state.get("hold_reason"):
            raise BatchBudgetStop(state["hold_reason"])
        reserved = round(state["reserved_cny"] + amount, 6)
        if state["limit_cny"] is not None and reserved > state["limit_cny"]:
            raise BatchBudgetStop(f"后续请求将超过批次{state['limit_cny']:g}元预算，已保存断点，未发起付费请求")
        index = len(state["calls"])
        state["reserved_cny"] = reserved
        state["calls"].append({
            "task_id": _current.get()[2], "model": model,
            "action": "writer" if model.startswith("gpt-") else "polish_or_refine" if model.startswith("gemini") else "format",
            "upper_bound_cny": amount, "status": "reserved_not_actual_invoice",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    return index


def settle(index, input_tokens, output_tokens, response_id=None):
    if index is None:
        return
    if type(input_tokens) is not int or type(output_tokens) is not int or min(input_tokens, output_tokens) < 0:
        with _ledger() as state:
            state["calls"][index]["status"] = "success_usage_missing_bound_retained"
        return
    with _ledger() as state:
        call = state["calls"][index]
        if call["status"] != "reserved_not_actual_invoice":
            raise BatchBudgetStop("该费用记录已处理，禁止重复释放预算")
        input_rate, output_rate = _RATES[call["model"]]
        amount = round((input_tokens * input_rate + output_tokens * output_rate) / 1_000_000 * 10, 6)
        if amount > call["upper_bound_cny"]:
            state["hold_reason"] = "返回用量超过保守费用预留，批次暂停，需人工核对"
            call["status"] = "usage_exceeded_bound"
        else:
            state["reserved_cny"] = round(state["reserved_cny"] - call["upper_bound_cny"] + amount, 6)
            call.update(status="usage_verified_upper_bound_not_actual_invoice", usage_upper_bound_cny=amount,
                        input_tokens=input_tokens, output_tokens=output_tokens, response_id=response_id)
    if amount > call["upper_bound_cny"]:
        raise BatchBudgetStop(state["hold_reason"])


def failed(index, error):
    if index is None:
        return
    code = getattr(error, "status_code", None) or getattr(error, "code", None)
    with _ledger() as state:
        call = state["calls"][index]
        if call["status"] != "reserved_not_actual_invoice":
            return
        call.update(status="uncertain_bound_retained", error_type=type(error).__name__, error_code=code)
        if code in (400, 401, 403, 404, 413, 422, 429):
            state["reserved_cny"] = round(state["reserved_cny"] - call["upper_bound_cny"], 6)
            call["status"] = "rejected_before_generation"
        else:
            state["hold_reason"] = "请求未取得明确用量或响应，保留费用预留并暂停批次，避免重复扣费"


def response_value(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def store_result(index, result):
    if index is None:
        return
    directory, _limit, task_id = _current.get()
    path = directory / "receipts" / task_id / f"{index:04d}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        path.chmod(0o600)
    except OSError as error:
        raise BatchBudgetStop("付费响应无法写入磁盘，停止后续请求，需人工检查存储") from error
