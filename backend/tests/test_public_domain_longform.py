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
            }
        )

    monkeypatch.setattr(service, "_call_openai_auditor", _valid_fallback)
    result = service._audit_chapter(  # noqa: SLF001
        work_dir=tmp_path,
        chapter_number=1,
        contract={"title": "第一章", "must_cover": []},
        allowed_evidence=[],
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
        "chapter_audit_research_preview.md",
        "chapter_repair_research_preview.md",
        "final_audit_research_preview.md",
        "primary_source_verification.md",
    }
    assert expected <= {path.name for path in service.PROMPT_DIR.glob("*.md")}


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
