from __future__ import annotations

from pathlib import Path

import pytest

from app.services import public_domain_classical_vernacular as classical
from app.services import public_domain_longform as longform


def _source(repeats: int = 120) -> longform.PublicDomainSource:
    return longform.PublicDomainSource(
        title="旧城记",
        text="第一回 城门领命\n" + "县令命张三到城门候命。" * repeats,
        author="佚名",
        rights_note="公版文本，保护期届满",
    )


def test_classical_prompt_is_required() -> None:
    with pytest.raises(longform.PublicDomainValidationError, match="外置 Prompt"):
        classical.normalize_profile({})


def test_classical_prompt_inserts_source_at_user_selected_placeholder() -> None:
    assert classical._compose_external_prompt(
        "请处理：{source_text}", "原文"
    ) == "请处理：原文"


def test_classical_prompt_appends_source_without_adding_instructions() -> None:
    assert classical._compose_external_prompt(
        "把下列内容改成现代白话", "原文"
    ) == "把下列内容改成现代白话\n\n原文"


def test_classical_mode_sends_only_external_prompt_and_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[str] = []

    def fake_call(_work_dir: Path, *, prompt: str, **_kwargs) -> str:
        prompts.append(prompt)
        return "完全由用户 Prompt 决定的输出"

    monkeypatch.setattr(longform, "_call_llm", fake_call)
    source = _source()
    result = longform.run_public_domain_longform(
        sources=[source],
        work_dir=tmp_path,
        generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
        public_domain_confirmed=True,
        adaptation_profile={"custom_prompt": "按要求处理：{source_text}"},
    )

    assert prompts == [f"按要求处理：{source.text.split(chr(10), 1)[1]}"]
    assert result.chapters[0].text == "完全由用户 Prompt 决定的输出"
    assert result.quality_report["status"] == "external_prompt_only"
    assert result.quality_report["targeted_repair_calls"] == 0


def test_changed_external_prompt_invalidates_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_call(_work_dir: Path, *, prompt: str, **_kwargs) -> str:
        calls.append(prompt)
        return "模型输出"

    monkeypatch.setattr(longform, "_call_llm", fake_call)
    common = {
        "sources": [_source()],
        "work_dir": tmp_path,
        "generation_mode": longform.CLASSICAL_VERNACULAR_MODE,
        "public_domain_confirmed": True,
    }
    longform.run_public_domain_longform(
        **common, adaptation_profile={"custom_prompt": "第一版：{source_text}"}
    )
    calls.clear()
    longform.run_public_domain_longform(
        **common, adaptation_profile={"custom_prompt": "第二版：{source_text}"}
    )

    assert calls
    assert all(prompt.startswith("第二版：") for prompt in calls)
