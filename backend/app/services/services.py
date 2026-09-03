"""
Gravity Singularity - 服务层 (LLM 客户端 + 5000字切片闭环)

本模块所有函数均为同步实现，专为 Celery worker (sync runtime) 设计。
对外暴露的核心函数:
  - generate_outline(video_text)          Step 2
  - generate_cover_prompt(outline_summary) Step 3 a
  - generate_cover_image(image_prompt)     Step 3 b
  - summarize_outline_for_poster(outline)  辅助
  - generate_chapter(global_setting, chap) Step 4 ★ 5000字闭环
  - count_words(text)                      字数统计 (中文友好)
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx
from openai import OpenAI

from app.core.config import settings
from app.prompts.goal_driven_templates import (
    PROMPT_REGISTRY,
    SUPPORTED_CATEGORIES,
    get_prompts,
)
from app.services.content_engine import OpenAIDeliveryUncertainError
from app.utils.official_api import official_deepseek_base_url, official_openai_base_url
from app.utils import paid_api_guard

logger = logging.getLogger(__name__)


# ============================================================
# LLM 客户端 (单例)
# ------------------------------------------------------------
# 文本主笔根据 WRITER_PROVIDER 路由到 OpenAI / DeepSeek / Gemini。
# 图像生成继续走 OpenAI 兼容接口。
# ============================================================

_text_client = OpenAI(
    api_key=settings.OPENAI_API_KEY or "sk-placeholder",
    base_url=official_openai_base_url(settings.OPENAI_BASE_URL),
    timeout=httpx.Timeout(1800.0, connect=60.0),
    # Paid-call retries are controlled by the surrounding workflow.  The SDK
    # must never silently submit a second request after a transport error.
    max_retries=0,
)

_deepseek_client = OpenAI(
    api_key=settings.DEEPSEEK_API_KEY or "sk-placeholder",
    base_url=official_deepseek_base_url(settings.DEEPSEEK_BASE_URL),
    timeout=httpx.Timeout(1800.0, connect=60.0),
    max_retries=0,
)

_image_client = OpenAI(
    api_key=(
        settings.OPENAI_API_KEY
        if settings.OFFICIAL_API_DIRECT_ONLY and settings.OPENAI_API_KEY
        else settings.IMAGE_API_KEY or settings.OPENAI_API_KEY or "sk-placeholder"
    ),
    base_url=official_openai_base_url(settings.IMAGE_BASE_URL),
    timeout=httpx.Timeout(1800.0, connect=60.0),
    max_retries=0,
)


# ============================================================
# 字数统计 (中文网文语感)
#   - CJK 汉字逐字计数
#   - 英文 / 数字按 token (连续字母数字串) 计数
# ============================================================

_CJK_RE = re.compile(r"[一-鿿㐀-䶿]")
_ALNUM_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def count_words(text: str) -> int:
    """返回中文'字数'。空字符串返回 0。"""
    if not text:
        return 0
    return len(_CJK_RE.findall(text)) + len(_ALNUM_TOKEN_RE.findall(text))


# ============================================================
# 私有: 通用 chat 调用
#
# These are paid requests.  A transport exception can happen after the
# provider accepted and billed the request, so retrying here is unsafe.  The
# caller can explicitly resume after checking the provider response/ledger.
# ============================================================


def _chat_with_client(
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float,
    json_mode: bool = False,
    max_tokens: int | None = None,
) -> str:
    """统一的 chat.completions 入口。所有 LLM 文本调用都过这里。"""
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    provider_name = "deepseek" if client is _deepseek_client else "openai"
    max_attempts = max(1, int(getattr(settings, "LLM_MAX_RETRIES", 1) or 1))
    response = None
    for attempt in range(1, max_attempts + 1):
        try:
            paid_api_guard.guard(provider_name)
            response = client.chat.completions.create(**kwargs)
            break
        except paid_api_guard.PaidApiQuotaExhausted:
            raise
        except paid_api_guard.PaidApiGuardUnavailable:
            raise
        except Exception as exc:
            if paid_api_guard.trip_if_quota(provider_name, exc):
                raise paid_api_guard.PaidApiQuotaExhausted(
                    f"{provider_name} quota exhausted; request blocked from retry: {exc}"
                ) from exc
            if paid_api_guard.is_safe_preflight_error(exc) and attempt < max_attempts:
                delay = min(2 ** (attempt - 1) * 2, 20)
                logger.warning(
                    "%s chat preflight connection failed; retrying attempt=%d/%d in %ss: %s",
                    provider_name,
                    attempt,
                    max_attempts,
                    delay,
                    str(exc)[:300],
                )
                time.sleep(delay)
                continue
            if paid_api_guard.is_safe_preflight_error(exc):
                raise
            raise OpenAIDeliveryUncertainError(
                f"{provider_name} chat request failed after it may have been accepted; "
                f"automatic retry is disabled ({type(exc).__name__}: {exc})"
            ) from exc
    if response is None:
        raise OpenAIDeliveryUncertainError(
            f"{provider_name} chat did not return a response; automatic retry is disabled"
        )
    try:
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        raise OpenAIDeliveryUncertainError(
            f"{provider_name} chat response could not be decoded; automatic retry is disabled "
            f"({type(exc).__name__}: {exc})"
        ) from exc
    if not content:
        raise OpenAIDeliveryUncertainError(
            f"{provider_name} chat returned an empty body; automatic retry is disabled"
        )
    return content


def _chat(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float,
    json_mode: bool = False,
    max_tokens: int | None = None,
) -> str:
    provider = (settings.WRITER_PROVIDER or "openai").lower()
    if provider == "gemini":
        raise NotImplementedError(
            "当前版本未接入 Gemini SDK；请将 WRITER_PROVIDER 切换为 openai 或 deepseek。"
        )
    if provider == "deepseek":
        return _chat_with_client(
            _deepseek_client,
            settings.DEEPSEEK_MODEL,
            system_prompt,
            user_prompt,
            temperature=temperature,
            json_mode=json_mode,
            max_tokens=max_tokens,
        )
    return _chat_with_client(
        _text_client,
        settings.OPENAI_WRITER_MODEL,
        system_prompt,
        user_prompt,
        temperature=temperature,
        json_mode=json_mode,
        max_tokens=max_tokens,
    )


# ============================================================
# Gemini 兼容主笔（如果未来接入 SDK，可在此扩展）
# ============================================================


# ============================================================
# 私有工具: 容错 JSON 解析
# ============================================================

_FENCE_HEAD_RE = re.compile(r"^```(?:json)?\s*\n", re.IGNORECASE)
_FENCE_TAIL_RE = re.compile(r"\n```\s*$")


def _strip_code_fence(text: str) -> str:
    """剥离模型偶尔加上的 ``` ... ``` 代码围栏。"""
    text = text.strip()
    if text.startswith("```"):
        text = _FENCE_HEAD_RE.sub("", text)
        text = _FENCE_TAIL_RE.sub("", text)
    return text.strip()


# ============================================================
# Step 2: 大纲生成
# ============================================================

REQUIRED_OUTLINE_KEYS = ("world_building", "character_profiles", "chapters")


def generate_outline(video_text: str) -> dict:
    """
    输入原始视频文案 -> 输出严格的 JSON 大纲 dict:
      {
        "world_building": str,
        "character_profiles": str | list,
        "chapters": [
          {
            "chapter_num": int,
            "title": str,
            "core_conflict": str,
            "sub_scenes": [str, str, str, str, ...]   # 至少 4 个
          },
          ...
        ]
      }
    """
    if not video_text or not video_text.strip():
        raise ValueError("video_text 不能为空")

    user_prompt = OUTLINE_USER_TEMPLATE.format(video_text=video_text)
    raw = _chat(
        OUTLINE_SYSTEM_PROMPT,
        user_prompt,
        temperature=settings.LLM_OUTLINE_TEMPERATURE,
        json_mode=True,
    )
    raw = _strip_code_fence(raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("outline JSON 解析失败: %s\nRAW(前500字)=%s", e, raw[:500])
        raise ValueError(f"outline JSON 解析失败: {e}") from e

    # 字段最低保证
    for key in REQUIRED_OUTLINE_KEYS:
        if key not in data:
            raise ValueError(f"outline 缺少字段: {key}")
    if not isinstance(data["chapters"], list) or not data["chapters"]:
        raise ValueError("outline.chapters 必须是非空数组")

    # 章节字段对齐 + sub_scenes 兜底
    for i, ch in enumerate(data["chapters"], start=1):
        ch.setdefault("chapter_num", i)
        ch.setdefault("title", f"第{i}章")
        ch.setdefault("core_conflict", "")
        sub = ch.get("sub_scenes")
        if not isinstance(sub, list) or len(sub) < 1:
            raise ValueError(
                f"chapter {ch['chapter_num']} 的 sub_scenes 缺失或为空，无法启动切片循环"
            )
        # 子场景元素归一化为字符串
        ch["sub_scenes"] = [
            s if isinstance(s, str) else json.dumps(s, ensure_ascii=False)
            for s in sub
        ]

    return data


# ============================================================
# Step 3: 海报描述词 + 图像生成
# ============================================================


def summarize_outline_for_poster(outline: dict) -> str:
    """把大纲压成简短中文摘要，作为 POSTER_USER_TEMPLATE 的输入。"""
    parts: list[str] = []
    if outline.get("world_building"):
        parts.append(f"【世界观】{outline['world_building']}")
    if outline.get("character_profiles"):
        cp = outline["character_profiles"]
        cp_text = cp if isinstance(cp, str) else json.dumps(cp, ensure_ascii=False)
        parts.append(f"【角色】{cp_text}")
    chapters = outline.get("chapters") or []
    if chapters:
        head = "、".join(f"《{c.get('title', '?')}》" for c in chapters[:3])
        parts.append(f"【主线开篇】{head}")
        first_conflict = chapters[0].get("core_conflict")
        if first_conflict:
            parts.append(f"【开篇冲突】{first_conflict}")
    return "\n".join(parts)


def generate_cover_prompt(outline_summary: str) -> str:
    """让 LLM 输出一句纯英文 image prompt。"""
    user_prompt = POSTER_USER_TEMPLATE.format(outline_summary=outline_summary)
    return _chat(
        POSTER_SYSTEM_PROMPT,
        user_prompt,
        temperature=0.5,
    )


def generate_cover_image(image_prompt: str) -> str:
    """
    返回封面图的可访问字符串:
      - 优先返回 url (DALL-E 3 / 部分代理)
      - 否则返回 data:image/png;base64,xxx (gpt-image-1 默认)
    """
    if not image_prompt or not image_prompt.strip():
        raise ValueError("image_prompt 不能为空")

    max_attempts = max(1, int(getattr(settings, "LLM_MAX_RETRIES", 1) or 1))
    resp = None
    for attempt in range(1, max_attempts + 1):
        try:
            paid_api_guard.guard("openai-image")
            resp = _image_client.images.generate(
                model=settings.IMAGE_MODEL,
                prompt=image_prompt,
                size=settings.IMAGE_SIZE,
                n=1,
            )
            break
        except paid_api_guard.PaidApiQuotaExhausted:
            raise
        except paid_api_guard.PaidApiGuardUnavailable:
            raise
        except Exception as exc:
            if paid_api_guard.trip_if_quota("openai-image", exc):
                raise paid_api_guard.PaidApiQuotaExhausted(
                    f"openai-image quota exhausted; request blocked from retry: {exc}"
                ) from exc
            if paid_api_guard.is_safe_preflight_error(exc) and attempt < max_attempts:
                delay = min(2 ** (attempt - 1) * 2, 20)
                logger.warning(
                    "openai-image preflight connection failed; retrying attempt=%d/%d in %ss: %s",
                    attempt,
                    max_attempts,
                    delay,
                    str(exc)[:300],
                )
                time.sleep(delay)
                continue
            if paid_api_guard.is_safe_preflight_error(exc):
                raise
            raise OpenAIDeliveryUncertainError(
                "openai-image request failed after it may have been accepted; "
                f"automatic retry is disabled ({type(exc).__name__}: {exc})"
            ) from exc
    if resp is None:
        raise OpenAIDeliveryUncertainError(
            "openai-image did not return a response; automatic retry is disabled"
        )
    try:
        item = resp.data[0]
    except Exception as exc:
        raise OpenAIDeliveryUncertainError(
            f"openai-image response could not be decoded; automatic retry is disabled "
            f"({type(exc).__name__}: {exc})"
        ) from exc
    url = getattr(item, "url", None)
    if url:
        return url
    b64 = getattr(item, "b64_json", None)
    if b64:
        return f"data:image/png;base64,{b64}"
    raise OpenAIDeliveryUncertainError(
        "openai-image returned neither url nor b64_json; automatic retry is disabled"
    )


# ============================================================
# Step 4 (内层): 单个子场景撰写
# ============================================================


def write_sub_scene(
    global_setting: str,
    previous_context: str,
    current_sub_scene: str,
) -> str:
    """子场景的一次撰写调用。"""
    user_prompt = WRITING_USER_TEMPLATE.format(
        global_setting=global_setting,
        previous_context=previous_context,
        current_sub_scene=current_sub_scene,
    )
    return _chat(
        WRITING_SYSTEM_PROMPT,
        user_prompt,
        temperature=settings.LLM_WRITING_TEMPERATURE,
        max_tokens=4096,
    )


def extend_chapter_patch(
    global_setting: str,
    tail_context: str,
    current_words: int,
    gap_words: int,
) -> str:
    """补丁扩展 - 不重复、不收尾、自然续写。复用网文大神 system 人设。"""
    user_prompt = PATCH_USER_TEMPLATE.format(
        global_setting=global_setting,
        tail_context=tail_context,
        tail_window=settings.SUB_SCENE_OVERLAP_WORDS,
        current_words=current_words,
        gap_words=gap_words,
    )
    return _chat(
        WRITING_SYSTEM_PROMPT,
        user_prompt,
        temperature=settings.LLM_WRITING_TEMPERATURE,
        max_tokens=4096,
    )


# ============================================================
# Step 4 (核心 ★): 单章 5000字闭环
#   蓝图算法:
#     A. (拆解) sub_scenes 已由 Step 2 outline 提前给出
#     B. (循环) 每个子场景携带前文最后 800 字作为衔接上下文
#     C. (校验+补丁) 不足 CHAPTER_MIN_WORDS 时自动追加补丁，
#                    最多 MAX_PATCH_ROUNDS 轮，超过则保留现有内容并告警
# ============================================================


def generate_chapter(
    global_setting: str,
    chapter: dict,
) -> tuple[str, int]:
    """
    生成单章完整正文。

    入参:
      global_setting: 整本书的全局设定 (世界观+角色+核心规则) 文本
      chapter:        outline.chapters[i]，至少含 chapter_num/title/
                      core_conflict/sub_scenes

    出参:
      (full_text, word_count)  full_text 为最终拼接后的章节正文
    """
    sub_scenes: list[str] = chapter.get("sub_scenes") or []
    if not sub_scenes:
        raise ValueError(
            f"chapter {chapter.get('chapter_num')} 没有 sub_scenes，"
            "无法启动 5000 字切片循环"
        )

    chapter_num = chapter.get("chapter_num", "?")
    title = chapter.get("title", f"第{chapter_num}章")
    core_conflict = chapter.get("core_conflict", "")
    overlap = settings.SUB_SCENE_OVERLAP_WORDS

    chunks: list[str] = []
    full_text = ""

    # ---- 步骤 B: 子场景循环撰写 ----
    for idx, sub_scene in enumerate(sub_scenes, start=1):
        previous_context = (
            full_text[-overlap:]
            if full_text
            else "（本章开篇，无前文。请直接进入场景，给读者一个有冲击力的开场镜头。）"
        )
        current_task = (
            f"【本章标题】《{title}》\n"
            f"【本章核心冲突】{core_conflict}\n"
            f"【当前子场景 {idx}/{len(sub_scenes)}】{sub_scene}"
        )
        logger.info(
            "[chapter %s] 撰写子场景 %d/%d", chapter_num, idx, len(sub_scenes)
        )
        text = write_sub_scene(
            global_setting=global_setting,
            previous_context=previous_context,
            current_sub_scene=current_task,
        )
        if not text:
            raise RuntimeError(
                f"chapter {chapter_num} 子场景 {idx} 返回空文本"
            )
        chunks.append(text)
        full_text = "\n\n".join(chunks)

    # ---- 步骤 C: 字数校验 + 补丁扩展 ----
    current_words = count_words(full_text)
    logger.info(
        "[chapter %s] 子场景全部完成: 当前字数=%d (目标≥%d)",
        chapter_num, current_words, settings.CHAPTER_MIN_WORDS,
    )

    patch_round = 0
    while (
        current_words < settings.CHAPTER_MIN_WORDS
        and patch_round < settings.MAX_PATCH_ROUNDS
    ):
        patch_round += 1
        gap = max(settings.CHAPTER_TARGET_WORDS - current_words, 200)
        tail = full_text[-overlap:]
        logger.info(
            "[chapter %s] 触发补丁 %d/%d (当前=%d, 缺口≈%d)",
            chapter_num, patch_round, settings.MAX_PATCH_ROUNDS,
            current_words, gap,
        )
        patch_text = extend_chapter_patch(
            global_setting=global_setting,
            tail_context=tail,
            current_words=current_words,
            gap_words=gap,
        )
        if not patch_text:
            logger.warning(
                "[chapter %s] 补丁 %d 返回空，提前中止补丁循环",
                chapter_num, patch_round,
            )
            break
        full_text = full_text + "\n\n" + patch_text
        new_words = count_words(full_text)
        # 防御: 若补丁完全没增加字数，避免死循环
        if new_words <= current_words:
            logger.warning(
                "[chapter %s] 补丁 %d 字数无增长 (%d -> %d)，中止",
                chapter_num, patch_round, current_words, new_words,
            )
            break
        current_words = new_words

    if current_words < settings.CHAPTER_MIN_WORDS:
        logger.warning(
            "[chapter %s] 经 %d 轮补丁后仍只有 %d 字 (< %d)，按现状返回",
            chapter_num, patch_round, current_words, settings.CHAPTER_MIN_WORDS,
        )
    else:
        logger.info(
            "[chapter %s] ✓ 闭环完成: 最终字数=%d (补丁 %d 轮)",
            chapter_num, current_words, patch_round,
        )

    return full_text, current_words


# ============================================================
# 工具: 把 outline 拼成"全局宪法设定"传给写作模型
# ============================================================


def build_global_setting(outline: dict) -> str:
    """从 outline 拼出 WRITING_USER_TEMPLATE 里 {global_setting} 所需的文本。"""
    parts: list[str] = []
    if outline.get("world_building"):
        parts.append(f"# 世界观\n{outline['world_building']}")
    if outline.get("character_profiles"):
        cp = outline["character_profiles"]
        cp_text = (
            cp if isinstance(cp, str)
            else json.dumps(cp, ensure_ascii=False, indent=2)
        )
        parts.append(f"# 角色档案\n{cp_text}")
    return "\n\n".join(parts)
