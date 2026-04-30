"""Gemini / Google Gen AI 调用封装。"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from google import genai

from app.core.config import settings
from app.prompts.goal_driven_templates import (
    GEMINI_FAST_POLISH_PROMPT,
    GEMINI_FAST_REFINE_PROMPT,
)


@lru_cache
def get_gemini_client() -> genai.Client:
    """按环境变量返回 Gemini Client。"""
    api_key = (settings.GOOGLE_API_KEY or os.getenv("GOOGLE_API_KEY", "")).strip()
    if not api_key:
        raise ValueError("GOOGLE_API_KEY 未配置")

    return genai.Client(api_key=api_key)


def generate_text(
    prompt: str,
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    system_instruction: str | None = None,
) -> str:
    """使用 Gemini 生成纯文本。"""
    client = get_gemini_client()
    config: dict[str, Any] = {}
    if temperature is not None:
        config["temperature"] = temperature
    if max_output_tokens is not None:
        config["max_output_tokens"] = max_output_tokens
    if system_instruction:
        config["system_instruction"] = system_instruction

    response = client.models.generate_content(
        model=model or settings.GEMINI_MODEL,
        contents=prompt,
        config=config or None,
    )
    return (response.text or "").strip()


def polish_book_text(text: str) -> str:
    """第一轮润色：自然化语言，保持原意。Prompt 来源: goal_driven_templates.GEMINI_FAST_POLISH_PROMPT"""
    return generate_text(
        text,
        model=settings.GEMINI_MODEL,
        temperature=0.3,
        system_instruction=GEMINI_FAST_POLISH_PROMPT,
    )


def refine_book_text(text: str) -> str:
    """第二轮微调：去重、改章节序号、重新分段。Prompt 来源: goal_driven_templates.GEMINI_FAST_REFINE_PROMPT"""
    return generate_text(
        text,
        model=settings.GEMINI_MODEL,
        temperature=0.2,
        system_instruction=GEMINI_FAST_REFINE_PROMPT,
    )
