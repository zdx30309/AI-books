from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services import public_domain_classical_vernacular as classical
from app.services import public_domain_longform as longform


def _source(*, with_headings: bool = True, repeats: int = 24) -> longform.PublicDomainSource:
    body = (
        "县令命张三带四十二（42）名乡勇到城门候命，又把《兵法》交给李四保管。"
        "张三不敢迟延，当日便依吩咐行事；李四仍守在旧宅，没有另作主张。"
    ) * repeats
    text = f"第一回 城门领命\n{body}" if with_headings else body
    return longform.PublicDomainSource(
        title="旧城记",
        text=text,
        author="佚名",
        rights_note="公版文本，保护期届满",
    )


def _echo_marked_source(prompt: str) -> str:
    return prompt.split("【本次必须完整翻译的带编号原文】", 1)[1].strip()


def test_classical_mode_executes_one_translation_only_call_and_preserves_chapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_call(_work_dir: Path, *, action: str, prompt: str, **_kwargs) -> str:
        calls.append((action, prompt))
        return _echo_marked_source(prompt)

    monkeypatch.setattr(longform, "_call_llm", fake_call)
    result = longform.run_public_domain_longform(
        sources=[_source()],
        work_dir=tmp_path,
        generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
        public_domain_confirmed=True,
        adaptation_profile={"adaptation_intensity": "深度改编"},
    )

    assert len(calls) == 1
    assert calls[0][0].startswith("classical_complete_translation")
    assert "只做翻译" in calls[0][1]
    assert "不得改编、重写、润色、仿写、扩写、缩写" in calls[0][1]
    assert "[[P0001]]" in calls[0][1]
    assert result.title == "旧城记"
    assert [item.title for item in result.chapters] == ["第一回 城门领命"]
    assert "四十二" in result.chapters[0].text
    assert result.quality_report["paid_calls_per_segment"] == 1
    assert result.quality_report["automatic_retry_calls"] == 0
    assert result.quality_report["chapter_audits"][0]["operation"] == "complete_translation_only"


def test_classical_mode_resumes_completed_chapter_without_new_paid_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_call(_work_dir: Path, *, action: str, prompt: str, **_kwargs) -> str:
        calls.append(action)
        return _echo_marked_source(prompt)

    monkeypatch.setattr(longform, "_call_llm", fake_call)
    kwargs = dict(
        sources=[_source()],
        work_dir=tmp_path,
        generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
        public_domain_confirmed=True,
    )
    first = longform.run_public_domain_longform(**kwargs)
    assert len(calls) == 1
    calls.clear()
    second = longform.run_public_domain_longform(**kwargs)
    assert calls == []
    assert second.markdown == first.markdown


def test_classical_mode_reuses_each_completed_segment_after_later_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    fail_second = True

    def fake_call(_work_dir: Path, *, action: str, prompt: str, **_kwargs) -> str:
        nonlocal fail_second
        calls.append(action)
        if action.endswith(":0002") and fail_second:
            fail_second = False
            raise RuntimeError("temporary model failure")
        return _echo_marked_source(prompt)

    monkeypatch.setattr(longform, "_call_llm", fake_call)
    kwargs = dict(
        sources=[_source(repeats=100)],
        work_dir=tmp_path,
        generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
        public_domain_confirmed=True,
    )
    with pytest.raises(RuntimeError, match="temporary model failure"):
        longform.run_public_domain_longform(**kwargs)
    assert len(calls) == 2

    calls.clear()
    longform.run_public_domain_longform(**kwargs)
    assert calls
    assert all(not value.endswith(":0001") for value in calls)


def test_translation_gate_rejects_missing_or_reordered_paragraph_ids() -> None:
    with pytest.raises(longform.PublicDomainQualityError, match="段落编号不完整或顺序变化"):
        classical._parse_marked_translation(  # noqa: SLF001
            "[[P0002]]\n第二段\n\n[[P0001]]\n第一段",
            expected_markers=("[[P0001]]", "[[P0002]]"),
            chapter_title="第一回",
        )


def test_classical_mode_uses_source_title_when_original_has_no_heading() -> None:
    sections = classical.extract_source_sections([_source(with_headings=False)])
    assert len(sections) == 1
    assert sections[0].title == "旧城记"


def test_classical_validation_allows_one_source_but_keeps_rights_gate() -> None:
    source = _source()
    cleaned = longform.validate_sources(
        [source],
        public_domain_confirmed=True,
        generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
    )
    assert len(cleaned) == 1
    with pytest.raises(longform.PublicDomainValidationError, match="确认"):
        longform.validate_sources(
            [source],
            public_domain_confirmed=False,
            generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
        )


def test_classical_mode_accepts_multi_million_character_source() -> None:
    source = _source(with_headings=False, repeats=25_000)
    assert longform.count_text_chars(source.text) > 1_500_000
    cleaned = longform.validate_sources(
        [source],
        public_domain_confirmed=True,
        generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
    )
    assert len(cleaned) == 1


def test_classical_local_gate_rejects_dropped_numeric_anchor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = "[[P0001]]\n" + (
        "县令叫来张三，又把《兵法》交给李四。两人都照吩咐办事。" * 50
    )
    monkeypatch.setattr(longform, "_call_llm", lambda *_args, **_kwargs: candidate)
    with pytest.raises(longform.PublicDomainQualityError, match="数字"):
        longform.run_public_domain_longform(
            sources=[_source()],
            work_dir=tmp_path,
            generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
            public_domain_confirmed=True,
        )
