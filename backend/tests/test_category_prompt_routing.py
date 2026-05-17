from __future__ import annotations

from pathlib import Path
import sys
import asyncio

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from app.prompts.goal_driven_templates import (
    SUPPORTED_CATEGORIES,
    get_category_meta,
    get_prompts,
)
from app.services import content_engine


MINIMAL_MANUSCRIPT = """@最终书名
测试书名
@最终简介
测试简介
@目录
1. 测试章节
@导言
测试导言。
@正文
@1. 测试章节
测试正文。
"""


@pytest.mark.parametrize("category", SUPPORTED_CATEGORIES)
def test_generate_full_book_routes_writer_prompt_by_category(
    category: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_text = "这是一段用于本地路由测试的原始文稿。"
    writer_prompts: list[str] = []
    expected_prompt = get_prompts(category)["full_book_writer"].format(raw_text=raw_text)

    async def fake_route_writer_call(prompt: str, **_: object) -> str:
        if prompt == expected_prompt:
            writer_prompts.append(prompt)
        return MINIMAL_MANUSCRIPT

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "polish_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "refine_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "format_book_text", lambda text, **_: text)

    result = asyncio.run(
        content_engine.generate_full_book(
            raw_text,
            category=category,
            output_dir=tmp_path / category,
        )
    )

    assert "@最终书名" in result
    assert writer_prompts == [expected_prompt]


def test_every_supported_category_has_ready_prompt_metadata() -> None:
    for category in SUPPORTED_CATEGORIES:
        meta = get_category_meta(category)
        prompts = get_prompts(category)

        assert meta["prompt_status"] == "ready"
        assert "{raw_text}" in prompts["full_book_writer"]
        assert "full_book_writer" in prompts
        assert "gemini_fast_polish" in prompts
        assert "gemini_fast_refine" in prompts
        assert "final_book_format" in prompts


def test_web_short_story_is_history_test_prompt_sync() -> None:
    history_prompts = get_prompts("history_culture")
    web_short_story_prompts = get_prompts("web_short_story")

    assert web_short_story_prompts == history_prompts
    assert get_category_meta("web_short_story")["label"] == "历史测试版"


def test_social_science_test_prompt_sync() -> None:
    social_prompts = get_prompts("social_science")
    social_test_prompts = get_prompts("social_science_test")

    assert social_test_prompts == social_prompts
    assert get_category_meta("social_science_test")["label"] == "社会痛点测试版"


def test_crayon_illustration_is_not_a_body_writing_category() -> None:
    assert "illustration_crayon" not in SUPPORTED_CATEGORIES
    with pytest.raises(ValueError):
        get_category_meta("illustration_crayon")


def test_web_short_story_style_reference_keeps_history_prompt_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_text = "女主醒来发现自己穿进了短剧反派剧本。"
    writer_prompts: list[str] = []

    async def fake_route_writer_call(prompt: str, **_: object) -> str:
        writer_prompts.append(prompt)
        return MINIMAL_MANUSCRIPT

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "polish_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "refine_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "format_book_text", lambda text, **_: text)

    asyncio.run(
        content_engine.generate_full_book(
            raw_text,
            category="web_short_story",
            output_dir=tmp_path / "web_short_story",
            style_reference="冷峻悬疑作家",
            style_notes="第一人称，反转更密。",
        )
    )

    assert writer_prompts[0].startswith(
        get_prompts("history_culture")["full_book_writer"].format(raw_text=raw_text)
    )
    assert "笔锋参考要求" in writer_prompts[0]
    assert "参考对象：冷峻悬疑作家" in writer_prompts[0]
    assert "补充要求：第一人称，反转更密。" in writer_prompts[0]
    assert "不要复刻、改写或搬运任何现成作品句子" in writer_prompts[0]


def test_social_science_test_style_reference_keeps_social_prompt_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_text = "普通社会议题素材。"
    writer_prompts: list[str] = []

    async def fake_route_writer_call(prompt: str, **_: object) -> str:
        writer_prompts.append(prompt)
        return MINIMAL_MANUSCRIPT

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "polish_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "refine_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "format_book_text", lambda text, **_: text)

    asyncio.run(
        content_engine.generate_full_book(
            raw_text,
            category="social_science_test",
            output_dir=tmp_path / "social_science_test",
            style_reference="冷峻非虚构笔法",
            style_notes="多一点现实细节和判断锋利度。",
        )
    )

    assert writer_prompts[0].startswith(
        get_prompts("social_science")["full_book_writer"].format(raw_text=raw_text)
    )
    assert "笔锋参考要求" in writer_prompts[0]
    assert "参考对象：冷峻非虚构笔法" in writer_prompts[0]
    assert "补充要求：多一点现实细节和判断锋利度。" in writer_prompts[0]
    assert "仍要严格遵守社会痛点类当前 prompt" in writer_prompts[0]


def test_style_reference_only_applies_to_test_categories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_text = "普通社会议题素材。"
    writer_prompts: list[str] = []

    async def fake_route_writer_call(prompt: str, **_: object) -> str:
        writer_prompts.append(prompt)
        return MINIMAL_MANUSCRIPT

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "polish_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "refine_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "format_book_text", lambda text, **_: text)

    asyncio.run(
        content_engine.generate_full_book(
            raw_text,
            category="social_science",
            output_dir=tmp_path / "social_science",
            style_reference="冷峻悬疑作家",
            style_notes="第一人称，反转更密。",
        )
    )

    assert "笔锋参考要求" not in writer_prompts[0]
