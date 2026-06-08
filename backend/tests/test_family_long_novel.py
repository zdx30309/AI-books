from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.api.api import api_router
from app.db.session import Base
from app.prompts.family_long_novel_prompts import (
    COMMON_GENRE_RULES,
    FAMILY_LONG_NOVEL_LABEL,
    FAMILY_LONG_NOVEL_TYPE,
    ZHOULI_BIBLE_PROMPT,
    ZHOULI_CHAPTER_WRITE_PROMPT,
    ZHOULI_CONTINUITY_CHECK_PROMPT,
)
from app.services.family_long_novel import _json_loads


def test_family_long_novel_prompts_are_dedicated() -> None:
    assert FAMILY_LONG_NOVEL_TYPE == "family_sisters_in_law_longform"
    assert FAMILY_LONG_NOVEL_LABEL == "家长里短·妯娌"
    for prompt in [ZHOULI_BIBLE_PROMPT, ZHOULI_CHAPTER_WRITE_PROMPT, ZHOULI_CONTINUITY_CHECK_PROMPT]:
        assert "{common_rules}" in prompt
    assert "家长里短" in COMMON_GENRE_RULES
    assert "妯娌" in COMMON_GENRE_RULES
    assert "重生" in COMMON_GENRE_RULES
    assert "系统" in COMMON_GENRE_RULES


def test_family_long_novel_tables_are_registered() -> None:
    expected = {
        "long_novel_projects",
        "long_novel_bibles",
        "long_novel_family_members",
        "long_novel_family_relations",
        "long_novel_conflict_lines",
        "long_novel_chapter_plans",
        "long_novel_chapters",
        "long_novel_events",
        "long_novel_canon_facts",
        "long_novel_character_states",
        "long_novel_object_states",
        "long_novel_continuity_issues",
        "long_novel_generation_logs",
    }
    assert expected.issubset(set(Base.metadata.tables))


def test_family_long_novel_routes_are_registered() -> None:
    paths = {getattr(route, "path", "") for route in api_router.routes}
    assert "/api/v1/long-novel/family/projects" in paths
    assert "/api/v1/long-novel/family/projects/{project_id}/bible" in paths
    assert "/api/v1/long-novel/family/projects/{project_id}/generate-batch" in paths
    assert "/api/v1/long-novel/family/projects/{project_id}/export" in paths


def test_json_loader_accepts_fenced_model_output() -> None:
    parsed = _json_loads('```json\n{"title": "账要算清", "chapters": []}\n```')
    assert parsed["title"] == "账要算清"
    assert parsed["chapters"] == []
