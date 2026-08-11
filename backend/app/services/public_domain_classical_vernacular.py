"""Resumable, translation-only classical Chinese to modern Chinese pipeline.

This route is intentionally isolated from public-domain multi-source fusion.
It never sends an entire long book into one context window.  Source chapters
are processed strictly in order, divided into bounded segments, translated,
and checkpointed after every successful segment.  Bounded sentence-unit IDs,
structured transport and local anchor checks protect completeness.  A failed
segment stops at its checkpoint and is never silently repaired or retried.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import time
from typing import Any, Callable, Iterable

from app.core.config import settings
from app.services import public_domain_longform as legacy


POLICY_VERSION = "public-domain-classical-external-prompt-v9"
PROMPT_VERSION = "external-prompt-only-per-segment-v9"
COVERAGE_POLICY_VERSION = "structured-sentence-unit-coverage-v2"

MAX_SECTION_COUNT = 100_000
# This is an internal per-request context ceiling, not a book-size limit.
# Short chapters are sent whole; only chapters larger than the model-safe
# semantic window are paged at paragraph/sentence boundaries.  A task may
# contain any number of these serial, resumable pages.
SEGMENT_CHARS = 12_000
CONTEXT_CHARS = 800
MIN_PREVIEW_CHARS = 500
MAX_PREVIEW_CHARS = 200_000
SCOPE_COMPLETE = "complete"
SCOPE_CHARACTERS = "characters"
SCOPE_CHAPTERS = "chapters"
VALID_SCOPE_MODES = {SCOPE_COMPLETE, SCOPE_CHARACTERS, SCOPE_CHAPTERS}
MIN_OUTPUT_RATIO = 0.70
MAX_OUTPUT_RATIO = 3.20
SOURCE_UNIT_TARGET_CHARS = 48
SOURCE_UNIT_MAX_CHARS = 96
TRANSLATION_WRITER_PROVIDER = "openai"
TRANSLATION_WRITER_MODEL = "gpt-5.6-sol"
TRANSLATION_WRITER_REASONING_EFFORT = "xhigh"
TRANSLATION_WRITER_VERBOSITY = "low"
TRANSLATION_WRITER_MAX_OUTPUT_TOKENS = 0
OPENAI_BACKGROUND_TIMEOUT_SECONDS = 7_200
OPENAI_BACKGROUND_STATE_FILE = "classical_openai_background_responses.json"
_WEB_SEARCH_REQUEST_RE = re.compile(
    r"(?:联网|上网|网络)(?:检索|搜索|查证|核实|考证|查询|资料)|"
    r"(?:检索|搜索|查证|核实|考证|查询)(?:网络|网上|在线|可靠资料)"
)
_WEB_SEARCH_NEGATION_RE = re.compile(r"(?:不要|不得|禁止|无需|不必|不能).{0,8}(?:联网|上网|网络搜索)")


def _translation_writer_config() -> tuple[str, str, str]:
    provider = str(settings.CLASSICAL_TRANSLATION_WRITER_PROVIDER or "openai").strip().lower()
    if provider != TRANSLATION_WRITER_PROVIDER:
        raise legacy.PublicDomainValidationError(
            "公版古文白话主笔固定使用 OpenAI gpt-5.6-sol"
        )
    model = str(settings.OPENAI_WRITER_MODEL or "").strip()
    if model != TRANSLATION_WRITER_MODEL:
        raise legacy.PublicDomainValidationError(
            "公版古文白话 OpenAI 主笔必须使用 gpt-5.6-sol"
        )
    return provider, model, TRANSLATION_WRITER_REASONING_EFFORT

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
    custom_prompt: str = ""
    scope_mode: str = SCOPE_COMPLETE
    preview_target_chars: int = 0
    preview_chapters: int = 0


@dataclass(frozen=True)
class TranslationSourceUnit:
    """A bounded source span that must have one independently checked translation."""

    marker: str
    text: str
    paragraph_index: int
    source_chars: int
    source_sha256: str


class ClassicalTranslationPaused(RuntimeError):
    """Raised only at a durable segment boundary after a pause request."""


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
    if not custom_prompt:
        raise legacy.PublicDomainValidationError("请输入本次古文白话翻译 Prompt；系统没有内置 Prompt。")
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
    """Compatibility endpoint value: this route intentionally has no default prompt."""

    return ""


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
    should_pause: Callable[[], bool] | None = None,
) -> legacy.PublicDomainLongformResult:
    """Translate all supplied public-domain classical text in source order."""

    items = list(sources)
    raw_profile = dict(adaptation_profile or {})
    profile = normalize_profile(raw_profile)
    writer_provider, writer_model, writer_reasoning_effort = _translation_writer_config()
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
    user_prompt = profile.custom_prompt
    prompt_hash = _prompt_hash(user_prompt)
    stored_local_overrides = (
        raw_profile.get("_local_prompt_overrides")
        if isinstance(raw_profile.get("_local_prompt_overrides"), dict)
        else {}
    )
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
        "translation_writer_provider": writer_provider,
        "translation_writer_model": writer_model,
        "translation_writer_reasoning_effort": writer_reasoning_effort,
        "translation_writer_max_output_tokens": TRANSLATION_WRITER_MAX_OUTPUT_TOKENS,
        "segment_strategy": "whole_chapter_then_adaptive_semantic_pages",
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
    source_manifest = {
        "policy_version": POLICY_VERSION,
        "prompt_version": PROMPT_VERSION,
        "prompt_hash": prompt_hash,
        "scope_mode": profile.scope_mode,
        "source_count": len(items),
        "available_chapter_count": len(all_sections),
        "selected_chapter_count": len(sections),
        "selected_source_chars": sum(
            legacy.count_text_chars(section.text) for section in sections
        ),
        "chapters": plan["chapters"],
        "source_order_digest": _sha(
            "|".join(
                f"{section.source_id}:{legacy.text_sha256(section.text)}"
                for section in sections
            )
        ),
    }
    _atomic_write_json(work_dir / "02_classical_source_manifest.json", source_manifest)
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
        if should_pause is not None and should_pause():
            raise ClassicalTranslationPaused("古文白话翻译已暂停，已完成分段均已保存")
        remaining_chars = max(0, preview_target_chars - translated_chars)
        if preview_target_chars and chapters and remaining_chars <= 0:
            break
        chapter_segment_limit = segment_limit_override
        if profile.scope_mode != SCOPE_CHARACTERS and segment_limit_override is not None:
            chapter_dir = work_dir / "classical_chapters"
            has_paid_checkpoint = any(
                chapter_dir.glob(f"{index:03d}_*_translation.json")
            ) if chapter_dir.exists() else False
            # Preserve the smaller preview boundary only for chapters that
            # actually have paid preview segments.  New chapters return to the
            # normal bounded context size instead of multiplying API calls.
            if not has_paid_checkpoint:
                chapter_segment_limit = None
        chapter = _translate_section(
            number=index,
            section=section,
            total_sections=len(sections),
            work_dir=work_dir,
            work_title=plan["title"],
            profile=profile,
            artifact_profile_hash=artifact_profile_hash,
            segment_limit_override=chapter_segment_limit,
            prompt_hash=prompt_hash,
            user_prompt=user_prompt,
            previous_section_context=(sections[index - 2].text[-CONTEXT_CHARS:] if index > 1 else "无"),
            previous_chapter_translation_context=(chapters[-1].text[-CONTEXT_CHARS:] if chapters else "无"),
            next_section_context=(sections[index].text[:CONTEXT_CHARS] if index < len(sections) else "无"),
            preview_output_chars=remaining_chars if preview_target_chars else 0,
            on_progress=on_progress,
            should_pause=should_pause,
            prompt_overrides=(
                dict(stored_local_overrides.get(str(index)) or {})
                if isinstance(stored_local_overrides.get(str(index)), dict)
                else None
            ),
        )
        chapters.append(chapter)
        translated_chars += legacy.count_text_chars(chapter.text)
        if on_chapter_completed is not None:
            on_chapter_completed(chapter)
        progress = 8 + int(84 * index / len(sections))
        _notify(on_progress, progress, f"古文完整翻译 {index}/{len(sections)} 章已完成并落盘")

    body_chars = sum(legacy.count_text_chars(item.text) for item in chapters)
    source_chars = sum(int(item.audit.get("processed_source_chars") or 0) for item in chapters)
    source_units = sum(int(item.audit.get("source_units") or 0) for item in chapters)
    translated_units = sum(int(item.audit.get("translated_units") or 0) for item in chapters)
    blocking: list[str] = []
    if profile.scope_mode != SCOPE_CHARACTERS and len(chapters) != len(sections):
        blocking.append("最终章节数量与原文章节数量不一致")
    expected_source_chars = sum(legacy.count_text_chars(item.text) for item in sections)
    if profile.scope_mode != SCOPE_CHARACTERS and source_chars != expected_source_chars:
        blocking.append(
            f"全书原文覆盖 {source_chars}/{expected_source_chars} 字，存在未处理原文"
        )
    if source_units <= 0 or translated_units != source_units:
        blocking.append(
            f"全书句群翻译覆盖 {translated_units}/{source_units}，存在漏译或未验证内容"
        )
    if body_chars < _minimum_output_chars(source_chars):
        blocking.append(
            f"全稿白话译文仅 {body_chars} 字，低于原文 {source_chars} 字的完整性下限"
        )
    translated_segments = sum(int(item.audit.get("segments") or 0) for item in chapters)
    prompt_injection_segments = sum(
        int(item.audit.get("prompt_injection_segments") or 0) for item in chapters
    )
    web_search_tool_segments = sum(
        sum(
            1
            for segment in list(item.audit.get("segment_audits") or [])
            if segment.get("web_search_tool_enabled") is True
        )
        for item in chapters
    )
    if prompt_injection_segments != translated_segments:
        blocking.append(
            f"自定义 Prompt 注入覆盖 {prompt_injection_segments}/{translated_segments}，存在未注入分段"
        )
    quality_report = {
        "status": "needs_review" if blocking else "ok",
        "policy_version": POLICY_VERSION,
        "prompt_version": PROMPT_VERSION,
        "prompt_hash": prompt_hash,
        "prompt_injection_mode": "exact_user_prompt_on_every_paid_segment",
        "prompt_injection_segments": prompt_injection_segments,
        "web_search_tool_segments": web_search_tool_segments,
        "translated_segments": translated_segments,
        "source_units": source_units,
        "translated_units": translated_units,
        "coverage_policy_version": COVERAGE_POLICY_VERSION,
        "translation_writer_provider": writer_provider,
        "translation_writer_model": writer_model,
        "translation_writer_reasoning_effort": writer_reasoning_effort,
        "translation_writer_max_output_tokens": TRANSLATION_WRITER_MAX_OUTPUT_TOKENS,
        "output_cap_mode": "model_default",
        "targeted_repair_calls": sum(
            int(item.audit.get("targeted_repair_calls") or 0) for item in chapters
        ),
        "max_targeted_repairs_per_segment": 0,
        "pipeline": "source_order -> whole_chapter_or_adaptive_semantic_pages -> exact_external_prompt -> structured_sentence_units -> local_length_and_anchor_gate -> checkpoint -> chapter_and_book_coverage_gate -> bookshelf",
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

    required_source_chars = (
        source_chars if profile.scope_mode == SCOPE_CHARACTERS else expected_source_chars
    )
    coverage_manifest = {
        "status": "complete",
        "policy_version": POLICY_VERSION,
        "prompt_version": PROMPT_VERSION,
        "coverage_policy_version": COVERAGE_POLICY_VERSION,
        "scope_mode": profile.scope_mode,
        "whole_book_complete": profile.scope_mode == SCOPE_COMPLETE,
        "selected_scope_complete": True,
        "prompt_hash": prompt_hash,
        "source_order_digest": source_manifest["source_order_digest"],
        "expected_chapters": len(sections),
        "translated_chapters": len(chapters),
        "available_selected_source_chars": expected_source_chars,
        "required_source_chars": required_source_chars,
        "processed_source_chars": source_chars,
        "source_units": source_units,
        "translated_units": translated_units,
        "translated_segments": translated_segments,
        "prompt_injection_segments": prompt_injection_segments,
        "writer": {
            "provider": writer_provider,
            "model": writer_model,
            "reasoning_effort": writer_reasoning_effort,
            "max_output_tokens": TRANSLATION_WRITER_MAX_OUTPUT_TOKENS,
        },
        "chapters": [
            {
                "number": item.number,
                "title": item.title,
                "source_sha256": item.audit.get("source_sha256"),
                "processed_source_chars": item.audit.get("processed_source_chars"),
                "source_units": item.audit.get("source_units"),
                "translated_units": item.audit.get("translated_units"),
                "segments": item.audit.get("segments"),
                "prompt_injection_segments": item.audit.get("prompt_injection_segments"),
                "pass": item.audit.get("pass") is True,
            }
            for item in chapters
        ],
    }
    _atomic_write_json(work_dir / "05_classical_coverage_manifest.json", coverage_manifest)

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


def _prompt_requests_web_search(user_prompt: str) -> bool:
    """Expose web lookup only when the exact editable prompt asks for it.

    ChatGPT's web UI may browse automatically, while an API request only has
    that capability when a hosted tool is supplied.  This small gate closes
    that product/API gap without adding web-search cost to ordinary translation
    prompts or silently overriding an explicit "不要联网" instruction.
    """

    value = str(user_prompt or "").strip()
    if not value or _WEB_SEARCH_NEGATION_RE.search(value):
        return False
    return bool(_WEB_SEARCH_REQUEST_RE.search(value))


def _call_translation_writer(
    work_dir: Path,
    *,
    action: str,
    prompt: str,
    temperature: float,
    max_output_tokens: int,
    model: str | None = None,
    thinking: bool | None = None,
    retries: int | None = None,
    enable_web_search: bool = False,
    response_schema: dict[str, Any] | None = None,
) -> str:
    """Call the configured writer without changing the translation pipeline.

    OpenAI GPT-5.6 Sol xhigh receives the exact rendered prompt and returns
    structured text to the deterministic coverage, anchor, checkpoint and
    merge stages.
    This route does not impose an artificial max_output_tokens cap: the source
    remains in broad bounded semantic windows, leaving context for names,
    pronouns and tone while the model has room for both hidden reasoning and
    the complete visible translation.  The deterministic sentence-unit gate
    still decides whether every source span was translated.
    There is no transport-level paid retry and no automatic paid repair.
    """

    from app.services.content_engine import _openai_writer_call_result

    provider, configured_model, reasoning_effort = _translation_writer_config()
    selected_model = str(model or configured_model).strip()
    if selected_model != configured_model:
        raise legacy.PublicDomainValidationError("公版古文白话主笔模型与当前配置不一致")

    started = time.monotonic()
    schema_sha256 = (
        _sha(json.dumps(response_schema, ensure_ascii=False, sort_keys=True))
        if response_schema
        else ""
    )
    log: dict[str, Any] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "policy_version": POLICY_VERSION,
        "prompt_version": PROMPT_VERSION,
        "action": action,
        "provider": provider,
        "model": selected_model,
        "reasoning_effort": reasoning_effort,
        "verbosity": TRANSLATION_WRITER_VERBOSITY,
        "input_chars": len(prompt),
        "prompt_sha256": _sha(prompt),
        "max_output_tokens": max_output_tokens,
        "output_cap_mode": "model_default" if max_output_tokens <= 0 else "explicit",
        "temperature_compat": temperature,
        "thinking_compat": thinking,
        "requested_retries_compat": retries,
        "transport_retries": 0,
        "delivery_mode": "background_persisted",
        "web_search_tool_enabled": bool(enable_web_search),
        "structured_output": bool(response_schema),
        "response_schema_sha256": schema_sha256 or None,
        "status": "started",
    }
    try:
        # Preserve resumability for jobs created before structured transport was
        # enabled.  Structured jobs also bind their checkpoint to the schema so
        # a changed envelope can never reuse an incompatible paid response.
        prompt_digest = (
            _sha(f"{prompt}\n---TRANSPORT-SCHEMA---\n{schema_sha256}")
            if schema_sha256
            else _sha(prompt)
        )
        response_state_path = work_dir / OPENAI_BACKGROUND_STATE_FILE
        response_state = _load_json(response_state_path) or {}
        previous = response_state.get(action) if isinstance(response_state, dict) else None
        resume_response_id = ""
        if (
            isinstance(previous, dict)
            and previous.get("prompt_sha256") == prompt_digest
            and previous.get("status") in {"created", "polling"}
        ):
            resume_response_id = str(previous.get("response_id") or "").strip()

        def persist_background_response(response_id: str) -> None:
            latest = _load_json(response_state_path) or {}
            latest[action] = {
                "action": action,
                "prompt_sha256": prompt_digest,
                "response_id": response_id,
                "status": "polling",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            _atomic_write_json(response_state_path, latest)

        output, usage = _openai_writer_call_result(
            prompt,
            reasoning_effort=TRANSLATION_WRITER_REASONING_EFFORT,
            verbosity=TRANSLATION_WRITER_VERBOSITY,
            max_output_tokens=max_output_tokens,
            prompt_cache_key=f"classical:{PROMPT_VERSION}:{prompt_digest}",
            service_tier="default",
            background=True,
            background_timeout_seconds=OPENAI_BACKGROUND_TIMEOUT_SECONDS,
            background_poll_interval_seconds=5,
            background_response_id=resume_response_id or None,
            on_background_response_id=persist_background_response,
            retry_on_failure=False,
            tools=(
                [{"type": "web_search", "search_context_size": "medium"}]
                if enable_web_search
                else None
            ),
            text_format=(
                {
                    "type": "json_schema",
                    "name": "classical_translation_units",
                    "strict": True,
                    "schema": response_schema,
                }
                if response_schema
                else None
            ),
        )
        completed_response_id = str(usage.get("response_id") or resume_response_id or "").strip()
        completed_state = _load_json(response_state_path) or {}
        completed_state[action] = {
            "action": action,
            "prompt_sha256": prompt_digest,
            "response_id": completed_response_id,
            "status": "completed",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _atomic_write_json(response_state_path, completed_state)
        output = output.strip()
        if not output:
            raise legacy.PublicDomainQualityError(f"{provider}/{selected_model} 返回空译文")
        log.update(
            status="ok",
            output_chars=len(output),
            duration_ms=int((time.monotonic() - started) * 1000),
            usage_tokens=usage,
            response_id=str(usage.get("response_id") or ""),
            service_tier=str(usage.get("service_tier") or "default"),
            cost=None,
            currency="USD",
        )
        legacy._append_jsonl(work_dir / "llm_calls.jsonl", log)  # noqa: SLF001
        return output
    except Exception as exc:
        log.update(
            status="failed",
            duration_ms=int((time.monotonic() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}"[:2000],
            cost=None,
            currency="USD",
        )
        legacy._append_jsonl(work_dir / "llm_calls.jsonl", log)  # noqa: SLF001
        raise


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
    should_pause: Callable[[], bool] | None = None,
    prompt_overrides: dict[int, str] | None = None,
    only_segment: int | None = None,
) -> legacy.ChapterResult:
    writer_provider, writer_model, writer_reasoning_effort = _translation_writer_config()
    chapter_dir = work_dir / "classical_chapters"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    source_hash = legacy.text_sha256(section.text)
    base_artifact_key = _sha(
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
    normalized_overrides: dict[int, str] = {}
    for raw_key, raw_value in dict(prompt_overrides or {}).items():
        value = str(raw_value or "").strip()
        if not value:
            continue
        if str(raw_key).strip().lower() == "all":
            normalized_overrides[0] = value
            continue
        try:
            key = int(raw_key)
        except (TypeError, ValueError):
            continue
        if key >= 1:
            normalized_overrides[key] = value
    override_hash = _sha(
        json.dumps(normalized_overrides, ensure_ascii=False, sort_keys=True)
    ) if normalized_overrides else ""
    artifact_key = (
        _sha("|".join((base_artifact_key, "LOCAL_OVERRIDE", override_hash)))
        if normalized_overrides
        else base_artifact_key
    )
    cached_output_chars = int(
        ((cached or {}).get("chapter") or {}).get("audit", {}).get("output_chars") or 0
    ) if isinstance(cached, dict) else 0
    cached_audit = (
        dict(((cached or {}).get("chapter") or {}).get("audit") or {})
        if isinstance(cached, dict)
        else {}
    )
    cached_complete = (
        int(cached_audit.get("processed_source_chars") or 0)
        == legacy.count_text_chars(section.text)
        and int(cached_audit.get("segments") or 0)
        == int(cached_audit.get("available_segments") or -1)
        and int(cached_audit.get("prompt_injection_segments") or 0)
        == int(cached_audit.get("segments") or -1)
        and cached_audit.get("coverage_policy_version") == COVERAGE_POLICY_VERSION
        and int(cached_audit.get("source_units") or 0) > 0
        and int(cached_audit.get("translated_units") or 0)
        == int(cached_audit.get("source_units") or -1)
    )
    reusable_partial = (
        profile.scope_mode == SCOPE_CHARACTERS
        and preview_output_chars > 0
        and cached_output_chars >= preview_output_chars
    )
    if (
        cached
        and cached.get("artifact_key") == artifact_key
        and not normalized_overrides
        and ((not cached_is_partial and cached_complete) or reusable_partial)
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

    segment_limit = segment_limit_override or _adaptive_segment_limit(section.text)
    if preview_output_chars and segment_limit_override is None:
        # Smaller semantic segments keep a paid preview close to the selected
        # output size without truncating a model response mid-sentence.
        segment_limit = min(SEGMENT_CHARS, max(600, int(preview_output_chars / 1.35)))
    segments = _split_for_context(section.text, limit=segment_limit)
    outputs: list[str] = []
    segment_audits: list[dict[str, Any]] = []
    processed_source_chars = 0
    for segment_index, source_text in enumerate(segments, start=1):
        if should_pause is not None and should_pause():
            raise ClassicalTranslationPaused("古文白话翻译已暂停，已完成分段均已保存")
        effective_user_prompt = normalized_overrides.get(
            segment_index,
            normalized_overrides.get(0, user_prompt),
        )
        has_local_override = segment_index in normalized_overrides or 0 in normalized_overrides
        web_search_enabled = _prompt_requests_web_search(effective_user_prompt)
        base_segment_key = _sha(
            "|".join((base_artifact_key, str(segment_index), legacy.text_sha256(source_text)))
        )
        segment_key = (
            _sha("|".join((base_segment_key, "LOCAL_OVERRIDE", _prompt_hash(effective_user_prompt))))
            if has_local_override
            else base_segment_key
        )
        segment_label = (
            f"第 {segment_index}/{len(segments)} 段（同属原文章节《{section.title}》）"
            if len(segments) > 1
            else "本章完整内容"
        )
        source_chars = legacy.count_text_chars(source_text)
        marked_source, source_units = _build_marked_source(source_text)
        expected_markers = tuple(unit.marker for unit in source_units)
        source_units_digest = _source_units_digest(source_units)
        anchor_manifest = _build_anchor_manifest(marked_source)
        prompt, prompt_injection_mode = _render_external_user_prompt(
            effective_user_prompt,
            source_text=marked_source,
            work_title=work_title,
            chapter_title=section.title,
            segment_label=segment_label,
        )
        response_schema = _translation_response_schema(expected_markers)
        rendered_prompt_hash = _sha(prompt)
        translation_path = chapter_dir / f"{number:03d}_{segment_index:04d}_translation.md"
        translation_meta_path = chapter_dir / f"{number:03d}_{segment_index:04d}_translation.json"
        translation_meta = _load_json(translation_meta_path)
        targeted_repair_calls = 0
        repair_prompt_hash: str | None = None
        cached_translation = (
            translation_path.read_text(encoding="utf-8").strip()
            if translation_path.exists()
            else ""
        )
        if (
            translation_meta
            and translation_meta.get("segment_key") == segment_key
            and translation_meta.get("coverage_policy_version") == COVERAGE_POLICY_VERSION
            and translation_meta.get("source_units_digest") == source_units_digest
            and int(translation_meta.get("source_units") or 0) == len(source_units)
            and int(translation_meta.get("translated_units") or 0) == len(source_units)
            and bool(cached_translation)
            and translation_meta.get("translation_sha256")
            == legacy.text_sha256(cached_translation)
        ):
            final = cached_translation
            translated_units = int(translation_meta.get("translated_units") or 0)
            targeted_repair_calls = int(translation_meta.get("targeted_repair_calls") or 0)
            repair_prompt_hash = str(translation_meta.get("repair_prompt_hash") or "") or None
        elif only_segment is not None and segment_index != only_segment:
            raise legacy.PublicDomainValidationError(
                f"《{section.title}》第 {segment_index} 段尚无可复用译文，不能只重译第 {only_segment} 段"
            )
        else:
            raw_translation = _call_translation_writer(
                work_dir,
                action=f"classical_complete_translation:{number:04d}:{segment_index:04d}",
                prompt=prompt,
                temperature=0.10,
                max_output_tokens=_output_tokens(source_chars),
                # xhigh reasoning is fixed by this route.  There is no
                # artificial output cap; broad semantic windows preserve
                # context while sentence-unit markers protect completeness.
                thinking=True,
                retries=1,
                enable_web_search=web_search_enabled,
                response_schema=response_schema,
            )
            try:
                final, translated_units = _parse_structured_translation(
                    raw_translation,
                    expected_markers=expected_markers,
                    chapter_title=section.title,
                    source_units=source_units,
                )
                issues = _segment_issues(source_text=source_text, candidate=final)
            except legacy.PublicDomainQualityError as exc:
                issues = [str(exc)]
            if issues:
                raise legacy.PublicDomainQualityError(
                    f"《{section.title}》第 {segment_index} 段未通过完整翻译检查，"
                    f"已停止且不会自动重试：{'；'.join(issues)}"
                )
            _atomic_write_text(translation_path, final)
            _atomic_write_json(
                translation_meta_path,
                {
                    "segment_key": segment_key,
                    "prompt_hash": prompt_hash,
                    "effective_prompt_hash": _prompt_hash(effective_user_prompt),
                    "rendered_prompt_hash": rendered_prompt_hash,
                    "user_prompt_injected": True,
                    "user_prompt_chars": len(effective_user_prompt),
                    "prompt_injection_mode": prompt_injection_mode,
                    "web_search_tool_enabled": web_search_enabled,
                    "local_override": has_local_override,
                    "translated_units": translated_units,
                    "source_units": len(expected_markers),
                    "source_units_digest": source_units_digest,
                    "coverage_policy_version": COVERAGE_POLICY_VERSION,
                    "operation": "complete_translation_only",
                    "anchor_manifest": anchor_manifest,
                    "targeted_repair_calls": targeted_repair_calls,
                    "repair_prompt_hash": repair_prompt_hash,
                    "translation_sha256": legacy.text_sha256(final),
                    "writer_provider": writer_provider,
                    "writer_model": writer_model,
                    "writer_reasoning_effort": writer_reasoning_effort,
                },
            )

        issues = _segment_issues(source_text=source_text, candidate=final)
        if translated_units != len(source_units):
            issues.append(
                f"句群翻译覆盖不完整（原文 {len(source_units)} 单元，译文 {translated_units} 单元）"
            )
        audit = {
            "segment": segment_index,
            "source_chars": source_chars,
            "translation_chars": legacy.count_text_chars(final),
            "source_units": len(expected_markers),
            "translated_units": translated_units,
            "source_units_digest": source_units_digest,
            "coverage_policy_version": COVERAGE_POLICY_VERSION,
            "writer_provider": writer_provider,
            "writer_model": writer_model,
            "writer_reasoning_effort": writer_reasoning_effort,
            "translation_sha256": legacy.text_sha256(final),
            "prompt_hash": prompt_hash,
            "effective_prompt_hash": _prompt_hash(effective_user_prompt),
            "rendered_prompt_hash": rendered_prompt_hash,
            "repair_prompt_hash": repair_prompt_hash,
            "user_prompt_injected": True,
            "user_prompt_chars": len(effective_user_prompt),
            "prompt_injection_mode": prompt_injection_mode,
            "web_search_tool_enabled": web_search_enabled,
            "local_override": has_local_override,
            "targeted_repair_calls": targeted_repair_calls,
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
        if should_pause is not None and should_pause():
            raise ClassicalTranslationPaused("古文白话翻译已暂停，当前完整分段已保存")
        if preview_output_chars and sum(legacy.count_text_chars(value) for value in outputs) >= preview_output_chars:
            break

    text = "\n\n".join(value.strip() for value in outputs if value.strip()).strip()
    full_source_chars = legacy.count_text_chars(section.text)
    if profile.scope_mode != SCOPE_CHARACTERS:
        if len(segment_audits) != len(segments) or processed_source_chars != full_source_chars:
            raise legacy.PublicDomainQualityError(
                f"《{section.title}》章节翻译不完整："
                f"原文分段覆盖 {len(segment_audits)}/{len(segments)}，"
                f"原文字符覆盖 {processed_source_chars}/{full_source_chars}"
            )
        injected_segments = sum(
            1 for item in segment_audits if item.get("user_prompt_injected") is True
        )
        if injected_segments != len(segments):
            raise legacy.PublicDomainQualityError(
                f"《{section.title}》自定义 Prompt 注入不完整："
                f"{injected_segments}/{len(segments)} 个付费分段"
            )
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
        "segment_limit": segment_limit,
        "partial_section": len(segment_audits) < len(segments),
        "segment_audits": segment_audits,
        "rounds": 1,
        "operation": "complete_translation_only",
        "prompt_version": PROMPT_VERSION,
        "coverage_policy_version": COVERAGE_POLICY_VERSION,
        "source_units": sum(int(item.get("source_units") or 0) for item in segment_audits),
        "translated_units": sum(int(item.get("translated_units") or 0) for item in segment_audits),
        "prompt_hash": prompt_hash,
        "prompt_injection_segments": sum(
            1 for item in segment_audits if item.get("user_prompt_injected") is True
        ),
        "targeted_repair_calls": sum(
            int(item.get("targeted_repair_calls") or 0) for item in segment_audits
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


def split_source_segments(text: str, limit: int = SEGMENT_CHARS) -> list[str]:
    """Public, deterministic segment view used by the workbench."""

    return _split_for_context(text, limit=max(600, min(SEGMENT_CHARS, int(limit))))


def _adaptive_segment_limit(text: str) -> int:
    """Keep ordinary chapters whole and page only genuinely long chapters.

    ``SEGMENT_CHARS`` is a model-request safety ceiling, never a source/book
    truncation limit.  Balancing long chapters avoids a tiny tail page while
    preserving paragraph and sentence boundaries in ``_split_for_context``.
    """

    source_chars = legacy.count_text_chars(text)
    if source_chars <= SEGMENT_CHARS:
        return max(600, source_chars)
    page_count = max(2, math.ceil(source_chars / SEGMENT_CHARS))
    return max(600, min(SEGMENT_CHARS, math.ceil(source_chars / page_count)))


def retranslate_classical_selection(
    *,
    sources: Iterable[legacy.PublicDomainSource],
    work_dir: Path,
    target_title: str | None,
    adaptation_profile: dict[str, Any] | None,
    chapter_num: int,
    segment_num: int | None,
    custom_prompt: str,
    on_progress: legacy.ProgressCallback | None = None,
) -> legacy.ChapterResult:
    """Retranslate one completed chapter or one durable segment, preserving all others."""

    items = list(sources)
    sections = extract_source_sections(items)
    if chapter_num < 1 or chapter_num > len(sections):
        raise legacy.PublicDomainValidationError("要重译的章节不存在")
    local_prompt = str(custom_prompt or "").strip()
    if not local_prompt or len(local_prompt) > 30_000:
        raise legacy.PublicDomainValidationError("局部翻译 Prompt 必须为 1–30000 字")
    raw_profile = dict(adaptation_profile or {})
    profile = normalize_profile(raw_profile)
    checkpoint_profile_raw = raw_profile.get("_checkpoint_profile")
    checkpoint_profile = (
        normalize_profile(checkpoint_profile_raw)
        if isinstance(checkpoint_profile_raw, dict)
        else profile
    )
    artifact_profile_hash = _sha(
        json.dumps(asdict(checkpoint_profile), ensure_ascii=False, sort_keys=True)
    )
    segment_limit = int(raw_profile.get("_checkpoint_segment_limit") or SEGMENT_CHARS)
    saved_chapter = _load_json(work_dir / "classical_chapters" / f"{chapter_num:03d}.json")
    if isinstance(saved_chapter, dict):
        saved_audit = dict(((saved_chapter.get("chapter") or {}).get("audit") or {}))
        segment_limit = int(saved_audit.get("segment_limit") or segment_limit)
    section = sections[chapter_num - 1]
    segments = split_source_segments(section.text, segment_limit)
    if segment_num is not None and (segment_num < 1 or segment_num > len(segments)):
        raise legacy.PublicDomainValidationError("要重译的分段不存在")
    overrides = (
        {segment_num: local_prompt}
        if segment_num is not None
        else {0: local_prompt}
    )
    global_prompt = profile.custom_prompt
    return _translate_section(
        number=chapter_num,
        section=section,
        total_sections=len(sections),
        work_dir=work_dir,
        work_title=(target_title or items[0].title).strip(),
        profile=profile,
        artifact_profile_hash=artifact_profile_hash,
        segment_limit_override=max(600, min(SEGMENT_CHARS, segment_limit)),
        prompt_hash=_prompt_hash(global_prompt),
        user_prompt=global_prompt,
        previous_section_context=(sections[chapter_num - 2].text[-CONTEXT_CHARS:] if chapter_num > 1 else "无"),
        previous_chapter_translation_context="无",
        next_section_context=(sections[chapter_num].text[:CONTEXT_CHARS] if chapter_num < len(sections) else "无"),
        preview_output_chars=0,
        on_progress=on_progress,
        prompt_overrides=overrides,
        only_segment=segment_num,
    )


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
    """Compatibility wrapper returning the sentence-unit marked source."""

    marked, units = _build_marked_source(text)
    return marked, tuple(unit.marker for unit in units)


def _build_marked_source(text: str) -> tuple[str, tuple[TranslationSourceUnit, ...]]:
    """Mark bounded sentence groups, not only paragraphs.

    Classical books are commonly extracted as one enormous physical paragraph.
    Paragraph-only IDs therefore allowed a model to translate the beginning,
    keep the sole ID and pass a coarse whole-segment length check.  Every source
    sentence group now has its own durable ID and its own minimum-output gate.
    """

    raw_units: list[tuple[int, str]] = []
    paragraphs = [value.strip() for value in re.split(r"\n+", text) if value.strip()]
    for paragraph_index, paragraph in enumerate(paragraphs, start=1):
        for unit in _split_source_paragraph_units(paragraph):
            if unit.strip():
                raw_units.append((paragraph_index, unit.strip()))
    if not raw_units:
        raise legacy.PublicDomainQualityError("待翻译分段为空")

    units = tuple(
        TranslationSourceUnit(
            marker=f"[[P{index:04d}]]",
            text=unit_text,
            paragraph_index=paragraph_index,
            source_chars=legacy.count_text_chars(unit_text),
            source_sha256=legacy.text_sha256(unit_text),
        )
        for index, (paragraph_index, unit_text) in enumerate(raw_units, start=1)
    )
    marked = "\n\n".join(f"{unit.marker}\n{unit.text}" for unit in units)
    return marked, units


def _split_source_paragraph_units(text: str) -> list[str]:
    """Split one source paragraph into complete, bounded sentence groups."""

    primary = [value for value in re.split(r"(?<=[。！？；!?;])", text) if value]
    atomic: list[str] = []
    for sentence in primary:
        sentence = sentence.strip()
        if not sentence:
            continue
        if legacy.count_text_chars(sentence) <= SOURCE_UNIT_MAX_CHARS:
            atomic.append(sentence)
            continue
        atomic.extend(_split_oversized_source_unit(sentence))

    grouped: list[str] = []
    current = ""
    for item in atomic:
        combined = current + item
        if current and legacy.count_text_chars(combined) > SOURCE_UNIT_MAX_CHARS:
            grouped.append(current.strip())
            current = item
            continue
        current = combined
        if legacy.count_text_chars(current) >= SOURCE_UNIT_TARGET_CHARS:
            grouped.append(current.strip())
            current = ""
    if current.strip():
        if grouped and (
            legacy.count_text_chars(current) < 24
            and legacy.count_text_chars(grouped[-1] + current) <= SOURCE_UNIT_MAX_CHARS
        ):
            grouped[-1] = (grouped[-1] + current).strip()
        else:
            grouped.append(current.strip())
    return grouped


def _split_oversized_source_unit(text: str) -> list[str]:
    """Prefer clause boundaries, then hard-split only pathological unpunctuated text."""

    clauses = [value for value in re.split(r"(?<=[，、：,:])", text) if value]
    pieces: list[str] = []
    current = ""
    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue
        if legacy.count_text_chars(clause) > SOURCE_UNIT_MAX_CHARS:
            if current:
                pieces.append(current.strip())
                current = ""
            pieces.extend(_hard_split_by_text_chars(clause, SOURCE_UNIT_MAX_CHARS))
            continue
        if current and legacy.count_text_chars(current + clause) > SOURCE_UNIT_MAX_CHARS:
            pieces.append(current.strip())
            current = ""
        current += clause
    if current.strip():
        pieces.append(current.strip())
    return pieces


def _hard_split_by_text_chars(text: str, limit: int) -> list[str]:
    pieces: list[str] = []
    current: list[str] = []
    current_chars = 0
    for char in text:
        char_chars = legacy.count_text_chars(char)
        if current and current_chars + char_chars > limit:
            pieces.append("".join(current).strip())
            current = []
            current_chars = 0
        current.append(char)
        current_chars += char_chars
    if current:
        pieces.append("".join(current).strip())
    return [piece for piece in pieces if piece]


def _source_units_digest(units: tuple[TranslationSourceUnit, ...]) -> str:
    return _sha(
        "|".join(
            f"{unit.marker}:{unit.paragraph_index}:{unit.source_sha256}" for unit in units
        )
    )


def _build_anchor_manifest(marked_source: str) -> str:
    """Extract per-source-unit hard anchors that models commonly omit."""

    pattern = re.compile(r"(\[\[P\d{4}\]\])\s*(.*?)(?=\n\s*\[\[P\d{4}\]\]|\Z)", re.S)
    lines: list[str] = []
    for marker, body in pattern.findall(marked_source):
        number_matches = re.findall(r"\d+(?:[./:-]\d+)*", body)
        title_matches = re.findall(r"《[^》]{1,40}》", body)
        numbers = list(dict.fromkeys(number_matches))
        titles = list(dict.fromkeys(title_matches))
        parts: list[str] = []
        if numbers:
            parts.append("数字 " + "、".join(numbers[:24]))
        if titles:
            parts.append("书名/篇名 " + "、".join(titles[:24]))
        if parts:
            lines.append(f"{marker}：" + "；".join(parts))
    if not lines:
        return "无显式数字、书名或篇名锚点；仍须完整翻译全部信息。"
    return "\n".join(lines)


def _render_external_user_prompt(
    user_prompt: str,
    *,
    source_text: str,
    work_title: str,
    chapter_title: str,
    segment_label: str,
) -> tuple[str, str]:
    """Render only the user's prompt and the current source segment.

    The application contributes no semantic instruction, style rule, repair
    prompt, system prompt, or default prompt.  Explicit placeholders belong to
    the user's own prompt.  Without a placeholder the source is simply appended
    after the exact prompt text.
    """

    value = str(user_prompt or "").strip()
    if not value:
        raise legacy.PublicDomainValidationError("请输入本次古文白话翻译 Prompt；系统没有内置 Prompt。")
    replacements = {
        "{source_text}": source_text,
        "{work_title}": work_title,
        "{chapter_title}": chapter_title,
        "{segment_label}": segment_label,
    }
    used = [key for key in replacements if key in value]
    if used:
        rendered = value
        for key in used:
            rendered = rendered.replace(key, replacements[key])
        if "{source_text}" not in used:
            rendered = f"{rendered}\n\n{source_text}"
        return rendered.strip(), "explicit_placeholders"
    return f"{value}\n\n{source_text}".strip(), "exact_prompt_plus_source"


def _translation_response_schema(markers: tuple[str, ...]) -> dict[str, Any]:
    """Return a transport-only schema; it contains no writing instruction."""

    return {
        "type": "object",
        "properties": {
            "units": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "marker": {"type": "string", "enum": list(markers)},
                        "text": {"type": "string"},
                    },
                    "required": ["marker", "text"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["units"],
        "additionalProperties": False,
    }


def _parse_structured_translation(
    text: str,
    *,
    expected_markers: tuple[str, ...],
    chapter_title: str,
    source_units: tuple[TranslationSourceUnit, ...],
) -> tuple[str, int]:
    """Validate the structured transport envelope and rebuild paragraphs."""

    value = re.sub(r"^```(?:json)?\s*", "", str(text or "").strip(), flags=re.I)
    value = re.sub(r"\s*```$", "", value).strip()
    try:
        payload = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise legacy.PublicDomainQualityError(
            f"《{chapter_title}》模型返回不是有效的分句结构"
        ) from exc
    raw_units = payload.get("units") if isinstance(payload, dict) else None
    if not isinstance(raw_units, list):
        raise legacy.PublicDomainQualityError(f"《{chapter_title}》模型返回缺少 units")
    received = tuple(
        str(item.get("marker") or "") if isinstance(item, dict) else ""
        for item in raw_units
    )
    if received != expected_markers:
        raise legacy.PublicDomainQualityError(
            f"《{chapter_title}》译文句群编号不完整或顺序变化："
            f"应有 {len(expected_markers)} 个句群，实际识别 {len(received)} 个句群"
        )
    translated = [
        str(item.get("text") or "").strip()
        for item in raw_units
        if isinstance(item, dict)
    ]
    if len(translated) != len(source_units) or any(not item for item in translated):
        raise legacy.PublicDomainQualityError(f"《{chapter_title}》存在空译文句群")
    unit_issues = _translation_unit_issues(source_units, translated)
    if unit_issues:
        raise legacy.PublicDomainQualityError(
            f"《{chapter_title}》句群级完整性检查未通过：{'；'.join(unit_issues[:8])}"
        )
    paragraphs: list[str] = []
    current_paragraph = source_units[0].paragraph_index
    current_blocks: list[str] = []
    for unit, block in zip(source_units, translated, strict=True):
        if unit.paragraph_index != current_paragraph:
            paragraphs.append("".join(current_blocks).strip())
            current_blocks = []
            current_paragraph = unit.paragraph_index
        current_blocks.append(block)
    if current_blocks:
        paragraphs.append("".join(current_blocks).strip())
    return legacy.normalize_source_text("\n\n".join(paragraphs)), len(translated)


def _parse_marked_translation(
    text: str,
    *,
    expected_markers: tuple[str, ...],
    chapter_title: str,
    source_units: tuple[TranslationSourceUnit, ...] | None = None,
) -> tuple[str, int]:
    """Remove IDs only after proving every bounded source unit survived in order."""

    value = re.sub(r"^```(?:markdown|md|text)?\s*", "", (text or "").strip(), flags=re.I)
    value = re.sub(r"\s*```$", "", value).strip()
    pattern = re.compile(r"\[\[P\d{4}\]\]")
    matches = list(pattern.finditer(value))
    received = tuple(match.group(0) for match in matches)
    if received != expected_markers:
        raise legacy.PublicDomainQualityError(
            f"《{chapter_title}》译文句群编号不完整或顺序变化："
            f"应有 {len(expected_markers)} 个句群，实际识别 {len(received)} 个句群"
        )
    if not matches:
        raise legacy.PublicDomainQualityError(f"《{chapter_title}》译文没有句群编号")
    if value[: matches[0].start()].strip():
        raise legacy.PublicDomainQualityError(f"《{chapter_title}》译文在首个句群前新增了说明文字")

    translated: list[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(value)
        block = value[match.end() : end].strip()
        if not block:
            raise legacy.PublicDomainQualityError(
                f"《{chapter_title}》译文第 {index + 1} 个原文句群为空，疑似漏译"
            )
        translated.append(block)
    if source_units is not None:
        if len(source_units) != len(translated):
            raise legacy.PublicDomainQualityError(
                f"《{chapter_title}》句群覆盖数量不一致："
                f"原文 {len(source_units)} 单元，译文 {len(translated)} 单元"
            )
        unit_issues = _translation_unit_issues(source_units, translated)
        if unit_issues:
            raise legacy.PublicDomainQualityError(
                f"《{chapter_title}》句群级完整性检查未通过：{'；'.join(unit_issues[:8])}"
            )
        paragraphs: list[str] = []
        current_paragraph = source_units[0].paragraph_index
        current_blocks: list[str] = []
        for unit, block in zip(source_units, translated, strict=True):
            if unit.paragraph_index != current_paragraph:
                paragraphs.append("".join(current_blocks).strip())
                current_blocks = []
                current_paragraph = unit.paragraph_index
            current_blocks.append(block.strip())
        if current_blocks:
            paragraphs.append("".join(current_blocks).strip())
        normalized = legacy.normalize_source_text("\n\n".join(paragraphs))
    else:
        normalized = legacy.normalize_source_text("\n\n".join(translated))
    return normalized, len(translated)


def _translation_unit_issues(
    source_units: tuple[TranslationSourceUnit, ...],
    translated: list[str],
) -> list[str]:
    issues: list[str] = []
    for index, (source, candidate) in enumerate(zip(source_units, translated, strict=True), start=1):
        output_chars = legacy.count_text_chars(candidate)
        minimum_chars = _minimum_unit_output_chars(source.source_chars)
        if output_chars < minimum_chars:
            issues.append(
                f"P{index:04d} 译文过短（原文 {source.source_chars} 字，译文 {output_chars} 字，至少 {minimum_chars} 字）"
            )
            continue
        if output_chars > max(180, int(source.source_chars * 4.5)):
            issues.append(
                f"P{index:04d} 异常扩写（原文 {source.source_chars} 字，译文 {output_chars} 字）"
            )
        numbers = re.findall(r"\d+(?:[./:-]\d+)*", source.text)
        missing_numbers = [value for value in sorted(set(numbers)) if value not in candidate]
        if missing_numbers:
            issues.append(f"P{index:04d} 遗漏数字 {'、'.join(missing_numbers[:5])}")
        titles = re.findall(r"《[^》]{1,40}》", source.text)
        missing_titles = [value for value in sorted(set(titles)) if value not in candidate]
        if missing_titles:
            issues.append(f"P{index:04d} 遗漏篇名 {'、'.join(missing_titles[:3])}")
    return issues


def _minimum_unit_output_chars(source_chars: int) -> int:
    if source_chars <= 12:
        return max(2, int(source_chars * 0.35))
    if source_chars <= 40:
        return max(5, int(source_chars * 0.50))
    if source_chars <= 100:
        return max(18, int(source_chars * 0.62))
    return max(40, int(source_chars * 0.68))


def _segment_issues(*, source_text: str, candidate: str) -> list[str]:
    issues: list[str] = []
    source_chars = legacy.count_text_chars(source_text)
    output_chars = legacy.count_text_chars(candidate)
    if not candidate.strip():
        return ["模型返回空正文"]
    # Do not impose a 120-character absolute floor on a short source line.
    # Short classical headings, verse lines and terse dialogue can be fully
    # translated into fewer characters.  Sentence-unit IDs plus explicit
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


def _output_tokens(target_chars: int) -> int:
    # Zero is normalized to ``None`` by the shared OpenAI adapter, so the API
    # uses the model's own maximum rather than an application cap.  This is
    # essential for xhigh: reasoning tokens and visible translation tokens
    # share max_output_tokens.  Completeness is bounded by SEGMENT_CHARS and
    # enforced after the call by exact sentence-unit coverage checks.
    _ = target_chars
    return TRANSLATION_WRITER_MAX_OUTPUT_TOKENS


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
    return _sha(str(user_prompt or "").strip())


__all__ = [
    "ClassicalTranslationPaused",
    "ClassicalVernacularProfile",
    "POLICY_VERSION",
    "PROMPT_VERSION",
    "extract_source_sections",
    "get_default_user_prompt",
    "normalize_profile",
    "run_classical_vernacular_longform",
    "retranslate_classical_selection",
    "split_source_segments",
]
