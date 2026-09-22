"""Next-step guidance: every stopped 通用长篇 project must name a real next action."""
from copy import deepcopy
import json

import pytest

from app.services import general_longform_guidance as guidance


def chapter(number, *, target=8750, words=None, status="accepted", issues=None, exported=True):
    record = {"number": number, "title": "观察与选择" + str(number), "version": 1, "status": status,
              "word_count": words if words is not None else target, "exported": exported,
              "issues": issues or [], "audit": {"summary": "第" + str(number) + "章摘要", "endingHook": "新的问题"}}
    return record


def outline_state(**overrides):
    plan = {"topic": "判断与选择", "domain": "认知成长与效能提升", "audience": "普通读者", "central_question": "如何判断",
            "voice": "清楚自然", "target_words": 105000, "recommended_index": 0, "research_gaps": [],
            "packaging": [{"direction": "方向", "title": "普通人的判断与选择新方式", "description": "简介"}],
            "chapters": [{"number": number, "title": "观察与选择" + str(number), "target_words": 8750, "sections": []}
                         for number in range(1, 13)]}
    state = {"phase": "ready_for_chapter", "last_error": None, "sources": {"S1": {"id": "S1"}},
             "outline": plan, "confirmed": True, "chapters": {}, "total_words": 0, "can_resume": False,
             "outline_version": 1}
    state.update(overrides)
    return state


def accepted_state(numbers, *, words_per_chapter=8750, total_words=None, **overrides):
    records = {str(number): {"accepted": chapter(number, words=words_per_chapter)} for number in numbers}
    state = outline_state(chapters=records,
                          total_words=total_words if total_words is not None else words_per_chapter * len(numbers))
    state.update(overrides)
    return state


def test_fresh_project_points_at_the_first_plan_run():
    result = guidance.build_guidance({"phase": "needs_outline"}, task_status="paused")
    assert result["stall_kind"] == "awaiting_user"
    assert result["primary"]["action"] == "plan"
    assert result["primary"]["enabled"] is True


def test_stopped_research_prefers_the_cheap_resume_and_keeps_plan_as_alternative():
    state = {"phase": "needs_review", "can_resume": True, "last_error": "实际读取材料未形成可靠的来源摘要，未开始正文",
             "sources": {}, "chapters": {}}
    result = guidance.build_guidance(state, task_status="manual_review")
    assert result["stall_kind"] == "needs_review"
    assert result["primary"]["action"] == "resume"
    assert result["primary"]["params"] == {"retry_uncertain": False}
    assert [item["action"] for item in result["alternatives"]] == ["plan", "manual"]
    assert "新的联网检索" in result["alternatives"][0]["cost"]


def test_outline_candidate_repair_is_offered_when_evidence_is_reusable():
    reusable = {"phase": "needs_review", "outline_candidate": {"topic": "x"}, "sources": {"S1": {}},
                "last_research": {"web_call_completed": True}, "can_resume": True, "chapters": {}, "last_error": "方案1书名需要10—18个可见字符"}
    result = guidance.build_guidance(reusable, task_status="manual_review")
    assert result["primary"]["action"] == "repair_outline_candidate"
    assert result["primary"]["enabled"] is True
    assert result["alternatives"][0]["action"] == "revise_outline"
    assert result["alternatives"][0]["requires_feedback"] is True

    cold = deepcopy(reusable)
    cold["last_research"] = {"web_call_completed": False}
    cold["sources"] = {}
    blocked = guidance.build_guidance(cold, task_status="manual_review")
    assert blocked["primary"]["action"] == "repair_outline_candidate"
    assert blocked["primary"]["enabled"] is False
    assert "没有可复用的检索证据" in blocked["primary"]["disabled_reason"]


def test_unconfirmed_outline_asks_for_confirmation_or_a_local_patch():
    state = outline_state(phase="awaiting_outline_confirmation", confirmed=False)
    state["chapters"] = {}
    result = guidance.build_guidance(state, task_status="paused")
    assert result["primary"]["action"] == "confirm_outline"

    gapped = deepcopy(state)
    gapped["outline"]["research_gaps"] = ["缺少 2026 年公开数据"]
    result = guidance.build_guidance(gapped, task_status="paused")
    assert result["primary"]["action"] == "revise_outline"
    assert "缺少 2026 年公开数据" in result["primary"]["params"]["feedback"]


def test_pending_chapter_continues_from_the_saved_breakpoint():
    state = accepted_state([1])
    result = guidance.build_guidance(state, task_status="paused")
    assert result["primary"]["action"] == "next_chapter"
    assert "第2章" in result["primary"]["label"]

    partial = accepted_state([1])
    partial["chapters"]["2"] = {"candidate": {"number": 2, "status": "draft", "issues": []}}
    result = guidance.build_guidance(partial, task_status="manual_review")
    assert result["primary"]["action"] == "next_chapter"
    assert result["primary"]["label"] == "继续完成第2章"


def test_blocking_chapter_issues_offer_a_prefilled_local_revision():
    state = accepted_state([1])
    state["phase"] = "needs_review"
    state["chapters"]["2"] = {"candidate": {"number": 2, "status": "needs_review", "issues": [
        {"severity": "high", "type": "length", "description": "本章实测 8000 字，至少需要 8500 字，未达成计划"},
        {"severity": "low", "type": "style", "description": "轻微问题不阻断"},
    ]}}
    result = guidance.build_guidance(state, task_status="manual_review")
    assert result["primary"]["action"] == "next_chapter"
    repair = next(item for item in result["alternatives"] if item["action"] == "revise_chapter")
    assert repair["params"]["chapter_number"] == 2
    assert "未达成计划" in repair["params"]["feedback"]
    assert "轻微问题不阻断" not in repair["params"]["feedback"]


def test_missing_previous_export_blocks_advancing_and_points_at_resume():
    state = accepted_state([1])
    state["chapters"]["1"]["accepted"]["exported"] = False
    result = guidance.build_guidance(state, task_status="manual_review")
    assert result["primary"]["action"] == "resume"
    assert "Word" in result["headline"]


def test_short_book_is_sent_back_for_a_concrete_chapter_extension():
    state = accepted_state(list(range(1, 13)), words_per_chapter=8000, total_words=96000, phase="ready_for_assembly")
    result = guidance.build_guidance(state, task_status="paused")
    assert result["primary"]["action"] == "revise_chapter"
    assert result["primary"]["enabled"] is True
    feedback = result["primary"]["params"]["feedback"]
    assert "96000 字" in feedback and "100000-110000" in feedback
    assert result["primary"]["params"]["chapter_number"] in range(1, 13)
    assert any(item["action"] == "assemble" for item in result["avoid"])
    assert all(item["action"] != "assemble" for item in [result["primary"], *result["alternatives"]])


def test_overlong_book_is_sent_back_for_a_concrete_chapter_trim():
    state = accepted_state(list(range(1, 13)), words_per_chapter=9500, total_words=114000, phase="ready_for_assembly")
    result = guidance.build_guidance(state, task_status="manual_review")
    assert result["primary"]["action"] == "revise_chapter"
    assert "压缩" in result["primary"]["label"]
    assert "114000 字" in result["primary"]["params"]["feedback"]


def test_final_audit_issues_become_numbered_chapter_revisions():
    state = accepted_state(list(range(1, 13)), phase="ready_for_assembly")
    state["final_audit"] = {"issues": [{"severity": "high", "chapter_number": 9, "description": "第9章结论与前文冲突"}],
                            "actual_words": 105000}
    result = guidance.build_guidance(state, task_status="manual_review")
    assert result["primary"]["action"] == "revise_chapter"
    assert result["primary"]["params"]["chapter_number"] == 9
    assert "第9章结论与前文冲突" in result["primary"]["params"]["feedback"]
    assert any(item["action"] == "assemble" for item in result["avoid"])


def test_all_chapters_accepted_and_in_range_asks_for_assembly():
    state = accepted_state(list(range(1, 13)), total_words=105000, phase="ready_for_assembly")
    result = guidance.build_guidance(state, task_status="paused")
    assert result["primary"]["action"] == "assemble"
    assert result["avoid"] == []


def test_delivery_hold_requires_an_explicit_confirmation_only_without_a_response_id():
    state = accepted_state([1], can_resume=True, phase="needs_review")
    state["delivery_holds"] = [{"request_id": "r1", "action": "outline", "status": "started", "response_id": "resp_1"}]
    result = guidance.build_guidance(state, task_status="manual_review")
    assert result["stall_kind"] == "hold"
    assert result["primary"]["action"] == "resume"
    assert result["primary"]["params"]["retry_uncertain"] is False

    state["delivery_holds"] = [{"request_id": "r1", "action": "outline", "status": "uncertain", "response_id": None}]
    result = guidance.build_guidance(state, task_status="manual_review")
    assert result["primary"]["params"]["retry_uncertain"] is True


def test_task_config_manual_gate_is_surfaced_even_without_holds():
    result = guidance.build_guidance({"phase": "needs_review", "chapters": {}}, task_status="manual_review",
                                     config={"generation_hold": True, "paid_call_manual_resume_required": True})
    assert result["stall_kind"] == "hold"
    assert "generation_hold" in result["notes"][0]


def test_running_state_has_no_button_but_explains_the_watchdog():
    fresh = guidance.build_guidance({"phase": "writing_chapter", "chapters": {}}, task_status="processing",
                                    config={"active_run_heartbeat_at": "2999-01-01T00:00:00+00:00"})
    assert fresh["stall_kind"] == "running"
    assert fresh["primary"] is None
    assert "等它跑完" in fresh["headline"]

    stale = guidance.build_guidance({"phase": "writing_chapter", "chapters": {}}, task_status="processing",
                                    config={"active_run_heartbeat_at": "2000-01-01T00:00:00+00:00"})
    assert "自动重排" in stale["headline"]


@pytest.mark.parametrize("status,phase,expected", [
    ("completed", "completed", "done"),
    ("failed", "writing_chapter", "failed"),
    ("paused", "paused", "paused"),
])
def test_terminal_states_still_explain_themselves(status, phase, expected):
    state = accepted_state([1], phase=phase, can_resume=True)
    result = guidance.build_guidance(state, task_status=status)
    assert result["stall_kind"] == expected
    assert result["headline"]


def test_awaiting_materials_needs_an_uploaded_file_before_the_import_button_works():
    empty = guidance.build_guidance({"phase": "awaiting_materials", "materials": [], "chapters": {}}, task_status="paused")
    assert empty["primary"]["action"] == "import_materials"
    assert empty["primary"]["enabled"] is False
    assert empty["primary"]["disabled_reason"]

    loaded = guidance.build_guidance({"phase": "awaiting_materials", "chapters": {},
                                      "materials": [{"id": "m1", "imported": False}]}, task_status="paused")
    assert loaded["primary"]["enabled"] is True


def test_prompt_hash_failure_is_reported_as_an_environment_problem():
    state = {"phase": "needs_review", "can_resume": True, "chapters": {},
             "last_error": "GeneralLongformReview: 项目 prompt 源文件哈希与其固定版本不一致，已保留断点"}
    result = guidance.build_guidance(state, task_status="manual_review")
    assert any("import_general_longform_prompt.py" in note for note in result["notes"])


@pytest.mark.parametrize("state,task_status,config", [
    (None, "", None),
    ({}, "", {}),
    ({"chapters": "坏数据", "outline": [], "sources": 3}, "processing", None),
    ({"chapters": {"a": [], "3": {}}, "total_words": "x"}, "manual_review", {"active_run_heartbeat_at": "bad"}),
])
def test_malformed_state_never_raises_and_still_names_a_step(state, task_status, config):
    result = guidance.build_guidance(state, task_status=task_status, config=config)
    assert result["headline"]
    # Only an actively running stage may legitimately expose no callable step.
    assert result["primary"] or result["alternatives"] or result["stall_kind"] == "running"


@pytest.mark.parametrize("state,task_status,config", [
    ({"phase": "needs_outline"}, "paused", None),
    ({"phase": "needs_review", "can_resume": True, "chapters": {}}, "manual_review", None),
    ({"phase": "needs_review", "outline_candidate": {"topic": "x"}, "chapters": {}, "sources": {"S": {}},
      "last_research": {"web_call_completed": True}}, "manual_review", None),
    (accepted_state([1]), "paused", None),
    (accepted_state(list(range(1, 13)), total_words=96000, phase="ready_for_assembly"), "paused", None),
    (accepted_state(list(range(1, 13)), total_words=105000, phase="ready_for_assembly"), "paused", None),
    ({"phase": "completed", "chapters": {}}, "completed", None),
    (accepted_state([1], phase="paused", can_resume=True), "paused", None),
])
def test_guidance_is_json_serialisable_shape_stable_and_never_mutates_state(state, task_status, config):
    snapshot = deepcopy(state)
    result = guidance.build_guidance(state, task_status=task_status, config=config)
    assert state == snapshot
    json.dumps(result, ensure_ascii=False)
    assert set(result) == {"stall_kind", "headline", "detail", "primary", "alternatives", "avoid", "notes", "auto"}
    for item in [result["primary"], *result["alternatives"]]:
        if item is None:
            continue
        assert set(item) == {"action", "label", "reason", "cost", "params", "enabled",
                             "disabled_reason", "requires_feedback", "tab"}
        assert item["label"]


def test_guidance_never_offers_an_action_the_api_would_reject():
    cases = [
        ({"phase": "needs_outline"}, "paused", None),
        ({"phase": "needs_review", "outline_candidate": {"topic": "x"}, "chapters": {},
          "sources": {"S": {}}, "last_research": {"web_call_completed": True}}, "manual_review", None),
        (outline_state(phase="awaiting_outline_confirmation", confirmed=False), "paused", None),
        (accepted_state([1]), "paused", None),
        (accepted_state(list(range(1, 13)), total_words=96000, phase="ready_for_assembly"), "paused", None),
    ]
    for state, status, config in cases:
        result = guidance.build_guidance(state, task_status=status, config=config)
        has_outline = isinstance(state.get("outline"), dict) or isinstance(state.get("outline_candidate"), dict)
        for item in [result["primary"], *result["alternatives"]]:
            if not item or item["action"] == "manual":
                continue
            if item["action"] == "plan":
                assert not has_outline, "plan is rejected by the API once an outline or candidate exists"
            if item["action"] in {"revise_outline", "revise_chapter"}:
                assert item["requires_feedback"] or item["params"].get("feedback")
            if item["action"] == "next_chapter":
                assert state.get("confirmed")


def test_pending_number_skips_accepted_chapters_and_stops_when_complete():
    state = accepted_state([1, 2, 4])
    assert guidance.pending_number(state) == 3
    assert guidance.pending_number(accepted_state(list(range(1, 13)))) is None


def test_length_advice_never_asks_for_more_words_than_the_gap():
    state = accepted_state(list(range(1, 13)), words_per_chapter=8000, total_words=96000, phase="ready_for_assembly")
    actions = guidance.length_revision_actions(state)
    assert actions
    total_requested = 0
    for item in actions:
        feedback = item["params"]["feedback"]
        assert "扩充约" in feedback
        total_requested += int(feedback.split("扩充约 ")[1].split(" 字")[0])
    assert total_requested <= 100000 - 96000


def test_length_advice_is_silent_inside_the_accepted_range():
    assert guidance.length_revision_actions(accepted_state(list(range(1, 13)), total_words=105000)) == []

def _revision_context(version, *, evidence=None):
    return {"feedback": "只改第二章的问题", "evidence": evidence if evidence is not None else {"web_call_completed": True},
            "outline_version": version}


def test_failed_outline_revision_offers_the_cheap_retry_first():
    state = outline_state(phase="needs_review", confirmed=False)
    state["chapters"] = {}
    state["outline_revision_context"] = _revision_context(state["outline_version"])
    state["last_error"] = "GeneralLongformReview: 大纲补丁没有产生任何有效修改：顶层字段与章节补丁为空或都不可用；请把修改要求写得更具体"
    result = guidance.build_guidance(state, task_status="manual_review")
    assert result["primary"]["action"] == "repair_outline_revision"
    assert "复用" in result["primary"]["label"]
    assert any(item["action"] == "revise_outline" for item in result["alternatives"])


def test_failed_outline_revision_retry_also_applies_to_an_imported_candidate():
    state = {"phase": "needs_review", "outline_candidate": {"topic": "x"}, "outline": None, "confirmed": False,
             "outline_version": 0, "chapters": {}, "sources": {}, "outline_revision_context": _revision_context(0),
             "last_error": "GeneralLongformReview: 大纲补丁没有产生任何有效修改"}
    result = guidance.build_guidance(state, task_status="manual_review")
    assert result["primary"]["action"] == "repair_outline_revision"
    assert any(item["action"] == "repair_outline_candidate" for item in result["alternatives"])


@pytest.mark.parametrize("context", [
    None,
    {"feedback": "x", "evidence": {}, "outline_version": 1},
    {"feedback": "", "evidence": {"web_call_completed": True}, "outline_version": 1},
    {"feedback": "x", "evidence": {"web_call_completed": True}, "outline_version": 0},
])
def test_outline_revision_retry_is_silent_without_reusable_evidence(context):
    state = outline_state(phase="needs_review", confirmed=False)
    state["outline_revision_context"] = context
    assert guidance.outline_revision_retry(state) is None

def test_legacy_outline_patch_failure_explains_the_automatic_retry():
    """A project stranded by the old patch-shape bug gets the reason, not just a button."""
    state = {"phase": "needs_review", "outline": None, "outline_candidate": {"topic": "x"}, "sources": {"S1": {}},
             "last_research": {"web_call_completed": True},
             "last_error": "GeneralLongformReview: 大纲补丁包含未允许的全局结构改写"}
    result = guidance.build_guidance(state, task_status="manual_review")
    assert result["primary"]["action"] == "repair_outline_candidate"
    assert any("大纲补丁" in note for note in result["notes"])


def test_trusted_source_policy_turns_the_gap_blocker_into_a_confirmation():
    state = outline_state(phase="awaiting_outline_confirmation", confirmed=False)
    state["chapters"] = {}
    state["outline"]["research_gaps"] = ["缺少 2026 年公开数据"]
    state["last_research"] = {"web_call_completed": True}
    strict = guidance.build_guidance(deepcopy(state), task_status="manual_review")
    switch = next(item for item in strict["alternatives"] if item["action"] == "set_source_policy")
    assert switch["params"] == {"policy": "search_trusted"}
    assert switch["cost"] == "不产生生成调用"
    assert strict["auto"]["action"] == "revise_outline_local"
    state["source_policy"] = "search_trusted"
    trusted = guidance.build_guidance(state, task_status="manual_review")
    assert trusted["primary"]["action"] == "confirm_outline"
    assert trusted["primary"]["enabled"] is True
    assert trusted["auto"]["action"] == "confirm_outline"
    assert any(item["action"] == "set_source_policy" and item["params"] == {"policy": "verified"}
               for item in trusted["alternatives"])