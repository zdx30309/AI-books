"""Cover-only category registry.

Standalone cover generation uses Excel metadata rather than manuscript body
generation, so it can expose cover-specific visual categories without adding
them to the book-writing category list.
"""
from __future__ import annotations

from app.prompts.goal_driven_templates import get_category_meta, list_categories, normalize_category

COVER_ONLY_CATEGORY_OPTIONS: tuple[dict[str, str], ...] = (
    {
        "id": "illustration",
        "label": "治愈插画",
        "core_feature": "插画封面入口，默认复用心理自助封面 prompt",
        "directions": "心理自助、情绪疗愈、插画风封面",
        "prompt_status": "ready",
    },
    {
        "id": "illustration_crayon",
        "label": "治愈插画（蜡笔）",
        "core_feature": "温柔情绪插画封面，强调蜡笔质感与治愈氛围",
        "directions": "情绪日记、心理自助、孤独感、成年人治愈读物",
        "prompt_status": "ready",
    },
)

COVER_CATEGORY_OPTIONS: tuple[dict[str, str], ...] = (
    *(dict(item) for item in list_categories()),
    *COVER_ONLY_CATEGORY_OPTIONS,
)

_COVER_CATEGORY_ALIASES: dict[str, str] = {}
_COVER_CATEGORY_ALIASES["分类插画"] = "illustration"
_COVER_CATEGORY_ALIASES["分类插画（蜡笔）"] = "illustration_crayon"
_COVER_CATEGORY_ALIASES["分类插画(蜡笔)"] = "illustration_crayon"
for _category in COVER_CATEGORY_OPTIONS:
    _COVER_CATEGORY_ALIASES[_category["id"]] = _category["id"]
    _COVER_CATEGORY_ALIASES[_category["label"]] = _category["id"]


def list_cover_categories() -> list[dict[str, str]]:
    return [dict(category) for category in COVER_CATEGORY_OPTIONS]


def normalize_cover_category_id(category: str | None, fallback: str | None = None) -> str:
    raw = (category or fallback or "social_science").strip() or "social_science"
    normalized = _COVER_CATEGORY_ALIASES.get(raw)
    if normalized:
        return normalized
    return normalize_category(raw)


def get_cover_category_meta(category: str | None) -> dict[str, str]:
    normalized = normalize_cover_category_id(category)
    for item in COVER_CATEGORY_OPTIONS:
        if item["id"] == normalized:
            return dict(item)
    return get_category_meta(normalized)


__all__ = [
    "COVER_CATEGORY_OPTIONS",
    "COVER_ONLY_CATEGORY_OPTIONS",
    "get_cover_category_meta",
    "list_cover_categories",
    "normalize_cover_category_id",
]
