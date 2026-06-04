"""DeepSeek 调用封装。"""
from __future__ import annotations

import logging
import os
from functools import lru_cache

import httpx
from openai import OpenAI, OpenAIError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.prompts.goal_driven_templates import FINAL_BOOK_FORMAT_PROMPT
from app.utils.distributed_limiter import distributed_semaphore
from app.utils.official_api import official_deepseek_base_url

logger = logging.getLogger(__name__)

_RETRYABLE_DEEPSEEK_ERRORS = (OpenAIError, httpx.HTTPError, ConnectionError, TimeoutError, ValueError)
_REQUIRED_FORMAT_MARKERS = ("@最终书名", "@最终简介", "@目录", "@导言", "@正文")


class DeepSeekMaxTokensError(RuntimeError):
    """Raised when DeepSeek stops because the requested output budget is exhausted."""

    def __init__(self, partial_text: str, max_output_tokens: int | None) -> None:
        self.partial_text = partial_text
        self.max_output_tokens = max_output_tokens
        super().__init__(
            f"DeepSeek reached max_tokens={max_output_tokens}; partial_chars={len(partial_text or '')}"
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
    )


@retry(
    retry=retry_if_exception_type(_RETRYABLE_DEEPSEEK_ERRORS),
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
    fail_on_max_tokens: bool = False,
) -> str:
    client = get_deepseek_client()
    messages: list[dict[str, str]] = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    kwargs = {
        "model": model or settings.DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": 0.1 if temperature is None else temperature,
    }
    if max_output_tokens is not None:
        kwargs["max_tokens"] = max_output_tokens

    chunks: list[str] = []
    stopped_by_max_tokens = False
    try:
        with distributed_semaphore(
            "deepseek-format",
            limit=settings.DEEPSEEK_FORMAT_MAX_CONCURRENT_CALLS,
        ):
            stream = client.chat.completions.create(**kwargs, stream=True)
            for event in stream:
                if not event.choices:
                    continue
                delta = event.choices[0].delta
                piece = getattr(delta, "content", None)
                if piece:
                    chunks.append(piece)
                finish_reason = getattr(event.choices[0], "finish_reason", None)
                if finish_reason in {"length", "max_tokens"}:
                    stopped_by_max_tokens = True
    except (httpx.RemoteProtocolError, httpx.ReadError, OpenAIError) as exc:
        partial = "".join(chunks).strip()
        if _looks_like_complete_format_output(partial):
            logger.warning(
                "DeepSeek 流式连接提前关闭，但已收到结构完整输出，继续使用: chars=%d error=%s",
                len(partial),
                exc,
            )
            return partial
        get_deepseek_client.cache_clear()
        raise
    content = "".join(chunks).strip()
    if not content:
        raise ValueError("DeepSeek 返回空响应")
    if stopped_by_max_tokens and fail_on_max_tokens:
        raise DeepSeekMaxTokensError(content, max_output_tokens)
    return content


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
) -> str:
    """格式整理。Prompt 来源: goal_driven_templates.FINAL_BOOK_FORMAT_PROMPT"""
    logger.info("DeepSeek 格式层开始: input_chars=%d model=%s", len(text or ""), settings.DEEPSEEK_MODEL)
    rendered = (prompt_template or FINAL_BOOK_FORMAT_PROMPT).format(text=text)
    effective_max_tokens = settings.DEEPSEEK_FORMAT_MAX_TOKENS if max_output_tokens is None else max_output_tokens
    effective_max_tokens = int(effective_max_tokens) if int(effective_max_tokens) > 0 else None
    return generate_text(
        rendered,
        model=settings.DEEPSEEK_MODEL,
        temperature=0.0,
        max_output_tokens=effective_max_tokens,
        fail_on_max_tokens=fail_on_max_tokens,
    )
