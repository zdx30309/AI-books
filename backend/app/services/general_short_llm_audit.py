"""Provider-backed semantic audit layer for the general_short similarity gate.

The deterministic core in ``general_short_similarity`` measures structure and
material with local heuristics.  This module adds the model-judged layer the
operator asked for: an LLM reads the source transcript and the final manuscript
and returns the same frozen rubric, which is then merged with the deterministic
score.

Merging rule: the LLM judges semantics (does this paragraph carry the source's
point?), the local core judges measurable overlap.  The final composite is the
higher of the two on the structure axis and the higher on the material axis, so
a model miss cannot silently lower a real risk.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from app.services.general_short_similarity import (
    HIGH_RISK_THRESHOLD,
    SourceAudit,
    audit_one_source,
    boundary_label,
    composite_score,
    one_decimal,
    structure_fallback_notice,
)

logger = logging.getLogger(__name__)

#: Only this much source text is sent to the model; the full text is always used
#: by the deterministic layer, and the model needs the argument skeleton only.
MODEL_SOURCE_CHARS = 12000
MODEL_DRAFT_CHARS = 16000


class LlmAuditUnavailable(RuntimeError):
    """The model layer could not run; the deterministic layer still stands."""


@dataclass
class LlmAuditResult:
    ran: bool = False
    provider: str = ""
    model: str = ""
    structure_score: float = 0.0
    material_score: float = 0.0
    composite: float = 0.0
    unit_map: list[dict[str, str]] = field(default_factory=list)
    differentiation_list: list[str] = field(default_factory=list)
    fix_directive: str = ""
    notes: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def _load_instruction() -> str:
    from app.prompts.general_short_prompts import BUNDLE

    return (BUNDLE / "stages" / "llm_audit.md").read_text(encoding="utf-8")


def _strip_json(text: str) -> str:
    value = (text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.IGNORECASE)
    start, end = value.find("{"), value.rfind("}")
    if start >= 0 and end > start:
        value = value[start : end + 1]
    return value.strip()


def _clamp(value: Any) -> float:
    try:
        return one_decimal(max(0.0, min(100.0, float(value))))
    except (TypeError, ValueError):
        return 0.0


def _parse(payload: str) -> dict[str, Any]:
    data = json.loads(_strip_json(payload))
    if not isinstance(data, dict):
        raise ValueError("模型返回的审查结果不是 JSON 对象")
    return data


def _render(source_text: str, draft_text: str) -> str:
    instruction = _load_instruction()
    return (
        instruction.replace("{source_text}", (source_text or "")[:MODEL_SOURCE_CHARS])
        .replace("{draft_text}", (draft_text or "")[:MODEL_DRAFT_CHARS])
    )


def run_llm_audit(
    source_text: str,
    draft_text: str,
    *,
    caller: Callable[[str], str] | None = None,
) -> LlmAuditResult:
    """Ask a model to score one source/draft pair against the frozen rubric.

    ``caller`` is injectable so tests never touch a provider.  Any failure
    degrades to ``ran=False`` rather than blocking the deterministic report.
    """
    prompt = _render(source_text, draft_text)
    if caller is None:
        try:
            from app.core.config import settings
            from app.services import deepseek

            model = getattr(settings, "GENERAL_SHORT_AUDIT_MODEL", "") or settings.DEEPSEEK_MODEL

            def caller(text: str) -> str:  # type: ignore[misc]
                return deepseek.generate_text(text, temperature=0.0, thinking=False)

            provider = "deepseek"
        except Exception as exc:  # pragma: no cover - environment dependent
            raise LlmAuditUnavailable(f"审查模型不可用：{exc}") from exc
    else:
        provider = "injected"
        model = "injected"

    try:
        raw_text = caller(prompt)
    except Exception as exc:
        raise LlmAuditUnavailable(f"审查模型调用失败：{exc}") from exc

    data = _parse(raw_text)
    structure = data.get("structure") or {}
    material = data.get("material") or {}

    a = _clamp(structure.get("A_unit_match"))
    b = _clamp(structure.get("B_case_order"))
    c = _clamp(structure.get("C_narrative_spine"))
    s = _clamp(structure.get("S")) or one_decimal(a * 0.40 + b * 0.30 + c * 0.30)

    m = _clamp(material.get("M")) or one_decimal(
        _clamp(material.get("data_reuse")) * 0.30
        + _clamp(material.get("case_reuse")) * 0.25
        + _clamp(material.get("named_entity_reuse")) * 0.20
        + _clamp(material.get("literal_reuse")) * 0.25
    )
    composite = _clamp(data.get("composite")) or composite_score(s, m)

    units: list[dict[str, str]] = []
    for item in (structure.get("units") or [])[:80]:
        if not isinstance(item, dict):
            continue
        units.append({
            "id": str(item.get("id", len(units) + 1)),
            "unit": str(item.get("source_unit", ""))[:120],
            "level": str(item.get("level", "无")),
            "evidence": str(item.get("draft_location", ""))[:80],
        })

    return LlmAuditResult(
        ran=True,
        provider=provider,
        model=model,
        structure_score=s,
        material_score=m,
        composite=composite,
        unit_map=units,
        differentiation_list=[str(x) for x in (data.get("differentiation_list") or [])][:20],
        fix_directive=str(data.get("fix_directive") or ""),
        notes=str(data.get("notes") or ""),
        raw=data,
    )


def merge_with_deterministic(base: SourceAudit, llm: LlmAuditResult) -> SourceAudit:
    """Take the higher structure and material score so a model miss cannot lower risk."""
    if not llm.ran:
        return base

    structure_score = max(base.structure.structure_score, llm.structure_score)
    material_score = max(base.material_score, llm.material_score)
    composite = max(base.composite, llm.composite, composite_score(structure_score, material_score))
    if base.literal.is_copy:
        structure_score = material_score = composite = 100.0

    verdict = "高危" if composite >= HIGH_RISK_THRESHOLD else "安全"
    merged = SourceAudit(
        source_name=base.source_name,
        source_chars=base.source_chars,
        literal=base.literal,
        structure=base.structure,
        material_score=material_score,
        composite=composite,
        verdict=verdict,
        boundary=boundary_label(composite),
        fallback_notice=structure_fallback_notice(structure_score),
        unit_none_count=base.unit_none_count,
        fix_directive=llm.fix_directive or base.fix_directive,
        differentiation_list=(llm.differentiation_list or base.differentiation_list)[:20],
        data_points=base.data_points,
        case_pairs=base.case_pairs,
        entity_counts=base.entity_counts,
    )
    # The model's own unit map is the better evidence table when it ran, but the
    # merged structure score must never fall below either input: swap the table
    # first, then re-derive and re-take the maximum.
    if llm.unit_map:
        merged.structure.unit_map = llm.unit_map
        merged.structure.unit_total = len(llm.unit_map)
        merged.structure.unit_full = sum(1 for u in llm.unit_map if u["level"] == "完整")
        merged.structure.unit_partial = sum(1 for u in llm.unit_map if u["level"] == "部分")
        merged.structure.unit_none = sum(1 for u in llm.unit_map if u["level"] == "无")
        merged.unit_none_count = merged.structure.unit_none
        merged.structure.unit_match_score = max(
            merged.structure.unit_match_score,
            one_decimal(
                (merged.structure.unit_full * 1.0 + merged.structure.unit_partial * 0.5)
                / max(len(llm.unit_map), 1)
                * 100
            ),
        )
    # Re-freeze the composite from the merged parts, then re-take the maximum.
    merged.structure.structure_score = max(merged.structure.structure_score, llm.structure_score)
    merged.composite = max(
        merged.composite,
        composite_score(merged.structure.structure_score, merged.material_score),
    )
    merged.verdict = "高危" if merged.composite >= HIGH_RISK_THRESHOLD else "安全"
    merged.boundary = boundary_label(merged.composite)
    merged.fallback_notice = structure_fallback_notice(merged.structure.structure_score)
    if llm.notes:
        logger.info("通用短篇模型审查备注：%s", llm.notes[:200])
    return merged


def audit_one_source_with_model(
    source_name: str,
    source_text: str,
    draft_text: str,
    *,
    use_model: bool = True,
    caller: Callable[[str], str] | None = None,
    fingerprint=None,
) -> tuple[SourceAudit, LlmAuditResult]:
    """Deterministic audit plus the optional model layer, merged."""
    base = audit_one_source(source_name, source_text, draft_text, fingerprint=fingerprint)
    if not use_model:
        return base, LlmAuditResult(ran=False)
    try:
        llm = run_llm_audit(source_text, draft_text, caller=caller)
    except LlmAuditUnavailable as exc:
        logger.warning("通用短篇模型审查层跳过：%s", exc)
        return base, LlmAuditResult(ran=False)
    return merge_with_deterministic(base, llm), llm
