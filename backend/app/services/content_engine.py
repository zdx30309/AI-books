"""Content engine for direct manuscript generation."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

import httpx
from openai import OpenAI

from app.core.config import settings
from app.prompts.goal_driven_templates import (
    INTRO_BODY_SEPARATION_PROMPT,
    SECTION_TITLE_REQUIREMENT_PROMPT,
    get_prompts,
    normalize_category,
)
from app.services.classical_chinese import build_classical_prompt_source, classical_final_title
from app.services.deepseek import DeepSeekMaxTokensError, format_book_text
from app.services.gemini import GeminiMaxTokensError, polish_book_text, refine_book_text
from app.utils.distributed_limiter import distributed_semaphore
from app.utils.official_api import official_openai_base_url, official_proxy

# 进度上报回调：(stage_name, info_dict)
# stage_name 取值：
#   writer_start / writer_done
#   polish_start / polish_chunk / polish_done
#   refine_start / refine_done
#   format_start / format_done
StageCallback = Callable[[str, dict[str, Any]], None]

logger = logging.getLogger(__name__)

_openai_writer_client: OpenAI | None = None

FINANCE_LONGFORM_CHARS_PER_MINUTE = 280
FINANCE_LONGFORM_BASE_TARGET_WORDS = 50_000
FINANCE_LONGFORM_MIN_TARGET_WORDS = 8_000
FINANCE_LONGFORM_STANDARD_WORDS = 12_000
FINANCE_LONGFORM_SHORT_VIDEO_WORDS_PER_MINUTE = 1_750
FINANCE_LONGFORM_DENSE_SOURCE_CPM = 420
FINANCE_LONGFORM_VERY_DENSE_SOURCE_CPM = 560
FINANCE_LONGFORM_DENSE_TARGET_MULTIPLIER = 1.10
FINANCE_LONGFORM_VERY_DENSE_TARGET_MULTIPLIER = 1.20
FINANCE_LONGFORM_STAGE_MIN_RETAIN_RATIO = 0.75
FINANCE_LONGFORM_CONTINUATION_BATCH_TARGET = 15_000
FINANCE_LONGFORM_CONTINUATION_MIN_GAIN_CHARS = 1_500
FINANCE_LONGFORM_NO_RETRY_ERROR_PREFIX = "automatic retry stopped to avoid duplicate billing"


_FRONT_SECTION_TITLES = ("最终书名", "最终简介", "三个备选书名", "三个备选简介", "目录", "导言", "文前", "正文")
_FRONT_SECTION_RE = re.compile(r"^@?\s*(最终书名|最终简介|三个备选书名|三个备选简介|目录|导言|文前|正文)\s*$")


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
    sep = "" if not prefix or prefix.endswith((" ", "\t", "@")) else " "
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


def _canonical_duplicate_text(text: str) -> str:
    return "".join(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaffA-Za-z0-9]+", text or "")).lower()


def _front_section_ranges(lines: list[str]) -> dict[str, tuple[int, int]]:
    markers: list[tuple[str, int]] = []
    for index, line in enumerate(lines):
        section = _FRONT_SECTION_RE.match(line.strip())
        if section:
            markers.append((section.group(1), index))

    ranges: dict[str, tuple[int, int]] = {}
    for pos, (section, index) in enumerate(markers):
        end = markers[pos + 1][1] if pos + 1 < len(markers) else len(lines)
        ranges[section] = (index + 1, end)
    return ranges


def _is_chapter_heading_line(line: str) -> bool:
    return bool(re.match(r"^@?\s*\d+[.．、]\s+.+$", line.strip()))


def _has_remaining_body_content(
    lines: list[str],
    *,
    start: int,
    end: int,
    removed: set[int],
) -> bool:
    for index in range(start, end):
        if index in removed:
            continue
        stripped = lines[index].strip()
        if not stripped or _is_chapter_heading_line(stripped):
            continue
        return True
    return False


def _same_duplicate_block(left: str, right: str) -> bool:
    left_key = _canonical_duplicate_text(left)
    right_key = _canonical_duplicate_text(right)
    if min(len(left_key), len(right_key)) < 24:
        return False
    if left_key == right_key:
        return True
    ratio = min(len(left_key), len(right_key)) / max(len(left_key), len(right_key))
    return ratio >= 0.92 and (left_key.startswith(right_key) or right_key.startswith(left_key))


def _remove_intro_duplicate_from_body(text: str) -> str:
    lines = text.split("\n")
    ranges = _front_section_ranges(lines)
    intro_range = ranges.get("导言")
    body_range = ranges.get("正文")
    if not intro_range or not body_range:
        return text

    intro_start, intro_end = intro_range
    body_start, body_end = body_range
    intro_lines = [
        lines[index].strip()
        for index in range(intro_start, intro_end)
        if lines[index].strip()
    ]
    intro_text = "\n".join(intro_lines)
    if len(_canonical_duplicate_text(intro_text)) < 24:
        return text

    cursor = body_start
    while cursor < body_end and not lines[cursor].strip():
        cursor += 1
    if cursor < body_end and _is_chapter_heading_line(lines[cursor]):
        cursor += 1
        while cursor < body_end and not lines[cursor].strip():
            cursor += 1

    candidate_indices: list[int] = []
    candidate_lines: list[str] = []
    while cursor < body_end:
        stripped = lines[cursor].strip()
        if _FRONT_SECTION_RE.match(stripped):
            break
        if _is_chapter_heading_line(stripped) and candidate_indices:
            break
        if stripped:
            candidate_indices.append(cursor)
            candidate_lines.append(stripped)
            candidate_text = "\n".join(candidate_lines)
            if _same_duplicate_block(candidate_text, intro_text):
                removed = set(candidate_indices)
                if _has_remaining_body_content(lines, start=body_start, end=body_end, removed=removed):
                    return "\n".join(
                        line for index, line in enumerate(lines) if index not in removed
                    )
                return text
            if len(_canonical_duplicate_text(candidate_text)) > len(_canonical_duplicate_text(intro_text)) * 1.15:
                break
        cursor += 1

    removed_prefix: list[int] = []
    body_index = 0
    for intro_line in intro_lines:
        if body_index >= len(candidate_indices):
            break
        body_line = lines[candidate_indices[body_index]].strip()
        if not _same_duplicate_block(intro_line, body_line):
            break
        removed_prefix.append(candidate_indices[body_index])
        body_index += 1

    removed = set(removed_prefix)
    if removed and _has_remaining_body_content(lines, start=body_start, end=body_end, removed=removed):
        return "\n".join(line for index, line in enumerate(lines) if index not in removed)
    return text


_SOFT_LINE_ENDINGS = "，,、；;：:"
_HARD_LINE_ENDINGS = "。."
_SENTENCE_ENDINGS = "。.!！?？；;：:」』”’）)]"
_TITLE_ENDINGS = "。.!！?？；;：:，,、」』”’）)]"
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
_EDITORIAL_COMMENT_RE = re.compile(
    r"^@?\s*(?:"
    r"这几句话|这句话|这段|这一段|这里|这部分|"
    r"逻辑自洽|普通人.*出口|可以量化.*解决|读者可以|"
    r"AI习惯|AI式|审稿|批注|修改建议|改成|建议改"
    r")"
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


def _is_body_chapter_heading(line: str) -> bool:
    return bool(re.match(r"^@\s*\d+[.．、]\s*.+$", line.strip()))


def _normalize_body_chapter_heading(line: str) -> str | None:
    match = re.match(r"^@\s*(\d+)[.．、]\s*(.+?)\s*$", line.strip())
    if not match:
        return None
    title = _strip_title_colons(match.group(2).strip().strip("*`# "))
    return f"@{match.group(1)}. {title or f'第{match.group(1)}章'}"


def _is_image_markdown_line(line: str) -> bool:
    return bool(re.match(r"^!\[[^\]]*\]\([^)]+\)\s*$", line.strip()))


def _cjk_ratio(text: str) -> float:
    stripped = text.strip()
    if not stripped:
        return 0.0
    cjk = sum(1 for char in stripped if "\u3400" <= char <= "\u9fff")
    return cjk / max(len(stripped), 1)


def _strip_inline_markdown_marks(line: str) -> str:
    cleaned = line.replace("**", "").replace("__", "").replace("`", "")
    cleaned = re.sub(r"^\s{0,3}#{1,6}\s*", "", cleaned)
    return cleaned.strip()


def _looks_like_editorial_comment(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("@"):
        return bool(_EDITORIAL_COMMENT_RE.search(stripped))
    return bool(re.search(r"逻辑自洽|审稿|批注|修改建议|建议改|AI习惯|AI式", stripped))


def _looks_like_pseudo_heading_text(text: str) -> bool:
    stripped = _strip_inline_markdown_marks(text).strip()
    if not stripped:
        return False
    if len(stripped) > 16:
        return False
    if _cjk_ratio(stripped) < 0.55:
        return False
    if re.search(r"[。！？；，、,.!?;：:]", stripped):
        return False
    if stripped.startswith(_CONTINUATION_PREFIXES):
        return False
    if stripped.endswith(("的", "和", "与", "及", "或", "把", "被", "让", "在", "对", "给")):
        return False
    return True


def _looks_like_bold_prefix_heading(title: str, rest: str) -> bool:
    clean_title = _strip_inline_markdown_marks(title)
    clean_rest = rest.strip()
    if not clean_rest or not _looks_like_pseudo_heading_text(clean_title):
        return False
    if clean_rest.startswith(tuple(_TITLE_ENDINGS)):
        return False
    return len(clean_title) <= 16 and len(clean_rest) >= 8


def _line_repeats_heading(heading: str, line: str) -> bool:
    heading_key = _canonical_duplicate_text(heading)
    line_key = _canonical_duplicate_text(line)[:80]
    if len(heading_key) < 4 or not line_key:
        return False
    if line_key.startswith(heading_key):
        return True
    return len(heading_key) >= 6 and line_key.startswith(heading_key[:4])


def _strip_body_noise_line(line: str) -> str | None:
    stripped = line.strip()
    if not stripped:
        return None
    if _is_image_markdown_line(stripped):
        return stripped

    chapter_heading = _normalize_body_chapter_heading(stripped)
    if chapter_heading is not None:
        return chapter_heading

    if _looks_like_editorial_comment(stripped):
        return None

    if stripped.startswith("@"):
        stripped = stripped.lstrip("@").strip()
        if _looks_like_editorial_comment(stripped):
            return None

    stripped = re.sub(r"^\s{0,3}#{1,6}\s*", "", stripped).strip()

    bold_prefix = re.match(r"^\*\*([^*\n]{1,24})\*\*\s*(.*?)\s*$", stripped)
    if bold_prefix:
        title = bold_prefix.group(1).strip()
        rest = bold_prefix.group(2).strip()
        if _looks_like_bold_prefix_heading(title, rest):
            stripped = rest
        elif rest:
            stripped = f"{title}{rest}"
        else:
            stripped = title

    numbered = re.match(r"^(?:\d+|[一二三四五六七八九十百]+)[.．、]\s*(.+?)\s*$", stripped)
    if numbered:
        stripped = numbered.group(1).strip()

    stripped = _strip_inline_markdown_marks(stripped)
    if _looks_like_editorial_comment(stripped):
        return None
    return stripped or None


def _remove_body_format_noise(lines: list[str]) -> list[str]:
    cleaned: list[str] = []
    pending = [_strip_body_noise_line(line) for line in lines]
    for index, line in enumerate(pending):
        if line is None:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if _is_image_markdown_line(stripped) or _is_body_chapter_heading(stripped):
            cleaned.append(stripped)
            continue

        if _looks_like_pseudo_heading_text(stripped):
            next_line = ""
            for candidate in pending[index + 1 :]:
                if candidate:
                    next_line = candidate.strip()
                    break
            if next_line and _line_repeats_heading(stripped, next_line):
                continue
            if not stripped.endswith(tuple(_SENTENCE_ENDINGS)):
                stripped = f"{stripped}。"
        cleaned.append(stripped)
    return cleaned


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


_CLASSICAL_BODY_PUNCT_TRANSLATION = str.maketrans(
    {
        ",": "，",
        ".": "。",
        ":": "：",
        ";": "；",
        "?": "？",
        "!": "！",
        "(": "（",
        ")": "）",
        "[": "【",
        "]": "】",
        "<": "《",
        ">": "》",
    }
)
_CLASSICAL_CHAPTER_HEADING_RE = re.compile(r"^(@?\s*\d+[.．、]\s*)(.+?)\s*$")
_CLASSICAL_PUNCT_ONLY_RE = re.compile(
    r"^[\s,，.。:：;；?？!！\"“”'‘’`·、（）()【】\[\]《》<>]+$"
)


def _convert_ascii_quotes_to_chinese(text: str) -> str:
    pieces: list[str] = []
    double_open = True
    single_open = True
    for char in text:
        if char == '"':
            pieces.append("“" if double_open else "”")
            double_open = not double_open
        elif char == "'":
            pieces.append("‘" if single_open else "’")
            single_open = not single_open
        else:
            pieces.append(char)
    return "".join(pieces)


def _clean_classical_body_text(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    cleaned = cleaned.replace("...", "……").replace("..", "……")
    cleaned = cleaned.translate(_CLASSICAL_BODY_PUNCT_TRANSLATION)
    cleaned = _convert_ascii_quotes_to_chinese(cleaned)
    cleaned = re.sub(r"[ \t\u00a0\u3000]+", "", cleaned)
    cleaned = re.sub(r"([“‘])\s+", r"\1", cleaned)
    cleaned = re.sub(r"\s+([”’])", r"\1", cleaned)
    return cleaned.strip()


def _clean_classical_body_line(raw_line: str) -> str:
    stripped = (raw_line or "").strip()
    if not stripped:
        return ""
    chapter = _CLASSICAL_CHAPTER_HEADING_RE.match(stripped)
    if chapter:
        prefix = re.sub(r"\s+", "", chapter.group(1)).replace("．", ".").replace("、", ".")
        title = _clean_classical_body_text(chapter.group(2))
        return f"{prefix} {title}".rstrip() if title else prefix
    cleaned = _clean_classical_body_text(stripped)
    if not cleaned or _CLASSICAL_PUNCT_ONLY_RE.fullmatch(cleaned):
        return ""
    return cleaned


def _clean_classical_body_lines(body: str) -> list[str]:
    cleaned: list[str] = []
    for raw_line in body.split("\n"):
        line = _clean_classical_body_line(raw_line)
        if line:
            cleaned.append(line)
    return cleaned


def _classical_cjk_signature(text: str) -> str:
    return "".join(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", text or ""))


def _classical_format_looks_truncated(source: str, formatted: str) -> bool:
    if not source or not formatted or len(source) < 5000:
        return False
    if len(formatted) < max(1000, int(len(source) * 0.65)):
        return True
    source_tail = _classical_cjk_signature(source)[-40:]
    if len(source_tail) >= 20 and source_tail not in _classical_cjk_signature(formatted):
        return True
    return False


def _classical_format_fallback_text(source: str, partial: str | None = None) -> str:
    partial_text = (partial or "").strip()
    if partial_text and not _front_matter_missing(partial_text) and not _classical_format_looks_truncated(source, partial_text):
        return partial_text
    logger.warning("古文白话终稿格式层疑似触达长度上限，改用 Gemini 二润完整稿进入确定性终稿清洗，避免后半截丢失")
    return source


def normalize_final_markdown(markdown: str, *, category: str | None = None) -> str:
    """Remove blank spacer lines only from the body, preserving front matter layout."""
    text = _postprocess_delivery_markdown(markdown).replace("\r\n", "\n").replace("\r", "\n")
    text = _repair_blank_catalog_titles(text)
    text = _remove_intro_duplicate_from_body(text)
    normalized = normalize_category(category or "")
    match = re.search(r"(?m)^@?\s*正文\s*$", text)
    if not match:
        lines = [line.rstrip() for line in text.split("\n")]
        compact = [line for line in lines if line.strip()]
        return "\n".join(compact).strip() + ("\n" if compact else "")

    front = text[: match.end()]
    body = text[match.end() :]
    front_lines = [line.rstrip() for line in front.split("\n")]
    if normalized == "classical_chinese":
        body_lines = _clean_classical_body_lines(body)
        result = "\n".join(front_lines + body_lines).strip()
        return result + ("\n" if result else "")
    body_lines = [line.rstrip() for line in body.split("\n") if line.strip()]
    if normalized in {"comic_series", "healing_comic"}:
        result = "\n".join(front_lines + body_lines).strip()
        return result + ("\n" if result else "")
    is_wisdom_story = normalized == "wisdom_stories"
    if is_wisdom_story:
        body_lines = _split_wisdom_story_body_subheadings(body_lines)
    body_lines = _remove_body_format_noise(body_lines)
    body_lines = _join_broken_body_lines(
        body_lines,
        preserve_short_subheadings=False,
    )
    result_lines = front_lines + body_lines
    result = "\n".join(result_lines).strip()
    return result + ("\n" if result else "")


def enforce_classical_title(markdown: str, original_title: object) -> str:
    final_title = classical_final_title(original_title)
    title_re = re.compile(
        r"^@?\s*(最终书名|三个备选书名)\s*$[\s\S]*?(?=^@?\s*(?:"
        + "|".join(map(re.escape, _FRONT_SECTION_TITLES))
        + r")\s*$|\Z)",
        re.MULTILINE,
    )
    match = title_re.search(markdown or "")
    if match:
        return (
            (markdown or "")[: match.start()]
            + f"@最终书名\n{final_title}\n"
            + (markdown or "")[match.end() :]
        )
    return f"@最终书名\n{final_title}\n{markdown or ''}"


def _resolve_openai_api_key() -> str | None:
    return (settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY") or "").strip() or None


def _get_openai_writer_client() -> OpenAI:
    global _openai_writer_client
    if _openai_writer_client is not None:
        return _openai_writer_client

    request_timeout = httpx.Timeout(1800.0, connect=30.0)
    client_kwargs: dict[str, Any] = {
        "base_url": official_openai_base_url(settings.OPENAI_BASE_URL),
        "timeout": request_timeout,
    }
    proxy = official_proxy("openai", settings.OPENAI_HTTPS_PROXY)
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
    proxy = official_proxy("openai", settings.OPENAI_HTTPS_PROXY)
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
            "请确认当前 OPENAI_HTTPS_PROXY 可访问 OpenAI 官方地址；"
            "若 OFFICIAL_API_DIRECT_ONLY=True，代理配置会被忽略。"
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


def _positive_max_tokens(value: int | None) -> int | None:
    if value is None:
        return None
    parsed = int(value)
    return parsed if parsed > 0 else None


def _stream_openai_writer(
    rendered_prompt: str,
    *,
    effort: str,
    verb: str,
    max_tok: int | None,
    on_progress: Callable[[int], None] | None = None,
) -> tuple[str, Any | None]:
    client = _get_openai_writer_client()
    kwargs: dict[str, Any] = {
        "model": settings.OPENAI_WRITER_MODEL,
        "input": rendered_prompt,
        "reasoning": {"effort": effort},
        "text": {"verbosity": verb},
        "stream": True,
    }
    if max_tok is not None:
        kwargs["max_output_tokens"] = max_tok
    stream = client.responses.create(**kwargs)
    chunks: list[str] = []
    final_response: Any | None = None
    generated_chars = 0
    last_progress_chars = 0
    last_progress_at = time.monotonic()
    for event in stream:
        if (getattr(event, "type", "") or "").endswith("output_text.delta"):
            delta = getattr(event, "delta", None)
            if delta:
                print(delta, end="", flush=True)
                chunks.append(delta)
                generated_chars += len(delta)
                if on_progress is not None:
                    now = time.monotonic()
                    if generated_chars - last_progress_chars >= 2000 or now - last_progress_at >= 15:
                        on_progress(generated_chars)
                        last_progress_chars = generated_chars
                        last_progress_at = now
        elif (getattr(event, "type", "") or "").endswith("response.completed"):
            final_response = getattr(event, "response", None) or final_response
    if on_progress is not None and generated_chars:
        on_progress(generated_chars)
    print()
    return "".join(chunks).strip(), final_response


def _create_openai_writer_response(
    rendered_prompt: str,
    *,
    effort: str,
    verb: str,
    max_tok: int | None,
) -> Any:
    client = _get_openai_writer_client()
    kwargs: dict[str, Any] = {
        "model": settings.OPENAI_WRITER_MODEL,
        "input": rendered_prompt,
        "reasoning": {"effort": effort},
        "text": {"verbosity": verb},
    }
    if max_tok is not None:
        kwargs["max_output_tokens"] = max_tok
    return client.responses.create(**kwargs)


def _openai_writer_call(
    rendered_prompt: str,
    *,
    reasoning_effort: str | None = None,
    verbosity: str | None = None,
    max_output_tokens: int | None = None,
    on_progress: Callable[[int], None] | None = None,
    retry_on_failure: bool = True,
) -> str:
    effort = reasoning_effort or settings.OPENAI_WRITER_REASONING_EFFORT
    verb = verbosity or settings.OPENAI_WRITER_VERBOSITY
    raw_max_tok = settings.OPENAI_WRITER_MAX_OUTPUT_TOKENS if max_output_tokens is None else max_output_tokens
    max_tok = _positive_max_tokens(raw_max_tok)

    response: Any | None = None
    text = ""
    first_stream_exc: Exception | None = None
    attempts = max(1, int(settings.OPENAI_WRITER_MAX_RETRIES or 1)) if retry_on_failure else 1
    backoff_seconds = max(0.0, float(settings.OPENAI_WRITER_RETRY_BACKOFF_SECONDS or 0.0))

    for attempt in range(1, attempts + 1):
        try:
            with distributed_semaphore(
                "openai-writer",
                limit=settings.OPENAI_WRITER_MAX_CONCURRENT_CALLS,
            ):
                if settings.OPENAI_WRITER_STREAM:
                    try:
                        text, response = _stream_openai_writer(
                            rendered_prompt,
                            effort=effort,
                            verb=verb,
                            max_tok=max_tok,
                            on_progress=on_progress,
                        )
                    except Exception as stream_exc:
                        if not retry_on_failure:
                            raise
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
        if retry_on_failure:
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
    on_progress: Callable[[int], None] | None = None,
    retry_on_failure: bool = True,
) -> str:
    return await asyncio.to_thread(
        _openai_writer_call,
        rendered_prompt,
        reasoning_effort=reasoning_effort,
        verbosity=verbosity,
        max_output_tokens=max_output_tokens,
        on_progress=on_progress,
        retry_on_failure=retry_on_failure,
    )


def _effective_gemini_chunk_timeout(
    configured_timeout: int | float | None,
    request_timeout: int | float | None,
) -> float:
    configured = float(configured_timeout or 0)
    if configured > 0:
        return configured
    request_limit = float(request_timeout or settings.GEMINI_REQUEST_TIMEOUT or 180)
    # Keep chunk calls bounded even when production config sets the outer
    # timeout to 0. This is a wait/retry boundary, not a text-length cap.
    return max(60.0, min(request_limit, 120.0))


def _classical_gemini_chunk_request_timeout(
    request_timeout: int | float | None,
) -> int:
    return int(_effective_gemini_chunk_timeout(0, request_timeout))


def _polish_book_text_without_output_cap(text: str, *, system_instruction: str | None = None) -> str:
    return polish_book_text(
        text,
        system_instruction=system_instruction,
        disable_max_output_tokens=True,
        request_timeout_seconds=_classical_gemini_chunk_request_timeout(
            settings.GEMINI_REQUEST_TIMEOUT
        ),
        retries=1,
    )


def _refine_book_text_without_output_cap(text: str, *, system_instruction: str | None = None) -> str:
    return refine_book_text(
        text,
        system_instruction=system_instruction,
        disable_max_output_tokens=True,
        request_timeout_seconds=_classical_gemini_chunk_request_timeout(
            settings.GEMINI_REFINE_REQUEST_TIMEOUT
        ),
        retries=1,
    )


def _split_long_text_by_sentence(text: str, *, target_chars: int) -> list[str]:
    compact = text.strip()
    if not compact:
        return []

    hard_target = max(300, int(target_chars or 1000))
    if len(compact) <= hard_target:
        return [compact]

    parts = re.split(r"([。！？!?；;])", compact)
    sentences: list[str] = []
    for pos in range(0, len(parts), 2):
        sentence = parts[pos].strip()
        if not sentence:
            continue
        if pos + 1 < len(parts):
            sentence += parts[pos + 1]
        sentences.append(sentence)

    if len(sentences) <= 1:
        return [
            compact[start : start + hard_target]
            for start in range(0, len(compact), hard_target)
        ]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for sentence in sentences:
        sentence_len = len(sentence)
        if sentence_len > hard_target:
            if current:
                chunks.append("".join(current))
                current = []
                current_len = 0
            chunks.extend(
                sentence[start : start + hard_target]
                for start in range(0, sentence_len, hard_target)
            )
            continue
        if current and current_len + sentence_len > hard_target:
            chunks.append("".join(current))
            current = [sentence]
            current_len = sentence_len
        else:
            current.append(sentence)
            current_len += sentence_len

    if current:
        chunks.append("".join(current))
    return [chunk for chunk in chunks if chunk.strip()]


def _split_text_by_paragraph(text: str, *, target_chars: int = 1000) -> list[str]:
    """Split text into model-sized chunks without treating huge paragraphs as one block."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        pieces = (
            _split_long_text_by_sentence(para, target_chars=target_chars)
            if len(para) > target_chars
            else [para]
        )
        for piece in pieces:
            piece_len = len(piece)
            if current and current_len + piece_len > target_chars:
                chunks.append("\n\n".join(current))
                current = []
                current_len = 0
            current.append(piece)
            current_len += piece_len
            if current_len >= target_chars:
                chunks.append("\n\n".join(current))
                current = []
                current_len = 0

    if current:
        chunks.append("\n\n".join(current))

    return [chunk for chunk in chunks if chunk.strip()]


def _split_text_for_max_tokens_retry(text: str, *, target_chars: int) -> list[str]:
    retry_target = max(300, min(700, target_chars // 2 if target_chars > 0 else 500))
    return _split_text_by_paragraph(text, target_chars=retry_target)


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
    max_tokens_retry_depth: int = 3,
    allow_chunk_fallback: bool = False,
    chunk_failure_bypass_threshold: int = 2,
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
    fallback_count = 0
    completed_count = 0
    concurrency = max(1, min(total, int(settings.GEMINI_POLISH_CONCURRENCY or 1)))
    semaphore = asyncio.Semaphore(concurrency)
    progress_lock = asyncio.Lock()
    error_lock = asyncio.Lock()
    abort_event = asyncio.Event()
    bypass_remaining_event = asyncio.Event()
    cache_dir = output_dir / cache_subdir if output_dir is not None else None
    call = chunk_func or polish_book_text
    configured_timeout = (
        settings.GEMINI_POLISH_CALL_TIMEOUT
        if call_timeout_seconds is None
        else call_timeout_seconds
    )
    timeout = _effective_gemini_chunk_timeout(
        configured_timeout,
        settings.GEMINI_REQUEST_TIMEOUT
        if progress_stage == "polish_chunk"
        else settings.GEMINI_REFINE_REQUEST_TIMEOUT,
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

    async def _polish_with_max_tokens_retry(
        chunk: str,
        *,
        idx: int,
        depth: int = 0,
        current_target_chars: int = target_chars,
    ) -> str:
        try:
            return await _polish_once(chunk)
        except GeminiMaxTokensError:
            if depth >= max_tokens_retry_depth:
                raise
            retry_chunks = _split_text_for_max_tokens_retry(
                chunk,
                target_chars=current_target_chars,
            )
            if len(retry_chunks) <= 1:
                raise

            retry_target = max(
                300,
                min(700, current_target_chars // 2 if current_target_chars > 0 else 500),
            )
            sub_concurrency = max(1, min(len(retry_chunks), concurrency))
            logger.warning(
                "%s chunk %d/%d hit MAX_TOKENS; retrying as %d subchunks depth=%d concurrency=%d",
                log_label,
                idx + 1,
                total,
                len(retry_chunks),
                depth + 1,
                sub_concurrency,
            )
            sub_semaphore = asyncio.Semaphore(sub_concurrency)

            async def _retry_subchunk(sub_idx: int, subchunk: str) -> str:
                async with sub_semaphore:
                    logger.info(
                        "%s chunk %d/%d MAX_TOKENS subchunk %d/%d (%d chars)",
                        log_label,
                        idx + 1,
                        total,
                        sub_idx + 1,
                        len(retry_chunks),
                        len(subchunk),
                    )
                    return await _polish_with_max_tokens_retry(
                        subchunk,
                        idx=idx,
                        depth=depth + 1,
                        current_target_chars=retry_target,
                    )

            sub_results = await asyncio.gather(
                *(
                    _retry_subchunk(sub_idx, subchunk)
                    for sub_idx, subchunk in enumerate(retry_chunks)
                )
            )
            return "\n\n".join(
                item.strip()
                for item in sub_results
                if item and item.strip()
            )

    async def _mark_started(chunk_idx: int, chunk: str) -> None:
        if not on_stage:
            return
        async with progress_lock:
            on_stage(
                progress_stage,
                {
                    "idx": chunk_idx,
                    "total": total,
                    "chars": len(chunk),
                    "chunk_idx": chunk_idx,
                    "phase": "started",
                },
            )

    async def _mark_completed(chunk_idx: int, chunk: str, *, phase: str = "completed") -> None:
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
                    "phase": phase,
                },
            )

    async def _fallback_chunk(idx: int, chunk: str, *, message: str) -> None:
        nonlocal fallback_count
        polished[idx] = chunk
        async with error_lock:
            fallback_count += 1
            if fallback_count >= max(1, int(chunk_failure_bypass_threshold or 1)):
                bypass_remaining_event.set()
        logger.error(
            "%s chunk %d/%d failed [%s]; using original chunk and continuing",
            log_label,
            idx + 1,
            total,
            message,
        )
        await _mark_completed(idx + 1, chunk, phase="fallback")

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
            if bypass_remaining_event.is_set():
                logger.warning(
                    "%s chunk %d/%d bypassed after repeated failures",
                    log_label,
                    idx + 1,
                    total,
                )
                await _fallback_chunk(idx, chunk, message="bypassed after repeated failures")
                return

            logger.info(
                "%s chunk %d/%d (%d chars) concurrency=%d",
                log_label,
                idx + 1,
                total,
                len(chunk),
                concurrency,
            )
            await _mark_started(idx + 1, chunk)
            try:
                result = await _polish_with_max_tokens_retry(chunk, idx=idx)
                polished[idx] = result
                _write_cache(idx, chunk, result)
                await _mark_completed(idx + 1, result)
            except GeminiMaxTokensError as exc:
                message = f"{exc.__class__.__name__}: {exc}"
                if allow_chunk_fallback:
                    await _fallback_chunk(idx, chunk, message=message)
                    return
                async with error_lock:
                    errors.append(f"chunk {idx + 1}/{total}: {message}")
                    abort_event.set()
                logger.error(
                    "%s chunk %d/%d failed after MAX_TOKENS retry [%s]; task will retry",
                    log_label,
                    idx + 1,
                    total,
                    message,
                )
            except asyncio.TimeoutError as exc:
                message = f"TimeoutError: {exc}"
                if allow_chunk_fallback:
                    await _fallback_chunk(idx, chunk, message=message)
                    return
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
                if allow_chunk_fallback:
                    await _fallback_chunk(idx, chunk, message=message)
                    return
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

    if fallback_count:
        logger.warning(
            "%s completed with %d/%d fallback chunks",
            log_label,
            fallback_count,
            total,
        )

    return "\n\n".join(item.strip() for item in polished if item and item.strip())


async def _refine_by_paragraph_chunks_strict(
    text: str,
    *,
    target_chars: int = 1000,
    system_instruction: str | None = None,
    on_stage: StageCallback | None = None,
    output_dir: Path | None = None,
    chunk_func: Callable[..., str] | None = None,
    allow_chunk_fallback: bool = False,
    chunk_failure_bypass_threshold: int = 2,
) -> str:
    return await _polish_by_paragraph_chunks_strict(
        text,
        target_chars=target_chars,
        system_instruction=system_instruction,
        on_stage=on_stage,
        output_dir=output_dir,
        chunk_func=chunk_func or refine_book_text,
        call_timeout_seconds=settings.GEMINI_REFINE_CALL_TIMEOUT,
        cache_subdir="03_refine_chunks",
        progress_stage="refine_chunk",
        log_label="Gemini refine",
        quality_gate_name="Gemini refine",
        allow_chunk_fallback=allow_chunk_fallback,
        chunk_failure_bypass_threshold=chunk_failure_bypass_threshold,
    )


def _finance_duration_seconds_from_text(raw_text: str) -> int:
    chars = max(0, len((raw_text or "").strip()))
    minutes = max(1.0, chars / max(FINANCE_LONGFORM_CHARS_PER_MINUTE, 1))
    return int(round(minutes * 60))


def _finance_content_density_multiplier(raw_text: str, minutes: float) -> float:
    if minutes <= 0:
        return 1.0
    source_chars_per_minute = len((raw_text or "").strip()) / minutes
    if source_chars_per_minute >= FINANCE_LONGFORM_VERY_DENSE_SOURCE_CPM:
        return FINANCE_LONGFORM_VERY_DENSE_TARGET_MULTIPLIER
    if source_chars_per_minute >= FINANCE_LONGFORM_DENSE_SOURCE_CPM:
        return FINANCE_LONGFORM_DENSE_TARGET_MULTIPLIER
    return 1.0


def _finance_dynamic_target_words(
    raw_text: str,
    *,
    source_duration_seconds: int | float | None = None,
) -> int:
    seconds = 0.0
    if source_duration_seconds not in (None, ""):
        try:
            seconds = float(source_duration_seconds or 0)
        except (TypeError, ValueError):
            seconds = 0.0
    if seconds <= 0:
        seconds = float(_finance_duration_seconds_from_text(raw_text))

    minutes = max(seconds / 60.0, 1.0)
    if minutes < 30:
        base_target = min(
            FINANCE_LONGFORM_BASE_TARGET_WORDS,
            round(minutes * FINANCE_LONGFORM_SHORT_VIDEO_WORDS_PER_MINUTE),
        )
    elif minutes <= 60:
        base_target = FINANCE_LONGFORM_BASE_TARGET_WORDS
    else:
        base_target = max(
            FINANCE_LONGFORM_BASE_TARGET_WORDS,
            int(round(minutes / 60.0 * FINANCE_LONGFORM_BASE_TARGET_WORDS)),
        )
    target = int(round(base_target * _finance_content_density_multiplier(raw_text, minutes)))
    return max(FINANCE_LONGFORM_MIN_TARGET_WORDS, target)


def _should_use_finance_longform(
    category: str,
    raw_text: str,
    *,
    source_duration_seconds: int | float | None = None,
) -> bool:
    if normalize_category(category) != "finance_technology":
        return False
    return (
        _finance_dynamic_target_words(
            raw_text,
            source_duration_seconds=source_duration_seconds,
        )
        > FINANCE_LONGFORM_STANDARD_WORDS
    )


def _finance_section_content(text: str, section: str) -> str:
    pattern = re.compile(
        r"^@?\s*"
        + re.escape(section)
        + r"\s*$([\s\S]*?)(?=^@?\s*(?:最终书名|最终简介|目录|导言|正文)\s*$|\Z)",
        re.MULTILINE,
    )
    match = pattern.search(text or "")
    return match.group(1).strip() if match else ""


def _finance_body_text(markdown: str) -> str:
    return _finance_section_content(markdown, "正文") or (markdown or "")


def _finance_body_chars(markdown: str) -> int:
    return len(_finance_body_text(markdown).strip())


def _finance_source_focus_points(raw_text: str, *, max_points: int = 40) -> str:
    chunks = _split_long_text_by_sentence(raw_text or "", target_chars=260)
    points = [chunk.strip() for chunk in chunks if chunk.strip()]
    if not points:
        return ""
    selected = points[: max(1, max_points)]
    return "\n".join(f"- {item}" for item in selected)


def _finance_whole_book_prompt(
    base_writer_prompt: str,
    raw_text: str,
    *,
    target_words: int,
    duration_seconds: int | None,
) -> str:
    minutes = round((duration_seconds or _finance_duration_seconds_from_text(raw_text)) / 60, 1)
    source_cpm = int(round(len((raw_text or "").strip()) / max(minutes, 1.0)))
    focus_points = _finance_source_focus_points(raw_text)
    focus_block = f"\n[视频信息点观察清单]\n{focus_points}\n" if focus_points else ""
    return f"""{base_writer_prompt.rstrip()}

【财经科技长视频动态长文强约束】
- 以上财经科技分类 prompt 全部继续生效；本段只增加长视频篇幅、质量和链路约束，不替换原分类规则。
- 必须一次性输出完整书稿，不要先写蓝图，不要分章分批生成，不要把正文留给后续补写。
- 这里的“一次性”指模型调用链路一次性整稿生成；成稿内部仍然必须按 @目录 和 @正文 组织章节。
- 视频时长约 {minutes} 分钟，文稿信息密度约每分钟 {source_cpm} 字，建议正文字数目标约 {target_words} 字；这是写作目标和篇幅引导，不是后处理硬截断，最终以视频内容密度、逻辑完整和不灌水为准。
- 如果视频时长约 20 分钟，正文尽量写到 35000 字以上；如果视频时长 30 分钟以上，正文尽量接近或超过 50000 字；超过 60 分钟继续按内容量上探，不设硬封顶。
- 如果服务端实际输出能力限制了篇幅，优先保证完整结构、核心信息点覆盖、论证密度和语言质量，不要回头重写已完成内容，不要为了达标堆砌无关信息。
- 必须按视频信息点展开，围绕原视频里的事实、数字、概念、事件、公司动作、产业变化、政策条件和市场语境组织。
- 禁止重复观点，禁止为了凑字数堆无关信息，禁止把同一判断换说法，禁止空泛铺陈和概念堆砌。
- 扩写只能来自内容价值：背景解释、机制拆解、影响链条、利益相关方、条件推演、现实误判、风险边界和普通人会受到的具体影响。
- 不做具体投资预测，不写涨跌结论，不给买卖建议；所有判断只能写成条件、机制、约束和可能影响。
- 输出顺序仍然只能是 @最终书名、@最终简介、@目录、@导言、@正文；只输出成稿，不要解释，不要代码块，不要元信息。
{focus_block}
请严格按照完整财经科技分类 prompt 和以上动态长文强约束，一次性输出完整书稿。
"""


def _finance_stage_instruction(base_instruction: str, stage_name: str) -> str:
    return f"""{base_instruction.rstrip()}

【财经科技超长文整稿处理保护】
- 当前阶段是 {stage_name}，只执行本阶段原有 workflow，不改变分工，不重写成新稿，不改成摘要。
- 你会收到上一阶段产出的完整书稿，必须从 @最终书名 处理到最后一行正文，不能只处理开头、前半部分或前 20000 字。
- 必须保留完整正文、完整章节、完整段落和全部视频信息点；不得因为篇幅长而删段、合并章节、跳过案例或压缩论证链条。
- 输出应与输入篇幅基本一致，允许必要的局部润色和格式整理，但不能把长稿压成短稿。
- 如果某些段落已经符合要求，原样保留即可，优先保证内容密度、正式书稿语感和财经科技分类规则不被破坏。
"""


def _finance_format_template(base_template: str) -> str:
    guard = """【财经科技超长文整稿格式保护】
- 你会收到上一阶段完整书稿，必须完整读取并整理全部内容，从 @最终书名 到最后一行正文都要处理。
- 本阶段只做格式整理和标题规范，不得摘要、缩写、删减、合并章节、重排论证或压缩正文。
- 输出应保留上一阶段的完整篇幅和全部视频信息点，不能只输出前半部分或前 20000 字。
"""
    template = base_template.rstrip()
    marker = "\n【文本】"
    if marker in template:
        return template.replace(marker, f"\n{guard}\n{marker}", 1)
    return f"{template}\n\n{guard}"


def _finance_body_target_reached(markdown: str, target_words: int) -> bool:
    return _finance_body_chars(markdown) >= max(1, int(target_words))


def _finance_chapter_titles_from_body(body: str) -> list[tuple[int, str]]:
    titles: list[tuple[int, str]] = []
    for line in (body or "").splitlines():
        match = re.match(r"^\s*@?\s*(\d+)\.\s*(.+?)\s*$", line)
        if not match:
            continue
        title = _strip_title_colons(match.group(2).strip())
        if title:
            titles.append((int(match.group(1)), title))
    return titles


def _finance_next_chapter_number(markdown: str) -> int:
    titles = _finance_chapter_titles_from_body(_finance_body_text(markdown))
    if not titles:
        return 1
    return max(number for number, _title in titles) + 1


def _finance_build_toc_from_body(body: str) -> str:
    titles = _finance_chapter_titles_from_body(body)
    if not titles:
        return ""
    return "\n".join(f"{idx}. {title}" for idx, title in titles)


def _finance_strip_continuation_noise(fragment: str) -> str:
    text = (fragment or "").strip()
    text = re.sub(r"^```(?:markdown|md)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```\s*$", "", text)
    body = _finance_section_content(text, "正文")
    if body:
        text = body
    lines: list[str] = []
    skip_front_content = False
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^@?\s*(最终书名|最终简介|目录|导言)\s*$", stripped):
            skip_front_content = True
            continue
        if re.match(r"^@?\s*正文\s*$", stripped):
            skip_front_content = False
            continue
        if skip_front_content:
            if re.match(r"^@?\s*(\d+)\.\s+", stripped):
                skip_front_content = False
            else:
                continue
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


def _finance_renumber_continuation_body(fragment_body: str, *, start_number: int) -> str:
    lines = (fragment_body or "").splitlines()
    next_number = max(1, start_number)
    found_heading = False
    output: list[str] = []
    heading_patterns = (
        re.compile(r"^\s*@?\s*\d+\.\s*(.+?)\s*$"),
        re.compile(r"^\s*@?\s*\d+、\s*(.+?)\s*$"),
        re.compile(r"^\s*@?\s*第[一二三四五六七八九十百千万\d]+章\s*[：:、.\s]*(.+?)\s*$"),
    )
    for line in lines:
        stripped = line.strip()
        title: str | None = None
        for pattern in heading_patterns:
            match = pattern.match(stripped)
            if match:
                title = match.group(1).strip()
                break
        if title:
            title = _strip_title_colons(title)
            output.append(f"@{next_number}. {title}")
            next_number += 1
            found_heading = True
        else:
            output.append(line.rstrip())
    body = "\n".join(output).strip()
    if not found_heading and body:
        body = f"@{start_number}. 补全现实链条\n{body}"
    return body


def _finance_rebuild_markdown_with_body(markdown: str, body: str) -> str:
    title = _finance_section_content(markdown, "最终书名")
    intro = _finance_section_content(markdown, "最终简介")
    guide = _finance_section_content(markdown, "导言")
    if not (title and intro and guide and body.strip()):
        return f"{(markdown or '').strip()}\n\n{body.strip()}".strip()
    toc = _finance_build_toc_from_body(body) or _finance_section_content(markdown, "目录")
    return (
        f"@最终书名\n{title.strip()}\n\n"
        f"@最终简介\n{intro.strip()}\n\n"
        f"@目录\n{toc.strip()}\n\n"
        f"@导言\n{guide.strip()}\n\n"
        f"@正文\n{body.strip()}"
    )


def _finance_append_continuation(markdown: str, continuation: str) -> str:
    current_body = _finance_body_text(markdown)
    fragment_body = _finance_strip_continuation_noise(continuation)
    if not fragment_body:
        return markdown
    fragment_body = _finance_renumber_continuation_body(
        fragment_body,
        start_number=_finance_next_chapter_number(markdown),
    )
    merged_body = "\n\n".join(part.strip() for part in (current_body, fragment_body) if part.strip())
    return _finance_rebuild_markdown_with_body(markdown, merged_body)


def _finance_existing_chapter_map(markdown: str) -> str:
    titles = _finance_chapter_titles_from_body(_finance_body_text(markdown))
    if not titles:
        return "当前正文没有稳定章节标题。"
    return "\n".join(f"- {number}. {title}" for number, title in titles[-20:])


def _finance_continuation_prompt(
    base_writer_prompt: str,
    raw_text: str,
    current_markdown: str,
    *,
    target_words: int,
    duration_seconds: int,
    round_index: int,
    batch_target: int,
) -> str:
    current_body_chars = _finance_body_chars(current_markdown)
    remaining = max(0, int(target_words) - current_body_chars)
    minutes = round(duration_seconds / 60.0, 1)
    focus_points = _finance_source_focus_points(raw_text, max_points=55)
    chapter_map = _finance_existing_chapter_map(current_markdown)
    return f"""{base_writer_prompt.rstrip()}

【财经科技长文同阶段定向续写任务】
当前仍属于 OpenAI Writer 初稿阶段，不是新 workflow，不是 Gemini 润色，不是 DeepSeek 格式层。
你会看到原视频文稿、当前已完成书稿和已有章节目录。请只输出“新增正文正式章节”，后端会把新增章节拼回 @正文 并重建目录。

【当前篇幅状态】
- 视频时长约 {minutes} 分钟。
- 动态目标正文字数约 {target_words} 字。
- 当前正文约 {current_body_chars} 字。
- 仍需补足约 {remaining} 字。
- 本轮新增正文目标约 {batch_target} 字；如果内容密度足够，可以更多，不设人为上限。
- 当前是第 {round_index} 轮定向续写。

【全局扩写蓝图】
前文如果已经把表层事实说完，不要重复前文结论，也不要沿着最后一段空转。必须从更高一层的全局视角补足前文没有讲透的内容：
- 产业链结构：上游资源、核心零部件、云厂商、应用公司、终端用户之间的传导关系。
- 技术机制：算力、模型、芯片、能源、数据、软件生态、效率提升之间怎样互相制约。
- 公司行为：资本开支、库存、采购节奏、价格谈判、竞争策略、财务压力和商业模式。
- 政策与地缘：监管条件、产业政策、出口限制、补贴、标准制定和安全边界。
- 资金与周期：利率、融资、估值、现金流、产能周期、价格周期和市场预期如何改变判断。
- 普通人影响：工作机会、消费成本、设备价格、教育选择、投资误判、行业迁移和生活成本。
- 条件推演：如果关键条件变化，结论怎样变化；哪些判断成立，哪些判断失效。
- 风险边界：哪些说法不能写成确定预测，哪些只是机制可能性，哪些是市场常见误读。

【反重复与质量要求】
- 只补充当前书稿未充分展开的层次，不能复述已有章节，不能换说法重复同一判断。
- 每个新增章节都必须围绕原视频信息点展开，并提供新增内容价值：背景解释、机制拆解、影响链条、利益相关方、条件推演、现实误判或风险边界。
- 不能为了凑字数堆无关知识，不能写百科，不能写研究报告，不能写投资建议。
- 不做具体投资预测，不写涨跌结论，不给买卖建议。
- 语言仍然要保持财经科技分类原 prompt 的正式书稿质量，不要口语化，不要论文腔，不要总结腔。

【输出格式】
- 只输出新增的正文正式章节，不要输出 @最终书名、@最终简介、@目录、@导言、@正文。
- 每个新增章节必须以 @N. 章节名 开头，N 可从 1 开始，后端会自动重排编号。
- 章节标题不得出现中文冒号或英文冒号，不要命名为“结语”。
- 不要解释，不要代码块，不要元信息，不要输出“续写如下”。

[已有章节目录]
{chapter_map}

[视频信息点观察清单]
{focus_points}

[原始视频文稿]
{raw_text.strip()}

[当前已完成书稿]
{current_markdown.strip()}
"""


async def _finance_expand_writer_to_target(
    current_markdown: str,
    *,
    base_writer_prompt: str,
    raw_text: str,
    target_words: int,
    duration_seconds: int,
    output_dir: Path | None,
    on_stage: StageCallback | None,
) -> tuple[str, bool]:
    current_markdown = normalize_final_markdown(current_markdown, category="finance_technology")
    current_body_chars = _finance_body_chars(current_markdown)
    if _finance_body_target_reached(current_markdown, target_words):
        return current_markdown, False

    deficit = max(0, int(target_words) - current_body_chars)
    max_rounds = max(
        1,
        int(math.ceil(deficit / FINANCE_LONGFORM_CONTINUATION_BATCH_TARGET)) + 2,
    )
    expanded = False
    no_gain_rounds = 0

    for round_index in range(1, max_rounds + 1):
        if _finance_body_target_reached(current_markdown, target_words):
            break
        before_body_chars = _finance_body_chars(current_markdown)
        remaining = max(0, int(target_words) - before_body_chars)
        batch_target = max(
            8_000,
            min(FINANCE_LONGFORM_CONTINUATION_BATCH_TARGET, remaining + 2_000),
        )
        prompt = _finance_continuation_prompt(
            base_writer_prompt,
            raw_text,
            current_markdown,
            target_words=target_words,
            duration_seconds=duration_seconds,
            round_index=round_index,
            batch_target=batch_target,
        )
        _dump_stage(output_dir, f"01c_finance_continuation_prompt_{round_index:02d}.md", prompt)
        logger.warning(
            "财经科技初稿低于动态目标，启动同阶段定向续写 round=%d/%d body_chars=%d target=%d",
            round_index,
            max_rounds,
            before_body_chars,
            target_words,
        )
        try:
            continuation = await _route_writer_call(
                prompt,
                max_output_tokens=0,
                retry_on_failure=False,
                on_progress=(
                    (
                        lambda chars, base=before_body_chars, round_idx=round_index: on_stage(
                            "writer_progress",
                            {"chars": base + chars, "continuation_round": round_idx},
                        )
                    )
                    if on_stage
                    else None
                ),
            )
        except Exception as exc:
            raise RuntimeError(
                f"{FINANCE_LONGFORM_NO_RETRY_ERROR_PREFIX}: "
                f"finance OpenAI continuation failed after 01_writer.md was saved: {exc}"
            ) from exc
        _dump_stage(output_dir, f"01c_finance_continuation_{round_index:02d}.md", continuation)
        current_markdown = _finance_append_continuation(current_markdown, continuation)
        current_markdown = normalize_final_markdown(current_markdown, category="finance_technology")
        after_body_chars = _finance_body_chars(current_markdown)
        gain = after_body_chars - before_body_chars
        logger.info(
            "财经科技定向续写完成 round=%d gain=%d body_chars=%d target=%d",
            round_index,
            gain,
            after_body_chars,
            target_words,
        )
        if gain >= FINANCE_LONGFORM_CONTINUATION_MIN_GAIN_CHARS:
            expanded = True
            no_gain_rounds = 0
            _dump_stage(output_dir, "01_writer.md", current_markdown)
            if on_stage:
                on_stage(
                    "writer_progress",
                    {"chars": after_body_chars, "continuation_round": round_index, "target_words": target_words},
                )
        else:
            no_gain_rounds += 1
        if no_gain_rounds >= 2:
            raise RuntimeError(
                f"{FINANCE_LONGFORM_NO_RETRY_ERROR_PREFIX}: "
                "finance OpenAI continuation did not add enough non-repetitive content; "
                f"body_chars={after_body_chars} target={target_words}"
            )

    if not _finance_body_target_reached(current_markdown, target_words):
        raise RuntimeError(
            f"{FINANCE_LONGFORM_NO_RETRY_ERROR_PREFIX}: "
            f"finance writer could not reach dynamic target without quality risk; "
            f"body_chars={_finance_body_chars(current_markdown)} target={target_words}"
        )
    return current_markdown, expanded


def _finance_stage_looks_compressed(source: str, candidate: str) -> bool:
    if not source or len(source) < 10_000:
        return False
    if not candidate or not candidate.strip():
        return True
    if _front_matter_missing(candidate):
        return True
    source_body_chars = _finance_body_chars(source)
    candidate_body_chars = _finance_body_chars(candidate)
    if source_body_chars >= 10_000 and candidate_body_chars < max(
        5000,
        int(source_body_chars * FINANCE_LONGFORM_STAGE_MIN_RETAIN_RATIO),
    ):
        return True
    return False


def _positive_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


async def _generate_finance_longform_book(
    raw_text: str,
    *,
    prompts: dict[str, str],
    output_dir: Path | None,
    on_stage: StageCallback | None,
    source_duration_seconds: int | float | None,
) -> str:
    duration_seconds = _positive_int(source_duration_seconds) or _finance_duration_seconds_from_text(raw_text)
    target_words = _finance_dynamic_target_words(
        raw_text,
        source_duration_seconds=duration_seconds,
    )
    metadata = {
        "targetWords": target_words,
        "durationSeconds": duration_seconds,
        "targetMode": "writer_stage_expand_to_dynamic_target",
        "stageMinRetainRatio": FINANCE_LONGFORM_STAGE_MIN_RETAIN_RATIO,
        "mode": "whole_workflow_with_writer_stage_continuations",
        "outputCaps": {
            "openai": "disabled",
            "gemini": "disabled",
            "deepseek": "disabled",
        },
    }
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "00_finance_longform_plan.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    final_resume_markdown = _read_stage_file(output_dir, "04_format.md")
    if final_resume_markdown:
        if _finance_body_target_reached(final_resume_markdown, target_words):
            logger.info("复用已存在的财经长文 04_format.md")
            _log_stage("finance 4/4 format (resume)", final_resume_markdown)
            if on_stage:
                on_stage("format_done", {"chars": len(final_resume_markdown)})
            return final_resume_markdown
        logger.warning(
            "已有财经长文 04_format.md 低于动态目标，忽略短终稿并回到 writer 阶段续写: body_chars=%d target=%d",
            _finance_body_chars(final_resume_markdown),
            target_words,
        )
        _dump_stage(output_dir, "04_format_stale_short.md", final_resume_markdown)

    resume_writer_markdown = _read_stage_file(output_dir, "01_writer.md")
    resume_polished_markdown = _read_stage_file(output_dir, "02_polish.md")
    resume_refined_markdown = _read_stage_file(output_dir, "03_refine.md")
    base_writer_prompt = prompts["full_book_writer"].format(raw_text=raw_text)
    if not raw_text.strip() and not (
        resume_writer_markdown or resume_polished_markdown or resume_refined_markdown
    ):
        raise ValueError("财经科技长文生成缺少原始视频文稿，且没有可续跑的 01_writer.md")

    generated_markdown = resume_writer_markdown
    if generated_markdown:
        logger.info("复用已存在的财经长文 01_writer.md")
        _log_stage("finance 1/4 writer (resume)", generated_markdown)
        if on_stage:
            on_stage("writer_done", {"chars": len(generated_markdown), "target_words": target_words})
    elif resume_polished_markdown or resume_refined_markdown:
        generated_markdown = resume_polished_markdown or resume_refined_markdown or ""
        logger.info("财经长文缺少 01_writer.md，但检测到后续阶段产物，跳过主笔续跑")
        if on_stage:
            on_stage("writer_done", {"chars": len(generated_markdown), "target_words": target_words})
    else:
        if on_stage:
            on_stage("writer_start", {"target_words": target_words, "duration_seconds": duration_seconds})
        writer_prompt = _finance_whole_book_prompt(
            base_writer_prompt,
            raw_text,
            target_words=target_words,
            duration_seconds=duration_seconds,
        )
        _dump_stage(output_dir, "01_finance_whole_prompt.md", writer_prompt)
        try:
            generated_markdown = await _route_writer_call(
                writer_prompt,
                max_output_tokens=0,
                retry_on_failure=False,
                on_progress=(
                    (lambda chars: on_stage("writer_progress", {"chars": chars}))
                    if on_stage
                    else None
                ),
            )
        except Exception as exc:
            raise RuntimeError(
                f"{FINANCE_LONGFORM_NO_RETRY_ERROR_PREFIX}: "
                f"finance OpenAI whole-book writer failed before 01_writer.md was saved: {exc}"
            ) from exc
        generated_markdown = normalize_final_markdown(generated_markdown, category="finance_technology")
        _log_stage("finance 1/4 writer", generated_markdown)
        _dump_stage(output_dir, "01_writer.md", generated_markdown)
        generated_body_chars = _finance_body_chars(generated_markdown)
        if generated_body_chars < target_words:
            logger.warning(
                "财经科技整稿低于动态篇幅目标但不触发二次整稿生成: body_chars=%d target=%d",
                generated_body_chars,
                target_words,
            )

    generated_markdown, writer_expanded = await _finance_expand_writer_to_target(
        generated_markdown,
        base_writer_prompt=base_writer_prompt,
        raw_text=raw_text,
        target_words=target_words,
        duration_seconds=duration_seconds,
        output_dir=output_dir,
        on_stage=on_stage,
    )
    if writer_expanded:
        resume_polished_markdown = None
        resume_refined_markdown = None
        _dump_stage(output_dir, "01_writer.md", generated_markdown)

    if on_stage:
        on_stage(
            "writer_done",
            {
                "chars": len(generated_markdown),
                "body_chars": _finance_body_chars(generated_markdown),
                "target_words": target_words,
                "target_mode": "expanded_to_target",
            },
        )

    writer_chars = len(generated_markdown)

    polished_markdown = resume_polished_markdown
    if polished_markdown:
        logger.info("复用已存在的财经长文 02_polish.md")
        _log_stage("finance 2/4 polish (resume)", polished_markdown, prev_chars=writer_chars)
        if on_stage:
            on_stage("polish_done", {"chars": len(polished_markdown)})
    elif resume_refined_markdown:
        polished_markdown = resume_refined_markdown
        logger.info("财经长文缺少 02_polish.md，但检测到 03_refine.md，跳过一润续跑")
        if on_stage:
            on_stage("polish_done", {"chars": len(polished_markdown)})
    else:
        if on_stage:
            on_stage("polish_start", {})
        polish_instruction = _finance_stage_instruction(prompts["gemini_fast_polish"], "Gemini 一润")
        logger.info("财经科技 Gemini 一润整稿输入: chars=%d body_chars=%d", len(generated_markdown), _finance_body_chars(generated_markdown))
        try:
            polished_markdown = await _to_thread_with_optional_timeout(
                polish_book_text,
                generated_markdown,
                system_instruction=polish_instruction,
                disable_max_output_tokens=True,
                request_timeout_seconds=max(
                    int(settings.GEMINI_REQUEST_TIMEOUT or 0),
                    int(settings.DEEPSEEK_FORMAT_CALL_TIMEOUT or 0),
                    1800,
                ),
                retries=1,
                timeout_seconds=settings.GEMINI_POLISH_CALL_TIMEOUT,
            )
        except Exception as exc:
            logger.warning(
                "财经科技 Gemini 一润失败，保留 OpenAI 完整初稿继续后续链路: %s",
                exc,
            )
            _dump_stage(output_dir, "02_polish_error.txt", f"{exc.__class__.__name__}: {exc}")
            polished_markdown = generated_markdown
        polished_markdown = normalize_final_markdown(polished_markdown, category="finance_technology")
        if _finance_stage_looks_compressed(generated_markdown, polished_markdown):
            logger.warning(
                "财经科技 Gemini 一润疑似压缩，保留 OpenAI 完整初稿: source_body=%d candidate_body=%d",
                _finance_body_chars(generated_markdown),
                _finance_body_chars(polished_markdown),
            )
            _dump_stage(output_dir, "02_polish_rejected_compressed.md", polished_markdown)
            polished_markdown = generated_markdown
        _log_stage("finance 2/4 polish", polished_markdown, prev_chars=writer_chars)
        _dump_stage(output_dir, "02_polish.md", polished_markdown)
        if on_stage:
            on_stage("polish_done", {"chars": len(polished_markdown)})

    polish_chars = len(polished_markdown)

    refined_markdown = resume_refined_markdown
    if refined_markdown:
        logger.info("复用已存在的财经长文 03_refine.md")
        _log_stage("finance 3/4 refine (resume)", refined_markdown, prev_chars=polish_chars)
        if on_stage:
            on_stage("refine_done", {"chars": len(refined_markdown)})
    else:
        if on_stage:
            on_stage("refine_start", {})
        refine_instruction = _finance_stage_instruction(prompts["gemini_fast_refine"], "Gemini 二润")
        logger.info("财经科技 Gemini 二润整稿输入: chars=%d body_chars=%d", len(polished_markdown), _finance_body_chars(polished_markdown))
        try:
            refined_markdown = await _to_thread_with_optional_timeout(
                refine_book_text,
                polished_markdown,
                system_instruction=refine_instruction,
                disable_max_output_tokens=True,
                request_timeout_seconds=max(
                    int(settings.GEMINI_REFINE_REQUEST_TIMEOUT or 0),
                    int(settings.DEEPSEEK_FORMAT_CALL_TIMEOUT or 0),
                    1800,
                ),
                retries=1,
                timeout_seconds=settings.GEMINI_REFINE_CALL_TIMEOUT,
            )
        except Exception as exc:
            logger.warning(
                "财经科技 Gemini 二润失败，保留上一阶段完整稿继续后续链路: %s",
                exc,
            )
            _dump_stage(output_dir, "03_refine_error.txt", f"{exc.__class__.__name__}: {exc}")
            refined_markdown = polished_markdown
        refined_markdown = normalize_final_markdown(refined_markdown, category="finance_technology")
        if _finance_stage_looks_compressed(polished_markdown, refined_markdown):
            logger.warning(
                "财经科技 Gemini 二润疑似压缩，保留一润完整稿: source_body=%d candidate_body=%d",
                _finance_body_chars(polished_markdown),
                _finance_body_chars(refined_markdown),
            )
            _dump_stage(output_dir, "03_refine_rejected_compressed.md", refined_markdown)
            refined_markdown = polished_markdown
        _log_stage("finance 3/4 refine", refined_markdown, prev_chars=polish_chars)
        _dump_stage(output_dir, "03_refine.md", refined_markdown)
        if on_stage:
            on_stage("refine_done", {"chars": len(refined_markdown)})

    refine_chars = len(refined_markdown)

    if on_stage:
        on_stage("format_start", {})
    format_template = _finance_format_template(prompts["final_book_format"])
    logger.info("财经科技 DeepSeek 格式层整稿输入: chars=%d body_chars=%d", len(refined_markdown), _finance_body_chars(refined_markdown))
    try:
        final_markdown = await asyncio.wait_for(
            asyncio.to_thread(
                format_book_text,
                refined_markdown,
                prompt_template=format_template,
                max_output_tokens=0,
                fail_on_max_tokens=True,
                retries=1,
                on_progress=(
                    (lambda chars: on_stage("format_progress", {"chars": chars}))
                    if on_stage
                    else None
                ),
            ),
            timeout=settings.DEEPSEEK_FORMAT_CALL_TIMEOUT,
        )
    except DeepSeekMaxTokensError as exc:
        _dump_stage(output_dir, "04_format_deepseek_partial.md", exc.partial_text)
        logger.warning(
            "DeepSeek 财经科技整稿格式化触达服务端长度上限，保留 Gemini 二润完整稿继续: partial_chars=%d",
            len(exc.partial_text or ""),
        )
        final_markdown = refined_markdown
    except asyncio.TimeoutError as exc:
        _dump_stage(output_dir, "04_format_error.txt", f"{exc.__class__.__name__}: {exc}")
        logger.warning(
            "DeepSeek 财经科技整稿格式化超过 %s 秒未返回，保留 Gemini 二润完整稿继续",
            settings.DEEPSEEK_FORMAT_CALL_TIMEOUT,
        )
        final_markdown = refined_markdown
    except Exception as exc:
        _dump_stage(output_dir, "04_format_error.txt", f"{exc.__class__.__name__}: {exc}")
        logger.warning(
            "DeepSeek 财经科技整稿格式化失败，保留 Gemini 二润完整稿继续: %s",
            exc,
        )
        final_markdown = refined_markdown

    final_markdown = normalize_final_markdown(final_markdown, category="finance_technology")
    if _finance_stage_looks_compressed(refined_markdown, final_markdown):
        logger.warning(
            "DeepSeek 财经科技整稿格式化疑似压缩或截断，保留 Gemini 二润完整稿: source_body=%d candidate_body=%d",
            _finance_body_chars(refined_markdown),
            _finance_body_chars(final_markdown),
        )
        _dump_stage(output_dir, "04_format_rejected_compressed.md", final_markdown)
        final_markdown = refined_markdown
    _log_stage("finance 4/4 format", final_markdown, prev_chars=refine_chars)
    _dump_stage(output_dir, "04_format.md", final_markdown)
    if on_stage:
        on_stage("format_done", {"chars": len(final_markdown)})
    return final_markdown


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
        f"{INTRO_BODY_SEPARATION_PROMPT}\n"
        "- 正文尽量沿用原稿，不要大幅改写\n"
        "- 如果前置块缺失，请根据下方原始视频文稿和当前书稿内容补齐\n"
        "- 所有书名、目录标题和正文章节标题都不要出现冒号；最后一章不要命名为“结语”\n"
        f"{SECTION_TITLE_REQUIREMENT_PROMPT}\n"
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
    category: str,
    prompts: dict[str, str],
    output_dir: Path | None,
    on_stage: StageCallback | None,
) -> str:
    from app.services.comic_series import (
        assemble_comic_series_markdown,
        build_comic_writer_input,
        extract_comic_title,
        generate_comic_section_images,
        inject_comic_images,
        parse_comic_input_parts,
        parse_comic_phrases,
        parse_comic_theme_requirement,
        validate_comic_phrases,
    )

    title, preface_phrase, phrases = parse_comic_input_parts(raw_text)
    theme_requirement = parse_comic_theme_requirement(
        next(
            (
                line
                for line in raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
                if line.strip().startswith(("主题要求", "动物主题", "角色要求", "主角要求", "用户要求"))
            ),
            "",
        )
    )
    if not phrases:
        phrases = parse_comic_phrases(raw_text)
    validate_comic_phrases(phrases)
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "00_comic_phrases.json").write_text(
            json.dumps(
                {
                    "title": title,
                    "theme_requirement": theme_requirement,
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
    writer_input = build_comic_writer_input(
        phrases,
        preface_phrase=preface_phrase,
        title=title,
        theme_requirement=theme_requirement,
    )
    _dump_stage(output_dir, "00_comic_writer_input.md", writer_input)

    refined_markdown = assemble_comic_series_markdown(phrases, preface_phrase=preface_phrase, title=title)
    _log_stage("comic phrase layout", refined_markdown)
    _dump_stage(output_dir, "01_writer.md", refined_markdown)
    _dump_stage(output_dir, "02_polish.md", refined_markdown)
    _dump_stage(output_dir, "03_refine.md", refined_markdown)
    if on_stage:
        on_stage("comic_structure_done", {"chars": len(refined_markdown), "skipped_text_generation": True})

    if on_stage:
        on_stage("comic_images_start", {"total": len(phrases) + 2})

    def _image_progress(index: int, total: int) -> None:
        if on_stage:
            on_stage("comic_image", {"idx": index, "total": total})

    images = await asyncio.to_thread(
        generate_comic_section_images,
        phrases,
        output_dir,
        allow_uncertain=True,
        title=title or extract_comic_title(refined_markdown) or None,
        preface_phrase=preface_phrase,
        theme_requirement=theme_requirement,
        include_healing_reference=category == "healing_comic",
        on_progress=_image_progress,
    )

    final_markdown = inject_comic_images(refined_markdown, images)
    final_markdown = normalize_final_markdown(final_markdown, category="comic_series")
    _dump_stage(output_dir, "04_format.md", final_markdown)
    if on_stage:
        on_stage("format_done", {"chars": len(final_markdown), "skipped_deepseek": True})
    return final_markdown


def _healing_comic_research_context(title: str) -> str:
    from app.services.comic_series import healing_comic_reference_context

    return healing_comic_reference_context(title)


async def _generate_healing_comic_book(
    raw_text: str,
    *,
    output_dir: Path | None,
    on_stage: StageCallback | None,
    panel_count: int = 32,
) -> str:
    from app.services.comic_series import (
        assemble_healing_comic_markdown,
        build_healing_comic_writer_prompt,
        generate_healing_comic_images,
        inject_comic_images,
        parse_healing_comic_panel_count,
        parse_healing_comic_title,
        parse_healing_comic_writer_output,
    )

    comic_title = parse_healing_comic_title(raw_text) or "今天也不用很厉害"
    panel_count = parse_healing_comic_panel_count(panel_count)
    if output_dir is None:
        raise ValueError("漫画需要 output_dir 以保存每张配图")
    output_dir.mkdir(parents=True, exist_ok=True)

    research_context = await asyncio.to_thread(_healing_comic_research_context, comic_title)
    _dump_stage(output_dir, "00_healing_comic_research.md", research_context)
    if on_stage:
        on_stage("comic_structure_done", {"chars": len(research_context), "research_done": True})

    writer_prompt = build_healing_comic_writer_prompt(comic_title, research_context, panel_count=panel_count)
    _dump_stage(output_dir, "01_writer_prompt.md", writer_prompt)
    try:
        writer_output = await _route_writer_call(
            writer_prompt,
            reasoning_effort="medium",
            verbosity="low",
            max_output_tokens=12000,
        )
    except Exception as exc:
        logger.warning("ChatGPT healing comic writer fallback: %s", exc)
        writer_output = ""
    _dump_stage(output_dir, "01_writer.md", writer_output or "(fallback)")
    title, intro, panels, series_meta = parse_healing_comic_writer_output(
        writer_output,
        comic_title,
        panel_count=panel_count,
    )
    script_json = json.dumps(
        {"title": title, "intro": intro, "series_meta": series_meta, "panels": panels},
        ensure_ascii=False,
        indent=2,
    )
    _dump_stage(output_dir, "02_healing_comic_script.json", script_json)

    refined_markdown = assemble_healing_comic_markdown(title, intro, panels)
    _dump_stage(output_dir, "03_refine.md", refined_markdown)
    if on_stage:
        on_stage("comic_images_start", {"total": len(panels) + 1})

    def _image_progress(index: int, total: int) -> None:
        if on_stage:
            on_stage("comic_image", {"idx": index, "total": total})

    images = await asyncio.to_thread(
        generate_healing_comic_images,
        panels,
        output_dir,
        series_context=f"{research_context}\n书名：{comic_title}\n统一风格：{series_meta.get('series_style') or ''}",
        title=title,
        intro=intro,
        series_meta=series_meta,
        allow_uncertain=True,
        retry_uncertain=True,
        on_progress=_image_progress,
    )
    final_markdown = inject_comic_images(refined_markdown, images)
    final_markdown = normalize_final_markdown(final_markdown, category="healing_comic")
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


REFERENCE_ARTICLE_INPUT_SOURCE = "reference_article"


def _append_reference_article_prompt(
    writer_prompt: str,
    *,
    category: str,
    input_source: str | None,
) -> str:
    if (input_source or "").strip() != REFERENCE_ARTICLE_INPUT_SOURCE:
        return writer_prompt
    if normalize_category(category) != "emotion_relationship":
        raise ValueError("参考文章生成当前只支持情感·关系分类")

    parts = [
        "【参考文章生成专项要求】",
        "- 当前输入不是视频文稿，而是一篇参考文章原文；请按情感·关系类 prompt 理解其核心关系问题、情绪处境、叙事入口、读者痛点和判断框架。",
        "- 只学习主题、问题意识和关系困境，不复刻原文标题、目录、段落顺序、句式、金句、比喻、独特案例、人物设定和可识别表达。",
        "- 必须重新选择切入场景、章节结构、案例材料和表达方式，生成一篇独立原创文章；不能改写、洗稿或同义替换原文。",
        "- 如果参考文章包含独特真实故事、个人经历或可识别细节，应抽象为更通用的关系处境，另起概括性场景，不沿用原文的关键人物关系和细节组合。",
        "- 正文字数不设人为上限，以把核心关系问题讲透为准；不要为了凑篇幅重复观点。",
    ]
    return writer_prompt.rstrip() + "\n\n" + "\n".join(parts)


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
    classical_variant: str | None = None,
    classical_original_title: str | None = None,
    healing_comic_panel_count: int | None = None,
    input_source: str | None = None,
    source_duration_seconds: int | float | None = None,
) -> str:
    normalized_category = normalize_category(category)
    if (
        (input_source or "").strip() == REFERENCE_ARTICLE_INPUT_SOURCE
        and normalized_category != "emotion_relationship"
    ):
        raise ValueError("参考文章生成当前只支持情感·关系分类")
    prompts = get_prompts(normalized_category)
    full_book_writer_prompt = prompts["full_book_writer"]
    gemini_fast_polish_prompt = prompts["gemini_fast_polish"]
    gemini_fast_refine_prompt = prompts["gemini_fast_refine"]
    final_book_format_prompt = prompts["final_book_format"]
    writer_input_text = raw_text.strip()
    if normalized_category == "classical_chinese":
        writer_input_text = build_classical_prompt_source(
            writer_input_text,
            variant=classical_variant,
            original_title=classical_original_title,
        )

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)

    if normalized_category == "healing_comic":
        return await _generate_healing_comic_book(
            raw_text,
            output_dir=output_dir,
            on_stage=on_stage,
            panel_count=healing_comic_panel_count or 32,
        )

    if normalized_category == "comic_series":
        return await _generate_comic_series_book(
            raw_text,
            category=normalized_category,
            prompts=prompts,
            output_dir=output_dir,
            on_stage=on_stage,
        )

    if _should_use_finance_longform(
        normalized_category,
        writer_input_text,
        source_duration_seconds=source_duration_seconds,
    ):
        return await _generate_finance_longform_book(
            writer_input_text,
            prompts=prompts,
            output_dir=output_dir,
            on_stage=on_stage,
            source_duration_seconds=source_duration_seconds,
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
        writer_prompt = full_book_writer_prompt.format(raw_text=writer_input_text)
        writer_prompt = _append_style_reference_prompt(
            writer_prompt,
            category=normalized_category,
            style_reference=style_reference,
            style_notes=style_notes,
        )
        writer_prompt = _append_reference_article_prompt(
            writer_prompt,
            category=normalized_category,
            input_source=input_source,
        )
        writer_max_output_tokens = 0 if normalized_category == "classical_chinese" else None
        generated_markdown = await _route_writer_call(
            writer_prompt,
            max_output_tokens=writer_max_output_tokens,
            on_progress=(
                (lambda chars: on_stage("writer_progress", {"chars": chars}))
                if on_stage
                else None
            ),
        )
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
        polish_chunk_func = (
            _polish_book_text_without_output_cap
            if normalized_category == "classical_chinese"
            else polish_book_text
        )
        polished_markdown = await _polish_by_paragraph_chunks_strict(
            generated_markdown,
            target_chars=1000,
            system_instruction=gemini_fast_polish_prompt,
            on_stage=on_stage,
            output_dir=output_dir,
            chunk_func=polish_chunk_func,
            allow_chunk_fallback=normalized_category == "classical_chinese",
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
        refine_func = (
            _refine_book_text_without_output_cap
            if normalized_category == "classical_chinese"
            else refine_book_text
        )
        if normalized_category == "classical_chinese":
            logger.info("Classical Chinese refine uses chunked path to avoid whole-book Gemini stalls")
            refined_markdown = await _refine_by_paragraph_chunks_strict(
                polished_markdown,
                target_chars=1000,
                system_instruction=gemini_fast_refine_prompt,
                on_stage=on_stage,
                output_dir=output_dir,
                chunk_func=refine_func,
                allow_chunk_fallback=True,
            )
        else:
            try:
                refined_markdown = await _to_thread_with_optional_timeout(
                    refine_func,
                    polished_markdown,
                    system_instruction=gemini_fast_refine_prompt,
                    timeout_seconds=settings.GEMINI_REFINE_CALL_TIMEOUT,
                )
            except GeminiMaxTokensError as exc:
                logger.warning("Gemini refine hit MAX_TOKENS; falling back to chunked refine: %s", exc)
                refined_markdown = await _refine_by_paragraph_chunks_strict(
                    polished_markdown,
                    target_chars=1000,
                    system_instruction=gemini_fast_refine_prompt,
                    on_stage=on_stage,
                    output_dir=output_dir,
                    chunk_func=refine_func,
                )
            except Exception as exc:
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
            format_max_output_tokens = 0 if normalized_category == "classical_chinese" else None
            final_markdown = await asyncio.wait_for(
                asyncio.to_thread(
                    format_book_text,
                    refined_markdown,
                    prompt_template=final_book_format_prompt,
                    max_output_tokens=format_max_output_tokens,
                    fail_on_max_tokens=normalized_category == "classical_chinese",
                    on_progress=(
                        (lambda chars: on_stage("format_progress", {"chars": chars}))
                        if on_stage
                        else None
                    ),
                ),
                timeout=settings.DEEPSEEK_FORMAT_CALL_TIMEOUT,
            )
            if (
                normalized_category == "classical_chinese"
                and _classical_format_looks_truncated(refined_markdown, final_markdown)
            ):
                final_markdown = _classical_format_fallback_text(refined_markdown, final_markdown)
        except DeepSeekMaxTokensError as exc:
            if normalized_category != "classical_chinese":
                raise
            final_markdown = _classical_format_fallback_text(refined_markdown, exc.partial_text)
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

    if normalized_category == "classical_chinese":
        final_markdown = enforce_classical_title(final_markdown, classical_original_title)
    final_markdown = normalize_final_markdown(final_markdown, category=normalized_category)
    _dump_stage(output_dir, "04_format.md", final_markdown)

    logger.info(
        "[summary] writer=%d → polish=%d → refine=%d → final=%d 字 (整体保留 %.1f%%)",
        writer_chars, polish_chars, refine_chars, len(final_markdown),
        100.0 * len(final_markdown) / max(writer_chars, 1),
    )

    if output_dir is not None:
        try:
            from app.core.quality_metrics import measure_quality, format_report

            report = measure_quality(final_markdown, category=normalized_category)
            report_text = format_report(report)
            report_path = output_dir / "quality_report.txt"
            report_path.write_text(report_text, encoding="utf-8")
        except Exception as exc:
            logger.warning("质量度量跳过: %s", exc)

    return final_markdown
