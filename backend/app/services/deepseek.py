"""DeepSeek 调用封装。"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Callable

import httpx
from openai import OpenAI, OpenAIError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.prompts.goal_driven_templates import FINAL_BOOK_FORMAT_PROMPT
from app.utils.distributed_limiter import distributed_semaphore
from app.utils import paid_api_guard
from app.utils.official_api import official_deepseek_base_url

logger = logging.getLogger(__name__)

# ``ValueError`` is intentionally excluded: it is normally a local request or
# response-shape error, and retrying it would submit another paid request.
# Transport errors are handled inside ``generate_text`` after the provider
# request has been submitted and are converted to a non-retryable uncertainty
# marker.  This tuple remains only for preflight failures that are known safe
# to retry by the narrow status check below.
_RETRYABLE_DEEPSEEK_ERRORS = (OpenAIError, httpx.HTTPError, ConnectionError, TimeoutError)
_REQUIRED_FORMAT_MARKERS = ("@最终书名", "@最终简介", "@目录", "@导言", "@正文")
_DEEPSEEK_DELIVERY_MARKERS = (
    "connection",
    "connecterror",
    "connection reset",
    "connection aborted",
    "connection refused",
    "remoteprotocol",
    "read error",
    "readerror",
    "read timeout",
    "timeout",
    "timed out",
    "peer closed",
    "incomplete chunk",
    "eof",
    "ssl",
    "tls",
    "stream",
    "http 429",
    "status code 429",
    "status=429",
    "http 5",
    "status code 5",
)


class DeepSeekMaxTokensError(RuntimeError):
    """Raised when DeepSeek stops because the requested output budget is exhausted."""

    def __init__(self, partial_text: str, max_output_tokens: int | None) -> None:
        self.partial_text = partial_text
        self.max_output_tokens = max_output_tokens
        super().__init__(
            f"DeepSeek reached max_tokens={max_output_tokens}; partial_chars={len(partial_text or '')}"
        )


class DeepSeekDeliveryUncertainError(RuntimeError):
    """The DeepSeek request may have been accepted but its result is unknown."""

    def __init__(self, message: str) -> None:
        super().__init__(f"paid_call_delivery_uncertain: DeepSeek {message}")


def _deepseek_status_code(exc: BaseException) -> int | None:
    value = getattr(exc, "status_code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _deepseek_preflight_error(exc: BaseException) -> bool:
    """Return whether an error is a clearly preflight, non-billable failure."""

    status = _deepseek_status_code(exc)
    # Client/auth/validation responses are rejected before model execution.
    # 429/5xx and transport failures remain delivery-uncertain because the
    # upstream may have accepted the request before the response was lost.
    return status in {400, 401, 403, 404, 409, 422} or paid_api_guard.is_safe_preflight_error(exc)


def _should_retry_deepseek_exception(exc: BaseException) -> bool:
    """Retry only a failed connection establishment, never a submitted POST."""

    return paid_api_guard.is_safe_preflight_error(exc)


def _looks_like_deepseek_delivery_error(exc: BaseException) -> bool:
    if isinstance(exc, _RETRYABLE_DEEPSEEK_ERRORS):
        return True
    message = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in message for marker in _DEEPSEEK_DELIVERY_MARKERS)


def _deepseek_delivery_uncertain(exc: BaseException, *, phase: str) -> DeepSeekDeliveryUncertainError:
    return DeepSeekDeliveryUncertainError(
        f"{phase} failed after the request may have been accepted; automatic replacement is disabled "
        f"({type(exc).__name__}: {exc})"
    )


@lru_cache
def get_deepseek_client() -> OpenAI:
    api_key = (settings.DEEPSEEK_API_KEY or os.getenv("DEEPSEEK_API_KEY", "")).strip()
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY 未配置")
    return OpenAI(
        api_key=api_key,
        base_url=official_deepseek_base_url(settings.DEEPSEEK_BASE_URL),
        timeout=httpx.Timeout(float(settings.DEEPSEEK_REQUEST_TIMEOUT), connect=60.0),
        # Do not let the OpenAI-compatible SDK multiply our explicit workflow
        # attempt budget or replay an uncertain paid request.
        max_retries=0,
    )


@retry(
    retry=retry_if_exception(_should_retry_deepseek_exception),
    wait=wait_exponential(multiplier=2, min=5, max=60),
    stop=stop_after_attempt(settings.DEEPSEEK_MAX_RETRIES),
    reraise=True,
)
def generate_text(
    prompt: str,
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    system_instruction: str | None = None,
    thinking: bool | None = None,
    fail_on_max_tokens: bool = False,
    on_progress: Callable[[int], None] | None = None,
    request_timeout_seconds: float | None = None,
) -> str:
    from app.services.generation_spend import active, reserve_deepseek

    max_output_tokens, reservation_index = reserve_deepseek(
        prompt, system_instruction, model or settings.DEEPSEEK_MODEL, max_output_tokens, with_index=True
    )
    if active():
        thinking = False
        fail_on_max_tokens = True
    client = get_deepseek_client()
    if request_timeout_seconds:
        client = client.with_options(timeout=httpx.Timeout(float(request_timeout_seconds), connect=60.0))
    messages: list[dict[str, str]] = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    kwargs = {
        "model": model or settings.DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": 0.1 if temperature is None else temperature,
    }
    if max_output_tokens is not None and int(max_output_tokens) > 0:
        kwargs["max_tokens"] = int(max_output_tokens)
    if thinking is not None:
        kwargs["extra_body"] = {
            "thinking": {"type": "enabled" if thinking else "disabled"}
        }

    chunks: list[str] = []
    reasoning_chars = 0
    generated_chars = 0
    stopped_by_max_tokens = False
    stream_usage: dict | None = None
    stream_response_id: str | None = None
    try:
        with distributed_semaphore(
            "deepseek-format",
            limit=settings.DEEPSEEK_FORMAT_MAX_CONCURRENT_CALLS,
        ):
            paid_api_guard.guard("deepseek")
            try:
                # The POST may already be accepted even when the SDK raises a
                # transport/status exception.  Handle it before tenacity sees
                # the exception so it cannot create a replacement request.
                stream = client.chat.completions.create(
                    **kwargs, stream=True, stream_options={"include_usage": True}
                )
            except Exception as exc:
                if paid_api_guard.trip_if_quota("deepseek", exc):
                    raise paid_api_guard.PaidApiQuotaExhausted(
                        f"DeepSeek quota exhausted; request blocked from retry: {exc}"
                    ) from exc
                if not _deepseek_preflight_error(exc) and _looks_like_deepseek_delivery_error(exc):
                    raise _deepseek_delivery_uncertain(exc, phase="request creation") from exc
                raise
            try:
                for event in stream:
                    if stream_response_id is None:
                        stream_response_id = getattr(event, "id", None)
                    event_usage = getattr(event, "usage", None)
                    if event_usage is not None:
                        stream_usage = event_usage.model_dump() if hasattr(event_usage, "model_dump") else event_usage
                    if not event.choices:
                        continue
                    delta = event.choices[0].delta
                    reasoning_piece = getattr(delta, "reasoning_content", None)
                    if reasoning_piece:
                        reasoning_chars += len(reasoning_piece)
                    piece = getattr(delta, "content", None)
                    if piece:
                        chunks.append(piece)
                        generated_chars += len(piece)
                        if on_progress is not None and generated_chars % 2000 < len(piece):
                            on_progress(generated_chars)
                    finish_reason = getattr(event.choices[0], "finish_reason", None)
                    if finish_reason in {"length", "max_tokens"}:
                        stopped_by_max_tokens = True
            except Exception as exc:
                if paid_api_guard.trip_if_quota("deepseek", exc):
                    raise paid_api_guard.PaidApiQuotaExhausted(
                        f"DeepSeek quota exhausted; request blocked from retry: {exc}"
                    ) from exc
                partial = "".join(chunks).strip()
                if partial and _looks_like_complete_format_output(partial) and _looks_like_deepseek_delivery_error(exc):
                    logger.warning(
                        "DeepSeek 流式连接提前关闭，但已收到结构完整输出，继续使用: chars=%d error=%s",
                        len(partial),
                        exc,
                    )
                    return partial
                get_deepseek_client.cache_clear()
                raise _deepseek_delivery_uncertain(exc, phase="stream delivery") from exc
    except DeepSeekDeliveryUncertainError:
        raise
    except paid_api_guard.PaidApiQuotaExhausted:
        raise
    except (httpx.RemoteProtocolError, httpx.ReadError, OpenAIError, ConnectionError, TimeoutError) as exc:
        if paid_api_guard.trip_if_quota("deepseek", exc):
            raise paid_api_guard.PaidApiQuotaExhausted(
                f"DeepSeek quota exhausted; request blocked from retry: {exc}"
            ) from exc
        if _deepseek_preflight_error(exc):
            raise
        raise _deepseek_delivery_uncertain(exc, phase="request delivery") from exc
    content = "".join(chunks).strip()
    if on_progress is not None and generated_chars:
        on_progress(generated_chars)
    if not content:
        raise DeepSeekDeliveryUncertainError(
            "returned an empty body after the paid request completed "
            f"(thinking={'default' if thinking is None else thinking}, "
            f"reasoning_chars={reasoning_chars}, max_tokens={max_output_tokens})"
        )
    if stopped_by_max_tokens and fail_on_max_tokens:
        raise DeepSeekMaxTokensError(content, max_output_tokens)
    _settle_stream_usage(reservation_index, stream_usage, stream_response_id, model or settings.DEEPSEEK_MODEL)
    return content


def _settle_stream_usage(reservation_index, usage, response_id, model) -> None:
    """Release the reserved upper bound once the real token usage is known.

    ``reserve_deepseek`` holds a conservative upper bound for every call; a
    streaming reply that never reports usage would otherwise keep that bound
    reserved for the whole task and exhaust the budget long before the real
    spend reached it.
    """
    from app.services.generation_spend import settle_deepseek_stream

    if reservation_index is None:
        return
    try:
        settle_deepseek_stream(reservation_index, usage, response_id, model)
    except Exception:  # noqa: BLE001 - accounting must never break generation
        logger.warning("DeepSeek 流式用量结算跳过", exc_info=True)


def _looks_like_complete_format_output(text: str) -> bool:
    if not text or len(text) < 5000:
        return False
    return all(marker in text for marker in _REQUIRED_FORMAT_MARKERS)


def format_book_text(
    text: str,
    *,
    prompt_template: str | None = None,
    max_output_tokens: int | None = None,
    fail_on_max_tokens: bool = False,
    on_progress: Callable[[int], None] | None = None,
    retries: int | None = None,
) -> str:
    """格式整理。Prompt 来源: goal_driven_templates.FINAL_BOOK_FORMAT_PROMPT"""
    logger.info("DeepSeek 格式层开始: input_chars=%d model=%s", len(text or ""), settings.DEEPSEEK_MODEL)
    rendered = (prompt_template or FINAL_BOOK_FORMAT_PROMPT).format(text=text)
    effective_max_tokens = settings.DEEPSEEK_FORMAT_MAX_TOKENS if max_output_tokens is None else max_output_tokens
    effective_max_tokens = int(effective_max_tokens) if int(effective_max_tokens) > 0 else None
    generator = generate_text
    if retries is not None:
        generator = generate_text.retry_with(stop=stop_after_attempt(max(1, int(retries))))
    return generator(
        rendered,
        model=settings.DEEPSEEK_MODEL,
        temperature=0.0,
        max_output_tokens=effective_max_tokens,
        fail_on_max_tokens=fail_on_max_tokens,
        on_progress=on_progress,
    )
