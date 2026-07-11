"""Global Tencent AIGC detection and targeted rewrite gate."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from app.core.config import settings

logger = logging.getLogger(__name__)


class AigcQualityGateError(RuntimeError):
    """Raised when an enabled AIGC gate cannot produce an accepted manuscript."""


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
        parts = [
            f"Score={self.score}",
            f"Suggestion={self.suggestion or 'unknown'}",
        ]
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
class AigcGateRound:
    round_number: int
    detections: tuple[AigcDetection, ...]
    rewritten_chunk_indexes: tuple[int, ...]


@dataclass(frozen=True)
class AigcGateResult:
    text: str
    passed: bool
    max_score: int
    rounds: tuple[AigcGateRound, ...]


class AigcDetector(Protocol):
    def detect(self, text: str, *, data_id: str) -> AigcDetection: ...


RewriteChunk = Callable[[str, str, str], Awaitable[str]]

_PARAGRAPH_RE = re.compile(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", re.S)
_HEADING_RE = re.compile(r"^(?:@|#{1,6}\s|第[一二三四五六七八九十百千万0-9]+[章节卷部篇回]\s*$)")
_ORIGINAL_CLASSICAL_RE = re.compile(r"^(?:【P\d{4}】\s*)?[（(]原文[）)]")
_SENTENCE_BREAK_RE = re.compile(r"(?<=[。！？!?；;])")
_SENTENCE_RE = re.compile(r"[^。！？!?；;]+[。！？!?；;]?")
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
    r"https?://\S+|《[^》\n]{1,80}》|`[^`\n]+`|"
    r"\d+(?:\.\d+)?(?:%|％|元|万元|亿元|年|月|日|时|分|秒|人|字|章|册|本|倍|公里|米|千克|克)?|"
    r"“[^”\n]{1,180}”|\"[^\"\n]{1,180}\""
)


def aigc_gate_configured() -> bool:
    return bool(
        settings.AIGC_GATE_ENABLED
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
            combined_length = end - previous_start
            if combined_length <= maximum and not gap.strip():
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
        text = source[start:end].strip()
        if len(text) < 20:
            continue
        leading = len(source[start:end]) - len(source[start:end].lstrip())
        trailing = len(source[start:end]) - len(source[start:end].rstrip())
        clean_start = start + leading
        clean_end = end - trailing
        chunks.append(AigcTextChunk(len(chunks), clean_start, clean_end, source[clean_start:clean_end]))
    return chunks


class TencentAigcDetector:
    def detect(self, text: str, *, data_id: str) -> AigcDetection:
        try:
            from tencentcloud.common import credential
            from tencentcloud.tms.v20201229 import models, tms_client
        except ImportError as exc:  # pragma: no cover - deployment dependency guard
            raise AigcQualityGateError("缺少 tencentcloud-sdk-python-tms") from exc

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
        return "只会向你提供翻译段。不得生成、补写或改动原文；保持译义、断句方向和术语准确。"
    if category in {"classical_chinese", "classical_chinese_interleaved", "classical_chinese_novel_style", "buddhist_scripture_story"}:
        return "保留古文或佛经所对应的人物、事件、义理、术语和先后关系，不得现代化改义或另编故事。"
    if "novel" in category or category in {"wisdom_stories_historical", "wisdom_stories_causal_philosophy"}:
        return "保持人物、剧情、对白事实、视角、时间线、伏笔、物品状态和章节钩子不变，只调整表达节奏。"
    return "保持原稿的知识、事实、数据、观点、论证关系和结论不变，不得加入未经原稿支持的个人经历或案例。"


def _local_risk_profile(text: str) -> str:
    """Explain observable prose patterns without pretending they came from Zhuque."""
    sentences = [item.strip() for item in _SENTENCE_RE.findall(text) if item.strip()]
    lengths = [len(item) for item in sentences]
    findings: list[str] = []
    if len(lengths) >= 4:
        mean = statistics.fmean(lengths)
        variation = statistics.pstdev(lengths) / max(mean, 1.0)
        if variation < 0.22:
            findings.append("连续句长过于整齐，需在不删信息的前提下拉开长短与停顿")
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


def _immutable_anchors(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(match.group(0) for match in _IMMUTABLE_ANCHOR_RE.finditer(text)))


def render_aigc_rewrite_prompt(chunk: str, category: str, feedback: str) -> str:
    template_path = Path(__file__).resolve().parents[1] / "prompts" / "aigc_natural_rewrite.md"
    template = template_path.read_text(encoding="utf-8")
    return template.format(
        category_guard=_category_guard(category),
        detector_feedback=feedback or "朱雀只返回概率，未提供具体语言原因。",
        chunk_text=chunk,
    )


async def rewrite_aigc_chunk_with_gemini(
    chunk: str,
    category: str,
    feedback: str,
) -> str:
    """Use the configured Gemini model as a detector-independent local rewriter."""
    from app.services.gemini import generate_text

    instruction = render_aigc_rewrite_prompt(chunk, category, feedback)
    return await asyncio.to_thread(
        generate_text,
        chunk,
        model=settings.GEMINI_MODEL,
        temperature=max(0.3, min(1.2, float(settings.AIGC_GATE_REWRITE_TEMPERATURE or 0.85))),
        max_output_tokens=None,
        system_instruction=instruction,
        request_timeout_seconds=settings.GEMINI_REQUEST_TIMEOUT,
        retries=settings.GEMINI_MAX_RETRIES,
    )


def _validate_rewrite(original: str, rewritten: str) -> None:
    candidate = rewritten.strip()
    if not candidate:
        raise AigcQualityGateError("降低 AI 率改写返回空文本")
    ratio = len(candidate) / max(len(original.strip()), 1)
    if ratio < 0.65 or ratio > 1.45:
        raise AigcQualityGateError(f"降低 AI 率改写长度异常: ratio={ratio:.2f}")
    if len(re.findall(r"\n\s*\n", candidate)) != len(re.findall(r"\n\s*\n", original.strip())):
        raise AigcQualityGateError("降低 AI 率改写改变了自然段数量")
    if re.search(r"^(?:@|#{1,6}\s)", candidate, flags=re.M):
        raise AigcQualityGateError("降低 AI 率改写新增了标题或格式标记")
    missing_anchors = [anchor for anchor in _immutable_anchors(original) if anchor not in candidate]
    if missing_anchors:
        raise AigcQualityGateError(
            "降低 AI 率改写丢失不可变锚点: " + "、".join(missing_anchors[:8])
        )


async def _detect_chunks(
    chunks: list[AigcTextChunk],
    *,
    detector: AigcDetector,
    cache: dict[str, AigcDetection] | None = None,
) -> list[AigcDetection]:
    semaphore = asyncio.Semaphore(max(1, int(settings.AIGC_GATE_DETECT_CONCURRENCY or 4)))
    detection_cache = cache if cache is not None else {}

    async def one(chunk: AigcTextChunk) -> AigcDetection:
        digest = hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
        detected = detection_cache.get(digest)
        if detected is None:
            async with semaphore:
                detected = await asyncio.to_thread(
                    detector.detect,
                    chunk.text,
                    data_id=f"vp-{digest[:20]}",
                )
            detection_cache[digest] = detected
        return AigcDetection(
            **{
                **asdict(detected),
                "chunk_index": chunk.index,
                "local_diagnostics": _local_risk_profile(chunk.text),
            }
        )

    return list(await asyncio.gather(*(one(chunk) for chunk in chunks)))


def _replace_chunks(source: str, replacements: list[tuple[AigcTextChunk, str]]) -> str:
    result = source
    for chunk, replacement in sorted(replacements, key=lambda item: item[0].start, reverse=True):
        result = result[: chunk.start] + replacement.strip() + result[chunk.end :]
    return result


def _write_report(output_dir: Path | None, result: AigcGateResult) -> None:
    if output_dir is None:
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "passed": result.passed,
        "maxScore": result.max_score,
        "threshold": int(settings.AIGC_GATE_THRESHOLD or 50),
        "textSha256": hashlib.sha256(result.text.encode("utf-8")).hexdigest(),
        "rounds": [
            {
                "round": item.round_number,
                "rewrittenChunkIndexes": list(item.rewritten_chunk_indexes),
                "detections": [asdict(detection) for detection in item.detections],
            }
            for item in result.rounds
        ],
    }
    (output_dir / "aigc_quality_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _read_cached_pass(output_dir: Path | None, text: str) -> AigcGateResult | None:
    if output_dir is None:
        return None
    report_path = output_dir / "aigc_quality_report.json"
    if not report_path.exists():
        return None
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if (
        payload.get("passed") is True
        and payload.get("textSha256") == digest
        and int(payload.get("threshold", -1)) == int(settings.AIGC_GATE_THRESHOLD or 50)
    ):
        return AigcGateResult(
            text=text,
            passed=True,
            max_score=int(payload.get("maxScore") or 0),
            rounds=(),
        )
    return None


async def enforce_aigc_quality_gate(
    text: str,
    *,
    category: str,
    rewrite_chunk: RewriteChunk,
    output_dir: Path | None = None,
    detector: AigcDetector | None = None,
) -> AigcGateResult:
    if not settings.AIGC_GATE_ENABLED:
        return AigcGateResult(text=text, passed=True, max_score=0, rounds=())
    if not aigc_gate_configured():
        message = "朱雀闸门已启用，但缺少 TENCENTCLOUD_SECRET_ID/SECRET_KEY/TENCENT_AIGC_BIZ_TYPE"
        if settings.AIGC_GATE_FAIL_CLOSED:
            raise AigcQualityGateError(message)
        logger.error(message)
        return AigcGateResult(text=text, passed=False, max_score=100, rounds=())

    cached = _read_cached_pass(output_dir, text)
    if cached is not None:
        logger.info("复用终稿朱雀检测通过报告，正文哈希未变化")
        return cached

    active_detector = detector or TencentAigcDetector()
    threshold = max(0, min(100, int(settings.AIGC_GATE_THRESHOLD or 50)))
    max_rounds = max(0, int(settings.AIGC_GATE_MAX_REWRITE_ROUNDS or 2))
    current = text
    rounds: list[AigcGateRound] = []
    max_score = 0
    detection_cache: dict[str, AigcDetection] = {}

    for round_number in range(max_rounds + 1):
        chunks = build_aigc_chunks(current, category=category)
        detections = await _detect_chunks(
            chunks,
            detector=active_detector,
            cache=detection_cache,
        )
        max_score = max((item.score for item in detections), default=0)
        high_indexes = {
            item.chunk_index
            for item in detections
            if item.score > threshold or item.suggestion.casefold() == "block"
        }
        if not high_indexes:
            rounds.append(AigcGateRound(round_number, tuple(detections), ()))
            result = AigcGateResult(current, True, max_score, tuple(rounds))
            _write_report(output_dir, result)
            return result
        if round_number >= max_rounds:
            rounds.append(AigcGateRound(round_number, tuple(detections), ()))
            result = AigcGateResult(current, False, max_score, tuple(rounds))
            _write_report(output_dir, result)
            raise AigcQualityGateError(
                f"朱雀复检仍有 {len(high_indexes)} 个分块高于 {threshold}%（最高 {max_score}%）"
            )

        detection_by_index = {item.chunk_index: item for item in detections}
        chunk_by_index = {item.index: item for item in chunks}
        rewrite_semaphore = asyncio.Semaphore(max(1, int(settings.AIGC_GATE_REWRITE_CONCURRENCY or 2)))

        async def rewrite_one(index: int) -> tuple[AigcTextChunk, str]:
            chunk = chunk_by_index[index]
            async with rewrite_semaphore:
                rewritten = await rewrite_chunk(
                    chunk.text,
                    category,
                    detection_by_index[index].feedback,
                )
            _validate_rewrite(chunk.text, rewritten)
            return chunk, rewritten

        replacements = list(await asyncio.gather(*(rewrite_one(index) for index in sorted(high_indexes))))
        current = _replace_chunks(current, replacements)
        rounds.append(
            AigcGateRound(round_number, tuple(detections), tuple(sorted(high_indexes)))
        )

    raise AssertionError("unreachable AIGC gate loop")


__all__ = [
    "AigcDetection",
    "AigcGateResult",
    "AigcQualityGateError",
    "AigcTextChunk",
    "TencentAigcDetector",
    "aigc_gate_configured",
    "build_aigc_chunks",
    "enforce_aigc_quality_gate",
    "render_aigc_rewrite_prompt",
    "rewrite_aigc_chunk_with_gemini",
]
