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
    FINANCE_CURRENT_WEB_EVIDENCE_GUARD_PROMPT,
    FINANCE_SOURCE_ATTRIBUTION_GUARD_PROMPT,
    INTRO_BODY_SEPARATION_PROMPT,
    SECTION_TITLE_REQUIREMENT_PROMPT,
    get_prompts,
    normalize_category,
)
from app.services.classical_chinese import (
    BUDDHIST_SCRIPTURE_STORY_CATEGORY,
    BuddhistStoryUnit,
    CLASSICAL_CHINESE_CATEGORY_IDS,
    CLASSICAL_INTERLEAVED_CATEGORY,
    CLASSICAL_PARAGRAPH_MARKER_RE,
    buddhist_scripture_final_title,
    build_buddhist_scripture_story_source,
    build_classical_prompt_source,
    build_classical_structure_checklist,
    classical_expected_paragraph_count,
    classical_final_title,
    classical_paragraph_marker,
    classical_source_lines,
    split_buddhist_story_units,
    classical_variant_for_category,
    strip_classical_paragraph_markers,
)
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
FINANCE_LONGFORM_CATEGORIES = frozenset({"finance_technology"})
FINANCE_LATEST_RESEARCH_STAGE_FILE = "00_finance_latest_research.md"
FINANCE_LATEST_RESEARCH_PROMPT_FILE = "00_finance_latest_research_prompt.md"
FINANCE_TECHNOLOGY_RECOMMENDATION_CATEGORY = "finance_technology_recommendation"
FINANCE_SOURCE_GUARDED_CATEGORIES = frozenset(
    {
        "finance_technology",
        FINANCE_TECHNOLOGY_RECOMMENDATION_CATEGORY,
    }
)
FINANCE_ADVICE_TARGET_10_MIN_WORDS = 12_000
FINANCE_ADVICE_TARGET_20_MIN_WORDS = 18_000
FINANCE_ADVICE_BASE_TARGET_WORDS = 28_000
FINANCE_ADVICE_WRITER_MIN_TARGET_RATIO = 0.65
FINANCE_ADVICE_STAGE_MIN_RETAIN_RATIO = 0.45
FINANCE_ADVICE_OUTPUT_MAX_TARGET_RATIO = 1.35
FINANCE_ADVICE_SOURCE_EXPANSION_MULTIPLIER = 2.5
FINANCE_ADVICE_CONTINUATION_BATCH_TARGET = 6_000
FINANCE_ADVICE_MAX_CONTINUATION_ROUNDS = 1
CLASSICAL_SEGMENTED_WRITER_MIN_SOURCE_CHARS = 60_000
CLASSICAL_SEGMENTED_WRITER_MIN_PARAGRAPHS = 600
CLASSICAL_SEGMENTED_WRITER_TARGET_SOURCE_CHARS = 12_000
CLASSICAL_SEGMENTED_WRITER_CONTEXT_CHARS = 1_500
CLASSICAL_LONG_SOURCE_DESIGN_TARGET_CHARS = 1_000_000
BUDDHIST_STORY_UNIT_WRITER_MIN_UNITS = 20
BUDDHIST_STORY_UNITS_PER_SEGMENT = 8
BUDDHIST_STORY_REPAIR_UNITS_PER_SEGMENT = 4
BUDDHIST_STORY_MIN_CHAPTER_CJK = 320
BUDDHIST_STORY_TINY_SOURCE_MIN_CHAPTER_CJK = 240
BUDDHIST_STORY_LONG_CHUNK_TARGET_CHARS = 2_800


_FRONT_SECTION_TITLES = ("最终书名", "最终简介", "三个备选书名", "三个备选简介", "目录", "导言", "文前", "正文")
_FRONT_SECTION_RE = re.compile(
    r"^(?:！！！)?@?\s*(最终书名|最终简介|三个备选书名|三个备选简介|目录|导言|文前|正文)\s*$"
)


def _is_finance_longform_category(category: str | None) -> bool:
    try:
        return normalize_category(category or "") in FINANCE_LONGFORM_CATEGORIES
    except Exception:
        return False


def _is_classical_chinese_category(category: str | None) -> bool:
    try:
        normalized = normalize_category(category or "")
        return (
            normalized in CLASSICAL_CHINESE_CATEGORY_IDS
            and normalized != BUDDHIST_SCRIPTURE_STORY_CATEGORY
        )
    except Exception:
        return False


def _is_classical_interleaved_category(category: str | None) -> bool:
    try:
        return normalize_category(category or "") == CLASSICAL_INTERLEAVED_CATEGORY
    except Exception:
        return False


def _strip_title_colons(title: str) -> str:
    return re.sub(r"\s*[:：]\s*", "，", title).strip(" ，,")


def _clean_delivery_title_line(line: str, current_section: str | None) -> str:
    stripped = line.strip()
    if not stripped:
        return line.rstrip()

    marker = re.match(r"^(?:！！！)?@?\s*(.+?)\s*$", stripped)
    if marker and marker.group(1).strip() in _FRONT_SECTION_TITLES:
        return "@" + marker.group(1).strip()

    should_clean = False
    prefix = ""
    title = stripped

    chapter = re.match(r"^((?:！！！)?@?\s*\d+\.\s*)(.+?)\s*$", stripped)
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
    elif stripped.startswith("@") or stripped.startswith("！！！"):
        should_clean = True
        if stripped.startswith("！！！"):
            prefix = "！！！"
            title = stripped.removeprefix("！！！").strip()
            if title.startswith("@"):
                prefix = "！！！@"
                title = title[1:].strip()
        else:
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

        chapter = re.match(r"^(?:！！！@?|@)\s*(\d+)[.．、]\s*(.+?)\s*$", stripped)
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
    return bool(re.match(r"^(?:！！！@?|@)\s*\d+[.．、]\s+.+$", line.strip()))


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
_INLINE_SUBHEADING_KEYWORDS = (
    "代价",
    "难题",
    "局限",
    "风险",
    "真相",
    "困境",
    "陷阱",
    "选择",
    "转折",
    "退路",
    "误判",
    "成本",
    "边界",
    "启示",
    "根源",
    "答案",
    "分水岭",
    "账本",
    "底气",
    "诉求",
    "策略",
    "模式",
    "路径",
    "危机",
    "余波",
    "裂缝",
    "规则",
    "逻辑",
    "竞争",
    "转化",
)


def _is_structural_markdown_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if re.match(r"^!\[[^\]]*\]\([^)]+\)\s*$", stripped):
        return True
    if stripped.startswith(("@", "！！！", "#", "```", "|", ">")):
        return True
    if re.match(r"^(?:[-*+]\s+|\d+[.．、]\s+)", stripped):
        return True
    return False


def _is_body_chapter_heading(line: str) -> bool:
    return bool(re.match(r"^(?:！！！@?|@)\s*\d+[.．、]\s*.+$", line.strip()))


def _normalize_body_chapter_heading(line: str) -> str | None:
    match = re.match(r"^(?:！！！@?|@)\s*(\d+)[.．、]\s*(.+?)\s*$", line.strip())
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


def _normalize_internal_subheading_title(title: str, *, max_len: int = 24) -> str | None:
    clean = _strip_inline_markdown_marks(title).lstrip("@").strip()
    clean = re.sub(r"^(?:\d+|[一二三四五六七八九十百]+)[.．、]\s*", "", clean).strip()
    clean = clean.strip(" \t*_`#：:，,、。.!！?？；;「」『』“”\"'‘’（）()[]【】")
    if not clean or len(clean) > max_len:
        return None
    if _cjk_ratio(clean) < 0.55:
        return None
    if re.search(r"[。！？；，、,.!?;：:]", clean):
        return None
    if clean.startswith(_CONTINUATION_PREFIXES):
        return None
    if clean.endswith(("的", "和", "与", "及", "或", "把", "被", "让", "在", "对", "给")):
        return None
    return clean


def _format_internal_subheading(title: str) -> str:
    return f"###{title}"


def _split_internal_subheading_line(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped or _is_image_markdown_line(stripped) or _is_body_chapter_heading(stripped):
        return None
    if _looks_like_editorial_comment(stripped):
        return None

    markdown_heading = re.match(r"^\s{0,3}#{1,6}\s*(.+?)\s*$", stripped)
    if markdown_heading:
        title = _normalize_internal_subheading_title(markdown_heading.group(1))
        return [_format_internal_subheading(title)] if title else None

    bold_prefix = re.match(r"^\*\*([^*\n]{1,32})\*\*\s*(.*?)\s*$", stripped)
    if bold_prefix:
        title = _normalize_internal_subheading_title(bold_prefix.group(1), max_len=18)
        rest = bold_prefix.group(2).strip()
        if title and (not rest or len(rest) >= 4):
            return [_format_internal_subheading(title), rest] if rest else [_format_internal_subheading(title)]

    candidate = stripped
    has_marker = False
    if candidate.startswith("@"):
        has_marker = True
        candidate = candidate.lstrip("@").strip()
        if _looks_like_editorial_comment(candidate):
            return None

    numbered = re.match(r"^(?:\d+|[一二三四五六七八九十百]+)[.．、]\s*(.+?)\s*$", candidate)
    if numbered:
        has_marker = True
        candidate = numbered.group(1).strip()

    direct_title = (
        _normalize_internal_subheading_title(candidate, max_len=16)
        if _looks_like_pseudo_heading_text(candidate)
        else None
    )
    if direct_title and _looks_like_pseudo_heading_text(direct_title):
        return [_format_internal_subheading(direct_title)]

    for separator in ("：", ":", "，", ","):
        index = candidate.find(separator)
        if index < 3:
            continue
        title_limit = 24 if separator in {"：", ":"} or has_marker else 12
        if index > title_limit:
            continue
        title = _normalize_internal_subheading_title(candidate[:index], max_len=title_limit)
        body = candidate[index + 1 :].strip()
        if not title or len(body) < 8:
            continue
        if not has_marker and not any(keyword in title for keyword in _INLINE_SUBHEADING_KEYWORDS):
            continue
        if separator in {"，", ",", "。"} and not has_marker and len(title) > 12:
            continue
        return [_format_internal_subheading(title), body]

    return None


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
    pending: list[str | None] = []
    for raw_line in lines:
        split_parts = _split_internal_subheading_line(raw_line)
        source_parts = split_parts if split_parts is not None else [raw_line]
        for part in source_parts:
            normalized = part.strip()
            if normalized.startswith("###"):
                title = _normalize_internal_subheading_title(normalized[3:].strip())
                pending.append(_format_internal_subheading(title) if title else None)
                continue
            pending.append(_strip_body_noise_line(normalized))

    for index, line in enumerate(pending):
        if line is None:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("###"):
            heading_text = stripped[3:].strip()
            next_line = ""
            for candidate in pending[index + 1 :]:
                if candidate:
                    next_line = candidate.strip()
                    break
            if next_line and _line_repeats_heading(heading_text, next_line):
                continue
            cleaned.append(stripped)
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
            title = _normalize_internal_subheading_title(stripped, max_len=16)
            if title:
                cleaned.append(_format_internal_subheading(title))
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
_CLASSICAL_MARKDOWN_HEADING_RE = re.compile(r"^\s*#{1,6}\s*(.+?)\s*$")
_CLASSICAL_ANY_MARKER_RE = re.compile(r"【P\d{4,}】")


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
    cleaned = strip_classical_paragraph_markers(cleaned)
    heading = _CLASSICAL_MARKDOWN_HEADING_RE.match(cleaned)
    if heading:
        cleaned = heading.group(1).strip()
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
    if _CLASSICAL_MARKDOWN_HEADING_RE.match(stripped) and not _CLASSICAL_ANY_MARKER_RE.search(stripped):
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


def _classical_body_for_validation(markdown: str) -> str:
    match = re.search(r"(?m)^(?:！！！)?@?\s*正文\s*$", markdown or "")
    return (markdown or "")[match.end() :] if match else (markdown or "")


def _classical_expected_marker_sequence(
    *,
    expected_count: int,
    expected_markers: list[int] | range | None = None,
) -> list[int]:
    if expected_markers is not None:
        sequence: list[int] = []
        seen: set[int] = set()
        for marker in expected_markers:
            value = int(marker)
            if value > 0 and value not in seen:
                sequence.append(value)
                seen.add(value)
        return sequence
    return list(range(1, max(0, int(expected_count or 0)) + 1))


def _classical_marker_label(marker: int) -> str:
    return f"P{marker:04d}"


def _classical_marker_range_label(markers: list[int]) -> str:
    if not markers:
        return "空范围"
    if len(markers) == 1:
        return _classical_marker_label(markers[0])
    return f"{_classical_marker_label(markers[0])} 到 {_classical_marker_label(markers[-1])}"


def _classical_structure_issues(
    markdown: str,
    *,
    expected_count: int,
    expected_markers: list[int] | range | None = None,
) -> list[str]:
    expected_sequence = _classical_expected_marker_sequence(
        expected_count=expected_count,
        expected_markers=expected_markers,
    )
    if len(expected_sequence) < 2 and expected_markers is None:
        return []
    body = _classical_body_for_validation(markdown)
    markers = [int(item) for item in CLASSICAL_PARAGRAPH_MARKER_RE.findall(body)]
    issues: list[str] = []
    if not markers:
        return [f"正文没有保留任何 P 段落编号，预期 {_classical_marker_range_label(expected_sequence)}"]
    expected = set(expected_sequence)
    seen = set(markers)
    missing = sorted(expected - seen)
    extra = sorted(seen - expected)
    duplicate = sorted({marker for marker in markers if markers.count(marker) > 1})
    if missing:
        sample = ", ".join(_classical_marker_label(item) for item in missing[:12])
        suffix = "..." if len(missing) > 12 else ""
        issues.append(f"缺少原文段落编号：{sample}{suffix}")
    if extra:
        sample = ", ".join(_classical_marker_label(item) for item in extra[:12])
        suffix = "..." if len(extra) > 12 else ""
        issues.append(f"出现超出原文范围的段落编号：{sample}{suffix}")
    if duplicate:
        sample = ", ".join(_classical_marker_label(item) for item in duplicate[:12])
        suffix = "..." if len(duplicate) > 12 else ""
        issues.append(f"出现重复段落编号：{sample}{suffix}")
    ordered = [marker for marker in markers if marker in expected]
    if ordered != sorted(ordered):
        issues.append("P 段落编号顺序被打乱")
    pseudo_headings = [
        line.strip()
        for line in body.splitlines()
        if _CLASSICAL_MARKDOWN_HEADING_RE.match(line.strip())
        and not _CLASSICAL_ANY_MARKER_RE.search(line)
    ]
    if pseudo_headings:
        sample = "；".join(pseudo_headings[:5])
        issues.append(f"出现没有原文编号的 Markdown 伪小标题：{sample}")
    return issues


def _classical_translation_issues(source_text: str, markdown: str) -> list[str]:
    source = source_text or ""
    body = _classical_body_for_validation(markdown)
    issues: list[str] = []
    if "双卫" in source and re.search(r"(侍卫|保镖|护卫)", body):
        issues.append("原文含“双卫”，正文疑似误译为侍卫、保镖或护卫")
    if "婢子不宜好相识" in source and re.search(r"结交.{0,8}好相识|好相识", body):
        issues.append("原文含“婢子不宜好相识”，正文疑似保留了错误的现代化理解")
    return issues


def _classical_interleaved_structure_issues(
    markdown: str,
    *,
    expected_count: int,
    expected_markers: list[int] | range | None = None,
) -> list[str]:
    expected_sequence = _classical_expected_marker_sequence(
        expected_count=expected_count,
        expected_markers=expected_markers,
    )
    if not expected_sequence:
        return []
    body = _classical_body_for_validation(markdown)
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    issues: list[str] = []
    for marker in expected_sequence:
        marker_text = classical_paragraph_marker(marker)
        line_index = next((idx for idx, line in enumerate(lines) if marker_text in line), -1)
        if line_index < 0:
            continue
        original_line = CLASSICAL_PARAGRAPH_MARKER_RE.sub("", lines[line_index], count=1).strip()
        if not re.match(r"^[（(]\s*原文\s*[）)]", original_line):
            issues.append(f"{_classical_marker_label(marker)} 缺少（原文）标注")
        translation_index = line_index + 1
        if translation_index >= len(lines):
            issues.append(f"{_classical_marker_label(marker)} 缺少紧随其后的（翻译）段")
            continue
        translation_line = lines[translation_index]
        if CLASSICAL_PARAGRAPH_MARKER_RE.search(translation_line) or not re.match(
            r"^[（(]\s*翻译\s*[）)]",
            translation_line,
        ):
            issues.append(f"{_classical_marker_label(marker)} 缺少紧随其后的（翻译）段")
    return issues


def _classical_quality_issues(
    markdown: str,
    *,
    expected_count: int,
    source_text: str,
    expected_markers: list[int] | range | None = None,
    interleaved: bool = False,
) -> list[str]:
    issues = _classical_structure_issues(
        markdown,
        expected_count=expected_count,
        expected_markers=expected_markers,
    ) + _classical_translation_issues(
        source_text,
        markdown,
    )
    if interleaved:
        issues += _classical_interleaved_structure_issues(
            markdown,
            expected_count=expected_count,
            expected_markers=expected_markers,
        )
    return issues


async def _repair_classical_quality_once(
    markdown: str,
    *,
    marked_source_text: str,
    source_text: str,
    expected_count: int,
    stage: str,
    output_dir: Path | None,
    expected_markers: list[int] | range | None = None,
    segment_only: bool = False,
    interleaved: bool = False,
) -> str:
    issues = _classical_quality_issues(
        markdown,
        expected_count=expected_count,
        source_text=source_text,
        expected_markers=expected_markers,
        interleaved=interleaved,
    )
    if not issues:
        return markdown
    issue_text = "\n".join(f"- {issue}" for issue in issues)
    logger.warning("古文白话%s阶段触发结构/误译修复: %s", stage, "; ".join(issues))
    _dump_stage(output_dir, f"classical_{stage}_quality_issues.txt", issue_text)
    marker_sequence = _classical_expected_marker_sequence(
        expected_count=expected_count,
        expected_markers=expected_markers,
    )
    marker_scope = _classical_marker_range_label(marker_sequence)
    output_scope = (
        "只输出修复后的本段 P 编号白话正文，不要输出书名、简介、目录、导言、@正文或章节标题。"
        if segment_only
        else "不要重写整篇，不要压缩，不要总结，不要输出解释，直接输出修复后的完整书稿。"
    )
    interleaved_rule = (
        "- 本稿是原文翻译穿插版，每个 P 编号必须对应两个连续自然段：【P0001】（原文）原文内容，下一段为（翻译）白话译文；原文段不得改写，翻译段不得漏译。\n"
        "- 原文翻译穿插版不得把原文和翻译合并成一段，不得集中输出全部原文后再输出全部翻译。"
        if interleaved
        else ""
    )
    repair_prompt = f"""你是古文白话稿件的结构校对编辑。下面的稿件已经生成，但存在段落编号、漏译、伪小标题或古今异义误译问题。请只修复这些问题，其他内容保持不变。

【必须修复的问题】
{issue_text}

【修复硬规则】
- 必须按原文编号 {marker_scope} 连续输出，每个对应段落段首保留原来的【P0001】这类编号。
- 不得新增没有 P 编号的小标题、Markdown 标题、解释性小节或资料型章节。
- 辑名、篇名、作者行、导言、引语、题记、按语、正文段落都要保留，不得漏句、漏半句或跳译。
- 原文中 **作者名** 形式的作者或署名行，必须保留加粗标记。
- “双卫”在本语境下不得译成侍卫、保镖或护卫；“婢子不宜好相识”不得译成“结交什么好相识”。
{interleaved_rule}
- {output_scope}

【带编号原文】
{marked_source_text.strip()}

【待修复稿件】
{markdown.strip()}
"""
    repaired = await _route_writer_call(
        repair_prompt,
        max_output_tokens=0,
        retry_on_failure=False,
    )
    _dump_stage(output_dir, f"classical_{stage}_quality_repaired.md", repaired)
    remaining = _classical_quality_issues(
        repaired,
        expected_count=expected_count,
        source_text=source_text,
        expected_markers=expected_markers,
        interleaved=interleaved,
    )
    if remaining:
        raise RuntimeError(f"古文白话{stage}阶段结构保护失败: {'；'.join(remaining)}")
    return repaired


def _should_use_classical_segmented_writer(source_text: str, expected_count: int) -> bool:
    return (
        len(source_text or "") >= CLASSICAL_SEGMENTED_WRITER_MIN_SOURCE_CHARS
        or expected_count >= CLASSICAL_SEGMENTED_WRITER_MIN_PARAGRAPHS
    )


def _classical_segment_source_lines(
    lines: list[str],
    *,
    target_chars: int = CLASSICAL_SEGMENTED_WRITER_TARGET_SOURCE_CHARS,
) -> list[tuple[int, list[str]]]:
    target = max(2_000, int(target_chars or CLASSICAL_SEGMENTED_WRITER_TARGET_SOURCE_CHARS))
    segments: list[tuple[int, list[str]]] = []
    current: list[str] = []
    current_len = 0
    start_index = 1
    for index, line in enumerate(lines, start=1):
        line_len = len(line) + 1
        if current and current_len + line_len > target:
            segments.append((start_index, current))
            current = []
            current_len = 0
            start_index = index
        current.append(line)
        current_len += line_len
    if current:
        segments.append((start_index, current))
    return segments


def _classical_context_from_lines(
    lines: list[str],
    *,
    start_index: int,
    end_index: int,
    max_chars: int = CLASSICAL_SEGMENTED_WRITER_CONTEXT_CHARS,
) -> tuple[str, str]:
    limit = max(0, int(max_chars or 0))
    if limit <= 0:
        return "", ""

    before_parts: list[str] = []
    before_len = 0
    for line in reversed(lines[: max(0, start_index - 1)]):
        line_len = len(line) + 1
        if before_parts and before_len + line_len > limit:
            break
        before_parts.append(line)
        before_len += line_len
    before = "\n".join(reversed(before_parts))

    after_parts: list[str] = []
    after_len = 0
    for line in lines[end_index:]:
        line_len = len(line) + 1
        if after_parts and after_len + line_len > limit:
            break
        after_parts.append(line)
        after_len += line_len
    return before, "\n".join(after_parts)


def _classical_source_preview(lines: list[str], *, max_chars: int = 8_000) -> str:
    if not lines:
        return ""
    limit = max(1_000, int(max_chars or 8_000))
    head_limit = limit // 2
    tail_limit = limit - head_limit
    head: list[str] = []
    head_len = 0
    for line in lines:
        line_len = len(line) + 1
        if head and head_len + line_len > head_limit:
            break
        head.append(line)
        head_len += line_len

    tail: list[str] = []
    tail_len = 0
    for line in reversed(lines):
        line_len = len(line) + 1
        if tail and tail_len + line_len > tail_limit:
            break
        tail.append(line)
        tail_len += line_len

    rendered_tail = list(reversed(tail))
    if head and rendered_tail and head[-1] == rendered_tail[0]:
        return "\n".join(head)
    omitted = max(0, len(lines) - len(head) - len(rendered_tail))
    middle = [f"……中间省略 {omitted} 个原文段落，仅用于前置信息判断，正文会逐段完整翻译……"] if omitted else []
    return "\n".join(head + middle + rendered_tail)


def _classical_frontmatter_fallback(original_title: object, total_paragraphs: int) -> str:
    return f"""@最终书名
{classical_final_title(original_title)}
@最终简介
这是一部以原文为根基整理的白话稿。文字尽量贴近原作的情节、人物关系和语气，把难读的文言转成更顺畅的现代汉语，同时保留原书的结构和余味。
@目录
1. 全文
@导言
这份白话稿处理的不是一段摘录，而是一部有自身结构的古文作品。原文共 {total_paragraphs} 个段落，每一段都关系到后文的语气、人物和叙事推进。

阅读时可以先把它看作一次忠实的语言转换：标题、署名、引语、导言和正文都按原有顺序保留，难处只在于把古汉语的省略、倒装和旧词义转成今天能读懂的表达。
@正文
@1. 全文"""


def _classical_frontmatter_prompt(
    *,
    lines: list[str],
    original_title: object,
    variant: object,
) -> str:
    preview = _classical_source_preview(lines)
    final_title = classical_final_title(original_title)
    variant_checklist = build_classical_structure_checklist("\n".join(lines[:80]), max_lines=80)
    return f"""你是古文白话书稿编辑。现在处理的是一部长原文，正文会由系统按 P 编号逐段完整翻译；你只负责生成前置信息，不得翻译正文，不得概括代替正文。

【输出范围】
只输出以下结构，顺序不得改变：
@最终书名
{final_title}
@最终简介
一段封底式简介
@目录
1. 全文
@导言
2 到 4 个自然段
@正文
@1. 全文

【硬规则】
- 最终书名必须逐字使用：{final_title}
- 不要输出 P 编号段落，不要翻译正文，不要补写正文。
- 简介和导言要服务古文白话读者，说明作品入口、阅读气质和整理方式，不要写成论文摘要、营销口播或空泛套话。
- 导言不得替代正文，不要提前复述完整故事。
- 直接输出成稿，不要解释，不要代码块。

【细分方向】
{variant or "plain"}

【原文结构参考】
{variant_checklist}

【原文节选参考】
{preview}"""


def _classical_segment_writer_prompt(
    *,
    segment_text: str,
    all_lines: list[str],
    start_index: int,
    variant: object,
    original_title: object,
    interleaved: bool = False,
) -> str:
    segment_lines = classical_source_lines(segment_text)
    end_index = start_index + len(segment_lines) - 1
    before, after = _classical_context_from_lines(
        all_lines,
        start_index=start_index,
        end_index=end_index,
    )
    task_pack = build_classical_prompt_source(
        segment_text,
        variant=variant,
        original_title=original_title,
        start_index=start_index,
        interleaved=interleaved,
    )
    before_block = before.strip() or "无"
    after_block = after.strip() or "无"
    output_mode_rule = (
        "把当前分段范围内的原文逐段完整输出为原文翻译穿插结构。只输出本段 P 编号原文翻译正文，不输出 @最终书名、@最终简介、@目录、@导言、@正文、章节标题、解释或分析。"
        if interleaved
        else "把当前分段范围内的原文逐段完整白话化。只输出本段 P 编号白话正文，不输出 @最终书名、@最终简介、@目录、@导言、@正文、章节标题、解释或分析。"
    )
    interleaved_rule = (
        "\n- 每个原文段落必须输出两个连续自然段：第一段为【P0001】（原文）原文内容，第二段为（翻译）现代汉语译文。\n- 原文段必须逐字保留任务包中的对应原文，不得改写；翻译段必须完整翻译上一段原文，不得漏译。\n- 不得把原文和翻译合并成一段，不得集中输出全部原文后再输出全部翻译。"
        if interleaved
        else ""
    )
    return f"""你是一名成熟的古文白话写作者。现在处理的是长篇古文白话工程中的一个连续分段。

【当前分段范围】
{_classical_marker_label(start_index)} 到 {_classical_marker_label(end_index)}

【本次只做一件事】
{output_mode_rule}

【必须严格遵守】
- 每个原文段落必须一一对应输出，不得漏段、漏句、漏半句，不得合并段落，不得拆散段落，不得调换顺序。
- 每段开头必须保留原来的 P 编号，从 {_classical_marker_label(start_index)} 连续到 {_classical_marker_label(end_index)}。
- 上文和下文只供理解人物关系、语气和词义，不属于本次输出范围，绝对不要翻译上下文参考。
- 输出时不得出现没有 P 编号的小标题、Markdown 标题、资料型说明、任务说明或模型过程词。
- 原文中 **作者名** 形式的作者或署名行，必须保留加粗标记。
- 古今异义、器物名、职官名和旧称必须按上下文处理；不确定时保守表达，不要改成另一层意思。
- 不要为了顺口改剧情，不要新增情节、人物、心理和结尾。
{interleaved_rule}

【上文参考，不翻译】
{before_block}

【下文参考，不翻译】
{after_block}

【当前分段任务包】
{task_pack}"""


def _classical_segment_output_body(markdown: str, *, interleaved: bool = False) -> str:
    body = _classical_body_for_validation(markdown)
    lines: list[str] = []
    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if _FRONT_SECTION_RE.match(stripped):
            continue
        if _CLASSICAL_CHAPTER_HEADING_RE.match(stripped):
            continue
        if _CLASSICAL_MARKDOWN_HEADING_RE.match(stripped) and not _CLASSICAL_ANY_MARKER_RE.search(stripped):
            continue
        if _CLASSICAL_PUNCT_ONLY_RE.fullmatch(stripped):
            continue
        if _CLASSICAL_ANY_MARKER_RE.search(stripped) or not lines:
            lines.append(stripped)
        elif interleaved and re.match(r"^[（(]\s*翻译\s*[）)]", stripped):
            lines.append(stripped)
        else:
            lines[-1] = f"{lines[-1]}{stripped}"
    return "\n".join(lines).strip()


def _classical_segment_cache_path(
    output_dir: Path | None,
    *,
    segment_index: int,
    start_index: int,
    end_index: int,
    segment_text: str,
) -> Path | None:
    if output_dir is None:
        return None
    digest = hashlib.sha256(segment_text.encode("utf-8")).hexdigest()[:16]
    return (
        output_dir
        / "01_classical_writer_segments"
        / f"{segment_index:04d}_{_classical_marker_label(start_index)}-{_classical_marker_label(end_index)}_{digest}.md"
    )


async def _generate_classical_frontmatter(
    *,
    lines: list[str],
    variant: object,
    original_title: object,
    output_dir: Path | None,
) -> str:
    total_paragraphs = len(lines)
    digest = hashlib.sha256(("\n".join(lines)).encode("utf-8")).hexdigest()[:16]
    cache_path = output_dir / f"01_classical_frontmatter_{digest}.md" if output_dir else None
    if cache_path and cache_path.exists():
        try:
            cached = cache_path.read_text(encoding="utf-8").strip()
            if cached and not _front_matter_missing(cached) and re.search(r"(?m)^@1[.．、]\s*全文\s*$", cached):
                logger.info("复用古文白话长稿前置信息缓存: %s", cache_path.name)
                return cached
        except Exception as exc:
            logger.warning("古文白话前置信息缓存读取失败: %s", exc)

    prompt = _classical_frontmatter_prompt(
        lines=lines,
        original_title=original_title,
        variant=variant,
    )
    try:
        frontmatter = await _route_writer_call(
            prompt,
            max_output_tokens=0,
            retry_on_failure=False,
        )
        if _front_matter_missing(frontmatter):
            raise ValueError("frontmatter missing required sections")
        frontmatter = enforce_classical_title(frontmatter, original_title)
        if not re.search(r"(?m)^@1[.．、]\s*全文\s*$", frontmatter):
            frontmatter = frontmatter.rstrip() + "\n@1. 全文"
    except Exception as exc:
        logger.warning("古文白话长稿前置信息生成失败，使用确定性兜底: %s", exc)
        frontmatter = _classical_frontmatter_fallback(original_title, total_paragraphs)

    if cache_path:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(frontmatter.strip(), encoding="utf-8")
        except Exception as exc:
            logger.warning("古文白话前置信息缓存写入失败: %s", exc)
    return frontmatter.strip()


async def _generate_classical_segmented_writer(
    source_text: str,
    *,
    variant: object,
    original_title: object,
    output_dir: Path | None,
    on_stage: StageCallback | None,
    interleaved: bool = False,
) -> str:
    lines = classical_source_lines(source_text)
    if not lines:
        raise ValueError("古文白话原文为空，无法生成")
    expected_count = len(lines)
    segments = _classical_segment_source_lines(
        lines,
        target_chars=CLASSICAL_SEGMENTED_WRITER_TARGET_SOURCE_CHARS,
    )
    logger.info(
        "古文白话启用长稿分段主笔 source_chars=%d design_target=%d paragraphs=%d segments=%d",
        len(source_text or ""),
        CLASSICAL_LONG_SOURCE_DESIGN_TARGET_CHARS,
        expected_count,
        len(segments),
    )

    frontmatter = await _generate_classical_frontmatter(
        lines=lines,
        variant=variant,
        original_title=original_title,
        output_dir=output_dir,
    )

    segment_bodies: list[str] = []
    completed_chars = len(frontmatter)
    for segment_index, (start_index, segment_lines) in enumerate(segments, start=1):
        segment_text = "\n".join(segment_lines)
        end_index = start_index + len(segment_lines) - 1
        expected_markers = list(range(start_index, end_index + 1))
        cache_path = _classical_segment_cache_path(
            output_dir,
            segment_index=segment_index,
            start_index=start_index,
            end_index=end_index,
            segment_text=segment_text,
        )
        body: str | None = None
        if cache_path and cache_path.exists():
            try:
                cached = cache_path.read_text(encoding="utf-8").strip()
                cached_issues = _classical_quality_issues(
                    cached,
                    expected_count=expected_count,
                    source_text=segment_text,
                    expected_markers=expected_markers,
                    interleaved=interleaved,
                )
                if not cached_issues:
                    body = cached
                    logger.info(
                        "复用古文白话分段缓存 %d/%d %s-%s",
                        segment_index,
                        len(segments),
                        _classical_marker_label(start_index),
                        _classical_marker_label(end_index),
                    )
                else:
                    logger.warning(
                        "古文白话分段缓存校验失败，将重生 %d/%d: %s",
                        segment_index,
                        len(segments),
                        "; ".join(cached_issues),
                    )
            except Exception as exc:
                logger.warning("古文白话分段缓存读取失败 %d/%d: %s", segment_index, len(segments), exc)

        if body is None:
            prompt = _classical_segment_writer_prompt(
                segment_text=segment_text,
                all_lines=lines,
                start_index=start_index,
                variant=variant,
                original_title=original_title,
                interleaved=interleaved,
            )
            raw_segment = await _route_writer_call(
                prompt,
                max_output_tokens=0,
                retry_on_failure=False,
            )
            body = _classical_segment_output_body(raw_segment, interleaved=interleaved)
            body = await _repair_classical_quality_once(
                body,
                marked_source_text=build_classical_prompt_source(
                    segment_text,
                    variant=variant,
                    original_title=original_title,
                    start_index=start_index,
                    interleaved=interleaved,
                ),
                source_text=segment_text,
                expected_count=expected_count,
                expected_markers=expected_markers,
                stage=f"writer_segment_{segment_index:04d}",
                output_dir=output_dir,
                segment_only=True,
                interleaved=interleaved,
            )
            body = _classical_segment_output_body(body, interleaved=interleaved)
            if cache_path:
                try:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(body.strip(), encoding="utf-8")
                except Exception as exc:
                    logger.warning("古文白话分段缓存写入失败 %d/%d: %s", segment_index, len(segments), exc)

        segment_bodies.append(body.strip())
        completed_chars += len(body)
        if on_stage:
            on_stage("writer_progress", {"chars": completed_chars})

    assembled = frontmatter.rstrip() + "\n" + "\n".join(segment_bodies).strip()
    final_issues = _classical_quality_issues(
        assembled,
        expected_count=expected_count,
        source_text=source_text,
        interleaved=interleaved,
    )
    if final_issues:
        raise RuntimeError(f"古文白话分段组装覆盖校验失败: {'；'.join(final_issues)}")
    return assembled


def _should_use_buddhist_story_unit_writer(
    units: list[BuddhistStoryUnit],
    *,
    original_title: object = None,
    source_text: str = "",
) -> bool:
    if len(units) >= BUDDHIST_STORY_UNIT_WRITER_MIN_UNITS:
        return True
    title_probe = f"{original_title or ''}\n{source_text[:500]}"
    return "百喻经" in title_probe and len(units) >= 12


def _buddhist_story_unit_chunks(
    units: list[BuddhistStoryUnit],
    *,
    per_segment: int = BUDDHIST_STORY_UNITS_PER_SEGMENT,
) -> list[list[BuddhistStoryUnit]]:
    size = max(1, int(per_segment or BUDDHIST_STORY_UNITS_PER_SEGMENT))
    return [units[pos : pos + size] for pos in range(0, len(units), size)]


def _buddhist_story_units_source_block(units: list[BuddhistStoryUnit]) -> str:
    blocks: list[str] = []
    for unit in units:
        blocks.append(
            f"【U{unit.index:04d}】标题：{unit.title}\n原文：\n{unit.source.strip()}"
        )
    return "\n\n".join(blocks)


def _buddhist_story_frontmatter(units: list[BuddhistStoryUnit], *, original_title: object = None) -> str:
    final_title = buddhist_scripture_final_title(original_title)
    is_baiyujing = "百喻经" in final_title
    catalog = "\n".join(f"{unit.index}. {unit.title}" for unit in units)
    if is_baiyujing:
        intro = "《百喻经》用一则则短小譬喻，照见人在贪、嗔、痴、疑和执著中的颠倒。它的故事常常看似荒唐，却把修行里最容易犯的错误放到眼前：有人为了多得一点盐而伤身，有人想建楼却不要地基，有人追逐影子却以为抓住了金子。读这些故事，重点不在笑话本身，而在笑过之后看见自己心里的相似处。"
        description = "本书依《百喻经》原有譬喻次第，将经中故事改写为连贯、平实、有故事感的白话文本。每一则譬喻都尽量保留原文里的动作、选择、后果和警醒，不把它写成逐句翻译，也不把它改成脱离原意的现代寓言。"
    else:
        intro = f"这部佛经白话稿依原文次第展开，共整理出 {len(units)} 个故事或说法单元。每个单元都从原文已有的人物、问答、动作或譬喻进入，把压缩在经文中的因缘和收束写成能顺着读下去的白话故事。"
        description = "本书依据佛经原文改写为连贯白话，尽量让读者在故事和说法推进中理解其中的警醒。文字保持克制，不另造情节，不离开原文讲述的事实和道理。"
    return f"""@最终书名
{final_title}
@最终简介
{description}
@目录
{catalog}
@导言
{intro}
@正文"""


_BUDDHIST_BODY_CHAPTER_RE = re.compile(r"^\s*@\s*(\d+)[.．、]\s*(.+?)\s*$")


def _extract_buddhist_body_chapters(markdown: str) -> list[tuple[int, str, list[str]]]:
    chapters: list[tuple[int, str, list[str]]] = []
    in_body = False
    current_num: int | None = None
    current_title = ""
    current_lines: list[str] = []
    for raw_line in (markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        section = _FRONT_SECTION_RE.match(line)
        if section:
            if current_num is not None:
                chapters.append((current_num, current_title, current_lines))
            in_body = section.group(1) == "正文"
            current_num = None
            current_title = ""
            current_lines = []
            continue
        if not in_body and not chapters and _BUDDHIST_BODY_CHAPTER_RE.match(line):
            in_body = True
        if not in_body:
            continue
        match = _BUDDHIST_BODY_CHAPTER_RE.match(line)
        if match:
            if current_num is not None:
                chapters.append((current_num, current_title, current_lines))
            current_num = int(match.group(1))
            current_title = match.group(2).strip()
            current_lines = []
            continue
        if current_num is not None and not _FRONT_SECTION_RE.match(line):
            current_lines.append(line)
    if current_num is not None:
        chapters.append((current_num, current_title, current_lines))
    return chapters


def _buddhist_chapter_cjk(lines: list[str]) -> int:
    return sum(1 for char in "".join(lines) if "\u3400" <= char <= "\u9fff")


def _buddhist_min_chapter_cjk(unit: BuddhistStoryUnit) -> int:
    source_cjk = sum(1 for char in unit.source if "\u3400" <= char <= "\u9fff")
    if source_cjk < 24:
        return BUDDHIST_STORY_TINY_SOURCE_MIN_CHAPTER_CJK
    return BUDDHIST_STORY_MIN_CHAPTER_CJK


def _buddhist_story_manuscript_issues(markdown: str, units: list[BuddhistStoryUnit]) -> list[str]:
    expected = [unit.index for unit in units]
    chapters = _extract_buddhist_body_chapters(markdown)
    actual = [number for number, _, _ in chapters]
    issues: list[str] = []
    if actual != expected:
        missing = [number for number in expected if number not in actual]
        extra = [number for number in actual if number not in expected]
        if missing:
            issues.append("缺少佛经故事章节：" + ", ".join(f"@{item}" for item in missing[:12]))
        if extra:
            issues.append("出现本批次外章节：" + ", ".join(f"@{item}" for item in extra[:12]))
        if not missing and not extra:
            issues.append("佛经故事章节顺序被改动")
    by_number = {number: lines for number, _, lines in chapters}
    unit_by_number = {unit.index: unit for unit in units}
    short: list[str] = []
    for number in expected:
        unit = unit_by_number[number]
        lines = by_number.get(number, [])
        cjk = _buddhist_chapter_cjk(lines)
        if cjk < _buddhist_min_chapter_cjk(unit):
            short.append(f"@{number} {unit.title} 仅 {cjk} 字")
    if short:
        issues.append("佛经故事章节展开不足：" + "；".join(short[:8]))
    return issues


def _buddhist_chapter_marker_issues(source_chunk: str, output_chunk: str) -> list[str]:
    source_numbers = [int(item) for item in re.findall(r"(?m)^\s*@\s*(\d+)[.．、]", source_chunk or "")]
    if not source_numbers:
        return []
    output_numbers = [number for number, _, _ in _extract_buddhist_body_chapters(output_chunk)]
    if output_numbers == source_numbers:
        return []
    issues: list[str] = []
    missing = [number for number in source_numbers if number not in output_numbers]
    extra = [number for number in output_numbers if number not in source_numbers]
    if missing:
        issues.append("缺少佛经章节：" + ", ".join(f"@{item}" for item in missing[:8]))
    if extra:
        issues.append("出现本块外佛经章节：" + ", ".join(f"@{item}" for item in extra[:8]))
    if not missing and not extra:
        issues.append("佛经章节顺序被改动")
    return issues


def _buddhist_story_chapter_map(markdown: str) -> dict[int, tuple[str, str]]:
    chapters = _extract_buddhist_body_chapters(markdown)
    return {
        number: (title, "\n".join(line for line in lines if line.strip()).strip())
        for number, title, lines in chapters
    }


def _buddhist_story_units_to_repair(markdown: str, units: list[BuddhistStoryUnit]) -> list[BuddhistStoryUnit]:
    by_number = _buddhist_story_chapter_map(markdown)
    targets: list[BuddhistStoryUnit] = []
    for unit in units:
        _, body = by_number.get(unit.index, ("", ""))
        cjk = sum(1 for char in body if "\u3400" <= char <= "\u9fff")
        if not body or cjk < _buddhist_min_chapter_cjk(unit):
            targets.append(unit)
    return targets


def _buddhist_story_repair_batches(units: list[BuddhistStoryUnit]) -> list[list[BuddhistStoryUnit]]:
    batches: list[list[BuddhistStoryUnit]] = []
    current: list[BuddhistStoryUnit] = []
    previous: int | None = None
    for unit in units:
        if (
            current
            and (
                len(current) >= BUDDHIST_STORY_REPAIR_UNITS_PER_SEGMENT
                or previous is None
                or unit.index != previous + 1
            )
        ):
            batches.append(current)
            current = []
        current.append(unit)
        previous = unit.index
    if current:
        batches.append(current)
    return batches


def _buddhist_story_existing_block(markdown: str, units: list[BuddhistStoryUnit]) -> str:
    by_number = _buddhist_story_chapter_map(markdown)
    blocks: list[str] = []
    for unit in units:
        title, body = by_number.get(unit.index, (unit.title, ""))
        body = body.strip() or "（当前稿件缺少这一章或内容过短。）"
        blocks.append(f"@{unit.index}. {title or unit.title}\n{body}")
    return "\n\n".join(blocks)


async def _repair_buddhist_story_batch_against_source(
    markdown: str,
    *,
    units: list[BuddhistStoryUnit],
    output_dir: Path | None,
    stage: str,
    batch_index: int,
) -> str:
    prompt = f"""你是佛经白话故事稿的单元修复作者。下面只给你一小批《百喻经》或佛经故事单元，请根据原文，把这些章节修成完整、连贯、有故事感的小故事。

【输出硬规则】
- 只输出本批次正文，不要输出 @最终书名、@最终简介、@目录、@导言、@正文。
- 必须严格输出这些章节：{", ".join(f"@{unit.index}. {unit.title}" for unit in units)}。
- 每个原文故事单元只能对应一个正式章节；不能新增章节，不能输出本批次之外的章节。
- 每章都要先讲完整故事，再在同一章末尾自然收束寓意。
- 每章尽量写充分，常规写成 350 到 650 汉字左右；原文特别短也要尽量写到 250 汉字以上。
- 不得编造原文没有的人物、地点、关键事实、因果结论或新结局。
- 可以参考当前稿件的已有表达，但如果当前稿件太短，必须补足故事进入、人物动作、误判、后果和收束。

【本批次原文故事单元】
{_buddhist_story_units_source_block(units)}

【当前稿件对应章节】
{_buddhist_story_existing_block(markdown, units)}
"""
    repaired = await _route_writer_call(prompt, max_output_tokens=0, retry_on_failure=False)
    body = _buddhist_story_segment_output_body(repaired)
    remaining = _buddhist_story_manuscript_issues(body, units)
    if remaining:
        strict_prompt = prompt + f"""

【上次修复仍不合格】
{'; '.join(remaining)}

请重新输出本批次，必须补足篇幅和故事细节，仍然只能输出指定章节。
"""
        repaired = await _route_writer_call(strict_prompt, max_output_tokens=0, retry_on_failure=False)
        body = _buddhist_story_segment_output_body(repaired)
        remaining = _buddhist_story_manuscript_issues(body, units)
    if remaining:
        raise RuntimeError(f"佛经白话单元定向修复失败: {'；'.join(remaining)}")
    _dump_stage(output_dir, f"buddhist_story_{stage}_targeted_repair_{batch_index:04d}.md", body)
    return body


async def _repair_buddhist_story_manuscript_against_units(
    markdown: str,
    *,
    units: list[BuddhistStoryUnit],
    output_dir: Path | None,
    stage: str,
    original_title: object = None,
) -> str:
    if not units:
        return markdown

    issues = _buddhist_story_manuscript_issues(markdown, units)
    if not issues:
        return markdown

    issue_text = "\n".join(f"- {issue}" for issue in issues)
    _dump_stage(output_dir, f"buddhist_story_{stage}_targeted_issues.txt", issue_text)
    targets = _buddhist_story_units_to_repair(markdown, units)
    replacements: dict[int, tuple[str, str]] = {}
    for batch_index, batch in enumerate(_buddhist_story_repair_batches(targets), start=1):
        batch_body = await _repair_buddhist_story_batch_against_source(
            markdown,
            units=batch,
            output_dir=output_dir,
            stage=stage,
            batch_index=batch_index,
        )
        replacements.update(_buddhist_story_chapter_map(batch_body))

    existing = _buddhist_story_chapter_map(markdown)
    parts = [_buddhist_story_frontmatter(units, original_title=original_title)]
    for unit in units:
        title, body = replacements.get(unit.index) or existing.get(unit.index, (unit.title, ""))
        title = title or unit.title
        body = body.strip()
        parts.append(f"@{unit.index}. {title}\n{body}".strip())

    repaired = "\n\n".join(part.strip() for part in parts if part.strip())
    repaired = normalize_final_markdown(repaired, category=BUDDHIST_SCRIPTURE_STORY_CATEGORY)
    remaining = _buddhist_story_manuscript_issues(repaired, units)
    if remaining:
        _dump_stage(
            output_dir,
            f"buddhist_story_{stage}_targeted_remaining.txt",
            "\n".join(f"- {issue}" for issue in remaining),
        )
        raise RuntimeError(f"佛经白话单元定向修复后仍不合格: {'；'.join(remaining)}")
    _dump_stage(output_dir, f"buddhist_story_{stage}_targeted_repaired.md", repaired)
    return repaired


def _split_buddhist_text_by_chapters(text: str, *, target_chars: int = BUDDHIST_STORY_LONG_CHUNK_TARGET_CHARS) -> list[str]:
    if not text or not text.strip():
        return []
    chapters = _extract_buddhist_body_chapters(text)
    if not chapters:
        return _split_text_by_paragraph(text, target_chars=target_chars)
    body_match = re.search(r"(?m)^(?:！！！)?@?\s*正文\s*$", text.replace("\r\n", "\n").replace("\r", "\n"))
    prefix = text[: body_match.end()].strip() if body_match else ""
    rendered_chapters = [
        f"@{number}. {title}\n" + "\n".join(lines).strip()
        for number, title, lines in chapters
    ]
    target = max(800, int(target_chars or BUDDHIST_STORY_LONG_CHUNK_TARGET_CHARS))
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for chapter in rendered_chapters:
        chapter_len = len(chapter) + 2
        if current and current_len + chapter_len > target:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(chapter)
        current_len += chapter_len
    if current:
        chunks.append("\n\n".join(current))
    if prefix and chunks:
        chunks[0] = prefix + "\n" + chunks[0]
    return [chunk for chunk in chunks if chunk.strip()]


def _buddhist_segment_cache_path(
    output_dir: Path | None,
    *,
    segment_index: int,
    units: list[BuddhistStoryUnit],
) -> Path | None:
    if output_dir is None:
        return None
    source = "\n\n".join(unit.source for unit in units)
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    start = units[0].index
    end = units[-1].index
    return output_dir / "01_buddhist_story_segments" / f"{segment_index:04d}_U{start:04d}-U{end:04d}_{digest}.md"


async def _repair_buddhist_story_segment_once(
    markdown: str,
    *,
    units: list[BuddhistStoryUnit],
    output_dir: Path | None,
    segment_index: int,
) -> str:
    issues = _buddhist_story_manuscript_issues(markdown, units)
    if not issues:
        return markdown
    issue_text = "\n".join(f"- {issue}" for issue in issues)
    _dump_stage(output_dir, f"buddhist_story_segment_{segment_index:04d}_issues.txt", issue_text)
    prompt = f"""你是佛经白话故事稿的结构校对编辑。下面这一批故事单元已经生成，但存在章节缺失、顺序错误或故事展开不足。请只修复列出的问题。

【必须修复的问题】
{issue_text}

【硬规则】
- 只输出本批次正文，不要输出 @最终书名、@最终简介、@目录、@导言、@正文。
- 本批次必须严格输出这些正式章节：{", ".join(f"@{unit.index}. {unit.title}" for unit in units)}。
- 每个原文故事单元只能对应一个正式章节；不能拆成“故事章”和“道理章”。
- 每章都要有完整小故事形态：进入故事、人物动作或选择、后果显现、同章末尾自然收束寓意。
- 每章要尽量充分展开，常规写成 350 到 600 汉字左右，原文特别短也要尽量写到 250 汉字以上；不能只补一句道理或几句解释。
- 不得编造原文没有的人物、地点、关键事实、因果结论或新结局。
- 不得只写一句“这譬喻……”；道理必须接在故事之后自然落下。
- 不要重写本批次之外的章节。

【本批次原文故事单元】
{_buddhist_story_units_source_block(units)}

【待修复正文】
{markdown.strip()}
"""
    repaired = await _route_writer_call(prompt, max_output_tokens=0, retry_on_failure=False)
    body = _buddhist_story_segment_output_body(repaired)
    remaining = _buddhist_story_manuscript_issues(body, units)
    if remaining:
        raise RuntimeError(f"佛经白话分单元修复失败: {'；'.join(remaining)}")
    _dump_stage(output_dir, f"buddhist_story_segment_{segment_index:04d}_repaired.md", body)
    return body


def _buddhist_story_segment_output_body(markdown: str) -> str:
    chapters = _extract_buddhist_body_chapters(markdown)
    if not chapters:
        return (markdown or "").strip()
    parts: list[str] = []
    for number, title, lines in chapters:
        parts.append(f"@{number}. {title}\n" + "\n".join(line for line in lines if line.strip()).strip())
    return "\n\n".join(part.strip() for part in parts if part.strip()).strip()


def _buddhist_story_segment_prompt(
    *,
    units: list[BuddhistStoryUnit],
    all_units: list[BuddhistStoryUnit],
    original_title: object,
) -> str:
    final_title = buddhist_scripture_final_title(original_title)
    return f"""你是一名佛经白话小故事改写作者。现在处理的是长佛经中的一个连续故事单元批次，不是整本一次生成。

【全书信息】
- 最终书名：{final_title}
- 全书共 {len(all_units)} 个故事/说法单元；当前只写第 {units[0].index} 到第 {units[-1].index} 单元。
- 当前批次之外的单元不要写，也不要概括。

【输出范围】
- 只输出本批次 @正文 里的正式章节，不要输出 @最终书名、@最终简介、@目录、@导言、@正文。
- 必须严格按以下章节输出：{", ".join(f"@{unit.index}. {unit.title}" for unit in units)}。
- 每个原文故事单元只能对应一个正式章节，不能拆成“故事章”和“寓意章”。

【写作质量】
- 每章都要是一则完整的小故事：故事进入、人物动作或选择、结果变化、同章内自然收束道理。
- 每章要有足够展开，按“能把故事表达清楚”为准，尽量往充分写；常规每则写成 350 到 600 汉字左右，原文特别短也要尽量写到 250 汉字以上。
- 不能只写梗概，不能只有两三句话，不能只写“这譬喻……”。必须把原文压缩的动作、误判、后果和警醒顺成一则能读的小故事。
- 原文很短时，也要把原文压缩的动作、动机、误判、结果和警醒顺成一个可读故事；但不得编造原文没有的关键事实、人物、地点、结局或教义。
- 道理、寓意、修行提醒只能放在同一章末尾自然收束，不能另起正式章节。
- 语言平实、顺滑、有故事感，不要写成逐句翻译、术语讲义、鸡汤、玄幻故事或现代心理学文章。
- 遇到不确定的佛教术语、人名、地名和核心教义，保守处理，不要乱翻。

【本批次原文故事单元】
{_buddhist_story_units_source_block(units)}"""


async def _generate_buddhist_story_unit_writer(
    source_text: str,
    *,
    units: list[BuddhistStoryUnit],
    original_title: object,
    output_dir: Path | None,
    on_stage: StageCallback | None,
) -> str:
    if not units:
        raise ValueError("佛经白话故事单元为空，无法分单元生成")
    chunks = _buddhist_story_unit_chunks(units)
    logger.info(
        "佛经白话启用故事单元分批 writer source_chars=%d units=%d chunks=%d",
        len(source_text or ""),
        len(units),
        len(chunks),
    )
    frontmatter = _buddhist_story_frontmatter(units, original_title=original_title)
    bodies: list[str] = []
    completed_chars = len(frontmatter)
    for segment_index, segment_units in enumerate(chunks, start=1):
        cache_path = _buddhist_segment_cache_path(
            output_dir,
            segment_index=segment_index,
            units=segment_units,
        )
        body: str | None = None
        if cache_path and cache_path.exists():
            try:
                cached = cache_path.read_text(encoding="utf-8").strip()
                if cached and not _buddhist_story_manuscript_issues(cached, segment_units):
                    body = cached
                    logger.info("复用佛经白话故事分批缓存 %d/%d", segment_index, len(chunks))
            except Exception as exc:
                logger.warning("佛经白话故事分批缓存读取失败 %d/%d: %s", segment_index, len(chunks), exc)
        if body is None:
            prompt = _buddhist_story_segment_prompt(
                units=segment_units,
                all_units=units,
                original_title=original_title,
            )
            raw_segment = await _route_writer_call(prompt, max_output_tokens=0, retry_on_failure=False)
            body = _buddhist_story_segment_output_body(raw_segment)
            body = await _repair_buddhist_story_segment_once(
                body,
                units=segment_units,
                output_dir=output_dir,
                segment_index=segment_index,
            )
            if cache_path:
                try:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(body.strip(), encoding="utf-8")
                except Exception as exc:
                    logger.warning("佛经白话故事分批缓存写入失败 %d/%d: %s", segment_index, len(chunks), exc)
        bodies.append(body.strip())
        completed_chars += len(body)
        if on_stage:
            on_stage("writer_progress", {"chars": completed_chars, "story_unit": segment_units[-1].index})
    assembled = frontmatter.rstrip() + "\n" + "\n\n".join(bodies).strip()
    issues = _buddhist_story_manuscript_issues(assembled, units)
    if issues:
        raise RuntimeError(f"佛经白话故事单元组装校验失败: {'；'.join(issues)}")
    return assembled


_LIFE_PHILOSOPHY_CENTERED_SECTIONS = {"三个备选书名", "三个备选简介", "目录", "导言", "正文"}


def _apply_life_philosophy_delivery_markers(text: str) -> str:
    lines: list[str] = []
    in_body = False
    for raw_line in (text or "").split("\n"):
        stripped = raw_line.strip()
        section = _FRONT_SECTION_RE.match(stripped)
        if section:
            section_name = section.group(1)
            in_body = section_name == "正文"
            if section_name in _LIFE_PHILOSOPHY_CENTERED_SECTIONS:
                lines.append(f"！！！@{section_name}")
            else:
                lines.append("@" + section_name)
            continue

        if in_body:
            chapter = re.match(r"^(?:！！！@?|@)\s*(\d+)[.．、]\s*(.+?)\s*$", stripped)
            if chapter:
                title = _strip_title_colons(chapter.group(2).strip())
                lines.append(f"！！！{chapter.group(1)}. {title or f'第{chapter.group(1)}章'}")
                continue

        lines.append(raw_line)
    return "\n".join(lines)


def normalize_final_markdown(markdown: str, *, category: str | None = None) -> str:
    """Remove blank spacer lines only from the body, preserving front matter layout."""
    text = _postprocess_delivery_markdown(markdown).replace("\r\n", "\n").replace("\r", "\n")
    text = _repair_blank_catalog_titles(text)
    text = _remove_intro_duplicate_from_body(text)
    normalized = normalize_category(category or "")
    match = re.search(r"(?m)^(?:！！！)?@?\s*正文\s*$", text)
    if not match:
        lines = [line.rstrip() for line in text.split("\n")]
        compact = [line for line in lines if line.strip()]
        result = "\n".join(compact).strip()
        if normalized in FINANCE_SOURCE_GUARDED_CATEGORIES:
            result = _clean_finance_source_leaks(result).strip()
        return result + ("\n" if result else "")

    front = text[: match.end()]
    body = text[match.end() :]
    front_lines = [line.rstrip() for line in front.split("\n")]
    if normalized in CLASSICAL_CHINESE_CATEGORY_IDS and normalized != BUDDHIST_SCRIPTURE_STORY_CATEGORY:
        front_lines = [strip_classical_paragraph_markers(line) for line in front_lines]
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
    if normalized in FINANCE_SOURCE_GUARDED_CATEGORIES:
        result = _clean_finance_source_leaks(result).strip()
    if normalized == "life_philosophy":
        result = _apply_life_philosophy_delivery_markers(result).strip()
    return result + ("\n" if result else "")


def enforce_classical_title(markdown: str, original_title: object) -> str:
    final_title = classical_final_title(original_title)
    title_re = re.compile(
        r"^(?:！！！)?@?\s*(最终书名|三个备选书名)\s*$[\s\S]*?(?=^(?:！！！)?@?\s*(?:"
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


_NON_REPAIRABLE_FINAL_HARD_ERROR_NAMES = {
    "《百喻经》章节覆盖不足",
    "《百喻经》章节数量异常",
}


def _has_non_repairable_final_hard_errors(issues: dict[str, list[str]]) -> bool:
    return any(name in issues for name in _NON_REPAIRABLE_FINAL_HARD_ERROR_NAMES)


async def _repair_final_hard_errors_once(
    markdown: str,
    *,
    category: str | None,
    output_dir: Path | None,
    stage: str = "format",
) -> str:
    from app.core.quality_metrics import detect_final_hard_errors, format_final_hard_error_hits

    normalized_category = normalize_category(category or "")
    issues = detect_final_hard_errors(markdown, category=normalized_category)
    if not issues:
        return markdown

    issue_text = format_final_hard_error_hits(issues)
    logger.warning("终稿硬伤闸门触发 stage=%s category=%s: %s", stage, normalized_category, issue_text)
    _dump_stage(output_dir, "final_hard_error_issues.txt", issue_text)
    if _has_non_repairable_final_hard_errors(issues):
        raise RuntimeError(f"终稿硬伤质检失败，需重跑完整生成链路: {issue_text}")

    repair_prompt = f"""你是中文长文终稿硬伤质检编辑。下面是一篇已经完成全部生成、润色、精修和格式化的终稿，但仍命中了高置信硬伤。请只修复列出的问题，不要重写文章。

【必须修复的问题】
{issue_text}

【修复规则】
- 只修复上面列出的语义搭配、伪小标题、残留提示词、重复标题或空块硬伤。
- 语义搭配要按中文常识修正，例如“菜/饭菜/菜肴好喝”应改成“好吃”或“味道好”，“汤/茶/水好吃”应改成“好喝”或等价自然表达。
- 正文内部小标题只能使用“###短标题”单独占一行；不能保留“@短标题”、编号伪标题、裸短标题或“短标题，正文内容”这种混排。
- 如果命中佛经白话章节过短、故事和寓意被拆章或《百喻经》章节数量异常，必须把同一则譬喻的“故事章”和“道理章/寓意章/启示章”合并为一个正式章节；每章内部先讲完整故事，再在同章末尾自然收束道理，不得保留只有一句道理的独立章节。
- 如果是《百喻经》全本，正文正式章节应按九十八则譬喻组织，不能生成一百九十多章、二百章或二百零四章。
- 删除任务说明、审稿批注、提示词残留和模型解释语。
- 不得新增故事情节、观点、事实、人物、章节或参考资料。
- 不得改变 @最终书名、@最终简介、@目录、@导言、@正文 的顺序和名称。
- 不得改变章节顺序、段落主旨、文风和字数规模。
- 只输出修复后的完整终稿，不要解释，不要列清单。

【待修复终稿】
{markdown.strip()}
"""
    repaired = await _route_writer_call(
        repair_prompt,
        max_output_tokens=0,
        retry_on_failure=False,
    )
    repaired = normalize_final_markdown(repaired, category=normalized_category)
    _dump_stage(output_dir, "semantic_quality_repaired.md", repaired)

    remaining = detect_final_hard_errors(repaired, category=normalized_category)
    if remaining:
        remaining_text = format_final_hard_error_hits(remaining)
        _dump_stage(output_dir, "final_hard_error_remaining.txt", remaining_text)
        raise RuntimeError(f"终稿硬伤质检失败，修复后仍命中: {remaining_text}")
    return repaired


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


def _split_text_by_classical_marker_lines(text: str, *, target_chars: int = 1000) -> list[str]:
    """Split classical drafts by physical lines so P-numbered paragraphs stay whole."""
    lines = [line.rstrip() for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    lines = [line for line in lines if line.strip()]
    if not lines:
        return []

    target = max(300, int(target_chars or 1000))
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        line_len = len(line) + 1
        if current and current_len + line_len > target:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len
        if current_len >= target:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
    if current:
        chunks.append("\n".join(current))
    return [chunk for chunk in chunks if chunk.strip()]


def _split_text_for_max_tokens_retry(text: str, *, target_chars: int) -> list[str]:
    retry_target = max(300, min(700, target_chars // 2 if target_chars > 0 else 500))
    return _split_text_by_paragraph(text, target_chars=retry_target)


def _classical_chunk_marker_issues(source_chunk: str, output_chunk: str) -> list[str]:
    source_markers = [int(item) for item in CLASSICAL_PARAGRAPH_MARKER_RE.findall(source_chunk or "")]
    if not source_markers:
        return []
    output_markers = [int(item) for item in CLASSICAL_PARAGRAPH_MARKER_RE.findall(output_chunk or "")]
    if source_markers == output_markers:
        return []
    issues: list[str] = []
    missing = [marker for marker in source_markers if marker not in output_markers]
    extra = [marker for marker in output_markers if marker not in source_markers]
    if missing:
        issues.append("缺少 P 编号：" + ", ".join(_classical_marker_label(item) for item in missing[:8]))
    if extra:
        issues.append("出现本块外 P 编号：" + ", ".join(_classical_marker_label(item) for item in extra[:8]))
    if not missing and not extra:
        issues.append("P 编号顺序被改动")
    return issues


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
    splitter: Callable[..., list[str]] | None = None,
    chunk_validator: Callable[[str, str], list[str]] | None = None,
) -> str:
    """Quality-safe Gemini polish with per-chunk resume cache.

    The old path allowed failed chunks to fall back to raw text. That keeps tasks
    moving, but it can quietly lower manuscript quality under high concurrency.
    This path caches successful chunks and raises a retryable error when any
    chunk fails, so Celery can retry only the missing pieces on the next run.
    """
    if not text or not text.strip():
        return text
    split_func = splitter or _split_text_by_paragraph
    chunks = split_func(text, target_chars=target_chars)
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
            if not content:
                return None
            if chunk_validator:
                validation_issues = chunk_validator(chunk, content)
                if validation_issues:
                    logger.warning(
                        "%s cache invalid chunk=%d: %s",
                        log_label,
                        idx + 1,
                        "; ".join(validation_issues),
                    )
                    return None
            return content
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
            retry_target = max(
                300,
                min(700, current_target_chars // 2 if current_target_chars > 0 else 500),
            )
            retry_chunks = (
                _split_text_for_max_tokens_retry(chunk, target_chars=current_target_chars)
                if splitter is None
                else split_func(chunk, target_chars=retry_target)
            )
            if len(retry_chunks) <= 1:
                raise

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
                if chunk_validator:
                    validation_issues = chunk_validator(chunk, result)
                    if validation_issues:
                        raise RuntimeError("; ".join(validation_issues))
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
    splitter: Callable[..., list[str]] | None = None,
    chunk_validator: Callable[[str, str], list[str]] | None = None,
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
        splitter=splitter,
        chunk_validator=chunk_validator,
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


def _finance_advice_dynamic_target_words(
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
    if minutes <= 10:
        base_target = max(
            FINANCE_LONGFORM_MIN_TARGET_WORDS,
            int(round(minutes / 10.0 * FINANCE_ADVICE_TARGET_10_MIN_WORDS)),
        )
    elif minutes <= 20:
        base_target = int(
            round(
                FINANCE_ADVICE_TARGET_10_MIN_WORDS
                + (minutes - 10.0)
                / 10.0
                * (FINANCE_ADVICE_TARGET_20_MIN_WORDS - FINANCE_ADVICE_TARGET_10_MIN_WORDS)
            )
        )
    elif minutes < 30:
        base_target = int(
            round(
                FINANCE_ADVICE_TARGET_20_MIN_WORDS
                + (minutes - 20.0)
                / 10.0
                * (FINANCE_ADVICE_BASE_TARGET_WORDS - FINANCE_ADVICE_TARGET_20_MIN_WORDS)
            )
        )
    elif minutes <= 60:
        base_target = FINANCE_ADVICE_BASE_TARGET_WORDS
    else:
        base_target = max(
            FINANCE_ADVICE_BASE_TARGET_WORDS,
            int(round(FINANCE_ADVICE_BASE_TARGET_WORDS + (minutes - 60.0) / 60.0 * 8_000)),
        )
    density_multiplier = min(1.05, _finance_content_density_multiplier(raw_text, minutes))
    duration_target = int(round(base_target * density_multiplier))
    source_target = max(
        FINANCE_LONGFORM_MIN_TARGET_WORDS,
        int(round(len((raw_text or "").strip()) * FINANCE_ADVICE_SOURCE_EXPANSION_MULTIPLIER)),
    )
    target = min(duration_target, source_target)
    return max(FINANCE_LONGFORM_MIN_TARGET_WORDS, target)


def _finance_required_body_target(target_words: int, *, category: str | None = None) -> int:
    ratio = FINANCE_ADVICE_WRITER_MIN_TARGET_RATIO if _is_finance_advice_category(category) else 1.0
    return max(1, int(math.ceil(int(target_words) * ratio)))


def _should_use_finance_longform(
    category: str,
    raw_text: str,
    *,
    source_duration_seconds: int | float | None = None,
) -> bool:
    try:
        normalized = normalize_category(category)
    except Exception:
        return False
    if normalized not in FINANCE_LONGFORM_CATEGORIES:
        return False
    return True


def _is_finance_advice_category(category: str | None) -> bool:
    return False


def _is_finance_recommendation_category(category: str | None) -> bool:
    try:
        return normalize_category(category) == FINANCE_TECHNOLOGY_RECOMMENDATION_CATEGORY
    except Exception:
        return False


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


_FINANCE_SOURCE_OBJECT_RE = (
    r"这段文稿|这份材料|这份素材|这份资料|原始视频文稿|视频材料|视频文稿|原视频|"
    r"公开资料|参考资料|参考材料|输入材料|视频|文稿|材料|素材|资料"
)
_FINANCE_SOURCE_LEAK_RE = re.compile(
    rf"(?P<lead>^|[。！？；，,、\s])"
    rf"(?:根据|据|按照)?\s*"
    rf"(?P<object>{_FINANCE_SOURCE_OBJECT_RE})"
    rf"(?:中|里)?"
    rf"(?:提到|提及|指出|显示|写到|说到|说|认为|表明|给出|呈现|说明)"
    rf"(?:[，,、：:\s]*(?P<source>《[^》]{{1,80}}》))?"
    rf"[，,、：:\s]*"
)
_FINANCE_NAMED_SOURCE_LEAK_RE = re.compile(
    r"(?P<lead>^|[。！？；，,、\s])"
    r"(?:根据|据|按照)?\s*"
    r"(?P<source>《[^》]{1,80}》)"
    r"(?:中|里)?"
    r"(?:提到|提及|指出|显示|写到|说到|说|认为|表明|给出|呈现|说明)"
    r"[，,、：:\s]*"
)


def _finance_nearby_named_source(line: str, start: int, end: int) -> str | None:
    window = line[max(0, start - 40) : min(len(line), end + 80)]
    match = re.search(r"《[^》]{1,80}》", window)
    return match.group(0) if match else None


def _clean_finance_source_leak_line(line: str) -> str:
    stripped = line.strip()
    if (
        not stripped
        or _FRONT_SECTION_RE.match(stripped)
        or _is_chapter_heading_line(stripped)
        or _is_image_markdown_line(stripped)
    ):
        return line

    def replace(match: re.Match[str]) -> str:
        source = match.group("source") or _finance_nearby_named_source(
            line,
            match.start(),
            match.end(),
        )
        attribution = f"根据{source}" if source else "根据公开资料"
        return f"{match.group('lead')}{attribution}，"

    cleaned = _FINANCE_NAMED_SOURCE_LEAK_RE.sub(
        lambda match: f"{match.group('lead')}根据{match.group('source')}，",
        line,
    )
    cleaned = _FINANCE_SOURCE_LEAK_RE.sub(replace, cleaned)
    cleaned = re.sub(r"(根据公开资料，)\s*(?:根据公开资料，)+", r"\1", cleaned)
    cleaned = re.sub(r"(根据《[^》]{1,80}》，)\s*(?:根据《[^》]{1,80}》，)+", r"\1", cleaned)
    cleaned = re.sub(r"，\s*，+", "，", cleaned)
    return cleaned.rstrip()


def _clean_finance_source_leaks(text: str) -> str:
    return "\n".join(_clean_finance_source_leak_line(line) for line in (text or "").split("\n"))


def _finance_source_focus_points(raw_text: str, *, max_points: int = 40) -> str:
    chunks = _split_long_text_by_sentence(raw_text or "", target_chars=260)
    points = [chunk.strip() for chunk in chunks if chunk.strip()]
    if not points:
        return ""
    selected = points[: max(1, max_points)]
    return "\n".join(f"- {item}" for item in selected)


def _finance_web_search_excerpt(raw_text: str, *, max_chars: int = 12_000) -> str:
    text = re.sub(r"\s+", " ", (raw_text or "").strip())
    if len(text) <= max_chars:
        return text
    head = text[: int(max_chars * 0.65)].strip()
    tail = text[-int(max_chars * 0.35) :].strip()
    return f"{head}\n\n……\n\n{tail}"


def _finance_latest_research_prompt(raw_text: str, *, category: str) -> str:
    excerpt = _finance_web_search_excerpt(raw_text)
    if _is_finance_recommendation_category(category):
        category_label = "财经科技（建议版）"
    else:
        category_label = "财经·科技"
    recommendation_guard = ""
    if _is_finance_recommendation_category(category):
        recommendation_guard = """

财经科技（建议版）额外要求：
1. 检索结果只作为正文的事实核验和论据边界，不作为可铺陈的写作材料库。
2. 最多输出 5 条可使用的最新例子；每条都必须能支撑一个明确判断、条件边界或具体建议。不能支撑判断的资料不要收录。
3. 不要为了显得信息量大而罗列公司名、年份、机构名、政策名、技术名或数据口径。
4. 每条资料必须写清“适用主题”和“使用边界”，提醒正文只能展开一次，不得在多个章节反复使用。
"""
    return f"""你是财经科技长文写作前的联网事实检索员。

任务：请调用联网搜索，围绕下方参考资料，查找可用于 {category_label} 长文的 2026 年或当前时点最新例子。

硬性要求：
1. 必须执行联网搜索，不得只凭模型知识回答。
2. 只收录 2026 年或明确属于当前时点的资料。资料必须有日期、来源名称、事件主体和可核对事实。
3. 对于“当前、时下、2026年、今年、最新、近期、前景预估、趋势判断、行业机会、产业变化”等判断，只允许使用本次检索到的最新例子。
4. 如果某个主题没有检索到 2026 年或明确当前的最新例子，明确写“未检索到 2026 年最新例子”，并说明不要写该主题的最新例子。
5. 不要用 2025 年、2024 年、2023 年或更早案例替代最新例子；过去案例只能标为历史背景或机制对照。
6. 不要编造来源、日期、数据或公司动作。找不到就写找不到。
{recommendation_guard}

输出格式：
【财经科技联网检索结果】
- 检索结论：写明哪些主题有 2026 年或当前最新例子，哪些主题没有。
- 可使用的最新例子：
  1. 日期｜来源｜主体｜事实｜适用主题｜使用边界
  2. 日期｜来源｜主体｜事实｜适用主题｜使用边界
- 不得作为最新例子的旧资料：
  1. 年份｜来源或主体｜只能作为历史背景或机制对照的原因
- 禁写提醒：列出没有检索到最新例子的主题，提醒正文不要退而求其次使用过去几年案例。

[参考资料摘要]
{excerpt}
"""


def _finance_latest_research_block(latest_research: str | None) -> str:
    research = (latest_research or "").strip()
    if not research:
        research = "【财经科技联网检索结果】\n- 检索结论：未检索到可用的 2026 年或当前最新例子。涉及当前前景、最新趋势或近期案例时不要写具体例子。"
    return f"""{FINANCE_CURRENT_WEB_EVIDENCE_GUARD_PROMPT}

[财经科技联网检索结果]
{research}
"""


def _append_finance_recommendation_research_prompt(
    writer_prompt: str,
    latest_research: str | None,
) -> str:
    return f"""{writer_prompt.rstrip()}

【财经科技建议版联网事实资料】
{FINANCE_SOURCE_ATTRIBUTION_GUARD_PROMPT}
{_finance_latest_research_block(latest_research)}
- 上方“财经科技（建议版）”主 prompt 仍然完整生效且优先级最高；本段只补充事实核验与最新论据边界，不改变主 prompt 的写作结构、标题结构、约12000字目标和建议要求。
- 上方联网检索结果只能作为论据，不作为材料库。正文不得把联网结果写成资料清单、公司清单、年份清单、政策清单或技术名词清单。
- 约12000字目标仍需完成，但只能靠背景解释、机制拆解、利益关系、影响链条、条件推演、现实误判、风险边界和建议深化完成；不得为了使用联网资料而扩写资料清单。
- 如果正文需要引用资料、数据、公司动作、政策变化、技术进展、市场变化或当前例子，只能选择最能支撑核心判断的少量资料。每条资料必须回答一个问题：它支撑了哪个判断，改变了谁的理解，导向哪条建议边界。
- 同一条联网事实、同一组数据、同一个公司动作或同一项政策背景，在全文中最多完整展开一次；后文如需承接，只能用一句话承接结论或影响，不得重复复述背景、过程、数字和来源。
- 建议小节不得重新复述联网资料。建议小节只负责把前文判断转成具体、可执行、有适用条件的行动边界。
- 如果联网检索没有提供 2026 年或明确当前时点的最新例子，就不要写当前前景、最新趋势或近期案例，也不要退而求其次使用过去几年案例冒充最新例子。
- 不要编造来源、日期、数据、公司动作或政策变化；不确定的事实宁可不写。
- 正式成稿不得出现“联网检索”“搜索结果”“API”“参考资料显示”“视频材料提到”等创作过程词。
"""


def _openai_finance_web_search_call(prompt: str) -> str:
    if not _resolve_openai_api_key():
        raise RuntimeError("财经科技联网检索需要 OPENAI_API_KEY，不能在没有联网检索的情况下生成财经科技稿件")

    model = (settings.FINANCE_WEB_SEARCH_MODEL or settings.OPENAI_WRITER_MODEL or "gpt-5.5").strip()
    context_size = (settings.FINANCE_WEB_SEARCH_CONTEXT_SIZE or "medium").strip().lower()
    if context_size not in {"low", "medium", "high"}:
        context_size = "medium"

    response = _get_openai_writer_client().responses.create(
        model=model,
        input=prompt,
        reasoning={"effort": "low"},
        tools=[
            {
                "type": "web_search",
                "search_context_size": context_size,
            }
        ],
        tool_choice="required",
        include=["web_search_call.action.sources"],
    )
    text = _extract_openai_response_text(response)
    if not text.strip():
        raise RuntimeError(f"财经科技联网检索无返回内容: {_openai_response_debug_summary(response)}")
    return text.strip()


async def _finance_latest_research_pack(
    raw_text: str,
    *,
    category: str,
    output_dir: Path | None,
    on_stage: StageCallback | None = None,
) -> str:
    if _is_finance_recommendation_category(category):
        raise RuntimeError("财经科技（建议版）已关闭联网检索，必须只按专属主 prompt 和用户提供资料生成")

    cached = _read_stage_file(output_dir, FINANCE_LATEST_RESEARCH_STAGE_FILE)
    if cached:
        logger.info("复用已存在的财经科技联网检索结果 %s", FINANCE_LATEST_RESEARCH_STAGE_FILE)
        return cached

    prompt = _finance_latest_research_prompt(raw_text, category=category)
    _dump_stage(output_dir, FINANCE_LATEST_RESEARCH_PROMPT_FILE, prompt)
    if on_stage:
        on_stage("finance_web_search_start", {"category": category})

    try:
        research = await _to_thread_with_optional_timeout(
            _openai_finance_web_search_call,
            prompt,
            timeout_seconds=settings.FINANCE_WEB_SEARCH_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        _dump_stage(output_dir, "00_finance_latest_research_error.txt", f"{exc.__class__.__name__}: {exc}")
        raise RuntimeError(f"财经科技分类必须完成 API 联网检索后才能生成：{exc}") from exc

    if not research.strip():
        raise RuntimeError("财经科技分类 API 联网检索返回空结果，已停止生成以避免使用过期资料")

    _dump_stage(output_dir, FINANCE_LATEST_RESEARCH_STAGE_FILE, research)
    if on_stage:
        on_stage("finance_web_search_done", {"chars": len(research)})
    return research.strip()


def _finance_whole_book_prompt(
    base_writer_prompt: str,
    raw_text: str,
    *,
    target_words: int,
    duration_seconds: int | None,
    latest_research: str | None = None,
    category: str | None = None,
) -> str:
    minutes = round((duration_seconds or _finance_duration_seconds_from_text(raw_text)) / 60, 1)
    source_cpm = int(round(len((raw_text or "").strip()) / max(minutes, 1.0)))
    required_body_target = _finance_required_body_target(target_words, category=category)
    focus_points = _finance_source_focus_points(raw_text)
    focus_block = f"\n[参考资料信息点观察清单]\n{focus_points}\n" if focus_points else ""
    latest_research_block = _finance_latest_research_block(latest_research)
    return f"""{base_writer_prompt.rstrip()}

【财经科技长视频动态长文强约束】
{FINANCE_SOURCE_ATTRIBUTION_GUARD_PROMPT}
{latest_research_block}
- 以上财经科技分类 prompt 全部继续生效；本段只增加长视频篇幅、质量和链路约束，不替换原分类规则。
- 必须一次性输出完整书稿，不要先写蓝图，不要分章分批生成，不要把正文留给后续补写。
- 这里的“一次性”指本轮直接整稿生成；成稿内部仍然必须按 @目录 和 @正文 组织章节。
- 参考资料对应时长约 {minutes} 分钟，参考资料信息密度约每分钟 {source_cpm} 字，建议正文字数目标约 {target_words} 字；这是写作目标和篇幅引导，不是后处理硬截断，最终以参考资料内容密度、逻辑完整和不灌水为准。
- 如果参考资料对应时长约 20 分钟，正文尽量写到 35000 字以上；如果参考资料对应时长 30 分钟以上，正文尽量接近或超过 50000 字；超过 60 分钟继续按内容量上探，不设硬封顶。
- 如果实际可写篇幅低于目标，优先保证完整结构、核心信息点覆盖、论证密度和语言质量，不要回头重写已完成内容，不要为了达标堆砌无关信息。
- 必须按参考资料中的事实与信息点展开，围绕其中的事实、数字、概念、事件、公司动作、产业变化、政策条件和市场语境组织。
- 禁止重复观点，禁止为了凑字数堆无关信息，禁止把同一判断换说法，禁止空泛铺陈和概念堆砌。
- 扩写只能来自内容价值：背景解释、机制拆解、影响链条、利益相关方、条件推演、现实误判、风险边界和普通人会受到的具体影响。
- 不做具体投资预测，不写涨跌结论，不给买卖建议；所有判断只能写成条件、机制、约束和可能影响。
- 输出顺序仍然只能是 @最终书名、@最终简介、@目录、@导言、@正文；只输出成稿，不要解释，不要代码块，不要元信息。
{focus_block}
请严格按照完整财经科技分类 prompt 和以上动态长文强约束，一次性输出完整书稿。
"""


def _finance_stage_instruction(
    base_instruction: str,
    stage_name: str,
    *,
    category: str | None = None,
) -> str:
    return f"""{base_instruction.rstrip()}

【财经科技超长文整稿处理保护】
{FINANCE_SOURCE_ATTRIBUTION_GUARD_PROMPT}
{FINANCE_CURRENT_WEB_EVIDENCE_GUARD_PROMPT}
- 当前环节只执行原有 workflow 的对应处理，不改变分工，不重写成新稿，不改成摘要。
- 你会收到待处理完整书稿，必须从 @最终书名 处理到最后一行正文，不能只处理开头、前半部分或前 20000 字。
- 必须保留完整正文、完整章节、完整段落和全部参考资料信息点；不得因为篇幅长而删段、合并章节、跳过案例或压缩论证链条。
- 输出应与输入篇幅基本一致，允许必要的局部润色和格式整理，但不能把长稿压成短稿。
- 如果某些段落已经符合要求，原样保留即可，优先保证内容密度、正式书稿语感和财经科技分类规则不被破坏。
- 不得新增任何未在原稿中出现的“当前、时下、2026年、最新、近期、前景预估”例子；不得把 2025 年及更早资料改写成当前最新例子。
"""


def _finance_format_template(base_template: str, *, category: str | None = None) -> str:
    guard = f"""【财经科技超长文整稿格式保护】
{FINANCE_SOURCE_ATTRIBUTION_GUARD_PROMPT}
{FINANCE_CURRENT_WEB_EVIDENCE_GUARD_PROMPT}
- 你会收到待处理完整书稿，必须完整读取并整理全部内容，从 @最终书名 到最后一行正文都要处理。
- 本阶段只做格式整理和标题规范，不得摘要、缩写、删减、合并章节、重排论证或压缩正文。
- 输出应保留完整篇幅和全部参考资料信息点，不能只输出前半部分或前 20000 字。
- 不得新增任何未在原稿中出现的“当前、时下、2026年、最新、近期、前景预估”例子；不得把 2025 年及更早资料改写成当前最新例子。
"""
    template = base_template.rstrip()
    marker = "\n【文本】"
    if marker in template:
        return template.replace(marker, f"\n{guard}\n{marker}", 1)
    return f"{template}\n\n{guard}"


def _finance_body_target_reached(
    markdown: str,
    target_words: int,
    *,
    category: str | None = None,
) -> bool:
    return _finance_body_chars(markdown) >= _finance_required_body_target(
        target_words,
        category=category,
    )


def _finance_final_resume_reusable(
    markdown: str,
    target_words: int,
    *,
    category: str | None = None,
) -> bool:
    if not _finance_body_target_reached(markdown, target_words, category=category):
        return False
    if not _is_finance_advice_category(category):
        return True
    return _finance_body_chars(markdown) <= int(
        max(target_words, 1) * FINANCE_ADVICE_OUTPUT_MAX_TARGET_RATIO
    )


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
    if re.fullmatch(r"\s*(?:无新增有效内容|没有新增有效内容|无需新增有效内容)[。.\s]*", fragment_body or ""):
        return markdown
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
    latest_research: str | None = None,
    category: str | None = None,
) -> str:
    current_body_chars = _finance_body_chars(current_markdown)
    required_body_target = _finance_required_body_target(target_words, category=category)
    remaining = max(0, required_body_target - current_body_chars)
    minutes = round(duration_seconds / 60.0, 1)
    focus_points = _finance_source_focus_points(raw_text, max_points=55)
    chapter_map = _finance_existing_chapter_map(current_markdown)
    return f"""{base_writer_prompt.rstrip()}

【财经科技长文同阶段定向续写任务】
{FINANCE_SOURCE_ATTRIBUTION_GUARD_PROMPT}
{_finance_latest_research_block(latest_research)}
当前仍属于初稿补写环节，不是重写任务，不是润色任务，也不是格式整理。
你会看到参考资料全文、当前已完成书稿和已有章节目录。请只输出“新增正文正式章节”，新增章节会拼回 @正文 并重建目录。

【当前篇幅状态】
- 参考资料对应时长约 {minutes} 分钟。
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
- 每个新增章节都必须围绕参考资料中的事实与信息点展开，并提供新增内容价值：背景解释、机制拆解、影响链条、利益相关方、条件推演、现实误判或风险边界。
- 如果需要写当前前景、2026 年趋势、最新产业例子或近期公司动作，只能使用上方联网检索结果里已有的 2026 年或明确当前资料；如果没有检索到，不要补写这类例子，也不要使用过去几年案例替代。
- 不能为了凑字数堆无关知识，不能写百科，不能写研究报告，不能写投资建议。
- 不做具体投资预测，不写涨跌结论，不给买卖建议。
- 语言仍然要保持财经科技分类原 prompt 的正式书稿质量，不要口语化，不要论文腔，不要总结腔。

【输出格式】
- 只输出新增的正文正式章节，不要输出 @最终书名、@最终简介、@目录、@导言、@正文。
- 每个新增章节必须以 @N. 章节名 开头，N 可从 1 开始，章节编号会在最终正文中统一重排。
- 章节标题不得出现中文冒号或英文冒号，不要命名为“结语”。
- 不要解释，不要代码块，不要元信息，不要输出“续写如下”。

[已有章节目录]
{chapter_map}

[参考资料信息点观察清单]
{focus_points}

[参考资料全文]
{raw_text.strip()}

[当前已完成书稿]
{current_markdown.strip()}
"""


async def _finance_expand_writer_to_target(
    current_markdown: str,
    *,
    base_writer_prompt: str,
    raw_text: str,
    latest_research: str | None,
    target_words: int,
    duration_seconds: int,
    category: str,
    output_dir: Path | None,
    on_stage: StageCallback | None,
) -> tuple[str, bool]:
    current_markdown = normalize_final_markdown(current_markdown, category=category)
    current_body_chars = _finance_body_chars(current_markdown)
    if _finance_body_target_reached(current_markdown, target_words, category=category):
        return current_markdown, False

    required_body_target = _finance_required_body_target(target_words, category=category)
    deficit = max(0, required_body_target - current_body_chars)
    is_advice = _is_finance_advice_category(category)
    max_rounds = (
        FINANCE_ADVICE_MAX_CONTINUATION_ROUNDS
        if is_advice
        else max(
            1,
            int(math.ceil(deficit / FINANCE_LONGFORM_CONTINUATION_BATCH_TARGET)) + 2,
        )
    )
    expanded = False
    no_gain_rounds = 0

    for round_index in range(1, max_rounds + 1):
        if _finance_body_target_reached(current_markdown, target_words, category=category):
            break
        before_body_chars = _finance_body_chars(current_markdown)
        remaining = max(0, required_body_target - before_body_chars)
        if is_advice:
            batch_target = max(
                2_000,
                min(FINANCE_ADVICE_CONTINUATION_BATCH_TARGET, remaining + 1_000),
            )
        else:
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
            latest_research=latest_research,
            category=category,
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
        current_markdown = normalize_final_markdown(current_markdown, category=category)
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

    if not _finance_body_target_reached(current_markdown, target_words, category=category):
        raise RuntimeError(
            f"{FINANCE_LONGFORM_NO_RETRY_ERROR_PREFIX}: "
            f"finance writer could not reach required target without quality risk; "
            f"body_chars={_finance_body_chars(current_markdown)} "
            f"required={required_body_target} target={target_words}"
        )
    return current_markdown, expanded


def _finance_stage_looks_compressed(source: str, candidate: str, *, category: str | None = None) -> bool:
    if not source or len(source) < 10_000:
        return False
    if not candidate or not candidate.strip():
        return True
    if _front_matter_missing(candidate):
        return True
    source_body_chars = _finance_body_chars(source)
    candidate_body_chars = _finance_body_chars(candidate)
    retain_ratio = (
        FINANCE_ADVICE_STAGE_MIN_RETAIN_RATIO
        if _is_finance_advice_category(category)
        else FINANCE_LONGFORM_STAGE_MIN_RETAIN_RATIO
    )
    if source_body_chars >= 10_000 and candidate_body_chars < max(
        5000,
        int(source_body_chars * retain_ratio),
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
    category: str,
    prompts: dict[str, str],
    output_dir: Path | None,
    on_stage: StageCallback | None,
    source_duration_seconds: int | float | None,
) -> str:
    is_advice = _is_finance_advice_category(category)
    duration_seconds = _positive_int(source_duration_seconds) or _finance_duration_seconds_from_text(raw_text)
    target_words = (
        _finance_advice_dynamic_target_words(
            raw_text,
            source_duration_seconds=duration_seconds,
        )
        if is_advice
        else _finance_dynamic_target_words(
            raw_text,
            source_duration_seconds=duration_seconds,
        )
    )
    required_body_target = _finance_required_body_target(target_words, category=category)
    metadata = {
        "targetWords": target_words,
        "requiredWriterBodyChars": required_body_target,
        "writerMinimumRatio": FINANCE_ADVICE_WRITER_MIN_TARGET_RATIO if is_advice else 1.0,
        "maxReusableFinalBodyChars": (
            int(target_words * FINANCE_ADVICE_OUTPUT_MAX_TARGET_RATIO)
            if is_advice
            else None
        ),
        "durationSeconds": duration_seconds,
        "targetMode": (
            "viewpoint_advice_density_bounded_quality_target"
            if is_advice
            else "writer_stage_expand_to_dynamic_target"
        ),
        "stageMinRetainRatio": (
            FINANCE_ADVICE_STAGE_MIN_RETAIN_RATIO
            if is_advice
            else FINANCE_LONGFORM_STAGE_MIN_RETAIN_RATIO
        ),
        "mode": (
            "viewpoint_advice_whole_workflow_quality_bounded_single_writer_continuation"
            if is_advice
            else "whole_workflow_with_writer_stage_continuations"
        ),
        "adviceDedup": {
            "enabled": is_advice,
            "rule": "facts_must_serve_judgment_or_advice_no_material_inventory",
        },
        "latestResearch": {
            "required": True,
            "stageFile": FINANCE_LATEST_RESEARCH_STAGE_FILE,
            "promptFile": FINANCE_LATEST_RESEARCH_PROMPT_FILE,
            "currentExamplesRule": "2026_or_current_only_no_older_fallback",
        },
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

    stale_final_resume = False
    final_resume_markdown = _read_stage_file(output_dir, "04_format.md")
    if final_resume_markdown:
        if _finance_final_resume_reusable(final_resume_markdown, target_words, category=category):
            logger.info("复用已存在的财经长文 04_format.md")
            final_resume_markdown = normalize_final_markdown(final_resume_markdown, category=category)
            final_resume_markdown = await _repair_final_hard_errors_once(
                final_resume_markdown,
                category=category,
                output_dir=output_dir,
                stage="finance_resume",
            )
            _dump_stage(output_dir, "04_format.md", final_resume_markdown)
            _log_stage("finance 4/4 format (resume)", final_resume_markdown)
            if on_stage:
                on_stage("format_done", {"chars": len(final_resume_markdown)})
            return final_resume_markdown
        logger.warning(
            "已有财经长文 04_format.md 不在当前质量区间内，忽略终稿并回到中间阶段处理: body_chars=%d target=%d",
            _finance_body_chars(final_resume_markdown),
            target_words,
        )
        _dump_stage(output_dir, "04_format_stale_short.md", final_resume_markdown)
        stale_final_resume = True

    resume_writer_markdown = _read_stage_file(output_dir, "01_writer.md")
    resume_polished_markdown = _read_stage_file(output_dir, "02_polish.md")
    resume_refined_markdown = _read_stage_file(output_dir, "03_refine.md")
    if stale_final_resume and resume_writer_markdown:
        resume_polished_markdown = None
        resume_refined_markdown = None
    latest_research = await _finance_latest_research_pack(
        raw_text,
        category=category,
        output_dir=output_dir,
        on_stage=on_stage,
    )
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
            latest_research=latest_research,
            category=category,
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
        generated_markdown = normalize_final_markdown(generated_markdown, category=category)
        _log_stage("finance 1/4 writer", generated_markdown)
        _dump_stage(output_dir, "01_writer.md", generated_markdown)
        generated_body_chars = _finance_body_chars(generated_markdown)
        if generated_body_chars < required_body_target:
            logger.warning(
                "财经科技整稿低于主稿必达目标，不触发二次整稿生成，将进入 writer 同阶段定向续写: body_chars=%d required=%d target=%d",
                generated_body_chars,
                required_body_target,
                target_words,
            )

    generated_markdown, writer_expanded = await _finance_expand_writer_to_target(
        generated_markdown,
        base_writer_prompt=base_writer_prompt,
        raw_text=raw_text,
        latest_research=latest_research,
        target_words=target_words,
        duration_seconds=duration_seconds,
        category=category,
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
                "required_writer_body_chars": required_body_target,
                "target_mode": (
                    "viewpoint_advice_density_bounded"
                    if is_advice
                    else "expanded_to_target"
                ),
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
        polish_instruction = _finance_stage_instruction(
            prompts["gemini_fast_polish"],
            "Gemini 一润",
            category=category,
        )
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
        polished_markdown = normalize_final_markdown(polished_markdown, category=category)
        if _finance_stage_looks_compressed(generated_markdown, polished_markdown, category=category):
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
        refine_instruction = _finance_stage_instruction(
            prompts["gemini_fast_refine"],
            "Gemini 二润",
            category=category,
        )
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
        refined_markdown = normalize_final_markdown(refined_markdown, category=category)
        if _finance_stage_looks_compressed(polished_markdown, refined_markdown, category=category):
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
    format_template = _finance_format_template(prompts["final_book_format"], category=category)
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

    final_markdown = normalize_final_markdown(final_markdown, category=category)
    if _finance_stage_looks_compressed(refined_markdown, final_markdown, category=category):
        logger.warning(
            "DeepSeek 财经科技整稿格式化疑似压缩或截断，保留 Gemini 二润完整稿: source_body=%d candidate_body=%d",
            _finance_body_chars(refined_markdown),
            _finance_body_chars(final_markdown),
        )
        _dump_stage(output_dir, "04_format_rejected_compressed.md", final_markdown)
        final_markdown = refined_markdown
    final_markdown = normalize_final_markdown(final_markdown, category=category)
    final_markdown = await _repair_final_hard_errors_once(
        final_markdown,
        category=category,
        output_dir=output_dir,
        stage="finance_format",
    )
    _log_stage("finance 4/4 format", final_markdown, prev_chars=refine_chars)
    _dump_stage(output_dir, "04_format.md", final_markdown)
    if on_stage:
        on_stage("format_done", {"chars": len(final_markdown)})
    return final_markdown


def _front_matter_missing(text: str) -> bool:
    required_sections = ("最终书名", "最终简介", "目录", "导言", "正文")
    return not all(marker in text for marker in required_sections)


async def _repair_front_matter(
    raw_text: str,
    generated_markdown: str,
    *,
    category: str | None = None,
) -> str:
    is_finance = _is_finance_longform_category(category)
    source_label = "参考资料全文" if is_finance else "原始视频文稿"
    source_instruction = (
        "- 如果前置块缺失，请根据下方参考资料全文和当前书稿内容补齐\n"
        if is_finance
        else "- 如果前置块缺失，请根据下方原始视频文稿和当前书稿内容补齐\n"
    )
    finance_guard = (
        f"{FINANCE_SOURCE_ATTRIBUTION_GUARD_PROMPT}\n{FINANCE_CURRENT_WEB_EVIDENCE_GUARD_PROMPT}\n"
        if is_finance
        else ""
    )
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
        f"{finance_guard}"
        "- 正文尽量沿用原稿，不要大幅改写\n"
        f"{source_instruction}"
        "- 所有书名、目录标题和正文章节标题都不要出现冒号；最后一章不要命名为“结语”\n"
        f"{SECTION_TITLE_REQUIREMENT_PROMPT}\n"
        "- 不要解释，不要输出分析\n\n"
        f"[{source_label}]\n{raw_text.strip()}\n\n"
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
    character_prompt: str | None = None,
    source_content: str | None = None,
    should_pause: Callable[[], bool] | None = None,
) -> str:
    from app.services.comic_series import (
        assemble_healing_comic_markdown,
        build_healing_comic_writer_prompt,
        build_healing_comic_character_bible,
        generate_healing_comic_images,
        inject_comic_images,
        parse_healing_comic_character_prompt,
        parse_healing_comic_panel_count,
        parse_healing_comic_source_content,
        parse_healing_comic_title,
        parse_healing_comic_writer_output,
    )

    comic_title = parse_healing_comic_title(raw_text) or "今天也不用很厉害"
    panel_count = parse_healing_comic_panel_count(panel_count)
    character_prompt = parse_healing_comic_character_prompt(character_prompt)
    source_content = parse_healing_comic_source_content(source_content)
    if output_dir is None:
        raise ValueError("漫画需要 output_dir 以保存每张配图")
    output_dir.mkdir(parents=True, exist_ok=True)

    def _raise_if_paused() -> None:
        if should_pause is not None and should_pause():
            from app.services.comic_series import ComicGenerationPaused

            raise ComicGenerationPaused("漫画生成已暂停")

    cached_final = _read_stage_file(output_dir, "04_format.md")
    if cached_final:
        if on_stage:
            on_stage("format_done", {"chars": len(cached_final), "reused_stage": True})
        return cached_final

    _raise_if_paused()
    research_context = _read_stage_file(output_dir, "00_healing_comic_research.md")
    if not research_context:
        research_context = await asyncio.to_thread(_healing_comic_research_context, comic_title)
        _dump_stage(output_dir, "00_healing_comic_research.md", research_context)
    if source_content:
        _dump_stage(output_dir, "00_healing_comic_source_content.md", source_content)

    _raise_if_paused()
    cached_script = _read_healing_comic_script_stage(output_dir)
    if cached_script is not None:
        title, intro, panels, series_meta = cached_script
        if on_stage:
            on_stage("comic_structure_done", {"chars": len(research_context), "reused_stage": True})
    else:
        if on_stage:
            on_stage("comic_structure_done", {"chars": len(research_context), "research_done": True})
        writer_prompt = build_healing_comic_writer_prompt(
            comic_title,
            research_context,
            panel_count=panel_count,
            character_prompt=character_prompt,
            source_content=source_content,
        )
        _dump_stage(output_dir, "01_writer_prompt.md", writer_prompt)
        try:
            writer_output = await _route_writer_call(
                writer_prompt,
                reasoning_effort="medium",
                verbosity="low",
                max_output_tokens=0,
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

    _raise_if_paused()
    character_bible = _read_stage_file(output_dir, "02_healing_comic_character_bible.md")
    if not character_bible:
        character_bible = build_healing_comic_character_bible(character_prompt, series_meta)
        _dump_stage(output_dir, "02_healing_comic_character_bible.md", character_bible)

    _raise_if_paused()
    refined_markdown = _read_stage_file(output_dir, "03_refine.md")
    if not refined_markdown:
        refined_markdown = assemble_healing_comic_markdown(title, intro, panels)
        _dump_stage(output_dir, "03_refine.md", refined_markdown)
    if on_stage:
        total_images = 1 + sum(len(panel.get("images") or []) for panel in panels)
        on_stage("comic_images_start", {"total": total_images})

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
        character_bible=character_bible,
        allow_uncertain=True,
        retry_uncertain=True,
        on_progress=_image_progress,
        should_pause=should_pause,
    )
    final_markdown = inject_comic_images(refined_markdown, images)
    final_markdown = normalize_final_markdown(final_markdown, category="healing_comic")
    _dump_stage(output_dir, "04_format.md", final_markdown)
    if on_stage:
        on_stage("format_done", {"chars": len(final_markdown), "skipped_deepseek": True})
    return final_markdown


def _read_healing_comic_script_stage(output_dir: Path | None) -> tuple[
    str,
    str,
    list[dict[str, object]],
    dict[str, str],
] | None:
    raw = _read_stage_file(output_dir, "02_healing_comic_script.json")
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("读取漫画脚本缓存失败: %s", exc)
        return None
    if not isinstance(payload, dict):
        return None
    panels = payload.get("panels")
    if not isinstance(panels, list) or not panels:
        return None
    normalized_panels = [panel for panel in panels if isinstance(panel, dict)]
    if not normalized_panels:
        return None
    series_meta = payload.get("series_meta")
    if not isinstance(series_meta, dict):
        series_meta = {}
    return (
        str(payload.get("title") or "治愈漫画").strip() or "治愈漫画",
        str(payload.get("intro") or "一本原创日常情绪短漫画。").strip(),
        normalized_panels,
        {str(key): str(value) for key, value in series_meta.items()},
    )


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
    healing_comic_character_prompt: str | None = None,
    healing_comic_source_content: str | None = None,
    healing_comic_should_pause: Callable[[], bool] | None = None,
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
    classical_source_text = writer_input_text
    classical_marked_source_text = ""
    classical_expected_count = 0
    is_buddhist_scripture_story = normalized_category == BUDDHIST_SCRIPTURE_STORY_CATEGORY
    buddhist_story_units: list[BuddhistStoryUnit] = []
    use_buddhist_story_unit_writer = False
    is_classical_category = (
        normalized_category in CLASSICAL_CHINESE_CATEGORY_IDS
        and not is_buddhist_scripture_story
    )
    is_classical_interleaved = normalized_category == CLASSICAL_INTERLEAVED_CATEGORY
    if is_buddhist_scripture_story:
        classical_variant = classical_variant_for_category(normalized_category, classical_variant)
        buddhist_story_units = split_buddhist_story_units(
            classical_source_text,
            original_title=classical_original_title,
        )
        use_buddhist_story_unit_writer = _should_use_buddhist_story_unit_writer(
            buddhist_story_units,
            original_title=classical_original_title,
            source_text=classical_source_text,
        )
        writer_input_text = build_buddhist_scripture_story_source(
            writer_input_text,
            original_title=classical_original_title,
        )
    if is_classical_category:
        classical_expected_count = classical_expected_paragraph_count(writer_input_text)
        writer_input_text = build_classical_prompt_source(
            writer_input_text,
            variant=classical_variant,
            original_title=classical_original_title,
            interleaved=is_classical_interleaved,
        )
        classical_marked_source_text = writer_input_text

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)

    if normalized_category == "healing_comic":
        return await _generate_healing_comic_book(
            raw_text,
            output_dir=output_dir,
            on_stage=on_stage,
            panel_count=healing_comic_panel_count or 32,
            character_prompt=healing_comic_character_prompt,
            source_content=healing_comic_source_content,
            should_pause=healing_comic_should_pause,
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
            category=normalized_category,
            prompts=prompts,
            output_dir=output_dir,
            on_stage=on_stage,
            source_duration_seconds=source_duration_seconds,
        )

    ignore_existing_stages = False

    def _read_stage(filename: str) -> str | None:
        if ignore_existing_stages:
            return None
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
        final_resume_markdown = normalize_final_markdown(final_resume_markdown, category=normalized_category)
        if is_buddhist_scripture_story and use_buddhist_story_unit_writer:
            final_resume_markdown = await _repair_buddhist_story_manuscript_against_units(
                final_resume_markdown,
                units=buddhist_story_units,
                output_dir=output_dir,
                stage="resume",
                original_title=classical_original_title,
            )
            _dump_stage(output_dir, "04_format.md", final_resume_markdown)
        from app.core.quality_metrics import detect_final_hard_errors

        resume_hard_errors = detect_final_hard_errors(
            final_resume_markdown,
            category=normalized_category,
        )
        if is_buddhist_scripture_story and _has_non_repairable_final_hard_errors(resume_hard_errors):
            logger.warning(
                "佛经白话既有 04_format.md 命中不可局部修复硬伤，将忽略旧阶段并重新生成: %s",
                "; ".join(resume_hard_errors),
            )
            ignore_existing_stages = True
            final_resume_markdown = None
        else:
            final_resume_markdown = await _repair_final_hard_errors_once(
                final_resume_markdown,
                category=normalized_category,
                output_dir=output_dir,
                stage="resume",
            )
            _dump_stage(output_dir, "04_format.md", final_resume_markdown)
            _log_stage("4/4 format (resume fast)", final_resume_markdown)
            if on_stage:
                on_stage("format_done", {"chars": len(final_resume_markdown)})
            return final_resume_markdown

    # ---- 第 1 层: OpenAI Writer ----
    generated_markdown = _read_stage("01_writer.md")
    if generated_markdown and use_buddhist_story_unit_writer:
        cached_writer_issues = _buddhist_story_manuscript_issues(
            generated_markdown,
            buddhist_story_units,
        )
        if cached_writer_issues:
            logger.warning(
                "佛经白话既有 01_writer.md 未通过章节/篇幅保护，将重新生成: %s",
                "; ".join(cached_writer_issues),
            )
            generated_markdown = None
    if generated_markdown:
        logger.info("复用已存在的 01_writer.md")
        _log_stage("1/4 writer (resume)", generated_markdown)
        if on_stage:
            on_stage("writer_done", {"chars": len(generated_markdown)})
    else:
        if on_stage:
            on_stage("writer_start", {})
        if (
            is_classical_category
            and _should_use_classical_segmented_writer(
                classical_source_text,
                classical_expected_count,
            )
        ):
            generated_markdown = await _generate_classical_segmented_writer(
                classical_source_text,
                variant=classical_variant,
                original_title=classical_original_title,
                output_dir=output_dir,
                on_stage=on_stage,
                interleaved=is_classical_interleaved,
            )
        elif use_buddhist_story_unit_writer:
            generated_markdown = await _generate_buddhist_story_unit_writer(
                classical_source_text,
                units=buddhist_story_units,
                original_title=classical_original_title,
                output_dir=output_dir,
                on_stage=on_stage,
            )
        else:
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
            writer_max_output_tokens = 0 if is_classical_category else None
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
        repaired_markdown = await _repair_front_matter(
            raw_text,
            generated_markdown,
            category=normalized_category,
        )
        if repaired_markdown.strip():
            generated_markdown = repaired_markdown
            _log_stage("1b/4 writer-repaired", generated_markdown)
            _dump_stage(output_dir, "01b_writer_repaired.md", generated_markdown)

    if is_classical_category:
        generated_markdown = await _repair_classical_quality_once(
            generated_markdown,
            marked_source_text=classical_marked_source_text,
            source_text=classical_source_text,
            expected_count=classical_expected_count,
            stage="writer",
            output_dir=output_dir,
            interleaved=is_classical_interleaved,
        )
        _dump_stage(output_dir, "01_writer.md", generated_markdown)

    writer_chars = len(generated_markdown)

    # ---- 第 2 层: Gemini 一润 (按完整段落 ~1000 字切块) ----
    polished_markdown = _read_stage("02_polish.md")
    if polished_markdown and use_buddhist_story_unit_writer:
        cached_polish_issues = _buddhist_story_manuscript_issues(
            polished_markdown,
            buddhist_story_units,
        )
        if cached_polish_issues:
            logger.warning(
                "佛经白话既有 02_polish.md 未通过章节/篇幅保护，将重新一润: %s",
                "; ".join(cached_polish_issues),
            )
            polished_markdown = None
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
            if is_classical_category
            else polish_book_text
        )
        polished_markdown = await _polish_by_paragraph_chunks_strict(
            generated_markdown,
            target_chars=(
                BUDDHIST_STORY_LONG_CHUNK_TARGET_CHARS
                if use_buddhist_story_unit_writer
                else 1000
            ),
            system_instruction=gemini_fast_polish_prompt,
            on_stage=on_stage,
            output_dir=output_dir,
            chunk_func=polish_chunk_func,
            allow_chunk_fallback=is_classical_category,
            splitter=(
                _split_text_by_classical_marker_lines
                if is_classical_category
                else _split_buddhist_text_by_chapters
                if use_buddhist_story_unit_writer
                else None
            ),
            chunk_validator=(
                _classical_chunk_marker_issues
                if is_classical_category
                else _buddhist_chapter_marker_issues
                if use_buddhist_story_unit_writer
                else None
            ),
        )
        _log_stage("2/4 polish", polished_markdown, prev_chars=writer_chars)
        _dump_stage(output_dir, "02_polish.md", polished_markdown)
        polish_chars = len(polished_markdown)
        if on_stage:
            on_stage("polish_done", {"chars": polish_chars})
    if use_buddhist_story_unit_writer:
        buddhist_polish_issues = _buddhist_story_manuscript_issues(
            polished_markdown,
            buddhist_story_units,
        )
        if buddhist_polish_issues:
            polished_markdown = await _repair_buddhist_story_manuscript_against_units(
                polished_markdown,
                units=buddhist_story_units,
                output_dir=output_dir,
                stage="polish",
                original_title=classical_original_title,
            )
        _dump_stage(output_dir, "02_polish.md", polished_markdown)
        polish_chars = len(polished_markdown)
    if is_classical_category:
        polished_markdown = await _repair_classical_quality_once(
            polished_markdown,
            marked_source_text=classical_marked_source_text,
            source_text=classical_source_text,
            expected_count=classical_expected_count,
            stage="polish",
            output_dir=output_dir,
            interleaved=is_classical_interleaved,
        )
        _dump_stage(output_dir, "02_polish.md", polished_markdown)
        polish_chars = len(polished_markdown)

    # ---- 第 3 层: Gemini 二润 (整篇) ----
    refined_markdown = _read_stage("03_refine.md")
    if refined_markdown and use_buddhist_story_unit_writer:
        cached_refine_issues = _buddhist_story_manuscript_issues(
            refined_markdown,
            buddhist_story_units,
        )
        if cached_refine_issues:
            logger.warning(
                "佛经白话既有 03_refine.md 未通过章节/篇幅保护，将重新二润: %s",
                "; ".join(cached_refine_issues),
            )
            refined_markdown = None
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
            if is_classical_category
            else refine_book_text
        )
        if is_classical_category:
            logger.info("Classical Chinese refine uses chunked path to avoid whole-book Gemini stalls")
            refined_markdown = await _refine_by_paragraph_chunks_strict(
                polished_markdown,
                target_chars=1000,
                system_instruction=gemini_fast_refine_prompt,
                on_stage=on_stage,
                output_dir=output_dir,
                chunk_func=refine_func,
                allow_chunk_fallback=True,
                splitter=_split_text_by_classical_marker_lines,
                chunk_validator=_classical_chunk_marker_issues,
            )
        elif use_buddhist_story_unit_writer:
            logger.info("Buddhist story refine uses chapter chunks to avoid whole-book compression")
            refined_markdown = await _refine_by_paragraph_chunks_strict(
                polished_markdown,
                target_chars=BUDDHIST_STORY_LONG_CHUNK_TARGET_CHARS,
                system_instruction=gemini_fast_refine_prompt,
                on_stage=on_stage,
                output_dir=output_dir,
                splitter=_split_buddhist_text_by_chapters,
                chunk_validator=_buddhist_chapter_marker_issues,
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
    if use_buddhist_story_unit_writer:
        buddhist_refine_issues = _buddhist_story_manuscript_issues(
            refined_markdown,
            buddhist_story_units,
        )
        if buddhist_refine_issues:
            refined_markdown = await _repair_buddhist_story_manuscript_against_units(
                refined_markdown,
                units=buddhist_story_units,
                output_dir=output_dir,
                stage="refine",
                original_title=classical_original_title,
            )
        _dump_stage(output_dir, "03_refine.md", refined_markdown)
        refine_chars = len(refined_markdown)
    if is_classical_category:
        refined_markdown = await _repair_classical_quality_once(
            refined_markdown,
            marked_source_text=classical_marked_source_text,
            source_text=classical_source_text,
            expected_count=classical_expected_count,
            stage="refine",
            output_dir=output_dir,
            interleaved=is_classical_interleaved,
        )
        _dump_stage(output_dir, "03_refine.md", refined_markdown)
        refine_chars = len(refined_markdown)

    # ---- 第 4 层: DeepSeek 格式 (整篇) ----
    final_markdown = _read_stage("04_format.md")
    if final_markdown and use_buddhist_story_unit_writer:
        cached_format_issues = _buddhist_story_manuscript_issues(
            final_markdown,
            buddhist_story_units,
        )
        if cached_format_issues:
            logger.warning(
                "佛经白话既有 04_format.md 未通过章节/篇幅保护，将重新格式化: %s",
                "; ".join(cached_format_issues),
            )
            final_markdown = None
    if final_markdown:
        logger.info("复用已存在的 04_format.md")
        _log_stage("4/4 format (resume)", final_markdown, prev_chars=refine_chars)
        if on_stage:
            on_stage("format_done", {"chars": len(final_markdown)})
    else:
        if on_stage:
            on_stage("format_start", {})
        try:
            format_max_output_tokens = 0 if (is_classical_category or use_buddhist_story_unit_writer) else None
            final_markdown = await asyncio.wait_for(
                asyncio.to_thread(
                    format_book_text,
                    refined_markdown,
                    prompt_template=final_book_format_prompt,
                    max_output_tokens=format_max_output_tokens,
                    fail_on_max_tokens=(is_classical_category or use_buddhist_story_unit_writer),
                    on_progress=(
                        (lambda chars: on_stage("format_progress", {"chars": chars}))
                        if on_stage
                        else None
                    ),
                ),
                timeout=settings.DEEPSEEK_FORMAT_CALL_TIMEOUT,
            )
            if (
                is_classical_category
                and _classical_format_looks_truncated(refined_markdown, final_markdown)
            ):
                final_markdown = _classical_format_fallback_text(refined_markdown, final_markdown)
            if use_buddhist_story_unit_writer:
                buddhist_format_issues = _buddhist_story_manuscript_issues(
                    final_markdown,
                    buddhist_story_units,
                )
                if buddhist_format_issues:
                    logger.warning(
                        "佛经白话 DeepSeek 格式层破坏章节或篇幅，回退到 Gemini 二润稿: %s",
                        "; ".join(buddhist_format_issues),
                    )
                    final_markdown = refined_markdown
        except DeepSeekMaxTokensError as exc:
            if not (is_classical_category or use_buddhist_story_unit_writer):
                raise
            final_markdown = (
                refined_markdown
                if use_buddhist_story_unit_writer
                else _classical_format_fallback_text(refined_markdown, exc.partial_text)
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"DeepSeek 终稿格式化超过 {settings.DEEPSEEK_FORMAT_CALL_TIMEOUT} 秒未返回"
            ) from exc
        _log_stage("4/4 format", final_markdown, prev_chars=refine_chars)
        _dump_stage(output_dir, "04_format.md", final_markdown)
        if on_stage:
            on_stage("format_done", {"chars": len(final_markdown)})

    if is_classical_category:
        final_issues = _classical_quality_issues(
            final_markdown,
            expected_count=classical_expected_count,
            source_text=classical_source_text,
            interleaved=is_classical_interleaved,
        )
        if final_issues:
            refined_issues = _classical_quality_issues(
                refined_markdown,
                expected_count=classical_expected_count,
                source_text=classical_source_text,
                interleaved=is_classical_interleaved,
            )
            if not refined_issues:
                logger.warning(
                    "古文白话 DeepSeek 格式层丢失结构保护，回退到 Gemini 二润完整稿: %s",
                    "; ".join(final_issues),
                )
                final_markdown = refined_markdown
            else:
                final_markdown = await _repair_classical_quality_once(
                    final_markdown,
                    marked_source_text=classical_marked_source_text,
                    source_text=classical_source_text,
                    expected_count=classical_expected_count,
                    stage="format",
                    output_dir=output_dir,
                    interleaved=is_classical_interleaved,
                )
    if use_buddhist_story_unit_writer:
        buddhist_final_issues = _buddhist_story_manuscript_issues(
            final_markdown,
            buddhist_story_units,
        )
        if buddhist_final_issues:
            final_markdown = await _repair_buddhist_story_manuscript_against_units(
                final_markdown,
                units=buddhist_story_units,
                output_dir=output_dir,
                stage="final",
                original_title=classical_original_title,
            )

    if _front_matter_missing(final_markdown):
        logger.warning("后处理丢失前置块，回退到修复后的主模型输出。")
        final_markdown = generated_markdown

    if _front_matter_missing(final_markdown):
        logger.warning("前置块仍缺失，保留当前输出以避免整篇回退。")

    if is_classical_category:
        final_markdown = enforce_classical_title(final_markdown, classical_original_title)
    elif is_buddhist_scripture_story:
        final_markdown = enforce_classical_title(
            final_markdown,
            buddhist_scripture_final_title(classical_original_title),
        )
    final_markdown = normalize_final_markdown(final_markdown, category=normalized_category)
    final_markdown = await _repair_final_hard_errors_once(
        final_markdown,
        category=normalized_category,
        output_dir=output_dir,
        stage="format",
    )
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
