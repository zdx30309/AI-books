"""Manual, report-driven Zhuque review workflow.

This module is deliberately independent from the automatic AIGC final gate.  It
does not mutate tasks or historical manuscripts.  A user supplies the editable
original plus a real Zhuque PDF, then explicitly chooses report segments to
rewrite and re-check.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import re
import statistics
import threading
import unicodedata
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import UUID, uuid4

import pdfplumber
import httpx
from openai import OpenAI

from app.core.config import PROJECT_ROOT, settings
from app.services.aigc_style_probe import (
    STYLE_FEATURE_VERSION,
    profile_style,
    style_feature_delta,
    summarize_feature_effects,
)
from app.services.deepseek import get_deepseek_client
from app.services.gemini import GeminiMaxTokensError
from app.services.gemini import generate_text as generate_gemini_text
from app.utils.distributed_limiter import distributed_semaphore

logger = logging.getLogger(__name__)

PROMPT_VERSION = "zhuque-report-review-v5.4"
PROBE_PROMPT_VERSION = "zhuque-detector-full-method-v5.0"
LOCAL_FACT_LEDGER_MODEL = "local-deterministic-v2"
HUMAN_PASS_THRESHOLD = 25.0
# Backwards-compatible export name used by older callers/tests.  v5 is inclusive.
STRICT_HUMAN_THRESHOLD = HUMAN_PASS_THRESHOLD
MATERIAL_SIMILARITY_MIN = 0.25
MATERIAL_SIMILARITY_MAX = 0.82
MATERIAL_PARAGRAPH_COVERAGE_MIN = 0.80
QUALITY_SAFE_LENGTH_MIN = 0.95
QUALITY_SAFE_LENGTH_MAX = 1.08
DEEP_RECAST_SCOPE_MIN = 0.75
DEEP_RECAST_SCOPE_TARGET = 0.80
DEEP_RECAST_SCOPE_MAX = 0.85
DEEP_RECAST_UNIT_MIN_CHARS = 600
DEEP_RECAST_UNIT_MAX_CHARS = 900
DETECTOR_PROBE_TEMPERATURE = 0.8
_PROMPT_DIR = PROJECT_ROOT / "backend" / "app" / "prompts"
_REWRITE_PROMPT_PATH = _PROMPT_DIR / "aigc_report_rewrite.md"
_FACT_LEDGER_PROMPT_PATH = _PROMPT_DIR / "aigc_report_fact_ledger.md"
_REAUTHOR_PROMPT_PATH = _PROMPT_DIR / "aigc_report_reauthor.md"
_AUDIT_PROMPT_PATH = _PROMPT_DIR / "aigc_report_quality_audit.md"
_CONTENT_RULES_PATH = _PROMPT_DIR / "aigc_report_content_rules.json"
_STRATEGY_RULES_PATH = _PROMPT_DIR / "aigc_report_rewrite_strategies.json"
_DIAGNOSTIC_RULES_PATH = _PROMPT_DIR / "aigc_report_diagnostic_rules.json"
_DETECTOR_PROBE_PROMPT_PATH = _PROMPT_DIR / "aigc_detector_probe.md"
_DETECTOR_PROBE_PROFILES_PATH = _PROMPT_DIR / "aigc_detector_probe_profiles.json"
_PERSISTENT_REVIEW_ROOT = PROJECT_ROOT / "backend" / "output" / "_aigc_review"
_EXPERIMENT_DIR = _PERSISTENT_REVIEW_ROOT / "experiments"
_CALIBRATION_DIR = _PERSISTENT_REVIEW_ROOT / "calibrations"
_FACT_LEDGER_CACHE_DIR = _PERSISTENT_REVIEW_ROOT / "fact_ledgers"
_LEGACY_EXPERIMENT_DIR = PROJECT_ROOT / ".local" / "aigc_review_experiments"
_OCR_LOCK = threading.Lock()


class ZhuqueReportError(ValueError):
    """Raised when a supplied report cannot be safely parsed or mapped."""


class AigcReviewModelError(RuntimeError):
    """Raised when the rewrite or independent audit model is unavailable."""


@dataclass(frozen=True)
class ZhuqueSegment:
    segment_id: int
    share_percent: float
    char_count: int
    aigc_value: float
    label: str
    start: int
    end: int
    mapped_text: str
    mapping_confidence: float
    recommended: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeterministicReview:
    passed: bool
    length_ratio: float
    paragraph_count_preserved: bool
    headings_preserved: bool
    anchors_preserved: bool
    protected_text_preserved: bool
    model_explanation_absent: bool
    hard_error_absent: bool
    material_rewrite_passed: bool
    literal_similarity: float
    rewritten_paragraph_ratio: float
    required_paragraph_coverage: float
    unchanged_editable_paragraphs: int
    editable_paragraphs: int
    missing_anchors: tuple[str, ...]
    extra_anchors: tuple[str, ...]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["missing_anchors"] = list(self.missing_anchors)
        payload["extra_anchors"] = list(self.extra_anchors)
        payload["reasons"] = list(self.reasons)
        return payload


@dataclass(frozen=True)
class RewriteProtocol:
    """Machine-checkable paragraph and immutable-anchor protocol for one segment."""

    pieces: tuple[str, ...]
    block_piece_indexes: tuple[int, ...]
    block_payloads: tuple[str, ...]
    protected_values: tuple[tuple[str, str], ...]
    protected_block_indexes: tuple[tuple[str, int], ...] = ()
    protected_heading_tokens: tuple[str, ...] = ()
    flexible_paragraphs: bool = False

    @property
    def prompt_segment(self) -> str:
        blocks: list[str] = []
        for index, value in enumerate(self.block_payloads, start=1):
            blocks.append(f"[[P{index:04d}]]\n{value}\n[[/P{index:04d}]]")
        return "\n".join(blocks)

    @property
    def protected_map(self) -> str:
        if not self.protected_values:
            return "（无）"
        return "\n".join(
            f"{token} = {json.dumps(value, ensure_ascii=False)}"
            for token, value in self.protected_values
        )


@dataclass(frozen=True)
class EditorialUnit:
    unit_index: int
    start: int
    end: int
    text: str

    @property
    def unit_id(self) -> str:
        return f"U{self.unit_index:02d}"


def parse_zhuque_report(
    pdf_bytes: bytes,
    original_text: str,
    *,
    filename: str = "zhuque-report.pdf",
    target_human_percent: float | None = None,
) -> dict[str, Any]:
    original = normalize_manuscript_text(original_text)
    if not original:
        raise ZhuqueReportError("必须同时提供送检时使用的可编辑原稿；仅靠图片型报告无法安全还原全文。")
    if len(original) > int(settings.AIGC_REVIEW_MAX_MANUSCRIPT_CHARS):
        raise ZhuqueReportError(
            f"原稿超过 {settings.AIGC_REVIEW_MAX_MANUSCRIPT_CHARS:,} 字，请拆成多份报告处理。"
        )
    if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
        raise ZhuqueReportError("上传文件不是有效 PDF。请使用朱雀打印页面导出的 PDF。")
    if len(pdf_bytes) > int(settings.AIGC_REVIEW_MAX_PDF_BYTES):
        raise ZhuqueReportError("报告 PDF 过大，请压缩到 30MB 以内。")

    report_text, extraction_method, page_count = extract_report_text(pdf_bytes)
    overview = parse_report_overview(report_text)
    raw_rows = parse_report_segment_rows(report_text)
    if not raw_rows:
        raise ZhuqueReportError(
            "没有识别到朱雀片段表。请确认 PDF 包含首页片段表，并使用浏览器打印为 PDF。"
        )
    overview = resolve_report_overview_from_chart(report_text, overview, raw_rows)

    target = float(target_human_percent or settings.AIGC_REVIEW_HUMAN_TARGET)
    target = min(90.0, max(HUMAN_PASS_THRESHOLD, target))
    mapped = map_segments_to_original(raw_rows, original)
    table_share_total = sum(item.share_percent for item in mapped)
    table_is_complete = table_share_total >= 98.0
    derived_human = _human_share_from_segments(mapped)
    derived_suspected = sum(item.share_percent for item in mapped if item.label == "suspected")
    derived_ai = sum(item.share_percent for item in mapped if item.label == "ai")
    overview_verified = report_overview_verified(overview)
    official_human = _optional_float(overview.get("human_percent"))
    official_suspected = _optional_float(overview.get("suspected_percent"))
    official_ai = _optional_float(overview.get("ai_percent"))
    # The overview and row table are different Tencent outputs.  The official
    # overview controls delivery when it is internally consistent; row-derived
    # shares remain diagnostics and must never silently overwrite it.
    human_percent = official_human if overview_verified else derived_human
    suspected_percent = official_suspected if overview_verified else derived_suspected
    ai_percent = official_ai if overview_verified else derived_ai
    selected_ids, projected = recommend_calibration_segments(
        mapped,
        human_percent=human_percent,
    )
    selected_set = set(selected_ids)
    segments = [
        ZhuqueSegment(**{**segment.to_dict(), "recommended": segment.segment_id in selected_set})
        for segment in mapped
    ]

    warnings: list[str] = []
    if not overview_verified:
        warnings.append("朱雀首页三项比例未完成可靠识别；表格推导值仅供定位，不能解锁终稿。")
    elif abs(float(official_human) - derived_human) >= 1.0:
        warnings.append(
            "朱雀首页人工特征与片段表推导占比不同；系统保留双指标，并以首页官方值验收。"
        )
    source_match_confidence, source_match_sample_chars = report_source_match_confidence(
        report_text,
        original,
    )
    source_match_verified = source_match_sample_chars >= 60 and source_match_confidence >= 0.75
    if not source_match_verified:
        warnings.append("报告正文抽样与原稿未完成可靠匹配；复检报告在匹配成功前不能解锁终稿。")
    expected_chars = sum(item.char_count for item in segments)
    nonspace_chars = sum(1 for char in original if not char.isspace())
    if expected_chars and abs(expected_chars - nonspace_chars) / max(expected_chars, nonspace_chars) > 0.12:
        warnings.append("报告字符数与粘贴原稿差异超过 12%，请确认原稿就是本次送检版本。")
    if min((item.mapping_confidence for item in segments), default=0.0) < 0.7:
        warnings.append("片段映射置信度偏低，执行前请逐块核对片段预览。")

    return {
        "policy_version": "report-driven-v5",
        "prompt_version": PROMPT_VERSION,
        "filename": filename,
        "report_sha256": _sha256_bytes(pdf_bytes),
        "original_sha256": sha256_text(original),
        "extraction_method": extraction_method,
        "page_count": page_count,
        "human_percent": round(human_percent, 2),
        "suspected_percent": round(suspected_percent, 2),
        "ai_percent": round(ai_percent, 2),
        "official_human_percent": round(official_human, 2) if official_human is not None else None,
        "official_suspected_percent": round(official_suspected, 2) if official_suspected is not None else None,
        "official_ai_percent": round(official_ai, 2) if official_ai is not None else None,
        "official_overview_verified": overview_verified,
        "derived_human_share_percent": round(derived_human, 2),
        "derived_suspected_share_percent": round(derived_suspected, 2),
        "derived_ai_share_percent": round(derived_ai, 2),
        "pass_metric_source": "official_overview" if overview_verified else "unverified",
        "strict_pass": bool(overview_verified and human_percent >= HUMAN_PASS_THRESHOLD),
        "source_match_verified": source_match_verified,
        "source_match_confidence": round(source_match_confidence, 4),
        "source_match_sample_chars": source_match_sample_chars,
        "strict_threshold": HUMAN_PASS_THRESHOLD,
        "target_human_percent": target,
        "projected_human_percent": round(projected, 2),
        "recommendation_mode": "calibration",
        "calibration_share_percent": round(
            sum(item.share_percent for item in mapped if item.segment_id in selected_set),
            2,
        ),
        "recommended_segment_ids": selected_ids,
        "segments": [item.to_dict() for item in segments],
        "warnings": warnings,
        "model_strategy": model_strategy(),
    }


def extract_report_text(pdf_bytes: bytes) -> tuple[str, str, int]:
    """Use embedded text when available; otherwise run entirely local OCR."""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            page_count = len(pdf.pages)
            if page_count > int(settings.AIGC_REVIEW_MAX_PDF_PAGES):
                raise ZhuqueReportError(
                    f"报告共 {page_count} 页，超过 {settings.AIGC_REVIEW_MAX_PDF_PAGES} 页安全上限。"
                )
            embedded = "\n\n".join((page.extract_text() or "") for page in pdf.pages).strip()
    except ZhuqueReportError:
        raise
    except Exception as exc:
        raise ZhuqueReportError(f"PDF 读取失败: {exc}") from exc

    if len(re.findall(r"[\u4e00-\u9fff]", embedded)) >= 80 and parse_report_segment_rows(embedded):
        return embedded, "embedded_text", page_count
    return _ocr_pdf_locally(pdf_bytes, page_count), "local_rapidocr", page_count


def _ocr_pdf_locally(pdf_bytes: bytes, page_count: int) -> str:
    try:
        import numpy as np
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise ZhuqueReportError(
            "服务器缺少本地 PDF 渲染依赖，请安装 pypdfium2、numpy 和 rapidocr-onnxruntime。"
        ) from exc

    engine = _get_ocr_engine()
    try:
        pdf = pdfium.PdfDocument(pdf_bytes)
        scan_pages = min(page_count, max(1, int(settings.AIGC_REVIEW_OCR_MAX_SCAN_PAGES)))
        scales = list(
            dict.fromkeys(
                (
                    float(settings.AIGC_REVIEW_OCR_SCALE),
                    float(settings.AIGC_REVIEW_OCR_RETRY_SCALE),
                )
            )
        )
        best_text = ""
        best_score = (-1, -1, -1)
        for scale in scales:
            pages: list[str] = []
            for page_index in range(scan_pages):
                page = pdf[page_index]
                bitmap = page.render(scale=scale, rotation=0)
                image = bitmap.to_pil().convert("RGB")
                with _OCR_LOCK:
                    raw = engine(np.asarray(image))
                result = raw[0] if isinstance(raw, tuple) else raw
                pages.append(_ordered_ocr_text(result))
                image.close()
                bitmap.close()
                page.close()
                candidate = "\n\n".join(pages).strip()
                rows = parse_report_segment_rows(candidate)
                overview = parse_report_overview(candidate)
                overview_count = sum(value is not None for value in overview.values())
                score = (len(rows), overview_count, len(candidate))
                if score > best_score:
                    best_score = score
                    best_text = candidate
                share_total = sum(float(item["share_percent"]) for item in rows)
                if rows and share_total >= 98.0:
                    pdf.close()
                    return candidate
        pdf.close()
        text = best_text
        if not text:
            raise ZhuqueReportError("本地 OCR 没有识别到文字。")
        return text
    except ZhuqueReportError:
        raise
    except Exception as exc:
        raise ZhuqueReportError(f"本地 OCR 失败: {exc}") from exc


@lru_cache(maxsize=1)
def _get_ocr_engine():
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise ZhuqueReportError(
            "服务器缺少本地 OCR 依赖 rapidocr-onnxruntime；该依赖不产生任何模型 API 费用。"
        ) from exc
    return RapidOCR()


def _ordered_ocr_text(result: Any) -> str:
    if not result:
        return ""
    items: list[tuple[float, float, str]] = []
    for item in result:
        try:
            box, value = item[0], str(item[1]).strip()
            if not value:
                continue
            center_y = sum(float(point[1]) for point in box) / len(box)
            left_x = min(float(point[0]) for point in box)
            items.append((center_y, left_x, value))
        except Exception:
            continue
    items.sort(key=lambda item: (round(item[0] / 12.0), item[1]))
    return "\n".join(item[2] for item in items)


def parse_report_overview(report_text: str) -> dict[str, float | None]:
    text = _normalize_ocr_text(report_text)
    return {
        "human_percent": _first_percent(text, (r"人工特征\s*([0-9]+(?:\.[0-9]+)?)\s*%",)),
        "suspected_percent": _first_percent(
            text,
            (r"疑似\s*AI(?:特征)?\s*([0-9]+(?:\.[0-9]+)?)\s*%", r"疑似AI\s*([0-9.]+)\s*%"),
        ),
        "ai_percent": _first_percent(text, (r"(?<!疑似)AI特征\s*([0-9]+(?:\.[0-9]+)?)\s*%",)),
    }


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def report_overview_verified(overview: dict[str, Any]) -> bool:
    """Accept the page-one metric only when all three percentages form one chart."""
    values = [
        _optional_float(overview.get("human_percent")),
        _optional_float(overview.get("suspected_percent")),
        _optional_float(overview.get("ai_percent")),
    ]
    if any(value is None or value < 0 or value > 100 for value in values):
        return False
    return abs(sum(float(value) for value in values) - 100.0) <= 1.0


def resolve_report_overview_from_chart(
    report_text: str,
    overview: dict[str, float | None],
    rows: Iterable[dict[str, Any]],
) -> dict[str, float | None]:
    """Map visually separated chart values to labels without replacing official values.

    Image PDFs often OCR the three chart percentages before their labels.  The row
    table is used only to choose the most plausible permutation of those three
    official chart numbers; row-derived shares never replace a chart number.
    """
    if report_overview_verified(overview):
        return overview
    text = _normalize_ocr_text(report_text)
    chart = re.split(r"(?:AI分布图|片段解析)", text, maxsplit=1)[0]
    candidates: list[float] = []
    for raw in re.findall(r"(?<![0-9])([0-9]{1,3}(?:\.[0-9]+)?)\s*%", chart):
        value = float(raw)
        if 0.0 <= value <= 100.0 and value not in candidates:
            candidates.append(value)
    if len(candidates) < 3:
        return overview
    row_values = list(rows)
    derived = {
        "human_percent": sum(
            float(item.get("share_percent") or 0.0)
            for item in row_values
            if str(item.get("label") or "") == "human"
        ),
        "suspected_percent": sum(
            float(item.get("share_percent") or 0.0)
            for item in row_values
            if str(item.get("label") or "") == "suspected"
        ),
        "ai_percent": sum(
            float(item.get("share_percent") or 0.0)
            for item in row_values
            if str(item.get("label") or "") == "ai"
        ),
    }
    keys = ("human_percent", "suspected_percent", "ai_percent")
    best: tuple[float, tuple[float, float, float]] | None = None
    from itertools import combinations, permutations

    for trio in combinations(candidates, 3):
        if abs(sum(trio) - 100.0) > 1.0:
            continue
        for assignment in permutations(trio):
            distance = sum(
                (float(assignment[index]) - float(derived[key])) ** 2
                for index, key in enumerate(keys)
            )
            if best is None or distance < best[0]:
                best = (distance, assignment)
    if best is None:
        return overview
    return {
        key: round(float(best[1][index]), 4)
        for index, key in enumerate(keys)
    }


def parse_report_segment_rows(report_text: str) -> list[dict[str, Any]]:
    text = _normalize_ocr_text(report_text)
    pattern = re.compile(
        r"(?:片段|片断)\s*(\d{1,3})\s+"
        r"([0-9]+(?:\.[0-9]+)?)\s*%\s+"
        r"([0-9]{2,7})\s+"
        r"([01](?:\.[0-9]+)?)",
        re.IGNORECASE,
    )
    found: dict[int, dict[str, Any]] = {}
    for match in pattern.finditer(text):
        segment_id = int(match.group(1))
        if segment_id in found:
            continue
        value = float(match.group(4))
        found[segment_id] = {
            "segment_id": segment_id,
            "share_percent": float(match.group(2)),
            "char_count": int(match.group(3)),
            "aigc_value": value,
            "label": classify_aigc_value(value),
        }
    return [found[key] for key in sorted(found)]


def classify_aigc_value(value: float) -> str:
    if value >= 0.99:
        return "ai"
    if value >= 0.5:
        return "suspected"
    return "human"


def report_source_match_confidence(report_text: str, original_text: str) -> tuple[float, int]:
    """Fuzzy-match the first detail card so a wrong report cannot unlock export."""
    marker = re.search(r"NO\s*\.?\s*1\b", report_text or "", flags=re.IGNORECASE)
    if not marker:
        return 0.0, 0
    tail = report_text[marker.end():]
    value_marker = re.search(
        r"AIGC\s*值?\s*[:：]?\s*[01](?:\.[0-9]+)?",
        tail,
        flags=re.IGNORECASE,
    )
    if value_marker:
        tail = tail[value_marker.end():]
    tail = re.split(r"https?\s*[:：/]", tail, maxsplit=1, flags=re.IGNORECASE)[0]
    normalized_sample = re.sub(r"[^\u3400-\u9fffA-Za-z0-9]", "", tail)[:500]
    normalized_original = re.sub(r"[^\u3400-\u9fffA-Za-z0-9]", "", original_text or "")
    if len(normalized_sample) < 40 or not normalized_original:
        return 0.0, len(normalized_sample)
    matcher = SequenceMatcher(None, normalized_sample, normalized_original, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return min(1.0, matched / len(normalized_sample)), len(normalized_sample)


def map_segments_to_original(rows: Iterable[dict[str, Any]], original_text: str) -> list[ZhuqueSegment]:
    ordered = sorted(rows, key=lambda item: int(item["segment_id"]))
    if not ordered:
        return []
    positions = [index for index, char in enumerate(original_text) if not char.isspace()]
    expected_total = sum(max(0, int(item["char_count"])) for item in ordered)
    if not positions or expected_total <= 0:
        raise ZhuqueReportError("原稿或报告片段字符数无效，无法建立安全映射。")

    actual_total = len(positions)
    confidence = max(0.0, 1.0 - abs(actual_total - expected_total) / max(actual_total, expected_total))
    cumulative = 0
    proportional_boundaries = [0]
    for item in ordered[:-1]:
        cumulative += int(item["char_count"])
        unit_index = min(actual_total - 1, max(0, round(cumulative / expected_total * actual_total)))
        proportional_boundaries.append(positions[unit_index])
    proportional_boundaries.append(len(original_text))
    raw_boundaries = _snap_boundaries_to_paragraphs(
        original_text,
        proportional_boundaries,
    )

    mapped: list[ZhuqueSegment] = []
    for index, item in enumerate(ordered):
        start = raw_boundaries[index]
        end = raw_boundaries[index + 1]
        if end <= start:
            raise ZhuqueReportError("片段映射发生重叠，请确认原稿与报告版本一致。")
        mapped.append(
            ZhuqueSegment(
                segment_id=int(item["segment_id"]),
                share_percent=float(item["share_percent"]),
                char_count=int(item["char_count"]),
                aigc_value=float(item["aigc_value"]),
                label=str(item.get("label") or classify_aigc_value(float(item["aigc_value"]))),
                start=start,
                end=end,
                mapped_text=original_text[start:end],
                mapping_confidence=round(confidence, 4),
            )
        )
    return mapped


def _snap_boundaries_to_paragraphs(text: str, boundaries: list[int]) -> list[int]:
    """Keep report-position mapping, but avoid cutting editable prose mid-paragraph.

    Zhuque exposes row sizes rather than exact character offsets. Proportional offsets
    are therefore estimates. Rewriting from a mid-sentence estimate was preserving the
    detector's larger rhetorical skeleton and occasionally produced awkward seams.
    Snapping only nearby internal offsets to existing paragraph boundaries gives the
    rewrite model complete editorial units without pretending to know Tencent's hidden
    segmentation.
    """
    if len(boundaries) <= 2:
        return boundaries
    paragraph_starts = sorted(
        {
            match.end()
            for match in re.finditer(r"\n[\t ]*(?:\n[\t ]*)*", text)
            if 0 < match.end() < len(text)
        }
    )
    if not paragraph_starts:
        return boundaries
    snapped = [0]
    for index, raw in enumerate(boundaries[1:-1], start=1):
        previous_raw = boundaries[index - 1]
        next_raw = boundaries[index + 1]
        local_span = max(1, min(raw - previous_raw, next_raw - raw))
        tolerance = min(180, max(24, int(local_span * 0.35)))
        candidates = [
            value
            for value in paragraph_starts
            if snapped[-1] < value < next_raw and abs(value - raw) <= tolerance
        ]
        candidate = min(candidates, key=lambda value: abs(value - raw)) if candidates else raw
        if candidate <= snapped[-1]:
            candidate = raw
        snapped.append(candidate)
    snapped.append(len(text))
    return snapped


def recommend_target_segments(
    segments: Iterable[ZhuqueSegment],
    *,
    human_percent: float,
    target_human_percent: float,
) -> tuple[list[int], float]:
    projected = float(human_percent)
    if projected > STRICT_HUMAN_THRESHOLD and projected >= target_human_percent:
        return [], projected
    candidates = [item for item in segments if item.label != "human"]
    candidates.sort(
        key=lambda item: (
            0 if item.label == "suspected" else 1,
            item.aigc_value,
            -item.share_percent,
            item.segment_id,
        )
    )
    selected: list[int] = []
    for item in candidates:
        selected.append(item.segment_id)
        projected += item.share_percent
        if projected >= target_human_percent:
            break
    return selected, min(100.0, projected)


def recommend_calibration_segments(
    segments: Iterable[ZhuqueSegment],
    *,
    human_percent: float,
    target_share_percent: float = 30.0,
    max_share_percent: float = 40.0,
    max_segments: int = 12,
) -> tuple[list[int], float]:
    """Choose one contiguous, high-leverage experiment region.

    The SaaS pair proves that report boundaries shift after editing.  v5 therefore
    tests an adjacent macro-region covering roughly one third of the document instead
    of forcing an arbitrary three-row cap or pretending each selected row turns green.
    """
    if human_percent >= HUMAN_PASS_THRESHOLD:
        return [], human_percent
    risks = sorted(
        (item for item in segments if item.label != "human"),
        key=lambda item: item.segment_id,
    )
    if not risks:
        return [], human_percent
    target_mid = (target_share_percent + max_share_percent) / 2.0
    windows: list[tuple[float, list[ZhuqueSegment]]] = []
    for start in range(len(risks)):
        window: list[ZhuqueSegment] = []
        share = 0.0
        expected_id = risks[start].segment_id
        for item in risks[start:start + max_segments]:
            if window and item.segment_id != expected_id:
                break
            window.append(item)
            share += item.share_percent
            expected_id = item.segment_id + 1
            if share >= target_share_percent or (len(window) == 1 and share >= HUMAN_PASS_THRESHOLD):
                average_risk = sum(value.aigc_value for value in window) / len(window)
                overflow_penalty = max(0.0, share - max_share_percent) * 3.0
                score = abs(share - target_mid) + overflow_penalty + (average_risk * 0.25)
                windows.append((score, list(window)))
                break
    if windows:
        chosen = min(windows, key=lambda item: (item[0], len(item[1]), item[1][0].segment_id))[1]
    else:
        chosen = [max(risks, key=lambda item: (item.share_percent, -item.aigc_value))]
    selected = [item.segment_id for item in chosen]
    selected_share = sum(item.share_percent for item in chosen)
    return selected, min(100.0, human_percent + selected_share)


def compare_zhuque_reports(
    baseline_report: dict[str, Any],
    recheck_report: dict[str, Any],
    optimize_result: dict[str, Any],
) -> dict[str, Any]:
    """Attribute a real Zhuque re-check delta to each experimental rewrite strategy.

    Report row boundaries commonly shift after a rewrite.  Alignment is therefore
    calculated on normalized manuscript positions instead of row numbers.  Only
    hashes, scores and aggregate experiment metadata are persisted; manuscript text
    and PDF contents are deliberately excluded.
    """
    baseline_hash = str(baseline_report.get("original_sha256") or "")
    recheck_hash = str(recheck_report.get("original_sha256") or "")
    if baseline_hash != str(optimize_result.get("original_sha256") or ""):
        raise ZhuqueReportError("首次报告与本轮不可变原稿不匹配，禁止归因。")
    if recheck_hash != str(optimize_result.get("candidate_sha256") or ""):
        raise ZhuqueReportError("复检报告与本轮候选稿不匹配，禁止归因。")
    if not recheck_report.get("source_match_verified"):
        raise ZhuqueReportError("复检报告尚未确认与候选同稿，禁止学习错误反馈。")
    baseline_segments = list(baseline_report.get("segments") or [])
    recheck_segments = list(recheck_report.get("segments") or [])
    if not baseline_segments or not recheck_segments:
        raise ZhuqueReportError("首次报告或复检报告缺少片段数据。")
    baseline_length = max(int(item.get("end") or 0) for item in baseline_segments)
    recheck_length = max(int(item.get("end") or 0) for item in recheck_segments)
    if baseline_length <= 0 or recheck_length <= 0:
        raise ZhuqueReportError("报告片段坐标无效，无法比较。")

    selected = {int(value) for value in optimize_result.get("selected_segment_ids") or []}
    result_by_segment = {
        int(item.get("segment_id")): item
        for item in optimize_result.get("segment_results") or []
    }
    strategy_by_segment = {
        segment_id: str(item.get("strategy_id") or "unknown")
        for segment_id, item in result_by_segment.items()
    }
    rows: list[dict[str, Any]] = []
    for baseline in baseline_segments:
        segment_id = int(baseline.get("segment_id") or 0)
        after_score = _normalized_overlap_score(
            baseline,
            baseline_length,
            recheck_segments,
            recheck_length,
        )
        before_score = float(baseline.get("aigc_value") or 0.0)
        delta = after_score - before_score
        before_label = classify_aigc_value(before_score)
        after_label = classify_aigc_value(after_score)
        became_human = before_label != "human" and after_label == "human"
        rows.append(
            {
                "segment_id": segment_id,
                "selected": segment_id in selected,
                "strategy_id": strategy_by_segment.get(segment_id),
                "share_percent": round(float(baseline.get("share_percent") or 0.0), 4),
                "before_aigc_value": round(before_score, 6),
                "after_aigc_value": round(after_score, 6),
                "before_label": before_label,
                "after_label": after_label,
                "became_human": became_human,
                "delta": round(delta, 6),
                "verdict": _delta_verdict(delta),
                "style_feature_version": result_by_segment.get(segment_id, {}).get(
                    "style_feature_version"
                ),
                "style_feature_delta": result_by_segment.get(segment_id, {}).get(
                    "style_feature_delta"
                ) or {},
            }
        )

    strategy_groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        strategy_id = row.get("strategy_id")
        if row["selected"] and strategy_id:
            strategy_groups.setdefault(str(strategy_id), []).append(row)
    effectiveness: list[dict[str, Any]] = []
    for strategy_id, group in strategy_groups.items():
        total_share = sum(float(row["share_percent"]) for row in group) or float(len(group))
        weighted_delta = sum(
            float(row["delta"]) * (float(row["share_percent"]) or 1.0)
            for row in group
        ) / total_share
        humanized_share = sum(
            float(row["share_percent"])
            for row in group
            if row.get("became_human")
        )
        effectiveness.append(
            {
                "strategy_id": strategy_id,
                "segment_ids": [int(row["segment_id"]) for row in group],
                "weighted_delta": round(weighted_delta, 6),
                "humanized_share_percent": round(humanized_share, 4),
                "green_conversion_rate": round(humanized_share / total_share, 6),
                "verdict": _delta_verdict(weighted_delta),
            }
        )
    effectiveness.sort(
        key=lambda item: (
            -float(item.get("green_conversion_rate") or 0.0),
            float(item["weighted_delta"]),
        )
    )
    # A whole-document report is useful for the delivery gate, but proportional
    # overlap cannot prove which local rewrite caused a score change.  Keep it as
    # an observation and require isolated exact-hash samples for learning.
    recommended_next_strategy = None
    probe_mode = optimize_result.get("experiment_type") == "detector_probe"
    experimental_quality_bypassed = bool(
        optimize_result.get("experimental_quality_bypassed")
    )
    quality_pass = quality_audit_passed(optimize_result.get("quality_audit"))
    detector_pass = bool(recheck_report.get("strict_pass"))
    strict_pass = detector_pass and quality_pass
    if probe_mode:
        recommendation_reason = (
            "探索实验命中：官方首页人工特征已达到 25%；该结果只证明本组操作值得继续隔离验证，因未运行质量终审仍不能作为终稿。"
            if detector_pass
            else "探索实验未命中：官方首页人工特征仍低于 25%；停止自动追加费用，不把这组网传方法当成已证实规律。"
        )
    else:
        recommendation_reason = (
            "官方首页人工特征已达到 25%，且质量硬闸门通过。"
            if strict_pass
            else (
                "复检只接受官方首页人工特征达到 25% 且质量硬闸门通过；"
                "整稿报告的归一化位置对齐只作观察，不自动扩大付费实验。"
            )
        )

    if probe_mode:
        if detector_pass and experimental_quality_bypassed:
            recommendation_reason = (
                "检测指标命中：完整方法实验使官方首页人工特征达到 25%。"
                "本候选故意未运行质量终审，只能作为后续质量修复的低检测基底，不能直接交付。"
            )
        elif detector_pass and quality_pass:
            recommendation_reason = (
                "强力组合实验命中：官方首页人工特征达到 25%，且事实与文稿质量终审通过。"
                "该结果只能证明整组操作在本篇有效，无法归因某一个变量；保留人工确认，不自动晋升为通用终稿策略。"
            )
        elif detector_pass:
            recommendation_reason = "检测指标命中，但质量终审没有通过；继续保留原稿，不交付这份候选。"
        else:
            recommendation_reason = (
                "完整方法实验未命中：官方首页人工特征仍低于 25%。"
                "停止自动追加费用，不把本轮结果冒充已证实规律。"
            )

    selected_rows = [row for row in rows if row["selected"]]
    comparison_id = str(uuid4())
    result = {
        "comparison_id": comparison_id,
        "request_id": optimize_result.get("request_id"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "baseline_report_sha256": baseline_report.get("report_sha256"),
        "recheck_report_sha256": recheck_report.get("report_sha256"),
        "baseline_human_percent": round(float(baseline_report.get("human_percent") or 0.0), 2),
        "recheck_human_percent": round(float(recheck_report.get("human_percent") or 0.0), 2),
        "baseline_official_human_percent": baseline_report.get("official_human_percent"),
        "recheck_official_human_percent": recheck_report.get("official_human_percent"),
        "baseline_derived_human_share_percent": baseline_report.get(
            "derived_human_share_percent"
        ),
        "recheck_derived_human_share_percent": recheck_report.get(
            "derived_human_share_percent"
        ),
        "official_overview_verified": recheck_report.get("official_overview_verified") is True,
        "human_pass_threshold": HUMAN_PASS_THRESHOLD,
        "human_percent_delta": round(
            float(recheck_report.get("human_percent") or 0.0)
            - float(baseline_report.get("human_percent") or 0.0),
            2,
        ),
        "baseline_weighted_aigc": round(_report_weighted_aigc(baseline_segments), 6),
        "recheck_weighted_aigc": round(_report_weighted_aigc(recheck_segments), 6),
        "selected_weighted_delta": round(_weighted_row_delta(selected_rows), 6),
        "selected_humanized_share_percent": round(
            sum(
                float(row["share_percent"])
                for row in selected_rows
                if row.get("became_human")
            ),
            4,
        ),
        "detector_pass": detector_pass,
        "probe_success": detector_pass if probe_mode else None,
        "quality_pass": quality_pass,
        "strict_pass": strict_pass,
        "segment_deltas": rows,
        "strategy_effectiveness": effectiveness,
        "recommended_next_strategy": recommended_next_strategy,
        "recommendation_reason": recommendation_reason,
        "stop_automatic_spend": True,
        "attribution_mode": "proportional_document_overlap",
        "learning_eligible": False,
        "prompt_version": PROMPT_VERSION,
    }
    _persist_experiment_result(result, optimize_result)
    content_type = str(optimize_result.get("content_type") or "article")
    result["historical_strategy_summary"] = strategy_learning_summary(
        content_type=content_type
    )
    result["historical_feature_summary"] = feature_learning_summary(
        content_type=content_type
    )
    return result


def analyze_paired_reports(
    baseline_text: str,
    modified_text: str,
    baseline_report: dict[str, Any],
    modified_report: dict[str, Any],
    *,
    content_type: str = "article",
    hashes_confirmed: bool = False,
) -> dict[str, Any]:
    """Analyze a before/after SaaS pair without treating correlation as causation."""
    baseline = normalize_manuscript_text(baseline_text)
    modified = normalize_manuscript_text(modified_text)
    if not baseline or not modified:
        raise ZhuqueReportError("前后正文都不能为空。")
    if baseline_report.get("original_sha256") != sha256_text(baseline):
        raise ZhuqueReportError("原始报告与原始正文哈希不一致。")
    if modified_report.get("original_sha256") != sha256_text(modified):
        raise ZhuqueReportError("修改报告与修改正文哈希不一致。")
    if content_type not in {"article", "classical", "public_domain"}:
        raise ZhuqueReportError("差分学习不支持小说链路。")

    before_features = paired_manuscript_features(baseline)
    after_features = paired_manuscript_features(modified)
    feature_delta = {
        key: round(float(after_features[key]) - float(before_features[key]), 6)
        for key in before_features
        if isinstance(before_features[key], (int, float))
        and isinstance(after_features.get(key), (int, float))
    }
    removed_anchors, added_anchors = _anchor_counter_delta(baseline, modified)
    sentence_diff = _sentence_level_diff(baseline, modified)
    quality_risks: list[dict[str, Any]] = []
    if removed_anchors:
        quality_risks.append(
            {
                "type": "removed_anchor",
                "severity": "high",
                "evidence": removed_anchors[:20],
                "reason": "数字、日期、引语、书名或 URL 在修改稿中减少。",
            }
        )
    if added_anchors:
        quality_risks.append(
            {
                "type": "added_anchor",
                "severity": "high",
                "evidence": added_anchors[:20],
                "reason": "修改稿增加了原稿没有的可核验锚点。",
            }
        )
    length_ratio = len(modified) / max(1, len(baseline))
    if not QUALITY_SAFE_LENGTH_MIN <= length_ratio <= QUALITY_SAFE_LENGTH_MAX:
        quality_risks.append(
            {
                "type": "length_drift",
                "severity": "high",
                "evidence": round(length_ratio, 4),
                "reason": "整稿篇幅变化超过质量安全区间 0.95–1.08。",
            }
        )
    if sentence_diff["inserted"]:
        quality_risks.append(
            {
                "type": "unverified_added_statements",
                "severity": "medium",
                "evidence": sentence_diff["inserted"][:12],
                "reason": "存在无法由纯格式变化解释的新增陈述，必须逐条核验。",
            }
        )
    if sentence_diff["removed"]:
        quality_risks.append(
            {
                "type": "possible_removed_information",
                "severity": "medium",
                "evidence": sentence_diff["removed"][:12],
                "reason": "存在无法由纯格式变化解释的删除陈述，必须逐条核验。",
            }
        )
    slogan_hits = _quality_slogan_hits(modified, baseline)
    if slogan_hits:
        quality_risks.append(
            {
                "type": "unsupported_rhetorical_upgrade",
                "severity": "medium",
                "evidence": slogan_hits,
                "reason": "修改稿新增了口号化、绝对化或总结性拔高表达。",
            }
        )

    hypotheses: list[dict[str, Any]] = []
    before_segments = list(baseline_report.get("segments") or [])
    after_segments = list(modified_report.get("segments") or [])
    if len(after_segments) < len(before_segments):
        hypotheses.append(
            {
                "id": "boundary_regrouping",
                "direction": "test",
                "evidence": f"报告片段 {len(before_segments)} → {len(after_segments)}",
                "causal_status": "unverified",
            }
        )
    if feature_delta.get("sentence_length_cv", 0.0) > 0.02:
        hypotheses.append(
            {
                "id": "sentence_rhythm_variation",
                "direction": "test",
                "evidence": round(feature_delta["sentence_length_cv"], 4),
                "causal_status": "unverified",
            }
        )
    if feature_delta.get("paragraph_length_cv", 0.0) > 0.02:
        hypotheses.append(
            {
                "id": "paragraph_rhythm_variation",
                "direction": "test",
                "evidence": round(feature_delta["paragraph_length_cv"], 4),
                "causal_status": "unverified",
            }
        )

    non_whitespace_similarity = round(
        SequenceMatcher(
            None,
            _non_whitespace_text(baseline),
            _non_whitespace_text(modified),
            autojunk=False,
        ).ratio(),
        6,
    )
    baseline_metrics = _paired_report_metrics(baseline_report)
    modified_metrics = _paired_report_metrics(modified_report)
    mechanism_assessment = assess_detector_mechanisms(
        baseline_metrics,
        modified_metrics,
        feature_delta=feature_delta,
        non_whitespace_similarity=non_whitespace_similarity,
        sentence_diff=sentence_diff,
    )

    analysis_id = str(uuid4())
    payload = {
        "analysis_id": analysis_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": "report-driven-v5",
        "prompt_version": PROMPT_VERSION,
        "content_type": content_type,
        "baseline_sha256": sha256_text(baseline),
        "modified_sha256": sha256_text(modified),
        "hashes_confirmed": bool(hashes_confirmed),
        "source_match_verified": bool(
            baseline_report.get("source_match_verified")
            and modified_report.get("source_match_verified")
        ),
        "baseline_metrics": baseline_metrics,
        "modified_metrics": modified_metrics,
        "feature_before": before_features,
        "feature_after": after_features,
        "feature_delta": feature_delta,
        "non_whitespace_similarity": non_whitespace_similarity,
        "length_ratio": round(length_ratio, 6),
        "segment_alignment": build_segment_overlap_matrix(
            before_segments,
            after_segments,
        ),
        "sentence_diff": sentence_diff,
        "quality_risks": quality_risks,
        "quality_status": "failed_or_unverified" if quality_risks else "unverified",
        "strategy_hypotheses": hypotheses,
        "mechanism_assessment": mechanism_assessment,
        "controlled_experiments": [
            {
                "id": "layout_only",
                "changes": "只改变空白和自然段边界，非空白正文哈希不变",
                "tests": "报告切块/窗口边界是否会在正文字符不变时改变首页人工特征",
                "model_cost_cny": 0.0,
            },
            {
                "id": "syntax_constrained",
                "changes": "只改变句法、信息次序和节奏，禁止新增或删除 claim",
                "tests": "句长波动、段长波动和模板连接结构是否是有效方向",
                "model_cost_cap_cny": float(settings.AIGC_REVIEW_RUN_BUDGET_CNY),
            },
            {
                "id": "external_saas_quality_repair",
                "changes": "只修外部稿中已定位的事实和语言硬伤",
                "tests": "外部稿人工特征是否依赖劣质新增，修复后必须以新哈希重新送检",
                "requires_manual_confirmation": True,
            },
        ],
        "causal_confidence": "observational_confounded",
        "learning_eligible": False,
        "next_experiment": "layout_only",
        "fallback_workflow": {
            "available": bool(
                modified_report.get("official_overview_verified") is True
                and float(modified_report.get("human_percent") or 0.0)
                >= HUMAN_PASS_THRESHOLD
                and quality_risks
            ),
            "mode": "external_saas_minimal_quality_repair",
            "repair_scope": [item["type"] for item in quality_risks],
            "must_recheck_repaired_text": True,
            "reuse_external_report_for_delivery": False,
            "reason": "外部 SaaS 报告只能证明 SaaS 版本；任何质量修复都会生成新哈希，必须重新上传修复版的真实朱雀报告。",
        },
    }
    _EXPERIMENT_DIR.mkdir(parents=True, exist_ok=True)
    (_EXPERIMENT_DIR / f"paired-{analysis_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def paired_manuscript_features(text: str) -> dict[str, float | int]:
    sentences = _sentence_units(text)
    paragraphs = _natural_paragraphs(text)
    sentence_lengths = [len(_non_whitespace_text(value)) for value in sentences] or [0]
    paragraph_lengths = [len(_non_whitespace_text(value)) for value in paragraphs] or [0]
    nonspace_chars = len(_non_whitespace_text(text))
    connector_pattern = re.compile(
        r"(?:首先|其次|再次|最后|总之|综上|与此同时|因此|然而|但是|此外|换言之|值得注意的是)"
    )
    return {
        "nonspace_chars": nonspace_chars,
        "paragraph_count": len(paragraphs),
        "sentence_count": len(sentences),
        "mean_sentence_chars": round(statistics.mean(sentence_lengths), 6),
        "sentence_length_cv": round(_coefficient_of_variation(sentence_lengths), 6),
        "paragraph_length_cv": round(_coefficient_of_variation(paragraph_lengths), 6),
        "short_sentence_ratio": round(
            sum(value <= 18 for value in sentence_lengths) / max(1, len(sentence_lengths)), 6
        ),
        "long_sentence_ratio": round(
            sum(value >= 55 for value in sentence_lengths) / max(1, len(sentence_lengths)), 6
        ),
        "connector_per_1000_chars": round(
            len(connector_pattern.findall(text)) * 1000 / max(1, nonspace_chars), 6
        ),
    }


def build_segment_overlap_matrix(
    baseline_segments: list[dict[str, Any]],
    modified_segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Align shifted report rows by normalized position, anchors and text similarity."""
    if not baseline_segments or not modified_segments:
        return []
    baseline_length = max(int(item.get("end") or 0) for item in baseline_segments) or 1
    modified_length = max(int(item.get("end") or 0) for item in modified_segments) or 1
    matrix: list[dict[str, Any]] = []
    for before in baseline_segments:
        before_start = float(before.get("start") or 0) / baseline_length
        before_end = float(before.get("end") or 0) / baseline_length
        before_span = max(1e-9, before_end - before_start)
        before_text = str(before.get("mapped_text") or "")
        before_anchors = set(_anchors(before_text))
        for after in modified_segments:
            after_start = float(after.get("start") or 0) / modified_length
            after_end = float(after.get("end") or 0) / modified_length
            after_span = max(1e-9, after_end - after_start)
            overlap = max(0.0, min(before_end, after_end) - max(before_start, after_start))
            position_score = overlap / max(before_span, after_span)
            after_text = str(after.get("mapped_text") or "")
            text_score = SequenceMatcher(
                None,
                _non_whitespace_text(before_text)[:4000],
                _non_whitespace_text(after_text)[:4000],
                autojunk=False,
            ).ratio()
            after_anchors = set(_anchors(after_text))
            anchor_score = (
                len(before_anchors & after_anchors) / len(before_anchors | after_anchors)
                if before_anchors or after_anchors
                else 0.0
            )
            confidence = 0.55 * position_score + 0.35 * text_score + 0.10 * anchor_score
            if confidence < 0.08:
                continue
            matrix.append(
                {
                    "baseline_segment_id": int(before.get("segment_id") or 0),
                    "modified_segment_id": int(after.get("segment_id") or 0),
                    "position_overlap": round(position_score, 6),
                    "text_similarity": round(text_score, 6),
                    "anchor_overlap": round(anchor_score, 6),
                    "confidence": round(confidence, 6),
                }
            )
    matrix.sort(key=lambda item: (-item["confidence"], item["baseline_segment_id"]))
    return matrix


def _paired_report_metrics(report: dict[str, Any]) -> dict[str, Any]:
    segments = list(report.get("segments") or [])
    char_counts = [
        int(item.get("char_count") or max(0, int(item.get("end") or 0) - int(item.get("start") or 0)))
        for item in segments
    ]
    human_segments = [item for item in segments if item.get("label") == "human"]
    official = _optional_float(report.get("official_human_percent"))
    derived = _optional_float(report.get("derived_human_share_percent"))
    return {
        "official_human_percent": report.get("official_human_percent"),
        "derived_human_share_percent": report.get("derived_human_share_percent"),
        "human_percent": report.get("human_percent"),
        "suspected_percent": report.get("suspected_percent"),
        "ai_percent": report.get("ai_percent"),
        "official_overview_verified": report.get("official_overview_verified"),
        "segment_count": len(segments),
        "mean_segment_chars": round(statistics.mean(char_counts), 2) if char_counts else 0.0,
        "max_segment_chars": max(char_counts, default=0),
        "human_segment_count": len(human_segments),
        "largest_human_segment_chars": max(
            (
                int(item.get("char_count") or max(0, int(item.get("end") or 0) - int(item.get("start") or 0)))
                for item in human_segments
            ),
            default=0,
        ),
        "official_derived_human_gap": (
            round(official - derived, 2)
            if official is not None and derived is not None
            else None
        ),
    }


def assess_detector_mechanisms(
    baseline_metrics: dict[str, Any],
    modified_metrics: dict[str, Any],
    *,
    feature_delta: dict[str, float],
    non_whitespace_similarity: float,
    sentence_diff: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return falsifiable black-box hypotheses, never claims about private internals."""
    assessments: list[dict[str, Any]] = []
    before_count = int(baseline_metrics.get("segment_count") or 0)
    after_count = int(modified_metrics.get("segment_count") or 0)
    if before_count and after_count and before_count != after_count:
        assessments.append(
            {
                "mechanism": "window_or_boundary_sensitivity",
                "support": "candidate_supported",
                "confidence": "medium",
                "evidence": (
                    f"报告片段数 {before_count}→{after_count}；最大片段 "
                    f"{baseline_metrics.get('max_segment_chars', 0)}→"
                    f"{modified_metrics.get('max_segment_chars', 0)} 字"
                ),
                "confounders": ["正文内容同时变化", "报告分段规则不可见"],
                "falsification_test": "layout_only：保持非空白正文完全一致，仅重排段落后复检",
            }
        )
    rhythm_delta = max(
        abs(float(feature_delta.get("sentence_length_cv") or 0.0)),
        abs(float(feature_delta.get("paragraph_length_cv") or 0.0)),
    )
    if rhythm_delta >= 0.02:
        assessments.append(
            {
                "mechanism": "rhythm_distribution_sensitivity",
                "support": "candidate_supported",
                "confidence": "medium",
                "evidence": {
                    "sentence_length_cv_delta": feature_delta.get("sentence_length_cv", 0.0),
                    "paragraph_length_cv_delta": feature_delta.get("paragraph_length_cv", 0.0),
                },
                "confounders": ["词汇和事实也可能同时变化"],
                "falsification_test": "syntax_constrained：claim 不变，仅调整句法与节奏后复检",
            }
        )
    gap = modified_metrics.get("official_derived_human_gap")
    if isinstance(gap, (int, float)) and abs(float(gap)) >= 1.0:
        assessments.append(
            {
                "mechanism": "overview_is_not_simple_row_sum",
                "support": "direct_output_evidence",
                "confidence": "high",
                "evidence": f"首页官方人工特征与表格绿色占比相差 {float(gap):.2f} 个百分点",
                "confounders": ["不能据此推断首页具体聚合公式"],
                "falsification_test": "继续保存多份报告的官方值与表格推导值，检验差值是否稳定存在",
            }
        )
    if sentence_diff.get("inserted_count") or sentence_diff.get("removed_count"):
        assessments.append(
            {
                "mechanism": "content_addition_is_confounded",
                "support": "not_identified",
                "confidence": "low",
                "evidence": {
                    "inserted_sentences": sentence_diff.get("inserted_count", 0),
                    "removed_sentences": sentence_diff.get("removed_count", 0),
                    "non_whitespace_similarity": non_whitespace_similarity,
                },
                "confounders": ["边界、句法、篇幅和事实同时变化"],
                "falsification_test": "禁止新增 claim 的受限编辑；若仍提升，排除劣质填充是必要条件",
            }
        )
    return assessments


def _sentence_units(text: str) -> list[str]:
    return [
        value.strip()
        for value in re.split(r"(?<=[。！？!?；;])|\n+", text)
        if value.strip()
    ]


def _sentence_level_diff(baseline: str, modified: str) -> dict[str, Any]:
    before = _sentence_units(baseline)
    after = _sentence_units(modified)
    matcher = SequenceMatcher(None, before, after, autojunk=False)
    inserted: list[str] = []
    removed: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in {"replace", "delete"}:
            removed.extend(before[i1:i2])
        if tag in {"replace", "insert"}:
            inserted.extend(after[j1:j2])
    return {
        "inserted_count": len(inserted),
        "removed_count": len(removed),
        "inserted": inserted[:30],
        "removed": removed[:30],
    }


def _anchor_counter_delta(baseline: str, modified: str) -> tuple[list[str], list[str]]:
    before = _anchors(baseline)
    after = _anchors(modified)
    removed = list((before - after).elements())
    added = list((after - before).elements())
    return removed, added


def _quality_slogan_hits(modified: str, baseline: str) -> list[str]:
    patterns = (
        r"最重要的功臣",
        r"国家危难时[^。！？]{0,30}硬骨头",
        r"真正的?将军",
        r"注定了[^。！？]{0,30}(?:命运|结局)",
        r"最让人记得的",
    )
    hits: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, modified):
            value = match.group(0)
            if value not in baseline:
                hits.append(value)
    return list(dict.fromkeys(hits))[:20]


def _coefficient_of_variation(values: list[int]) -> float:
    if not values:
        return 0.0
    mean = statistics.mean(values)
    if mean <= 0:
        return 0.0
    return statistics.pstdev(values) / mean


def _non_whitespace_text(text: str) -> str:
    return re.sub(r"\s+", "", text)


def create_calibration_sample(
    *,
    user_id: str,
    text: str,
    content_type: str,
    source_group: str,
    variant_type: str,
    parent_sha256: str | None = None,
    strategy_id: str | None = None,
    quality_gate_passed: bool | None = None,
) -> dict[str, Any]:
    """Register one isolated Zhuque submission with an exact manuscript hash."""
    normalized = normalize_manuscript_text(text)
    if not 350 <= len(normalized) <= 2500:
        raise ZhuqueReportError("校准样本必须是 350–2500 字的完整编辑单元。")
    allowed_types = {"article", "classical", "public_domain"}
    if content_type not in allowed_types:
        raise ZhuqueReportError("校准样本不支持小说链路。")
    allowed_variants = {
        "original",
        "fact_reauthor",
        "human_directed",
        "human_edited",
        "human_control",
        "external_saas",
        "layout_only",
        "syntax_constrained",
        "combined",
        "detector_probe",
    }
    if variant_type not in allowed_variants:
        raise ZhuqueReportError("未知校准样本类型。")
    group = re.sub(r"[^A-Za-z0-9_-]", "", source_group)[:80]
    if not group:
        group = str(uuid4())
    digest = sha256_text(normalized)
    directory = _CALIBRATION_DIR / _safe_cache_namespace(user_id)
    directory.mkdir(parents=True, exist_ok=True)
    for path in directory.glob("*.json"):
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if existing.get("text_sha256") == digest:
            return existing
    sample_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    payload: dict[str, Any] = {
        "sample_id": sample_id,
        "source_group": group,
        "variant_type": variant_type,
        "content_type": content_type,
        "strategy_id": strategy_id,
        "parent_sha256": parent_sha256,
        "text": normalized,
        "text_sha256": digest,
        "char_count": len(normalized),
        "status": "awaiting_zhuque",
        "aigc_value": None,
        "label": None,
        "green": None,
        "learning_eligible": False,
        "quality_gate_passed": quality_gate_passed,
        "prompt_version": PROMPT_VERSION,
        "created_at": now,
        "updated_at": now,
    }
    (directory / f"{sample_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def list_calibration_samples(*, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
    directory = _CALIBRATION_DIR / _safe_cache_namespace(user_id)
    if not directory.exists():
        return []
    results: list[dict[str, Any]] = []
    paths = sorted(
        directory.glob("*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:max(1, min(500, limit))]
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if payload.get("prompt_version") == PROMPT_VERSION:
            results.append(payload)
    return results


def calibration_evidence_status(
    *,
    user_id: str | None = None,
    content_type: str | None = None,
) -> dict[str, Any]:
    """Report detector efficacy only from quality-safe, exact-hash PDF pairs."""
    promotion_rule = (
        "同一策略至少 2 个方向一致的真实对照才为 directional；至少 5 个且胜率不低于 80%、"
        "中位提升不低于 5 个百分点才为 validated。"
    )
    if not user_id:
        return {
            "status": "unproven",
            "verified_pair_count": 0,
            "claim": "尚无当前用户的精确哈希真实复检对照，不能声称可显著提高人工特征。",
            "strategies": [],
            "promotion_rule": promotion_rule,
        }
    samples = list_calibration_samples(user_id=user_id, limit=500)
    originals = {
        str(item.get("text_sha256") or ""): item
        for item in samples
        if item.get("variant_type") in {"original", "human_control"}
        and item.get("learning_eligible") is True
        and item.get("verification_mode") == "pdf_exact_hash"
    }
    grouped: dict[str, list[float]] = {}
    pass_grouped: dict[str, int] = {}
    for item in samples:
        variant = str(item.get("variant_type") or "")
        if variant not in {"layout_only", "syntax_constrained", "combined"}:
            continue
        if item.get("learning_eligible") is not True:
            continue
        if item.get("verification_mode") != "pdf_exact_hash":
            continue
        if content_type and item.get("content_type") != content_type:
            continue
        baseline = originals.get(str(item.get("parent_sha256") or ""))
        if baseline is None:
            continue
        before = _optional_float(baseline.get("human_percent"))
        after = _optional_float(item.get("human_percent"))
        if before is None or after is None:
            continue
        grouped.setdefault(variant, []).append(after - before)
        pass_grouped[variant] = pass_grouped.get(variant, 0) + int(bool(item.get("green")))

    rank = {"unproven": 0, "directional": 1, "validated": 2}
    best_status = "unproven"
    summaries: list[dict[str, Any]] = []
    for variant, deltas in grouped.items():
        positive_count = sum(value > 0 for value in deltas)
        win_rate = positive_count / len(deltas)
        median_delta = statistics.median(deltas)
        status = "unproven"
        if len(deltas) >= 2 and positive_count == len(deltas):
            status = "directional"
        if len(deltas) >= 5 and win_rate >= 0.8 and median_delta >= 5.0:
            status = "validated"
        if rank[status] > rank[best_status]:
            best_status = status
        summaries.append(
            {
                "experiment_type": variant,
                "status": status,
                "verified_pair_count": len(deltas),
                "positive_count": positive_count,
                "win_rate": round(win_rate, 4),
                "median_human_percent_delta": round(median_delta, 2),
                "final_pass_count": pass_grouped.get(variant, 0),
            }
        )
    claim = {
        "unproven": "尚无足够真实对照，当前能力是安全实验平台，不是已验证的朱雀降率引擎。",
        "directional": "已有方向一致的初步真实证据，但样本仍不足以承诺稳定显著增益。",
        "validated": "已有达到本项目验证门槛的真实对照证据；仍须对每个候选重新上传朱雀报告。",
    }[best_status]
    return {
        "status": best_status,
        "verified_pair_count": sum(len(values) for values in grouped.values()),
        "claim": claim,
        "strategies": sorted(
            summaries,
            key=lambda item: (
                -rank[str(item["status"])],
                -float(item["median_human_percent_delta"]),
            ),
        ),
        "promotion_rule": promotion_rule,
    }


def get_calibration_sample(*, user_id: str, sample_id: str) -> dict[str, Any]:
    try:
        normalized_id = str(UUID(sample_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ZhuqueReportError("校准样本编号无效。") from exc
    path = _CALIBRATION_DIR / _safe_cache_namespace(user_id) / f"{normalized_id}.json"
    if not path.is_file():
        raise ZhuqueReportError("校准样本不存在。")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ZhuqueReportError("校准样本损坏，无法读取。") from exc


def record_calibration_result(
    *,
    user_id: str,
    sample_id: str,
    submitted_text_sha256: str,
    aigc_value: float,
    report_sha256: str | None = None,
) -> dict[str, Any]:
    """Accept learning evidence only when the submitted text hash is exact."""
    try:
        normalized_id = str(UUID(sample_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ZhuqueReportError("校准样本编号无效。") from exc
    path = _CALIBRATION_DIR / _safe_cache_namespace(user_id) / f"{normalized_id}.json"
    payload = get_calibration_sample(user_id=user_id, sample_id=normalized_id)
    expected_hash = str(payload.get("text_sha256") or "")
    if submitted_text_sha256 != expected_hash:
        raise ZhuqueReportError("送检正文哈希不一致；该结果不能用于学习。")
    value = float(aigc_value)
    if not 0.0 <= value <= 1.0:
        raise ZhuqueReportError("AIGC 值必须在 0 到 1 之间。")
    label = classify_aigc_value(value)
    payload.update(
        {
            "status": "labeled",
            "aigc_value": round(value, 6),
            "label": label,
            "green": label == "human",
            "learning_eligible": False,
            "verification_mode": "manual_value",
            "report_sha256": report_sha256,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def record_calibration_report_result(
    *,
    user_id: str,
    sample_id: str,
    parsed_report: dict[str, Any],
) -> dict[str, Any]:
    """Persist a report-verified calibration result eligible for local learning."""
    payload = get_calibration_sample(user_id=user_id, sample_id=sample_id)
    expected_hash = str(payload.get("text_sha256") or "")
    if parsed_report.get("original_sha256") != expected_hash:
        raise ZhuqueReportError("校准报告对应的正文哈希不一致。")
    if parsed_report.get("source_match_verified") is not True:
        raise ZhuqueReportError("校准报告未确认与样本同稿，禁止用于学习。")
    human_percent = float(parsed_report.get("human_percent") or 0.0)
    suspected_percent = float(parsed_report.get("suspected_percent") or 0.0)
    ai_percent = float(parsed_report.get("ai_percent") or 0.0)
    official_verified = parsed_report.get("official_overview_verified") is True
    strict_pass = official_verified and human_percent >= HUMAN_PASS_THRESHOLD
    label = "human" if strict_pass else ("ai" if ai_percent >= suspected_percent else "suspected")
    weighted_aigc = _report_weighted_aigc(list(parsed_report.get("segments") or []))
    variant_type = str(payload.get("variant_type") or "")
    quality_gate = payload.get("quality_gate_passed") is True or variant_type in {
        "original",
        "human_control",
    }
    learning_eligible = bool(
        official_verified
        and quality_gate
        and variant_type != "external_saas"
    )
    payload.update(
        {
            "status": "labeled",
            "aigc_value": round(weighted_aigc, 6),
            "label": label,
            "green": strict_pass,
            "human_percent": round(human_percent, 2),
            "suspected_percent": round(suspected_percent, 2),
            "ai_percent": round(ai_percent, 2),
            "official_overview_verified": official_verified,
            "strict_threshold": HUMAN_PASS_THRESHOLD,
            "learning_eligible": learning_eligible,
            "verification_mode": "pdf_exact_hash",
            "report_sha256": parsed_report.get("report_sha256"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    path = (
        _CALIBRATION_DIR
        / _safe_cache_namespace(user_id)
        / f"{str(UUID(sample_id))}.json"
    )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _normalized_overlap_score(
    baseline: dict[str, Any],
    baseline_length: int,
    recheck_segments: list[dict[str, Any]],
    recheck_length: int,
) -> float:
    start = float(baseline.get("start") or 0) / baseline_length
    end = float(baseline.get("end") or 0) / baseline_length
    weighted_score = 0.0
    overlap_total = 0.0
    for item in recheck_segments:
        other_start = float(item.get("start") or 0) / recheck_length
        other_end = float(item.get("end") or 0) / recheck_length
        overlap = max(0.0, min(end, other_end) - max(start, other_start))
        if overlap <= 0:
            continue
        weighted_score += overlap * float(item.get("aigc_value") or 0.0)
        overlap_total += overlap
    if overlap_total <= 0:
        raise ZhuqueReportError("两份报告的片段区间无法对齐。")
    return weighted_score / overlap_total


def _report_weighted_aigc(segments: list[dict[str, Any]]) -> float:
    total = sum(float(item.get("share_percent") or 0.0) for item in segments)
    if total <= 0:
        return 0.0
    return sum(
        float(item.get("aigc_value") or 0.0) * float(item.get("share_percent") or 0.0)
        for item in segments
    ) / total


def _weighted_row_delta(rows: list[dict[str, Any]]) -> float:
    total = sum(float(row.get("share_percent") or 0.0) for row in rows)
    if total <= 0:
        return 0.0
    return sum(
        float(row.get("delta") or 0.0) * float(row.get("share_percent") or 0.0)
        for row in rows
    ) / total


def _delta_verdict(delta: float) -> str:
    if delta <= -0.05:
        return "strong_improvement"
    if delta <= -0.01:
        return "improvement"
    if delta < 0.01:
        return "neutral"
    return "worse"


def _persist_experiment_result(result: dict[str, Any], optimize_result: dict[str, Any]) -> None:
    payload = {
        **result,
        "original_sha256": optimize_result.get("original_sha256"),
        "candidate_sha256": optimize_result.get("candidate_sha256"),
        "iteration": optimize_result.get("iteration", 1),
        "content_type": optimize_result.get("content_type", "article"),
        "selected_segment_ids": optimize_result.get("selected_segment_ids") or [],
        "cost": optimize_result.get("cost") or {},
        "models": optimize_result.get("models") or {},
    }
    _EXPERIMENT_DIR.mkdir(parents=True, exist_ok=True)
    path = _EXPERIMENT_DIR / f"{result['comparison_id']}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _experiment_paths(max_files: int) -> list[Path]:
    paths: list[Path] = []
    for directory in (_EXPERIMENT_DIR, _LEGACY_EXPERIMENT_DIR):
        if directory.exists():
            paths.extend(directory.glob("*.json"))
    return sorted(
        paths,
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:max_files]


def strategy_learning_summary(
    *,
    max_files: int = 200,
    content_type: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate only v3 score deltas; corrupt/old files never affect a live request."""
    grouped: dict[str, list[float]] = {}
    green_grouped: dict[str, list[float]] = {}
    for path in _experiment_paths(max_files):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if payload.get("prompt_version") != PROMPT_VERSION:
            continue
        if payload.get("learning_eligible") is not True:
            continue
        if content_type and payload.get("content_type") != content_type:
            continue
        for item in payload.get("strategy_effectiveness") or []:
            strategy_id = str(item.get("strategy_id") or "")
            if strategy_id not in rewrite_strategy_map():
                continue
            try:
                grouped.setdefault(strategy_id, []).append(float(item["weighted_delta"]))
                green_grouped.setdefault(strategy_id, []).append(
                    float(item.get("green_conversion_rate") or 0.0)
                )
            except (KeyError, TypeError, ValueError):
                continue
    summary: list[dict[str, Any]] = []
    for strategy_id in rewrite_strategy_map():
        values = grouped.get(strategy_id, [])
        if not values:
            continue
        average = sum(values) / len(values)
        green_values = green_grouped.get(strategy_id, [])
        summary.append(
            {
                "strategy_id": strategy_id,
                "experiment_count": len(values),
                "average_weighted_delta": round(average, 6),
                "average_green_conversion_rate": round(
                    sum(green_values) / len(green_values),
                    6,
                ) if green_values else 0.0,
                "green_experiment_count": sum(value > 0 for value in green_values),
                "improvement_count": sum(value <= -0.01 for value in values),
                "worse_count": sum(value >= 0.01 for value in values),
            }
        )
    return sorted(summary, key=lambda item: item["average_weighted_delta"])


def feature_learning_summary(
    *,
    max_files: int = 200,
    content_type: str | None = None,
) -> list[dict[str, Any]]:
    """Learn directional correlations from real re-checks, never from model guesses."""
    records: list[dict[str, Any]] = []
    for path in _experiment_paths(max_files):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if payload.get("prompt_version") != PROMPT_VERSION:
            continue
        if payload.get("learning_eligible") is not True:
            continue
        if content_type and payload.get("content_type") != content_type:
            continue
        for row in payload.get("segment_deltas") or []:
            if not row.get("selected"):
                continue
            records.append(
                {
                    # Negative is "better" in summarize_feature_effects. Only a real
                    # orange/red -> green transition counts; red -> orange is not success.
                    "score_delta": -1.0 if row.get("became_human") else 0.0,
                    "style_feature_delta": row.get("style_feature_delta") or {},
                }
            )
    return summarize_feature_effects(records, min_samples=5)


def build_layout_only_result(
    original: str,
    segments: Iterable[ZhuqueSegment],
    selected_ids: Iterable[int],
    *,
    content_type: str,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Create a zero-model-cost whitespace-only experiment."""
    if content_type == "classical":
        raise ZhuqueReportError("古文/佛经不执行排版边界实验。")
    normalized_segments = list(segments)
    by_id = {item.segment_id: item for item in normalized_segments}
    ids = list(dict.fromkeys(int(value) for value in selected_ids))
    replacements: list[tuple[int, int, str]] = []
    segment_results: list[dict[str, Any]] = []
    for segment_id in ids:
        segment = by_id.get(segment_id)
        if segment is None:
            raise ZhuqueReportError(f"报告中不存在片段: {segment_id}")
        if segment.label == "human":
            raise ZhuqueReportError(f"片段 {segment_id} 已是人工特征，不应参与实验。")
        source = original[segment.start:segment.end]
        if source != segment.mapped_text:
            raise ZhuqueReportError(f"片段 {segment_id} 的原稿坐标已经变化。")
        candidate = reflow_layout_only(source)
        if _non_whitespace_text(candidate) != _non_whitespace_text(source):
            raise ZhuqueReportError("排版实验改变了非空白正文，已安全中止。")
        deterministic = review_deterministic_constraints(
            source,
            candidate,
            preserve_paragraph_count=False,
            min_length_ratio=0.90,
            max_length_ratio=1.10,
        )
        if not deterministic_hard_safety_passed(
            deterministic,
            require_paragraph_preservation=False,
            min_length_ratio=0.90,
            max_length_ratio=1.10,
        ):
            raise ZhuqueReportError(
                f"片段 {segment_id} 的排版实验未通过确定性保护："
                + "；".join(deterministic.reasons)
            )
        replacements.append((segment.start, segment.end, candidate))
        segment_results.append(
            {
                "segment_id": segment_id,
                "strategy_id": "layout_only",
                "rewrite_provider": "local",
                "rewrite_model": "deterministic-layout-v1",
                "status": "ready",
                "hard_safety_passed": True,
                "substantive_rewrite_passed": True,
                "material_rewrite_advisory_passed": deterministic.material_rewrite_passed,
                "original_chars": len(source),
                "candidate_chars": len(candidate),
                "report_aigc_value": segment.aigc_value,
                "report_label": segment.label,
                "style_diagnosis": diagnose_segment_style(source),
                "editorial_unit_count": 1,
                "rewrite_attempts": 0,
                "attempts": [],
                "unit_results": [
                    {
                        "unit_id": "LAYOUT",
                        "original_chars": len(source),
                        "candidate_chars": len(candidate),
                        "original_text": source,
                        "candidate_text": candidate,
                        "status": "ready",
                        "fact_ledger_mode": "not_required_identical_non_whitespace",
                        "fact_ledger_claim_count": 0,
                        "fact_ledger_cost_cny": 0.0,
                        "hard_safety_passed": True,
                        "substantive_rewrite_passed": True,
                        "rewrite_attempts": 0,
                        "attempts": [],
                        "deterministic_review": deterministic.to_dict(),
                        "candidate_preview": candidate[:400],
                        "rejected_candidate_preview": "",
                    }
                ],
                "deterministic_review": deterministic.to_dict(),
                "style_feature_version": STYLE_FEATURE_VERSION,
                "style_feature_delta": style_feature_delta(source, candidate),
                "candidate_preview": candidate[:500],
                "rejected_candidate_preview": "",
            }
        )
    assembled = _assemble_replacements(original, replacements)
    if assembled == original:
        return {
            **_manual_review_result(
                request_id=request_id or str(uuid4()),
                original=original,
                selected_ids=ids,
                segment_results=segment_results,
                reason="原稿排版已经满足安全边界，没有产生可送检的新版本；未调用任何模型。",
                total_input_tokens=0,
                total_output_tokens=0,
                rewrite_cost_cny=0.0,
                rewrite_provider="local",
                rewrite_model="deterministic-layout-v1",
                rewrite_reasoning="disabled",
            ),
            "experiment_type": "layout_only",
        }
    quality_audit = {
        "pass": True,
        "semantic_fidelity": 100,
        "information_completeness": 100,
        "fluency": 100,
        "voice_continuity": 100,
        "no_added_facts": True,
        "no_removed_information": True,
        "no_stance_change": True,
        "no_plot_or_event_change": True,
        "structural_rewrite_sufficient": True,
        "claim_results": [
            {"claim_id": f"SEG{segment_id}", "status": "preserved", "evidence": "非空白正文完全一致"}
            for segment_id in ids
        ],
        "added_claims": [],
        "failed_segment_ids": [],
        "issues": [],
        "reason": "仅调整空白与自然段边界，非空白正文哈希一致。",
        "audit_mode": "deterministic_non_whitespace_identity",
    }
    return {
        "request_id": request_id or str(uuid4()),
        "status": "candidate_ready",
        "deliverable": False,
        "next_action": "零费用排版候选已生成；请先上传真实朱雀复检 PDF。",
        "original_sha256": sha256_text(original),
        "candidate_sha256": sha256_text(assembled),
        "original_non_whitespace_sha256": sha256_text(_non_whitespace_text(original)),
        "candidate_non_whitespace_sha256": sha256_text(_non_whitespace_text(assembled)),
        "original_text": original,
        "candidate_text": assembled,
        "selected_segment_ids": ids,
        "iteration": 1,
        "preferred_strategy": "layout_only",
        "content_type": content_type,
        "experiment_type": "layout_only",
        "segment_results": segment_results,
        "quality_audit": quality_audit,
        "editorial_unit_count": len(ids),
        "audit_batch_count": 0,
        "budget": {"currency": "CNY", "limit_cny": 0.0, "spent_cny": 0.0, "exhausted": False},
        "cost": {
            "currency": "CNY",
            "estimated": False,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cny": 0.0,
        },
        "models": {
            "rewrite_provider": "local",
            "rewrite": "deterministic-layout-v1",
            "fact": "not_required",
            "audit": "deterministic_hash",
            "rewrite_thinking": "disabled",
        },
        "prompt_version": PROMPT_VERSION,
    }


def reflow_layout_only(source: str) -> str:
    """Regroup whitespace while preserving every non-whitespace character in order."""
    paragraphs = [value.strip() for value in re.split(r"\n\s*\n", source) if value.strip()]
    if len(paragraphs) <= 1:
        return source.strip()
    output: list[str] = []
    buffer: list[str] = []
    buffer_chars = 0

    def flush() -> None:
        nonlocal buffer, buffer_chars
        if buffer:
            output.append("\n".join(buffer))
            buffer = []
            buffer_chars = 0

    for paragraph in paragraphs:
        paragraph_chars = len(_non_whitespace_text(paragraph))
        if _looks_like_heading(paragraph):
            flush()
            output.append(paragraph)
            continue
        if paragraph_chars > 480:
            flush()
            sentences = _sentence_units(paragraph)
            sentence_buffer: list[str] = []
            sentence_chars = 0
            for sentence in sentences:
                value_chars = len(_non_whitespace_text(sentence))
                if sentence_buffer and sentence_chars + value_chars > 420:
                    output.append("".join(sentence_buffer))
                    sentence_buffer = []
                    sentence_chars = 0
                sentence_buffer.append(sentence)
                sentence_chars += value_chars
            if sentence_buffer:
                output.append("".join(sentence_buffer))
            continue
        if buffer and buffer_chars + paragraph_chars > 420:
            flush()
        buffer.append(paragraph)
        buffer_chars += paragraph_chars
        if buffer_chars >= 220:
            flush()
    flush()
    candidate = "\n\n".join(output).strip()
    if _non_whitespace_text(candidate) != _non_whitespace_text(source):
        raise ZhuqueReportError("排版重组未保持正文字符顺序。")
    return candidate


@lru_cache(maxsize=1)
def detector_probe_profiles() -> dict[str, str]:
    payload = json.loads(_DETECTOR_PROBE_PROFILES_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not payload:
        raise ZhuqueReportError("朱雀探索实验的语气配置为空。")
    profiles = {
        str(key).strip(): str(value).strip()
        for key, value in payload.items()
        if str(key).strip() and str(value).strip()
    }
    if not profiles:
        raise ZhuqueReportError("朱雀探索实验没有可用语气配置。")
    return profiles


@lru_cache(maxsize=1)
def detector_probe_prompt_template() -> str:
    template = _DETECTOR_PROBE_PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not template:
        raise ZhuqueReportError("朱雀探索实验 Prompt 为空。")
    return template


def render_detector_probe_prompt(
    source: str,
    *,
    previous_context: str,
    next_context: str,
    profile_rules: str,
    is_first_unit: bool,
) -> str:
    anchors = list(_anchors(source).elements())
    replacements = {
        "{{PROFILE_RULES}}": profile_rules,
        "{{IS_FIRST_UNIT}}": "是" if is_first_unit else "否",
        "{{PREVIOUS_CONTEXT}}": previous_context[-300:].strip() or "（无）",
        "{{SOURCE_TEXT}}": source.strip(),
        "{{NEXT_CONTEXT}}": next_context[:300].strip() or "（无）",
        "{{ANCHORS}}": json.dumps(anchors, ensure_ascii=False) if anchors else "（无）",
    }
    rendered = detector_probe_prompt_template()
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    return rendered


def parse_detector_probe_candidate(raw: str) -> tuple[str, bool]:
    cleaned = _clean_model_text(raw)
    match = re.search(r"<rewritten>\s*(.*?)\s*</rewritten>", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip(), True
    cleaned = re.sub(r"^<rewritten>\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*</rewritten>$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip(), False


def _detector_probe_call_cost_ceiling(prompt: str, source_chars: int) -> float:
    """Return a deliberately conservative DeepSeek call ceiling for budget preflight."""
    max_output_tokens = min(12000, max(1600, int(source_chars * 0.9)))
    conservative_input_tokens = max(_estimate_tokens(prompt), len(prompt) * 4)
    return (conservative_input_tokens * 3.0 + max_output_tokens * 6.0) / 1_000_000


def _build_full_method_detector_probe_result(
    original: str,
    segments: Iterable[ZhuqueSegment],
    selected_ids: Iterable[int],
    *,
    content_type: str,
    rewrite: Callable[..., Any],
    rewrite_provider: str,
    rewrite_model: str,
    request_id: str,
    iteration: int,
    budget_cny: float,
) -> dict[str, Any]:
    """Run the complete supplied detector experiment without editorial rollback.

    This path intentionally skips the fact ledger, placeholder protocol, Gemini
    audit and quality rollback.  It directly uses ``aigc_detector_probe.md`` so the
    detector experiment is not silently diluted by the quality-safe reauthor path.
    Its output can only be used for an exact-hash Zhuque re-check, never delivery.
    """
    if content_type == "classical":
        raise ZhuqueReportError("古文/佛经不参与跳过质量终审的朱雀探索实验。")
    if rewrite_provider != "deepseek":
        raise ZhuqueReportError("朱雀探索实验固定使用低成本 DeepSeek，避免模型变化污染对照结果。")
    if budget_cny <= 0:
        raise ZhuqueReportError("朱雀探索实验预算必须大于 0 元。")
    if iteration != 1:
        raise ZhuqueReportError("请先上传本轮真实朱雀复检报告，再决定下一轮实验。")

    normalized_segments = list(segments)
    by_id = {item.segment_id: item for item in normalized_segments}
    ids = sorted(set(int(value) for value in selected_ids))
    profiles = detector_probe_profiles()
    profile_items = list(profiles.items())
    replacements: list[tuple[int, int, str]] = []
    segment_results: list[dict[str, Any]] = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_cost = 0.0
    processed_units = 0
    processed_chars = 0
    processed_segment_ids: list[int] = []
    budget_stopped = False
    budget_stop_reason = ""
    profile_counts: Counter[str] = Counter()

    for segment_id in ids:
        segment = by_id.get(segment_id)
        if segment is None:
            raise ZhuqueReportError(f"报告中不存在片段: {segment_id}")
        if segment.label == "human":
            raise ZhuqueReportError(f"片段 {segment_id} 已是人工特征，不应参与探索实验。")
        if segment.mapping_confidence < 0.65:
            raise ZhuqueReportError(f"片段 {segment_id} 映射置信度过低，禁止自动实验。")
        if segment.start < 0 or segment.end > len(original) or segment.start >= segment.end:
            raise ZhuqueReportError(f"片段 {segment_id} 的原稿坐标无效。")
        source = original[segment.start:segment.end]
        if source != segment.mapped_text:
            raise ZhuqueReportError(f"片段 {segment_id} 的映射正文已被修改，请重新解析报告。")

        units = _detector_probe_units(
            source,
            min_chars=DEEP_RECAST_UNIT_MIN_CHARS,
            max_chars=DEEP_RECAST_UNIT_MAX_CHARS,
        )
        local_replacements: list[tuple[int, int, str]] = []
        unit_results: list[dict[str, Any]] = []
        for unit in units:
            if total_cost >= budget_cny:
                budget_stopped = True
                break
            unit_source = source[unit.start:unit.end]
            profile_id, profile_rules = profile_items[processed_units % len(profile_items)]
            global_start = segment.start + unit.start
            global_end = segment.start + unit.end
            prompt = render_detector_probe_prompt(
                unit_source,
                previous_context=original[max(0, global_start - 300):global_start],
                next_context=original[global_end:min(len(original), global_end + 300)],
                profile_rules=profile_rules,
                is_first_unit=processed_units == 0,
            )
            call_cost_ceiling = _detector_probe_call_cost_ceiling(prompt, len(unit_source))
            if total_cost + call_cost_ceiling > budget_cny:
                budget_stopped = True
                budget_stop_reason = (
                    "继续调用可能超过预算硬上限，已在下一次 DeepSeek 请求前停止。"
                )
                break
            unit_segment = ZhuqueSegment(
                segment_id=segment.segment_id,
                share_percent=segment.share_percent,
                char_count=len(unit_source),
                aigc_value=segment.aigc_value,
                label=segment.label,
                start=global_start,
                end=global_end,
                mapped_text=unit_source,
                mapping_confidence=segment.mapping_confidence,
                recommended=segment.recommended,
            )
            try:
                raw_result = rewrite(
                    prompt,
                    segment=unit_segment,
                    request_id=request_id,
                    attempt=1,
                    temperature=DETECTOR_PROBE_TEMPERATURE,
                )
                raw_text, usage = _coerce_model_result(raw_result)
                candidate, structured = parse_detector_probe_candidate(raw_text)
            except Exception as exc:
                logger.exception(
                    "LLM action=aigc_detector_probe request_id=%s model=%s segment=%s unit=%s status=failed error_type=%s",
                    request_id,
                    rewrite_model,
                    segment_id,
                    unit.unit_id,
                    type(exc).__name__,
                )
                raise AigcReviewModelError(
                    f"朱雀探索实验在片段 {segment_id}/{unit.unit_id} 调用 DeepSeek 失败: {exc}"
                ) from exc
            if not candidate:
                raise AigcReviewModelError(
                    f"朱雀探索实验在片段 {segment_id}/{unit.unit_id} 收到空候选。"
                )

            input_tokens = int(usage.get("input_tokens") or _estimate_tokens(prompt))
            output_tokens = int(usage.get("output_tokens") or _estimate_tokens(candidate))
            attempt_cost = float(
                usage.get("cost_cny")
                or ((input_tokens * 3.0 + output_tokens * 6.0) / 1_000_000)
            )
            total_input_tokens += input_tokens
            total_output_tokens += output_tokens
            total_cost += attempt_cost
            deterministic = review_deterministic_constraints(
                unit_source,
                candidate,
                preserve_paragraph_count=False,
                min_length_ratio=0.70,
                max_length_ratio=1.35,
            )
            attempt_payload = {
                "attempt": 1,
                "passed": True,
                "hard_safety_passed": deterministic_hard_safety_passed(
                    deterministic,
                    require_paragraph_preservation=False,
                    min_length_ratio=0.70,
                    max_length_ratio=1.35,
                ),
                "substantive_rewrite_passed": candidate.strip() != unit_source.strip(),
                "material_rewrite_advisory_passed": deterministic.material_rewrite_passed,
                "structured_output": structured,
                "protocol_issues": [] if structured else ["模型未使用 rewritten 包装；探索模式接受纯正文"],
                "substantive_rewrite_reasons": [],
                "deterministic_review": deterministic.to_dict(),
                "input_tokens": input_tokens,
                "cached_input_tokens": int(usage.get("cached_input_tokens") or 0),
                "output_tokens": output_tokens,
                "reasoning_tokens": int(usage.get("reasoning_tokens") or 0),
                "cost_cny": round(attempt_cost, 6),
                "budget_preflight_ceiling_cny": round(call_cost_ceiling, 6),
                "strategy_id": f"detector_probe:{profile_id}",
                "rewrite_provider": rewrite_provider,
                "rewrite_model": rewrite_model,
            }
            unit_results.append(
                {
                    "unit_id": unit.unit_id,
                    "original_chars": len(unit_source),
                    "candidate_chars": len(candidate),
                    "original_text": unit_source,
                    "candidate_text": candidate,
                    "status": "ready",
                    "fact_ledger_mode": "not_used_detector_probe",
                    "fact_ledger_claim_count": 0,
                    "fact_ledger_cost_cny": 0.0,
                    "hard_safety_passed": attempt_payload["hard_safety_passed"],
                    "substantive_rewrite_passed": attempt_payload["substantive_rewrite_passed"],
                    "rewrite_attempts": 1,
                    "attempts": [attempt_payload],
                    "deterministic_review": deterministic.to_dict(),
                    "candidate_preview": candidate[:400],
                    "rejected_candidate_preview": "",
                    "probe_profile": profile_id,
                }
            )
            local_replacements.append((unit.start, unit.end, candidate))
            processed_units += 1
            processed_chars += len(unit_source)
            profile_counts[profile_id] += 1

        segment_candidate = _assemble_replacements(source, local_replacements)
        if local_replacements:
            replacements.append((segment.start, segment.end, segment_candidate))
            processed_segment_ids.append(segment_id)
        parent_review = review_deterministic_constraints(
            source,
            segment_candidate,
            preserve_paragraph_count=False,
            min_length_ratio=0.70,
            max_length_ratio=1.35,
        )
        segment_results.append(
            {
                "segment_id": segment_id,
                "strategy_id": "detector_probe_mixed",
                "rewrite_provider": rewrite_provider,
                "rewrite_model": rewrite_model,
                "status": "ready" if local_replacements else "rejected",
                "hard_safety_passed": deterministic_hard_safety_passed(
                    parent_review,
                    require_paragraph_preservation=False,
                    min_length_ratio=0.70,
                    max_length_ratio=1.35,
                ),
                "substantive_rewrite_passed": segment_candidate.strip() != source.strip(),
                "material_rewrite_advisory_passed": parent_review.material_rewrite_passed,
                "original_chars": len(source),
                "candidate_chars": len(segment_candidate),
                "report_aigc_value": segment.aigc_value,
                "report_label": segment.label,
                "style_diagnosis": diagnose_segment_style(source),
                "editorial_unit_count": len(unit_results),
                "rewrite_attempts": len(unit_results),
                "attempts": [attempt for row in unit_results for attempt in row["attempts"]],
                "unit_results": unit_results,
                "deterministic_review": parent_review.to_dict(),
                "style_feature_version": STYLE_FEATURE_VERSION,
                "style_feature_delta": style_feature_delta(source, segment_candidate),
                "candidate_preview": segment_candidate[:500],
                "rejected_candidate_preview": "",
            }
        )
        if budget_stopped:
            break

    assembled = _assemble_replacements(original, replacements)
    if assembled == original:
        raise AigcReviewModelError("朱雀探索实验没有生成任何可复检变化。")
    return {
        "request_id": request_id,
        "status": "probe_ready",
        "deliverable": False,
        "next_action": (
            "完整方法实验稿已生成；本轮直接执行高频词清理、节奏打乱、分段语气轮换、首段重写和有限阅读反应，"
            "故意跳过事实账本与质量终审。只能用于上传朱雀验证人工特征变化，不能作为终稿。"
        ),
        "original_sha256": sha256_text(original),
        "candidate_sha256": sha256_text(assembled),
        "original_text": original,
        "candidate_text": assembled,
        "selected_segment_ids": ids,
        "iteration": iteration,
        "preferred_strategy": "detector_probe_full_method",
        "content_type": content_type,
        "experiment_type": "detector_probe",
        "segment_results": segment_results,
        "quality_audit": None,
        "experimental_quality_bypassed": True,
        "editorial_unit_count": processed_units,
        "audit_batch_count": 0,
        "budget": {
            "currency": "CNY",
            "limit_cny": round(budget_cny, 4),
            "spent_cny": round(total_cost, 6),
            "exhausted": budget_stopped or total_cost >= budget_cny,
        },
        "cost": {
            "currency": "CNY",
            "estimated": False,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "estimated_cny": round(total_cost, 6),
        },
        "models": {
            "rewrite_provider": rewrite_provider,
            "rewrite": rewrite_model,
            "fact": "not_used_detector_probe",
            "audit": "skipped_detector_probe",
            "rewrite_thinking": "disabled",
        },
        "prompt_version": PROBE_PROMPT_VERSION,
        "detector_probe": {
            "mode": "full_method_profile_rotation",
            "variable": "full_method_multi_variable",
            "temperature": DETECTOR_PROBE_TEMPERATURE,
            "profile_order": list(profiles),
            "profile_counts": dict(profile_counts),
            "processed_units": processed_units,
            "processed_chars": processed_chars,
            "processed_scope_percent": round(
                processed_chars * 100 / max(1, len(original)),
                4,
            ),
            "processed_segment_ids": processed_segment_ids,
            "budget_stopped": budget_stopped,
            "budget_stop_reason": budget_stop_reason,
            "quality_audit_required": False,
            "fact_ledger_required": False,
            "rewrite_provider": "deepseek",
            "max_budget_cny": round(budget_cny, 4),
            "variables": [
                "remove_template_phrases",
                "irregular_sentence_length",
                "irregular_paragraph_length",
                "standalone_short_sentence",
                "remove_numbered_argument",
                "break_summary_structure",
                "rotate_unit_voice",
                "source_grounded_reading_reaction",
                "rewrite_opening_paragraph",
                "segment_by_segment_generation",
                "temperature_0_8",
            ],
            "hypotheses": [
                "remove_template_phrases",
                "irregular_sentence_and_paragraph_rhythm",
                "rotate_unit_voice",
                "limited_first_person_reading_reaction",
                "segment_by_segment_generation",
            ],
        },
    }


def _probe_quote_mask(text: str) -> list[bool]:
    """Mark quoted spans so a punctuation-only probe never edits quotations."""
    pairs = {"“": "”", "‘": "’", "「": "」", "『": "』"}
    closing = set(pairs.values())
    stack: list[str] = []
    mask = [False] * len(text)
    ascii_quote_open = False
    for index, char in enumerate(text):
        if char in pairs:
            stack.append(pairs[char])
            mask[index] = True
            continue
        if char == '"':
            ascii_quote_open = not ascii_quote_open
            mask[index] = True
            continue
        if stack or ascii_quote_open:
            mask[index] = True
        if char in closing and stack and char == stack[-1]:
            stack.pop()
    return mask


def _probe_heading_mask(text: str) -> list[bool]:
    mask = [False] * len(text)
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped and _looks_like_heading(stripped):
            start = offset + line.find(stripped)
            for index in range(start, start + len(stripped)):
                mask[index] = True
        offset += len(line)
    return mask


def _probe_visible_chars(value: str) -> int:
    return len(re.sub(r"[\s，。！？!?；;：:、,.]", "", value))


def _spaced_probe_positions(
    rows: Iterable[tuple[float, int]],
    *,
    limit: int,
    minimum_distance: int = 48,
) -> list[int]:
    selected: list[int] = []
    for _score, position in sorted(rows, reverse=True):
        if all(abs(position - existing) >= minimum_distance for existing in selected):
            selected.append(position)
        if len(selected) >= limit:
            break
    return selected


def sentence_boundary_distribution_probe(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Move sentence boundaries while preserving every non-punctuation character.

    The experiment swaps the same number of Chinese commas and full stops, so
    punctuation totals, paragraphs, words, anchors and quotations remain fixed.
    It tests boundary placement only; the result is never a deliverable draft.
    """
    quote_mask = _probe_quote_mask(text)
    heading_mask = _probe_heading_mask(text)
    split_candidates: list[tuple[float, int]] = []
    merge_candidates: list[tuple[float, int]] = []
    paragraph_offset = 0
    for paragraph_match in re.finditer(r"[^\n]+", text):
        paragraph = paragraph_match.group(0)
        paragraph_offset = paragraph_match.start()
        if _looks_like_heading(paragraph.strip()):
            continue
        sentence_matches = list(re.finditer(r"[^。！？!?]+[。！？!?]?", paragraph))
        for sentence in sentence_matches:
            value = sentence.group(0)
            sentence_start = paragraph_offset + sentence.start()
            sentence_end = paragraph_offset + sentence.end()
            total_chars = _probe_visible_chars(value)
            if total_chars < 30:
                continue
            for comma in re.finditer("，", value):
                position = sentence_start + comma.start()
                if quote_mask[position] or heading_mask[position]:
                    continue
                left = _probe_visible_chars(text[sentence_start:position])
                right = _probe_visible_chars(text[position + 1:sentence_end])
                if min(left, right) < 6:
                    continue
                left_text = text[sentence_start:position].strip()
                right_text = text[position + 1:sentence_end].lstrip()
                if re.search(
                    r"(?:在于|不在于|看来|认为|由于|因为|如果|虽然|尽管|无论|一旦|随着|通过|对于|关于|前后)$",
                    left_text,
                ):
                    continue
                if re.match(
                    r"(?:而|但|并|也|就|才|且|的|着|了|在|于|以此|借此|用以|从而|进而|转向|构成|导致|意味着|说明|体现|成为|使|让|表明|证明|反映)",
                    right_text,
                ):
                    continue
                # Prefer deliberately unequal but still readable splits.
                score = abs(left - right) + min(total_chars, 90) / 10
                split_candidates.append((score, position))
        for index in range(len(sentence_matches) - 1):
            current = sentence_matches[index]
            following = sentence_matches[index + 1]
            if not current.group(0).endswith("。"):
                continue
            position = paragraph_offset + current.end() - 1
            if quote_mask[position] or heading_mask[position]:
                continue
            left = _probe_visible_chars(current.group(0))
            right = _probe_visible_chars(following.group(0))
            if not (8 <= left <= 34 and 8 <= right <= 34 and left + right <= 58):
                continue
            # Merging two compact sentences offsets one long-sentence split.
            merge_candidates.append((left + right - abs(left - right) / 4, position))

    target_pairs = min(
        8,
        len(split_candidates),
        len(merge_candidates),
        max(3, len(text) // 320),
    )
    if target_pairs <= 0:
        return text, []
    split_positions = _spaced_probe_positions(split_candidates, limit=target_pairs)
    merge_positions = _spaced_probe_positions(merge_candidates, limit=len(split_positions))
    pair_count = min(len(split_positions), len(merge_positions))
    split_positions = split_positions[:pair_count]
    merge_positions = merge_positions[:pair_count]
    if pair_count <= 0:
        return text, []

    chars = list(text)
    changes: list[dict[str, Any]] = []
    for position in split_positions:
        chars[position] = "。"
        changes.append({"position": position, "from": "，", "to": "。"})
    for position in merge_positions:
        chars[position] = "，"
        changes.append({"position": position, "from": "。", "to": "，"})
    candidate = "".join(chars)
    if candidate.count("，") != text.count("，") or candidate.count("。") != text.count("。"):
        raise AigcReviewModelError("受控句界实验没有保持逗号与句号总量。")
    if re.sub(r"[，。]", "", candidate) != re.sub(r"[，。]", "", text):
        raise AigcReviewModelError("受控句界实验改动了标点以外的正文。")
    return candidate, sorted(changes, key=lambda item: int(item["position"]))


_DISCOURSE_MARKER_REPLACEMENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Longer, context-bound phrases come first.  They prevent a generic
    # connector swap from producing broken combinations such as ``也所以``
    # or changing temporal ``同时`` into additive ``另外``.
    ("值得注意的是", ("需要注意的是",)),
    ("从这个角度", ("就这个角度看",)),
    ("这两者同时存在", ("这两者并存",)),
    ("进一步削弱", ("继续削弱",)),
    ("进一步推进", ("继续推进",)),
    ("进一步加强", ("继续加强",)),
    ("进一步完善", ("继续完善",)),
    ("同时也是", ("也是",)),
    ("同时也", ("也",)),
    ("也因此", ("也由此",)),
    ("不可否认", ("不能否认",)),
    ("综上所述", ("总的来看",)),
    ("总而言之", ("总的说来",)),
    ("也就是说", ("换句话说",)),
    ("问题在于", ("麻烦在于",)),
    ("值得注意", ("需要留意",)),
    ("事实上", ("实际上",)),
    ("然而", ("不过", "可是")),
    ("因此", ("所以", "因而")),
    ("此外", ("另外", "除此之外")),
    ("不仅", ("不只",)),
    ("恰恰", ("偏偏",)),
    ("正是", ("就是", "恰是")),
)


def discourse_marker_distribution_probe(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Replace only recurrent discourse markers outside quotes and headings.

    This is a deliberately narrow black-box variable.  It does not reorder
    claims, sentences, paragraphs, numbers, names or quotations, and it never
    calls a model.  The previous sentence-boundary experiment remains above
    only so historical reports can be reproduced; it is no longer selected.
    """
    quote_mask = _probe_quote_mask(text)
    heading_mask = _probe_heading_mask(text)
    replacements: list[tuple[int, int, str, str]] = []
    occupied: list[tuple[int, int]] = []
    replacement_counts: dict[str, int] = {}
    for marker, alternatives in sorted(
        _DISCOURSE_MARKER_REPLACEMENTS,
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        for match in re.finditer(re.escape(marker), text):
            start, end = match.span()
            if any(quote_mask[start:end]) or any(heading_mask[start:end]):
                continue
            if any(start < used_end and end > used_start for used_start, used_end in occupied):
                continue
            index = replacement_counts.get(marker, 0)
            replacement = alternatives[index % len(alternatives)]
            replacement_counts[marker] = index + 1
            if replacement == marker:
                continue
            replacements.append((start, end, marker, replacement))
            occupied.append((start, end))
    if not replacements:
        return text, []

    candidate = text
    changes: list[dict[str, Any]] = []
    for start, end, marker, replacement in sorted(replacements, reverse=True):
        candidate = candidate[:start] + replacement + candidate[end:]
        changes.append(
            {
                "position": start,
                "from": marker,
                "to": replacement,
                "marker": marker,
            }
        )
    return candidate, sorted(changes, key=lambda item: int(item["position"]))


def _detector_probe_units(
    text: str,
    *,
    min_chars: int = DEEP_RECAST_UNIT_MIN_CHARS,
    max_chars: int = DEEP_RECAST_UNIT_MAX_CHARS,
) -> list[EditorialUnit]:
    """Build probe units without ever crossing a detected section heading."""
    heading_starts = sorted({start for start, _end in _heading_spans(text)})
    if not heading_starts:
        return split_editorial_units(text, min_chars=min_chars, max_chars=max_chars)

    boundaries = [0, *heading_starts, len(text)]
    boundaries = sorted(set(boundaries))
    units: list[EditorialUnit] = []
    for range_start, range_end in zip(boundaries, boundaries[1:]):
        if range_end <= range_start:
            continue
        section = text[range_start:range_end]
        for local_unit in split_editorial_units(
            section,
            min_chars=min_chars,
            max_chars=max_chars,
        ):
            units.append(
                EditorialUnit(
                    unit_index=len(units) + 1,
                    start=range_start + local_unit.start,
                    end=range_start + local_unit.end,
                    text=local_unit.text,
                )
            )
    return units


def _controlled_probe_scope(
    original: str,
    segments: list[ZhuqueSegment],
    selected_ids: Iterable[int],
) -> tuple[ZhuqueSegment, EditorialUnit, str, list[dict[str, Any]]]:
    selected = {int(value) for value in selected_ids}
    candidates: list[tuple[int, float, ZhuqueSegment, EditorialUnit, str, list[dict[str, Any]]]] = []
    for segment in segments:
        if segment.segment_id not in selected or segment.label == "human":
            continue
        if segment.mapping_confidence < 0.65:
            continue
        source = original[segment.start:segment.end]
        if source != segment.mapped_text:
            raise ZhuqueReportError(f"片段 {segment.segment_id} 的映射正文已被修改，请重新解析报告。")
        for unit in _detector_probe_units(source, min_chars=1200, max_chars=2500):
            raw_unit_source = source[unit.start:unit.end]
            if len(raw_unit_source) < 1200:
                continue
            candidate, changes = discourse_marker_distribution_probe(raw_unit_source)
            if not changes:
                continue
            risk = segment.share_percent * (1.1 if segment.label == "ai" else 1.0)
            candidates.append((len(changes), risk, segment, unit, candidate, changes))
    if not candidates:
        raise ZhuqueReportError(
            "选中区域没有可形成 1200–2500 字高频连接标记对照块；请多选相邻风险片段或选择较长片段。"
        )
    _change_count, _risk, segment, unit, candidate, changes = max(
        candidates,
        key=lambda item: (item[0], item[1], len(item[3].text)),
    )
    return segment, unit, candidate, changes


def _build_v3_detector_probe_result(
    original: str,
    segments: Iterable[ZhuqueSegment],
    selected_ids: Iterable[int],
    *,
    content_type: str,
    rewrite: Callable[..., Any],
    rewrite_provider: str,
    rewrite_model: str,
    request_id: str,
    iteration: int,
    budget_cny: float,
) -> dict[str, Any]:
    """Run one falsifiable variable at a time; v1 multi-variable probing is retired."""
    _ = (rewrite, rewrite_provider, rewrite_model, budget_cny)
    if content_type == "classical":
        raise ZhuqueReportError("古文/佛经不参与跳过质量终审的朱雀探索实验。")
    if iteration != 1:
        raise ZhuqueReportError("请先上传本轮真实朱雀复检报告，再决定下一项单变量实验。")
    normalized_segments = list(segments)
    segment, unit, unit_candidate, changes = _controlled_probe_scope(
        original,
        normalized_segments,
        selected_ids,
    )
    global_start = segment.start + unit.start
    global_end = segment.start + unit.end
    assembled = _assemble_replacements(
        original,
        [(global_start, global_end, unit_candidate)],
    )
    source = original[global_start:global_end]
    deterministic = review_deterministic_constraints(
        source,
        unit_candidate,
        preserve_paragraph_count=True,
        min_length_ratio=0.98,
        max_length_ratio=1.02,
    )
    hard_safety_passed = deterministic_hard_safety_passed(
        deterministic,
        min_length_ratio=0.98,
        max_length_ratio=1.02,
    )
    if not hard_safety_passed:
        raise AigcReviewModelError("高频连接标记单变量实验未通过事实、锚点或格式保护。")
    attempt = {
        "attempt": 1,
        "passed": True,
        "hard_safety_passed": hard_safety_passed,
        "substantive_rewrite_passed": unit_candidate != source,
        "material_rewrite_advisory_passed": deterministic.material_rewrite_passed,
        "structured_output": True,
        "protocol_issues": [],
        "substantive_rewrite_reasons": [],
        "deterministic_review": deterministic.to_dict(),
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cost_cny": 0.0,
        "strategy_id": "controlled_probe:discourse_marker_distribution_only",
        "rewrite_provider": "local",
        "rewrite_model": "local-marker-rewrite-v1",
    }
    unit_result = {
        "unit_id": unit.unit_id,
        "original_chars": len(source),
        "candidate_chars": len(unit_candidate),
        "original_text": source,
        "candidate_text": unit_candidate,
        "status": "ready",
        "fact_ledger_mode": "not_used_detector_probe",
        "fact_ledger_claim_count": 0,
        "fact_ledger_cost_cny": 0.0,
        "hard_safety_passed": hard_safety_passed,
        "substantive_rewrite_passed": True,
        "rewrite_attempts": 1,
        "attempts": [attempt],
        "deterministic_review": deterministic.to_dict(),
        "candidate_preview": unit_candidate[:400],
        "rejected_candidate_preview": "",
        "probe_profile": "discourse_marker_distribution_only",
    }
    segment_result = {
        "segment_id": segment.segment_id,
        "strategy_id": "controlled_probe:discourse_marker_distribution_only",
        "rewrite_provider": "local",
        "rewrite_model": "local-marker-rewrite-v1",
        "status": "ready",
        "hard_safety_passed": hard_safety_passed,
        "substantive_rewrite_passed": True,
        "material_rewrite_advisory_passed": deterministic.material_rewrite_passed,
        "original_chars": len(source),
        "candidate_chars": len(unit_candidate),
        "report_aigc_value": segment.aigc_value,
        "report_label": segment.label,
        "style_diagnosis": diagnose_segment_style(source),
        "editorial_unit_count": 1,
        "rewrite_attempts": 1,
        "attempts": [attempt],
        "unit_results": [unit_result],
        "deterministic_review": deterministic.to_dict(),
        "style_feature_version": STYLE_FEATURE_VERSION,
        "style_feature_delta": style_feature_delta(source, unit_candidate),
        "candidate_preview": unit_candidate[:500],
        "rejected_candidate_preview": "",
    }
    return {
        "request_id": request_id,
        "status": "probe_ready",
        "deliverable": False,
        "next_action": (
            "高频连接标记单变量实验稿已生成：仅在一个宏块中替换反复出现的机器化连接标记，模型费用 0 元。"
            "请上传这一版的真实朱雀报告；未取得结果前不会追加其他变量。"
        ),
        "original_sha256": sha256_text(original),
        "candidate_sha256": sha256_text(assembled),
        "original_text": original,
        "candidate_text": assembled,
        "selected_segment_ids": [segment.segment_id],
        "iteration": iteration,
        "preferred_strategy": "controlled_probe:discourse_marker_distribution_only",
        "content_type": content_type,
        "experiment_type": "detector_probe",
        "segment_results": [segment_result],
        "quality_audit": None,
        "experimental_quality_bypassed": True,
        "editorial_unit_count": 1,
        "audit_batch_count": 0,
        "budget": {
            "currency": "CNY",
            "limit_cny": 0.0,
            "spent_cny": 0.0,
            "exhausted": False,
        },
        "cost": {
            "currency": "CNY",
            "estimated": False,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cny": 0.0,
        },
        "models": {
            "rewrite_provider": "local",
            "rewrite": "local-marker-rewrite-v1",
            "fact": "not_used_detector_probe",
            "audit": "skipped_detector_probe",
            "rewrite_thinking": "disabled",
        },
        "prompt_version": PROBE_PROMPT_VERSION,
        "detector_probe": {
            "mode": "controlled_single_variable",
            "temperature": 0.0,
            "profile_order": [],
            "processed_units": 1,
            "processed_segment_ids": [segment.segment_id],
            "budget_stopped": False,
            "variable": "discourse_marker_distribution_only",
            "scope_start": global_start,
            "scope_end": global_end,
            "scope_chars": len(source),
            "changed_marker_count": len(changes),
            "invariants": {
                "same_paragraph_count": source.count("\n") == unit_candidate.count("\n"),
                "same_sentence_order": True,
                "same_comma_count": source.count("，") == unit_candidate.count("，"),
                "same_full_stop_count": source.count("。") == unit_candidate.count("。"),
                "same_digits": re.findall(r"\d+(?:\.\d+)?", source)
                == re.findall(r"\d+(?:\.\d+)?", unit_candidate),
                "quotes_untouched": all(
                    not any(
                        _probe_quote_mask(source)[
                            int(item["position"]):int(item["position"]) + len(str(item["from"]))
                        ]
                    )
                    for item in changes
                ),
            },
            "changes": changes,
            "hypotheses": ["discourse_marker_distribution_sensitivity"],
            "legacy_failed_hypotheses": [
                "multi_profile_rotation",
                "uniform_shortening",
                "templated_first_person_reaction",
                "sentence_boundary_distribution_sensitivity",
            ],
        },
    }


def _deep_recast_scope_boundaries(text: str) -> tuple[list[int], set[int]]:
    """Return safe sentence/paragraph boundaries for one continuous recast scope."""
    paragraph_boundaries = {0, len(text)}
    for match in re.finditer(r"\n[\t ]*(?:\n[\t ]*)+", text):
        paragraph_boundaries.update((match.start(), match.end()))
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped and _looks_like_heading(stripped):
            start = offset + line.find(stripped)
            paragraph_boundaries.update((start, start + len(stripped)))
        offset += len(line)
    sentence_boundaries = set(paragraph_boundaries)
    sentence_boundaries.update(
        match.end()
        for match in re.finditer(r"[。！？!?；;](?:[”’\"']?)[\t ]*", text)
    )
    return sorted(sentence_boundaries), paragraph_boundaries


def _deep_recast_probe_scope(
    original: str,
    segments: list[ZhuqueSegment],
    selected_ids: Iterable[int],
) -> tuple[ZhuqueSegment, dict[str, Any]]:
    """Select one continuous 75%-85% high-risk span without trusting row numbers as boundaries."""
    selected = {int(value) for value in selected_ids}
    eligible: list[ZhuqueSegment] = []
    for segment in sorted(segments, key=lambda item: item.start):
        if segment.segment_id not in selected or segment.label == "human":
            continue
        if segment.mapping_confidence < 0.65:
            continue
        if segment.start < 0 or segment.end > len(original) or segment.start >= segment.end:
            raise ZhuqueReportError(f"片段 {segment.segment_id} 的原稿坐标无效。")
        if original[segment.start:segment.end] != segment.mapped_text:
            raise ZhuqueReportError(
                f"片段 {segment.segment_id} 的映射正文已经变化，请重新解析报告。"
            )
        eligible.append(segment)
    if not eligible:
        raise ZhuqueReportError("选中区域没有可用于强力组合实验的非人工特征正文。")

    scope_min = max(350, math.ceil(len(original) * DEEP_RECAST_SCOPE_MIN))
    scope_target = max(scope_min, round(len(original) * DEEP_RECAST_SCOPE_TARGET))
    scope_max = max(scope_min, math.floor(len(original) * DEEP_RECAST_SCOPE_MAX))
    selected_chars = sum(item.end - item.start for item in eligible)
    if selected_chars < scope_min:
        raise ZhuqueReportError(
            f"选中高风险正文只有 {selected_chars} 字；强力组合实验至少需要覆盖全稿约 75%（当前需 {scope_min} 字）。"
        )

    regions: list[tuple[int, int]] = [(item.start, item.end) for item in eligible]
    if len(eligible) > 1:
        union_start = min(item.start for item in eligible)
        union_end = max(item.end for item in eligible)
        covered = sum(
            max(0, min(union_end, item.end) - max(union_start, item.start))
            for item in eligible
        )
        if covered >= (union_end - union_start) * 0.96:
            regions.append((union_start, union_end))

    candidates: list[tuple[tuple[float, int, int, int], int, int, list[int], float, float]] = []
    seen_regions: set[tuple[int, int]] = set()
    for region_start, region_end in regions:
        if (region_start, region_end) in seen_regions or region_end - region_start < scope_min:
            continue
        seen_regions.add((region_start, region_end))
        region = original[region_start:region_end]
        boundaries, paragraph_boundaries = _deep_recast_scope_boundaries(region)
        for local_start in boundaries:
            minimum_end = local_start + scope_min
            maximum_end = min(len(region), local_start + scope_max)
            if minimum_end > maximum_end:
                continue
            lo = bisect_left(boundaries, minimum_end)
            hi = bisect_right(boundaries, maximum_end)
            if lo >= hi:
                continue
            target_index = bisect_left(boundaries, local_start + scope_target, lo, hi)
            end_indexes = {
                lo,
                hi - 1,
                max(lo, min(hi - 1, target_index)),
                max(lo, min(hi - 1, target_index - 1)),
            }
            for end_index in end_indexes:
                local_end = boundaries[end_index]
                span_start = region_start + local_start
                span_end = region_start + local_end
                span_chars = span_end - span_start
                if not scope_min <= span_chars <= scope_max:
                    continue
                overlaps: list[tuple[ZhuqueSegment, int]] = []
                for segment in eligible:
                    overlap = max(0, min(span_end, segment.end) - max(span_start, segment.start))
                    if overlap:
                        overlaps.append((segment, overlap))
                covered = sum(overlap for _segment, overlap in overlaps)
                if covered < span_chars * 0.96:
                    continue
                raw_risk = sum(
                    overlap * segment.aigc_value
                    for segment, overlap in overlaps
                ) / max(1, covered)
                priority = raw_risk + sum(
                    overlap * (0.015 if segment.label == "ai" else 0.0)
                    for segment, overlap in overlaps
                ) / max(1, covered)
                contributing_ids = [segment.segment_id for segment, _overlap in overlaps]
                confidence = min(segment.mapping_confidence for segment, _overlap in overlaps)
                paragraph_bonus = int(
                    local_start in paragraph_boundaries and local_end in paragraph_boundaries
                )
                score = (
                    priority,
                    paragraph_bonus,
                    -abs(span_chars - scope_target),
                    -len(_headings(original[span_start:span_end])),
                )
                candidates.append(
                    (score, span_start, span_end, contributing_ids, confidence, raw_risk)
                )
    if not candidates:
        raise ZhuqueReportError(
            "选中区域无法在自然句段边界上形成占全文 75%–85% 的连续实验范围；请多选相邻风险片段。"
        )

    _score, start, end, contributing_ids, confidence, risk = max(
        candidates,
        key=lambda item: item[0],
    )
    overlaps = [
        (segment, max(0, min(end, segment.end) - max(start, segment.start)))
        for segment in eligible
    ]
    overlaps = [(segment, overlap) for segment, overlap in overlaps if overlap]
    dominant = max(overlaps, key=lambda item: item[1])[0]
    share_percent = sum(
        segment.share_percent * overlap / max(1, segment.end - segment.start)
        for segment, overlap in overlaps
    )
    label = classify_aigc_value(min(1.0, risk))
    if label == "human":
        label = "suspected"
    synthetic = ZhuqueSegment(
        segment_id=dominant.segment_id,
        share_percent=round(share_percent, 4),
        char_count=end - start,
        aigc_value=min(1.0, risk),
        label=label,
        start=start,
        end=end,
        mapped_text=original[start:end],
        mapping_confidence=confidence,
        recommended=True,
    )
    return synthetic, {
        "scope_start": start,
        "scope_end": end,
        "scope_chars": end - start,
        "scope_percent": round((end - start) * 100 / max(1, len(original)), 4),
        "source_segment_ids": contributing_ids,
        "weighted_aigc_risk": round(risk, 6),
        "target_scope_percent": DEEP_RECAST_SCOPE_TARGET * 100,
    }


def build_detector_probe_result(
    original: str,
    segments: Iterable[ZhuqueSegment],
    selected_ids: Iterable[int],
    *,
    content_type: str,
    rewrite: Callable[..., Any],
    audit: Callable[..., Any],
    fact: Callable[..., Any] | None,
    request_id: str,
    iteration: int,
    budget_cny: float,
    cache_namespace: str | None,
) -> dict[str, Any]:
    """Build the v5 full-method detector probe through the dedicated prompt path."""
    _ = (audit, fact, cache_namespace)
    if content_type == "classical":
        raise ZhuqueReportError("古文/佛经不参加跳过质量终审的朱雀完整方法实验。")
    if iteration != 1:
        raise ZhuqueReportError("请先上传本轮真实朱雀复检报告，再决定下一轮实验。")
    if budget_cny <= 0:
        raise ZhuqueReportError("完整方法实验会调用 DeepSeek，预算必须大于 0 元。")

    return _build_full_method_detector_probe_result(
        original,
        segments,
        selected_ids,
        content_type=content_type,
        rewrite=rewrite,
        rewrite_provider="deepseek",
        rewrite_model=_rewrite_model_for_provider("deepseek"),
        request_id=request_id,
        iteration=1,
        budget_cny=budget_cny,
    )


def optimize_report_segments(
    original_text: str,
    segments: Iterable[ZhuqueSegment | dict[str, Any]],
    selected_segment_ids: Iterable[int],
    *,
    content_type: str = "article",
    iteration: int = 1,
    preferred_strategy: str | None = None,
    rewrite_provider: str | None = None,
    rewrite_reasoning_effort: str = "low",
    rewrite_fn: Callable[..., Any] | None = None,
    audit_fn: Callable[..., Any] | None = None,
    fact_fn: Callable[..., Any] | None = None,
    cache_namespace: str | None = None,
    budget_cny: float | None = None,
    experiment_type: str = "syntax_constrained",
    respect_headings: bool = False,
    preserve_paragraph_structure: bool = False,
    audit_batch_units: int | None = None,
    allow_unit_rollback: bool = False,
) -> dict[str, Any]:
    """Run one quality-safe v5 experiment, then apply hard and semantic gates."""
    request_id = str(uuid4())
    original = normalize_manuscript_text(original_text)
    normalized_segments = [
        item if isinstance(item, ZhuqueSegment) else ZhuqueSegment(**item) for item in segments
    ]
    by_id = {item.segment_id: item for item in normalized_segments}
    selected_ids = sorted(set(int(value) for value in selected_segment_ids))
    if not selected_ids:
        raise ZhuqueReportError("至少选择一个朱雀风险片段。")
    missing = [value for value in selected_ids if value not in by_id]
    if missing:
        raise ZhuqueReportError(f"报告中不存在片段: {missing}")
    if experiment_type != "detector_probe" and len(selected_ids) > 12:
        raise ZhuqueReportError("单轮最多处理 12 个相邻风险片段。")
    if (
        experiment_type != "detector_probe"
        and any(right != left + 1 for left, right in zip(selected_ids, selected_ids[1:]))
    ):
        raise ZhuqueReportError("单轮必须选择连续相邻的风险片段，不能把分散区域混入同一实验。")
    if not original:
        raise ZhuqueReportError("原稿不能为空。")
    if experiment_type not in {"layout_only", "syntax_constrained", "combined", "detector_probe"}:
        raise ZhuqueReportError("未知实验类型。")

    if experiment_type == "layout_only":
        return build_layout_only_result(
            original,
            normalized_segments,
            selected_ids,
            content_type=content_type,
            request_id=request_id,
        )

    if experiment_type == "detector_probe":
        selected_provider = _normalize_rewrite_provider(rewrite_provider)
        if selected_provider != "deepseek":
            raise ZhuqueReportError("强力组合实验固定使用低成本 DeepSeek，避免无意义增加模型费用。")
        if rewrite_fn is None:
            _ensure_rewrite_provider_configured("deepseek")
        effective_budget = float(
            budget_cny
            if budget_cny is not None
            else settings.AIGC_REVIEW_RUN_BUDGET_CNY
        )
        return build_detector_probe_result(
            original,
            normalized_segments,
            selected_ids,
            content_type=content_type,
            rewrite=rewrite_fn or _rewrite_callable("deepseek"),
            audit=audit_fn or _gemini_audit,
            fact=fact_fn,
            request_id=request_id,
            iteration=iteration,
            budget_cny=effective_budget,
            cache_namespace=cache_namespace,
        )

    selected_provider = _normalize_rewrite_provider(rewrite_provider)
    rewrite_model = _rewrite_model_for_provider(selected_provider)
    selected_reasoning = (
        _normalize_grok_reasoning(rewrite_reasoning_effort)
        if selected_provider == "grok"
        else "disabled"
    )
    if rewrite_fn is None:
        _ensure_rewrite_provider_configured(selected_provider)
    rewrite = rewrite_fn or _rewrite_callable(selected_provider)
    effective_budget = float(
        budget_cny
        if budget_cny is not None
        else settings.AIGC_REVIEW_RUN_BUDGET_CNY
    )
    if effective_budget <= 0:
        raise ZhuqueReportError("单次预算必须大于 0 元。")
    audit = audit_fn or _gemini_audit
    # Production defaults to a local sentence ledger.  It is complete by
    # construction, costs nothing, and cannot fail because a model truncates
    # JSON.  ``fact_fn`` remains an injectable comparison hook; any failure in
    # that optional path falls back locally before the paid rewrite starts.
    extract_facts = fact_fn
    fact_backends_used: set[str] = set()
    replacements: list[tuple[int, int, str]] = []
    segment_results: list[dict[str, Any]] = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_model_cost = 0.0
    audit_items: list[dict[str, Any]] = []
    preserve_paragraph_count = content_type == "classical" or preserve_paragraph_structure
    def budget_exhausted() -> bool:
        return total_model_cost >= effective_budget

    def manual_result(reason: str, *, guarded_text: str = "") -> dict[str, Any]:
        fact_model = _fact_model_label(fact_backends_used, content_type=content_type)
        result = _manual_review_result(
            request_id=request_id,
            original=original,
            selected_ids=selected_ids,
            segment_results=segment_results,
            reason=reason,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            rewrite_cost_cny=total_model_cost,
            rewrite_provider=selected_provider,
            rewrite_model=rewrite_model,
            rewrite_reasoning=selected_reasoning,
            fact_model=fact_model,
            guarded_candidate_text=guarded_text,
        )
        result["budget"] = {
            "currency": "CNY",
            "limit_cny": round(effective_budget, 4),
            "spent_cny": round(total_model_cost, 6),
            "exhausted": budget_exhausted(),
        }
        result["editorial_unit_count"] = len(audit_items)
        result["experiment_type"] = experiment_type
        return result

    if iteration < 1 or iteration > 20:
        raise ZhuqueReportError("迭代轮次必须在 1 到 20 之间。")
    available_strategies = rewrite_strategy_map()
    if preferred_strategy and preferred_strategy not in available_strategies:
        raise ZhuqueReportError(f"未知改写策略: {preferred_strategy}")
    for selected_index, segment_id in enumerate(selected_ids):
        segment = by_id[segment_id]
        strategy_id = preferred_strategy or strategy_for_index(selected_index, iteration=iteration)
        strategy_rules = available_strategies[strategy_id]
        if segment.mapping_confidence < 0.65:
            raise ZhuqueReportError(f"片段 {segment_id} 映射置信度过低，禁止自动改写。")
        if segment.start < 0 or segment.end > len(original) or segment.start >= segment.end:
            raise ZhuqueReportError(f"片段 {segment_id} 的原稿坐标无效。")
        source = original[segment.start:segment.end]
        if segment.mapped_text != source:
            raise ZhuqueReportError(f"片段 {segment_id} 的映射正文已被修改，请重新解析报告。")
        if segment.label == "human":
            raise ZhuqueReportError(f"片段 {segment_id} 已是人工特征，不应产生改写费用。")
        protected_originals = (
            _classical_protected_fragments(original, segment.start, segment.end)
            if content_type == "classical"
            else ()
        )
        style_diagnosis = diagnose_segment_style(source)
        max_attempts = max(1, min(2, int(settings.AIGC_REVIEW_MAX_REWRITE_ATTEMPTS)))
        if content_type == "classical":
            units = [EditorialUnit(1, 0, len(source), source)]
        elif respect_headings:
            units = _detector_probe_units(
                source,
                min_chars=DEEP_RECAST_UNIT_MIN_CHARS,
                max_chars=DEEP_RECAST_UNIT_MAX_CHARS,
            )
        else:
            units = split_editorial_units(source)
        unit_candidates: list[str] = []
        unit_replacements: list[tuple[int, int, str]] = []
        unit_results: list[dict[str, Any]] = []
        segment_candidate_safe = True

        for unit in units:
            if budget_exhausted():
                segment_candidate_safe = False
                break
            unit_source = unit.text
            unit_absolute_start = segment.start + unit.start
            unit_absolute_end = segment.start + unit.end
            previous_context = original[max(0, unit_absolute_start - 350):unit_absolute_start]
            next_context = original[unit_absolute_end:min(len(original), unit_absolute_end + 350)]
            unit_segment = ZhuqueSegment(
                segment_id=segment.segment_id,
                share_percent=segment.share_percent,
                char_count=len(unit_source),
                aigc_value=segment.aigc_value,
                label=segment.label,
                start=unit_absolute_start,
                end=unit_absolute_end,
                mapped_text=unit_source,
                mapping_confidence=segment.mapping_confidence,
                recommended=segment.recommended,
            )
            unit_protected = (
                _classical_protected_fragments(
                    original,
                    unit_absolute_start,
                    unit_absolute_end,
                )
                if content_type == "classical"
                else ()
            )
            protocol = prepare_rewrite_protocol(
                unit_source,
                protected_texts=unit_protected,
                flexible_paragraphs=(
                    content_type != "classical" and not preserve_paragraph_structure
                ),
            )
            required_tokens = [token for token, _ in protocol.protected_values]
            ledger: dict[str, Any] | None = None
            ledger_mode = "classical_legacy"
            ledger_usage: dict[str, Any] = {}

            if content_type != "classical":
                source_with_tokens = "\n".join(protocol.block_payloads)
                ledger_usage = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_cny": 0.0,
                }
                ledger = _load_cached_fact_ledger(
                    source_with_tokens,
                    cache_namespace=cache_namespace,
                    required_tokens=required_tokens,
                )
                if ledger is not None:
                    ledger_mode = "cached"
                    fact_backends_used.add("cached")
                elif extract_facts is None:
                    ledger = deterministic_fact_ledger(source_with_tokens)
                    ledger = parse_fact_ledger(
                        json.dumps(ledger, ensure_ascii=False),
                        required_tokens=required_tokens,
                    )
                    ledger_mode = "local_deterministic"
                    fact_backends_used.add("local")
                    _persist_fact_ledger(
                        source_with_tokens,
                        ledger,
                        cache_namespace=cache_namespace,
                    )
                else:
                    ledger_prompt = render_fact_ledger_prompt(
                        source_with_tokens,
                        protected_map=protocol.protected_map,
                        content_type=content_type,
                    )
                    try:
                        raw_ledger = extract_facts(
                            ledger_prompt,
                            segment=unit_segment,
                            request_id=request_id,
                        )
                        ledger_text, one_usage = _coerce_model_result(raw_ledger)
                        ledger_input = int(
                            one_usage.get("input_tokens")
                            or _estimate_tokens(ledger_prompt)
                        )
                        ledger_output = int(
                            one_usage.get("output_tokens")
                            or _estimate_tokens(ledger_text)
                        )
                        ledger_cost = float(one_usage.get("cost_cny") or 0.0)
                        total_input_tokens += ledger_input
                        total_output_tokens += ledger_output
                        total_model_cost += ledger_cost
                        ledger_usage["input_tokens"] = ledger_input
                        ledger_usage["output_tokens"] = ledger_output
                        ledger_usage["cost_cny"] = ledger_cost
                        ledger = parse_fact_ledger(
                            ledger_text,
                            required_tokens=required_tokens,
                        )
                        copied_claims = fact_ledger_copying_reasons(
                            source_with_tokens,
                            ledger,
                        )
                        if copied_claims:
                            raise ValueError("；".join(copied_claims))
                        ledger_mode = "model"
                        fact_backends_used.add("model")
                    except Exception as exc:
                        # A fact ledger is a deterministic representation of the
                        # supplied source, so a truncated/invalid optional model
                        # response must not abort the paid editing run.  Fall back
                        # immediately instead of paying for another verbose JSON
                        # attempt.
                        logger.warning(
                            "LLM action=aigc_report_fact_ledger request_id=%s segment=%s unit=%s status=local_fallback error_type=%s error=%s",
                            request_id,
                            segment_id,
                            unit.unit_id,
                            type(exc).__name__,
                            exc,
                        )
                        ledger = deterministic_fact_ledger(source_with_tokens)
                        ledger = parse_fact_ledger(
                            json.dumps(ledger, ensure_ascii=False),
                            required_tokens=required_tokens,
                        )
                        ledger_mode = "local_fallback"
                        fact_backends_used.add("local_fallback")
                    _persist_fact_ledger(
                        source_with_tokens,
                        ledger,
                        cache_namespace=cache_namespace,
                    )

            unit_candidate = ""
            deterministic = review_deterministic_constraints(
                unit_source,
                unit_candidate,
                protected_texts=unit_protected,
                require_material_rewrite=False,
                preserve_paragraph_count=preserve_paragraph_count,
                min_length_ratio=(0.8 if content_type == "classical" else QUALITY_SAFE_LENGTH_MIN),
                max_length_ratio=(1.25 if content_type == "classical" else QUALITY_SAFE_LENGTH_MAX),
            )
            candidate_safe = False
            attempt_results: list[dict[str, Any]] = []
            retry_feedback = "首次重著：按事实账本重新组织正文，禁止沿用原句骨架。"

            for attempt in range(1, max_attempts + 1):
                if budget_exhausted():
                    break
                if content_type == "classical":
                    prompt = render_rewrite_prompt(
                        segment_id=segment_id,
                        original_segment=protocol.prompt_segment,
                        previous_context=previous_context,
                        next_context=next_context,
                        protected_originals=unit_protected,
                        protected_map=protocol.protected_map,
                        block_count=len(protocol.block_payloads),
                        retry_feedback=retry_feedback,
                        content_type=content_type,
                        strategy_id=strategy_id,
                        strategy_rules=strategy_rules,
                        segment_signal="只保护古文原文并重写译文；不得根据检测分数做文字表演。",
                        style_diagnosis=style_diagnosis,
                        learned_evidence="不注入检测器猜测。",
                        flexible_paragraphs=False,
                    )
                else:
                    prompt = render_reauthor_prompt(
                        ledger or {},
                        original_with_tokens=source_with_tokens,
                        protected_map=protocol.protected_map,
                        previous_context=previous_context,
                        next_context=next_context,
                        content_type=content_type,
                        strategy_rules=strategy_rules,
                        source_chars=len(unit_source),
                        retry_feedback=retry_feedback,
                        controlled_topology=(strategy_id == "controlled_topology"),
                        maximal_recast=(strategy_id == "maximal_variance"),
                        preserve_paragraph_structure=preserve_paragraph_structure,
                    )
                try:
                    rewrite_kwargs = {
                        "segment": unit_segment,
                        "request_id": request_id,
                        "attempt": attempt,
                    }
                    if selected_provider == "grok":
                        rewrite_kwargs["reasoning_effort"] = selected_reasoning
                    raw = rewrite(prompt, **rewrite_kwargs)
                    raw_candidate, usage = _coerce_model_result(raw)
                except Exception as exc:
                    logger.exception(
                        "LLM action=aigc_report_rewrite request_id=%s provider=%s segment=%s unit=%s attempt=%s model=%s status=failed error_type=%s",
                        request_id,
                        selected_provider,
                        segment_id,
                        unit.unit_id,
                        attempt,
                        rewrite_model,
                        type(exc).__name__,
                    )
                    raise AigcReviewModelError(
                        f"片段 {segment_id}/{unit.unit_id} 改写模型调用失败: {exc}"
                    ) from exc

                cleaned_candidate = _clean_model_text(raw_candidate)
                unit_candidate, protocol_issues, structured = parse_rewrite_candidate(
                    cleaned_candidate,
                    protocol,
                )
                deterministic = review_deterministic_constraints(
                    unit_source,
                    unit_candidate,
                    protected_texts=unit_protected,
                    require_material_rewrite=False,
                    preserve_paragraph_count=preserve_paragraph_count,
                    min_length_ratio=(0.8 if content_type == "classical" else QUALITY_SAFE_LENGTH_MIN),
                    max_length_ratio=(1.25 if content_type == "classical" else QUALITY_SAFE_LENGTH_MAX),
                )
                protocol_passed = not protocol_issues or not structured
                hard_safety_passed = deterministic_hard_safety_passed(
                    deterministic,
                    require_paragraph_preservation=preserve_paragraph_count,
                    min_length_ratio=(0.8 if content_type == "classical" else QUALITY_SAFE_LENGTH_MIN),
                    max_length_ratio=(1.25 if content_type == "classical" else QUALITY_SAFE_LENGTH_MAX),
                )
                substantive_passed = substantive_rewrite_passed(
                    deterministic,
                    require_text_change=content_type != "classical",
                )
                candidate_safe = hard_safety_passed and protocol_passed and substantive_passed
                input_tokens = int(usage.get("input_tokens") or _estimate_tokens(prompt))
                cached_input_tokens = int(usage.get("cached_input_tokens") or 0)
                output_tokens = int(
                    usage.get("output_tokens") or _estimate_tokens(cleaned_candidate)
                )
                reasoning_tokens = int(usage.get("reasoning_tokens") or 0)
                attempt_cost = float(usage.get("cost_cny") or 0.0)
                total_input_tokens += input_tokens
                total_output_tokens += output_tokens
                total_model_cost += attempt_cost
                attempt_results.append(
                    {
                        "attempt": attempt,
                        "passed": candidate_safe,
                        "hard_safety_passed": hard_safety_passed,
                        "substantive_rewrite_passed": substantive_passed,
                        "material_rewrite_advisory_passed": deterministic.material_rewrite_passed,
                        "structured_output": structured,
                        "protocol_issues": protocol_issues,
                        "substantive_rewrite_reasons": substantive_rewrite_reasons(
                            deterministic,
                            require_text_change=content_type != "classical",
                        ),
                        "deterministic_review": deterministic.to_dict(),
                        "input_tokens": input_tokens,
                        "cached_input_tokens": cached_input_tokens,
                        "output_tokens": output_tokens,
                        "reasoning_tokens": reasoning_tokens,
                        "cost_cny": round(attempt_cost, 6),
                        "strategy_id": strategy_id,
                        "rewrite_provider": selected_provider,
                        "rewrite_model": rewrite_model,
                    }
                )
                if candidate_safe:
                    break
                hard_feedback = format_rewrite_retry_feedback(
                    deterministic,
                    protocol_issues=protocol_issues,
                    hard_safety_only=True,
                )
                retry_feedback = hard_feedback + "\n- " + "\n- ".join(
                    substantive_rewrite_reasons(
                        deterministic,
                        require_text_change=content_type != "classical",
                    )
                    or ["候选没有形成可验证的实质重著"]
                )

            unit_results.append(
                {
                    "unit_id": unit.unit_id,
                    "original_chars": len(unit_source),
                    "candidate_chars": len(unit_candidate),
                    "original_text": unit_source,
                    "candidate_text": unit_candidate if candidate_safe else "",
                    "status": "ready" if candidate_safe else "rejected",
                    "fact_ledger_mode": ledger_mode,
                    "fact_ledger_claim_count": len((ledger or {}).get("claims") or []),
                    "fact_ledger_cost_cny": round(float(ledger_usage.get("cost_cny") or 0.0), 6),
                    "fact_ledger": ledger or {},
                    "hard_safety_passed": deterministic_hard_safety_passed(
                        deterministic,
                        require_paragraph_preservation=preserve_paragraph_count,
                        min_length_ratio=(0.8 if content_type == "classical" else QUALITY_SAFE_LENGTH_MIN),
                        max_length_ratio=(1.25 if content_type == "classical" else QUALITY_SAFE_LENGTH_MAX),
                    ),
                    "substantive_rewrite_passed": substantive_rewrite_passed(
                        deterministic,
                        require_text_change=content_type != "classical",
                    ),
                    "rewrite_attempts": len(attempt_results),
                    "attempts": attempt_results,
                    "deterministic_review": deterministic.to_dict(),
                    "candidate_preview": unit_candidate[:400] if candidate_safe else "",
                    "rejected_candidate_preview": unit_candidate[:400] if not candidate_safe else "",
                }
            )
            if not candidate_safe:
                if allow_unit_rollback and not budget_exhausted():
                    unit_results[-1]["status"] = "rolled_back"
                    continue
                segment_candidate_safe = False
                break
            unit_candidates.append(unit_candidate.strip())
            unit_replacements.append((unit.start, unit.end, unit_candidate.strip()))
            audit_items.append(
                {
                    "audit_id": f"{segment_id}.{unit.unit_index}",
                    "segment_id": segment_id,
                    "unit_id": unit.unit_id,
                    "original": unit_source,
                    "candidate": unit_candidate,
                    "previous_context": previous_context,
                    "next_context": next_context,
                    "fact_ledger": ledger or {},
                }
            )

        candidate = (
            _assemble_replacements(source, unit_replacements)
            if unit_replacements
            else ""
        )
        if experiment_type == "combined" and content_type != "classical":
            candidate = reflow_layout_only(candidate)
        parent_review = review_deterministic_constraints(
            source,
            candidate,
            protected_texts=protected_originals,
            require_material_rewrite=False,
            preserve_paragraph_count=preserve_paragraph_count,
            min_length_ratio=(0.8 if content_type == "classical" else QUALITY_SAFE_LENGTH_MIN),
            max_length_ratio=(1.25 if content_type == "classical" else QUALITY_SAFE_LENGTH_MAX),
        )
        parent_hard_passed = deterministic_hard_safety_passed(
            parent_review,
            require_paragraph_preservation=preserve_paragraph_count,
            min_length_ratio=(0.8 if content_type == "classical" else QUALITY_SAFE_LENGTH_MIN),
            max_length_ratio=(1.25 if content_type == "classical" else QUALITY_SAFE_LENGTH_MAX),
        )
        parent_substantive_passed = substantive_rewrite_passed(
            parent_review,
            require_text_change=content_type != "classical",
        )
        completed_unit_requirement = (
            bool(unit_candidates)
            if allow_unit_rollback
            else len(unit_candidates) == len(units)
        )
        segment_candidate_safe = (
            segment_candidate_safe
            and completed_unit_requirement
            and parent_hard_passed
            and parent_substantive_passed
        )
        segment_results.append(
            {
                "segment_id": segment_id,
                "strategy_id": strategy_id,
                "rewrite_provider": selected_provider,
                "rewrite_model": rewrite_model,
                "status": "ready" if segment_candidate_safe else "rejected",
                "hard_safety_passed": parent_hard_passed,
                "substantive_rewrite_passed": parent_substantive_passed,
                "material_rewrite_advisory_passed": parent_review.material_rewrite_passed,
                "original_chars": len(source),
                "candidate_chars": len(candidate),
                "report_aigc_value": segment.aigc_value,
                "report_label": segment.label,
                "style_diagnosis": style_diagnosis,
                "editorial_unit_count": len(units),
                "rolled_back_unit_count": sum(
                    item.get("status") == "rolled_back" for item in unit_results
                ),
                "rewrite_attempts": sum(item["rewrite_attempts"] for item in unit_results),
                "attempts": [
                    attempt
                    for unit_result in unit_results
                    for attempt in unit_result.get("attempts") or []
                ],
                "unit_results": unit_results,
                "deterministic_review": parent_review.to_dict(),
                "style_feature_version": STYLE_FEATURE_VERSION,
                "style_feature_delta": style_feature_delta(source, candidate) if candidate else {},
                "candidate_preview": candidate[:500] if segment_candidate_safe else "",
                "rejected_candidate_preview": candidate[:500] if not segment_candidate_safe else "",
            }
        )
        if not segment_candidate_safe:
            partial_replacements = list(replacements)
            if unit_replacements:
                partial_replacements.append(
                    (segment.start, segment.end, _assemble_replacements(source, unit_replacements))
                )
            guarded_partial = _assemble_replacements(original, partial_replacements)
            if budget_exhausted():
                reason = (
                    f"已达到本轮 ¥{effective_budget:.2f} 预算上限，停止后续付费调用；"
                    "原稿未被覆盖，可在人工调整后只重跑未完成单元。"
                )
            else:
                reason = (
                    f"片段 {segment_id} 未同时通过事实/锚点/格式与实质重著硬门槛，"
                    "已停止后续付费调用并保留此前安全候选。"
                )
            return manual_result(
                reason,
                guarded_text=(guarded_partial if guarded_partial != original else ""),
            )
        replacements.append((segment.start, segment.end, candidate))

    assembled = _assemble_replacements(original, replacements)
    if budget_exhausted():
        return manual_result(
            f"改写阶段已达到本轮 ¥{effective_budget:.2f} 预算上限，未继续产生终审费用。",
            guarded_text=assembled,
        )

    audit_batch_size = max(
        1,
        int(
            audit_batch_units
            if audit_batch_units is not None
            else settings.AIGC_REVIEW_AUDIT_BATCH_UNITS
        ),
    )
    audit_results: list[dict[str, Any]] = []
    audit_estimated = False
    for batch_start in range(0, len(audit_items), audit_batch_size):
        if budget_exhausted():
            return manual_result(
                f"终审阶段已达到本轮 ¥{effective_budget:.2f} 预算上限，停止剩余审稿调用。",
                guarded_text=assembled,
            )
        batch = audit_items[batch_start:batch_start + audit_batch_size]
        original_blocks = [
            (
                f"【编辑单元 {item['audit_id']} 上文】\n{item['previous_context']}\n"
                f"【原单元】\n{item['original']}\n【下文】\n{item['next_context']}"
            )
            for item in batch
        ]
        candidate_blocks = [
            (
                f"【编辑单元 {item['audit_id']} 上文】\n{item['previous_context']}\n"
                f"【候选单元】\n{item['candidate']}\n【下文】\n{item['next_context']}"
            )
            for item in batch
        ]
        audit_prompt = render_audit_prompt(
            "\n\n".join(original_blocks),
            "\n\n".join(candidate_blocks),
            [item["audit_id"] for item in batch],
            content_type=content_type,
            fact_ledgers={
                str(item["audit_id"]): item.get("fact_ledger") or {}
                for item in batch
            },
        )
        try:
            raw_audit = audit(audit_prompt, request_id=request_id)
            audit_text, audit_usage = _coerce_model_result(raw_audit)
            batch_audit = parse_quality_audit(audit_text)
            claim_coverage_reasons = quality_audit_claim_coverage_reasons(
                batch_audit,
                {
                    str(item["audit_id"]): item.get("fact_ledger") or {}
                    for item in batch
                },
            )
            if claim_coverage_reasons:
                batch_audit["pass"] = False
                batch_audit.setdefault("issues", []).append(
                    {
                        "segment_id": "batch",
                        "type": "claim_coverage",
                        "original_claim": "；".join(claim_coverage_reasons),
                        "candidate_change": "终审未逐条覆盖事实账本",
                        "severity": "high",
                    }
                )
        except Exception as exc:
            logger.exception(
                "LLM action=aigc_report_quality_audit request_id=%s model=%s batch=%s status=failed error_type=%s",
                request_id,
                settings.AIGC_REVIEW_AUDIT_MODEL,
                batch_start // audit_batch_size + 1,
                type(exc).__name__,
            )
            reason = (
                "独立终审输出达到模型额度上限；已保留完成确定性保护的候选稿，"
                "但在终审完整通过前不会交付，请稍后重试或人工复核。"
                if isinstance(exc, GeminiMaxTokensError)
                else "独立终审暂时不可用；已保留完成确定性保护的候选稿，"
                "但在终审完整通过前不会交付，请稍后重试或人工复核。"
            )
            result = manual_result(reason, guarded_text=assembled)
            result["audit_batch_count"] = len(audit_results)
            result["technical_failure"] = {
                "stage": "quality_audit",
                "type": type(exc).__name__,
                "retryable": True,
                "completed_audit_batches": len(audit_results),
                "failed_batch": batch_start // audit_batch_size + 1,
            }
            return result
        audit_input = int(audit_usage.get("input_tokens") or _estimate_tokens(audit_prompt))
        audit_output = int(audit_usage.get("output_tokens") or _estimate_tokens(audit_text))
        audit_cost = float(
            audit_usage.get("cost_cny")
            or _gemini_flash_lite_cost_cny(audit_input, audit_output)
        )
        total_input_tokens += audit_input
        total_output_tokens += audit_output
        total_model_cost += audit_cost
        audit_estimated = audit_estimated or bool(audit_usage.get("estimated")) or not bool(
            audit_usage.get("input_tokens")
        )
        batch_audit["audit_unit_ids"] = [item["audit_id"] for item in batch]
        audit_results.append(batch_audit)

    quality_audit = combine_quality_audits(audit_results)
    quality_passed = quality_audit_passed(quality_audit)
    if not quality_passed:
        result = manual_result(
            "分批独立终审未达到质量硬标准；候选只供人工修订，原稿保持不变。",
            guarded_text=assembled,
        )
        result["quality_audit"] = quality_audit
        result["audit_batch_count"] = len(audit_results)
        return result

    return {
        "request_id": request_id,
        "status": "candidate_ready",
        "deliverable": False,
        "next_action": "上传候选稿的真实朱雀复检 PDF；官方首页人工特征达到 25% 且质量硬闸门仍通过后才可标记通过。",
        "original_sha256": sha256_text(original),
        "candidate_sha256": sha256_text(assembled),
        "original_text": original,
        "candidate_text": assembled,
        "selected_segment_ids": selected_ids,
        "iteration": iteration,
        "preferred_strategy": preferred_strategy,
        "content_type": content_type,
        "experiment_type": experiment_type,
        "segment_results": segment_results,
        "quality_audit": quality_audit,
        "editorial_unit_count": len(audit_items),
        "audit_batch_count": len(audit_results),
        "budget": {
            "currency": "CNY",
            "limit_cny": round(effective_budget, 4),
            "spent_cny": round(total_model_cost, 6),
            "exhausted": budget_exhausted(),
        },
        "cost": {
            "currency": "CNY",
            "estimated": audit_estimated,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "estimated_cny": round(total_model_cost, 6),
        },
        "models": {
            "rewrite_provider": selected_provider,
            "rewrite": rewrite_model,
            "fact": _fact_model_label(fact_backends_used, content_type=content_type),
            "audit": settings.AIGC_REVIEW_AUDIT_MODEL,
            "rewrite_thinking": selected_reasoning,
        },
        "prompt_version": PROMPT_VERSION,
    }


def review_deterministic_constraints(
    original: str,
    candidate: str,
    *,
    protected_texts: Iterable[str] = (),
    require_material_rewrite: bool = False,
    preserve_paragraph_count: bool = True,
    min_length_ratio: float = 0.8,
    max_length_ratio: float = 1.25,
) -> DeterministicReview:
    reasons: list[str] = []
    ratio = len(candidate) / max(1, len(original))
    if not min_length_ratio <= ratio <= max_length_ratio:
        reasons.append(
            f"长度比例 {ratio:.3f} 不在 {min_length_ratio:.2f}-{max_length_ratio:.2f}"
        )
    paragraph_ok = _paragraph_count(original) == _paragraph_count(candidate)
    if preserve_paragraph_count and not paragraph_ok:
        reasons.append("自然段数量发生变化")
    headings_ok = _headings(original) == _headings(candidate)
    if not headings_ok:
        reasons.append("标题或章节标记发生变化")
    original_anchors = _anchors(original)
    candidate_anchors = _anchors(candidate)
    missing: list[str] = []
    extra: list[str] = []
    for anchor, count in original_anchors.items():
        if candidate_anchors[anchor] < count:
            missing.extend([anchor] * (count - candidate_anchors[anchor]))
    for anchor, count in candidate_anchors.items():
        if original_anchors[anchor] < count:
            extra.extend([anchor] * (count - original_anchors[anchor]))
    anchor_order_ok = _anchor_sequence(original) == _anchor_sequence(candidate)
    anchors_ok = not missing and not extra and anchor_order_ok
    if not anchors_ok:
        if missing or extra:
            reasons.append("数字、日期、金额、引语、书名或 URL 锚点出现次数发生变化")
        if not anchor_order_ok:
            reasons.append("数字、日期、金额、引语、书名或 URL 锚点顺序发生变化")
    protected_values = tuple(value for value in protected_texts if value)
    protected_ok = all(candidate.count(value) >= original.count(value) for value in protected_values)
    if not protected_ok:
        reasons.append("古文/佛经受保护原文被改动")
    model_explanation_absent = not _contains_model_explanation(candidate)
    if not model_explanation_absent:
        reasons.append("候选包含模型说明、改写提示或非正文包装")
    hard_errors = _new_hard_errors(original, candidate)
    hard_error_absent = not hard_errors
    if hard_errors:
        reasons.extend(hard_errors)
    similarity, rewritten_ratio, unchanged_count, editable_count = material_rewrite_metrics(
        original,
        candidate,
        protected_texts=protected_values,
    )
    required_coverage = material_paragraph_coverage_requirement(editable_count)
    material_passed = (
        editable_count == 0
        or (
            MATERIAL_SIMILARITY_MIN <= similarity <= MATERIAL_SIMILARITY_MAX
            and rewritten_ratio >= required_coverage
        )
    )
    if require_material_rewrite and not material_passed:
        if similarity > MATERIAL_SIMILARITY_MAX:
            reasons.append(
                f"实质改写不足：可改正文相似度 {similarity:.3f}，必须不高于 {MATERIAL_SIMILARITY_MAX:.2f}"
            )
        elif similarity < MATERIAL_SIMILARITY_MIN:
            reasons.append(
                f"改写偏离过大：可改正文相似度 {similarity:.3f}，必须不低于 {MATERIAL_SIMILARITY_MIN:.2f}"
            )
        if rewritten_ratio < required_coverage:
            reasons.append(
                f"实质改写覆盖率 {rewritten_ratio:.1%}，当前 {editable_count} 个可编辑段落至少需要 {required_coverage:.1%}"
            )
    passed = not reasons and bool(candidate.strip())
    return DeterministicReview(
        passed=passed,
        length_ratio=round(ratio, 4),
        paragraph_count_preserved=paragraph_ok,
        headings_preserved=headings_ok,
        anchors_preserved=anchors_ok,
        protected_text_preserved=protected_ok,
        model_explanation_absent=model_explanation_absent,
        hard_error_absent=hard_error_absent,
        material_rewrite_passed=material_passed,
        literal_similarity=round(similarity, 4),
        rewritten_paragraph_ratio=round(rewritten_ratio, 4),
        required_paragraph_coverage=round(required_coverage, 4),
        unchanged_editable_paragraphs=unchanged_count,
        editable_paragraphs=editable_count,
        missing_anchors=tuple(missing[:30]),
        extra_anchors=tuple(extra[:30]),
        reasons=tuple(reasons),
    )


def deterministic_hard_safety_passed(
    review: DeterministicReview,
    *,
    require_paragraph_preservation: bool = True,
    min_length_ratio: float = 0.8,
    max_length_ratio: float = 1.25,
) -> bool:
    """Protect manuscript integrity without treating detector heuristics as a gate."""
    return (
        min_length_ratio <= review.length_ratio <= max_length_ratio
        and (review.paragraph_count_preserved or not require_paragraph_preservation)
        and review.headings_preserved
        and review.anchors_preserved
        and review.protected_text_preserved
        and review.model_explanation_absent
        and review.hard_error_absent
    )


def _assemble_replacements(
    original: str,
    replacements: Iterable[tuple[int, int, str]],
) -> str:
    assembled = original
    for start, end, candidate in sorted(replacements, reverse=True):
        assembled = assembled[:start] + candidate + assembled[end:]
    return assembled


def material_rewrite_metrics(
    original: str,
    candidate: str,
    *,
    protected_texts: Iterable[str] = (),
) -> tuple[float, float, int, int]:
    """Measure literal/structural change only on editable natural paragraphs."""
    original_paragraphs = _natural_paragraphs(original)
    candidate_paragraphs = _natural_paragraphs(candidate)
    protected = tuple(value.strip() for value in protected_texts if value.strip())
    if not candidate.strip():
        editable_count = sum(
            1
            for source in original_paragraphs
            if not _looks_like_heading(source.strip())
            and not any(value == source.strip() or value in source for value in protected)
        )
        return 1.0, 0.0, editable_count, editable_count
    editable_pairs: list[tuple[str, str]] = []
    for index, source in enumerate(original_paragraphs):
        if index >= len(candidate_paragraphs):
            break
        if _looks_like_heading(source.strip()):
            continue
        if any(value == source.strip() or value in source for value in protected):
            continue
        editable_pairs.append((source, candidate_paragraphs[index]))
    if not editable_pairs:
        return 1.0, 1.0, 0, 0
    source_joined = "\n".join(item[0] for item in editable_pairs)
    candidate_joined = "\n".join(item[1] for item in editable_pairs)
    similarity = SequenceMatcher(None, source_joined, candidate_joined, autojunk=False).ratio()
    per_paragraph = [
        SequenceMatcher(None, source, rewritten, autojunk=False).ratio()
        for source, rewritten in editable_pairs
    ]
    unchanged = sum(1 for value in per_paragraph if value >= 0.96)
    rewritten_ratio = 1.0 - (unchanged / len(editable_pairs))
    return similarity, rewritten_ratio, unchanged, len(editable_pairs)


def material_paragraph_coverage_requirement(editable_paragraphs: int) -> float:
    """Quantize the 80% target to the nearest achievable whole-paragraph count.

    A four-paragraph segment can only reach 0/25/50/75/100 percent.  Requiring
    a literal 80% accidentally means 100%, causing paid retries with no quality
    benefit.  Nearest-count quantization makes the target achievable while the
    independent 0.25-0.82 similarity and quality gates remain unchanged.
    """
    if editable_paragraphs <= 0:
        return 0.0
    required_changed = max(
        1,
        int(math.floor(MATERIAL_PARAGRAPH_COVERAGE_MIN * editable_paragraphs + 0.5)),
    )
    return required_changed / editable_paragraphs


def _natural_paragraphs(text: str) -> list[str]:
    return [value.strip() for value in re.split(r"\n\s*\n|\n", text) if value.strip()]


def _new_hard_errors(original: str, candidate: str) -> list[str]:
    errors: list[str] = []
    duplicated_function_words = re.compile(r"(从从|的的|了了|在在|和和|与与|但但|而而|是是|把把|将将|对对|由由|因因)")
    introduced = sorted(set(duplicated_function_words.findall(candidate)) - set(duplicated_function_words.findall(original)))
    if introduced:
        errors.append("新增明显叠字硬伤：" + "、".join(introduced))
    return errors


def prepare_rewrite_protocol(
    source: str,
    *,
    protected_texts: Iterable[str] = (),
    flexible_paragraphs: bool = False,
) -> RewriteProtocol:
    """Protect layout and immutable anchors before text reaches the rewrite model."""
    pieces = (
        (source,)
        if flexible_paragraphs
        else tuple(re.split(r"(\n[\t ]*(?:\n[\t ]*)*)", source))
    )
    block_piece_indexes: list[int] = []
    block_payloads: list[str] = []
    protected_values: list[tuple[str, str]] = []
    protected_block_indexes: list[tuple[str, int]] = []
    protected_heading_tokens: list[str] = []
    protected_counter = 0

    for piece_index in range(0, len(pieces), 2):
        value = pieces[piece_index]
        if not value.strip():
            continue
        spans = _protected_spans(value, protected_texts=protected_texts)
        heading_spans: list[tuple[int, int]] = []
        if _looks_like_heading(value.strip()):
            heading_spans = [(0, len(value))]
            spans = list(heading_spans)
        elif flexible_paragraphs:
            heading_spans = _heading_spans(value)
            spans.extend(heading_spans)
            spans = _merge_spans(spans)
        payload = value
        replacements: list[tuple[int, int, str, str]] = []
        current_block_index = len(block_payloads)
        for start, end in spans:
            protected_counter += 1
            token = f"[[KEEP_{protected_counter:04d}]]"
            replacements.append((start, end, token, value[start:end]))
            protected_block_indexes.append((token, current_block_index))
            if any(start <= heading_start and end >= heading_end for heading_start, heading_end in heading_spans):
                protected_heading_tokens.append(token)
        for start, end, token, original_value in reversed(replacements):
            payload = payload[:start] + token + payload[end:]
            protected_values.append((token, original_value))
        block_piece_indexes.append(piece_index)
        block_payloads.append(payload)

    if not block_payloads:
        block_piece_indexes = [0]
        block_payloads = [source]
        pieces = (source,)
    return RewriteProtocol(
        pieces=pieces,
        block_piece_indexes=tuple(block_piece_indexes),
        block_payloads=tuple(block_payloads),
        protected_values=tuple(protected_values),
        protected_block_indexes=tuple(protected_block_indexes),
        protected_heading_tokens=tuple(protected_heading_tokens),
        flexible_paragraphs=flexible_paragraphs,
    )


def parse_rewrite_candidate(
    raw_candidate: str,
    protocol: RewriteProtocol,
) -> tuple[str, list[str], bool]:
    """Restore original separators/anchors when the model follows the block protocol.

    Legacy/plain candidates are still accepted for deterministic review so tests and
    provider fallbacks remain compatible. They simply do not receive structural repair.
    """
    expected_count = len(protocol.block_payloads)
    marker_pattern = re.compile(
        r"\[\[P(\d{4})\]\]\s*(.*?)\s*\[\[/P\1\]\]",
        flags=re.DOTALL,
    )
    matches = list(marker_pattern.finditer(raw_candidate))
    if not matches:
        return raw_candidate, ["模型未使用编号块协议"], False

    issues: list[str] = []
    outside = marker_pattern.sub("", raw_candidate).strip()
    if outside:
        issues.append("编号块之外出现了额外说明")
    ids = [int(match.group(1)) for match in matches]
    expected_ids = list(range(1, expected_count + 1))
    if ids != expected_ids:
        issues.append(f"编号块顺序或数量错误：需要 {expected_ids}，实际 {ids}")
        return raw_candidate, issues, True

    candidate_pieces = list(protocol.pieces)
    rendered_blocks: list[str] = []
    for match in matches:
        value = match.group(2).strip()
        if not protocol.flexible_paragraphs:
            # A line wrap inside a fixed paragraph block is formatting noise.
            value = re.sub(r"[\t ]*\n+[\t ]*", " ", value)
        if not value:
            issues.append(f"编号块 P{int(match.group(1)):04d} 为空")
        rendered_blocks.append(value)

    # Models sometimes return the exact protected value instead of its KEEP token.
    # Recover that representation only inside the expected block. Missing or changed
    # values remain hard failures, and the deterministic anchor/order checks still run.
    protected_value_by_token = dict(protocol.protected_values)
    expected_block_by_token = dict(protocol.protected_block_indexes)
    for token, expected_block_index in protocol.protected_block_indexes:
        if any(token in value for value in rendered_blocks):
            continue
        protected_value = protected_value_by_token.get(token, "")
        if not protected_value or expected_block_index >= len(rendered_blocks):
            continue
        block = rendered_blocks[expected_block_index]
        if protected_value in block:
            rendered_blocks[expected_block_index] = block.replace(protected_value, token, 1)

    # The maximal strategy may merge paragraphs, but section titles are immutable
    # layout anchors. Restore their stand-alone line without changing their text.
    for token in protocol.protected_heading_tokens:
        expected_block_index = expected_block_by_token.get(token)
        if expected_block_index is None or expected_block_index >= len(rendered_blocks):
            continue
        block = rendered_blocks[expected_block_index]
        if token not in block:
            continue
        without_heading = block.replace(token, "", 1).strip()
        rendered_blocks[expected_block_index] = (
            f"{token}\n{without_heading}" if without_heading else token
        )

    for token, expected_block_index in protocol.protected_block_indexes:
        actual_blocks = [
            index for index, value in enumerate(rendered_blocks) if token in value
        ]
        if actual_blocks != [expected_block_index]:
            issues.append(
                f"受保护占位符 {token} 被移动到错误编号块"
            )
    combined_blocks = "\n".join(rendered_blocks)
    for token, protected_value in protocol.protected_values:
        count = combined_blocks.count(token)
        if count != 1:
            issues.append(f"受保护占位符 {token} 出现 {count} 次，应为 1 次")
    restored_blocks = []
    for value in rendered_blocks:
        for token, protected_value in protocol.protected_values:
            value = value.replace(token, protected_value)
        restored_blocks.append(value)
    for piece_index, value in zip(protocol.block_piece_indexes, restored_blocks, strict=True):
        candidate_pieces[piece_index] = value
    return "".join(candidate_pieces), issues, True


def _heading_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped and _looks_like_heading(stripped):
            start = offset + line.find(stripped)
            spans.append((start, start + len(stripped)))
        offset += len(line)
    return spans


def _merge_spans(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if start >= end:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def format_rewrite_retry_feedback(
    review: DeterministicReview,
    *,
    protocol_issues: Iterable[str] = (),
    hard_safety_only: bool = False,
) -> str:
    review_reasons = (
        [reason for reason in review.reasons if not _is_material_advisory_reason(reason)]
        if hard_safety_only
        else list(review.reasons)
    )
    details = [*protocol_issues, *review_reasons]
    if review.missing_anchors:
        details.append("缺失锚点：" + "、".join(review.missing_anchors[:10]))
    if review.extra_anchors:
        details.append("多余锚点：" + "、".join(review.extra_anchors[:10]))
    return "上一次候选被机器拒绝。只修复以下问题，不得改变事实：\n- " + "\n- ".join(
        dict.fromkeys(details)
    )


def _is_material_advisory_reason(reason: str) -> bool:
    return reason.startswith(("实质改写不足", "改写偏离过大", "实质改写覆盖率"))


def render_rewrite_prompt(
    *,
    segment_id: int,
    original_segment: str,
    previous_context: str,
    next_context: str,
    protected_originals: Iterable[str] = (),
    protected_map: str = "（无）",
    block_count: int = 1,
    retry_feedback: str = "首次改写：严格按编号块协议输出。",
    content_type: str = "article",
    strategy_id: str = "syntax_rebuild",
    strategy_rules: str | None = None,
    segment_signal: str = "未提供",
    style_diagnosis: str = "未命中高置信局部模式，按正文内容做编辑判断。",
    learned_evidence: str = "真实绿化配对样本不足，当前不注入未经验证的规律。",
    flexible_paragraphs: bool = False,
) -> str:
    template = _REWRITE_PROMPT_PATH.read_text(encoding="utf-8")
    resolved_strategy_rules = strategy_rules or rewrite_strategy_map().get(
        strategy_id,
        rewrite_strategy_map()["syntax_rebuild"],
    )
    return (
        template.replace("{{SEGMENT_ID}}", str(segment_id))
        .replace("{{STRATEGY_ID}}", strategy_id)
        .replace("{{STRATEGY_RULES}}", resolved_strategy_rules)
        .replace("{{SEGMENT_SIGNAL}}", segment_signal)
        .replace("{{STYLE_DIAGNOSIS}}", style_diagnosis)
        .replace("{{LEARNED_EVIDENCE}}", learned_evidence)
        .replace(
            "{{STRUCTURE_PROTOCOL}}",
            (
                "本轮只有一个正文块。允许在块内重新分段、合并过碎段落或拆开过长段落，"
                "但标题、事实顺序、锚点与整体篇幅必须保持；块内换行会被保留。"
                if flexible_paragraphs
                else "每个编号块对应原稿一个自然段，不得合并、拆分、调换或遗漏；块内不要换行。"
            ),
        )
        .replace("{{SIMILARITY_MIN}}", f"{MATERIAL_SIMILARITY_MIN:.2f}")
        .replace("{{SIMILARITY_MAX}}", f"{MATERIAL_SIMILARITY_MAX:.2f}")
        .replace("{{COVERAGE_MIN}}", f"{MATERIAL_PARAGRAPH_COVERAGE_MIN:.0%}")
        .replace("{{PROTECTED_ORIGINALS}}", "\n".join(protected_originals))
        .replace("{{PROTECTED_MAP}}", protected_map)
        .replace("{{BLOCK_COUNT}}", str(block_count))
        .replace("{{RETRY_FEEDBACK}}", retry_feedback)
        .replace("{{CONTENT_TYPE_RULES}}", _content_type_rules(content_type))
        .replace("{{PREVIOUS_CONTEXT}}", previous_context)
        .replace("{{ORIGINAL_SEGMENT}}", original_segment)
        .replace("{{NEXT_CONTEXT}}", next_context)
    )


def render_fact_ledger_prompt(
    source_with_tokens: str,
    *,
    protected_map: str,
    content_type: str,
) -> str:
    template = _FACT_LEDGER_PROMPT_PATH.read_text(encoding="utf-8")
    return (
        template.replace("{{CONTENT_TYPE_RULES}}", _content_type_rules(content_type))
        .replace("{{SOURCE_WITH_TOKENS}}", source_with_tokens)
        .replace("{{PROTECTED_MAP}}", protected_map)
    )


def parse_fact_ledger(text: str, *, required_tokens: Iterable[str] = ()) -> dict[str, Any]:
    cleaned = _clean_model_text(text)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("事实账本未返回 JSON")
    payload = json.loads(cleaned[start:end + 1])
    claims = payload.get("claims") if isinstance(payload, dict) else None
    if not isinstance(claims, list) or not claims:
        raise ValueError("事实账本缺少 claims")
    claim_ids: list[str] = []
    token_counts: Counter[str] = Counter()
    for index, claim in enumerate(claims, start=1):
        if not isinstance(claim, dict):
            raise ValueError(f"事实账本第 {index} 条 claim 无效")
        claim_id = str(claim.get("claim_id") or "").strip()
        summary = str(claim.get("summary") or "").strip()
        if not claim_id or not summary:
            raise ValueError(f"事实账本第 {index} 条缺少 claim_id 或 summary")
        source_sentence_ids = claim.get("source_sentence_ids")
        if (
            not isinstance(source_sentence_ids, list)
            or not source_sentence_ids
            or any(not str(value).strip() for value in source_sentence_ids)
        ):
            raise ValueError(f"事实账本第 {index} 条缺少 source_sentence_ids")
        if not isinstance(claim.get("qualifiers", []), list):
            raise ValueError(f"事实账本第 {index} 条 qualifiers 无效")
        if not isinstance(claim.get("negations", []), list):
            raise ValueError(f"事实账本第 {index} 条 negations 无效")
        claim_ids.append(claim_id)
        for token in claim.get("required_tokens") or []:
            token_counts[str(token)] += 1
    if len(set(claim_ids)) != len(claim_ids):
        raise ValueError("事实账本 claim_id 重复")
    missing = [token for token in required_tokens if token_counts[str(token)] != 1]
    if missing:
        raise ValueError("事实账本未逐一保留受保护占位符: " + "、".join(missing[:12]))
    chronology = payload.get("chronology") or []
    if chronology and any(str(value) not in set(claim_ids) for value in chronology):
        raise ValueError("事实账本 chronology 引用了不存在的 claim_id")
    return payload


def fact_ledger_copying_reasons(
    source_with_tokens: str,
    ledger: dict[str, Any],
) -> list[str]:
    """Reject a ledger that copies source prose instead of abstracting atomic claims."""
    source_sentences = [
        re.sub(r"\s+", "", value)
        for value in re.split(r"(?<=[。！？!?；;])|\n+", source_with_tokens)
        if value.strip()
    ]
    reasons: list[str] = []
    for claim in ledger.get("claims") or []:
        claim_id = str(claim.get("claim_id") or "?")
        summary = re.sub(r"\s+", "", str(claim.get("summary") or ""))
        if len(summary) < 24:
            continue
        for sentence in source_sentences:
            shorter = min(len(summary), len(sentence))
            if shorter < 24:
                continue
            similarity = SequenceMatcher(None, summary, sentence, autojunk=False).ratio()
            if summary in sentence or sentence in summary or similarity >= 0.93:
                reasons.append(f"事实账本 {claim_id} 复制了原句而非抽象事实")
                break
    return reasons


def deterministic_fact_ledger(source_with_tokens: str) -> dict[str, Any]:
    """Build a complete local sentence ledger without an API call.

    The source sentence is deliberately retained as the claim summary.  This
    ledger is evidence for the independent audit, not prose supplied as the
    candidate, so completeness and exact polarity are more important here than
    model-generated abstraction.
    """
    sentences = [
        value.strip()
        for value in re.split(r"(?<=[。！？!?；;])|\n+", source_with_tokens)
        if value.strip()
    ]
    claims: list[dict[str, Any]] = []
    for index, sentence in enumerate(sentences or [source_with_tokens.strip()], start=1):
        tokens = re.findall(r"\[\[KEEP_\d{4}\]\]", sentence)
        claims.append(
            {
                "claim_id": f"C{index:03d}",
                "source_sentence_ids": [f"S{index:03d}"],
                "summary": sentence,
                "subject": "",
                "predicate": "",
                "object": "",
                "time": "",
                "place": "",
                "cause_or_condition": "",
                "qualifiers": re.findall(
                    r"(?:仅|只|可能|或许|通常|一般|至少|至多|大约|几乎|并未|并不|未必|直到|不一定|往往)",
                    sentence,
                ),
                "negations": re.findall(
                    r"(?:不|未|没有|并非|不能|不会|无)[^，。！？；]{0,18}",
                    sentence,
                ),
                "negated": bool(re.search(r"不|未|无|没有|并非|不能|不会|未必", sentence)),
                "modality": "fact",
                "evaluation_owner": "",
                "required_tokens": tokens,
            }
        )
    return {
        "claims": claims,
        "chronology": [item["claim_id"] for item in claims],
        "causal_links": [],
        "paragraph_purposes": [],
    }


def fact_ledger_writer_view(
    fact_ledger: dict[str, Any],
    *,
    source_with_tokens: str,
) -> dict[str, Any]:
    """Remove duplicate source prose from the writer view while retaining every constraint.

    The full ledger remains unchanged for the independent auditor.  Repeating every
    source sentence verbatim in both ``original`` and ``fact_ledger`` anchored the
    rewrite model to the old syntax and produced synonym-only edits.
    """
    compact_claims: list[dict[str, Any]] = []
    normalized_source = re.sub(r"\s+", "", source_with_tokens)
    for claim in fact_ledger.get("claims") or []:
        value = {
            str(key): item
            for key, item in dict(claim).items()
            if item not in (None, "", [], {})
        }
        summary = str(value.get("summary") or "").strip()
        normalized_summary = re.sub(r"\s+", "", summary)
        if normalized_summary and normalized_summary in normalized_source:
            source_ids = ",".join(str(item) for item in value.get("source_sentence_ids") or [])
            value["summary"] = f"见{source_ids or '对应句'}"
        compact_claims.append(value)
    payload = {
        "claims": compact_claims,
        "chronology": list(fact_ledger.get("chronology") or []),
        "writer_view": "constraint_only_no_duplicate_source_prose",
    }
    if fact_ledger.get("causal_links"):
        payload["causal_links"] = list(fact_ledger["causal_links"])
    if fact_ledger.get("paragraph_purposes"):
        payload["paragraph_purposes"] = list(fact_ledger["paragraph_purposes"])
    return payload


def fact_ledger_audit_view(fact_ledger: dict[str, Any]) -> dict[str, Any]:
    """Compact empty ledger fields for the auditor without dropping any claim."""
    claims = [
        {
            str(key): item
            for key, item in dict(claim).items()
            if item not in (None, "", [], {})
        }
        for claim in (fact_ledger.get("claims") or [])
    ]
    payload: dict[str, Any] = {
        "claims": claims,
        "chronology": list(fact_ledger.get("chronology") or []),
        "audit_view": "all_claims_without_empty_fields",
    }
    if fact_ledger.get("causal_links"):
        payload["causal_links"] = list(fact_ledger["causal_links"])
    if fact_ledger.get("paragraph_purposes"):
        payload["paragraph_purposes"] = list(fact_ledger["paragraph_purposes"])
    return payload


def render_reauthor_prompt(
    fact_ledger: dict[str, Any],
    *,
    original_with_tokens: str,
    protected_map: str,
    previous_context: str,
    next_context: str,
    content_type: str,
    strategy_rules: str,
    source_chars: int,
    retry_feedback: str,
    controlled_topology: bool = False,
    maximal_recast: bool = False,
    preserve_paragraph_structure: bool = False,
) -> str:
    template = _REAUTHOR_PROMPT_PATH.read_text(encoding="utf-8")
    writer_ledger = fact_ledger_writer_view(
        fact_ledger,
        source_with_tokens=original_with_tokens,
    )
    if maximal_recast:
        primary_edit_rule = (
            "4. 本轮必须进行深层组合重构：同时改变主从句层级、主语落点、句子边界、同主题信息落点、"
            "自然段边界、长短句分布、标点停顿、连接词密度与段落功能；不得沿原句骨架做近义词替换。"
        )
        scope_execution_rules = (
            "本轮是最大幅度组合实验，不做单变量归因：\n"
            "1. 在不跨标题的前提下，重建当前编辑单元的叙述路径；相邻 claim 可以调整落点，但真实时间、因果、条件、否定、评价归属和事件先后不得改变。\n"
            "2. 同时改变主从句层级、主语落点、句子边界、段落边界、长短句分布、标点停顿、连接词密度和段落功能。不要让连续段落保持相同起笔、相同长度、相同推进方式或相同收束。\n"
            "3. 必须形成实质重构，而不是只替换连接词或近义词；准确的专名、术语、数字、日期、金额、引语和书名仍须原样保留。\n"
            "4. 允许正式书稿内部出现自然的疏密、急缓和句式变化，但不得加入错别字、残句、网络腔、伪造个人经历、心理、背景、解释、结论或口号。\n"
            "5. 若扩大变化会损害 claim，立即收回该处变化；事实完整和可读性仍由独立终审逐条验收。"
        )
    elif controlled_topology:
        primary_edit_rule = (
            "4. 本轮只重建每个自然段内部的主从句层级、修饰关系和句子边界；"
            "事实顺序、信息落点和作者语体必须保持不变。已经准确自然的专名、术语和关键判断原样保留。"
        )
        scope_execution_rules = (
            "本轮是逐步验证中的单变量实验，只允许改变‘句子拓扑’：\n"
            "1. 段落数量、段落顺序、段落边界、事实顺序、信息落点和作者语体必须保持不变；不得跨段移动任何 claim。\n"
            "2. 在每个自然段内部，先还原 claim 的事实、限定、否定、因果和评价归属，再重建主从句层级、修饰关系和句子边界。至少发生一处真正的句法层级变化，禁止沿原句骨架只换近义词。\n"
            "3. 词汇变化仅限新句法成立所必需的调整。不得主动口语化，不得改变段长分布，不得添加短促感叹、个人经历、解释、细节、心理、结论或拔高。\n"
            "4. 受保护锚点、引语、专名和术语原样保留。若句法重构与事实完整冲突，优先保住事实并让终审拒绝，不得擅自补写。\n"
            "5. 本轮真实复检回来之前，不叠加节奏、段落重组、高频词清理或风格轮换等其他变量。"
        )
    else:
        primary_edit_rule = (
            "4. 优先做最小必要编辑：调整主从句关系、拆合句子、改变信息落点、消除连续同构句；"
            "已经准确自然的专名、术语和关键判断可以原样保留。"
        )
        scope_execution_rules = (
            "按编辑策略对当前单元做质量安全编辑；不得为追求字面差异而损害事实、限定、作者声音或阅读体验。"
        )
    paragraph_rule = (
        "8. 本轮段落数量、段落顺序和段落边界必须完全不变；不得跨段移动任何 claim。"
        if preserve_paragraph_structure
        else "8. 可以在同一主题内自然拆分或合并段落，但不得跨标题、跨主题或改变事实顺序；不制造超过 500 字的难读自然段。"
    )
    return (
        template.replace("{{CONTENT_TYPE_RULES}}", _content_type_rules(content_type))
        .replace("{{STRATEGY_RULES}}", strategy_rules)
        .replace("{{PRIMARY_EDIT_RULE}}", primary_edit_rule)
        .replace("{{PARAGRAPH_RULE}}", paragraph_rule)
        .replace("{{SCOPE_EXECUTION_RULES}}", scope_execution_rules)
        .replace(
            "{{FACT_LEDGER}}",
            json.dumps(writer_ledger, ensure_ascii=False, separators=(",", ":")),
        )
        .replace("{{ORIGINAL_WITH_TOKENS}}", original_with_tokens)
        .replace("{{PROTECTED_MAP}}", protected_map)
        .replace("{{PREVIOUS_CONTEXT}}", previous_context)
        .replace("{{NEXT_CONTEXT}}", next_context)
        .replace("{{MIN_CHARS}}", str(max(1, int(source_chars * QUALITY_SAFE_LENGTH_MIN))))
        .replace("{{MAX_CHARS}}", str(max(1, int(source_chars * QUALITY_SAFE_LENGTH_MAX))))
        .replace("{{RETRY_FEEDBACK}}", retry_feedback)
    )


def render_audit_prompt(
    original: str,
    candidate: str,
    segment_ids: Iterable[int],
    *,
    content_type: str = "article",
    fact_ledgers: dict[str, Any] | None = None,
) -> str:
    template = _AUDIT_PROMPT_PATH.read_text(encoding="utf-8")
    compact_ledgers = {
        str(audit_id): fact_ledger_audit_view(ledger)
        for audit_id, ledger in (fact_ledgers or {}).items()
    }
    return (
        template.replace("{{SEGMENT_IDS}}", json.dumps(list(segment_ids), ensure_ascii=False))
        .replace("{{CONTENT_TYPE_RULES}}", _content_type_rules(content_type))
        .replace(
            "{{FACT_LEDGERS}}",
            json.dumps(compact_ledgers, ensure_ascii=False, separators=(",", ":")),
        )
        .replace("{{ORIGINAL_MANUSCRIPT}}", original)
        .replace("{{CANDIDATE_MANUSCRIPT}}", candidate)
    )


@lru_cache(maxsize=1)
def _content_type_rule_map() -> dict[str, str]:
    payload = json.loads(_CONTENT_RULES_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or "article" not in payload:
        raise ValueError("AIGC 报告审稿类型规则模板无效")
    return {str(key): str(value) for key, value in payload.items()}


def _content_type_rules(content_type: str) -> str:
    rules = _content_type_rule_map()
    return rules.get(content_type, rules["article"])


@lru_cache(maxsize=1)
def rewrite_strategy_map() -> dict[str, str]:
    payload = json.loads(_STRATEGY_RULES_PATH.read_text(encoding="utf-8"))
    required = {
        "syntax_rebuild",
        "evidence_first",
        "rhythm_recompose",
        "controlled_topology",
        "maximal_variance",
    }
    if not isinstance(payload, dict) or not required.issubset(payload):
        raise ValueError("AIGC 报告改写策略模板无效")
    return {str(key): str(value) for key, value in payload.items()}


@lru_cache(maxsize=1)
def diagnostic_rule_map() -> dict[str, str]:
    payload = json.loads(_DIAGNOSTIC_RULES_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not payload:
        raise ValueError("AIGC 报告局部诊断规则模板无效")
    return {str(key): str(value) for key, value in payload.items()}


def diagnose_segment_style(text: str) -> str:
    """Turn observable prose patterns into prompt evidence, never a fake detector score."""
    profile = profile_style(text)
    paragraphs = _natural_paragraphs(text)
    prose = [value for value in paragraphs if not _looks_like_heading(value)]
    endings = re.compile(r"(?:这(?:也|就)?(?:说明|意味着)|由此可见|可以看出|这并非|这不是|成为了?|证明了?|体现了?)[^。！？]{0,45}[。！？]?$")
    time_opening = re.compile(r"^(?:公元前?\d+年|\d{3,4}年|同年|次年|后来|随后|此后|不久|当时|早在)")
    denominator = max(1, len(prose))
    summary_ratio = sum(bool(endings.search(item)) for item in prose) / denominator
    time_opening_ratio = sum(bool(time_opening.search(item)) for item in prose) / denominator
    opening_counts = Counter(re.sub(r"^[“‘（(\s]+", "", item)[:2] for item in prose if len(item) >= 2)
    repeated_opening_ratio = (
        sum(count for count in opening_counts.values() if count >= 2) / denominator
    )
    triggered: list[str] = []
    if len(prose) >= 3 and profile["sentence_length_cv"] < 0.48:
        triggered.append("uniform_sentence_rhythm")
    if len(prose) >= 3 and profile["paragraph_length_cv"] < 0.42:
        triggered.append("uniform_paragraph_rhythm")
    if summary_ratio >= 0.28:
        triggered.append("summary_tail_density")
    if time_opening_ratio >= 0.35:
        triggered.append("chronology_scaffold")
    if repeated_opening_ratio >= 0.45:
        triggered.append("repeated_openings")
    if profile["discourse_markers_per_1000_chars"] >= 4.0:
        triggered.append("discourse_scaffold")
    rules = diagnostic_rule_map()
    metrics = (
        f"句长变异系数={profile['sentence_length_cv']:.3f}；"
        f"段长变异系数={profile['paragraph_length_cv']:.3f}；"
        f"总结式段尾占比={summary_ratio:.1%}；"
        f"时间词起段占比={time_opening_ratio:.1%}；"
        f"重复起笔占比={repeated_opening_ratio:.1%}。"
    )
    if not triggered:
        return metrics + " 未命中高置信机械骨架；不要为了求变化强行制造口语或跳跃。"
    return metrics + "\n" + "\n".join(f"- {rules[key]}" for key in triggered if key in rules)


def learned_evidence_for_prompt(*, content_type: str) -> str:
    summary = feature_learning_summary(content_type=content_type)
    if not summary:
        return "真实绿化配对样本不足，当前不注入未经验证的规律。"
    lines: list[str] = []
    for item in summary[:4]:
        correlation = float(item.get("pearson_correlation") or 0.0)
        direction = "适度增加" if correlation < 0 else "适度减少"
        lines.append(
            f"- {direction} {item['feature']} 与真实转绿同向；"
            f"样本 {item['sample_count']}，证据级别 {item['evidence_grade']}。"
        )
    return "这些只是本项目真实复检的相关方向，必须服从事实与文稿质量：\n" + "\n".join(lines)


def split_editorial_units(
    text: str,
    *,
    min_chars: int | None = None,
    max_chars: int | None = None,
) -> list[EditorialUnit]:
    """Split a report region into bounded editorial units without inventing detector labels."""
    leading_offset = len(text) - len(text.lstrip())
    source = text.strip()
    if not source:
        return []
    minimum = max(120, int(min_chars or settings.AIGC_REVIEW_EDITORIAL_UNIT_MIN_CHARS))
    maximum = max(minimum + 80, int(max_chars or settings.AIGC_REVIEW_EDITORIAL_UNIT_MAX_CHARS))
    if len(source) <= maximum:
        return [
            EditorialUnit(
                1,
                leading_offset,
                leading_offset + len(source),
                source,
            )
        ]

    paragraph_boundaries = {
        match.end() for match in re.finditer(r"\n[\t ]*(?:\n[\t ]*)+", source)
    }
    sentence_boundaries = {
        match.end() for match in re.finditer(r"[。！？!?；;](?:[”’\"']?)[\t ]*", source)
    }
    boundaries = sorted(paragraph_boundaries | sentence_boundaries)
    ranges: list[tuple[int, int]] = []
    start = 0
    while len(source) - start > maximum:
        lower = start + minimum
        upper = min(len(source), start + maximum)
        paragraph_candidates = [
            value for value in paragraph_boundaries if lower <= value <= upper
        ]
        sentence_candidates = [
            value for value in boundaries if lower <= value <= upper
        ]
        cut = max(paragraph_candidates or sentence_candidates or [upper])
        if cut <= start:
            cut = upper
        ranges.append((start, cut))
        start = cut
        while start < len(source) and source[start].isspace():
            start += 1
    if start < len(source):
        ranges.append((start, len(source)))

    if len(ranges) >= 2:
        last_start, last_end = ranges[-1]
        if last_end - last_start < minimum:
            previous_start, _ = ranges[-2]
            combined_length = last_end - previous_start
            if combined_length <= maximum:
                ranges[-2:] = [(previous_start, last_end)]
            else:
                lower = max(previous_start + minimum, last_end - maximum)
                upper = min(previous_start + maximum, last_end - minimum)
                candidates = [value for value in boundaries if lower <= value <= upper]
                target = previous_start + combined_length // 2
                cut = min(candidates, key=lambda value: abs(value - target)) if candidates else min(
                    previous_start + maximum,
                    max(previous_start + minimum, target),
                )
                ranges[-2:] = [(previous_start, cut), (cut, last_end)]

    units: list[EditorialUnit] = []
    for index, (start, end) in enumerate(ranges, start=1):
        value = source[start:end].strip()
        if value:
            units.append(
                EditorialUnit(
                    index,
                    leading_offset + start,
                    leading_offset + end,
                    value,
                )
            )
    return units or [
        EditorialUnit(1, leading_offset, leading_offset + len(source), source)
    ]


def substantive_rewrite_reasons(
    review: DeterministicReview,
    *,
    require_text_change: bool = True,
) -> list[str]:
    reasons: list[str] = []
    if review.length_ratio <= 0:
        return ["候选正文为空"]
    # v5 deliberately removes detector-driven similarity targets.  They forced
    # unnecessary lexical drift and damaged voice.  Syntax experiments only need
    # a real text change; the claim audit, not a similarity quota, protects quality.
    if (
        require_text_change
        and review.editable_paragraphs > 0
        and review.literal_similarity >= 0.995
        and review.rewritten_paragraph_ratio <= 0.0
    ):
        reasons.append("候选非空白正文没有发生可送检的句法变化")
    return reasons


def substantive_rewrite_passed(
    review: DeterministicReview,
    *,
    require_text_change: bool = True,
) -> bool:
    return not substantive_rewrite_reasons(
        review,
        require_text_change=require_text_change,
    )


def combine_quality_audits(audits: Iterable[dict[str, Any]]) -> dict[str, Any]:
    values = list(audits)
    if not values:
        return {}
    score_keys = (
        "semantic_fidelity",
        "information_completeness",
        "fluency",
        "voice_continuity",
    )
    boolean_keys = (
        "no_added_facts",
        "no_removed_information",
        "no_stance_change",
        "no_plot_or_event_change",
    )
    failed_ids: list[Any] = []
    issues: list[Any] = []
    claim_results: list[Any] = []
    added_claims: list[Any] = []
    reasons: list[str] = []
    for item in values:
        failed_ids.extend(item.get("failed_segment_ids") or [])
        issues.extend(item.get("issues") or [])
        claim_results.extend(item.get("claim_results") or [])
        added_claims.extend(item.get("added_claims") or [])
        reason = str(item.get("reason") or "").strip()
        if reason:
            reasons.append(reason)
    result: dict[str, Any] = {
        "pass": all(quality_audit_passed(item) for item in values),
        **{key: min(float(item.get(key, 0)) for item in values) for key in score_keys},
        **{key: all(item.get(key) is True for item in values) for key in boolean_keys},
        "failed_segment_ids": list(dict.fromkeys(failed_ids)),
        "issues": issues,
        "claim_results": claim_results,
        "added_claims": added_claims,
        "structural_rewrite_sufficient": all(
            item.get("structural_rewrite_sufficient") is True for item in values
        ),
        "reason": "；".join(dict.fromkeys(reasons)),
        "batch_count": len(values),
        "batches": values,
    }
    return result


def _safe_cache_namespace(value: str | None) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]", "", value or "shared")
    return normalized[:80] or "shared"


def _fact_ledger_cache_path(source_with_tokens: str, *, cache_namespace: str | None) -> Path:
    digest = hashlib.sha256(
        (PROMPT_VERSION + "\n" + source_with_tokens).encode("utf-8")
    ).hexdigest()
    return _FACT_LEDGER_CACHE_DIR / _safe_cache_namespace(cache_namespace) / f"{digest}.json"


def _load_cached_fact_ledger(
    source_with_tokens: str,
    *,
    cache_namespace: str | None,
    required_tokens: Iterable[str],
) -> dict[str, Any] | None:
    path = _fact_ledger_cache_path(source_with_tokens, cache_namespace=cache_namespace)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("prompt_version") != PROMPT_VERSION:
            return None
        return parse_fact_ledger(
            json.dumps(payload.get("ledger"), ensure_ascii=False),
            required_tokens=required_tokens,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _persist_fact_ledger(
    source_with_tokens: str,
    ledger: dict[str, Any],
    *,
    cache_namespace: str | None,
) -> None:
    path = _fact_ledger_cache_path(source_with_tokens, cache_namespace=cache_namespace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "prompt_version": PROMPT_VERSION,
                "source_sha256": sha256_text(source_with_tokens),
                "ledger": ledger,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def strategy_for_index(index: int, *, iteration: int = 1) -> str:
    strategy_ids = tuple(rewrite_strategy_map())
    return strategy_ids[(max(1, iteration) - 1 + index) % len(strategy_ids)]


def parse_quality_audit(text: str) -> dict[str, Any]:
    cleaned = _clean_model_text(text)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("质量审稿未返回 JSON")
    payload = json.loads(cleaned[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("质量审稿 JSON 不是对象")
    claim_results = payload.get("claim_results")
    added_claims = payload.get("added_claims")
    if not isinstance(claim_results, list) or not claim_results:
        raise ValueError("质量审稿缺少逐 claim 的 claim_results")
    if not isinstance(added_claims, list):
        raise ValueError("质量审稿缺少 added_claims")
    for index, item in enumerate(claim_results, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"质量审稿第 {index} 条 claim_result 无效")
        if str(item.get("status") or "") not in {"preserved", "altered", "missing"}:
            raise ValueError(f"质量审稿第 {index} 条 claim_result 状态无效")
    return payload


def quality_audit_claim_coverage_reasons(
    payload: dict[str, Any],
    fact_ledgers: dict[str, Any],
) -> list[str]:
    expected = {
        (str(audit_id), str(claim.get("claim_id") or ""))
        for audit_id, ledger in fact_ledgers.items()
        for claim in (ledger.get("claims") or [])
        if str(claim.get("claim_id") or "").strip()
    }
    actual = {
        (str(item.get("audit_id") or ""), str(item.get("claim_id") or ""))
        for item in (payload.get("claim_results") or [])
        if isinstance(item, dict)
    }
    reasons: list[str] = []
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        reasons.append(
            "缺少 claim 验收："
            + "、".join(f"{audit_id}:{claim_id}" for audit_id, claim_id in missing[:20])
        )
    if extra:
        reasons.append(
            "存在账本外 claim 验收："
            + "、".join(f"{audit_id}:{claim_id}" for audit_id, claim_id in extra[:20])
        )
    return reasons


def quality_audit_passed(payload: dict[str, Any] | None) -> bool:
    if not payload:
        return False
    score_rules = {
        "semantic_fidelity": 98,
        "information_completeness": 100,
        "fluency": 92,
        "voice_continuity": 92,
    }
    boolean_rules = (
        "no_added_facts",
        "no_removed_information",
        "no_stance_change",
        "no_plot_or_event_change",
    )
    issues = payload.get("issues")
    claim_results = payload.get("claim_results")
    added_claims = payload.get("added_claims")
    claims_preserved = (
        isinstance(claim_results, list)
        and bool(claim_results)
        and all(
            isinstance(item, dict) and item.get("status") == "preserved"
            for item in claim_results
        )
    )
    blocking_issue = isinstance(issues, list) and any(
        not isinstance(item, dict)
        or str(item.get("severity") or "").lower() in {"medium", "high"}
        or str(item.get("type") or "")
        in {"added_fact", "removed_information", "stance_change", "event_change"}
        for item in issues
    )
    return (
        bool(payload.get("pass"))
        and all(float(payload.get(key, 0)) >= threshold for key, threshold in score_rules.items())
        and all(payload.get(key) is True for key in boolean_rules)
        and payload.get("structural_rewrite_sufficient") is True
        and isinstance(issues, list)
        and not blocking_issue
        and claims_preserved
        and isinstance(added_claims, list)
        and not added_claims
    )


def model_strategy(*, user_id: str | None = None) -> dict[str, Any]:
    default_provider = "deepseek"
    return {
        "default_rewrite_provider": default_provider,
        "rewrite_model": _rewrite_model_for_provider(default_provider),
        "fact_model": LOCAL_FACT_LEDGER_MODEL,
        "rewrite_providers": _rewrite_provider_statuses(),
        "audit_model": settings.AIGC_REVIEW_AUDIT_MODEL,
        "rewrite_thinking": _rewrite_reasoning_for_provider(default_provider),
        "recommended_rewrite_reasoning": "low",
        "rewrite_reasoning_options": ["low", "medium", "high"],
        "candidate_count": 1,
        "max_rewrite_attempts_per_segment": int(settings.AIGC_REVIEW_MAX_REWRITE_ATTEMPTS),
        "rewrite_protocol": "fact_ledger_constrained_edit_v5",
        "experiment_mode": "paired_black_box_human_in_loop",
        "selection_mode": "contiguous_30_to_35_percent_then_stop",
        "experiment_order": ["layout_only", "syntax_constrained", "combined", "detector_probe"],
        "recommended_max_calibration_segments": 12,
        "strategies": list(rewrite_strategy_map()),
        "material_similarity_range": [MATERIAL_SIMILARITY_MIN, MATERIAL_SIMILARITY_MAX],
        "material_paragraph_coverage_min": MATERIAL_PARAGRAPH_COVERAGE_MIN,
        "material_coverage_quantization": "nearest_achievable_whole_paragraph_count",
        "legacy_material_metrics_are_advisory": True,
        "similarity_is_diagnostic_only": True,
        "editorial_unit_chars": [
            int(settings.AIGC_REVIEW_EDITORIAL_UNIT_MIN_CHARS),
            int(settings.AIGC_REVIEW_EDITORIAL_UNIT_MAX_CHARS),
        ],
        "macro_block_chars": [
            int(settings.AIGC_REVIEW_MACRO_BLOCK_MIN_CHARS),
            int(settings.AIGC_REVIEW_MACRO_BLOCK_MAX_CHARS),
        ],
        "audit_batch_units": int(settings.AIGC_REVIEW_AUDIT_BATCH_UNITS),
        "audit_max_output_tokens": int(settings.AIGC_REVIEW_AUDIT_MAX_OUTPUT_TOKENS),
        "default_run_budget_cny": float(settings.AIGC_REVIEW_RUN_BUDGET_CNY),
        "default_calibration_budget_cny": float(settings.AIGC_REVIEW_CALIBRATION_BUDGET_CNY),
        "style_feature_version": STYLE_FEATURE_VERSION,
        "feature_learning_min_real_samples": 5,
        "detector_efficacy": calibration_evidence_status(user_id=user_id),
        "audit_scope": "bounded_editorial_units_issue_first",
        "proportional_report_learning": False,
        "ocr": "local_only_zero_api_cost",
        "human_pass_rule": ">= 25% official overview",
        "safety_target_percent": float(settings.AIGC_REVIEW_HUMAN_TARGET),
        "global_gate_changed": False,
        "novel_workflows_included": False,
    }


def _fact_model_label(
    backends: Iterable[str],
    *,
    content_type: str = "article",
) -> str:
    if content_type == "classical":
        return "not_required"
    modes = set(backends)
    if "model" in modes and ("local" in modes or "local_fallback" in modes):
        return f"{settings.AIGC_REVIEW_AUDIT_MODEL}+{LOCAL_FACT_LEDGER_MODEL}"
    if "model" in modes:
        return settings.AIGC_REVIEW_AUDIT_MODEL
    return LOCAL_FACT_LEDGER_MODEL


def _normalize_rewrite_provider(provider: str | None) -> str:
    selected = (provider or settings.AIGC_REVIEW_REWRITE_PROVIDER or "deepseek").strip().lower()
    if selected not in {"grok", "deepseek"}:
        raise ZhuqueReportError(f"不支持的审稿改写模型提供方: {selected}")
    return selected


def _rewrite_model_for_provider(provider: str) -> str:
    if provider == "grok":
        return (settings.AIGC_REVIEW_GROK_MODEL or "grok-4.5").strip()
    return (settings.AIGC_REVIEW_REWRITE_MODEL or "deepseek-v4-pro").strip()


def _rewrite_reasoning_for_provider(provider: str) -> str:
    if provider == "grok":
        return (settings.AIGC_REVIEW_GROK_REASONING_EFFORT or "low").strip().lower()
    return "disabled"


def _normalize_grok_reasoning(value: str | None) -> str:
    effort = (value or "low").strip().lower()
    if effort not in {"low", "medium", "high"}:
        raise ZhuqueReportError("Grok 推理档位必须是 low、medium 或 high。")
    return effort


def _rewrite_provider_configured(provider: str) -> bool:
    if provider == "grok":
        return bool(settings.XAI_API_KEY.strip())
    return bool(settings.DEEPSEEK_API_KEY.strip())


def _ensure_rewrite_provider_configured(provider: str) -> None:
    if _rewrite_provider_configured(provider):
        return
    if provider == "grok":
        raise AigcReviewModelError(
            "Grok 4.5 尚未配置：请在服务器 .env 安全写入 XAI_API_KEY 后重启服务；不要把密钥粘贴到聊天或日志。"
        )
    raise AigcReviewModelError("DeepSeek 改写模型尚未配置 DEEPSEEK_API_KEY。")


def _rewrite_callable(provider: str) -> Callable[..., Any]:
    return _grok_rewrite if provider == "grok" else _deepseek_rewrite


def _rewrite_provider_statuses() -> list[dict[str, Any]]:
    default_provider = "deepseek"
    return [
        {
            "id": "grok",
            "label": "Grok 4.5",
            "model": _rewrite_model_for_provider("grok"),
            "configured": _rewrite_provider_configured("grok"),
            "default": default_provider == "grok",
            "automatic": False,
            "reasoning": _rewrite_reasoning_for_provider("grok"),
            "input_usd_per_m": float(settings.AIGC_REVIEW_GROK_INPUT_USD_PER_M),
            "cached_input_usd_per_m": float(
                settings.AIGC_REVIEW_GROK_CACHED_INPUT_USD_PER_M
            ),
            "output_usd_per_m": float(settings.AIGC_REVIEW_GROK_OUTPUT_USD_PER_M),
        },
        {
            "id": "deepseek",
            "label": "DeepSeek V4 Pro",
            "model": _rewrite_model_for_provider("deepseek"),
            "configured": _rewrite_provider_configured("deepseek"),
            "default": default_provider == "deepseek",
            "automatic": True,
            "reasoning": "disabled",
        },
    ]


@lru_cache(maxsize=1)
def _get_xai_client() -> OpenAI:
    _ensure_rewrite_provider_configured("grok")
    timeout = max(30, int(settings.XAI_REQUEST_TIMEOUT))
    proxy = _xai_proxy_url()
    http_client = httpx.Client(
        proxy=proxy or None,
        timeout=httpx.Timeout(float(timeout)),
        trust_env=False,
    )
    return OpenAI(
        api_key=settings.XAI_API_KEY,
        base_url=settings.XAI_BASE_URL.rstrip("/"),
        timeout=timeout,
        max_retries=max(0, int(settings.XAI_MAX_RETRIES)),
        http_client=http_client,
    )


def _xai_proxy_url() -> str:
    """Use an explicit xAI proxy, then the already-provisioned official API egress."""
    return (settings.XAI_HTTPS_PROXY or settings.OPENAI_HTTPS_PROXY or "").strip()


def _grok_rewrite(
    prompt: str,
    *,
    segment: ZhuqueSegment,
    request_id: str,
    attempt: int = 1,
    reasoning_effort: str | None = None,
) -> tuple[str, dict[str, Any]]:
    model = _rewrite_model_for_provider("grok")
    reasoning_effort = _normalize_grok_reasoning(
        reasoning_effort or _rewrite_reasoning_for_provider("grok")
    )
    max_tokens = min(
        max(2000, int(settings.AIGC_REVIEW_GROK_MAX_OUTPUT_TOKENS)),
        max(6000, int(len(segment.mapped_text) * 2.2)),
    )
    logger.info(
        "LLM action=aigc_report_rewrite request_id=%s provider=xai model=%s reasoning=%s segment=%s attempt=%s status=started",
        request_id,
        model,
        reasoning_effort,
        segment.segment_id,
        attempt,
    )
    with distributed_semaphore(
        "aigc-report-review-grok",
        limit=max(1, int(settings.AIGC_GATE_REWRITE_CONCURRENCY)),
    ):
        response = _get_xai_client().responses.create(
            model=model,
            input=[{"role": "user", "content": prompt}],
            reasoning={"effort": reasoning_effort},
            max_output_tokens=max_tokens,
            prompt_cache_key=f"aigc-review:{PROMPT_VERSION}:{request_id}",
            store=False,
        )
    if getattr(response, "status", None) == "incomplete":
        details = getattr(response, "incomplete_details", None)
        reason = getattr(details, "reason", "unknown")
        raise ValueError(f"Grok 4.5 输出未完成: {reason}")
    content = (getattr(response, "output_text", "") or "").strip()
    if not content:
        raise ValueError("Grok 4.5 返回空候选")
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    input_details = getattr(usage, "input_tokens_details", None)
    cached_input_tokens = int(getattr(input_details, "cached_tokens", 0) or 0)
    output_details = getattr(usage, "output_tokens_details", None)
    reasoning_tokens = int(getattr(output_details, "reasoning_tokens", 0) or 0)
    cost = _grok_cost_cny(input_tokens, cached_input_tokens, output_tokens)
    logger.info(
        "LLM action=aigc_report_rewrite request_id=%s provider=xai model=%s reasoning=%s segment=%s attempt=%s status=succeeded input_tokens=%s cached_input_tokens=%s output_tokens=%s reasoning_tokens=%s cost_cny=%.6f",
        request_id,
        model,
        reasoning_effort,
        segment.segment_id,
        attempt,
        input_tokens,
        cached_input_tokens,
        output_tokens,
        reasoning_tokens,
        cost,
    )
    return content, {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "cost_cny": cost,
    }


def _grok_cost_cny(
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
) -> float:
    cached = max(0, min(int(input_tokens), int(cached_input_tokens)))
    uncached = max(0, int(input_tokens) - cached)
    usd = (
        uncached * float(settings.AIGC_REVIEW_GROK_INPUT_USD_PER_M)
        + cached * float(settings.AIGC_REVIEW_GROK_CACHED_INPUT_USD_PER_M)
        + max(0, int(output_tokens)) * float(settings.AIGC_REVIEW_GROK_OUTPUT_USD_PER_M)
    ) / 1_000_000
    return usd * float(settings.AIGC_REVIEW_USD_CNY_RATE)


def _deepseek_rewrite(
    prompt: str,
    *,
    segment: ZhuqueSegment,
    request_id: str,
    attempt: int = 1,
    temperature: float | None = None,
) -> tuple[str, dict[str, Any]]:
    model = settings.AIGC_REVIEW_REWRITE_MODEL
    max_tokens = min(12000, max(1600, int(len(segment.mapped_text) * 0.9)))
    effective_temperature = 0.85 if temperature is None else float(temperature)
    logger.info(
        "LLM action=aigc_report_rewrite request_id=%s model=%s segment=%s attempt=%s temperature=%.2f status=started",
        request_id,
        model,
        segment.segment_id,
        attempt,
        effective_temperature,
    )
    with distributed_semaphore(
        "aigc-report-review",
        limit=max(1, int(settings.AIGC_GATE_REWRITE_CONCURRENCY)),
    ):
        response = get_deepseek_client().chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=effective_temperature,
            max_tokens=max_tokens,
            extra_body={"thinking": {"type": "disabled"}},
        )
    content = (response.choices[0].message.content or "").strip()
    if not content:
        raise ValueError("DeepSeek 返回空候选")
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    cost = (input_tokens * 3.0 + output_tokens * 6.0) / 1_000_000
    logger.info(
        "LLM action=aigc_report_rewrite request_id=%s model=%s segment=%s attempt=%s temperature=%.2f status=succeeded input_tokens=%s output_tokens=%s cost_cny=%.6f",
        request_id,
        model,
        segment.segment_id,
        attempt,
        effective_temperature,
        input_tokens,
        output_tokens,
        cost,
    )
    return content, {"input_tokens": input_tokens, "output_tokens": output_tokens, "cost_cny": cost}


def _gemini_fact_ledger(
    prompt: str,
    *,
    segment: ZhuqueSegment,
    request_id: str,
) -> tuple[str, dict[str, Any]]:
    model = settings.AIGC_REVIEW_AUDIT_MODEL
    logger.info(
        "LLM action=aigc_report_fact_ledger request_id=%s model=%s segment=%s status=started",
        request_id,
        model,
        segment.segment_id,
    )
    content = generate_gemini_text(
        prompt,
        model=model,
        temperature=0.0,
        max_output_tokens=2200,
        retries=1,
    )
    input_tokens = _estimate_tokens(prompt)
    output_tokens = _estimate_tokens(content)
    cost = _gemini_flash_lite_cost_cny(input_tokens, output_tokens)
    logger.info(
        "LLM action=aigc_report_fact_ledger request_id=%s model=%s segment=%s status=succeeded estimated_input_tokens=%s estimated_output_tokens=%s estimated_cost_cny=%.6f",
        request_id,
        model,
        segment.segment_id,
        input_tokens,
        output_tokens,
        cost,
    )
    return content, {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_cny": cost,
        "estimated": True,
    }


def _gemini_audit(prompt: str, *, request_id: str) -> tuple[str, dict[str, Any]]:
    model = settings.AIGC_REVIEW_AUDIT_MODEL
    # One editorial unit normally has a small ledger, but claim counts vary
    # widely.  Allocate enough response room for one compact result per claim
    # while retaining a firm deployment cap.
    claim_count = max(1, prompt.count('"claim_id"') - 1)
    configured_cap = max(2_400, int(settings.AIGC_REVIEW_AUDIT_MAX_OUTPUT_TOKENS))
    max_output_tokens = min(configured_cap, max(2_400, 700 + claim_count * 110))
    logger.info(
        "LLM action=aigc_report_quality_audit request_id=%s model=%s status=started claim_count=%s max_output_tokens=%s",
        request_id,
        model,
        claim_count,
        max_output_tokens,
    )
    content = generate_gemini_text(
        prompt,
        model=model,
        temperature=0.0,
        max_output_tokens=max_output_tokens,
        retries=1,
    )
    input_tokens = _estimate_tokens(prompt)
    output_tokens = _estimate_tokens(content)
    cost = _gemini_flash_lite_cost_cny(input_tokens, output_tokens)
    logger.info(
        "LLM action=aigc_report_quality_audit request_id=%s model=%s status=succeeded estimated_input_tokens=%s estimated_output_tokens=%s estimated_cost_cny=%.6f",
        request_id,
        model,
        input_tokens,
        output_tokens,
        cost,
    )
    return content, {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_cny": cost,
        "estimated": True,
    }


def _manual_review_result(
    *,
    request_id: str,
    original: str,
    selected_ids: list[int],
    segment_results: list[dict[str, Any]],
    reason: str,
    total_input_tokens: int,
    total_output_tokens: int,
    rewrite_cost_cny: float,
    rewrite_provider: str,
    rewrite_model: str,
    rewrite_reasoning: str,
    fact_model: str = LOCAL_FACT_LEDGER_MODEL,
    guarded_candidate_text: str = "",
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "status": "manual_review",
        "deliverable": False,
        "next_action": reason,
        "original_sha256": sha256_text(original),
        "candidate_sha256": None,
        "original_text": original,
        "candidate_text": "",
        "guarded_candidate_text": guarded_candidate_text,
        "selected_segment_ids": selected_ids,
        "segment_results": segment_results,
        "quality_audit": None,
        "cost": {
            "currency": "CNY",
            "estimated": True,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "estimated_cny": round(rewrite_cost_cny, 6),
        },
        "models": {
            "rewrite_provider": rewrite_provider,
            "rewrite": rewrite_model,
            "fact": fact_model,
            "audit": settings.AIGC_REVIEW_AUDIT_MODEL,
            "rewrite_thinking": rewrite_reasoning,
        },
        "prompt_version": PROMPT_VERSION,
    }


def _anchors(text: str) -> Counter[str]:
    patterns = (
        r"https?://[^\s)）\]】>]+",
        r"《[^《》\n]{1,120}》",
        r"“[^“”\n]{1,180}”",
        r"「[^「」\n]{1,180}」",
        r"『[^『』\n]{1,180}』",
        r"\d{1,4}年\d{1,2}月(?:\d{1,2}日)?",
        r"\d+(?:\.\d+)?(?:%|％|元|万元|亿元|美元|岁|人|次|条|章|页|字|公里|千米|米|小时|分钟|秒)?",
    )
    values: list[str] = []
    for pattern in patterns:
        values.extend(re.findall(pattern, text, flags=re.IGNORECASE))
    return Counter(values)


def _anchor_sequence(text: str) -> tuple[str, ...]:
    return tuple(text[start:end] for start, end in _protected_spans(text))


def _protected_spans(
    text: str,
    *,
    protected_texts: Iterable[str] = (),
) -> list[tuple[int, int]]:
    candidates: list[tuple[int, int, int]] = []
    for protected in protected_texts:
        if not protected:
            continue
        cursor = 0
        while True:
            index = text.find(protected, cursor)
            if index < 0:
                break
            candidates.append((index, index + len(protected), 0))
            cursor = index + len(protected)
    patterns = (
        r"https?://[^\s)）\]】>]+",
        r"《[^《》\n]{1,120}》",
        r"“[^“”\n]{1,180}”",
        r"「[^「」\n]{1,180}」",
        r"『[^『』\n]{1,180}』",
        r"\d{1,4}年\d{1,2}月(?:\d{1,2}日)?",
        r"\d+(?:\.\d+)?(?:%|％|元|万元|亿元|美元|岁|人|次|条|章|页|字|公里|千米|米|小时|分钟|秒)?",
    )
    for priority, pattern in enumerate(patterns, start=1):
        candidates.extend(
            (match.start(), match.end(), priority)
            for match in re.finditer(pattern, text, flags=re.IGNORECASE)
        )
    selected: list[tuple[int, int]] = []
    for start, end, _priority in sorted(candidates, key=lambda item: (item[0], item[2], -(item[1] - item[0]))):
        if any(start < kept_end and end > kept_start for kept_start, kept_end in selected):
            continue
        selected.append((start, end))
    return sorted(selected)


def _paragraph_count(text: str) -> int:
    return len([part for part in re.split(r"\n\s*\n|\n", text) if part.strip()])


def _classical_protected_fragments(text: str, start: int, end: int) -> tuple[str, ...]:
    marker = re.compile(r"^(?:【P\d{4}】\s*)?[（(]\s*原文\s*[）)]")
    fragments: list[str] = []
    cursor = 0
    for line in text.splitlines(keepends=True):
        line_start = cursor
        line_end = cursor + len(line)
        cursor = line_end
        if not marker.match(line.strip()):
            continue
        overlap_start = max(start, line_start)
        overlap_end = min(end, line_end)
        if overlap_end > overlap_start:
            value = text[overlap_start:overlap_end].rstrip("\r\n")
            if value:
                fragments.append(value)
    return tuple(fragments)


def _headings(text: str) -> tuple[str, ...]:
    values: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if _looks_like_heading(stripped):
            values.append(stripped)
    return tuple(values)


def _looks_like_heading(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    if re.match(r"^#{1,6}\s+", stripped) or re.match(
        r"^第[一二三四五六七八九十百千万\d]+[章节篇部卷]",
        stripped,
    ):
        return True
    if len(stripped) > 32 or re.search(r"[。！？；，,:：]", stripped):
        return False
    # Stand-alone short lines in article manuscripts are usually section titles.
    return len(re.findall(r"[\u3400-\u9fffA-Za-z]", stripped)) >= 2


def _contains_model_explanation(text: str) -> bool:
    value = (text or "").strip()
    patterns = (
        r"^(?:以下(?:是|为)|下面(?:是|为))[^\n]{0,24}(?:改写|润色|修改)(?:后|的|如下)",
        r"^(?:改写|润色|修改)(?:后|结果|版本)?(?:如下|：|:)",
        r"^(?:作为|身为)(?:一个)?AI",
        r"(?:^|\n)(?:修改说明|改写说明|审稿说明)(?:：|:)",
    )
    return any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in patterns)


def _normalize_ocr_text(text: str) -> str:
    value = (text or "").replace("％", "%").replace("ＡＩ", "AI")
    value = re.sub(r"\bA[lI1]\b", "AI", value, flags=re.IGNORECASE)
    value = value.replace("片断", "片段")
    return value


def _first_percent(text: str, patterns: Iterable[str]) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def _human_share_from_segments(segments: Iterable[ZhuqueSegment]) -> float:
    return sum(item.share_percent for item in segments if item.label == "human")


def _coerce_model_result(raw: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(raw, tuple) and len(raw) == 2 and isinstance(raw[1], dict):
        return str(raw[0]), dict(raw[1])
    return str(raw), {}


def _clean_model_text(text: str) -> str:
    value = (text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json|markdown|md|text)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    return value.strip()


def _estimate_tokens(text: str) -> int:
    chinese = len(re.findall(r"[\u3400-\u9fff]", text or ""))
    other = max(0, len(text or "") - chinese)
    return max(1, round(chinese * 0.6 + other * 0.25))


def _gemini_flash_lite_cost_cny(input_tokens: int, output_tokens: int) -> float:
    # Official standard pricing: USD $0.25 input / $1.50 output per 1M tokens.
    # Use a conservative fixed 7.2 CNY/USD estimate for the UI estimate only.
    return ((input_tokens * 0.25 + output_tokens * 1.5) / 1_000_000) * 7.2


def sha256_text(text: str) -> str:
    return hashlib.sha256(normalize_manuscript_text(text).encode("utf-8")).hexdigest()


def normalize_manuscript_text(text: str | None) -> str:
    """Canonicalize browser multipart/JSON line endings before hashing or mapping."""
    value = unicodedata.normalize("NFC", text or "")
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
