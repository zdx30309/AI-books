"""Service pipeline for family sisters-in-law long-form novels."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    LongNovelBible,
    LongNovelCanonFact,
    LongNovelChapter,
    LongNovelChapterPlan,
    LongNovelChapterStatus,
    LongNovelCharacterState,
    LongNovelConflictLine,
    LongNovelContinuityIssue,
    LongNovelEvent,
    LongNovelFamilyMember,
    LongNovelFamilyRelation,
    LongNovelGenerationLog,
    LongNovelObjectState,
    LongNovelProject,
    LongNovelProjectStatus,
    User,
)
from app.prompts.family_long_novel_prompts import (
    FAMILY_LONG_NOVEL_LABEL,
    FAMILY_LONG_NOVEL_TYPE,
    ZHOULI_30W_CHAPTER_BATCH_REPLAN_PROMPT,
    ZHOULI_30W_GLOBAL_REPLAN_PROMPT,
    ZHOULI_BIBLE_PROMPT,
    ZHOULI_CHAPTER_PLAN_PROMPT,
    ZHOULI_CHAPTER_WRITE_PROMPT,
    ZHOULI_CONTINUITY_CHECK_PROMPT,
    ZHOULI_OUTLINE_PROMPT,
    ZHOULI_QUALITY_CHECK_PROMPT,
    ZHOULI_REPAIR_PROMPT,
    ZHOULI_STATE_EXTRACT_PROMPT,
    render_prompt,
)
from app.prompts.novel_quality_prompts import GENERAL_NOVEL_PROSE_GUARDRAILS
from app.schemas.long_novel import (
    FamilyLongNovelBatchRequest,
    FamilyLongNovelCreateRequest,
    LongNovelAssistRequest,
    LongNovelAssistResponse,
)
from app.services import content_engine

logger = logging.getLogger(__name__)


class FamilyLongNovelError(RuntimeError):
    """Raised when the family long novel workflow cannot continue."""


class MalformedModelJsonError(FamilyLongNovelError):
    """Raised when model text cannot be parsed as JSON after local repair attempts."""

    def __init__(self, message: str, raw_text: str) -> None:
        super().__init__(message)
        self.raw_text = raw_text


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_ALNUM_RE = re.compile(r"[A-Za-z0-9]+")
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_CONTEXT_PACK_PLAN_RULES = """
Chapter Context Pack is the only source of continuity truth.
- For chapter 2 and later, the plan must directly continue previousChapter.endingHook and previousChapter.last800Chars.
- latestCharacterStates, familyRelations, objectStates, canonFacts, activeConflictLines and openContinuityIssues are hard constraints.
- Each scene must show which conflict line or state changes it advances; do not invent events that are not supported by the pack.
- Do not let a character know information outside their knowledge/secrets state.
""".strip()
_CONTEXT_PACK_WRITE_RULES = """
Continuity is mandatory.
- The opening of chapter 2 and later must visibly continue previousChapter.endingHook and previousChapter.last800Chars.
- Use latestCharacterStates as the current state of every family member; if a person is not updated in this chapter, keep their prior goal, leverage, knowledge and secrets.
- Use canonFacts and objectStates as immutable facts. Do not move objects, reveal secrets, resolve conflicts, or change relationships unless the current plan explicitly advances that change.
- At least one activeConflictLines item must be advanced in a concrete family-interest event, and the ending must leave the next chapter a usable endingHook.
- The writing must still be full-quality fiction, not a summary, outline, continuity report, or mechanical checklist.
""".strip()
_CONTEXT_PACK_STATE_RULES = """
Extract durable state for the next chapter. If a person, object, fact, conflict line or knowledge boundary changed, it must be written in JSON.
Also include "endingHook": a concise unresolved final beat that the next chapter must continue.
""".strip()
_CONTEXT_PACK_CHECK_RULES = """
Use Chapter Context Pack as hard continuity evidence. Focus on whether this chapter continued previousChapter.endingHook/last800Chars, respected latestCharacterStates/objectStates/canonFacts, advanced activeConflictLines, and avoided knowledge leaks.
""".strip()
_FORBIDDEN_WORDS = (
    "重生",
    "系统",
    "玄幻",
    "霸总",
    "金手指",
    "中彩票",
    "超级富豪",
    "机械降神",
)


def _word_count(text: str) -> int:
    return len(_CJK_RE.findall(text or "")) + len(_ALNUM_RE.findall(text or ""))


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _canonical_json_dumps(data: Any) -> str:
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _json_loads(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    cleaned = _JSON_FENCE_RE.sub("", cleaned).strip()
    if not cleaned:
        raise FamilyLongNovelError("模型返回为空，无法解析 JSON")
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start : end + 1]
    last_error: json.JSONDecodeError | None = None
    for candidate in _json_repair_candidates(cleaned):
        try:
            parsed = json.loads(candidate)
            break
        except json.JSONDecodeError as exc:
            last_error = exc
    else:
        message = f"模型返回不是合法 JSON: {last_error}" if last_error else "模型返回不是合法 JSON"
        raise MalformedModelJsonError(message, cleaned)
    if not isinstance(parsed, dict):
        raise FamilyLongNovelError("模型 JSON 顶层必须是对象")
    return parsed


def _json_repair_candidates(text: str) -> list[str]:
    candidates = [text]
    repaired = text
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    repaired = re.sub(r"([}\]])\s*(?=[{\[])", r"\1,", repaired)
    repaired = re.sub(r"([}\]\"0-9])\s*\n\s*(\"[A-Za-z_][A-Za-z0-9_]*\"\s*:)", r"\1,\n\2", repaired)
    repaired = re.sub(r"(true|false|null)\s*\n\s*(\"[A-Za-z_][A-Za-z0-9_]*\"\s*:)", r"\1,\n\2", repaired)
    repaired = re.sub(r"([}\]\"0-9])\s*\n\s*(\"[^\"\n]+\"\s*(?:,|\]))", r"\1,\n\2", repaired)
    if repaired != text:
        candidates.append(repaired)
    balanced = _balance_json_tail(repaired)
    if balanced != repaired:
        candidates.append(balanced)
    return list(dict.fromkeys(candidates))


def _balance_json_tail(text: str) -> str:
    stack: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_string:
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char in "{[":
            stack.append(char)
        elif char in "}]" and stack:
            stack.pop()
    suffix = "".join("}" if char == "{" else "]" for char in reversed(stack))
    return text + suffix


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _get(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


def _text(data: dict[str, Any], *keys: str, default: str = "") -> str:
    value = _get(data, *keys, default=default)
    return str(value).strip() if value is not None else default


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean_title(title: str, fallback: str) -> str:
    cleaned = re.sub(r"[:：]", "", (title or "").strip())
    cleaned = re.sub(r"\s+", "", cleaned)
    return (cleaned[:10] or fallback)[:10]


def _run_writer(
    prompt: str,
    *,
    max_output_tokens: int,
    reasoning_effort: str | None = None,
    verbosity: str | None = None,
    prompt_cache_key: str | None = None,
    service_tier: str | None = None,
    background: bool = False,
    fallback_service_tier: str | None = None,
) -> str:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            content_engine._route_writer_call(
                prompt,
                reasoning_effort=reasoning_effort,
                verbosity=verbosity,
                max_output_tokens=max_output_tokens,
                prompt_cache_key=prompt_cache_key,
                service_tier=service_tier,
                background=background,
                background_timeout_seconds=settings.LONG_NOVEL_FLEX_TIMEOUT_SECONDS,
                background_poll_interval_seconds=settings.LONG_NOVEL_FLEX_POLL_INTERVAL_SECONDS,
                fallback_service_tier=fallback_service_tier,
            )
        )
    raise FamilyLongNovelError("family long novel service must run from a sync worker thread")


def _run_writer_with_usage(
    prompt: str,
    *,
    max_output_tokens: int,
    reasoning_effort: str | None = None,
    verbosity: str | None = None,
    prompt_cache_key: str | None = None,
    service_tier: str | None = None,
    background: bool = False,
    fallback_service_tier: str | None = None,
) -> tuple[str, dict[str, Any]]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            content_engine._route_writer_call_with_usage(
                prompt,
                reasoning_effort=reasoning_effort,
                verbosity=verbosity,
                max_output_tokens=max_output_tokens,
                prompt_cache_key=prompt_cache_key,
                service_tier=service_tier,
                background=background,
                background_timeout_seconds=settings.LONG_NOVEL_FLEX_TIMEOUT_SECONDS,
                background_poll_interval_seconds=settings.LONG_NOVEL_FLEX_POLL_INTERVAL_SECONDS,
                fallback_service_tier=fallback_service_tier,
            )
        )
    raise FamilyLongNovelError("family long novel service must run from a sync worker thread")


def _project_prompt_cache_key(project_id: UUID) -> str:
    return f"fln:{project_id}:p:v2"


def _chapter_prompt_cache_key(project_id: UUID, chapter_number: int) -> str:
    return f"fln:{project_id}:c{chapter_number}:v2"


def _long_novel_processing_policy() -> tuple[str | None, bool, str | None]:
    mode = str(settings.LONG_NOVEL_PROCESSING_MODE or "standard").strip().lower()
    if mode != "flex" or (settings.WRITER_PROVIDER or "openai").lower() != "openai":
        return None, False, None
    fallback = "auto" if settings.LONG_NOVEL_FLEX_FALLBACK_TO_STANDARD else None
    return "flex", bool(settings.LONG_NOVEL_FLEX_BACKGROUND), fallback


def _prompt_prefix_hash(prompt: str) -> str:
    marker = "[END AUTHORITATIVE ROLLING CHAPTER CONTEXT]"
    end = prompt.find(marker)
    prefix = prompt[: end + len(marker)] if end >= 0 else prompt
    return hashlib.sha256(prefix.encode("utf-8")).hexdigest()


def _ensure_chapter_prompt_cache_warm(
    db: Session,
    *,
    project_id: UUID,
    chapter_number: int,
    context_pack: dict[str, Any],
) -> None:
    """Prime the shared Flex prefix without changing any content-stage output."""

    requested_tier, _use_background, _fallback_tier = _long_novel_processing_policy()
    if requested_tier != "flex" or not settings.LONG_NOVEL_PROMPT_CACHE_WARMUP:
        return

    cache_key = _chapter_prompt_cache_key(project_id, chapter_number)
    warm_prompt = _with_project_cache_prefix(
        context_pack,
        (
            "[PROMPT CACHE PREFILL ONLY]\n"
            "Do not plan, draft, revise, summarize, or judge the novel. "
            "Return only CACHE_READY."
        ),
    )
    prefix_hash = _prompt_prefix_hash(warm_prompt)
    now = datetime.now(timezone.utc)
    verified_after = now - timedelta(hours=20)
    attempted_after = now - timedelta(minutes=10)

    verified = (
        db.query(LongNovelGenerationLog)
        .filter(
            LongNovelGenerationLog.project_id == project_id,
            LongNovelGenerationLog.chapter_number == chapter_number,
            LongNovelGenerationLog.action.in_(["warm_prompt_cache", "verify_prompt_cache"]),
            LongNovelGenerationLog.status == "completed",
            LongNovelGenerationLog.prompt_cache_key == cache_key,
            LongNovelGenerationLog.prompt_prefix_hash == prefix_hash,
            LongNovelGenerationLog.cached_prompt_tokens >= 1024,
            LongNovelGenerationLog.created_at >= verified_after,
        )
        .first()
    )
    if verified is not None:
        return

    recent_attempt = (
        db.query(LongNovelGenerationLog)
        .filter(
            LongNovelGenerationLog.project_id == project_id,
            LongNovelGenerationLog.chapter_number == chapter_number,
            LongNovelGenerationLog.action == "verify_prompt_cache",
            LongNovelGenerationLog.prompt_cache_key == cache_key,
            LongNovelGenerationLog.prompt_prefix_hash == prefix_hash,
            LongNovelGenerationLog.created_at >= attempted_after,
        )
        .first()
    )
    if recent_attempt is not None:
        return

    try:
        _log_llm_call(
            db,
            project_id=project_id,
            chapter_number=chapter_number,
            action="warm_prompt_cache",
            tier="cheap",
            prompt=warm_prompt,
            max_output_tokens=512,
            prompt_cache_key=cache_key,
            reasoning_effort="low",
        )
        first = (
            db.query(LongNovelGenerationLog)
            .filter(
                LongNovelGenerationLog.project_id == project_id,
                LongNovelGenerationLog.chapter_number == chapter_number,
                LongNovelGenerationLog.action == "warm_prompt_cache",
                LongNovelGenerationLog.prompt_cache_key == cache_key,
                LongNovelGenerationLog.prompt_prefix_hash == prefix_hash,
                LongNovelGenerationLog.status == "completed",
            )
            .order_by(LongNovelGenerationLog.created_at.desc())
            .first()
        )
        if first is not None and int(first.cached_prompt_tokens or 0) >= 1024:
            return
        _log_llm_call(
            db,
            project_id=project_id,
            chapter_number=chapter_number,
            action="verify_prompt_cache",
            tier="cheap",
            prompt=warm_prompt,
            max_output_tokens=512,
            prompt_cache_key=cache_key,
            reasoning_effort="low",
        )
    except Exception as exc:
        logger.warning(
            "chapter prompt cache warmup failed; continuing unchanged workflow project=%s chapter=%s error=%s",
            project_id,
            chapter_number,
            str(exc)[:500],
        )


def _model_for_tier(tier: str) -> str:
    configured = {
        "strong": settings.LONG_NOVEL_STRONG_MODEL,
        "default": settings.LONG_NOVEL_DEFAULT_MODEL,
        "cheap": settings.LONG_NOVEL_CHEAP_MODEL,
    }.get(tier, "")
    if configured:
        return configured
    provider = (settings.WRITER_PROVIDER or "openai").lower()
    if provider == "gemini":
        return settings.GEMINI_MODEL
    if provider == "deepseek":
        return settings.DEEPSEEK_MODEL
    return settings.OPENAI_WRITER_MODEL


def _estimate_long_novel_cost(
    *,
    prompt_tokens: int,
    cached_prompt_tokens: int,
    completion_tokens: int,
    service_tier: str | None = None,
) -> float:
    input_rate = max(0.0, float(settings.LONG_NOVEL_INPUT_COST_PER_1M or 0.0))
    cached_rate = max(0.0, float(settings.LONG_NOVEL_CACHED_INPUT_COST_PER_1M or 0.0))
    output_rate = max(0.0, float(settings.LONG_NOVEL_OUTPUT_COST_PER_1M or 0.0))
    if input_rate <= 0 and cached_rate <= 0 and output_rate <= 0:
        return 0.0
    cached = min(max(0, cached_prompt_tokens), max(0, prompt_tokens))
    uncached = max(0, prompt_tokens - cached)
    cost = (
        (uncached * input_rate)
        + (cached * cached_rate)
        + (max(0, completion_tokens) * output_rate)
    ) / 1_000_000
    if str(service_tier or "").strip().lower() in {"flex", "batch"}:
        cost *= 0.5
    return round(cost, 8)


def _chapter_target_words(project: LongNovelProject) -> int:
    chapters = max(1, min(80, int(project.target_chapters or 50)))
    total = max(1000, int(project.target_words or 200000))
    return max(1200, int(round(total / chapters)))


def _chapter_word_tolerance(project: LongNovelProject) -> int:
    return max(150, int(round(_chapter_target_words(project) * 0.08)))


def _outline_max_output_tokens(project: LongNovelProject) -> int:
    target = max(1, min(80, int(project.target_chapters or 50)))
    return min(32000, max(10000, 5000 + target * 900))


def _chapter_write_max_output_tokens(project: LongNovelProject) -> int:
    return min(50000, max(24000, _chapter_target_words(project) * 4))


_ASSIST_FIELD_RULES = {
    "premise": "生成一个80-140字的核心创意，必须有明确家庭利益矛盾、女主处境和可连续展开的冲突钩子。",
    "family_setup": "生成120-260字家庭关系设定，必须写清婆婆、丈夫、另一位妯娌、关键亲戚的利益位置和称谓关系。",
    "conflict_tags": "从候选矛盾中选择3-6个最适合本项目的标签，不要编造过泛标签。",
    "source_text": "生成一段180-360字可作为补充素材的现实家庭事件梗概，给后续圣经和大纲提供信息，不写正文。",
    "forbidden_rules": "生成一段简洁禁忌要求，强调现实家长里短，不要玄幻、重生、系统、霸总、金手指、机械降神。",
}


_ASSIST_CONFLICT_TAGS = [
    "婆媳偏心",
    "兄弟分家",
    "拆迁补偿",
    "房产归属",
    "养老赡养",
    "带娃教育",
    "彩礼嫁妆",
    "借钱不还",
    "小姑子啃老",
    "妯娌争宠",
    "丈夫愚孝",
    "亲戚道德绑架",
    "住院陪护",
    "年夜饭爆发",
]


def assist_long_novel_field(req: LongNovelAssistRequest) -> LongNovelAssistResponse:
    """Use the OpenAI writer model only when the user explicitly asks for form help."""

    if req.module_type != FAMILY_LONG_NOVEL_TYPE:
        raise FamilyLongNovelError("当前只支持家长里短·妯娌长篇模块的辅助填写")
    field_rule = _ASSIST_FIELD_RULES.get(req.field)
    if not field_rule:
        raise FamilyLongNovelError("不支持的辅助填写字段")
    compact_values = {
        key: value
        for key, value in (req.current_values or {}).items()
        if value not in (None, "", [], {})
    }
    context = _json_dumps(compact_values)[:4000]
    prompt = f"""
你是长篇小说策划助理。当前长篇模块是“家长里短·妯娌”，你只帮助填写创建表单，不生成正文，不创建项目。

必须遵守：
- 只服务现实家庭矛盾长篇，围绕婆媳、妯娌、房产、养老、借钱、带娃、分家、亲戚舆论等。
- 不要玄幻、重生、系统、霸总、金手指、机械降神。
- 输出要短，能直接粘进表单，不要解释。
- 如果用户已有方向，要顺着现有方向补强，不要推翻。
- 节省 token，不要扩写成长文。

字段：{req.field}
任务：{field_rule}
候选矛盾标签：{_json_dumps(_ASSIST_CONFLICT_TAGS)}
当前表单已有内容：
{context}

输出严格 JSON，不要代码块：
{{
  "content": "",
  "conflict_tags": []
}}
""".strip()
    output = _run_writer(
        prompt,
        max_output_tokens=1200,
        reasoning_effort="xhigh",
        verbosity="low",
    )
    data = _json_loads(output)
    tags = [str(item).strip() for item in _as_list(data.get("conflict_tags")) if str(item).strip()]
    tags = [tag for tag in tags if tag in _ASSIST_CONFLICT_TAGS][:6]
    return LongNovelAssistResponse(
        field=req.field,
        content=_text(data, "content")[:1200],
        conflict_tags=tags,
    )


def _log_llm_call(
    db: Session,
    *,
    project_id: UUID,
    chapter_number: int | None,
    action: str,
    tier: str,
    prompt: str,
    max_output_tokens: int,
    prompt_cache_key: str | None = None,
    reasoning_effort: str | None = None,
) -> str:
    requested_service_tier, use_background, fallback_service_tier = _long_novel_processing_policy()
    log = LongNovelGenerationLog(
        project_id=project_id,
        chapter_number=chapter_number,
        action=action,
        provider=settings.WRITER_PROVIDER,
        model=_model_for_tier(tier),
        prompt_tokens=max(1, len(prompt) // 4),
        service_tier=requested_service_tier or "default",
        prompt_cache_key=prompt_cache_key,
        prompt_prefix_hash=_prompt_prefix_hash(prompt),
        status="started",
    )
    db.add(log)
    db.commit()
    try:
        output, usage = _run_writer_with_usage(
            prompt,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
            prompt_cache_key=prompt_cache_key,
            service_tier=requested_service_tier,
            background=use_background,
            fallback_service_tier=fallback_service_tier,
        )
    except Exception as exc:
        log.status = "failed"
        log.error = str(exc)[:2000]
        db.add(log)
        db.commit()
        raise
    log.status = "completed"
    prompt_tokens = int(usage.get("input_tokens") or 0) or max(1, len(prompt) // 4)
    completion_tokens = int(usage.get("output_tokens") or 0) or max(1, len(output) // 4)
    cached_prompt_tokens = min(prompt_tokens, int(usage.get("cached_input_tokens") or 0))
    log.prompt_tokens = prompt_tokens
    log.cached_prompt_tokens = cached_prompt_tokens
    log.completion_tokens = completion_tokens
    log.reasoning_tokens = int(usage.get("reasoning_tokens") or 0)
    actual_service_tier = str(usage.get("service_tier") or requested_service_tier or "default")
    log.service_tier = actual_service_tier
    log.response_id = str(usage.get("response_id") or "") or None
    log.estimated_cost = _estimate_long_novel_cost(
        prompt_tokens=prompt_tokens,
        cached_prompt_tokens=cached_prompt_tokens,
        completion_tokens=completion_tokens,
        service_tier=actual_service_tier,
    )
    db.add(log)
    db.commit()
    return output


def _call_json(
    db: Session,
    *,
    project_id: UUID,
    chapter_number: int | None,
    action: str,
    tier: str,
    prompt: str,
    max_output_tokens: int = 16000,
    prompt_cache_key: str | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    return _json_loads(
        _log_llm_call(
            db,
            project_id=project_id,
            chapter_number=chapter_number,
            action=action,
            tier=tier,
            prompt=prompt,
            max_output_tokens=max_output_tokens,
            prompt_cache_key=prompt_cache_key,
            reasoning_effort=reasoning_effort,
        )
    )


def _call_text(
    db: Session,
    *,
    project_id: UUID,
    chapter_number: int | None,
    action: str,
    tier: str,
    prompt: str,
    max_output_tokens: int,
    prompt_cache_key: str | None = None,
    reasoning_effort: str | None = None,
) -> str:
    return _log_llm_call(
        db,
        project_id=project_id,
        chapter_number=chapter_number,
        action=action,
        tier=tier,
        prompt=prompt,
        max_output_tokens=max_output_tokens,
        prompt_cache_key=prompt_cache_key,
        reasoning_effort=reasoning_effort,
    ).strip()


def _mark_project_generating(db: Session, project: LongNovelProject) -> None:
    project.status = LongNovelProjectStatus.GENERATING
    db.add(project)
    db.commit()
    db.refresh(project)


def _mark_generation_failed(
    db: Session,
    project: LongNovelProject,
    *,
    plan: LongNovelChapterPlan | None = None,
    chapter: LongNovelChapter | None = None,
) -> None:
    db.rollback()
    project.status = LongNovelProjectStatus.FAILED
    db.add(project)
    if plan is not None:
        plan.status = LongNovelChapterStatus.FAILED
        db.add(plan)
    if chapter is not None and chapter.status != LongNovelChapterStatus.COMPLETED:
        chapter.status = LongNovelChapterStatus.FAILED
        db.add(chapter)
    db.commit()


def _project_payload(project: LongNovelProject) -> dict[str, Any]:
    return {
        "type": project.type,
        "moduleType": FAMILY_LONG_NOVEL_TYPE,
        "genreLabel": project.genre_label,
        "premise": project.premise,
        "familySetup": project.family_setup,
        "conflictTags": project.conflict_tags or [],
        "sourceType": project.source_type,
        "sourceText": project.source_text,
        "targetWords": project.target_words,
        "targetChapters": project.target_chapters,
        "averageChapterTargetWords": _chapter_target_words(project),
        "chapterWordTolerance": _chapter_word_tolerance(project),
        "generationMode": project.generation_mode,
        "style": project.style,
        "forbiddenRules": project.forbidden_rules,
    }


def _get_project(db: Session, project_id: UUID, user: User | None = None) -> LongNovelProject:
    query = db.query(LongNovelProject).filter(LongNovelProject.id == project_id)
    if user is not None:
        query = query.filter(LongNovelProject.owner_id == user.id)
    project = query.first()
    if project is None:
        raise FamilyLongNovelError("长篇项目不存在或无权访问")
    return project


def create_family_long_novel_project(
    db: Session,
    req: FamilyLongNovelCreateRequest,
    user: User | None,
) -> LongNovelProject:
    project = LongNovelProject(
        owner_id=user.id if user else None,
        title="未命名长篇小说",
        type="long_novel",
        genre_label=FAMILY_LONG_NOVEL_LABEL,
        premise=req.premise.strip(),
        family_setup=(req.family_setup or "").strip() or None,
        conflict_tags=req.conflict_tags,
        source_type=req.source_type,
        source_text=(req.source_text or "").strip() or None,
        target_words=req.target_words,
        target_chapters=req.target_chapters,
        generation_mode=req.generation_mode,
        style=req.style,
        forbidden_rules=(req.forbidden_rules or "").strip() or None,
        status=LongNovelProjectStatus.DRAFT,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def list_family_projects(
    db: Session,
    user: User,
    *,
    limit: int = 50,
    offset: int = 0,
) -> tuple[int, list[LongNovelProject]]:
    query = db.query(LongNovelProject).filter(LongNovelProject.owner_id == user.id)
    total = int(query.with_entities(func.count(LongNovelProject.id)).scalar() or 0)
    items = (
        query.order_by(LongNovelProject.updated_at.desc(), LongNovelProject.created_at.desc())
        .offset(max(0, offset))
        .limit(max(1, min(limit, 100)))
        .all()
    )
    return total, items


def _heal_completed_family_chapters(db: Session, project: LongNovelProject) -> None:
    completed_chapters = (
        db.query(LongNovelChapter)
        .filter(
            LongNovelChapter.project_id == project.id,
            LongNovelChapter.status == LongNovelChapterStatus.COMPLETED,
        )
        .order_by(LongNovelChapter.chapter_number.asc())
        .all()
    )
    for chapter in completed_chapters:
        plan = (
            db.query(LongNovelChapterPlan)
            .filter(
                LongNovelChapterPlan.project_id == project.id,
                LongNovelChapterPlan.chapter_number == chapter.chapter_number,
            )
            .first()
        )
        if plan is not None and plan.status != LongNovelChapterStatus.COMPLETED:
            plan.status = LongNovelChapterStatus.COMPLETED
            db.add(plan)
        if not (chapter.summary or "").strip():
            _ensure_chapter_state_floor(db, project, chapter)


def _close_stale_family_logs(
    db: Session,
    project: LongNovelProject,
    *,
    has_active_generation: bool,
) -> None:
    if has_active_generation:
        return
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    stale_logs = (
        db.query(LongNovelGenerationLog)
        .filter(
            LongNovelGenerationLog.project_id == project.id,
            LongNovelGenerationLog.status == "started",
            LongNovelGenerationLog.created_at < cutoff,
        )
        .all()
    )
    for log in stale_logs:
        log.status = "failed"
        log.error = "stale started log closed after generation state healed"
        db.add(log)


def sync_family_project_progress(db: Session, project: LongNovelProject) -> LongNovelProject:
    _heal_completed_family_chapters(db, project)
    total_words, current_chapter = (
        db.query(
            func.coalesce(func.sum(LongNovelChapter.word_count), 0),
            func.coalesce(func.max(LongNovelChapter.chapter_number), 0),
        )
        .filter(
            LongNovelChapter.project_id == project.id,
            LongNovelChapter.status == LongNovelChapterStatus.COMPLETED,
        )
        .one()
    )
    total_words = int(total_words or 0)
    current_chapter = int(current_chapter or 0)
    next_status = project.status
    has_generating_plan = (
        db.query(LongNovelChapterPlan.id)
        .filter(
            LongNovelChapterPlan.project_id == project.id,
            LongNovelChapterPlan.status == LongNovelChapterStatus.GENERATING,
        )
        .first()
        is not None
    )
    has_generating_chapter = (
        db.query(LongNovelChapter.id)
        .filter(
            LongNovelChapter.project_id == project.id,
            LongNovelChapter.status == LongNovelChapterStatus.GENERATING,
        )
        .first()
        is not None
    )
    _close_stale_family_logs(
        db,
        project,
        has_active_generation=has_generating_plan or has_generating_chapter,
    )
    if project.status == LongNovelProjectStatus.GENERATING:
        latest_log = (
            db.query(LongNovelGenerationLog)
            .filter(LongNovelGenerationLog.project_id == project.id)
            .order_by(LongNovelGenerationLog.created_at.desc())
            .first()
        )
        has_started_log = latest_log is not None and latest_log.status == "started"
        if not has_started_log and not has_generating_plan and not has_generating_chapter:
            next_status = (
                LongNovelProjectStatus.COMPLETED
                if current_chapter >= project.target_chapters and project.target_chapters > 0
                else LongNovelProjectStatus.ACTIVE
            )
    if (
        project.total_words == total_words
        and project.current_chapter == current_chapter
        and project.status == next_status
    ):
        return project
    project.total_words = total_words
    project.current_chapter = current_chapter
    project.status = next_status
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def generate_family_novel_bible(
    db: Session,
    project_id: UUID,
    user: User | None = None,
) -> LongNovelProject:
    project = _get_project(db, project_id, user)
    _mark_project_generating(db, project)
    prompt = render_prompt(
        ZHOULI_BIBLE_PROMPT,
        project_payload=_json_dumps(_project_payload(project)),
    )
    try:
        data = _call_json(
            db,
            project_id=project.id,
            chapter_number=None,
            action="generate_bible",
            tier="strong",
            prompt=prompt,
            max_output_tokens=24000,
            prompt_cache_key=_project_prompt_cache_key(project.id),
        )
    except Exception:
        _mark_generation_failed(db, project)
        raise

    project.title = _clean_title(_text(data, "title", default=project.title), project.title)
    project.status = LongNovelProjectStatus.ACTIVE
    bible = project.bible or LongNovelBible(project_id=project.id)
    bible.theme = _text(data, "theme")
    bible.core_promise = _text(data, "corePromise", "core_promise")
    bible.emotional_hook = _text(data, "emotionalHook", "emotional_hook")
    bible.family_background = _text(data, "familyBackground", "family_background")
    bible.main_conflict = _text(data, "mainConflict", "main_conflict")
    bible.ending_direction = _text(data, "endingDirection", "ending_direction")
    bible.world_rules = _as_list(_get(data, "worldRules", "world_rules"))
    bible.style_rules = _as_list(_get(data, "styleRules", "style_rules"))
    bible.forbidden_rules = _as_list(_get(data, "forbiddenRules", "forbidden_rules"))
    bible.raw_json = data
    db.add(project)
    db.add(bible)

    db.query(LongNovelFamilyMember).filter(LongNovelFamilyMember.project_id == project.id).delete()
    db.query(LongNovelFamilyRelation).filter(LongNovelFamilyRelation.project_id == project.id).delete()
    db.query(LongNovelConflictLine).filter(LongNovelConflictLine.project_id == project.id).delete()

    for item in _as_list(_get(data, "familyMembers", "family_members")):
        if not isinstance(item, dict):
            continue
        name = _text(item, "name")
        if not name:
            continue
        db.add(
            LongNovelFamilyMember(
                project_id=project.id,
                name=name,
                family_role=_text(item, "familyRole", "family_role"),
                public_image=_text(item, "publicImage", "public_image"),
                private_desire=_text(item, "privateDesire", "private_desire"),
                grievance=_text(item, "grievance"),
                leverage=_text(item, "leverage"),
                weakness=_text(item, "weakness"),
                secret=_text(item, "secret"),
                relationship_to_heroine=_text(item, "relationshipToHeroine", "relationship_to_heroine"),
                relationship_map=_as_dict(_get(item, "relationshipMap", "relationship_map")),
                voice_rules=_as_dict(_get(item, "voiceRules", "voice_rules")),
                arc_start=_text(item, "arcStart", "arc_start"),
                arc_end=_text(item, "arcEnd", "arc_end"),
                ending=_text(item, "ending"),
                raw_json=item,
            )
        )

    for item in _as_list(_get(data, "familyRelations", "family_relations")):
        if not isinstance(item, dict):
            continue
        from_member = _text(item, "fromMember", "from_member")
        to_member = _text(item, "toMember", "to_member")
        if not from_member or not to_member:
            continue
        db.add(
            LongNovelFamilyRelation(
                project_id=project.id,
                from_member=from_member,
                to_member=to_member,
                relation_type=_text(item, "relationType", "relation_type"),
                surface_relation=_text(item, "surfaceRelation", "surface_relation"),
                hidden_conflict=_text(item, "hiddenConflict", "hidden_conflict"),
                interest_conflict=_text(item, "interestConflict", "interest_conflict"),
                emotional_debt=_text(item, "emotionalDebt", "emotional_debt"),
                current_tension_level=_int_value(_get(item, "currentTensionLevel", "current_tension_level"), 1),
                raw_json=item,
            )
        )

    for item in _as_list(_get(data, "conflictLines", "conflict_lines")):
        if not isinstance(item, dict):
            continue
        conflict_key = _text(item, "conflictKey", "conflict_key") or _text(item, "name")
        name = _text(item, "name") or conflict_key
        if not conflict_key or not name:
            continue
        db.add(
            LongNovelConflictLine(
                project_id=project.id,
                conflict_key=conflict_key,
                name=name,
                conflict_type=_text(item, "conflictType", "conflict_type"),
                description=_text(item, "description"),
                introduced_chapter=_int_value(_get(item, "introducedChapter", "introduced_chapter"), 1),
                planned_explosion_chapter=_int_value(_get(item, "plannedExplosionChapter", "planned_explosion_chapter"), 20),
                planned_resolution_chapter=_int_value(_get(item, "plannedResolutionChapter", "planned_resolution_chapter"), 45),
                status="open",
                importance=_text(item, "importance", default="normal") or "normal",
            )
        )

    db.commit()
    db.refresh(project)
    return project


def _ensure_bible(db: Session, project: LongNovelProject, user: User | None) -> None:
    if project.bible is None:
        generate_family_novel_bible(db, project.id, user)
        db.refresh(project)


def _fallback_chapter_plan(project: LongNovelProject, chapter_number: int) -> dict[str, Any]:
    per_chapter = _chapter_target_words(project)
    return {
        "chapterNumber": chapter_number,
        "volumeNumber": max(1, (chapter_number - 1) // 12 + 1),
        "title": f"账要算清",
        "targetWords": per_chapter,
        "chapterGoal": "推进一条家庭利益矛盾，让女主更清楚自己在家中的处境。",
        "domesticConflict": "围绕钱、房子、老人或孩子产生具体争执。",
        "emotionalHook": "亲近的人用亲情要求女主退让。",
        "requiredEvents": ["一次具体家庭冲突", "一个信息或利益变化"],
        "characterArcMove": ["女主从忍让向清醒推进一步"],
        "conflictLinesToAdvance": [],
        "forbiddenReveals": [],
        "endingHook": "有人留下一个新的把柄或疑点。",
    }


def generate_family_novel_outline(
    db: Session,
    project_id: UUID,
    user: User | None = None,
) -> LongNovelProject:
    project = _get_project(db, project_id, user)
    _ensure_bible(db, project, user)
    db.refresh(project)
    prompt = render_prompt(
        ZHOULI_OUTLINE_PROMPT,
        project_payload=_json_dumps(_project_payload(project)),
        bible_json=_json_dumps(project.bible.raw_json if project.bible else {}),
    )
    _mark_project_generating(db, project)
    try:
        data = _call_json(
            db,
            project_id=project.id,
            chapter_number=None,
            action="generate_outline",
            tier="strong",
            prompt=prompt,
            max_output_tokens=_outline_max_output_tokens(project),
            prompt_cache_key=_project_prompt_cache_key(project.id),
        )
    except Exception:
        _mark_generation_failed(db, project)
        raise
    chapters = [item for item in _as_list(_get(data, "chapters")) if isinstance(item, dict)]
    by_number = {
        _int_value(_get(item, "chapterNumber", "chapter_number"), 0): item
        for item in chapters
    }
    target = max(1, min(80, project.target_chapters or 50))
    uniform_target_words = _chapter_target_words(project)
    for number in range(1, target + 1):
        item = by_number.get(number) or _fallback_chapter_plan(project, number)
        title = _clean_title(_text(item, "title"), f"第{number}章")
        plan = (
            db.query(LongNovelChapterPlan)
            .filter(
                LongNovelChapterPlan.project_id == project.id,
                LongNovelChapterPlan.chapter_number == number,
            )
            .first()
            or LongNovelChapterPlan(project_id=project.id, chapter_number=number)
        )
        plan.volume_number = _int_value(_get(item, "volumeNumber", "volume_number"), max(1, (number - 1) // 12 + 1))
        plan.title = title
        plan.target_words = uniform_target_words
        plan.chapter_goal = _text(item, "chapterGoal", "chapter_goal")
        plan.domestic_conflict = _text(item, "domesticConflict", "domestic_conflict")
        plan.emotional_hook = _text(item, "emotionalHook", "emotional_hook")
        plan.required_events = _as_list(_get(item, "requiredEvents", "required_events"))
        plan.character_arc_move = _as_list(_get(item, "characterArcMove", "character_arc_move"))
        plan.conflict_lines_to_advance = _as_list(_get(item, "conflictLinesToAdvance", "conflict_lines_to_advance"))
        plan.forbidden_reveals = _as_list(_get(item, "forbiddenReveals", "forbidden_reveals"))
        plan.ending_hook = _text(item, "endingHook", "ending_hook")
        plan.raw_json = {**item, "targetWords": uniform_target_words}
        plan.status = LongNovelChapterStatus.PLANNED
        db.add(plan)
    project.status = LongNovelProjectStatus.ACTIVE
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


_SISTER_ACCOUNT_EXPANSION_TITLES = {"嫂子的账本", "嫂子的帐本"}


def _is_sister_account_project(project: LongNovelProject) -> bool:
    title = (project.title or "").strip()
    return any(name in title for name in _SISTER_ACCOUNT_EXPANSION_TITLES)


def _completed_chapter_payloads(db: Session, project: LongNovelProject) -> list[dict[str, Any]]:
    chapters = (
        db.query(LongNovelChapter)
        .filter(
            LongNovelChapter.project_id == project.id,
            LongNovelChapter.status == LongNovelChapterStatus.COMPLETED,
        )
        .order_by(LongNovelChapter.chapter_number.asc())
        .all()
    )
    payloads: list[dict[str, Any]] = []
    for chapter in chapters:
        _ensure_chapter_state_floor(db, project, chapter)
        payloads.append(
            {
                "chapterNumber": chapter.chapter_number,
                "title": chapter.title,
                "summary": _chapter_summary(chapter),
                "endingHook": _chapter_ending_hook(db, project.id, chapter.chapter_number, chapter),
                "wordCount": chapter.word_count,
                "last600Chars": _clip_text(chapter.content, 600),
            }
        )
    db.flush()
    return payloads


def _family_members_payload(project: LongNovelProject) -> list[dict[str, Any]]:
    return [
        {
            "name": row.name,
            "familyRole": row.family_role,
            "publicImage": row.public_image,
            "privateDesire": row.private_desire,
            "grievance": row.grievance,
            "leverage": row.leverage,
            "weakness": row.weakness,
            "secret": row.secret,
            "relationshipToHeroine": row.relationship_to_heroine,
            "arcStart": row.arc_start,
            "arcEnd": row.arc_end,
            "ending": row.ending,
        }
        for row in project.family_members
    ]


def _family_relations_payload(project: LongNovelProject) -> list[dict[str, Any]]:
    return [
        {
            "fromMember": row.from_member,
            "toMember": row.to_member,
            "relationType": row.relation_type,
            "surfaceRelation": row.surface_relation,
            "hiddenConflict": row.hidden_conflict,
            "interestConflict": row.interest_conflict,
            "emotionalDebt": row.emotional_debt,
            "currentTensionLevel": row.current_tension_level,
        }
        for row in project.family_relations
    ]


def _continuation_replan_context(
    db: Session,
    project: LongNovelProject,
    *,
    current_chapter: int,
    target_words: int,
    target_chapters: int,
    target_words_per_chapter: int,
    expansion_notes: str | None,
) -> dict[str, Any]:
    next_chapter = current_chapter + 1
    completed = _completed_chapter_payloads(db, project)
    canon_facts = (
        db.query(LongNovelCanonFact)
        .filter(LongNovelCanonFact.project_id == project.id, LongNovelCanonFact.chapter_number <= current_chapter)
        .order_by(LongNovelCanonFact.chapter_number.asc(), LongNovelCanonFact.id.asc())
        .limit(160)
        .all()
    )
    open_issues = (
        db.query(LongNovelContinuityIssue)
        .filter(
            LongNovelContinuityIssue.project_id == project.id,
            LongNovelContinuityIssue.status == "open",
        )
        .order_by(LongNovelContinuityIssue.chapter_number.asc(), LongNovelContinuityIssue.created_at.asc())
        .limit(80)
        .all()
    )
    return {
        "project": {
            **_project_payload(project),
            "targetWords": target_words,
            "targetChapters": target_chapters,
            "averageChapterTargetWords": target_words_per_chapter,
            "completedChapterCount": current_chapter,
            "completedWords": int(project.total_words or 0),
            "futureStartChapter": next_chapter,
            "futureEndChapter": target_chapters,
            "futureChapterCount": max(0, target_chapters - current_chapter),
        },
        "instruction": {
            "scope": f"只重规划第 {next_chapter}-{target_chapters} 章，不改第 1-{current_chapter} 章正文。",
            "mustPreserve": [
                "已完成正文",
                "已完成章节摘要",
                "已登记人物状态",
                "已登记物品状态",
                "已登记事实",
                "已存在矛盾线",
                "已存在家庭关系",
            ],
            "expansionNotes": (expansion_notes or "").strip(),
        },
        "currentBible": project.bible.raw_json if project.bible else {},
        "completedChapters": completed,
        "recentCompletedChapters": completed[-5:],
        "familyMembers": _family_members_payload(project),
        "familyRelations": _family_relations_payload(project),
        "activeConflictLines": _active_conflict_payloads(db, project.id),
        "latestCharacterStates": _latest_states(db, project.id, next_chapter, project=project),
        "latestObjectStates": _latest_object_states(db, project.id, next_chapter),
        "canonFacts": [
            {
                "chapterNumber": row.chapter_number,
                "fact": row.fact,
                "factType": row.fact_type,
                "entities": row.entities,
                "confidence": row.confidence,
            }
            for row in canon_facts
        ],
        "openContinuityIssues": [
            {
                "chapterNumber": row.chapter_number,
                "severity": row.severity,
                "issueType": row.issue_type,
                "description": row.description,
                "evidence": row.evidence,
                "suggestedFix": row.suggested_fix,
            }
            for row in open_issues
        ],
    }


def _continuation_replan_label(target_words: int | None = None) -> str:
    if not target_words:
        return "续规划"
    if target_words >= 10000:
        return f"{max(1, int(round(target_words / 10000)))}万续规划"
    return f"{target_words}字续规划"


def _validate_continuation_replan(
    data: dict[str, Any],
    *,
    current_chapter: int,
    target_chapters: int,
    target_words: int | None = None,
) -> None:
    label = _continuation_replan_label(target_words)
    expected_numbers = set(range(current_chapter + 1, target_chapters + 1))
    chapters = [item for item in _as_list(_get(data, "chapters")) if isinstance(item, dict)]
    numbers = [_int_value(_get(item, "chapterNumber", "chapter_number"), 0) for item in chapters]
    got_numbers = {number for number in numbers if number > 0}
    missing = sorted(expected_numbers - got_numbers)
    extra = sorted(got_numbers - expected_numbers)
    duplicate_count = len(numbers) - len(got_numbers)
    if missing or extra or duplicate_count:
        raise FamilyLongNovelError(
            f"{label}章节覆盖失败：missing={missing[:8]} extra={extra[:8]} duplicates={duplicate_count}"
        )
    bible_patch = _as_dict(_get(data, "biblePatch", "bible_patch"))
    if not bible_patch:
        raise FamilyLongNovelError(f"{label}缺少 biblePatch，不能升级作品圣经")
    conflicts = [item for item in _as_list(_get(data, "conflictLines", "conflict_lines")) if isinstance(item, dict)]
    if len(conflicts) < 6:
        raise FamilyLongNovelError(f"{label}矛盾线过少，不能支撑长篇扩容")
    future_count = max(1, target_chapters - current_chapter)
    unique_conflicts = {
        _text(item, "domesticConflict", "domestic_conflict")
        for item in chapters
        if _text(item, "domesticConflict", "domestic_conflict")
    }
    unique_hooks = {
        _text(item, "endingHook", "ending_hook")
        for item in chapters
        if _text(item, "endingHook", "ending_hook")
    }
    minimum_unique = min(future_count, max(12, int(future_count * 0.65)))
    if len(unique_conflicts) < minimum_unique or len(unique_hooks) < minimum_unique:
        raise FamilyLongNovelError(
            f"{label}重复度过高：unique_conflicts={len(unique_conflicts)} unique_hooks={len(unique_hooks)} required={minimum_unique}"
        )


def _validate_global_continuation_replan(data: dict[str, Any], *, target_words: int | None = None) -> None:
    label = _continuation_replan_label(target_words)
    bible_patch = _as_dict(_get(data, "biblePatch", "bible_patch"))
    if not bible_patch:
        raise FamilyLongNovelError(f"{label}全局规划缺少 biblePatch")
    conflicts = [item for item in _as_list(_get(data, "conflictLines", "conflict_lines")) if isinstance(item, dict)]
    if len(conflicts) < 6:
        raise FamilyLongNovelError(f"{label}全局规划矛盾线过少，不能支撑后续分章")


def _validate_chapter_batch_replan(
    data: dict[str, Any],
    *,
    batch_start: int,
    batch_end: int,
    target_words: int | None = None,
) -> list[dict[str, Any]]:
    label = _continuation_replan_label(target_words)
    chapters = [item for item in _as_list(_get(data, "chapters")) if isinstance(item, dict)]
    expected = set(range(batch_start, batch_end + 1))
    numbers = [_int_value(_get(item, "chapterNumber", "chapter_number"), 0) for item in chapters]
    got = {number for number in numbers if number > 0}
    missing = sorted(expected - got)
    extra = sorted(got - expected)
    duplicate_count = len(numbers) - len(got)
    if missing or extra or duplicate_count:
        raise FamilyLongNovelError(
            f"{label}分批章节规划覆盖失败：batch={batch_start}-{batch_end} missing={missing} extra={extra} duplicates={duplicate_count}"
        )
    for item in chapters:
        number = _int_value(_get(item, "chapterNumber", "chapter_number"), 0)
        if not _text(item, "title"):
            raise FamilyLongNovelError(f"{label}分批章节规划第 {number} 章缺少标题")
        if not _text(item, "chapterGoal", "chapter_goal"):
            raise FamilyLongNovelError(f"{label}分批章节规划第 {number} 章缺少 chapterGoal")
        if not _text(item, "domesticConflict", "domestic_conflict"):
            raise FamilyLongNovelError(f"{label}分批章节规划第 {number} 章缺少 domesticConflict")
        if not _text(item, "endingHook", "ending_hook"):
            raise FamilyLongNovelError(f"{label}分批章节规划第 {number} 章缺少 endingHook")
    return sorted(chapters, key=lambda item: _int_value(_get(item, "chapterNumber", "chapter_number"), 0))


def _continuation_batch_context(
    base_context: dict[str, Any],
    global_plan: dict[str, Any],
    *,
    batch_start: int,
    batch_end: int,
    planned_chapters: list[dict[str, Any]],
) -> dict[str, Any]:
    previous_future = [
        {
            "chapterNumber": _int_value(_get(item, "chapterNumber", "chapter_number"), 0),
            "title": _text(item, "title"),
            "chapterGoal": _text(item, "chapterGoal", "chapter_goal"),
            "domesticConflict": _text(item, "domesticConflict", "domestic_conflict"),
            "endingHook": _text(item, "endingHook", "ending_hook"),
        }
        for item in planned_chapters[-8:]
    ]
    planned_briefs = [
        {
            "chapterNumber": _int_value(_get(item, "chapterNumber", "chapter_number"), 0),
            "title": _text(item, "title"),
            "domesticConflict": _text(item, "domesticConflict", "domestic_conflict"),
            "endingHook": _text(item, "endingHook", "ending_hook"),
        }
        for item in planned_chapters
    ]
    return {
        "project": base_context.get("project", {}),
        "instruction": base_context.get("instruction", {}),
        "completedChapters": base_context.get("completedChapters", []),
        "recentCompletedChapters": base_context.get("recentCompletedChapters", []),
        "latestCharacterStates": base_context.get("latestCharacterStates", []),
        "latestObjectStates": base_context.get("latestObjectStates", []),
        "openContinuityIssues": base_context.get("openContinuityIssues", []),
        "globalBiblePatch": _as_dict(_get(global_plan, "biblePatch", "bible_patch")),
        "globalContinuationStrategy": _as_dict(_get(global_plan, "continuationStrategy", "continuation_strategy")),
        "globalConflictLines": _as_list(_get(global_plan, "conflictLines", "conflict_lines")),
        "familyMembers": _as_list(_get(global_plan, "familyMembers", "family_members")) or base_context.get("familyMembers", []),
        "familyRelations": _as_list(_get(global_plan, "familyRelations", "family_relations")) or base_context.get("familyRelations", []),
        "previousFutureChapters": previous_future,
        "allPlannedFutureBriefs": planned_briefs,
        "batchStart": batch_start,
        "batchEnd": batch_end,
        "expectedChapterNumbers": list(range(batch_start, batch_end + 1)),
    }


def _run_batched_continuation_replan(
    db: Session,
    project: LongNovelProject,
    *,
    base_context: dict[str, Any],
    current_chapter: int,
    target_chapters: int,
    target_words: int,
) -> dict[str, Any]:
    global_prompt = render_prompt(
        ZHOULI_30W_GLOBAL_REPLAN_PROMPT,
        expansion_context=_json_dumps(base_context),
    )
    global_plan = _call_json(
        db,
        project_id=project.id,
        chapter_number=None,
        action="replan_target_global",
        tier="strong",
        prompt=global_prompt,
        max_output_tokens=20000,
        prompt_cache_key=_project_prompt_cache_key(project.id),
    )
    _validate_global_continuation_replan(global_plan, target_words=target_words)

    planned_chapters: list[dict[str, Any]] = []
    batch_size = 3
    for batch_start in range(current_chapter + 1, target_chapters + 1, batch_size):
        batch_end = min(target_chapters, batch_start + batch_size - 1)
        batch_context = _continuation_batch_context(
            base_context,
            global_plan,
            batch_start=batch_start,
            batch_end=batch_end,
            planned_chapters=planned_chapters,
        )
        batch_prompt = render_prompt(
            ZHOULI_30W_CHAPTER_BATCH_REPLAN_PROMPT,
            batch_context=_json_dumps(batch_context),
        )
        batch_data = _call_json(
            db,
            project_id=project.id,
            chapter_number=None,
            action=f"replan_target_batch_{batch_start}_{batch_end}",
            tier="strong",
            prompt=batch_prompt,
            max_output_tokens=12000,
            prompt_cache_key=_project_prompt_cache_key(project.id),
        )
        try:
            planned_chapters.extend(
                _validate_chapter_batch_replan(
                    batch_data,
                    batch_start=batch_start,
                    batch_end=batch_end,
                    target_words=target_words,
                )
            )
        except FamilyLongNovelError:
            if batch_start == batch_end:
                raise
            for single_number in range(batch_start, batch_end + 1):
                single_context = _continuation_batch_context(
                    base_context,
                    global_plan,
                    batch_start=single_number,
                    batch_end=single_number,
                    planned_chapters=planned_chapters,
                )
                single_prompt = render_prompt(
                    ZHOULI_30W_CHAPTER_BATCH_REPLAN_PROMPT,
                    batch_context=_json_dumps(single_context),
                )
                single_data = _call_json(
                    db,
                    project_id=project.id,
                    chapter_number=None,
                    action=f"replan_target_batch_{single_number}_{single_number}",
                    tier="strong",
                    prompt=single_prompt,
                    max_output_tokens=6000,
                    prompt_cache_key=_project_prompt_cache_key(project.id),
                )
                planned_chapters.extend(
                    _validate_chapter_batch_replan(
                        single_data,
                        batch_start=single_number,
                        batch_end=single_number,
                        target_words=target_words,
                    )
                )

    combined = dict(global_plan)
    combined["chapters"] = planned_chapters
    _validate_continuation_replan(
        combined,
        current_chapter=current_chapter,
        target_chapters=target_chapters,
        target_words=target_words,
    )
    return combined


def _apply_bible_patch(project: LongNovelProject, data: dict[str, Any], *, current_chapter: int, target_words: int, target_chapters: int) -> None:
    patch = _as_dict(_get(data, "biblePatch", "bible_patch"))
    bible = project.bible or LongNovelBible(project_id=project.id)
    bible.theme = _text(patch, "theme", default=bible.theme or "")
    bible.core_promise = _text(patch, "corePromise", "core_promise", default=bible.core_promise or "")
    bible.emotional_hook = _text(patch, "emotionalHook", "emotional_hook", default=bible.emotional_hook or "")
    bible.family_background = _text(patch, "familyBackground", "family_background", default=bible.family_background or "")
    bible.main_conflict = _text(patch, "mainConflict", "main_conflict", default=bible.main_conflict or "")
    bible.ending_direction = _text(patch, "endingDirection", "ending_direction", default=bible.ending_direction or "")
    bible.world_rules = _as_list(_get(patch, "worldRules", "world_rules")) or bible.world_rules
    bible.style_rules = _as_list(_get(patch, "styleRules", "style_rules")) or bible.style_rules
    bible.forbidden_rules = _as_list(_get(patch, "forbiddenRules", "forbidden_rules")) or bible.forbidden_rules
    raw = dict(bible.raw_json or {})
    raw.update({key: value for key, value in patch.items() if value not in (None, "", [], {})})
    raw["targetWordExpansion"] = {
        **_as_dict(_get(patch, "targetExpansion", "target_expansion")),
        "mode": "system_replan_target_continuation",
        "lockedCompletedChapters": current_chapter,
        "targetWords": target_words,
        "targetChapters": target_chapters,
    }
    bible.raw_json = raw
    project.bible = bible


def _upsert_family_members_from_replan(db: Session, project: LongNovelProject, data: dict[str, Any]) -> None:
    existing = {row.name: row for row in project.family_members}
    for item in [row for row in _as_list(_get(data, "familyMembers", "family_members")) if isinstance(row, dict)]:
        name = _text(item, "name")
        if not name:
            continue
        row = existing.get(name) or LongNovelFamilyMember(project_id=project.id, name=name)
        row.family_role = _text(item, "familyRole", "family_role", default=row.family_role or "")
        row.public_image = _text(item, "publicImage", "public_image", default=row.public_image or "")
        row.private_desire = _text(item, "privateDesire", "private_desire", default=row.private_desire or "")
        row.grievance = _text(item, "grievance", default=row.grievance or "")
        row.leverage = _text(item, "leverage", default=row.leverage or "")
        row.weakness = _text(item, "weakness", default=row.weakness or "")
        row.secret = _text(item, "secret", REMOVED_PRIVATE_CREDENTIAL or "")
        row.relationship_to_heroine = _text(item, "relationshipToHeroine", "relationship_to_heroine", default=row.relationship_to_heroine or "")
        row.arc_start = _text(item, "arcStart", "arc_start", default=row.arc_start or "")
        row.arc_end = _text(item, "arcEnd", "arc_end", default=row.arc_end or "")
        row.ending = _text(item, "ending", default=row.ending or "")
        row.raw_json = {**(row.raw_json or {}), **item}
        db.add(row)


def _upsert_family_relations_from_replan(db: Session, project: LongNovelProject, data: dict[str, Any]) -> None:
    existing = {(row.from_member, row.to_member, row.relation_type or ""): row for row in project.family_relations}
    for item in [row for row in _as_list(_get(data, "familyRelations", "family_relations")) if isinstance(row, dict)]:
        from_member = _text(item, "fromMember", "from_member")
        to_member = _text(item, "toMember", "to_member")
        if not from_member or not to_member:
            continue
        relation_type = _text(item, "relationType", "relation_type")
        key = (from_member, to_member, relation_type or "")
        row = existing.get(key) or LongNovelFamilyRelation(project_id=project.id, from_member=from_member, to_member=to_member)
        row.relation_type = relation_type or row.relation_type
        row.surface_relation = _text(item, "surfaceRelation", "surface_relation", default=row.surface_relation or "")
        row.hidden_conflict = _text(item, "hiddenConflict", "hidden_conflict", default=row.hidden_conflict or "")
        row.interest_conflict = _text(item, "interestConflict", "interest_conflict", default=row.interest_conflict or "")
        row.emotional_debt = _text(item, "emotionalDebt", "emotional_debt", default=row.emotional_debt or "")
        row.current_tension_level = _int_value(_get(item, "currentTensionLevel", "current_tension_level"), row.current_tension_level or 1)
        row.raw_json = {**(row.raw_json or {}), **item}
        db.add(row)


def _upsert_conflicts_from_replan(db: Session, project: LongNovelProject, data: dict[str, Any], *, current_chapter: int, target_chapters: int) -> None:
    existing = {row.conflict_key: row for row in project.conflict_lines}
    for item in [row for row in _as_list(_get(data, "conflictLines", "conflict_lines")) if isinstance(row, dict)]:
        key = _text(item, "conflictKey", "conflict_key") or _text(item, "name")
        name = _text(item, "name") or key
        if not key or not name:
            continue
        row = existing.get(key) or LongNovelConflictLine(project_id=project.id, conflict_key=key, name=name)
        row.name = name
        row.conflict_type = _text(item, "conflictType", "conflict_type", default=row.conflict_type or "")
        row.description = _text(item, "description", default=row.description or "")
        row.introduced_chapter = min(target_chapters, max(1, _int_value(_get(item, "introducedChapter", "introduced_chapter"), row.introduced_chapter or 1)))
        explosion = _int_value(_get(item, "plannedExplosionChapter", "planned_explosion_chapter"), row.planned_explosion_chapter or current_chapter + 5)
        resolution = _int_value(_get(item, "plannedResolutionChapter", "planned_resolution_chapter"), row.planned_resolution_chapter or target_chapters)
        row.planned_explosion_chapter = min(target_chapters, max(current_chapter + 1, explosion))
        row.planned_resolution_chapter = min(target_chapters, max(row.planned_explosion_chapter, resolution))
        row.status = _text(item, "status", default=row.status or "open") or "open"
        row.importance = _text(item, "importance", default=row.importance or "normal") or "normal"
        db.add(row)


def _write_future_plans_from_replan(
    db: Session,
    project: LongNovelProject,
    data: dict[str, Any],
    *,
    current_chapter: int,
    target_chapters: int,
    target_words_per_chapter: int,
) -> None:
    db.query(LongNovelChapterPlan).filter(
        LongNovelChapterPlan.project_id == project.id,
        LongNovelChapterPlan.chapter_number > current_chapter,
    ).delete(synchronize_session=False)
    by_number = {
        _int_value(_get(item, "chapterNumber", "chapter_number"), 0): item
        for item in _as_list(_get(data, "chapters"))
        if isinstance(item, dict)
    }
    for number in range(current_chapter + 1, target_chapters + 1):
        item = by_number[number]
        plan = LongNovelChapterPlan(project_id=project.id, chapter_number=number)
        plan.volume_number = _int_value(_get(item, "volumeNumber", "volume_number"), max(1, (number - 1) // 12 + 1))
        plan.title = _clean_title(_text(item, "title"), f"第{number}章")
        plan.target_words = target_words_per_chapter
        plan.chapter_goal = _text(item, "chapterGoal", "chapter_goal")
        plan.domestic_conflict = _text(item, "domesticConflict", "domestic_conflict")
        plan.emotional_hook = _text(item, "emotionalHook", "emotional_hook")
        plan.required_events = _as_list(_get(item, "requiredEvents", "required_events"))
        plan.character_arc_move = _as_list(_get(item, "characterArcMove", "character_arc_move"))
        plan.conflict_lines_to_advance = _as_list(_get(item, "conflictLinesToAdvance", "conflict_lines_to_advance"))
        plan.forbidden_reveals = _as_list(_get(item, "forbiddenReveals", "forbidden_reveals"))
        plan.ending_hook = _text(item, "endingHook", "ending_hook")
        plan.raw_json = {
            **item,
            "targetWords": target_words_per_chapter,
            "extensionMode": "system_replan_target_continuation",
            "lockedCompletedChapters": current_chapter,
        }
        plan.status = LongNovelChapterStatus.PLANNED
        db.add(plan)


def extend_family_project_to_target(
    db: Session,
    project_id: UUID,
    user: User | None = None,
    *,
    target_words: int = 200000,
    future_chapter_words: int = 4000,
    expansion_notes: str | None = None,
) -> LongNovelProject:
    """Extend one family project by rebuilding only future chapter plans.

    Completed chapters and extracted canon/state ledgers are intentionally left
    untouched. This is a project-plan expansion, not a manuscript rewrite.
    """

    project = _get_project(db, project_id, user)
    project = sync_family_project_progress(db, project)
    if not _is_sister_account_project(project):
        raise FamilyLongNovelError("目标字数续规划目前只允许用于《嫂子的账本》这个项目，避免误伤其它长篇")
    current_chapter = max(0, int(project.current_chapter or 0))
    current_words = max(0, int(project.total_words or 0))
    if current_chapter <= 0:
        raise FamilyLongNovelError("项目还没有已完成章节，不能做承接式目标字数续规划")
    requested_target_words = min(500000, max(1000, int(target_words or 200000)))
    target_words = max(current_words + 1, requested_target_words)
    future_chapter_words = max(1200, min(12000, int(future_chapter_words or 4000)))
    remaining_words = max(0, target_words - current_words)
    future_count = max(1, (remaining_words + future_chapter_words - 1) // future_chapter_words)
    target_chapters = min(80, current_chapter + future_count)
    if target_chapters <= current_chapter:
        target_chapters = min(80, current_chapter + 1)
    if target_chapters <= current_chapter:
        raise FamilyLongNovelError("当前项目已达到最大章节数，无法继续扩容")
    actual_future_count = max(1, target_chapters - current_chapter)
    actual_future_words = max(1200, int(round(remaining_words / actual_future_count)))

    project.target_words = target_words
    project.target_chapters = target_chapters
    db.add(project)
    db.flush()

    context = _continuation_replan_context(
        db,
        project,
        current_chapter=current_chapter,
        target_words=target_words,
        target_chapters=target_chapters,
        target_words_per_chapter=actual_future_words,
        expansion_notes=expansion_notes,
    )
    _mark_project_generating(db, project)
    try:
        data = _run_batched_continuation_replan(
            db,
            project,
            base_context=context,
            current_chapter=current_chapter,
            target_chapters=target_chapters,
            target_words=target_words,
        )
    except Exception as exc:
        db.add(
            LongNovelGenerationLog(
                project_id=project.id,
                chapter_number=None,
                action="extend_target",
                provider=settings.WRITER_PROVIDER,
                model=_model_for_tier("strong"),
                prompt_tokens=0,
                completion_tokens=0,
                estimated_cost=0.0,
                status="failed",
                error=str(exc)[:2000],
            )
        )
        _mark_generation_failed(db, project)
        raise

    _apply_bible_patch(project, data, current_chapter=current_chapter, target_words=target_words, target_chapters=target_chapters)
    db.add(project.bible)
    _upsert_family_members_from_replan(db, project, data)
    _upsert_family_relations_from_replan(db, project, data)
    _upsert_conflicts_from_replan(db, project, data, current_chapter=current_chapter, target_chapters=target_chapters)
    _write_future_plans_from_replan(
        db,
        project,
        data,
        current_chapter=current_chapter,
        target_chapters=target_chapters,
        target_words_per_chapter=actual_future_words,
    )
    if project.status not in {LongNovelProjectStatus.PAUSED, LongNovelProjectStatus.GENERATING}:
        project.status = LongNovelProjectStatus.ACTIVE
    else:
        project.status = LongNovelProjectStatus.ACTIVE
    db.add(project)
    db.add(
        LongNovelGenerationLog(
            project_id=project.id,
            chapter_number=None,
            action="extend_target",
            provider=settings.WRITER_PROVIDER,
            model=_model_for_tier("strong"),
            prompt_tokens=0,
            completion_tokens=0,
            estimated_cost=0.0,
            status="completed",
            error=f"system_replan_target_continuation target_words={target_words}; requested_target_words={requested_target_words}; target_chapters={target_chapters}; current_chapter={current_chapter}",
        )
    )
    db.commit()
    db.refresh(project)
    return project


def _clip_text(text: str | None, limit: int) -> str:
    value = (text or "").strip()
    return value if len(value) <= limit else value[-limit:]


def _initial_character_state(member: LongNovelFamilyMember) -> dict[str, Any]:
    return {
        "memberName": member.name,
        "chapterNumber": 0,
        "sourceChapterNumber": 0,
        "source": "bible",
        "goal": member.private_desire or member.arc_start or "",
        "emotion": member.public_image or "",
        "grievanceLevel": 1,
        "knowledge": [],
        "secrets": [member.secret] if member.secret else [],
        "currentLeverage": member.leverage or "",
        "relationshipTensionDelta": member.relationship_map or {},
        "raw": member.raw_json or {},
    }


def _latest_states(
    db: Session,
    project_id: UUID,
    chapter_number: int,
    *,
    project: LongNovelProject | None = None,
) -> list[dict[str, Any]]:
    rows = (
        db.query(LongNovelCharacterState)
        .filter(
            LongNovelCharacterState.project_id == project_id,
            LongNovelCharacterState.chapter_number < chapter_number,
        )
        .order_by(LongNovelCharacterState.member_name.asc(), LongNovelCharacterState.chapter_number.desc())
        .all()
    )
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        if row.member_name in seen:
            continue
        seen.add(row.member_name)
        result.append(
            {
                "memberName": row.member_name,
                "chapterNumber": row.chapter_number,
                "sourceChapterNumber": row.chapter_number,
                "source": "state_extract",
                "goal": row.goal,
                "emotion": row.emotion,
                "grievanceLevel": row.grievance_level,
                "knowledge": row.knowledge,
                "secrets": row.secrets,
                "currentLeverage": row.current_leverage,
                "relationshipTensionDelta": row.tension_with_others,
                "raw": row.raw_json or {},
            }
        )
    if project is not None:
        for member in project.family_members:
            if member.name and member.name not in seen:
                seen.add(member.name)
                result.append(_initial_character_state(member))
    return result


def _latest_object_states(db: Session, project_id: UUID, chapter_number: int) -> list[dict[str, Any]]:
    rows = (
        db.query(LongNovelObjectState)
        .filter(
            LongNovelObjectState.project_id == project_id,
            LongNovelObjectState.chapter_number < chapter_number,
        )
        .order_by(LongNovelObjectState.object_name.asc(), LongNovelObjectState.chapter_number.desc())
        .all()
    )
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        if row.object_name in seen:
            continue
        seen.add(row.object_name)
        result.append(
            {
                "objectName": row.object_name,
                "chapterNumber": row.chapter_number,
                "sourceChapterNumber": row.chapter_number,
                "holder": row.holder,
                "location": row.location,
                "knownBy": row.known_by,
                "status": row.status,
            }
        )
    return result


def _plan_payload(plan: LongNovelChapterPlan | None) -> dict[str, Any] | None:
    if plan is None:
        return None
    return {
        "chapterNumber": plan.chapter_number,
        "volumeNumber": plan.volume_number,
        "title": plan.title,
        "targetWords": plan.target_words,
        "chapterGoal": plan.chapter_goal,
        "domesticConflict": plan.domestic_conflict,
        "emotionalHook": plan.emotional_hook,
        "requiredEvents": plan.required_events,
        "characterArcMove": plan.character_arc_move,
        "conflictLinesToAdvance": plan.conflict_lines_to_advance,
        "forbiddenReveals": plan.forbidden_reveals,
        "endingHook": plan.ending_hook,
        "scenes": plan.scenes,
        "raw": plan.raw_json or {},
    }


def _chapter_summary(chapter: LongNovelChapter | None) -> str:
    if chapter is None:
        return ""
    return (chapter.summary or _clip_text(chapter.content, 240)).strip()


def _raw_nested_text(raw: dict[str, Any] | None, *keys: str) -> str:
    if not isinstance(raw, dict):
        return ""
    for section in ("stateExtract", "chapterPlan", "raw", ""):
        data: Any = raw
        if section:
            data = raw.get(section)
        if isinstance(data, dict):
            value = _text(data, *keys)
            if value:
                return value
    return ""


def _chapter_ending_hook(
    db: Session,
    project_id: UUID,
    chapter_number: int,
    chapter: LongNovelChapter | None = None,
) -> str:
    plan = (
        db.query(LongNovelChapterPlan)
        .filter(
            LongNovelChapterPlan.project_id == project_id,
            LongNovelChapterPlan.chapter_number == chapter_number,
        )
        .first()
    )
    if plan:
        hook = (plan.ending_hook or _raw_nested_text(plan.raw_json, "endingHook", "ending_hook")).strip()
        if hook:
            return hook
    if chapter is None:
        chapter = (
            db.query(LongNovelChapter)
            .filter(
                LongNovelChapter.project_id == project_id,
                LongNovelChapter.chapter_number == chapter_number,
            )
            .first()
        )
    summary = _chapter_summary(chapter)
    if summary:
        return summary[-180:]
    return _clip_text(chapter.content if chapter else "", 240)


def _conflict_latest_update(
    db: Session,
    project_id: UUID,
    line: LongNovelConflictLine,
) -> str:
    chapter_number = line.last_escalated_chapter or line.introduced_chapter
    if not chapter_number:
        return (line.description or "").strip()
    chapter = (
        db.query(LongNovelChapter)
        .filter(
            LongNovelChapter.project_id == project_id,
            LongNovelChapter.chapter_number == chapter_number,
        )
        .first()
    )
    plan = (
        db.query(LongNovelChapterPlan)
        .filter(
            LongNovelChapterPlan.project_id == project_id,
            LongNovelChapterPlan.chapter_number == chapter_number,
        )
        .first()
    )
    update = (plan.domestic_conflict if plan else "") or _chapter_summary(chapter) or line.description or ""
    return f"第{chapter_number}章推进：{update}".strip()


def _ensure_chapter_state_floor(
    db: Session,
    project: LongNovelProject,
    chapter: LongNovelChapter,
    *,
    extracted: dict[str, Any] | None = None,
) -> None:
    summary = (chapter.summary or "").strip()
    if not summary:
        summary = (chapter.content or "").strip()[:240] or f"第{chapter.chapter_number}章已生成"
        chapter.summary = summary
        db.add(chapter)

    plan = (
        db.query(LongNovelChapterPlan)
        .filter(
            LongNovelChapterPlan.project_id == project.id,
            LongNovelChapterPlan.chapter_number == chapter.chapter_number,
        )
        .first()
    )
    if plan is not None and extracted is not None:
        ending_hook = _text(extracted, "endingHook", "ending_hook")
        if ending_hook:
            plan.ending_hook = ending_hook
        plan.raw_json = {**(plan.raw_json or {}), "stateExtract": extracted}
        db.add(plan)

    has_fact = (
        db.query(LongNovelCanonFact.id)
        .filter(
            LongNovelCanonFact.project_id == project.id,
            LongNovelCanonFact.chapter_number == chapter.chapter_number,
        )
        .first()
        is not None
    )
    if not has_fact:
        db.add(
            LongNovelCanonFact(
                project_id=project.id,
                chapter_number=chapter.chapter_number,
                fact=summary,
                fact_type="chapter_summary",
                entities=[member.name for member in project.family_members if member.name][:6],
                source=f"chapter:{chapter.chapter_number}:fallback",
                confidence=0.8,
            )
        )

    existing_members = {
        row.member_name
        for row in db.query(LongNovelCharacterState.member_name)
        .filter(
            LongNovelCharacterState.project_id == project.id,
            LongNovelCharacterState.chapter_number == chapter.chapter_number,
        )
        .all()
    }
    previous_states = {
        item["memberName"]: item
        for item in _latest_states(db, project.id, chapter.chapter_number, project=project)
        if item.get("memberName")
    }
    for member in project.family_members:
        if not member.name or member.name in existing_members:
            continue
        previous = previous_states.get(member.name) or _initial_character_state(member)
        db.add(
            LongNovelCharacterState(
                project_id=project.id,
                chapter_number=chapter.chapter_number,
                member_name=member.name,
                goal=previous.get("goal") or member.private_desire or "",
                emotion=previous.get("emotion") or "",
                grievance_level=_int_value(previous.get("grievanceLevel"), 1),
                tension_with_others=_as_dict(previous.get("relationshipTensionDelta")),
                knowledge=_as_list(previous.get("knowledge")),
                secrets=_as_list(previous.get("secrets")),
                current_leverage=previous.get("currentLeverage") or member.leverage or "",
                raw_json={
                    "fallback": True,
                    "carriedFromChapter": previous.get("sourceChapterNumber", previous.get("chapterNumber", 0)),
                    "chapterSummary": summary,
                },
            )
        )

    active_lines = (
        db.query(LongNovelConflictLine)
        .filter(
            LongNovelConflictLine.project_id == project.id,
            LongNovelConflictLine.status.notin_(["resolved", "closed", "done"]),
        )
        .order_by(LongNovelConflictLine.importance.desc(), LongNovelConflictLine.id.asc())
        .all()
    )
    if active_lines and not any(line.last_escalated_chapter == chapter.chapter_number for line in active_lines):
        planned_keys = set(_as_list(plan.conflict_lines_to_advance if plan else []))
        selected = next(
            (
                line
                for line in active_lines
                if line.conflict_key in planned_keys or line.name in planned_keys
            ),
            active_lines[0],
        )
        if selected.status == "open":
            selected.status = "escalating"
        selected.last_escalated_chapter = chapter.chapter_number
        db.add(selected)


def _context_terms(text: str | None) -> set[str]:
    value = (text or "").strip()
    terms: set[str] = set()
    for match in re.finditer(r"[\u4e00-\u9fff]{2,8}", value):
        chunk = match.group(0)
        if len(chunk) <= 4:
            terms.add(chunk)
        else:
            for size in (2, 3, 4):
                for index in range(0, max(0, len(chunk) - size + 1)):
                    terms.add(chunk[index : index + size])
    terms.update(item.lower() for item in _ALNUM_RE.findall(value) if len(item) >= 3)
    stop_terms = {"一个", "这个", "那个", "他们", "她们", "自己", "没有", "已经", "还是", "不是", "因为", "所以", "但是"}
    return {item for item in terms if item not in stop_terms}


def _has_context_overlap(source: str | None, target: str | None, *, minimum: int = 2) -> bool:
    source_terms = _context_terms(source)
    if not source_terms:
        return True
    target_terms = _context_terms(target)
    return len(source_terms.intersection(target_terms)) >= min(minimum, max(1, len(source_terms)))


def _fallback_state_extract_data(project: LongNovelProject, chapter: LongNovelChapter) -> dict[str, Any]:
    content = (chapter.content or "").strip()
    summary = (chapter.summary or "").strip() or content[:240] or f"第{chapter.chapter_number}章已生成"
    ending_hook = _clip_text(content, 220) or summary
    members = [member.name for member in project.family_members if member.name]
    return {
        "chapterSummary": summary,
        "endingHook": ending_hook,
        "events": [
            {
                "eventType": "chapter_generated",
                "actors": members[:6],
                "location": "",
                "timeLabel": f"第{chapter.chapter_number}章",
                "cause": "",
                "effect": summary,
                "familyImpact": summary,
            }
        ],
        "canonFacts": [
            {
                "fact": summary,
                "factType": "chapter_summary",
                "entities": members[:6],
                "confidence": 0.75,
            }
        ],
        "familyMemberStateDeltas": [],
        "objectStateChanges": [],
        "conflictLineUpdates": [],
        "continuityClaims": ["state extraction fell back to deterministic chapter summary"],
        "fallbackReason": "malformed_model_json",
    }


def _fallback_chapter_plan_data(
    plan: LongNovelChapterPlan,
    context_pack: dict[str, Any],
) -> dict[str, Any]:
    active_conflict = next(
        (item for item in _as_list(context_pack.get("activeConflictLines")) if isinstance(item, dict)),
        {},
    )
    character_states = [
        item for item in _as_list(context_pack.get("latestCharacterStates")) if isinstance(item, dict)
    ]
    characters = [_text(item, "memberName") for item in character_states if _text(item, "memberName")][:6]
    domestic_conflict = (
        plan.domestic_conflict
        or _text(active_conflict, "latestUpdate")
        or _text(active_conflict, "description")
        or "围绕家庭利益和知情范围继续推进冲突"
    )
    base_events = _as_list(plan.required_events) or [domestic_conflict, plan.emotional_hook or "留下下一章钩子"]
    scenes: list[dict[str, Any]] = []
    for index, event in enumerate(base_events[:4], start=1):
        scenes.append(
            {
                "sceneNumber": index,
                "sceneGoal": str(event),
                "location": "家中",
                "characters": characters,
                "domesticConflict": domestic_conflict,
                "subtext": "每个人都在用亲情包装自己的利益诉求",
                "informationChange": str(event),
                "emotionalTurn": plan.emotional_hook or domestic_conflict,
                "endingBeat": plan.ending_hook or _text(context_pack.get("mustContinueFrom") or {}, "previousEndingHook"),
            }
        )
    if not scenes:
        scenes.append(
            {
                "sceneNumber": 1,
                "sceneGoal": plan.chapter_goal or domestic_conflict,
                "location": "家中",
                "characters": characters,
                "domesticConflict": domestic_conflict,
                "subtext": "旧账继续压到饭桌上",
                "informationChange": domestic_conflict,
                "emotionalTurn": plan.emotional_hook or domestic_conflict,
                "endingBeat": plan.ending_hook or domestic_conflict,
            }
        )
    return {
        "chapterNumber": plan.chapter_number,
        "title": plan.title,
        "chapterGoal": plan.chapter_goal,
        "mainDomesticConflict": domestic_conflict,
        "emotionalHook": plan.emotional_hook,
        "scenes": scenes,
        "mustInclude": base_events,
        "mustNotReveal": plan.forbidden_reveals or [],
        "characterArcMove": plan.character_arc_move or [],
        "conflictLinesToAdvance": plan.conflict_lines_to_advance
        or [_text(active_conflict, "conflictKey") or _text(active_conflict, "name")],
        "endingHook": plan.ending_hook or scenes[-1]["endingBeat"],
        "styleNotes": ["fallback plan after malformed model JSON; preserve chapter context pack constraints"],
    }


def _active_conflict_payloads(
    db: Session,
    project_id: UUID,
) -> list[dict[str, Any]]:
    conflict_lines = (
        db.query(LongNovelConflictLine)
        .filter(LongNovelConflictLine.project_id == project_id)
        .order_by(LongNovelConflictLine.importance.desc(), LongNovelConflictLine.id.asc())
        .all()
    )
    result: list[dict[str, Any]] = []
    for row in conflict_lines:
        if row.status in {"resolved", "closed", "done"}:
            continue
        result.append(
            {
                "conflictKey": row.conflict_key,
                "name": row.name,
                "type": row.conflict_type,
                "description": row.description,
                "latestUpdate": _conflict_latest_update(db, project_id, row),
                "status": row.status,
                "introducedChapter": row.introduced_chapter,
                "lastEscalatedChapter": row.last_escalated_chapter,
                "plannedExplosionChapter": row.planned_explosion_chapter,
                "plannedResolutionChapter": row.planned_resolution_chapter,
                "importance": row.importance,
            }
        )
    return result


def _build_chapter_context_pack_for_project(
    db: Session,
    project: LongNovelProject,
    chapter_number: int,
) -> dict[str, Any]:
    plan = (
        db.query(LongNovelChapterPlan)
        .filter(
            LongNovelChapterPlan.project_id == project.id,
            LongNovelChapterPlan.chapter_number == chapter_number,
        )
        .first()
    )
    previous_chapter = None
    if chapter_number > 1:
        previous_chapter = (
            db.query(LongNovelChapter)
            .filter(
                LongNovelChapter.project_id == project.id,
                LongNovelChapter.chapter_number == chapter_number - 1,
                LongNovelChapter.status == LongNovelChapterStatus.COMPLETED,
            )
            .first()
        )
        if previous_chapter is not None:
            _ensure_chapter_state_floor(db, project, previous_chapter)
            db.flush()
    recent_chapters = (
        db.query(LongNovelChapter)
        .filter(
            LongNovelChapter.project_id == project.id,
            LongNovelChapter.chapter_number < chapter_number,
            LongNovelChapter.status == LongNovelChapterStatus.COMPLETED,
        )
        .order_by(LongNovelChapter.chapter_number.desc())
        .limit(2)
        .all()
    )
    canon_facts = (
        db.query(LongNovelCanonFact)
        .filter(
            LongNovelCanonFact.project_id == project.id,
            LongNovelCanonFact.chapter_number < chapter_number,
        )
        .order_by(LongNovelCanonFact.chapter_number.desc(), LongNovelCanonFact.id.asc())
        .limit(80)
        .all()
    )
    family_relations = (
        db.query(LongNovelFamilyRelation)
        .filter(LongNovelFamilyRelation.project_id == project.id)
        .order_by(LongNovelFamilyRelation.id.asc())
        .all()
    )
    open_issues = (
        db.query(LongNovelContinuityIssue)
        .filter(
            LongNovelContinuityIssue.project_id == project.id,
            LongNovelContinuityIssue.chapter_number < chapter_number,
            LongNovelContinuityIssue.status == "open",
        )
        .order_by(
            LongNovelContinuityIssue.chapter_number.desc(),
            LongNovelContinuityIssue.created_at.desc(),
            LongNovelContinuityIssue.id.asc(),
        )
        .limit(40)
        .all()
    )
    latest_character_states = _latest_states(db, project.id, chapter_number, project=project)
    latest_object_states = _latest_object_states(db, project.id, chapter_number)
    active_conflicts = _active_conflict_payloads(db, project.id)
    previous_payload = None
    if previous_chapter is not None:
        previous_payload = {
            "chapterNumber": previous_chapter.chapter_number,
            "title": previous_chapter.title,
            "summary": _chapter_summary(previous_chapter),
            "endingHook": _chapter_ending_hook(db, project.id, previous_chapter.chapter_number, previous_chapter),
            "last800Chars": _clip_text(previous_chapter.content, 800),
            "wordCount": previous_chapter.word_count,
        }
    recent_payload = [
        {
            "chapterNumber": row.chapter_number,
            "title": row.title,
            "summary": _chapter_summary(row),
            "endingHook": _chapter_ending_hook(db, project.id, row.chapter_number, row),
            "last300Chars": _clip_text(row.content, 300),
            "wordCount": row.word_count,
        }
        for row in reversed(recent_chapters)
    ]
    context_pack = {
        "contextPackVersion": 2,
        "project": _project_payload(project),
        "bible": project.bible.raw_json if project.bible else {},
        "chapterNumber": chapter_number,
        "currentPlan": plan.raw_json if plan else None,
        "currentPlanRecord": _plan_payload(plan),
        "currentChapterOutline": _plan_payload(plan),
        "previousChapter": previous_payload,
        "recentChapters": recent_payload,
        "recentChapterSummaries": [
            {
                "chapterNumber": row.chapter_number,
                "title": row.title,
                "summary": _chapter_summary(row),
            }
            for row in reversed(recent_chapters)
        ],
        "familyRelations": [
            {
                "fromMember": row.from_member,
                "toMember": row.to_member,
                "relationType": row.relation_type,
                "surfaceRelation": row.surface_relation,
                "hiddenConflict": row.hidden_conflict,
                "interestConflict": row.interest_conflict,
                "emotionalDebt": row.emotional_debt,
                "currentTensionLevel": row.current_tension_level,
            }
            for row in family_relations
        ],
        "activeConflictLines": active_conflicts,
        "latestCharacterStates": latest_character_states,
        "latestObjectStates": latest_object_states,
        "objectStates": latest_object_states,
        "canonFacts": [
            {
                "chapterNumber": row.chapter_number,
                "fact": row.fact,
                "factType": row.fact_type,
                "entities": row.entities,
                "confidence": row.confidence,
            }
            for row in canon_facts
        ],
        "openContinuityIssues": [
            {
                "chapterNumber": row.chapter_number,
                "severity": row.severity,
                "issueType": row.issue_type,
                "description": row.description,
                "evidence": row.evidence,
                "suggestedFix": row.suggested_fix,
            }
            for row in open_issues
        ],
        "mustContinueFrom": {
            "previousEndingHook": previous_payload["endingHook"] if previous_payload else "",
            "previousChapterSummary": previous_payload["summary"] if previous_payload else "",
            "previousLast800Chars": previous_payload["last800Chars"] if previous_payload else "",
            "requiredCarryOver": [
                item
                for item in [
                    previous_payload["endingHook"] if previous_payload else "",
                    *[line.get("latestUpdate") or line.get("description") for line in active_conflicts[:6]],
                    *[
                        f"{item.get('objectName')}：{item.get('holder') or item.get('location') or item.get('status')}"
                        for item in latest_object_states[:8]
                    ],
                ]
                if item
            ],
            "strictRules": [
                "下一章必须承接上一章 endingHook 和 last800Chars 的即时情境。",
                "人物目标、知情范围、把柄、物品持有人必须以 latestCharacterStates/objectStates 为准。",
                "至少推进一条 activeConflictLines，不得把已解决矛盾当新矛盾。",
                "canonFacts 是硬事实，不得改写、遗忘或让角色提前知道未公开信息。",
            ],
        },
    }
    if chapter_number > 1:
        logger.info(
            "long novel context pack project=%s chapter=%s previous=%s states=%s conflicts=%s facts=%s objects=%s",
            project.id,
            chapter_number,
            bool(previous_payload),
            len(latest_character_states),
            len(active_conflicts),
            len(context_pack["canonFacts"]),
            len(latest_object_states),
        )
    return context_pack


def validate_chapter_context_pack(context_pack: dict[str, Any], chapter_number: int | None = None) -> None:
    number = _int_value(context_pack.get("chapterNumber"), chapter_number or 0)
    if number <= 1:
        return
    missing: list[str] = []
    previous = _as_dict(context_pack.get("previousChapter"))
    if not previous:
        missing.append("previous completed chapter")
    if not _text(previous, "summary"):
        missing.append("previous chapter summary")
    if not _text(previous, "endingHook"):
        missing.append("previous chapter ending hook")
    states = [item for item in _as_list(context_pack.get("latestCharacterStates")) if isinstance(item, dict)]
    if not any(_int_value(item.get("sourceChapterNumber", item.get("chapterNumber")), 0) > 0 for item in states):
        missing.append("persisted character state")
    if not _as_list(context_pack.get("activeConflictLines")):
        missing.append("active conflict lines")
    if not _as_list(context_pack.get("canonFacts")):
        missing.append("canon facts")
    if missing:
        raise FamilyLongNovelError(
            f"Chapter {number} context pack is incomplete before generation: {', '.join(missing)}"
        )


def _clip_prompt_value(value: Any, limit: int) -> Any:
    if isinstance(value, str):
        text = value.strip()
        return text if len(text) <= limit else text[:limit]
    if isinstance(value, list):
        return [_clip_prompt_value(item, limit) for item in value]
    if isinstance(value, dict):
        return {key: _clip_prompt_value(item, limit) for key, item in value.items()}
    return value


def _compact_project_for_prompt(project_payload: dict[str, Any]) -> dict[str, Any]:
    compact = dict(project_payload)
    source_text = str(compact.pop("sourceText", "") or "").strip()
    if source_text:
        compact["sourceTextPreview"] = source_text[:800]
    return compact


def _compact_bible_for_prompt(bible: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(bible, dict):
        return {}
    keys = [
        "title",
        "genre",
        "theme",
        "corePromise",
        "emotionalHook",
        "familyBackground",
        "mainConflict",
        "endingDirection",
        "worldRules",
        "styleRules",
        "forbiddenRules",
    ]
    compact = {key: _clip_prompt_value(bible.get(key), 700) for key in keys if bible.get(key)}
    members = [item for item in _as_list(bible.get("familyMembers")) if isinstance(item, dict)]
    if members:
        compact["familyMembers"] = [
            {
                "name": _text(item, "name"),
                "familyRole": _text(item, "familyRole", "family_role"),
                "privateDesire": _clip_prompt_value(_text(item, "privateDesire", "private_desire"), 260),
                "grievance": _clip_prompt_value(_text(item, "grievance"), 260),
                "leverage": _clip_prompt_value(_text(item, "leverage"), 220),
                "secret": _clip_prompt_value(_text(item, "secret"), 220),
            }
            for item in members[:20]
        ]
    return compact


def _stable_project_cache_prefix(context_pack: dict[str, Any]) -> str:
    project_payload = _compact_project_for_prompt(_as_dict(context_pack.get("project")))
    bible_payload = _compact_bible_for_prompt(_as_dict(context_pack.get("bible")))
    relations = sorted(
        [item for item in _as_list(context_pack.get("familyRelations")) if isinstance(item, dict)],
        key=lambda item: (
            str(item.get("fromMember") or ""),
            str(item.get("toMember") or ""),
            str(item.get("relationType") or ""),
        ),
    )
    conflicts = sorted(
        [item for item in _as_list(context_pack.get("activeConflictLines")) if isinstance(item, dict)],
        key=lambda item: (str(item.get("conflictKey") or ""), str(item.get("name") or "")),
    )
    stable_payload = {
        "cachePrefixVersion": "family-long-novel-stable-v4",
        "purpose": "shared_project_memory_for_prompt_cache",
        "rule": "This stable prefix is shared by all chapter workflow calls for this project. Use it only as project-level memory; the later per-call context is authoritative for current chapter state.",
        "project": project_payload,
        "bible": bible_payload,
        "familyRelations": [
            {
                "fromMember": item.get("fromMember"),
                "toMember": item.get("toMember"),
                "relationType": item.get("relationType"),
                "surfaceRelation": _clip_prompt_value(item.get("surfaceRelation"), 220),
                "hiddenConflict": _clip_prompt_value(item.get("hiddenConflict"), 260),
                "interestConflict": _clip_prompt_value(item.get("interestConflict"), 260),
            }
            for item in relations[:36]
        ],
        "conflictLineBlueprints": [
            {
                "conflictKey": item.get("conflictKey"),
                "name": item.get("name"),
                "type": item.get("type"),
                "description": _clip_prompt_value(item.get("description"), 420),
                "plannedExplosionChapter": item.get("plannedExplosionChapter"),
                "plannedResolutionChapter": item.get("plannedResolutionChapter"),
                "importance": item.get("importance"),
            }
            for item in conflicts
        ],
    }
    return (
        "【长篇项目稳定缓存前缀】\n"
        "以下内容在同一长篇项目内保持稳定，放在提示词最前方用于 prompt cache。"
        "不得用它替代后文的本章动态上下文、上一章结尾、最新人物状态和最新物品状态。\n"
        f"{_canonical_json_dumps(stable_payload)}\n"
        "【长篇项目稳定缓存前缀结束】"
    )


def _with_project_cache_prefix(context_pack: dict[str, Any], prompt: str) -> str:
    rolling_context = _shared_chapter_cache_context(context_pack)
    rolling_prefix = (
        "[AUTHORITATIVE ROLLING CHAPTER CONTEXT]\n"
        "This JSON contains the complete pre-chapter continuity ledger shared by planning, writing, state extraction, and QA. "
        "The later action delta contains only fields that can change between workflow nodes.\n"
        f"{_canonical_json_dumps(rolling_context)}\n"
        "[END AUTHORITATIVE ROLLING CHAPTER CONTEXT]"
    )
    return f"{_stable_project_cache_prefix(context_pack)}\n\n{rolling_prefix}\n\n{prompt}"


def _compact_plan_for_prompt(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(plan, dict):
        return None
    scenes = [item for item in _as_list(plan.get("scenes")) if isinstance(item, dict)]
    return {
        "chapterNumber": plan.get("chapterNumber"),
        "volumeNumber": plan.get("volumeNumber"),
        "title": _clip_prompt_value(plan.get("title"), 80),
        "targetWords": plan.get("targetWords"),
        "chapterGoal": _clip_prompt_value(plan.get("chapterGoal"), 500),
        "domesticConflict": _clip_prompt_value(plan.get("domesticConflict"), 500),
        "emotionalHook": _clip_prompt_value(plan.get("emotionalHook"), 420),
        "requiredEvents": _clip_prompt_value(_as_list(plan.get("requiredEvents"))[:8], 260),
        "characterArcMove": _clip_prompt_value(_as_list(plan.get("characterArcMove"))[:8], 260),
        "conflictLinesToAdvance": _clip_prompt_value(_as_list(plan.get("conflictLinesToAdvance"))[:8], 160),
        "forbiddenReveals": _clip_prompt_value(_as_list(plan.get("forbiddenReveals"))[:12], 180),
        "endingHook": _clip_prompt_value(plan.get("endingHook"), 360),
        "scenes": [
            {
                "sceneNumber": item.get("sceneNumber"),
                "sceneGoal": _clip_prompt_value(item.get("sceneGoal"), 260),
                "location": _clip_prompt_value(item.get("location"), 80),
                "characters": _clip_prompt_value(_as_list(item.get("characters"))[:8], 80),
                "domesticConflict": _clip_prompt_value(item.get("domesticConflict"), 260),
                "informationChange": _clip_prompt_value(item.get("informationChange"), 220),
                "emotionalTurn": _clip_prompt_value(item.get("emotionalTurn"), 220),
                "endingBeat": _clip_prompt_value(item.get("endingBeat"), 220),
            }
            for item in scenes[:6]
        ],
    }


def _compact_context_for_prompt(context_pack: dict[str, Any], purpose: str) -> dict[str, Any]:
    previous = _as_dict(context_pack.get("previousChapter"))
    current_plan = _compact_plan_for_prompt(
        _as_dict(context_pack.get("currentPlanRecord") or context_pack.get("currentChapterOutline"))
    )
    recent_chapters = [item for item in _as_list(context_pack.get("recentChapters")) if isinstance(item, dict)]
    conflicts = [item for item in _as_list(context_pack.get("activeConflictLines")) if isinstance(item, dict)]
    states = [item for item in _as_list(context_pack.get("latestCharacterStates")) if isinstance(item, dict)]
    objects = [item for item in _as_list(context_pack.get("objectStates") or context_pack.get("latestObjectStates")) if isinstance(item, dict)]
    facts = [item for item in _as_list(context_pack.get("canonFacts")) if isinstance(item, dict)]
    relations = [item for item in _as_list(context_pack.get("familyRelations")) if isinstance(item, dict)]
    issues = [item for item in _as_list(context_pack.get("openContinuityIssues")) if isinstance(item, dict)]
    issues_for_prompt = [] if purpose == "state_extract" else issues[:12]
    recent_limit = 1 if purpose == "state_extract" else 2
    previous_tail_limit = 500 if purpose == "state_extract" else 800
    must_continue = _as_dict(context_pack.get("mustContinueFrom"))
    return {
        "contextPackVersion": context_pack.get("contextPackVersion"),
        "promptContextMode": "compact",
        "stableProjectContext": "Project, Bible, relation definitions, and conflict blueprints are provided once in the stable cache prefix before this JSON.",
        "projectControls": {
            key: value
            for key, value in _compact_project_for_prompt(
                _as_dict(context_pack.get("project"))
            ).items()
            if key
            in {
                "sourceType",
                "targetWords",
                "targetChapters",
                "averageChapterTargetWords",
                "chapterWordTolerance",
                "generationMode",
            }
        },
        "familyRelations": [
            {
                "fromMember": item.get("fromMember"),
                "toMember": item.get("toMember"),
                "relationType": item.get("relationType"),
                "currentTensionLevel": item.get("currentTensionLevel"),
            }
            for item in relations[:30]
        ],
        "activeConflictLines": [
            {
                "conflictKey": item.get("conflictKey"),
                "name": item.get("name"),
                "type": item.get("type"),
                "latestUpdate": _clip_prompt_value(item.get("latestUpdate"), 360),
                "status": item.get("status"),
                "lastEscalatedChapter": item.get("lastEscalatedChapter"),
            }
            for item in conflicts[:14]
        ],
        "latestCharacterStates": [
            {
                "memberName": item.get("memberName"),
                "sourceChapterNumber": item.get("sourceChapterNumber", item.get("chapterNumber")),
                "goal": _clip_prompt_value(item.get("goal"), 240),
                "emotion": _clip_prompt_value(item.get("emotion"), 180),
                "grievanceLevel": item.get("grievanceLevel"),
                "knowledge": _clip_prompt_value(_as_list(item.get("knowledge"))[:10], 180),
                "secrets": _clip_prompt_value(_as_list(item.get("secrets"))[:8], 180),
                "currentLeverage": _clip_prompt_value(item.get("currentLeverage"), 220),
                "relationshipTensionDelta": _clip_prompt_value(item.get("relationshipTensionDelta"), 160),
            }
            for item in states[:30]
        ],
        "objectStates": [
            {
                "objectName": item.get("objectName"),
                "sourceChapterNumber": item.get("sourceChapterNumber", item.get("chapterNumber")),
                "holder": item.get("holder"),
                "location": item.get("location"),
                "knownBy": _as_list(item.get("knownBy"))[:8],
                "status": _clip_prompt_value(item.get("status"), 160),
            }
            for item in objects[:20]
        ],
        "canonFacts": [
            {
                "chapterNumber": item.get("chapterNumber"),
                "fact": _clip_prompt_value(item.get("fact"), 260),
                "factType": item.get("factType"),
                "entities": _as_list(item.get("entities"))[:8],
                "confidence": item.get("confidence"),
            }
            for item in facts[:50]
        ],
        "openContinuityIssues": [
            {
                "chapterNumber": item.get("chapterNumber"),
                "severity": item.get("severity"),
                "issueType": item.get("issueType"),
                "description": _clip_prompt_value(item.get("description"), 260),
                "suggestedFix": _clip_prompt_value(item.get("suggestedFix"), 220),
            }
            for item in issues_for_prompt
        ],
        "purpose": purpose,
        "chapterNumber": context_pack.get("chapterNumber"),
        "currentPlanRecord": current_plan,
        "previousChapter": {
            "chapterNumber": previous.get("chapterNumber"),
            "title": _clip_prompt_value(previous.get("title"), 80),
            "summary": _clip_prompt_value(previous.get("summary"), 700),
            "endingHook": _clip_prompt_value(previous.get("endingHook"), 420),
            "last800Chars": _clip_text(previous.get("last800Chars"), previous_tail_limit),
            "wordCount": previous.get("wordCount"),
        }
        if previous
        else None,
        "recentChapters": [
            {
                "chapterNumber": item.get("chapterNumber"),
                "title": _clip_prompt_value(item.get("title"), 80),
                "summary": _clip_prompt_value(item.get("summary"), 500),
                "endingHook": _clip_prompt_value(item.get("endingHook"), 260),
            }
            for item in recent_chapters[-recent_limit:]
        ],
        "mustContinueFrom": {
            "previousEndingHook": _clip_prompt_value(must_continue.get("previousEndingHook"), 420),
            "previousChapterSummary": _clip_prompt_value(must_continue.get("previousChapterSummary"), 700),
            "previousLast800Chars": _clip_text(must_continue.get("previousLast800Chars"), previous_tail_limit),
            "requiredCarryOver": _clip_prompt_value(_as_list(must_continue.get("requiredCarryOver"))[:14], 260),
            "strictRules": _as_list(must_continue.get("strictRules")),
        },
        "omittedForCostControl": {
            "fullCurrentPlanRaw": bool(context_pack.get("currentPlan")),
            "recentChapterCount": len(recent_chapters),
            "canonFactCount": len(facts),
            "conflictLineCount": len(conflicts),
            "characterStateCount": len(states),
            "objectStateCount": len(objects),
        },
    }


def _shared_chapter_cache_context(context_pack: dict[str, Any]) -> dict[str, Any]:
    shared = _compact_context_for_prompt(context_pack, "chapter_write")
    shared.pop("purpose", None)
    shared.pop("currentPlanRecord", None)
    shared.pop("omittedForCostControl", None)
    shared["promptContextMode"] = "shared_chapter_cache_prefix"
    return shared


def _compact_context_delta_for_prompt(context_pack: dict[str, Any], purpose: str) -> dict[str, Any]:
    full = _compact_context_for_prompt(context_pack, purpose)
    return {
        "contextPackVersion": full.get("contextPackVersion"),
        "promptContextMode": "action_delta",
        "sharedChapterContext": "See AUTHORITATIVE ROLLING CHAPTER CONTEXT before this action prompt.",
        "purpose": purpose,
        "chapterNumber": full.get("chapterNumber"),
        "currentPlanRecord": full.get("currentPlanRecord"),
        "omittedForCostControl": full.get("omittedForCostControl"),
    }


def _context_prompt_json(context_pack: dict[str, Any], purpose: str) -> str:
    return _json_dumps(_compact_context_delta_for_prompt(context_pack, purpose))


def build_chapter_context_pack(
    db: Session,
    project_id: UUID,
    chapter_number: int,
    user: User | None = None,
) -> dict[str, Any]:
    project = _get_project(db, project_id, user)
    _ensure_bible(db, project, user)
    context_pack = _build_chapter_context_pack_for_project(db, project, chapter_number)
    validate_chapter_context_pack(context_pack, chapter_number)
    return context_pack


def _build_chapter_context(db: Session, project: LongNovelProject, chapter_number: int) -> dict[str, Any]:
    return _build_chapter_context_pack_for_project(db, project, chapter_number)


def _chapter_context_snapshot_with_plan(
    context_pack: dict[str, Any],
    plan: LongNovelChapterPlan,
) -> dict[str, Any]:
    """Freeze one pre-chapter ledger while allowing the newly approved plan as action data."""

    snapshot = copy.deepcopy(context_pack)
    plan_payload = _plan_payload(plan)
    snapshot["currentPlan"] = copy.deepcopy(plan.raw_json or {})
    snapshot["currentPlanRecord"] = plan_payload
    snapshot["currentChapterOutline"] = copy.deepcopy(plan_payload)
    return snapshot


def generate_family_chapter_plan(
    db: Session,
    project_id: UUID,
    chapter_number: int,
    user: User | None = None,
    *,
    context_pack: dict[str, Any] | None = None,
) -> LongNovelChapterPlan:
    project = _get_project(db, project_id, user)
    _ensure_bible(db, project, user)
    if (
        db.query(LongNovelChapterPlan)
        .filter(LongNovelChapterPlan.project_id == project.id)
        .count()
        == 0
    ):
        generate_family_novel_outline(db, project.id, user)
        db.refresh(project)
    plan = (
        db.query(LongNovelChapterPlan)
        .filter(
            LongNovelChapterPlan.project_id == project.id,
            LongNovelChapterPlan.chapter_number == chapter_number,
        )
        .first()
    )
    if plan is None:
        fallback = _fallback_chapter_plan(project, chapter_number)
        plan = LongNovelChapterPlan(
            project_id=project.id,
            chapter_number=chapter_number,
            volume_number=fallback["volumeNumber"],
            title=fallback["title"],
            target_words=fallback["targetWords"],
            chapter_goal=fallback["chapterGoal"],
            domestic_conflict=fallback["domesticConflict"],
            emotional_hook=fallback["emotionalHook"],
            required_events=fallback["requiredEvents"],
            character_arc_move=fallback["characterArcMove"],
            conflict_lines_to_advance=fallback["conflictLinesToAdvance"],
            forbidden_reveals=fallback["forbiddenReveals"],
            ending_hook=fallback["endingHook"],
            raw_json=fallback,
        )
        db.add(plan)
        db.commit()
        db.refresh(plan)

    context = context_pack or _build_chapter_context(db, project, chapter_number)
    validate_chapter_context_pack(context, chapter_number)
    _ensure_chapter_prompt_cache_warm(
        db,
        project_id=project.id,
        chapter_number=chapter_number,
        context_pack=context,
    )

    project.status = LongNovelProjectStatus.GENERATING
    plan.status = LongNovelChapterStatus.GENERATING
    db.add(project)
    db.add(plan)
    db.commit()
    db.refresh(project)
    db.refresh(plan)

    prompt = render_prompt(
        ZHOULI_CHAPTER_PLAN_PROMPT,
        chapter_context=_context_prompt_json(context, "chapter_plan"),
    )
    prompt = f"{prompt}\n\n{_CONTEXT_PACK_PLAN_RULES}\n\n{GENERAL_NOVEL_PROSE_GUARDRAILS}"
    prompt = _with_project_cache_prefix(context, prompt)
    try:
        data = _call_json(
            db,
            project_id=project.id,
            chapter_number=chapter_number,
            action="generate_chapter_plan",
            tier="default",
            prompt=prompt,
            max_output_tokens=16000,
            prompt_cache_key=_chapter_prompt_cache_key(project.id, chapter_number),
        )
    except MalformedModelJsonError as exc:
        logger.warning(
            "chapter plan JSON malformed; using deterministic fallback project=%s chapter=%s error=%s",
            project.id,
            chapter_number,
            exc,
        )
        data = _fallback_chapter_plan_data(plan, context)
    except Exception:
        _mark_generation_failed(db, project, plan=plan)
        raise
    plan.title = _clean_title(_text(data, "title", default=plan.title), plan.title)
    plan.chapter_goal = _text(data, "chapterGoal", "chapter_goal", default=plan.chapter_goal or "")
    plan.domestic_conflict = _text(data, "mainDomesticConflict", "domesticConflict", "domestic_conflict", default=plan.domestic_conflict or "")
    plan.emotional_hook = _text(data, "emotionalHook", "emotional_hook", default=plan.emotional_hook or "")
    plan.scenes = _as_list(_get(data, "scenes"))
    plan.required_events = _as_list(_get(data, "mustInclude", "requiredEvents")) or plan.required_events
    plan.character_arc_move = _as_list(_get(data, "characterArcMove", "character_arc_move")) or plan.character_arc_move
    plan.conflict_lines_to_advance = _as_list(_get(data, "conflictLinesToAdvance", "conflict_lines_to_advance")) or plan.conflict_lines_to_advance
    plan.forbidden_reveals = _as_list(_get(data, "mustNotReveal", "forbiddenReveals")) or plan.forbidden_reveals
    plan.ending_hook = _text(data, "endingHook", "ending_hook", default=plan.ending_hook or "")
    plan.raw_json = {**(plan.raw_json or {}), "chapterPlan": data}
    plan.status = LongNovelChapterStatus.PLANNED
    project.status = LongNovelProjectStatus.ACTIVE
    db.add(project)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def generate_family_chapter(
    db: Session,
    project_id: UUID,
    chapter_number: int,
    user: User | None = None,
    *,
    auto_repair: bool = True,
) -> LongNovelChapter:
    project = _get_project(db, project_id, user)
    _ensure_bible(db, project, user)
    if (
        db.query(LongNovelChapterPlan)
        .filter(LongNovelChapterPlan.project_id == project.id)
        .count()
        == 0
    ):
        generate_family_novel_outline(db, project.id, user)
    plan = (
        db.query(LongNovelChapterPlan)
        .filter(
            LongNovelChapterPlan.project_id == project.id,
            LongNovelChapterPlan.chapter_number == chapter_number,
        )
        .first()
    )
    initial_context = _build_chapter_context(db, project, chapter_number)
    validate_chapter_context_pack(initial_context, chapter_number)
    if plan is None or not plan.scenes:
        plan = generate_family_chapter_plan(db, project.id, chapter_number, user, context_pack=initial_context)
        db.refresh(project)
        db.refresh(plan)

    context = _chapter_context_snapshot_with_plan(initial_context, plan)
    validate_chapter_context_pack(context, chapter_number)
    _ensure_chapter_prompt_cache_warm(
        db,
        project_id=project.id,
        chapter_number=chapter_number,
        context_pack=context,
    )

    saved_chapter = (
        db.query(LongNovelChapter)
        .filter(
            LongNovelChapter.project_id == project.id,
            LongNovelChapter.chapter_number == chapter_number,
        )
        .first()
    )
    if (
        saved_chapter is not None
        and (saved_chapter.content or "").strip()
        and saved_chapter.status == LongNovelChapterStatus.NEEDS_REPAIR
    ):
        project.status = LongNovelProjectStatus.GENERATING
        plan.status = LongNovelChapterStatus.GENERATING
        db.add(project)
        db.add(plan)
        db.commit()
        try:
            saved_chapter = _postprocess_family_chapter(
                db,
                project,
                plan,
                saved_chapter,
                context,
                user,
                auto_repair=auto_repair,
            )
        except Exception as exc:
            _mark_saved_chapter_postprocess_failed(db, project, plan, saved_chapter)
            raise FamilyLongNovelError(
                f"第{chapter_number}章正文已保留，但状态抽取或连续性审校失败，可继续从质检节点恢复：{exc}"
            ) from exc
        return saved_chapter

    project.status = LongNovelProjectStatus.GENERATING
    plan.status = LongNovelChapterStatus.GENERATING
    db.add(project)
    db.add(plan)
    db.commit()

    prompt = render_prompt(
        ZHOULI_CHAPTER_WRITE_PROMPT,
        chapter_context=_context_prompt_json(context, "chapter_write"),
    )
    prompt = f"{prompt}\n\n{_CONTEXT_PACK_WRITE_RULES}\n\n{GENERAL_NOVEL_PROSE_GUARDRAILS}"
    prompt = _with_project_cache_prefix(context, prompt)
    try:
        content = _call_text(
            db,
            project_id=project.id,
            chapter_number=chapter_number,
            action="generate_chapter",
            tier="default",
            prompt=prompt,
            max_output_tokens=_chapter_write_max_output_tokens(project),
            prompt_cache_key=_chapter_prompt_cache_key(project.id, chapter_number),
        )
    except Exception:
        _mark_generation_failed(db, project, plan=plan)
        raise
    title = plan.title if plan else f"第{chapter_number}章"
    chapter = (
        db.query(LongNovelChapter)
        .filter(
            LongNovelChapter.project_id == project.id,
            LongNovelChapter.chapter_number == chapter_number,
        )
        .first()
        or LongNovelChapter(project_id=project.id, chapter_number=chapter_number, title=title)
    )
    chapter.title = title
    chapter.content = content
    chapter.raw_model_output = content
    chapter.word_count = _word_count(content)
    chapter.status = LongNovelChapterStatus.NEEDS_REPAIR
    plan.status = LongNovelChapterStatus.NEEDS_REPAIR
    db.add(chapter)
    db.add(plan)
    db.commit()
    db.refresh(chapter)
    try:
        chapter = _postprocess_family_chapter(
            db,
            project,
            plan,
            chapter,
            context,
            user,
            auto_repair=auto_repair,
        )
    except Exception as exc:
        logger.exception(
            "family chapter postprocess failed after content completion project=%s chapter=%s",
            project.id,
            chapter_number,
        )
        _mark_saved_chapter_postprocess_failed(db, project, plan, chapter)
        raise FamilyLongNovelError(
            f"第{chapter_number}章正文已保留，但状态抽取或连续性审校失败，可继续从质检节点恢复：{exc}"
        ) from exc

    sync_family_project_progress(db, project)
    project.status = (
        LongNovelProjectStatus.COMPLETED
        if project.current_chapter >= project.target_chapters
        else LongNovelProjectStatus.ACTIVE
    )
    db.add(project)
    db.commit()
    db.refresh(chapter)
    return chapter


def _mark_saved_chapter_postprocess_failed(
    db: Session,
    project: LongNovelProject,
    plan: LongNovelChapterPlan,
    chapter: LongNovelChapter,
) -> None:
    db.rollback()
    project.status = LongNovelProjectStatus.FAILED
    plan.status = LongNovelChapterStatus.NEEDS_REPAIR
    chapter.status = LongNovelChapterStatus.NEEDS_REPAIR
    db.add(project)
    db.add(plan)
    db.add(chapter)
    db.commit()


def _postprocess_family_chapter(
    db: Session,
    project: LongNovelProject,
    plan: LongNovelChapterPlan,
    chapter: LongNovelChapter,
    context: dict[str, Any],
    user: User | None,
    *,
    auto_repair: bool,
) -> LongNovelChapter:
    extracted_state = extract_family_chapter_state(
        db,
        project.id,
        chapter.chapter_number,
        user,
        context_pack=context,
    )
    issues = check_family_continuity(
        db,
        project.id,
        chapter.chapter_number,
        user,
        context_pack=context,
        extracted_state=extracted_state,
    )
    severe = [item for item in issues if item.severity in {"high", "severe"} and item.status == "open"]
    if severe and auto_repair and settings.LONG_NOVEL_MAX_CHAPTER_REPAIR_COUNT > 0:
        chapter = repair_family_chapter(db, project.id, chapter.chapter_number, user)
    chapter.status = LongNovelChapterStatus.COMPLETED
    plan.status = LongNovelChapterStatus.COMPLETED
    project.status = LongNovelProjectStatus.ACTIVE
    db.add(chapter)
    db.add(plan)
    db.add(project)
    db.commit()
    sync_family_project_progress(db, project)
    db.refresh(chapter)
    return chapter


def extract_family_chapter_state(
    db: Session,
    project_id: UUID,
    chapter_number: int,
    user: User | None = None,
    *,
    context_pack: dict[str, Any] | None = None,
) -> dict[str, Any]:
    project = _get_project(db, project_id, user)
    chapter = (
        db.query(LongNovelChapter)
        .filter(
            LongNovelChapter.project_id == project.id,
            LongNovelChapter.chapter_number == chapter_number,
        )
        .first()
    )
    if chapter is None:
        raise FamilyLongNovelError("章节不存在，无法抽取状态")
    context = copy.deepcopy(context_pack) if context_pack is not None else _build_chapter_context(db, project, chapter_number)
    validate_chapter_context_pack(context, chapter_number)
    _ensure_chapter_prompt_cache_warm(
        db,
        project_id=project.id,
        chapter_number=chapter_number,
        context_pack=context,
    )
    prompt = render_prompt(
        ZHOULI_STATE_EXTRACT_PROMPT,
        chapter_context=_context_prompt_json(context, "state_extract"),
        chapter_text=chapter.content,
    )
    prompt = f"{prompt}\n\n{_CONTEXT_PACK_STATE_RULES}"
    prompt = _with_project_cache_prefix(context, prompt)
    try:
        data = _call_json(
            db,
            project_id=project.id,
            chapter_number=chapter_number,
            action="extract_chapter_state",
            tier="cheap",
            prompt=prompt,
            max_output_tokens=max(8000, int(settings.LONG_NOVEL_STATE_MAX_OUTPUT_TOKENS or 14000)),
            prompt_cache_key=_chapter_prompt_cache_key(project.id, chapter_number),
            reasoning_effort=str(settings.LONG_NOVEL_STATE_REASONING_EFFORT or "high"),
        )
    except MalformedModelJsonError as exc:
        logger.warning(
            "chapter state JSON malformed; using deterministic fallback project=%s chapter=%s error=%s",
            project.id,
            chapter_number,
            exc,
        )
        data = _fallback_state_extract_data(project, chapter)
    chapter.summary = _text(data, "chapterSummary", "chapter_summary") or chapter.content[:240]
    db.add(chapter)

    db.query(LongNovelEvent).filter(LongNovelEvent.project_id == project.id, LongNovelEvent.chapter_number == chapter_number).delete()
    db.query(LongNovelCanonFact).filter(LongNovelCanonFact.project_id == project.id, LongNovelCanonFact.chapter_number == chapter_number).delete()
    db.query(LongNovelCharacterState).filter(LongNovelCharacterState.project_id == project.id, LongNovelCharacterState.chapter_number == chapter_number).delete()
    db.query(LongNovelObjectState).filter(LongNovelObjectState.project_id == project.id, LongNovelObjectState.chapter_number == chapter_number).delete()

    for item in _as_list(_get(data, "events")):
        if not isinstance(item, dict):
            continue
        db.add(
            LongNovelEvent(
                project_id=project.id,
                chapter_number=chapter_number,
                event_type=_text(item, "eventType", "event_type"),
                actors=_as_list(_get(item, "actors")),
                location=_text(item, "location"),
                time_label=_text(item, "timeLabel", "time_label"),
                cause=_text(item, "cause"),
                effect=_text(item, "effect"),
                family_impact=_text(item, "familyImpact", "family_impact"),
                raw_json=item,
            )
        )
    for item in _as_list(_get(data, "canonFacts", "canon_facts")):
        if not isinstance(item, dict):
            continue
        fact = _text(item, "fact")
        if not fact:
            continue
        db.add(
            LongNovelCanonFact(
                project_id=project.id,
                chapter_number=chapter_number,
                fact=fact,
                fact_type=_text(item, "factType", "fact_type"),
                entities=_as_list(_get(item, "entities")),
                source=f"chapter:{chapter_number}",
                confidence=float(_get(item, "confidence", default=1) or 1),
            )
        )
    for item in _as_list(_get(data, "familyMemberStateDeltas", "family_member_state_deltas")):
        if not isinstance(item, dict):
            continue
        member_name = _text(item, "memberName", "member_name")
        if not member_name:
            continue
        db.add(
            LongNovelCharacterState(
                project_id=project.id,
                chapter_number=chapter_number,
                member_name=member_name,
                goal=_text(item, "goal"),
                emotion=_text(item, "emotion"),
                grievance_level=_int_value(_get(item, "grievanceLevel", "grievance_level"), 1),
                tension_with_others=_as_dict(_get(item, "relationshipTensionDelta", "relationship_tension_delta")),
                knowledge=_as_list(_get(item, "knowledgeAdded", "knowledge_added")),
                secrets=_as_list(_get(item, "secretRevealed", "secret_revealed")),
                current_leverage=_text(item, "currentLeverage", "current_leverage"),
                raw_json=item,
            )
        )
    for item in _as_list(_get(data, "objectStateChanges", "object_state_changes")):
        if not isinstance(item, dict):
            continue
        object_name = _text(item, "objectName", "object_name")
        if not object_name:
            continue
        db.add(
            LongNovelObjectState(
                project_id=project.id,
                chapter_number=chapter_number,
                object_name=object_name,
                holder=_text(item, "holder"),
                location=_text(item, "location"),
                known_by=_as_list(_get(item, "knownBy", "known_by")),
                status=_text(item, "status"),
            )
        )
    for item in _as_list(_get(data, "conflictLineUpdates", "conflict_line_updates")):
        if not isinstance(item, dict):
            continue
        key = _text(item, "conflictKey", "conflict_key")
        if not key:
            continue
        line = (
            db.query(LongNovelConflictLine)
            .filter(LongNovelConflictLine.project_id == project.id, LongNovelConflictLine.conflict_key == key)
            .first()
        )
        if line is None:
            line = LongNovelConflictLine(
                project_id=project.id,
                conflict_key=key,
                name=key,
                description=_text(item, "description"),
                introduced_chapter=chapter_number,
            )
        update_type = _text(item, "updateType", "update_type", default="advanced")
        if update_type in {"advanced", "exploded", "resolved", "introduced"}:
            line.status = {"advanced": "escalating"}.get(update_type, update_type)
            line.last_escalated_chapter = chapter_number
        db.add(line)
    db.flush()
    _ensure_chapter_state_floor(db, project, chapter, extracted=data)
    db.commit()
    return data


def _rule_continuity_issues(
    db: Session,
    project: LongNovelProject,
    chapter: LongNovelChapter,
    context_pack: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    content = chapter.content or ""
    context_pack = context_pack or {}
    previous = _as_dict(context_pack.get("previousChapter"))
    if chapter.chapter_number > 1 and previous:
        opening = content[:1800]
        previous_hook = _text(previous, "endingHook")
        if previous_hook and not _has_context_overlap(previous_hook, opening):
            issues.append(
                {
                    "severity": "high",
                    "issueType": "previous_hook_not_continued",
                    "description": "This chapter does not visibly continue the previous chapter ending hook.",
                    "evidence": previous_hook[:240],
                    "suggestedFix": "Open this chapter from the unresolved situation left by the previous ending hook.",
                }
            )
    active_conflicts = [
        item for item in _as_list(context_pack.get("activeConflictLines")) if isinstance(item, dict)
    ]
    if active_conflicts:
        current_plan = _as_dict(context_pack.get("currentPlanRecord"))
        planned_conflict = " ".join(
            [
                _text(current_plan, "domesticConflict"),
                " ".join(str(item) for item in _as_list(current_plan.get("conflictLinesToAdvance"))),
            ]
        )
        conflict_hit = False
        for line in active_conflicts:
            probes = [
                _text(line, "conflictKey"),
                _text(line, "name"),
                _text(line, "latestUpdate"),
                _text(line, "description"),
            ]
            if any(probe and (probe in content or probe in planned_conflict) for probe in probes[:2]):
                conflict_hit = True
                break
            if any(_has_context_overlap(probe, content, minimum=2) for probe in probes[2:]):
                conflict_hit = True
                break
        if not conflict_hit:
            issues.append(
                {
                    "severity": "high",
                    "issueType": "active_conflict_not_advanced",
                    "description": "This chapter does not clearly advance any active conflict line from the context pack.",
                    "evidence": ", ".join(_text(item, "name") or _text(item, "conflictKey") for item in active_conflicts[:5]),
                    "suggestedFix": "Use at least one active conflict line as the concrete family-interest event of this chapter.",
                }
            )
    for word in _FORBIDDEN_WORDS:
        if word in content:
            issues.append(
                {
                    "severity": "high",
                    "issueType": "forbidden_rule",
                    "description": f"正文出现禁忌元素：{word}",
                    "evidence": word,
                    "suggestedFix": "删除该设定，改为现实家庭利益和信息差推动冲突。",
                }
            )
    if chapter.word_count and chapter.word_count < max(1200, int((project.target_words / max(project.target_chapters, 1)) * 0.35)):
        issues.append(
            {
                "severity": "medium",
                "issueType": "word_count_low",
                "description": "章节字数明显低于目标章节篇幅。",
                "evidence": str(chapter.word_count),
                "suggestedFix": "补足生活场景、利益变化和关系 tension。",
            }
        )
    lines = db.query(LongNovelConflictLine).filter(LongNovelConflictLine.project_id == project.id).all()
    if lines and not any((line.name and line.name in content) or (line.conflict_key and line.conflict_key in content) for line in lines):
        issues.append(
            {
                "severity": "medium",
                "issueType": "conflict_line_missing",
                "description": "本章未明显推进已登记的家庭矛盾线。",
                "evidence": "未命中 conflict line 名称或 key",
                "suggestedFix": "至少推进一条房产、养老、借钱、偏心或妯娌争夺相关矛盾线。",
            }
        )
    tail = content[-160:]
    if tail and not any(mark in tail for mark in ("？", "！", "?", "!", "没想到", "忽然", "电话", "消息", "门口")):
        issues.append(
            {
                "severity": "low",
                "issueType": "ending_hook_weak",
                "description": "章节结尾钩子偏弱。",
                "evidence": tail[:120],
                "suggestedFix": "结尾留下一个新信息、把柄、账目或关系变化。",
            }
        )
    return issues


def _compact_state_delta_for_continuity(data: dict[str, Any] | None) -> dict[str, Any]:
    source = data if isinstance(data, dict) else {}
    limits = {
        "events": 8,
        "canonFacts": 12,
        "familyMemberStateDeltas": 12,
        "objectStateChanges": 10,
        "conflictLineUpdates": 8,
        "continuityClaims": 10,
    }
    compact: dict[str, Any] = {
        "chapterSummary": _clip_prompt_value(source.get("chapterSummary"), 360),
    }
    for key, limit in limits.items():
        compact[key] = _clip_prompt_value(_as_list(source.get(key))[:limit], 360)
    return compact


def check_family_continuity(
    db: Session,
    project_id: UUID,
    chapter_number: int,
    user: User | None = None,
    *,
    context_pack: dict[str, Any] | None = None,
    extracted_state: dict[str, Any] | None = None,
) -> list[LongNovelContinuityIssue]:
    project = _get_project(db, project_id, user)
    chapter = (
        db.query(LongNovelChapter)
        .filter(
            LongNovelChapter.project_id == project.id,
            LongNovelChapter.chapter_number == chapter_number,
        )
        .first()
    )
    if chapter is None:
        raise FamilyLongNovelError("章节不存在，无法检查连续性")

    db.query(LongNovelContinuityIssue).filter(
        LongNovelContinuityIssue.project_id == project.id,
        LongNovelContinuityIssue.chapter_number == chapter_number,
    ).delete()
    context = copy.deepcopy(context_pack) if context_pack is not None else _build_chapter_context(db, project, chapter_number)
    validate_chapter_context_pack(context, chapter_number)
    _ensure_chapter_prompt_cache_warm(
        db,
        project_id=project.id,
        chapter_number=chapter_number,
        context_pack=context,
    )
    rule_issues = _rule_continuity_issues(db, project, chapter, context)
    prompt = render_prompt(
        ZHOULI_CONTINUITY_CHECK_PROMPT,
        chapter_context=_context_prompt_json(context, "continuity_check"),
        chapter_text=chapter.content,
    )
    audit_seed = {
        "deterministicCandidateIssues": rule_issues,
        "acceptedChapterStateDelta": _compact_state_delta_for_continuity(extracted_state),
    }
    prompt = (
        f"{prompt}\n\n连续性审校辅助数据（只用于核对，不得照抄扩写）：\n"
        f"{_canonical_json_dumps(audit_seed)}"
    )
    prompt = f"{prompt}\n\n{_CONTEXT_PACK_CHECK_RULES}\n\n{GENERAL_NOVEL_PROSE_GUARDRAILS}"
    prompt = _with_project_cache_prefix(context, prompt)
    try:
        data = _call_json(
            db,
            project_id=project.id,
            chapter_number=chapter_number,
            action="check_continuity",
            tier="cheap",
            prompt=prompt,
            max_output_tokens=max(6000, int(settings.LONG_NOVEL_CONTINUITY_MAX_OUTPUT_TOKENS or 10000)),
            prompt_cache_key=_chapter_prompt_cache_key(project.id, chapter_number),
            reasoning_effort=str(settings.LONG_NOVEL_CONTINUITY_REASONING_EFFORT or "high"),
        )
    except MalformedModelJsonError as exc:
        logger.warning(
            "continuity check JSON malformed; keeping rule-based issues project=%s chapter=%s error=%s",
            project.id,
            chapter_number,
            exc,
        )
        data = {"issues": []}
    all_issues = rule_issues + [
        item for item in _as_list(_get(data, "issues")) if isinstance(item, dict)
    ]
    saved: list[LongNovelContinuityIssue] = []
    for item in all_issues:
        description = _text(item, "description")
        if not description:
            continue
        issue = LongNovelContinuityIssue(
            project_id=project.id,
            chapter_number=chapter_number,
            severity=_text(item, "severity", default="low") or "low",
            issue_type=_text(item, "issueType", "issue_type"),
            description=description,
            evidence=_text(item, "evidence"),
            suggested_fix=_text(item, "suggestedFix", "suggested_fix"),
            status="open",
        )
        db.add(issue)
        saved.append(issue)
    db.commit()
    return saved


def quality_check_family_chapter(
    db: Session,
    project_id: UUID,
    chapter_number: int,
    user: User | None = None,
) -> dict[str, Any]:
    project = _get_project(db, project_id, user)
    chapter = (
        db.query(LongNovelChapter)
        .filter(LongNovelChapter.project_id == project.id, LongNovelChapter.chapter_number == chapter_number)
        .first()
    )
    if chapter is None:
        raise FamilyLongNovelError("章节不存在，无法质检")
    context = _build_chapter_context(db, project, chapter_number)
    validate_chapter_context_pack(context, chapter_number)
    prompt = render_prompt(
        ZHOULI_QUALITY_CHECK_PROMPT,
        chapter_context=_context_prompt_json(context, "quality_check"),
        chapter_text=chapter.content,
    )
    prompt = f"{prompt}\n\n{_CONTEXT_PACK_CHECK_RULES}\n\n{GENERAL_NOVEL_PROSE_GUARDRAILS}"
    prompt = _with_project_cache_prefix(context, prompt)
    try:
        return _call_json(
            db,
            project_id=project.id,
            chapter_number=chapter_number,
            action="quality_check",
            tier="cheap",
            prompt=prompt,
            max_output_tokens=8000,
            prompt_cache_key=_chapter_prompt_cache_key(project.id, chapter_number),
        )
    except MalformedModelJsonError as exc:
        logger.warning(
            "quality check JSON malformed; returning fallback report project=%s chapter=%s error=%s",
            project.id,
            chapter_number,
            exc,
        )
        return {
            "score": 0,
            "problems": ["quality check returned malformed JSON"],
            "rewriteSuggestions": [],
            "fallbackReason": "malformed_model_json",
        }


def repair_family_chapter(
    db: Session,
    project_id: UUID,
    chapter_number: int,
    user: User | None = None,
) -> LongNovelChapter:
    project = _get_project(db, project_id, user)
    chapter = (
        db.query(LongNovelChapter)
        .filter(LongNovelChapter.project_id == project.id, LongNovelChapter.chapter_number == chapter_number)
        .first()
    )
    if chapter is None:
        raise FamilyLongNovelError("章节不存在，无法修复")
    issues = (
        db.query(LongNovelContinuityIssue)
        .filter(
            LongNovelContinuityIssue.project_id == project.id,
            LongNovelContinuityIssue.chapter_number == chapter_number,
            LongNovelContinuityIssue.status == "open",
            LongNovelContinuityIssue.severity.in_(["high", "severe"]),
        )
        .all()
    )
    if not issues:
        return chapter
    context = _build_chapter_context(db, project, chapter_number)
    validate_chapter_context_pack(context, chapter_number)
    issues_payload = [
        {
            "severity": item.severity,
            "issueType": item.issue_type,
            "description": item.description,
            "evidence": item.evidence,
            "suggestedFix": item.suggested_fix,
        }
        for item in issues
    ]
    prompt = render_prompt(
        ZHOULI_REPAIR_PROMPT,
        chapter_context=_context_prompt_json(context, "repair"),
        issues_json=_json_dumps(issues_payload),
        chapter_text=chapter.content,
    )
    prompt = f"{prompt}\n\n{_CONTEXT_PACK_WRITE_RULES}\n\n{GENERAL_NOVEL_PROSE_GUARDRAILS}"
    prompt = _with_project_cache_prefix(context, prompt)
    repaired = _call_text(
        db,
        project_id=project.id,
        chapter_number=chapter_number,
        action="repair_chapter",
        tier="default",
        prompt=prompt,
        max_output_tokens=24000,
        prompt_cache_key=_chapter_prompt_cache_key(project.id, chapter_number),
    )
    chapter.content = repaired
    chapter.raw_model_output = repaired
    chapter.word_count = _word_count(repaired)
    chapter.version = (chapter.version or 1) + 1
    chapter.status = LongNovelChapterStatus.COMPLETED
    db.add(chapter)
    db.commit()
    extract_family_chapter_state(db, project.id, chapter_number, user)
    check_family_continuity(db, project.id, chapter_number, user)
    db.refresh(chapter)
    return chapter


def generate_family_batch(
    db: Session,
    project_id: UUID,
    req: FamilyLongNovelBatchRequest,
    user: User | None = None,
) -> dict[str, Any]:
    project = _get_project(db, project_id, user)
    _ensure_bible(db, project, user)
    if (
        db.query(LongNovelChapterPlan)
        .filter(LongNovelChapterPlan.project_id == project.id)
        .count()
        == 0
    ):
        generate_family_novel_outline(db, project.id, user)
    project.status = LongNovelProjectStatus.GENERATING
    db.add(project)
    db.commit()

    generated: list[int] = []
    failed: list[dict[str, Any]] = []
    skipped: list[int] = []
    start = min(req.from_chapter, req.to_chapter)
    end = max(req.from_chapter, req.to_chapter)
    for chapter_number in range(start, end + 1):
        db.refresh(project)
        if project.status == LongNovelProjectStatus.PAUSED:
            break
        existing = (
            db.query(LongNovelChapter)
            .filter(LongNovelChapter.project_id == project.id, LongNovelChapter.chapter_number == chapter_number)
            .first()
        )
        if existing and existing.status == LongNovelChapterStatus.COMPLETED:
            skipped.append(chapter_number)
            continue
        if existing and existing.status == LongNovelChapterStatus.FAILED and not req.retry_failed:
            skipped.append(chapter_number)
            continue
        try:
            generate_family_chapter(db, project.id, chapter_number, user)
            generated.append(chapter_number)
        except Exception as exc:
            logger.exception("family chapter generation failed project=%s chapter=%s", project.id, chapter_number)
            failed.append({"chapter_number": chapter_number, "error": str(exc)})
            failed_chapter = existing or LongNovelChapter(
                project_id=project.id,
                chapter_number=chapter_number,
                title=f"第{chapter_number}章",
                content="",
            )
            failed_chapter.status = LongNovelChapterStatus.FAILED
            db.add(failed_chapter)
            db.commit()
    db.refresh(project)
    if project.status != LongNovelProjectStatus.PAUSED:
        project.status = LongNovelProjectStatus.FAILED if failed and not generated else LongNovelProjectStatus.ACTIVE
    sync_family_project_progress(db, project)
    db.add(project)
    db.commit()
    return {
        "project_id": project.id,
        "status": project.status,
        "generated": generated,
        "failed": failed,
        "skipped": skipped,
    }


def pause_family_project(db: Session, project_id: UUID, user: User | None = None) -> LongNovelProject:
    project = _get_project(db, project_id, user)
    project.status = LongNovelProjectStatus.PAUSED
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def resume_family_project(db: Session, project_id: UUID, user: User | None = None) -> LongNovelProject:
    project = _get_project(db, project_id, user)
    project.status = LongNovelProjectStatus.ACTIVE
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def export_family_novel(db: Session, project_id: UUID, fmt: str, user: User | None = None) -> tuple[str, str, str]:
    project = _get_project(db, project_id, user)
    chapters = (
        db.query(LongNovelChapter)
        .filter(LongNovelChapter.project_id == project.id)
        .order_by(LongNovelChapter.chapter_number.asc())
        .all()
    )
    if fmt == "json":
        payload = {
            "project": _project_payload(project),
            "title": project.title,
            "bible": project.bible.raw_json if project.bible else None,
            "members": [item.raw_json for item in project.family_members],
            "relations": [item.raw_json for item in project.family_relations],
            "conflictLines": [
                {
                    "conflictKey": item.conflict_key,
                    "name": item.name,
                    "status": item.status,
                    "description": item.description,
                }
                for item in project.conflict_lines
            ],
            "chapters": [
                {
                    "chapterNumber": item.chapter_number,
                    "title": item.title,
                    "summary": item.summary,
                    "wordCount": item.word_count,
                    "content": item.content,
                }
                for item in chapters
            ],
        }
        return _json_dumps(payload), "application/json; charset=utf-8", f"{project.title}.json"

    if fmt == "txt":
        body = "\n\n".join([f"第{item.chapter_number}章 {item.title}\n\n{item.content}" for item in chapters])
        return f"{project.title}\n\n{body}", "text/plain; charset=utf-8", f"{project.title}.txt"

    lines: list[str] = [
        f"# {project.title}",
        "",
        f"类型：{project.genre_label}",
        "",
        "## 作品简介",
        "",
        project.bible.core_promise if project.bible and project.bible.core_promise else project.premise,
        "",
        "## 主要人物",
        "",
    ]
    for member in project.family_members:
        lines.append(f"- {member.name}（{member.family_role or '家庭成员'}）：{member.public_image or ''} {member.private_desire or ''}".strip())
    lines.extend(["", "## 家庭关系图", ""])
    for relation in project.family_relations:
        lines.append(f"- {relation.from_member} -> {relation.to_member}：{relation.relation_type or ''}，{relation.hidden_conflict or relation.surface_relation or ''}".strip())
    lines.extend(["", "## 分卷大纲", ""])
    plans = sorted(project.chapter_plans, key=lambda item: item.chapter_number)
    for plan in plans:
        lines.append(f"- 第{plan.chapter_number}章 {plan.title}：{plan.domestic_conflict or plan.chapter_goal or ''}".strip())
    lines.extend(["", "## 正文", ""])
    for chapter in chapters:
        lines.extend([f"### 第{chapter.chapter_number}章 {chapter.title}", "", chapter.content, ""])
    return "\n".join(lines), "text/markdown; charset=utf-8", f"{project.title}.md"
