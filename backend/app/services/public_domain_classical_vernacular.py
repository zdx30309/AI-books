"""Resumable, translation-only classical Chinese to modern Chinese pipeline.

This route is intentionally isolated from public-domain multi-source fusion.
It never sends an entire long book into one context window.  Source chapters
are processed strictly in order, divided into bounded segments, translated
once, and checkpointed after every successful segment.  Paragraph IDs and
deterministic anchors protect completeness without launching a second paid
rewrite/review pass.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable

from app.services import public_domain_longform as legacy


POLICY_VERSION = "public-domain-classical-translation-v4"
PROMPT_VERSION = "classical-complete-translation-only-v4"
PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts" / "public_domain_rewrite"
TRANSLATION_PROMPT = PROMPT_DIR / "classical_vernacular_translation.md"
DEFAULT_USER_PROMPT = PROMPT_DIR / "classical_vernacular_user_default.md"

MAX_SECTION_COUNT = 5_000
SEGMENT_CHARS = 5_200
CONTEXT_CHARS = 800
MIN_PREVIEW_CHARS = 500
MAX_PREVIEW_CHARS = 200_000
SCOPE_COMPLETE = "complete"
SCOPE_CHARACTERS = "characters"
SCOPE_CHAPTERS = "chapters"
VALID_SCOPE_MODES = {SCOPE_COMPLETE, SCOPE_CHARACTERS, SCOPE_CHAPTERS}
MIN_OUTPUT_RATIO = 0.70
MAX_OUTPUT_RATIO = 3.20

_CHAPTER_HEADING_RE = re.compile(
    r"^(?:"
    r"第\s*[一二三四五六七八九十百千零〇两\d]+\s*[回章节篇部卷折出]"
    r"|[卷部篇]\s*[一二三四五六七八九十百千零〇两\d]+"
    r"|楔子|引子|序|序文|序章|序言|前言|后记|尾声"
    r")[^\n]{0,100}$"
)
_META_RESIDUE = (
    "以下是翻译内容",
    "以下为翻译",
    "翻译说明",
    "修改说明",
    "作为一个AI",
    "作为AI",
    "根据您的要求",
    "本次翻译",
    "输出要求",
    "提示词",
)
_MODERN_INTRUSIONS = (
    "KPI",
    "PPT",
    "OKR",
    "打工人",
    "职场PUA",
    "互联网思维",
    "流量密码",
    "霸总",
    "系统绑定",
    "金手指",
    "穿越者",
    "重生者",
    "恐怖如斯",
    "全场震惊",
)


@dataclass(frozen=True)
class ClassicalVernacularProfile:
    work_type: str = "古典小说"
    era_background: str = "保持原文时代背景，不擅自现代化"
    narrative_perspective: str = "保持原文叙事视角"
    target_audience: str = "现代汉语读者"
    vernacular_level: str = "准确、清楚、完整的现代白话"
    adaptation_intensity: str = "仅完整翻译"
    web_fiction_ratio: str = "0%"
    language_tendency: str = "忠实原意，不做文学改写或风格润色"
    must_preserve: str = "原文全部信息、顺序、人物、称谓、专名、数字、事件、因果与语气"
    focus_issues: str = "漏译、错译、增译、缩写、扩写、重排、解释和续写"
    special_requirements: str = "只做完整翻译；不得改写、润色、扩写、缩写、重组、评论、总结或续写"
    custom_prompt: str = ""
    scope_mode: str = SCOPE_COMPLETE
    preview_target_chars: int = 0
    preview_chapters: int = 0


@dataclass(frozen=True)
class SourceSection:
    source_id: str
    source_title: str
    title: str
    text: str


def normalize_profile(value: dict[str, Any] | None) -> ClassicalVernacularProfile:
    # Older adaptation/style fields remain ignored.  Only the editable
    # translation instruction and bounded execution scope are accepted.
    raw = dict(value or {})
    scope_mode = str(raw.get("scope_mode") or SCOPE_COMPLETE).strip().lower()
    if scope_mode not in VALID_SCOPE_MODES:
        raise legacy.PublicDomainValidationError("古文翻译范围必须是完整翻译、按字数试译或按章节试译。")
    custom_prompt = str(raw.get("custom_prompt") or "").strip()
    if len(custom_prompt) > 30_000:
        raise legacy.PublicDomainValidationError("自定义翻译 Prompt 不能超过 30000 字。")
    try:
        preview_target_chars = int(raw.get("preview_target_chars") or 0)
        preview_chapters = int(raw.get("preview_chapters") or 0)
    except (TypeError, ValueError) as exc:
        raise legacy.PublicDomainValidationError("试译字数或章节数格式不正确。") from exc
    if scope_mode == SCOPE_CHARACTERS and not (
        MIN_PREVIEW_CHARS <= preview_target_chars <= MAX_PREVIEW_CHARS
    ):
        raise legacy.PublicDomainValidationError(
            f"按字数试译请输入 {MIN_PREVIEW_CHARS}–{MAX_PREVIEW_CHARS} 字。"
        )
    if scope_mode == SCOPE_CHAPTERS and not (1 <= preview_chapters <= MAX_SECTION_COUNT):
        raise legacy.PublicDomainValidationError(
            f"按章节试译请输入 1–{MAX_SECTION_COUNT} 章。"
        )
    return ClassicalVernacularProfile(
        custom_prompt=custom_prompt,
        scope_mode=scope_mode,
        preview_target_chars=preview_target_chars,
        preview_chapters=preview_chapters,
    )


def get_default_user_prompt() -> str:
    return DEFAULT_USER_PROMPT.read_text(encoding="utf-8").strip()


def run_classical_vernacular_longform(
    *,
    sources: Iterable[legacy.PublicDomainSource],
    reports: tuple[legacy.SourceReport, ...],
    dimensions: legacy.BookDimensions,
    work_dir: Path,
    target_title: str | None,
    adaptation_profile: dict[str, Any] | None,
    on_progress: legacy.ProgressCallback | None,
    on_chapter_completed: legacy.ChapterCompletedCallback | None,
) -> legacy.PublicDomainLongformResult:
    """Translate all supplied public-domain classical text in source order."""

    items = list(sources)
    raw_profile = dict(adaptation_profile or {})
    profile = normalize_profile(raw_profile)
    # A preview may later be upgraded to a complete translation on the same
    # task.  Scope is an execution concern, not a translation-style change.
    # Keep the original profile fingerprint and segment boundary so already
    # paid preview checkpoints remain reusable after the scope changes.
    checkpoint_profile_raw = raw_profile.get("_checkpoint_profile")
    checkpoint_profile = (
        normalize_profile(checkpoint_profile_raw)
        if isinstance(checkpoint_profile_raw, dict)
        else profile
    )
    artifact_profile_hash = _sha(
        json.dumps(asdict(checkpoint_profile), ensure_ascii=False, sort_keys=True)
    )
    checkpoint_segment_limit = raw_profile.get("_checkpoint_segment_limit")
    try:
        segment_limit_override = (
            max(600, min(SEGMENT_CHARS, int(checkpoint_segment_limit)))
            if checkpoint_segment_limit
            else None
        )
    except (TypeError, ValueError):
        segment_limit_override = None
    user_prompt = profile.custom_prompt or get_default_user_prompt()
    prompt_hash = _prompt_hash(user_prompt)
    all_sections = extract_source_sections(items)
    if not all_sections:
        raise legacy.PublicDomainValidationError("没有识别到可翻译的公版古文正文。")
    if len(all_sections) > MAX_SECTION_COUNT:
        raise legacy.PublicDomainValidationError(
            f"识别到 {len(all_sections)} 个原文章节，超过单任务 {MAX_SECTION_COUNT} 章上限。"
        )
    sections = all_sections
    if profile.scope_mode == SCOPE_CHAPTERS:
        sections = all_sections[: profile.preview_chapters]
    preview_target_chars = (
        profile.preview_target_chars if profile.scope_mode == SCOPE_CHARACTERS else 0
    )

    plan = {
        "policy_version": POLICY_VERSION,
        "prompt_version": PROMPT_VERSION,
        "prompt_hash": prompt_hash,
        "title": (target_title or items[0].title).strip(),
        "chapter_count": len(sections),
        "available_chapter_count": len(all_sections),
        "scope_mode": profile.scope_mode,
        "preview_target_chars": preview_target_chars,
        "preview_chapters": profile.preview_chapters,
        "user_prompt": user_prompt,
        "profile": asdict(profile),
        "chapters": [
            {
                "number": index,
                "title": section.title,
                "source_id": section.source_id,
                "source_title": section.source_title,
                "source_chars": legacy.count_text_chars(section.text),
                "source_sha256": legacy.text_sha256(section.text),
            }
            for index, section in enumerate(sections, start=1)
        ],
    }
    # Keep the exact editable instruction beside the source/checkpoints.  This
    # makes every long-running task reproducible and proves which prompt was
    # applied after a worker restart without placing the text in application
    # logs.
    _atomic_write_text(work_dir / "00_classical_user_prompt.md", user_prompt)
    _atomic_write_json(work_dir / "01_classical_vernacular_plan.json", plan)
    _notify(
        on_progress,
        8,
        (
            f"已识别 {len(all_sections)} 个原文章节，本次按字数试译约 {preview_target_chars} 字"
            if profile.scope_mode == SCOPE_CHARACTERS
            else f"已识别 {len(all_sections)} 个原文章节，本次试译前 {len(sections)} 章"
            if profile.scope_mode == SCOPE_CHAPTERS
            else f"已按原文顺序识别 {len(sections)} 个章节，开始逐段完整翻译；每段完成即保存断点"
        ),
    )

    chapters: list[legacy.ChapterResult] = []
    translated_chars = 0
    for index, section in enumerate(sections, start=1):
        remaining_chars = max(0, preview_target_chars - translated_chars)
        if preview_target_chars and chapters and remaining_chars <= 0:
            break
        chapter = _translate_section(
            number=index,
            section=section,
            total_sections=len(sections),
            work_dir=work_dir,
            work_title=plan["title"],
            profile=profile,
            artifact_profile_hash=artifact_profile_hash,
            segment_limit_override=segment_limit_override,
            prompt_hash=prompt_hash,
            user_prompt=user_prompt,
            previous_section_context=(sections[index - 2].text[-CONTEXT_CHARS:] if index > 1 else "无"),
            previous_chapter_translation_context=(chapters[-1].text[-CONTEXT_CHARS:] if chapters else "无"),
            next_section_context=(sections[index].text[:CONTEXT_CHARS] if index < len(sections) else "无"),
            preview_output_chars=remaining_chars if preview_target_chars else 0,
            on_progress=on_progress,
        )
        chapters.append(chapter)
        translated_chars += legacy.count_text_chars(chapter.text)
        if on_chapter_completed is not None:
            on_chapter_completed(chapter)
        progress = 8 + int(84 * index / len(sections))
        _notify(on_progress, progress, f"古文完整翻译 {index}/{len(sections)} 章已完成并落盘")

    body_chars = sum(legacy.count_text_chars(item.text) for item in chapters)
    source_chars = sum(int(item.audit.get("processed_source_chars") or 0) for item in chapters)
    blocking: list[str] = []
    if profile.scope_mode != SCOPE_CHARACTERS and len(chapters) != len(sections):
        blocking.append("最终章节数量与原文章节数量不一致")
    expected_source_chars = sum(legacy.count_text_chars(item.text) for item in sections)
    if profile.scope_mode == SCOPE_COMPLETE and source_chars != expected_source_chars:
        blocking.append(
            f"全书原文覆盖 {source_chars}/{expected_source_chars} 字，存在未处理原文"
        )
    if body_chars < _minimum_output_chars(source_chars):
        blocking.append(
            f"全稿白话译文仅 {body_chars} 字，低于原文 {source_chars} 字的完整性下限"
        )
    quality_report = {
        "status": "needs_review" if blocking else "ok",
        "policy_version": POLICY_VERSION,
        "prompt_version": PROMPT_VERSION,
        "prompt_hash": prompt_hash,
        "prompt_injection_mode": "exact_user_prompt_on_every_paid_segment",
        "prompt_injection_segments": sum(
            int(item.audit.get("prompt_injection_segments") or 0) for item in chapters
        ),
        "translated_segments": sum(int(item.audit.get("segments") or 0) for item in chapters),
        "pipeline": "source_order -> bounded_segments -> paragraph_ids -> complete_translation -> local_completeness_gate -> bookshelf",
        "source_chapters": len(chapters),
        "requested_source_chapters": len(sections),
        "available_source_chapters": len(all_sections),
        "output_chapters": len(chapters),
        "source_chars": source_chars,
        "output_chars": body_chars,
        "paid_calls_per_segment": 1,
        "automatic_retry_calls": 0,
        "scope_mode": profile.scope_mode,
        "preview_target_chars": preview_target_chars,
        "preview_target_chapters": profile.preview_chapters,
        "is_preview": profile.scope_mode != SCOPE_COMPLETE,
        "blocking_issues": blocking,
        "chapter_audits": [item.audit for item in chapters],
    }
    _atomic_write_json(work_dir / "quality_report.json", quality_report)
    if blocking:
        raise legacy.PublicDomainQualityError("；".join(blocking))

    title = str(plan["title"] or items[0].title).strip()
    summary = (
        f"《{items[0].title}》古文白话试译稿；仅包含本次选定范围。"
        if profile.scope_mode != SCOPE_COMPLETE
        else f"按公版原作的章节与段落顺序，对《{items[0].title}》逐段完整翻译为现代白话。"
    ) + "译文不增删原文信息，不改写、不重组、不评论、不总结，也不续写。"
    markdown = legacy.render_final_markdown(title=title, summary=summary, chapters=chapters)
    _atomic_write_text(work_dir / "04_final.md", markdown)
    _notify(on_progress, 94, "完整翻译与整书完整性检查完成，正在写入书架")
    return legacy.PublicDomainLongformResult(
        title=title,
        summary=summary,
        markdown=markdown,
        chapters=tuple(chapters),
        source_reports=reports,
        dimensions=legacy.BookDimensions(
            target_chars=body_chars,
            target_chapters=len(chapters),
            median_source_chars=dimensions.median_source_chars,
            median_source_chapters=len(chapters),
        ),
        quality_report=quality_report,
        plan=plan,
    )


def extract_source_sections(sources: Iterable[legacy.PublicDomainSource]) -> list[SourceSection]:
    """Extract source chapters deterministically without asking a model to re-plan them."""

    sections: list[SourceSection] = []
    for source_index, source in enumerate(sources, start=1):
        text = legacy.normalize_source_text(source.text)
        lines = text.splitlines()
        detected_heading_indexes = [
            index for index, line in enumerate(lines) if _CHAPTER_HEADING_RE.fullmatch(line.strip())
        ]
        heading_indexes, catalog_removed = _body_heading_indexes(
            lines,
            detected_heading_indexes,
        )
        source_id = f"S{source_index:02d}"
        if not heading_indexes:
            if text.strip():
                sections.append(
                    SourceSection(
                        source_id=source_id,
                        source_title=source.title,
                        title=source.title,
                        text=text,
                    )
                )
            continue
        # A genuine unlabelled preface can remain attached to the first
        # chapter.  Once a catalogue block has been identified, however,
        # everything before the first real body heading is navigation or
        # editorial front matter and must never be translated as chapter 1.
        preface = "" if catalog_removed else "\n".join(lines[: heading_indexes[0]]).strip()
        for position, heading_index in enumerate(heading_indexes):
            end = heading_indexes[position + 1] if position + 1 < len(heading_indexes) else len(lines)
            body = "\n".join(lines[heading_index + 1 : end]).strip()
            if position == 0 and preface:
                body = f"{preface}\n\n{body}".strip()
            if not body:
                continue
            sections.append(
                SourceSection(
                    source_id=source_id,
                    source_title=source.title,
                    title=lines[heading_index].strip(),
                    text=body,
                )
            )
    return sections


def _body_heading_indexes(
    lines: list[str],
    heading_indexes: list[int],
) -> tuple[list[int], bool]:
    """Remove a front-matter table of contents from chapter boundaries.

    EPUB/PDF/Word extraction commonly places a dense list of every chapter
    title before the actual book.  Treating that list as body text creates a
    fake first chapter containing only a few catalogue words and makes the
    completeness gate report errors such as ``原文 20 字，成稿 11 字``.  A
    catalogue is recognised only when at least three headings are adjacent
    and real headings occur later, so normal short works keep their original
    boundaries.
    """

    if len(heading_indexes) < 4:
        return heading_indexes, False

    runs: list[list[int]] = []
    current: list[int] = [heading_indexes[0]]
    for index in heading_indexes[1:]:
        if index - current[-1] <= 2:
            current.append(index)
        else:
            runs.append(current)
            current = [index]
    runs.append(current)

    for run in runs:
        if len(run) < 3 or run[-1] == heading_indexes[-1]:
            continue
        later = [value for value in heading_indexes if value > run[-1]]
        if not later:
            continue

        # Prefer direct duplicate evidence, while still handling catalogues
        # whose extracted entries contain page numbers or spacing changes.
        later_keys = {_heading_key(lines[value]) for value in later}
        duplicate_count = sum(_heading_key(lines[value]) in later_keys for value in run)
        front_matter_limit = max(80, int(len(lines) * 0.20))
        looks_like_front_catalog = run[0] <= front_matter_limit
        repeated_in_body = duplicate_count >= min(3, len(run))
        if looks_like_front_catalog and repeated_in_body:
            return later, True

    return heading_indexes, False


def _heading_key(value: str) -> str:
    return re.sub(r"[\s·•.。…—_-]+", "", value or "").strip()


def _translate_section(
    *,
    number: int,
    section: SourceSection,
    total_sections: int,
    work_dir: Path,
    work_title: str,
    profile: ClassicalVernacularProfile,
    artifact_profile_hash: str,
    segment_limit_override: int | None,
    prompt_hash: str,
    user_prompt: str,
    previous_section_context: str,
    previous_chapter_translation_context: str,
    next_section_context: str,
    preview_output_chars: int,
    on_progress: legacy.ProgressCallback | None,
) -> legacy.ChapterResult:
    chapter_dir = work_dir / "classical_chapters"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    source_hash = legacy.text_sha256(section.text)
    artifact_key = _sha(
        "|".join(
            (
                POLICY_VERSION,
                PROMPT_VERSION,
                prompt_hash,
                artifact_profile_hash,
                source_hash,
                section.title,
            )
        )
    )
    final_path = chapter_dir / f"{number:03d}.json"
    cached = _load_json(final_path)
    cached_is_partial = bool(
        isinstance(cached, dict)
        and isinstance(cached.get("chapter"), dict)
        and isinstance(cached["chapter"].get("audit"), dict)
        and cached["chapter"]["audit"].get("partial_section")
    )
    if (
        cached
        and cached.get("artifact_key") == artifact_key
        and not (profile.scope_mode == SCOPE_COMPLETE and cached_is_partial)
    ):
        payload = dict(cached.get("chapter") or {})
        return legacy.ChapterResult(
            number=number,
            title=section.title,
            text=str(payload.get("text") or ""),
            summary=str(payload.get("summary") or ""),
            used_claim_ids=(section.source_id,),
            audit=dict(payload.get("audit") or {}),
        )

    segment_limit = segment_limit_override or SEGMENT_CHARS
    if preview_output_chars and segment_limit_override is None:
        # Smaller semantic segments keep a paid preview close to the selected
        # output size without truncating a model response mid-sentence.
        segment_limit = min(SEGMENT_CHARS, max(600, int(preview_output_chars / 1.35)))
    segments = _split_for_context(section.text, limit=segment_limit)
    outputs: list[str] = []
    segment_audits: list[dict[str, Any]] = []
    processed_source_chars = 0
    for segment_index, source_text in enumerate(segments, start=1):
        segment_key = _sha(
            "|".join((artifact_key, str(segment_index), legacy.text_sha256(source_text)))
        )
        segment_label = (
            f"第 {segment_index}/{len(segments)} 段（同属原文章节《{section.title}》）"
            if len(segments) > 1
            else "本章完整内容"
        )
        source_chars = legacy.count_text_chars(source_text)
        target_chars = max(800, int(source_chars * 1.75))
        marked_source, expected_markers = _mark_source_units(source_text)
        previous_context = (
            segments[segment_index - 2][-CONTEXT_CHARS:]
            if segment_index > 1
            else previous_section_context
        )
        previous_translation_context = (
            outputs[-1][-CONTEXT_CHARS:]
            if outputs
            else previous_chapter_translation_context
        )
        next_context = (
            segments[segment_index][:CONTEXT_CHARS]
            if segment_index < len(segments)
            else next_section_context
        )
        prompt = _render(
            TRANSLATION_PROMPT,
            user_prompt=user_prompt,
            work_title=work_title,
            chapter_title=section.title,
            segment_label=segment_label,
            target_chars=target_chars,
            previous_context=previous_context,
            previous_translation_context=previous_translation_context,
            next_context=next_context,
            source_text=marked_source,
        )
        if user_prompt not in prompt:
            raise legacy.PublicDomainValidationError(
                f"《{section.title}》第 {segment_index} 段未完整注入用户 Prompt"
            )
        rendered_prompt_hash = _sha(prompt)
        translation_path = chapter_dir / f"{number:03d}_{segment_index:04d}_translation.md"
        translation_meta_path = chapter_dir / f"{number:03d}_{segment_index:04d}_translation.json"
        translation_meta = _load_json(translation_meta_path)
        if (
            translation_meta
            and translation_meta.get("segment_key") == segment_key
            and translation_path.exists()
        ):
            final = translation_path.read_text(encoding="utf-8").strip()
            translated_units = int(translation_meta.get("translated_units") or 0)
        else:
            raw_translation = legacy._call_llm(  # noqa: SLF001
                work_dir,
                action=f"classical_complete_translation:{number:04d}:{segment_index:04d}",
                prompt=prompt,
                temperature=0.10,
                max_output_tokens=_output_tokens(target_chars),
                # Translation is a constrained transformation rather than a
                # reasoning task.  Disabling hidden thinking prevents the
                # model from spending the whole output budget on reasoning
                # and returning an empty visible answer.  One provider call
                # per segment also preserves the advertised cost contract.
                thinking=False,
                retries=1,
            )
            final, translated_units = _parse_marked_translation(
                raw_translation,
                expected_markers=expected_markers,
                chapter_title=section.title,
            )
            issues = _segment_issues(source_text=source_text, candidate=final)
            if issues:
                raise legacy.PublicDomainQualityError(
                    f"《{section.title}》第 {segment_index} 段未通过完整翻译检查：{'；'.join(issues)}"
                )
            _atomic_write_text(translation_path, final)
            _atomic_write_json(
                translation_meta_path,
                {
                    "segment_key": segment_key,
                    "prompt_hash": prompt_hash,
                    "rendered_prompt_hash": rendered_prompt_hash,
                    "user_prompt_injected": True,
                    "translated_units": translated_units,
                    "source_units": len(expected_markers),
                    "operation": "complete_translation_only",
                },
            )

        issues = _segment_issues(source_text=source_text, candidate=final)
        audit = {
            "segment": segment_index,
            "source_chars": source_chars,
            "translation_chars": legacy.count_text_chars(final),
            "source_units": len(expected_markers),
            "translated_units": translated_units,
            "prompt_hash": prompt_hash,
            "rendered_prompt_hash": rendered_prompt_hash,
            "user_prompt_injected": True,
            "issues": issues,
        }
        segment_audits.append(audit)
        _atomic_write_json(
            chapter_dir / f"{number:03d}_{segment_index:02d}_audit.json", audit
        )
        if issues:
            raise legacy.PublicDomainQualityError(
                f"《{section.title}》第 {segment_index} 段未通过完整翻译检查：{'；'.join(issues)}"
            )
        outputs.append(final)
        processed_source_chars += source_chars
        chapter_fraction = ((number - 1) + (segment_index / max(1, len(segments)))) / max(
            1, total_sections
        )
        _notify(
            on_progress,
            8 + int(84 * chapter_fraction),
            f"完整翻译第 {number}/{total_sections} 章，第 {segment_index}/{len(segments)} 段已保存",
        )
        if preview_output_chars and sum(legacy.count_text_chars(value) for value in outputs) >= preview_output_chars:
            break

    text = "\n\n".join(value.strip() for value in outputs if value.strip()).strip()
    chapter_audit = {
        "number": number,
        "title": section.title,
        "source_id": section.source_id,
        "source_title": section.source_title,
        "source_sha256": source_hash,
        "source_chars": legacy.count_text_chars(section.text),
        "processed_source_chars": processed_source_chars,
        "output_chars": legacy.count_text_chars(text),
        "segments": len(segment_audits),
        "available_segments": len(segments),
        "partial_section": len(segment_audits) < len(segments),
        "segment_audits": segment_audits,
        "rounds": 1,
        "operation": "complete_translation_only",
        "prompt_version": PROMPT_VERSION,
        "prompt_hash": prompt_hash,
        "prompt_injection_segments": sum(
            1 for item in segment_audits if item.get("user_prompt_injected") is True
        ),
        "pass": True,
    }
    chapter = legacy.ChapterResult(
        number=number,
        title=section.title,
        text=text,
        summary=legacy._fallback_summary(text),  # noqa: SLF001
        used_claim_ids=(section.source_id,),
        audit=chapter_audit,
    )
    _atomic_write_json(
        final_path,
        {
            "artifact_key": artifact_key,
            "policy_version": POLICY_VERSION,
            "chapter": {
                "text": chapter.text,
                "summary": chapter.summary,
                "audit": chapter.audit,
            },
        },
    )
    return chapter


def _split_for_context(text: str, limit: int = SEGMENT_CHARS) -> list[str]:
    paragraphs = [value.strip() for value in re.split(r"\n+", text) if value.strip()]
    if not paragraphs:
        return []
    chunks: list[str] = []
    current: list[str] = []
    current_chars = 0
    for paragraph in paragraphs:
        paragraph_chars = legacy.count_text_chars(paragraph)
        if paragraph_chars > limit:
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_chars = 0
            chunks.extend(_split_long_paragraph(paragraph, limit))
            continue
        if current and current_chars + paragraph_chars > limit:
            chunks.append("\n\n".join(current))
            current = []
            current_chars = 0
        current.append(paragraph)
        current_chars += paragraph_chars
    if current:
        chunks.append("\n\n".join(current))
    return [value for value in chunks if value.strip()]


def _split_long_paragraph(text: str, limit: int) -> list[str]:
    sentences = [value for value in re.split(r"(?<=[。！？；!?;])", text) if value]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if current and legacy.count_text_chars(current + sentence) > limit:
            chunks.append(current.strip())
            current = ""
        if legacy.count_text_chars(sentence) > limit:
            for start in range(0, len(sentence), limit):
                piece = sentence[start : start + limit].strip()
                if piece:
                    if current:
                        chunks.append(current.strip())
                        current = ""
                    chunks.append(piece)
        else:
            current += sentence
    if current.strip():
        chunks.append(current.strip())
    return chunks


def _mark_source_units(text: str) -> tuple[str, tuple[str, ...]]:
    """Attach deterministic paragraph IDs so omissions/reordering are visible."""

    units = [value.strip() for value in re.split(r"\n+", text) if value.strip()]
    if not units:
        raise legacy.PublicDomainQualityError("待翻译分段为空")
    markers = tuple(f"[[P{index:04d}]]" for index in range(1, len(units) + 1))
    marked = "\n\n".join(
        f"{marker}\n{unit}" for marker, unit in zip(markers, units, strict=True)
    )
    return marked, markers


def _parse_marked_translation(
    text: str,
    *,
    expected_markers: tuple[str, ...],
    chapter_title: str,
) -> tuple[str, int]:
    """Remove paragraph IDs only after proving every source unit survived in order."""

    value = re.sub(r"^```(?:markdown|md|text)?\s*", "", (text or "").strip(), flags=re.I)
    value = re.sub(r"\s*```$", "", value).strip()
    pattern = re.compile(r"\[\[P\d{4}\]\]")
    matches = list(pattern.finditer(value))
    received = tuple(match.group(0) for match in matches)
    if received != expected_markers:
        raise legacy.PublicDomainQualityError(
            f"《{chapter_title}》译文段落编号不完整或顺序变化："
            f"应有 {len(expected_markers)} 段，实际识别 {len(received)} 段"
        )
    if not matches:
        raise legacy.PublicDomainQualityError(f"《{chapter_title}》译文没有段落编号")
    if value[: matches[0].start()].strip():
        raise legacy.PublicDomainQualityError(f"《{chapter_title}》译文在首段前新增了说明文字")

    translated: list[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(value)
        block = value[match.end() : end].strip()
        if not block:
            raise legacy.PublicDomainQualityError(
                f"《{chapter_title}》译文第 {index + 1} 个原文段落为空，疑似漏译"
            )
        translated.append(block)
    return legacy.normalize_source_text("\n\n".join(translated)), len(translated)


def _segment_issues(*, source_text: str, candidate: str) -> list[str]:
    issues: list[str] = []
    source_chars = legacy.count_text_chars(source_text)
    output_chars = legacy.count_text_chars(candidate)
    if not candidate.strip():
        return ["模型返回空正文"]
    # Do not impose a 120-character absolute floor on a short source line.
    # Short classical headings, verse lines and terse dialogue can be fully
    # translated into fewer characters.  Paragraph IDs plus explicit
    # number/title anchors remain the primary completeness evidence there.
    minimum_output_chars = _minimum_output_chars(source_chars)
    if output_chars < minimum_output_chars:
        issues.append(f"正文过短（原文 {source_chars} 字，成稿 {output_chars} 字）")
    if output_chars > max(600, int(source_chars * MAX_OUTPUT_RATIO)):
        issues.append(f"正文异常扩写（原文 {source_chars} 字，成稿 {output_chars} 字）")
    for marker in _META_RESIDUE:
        if marker.lower() in candidate.lower():
            issues.append(f"出现模型过程说明“{marker}”")
            break
    found_modern = [value for value in _MODERN_INTRUSIONS if value.lower() in candidate.lower()]
    if found_modern:
        issues.append(f"出现现代或套路化表达：{'、'.join(found_modern[:5])}")
    source_numbers = re.findall(r"\d+(?:[./:-]\d+)*", source_text)
    missing_numbers = [value for value in sorted(set(source_numbers)) if value not in candidate]
    if missing_numbers:
        issues.append(f"遗漏原文明示数字：{'、'.join(missing_numbers[:8])}")
    source_titles = re.findall(r"《[^》]{1,40}》", source_text)
    missing_titles = [value for value in sorted(set(source_titles)) if value not in candidate]
    if missing_titles:
        issues.append(f"遗漏原文书名或篇名：{'、'.join(missing_titles[:5])}")
    return issues


def _minimum_output_chars(source_chars: int) -> int:
    if source_chars < 40:
        return max(2, int(source_chars * 0.35))
    if source_chars < 160:
        return max(12, int(source_chars * 0.50))
    return int(source_chars * MIN_OUTPUT_RATIO)


def _clean_model_text(text: str, chapter_title: str) -> str:
    value = re.sub(r"^```(?:markdown|md|text)?\s*", "", (text or "").strip(), flags=re.I)
    value = re.sub(r"\s*```$", "", value).strip()
    lines = value.splitlines()
    if lines and lines[0].strip().lstrip("# ") == chapter_title.strip():
        lines = lines[1:]
    while lines and not lines[0].strip():
        lines.pop(0)
    return legacy.normalize_source_text("\n".join(lines))


def _render(path: Path, **values: object) -> str:
    template = path.read_text(encoding="utf-8")
    placeholders = sorted(set(re.findall(r"\{([a-z_]+)\}", template)))
    missing = [key for key in placeholders if key not in values]
    if missing:
        raise legacy.PublicDomainValidationError(
            f"古文白话 Prompt 仍有未填字段：{', '.join('{' + key + '}' for key in missing)}"
        )
    # Replace template fields in one pass.  A user prompt containing literal
    # braces or names such as {work_title} must remain byte-for-byte intact and
    # must never be interpreted as another system placeholder.
    return re.sub(
        r"\{([a-z_]+)\}",
        lambda match: str(values[match.group(1)]),
        template,
    ).strip()


def _output_tokens(target_chars: int) -> int:
    return max(5_000, min(18_000, int(target_chars * 1.8)))


def _notify(callback: legacy.ProgressCallback | None, progress: int, step: str) -> None:
    if callback is not None:
        callback(max(0, min(100, int(progress))), step[:255])


def _atomic_write_text(path: Path, text: str) -> None:
    legacy._atomic_write_text(path, text)  # noqa: SLF001


def _atomic_write_json(path: Path, payload: object) -> None:
    legacy._atomic_write_json(path, payload)  # noqa: SLF001


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _sha(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _prompt_hash(user_prompt: str) -> str:
    return _sha(
        TRANSLATION_PROMPT.read_text(encoding="utf-8")
        + "\n---USER---\n"
        + user_prompt
    )


__all__ = [
    "ClassicalVernacularProfile",
    "POLICY_VERSION",
    "PROMPT_VERSION",
    "extract_source_sections",
    "get_default_user_prompt",
    "normalize_profile",
    "run_classical_vernacular_longform",
]
