"""Serial two-pass vernacular adaptation for public-domain classical fiction.

This route is intentionally isolated from public-domain multi-source fusion.
It preserves source order and chapter boundaries, then executes the exact
two-pass author/editor prompt supplied for classical-to-vernacular rewriting.
Every segment is checkpointed, and no automatic paid retry or third model
review is launched when deterministic checks fail.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable

from app.services import public_domain_longform as legacy


POLICY_VERSION = "public-domain-classical-vernacular-v1"
PROMPT_VERSION = "classical-vernacular-exact-two-pass-v1"
PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts" / "public_domain_rewrite"
ROUND1_PROMPT = PROMPT_DIR / "classical_vernacular_round1.md"
ROUND2_PROMPT = PROMPT_DIR / "classical_vernacular_round2.md"

MAX_SECTION_COUNT = 200
SEGMENT_CHARS = 7_000
MIN_OUTPUT_RATIO = 0.45
MAX_OUTPUT_RATIO = 2.80

_CHAPTER_HEADING_RE = re.compile(
    r"^(?:"
    r"第\s*[一二三四五六七八九十百千零〇两\d]+\s*[回章节篇部卷折出]"
    r"|[卷部篇]\s*[一二三四五六七八九十百千零〇两\d]+"
    r"|楔子|引子|序章|序言|前言|后记|尾声"
    r")[^\n]{0,100}$"
)
_META_RESIDUE = (
    "以下是改编内容",
    "以下为改编",
    "改编说明",
    "修改说明",
    "作为一个AI",
    "作为AI",
    "根据您的要求",
    "本次改编",
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
    target_audience: str = "普通大众读者、手机阅读用户"
    vernacular_level: str = "通俗易读，但不过度口语化"
    adaptation_intensity: str = "中度改编"
    web_fiction_ratio: str = "约15%"
    language_tendency: str = "通俗自然，兼具叙事感和适度文学质感"
    must_preserve: str = "全部主要人物、人物关系、时代背景、关键事件、因果、线索、结果与原有章节顺序"
    focus_issues: str = "翻译腔、对白生硬、叙述拖沓、人物扁平、场景不足、AI腔、说明腔"
    special_requirements: str = "不得提前续写后续剧情；不得新增重要人物、事件、感情线、反派或支线"


@dataclass(frozen=True)
class SourceSection:
    source_id: str
    source_title: str
    title: str
    text: str


def normalize_profile(value: dict[str, Any] | None) -> ClassicalVernacularProfile:
    raw = dict(value or {})
    defaults = ClassicalVernacularProfile()
    allowed = set(asdict(defaults))
    cleaned: dict[str, str] = {}
    for key in allowed:
        candidate = str(raw.get(key) or getattr(defaults, key)).strip()
        cleaned[key] = candidate[:3000]
    return ClassicalVernacularProfile(**cleaned)


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
    """Adapt all supplied public-domain classical text in source order."""

    items = list(sources)
    profile = normalize_profile(adaptation_profile)
    prompt_hash = _prompt_hash()
    sections = extract_source_sections(items)
    if not sections:
        raise legacy.PublicDomainValidationError("没有识别到可改编的公版古文正文。")
    if len(sections) > MAX_SECTION_COUNT:
        raise legacy.PublicDomainValidationError(
            f"识别到 {len(sections)} 个原文章节，超过单任务 {MAX_SECTION_COUNT} 章上限；请按卷拆分任务。"
        )

    plan = {
        "policy_version": POLICY_VERSION,
        "prompt_version": PROMPT_VERSION,
        "prompt_hash": prompt_hash,
        "title": (target_title or items[0].title).strip(),
        "chapter_count": len(sections),
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
    _atomic_write_json(work_dir / "01_classical_vernacular_plan.json", plan)
    _notify(on_progress, 8, f"已按原文顺序识别 {len(sections)} 个章节，开始两轮白话改编")

    chapters: list[legacy.ChapterResult] = []
    for index, section in enumerate(sections, start=1):
        chapter = _adapt_section(
            number=index,
            section=section,
            total_sections=len(sections),
            work_dir=work_dir,
            work_title=plan["title"],
            profile=profile,
            prompt_hash=prompt_hash,
        )
        chapters.append(chapter)
        if on_chapter_completed is not None:
            on_chapter_completed(chapter)
        progress = 8 + int(84 * index / len(sections))
        _notify(on_progress, progress, f"古文白话改编 {index}/{len(sections)} 章已完成并落盘")

    body_chars = sum(legacy.count_text_chars(item.text) for item in chapters)
    source_chars = sum(legacy.count_text_chars(item.text) for item in items)
    blocking: list[str] = []
    if len(chapters) != len(sections):
        blocking.append("最终章节数量与原文章节数量不一致")
    if body_chars < int(source_chars * MIN_OUTPUT_RATIO):
        blocking.append(
            f"全稿白话正文仅 {body_chars} 字，低于原文 {source_chars} 字的安全下限"
        )
    quality_report = {
        "status": "needs_review" if blocking else "ok",
        "policy_version": POLICY_VERSION,
        "prompt_version": PROMPT_VERSION,
        "prompt_hash": prompt_hash,
        "pipeline": "source_order -> round1_adaptation -> round2_editorial_rewrite -> local_qa -> bookshelf",
        "source_chapters": len(sections),
        "output_chapters": len(chapters),
        "source_chars": source_chars,
        "output_chars": body_chars,
        "paid_calls_per_segment": 2,
        "automatic_retry_calls": 0,
        "blocking_issues": blocking,
        "chapter_audits": [item.audit for item in chapters],
    }
    _atomic_write_json(work_dir / "quality_report.json", quality_report)
    if blocking:
        raise legacy.PublicDomainQualityError("；".join(blocking))

    title = str(plan["title"] or items[0].title).strip()
    summary = (
        f"按公版原作的章节顺序，对《{items[0].title}》进行现代白话改编。"
        "保留主要人物、关系、关键事件、因果、时代背景与故事结局。"
    )
    markdown = legacy.render_final_markdown(title=title, summary=summary, chapters=chapters)
    _atomic_write_text(work_dir / "04_final.md", markdown)
    _notify(on_progress, 94, "两轮改编与整书本地检查完成，正在写入书架")
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
        heading_indexes = [
            index for index, line in enumerate(lines) if _CHAPTER_HEADING_RE.fullmatch(line.strip())
        ]
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
        preface = "\n".join(lines[: heading_indexes[0]]).strip()
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


def _adapt_section(
    *,
    number: int,
    section: SourceSection,
    total_sections: int,
    work_dir: Path,
    work_title: str,
    profile: ClassicalVernacularProfile,
    prompt_hash: str,
) -> legacy.ChapterResult:
    chapter_dir = work_dir / "classical_chapters"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    profile_hash = _sha(json.dumps(asdict(profile), ensure_ascii=False, sort_keys=True))
    source_hash = legacy.text_sha256(section.text)
    artifact_key = _sha(
        "|".join(
            (
                POLICY_VERSION,
                PROMPT_VERSION,
                prompt_hash,
                profile_hash,
                source_hash,
                section.title,
            )
        )
    )
    final_path = chapter_dir / f"{number:03d}.json"
    cached = _load_json(final_path)
    if cached and cached.get("artifact_key") == artifact_key:
        payload = dict(cached.get("chapter") or {})
        return legacy.ChapterResult(
            number=number,
            title=section.title,
            text=str(payload.get("text") or ""),
            summary=str(payload.get("summary") or ""),
            used_claim_ids=(section.source_id,),
            audit=dict(payload.get("audit") or {}),
        )

    segments = _split_for_context(section.text)
    outputs: list[str] = []
    segment_audits: list[dict[str, Any]] = []
    for segment_index, source_text in enumerate(segments, start=1):
        segment_key = _sha(
            "|".join((artifact_key, str(segment_index), legacy.text_sha256(source_text)))
        )
        segment_label = (
            f"第 {segment_index}/{len(segments)} 段（同属原文章节《{section.title}》，不得重复标题）"
            if len(segments) > 1
            else "本章完整内容"
        )
        source_chars = legacy.count_text_chars(source_text)
        target_chars = max(500, int(source_chars * 1.18))
        common = {
            **asdict(profile),
            "work_title": work_title,
            "chapter_title": section.title,
            "segment_label": segment_label,
            "target_chars": target_chars,
            "source_text": source_text,
        }
        round1_path = chapter_dir / f"{number:03d}_{segment_index:02d}_round1.md"
        round1_meta_path = chapter_dir / f"{number:03d}_{segment_index:02d}_round1.json"
        round1_meta = _load_json(round1_meta_path)
        if (
            round1_meta
            and round1_meta.get("segment_key") == segment_key
            and round1_path.exists()
        ):
            round1 = round1_path.read_text(encoding="utf-8").strip()
        else:
            round1_prompt = _render(ROUND1_PROMPT, **common)
            round1 = legacy._call_llm(  # noqa: SLF001
                work_dir,
                action=f"classical_vernacular_round1:{number:03d}:{segment_index:02d}",
                prompt=round1_prompt,
                temperature=0.50,
                max_output_tokens=_output_tokens(target_chars),
            )
            round1 = _clean_model_text(round1, section.title)
            if not round1:
                raise legacy.PublicDomainQualityError(
                    f"《{section.title}》第 {segment_index} 段第一轮返回空正文"
                )
            _atomic_write_text(round1_path, round1)
            _atomic_write_json(
                round1_meta_path,
                {
                    "segment_key": segment_key,
                    "prompt_hash": prompt_hash,
                    "round": 1,
                },
            )

        round2_prompt = _render(ROUND2_PROMPT, **common, round1_text=round1)
        round2 = legacy._call_llm(  # noqa: SLF001
            work_dir,
            action=f"classical_vernacular_round2:{number:03d}:{segment_index:02d}",
            prompt=round2_prompt,
            temperature=0.34,
            max_output_tokens=_output_tokens(target_chars),
        )
        final = _clean_model_text(round2, section.title)
        issues = _segment_issues(source_text=source_text, candidate=final)
        audit = {
            "segment": segment_index,
            "source_chars": source_chars,
            "round1_chars": legacy.count_text_chars(round1),
            "final_chars": legacy.count_text_chars(final),
            "issues": issues,
        }
        segment_audits.append(audit)
        _atomic_write_json(
            chapter_dir / f"{number:03d}_{segment_index:02d}_audit.json", audit
        )
        if issues:
            raise legacy.PublicDomainQualityError(
                f"《{section.title}》第 {segment_index} 段未通过古文白话本地检查：{'；'.join(issues)}"
            )
        _atomic_write_text(
            chapter_dir / f"{number:03d}_{segment_index:02d}_round2.md", final
        )
        outputs.append(final)

    text = "\n\n".join(value.strip() for value in outputs if value.strip()).strip()
    chapter_audit = {
        "number": number,
        "title": section.title,
        "source_id": section.source_id,
        "source_title": section.source_title,
        "source_sha256": source_hash,
        "source_chars": legacy.count_text_chars(section.text),
        "output_chars": legacy.count_text_chars(text),
        "segments": len(segments),
        "segment_audits": segment_audits,
        "rounds": 2,
        "prompt_version": PROMPT_VERSION,
        "prompt_hash": prompt_hash,
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


def _segment_issues(*, source_text: str, candidate: str) -> list[str]:
    issues: list[str] = []
    source_chars = legacy.count_text_chars(source_text)
    output_chars = legacy.count_text_chars(candidate)
    if not candidate.strip():
        return ["模型返回空正文"]
    if output_chars < max(120, int(source_chars * MIN_OUTPUT_RATIO)):
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
    for key, value in values.items():
        template = template.replace("{" + key + "}", str(value))
    unresolved = sorted(set(re.findall(r"\{[a-z_]+\}", template)))
    if unresolved:
        raise legacy.PublicDomainValidationError(
            f"古文白话 Prompt 仍有未填字段：{', '.join(unresolved)}"
        )
    return template.strip()


def _output_tokens(target_chars: int) -> int:
    return max(5_000, min(14_000, int(target_chars * 2.0)))


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


def _prompt_hash() -> str:
    return _sha(
        ROUND1_PROMPT.read_text(encoding="utf-8")
        + "\n---ROUND-BOUNDARY---\n"
        + ROUND2_PROMPT.read_text(encoding="utf-8")
    )


__all__ = [
    "ClassicalVernacularProfile",
    "POLICY_VERSION",
    "PROMPT_VERSION",
    "extract_source_sections",
    "normalize_profile",
    "run_classical_vernacular_longform",
]
