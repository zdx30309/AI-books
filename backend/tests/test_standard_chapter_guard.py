import asyncio
import json
import re

import pytest

from app.services import content_engine as engine
from app.services import standard_chapter_guard as guard


CATEGORY = "social_science_core_conflict"


def manuscript(sizes):
    headings = [f"@{index}. 主题{index}" for index in range(1, len(sizes) + 1)]
    return ("@最终书名\n测试书\n@最终简介\n简介\n@目录\n" + "\n".join(headings)
            + "\n@导言\n导言原文。\n@正文\n" + "\n\n".join(
                heading + "\n" + "文" * size + f"。证据{index}。"
                for index, (heading, size) in enumerate(zip(headings, sizes), 1))
            + "\n\n@参考资料\n原参考资料。\n")


def prose(markdown):
    _, groups, suffix = guard._split(markdown)
    return re.sub(r"\s+", "", "".join(body for group in groups for body in group["bodies"])), suffix


def test_latest_failure_is_fixed_without_rewriting_or_payment(tmp_path):
    original = manuscript([882, 976, 940, 806, 914, 967, 837, 772, 692, 694, 716, 809])
    assert len(guard.structure_issues(original, CATEGORY)["常规长文相邻短章疑似拆章"]) == 5
    result = guard.ensure_chapter_structure(original, CATEGORY, tmp_path)
    assert not guard.structure_issues(result, CATEGORY)
    assert prose(result) == prose(original)
    assert "\n\n".join(body for group in guard._split(original)[1] for body in group["bodies"]) == "\n\n".join(
        body for group in guard._split(result)[1] for body in group["bodies"])
    assert len(guard._split(result)[1]) == 9
    prefix, groups, _ = guard._split(result)
    for number, group in enumerate(groups, 1):
        assert f"@{number}. {'、'.join(group['titles'])}" in prefix
    assert guard.ensure_chapter_structure(original, CATEGORY, tmp_path) == result
    assert guard.ensure_chapter_structure(result, CATEGORY, tmp_path) == result
    record = json.loads(next(tmp_path.rglob("report.json")).read_text())
    assert record["paid_calls"] == 0 and record["status"] == "repaired"
    assert next(tmp_path.rglob("source.md")).read_text() == original


@pytest.mark.parametrize("sizes", [[1200, 300, 1500, 1400, 1500, 1300],
                                 [2200, 650, 1000, 1000, 1000, 1000]])
def test_short_and_imbalanced_chapters_preserve_prose(sizes):
    original = manuscript(sizes)
    assert guard.structure_issues(original, CATEGORY)
    result = guard.ensure_chapter_structure(original, CATEGORY)
    assert not guard.structure_issues(result, CATEGORY)
    assert prose(result) == prose(original)


@pytest.mark.parametrize("category", ["general_short", "classical_chinese", "healing_comic", "epic_novel", "web_short_story"])
def test_other_workflows_and_prompts_are_untouched(category):
    source = manuscript([900] * 6 + [700] * 6)
    assert guard.ensure_chapter_structure(source, category) == source
    assert guard.writer_contract("外置提示词", category) == "外置提示词"


def test_valid_manuscript_is_byte_identical(tmp_path):
    source = manuscript([1000] * 8)
    assert guard.ensure_chapter_structure(source, CATEGORY, tmp_path) == source
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("sizes", [[2200, 700, 700, 2200], [3500, 200, 3500, 3500, 3500]])
def test_unrepairable_structure_holds_instead_of_dropping_below_gate_or_spending(tmp_path, sizes):
    source = manuscript(sizes)
    with pytest.raises(guard.ChapterStructureHold, match="不自动付费"):
        guard.ensure_chapter_structure(source, CATEGORY, tmp_path)
    record = json.loads(next(tmp_path.rglob("report.json")).read_text())
    assert record["status"] == "manual_review" and record["paid_calls"] == 0
    assert next(tmp_path.rglob("source.md")).read_text() == source
    assert not list(tmp_path.rglob("result.md"))


def test_missing_toc_holds_without_losing_original(tmp_path):
    source = manuscript([900] * 6 + [700] * 6).replace("@目录", "目录缺失")
    with pytest.raises(guard.ChapterStructureHold, match="目录"):
        guard.ensure_chapter_structure(source, CATEGORY, tmp_path)
    assert next(tmp_path.rglob("source.md")).read_text() == source


def test_contract_retains_category_target():
    result = guard.writer_contract("原有目标一万二千字，素材：{raw_text}", CATEGORY)
    assert result.startswith("原有目标一万二千字")
    assert "不得编造事实" in result and "八百五十" in result
    assert "{raw_text}" in result


def test_writer_gate_runs_before_any_polish_and_preserves_original(tmp_path, monkeypatch):
    source = manuscript([900] * 6 + [700] * 6)
    calls = []

    async def writer(prompt, **kwargs):
        assert "正文篇幅与分章协调要求" in prompt
        return source

    async def polish(text, **kwargs):
        assert not guard.structure_issues(text, CATEGORY)
        assert prose(text) == prose(source)
        calls.append("polish")
        raise RuntimeError("STOP_AFTER_VERIFIED_WRITER_GATE")

    monkeypatch.setattr(engine, "_route_writer_call", writer)
    monkeypatch.setattr(engine, "_polish_by_paragraph_chunks_strict", polish)
    with pytest.raises(RuntimeError, match="STOP_AFTER_VERIFIED_WRITER_GATE"):
        asyncio.run(engine.generate_full_book("原视频素材", category=CATEGORY, output_dir=tmp_path))
    assert calls == ["polish"]
    assert (tmp_path / "01_writer.md").read_text() == source


def test_unrepairable_writer_never_calls_polish(tmp_path, monkeypatch):
    source = manuscript([2200, 700, 700, 2200])

    async def writer(*args, **kwargs):
        return source

    async def forbidden(*args, **kwargs):
        pytest.fail("invalid writer must stop before paid editing")

    monkeypatch.setattr(engine, "_route_writer_call", writer)
    monkeypatch.setattr(engine, "_polish_by_paragraph_chunks_strict", forbidden)
    with pytest.raises(guard.ChapterStructureHold):
        asyncio.run(engine.generate_full_book("原视频素材", category=CATEGORY, output_dir=tmp_path))


def test_final_gate_repairs_structure_without_paid_calls(tmp_path, monkeypatch):
    source = manuscript([900] * 6 + [700] * 6).split("@参考资料")[0].strip()

    async def forbidden(*args, **kwargs):
        pytest.fail("structure merging must not call a model")

    monkeypatch.setattr(engine, "_route_writer_call", forbidden)
    result = asyncio.run(engine._repair_final_hard_errors_once(source, category=CATEGORY, output_dir=tmp_path))
    assert not guard.structure_issues(result, CATEGORY)
    assert prose(result) == prose(source)


def test_structure_repair_does_not_disable_semantic_hard_errors(tmp_path, monkeypatch):
    source = manuscript([900] * 6 + [700] * 6).split("@参考资料")[0].strip()
    source += "\n这盘菜很好喝。"
    calls = []

    async def repair(prompt, **kwargs):
        assert "菜很好喝" in prompt
        calls.append("semantic_gate_still_active")
        return guard.ensure_chapter_structure(source, CATEGORY).replace("菜很好喝", "菜很好吃")

    monkeypatch.setattr(engine, "_route_writer_call", repair)
    result = asyncio.run(engine._repair_final_hard_errors_once(source, category=CATEGORY, output_dir=tmp_path))
    assert calls == ["semantic_gate_still_active"]
    assert "菜很好吃" in result and "菜很好喝" not in result


def test_saved_final_resumes_without_replaying_any_paid_generation(tmp_path, monkeypatch):
    source = manuscript([900] * 6 + [700] * 6).split("@参考资料")[0].strip()
    (tmp_path / "04_format.md").write_text(source)

    async def forbidden(*args, **kwargs):
        pytest.fail("saved final must not replay paid generation")

    async def final_gate(text, **kwargs):
        assert not guard.structure_issues(text, CATEGORY)
        return text

    monkeypatch.setattr(engine, "polish_provider_name", lambda: "gemini")
    monkeypatch.setattr(engine, "_route_writer_call", forbidden)
    monkeypatch.setattr(engine, "_polish_by_paragraph_chunks_strict", forbidden)
    monkeypatch.setattr(engine, "format_book_text", forbidden)
    monkeypatch.setattr(engine, "_apply_final_aigc_gate", final_gate)
    result = asyncio.run(engine.generate_full_book("视频原稿", category=CATEGORY, output_dir=tmp_path))
    assert prose(result) == prose(source)
    assert next(tmp_path.rglob("source.md")).read_text() == source
