"""Frozen May 2026 templates, independent of the current comic prompt registry."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

VERSION = "classic_20260513"
PROMPT_DIR = Path(__file__).with_name(VERSION)
MANIFEST = json.loads((PROMPT_DIR / "manifest.json").read_text(encoding="utf-8"))

# Categories introduced after May retain their bookshelf identity but use the
# closest historical subject contract. There was no dedicated finance prompt.
CATEGORY_MAP = {
    "social_science_test": "social_science",
    "social_science_core_conflict": "social_science",
    "life_philosophy_self_rescue": "life_philosophy",
    "popular_science_cognitive_upgrade": "popular_science",
    "wisdom_stories_causal_philosophy": "wisdom_stories",
    "wisdom_stories_historical": "wisdom_stories",
    "emotion_relationship_unique_view": "emotion_relationship",
    "health_wellness_daily_pain": "health_wellness",
    "masterpiece_interpretation": "classic_interpretation",
    "finance_technology": "social_science",
    "finance_technology_recommendation": "social_science",
    "finance_technology_ai_optimized": "social_science",
    "classical_chinese_interleaved": "classical_chinese",
    "classical_chinese_reference_rewrite": "classical_chinese",
    "buddhist_scripture_story": "classical_chinese",
}
COMIC_CATEGORIES = frozenset({"comic_series", "healing_comic", "story_comic", "homophone_comic"})


def base_category(category: str) -> str:
    base = CATEGORY_MAP.get(category, category)
    if base not in MANIFEST["registry"]:
        raise ValueError(f"No historical text category for {category!r}")
    return base


def get_prompts(category: str) -> dict[str, str]:
    result = {}
    for stage, name in MANIFEST["registry"][base_category(category)].items():
        value = (PROMPT_DIR / name).read_text(encoding="utf-8")
        if hashlib.sha256(value.encode()).hexdigest() != MANIFEST["sha256"][name]:
            raise ValueError(f"Historical prompt checksum mismatch: {name}")
        result[stage] = value
    return result


def load_auxiliary_template(name: str) -> str:
    if name not in {"repair_front_matter.md", "history_style.md"}:
        raise ValueError(f"Unknown auxiliary template: {name!r}")
    value = (PROMPT_DIR / name).read_text(encoding="utf-8")
    if hashlib.sha256(value.encode()).hexdigest() != MANIFEST["sha256"][name]:
        raise ValueError(f"Historical prompt checksum mismatch: {name}")
    return value


def pin_new_task(config: dict) -> None:
    category = config.get("category", "social_science")
    if category in COMIC_CATEGORIES:
        config.pop("text_workflow_version", None)
        config.pop("text_prompt_base_category", None)
        return
    config["text_prompt_base_category"] = base_category(category)
    config["text_workflow_version"] = VERSION


def workflow_for_existing_task(config: dict) -> str:
    version = config.get("text_workflow_version") or "legacy_20260704"
    if version not in {VERSION, "legacy_20260704"}:
        raise ValueError(f"Unknown text workflow: {version!r}")
    return version
