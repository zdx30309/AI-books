"""User-facing task status text helpers.

The task row also carries operational evidence used by the recovery worker.
That evidence is intentionally not suitable for the bookshelf UI: it can
contain provider names, quota responses, retry details, or implementation
markers.  Keep the internal evidence in the database/logs, but expose only a
short, stable progress message to clients.
"""

from __future__ import annotations

from typing import Any


# These markers describe queue/recovery internals rather than book progress.
# Do not include a provider name by itself: the normal workflow labels may
# legitimately say "Gemini 一润" or "DeepSeek 终稿格式化".
INTERNAL_TASK_MESSAGE_MARKERS = (
    "上游额度",
    "额度/资源",
    "额度暂",
    "退避",
    "自动续跑",
    "自动退避",
    "自动重试",
    "不会切换到",
    "费用保护",
    "成本保护",
    "generation_hold",
    "generation hold",
    "paid_api",
    "paid api",
    "paid_call",
    "delivery_uncertain",
    "delivery uncertain",
    "resource_exhausted",
    "resource exhausted",
    "insufficient_quota",
    "quota exhausted",
    "quota exhausted",
    "request blocked",
    "dunning",
    "retryable gemini",
    "retryable claude",
    "retryable openai",
    "上游暂不可用",
    "上游接口",
    "上游回传",
    "等待上游",
    "未再次投递模型调用",
    "request rejected",
    "duplicate billing",
    "重复计费",
    "manual_resume_required",
)


def is_internal_task_message(value: Any) -> bool:
    """Return whether *value* is an implementation/operations message."""

    text = str(value or "").strip().lower()
    if not text:
        return False
    return any(marker.lower() in text for marker in INTERNAL_TASK_MESSAGE_MARKERS)


def public_task_step(
    value: Any,
    *,
    status: Any = "",
    progress: Any = 0,
) -> str:
    """Convert an internal step into a normal bookshelf progress label."""

    text = str(value or "").strip()
    if text and not is_internal_task_message(text):
        return text[:255]

    state = str(status or "").strip().lower()
    try:
        pct = int(progress or 0)
    except (TypeError, ValueError):
        pct = 0

    if state == "completed":
        return "已完成"
    if state == "paused":
        return "已暂停"
    if state == "manual_review":
        return "等待处理"
    if state == "failed":
        return "生成暂时中断，已保留断点"
    if state in {
        "pending",
        "processing",
        "fetching_topics",
        "filtering_topics",
        "generating_outline",
        "writing_chapters",
        "fact_checking",
    }:
        return "正在继续生成" if pct > 0 else "生成队列中"
    return "正在处理"


def public_task_error(value: Any, *, status: Any = "") -> str | None:
    """Return a safe error string, suppressing operational evidence.

    Pending/processing quota and retry details are not errors from the user's
    perspective, so they are hidden entirely.  A genuinely terminal task gets
    a generic message without exposing provider/account information.
    """

    text = str(value or "").strip()
    if not text:
        return None
    if not is_internal_task_message(text):
        return text[:2000]
    state = str(status or "").strip().lower()
    if state in {"pending", "processing", "fetching_topics", "filtering_topics", "generating_outline", "writing_chapters", "fact_checking"}:
        return None
    if state == "completed":
        return None
    if state == "paused":
        return "任务已暂停"
    if state == "manual_review":
        return "任务等待处理"
    return "生成暂时中断，已保留断点"

