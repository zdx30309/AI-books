"""DeepSeek 调用封装。"""
from __future__ import annotations

import logging
import os
from functools import lru_cache

import httpx
from openai import OpenAI, OpenAIError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.prompts.goal_driven_templates import DEEPSEEK_V4_PRO_FORMAT_PROMPT
from app.utils.retry_utils import network_retry

logger = logging.getLogger(__name__)

_RETRYABLE_DEEPSEEK_ERRORS = (OpenAIError, httpx.HTTPError, ConnectionError, TimeoutError, ValueError)


@lru_cache
def get_deepseek_client() -> OpenAI:
    api_key = (settings.DEEPSEEK_API_KEY or os.getenv("DEEPSEEK_API_KEY", "")).strip()
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY 未配置")
    return OpenAI(
        api_key=api_key,
        base_url=settings.DEEPSEEK_BASE_URL,
        timeout=httpx.Timeout(float(settings.DEEPSEEK_REQUEST_TIMEOUT), connect=60.0),
    )


@network_retry
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

    response = client.chat.completions.create(**kwargs)
    content = (response.choices[0].message.content or "").strip()
    if not content:
        raise ValueError("DeepSeek 返回空响应")
    return content


def format_book_text(text: str) -> str:
    """格式整理。Prompt 来源: goal_driven_templates.DEEPSEEK_V4_PRO_FORMAT_PROMPT"""
    rendered = DEEPSEEK_V4_PRO_FORMAT_PROMPT.format(text=text)
    logger.info("DeepSeek 格式层开始: input_chars=%d model=%s", len(text or ""), settings.DEEPSEEK_MODEL)
    return generate_text(
        rendered,
        model=settings.DEEPSEEK_MODEL,
        temperature=0.0,
        max_output_tokens=settings.DEEPSEEK_FORMAT_MAX_TOKENS,
    )
