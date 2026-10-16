"""社科2610: user supplied Word prompt, no additional writing instructions."""
from pathlib import Path

CATEGORY = "social_science_test"
VERSION = "sheke2610_astra_pro_20261016"
MODEL = "gpt-6-astra"
REASONING_MODE = "pro"
PROMPT_PATH = Path(__file__).with_name("sheke2610_writer.md")


def render_prompt(raw_text: str) -> str:
    # Replace only the source slot; never reinterpret braces in source material.
    return PROMPT_PATH.read_text(encoding="utf-8").replace("{raw_text}", raw_text)
