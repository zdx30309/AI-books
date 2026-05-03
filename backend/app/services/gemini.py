"""Gemini / Google Gen AI 调用封装。"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from time import sleep
from typing import Any, Iterator

from google import genai
from google.genai import types

from app.core.config import settings
from app.prompts.goal_driven_templates import (
    GEMINI_FAST_POLISH_PROMPT,
    GEMINI_FAST_REFINE_PROMPT,
)

logger = logging.getLogger(__name__)


_vertex_clients: dict[int, genai.Client] = {}
_api_key_clients: dict[int, genai.Client] = {}


def _configure_google_runtime() -> None:
    """Apply ADC environment expected by google-auth and Google Gen AI."""
    credentials = (settings.GOOGLE_APPLICATION_CREDENTIALS or "").strip()
    if credentials and not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials


@contextmanager
def _google_proxy_env() -> Iterator[None]:
    """Temporarily expose a Google-only proxy during ADC/Gemini calls."""
    proxy = (settings.GOOGLE_HTTPS_PROXY or "").strip()
    if not proxy:
        yield
        return

    keys = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy")
    previous = {key: os.environ.get(key) for key in keys}
    try:
        os.environ["HTTPS_PROXY"] = proxy
        os.environ["https_proxy"] = proxy
        if proxy.startswith("http://"):
            os.environ["HTTP_PROXY"] = proxy
            os.environ["http_proxy"] = proxy
        no_proxy = previous.get("NO_PROXY") or previous.get("no_proxy") or ""
        additions = "localhost,127.0.0.1,backend,web,worker,*.supabase.co,*.pooler.supabase.com,*.upstash.io"
        os.environ["NO_PROXY"] = ",".join(part for part in (no_proxy, additions) if part)
        os.environ["no_proxy"] = os.environ["NO_PROXY"]
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _resolve_google_api_key() -> str | None:
    return (settings.GOOGLE_API_KEY or os.getenv("GOOGLE_API_KEY", "")).strip() or None


def _build_http_options(timeout_seconds: int) -> types.HttpOptions:
    return types.HttpOptions(
        apiVersion="v1",
        timeout=timeout_seconds * 1000,
    )


def _get_vertex_client(timeout_seconds: int | None = None) -> genai.Client:
    _configure_google_runtime()
    timeout = int(timeout_seconds or settings.GEMINI_REQUEST_TIMEOUT)
    if timeout in _vertex_clients:
        return _vertex_clients[timeout]

    project = (settings.GOOGLE_CLOUD_PROJECT or os.getenv("GOOGLE_CLOUD_PROJECT", "video-plan2")).strip() or "video-plan2"
    location = (settings.GOOGLE_CLOUD_LOCATION or os.getenv("GOOGLE_CLOUD_LOCATION", "global")).strip() or "global"
    with _google_proxy_env():
        client = genai.Client(
            vertexai=True,
            project=project,
            location=location,
            http_options=_build_http_options(timeout),
        )
    _vertex_clients[timeout] = client
    return client


def _get_api_key_client(timeout_seconds: int | None = None) -> genai.Client:
    _configure_google_runtime()
    timeout = int(timeout_seconds or settings.GEMINI_REQUEST_TIMEOUT)
    if timeout in _api_key_clients:
        return _api_key_clients[timeout]

    api_key = _resolve_google_api_key()
    if not api_key:
        raise ValueError(
            "GOOGLE_API_KEY 未配置，无法切换到 Gemini API Key 通道。"
            "请在 .env 设置 GOOGLE_API_KEY，或保持 GOOGLE_GENAI_USE_VERTEXAI=True 使用 Vertex AI。"
        )
    with _google_proxy_env():
        client = genai.Client(
            api_key=api_key,
            http_options=_build_http_options(timeout),
        )
    _api_key_clients[timeout] = client
    return client


def get_gemini_client() -> genai.Client:
    """按配置返回 Gemini Client：Vertex AI 或 Google AI Studio API Key。"""
    if settings.GOOGLE_GENAI_USE_VERTEXAI:
        return _get_vertex_client()
    return _get_api_key_client()


# 代理打断类错误的特征字符串：识别这些后立刻 fail-fast，不再内部 retry，
# 因为同一个本地代理在短时间内还是会撞同一堵墙，retry 只是空转。
# 让外层（content_engine 的 graceful degrade）来兜底。
_PROXY_CANCEL_SIGNATURES = (
    "499",
    "client closed request",
    "operation was cancelled",
    "cancelled",
    "readtimeout",
    "read timeout",
    "remote end closed",
    "connection reset",
    "connection aborted",
)
_RETRYABLE_SIGNATURES = _PROXY_CANCEL_SIGNATURES + (
    "504",
    "deadline_exceeded",
    "deadline expired",
    "gateway timeout",
    "service unavailable",
    "503",
    "internal server error",
    "500",
    "ssl",
    "handshake",
    "timeout",
    "temporarily unavailable",
)


def _is_retryable_error(exc: Exception) -> bool:
    """识别 Gemini/网络瞬时错误。"""
    msg = (str(exc) or "").lower()
    cls = exc.__class__.__name__.lower()
    for sig in _RETRYABLE_SIGNATURES:
        if sig in msg or sig in cls:
            return True
    return False


def generate_text(
    prompt: str,
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    system_instruction: str | None = None,
    request_timeout_seconds: int | None = None,
    retries: int = 2,
) -> str:
    """使用 Gemini 生成纯文本。

    重试策略：
    - 尊重 GOOGLE_GENAI_USE_VERTEXAI：可走 Vertex AI，也可走 API Key 通道。
    - Vertex 端遇到 504/499/SSL timeout 时，如果配置了 GOOGLE_API_KEY，会自动切到 API Key 通道。
    - 关闭 AFC，避免无工具调用场景下多余的 automatic function calling 行为。
    """
    config_kwargs: dict[str, Any] = {
        "automaticFunctionCalling": types.AutomaticFunctionCallingConfig(disable=True),
        "thinkingConfig": types.ThinkingConfig(
            includeThoughts=False,
            thinkingBudget=settings.GEMINI_THINKING_BUDGET,
        ),
    }
    if temperature is not None:
        config_kwargs["temperature"] = temperature
    if max_output_tokens is not None:
        config_kwargs["maxOutputTokens"] = max_output_tokens

    contents = prompt
    system_instruction = (system_instruction or "").strip() or None
    if system_instruction:
        config_kwargs["systemInstruction"] = system_instruction

    config = types.GenerateContentConfig(**config_kwargs)
    primary_backend = "vertex" if settings.GOOGLE_GENAI_USE_VERTEXAI else "api_key"
    backends = [primary_backend]
    if (
        primary_backend == "vertex"
        and settings.GEMINI_FALLBACK_TO_API_KEY
        and _resolve_google_api_key()
    ):
        backends.append("api_key")

    last_exc: Exception | None = None
    attempts = max(1, retries)
    timeout = int(request_timeout_seconds or settings.GEMINI_REQUEST_TIMEOUT)
    for backend in backends:
        client = _get_vertex_client(timeout) if backend == "vertex" else _get_api_key_client(timeout)
        for attempt in range(1, attempts + 1):
            try:
                with _google_proxy_env():
                    response = client.models.generate_content(
                        model=model or settings.GEMINI_MODEL,
                        contents=contents,
                        config=config,
                    )
                text = (response.text or "").strip()
                if text:
                    if backend != primary_backend:
                        logger.info("Gemini 调用通过备用通道完成: %s", backend)
                    return text
                finish_reason = None
                thoughts_tokens = None
                try:
                    if response.candidates:
                        finish_reason = response.candidates[0].finish_reason
                    usage = getattr(response, "usage_metadata", None)
                    thoughts_tokens = getattr(usage, "thoughts_token_count", None)
                except Exception:
                    pass
                raise ValueError(
                    "Gemini 返回空响应"
                    f" finish_reason={finish_reason} thoughts_tokens={thoughts_tokens}"
                )
            except Exception as exc:
                last_exc = exc
                retryable = _is_retryable_error(exc)
                logger.warning(
                    "Gemini 调用失败 backend=%s attempt=%d/%d retryable=%s error=%s: %s",
                    backend,
                    attempt,
                    attempts,
                    retryable,
                    exc.__class__.__name__,
                    str(exc)[:500],
                )
                if not retryable:
                    raise
                if attempt < attempts:
                    sleep(min(2 ** (attempt - 1) * 3, 30))
                    continue
                break

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Gemini 调用失败")


def polish_book_text(text: str) -> str:
    """第一轮润色：自然化语言，保持原意。Prompt 来源: goal_driven_templates.GEMINI_FAST_POLISH_PROMPT"""
    return generate_text(
        text,
        model=settings.GEMINI_MODEL,
        temperature=0.3,
        max_output_tokens=settings.GEMINI_POLISH_MAX_OUTPUT_TOKENS,
        system_instruction=GEMINI_FAST_POLISH_PROMPT,
        request_timeout_seconds=settings.GEMINI_REQUEST_TIMEOUT,
        retries=settings.GEMINI_MAX_RETRIES,
    )


def refine_book_text(text: str) -> str:
    """第二轮微调：去重、改章节序号、重新分段。Prompt 来源: goal_driven_templates.GEMINI_FAST_REFINE_PROMPT"""
    return generate_text(
        text,
        model=settings.GEMINI_MODEL,
        temperature=0.2,
        max_output_tokens=settings.GEMINI_REFINE_MAX_OUTPUT_TOKENS,
        system_instruction=GEMINI_FAST_REFINE_PROMPT,
        request_timeout_seconds=settings.GEMINI_REFINE_REQUEST_TIMEOUT,
        retries=settings.GEMINI_MAX_RETRIES,
    )
