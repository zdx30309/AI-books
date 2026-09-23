"""The generation context carries the book, never the bookkeeping."""
from copy import deepcopy
from uuid import uuid4

from app.prompts.general_longform_prompts import render_stage
from app.services import general_longform as engine
from general_longform_helpers import outline


def source(index, *, bookkeeping=True):
    entry = {"id": f"S{index}", "url": f"https://example.org/{index}", "title": "公开研究",
             "authors": "Institute", "year": "2026", "publication": "Journal",
             "doi": f"10.0000/{index}", "bibliography": f"Institute. Study {index}. 2026.",
             "approved_summary": "经核验的抽象材料" * 20, "supported_claims": ["受限定的测试事实"],
             "limitations": ["测试数据"]}
    if bookkeeping:
        entry.update({"file_sha256": "a" * 64, "text_sha256": "b" * 64, "read_scope": "测试页全文",
                      "retrieved_at": "2026-09-14T08:00:00+00:00", "verification": "page_read_and_abstracted",
                      "provider": "openai_web_search+public_page_reader", "query": "测试检索" * 20,
                      "local_path": f"research/request-one/deadbeef/S{index}.json", "status": "imported",
                      "error": None, "generated_card_count": 0})
    return entry


def accepted_record(number, *, words=8750):
    return {"number": number, "title": f"观察与选择{number}", "version": 1, "status": "accepted",
            "word_count": words, "exported": True, "content_sha256": "c" * 64,
            "state_source_sha256": "d" * 64, "draftContent": f"chapters/{number:02d}/v01/draft.txt",
            "issues": [{"severity": "minor", "description": "验收备注" * 600}],
            "policy_downgrades": [{"reason": "记录" * 400}],
            "audit": {"summary": f"第{number}章摘要", "endingHook": "新的问题",
                      "sections": [{"note": "逐段记录" * 1700}]},
            "research": {"S1": {"approved_summary": "旧章来源" * 2200}},
            "content": "正文段落。" * 2100}


def runner_for(tmp_path, state):
    return engine.GeneralLongformRunner(tmp_path, state, {"id": str(uuid4()), "action": "next_chapter",
                                                          "created_at": "2026-09-14T08:00:00+00:00"})


def book_state():
    plan = outline()
    state = engine.initial_state(str(uuid4()))
    state.update(outline=plan, confirmed=True, outline_version=1, source_policy="search_trusted")
    state["sources"] = {f"S{index}": source(index) for index in range(1, 31)}
    state["accepted_state"] = {str(number): {"source": "acceptedContent", "summary": f"第{number}章摘要",
                                             "endingHook": "新的问题"} for number in range(1, 13)}
    return state, plan


def test_previous_chapters_keep_the_prose_and_drop_the_qa_records(tmp_path):
    state, plan = book_state()
    record = accepted_record(1)
    before = deepcopy(record)
    context = runner_for(tmp_path, state).context(previous_chapters=[record], chapter=plan["chapters"][0])
    chapter = context["previous_chapters"][0]
    assert chapter["content"] == record["content"] and chapter["word_count"] == record["word_count"]
    assert chapter["summary"] == "第1章摘要" and chapter["endingHook"] == "新的问题"
    assert set(chapter) == {"number", "title", "word_count", "content", "summary", "endingHook"}
    assert record == before                       # the state itself is never rewritten


def test_sources_keep_their_citations_and_drop_the_tool_records(tmp_path):
    state, plan = book_state()
    entry = source(1)
    before = deepcopy(entry)
    context = runner_for(tmp_path, state).context(chapter=plan["chapters"][0])
    projected = context["sources"]["S1"]
    assert projected["approved_summary"] == entry["approved_summary"]
    assert projected["bibliography"] == entry["bibliography"] and projected["url"] == entry["url"]
    assert "file_sha256" not in projected and "local_path" not in projected and "read_scope" not in projected
    assert state["sources"]["S1"] == before      # the book's own source ledger keeps every field


def test_a_late_chapter_stays_inside_the_context_guard(tmp_path):
    state, plan = book_state()
    previous = [accepted_record(number) for number in range(1, 12)]
    runner = runner_for(tmp_path, state)
    context = runner.context(previous_chapters=previous, chapter=plan["chapters"][11],
                             total_accepted_words=108000, editorial_notes=[])
    projected = len(render_stage("polish", {**context, "candidate": "正文段落。" * 2200}))
    unprojected = len(render_stage("polish", {**context, "previous_chapters": previous,
                                              "candidate": "正文段落。" * 2200}))
    assert projected < 350_000, projected
    assert unprojected > 350_000, (projected, unprojected)
