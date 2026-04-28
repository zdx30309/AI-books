from __future__ import annotations

import time
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.quality_metrics import measure_quality


def test_empty_string_all_zero() -> None:
    report = measure_quality("")
    assert report.total_chars == 0
    assert report.total_paragraphs == 0
    assert report.total_violations == 0
    assert report.violation_density_per_kchar == 0.0


def test_a1_pattern_hit() -> None:
    report = measure_quality("这不是简单问题，而是结构性的。")
    assert report.a1_pattern_hits.get("不是X而是Y")


def test_a1_fixed_hit() -> None:
    report = measure_quality("他用力托住那块石头。")
    assert report.a1_fixed_hits["托住"]


def test_ellipsis_compatibility() -> None:
    texts = [
        "新的秩序逐渐长出来。",
        "新的秩序…长出来。",
        "新的秩序……长出来。",
        "新的秩序...长出来。",
    ]
    for text in texts:
        report = measure_quality(text)
        assert report.a1_pattern_hits["X长出来"]


def test_a2_hits() -> None:
    report = measure_quality("综上所述，不可否认这件事。")
    assert report.a2_closing_hits["综上所述"]
    assert report.a2_empty_modifier_hits["不可否认"]


def test_llm_residual_and_meta_tags() -> None:
    report = measure_quality("请随时告诉我。<audit></audit></final_text>")
    assert report.llm_residual_hits["请随时告诉我"]
    assert report.llm_residual_hits["<audit>"]
    assert report.llm_residual_hits["</audit>"]
    assert report.llm_residual_hits["</final_text>"]


def test_chicken_soup_tail_sensitive() -> None:
    middle = "前面内容。" + ("中段文字。" * 80) + "未来可期。"
    report_mid = measure_quality(middle)
    assert "未来可期" not in report_mid.a2_chicken_soup_hits

    tail = "前面内容。" + ("中段文字。" * 10) + ("尾部内容。" * 10) + "未来可期。"
    report_tail = measure_quality(tail)
    assert report_tail.a2_chicken_soup_hits["未来可期"]


def test_transition_sentence_start_sensitive() -> None:
    report_start = measure_quality("与此同时，我们开始行动。")
    assert report_start.a2_transition_hits["与此同时(段首)"]

    report_mid = measure_quality("前文铺垫。与此同时，我们开始行动。")
    assert "与此同时(段首)" not in report_mid.a2_transition_hits


def test_half_width_punct_and_marks() -> None:
    report = measure_quality("这是一段中文,带半角逗号。\n！！这是强调。")
    assert report.half_width_punct_count >= 1
    assert report.mark_double_excl == 1


def test_decimal_not_counted() -> None:
    report = measure_quality("GDP 增长 3.5% 。")
    assert report.half_width_punct_count == 0


def test_performance_smoke() -> None:
    text = ("这是一个中文段落，没有太多违规内容。" * 2000)
    start = time.time()
    report = measure_quality(text)
    elapsed = time.time() - start
    assert report.total_chars == len(text)
    assert elapsed < 1.0
