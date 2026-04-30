"""DeepSeek 调用封装。"""
from __future__ import annotations

import os
from functools import lru_cache

from openai import OpenAI

from app.core.config import settings
from app.prompts.goal_driven_templates import DEEPSEEK_V4_PRO_FORMAT_PROMPT


@lru_cache
def get_deepseek_client() -> OpenAI:
    api_key = (settings.DEEPSEEK_API_KEY or os.getenv("DEEPSEEK_API_KEY", "")).strip()
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY 未配置")
    return OpenAI(api_key=api_key, base_url=settings.DEEPSEEK_BASE_URL, timeout=1800.0)


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

    response = client.chat.completions.create(
        model=model or settings.DEEPSEEK_MODEL,
        messages=messages,
        temperature=temperature or 0.1,
        max_tokens=max_output_tokens,
    )
    return (response.choices[0].message.content or "").strip()


def format_book_text(text: str) -> str:
    """格式整理。Prompt 来源: goal_driven_templates.DEEPSEEK_V4_PRO_FORMAT_PROMPT"""
    rendered = DEEPSEEK_V4_PRO_FORMAT_PROMPT.format(text=text)
    return generate_text(rendered, model=settings.DEEPSEEK_MODEL, temperature=0.0)
