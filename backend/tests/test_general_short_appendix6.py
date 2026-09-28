"""附录六原文规则：逐条回归测试（以原文口径为准）。"""
from __future__ import annotations

from pathlib import Path

from app.services.general_short_similarity import (
    GRAM_SIZES,
    HIGH_RISK_THRESHOLD,
    MIN_DRAFT_SENTENCE_CHARS,
    MIN_SOURCE_SENTENCE_CHARS,
    literal_reuse_grade,
    literal_grade_from_table,
    measure_literal,
    sentences,
)

BUNDLE = Path(__file__).resolve().parents[1] / "app" / "prompts" / "general_short"


def test_llm_audit_prompt_is_the_verbatim_appendix_six() -> None:
    text = (BUNDLE / "stages" / "llm_audit.md").read_text(encoding="utf-8")
    # Every part of the original instruction must be present verbatim.
    for marker in ("第零部分", "第一部分", "第二部分", "第三部分",
                   "第四部分", "第五部分", "第六部分", "第七部分",
                   "附录 A", "附录 B", "附录 C"):
        assert marker in text, marker
    # The frozen scoring rules themselves.
    assert "S × 0.60 ＋ M × 0.40" in text or "S × 0.60 + M × 0.40" in text
    assert "autojunk=False" in text
    assert "6、8、10、15、20" in text
    assert "结构框架相似度 ≥ 70%" in text


def test_gram_sizes_match_the_original_six_eight_ten_fifteen_twenty() -> None:
    assert GRAM_SIZES == (6, 8, 10, 15, 20)


def test_sentence_length_floors_match_the_original() -> None:
    assert MIN_DRAFT_SENTENCE_CHARS == 15
    assert MIN_SOURCE_SENTENCE_CHARS == 10


def test_sentence_filter_applies_the_floor() -> None:
    text = "短句。这是一个足够长的新稿句子用来通过十五字门槛。"
    assert len(sentences(text)) == 2
    assert len(sentences(text, min_chars=15)) == 1


def test_d_conversion_table_row_one() -> None:
    # 共有 10-gram 占比为 0，最长连续片段小于 12 字，句级占比低于 2% -> 5
    assert literal_reuse_grade(gram10_overlap=0.0, longest_common_chars=11,
                               high_sentence_ratio=0.019) == 5.0


def test_d_conversion_table_row_three() -> None:
    # 最长连续片段 12—29 字 -> 50
    assert literal_reuse_grade(gram10_overlap=0.01, longest_common_chars=12,
                               high_sentence_ratio=0.0) == 50.0
    assert literal_reuse_grade(gram10_overlap=0.01, longest_common_chars=29,
                               high_sentence_ratio=0.0) == 50.0


def test_d_conversion_table_row_four() -> None:
    # 最长连续片段 30—99 字 -> 75
    assert literal_reuse_grade(gram10_overlap=0.02, longest_common_chars=30,
                               high_sentence_ratio=0.0) == 75.0
    assert literal_reuse_grade(gram10_overlap=0.02, longest_common_chars=99,
                               high_sentence_ratio=0.0) == 75.0


def test_d_conversion_table_row_five() -> None:
    # 整段搬用 >= 100 字，或句级占比 > 25% -> 100
    assert literal_reuse_grade(gram10_overlap=0.05, longest_common_chars=100,
                               high_sentence_ratio=0.0) == 100.0
    assert literal_reuse_grade(gram10_overlap=0.0, longest_common_chars=0,
                               high_sentence_ratio=0.26) == 100.0


def test_d_grade_is_populated_on_measurement() -> None:
    layer = measure_literal("甲" * 300, "乙" * 300)
    assert layer.literal_grade_d == 5.0  # no overlap at all


def test_copy_short_circuits_at_hundred() -> None:
    layer = measure_literal("同样的文本内容" * 40, "同样的文本内容" * 40)
    assert layer.is_copy is True
    assert layer.literal_grade_d == 100.0


def test_thresholds_are_the_original_ones() -> None:
    assert HIGH_RISK_THRESHOLD == 50.0
