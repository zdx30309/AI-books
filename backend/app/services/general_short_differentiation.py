"""Differentiation gate for the general_short category.

v09 asked every finished book to run a structural similarity audit and to treat
55%/50% as the block boundary, but the writer prompt carried no such check and
nothing in the pipeline measured it.  This module implements the audit:

* the literal layer is deterministic (n-gram overlap, longest common run, and a
  sentence-similarity distribution), so it is reproducible without a provider;
* the structural layer is scored by the frozen v09 formula
  ``S = A*0.40 + B*0.30 + C*0.30`` and ``composite = S*0.60 + M*0.40``;
* the verdict is ``50%`` and above => high risk, matching appendix six.

Nothing here rewrites the manuscript; it only reports so the caller can decide to
regenerate the skeleton.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
import re
from typing import Any

from app.services.general_short_fingerprint import StructureFingerprint, build_fingerprint

#: Composite similarity at or above this value blocks acceptance (v09 appendix six).
HIGH_RISK_THRESHOLD = 50.0


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？；!?;])", text or "")
    return [part.strip() for part in parts if part.strip()]


def _shingles(text: str, size: int) -> set[str]:
    if len(text) < size:
        return {text} if text else set()
    return {text[i : i + size] for i in range(len(text) - size + 1)}


def longest_common_run(source: str, draft: str) -> tuple[int, str]:
    if not source or not draft:
        return 0, ""
    match = SequenceMatcher(None, source, draft, autojunk=False).find_longest_match(
        0, len(source), 0, len(draft)
    )
    return match.size, source[match.a : match.a + match.size]


@dataclass
class LiteralLayer:
    source_chars: int = 0
    draft_chars: int = 0
    overlap_ratio: float = 0.0
    source_overlap_ratio: float = 0.0
    longest_common_chars: int = 0
    longest_common_sample: str = ""
    max_sentence_similarity: float = 0.0
    high_similarity_sentence_ratio: float = 0.0
    literal_reuse_score: float = 0.0


def measure_literal(source_text: str, draft_text: str) -> LiteralLayer:
    source = _normalize(source_text)
    draft = _normalize(draft_text)
    layer = LiteralLayer(source_chars=len(source), draft_chars=len(draft))
    if not source or not draft:
        return layer

    grams_source = _shingles(source, 8)
    grams_draft = _shingles(draft, 8)
    shared = grams_source & grams_draft
    layer.overlap_ratio = len(shared) / max(len(grams_draft), 1)
    layer.source_overlap_ratio = len(shared) / max(len(grams_source), 1)

    size, sample = longest_common_run(source, draft)
    layer.longest_common_chars = size
    layer.longest_common_sample = sample[:120]

    draft_sentences = _sentences(draft_text)
    source_sentences = _sentences(source_text)
    if draft_sentences and source_sentences:
        best = 0.0
        high = 0
        for sentence in draft_sentences:
            candidate = 0.0
            for other in source_sentences:
                if abs(len(sentence) - len(other)) > max(len(sentence), len(other)):
                    continue
                ratio = SequenceMatcher(None, _normalize(sentence), _normalize(other), autojunk=False).ratio()
                candidate = max(candidate, ratio)
                if candidate >= 0.95:
                    break
            best = max(best, candidate)
            if candidate >= 0.5:
                high += 1
        layer.max_sentence_similarity = round(best, 4)
        layer.high_similarity_sentence_ratio = round(high / len(draft_sentences), 4)

    # Literal reuse blends source coverage, a long-run penalty and the share of
    # near-duplicate sentences; all three are 0—1.
    run_penalty = min(size / 100.0, 1.0)
    layer.literal_reuse_score = round(
        min(
            1.0,
            0.35 * layer.source_overlap_ratio
            + 0.35 * run_penalty
            + 0.30 * layer.high_similarity_sentence_ratio,
        )
        * 100,
        2,
    )
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
    """Map each abstracted source unit onto the draft by shared keyword evidence.

    This is a deterministic proxy for the manual unit-by-unit mapping the spec
    describes.  It reads only the abstracted fingerprint, never the raw source.
    """
    units = fingerprint.argument_units
    draft = _normalize(draft_text)
    layer = StructureLayer(unit_total=len(units))
    if not units or not draft:
        return layer

    draft_grams = _shingles(draft, 6)
    full = partial = none = 0
    for unit in units:
        tokens = [t for t in re.findall(r"[\u4e00-\u9fff]{2,6}", unit.unit) if t]
        if not tokens:
            none += 1
            layer.unit_map.append({"id": str(unit.index), "level": "无"})
            continue
        hit = sum(1 for token in tokens if token in draft)
        ratio = hit / len(tokens)
        if ratio >= 0.6:
            level = "完整"
            full += 1
        elif ratio >= 0.25:
            level = "部分"
            partial += 1
        else:
            level = "无"
            none += 1
        layer.unit_map.append({"id": str(unit.index), "level": level, "evidence": f"{hit}/{len(tokens)}"})

    layer.unit_full, layer.unit_partial, layer.unit_none = full, partial, none
    layer.unit_match_score = round((full * 1.0 + partial * 0.5) / len(units) * 100, 2)

    # Case order: penalise a draft whose case marks appear in the source order.
    if fingerprint.case_sequence:
        source_positions = [i for i, _ in enumerate(fingerprint.case_sequence)]
        draft_positions = [
            draft.find(_normalize(case)) for case in fingerprint.case_sequence
        ]
        present = [p for p in draft_positions if p >= 0]
        if len(present) >= 2:
            ordered = present == sorted(present)
            coverage = len(present) / len(fingerprint.case_sequence)
            layer.case_order_score = round((100.0 if ordered else 55.0) * coverage, 2)
        elif len(present) == 1:
            layer.case_order_score = 25.0
        else:
            layer.case_order_score = 10.0
    else:
        layer.case_order_score = 10.0

    # Narrative spine: opening device, causal chain and ending function retention.
    spine_hits = 0
    spine_total = 3
    opening = _normalize(fingerprint.opening_device)
    if "设问" in opening and re.search(r"[？?]", draft_text[:600]):
        spine_hits += 1
    elif "数据" in opening and re.search(r"\d", draft_text[:600]):
        spine_hits += 1
    elif "人物" in opening and re.search(r"(?:我|你|他|她)", draft_text[:600]):
        spine_hits += 1
    if fingerprint.causal_chain:
        markers = [m for m in re.findall(r"(?:因为|由于|所以|因此|导致|造成|使得|于是|从而)", fingerprint.causal_chain)]
        if markers and sum(1 for m in markers if m in draft) / len(markers) >= 0.6:
            spine_hits += 1
    ending = fingerprint.ending_function
    draft_tail = draft_text[-600:]
    if "行动" in ending and re.search(r"(?:建议|第一步|可以试着|行动)", draft_tail):
        spine_hits += 1
    elif "升华" in ending and re.search(r"(?:希望|愿|未来|终将)", draft_tail):
        spine_hits += 1
    elif "开放" in ending and re.search(r"(?:问题|仍|尚待|留给)", draft_tail):
        spine_hits += 1
    elif "回扣" in ending and draft_tail.strip():
        spine_hits += 1
    layer.narrative_spine_score = round(spine_hits / spine_total * 100, 2)

    layer.structure_score = round(
        layer.unit_match_score * 0.40
        + layer.case_order_score * 0.30
        + layer.narrative_spine_score * 0.30,
        2,
    )
    return layer


def material_similarity(literal: LiteralLayer, structure: StructureLayer) -> float:
    """M layer: literal reuse dominates, structure supplies the rest."""
    data_reuse = min(100.0, literal.source_overlap_ratio * 100 * 1.5)
    case_reuse = structure.case_order_score
    entity_reuse = min(100.0, literal.high_similarity_sentence_ratio * 100 * 2)
    literal_reuse = literal.literal_reuse_score
    return round(data_reuse * 0.30 + case_reuse * 0.25 + entity_reuse * 0.20 + literal_reuse * 0.25, 2)


def composite_score(structure_score: float, material_score: float) -> float:
    return round(structure_score * 0.60 + material_score * 0.40, 2)


@dataclass
class DifferentiationReport:
    literal: LiteralLayer
    structure: StructureLayer
    material_score: float = 0.0
    composite: float = 0.0
    verdict: str = "安全"
    blocked: bool = False
    fix_directive: str = ""
    differentiation_list: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "literal": asdict(self.literal),
            "structure": asdict(self.structure),
            "material_score": self.material_score,
            "composite": self.composite,
            "verdict": self.verdict,
            "blocked": self.blocked,
            "fix_directive": self.fix_directive,
            "differentiation_list": self.differentiation_list,
        }


def evaluate_differentiation(
    source_text: str,
    draft_text: str,
    *,
    fingerprint: StructureFingerprint | None = None,
) -> DifferentiationReport:
    fp = fingerprint or build_fingerprint(source_text)
    literal = measure_literal(source_text, draft_text)
    structure = score_structure(fp, draft_text)
    material = material_similarity(literal, structure)
    composite = composite_score(structure.structure_score, material)
    blocked = composite >= HIGH_RISK_THRESHOLD
    report = DifferentiationReport(
        literal=literal,
        structure=structure,
        material_score=material,
        composite=composite,
        verdict="高危" if blocked else "安全",
        blocked=blocked,
    )
    if blocked:
        if structure.structure_score >= 70:
            report.fix_directive = (
                "结构框架相似度过高：请打散时间线，按要素因果链重组章节，更换叙事主角与案例组合后重写，不要只做换词处理。"
            )
        else:
            report.fix_directive = (
                "综合相似度超过 50%：优先替换沿用原文的案例与数据，重排论证入口，再复查结构映射。"
            )
    report.differentiation_list = _differentiation_list(fp, draft_text)
    return report


def _differentiation_list(fingerprint: StructureFingerprint, draft_text: str) -> list[str]:
    """List material the draft carries that the abstracted source does not."""
    draft = _normalize(draft_text)
    listed: list[str] = []
    for keyword in fingerprint.keyword_fingerprint:
        if keyword and _normalize(keyword) not in draft:
            listed.append(f"未沿用原文术语：{keyword}")
    for case in fingerprint.case_sequence:
        if case and _normalize(case) not in draft:
            listed.append(f"未沿用原文案例：{case}")
    return listed[:20]
