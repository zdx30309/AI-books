"""Image OCR helpers for public-domain source ingestion."""
from __future__ import annotations

import base64
import io
import logging
from typing import Any

import httpx
from openai import OpenAI
from PIL import Image, ImageOps

from app.core.config import settings
from app.utils.official_api import official_openai_base_url, official_proxy

logger = logging.getLogger(__name__)

_SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
_MAX_IMAGE_EDGE = 2200


class ImageOcrError(RuntimeError):
    """Raised when OCR cannot produce usable text."""


def is_supported_ocr_image_suffix(suffix: str) -> bool:
    return suffix.lower() in _SUPPORTED_IMAGE_SUFFIXES


def ocr_image_bytes(content: bytes, *, filename: str = "") -> str:
    if not content:
        raise ImageOcrError("图片文件为空。")
    image_bytes, mime_type = _normalize_image(content)
    encoded = base64.b64encode(image_bytes).decode("ascii")
    model = (settings.OPENAI_OCR_MODEL or settings.OPENAI_WRITER_MODEL or "gpt-5.6-sol").strip()
    client = _openai_ocr_client()
    prompt = (
        "请对这张图片做高精度中文 OCR，只提取图片里的正文文字。\n"
        "要求：\n"
        "1. 保持原有段落顺序，尽量保留章节标题和注释编号。\n"
        "2. 不要解释，不要总结，不要补写，不要翻译。\n"
        "3. 如果图片里有页眉、页码或网页按钮，除非它属于正文，否则不要输出。\n"
        "4. 输出纯文本。"
    )
    try:
        response = client.responses.create(
            model=model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": f"data:{mime_type};base64,{encoded}"},
                    ],
                }
            ],
            max_output_tokens=max(800, int(settings.OPENAI_OCR_MAX_OUTPUT_TOKENS or 5000)),
        )
    except Exception as exc:  # pragma: no cover - exercised through API error path in production
        logger.exception("OpenAI image OCR failed for %s", filename or "<uploaded image>")
        raise ImageOcrError(f"图片 OCR 失败: {exc}") from exc
    text = _extract_response_text(response).strip()
    if not text:
        raise ImageOcrError("图片 OCR 未识别到文字。")
    return text


def _openai_ocr_client() -> OpenAI:
    request_timeout = httpx.Timeout(300.0, connect=30.0)
    kwargs: dict[str, Any] = {
        "base_url": official_openai_base_url(settings.OPENAI_BASE_URL),
        "timeout": request_timeout,
    }
    proxy = official_proxy("openai-ocr", settings.OPENAI_HTTPS_PROXY)
    if proxy:
        kwargs["http_client"] = httpx.Client(proxy=proxy, timeout=request_timeout)
    api_key = (settings.OPENAI_API_KEY or "").strip()
    if api_key:
        kwargs["api_key"] = api_key
    return OpenAI(**kwargs)


def _normalize_image(content: bytes) -> tuple[bytes, str]:
    try:
        with Image.open(io.BytesIO(content)) as image:
            image = ImageOps.exif_transpose(image)
            image.load()
            if max(image.size) > _MAX_IMAGE_EDGE:
                ratio = _MAX_IMAGE_EDGE / max(image.size)
                size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
                image = image.resize(size, Image.Resampling.LANCZOS)
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            output = io.BytesIO()
            image.save(output, format="PNG", optimize=True)
            return output.getvalue(), "image/png"
    except Exception as exc:
        raise ImageOcrError(f"图片无法读取: {exc}") from exc


def _extract_response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    chunks: list[str] = []
    for item in getattr(response, "output", None) or []:
        for content in getattr(item, "content", None) or []:
            text = getattr(content, "text", None)
            if isinstance(text, str):
                chunks.append(text)
            elif isinstance(content, dict) and isinstance(content.get("text"), str):
                chunks.append(str(content["text"]))
    if chunks:
        return "\n".join(chunks)
    if isinstance(response, dict):
        if isinstance(response.get("output_text"), str):
            return str(response["output_text"])
    return ""
