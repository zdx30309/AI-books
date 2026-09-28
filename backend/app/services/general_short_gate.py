"""Wires the general_short structure fingerprint and differentiation gate.

Two provider-free steps bracket the existing writer:

1. before the writer runs, the inspiration transcript is abstracted into a
   structure fingerprint that the draft stage must avoid;
2. after the final manuscript exists, the same fingerprint scores how much of
   that structure survived, and the report is persisted next to the manuscript.

The gate is measurement-first by default: it always writes a report and records
the verdict, and only stops the run when strict mode is enabled.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from app.core.config import settings
from app.services.general_short_differentiation import (
    HIGH_RISK_THRESHOLD,
    DifferentiationReport,
    evaluate_differentiation,
)
from app.services.general_short_fingerprint import StructureFingerprint, build_fingerprint

logger = logging.getLogger(__name__)

FINGERPRINT_FILENAME = "00_source_fingerprint.json"
REPORT_FILENAME = "05_differentiation_report.json"
STRICT_ENV = "GENERAL_SHORT_DIFFERENTIATION_STRICT"

MANUAL_REVIEW_MESSAGE = (
    "通用短篇差异化审查判定高危（综合相似度 {score}% ≥ {threshold}%）：{directive} "
    "已保留终稿与审查报告，未标记为可发布。"
)


class DifferentiationReviewRequired(RuntimeError):
    """Raised when the manuscript's structure still mirrors the source."""


def strict_mode() -> bool:
    raw = getattr(settings, "GENERAL_SHORT_DIFFERENTIATION_STRICT", False)
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return bool(raw)


def build_source_fingerprint(source_text: str, output_dir: Path | None) -> StructureFingerprint:
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


def run_differentiation_gate(
    source_text: str,
    final_markdown: str,
    *,
    category: str,
    output_dir: Path | None,
    on_stage=None,
    fingerprint: StructureFingerprint | None = None,
) -> DifferentiationReport:
    """Score the finished manuscript against the source structure and persist it."""
    report = evaluate_differentiation(source_text, final_markdown, fingerprint=fingerprint)
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / REPORT_FILENAME).write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if on_stage:
        on_stage(
            "differentiation_done",
            {
                "composite": report.composite,
                "structure": report.structure.structure_score,
                "material": report.material_score,
                "verdict": report.verdict,
            },
        )
    if report.blocked:
        logger.warning(
            "通用短篇差异化审查高危：综合 %.2f%%，结构 %.2f%%，素材 %.2f%%",
            report.composite,
            report.structure.structure_score,
            report.material_score,
        )
        if strict_mode():
            raise DifferentiationReviewRequired(
                MANUAL_REVIEW_MESSAGE.format(
                    score=report.composite,
                    threshold=HIGH_RISK_THRESHOLD,
                    directive=report.fix_directive,
                )
            )
    else:
        logger.info("通用短篇差异化审查通过：综合 %.2f%%", report.composite)
    return report
