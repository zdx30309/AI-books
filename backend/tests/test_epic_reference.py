from __future__ import annotations

import uuid

from app.models import ReferenceBook
from app.services.epic_model_router import resolve_model
from app.services.epic_novel import validate_bible_section_content
from app.services.epic_reference import SoNovelAdapter, parse_booklist_seed
from app.services.epic_reference import _abstract_card_data, _fallback_section_analysis, split_by_chapter


def test_parse_inline_continuous_numbered_seed() -> None:
    raw = (
        "1. 《大奉打更人》 - 卖报小郎君（完结） "
        "2. 《仙官有令》 - 裴不了（170万字，完结）：穿越探案 "
        "第二梯队：仙侠探案（1本） "
        "21. 《奇物异志》 - 引魂香（连载中）：志怪探案 "
        "第三梯队：朝堂权谋（1本） "
        "51. 《龙符》 - 梦入神机（300万字，完结） "
        "第四梯队：长篇仙侠（1本） "
        "76. 《凡人修仙传》 - 忘语（770万字，完结）"
    )

    rows = parse_booklist_seed(raw_markdown=raw)

    assert [row.title for row in rows] == ["大奉打更人", "仙官有令", "奇物异志", "龙符", "凡人修仙传"]
    assert [row.author for row in rows] == ["卖报小郎君", "裴不了", "引魂香", "梦入神机", "忘语"]
    assert [row.tier_hint for row in rows] == [1, 1, 2, 3, 4]


def test_sonovel_candidate_selection_rejects_near_miss_title() -> None:
    adapter = SoNovelAdapter()
    results = [
        {"bookName": "一个人的武侠游记", "author": "萌天帝", "url": "https://example.invalid/wrong"},
        {"bookName": "一个人的武侠游戏", "author": "墨倾遥", "url": "https://example.invalid/right"},
    ]

    candidates = adapter.select_candidates(results, "一个人的武侠游戏", "墨倾遥", limit=3)

    assert len(candidates) == 1
    assert candidates[0]["url"] == "https://example.invalid/right"


def test_sonovel_candidate_selection_accepts_exact_title_without_author() -> None:
    adapter = SoNovelAdapter()
    results = [
        {"bookName": "仙道神", "author": "自由水杯", "url": "https://example.invalid/wrong"},
        {"bookName": "仙道神探簿", "author": "熙迟落", "url": "https://example.invalid/right"},
    ]

    candidates = adapter.select_candidates(results, "仙道神探簿", None, limit=3)

    assert len(candidates) == 1
    assert candidates[0]["url"] == "https://example.invalid/right"


def test_reference_role_defaults_to_deepseek_v4_pro() -> None:
    selection = resolve_model("reference")

    assert selection.provider == "deepseek"
    assert selection.model == "deepseek-v4-pro"


def test_reference_section_identity_and_pattern_card_are_abstract_only() -> None:
    book = ReferenceBook(
        id=uuid.uuid4(),
        title="测试书",
        author="测试作者",
        tier=1,
        category="core_similarity",
        source_path="dummy.epub",
        file_hash="filehash",
    )
    raw = "第一章 开端\n这是一段只用于抽象的参考文本。\n第二章 推进\n另一段参考文本。"

    sections = split_by_chapter(raw)
    analysis = _fallback_section_analysis(book, sections[0])
    card = _abstract_card_data(book, sections[0], "pleasure", 0, analysis)

    assert sections[0]["sourceSectionId"].startswith("section_")
    assert sections[0]["sectionHash"]
    assert analysis["sourceBookId"] == str(book.id)
    assert analysis["sourceSectionId"] == sections[0]["sourceSectionId"]
    assert analysis["rawTextIncluded"] is False
    assert card["source_section_ids"] == [sections[0]["sourceSectionId"]]
    assert card["evidence_count"] == 1
    assert card["raw_text_included"] is False
    assert "这是一段只用于抽象的参考文本" not in card["description"]
    assert card["raw_json"]["rawTextIncluded"] is False


def test_bible_section_acceptance_rejects_placeholder_or_english() -> None:
    bad = validate_bible_section_content(
        "Reference cards available. TODO placeholder.",
        {"sectionKey": "project_overview", "storyPayload": {}, "referenceProfile": {}, "rawTextIncluded": False},
    )

    assert bad["status"] == "fail"
    assert "boilerplate_or_placeholder" in bad["issues"]
    assert "mostly_english" in bad["issues"]
