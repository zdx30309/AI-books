"""Wires the general_short similarity audit into the generation pipeline.

The audit runs after the finished manuscript exists:

1. the caller supplies every external inspiration transcript (the 青豆 parsed
   text and any additional sources);
2. each source is scored independently by the deterministic core and, when
   enabled, by the model layer, then merged;
3. the highest composite is the gate value;
4. a Word report is written into 04_文本相似度审查报告/, plus a JSON twin for
   automation.

No source supplied => the step is recorded as 不适用 and no report is invented.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from app.core.config import settings
from app.services.general_short_fingerprint import StructureFingerprint, build_fingerprint
from app.services.general_short_llm_audit import audit_one_source_with_model
from app.services.general_short_report import write_report
from app.services.general_short_similarity import (
    HIGH_RISK_THRESHOLD,
    MultiSourceReport,
)

logger = logging.getLogger(__name__)

FINGERPRINT_FILENAME = "00_source_fingerprint.json"
REPORT_FILENAME = "05_differentiation_report.json"

MANUAL_REVIEW_MESSAGE = (
    "通用短篇相似度审查判定高危（最高综合相似度 {score}% ≥ {threshold}%）：{directive} "
    "已保留终稿与审查报告，未标记为可发布。"
)

class DifferentiationReviewRequired(RuntimeError):
    """Raised when the manuscript still mirrors a source."""


def strict_mode() -> bool:
    raw = getattr(settings, "GENERAL_SHORT_DIFFERENTIATION_STRICT", False)
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return bool(raw)


def model_layer_enabled() -> bool:
    return bool(getattr(settings, "GENERAL_SHORT_LLM_AUDIT_ENABLED", True))


def build_source_fingerprint(source_text: str, output_dir: Path | None) -> StructureFingerprint:
    """Abstract the inspiration transcript so the writer knows what to avoid."""
    fingerprint = build_fingerprint(source_text)
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / FINGERPRINT_FILENAME).write_text(
            json.dumps(fingerprint.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    logger.info(
        "通用短篇结构指纹：%s 个论点单元，%s 个案例，领域=%s",
        len(fingerprint.argument_units),
        len(fingerprint.case_sequence),
        fingerprint.domain_guess or "未判定",
    )
    return fingerprint


def _normalize_sources(sources) -> list[tuple[str, str]]:
    """Accept str, a (name, text) pair, or a list of either."""
    if sources is None:
        return []
    if isinstance(sources, str):
        return [("灵感原文", sources)] if sources.strip() else []
    if isinstance(sources, tuple) and len(sources) == 2:
        return [(str(sources[0]), str(sources[1]))] if str(sources[1]).strip() else []
    out: list[tuple[str, str]] = []
    for item in sources:
        if isinstance(item, str):
            if item.strip():
                out.append((f"来源{len(out) + 1}", item))
        elif isinstance(item, (tuple, list)) and len(item) == 2 and str(item[1]).strip():
            out.append((str(item[0]), str(item[1])))
    return out


def run_differentiation_gate(
    sources,
    final_markdown: str,
    *,
    category: str,
    output_dir: Path | None,
    on_stage=None,
    fingerprint: StructureFingerprint | None = None,
    book_title: str = "",
    draft_name: str = "",
    use_model: bool | None = None,
) -> MultiSourceReport:
    """Score the finished manuscript against every source and persist the report."""
    real = _normalize_sources(sources)
    report = MultiSourceReport(draft_name=draft_name or book_title)
    if not real:
        report.applicable = False
        report.reason = "本轮没有用户提供的外部灵感原文，相似度审查不适用。"
        logger.info("通用短篇相似度审查：不适用（无外部灵感原文）")
        _persist(report, output_dir)
        if on_stage:
            on_stage("differentiation_done", {"applicable": False})
        return report

    report.applicable = True
    enable_model = model_layer_enabled() if use_model is None else bool(use_model)

    for name, text in real:
        fp = fingerprint if (fingerprint is not None and len(real) == 1) else build_fingerprint(text)
        audit, llm = audit_one_source_with_model(
            name, text, final_markdown, use_model=enable_model, fingerprint=fp
        )
        report.sources.append(audit)
        logger.info(
            "来源《%s》：S=%.1f M=%.1f 综合=%.1f%%（模型层=%s）",
            name, audit.structure.structure_score, audit.material_score,
            audit.composite, "启用" if llm.ran else "跳过",
        )

    report.max_composite = max(s.composite for s in report.sources)
    report.overall_verdict = "高危" if report.max_composite >= HIGH_RISK_THRESHOLD else "安全"
    worst = max(report.sources, key=lambda s: s.composite)
    report.overall_boundary = worst.boundary
    report.overall_fallback_notice = next(
        (s.fallback_notice for s in report.sources if s.fallback_notice), ""
    )

    _persist(report, output_dir, book_title=book_title, draft_name=draft_name)

    if on_stage:
        on_stage("differentiation_done", {
            "applicable": True,
            "composite": report.max_composite,
            "verdict": report.overall_verdict,
            "sources": len(report.sources),
        })

    if report.overall_verdict == "高危":
        logger.warning("通用短篇相似度审查高危：最高综合 %.1f%%", report.max_composite)
        if strict_mode():
            raise DifferentiationReviewRequired(
                MANUAL_REVIEW_MESSAGE.format(
                    score=report.max_composite,
                    threshold=HIGH_RISK_THRESHOLD,
                    directive=worst.fix_directive,
                )
            )
    else:
        logger.info("通用短篇相似度审查通过：最高综合 %.1f%%", report.max_composite)
    return report


def _persist(
    report: MultiSourceReport,
    output_dir: Path | None,
    *,
    book_title: str = "",
    draft_name: str = "",
) -> None:
    if output_dir is None:
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / REPORT_FILENAME).write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if report.applicable:
        path = write_report(output_dir, report, book_title=book_title, draft_name=draft_name)
        logger.info("相似度审查报告已生成：%s", path)
