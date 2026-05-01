"""Content engine for direct manuscript generation."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Callable

from openai import OpenAI

from app.core.config import settings
from app.prompts.goal_driven_templates import get_prompts
from app.services.deepseek import format_book_text
from app.services.gemini import polish_book_text, refine_book_text

# 进度上报回调：(stage_name, info_dict)
# stage_name 取值：
#   writer_start / writer_done
#   polish_start / polish_chunk / polish_done
#   refine_start / refine_done
#   format_start / format_done
StageCallback = Callable[[str, dict[str, Any]], None]

logger = logging.getLogger(__name__)

_openai_writer_client: OpenAI | None = None


def _resolve_openai_api_key() -> str | None:
    return (settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY") or "").strip() or None


def _get_openai_writer_client() -> OpenAI:
    global _openai_writer_client
    if _openai_writer_client is not None:
        return _openai_writer_client

    client_kwargs: dict[str, Any] = {"base_url": settings.OPENAI_BASE_URL, "timeout": 1800.0}
    api_key = _resolve_openai_api_key()
    if api_key:
        client_kwargs["api_key"] = api_key

    _openai_writer_client = OpenAI(**client_kwargs)
    return _openai_writer_client


def _stream_openai_writer(rendered_prompt: str, *, effort: str, verb: str, max_tok: int) -> tuple[str, Any | None]:
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
        if (getattr(event, "type", "") or "").endswith("output_text.delta"):
            delta = getattr(event, "delta", None)
            if delta:
                print(delta, end="", flush=True)
                chunks.append(delta)
        elif (getattr(event, "type", "") or "").endswith("response.completed"):
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
    effort = reasoning_effort or settings.OPENAI_WRITER_REASONING_EFFORT
    verb = verbosity or settings.OPENAI_WRITER_VERBOSITY
    max_tok = max_output_tokens or settings.OPENAI_WRITER_MAX_OUTPUT_TOKENS

    response: Any | None = None
    text = ""
    try:
        text, response = _stream_openai_writer(rendered_prompt, effort=effort, verb=verb, max_tok=max_tok)
    except Exception:
        global _openai_writer_client
        _openai_writer_client = None
        client = _get_openai_writer_client()
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

    return text.strip()


async def _route_writer_call(rendered_prompt: str) -> str:
    return await asyncio.to_thread(_openai_writer_call, rendered_prompt)


def _split_text_by_paragraph(text: str, *, target_chars: int = 1000) -> list[str]:
    """按完整段落切块，每块目标 ~target_chars 字。

    - 段落边界 = 一个或多个空行（保留 markdown 段落语义）
    - 不破坏段落：单个段落超过 target_chars 时独立成块
    - 否则贪心拼装到接近 target_chars
    """
    import re

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para)
        if current and current_len + para_len > target_chars:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = para_len
        else:
            current.append(para)
            current_len += para_len

    if current:
        chunks.append("\n\n".join(current))

    return chunks


async def _polish_by_paragraph_chunks(
    text: str,
    *,
    target_chars: int = 1000,
    on_stage: StageCallback | None = None,
) -> str:
    """第一层润色：按完整段落切成 ~1000 字一块，每块独立调用 polish_book_text
    （都走同一份 GEMINI_FAST_POLISH_PROMPT），最终用空行拼接还原段落分隔。
    """
    if not text or not text.strip():
        return text
    chunks = _split_text_by_paragraph(text, target_chars=target_chars)
    if not chunks:
        return text

    total = len(chunks)
    polished: list[str] = []
    for idx, chunk in enumerate(chunks, 1):
        logger.info("Gemini 一润 块 %d/%d (%d 字)", idx, total, len(chunk))
        if on_stage:
            on_stage("polish_chunk", {"idx": idx, "total": total, "chars": len(chunk)})
        polished.append(await asyncio.to_thread(polish_book_text, chunk))
    return "\n\n".join(p.strip() for p in polished if p and p.strip())


def _front_matter_missing(text: str) -> bool:
    required_sections = ("三个备选书名", "三个备选简介", "目录")
    return not all(marker in text for marker in required_sections)


async def _repair_front_matter(raw_text: str, generated_markdown: str) -> str:
    repair_prompt = (
        "你现在只负责修复书稿的前置块，不要重写全文。\n"
        "必须补齐并保留以下三个部分：\n"
        "1. 三个备选书名\n"
        "2. 三个备选简介\n"
        "3. 目录\n"
        "要求：\n"
        "- 只输出修复后的完整书稿，前置块和正文都要保留\n"
        "- 正文尽量沿用原稿，不要大幅改写\n"
        "- 如果前置块缺失，请根据下方原始视频文稿和当前书稿内容补齐\n"
        "- 不要解释，不要输出分析\n\n"
        f"[原始视频文稿]\n{raw_text.strip()}\n\n"
        f"[当前书稿]\n{generated_markdown.strip()}\n"
    )
    return await _route_writer_call(repair_prompt)


def _dump_stage(output_dir: Path | None, filename: str, content: str) -> None:
    """把每一层的产物落盘，便于审稿和定位是哪一层吃掉了内容。"""
    if output_dir is None or not content:
        return
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / filename).write_text(content, encoding="utf-8")
    except Exception as exc:
        logger.warning("中间产物 %s 写盘失败: %s", filename, exc)


def _log_stage(stage: str, content: str, prev_chars: int | None = None) -> None:
    n = len(content)
    if prev_chars and prev_chars > 0:
        ratio = 100.0 * n / prev_chars
        logger.info("[stage %s] %d 字，较上一层保留 %.1f%%", stage, n, ratio)
        if ratio < 80.0:
            logger.warning(
                "[stage %s] 字数缩减超过 20%%（%d → %d），可能存在过度删减，"
                "请检查 output_dir 下的中间产物对比",
                stage, prev_chars, n,
            )
    else:
        logger.info("[stage %s] %d 字", stage, n)


async def generate_full_book(
    raw_text: str,
    category: str = "social_science",
    output_dir: Path | None = None,
    on_stage: StageCallback | None = None,
) -> str:
    prompts = get_prompts(category)
    full_book_writer_prompt = prompts["full_book_writer"]

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)

    # ---- 第 1 层: OpenAI Writer ----
    if on_stage:
        on_stage("writer_start", {})
    generated_markdown = await _route_writer_call(full_book_writer_prompt.format(raw_text=raw_text.strip()))
    _log_stage("1/4 writer", generated_markdown)
    _dump_stage(output_dir, "01_writer.md", generated_markdown)
    if on_stage:
        on_stage("writer_done", {"chars": len(generated_markdown)})

    if _front_matter_missing(generated_markdown):
        logger.warning("主模型初稿缺少前置块，尝试修复。")
        repaired_markdown = await _repair_front_matter(raw_text, generated_markdown)
        if repaired_markdown.strip():
            generated_markdown = repaired_markdown
            _log_stage("1b/4 writer-repaired", generated_markdown)
            _dump_stage(output_dir, "01b_writer_repaired.md", generated_markdown)

    writer_chars = len(generated_markdown)

    # ---- 第 2 层: Gemini 一润 (按完整段落 ~1000 字切块) ----
    if on_stage:
        on_stage("polish_start", {})
    polished_markdown = await _polish_by_paragraph_chunks(
        generated_markdown,
        target_chars=1000,
        on_stage=on_stage,
    )
    _log_stage("2/4 polish", polished_markdown, prev_chars=writer_chars)
    _dump_stage(output_dir, "02_polish.md", polished_markdown)
    polish_chars = len(polished_markdown)
    if on_stage:
        on_stage("polish_done", {"chars": polish_chars})

    # ---- 第 3 层: Gemini 二润 (整篇) ----
    if on_stage:
        on_stage("refine_start", {})
    refined_markdown = await asyncio.to_thread(refine_book_text, polished_markdown)
    _log_stage("3/4 refine", refined_markdown, prev_chars=polish_chars)
    _dump_stage(output_dir, "03_refine.md", refined_markdown)
    refine_chars = len(refined_markdown)
    if on_stage:
        on_stage("refine_done", {"chars": refine_chars})

    # ---- 第 4 层: DeepSeek 格式 (整篇) ----
    if on_stage:
        on_stage("format_start", {})
    final_markdown = await asyncio.to_thread(format_book_text, refined_markdown)
    _log_stage("4/4 format", final_markdown, prev_chars=refine_chars)
    _dump_stage(output_dir, "04_format.md", final_markdown)
    if on_stage:
        on_stage("format_done", {"chars": len(final_markdown)})

    if _front_matter_missing(final_markdown):
        logger.warning("后处理丢失前置块，回退到修复后的主模型输出。")
        final_markdown = generated_markdown

    if _front_matter_missing(final_markdown):
        logger.warning("前置块仍缺失，保留当前输出以避免整篇回退。")

    logger.info(
        "[summary] writer=%d → polish=%d → refine=%d → final=%d 字 (整体保留 %.1f%%)",
        writer_chars, polish_chars, refine_chars, len(final_markdown),
        100.0 * len(final_markdown) / max(writer_chars, 1),
    )

    if output_dir is not None:
        try:
            from app.core.quality_metrics import measure_quality, format_report

            report = measure_quality(final_markdown)
            report_text = format_report(report)
            report_path = output_dir / "quality_report.txt"
            report_path.write_text(report_text, encoding="utf-8")
        except Exception as exc:
            logger.warning("质量度量跳过: %s", exc)

    return final_markdown
