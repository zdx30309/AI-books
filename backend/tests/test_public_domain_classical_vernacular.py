from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services import public_domain_classical_vernacular as classical
from app.services import content_engine
from app.services import public_domain_longform as longform
from app.api.routers import public_domain_rewrite as public_domain_router
from app.models import Chapter, Task, TaskStatus
from app.tasks import tasks as task_jobs


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


def test_external_prompt_is_required_and_there_is_no_default() -> None:
    with pytest.raises(longform.PublicDomainValidationError, match="没有内置 Prompt"):
        classical.normalize_profile({})
    assert classical.get_default_user_prompt() == ""


def test_external_prompt_is_the_only_semantic_instruction() -> None:
    rendered, mode = classical._render_external_user_prompt(  # noqa: SLF001
        "按我的规则翻译：{source_text}",
        source_text="[[P0001]]\n原文",
        work_title="书名",
        chapter_title="第一回",
        segment_label="本章",
    )
    assert rendered == "按我的规则翻译：[[P0001]]\n原文"
    assert mode == "explicit_placeholders"
    assert "系统" not in rendered


def _echo_marked_source(prompt: str) -> str:
    matches = re.findall(r"(\[\[P\d{4}\]\])\s*(.*?)(?=\n\s*\[\[P\d{4}\]\]|\Z)", prompt, re.S)
    return json.dumps(
        {"units": [{"marker": marker, "text": body.strip()} for marker, body in matches]},
        ensure_ascii=False,
    )


def test_anchor_manifest_stays_local_and_is_not_injected_into_model_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_call(_work_dir: Path, *, prompt: str, **kwargs) -> str:
        calls.append((prompt, kwargs))
        return _echo_marked_source(prompt)

    monkeypatch.setattr(classical, "_call_translation_writer", fake_call)
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
        adaptation_profile={"custom_prompt": "完整翻译：{source_text}"},
    )

    assert len(calls) == 1
    assert "【逐段显式锚点清单】" not in calls[0][0]
    assert calls[0][0].startswith("完整翻译：[[P0001]]")
    assert calls[0][1]["response_schema"]["required"] == ["units"]


def test_classical_mode_stops_without_paid_repair_when_anchor_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_call(_work_dir: Path, *, action: str, prompt: str, **_kwargs) -> str:
        calls.append((action, prompt))
        payload = json.loads(_echo_marked_source(prompt))
        for item in payload["units"]:
            item["text"] = item["text"].replace("《楚些》", "楚辞").replace("《兵法》", "兵书")
        return json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(classical, "_call_translation_writer", fake_call)
    body = "张三读《楚些》3章，又把《兵法》交给李四。" * 30
    source = longform.PublicDomainSource(
        title="旧城记",
        text=f"第一回 城门领命\n{body}",
        author="佚名",
        rights_note="公版文本，保护期届满",
    )

    with pytest.raises(longform.PublicDomainQualityError, match="不会自动重试"):
        longform.run_public_domain_longform(
            sources=[source],
            work_dir=tmp_path,
            generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
            public_domain_confirmed=True,
            adaptation_profile={"custom_prompt": "完整翻译：{source_text}"},
        )

    assert [item[0] for item in calls] == ["classical_complete_translation:0001:0001"]


def test_classical_mode_executes_one_translation_only_call_and_preserves_chapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []

    def fake_call(_work_dir: Path, *, action: str, prompt: str, **kwargs) -> str:
        calls.append((action, prompt, kwargs))
        return _echo_marked_source(prompt)

    monkeypatch.setattr(classical, "_call_translation_writer", fake_call)
    result = longform.run_public_domain_longform(
        sources=[_source()],
        work_dir=tmp_path,
        generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
        public_domain_confirmed=True,
        adaptation_profile={"custom_prompt": "完整翻译：{source_text}"},
    )

    assert len(calls) == 1
    assert calls[0][0].startswith("classical_complete_translation")
    assert calls[0][1].startswith("完整翻译：[[P0001]]")
    assert "本分片最高优先级的用户指令" not in calls[0][1]
    assert "[[P0001]]" in calls[0][1]
    assert "输出空间" not in calls[0][1]
    assert "最多约" not in calls[0][1]
    assert calls[0][2]["thinking"] is True
    assert calls[0][2]["max_output_tokens"] == 0
    assert calls[0][2]["retries"] == 1
    assert calls[0][2]["response_schema"]["required"] == ["units"]
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

    monkeypatch.setattr(classical, "_call_translation_writer", fake_call)
    kwargs = dict(
        sources=[_source()],
        work_dir=tmp_path,
        generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
        public_domain_confirmed=True,
        adaptation_profile={"custom_prompt": "完整翻译：{source_text}"},
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

    monkeypatch.setattr(classical, "_call_translation_writer", fake_call)
    kwargs = dict(
        sources=[_source(repeats=300)],
        work_dir=tmp_path,
        generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
        public_domain_confirmed=True,
        adaptation_profile={"custom_prompt": "完整翻译：{source_text}"},
    )
    with pytest.raises(RuntimeError, match="temporary model failure"):
        longform.run_public_domain_longform(**kwargs)
    assert len(calls) == 2

    calls.clear()
    longform.run_public_domain_longform(**kwargs)
    assert calls
    assert all(not value.endswith(":0001") for value in calls)


def test_translation_gate_rejects_missing_or_reordered_paragraph_ids() -> None:
    with pytest.raises(longform.PublicDomainQualityError, match="句群编号不完整或顺序变化"):
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
    def omit_numeric_anchor(_work_dir: Path, *, prompt: str, **_kwargs) -> str:
        payload = json.loads(_echo_marked_source(prompt))
        for item in payload["units"]:
            item["text"] = item["text"].replace("42", "若干")
        return json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(classical, "_call_translation_writer", omit_numeric_anchor)
    with pytest.raises(longform.PublicDomainQualityError, match="数字"):
        longform.run_public_domain_longform(
            sources=[_source()],
            work_dir=tmp_path,
            generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
            public_domain_confirmed=True,
            adaptation_profile={"custom_prompt": "完整翻译：{source_text}"},
        )


def test_custom_prompt_is_exact_highest_priority_contract_on_every_segment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[str] = []

    def fake_call(_work_dir: Path, *, prompt: str, **_kwargs) -> str:
        prompts.append(prompt)
        return _echo_marked_source(prompt)

    monkeypatch.setattr(classical, "_call_translation_writer", fake_call)
    custom_prompt = "对白用简洁现代汉语，叙述保持沉稳；字面占位 {literal_field} 不得被系统改写。"
    result = longform.run_public_domain_longform(
        sources=[_source(repeats=300)],
        work_dir=tmp_path,
        generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
        public_domain_confirmed=True,
        adaptation_profile={"custom_prompt": custom_prompt},
    )
    assert len(prompts) > 1
    assert all(prompt.startswith(custom_prompt) for prompt in prompts)
    assert all("完整编辑合同，不是参考意见" not in prompt for prompt in prompts)
    assert (tmp_path / "00_classical_user_prompt.md").read_text(encoding="utf-8") == custom_prompt
    assert result.quality_report["prompt_injection_segments"] == len(prompts)
    assert result.quality_report["translated_segments"] == len(prompts)


def test_prompt_requested_web_lookup_is_exposed_to_every_paid_segment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flags: list[bool] = []

    def fake_call(
        _work_dir: Path,
        *,
        prompt: str,
        enable_web_search: bool = False,
        **_kwargs,
    ) -> str:
        flags.append(enable_web_search)
        return _echo_marked_source(prompt)

    monkeypatch.setattr(classical, "_call_translation_writer", fake_call)
    custom_prompt = "必要时必须联网考证官名和典章制度，再按原文语气完成白话改写。"

    longform.run_public_domain_longform(
        sources=[_source(repeats=300)],
        work_dir=tmp_path,
        generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
        public_domain_confirmed=True,
        adaptation_profile={"custom_prompt": custom_prompt},
    )

    assert len(flags) > 1
    assert all(flags)
    assert classical._prompt_requests_web_search("不要联网，只按原文处理") is False  # noqa: SLF001


def test_changing_custom_prompt_invalidates_segment_checkpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_call(_work_dir: Path, *, prompt: str, **_kwargs) -> str:
        calls.append(prompt)
        return _echo_marked_source(prompt)

    monkeypatch.setattr(classical, "_call_translation_writer", fake_call)
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
        classical,
        "_call_translation_writer",
        lambda _work_dir, *, prompt, **_kwargs: _echo_marked_source(prompt),
    )

    result = longform.run_public_domain_longform(
        sources=[source],
        work_dir=tmp_path,
        generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
        public_domain_confirmed=True,
        adaptation_profile={
            "custom_prompt": "完整翻译：{source_text}",
            "scope_mode": "characters",
            "preview_target_chars": 1_000,
        },
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

    monkeypatch.setattr(classical, "_call_translation_writer", fake_call)
    source = _source(repeats=100)
    result = longform.run_public_domain_longform(
        sources=[source],
        work_dir=tmp_path,
        generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
        public_domain_confirmed=True,
        adaptation_profile={
            "custom_prompt": "完整翻译：{source_text}",
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
        classical,
        "_call_translation_writer",
        lambda _work_dir, *, prompt, **_kwargs: _echo_marked_source(prompt),
    )
    result = longform.run_public_domain_longform(
        sources=[source],
        work_dir=tmp_path,
        generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
        public_domain_confirmed=True,
        adaptation_profile={
            "custom_prompt": "完整翻译：{source_text}",
            "scope_mode": "chapters",
            "preview_chapters": 1,
        },
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

    monkeypatch.setattr(classical, "_call_translation_writer", fake_call)
    source = _source(repeats=100)
    preview_profile = {
        "custom_prompt": "完整翻译：{source_text}",
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
    assert complete.chapters[0].audit["source_units"] > 1
    assert complete.chapters[0].audit["translated_units"] == complete.chapters[0].audit["source_units"]
    assert complete.quality_report["translated_units"] == complete.quality_report["source_units"]
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


def test_continue_uses_current_scope_and_exact_edited_prompt() -> None:
    task = Task(
        id=uuid4(),
        source_url="manual://public-domain-classical-vernacular",
        status=TaskStatus.COMPLETED,
        progress=100,
        current_step="试译完成",
        config={
            "public_domain_classical_vernacular": True,
            "public_domain_classical_scope": "characters",
            "generation_complete": True,
            "source_snapshot": "saved-source.json",
            "source_reports": [{"chars": 50_000, "detected_chapters": 12}],
            "adaptation_profile": {
                "custom_prompt": "旧 Prompt。",
                "scope_mode": "characters",
                "preview_target_chars": 3_000,
                "preview_chapters": 0,
            },
        },
    )

    public_domain_router._prepare_classical_continuation(  # noqa: SLF001
        task,
        scope_mode="chapters",
        preview_chapters=3,
        custom_prompt="逐段完整翻译；每个付费分段必须原样注入本指令。",
    )

    profile = task.config["adaptation_profile"]
    assert task.status == TaskStatus.PENDING
    assert profile["scope_mode"] == "chapters"
    assert profile["preview_chapters"] == 3
    assert profile["custom_prompt"] == "逐段完整翻译；每个付费分段必须原样注入本指令。"
    assert task.config["source_snapshot"] == "saved-source.json"


def test_pause_request_stops_before_next_paid_segment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_call(_work_dir: Path, *, action: str, prompt: str, **_kwargs) -> str:
        calls.append(action)
        return _echo_marked_source(prompt)

    monkeypatch.setattr(classical, "_call_translation_writer", fake_call)
    with pytest.raises(classical.ClassicalTranslationPaused):
        longform.run_public_domain_longform(
            sources=[_source(repeats=100)],
            work_dir=tmp_path,
            generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
            public_domain_confirmed=True,
            adaptation_profile={"custom_prompt": "完整翻译：{source_text}"},
            should_pause=lambda: True,
        )
    assert calls == []


def test_single_physical_paragraph_is_split_into_bounded_sentence_units() -> None:
    source = (
        "县令命张三到城门候命，李四仍守旧宅，不敢擅离。"
        "俄而鼓声大作，众人皆起，张三却仍立在原处。"
        "县令再问其故，张三把前因后果一一说了。"
    ) * 18

    marked, units = classical._build_marked_source(source)  # noqa: SLF001

    assert len(units) > 8
    assert all(unit.source_chars <= classical.SOURCE_UNIT_MAX_CHARS for unit in units)
    assert "[[P0002]]" in marked
    assert "".join(unit.text for unit in units) == source


def test_unpunctuated_long_source_is_fully_partitioned_without_data_loss() -> None:
    source = "忠孝节义礼智信" * 90

    _marked, units = classical._build_marked_source(source)  # noqa: SLF001

    assert len(units) >= 3
    assert all(unit.source_chars <= classical.SOURCE_UNIT_MAX_CHARS for unit in units)
    assert "".join(unit.text for unit in units) == source


def test_sentence_unit_gate_rejects_tail_omission_even_when_all_markers_exist() -> None:
    source = (
        "县令命张三带四十二名乡勇到城门候命，又把《兵法》交给李四保管。"
        "张三不敢迟延，当日便依吩咐行事；李四仍守在旧宅，没有另作主张。"
    ) * 8
    _marked, units = classical._build_marked_source(source)  # noqa: SLF001
    candidate = "\n\n".join(
        f"{unit.marker}\n{unit.text if index < len(units) else '略'}"
        for index, unit in enumerate(units, start=1)
    )

    with pytest.raises(longform.PublicDomainQualityError, match="句群级完整性检查未通过"):
        classical._parse_marked_translation(  # noqa: SLF001
            candidate,
            expected_markers=tuple(unit.marker for unit in units),
            chapter_title="第一回",
            source_units=units,
        )


def test_valid_sentence_units_reassemble_original_paragraph_boundaries() -> None:
    source = "甲曰：今日当行。乙曰：尚可再议。\n\n众人闻之，各自散去。"
    marked, units = classical._build_marked_source(source)  # noqa: SLF001

    translated, count = classical._parse_marked_translation(  # noqa: SLF001
        marked,
        expected_markers=tuple(unit.marker for unit in units),
        chapter_title="第一回",
        source_units=units,
    )

    assert count == len(units)
    assert translated == source


def test_translation_writer_is_openai_gpt_56_sol_xhigh_without_hidden_paid_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_openai_call(prompt: str, **kwargs):
        captured.update(prompt=prompt, **kwargs)
        callback = kwargs.get("on_background_response_id")
        if callback:
            callback("resp_fixture")
        return "完整白话译文", {
            "input_tokens": 120,
            "output_tokens": 80,
            "total_tokens": 200,
            "cached_input_tokens": 0,
            "reasoning_tokens": 0,
            "service_tier": "default",
            "response_id": "resp_fixture",
        }

    monkeypatch.setattr(content_engine, "_openai_writer_call_result", fake_openai_call)
    monkeypatch.setattr(classical.settings, "OPENAI_WRITER_MODEL", "gpt-5.6-sol")
    monkeypatch.setattr(
        classical.settings, "CLASSICAL_TRANSLATION_WRITER_PROVIDER", "openai"
    )

    output = classical._call_translation_writer(  # noqa: SLF001
        tmp_path,
        action="classical_complete_translation:0001:0001",
        prompt="逐句完整翻译",
        temperature=0.1,
        max_output_tokens=5000,
        thinking=False,
        retries=1,
        enable_web_search=True,
    )

    assert output == "完整白话译文"
    assert captured["prompt"] == "逐句完整翻译"
    assert captured["reasoning_effort"] == "xhigh"
    assert captured["verbosity"] == "low"
    assert captured["retry_on_failure"] is False
    assert captured["background"] is True
    assert captured["background_timeout_seconds"] == 7200
    assert captured["background_response_id"] is None
    assert captured["tools"] == [
        {"type": "web_search", "search_context_size": "medium"}
    ]
    response_state = json.loads(
        (tmp_path / classical.OPENAI_BACKGROUND_STATE_FILE).read_text(encoding="utf-8")
    )
    assert response_state["classical_complete_translation:0001:0001"]["status"] == "completed"
    assert response_state["classical_complete_translation:0001:0001"]["response_id"] == "resp_fixture"
    events = [
        json.loads(line)
        for line in (tmp_path / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert events[-1]["provider"] == "openai"
    assert events[-1]["model"] == "gpt-5.6-sol"
    assert events[-1]["status"] == "ok"
    assert events[-1]["web_search_tool_enabled"] is True
    assert events[-1]["prompt_sha256"] == classical._sha("逐句完整翻译")  # noqa: SLF001
    assert events[-1]["usage_tokens"]["total_tokens"] == 200


def test_translation_writer_resumes_persisted_background_response_without_new_paid_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action = "classical_complete_translation:0001:0002"
    prompt = "继续完整翻译"
    state_path = tmp_path / classical.OPENAI_BACKGROUND_STATE_FILE
    state_path.write_text(
        json.dumps(
            {
                action: {
                    "action": action,
                    "prompt_sha256": classical._sha(prompt),  # noqa: SLF001
                    "response_id": "resp_already_billed",
                    "status": "polling",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_openai_call(_prompt: str, **kwargs):
        captured.update(kwargs)
        return "已恢复的完整译文", {"response_id": "resp_already_billed", "total_tokens": 9}

    monkeypatch.setattr(content_engine, "_openai_writer_call_result", fake_openai_call)
    monkeypatch.setattr(classical.settings, "OPENAI_WRITER_MODEL", "gpt-5.6-sol")
    monkeypatch.setattr(
        classical.settings, "CLASSICAL_TRANSLATION_WRITER_PROVIDER", "openai"
    )

    output = classical._call_translation_writer(  # noqa: SLF001
        tmp_path,
        action=action,
        prompt=prompt,
        temperature=0.1,
        max_output_tokens=0,
    )

    assert output == "已恢复的完整译文"
    assert captured["background_response_id"] == "resp_already_billed"


def test_classical_connection_failure_is_never_watchdog_replayed() -> None:
    config = {
        "public_domain_classical_vernacular": True,
        "generation_mode": "classical_vernacular",
    }
    assert task_jobs._is_classical_paid_delivery_uncertain(  # noqa: SLF001
        config, "OpenAI APIConnectionError: Connection error"
    )
    assert not task_jobs._is_classical_paid_delivery_uncertain(  # noqa: SLF001
        config, "译文缺失原文锚点"
    )
    assert not task_jobs._is_classical_paid_delivery_uncertain(  # noqa: SLF001
        {"generation_mode": "research_preview"},
        "OpenAI APIConnectionError: Connection error",
    )


def test_translation_writer_rejects_non_openai_provider_before_paid_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        classical.settings, "CLASSICAL_TRANSLATION_WRITER_PROVIDER", "deepseek"
    )
    with pytest.raises(longform.PublicDomainValidationError, match="固定使用 OpenAI gpt-5.6-sol"):
        classical._call_translation_writer(  # noqa: SLF001
            tmp_path,
            action="classical_complete_translation:0001:0001",
            prompt="逐句完整翻译，同一份已渲染 Prompt",
            temperature=0.1,
            max_output_tokens=0,
        )
    assert not (tmp_path / "llm_calls.jsonl").exists()


def test_openai_responses_request_receives_exact_rendered_classical_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeResponses:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(id="resp_native", status="completed")

    fake_client = SimpleNamespace(responses=FakeResponses())
    monkeypatch.setattr(content_engine, "_get_openai_writer_client", lambda: fake_client)
    monkeypatch.setattr(content_engine.settings, "OPENAI_WRITER_MODEL", "gpt-5.6-sol")
    rendered_prompt = "固定翻译契约\n【用户本次可编辑的翻译要求】\n逐句翻译，不增不减。"

    content_engine._create_openai_writer_response(  # noqa: SLF001
        rendered_prompt,
        effort="xhigh",
        verb="low",
        max_tok=None,
        service_tier="default",
        background=True,
    )

    assert captured["model"] == "gpt-5.6-sol"
    assert captured["input"] == rendered_prompt
    assert captured["reasoning"] == {"effort": "xhigh"}
    assert captured["text"] == {"verbosity": "low"}
    assert captured["background"] is True
    assert captured["store"] is True
    assert "instructions" not in captured


def test_xhigh_translation_uses_adaptive_semantic_pages_without_output_cap() -> None:
    assert classical.TRANSLATION_WRITER_REASONING_EFFORT == "xhigh"
    assert classical.SEGMENT_CHARS == 12_000
    assert classical._output_tokens(4_200) == 0  # noqa: SLF001

    short_chapter = "甲曰今日当行。乙曰尚可再议。"
    assert classical._adaptive_segment_limit(short_chapter) == 600  # noqa: SLF001
    assert classical.split_source_segments(short_chapter) == [short_chapter]

    long_chapter = "甲曰今日当行。乙曰尚可再议。" * 1_200
    adaptive_limit = classical._adaptive_segment_limit(long_chapter)  # noqa: SLF001
    assert 600 <= adaptive_limit <= classical.SEGMENT_CHARS
    pages = classical._split_for_context(long_chapter, adaptive_limit)  # noqa: SLF001
    assert "".join(pages) == long_chapter
    assert len(pages) > 1


def test_unheaded_full_book_is_paged_serially_and_publishes_only_after_full_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_call(_work_dir: Path, *, action: str, prompt: str, **_kwargs) -> str:
        calls.append(action)
        return _echo_marked_source(prompt)

    monkeypatch.setattr(classical, "_call_translation_writer", fake_call)
    source = _source(with_headings=False, repeats=400)
    result = longform.run_public_domain_longform(
        sources=[source],
        work_dir=tmp_path,
        generation_mode=longform.CLASSICAL_VERNACULAR_MODE,
        public_domain_confirmed=True,
        adaptation_profile={
            "scope_mode": "complete",
            "custom_prompt": "逐句完整翻译，保留人名、数字与先后顺序。",
        },
    )

    assert len(result.chapters) == 1
    assert len(calls) > 1
    assert calls == [
        f"classical_complete_translation:0001:{index:04d}"
        for index in range(1, len(calls) + 1)
    ]
    assert result.chapters[0].audit["processed_source_chars"] == longform.count_text_chars(
        source.text
    )
    assert result.chapters[0].audit["segments"] == len(calls)
    assert result.chapters[0].audit["prompt_injection_segments"] == len(calls)
    manifest = json.loads(
        (tmp_path / "05_classical_coverage_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "complete"
    assert manifest["whole_book_complete"] is True
    assert manifest["processed_source_chars"] == manifest["required_source_chars"]
    assert manifest["translated_segments"] == manifest["prompt_injection_segments"]
    assert manifest["writer"] == {
        "provider": "openai",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "xhigh",
        "max_output_tokens": 0,
    }
