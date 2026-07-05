"""Public-domain multi-source fusion rewrite service.

This module is intentionally isolated from the existing short-form and epic
novel workflows. It accepts two or three public-domain sources,
builds a fusion blueprint, generates a new manuscript, and then runs a local
hard QA gate for obvious source-overlap risk.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from app.services.deepseek import generate_text as deepseek_generate_text


MIN_SOURCE_CHARS = 300
MAX_SOURCE_CHARS = 240_000
MAX_PROMPT_SAMPLE_CHARS = 13_500
OVERLAP_WINDOW = 42
OVERLAP_STEP = 14
OVERLAP_RATIO_BLOCK = 0.12
OVERLAP_RATIO_WARN = 0.07
MAX_REPAIR_ISSUES = 8

_PUBLIC_DOMAIN_HINTS = (
    "公版",
    "公共领域",
    "public domain",
    "cc0",
)

_COPYRIGHT_MARKERS = (
    "版权所有",
    "版權所有",
    "copyright",
    "all rights reserved",
    "未经许可",
    "未經許可",
    "不得转载",
    "不得轉載",
    "侵权必究",
)

_AI_RESIDUE_PATTERNS = (
    "作为一个ai",
    "作为ai",
    "以下是",
    "我将",
    "任务要求",
    "输出要求",
    "prompt",
)


@dataclass(frozen=True)
class PublicDomainSource:
    title: str
    text: str
    author: str | None = None
    publication_year: int | None = None
    rights_note: str | None = None


@dataclass(frozen=True)
class SourceReport:
    title: str
    chars: int
    public_domain_basis: str
    sampled_chars: int


@dataclass(frozen=True)
class OverlapIssue:
    source_title: str
    matched_chunks: int
    total_chunks: int
    ratio: float
    samples: tuple[str, ...]


@dataclass(frozen=True)
class PublicDomainFusionResult:
    title: str
    manuscript: str
    blueprint: str
    quality_report: str
    source_reports: tuple[SourceReport, ...]
    warnings: tuple[str, ...]
    status: str


class PublicDomainRewriteError(ValueError):
    """Raised for validation failures before any LLM call is made."""


def rewrite_public_domain_fusion(
    *,
    sources: Iterable[PublicDomainSource],
    target_title: str | None = None,
    target_direction: str | None = None,
    target_length: int = 4000,
    output_style: str | None = None,
    public_domain_confirmed: bool = False,
) -> PublicDomainFusionResult:
    clean_sources = _validate_sources(
        list(sources),
        public_domain_confirmed=public_domain_confirmed,
    )
    target_length = max(800, min(int(target_length or 4000), 20_000))
    source_reports = tuple(_build_source_report(item) for item in clean_sources)
    source_briefs = "\n\n".join(_source_brief(item, idx + 1) for idx, item in enumerate(clean_sources))

    blueprint_prompt = _build_blueprint_prompt(
        source_briefs=source_briefs,
        target_title=target_title,
        target_direction=target_direction,
        target_length=target_length,
        output_style=output_style,
    )
    blueprint = deepseek_generate_text(
        blueprint_prompt,
        temperature=0.2,
        max_output_tokens=3600,
        system_instruction=(
            "你是严谨的公版图书融合改写策划编辑。只处理公版或公共领域文本，"
            "目标是抽象机制、主题与叙事结构，生成新的原创重构方案。"
        ),
    ).strip()
    if not blueprint:
        raise RuntimeError("DeepSeek returned an empty fusion blueprint")

    manuscript_prompt = _build_manuscript_prompt(
        blueprint=blueprint,
        source_briefs=source_briefs,
        target_title=target_title,
        target_direction=target_direction,
        target_length=target_length,
        output_style=output_style,
    )
    manuscript = deepseek_generate_text(
        manuscript_prompt,
        temperature=0.62,
        max_output_tokens=_tokens_for_target(target_length),
        system_instruction=(
            "你是成熟的中文长文作者。你只根据公版来源的抽象主题、人物机制、论证逻辑"
            "和叙事结构写新的稿件，禁止句子级拼接和复述。"
        ),
    ).strip()
    if not manuscript:
        raise RuntimeError("DeepSeek returned an empty manuscript")

    quality = _quality_check(manuscript, clean_sources)
    warnings = list(quality["warnings"])

    if quality["status"] == "needs_repair":
        repair_prompt = _build_repair_prompt(manuscript, quality["overlap_issues"], quality["residue_issues"])
        repaired = deepseek_generate_text(
            repair_prompt,
            temperature=0.25,
            max_output_tokens=_tokens_for_target(max(target_length, len(manuscript))),
            system_instruction=(
                "你是终稿硬伤修复编辑。只修复列出的硬伤，不改变文章主旨、段落顺序和文风。"
            ),
        ).strip()
        if repaired:
            repaired_quality = _quality_check(repaired, clean_sources)
            if repaired_quality["status"] != "needs_repair":
                manuscript = repaired
                quality = repaired_quality
                warnings.append("已触发一次高重叠/残留硬伤局部修复，并使用修复后的终稿。")
            else:
                warnings.append("已尝试一次局部修复，但仍命中高风险硬伤，建议人工复核或调整来源。")

    title = _pick_title(target_title, manuscript)
    report = _format_quality_report(quality, source_reports, len(manuscript))
    return PublicDomainFusionResult(
        title=title,
        manuscript=manuscript,
        blueprint=blueprint,
        quality_report=report,
        source_reports=source_reports,
        warnings=tuple(dict.fromkeys(warnings)),
        status=str(quality["status"]),
    )


def _validate_sources(
    sources: list[PublicDomainSource],
    *,
    public_domain_confirmed: bool,
) -> list[PublicDomainSource]:
    if len(sources) < 2 or len(sources) > 3:
        raise PublicDomainRewriteError("必须提供 2 到 3 本公版/公共领域来源书稿。")
    if not public_domain_confirmed:
        raise PublicDomainRewriteError("请先确认所有输入均为公版或公共领域文本。")

    cleaned: list[PublicDomainSource] = []
    for idx, item in enumerate(sources, start=1):
        title = (item.title or "").strip()
        text = _normalize_source_text(item.text)
        if not title:
            raise PublicDomainRewriteError(f"第 {idx} 个来源缺少书名。")
        if len(text) < MIN_SOURCE_CHARS:
            raise PublicDomainRewriteError(f"《{title}》正文过短，至少需要 {MIN_SOURCE_CHARS} 字。")
        if len(text) > MAX_SOURCE_CHARS:
            text = text[:MAX_SOURCE_CHARS]

        rights_note = (item.rights_note or "").strip()
        basis = _public_domain_basis(item, rights_note)
        if not basis:
            raise PublicDomainRewriteError(
                f"《{title}》缺少明确的公版/公共领域依据。请填写出版年份或来源说明。"
            )
        lowered_text = text.lower()
        if any(marker in lowered_text for marker in _COPYRIGHT_MARKERS):
            raise PublicDomainRewriteError(
                f"《{title}》正文里检测到版权限制提示，请确认来源后再使用。"
            )
        cleaned.append(
            PublicDomainSource(
                title=title,
                author=(item.author or "").strip() or None,
                publication_year=item.publication_year,
                rights_note=rights_note or None,
                text=text,
            )
        )
    return cleaned


def _public_domain_basis(item: PublicDomainSource, rights_note: str) -> str:
    note = rights_note.lower()
    if any(hint in note for hint in _PUBLIC_DOMAIN_HINTS):
        return rights_note.strip()
    if item.publication_year and int(item.publication_year) <= 1930:
        return f"出版年份 {int(item.publication_year)}，按公版候选处理"
    return ""


def _build_source_report(item: PublicDomainSource) -> SourceReport:
    return SourceReport(
        title=item.title,
        chars=len(item.text or ""),
        public_domain_basis=_public_domain_basis(item, item.rights_note or ""),
        sampled_chars=len(_sample_source(item.text)),
    )


def _normalize_source_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    collapsed: list[str] = []
    blank_seen = False
    for line in lines:
        if not line:
            if not blank_seen:
                collapsed.append("")
            blank_seen = True
            continue
        collapsed.append(line)
        blank_seen = False
    return "\n".join(collapsed).strip()


def _source_brief(item: PublicDomainSource, index: int) -> str:
    meta = [
        f"来源{index}：《{item.title}》",
        f"作者：{item.author or '未填写'}",
        f"出版年份/依据：{item.publication_year or '未填写'}；{item.rights_note or '未填写'}",
        f"总字数：{len(item.text)}",
    ]
    headings = _extract_heading_candidates(item.text)
    if headings:
        meta.append("可能的章节/段落线索：" + "；".join(headings[:24]))
    sample = _sample_source(item.text)
    return "\n".join(meta) + "\n节选样本：\n" + sample


def _extract_heading_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if len(line) <= 28 and not line.endswith(("。", "，", "；", "：", ".", ",")):
            candidates.append(line)
        elif re.match(r"^(第[一二三四五六七八九十百千万\d]+[章节回篇]|[一二三四五六七八九十]+、|\d+[.、])", line):
            candidates.append(line[:36])
        if len(candidates) >= 40:
            break
    return candidates


def _sample_source(text: str) -> str:
    text = _normalize_source_text(text)
    if len(text) <= MAX_PROMPT_SAMPLE_CHARS:
        return text
    head = text[:5400]
    middle_start = max(0, len(text) // 2 - 2200)
    middle = text[middle_start : middle_start + 4400]
    tail = text[-3700:]
    return "\n\n[开篇样本]\n" + head + "\n\n[中段样本]\n" + middle + "\n\n[结尾样本]\n" + tail


def _build_blueprint_prompt(
    *,
    source_briefs: str,
    target_title: str | None,
    target_direction: str | None,
    target_length: int,
    output_style: str | None,
) -> str:
    return f"""
请基于下列 2-3 本公版/公共领域来源，设计一篇新的中文融合改写稿蓝图。

目标标题：{target_title or "请自行拟定"}
目标方向：{target_direction or "从来源中提炼共同问题、冲突、人物处境和思想价值，写成新的完整稿件"}
目标长度：约 {target_length} 字，可按表达完整度自然浮动
风格倾向：{output_style or "自然、连贯、有知识密度，有明确新结构，不像机械翻译或资料拼贴"}

硬性规则：
1. 只能吸收公版来源中的主题、逻辑、人物机制、叙事功能和思想资源。
2. 不得照搬任何来源的连续句子、段落、固定场景顺序、独特表达和专名组合。
3. 必须让 2-3 个来源都进入方案，各自承担不同功能，不能只改写其中一本。
4. 方案必须有新的中心问题、新的结构顺序和新的表达角度。
5. 不要写成版权规避，不要写“洗稿”，只写原创融合重构蓝图。

请按以下格式输出：
@拟题
@核心问题
@来源吸收表
@创新重构策略
@章节结构
@风险规避

来源资料如下：
{source_briefs}
""".strip()


def _build_manuscript_prompt(
    *,
    blueprint: str,
    source_briefs: str,
    target_title: str | None,
    target_direction: str | None,
    target_length: int,
    output_style: str | None,
) -> str:
    return f"""
根据“融合蓝图”写出一篇新的中文成稿。

成稿要求：
- 这是一篇公版图书融合改写稿，不是逐句翻译，不是复述，不是资料拼贴。
- 必须把 2-3 个来源的思想、故事机制或论证逻辑融会贯通，形成新的完整表达。
- 可以保留公版作品中通用的思想资源和公共文化常识，但不能连续复刻来源句子。
- 不要出现“本文/本稿/以下/作为AI/根据要求/参考来源”等说明语。
- 不要输出蓝图、分析过程或版权声明，只输出成稿。
- 段落要舒展，表达要自然，结构清楚，读起来像一篇真正完成的文章。
- 目标长度约 {target_length} 字，优先保证表达完整，不为了凑字硬扩写。

目标标题：{target_title or "请根据成稿自然拟题"}
目标方向：{target_direction or "融合来源的核心矛盾和思想价值，写成新的完整稿件"}
风格倾向：{output_style or "自然、凝练、有文学和思想密度"}

融合蓝图：
{blueprint}

来源摘要与样本，仅用于理解公共领域素材，禁止复制原句：
{source_briefs}
""".strip()


def _build_repair_prompt(
    manuscript: str,
    overlap_issues: list[OverlapIssue],
    residue_issues: list[str],
) -> str:
    issue_lines: list[str] = []
    for issue in overlap_issues[:MAX_REPAIR_ISSUES]:
        samples = " / ".join(issue.samples[:3])
        issue_lines.append(
            f"- 与《{issue.source_title}》重叠偏高，命中率 {issue.ratio:.1%}，样本：{samples}"
        )
    for item in residue_issues[:MAX_REPAIR_ISSUES]:
        issue_lines.append(f"- 输出残留：{item}")
    return f"""
下面是一篇已经完成的稿件，但终稿 QA 命中了少量硬伤。

请只做硬伤修复：
1. 对重叠偏高的表达做局部改写，换成新的句式、节奏和叙述角度。
2. 删除 AI 说明语、任务说明、格式残留。
3. 不改变文章主旨、章节顺序、段落大意和整体文风。
4. 不扩写、不缩写，不重新生成另一篇。
5. 只输出修复后的完整稿件。

命中问题：
{chr(10).join(issue_lines) or "- 未列出具体样本，请整体检查明显硬伤。"}

待修稿件：
{manuscript}
""".strip()


def _quality_check(manuscript: str, sources: list[PublicDomainSource]) -> dict[str, object]:
    warnings: list[str] = []
    overlap_issues: list[OverlapIssue] = []
    residue_issues: list[str] = []

    for item in sources:
        issue = _overlap_issue(manuscript, item)
        if issue and issue.ratio >= OVERLAP_RATIO_WARN:
            overlap_issues.append(issue)
            warnings.append(f"与《{item.title}》存在较高连续短句重合：{issue.ratio:.1%}")

    lowered = manuscript.lower()
    for pattern in _AI_RESIDUE_PATTERNS:
        if pattern in lowered:
            residue_issues.append(pattern)
            warnings.append(f"疑似输出残留：{pattern}")

    status = "ok"
    if any(item.ratio >= OVERLAP_RATIO_BLOCK for item in overlap_issues) or residue_issues:
        status = "needs_repair"
    elif overlap_issues:
        status = "needs_review"

    return {
        "status": status,
        "warnings": warnings,
        "overlap_issues": overlap_issues,
        "residue_issues": residue_issues,
    }


def _overlap_issue(manuscript: str, source: PublicDomainSource) -> OverlapIssue | None:
    generated = _compact_for_overlap(manuscript)
    source_text = _compact_for_overlap(source.text)
    if len(generated) < OVERLAP_WINDOW or len(source_text) < OVERLAP_WINDOW:
        return None
    chunks = [
        generated[pos : pos + OVERLAP_WINDOW]
        for pos in range(0, max(1, len(generated) - OVERLAP_WINDOW + 1), OVERLAP_STEP)
    ]
    if not chunks:
        return None
    hits: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        if chunk in source_text and chunk not in seen:
            hits.append(chunk)
            seen.add(chunk)
    ratio = len(hits) / max(1, len(chunks))
    if ratio < OVERLAP_RATIO_WARN:
        return None
    return OverlapIssue(
        source_title=source.title,
        matched_chunks=len(hits),
        total_chunks=len(chunks),
        ratio=ratio,
        samples=tuple(_restore_sample_spacing(item) for item in hits[:5]),
    )


def _compact_for_overlap(text: str) -> str:
    return re.sub(r"[\s，。、“”‘’：；！？,.!?;:()\[\]（）《》<>【】\-—_]+", "", text or "").lower()


def _restore_sample_spacing(text: str) -> str:
    return text[:80]


def _format_quality_report(
    quality: dict[str, object],
    source_reports: tuple[SourceReport, ...],
    manuscript_chars: int,
) -> str:
    lines = [
        f"状态：{quality['status']}",
        f"成稿字数：{manuscript_chars}",
        f"来源数量：{len(source_reports)}",
        "来源检查：",
    ]
    for item in source_reports:
        lines.append(
            f"- 《{item.title}》：{item.chars} 字；依据：{item.public_domain_basis}；送入模型样本 {item.sampled_chars} 字"
        )
    overlap_issues = quality.get("overlap_issues") or []
    if overlap_issues:
        lines.append("重叠率检查：")
        for issue in overlap_issues:
            assert isinstance(issue, OverlapIssue)
            lines.append(
                f"- 《{issue.source_title}》：{issue.matched_chunks}/{issue.total_chunks}，约 {issue.ratio:.1%}"
            )
    warnings = quality.get("warnings") or []
    if warnings:
        lines.append("提示：")
        for item in warnings:
            lines.append(f"- {item}")
    else:
        lines.append("提示：未命中高置信硬伤。")
    return "\n".join(lines)


def _pick_title(target_title: str | None, manuscript: str) -> str:
    if target_title and target_title.strip():
        return target_title.strip()
    for line in manuscript.splitlines():
        clean = line.strip().lstrip("#").strip()
        if clean and len(clean) <= 40:
            return clean
    return "公版融合改写稿"


def _tokens_for_target(target_length: int) -> int:
    return max(1800, min(12_000, int(target_length * 1.8)))
