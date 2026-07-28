"""Low-call, resumable public-domain long-form story writer.

The formal bookshelf flow deliberately performs one book-plan call and one
writer call per chapter.  A chapter may receive at most one bounded
continuation when it is materially short.  All other checks are local and do
not launch paid repair or auditor loops.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable

from app.services import public_domain_longform as legacy
from app.services import public_domain_rewrite as simple

SERIAL_POLICY_VERSION = "public-domain-serial-story-v1"
_MIN_CHAPTER_RATIO = 0.90
_MAX_CHAPTER_RATIO = 1.10
_SOURCE_EXCERPT_CHARS = 2_200
_PROHIBITED_DASH_RE = re.compile(r"(?:——|—|–)")


def run_serial_public_domain_longform(
    *,
    sources: Iterable[legacy.PublicDomainSource],
    reports: tuple[legacy.SourceReport, ...],
    dimensions: legacy.BookDimensions,
    work_dir: Path,
    target_title: str | None,
    target_direction: str | None,
    output_style: str | None,
    on_progress: legacy.ProgressCallback | None = None,
    on_chapter_completed: legacy.ChapterCompletedCallback | None = None,
) -> legacy.PublicDomainLongformResult:
    items = list(sources)
    short_sources = [_to_simple_source(item) for item in items]
    source_mode = simple._infer_source_mode(  # noqa: SLF001
        short_sources,
        target_direction=target_direction,
        output_style=output_style,
    )
    mode_guidance = simple._load_mode_guidance(source_mode.key)  # noqa: SLF001
    source_briefs = "\n\n".join(
        simple._source_brief(item, index, source_mode.key)  # noqa: SLF001
        for index, item in enumerate(short_sources, start=1)
    )
    plan = _build_or_resume_plan(
        work_dir=work_dir,
        reports=reports,
        dimensions=dimensions,
        source_briefs=source_briefs,
        source_mode=source_mode,
        mode_guidance=mode_guidance,
        target_title=target_title,
        target_direction=target_direction,
        output_style=output_style,
    )
    legacy._notify(  # noqa: SLF001
        on_progress,
        12,
        f"整书结构已固定：{dimensions.target_chapters} 章，目标 {dimensions.target_chars} 字",
    )

    chapters: list[legacy.ChapterResult] = []
    previous_summaries: list[str] = []
    for number, contract in enumerate(plan["chapters"], start=1):
        chapter = _build_or_resume_chapter(
            work_dir=work_dir,
            number=number,
            contract=contract,
            plan=plan,
            sources=short_sources,
            previous_summaries=previous_summaries,
            source_mode=source_mode,
            mode_guidance=mode_guidance,
        )
        chapters.append(chapter)
        previous_summaries.append(chapter.summary)
        _write_progress(work_dir, dimensions, chapters)
        if on_chapter_completed is not None:
            on_chapter_completed(chapter)
        progress = 12 + int(78 * number / max(1, dimensions.target_chapters))
        legacy._notify(  # noqa: SLF001
            on_progress,
            progress,
            f"逐章串行创作 {number}/{dimensions.target_chapters}，已落盘 {sum(legacy.count_text_chars(item.text) for item in chapters)} 字",
        )

    markdown = legacy.render_final_markdown(
        title=str(plan.get("title") or target_title or "公版融合长篇"),
        summary=str(plan.get("summary") or ""),
        chapters=chapters,
    )
    quality = _final_quality_report(
        manuscript=markdown,
        chapters=chapters,
        dimensions=dimensions,
        sources=short_sources,
    )
    legacy._atomic_write_text(work_dir / "04_format.md", markdown)  # noqa: SLF001
    legacy._atomic_write_json(work_dir / "quality_report.json", quality)  # noqa: SLF001
    if quality["status"] != "ok":
        raise legacy.PublicDomainQualityError("；".join(quality["blocking_issues"]))
    legacy._notify(on_progress, 93, "整书字数、章节、重复、现代材料与破折号终检通过")  # noqa: SLF001
    return legacy.PublicDomainLongformResult(
        title=str(plan.get("title") or target_title or "公版融合长篇").strip(),
        summary=str(plan.get("summary") or "").strip(),
        markdown=markdown,
        chapters=tuple(chapters),
        source_reports=reports,
        dimensions=dimensions,
        quality_report=quality,
        plan=plan,
    )


def _build_or_resume_plan(
    *,
    work_dir: Path,
    reports: tuple[legacy.SourceReport, ...],
    dimensions: legacy.BookDimensions,
    source_briefs: str,
    source_mode: simple.SourceModeProfile,
    mode_guidance: str,
    target_title: str | None,
    target_direction: str | None,
    output_style: str | None,
) -> dict[str, Any]:
    path = work_dir / "01_serial_plan.json"
    artifact_key = _hash_json(
        {
            "policy": SERIAL_POLICY_VERSION,
            "sources": [item.sha256 for item in reports],
            "target_chars": dimensions.target_chars,
            "target_chapters": dimensions.target_chapters,
            "target_title": target_title or "",
            "target_direction": target_direction or "",
            "output_style": output_style or "",
            "source_mode": source_mode.key,
        }
    )
    cached = _load_artifact(path, artifact_key)
    if cached is not None:
        return dict(cached["plan"])
    prompt = _render_prompt(
        "longform_plan.md",
        source_count=len(reports),
        source_briefs=source_briefs,
        target_title=target_title or "根据全书内容自然拟题",
        target_direction=target_direction or "形成一部以故事为主、说理为辅的独立新书",
        target_chars=dimensions.target_chars,
        target_chapters=dimensions.target_chapters,
        output_style=output_style or "通俗、自然、耐读，故事为主，说理为辅",
        source_mode=source_mode.label,
        mode_guidance=mode_guidance,
    )
    raw = legacy._call_llm(  # noqa: SLF001
        work_dir,
        action="serial_book_plan",
        prompt=prompt,
        temperature=0.32,
        max_output_tokens=12_000,
    )
    payload = legacy._parse_json_object(raw)  # noqa: SLF001
    raw_chapters = [item for item in payload.get("chapters") or [] if isinstance(item, dict)]
    if len(raw_chapters) != dimensions.target_chapters:
        raise legacy.PublicDomainQualityError(
            f"整书规划返回 {len(raw_chapters)} 章，要求精确为 {dimensions.target_chapters} 章；规划已保存，不自动付费重试。"
        )
    base = dimensions.target_chars // dimensions.target_chapters
    remainder = dimensions.target_chars % dimensions.target_chapters
    chapters: list[dict[str, Any]] = []
    source_count = len(reports)
    for index, raw_chapter in enumerate(raw_chapters, start=1):
        selected = []
        for value in raw_chapter.get("source_indexes") or []:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            if 1 <= parsed <= source_count and parsed not in selected:
                selected.append(parsed)
        for offset in range(source_count):
            candidate = ((index - 1 + offset) % source_count) + 1
            if candidate not in selected:
                selected.append(candidate)
            if len(selected) >= min(3, source_count):
                break
        chapters.append(
            {
                "number": index,
                "title": _clean_plan_text(raw_chapter.get("title") or f"第{index}章")[:80],
                "purpose": _clean_plan_text(raw_chapter.get("purpose") or "推进全书叙事")[:400],
                "focus": _clean_plan_text(raw_chapter.get("focus") or "")[:500],
                "material_queries": [
                    _clean_plan_text(value)[:100]
                    for value in (raw_chapter.get("material_queries") or [])[:8]
                    if str(value).strip()
                ],
                "avoid_repeat": _clean_plan_text(raw_chapter.get("avoid_repeat") or "")[:300],
                "source_indexes": selected[: min(4, source_count)],
                "target_chars": base + (1 if index <= remainder else 0),
            }
        )
    plan = {
        "title": _clean_plan_text(target_title or payload.get("title") or "公版融合长篇")[:120],
        "summary": _clean_plan_text(payload.get("summary") or "")[:1200],
        "central_thread": _clean_plan_text(payload.get("central_thread") or "")[:800],
        "source_mode": source_mode.key,
        "chapters": chapters,
    }
    legacy._atomic_write_json(  # noqa: SLF001
        path,
        {
            "artifact_key": artifact_key,
            "policy_version": SERIAL_POLICY_VERSION,
            "plan": plan,
        },
    )
    return plan


def _build_or_resume_chapter(
    *,
    work_dir: Path,
    number: int,
    contract: dict[str, Any],
    plan: dict[str, Any],
    sources: list[simple.PublicDomainSource],
    previous_summaries: list[str],
    source_mode: simple.SourceModeProfile,
    mode_guidance: str,
) -> legacy.ChapterResult:
    chapter_dir = work_dir / "chapters"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    path = chapter_dir / f"{number:03d}.json"
    packet = _chapter_source_packet(sources, contract, number, len(plan["chapters"]))
    artifact_key = _hash_json(
        {
            "policy": SERIAL_POLICY_VERSION,
            "contract": contract,
            "previous": previous_summaries,
            "packet": packet,
        }
    )
    cached = _load_artifact(path, artifact_key)
    if cached is not None:
        return legacy._chapter_from_dict(dict(cached["chapter"]))  # noqa: SLF001

    target_chars = int(contract["target_chars"])
    prompt = _render_prompt(
        "longform_chapter.md",
        book_title=plan.get("title") or "",
        book_summary=plan.get("summary") or "",
        central_thread=plan.get("central_thread") or "",
        chapter_number=number,
        total_chapters=len(plan["chapters"]),
        chapter_title=contract.get("title") or "",
        chapter_contract=json.dumps(contract, ensure_ascii=False),
        target_chars=target_chars,
        minimum_chars=int(target_chars * _MIN_CHAPTER_RATIO),
        maximum_chars=int(target_chars * _MAX_CHAPTER_RATIO),
        previous_summaries="\n".join(
            f"第{index + 1}章：{value}" for index, value in enumerate(previous_summaries)
        ) or "无，这是第一章。",
        source_packet=packet,
        source_mode=source_mode.label,
        mode_guidance=mode_guidance,
    )
    raw = legacy._call_llm(  # noqa: SLF001
        work_dir,
        action=f"serial_chapter_write:{number:03d}",
        prompt=prompt,
        temperature=0.72,
        max_output_tokens=legacy._chapter_tokens(target_chars),  # noqa: SLF001
    )
    text = _clean_chapter_text(raw, str(contract.get("title") or ""))
    minimum = int(target_chars * _MIN_CHAPTER_RATIO)
    maximum = int(target_chars * _MAX_CHAPTER_RATIO)
    if legacy.count_text_chars(text) < minimum:
        remaining = minimum - legacy.count_text_chars(text) + int(target_chars * 0.04)
        continuation_prompt = _render_prompt(
            "longform_continue.md",
            book_title=plan.get("title") or "",
            chapter_title=contract.get("title") or "",
            chapter_contract=json.dumps(contract, ensure_ascii=False),
            current_ending=text[-1600:],
            source_packet=packet,
            remaining_chars=max(500, remaining),
        )
        extension = legacy._call_llm(  # noqa: SLF001
            work_dir,
            action=f"serial_chapter_continue:{number:03d}",
            prompt=continuation_prompt,
            temperature=0.68,
            max_output_tokens=max(1800, min(6000, int(remaining * 2.0))),
        )
        text = _merge_continuation(text, _clean_chapter_text(extension, ""))
    if legacy.count_text_chars(text) > maximum:
        text = _truncate_at_sentence(text, maximum)

    issues, local_report = _chapter_local_issues(
        text=text,
        target_chars=target_chars,
        sources=sources,
        previous_chapters=chapter_dir,
        number=number,
    )
    audit = {
        "pass": not issues,
        "policy_version": SERIAL_POLICY_VERSION,
        "deterministic_issues": issues,
        "local_report": local_report,
        "paid_auditor_calls": 0,
    }
    summary = legacy._fallback_summary(text)  # noqa: SLF001
    chapter = legacy.ChapterResult(
        number=number,
        title=str(contract.get("title") or f"第{number}章")[:120],
        text=text,
        summary=summary,
        used_claim_ids=(),
        audit=audit,
    )
    if issues:
        legacy._atomic_write_json(  # noqa: SLF001
            chapter_dir / f"{number:03d}.failed.json",
            {
                "artifact_key": artifact_key,
                "policy_version": SERIAL_POLICY_VERSION,
                "chapter": legacy._chapter_to_dict(chapter),  # noqa: SLF001
            },
        )
        raise legacy.PublicDomainQualityError(
            f"第 {number} 章本地终检未通过：{'；'.join(issues)}。候选已保留，不自动付费重写。"
        )
    legacy._atomic_write_json(  # noqa: SLF001
        path,
        {
            "artifact_key": artifact_key,
            "policy_version": SERIAL_POLICY_VERSION,
            "chapter": legacy._chapter_to_dict(chapter),  # noqa: SLF001
        },
    )
    legacy._atomic_write_text(chapter_dir / f"{number:03d}.md", text)  # noqa: SLF001
    return chapter


def _chapter_source_packet(
    sources: list[simple.PublicDomainSource],
    contract: dict[str, Any],
    chapter_number: int,
    total_chapters: int,
) -> str:
    selected = [int(value) for value in contract.get("source_indexes") or []]
    parts: list[str] = []
    for offset, source_index in enumerate(selected):
        if not 1 <= source_index <= len(sources):
            continue
        source = sources[source_index - 1]
        filtered = simple._filter_modern_narrative_lines(source.text)  # noqa: SLF001
        compact = simple._normalize_source_text(filtered)  # noqa: SLF001
        window = min(_SOURCE_EXCERPT_CHARS, len(compact))
        if len(compact) <= window:
            excerpt = compact
        else:
            slots = max(2, total_chapters)
            slot = (chapter_number * 7 + source_index * 3 + offset) % slots
            center = int((slot + 0.5) * len(compact) / slots)
            start = max(0, min(len(compact) - window, center - window // 2))
            excerpt = compact[start : start + window]
            excerpt = _trim_fragment(excerpt)
        parts.append(
            f"材料 {source_index}《{source.title}》的轮换节选。只取传统事实线索，不得复制措辞或现代评论：\n{excerpt}"
        )
    return "\n\n".join(parts)


def _chapter_local_issues(
    *,
    text: str,
    target_chars: int,
    sources: list[simple.PublicDomainSource],
    previous_chapters: Path,
    number: int,
) -> tuple[list[str], dict[str, Any]]:
    issues: list[str] = []
    chars = legacy.count_text_chars(text)
    lower = int(target_chars * _MIN_CHAPTER_RATIO)
    upper = int(target_chars * _MAX_CHAPTER_RATIO)
    if not lower <= chars <= upper:
        issues.append(f"有效字数 {chars}，要求 {lower} 至 {upper}")
    if _PROHIBITED_DASH_RE.search(text):
        issues.append("仍含破折号")
    paragraphs = [value.strip() for value in re.split(r"\n+", text) if value.strip()]
    if chars >= 3000 and len(paragraphs) < 10:
        issues.append(f"自然段只有 {len(paragraphs)} 个，故事被压缩成大段摘要")
    quality = simple.check_public_domain_manuscript(text, sources)
    if quality.get("status") != "ok":
        issues.extend(str(value) for value in quality.get("warnings") or [])
    repeated = _cross_chapter_repetition(text, previous_chapters, number)
    if repeated:
        issues.append(f"与前文章节存在 {len(repeated)} 处 80 字连续重复")
    report = {
        "chars": chars,
        "paragraphs": len(paragraphs),
        "dash_count": len(_PROHIBITED_DASH_RE.findall(text)),
        "modern_markers": list(quality.get("modern_narrative_issues") or []),
        "residue_markers": list(quality.get("residue_issues") or []),
        "overlap": [asdict(item) for item in quality.get("overlap_issues") or []],
        "cross_chapter_repeat_samples": repeated,
    }
    return list(dict.fromkeys(issues)), report


def _final_quality_report(
    *,
    manuscript: str,
    chapters: list[legacy.ChapterResult],
    dimensions: legacy.BookDimensions,
    sources: list[simple.PublicDomainSource],
) -> dict[str, Any]:
    body = "\n\n".join(item.text for item in chapters)
    chars = legacy.count_text_chars(body)
    lower = int(dimensions.target_chars * _MIN_CHAPTER_RATIO)
    upper = int(dimensions.target_chars * _MAX_CHAPTER_RATIO)
    blocking: list[str] = []
    if len(chapters) != dimensions.target_chapters:
        blocking.append(f"章节数 {len(chapters)}，目标 {dimensions.target_chapters}")
    if not lower <= chars <= upper:
        blocking.append(f"全书有效字数 {chars}，目标范围 {lower} 至 {upper}")
    dash_count = len(_PROHIBITED_DASH_RE.findall(manuscript))
    if dash_count:
        blocking.append(f"全书仍有 {dash_count} 个破折号")
    quality = simple.check_public_domain_manuscript(body, sources)
    if quality.get("status") != "ok":
        blocking.extend(str(value) for value in quality.get("warnings") or [])
    failed = [item.number for item in chapters if not bool(item.audit.get("pass"))]
    if failed:
        blocking.append(f"章节本地终检失败：{failed}")
    return {
        "status": "ok" if not blocking else "needs_review",
        "policy_version": SERIAL_POLICY_VERSION,
        "target_chars": dimensions.target_chars,
        "actual_chars": chars,
        "target_chapters": dimensions.target_chapters,
        "actual_chapters": len(chapters),
        "dash_count": dash_count,
        "paid_plan_calls": 1,
        "paid_auditor_calls": 0,
        "maximum_paid_writer_calls": 1 + dimensions.target_chapters * 2,
        "blocking_issues": list(dict.fromkeys(blocking)),
        "source_overlap": [asdict(item) for item in quality.get("overlap_issues") or []],
        "modern_markers": list(quality.get("modern_narrative_issues") or []),
        "residue_markers": list(quality.get("residue_issues") or []),
        "chapter_reports": [item.audit.get("local_report") for item in chapters],
    }


def _clean_chapter_text(text: str, title: str) -> str:
    value = re.sub(r"^```(?:markdown|md|text)?\s*", "", (text or "").strip(), flags=re.I)
    value = re.sub(r"\s*```$", "", value)
    lines = value.splitlines()
    if lines and (
        lines[0].strip().lstrip("# ") == title.strip()
        or re.match(r"^第[一二三四五六七八九十百零\d]+章", lines[0].strip().lstrip("# "))
    ):
        lines = lines[1:]
    value = "\n".join(lines).strip()
    value = simple._remove_prohibited_dashes(value)  # noqa: SLF001
    value = _remove_modern_sentences(value)
    return simple._normalize_source_text(value)  # noqa: SLF001


def _clean_plan_text(value: object) -> str:
    return simple._remove_prohibited_dashes(str(value or "").strip())  # noqa: SLF001


def _merge_continuation(original: str, extension: str) -> str:
    if not extension:
        return original
    tail = simple._compact_for_overlap(original[-1000:])  # noqa: SLF001
    lines = [value for value in extension.splitlines() if value.strip()]
    while lines:
        compact = simple._compact_for_overlap(lines[0])  # noqa: SLF001
        if compact and compact in tail:
            lines.pop(0)
        else:
            break
    merged = f"{original.rstrip()}\n\n{'\n'.join(lines).strip()}".strip()
    return simple._remove_prohibited_dashes(merged)  # noqa: SLF001


def _truncate_at_sentence(text: str, maximum_chars: int) -> str:
    if legacy.count_text_chars(text) <= maximum_chars:
        return text
    consumed = 0
    pieces: list[str] = []
    for match in re.finditer(r"[\s\S]*?[。！？!?](?:\n+|$)|[\s\S]+$", text):
        piece = match.group(0)
        piece_chars = legacy.count_text_chars(piece)
        if pieces and consumed + piece_chars > maximum_chars:
            break
        pieces.append(piece)
        consumed += piece_chars
        if consumed >= int(maximum_chars * 0.97):
            break
    return "".join(pieces).strip() or text


def _cross_chapter_repetition(text: str, chapter_dir: Path, number: int) -> list[str]:
    compact = simple._compact_for_overlap(text)  # noqa: SLF001
    grams = {
        compact[index : index + 80]
        for index in range(0, max(0, len(compact) - 79), 40)
    }
    samples: list[str] = []
    for path in sorted(chapter_dir.glob("*.md")):
        try:
            previous_number = int(path.stem)
        except ValueError:
            continue
        if previous_number >= number:
            continue
        previous = simple._compact_for_overlap(path.read_text(encoding="utf-8"))  # noqa: SLF001
        for gram in grams:
            if gram and gram in previous:
                samples.append(gram[:80])
                if len(samples) >= 3:
                    return samples
    return samples


def _trim_fragment(text: str) -> str:
    value = text.strip()
    first = re.search(r"[。！？!?\n]", value[:180])
    if first:
        value = value[first.end() :].lstrip()
    last = max(value.rfind("。"), value.rfind("！"), value.rfind("？"))
    return value[: last + 1].strip() if last > 200 else value


def _remove_modern_sentences(text: str) -> str:
    """Drop narrow, known-modern anecdotes without a model repair call."""

    parts = re.split(r"(?<=[。！？!?])", text or "")
    kept = [
        part
        for part in parts
        if part.strip()
        and not any(
            marker in part
            for marker in simple._MODERN_NARRATIVE_MARKERS  # noqa: SLF001
        )
    ]
    return "".join(kept).strip()


def _write_progress(
    work_dir: Path,
    dimensions: legacy.BookDimensions,
    chapters: list[legacy.ChapterResult],
) -> None:
    legacy._atomic_write_json(  # noqa: SLF001
        work_dir / "02_serial_progress.json",
        {
            "policy_version": SERIAL_POLICY_VERSION,
            "target_chars": dimensions.target_chars,
            "target_chapters": dimensions.target_chapters,
            "completed_chapters": len(chapters),
            "completed_chars": sum(legacy.count_text_chars(item.text) for item in chapters),
            "chapter_hashes": {
                str(item.number): sha256(item.text.encode("utf-8")).hexdigest()
                for item in chapters
            },
        },
    )


def _to_simple_source(item: legacy.PublicDomainSource) -> simple.PublicDomainSource:
    return simple.PublicDomainSource(
        title=item.title,
        text=item.text,
        author=item.author,
        publication_year=item.publication_year,
        rights_note=item.rights_note,
    )


def _render_prompt(filename: str, **values: object) -> str:
    path = Path(__file__).resolve().parents[1] / "prompts" / "public_domain_rewrite" / filename
    template = path.read_text(encoding="utf-8")
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", str(value))
    return template.strip()


def _hash_json(payload: object) -> str:
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _load_artifact(path: Path, artifact_key: str) -> dict[str, Any] | None:
    if not path.exists() or path.stat().st_size <= 0:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("policy_version") != SERIAL_POLICY_VERSION:
        return None
    if payload.get("artifact_key") != artifact_key:
        return None
    return payload


__all__ = ["SERIAL_POLICY_VERSION", "run_serial_public_domain_longform"]
