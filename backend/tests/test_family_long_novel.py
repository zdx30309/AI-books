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
from sqlalchemy import Text

from app.models import LongNovelGenerationLog, LongNovelObjectState, LongNovelProject
from app.prompts.family_long_novel_prompts import (
    COMMON_GENRE_RULES,
    FAST_PACING_AND_CUSTOM_PLOT_RULES,
    FAMILY_LONG_NOVEL_LABEL,
    FAMILY_LONG_NOVEL_TYPE,
    ZHOULI_30W_CHAPTER_BATCH_REPLAN_PROMPT,
    ZHOULI_30W_CONTINUATION_REPLAN_PROMPT,
    ZHOULI_30W_GLOBAL_REPLAN_PROMPT,
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
from app.services import content_engine
from app.services.family_long_novel import (
    FamilyLongNovelError,
    MalformedModelJsonError,
    _chapter_target_words,
    _chapter_write_max_output_tokens,
    _compact_context_for_prompt,
    _json_loads,
    _outline_max_output_tokens,
    _stable_project_cache_prefix,
    _with_project_cache_prefix,
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
    assert "只做全局承接规划" in ZHOULI_30W_GLOBAL_REPLAN_PROMPT
    assert "分批上下文" in ZHOULI_30W_CHAPTER_BATCH_REPLAN_PROMPT


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
    assert "_route_writer_call" not in source
    assert "LongNovelChapter(" not in source
    assert "_write_future_plans_from_replan" in source
    assert "_run_batched_continuation_replan" in source
    assert "_is_sister_account_project" in source
    batch_source = inspect.getsource(family_long_novel._run_batched_continuation_replan)
    assert "_call_json" in batch_source
    assert "replan_target_global" in batch_source
    assert "replan_target_batch_" in batch_source
    assert "batch_size = 3" in batch_source
    assert "batch_start == batch_end" in batch_source
    assert "max_output_tokens=6000" in batch_source
    assert "ZHOULI_30W_GLOBAL_REPLAN_PROMPT" in batch_source
    assert "ZHOULI_30W_CHAPTER_BATCH_REPLAN_PROMPT" in batch_source
    assert "expectedChapterNumbers" in inspect.getsource(family_long_novel._continuation_batch_context)
    writer_source = inspect.getsource(family_long_novel._write_future_plans_from_replan)
    assert "LongNovelChapterPlan" in writer_source
    assert "chapter_number > current_chapter" in writer_source


def test_sister_account_extension_validates_full_future_coverage() -> None:
    source = inspect.getsource(family_long_novel._validate_continuation_replan)
    assert "missing=" in source
    assert "duplicates=" in source
    assert "unique_conflicts" in source


def test_sister_account_extension_can_replan_down_to_20w_after_30w() -> None:
    source = inspect.getsource(family_long_novel.extend_family_project_to_target)

    assert "target_words: int = 200000" in source
    assert "requested_target_words" in source
    assert "current_chapter + future_count" in source
    assert "max(int(project.target_chapters" not in source
    assert "system_replan_target_continuation" in source


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
    assert isinstance(LongNovelObjectState.__table__.c.status.type, Text)
    assert "cached_prompt_tokens" in LongNovelGenerationLog.__table__.c
    assert "reasoning_tokens" in LongNovelGenerationLog.__table__.c


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
    assert "project" not in compact
    assert "bible" not in compact
    assert compact["projectControls"]["targetWords"] == 80000
    stable_prefix = _stable_project_cache_prefix(full_context)
    assert "sourceTextPreview" in stable_prefix
    assert "x" * 800 in stable_prefix
    assert "currentPlan" not in compact
    assert "raw" not in compact["currentPlanRecord"]
    assert compact["previousChapter"]["endingHook"] == "hook"
    assert len(compact["previousChapter"]["last800Chars"]) == 800
    assert compact["activeConflictLines"][0]["latestUpdate"] == "update"
    assert compact["latestCharacterStates"][0]["memberName"] == "A"
    assert compact["canonFacts"][0]["fact"] == "fact"


def test_prompt_context_compaction_keeps_stable_prefix_before_chapter_delta() -> None:
    compact = _compact_context_for_prompt(
        {
            "contextPackVersion": 2,
            "chapterNumber": 12,
            "project": {"premise": "premise"},
            "bible": {"theme": "theme"},
            "currentPlanRecord": {"chapterNumber": 12, "chapterGoal": "goal"},
            "previousChapter": {"chapterNumber": 11, "summary": "summary", "endingHook": "hook"},
            "familyRelations": [{"fromMember": "A", "toMember": "B"}],
            "activeConflictLines": [{"conflictKey": "c1", "name": "conflict"}],
            "latestCharacterStates": [{"memberName": "A", "sourceChapterNumber": 11}],
            "objectStates": [{"objectName": "ledger", "sourceChapterNumber": 11}],
            "canonFacts": [{"chapterNumber": 11, "fact": "fact"}],
            "mustContinueFrom": {"previousEndingHook": "hook", "strictRules": ["rule"]},
        },
        "chapter_write",
    )

    keys = list(compact)
    assert keys.index("stableProjectContext") < keys.index("chapterNumber")
    assert keys.index("projectControls") < keys.index("currentPlanRecord")
    assert keys.index("familyRelations") < keys.index("previousChapter")
    assert keys.index("activeConflictLines") < keys.index("mustContinueFrom")
    assert compact["previousChapter"]["endingHook"] == "hook"
    assert compact["mustContinueFrom"]["strictRules"] == ["rule"]


def test_stable_project_cache_prefix_ignores_chapter_specific_delta() -> None:
    base_context = {
        "contextPackVersion": 2,
        "chapterNumber": 12,
        "project": {"premise": "premise", "targetWords": 200000, "targetChapters": 41},
        "bible": {
            "theme": "theme",
            "corePromise": "promise",
            "familyMembers": [{"name": "A", "familyRole": "heroine", "privateDesire": "fairness"}],
        },
        "currentPlanRecord": {"chapterNumber": 12, "chapterGoal": "goal 12"},
        "previousChapter": {"chapterNumber": 11, "summary": "summary 11", "endingHook": "hook 11"},
        "familyRelations": [{"fromMember": "B", "toMember": "A", "relationType": "kin"}],
        "activeConflictLines": [
            {
                "conflictKey": "house",
                "name": "house conflict",
                "description": "stable description",
                "latestUpdate": "dynamic update 12",
            }
        ],
        "latestCharacterStates": [{"memberName": "A", "sourceChapterNumber": 11, "goal": "dynamic goal"}],
        "objectStates": [{"objectName": "ledger", "sourceChapterNumber": 11, "status": "dynamic"}],
        "canonFacts": [{"chapterNumber": 11, "fact": "dynamic fact"}],
        "mustContinueFrom": {"previousEndingHook": "hook 11", "strictRules": ["rule"]},
    }
    next_context = {
        **base_context,
        "chapterNumber": 13,
        "currentPlanRecord": {"chapterNumber": 13, "chapterGoal": "goal 13"},
        "previousChapter": {"chapterNumber": 12, "summary": "summary 12", "endingHook": "hook 12"},
        "activeConflictLines": [
            {
                "conflictKey": "house",
                "name": "house conflict",
                "description": "stable description",
                "latestUpdate": "dynamic update 13",
            }
        ],
        "mustContinueFrom": {"previousEndingHook": "hook 12", "strictRules": ["rule"]},
    }

    assert _stable_project_cache_prefix(base_context) == _stable_project_cache_prefix(next_context)
    prompt = _with_project_cache_prefix(base_context, "CHAPTER DELTA")
    assert prompt.startswith("【长篇项目稳定缓存前缀】")
    assert prompt.endswith("CHAPTER DELTA")


def test_dynamic_context_does_not_repeat_stable_project_blueprints() -> None:
    context = {
        "contextPackVersion": 2,
        "chapterNumber": 13,
        "project": {
            "premise": "stable premise",
            "targetWords": 200000,
            "targetChapters": 41,
            "sourceType": "custom_plot",
        },
        "bible": {"theme": "stable theme", "corePromise": "stable promise"},
        "familyRelations": [
            {
                "fromMember": "A",
                "toMember": "B",
                "relationType": "kin",
                "hiddenConflict": "stable hidden conflict",
                "currentTensionLevel": 7,
            }
        ],
        "activeConflictLines": [
            {
                "conflictKey": "debt",
                "name": "debt line",
                "description": "stable conflict description",
                "latestUpdate": "chapter 12 update",
                "status": "escalating",
            }
        ],
        "previousChapter": {"chapterNumber": 12, "summary": "summary", "endingHook": "hook"},
        "latestCharacterStates": [{"memberName": "A", "sourceChapterNumber": 12}],
        "objectStates": [{"objectName": "ledger", "sourceChapterNumber": 12}],
        "canonFacts": [{"chapterNumber": 12, "fact": "fact"}],
        "mustContinueFrom": {"previousEndingHook": "hook", "strictRules": ["rule"]},
    }

    prefix = _stable_project_cache_prefix(context)
    compact_json = family_long_novel._context_prompt_json(context, "chapter_write")
    combined_prompt = _with_project_cache_prefix(context, compact_json)

    assert "stable premise" in prefix
    assert "stable theme" in prefix
    assert "stable hidden conflict" in prefix
    assert "stable conflict description" in prefix
    assert "stable premise" not in compact_json
    assert "stable theme" not in compact_json
    assert "stable hidden conflict" not in compact_json
    assert "stable conflict description" not in compact_json
    assert "chapter 12 update" not in compact_json
    assert "chapter 12 update" in combined_prompt
    assert '"currentTensionLevel": 7' in combined_prompt
    assert _json_loads(compact_json)["promptContextMode"] == "action_delta"


def test_shared_chapter_cache_context_excludes_changing_plan() -> None:
    context = {
        "contextPackVersion": 2,
        "chapterNumber": 13,
        "project": {"premise": "premise", "targetWords": 200000, "targetChapters": 41},
        "bible": {"theme": "theme"},
        "currentPlanRecord": {"chapterNumber": 13, "chapterGoal": "first plan"},
        "previousChapter": {"chapterNumber": 12, "summary": "summary", "endingHook": "hook"},
        "familyRelations": [{"fromMember": "A", "toMember": "B"}],
        "activeConflictLines": [{"conflictKey": "debt", "latestUpdate": "update"}],
        "latestCharacterStates": [{"memberName": "A", "sourceChapterNumber": 12}],
        "objectStates": [{"objectName": "ledger", "sourceChapterNumber": 12}],
        "canonFacts": [{"chapterNumber": 12, "fact": "fact"}],
        "mustContinueFrom": {"previousEndingHook": "hook", "strictRules": ["rule"]},
    }
    changed_plan_context = {
        **context,
        "currentPlanRecord": {"chapterNumber": 13, "chapterGoal": "updated plan"},
    }

    assert family_long_novel._shared_chapter_cache_context(context) == family_long_novel._shared_chapter_cache_context(changed_plan_context)
    assert family_long_novel._context_prompt_json(context, "chapter_write") != family_long_novel._context_prompt_json(changed_plan_context, "chapter_write")


def test_prompt_cache_options_enable_extended_retention(monkeypatch) -> None:
    monkeypatch.setattr(content_engine.settings, "OPENAI_PROMPT_CACHE_RETENTION", "24h")
    kwargs: dict[str, object] = {}

    content_engine._apply_prompt_cache_options(kwargs, "family-long-novel:project:v1")

    assert kwargs == {
        "prompt_cache_key": "family-long-novel:project:v1",
        "prompt_cache_retention": "24h",
    }


def test_openai_usage_merges_all_billable_responses() -> None:
    total = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
    }
    content_engine._merge_openai_usage(
        total,
        {
            "usage": {
                "input_tokens": 1200,
                "output_tokens": 300,
                "total_tokens": 1500,
                "input_tokens_details": {"cached_tokens": 1024},
                "output_tokens_details": {"reasoning_tokens": 80},
            }
        },
    )
    content_engine._merge_openai_usage(
        total,
        {
            "usage": {
                "input_tokens": 1200,
                "output_tokens": 500,
                "total_tokens": 1700,
                "input_tokens_details": {"cached_tokens": 1200},
                "output_tokens_details": {"reasoning_tokens": 120},
            }
        },
    )

    assert total == {
        "input_tokens": 2400,
        "output_tokens": 800,
        "total_tokens": 3200,
        "cached_input_tokens": 2224,
        "reasoning_tokens": 200,
    }


def test_long_novel_openai_calls_use_project_prompt_cache_key() -> None:
    source = "\n".join(
        inspect.getsource(func)
        for func in [
            family_long_novel.generate_family_novel_bible,
            family_long_novel.generate_family_novel_outline,
            family_long_novel._run_batched_continuation_replan,
            family_long_novel.generate_family_chapter_plan,
            family_long_novel.generate_family_chapter,
            family_long_novel.extract_family_chapter_state,
            family_long_novel.check_family_continuity,
            family_long_novel.quality_check_family_chapter,
            family_long_novel.repair_family_chapter,
        ]
    )

    assert source.count("prompt_cache_key=_project_prompt_cache_key(project.id)") >= 10
    assert source.count("_with_project_cache_prefix(context, prompt)") >= 6
    assert "prompt_cache_key" in inspect.signature(family_long_novel._run_writer).parameters
    assert "prompt_cache_key" in inspect.signature(family_long_novel._run_writer_with_usage).parameters
    assert "prompt_cache_key" in inspect.signature(family_long_novel._call_json).parameters
    assert "prompt_cache_key" in inspect.signature(family_long_novel._call_text).parameters
