"""Backend-only provider selection for the two manuscript polish stages."""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.services import claude, gemini


def _provider(setting_name: str) -> str:
    provider = str(getattr(settings, setting_name, "gemini") or "gemini").strip().lower()
    if provider not in {"gemini", "claude"}:
        raise ValueError(f"{setting_name} 只支持 gemini 或 claude，当前值: {provider!r}")
    return provider


def polish_provider_name() -> str:
    return _provider("BACKEND_POLISH_PROVIDER")


def refine_provider_name() -> str:
    return _provider("BACKEND_REFINE_PROVIDER")


def polish_book_text(text: str, **kwargs: Any) -> str:
    if polish_provider_name() == "claude":
        return claude.polish_book_text(text, **kwargs)
    return gemini.polish_book_text(text, **kwargs)


def refine_book_text(text: str, **kwargs: Any) -> str:
    if refine_provider_name() == "claude":
        return claude.refine_book_text(text, **kwargs)
    return gemini.refine_book_text(text, **kwargs)


__all__ = [
    "polish_book_text",
    "polish_provider_name",
    "refine_book_text",
    "refine_provider_name",
]
