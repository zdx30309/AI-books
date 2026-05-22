"""Helpers that keep provider calls on official API origins."""
from __future__ import annotations

import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

OPENAI_API_BASE_URL = "https://api.openai.com/v1"
DEEPSEEK_API_BASE_URL = "https://api.deepseek.com/v1"


def official_openai_base_url(configured: str | None = None) -> str:
    raw = (configured or OPENAI_API_BASE_URL).strip().rstrip("/") or OPENAI_API_BASE_URL
    if not settings.OFFICIAL_API_DIRECT_ONLY:
        return raw
    if raw != OPENAI_API_BASE_URL:
        logger.warning("Ignoring non-official OpenAI-compatible base URL in direct-only mode: %s", raw)
    return OPENAI_API_BASE_URL


def official_deepseek_base_url(configured: str | None = None) -> str:
    raw = (configured or DEEPSEEK_API_BASE_URL).strip().rstrip("/") or DEEPSEEK_API_BASE_URL
    if not settings.OFFICIAL_API_DIRECT_ONLY:
        return raw
    if raw != DEEPSEEK_API_BASE_URL:
        logger.warning("Ignoring non-official DeepSeek-compatible base URL in direct-only mode: %s", raw)
    return DEEPSEEK_API_BASE_URL


def official_proxy(provider: str, configured: str | None = None) -> str:
    proxy = (configured or "").strip()
    if settings.OFFICIAL_API_DIRECT_ONLY and proxy:
        logger.warning("Ignoring %s proxy in direct-only mode.", provider)
        return ""
    return proxy
