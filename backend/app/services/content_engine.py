"""Content engine for blueprint-to-book generation."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from time import perf_counter
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
    SCENE_CRITIC_PROMPT,
    SCENE_WRITER_PROMPT,
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
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"大模型返回内容中未找到完整 JSON 对象: {cleaned[:300]}")
    return cleaned[start : end + 1]


def _extract_json_like_segment(text: str) -> str:
    cleaned = _strip_json_fence(text)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return cleaned[start : end + 1]
    return cleaned


def _stream_chat(rendered_prompt: str, *, temperature: float) -> str:
    messages = [{"role": "user", "content": rendered_prompt}]
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


def _non_stream_chat(rendered_prompt: str, *, temperature: float) -> str:
    messages = [{"role": "user", "content": rendered_prompt}]
    resp = _text_client.chat.completions.create(
        model=settings.DEEPSEEK_MODEL,
        messages=messages,
        temperature=temperature,
        timeout=180.0,
    )
    return (resp.choices[0].message.content or "").strip()


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
def _call_chat(
    rendered_prompt: str,
    *,
    json_mode: bool = False,
    temperature: float,
    stream: bool = True,
) -> str:
    if stream:
        return _stream_chat(rendered_prompt, temperature=temperature)
    return _non_stream_chat(rendered_prompt, temperature=temperature)


async def _chat(
    rendered_prompt: str,
    *,
    json_mode: bool = False,
    temperature: float,
    stream: bool = True,
) -> str:
    return await asyncio.to_thread(
        _call_chat,
        rendered_prompt,
        json_mode=json_mode,
        temperature=temperature,
        stream=stream,
    )


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
    content = await _chat(rendered_prompt, json_mode=False, temperature=0.3, stream=True)
    json_text = _extract_json_text(content)
    try:
        blueprint = json.loads(json_text)
    except json.JSONDecodeError as exc:
        cleaned = _extract_json_like_segment(content)
        try:
            blueprint = json.loads(cleaned)
        except json.JSONDecodeError as inner_exc:
            raise ValueError(f"蓝图 JSON 解析失败: {exc}. 原始输出: {content[:1000]}") from inner_exc

    if not isinstance(blueprint, dict):
        raise ValueError("蓝图解析结果必须是字典")
    blueprint.setdefault("chapters", [])
    return blueprint


async def _generate_scene_draft(
    *,
    global_context: str,
    chapter_goal: str,
    scene_purpose: str,
    required_elements: str,
    previous_scene_tail: str,
    temperature: float = 0.65,
) -> str:
    rendered_prompt = SCENE_WRITER_PROMPT.format(
        global_context=global_context,
        previous_scene_tail=previous_scene_tail,
        chapter_goal=chapter_goal,
        scene_purpose=scene_purpose,
        required_elements=required_elements,
    )
    return await _chat(rendered_prompt, json_mode=False, temperature=temperature, stream=False)


async def _polish_scene_content(draft_content: str, *, temperature: float = 0.25) -> str:
    rendered_prompt = SCENE_CRITIC_PROMPT.format(draft_content=draft_content)
    return await _chat(rendered_prompt, json_mode=False, temperature=temperature, stream=True)


async def generate_full_book(anxiety_point: str, blueprint: dict[str, Any]) -> str:
    """Generate the full markdown book from blueprint chapters and sub-scenes."""
    if not anxiety_point or not anxiety_point.strip():
        raise ValueError("anxiety_point 不能为空")
    if not isinstance(blueprint, dict):
        raise ValueError("blueprint 必须是字典")

    title = str(blueprint.get("title") or "未命名之书")
    intro = str(blueprint.get("intro") or "")
    global_context = str(blueprint.get("global_context") or "")
    chapters = blueprint.get("chapters") or []

    final_markdown = f"# {title}\n\n## 简介\n{intro}\n\n"
    previous_scene_tail = intro[-400:] if intro else global_context[-400:]

    total_chapters = len(chapters)
    for chapter_index, chapter in enumerate(chapters, start=1):
        chapter_title = str(chapter.get("chapter_title") or chapter.get("title") or f"第{chapter_index}章")
        chapter_goal = str(chapter.get("chapter_goal") or chapter.get("core_logic") or "")
        logger.info("正在生成第 %d 章: %s", chapter_index, chapter_title)
        final_markdown += f"## {chapter_title}\n"

        sub_scenes = chapter.get("sub_scenes") or []
        total_scenes = len(sub_scenes)
        for scene_index, scene in enumerate(sub_scenes, start=1):
            scene_purpose = str(scene.get("scene_purpose") or "")
            required_elements = str(scene.get("required_elements") or "")
            target_length = str(scene.get("target_length") or "")
            scene_title = str(scene.get("scene_title") or f"场景{scene_index}")
            progress_label = f"[{chapter_index}/{total_chapters}] [{scene_index}/{total_scenes}]"

            logger.info("%s 正在生成: %s - %s (初稿)...", progress_label, chapter_title, scene_title)
            draft_content = await _generate_scene_draft(
                global_context=global_context,
                chapter_goal=chapter_goal,
                scene_purpose=f"{scene_purpose}\n目标字数：{target_length}".strip(),
                required_elements=required_elements,
                previous_scene_tail=previous_scene_tail,
            )

            logger.info("%s 毒舌主编正在洗稿 (流式输出)...", progress_label)
            final_scene_content = await _polish_scene_content(draft_content)

            if final_scene_content:
                print(final_scene_content, end="", flush=True)
                print()

            final_markdown += f"{final_scene_content}\n\n"
            previous_scene_tail = final_scene_content[-400:] if final_scene_content else previous_scene_tail

    return final_markdown
