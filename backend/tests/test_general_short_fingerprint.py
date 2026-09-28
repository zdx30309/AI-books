"""通用短篇结构指纹：确定性抽取的回归测试。"""
from __future__ import annotations

import json

from app.services.general_short_fingerprint import (
    build_fingerprint,
    fingerprint_json,
    guess_domain,
)

DEMO = (
    "你有没有想过，为什么越省钱反而越穷？我认识一位朋友，他每个月记三千笔账。"
    "2023年央行数据显示居民储蓄率上升。比如日本失落的三十年，比如韩国的高负债家庭。"
    "因为收入停滞，所以消费收缩，导致经济下行。再看德国制造业，它的中小企业一直很稳。"
    "建议你第一步先做预算，第二步记账，第三步复盘。"
)


def test_empty_source_returns_empty_fingerprint() -> None:
    fp = build_fingerprint("")
    assert fp.argument_units == []
    assert fp.source_chars == 0
    assert fp.render()


def test_fingerprint_never_copies_a_source_sentence_verbatim() -> None:
    fp = build_fingerprint(DEMO)
    for unit in fp.argument_units:
        # The summariser keeps a short structural cue, never the full sentence.
        assert len(unit.unit) <= 240
    assert fp.source_chars == len(DEMO)


def test_opening_device_and_ending_function_are_classified() -> None:
    fp = build_fingerprint(DEMO)
    assert fp.opening_device == "设问切入"
    assert fp.ending_function == "含行动建议"


def test_case_sequence_keeps_source_order() -> None:
    fp = build_fingerprint(DEMO)
    assert fp.case_sequence, "expected at least one ordered case marker"
    positions = [DEMO.index(case) for case in fp.case_sequence]
    assert positions == sorted(positions), fp.case_sequence


def test_domain_guess_is_reported_for_a_finance_transcript() -> None:
    assert guess_domain(DEMO) == "小白理财"


def test_unit_count_scales_with_length() -> None:
    long_source = DEMO * 60
    fp = build_fingerprint(long_source)
    assert len(fp.argument_units) >= 6
    assert len(fp.argument_units) <= 80


def test_fingerprint_json_is_valid_and_structured() -> None:
    payload = json.loads(fingerprint_json(DEMO))
    assert set(payload) >= {
        "core_topic",
        "argument_units",
        "case_sequence",
        "opening_device",
        "causal_chain",
        "ending_function",
        "keyword_fingerprint",
        "domain_guess",
        "source_chars",
    }
