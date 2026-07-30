from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services import public_domain_classical_vernacular as classical
from app.services import public_domain_longform as longform


def _source(*, with_headings: bool = True) -> longform.PublicDomainSource:
    body = (
        "县令命张三带四十二（42）名乡勇到城门候命，又把《兵法》交给李四保管。"
        "张三不敢迟延，当日便依吩咐行事；李四仍守在旧宅，没有另作主张。"
    ) * 24
    text = f"第一回 城门领命\n{body}" if with_headings else body
    return longform.PublicDomainSource(
        title="旧城记",
        text=text,
        author="佚名",
        rights_note="公版文本，保护期届满",
    )


def _candidate() -> str:
    return (
        "县令叫来张三，命他带四十二（42）名乡勇去城门候命，又把《兵法》交给李四收好。"
        "张三不敢耽搁，当日就照吩咐办了。李四仍守着旧宅，没有擅作主张。"
    ) * 18


def test_classical_mode_executes_exact_two_pass_prompts_and_preserves_chapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_call(_work_dir: Path, *, action: str, prompt: str, **_kwargs) -> str:
        calls.append((action, prompt))
        return _candidate()

    monkeypatch.setattr(longform, "_call_llm", fake_call)
    result = longform.run_public_domain_longform(
        sources=[_source()],
        work_dir=tmp_path,
        generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
        public_domain_confirmed=True,
        adaptation_profile={"special_requirements": "保留城门领命的事件次序"},
    )

    assert len(calls) == 2
    assert calls[0][0].startswith("classical_vernacular_round1")
    assert "核心剧情保留率：95%以上" in calls[0][1]
    assert "每次只处理我提供的内容，不提前续写后续剧情" in calls[0][1]
    assert "保留城门领命的事件次序" in calls[0][1]
    assert calls[1][0].startswith("classical_vernacular_round2")
    assert "先理解，再重写" in calls[1][1]
    assert "【第一轮白话改编稿】" in calls[1][1]
    assert result.title == "旧城记"
    assert [item.title for item in result.chapters] == ["第一回 城门领命"]
    assert "四十二" in result.chapters[0].text
    assert result.quality_report["paid_calls_per_segment"] == 2
    assert result.quality_report["automatic_retry_calls"] == 0


def test_classical_mode_resumes_completed_chapter_without_new_paid_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_call(_work_dir: Path, *, action: str, **_kwargs) -> str:
        calls.append(action)
        return _candidate()

    monkeypatch.setattr(longform, "_call_llm", fake_call)
    kwargs = dict(
        sources=[_source()],
        work_dir=tmp_path,
        generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
        public_domain_confirmed=True,
    )
    first = longform.run_public_domain_longform(**kwargs)
    assert len(calls) == 2
    calls.clear()
    second = longform.run_public_domain_longform(**kwargs)
    assert calls == []
    assert second.markdown == first.markdown


def test_classical_mode_reuses_finished_round_one_after_round_two_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    fail_round_two = True

    def fake_call(_work_dir: Path, *, action: str, **_kwargs) -> str:
        nonlocal fail_round_two
        calls.append(action)
        if "round2" in action and fail_round_two:
            fail_round_two = False
            raise RuntimeError("temporary model failure")
        return _candidate()

    monkeypatch.setattr(longform, "_call_llm", fake_call)
    kwargs = dict(
        sources=[_source()],
        work_dir=tmp_path,
        generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
        public_domain_confirmed=True,
    )
    with pytest.raises(RuntimeError, match="temporary model failure"):
        longform.run_public_domain_longform(**kwargs)
    assert len(calls) == 2

    calls.clear()
    longform.run_public_domain_longform(**kwargs)
    assert len(calls) == 1
    assert "round2" in calls[0]


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


def test_classical_local_gate_rejects_dropped_numeric_anchor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = "县令叫来张三，又把《兵法》交给李四。两人都照吩咐办事。" * 50
    monkeypatch.setattr(longform, "_call_llm", lambda *_args, **_kwargs: candidate)
    with pytest.raises(longform.PublicDomainQualityError, match="数字"):
        longform.run_public_domain_longform(
            sources=[_source()],
            work_dir=tmp_path,
            generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
            public_domain_confirmed=True,
        )
