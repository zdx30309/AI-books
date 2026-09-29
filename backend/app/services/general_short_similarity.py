"""Deterministic similarity audit core for the general_short category.

Implements the frozen scoring of v09 section ten plus appendix six:
literal metrics at 6/8/10/15/20-grams, one-decimal rounding, the boundary
labels, the structure fallback notice, and the copy/multi-source rules.
Provider calls live in separate modules so this file stays reproducible offline.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
import re
from typing import Any

from app.services.general_short_fingerprint import StructureFingerprint, build_fingerprint

#: Composite similarity at or above this value is high risk (v09 section ten).
HIGH_RISK_THRESHOLD = 50.0
#: Still "safe", but the report must carry the near-boundary label.
NEAR_BOUNDARY_MIN = 45.0
#: High risk, but only just over the line.
JUST_OVER_MAX = 55.0
#: Structure score at or above this forces the fallback notice regardless of composite.
STRUCTURE_FALLBACK_THRESHOLD = 70.0
#: The literal layer must report every one of these gram sizes.
GRAM_SIZES = (6, 8, 10, 15, 20)
#: Source files that are byte-identical to the draft short-circuit at 100%.
COPY_SIMILARITY = 100.0

_TIMELINE_RE = re.compile(r"\[\s*\d{1,2}:\d{2}(?::\d{2})?\s*[-—~]\s*\d{1,2}:\d{2}(?::\d{2})?\s*\]")
_WORDCOUNT_LINE_RE = re.compile(r"^\s*(?:全文|总字数|字数|word count)\s*[:：]?\s*\d+\s*字?\s*$", re.IGNORECASE)


def normalize(text: str) -> str:
    """Whitespace-free comparison form used by every metric."""
    return re.sub(r"\s+", "", text or "")


def denoise(text: str) -> str:
    """Drop timeline stamps and trailing word-count lines before any counting."""
    lines: list[str] = []
    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _WORDCOUNT_LINE_RE.match(raw):
            continue
        lines.append(_TIMELINE_RE.sub("", raw))
    return "\n".join(line for line in lines if line.strip())


#: Appendix six: when comparing sentences, keep draft sentences of >= 15 chars
#: and source sentences of >= 10 chars, handling Chinese and English separately.
MIN_DRAFT_SENTENCE_CHARS = 15
MIN_SOURCE_SENTENCE_CHARS = 10


def sentences(text: str, *, min_chars: int = 0) -> list[str]:
    parts = re.split(r"(?<=[。！？；!?;])", text or "")
    out = [p.strip() for p in parts if p.strip()]
    if min_chars > 0:
        out = [p for p in out if len(p) >= min_chars]
    return out


def shingles(text: str, size: int) -> set[str]:
    if len(text) < size:
        return {text} if text else set()
    return {text[i : i + size] for i in range(len(text) - size + 1)}


def longest_common_run(source: str, draft: str) -> tuple[int, str]:
    if not source or not draft:
        return 0, ""
    m = SequenceMatcher(None, source, draft, autojunk=False).find_longest_match(
        0, len(source), 0, len(draft)
    )
    return m.size, source[m.a : m.a + m.size]


def one_decimal(value: float) -> float:
    """v09 section ten: composite similarity keeps exactly one decimal."""
    return round(float(value) + 1e-9, 1)


@dataclass
class NgramRow:
    size: int
    source_count: int
    draft_count: int
    shared_count: int
    overlap: float


@dataclass
class LiteralLayer:
    source_chars: int = 0
    draft_chars: int = 0
    length_ratio: float = 0.0
    ngrams: list[NgramRow] = field(default_factory=list)
    longest_common_chars: int = 0
    longest_common_sample: str = ""
    max_sentence_similarity: float = 0.0
    high_similarity_sentence_ratio: float = 0.0
    sentence_buckets: dict[str, int] = field(default_factory=dict)
    literal_reuse_score: float = 0.0
    literal_grade_d: float = 0.0
    is_copy: bool = False


def measure_literal(source_text: str, draft_text: str) -> LiteralLayer:
    source = normalize(denoise(source_text))
    draft = normalize(denoise(draft_text))
    layer = LiteralLayer(source_chars=len(source), draft_chars=len(draft))
    if not source or not draft:
        return layer

    # Identical files are copies, not independent drafts (appendix six, part two).
    if source == draft:
        layer.is_copy = True
        layer.longest_common_chars = len(source)
        layer.longest_common_sample = source[:200]
        layer.max_sentence_similarity = 1.0
        layer.high_similarity_sentence_ratio = 1.0
        layer.literal_reuse_score = COPY_SIMILARITY
        layer.literal_grade_d = 100.0
        for size in GRAM_SIZES:
            grams = shingles(source, size)
            layer.ngrams.append(NgramRow(size, len(grams), len(grams), len(grams), 1.0))
        return layer

    layer.length_ratio = one_decimal(len(draft) / max(len(source), 1) * 100)

    shared_by_size: dict[int, int] = {}
    for size in GRAM_SIZES:
        gs, gd = shingles(source, size), shingles(draft, size)
        shared = len(gs & gd)
        shared_by_size[size] = shared
        layer.ngrams.append(
            NgramRow(size, len(gs), len(gd), shared, round(shared / max(len(gd), 1), 4))
        )

    size8 = next(r for r in layer.ngrams if r.size == 8)
    run_size, run_sample = longest_common_run(source, draft)
    layer.longest_common_chars = run_size
    layer.longest_common_sample = run_sample[:200]

    draft_sents = sentences(draft_text, min_chars=MIN_DRAFT_SENTENCE_CHARS)
    source_sents = sentences(source_text, min_chars=MIN_SOURCE_SENTENCE_CHARS)
    buckets = {"<0.3": 0, "0.3-0.5": 0, "0.5-0.7": 0, "0.7-0.9": 0, ">=0.9": 0}
    if draft_sents and source_sents:
        best, high = 0.0, 0
        for d in draft_sents:
            cand = 0.0
            for s in source_sents:
                if not s:
                    continue
                ratio = SequenceMatcher(None, normalize(d), normalize(s), autojunk=False).ratio()
                cand = max(cand, ratio)
                if cand >= 0.99:
                    break
            best = max(best, cand)
            if cand >= 0.5:
                high += 1
            if cand < 0.3:
                buckets["<0.3"] += 1
            elif cand < 0.5:
                buckets["0.3-0.5"] += 1
            elif cand < 0.7:
                buckets["0.5-0.7"] += 1
            elif cand < 0.9:
                buckets["0.7-0.9"] += 1
            else:
                buckets[">=0.9"] += 1
        layer.max_sentence_similarity = round(best, 4)
        layer.high_similarity_sentence_ratio = round(high / len(draft_sents), 4)
    layer.sentence_buckets = buckets

    run_penalty = min(run_size / 100.0, 1.0)
    layer.literal_reuse_score = one_decimal(
        min(
            1.0,
            0.35 * size8.overlap
            + 0.35 * run_penalty
            + 0.30 * layer.high_similarity_sentence_ratio,
        )
        * 100
    )
    layer.literal_grade_d = literal_grade_from_table(layer)
    return layer


@dataclass
class StructureLayer:
    unit_total: int = 0
    unit_full: int = 0
    unit_partial: int = 0
    unit_none: int = 0
    unit_match_score: float = 0.0
    case_order_score: float = 0.0
    narrative_spine_score: float = 0.0
    structure_score: float = 0.0
    unit_map: list[dict[str, str]] = field(default_factory=list)


def score_structure(fingerprint: StructureFingerprint, draft_text: str) -> StructureLayer:
    """Map abstracted source units onto the draft by shared keyword evidence."""
    units = fingerprint.argument_units
    draft = normalize(draft_text)
    layer = StructureLayer(unit_total=len(units))
    if not units or not draft:
        return layer

    full = partial = none = 0
    for unit in units:
        tokens = [t for t in re.findall(r"[\u4e00-\u9fff]{2,6}", unit.unit) if t]
        if not tokens:
            none += 1
            layer.unit_map.append({"id": str(unit.index), "unit": unit.unit[:80], "level": "无", "evidence": "0/0"})
            continue
        hit = sum(1 for t in tokens if t in draft)
        ratio = hit / len(tokens)
        if ratio >= 0.6:
            level, full = "完整", full + 1
        elif ratio >= 0.25:
            level, partial = "部分", partial + 1
        else:
            level, none = "无", none + 1
        layer.unit_map.append(
            {"id": str(unit.index), "unit": unit.unit[:80], "level": level, "evidence": f"{hit}/{len(tokens)}"}
        )

    layer.unit_full, layer.unit_partial, layer.unit_none = full, partial, none
    layer.unit_match_score = one_decimal((full * 1.0 + partial * 0.5) / len(units) * 100)

    if fingerprint.case_sequence:
        positions = [draft.find(normalize(c)) for c in fingerprint.case_sequence]
        present = [p for p in positions if p >= 0]
        if len(present) >= 2:
            ordered = present == sorted(present)
            coverage = len(present) / len(fingerprint.case_sequence)
            layer.case_order_score = one_decimal((100.0 if ordered else 55.0) * coverage)
        elif len(present) == 1:
            layer.case_order_score = 25.0
        else:
            layer.case_order_score = 10.0
    else:
        layer.case_order_score = 10.0

    hits, total = 0, 3
    opening = fingerprint.opening_device
    if "设问" in opening and re.search(r"[？?]", draft_text[:600]):
        hits += 1
    elif "数据" in opening and re.search(r"\d", draft_text[:600]):
        hits += 1
    elif "人物" in opening and re.search(r"(?:我|你|他|她)", draft_text[:600]):
        hits += 1
    if fingerprint.causal_chain:
        markers = re.findall(r"(?:因为|由于|所以|因此|导致|造成|使得|于是|从而)", fingerprint.causal_chain)
        if markers and sum(1 for m in markers if m in draft) / len(markers) >= 0.6:
            hits += 1
    ending, tail = fingerprint.ending_function, draft_text[-600:]
    if "行动" in ending and re.search(r"(?:建议|第一步|可以试着|行动)", tail):
        hits += 1
    elif "升华" in ending and re.search(r"(?:希望|愿|未来|终将)", tail):
        hits += 1
    elif "开放" in ending and re.search(r"(?:问题|仍|尚待|留给)", tail):
        hits += 1
    elif "回扣" in ending and tail.strip():
        hits += 1
    layer.narrative_spine_score = one_decimal(hits / total * 100)

    layer.structure_score = one_decimal(
        layer.unit_match_score * 0.40
        + layer.case_order_score * 0.30
        + layer.narrative_spine_score * 0.30
    )
    return layer


def literal_reuse_grade(
    *,
    gram10_overlap: float,
    longest_common_chars: int,
    high_sentence_ratio: float,
) -> float:
    """Appendix six, part three: the D conversion table.

    The table is evaluated top-down and the first matching row wins, exactly as
    written.  It cannot be judged by a model, so it is computed here.
    """
    if (
        gram10_overlap <= 0.0
        and longest_common_chars < 12
        and high_sentence_ratio < 0.02
    ):
        return 5.0
    if longest_common_chars >= 100 or high_sentence_ratio > 0.25:
        return 100.0
    if longest_common_chars >= 30 or high_sentence_ratio >= 0.10:
        return 75.0
    if longest_common_chars >= 12 or high_sentence_ratio >= 0.02:
        return 50.0
    # A small overlap that the caller has already classified as factual terms.
    return 20.0


def material_similarity(literal: LiteralLayer, structure: StructureLayer) -> float:
    """M layer: data / case / named-entity / literal reuse, each 0-100."""
    data_reuse = min(100.0, literal.literal_reuse_score * 0.9)
    case_reuse = structure.case_order_score
    entity_reuse = min(100.0, literal.high_similarity_sentence_ratio * 100 * 2)
    literal_reuse = literal_grade_from_table(literal)
    return one_decimal(
        data_reuse * 0.30 + case_reuse * 0.25 + entity_reuse * 0.20 + literal_reuse * 0.25
    )


def literal_grade_from_table(literal: LiteralLayer) -> float:
    """D value for this literal layer, via the frozen conversion table."""
    gram10 = next((r.overlap for r in literal.ngrams if r.size == 10), 0.0)
    return literal_reuse_grade(
        gram10_overlap=gram10,
        longest_common_chars=literal.longest_common_chars,
        high_sentence_ratio=literal.high_similarity_sentence_ratio,
    )


def composite_score(structure_score: float, material_score: float) -> float:
    return one_decimal(structure_score * 0.60 + material_score * 0.40)


def boundary_label(composite: float) -> str:
    """The two mandatory boundary annotations from v09 section ten."""
    if NEAR_BOUNDARY_MIN <= composite < HIGH_RISK_THRESHOLD:
        return "【临界限】"
    if HIGH_RISK_THRESHOLD < composite <= JUST_OVER_MAX:
        return "【刚过线】"
    if composite == HIGH_RISK_THRESHOLD:
        return "【临界点】"
    return ""


def structure_fallback_notice(structure_score: float) -> str:
    """v09: this sentence is mandatory whenever S >= 70%, whatever the composite."""
    if structure_score >= STRUCTURE_FALLBACK_THRESHOLD:
        return "结构骨架沿用明显，即使综合分低于 50%，公开发布前也建议按“改骨架”方案处理。"
    return ""


@dataclass
class SourceAudit:
    source_name: str
    source_chars: int
    literal: LiteralLayer
    structure: StructureLayer
    material_score: float
    composite: float
    verdict: str
    boundary: str
    fallback_notice: str
    unit_none_count: int
    fix_directive: str
    differentiation_list: list[str]
    material_data: float = 0.0
    material_case: float = 0.0
    material_entity: float = 0.0
    material_literal: float = 0.0
    data_points: list[dict[str, str]] = field(default_factory=list)
    case_pairs: list[dict[str, str]] = field(default_factory=list)
    entity_counts: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "source_chars": self.source_chars,
            "literal": asdict(self.literal),
            "structure": asdict(self.structure),
            "material_score": self.material_score,
            "material_data": self.material_data,
            "material_case": self.material_case,
            "material_entity": self.material_entity,
            "material_literal": self.material_literal,
            "composite": self.composite,
            "verdict": self.verdict,
            "boundary": self.boundary,
            "fallback_notice": self.fallback_notice,
            "unit_none_count": self.unit_none_count,
            "fix_directive": self.fix_directive,
            "differentiation_list": self.differentiation_list,
            "data_points": self.data_points,
            "case_pairs": self.case_pairs,
            "entity_counts": self.entity_counts,
        }


def _data_points(source_text: str) -> list[dict[str, str]]:
    rows = []
    for m in re.finditer(r"\d+(?:\.\d+)?\s*(?:%|％|倍|万|亿|元|美元|年|月|天|人|次|例)", source_text):
        rows.append({"point": m.group(0), "reused": ""})
    return rows[:40]


def _entity_counts(source_text: str, draft_text: str) -> list[dict[str, str]]:
    names = set(re.findall(r"[\u4e00-\u9fff]{2,8}(?:公司|集团|银行|大学|医院|政府|城市|国家|地区|平台)", source_text))
    names |= set(re.findall(r"[A-Z][A-Za-z]{2,}", source_text))
    rows = []
    for n in sorted(names)[:30]:
        rows.append({
            "entity": n,
            "source_count": str(source_text.count(n)),
            "draft_count": str(draft_text.count(n)),
        })
    return rows


def audit_one_source(
    source_name: str,
    source_text: str,
    draft_text: str,
    *,
    fingerprint: StructureFingerprint | None = None,
) -> SourceAudit:
    fp = fingerprint or build_fingerprint(source_text)
    literal = measure_literal(source_text, draft_text)
    structure = score_structure(fp, draft_text)
    material = material_similarity(literal, structure)
    data_part = min(100.0, literal.literal_reuse_score * 0.9)
    case_part = structure.case_order_score
    entity_part = min(100.0, literal.high_similarity_sentence_ratio * 100 * 2)
    literal_part = literal_grade_from_table(literal)
    composite = COPY_SIMILARITY if literal.is_copy else composite_score(structure.structure_score, material)
    verdict = "高危" if composite >= HIGH_RISK_THRESHOLD else "安全"

    if composite >= HIGH_RISK_THRESHOLD:
        directive = (
            "结构框架相似度过高：请打散时间线，按要素因果链重组章节，更换叙事主角与案例组合后重写，不要只做换词处理。"
            if structure.structure_score >= STRUCTURE_FALLBACK_THRESHOLD
            else "综合相似度超过 50%：优先替换沿用原文的案例与数据，重排论证入口，再复查结构映射。"
        )
    else:
        directive = "未越过 50% 阈值，可按正常发布流程推进；如结构分偏高，发布前仍建议先改骨架。"

    diff_list = [f"未沿用原文术语：{k}" for k in fp.keyword_fingerprint if normalize(k) and normalize(k) not in normalize(draft_text)]
    diff_list += [f"未沿用原文案例：{c}" for c in fp.case_sequence if normalize(c) and normalize(c) not in normalize(draft_text)]

    return SourceAudit(
        source_name=source_name,
        source_chars=len(normalize(source_text)),
        literal=literal,
        structure=structure,
        material_score=material,
        material_data=data_part,
        material_case=case_part,
        material_entity=entity_part,
        material_literal=literal_part,
        composite=composite,
        verdict=verdict,
        boundary=boundary_label(composite),
        fallback_notice=structure_fallback_notice(structure.structure_score),
        unit_none_count=structure.unit_none,
        fix_directive=directive,
        differentiation_list=diff_list[:20],
        data_points=_data_points(source_text),
        entity_counts=_entity_counts(source_text, draft_text),
    )


@dataclass
class MultiSourceReport:
    applicable: bool = True
    reason: str = ""
    sources: list[SourceAudit] = field(default_factory=list)
    max_composite: float = 0.0
    overall_verdict: str = "安全"
    overall_boundary: str = ""
    overall_fallback_notice: str = ""
    draft_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "applicable": self.applicable,
            "reason": self.reason,
            "draft_name": self.draft_name,
            "max_composite": self.max_composite,
            "overall_verdict": self.overall_verdict,
            "overall_boundary": self.overall_boundary,
            "overall_fallback_notice": self.overall_fallback_notice,
            "sources": [s.to_dict() for s in self.sources],
        }


def audit_multi_source(
    sources: list[tuple[str, str]],
    draft_text: str,
    *,
    draft_name: str = "",
) -> MultiSourceReport:
    """Score each source independently and gate on the highest composite."""
    report = MultiSourceReport(draft_name=draft_name)
    real = [(n, t) for n, t in sources if (t or "").strip()]
    if not real:
        report.applicable = False
        report.reason = "本轮没有用户提供的外部灵感原文，相似度审查不适用。"
        return report

    for name, text in real:
        report.sources.append(audit_one_source(name, text, draft_text))

    report.max_composite = max(s.composite for s in report.sources)
    report.overall_verdict = "高危" if report.max_composite >= HIGH_RISK_THRESHOLD else "安全"
    worst = max(report.sources, key=lambda s: s.composite)
    report.overall_boundary = worst.boundary
    report.overall_fallback_notice = next(
        (s.fallback_notice for s in report.sources if s.fallback_notice), ""
    )
    return report
