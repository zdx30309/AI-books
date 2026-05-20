"""Content engine for direct manuscript generation."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

import httpx
from openai import OpenAI

from app.core.config import settings
from app.prompts.goal_driven_templates import get_prompts, normalize_category
from app.services.deepseek import format_book_text
from app.services.gemini import GeminiMaxTokensError, polish_book_text, refine_book_text
from app.utils.distributed_limiter import distributed_semaphore

# 进度上报回调：(stage_name, info_dict)
# stage_name 取值：
#   writer_start / writer_done
#   polish_start / polish_chunk / polish_done
#   refine_start / refine_done
#   format_start / format_done
StageCallback = Callable[[str, dict[str, Any]], None]

logger = logging.getLogger(__name__)

_openai_writer_client: OpenAI | None = None


_FRONT_SECTION_TITLES = ("最终书名", "最终简介", "三个备选书名", "三个备选简介", "目录", "导言", "正文")
_FRONT_SECTION_RE = re.compile(r"^@?\s*(最终书名|最终简介|三个备选书名|三个备选简介|目录|导言|正文)\s*$")


def _strip_title_colons(title: str) -> str:
    return re.sub(r"\s*[:：]\s*", "，", title).strip(" ，,")


def _clean_delivery_title_line(line: str, current_section: str | None) -> str:
    stripped = line.strip()
    if not stripped:
        return line.rstrip()

    marker = re.match(r"^@\s*(.+?)\s*$", stripped)
    if marker and marker.group(1).strip() in _FRONT_SECTION_TITLES:
        return "@" + marker.group(1).strip()

    should_clean = False
    prefix = ""
    title = stripped

    chapter = re.match(r"^(@?\s*\d+\.\s*)(.+?)\s*$", stripped)
    if chapter:
        should_clean = True
        prefix = chapter.group(1).strip()
        title = chapter.group(2).strip()
    elif current_section in {"最终书名", "三个备选书名", "目录"}:
        should_clean = True
        numbered = re.match(r"^(\s*(?:[-*]\s*|\d+[\.、]\s*))(.+?)\s*$", line.rstrip())
        if numbered:
            prefix = numbered.group(1)
            title = numbered.group(2).strip()
    elif stripped.startswith("@"):
        should_clean = True
        prefix = "@"
        title = stripped[1:].strip()

    if not should_clean:
        return line.rstrip()

    title = re.sub(r"\b结语\b|结语", "", title).strip()
    title = _strip_title_colons(title)
    if not title:
        title = "最后一章"
    sep = "" if prefix.endswith((" ", "\t", "@")) else " "
    return f"{prefix}{sep}{title}".rstrip()


def _postprocess_delivery_markdown(markdown: str) -> str:
    """Apply hard delivery rules that should not depend on model compliance."""
    current_section: str | None = None
    cleaned: list[str] = []
    for raw_line in (markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.rstrip()
        section = _FRONT_SECTION_RE.match(line)
        if section:
            current_section = section.group(1)
            cleaned.append("@" + current_section)
            continue
        cleaned.append(_clean_delivery_title_line(line, current_section))
    return "\n".join(cleaned)


def _chapter_title_map_from_body(lines: list[str]) -> dict[str, str]:
    chapter_titles: dict[str, str] = {}
    in_body = False
    for line in lines:
        stripped = line.strip()
        section = _FRONT_SECTION_RE.match(stripped)
        if section:
            in_body = section.group(1) == "正文"
            continue
        if not in_body:
            continue

        chapter = re.match(r"^@?\s*(\d+)[.．、]\s*(.+?)\s*$", stripped)
        if not chapter:
            continue
        title = re.sub(r"\b结语\b|结语", "", chapter.group(2)).strip()
        title = _strip_title_colons(title)
        if title:
            chapter_titles.setdefault(chapter.group(1), title)
    return chapter_titles


def _repair_blank_catalog_titles(text: str) -> str:
    """Fill catalog lines like `1.` from matching body chapter titles."""
    lines = text.split("\n")
    chapter_titles = _chapter_title_map_from_body(lines)
    if not chapter_titles:
        return text

    current_section: str | None = None
    repaired: list[str] = []
    for line in lines:
        section = _FRONT_SECTION_RE.match(line.strip())
        if section:
            current_section = section.group(1)
            repaired.append(line)
            continue

        if current_section == "目录":
            blank_number = re.match(r"^(\s*)(\d+)([.．、])\s*$", line)
            fallback_title = re.match(r"^(\s*)(\d+)([.．、])\s*最后一章\s*$", line)
            match = blank_number or fallback_title
            if match and match.group(2) in chapter_titles:
                repaired.append(
                    f"{match.group(1)}{match.group(2)}{match.group(3)} {chapter_titles[match.group(2)]}"
                )
                continue

        repaired.append(line)
    return "\n".join(repaired)


_SOFT_LINE_ENDINGS = "，,、；;：:"
_HARD_LINE_ENDINGS = "。."
_SENTENCE_ENDINGS = "。.!！?？；;：:」』”’）)]"
_CONTINUATION_PREFIXES = (
    "以及",
    "和",
    "与",
    "还有",
    "包括",
    "比如",
    "例如",
    "譬如",
    "尤其",
    "甚至",
    "同时",
    "并且",
    "而且",
    "或者",
    "或",
    "也",
    "还",
    "更",
    "但",
    "但是",
    "而",
    "却",
    "其中",
    "这些",
    "这种",
    "这类",
)


_WISDOM_STORY_INLINE_STARTERS = (
    "清晨",
    "凌晨",
    "早晨",
    "上午",
    "中午",
    "午后",
    "傍晚",
    "黄昏",
    "夜里",
    "半夜",
    "第二天",
    "第二日",
    "那天",
    "这天",
    "有一天",
    "很久以前",
    "传说",
    "民间",
    "县城",
    "城里",
    "村里",
    "山里",
    "街口",
    "巷子",
    "巷子里",
    "寺庙",
    "老",
    "小",
    "许",
    "罗",
    "陈",
    "王",
    "李",
    "张",
)


def _is_structural_markdown_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if re.match(r"^!\[[^\]]*\]\([^)]+\)\s*$", stripped):
        return True
    if stripped.startswith(("@", "#", "```", "|", ">")):
        return True
    if re.match(r"^(?:[-*+]\s+|\d+[.．、]\s+)", stripped):
        return True
    return False


def _looks_like_wisdom_story_subheading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or _is_structural_markdown_line(stripped):
        return False
    if len(stripped) > 18:
        return False
    if re.search(r"[。！？；，、,.!?;：:]", stripped):
        return False
    if stripped.startswith(_CONTINUATION_PREFIXES):
        return False
    return True


def _split_wisdom_story_inline_subheading(line: str) -> list[str]:
    stripped = line.strip()
    if len(stripped) < 18 or _is_structural_markdown_line(stripped):
        return [line]

    for starter in _WISDOM_STORY_INLINE_STARTERS:
        idx = stripped.find(starter)
        if idx < 2 or idx > 16:
            continue
        title = stripped[:idx].strip()
        body = stripped[idx:].strip()
        if _looks_like_wisdom_story_subheading(title) and len(body) >= 12:
            return [title, body]
    return [line]


def _split_wisdom_story_body_subheadings(lines: list[str]) -> list[str]:
    split_lines: list[str] = []
    previous_was_chapter_heading = False
    for line in lines:
        stripped = line.strip()
        is_chapter_heading = bool(re.match(r"^@?\s*\d+[.．、]\s+.+$", stripped))
        if previous_was_chapter_heading:
            split_lines.extend(_split_wisdom_story_inline_subheading(line))
        else:
            split_lines.append(line)
        previous_was_chapter_heading = is_chapter_heading
    return split_lines


def _looks_like_continuation_fragment(line: str) -> bool:
    stripped = line.strip()
    if not stripped or _is_structural_markdown_line(stripped):
        return False
    if stripped.startswith(("，", ",", "、", "；", ";", "：", ":", "。", ".", "）", ")", "」", "』", "”")):
        return True
    if stripped.startswith(_CONTINUATION_PREFIXES):
        return True
    core = stripped.rstrip("。.!！?？")
    return len(core) <= 28 and core.endswith(("等", "等等", "之类", "一类", "这类"))


def _join_broken_body_lines(lines: list[str], *, preserve_short_subheadings: bool = False) -> list[str]:
    """Repair obvious model-introduced line breaks inside one sentence."""
    repaired: list[str] = []
    for line in lines:
        current = line.rstrip()
        if not current.strip():
            continue
        if not repaired or _is_structural_markdown_line(current):
            repaired.append(current)
            continue

        previous = repaired[-1]
        if _is_structural_markdown_line(previous):
            repaired.append(current)
            continue
        if preserve_short_subheadings and (
            _looks_like_wisdom_story_subheading(previous)
            or _looks_like_wisdom_story_subheading(current)
        ):
            repaired.append(current)
            continue

        previous_stripped = previous.rstrip()
        current_stripped = current.lstrip()
        should_join = False
        soften_previous_period = False

        if previous_stripped.endswith(tuple(_SOFT_LINE_ENDINGS)):
            should_join = True
        elif not previous_stripped.endswith(tuple(_SENTENCE_ENDINGS)):
            should_join = True
        elif previous_stripped.endswith(tuple(_HARD_LINE_ENDINGS)) and _looks_like_continuation_fragment(current):
            should_join = True
            soften_previous_period = True

        if not should_join:
            repaired.append(current)
            continue

        if soften_previous_period:
            previous_stripped = previous_stripped[:-1] + "，"
        elif previous_stripped.endswith((",", "，", ":", "：")):
            previous_stripped = previous_stripped[:-1] + "，"
        elif previous_stripped.endswith((";", "；")):
            previous_stripped = previous_stripped[:-1] + "；"

        repaired[-1] = f"{previous_stripped}{current_stripped}"

    return repaired


def normalize_final_markdown(markdown: str, *, category: str | None = None) -> str:
    """Remove blank spacer lines only from the body, preserving front matter layout."""
    text = _postprocess_delivery_markdown(markdown).replace("\r\n", "\n").replace("\r", "\n")
    text = _repair_blank_catalog_titles(text)
    match = re.search(r"(?m)^@?\s*正文\s*$", text)
    if not match:
        lines = [line.rstrip() for line in text.split("\n")]
        compact = [line for line in lines if line.strip()]
        return "\n".join(compact).strip() + ("\n" if compact else "")

    front = text[: match.end()]
    body = text[match.end() :]
    front_lines = [line.rstrip() for line in front.split("\n")]
    body_lines = [line.rstrip() for line in body.split("\n") if line.strip()]
    is_wisdom_story = normalize_category(category or "") == "wisdom_stories"
    if is_wisdom_story:
        body_lines = _split_wisdom_story_body_subheadings(body_lines)
    body_lines = _join_broken_body_lines(
        body_lines,
        preserve_short_subheadings=is_wisdom_story,
    )
    result_lines = front_lines + body_lines
    result = "\n".join(result_lines).strip()
    return result + ("\n" if result else "")


def _resolve_openai_api_key() -> str | None:
    return (settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY") or "").strip() or None


def _get_openai_writer_client() -> OpenAI:
    global _openai_writer_client
    if _openai_writer_client is not None:
        return _openai_writer_client

    request_timeout = httpx.Timeout(1800.0, connect=30.0)
    client_kwargs: dict[str, Any] = {"base_url": settings.OPENAI_BASE_URL, "timeout": request_timeout}
    proxy = (settings.OPENAI_HTTPS_PROXY or "").strip()
    if proxy:
        logger.info("OpenAI Writer 使用代理: %s", proxy)
        client_kwargs["http_client"] = httpx.Client(proxy=proxy, timeout=request_timeout)
    api_key = _resolve_openai_api_key()
    if api_key:
        client_kwargs["api_key"] = api_key

    _openai_writer_client = OpenAI(**client_kwargs)
    return _openai_writer_client


def _reset_openai_writer_client() -> None:
    global _openai_writer_client
    _openai_writer_client = None


def _is_retryable_openai_error(exc: Exception) -> bool:
    msg = f"{exc.__class__.__name__}: {exc}".lower()
    retryable_signatures = (
        "apiconnectionerror",
        "connecterror",
        "connection error",
        "connection refused",
        "remoteprotocolerror",
        "incomplete chunked read",
        "peer closed connection",
        "ssl",
        "ssleoferror",
        "wrong version number",
        "tls",
        "certificate verify failed",
        "readtimeout",
        "read timeout",
        "timeout",
        "模型调用超时",
        "上游接口连接中断",
        "openai 初稿生成连接失败",
        "distributedlimiterror",
        "paid api concurrency limiter",
        "concurrency limiter",
        "temporarily unavailable",
        "service unavailable",
        "gateway timeout",
        "rate limit",
        "ratelimit",
        "internalservererror",
        "429",
        "500",
        "502",
        "503",
        "504",
        "520",
        "521",
        "522",
        "524",
    )
    return any(signature in msg for signature in retryable_signatures)


def _openai_writer_connection_error_message(exc: Exception, first_exc: Exception | None = None) -> str:
    proxy = (settings.OPENAI_HTTPS_PROXY or "").strip()
    parts = [
        "OpenAI 初稿生成连接失败",
        f"base_url={settings.OPENAI_BASE_URL}",
        f"proxy={proxy or '未配置'}",
        f"error={type(exc).__name__}: {exc}",
    ]
    if first_exc is not None:
        parts.append(f"stream_error={type(first_exc).__name__}: {first_exc}")
    if proxy:
        parts.append(
            "请确认北京 ECS 到日本代理服务器的 video-plan-jp-proxy-tunnel.service 正常，"
            "且容器可访问当前 OPENAI_HTTPS_PROXY；生产链路固定为北京 ECS 到日本代理服务器。"
        )
    return "；".join(parts)


def _response_attr(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _extract_openai_response_text(response: Any | None) -> str:
    if response is None:
        return ""

    output_text = _response_attr(response, "output_text", "")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    text_parts: list[str] = []
    for item in _response_attr(response, "output", []) or []:
        item_type = _response_attr(item, "type")
        if item_type == "output_text":
            item_text = _response_attr(item, "text", "")
            if isinstance(item_text, str):
                text_parts.append(item_text)
            continue

        content_items = _response_attr(item, "content", []) or []
        if isinstance(content_items, str):
            text_parts.append(content_items)
            continue

        for content in content_items:
            content_type = _response_attr(content, "type")
            if content_type in {"output_text", "text"}:
                content_text = _response_attr(content, "text", "")
                if isinstance(content_text, str):
                    text_parts.append(content_text)

    if text_parts:
        return "".join(text_parts).strip()

    model_dump = getattr(response, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
        except Exception:
            dumped = None
        if isinstance(dumped, dict) and dumped is not response:
            return _extract_openai_response_text(dumped)

    return ""


def _openai_response_debug_summary(response: Any | None) -> str:
    if response is None:
        return "response=None"

    output_types = []
    for item in _response_attr(response, "output", []) or []:
        output_types.append(str(_response_attr(item, "type", "unknown")))

    incomplete_details = _response_attr(response, "incomplete_details")
    if incomplete_details is not None:
        reason = _response_attr(incomplete_details, "reason", incomplete_details)
    else:
        reason = None

    usage = _response_attr(response, "usage")
    return (
        f"status={_response_attr(response, 'status', 'unknown')}; "
        f"incomplete_reason={reason or 'none'}; "
        f"output_types={output_types or 'none'}; "
        f"usage={usage or 'unknown'}"
    )


def _openai_writer_retry_efforts(current_effort: str) -> list[str]:
    efforts = []
    for effort in ("medium", "low"):
        if effort != current_effort and effort not in efforts:
            efforts.append(effort)
    return efforts


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


def _create_openai_writer_response(rendered_prompt: str, *, effort: str, verb: str, max_tok: int) -> Any:
    client = _get_openai_writer_client()
    return client.responses.create(
        model=settings.OPENAI_WRITER_MODEL,
        input=rendered_prompt,
        reasoning={"effort": effort},
        text={"verbosity": verb},
        max_output_tokens=max_tok,
    )


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
    first_stream_exc: Exception | None = None
    attempts = max(1, int(settings.OPENAI_WRITER_MAX_RETRIES or 1))
    backoff_seconds = max(0.0, float(settings.OPENAI_WRITER_RETRY_BACKOFF_SECONDS or 0.0))

    for attempt in range(1, attempts + 1):
        try:
            with distributed_semaphore(
                "openai-writer",
                limit=settings.OPENAI_WRITER_MAX_CONCURRENT_CALLS,
            ):
                if settings.OPENAI_WRITER_STREAM:
                    try:
                        text, response = _stream_openai_writer(rendered_prompt, effort=effort, verb=verb, max_tok=max_tok)
                    except Exception as stream_exc:
                        if first_stream_exc is None:
                            first_stream_exc = stream_exc
                        _reset_openai_writer_client()
                        response = _create_openai_writer_response(
                            rendered_prompt,
                            effort=effort,
                            verb=verb,
                            max_tok=max_tok,
                        )
                        text = _extract_openai_response_text(response)
                else:
                    response = _create_openai_writer_response(
                        rendered_prompt,
                        effort=effort,
                        verb=verb,
                        max_tok=max_tok,
                    )
                    text = _extract_openai_response_text(response)
            break
        except Exception as exc:
            _reset_openai_writer_client()
            if attempt >= attempts or not _is_retryable_openai_error(exc):
                raise RuntimeError(
                    _openai_writer_connection_error_message(exc, first_stream_exc)
                ) from exc
            sleep_for = min(backoff_seconds * attempt, 30.0)
            logger.warning(
                "OpenAI Writer 调用失败，准备重试 attempt=%d/%d sleep=%.1fs error=%s: %s",
                attempt,
                attempts,
                sleep_for,
                exc.__class__.__name__,
                str(exc)[:500],
            )
            if sleep_for > 0:
                time.sleep(sleep_for)

    if not text:
        text = _extract_openai_response_text(response)

    if not text or not text.strip():
        logger.warning("OpenAI Writer empty response: %s", _openai_response_debug_summary(response))
        client = _get_openai_writer_client()
        for retry_effort in _openai_writer_retry_efforts(effort):
            logger.warning("Retrying OpenAI Writer with reasoning_effort=%s", retry_effort)
            try:
                response = _create_openai_writer_response(
                    rendered_prompt,
                    effort=retry_effort,
                    verb=verb,
                    max_tok=max_tok,
                )
            except Exception as exc:
                _reset_openai_writer_client()
                raise RuntimeError(
                    _openai_writer_connection_error_message(exc, first_stream_exc)
                ) from exc
            text = _extract_openai_response_text(response)
            if text.strip():
                break
            logger.warning(
                "OpenAI Writer retry returned empty response: %s",
                _openai_response_debug_summary(response),
            )

    if not text or not text.strip():
        raise ValueError("OpenAI Writer 返回空响应")

    return text.strip()


async def _route_writer_call(
    rendered_prompt: str,
    *,
    reasoning_effort: str | None = None,
    verbosity: str | None = None,
    max_output_tokens: int | None = None,
) -> str:
    return await asyncio.to_thread(
        _openai_writer_call,
        rendered_prompt,
        reasoning_effort=reasoning_effort,
        verbosity=verbosity,
        max_output_tokens=max_output_tokens,
    )


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
    system_instruction: str | None = None,
    on_stage: StageCallback | None = None,
) -> str:
    """第一层润色：按完整段落切成 ~1000 字一块，每块独立调用 polish_book_text
    （都走同一份 GEMINI_FAST_POLISH_PROMPT），最终用空行拼接还原段落分隔。

    健壮性原则：一润是质量增强层，不是必经任务。任何单块失败都不应让整个任务挂掉。
    每块独立 try：成功用润色稿；失败或超时则用原 chunk 兜底，继续往下走。
    最后日志统计降级数量。
    """
    if not text or not text.strip():
        return text
    chunks = _split_text_by_paragraph(text, target_chars=target_chars)
    if not chunks:
        return text

    total = len(chunks)
    polished: list[str | None] = [None] * total
    fallback_count = 0
    completed_count = 0
    concurrency = max(1, min(total, int(settings.GEMINI_POLISH_CONCURRENCY or 1)))
    semaphore = asyncio.Semaphore(concurrency)
    progress_lock = asyncio.Lock()

    async def _polish_once(chunk: str) -> str:
        return await _to_thread_with_optional_timeout(
            polish_book_text,
            chunk,
            system_instruction=system_instruction,
            timeout_seconds=settings.GEMINI_POLISH_CALL_TIMEOUT,
        )

    async def _mark_completed(chunk_idx: int, chunk: str) -> None:
        nonlocal completed_count
        if not on_stage:
            return
        async with progress_lock:
            completed_count += 1
            on_stage(
                "polish_chunk",
                {
                    "idx": completed_count,
                    "total": total,
                    "chars": len(chunk),
                    "chunk_idx": chunk_idx,
                },
            )

    async def _polish_chunk(idx: int, chunk: str) -> None:
        nonlocal fallback_count
        async with semaphore:
            logger.info(
                "Gemini 一润 块 %d/%d (%d 字) concurrency=%d",
                idx + 1,
                total,
                len(chunk),
                concurrency,
            )
            try:
                polished[idx] = await _polish_once(chunk)
            except asyncio.TimeoutError as exc:
                first_err = f"超时（{exc.__class__.__name__}）"
                fallback_count += 1
                logger.error(
                    "Gemini 一润 块 %d/%d 失败 [%s]，回退到原文继续",
                    idx + 1, total, first_err,
                )
                polished[idx] = chunk
            except Exception as exc:
                first_err = f"{exc.__class__.__name__}: {exc}"
                fallback_count += 1
                logger.error(
                    "Gemini 一润 块 %d/%d 失败 [%s]，回退到原文继续",
                    idx + 1, total, first_err,
                )
                polished[idx] = chunk
            await _mark_completed(idx + 1, chunk)

    await asyncio.gather(*(_polish_chunk(idx, chunk) for idx, chunk in enumerate(chunks)))

    if fallback_count:
        logger.warning(
            "Gemini 一润完成但有降级 [成功 %d / 总 %d / 降级 %d 块]",
            total - fallback_count, total, fallback_count,
        )

    return "\n\n".join(
        (item or chunks[idx]).strip()
        for idx, item in enumerate(polished)
        if (item or chunks[idx]).strip()
    )


async def _polish_by_paragraph_chunks_strict(
    text: str,
    *,
    target_chars: int = 1000,
    system_instruction: str | None = None,
    on_stage: StageCallback | None = None,
    output_dir: Path | None = None,
    chunk_func: Callable[..., str] | None = None,
    call_timeout_seconds: int | float | None = None,
    cache_subdir: str = "02_polish_chunks",
    progress_stage: str = "polish_chunk",
    log_label: str = "Gemini polish",
    quality_gate_name: str = "Gemini polish",
) -> str:
    """Quality-safe Gemini polish with per-chunk resume cache.

    The old path allowed failed chunks to fall back to raw text. That keeps tasks
    moving, but it can quietly lower manuscript quality under high concurrency.
    This path caches successful chunks and raises a retryable error when any
    chunk fails, so Celery can retry only the missing pieces on the next run.
    """
    if not text or not text.strip():
        return text
    chunks = _split_text_by_paragraph(text, target_chars=target_chars)
    if not chunks:
        return text

    total = len(chunks)
    polished: list[str | None] = [None] * total
    errors: list[str] = []
    completed_count = 0
    concurrency = max(1, min(total, int(settings.GEMINI_POLISH_CONCURRENCY or 1)))
    semaphore = asyncio.Semaphore(concurrency)
    progress_lock = asyncio.Lock()
    error_lock = asyncio.Lock()
    abort_event = asyncio.Event()
    cache_dir = output_dir / cache_subdir if output_dir is not None else None
    call = chunk_func or polish_book_text
    timeout = (
        settings.GEMINI_POLISH_CALL_TIMEOUT
        if call_timeout_seconds is None
        else call_timeout_seconds
    )

    def _cache_path(idx: int, chunk: str) -> Path | None:
        if cache_dir is None:
            return None
        digest = hashlib.sha256(chunk.encode("utf-8")).hexdigest()[:16]
        return cache_dir / f"{idx + 1:03d}_{digest}.md"

    def _read_cache(idx: int, chunk: str) -> str | None:
        path = _cache_path(idx, chunk)
        if path is None or not path.exists():
            return None
        try:
            content = path.read_text(encoding="utf-8").strip()
            return content or None
        except Exception as exc:
            logger.warning("%s cache read failed chunk=%d: %s", log_label, idx + 1, exc)
            return None

    def _write_cache(idx: int, chunk: str, content: str) -> None:
        path = _cache_path(idx, chunk)
        if path is None or not content.strip():
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content.strip(), encoding="utf-8")
        except Exception as exc:
            logger.warning("%s cache write failed chunk=%d: %s", log_label, idx + 1, exc)

    async def _polish_once(chunk: str) -> str:
        return await _to_thread_with_optional_timeout(
            call,
            chunk,
            system_instruction=system_instruction,
            timeout_seconds=timeout,
        )

    async def _mark_completed(chunk_idx: int, chunk: str) -> None:
        nonlocal completed_count
        if not on_stage:
            return
        async with progress_lock:
            completed_count += 1
            on_stage(
                progress_stage,
                {
                    "idx": completed_count,
                    "total": total,
                    "chars": len(chunk),
                    "chunk_idx": chunk_idx,
                },
            )

    async def _polish_chunk(idx: int, chunk: str) -> None:
        if abort_event.is_set():
            return

        cached = _read_cache(idx, chunk)
        if cached:
            polished[idx] = cached
            logger.info("%s chunk %d/%d reused from cache", log_label, idx + 1, total)
            await _mark_completed(idx + 1, cached)
            return

        async with semaphore:
            if abort_event.is_set():
                return

            logger.info(
                "%s chunk %d/%d (%d chars) concurrency=%d",
                log_label,
                idx + 1,
                total,
                len(chunk),
                concurrency,
            )
            try:
                result = await _polish_once(chunk)
                polished[idx] = result
                _write_cache(idx, chunk, result)
                await _mark_completed(idx + 1, result)
            except asyncio.TimeoutError as exc:
                message = f"TimeoutError: {exc}"
                async with error_lock:
                    errors.append(f"chunk {idx + 1}/{total}: {message}")
                    abort_event.set()
                logger.error(
                    "%s chunk %d/%d failed [%s]; task will retry",
                    log_label,
                    idx + 1,
                    total,
                    message,
                )
            except Exception as exc:
                message = f"{exc.__class__.__name__}: {exc}"
                async with error_lock:
                    errors.append(f"chunk {idx + 1}/{total}: {message}")
                    abort_event.set()
                logger.error(
                    "%s chunk %d/%d failed [%s]; task will retry",
                    log_label,
                    idx + 1,
                    total,
                    message,
                )

    await asyncio.gather(*(_polish_chunk(idx, chunk) for idx, chunk in enumerate(chunks)))

    if errors:
        first = errors[0][:500]
        raise RuntimeError(
            f"retryable {quality_gate_name} quality gate: {len(errors)}/{total} chunks failed; first={first}"
        )

    return "\n\n".join(item.strip() for item in polished if item and item.strip())


async def _refine_by_paragraph_chunks_strict(
    text: str,
    *,
    target_chars: int = 1000,
    system_instruction: str | None = None,
    on_stage: StageCallback | None = None,
    output_dir: Path | None = None,
) -> str:
    return await _polish_by_paragraph_chunks_strict(
        text,
        target_chars=target_chars,
        system_instruction=system_instruction,
        on_stage=on_stage,
        output_dir=output_dir,
        chunk_func=refine_book_text,
        call_timeout_seconds=settings.GEMINI_REFINE_CALL_TIMEOUT,
        cache_subdir="03_refine_chunks",
        progress_stage="refine_chunk",
        log_label="Gemini refine",
        quality_gate_name="Gemini refine",
    )


def _front_matter_missing(text: str) -> bool:
    required_sections = ("最终书名", "最终简介", "目录", "导言", "正文")
    return not all(marker in text for marker in required_sections)


async def _repair_front_matter(raw_text: str, generated_markdown: str) -> str:
    repair_prompt = (
        "你现在只负责修复书稿的前置块，不要重写全文。\n"
        "必须补齐并保留以下五个部分，标题前的 @ 加工符号也必须保留：\n"
        "1. @最终书名\n"
        "2. @最终简介\n"
        "3. @目录\n"
        "4. @导言\n"
        "5. @正文\n"
        "要求：\n"
        "- 只输出修复后的完整书稿，前置块和正文都要保留\n"
        "- 导言放在目录之后、正文之前，长度 100~400 字，负责说明全书问题意识、阅读入口和核心判断，不要写成客套前言，不要和正文太相似。\n"
        "- 导言第一段要带钩子：用问题、冲突、反常识判断或现实困境把读者带入；但只搭建阅读入口，不提前展开正文完整论证\n"
        "- 导言钩子要冷静克制，不要标题党、悬疑营销、鸡汤口号或夸张情绪\n"
        "- 正文尽量沿用原稿，不要大幅改写\n"
        "- 如果前置块缺失，请根据下方原始视频文稿和当前书稿内容补齐\n"
        "- 所有书名、目录标题和正文章节标题都不要出现冒号；最后一章不要命名为“结语”\n"
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


async def _generate_comic_series_book(
    raw_text: str,
    *,
    prompts: dict[str, str],
    output_dir: Path | None,
    on_stage: StageCallback | None,
) -> str:
    from app.services.comic_series import (
        build_comic_writer_input,
        generate_comic_section_images,
        inject_comic_images,
        parse_comic_input,
        parse_comic_phrases,
        plan_comic_section_images,
        validate_comic_phrases,
    )

    preface_phrase, phrases = parse_comic_input(raw_text)
    if not phrases:
        phrases = parse_comic_phrases(raw_text)
    validate_comic_phrases(phrases)
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "00_comic_phrases.json").write_text(
            json.dumps(
                {
                    "preface_phrase": preface_phrase,
                    "phrases": phrases,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    if output_dir is None:
        raise ValueError("漫画系列需要 output_dir 以保存每一辑配图")
    images = plan_comic_section_images(
        phrases,
        output_dir,
        preface_phrase=preface_phrase,
    )
    writer_input = build_comic_writer_input(phrases, images)
    _dump_stage(output_dir, "00_comic_writer_input.md", writer_input)

    generated_markdown = _read_stage_file(output_dir, "01_writer.md")
    if generated_markdown:
        logger.info("复用已存在的 01_writer.md")
        _log_stage("1/3 comic writer (resume)", generated_markdown)
        if on_stage:
            on_stage("writer_done", {"chars": len(generated_markdown)})
    else:
        if on_stage:
            on_stage("writer_start", {})
        writer_prompt = prompts["full_book_writer"].format(raw_text=writer_input)
        generated_markdown = await _route_writer_call(writer_prompt)
        _log_stage("1/3 comic writer", generated_markdown)
        _dump_stage(output_dir, "01_writer.md", generated_markdown)
        if on_stage:
            on_stage("writer_done", {"chars": len(generated_markdown)})
    writer_chars = len(generated_markdown)

    polished_markdown = _read_stage_file(output_dir, "02_polish.md")
    if polished_markdown:
        logger.info("复用已存在的 02_polish.md")
        _log_stage("2/3 comic polish (resume)", polished_markdown, prev_chars=writer_chars)
        polish_chars = len(polished_markdown)
        if on_stage:
            on_stage("polish_done", {"chars": polish_chars})
    else:
        if on_stage:
            on_stage("polish_start", {})
        polished_markdown = await _polish_by_paragraph_chunks_strict(
            generated_markdown,
            target_chars=1000,
            system_instruction=prompts["gemini_fast_polish"],
            on_stage=on_stage,
            output_dir=output_dir,
        )
        _log_stage("2/3 comic polish", polished_markdown, prev_chars=writer_chars)
        _dump_stage(output_dir, "02_polish.md", polished_markdown)
        polish_chars = len(polished_markdown)
        if on_stage:
            on_stage("polish_done", {"chars": polish_chars})

    refined_markdown = _read_stage_file(output_dir, "03_refine.md")
    if refined_markdown:
        logger.info("复用已存在的 03_refine.md")
        _log_stage("3/3 comic refine (resume)", refined_markdown, prev_chars=polish_chars)
        if on_stage:
            on_stage("refine_done", {"chars": len(refined_markdown)})
    else:
        if on_stage:
            on_stage("refine_start", {})
        try:
            refined_markdown = await _to_thread_with_optional_timeout(
                refine_book_text,
                polished_markdown,
                system_instruction=prompts["gemini_fast_refine"],
                timeout_seconds=settings.GEMINI_REFINE_CALL_TIMEOUT,
            )
        except GeminiMaxTokensError as exc:
            logger.warning(
                "Gemini 漫画系列二润触发 MAX_TOKENS，切换为分块二润兜底: %s",
                exc,
            )
            refined_markdown = await _refine_by_paragraph_chunks_strict(
                polished_markdown,
                target_chars=1000,
                system_instruction=prompts["gemini_fast_refine"],
                on_stage=on_stage,
                output_dir=output_dir,
            )
        except Exception as exc:
            logger.error("Gemini 漫画系列二润失败，任务重试: %s", exc)
            raise RuntimeError(f"retryable Gemini comic refine quality gate: {exc}") from exc
        _log_stage("3/3 comic refine", refined_markdown, prev_chars=polish_chars)
        _dump_stage(output_dir, "03_refine.md", refined_markdown)
        if on_stage:
            on_stage("refine_done", {"chars": len(refined_markdown)})

    if on_stage:
        on_stage("comic_images_start", {"total": len(images)})

    def _image_progress(index: int, total: int) -> None:
        if on_stage:
            on_stage("comic_image", {"idx": index, "total": total})

    images = await asyncio.to_thread(
        generate_comic_section_images,
        phrases,
        output_dir,
        allow_uncertain=True,
        preface_phrase=preface_phrase,
        on_progress=_image_progress,
    )

    final_markdown = inject_comic_images(refined_markdown, images)
    final_markdown = normalize_final_markdown(final_markdown, category="comic_series")
    _dump_stage(output_dir, "04_format.md", final_markdown)
    if on_stage:
        on_stage("format_done", {"chars": len(final_markdown), "skipped_deepseek": True})
    return final_markdown


def _read_stage_file(output_dir: Path | None, filename: str) -> str | None:
    if output_dir is None:
        return None
    path = output_dir / filename
    if not path.exists():
        return None
    try:
        content = path.read_text(encoding="utf-8").strip()
        return content or None
    except Exception as exc:
        logger.warning("读取中间产物 %s 失败: %s", filename, exc)
        return None


async def _to_thread_with_optional_timeout(
    func,
    *args,
    timeout_seconds: int | float | None = None,
    **kwargs,
):
    """Run blocking SDK calls without cancelling live API work by default.

    A positive timeout is still honored for local/dev overrides, but production
    should normally rely on the SDK request timeouts and retry budget.
    """
    coro = asyncio.to_thread(func, *args, **kwargs)
    timeout = float(timeout_seconds or 0)
    if timeout > 0:
        return await asyncio.wait_for(coro, timeout=timeout)
    return await coro


_STYLE_REFERENCE_CATEGORY_COPY: dict[str, tuple[str, str]] = {
    "web_short_story": (
        "这是历史测试版专用的文风控制项，只影响叙事笔法，不改变题材 prompt、结构要求和原始素材事实边界。",
        "仍要严格遵守历史测试版当前 prompt，不要改成网文爽文、不要偏离历史叙事。",
    ),
    "social_science_test": (
        "这是社会痛点测试版专用的文风控制项，只影响论述笔法，不改变题材 prompt、结构要求和原始素材事实边界。",
        "仍要严格遵守社会痛点类当前 prompt，不要改成鸡汤、爽文或情绪煽动稿，不要偏离现实议题。",
    ),
}


def _append_style_reference_prompt(
    writer_prompt: str,
    *,
    category: str,
    style_reference: str | None,
    style_notes: str | None,
) -> str:
    style_copy = _STYLE_REFERENCE_CATEGORY_COPY.get(normalize_category(category))
    if style_copy is None:
        return writer_prompt

    reference = (style_reference or "").strip()
    notes = (style_notes or "").strip()
    if not reference and not notes:
        return writer_prompt

    intro, boundary = style_copy
    parts = [
        "\n【笔锋参考要求】",
        intro,
    ]
    if reference:
        parts.append(f"- 参考对象：{reference}")
    if notes:
        parts.append(f"- 补充要求：{notes}")
    parts.extend(
        [
            "- 请提炼参考对象的叙事节奏、镜头感、人物推进、信息密度、段落呼吸和语言锋利度。",
            "- 只能学习气质和技法，必须生成原创书稿；不要复刻、改写或搬运任何现成作品句子。",
            f"- {boundary}",
        ]
    )
    return writer_prompt.rstrip() + "\n\n" + "\n".join(parts)


async def generate_full_book(
    raw_text: str,
    category: str = "social_science",
    output_dir: Path | None = None,
    on_stage: StageCallback | None = None,
    style_reference: str | None = None,
    style_notes: str | None = None,
) -> str:
    normalized_category = normalize_category(category)
    prompts = get_prompts(normalized_category)
    full_book_writer_prompt = prompts["full_book_writer"]
    gemini_fast_polish_prompt = prompts["gemini_fast_polish"]
    gemini_fast_refine_prompt = prompts["gemini_fast_refine"]
    final_book_format_prompt = prompts["final_book_format"]

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)

    if normalized_category == "comic_series":
        return await _generate_comic_series_book(
            raw_text,
            prompts=prompts,
            output_dir=output_dir,
            on_stage=on_stage,
        )

    def _read_stage(filename: str) -> str | None:
        if output_dir is None:
            return None
        path = output_dir / filename
        if not path.exists():
            return None
        try:
            content = path.read_text(encoding="utf-8").strip()
            return content or None
        except Exception as exc:
            logger.warning("读取中间产物 %s 失败: %s", filename, exc)
            return None

    final_resume_markdown = _read_stage("04_format.md")
    if final_resume_markdown:
        logger.info("复用已存在的 04_format.md，跳过 writer/polish/refine/format")
        _log_stage("4/4 format (resume fast)", final_resume_markdown)
        if on_stage:
            on_stage("format_done", {"chars": len(final_resume_markdown)})
        return final_resume_markdown

    # ---- 第 1 层: OpenAI Writer ----
    generated_markdown = _read_stage("01_writer.md")
    if generated_markdown:
        logger.info("复用已存在的 01_writer.md")
        _log_stage("1/4 writer (resume)", generated_markdown)
        if on_stage:
            on_stage("writer_done", {"chars": len(generated_markdown)})
    else:
        if on_stage:
            on_stage("writer_start", {})
        writer_prompt = full_book_writer_prompt.format(raw_text=raw_text.strip())
        writer_prompt = _append_style_reference_prompt(
            writer_prompt,
            category=normalized_category,
            style_reference=style_reference,
            style_notes=style_notes,
        )
        generated_markdown = await _route_writer_call(writer_prompt)
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
    polished_markdown = _read_stage("02_polish.md")
    if polished_markdown:
        logger.info("复用已存在的 02_polish.md")
        _log_stage("2/4 polish (resume)", polished_markdown, prev_chars=writer_chars)
        polish_chars = len(polished_markdown)
        if on_stage:
            on_stage("polish_done", {"chars": polish_chars})
    else:
        if on_stage:
            on_stage("polish_start", {})
        polished_markdown = await _polish_by_paragraph_chunks_strict(
            generated_markdown,
            target_chars=1000,
            system_instruction=gemini_fast_polish_prompt,
            on_stage=on_stage,
            output_dir=output_dir,
        )
        _log_stage("2/4 polish", polished_markdown, prev_chars=writer_chars)
        _dump_stage(output_dir, "02_polish.md", polished_markdown)
        polish_chars = len(polished_markdown)
        if on_stage:
            on_stage("polish_done", {"chars": polish_chars})

    # ---- 第 3 层: Gemini 二润 (整篇) ----
    refined_markdown = _read_stage("03_refine.md")
    if refined_markdown:
        logger.info("复用已存在的 03_refine.md")
        _log_stage("3/4 refine (resume)", refined_markdown, prev_chars=polish_chars)
        refine_chars = len(refined_markdown)
        if on_stage:
            on_stage("refine_done", {"chars": refine_chars})
    else:
        if on_stage:
            on_stage("refine_start", {})
        try:
            refined_markdown = await _to_thread_with_optional_timeout(
                refine_book_text,
                polished_markdown,
                system_instruction=gemini_fast_refine_prompt,
                timeout_seconds=settings.GEMINI_REFINE_CALL_TIMEOUT,
            )
        except GeminiMaxTokensError as exc:
            logger.warning(
                "Gemini 二润触发 MAX_TOKENS，切换为分块二润兜底: %s",
                exc,
            )
            refined_markdown = await _refine_by_paragraph_chunks_strict(
                polished_markdown,
                target_chars=1000,
                system_instruction=gemini_fast_refine_prompt,
                on_stage=on_stage,
                output_dir=output_dir,
            )
        except Exception as exc:
            logger.error("Gemini 二润失败，回退到一润结果继续: %s", exc)
            logger.error("Gemini refine failed; task will retry instead of degrading: %s", exc)
            raise RuntimeError(f"retryable Gemini refine quality gate: {exc}") from exc
        _log_stage("3/4 refine", refined_markdown, prev_chars=polish_chars)
        _dump_stage(output_dir, "03_refine.md", refined_markdown)
        refine_chars = len(refined_markdown)
        if on_stage:
            on_stage("refine_done", {"chars": refine_chars})

    # ---- 第 4 层: DeepSeek 格式 (整篇) ----
    final_markdown = _read_stage("04_format.md")
    if final_markdown:
        logger.info("复用已存在的 04_format.md")
        _log_stage("4/4 format (resume)", final_markdown, prev_chars=refine_chars)
        if on_stage:
            on_stage("format_done", {"chars": len(final_markdown)})
    else:
        if on_stage:
            on_stage("format_start", {})
        try:
            final_markdown = await asyncio.wait_for(
                asyncio.to_thread(
                    format_book_text,
                    refined_markdown,
                    prompt_template=final_book_format_prompt,
                ),
                timeout=settings.DEEPSEEK_FORMAT_CALL_TIMEOUT,
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"DeepSeek 终稿格式化超过 {settings.DEEPSEEK_FORMAT_CALL_TIMEOUT} 秒未返回"
            ) from exc
        _log_stage("4/4 format", final_markdown, prev_chars=refine_chars)
        _dump_stage(output_dir, "04_format.md", final_markdown)
        if on_stage:
            on_stage("format_done", {"chars": len(final_markdown)})

    if _front_matter_missing(final_markdown):
        logger.warning("后处理丢失前置块，回退到修复后的主模型输出。")
        final_markdown = generated_markdown

    if _front_matter_missing(final_markdown):
        logger.warning("前置块仍缺失，保留当前输出以避免整篇回退。")

    final_markdown = normalize_final_markdown(final_markdown)
    _dump_stage(output_dir, "04_format.md", final_markdown)

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
