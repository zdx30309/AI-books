"""Gemini / Google Gen AI 调用封装。"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from google import genai

from app.core.config import settings

FIRST_POLISH_PROMPT = (
    "将我的文本重新表达为自然流畅的现代汉语，不要掉书袋，对大众读者友好。"
)

SECOND_POLISH_PROMPT = (
    "将我的文本做二次润色微调。\n"
    "1. 删除语义重复的内容；如果前文已经出现过有关论述，将重复的内容删掉，并一笔带过。\n"
    "2. 个别措辞做微调，保持原意不变。\n"
    "3. 修改章节序号，将【第N章：】改为【N.】。\n"
    "4. 重新分段，每段 50~100 字；如内容偏多或逻辑关系紧密，可宽限到 200 字。\n"
    "不要新增观点，不要改写结构，不要加入解释。"
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
    """第一轮润色：自然化语言，保持原意。"""
    return generate_text(
        text,
        model=settings.GEMINI_MODEL,
        temperature=0.3,
        system_instruction=FIRST_POLISH_PROMPT,
    )


def refine_book_text(text: str) -> str:
    """第二轮微调：去重、改章节序号、重新分段。"""
    return generate_text(
        text,
        model=settings.GEMINI_MODEL,
        temperature=0.2,
        system_instruction=SECOND_POLISH_PROMPT,
    )


def format_book_text(text: str) -> str:
    """仅做格式整理，不修改内容。"""
    return generate_text(
        text,
        model=settings.GEMINI_MODEL,
        temperature=0.1,
        system_instruction=(
            "你是格式整理器。只根据给定文本做排版，不要改写内容，不要增删观点，不要调整语义。\n"
            "要求：只加格式，不修改内容；章节名前面加加粗符号 @；不再使用章节符号 #；保留正文原意；保留必要的分段与列表；不要加入解释、前言或总结。"
        ),
    )
