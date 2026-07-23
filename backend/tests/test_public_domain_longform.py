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
        "chapter_repair_research_preview.md",
        "final_audit_research_preview.md",
    }
    assert expected <= {path.name for path in service.PROMPT_DIR.glob("*.md")}


def test_post_edit_audit_reuses_only_exact_accepted_hash(tmp_path: Path) -> None:
    manuscript = "已通过的独立公版融合终稿"
    (tmp_path / "quality_report.json").write_text(
        json.dumps(
            {
                "passed": True,
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
