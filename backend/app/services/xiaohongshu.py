from __future__ import annotations

import html
import json
import logging
import re
import time
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.xiaohongshu.com/",
}

_JSON_STRING_FIELD_RE = r'"{key}"\s*:\s*"((?:\\.|[^"\\])*)"'
_META_RE = r'<meta[^>]+(?:name|property)="{name}"[^>]+content="([^"]*)"'
_SUBTITLE_URL_RE = re.compile(
    r"https:\\u002F\\u002F(?:[^\\\"]|\\u002F)+?\.srt\?sign=[^\\\"]+&t=[0-9a-f]+"
)
_CHAPTER_RE = re.compile(
    r'"text"\s*:\s*"((?:\\.|[^"\\])*)"\s*,\s*"time"\s*:\s*(\d+)'
    r'|"time"\s*:\s*(\d+)\s*,\s*"text"\s*:\s*"((?:\\.|[^"\\])*)"'
)


class XiaohongshuPageTextError(RuntimeError):
    pass


@dataclass(frozen=True)
class XiaohongshuPageText:
    text: str
    title: str | None = None
    desc: str | None = None
    author: str | None = None
    duration_seconds: int | None = None
    subtitle_url: str | None = None


def extract_xiaohongshu_page_text(source_url: str, *, timeout: int = 20) -> XiaohongshuPageText:
    page = _fetch_text(source_url, timeout=timeout)
    segment = _note_segment(page)

    title = _field(segment, "title") or _meta(page, "og:title")
    if title and title.endswith(" - 小红书"):
        title = title[: -len(" - 小红书")].strip()
    desc = _field(segment, "desc") or _meta(page, "description")
    author = _field(segment, "nickname")
    duration_seconds = _duration_seconds(page, segment)
    chapters = _chapters(segment)

    subtitle_url = _choose_subtitle_url(page)
    subtitle_text = ""
    subtitle_error: Exception | None = None
    if subtitle_url:
        try:
            subtitle_text = _srt_to_text(_fetch_text(subtitle_url, timeout=timeout))
        except Exception as exc:
            subtitle_error = exc
            logger.warning("Xiaohongshu subtitle fallback failed url=%s", subtitle_url, exc_info=True)
    if subtitle_url and not subtitle_text:
        raise XiaohongshuPageTextError(f"小红书页面字幕正文提取失败：{subtitle_error}")
    if not subtitle_url:
        raise XiaohongshuPageTextError("小红书页面未找到可用字幕正文")

    body_parts: list[str] = []
    if title:
        body_parts.append(f"标题：{title}")
    if author:
        body_parts.append(f"作者：{author}")
    if desc:
        body_parts.append(f"简介：{_clean_desc(desc)}")
    if chapters:
        body_parts.append("章节线索：\n" + "\n".join(chapters))
    if subtitle_text:
        body_parts.append("正文：\n" + subtitle_text)

    text = "\n\n".join(part.strip() for part in body_parts if part.strip()).strip()
    if not subtitle_text or len(subtitle_text) < 20:
        raise XiaohongshuPageTextError("小红书页面字幕正文过短")
    if len(text) < 100:
        raise XiaohongshuPageTextError("小红书页面未提取到足够正文")
    return XiaohongshuPageText(
        text=text,
        title=title,
        desc=desc,
        author=author,
        duration_seconds=duration_seconds,
        subtitle_url=subtitle_url,
    )


def _fetch_text(url: str, *, timeout: int) -> str:
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=_DEFAULT_HEADERS, timeout=timeout)
            resp.raise_for_status()
            return resp.text
        except Exception as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(0.8 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def _note_segment(page: str) -> str:
    idx = page.find("noteDetailMap")
    if idx < 0:
        return page
    return page[idx : idx + 200_000]


def _field(text: str, key: str) -> str | None:
    match = re.search(_JSON_STRING_FIELD_RE.format(key=re.escape(key)), text)
    if not match:
        return None
    return _decode_json_string(match.group(1))


def _decode_json_string(raw: str) -> str:
    try:
        value = json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        value = raw.replace(r"\u002F", "/")
    return html.unescape(str(value)).strip()


def _meta(page: str, name: str) -> str | None:
    match = re.search(_META_RE.format(name=re.escape(name)), page, flags=re.I)
    if not match:
        return None
    return html.unescape(match.group(1)).strip()


def _duration_seconds(page: str, segment: str) -> int | None:
    meta_time = _meta(page, "og:videotime")
    if meta_time:
        parts = [int(p) for p in re.findall(r"\d+", meta_time)]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
    match = re.search(r'"duration"\s*:\s*(\d+)', segment)
    if match:
        value = int(match.group(1))
        if value > 10_000:
            return round(value / 1000)
        return value
    return None


def _chapters(segment: str) -> list[str]:
    rows: list[tuple[int, str]] = []
    for match in _CHAPTER_RE.finditer(segment):
        raw = match.group(1) or match.group(4) or ""
        raw_time = match.group(2) or match.group(3) or "0"
        text = _decode_json_string(raw)
        if text:
            rows.append((int(raw_time), text))
    rows.sort(key=lambda item: item[0])
    result: list[str] = []
    seen: set[str] = set()
    for ms, text in rows:
        marker = f"{_format_ms(ms)} {text}"
        if marker not in seen:
            seen.add(marker)
            result.append(marker)
    return result


def _format_ms(ms: int) -> str:
    total = max(0, round(ms / 1000))
    minute, second = divmod(total, 60)
    hour, minute = divmod(minute, 60)
    if hour:
        return f"{hour:02d}:{minute:02d}:{second:02d}"
    return f"{minute:02d}:{second:02d}"


def _choose_subtitle_url(page: str) -> str | None:
    matches = list(_SUBTITLE_URL_RE.finditer(page))
    candidates: list[tuple[int, str]] = []
    for match in matches:
        raw = match.group(0)
        context = page[max(0, match.start() - 180) : match.end() + 260]
        score = 0
        if "zh-CN" in context:
            score += 20
        if "source" in context:
            score += 5
        if "en-US" in context:
            score -= 10
        url = raw.replace(r"\u002F", "/")
        candidates.append((score, url))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _srt_to_text(srt: str) -> str:
    lines: list[str] = []
    previous = ""
    for raw_line in srt.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line or line.isdigit() or "-->" in line:
            continue
        line = re.sub(r"<[^>]+>", "", line).strip()
        if not line or line == previous:
            continue
        previous = line
        lines.append(line)
    return "\n".join(lines).strip()


def _clean_desc(desc: str) -> str:
    text = re.sub(r"\[话题\]#", "#", desc)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


__all__ = ["XiaohongshuPageText", "XiaohongshuPageTextError", "extract_xiaohongshu_page_text"]
