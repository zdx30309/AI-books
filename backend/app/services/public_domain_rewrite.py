"""Public-domain rewrite/fusion service.

This module is intentionally isolated from the existing short-form and epic
novel workflows. It accepts one to three public-domain sources, builds a
substance-preserving rewrite/fusion blueprint, generates a new manuscript, and
then runs a local hard QA gate for obvious source-overlap risk.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re
from typing import Iterable

from app.core.config import settings
from app.services.aigc_quality_gate import (
    enforce_aigc_quality_gate,
    rewrite_aigc_chunk_with_gemini,
)
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
    target_length: int | None = None,
    output_style: str | None = None,
    public_domain_confirmed: bool = False,
) -> PublicDomainFusionResult:
    clean_sources = _validate_sources(
        list(sources),
        public_domain_confirmed=public_domain_confirmed,
    )
    target_length = _auto_target_length(clean_sources, target_length)
    source_reports = tuple(_build_source_report(item) for item in clean_sources)
    source_briefs = "\n\n".join(_source_brief(item, idx + 1) for idx, item in enumerate(clean_sources))
    source_count = len(clean_sources)

    blueprint_prompt = _build_blueprint_prompt(
        source_briefs=source_briefs,
        source_count=source_count,
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
            "目标是保持来源实质不变，只设计表达换形、结构更顺、语言更新的改写方案。"
        ),
    ).strip()
    if not blueprint:
        raise RuntimeError("DeepSeek returned an empty fusion blueprint")

    manuscript_prompt = _build_manuscript_prompt(
        blueprint=blueprint,
        source_briefs=source_briefs,
        source_count=source_count,
        target_title=target_title,
        target_direction=target_direction,
        target_length=target_length,
        output_style=output_style,
    )
    manuscript = deepseek_generate_text(
        manuscript_prompt,
        temperature=0.45,
        max_output_tokens=_tokens_for_target(target_length),
        system_instruction=(
            "你是成熟的中文公版改写作者。你的任务是换表达、不换实质：保留来源的"
            "人物、事件、顺序、观点、情绪和结论，禁止另写成一篇新故事。"
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
                "你是终稿硬伤修复编辑。只修复列出的连续重合或输出残留；必须保持原稿实质、"
                "人物、事件顺序、事实、观点和结论不变。"
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

    if settings.AIGC_GATE_ENABLED:
        gate_result = asyncio.run(
            enforce_aigc_quality_gate(
                manuscript,
                category="public_domain_rewrite",
                rewrite_chunk=rewrite_aigc_chunk_with_gemini,
            )
        )
        manuscript = gate_result.text
        warnings.append(
            f"终稿已通过朱雀 AIGC 闸门（最高分 {gate_result.max_score}%，"
            f"检测轮次 {len(gate_result.rounds)}）。"
        )
        quality = _quality_check(manuscript, clean_sources)
        if quality["status"] == "needs_repair":
            raise RuntimeError("降低 AI 率后的公版终稿未通过来源重合/残留复检")

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
    if len(sources) < 1 or len(sources) > 3:
        raise PublicDomainRewriteError("必须提供 1 到 3 个公版/公共领域来源书稿。")
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
        public_domain_basis=_public_domain_basis(item, item.rights_note or "") or "用户确认公版/公共领域",
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
    source_count: int,
    target_title: str | None,
    target_direction: str | None,
    target_length: int,
    output_style: str | None,
) -> str:
    source_mode = (
        "当前只有 1 个来源：这是单本/单篇公版同质改写，不要假装有多本融合。必须保留原文的主要人物、场景、事件顺序、论证链、情绪变化和结论，只在表达形式上换形。"
        if source_count == 1
        else "当前有 2-3 个来源：这是多来源公版同质编排。必须让每个来源的核心事实、观点、人物处境和结论都进入方案；融合只做编排和衔接，不得抽象成另一个故事。"
    )
    return f"""
来源数量：{source_count}
来源模式：{source_mode}
请基于下列 1-3 个公版/公共领域来源，设计一份“实质不变、表达换形”的中文改写蓝图。

目标标题：{target_title or "请自动拟定，标题必须服务于来源原本的核心内容"}
目标方向：{target_direction or "保留来源的核心事实、故事推进、论证顺序和情绪线，只改变语言形式、段落节奏、句式和叙述角度"}
篇幅原则：不设固定字数，优先把来源的关键事实、情节、观点和结论讲完整；系统为你预留约 {target_length} 字输出空间
风格倾向：{output_style or "自然、通顺、可读性强，但实质内容和来源保持一致"}

硬性规则：
1. 必须保留来源的主要事实、人物关系、事件顺序、论证链、情绪线、结论或寓意，不得换成另一个故事或观点。
2. 可以改变句式、词语、段落衔接、叙述角度和表达密度，但不得连续复刻来源句子。
3. 单来源时按原文实质顺序做同源改写；多来源时只做同主题编排和衔接，不得牺牲任何来源的关键信息。
4. 禁止另起中心问题、改换主角、改换冲突、改写结局、增删关键事实、把具体材料抽象成另一篇原创稿。
5. 蓝图必须列出“必须保留的信息点”和“只能改变的表达层”，让后续成稿知道哪里绝对不能动。
6. 不要写成版权规避，不要写“洗稿”，只输出可执行的公版改写蓝图。

请按以下格式输出：
@拟题
@来源实质保留表
@必须保留的信息点
@换形不换质策略
@改写结构
@风险规避

来源资料如下：
{source_briefs}
""".strip()


def _build_manuscript_prompt(
    *,
    blueprint: str,
    source_briefs: str,
    source_count: int,
    target_title: str | None,
    target_direction: str | None,
    target_length: int,
    output_style: str | None,
) -> str:
    source_mode = (
        "当前是单来源同稿改写：输出必须仍然是这一本书、这一章、这一段情节或论述。原稿的人物、场景、事件顺序、动作、对白、因果、观点和结论都不能变，只允许语言表达方式变化。"
        if source_count == 1
        else "当前是多来源同质编排：输出必须仍然来自这 2-3 个公版来源本身。每个来源的关键情节、事实、观点和结论都要保留，融合只负责排序、衔接和语言换形，不能创作另一本书。"
    )
    return f"""
来源数量：{source_count}
来源模式：{source_mode}
根据“改写蓝图”写出一篇同稿改写成稿。

成稿要求：
- 这是公版图书同稿改写：原稿内容不变，只改变表达形式；小说放进来，输出仍然必须是那本小说，不得写成另一部小说。
- {source_mode}
- 必须逐段吃透来源，把同一件事、同一个场面、同一条论证，用新的句法、段落节奏和叙述口吻说出来。
- 可以调整语序、拆并句、替换词语、改换承接方式、让表达更自然；不能新增人物、地点、事件、冲突、支线、结局、观点和价值判断。
- 不能删掉原稿关键内容，不能把原稿压成摘要，不能跳过中段，不能把具体情节抽象成主题文章。
- 不能连续复刻来源句子；遇到必须保留的专名、人物名、地点名、章节名，可以保留。
- 不要出现“本文/本稿/以下/作为AI/根据要求/参考来源”等说明语。
- 不要输出蓝图、分析过程或版权声明，只输出成稿。
- 段落要舒展，表达要自然，结构清楚，读起来像一篇真正完成的文章。
- 不设固定字数，优先保证表达完整；系统为你预留约 {target_length} 字输出空间，不要为了短而压缩核心内容，也不要为了长而空转。

目标标题：{target_title or "请根据成稿自然拟题"}
目标方向：{target_direction or "保持原稿实质不变，只让文字更自然、更顺、更像重新写过"}
风格倾向：{output_style or "自然、通顺、可读性强，但不改变来源内容"}

改写蓝图：
{blueprint}

来源摘要与样本，用于保持原稿内容一致，禁止连续复制原句：
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
3. 不改变原稿实质、人物、场景、事件顺序、动作、对白、因果、观点、结论、章节顺序、段落大意和整体文风。
4. 不扩写、不缩写，不新增内容，不删关键内容，不重新生成另一篇。
5. 如果某处需要降重，只能改句法和用词，不能改事实和含义。
6. 只输出修复后的完整稿件。

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


def _auto_target_length(sources: list[PublicDomainSource], requested: int | None) -> int:
    if requested and int(requested) > 0:
        return max(1500, min(int(requested), 20_000))
    source_chars = sum(len(item.text or "") for item in sources)
    if source_chars <= 0:
        return 6000
    return max(5000, min(12_000, source_chars // 8))


def _tokens_for_target(target_length: int) -> int:
    return max(1800, min(12_000, int(target_length * 1.8)))
