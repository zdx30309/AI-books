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
import json
import math
from pathlib import Path
import re
import statistics
import time
from typing import Any, Callable, Iterable

from app.core.config import settings
from app.services.deepseek import generate_text as deepseek_generate_text
from app.services.gemini import generate_text as gemini_generate_text


POLICY_VERSION = "public-domain-longform-v2"
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
                "研究试稿必须确认：来源只用于抽取事实/概念线索和检查重合，不复制原文、独创故事或现代评论。"
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
                    or "研究试稿：仅作事实/概念线索与来源重合排查，未主张该现代整理本属于公版"
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
    if generation_mode == RESEARCH_PREVIEW_MODE:
        _notify(on_progress, 5, f"已隔离 {len(clean_sources)} 本研究材料；只抽取事实/概念线索")
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

    plan = _build_book_plan(
        work_dir=work_dir,
        source_reports=reports,
        source_digests=digests,
        dimensions=dimensions,
        target_title=target_title,
        target_direction=target_direction,
        output_style=output_style,
        generation_mode=generation_mode,
    )
    _notify(on_progress, 38, f"长篇规划完成：{dimensions.target_chapters} 章 / 目标 {dimensions.target_chars} 字")

    overlap_indexes = _build_overlap_indexes(clean_sources, reports)
    chapters: list[ChapterResult] = []
    previous_summary = "这是第一章，没有上一章。"
    contracts = list(plan["chapters"])
    for chapter_index, contract in enumerate(contracts, start=1):
        allowed_evidence = _select_chapter_evidence(contract, all_evidence, reports)
        chapter = _build_or_resume_chapter(
            work_dir=work_dir,
            chapter_number=chapter_index,
            contract=contract,
            book_summary=str(plan.get("summary") or ""),
            previous_summary=previous_summary,
            allowed_evidence=allowed_evidence,
            overlap_indexes=overlap_indexes,
            generation_mode=generation_mode,
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
        source_reports=reports,
        dimensions=dimensions,
        overlap_indexes=overlap_indexes,
        generation_mode=generation_mode,
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
        source_reports=reports,
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
    candidate_hash = text_sha256(manuscript)
    original_report_path = work_dir / "quality_report.json"
    if original_report_path.exists():
        try:
            original_report = json.loads(original_report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            original_report = {}
        if bool(original_report.get("passed")) and original_report.get("final_sha256") == candidate_hash:
            return {
                "status": "ok",
                "passed": True,
                "candidate_sha256": candidate_hash,
                "reused_original_audit": True,
                "blocking_issues": [],
            }

    cache_path = work_dir / "post_edit_quality_report.json"
    cache_key = text_sha256(f"{POLICY_VERSION}\npost-edit\n{candidate_hash}")
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
    source_reports = build_source_reports(clean_sources)
    overlap_indexes = _build_overlap_indexes(clean_sources, source_reports)
    deterministic = check_final_manuscript(manuscript, clean_sources)
    blocking.extend(
        f"与《{item.get('source_title') or item.get('source_id')}》来源重合超限"
        for item in deterministic.get("blocked_overlap") or []
    )
    if deterministic.get("residue_issues"):
        blocking.append("终稿含模型说明或任务残留")

    all_evidence: list[dict[str, Any]] = []
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
    if len(chapters) == len(contracts) and not any("证据账本不可用" in item for item in blocking):
        for chapter, contract in zip(chapters, contracts):
            try:
                allowed_evidence = _select_chapter_evidence(contract, all_evidence, source_reports)
                evidence_payload = [
                    {
                        "claim_id": item.get("claim_id"),
                        "source_id": item.get("source_id"),
                        "source_title": item.get("source_title"),
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
                )
                chapter_issues = _post_edit_chapter_issues(
                    text=chapter.text,
                    target_chars=int(contract.get("target_chars") or 0),
                    overlap_indexes=overlap_indexes,
                )
                if chapter_issues:
                    audit["pass"] = False
                    audit.setdefault("repair_instructions", []).extend(chapter_issues)
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
            },
        }
        try:
            raw = _call_gemini(
                work_dir,
                action="post_edit_final_audit",
                prompt=_render_prompt(
                    "final_audit.md",
                    audit_material=json.dumps(audit_material, ensure_ascii=False),
                ),
                temperature=0.0,
                max_output_tokens=5000,
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
                "从全部研究材料共同出现的概念与事实线索中形成独立问题与论述；排除独创故事和现代作者评论"
                if generation_mode == RESEARCH_PREVIEW_MODE
                else "从 8–10 本来源的交叉材料中形成独立问题与论述"
            )
        ),
        output_style=output_style or "清晰、克制、准确、可读；不模仿任何单一来源",
        source_digests=json.dumps(source_digests, ensure_ascii=False),
    )
    raw = _call_llm(
        work_dir,
        action="book_plan",
        prompt=prompt,
        temperature=0.15,
        max_output_tokens=3000 if generation_mode == RESEARCH_PREVIEW_MODE else 9000,
        model=PREVIEW_DEEPSEEK_MODEL if generation_mode == RESEARCH_PREVIEW_MODE else None,
    )
    plan = _normalize_plan(_parse_json_object(raw), source_reports, dimensions, target_title)
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
) -> dict[str, Any]:
    raw_chapters = [item for item in plan.get("chapters") or [] if isinstance(item, dict)]
    if len(raw_chapters) != dimensions.target_chapters:
        raise PublicDomainQualityError(
            f"长篇规划返回 {len(raw_chapters)} 章，必须精确为 {dimensions.target_chapters} 章。"
        )
    source_ids = [item.source_id for item in source_reports]
    base = dimensions.target_chars // dimensions.target_chapters
    remainder = dimensions.target_chars - base * dimensions.target_chapters
    chapters: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_chapters, start=1):
        selected = [value for value in raw.get("source_ids") or [] if value in source_ids]
        for offset in range(2):
            candidate = source_ids[(index - 1 + offset) % len(source_ids)]
            if candidate not in selected:
                selected.append(candidate)
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
            }
        )
    # Ensure every source participates in at least two chapter contracts.
    for source_offset, source_id in enumerate(source_ids):
        used = [item for item in chapters if source_id in item["source_ids"]]
        while len(used) < 2:
            target = chapters[(source_offset + len(used) * len(source_ids)) % len(chapters)]
            if source_id not in target["source_ids"]:
                target["source_ids"].append(source_id)
            used = [item for item in chapters if source_id in item["source_ids"]]
    return {
        "title": str(target_title or plan.get("title") or "公版融合长篇").strip()[:120],
        "subtitle": str(plan.get("subtitle") or "").strip()[:160],
        "summary": str(plan.get("summary") or "").strip()[:1200],
        "central_question": str(plan.get("central_question") or "").strip()[:500],
        "chapters": chapters,
    }


def _select_chapter_evidence(
    contract: dict[str, Any],
    evidence: list[dict[str, Any]],
    reports: tuple[SourceReport, ...],
) -> list[dict[str, Any]]:
    selected_sources = set(contract.get("source_ids") or [])
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
    result: list[dict[str, Any]] = []
    per_source: dict[str, int] = {}
    for _, item in scored:
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
                    failed_payload.get("contract") == contract
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
            target_chars=target_chars,
            minimum_chars=minimum_chars,
            maximum_chars=maximum_chars,
        )
        raw = _call_llm(
            work_dir,
            action=f"chapter_write:{chapter_number:03d}",
            prompt=prompt,
            temperature=0.35,
            max_output_tokens=_chapter_tokens(target_chars),
            model=PREVIEW_DEEPSEEK_MODEL if generation_mode == RESEARCH_PREVIEW_MODE else None,
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
            target_chars=int(contract["target_chars"]),
            used_claim_ids=used_claim_ids,
            allowed_evidence=allowed_evidence,
            overlap_indexes=overlap_indexes,
        )
        audit = _audit_chapter(
            work_dir=work_dir,
            chapter_number=chapter_number,
            contract=contract,
            allowed_evidence=evidence_payload,
            chapter_text=text,
            generation_mode=generation_mode,
        )
        if deterministic:
            audit["pass"] = False
            audit.setdefault("repair_instructions", []).extend(deterministic)
        last_audit = audit
        if minimum_chars <= count_text_chars(text) <= maximum_chars:
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
        repair_source = best_full_candidate or candidate
        repair_source_text = str(repair_source.get("text") or "").strip()
        repair_audit = best_full_audit or audit
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
        )
        action = f"chapter_repair:{chapter_number:03d}:attempt{attempt}"
        if generation_mode == RESEARCH_PREVIEW_MODE and (resumed_failed_candidate or attempt >= 3):
            repaired = _call_openai_preview_writer(
                work_dir,
                action=action,
                prompt=repair_prompt,
                max_output_tokens=_chapter_tokens(target_chars),
            )
        else:
            repaired = _call_llm(
                work_dir,
                action=action,
                prompt=repair_prompt,
                temperature=0.15,
                max_output_tokens=_chapter_tokens(target_chars),
                model=PREVIEW_DEEPSEEK_MODEL if generation_mode == RESEARCH_PREVIEW_MODE else None,
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


def _audit_chapter(
    *,
    work_dir: Path,
    chapter_number: int,
    contract: dict[str, Any],
    allowed_evidence: list[dict[str, Any]],
    chapter_text: str,
    generation_mode: str = FULL_MODE,
) -> dict[str, Any]:
    prompt = _render_prompt(
        "chapter_audit.md",
        chapter_contract=json.dumps(contract, ensure_ascii=False),
        allowed_evidence=json.dumps(allowed_evidence, ensure_ascii=False),
        chapter_text=chapter_text,
    )
    raw = _call_auditor(
        work_dir,
        action=f"chapter_audit:{chapter_number:03d}",
        prompt=prompt,
        temperature=0.0,
        max_output_tokens=5000,
        generation_mode=generation_mode,
    )
    audit = _parse_json_object(raw)
    audit["pass"] = bool(audit.get("pass"))
    for key in ("unsupported_claims", "contradictions", "missing_materials", "language_issues", "repair_instructions"):
        audit[key] = [str(value) for value in audit.get(key) or []]
    return audit


def _chapter_deterministic_issues(
    *,
    text: str,
    target_chars: int,
    used_claim_ids: tuple[str, ...],
    allowed_evidence: list[dict[str, Any]],
    overlap_indexes: list[dict[str, Any]],
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
    if len([value for value in used_claim_ids if value in allowed_ids]) < 4:
        issues.append("至少要明确使用 4 条允许证据，不能靠空泛扩写凑篇幅")
    residues = [value for value in _AI_RESIDUE if value in text.lower()]
    if residues:
        issues.append(f"存在模型说明或任务残留：{', '.join(residues)}")
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
    for item in _overlap_reports(text, overlap_indexes):
        if _overlap_blocked(item):
            issues.append(
                f"与《{item.source_title}》连续表达重合偏高（{item.ratio:.2%}，长连续重合={item.long_exact_match}）"
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

    source_usage: dict[str, int] = {item.source_id: 0 for item in source_reports}
    source_chapter_usage: dict[str, set[int]] = {item.source_id: set() for item in source_reports}
    for chapter in chapters:
        for claim_id in chapter.used_claim_ids:
            source_id = claim_id.split("-", 1)[0]
            if source_id in source_usage:
                source_usage[source_id] += 1
                source_chapter_usage[source_id].add(chapter.number)
    min_claims = 2 if generation_mode == RESEARCH_PREVIEW_MODE else 4
    min_source_chapters = 1 if generation_mode == RESEARCH_PREVIEW_MODE else 2
    for report in source_reports:
        if (
            source_usage[report.source_id] < min_claims
            or len(source_chapter_usage[report.source_id]) < min_source_chapters
        ):
            blocking.append(
                f"{report.source_id}《{report.title}》未达到至少 {min_claims} 条证据、"
                f"分布在至少 {min_source_chapters} 章的实质贡献"
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
        "source_overlap": [asdict(item) for item in overlap],
        "internal_duplicate_ratio": duplicate_ratio,
        "rights_complete": all(bool(item.rights_basis) for item in source_reports),
        "blocking_issues": blocking,
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
    model_audit = _parse_json_object(raw)
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
            "研究试稿未主张现代整理本属于公版。原始文字只用于事实/概念线索抽取与重合排查；"
            "写作层不得接触原文、独创故事、来源句法或现代评论。该技术隔离不替代专业法律意见。"
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
    """Use the configured official OpenAI writer only after preview repairs stagnate."""
    from app.services.content_engine import _openai_writer_call

    started = time.monotonic()
    model = str(settings.OPENAI_WRITER_MODEL or "openai-writer")
    log: dict[str, Any] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "policy_version": POLICY_VERSION,
        "action": action,
        "provider": "openai-preview-writer-fallback",
        "model": model,
        "input_chars": len(prompt),
        "max_output_tokens": max_output_tokens,
        "status": "started",
    }
    try:
        output = _openai_writer_call(
            prompt,
            reasoning_effort="medium",
            verbosity="medium",
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
