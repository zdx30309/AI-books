"""通用短篇：大模型审查层与合并规则测试。"""
from __future__ import annotations

import json

from app.services.general_short_llm_audit import (
    LlmAuditUnavailable,
    audit_one_source_with_model,
    merge_with_deterministic,
    run_llm_audit,
)
from app.services.general_short_similarity import audit_one_source

FAKE = json.dumps({
    "structure": {
        "units": [
            {"id": 1, "source_unit": "储蓄率上升", "draft_location": "第1章", "level": "完整"},
            {"id": 2, "source_unit": "消费收缩", "draft_location": "无", "level": "无"},
        ],
        "A_unit_match": 50, "B_case_order": 85, "C_narrative_spine": 75, "S": 80,
    },
    "material": {"data_reuse": 70, "case_reuse": 60, "named_entity_reuse": 50,
                 "literal_reuse": 40, "M": 55},
    "composite": 70,
    "verdict": "高危",
    "differentiation_list": ["独立章节X"],
    "fix_directive": "改骨架",
    "notes": "ok",
}, ensure_ascii=False)


def _fake(_prompt: str) -> str:
    return FAKE


def test_llm_audit_parses_the_frozen_rubric() -> None:
    # M is A*0.30 + B*0.25 + C*0.20 + D*0.25, where A/B/C come from the model
    # and D comes from the deterministic literal table (appendix six).
    r = run_llm_audit("src", "draft", caller=_fake, literal_grade_d=50.0)
    assert r.ran is True
    assert r.structure_score == 80.0
    # 70*0.30 + 60*0.25 + 50*0.20 + 50*0.25 = 21 + 15 + 10 + 12.5 = 58.5
    assert r.material_score == 58.5, r.material_score
    assert r.composite == 71.4, r.composite
    assert len(r.unit_map) == 2
    assert r.differentiation_list == ["独立章节X"]


def test_merge_takes_the_higher_score_so_a_model_miss_cannot_lower_risk() -> None:
    base = audit_one_source("s", "甲" * 300, "乙" * 300)
    llm = run_llm_audit("s", "d", caller=_fake, literal_grade_d=50.0)
    merged = merge_with_deterministic(base, llm)
    assert merged.composite >= llm.composite
    assert merged.structure.structure_score >= llm.structure_score
    assert merged.verdict == "高危"


def test_merge_uses_the_model_unit_map_as_evidence() -> None:
    base = audit_one_source("s", "甲" * 300, "乙" * 300)
    llm = run_llm_audit("s", "d", caller=_fake, literal_grade_d=50.0)
    merged = merge_with_deterministic(base, llm)
    assert merged.structure.unit_total == 2
    assert merged.structure.unit_full == 1
    assert merged.structure.unit_none == 1
    assert merged.unit_none_count == 1


def test_model_layer_can_be_disabled() -> None:
    base, llm = audit_one_source_with_model("s", "甲" * 200, "乙" * 200, use_model=False)
    assert llm.ran is False
    assert base.composite >= 0


def test_model_failure_degrades_instead_of_blocking() -> None:
    def boom(_prompt: str) -> str:
        raise RuntimeError("provider down")

    base, llm = audit_one_source_with_model("s", "甲" * 200, "乙" * 200, caller=boom)
    assert llm.ran is False
    assert base.composite >= 0  # deterministic layer still produced a report


def test_non_json_reply_is_rejected() -> None:
    try:
        run_llm_audit("s", "d", caller=lambda _p: "这不是 JSON")
    except Exception as exc:
        assert "JSON" in str(exc) or isinstance(exc, (ValueError, LlmAuditUnavailable))
    else:
        raise AssertionError("应当拒绝非 JSON 回复")
