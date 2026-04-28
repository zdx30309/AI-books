"""Content engine for blueprint-to-book generation."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
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
from app.core.rewriter import force_rewrite_all, has_zero_tolerance
from app.prompts.goal_driven_templates import SUPREME_AUDITOR_PROMPT, get_prompts

logger = logging.getLogger(__name__)

# DeepSeek 客户端：用于物理拦截器单句重写、可选退路通道
_deepseek_client = OpenAI(
    api_key=settings.DEEPSEEK_API_KEY or "sk-placeholder",
    base_url=settings.DEEPSEEK_BASE_URL,
    timeout=settings.LLM_REQUEST_TIMEOUT,
)

# OpenAI 主笔客户端（GPT-5.5）：用于蓝图 + 全书生成
_openai_writer_client: OpenAI | None = None


def _resolve_openai_api_key() -> str | None:
    """优先读取 settings，其次回退到进程环境变量。"""
    api_key = (settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY") or "").strip()
    return api_key or None


def _get_openai_writer_client() -> OpenAI:
    """Lazy-init OpenAI 主笔客户端。"""
    global _openai_writer_client
    if _openai_writer_client is not None:
        return _openai_writer_client

    client_kwargs: dict[str, Any] = {
        "base_url": settings.OPENAI_BASE_URL,
        "timeout": 1800.0,  # GPT-5.5 xhigh 思考时间长，给足 30 分钟
    }
    api_key = _resolve_openai_api_key()
    if api_key:
        client_kwargs["api_key"] = api_key

    _openai_writer_client = OpenAI(**client_kwargs)
    return _openai_writer_client


# 出厂终审专用的异步 OpenAI 客户端（异厂模型做最后交叉校验）
# 模型可通过 settings.OPENAI_AUDITOR_MODEL 切换，默认 gpt-4o，为 GPT-5.5 升级预留
_supreme_auditor_client: AsyncOpenAI | None = None
_OPENAI_AUDIT_MODEL = settings.OPENAI_AUDITOR_MODEL


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
_JSON_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


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


def _repair_json_text(text: str) -> str:
    """对模型输出做轻量 JSON 修复，优先消除尾逗号等格式噪声。"""
    repaired = text.strip()
    repaired = _JSON_TRAILING_COMMA_RE.sub(r"\1", repaired)
    return repaired


def _loads_json_with_repair(text: str, *, source_name: str) -> dict[str, Any]:
    """宽松 JSON 解析：先直读，再做尾逗号修复，再尝试一次。"""
    candidates = [text, _repair_json_text(text)]
    last_exc: Exception | None = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
            raise ValueError(f"{source_name} 解析结果必须是字典")
        except json.JSONDecodeError as exc:
            last_exc = exc
    raise ValueError(f"{source_name} JSON 解析失败: {last_exc}. 原始输出: {text[:1000]}")


def _stream_chat(rendered_prompt: str, *, temperature: float) -> str:
    messages = [{"role": "user", "content": rendered_prompt}]
    response = _deepseek_client.chat.completions.create(
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
    resp = _deepseek_client.chat.completions.create(
        model=settings.DEEPSEEK_MODEL,
        messages=messages,
        temperature=temperature,
        timeout=180.0,
    )
    return (resp.choices[0].message.content or "").strip()


def _stream_openai_writer(rendered_prompt: str, *, effort: str, verb: str, max_tok: int) -> tuple[str, Any | None]:
    """流式收集 OpenAI Responses 输出。"""
    client = _get_openai_writer_client()
    stream = client.responses.create(
        model=settings.OPENAI_WRITER_MODEL,
        input=rendered_prompt,
        reasoning={"effort": effort},
        text={"verbosity": verb},
        max_output_tokens=max_tok,
        stream=True,
    )
    chunks: list[str] = []
    final_response: Any | None = None
    for event in stream:
        event_type = getattr(event, "type", "") or ""
        if event_type.endswith("output_text.delta"):
            delta = getattr(event, "delta", None)
            if delta:
                print(delta, end="", flush=True)
                chunks.append(delta)
        elif event_type.endswith("output_text.done"):
            pass
        elif event_type.endswith("response.completed"):
            final_response = getattr(event, "response", None) or final_response
    print()
    return "".join(chunks).strip(), final_response


def _openai_writer_call(
    rendered_prompt: str,
    *,
    reasoning_effort: str | None = None,
    verbosity: str | None = None,
    max_output_tokens: int | None = None,
) -> str:
    """通过 OpenAI Responses API 调用 GPT-5.5。"""
    effort = reasoning_effort or settings.OPENAI_WRITER_REASONING_EFFORT
    verb = verbosity or settings.OPENAI_WRITER_VERBOSITY
    max_tok = max_output_tokens or settings.OPENAI_WRITER_MAX_OUTPUT_TOKENS

    logger.info(
        "OpenAI Writer 调用 [model=%s, effort=%s, verbosity=%s, max_tokens=%d]",
        settings.OPENAI_WRITER_MODEL, effort, verb, max_tok,
    )

    response: Any | None = None
    text = ""
    try:
        text, response = _stream_openai_writer(rendered_prompt, effort=effort, verb=verb, max_tok=max_tok)
    except Exception as exc:
        logger.warning(
            "OpenAI Writer 流式调用失败 [%s: %s]，尝试重建客户端后重试一次。",
            type(exc).__name__,
            exc,
        )
        global _openai_writer_client
        _openai_writer_client = None
        client = _get_openai_writer_client()
        try:
            stream = client.responses.create(
                model=settings.OPENAI_WRITER_MODEL,
                input=rendered_prompt,
                reasoning={"effort": effort},
                text={"verbosity": verb},
                max_output_tokens=max_tok,
                stream=True,
            )
            chunks: list[str] = []
            for event in stream:
                event_type = getattr(event, "type", "") or ""
                if event_type.endswith("output_text.delta"):
                    delta = getattr(event, "delta", None)
                    if delta:
                        print(delta, end="", flush=True)
                        chunks.append(delta)
                elif event_type.endswith("response.completed"):
                    response = getattr(event, "response", None) or response
            print()
            text = "".join(chunks).strip()
        except Exception:
            logger.warning("OpenAI Writer 流式重试失败，回退到非流式 Responses 调用。")
            response = client.responses.create(
                model=settings.OPENAI_WRITER_MODEL,
                input=rendered_prompt,
                reasoning={"effort": effort},
                text={"verbosity": verb},
                max_output_tokens=max_tok,
            )
            text = getattr(response, "output_text", "") or ""

    if not text:
        if response is not None and hasattr(response, "output_text") and response.output_text:
            text = response.output_text
        else:
            text_parts: list[str] = []
            for item in getattr(response, "output", []) or []:
                if getattr(item, "type", None) == "message":
                    for content in getattr(item, "content", []) or []:
                        if getattr(content, "type", None) == "output_text":
                            text_parts.append(getattr(content, "text", ""))
            text = "".join(text_parts)

    if not text or not text.strip():
        raise ValueError("OpenAI Writer 返回空响应")

    usage = getattr(response, "usage", None) if response is not None else None
    if usage is not None:
        logger.info(
            "OpenAI Writer 用量 [input=%s, output=%s, reasoning=%s, total=%s]",
            getattr(usage, "input_tokens", "?"),
            getattr(usage, "output_tokens", "?"),
            getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", "?")
                if hasattr(usage, "output_tokens_details") else "?",
            getattr(usage, "total_tokens", "?"),
        )

    return text.strip()


async def _route_writer_call(
    rendered_prompt: str,
    *,
    temperature: float = 0.55,
    stream: bool = False,
    is_writer_task: bool = True,
) -> str:
    """主笔调用路由。

    is_writer_task=True 且 WRITER_PROVIDER=openai → 走 GPT-5.5 Responses
    否则走 DeepSeek（chat.completions）—— 用于物理拦截器单句重写
    """
    if is_writer_task and settings.WRITER_PROVIDER == "openai":
        return await asyncio.to_thread(_openai_writer_call, rendered_prompt)

    return await asyncio.to_thread(
        _call_chat,
        rendered_prompt,
        json_mode=False,
        temperature=temperature,
        stream=stream,
    )


# ---- CoT 输出剥离 ---------------------------------------------------------
# Supreme Auditor 走 <audit>...</audit><final_text>...</final_text> 双标签协议
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
        - OPENAI_AUDITOR_ENABLED=False → 直接跳过终审
        - OPENAI_API_KEY 未配置 → 跳过终审，回退原稿（不阻塞主流程）
        - API 调用异常 → 记录日志后回退原稿
        - 输出经 _extract_final_text 剥洗，确保 <audit>/<final_text> 标签
          绝不会泄漏到最终 Markdown 文件
    """
    if not settings.OPENAI_AUDITOR_ENABLED:
        logger.info("Supreme Auditor 已通过配置关闭（主笔已是 OpenAI 通道时无需终审）")
        return manuscript
    client = _get_supreme_auditor_client()
    if client is None:
        logger.warning("OPENAI_API_KEY 未设置，跳过出厂终审")
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
    content = await _route_writer_call(
        rendered_prompt,
        is_writer_task=True,
    )
    json_text = _extract_json_text(content)
    cleaned = _repair_json_text(_extract_json_like_segment(json_text))
    blueprint = _loads_json_with_repair(cleaned, source_name="蓝图")

    blueprint.setdefault("chapters", [])
    return blueprint


def _build_chapters_outline_block(chapters: list[dict]) -> str:
    """把蓝图的 chapters 数组拼成 prompt 能用的章节大纲字符串。"""
    blocks: list[str] = []
    for ch in chapters:
        num = ch.get("chapter_num", "?")
        title = ch.get("chapter_title", f"第{num}章")
        layer = ch.get("chapter_layer", "")
        goal = ch.get("chapter_goal", "")
        outline = ch.get("chapter_outline") or ch.get("core_logic", "")
        target = ch.get("target_length", 4000)

        block = (
            f"### 第 {num} 章 · {title}\n"
            f"层级：{layer}\n"
            f"目标字数：约 {target} 字\n"
            f"章节目标：{goal}\n"
            f"详细写作指南：\n{outline}\n"
        )
        blocks.append(block)
    return "\n\n".join(blocks)


async def generate_full_book(
    anxiety_point: str,
    blueprint: dict[str, Any],
    category: str = "social_science",
    output_dir: Path | None = None,
) -> str:
    """一次性出全书架构（v3 GPT-5.5 通道）。

    流程：
        1. 装配 prompt（书名/引言/前言/章节大纲全部塞进一条 prompt）
        2. 一次调用主笔（GPT-5.5 Responses API + reasoning effort）
        3. 物理拦截器扫违规并重写
        4. （可选）Supreme Auditor 终审 —— 默认关闭
        5. 自动质量度量
    """
    if not anxiety_point or not anxiety_point.strip():
        raise ValueError("anxiety_point 不能为空")
    if not isinstance(blueprint, dict):
        raise ValueError("blueprint 必须是字典")

    prompts = get_prompts(category)
    full_book_writer_prompt = prompts.get("full_book_writer")
    if full_book_writer_prompt is None:
        raise ValueError(
            f"category={category} 没有注册 full_book_writer prompt。"
            "请检查 PROMPT_REGISTRY。"
        )

    # ===== 装配输入 =====
    title_candidates_raw = blueprint.get("title_candidates") or []
    intro_candidates_raw = blueprint.get("intro_candidates") or []
    title_candidates = [str(t) for t in title_candidates_raw if t]
    intro_candidates = [str(i) for i in intro_candidates_raw if i]

    title = str(blueprint.get("title") or (title_candidates[0] if title_candidates else "未命名书稿"))
    intro = str(blueprint.get("intro") or (intro_candidates[0] if intro_candidates else ""))
    preface = str(blueprint.get("preface") or "")
    global_context = str(blueprint.get("global_context") or "")

    chapters = blueprint.get("chapters") or []
    if not chapters:
        raise ValueError("蓝图 chapters 为空，无法生成全书")

    chapters_outline = _build_chapters_outline_block(chapters)

    total_target_length = blueprint.get("total_target_length")
    if not total_target_length:
        total_target_length = sum(int(ch.get("target_length", 4000)) for ch in chapters)
    total_target_length = int(total_target_length)

    logger.info(
        "开始生成全书 [provider=%s, 章节数=%d, 目标字数=%d]",
        settings.WRITER_PROVIDER, len(chapters), total_target_length,
    )

    writer_rendered = full_book_writer_prompt.format(
        anxiety_point=anxiety_point.strip(),
        title=title,
        intro=intro,
        preface=preface,
        global_context=global_context,
        chapters_outline=chapters_outline,
        total_target_length=total_target_length,
    )

    logger.info("主笔调用开始（这一步会比较慢，xhigh 思考可能需要 5-15 分钟）...")
    raw_book = await _route_writer_call(
        writer_rendered,
        is_writer_task=True,
    )
    logger.info("主笔调用完成，原始字数 = %d", len(raw_book))

    candidates_block = _build_candidates_block(title_candidates, intro_candidates)
    if candidates_block:
        first_h2_idx = raw_book.find("\n## ")
        if first_h2_idx != -1:
            final_markdown = (
                raw_book[:first_h2_idx + 1]
                + "\n"
                + candidates_block
                + raw_book[first_h2_idx + 1:]
            )
        else:
            final_markdown = raw_book + "\n\n" + candidates_block
    else:
        final_markdown = raw_book

    logger.info("启动物理拦截器扫违规...")
    zero_hits = has_zero_tolerance(final_markdown)
    if zero_hits:
        logger.warning("触发零容忍短语 %s，启动物理重写", zero_hits)

    final_markdown, rewrite_stats = await force_rewrite_all(final_markdown)
    logger.info("物理拦截器统计: %s", rewrite_stats)

    final_markdown = await _run_supreme_audit(final_markdown)

    if output_dir is not None:
        try:
            from app.core.quality_metrics import measure_quality, format_report

            output_dir.mkdir(parents=True, exist_ok=True)
            report = measure_quality(final_markdown)
            report_text = format_report(report)

            report_path = output_dir / "quality_report.txt"
            report_path.write_text(report_text, encoding="utf-8")

            logger.info(
                "质量度量完成 [总字数=%d, 总违规=%d, 违规密度=%.2f/千字]，报告 → %s",
                report.total_chars,
                report.total_violations,
                report.violation_density_per_kchar,
                report_path,
            )
        except ImportError as exc:
            logger.warning("质量度量模块导入失败（这不应发生）: %s", exc)
        except Exception as exc:
            logger.error(
                "质量度量执行失败 [%s: %s]，不影响主流程",
                type(exc).__name__, exc,
            )

    return final_markdown
