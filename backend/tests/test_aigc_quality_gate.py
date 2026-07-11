from __future__ import annotations

import json
import asyncio
from pathlib import Path

import pytest

from app.core.config import settings
from app.services.aigc_quality_gate import (
    AigcDetection,
    AigcQualityGateError,
    build_aigc_chunks,
    enforce_aigc_quality_gate,
)


class _FakeDetector:
    def detect(self, text: str, *, data_id: str) -> AigcDetection:
        high_risk = "整齐模板" in text
        return AigcDetection(
            chunk_index=-1,
            score=82 if high_risk else 18,
            suggestion="Block" if high_risk else "Pass",
            label="AIGC" if high_risk else "Normal",
            request_id=data_id,
            detail_results=({"Label": "AIGC", "Score": 82},) if high_risk else (),
        )


def _enable_gate(monkeypatch: pytest.MonkeyPatch, *, rounds: int = 2) -> None:
    monkeypatch.setattr(settings, "AIGC_GATE_ENABLED", True)
    monkeypatch.setattr(settings, "AIGC_GATE_FAIL_CLOSED", True)
    monkeypatch.setattr(settings, "AIGC_GATE_THRESHOLD", 50)
    monkeypatch.setattr(settings, "AIGC_GATE_MAX_REWRITE_ROUNDS", rounds)
    monkeypatch.setattr(settings, "AIGC_GATE_CHUNK_MIN_CHARS", 80)
    monkeypatch.setattr(settings, "AIGC_GATE_CHUNK_MAX_CHARS", 240)
    monkeypatch.setattr(settings, "TENCENTCLOUD_SECRET_ID", "test-id")
    monkeypatch.setattr(settings, "TENCENTCLOUD_SECRET_KEY", "test-key")
    monkeypatch.setattr(settings, "TENCENT_AIGC_BIZ_TYPE", "test-biz")


def test_build_chunks_preserves_headings_and_classical_original() -> None:
    source = (
        "@最终书名\n\n"
        "###第一章\n\n"
        "（原文）欲做精金美玉的人品，定从烈火中煅来；思立掀天揭地的事功，须向薄冰上履过。\n\n"
        "（翻译）一个人要想拥有真正过硬的好品格，就必须在现实的严酷考验中不断捶打自己；"
        "要想建立了不起的功业，就必须在如履薄冰的压力下，踏踏实实走好每一步，熬过所有磨难。"
    )
    chunks = build_aigc_chunks(
        source,
        category="classical_chinese_reference_rewrite",
        min_chars=40,
        max_chars=240,
    )

    joined = "\n".join(chunk.text for chunk in chunks)
    assert "原文" not in joined
    assert "欲做精金美玉" not in joined
    assert "一个人要想拥有" in joined
    assert all("###" not in chunk.text and "@最终书名" not in chunk.text for chunk in chunks)


def test_gate_rewrites_only_high_risk_chunks_and_retests(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _enable_gate(monkeypatch)
    source = (
        "这是一段整齐模板式的文字，句子彼此过分匀称，读来像预先排列好的说明。"
        "它保留全部事实，只需要改变句间节奏和承接方式，让表达更自然一些。"
    )
    calls: list[tuple[str, str, str]] = []

    async def rewrite(chunk: str, category: str, feedback: str) -> str:
        calls.append((chunk, category, feedback))
        return chunk.replace("整齐模板式", "过分规整").replace("预先排列好的", "提前排好的")

    result = asyncio.run(
        enforce_aigc_quality_gate(
            source,
            category="social_science",
            rewrite_chunk=rewrite,
            output_dir=tmp_path,
            detector=_FakeDetector(),
        )
    )

    assert result.passed is True
    assert result.max_score == 18
    assert len(result.rounds) == 2
    assert len(calls) == 1
    assert calls[0][1] == "social_science"
    assert "Score=82" in calls[0][2]
    assert "整齐模板" not in result.text
    report = json.loads((tmp_path / "aigc_quality_report.json").read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["rounds"][0]["rewrittenChunkIndexes"] == [0]


def test_gate_fails_closed_when_retest_stays_high(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _enable_gate(monkeypatch, rounds=1)
    source = "整齐模板仍然存在。" * 12

    async def unchanged(chunk: str, category: str, feedback: str) -> str:
        return chunk

    with pytest.raises(AigcQualityGateError, match="复检仍有"):
        asyncio.run(
            enforce_aigc_quality_gate(
                source,
                category="social_science",
                rewrite_chunk=unchanged,
                output_dir=tmp_path,
                detector=_FakeDetector(),
            )
        )

    report = json.loads((tmp_path / "aigc_quality_report.json").read_text(encoding="utf-8"))
    assert report["passed"] is False


def test_gate_blocks_rewrite_that_drops_numeric_or_quoted_anchors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_gate(monkeypatch, rounds=1)
    source = (
        "整齐模板反复出现，原稿明确写着2026年投入260元，并保留了“按原协议处理”这句原话。"
        "这些事实都不能在调整表达节奏时被删除或改写。"
    )

    async def drops_anchors(chunk: str, category: str, feedback: str) -> str:
        assert "本地可解释诊断" in feedback
        return "这段文字改换了表达方式，却把原稿里的金额、年份和引语全部省略了。" * 2

    with pytest.raises(AigcQualityGateError, match="丢失不可变锚点"):
        asyncio.run(
            enforce_aigc_quality_gate(
                source,
                category="family_long_novel",
                rewrite_chunk=drops_anchors,
                detector=_FakeDetector(),
            )
        )


def test_gate_reuses_matching_pass_report_without_new_detection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _enable_gate(monkeypatch)
    source = "这是一段自然稿件，包含足够上下文，用于确认同一终稿不会重复产生朱雀检测费用。" * 3
    detector = _FakeDetector()

    async def should_not_rewrite(chunk: str, category: str, feedback: str) -> str:
        raise AssertionError("clean manuscript must not be rewritten")

    first = asyncio.run(
        enforce_aigc_quality_gate(
            source,
            category="social_science",
            rewrite_chunk=should_not_rewrite,
            output_dir=tmp_path,
            detector=detector,
        )
    )

    class _MustNotRunDetector:
        def detect(self, text: str, *, data_id: str) -> AigcDetection:
            raise AssertionError("matching report should bypass Tencent API")

    second = asyncio.run(
        enforce_aigc_quality_gate(
            source,
            category="social_science",
            rewrite_chunk=should_not_rewrite,
            output_dir=tmp_path,
            detector=_MustNotRunDetector(),
        )
    )
    assert first.passed is True
    assert second.passed is True
    assert second.rounds == ()


def test_disabled_gate_is_zero_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AIGC_GATE_ENABLED", False)

    async def should_not_run(chunk: str, category: str, feedback: str) -> str:
        raise AssertionError("disabled gate must not call the rewriter")

    result = asyncio.run(
        enforce_aigc_quality_gate(
            "保持原样的正文。",
            category="social_science",
            rewrite_chunk=should_not_run,
            detector=_FakeDetector(),
        )
    )
    assert result.text == "保持原样的正文。"
    assert result.rounds == ()
