"""Book cover generation."""
from __future__ import annotations

import base64
import logging
import re
import time
from pathlib import Path
from typing import Any

import httpx

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
    base_url = (settings.IMAGE_BASE_URL or settings.OPENAI_BASE_URL).rstrip("/")
    endpoint = f"{base_url}/images/generations"
    api_key = _resolve_image_api_key()

    logger.info("开始生成封面: model=%s size=%s title=%s", settings.IMAGE_MODEL, settings.IMAGE_SIZE, title)
    payload: dict[str, Any] = {
        "model": settings.IMAGE_MODEL,
        "prompt": prompt[:5000],
        "size": settings.IMAGE_SIZE,
        "n": 1,
    }
    if settings.IMAGE_RESPONSE_FORMAT:
        payload["response_format"] = settings.IMAGE_RESPONSE_FORMAT
    if settings.IMAGE_OUTPUT_FORMAT:
        payload["output_format"] = settings.IMAGE_OUTPUT_FORMAT
    if settings.IMAGE_QUALITY:
        payload["quality"] = settings.IMAGE_QUALITY

    last_error: Exception | None = None
    with httpx.Client(timeout=httpx.Timeout(600.0, connect=60.0)) as http:
        for attempt in range(1, 4):
            try:
                response = http.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                break
            except httpx.HTTPStatusError as exc:
                body = exc.response.text[:1000]
                last_error = RuntimeError(f"图像 API 请求失败 {exc.response.status_code}: {body}")
                if exc.response.status_code < 500 or attempt >= 3:
                    raise last_error from exc
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt >= 3:
                    raise RuntimeError(f"图像 API 连接失败: {exc}") from exc
            time.sleep(2 * attempt)
        else:
            raise RuntimeError(f"图像 API 请求失败: {last_error}")

    items = data.get("data")
    if not isinstance(items, list) or not items:
        raise RuntimeError(f"图像 API 返回缺少 data: {str(data)[:1000]}")
    item = items[0]

    b64 = item.get("b64_json") if isinstance(item, dict) else getattr(item, "b64_json", None)
    if b64:
        cover_path.write_bytes(base64.b64decode(b64))
        return cover_path

    url = item.get("url") if isinstance(item, dict) else getattr(item, "url", None)
    if url:
        with httpx.Client(timeout=120.0) as http:
            img = http.get(url)
            img.raise_for_status()
            cover_path.write_bytes(img.content)
        return cover_path

    raise RuntimeError("图像 API 返回值既无 b64_json 也无 url")
