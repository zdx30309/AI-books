"""Content engine for blueprint-to-book generation."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx
from openai import APIConnectionError, APITimeoutError, InternalServerError, OpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

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


def _log_retry(retry_state) -> None:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    attempt = retry_state.attempt_number
    logger.warning(
        "网络断开，正在进行第 %d 次重试... 原因: %s",
        attempt,
        type(exc).__name__ if exc else "unknown",
    )


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=4, max=20),
    retry=retry_if_exception_type(
        (APIConnectionError, APITimeoutError, InternalServerError, httpx.RemoteProtocolError)
    ),
    reraise=True,
    before_sleep=_log_retry,
)
def _call_chat(rendered_prompt: str, *, json_mode: bool = False, temperature: float) -> str:
    messages = [{"role": "user", "content": rendered_prompt}]

    if json_mode:
        resp = _text_client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=messages,
            temperature=temperature,
            timeout=180.0,
            response_format={"type": "json_object"},
        )
        return (resp.choices[0].message.content or "").strip()

    response = _text_client.chat.completions.create(
        model=settings.DEEPSEEK_MODEL,
        messages=messages,
        temperature=temperature,
        timeout=180.0,
        stream=True,
    )
    chunks: list[str] = []
    for chunk in response:
        delta = chunk.choices[0].delta
        content = getattr(delta, "content", None)
        if content:
            print(content, end="", flush=True)
            chunks.append(content)
    print()
    return "".join(chunks).strip()


async def _chat(rendered_prompt: str, *, json_mode: bool = False, temperature: float) -> str:
    return await asyncio.to_thread(_call_chat, rendered_prompt, json_mode=json_mode, temperature=temperature)


async def generate_blueprint(anxiety_point: str, raw_text: str) -> dict[str, Any]:
    """Generate structured blueprint JSON from anxiety point and raw material."""
    if not anxiety_point or not anxiety_point.strip():
        raise ValueError("anxiety_point 不能为空")
    if not raw_text or not raw_text.strip():
        raise ValueError("raw_text 不能为空")

    rendered_prompt = BLUEPRINT_ARCHITECT_PROMPT.format(
        anxiety_point=anxiety_point.strip(),
        raw_text=raw_text.strip(),
    )
    logger.info("正在生成蓝图...")
    content = await _chat(rendered_prompt, json_mode=True, temperature=0.3)
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

        rendered_prompt = CHAPTER_WRITER_PROMPT.format(
            anxiety_point=anxiety_point.strip(),
            previous_context=previous_context[-400:],
            chapter_title=chapter_title,
            core_logic=core_logic,
        )
        chapter_content = await _chat(rendered_prompt, json_mode=False, temperature=0.75)
        final_markdown += f"## {chapter_title}\n{chapter_content}\n\n"
        previous_context = chapter_content[-400:]

    return final_markdown
