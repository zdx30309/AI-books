"""Claude text generation through an Anthropic-compatible relay.

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

from app.core.config import settings
from app.utils import paid_api_guard
from app.utils.distributed_limiter import distributed_semaphore

logger = logging.getLogger(__name__)


class ClaudeDeliveryUncertainError(RuntimeError):
    """The relay may have accepted a paid Claude request but its result is unknown."""

    def __init__(self, message: str) -> None:
        super().__init__(f"paid_call_delivery_uncertain: Claude {message}")


class ClaudeRequestRejectedError(RuntimeError):
    """The relay rejected the request before model execution."""


_clients: dict[tuple[str, int], httpx.Client] = {}
_clients_lock = threading.RLock()


def _validate_model_name(model_name: str) -> None:
    """Never let the Claude relay receive an OpenAI or other provider model."""

    if not model_name.lower().startswith("claude-"):
        raise ClaudeRequestRejectedError(
            f"Claude relay refuses non-Claude model={model_name!r}; "
            "configure CLAUDE_MODEL with a claude-* model"
        )


def _relay_quota_error(value: object) -> bool:
    """Recognize AnyRouter's account-level free-1m quota response."""

    message = str(value or "").lower()
    return any(
        marker in message
        for marker in (
            "1m free quota",
            "free quota has been used up",
            "免费额度已经全部用完",
            "免费额度已全部用完",
            "请充值 1m 额度",
        )
    )


def _anthropic_beta_header() -> str | None:
    raw = str(getattr(settings, "CLAUDE_ANTHROPIC_BETA", "") or "").strip()
    return raw or None


def _base_url() -> str:
    raw = str(getattr(settings, "CLAUDE_BASE_URL", "") or "https://anyrouter.top").strip().rstrip("/")
    return raw if raw.endswith("/v1") else f"{raw}/v1"


def _get_client(timeout_seconds: int) -> httpx.Client:
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
        client = http_client or httpx.Client(timeout=timeout_seconds)
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
    if isinstance(response, dict):
        blocks = response.get("content") or []
        if isinstance(blocks, list):
            parts = [
                str(block.get("text"))
                for block in blocks
                if isinstance(block, dict) and block.get("text")
            ]
            if parts:
                return "".join(parts).strip()
        return str(response.get("text") or "").strip()
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


def _status_code(exc: BaseException) -> int | None:
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(exc, "code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        try:
            if candidate is not None:
                return int(candidate)
        except (TypeError, ValueError):
            continue
    return None


def _request_rejected(exc: BaseException) -> bool:
    """Return true for explicit pre-execution relay rejections."""

    return _status_code(exc) in {400, 401, 403, 404, 409, 422}


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
    model_name = str(model or getattr(settings, "CLAUDE_MODEL", "claude-opus-5") or "claude-opus-5").strip()
    _validate_model_name(model_name)
    timeout = max(30, int(request_timeout_seconds or getattr(settings, "CLAUDE_REQUEST_TIMEOUT", 1800) or 1800))
    instruction = str(system_instruction or "").strip()
    payload: dict[str, Any] = {
        "model": model_name,
        "max_tokens": _max_tokens(str(prompt), max_output_tokens),
        "messages": [{"role": "user", "content": str(prompt)}],
    }
    if instruction:
        payload["system"] = instruction
    client = _get_client(timeout)
    request_started = False
    try:
        with distributed_semaphore(
            "claude",
            limit=max(1, int(getattr(settings, "CLAUDE_MAX_CONCURRENT_CALLS", 1) or 1)),
        ):
            paid_api_guard.guard("claude")
            request_started = True
            # AnyRouter's Claude route implements Anthropic Messages. Sending
            # Claude through OpenAI Chat Completions returns a misleading 404
            # even when the account has access to the model. Keep this request
            # single-shot because the relay may bill an accepted request.
            if temperature is not None:
                payload["temperature"] = temperature
            response = client.post(
                f"{_base_url()}/messages",
                headers={
                    "x-api-key": str(getattr(settings, "CLAUDE_API_KEY", "") or "").strip(),
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                    "accept": "application/json",
                    **(
                        {"anthropic-beta": beta}
                        if (beta := _anthropic_beta_header())
                        else {}
                    ),
                },
                json=payload,
            )
            response.raise_for_status()
            response_data = response.json()
    except Exception as exc:
        response_body = str(getattr(getattr(exc, "response", None), "text", "") or "")
        quota_signal = f"{exc} {response_body}".strip()
        if paid_api_guard.trip_if_quota("claude", quota_signal) or _relay_quota_error(quota_signal):
            if _relay_quota_error(quota_signal):
                paid_api_guard.trip("claude", quota_signal)
            raise paid_api_guard.PaidApiQuotaExhausted(
                f"Claude quota exhausted; request blocked from retry: {quota_signal}"
            ) from exc
        if _request_rejected(exc):
            raise ClaudeRequestRejectedError(
                f"relay rejected model={model_name} before execution; check model name, account access, and request settings "
                f"({type(exc).__name__}: {exc})"
            ) from exc
        if request_started:
            raise ClaudeDeliveryUncertainError(
                f"request delivery failed after the request may have been accepted; automatic replacement is disabled "
                f"({type(exc).__name__}: {exc})"
            ) from exc
        raise

    text = _message_text(response_data)
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
        model=getattr(settings, "CLAUDE_MODEL", "claude-opus-5"),
        temperature=0.3,
        **kwargs,
    )


def refine_book_text(text: str, **kwargs: Any) -> str:
    return generate_text(
        text,
        model=getattr(settings, "CLAUDE_MODEL", "claude-opus-5"),
        temperature=0.2,
        **kwargs,
    )


__all__ = [
    "ClaudeDeliveryUncertainError",
    "ClaudeRequestRejectedError",
    "generate_text",
    "polish_book_text",
    "refine_book_text",
]
