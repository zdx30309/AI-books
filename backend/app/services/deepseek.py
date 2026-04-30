"""DeepSeek 调用封装。"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from openai import OpenAI

from app.core.config import settings

FORMAT_PROMPT = (
    "你是格式整理器，只负责排版。\n"
    "绝对禁止改写、增删、改序、合并、拆分或重述任何正文内容。\n"
    "只能做以下事情：\n"
    "1. 按原文内容保留全部语义和信息。\n"
    "2. 仅对章节标题做格式标记，章节名前面加加粗符号 @。\n"
    "3. 不再使用章节符号 #。\n"
    "4. 保留必要的分段与列表。\n"
    "5. 不要加入解释、前言、总结或任何新增内容。\n"
    "如果需要调整，必须只改格式，不得改文字内容。"
)


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
    return generate_text(text, model=settings.DEEPSEEK_MODEL, temperature=0.0, system_instruction=FORMAT_PROMPT)
