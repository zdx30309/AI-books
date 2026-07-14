"""Local, zero-API-cost style measurements for Zhuque black-box experiments.

These features do not pretend to reproduce Tencent's proprietary model.  They
turn each real re-check into structured evidence about which observable changes
coincide with a lower Zhuque score.  Only numeric profiles are persisted.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from statistics import mean, pstdev
from typing import Any, Iterable

STYLE_FEATURE_VERSION = "style-probe-v1"

_DISCOURSE_MARKERS = (
    "首先",
    "其次",
    "此外",
    "因此",
    "所以",
    "与此同时",
    "总之",
    "综上",
    "值得注意的是",
    "可以看出",
    "换言之",
    "从某种意义上说",
)
_PUNCTUATION = "，。；：！？、"


def profile_style(text: str) -> dict[str, float]:
    value = text or ""
    compact = re.sub(r"\s+", "", value)
    sentences = [
        item.strip()
        for item in re.split(r"(?<=[。！？!?])", value)
        if re.search(r"[\u3400-\u9fffA-Za-z0-9]", item)
    ]
    sentence_lengths = [len(re.sub(r"\s+", "", item)) for item in sentences]
    paragraphs = [
        item.strip()
        for item in re.split(r"\n\s*\n|\n", value)
        if item.strip()
    ]
    paragraph_lengths = [len(re.sub(r"\s+", "", item)) for item in paragraphs]
    cjk = "".join(re.findall(r"[\u3400-\u9fff]", compact))
    punctuation_counts = Counter(char for char in compact if char in _PUNCTUATION)
    punctuation_total = sum(punctuation_counts.values())
    sentence_end_count = sum(compact.count(char) for char in "。！？!?")
    marker_count = sum(compact.count(marker) for marker in _DISCOURSE_MARKERS)
    repeated_four_gram_ratio = _repeated_ngram_ratio(cjk, 4)
    return {
        "mean_sentence_chars": _safe_mean(sentence_lengths),
        "sentence_length_cv": _coefficient_of_variation(sentence_lengths),
        "short_sentence_ratio": _ratio(sum(item <= 12 for item in sentence_lengths), len(sentence_lengths)),
        "long_sentence_ratio": _ratio(sum(item >= 40 for item in sentence_lengths), len(sentence_lengths)),
        "paragraph_length_cv": _coefficient_of_variation(paragraph_lengths),
        "comma_per_sentence_end": _ratio(compact.count("，"), sentence_end_count),
        "semicolon_per_1000_chars": _per_1000(compact.count("；"), len(compact)),
        "colon_per_1000_chars": _per_1000(compact.count("："), len(compact)),
        "discourse_markers_per_1000_chars": _per_1000(marker_count, len(compact)),
        "punctuation_entropy": _entropy(punctuation_counts, punctuation_total),
        "repeated_four_gram_ratio": repeated_four_gram_ratio,
        "unique_cjk_ratio": _ratio(len(set(cjk)), len(cjk)),
    }


def style_feature_delta(original: str, candidate: str) -> dict[str, float]:
    before = profile_style(original)
    after = profile_style(candidate)
    return {
        key: round(after[key] - before[key], 6)
        for key in before
    }


def summarize_feature_effects(
    records: Iterable[dict[str, Any]],
    *,
    min_samples: int = 5,
) -> list[dict[str, Any]]:
    """Return correlations only after enough real paired Zhuque observations exist."""
    grouped: dict[str, list[tuple[float, float]]] = {}
    for record in records:
        try:
            score_delta = float(record["score_delta"])
        except (KeyError, TypeError, ValueError):
            continue
        for feature, raw_delta in (record.get("style_feature_delta") or {}).items():
            try:
                feature_delta = float(raw_delta)
            except (TypeError, ValueError):
                continue
            if math.isfinite(feature_delta) and math.isfinite(score_delta):
                grouped.setdefault(str(feature), []).append((feature_delta, score_delta))
    summaries: list[dict[str, Any]] = []
    for feature, values in grouped.items():
        if len(values) < min_samples:
            continue
        feature_values = [item[0] for item in values]
        score_values = [item[1] for item in values]
        correlation = _pearson(feature_values, score_values)
        if correlation is None:
            continue
        increased = [score for delta, score in values if delta > 0]
        decreased = [score for delta, score in values if delta < 0]
        summaries.append(
            {
                "feature": feature,
                "sample_count": len(values),
                "pearson_correlation": round(correlation, 6),
                "mean_score_delta_when_feature_increased": round(_safe_mean(increased), 6),
                "mean_score_delta_when_feature_decreased": round(_safe_mean(decreased), 6),
                "evidence_grade": _evidence_grade(len(values)),
            }
        )
    return sorted(
        summaries,
        key=lambda item: (-abs(float(item["pearson_correlation"])), item["feature"]),
    )


def _safe_mean(values: list[float] | list[int]) -> float:
    return round(float(mean(values)), 6) if values else 0.0


def _coefficient_of_variation(values: list[int]) -> float:
    if not values:
        return 0.0
    average = mean(values)
    return round(float(pstdev(values) / average), 6) if average else 0.0


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _per_1000(count: int, length: int) -> float:
    return round(count * 1000 / length, 6) if length else 0.0


def _entropy(counts: Counter[str], total: int) -> float:
    if total <= 0:
        return 0.0
    return round(
        -sum((count / total) * math.log2(count / total) for count in counts.values()),
        6,
    )


def _repeated_ngram_ratio(text: str, size: int) -> float:
    if len(text) < size:
        return 0.0
    counts = Counter(text[index:index + size] for index in range(len(text) - size + 1))
    repeated = sum(count - 1 for count in counts.values() if count > 1)
    return round(repeated / max(1, sum(counts.values())), 6)


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = mean(left)
    right_mean = mean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right, strict=True))
    left_variance = sum((x - left_mean) ** 2 for x in left)
    right_variance = sum((y - right_mean) ** 2 for y in right)
    denominator = math.sqrt(left_variance * right_variance)
    return numerator / denominator if denominator else None


def _evidence_grade(sample_count: int) -> str:
    if sample_count >= 30:
        return "local_surrogate_ready"
    if sample_count >= 15:
        return "directional"
    return "exploratory"
