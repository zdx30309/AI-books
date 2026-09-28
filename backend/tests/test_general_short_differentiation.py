"""通用短篇差异化审查：阈值与打分的回归测试。"""
from __future__ import annotations

from app.services.general_short_differentiation import (
    HIGH_RISK_THRESHOLD,
    composite_score,
    evaluate_differentiation,
    longest_common_run,
    measure_literal,
)

SOURCE = (
    "你有没有想过，为什么越省钱反而越穷？我认识一位朋友，他每个月记三千笔账。"
    "2023年央行数据显示居民储蓄率上升。比如日本失落的三十年，比如韩国的高负债家庭。"
    "因为收入停滞，所以消费收缩，导致经济下行。再看德国制造业，它的中小企业一直很稳。"
    "建议你第一步先做预算，第二步记账，第三步复盘。"
) * 8


def test_threshold_is_frozen_at_fifty() -> None:
    assert HIGH_RISK_THRESHOLD == 50.0


def test_composite_weights_match_the_spec() -> None:
    assert composite_score(100.0, 0.0) == 60.0
    assert composite_score(0.0, 100.0) == 40.0
    assert composite_score(50.0, 50.0) == 50.0


def test_longest_common_run_detects_a_verbatim_span() -> None:
    size, sample = longest_common_run("abcdefghij", "xxabcdefghijyy")
    assert size == 10
    assert sample == "abcdefghij"


def test_literal_layer_counts_verbatim_reuse() -> None:
    literal = measure_literal(SOURCE, SOURCE)
    assert literal.longest_common_chars >= 100
    assert literal.max_sentence_similarity > 0.95


def test_verbatim_copy_is_blocked() -> None:
    report = evaluate_differentiation(SOURCE, SOURCE)
    assert report.blocked is True
    assert report.verdict == "高危"
    assert report.composite >= HIGH_RISK_THRESHOLD
    assert report.fix_directive


def test_independent_draft_on_a_different_axis_is_not_blocked() -> None:
    draft = (
        "@最终书名 测试书名\n@最终简介 测试简介\n@目录\n1. 第一条\n@导言\n导言正文。\n@正文\n"
        "1. 第一条\n"
        "先说一个完全不同的切入点：家庭账本的记录方式会改变人对消费的判断。"
        "举个不相关的例子，滑雪教练会根据雪况调整教学节奏。"
        "这里没有任何来自原稿的案例、术语或因果链。"
    )
    report = evaluate_differentiation(SOURCE, draft)
    assert report.composite < HIGH_RISK_THRESHOLD
    assert report.verdict == "安全"


def test_report_always_ships_a_differentiation_list() -> None:
    report = evaluate_differentiation(SOURCE, "完全无关的短稿。")
    assert isinstance(report.differentiation_list, list)
    payload = report.to_dict()
    for key in ("literal", "structure", "composite", "verdict", "blocked", "fix_directive"):
        assert key in payload
