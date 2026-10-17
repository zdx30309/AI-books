"""User Word template applied to the completed classic manuscript."""
from pathlib import Path
CATEGORY = 'social_pain_2610'
VERSION = 'social_pain2610_classic_astra_20261017'
MODEL = 'gpt-6-astra'
PROMPT_PATH = Path(__file__).with_name('social_pain2610_rewrite.md')
def render_prompt(manuscript: str) -> str:
    return PROMPT_PATH.read_text(encoding='utf-8').replace('{raw_text}', manuscript)
