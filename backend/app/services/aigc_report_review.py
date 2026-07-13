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
import re
import threading
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import uuid4

import pdfplumber

from app.core.config import PROJECT_ROOT, settings
from app.services.deepseek import get_deepseek_client
from app.services.gemini import generate_text as generate_gemini_text
from app.utils.distributed_limiter import distributed_semaphore

logger = logging.getLogger(__name__)

PROMPT_VERSION = "zhuque-report-review-v2"
STRICT_HUMAN_THRESHOLD = 50.0
_PROMPT_DIR = PROJECT_ROOT / "backend" / "app" / "prompts"
_REWRITE_PROMPT_PATH = _PROMPT_DIR / "aigc_report_rewrite.md"
_AUDIT_PROMPT_PATH = _PROMPT_DIR / "aigc_report_quality_audit.md"
_CONTENT_RULES_PATH = _PROMPT_DIR / "aigc_report_content_rules.json"
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

    target = float(target_human_percent or settings.AIGC_REVIEW_HUMAN_TARGET)
    target = min(90.0, max(50.01, target))
    mapped = map_segments_to_original(raw_rows, original)
    table_share_total = sum(item.share_percent for item in mapped)
    table_is_complete = table_share_total >= 98.0
    derived_human = _human_share_from_segments(mapped)
    derived_suspected = sum(item.share_percent for item in mapped if item.label == "suspected")
    derived_ai = sum(item.share_percent for item in mapped if item.label == "ai")
    human_percent = (
        derived_human
        if table_is_complete
        else float(overview.get("human_percent") if overview.get("human_percent") is not None else derived_human)
    )
    suspected_percent = (
        derived_suspected
        if table_is_complete
        else float(
            overview.get("suspected_percent")
            if overview.get("suspected_percent") is not None
            else derived_suspected
        )
    )
    ai_percent = (
        derived_ai
        if table_is_complete
        else float(overview.get("ai_percent") if overview.get("ai_percent") is not None else derived_ai)
    )
    selected_ids, projected = recommend_target_segments(
        mapped,
        human_percent=human_percent,
        target_human_percent=target,
    )
    selected_set = set(selected_ids)
    segments = [
        ZhuqueSegment(**{**segment.to_dict(), "recommended": segment.segment_id in selected_set})
        for segment in mapped
    ]

    warnings: list[str] = []
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
        "policy_version": "report-driven-v1",
        "prompt_version": PROMPT_VERSION,
        "filename": filename,
        "report_sha256": _sha256_bytes(pdf_bytes),
        "original_sha256": sha256_text(original),
        "extraction_method": extraction_method,
        "page_count": page_count,
        "human_percent": round(human_percent, 2),
        "suspected_percent": round(suspected_percent, 2),
        "ai_percent": round(ai_percent, 2),
        "strict_pass": human_percent > STRICT_HUMAN_THRESHOLD,
        "source_match_verified": source_match_verified,
        "source_match_confidence": round(source_match_confidence, 4),
        "source_match_sample_chars": source_match_sample_chars,
        "strict_threshold": STRICT_HUMAN_THRESHOLD,
        "target_human_percent": target,
        "projected_human_percent": round(projected, 2),
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
    raw_boundaries = [0]
    for item in ordered[:-1]:
        cumulative += int(item["char_count"])
        unit_index = min(actual_total - 1, max(0, round(cumulative / expected_total * actual_total)))
        raw_boundaries.append(positions[unit_index])
    raw_boundaries.append(len(original_text))

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


def optimize_report_segments(
    original_text: str,
    segments: Iterable[ZhuqueSegment | dict[str, Any]],
    selected_segment_ids: Iterable[int],
    *,
    content_type: str = "article",
    rewrite_fn: Callable[..., Any] | None = None,
    audit_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Create one low-cost candidate and keep it gated behind a quality audit."""
    request_id = str(uuid4())
    original = normalize_manuscript_text(original_text)
    normalized_segments = [
        item if isinstance(item, ZhuqueSegment) else ZhuqueSegment(**item) for item in segments
    ]
    by_id = {item.segment_id: item for item in normalized_segments}
    selected_ids = list(dict.fromkeys(int(value) for value in selected_segment_ids))
    if not selected_ids:
        raise ZhuqueReportError("至少选择一个朱雀风险片段。")
    missing = [value for value in selected_ids if value not in by_id]
    if missing:
        raise ZhuqueReportError(f"报告中不存在片段: {missing}")
    if not original:
        raise ZhuqueReportError("原稿不能为空。")

    rewrite = rewrite_fn or _deepseek_rewrite
    audit = audit_fn or _gemini_audit
    replacements: list[tuple[int, int, str]] = []
    segment_results: list[dict[str, Any]] = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_rewrite_cost = 0.0

    for segment_id in selected_ids:
        segment = by_id[segment_id]
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
        protocol = prepare_rewrite_protocol(source, protected_texts=protected_originals)
        max_attempts = max(1, min(2, int(settings.AIGC_REVIEW_MAX_REWRITE_ATTEMPTS)))
        candidate = ""
        deterministic = review_deterministic_constraints(
            source,
            candidate,
            protected_texts=protected_originals,
        )
        candidate_passed = False
        attempt_results: list[dict[str, Any]] = []
        retry_feedback = "首次改写：严格按编号块协议输出。"
        for attempt in range(1, max_attempts + 1):
            prompt = render_rewrite_prompt(
                segment_id=segment_id,
                original_segment=protocol.prompt_segment,
                previous_context=original[max(0, segment.start - 600):segment.start],
                next_context=original[segment.end:min(len(original), segment.end + 600)],
                protected_originals=protected_originals,
                protected_map=protocol.protected_map,
                block_count=len(protocol.block_payloads),
                retry_feedback=retry_feedback,
                content_type=content_type,
            )
            try:
                raw = rewrite(
                    prompt,
                    segment=segment,
                    request_id=request_id,
                    attempt=attempt,
                )
                raw_candidate, usage = _coerce_model_result(raw)
            except Exception as exc:
                logger.exception(
                    "LLM action=aigc_report_rewrite request_id=%s segment=%s attempt=%s model=%s status=failed error_type=%s",
                    request_id,
                    segment_id,
                    attempt,
                    settings.AIGC_REVIEW_REWRITE_MODEL,
                    type(exc).__name__,
                )
                raise AigcReviewModelError(f"片段 {segment_id} 改写模型调用失败: {exc}") from exc

            cleaned_candidate = _clean_model_text(raw_candidate)
            candidate, protocol_issues, structured = parse_rewrite_candidate(
                cleaned_candidate,
                protocol,
            )
            deterministic = review_deterministic_constraints(
                source,
                candidate,
                protected_texts=protected_originals,
            )
            protocol_passed = not protocol_issues or not structured
            candidate_passed = deterministic.passed and protocol_passed
            input_tokens = int(usage.get("input_tokens") or _estimate_tokens(prompt))
            output_tokens = int(usage.get("output_tokens") or _estimate_tokens(cleaned_candidate))
            attempt_cost = float(usage.get("cost_cny") or 0.0)
            total_input_tokens += input_tokens
            total_output_tokens += output_tokens
            total_rewrite_cost += attempt_cost
            attempt_results.append(
                {
                    "attempt": attempt,
                    "passed": candidate_passed,
                    "structured_output": structured,
                    "protocol_issues": protocol_issues,
                    "deterministic_review": deterministic.to_dict(),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_cny": round(attempt_cost, 6),
                }
            )
            if candidate_passed:
                break
            retry_feedback = format_rewrite_retry_feedback(
                deterministic,
                protocol_issues=protocol_issues,
            )
            logger.warning(
                "LLM action=aigc_report_rewrite request_id=%s segment=%s attempt=%s status=rejected reasons=%s protocol=%s",
                request_id,
                segment_id,
                attempt,
                ";".join(deterministic.reasons),
                ";".join(protocol_issues),
            )

        segment_results.append(
            {
                "segment_id": segment_id,
                "status": "ready" if candidate_passed else "rejected",
                "original_chars": len(source),
                "candidate_chars": len(candidate),
                "rewrite_attempts": len(attempt_results),
                "attempts": attempt_results,
                "deterministic_review": deterministic.to_dict(),
                "candidate_preview": candidate[:500] if candidate_passed else "",
                "rejected_candidate_preview": candidate[:500] if not candidate_passed else "",
            }
        )
        if not candidate_passed:
            return _manual_review_result(
                request_id=request_id,
                original=original,
                selected_ids=selected_ids,
                segment_results=segment_results,
                reason=f"片段 {segment_id} 未通过确定性保护，已停止后续付费调用并保留原稿。",
                total_input_tokens=total_input_tokens,
                total_output_tokens=total_output_tokens,
                rewrite_cost_cny=total_rewrite_cost,
            )
        replacements.append((segment.start, segment.end, candidate))

    assembled = original
    for start, end, candidate in sorted(replacements, reverse=True):
        assembled = assembled[:start] + candidate + assembled[end:]

    audit_prompt = render_audit_prompt(
        original,
        assembled,
        selected_ids,
        content_type=content_type,
    )
    try:
        raw_audit = audit(audit_prompt, request_id=request_id)
        audit_text, audit_usage = _coerce_model_result(raw_audit)
        quality_audit = parse_quality_audit(audit_text)
    except Exception as exc:
        logger.exception(
            "LLM action=aigc_report_quality_audit request_id=%s model=%s status=failed error_type=%s",
            request_id,
            settings.AIGC_REVIEW_AUDIT_MODEL,
            type(exc).__name__,
        )
        raise AigcReviewModelError(f"独立质量审稿失败: {exc}") from exc

    audit_input_tokens = int(audit_usage.get("input_tokens") or _estimate_tokens(audit_prompt))
    audit_output_tokens = int(audit_usage.get("output_tokens") or _estimate_tokens(audit_text))
    audit_cost = float(
        audit_usage.get("cost_cny")
        or _gemini_flash_lite_cost_cny(audit_input_tokens, audit_output_tokens)
    )
    quality_passed = quality_audit_passed(quality_audit)
    total_cost = total_rewrite_cost + audit_cost
    if not quality_passed:
        return {
            **_manual_review_result(
                request_id=request_id,
                original=original,
                selected_ids=selected_ids,
                segment_results=segment_results,
                reason="独立终审未达到质量硬标准；候选不允许导出，原稿保持不变。",
                total_input_tokens=total_input_tokens + audit_input_tokens,
                total_output_tokens=total_output_tokens + audit_output_tokens,
                rewrite_cost_cny=total_cost,
            ),
            "quality_audit": quality_audit,
            "guarded_candidate_preview": assembled[:1200],
        }

    return {
        "request_id": request_id,
        "status": "candidate_ready",
        "deliverable": False,
        "next_action": "上传候选稿的真实朱雀复检 PDF；人工特征严格大于 50% 后才可标记通过。",
        "original_sha256": sha256_text(original),
        "candidate_sha256": sha256_text(assembled),
        "original_text": original,
        "candidate_text": assembled,
        "selected_segment_ids": selected_ids,
        "segment_results": segment_results,
        "quality_audit": quality_audit,
        "cost": {
            "currency": "CNY",
            "estimated": bool(audit_usage.get("estimated")) or not bool(audit_usage.get("input_tokens")),
            "input_tokens": total_input_tokens + audit_input_tokens,
            "output_tokens": total_output_tokens + audit_output_tokens,
            "estimated_cny": round(total_cost, 6),
        },
        "models": {
            "rewrite": settings.AIGC_REVIEW_REWRITE_MODEL,
            "audit": settings.AIGC_REVIEW_AUDIT_MODEL,
            "rewrite_thinking": "disabled",
        },
        "prompt_version": PROMPT_VERSION,
    }


def review_deterministic_constraints(
    original: str,
    candidate: str,
    *,
    protected_texts: Iterable[str] = (),
) -> DeterministicReview:
    reasons: list[str] = []
    ratio = len(candidate) / max(1, len(original))
    if not 0.8 <= ratio <= 1.25:
        reasons.append(f"长度比例 {ratio:.3f} 不在 0.80-1.25")
    paragraph_ok = _paragraph_count(original) == _paragraph_count(candidate)
    if not paragraph_ok:
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
    anchors_ok = not missing and not extra
    if not anchors_ok:
        reasons.append("数字、日期、金额、引语、书名或 URL 锚点出现次数发生变化")
    protected_values = tuple(value for value in protected_texts if value)
    protected_ok = all(candidate.count(value) >= original.count(value) for value in protected_values)
    if not protected_ok:
        reasons.append("古文/佛经受保护原文被改动")
    model_explanation_absent = not _contains_model_explanation(candidate)
    if not model_explanation_absent:
        reasons.append("候选包含模型说明、改写提示或非正文包装")
    passed = not reasons and bool(candidate.strip())
    return DeterministicReview(
        passed=passed,
        length_ratio=round(ratio, 4),
        paragraph_count_preserved=paragraph_ok,
        headings_preserved=headings_ok,
        anchors_preserved=anchors_ok,
        protected_text_preserved=protected_ok,
        model_explanation_absent=model_explanation_absent,
        missing_anchors=tuple(missing[:30]),
        extra_anchors=tuple(extra[:30]),
        reasons=tuple(reasons),
    )


def prepare_rewrite_protocol(
    source: str,
    *,
    protected_texts: Iterable[str] = (),
) -> RewriteProtocol:
    """Protect layout and immutable anchors before text reaches the rewrite model."""
    pieces = tuple(re.split(r"(\n[\t ]*(?:\n[\t ]*)*)", source))
    block_piece_indexes: list[int] = []
    block_payloads: list[str] = []
    protected_values: list[tuple[str, str]] = []
    protected_counter = 0

    for piece_index in range(0, len(pieces), 2):
        value = pieces[piece_index]
        if not value.strip():
            continue
        spans = _protected_spans(value, protected_texts=protected_texts)
        if _looks_like_heading(value.strip()):
            spans = [(0, len(value))]
        payload = value
        replacements: list[tuple[int, int, str, str]] = []
        for start, end in spans:
            protected_counter += 1
            token = f"[[KEEP_{protected_counter:04d}]]"
            replacements.append((start, end, token, value[start:end]))
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
        # A line wrap inside one numbered block is formatting noise, not a new paragraph.
        value = re.sub(r"[\t ]*\n+[\t ]*", " ", match.group(2)).strip()
        if not value:
            issues.append(f"编号块 P{int(match.group(1)):04d} 为空")
        rendered_blocks.append(value)

    combined_blocks = "\n".join(rendered_blocks)
    for token, protected_value in protocol.protected_values:
        count = combined_blocks.count(token)
        if count != 1:
            issues.append(f"受保护占位符 {token} 出现 {count} 次，应为 1 次")
        combined_blocks = combined_blocks.replace(token, protected_value)
    restored_blocks = combined_blocks.split("\n")
    if len(restored_blocks) != expected_count:
        issues.append("恢复受保护内容后编号块数量异常")
        return raw_candidate, issues, True
    for piece_index, value in zip(protocol.block_piece_indexes, restored_blocks, strict=True):
        candidate_pieces[piece_index] = value
    return "".join(candidate_pieces), issues, True


def format_rewrite_retry_feedback(
    review: DeterministicReview,
    *,
    protocol_issues: Iterable[str] = (),
) -> str:
    details = [*protocol_issues, *review.reasons]
    if review.missing_anchors:
        details.append("缺失锚点：" + "、".join(review.missing_anchors[:10]))
    if review.extra_anchors:
        details.append("多余锚点：" + "、".join(review.extra_anchors[:10]))
    return "上一次候选被机器拒绝。只修复以下问题，不得改变事实：\n- " + "\n- ".join(
        dict.fromkeys(details)
    )


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
) -> str:
    template = _REWRITE_PROMPT_PATH.read_text(encoding="utf-8")
    return (
        template.replace("{{SEGMENT_ID}}", str(segment_id))
        .replace("{{PROTECTED_ORIGINALS}}", "\n".join(protected_originals))
        .replace("{{PROTECTED_MAP}}", protected_map)
        .replace("{{BLOCK_COUNT}}", str(block_count))
        .replace("{{RETRY_FEEDBACK}}", retry_feedback)
        .replace("{{CONTENT_TYPE_RULES}}", _content_type_rules(content_type))
        .replace("{{PREVIOUS_CONTEXT}}", previous_context)
        .replace("{{ORIGINAL_SEGMENT}}", original_segment)
        .replace("{{NEXT_CONTEXT}}", next_context)
    )


def render_audit_prompt(
    original: str,
    candidate: str,
    segment_ids: Iterable[int],
    *,
    content_type: str = "article",
) -> str:
    template = _AUDIT_PROMPT_PATH.read_text(encoding="utf-8")
    return (
        template.replace("{{SEGMENT_IDS}}", json.dumps(list(segment_ids), ensure_ascii=False))
        .replace("{{CONTENT_TYPE_RULES}}", _content_type_rules(content_type))
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


def parse_quality_audit(text: str) -> dict[str, Any]:
    cleaned = _clean_model_text(text)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("质量审稿未返回 JSON")
    payload = json.loads(cleaned[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("质量审稿 JSON 不是对象")
    return payload


def quality_audit_passed(payload: dict[str, Any]) -> bool:
    score_rules = {
        "semantic_fidelity": 95,
        "information_completeness": 98,
        "fluency": 90,
        "voice_continuity": 90,
    }
    boolean_rules = (
        "no_added_facts",
        "no_removed_information",
        "no_stance_change",
        "no_plot_or_event_change",
    )
    return (
        bool(payload.get("pass"))
        and all(float(payload.get(key, 0)) >= threshold for key, threshold in score_rules.items())
        and all(payload.get(key) is True for key in boolean_rules)
    )


def model_strategy() -> dict[str, Any]:
    return {
        "rewrite_model": settings.AIGC_REVIEW_REWRITE_MODEL,
        "audit_model": settings.AIGC_REVIEW_AUDIT_MODEL,
        "rewrite_thinking": "disabled",
        "candidate_count": 1,
        "max_rewrite_attempts_per_segment": int(settings.AIGC_REVIEW_MAX_REWRITE_ATTEMPTS),
        "rewrite_protocol": "numbered_paragraphs_with_immutable_anchors",
        "audit_scope": "one_full_manuscript_call",
        "ocr": "local_only_zero_api_cost",
        "human_pass_rule": "> 50%",
        "safety_target_percent": float(settings.AIGC_REVIEW_HUMAN_TARGET),
        "global_gate_changed": False,
        "novel_workflows_included": False,
    }


def _deepseek_rewrite(
    prompt: str,
    *,
    segment: ZhuqueSegment,
    request_id: str,
    attempt: int = 1,
) -> tuple[str, dict[str, Any]]:
    model = settings.AIGC_REVIEW_REWRITE_MODEL
    max_tokens = min(12000, max(1600, int(len(segment.mapped_text) * 0.9)))
    logger.info(
        "LLM action=aigc_report_rewrite request_id=%s model=%s segment=%s attempt=%s status=started",
        request_id,
        model,
        segment.segment_id,
        attempt,
    )
    with distributed_semaphore(
        "aigc-report-review",
        limit=max(1, int(settings.AIGC_GATE_REWRITE_CONCURRENCY)),
    ):
        response = get_deepseek_client().chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.65,
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
        "LLM action=aigc_report_rewrite request_id=%s model=%s segment=%s attempt=%s status=succeeded input_tokens=%s output_tokens=%s cost_cny=%.6f",
        request_id,
        model,
        segment.segment_id,
        attempt,
        input_tokens,
        output_tokens,
        cost,
    )
    return content, {"input_tokens": input_tokens, "output_tokens": output_tokens, "cost_cny": cost}


def _gemini_audit(prompt: str, *, request_id: str) -> tuple[str, dict[str, Any]]:
    model = settings.AIGC_REVIEW_AUDIT_MODEL
    logger.info(
        "LLM action=aigc_report_quality_audit request_id=%s model=%s status=started",
        request_id,
        model,
    )
    content = generate_gemini_text(
        prompt,
        model=model,
        temperature=0.0,
        max_output_tokens=1800,
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
            "rewrite": settings.AIGC_REVIEW_REWRITE_MODEL,
            "audit": settings.AIGC_REVIEW_AUDIT_MODEL,
            "rewrite_thinking": "disabled",
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
