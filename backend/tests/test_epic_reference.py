from __future__ import annotations

import uuid

from app.models import ReferenceBook, ReferencePatternCard, ReferenceSectionAnalysis
from app.services.epic_model_router import resolve_model
from app.services.epic_novel import validate_bible_section_content
from app.services.epic_reference import SoNovelAdapter, parse_booklist_seed
from app.services.epic_reference import (
    _abstract_card_data,
    _analysis_hash,
    _analysis_is_production_ready,
    _card_is_production_ready,
    _fallback_section_analysis,
    _estimate_reference_model_cost,
    _gate2b_should_process_section,
    _gate2b_verdict,
    _merge_card_group_data,
    _normalize_section_analysis,
    _profile_raw_json_payload,
    _stable_source_section_id,
    split_by_chapter,
)


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


def _gate2a_book() -> ReferenceBook:
    return ReferenceBook(
        id=uuid.uuid4(),
        title="测试参考书",
        author="测试作者",
        tier=1,
        category="bureaucracy",
        genre="ancient_transmigration_mystery_xianxia",
        source_path="dummy.txt",
        file_hash="abcdef1234567890",
    )


def _gate2a_section() -> dict[str, object]:
    return {
        "title": "第一章 测试",
        "text": "县衙档案出现矛盾，主角必须在规章限制中找到线索。" * 80,
        "sectionIndex": 0,
        "sectionHash": "sectionhash123456",
        "startOffset": 0,
        "endOffset": 500,
    }


def _gate2a_success_analysis(book: ReferenceBook, section: dict[str, object]) -> dict[str, object]:
    section["sourceSectionId"] = _stable_source_section_id(book, section)
    data = {
        "sectionSummary": "本节通过县衙档案矛盾制造制度压力，角色必须在规章限制中寻找可验证线索，并在小胜后承担新的责任。",
        "plotFunction": "把局部案件推进为长期制度矛盾，让眼前问题连接更大的主线压力。",
        "pleasureFunction": "读者爽点来自信息差、程序阻力、阶段反转和有代价的线索兑现。",
        "characterArcMove": "角色从被动应对规章，转为主动利用规章漏洞，但因此暴露自身立场。",
        "heroineAgencyMove": "女性角色掌握独立证据和行动目标，她的选择改变调查路径，而不是被动等待救援。",
        "mysteryClueForeshadowActions": ["档案时间冲突", "证人说法延迟解释", "局部答案指向上级机构"],
        "bureaucracyCourtFactionFunction": "制度流程、档案权限和派系利益共同构成阻力，让调查必须绕开正式程序。",
        "powerSystemMove": "能力推进受到身份、资源、代价和公开承认限制，不能无成本跃迁。",
        "pacingRole": "阶段答案解决眼前困境，同时留下更高层机构的未解问题作为下一节钩子。",
        "antiClicheNotes": ["不复制原书表达", "不照搬案件、机构、人物或反转", "不使用天降便利解决冲突"],
        "reusableMechanisms": [
            {"name": "制度阻力转线索", "function": "把流程卡点转化为可验证证据"},
            {"name": "局部兑现挂全局", "function": "小答案解决当下问题，同时暴露更大结构矛盾"},
        ],
        "notToCopyNotes": ["原书措辞", "原书人物名", "原书案件结构", "原书机构名"],
    }
    normalized = _normalize_section_analysis(book, section, data)
    normalized["sourceAnalysisId"] = str(uuid.uuid4())
    normalized["analysisHash"] = _analysis_hash(normalized)
    return normalized


def test_gate2a_stable_source_section_id_uses_book_and_section_hash() -> None:
    book = _gate2a_book()
    section = _gate2a_section()

    first = _stable_source_section_id(book, section)
    second = _stable_source_section_id(book, dict(section))

    assert first == second
    assert first.startswith("book_abcdef123456_section_000001_sectionhash")


def test_gate2a_normalized_success_analysis_is_chinese_and_production_ready() -> None:
    book = _gate2a_book()
    section = _gate2a_section()
    analysis = _gate2a_success_analysis(book, section)
    row = ReferenceSectionAnalysis(
        id=uuid.uuid4(),
        section_id=uuid.uuid4(),
        source_book_id=book.id,
        section_hash=str(section["sectionHash"]),
        status="success",
        analysis_json=analysis,
        raw_text_included=False,
    )

    assert analysis["production_quality"] is True
    assert analysis["rawTextIncluded"] is False
    assert "Pleasure Pattern" not in str(analysis)
    assert _analysis_is_production_ready(row) is True


def test_gate2a_fallback_analysis_is_never_production_ready() -> None:
    book = _gate2a_book()
    section = _gate2a_section()
    section["sourceSectionId"] = _stable_source_section_id(book, section)
    analysis = _fallback_section_analysis(book, section)
    row = ReferenceSectionAnalysis(
        id=uuid.uuid4(),
        section_id=uuid.uuid4(),
        source_book_id=book.id,
        section_hash=str(section["sectionHash"]),
        status="fallback",
        analysis_json=analysis,
        raw_text_included=False,
    )

    assert analysis["production_quality"] is False
    assert _analysis_is_production_ready(row) is False


def test_gate2a_pattern_card_comes_from_analysis_and_keeps_source_ids() -> None:
    book = _gate2a_book()
    section = _gate2a_section()
    analysis = _gate2a_success_analysis(book, section)
    card_data = _abstract_card_data(book, section, "bureaucracy", 0, analysis)
    card = ReferencePatternCard(id=uuid.uuid4(), source_book_id=book.id, **card_data)

    assert card_data["source_section_ids"] == [analysis["sourceSectionId"]]
    assert card_data["source_analysis_ids"] == [analysis["sourceAnalysisId"]]
    assert card_data["source_book_ids"] == [str(book.id)]
    assert card_data["raw_text_included"] is False
    assert card_data["raw_json"]["production_quality"] is True
    assert "官僚" in card_data["name"] or "制度" in card_data["function"]
    assert _card_is_production_ready(card) is True


def test_gate2a_old_card_without_source_sections_is_excluded_from_production_ready_counts() -> None:
    old_card = ReferencePatternCard(
        id=uuid.uuid4(),
        pattern_key="old:card",
        category="pleasure",
        name="旧卡",
        description="旧卡没有分节证据",
        source_section_ids=[],
        source_analysis_ids=[],
        source_book_ids=[],
        raw_text_included=False,
        raw_json={"production_quality": True, "rawTextIncluded": False},
    )

    assert _card_is_production_ready(old_card) is False


def test_gate2a_merge_preserves_all_source_ids_and_evidence_count() -> None:
    book = _gate2a_book()
    section_one = _gate2a_section()
    section_two = _gate2a_section()
    section_two["sectionIndex"] = 1
    section_two["sectionHash"] = "sectionhash654321"
    analysis_one = _gate2a_success_analysis(book, section_one)
    analysis_two = _gate2a_success_analysis(book, section_two)
    first = ReferencePatternCard(id=uuid.uuid4(), source_book_id=book.id, **_abstract_card_data(book, section_one, "bureaucracy", 0, analysis_one))
    second = ReferencePatternCard(id=uuid.uuid4(), source_book_id=book.id, **_abstract_card_data(book, section_two, "bureaucracy", 1, analysis_two))

    merged = _merge_card_group_data([first, second])

    assert set(merged["source_section_ids"]) == {analysis_one["sourceSectionId"], analysis_two["sourceSectionId"]}
    assert set(merged["source_analysis_ids"]) == {analysis_one["sourceAnalysisId"], analysis_two["sourceAnalysisId"]}
    assert set(merged["source_card_ids"]) == {str(first.id), str(second.id)}
    assert merged["evidence_count"] >= 2
    assert merged["raw_json"]["rawTextIncluded"] is False


def test_gate2a_profile_raw_json_reports_required_metadata() -> None:
    payload = _profile_raw_json_payload(
        generation_status="success",
        source_book_ids=["book-1", "book-2"],
        source_card_ids=["card-1"],
        source_section_ids=["section-1", "section-2"],
        categories={"bureaucracy": 1},
        evidence_counts=[2, 4],
        production_ready_only=True,
    )

    assert payload["generationStatus"] == "success"
    assert payload["sourceBookCount"] == 2
    assert payload["sourceCardCount"] == 1
    assert payload["sourceSectionIdsSample"] == ["section-1", "section-2"]
    assert payload["rawTextIncluded"] is False
    assert payload["categoriesCovered"] == ["bureaucracy"]
    assert payload["evidenceCounts"]["min"] == 2
    assert payload["evidenceCounts"]["max"] == 4
    assert payload["oldSourceLessCardsExcluded"] is True
    assert payload["productionReadyCardCount"] == 1


def test_gate2b_resume_and_retry_rules_do_not_reprocess_successes() -> None:
    should_process, reason = _gate2b_should_process_section(
        "success",
        resume=True,
        retry_failed_only=False,
        force=False,
    )

    assert should_process is False
    assert reason == "already_success"

    should_process, reason = _gate2b_should_process_section(
        "fallback",
        resume=True,
        retry_failed_only=True,
        force=False,
    )

    assert should_process is True
    assert reason == "retry_failed"

    should_process, reason = _gate2b_should_process_section(
        None,
        resume=True,
        retry_failed_only=True,
        force=False,
    )

    assert should_process is False
    assert reason == "retry_failed_only_skip"


def test_gate2b_cost_estimator_returns_positive_configurable_proxy() -> None:
    cost = _estimate_reference_model_cost(10_000, 2_000)

    assert cost > 0


def test_gate2b_verdict_is_partial_for_staged_not_full_run() -> None:
    verdict = _gate2b_verdict(
        {
            "useLlmExtraction": True,
            "deepseekCredentialsAvailable": True,
            "dryRun": False,
            "gate2aBaselineSuccessfulAnalyses": 5,
            "gate2aBaselineProductionReadyCards": 18,
            "gate2aBaselineSourceSections": 5,
            "gate2aBaselineSourceAnalyses": 5,
            "cumulativeSuccessfulAnalyses": 15,
            "cumulativeProductionReadyCards": 36,
            "sourceBookIdsPreserved": 3,
            "sourceSectionIdsPreserved": 15,
            "sourceAnalysisIdsPreserved": 15,
            "profileRawTextIncluded": False,
            "profileChinese": True,
            "estimatedImportedCorpusSections": 106_914,
            "processedBookCount": 3,
            "importedBookCount": 57,
        }
    )

    assert verdict == "PARTIAL"


def test_bible_section_acceptance_rejects_placeholder_or_english() -> None:
    bad = validate_bible_section_content(
        "Reference cards available. TODO placeholder.",
        {"sectionKey": "project_overview", "storyPayload": {}, "referenceProfile": {}, "rawTextIncluded": False},
    )

    assert bad["status"] == "fail"
    assert "boilerplate_or_placeholder" in bad["issues"]
    assert "mostly_english" in bad["issues"]
