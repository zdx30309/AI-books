from __future__ import annotations

from dataclasses import asdict
from io import BytesIO
import json
from pathlib import Path
import sys
import zipfile

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services import public_domain_longform as service
from app.services.public_domain_longform import (
    BookDimensions,
    ChapterResult,
    PublicDomainSource,
    PublicDomainValidationError,
    build_source_reports,
    check_final_manuscript,
    derive_dimensions,
    parse_final_markdown,
    render_final_markdown,
    validate_sources,
)
from app.utils.epub_text import extract_epub_text


def _source(index: int, *, chapters: int = 20, chars: int = 12_000) -> PublicDomainSource:
    heading_blocks = []
    for chapter in range(1, chapters + 1):
        heading_blocks.append(
            f"第{chapter}章 资料{index}-{chapter}\n"
            f"来源{index}记录事项{chapter}，时间范围、人物归属和条件都写在这一段。"
        )
    seed = "\n\n".join(heading_blocks)
    filler = (f"来源{index}的独立材料包含限定条件与事实边界。" * ((chars // 20) + 1))
    return PublicDomainSource(
        title=f"来源书{index}",
        author=f"作者{index}",
        publication_year=1900 + index,
        author_death_year=1930 + index,
        rights_note="公共领域，版权保护期届满，已人工核对",
        rights_evidence_url=f"https://example.test/public-domain/{index}",
        text=(seed + "\n\n" + filler)[:chars],
    )


def _sources(count: int = 8, **kwargs) -> list[PublicDomainSource]:
    return [_source(index, **kwargs) for index in range(1, count + 1)]


def test_requires_eight_to_ten_sources_before_llm() -> None:
    with pytest.raises(PublicDomainValidationError, match="8 到 10"):
        validate_sources(_sources(7), public_domain_confirmed=True)
    with pytest.raises(PublicDomainValidationError, match="8 到 10"):
        validate_sources(_sources(11), public_domain_confirmed=True)


def test_research_preview_accepts_five_sources_without_claiming_public_domain() -> None:
    items = _sources(5)
    items[0] = PublicDomainSource(
        **{**asdict(items[0]), "text": items[0].text + "\n版权所有，未经许可不得转载"}
    )
    cleaned = validate_sources(
        items,
        public_domain_confirmed=False,
        generation_mode=service.RESEARCH_PREVIEW_MODE,
        research_use_confirmed=True,
    )
    assert len(cleaned) == 5


def test_research_preview_requires_explicit_research_confirmation() -> None:
    with pytest.raises(PublicDomainValidationError, match="事实/概念线索"):
        validate_sources(
            _sources(5),
            public_domain_confirmed=False,
            generation_mode=service.RESEARCH_PREVIEW_MODE,
            research_use_confirmed=False,
        )


def test_research_preview_defaults_to_three_chapters_and_ten_thousand_chars() -> None:
    dimensions = derive_dimensions(_sources(5), generation_mode=service.RESEARCH_PREVIEW_MODE)
    assert dimensions.target_chars == 10_000
    assert dimensions.target_chapters == 3


def test_research_preview_auditor_falls_back_when_gemini_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(service, "_call_gemini", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("429")))
    monkeypatch.setattr(service, "_call_openai_auditor", lambda *_args, **_kwargs: '{"pass": true}')
    result = service._call_auditor(  # noqa: SLF001
        tmp_path,
        action="test-audit",
        prompt="audit",
        temperature=0.0,
        max_output_tokens=100,
        generation_mode=service.RESEARCH_PREVIEW_MODE,
    )
    assert result == '{"pass": true}'


def test_research_preview_audit_falls_back_when_provider_returns_non_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(service, "_call_auditor", lambda *_args, **_kwargs: "无法按要求返回结果")
    fallback_actions: list[str] = []

    def _valid_fallback(*_args, **kwargs) -> str:
        fallback_actions.append(str(kwargs.get("action") or ""))
        return json.dumps(
            {
                "pass": True,
                "unsupported_claims": [],
                "contradictions": [],
                "missing_materials": [],
                "language_issues": [],
                "repair_instructions": [],
                "fact_sentence_count": 1,
                "mapped_fact_sentence_count": 1,
                "evidence_map": [{"fact": "候选正文", "claim_ids": ["P01-V0001"]}],
            }
        )

    monkeypatch.setattr(service, "_call_openai_auditor", _valid_fallback)
    result = service._audit_chapter(  # noqa: SLF001
        work_dir=tmp_path,
        chapter_number=1,
        contract={"title": "第一章", "must_cover": []},
        allowed_evidence=[{"claim_id": "P01-V0001"}],
        chapter_text="候选正文",
        generation_mode=service.RESEARCH_PREVIEW_MODE,
    )

    assert result["pass"] is True
    assert fallback_actions == ["chapter_audit:001:json_fallback"]
    log_rows = [json.loads(line) for line in (tmp_path / "llm_calls.jsonl").read_text().splitlines()]
    assert log_rows[-1]["status"] == "malformed_output"


def test_research_preview_repairs_accumulate_from_safe_shorter_candidate() -> None:
    stale_full = {"text": "旧" * 1_000}
    repaired = {"text": "新" * 650}
    stale_audit = {"repair_instructions": ["旧问题"]}
    current_audit = {"repair_instructions": ["只剩新问题"]}

    source, audit = service._select_chapter_repair_source(  # noqa: SLF001
        candidate=repaired,
        audit=current_audit,
        best_full_candidate=stale_full,
        best_full_audit=stale_audit,
        target_chars=1_000,
        generation_mode=service.RESEARCH_PREVIEW_MODE,
    )

    assert source is repaired
    assert audit is current_audit


def test_research_preview_rolls_back_truncated_repair_candidate() -> None:
    stale_full = {"text": "旧" * 1_000}
    truncated = {"text": "新" * 100}
    stale_audit = {"repair_instructions": ["旧问题"]}

    source, audit = service._select_chapter_repair_source(  # noqa: SLF001
        candidate=truncated,
        audit={"repair_instructions": ["截断"]},
        best_full_candidate=stale_full,
        best_full_audit=stale_audit,
        target_chars=1_000,
        generation_mode=service.RESEARCH_PREVIEW_MODE,
    )

    assert source is stale_full
    assert audit is stale_audit


def test_research_preview_repair_uses_openai_writer_directly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        service,
        "_call_llm",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("max tokens")),
    )
    fallback_actions: list[str] = []

    def _fallback(*_args, **kwargs) -> str:
        fallback_actions.append(str(kwargs.get("action") or ""))
        return '{"title":"第三章","text":"正文","summary":"摘要","used_claim_ids":[]}'

    monkeypatch.setattr(service, "_call_openai_preview_writer", _fallback)
    result = service._call_chapter_repair_model(  # noqa: SLF001
        tmp_path,
        action="chapter_repair:003:attempt1",
        prompt="repair",
        max_output_tokens=6_000,
        generation_mode=service.RESEARCH_PREVIEW_MODE,
        prefer_openai=False,
    )

    assert json.loads(result)["title"] == "第三章"
    assert fallback_actions == ["chapter_repair:003:attempt1"]


def test_research_preview_source_gate_uses_global_diversity_not_every_title() -> None:
    reports = tuple(
        service.SourceReport(
            source_id=f"P{index:02d}",
            title=f"原典{index}",
            author="古代",
            chars=1_000,
            detected_chapters=1,
            rights_basis="verified primary",
            sha256=str(index),
        )
        for index in range(1, 20)
    )
    used_claim_ids = {
        f"P{source:02d}-V{claim:04d}"
        for source in range(1, 7)
        for claim in range(1, 3)
    }
    used_source_ids = {value.split("-", 1)[0] for value in used_claim_ids}

    issues = service._source_contribution_issues(  # noqa: SLF001
        source_reports=reports,
        source_usage={report.source_id: (2 if report.source_id in used_source_ids else 0) for report in reports},
        source_chapter_usage={
            report.source_id: ({1} if report.source_id in used_source_ids else set()) for report in reports
        },
        used_claim_ids=used_claim_ids,
        used_source_ids=used_source_ids,
        generation_mode=service.RESEARCH_PREVIEW_MODE,
        verified_claim_count=25,
    )

    assert issues == []


def test_research_preview_source_gate_rejects_too_little_global_diversity() -> None:
    reports = tuple(
        service.SourceReport(
            source_id=f"P{index:02d}",
            title=f"原典{index}",
            author="古代",
            chars=1_000,
            detected_chapters=1,
            rights_basis="verified primary",
            sha256=str(index),
        )
        for index in range(1, 8)
    )
    used_claim_ids = {"P01-V0001", "P02-V0001", "P03-V0001"}

    issues = service._source_contribution_issues(  # noqa: SLF001
        source_reports=reports,
        source_usage={report.source_id: 0 for report in reports},
        source_chapter_usage={report.source_id: set() for report in reports},
        used_claim_ids=used_claim_ids,
        used_source_ids={"P01", "P02", "P03"},
        generation_mode=service.RESEARCH_PREVIEW_MODE,
        verified_claim_count=18,
    )

    assert len(issues) == 2


def test_public_domain_success_clears_stale_manual_review_flags() -> None:
    from app.tasks import tasks as task_service

    cleaned = task_service._clear_public_domain_failure_state(  # noqa: SLF001
        {
            "public_domain_quality_blocked": True,
            "public_domain_quality_reason": "旧失败原因",
            "auto_requeue_reason": "stale",
            "auto_requeued_at": "yesterday",
            "status": "ok",
        }
    )

    assert cleaned["public_domain_quality_blocked"] is False
    assert "public_domain_quality_reason" not in cleaned
    assert "auto_requeue_reason" not in cleaned
    assert "auto_requeued_at" not in cleaned
    assert cleaned["status"] == "ok"


def test_rights_basis_is_mandatory() -> None:
    items = _sources()
    items[3] = PublicDomainSource(title="权利不清", text=items[3].text, rights_note="网上下载")
    with pytest.raises(PublicDomainValidationError, match="公版依据"):
        validate_sources(items, public_domain_confirmed=True)


def test_rejects_restricted_copyright_marker() -> None:
    items = _sources()
    items[0] = PublicDomainSource(
        **{**asdict(items[0]), "text": items[0].text + "\n版权所有，未经许可不得转载"}
    )
    with pytest.raises(PublicDomainValidationError, match="版权限制标记"):
        validate_sources(items, public_domain_confirmed=True)


def test_rejects_duplicate_books_as_fake_source_count() -> None:
    items = _sources()
    items[7] = PublicDomainSource(**{**asdict(items[7]), "text": items[0].text})
    with pytest.raises(PublicDomainValidationError, match="正文完全相同"):
        validate_sources(items, public_domain_confirmed=True)


def test_dimensions_follow_reference_medians() -> None:
    items = [_source(index, chapters=18 + index, chars=100_000 + index * 2_000) for index in range(1, 9)]
    dimensions = derive_dimensions(items)
    assert 108_000 <= dimensions.target_chars <= 110_000
    assert dimensions.target_chapters in {22, 23}
    assert abs(dimensions.median_source_chars - dimensions.target_chars) <= 500


def test_plan_normalization_enforces_exact_length_and_every_source_twice() -> None:
    reports = build_source_reports(_sources())
    dimensions = BookDimensions(
        target_chars=96_000,
        target_chapters=12,
        median_source_chars=96_000,
        median_source_chapters=20,
    )
    raw = {
        "title": "独立融合书",
        "summary": "简介",
        "chapters": [
            {
                "number": index,
                "title": f"第{index}章",
                "purpose": "推进问题",
                "source_ids": ["S01", "S02"],
                "topics": ["主题"],
                "must_cover": ["事实"],
            }
            for index in range(1, 13)
        ],
    }
    plan = service._normalize_plan(raw, reports, dimensions, None)  # noqa: SLF001
    assert len(plan["chapters"]) == 12
    assert sum(item["target_chars"] for item in plan["chapters"]) == 96_000
    for source_id in [f"S{index:02d}" for index in range(1, 9)]:
        assert sum(source_id in item["source_ids"] for item in plan["chapters"]) >= 2


def test_preview_plan_requires_evidence_bound_narrative_nonfiction() -> None:
    reports = build_source_reports(_sources(5))
    dimensions = BookDimensions(
        target_chars=10_000,
        target_chapters=3,
        median_source_chars=10_000,
        median_source_chapters=3,
    )
    raw = {
        "title": "言语留下的路",
        "summary": "从古代原典已经确认的事实出发，观察言语、选择与修行之间的关系。",
        "central_question": "人在言语造成的后果面前如何重新选择",
        "book_premise": "只重组古代原典核验事实，不新增人物与事件。",
        "narrative_thread": "沿言语、心念和行为三条线逐章推进。",
        "chapters": [
            {
                "number": index,
                "title": f"雨声{index}",
                "purpose": "推进全书问题",
                "source_ids": ["S01", "S02"],
                "topics": ["言语"],
                "must_cover": ["已核验事实边界"],
                "must_not_claim": ["不冒充历史"],
                "narrative_goal": "让真实材料自然推进",
                "narrative_beats": [
                    {"description": "事实一", "claim_ids": ["S01-V0001"], "boundary": "不补心理"},
                    {"description": "事实二", "claim_ids": ["S02-V0001"], "boundary": "不补对话"},
                    {"description": "事实三", "claim_ids": ["S01-V0002"], "boundary": "不补因果"},
                    {"description": "事实四", "claim_ids": ["S02-V0002"], "boundary": "不补场景"},
                    {"description": "事实五", "claim_ids": ["S01-V0003"], "boundary": "不补关系"},
                    {"description": "事实六", "claim_ids": ["S02-V0003"], "boundary": "不补评价"},
                ],
                "evidence_function": "形成事实推进，不逐条讲解",
            }
            for index in range(1, 4)
        ],
    }

    plan = service._normalize_plan(  # noqa: SLF001
        raw,
        reports,
        dimensions,
        None,
        generation_mode=service.RESEARCH_PREVIEW_MODE,
        allowed_claim_ids={
            "S01-V0001",
            "S01-V0002",
            "S01-V0003",
            "S02-V0001",
            "S02-V0002",
            "S02-V0003",
        },
    )

    assert plan["narrative_mode"] == "verified_narrative_nonfiction"
    assert "fictional_characters" not in plan
    assert all(len(item["narrative_beats"]) == 6 for item in plan["chapters"])
    assert all(item["narrative_beats"][0]["claim_ids"] for item in plan["chapters"])


def test_preview_plan_fills_missing_beats_only_from_verified_claim_catalog() -> None:
    reports = build_source_reports(_sources(5))
    dimensions = BookDimensions(10_000, 2, 10_000, 2)
    raw = {
        "title": "言语留下的路",
        "book_premise": "只重组已经核验的古代原典事实。",
        "narrative_thread": "沿不同原典事实所呈现的选择与后果逐章推进。",
        "chapters": [
            {
                "title": f"第{index}章",
                "source_ids": ["S01", "S02"],
                "narrative_beats": [
                    {
                        "description": "模型唯一完整返回的推进点",
                        "claim_ids": ["S01-V0001"],
                        "boundary": "不补写",
                    }
                ],
            }
            for index in range(1, 3)
        ],
    }
    catalog = {
        "S01-V0001": {"source_id": "S01", "statement": "已核验事实一", "qualifier": ""},
        "S01-V0002": {"source_id": "S01", "statement": "已核验事实二", "qualifier": "限定二"},
        "S02-V0001": {"source_id": "S02", "statement": "已核验事实三", "qualifier": ""},
        "S03-V0001": {"source_id": "S03", "statement": "已核验事实四", "qualifier": ""},
        "S04-V0001": {"source_id": "S04", "statement": "已核验事实五", "qualifier": ""},
        "S05-V0001": {"source_id": "S05", "statement": "已核验事实六", "qualifier": ""},
    }

    plan = service._normalize_plan(  # noqa: SLF001
        raw,
        reports,
        dimensions,
        None,
        generation_mode=service.RESEARCH_PREVIEW_MODE,
        allowed_claim_ids=set(catalog),
        claim_catalog=catalog,
    )

    assert all(len(chapter["narrative_beats"]) == 6 for chapter in plan["chapters"])
    added = plan["chapters"][0]["narrative_beats"][1:]
    assert {item["description"] for item in added} <= {
        "已核验事实二",
        "已核验事实三",
        "已核验事实四",
        "已核验事实五",
        "已核验事实六",
    }
    assert all(item["claim_ids"][0] in catalog for item in added)


def test_preview_plan_rejects_fictional_people_and_events() -> None:
    reports = build_source_reports(_sources(5))
    dimensions = BookDimensions(10_000, 2, 10_000, 2)
    raw = {
        "title": "错误计划",
        "book_premise": "虚构故事",
        "narrative_thread": "虚构推进",
        "fictional_characters": [{"name": "某甲"}],
        "chapters": [{"title": "一"}, {"title": "二"}],
    }

    with pytest.raises(service.PublicDomainQualityError, match="不得包含虚构"):
        service._normalize_plan(  # noqa: SLF001
            raw,
            reports,
            dimensions,
            None,
            generation_mode=service.RESEARCH_PREVIEW_MODE,
        )


def test_narrative_profile_contains_only_anonymous_aggregate_statistics(tmp_path: Path) -> None:
    items = _sources(5)
    reports = build_source_reports(items)
    profile = service._build_narrative_mechanism_profile(items, reports, tmp_path)  # noqa: SLF001
    serialized = json.dumps(profile, ensure_ascii=False)

    assert profile["version"] == service.NARRATIVE_PROFILE_VERSION
    assert profile["contains_raw_text"] is False
    assert profile["contains_source_identity"] is False
    assert all(item.title not in serialized and item.author not in serialized for item in items)
    assert "来源1记录事项" not in serialized


def test_narrative_nonfiction_gate_rejects_paper_and_source_catalogue_voice() -> None:
    text = (
        "本章依据《十不善业道经》展开讨论。原典所能确认的是妄语位于语业之首。"
        "据《华严经》记载，本章讨论另一组次序。综合上述，可以确认本章结论。"
    ) * 20

    issues = service._narrative_nonfiction_form_issues(text)  # noqa: SLF001

    assert any("论文/资料汇编" in item for item in issues)
    assert any("书名号" in item for item in issues)


def test_narrative_nonfiction_gate_accepts_plain_independent_prose_without_quotes() -> None:
    text = "\n".join(
        [
            "事情从一次已经发生的选择开始，言语出口之后，人的处境也随之改变。",
            "另一个人停住了原本会造成伤害的行为，选择因此有了看得见的分量。",
            "有些转折来得很快，前一刻仍在犹疑，下一刻便要承担自己的决定。",
            "长句把前因慢慢铺开，短句只留下结果。没有人替当事人作答。",
            "读者看到的是几种不同选择，它们并排存在，又各自通向不同后果。",
            "到了最后，最具体的行动仍留在那里，判断反而可以稍后再来。",
            "那个人停下脚步。事情也就在这个动作上换了方向。",
        ]
    ) * 40

    assert service._narrative_nonfiction_form_issues(text) == []  # noqa: SLF001


def test_narrative_nonfiction_gate_rejects_visible_safety_boundary_commentary() -> None:
    text = (
        "这一处材料只留下题下署名，文字没有展开当时场景。"
        "同一处材料也没有补写人物心理，事实边界停在传本位置。"
    ) * 40

    issues = service._narrative_nonfiction_form_issues(text)  # noqa: SLF001

    assert any("论文/资料汇编" in item for item in issues)


def test_whole_book_form_gate_does_not_treat_one_title_per_chapter_as_single_chapter_overuse() -> None:
    text = ("第一章偶尔出现《甲经》，其余正文自然推进。\n" * 80) + (
        "第二章偶尔出现《乙经》，其余正文自然推进。\n" * 80
    )

    issues = service._narrative_nonfiction_form_issues(text, whole_book=True)  # noqa: SLF001

    assert not any("单章出现" in item for item in issues)


def test_narrative_nonfiction_gate_rejects_direct_dialogue_without_quote_evidence() -> None:
    text = ("事情已经有记录。\n“我当时就是这样想的。”\n其余事实仍按边界叙述。\n" * 200)

    issues = service._narrative_nonfiction_form_issues(text)  # noqa: SLF001

    assert any("直接引语" in item for item in issues)


def test_narrative_beats_force_exact_claims_into_chapter_evidence() -> None:
    evidence = [
        {
            "claim_id": f"P01-V{index:04d}",
            "source_id": "P01",
            "statement": "普通材料" if index < 20 else "与章节主题完全无关但被计划明确绑定",
            "qualifier": "",
            "importance": 1,
        }
        for index in range(1, 21)
    ]
    reports = (
        service.SourceReport("P01", "古代原典", "古代原典", 0, 0, "古代原典", "hash"),
    )
    contract = {
        "number": 1,
        "title": "普通主题",
        "source_ids": ["P01"],
        "narrative_beats": [
            {"description": "必须使用的推进点", "claim_ids": ["P01-V0020"]}
        ],
    }

    selected = service._select_chapter_evidence(contract, evidence, reports)  # noqa: SLF001

    assert selected[0]["claim_id"] == "P01-V0020"


def test_chapter_gate_rejects_missing_planned_claim_mapping() -> None:
    contract = {
        "narrative_beats": [
            {"description": "必须覆盖", "claim_ids": ["P01-V0005"]}
        ]
    }
    allowed = [
        {
            "claim_id": f"P01-V{index:04d}",
            "evidence_status": "verified_primary",
            "generation_eligible": True,
        }
        for index in range(1, 6)
    ]

    issues = service._chapter_deterministic_issues(  # noqa: SLF001
        text="一段只用于验证证据覆盖的正文。" * 20,
        contract=contract,
        target_chars=260,
        used_claim_ids=("P01-V0001", "P01-V0002", "P01-V0003", "P01-V0004"),
        allowed_evidence=allowed,
        overlap_indexes=[],
        require_narrative_nonfiction=True,
    )

    assert any("P01-V0005" in item for item in issues)


def test_exact_source_copy_is_blocked_but_independent_text_is_not() -> None:
    items = _sources()
    copied = check_final_manuscript(items[0].text, items)
    independent = check_final_manuscript("完全独立的讨论材料与组织方式。" * 500, items)
    assert copied["status"] == "needs_repair"
    assert copied["blocked_overlap"]
    assert independent["status"] == "ok"


def test_final_markdown_preserves_real_chapter_structure() -> None:
    chapters = [
        ChapterResult(
            number=index,
            title=f"章节{index}",
            text=f"正文{index}" * 100,
            summary=f"摘要{index}",
            used_claim_ids=(f"S01-C0001-{index:02d}",),
            audit={"pass": True},
        )
        for index in range(1, 13)
    ]
    markdown = render_final_markdown(title="融合长篇", summary="简介", chapters=chapters)
    title, summary, parsed = parse_final_markdown(markdown)
    assert title == "融合长篇"
    assert summary == "简介"
    assert len(parsed) == 12
    assert parsed[-1].text.startswith("正文12")


def test_rights_manifest_never_contains_raw_books(tmp_path: Path) -> None:
    items = _sources()
    reports = build_source_reports(items)
    dimensions = derive_dimensions(items, requested_chars=80_000, requested_chapters=12)
    service._write_rights_manifest(tmp_path, reports, items, dimensions)  # noqa: SLF001
    payload = json.loads((tmp_path / "00_rights_manifest.json").read_text(encoding="utf-8"))
    assert len(payload["sources"]) == 8
    assert all("text" not in item for item in payload["sources"])
    assert all(item["sha256"] for item in payload["sources"])


def test_all_longform_prompts_live_in_template_files() -> None:
    expected = {
        "source_evidence.md",
        "evidence_audit.md",
        "source_digest.md",
        "book_plan.md",
        "chapter_writer.md",
        "chapter_audit.md",
        "chapter_repair.md",
        "final_audit.md",
        "source_evidence_research_preview.md",
        "book_plan_research_preview.md",
        "chapter_writer_research_preview.md",
        "chapter_audit_research_preview.md",
        "chapter_repair_research_preview.md",
        "final_audit_research_preview.md",
        "primary_source_verification.md",
    }
    assert expected <= {path.name for path in service.PROMPT_DIR.glob("*.md")}


def test_preview_prompts_enforce_verified_narrative_without_modern_sources() -> None:
    plan_prompt = (service.PROMPT_DIR / "book_plan_research_preview.md").read_text(encoding="utf-8")
    writer_prompt = (service.PROMPT_DIR / "chapter_writer_research_preview.md").read_text(encoding="utf-8")
    audit_prompt = (service.PROMPT_DIR / "chapter_audit_research_preview.md").read_text(encoding="utf-8")

    assert '"narrative_mode": "verified_narrative_nonfiction"' in plan_prompt
    assert "多书聚合叙事统计" in plan_prompt
    assert "不是论文" in writer_prompt
    assert "不得创造人物、地点、事件、关系、对话" in plan_prompt
    assert "人物、地点、事件、时间、数字、动作、对话、心理" in writer_prompt
    assert "每个事实性句子都要能映射到允许证据" in audit_prompt
    assert "现代书名、作者、出版社" in writer_prompt
    assert "不得推测、复原或模仿任何单一来源" in plan_prompt


def test_preview_plan_cache_is_invalidated_by_narrative_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    reports = build_source_reports(_sources(5))
    dimensions = BookDimensions(10_000, 3, 10_000, 3)
    calls: list[str] = []

    def fake_plan(*_args, **kwargs) -> str:
        calls.append(kwargs["prompt"])
        return json.dumps(
            {
                "title": "言语留下的路",
                "summary": "从已核验古代事实出发，观察言语与行为的关系。",
                "central_question": "说出口的话如何改变关系",
                "book_premise": "只重组古代原典核验事实。",
                "narrative_thread": "沿言语、行为与后果推进。",
                "chapters": [
                    {
                        "title": f"第{index}夜",
                        "purpose": "推进问题",
                        "source_ids": ["S01", "S02"],
                        "topics": ["言语"],
                        "must_cover": ["事实边界"],
                        "must_not_claim": ["不冒充历史"],
                        "narrative_goal": "让真实材料自然推进",
                        "narrative_beats": [
                            {"description": "事实一", "claim_ids": ["S01-V0001"], "boundary": "不补心理"},
                            {"description": "事实二", "claim_ids": ["S02-V0001"], "boundary": "不补对话"},
                            {"description": "事实三", "claim_ids": ["S01-V0002"], "boundary": "不补因果"},
                            {"description": "事实四", "claim_ids": ["S02-V0002"], "boundary": "不补场景"},
                            {"description": "事实五", "claim_ids": ["S01-V0003"], "boundary": "不补关系"},
                            {"description": "事实六", "claim_ids": ["S02-V0003"], "boundary": "不补评价"},
                        ],
                        "evidence_function": "限制思想边界",
                    }
                    for index in range(1, 4)
                ],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(service, "_call_llm", fake_plan)
    kwargs = {
        "work_dir": tmp_path,
        "source_reports": reports,
        "source_digests": [
            {
                "source_id": "S01",
                "core_materials": [
                    {"claim_id": "S01-V0001", "statement": "事实一"},
                    {"claim_id": "S01-V0002", "statement": "事实三"},
                    {"claim_id": "S01-V0003", "statement": "事实五"},
                    {"claim_id": "S02-V0001", "statement": "事实二"},
                    {"claim_id": "S02-V0002", "statement": "事实四"},
                    {"claim_id": "S02-V0003", "statement": "事实六"},
                ],
            }
        ],
        "dimensions": dimensions,
        "target_title": None,
        "target_direction": None,
        "output_style": None,
        "generation_mode": service.RESEARCH_PREVIEW_MODE,
    }
    service._build_book_plan(**kwargs, narrative_profile={"sentence_chars": {"mean": 30}})  # noqa: SLF001
    service._build_book_plan(**kwargs, narrative_profile={"sentence_chars": {"mean": 30}})  # noqa: SLF001
    service._build_book_plan(**kwargs, narrative_profile={"sentence_chars": {"mean": 36}})  # noqa: SLF001

    assert len(calls) == 2


def test_post_edit_audit_reuses_only_exact_accepted_hash(tmp_path: Path) -> None:
    manuscript = "已通过的独立公版融合终稿"
    (tmp_path / "quality_report.json").write_text(
        json.dumps(
                {
                    "passed": True,
                    "policy_version": service.POLICY_VERSION,
                    "final_sha256": service.text_sha256(manuscript),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = service.audit_edited_final_manuscript(
        work_dir=tmp_path,
        manuscript=manuscript,
        sources=_sources(),
    )
    assert result["status"] == "ok"
    assert result["reused_original_audit"] is True


def test_old_quality_report_cannot_bypass_primary_evidence_policy(tmp_path: Path) -> None:
    manuscript = "旧策略曾经通过的研究试稿"
    (tmp_path / "quality_report.json").write_text(
        json.dumps(
            {
                "passed": True,
                "policy_version": "public-domain-longform-v2",
                "final_sha256": service.text_sha256(manuscript),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = service.audit_edited_final_manuscript(
        work_dir=tmp_path,
        manuscript=manuscript,
        sources=_sources(),
    )
    assert result["status"] == "needs_repair"
    assert result.get("reused_original_audit") is not True


def test_preview_quality_report_cannot_bypass_missing_primary_ledger(tmp_path: Path) -> None:
    manuscript = "当前策略曾通过、但原典核验账本已经丢失的试稿"
    (tmp_path / "00_rights_manifest.json").write_text(
        json.dumps({"generation_mode": service.RESEARCH_PREVIEW_MODE}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "quality_report.json").write_text(
        json.dumps(
            {
                "passed": True,
                "policy_version": service.POLICY_VERSION,
                "final_sha256": service.text_sha256(manuscript),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = service.audit_edited_final_manuscript(
        work_dir=tmp_path,
        manuscript=manuscript,
        sources=_sources(),
    )
    assert result["status"] == "needs_repair"
    assert result.get("reused_original_audit") is not True


def test_research_discovery_is_promoted_only_by_real_primary_tool_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    discoveries = []
    for index in range(1, 31):
        source_id = f"S{((index - 1) % 5) + 1:02d}"
        discoveries.append(
            {
                "claim_id": f"{source_id}-C{index:04d}-01",
                "source_id": source_id,
                "source_title": f"现代整理书{source_id}",
                "kind": "concept",
                "statement": f"待核验的佛教概念命题{index}具有明确边界",
                "qualifier": "",
                "importance": 3,
                "evidence_status": "discovery_only",
                "generation_eligible": False,
            }
        )

    canonical = ["《杂阿含经》", "《长阿含经》", "《法句经》"]
    urls = [
        "https://zh.wikisource.org/wiki/雜阿含經",
        "https://cbetaonline.dila.edu.tw/zh/T0001",
        "https://tripitaka.cbeta.org/T04n0210",
    ]

    def fake_verify(_work_dir: Path, *, action: str, prompt: str):
        batch = json.loads(prompt.split("待核验候选：", 1)[1].strip())
        verified = []
        for offset, item in enumerate(batch):
            slot = offset % 3
            verified.append(
                {
                    "candidate_ids": [item["candidate_id"]],
                    "kind": item["kind"],
                    "statement": item["statement"],
                    "qualifier": "",
                    "canonical_title": canonical[slot],
                    "canonical_locator": f"卷{offset + 1}",
                    "evidence_quote": "这是古代原典中的短证据",
                    "evidence_urls": [urls[slot]],
                }
            )
        return json.dumps({"verified_claims": verified}, ensure_ascii=False), [
            {"url": url, "title": title} for url, title in zip(urls, canonical)
        ]

    monkeypatch.setattr(service, "_call_openai_primary_verifier", fake_verify)
    monkeypatch.setattr(
        service,
        "_discover_primary_pages_for_batch",
        lambda batch: [
            {
                "candidate_ids": [str(item.get("claim_id") or "") for item in batch],
                "title": title,
                "url": url,
                "text": "这是古代原典中的短证据，且正文足够长。" * 10,
            }
            for title, url in zip(canonical, urls)
        ],
    )
    result = service._verify_research_evidence(  # noqa: SLF001
        work_dir=tmp_path,
        discovery_evidence=discoveries,
        modern_sources=_sources(5),
    )
    assert len(result) >= service.PREVIEW_MIN_VERIFIED_CLAIMS
    assert len({item["source_title"] for item in result}) == 3
    assert all(item["evidence_status"] == "verified_primary" for item in result)
    assert all(item["generation_eligible"] is True for item in result)
    assert all("现代整理书" not in item["source_title"] for item in result)
    ledger = json.loads((tmp_path / "01_verified_primary_ledger.json").read_text(encoding="utf-8"))
    assert ledger["evidence_status"] == "verified_primary"


def test_hallucinated_primary_url_is_not_promoted() -> None:
    batch = [
        {
            "claim_id": "S01-C0001-01",
            "source_id": "S01",
            "kind": "fact",
            "statement": "待核验命题具有足够长度",
            "importance": 3,
        }
    ]
    payload = {
        "verified_claims": [
            {
                "candidate_ids": ["S01-C0001-01"],
                "statement": "待核验命题具有足够长度",
                "canonical_title": "《法句经》",
                "canonical_locator": "卷一",
                "evidence_quote": "古代原典中的短证据",
                "evidence_urls": ["https://zh.wikisource.org/wiki/法句經"],
            }
        ]
    }
    result = service._normalize_verified_batch(  # noqa: SLF001
        payload=payload,
        batch=batch,
        tool_sources=[{"url": "https://zh.wikisource.org/wiki/雜阿含經", "title": "杂阿含经"}],
        modern_titles={"现代佛教故事"},
    )
    assert result == []


def test_primary_quote_must_exist_in_fetched_page() -> None:
    batch = [
        {
            "claim_id": "S01-C0001-01",
            "source_id": "S01",
            "kind": "fact",
            "statement": "待核验命题具有足够长度",
            "importance": 3,
        }
    ]
    url = "https://zh.wikisource.org/wiki/法句經"
    payload = {
        "verified_claims": [
            {
                "candidate_ids": ["S01-C0001-01"],
                "statement": "待核验命题具有足够长度",
                "canonical_title": "《法句经》",
                "canonical_locator": "卷一",
                "evidence_quote": "模型虚构、页面实际不存在的短证据",
                "evidence_urls": [url],
            }
        ]
    }
    result = service._normalize_verified_batch(  # noqa: SLF001
        payload=payload,
        batch=batch,
        tool_sources=[{"url": url, "title": "法句经"}],
        primary_pages=[{"url": url, "title": "法句经", "text": "页面只有另一段真实原典正文。" * 20}],
        modern_titles=set(),
    )
    assert result == []


def test_discovery_candidate_selection_prioritizes_explicit_primary_anchor() -> None:
    discoveries = [
        {
            "claim_id": "S01-C0001-01",
            "source_id": "S01",
            "statement": "清净心一般指不受杂念干扰的心理状态",
            "qualifier": "",
            "importance": 3,
            "evidence_status": "discovery_only",
            "generation_eligible": False,
        },
        {
            "claim_id": "S01-C0002-01",
            "source_id": "S01",
            "statement": "忍辱波罗蜜，如来说非忍辱波罗蜜，是名忍辱波罗蜜",
            "qualifier": "出自《金刚经》",
            "importance": 2,
            "evidence_status": "discovery_only",
            "generation_eligible": False,
        },
    ]
    selected = service._select_discovery_candidates(discoveries)  # noqa: SLF001
    assert selected[0]["claim_id"] == "S01-C0002-01"


def test_primary_search_tries_exact_canonical_title_before_broad_clauses() -> None:
    queries = service._primary_search_queries(  # noqa: SLF001
        {
            "statement": "忍辱波罗蜜，如来说非忍辱波罗蜜，是名忍辱波罗蜜",
            "qualifier": "出自《金刚经》",
        }
    )
    assert queries[0] == "title:金刚经"


def test_canonical_title_drops_trailing_edition_annotation() -> None:
    assert service._normalize_canonical_title("《大般涅槃经》三十六卷本》") == "《大般涅槃经》"  # noqa: SLF001


def test_successful_exact_title_lookup_skips_broad_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_search(query: str):
        calls.append(query)
        return [
            {
                "title": "金剛般若波羅蜜經",
                "url": "https://zh.wikisource.org/wiki/金剛般若波羅蜜經",
                "text": "如是我聞，古代原典正文。" * 20,
                "categories": ["大正新脩大藏經"],
            }
        ]

    monkeypatch.setattr(service, "_search_wikisource_pages", fake_search)
    pages = service._discover_primary_pages_for_batch(  # noqa: SLF001
        [
            {
                "claim_id": "S01-C0001-01",
                "statement": "忍辱波罗蜜，如来说非忍辱波罗蜜",
                "qualifier": "出自《金刚经》",
            }
        ]
    )
    assert calls == ["title:金刚经"]
    assert len(pages) == 1


def test_verifier_discovered_subpage_is_fetched_before_literal_quote_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://zh.wikisource.org/zh-hant/金剛般若波羅蜜經_(鳩摩羅什)"
    payload = {
        "verified_claims": [
            {
                "candidate_ids": ["S01-C0001-01"],
                "statement": "原典记载忍辱波罗蜜，如来说非忍辱波罗蜜",
                "canonical_title": "《金刚般若波罗蜜经》",
                "canonical_locator": "离相寂灭分第十四",
                "evidence_quote": "忍辱波羅蜜，如來說非忍辱波羅蜜",
                "evidence_urls": [url],
            }
        ]
    }
    monkeypatch.setattr(
        service,
        "_fetch_primary_page_by_url",
        lambda requested_url: {
            "title": "金剛般若波羅蜜經 (鳩摩羅什)",
            "text": "須菩提！忍辱波羅蜜，如來說非忍辱波羅蜜。" * 10,
            "categories": ["大正新脩大藏經"],
        }
        if requested_url == service._normalize_evidence_url(url)  # noqa: SLF001
        else None,
    )
    pages = service._hydrate_requested_primary_pages(  # noqa: SLF001
        payload=payload,
        existing_pages=[],
    )
    result = service._normalize_verified_batch(  # noqa: SLF001
        payload=payload,
        batch=[
            {
                "claim_id": "S01-C0001-01",
                "source_id": "S01",
                "kind": "concept",
                "importance": 3,
            }
        ],
        tool_sources=[],
        primary_pages=pages,
        modern_titles=set(),
    )
    assert len(result) == 1
    assert result[0]["evidence_urls"] == [service._normalize_evidence_url(url)]  # noqa: SLF001


def test_primary_page_filter_rejects_modern_noise_and_accepts_canonical_text() -> None:
    assert service._looks_like_ancient_buddhist_primary_page(  # noqa: SLF001
        title="佛說八大人覺經",
        categories=["大正新脩大藏經"],
        exact_title="佛说八大人觉经",
    )
    assert service._looks_like_ancient_buddhist_primary_page(  # noqa: SLF001
        title="舊雜譬喻經/卷上",
        categories=[],
        exact_title="旧杂譬喻经/卷上",
    )
    assert service._looks_like_ancient_buddhist_primary_page(  # noqa: SLF001
        title="續高僧傳/卷20",
        categories=[],
        exact_title="续高僧传/卷20",
    )
    assert not service._looks_like_ancient_buddhist_primary_page(  # noqa: SLF001
        title="某地人民法院民事判決書",
        categories=["中華人民共和國司法文書"],
    )
    assert not service._looks_like_ancient_buddhist_primary_page(  # noqa: SLF001
        title="大佛頂首楞嚴經淺釋",
        categories=["佛教"],
    )


def test_modern_source_and_process_language_are_hard_blocked() -> None:
    sources = [
        PublicDomainSource(
            title="不可不知的佛教故事全集",
            author="谢志强",
            rights_note="研究材料",
            text="现代整理材料" * 3000,
        )
    ]
    issues = service._modern_source_leakage_issues(  # noqa: SLF001
        "据《不可不知的佛教故事全集》记载，片段中还提到另一件事。",
        modern_sources=sources,
        approved_primary_titles={"《法句经》"},
    )
    assert any("现代整理资料名称" in item for item in issues)
    assert any("研究过程措辞" in item for item in issues)
    assert any("未进入核验账本" in item for item in issues)


def test_changed_final_never_bypasses_missing_evidence_artifacts(tmp_path: Path) -> None:
    result = service.audit_edited_final_manuscript(
        work_dir=tmp_path,
        manuscript="被后置阶段改动的终稿",
        sources=_sources(),
    )
    assert result["status"] == "needs_repair"
    assert result["blocking_issues"]


def test_public_domain_ingestion_reads_epub_spine_in_order() -> None:
    content = BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
            <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
              <rootfiles><rootfile full-path="OEBPS/content.opf" /></rootfiles>
            </container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<package xmlns="http://www.idpf.org/2007/opf">
              <manifest>
                <item id="c1" href="one.xhtml" media-type="application/xhtml+xml" />
                <item id="c2" href="two.xhtml" media-type="application/xhtml+xml" />
              </manifest>
              <spine><itemref idref="c2" /><itemref idref="c1" /></spine>
            </package>""",
        )
        archive.writestr("OEBPS/one.xhtml", "<html><body><p>第一章正文</p></body></html>")
        archive.writestr("OEBPS/two.xhtml", "<html><body><p>第二章正文</p></body></html>")
    text = extract_epub_text(content.getvalue())
    assert text.index("第二章正文") < text.index("第一章正文")
