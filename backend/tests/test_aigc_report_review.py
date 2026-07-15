from __future__ import annotations

import json
import math
from types import SimpleNamespace

import pytest

from app.services import aigc_report_review as review
from app.services.aigc_report_review import (
    ZhuqueSegment,
    compare_zhuque_reports,
    map_segments_to_original,
    material_paragraph_coverage_requirement,
    material_rewrite_metrics,
    normalize_manuscript_text,
    optimize_report_segments,
    parse_rewrite_candidate,
    parse_report_overview,
    parse_report_segment_rows,
    quality_audit_passed,
    prepare_rewrite_protocol,
    recommend_target_segments,
    recommend_calibration_segments,
    report_source_match_confidence,
    render_audit_prompt,
    render_rewrite_prompt,
    review_deterministic_constraints,
    sha256_text,
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


def _passing_audit_json() -> str:
    return json.dumps(
        {
            "pass": True,
            "semantic_fidelity": 99,
            "information_completeness": 99,
            "fluency": 95,
            "voice_continuity": 95,
            "no_added_facts": True,
            "no_removed_information": True,
            "no_stance_change": True,
            "no_plot_or_event_change": True,
            "structural_rewrite_sufficient": True,
            "failed_segment_ids": [],
            "issues": [],
            "reason": "通过",
        },
        ensure_ascii=False,
    )


def test_multipart_crlf_and_json_lf_share_one_canonical_hash() -> None:
    multipart_text = "第一段。\r\n\r\n第二段包含260元。\r\n"
    json_text = "第一段。\n\n第二段包含260元。"

    assert normalize_manuscript_text(multipart_text) == json_text
    assert sha256_text(multipart_text) == sha256_text(json_text)


def test_model_strategy_exposes_grok_45_without_leaking_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(review.settings, "AIGC_REVIEW_REWRITE_PROVIDER", "grok")
    monkeypatch.setattr(review.settings, "XAI_API_KEY", "configured-secret")

    strategy = review.model_strategy()

    assert strategy["default_rewrite_provider"] == "grok"
    assert strategy["rewrite_model"] == "grok-4.5"
    grok = next(item for item in strategy["rewrite_providers"] if item["id"] == "grok")
    assert grok["configured"] is True
    assert grok["reasoning"] == "low"
    assert "configured-secret" not in json.dumps(strategy)


def test_grok_cost_uses_cached_input_and_output_prices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(review.settings, "AIGC_REVIEW_GROK_INPUT_USD_PER_M", 2.0)
    monkeypatch.setattr(review.settings, "AIGC_REVIEW_GROK_CACHED_INPUT_USD_PER_M", 0.5)
    monkeypatch.setattr(review.settings, "AIGC_REVIEW_GROK_OUTPUT_USD_PER_M", 6.0)
    monkeypatch.setattr(review.settings, "AIGC_REVIEW_USD_CNY_RATE", 7.2)

    actual = review._grok_cost_cny(1000, 400, 200)
    expected = ((600 * 2.0 + 400 * 0.5 + 200 * 6.0) / 1_000_000) * 7.2

    assert actual == pytest.approx(expected)


def test_xai_proxy_reuses_existing_official_api_egress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(review.settings, "XAI_HTTPS_PROXY", "")
    monkeypatch.setattr(review.settings, "OPENAI_HTTPS_PROXY", "http://proxy.example:7898")
    assert review._xai_proxy_url() == "http://proxy.example:7898"

    monkeypatch.setattr(review.settings, "XAI_HTTPS_PROXY", "http://xai-proxy.example:3128")
    assert review._xai_proxy_url() == "http://xai-proxy.example:3128"


def test_grok_rewrite_uses_high_reasoning_store_false_and_reports_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    class FakeResponses:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                status="completed",
                output_text="这是严格保留事实后的候选稿。",
                usage=SimpleNamespace(
                    input_tokens=1000,
                    output_tokens=200,
                    input_tokens_details=SimpleNamespace(cached_tokens=400),
                    output_tokens_details=SimpleNamespace(reasoning_tokens=80),
                ),
            )

    monkeypatch.setattr(review, "_get_xai_client", lambda: SimpleNamespace(responses=FakeResponses()))
    monkeypatch.setattr(review.settings, "AIGC_REVIEW_GROK_REASONING_EFFORT", "high")
    segment = ZhuqueSegment(1, 100, 20, 0.99, "ai", 0, 20, "甲" * 20, 1.0, True)

    content, usage = review._grok_rewrite(
        "只输出候选稿",
        segment=segment,
        request_id="request-1",
        attempt=1,
    )

    assert content == "这是严格保留事实后的候选稿。"
    assert captured["model"] == "grok-4.5"
    assert captured["reasoning"] == {"effort": "high"}
    assert captured["store"] is False
    assert captured["prompt_cache_key"].startswith("aigc-review:zhuque-report-review-v4.0:")
    assert usage["cached_input_tokens"] == 400
    assert usage["reasoning_tokens"] == 80
    assert usage["cost_cny"] > 0


def test_grok_missing_key_fails_before_any_paid_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(review.settings, "XAI_API_KEY", "")
    original = "完整事实仍然保留，没有新增信息。"
    segment = ZhuqueSegment(
        1, 100, len(original), 0.99, "ai", 0, len(original), original, 1.0, True
    )

    with pytest.raises(review.AigcReviewModelError, match="XAI_API_KEY"):
        optimize_report_segments(
            original,
            [segment],
            [1],
            rewrite_provider="grok",
            audit_fn=lambda *_args, **_kwargs: "never called",
        )


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


def test_calibration_plan_limits_first_paid_round_to_small_batch() -> None:
    rows = parse_report_segment_rows(SAMPLE_REPORT_TEXT)
    source = "甲" * sum(item["char_count"] for item in rows)
    segments = map_segments_to_original(rows, source)

    selected, projected = recommend_calibration_segments(segments, human_percent=0)

    assert 1 <= len(selected) <= 3
    selected_share = sum(item.share_percent for item in segments if item.segment_id in selected)
    assert selected_share <= 20
    assert projected == pytest.approx(selected_share)

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


def test_near_copy_is_rejected_before_paid_quality_audit() -> None:
    original = "\n\n".join(
        [
            "这段先交代背景事实，然后按照同一种句式给出判断。",
            "这一段继续说明既有材料，并在结尾重复前面的判断。",
            "接下来仍然沿用同样结构，把事实和结论整齐排列。",
            "第四段只是更换连接词，句子骨架和信息顺序没有变化。",
            "最后一段再次收束全文，重复已经说过的核心判断。",
        ]
    )
    candidate = original.replace("第四段只是", "第四段仅仅", 1)

    review_result = review_deterministic_constraints(
        original,
        candidate,
        require_material_rewrite=True,
    )

    assert review_result.passed is False
    assert review_result.literal_similarity > 0.98
    assert review_result.rewritten_paragraph_ratio < 0.8
    assert review_result.material_rewrite_passed is False


def test_new_duplicate_function_word_is_a_hard_quality_error() -> None:
    result = review_deterministic_constraints(
        "从一次伏击开始，他逐步稳住了位置。",
        "从从一次伏击说起，他后来才把位置稳住。",
    )

    assert result.hard_error_absent is False
    assert any("新增明显叠字硬伤" in reason for reason in result.reasons)


def test_material_rewrite_metrics_accept_real_clause_reconstruction() -> None:
    original = (
        "行业下行时，公司先缩减预算，随后取消了原定项目，团队因此停止招聘。\n\n"
        "管理层并未否认压力，他们只是认为现金流比短期规模更重要。"
    )
    candidate = (
        "预算最先被压缩，原定项目也随之取消；招聘停下来，是行业下行传导到团队的结果。\n\n"
        "压力确实存在，管理层没有回避。只是在他们的判断里，短期规模要让位于现金流。"
    )

    similarity, coverage, unchanged, editable = material_rewrite_metrics(original, candidate)
    result = review_deterministic_constraints(original, candidate, require_material_rewrite=True)

    assert 0.25 <= similarity <= 0.82
    assert coverage == 1
    assert unchanged == 0
    assert editable == 2
    assert result.material_rewrite_passed is True
    assert result.passed is True


def test_four_paragraph_segment_accepts_achievable_seventy_five_percent_coverage() -> None:
    original = "\n\n".join(
        [
            "行业转冷以后，公司先削减预算，随后暂停项目，团队也停止招聘。",
            "管理层承认经营压力，但他们认为现金流比短期规模更加重要。",
            "员工最初关注岗位变化，后来才发现项目节奏已经整体放慢。",
            "这一决定没有改变公司的长期方向，只调整了眼前的投入顺序。",
        ]
    )
    candidate = "\n\n".join(
        [
            "预算收缩是最先出现的变化。行业转冷传导下来，项目暂停，招聘随之停住。",
            "经营压力并未被管理层否认。在他们看来，短期规模现在必须让位于现金流。",
            "岗位是否变化，起初最牵动员工；项目整体放慢的事实，反倒是后来才显出来。",
            "这一决定没有改变公司的长期方向，只调整了眼前的投入顺序。",
        ]
    )

    result = review_deterministic_constraints(original, candidate, require_material_rewrite=True)

    assert result.editable_paragraphs == 4
    assert result.rewritten_paragraph_ratio == 0.75
    assert result.required_paragraph_coverage == 0.75
    assert material_paragraph_coverage_requirement(4) == 0.75
    assert result.material_rewrite_passed is True
    assert result.passed is True


def test_structured_protocol_restores_layout_headings_and_repeated_anchors() -> None:
    original = "井边少年\n\n唐宣宗大中六年，即公元852年，钱镠出生。\n\n铁券写着“恕卿九死”，公元852年仍需保留。"
    protocol = prepare_rewrite_protocol(original)

    assert "[[P0001]]" in protocol.prompt_segment
    assert "[[KEEP_" in protocol.prompt_segment
    rewritten = protocol.prompt_segment.replace("钱镠出生", "钱镠诞生")
    candidate, issues, structured = parse_rewrite_candidate(rewritten, protocol)

    assert structured is True
    assert issues == []
    assert candidate.splitlines()[0] == "井边少年"
    assert candidate.count("公元852年") == 2
    deterministic = review_deterministic_constraints(original, candidate)
    assert deterministic.passed is True


def test_structured_protocol_flattens_model_line_wrap_without_merging_paragraphs() -> None:
    original = "第一段说明事实。\n\n第二段说明结论。"
    protocol = prepare_rewrite_protocol(original)
    raw = protocol.prompt_segment.replace("第一段说明事实。", "第一段说明\n事实。")

    candidate, issues, structured = parse_rewrite_candidate(raw, protocol)

    assert structured is True
    assert issues == []
    assert candidate == "第一段说明 事实。\n\n第二段说明结论。"
    assert review_deterministic_constraints(original, candidate).paragraph_count_preserved is True


def test_anchor_occurrence_count_rejects_added_duplicate() -> None:
    original = "合同金额260元。"
    candidate = "合同金额260元，不是260元。"

    result = review_deterministic_constraints(original, candidate)

    assert result.passed is False
    assert result.extra_anchors == ("260元",)


def test_structured_protocol_rejects_anchor_moved_between_paragraphs() -> None:
    original = "首段金额260元并说明原因。\n\n次段金额380元并说明结论。"
    protocol = prepare_rewrite_protocol(original)
    blocks = protocol.prompt_segment
    first_token, second_token = [item[0] for item in protocol.protected_values]
    moved = blocks.replace(first_token, "__SWAP__").replace(second_token, first_token).replace("__SWAP__", second_token)

    _candidate, issues, structured = parse_rewrite_candidate(moved, protocol)

    assert structured is True
    assert any("移动到错误编号块" in issue for issue in issues)


def test_large_report_segment_protocol_keeps_all_blocks_and_anchors() -> None:
    paragraphs = [
        f"第{index}段记载公元852年发生的既有事实，金额260元，详见《旧志》。" + "叙述保持完整。" * 18
        for index in range(1, 19)
    ]
    original = "\n\n".join(paragraphs)
    protocol = prepare_rewrite_protocol(original)

    candidate, issues, structured = parse_rewrite_candidate(protocol.prompt_segment, protocol)

    assert len(original) >= 2700
    assert structured is True
    assert issues == []
    assert candidate == original
    assert review_deterministic_constraints(original, candidate).passed is True


def test_content_type_rules_are_loaded_from_prompt_assets() -> None:
    classical = render_rewrite_prompt(
        segment_id=1,
        original_segment="[[P0001]]译文[[/P0001]]",
        previous_context="",
        next_context="",
        content_type="classical",
    )
    public_domain = render_audit_prompt("原稿", "候选", [1], content_type="public_domain")

    assert "受保护原文必须逐字逐标点保留" in classical
    assert "句法重建" in classical
    assert "0.82" in classical
    assert "作品名、作者、出处" in public_domain


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
                "structural_rewrite_sufficient": True,
                "failed_segment_ids": [],
                "issues": [],
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


def test_shallow_synonym_rewrite_is_a_hard_gate_and_stops_before_audit() -> None:
    paragraphs = [
        "这是一段完整说明，用来保留原稿中的全部事实和既有判断。",
        "第二段继续解释原因，同时维持原先的立场与限定条件。",
        "第三段记录既有过程，不增加案例、数字或新的因果关系。",
        "最后一段收住原有结论，内容和语气都保持前后一致。",
    ]
    original = "\n\n".join(paragraphs)
    candidate = original.replace("这是一段完整说明", "这一段给出完整说明", 1)
    segment = ZhuqueSegment(
        1, 100, len(original), 0.99, "ai", 0, len(original), original, 1.0, True
    )
    calls: list[str] = []

    def rewrite_fn(_prompt: str, **_kwargs):
        calls.append("rewrite")
        return candidate

    def audit_fn(_prompt: str, **_kwargs):
        calls.append("audit")
        return _passing_audit_json()

    result = optimize_report_segments(
        original,
        [segment],
        [1],
        rewrite_fn=rewrite_fn,
        audit_fn=audit_fn,
    )

    assert calls == ["rewrite", "rewrite"]
    assert result["status"] == "manual_review"
    assert result["segment_results"][0]["substantive_rewrite_passed"] is False
    assert result["segment_results"][0]["material_rewrite_advisory_passed"] is False
    assert result["segment_results"][0]["rewrite_attempts"] == 2


def test_full_run_allows_more_than_three_selected_segments() -> None:
    originals = [
        "甲段保留既有事实和判断。",
        "乙段说明原来的原因关系。",
        "丙段维持已有立场和边界。",
        "丁段延续既定结论和语气。",
    ]
    candidates = [
        "既有事实和判断由甲段保留。",
        "原来的原因关系在乙段说明。",
        "已有立场和边界仍由丙段维持。",
        "既定结论和语气由丁段延续。",
    ]
    original = "\n\n".join(originals)
    segments = []
    cursor = 0
    for segment_id, value in enumerate(originals, start=1):
        start = original.index(value, cursor)
        end = start + len(value)
        cursor = end
        segments.append(
            ZhuqueSegment(
                segment_id, 25, len(value), 0.99, "ai", start, end, value, 1.0, True
            )
        )
    calls: list[int] = []

    def rewrite_fn(_prompt: str, **kwargs):
        segment = kwargs["segment"]
        calls.append(segment.segment_id)
        return candidates[segment.segment_id - 1]

    result = optimize_report_segments(
        original,
        segments,
        [1, 2, 3, 4],
        rewrite_fn=rewrite_fn,
        audit_fn=lambda *_args, **_kwargs: _passing_audit_json(),
    )

    assert calls == [1, 2, 3, 4]
    assert result["status"] == "candidate_ready"
    assert len(result["segment_results"]) == 4


def test_quality_failure_never_exports_candidate() -> None:
    original = "这份原稿完整记录了既有事实，也明确说明没有添加任何内容。"
    segment = ZhuqueSegment(1, 100, len(original), 0.995, "ai", 0, len(original), original, 1.0, True)

    def rewrite_fn(_prompt: str, **_kwargs):
        return "既有事实已被这份稿子完整记下，其中并未加入新的内容。"

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
    assert result["guarded_candidate_text"]
    assert result["original_text"] == original
    assert quality_audit_passed(result["quality_audit"]) is False


def test_deterministic_failure_gets_one_bounded_retry_before_audit() -> None:
    original = "第一段保留事实260元。\n\n第二段保留结论。"
    segment = ZhuqueSegment(1, 100, len(original), 0.95, "suspected", 0, len(original), original, 1.0, True)
    calls: list[str] = []

    def rewrite_fn(_prompt: str, **_kwargs):
        calls.append("rewrite")
        if len(calls) == 1:
            return "第一段遗漏金额。第二段也被合并。", {"input_tokens": 10, "output_tokens": 10, "cost_cny": 0.01}
        return "260元这一事实留在首段。\n\n结论由下一段继续保留。", {"input_tokens": 12, "output_tokens": 12, "cost_cny": 0.02}

    def audit_fn(_prompt: str, **_kwargs):
        calls.append("audit")
        return json.dumps(
            {
                "pass": True,
                "semantic_fidelity": 99,
                "information_completeness": 99,
                "fluency": 95,
                "voice_continuity": 95,
                "no_added_facts": True,
                "no_removed_information": True,
                "no_stance_change": True,
                "no_plot_or_event_change": True,
                "structural_rewrite_sufficient": True,
                "failed_segment_ids": [],
                "issues": [],
                "reason": "通过",
            },
            ensure_ascii=False,
        )

    result = optimize_report_segments(original, [segment], [1], rewrite_fn=rewrite_fn, audit_fn=audit_fn)

    assert calls == ["rewrite", "rewrite", "audit"]
    assert result["status"] == "candidate_ready"
    assert result["segment_results"][0]["rewrite_attempts"] == 2
    assert result["segment_results"][0]["attempts"][0]["passed"] is False
    assert result["segment_results"][0]["attempts"][1]["passed"] is True
    assert result["cost"]["estimated_cny"] >= 0.03


def test_two_failed_attempts_stop_before_paid_full_manuscript_audit() -> None:
    original = "第一段保留事实260元。\n\n第二段保留结论。"
    segment = ZhuqueSegment(1, 100, len(original), 0.95, "suspected", 0, len(original), original, 1.0, True)
    rewrite_calls = 0

    def rewrite_fn(_prompt: str, **_kwargs):
        nonlocal rewrite_calls
        rewrite_calls += 1
        return "金额丢失，而且自然段被合并。"

    def audit_fn(_prompt: str, **_kwargs):
        raise AssertionError("deterministic rejection must stop before full audit")

    result = optimize_report_segments(original, [segment], [1], rewrite_fn=rewrite_fn, audit_fn=audit_fn)

    assert rewrite_calls == 2
    assert result["status"] == "manual_review"
    assert result["segment_results"][0]["rewrite_attempts"] == 2
    assert result["segment_results"][0]["deterministic_review"]["missing_anchors"] == ["260元"]


def test_real_wdx_to_eeee_reports_are_aligned_and_expose_failed_direction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    before_rows = [
        (2709, 0.9707, 24.06), (297, 0.9941, 2.64), (527, 0.9825, 4.68),
        (545, 0.9954, 4.84), (525, 0.9884, 4.66), (580, 0.9957, 5.15),
        (538, 0.9860, 4.78), (557, 0.9974, 4.95), (559, 0.9856, 4.96),
        (541, 0.9940, 4.80), (850, 0.9815, 7.55), (275, 0.9979, 2.44),
        (2136, 0.9346, 18.97), (622, 0.9942, 5.52),
    ]
    after_rows = [
        (2459, 0.9705, 21.94), (539, 0.9935, 4.81), (575, 0.9772, 5.13),
        (274, 0.9903, 2.45), (805, 0.9761, 7.18), (1396, 0.9959, 12.46),
        (800, 0.9723, 7.14), (537, 0.9972, 4.79), (823, 0.9820, 7.34),
        (302, 0.9947, 2.69), (1973, 0.9228, 17.61), (544, 0.9917, 4.85),
        (179, 0.9846, 1.60),
    ]

    def make_segments(rows):
        cursor = 0
        values = []
        for segment_id, (chars, score, share) in enumerate(rows, start=1):
            values.append(
                {
                    "segment_id": segment_id,
                    "share_percent": share,
                    "char_count": chars,
                    "aigc_value": score,
                    "label": "suspected" if score < 0.99 else "ai",
                    "start": cursor,
                    "end": cursor + chars,
                    "mapped_text": "甲" * chars,
                    "mapping_confidence": 1.0,
                    "recommended": False,
                }
            )
            cursor += chars
        return values

    monkeypatch.setattr(review, "_EXPERIMENT_DIR", tmp_path)
    baseline = {
        "report_sha256": "before-report",
        "original_sha256": "original-hash",
        "human_percent": 0,
        "segments": make_segments(before_rows),
    }
    recheck = {
        "report_sha256": "after-report",
        "original_sha256": "candidate-hash",
        "human_percent": 0,
        "strict_pass": False,
        "source_match_verified": True,
        "segments": make_segments(after_rows),
    }
    selected = [1, 3, 9, 11, 13]
    optimize_result = {
        "request_id": "real-regression",
        "original_sha256": "original-hash",
        "candidate_sha256": "candidate-hash",
        "selected_segment_ids": selected,
        "segment_results": [
            {"segment_id": value, "strategy_id": "syntax_rebuild"}
            for value in selected
        ],
        "cost": {"estimated_cny": 0.0347},
        "models": {"rewrite": "deepseek-v4-pro", "audit": "gemini-3.1-flash-lite"},
    }

    compared = compare_zhuque_reports(baseline, recheck, optimize_result)
    by_id = {item["segment_id"]: item for item in compared["segment_deltas"]}

    assert compared["baseline_human_percent"] == 0
    assert compared["recheck_human_percent"] == 0
    assert compared["selected_weighted_delta"] < 0
    assert compared["selected_weighted_delta"] > -0.01
    assert by_id[1]["delta"] > 0
    assert by_id[9]["delta"] == pytest.approx(-0.0133, abs=0.001)
    assert compared["strategy_effectiveness"][0]["verdict"] == "neutral"
    assert compared["recommended_next_strategy"] is None
    assert compared["stop_automatic_spend"] is True
    assert compared["learning_eligible"] is False
    assert compared["historical_strategy_summary"] == []
    assert compared["historical_feature_summary"] == []
    assert (tmp_path / f"{compared['comparison_id']}.json").exists()


def test_flexible_protocol_preserves_new_paragraph_boundaries_and_anchors() -> None:
    original = "第一段记录260元这一既有事实，并说明原来的条件。\n\n第二段保留结论。"
    protocol = prepare_rewrite_protocol(original, flexible_paragraphs=True)

    assert len(protocol.block_payloads) == 1
    raw = protocol.prompt_segment.replace(
        "这一既有事实，并说明原来的条件。",
        "这个数字对应原有条件。\n\n条件仍按原稿保留。",
    )
    candidate, issues, structured = parse_rewrite_candidate(raw, protocol)

    assert structured is True
    assert issues == []
    assert "260元" in candidate
    assert candidate.count("\n\n") == 2
    deterministic = review_deterministic_constraints(
        original,
        candidate,
        preserve_paragraph_count=False,
    )
    assert deterministic.paragraph_count_preserved is False
    assert review.deterministic_hard_safety_passed(
        deterministic,
        require_paragraph_preservation=False,
    ) is True


def test_flexible_resegmentation_cannot_reorder_dates_or_numbers() -> None:
    original = "1899年发生第一件事，共260元。\n\n1903年发生第二件事，共300元。"
    candidate = "1903年发生第二件事，共300元。\n\n1899年发生第一件事，共260元。"

    deterministic = review_deterministic_constraints(
        original,
        candidate,
        preserve_paragraph_count=False,
    )

    assert deterministic.anchors_preserved is False
    assert any("锚点顺序" in reason for reason in deterministic.reasons)


def test_article_can_resegment_but_classical_keeps_paragraph_boundary_gate() -> None:
    original = "第一段保留260元事实。\n\n第二段保留原有结论。"
    candidate = "260元这一事实仍在，原有结论也完整保留。"
    segment = ZhuqueSegment(
        1, 100, len(original), 0.99, "ai", 0, len(original), original, 1.0, True
    )

    article = optimize_report_segments(
        original,
        [segment],
        [1],
        content_type="article",
        rewrite_fn=lambda *_args, **_kwargs: candidate,
        audit_fn=lambda *_args, **_kwargs: _passing_audit_json(),
    )
    classical = optimize_report_segments(
        original,
        [segment],
        [1],
        content_type="classical",
        rewrite_fn=lambda *_args, **_kwargs: candidate,
        audit_fn=lambda *_args, **_kwargs: _passing_audit_json(),
    )

    assert article["status"] == "candidate_ready"
    assert classical["status"] == "manual_review"


def test_segment_mapping_snaps_nearby_estimate_to_existing_paragraph_boundary() -> None:
    source = "甲" * 90 + "\n\n" + "乙" * 90
    rows = [
        {"segment_id": 1, "share_percent": 55.56, "char_count": 100, "aigc_value": 0.99, "label": "ai"},
        {"segment_id": 2, "share_percent": 44.44, "char_count": 80, "aigc_value": 0.90, "label": "suspected"},
    ]

    mapped = map_segments_to_original(rows, source)

    assert mapped[0].end == 92
    assert mapped[1].start == 92
    assert mapped[0].mapped_text.endswith("\n\n")
    assert mapped[1].mapped_text.startswith("乙")


def test_prompt_receives_real_report_signal_and_local_style_diagnosis() -> None:
    source = "1899年，他进入学校。\n\n1903年，他完成学业。\n\n随后，他开始任职。"
    prompt = render_rewrite_prompt(
        segment_id=7,
        original_segment=source,
        previous_context="",
        next_context="",
        segment_signal="AIGC 值=0.9972；报告分类=ai",
        style_diagnosis=review.diagnose_segment_style(source),
        flexible_paragraphs=True,
    )

    assert "0.9972" in prompt
    assert "时间词起段占比" in prompt
    assert "允许在块内重新分段" in prompt
    assert "红色区间降到橙色区间不等于成功" in prompt


def test_real_feedback_repeats_only_a_strategy_that_created_green_share(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    def segments(scores):
        return [
            {
                "segment_id": index,
                "share_percent": 50.0,
                "char_count": 100,
                "aigc_value": score,
                "label": review.classify_aigc_value(score),
                "start": (index - 1) * 100,
                "end": index * 100,
                "mapped_text": "甲" * 100,
                "mapping_confidence": 1.0,
                "recommended": True,
            }
            for index, score in enumerate(scores, start=1)
        ]

    monkeypatch.setattr(review, "_EXPERIMENT_DIR", tmp_path)
    baseline = {
        "report_sha256": "before",
        "original_sha256": "original",
        "human_percent": 0,
        "segments": segments([0.995, 0.995]),
    }
    recheck = {
        "report_sha256": "after",
        "original_sha256": "candidate",
        "human_percent": 50,
        "strict_pass": False,
        "source_match_verified": True,
        "segments": segments([0.80, 0.40]),
    }
    optimized = {
        "request_id": "green-test",
        "original_sha256": "original",
        "candidate_sha256": "candidate",
        "selected_segment_ids": [1, 2],
        "segment_results": [
            {"segment_id": 1, "strategy_id": "syntax_rebuild"},
            {"segment_id": 2, "strategy_id": "evidence_first"},
        ],
    }

    compared = compare_zhuque_reports(baseline, recheck, optimized)

    assert compared["selected_humanized_share_percent"] == 50.0
    assert compared["recommended_next_strategy"] is None
    assert compared["stop_automatic_spend"] is True
    assert compared["attribution_mode"] == "proportional_document_overlap"
    by_strategy = {item["strategy_id"]: item for item in compared["strategy_effectiveness"]}
    assert by_strategy["syntax_rebuild"]["green_conversion_rate"] == 0
    assert by_strategy["evidence_first"]["green_conversion_rate"] == 1


def test_editorial_units_are_bounded_for_large_report_region() -> None:
    original = "".join(
        f"这一句记录第{chr(0x4E00 + index)}项既有判断，并说明它与前文的关系。"
        for index in range(120)
    )

    units = review.split_editorial_units(original, min_chars=350, max_chars=900)

    assert len(units) >= 3
    assert "".join(item.text for item in units) == original
    assert all(len(item.text) <= 900 for item in units)
    assert all(len(item.text) >= 350 for item in units)


def test_fact_ledger_reauthor_prompt_never_contains_original_prose() -> None:
    original = "这段原句具有独特而不可复用的表达路径。"
    ledger = {
        "claims": [
            {
                "claim_id": "C001",
                "summary": "作者认为表达路径不应复用",
                "required_tokens": [],
            }
        ],
        "chronology": ["C001"],
        "causal_links": [],
        "paragraph_purposes": [],
    }

    prompt = review.render_reauthor_prompt(
        ledger,
        protected_map="（无）",
        previous_context="",
        next_context="",
        content_type="article",
        strategy_rules="重新组织论证顺序。",
        source_chars=len(original),
        retry_feedback="首次重著。",
    )

    assert original not in prompt
    assert "作者认为表达路径不应复用" in prompt
    assert "AIGC 值" not in prompt
    assert "人工特征" not in prompt


def test_fact_ledger_requires_every_keep_token_exactly_once() -> None:
    valid = json.dumps(
        {
            "claims": [
                {
                    "claim_id": "C001",
                    "summary": "金额事实",
                    "required_tokens": ["[[KEEP_0001]]"],
                }
            ],
            "chronology": ["C001"],
        },
        ensure_ascii=False,
    )
    assert review.parse_fact_ledger(valid, required_tokens=["[[KEEP_0001]]"])["claims"]

    duplicate = valid.replace(
        '"required_tokens": ["[[KEEP_0001]]"]',
        '"required_tokens": ["[[KEEP_0001]]", "[[KEEP_0001]]"]',
    )
    with pytest.raises(ValueError, match="逐一保留"):
        review.parse_fact_ledger(duplicate, required_tokens=["[[KEEP_0001]]"])


def test_fact_ledger_rejects_full_source_sentence_copy() -> None:
    sentence = "这一整句原文包含足够多的独特措辞，因此不能被事实账本原封不动地搬运进去。"
    copied = {
        "claims": [
            {"claim_id": "C001", "summary": sentence, "required_tokens": []},
        ],
        "chronology": ["C001"],
    }
    abstracted = {
        "claims": [
            {"claim_id": "C001", "summary": "禁止照搬原句措辞", "required_tokens": []},
        ],
        "chronology": ["C001"],
    }

    assert review.fact_ledger_copying_reasons(sentence, copied)
    assert review.fact_ledger_copying_reasons(sentence, abstracted) == []


def test_large_region_is_audited_in_bounded_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(review.settings, "AIGC_REVIEW_AUDIT_BATCH_UNITS", 3)
    original = "".join("原稿句子保持事实关系并提供必要限定。" for _ in range(180))
    segment = ZhuqueSegment(
        1, 100, len(original), 0.99, "ai", 0, len(original), original, 1.0, True
    )
    audit_calls: list[str] = []

    def rewrite_fn(_prompt: str, **kwargs):
        source = kwargs["segment"].mapped_text
        return "改" * (len(source) - 1) + "。"

    def audit_fn(prompt: str, **_kwargs):
        audit_calls.append(prompt)
        return _passing_audit_json()

    result = optimize_report_segments(
        original,
        [segment],
        [1],
        rewrite_fn=rewrite_fn,
        audit_fn=audit_fn,
    )

    assert result["status"] == "candidate_ready"
    assert result["editorial_unit_count"] > 3
    assert result["audit_batch_count"] == len(audit_calls)
    assert result["audit_batch_count"] == math.ceil(result["editorial_unit_count"] / 3)


def test_quality_audit_rejects_rubber_stamp_and_contradictory_reason() -> None:
    payload = json.loads(_passing_audit_json())
    payload["reason"] = "候选保留原有骨架，但仍然通过。"
    assert quality_audit_passed(payload) is False

    payload = json.loads(_passing_audit_json())
    payload["issues"] = [
        {
            "segment_id": "1.1",
            "type": "added_fact",
            "original_claim": "原文没有该判断",
            "candidate_change": "候选新增判断",
            "severity": "high",
        }
    ]
    assert quality_audit_passed(payload) is False


def test_calibration_result_requires_exact_hash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(review, "_CALIBRATION_DIR", tmp_path)
    sample = review.create_calibration_sample(
        user_id="user-1",
        text="这是一份用于隔离校准的完整正文。" * 30,
        content_type="article",
        source_group="group-1",
        variant_type="original",
    )

    with pytest.raises(review.ZhuqueReportError, match="哈希不一致"):
        review.record_calibration_result(
            user_id="user-1",
            sample_id=sample["sample_id"],
            submitted_text_sha256="0" * 64,
            aigc_value=0.4,
        )

    labeled = review.record_calibration_result(
        user_id="user-1",
        sample_id=sample["sample_id"],
        submitted_text_sha256=sample["text_sha256"],
        aigc_value=0.4,
    )
    assert labeled["label"] == "human"
    assert labeled["learning_eligible"] is False
    assert labeled["verification_mode"] == "manual_value"
    assert review.list_calibration_samples(user_id="user-1")[0]["sample_id"] == sample["sample_id"]

    verified = review.record_calibration_report_result(
        user_id="user-1",
        sample_id=sample["sample_id"],
        parsed_report={
            "original_sha256": sample["text_sha256"],
            "source_match_verified": True,
            "human_percent": 60.0,
            "suspected_percent": 40.0,
            "ai_percent": 0.0,
            "report_sha256": "report-hash",
            "segments": [
                {"share_percent": 100.0, "aigc_value": 0.4},
            ],
        },
    )
    assert verified["green"] is True
    assert verified["learning_eligible"] is True
    assert verified["verification_mode"] == "pdf_exact_hash"
