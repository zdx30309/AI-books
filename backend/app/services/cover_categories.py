"""Cover-only category registry.

Standalone cover generation uses Excel metadata rather than manuscript body
generation, so it can expose cover-specific visual categories without adding
them to the book-writing category list.
"""
from __future__ import annotations

from app.prompts.goal_driven_templates import get_category_meta

BASE_COVER_CATEGORY_IDS: tuple[str, ...] = (
    "social_science",
    "psychology_self_help",
)

COVER_ONLY_CATEGORY_OPTIONS: tuple[dict[str, str], ...] = (
    {
        "id": "illustration",
        "label": "治愈插画",
        "core_feature": "治愈感插画封面入口",
        "directions": "情绪疗愈、插画风封面、温柔生活感",
        "prompt_status": "ready",
    },
    {
        "id": "illustration_crayon",
        "label": "治愈插画（蜡笔）",
        "core_feature": "温柔情绪插画封面，强调蜡笔质感与治愈氛围",
        "directions": "情绪日记、心理自助、孤独感、成年人治愈读物",
        "prompt_status": "ready",
    },
    {
        "id": "literature",
        "label": "文学类",
        "core_feature": "文艺治愈系插画书封，强调诗意与生活气息",
        "directions": "文学、文艺治愈、城市、自然、房间、旅行、生活切片",
        "prompt_status": "ready",
    },
)

COVER_CATEGORY_OPTIONS: tuple[dict[str, str], ...] = (
    *(get_category_meta(category_id) for category_id in BASE_COVER_CATEGORY_IDS),
    *COVER_ONLY_CATEGORY_OPTIONS,
)

_COVER_CATEGORY_ALIASES: dict[str, str] = {}


def _alias_key(value: str) -> str:
    return "".join(value.strip().split()).replace("(", "（").replace(")", "）")


def _register_alias(alias: str, category_id: str) -> None:
    _COVER_CATEGORY_ALIASES[alias] = category_id
    _COVER_CATEGORY_ALIASES[_alias_key(alias)] = category_id


_register_alias("分类插画", "illustration")
_register_alias("分类插画（蜡笔）", "illustration_crayon")
_register_alias("治愈插画", "illustration")
_register_alias("治愈插画（蜡笔）", "illustration_crayon")
_register_alias("文学", "literature")
_register_alias("文学类", "literature")
_register_alias("文学题材", "literature")
_register_alias("文艺治愈", "literature")
for _category in COVER_CATEGORY_OPTIONS:
    _register_alias(_category["id"], _category["id"])
    _register_alias(_category["label"], _category["id"])


def list_cover_categories() -> list[dict[str, str]]:
    return [dict(category) for category in COVER_CATEGORY_OPTIONS]


def normalize_cover_category_id(category: str | None, fallback: str | None = None) -> str:
    raw = (category or fallback or "social_science").strip() or "social_science"
    normalized = _COVER_CATEGORY_ALIASES.get(raw) or _COVER_CATEGORY_ALIASES.get(_alias_key(raw))
    if normalized:
        return normalized
    raise ValueError(f"未支持的封面分类: {category!r}")


def get_cover_category_meta(category: str | None) -> dict[str, str]:
    normalized = normalize_cover_category_id(category)
    for item in COVER_CATEGORY_OPTIONS:
        if item["id"] == normalized:
            return dict(item)
    return get_category_meta(normalized)


__all__ = [
    "COVER_CATEGORY_OPTIONS",
    "COVER_ONLY_CATEGORY_OPTIONS",
    "BASE_COVER_CATEGORY_IDS",
    "get_cover_category_meta",
    "list_cover_categories",
    "normalize_cover_category_id",
]
