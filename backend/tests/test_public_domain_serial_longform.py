from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services import public_domain_longform as legacy
from app.services import public_domain_rewrite as simple
from app.services import public_domain_serial_longform as serial


def _legacy_sources(count: int = 5) -> list[legacy.PublicDomainSource]:
    return [
        legacy.PublicDomainSource(
            title=f"公版来源{index}",
            text=(f"来源{index}记录一则古代人物处境和事情经过。" * 400),
            author="佚名",
            publication_year=1900,
            rights_note="公共领域，已人工核对",
        )
        for index in range(1, count + 1)
    ]


def _short_sources(count: int = 5) -> list[simple.PublicDomainSource]:
    return [
        simple.PublicDomainSource(
            title=f"公版来源{index}",
            text=(f"来源{index}保留古代故事事实线索。" * 400),
            author="佚名",
            publication_year=1900,
            rights_note="公共领域，已人工核对",
        )
        for index in range(1, count + 1)
    ]


def test_serial_cleaner_removes_all_dash_punctuation_and_modern_anecdote() -> None:
    raw = "第一章\n老人——停了一会儿—才继续说。阿瑟刚当上军官，住在办公室。余下的传统故事仍在。"

    cleaned = serial._clean_chapter_text(raw, "第一章")  # noqa: SLF001

    assert "—" not in cleaned
    assert "–" not in cleaned
    assert "阿瑟" not in cleaned
    assert "办公室" not in cleaned
    assert "传统故事" in cleaned


def test_serial_plan_has_exact_chapter_and_character_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sources = _legacy_sources()
    reports = legacy.build_source_reports(sources)
    dimensions = legacy.BookDimensions(
        target_chars=12_003,
        target_chapters=3,
        median_source_chars=10_000,
        median_source_chapters=None,
    )
    payload = {
        "title": "新书——不应残留",
        "summary": "简介—继续",
        "central_thread": "中心线索–推进",
        "chapters": [
            {
                "number": index,
                "title": f"第{index}章——章名",
                "purpose": "推进故事",
                "focus": f"处境{index}",
                "material_queries": ["传统故事"],
                "avoid_repeat": "不重复前章",
                "source_indexes": [index, index + 1],
            }
            for index in range(1, 4)
        ],
    }
    calls: list[str] = []

    def fake_call(*_args, **kwargs):
        calls.append(str(kwargs["action"]))
        return json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(legacy, "_call_llm", fake_call)
    mode = simple.SourceModeProfile("narrative", "故事叙事型", ())
    kwargs = {
        "work_dir": tmp_path,
        "reports": reports,
        "dimensions": dimensions,
        "source_briefs": "五本来源的抽样材料",
        "source_mode": mode,
        "mode_guidance": "故事为主，说理为辅",
        "target_title": None,
        "target_direction": None,
        "output_style": None,
    }

    first = serial._build_or_resume_plan(**kwargs)  # noqa: SLF001
    second = serial._build_or_resume_plan(**kwargs)  # noqa: SLF001

    assert calls == ["serial_book_plan"]
    assert first == second
    assert len(first["chapters"]) == 3
    assert sum(item["target_chars"] for item in first["chapters"]) == 12_003
    assert all(len(item["source_indexes"]) >= 3 for item in first["chapters"])
    assert "—" not in json.dumps(first, ensure_ascii=False)
    assert "–" not in json.dumps(first, ensure_ascii=False)


def test_serial_chapter_checkpoint_reuses_completed_text_without_second_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paragraphs = [
        f"第{index}段写人物面对的处境，事情沿着已有事实向前推进，随后留下自然的停顿和余意。"
        for index in range(1, 31)
    ]
    body = "\n\n".join(paragraphs)
    target = legacy.count_text_chars(body)
    calls: list[str] = []

    def fake_call(*_args, **kwargs):
        calls.append(str(kwargs["action"]))
        return body

    monkeypatch.setattr(legacy, "_call_llm", fake_call)
    sources = _short_sources()
    contract = {
        "number": 1,
        "title": "开篇",
        "purpose": "让故事自然出现",
        "focus": "人物的选择",
        "source_indexes": [1, 2, 3],
        "target_chars": target,
    }
    plan = {
        "title": "新书",
        "summary": "简介",
        "central_thread": "中心线索",
        "chapters": [contract],
    }
    kwargs = {
        "work_dir": tmp_path,
        "number": 1,
        "contract": contract,
        "plan": plan,
        "sources": sources,
        "previous_summaries": [],
        "source_mode": simple.SourceModeProfile("narrative", "故事叙事型", ()),
        "mode_guidance": "故事为主，说理为辅",
    }

    first = serial._build_or_resume_chapter(**kwargs)  # noqa: SLF001
    second = serial._build_or_resume_chapter(**kwargs)  # noqa: SLF001

    assert calls == ["serial_chapter_write:001"]
    assert first.text == second.text == simple._normalize_source_text(body)  # noqa: SLF001
    assert first.audit["pass"] is True
    assert first.audit["local_report"]["dash_count"] == 0


def test_serial_prompt_templates_contain_no_prohibited_dash_character() -> None:
    prompt_dir = PROJECT_ROOT / "app" / "prompts" / "public_domain_rewrite"
    for name in ("longform_plan.md", "longform_chapter.md", "longform_continue.md"):
        text = (prompt_dir / name).read_text(encoding="utf-8")
        assert "—" not in text
        assert "–" not in text


def test_story_dialogue_with_wo_jiang_is_not_ai_residue() -> None:
    text = "比丘说：\"我将这持戒的功德回向给那条龙，它便自然被降伏了。\""

    report = simple._quality_check(text, _short_sources())  # noqa: SLF001

    assert report["residue_issues"] == []


def test_model_process_wo_jiang_is_still_blocked() -> None:
    text = "我将根据要求整理全文，并输出以下内容。"

    report = simple._quality_check(text, _short_sources())  # noqa: SLF001

    assert "我将" in report["residue_issues"]


def test_failed_candidate_is_rechecked_before_any_paid_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    body = "\n\n".join(
        f"第{index}段保留传统故事的处境、行动和结果，让叙述自然向前推进。"
        for index in range(1, 80)
    )
    target = legacy.count_text_chars(body)
    calls: list[str] = []
    blocked = {"value": True}

    def fake_call(*_args, **kwargs):
        calls.append(str(kwargs["action"]))
        return body

    def fake_quality(*_args, **_kwargs):
        return {
            "status": "needs_review" if blocked["value"] else "ok",
            "warnings": ["暂时误报"] if blocked["value"] else [],
            "overlap_issues": [],
            "modern_narrative_issues": [],
            "residue_issues": [],
        }

    monkeypatch.setattr(legacy, "_call_llm", fake_call)
    monkeypatch.setattr(simple, "check_public_domain_manuscript", fake_quality)
    sources = _short_sources()
    contract = {
        "number": 1,
        "title": "持戒故事",
        "purpose": "让故事自然出现",
        "focus": "人物的选择",
        "source_indexes": [1, 2, 3],
        "target_chars": target,
    }
    kwargs = {
        "work_dir": tmp_path,
        "number": 1,
        "contract": contract,
        "plan": {
            "title": "新书",
            "summary": "简介",
            "central_thread": "中心线索",
            "chapters": [contract],
        },
        "sources": sources,
        "previous_summaries": [],
        "source_mode": simple.SourceModeProfile("narrative", "故事叙事型", ()),
        "mode_guidance": "故事为主，说理为辅",
    }

    with pytest.raises(legacy.PublicDomainQualityError, match="暂时误报"):
        serial._build_or_resume_chapter(**kwargs)  # noqa: SLF001
    blocked["value"] = False
    chapter = serial._build_or_resume_chapter(**kwargs)  # noqa: SLF001

    assert calls == ["serial_chapter_write:001"]
    assert chapter.audit["pass"] is True
    assert chapter.audit["reused_failed_candidate"] is True


def test_naturally_short_chapter_can_pass_without_padding() -> None:
    target = 10_000
    body = "\n\n".join(
        f"第{index}段让人物处境与行动自然展开，不为整齐字数强行补写。"
        for index in range(1, 140)
    )
    while legacy.count_text_chars(body) < 7_050:
        body += "叙述保留必要余地。"

    issues, report = serial._chapter_local_issues(  # noqa: SLF001
        text=body,
        target_chars=target,
        sources=_short_sources(),
        previous_chapters=Path("missing"),
        number=1,
    )

    assert report["chars"] >= 7_000
    assert not any("有效字数" in issue for issue in issues)
