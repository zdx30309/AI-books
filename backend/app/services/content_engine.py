"""Content engine for direct manuscript generation."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any


from openai import OpenAI

from app.core.config import settings
from app.prompts.goal_driven_templates import get_prompts
from app.services.gemini import polish_book_text, refine_book_text
from app.services.deepseek import format_book_text

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


async def generate_full_book(
    raw_text: str,
    category: str = "social_science",
    output_dir: Path | None = None,
) -> str:
    prompts = get_prompts(category)
    full_book_writer_prompt = prompts["full_book_writer"]

    generated_markdown = await _route_writer_call(full_book_writer_prompt.format(raw_text=raw_text.strip()))
    polished_markdown = await asyncio.to_thread(polish_book_text, generated_markdown)
    refined_markdown = await asyncio.to_thread(refine_book_text, polished_markdown)
    final_markdown = await asyncio.to_thread(format_book_text, refined_markdown)

    front_matter_required = ("三个备选书名", "三个备选简介", "目录")
    if not all(marker in final_markdown for marker in front_matter_required):
        logger.warning("后处理丢失前置块，回退到主模型原始输出以确保书名/简介/目录保留。")
        final_markdown = generated_markdown

    if output_dir is not None:
        try:
            from app.core.quality_metrics import measure_quality, format_report

            output_dir.mkdir(parents=True, exist_ok=True)
            report = measure_quality(final_markdown)
            report_text = format_report(report)
            report_path = output_dir / "quality_report.txt"
            report_path.write_text(report_text, encoding="utf-8")
        except Exception as exc:
            logger.warning("质量度量跳过: %s", exc)

    return final_markdown
