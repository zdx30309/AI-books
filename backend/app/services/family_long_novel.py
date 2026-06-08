"""Service pipeline for family sisters-in-law long-form novels."""
from __future__ import annotations

import asyncio
import json
import logging
import re
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


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_ALNUM_RE = re.compile(r"[A-Za-z0-9]+")
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
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


def _json_loads(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    cleaned = _JSON_FENCE_RE.sub("", cleaned).strip()
    if not cleaned:
        raise FamilyLongNovelError("模型返回为空，无法解析 JSON")
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start : end + 1]
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise FamilyLongNovelError(f"模型返回不是合法 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise FamilyLongNovelError("模型 JSON 顶层必须是对象")
    return parsed


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
            )
        )
    raise FamilyLongNovelError("family long novel service must run from a sync worker thread")


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
) -> str:
    log = LongNovelGenerationLog(
        project_id=project_id,
        chapter_number=chapter_number,
        action=action,
        provider=settings.WRITER_PROVIDER,
        model=_model_for_tier(tier),
        prompt_tokens=max(1, len(prompt) // 4),
        status="started",
    )
    db.add(log)
    db.commit()
    try:
        output = _run_writer(prompt, max_output_tokens=max_output_tokens)
    except Exception as exc:
        log.status = "failed"
        log.error = str(exc)[:2000]
        db.add(log)
        db.commit()
        raise
    log.status = "completed"
    log.completion_tokens = max(1, len(output) // 4)
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
) -> str:
    return _log_llm_call(
        db,
        project_id=project_id,
        chapter_number=chapter_number,
        action=action,
        tier=tier,
        prompt=prompt,
        max_output_tokens=max_output_tokens,
    ).strip()


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


def generate_family_novel_bible(
    db: Session,
    project_id: UUID,
    user: User | None = None,
) -> LongNovelProject:
    project = _get_project(db, project_id, user)
    prompt = render_prompt(
        ZHOULI_BIBLE_PROMPT,
        project_payload=_json_dumps(_project_payload(project)),
    )
    data = _call_json(
        db,
        project_id=project.id,
        chapter_number=None,
        action="generate_bible",
        tier="strong",
        prompt=prompt,
        max_output_tokens=24000,
    )

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
    per_chapter = max(2500, int(project.target_words / max(project.target_chapters, 1)))
    return {
        "chapterNumber": chapter_number,
        "volumeNumber": max(1, (chapter_number - 1) // 12 + 1),
        "title": f"账要算清",
        "targetWords": min(4500, max(3000, per_chapter)),
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
    data = _call_json(
        db,
        project_id=project.id,
        chapter_number=None,
        action="generate_outline",
        tier="strong",
        prompt=prompt,
        max_output_tokens=32000,
    )
    chapters = [item for item in _as_list(_get(data, "chapters")) if isinstance(item, dict)]
    by_number = {
        _int_value(_get(item, "chapterNumber", "chapter_number"), 0): item
        for item in chapters
    }
    target = max(1, min(80, project.target_chapters or 50))
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
        plan.target_words = _int_value(_get(item, "targetWords", "target_words"), 4000)
        plan.chapter_goal = _text(item, "chapterGoal", "chapter_goal")
        plan.domestic_conflict = _text(item, "domesticConflict", "domestic_conflict")
        plan.emotional_hook = _text(item, "emotionalHook", "emotional_hook")
        plan.required_events = _as_list(_get(item, "requiredEvents", "required_events"))
        plan.character_arc_move = _as_list(_get(item, "characterArcMove", "character_arc_move"))
        plan.conflict_lines_to_advance = _as_list(_get(item, "conflictLinesToAdvance", "conflict_lines_to_advance"))
        plan.forbidden_reveals = _as_list(_get(item, "forbiddenReveals", "forbidden_reveals"))
        plan.ending_hook = _text(item, "endingHook", "ending_hook")
        plan.raw_json = item
        plan.status = LongNovelChapterStatus.PLANNED
        db.add(plan)
    project.status = LongNovelProjectStatus.ACTIVE
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def _latest_states(db: Session, project_id: UUID, chapter_number: int) -> list[dict[str, Any]]:
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
                "goal": row.goal,
                "emotion": row.emotion,
                "grievanceLevel": row.grievance_level,
                "knowledge": row.knowledge,
                "secrets": row.secrets,
                "currentLeverage": row.current_leverage,
            }
        )
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
                "holder": row.holder,
                "location": row.location,
                "knownBy": row.known_by,
                "status": row.status,
            }
        )
    return result


def _build_chapter_context(db: Session, project: LongNovelProject, chapter_number: int) -> dict[str, Any]:
    plan = (
        db.query(LongNovelChapterPlan)
        .filter(
            LongNovelChapterPlan.project_id == project.id,
            LongNovelChapterPlan.chapter_number == chapter_number,
        )
        .first()
    )
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
    conflict_lines = (
        db.query(LongNovelConflictLine)
        .filter(LongNovelConflictLine.project_id == project.id)
        .order_by(LongNovelConflictLine.importance.desc(), LongNovelConflictLine.id.asc())
        .all()
    )
    canon_facts = (
        db.query(LongNovelCanonFact)
        .filter(
            LongNovelCanonFact.project_id == project.id,
            LongNovelCanonFact.chapter_number < chapter_number,
        )
        .order_by(LongNovelCanonFact.chapter_number.desc())
        .limit(80)
        .all()
    )
    return {
        "project": _project_payload(project),
        "bible": project.bible.raw_json if project.bible else {},
        "chapterNumber": chapter_number,
        "currentPlan": plan.raw_json if plan else None,
        "currentPlanRecord": {
            "title": plan.title,
            "targetWords": plan.target_words,
            "chapterGoal": plan.chapter_goal,
            "domesticConflict": plan.domestic_conflict,
            "emotionalHook": plan.emotional_hook,
            "conflictLinesToAdvance": plan.conflict_lines_to_advance,
            "forbiddenReveals": plan.forbidden_reveals,
            "endingHook": plan.ending_hook,
            "scenes": plan.scenes,
        }
        if plan
        else None,
        "recentChapterSummaries": [
            {
                "chapterNumber": row.chapter_number,
                "title": row.title,
                "summary": row.summary,
            }
            for row in reversed(recent_chapters)
        ],
        "activeConflictLines": [
            {
                "conflictKey": row.conflict_key,
                "name": row.name,
                "type": row.conflict_type,
                "description": row.description,
                "status": row.status,
                "lastEscalatedChapter": row.last_escalated_chapter,
                "plannedExplosionChapter": row.planned_explosion_chapter,
                "plannedResolutionChapter": row.planned_resolution_chapter,
                "importance": row.importance,
            }
            for row in conflict_lines
        ],
        "latestCharacterStates": _latest_states(db, project.id, chapter_number),
        "latestObjectStates": _latest_object_states(db, project.id, chapter_number),
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
    }


def generate_family_chapter_plan(
    db: Session,
    project_id: UUID,
    chapter_number: int,
    user: User | None = None,
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

    context = _build_chapter_context(db, project, chapter_number)
    prompt = render_prompt(
        ZHOULI_CHAPTER_PLAN_PROMPT,
        chapter_context=_json_dumps(context),
    )
    data = _call_json(
        db,
        project_id=project.id,
        chapter_number=chapter_number,
        action="generate_chapter_plan",
        tier="default",
        prompt=prompt,
        max_output_tokens=16000,
    )
    plan.title = _clean_title(_text(data, "title", default=plan.title), plan.title)
    plan.chapter_goal = _text(data, "chapterGoal", "chapter_goal", default=plan.chapter_goal or "")
    plan.domestic_conflict = _text(data, "mainDomesticConflict", "domesticConflict", "domestic_conflict", default=plan.domestic_conflict or "")
    plan.emotional_hook = _text(data, "emotionalHook", "emotional_hook", default=plan.emotional_hook or "")
    plan.scenes = _as_list(_get(data, "scenes"))
    plan.required_events = _as_list(_get(data, "mustInclude", "requiredEvents")) or plan.required_events
    plan.forbidden_reveals = _as_list(_get(data, "mustNotReveal", "forbiddenReveals")) or plan.forbidden_reveals
    plan.raw_json = {**(plan.raw_json or {}), "chapterPlan": data}
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
    if plan is None or not plan.scenes:
        plan = generate_family_chapter_plan(db, project.id, chapter_number, user)

    project.status = LongNovelProjectStatus.GENERATING
    plan.status = LongNovelChapterStatus.GENERATING
    db.add(project)
    db.add(plan)
    db.commit()

    context = _build_chapter_context(db, project, chapter_number)
    prompt = render_prompt(
        ZHOULI_CHAPTER_WRITE_PROMPT,
        chapter_context=_json_dumps(context),
    )
    content = _call_text(
        db,
        project_id=project.id,
        chapter_number=chapter_number,
        action="generate_chapter",
        tier="default",
        prompt=prompt,
        max_output_tokens=24000,
    )
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
    chapter.status = LongNovelChapterStatus.COMPLETED
    plan.status = LongNovelChapterStatus.COMPLETED
    db.add(chapter)
    db.add(plan)
    db.commit()
    db.refresh(chapter)

    extract_family_chapter_state(db, project.id, chapter_number, user)
    issues = check_family_continuity(db, project.id, chapter_number, user)
    severe = [item for item in issues if item.severity in {"high", "severe"} and item.status == "open"]
    if severe and auto_repair and settings.LONG_NOVEL_MAX_CHAPTER_REPAIR_COUNT > 0:
        chapter = repair_family_chapter(db, project.id, chapter_number, user)

    total_words = int(
        db.query(func.coalesce(func.sum(LongNovelChapter.word_count), 0))
        .filter(LongNovelChapter.project_id == project.id)
        .scalar()
        or 0
    )
    project.total_words = total_words
    project.current_chapter = max(project.current_chapter or 0, chapter_number)
    project.status = (
        LongNovelProjectStatus.COMPLETED
        if project.current_chapter >= project.target_chapters
        else LongNovelProjectStatus.ACTIVE
    )
    db.add(project)
    db.commit()
    db.refresh(chapter)
    return chapter


def extract_family_chapter_state(
    db: Session,
    project_id: UUID,
    chapter_number: int,
    user: User | None = None,
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
    context = _build_chapter_context(db, project, chapter_number)
    prompt = render_prompt(
        ZHOULI_STATE_EXTRACT_PROMPT,
        chapter_context=_json_dumps(context),
        chapter_text=chapter.content,
    )
    data = _call_json(
        db,
        project_id=project.id,
        chapter_number=chapter_number,
        action="extract_chapter_state",
        tier="cheap",
        prompt=prompt,
        max_output_tokens=14000,
    )
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
    db.commit()
    return data


def _rule_continuity_issues(
    db: Session,
    project: LongNovelProject,
    chapter: LongNovelChapter,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    content = chapter.content or ""
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


def check_family_continuity(
    db: Session,
    project_id: UUID,
    chapter_number: int,
    user: User | None = None,
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
    rule_issues = _rule_continuity_issues(db, project, chapter)
    context = _build_chapter_context(db, project, chapter_number)
    prompt = render_prompt(
        ZHOULI_CONTINUITY_CHECK_PROMPT,
        chapter_context=_json_dumps(context),
        chapter_text=chapter.content,
    )
    data = _call_json(
        db,
        project_id=project.id,
        chapter_number=chapter_number,
        action="check_continuity",
        tier="cheap",
        prompt=prompt,
        max_output_tokens=12000,
    )
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
    prompt = render_prompt(
        ZHOULI_QUALITY_CHECK_PROMPT,
        chapter_context=_json_dumps(context),
        chapter_text=chapter.content,
    )
    return _call_json(
        db,
        project_id=project.id,
        chapter_number=chapter_number,
        action="quality_check",
        tier="cheap",
        prompt=prompt,
        max_output_tokens=8000,
    )


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
        chapter_context=_json_dumps(context),
        issues_json=_json_dumps(issues_payload),
        chapter_text=chapter.content,
    )
    repaired = _call_text(
        db,
        project_id=project.id,
        chapter_number=chapter_number,
        action="repair_chapter",
        tier="default",
        prompt=prompt,
        max_output_tokens=24000,
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
    total_words = int(
        db.query(func.coalesce(func.sum(LongNovelChapter.word_count), 0))
        .filter(LongNovelChapter.project_id == project.id)
        .scalar()
        or 0
    )
    project.total_words = total_words
    project.current_chapter = max(project.current_chapter or 0, max(generated or [project.current_chapter or 0]))
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
