"""自动连跑：下一步由系统自己提交，且只在必须问人时停下。"""
from copy import deepcopy
import json

from app.services import general_longform_autopilot as autopilot
from app.services import general_longform_guidance as guidance


def chapter(number, *, words=8750, exported=True, issues=None):
    return {"number": number, "title": "观察与选择" + str(number), "version": 1, "status": "accepted",
            "word_count": words, "exported": exported, "issues": issues or [],
            "audit": {"summary": "第" + str(number) + "章摘要", "endingHook": "新的问题"}}


def plan_state(**overrides):
    plan = {"topic": "判断与选择", "domain": "认知成长与效能提升", "audience": "普通读者", "central_question": "如何判断",
            "voice": "清楚自然", "target_words": 105000, "recommended_index": 2, "research_gaps": [],
            "packaging": [{"direction": "方向" + str(i), "title": "普通人的判断与选择新方式" + str(i), "description": "简介"}
                          for i in range(5)],
            "chapters": [{"number": n, "title": "观察与选择" + str(n), "target_words": 8750, "sections": []}
                         for n in range(1, 13)]}
    state = {"phase": "awaiting_outline_confirmation", "confirmed": False, "outline": plan, "sources": {"S1": {"id": "S1"}},
             "chapters": {}, "outline_version": 1, "last_research": {"web_call_completed": True}}
    state.update(overrides)
    return state


def confirmed_state(**overrides):
    state = plan_state(confirmed=True, phase="ready_for_chapter")
    state.update(overrides)
    return state


def test_fresh_project_stops_before_an_unpaid_search():
    step = autopilot.auto_next({"phase": "needs_outline", "sources": {}, "chapters": {}})
    assert step["action"] is None
    assert "联网检索" in step["stop"]


def test_fresh_project_plans_when_search_is_allowed():
    step = autopilot.auto_next({"phase": "needs_outline", "sources": {}, "chapters": {}}, allow_search=True)
    assert step["action"] == "plan"


def test_research_gaps_are_closed_locally_without_a_new_search():
    state = plan_state()
    state["outline"]["research_gaps"] = ["需要一份读不到的统计"]
    step = autopilot.auto_next(state)
    assert step["action"] == "revise_outline_local"
    assert "research_gaps" in step["params"]["feedback"]
    assert step["requires_feedback"] is True


def test_research_gaps_without_stored_evidence_ask_before_searching():
    state = plan_state(sources={}, last_research=None)
    state["outline"]["research_gaps"] = ["需要一份读不到的统计"]
    assert autopilot.auto_next(state)["action"] is None
    assert autopilot.auto_next(state, allow_search=True)["action"] == "revise_outline"


def state_title(step):
    return step["label"].split("《")[1].split("》")[0]


def test_confirmation_uses_the_recommended_packaging():
    step = autopilot.auto_next(plan_state())
    assert step["action"] == "confirm_outline"
    assert step["params"] == {"packaging_index": 2}
    assert state_title(step) in step["label"]


def test_candidate_outline_is_repaired_locally():
    state = plan_state(outline=None, outline_candidate={"topic": "x"})
    assert autopilot.auto_next(state)["action"] == "repair_outline_candidate"


def test_confirmed_outline_writes_chapters_one_at_a_time():
    step = autopilot.auto_next(confirmed_state())
    assert step["action"] == "next_chapter"
    assert "1" in step["label"]
    chapters = {"1": {"accepted": chapter(1)}}
    step = autopilot.auto_next(confirmed_state(chapters=chapters))
    assert step["action"] == "next_chapter"
    assert "第2章" in step["label"]


def test_missing_word_pair_is_completed_before_the_next_chapter():
    chapters = {"1": {"accepted": chapter(1, exported=False)}}
    step = autopilot.auto_next(confirmed_state(chapters=chapters))
    assert step["action"] == "resume"


def test_blocking_chapter_issues_are_repaired_with_the_report():
    chapters = {"1": {"accepted": chapter(1)}, "2": {"candidate": {"number": 2, "issues": [
        {"severity": "high", "description": "第二小节的问题没有被回答"}]}}}
    step = autopilot.auto_next(confirmed_state(chapters=chapters))
    assert step["action"] == "revise_chapter"
    assert step["params"]["chapter_number"] == 2
    assert "第二小节的问题没有被回答" in step["params"]["feedback"]


def test_finished_book_assembles_and_then_stops():
    chapters = {str(n): {"accepted": chapter(n)} for n in range(1, 13)}
    step = autopilot.auto_next(confirmed_state(chapters=chapters))
    assert step["action"] == "assemble"
    done = autopilot.auto_next(confirmed_state(chapters=chapters, phase="completed"))
    assert done["action"] is None and "完成" in done["stop"]


def test_short_book_revises_instead_of_assembling():
    chapters = {str(n): {"accepted": chapter(n, words=5000)} for n in range(1, 13)}
    step = autopilot.auto_next(confirmed_state(chapters=chapters))
    assert step["action"] in autopilot.AUTO_ACTIONS
    assert step["action"] != "assemble"


def test_holds_and_running_states_pause_the_auto_run():
    held = autopilot.auto_next(confirmed_state(config={}) | {"delivery_holds": [{"request_id": "r"}]})
    assert held["action"] is None and "核对" in held["stop"]
    running = autopilot.auto_next(confirmed_state(), task_status="writing_chapters")
    assert running["action"] is None and "正在跑" in running["stop"]


def test_auto_next_is_read_only_and_never_invents_an_action():
    state = confirmed_state()
    before = deepcopy(state)
    step = autopilot.auto_next(state, task_status="paused", config={"general_longform_auto_run": True})
    assert state == before
    assert step["action"] in autopilot.AUTO_ACTIONS
    assert json.loads(json.dumps(step, ensure_ascii=False))


def test_guidance_payload_carries_the_auto_step():
    state = plan_state(auto_run=True)
    payload = guidance.build_guidance(state, task_status="paused")
    assert payload["auto"]["enabled"] is True
    assert payload["auto"]["action"] == "confirm_outline"
    assert guidance.build_guidance(plan_state(), task_status="paused")["auto"]["enabled"] is False


def test_trusted_source_policy_confirms_the_outline_instead_of_revising():
    state = plan_state(source_policy="search_trusted")
    state["outline"]["research_gaps"] = ["需要一份读不到的统计"]
    step = autopilot.auto_next(state)
    assert step["action"] == "confirm_outline"
    assert step["params"] == {"packaging_index": 2}
    assert "留痕" in step["reason"]
    strict = plan_state()
    strict["outline"]["research_gaps"] = ["需要一份读不到的统计"]
    assert autopilot.auto_next(strict)["action"] == "revise_outline_local"


def test_auto_run_never_switches_the_source_policy_by_itself():
    state = plan_state()
    state["outline"]["research_gaps"] = ["需要一份读不到的统计"]
    step = autopilot.auto_next(state)
    assert step["action"] == "revise_outline_local"
    assert "set_source_policy" not in autopilot.AUTO_ACTIONS
    assert state.get("source_policy") != "search_trusted"