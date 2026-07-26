"""Simple public-domain fusion writer.

The public-domain workflow is intentionally small and separate from the Epic
Novel engine.  It makes one planning call and one writing call, then performs
only local, non-billable checks.  It does not run Zhuque, an external
verifier, a repair loop, or a second editorial model.

The source material is an input to an independent work.  Modern compilations
are not treated as public-domain merely because they discuss old stories:
their prose, chapter order, examples, comments, and quotations must not be
copied into the result.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable

from app.core.config import settings
from app.services.aigc_quality_gate import (
    AigcManualReviewRequired,
    AigcTechnicalError,
    enforce_aigc_quality_gate,
)
from app.services.deepseek import generate_text as deepseek_generate_text


MIN_SOURCE_CHARS = 300
MAX_SOURCES = 10
MAX_SOURCE_CHARS = 240_000
MAX_PROMPT_SAMPLE_CHARS = 8_000
MAX_TARGET_LENGTH = 20_000
OVERLAP_WINDOW = 42
OVERLAP_STEP = 14
OVERLAP_RATIO_BLOCK = 0.12
OVERLAP_RATIO_WARN = 0.07

_PUBLIC_DOMAIN_HINTS = ("公版", "公共领域", "public domain", "cc0", "保护期届满")
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
    "根据要求",
    "参考来源",
)
_PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts" / "public_domain_rewrite"


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
class SourceModeProfile:
    key: str
    label: str
    evidence: tuple[str, ...]


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
    source_mode: str = "hybrid"
    source_mode_label: str = "混合型"
    source_mode_evidence: tuple[str, ...] = ()
    # Compatibility fields used by the old task/detail page.  The simple
    # public-domain route does not enable the AIGC gate.
    pre_aigc_manuscript: str = ""
    aigc_required: bool = False
    aigc_status: str = "not_required"
    aigc_initial_max_score: int | None = None
    aigc_final_max_score: int | None = None
    aigc_rewrite_rounds: int = 0
    aigc_quality_passed: bool | None = None
    aigc_report: dict[str, object] | None = None


class PublicDomainRewriteError(ValueError):
    """Raised for validation failures before a paid model call."""


def rewrite_public_domain_fusion(
    *,
    sources: Iterable[PublicDomainSource],
    target_title: str | None = None,
    target_direction: str | None = None,
    target_length: int | None = None,
    output_style: str | None = None,
    public_domain_confirmed: bool = False,
    # Kept only for backwards compatibility with old callers/tests.  The
    # public-domain bookshelf route leaves this unset, so no Zhuque call is
    # made.  A caller explicitly passing an output directory can still opt in.
    aigc_output_dir: Path | None = None,
) -> PublicDomainFusionResult:
    clean_sources = _validate_sources(
        list(sources), public_domain_confirmed=public_domain_confirmed
    )
    target_length = _auto_target_length(clean_sources, target_length)
    source_mode = _infer_source_mode(
        clean_sources,
        target_direction=target_direction,
        output_style=output_style,
    )
    mode_guidance = _load_mode_guidance(source_mode.key)
    source_reports = tuple(_build_source_report(item) for item in clean_sources)
    source_briefs = "\n\n".join(
        _source_brief(item, index + 1) for index, item in enumerate(clean_sources)
    )

    blueprint_prompt = _render_prompt(
        "blueprint.md",
        source_count=len(clean_sources),
        source_briefs=source_briefs,
        target_title=target_title or "请根据融合后的新作自然拟题",
        target_direction=target_direction or "从多来源材料中提炼一个新的中心问题和叙述路径",
        target_length=target_length,
        output_style=output_style or "自然、耐读、有叙事感，不写成论文",
        source_mode=source_mode.label,
        source_mode_evidence="；".join(source_mode.evidence) or "未发现单一类型占绝对优势",
        mode_guidance=mode_guidance,
    )
    blueprint = deepseek_generate_text(
        blueprint_prompt,
        temperature=0.25,
        max_output_tokens=4_500,
        system_instruction=(
            "你是公版资料的独立创作策划。只把输入当作事实线索和思想材料，"
            "不要复制现代整理本的表达、章节安排或评论。"
        ),
    ).strip()
    if not blueprint:
        raise RuntimeError("DeepSeek returned an empty fusion blueprint")

    manuscript_prompt = _render_prompt(
        "manuscript.md",
        source_count=len(clean_sources),
        source_briefs=source_briefs,
        blueprint=blueprint,
        target_title=target_title or "请根据成稿自然拟题",
        target_direction=target_direction or "把材料融会为一部独立的新作",
        target_length=target_length,
        output_style=output_style or "自然、耐读、有叙事感，不写成论文",
        source_mode=source_mode.label,
        source_mode_evidence="；".join(source_mode.evidence) or "未发现单一类型占绝对优势",
        mode_guidance=mode_guidance,
    )
    manuscript = deepseek_generate_text(
        manuscript_prompt,
        temperature=0.72,
        max_output_tokens=_tokens_for_target(target_length),
        system_instruction=(
            "你是中文非虚构图书作者。请独立写成新稿，材料真实，来源表达不可复刻，"
            "不补写没有依据的现代人物、现代事件、心理和结论。"
        ),
    ).strip()
    if not manuscript:
        raise RuntimeError("DeepSeek returned an empty manuscript")

    # One local, free check.  It reports risk but never launches a paid repair
    # loop.  This is deliberately not a copyright detector or a substitute for
    # a final human/legal review.
    quality = _quality_check(manuscript, clean_sources)
    warnings = list(quality["warnings"])

    # Opt-in compatibility path for the historical AIGC unit test.  New public
    # domain tasks never pass aigc_output_dir and therefore never spend on it.
    pre_aigc_manuscript = manuscript
    aigc_required = bool(settings.AIGC_GATE_ENABLED and aigc_output_dir is not None)
    aigc_status = "not_required"
    aigc_initial_max_score: int | None = None
    aigc_final_max_score: int | None = None
    aigc_rewrite_rounds = 0
    aigc_quality_passed: bool | None = None
    aigc_report: dict[str, object] | None = None
    if aigc_required:
        try:
            gate_result = asyncio.run(
                enforce_aigc_quality_gate(
                    manuscript,
                    category="public_domain_rewrite",
                    output_dir=aigc_output_dir,
                    required=True,
                )
            )
            manuscript = gate_result.text.strip()
            aigc_status = gate_result.status
            aigc_initial_max_score = gate_result.initial_max_score
            aigc_final_max_score = gate_result.max_score
            aigc_rewrite_rounds = gate_result.rewrite_rounds
            aigc_quality_passed = gate_result.quality_passed
            aigc_report = gate_result.report
            quality = _quality_check(manuscript, clean_sources)
        except AigcManualReviewRequired as exc:
            result = exc.result
            aigc_status = "manual_review"
            aigc_initial_max_score = result.initial_max_score if result else None
            aigc_final_max_score = result.max_score if result else None
            aigc_rewrite_rounds = result.rewrite_rounds if result else 0
            aigc_quality_passed = result.quality_passed if result else False
            aigc_report = result.report if result else None
            warnings.append(f"朱雀阶段需要人工复核：{exc}")
        except AigcTechnicalError as exc:
            aigc_status = "technical_failed"
            aigc_quality_passed = False
            aigc_report = exc.report if isinstance(exc.report, dict) else None
            warnings.append(f"朱雀技术故障：{exc}")

    title = _pick_title(target_title, manuscript or pre_aigc_manuscript)
    report = _format_quality_report(
        quality,
        source_reports,
        len(manuscript or pre_aigc_manuscript),
        source_mode,
    )
    return PublicDomainFusionResult(
        title=title,
        manuscript=manuscript or pre_aigc_manuscript,
        blueprint=blueprint,
        quality_report=report,
        source_reports=source_reports,
        warnings=tuple(dict.fromkeys(warnings)),
        status=str(quality["status"]),
        source_mode=source_mode.key,
        source_mode_label=source_mode.label,
        source_mode_evidence=source_mode.evidence,
        pre_aigc_manuscript=pre_aigc_manuscript,
        aigc_required=aigc_required,
        aigc_status=aigc_status,
        aigc_initial_max_score=aigc_initial_max_score,
        aigc_final_max_score=aigc_final_max_score,
        aigc_rewrite_rounds=aigc_rewrite_rounds,
        aigc_quality_passed=aigc_quality_passed,
        aigc_report=aigc_report,
    )


def check_public_domain_manuscript(
    manuscript: str, sources: Iterable[PublicDomainSource]
) -> dict[str, object]:
    """Run the same free local report after a user-provided recheck."""
    return _quality_check(manuscript, list(sources))


def _validate_sources(
    sources: list[PublicDomainSource], *, public_domain_confirmed: bool
) -> list[PublicDomainSource]:
    if not 1 <= len(sources) <= MAX_SOURCES:
        raise PublicDomainRewriteError(f"必须提供 1 到 {MAX_SOURCES} 个来源。")
    if not public_domain_confirmed:
        raise PublicDomainRewriteError("请确认你拥有使用这些来源的权利，或它们属于公版/公共领域。")

    cleaned: list[PublicDomainSource] = []
    for index, item in enumerate(sources, start=1):
        title = (item.title or "").strip()
        text = _normalize_source_text(item.text)
        if not title:
            raise PublicDomainRewriteError(f"第 {index} 个来源缺少书名。")
        if len(text) < MIN_SOURCE_CHARS:
            raise PublicDomainRewriteError(
                f"《{title}》正文过短，至少需要 {MIN_SOURCE_CHARS} 字。"
            )
        # Large books are sampled before prompting; never silently send the
        # whole raw book to an API request.
        text = text[:MAX_SOURCE_CHARS]
        lowered = text.lower()
        if any(marker in lowered for marker in _COPYRIGHT_MARKERS):
            raise PublicDomainRewriteError(
                f"《{title}》正文里检测到版权限制提示，请先确认来源权利。"
            )
        cleaned.append(
            PublicDomainSource(
                title=title,
                text=text,
                author=(item.author or "").strip() or None,
                publication_year=item.publication_year,
                rights_note=(item.rights_note or "").strip() or None,
            )
        )
    return cleaned


def _public_domain_basis(item: PublicDomainSource) -> str:
    note = (item.rights_note or "").strip()
    lowered = note.lower()
    if any(hint in lowered for hint in _PUBLIC_DOMAIN_HINTS):
        return note
    if item.publication_year and int(item.publication_year) <= 1930:
        return f"出版年份 {int(item.publication_year)}；仍需按具体版本核对"
    return "用户确认；未在服务端替代人工权利审查"


def _build_source_report(item: PublicDomainSource) -> SourceReport:
    return SourceReport(
        title=item.title,
        chars=len(item.text),
        public_domain_basis=_public_domain_basis(item),
        sampled_chars=len(_sample_source(item.text)),
    )


def _normalize_source_text(text: str) -> str:
    lines = [
        re.sub(r"[ \t]+", " ", line).strip()
        for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()
    ]
    result: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if not blank:
                result.append("")
            blank = True
        else:
            result.append(line)
            blank = False
    return "\n".join(result).strip()


def _source_brief(item: PublicDomainSource, index: int) -> str:
    sample = _sample_source(item.text)
    return (
        f"来源 {index}：{item.title}\n"
        f"作者：{item.author or '未填写'}；出版年份：{item.publication_year or '未填写'}；"
        f"权利说明：{item.rights_note or '未填写'}；全文字数：{len(item.text)}\n"
        "输入节选（只用于理解事实线索和思想材料，不得照抄）：\n"
        f"{sample}"
    )


def _sample_source(text: str) -> str:
    text = _normalize_source_text(text)
    if len(text) <= MAX_PROMPT_SAMPLE_CHARS:
        return text
    head = text[:3_200]
    middle_start = max(0, len(text) // 2 - 1_400)
    middle = text[middle_start : middle_start + 2_800]
    tail = text[-2_000:]
    return f"[开篇节选]\n{head}\n\n[中段节选]\n{middle}\n\n[结尾节选]\n{tail}"


def _render_prompt(filename: str, **values: object) -> str:
    path = _PROMPT_DIR / filename
    try:
        template = path.read_text(encoding="utf-8")
    except OSError:
        template = (
            "请基于输入材料独立创作，不复制现代整理本。"
            "\n来源：{{source_briefs}}\n蓝图：{{blueprint}}\n"
        )
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", str(value))
    return template


def _load_mode_guidance(mode: str) -> str:
    path = _PROMPT_DIR / "modes" / f"{mode}.md"
    if not path.exists():
        path = _PROMPT_DIR / "modes" / "hybrid.md"
    return path.read_text(encoding="utf-8").strip()


def _infer_source_mode(
    sources: list[PublicDomainSource],
    *,
    target_direction: str | None,
    output_style: str | None,
) -> SourceModeProfile:
    """Choose a writing architecture without adding another model call."""

    title_rules: dict[str, tuple[str, ...]] = {
        "narrative": ("故事", "公案", "寓言", "传奇", "小说", "演义", "轶事"),
        "historical": ("传记", "列传", "年谱", "通史", "史话", "史记", "纪事", "人物志"),
        # Avoid the single character “经”: titles such as “佛经故事” are
        # narrative collections, not automatically source-text exegesis.
        "classical": ("原典", "经文", "译注", "注疏", "笺注", "语录", "古文", "诗选", "词选", "赋选", "文选"),
        # “智慧” is too broad to decide architecture on its own; many story
        # collections use it as marketing language.
        "expository": ("知识", "全知道", "百科", "概论", "导论", "常识", "辞典"),
        "practical": ("方法", "指南", "手册", "修习", "修行", "养生", "技法", "入门"),
    }
    request_rules: dict[str, tuple[str, ...]] = {
        "narrative": ("故事", "叙事", "讲述", "情节", "人物"),
        "historical": ("传记", "生平", "历史", "年代", "沿革"),
        "classical": ("原典", "原文", "译注", "注疏", "经文", "古文"),
        "expository": ("知识", "解释", "概念", "义理", "科普", "百科"),
        "practical": ("方法", "步骤", "实践", "指南", "训练", "修习"),
    }
    scores = {key: 0 for key in title_rules}
    evidence: dict[str, list[str]] = {key: [] for key in title_rules}
    for source in sources:
        title = source.title.strip()
        title_matched = False
        for mode, hints in title_rules.items():
            matched = [hint for hint in hints if hint in title]
            if not matched:
                continue
            title_matched = True
            scores[mode] += 3
            evidence[mode].append(f"《{title}》命中“{matched[0]}”")

        if (
            not title_matched
            and title.endswith("经")
            and "故事" not in title
            and "智慧" not in title
        ):
            title_matched = True
            scores["classical"] += 3
            evidence["classical"].append(f"《{title}》以经典名称结尾")

        # Only inspect text when the title provides no useful type signal.
        # This is local and free, and prevents an ambiguous title from making
        # an actual story collection fall back to the wrong architecture.
        if not title_matched:
            sample = _sample_source(source.text)
            narrative_hits = sum(
                sample.count(marker)
                for marker in (
                    "从前",
                    "有一天",
                    "有一位",
                    "来到",
                    "看见",
                    "听见",
                    "问道",
                    "回答",
                    "说道",
                    "国王",
                    "禅师",
                    "比丘",
                    "弟子",
                    "老人",
                )
            )
            quote_pairs = min(sample.count("“"), sample.count("”"))
            if narrative_hits >= 8 or (narrative_hits >= 4 and quote_pairs >= 2):
                scores["narrative"] += 3
                evidence["narrative"].append(f"《{title}》正文抽样呈连续叙事特征")

    request_text = f"{target_direction or ''}\n{output_style or ''}"
    for mode, hints in request_rules.items():
        matched = [hint for hint in hints if _has_positive_request_hint(request_text, hint)]
        if not matched:
            continue
        scores[mode] += 5
        evidence[mode].append(f"目标要求命中“{matched[0]}”")

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_mode, best_score = ranked[0]
    second_score = ranked[1][1]
    labels = {
        "narrative": "故事叙事型",
        "historical": "史传纪实型",
        "classical": "原典阐释型",
        "expository": "知识义理型",
        "practical": "实践指南型",
        "hybrid": "混合型",
    }
    if best_score < 3 or (best_score == second_score and best_score < 8):
        combined = tuple(
            item
            for mode, _score in ranked[:2]
            for item in evidence.get(mode, [])[:2]
        )
        return SourceModeProfile("hybrid", labels["hybrid"], combined)
    return SourceModeProfile(
        best_mode,
        labels[best_mode],
        tuple(evidence[best_mode][:6]),
    )


def _has_positive_request_hint(text: str, hint: str) -> bool:
    """Ignore type words that occur inside an explicit negative request."""

    for match in re.finditer(re.escape(hint), text):
        before = text[max(0, match.start() - 14) : match.start()]
        clause = re.split(r"[，。；;\n]", before)[-1]
        if re.search(r"(?:不要|不得|避免|禁止|并非|不是|不再|无须|无需).{0,10}$", clause):
            continue
        return True
    return False


def _quality_check(
    manuscript: str, sources: list[PublicDomainSource]
) -> dict[str, object]:
    warnings: list[str] = []
    overlap_issues: list[OverlapIssue] = []
    residue_issues: list[str] = []
    for source in sources:
        issue = _overlap_issue(manuscript, source)
        if issue and issue.ratio >= OVERLAP_RATIO_WARN:
            overlap_issues.append(issue)
            warnings.append(f"与《{source.title}》存在连续短句重合：{issue.ratio:.1%}")
    lowered = manuscript.lower()
    for pattern in _AI_RESIDUE_PATTERNS:
        if pattern in lowered:
            residue_issues.append(pattern)
            warnings.append(f"疑似输出残留：{pattern}")
    status = "ok"
    if any(issue.ratio >= OVERLAP_RATIO_BLOCK for issue in overlap_issues):
        status = "needs_review"
        warnings.append("本地重合提示仅供人工复核，系统未自动发起修复调用。")
    if residue_issues:
        status = "needs_review"
    return {
        "status": status,
        "warnings": warnings,
        "overlap_issues": overlap_issues,
        "residue_issues": residue_issues,
    }


def _overlap_issue(
    manuscript: str, source: PublicDomainSource
) -> OverlapIssue | None:
    generated = _compact_for_overlap(manuscript)
    source_text = _compact_for_overlap(source.text)
    if len(generated) < OVERLAP_WINDOW or len(source_text) < OVERLAP_WINDOW:
        return None
    chunks = [
        generated[pos : pos + OVERLAP_WINDOW]
        for pos in range(
            0, max(1, len(generated) - OVERLAP_WINDOW + 1), OVERLAP_STEP
        )
    ]
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
        samples=tuple(item[:80] for item in hits[:5]),
    )


def _compact_for_overlap(text: str) -> str:
    return re.sub(r"[\s，。、“”‘’：；！？,.!?;:()\[\]（）《》<>【】\-—_]+", "", text or "").lower()


def _format_quality_report(
    quality: dict[str, object],
    source_reports: tuple[SourceReport, ...],
    manuscript_chars: int,
    source_mode: SourceModeProfile,
) -> str:
    lines = [
        f"状态：{quality['status']}",
        f"成稿字数：{manuscript_chars}",
        f"来源数量：{len(source_reports)}",
        f"来源类型：{source_mode.label}",
        f"类型依据：{'；'.join(source_mode.evidence) or '自动归为混合型'}",
        "来源仅作为输入材料；现代整理本的文字、章节安排、评论和现代例子不得直接进入正文。",
    ]
    for item in source_reports:
        lines.append(
            f"- 《{item.title}》：{item.chars} 字；依据：{item.public_domain_basis}；"
            f"送入模型样本 {item.sampled_chars} 字"
        )
    for issue in quality.get("overlap_issues") or []:
        assert isinstance(issue, OverlapIssue)
        lines.append(
            f"- 本地重合提示《{issue.source_title}》："
            f"{issue.matched_chunks}/{issue.total_chunks}（约 {issue.ratio:.1%}）"
        )
    return "\n".join(lines)


def _pick_title(target_title: str | None, manuscript: str) -> str:
    if target_title and target_title.strip():
        return target_title.strip()
    for line in manuscript.splitlines():
        clean = line.strip().lstrip("#").strip()
        if clean and len(clean) <= 40:
            return clean
    return "公版融合新作"


def _auto_target_length(
    sources: list[PublicDomainSource], requested: int | None
) -> int:
    if requested and int(requested) > 0:
        return max(1_500, min(int(requested), MAX_TARGET_LENGTH))
    median = sorted(len(item.text) for item in sources)[len(sources) // 2]
    return max(5_000, min(12_000, median // 8))


def _tokens_for_target(target_length: int) -> int:
    return max(2_400, min(24_000, int(target_length * 1.8)))


__all__ = [
    "MAX_SOURCES",
    "PublicDomainFusionResult",
    "PublicDomainRewriteError",
    "PublicDomainSource",
    "SourceModeProfile",
    "SourceReport",
    "check_public_domain_manuscript",
    "rewrite_public_domain_fusion",
]
