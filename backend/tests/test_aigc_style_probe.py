from __future__ import annotations

from app.services.aigc_style_probe import (
    profile_style,
    style_feature_delta,
    summarize_feature_effects,
)


def test_style_probe_measures_observable_content_format_and_statistics() -> None:
    original = "首先说明背景，因此得到结论。\n\n其次补充材料，总之再次收束。"
    candidate = "背景先摆在这里。材料跟上来以后，原有结论才显出来。"

    profile = profile_style(original)
    delta = style_feature_delta(original, candidate)

    assert set(profile) == set(delta)
    assert delta["discourse_markers_per_1000_chars"] < 0
    assert profile["punctuation_entropy"] >= 0
    assert profile["sentence_length_cv"] >= 0


def test_feature_learning_refuses_to_invent_rule_before_five_real_pairs() -> None:
    records = [
        {"score_delta": -0.01, "style_feature_delta": {"sentence_length_cv": 0.1}}
        for _ in range(4)
    ]

    assert summarize_feature_effects(records, min_samples=5) == []


def test_feature_learning_exposes_directional_correlation_after_real_pairs() -> None:
    records = [
        {
            "score_delta": score_delta,
            "style_feature_delta": {"sentence_length_cv": feature_delta},
        }
        for feature_delta, score_delta in [
            (-0.3, 0.04),
            (-0.2, 0.03),
            (0.1, -0.01),
            (0.2, -0.03),
            (0.4, -0.05),
        ]
    ]

    summary = summarize_feature_effects(records, min_samples=5)

    assert len(summary) == 1
    assert summary[0]["feature"] == "sentence_length_cv"
    assert summary[0]["sample_count"] == 5
    assert summary[0]["pearson_correlation"] < -0.9
    assert summary[0]["evidence_grade"] == "exploratory"
