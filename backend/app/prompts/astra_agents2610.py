"""Versioned agents using the date-pinned user Word templates."""
from pathlib import Path
MODEL = "gpt-6-astra"
PROMPT_PATH = Path(__file__).with_name("general_rewrite_20261102.md")
PREVIOUS_PROMPT_PATH = Path(__file__).with_name("general_rewrite_20261027.md")
PROMPT_PATHS = {date: Path(__file__).with_name(f"general_rewrite_{date}.md")
                for date in ("20261027", "20261028", "20261029", "20261102", "20261103", "20261104", "20261104v2", "20261105")}
AGENTS = {
    "general_manuscript": {"label": "通用书稿", "base_category": "popular_science", "version": "general_manuscript_astra_direct_20261105", "direct": True, "directions": "不限题材，以视频原文为准", "core_feature": "GPT-6 Astra standard/high，视频原文加1105版Word指令词，单次直出"},
    "finance_technology_2610": {"label": "财经·科技2610", "base_category": "finance_technology", "version": "finance2610_classic_astra_20261028"},
    "psychology_emotion_2610": {"label": "心理情感2610", "base_category": "emotion_relationship", "version": "psychology2610_classic_astra_20261102"},
    "health_wellness_2610": {"label": "健康养生2610", "base_category": "health_wellness", "version": "health2610_classic_astra_20261104v2"},
    "social_pain_2610_2": {"label": "社会痛点2610-2", "base_category": "social_science", "version": "social_pain2610_2_classic_astra_20261102"},
    "social_pain_2611_astra_direct": {"label": "社会痛点2611(Astra直出)", "base_category": "social_science", "version": "social_pain2611_astra_direct_20261102", "direct": True},
}
# Existing tasks retain their pinned prompt and response receipt identity.
VERSION_CATEGORIES = {agent["version"]: category for category, agent in AGENTS.items()}
for category, agent in AGENTS.items():
    prefix = agent["version"].rsplit("_", 1)[0]
    dates = {"finance_technology_2610": ("20261027", "20261028"),
             "psychology_emotion_2610": ("20261027", "20261028", "20261029"),
             "health_wellness_2610": ("20261027", "20261028", "20261029", "20261102", "20261103", "20261104"),
             "social_pain_2610_2": ("20261029",)}.get(category, ())
    VERSION_CATEGORIES.update({f"{prefix}_{date}": category for date in dates})
VERSIONS = frozenset(VERSION_CATEGORIES)
DIRECT_VERSIONS = frozenset(v for v, c in VERSION_CATEGORIES.items() if AGENTS[c].get("direct"))

def validate_workflow(category: str, version: str) -> None:
    if VERSION_CATEGORIES.get(version) != category:
        raise ValueError("2610智能体工作流类别不一致")

def render_prompt(manuscript: str, *, workflow_version: str | None = None) -> str:
    if workflow_version is not None and workflow_version not in VERSIONS:
        raise ValueError("Unknown Astra rewrite workflow")
    path = PROMPT_PATHS[workflow_version.rsplit("_", 1)[1]] if workflow_version else PROMPT_PATH
    return path.read_text(encoding="utf-8").replace("{raw_text}", manuscript)
