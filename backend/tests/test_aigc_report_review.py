from __future__ import annotations

import json

import pytest

from app.services import aigc_report_review as review
from app.services.aigc_report_review import (
    ZhuqueSegment,
    map_segments_to_original,
    optimize_report_segments,
    parse_report_overview,
    parse_report_segment_rows,
    quality_audit_passed,
    recommend_target_segments,
    report_source_match_confidence,
    review_deterministic_constraints,
)


SAMPLE_REPORT_TEXT = """
朱雀 AI 检测报告
人工特征 0%
疑似AI 62.17%
AI特征 37.83%
片段1 8.30% 1440 0.9956
片段2 15.11% 2621 0.9271
片段3 1.72% 299 0.9973
片段4 8.08% 1402 0.9455
片段5 8.23% 1427 0.9950
片段6 14.77% 2562 0.9460
片段7 3.30% 572 0.9941
片段8 7.95% 1380 0.9711
片段9 6.48% 1125 0.9951
片段10 6.45% 1119 0.9773
片段11 5.15% 893 0.9930
片段12 6.63% 1150 0.9543
片段13 4.65% 806 0.9947
片段14 3.18% 552 0.9805
"""


def test_real_report_summary_and_rows_are_parsed() -> None:
    overview = parse_report_overview(SAMPLE_REPORT_TEXT)
    rows = parse_report_segment_rows(SAMPLE_REPORT_TEXT)

    assert overview == {"human_percent": 0.0, "suspected_percent": 62.17, "ai_percent": 37.83}
    assert len(rows) == 14
    assert rows[0]["label"] == "ai"
    assert rows[1]["label"] == "suspected"
    assert rows[-1]["aigc_value"] == pytest.approx(0.9805)


def test_strict_human_rule_requires_more_than_50(monkeypatch: pytest.MonkeyPatch) -> None:
    threshold_report = """
    人工特征 50%
    疑似AI 50%
    AI特征 0%
    片段1 50% 100 0.4000
    片段2 50% 100 0.7000
    """
    monkeypatch.setattr(
        review,
        "extract_report_text",
        lambda *_args: (threshold_report, "test", 2),
    )
    source = "甲" * 200
    parsed = review.parse_zhuque_report(b"%PDF-test", source)
    assert parsed["human_percent"] == 50.0
    assert parsed["strict_pass"] is False


def test_complete_segment_table_overrides_misordered_chart_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    actual_ocr_order = "0%\n人工特征37.83%\n疑似AI\nAI特征\n62.17%\n" + SAMPLE_REPORT_TEXT
    monkeypatch.setattr(review, "extract_report_text", lambda *_args: (actual_ocr_order, "local_rapidocr", 19))
    rows = parse_report_segment_rows(SAMPLE_REPORT_TEXT)
    source = "甲" * sum(item["char_count"] for item in rows)

    parsed = review.parse_zhuque_report(b"%PDF-test", source)

    assert parsed["human_percent"] == 0.0
    assert parsed["suspected_percent"] == 62.17
    assert parsed["ai_percent"] == 37.83


def test_report_detail_body_is_matched_to_the_same_manuscript() -> None:
    report_text = """
    检测片段详情
    NO.1
    片段1
    AIGC值0.9956
    很多人在做人生抉择时，最难熬的时刻往往不是宏大的转折点，而是在看到一张账单的瞬间。
    房租每个月准时到期，孩子的学费也不会因为行业不景气就往后推。
    """
    original = "开头。" + (
        "很多人在做人生抉择时，最难熬的时刻往往不是宏大的转折点，而是在看到一张账单的瞬间。"
        "房租每个月准时到期，孩子的学费也不会因为行业不景气就往后推。"
    ) + "结尾。"

    confidence, sample_chars = report_source_match_confidence(report_text, original)

    assert sample_chars >= 60
    assert confidence >= 0.95


def test_cost_first_plan_uses_suspected_segments_before_red_segments() -> None:
    rows = parse_report_segment_rows(SAMPLE_REPORT_TEXT)
    source = "甲" * sum(item["char_count"] for item in rows)
    segments = map_segments_to_original(rows, source)

    selected, projected = recommend_target_segments(
        segments,
        human_percent=0,
        target_human_percent=60,
    )

    assert set(selected) == {2, 4, 6, 8, 10, 12, 14}
    assert projected == pytest.approx(62.17)
    assert all(
        next(item for item in segments if item.segment_id == value).label == "suspected"
        for value in selected
    )


def test_repeated_anchors_and_paragraphs_are_hard_protected() -> None:
    original = "合同金额260元，复核仍为260元。\n\n详见《示例》与https://example.com。"
    missing = "合同金额260元，复核仍为该金额。\n\n详见《示例》与https://example.com。"
    merged = "合同金额260元，复核仍为260元。详见《示例》与https://example.com。"

    first = review_deterministic_constraints(original, missing)
    second = review_deterministic_constraints(original, merged)
    assert first.passed is False
    assert first.missing_anchors == ("260元",)
    assert second.passed is False
    assert second.paragraph_count_preserved is False


def test_classical_original_fragment_is_immutable() -> None:
    original = "（原文）山不在高，有仙则名。\n\n（翻译）山不一定要高，有仙人居住就出名了。"
    changed = "（原文）山不在高，有仙就有名。\n\n（翻译）山不一定要高，有仙人居住就出名了。"

    result = review_deterministic_constraints(
        original,
        changed,
        protected_texts=("（原文）山不在高，有仙则名。",),
    )

    assert result.passed is False
    assert result.protected_text_preserved is False
    assert "古文/佛经受保护原文被改动" in result.reasons


def test_one_candidate_plus_one_full_audit_produces_guarded_candidate() -> None:
    original = "第一段保留事实260元，并说明原因。\n\n第二段延续结论，不新增案例。"
    segment = ZhuqueSegment(
        segment_id=1,
        share_percent=60.0,
        char_count=len(original),
        aigc_value=0.93,
        label="suspected",
        start=0,
        end=len(original),
        mapped_text=original,
        mapping_confidence=1.0,
        recommended=True,
    )
    calls: list[str] = []

    def rewrite_fn(_prompt: str, **_kwargs):
        calls.append("rewrite")
        return "事实仍是260元，原因也完整保留。\n\n结论接着展开，没有加入新案例。"

    def audit_fn(_prompt: str, **_kwargs):
        calls.append("audit")
        return json.dumps(
            {
                "pass": True,
                "semantic_fidelity": 98,
                "information_completeness": 99,
                "fluency": 95,
                "voice_continuity": 94,
                "no_added_facts": True,
                "no_removed_information": True,
                "no_stance_change": True,
                "no_plot_or_event_change": True,
                "failed_segment_ids": [],
                "reason": "通过",
            },
            ensure_ascii=False,
        )

    result = optimize_report_segments(original, [segment], [1], rewrite_fn=rewrite_fn, audit_fn=audit_fn)
    assert calls == ["rewrite", "audit"]
    assert result["status"] == "candidate_ready"
    assert result["deliverable"] is False
    assert result["candidate_text"] != original
    assert "真实朱雀复检" in result["next_action"]


def test_quality_failure_never_exports_candidate() -> None:
    original = "原稿事实完整，没有新增内容。"
    segment = ZhuqueSegment(1, 100, len(original), 0.995, "ai", 0, len(original), original, 1.0, True)

    def rewrite_fn(_prompt: str, **_kwargs):
        return "原稿事实完整，没有新增内容。"

    def audit_fn(_prompt: str, **_kwargs):
        return json.dumps(
            {
                "pass": True,
                "semantic_fidelity": 94,
                "information_completeness": 99,
                "fluency": 99,
                "voice_continuity": 99,
                "no_added_facts": True,
                "no_removed_information": True,
                "no_stance_change": True,
                "no_plot_or_event_change": True,
                "failed_segment_ids": [1],
                "reason": "语义忠实度不足",
            }
        )

    result = optimize_report_segments(original, [segment], [1], rewrite_fn=rewrite_fn, audit_fn=audit_fn)
    assert result["status"] == "manual_review"
    assert result["candidate_text"] == ""
    assert result["original_text"] == original
    assert quality_audit_passed(result["quality_audit"]) is False
