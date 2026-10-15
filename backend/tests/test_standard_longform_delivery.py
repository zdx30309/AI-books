import asyncio
import json
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace

import pytest

from app.core.quality_metrics import detect_final_hard_errors
from app.services import ai_or_finalizer as finalizer
from app.services import content_engine as engine
from app.services import deepseek
from app.services import editor_v05 as editor
from app.services import standard_chapter_guard as chapter_guard
from app.services.general_short_length import LengthHold, issues, require_length, word_count
from app.services.generation_spend import active, generation_budget, reserve


CATEGORY = "social_science_core_conflict"
SOURCE = "甲银行2020年花费10万元采购设备，第二个论点是节省费用。"


def test_final_reference_article_prompt_cannot_reintroduce_unlimited_length(standard_settings):
    category = "emotion_relationship"
    prompt = engine.get_prompts(category)["full_book_writer"].format(raw_text="关系素材")
    final = engine._append_reference_article_prompt(prompt, category=category, input_source="reference_article")
    assert "11000至25000字" in final
    assert "正文字数不设人为上限" not in final


def manuscript(size=1400, count=5):
    headings = [f"@{number}. 主题{number}" for number in range(1, count + 1)]
    return ("@最终书名\n城市夜灯\n@最终简介\n不同街道的观察方法。\n@目录\n" + "\n".join(headings)
            + "\n@导言\n从日常观察中理解不同选择。\n@正文\n"
            + "\n\n".join(heading + "\n" + "文" * size for heading in headings))


def audit_payload(*, high=False):
    return {
        "units": [{"source_quote": SOURCE, "unit": "设备投入", "draft_quote": "文" if high else "",
                   "location": "正文" if high else "无", "level": "完整" if high else "无"}],
        "case_order_score": 100 if high else 10, "case_order_reason": "离线模拟顺序依据。",
        "narrative_score": 100 if high else 10, "narrative_reason": "离线模拟叙述依据。",
        "data_points": [], "cases": [], "entities": ["甲银行"],
        "differentiation_list": ["分析角度不同"], "fix_directive": "由编辑复核具体差异。",
        "risks": {name: {"level": "低", "reason": "仅用于离线流程测试，不是法律判断。"}
                  for name in ("著作权风险", "反不正当竞争风险", "平台原创审核风险")},
        "notes": "全部供应商均为离线模拟，不代表真实生成质量或AI率。",
    }


@pytest.fixture
def standard_settings(monkeypatch):
    monkeypatch.setattr(engine.settings, "LONGFORM_LENGTH_GUARD_ENABLED", True)
    monkeypatch.setattr(engine.settings, "LONGFORM_MIN_WORDS", 11000)
    monkeypatch.setattr(engine.settings, "LONGFORM_MAX_WORDS", 25000)
    monkeypatch.setattr(engine.settings, "LONGFORM_TARGET_WORDS", 14000)
    monkeypatch.setattr(engine.settings, "AIGC_GATE_ENABLED", False)
    monkeypatch.setattr(engine.settings, "EDITOR_V05_ENABLED", True)
    monkeypatch.setattr(engine.settings, "EDITOR_V05_START_AT", "2026-10-13T00:00:00Z")
    monkeypatch.setattr(engine.settings, "EDITOR_V05_SIMILARITY_GATE_ENABLED", True)
    monkeypatch.setattr(engine.settings, "AI_OR_FINALIZER_ENABLED", True)
    monkeypatch.setattr(engine.settings, "AI_OR_FINALIZER_START_AT", "")


def providers(monkeypatch, *, draft=None, high=False, budget=False):
    calls = []

    def record(stage):
        calls.append(stage)
        if budget:
            assert active(), f"{stage} lost generation_budget context"
            reserve("offline-" + stage, 0.01, "mock-provider")

    async def writer(prompt, **kwargs):
        record("writer")
        assert "11000至25000字" in prompt
        return draft or manuscript()

    def supplement(payload, **kwargs):
        data = json.loads(payload)
        record("length-" + str(data["chapter_number"]))
        return "文" * data["minimum_words"]

    def polish(text, **kwargs):
        record("polish")
        assert "标准长文篇幅保护" in kwargs["system_instruction"]
        return text

    def refine(text, **kwargs):
        record("refine")
        assert "标准长文篇幅保护" in kwargs["system_instruction"]
        return text

    def formatting(text, **kwargs):
        record("format")
        assert kwargs["max_output_tokens"] == 32768
        return text

    def audit(*args):
        record("similarity")
        return {"text": json.dumps(audit_payload(high=high), ensure_ascii=False),
                "finish_reason": "stop", "model": "deepseek-flash"}

    def rewrite(text):
        record("rewrite")
        return {"outputText": text}

    def correction(*args):
        record("correction")
        return {"text": '{"issues": []}'}

    monkeypatch.setattr(engine, "_route_writer_call", writer)
    monkeypatch.setattr(deepseek, "generate_text", supplement)
    monkeypatch.setattr(engine, "polish_book_text", polish)
    monkeypatch.setattr(engine, "refine_book_text", refine)
    monkeypatch.setattr(engine, "format_book_text", formatting)
    monkeypatch.setattr(editor, "call_audit", audit)
    monkeypatch.setattr(finalizer, "call_rewrite", rewrite)
    monkeypatch.setattr(finalizer, "call_correction", correction)
    return calls


def current_task(category=CATEGORY):
    return SimpleNamespace(created_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
                           source_url="https://example.org/offline-video",
                           config={"category": category, "raw_text": SOURCE, "generation_budget_cny": 20})


@pytest.mark.parametrize("category", [CATEGORY, "finance_technology_recommendation", "finance_technology", "finance_technology_ai_optimized"])
def test_standard_writer_to_similarity_correction_and_two_word_zip(tmp_path, monkeypatch, standard_settings, category):
    calls = providers(monkeypatch, budget=True)
    task = current_task(category)
    with generation_budget(task.config, tmp_path):
        reserve("earlier-paid-result", 0.4, "mock-provider")
        draft = asyncio.run(engine.generate_full_book(SOURCE, category=category, output_dir=tmp_path,
                                                      editor_v05_delivery=True, aigc_required=False))
        assert not issues(draft, category)
        assert not detect_final_hard_errors(draft, category=category)
        final = finalizer.finalize_task_manuscript(task, draft, tmp_path)
    assert not issues(final, category)
    assert calls[:6] == ["writer", "length-1", "length-2", "length-3", "length-4", "length-5"]
    assert calls.index("polish") < calls.index("refine") < calls.index("format") < calls.index("similarity")
    assert calls.index("similarity") < calls.index("rewrite") < calls.index("correction")
    assert task.config["editor_v05_similarity"] < 50
    assert task.config["editor_v05_status"] == "completed"
    assert (tmp_path / "01_writer.md").read_text() == manuscript()
    assert all((tmp_path / filename).exists() for filename in ("02_polish.md", "03_refine.md", "04_format.md"))
    directory = tmp_path / "editor_v05/ai_or_finalizer"
    with zipfile.ZipFile(directory / "delivery.zip") as archive:
        assert len(archive.namelist()) == 2
        assert all(name.endswith(".docx") for name in archive.namelist())
        assert archive.testzip() is None
        for name in archive.namelist():
            with zipfile.ZipFile(BytesIO(archive.read(name))) as document:
                assert "word/document.xml" in document.namelist()
    ledger = json.loads((tmp_path / "generation_spend.json").read_text())
    assert ledger["calls"][0]["action"] == "earlier-paid-result"
    expected_count = len(calls)
    with generation_budget(task.config, tmp_path):
        resumed = asyncio.run(engine.generate_full_book(SOURCE, category=category, output_dir=tmp_path,
                                                        editor_v05_delivery=True, aigc_required=False))
        assert finalizer.finalize_task_manuscript(task, resumed, tmp_path) == final
    assert len(calls) == expected_count
    assert json.loads((tmp_path / "generation_spend.json").read_text()) == ledger


def test_standard_50_percent_gate_stops_before_rewrite_or_correction(tmp_path, monkeypatch, standard_settings):
    calls = providers(monkeypatch, draft=manuscript(2800), high=True)
    task = current_task()
    draft = asyncio.run(engine.generate_full_book(SOURCE, category=CATEGORY, output_dir=tmp_path, editor_v05_delivery=True))
    for attempt in range(2):
        with pytest.raises(editor.SimilarityGateHold):
            finalizer.finalize_task_manuscript(task, draft, tmp_path)
    assert task.config["editor_v05_status"] == "similarity_blocked"
    assert calls.count("similarity") == 1
    assert "rewrite" not in calls and "correction" not in calls
    assert not list(tmp_path.rglob("delivery.zip"))


def test_standard_writer_structure_is_losslessly_repaired_before_length_calls(tmp_path, monkeypatch, standard_settings):
    original = manuscript(700, 12)
    calls = providers(monkeypatch, draft=original)
    final = asyncio.run(engine.generate_full_book(SOURCE, category=CATEGORY, output_dir=tmp_path))
    assert not issues(final, CATEGORY)
    assert not chapter_guard.structure_issues(final, CATEGORY)
    assert (tmp_path / "01_writer.md").read_text() == original
    report = json.loads(next(tmp_path.glob("chapter_structure/writer_before_length/*/report.json")).read_text())
    assert report["status"] == "repaired" and report["paid_calls"] == 0
    repaired_source = next(tmp_path.glob("length_repair/*/source.md")).read_text()
    assert len(engine._stage_formal_chapter_parts(repaired_source)) == 6
    assert sum(word_count(part[2]) for part in engine._stage_formal_chapter_parts(repaired_source)) == 8400
    assert calls[1:7] == [f"length-{number}" for number in range(1, 7)]


def test_standard_unrepairable_structure_never_starts_paid_supplement(tmp_path, monkeypatch, standard_settings):
    draft = manuscript(2200, 4).replace("文" * 2200, "文" * 700, 2)
    calls = providers(monkeypatch, draft=draft)
    with pytest.raises(chapter_guard.ChapterStructureHold):
        asyncio.run(engine.generate_full_book(SOURCE, category=CATEGORY, output_dir=tmp_path))
    assert calls == ["writer"]
    assert (tmp_path / "01_writer.md").read_text() == draft


@pytest.mark.parametrize("stage", ["02_polish.md", "03_refine.md", "04_format.md"])
def test_standard_saved_underlength_stage_holds_without_replaying_paid_work(tmp_path, monkeypatch, standard_settings, stage):
    saved = manuscript(2000)
    (tmp_path / stage).write_text(saved)
    (tmp_path / "01_writer.md").write_text(manuscript(2800))
    if stage == "03_refine.md":
        engine._dump_stage(tmp_path, "02_polish.md", manuscript(2800),
                           metadata=engine._refine_stage_identity(CATEGORY, stage="polish"))
    calls = providers(monkeypatch)
    with pytest.raises(LengthHold):
        asyncio.run(engine.generate_full_book(SOURCE, category=CATEGORY, output_dir=tmp_path))
    assert (tmp_path / stage).read_text() == saved
    assert not calls


def test_standard_valid_saved_final_with_unknown_paid_identity_holds(tmp_path, monkeypatch, standard_settings):
    saved = manuscript(2800)
    (tmp_path / "04_format.md").write_text(saved)
    calls = providers(monkeypatch)
    monkeypatch.setattr(engine, "polish_provider_name", lambda: "deepseek")
    with pytest.raises(engine.AigcManualReviewRequired, match="缺少模型断点信息"):
        asyncio.run(engine.generate_full_book(SOURCE, category=CATEGORY, output_dir=tmp_path))
    assert not calls
    assert (tmp_path / "04_format.md").read_text() == saved


def test_standard_polish_and_format_cannot_shrink_valid_body(tmp_path, monkeypatch, standard_settings):
    providers(monkeypatch, draft=manuscript(2300))

    def polish(text, **kwargs):
        return text.replace("文" * 2300, "文" * 2000)

    monkeypatch.setattr(engine, "polish_book_text", polish)
    monkeypatch.setattr(engine, "format_book_text", lambda text, **kwargs: manuscript(2000))
    final = asyncio.run(engine.generate_full_book(SOURCE, category=CATEGORY, output_dir=tmp_path))
    assert not issues(final, CATEGORY)
    assert not issues((tmp_path / "02_polish.md").read_text(), CATEGORY)
    assert (tmp_path / "02_polish_rejected_structure.md").exists()
    assert (tmp_path / "04_format_rejected_incomplete.md").exists()


def test_standard_refine_cannot_shrink_below_contract_or_auto_retry(tmp_path, monkeypatch, standard_settings):
    calls = providers(monkeypatch, draft=manuscript(2300))

    def refine(text, **kwargs):
        calls.append("shrunk-refine")
        return text.replace("文" * 2300, "文" * 2000)

    monkeypatch.setattr(engine, "refine_book_text", refine)
    with pytest.raises(LengthHold):
        asyncio.run(engine.generate_full_book(SOURCE, category=CATEGORY, output_dir=tmp_path))
    count = len(calls)
    with pytest.raises(LengthHold):
        asyncio.run(engine.generate_full_book(SOURCE, category=CATEGORY, output_dir=tmp_path))
    assert len(calls) == count
    assert "format" not in calls


def test_standard_final_optional_aigc_result_is_also_length_checked(tmp_path, monkeypatch, standard_settings):
    providers(monkeypatch, draft=manuscript(2300))

    async def shrink(markdown, **kwargs):
        return manuscript(2000)

    monkeypatch.setattr(engine, "_apply_final_aigc_gate", shrink)
    with pytest.raises(LengthHold):
        asyncio.run(engine.generate_full_book(SOURCE, category=CATEGORY, output_dir=tmp_path))
    assert not issues((tmp_path / "04_format.md").read_text(), CATEGORY)


def test_post_correction_length_contract_rejects_underlength_delivery(tmp_path, monkeypatch, standard_settings):
    providers(monkeypatch, draft=manuscript(2300))
    draft = asyncio.run(engine.generate_full_book(SOURCE, category=CATEGORY, output_dir=tmp_path))
    monkeypatch.setattr(finalizer, "call_rewrite", lambda text: {
        "outputText": text.replace("文" * 2300, "文" * 2000)})
    with pytest.raises(LengthHold):
        final = finalizer.finalize_task_manuscript(current_task(), draft, tmp_path)
        require_length(final, CATEGORY)
    assert (tmp_path / "04_format.md").read_text() == draft
    assert list(tmp_path.glob("editor_v05/ai_or_finalizer/*_rewrite.json"))
