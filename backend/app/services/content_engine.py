"""Content engine for blueprint-to-book generation."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from time import perf_counter
from typing import Any

import httpx
from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    OpenAI,
)
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.prompts.goal_driven_templates import (
    SCENE_CRITIC_PROMPT,
    SCENE_WRITER_PROMPT,
    SUPREME_AUDITOR_PROMPT,
    get_prompts,
)

logger = logging.getLogger(__name__)

# 主创作链路用的同步 DeepSeek 客户端（Architect / Writer / Critic 都走这里）
_text_client = OpenAI(
    api_key=settings.DEEPSEEK_API_KEY or "sk-placeholder",
    base_url=settings.DEEPSEEK_BASE_URL,
    timeout=settings.LLM_REQUEST_TIMEOUT,
)

# 出厂终审专用的异步 OpenAI 客户端（异厂模型做最后交叉校验）
# 模型可通过环境变量 OPENAI_AUDIT_MODEL 切换，默认 gpt-4o，为 GPT-5.5 升级预留
_supreme_auditor_client: AsyncOpenAI | None = None
_OPENAI_AUDIT_MODEL = os.getenv("OPENAI_AUDIT_MODEL", "gpt-4o")


def _get_supreme_auditor_client() -> AsyncOpenAI | None:
    """Lazy-init OpenAI 异步客户端。OPENAI_API_KEY 未配置时返回 None，跳过终审。"""
    global _supreme_auditor_client
    if _supreme_auditor_client is not None:
        return _supreme_auditor_client
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    _supreme_auditor_client = AsyncOpenAI(api_key=api_key, timeout=600.0)
    return _supreme_auditor_client

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


# ---- CoT 输出剥离 ---------------------------------------------------------
# Critic 与 Supreme Auditor 都走 <audit>...</audit><final_text>...</final_text> 双标签协议
_FINAL_TEXT_RE = re.compile(
    r"<final_text>\s*([\s\S]*?)\s*</final_text>",
    re.IGNORECASE,
)
_AUDIT_BLOCK_RE = re.compile(
    r"<audit>[\s\S]*?</audit>",
    re.IGNORECASE,
)


def _extract_final_text(cot_output: str) -> str:
    """从 CoT 输出中抽取 <final_text>...</final_text> 之间的纯净正文。

    兜底策略（防止模型偶发漏标签导致整段丢失）：
        1. 优先匹配完整 <final_text>...</final_text>
        2. 若标签缺失，剥掉 <audit>...</audit> 段后回传剩余
        3. 极端情况下清掉孤立的 <final_text> / </final_text> 标记后直接返回
    """
    m = _FINAL_TEXT_RE.search(cot_output)
    if m:
        return m.group(1).strip()
    cleaned = _AUDIT_BLOCK_RE.sub("", cot_output).strip()
    cleaned = re.sub(r"</?final_text>", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned or cot_output.strip()


def _build_candidates_block(titles: list[str], intros: list[str]) -> str:
    """把蓝图阶段的 3 个备选包装方向（书名 + 简介）拼成 Markdown 头部块。

    模型可能只返回 1-2 条，按现有数量拼，最多 3 条。无候选时返回空串。
    """
    if not titles and not intros:
        return ""
    n = min(max(len(titles), len(intros)), 3)
    if n == 0:
        return ""
    labels = ["一", "二", "三"]
    lines = ["## 三个备选包装方向", ""]
    for i in range(n):
        title_i = (titles[i] if i < len(titles) else "（书名缺失）").strip()
        intro_i = (intros[i] if i < len(intros) else "（简介缺失）").strip()
        lines.append(f"**备选方案{labels[i]}**")
        lines.append(f"- 书名：《{title_i}》")
        lines.append(f"- 简介：{intro_i}")
        lines.append("")
    return "\n".join(lines) + "\n"


async def _run_supreme_audit(manuscript: str) -> str:
    """OpenAI 异厂模型担任出厂终审。

    设计：在文稿生成的最末端做一次全局扫描，对前面三道工序（Architect / Writer /
    Critic CoT）遗漏的 AI 痕迹做最后一遍清洗。同厂模型容易共享盲点，引入 OpenAI
    跨厂校验是性价比最高的兜底。

    行为：
        - OPENAI_API_KEY 未配置 → 跳过终审，回退原稿（不阻塞主流程）
        - API 调用异常 → 记录日志后回退原稿
        - 输出经 _extract_final_text 剥洗，确保 <audit>/<final_text> 标签
          绝不会泄漏到最终 Markdown 文件
    """
    client = _get_supreme_auditor_client()
    if client is None:
        logger.warning("OPENAI_API_KEY 未设置，跳过出厂终审，文稿走原稿通路。")
        return manuscript

    rendered = SUPREME_AUDITOR_PROMPT.format(manuscript_content=manuscript)
    logger.info(
        "出厂终审启动 [model=%s, 输入字数=%d]...",
        _OPENAI_AUDIT_MODEL, len(manuscript),
    )
    try:
        resp = await client.chat.completions.create(
            model=_OPENAI_AUDIT_MODEL,
            messages=[{"role": "user", "content": rendered}],
            temperature=0.3,
            timeout=600.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001 -- 终审失败不能阻塞主流程
        logger.error("出厂终审失败 [%s: %s]，回退到原稿。", type(exc).__name__, exc)
        return manuscript

    polished = _extract_final_text(raw)
    logger.info(
        "出厂终审完成 [输入=%d 字, 输出=%d 字]", len(manuscript), len(polished),
    )
    return polished


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


async def generate_blueprint(
    anxiety_point: str,
    raw_text: str,
    category: str = "social_science",
) -> dict[str, Any]:
    """Generate structured blueprint JSON from anxiety point and raw material.

    根据 category 从 PROMPT_REGISTRY 中动态提取对应的 Architect Prompt，
    实现品类路由（开闭原则）。当前默认 social_science（人文社科 / 大众焦虑）。
    """
    if not anxiety_point or not anxiety_point.strip():
        raise ValueError("anxiety_point 不能为空")
    if not raw_text or not raw_text.strip():
        raise ValueError("raw_text 不能为空")

    prompts = get_prompts(category)
    rendered_prompt = prompts["blueprint_architect"].format(
        anxiety_point=anxiety_point.strip(),
        raw_text=raw_text.strip(),
    )
    logger.info("正在生成蓝图... [category=%s]", category)
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


async def generate_full_book(
    anxiety_point: str,
    blueprint: dict[str, Any],
    category: str = "social_science",
) -> str:
    """Generate the full markdown book via the chapter × sub_scene double loop.

    流水线：
        蓝图阶段：3 个 title_candidates / intro_candidates 拼装到文档头部
        场景循环（每个 sub_scene 都执行）：
            1. Scene Writer (品类专版)            -> draft 初稿
            2. Scene Critic CoT (通用底座)        -> <audit> + <final_text>
            3. _extract_final_text                -> 纯净 polished
            4. polished 的尾部更新 previous_scene_tail
        全部章节拼装完成后：
            5. _run_supreme_audit (OpenAI 异厂)   -> 全局终审 + 标签剥离
    """
    if not anxiety_point or not anxiety_point.strip():
        raise ValueError("anxiety_point 不能为空")
    if not isinstance(blueprint, dict):
        raise ValueError("blueprint 必须是字典")

    prompts = get_prompts(category)
    scene_writer_prompt = prompts["scene_writer"]

    # 蓝图字段读取 —— title_candidates / intro_candidates 与 title / intro 并存兼容
    title_candidates_raw = blueprint.get("title_candidates") or []
    intro_candidates_raw = blueprint.get("intro_candidates") or []
    title_candidates = [str(t) for t in title_candidates_raw if t]
    intro_candidates = [str(i) for i in intro_candidates_raw if i]

    title = str(
        blueprint.get("title")
        or (title_candidates[0] if title_candidates else "未命名之书")
    )
    intro = str(
        blueprint.get("intro")
        or (intro_candidates[0] if intro_candidates else "")
    )
    preface = str(blueprint.get("preface") or "")
    global_context = str(blueprint.get("global_context") or "")

    # 头部组装：备选方案块插入到主标题与简介之间
    candidates_block = _build_candidates_block(title_candidates, intro_candidates)
    final_markdown = (
        f"# {title}\n\n"
        f"{candidates_block}"
        f"## 简介\n{intro}\n\n"
        f"## 导言\n{preface}\n\n"
    )
    # 滑动窗口式局部记忆：用最近一段成稿尾部维持子场景间的自然衔接
    previous_scene_tail = (preface or global_context or intro)[-400:]

    chapters = blueprint.get("chapters") or []
    for ch_idx, chapter in enumerate(chapters, start=1):
        chapter_title = str(
            chapter.get("chapter_title") or chapter.get("title") or f"第{ch_idx}章"
        )
        chapter_goal = str(chapter.get("chapter_goal") or chapter.get("core_logic") or "")
        sub_scenes = chapter.get("sub_scenes") or []
    title = str(blueprint.get("title") or "未命名之书")
    intro = str(blueprint.get("intro") or "")
    global_context = str(blueprint.get("global_context") or "")
    chapters = blueprint.get("chapters") or []

    final_markdown = f"# {title}\n\n## 简介\n{intro}\n\n"
    previous_scene_tail = intro[-400:] if intro else global_context[-400:]

    chapters = blueprint.get("chapters") or []
    for chapter_index, chapter in enumerate(chapters, start=1):
        chapter_title = str(chapter.get("chapter_title") or chapter.get("title") or f"第{chapter_index}章")
        chapter_goal = str(chapter.get("chapter_goal") or chapter.get("core_logic") or "")
        sub_scenes = chapter.get("sub_scenes") or []

        if not sub_scenes:
            sub_scenes = [{
                "scene_purpose": chapter_goal or "完成本章核心论证",
                "required_elements": "至少一个具体人物 / 场景 / 数据锚点",
                "target_length": 1500,
            }]

        logger.info("正在生成第 %d 章 [%s] —— 子场景数 %d", chapter_index, chapter_title, len(sub_scenes))
        final_markdown += f"## {chapter_title}\n\n"

        for scene_index, scene in enumerate(sub_scenes, start=1):
            scene_purpose = str(scene.get("scene_purpose") or "")
            required_elements = str(scene.get("required_elements") or "")
            target_length = str(scene.get("target_length") or 800)
            scene_title = str(scene.get("scene_title") or f"场景{scene_index}")
            progress_label = f"[{chapter_index}/{len(chapters)}] [{scene_index}/{len(sub_scenes)}]"

            logger.info("%s 正在生成: %s - %s (初稿)...", progress_label, chapter_title, scene_title)
            writer_rendered = scene_writer_prompt.format(
                anxiety_point=anxiety_point.strip(),
                global_context=global_context,
                chapter_title=chapter_title,
                chapter_goal=chapter_goal,
                scene_purpose=scene_purpose,
                required_elements=required_elements,
                target_length=target_length,
                previous_scene_tail=previous_scene_tail,
            )
            draft_content = await _chat(writer_rendered, json_mode=False, temperature=0.75)

            logger.info("%s 毒舌主编正在洗稿 (流式输出)...", progress_label)
            critic_rendered = SCENE_CRITIC_PROMPT.format(draft_content=draft_content)
            critic_raw = await _chat(critic_rendered, json_mode=False, temperature=0.5)
            polished = _extract_final_text(critic_raw)

            if polished:
                print(polished, end="", flush=True)
                print()

            final_markdown += f"{polished}\n\n"
            previous_scene_tail = polished[-400:] if polished else previous_scene_tail

    final_markdown = await _run_supreme_audit(final_markdown)
    return final_markdown
