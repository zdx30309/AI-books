"""Content engine for blueprint-to-book generation."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from openai import OpenAI, OpenAIError

from app.core.config import settings
from app.prompts.goal_driven_templates import (
    BLUEPRINT_ARCHITECT_PROMPT,
    CHAPTER_WRITER_PROMPT,
)

logger = logging.getLogger(__name__)

_text_client = OpenAI(
    api_key=settings.DEEPSEEK_API_KEY or "sk-placeholder",
    base_url=settings.DEEPSEEK_BASE_URL,
    timeout=settings.LLM_REQUEST_TIMEOUT,
)

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.DOTALL)


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = _JSON_FENCE_RE.sub("", text).strip()
    return text


def _extract_json_text(text: str) -> str:
    cleaned = _strip_json_fence(text)
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        raise ValueError(f"大模型返回内容中未找到 JSON 对象: {cleaned[:300]}")
    return match.group(0)


async def _chat(system_prompt: str, user_prompt: str, *, temperature: float) -> str:
    def _call() -> str:
        resp = _text_client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            response_format={"type": "json_object"} if system_prompt == BLUEPRINT_ARCHITECT_PROMPT else None,
        )
        return (resp.choices[0].message.content or "").strip()

    return await asyncio.to_thread(_call)


async def generate_blueprint(anxiety_point: str, raw_text: str) -> dict[str, Any]:
    """Generate structured blueprint JSON from anxiety point and raw material."""
    if not anxiety_point or not anxiety_point.strip():
        raise ValueError("anxiety_point 不能为空")
    if not raw_text or not raw_text.strip():
        raise ValueError("raw_text 不能为空")

    user_prompt = BLUEPRINT_ARCHITECT_PROMPT.format(
        anxiety_point=anxiety_point.strip(),
        raw_text=raw_text.strip(),
    )
    logger.info("正在生成蓝图...")
    content = await _chat(BLUEPRINT_ARCHITECT_PROMPT, user_prompt, temperature=0.3)
    json_text = _extract_json_text(content)
    try:
        return json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"蓝图 JSON 解析失败: {exc}. 原始输出: {content[:500]}") from exc


async def generate_full_book(anxiety_point: str, blueprint: dict[str, Any]) -> str:
    """Generate the full markdown book from blueprint chapters."""
    if not anxiety_point or not anxiety_point.strip():
        raise ValueError("anxiety_point 不能为空")
    if not isinstance(blueprint, dict):
        raise ValueError("blueprint 必须是字典")

    title = str(blueprint.get("title") or "未命名之书")
    intro = str(blueprint.get("intro") or "")
    preface = str(blueprint.get("preface") or "")

    final_markdown = f"# {title}\n\n## 简介\n{intro}\n\n## 导言\n{preface}\n\n"
    previous_context = preface

    chapters = blueprint.get("chapters") or []
    for idx, chapter in enumerate(chapters, start=1):
        chapter_title = str(chapter.get("chapter_title") or chapter.get("title") or f"第{idx}章")
        core_logic = str(chapter.get("core_logic") or "")
        logger.info("正在生成第 %d 章...", idx)

        user_prompt = CHAPTER_WRITER_PROMPT.format(
            anxiety_point=anxiety_point.strip(),
            previous_context=previous_context[-400:],
            chapter_title=chapter_title,
            core_logic=core_logic,
        )
        chapter_content = await _chat(CHAPTER_WRITER_PROMPT, user_prompt, temperature=0.75)
        final_markdown += f"## {chapter_title}\n{chapter_content}\n\n"
        previous_context = chapter_content[-400:]

    return final_markdown
