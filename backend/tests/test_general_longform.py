from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import asyncio
import importlib.util
import json
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile
from io import BytesIO

import pytest

from app.prompts.general_longform_prompts import BUNDLE, get_bundle, get_manifest, render_stage, stage_source
from app.prompts.goal_driven_templates import get_prompts, list_categories, normalize_category
from app.services import general_longform as engine
from app.services import general_longform_documents as documents
from app.services import content_engine
from app.services.general_longform_runtime import GeneralLongformPaused, GeneralLongformReview, atomic_json, read_json
from general_longform_helpers import FakeGateway, SOURCE, add_material, chapter_text, fake_research, new_state, outline, ready, run, section_text

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("version,paragraphs,sections,digest", [
    ("2026-09-14-v16", 571, 19, "3dc1e57353c3ac57535ee1a818effb12501364eb1fe7f40b2f1de69437e96f07"),
    ("2026-09-14-v02", 840, 23, "1f70baec84e1cd1f47ddb269574c1e7daa0d6ac54e6d71b8f1c9bbe996f8cbef"),
])
def test_docx_source_is_lossless_and_every_paragraph_has_a_stage(version, paragraphs, sections, digest):
    spec = importlib.util.spec_from_file_location("prompt_import", ROOT / "scripts/import_general_longform_prompt.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bundle = get_bundle(version)
    result = module.verify(bundle)
    assert result == {"paragraphs": paragraphs, "sections": sections, "source_sha256": digest, "status": "pass"}
    manifest = get_manifest(version)
    for stage in manifest["stage_sections"]:
        assert (bundle / "stages" / f"{stage}.md").is_file()
        for section in manifest["sections"]:
            if stage in section["stages"]:
                assert (bundle / section["file"]).read_text(encoding="utf-8") in stage_source(stage, version)


def test_legacy_categories_and_all_132_prompts_are_byte_identical():
    baseline = read_json(Path(__file__).parent / "fixtures/general_longform_legacy_prompt_hashes.json")
    assert len(baseline) == 33
    original_order = read_json(Path(__file__).parent / "fixtures/general_longform_category_order.json")
    assert [item["id"] for item in list_categories()] == [*original_order, "general_longform"]
    for category, prompts in baseline.items():
        for key, expected in prompts.items():
            assert sha256(get_prompts(category)[key].encode()).hexdigest() == expected, (category, key)


@pytest.mark.parametrize("stage", ["outline", "draft", "continue", "polish", "refine", "acceptance", "repair", "final"])
@pytest.mark.parametrize("version", ["2026-09-14-v16", "2026-09-14-v02"])
def test_generation_never_receives_raw_historical_sample_or_report(stage, version):
    prompt = render_stage(stage, {"ReferencePatternCards": []}, version)
    for section in get_manifest(version)["sections"]:
        if section["role"] == "reference_only":
            raw = (get_bundle(version) / section["file"]).read_text(encoding="utf-8")
            assert raw in stage_source("reference_analysis", version)
            assert raw not in prompt
    assert "本次唯一工作节点" in prompt


def test_dedicated_category_cannot_enter_one_shot_short_workflow():
    assert normalize_category("通用长篇") == "general_longform"
    with pytest.raises(ValueError, match="大纲确认"):
        asyncio.run(content_engine.generate_full_book("素材", category="general_longform"))
    assert "@N. 章节名" not in get_prompts("general_longform")["full_book_writer"]


def test_word_counts_exclude_headings_but_count_english_and_numbers():
    text = "##第1章 标题\n#小节标题\n中国 hello-world 2026年 3.14 100,000。\n**自然**"
    assert documents.word_count(text) == 9  # 中国2 + hello-world1 + 2026/年2 + 3.14/100,0002 + 自然2
    assert documents.visible_count("书名 123，！\n") == 7


@pytest.mark.parametrize("mutate", [
    lambda p: p["packaging"].pop(), lambda p: p["packaging"][0].update(title="短"),
    lambda p: p["packaging"][0].update(description="不足"), lambda p: p["chapters"].pop(),
    lambda p: p["chapters"][0].update(target_words=1), lambda p: p["chapters"][0]["sections"][0].update(source_ids=[{}]),
    lambda p: p["chapters"][0].update(title={}), lambda p: p["chapters"][0]["sections"][0].update(title=[]),
    lambda p: p.update(target_words=15000), lambda p: p.update(domain="任意分类"),
])
def test_outline_validation_fails_closed(mutate):
    plan = outline()
    assert engine.validate_outline(plan, {SOURCE["id"]: SOURCE}) == []
    mutate(plan)
    assert engine.validate_outline(plan, {SOURCE["id"]: SOURCE})


def test_outline_patch_does_not_rewrite_unrelated_or_accepted_chapters():
    plan = outline()
    patched = engine.apply_outline_patch(plan, {"chapter_patches": [{"number": 2, "changes": {"question": "更具体的问题"}}]}, accepted_numbers={1})
    assert patched["chapters"][0] == plan["chapters"][0]
    assert patched["chapters"][2:] == plan["chapters"][2:]
    assert plan["chapters"][1]["question"] != patched["chapters"][1]["question"]
    for patch in ({"chapter_patches": [{"number": 1, "changes": {"title": "改名"}}]}, {"top_level_changes": {"packaging": []}}, {"chapter_patches": [None]}):
        with pytest.raises(GeneralLongformReview):
            engine.apply_outline_patch(plan, patch, accepted_numbers={1})


def test_outline_patch_drops_unknown_top_level_fields_instead_of_blocking():
    plan = outline()
    noisy = {"top_level_changes": {"packaging": [dict(item, title=f"新的判断方式第{i}讲读本") for i, item in enumerate(plan["packaging"], 1)],
                                   "chapters": plan["chapters"], "sections": []},
             "chapter_patches": []}
    patched = engine.apply_outline_patch(plan, noisy, accepted_numbers=set())
    assert patched["chapters"] == plan["chapters"]
    assert patched["packaging"][0]["title"] == "新的判断方式第1讲读本"
    assert engine.validate_outline(patched, {SOURCE["id"]: SOURCE}) == []
    with pytest.raises(GeneralLongformReview):
        engine.apply_outline_patch(plan, {"top_level_changes": ["不是对象"]}, accepted_numbers=set())


def test_revise_outline_retries_once_when_patch_shape_is_unusable(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "research", fake_research)
    monkeypatch.setenv("GENERAL_LONGFORM_OUTLINE_REPAIR_ATTEMPTS", "1")
    state, gateway = new_state(), FakeGateway(tmp_path)
    run(tmp_path, state, gateway, "plan")
    assert state["phase"] == "awaiting_outline_confirmation", state.get("last_error")
    seen: list[int] = []

    def revision(context, kwargs):
        seen.append(1)
        if len(seen) == 1:
            return {"top_level_changes": ["坏形状"], "chapter_patches": []}
        return {"top_level_changes": {}, "chapter_patches": [{"number": 2, "changes": {"question": "更具体的问题"}}], "reason": "只改第二章问题"}

    gateway.hooks["outline_revision"] = revision
    run(tmp_path, state, gateway, "revise_outline", feedback="把第二章的问题改得更具体")
    assert state["phase"] == "awaiting_outline_confirmation", state.get("last_error")
    assert len(seen) == 2, seen
    assert state["outline"]["chapters"][1]["question"] == "更具体的问题"
    retries = [call for call in gateway.calls if call["stage"] == "outline_revision" and str(call.get("slot", "")).startswith("outline-revision-retry")]
    assert retries and engine._REVISION_SCHEMA_REMINDER in retries[0]["context"]["feedback"]


def test_candidate_outline_repair_retries_after_invalid_patch_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("GENERAL_LONGFORM_OUTLINE_REPAIR_ATTEMPTS", "2")
    state, gateway = new_state(), FakeGateway(tmp_path)
    state["sources"] = {SOURCE["id"]: SOURCE}
    state["last_research"] = {"web_call_completed": True}
    candidate = outline()
    candidate["packaging"][0]["title"] = "短"
    state["outline_candidate"] = candidate
    runner = engine.GeneralLongformRunner(
        tmp_path,
        state,
        {"id": str(uuid4()), "action": "repair_outline_candidate"},
        gateway=gateway,
    )
    calls: list[int] = []

    def repair(context, kwargs):
        calls.append(1)
        if len(calls) == 1:
            return {"top_level_changes": ["坏形状"], "chapter_patches": []}
        return {
            "top_level_changes": {"packaging": outline()["packaging"]},
            "chapter_patches": [],
            "reason": "只修正包装书名",
        }

    gateway.hooks["outline_revision"] = repair
    repaired, errors = runner.repair_outline(
        candidate,
        ["方案1书名需要10—18个可见字符"],
        {"sources": state["sources"]},
    )
    assert errors == [], errors
    assert repaired["packaging"][0]["title"] == outline()["packaging"][0]["title"]
    assert len(calls) == 2


def test_format_invalid_first_outline_is_repaired_with_the_existing_stage(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "research", fake_research)
    state, gateway = new_state(), FakeGateway(tmp_path)
    broken = outline()
    broken["packaging"][0]["title"] = "短"
    broken["packaging"][2]["title"] = "短"
    gateway.plan = broken
    gateway.hooks["outline_revision"] = lambda context, kwargs: {"top_level_changes": {"packaging": outline()["packaging"]}}
    run(tmp_path, state, gateway, "plan")
    assert state["phase"] == "awaiting_outline_confirmation", state.get("last_error")
    assert state["outline"] and not state.get("outline_candidate")
    repair = next(call for call in gateway.calls if call["stage"] == "outline_revision")
    assert "方案1书名需要10—18个可见字符" in repair["context"]["feedback"]
    assert repair["context"]["outline"]["packaging"][0]["title"] == "短"  # patch the candidate, not a rewrite
    assert read_json(tmp_path / "planning" / "outline_repair_1.json")["adopted"] is True
    assert len([call for call in gateway.calls if call["stage"] == "research"]) == 1  # no re-search


def test_failed_local_repair_keeps_a_revisable_candidate_and_blocks_normally(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "research", fake_research)
    state, gateway = new_state(), FakeGateway(tmp_path)
    broken = outline()
    broken["packaging"][0]["title"] = "短"
    gateway.plan = broken
    gateway.hooks["outline_revision"] = lambda context, kwargs: {"top_level_changes": {"recommendation": "仍未补齐书名"}}
    run(tmp_path, state, gateway, "plan")
    assert state["phase"] == "needs_review" and not state["confirmed"]
    assert state["outline_candidate"] and "方案1书名需要10—18个可见字符" in state["last_error"]
    calls = len(gateway.calls)
    run(tmp_path, state, gateway, "plan")
    assert len(gateway.calls) == calls  # a kept candidate never triggers a second paid plan


def test_blocked_or_unpatchable_outline_errors_do_not_spend_a_repair_call(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "research", fake_research)
    state, gateway = new_state(), FakeGateway(tmp_path)
    short = outline()
    short["chapters"] = short["chapters"][:11]
    gateway.plan = short
    run(tmp_path, state, gateway, "plan")
    assert state["phase"] == "needs_review" and state["outline_candidate"]
    assert not [call for call in gateway.calls if call["stage"] == "outline_revision"]

    monkeypatch.setattr(engine, "research", fake_research)
    monkeypatch.setenv("GENERAL_LONGFORM_OUTLINE_REPAIR_ATTEMPTS", "0")
    state, gateway = new_state(), FakeGateway(tmp_path)
    gateway.plan = broken_titles()
    run(tmp_path, state, gateway, "plan")
    assert state["phase"] == "needs_review" and state["outline_candidate"]
    assert not [call for call in gateway.calls if call["stage"] == "outline_revision"]


def broken_titles():
    plan = outline()
    plan["packaging"][0]["title"] = "短"
    return plan


def blocked_candidate(tmp_path, monkeypatch):
    """Reproduce a first plan that failed validation and kept its candidate."""
    monkeypatch.setattr(engine, "research", fake_research)
    state, gateway = new_state(), FakeGateway(tmp_path)
    gateway.plan = broken_titles()
    gateway.hooks["outline_revision"] = lambda context, kwargs: {"top_level_changes": {"recommendation": "仍未补齐书名"}}
    run(tmp_path, state, gateway, "plan")
    assert state["phase"] == "needs_review" and state["outline_candidate"]
    calls = len(gateway.calls)
    gateway.hooks["outline_revision"] = lambda context, kwargs: {"top_level_changes": {"packaging": outline()["packaging"]}}
    return state, gateway, calls


def test_blocked_candidate_is_repaired_from_stored_evidence_without_a_new_research_round(tmp_path, monkeypatch):
    state, gateway, calls = blocked_candidate(tmp_path, monkeypatch)
    run(tmp_path, state, gateway, "repair_outline_candidate")
    assert state["phase"] == "awaiting_outline_confirmation", state.get("last_error")
    assert state["outline"] and not state["outline_candidate"] and not state["confirmed"]
    added = gateway.calls[calls:]
    assert [call["stage"] for call in added] == ["outline_revision"]          # one generation call, no new search
    assert len([call for call in gateway.calls if call["stage"] == "research"]) == 1
    assert added[0]["context"]["research"]["web_call_completed"] is True
    assert added[0]["context"]["research"]["sources"] == state["sources"]     # reuses this book's verified evidence
    exported = read_json(tmp_path / "planning" / "outline_repair_1.json")
    assert exported["adopted"] is True and exported["errors_after"] == []


def test_blocked_candidate_repair_is_idempotent_and_never_double_charges(tmp_path, monkeypatch):
    state, gateway, calls = blocked_candidate(tmp_path, monkeypatch)
    request = {"id": str(uuid4()), "action": "repair_outline_candidate", "created_at": "2026-09-20T00:00:00+00:00"}
    run(tmp_path, state, gateway, "repair_outline_candidate", request=request)
    after = len(gateway.calls)
    state["completed_requests"].remove(request["id"])  # a worker restart replays the same request id
    run(tmp_path, state, gateway, "repair_outline_candidate", request=request)
    assert len(gateway.calls) == after
    assert state["phase"] == "awaiting_outline_confirmation"


def test_candidate_without_stored_evidence_points_to_the_documented_revision_path(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "research", fake_research)
    state, gateway = new_state(), FakeGateway(tmp_path)
    state["outline_candidate"] = broken_titles()
    run(tmp_path, state, gateway, "repair_outline_candidate")
    assert state["phase"] == "needs_review" and "大纲局部修订" in state["last_error"]
    assert not gateway.calls


def test_plan_confirm_three_serial_chapters_and_idempotent_replay(tmp_path, monkeypatch):
    state, gateway = ready(tmp_path, monkeypatch)
    assert not state["chapters"] and len(state["artifacts"]) == 2
    for number in range(1, 4):
        run(tmp_path, state, gateway, "next_chapter")
        assert state["phase"] == "ready_for_chapter", state["last_error"]
        assert sorted(state["chapters"]) == [str(i) for i in range(1, number + 1)]
        record = state["chapters"][str(number)]["accepted"]
        assert record["state_persisted"] and record["audit"]["planned_vs_actual"]
        assert state["accepted_state"][str(number)]["source"] == "acceptedContent"
        before = deepcopy(state["chapters"])
        calls = len(gateway.calls)
        run(tmp_path, state, gateway, "next_chapter", request=state["last_request"])
        assert before == state["chapters"] and len(gateway.calls) == calls
    drafts = [item for item in gateway.calls if item["stage"] == "draft"]
    assert [item["context"]["chapter"]["number"] for item in drafts] == [1, 1, 2, 2, 3, 3]
    assert [p["number"] for p in drafts[-1]["context"]["previous_chapters"]] == [1, 2]
    assert state["total_words"] == 26250
    assert len(state["artifacts"]) == 8


def test_full_105000_word_simulated_book_preserves_chapters_and_deduplicates_sources(tmp_path, monkeypatch):
    state, gateway = ready(tmp_path, monkeypatch)
    for _ in range(12):
        run(tmp_path, state, gateway, "next_chapter")
        assert not state["last_error"], state["last_error"]
    assert state["phase"] == "ready_for_assembly"
    run(tmp_path, state, gateway, "assemble")
    assert state["phase"] == "completed", state["last_error"]
    assert state["total_words"] == 105000
    artifact = state["artifacts"][-1]
    paragraphs, metadata = documents.inspect_docx((tmp_path / "exports" / artifact["filename"]).read_bytes())
    assert metadata["word_count"] == 105000
    assert len([p for p in paragraphs if p[1] == "Heading1"]) == 13
    assert len([p for p in paragraphs if p[1] == "Heading2"]) == 24
    assert paragraphs[-2] == ("文章参考来源", "Heading1")
    assert len([p for p in paragraphs if SOURCE["url"] in p[0]]) == 1
    assert "文章参考来源" in (tmp_path / "manuscript.md").read_text(encoding="utf-8")
    # A new assembly operation creates another version, never overwriting v1.
    first_hash = artifact["sha256"]
    run(tmp_path, state, gateway, "assemble")
    assert state["book_version"] == 2
    assert sha256((tmp_path / "exports" / artifact["filename"]).read_bytes()).hexdigest() == first_hash


@pytest.mark.parametrize("problem", ["missing_summary", "missing_state", "hash_mismatch"])
def test_next_chapter_requires_valid_accepted_predecessor(tmp_path, monkeypatch, problem):
    state, gateway = ready(tmp_path, monkeypatch)
    run(tmp_path, state, gateway, "next_chapter")
    record = state["chapters"]["1"]["accepted"]
    if problem == "missing_summary": record["audit"]["summary"] = ""
    elif problem == "missing_state": state["accepted_state"].pop("1")
    else: (tmp_path / record["acceptedContent"]).write_text("changed", encoding="utf-8")
    calls = len(gateway.calls)
    run(tmp_path, state, gateway, "next_chapter")
    assert state["phase"] == "needs_review" and "2" not in state["chapters"]
    assert len(gateway.calls) == calls


def test_high_severe_block_acceptance_and_repairs_are_reextracted(tmp_path, monkeypatch):
    state, gateway = ready(tmp_path, monkeypatch)
    gateway.hooks["acceptance"] = lambda context, _: {"summary": "虚构", "endingHook": "待核对", "issues": [{"severity": "severe", "description": "没有依据"}]}
    run(tmp_path, state, gateway, "next_chapter")
    assert state["phase"] == "needs_review"
    assert not state["accepted_state"] and state["total_words"] == 0
    assert not state["chapters"]["1"].get("accepted")
    assert len([c for c in gateway.calls if c["stage"] == "acceptance"]) == 2
    assert len([c for c in gateway.calls if c["stage"] == "repair"]) == 1
    partials = state["artifacts"][-2:]
    assert all("未完成" in item["filename"] for item in partials)
    assert {item["kind"] for item in partials} == {"chapter", "editorial"}
    del gateway.hooks["acceptance"]
    run(tmp_path, state, gateway, "revise_chapter", chapter_number=1, feedback="核对事实边界")
    assert state["chapters"]["1"]["accepted"]["version"] == 2
    assert state["total_words"] == 8750


def test_partial_continuation_runs_once_per_request_and_resumes_without_duplication(tmp_path, monkeypatch):
    state, gateway = ready(tmp_path, monkeypatch)
    gateway.hooks["draft"] = lambda ctx, _: section_text(ctx["chapter"]["number"], 0, 100)
    gateway.hooks["continue"] = lambda ctx, _: section_text(ctx["chapter"]["number"], 0, 100)
    run(tmp_path, state, gateway, "next_chapter")
    assert state["phase"] == "needs_review"
    request = deepcopy(state["last_request"])
    candidate = state["chapters"]["1"]["candidate"]
    first = (tmp_path / candidate["draftContent"]).read_text(encoding="utf-8")
    assert documents.word_count(first) == 200
    run(tmp_path, state, gateway, "next_chapter", request=request)
    assert (tmp_path / candidate["draftContent"]).read_text(encoding="utf-8") == first
    assert len([c for c in gateway.calls if c["stage"] == "continue"]) == 1
    assert len(state["artifacts"]) == 4


def test_revising_earlier_chapter_preserves_later_versions_and_single_count(tmp_path, monkeypatch):
    state, gateway = ready(tmp_path, monkeypatch)
    for _ in range(3): run(tmp_path, state, gateway, "next_chapter")
    later = deepcopy({k: state["chapters"][k] for k in ("2", "3")})
    run(tmp_path, state, gateway, "revise_chapter", chapter_number=1, feedback="只核对第一段条件")
    assert state["chapters"]["1"]["accepted"]["version"] == 2
    assert len(state["chapters"]["1"]["history"]) == 1
    assert state["total_words"] == 26250
    assert later == {k: state["chapters"][k] for k in ("2", "3")}


def test_word_pair_protocol_timestamps_and_export_replay(tmp_path, monkeypatch):
    state, gateway = ready(tmp_path, monkeypatch)
    run(tmp_path, state, gateway, "next_chapter")
    record = state["chapters"]["1"]["accepted"]
    body, companion = state["artifacts"][-2:]
    assert body["filename"].split("_v01_", 1)[1] == companion["filename"].split("_v01_", 1)[1]
    bp, bm = documents.inspect_docx((tmp_path / "exports" / body["filename"]).read_bytes())
    cp, cm = documents.inspect_docx((tmp_path / "exports" / companion["filename"]).read_bytes())
    assert bp[0] == ("##第1章 观察与选择1", "Heading1")
    assert [p for p in bp if p[1] == "Heading2"] == [("#问题1甲", "Heading2"), ("#问题1乙", "Heading2")]
    assert not any("文章参考来源" in p[0] or "编辑记录" in p[0] for p in bp)
    assert cp[0] == ("文章参考来源", "Heading1")
    with ZipFile(BytesIO((tmp_path / "exports" / companion["filename"]).read_bytes())) as archive:
        assert b'w:type="page"' in archive.read("word/document.xml")
        assert b'TargetMode="External"' in archive.read("word/_rels/document.xml.rels")
        assert b'outlineLvl w:val="2"' not in archive.read("word/styles.xml")
    # Simulate a crash after just the body was written and no state marker.
    (tmp_path / "exports" / companion["filename"]).unlink()
    records = documents.export_chapter(tmp_path / "exports", state, record, chapter_text(1), accepted=True)
    assert records[0]["sha256"] == body["sha256"]
    assert (tmp_path / "exports" / records[1]["filename"]).is_file()
    assert documents.timestamp(datetime(2026, 9, 14, 8, 3, 4, tzinfo=timezone.utc)) == "2026年9月14日_16时03分04秒"


def test_pause_after_first_section_delivers_pair_and_resumes_serially(tmp_path, monkeypatch):
    state, gateway = ready(tmp_path, monkeypatch)
    request = {"id": str(uuid4()), "action": "next_chapter"}
    gateway.request_id = request["id"]
    def checkpoint():
        if state["chapters"].get("1", {}).get("candidate", {}).get("sections"):
            raise GeneralLongformPaused("用户请求暂停")
    result = engine.GeneralLongformRunner(tmp_path, state, request, gateway=gateway, checkpoint=checkpoint).run()
    assert result["phase"] == "paused" and len(state["artifacts"]) == 4
    assert not state["accepted_state"]
    run(tmp_path, state, gateway, "next_chapter", request=request)
    assert state["phase"] == "ready_for_chapter" and set(state["chapters"]) == {"1"}
    assert state["total_words"] == 8750
    assert len([c for c in gateway.calls if c["stage"] == "draft"]) == 2


def test_provider_interruption_preserves_partial_files_and_resumes_same_chapter(tmp_path, monkeypatch):
    state, gateway = ready(tmp_path, monkeypatch)
    def fail(context, kwargs): raise RuntimeError("provider interrupted")
    gateway.hooks["polish"] = fail
    with pytest.raises(RuntimeError): run(tmp_path, state, gateway, "next_chapter")
    assert state["phase"] == "needs_review" and not state["accepted_state"]
    assert len(state["artifacts"]) == 4
    old_partial_hashes = [item["sha256"] for item in state["artifacts"][-2:]]
    del gateway.hooks["polish"]
    run(tmp_path, state, gateway, "next_chapter", request=state["last_request"])
    assert state["chapters"]["1"]["accepted"] and "2" not in state["chapters"]
    assert [item["sha256"] for item in state["artifacts"][2:4]] == old_partial_hashes


def test_native_confirmed_outline_import_automatically_writes_first_chapter(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "research", fake_research)
    state, gateway = new_state(), FakeGateway(tmp_path)
    add_material(tmp_path, state, "已确认大纲", metadata={"kind": "outline", "confirmed": True, "outline": outline(), "sources": {"Sverified": SOURCE}})
    run(tmp_path, state, gateway, "import_materials")
    assert state["confirmed"] and state["chapters"]["1"]["accepted"], state["last_error"]
    assert "2" not in state["chapters"]
    assert state["materials"][0]["imported"]


def test_outline_revision_researches_feedback_and_abstracts_new_source(tmp_path, monkeypatch):
    state, gateway = ready(tmp_path, monkeypatch)
    original = deepcopy(state["outline"])
    raw = "新补充的原始素材只进入抽象" * 60
    add_material(tmp_path, state, raw, role="source")
    run(tmp_path, state, gateway, "import_materials")
    assert not state["chapters"]
    gateway.hooks["outline_revision"] = lambda context, kwargs: {"chapter_patches": [{"number": 2, "changes": {"question": "加入新材料的边界"}}]}
    run(tmp_path, state, gateway, "revise_outline", feedback="只补充第二章材料边界")
    assert state["phase"] == "awaiting_outline_confirmation", state["last_error"]
    assert not state["confirmed"] and state["outline_version"] == 2
    assert state["outline"]["chapters"][0] == original["chapters"][0]
    assert state["outline"]["chapters"][2:] == original["chapters"][2:]
    research_call = next(c for c in reversed(gateway.calls) if c["stage"] == "research")
    revision_call = next(c for c in reversed(gateway.calls) if c["stage"] == "outline_revision")
    assert research_call["context"]["feedback"] == "只补充第二章材料边界"
    assert revision_call["context"]["research"]["web_call_completed"]
    assert revision_call["context"]["source_summary"]
    assert raw not in json.dumps([research_call, revision_call], ensure_ascii=False)


def test_import_candidate_local_repair_consumes_material_and_keeps_final_packaging(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "research", fake_research)
    state, gateway = new_state(), FakeGateway(tmp_path)
    plan = outline()
    plan["packaging"] = plan["packaging"][:1]
    plan["chapters"][1]["question"] = ""
    material = add_material(tmp_path, state, "已确认但缺少局部字段的大纲", metadata={"kind": "outline", "confirmed": True, "outline": plan, "sources": {"Sverified": SOURCE}})
    run(tmp_path, state, gateway, "import_materials")
    assert state["phase"] == "needs_review" and not material["imported"]
    assert state["outline_candidate_material_id"] == material["id"]
    assert material["status"] == "needs_review" and material["error"]
    calls = len(gateway.calls)
    run(tmp_path, state, gateway, "plan")
    assert len(gateway.calls) == calls and state["outline_candidate"]
    gateway.hooks["outline_revision"] = lambda context, kwargs: {"chapter_patches": [{"number": 2, "changes": {"question": "恢复具体问题"}}]}
    run(tmp_path, state, gateway, "revise_outline", feedback="补齐第二章问题，不改其他章节")
    assert state["phase"] == "awaiting_outline_confirmation", state["last_error"]
    assert material["imported"] and material["status"] == "imported" and not material["error"]
    assert state["imported_outline_id"] == material["id"] and not state.get("outline_candidate_material_id")
    assert not state["confirmed"] and not state["chapters"]
    assert len(state["outline"]["packaging"]) == 1
    gateway.hooks["outline_revision"] = lambda context, kwargs: {"chapter_patches": [{"number": 2, "changes": {"question": "再次核对后的具体问题"}}]}
    run(tmp_path, state, gateway, "revise_outline", feedback="再次核对同一处边界")
    assert state["outline_version"] == 2 and not state["last_error"]
    add_material(tmp_path, state, "新增来源", role="source", filename="来源.txt")
    run(tmp_path, state, gateway, "import_materials")
    assert all(item["imported"] for item in state["materials"]) and not state["last_error"]
    run(tmp_path, state, gateway, "confirm_outline")
    assert state["confirmed"] and not state["chapters"]


def test_identification_error_is_attached_to_its_material(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "research", fake_research)
    state, gateway = new_state(), FakeGateway(tmp_path)
    material = add_material(tmp_path, state, "未识别材料")
    def fail(context, kwargs):
        raise GeneralLongformReview("字段识别需要检查")
    gateway.hooks["materials"] = fail
    run(tmp_path, state, gateway, "import_materials")
    assert material["status"] == "needs_review" and "字段识别" in material["error"]
    assert not material["imported"] and not state["accepted_state"]


def test_native_chapter_import_rechecks_and_then_writes_only_next_chapter(tmp_path, monkeypatch):
    state, gateway = ready(tmp_path, monkeypatch)
    add_material(tmp_path, state, chapter_text(1), metadata={"kind": "chapter", "number": 1, "title": "观察与选择1"})
    run(tmp_path, state, gateway, "import_materials")
    assert set(state["chapters"]) == {"1", "2"}, state["last_error"]
    drafts = [call for call in gateway.calls if call["stage"] == "draft"]
    assert [call["context"]["chapter"]["number"] for call in drafts] == [2, 2]
    before = deepcopy(state["chapters"])
    # Request replay after both export and state acceptance never imports twice.
    state["completed_requests"].remove(state["last_request"]["id"])
    run(tmp_path, state, gateway, "import_materials", request=state["last_request"])
    assert state["chapters"] == before


def test_source_and_editorial_materials_are_abstracted_not_generation_raw(tmp_path, monkeypatch):
    state, gateway = ready(tmp_path, monkeypatch)
    state["source_text"] = "源文参考长段落" * 100
    add_material(tmp_path, state, "旧报告原文不作为新章" * 100, role="report")
    run(tmp_path, state, gateway, "import_materials")
    assert not state["chapters"]  # A report alone is not a next-chapter instruction.
    run(tmp_path, state, gateway, "next_chapter")
    assert state["chapters"]["1"]["accepted"], state["last_error"]
    calls = [c for c in gateway.calls if c["stage"] == "draft"]
    assert calls and calls[0]["context"]["editorial_notes"] == ["第三段条件需核对"]
    assert state["source_text"] not in json.dumps(calls, ensure_ascii=False)
    assert "旧报告原文不作为新章" not in json.dumps(calls, ensure_ascii=False)


def test_confirmed_packaging_cannot_change_without_new_outline_version(tmp_path, monkeypatch):
    state, gateway = ready(tmp_path, monkeypatch)
    old_artifacts = deepcopy(state["artifacts"])
    run(tmp_path, state, gateway, "confirm_outline", packaging_index=1)
    assert state["phase"] == "needs_review"
    assert state["outline"]["recommended_index"] == 0
    assert state["artifacts"] == old_artifacts


@pytest.mark.parametrize("suffix", ["[1]", "（Smith, 2024）", "https://example.org", "\u200b", "文章参考来源"])
def test_local_acceptance_catches_format_pollution(suffix):
    plan = outline()
    issues = engine.chapter_issues(chapter_text(1) + suffix, plan["chapters"][0], plan, [])
    assert any(i["type"] == "format" for i in issues)


def test_bibliography_strips_work_notes_and_numbering_not_formal_volume():
    rows = documents.bibliography([{**SOURCE, "bibliography": "一、Author. Title. Journal. 2026;11(6):591-592.（网页主要内容已读）"}, SOURCE])
    assert len(rows) == 2
    assert rows[1][0].startswith("Author") and "11(6)" in rows[1][0] and "已读" not in rows[1][0]


def test_old_heading_import_changes_format_not_body():
    plan = outline()
    plan["chapters"][0]["sections"][0]["title"] = "1.1 问题1甲：详细解释"
    updated = engine.normalize_imported_headings(plan)
    assert updated["chapters"][0]["sections"][0]["title"] == "问题1甲"
    text = "#第1章 观察与选择1\n##1.1 问题1甲：详细解释\n正文保持不动。\n###旧三级内容\n更多正文。"
    normalized = engine.normalize_imported_chapter(text, updated["chapters"][0], updated["handoff"]["heading_aliases"])
    assert normalized.startswith("##第1章 观察与选择1\n#问题1甲")
    assert "正文保持不动。\n旧三级内容\n更多正文。" in normalized

def test_outline_revision_reports_a_noop_patch_instead_of_silently_accepting_it(tmp_path, monkeypatch):
    state, gateway = ready(tmp_path, monkeypatch)
    monkeypatch.setenv("GENERAL_LONGFORM_OUTLINE_REPAIR_ATTEMPTS", "0")
    version = state["outline_version"]
    # Applies cleanly (topic is an allowed field) but changes nothing.
    gateway.hooks["outline_revision"] = lambda context, kwargs: {"top_level_changes": {"topic": outline()["topic"]}, "chapter_patches": []}
    run(tmp_path, state, gateway, "revise_outline", feedback="把第二章的问题写清楚")
    assert state["phase"] == "needs_review"
    assert state["outline_version"] == version  # no silent version bump
    assert "没有产生任何有效修改" in (state["last_error"] or "")
    assert state["outline_revision_context"]["feedback"] == "把第二章的问题写清楚"
    assert state["outline_revision_context"]["evidence"]["web_call_completed"]


def test_malformed_top_level_changes_keeps_valid_chapter_patches(tmp_path, monkeypatch):
    state, gateway = ready(tmp_path, monkeypatch)
    gateway.hooks["outline_revision"] = lambda context, kwargs: {"top_level_changes": ["整本书结构"], "chapter_patches": [{"number": 2, "changes": {"question": "有效章节补丁"}}]}
    run(tmp_path, state, gateway, "revise_outline", feedback="只改第二章问题")
    assert state["phase"] == "awaiting_outline_confirmation", state["last_error"]
    assert state["outline"]["chapters"][1]["question"] == "有效章节补丁"
    assert state["outline_version"] == 2
    assert not state.get("outline_revision_context")


def test_repair_outline_revision_reuses_the_paid_evidence_without_a_new_search(tmp_path, monkeypatch):
    state, gateway = ready(tmp_path, monkeypatch)
    monkeypatch.setenv("GENERAL_LONGFORM_OUTLINE_REPAIR_ATTEMPTS", "0")
    gateway.hooks["outline_revision"] = lambda context, kwargs: {"top_level_changes": {"topic": outline()["topic"]}, "chapter_patches": []}
    run(tmp_path, state, gateway, "revise_outline", feedback="只补充第二章材料边界")
    assert state["phase"] == "needs_review"
    version = state["outline_version"]
    searches = len([call for call in gateway.calls if call["stage"] == "research"])
    gateway.hooks["outline_revision"] = lambda context, kwargs: {"chapter_patches": [{"number": 2, "changes": {"question": "加入新材料的边界"}}]}
    run(tmp_path, state, gateway, "repair_outline_revision")
    assert state["phase"] == "awaiting_outline_confirmation", state["last_error"]
    assert state["outline_version"] == version + 1
    assert state["outline"]["chapters"][1]["question"] == "加入新材料的边界"
    assert len([call for call in gateway.calls if call["stage"] == "research"]) == searches
    assert not state.get("outline_revision_context")
    assert state["outline_revision_repair_request_id"]


def test_repair_outline_revision_is_refused_without_a_saved_context(tmp_path, monkeypatch):
    state, gateway = ready(tmp_path, monkeypatch)
    calls = len(gateway.calls)
    run(tmp_path, state, gateway, "repair_outline_revision")
    assert state["phase"] == "needs_review"
    assert "可复用的上次修订检索" in (state["last_error"] or "")
    assert len(gateway.calls) == calls


def _blocked_by_gaps(state):
    """Turn a confirmed book back into the real blocker: the outline still rests
    on material no verified source can support, so confirm_outline is refused."""
    state["confirmed"] = False
    state["phase"] = "needs_review"
    state["outline"]["research_gaps"] = ["缺少可读全文的睡眠规律性共识", "缺少2015年以后的昼夜节律指南"]
    state["last_research"] = {"topic": "判断与选择", "domain": "认知成长与效能提升", "gaps": [], "errors": [],
                              "rawTextIncluded": False, "web_call_completed": True,
                              "retrieved_at": "2026-09-14T08:00:00+00:00"}


def test_revise_outline_local_clears_gaps_from_already_paid_evidence(tmp_path, monkeypatch):
    state, gateway = ready(tmp_path, monkeypatch)
    monkeypatch.setenv("GENERAL_LONGFORM_OUTLINE_REPAIR_ATTEMPTS", "0")
    _blocked_by_gaps(state)
    version = state["outline_version"]
    searches = len([call for call in gateway.calls if call["stage"] == "research"])
    gateway.hooks["outline_revision"] = lambda context, kwargs: {"top_level_changes": {"research_gaps": []}}
    run(tmp_path, state, gateway, "revise_outline_local")
    assert state["phase"] == "awaiting_outline_confirmation", state["last_error"]
    assert state["confirmed"] is False
    assert state["outline_version"] == version + 1
    assert state["outline"]["research_gaps"] == []
    assert len([call for call in gateway.calls if call["stage"] == "research"]) == searches
    run(tmp_path, state, gateway, "confirm_outline")
    assert state["confirmed"] and state["phase"] == "ready_for_chapter", state["last_error"]


def test_revise_outline_local_that_leaves_gaps_cannot_silently_return(tmp_path, monkeypatch):
    state, gateway = ready(tmp_path, monkeypatch)
    monkeypatch.setenv("GENERAL_LONGFORM_OUTLINE_REPAIR_ATTEMPTS", "0")
    _blocked_by_gaps(state)
    version = state["outline_version"]
    gateway.hooks["outline_revision"] = lambda context, kwargs: {"top_level_changes": {"research_gaps": ["仍然没有来源"]}}
    run(tmp_path, state, gateway, "revise_outline_local")
    assert state["phase"] == "needs_review"
    assert "待核实材料仍未清空" in (state["last_error"] or "")
    assert state["outline_version"] == version
    assert state["outline"]["research_gaps"]
    assert state["confirmed"] is False
    run(tmp_path, state, gateway, "confirm_outline")
    assert not state["confirmed"] and "待核实材料" in (state["last_error"] or "")


def test_revise_outline_local_is_refused_without_stored_evidence(tmp_path, monkeypatch):
    state, gateway = ready(tmp_path, monkeypatch)
    state["last_research"] = None
    calls = len(gateway.calls)
    run(tmp_path, state, gateway, "revise_outline_local")
    assert state["phase"] == "needs_review"
    assert "没有可直接复用的检索证据" in (state["last_error"] or "")
    assert len(gateway.calls) == calls


def test_set_auto_run_never_spends_a_model_call(tmp_path):
    state, gateway = new_state(), FakeGateway(tmp_path)
    calls = len(gateway.calls)
    run(tmp_path, state, gateway, "set_auto_run", enabled=True)
    assert state["auto_run"] is True
    assert len(gateway.calls) == calls
    run(tmp_path, state, gateway, "set_auto_run", enabled=True)
    assert state["auto_run"] is True and len(gateway.calls) == calls
    run(tmp_path, state, gateway, "set_auto_run", enabled=False)
    assert state["auto_run"] is False and len(gateway.calls) == calls


def test_search_trusted_policy_confirms_the_outline_and_starts_chapter_one(tmp_path, monkeypatch):
    state, gateway = ready(tmp_path, monkeypatch)
    _blocked_by_gaps(state)
    calls = len(gateway.calls)
    run(tmp_path, state, gateway, "set_source_policy", policy="search_trusted")
    assert state["source_policy"] == "search_trusted"
    assert len(gateway.calls) == calls
    run(tmp_path, state, gateway, "confirm_outline")
    assert state["confirmed"] and state["phase"] == "ready_for_chapter", state["last_error"]
    assert state["gap_waiver"]["count"] == 2
    assert state["outline"]["research_gaps"]
    run(tmp_path, state, gateway, "next_chapter")
    assert state["chapters"]["1"]["accepted"], state["last_error"]


def test_default_policy_still_blocks_an_unverified_outline(tmp_path, monkeypatch):
    state, gateway = ready(tmp_path, monkeypatch)
    _blocked_by_gaps(state)
    assert state.get("source_policy", "verified") == "verified"
    run(tmp_path, state, gateway, "confirm_outline")
    assert not state["confirmed"]
    assert "待核实材料" in (state["last_error"] or "")
    run(tmp_path, state, gateway, "set_source_policy", policy="not_a_policy")
    assert "来源策略" in (state["last_error"] or "")
    assert state.get("source_policy") == "verified"