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
    "finance_technology_2610": "social_science",
    "psychology_emotion_2610": "emotion_relationship",
    "health_wellness_2610": "health_wellness",
    "social_pain_2610": "social_science",
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
    from app.services.bookshelf_versions import category_shelf_version
    config["bookshelf_version"] = category_shelf_version(category)
    if category in COMIC_CATEGORIES:
        config.pop("text_workflow_version", None)
        config.pop("text_prompt_base_category", None)
        return
    from app.prompts.sheke2610 import CATEGORY, VERSION as DIRECT_VERSION
    if category == CATEGORY:
        config.pop("text_prompt_base_category", None)
        config["text_workflow_version"] = DIRECT_VERSION
        return
    from app.prompts.social_pain2610 import CATEGORY as HYBRID_CATEGORY, VERSION as HYBRID_VERSION
    if category == HYBRID_CATEGORY:
        config["text_prompt_base_category"] = "social_science"
        config["text_workflow_version"] = HYBRID_VERSION
        return
    from app.prompts.astra_agents2610 import AGENTS
    if category in AGENTS:
        config["text_prompt_base_category"] = base_category(AGENTS[category]["base_category"])
        config["text_workflow_version"] = AGENTS[category]["version"]
        return
    config["text_prompt_base_category"] = base_category(category)
    config["text_workflow_version"] = VERSION


def workflow_for_existing_task(config: dict) -> str:
    version = config.get("text_workflow_version") or "legacy_20260704"
    from app.prompts.sheke2610 import CATEGORY, VERSION as DIRECT_VERSION
    from app.prompts.social_pain2610 import CATEGORY as HYBRID_CATEGORY, VERSION as HYBRID_VERSION
    if version == HYBRID_VERSION:
        if config.get("category") != HYBRID_CATEGORY:
            raise ValueError("社会痛点2610工作流类别不一致")
        return version
    if version == DIRECT_VERSION:
        if config.get("category") != CATEGORY:
            raise ValueError("社科2610工作流仅支持social_science_test")
        return version
    from app.prompts.astra_agents2610 import AGENTS, VERSIONS
    if version in VERSIONS:
        if AGENTS.get(config.get("category"), {}).get("version") != version:
            raise ValueError("2610智能体工作流类别不一致")
        return version
    if version not in {VERSION, "legacy_20260704"}:
        raise ValueError(f"Unknown text workflow: {version!r}")
    return version
