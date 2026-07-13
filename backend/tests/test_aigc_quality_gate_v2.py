from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.core.config import settings
from app.services.aigc_quality_gate import (
    AigcDetection,
    AigcManualReviewRequired,
    AigcTechnicalError,
    enforce_aigc_quality_gate,
)


def _enable_v2(monkeypatch: pytest.MonkeyPatch, *, rounds: int = 2) -> None:
    monkeypatch.setattr(settings, "AIGC_GATE_ENABLED", True)
    monkeypatch.setattr(settings, "AIGC_GATE_FAIL_CLOSED", True)
    monkeypatch.setattr(settings, "AIGC_GATE_THRESHOLD", 50)
    monkeypatch.setattr(settings, "AIGC_GATE_MAX_REWRITE_ROUNDS", rounds)
    monkeypatch.setattr(settings, "AIGC_GATE_CHUNK_MIN_CHARS", 80)
    monkeypatch.setattr(settings, "AIGC_GATE_CHUNK_MAX_CHARS", 500)
    monkeypatch.setattr(settings, "TENCENTCLOUD_SECRET_ID", "test-id")
    monkeypatch.setattr(settings, "TENCENTCLOUD_SECRET_KEY", "test-key")
    monkeypatch.setattr(settings, "TENCENT_AIGC_BIZ_TYPE", "test-biz")


def _source() -> str:
    return (
        "原始风险段保留了全部事实，只是句间节奏过分整齐。编辑可以调整承接方式和语序，"
        "但不得添加新事实、删掉原信息、改变立场，也不能改变故事事件。"
        "这一段还需要保持原来的声音和自然段位置，最终只接受质量审稿与朱雀同时通过的版本。"
    )


def _review(*, fidelity: int = 99, completeness: int = 99, fluency: int = 95, voice: int = 95):
    return {
        "semanticFidelity": fidelity,
        "informationCompleteness": completeness,
        "fluency": fluency,
        "voiceContinuity": voice,
        "noAddedFacts": True,
        "noRemovedInformation": True,
        "noStanceChange": True,
        "noPlotOrEventChange": True,
        "reason": "test review",
    }


class _RuleDetector:
    def __init__(self, rule):
        self.rule = rule
        self.calls: list[str] = []

    def detect(self, text: str, *, data_id: str) -> AigcDetection:
        self.calls.append(text)
        score, suggestion = self.rule(text)
        return AigcDetection(-1, score, suggestion, request_id=data_id)


def test_score_50_is_rejected_even_when_suggestion_is_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_v2(monkeypatch, rounds=1)
    detector = _RuleDetector(lambda _text: (50, "Pass"))

    async def unchanged(seed: str, *_args, **_kwargs) -> str:
        return seed

    with pytest.raises(AigcManualReviewRequired) as raised:
        asyncio.run(
            enforce_aigc_quality_gate(
                _source(),
                category="social_science",
                detector=detector,
                candidate_generators={"gemini": unchanged, "openai": unchanged},
                quality_auditors={"openai": lambda *_a, **_k: _review(), "gemini": lambda *_a, **_k: _review()},
            )
        )
    assert raised.value.result is not None
    assert raised.value.result.status == "manual_review"
    assert raised.value.result.report["thresholdExclusive"] is True


def test_review_suggestion_is_never_auto_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_v2(monkeypatch, rounds=1)
    detector = _RuleDetector(lambda _text: (18, "Review"))

    async def unchanged(seed: str, *_args, **_kwargs) -> str:
        return seed

    with pytest.raises(AigcManualReviewRequired):
        asyncio.run(
            enforce_aigc_quality_gate(
                _source(),
                category="social_science",
                detector=detector,
                candidate_generators={"gemini": unchanged, "openai": unchanged},
                quality_auditors={"openai": lambda *_a, **_k: _review(), "gemini": lambda *_a, **_k: _review()},
            )
        )


def test_lazy_dual_model_path_stops_after_first_double_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_v2(monkeypatch)
    detector = _RuleDetector(lambda text: (82, "Block") if "原始风险段" in text else (22, "Pass"))
    calls: list[str] = []

    async def gemini(_seed: str, *_args, **kwargs) -> str:
        calls.append("gemini_generate")
        return str(kwargs["original_chunk"]).replace("原始风险段", "自然表达段")

    async def openai_generator(*_args, **_kwargs) -> str:
        raise AssertionError("first candidate passed; OpenAI generation would waste API spend")

    async def openai_audit(*_args, **_kwargs):
        calls.append("openai_audit")
        return _review()

    result = asyncio.run(
        enforce_aigc_quality_gate(
            _source(),
            category="social_science",
            detector=detector,
            candidate_generators={"gemini": gemini, "openai": openai_generator},
            quality_auditors={"openai": openai_audit},
        )
    )
    assert result.passed is True
    assert calls == ["gemini_generate", "openai_audit"]
    assert result.report["costSummary"]["modelCalls"] == 2


def test_cross_review_falls_back_to_second_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_v2(monkeypatch)
    detector = _RuleDetector(lambda text: (82, "Block") if "原始风险段" in text else (20, "Pass"))
    calls: list[str] = []

    async def gemini(_seed: str, *_args, **kwargs) -> str:
        calls.append("gemini_generate")
        return str(kwargs["original_chunk"]).replace("原始风险段", "第一候选段")

    async def openai(_seed: str, *_args, **kwargs) -> str:
        calls.append("openai_generate")
        return str(kwargs["original_chunk"]).replace("原始风险段", "第二候选段")

    async def openai_audit(*_args, **_kwargs):
        calls.append("openai_audit")
        return _review(fidelity=94)

    async def gemini_audit(*_args, **_kwargs):
        calls.append("gemini_audit")
        return _review()

    result = asyncio.run(
        enforce_aigc_quality_gate(
            _source(),
            category="social_science",
            detector=detector,
            candidate_generators={"gemini": gemini, "openai": openai},
            quality_auditors={"openai": openai_audit, "gemini": gemini_audit},
        )
    )
    assert result.passed is True
    assert "第二候选段" in result.text
    assert calls == ["gemini_generate", "openai_audit", "openai_generate", "gemini_audit"]


def test_second_round_uses_best_safe_seed_and_still_compares_to_original(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_v2(monkeypatch, rounds=2)
    detector = _RuleDetector(
        lambda text: (82, "Block")
        if "原始风险段" in text
        else (63, "Review")
        if "第一轮" in text
        else (19, "Pass")
    )
    rounds_seen: list[tuple[str, int]] = []

    async def candidate(_seed: str, *_args, **kwargs) -> str:
        provider = "gemini" if len(rounds_seen) % 2 == 0 else "openai"
        round_number = int(kwargs["round_number"])
        rounds_seen.append((provider, round_number))
        marker = "第一轮" if round_number == 1 else "第二轮"
        return str(kwargs["original_chunk"]).replace("原始风险段", marker)

    async def audit(*_args, **_kwargs):
        return _review()

    result = asyncio.run(
        enforce_aigc_quality_gate(
            _source(),
            category="social_science",
            detector=detector,
            candidate_generators={"gemini": candidate, "openai": candidate},
            quality_auditors={"openai": audit, "gemini": audit},
        )
    )
    assert result.passed is True
    assert result.rewrite_rounds == 2
    assert "第二轮" in result.text
    assert [round_number for _, round_number in rounds_seen] == [1, 1, 2]


def test_repeated_anchor_count_is_preserved_and_original_is_rolled_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _enable_v2(monkeypatch, rounds=1)
    source = _source() + " 合同写明260元，复核表再次写明260元，两处金额都必须保留。"
    detector = _RuleDetector(lambda text: (82, "Block") if text == source else (18, "Pass"))

    async def drops_one_anchor(_seed: str, *_args, **kwargs) -> str:
        original = str(kwargs["original_chunk"])
        return original.replace("复核表再次写明260元", "复核表再次写明该金额")

    with pytest.raises(AigcManualReviewRequired) as raised:
        asyncio.run(
            enforce_aigc_quality_gate(
                source,
                category="finance",
                detector=detector,
                output_dir=tmp_path,
                candidate_generators={"gemini": drops_one_anchor, "openai": drops_one_anchor},
            )
        )
    assert raised.value.result is not None
    assert raised.value.result.text == source
    assert not (tmp_path / "05_aigc_final.md").exists()
    report = json.loads((tmp_path / "aigc_quality_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "manual_review"
    assert "丢失不可变锚点" in report["manualReviewReason"]


def test_model_change_invalidates_pass_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _enable_v2(monkeypatch)
    source = _source().replace("原始风险段", "自然表达段")
    first_detector = _RuleDetector(lambda _text: (12, "Pass"))
    asyncio.run(
        enforce_aigc_quality_gate(
            source,
            category="social_science",
            detector=first_detector,
            output_dir=tmp_path,
        )
    )
    assert first_detector.calls

    monkeypatch.setattr(settings, "GEMINI_MODEL", f"{settings.GEMINI_MODEL}-changed")
    second_detector = _RuleDetector(lambda _text: (11, "Pass"))
    asyncio.run(
        enforce_aigc_quality_gate(
            source,
            category="social_science",
            detector=second_detector,
            output_dir=tmp_path,
        )
    )
    assert second_detector.calls, "model combination must be part of the cache key"


def test_model_failure_is_retryable_technical_failure_with_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _enable_v2(monkeypatch)
    detector = _RuleDetector(lambda text: (82, "Block") if "原始风险段" in text else (20, "Pass"))

    async def broken(*_args, **_kwargs) -> str:
        raise TimeoutError("provider timeout")

    with pytest.raises(AigcTechnicalError) as raised:
        asyncio.run(
            enforce_aigc_quality_gate(
                _source(),
                category="social_science",
                detector=detector,
                output_dir=tmp_path,
                candidate_generators={"gemini": broken},
            )
        )
    assert raised.value.report is not None
    assert raised.value.report["status"] == "technical_failed"
    assert (tmp_path / "aigc_quality_report.json").exists()
