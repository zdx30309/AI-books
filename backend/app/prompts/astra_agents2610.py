"""Three versioned agents using the date-pinned user Word templates."""
from pathlib import Path
MODEL = "gpt-6-astra"
PROMPT_PATH = Path(__file__).with_name("general_rewrite_20261028.md")
PREVIOUS_PROMPT_PATH = Path(__file__).with_name("general_rewrite_20261027.md")
AGENTS = {
    "finance_technology_2610": {"label": "财经·科技2610", "base_category": "finance_technology", "version": "finance2610_classic_astra_20261028"},
    "psychology_emotion_2610": {"label": "心理情感2610", "base_category": "emotion_relationship", "version": "psychology2610_classic_astra_20261028"},
    "health_wellness_2610": {"label": "健康养生2610", "base_category": "health_wellness", "version": "health2610_classic_astra_20261028"},
}
# Existing tasks retain their pinned prompt and response receipt identity.
VERSION_CATEGORIES = {agent["version"]: category for category, agent in AGENTS.items()}
VERSION_CATEGORIES.update({agent["version"].replace("20261028", "20261027"): category for category, agent in AGENTS.items()})
VERSIONS = frozenset(VERSION_CATEGORIES)

def validate_workflow(category: str, version: str) -> None:
    if VERSION_CATEGORIES.get(version) != category:
        raise ValueError("2610智能体工作流类别不一致")

def render_prompt(manuscript: str, *, workflow_version: str | None = None) -> str:
    if workflow_version is not None and workflow_version not in VERSIONS:
        raise ValueError("Unknown Astra rewrite workflow")
    path = PREVIOUS_PROMPT_PATH if workflow_version and workflow_version.endswith("20261027") else PROMPT_PATH
    return path.read_text(encoding="utf-8").replace("{raw_text}", manuscript)
