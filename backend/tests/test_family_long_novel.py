from __future__ import annotations

import inspect
from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.api.api import api_router
from app.db.session import Base
from app.models import LongNovelProject
from app.prompts.family_long_novel_prompts import (
    COMMON_GENRE_RULES,
    FAST_PACING_AND_CUSTOM_PLOT_RULES,
    FAMILY_LONG_NOVEL_LABEL,
    FAMILY_LONG_NOVEL_TYPE,
    ZHOULI_30W_CONTINUATION_REPLAN_PROMPT,
    ZHOULI_BIBLE_PROMPT,
    ZHOULI_CHAPTER_PLAN_PROMPT,
    ZHOULI_CHAPTER_WRITE_PROMPT,
    ZHOULI_CONTINUITY_CHECK_PROMPT,
    ZHOULI_OUTLINE_PROMPT,
    ZHOULI_QUALITY_CHECK_PROMPT,
    render_prompt,
)
from app.prompts.novel_quality_prompts import GENERAL_NOVEL_PROSE_GUARDRAILS
from app.schemas.long_novel import LongNovelAssistRequest
from app.services import family_long_novel
from app.services.family_long_novel import (
    FamilyLongNovelError,
    MalformedModelJsonError,
    _chapter_target_words,
    _chapter_write_max_output_tokens,
    _compact_context_for_prompt,
    _json_loads,
    _outline_max_output_tokens,
    validate_chapter_context_pack,
)


def test_family_long_novel_prompts_are_dedicated() -> None:
    assert FAMILY_LONG_NOVEL_TYPE == "family_sisters_in_law_longform"
    assert FAMILY_LONG_NOVEL_LABEL == "家长里短·妯娌"
    for prompt in [
        ZHOULI_BIBLE_PROMPT,
        ZHOULI_OUTLINE_PROMPT,
        ZHOULI_CHAPTER_PLAN_PROMPT,
        ZHOULI_CHAPTER_WRITE_PROMPT,
        ZHOULI_CONTINUITY_CHECK_PROMPT,
    ]:
        assert "{common_rules}" in prompt
    assert "家长里短" in COMMON_GENRE_RULES
    assert "妯娌" in COMMON_GENRE_RULES
    assert "重生" in COMMON_GENRE_RULES
    assert "系统" in COMMON_GENRE_RULES
    assert "开章要更快进入矛盾" in FAST_PACING_AND_CUSTOM_PLOT_RULES
    assert "sourceType 是 custom_plot" in FAST_PACING_AND_CUSTOM_PLOT_RULES
    rendered = render_prompt(ZHOULI_CHAPTER_WRITE_PROMPT, chapter_context="{}")
    assert "开章要更快进入矛盾" in rendered
    assert "sourceType 是 custom_plot" in rendered
    assert "开头过慢" in ZHOULI_QUALITY_CHECK_PROMPT
    assert "扩容上下文" in ZHOULI_30W_CONTINUATION_REPLAN_PROMPT
    assert "只规划后续，不改写已完成章节正文" in ZHOULI_30W_CONTINUATION_REPLAN_PROMPT


def test_general_novel_guardrails_cover_editor_ai_prose_complaints() -> None:
    rules = GENERAL_NOVEL_PROSE_GUARDRAILS
    for phrase in [
        "机械复述前文",
        "滥用煽情",
        "跳出故事",
        "疼得不行",
        "一遍又一遍",
        "对白必须",
    ]:
        assert phrase in rules
    assert "家长里短" not in rules
    assert "妯娌" not in rules


def test_family_workflow_appends_general_novel_prose_guardrails() -> None:
    source = "\n".join(
        inspect.getsource(func)
        for func in [
            family_long_novel.generate_family_chapter_plan,
            family_long_novel.generate_family_chapter,
            family_long_novel.check_family_continuity,
            family_long_novel.quality_check_family_chapter,
            family_long_novel.repair_family_chapter,
        ]
    )
    assert source.count("GENERAL_NOVEL_PROSE_GUARDRAILS") == 5


def test_sister_account_extension_uses_system_replan_not_local_templates() -> None:
    source = inspect.getsource(family_long_novel.extend_family_project_to_target)
    assert "_call_text" not in source
    assert "_call_json" in source
    assert "_route_writer_call" not in source
    assert "LongNovelChapter(" not in source
    assert "_write_future_plans_from_replan" in source
    assert "replan_30w_continuation" in source
    assert "ZHOULI_30W_CONTINUATION_REPLAN_PROMPT" in source
    assert "_is_sister_account_project" in source
    writer_source = inspect.getsource(family_long_novel._write_future_plans_from_replan)
    assert "LongNovelChapterPlan" in writer_source
    assert "chapter_number > current_chapter" in writer_source


def test_sister_account_extension_validates_full_future_coverage() -> None:
    source = inspect.getsource(family_long_novel._validate_continuation_replan)
    assert "missing=" in source
    assert "duplicates=" in source
    assert "unique_conflicts" in source


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
    assert LongNovelProject.__table__.c.type.default.arg == "long_novel"


def test_family_long_novel_routes_are_registered() -> None:
    paths = {getattr(route, "path", "") for route in api_router.routes}
    assert "/api/v1/long-novel/assist" in paths
    assert "/api/v1/long-novel/family/projects" in paths
    assert "/api/v1/long-novel/family/projects/{project_id}/bible" in paths
    assert "/api/v1/long-novel/family/projects/{project_id}/extend-target" in paths
    assert "/api/v1/long-novel/family/projects/{project_id}/generate-batch" in paths
    assert "/api/v1/long-novel/family/projects/{project_id}/chapters/{chapter_number}/context-pack" in paths
    assert "/api/v1/long-novel/family/projects/{project_id}/export" in paths


def test_long_novel_assist_is_short_explicit_openai_form_help(monkeypatch) -> None:
    calls: dict[str, object] = {}

    def fake_run_writer(prompt: str, **kwargs: object) -> str:
        calls.update(kwargs)
        assert "只帮助填写创建表单" in prompt
        assert "不生成正文" in prompt
        return '{"content":"大嫂照顾婆婆多年，拆迁款下来后，小叔一家突然要分走老宅。","conflict_tags":["婆媳偏心","兄弟分家","房产归属"]}'

    monkeypatch.setattr(family_long_novel, "_run_writer", fake_run_writer)
    res = family_long_novel.assist_long_novel_field(
        LongNovelAssistRequest(
            field="premise",
            current_values={"premise": "大嫂照顾婆婆多年"},
        )
    )

    assert res.content.startswith("大嫂照顾婆婆多年")
    assert res.conflict_tags == ["婆媳偏心", "兄弟分家", "房产归属"]
    assert calls["max_output_tokens"] == 1200
    assert calls["reasoning_effort"] == "xhigh"
    assert calls["verbosity"] == "low"


def test_outline_budget_scales_with_chapter_count() -> None:
    project = LongNovelProject(premise="大嫂照顾婆婆多年", target_chapters=10)
    assert _outline_max_output_tokens(project) == 14000

    project.target_chapters = 50
    assert _outline_max_output_tokens(project) == 32000


def test_chapter_word_targets_are_uniform() -> None:
    project = LongNovelProject(
        premise="大嫂照顾婆婆多年",
        target_words=80000,
        target_chapters=10,
    )
    assert _chapter_target_words(project) == 8000
    assert _chapter_write_max_output_tokens(project) == 32000

    project.target_words = 200000
    project.target_chapters = 50
    assert _chapter_target_words(project) == 4000
    assert _chapter_write_max_output_tokens(project) == 24000


def test_json_loader_accepts_fenced_model_output() -> None:
    parsed = _json_loads('```json\n{"title": "账要算清", "chapters": []}\n```')
    assert parsed["title"] == "账要算清"
    assert parsed["chapters"] == []


def test_json_loader_repairs_missing_comma_between_objects() -> None:
    parsed = _json_loads('{"issues":[{"severity":"low"}\n{"severity":"high"}]}')
    assert parsed["issues"] == [{"severity": "low"}, {"severity": "high"}]


def test_json_loader_raises_typed_error_after_repair_fails() -> None:
    with pytest.raises(MalformedModelJsonError):
        _json_loads('{"issues": [ invalid ]}')


def test_context_pack_validation_blocks_missing_previous_state() -> None:
    with pytest.raises(FamilyLongNovelError) as exc:
        validate_chapter_context_pack(
            {
                "chapterNumber": 2,
                "previousChapter": {"summary": "", "endingHook": ""},
                "latestCharacterStates": [],
                "activeConflictLines": [],
                "canonFacts": [],
            },
            2,
        )

    message = str(exc.value)
    assert "previous chapter summary" in message
    assert "persisted character state" in message
    assert "active conflict lines" in message
    assert "canon facts" in message


def test_context_pack_validation_accepts_durable_previous_state() -> None:
    validate_chapter_context_pack(
        {
            "chapterNumber": 2,
            "previousChapter": {"summary": "Chapter one summary", "endingHook": "Doorbell rings"},
            "latestCharacterStates": [{"memberName": "A", "sourceChapterNumber": 1}],
            "activeConflictLines": [{"conflictKey": "house", "name": "House dispute"}],
            "canonFacts": [{"chapterNumber": 1, "fact": "The house certificate is missing"}],
        },
        2,
    )


def test_prompt_context_compaction_preserves_quality_fields_without_raw_payloads() -> None:
    full_context = {
        "contextPackVersion": 2,
        "chapterNumber": 5,
        "project": {
            "premise": "family premise",
            "sourceText": "x" * 5000,
            "targetWords": 80000,
            "targetChapters": 10,
        },
        "bible": {
            "theme": "theme",
            "corePromise": "promise",
            "familyMembers": [{"name": "A", "familyRole": "heroine", "raw": {"drop": True}}],
            "rawLargeSection": "drop me",
        },
        "currentPlanRecord": {
            "chapterNumber": 5,
            "targetWords": 8000,
            "chapterGoal": "goal" * 200,
            "domesticConflict": "conflict",
            "raw": {"drop": True},
            "scenes": [{"sceneNumber": 1, "sceneGoal": "scene", "raw": {"drop": True}}],
        },
        "currentPlan": {"raw": "drop me"},
        "previousChapter": {
            "chapterNumber": 4,
            "summary": "summary",
            "endingHook": "hook",
            "last800Chars": "z" * 900,
        },
        "recentChapters": [{"chapterNumber": 4, "summary": "summary", "endingHook": "hook"}],
        "familyRelations": [{"fromMember": "A", "toMember": "B", "hiddenConflict": "hidden"}],
        "activeConflictLines": [{"conflictKey": "c1", "name": "conflict", "latestUpdate": "update"}],
        "latestCharacterStates": [{"memberName": "A", "sourceChapterNumber": 4, "knowledge": ["knows"]}],
        "objectStates": [{"objectName": "paper", "sourceChapterNumber": 4, "holder": "A"}],
        "canonFacts": [{"chapterNumber": 4, "fact": "fact"}],
        "openContinuityIssues": [{"chapterNumber": 3, "description": "issue"}],
        "mustContinueFrom": {
            "previousEndingHook": "hook",
            "previousChapterSummary": "summary",
            "previousLast800Chars": "z" * 900,
            "requiredCarryOver": ["carry"],
            "strictRules": ["rule"],
        },
    }

    compact = _compact_context_for_prompt(full_context, "chapter_write")

    assert compact["promptContextMode"] == "compact"
    assert "sourceText" not in compact["project"]
    assert len(compact["project"]["sourceTextPreview"]) == 800
    assert "currentPlan" not in compact
    assert "raw" not in compact["currentPlanRecord"]
    assert compact["previousChapter"]["endingHook"] == "hook"
    assert len(compact["previousChapter"]["last800Chars"]) == 800
    assert compact["activeConflictLines"][0]["latestUpdate"] == "update"
    assert compact["latestCharacterStates"][0]["memberName"] == "A"
    assert compact["canonFacts"][0]["fact"] == "fact"
