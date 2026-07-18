"""Deterministic style measurements for Zhuque black-box experiments.

These measurements are observable proxies.  They do not reproduce Tencent's
proprietary detector and must never be presented as a predicted Zhuque score.
Only numeric profiles and comparisons are persisted; raw reference books are
not returned to generation prompts.
"""
from __future__ import annotations

import math
import re
import zlib
from collections import Counter
from statistics import mean, pstdev
from typing import Any, Iterable

STYLE_FEATURE_VERSION = "style-probe-v2"

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
    "由此可见",
    "不难发现",
    "毋庸置疑",
)
_PUNCTUATION = "，。；：！？、…—“”‘’（）《》"
_SENTENCE_END = "。！？!?"
_FIRST_PERSON_MARKERS = ("我", "我们", "笔者", "在我看来", "我的")
_SECOND_PERSON_MARKERS = ("你", "你们", "您", "读者")

# Count-like metrics are useful for display but unsuitable for distance.
STYLE_DISTANCE_FEATURES = (
    "mean_sentence_chars",
    "sentence_length_cv",
    "sentence_length_volatility",
    "short_sentence_ratio",
    "long_sentence_ratio",
    "mean_paragraph_chars",
    "paragraph_length_cv",
    "comma_per_sentence_end",
    "semicolon_per_1000_chars",
    "colon_per_1000_chars",
    "question_per_1000_chars",
    "exclamation_per_1000_chars",
    "quote_per_1000_chars",
    "discourse_markers_per_1000_chars",
    "first_person_per_1000_chars",
    "second_person_per_1000_chars",
    "punctuation_entropy",
    "character_entropy",
    "trigram_perplexity_proxy",
    "compression_ratio",
    "repeated_four_gram_ratio",
    "unique_cjk_ratio",
)

# A floor prevents a reference value near zero from dominating the distance.
_DISTANCE_FLOORS = {
    "mean_sentence_chars": 8.0,
    "sentence_length_cv": 0.20,
    "sentence_length_volatility": 6.0,
    "short_sentence_ratio": 0.08,
    "long_sentence_ratio": 0.08,
    "mean_paragraph_chars": 30.0,
    "paragraph_length_cv": 0.20,
    "comma_per_sentence_end": 0.50,
    "semicolon_per_1000_chars": 0.30,
    "colon_per_1000_chars": 0.30,
    "question_per_1000_chars": 0.30,
    "exclamation_per_1000_chars": 0.30,
    "quote_per_1000_chars": 0.50,
    "discourse_markers_per_1000_chars": 0.50,
    "first_person_per_1000_chars": 0.50,
    "second_person_per_1000_chars": 0.50,
    "punctuation_entropy": 0.50,
    "character_entropy": 1.0,
    "trigram_perplexity_proxy": 10.0,
    "compression_ratio": 0.10,
    "repeated_four_gram_ratio": 0.02,
    "unique_cjk_ratio": 0.02,
}


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
        if re.search(r"[\u3400-\u9fffA-Za-z0-9]", item)
    ]
    paragraph_lengths = [len(re.sub(r"\s+", "", item)) for item in paragraphs]
    cjk = "".join(re.findall(r"[\u3400-\u9fff]", compact))
    punctuation_counts = Counter(char for char in compact if char in _PUNCTUATION)
    punctuation_total = sum(punctuation_counts.values())
    sentence_end_count = sum(compact.count(char) for char in _SENTENCE_END)
    marker_count = sum(compact.count(marker) for marker in _DISCOURSE_MARKERS)
    first_person_count = sum(compact.count(marker) for marker in _FIRST_PERSON_MARKERS)
    second_person_count = sum(compact.count(marker) for marker in _SECOND_PERSON_MARKERS)
    character_counts = Counter(cjk)
    bigram_counts = _ngram_counts(cjk, 2)
    trigram_counts = _ngram_counts(cjk, 3)
    length_volatility = [
        abs(current - previous)
        for previous, current in zip(sentence_lengths, sentence_lengths[1:])
    ]
    bigram_entropy_raw = _entropy_raw(bigram_counts, sum(bigram_counts.values()))
    trigram_entropy_raw = _entropy_raw(trigram_counts, sum(trigram_counts.values()))
    return {
        "char_count": float(len(compact)),
        "cjk_char_count": float(len(cjk)),
        "sentence_count": float(len(sentence_lengths)),
        "paragraph_count": float(len(paragraph_lengths)),
        "mean_sentence_chars": _safe_mean(sentence_lengths),
        "sentence_length_std": _safe_pstdev(sentence_lengths),
        "sentence_length_cv": _coefficient_of_variation(sentence_lengths),
        "sentence_length_volatility": _safe_mean(length_volatility),
        "short_sentence_ratio": _ratio(sum(item <= 12 for item in sentence_lengths), len(sentence_lengths)),
        "long_sentence_ratio": _ratio(sum(item >= 40 for item in sentence_lengths), len(sentence_lengths)),
        "mean_paragraph_chars": _safe_mean(paragraph_lengths),
        "paragraph_length_cv": _coefficient_of_variation(paragraph_lengths),
        "comma_per_sentence_end": _ratio(compact.count("，"), sentence_end_count),
        "semicolon_per_1000_chars": _per_1000(compact.count("；"), len(compact)),
        "colon_per_1000_chars": _per_1000(compact.count("："), len(compact)),
        "question_per_1000_chars": _per_1000(compact.count("？") + compact.count("?"), len(compact)),
        "exclamation_per_1000_chars": _per_1000(compact.count("！") + compact.count("!"), len(compact)),
        "quote_per_1000_chars": _per_1000(
            sum(compact.count(char) for char in "“”‘’"),
            len(compact),
        ),
        "discourse_markers_per_1000_chars": _per_1000(marker_count, len(compact)),
        "first_person_per_1000_chars": _per_1000(first_person_count, len(compact)),
        "second_person_per_1000_chars": _per_1000(second_person_count, len(compact)),
        "punctuation_entropy": _entropy(punctuation_counts, punctuation_total),
        "character_entropy": _entropy(character_counts, len(cjk)),
        "bigram_entropy": round(bigram_entropy_raw, 6),
        "trigram_entropy": round(trigram_entropy_raw, 6),
        # Entropy-rate proxy H(X3 | X1,X2) ≈ H(trigram)-H(bigram).
        # This is less length-sensitive than raw trigram entropy, but it is
        # still a corpus statistic rather than model or Zhuque perplexity.
        "trigram_perplexity_proxy": _perplexity_from_entropy(
            max(0.0, trigram_entropy_raw - bigram_entropy_raw)
        ),
        "compression_ratio": _compression_ratio(compact),
        "repeated_four_gram_ratio": _repeated_ngram_ratio(cjk, 4),
        "unique_cjk_ratio": _ratio(len(set(cjk)), len(cjk)),
    }


def compare_style_profiles(
    reference: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Return an explainable profile distance; lower only means closer to reference."""
    features: list[dict[str, Any]] = []
    distances: list[float] = []
    for key in STYLE_DISTANCE_FEATURES:
        try:
            ref_value = float(reference.get(key, 0.0))
            candidate_value = float(candidate.get(key, 0.0))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(ref_value) or not math.isfinite(candidate_value):
            continue
        denominator = max(
            abs(ref_value),
            abs(candidate_value),
            float(_DISTANCE_FLOORS.get(key, 0.01)),
        )
        normalized_distance = min(1.0, abs(candidate_value - ref_value) / denominator)
        distances.append(normalized_distance)
        features.append(
            {
                "feature": key,
                "reference": round(ref_value, 6),
                "candidate": round(candidate_value, 6),
                "delta": round(candidate_value - ref_value, 6),
                "normalized_distance": round(normalized_distance, 6),
            }
        )
    features.sort(key=lambda item: (-float(item["normalized_distance"]), str(item["feature"])))
    return {
        "version": STYLE_FEATURE_VERSION,
        "overall_distance": round(_safe_mean(distances), 6),
        "feature_count": len(features),
        "largest_gaps": features[:10],
        "features": features,
        "disclaimer": "诊断距离只表示与参考书统计画像的接近程度，不是朱雀分数预测。",
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


def _safe_pstdev(values: list[int]) -> float:
    return round(float(pstdev(values)), 6) if values else 0.0


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
    return round(_entropy_raw(counts, total), 6)


def _entropy_raw(counts: Counter[str], total: int) -> float:
    if total <= 0:
        return 0.0
    return -sum((count / total) * math.log2(count / total) for count in counts.values())


def _perplexity_from_entropy(entropy: float) -> float:
    if entropy <= 0:
        return 0.0
    # Avoid an unhelpful overflow on malformed input.
    return round(float(2 ** min(entropy, 30.0)), 6)


def _ngram_counts(text: str, size: int) -> Counter[str]:
    if len(text) < size:
        return Counter()
    return Counter(text[index:index + size] for index in range(len(text) - size + 1))


def _compression_ratio(text: str) -> float:
    if not text:
        return 0.0
    raw = text.encode("utf-8", errors="ignore")
    if not raw:
        return 0.0
    return round(len(zlib.compress(raw, level=9)) / len(raw), 6)


def _repeated_ngram_ratio(text: str, size: int) -> float:
    counts = _ngram_counts(text, size)
    if not counts:
        return 0.0
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
