from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.api.routers.tasks import _normalize_task_config
from app.prompts.classic_text import (
    COMIC_CATEGORIES, MANIFEST, VERSION, base_category, get_prompts,
    workflow_for_existing_task,
)
from app.prompts.goal_driven_templates import SUPPORTED_CATEGORIES
from app.services import classic_text_workflow as classic, content_engine
from app.services.batch_spend import BatchBudgetStop

BOOK = "@最终书名\n测试书\n@最终简介\n简介\n@目录\n1. 起点\n@导言\n导言内容\n@正文\n@1. 起点\n正文内容。"


@pytest.mark.parametrize("category", [c for c in SUPPORTED_CATEGORIES if c not in COMIC_CATEGORIES and c not in {"social_science_test", "social_pain_2610"}])
def test_new_task_pins_classic_subject_but_old_tasks_keep_workflow(category):
    cfg = _normalize_task_config({"category": category})
    assert cfg["text_workflow_version"] == VERSION
    assert cfg["text_prompt_base_category"] == base_category(category)
    assert workflow_for_existing_task({"category": category}) == "legacy_20260704"
    assert set(get_prompts(category)) == {"full_book_writer", "gemini_fast_polish", "gemini_fast_refine", "final_book_format"}


def install_clients(monkeypatch, calls, *, stop_polish=False):
    async def writer(prompt, **kwargs):
        calls.append(("writer", prompt))
        return BOOK
    def polish(text, **kwargs):
        calls.append(("polish", kwargs["system_instruction"]))
        if stop_polish:
            raise BatchBudgetStop("budget stopped")
        return text
    def refine(text, **kwargs):
        calls.append(("refine", kwargs["system_instruction"]))
        return text
    def formatter(text, **kwargs):
        calls.append(("format", kwargs["prompt_template"]))
        return text
    monkeypatch.setattr(content_engine, "_route_writer_call", writer)
    monkeypatch.setattr(content_engine, "polish_book_text", polish)
    monkeypatch.setattr(content_engine, "refine_book_text", refine)
    monkeypatch.setattr(content_engine, "format_book_text", formatter)


def test_full_pipeline_uses_only_historical_prompts_and_reuses_paid_stages(tmp_path, monkeypatch):
    calls = []
    install_clients(monkeypatch, calls)
    async def modern_must_not_run(*args, **kwargs):
        raise AssertionError("modern finance continuation must not run")
    monkeypatch.setattr(content_engine, "_generate_finance_longform_book", modern_must_not_run)
    result = asyncio.run(content_engine.generate_full_book(
        "原始材料", category="finance_technology", output_dir=tmp_path,
        source_duration_seconds=7200, text_workflow_version=VERSION,
    ))
    prompts = get_prompts("finance_technology")
    assert calls == [
        ("writer", prompts["full_book_writer"].format(raw_text="原始材料")),
        ("polish", prompts["gemini_fast_polish"]),
        ("refine", prompts["gemini_fast_refine"]),
        ("format", prompts["final_book_format"]),
    ]
    assert "@正文" in result
    assert json.loads((tmp_path / "00_text_workflow.json").read_text())["version"] == VERSION
    assert asyncio.run(content_engine.generate_full_book(
        "原始材料", category="finance_technology", output_dir=tmp_path, text_workflow_version=VERSION,
    )) == result
    assert len(calls) == 4
    with pytest.raises(ValueError, match="cache differs"):
        asyncio.run(classic.generate_full_book("另一份材料", output_dir=tmp_path))
    assert len(calls) == 4


def test_budget_pause_is_not_swallowed_or_cached_as_success(tmp_path, monkeypatch):
    calls = []
    install_clients(monkeypatch, calls, stop_polish=True)
    with pytest.raises(BatchBudgetStop):
        asyncio.run(classic.generate_full_book("素材", output_dir=tmp_path))
    assert (tmp_path / "01_writer.md").exists()
    assert not (tmp_path / "02_polish.md").exists()
    assert not (tmp_path / "04_format.md").exists()
    install_clients(monkeypatch, calls)
    asyncio.run(classic.generate_full_book("素材", output_dir=tmp_path))
    assert sum(stage == "writer" for stage, _ in calls) == 1


def test_unversioned_cache_requires_explicit_regeneration(tmp_path, monkeypatch):
    (tmp_path / "01_writer.md").write_text(BOOK)
    calls = []
    install_clients(monkeypatch, calls)
    with pytest.raises(ValueError, match="Unversioned"):
        asyncio.run(classic.generate_full_book("素材", output_dir=tmp_path))
    assert calls == []
    assert (tmp_path / "01_writer.md").read_text() == BOOK


def test_mid_polish_budget_pause_reuses_completed_chunks(tmp_path, monkeypatch):
    counts = {}
    blocked = True
    def polish(text, **kwargs):
        nonlocal blocked
        counts[text] = counts.get(text, 0) + 1
        if text == "乙" * 900 and blocked:
            raise BatchBudgetStop("budget stopped mid-book")
        return text
    monkeypatch.setattr(content_engine, "polish_book_text", polish)
    source = "甲" * 900 + "\n\n" + "乙" * 900
    with pytest.raises(BatchBudgetStop):
        asyncio.run(classic._polish_by_paragraph_chunks(source, output_dir=tmp_path))
    blocked = False
    assert asyncio.run(classic._polish_by_paragraph_chunks(source, output_dir=tmp_path)) == source
    assert counts["甲" * 900] == 1
    assert counts["乙" * 900] == 2


def test_historical_split_keeps_whole_paragraphs_and_subject_contract():
    long_paragraph = "甲" * 2000
    assert classic._split_text_by_paragraph(long_paragraph + "\n\n" + "乙" * 20) == [long_paragraph, "乙" * 20]
    assert MANIFEST["source_commit"] == "70c7ad81dca81c14966c39c574b6bb5797da99ac"
    assert "12000" in get_prompts("life_philosophy")["full_book_writer"]
    assert "！！！" not in get_prompts("life_philosophy")["final_book_format"]


@pytest.mark.parametrize("category", ["story_comic", "homophone_comic"])
def test_latest_comics_never_enter_historical_text_pipeline(category, tmp_path, monkeypatch):
    calls = []
    async def variant(raw, **kwargs):
        calls.append(kwargs["category"])
        return "漫画产物"
    monkeypatch.setattr(content_engine, "_generate_comic_variant_book", variant)
    assert "text_workflow_version" not in _normalize_task_config({"category": category, "comic_title": "测试漫画"})
    assert asyncio.run(content_engine.generate_full_book(
        "书名：测试漫画", category=category, output_dir=tmp_path, text_workflow_version=VERSION,
    )) == "漫画产物"
    assert calls == [category]
