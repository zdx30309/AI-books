"""Quality-first Tencent Zhuque final-manuscript gate.

Policy v2 keeps the formatted manuscript immutable, rewrites only risky chunks,
and accepts a candidate only after deterministic preservation checks, a
cross-provider semantic review, and a strict ``Pass`` + score ``< threshold``
Zhuque result.  The second model is called lazily to control API spend.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import math
import re
import statistics
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from app.core.config import settings

logger = logging.getLogger(__name__)

AIGC_POLICY_VERSION = "v2"
AIGC_PROMPT_VERSION = "v2.1"


class AigcQualityGateError(RuntimeError):
    """Base class for final-gate failures."""


class AigcTechnicalError(AigcQualityGateError):
    """A retryable provider, network, credential, or detector failure."""

    def __init__(self, message: str, *, report: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.report = report


class AigcManualReviewRequired(AigcQualityGateError):
    """The original is safe, but no candidate met both content and AIGC gates."""

    def __init__(self, message: str, *, result: AigcGateResult | None = None) -> None:
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class AigcTextChunk:
    index: int
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class AigcDetection:
    chunk_index: int
    score: int
    suggestion: str
    label: str = ""
    sub_label: str = ""
    request_id: str = ""
    extra: str = ""
    detail_results: tuple[dict[str, object], ...] = ()
    local_diagnostics: str = ""

    @property
    def feedback(self) -> str:
        parts = [f"Score={self.score}", f"Suggestion={self.suggestion or 'unknown'}"]
        if self.label:
            parts.append(f"Label={self.label}")
        if self.sub_label:
            parts.append(f"SubLabel={self.sub_label}")
        if self.extra:
            parts.append(f"Extra={self.extra[:1200]}")
        if self.detail_results:
            parts.append(
                "DetailResults="
                + json.dumps(self.detail_results, ensure_ascii=False, separators=(",", ":"))[:1800]
            )
        if self.local_diagnostics:
            parts.append(self.local_diagnostics)
        return "\n".join(parts)


@dataclass(frozen=True)
class AigcQualityReview:
    semantic_fidelity: int
    information_completeness: int
    fluency: int
    voice_continuity: int
    no_added_facts: bool
    no_removed_information: bool
    no_stance_change: bool
    no_plot_or_event_change: bool
    passed: bool
    reason: str = ""


@dataclass(frozen=True)
class AigcGateRound:
    round_number: int
    detections: tuple[AigcDetection, ...]
    rewritten_chunk_indexes: tuple[int, ...]
    candidates: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True)
class AigcGateResult:
    text: str
    passed: bool
    max_score: int
    rounds: tuple[AigcGateRound, ...]
    initial_max_score: int = 0
    rewrite_rounds: int = 0
    quality_passed: bool = True
    status: str = "passed"
    report: dict[str, object] = field(default_factory=dict)


class AigcDetector(Protocol):
    def detect(self, text: str, *, data_id: str) -> AigcDetection: ...


RewriteChunk = Callable[[str, str, str], Awaitable[str]]
CandidateGenerator = Callable[..., Awaitable[str]]
QualityAuditor = Callable[..., Awaitable[AigcQualityReview | dict[str, object] | str]]

_PARAGRAPH_RE = re.compile(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", re.S)
_HEADING_RE = re.compile(r"^(?:@|#{1,6}\s|[第卷部篇章节回一二三四五六七八九十百千万0-9]+[章节卷部篇回]\s*$)")
_ORIGINAL_CLASSICAL_RE = re.compile(r"^(?:【P\d{4}】\s*)?[（(]原文[）)]")
_SENTENCE_BREAK_RE = re.compile(r"(?<=[。！？!?；;])")
_SENTENCE_RE = re.compile(r"[^。！？!?；;]+[。！？!?；;]?")
_MODEL_EXPLANATION_RE = re.compile(
    r"(?:作为(?:一个|一名)?AI|以下是(?:改写|修改)后的|修改说明|改写说明|AI\s*检测|朱雀(?:分数|检测))",
    re.I,
)
_TEMPLATE_PHRASES = (
    "值得注意的是",
    "综上所述",
    "由此可见",
    "首先",
    "其次",
    "最后",
    "不仅",
    "而且",
    "这说明",
    "从某种意义上说",
    "不可忽视的是",
)
_IMMUTABLE_ANCHOR_RE = re.compile(
    r"https?://[^\s)）】]+"
    r"|《[^》\n]{1,120}》"
    r"|`[^`\n]+`"
    r"|\d{4}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?"
    r"|(?:¥|￥|\$|USD\s*|CNY\s*)\d+(?:,\d{3})*(?:\.\d+)?"
    r"|\d+(?:,\d{3})*(?:\.\d+)?(?:%|％|元|万元|亿元|美元|人民币|年|月|日|时|分|秒|人|字|章|册|公里|米|千克|克|吨|次|倍)"
    r"|“[^”\n]{1,240}”|‘[^’\n]{1,240}’|\"[^\"\n]{1,240}\""
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _model_combination() -> str:
    openai_model = str(
        getattr(settings, "AIGC_OPENAI_MODEL", "")
        or settings.OPENAI_WRITER_MODEL
    ).strip()
    return f"gemini:{settings.GEMINI_MODEL}|openai:{openai_model}"


def _cache_key(text: str, category: str, threshold: int) -> str:
    payload = {
        "textSha256": _sha256(text),
        "category": category,
        "thresholdExclusive": threshold,
        "policyVersion": AIGC_POLICY_VERSION,
        "promptVersion": AIGC_PROMPT_VERSION,
        "modelCombination": _model_combination(),
    }
    return _sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def aigc_gate_configured(*, require_enabled: bool = True) -> bool:
    return bool(
        (settings.AIGC_GATE_ENABLED or not require_enabled)
        and settings.TENCENTCLOUD_SECRET_ID.strip()
        and settings.TENCENTCLOUD_SECRET_KEY.strip()
        and settings.TENCENT_AIGC_BIZ_TYPE.strip()
    )


def _is_protected_paragraph(text: str, category: str) -> bool:
    stripped = text.strip()
    if not stripped or _HEADING_RE.match(stripped):
        return True
    if stripped.startswith(("```", "![", "<img", "|")):
        return True
    if category == "classical_chinese_reference_rewrite" and _ORIGINAL_CLASSICAL_RE.match(stripped):
        return True
    return False


def _split_long_span(source: str, start: int, end: int, max_chars: int) -> list[tuple[int, int]]:
    text = source[start:end]
    if len(text) <= max_chars:
        return [(start, end)]
    spans: list[tuple[int, int]] = []
    chunk_start = 0
    cursor = 0
    for sentence in _SENTENCE_BREAK_RE.split(text):
        if not sentence:
            continue
        sentence_end = cursor + len(sentence)
        if sentence_end - chunk_start <= max_chars:
            cursor = sentence_end
            continue
        if cursor > chunk_start:
            spans.append((start + chunk_start, start + cursor))
            chunk_start = cursor
        while sentence_end - chunk_start > max_chars:
            cut = chunk_start + max_chars
            spans.append((start + chunk_start, start + cut))
            chunk_start = cut
        cursor = sentence_end
    if cursor > chunk_start:
        spans.append((start + chunk_start, start + cursor))
    return spans


def build_aigc_chunks(
    source: str,
    *,
    category: str,
    min_chars: int | None = None,
    max_chars: int | None = None,
) -> list[AigcTextChunk]:
    minimum = max(20, int(min_chars or settings.AIGC_GATE_CHUNK_MIN_CHARS or 350))
    maximum = max(minimum, min(2000, int(max_chars or settings.AIGC_GATE_CHUNK_MAX_CHARS or 1800)))
    raw_spans: list[tuple[int, int]] = []
    for match in _PARAGRAPH_RE.finditer(source):
        if _is_protected_paragraph(match.group(0), category):
            continue
        raw_spans.extend(_split_long_span(source, match.start(), match.end(), maximum))

    grouped: list[tuple[int, int]] = []
    for start, end in raw_spans:
        if grouped:
            previous_start, previous_end = grouped[-1]
            gap = source[previous_end:start]
            if end - previous_start <= maximum and not gap.strip():
                grouped[-1] = (previous_start, end)
                continue
        grouped.append((start, end))

    if len(grouped) >= 2 and grouped[-1][1] - grouped[-1][0] < minimum:
        previous_start, previous_end = grouped[-2]
        last_start, last_end = grouped[-1]
        if last_end - previous_start <= 2000 and not source[previous_end:last_start].strip():
            grouped[-2:] = [(previous_start, last_end)]

    chunks: list[AigcTextChunk] = []
    for start, end in grouped:
        raw = source[start:end]
        if len(raw.strip()) < 20:
            continue
        clean_start = start + (len(raw) - len(raw.lstrip()))
        clean_end = end - (len(raw) - len(raw.rstrip()))
        chunks.append(AigcTextChunk(len(chunks), clean_start, clean_end, source[clean_start:clean_end]))
    return chunks


class TencentAigcDetector:
    def detect(self, text: str, *, data_id: str) -> AigcDetection:
        try:
            from tencentcloud.common import credential
            from tencentcloud.tms.v20201229 import models, tms_client
        except ImportError as exc:  # pragma: no cover - deployment dependency guard
            raise AigcTechnicalError("缺少 tencentcloud-sdk-python-tms") from exc

        try:
            cred = credential.Credential(
                settings.TENCENTCLOUD_SECRET_ID,
                settings.TENCENTCLOUD_SECRET_KEY,
            )
            client = tms_client.TmsClient(cred, settings.TENCENT_AIGC_REGION)
            request = models.TextModerationRequest()
            request.from_json_string(
                json.dumps(
                    {
                        "Content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
                        "BizType": settings.TENCENT_AIGC_BIZ_TYPE,
                        "DataId": data_id[:64],
                        "SourceLanguage": "zh",
                        "Type": "TEXT_AIGC",
                    }
                )
            )
            response = client.TextModeration(request)
            payload = json.loads(response.to_json_string())
        except Exception as exc:  # pragma: no cover - requires live Tencent service
            raise AigcTechnicalError(f"腾讯朱雀检测调用失败: {type(exc).__name__}: {exc}") from exc
        detail_results = tuple(
            item for item in (payload.get("DetailResults") or []) if isinstance(item, dict)
        )
        return AigcDetection(
            chunk_index=-1,
            score=int(payload.get("Score") or 0),
            suggestion=str(payload.get("Suggestion") or ""),
            label=str(payload.get("Label") or ""),
            sub_label=str(payload.get("SubLabel") or ""),
            request_id=str(payload.get("RequestId") or ""),
            extra=str(payload.get("Extra") or ""),
            detail_results=detail_results,
        )


def _category_guard(category: str) -> str:
    if category == "classical_chinese_reference_rewrite":
        return "只会向你提供译文段。不得生成、补写或改动原文；保持释义、断句方向和术语准确。"
    if category in {
        "classical_chinese",
        "classical_chinese_interleaved",
        "classical_chinese_novel_style",
        "buddhist_scripture_story",
    }:
        return "保留古文或佛经对应的人物、事件、义理、术语和先后关系，不得现代化改义或另编故事。"
    if "novel" in category or category in {
        "wisdom_stories_historical",
        "wisdom_stories_causal_philosophy",
    }:
        return "保持人物、剧情、对白事实、视角、时间线、伏笔、物品状态和章节钩子不变，只调整表达节奏。"
    return "保持原稿的知识、事实、数据、观点、论证关系和结论不变，不得加入原稿未支持的个人经历或案例。"


def _local_risk_profile(text: str) -> str:
    sentences = [item.strip() for item in _SENTENCE_RE.findall(text) if item.strip()]
    lengths = [len(item) for item in sentences]
    findings: list[str] = []
    if len(lengths) >= 4:
        mean = statistics.fmean(lengths)
        if statistics.pstdev(lengths) / max(mean, 1.0) < 0.22:
            findings.append("连续句长过于整齐，需要在不删信息的前提下拉开长短与停顿")
    phrase_hits = [phrase for phrase in _TEMPLATE_PHRASES if phrase in text]
    if phrase_hits:
        findings.append("模板连接/总结词偏密：" + "、".join(phrase_hits[:6]))
    openers = [re.sub(r"^[\s，。；：、“”‘’]+", "", item)[:4] for item in sentences]
    repeated_openers = sorted({item for item in openers if item and openers.count(item) >= 2})
    if repeated_openers:
        findings.append("句首结构重复：" + "、".join(repeated_openers[:5]))
    symmetric_hits = len(re.findall(r"(?:一方面|另一方面|前者|后者|既要|又要|不仅|而且)", text))
    if symmetric_hits >= 3:
        findings.append("对称句式连续出现，应拆开局部排比但保留逻辑关系")
    if not findings:
        findings.append("未命中高置信规则；从句法、信息次序和段内节奏做克制重组")
    return "【本地可解释诊断（不是朱雀官方原因）】\n- " + "\n- ".join(findings)


def _anchor_counter(text: str) -> Counter[str]:
    return Counter(match.group(0) for match in _IMMUTABLE_ANCHOR_RE.finditer(text))


def _immutable_anchors(text: str) -> tuple[str, ...]:
    """Compatibility helper; repeated anchors intentionally remain repeated."""
    return tuple(match.group(0) for match in _IMMUTABLE_ANCHOR_RE.finditer(text))


def _paragraphs(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"\n\s*\n", text.strip())]


def _validate_rewrite(original: str, rewritten: str) -> None:
    candidate = rewritten.strip()
    if not candidate:
        raise AigcQualityGateError("降低 AI 率改写返回空文本")
    ratio = len(candidate) / max(len(original.strip()), 1)
    if ratio < 0.80 or ratio > 1.25:
        raise AigcQualityGateError(f"降低 AI 率改写长度异常: ratio={ratio:.2f}")
    original_paragraphs = _paragraphs(original)
    candidate_paragraphs = _paragraphs(candidate)
    if len(candidate_paragraphs) != len(original_paragraphs):
        raise AigcQualityGateError("降低 AI 率改写改变了自然段数量")
    if re.search(r"^(?:@|#{1,6}\s)", candidate, flags=re.M):
        raise AigcQualityGateError("降低 AI 率改写新增了标题或格式标记")
    if _MODEL_EXPLANATION_RE.search(candidate):
        raise AigcQualityGateError("降低 AI 率改写夹带了模型说明")

    original_counts = _anchor_counter(original)
    candidate_counts = _anchor_counter(candidate)
    missing = list((original_counts - candidate_counts).elements())
    added = list((candidate_counts - original_counts).elements())
    if missing:
        raise AigcQualityGateError("降低 AI 率改写丢失不可变锚点: " + "、".join(missing[:8]))
    if added:
        raise AigcQualityGateError("降低 AI 率改写新增或重复了不可变锚点: " + "、".join(added[:8]))
    for index, (before, after) in enumerate(zip(original_paragraphs, candidate_paragraphs), start=1):
        if _anchor_counter(before) != _anchor_counter(after):
            raise AigcQualityGateError(f"降低 AI 率改写移动了第 {index} 段的不可变锚点")


def render_aigc_rewrite_prompt(
    chunk: str,
    category: str,
    feedback: str,
    *,
    left_context: str = "",
    right_context: str = "",
    original_chunk: str | None = None,
    round_number: int = 1,
) -> str:
    template_path = Path(__file__).resolve().parents[1] / "prompts" / "aigc_natural_rewrite.md"
    template = template_path.read_text(encoding="utf-8")
    return template.format(
        category_guard=_category_guard(category),
        detector_feedback=feedback or "朱雀只返回了概率，未提供具体语言原因。",
        left_context=left_context[-800:] or "（无）",
        right_context=right_context[:800] or "（无）",
        original_chunk=(original_chunk or chunk).strip(),
        rewrite_seed=chunk.strip(),
        round_number=round_number,
    )


def render_aigc_audit_prompt(
    original: str,
    candidate: str,
    category: str,
    *,
    left_context: str = "",
    right_context: str = "",
) -> str:
    template_path = Path(__file__).resolve().parents[1] / "prompts" / "aigc_quality_audit.md"
    template = template_path.read_text(encoding="utf-8")
    return template.format(
        category_guard=_category_guard(category),
        left_context=left_context[-800:] or "（无）",
        right_context=right_context[:800] or "（无）",
        original_chunk=original.strip(),
        candidate_chunk=candidate.strip(),
    )


def _estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 1.7))


async def _call_gemini_model(prompt: str, *, action: str, max_output_tokens: int) -> tuple[str, dict[str, object]]:
    from app.services.gemini import generate_text

    started = time.monotonic()
    text = await asyncio.to_thread(
        generate_text,
        prompt,
        model=settings.GEMINI_MODEL,
        temperature=(
            max(0.2, min(1.0, float(settings.AIGC_GATE_REWRITE_TEMPERATURE or 0.75)))
            if action == "rewrite"
            else 0.0
        ),
        max_output_tokens=max_output_tokens,
        request_timeout_seconds=settings.GEMINI_REQUEST_TIMEOUT,
        retries=settings.GEMINI_MAX_RETRIES,
    )
    return text, {
        "provider": "gemini",
        "model": settings.GEMINI_MODEL,
        "inputTokens": _estimate_tokens(prompt),
        "outputTokens": _estimate_tokens(text),
        "cachedInputTokens": 0,
        "reasoningTokens": 0,
        "durationMs": round((time.monotonic() - started) * 1000),
        "estimatedCost": 0.0,
        "currency": "USD",
    }


async def _call_openai_model(prompt: str, *, action: str, max_output_tokens: int) -> tuple[str, dict[str, object]]:
    # Lazy import avoids a module cycle: content_engine imports this gate.
    from app.services import content_engine

    started = time.monotonic()
    model = str(
        getattr(settings, "AIGC_OPENAI_MODEL", "")
        or settings.OPENAI_WRITER_MODEL
    ).strip()
    # AIGC_OPENAI_MODEL defaults to the writer model.  The shared writer client
    # does not allow a per-call model override, so reject a divergent value
    # instead of mutating global settings while other tasks may be running.
    if model != settings.OPENAI_WRITER_MODEL:
        raise AigcTechnicalError(
            "AIGC_OPENAI_MODEL 必须与 OPENAI_WRITER_MODEL 一致；当前共享客户端不允许并发时切换全局模型"
        )
    text, usage = await asyncio.to_thread(
        content_engine._openai_writer_call_result,
        prompt,
        reasoning_effort=str(getattr(settings, "AIGC_OPENAI_REASONING_EFFORT", "low") or "low"),
        verbosity="low",
        max_output_tokens=max_output_tokens,
        prompt_cache_key=f"aigc:{AIGC_PROMPT_VERSION}:{_sha256(prompt)[:32]}",
        service_tier=str(getattr(settings, "AIGC_OPENAI_SERVICE_TIER", "flex") or "flex"),
        fallback_service_tier="default",
    )
    input_tokens = int(usage.get("input_tokens") or _estimate_tokens(prompt))
    cached_tokens = int(usage.get("cached_input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or _estimate_tokens(text))
    input_rate = float(getattr(settings, "LONG_NOVEL_INPUT_COST_PER_1M", 0.0) or 0.0)
    cached_rate = float(getattr(settings, "LONG_NOVEL_CACHED_INPUT_COST_PER_1M", 0.0) or 0.0)
    output_rate = float(getattr(settings, "LONG_NOVEL_OUTPUT_COST_PER_1M", 0.0) or 0.0)
    cost = (
        max(0, input_tokens - cached_tokens) * input_rate
        + cached_tokens * cached_rate
        + output_tokens * output_rate
    ) / 1_000_000
    return text, {
        "provider": "openai",
        "model": model,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cachedInputTokens": cached_tokens,
        "reasoningTokens": int(usage.get("reasoning_tokens") or 0),
        "durationMs": round((time.monotonic() - started) * 1000),
        "estimatedCost": round(cost, 8),
        "currency": "USD",
        "responseId": str(usage.get("response_id") or ""),
        "serviceTier": str(usage.get("service_tier") or ""),
    }


async def rewrite_aigc_chunk_with_gemini(
    chunk: str,
    category: str,
    feedback: str,
    *,
    left_context: str = "",
    right_context: str = "",
    original_chunk: str | None = None,
    round_number: int = 1,
) -> str:
    prompt = render_aigc_rewrite_prompt(
        chunk,
        category,
        feedback,
        left_context=left_context,
        right_context=right_context,
        original_chunk=original_chunk,
        round_number=round_number,
    )
    text, _ = await _call_gemini_model(
        prompt,
        action="rewrite",
        max_output_tokens=max(800, _estimate_tokens(chunk) * 2),
    )
    return text


async def rewrite_aigc_chunk_with_openai(
    chunk: str,
    category: str,
    feedback: str,
    *,
    left_context: str = "",
    right_context: str = "",
    original_chunk: str | None = None,
    round_number: int = 1,
) -> str:
    prompt = render_aigc_rewrite_prompt(
        chunk,
        category,
        feedback,
        left_context=left_context,
        right_context=right_context,
        original_chunk=original_chunk,
        round_number=round_number,
    )
    text, _ = await _call_openai_model(
        prompt,
        action="rewrite",
        max_output_tokens=max(800, _estimate_tokens(chunk) * 2),
    )
    return text


def _parse_json_object(raw: str) -> dict[str, object]:
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("quality auditor did not return JSON")
    payload = json.loads(cleaned[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("quality auditor JSON must be an object")
    return payload


def _as_int(payload: dict[str, object], key: str) -> int:
    try:
        return max(0, min(100, int(float(payload.get(key) or 0))))
    except (TypeError, ValueError):
        return 0


def _as_bool(payload: dict[str, object], key: str) -> bool:
    value = payload.get(key)
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"true", "1", "yes", "pass"}


def _quality_review_from_payload(payload: dict[str, object]) -> AigcQualityReview:
    fidelity = _as_int(payload, "semanticFidelity")
    completeness = _as_int(payload, "informationCompleteness")
    fluency = _as_int(payload, "fluency")
    voice = _as_int(payload, "voiceContinuity")
    flags = (
        _as_bool(payload, "noAddedFacts"),
        _as_bool(payload, "noRemovedInformation"),
        _as_bool(payload, "noStanceChange"),
        _as_bool(payload, "noPlotOrEventChange"),
    )
    passed = fidelity >= 95 and completeness >= 98 and fluency >= 90 and voice >= 90 and all(flags)
    return AigcQualityReview(
        semantic_fidelity=fidelity,
        information_completeness=completeness,
        fluency=fluency,
        voice_continuity=voice,
        no_added_facts=flags[0],
        no_removed_information=flags[1],
        no_stance_change=flags[2],
        no_plot_or_event_change=flags[3],
        passed=passed,
        reason=str(payload.get("reason") or "")[:1200],
    )


def _coerce_review(value: AigcQualityReview | dict[str, object] | str) -> AigcQualityReview:
    if isinstance(value, AigcQualityReview):
        return value
    if isinstance(value, str):
        value = _parse_json_object(value)
    if not isinstance(value, dict):
        raise ValueError("invalid quality review")
    return _quality_review_from_payload(value)


def _perfect_legacy_review() -> AigcQualityReview:
    return AigcQualityReview(100, 100, 100, 100, True, True, True, True, True, "legacy test adapter")


def _detection_passes(detection: AigcDetection, threshold: int) -> bool:
    return detection.score < threshold and detection.suggestion.strip().casefold() == "pass"


def _replace_chunks(source: str, replacements: list[tuple[AigcTextChunk, str]]) -> str:
    result = source
    for chunk, replacement in sorted(replacements, key=lambda item: item[0].start, reverse=True):
        result = result[: chunk.start] + replacement.strip() + result[chunk.end :]
    return result


def _candidate_context(source: str, chunk: AigcTextChunk) -> tuple[str, str]:
    return source[max(0, chunk.start - 800) : chunk.start], source[chunk.end : chunk.end + 800]


def _call_record(
    *,
    action: str,
    provider: str,
    model: str,
    status: str,
    duration_ms: int,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_input_tokens: int = 0,
    reasoning_tokens: int = 0,
    estimated_cost: float = 0.0,
    currency: str = "USD",
    request_id: str = "",
    cached: bool = False,
    error: str = "",
    chunk_index: int | None = None,
    round_number: int | None = None,
) -> dict[str, object]:
    return {
        "action": action,
        "provider": provider,
        "model": model,
        "status": status,
        "durationMs": duration_ms,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cachedInputTokens": cached_input_tokens,
        "reasoningTokens": reasoning_tokens,
        "estimatedCost": estimated_cost,
        "currency": currency,
        "requestId": request_id,
        "cached": cached,
        "error": error[:1200],
        "chunkIndex": chunk_index,
        "round": round_number,
    }


async def _detect_chunks(
    chunks: list[AigcTextChunk],
    *,
    detector: AigcDetector,
    cache: dict[str, AigcDetection] | None = None,
    calls: list[dict[str, object]] | None = None,
    round_number: int = 0,
) -> list[AigcDetection]:
    semaphore = asyncio.Semaphore(max(1, int(settings.AIGC_GATE_DETECT_CONCURRENCY or 4)))
    detection_cache = cache if cache is not None else {}

    async def one(chunk: AigcTextChunk) -> AigcDetection:
        digest = _sha256(chunk.text)
        detected = detection_cache.get(digest)
        if detected is not None:
            if calls is not None:
                calls.append(
                    _call_record(
                        action="zhuque_detect",
                        provider="tencent",
                        model="zhuque-text-aigc",
                        status="completed",
                        duration_ms=0,
                        currency="CNY",
                        request_id=detected.request_id,
                        cached=True,
                        chunk_index=chunk.index,
                        round_number=round_number,
                    )
                )
        else:
            started = time.monotonic()
            try:
                async with semaphore:
                    detected = await asyncio.to_thread(
                        detector.detect,
                        chunk.text,
                        data_id=f"vp-{digest[:20]}",
                    )
            except Exception as exc:
                if calls is not None:
                    calls.append(
                        _call_record(
                            action="zhuque_detect",
                            provider="tencent",
                            model="zhuque-text-aigc",
                            status="failed",
                            duration_ms=round((time.monotonic() - started) * 1000),
                            currency="CNY",
                            error=f"{type(exc).__name__}: {exc}",
                            chunk_index=chunk.index,
                            round_number=round_number,
                        )
                    )
                if isinstance(exc, AigcTechnicalError):
                    raise
                raise AigcTechnicalError(f"腾讯朱雀检测调用失败: {type(exc).__name__}: {exc}") from exc
            detection_cache[digest] = detected
            if calls is not None:
                calls.append(
                    _call_record(
                        action="zhuque_detect",
                        provider="tencent",
                        model="zhuque-text-aigc",
                        status="completed",
                        duration_ms=round((time.monotonic() - started) * 1000),
                        estimated_cost=0.05,
                        currency="CNY",
                        request_id=detected.request_id,
                        chunk_index=chunk.index,
                        round_number=round_number,
                    )
                )
        return AigcDetection(
            **{
                **asdict(detected),
                "chunk_index": chunk.index,
                "local_diagnostics": _local_risk_profile(chunk.text),
            }
        )

    return list(await asyncio.gather(*(one(chunk) for chunk in chunks)))


async def _detect_candidate(
    text: str,
    *,
    chunk_index: int,
    detector: AigcDetector,
    cache: dict[str, AigcDetection],
    calls: list[dict[str, object]],
    round_number: int,
) -> AigcDetection:
    chunk = AigcTextChunk(chunk_index, 0, len(text), text)
    return (
        await _detect_chunks(
            [chunk],
            detector=detector,
            cache=cache,
            calls=calls,
            round_number=round_number,
        )
    )[0]


async def _generate_candidate(
    provider: str,
    *,
    seed: str,
    original: str,
    category: str,
    feedback: str,
    left_context: str,
    right_context: str,
    round_number: int,
    callback: CandidateGenerator | None,
) -> tuple[str, dict[str, object]]:
    started = time.monotonic()
    if callback is not None:
        text = await callback(
            seed,
            category,
            feedback,
            left_context=left_context,
            right_context=right_context,
            original_chunk=original,
            round_number=round_number,
        )
        return text, {
            "provider": provider,
            "model": "test-or-injected",
            "inputTokens": _estimate_tokens(seed + original + left_context + right_context),
            "outputTokens": _estimate_tokens(text),
            "cachedInputTokens": 0,
            "reasoningTokens": 0,
            "durationMs": round((time.monotonic() - started) * 1000),
            "estimatedCost": 0.0,
            "currency": "USD",
        }
    prompt = render_aigc_rewrite_prompt(
        seed,
        category,
        feedback,
        left_context=left_context,
        right_context=right_context,
        original_chunk=original,
        round_number=round_number,
    )
    max_tokens = max(800, _estimate_tokens(original) * 2)
    if provider == "gemini":
        return await _call_gemini_model(prompt, action="rewrite", max_output_tokens=max_tokens)
    return await _call_openai_model(prompt, action="rewrite", max_output_tokens=max_tokens)


async def _audit_candidate(
    provider: str,
    *,
    original: str,
    candidate: str,
    category: str,
    left_context: str,
    right_context: str,
    callback: QualityAuditor | None,
) -> tuple[AigcQualityReview, dict[str, object]]:
    started = time.monotonic()
    if callback is not None:
        value = await callback(
            original,
            candidate,
            category,
            left_context=left_context,
            right_context=right_context,
        )
        review = _coerce_review(value)
        return review, {
            "provider": provider,
            "model": "test-or-injected",
            "inputTokens": _estimate_tokens(original + candidate + left_context + right_context),
            "outputTokens": 100,
            "cachedInputTokens": 0,
            "reasoningTokens": 0,
            "durationMs": round((time.monotonic() - started) * 1000),
            "estimatedCost": 0.0,
            "currency": "USD",
        }
    prompt = render_aigc_audit_prompt(
        original,
        candidate,
        category,
        left_context=left_context,
        right_context=right_context,
    )
    if provider == "gemini":
        raw, meta = await _call_gemini_model(prompt, action="audit", max_output_tokens=700)
    else:
        raw, meta = await _call_openai_model(prompt, action="audit", max_output_tokens=700)
    return _coerce_review(raw), meta


def _write_candidate(
    output_dir: Path | None,
    *,
    chunk_index: int,
    round_number: int,
    provider: str,
    text: str,
) -> str | None:
    if output_dir is None:
        return None
    directory = output_dir / "aigc_candidates"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"chunk_{chunk_index:04d}_round_{round_number}_{provider}.md"
    path.write_text(text, encoding="utf-8")
    return str(path)


def _report_payload(
    *,
    original: str,
    final_text: str | None,
    category: str,
    threshold: int,
    status: str,
    passed: bool,
    initial_max_score: int,
    final_max_score: int,
    rewrite_rounds: int,
    quality_passed: bool,
    rounds: list[AigcGateRound],
    candidates: list[dict[str, object]],
    calls: list[dict[str, object]],
    manual_review_reason: str = "",
    technical_error: str = "",
) -> dict[str, object]:
    selected = [item for item in candidates if item.get("selected")]
    return {
        "policyVersion": AIGC_POLICY_VERSION,
        "promptVersion": AIGC_PROMPT_VERSION,
        "modelCombination": _model_combination(),
        "cacheKey": _cache_key(original, category, threshold),
        "category": category,
        "status": status,
        "passed": passed,
        "threshold": threshold,
        "thresholdExclusive": True,
        "originalSha256": _sha256(original),
        "finalSha256": _sha256(final_text) if final_text is not None else None,
        "textSha256": _sha256(final_text) if final_text is not None else _sha256(original),
        "initialMaxScore": initial_max_score,
        "finalMaxScore": final_max_score,
        "maxScore": final_max_score,
        "rewriteRounds": rewrite_rounds,
        "qualityPassed": quality_passed,
        "selectedCandidates": selected,
        "manualReviewReason": manual_review_reason,
        "technicalError": technical_error,
        "rounds": [
            {
                "round": item.round_number,
                "rewrittenChunkIndexes": list(item.rewritten_chunk_indexes),
                "detections": [asdict(detection) for detection in item.detections],
                "candidates": list(item.candidates),
            }
            for item in rounds
        ],
        "candidates": candidates,
        "calls": calls,
        "costSummary": {
            "tencentCalls": sum(
                1 for item in calls if item.get("provider") == "tencent" and not item.get("cached")
            ),
            "modelCalls": sum(
                1 for item in calls if item.get("provider") in {"openai", "gemini"}
            ),
            "estimatedCny": round(
                sum(
                    float(item.get("estimatedCost") or 0)
                    for item in calls
                    if item.get("currency") == "CNY"
                ),
                4,
            ),
            "estimatedUsd": round(
                sum(
                    float(item.get("estimatedCost") or 0)
                    for item in calls
                    if item.get("currency") == "USD"
                ),
                8,
            ),
        },
    }


def _write_report(output_dir: Path | None, payload: dict[str, object]) -> None:
    if output_dir is None:
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "aigc_quality_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_final_artifact(output_dir: Path | None, text: str) -> None:
    if output_dir is None:
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "05_aigc_final.md").write_text(text, encoding="utf-8")


def _read_cached_pass(
    output_dir: Path | None,
    original: str,
    *,
    category: str,
    threshold: int,
) -> AigcGateResult | None:
    if output_dir is None:
        return None
    report_path = output_dir / "aigc_quality_report.json"
    final_path = output_dir / "05_aigc_final.md"
    if not report_path.exists() or not final_path.exists():
        return None
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        final_text = final_path.read_text(encoding="utf-8")
    except (OSError, ValueError, TypeError):
        return None
    if (
        payload.get("passed") is True
        and payload.get("status") == "passed"
        and payload.get("policyVersion") == AIGC_POLICY_VERSION
        and payload.get("promptVersion") == AIGC_PROMPT_VERSION
        and payload.get("modelCombination") == _model_combination()
        and payload.get("cacheKey") == _cache_key(original, category, threshold)
        and payload.get("originalSha256") == _sha256(original)
        and payload.get("finalSha256") == _sha256(final_text)
        and int(payload.get("threshold", -1)) == threshold
        and payload.get("thresholdExclusive") is True
    ):
        return AigcGateResult(
            text=final_text,
            passed=True,
            max_score=int(payload.get("finalMaxScore") or payload.get("maxScore") or 0),
            rounds=(),
            initial_max_score=int(payload.get("initialMaxScore") or 0),
            rewrite_rounds=int(payload.get("rewriteRounds") or 0),
            quality_passed=bool(payload.get("qualityPassed")),
            status="passed",
            report=payload,
        )
    return None


def _load_detection_cache(
    output_dir: Path | None,
    original: str,
    *,
    category: str,
    threshold: int,
) -> dict[str, AigcDetection]:
    """Reuse paid Zhuque results when every policy-sensitive cache field matches."""
    if output_dir is None:
        return {}
    report_path = output_dir / "aigc_quality_report.json"
    if not report_path.exists():
        return {}
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if (
        payload.get("cacheKey") != _cache_key(original, category, threshold)
        or payload.get("policyVersion") != AIGC_POLICY_VERSION
        or payload.get("promptVersion") != AIGC_PROMPT_VERSION
        or payload.get("modelCombination") != _model_combination()
        or payload.get("originalSha256") != _sha256(original)
        or int(payload.get("threshold", -1)) != threshold
        or payload.get("thresholdExclusive") is not True
    ):
        return {}

    cache: dict[str, AigcDetection] = {}
    original_chunks = build_aigc_chunks(original, category=category)
    first_round = next(
        (item for item in (payload.get("rounds") or []) if isinstance(item, dict) and item.get("round") == 0),
        None,
    )
    if isinstance(first_round, dict):
        by_index = {
            int(item.get("chunk_index")): item
            for item in (first_round.get("detections") or [])
            if isinstance(item, dict) and isinstance(item.get("chunk_index"), int)
        }
        for chunk in original_chunks:
            raw = by_index.get(chunk.index)
            if raw is not None:
                try:
                    cache[_sha256(chunk.text)] = AigcDetection(**raw)
                except TypeError:
                    continue
    for item in payload.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        digest = str(item.get("candidateSha256") or "")
        raw = item.get("detection")
        if not digest or not isinstance(raw, dict):
            continue
        try:
            cache[digest] = AigcDetection(**raw)
        except TypeError:
            continue
    return cache


async def enforce_aigc_quality_gate(
    text: str,
    *,
    category: str,
    rewrite_chunk: RewriteChunk | None = None,
    output_dir: Path | None = None,
    detector: AigcDetector | None = None,
    candidate_generators: dict[str, CandidateGenerator] | None = None,
    quality_auditors: dict[str, QualityAuditor] | None = None,
    required: bool | None = None,
) -> AigcGateResult:
    """Apply policy v2.

    ``rewrite_chunk`` is retained as a deterministic legacy/test adapter.  The
    production path omits it and uses Gemini first, then lazily escalates to
    OpenAI with the opposite provider performing the semantic audit.
    """
    active = settings.AIGC_GATE_ENABLED if required is None else bool(required)
    if not active:
        return AigcGateResult(text=text, passed=True, max_score=0, rounds=(), status="not_required")
    if not aigc_gate_configured(require_enabled=required is None):
        message = "朱雀闸门已启用，但缺少 TENCENTCLOUD_SECRET_ID/SECRET_KEY/TENCENT_AIGC_BIZ_TYPE"
        if settings.AIGC_GATE_FAIL_CLOSED:
            raise AigcTechnicalError(message)
        logger.error(message)
        return AigcGateResult(
            text=text,
            passed=False,
            max_score=100,
            rounds=(),
            quality_passed=False,
            status="technical_failed",
        )

    threshold = max(0, min(100, int(settings.AIGC_GATE_THRESHOLD or 50)))
    cached = _read_cached_pass(output_dir, text, category=category, threshold=threshold)
    if cached is not None:
        logger.info("复用 AIGC v2 通过报告；原稿、策略、Prompt 和模型组合均未变化")
        return cached

    original = text
    active_detector = detector or TencentAigcDetector()
    max_rounds = max(0, int(settings.AIGC_GATE_MAX_REWRITE_ROUNDS or 2))
    detection_cache = _load_detection_cache(
        output_dir,
        original,
        category=category,
        threshold=threshold,
    )
    calls: list[dict[str, object]] = []
    rounds: list[AigcGateRound] = []
    candidates: list[dict[str, object]] = []
    initial_max_score = 0
    final_max_score = 0
    max_rewrite_round = 0

    legacy_mode = rewrite_chunk is not None
    generators = dict(candidate_generators or {})
    auditors = dict(quality_auditors or {})
    if legacy_mode:
        async def legacy_generator(seed: str, category_name: str, feedback: str, **_: object) -> str:
            assert rewrite_chunk is not None
            return await rewrite_chunk(seed, category_name, feedback)

        generators = {"legacy": legacy_generator}

    try:
        initial_chunks = build_aigc_chunks(original, category=category)
        initial_detections = await _detect_chunks(
            initial_chunks,
            detector=active_detector,
            cache=detection_cache,
            calls=calls,
            round_number=0,
        )
        initial_max_score = max((item.score for item in initial_detections), default=0)
        risky_indexes = {
            item.chunk_index for item in initial_detections if not _detection_passes(item, threshold)
        }
        rounds.append(
            AigcGateRound(
                0,
                tuple(initial_detections),
                tuple(sorted(risky_indexes)),
            )
        )
        if not risky_indexes:
            report = _report_payload(
                original=original,
                final_text=original,
                category=category,
                threshold=threshold,
                status="passed",
                passed=True,
                initial_max_score=initial_max_score,
                final_max_score=initial_max_score,
                rewrite_rounds=0,
                quality_passed=True,
                rounds=rounds,
                candidates=candidates,
                calls=calls,
            )
            result = AigcGateResult(
                original,
                True,
                initial_max_score,
                tuple(rounds),
                initial_max_score,
                0,
                True,
                "passed",
                report,
            )
            _write_final_artifact(output_dir, original)
            _write_report(output_dir, report)
            return result

        detection_by_index = {item.chunk_index: item for item in initial_detections}
        chunk_by_index = {item.index: item for item in initial_chunks}
        replacements: list[tuple[AigcTextChunk, str]] = []
        failed_reasons: list[str] = []

        for chunk_index in sorted(risky_indexes):
            chunk = chunk_by_index[chunk_index]
            original_chunk = chunk.text
            left_context, right_context = _candidate_context(original, chunk)
            seed = original_chunk
            best_safe: tuple[int, str] | None = None
            selected_text: str | None = None

            provider_pairs = (
                [("legacy", "legacy")]
                if legacy_mode
                else [("gemini", "openai"), ("openai", "gemini")]
            )
            for round_number in range(1, max_rounds + 1):
                max_rewrite_round = max(max_rewrite_round, round_number)
                round_candidates: list[dict[str, object]] = []
                for provider, auditor_provider in provider_pairs:
                    record: dict[str, object] = {
                        "chunkIndex": chunk_index,
                        "round": round_number,
                        "provider": provider,
                        "auditorProvider": auditor_provider,
                        "selected": False,
                        "originalSha256": _sha256(original_chunk),
                    }
                    started = time.monotonic()
                    try:
                        generated, meta = await _generate_candidate(
                            provider,
                            seed=seed,
                            original=original_chunk,
                            category=category,
                            feedback=detection_by_index[chunk_index].feedback,
                            left_context=left_context,
                            right_context=right_context,
                            round_number=round_number,
                            callback=generators.get(provider),
                        )
                    except Exception as exc:
                        calls.append(
                            _call_record(
                                action="candidate_rewrite",
                                provider=provider,
                                model="unknown",
                                status="failed",
                                duration_ms=round((time.monotonic() - started) * 1000),
                                error=f"{type(exc).__name__}: {exc}",
                                chunk_index=chunk_index,
                                round_number=round_number,
                            )
                        )
                        raise AigcTechnicalError(
                            f"{provider} AIGC 候选生成失败: {type(exc).__name__}: {exc}"
                        ) from exc
                    calls.append(
                        _call_record(
                            action="candidate_rewrite",
                            provider=str(meta.get("provider") or provider),
                            model=str(meta.get("model") or ""),
                            status="completed",
                            duration_ms=int(meta.get("durationMs") or 0),
                            input_tokens=int(meta.get("inputTokens") or 0),
                            output_tokens=int(meta.get("outputTokens") or 0),
                            cached_input_tokens=int(meta.get("cachedInputTokens") or 0),
                            reasoning_tokens=int(meta.get("reasoningTokens") or 0),
                            estimated_cost=float(meta.get("estimatedCost") or 0),
                            currency=str(meta.get("currency") or "USD"),
                            request_id=str(meta.get("responseId") or ""),
                            chunk_index=chunk_index,
                            round_number=round_number,
                        )
                    )
                    candidate = generated.strip()
                    record["candidateSha256"] = _sha256(candidate)
                    record["artifactPath"] = _write_candidate(
                        output_dir,
                        chunk_index=chunk_index,
                        round_number=round_number,
                        provider=provider,
                        text=candidate,
                    )
                    try:
                        _validate_rewrite(original_chunk, candidate)
                    except AigcQualityGateError as exc:
                        record.update(
                            deterministicPassed=False,
                            deterministicError=str(exc),
                            accepted=False,
                            selectionReason="deterministic_guard_failed",
                        )
                        candidates.append(record)
                        round_candidates.append(record)
                        failed_reasons.append(f"块 {chunk_index}: {exc}")
                        continue
                    record["deterministicPassed"] = True

                    candidate_detection = await _detect_candidate(
                        candidate,
                        chunk_index=chunk_index,
                        detector=active_detector,
                        cache=detection_cache,
                        calls=calls,
                        round_number=round_number,
                    )
                    record["detection"] = asdict(candidate_detection)
                    if best_safe is None or candidate_detection.score < best_safe[0]:
                        best_safe = (candidate_detection.score, candidate)
                    if not _detection_passes(candidate_detection, threshold):
                        record.update(
                            qualityAuditSkipped=True,
                            accepted=False,
                            selectionReason="zhuque_not_passed_audit_skipped_to_save_cost",
                        )
                        candidates.append(record)
                        round_candidates.append(record)
                        continue

                    if legacy_mode:
                        review = _perfect_legacy_review()
                    else:
                        audit_started = time.monotonic()
                        try:
                            review, audit_meta = await _audit_candidate(
                                auditor_provider,
                                original=original_chunk,
                                candidate=candidate,
                                category=category,
                                left_context=left_context,
                                right_context=right_context,
                                callback=auditors.get(auditor_provider),
                            )
                            calls.append(
                                _call_record(
                                    action="quality_audit",
                                    provider=str(audit_meta.get("provider") or auditor_provider),
                                    model=str(audit_meta.get("model") or ""),
                                    status="completed",
                                    duration_ms=int(audit_meta.get("durationMs") or 0),
                                    input_tokens=int(audit_meta.get("inputTokens") or 0),
                                    output_tokens=int(audit_meta.get("outputTokens") or 0),
                                    cached_input_tokens=int(audit_meta.get("cachedInputTokens") or 0),
                                    reasoning_tokens=int(audit_meta.get("reasoningTokens") or 0),
                                    estimated_cost=float(audit_meta.get("estimatedCost") or 0),
                                    currency=str(audit_meta.get("currency") or "USD"),
                                    request_id=str(audit_meta.get("responseId") or ""),
                                    chunk_index=chunk_index,
                                    round_number=round_number,
                                )
                            )
                        except (ValueError, json.JSONDecodeError) as exc:
                            review = AigcQualityReview(
                                0, 0, 0, 0, False, False, False, False, False,
                                f"审稿模型返回不可解析结果: {exc}",
                            )
                            calls.append(
                                _call_record(
                                    action="quality_audit",
                                    provider=auditor_provider,
                                    model="unknown",
                                    status="invalid_output",
                                    duration_ms=round((time.monotonic() - audit_started) * 1000),
                                    error=str(exc),
                                    chunk_index=chunk_index,
                                    round_number=round_number,
                                )
                            )
                        except Exception as exc:
                            calls.append(
                                _call_record(
                                    action="quality_audit",
                                    provider=auditor_provider,
                                    model="unknown",
                                    status="failed",
                                    duration_ms=round((time.monotonic() - audit_started) * 1000),
                                    error=f"{type(exc).__name__}: {exc}",
                                    chunk_index=chunk_index,
                                    round_number=round_number,
                                )
                            )
                            raise AigcTechnicalError(
                                f"{auditor_provider} AIGC 质量审稿失败: {type(exc).__name__}: {exc}"
                            ) from exc
                    record["qualityReview"] = asdict(review)
                    record["accepted"] = review.passed
                    record["selectionReason"] = (
                        "quality_and_zhuque_passed" if review.passed else "quality_audit_failed"
                    )
                    candidates.append(record)
                    round_candidates.append(record)
                    if review.passed:
                        record["selected"] = True
                        selected_text = candidate
                        break
                if selected_text is not None:
                    break
                seed = best_safe[1] if best_safe is not None else original_chunk

            if selected_text is None:
                reason = (
                    f"块 {chunk_index} 朱雀复检仍有风险：在 {max_rounds} 轮内没有同时通过质量审稿"
                    f"和朱雀 Pass + <{threshold} 的候选"
                )
                failed_reasons.append(reason)
                continue
            replacements.append((chunk, selected_text))

        if len(replacements) != len(risky_indexes):
            reason = "；".join(dict.fromkeys(failed_reasons))[:4000]
            report = _report_payload(
                original=original,
                final_text=None,
                category=category,
                threshold=threshold,
                status="manual_review",
                passed=False,
                initial_max_score=initial_max_score,
                final_max_score=initial_max_score,
                rewrite_rounds=max_rewrite_round,
                quality_passed=False,
                rounds=rounds,
                candidates=candidates,
                calls=calls,
                manual_review_reason=reason,
            )
            result = AigcGateResult(
                original,
                False,
                initial_max_score,
                tuple(rounds),
                initial_max_score,
                max_rewrite_round,
                False,
                "manual_review",
                report,
            )
            _write_report(output_dir, report)
            raise AigcManualReviewRequired(reason or "AIGC 双达标失败，已保留高质量原稿", result=result)

        reconstructed = _replace_chunks(original, replacements)
        final_chunks = build_aigc_chunks(reconstructed, category=category)
        final_detections = await _detect_chunks(
            final_chunks,
            detector=active_detector,
            cache=detection_cache,
            calls=calls,
            round_number=max_rewrite_round + 1,
        )
        final_max_score = max((item.score for item in final_detections), default=0)
        final_failures = [item for item in final_detections if not _detection_passes(item, threshold)]
        rounds.append(AigcGateRound(max_rewrite_round + 1, tuple(final_detections), ()))
        if final_failures:
            reason = (
                f"拼回整稿后仍有 {len(final_failures)} 个正文块未达到朱雀 Pass + <{threshold}，"
                f"最高分 {final_max_score}；原稿已保留"
            )
            report = _report_payload(
                original=original,
                final_text=None,
                category=category,
                threshold=threshold,
                status="manual_review",
                passed=False,
                initial_max_score=initial_max_score,
                final_max_score=final_max_score,
                rewrite_rounds=max_rewrite_round,
                quality_passed=True,
                rounds=rounds,
                candidates=candidates,
                calls=calls,
                manual_review_reason=reason,
            )
            result = AigcGateResult(
                original,
                False,
                final_max_score,
                tuple(rounds),
                initial_max_score,
                max_rewrite_round,
                True,
                "manual_review",
                report,
            )
            _write_report(output_dir, report)
            raise AigcManualReviewRequired(reason, result=result)

        report = _report_payload(
            original=original,
            final_text=reconstructed,
            category=category,
            threshold=threshold,
            status="passed",
            passed=True,
            initial_max_score=initial_max_score,
            final_max_score=final_max_score,
            rewrite_rounds=max_rewrite_round,
            quality_passed=True,
            rounds=rounds,
            candidates=candidates,
            calls=calls,
        )
        result = AigcGateResult(
            reconstructed,
            True,
            final_max_score,
            tuple(rounds),
            initial_max_score,
            max_rewrite_round,
            True,
            "passed",
            report,
        )
        _write_final_artifact(output_dir, reconstructed)
        _write_report(output_dir, report)
        return result
    except AigcManualReviewRequired:
        raise
    except AigcTechnicalError as exc:
        report = _report_payload(
            original=original,
            final_text=None,
            category=category,
            threshold=threshold,
            status="technical_failed",
            passed=False,
            initial_max_score=initial_max_score,
            final_max_score=final_max_score,
            rewrite_rounds=max_rewrite_round,
            quality_passed=False,
            rounds=rounds,
            candidates=candidates,
            calls=calls,
            technical_error=f"{type(exc).__name__}: {exc}",
        )
        _write_report(output_dir, report)
        exc.report = report
        raise


__all__ = [
    "AIGC_POLICY_VERSION",
    "AIGC_PROMPT_VERSION",
    "AigcDetection",
    "AigcGateResult",
    "AigcManualReviewRequired",
    "AigcQualityGateError",
    "AigcQualityReview",
    "AigcTechnicalError",
    "AigcTextChunk",
    "TencentAigcDetector",
    "aigc_gate_configured",
    "build_aigc_chunks",
    "enforce_aigc_quality_gate",
    "render_aigc_audit_prompt",
    "render_aigc_rewrite_prompt",
    "rewrite_aigc_chunk_with_gemini",
    "rewrite_aigc_chunk_with_openai",
]
