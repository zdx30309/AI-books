"""Evidence-grounded, resumable long-form public-domain fusion pipeline.

This module is deliberately isolated from every other content category.  Raw
source prose is used only during evidence extraction and overlap inspection.
Planning and chapter writing receive normalized evidence records, never the raw
books, so the final work is a synthesis rather than a paraphrase of one source.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from html import unescape as html_unescape
import json
import math
from pathlib import Path
import re
import statistics
import time
from typing import Any, Callable, Iterable
from urllib.parse import quote, unquote, urlencode, urlsplit, urlunsplit

import httpx

from app.core.config import settings
from app.services.deepseek import generate_text as deepseek_generate_text
from app.services.gemini import generate_text as gemini_generate_text
from app.utils.official_api import official_proxy


POLICY_VERSION = "public-domain-longform-v3-primary-evidence"
PRIMARY_RETRIEVAL_VERSION = "primary-retrieval-v6-expanded-anchored-candidates"
NARRATIVE_SYNTHESIS_VERSION = "public-domain-verified-narrative-v5"
NARRATIVE_PLAN_VERSION = "public-domain-verified-narrative-v3"
NARRATIVE_PROFILE_VERSION = "cross-source-narrative-profile-v1"
FULL_MODE = "full"
RESEARCH_PREVIEW_MODE = "research_preview"
MIN_SOURCES = 8
MAX_SOURCES = 10
PREVIEW_MIN_SOURCES = 5
PREVIEW_MAX_SOURCES = 7
MIN_SOURCE_CHARS = 5_000
MAX_SOURCE_CHARS = 1_500_000
MAX_TOTAL_SOURCE_CHARS = 4_000_000
MIN_TARGET_CHARS = 80_000
MAX_TARGET_CHARS = 220_000
MIN_CHAPTERS = 12
MAX_CHAPTERS = 36
PREVIEW_MIN_TARGET_CHARS = 8_000
PREVIEW_MAX_TARGET_CHARS = 15_000
PREVIEW_DEFAULT_TARGET_CHARS = 10_000
PREVIEW_MIN_CHAPTERS = 2
PREVIEW_MAX_CHAPTERS = 4
PREVIEW_DEFAULT_CHAPTERS = 3
PREVIEW_DEEPSEEK_MODEL = "deepseek-chat"
PREVIEW_PRIMARY_BATCH_SIZE = 8
PREVIEW_MAX_DISCOVERY_CANDIDATES = 96
PREVIEW_MIN_VERIFIED_CLAIMS = 18
PREVIEW_MIN_PRIMARY_TITLES = 3
PREVIEW_MAX_PRIMARY_TITLES = 24
PREVIEW_MIN_USED_CLAIMS = 12
PREVIEW_MIN_USED_PRIMARY_TITLES = 6
EVIDENCE_CHUNK_CHARS = 24_000
EVIDENCE_CHUNK_OVERLAP = 400
MAX_EVIDENCE_PER_CHAPTER = 90
MAX_CHAPTER_ATTEMPTS = 3
PREVIEW_MAX_CHAPTER_ATTEMPTS = 7
OVERLAP_WINDOW = 36
OVERLAP_SOURCE_STEP = 1
OVERLAP_OUTPUT_STEP = 4
OVERLAP_RATIO_BLOCK = 0.015
LONG_EXACT_WINDOW = 120

_PRIMARY_SOURCE_HOSTS = (
    "zh.wikisource.org",
    "cbetaonline.dila.edu.tw",
    "tripitaka.cbeta.org",
)
_RESEARCH_PROCESS_LEAKS = (
    "根据材料",
    "据材料",
    "材料中提到",
    "材料指出",
    "来源指出",
    "来源提到",
    "片段中",
    "本片段",
    "研究材料",
    "现代整理本",
    "现代作者认为",
)
_ACADEMIC_NARRATIVE_MARKERS = (
    "本试稿",
    "本章依据",
    "本章所据",
    "本章讨论",
    "本章考察",
    "本文依据",
    "原典所能确认",
    "所据原典",
    "可据原典",
    "该段能确认",
    "综合上述",
    "总而言之",
    "文字没有",
    "材料没有",
    "经文没有",
    "原典没有",
    "没有展开",
    "没有补写",
    "没有被写",
    "没有另写",
    "不能被合成",
    "事实边界",
    "传本位置",
    "题下署",
    "題下署",
    "一处论述",
    "一處論述",
    "这一处材料",
    "這一處材料",
    "同一处材料",
    "同一處材料",
    "另一处材料",
    "另一處材料",
    "不同文本",
    "不同材料",
    "各自出现在",
    "不替另一",
    "没有要求读者",
    "沒有要求讀者",
    "没有进一步叙述",
    "沒有進一步敘述",
    "不需要把",
)
_ACADEMIC_ATTRIBUTION_TERMS = (
    "原典",
    "据《",
    "记载",
    "该经",
    "该论",
    "经文未",
    "本章",
)
_MODERN_PRIMARY_CATEGORIES = ("中華民國", "民國", "现代", "現代")
_MODERN_PRIMARY_TITLE_MARKERS = (
    "判決",
    "判决",
    "白皮書",
    "白皮书",
    "工作報告",
    "工作报告",
    "公報",
    "公报",
    "政策",
    "方案",
    "講話",
    "讲话",
    "淺釋",
    "浅释",
    "講義",
    "讲义",
    "摘要",
    "白話",
    "白话",
    "故事",
    "全知道",
    "辭典",
    "辞典",
    "感應分類輯要",
    "感应分类辑要",
)

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts" / "public_domain_longform"

_PUBLIC_DOMAIN_HINTS = (
    "公版",
    "公共领域",
    "public domain",
    "cc0",
    "保护期届满",
    "版权保护期届满",
)
_COPYRIGHT_MARKERS = (
    "版权所有",
    "版權所有",
    "all rights reserved",
    "未经许可不得转载",
    "未經許可不得轉載",
    "侵权必究",
)
_AI_RESIDUE = (
    "作为一个ai",
    "作为ai",
    "根据你的要求",
    "以下是生成",
    "任务要求",
    "输出要求",
    "prompt",
)
_HEADING_RE = re.compile(
    r"^(?:第\s*[一二三四五六七八九十百零〇两\d]+\s*[章节回篇部卷]|[一二三四五六七八九十百零〇两\d]+[、.．])\s*[^\n]{0,80}$",
    re.MULTILINE,
)


class PublicDomainValidationError(ValueError):
    """Input or rights metadata is unsafe before any paid model call."""


class PublicDomainQualityError(RuntimeError):
    """A generated candidate failed a content/rights/overlap hard gate."""


@dataclass(frozen=True)
class PublicDomainSource:
    title: str
    text: str
    author: str | None = None
    publication_year: int | None = None
    author_death_year: int | None = None
    rights_note: str | None = None
    rights_evidence_url: str | None = None


@dataclass(frozen=True)
class SourceReport:
    source_id: str
    title: str
    author: str
    chars: int
    detected_chapters: int
    rights_basis: str
    sha256: str


@dataclass(frozen=True)
class BookDimensions:
    target_chars: int
    target_chapters: int
    median_source_chars: int
    median_source_chapters: int | None


@dataclass(frozen=True)
class OverlapReport:
    source_id: str
    source_title: str
    matched_windows: int
    total_windows: int
    ratio: float
    long_exact_match: bool
    samples: tuple[str, ...]


@dataclass(frozen=True)
class ChapterResult:
    number: int
    title: str
    text: str
    summary: str
    used_claim_ids: tuple[str, ...]
    audit: dict[str, Any]


@dataclass(frozen=True)
class PublicDomainLongformResult:
    title: str
    summary: str
    markdown: str
    chapters: tuple[ChapterResult, ...]
    source_reports: tuple[SourceReport, ...]
    dimensions: BookDimensions
    quality_report: dict[str, Any]
    plan: dict[str, Any]


ProgressCallback = Callable[[int, str], None]


def validate_sources(
    sources: Iterable[PublicDomainSource],
    *,
    public_domain_confirmed: bool,
    generation_mode: str = FULL_MODE,
    research_use_confirmed: bool = False,
) -> list[PublicDomainSource]:
    items = list(sources)
    preview = generation_mode == RESEARCH_PREVIEW_MODE
    if generation_mode not in {FULL_MODE, RESEARCH_PREVIEW_MODE}:
        raise PublicDomainValidationError(f"不支持的公版生成模式：{generation_mode}")
    min_sources = PREVIEW_MIN_SOURCES if preview else MIN_SOURCES
    max_sources = PREVIEW_MAX_SOURCES if preview else MAX_SOURCES
    if not min_sources <= len(items) <= max_sources:
        label = "研究试稿来源" if preview else "公版来源"
        raise PublicDomainValidationError(f"必须提供 {min_sources} 到 {max_sources} 本{label}。")
    if preview:
        if not research_use_confirmed:
            raise PublicDomainValidationError(
                "研究试稿必须确认：来源只用于抽取事实/概念线索、匿名聚合叙事统计和检查重合；"
                "不复制来源原文、来源故事或现代评论，成稿人物与情节另行原创。"
            )
    elif not public_domain_confirmed:
        raise PublicDomainValidationError("请先逐本核对并确认全部来源均可作为公版/公共领域材料使用。")

    cleaned: list[PublicDomainSource] = []
    seen_hashes: set[str] = set()
    total_chars = 0
    for index, item in enumerate(items, start=1):
        title = str(item.title or "").strip()
        text = normalize_source_text(item.text)
        rights_note = str(item.rights_note or "").strip()
        rights_url = str(item.rights_evidence_url or "").strip()
        if not title:
            raise PublicDomainValidationError(f"第 {index} 本来源缺少书名。")
        chars = count_text_chars(text)
        if chars < MIN_SOURCE_CHARS:
            raise PublicDomainValidationError(
                f"《{title}》有效正文只有 {chars} 字；长篇融合要求每本至少 {MIN_SOURCE_CHARS} 字。"
            )
        if chars > MAX_SOURCE_CHARS:
            raise PublicDomainValidationError(
                f"《{title}》超过单本 {MAX_SOURCE_CHARS} 字安全上限，请先拆除附录或无关资料。"
            )
        total_chars += chars
        if total_chars > MAX_TOTAL_SOURCE_CHARS:
            raise PublicDomainValidationError(
                f"全部来源合计超过 {MAX_TOTAL_SOURCE_CHARS} 字安全上限，请移除附录、索引或无关资料。"
            )
        lowered = text.lower()
        marker = next((value for value in _COPYRIGHT_MARKERS if value in lowered), None)
        if marker and not preview:
            raise PublicDomainValidationError(
                f"《{title}》正文检测到版权限制标记“{marker}”，已在模型调用前阻止。"
            )
        rights_lower = f"{rights_note} {rights_url}".lower()
        if not preview and (not rights_note or not any(hint in rights_lower for hint in _PUBLIC_DOMAIN_HINTS)):
            raise PublicDomainValidationError(
                f"《{title}》缺少可审计的公版依据；请在权利依据中明确写明“公版/公共领域/CC0/保护期届满”及核对理由。"
            )
        if not preview and not rights_url and item.publication_year is None and item.author_death_year is None:
            raise PublicDomainValidationError(
                f"《{title}》除文字说明外没有出版年份、作者去世年份或依据链接，无法形成可审计权利清单。"
            )
        digest = text_sha256(text)
        if digest in seen_hashes:
            raise PublicDomainValidationError(f"《{title}》与另一来源正文完全相同，不能重复计入 8–10 本来源。")
        seen_hashes.add(digest)
        cleaned.append(
            PublicDomainSource(
                title=title,
                text=text,
                author=str(item.author or "").strip() or None,
                publication_year=item.publication_year,
                author_death_year=item.author_death_year,
                rights_note=(
                    rights_note
                    or "研究试稿：仅作 discovery_only 检索线索、匿名聚合叙事统计与来源重合排查；"
                    "未主张现代整理本属于公版，现代内容不得进入正文；成稿人物与情节必须另行原创"
                ),
                rights_evidence_url=rights_url or None,
            )
        )
    return cleaned


def derive_dimensions(
    sources: Iterable[PublicDomainSource],
    *,
    requested_chars: int | None = None,
    requested_chapters: int | None = None,
    generation_mode: str = FULL_MODE,
) -> BookDimensions:
    items = list(sources)
    lengths = [count_text_chars(item.text) for item in items]
    median_chars = int(statistics.median(lengths))
    if generation_mode == RESEARCH_PREVIEW_MODE:
        target_chars = int(requested_chars or PREVIEW_DEFAULT_TARGET_CHARS)
        target_chars = max(PREVIEW_MIN_TARGET_CHARS, min(PREVIEW_MAX_TARGET_CHARS, target_chars))
        target_chapters = int(requested_chapters or PREVIEW_DEFAULT_CHAPTERS)
        target_chapters = max(PREVIEW_MIN_CHAPTERS, min(PREVIEW_MAX_CHAPTERS, target_chapters))
        return BookDimensions(
            target_chars=target_chars,
            target_chapters=target_chapters,
            median_source_chars=median_chars,
            median_source_chapters=None,
        )
    target_chars = int(requested_chars or round(median_chars / 1000) * 1000)
    target_chars = max(MIN_TARGET_CHARS, min(MAX_TARGET_CHARS, target_chars))

    chapter_counts = [count_source_chapters(item.text) for item in items]
    usable_counts = [value for value in chapter_counts if MIN_CHAPTERS <= value <= 80]
    median_chapters = int(round(statistics.median(usable_counts))) if len(usable_counts) >= 4 else None
    fallback_chapters = int(round(target_chars / 5_500))
    target_chapters = int(requested_chapters or median_chapters or fallback_chapters)
    target_chapters = max(MIN_CHAPTERS, min(MAX_CHAPTERS, target_chapters))
    return BookDimensions(
        target_chars=target_chars,
        target_chapters=target_chapters,
        median_source_chars=median_chars,
        median_source_chapters=median_chapters,
    )


def build_source_reports(sources: Iterable[PublicDomainSource]) -> tuple[SourceReport, ...]:
    reports: list[SourceReport] = []
    for index, item in enumerate(sources, start=1):
        reports.append(
            SourceReport(
                source_id=f"S{index:02d}",
                title=item.title,
                author=item.author or "佚名/未填写",
                chars=count_text_chars(item.text),
                detected_chapters=count_source_chapters(item.text),
                rights_basis=_rights_basis(item),
                sha256=text_sha256(item.text),
            )
        )
    return tuple(reports)


def _build_narrative_mechanism_profile(
    sources: Iterable[PublicDomainSource],
    reports: tuple[SourceReport, ...],
    work_dir: Path,
) -> dict[str, Any]:
    """Distil non-identifying corpus mechanics without retaining source prose.

    The uploaded modern books are not generation evidence and may still be
    copyrighted.  This profile therefore stores only aggregate distributions;
    it contains no title, author, quotation, phrase, scene, character or raw
    source fragment.  The writer receives this profile plus verified ancient
    facts, never the modern books themselves.
    """

    path = work_dir / "01_narrative_mechanism_profile.json"
    expected_key = text_sha256(
        json.dumps(
            {
                "policy": POLICY_VERSION,
                "profile_version": NARRATIVE_PROFILE_VERSION,
                "source_hashes": [item.sha256 for item in reports],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    cached = _load_versioned_artifact(path, expected_key)
    if cached is not None:
        return dict(cached.get("profile") or {})

    per_source: list[dict[str, float]] = []
    for source in sources:
        text = normalize_source_text(source.text)
        sentence_lengths = [
            count_text_chars(value)
            for value in re.split(r"[。！？!?]+", text)
            if 1 <= count_text_chars(value) <= 300
        ]
        paragraph_lengths = [
            count_text_chars(value)
            for value in text.splitlines()
            if 6 <= count_text_chars(value) <= 800
        ]
        if not sentence_lengths:
            sentence_lengths = [max(1, count_text_chars(text))]
        if not paragraph_lengths:
            paragraph_lengths = sentence_lengths
        paragraph_mean = statistics.mean(paragraph_lengths)
        per_source.append(
            {
                "sentence_mean": statistics.mean(sentence_lengths),
                "sentence_sd": statistics.pstdev(sentence_lengths)
                if len(sentence_lengths) > 1
                else 0.0,
                "short_sentence_ratio": sum(value <= 12 for value in sentence_lengths)
                / len(sentence_lengths),
                "long_sentence_ratio": sum(value >= 45 for value in sentence_lengths)
                / len(sentence_lengths),
                "paragraph_mean": paragraph_mean,
                "paragraph_cv": (
                    statistics.pstdev(paragraph_lengths) / paragraph_mean
                    if len(paragraph_lengths) > 1 and paragraph_mean
                    else 0.0
                ),
                "dialogue_marks_per_10k": (
                    (text.count("“") + text.count("”") + text.count("曰：") + text.count("问："))
                    * 10_000
                    / max(1, count_text_chars(text))
                ),
            }
        )

    def median(field: str) -> float:
        return round(statistics.median(item[field] for item in per_source), 4)

    profile = {
        "version": NARRATIVE_PROFILE_VERSION,
        "aggregation": "median_across_all_uploaded_sources",
        "source_count": len(per_source),
        "sentence_chars": {
            "mean": median("sentence_mean"),
            "standard_deviation": median("sentence_sd"),
            "short_ratio_le_12": median("short_sentence_ratio"),
            "long_ratio_ge_45": median("long_sentence_ratio"),
        },
        "paragraph_chars": {
            "mean": median("paragraph_mean"),
            "coefficient_of_variation": median("paragraph_cv"),
        },
        "dialogue_marks_per_10k": median("dialogue_marks_per_10k"),
        "contains_raw_text": False,
        "contains_source_identity": False,
        "contains_source_specific_style": False,
    }
    _atomic_write_json(
        path,
        {
            "artifact_key": expected_key,
            "policy_version": POLICY_VERSION,
            "profile": profile,
        },
    )
    return profile


def run_public_domain_longform(
    *,
    sources: Iterable[PublicDomainSource],
    work_dir: Path,
    target_title: str | None = None,
    target_direction: str | None = None,
    target_length: int | None = None,
    target_chapters: int | None = None,
    output_style: str | None = None,
    public_domain_confirmed: bool = False,
    generation_mode: str = FULL_MODE,
    research_use_confirmed: bool = False,
    on_progress: ProgressCallback | None = None,
) -> PublicDomainLongformResult:
    """Build or resume a long-form public-domain synthesis.

    Every expensive stage writes an atomic artifact.  A retry therefore skips
    completed evidence chunks, source digests and accepted chapters.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    clean_sources = validate_sources(
        sources,
        public_domain_confirmed=public_domain_confirmed,
        generation_mode=generation_mode,
        research_use_confirmed=research_use_confirmed,
    )
    dimensions = derive_dimensions(
        clean_sources,
        requested_chars=target_length,
        requested_chapters=target_chapters,
        generation_mode=generation_mode,
    )
    reports = build_source_reports(clean_sources)
    _write_rights_manifest(work_dir, reports, clean_sources, dimensions, generation_mode=generation_mode)
    narrative_profile = _build_narrative_mechanism_profile(clean_sources, reports, work_dir)
    if generation_mode == RESEARCH_PREVIEW_MODE:
        _notify(
            on_progress,
            5,
            f"已隔离 {len(clean_sources)} 本研究材料；只抽取事实线索与匿名聚合叙事统计",
        )
    else:
        _notify(on_progress, 5, f"已验证 {len(clean_sources)} 本来源与权利依据")

    all_evidence: list[dict[str, Any]] = []
    digests: list[dict[str, Any]] = []
    total_sources = len(clean_sources)
    total_evidence_chunks = sum(len(_split_text(item.text)) for item in clean_sources)
    completed_evidence_chunks = 0

    def evidence_chunk_done() -> None:
        nonlocal completed_evidence_chunks
        completed_evidence_chunks += 1
        progress = 8 + int(24 * completed_evidence_chunks / max(1, total_evidence_chunks))
        _notify(
            on_progress,
            progress,
            f"正在完整抽取来源证据 {completed_evidence_chunks}/{total_evidence_chunks} 片",
        )

    for source_index, (source, report) in enumerate(zip(clean_sources, reports), start=1):
        evidence = _extract_source_evidence(
            source,
            report,
            work_dir,
            generation_mode=generation_mode,
            on_chunk_completed=evidence_chunk_done,
        )
        if len(evidence) < 8:
            raise PublicDomainQualityError(
                f"《{source.title}》只得到 {len(evidence)} 条可锚定材料，无法支持高质量长篇融合。"
            )
        all_evidence.extend(evidence)
        if generation_mode != RESEARCH_PREVIEW_MODE:
            digests.append(
                _build_source_digest(
                    source,
                    report,
                    evidence,
                    work_dir,
                    generation_mode=generation_mode,
                )
            )
        progress = 8 + int(27 * source_index / total_sources)
        _notify(on_progress, progress, f"已完成来源证据抽取 {source_index}/{total_sources}")

    planning_reports = reports
    if generation_mode == RESEARCH_PREVIEW_MODE:
        _atomic_write_json(
            work_dir / "01_discovery_only_ledger.json",
            {
                "policy_version": POLICY_VERSION,
                "evidence_status": "discovery_only",
                "generation_eligible": False,
                "claims": all_evidence,
            },
        )
        _notify(on_progress, 35, "现代材料线索抽取完成，正在联网核验古代原典")
        all_evidence = _verify_research_evidence(
            work_dir=work_dir,
            discovery_evidence=all_evidence,
            modern_sources=clean_sources,
        )
        planning_reports = _build_primary_source_reports(all_evidence)
        digests = _build_primary_source_digests(all_evidence, planning_reports)
        _notify(
            on_progress,
            37,
            f"古代原典核验完成：{len(all_evidence)} 条证据 / {len(planning_reports)} 部原典",
        )

    plan = _build_book_plan(
        work_dir=work_dir,
        source_reports=planning_reports,
        source_digests=digests,
        dimensions=dimensions,
        target_title=target_title,
        target_direction=target_direction,
        output_style=output_style,
        generation_mode=generation_mode,
        narrative_profile=narrative_profile,
    )
    _notify(on_progress, 38, f"长篇规划完成：{dimensions.target_chapters} 章 / 目标 {dimensions.target_chars} 字")

    overlap_indexes = _build_overlap_indexes(clean_sources, reports)
    chapters: list[ChapterResult] = []
    previous_summary = "这是第一章，没有上一章。"
    narrative_blueprint = _narrative_blueprint(plan)
    contracts = list(plan["chapters"])
    for chapter_index, contract in enumerate(contracts, start=1):
        allowed_evidence = _select_chapter_evidence(contract, all_evidence, planning_reports)
        chapter = _build_or_resume_chapter(
            work_dir=work_dir,
            chapter_number=chapter_index,
            contract=contract,
            book_summary=str(plan.get("summary") or ""),
            previous_summary=previous_summary,
            allowed_evidence=allowed_evidence,
            overlap_indexes=overlap_indexes,
            generation_mode=generation_mode,
            forbidden_sources=clean_sources if generation_mode == RESEARCH_PREVIEW_MODE else (),
            approved_source_titles={item.title for item in planning_reports},
            narrative_blueprint=narrative_blueprint,
            narrative_profile=narrative_profile,
        )
        chapters.append(chapter)
        previous_summary = chapter.summary
        progress = 40 + int(48 * chapter_index / len(contracts))
        _notify(on_progress, progress, f"逐章写作与审查 {chapter_index}/{len(contracts)}")

    markdown = render_final_markdown(
        title=str(plan.get("title") or target_title or "公版融合长篇"),
        summary=str(plan.get("summary") or ""),
        chapters=chapters,
    )
    quality = _run_final_gate(
        work_dir=work_dir,
        markdown=markdown,
        plan=plan,
        chapters=chapters,
        source_reports=planning_reports,
        dimensions=dimensions,
        overlap_indexes=overlap_indexes,
        generation_mode=generation_mode,
        verified_evidence=all_evidence if generation_mode == RESEARCH_PREVIEW_MODE else None,
        forbidden_sources=clean_sources if generation_mode == RESEARCH_PREVIEW_MODE else (),
        narrative_profile=narrative_profile,
    )
    _atomic_write_text(work_dir / "04_format.md", markdown)
    _notify(
        on_progress,
        93,
        (
            "研究材料隔离、事实、来源重合、章节与全稿质量终审通过"
            if generation_mode == RESEARCH_PREVIEW_MODE
            else "公版权利、事实、来源重合、章节与全书质量终审通过"
        ),
    )
    return PublicDomainLongformResult(
        title=str(plan.get("title") or target_title or "公版融合长篇").strip(),
        summary=str(plan.get("summary") or "").strip(),
        markdown=markdown,
        chapters=tuple(chapters),
        source_reports=planning_reports,
        dimensions=dimensions,
        quality_report=quality,
        plan=plan,
    )


def check_final_manuscript(
    manuscript: str,
    sources: Iterable[PublicDomainSource],
) -> dict[str, Any]:
    clean_sources = list(sources)
    reports = build_source_reports(clean_sources)
    indexes = _build_overlap_indexes(clean_sources, reports)
    overlap = _overlap_reports(manuscript, indexes)
    residues = [value for value in _AI_RESIDUE if value in manuscript.lower()]
    blocked = [asdict(item) for item in overlap if _overlap_blocked(item)]
    return {
        "status": "needs_repair" if blocked or residues else "ok",
        "overlap": [asdict(item) for item in overlap],
        "blocked_overlap": blocked,
        "residue_issues": residues,
    }


def audit_edited_final_manuscript(
    *,
    work_dir: Path,
    manuscript: str,
    sources: Iterable[PublicDomainSource],
) -> dict[str, Any]:
    """Re-audit any post-generation edit against the accepted evidence ledger.

    The original accepted manuscript can reuse its hash-bound final report.  A
    changed manuscript must pass deterministic structure/overlap checks and a
    fresh evidence-grounded Gemini audit for every chapter before delivery.
    """
    generation_mode = FULL_MODE
    try:
        rights_manifest = json.loads((work_dir / "00_rights_manifest.json").read_text(encoding="utf-8"))
        generation_mode = str(rights_manifest.get("generation_mode") or FULL_MODE)
    except (OSError, ValueError, TypeError):
        pass
    preview_ledger_ready = (
        generation_mode != RESEARCH_PREVIEW_MODE
        or (work_dir / "01_verified_primary_ledger.json").exists()
    )
    candidate_hash = text_sha256(manuscript)
    original_report_path = work_dir / "quality_report.json"
    if original_report_path.exists():
        try:
            original_report = json.loads(original_report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            original_report = {}
        if (
            bool(original_report.get("passed"))
            and original_report.get("policy_version") == POLICY_VERSION
            and original_report.get("final_sha256") == candidate_hash
            and preview_ledger_ready
            and (
                generation_mode != RESEARCH_PREVIEW_MODE
                or original_report.get("narrative_synthesis_version") == NARRATIVE_SYNTHESIS_VERSION
            )
        ):
            return {
                "status": "ok",
                "passed": True,
                "candidate_sha256": candidate_hash,
                "reused_original_audit": True,
                "blocking_issues": [],
            }

    cache_path = work_dir / "post_edit_quality_report.json"
    cache_key = text_sha256(
        f"{POLICY_VERSION}\n{NARRATIVE_SYNTHESIS_VERSION}\npost-edit\n{candidate_hash}"
    )
    cached = _load_versioned_artifact(cache_path, cache_key)
    if cached is not None:
        return dict(cached.get("report") or {})

    blocking: list[str] = []
    try:
        plan_payload = json.loads((work_dir / "02_book_plan.json").read_text(encoding="utf-8"))
        plan = dict(plan_payload.get("plan") or {})
        contracts = [item for item in plan.get("chapters") or [] if isinstance(item, dict)]
    except (OSError, ValueError, TypeError) as exc:
        report = {
            "status": "needs_repair",
            "passed": False,
            "candidate_sha256": candidate_hash,
            "blocking_issues": [f"长篇规划证据不可用：{type(exc).__name__}: {exc}"],
        }
        _atomic_write_json(
            cache_path,
            {"artifact_key": cache_key, "policy_version": POLICY_VERSION, "report": report},
        )
        return report

    title, summary, chapters = parse_final_markdown(manuscript)
    narrative_blueprint = _narrative_blueprint(plan)
    narrative_profile: dict[str, Any] = {}
    profile_path = work_dir / "01_narrative_mechanism_profile.json"
    if profile_path.exists():
        try:
            narrative_profile = dict(
                json.loads(profile_path.read_text(encoding="utf-8")).get("profile") or {}
            )
        except (OSError, TypeError, ValueError):
            narrative_profile = {}
    if len(chapters) != len(contracts):
        blocking.append(f"终稿章节数 {len(chapters)}，规划要求 {len(contracts)}")
    if [item.number for item in chapters] != list(range(1, len(chapters) + 1)):
        blocking.append("终稿章节编号不连续或顺序发生变化")

    target_chars = sum(int(item.get("target_chars") or 0) for item in contracts)
    actual_chars = count_text_chars("\n".join(item.text for item in chapters))
    if target_chars and not int(target_chars * 0.90) <= actual_chars <= int(target_chars * 1.10):
        blocking.append(
            f"终稿有效字数 {actual_chars}，必须在 {int(target_chars * 0.90)}–{int(target_chars * 1.10)} 字之间"
        )

    clean_sources = list(sources)
    raw_source_reports = build_source_reports(clean_sources)
    overlap_indexes = _build_overlap_indexes(clean_sources, raw_source_reports)
    deterministic = check_final_manuscript(manuscript, clean_sources)
    blocking.extend(
        f"与《{item.get('source_title') or item.get('source_id')}》来源重合超限"
        for item in deterministic.get("blocked_overlap") or []
    )
    if deterministic.get("residue_issues"):
        blocking.append("终稿含模型说明或任务残留")

    all_evidence: list[dict[str, Any]] = []
    if generation_mode == RESEARCH_PREVIEW_MODE:
        try:
            ledger = json.loads((work_dir / "01_verified_primary_ledger.json").read_text(encoding="utf-8"))
            all_evidence = [item for item in ledger.get("claims") or [] if isinstance(item, dict)]
            if (
                ledger.get("evidence_status") != "verified_primary"
                or not bool(ledger.get("generation_eligible"))
            ):
                raise ValueError("verified primary ledger is not generation eligible")
        except (OSError, ValueError, TypeError) as exc:
            blocking.append(f"古代原典核验账本不可用：{type(exc).__name__}: {exc}")
        source_reports = _build_primary_source_reports(all_evidence)
        blocking.extend(
            _modern_source_leakage_issues(
                "\n\n".join(item.text for item in chapters),
                modern_sources=clean_sources,
                approved_primary_titles={item.title for item in source_reports},
            )
        )
        blocking.extend(_narrative_nonfiction_form_issues("\n\n".join(item.text for item in chapters)))
    else:
        source_reports = raw_source_reports
        for source_report in source_reports:
            ledger_path = work_dir / "evidence" / source_report.source_id / "ledger.json"
            try:
                ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
                claims = [item for item in ledger.get("claims") or [] if isinstance(item, dict)]
            except (OSError, ValueError, TypeError) as exc:
                claims = []
                blocking.append(f"{source_report.source_id} 证据账本不可用：{type(exc).__name__}: {exc}")
            all_evidence.extend(claims)

    chapter_audits: list[dict[str, Any]] = []
    if len(chapters) == len(contracts) and not any("账本不可用" in item for item in blocking):
        for chapter, contract in zip(chapters, contracts):
            try:
                allowed_evidence = _select_chapter_evidence(contract, all_evidence, source_reports)
                evidence_payload = [
                    {
                        "claim_id": item.get("claim_id"),
                        "source_id": item.get("source_id"),
                        "source_title": item.get("source_title"),
                        "canonical_locator": item.get("canonical_locator"),
                        "evidence_status": item.get("evidence_status"),
                        "kind": item.get("kind"),
                        "statement": item.get("statement"),
                        "qualifier": item.get("qualifier"),
                    }
                    for item in allowed_evidence
                ]
                audit = _audit_chapter(
                    work_dir=work_dir,
                    chapter_number=chapter.number,
                    contract=contract,
                    allowed_evidence=evidence_payload,
                    chapter_text=chapter.text,
                    generation_mode=generation_mode,
                    narrative_blueprint=narrative_blueprint,
                    narrative_profile=narrative_profile,
                )
                chapter_issues = _post_edit_chapter_issues(
                    text=chapter.text,
                    target_chars=int(contract.get("target_chars") or 0),
                    overlap_indexes=overlap_indexes,
                    require_narrative_nonfiction=generation_mode == RESEARCH_PREVIEW_MODE,
                )
                if chapter_issues:
                    audit["pass"] = False
                    audit.setdefault("repair_instructions", []).extend(chapter_issues)
                if generation_mode == RESEARCH_PREVIEW_MODE:
                    leakage = _modern_source_leakage_issues(
                        chapter.text,
                        modern_sources=clean_sources,
                        approved_primary_titles={item.title for item in source_reports},
                    )
                    if leakage:
                        audit["pass"] = False
                        audit.setdefault("repair_instructions", []).extend(leakage)
                if not _audit_passed(audit):
                    blocking.append(f"第 {chapter.number} 章未通过终稿事实与语言复审")
                chapter_audits.append(
                    {
                        "number": chapter.number,
                        "title": chapter.title,
                        "summary": _fallback_summary(chapter.text),
                        "audit": audit,
                    }
                )
            except Exception as exc:
                blocking.append(f"第 {chapter.number} 章终稿复审失败：{type(exc).__name__}: {exc}")

    model_audit: dict[str, Any] = {}
    if not blocking:
        audit_material = {
            "plan": plan,
            "candidate": {"title": title, "summary": summary, "sha256": candidate_hash},
            "chapter_summaries": chapter_audits,
            "source_reports": [asdict(item) for item in source_reports],
            "deterministic": {
                "target_chars": target_chars,
                "actual_chars": actual_chars,
                "source_overlap": deterministic.get("overlap") or [],
                "blocking_issues": [],
                "narrative_synthesis_version": NARRATIVE_SYNTHESIS_VERSION,
                "narrative_nonfiction_form": _narrative_nonfiction_form_metrics(
                    "\n\n".join(item.text for item in chapters)
                ) if generation_mode == RESEARCH_PREVIEW_MODE else None,
            },
        }
        try:
            raw = _call_auditor(
                work_dir,
                action="post_edit_final_audit",
                prompt=_render_prompt(
                    (
                        "final_audit_research_preview.md"
                        if generation_mode == RESEARCH_PREVIEW_MODE
                        else "final_audit.md"
                    ),
                    audit_material=json.dumps(audit_material, ensure_ascii=False),
                ),
                temperature=0.0,
                max_output_tokens=5000,
                generation_mode=generation_mode,
            )
            model_audit = _parse_json_object(raw)
            blocking.extend(str(value) for value in model_audit.get("blocking_issues") or [])
            if not bool(model_audit.get("pass")) and not blocking:
                blocking.append("终稿模型总审未通过")
        except Exception as exc:
            blocking.append(f"终稿模型总审失败：{type(exc).__name__}: {exc}")

    passed = not blocking
    report = {
        "status": "ok" if passed else "needs_repair",
        "passed": passed,
        "candidate_sha256": candidate_hash,
        "reused_original_audit": False,
        "target_chars": target_chars,
        "actual_chars": actual_chars,
        "generation_mode": generation_mode,
        "verified_primary_claim_count": (
            len(all_evidence) if generation_mode == RESEARCH_PREVIEW_MODE else None
        ),
        "chapter_audits": chapter_audits,
        "source_overlap": deterministic.get("overlap") or [],
        "model_audit": model_audit,
        "blocking_issues": blocking,
    }
    _atomic_write_json(
        cache_path,
        {"artifact_key": cache_key, "policy_version": POLICY_VERSION, "report": report},
    )
    return report


def render_final_markdown(
    *,
    title: str,
    summary: str,
    chapters: Iterable[ChapterResult],
) -> str:
    items = list(chapters)
    directory = "\n".join(f"{item.number}. {item.title}" for item in items)
    body = "\n\n".join(f"@{item.number}. {item.title}\n{item.text.strip()}" for item in items)
    return (
        f"@最终书名\n{title.strip()}\n\n"
        f"@最终简介\n{summary.strip()}\n\n"
        f"@目录\n{directory}\n\n"
        f"@正文\n{body}\n"
    )


def parse_final_markdown(markdown: str) -> tuple[str, str, list[ChapterResult]]:
    title_match = re.search(r"@最终书名\s*\n([^\n]+)", markdown or "")
    summary_match = re.search(r"@最终简介\s*\n([\s\S]*?)(?=\n\s*@目录)", markdown or "")
    chapter_matches = list(re.finditer(r"^@(\d+)\.\s*(.+?)\s*$", markdown or "", re.MULTILINE))
    chapters: list[ChapterResult] = []
    for index, match in enumerate(chapter_matches):
        start = match.end()
        end = chapter_matches[index + 1].start() if index + 1 < len(chapter_matches) else len(markdown)
        text = markdown[start:end].strip()
        chapters.append(
            ChapterResult(
                number=int(match.group(1)),
                title=match.group(2).strip(),
                text=text,
                summary="",
                used_claim_ids=(),
                audit={},
            )
        )
    return (
        title_match.group(1).strip() if title_match else "公版融合长篇",
        summary_match.group(1).strip() if summary_match else "",
        chapters,
    )


def normalize_source_text(text: str) -> str:
    value = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\u0000", "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    result: list[str] = []
    previous_blank = False
    for line in lines:
        if not line:
            if not previous_blank:
                result.append("")
            previous_blank = True
        else:
            result.append(line)
            previous_blank = False
    return "\n".join(result).strip()


def count_text_chars(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def count_source_chapters(text: str) -> int:
    return len(_HEADING_RE.findall(text or ""))


def text_sha256(text: str) -> str:
    return sha256((text or "").encode("utf-8")).hexdigest()


def source_to_dict(item: PublicDomainSource) -> dict[str, Any]:
    return asdict(item)


def source_from_dict(item: dict[str, Any]) -> PublicDomainSource:
    return PublicDomainSource(
        title=str(item.get("title") or ""),
        text=str(item.get("text") or ""),
        author=str(item.get("author") or "").strip() or None,
        publication_year=_optional_int(item.get("publication_year")),
        author_death_year=_optional_int(item.get("author_death_year")),
        rights_note=str(item.get("rights_note") or "").strip() or None,
        rights_evidence_url=str(item.get("rights_evidence_url") or "").strip() or None,
    )


def _extract_source_evidence(
    source: PublicDomainSource,
    report: SourceReport,
    work_dir: Path,
    *,
    generation_mode: str = FULL_MODE,
    on_chunk_completed: Callable[[], None] | None = None,
) -> list[dict[str, Any]]:
    source_dir = work_dir / "evidence" / report.source_id
    source_dir.mkdir(parents=True, exist_ok=True)
    chunks = _split_text(source.text)
    evidence: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks, start=1):
        chunk_id = f"C{index:04d}"
        path = source_dir / f"{chunk_id}.json"
        expected_key = text_sha256(
            f"{POLICY_VERSION}\n{generation_mode}\n"
            f"{PREVIEW_DEEPSEEK_MODEL if generation_mode == RESEARCH_PREVIEW_MODE else settings.DEEPSEEK_MODEL}\n"
            f"{report.sha256}\n{chunk_id}\n{chunk}"
        )
        cached = _load_versioned_artifact(path, expected_key)
        if cached is None:
            prompt = _render_prompt(
                (
                    "source_evidence_research_preview.md"
                    if generation_mode == RESEARCH_PREVIEW_MODE
                    else "source_evidence.md"
                ),
                source_id=report.source_id,
                source_title=source.title,
                chunk_id=chunk_id,
                chunk_text=chunk,
            )
            raw = _call_llm(
                work_dir,
                action=f"evidence:{report.source_id}:{chunk_id}",
                prompt=prompt,
                temperature=0.0,
                max_output_tokens=1800 if generation_mode == RESEARCH_PREVIEW_MODE else 5000,
                model=PREVIEW_DEEPSEEK_MODEL if generation_mode == RESEARCH_PREVIEW_MODE else None,
            )
            payload = _parse_json_object(raw)
            claims: list[dict[str, Any]] = []
            for claim_index, claim in enumerate(payload.get("claims") or [], start=1):
                if not isinstance(claim, dict):
                    continue
                anchor = str(claim.get("anchor") or "").strip()
                statement = str(claim.get("statement") or "").strip()
                if not anchor or anchor not in chunk or not statement:
                    continue
                claims.append(
                    {
                        "claim_id": f"{report.source_id}-{chunk_id}-{claim_index:02d}",
                        "source_id": report.source_id,
                        "source_title": source.title,
                        "chunk_id": chunk_id,
                        "kind": str(claim.get("kind") or "fact")[:24],
                        "statement": statement[:160],
                        "qualifier": str(claim.get("qualifier") or "").strip()[:120],
                        "anchor": anchor[:24],
                        "importance": max(1, min(3, int(claim.get("importance") or 1))),
                        "chunk_sha256": text_sha256(chunk),
                        "evidence_status": (
                            "discovery_only"
                            if generation_mode == RESEARCH_PREVIEW_MODE
                            else "source_grounded"
                        ),
                        "generation_eligible": generation_mode != RESEARCH_PREVIEW_MODE,
                    }
                )
            if claims:
                audit_prompt = _render_prompt(
                    "evidence_audit.md",
                    chunk_text=chunk,
                    claims=json.dumps(claims, ensure_ascii=False),
                )
                audit_raw = _call_auditor(
                    work_dir,
                    action=f"evidence_audit:{report.source_id}:{chunk_id}",
                    prompt=audit_prompt,
                    temperature=0.0,
                    max_output_tokens=4000,
                    generation_mode=generation_mode,
                )
                evidence_audit = _parse_json_object(audit_raw)
                valid_ids = {str(value) for value in evidence_audit.get("valid_claim_ids") or []}
                claims = [item for item in claims if item["claim_id"] in valid_ids]
            cached = {
                "artifact_key": expected_key,
                "policy_version": POLICY_VERSION,
                "claims": claims,
                "section_summary": str(payload.get("section_summary") or "")[:500],
                "open_questions": payload.get("open_questions") or [],
            }
            _atomic_write_json(path, cached)
        evidence.extend([item for item in cached.get("claims") or [] if isinstance(item, dict)])
        if on_chunk_completed is not None:
            on_chunk_completed()
    _atomic_write_json(
        source_dir / "ledger.json",
        {
            "policy_version": POLICY_VERSION,
            "source_id": report.source_id,
            "source_sha256": report.sha256,
            "claims": evidence,
        },
    )
    return evidence


def _verify_research_evidence(
    *,
    work_dir: Path,
    discovery_evidence: list[dict[str, Any]],
    modern_sources: list[PublicDomainSource],
) -> list[dict[str, Any]]:
    """Promote discovery-only hints only after a tool-grounded ancient-source lookup.

    The modern books are deliberately absent from every downstream generation
    payload.  A candidate becomes generation-eligible only when its cited URL
    was returned by the web-search tool and belongs to an approved primary-text
    host.  Failure is a quality stop, never a best-effort generation fallback.
    """
    candidates = _select_discovery_candidates(discovery_evidence)
    if not candidates:
        raise PublicDomainQualityError("现代整理材料没有提取到可供古代原典核验的中性线索。")

    verification_dir = work_dir / "primary_verification"
    verification_dir.mkdir(parents=True, exist_ok=True)
    modern_titles = {
        value
        for item in modern_sources
        for value in (str(item.title or "").strip(), str(item.author or "").strip())
        if value
    }
    verified: list[dict[str, Any]] = []
    for batch_index, start in enumerate(range(0, len(candidates), PREVIEW_PRIMARY_BATCH_SIZE), start=1):
        batch = candidates[start : start + PREVIEW_PRIMARY_BATCH_SIZE]
        batch_input = [
            {
                "candidate_id": item.get("claim_id"),
                "kind": item.get("kind"),
                "statement": item.get("statement"),
                "qualifier": item.get("qualifier"),
            }
            for item in batch
        ]
        artifact_key = text_sha256(
            json.dumps(
                {
                    "policy": POLICY_VERSION,
                    "primary_retrieval": PRIMARY_RETRIEVAL_VERSION,
                    "batch": batch_input,
                    "hosts": _PRIMARY_SOURCE_HOSTS,
                    "model": settings.FINANCE_WEB_SEARCH_MODEL or settings.OPENAI_WRITER_MODEL,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        path = verification_dir / f"batch_{batch_index:03d}.json"
        cached = _load_versioned_artifact(path, artifact_key)
        if cached is not None:
            verified.extend(
                item for item in cached.get("verified_claims") or [] if isinstance(item, dict)
            )
            continue

        primary_pages = _discover_primary_pages_for_batch(batch)
        prompt = _render_prompt(
            "primary_source_verification.md",
            allowed_hosts="、".join(_PRIMARY_SOURCE_HOSTS),
            candidates=json.dumps(batch_input, ensure_ascii=False),
            primary_page_context=json.dumps(
                [
                    {
                        "candidate_ids": item.get("candidate_ids") or [],
                        "canonical_title_hint": item.get("title"),
                        "categories": item.get("categories") or [],
                        "url": item.get("url"),
                        "page_text": str(item.get("text") or "")[:5000],
                    }
                    for item in primary_pages
                ],
                ensure_ascii=False,
            ),
        )
        raw, tool_sources = _call_openai_primary_verifier(
            work_dir,
            action=f"primary_verify:{batch_index:03d}",
            prompt=prompt,
        )
        payload = _parse_json_object(raw)
        primary_pages = _hydrate_requested_primary_pages(
            payload=payload,
            existing_pages=primary_pages,
        )
        normalized = _normalize_verified_batch(
            payload=payload,
            batch=batch,
            tool_sources=tool_sources,
            primary_pages=primary_pages,
            modern_titles=modern_titles,
        )
        _atomic_write_json(
            path,
            {
                "artifact_key": artifact_key,
                "policy_version": POLICY_VERSION,
                "primary_retrieval_version": PRIMARY_RETRIEVAL_VERSION,
                "verifier_payload": payload,
                "tool_sources": tool_sources,
                "primary_pages": [
                    {
                        "candidate_ids": item.get("candidate_ids") or [],
                        "title": item.get("title"),
                        "categories": item.get("categories") or [],
                        "url": item.get("url"),
                        "sha256": text_sha256(str(item.get("text") or "")),
                    }
                    for item in primary_pages
                ],
                "verified_claims": normalized,
                "rejected_candidate_ids": payload.get("rejected_candidate_ids") or [],
            },
        )
        verified.extend(normalized)

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for item in verified:
        key = (str(item.get("canonical_title") or ""), _compact(str(item.get("statement") or "")))
        if not key[0] or not key[1]:
            continue
        current = merged.get(key)
        if current is None:
            merged[key] = dict(item)
            continue
        current["discovery_claim_ids"] = sorted(
            set(current.get("discovery_claim_ids") or []) | set(item.get("discovery_claim_ids") or [])
        )
        current["discovery_source_ids"] = sorted(
            set(current.get("discovery_source_ids") or []) | set(item.get("discovery_source_ids") or [])
        )
        current["evidence_urls"] = sorted(
            set(current.get("evidence_urls") or []) | set(item.get("evidence_urls") or [])
        )
        current["importance"] = max(int(current.get("importance") or 1), int(item.get("importance") or 1))

    title_counts: dict[str, int] = {}
    for item in merged.values():
        title = str(item.get("canonical_title") or "")
        title_counts[title] = title_counts.get(title, 0) + 1
    selected_titles = {
        title
        for title, _ in sorted(title_counts.items(), key=lambda pair: (-pair[1], pair[0]))[
            :PREVIEW_MAX_PRIMARY_TITLES
        ]
    }
    selected = [item for item in merged.values() if item.get("canonical_title") in selected_titles]
    covered_discovery_sources = {
        value for item in selected for value in item.get("discovery_source_ids") or [] if value
    }
    if len(selected) < PREVIEW_MIN_VERIFIED_CLAIMS:
        raise PublicDomainQualityError(
            f"古代原典只核验成功 {len(selected)} 条证据，至少需要 {PREVIEW_MIN_VERIFIED_CLAIMS} 条；"
            "现代整理材料不得直接进入正文。"
        )
    if len(selected_titles) < PREVIEW_MIN_PRIMARY_TITLES:
        raise PublicDomainQualityError(
            f"古代原典只覆盖 {len(selected_titles)} 部，至少需要 {PREVIEW_MIN_PRIMARY_TITLES} 部独立原典。"
        )
    if len(covered_discovery_sources) < min(3, len(modern_sources)):
        raise PublicDomainQualityError(
            f"核验后的事实线索只覆盖 {len(covered_discovery_sources)} 份研究材料，交叉发现范围不足。"
        )

    source_id_by_title = {
        title: f"P{index:02d}"
        for index, title in enumerate(sorted(selected_titles), start=1)
    }
    counters: dict[str, int] = {}
    final: list[dict[str, Any]] = []
    for item in sorted(
        selected,
        key=lambda value: (
            source_id_by_title[str(value.get("canonical_title") or "")],
            -int(value.get("importance") or 1),
            str(value.get("statement") or ""),
        ),
    ):
        title = str(item.get("canonical_title") or "")
        source_id = source_id_by_title[title]
        counters[source_id] = counters.get(source_id, 0) + 1
        final.append(
            {
                **item,
                "claim_id": f"{source_id}-V{counters[source_id]:04d}",
                "source_id": source_id,
                "source_title": title,
                "evidence_status": "verified_primary",
                "generation_eligible": True,
            }
        )
    _atomic_write_json(
        work_dir / "01_verified_primary_ledger.json",
        {
            "policy_version": POLICY_VERSION,
            "primary_retrieval_version": PRIMARY_RETRIEVAL_VERSION,
            "evidence_status": "verified_primary",
            "generation_eligible": True,
            "approved_hosts": list(_PRIMARY_SOURCE_HOSTS),
            "claims": final,
        },
    )
    return final


def _select_discovery_candidates(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    seen: set[str] = set()
    for item in sorted(
        evidence,
        key=lambda value: (-_discovery_candidate_priority(value), str(value.get("claim_id") or "")),
    ):
        if item.get("evidence_status") != "discovery_only" or bool(item.get("generation_eligible")):
            continue
        compact = _compact(str(item.get("statement") or ""))
        if len(compact) < 8 or compact in seen:
            continue
        seen.add(compact)
        source_id = str(item.get("source_id") or "")
        buckets.setdefault(source_id, []).append(item)
    selected: list[dict[str, Any]] = []
    source_ids = sorted(buckets)
    offset = 0
    while source_ids and len(selected) < PREVIEW_MAX_DISCOVERY_CANDIDATES:
        source_id = source_ids[offset % len(source_ids)]
        bucket = buckets[source_id]
        if bucket:
            selected.append(bucket.pop(0))
        source_ids = [value for value in source_ids if buckets[value]]
        offset += 1
    return selected


def _discovery_candidate_priority(item: dict[str, Any]) -> int:
    """Prefer source-anchored clues over broad modern explanations.

    The modern research books are useful as an index into ancient texts, but a
    generic definition such as "what pure mind means" is a poor search key.  An
    explicit sutra title, locator, or attributed quotation is substantially
    more likely to produce auditable primary evidence and therefore receives a
    deterministic priority boost before any paid model call.
    """
    statement = str(item.get("statement") or "")
    qualifier = str(item.get("qualifier") or "")
    text = f"{statement} {qualifier}"
    titles = _primary_title_hints(text)
    score = max(1, min(3, int(item.get("importance") or 1))) * 10
    if titles:
        score += 120
    if _primary_title_hints(qualifier):
        score += 30
    if any(marker in text for marker in ("出自", "见《", "見《", "卷", "品", "偈", "经云", "經云", "佛说", "佛說")):
        score += 35
    if any(marker in qualifier for marker in ("原文", "引文", "出处", "出處")):
        score += 20
    if any(marker in statement for marker in ("指的是", "一般指", "可以理解为", "可以理解為", "通常是", "概念是")):
        score -= 25
    return score


def _primary_title_hints(text: str) -> list[str]:
    return list(
        dict.fromkeys(
            value.strip()
            for value in re.findall(r"《([^》]{2,60})》", text or "")
            if value.strip()
        )
    )


def _normalize_verified_batch(
    *,
    payload: dict[str, Any],
    batch: list[dict[str, Any]],
    tool_sources: list[dict[str, str]],
    primary_pages: list[dict[str, Any]] | None = None,
    modern_titles: set[str],
) -> list[dict[str, Any]]:
    candidates = {str(item.get("claim_id") or ""): item for item in batch}
    page_text_by_url = {
        _normalize_evidence_url(str(item.get("url") or "")): str(item.get("text") or "")
        for item in (primary_pages or [])
        if _normalize_evidence_url(str(item.get("url") or ""))
        and _is_approved_primary_url(str(item.get("url") or ""))
    }
    normalized: list[dict[str, Any]] = []
    for raw in payload.get("verified_claims") or []:
        if not isinstance(raw, dict):
            continue
        candidate_ids = [
            str(value) for value in raw.get("candidate_ids") or [] if str(value) in candidates
        ]
        if not candidate_ids:
            continue
        title = _normalize_canonical_title(str(raw.get("canonical_title") or ""))
        if not title or any(value and value in title for value in modern_titles):
            continue
        statement = str(raw.get("statement") or "").strip()[:180]
        qualifier = str(raw.get("qualifier") or "").strip()[:120]
        locator = str(raw.get("canonical_locator") or "").strip()[:120]
        evidence_quote = str(raw.get("evidence_quote") or "").strip()[:120]
        requested_urls = raw.get("evidence_urls") or [raw.get("evidence_url")]
        evidence_urls = sorted(
            {
                _normalize_evidence_url(str(value or ""))
                for value in requested_urls
                if _normalize_evidence_url(str(value or "")) in page_text_by_url
                and _is_approved_primary_url(str(value or ""))
            }
        )
        quote_supported = any(
            _compact(evidence_quote) in _compact(page_text_by_url.get(url) or "")
            for url in evidence_urls
        )
        if (
            len(_compact(statement)) < 8
            or not locator
            or len(_compact(evidence_quote)) < 4
            or not evidence_urls
            or not quote_supported
            or _contains_research_process_leak(statement)
            or any(value and (value in statement or value in qualifier) for value in modern_titles)
        ):
            continue
        discovery_source_ids = sorted(
            {str(candidates[value].get("source_id") or "") for value in candidate_ids}
        )
        importance = max(int(candidates[value].get("importance") or 1) for value in candidate_ids)
        kind = str(raw.get("kind") or candidates[candidate_ids[0]].get("kind") or "fact")[:24]
        normalized.append(
            {
                "canonical_title": title,
                "canonical_locator": locator,
                "statement": statement,
                "qualifier": qualifier,
                "kind": kind,
                "importance": max(1, min(3, importance)),
                "evidence_quote": evidence_quote,
                "evidence_urls": evidence_urls,
                "discovery_claim_ids": sorted(set(candidate_ids)),
                "discovery_source_ids": discovery_source_ids,
            }
        )
    return normalized


def _hydrate_requested_primary_pages(
    *,
    payload: dict[str, Any],
    existing_pages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Independently fetch every approved evidence URL returned by the verifier.

    The web-search model may identify a more precise translation, volume, or
    subpage than the pre-search title lookup.  Previously those correct URLs
    were discarded solely because their path differed from the coarse page
    supplied in the prompt.  We now fetch the requested approved URL ourselves
    and still require the evidence quote to occur literally in its content.
    Thus URL variants are accepted without trusting model memory.
    """
    pages_by_url: dict[str, dict[str, Any]] = {}
    for page in existing_pages:
        url = _normalize_evidence_url(str(page.get("url") or ""))
        if url:
            pages_by_url[url] = {**page, "url": url}
    requested_urls = {
        _normalize_evidence_url(str(url or ""))
        for item in payload.get("verified_claims") or []
        if isinstance(item, dict)
        for url in (item.get("evidence_urls") or [item.get("evidence_url")])
        if _normalize_evidence_url(str(url or ""))
        and _is_approved_primary_url(str(url or ""))
    }
    for url in sorted(requested_urls):
        if url in pages_by_url:
            continue
        fetched = _fetch_primary_page_by_url(url)
        if fetched is not None:
            pages_by_url[url] = {**fetched, "url": url, "requested_url": url}
    return list(pages_by_url.values())


def _fetch_primary_page_by_url(url: str) -> dict[str, Any] | None:
    normalized = _normalize_evidence_url(url)
    if not normalized or not _is_approved_primary_url(normalized):
        return None
    parts = urlsplit(normalized)
    host = (parts.hostname or "").lower()
    if host == "zh.wikisource.org" or host.endswith(".zh.wikisource.org"):
        path = unquote(parts.path or "")
        title = ""
        for prefix in ("/wiki/", "/zh-hant/", "/zh-hans/", "/zh/"):
            if path.startswith(prefix):
                title = path[len(prefix) :].replace("_", " ").strip()
                break
        if not title:
            return None
        # The context page shown to the verifier is intentionally shortened,
        # but the final literal-quote gate must inspect the complete primary
        # page.  Otherwise a valid quote after the first 6,000 characters is
        # incorrectly rejected.
        pages = _search_wikisource_pages(f"title:{title}", extract_chars=None)
        if not pages:
            return None
        return pages[0]

    proxy = official_proxy("openai", settings.OPENAI_HTTPS_PROXY)
    try:
        with httpx.Client(
            proxy=proxy or None,
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": "video-plan-public-domain/3.1 (primary-source verification)"},
        ) as client:
            response = client.get(normalized)
            response.raise_for_status()
            if not _is_approved_primary_url(str(response.url)):
                return None
            text = html_unescape(re.sub(r"<[^>]+>", " ", response.text))
            text = re.sub(r"\s+", " ", text).strip()
    except Exception:
        return None
    if len(_compact(text)) < 80:
        return None
    return {"title": parts.path.rsplit("/", 1)[-1], "text": text, "categories": []}


def _discover_primary_pages_for_batch(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fetch concrete ancient-text pages from Wikisource's public API.

    These pages are discovery/audit inputs only.  Their raw text is never sent
    to the chapter writer, and an evidence quote must occur verbatim in the
    fetched page before the claim can become generation-eligible.
    """
    found: dict[str, dict[str, Any]] = {}
    for item in batch:
        candidate_id = str(item.get("claim_id") or "")
        for query in _primary_search_queries(item):
            pages = _search_wikisource_pages(query)
            for page in pages:
                url = _normalize_evidence_url(str(page.get("url") or ""))
                if not url:
                    continue
                current = found.get(url)
                if current is None:
                    current = {**page, "url": url, "candidate_ids": []}
                    found[url] = current
                if candidate_id and candidate_id not in current["candidate_ids"]:
                    current["candidate_ids"].append(candidate_id)
            # A successful exact-title lookup is stronger than multiple broad
            # search hits and is sufficient context for literal quote
            # validation.  Do not spend two more requests collecting noisy
            # pages for the same candidate.
            if query.startswith("title:") and pages:
                break
            if sum(candidate_id in value["candidate_ids"] for value in found.values()) >= 3:
                break
    return list(found.values())[:24]


def _primary_search_queries(item: dict[str, Any]) -> list[str]:
    text = f"{item.get('statement') or ''} {item.get('qualifier') or ''}".strip()
    titles = _primary_title_hints(text)
    clauses = [
        value.strip()
        for value in re.split(r"[，。；：、！？,.!?;:\s]+", re.sub(r"《[^》]+》", " ", text))
        if 4 <= len(value.strip()) <= 28
    ]
    # ``title:`` is an internal exact-page instruction consumed below.  It is
    # deliberately tried before free search so an explicit 《金刚经》 citation
    # cannot be displaced by unrelated pages that merely mention the same
    # generic Buddhist term.
    queries = [f"title:{title}" for title in titles]
    queries.extend(titles)
    queries.extend(sorted(clauses, key=len, reverse=True)[:2])
    if not queries:
        compact = _compact(text)
        if compact:
            queries.append(compact[:32])
    return list(dict.fromkeys(queries))[:3]


def _search_wikisource_pages(
    query: str,
    *,
    extract_chars: int | None = 6000,
) -> list[dict[str, str]]:
    exact_title = query.removeprefix("title:").strip() if query.startswith("title:") else ""
    request_params: dict[str, Any] = {
        "action": "query",
        "prop": "extracts|categories",
        "explaintext": 1,
        "cllimit": "max",
        "format": "json",
        "formatversion": 2,
        "utf8": 1,
        "variant": "zh-hant",
        "converttitles": 1,
        "redirects": 1,
    }
    if extract_chars is not None:
        request_params["exchars"] = max(100, int(extract_chars))
    if exact_title:
        request_params["titles"] = exact_title
    else:
        request_params.update(
            {
                "generator": "search",
                "gsrsearch": query,
                "gsrnamespace": 0,
                "gsrlimit": 3,
            }
        )
    params = urlencode(request_params)
    proxy = official_proxy("openai", settings.OPENAI_HTTPS_PROXY)
    try:
        with httpx.Client(
            proxy=proxy or None,
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": "video-plan-public-domain/3.0 (primary-source verification)"},
        ) as client:
            response = client.get(f"https://zh.wikisource.org/w/api.php?{params}")
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return []
    result: list[dict[str, str]] = []
    for page in (payload.get("query") or {}).get("pages") or []:
        title = str(page.get("title") or "").strip()
        text = str(page.get("extract") or "").strip()
        categories = [
            str(item.get("title") or "").removeprefix("Category:").strip()
            for item in page.get("categories") or []
        ]
        if (
            len(_compact(text)) < 80
            or title.startswith(("Portal:", "Category:", "Template:"))
            or title.startswith("佛學大辭典/")
            or any(
                marker in category
                for category in categories
                for marker in _MODERN_PRIMARY_CATEGORIES
            )
            or not _looks_like_ancient_buddhist_primary_page(
                title=title,
                categories=categories,
                exact_title=exact_title,
            )
        ):
            continue
        result.append(
            {
                "title": title,
                "url": f"https://zh.wikisource.org/wiki/{quote(title.replace(' ', '_'), safe='/:()')}",
                "text": text,
                "categories": categories,
            }
        )
    return result


def _looks_like_ancient_buddhist_primary_page(
    *,
    title: str,
    categories: list[str],
    exact_title: str = "",
) -> bool:
    compact_title = re.sub(r"\s+", "", str(title or ""))
    if not compact_title or any(marker in compact_title for marker in _MODERN_PRIMARY_TITLE_MARKERS):
        return False
    category_text = " ".join(categories)
    category_signal = any(
        marker in category_text
        for marker in ("佛經", "佛经", "大藏經", "大藏经", "經部", "经部", "律部", "論部", "论部")
    )
    title_root = compact_title.split("/", 1)[0]
    title_stem = re.split(r"[_（(]", title_root, maxsplit=1)[0]
    title_signal = bool(
        re.search(r"(?:經|经|律|論|论|疏|記|记|錄|录|品|頌|颂|咒)$", title_stem)
        or title_stem.startswith(("佛說", "佛说"))
    )
    if exact_title:
        # MediaWiki may convert a simplified requested title to a traditional
        # canonical title (金刚经 -> 金剛經).  Exact verified URLs also legitimately
        # end in 傳、林、錄 or a volume number, e.g.《續高僧傳/卷20》and
        #《法苑珠林/卷090》.  The exact ``titles=`` lookup plus the modern-page
        # exclusions above constrain the page; final eligibility still requires
        # a literal quote from the complete fetched text.
        return True
    return title_signal or category_signal


def _build_primary_source_reports(evidence: list[dict[str, Any]]) -> tuple[SourceReport, ...]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in evidence:
        grouped.setdefault(str(item.get("source_id") or ""), []).append(item)
    reports: list[SourceReport] = []
    for source_id in sorted(grouped):
        claims = grouped[source_id]
        title = str(claims[0].get("canonical_title") or claims[0].get("source_title") or "古代原典")
        urls = sorted({url for item in claims for url in item.get("evidence_urls") or []})
        reports.append(
            SourceReport(
                source_id=source_id,
                title=title,
                author="古代原典",
                chars=0,
                detected_chapters=0,
                rights_basis="古代原典；仅使用经联网定位的短证据进行事实核验，不复制现代整理材料",
                sha256=text_sha256(f"{title}\n" + "\n".join(urls)),
            )
        )
    return tuple(reports)


def _build_primary_source_digests(
    evidence: list[dict[str, Any]],
    reports: tuple[SourceReport, ...],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for report in reports:
        claims = [item for item in evidence if item.get("source_id") == report.source_id]
        result.append(
            {
                "source_id": report.source_id,
                "title": report.title,
                "evidence_status": "verified_primary",
                "core_materials": [
                    {
                        "claim_id": item.get("claim_id"),
                        "statement": item.get("statement"),
                        "qualifier": item.get("qualifier"),
                        "canonical_locator": item.get("canonical_locator"),
                    }
                    for item in claims[:30]
                ],
                "usable_topics": sorted({str(item.get("kind") or "fact") for item in claims}),
                "limits": ["只允许使用已核验的中性表述；不得补写原典未支持的故事、心理、因果或评价"],
            }
        )
    return result


def _call_openai_primary_verifier(
    work_dir: Path,
    *,
    action: str,
    prompt: str,
) -> tuple[str, list[dict[str, str]]]:
    from app.services.content_engine import (
        _extract_openai_response_text,
        _get_openai_writer_client,
        _resolve_openai_api_key,
    )

    if not _resolve_openai_api_key():
        raise PublicDomainQualityError("古代原典联网核验需要 OPENAI_API_KEY；没有核验结果时禁止生成。")
    model = str(settings.FINANCE_WEB_SEARCH_MODEL or settings.OPENAI_WRITER_MODEL or "gpt-5.5").strip()
    started = time.monotonic()
    log: dict[str, Any] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "policy_version": POLICY_VERSION,
        "action": action,
        "provider": "openai-web-search",
        "model": model,
        "input_chars": len(prompt),
        "status": "started",
    }
    try:
        response = _get_openai_writer_client().responses.create(
            model=model,
            input=prompt,
            reasoning={"effort": "low"},
            tools=[{"type": "web_search", "search_context_size": "high"}],
            tool_choice="required",
            include=["web_search_call.action.sources"],
        )
        output = _extract_openai_response_text(response).strip()
        tool_sources = _collect_web_tool_sources(response)
        if not output or not tool_sources:
            raise RuntimeError("联网核验没有同时返回正文和可审计来源")
        log.update(
            status="ok",
            output_chars=len(output),
            source_count=len(tool_sources),
            duration_ms=int((time.monotonic() - started) * 1000),
            usage_tokens=None,
            cost=None,
            currency=None,
        )
        _append_jsonl(work_dir / "llm_calls.jsonl", log)
        return output, tool_sources
    except PublicDomainQualityError:
        raise
    except Exception as exc:
        log.update(
            status="failed",
            duration_ms=int((time.monotonic() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}"[:2000],
        )
        _append_jsonl(work_dir / "llm_calls.jsonl", log)
        raise PublicDomainQualityError(f"古代原典联网核验失败：{type(exc).__name__}: {exc}") from exc


def _collect_web_tool_sources(response: Any) -> list[dict[str, str]]:
    try:
        payload = response.model_dump(mode="json")
    except Exception:
        payload = response
    found: dict[str, dict[str, str]] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            url = str(value.get("url") or "").strip()
            if url.startswith(("http://", "https://")):
                normalized = _normalize_evidence_url(url)
                if normalized:
                    found[normalized] = {
                        "url": normalized,
                        "title": str(value.get("title") or value.get("name") or "").strip()[:300],
                    }
            for child in value.values():
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)

    visit(payload)
    return list(found.values())


def _normalize_evidence_url(value: str) -> str:
    try:
        parts = urlsplit(str(value or "").strip())
    except ValueError:
        return ""
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return ""
    netloc = parts.hostname.lower()
    try:
        port = parts.port
    except ValueError:
        return ""
    if port and not (parts.scheme.lower() == "https" and port == 443):
        netloc = f"{netloc}:{port}"
    path = unquote(re.sub(r"/{2,}", "/", parts.path or "/")).rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path, "", ""))


def _is_approved_primary_url(value: str) -> bool:
    try:
        host = (urlsplit(value).hostname or "").lower()
    except ValueError:
        return False
    return any(host == allowed or host.endswith("." + allowed) for allowed in _PRIMARY_SOURCE_HOSTS)


def _normalize_canonical_title(value: str) -> str:
    title = str(value or "").strip()
    quoted = re.search(r"[《〈]([^》〉]{2,80})[》〉]", title)
    title = quoted.group(1).strip() if quoted else re.sub(r"^[《〈]|[》〉]$", "", title).strip()
    if not 1 < len(title) <= 80:
        return ""
    return f"《{title}》"


def _contains_research_process_leak(text: str) -> bool:
    return any(value in str(text or "") for value in _RESEARCH_PROCESS_LEAKS)


def _modern_source_leakage_issues(
    text: str,
    *,
    modern_sources: Iterable[PublicDomainSource],
    approved_primary_titles: set[str],
) -> list[str]:
    value = str(text or "")
    issues: list[str] = []
    for marker in _RESEARCH_PROCESS_LEAKS:
        if marker in value:
            issues.append(f"正文泄漏研究过程措辞：{marker}")
    for source in modern_sources:
        title = str(source.title or "").strip()
        author = str(source.author or "").strip()
        if len(title) >= 2 and (title in value or f"《{title.strip('《》')}》" in value):
            issues.append(f"正文出现现代整理资料名称：{title}")
        if len(author) >= 2 and author in value:
            issues.append(f"正文出现现代整理资料作者：{author}")
    approved = {item.strip() for item in approved_primary_titles if item.strip()}
    approved_plain = {item.strip("《》") for item in approved}
    for title in re.findall(r"《([^》]{1,80})》", value):
        if title.strip() not in approved_plain:
            issues.append(f"正文引用未进入核验账本的书名：《{title.strip()}》")
    return list(dict.fromkeys(issues))


def _build_source_digest(
    source: PublicDomainSource,
    report: SourceReport,
    evidence: list[dict[str, Any]],
    work_dir: Path,
    *,
    generation_mode: str = FULL_MODE,
) -> dict[str, Any]:
    path = work_dir / "evidence" / report.source_id / "digest.json"
    evidence_hash = text_sha256(json.dumps(evidence, ensure_ascii=False, sort_keys=True))
    expected_key = text_sha256(
        f"{POLICY_VERSION}\n{generation_mode}\n{report.sha256}\n{evidence_hash}"
    )
    cached = _load_versioned_artifact(path, expected_key)
    if cached is not None:
        return dict(cached.get("digest") or {})
    prioritized = sorted(evidence, key=lambda item: (-int(item.get("importance") or 1), item.get("claim_id") or ""))
    records = [
        {
            "claim_id": item.get("claim_id"),
            "kind": item.get("kind"),
            "statement": item.get("statement"),
            "qualifier": item.get("qualifier"),
        }
        for item in prioritized[:280]
    ]
    prompt = _render_prompt(
        "source_digest.md",
        source_id=report.source_id,
        source_title=source.title,
        evidence_records=json.dumps(records, ensure_ascii=False),
    )
    raw = _call_llm(
        work_dir,
        action=f"source_digest:{report.source_id}",
        prompt=prompt,
        temperature=0.0,
        max_output_tokens=2500 if generation_mode == RESEARCH_PREVIEW_MODE else 5000,
        model=PREVIEW_DEEPSEEK_MODEL if generation_mode == RESEARCH_PREVIEW_MODE else None,
    )
    digest = _parse_json_object(raw)
    digest["source_id"] = report.source_id
    digest["title"] = source.title
    _atomic_write_json(
        path,
        {
            "artifact_key": expected_key,
            "policy_version": POLICY_VERSION,
            "digest": digest,
        },
    )
    return digest


def _build_book_plan(
    *,
    work_dir: Path,
    source_reports: tuple[SourceReport, ...],
    source_digests: list[dict[str, Any]],
    dimensions: BookDimensions,
    target_title: str | None,
    target_direction: str | None,
    output_style: str | None,
    generation_mode: str = FULL_MODE,
    narrative_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = work_dir / "02_book_plan.json"
    plan_input = {
        "policy": POLICY_VERSION,
        "source_hashes": [item.sha256 for item in source_reports],
        "target_chars": dimensions.target_chars,
        "target_chapters": dimensions.target_chapters,
        "target_title": target_title or "",
        "target_direction": target_direction or "",
        "output_style": output_style or "",
        "generation_mode": generation_mode,
        # Chapter-writing fixes must not invalidate a verified book plan.
        # Keep the plan version independent so retries do not pay for or risk
        # regenerating the same planning JSON.
        "narrative_synthesis_version": NARRATIVE_PLAN_VERSION,
        "narrative_profile": narrative_profile or {},
    }
    expected_key = text_sha256(json.dumps(plan_input, ensure_ascii=False, sort_keys=True))
    cached = _load_versioned_artifact(path, expected_key)
    if cached is not None:
        return dict(cached["plan"])
    prompt = _render_prompt(
        "book_plan_research_preview.md" if generation_mode == RESEARCH_PREVIEW_MODE else "book_plan.md",
        target_chars=dimensions.target_chars,
        target_chapters=dimensions.target_chapters,
        target_title=target_title or "由材料自然拟定",
        target_direction=(
            target_direction
            or (
                "只使用已核验古代原典中的人物、事件与观点，写成叙事化非虚构新书；不新增事实，排除现代材料内容和论文式论述"
                if generation_mode == RESEARCH_PREVIEW_MODE
                else "从 8–10 本来源的交叉材料中形成独立问题与论述"
            )
        ),
        output_style=(
            output_style
            or "通俗、有叙事推进和阅读气息；不模仿任何单一来源，不写论文或经典导读，不新增原典未支持的事实"
        ),
        source_digests=json.dumps(source_digests, ensure_ascii=False),
        narrative_profile=json.dumps(narrative_profile or {}, ensure_ascii=False),
    )
    raw = _call_llm(
        work_dir,
        action="book_plan",
        prompt=prompt,
        temperature=0.30 if generation_mode == RESEARCH_PREVIEW_MODE else 0.15,
        max_output_tokens=6000 if generation_mode == RESEARCH_PREVIEW_MODE else 9000,
        model=PREVIEW_DEEPSEEK_MODEL if generation_mode == RESEARCH_PREVIEW_MODE else None,
    )
    claim_catalog = {
        str(item.get("claim_id") or ""): {
            "source_id": str(digest.get("source_id") or ""),
            "statement": str(item.get("statement") or "").strip(),
            "qualifier": str(item.get("qualifier") or "").strip(),
        }
        for digest in source_digests
        for item in digest.get("core_materials") or []
        if str(item.get("claim_id") or "").strip()
    }
    allowed_claim_ids = set(claim_catalog)
    plan = _normalize_plan(
        _parse_json_object(raw),
        source_reports,
        dimensions,
        target_title,
        generation_mode=generation_mode,
        allowed_claim_ids=allowed_claim_ids,
        claim_catalog=claim_catalog,
    )
    _atomic_write_json(
        path,
        {"artifact_key": expected_key, "policy_version": POLICY_VERSION, "plan": plan},
    )
    return plan


def _normalize_plan(
    plan: dict[str, Any],
    source_reports: tuple[SourceReport, ...],
    dimensions: BookDimensions,
    target_title: str | None,
    *,
    generation_mode: str = FULL_MODE,
    allowed_claim_ids: set[str] | None = None,
    claim_catalog: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    raw_chapters = [item for item in plan.get("chapters") or [] if isinstance(item, dict)]
    if len(raw_chapters) != dimensions.target_chapters:
        raise PublicDomainQualityError(
            f"长篇规划返回 {len(raw_chapters)} 章，必须精确为 {dimensions.target_chapters} 章。"
        )
    source_ids = [item.source_id for item in source_reports]
    if generation_mode == RESEARCH_PREVIEW_MODE and any(
        plan.get(key)
        for key in ("fiction_notice", "story_premise", "fictional_setting", "fictional_characters")
    ):
        raise PublicDomainQualityError("叙事化非虚构规划不得包含虚构人物、地点、故事前提或虚构声明。")
    base = dimensions.target_chars // dimensions.target_chapters
    remainder = dimensions.target_chars - base * dimensions.target_chapters
    chapters: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_chapters, start=1):
        selected = [value for value in raw.get("source_ids") or [] if value in source_ids]
        for offset in range(2):
            candidate = source_ids[(index - 1 + offset) % len(source_ids)]
            if candidate not in selected:
                selected.append(candidate)
        narrative_beats = []
        for value in raw.get("narrative_beats") or []:
            if not isinstance(value, dict):
                continue
            claim_ids = list(
                dict.fromkeys(
                    str(claim_id).strip()
                    for claim_id in value.get("claim_ids") or []
                    if str(claim_id).strip()
                )
            )[:8]
            narrative_beats.append(
                {
                    "description": str(value.get("description") or "").strip()[:260],
                    "claim_ids": claim_ids,
                    "boundary": str(value.get("boundary") or "").strip()[:220],
                }
            )
        narrative_beats = [
            item for item in narrative_beats if item["description"] and item["claim_ids"]
        ][:8]
        if generation_mode == RESEARCH_PREVIEW_MODE:
            # Planning models occasionally omit or truncate the nested beat array.
            # Repair the structure only from the already verified primary-evidence
            # ledger.  This is deliberately not a prose/model fallback: the exact
            # verified statement becomes the beat, so no person, event, relation,
            # cause, dialogue, or scene detail can be invented while making the
            # downstream contract runnable.
            existing_claim_ids = {
                claim_id for item in narrative_beats for claim_id in item["claim_ids"]
            }
            catalog = claim_catalog or {}
            preferred = [
                claim_id
                for claim_id, record in sorted(catalog.items())
                if record.get("source_id") in selected
                and record.get("statement")
                and claim_id not in existing_claim_ids
            ]
            remaining = [
                claim_id
                for claim_id, record in sorted(catalog.items())
                if record.get("statement")
                and claim_id not in existing_claim_ids
                and claim_id not in preferred
            ]
            candidates = preferred + remaining
            if candidates:
                offset = ((index - 1) * 6) % len(candidates)
                candidates = candidates[offset:] + candidates[:offset]
            for claim_id in candidates:
                if len(narrative_beats) >= 6:
                    break
                record = catalog[claim_id]
                narrative_beats.append(
                    {
                        "description": record["statement"][:260],
                        "claim_ids": [claim_id],
                        "boundary": (
                            record.get("qualifier")
                            or "只写该证据直接支持的事实，不补写场景、心理、对话或因果"
                        )[:220],
                    }
                )
                existing_claim_ids.add(claim_id)
            if len(narrative_beats) < 6:
                raise PublicDomainQualityError(
                    f"第 {index} 章至少需要 6 个由 claim_id 支持的叙事推进点；"
                    "核验账本也不足以安全补齐，禁止靠模型杜撰。"
                )
            unknown_claim_ids = sorted(
                {
                    claim_id
                    for item in narrative_beats
                    for claim_id in item["claim_ids"]
                    if allowed_claim_ids is not None and claim_id not in allowed_claim_ids
                }
            )
            if unknown_claim_ids:
                raise PublicDomainQualityError(
                    f"第 {index} 章叙事推进点引用未授权证据：{unknown_claim_ids[:8]}"
                )
        chapters.append(
            {
                "number": index,
                "title": str(raw.get("title") or f"第{index}章").strip()[:80],
                "purpose": str(raw.get("purpose") or "推进全书中心问题").strip()[:500],
                "target_chars": base + (1 if index <= remainder else 0),
                "source_ids": selected[:5],
                "topics": [str(value)[:80] for value in (raw.get("topics") or [])[:8]],
                "must_cover": [str(value)[:160] for value in (raw.get("must_cover") or [])[:10]],
                "must_not_claim": [str(value)[:160] for value in (raw.get("must_not_claim") or [])[:8]],
                "narrative_goal": str(raw.get("narrative_goal") or "").strip()[:300],
                "narrative_beats": narrative_beats,
                "evidence_function": str(raw.get("evidence_function") or "").strip()[:300],
            }
        )
    # Ensure every source participates in at least two chapter contracts.
    for source_offset, source_id in enumerate(source_ids):
        used_count = sum(source_id in item["source_ids"] for item in chapters)
        candidates = [
            chapters[index % len(chapters)]
            for index in range(source_offset, source_offset + len(chapters))
            if source_id not in chapters[index % len(chapters)]["source_ids"]
        ]
        for target in candidates:
            if used_count >= min(2, len(chapters)):
                break
            target["source_ids"].append(source_id)
            used_count += 1
    normalized = {
        "title": str(target_title or plan.get("title") or "公版融合长篇").strip()[:120],
        "subtitle": str(plan.get("subtitle") or "").strip()[:160],
        "summary": str(plan.get("summary") or "").strip()[:1200],
        "central_question": str(plan.get("central_question") or "").strip()[:500],
        "chapters": chapters,
    }
    if generation_mode == RESEARCH_PREVIEW_MODE:
        normalized.update(
            narrative_mode="verified_narrative_nonfiction",
            book_premise=str(plan.get("book_premise") or "").strip()[:800],
            narrative_thread=str(plan.get("narrative_thread") or "").strip()[:800],
        )
        if not normalized["book_premise"] or not normalized["narrative_thread"]:
            raise PublicDomainQualityError("叙事化非虚构规划缺少全书前提或主题推进线。")
    return normalized


def _narrative_blueprint(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "narrative_mode": plan.get("narrative_mode"),
        "book_premise": plan.get("book_premise"),
        "narrative_thread": plan.get("narrative_thread"),
        "central_question": plan.get("central_question"),
        "summary": plan.get("summary"),
    }


def _select_chapter_evidence(
    contract: dict[str, Any],
    evidence: list[dict[str, Any]],
    reports: tuple[SourceReport, ...],
) -> list[dict[str, Any]]:
    selected_sources = set(contract.get("source_ids") or [])
    required_claim_ids = list(
        dict.fromkeys(
            str(claim_id).strip()
            for beat in contract.get("narrative_beats") or []
            if isinstance(beat, dict)
            for claim_id in beat.get("claim_ids") or []
            if str(claim_id).strip()
        )
    )
    query = " ".join(
        [
            str(contract.get("title") or ""),
            str(contract.get("purpose") or ""),
            *[str(value) for value in contract.get("topics") or []],
            *[str(value) for value in contract.get("must_cover") or []],
        ]
    )
    query_grams = _char_grams(query, 2)
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in evidence:
        if selected_sources and item.get("source_id") not in selected_sources:
            continue
        statement = f"{item.get('statement') or ''} {item.get('qualifier') or ''}"
        grams = _char_grams(statement, 2)
        overlap = len(query_grams & grams) / max(1, len(query_grams))
        score = overlap * 10 + float(item.get("importance") or 1)
        scored.append((score, item))
    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("claim_id") or "")))
    evidence_by_id = {
        str(item.get("claim_id") or ""): item
        for item in evidence
        if str(item.get("claim_id") or "")
    }
    result: list[dict[str, Any]] = [
        evidence_by_id[claim_id]
        for claim_id in required_claim_ids
        if claim_id in evidence_by_id
    ]
    per_source: dict[str, int] = {}
    for item in result:
        source_id = str(item.get("source_id") or "")
        per_source[source_id] = per_source.get(source_id, 0) + 1
    for _, item in scored:
        if item in result:
            continue
        source_id = str(item.get("source_id") or "")
        if per_source.get(source_id, 0) >= 32:
            continue
        result.append(item)
        per_source[source_id] = per_source.get(source_id, 0) + 1
        if len(result) >= MAX_EVIDENCE_PER_CHAPTER:
            break
    if len(result) < 12:
        raise PublicDomainQualityError(
            f"第 {contract.get('number')} 章只检索到 {len(result)} 条允许证据，拒绝靠模型补写。"
        )
    return result


def _build_or_resume_chapter(
    *,
    work_dir: Path,
    chapter_number: int,
    contract: dict[str, Any],
    book_summary: str,
    previous_summary: str,
    allowed_evidence: list[dict[str, Any]],
    overlap_indexes: list[dict[str, Any]],
    generation_mode: str = FULL_MODE,
    forbidden_sources: Iterable[PublicDomainSource] = (),
    approved_source_titles: set[str] | None = None,
    narrative_blueprint: dict[str, Any] | None = None,
    narrative_profile: dict[str, Any] | None = None,
) -> ChapterResult:
    chapter_dir = work_dir / "chapters"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    path = chapter_dir / f"{chapter_number:03d}.json"
    artifact_key = text_sha256(
        json.dumps(
            {
                "policy": POLICY_VERSION,
                "contract": contract,
                "evidence_ids": [item.get("claim_id") for item in allowed_evidence],
                "previous_summary": previous_summary,
                "generation_mode": generation_mode,
                "narrative_synthesis_version": NARRATIVE_SYNTHESIS_VERSION,
                "narrative_blueprint": narrative_blueprint or {},
                "narrative_profile": narrative_profile or {},
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    cached = _load_versioned_artifact(path, artifact_key)
    if cached is not None and bool((cached.get("chapter") or {}).get("audit", {}).get("pass")):
        return _chapter_from_dict(dict(cached["chapter"]))

    evidence_payload = [
        {
            "claim_id": item.get("claim_id"),
            "source_id": item.get("source_id"),
            "source_title": item.get("source_title"),
            "canonical_locator": item.get("canonical_locator"),
            "evidence_status": item.get("evidence_status"),
            "kind": item.get("kind"),
            "statement": item.get("statement"),
            "qualifier": item.get("qualifier"),
        }
        for item in allowed_evidence
    ]
    target_chars = int(contract["target_chars"])
    minimum_chars = int(target_chars * 0.90)
    maximum_chars = int(target_chars * 1.10)
    candidate: dict[str, Any] | None = None
    resumed_failed_candidate = False
    failed_path = chapter_dir / f"{chapter_number:03d}.failed.json"
    checkpoint_path = chapter_dir / f"{chapter_number:03d}.checkpoint.json"
    if generation_mode == RESEARCH_PREVIEW_MODE:
        for recovery_path in (checkpoint_path, failed_path):
            if not recovery_path.exists():
                continue
            try:
                failed_payload = json.loads(recovery_path.read_text(encoding="utf-8"))
                failed_candidate = dict(failed_payload.get("last_candidate") or {})
                failed_text = str(failed_candidate.get("text") or "").strip()
                failed_ids = tuple(str(value) for value in failed_candidate.get("used_claim_ids") or [])
                allowed_ids = {str(item.get("claim_id") or "") for item in allowed_evidence}
                if (
                    failed_payload.get("artifact_key") == artifact_key
                    and failed_payload.get("contract") == contract
                    and int(target_chars * 0.88) <= count_text_chars(failed_text) <= int(target_chars * 1.12)
                    and failed_ids
                    and all(value in allowed_ids for value in failed_ids)
                ):
                    candidate = failed_candidate
                    resumed_failed_candidate = True
                    break
            except (OSError, TypeError, ValueError):
                continue
    if candidate is None:
        prompt = _render_prompt(
            (
                "chapter_writer_research_preview.md"
                if generation_mode == RESEARCH_PREVIEW_MODE
                else "chapter_writer.md"
            ),
            book_summary=book_summary,
            previous_summary=previous_summary,
            chapter_contract=json.dumps(contract, ensure_ascii=False),
            allowed_evidence=json.dumps(evidence_payload, ensure_ascii=False),
            target_chars=int(target_chars * 1.08),
            minimum_chars=minimum_chars,
            maximum_chars=maximum_chars,
            narrative_blueprint=json.dumps(narrative_blueprint or {}, ensure_ascii=False),
            narrative_profile=json.dumps(narrative_profile or {}, ensure_ascii=False),
        )
        if generation_mode == RESEARCH_PREVIEW_MODE:
            raw = _call_openai_preview_writer(
                work_dir,
                action=f"chapter_write:{chapter_number:03d}",
                prompt=prompt,
                max_output_tokens=_chapter_tokens(target_chars),
            )
        else:
            raw = _call_llm(
                work_dir,
                action=f"chapter_write:{chapter_number:03d}",
                prompt=prompt,
                temperature=0.35,
                max_output_tokens=_chapter_tokens(target_chars),
            )
        candidate = _parse_json_object(raw)
    last_audit: dict[str, Any] = {}
    attempt_limit = (
        PREVIEW_MAX_CHAPTER_ATTEMPTS
        if generation_mode == RESEARCH_PREVIEW_MODE
        else MAX_CHAPTER_ATTEMPTS
    )
    best_full_candidate: dict[str, Any] | None = None
    best_full_audit: dict[str, Any] | None = None
    for attempt in range(1, attempt_limit + 1):
        text = str(candidate.get("text") or "").strip()
        title = str(candidate.get("title") or contract.get("title") or f"第{chapter_number}章").strip()
        summary = str(candidate.get("summary") or "").strip()
        used_claim_ids = tuple(dict.fromkeys(str(value) for value in candidate.get("used_claim_ids") or []))
        deterministic = _chapter_deterministic_issues(
            text=text,
            contract=contract,
            target_chars=int(contract["target_chars"]),
            used_claim_ids=used_claim_ids,
            allowed_evidence=allowed_evidence,
            overlap_indexes=overlap_indexes,
            forbidden_sources=forbidden_sources,
            approved_source_titles=approved_source_titles,
            require_narrative_nonfiction=generation_mode == RESEARCH_PREVIEW_MODE,
        )
        audit = _audit_chapter(
            work_dir=work_dir,
            chapter_number=chapter_number,
            contract=contract,
            allowed_evidence=evidence_payload,
            chapter_text=text,
            generation_mode=generation_mode,
            narrative_blueprint=narrative_blueprint,
            narrative_profile=narrative_profile,
        )
        if deterministic:
            audit["pass"] = False
            audit.setdefault("repair_instructions", []).extend(deterministic)
        last_audit = audit
        audit_has_content_issue = any(
            audit.get(key)
            for key in ("unsupported_claims", "contradictions", "missing_materials", "language_issues")
        )
        if (
            generation_mode == RESEARCH_PREVIEW_MODE
            and not audit_has_content_issue
            and candidate
            and count_text_chars(text) >= max(1, int(target_chars * 0.60))
            and (best_full_candidate is None or count_text_chars(text) > count_text_chars(str(best_full_candidate.get("text") or "")))
        ):
            # Keep a safe short candidate as a repair base. A later model
            # repair that introduces unsupported claims must never replace it.
            best_full_candidate = dict(candidate)
            best_full_audit = dict(audit)
        if minimum_chars <= count_text_chars(text) <= maximum_chars and not audit_has_content_issue:
            best_full_candidate = dict(candidate)
            best_full_audit = dict(audit)
            if generation_mode == RESEARCH_PREVIEW_MODE:
                _atomic_write_json(
                    checkpoint_path,
                    {
                        "artifact_key": artifact_key,
                        "policy_version": POLICY_VERSION,
                        "contract": contract,
                        "last_candidate": candidate,
                        "last_audit": audit,
                    },
                )
        if _audit_passed(audit):
            chapter = ChapterResult(
                number=chapter_number,
                title=title[:120],
                text=text,
                summary=summary[:800] or _fallback_summary(text),
                used_claim_ids=used_claim_ids,
                audit=audit,
            )
            _atomic_write_json(
                path,
                {
                    "artifact_key": artifact_key,
                    "policy_version": POLICY_VERSION,
                    "chapter": _chapter_to_dict(chapter),
                },
            )
            _atomic_write_text(chapter_dir / f"{chapter_number:03d}.md", text)
            checkpoint_path.unlink(missing_ok=True)
            return chapter
        if attempt >= attempt_limit:
            break
        if (
            generation_mode == RESEARCH_PREVIEW_MODE
            and _can_apply_short_preview_expansion(
                text=text,
                audit=audit,
                deterministic_issues=deterministic,
                minimum_chars=minimum_chars,
            )
        ):
            expanded = _expand_short_preview_candidate(
                work_dir=work_dir,
                chapter_number=chapter_number,
                candidate=candidate,
                contract=contract,
                allowed_evidence=evidence_payload,
                minimum_chars=minimum_chars,
                maximum_chars=maximum_chars,
                narrative_blueprint=narrative_blueprint,
                narrative_profile=narrative_profile,
            )
            if expanded is not None:
                candidate = expanded
                continue
        repair_source, repair_audit = _select_chapter_repair_source(
            candidate=candidate,
            audit=audit,
            best_full_candidate=best_full_candidate,
            best_full_audit=best_full_audit,
            target_chars=int(target_chars * 1.08),
            generation_mode=generation_mode,
        )
        repair_source_text = str(repair_source.get("text") or "").strip()
        repair_prompt = _render_prompt(
            (
                "chapter_repair_research_preview.md"
                if generation_mode == RESEARCH_PREVIEW_MODE
                else "chapter_repair.md"
            ),
            chapter_contract=json.dumps(contract, ensure_ascii=False),
            allowed_evidence=json.dumps(evidence_payload, ensure_ascii=False),
            audit_report=json.dumps(repair_audit, ensure_ascii=False),
            chapter_text=repair_source_text,
            current_chars=count_text_chars(repair_source_text),
            target_chars=target_chars,
            minimum_chars=minimum_chars,
            maximum_chars=maximum_chars,
            narrative_blueprint=json.dumps(narrative_blueprint or {}, ensure_ascii=False),
            narrative_profile=json.dumps(narrative_profile or {}, ensure_ascii=False),
        )
        action = f"chapter_repair:{chapter_number:03d}:attempt{attempt}"
        repaired = _call_chapter_repair_model(
            work_dir,
            action=action,
            prompt=repair_prompt,
            max_output_tokens=_chapter_tokens(target_chars),
            generation_mode=generation_mode,
            prefer_openai=bool(resumed_failed_candidate or attempt >= 3),
        )
        candidate = _parse_json_object(repaired)
    _atomic_write_json(
        chapter_dir / f"{chapter_number:03d}.failed.json",
        {
            "artifact_key": artifact_key,
            "policy_version": POLICY_VERSION,
            "contract": contract,
            "last_candidate": candidate,
            "last_audit": last_audit,
        },
    )
    raise PublicDomainQualityError(
        f"第 {chapter_number} 章在 {attempt_limit} 次内未同时通过事实、篇幅、来源重合与语言闸门。"
    )


def _can_apply_short_preview_expansion(
    *,
    text: str,
    audit: dict[str, Any],
    deterministic_issues: list[str],
    minimum_chars: int,
) -> bool:
    """Use a narrow continuation only when length is the sole failing gate."""

    current_chars = count_text_chars(text)
    if current_chars < int(minimum_chars * 0.72) or current_chars >= minimum_chars:
        return False
    for key in ("unsupported_claims", "contradictions", "missing_materials", "language_issues"):
        if audit.get(key):
            return False
    return bool(deterministic_issues) and all(
        _is_length_only_issue(str(issue)) for issue in deterministic_issues
    )


def _is_length_only_issue(issue: str) -> bool:
    normalized = re.sub(r"\s+", "", str(issue or ""))
    return (
        ("字数" in normalized or "有效字" in normalized or "长度" in normalized)
        and ("必须" in normalized or "范围" in normalized or "比例" in normalized)
    )


def _expand_short_preview_candidate(
    *,
    work_dir: Path,
    chapter_number: int,
    candidate: dict[str, Any],
    contract: dict[str, Any],
    allowed_evidence: list[dict[str, Any]],
    minimum_chars: int,
    maximum_chars: int,
    narrative_blueprint: dict[str, Any] | None,
    narrative_profile: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Insert a small evidence-bound passage instead of rewriting a safe chapter.

    The returned candidate is never accepted here. It re-enters the complete
    deterministic and model audit on the next loop, so this optimization cannot
    bypass fact, modern-source, overlap, or language gates.
    """

    current_text = str(candidate.get("text") or "").strip()
    current_chars = count_text_chars(current_text)
    if not current_text or current_chars >= minimum_chars:
        return None

    allowed_by_id = {
        str(item.get("claim_id") or ""): item
        for item in allowed_evidence
        if str(item.get("claim_id") or "")
    }
    used_ids = tuple(
        dict.fromkeys(str(value) for value in candidate.get("used_claim_ids") or [])
    )
    used_id_set = set(used_ids)
    unused_evidence = [
        item
        for claim_id, item in allowed_by_id.items()
        if claim_id not in used_id_set
    ]
    if not unused_evidence:
        return None

    desired_additional_chars = min(
        max(180, int(contract.get("target_chars") or minimum_chars) - current_chars + 120),
        max(180, maximum_chars - current_chars - 40),
    )
    prompt = _render_prompt(
        "chapter_expand_research_preview.md",
        chapter_contract=json.dumps(contract, ensure_ascii=False),
        unused_evidence=json.dumps(unused_evidence[:16], ensure_ascii=False),
        chapter_text=current_text,
        current_chars=current_chars,
        minimum_chars=minimum_chars,
        maximum_chars=maximum_chars,
        desired_additional_chars=desired_additional_chars,
        narrative_blueprint=json.dumps(narrative_blueprint or {}, ensure_ascii=False),
        narrative_profile=json.dumps(narrative_profile or {}, ensure_ascii=False),
    )
    raw = _call_openai_preview_writer(
        work_dir,
        action=f"chapter_expand:{chapter_number:03d}",
        prompt=prompt,
        max_output_tokens=2_400,
    )
    try:
        patch = _parse_json_object(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    insertion = str(patch.get("insertion") or "").strip()
    patch_ids = tuple(
        dict.fromkeys(str(value) for value in patch.get("used_claim_ids") or [])
    )
    if (
        not insertion
        or not patch_ids
        or any(claim_id not in allowed_by_id for claim_id in patch_ids)
        or count_text_chars(insertion) < 120
    ):
        return None

    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", current_text)
        if paragraph.strip()
    ]
    if len(paragraphs) >= 2:
        merged_text = "\n\n".join([*paragraphs[:-1], insertion, paragraphs[-1]])
    else:
        merged_text = f"{current_text}\n\n{insertion}"
    merged_chars = count_text_chars(merged_text)
    if merged_chars > maximum_chars or merged_chars <= current_chars:
        return None
    return {
        **candidate,
        "text": merged_text,
        "used_claim_ids": list(dict.fromkeys([*used_ids, *patch_ids])),
    }


def _select_chapter_repair_source(
    *,
    candidate: dict[str, Any],
    audit: dict[str, Any],
    best_full_candidate: dict[str, Any] | None,
    best_full_audit: dict[str, Any] | None,
    target_chars: int,
    generation_mode: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Keep cumulative safe repairs instead of restoring rejected prose.

    Removing unsupported quotations can temporarily shorten a preview chapter.
    Reverting every such round to the first full-length draft reintroduces the
    same rejected claims forever. A current candidate retaining at least 60%
    of the contract length remains the repair base, so the next round can fill
    only from the approved evidence ledger. Truly truncated output still rolls
    back to the best complete candidate.
    """

    if generation_mode == RESEARCH_PREVIEW_MODE:
        current_text = str(candidate.get("text") or "").strip()
        current_chars = count_text_chars(current_text)
        content_issue = any(
            audit.get(key)
            for key in ("unsupported_claims", "contradictions", "missing_materials", "language_issues")
        )
        if content_issue and best_full_candidate is not None:
            return best_full_candidate, best_full_audit or audit
        if current_text and current_chars >= max(1, int(target_chars * 0.60)):
            return candidate, audit
    return best_full_candidate or candidate, best_full_audit or audit


def _call_chapter_repair_model(
    work_dir: Path,
    *,
    action: str,
    prompt: str,
    max_output_tokens: int,
    generation_mode: str,
    prefer_openai: bool,
) -> str:
    """Call a repair model without losing a chapter on provider failure.

    A preview repair is recoverable work: the paid candidate and its audit are
    already checkpointed.  Use the configured OpenAI preview writer for the
    evidence-bound prose repair; full production mode keeps its original
    fail-fast DeepSeek behavior.
    """

    if generation_mode == RESEARCH_PREVIEW_MODE:
        return _call_openai_preview_writer(
            work_dir,
            action=action,
            prompt=prompt,
            max_output_tokens=max_output_tokens,
        )
    try:
        return _call_llm(
            work_dir,
            action=action,
            prompt=prompt,
            temperature=0.30,
            max_output_tokens=max_output_tokens,
            model=PREVIEW_DEEPSEEK_MODEL if generation_mode == RESEARCH_PREVIEW_MODE else None,
        )
    except Exception:
        if generation_mode != RESEARCH_PREVIEW_MODE:
            raise
        return _call_openai_preview_writer(
            work_dir,
            action=action,
            prompt=prompt,
            max_output_tokens=max_output_tokens,
        )


def _audit_chapter(
    *,
    work_dir: Path,
    chapter_number: int,
    contract: dict[str, Any],
    allowed_evidence: list[dict[str, Any]],
    chapter_text: str,
    generation_mode: str = FULL_MODE,
    narrative_blueprint: dict[str, Any] | None = None,
    narrative_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prompt = _render_prompt(
        (
            "chapter_audit_research_preview.md"
            if generation_mode == RESEARCH_PREVIEW_MODE
            else "chapter_audit.md"
        ),
        chapter_contract=json.dumps(contract, ensure_ascii=False),
        allowed_evidence=json.dumps(allowed_evidence, ensure_ascii=False),
        chapter_text=chapter_text,
        narrative_blueprint=json.dumps(narrative_blueprint or {}, ensure_ascii=False),
        narrative_profile=json.dumps(narrative_profile or {}, ensure_ascii=False),
    )
    action = f"chapter_audit:{chapter_number:03d}"
    raw = _call_auditor(
        work_dir,
        action=action,
        prompt=prompt,
        temperature=0.0,
        max_output_tokens=5000,
        generation_mode=generation_mode,
    )
    try:
        audit = _parse_json_object(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        # Gemini occasionally returns a short natural-language refusal or an
        # incomplete object while the provider call itself is reported as OK.
        # Treat that as a provider-format failure, never as an audit result.
        # Research previews can safely fail over to the already configured
        # independent OpenAI auditor without regenerating the paid chapter.
        _append_jsonl(
            work_dir / "llm_calls.jsonl",
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "policy_version": POLICY_VERSION,
                "action": f"{action}:parse",
                "provider": "audit-parser",
                "status": "malformed_output",
                "output_chars": len(raw or ""),
                "error": f"{type(exc).__name__}: {exc}"[:2000],
            },
        )
        if generation_mode != RESEARCH_PREVIEW_MODE:
            raise
        fallback_prompt = (
            prompt
            + "\n\n上一个审稿器未返回可解析的 JSON。请重新独立审查，并且只输出题目要求的单个完整 JSON 对象；"
            "不要解释、不要 Markdown、不要省略任何数组字段。"
        )
        fallback_raw = _call_openai_auditor(
            work_dir,
            action=f"{action}:json_fallback",
            prompt=fallback_prompt,
            max_output_tokens=5000,
        )
        audit = _parse_json_object(fallback_raw)
    audit["pass"] = bool(audit.get("pass"))
    for key in ("unsupported_claims", "contradictions", "missing_materials", "language_issues", "repair_instructions"):
        audit[key] = [str(value) for value in audit.get(key) or []]
    if generation_mode == RESEARCH_PREVIEW_MODE:
        allowed_ids = {
            str(item.get("claim_id") or "")
            for item in allowed_evidence
            if str(item.get("claim_id") or "")
        }
        try:
            fact_count = int(audit.get("fact_sentence_count"))
            mapped_count = int(audit.get("mapped_fact_sentence_count"))
        except (TypeError, ValueError):
            fact_count = -1
            mapped_count = -1
        evidence_map = [
            item for item in audit.get("evidence_map") or [] if isinstance(item, dict)
        ]
        invalid_rows = []
        for item in evidence_map:
            claim_ids = [
                str(value).strip()
                for value in item.get("claim_ids") or []
                if str(value).strip()
            ]
            if (
                not str(item.get("fact") or "").strip()
                or not claim_ids
                or any(value not in allowed_ids for value in claim_ids)
            ):
                invalid_rows.append(item)
        if (
            fact_count < 1
            or mapped_count != fact_count
            or len(evidence_map) != fact_count
            or invalid_rows
        ):
            audit["pass"] = False
            audit["repair_instructions"].append(
                "事实句证据映射不完整：必须逐句映射，且 fact_sentence_count、mapped_fact_sentence_count 与 evidence_map 条目数完全一致，所有 claim_id 均在允许证据内"
            )
        audit["fact_sentence_count"] = fact_count
        audit["mapped_fact_sentence_count"] = mapped_count
        audit["evidence_map"] = evidence_map
    return audit


def _chapter_deterministic_issues(
    *,
    text: str,
    contract: dict[str, Any],
    target_chars: int,
    used_claim_ids: tuple[str, ...],
    allowed_evidence: list[dict[str, Any]],
    overlap_indexes: list[dict[str, Any]],
    forbidden_sources: Iterable[PublicDomainSource] = (),
    approved_source_titles: set[str] | None = None,
    require_narrative_nonfiction: bool = False,
) -> list[str]:
    issues: list[str] = []
    chars = count_text_chars(text)
    lower = int(target_chars * 0.88)
    upper = int(target_chars * 1.12)
    if chars < lower or chars > upper:
        issues.append(f"正文有效字数 {chars}，必须在 {lower}–{upper} 字之间")
    allowed_ids = {str(item.get("claim_id") or "") for item in allowed_evidence}
    unknown_ids = [value for value in used_claim_ids if value not in allowed_ids]
    if unknown_ids:
        issues.append(f"used_claim_ids 含未授权证据：{', '.join(unknown_ids[:8])}")
    minimum_evidence = 6 if require_narrative_nonfiction else 4
    if len([value for value in used_claim_ids if value in allowed_ids]) < minimum_evidence:
        issues.append(
            f"至少要明确使用 {minimum_evidence} 条允许证据，不能靠空泛扩写凑篇幅"
        )
    required_claim_ids = {
        str(claim_id).strip()
        for beat in (contract.get("narrative_beats") or [])
        if isinstance(beat, dict)
        for claim_id in beat.get("claim_ids") or []
        if str(claim_id).strip()
    }
    missing_required_ids = sorted(required_claim_ids - set(used_claim_ids))
    if missing_required_ids:
        issues.append(f"章节未覆盖计划中的叙事证据：{missing_required_ids[:8]}")
    ineligible = [
        item.get("claim_id")
        for item in allowed_evidence
        if item.get("evidence_status") == "discovery_only" or item.get("generation_eligible") is False
    ]
    if ineligible:
        issues.append("允许证据中混入 discovery_only 现代材料线索，已拒绝候选")
    issues.extend(
        _modern_source_leakage_issues(
            text,
            modern_sources=forbidden_sources,
            approved_primary_titles=approved_source_titles or set(),
        )
    )
    residues = [value for value in _AI_RESIDUE if value in text.lower()]
    if residues:
        issues.append(f"存在模型说明或任务残留：{', '.join(residues)}")
    if require_narrative_nonfiction:
        issues.extend(_narrative_nonfiction_form_issues(text))
    blocked = [item for item in _overlap_reports(text, overlap_indexes) if _overlap_blocked(item)]
    for item in blocked:
        issues.append(
            f"与《{item.source_title}》连续表达重合偏高（{item.ratio:.2%}，长连续重合={item.long_exact_match}）"
        )
    return issues


def _post_edit_chapter_issues(
    *,
    text: str,
    target_chars: int,
    overlap_indexes: list[dict[str, Any]],
    require_narrative_nonfiction: bool = False,
) -> list[str]:
    issues: list[str] = []
    chars = count_text_chars(text)
    lower = int(target_chars * 0.88)
    upper = int(target_chars * 1.12)
    if chars < lower or chars > upper:
        issues.append(f"正文有效字数 {chars}，必须在 {lower}–{upper} 字之间")
    residues = [value for value in _AI_RESIDUE if value in text.lower()]
    if residues:
        issues.append(f"存在模型说明或任务残留：{', '.join(residues)}")
    if require_narrative_nonfiction:
        issues.extend(_narrative_nonfiction_form_issues(text))
    for item in _overlap_reports(text, overlap_indexes):
        if _overlap_blocked(item):
            issues.append(
                f"与《{item.source_title}》连续表达重合偏高（{item.ratio:.2%}，长连续重合={item.long_exact_match}）"
            )
    return issues


def _narrative_nonfiction_form_metrics(text: str) -> dict[str, Any]:
    chars = count_text_chars(text)
    paragraphs = [value.strip() for value in re.split(r"\n+", text) if value.strip()]
    sentences = [
        count_text_chars(value)
        for value in re.split(r"[。！？!?]+", text)
        if count_text_chars(value) > 0
    ]
    academic_markers = {
        marker: text.count(marker)
        for marker in _ACADEMIC_NARRATIVE_MARKERS
        if marker in text
    }
    attribution_count = sum(text.count(marker) for marker in _ACADEMIC_ATTRIBUTION_TERMS)
    return {
        "chars": chars,
        "paragraph_count": len(paragraphs),
        "sentence_mean": round(statistics.mean(sentences), 3) if sentences else 0.0,
        "sentence_sd": round(statistics.pstdev(sentences), 3) if len(sentences) > 1 else 0.0,
        "short_sentence_ratio": (
            round(sum(value <= 12 for value in sentences) / len(sentences), 4) if sentences else 0.0
        ),
        "book_title_mentions": len(re.findall(r"《[^》]{1,80}》", text)),
        "academic_attribution_count": attribution_count,
        "academic_marker_counts": academic_markers,
        "direct_quote_marks": min(text.count("“"), text.count("”")),
    }


def _narrative_nonfiction_form_issues(
    text: str,
    *,
    whole_book: bool = False,
) -> list[str]:
    metrics = _narrative_nonfiction_form_metrics(text)
    issues: list[str] = []
    if metrics["academic_marker_counts"]:
        labels = "、".join(metrics["academic_marker_counts"].keys())
        issues.append(f"叙事化非虚构正文出现论文/资料汇编式模板句：{labels}")
    attribution_limit = max(2, int(metrics["chars"] / 2_000))
    if metrics["academic_attribution_count"] > attribution_limit:
        issues.append(
            "正文以原典归属和证据边界代替叙事："
            f"归属词 {metrics['academic_attribution_count']} 次，最多允许 {attribution_limit} 次"
        )
    if not whole_book and metrics["book_title_mentions"] > 1:
        issues.append(
            f"单章出现 {metrics['book_title_mentions']} 处书名号文献归属，叙事正文最多允许 1 处"
        )
    if metrics["chars"] >= 1_500 and metrics["paragraph_count"] < 6:
        issues.append("叙事化非虚构段落过度整块化，至少需要 6 个自然段承载不同信息与节奏")
    if metrics["direct_quote_marks"]:
        issues.append(
            f"正文出现 {metrics['direct_quote_marks']} 处直接引语；写作层没有原典逐字引文权限，必须改为有证据支持的间接转述"
        )
    return issues


def _run_final_gate(
    *,
    work_dir: Path,
    markdown: str,
    plan: dict[str, Any],
    chapters: list[ChapterResult],
    source_reports: tuple[SourceReport, ...],
    dimensions: BookDimensions,
    overlap_indexes: list[dict[str, Any]],
    generation_mode: str = FULL_MODE,
    verified_evidence: list[dict[str, Any]] | None = None,
    forbidden_sources: Iterable[PublicDomainSource] = (),
    narrative_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = "\n\n".join(item.text for item in chapters)
    body_chars = count_text_chars(body)
    blocking: list[str] = []
    if len(chapters) != dimensions.target_chapters:
        blocking.append(f"章节数 {len(chapters)}，目标为 {dimensions.target_chapters}")
    lower = int(dimensions.target_chars * 0.90)
    upper = int(dimensions.target_chars * 1.10)
    if not lower <= body_chars <= upper:
        blocking.append(f"全书有效字数 {body_chars}，必须在 {lower}–{upper} 字之间")
    failed_chapters = [item.number for item in chapters if not _audit_passed(item.audit)]
    if failed_chapters:
        blocking.append(f"章节审查未通过：{failed_chapters}")

    if generation_mode == RESEARCH_PREVIEW_MODE:
        verified_items = list(verified_evidence or [])
        if len(verified_items) < PREVIEW_MIN_VERIFIED_CLAIMS:
            blocking.append(
                f"古代原典核验证据只有 {len(verified_items)} 条，至少需要 {PREVIEW_MIN_VERIFIED_CLAIMS} 条"
            )
        invalid_verified = [
            item.get("claim_id")
            for item in verified_items
            if item.get("evidence_status") != "verified_primary"
            or not bool(item.get("generation_eligible"))
            or not item.get("canonical_locator")
            or not item.get("evidence_urls")
        ]
        if invalid_verified:
            blocking.append(f"原典证据账本含未晋级或不可审计记录：{invalid_verified[:8]}")
        blocking.extend(
            _modern_source_leakage_issues(
                body,
                modern_sources=forbidden_sources,
                approved_primary_titles={item.title for item in source_reports},
            )
        )
        blocking.extend(_narrative_nonfiction_form_issues(body, whole_book=True))

    source_usage: dict[str, int] = {item.source_id: 0 for item in source_reports}
    source_chapter_usage: dict[str, set[int]] = {item.source_id: set() for item in source_reports}
    for chapter in chapters:
        for claim_id in chapter.used_claim_ids:
            source_id = claim_id.split("-", 1)[0]
            if source_id in source_usage:
                source_usage[source_id] += 1
                source_chapter_usage[source_id].add(chapter.number)
    used_claim_ids = {
        str(claim_id)
        for chapter in chapters
        for claim_id in chapter.used_claim_ids
        if str(claim_id).strip()
    }
    used_source_ids = {claim_id.split("-", 1)[0] for claim_id in used_claim_ids}
    blocking.extend(
        _source_contribution_issues(
            source_reports=source_reports,
            source_usage=source_usage,
            source_chapter_usage=source_chapter_usage,
            used_claim_ids=used_claim_ids,
            used_source_ids=used_source_ids,
            generation_mode=generation_mode,
            verified_claim_count=len(verified_evidence or []),
        )
    )

    overlap = _overlap_reports(body, overlap_indexes)
    for item in overlap:
        if _overlap_blocked(item):
            blocking.append(
                f"与 {item.source_id}《{item.source_title}》重合超限：{item.ratio:.2%}，长连续重合={item.long_exact_match}"
            )
    duplicate_ratio = _internal_duplicate_ratio(body)
    if duplicate_ratio > 0.018:
        blocking.append(f"成稿内部连续重复比例 {duplicate_ratio:.2%}，超过 1.80%")

    deterministic = {
        "policy_version": POLICY_VERSION,
        "generation_mode": generation_mode,
        "target_chars": dimensions.target_chars,
        "actual_chars": body_chars,
        "target_chapters": dimensions.target_chapters,
        "actual_chapters": len(chapters),
        "source_usage": source_usage,
        "source_chapter_usage": {key: sorted(value) for key, value in source_chapter_usage.items()},
        "distinct_used_claim_count": len(used_claim_ids),
        "distinct_used_primary_title_count": len(used_source_ids),
        "source_overlap": [asdict(item) for item in overlap],
        "internal_duplicate_ratio": duplicate_ratio,
        "rights_complete": all(bool(item.rights_basis) for item in source_reports),
        "verified_primary_claim_count": (
            len(verified_evidence or []) if generation_mode == RESEARCH_PREVIEW_MODE else None
        ),
        "modern_source_leakage_count": len(
            _modern_source_leakage_issues(
                body,
                modern_sources=forbidden_sources,
                approved_primary_titles={item.title for item in source_reports},
            )
        ) if generation_mode == RESEARCH_PREVIEW_MODE else 0,
        "blocking_issues": blocking,
        "narrative_synthesis_version": (
            NARRATIVE_SYNTHESIS_VERSION if generation_mode == RESEARCH_PREVIEW_MODE else None
        ),
        "narrative_profile": narrative_profile if generation_mode == RESEARCH_PREVIEW_MODE else None,
        "narrative_nonfiction_form": (
            _narrative_nonfiction_form_metrics(body) if generation_mode == RESEARCH_PREVIEW_MODE else None
        ),
    }
    audit_material = {
        "plan": plan,
        "chapter_summaries": [
            {"number": item.number, "title": item.title, "summary": item.summary, "audit": item.audit}
            for item in chapters
        ],
        "source_reports": [asdict(item) for item in source_reports],
        "deterministic": deterministic,
    }
    prompt = _render_prompt(
        "final_audit_research_preview.md" if generation_mode == RESEARCH_PREVIEW_MODE else "final_audit.md",
        audit_material=json.dumps(audit_material, ensure_ascii=False),
    )
    raw = _call_auditor(
        work_dir,
        action="final_audit",
        prompt=prompt,
        temperature=0.0,
        max_output_tokens=5000,
        generation_mode=generation_mode,
    )
    try:
        model_audit = _parse_json_object(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        _append_jsonl(
            work_dir / "llm_calls.jsonl",
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "policy_version": POLICY_VERSION,
                "action": "final_audit:parse",
                "provider": "audit-parser",
                "status": "malformed_output",
                "output_chars": len(raw or ""),
                "error": f"{type(exc).__name__}: {exc}"[:2000],
            },
        )
        if generation_mode != RESEARCH_PREVIEW_MODE:
            raise
        fallback_raw = _call_openai_auditor(
            work_dir,
            action="final_audit:json_fallback",
            prompt=(
                prompt
                + "\n\n上一个审稿器未返回可解析的 JSON。请重新独立终审，只输出题目要求的单个完整 JSON 对象；"
                "不要解释、不要 Markdown、不要省略字段。"
            ),
            max_output_tokens=5000,
        )
        model_audit = _parse_json_object(fallback_raw)
    model_blocking = [str(value) for value in model_audit.get("blocking_issues") or []]
    passed = not blocking and bool(model_audit.get("pass")) and not model_blocking
    quality = {
        **deterministic,
        "model_audit": model_audit,
        "passed": passed,
        "final_sha256": text_sha256(markdown) if passed else None,
    }
    _atomic_write_json(work_dir / "quality_report.json", quality)
    if not passed:
        reasons = blocking + model_blocking
        raise PublicDomainQualityError("全书终审未通过：" + "；".join(reasons[:8]))
    return quality


def _source_contribution_issues(
    *,
    source_reports: tuple[SourceReport, ...],
    source_usage: dict[str, int],
    source_chapter_usage: dict[str, set[int]],
    used_claim_ids: set[str],
    used_source_ids: set[str],
    generation_mode: str,
    verified_claim_count: int,
) -> list[str]:
    """Validate meaningful source use without making previews impossible.

    A three-chapter preview may verify many one-claim primary titles. Requiring
    two claims from every verified title is mathematically impossible and also
    turns optional planning sources into mandatory padding. Chapter audits
    already enforce every ``must_cover`` item. The preview final gate therefore
    checks strong manuscript-wide diversity; full-length mode keeps the stricter
    per-source, cross-chapter contribution rule.
    """

    issues: list[str] = []
    if generation_mode == RESEARCH_PREVIEW_MODE:
        required_claims = min(PREVIEW_MIN_USED_CLAIMS, max(0, verified_claim_count))
        required_sources = min(PREVIEW_MIN_USED_PRIMARY_TITLES, len(source_reports))
        if len(used_claim_ids) < required_claims:
            issues.append(
                f"全稿仅实质使用 {len(used_claim_ids)} 条不同原典证据，至少需要 {required_claims} 条"
            )
        if len(used_source_ids) < required_sources:
            issues.append(
                f"全稿仅实质使用 {len(used_source_ids)} 部不同古代原典，至少需要 {required_sources} 部"
            )
        return issues

    for report in source_reports:
        if source_usage[report.source_id] < 4 or len(source_chapter_usage[report.source_id]) < 2:
            issues.append(
                f"{report.source_id}《{report.title}》未达到至少 4 条证据、分布在至少 2 章的实质贡献"
            )
    return issues


def _build_overlap_indexes(
    sources: Iterable[PublicDomainSource],
    reports: tuple[SourceReport, ...],
) -> list[dict[str, Any]]:
    indexes: list[dict[str, Any]] = []
    for source, report in zip(sources, reports):
        compact = _compact(source.text)
        grams = {
            hash(compact[index : index + OVERLAP_WINDOW])
            for index in range(0, max(0, len(compact) - OVERLAP_WINDOW + 1), OVERLAP_SOURCE_STEP)
        }
        indexes.append(
            {
                "source_id": report.source_id,
                "title": report.title,
                "compact": compact,
                "grams": grams,
            }
        )
    return indexes


def _overlap_reports(text: str, indexes: list[dict[str, Any]]) -> list[OverlapReport]:
    compact = _compact(text)
    windows = [
        compact[index : index + OVERLAP_WINDOW]
        for index in range(0, max(0, len(compact) - OVERLAP_WINDOW + 1), OVERLAP_OUTPUT_STEP)
    ]
    long_windows = [
        compact[index : index + LONG_EXACT_WINDOW]
        for index in range(0, max(0, len(compact) - LONG_EXACT_WINDOW + 1), max(10, LONG_EXACT_WINDOW // 3))
    ]
    reports: list[OverlapReport] = []
    for item in indexes:
        source_grams = item["grams"]
        hits = [value for value in windows if hash(value) in source_grams]
        long_exact = any(value and value in item["compact"] for value in long_windows)
        reports.append(
            OverlapReport(
                source_id=str(item["source_id"]),
                source_title=str(item["title"]),
                matched_windows=len(hits),
                total_windows=len(windows),
                ratio=len(hits) / max(1, len(windows)),
                long_exact_match=long_exact,
                samples=tuple(dict.fromkeys(hits[:5])),
            )
        )
    return reports


def _overlap_blocked(item: OverlapReport) -> bool:
    return item.ratio >= OVERLAP_RATIO_BLOCK or item.long_exact_match


def _internal_duplicate_ratio(text: str) -> float:
    compact = _compact(text)
    window = 50
    values = [compact[index : index + window] for index in range(0, max(0, len(compact) - window + 1), 5)]
    if not values:
        return 0.0
    seen: set[str] = set()
    duplicates = 0
    for value in values:
        if value in seen:
            duplicates += 1
        else:
            seen.add(value)
    return duplicates / len(values)


def _split_text(text: str) -> list[str]:
    value = normalize_source_text(text)
    if len(value) <= EVIDENCE_CHUNK_CHARS:
        return [value]
    chunks: list[str] = []
    start = 0
    while start < len(value):
        preferred_end = min(len(value), start + EVIDENCE_CHUNK_CHARS)
        end = preferred_end
        if preferred_end < len(value):
            boundary = value.rfind("\n", start + EVIDENCE_CHUNK_CHARS // 2, preferred_end)
            if boundary > start:
                end = boundary
        chunk = value[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(value):
            break
        next_start = max(start + 1, end - EVIDENCE_CHUNK_OVERLAP)
        start = next_start
    return chunks


def _write_rights_manifest(
    work_dir: Path,
    reports: tuple[SourceReport, ...],
    sources: list[PublicDomainSource],
    dimensions: BookDimensions,
    *,
    generation_mode: str = FULL_MODE,
) -> None:
    preview = generation_mode == RESEARCH_PREVIEW_MODE
    manifest = {
        "policy_version": POLICY_VERSION,
        "generation_mode": generation_mode,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "legal_note": (
            "现代整理本仅用于产生 discovery_only 检索线索、计算不含原文和身份的多书聚合叙事统计，以及成稿重合排查，不属于写作证据。"
            "只有经联网命中古代原典页面并晋级为 verified_primary 的中性事实才能进入计划、写作和审稿；"
            "写作层不得接触现代原文、现代书名、作者身份、来源句法、具体表达或现代评论。"
            "成稿只能叙述 verified_primary 古代原典直接支持的人物、事件与观点；不得新增人物、地点、情节、对话、心理、场景、因果或评价。"
            "该技术隔离不替代专业法律意见。"
            if preview
            else "系统记录并核对用户提供的权利依据，但不替代专业法律意见。任何依据不清的来源不得进入生成。"
        ),
        "dimensions": asdict(dimensions),
        "sources": [
            {
                **asdict(report),
                "publication_year": source.publication_year,
                "author_death_year": source.author_death_year,
                "rights_note": source.rights_note,
                "rights_evidence_url": source.rights_evidence_url,
            }
            for report, source in zip(reports, sources)
        ],
    }
    _atomic_write_json(work_dir / "00_rights_manifest.json", manifest)


def _rights_basis(source: PublicDomainSource) -> str:
    parts = [str(source.rights_note or "").strip()]
    if source.publication_year is not None:
        parts.append(f"出版年份 {source.publication_year}")
    if source.author_death_year is not None:
        parts.append(f"作者去世年份 {source.author_death_year}")
    if source.rights_evidence_url:
        parts.append(f"依据链接 {source.rights_evidence_url}")
    return "；".join(value for value in parts if value)


def _render_prompt(filename: str, **values: object) -> str:
    template = (PROMPT_DIR / filename).read_text(encoding="utf-8")
    for key, value in values.items():
        template = template.replace("{" + key + "}", str(value))
    return template.strip()


def _call_llm(
    work_dir: Path,
    *,
    action: str,
    prompt: str,
    temperature: float,
    max_output_tokens: int,
    model: str | None = None,
) -> str:
    selected_model = model or settings.DEEPSEEK_MODEL
    started = time.monotonic()
    log: dict[str, Any] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "policy_version": POLICY_VERSION,
        "action": action,
        "model": selected_model,
        "input_chars": len(prompt),
        "max_output_tokens": max_output_tokens,
        "status": "started",
    }
    try:
        output = deepseek_generate_text(
            prompt,
            model=selected_model,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            fail_on_max_tokens=True,
        ).strip()
        log.update(
            status="ok",
            output_chars=len(output),
            duration_ms=int((time.monotonic() - started) * 1000),
            usage_tokens=None,
            cost=None,
            currency=None,
        )
        _append_jsonl(work_dir / "llm_calls.jsonl", log)
        return output
    except Exception as exc:
        log.update(
            status="failed",
            duration_ms=int((time.monotonic() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}"[:2000],
        )
        _append_jsonl(work_dir / "llm_calls.jsonl", log)
        raise


def _call_gemini(
    work_dir: Path,
    *,
    action: str,
    prompt: str,
    temperature: float,
    max_output_tokens: int,
) -> str:
    started = time.monotonic()
    log: dict[str, Any] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "policy_version": POLICY_VERSION,
        "action": action,
        "provider": "gemini",
        "model": settings.GEMINI_MODEL,
        "input_chars": len(prompt),
        "max_output_tokens": max_output_tokens,
        "status": "started",
    }
    try:
        output = gemini_generate_text(
            prompt,
            model=settings.GEMINI_MODEL,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            retries=2,
        ).strip()
        log.update(
            status="ok",
            output_chars=len(output),
            duration_ms=int((time.monotonic() - started) * 1000),
            usage_tokens=None,
            cost=None,
            currency=None,
        )
        _append_jsonl(work_dir / "llm_calls.jsonl", log)
        return output
    except Exception as exc:
        log.update(
            status="failed",
            duration_ms=int((time.monotonic() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}"[:2000],
        )
        _append_jsonl(work_dir / "llm_calls.jsonl", log)
        raise


def _call_openai_auditor(
    work_dir: Path,
    *,
    action: str,
    prompt: str,
    max_output_tokens: int,
) -> str:
    """Use the existing official OpenAI writer client as an independent audit fallback."""
    from app.services.content_engine import _openai_writer_call

    started = time.monotonic()
    model = str(settings.OPENAI_WRITER_MODEL or "openai-writer")
    log: dict[str, Any] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "policy_version": POLICY_VERSION,
        "action": action,
        "provider": "openai-audit-fallback",
        "model": model,
        "input_chars": len(prompt),
        "max_output_tokens": max_output_tokens,
        "status": "started",
    }
    try:
        output = _openai_writer_call(
            prompt,
            reasoning_effort="low",
            verbosity="low",
            max_output_tokens=max_output_tokens,
            service_tier="default",
            retry_on_failure=True,
        ).strip()
        log.update(
            status="ok",
            output_chars=len(output),
            duration_ms=int((time.monotonic() - started) * 1000),
            usage_tokens=None,
            cost=None,
            currency=None,
        )
        _append_jsonl(work_dir / "llm_calls.jsonl", log)
        return output
    except Exception as exc:
        log.update(
            status="failed",
            duration_ms=int((time.monotonic() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}"[:2000],
        )
        _append_jsonl(work_dir / "llm_calls.jsonl", log)
        raise


def _call_openai_preview_writer(
    work_dir: Path,
    *,
    action: str,
    prompt: str,
    max_output_tokens: int,
) -> str:
    """Use the configured official OpenAI writer for evidence-bound preview prose."""
    from app.services.content_engine import _openai_writer_call

    started = time.monotonic()
    model = str(settings.OPENAI_WRITER_MODEL or "openai-writer")
    log: dict[str, Any] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "policy_version": POLICY_VERSION,
        "action": action,
        "provider": "openai-preview-writer",
        "model": model,
        "input_chars": len(prompt),
        "max_output_tokens": max_output_tokens,
        "status": "started",
    }
    try:
        output = _openai_writer_call(
            prompt,
            reasoning_effort="medium",
            verbosity="high",
            max_output_tokens=max_output_tokens,
            service_tier="default",
            retry_on_failure=True,
        ).strip()
        log.update(
            status="ok",
            output_chars=len(output),
            duration_ms=int((time.monotonic() - started) * 1000),
            usage_tokens=None,
            cost=None,
            currency=None,
        )
        _append_jsonl(work_dir / "llm_calls.jsonl", log)
        return output
    except Exception as exc:
        log.update(
            status="failed",
            duration_ms=int((time.monotonic() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}"[:2000],
        )
        _append_jsonl(work_dir / "llm_calls.jsonl", log)
        raise


def _call_auditor(
    work_dir: Path,
    *,
    action: str,
    prompt: str,
    temperature: float,
    max_output_tokens: int,
    generation_mode: str = FULL_MODE,
) -> str:
    try:
        return _call_gemini(
            work_dir,
            action=action,
            prompt=prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
    except Exception:
        if generation_mode != RESEARCH_PREVIEW_MODE:
            raise
        return _call_openai_auditor(
            work_dir,
            action=action,
            prompt=prompt,
            max_output_tokens=max_output_tokens,
        )


def _parse_json_object(text: str) -> dict[str, Any]:
    value = (text or "").strip()
    value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*```$", "", value)
    start = value.find("{")
    end = value.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型输出不含完整 JSON 对象")
    payload = json.loads(value[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("模型 JSON 顶层必须为对象")
    return payload


def _load_versioned_artifact(path: Path, expected_key: str) -> dict[str, Any] | None:
    if not path.exists() or path.stat().st_size <= 0:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("policy_version") != POLICY_VERSION or payload.get("artifact_key") != expected_key:
        return None
    return payload


def _atomic_write_json(path: Path, payload: object) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _notify(callback: ProgressCallback | None, progress: int, step: str) -> None:
    if callback is not None:
        callback(max(0, min(100, int(progress))), step[:255])


def _chapter_tokens(target_chars: int) -> int:
    return max(6000, min(14_000, int(target_chars * 1.8)))


def _chapter_to_dict(item: ChapterResult) -> dict[str, Any]:
    return {
        "number": item.number,
        "title": item.title,
        "text": item.text,
        "summary": item.summary,
        "used_claim_ids": list(item.used_claim_ids),
        "audit": item.audit,
    }


def _chapter_from_dict(item: dict[str, Any]) -> ChapterResult:
    return ChapterResult(
        number=int(item.get("number") or 0),
        title=str(item.get("title") or ""),
        text=str(item.get("text") or ""),
        summary=str(item.get("summary") or ""),
        used_claim_ids=tuple(str(value) for value in item.get("used_claim_ids") or []),
        audit=dict(item.get("audit") or {}),
    )


def _audit_passed(audit: dict[str, Any]) -> bool:
    if not bool(audit.get("pass")):
        return False
    return not any(
        audit.get(key)
        for key in ("unsupported_claims", "contradictions", "missing_materials", "language_issues")
    )


def _fallback_summary(text: str) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    return compact[:220]


def _char_grams(text: str, size: int) -> set[str]:
    compact = _compact(text)
    return {compact[index : index + size] for index in range(max(0, len(compact) - size + 1))}


def _compact(text: str) -> str:
    return re.sub(r"[^\u3400-\u9fffA-Za-z0-9]+", "", text or "").lower()


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "BookDimensions",
    "ChapterResult",
    "FULL_MODE",
    "MAX_SOURCES",
    "MIN_SOURCES",
    "POLICY_VERSION",
    "PREVIEW_MAX_SOURCES",
    "PREVIEW_MIN_SOURCES",
    "RESEARCH_PREVIEW_MODE",
    "PublicDomainLongformResult",
    "PublicDomainQualityError",
    "PublicDomainSource",
    "PublicDomainValidationError",
    "audit_edited_final_manuscript",
    "build_source_reports",
    "check_final_manuscript",
    "count_text_chars",
    "derive_dimensions",
    "parse_final_markdown",
    "render_final_markdown",
    "run_public_domain_longform",
    "source_from_dict",
    "source_to_dict",
    "validate_sources",
]
