"""Book cover generation."""
from __future__ import annotations

import base64
import logging
import re
from pathlib import Path
from typing import Any

import httpx
from openai import OpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

_PLACEHOLDER_KEYS = {"", "sk-xxx", "sk-xxxx", "sk-xxxx..."}
_INTRO_SECTION_RE = re.compile(
    r"^@?\s*三个备选简介\s*$([\s\S]*?)(?=^@?\s*目录\s*$|^@?\s*正文\s*$|^@|\Z)",
    re.MULTILINE,
)
_INTRO_LINE_RE = re.compile(r"^\s*(?:\d+[\.、]\s*|[-*]\s*)?(.+?)\s*$")


def _resolve_image_api_key() -> str:
    image_key = (settings.IMAGE_API_KEY or "").strip()
    if image_key.lower() not in _PLACEHOLDER_KEYS and "xxx" not in image_key.lower():
        return image_key
    openai_key = (settings.OPENAI_API_KEY or "").strip()
    if not openai_key:
        raise ValueError("IMAGE_API_KEY 未配置，且 OPENAI_API_KEY 也为空，无法生成封面")
    return openai_key


def _get_image_client() -> OpenAI:
    return OpenAI(
        api_key=_resolve_image_api_key(),
        base_url=settings.IMAGE_BASE_URL or settings.OPENAI_BASE_URL,
        timeout=httpx.Timeout(600.0, connect=60.0),
    )


def _extract_primary_intro(markdown: str) -> str:
    section = _INTRO_SECTION_RE.search(markdown or "")
    if not section:
        return ""
    lines: list[str] = []
    for line in section.group(1).splitlines():
        line = line.strip()
        if not line:
            continue
        match = _INTRO_LINE_RE.match(line)
        if match:
            lines.append(match.group(1).strip())
        if lines:
            break
    return " ".join(lines)[:600]


def build_cover_prompt(title: str, markdown: str) -> str:
    intro = _extract_primary_intro(markdown)
    context = intro or title
    return (
        "Create a premium vertical book cover for a serious Chinese nonfiction book. "
        "No readable text, no typography, no logos, no watermarks. "
        "Use only the book title and short synopsis below as creative context. "
        "Visual direction: refined editorial nonfiction cover, AI-era education, human judgment, learning, tools, future classroom, "
        "warm intellectual atmosphere, clean central composition, premium print design, 3:4 portrait cover. "
        f"Book title: {title}. "
        f"Synopsis: {context}"
    )


def generate_cover_file(title: str, markdown: str, output_dir: Path) -> Path:
    """Generate a cover image and save it under the task output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    cover_path = output_dir / "cover.png"
    prompt = build_cover_prompt(title, markdown)
    client = _get_image_client()

    logger.info("开始生成封面: model=%s size=%s title=%s", settings.IMAGE_MODEL, settings.IMAGE_SIZE, title)
    response = client.images.generate(
        model=settings.IMAGE_MODEL,
        prompt=prompt,
        size=settings.IMAGE_SIZE,
        n=1,
    )
    item: Any = response.data[0]

    b64 = getattr(item, "b64_json", None)
    if b64:
        cover_path.write_bytes(base64.b64decode(b64))
        return cover_path

    url = getattr(item, "url", None)
    if url:
        with httpx.Client(timeout=120.0) as http:
            img = http.get(url)
            img.raise_for_status()
            cover_path.write_bytes(img.content)
        return cover_path

    raise RuntimeError("图像 API 返回值既无 b64_json 也无 url")
