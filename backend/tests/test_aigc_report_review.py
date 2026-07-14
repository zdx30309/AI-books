from __future__ import annotations

import json

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


def test_multipart_crlf_and_json_lf_share_one_canonical_hash() -> None:
    multipart_text = "第一段。\r\n\r\n第二段包含260元。\r\n"
    json_text = "第一段。\n\n第二段包含260元。"

    assert normalize_manuscript_text(multipart_text) == json_text
    assert sha256_text(multipart_text) == sha256_text(json_text)


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
                "failed_segment_ids": [],
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
    assert compared["recommended_next_strategy"] == "evidence_first"
    assert compared["historical_strategy_summary"][0]["experiment_count"] == 1
    assert compared["historical_feature_summary"] == []
    assert (tmp_path / f"{compared['comparison_id']}.json").exists()
