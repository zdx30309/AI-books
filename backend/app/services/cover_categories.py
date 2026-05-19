"""Cover-only category registry.

Standalone cover generation uses Excel metadata rather than manuscript body
generation, so it can expose cover-specific visual categories without adding
them to the book-writing category list.
"""
from __future__ import annotations

from app.prompts.goal_driven_templates import get_category_meta

BASE_COVER_CATEGORY_IDS: tuple[str, ...] = (
    "social_science",
)

_COVER_CATEGORY_LABEL_OVERRIDES: dict[str, str] = {
    "social_science": "视觉冲击",
}


def _cover_category_item(item: dict[str, str]) -> dict[str, str]:
    next_item = dict(item)
    label = _COVER_CATEGORY_LABEL_OVERRIDES.get(next_item.get("id", ""))
    if label:
        next_item["label"] = label
    return next_item


COVER_ONLY_CATEGORY_OPTIONS: tuple[dict[str, str], ...] = (
    {
        "id": "illustration",
        "label": "饱和插画",
        "core_feature": "高饱和插画封面入口，默认复用心理自助封面 prompt",
        "directions": "高饱和、情绪疗愈、插画风封面",
        "prompt_status": "ready",
    },
    {
        "id": "illustration_crayon",
        "label": "萌系治愈",
        "core_feature": "温柔情绪插画封面，强调蜡笔质感与治愈氛围",
        "directions": "情绪日记、心理自助、孤独感、成年人治愈读物",
        "prompt_status": "ready",
    },
    {
        "id": "literature",
        "label": "正统出版",
        "core_feature": "文艺文学感出版封面，克制留白、现代书籍气质",
        "directions": "文学、散文、小说、诗意叙事、现代出版设计",
        "prompt_status": "ready",
    },
)

COVER_CATEGORY_OPTIONS: tuple[dict[str, str], ...] = (
    *(_cover_category_item(get_category_meta(category_id)) for category_id in BASE_COVER_CATEGORY_IDS),
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
_register_alias("文学", "literature")
_register_alias("正统出版", "literature")
_register_alias("治愈插画", "illustration")
_register_alias("治愈插画（蜡笔）", "illustration_crayon")
_register_alias("文学类", "literature")
_register_alias("文学题材", "literature")
_register_alias("文艺治愈", "literature")
_register_alias("饱和插画", "illustration")
_register_alias("萌系治愈", "illustration_crayon")
_register_alias("社会痛点", "social_science")
_register_alias("视觉冲击", "social_science")
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
    return _cover_category_item(get_category_meta(normalized))


__all__ = [
    "COVER_CATEGORY_OPTIONS",
    "COVER_ONLY_CATEGORY_OPTIONS",
    "BASE_COVER_CATEGORY_IDS",
    "get_cover_category_meta",
    "list_cover_categories",
    "normalize_cover_category_id",
]
