from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services import public_domain_classical_vernacular as classical
from app.services import public_domain_longform as longform
from app.api.routers import public_domain_rewrite as public_domain_router
from app.models import Chapter, Task, TaskStatus


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


def test_complete_and_preview_tasks_use_separate_bookshelves() -> None:
    assert public_domain_router._classical_bookshelf_for_scope("complete") == "公版古文白话"  # noqa: SLF001
    assert public_domain_router._classical_bookshelf_for_scope("characters") == "公版古文白话试译"  # noqa: SLF001
    assert public_domain_router._classical_bookshelf_for_scope("chapters") == "公版古文白话试译"  # noqa: SLF001


def _echo_marked_source(prompt: str) -> str:
    return prompt.split("【本次必须完整翻译的带编号原文】", 1)[1].strip()


def test_anchor_manifest_is_injected_for_titles_and_numbers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[str] = []

    def fake_call(_work_dir: Path, *, prompt: str, **_kwargs) -> str:
        prompts.append(prompt)
        return _echo_marked_source(prompt)

    monkeypatch.setattr(longform, "_call_llm", fake_call)
    body = "张三读《楚些》3章，又把《兵法》交给李四。" * 30
    source = longform.PublicDomainSource(
        title="旧城记",
        text=f"第一回 城门领命\n{body}",
        author="佚名",
        rights_note="公版文本，保护期届满",
    )

    longform.run_public_domain_longform(
        sources=[source],
        work_dir=tmp_path,
        generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
        public_domain_confirmed=True,
    )

    assert len(prompts) == 1
    assert "【逐段显式锚点清单】" in prompts[0]
    assert "[[P0001]]：数字 3；书名/篇名 《楚些》、《兵法》" in prompts[0]
    assert "不得改名、改写、概括或移动到其他段落" in prompts[0]


def test_classical_mode_targeted_repair_fixes_missing_title_anchor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_call(_work_dir: Path, *, action: str, prompt: str, **_kwargs) -> str:
        calls.append((action, prompt))
        if action.startswith("classical_complete_translation"):
            return "[[P0001]]\n张三读楚辞三章，又把兵书交给李四。"
        return prompt.split("【必须完整翻译的带编号原文】", 1)[1].split(
            "【第一版带编号候选译文】", 1
        )[0].strip()

    monkeypatch.setattr(longform, "_call_llm", fake_call)
    body = "张三读《楚些》3章，又把《兵法》交给李四。" * 30
    source = longform.PublicDomainSource(
        title="旧城记",
        text=f"第一回 城门领命\n{body}",
        author="佚名",
        rights_note="公版文本，保护期届满",
    )

    result = longform.run_public_domain_longform(
        sources=[source],
        work_dir=tmp_path,
        generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
        public_domain_confirmed=True,
    )

    assert [item[0] for item in calls] == [
        "classical_complete_translation:0001:0001",
        "classical_targeted_repair:0001:0001",
    ]
    assert "遗漏原文书名或篇名" in calls[1][1]
    assert "《楚些》" in result.chapters[0].text
    assert result.quality_report["targeted_repair_calls"] == 1
    assert result.quality_report["chapter_audits"][0]["targeted_repair_calls"] == 1
    assert result.quality_report["chapter_audits"][0]["segment_audits"][0]["targeted_repair_calls"] == 1


def test_classical_mode_executes_one_translation_only_call_and_preserves_chapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []

    def fake_call(_work_dir: Path, *, action: str, prompt: str, **kwargs) -> str:
        calls.append((action, prompt, kwargs))
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
    assert calls[0][2]["thinking"] is False
    assert calls[0][2]["retries"] == 1
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


def test_classical_extractor_removes_contents_and_keeps_real_preface_and_chapters() -> None:
    source = longform.PublicDomainSource(
        title="旧城三卷",
        author="佚名",
        rights_note="公版文本，保护期届满",
        text=(
            "Cover\n\nContents\n\n目录\n"
            "第一回 城门领命\n"
            "第二回 守宅待信\n"
            "第三回 河边送行\n\n"
            "编辑说明\n这段现代编辑说明不属于古文正文。\n\n"
            "序\n尝闻忠义之士，临难不改其节。\n\n"
            "第一回 城门领命\n县令命张三到城门候命。\n\n"
            "第二回 守宅待信\n李四仍守在旧宅，不敢擅离。\n\n"
            "第三回 河边送行\n二人相别，各自珍重。"
        ),
    )

    sections = classical.extract_source_sections([source])

    assert [item.title for item in sections] == [
        "序",
        "第一回 城门领命",
        "第二回 守宅待信",
        "第三回 河边送行",
    ]
    combined = "\n".join(item.text for item in sections)
    assert "Cover" not in combined
    assert "目录" not in combined
    assert "现代编辑说明" not in combined
    assert "忠义之士" in combined


def test_short_complete_translation_is_not_forced_to_120_characters() -> None:
    issues = classical._segment_issues(  # noqa: SLF001
        source_text="闻鼓而进，闻金而退，不避水火。",
        candidate="听见鼓声就前进，听见鸣金便后退，水火也不躲避。",
    )
    assert issues == []


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


def test_custom_prompt_is_injected_without_removing_fixed_translation_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[str] = []

    def fake_call(_work_dir: Path, *, prompt: str, **_kwargs) -> str:
        prompts.append(prompt)
        return _echo_marked_source(prompt)

    monkeypatch.setattr(longform, "_call_llm", fake_call)
    custom_prompt = "对白用简洁现代汉语，叙述保持沉稳；字面占位 {work_title} 不得被系统改写。"
    result = longform.run_public_domain_longform(
        sources=[_source(repeats=100)],
        work_dir=tmp_path,
        generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
        public_domain_confirmed=True,
        adaptation_profile={"custom_prompt": custom_prompt},
    )
    assert len(prompts) > 1
    assert all(custom_prompt in prompt for prompt in prompts)
    assert all("如果二者冲突，以固定规则为准" in prompt for prompt in prompts)
    assert all("不得改编、重写、润色、仿写、扩写、缩写" in prompt for prompt in prompts)
    assert (tmp_path / "00_classical_user_prompt.md").read_text(encoding="utf-8") == custom_prompt
    assert result.quality_report["prompt_injection_segments"] == len(prompts)
    assert result.quality_report["translated_segments"] == len(prompts)


def test_changing_custom_prompt_invalidates_segment_checkpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_call(_work_dir: Path, *, prompt: str, **_kwargs) -> str:
        calls.append(prompt)
        return _echo_marked_source(prompt)

    monkeypatch.setattr(longform, "_call_llm", fake_call)
    common = dict(
        sources=[_source()],
        work_dir=tmp_path,
        generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
        public_domain_confirmed=True,
    )
    longform.run_public_domain_longform(
        **common,
        adaptation_profile={"custom_prompt": "第一版译法要求。"},
    )
    first_count = len(calls)
    calls.clear()

    longform.run_public_domain_longform(
        **common,
        adaptation_profile={"custom_prompt": "第二版译法要求。"},
    )

    assert first_count > 0
    assert calls
    assert all("第二版译法要求。" in prompt for prompt in calls)


def test_character_preview_can_end_before_all_available_source_chapters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _source(repeats=80).text.split("\n", 1)[1]
    source = longform.PublicDomainSource(
        title="旧城多卷",
        text=f"第一回 城门领命\n{body}\n第二回 守宅待信\n{body}",
        author="佚名",
        rights_note="公版文本，保护期届满",
    )
    monkeypatch.setattr(
        longform,
        "_call_llm",
        lambda _work_dir, *, prompt, **_kwargs: _echo_marked_source(prompt),
    )

    result = longform.run_public_domain_longform(
        sources=[source],
        work_dir=tmp_path,
        generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
        public_domain_confirmed=True,
        adaptation_profile={"scope_mode": "characters", "preview_target_chars": 1_000},
    )

    assert result.quality_report["status"] == "ok"
    assert result.quality_report["is_preview"] is True
    assert len(result.chapters) == 1
    assert result.quality_report["available_source_chapters"] == 2


def test_character_preview_stops_after_selected_output_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_call(_work_dir: Path, *, action: str, prompt: str, **_kwargs) -> str:
        calls.append(action)
        return _echo_marked_source(prompt)

    monkeypatch.setattr(longform, "_call_llm", fake_call)
    source = _source(repeats=100)
    result = longform.run_public_domain_longform(
        sources=[source],
        work_dir=tmp_path,
        generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
        public_domain_confirmed=True,
        adaptation_profile={
            "scope_mode": "characters",
            "preview_target_chars": 1_000,
        },
    )
    output_chars = longform.count_text_chars(result.chapters[0].text)
    assert output_chars >= 1_000
    assert output_chars < longform.count_text_chars(source.text)
    assert result.quality_report["is_preview"] is True
    assert result.quality_report["scope_mode"] == "characters"
    assert result.chapters[0].audit["partial_section"] is True
    assert 1 < len(calls) < 10


def test_chapter_preview_uses_original_chapter_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _source().text.split("\n", 1)[1]
    source = longform.PublicDomainSource(
        title="旧城两卷",
        text=f"第一回 城门领命\n{body}\n第二回 守宅待信\n{body}",
        author="佚名",
        rights_note="公版文本，保护期届满",
    )
    monkeypatch.setattr(
        longform,
        "_call_llm",
        lambda _work_dir, *, prompt, **_kwargs: _echo_marked_source(prompt),
    )
    result = longform.run_public_domain_longform(
        sources=[source],
        work_dir=tmp_path,
        generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
        public_domain_confirmed=True,
        adaptation_profile={"scope_mode": "chapters", "preview_chapters": 1},
    )
    assert len(result.chapters) == 1
    assert result.chapters[0].title == "第一回 城门领命"
    assert result.quality_report["available_source_chapters"] == 2
    assert result.quality_report["preview_target_chapters"] == 1


def test_character_preview_upgrade_reuses_paid_segment_checkpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_call(_work_dir: Path, *, action: str, prompt: str, **_kwargs) -> str:
        calls.append(action)
        return _echo_marked_source(prompt)

    monkeypatch.setattr(longform, "_call_llm", fake_call)
    source = _source(repeats=100)
    preview_profile = {
        "scope_mode": "characters",
        "preview_target_chars": 1_000,
        "preview_chapters": 0,
    }
    preview = longform.run_public_domain_longform(
        sources=[source],
        work_dir=tmp_path,
        generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
        public_domain_confirmed=True,
        adaptation_profile=preview_profile,
    )
    preview_calls = list(calls)
    assert preview.chapters[0].audit["partial_section"] is True
    assert len(preview_calls) >= 1

    calls.clear()
    continued_profile = {
        **preview_profile,
        "scope_mode": "complete",
        "preview_target_chars": 0,
        "_checkpoint_profile": preview_profile,
        "_checkpoint_segment_limit": min(
            classical.SEGMENT_CHARS,
            max(600, int(1_000 / 1.35)),
        ),
    }
    complete = longform.run_public_domain_longform(
        sources=[source],
        work_dir=tmp_path,
        generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
        public_domain_confirmed=True,
        adaptation_profile=continued_profile,
    )

    assert complete.chapters[0].audit["partial_section"] is False
    assert calls
    reused_indexes = {value.rsplit(":", 1)[-1] for value in preview_calls}
    assert all(value.rsplit(":", 1)[-1] not in reused_indexes for value in calls)


def test_classical_bookshelf_workspace_restores_saved_task_state() -> None:
    task = Task(
        id=uuid4(),
        source_url="manual://public-domain-classical-vernacular",
        status=TaskStatus.COMPLETED,
        progress=100,
        current_step="试译完成",
        config={
            "title": "旧城记试译",
            "bookshelf": "公版古文白话试译",
            "public_domain_classical_vernacular": True,
            "public_domain_classical_scope": "characters",
            "public_domain_confirmed": True,
            "generation_complete": True,
            "source_count": 1,
            "source_reports": [
                {
                    "title": "旧城记",
                    "author": "佚名",
                    "chars": 12_345,
                    "detected_chapters": 8,
                    "rights_basis": "公版文本",
                    "sha256": "abc123",
                }
            ],
            "adaptation_profile": {
                "custom_prompt": "逐句完整翻译。",
                "scope_mode": "characters",
                "preview_target_chars": 3_000,
                "preview_chapters": 0,
            },
            "checkpoint_chapters": 1,
            "checkpoint_chars": 3_100,
            "classical_translated_segments": 3,
        },
    )
    task.chapters.append(
        Chapter(chapter_num=1, title="第一回", content="白话译文" * 20, word_count=80)
    )

    state = public_domain_router._classical_workspace_state(task)  # noqa: SLF001

    assert state.task_id == task.id
    assert state.custom_prompt == "逐句完整翻译。"
    assert state.scope_mode == "characters"
    assert state.preview_target_chars == 3_000
    assert state.sources[0].title == "旧城记"
    assert state.available_chapters == 8
    assert state.can_continue_complete is True
    assert state.can_resume is True


def test_continue_complete_keeps_same_task_and_original_checkpoint_profile() -> None:
    task_id = uuid4()
    task = Task(
        id=task_id,
        source_url="manual://public-domain-classical-vernacular",
        status=TaskStatus.COMPLETED,
        progress=100,
        current_step="试译完成",
        config={
            "title": "旧城记试译",
            "bookshelf": "公版古文白话试译",
            "category_label": "公版古文白话试译",
            "public_domain_classical_vernacular": True,
            "public_domain_classical_preview": True,
            "public_domain_classical_scope": "characters",
            "generation_complete": True,
            "source_snapshot": "saved-source.json",
            "source_reports": [{"chars": 50_000, "detected_chapters": 12}],
            "adaptation_profile": {
                "custom_prompt": "忠实翻译。",
                "scope_mode": "characters",
                "preview_target_chars": 3_000,
                "preview_chapters": 0,
            },
        },
    )
    task.chapters.append(
        Chapter(chapter_num=1, title="第一回", content="译文" * 1_000, word_count=2_000)
    )

    public_domain_router._prepare_classical_complete_continuation(task)  # noqa: SLF001

    assert task.id == task_id
    assert task.status == TaskStatus.PENDING
    assert task.config["source_snapshot"] == "saved-source.json"
    assert task.config["bookshelf"] == "公版古文白话"
    assert task.config["generation_complete"] is False
    profile = task.config["adaptation_profile"]
    assert profile["scope_mode"] == "complete"
    assert profile["_checkpoint_profile"]["scope_mode"] == "characters"
    assert profile["_checkpoint_profile"]["preview_target_chars"] == 3_000
    assert 600 <= profile["_checkpoint_segment_limit"] <= classical.SEGMENT_CHARS
