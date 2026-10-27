"""Three versioned agents using the October 27 user Word template."""
from pathlib import Path
MODEL = "gpt-6-astra"
PROMPT_PATH = Path(__file__).with_name("general_rewrite_20261027.md")
AGENTS = {
    "finance_technology_2610": {"label": "财经·科技2610", "base_category": "finance_technology", "version": "finance2610_classic_astra_20261027"},
    "psychology_emotion_2610": {"label": "心理情感2610", "base_category": "emotion_relationship", "version": "psychology2610_classic_astra_20261027"},
    "health_wellness_2610": {"label": "健康养生2610", "base_category": "health_wellness", "version": "health2610_classic_astra_20261027"},
}
VERSIONS = frozenset(agent["version"] for agent in AGENTS.values())
def render_prompt(manuscript: str) -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").replace("{raw_text}", manuscript)
