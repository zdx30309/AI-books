"""Claude text generation through an OpenAI-compatible relay.

This client is intentionally limited to the backend polish/refine stages. It
does not participate in the main writer route, and it never retries a request
after the provider call has started because delivery may already be billable.
"""

from __future__ import annotations

import logging
import math
import threading
from typing import Any

import httpx
from openai import OpenAI

from app.core.config import settings
from app.utils import paid_api_guard
from app.utils.distributed_limiter import distributed_semaphore

logger = logging.getLogger(__name__)


class ClaudeDeliveryUncertainError(RuntimeError):
    """The relay may have accepted a paid Claude request but its result is unknown."""

    def __init__(self, message: str) -> None:
        super().__init__(f"paid_call_delivery_uncertain: Claude {message}")


_clients: dict[tuple[str, int], OpenAI] = {}
_clients_lock = threading.RLock()


def _base_url() -> str:
    raw = str(getattr(settings, "CLAUDE_BASE_URL", "") or "https://anyrouter.top").strip().rstrip("/")
    return raw if raw.endswith("/v1") else f"{raw}/v1"


def _get_client(timeout_seconds: int) -> OpenAI:
    api_key = str(getattr(settings, "CLAUDE_API_KEY", "") or "").strip()
    if not api_key:
        raise ValueError("CLAUDE_API_KEY 未配置，无法调用 Claude 中转站")

    proxy = str(getattr(settings, "CLAUDE_HTTPS_PROXY", "") or "").strip() or None
    key = (proxy or "", int(timeout_seconds))
    with _clients_lock:
        cached = _clients.get(key)
        if cached is not None:
            return cached
        http_client = httpx.Client(proxy=proxy, timeout=timeout_seconds) if proxy else None
        client = OpenAI(
            api_key=api_key,
            base_url=_base_url(),
            http_client=http_client,
            max_retries=0,
            timeout=timeout_seconds,
        )
        _clients[key] = client
        return client


def _max_tokens(prompt: str, configured: int | None) -> int:
    explicit = int(configured or getattr(settings, "CLAUDE_MAX_OUTPUT_TOKENS", 0) or 0)
    if explicit > 0:
        return explicit
    # Chinese prose is close to one token per character. Keep enough headroom
    # for a full rewrite while bounding accidental runaway spend.
    return max(4096, min(64_000, math.ceil(len(prompt) * 1.5)))


def _message_text(response: Any) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""
    content = getattr(getattr(choices[0], "message", None), "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                value = block.get("text")
            else:
                value = getattr(block, "text", None)
            if value:
                parts.append(str(value))
        return "".join(parts).strip()
    return str(content or "").strip()


def generate_text(
    prompt: str,
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    system_instruction: str | None = None,
    request_timeout_seconds: int | None = None,
    retries: int | None = None,
) -> str:
    """Generate one Claude response through the configured relay."""

    del retries  # Deliberately no paid request replay.
    model_name = str(model or getattr(settings, "CLAUDE_MODEL", "claude-5-opus") or "claude-5-opus").strip()
    timeout = max(30, int(request_timeout_seconds or getattr(settings, "CLAUDE_REQUEST_TIMEOUT", 1800) or 1800))
    messages: list[dict[str, str]] = []
    instruction = str(system_instruction or "").strip()
    if instruction:
        messages.append({"role": "system", "content": instruction})
    messages.append({"role": "user", "content": str(prompt)})
    client = _get_client(timeout)
    request_started = False
    try:
        with distributed_semaphore(
            "claude",
            limit=max(1, int(getattr(settings, "CLAUDE_MAX_CONCURRENT_CALLS", 1) or 1)),
        ):
            paid_api_guard.guard("claude")
            request_started = True
            response = client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=_max_tokens(str(prompt), max_output_tokens),
            )
    except Exception as exc:
        if paid_api_guard.trip_if_quota("claude", exc):
            raise paid_api_guard.PaidApiQuotaExhausted(
                f"Claude quota exhausted; request blocked from retry: {exc}"
            ) from exc
        if request_started:
            raise ClaudeDeliveryUncertainError(
                f"request delivery failed after the request may have been accepted; automatic replacement is disabled "
                f"({type(exc).__name__}: {exc})"
            ) from exc
        raise

    text = _message_text(response)
    if not text:
        # A successful HTTP response can still be empty because of an upstream
        # relay truncation. Treat it as uncertain delivery so callers cannot
        # replay the same paid prompt or silently fall back to another model.
        raise ClaudeDeliveryUncertainError(
            "returned an empty response after the request may have been accepted; automatic replacement is disabled"
        )
    logger.info(
        "Claude relay call completed provider=claude model=%s input_chars=%d output_chars=%d",
        model_name,
        len(str(prompt)),
        len(text),
    )
    return text


def polish_book_text(text: str, **kwargs: Any) -> str:
    return generate_text(
        text,
        model=getattr(settings, "CLAUDE_MODEL", "claude-5-opus"),
        temperature=0.3,
        **kwargs,
    )


def refine_book_text(text: str, **kwargs: Any) -> str:
    return generate_text(
        text,
        model=getattr(settings, "CLAUDE_MODEL", "claude-5-opus"),
        temperature=0.2,
        **kwargs,
    )


__all__ = [
    "ClaudeDeliveryUncertainError",
    "generate_text",
    "polish_book_text",
    "refine_book_text",
]
