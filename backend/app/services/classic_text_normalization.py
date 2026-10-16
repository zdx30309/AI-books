"""Delivery normalization preserved from 70c7ad8."""
from __future__ import annotations
import re
from app.prompts.classic_text import base_category

def normalize_category(category: str | None) -> str:
    return base_category(category or "social_science")

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
