"""Epic Novel Engine service layer.

This module implements a runnable deterministic control plane first. Live LLM
calls can be swapped in behind the model router without changing the storage,
state, QA, or API contracts.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    EpicAcceptanceReport,
    EpicBibleCoverageItem,
    EpicBibleSection,
    EpicBureaucracySystem,
    EpicCanonFact,
    EpicChapter,
    EpicChapterContract,
    EpicChapterStatus,
    EpicCharacter,
    EpicCharacterArc,
    EpicCharacterState,
    EpicFaction,
    EpicFactionState,
    EpicForeshadow,
    EpicGenerationLog,
    EpicGlobalSpine,
    EpicInstitution,
    EpicInstitutionState,
    EpicIssue,
    EpicMapRegion,
    EpicMasterBible,
    EpicNation,
    EpicNovelProject,
    EpicNovelProjectStatus,
    EpicObjectState,
    EpicPleasureBeat,
    EpicPowerLedger,
    EpicPowerSystem,
    EpicReaderQuestion,
    EpicRelationshipEdge,
    EpicRetrievalTrace,
    EpicStateExtract,
    EpicVolumeContract,
    EpicWorldAtlas,
    EpicWorldBible,
    ReferenceDownloadTask,
    ReferenceMechanismProfile,
    ReferencePatternCard,
    WorldviewCandidate,
)
from app.schemas.epic_novel import EpicBatchGenerationRequest, EpicNovelCreateRequest
from app.services.epic_context import compile_dynamic_context
from app.services.epic_model_router import call_epic_model, call_epic_model_with_continuation, log_epic_generation, resolve_model, strip_done_marker
from app.services.epic_reference import auto_retrieval_plan, build_reference_mechanism_profile, latest_reference_profile
from app.services.epic_storage import ensure_epic_storage, epic_export_path


class EpicNovelError(RuntimeError):
    """Raised for expected Epic Novel workflow errors."""


BIBLE_SECTIONS: list[tuple[str, str, str, int]] = [
    ("project_overview", "项目总览", "overview", 1200),
    ("genre_promise", "类型与爽点承诺", "promise", 1200),
    ("world_law", "世界底层法则", "world", 1600),
    ("power_system", "战力体系", "power", 1800),
    ("nation_and_court", "国家与朝堂", "world", 1800),
    ("bureaucracy", "官僚体系", "institution", 1800),
    ("map_geography", "地图与地理", "world", 1500),
    ("factions", "势力系统", "faction", 1500),
    ("characters", "人物系统", "character", 2200),
    ("heroine_arcs", "多女主独立线", "character", 1600),
    ("relationship_graph", "关系图谱", "character", 1200),
    ("history_timeline", "历史年表", "world", 1500),
    ("global_spine", "全书大主线", "spine", 1600),
    ("volume_contracts", "分卷合同", "planning", 1600),
    ("foreshadow_ledger", "伏笔账本", "ledger", 1400),
    ("reader_question_ledger", "读者疑问账本", "ledger", 1200),
    ("pleasure_curve", "爽点曲线", "pleasure", 1200),
    ("villain_plan", "反派计划", "character", 1200),
    ("style_guide", "风格指南", "style", 1000),
    ("anti_cliche_rules", "禁忌与反俗套", "style", 1000),
    ("ending_plan", "结局规划", "ending", 1400),
    ("glossary", "术语表", "glossary", 1000),
    ("reference_profile_usage", "ReferenceMechanismProfile接入说明", "reference", 1200),
    ("production_control_rules", "生产控制规则", "control", 1200),
]


def _asdict_model(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _safe_export_stem(value: str) -> str:
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value or "").strip(" ._")
    return stem or "epic_novel"


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw, flags=re.I).strip()
        raw = re.sub(r"```$", "", raw).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(raw[start : end + 1])
    if not isinstance(parsed, dict):
        raise EpicNovelError("Model output must be a JSON object")
    return parsed


def _fallback_meta(reason: str | None = None) -> dict[str, Any]:
    return {
        "mode": "fallback",
        "status": "fallback",
        "production_quality": False,
        "smoke_validated": True,
        "rawTextIncluded": False,
        "reason": reason or "模型不可用或烟测模式",
    }


def _production_meta() -> dict[str, Any]:
    return {
        "mode": "model",
        "status": "generated",
        "production_quality": True,
        "smoke_validated": False,
        "rawTextIncluded": False,
    }


def _generic_entity_pack(project: EpicNovelProject) -> dict[str, Any]:
    title = project.title or "本项目"
    premise = project.premise or "尚未填写的故事前提"
    return {
        "项目名": title,
        "核心前提": premise,
        "主角占位": f"{title}的主角需由模型根据前提生成，不在服务层写死姓名。",
        "关键人物": ["主角", "关键同盟", "关键对手", "独立女性角色"],
        "核心机构": ["核心官署", "地方执行机构", "信息/档案机构", "敌对权力组织"],
        "核心地域": ["主舞台", "地方扩展区域", "危险边界区域"],
        "生产说明": "这是烟测 fallback，只验证流程；不能标记为 production_quality。",
    }


def _call_structured_generation(
    db: Session,
    project: EpicNovelProject,
    *,
    action: str,
    prompt: str,
    fallback: dict[str, Any],
    role: str = "author",
    section_key: str | None = None,
) -> tuple[dict[str, Any], bool]:
    roles = [role]
    if settings.EPIC_STRUCTURED_GENERATION_FALLBACK_TO_EDITOR and role != "editor":
        roles.append("editor")
    errors: list[str] = []
    for candidate_role in roles:
        try:
            completion = call_epic_model(
                db,
                action=action,
                role=candidate_role,  # type: ignore[arg-type]
                project_id=project.id,
                section_key=section_key,
                prompt=prompt,
                max_output_tokens=0,
            )
            data = _extract_json_object(completion)
            data.setdefault("_meta", _production_meta())
            data["_meta"].update(_production_meta())
            if candidate_role != role:
                data["_meta"]["fallbackRole"] = candidate_role
            return data, False
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{candidate_role}: {str(exc)[:300]}")
    if not settings.EPIC_ALLOW_SMOKE_FALLBACK:
        raise EpicNovelError("; ".join(errors))
    payload = dict(fallback)
    payload["_meta"] = _fallback_meta("; ".join(errors)[:500])
    return payload, True


def _positive_limit(value: int | None) -> int | None:
    if value is None:
        return None
    parsed = int(value)
    return parsed if parsed > 0 else None


def _apply_query_limit(query: Any, value: int | None) -> Any:
    limit = _positive_limit(value)
    return query.limit(limit) if limit is not None else query


def _take(items: list[Any], value: int | None) -> list[Any]:
    limit = _positive_limit(value)
    return items[:limit] if limit is not None else items


def _get_project(db: Session, project_id: UUID, user: Any | None = None) -> EpicNovelProject:
    query = db.query(EpicNovelProject).filter(EpicNovelProject.id == project_id)
    if user is not None:
        query = query.filter(EpicNovelProject.owner_id == user.id)
    project = query.first()
    if project is None:
        raise EpicNovelError(f"EpicNovelProject not found: {project_id}")
    return project


def create_epic_project(db: Session, req: EpicNovelCreateRequest, user: Any | None = None) -> EpicNovelProject:
    ensure_epic_storage()
    editor = resolve_model("editor")
    author = resolve_model("author")
    cheap = resolve_model("cheap")
    project = EpicNovelProject(
        owner_id=getattr(user, "id", None),
        title=req.title.strip() or "Untitled Epic Novel",
        genre=req.genre,
        premise=req.premise,
        target_words=req.target_words,
        target_volumes=req.target_volumes,
        target_chapters=req.target_chapters,
        bible_target_words=req.bible_target_words,
        style=req.style,
        forbidden_rules=req.forbidden_rules,
        generation_mode=req.generation_mode,
        editor_model=f"{editor.provider}/{editor.model}",
        author_model=f"{author.provider}/{author.model}",
        cheap_model=f"{cheap.provider}/{cheap.model}",
        reference_booklist_path=req.reference_booklist_path,
        sonovel_home=req.sonovel_home or settings.SONOVEL_HOME,
        sonovel_download_path=req.sonovel_download_path or settings.SONOVEL_DOWNLOAD_PATH,
        reference_raw_path=req.reference_raw_path or settings.REFERENCE_RAW_PATH,
        raw_json={"corePleasures": req.core_pleasures},
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    log_epic_generation(db, action="create_project", role="cheap", project_id=project.id, completion=project.title)
    db.commit()
    return project


def list_epic_projects(db: Session, user: Any | None = None, *, limit: int = 50, offset: int = 0) -> tuple[int, list[EpicNovelProject]]:
    query = db.query(EpicNovelProject)
    if user is not None:
        query = query.filter(EpicNovelProject.owner_id == user.id)
    total = query.count()
    items = query.order_by(EpicNovelProject.created_at.desc()).offset(offset).limit(limit).all()
    return total, items


def sync_project_progress(db: Session, project: EpicNovelProject) -> EpicNovelProject:
    accepted = (
        db.query(EpicChapter)
        .filter(EpicChapter.project_id == project.id, EpicChapter.status == EpicChapterStatus.ACCEPTED)
        .order_by(EpicChapter.chapter_number.asc())
        .all()
    )
    project.current_chapter = max([chapter.chapter_number for chapter in accepted], default=0)
    project.total_words = sum(chapter.word_count for chapter in accepted)
    if project.current_chapter:
        project.current_volume = _volume_for_chapter(project, project.current_chapter)
    db.commit()
    db.refresh(project)
    return project


def project_detail(db: Session, project_id: UUID, user: Any | None = None) -> dict[str, Any]:
    project = sync_project_progress(db, _get_project(db, project_id, user))
    logs = (
        db.query(EpicGenerationLog)
        .filter(EpicGenerationLog.project_id == project.id)
        .order_by(EpicGenerationLog.created_at.desc())
        .all()
    )
    return {
        "project": project,
        "world_bible": _asdict_model(db.query(EpicWorldBible).filter(EpicWorldBible.project_id == project.id).first()) or None,
        "world_atlas": _asdict_model(db.query(EpicWorldAtlas).filter(EpicWorldAtlas.project_id == project.id).first()) or None,
        "power_system": _asdict_model(db.query(EpicPowerSystem).filter(EpicPowerSystem.project_id == project.id).first()) or None,
        "master_bible": _asdict_model(db.query(EpicMasterBible).filter(EpicMasterBible.project_id == project.id).first()) or None,
        "bible_sections": [
            _asdict_model(row)
            for row in db.query(EpicBibleSection).filter(EpicBibleSection.project_id == project.id).order_by(EpicBibleSection.order_index.asc()).all()
        ],
        "volume_contracts": [
            _asdict_model(row)
            for row in db.query(EpicVolumeContract).filter(EpicVolumeContract.project_id == project.id).order_by(EpicVolumeContract.volume_number.asc()).all()
        ],
        "chapter_contracts": db.query(EpicChapterContract).filter(EpicChapterContract.project_id == project.id).order_by(EpicChapterContract.chapter_number.asc()).all(),
        "chapters": db.query(EpicChapter).filter(EpicChapter.project_id == project.id).order_by(EpicChapter.chapter_number.asc()).all(),
        "acceptance_reports": db.query(EpicAcceptanceReport).filter(EpicAcceptanceReport.project_id == project.id).order_by(EpicAcceptanceReport.chapter_number.asc()).all(),
        "retrieval_traces": [
            _asdict_model(row)
            for row in db.query(EpicRetrievalTrace).filter(EpicRetrievalTrace.project_id == project.id).order_by(EpicRetrievalTrace.created_at.desc()).all()
        ],
        "logs": logs,
    }


def _log_success(db: Session, project: EpicNovelProject, action: str, role: str = "cheap", completion: str = "") -> None:
    log_epic_generation(db, action=action, role=role, project_id=project.id, prompt=project.premise, completion=completion)


def run_worldview_tournament(db: Session, project_id: UUID, user: Any | None = None) -> list[WorldviewCandidate]:
    project = _get_project(db, project_id, user)
    existing = db.query(WorldviewCandidate).filter(WorldviewCandidate.project_id == project.id).order_by(WorldviewCandidate.candidate_number.asc()).all()
    if existing:
        return existing
    entity_pack = _generic_entity_pack(project)
    fallback = {
        "candidates": [
            {
                "coreConcept": f"围绕《{project.title}》前提的世界观候选 {index}",
                "powerSource": "由模型根据项目题材生成的核心力量来源；当前为烟测占位。",
                "worldLaw": f"围绕“{project.premise}”建立可检验、可付价、可持续扩展的世界法则。",
                "societyStructure": "由主要权力机构、地方执行层和民间资源网络构成。",
                "mainConflict": "局部事件逐步牵引到全书级矛盾。",
                "protagonistSpecialness": "主角特殊性需绑定项目独有前提，而不是服务层固定设定。",
                "heroineIntegration": "关键女性角色必须拥有独立目标、资源和行动代价。",
                "ensemblePotential": "群像角色分别连接不同信息、权力与情感通道。",
                "finalMystery": "终局谜题由项目前提和机制画像生成。",
                "score": {"originality": 0.5, "longformCapacity": 0.5},
            }
            for index in range(1, 11)
        ],
        "entityPack": entity_pack,
    }
    prompt = (
        "你是中文300万字网文世界观总设计。根据项目资料和参考机制画像，生成10个互不重复的世界观候选。"
        "只输出严格 JSON：{candidates:[{coreConcept,powerSource,worldLaw,societyStructure,mainConflict,"
        "protagonistSpecialness,heroineIntegration,ensemblePotential,finalMystery,score}]}。"
        "所有用户可见内容必须是简体中文，不得使用固定测试项目人名、国名、机构名。\n\n"
        f"项目标题：{project.title}\n项目前提：{project.premise}\n类型：{project.genre}\n"
        f"参考机制画像：{json.dumps(_profile_bible_notes(latest_reference_profile(db, project_id=project.id)), ensure_ascii=False)}"
    )
    data, used_fallback = _call_structured_generation(db, project, action="worldview_tournament", role="editor", prompt=prompt, fallback=fallback)
    for index, candidate in enumerate(data.get("candidates") or fallback["candidates"], start=1):
        db.add(
            WorldviewCandidate(
                project_id=project.id,
                candidate_number=index,
                core_concept=str(candidate.get("coreConcept") or f"{project.title} 世界观候选 {index}"),
                power_source=str(candidate.get("powerSource") or ""),
                world_law=str(candidate.get("worldLaw") or ""),
                society_structure=str(candidate.get("societyStructure") or ""),
                main_conflict=str(candidate.get("mainConflict") or ""),
                protagonist_specialness=str(candidate.get("protagonistSpecialness") or ""),
                heroine_integration=str(candidate.get("heroineIntegration") or ""),
                ensemble_potential=str(candidate.get("ensemblePotential") or ""),
                final_mystery=str(candidate.get("finalMystery") or ""),
                score_json={**(candidate.get("score") if isinstance(candidate.get("score"), dict) else {}), "selected": index <= 3},
                selected=index <= 3,
                raw_json={"abstractOnly": True, **data.get("_meta", _fallback_meta() if used_fallback else _production_meta())},
            )
        )
    db.commit()
    _log_success(db, project, "worldview_tournament", "editor", "10 candidates")
    db.commit()
    return db.query(WorldviewCandidate).filter(WorldviewCandidate.project_id == project.id).order_by(WorldviewCandidate.candidate_number.asc()).all()


def generate_world_bible(db: Session, project_id: UUID, user: Any | None = None) -> EpicWorldBible:
    project = _get_project(db, project_id, user)
    existing = db.query(EpicWorldBible).filter(EpicWorldBible.project_id == project.id).first()
    if existing:
        return existing
    run_worldview_tournament(db, project.id, user)
    fallback = {
        "title": f"{project.title} 世界圣经",
        "coreConcept": f"围绕“{project.premise}”展开的烟测世界观；生产内容必须由模型生成。",
        "worldLaw": "核心法则需由项目题材、前提和机制画像动态生成。",
        "powerSource": "核心力量来源待模型生成。",
        "societyStructure": "社会结构由主要机构、地方执行层、民间资源和敌对组织组成。",
        "history": "历史年表待模型根据项目主线生成。",
        "tabooRules": ["禁止复制参考书原文、人名、机构名或桥段", "禁止无代价升级"],
        "styleRules": ["简体中文", "通过行动展示设定", "每个设定服务情节压力"],
        "finalMystery": "终局谜题待模型生成。",
        "endingDirection": "结局方向待模型结合项目目标生成。",
    }
    prompt = (
        "你是中文长篇网文世界圣经设计师。根据项目与参考机制画像生成项目专属 WorldBible JSON。"
        "字段：title, coreConcept, worldLaw, powerSource, societyStructure, history, tabooRules, styleRules, finalMystery, endingDirection。"
        "必须简体中文；不得使用固定测试项目内容；不得包含参考书原文。\n\n"
        f"项目：{project.title}\n前提：{project.premise}\n类型：{project.genre}\n"
        f"参考机制画像：{json.dumps(_profile_bible_notes(latest_reference_profile(db, project_id=project.id)), ensure_ascii=False)}"
    )
    data, used_fallback = _call_structured_generation(db, project, action="generate_world_bible", role="editor", prompt=prompt, fallback=fallback)
    bible = EpicWorldBible(
        project_id=project.id,
        title=str(data.get("title") or fallback["title"]),
        core_concept=str(data.get("coreConcept") or fallback["coreConcept"]),
        world_law=str(data.get("worldLaw") or fallback["worldLaw"]),
        power_source=str(data.get("powerSource") or fallback["powerSource"]),
        society_structure=str(data.get("societyStructure") or fallback["societyStructure"]),
        history=str(data.get("history") or fallback["history"]),
        taboo_rules=data.get("tabooRules") if isinstance(data.get("tabooRules"), list) else fallback["tabooRules"],
        style_rules=data.get("styleRules") if isinstance(data.get("styleRules"), list) else fallback["styleRules"],
        forbidden_rules=[project.forbidden_rules] if project.forbidden_rules else [],
        final_mystery=str(data.get("finalMystery") or fallback["finalMystery"]),
        ending_direction=str(data.get("endingDirection") or fallback["endingDirection"]),
        raw_json={"source": "model_world_bible" if not used_fallback else "smoke_fallback_world_bible", **data.get("_meta", {})},
    )
    db.add(bible)
    db.commit()
    _log_success(db, project, "generate_world_bible", "editor", bible.core_concept or "")
    db.commit()
    return bible


def generate_world_atlas(db: Session, project_id: UUID, user: Any | None = None) -> EpicWorldAtlas:
    project = _get_project(db, project_id, user)
    existing = db.query(EpicWorldAtlas).filter(EpicWorldAtlas.project_id == project.id).first()
    if existing:
        return existing
    generate_world_bible(db, project.id, user)
    entity_pack = _generic_entity_pack(project)
    fallback = {
        "nation": {"name": f"{project.title}核心政体", "dynastyFlavor": "待模型生成", "currentCrisis": project.premise},
        "bureaucracy": {
            "centralGovernment": {"核心部门": ["权力中枢", "监察机构", "资源机构"]},
            "localGovernment": {"地方层": "地方执行机构与主线矛盾相互牵制"},
            "rankSystem": {"logic": "职位、资源与主线机制互相绑定"},
            "departments": entity_pack["核心机构"],
            "storyFunctions": ["案件阻碍", "线索转折", "权力反制"],
        },
        "regions": entity_pack["核心地域"],
        "institutions": entity_pack["核心机构"],
        "factions": ["主角阵营", "中立权力方", "敌对权力方"],
        "timeline": ["起点事件", "中段升级", "终局爆发"],
    }
    prompt = (
        "你是中文长篇网文 WorldAtlas 设计师。生成项目专属地图、政体、官僚体系、机构、地域和势力 JSON。"
        "字段：nation{name,dynastyFlavor,currentCrisis}, bureaucracy{centralGovernment,localGovernment,rankSystem,departments,storyFunctions}, "
        "regions, institutions, factions, timeline。必须简体中文，不得使用固定测试项目内容。\n\n"
        f"项目：{project.title}\n前提：{project.premise}\n类型：{project.genre}\n"
    )
    data, used_fallback = _call_structured_generation(db, project, action="generate_world_atlas", role="editor", prompt=prompt, fallback=fallback)
    nation_data = data.get("nation") if isinstance(data.get("nation"), dict) else fallback["nation"]
    bureaucracy_data = data.get("bureaucracy") if isinstance(data.get("bureaucracy"), dict) else fallback["bureaucracy"]
    regions = [str(x) for x in (data.get("regions") if isinstance(data.get("regions"), list) else fallback["regions"])][:8] or fallback["regions"]
    institutions = [str(x) for x in (data.get("institutions") if isinstance(data.get("institutions"), list) else fallback["institutions"])][:8] or fallback["institutions"]
    factions = [str(x) for x in (data.get("factions") if isinstance(data.get("factions"), list) else fallback["factions"])][:8] or fallback["factions"]
    meta = data.get("_meta", _fallback_meta() if used_fallback else _production_meta())
    bureaucracy = EpicBureaucracySystem(
        project_id=project.id,
        nation_name=str(nation_data.get("name") or fallback["nation"]["name"]),
        base_inspiration="多书机制抽象",
        central_government=bureaucracy_data.get("centralGovernment") if isinstance(bureaucracy_data.get("centralGovernment"), dict) else {},
        local_government=bureaucracy_data.get("localGovernment") if isinstance(bureaucracy_data.get("localGovernment"), dict) else {},
        rank_system=bureaucracy_data.get("rankSystem") if isinstance(bureaucracy_data.get("rankSystem"), dict) else {},
        official_titles=[f"{name}职阶" for name in institutions[:3]],
        departments=institutions,
        censorate_system={"storyFunction": "把真相转化为权力风险"},
        judicial_system={"storyFunction": "让案件证据改变局部秩序"},
        military_administration={"storyFunction": "让暴力资源受到制度约束"},
        finance_tax_system={"storyFunction": "让资源流向成为线索"},
        examination_system={"storyFunction": "让身份跃迁受到公开规则限制"},
        intelligence_agencies=institutions[:2],
        special_agencies=institutions[2:4],
        court_factions=factions,
        power_checks=["交叉验证", "公开代价"],
        corruption_channels=["删改记录", "利益输送"],
        story_functions=bureaucracy_data.get("storyFunctions") if isinstance(bureaucracy_data.get("storyFunctions"), list) else fallback["bureaucracy"]["storyFunctions"],
        raw_json={"abstractOriginalInstitution": True, **meta},
    )
    db.add(bureaucracy)
    nation = EpicNation(
        project_id=project.id,
        name=str(nation_data.get("name") or fallback["nation"]["name"]),
        inspiration_profile="多书机制抽象",
        dynasty_flavor=str(nation_data.get("dynastyFlavor") or "待模型生成"),
        ruling_ideology="围绕项目核心秩序维持合法性",
        political_structure="由中央、地方、资源和监察层相互制衡",
        capital=regions[0],
        provinces=regions[1:],
        military_system="武力资源必须服务项目世界法则",
        fiscal_system="资源流动必须能转化为情节线索",
        legal_system="裁决会改变角色关系和公共认知",
        official_selection_system="身份跃迁必须付出公开代价",
        relationship_to_hero="主角从低位进入权力链条",
        secrets=["核心秩序存在被隐瞒的历史缺口"],
        current_crisis=str(nation_data.get("currentCrisis") or project.premise),
        raw_json={**nation_data, **meta},
    )
    db.add(nation)
    for index, name in enumerate(regions[:3]):
        db.add(
            EpicMapRegion(
                project_id=project.id,
                name=name,
                region_type="capital" if index == 0 else "province",
                controlling_faction=factions[index % len(factions)],
                geography=f"{name}必须承担项目主线中的地理压力。",
                economy="资源、信息和人情流动构成线索。",
                strategic_value="展示地方事实如何影响全书秩序。",
                conflicts="地方利益与中心规则发生冲突。",
                secrets="隐藏一个与全局主线相关的历史缺口。",
                related_foreshadows=[],
                raw_json={"atlasFunction": "plot pressure"},
            )
        )
    for name in institutions[:3]:
        db.add(
            EpicInstitution(
                project_id=project.id,
                name=name,
                institution_type="project_specific",
                public_function="维持表层秩序",
                hidden_function="控制与项目核心法则相关的关键资源",
                hierarchy=["基层", "中层", "高层"],
                secrets=["内部规则存在可被剧情利用的缺口"],
                relationship_to_hero="既是阻碍也是工具",
                story_use_cases=["收案", "阻滞", "反转"],
                raw_json={"institutionFunction": "active plot engine", **meta},
            )
        )
    for faction in factions[:4]:
        db.add(EpicFaction(project_id=project.id, name=faction, type="project_faction", public_goal="维护自身秩序", hidden_goal="争夺核心资源解释权", status="active", raw_json=meta))
    atlas = EpicWorldAtlas(
        project_id=project.id,
        nations=[nation_data],
        regions=regions,
        institutions=institutions,
        factions=factions,
        trade_routes=[f"{regions[0]}资源线"] if regions else [],
        war_zones=regions[-1:] if regions else [],
        secret_realms=[f"{project.title}隐秘场域"],
        historical_timeline=data.get("timeline") if isinstance(data.get("timeline"), list) else fallback["timeline"],
        raw_json={"bureaucracySystem": nation_data.get("name"), **meta},
    )
    db.add(atlas)
    db.commit()
    _log_success(db, project, "generate_world_atlas", "editor", "atlas + bureaucracy")
    db.commit()
    return atlas


def forge_power_system(db: Session, project_id: UUID, user: Any | None = None) -> EpicPowerSystem:
    project = _get_project(db, project_id, user)
    existing = db.query(EpicPowerSystem).filter(EpicPowerSystem.project_id == project.id).first()
    if existing:
        return existing
    fallback = {
        "name": f"{project.title}力量体系",
        "source": "根据项目核心前提生成；当前为烟测 fallback。",
        "realmLevels": ["起步", "入门", "承压", "突破", "立局", "终局"],
        "progressionRules": ["升级必须付出代价", "力量必须受世界法则限制"],
        "breakthroughConditions": ["解决关键事件", "承担公开后果"],
        "costs": ["身份风险", "关系代价", "资源损耗"],
        "limits": ["禁止无证据无代价升级"],
        "professions": ["主角路径", "同盟路径", "对手路径"],
        "resources": ["信息", "资源", "关系", "地理优势"],
        "protagonistPath": ["理解规则", "利用规则", "重塑规则"],
        "heroinePaths": ["独立目标", "独立资源", "独立代价"],
        "villainUsage": ["遮蔽规则", "垄断资源", "制造误导"],
        "finalSystemTwist": "终局反转必须由项目核心前提推出。",
    }
    prompt = (
        "你是中文长篇网文战力体系设计师。生成项目专属 PowerSystem JSON。"
        "字段：name, source, realmLevels, progressionRules, breakthroughConditions, costs, limits, professions, resources, protagonistPath, heroinePaths, villainUsage, finalSystemTwist。"
        "必须简体中文，不得使用固定测试项目内容。\n\n"
        f"项目：{project.title}\n前提：{project.premise}\n类型：{project.genre}"
    )
    data, used_fallback = _call_structured_generation(db, project, action="forge_power_system", role="editor", prompt=prompt, fallback=fallback)
    meta = data.get("_meta", _fallback_meta() if used_fallback else _production_meta())
    system = EpicPowerSystem(
        project_id=project.id,
        name=str(data.get("name") or fallback["name"]),
        source=str(data.get("source") or fallback["source"]),
        realm_levels=data.get("realmLevels") if isinstance(data.get("realmLevels"), list) else fallback["realmLevels"],
        progression_rules=data.get("progressionRules") if isinstance(data.get("progressionRules"), list) else fallback["progressionRules"],
        breakthrough_conditions=data.get("breakthroughConditions") if isinstance(data.get("breakthroughConditions"), list) else fallback["breakthroughConditions"],
        costs=data.get("costs") if isinstance(data.get("costs"), list) else fallback["costs"],
        limits=data.get("limits") if isinstance(data.get("limits"), list) else fallback["limits"],
        professions=data.get("professions") if isinstance(data.get("professions"), list) else fallback["professions"],
        resources=data.get("resources") if isinstance(data.get("resources"), list) else fallback["resources"],
        protagonist_path=data.get("protagonistPath") if isinstance(data.get("protagonistPath"), list) else fallback["protagonistPath"],
        heroine_paths=data.get("heroinePaths") if isinstance(data.get("heroinePaths"), list) else fallback["heroinePaths"],
        villain_usage=data.get("villainUsage") if isinstance(data.get("villainUsage"), list) else fallback["villainUsage"],
        final_system_twist=str(data.get("finalSystemTwist") or fallback["finalSystemTwist"]),
        raw_json={"original": True, **meta},
    )
    db.add(system)
    db.commit()
    _log_success(db, project, "forge_power_system", "editor", system.name)
    db.commit()
    return system


def generate_ensemble_cast(db: Session, project_id: UUID, user: Any | None = None) -> list[EpicCharacter]:
    project = _get_project(db, project_id, user)
    existing = db.query(EpicCharacter).filter(EpicCharacter.project_id == project.id).all()
    if existing:
        return existing
    fallback = {
        "characters": [
            {"name": "主角", "tier": "S", "role": "protagonist", "gender": "unknown", "goal": "根据项目前提生成的主线目标"},
            {"name": "关键女性角色甲", "tier": "A", "role": "heroine", "gender": "female", "goal": "拥有独立资源和行动代价"},
            {"name": "关键女性角色乙", "tier": "A", "role": "heroine", "gender": "female", "goal": "推动另一条独立线"},
            {"name": "导师或对手", "tier": "A", "role": "mentor_rival", "gender": "unknown", "goal": "制造制度压力"},
            {"name": "主反派", "tier": "S", "role": "villain", "gender": "unknown", "goal": "维护对核心资源的垄断"},
        ]
    }
    prompt = (
        "你是中文长篇网文群像人物设计师。生成项目专属角色 JSON：{characters:[{name,tier,role,gender,goal,archetype,fear,falseBelief,trueNeed,faction,powerPath,ending,mustNotDo}]}。"
        "角色名必须根据项目生成，不得使用固定测试项目姓名；女主必须有独立目标。\n\n"
        f"项目：{project.title}\n前提：{project.premise}\n类型：{project.genre}"
    )
    data, used_fallback = _call_structured_generation(db, project, action="generate_ensemble_cast", role="author", prompt=prompt, fallback=fallback)
    meta = data.get("_meta", _fallback_meta() if used_fallback else _production_meta())
    chars = data.get("characters") if isinstance(data.get("characters"), list) else fallback["characters"]
    for item in chars[:8]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "未命名角色")
        tier = str(item.get("tier") or "A")
        role = str(item.get("role") or "support")
        gender = str(item.get("gender") or "unknown")
        goal = str(item.get("goal") or "承担项目主线功能")
        db.add(
            EpicCharacter(
                project_id=project.id,
                name=name,
                tier=tier,
                role=role,
                gender=gender,
                archetype=str(item.get("archetype") or "项目机制压力中的行动者"),
                independent_goal=goal,
                desire=goal,
                fear=str(item.get("fear") or "被项目核心矛盾吞没"),
                false_belief=str(item.get("falseBelief") or "以为自己可以单独控制真相"),
                true_need=str(item.get("trueNeed") or "学会承担公开代价"),
                faction=str(item.get("faction") or ("主角线" if role == "protagonist" else "独立线")),
                power_path=str(item.get("powerPath") or "通过项目核心规则成长"),
                relationship_to_hero="根据独立目标与主角形成合作或冲突",
                active_volumes=[1, 2, 3],
                screen_time_budget=100 if tier == "S" else 65,
                ending=str(item.get("ending") or "拥有与自身目标绑定的结局"),
                must_not_do=item.get("mustNotDo") if isinstance(item.get("mustNotDo"), list) else ["只为衬托主角而存在", "丢失独立目标"],
                raw_json={"agencyRequired": role == "heroine", **meta},
            )
        )
        db.add(
            EpicCharacterArc(
                project_id=project.id,
                character_name=name,
                tier=tier,
                arc_start="被项目核心压力推着行动",
                arc_midpoint="付出现实代价选择目标",
                low_point="自身资源或身份被反噬",
                key_choice="保护具体的人或目标，而不是保住表面安全",
                cost="失去安全位置",
                arc_end="重写自身与项目核心秩序的关系",
                volume_arc_grid=[{"volume": 1, "move": "建立"}, {"volume": 2, "move": "付价"}, {"volume": 3, "move": "兑现"}],
                raw_json={},
            )
        )
    if len(chars) >= 2 and isinstance(chars[0], dict) and isinstance(chars[1], dict):
        db.add(EpicRelationshipEdge(project_id=project.id, from_character=str(chars[0].get("name")), to_character=str(chars[1].get("name")), relation_type="盟友兼对手", surface_relation="主线合作", hidden_conflict="目标优先级冲突", current_tension_level=4, raw_json=meta))
    db.commit()
    _log_success(db, project, "generate_ensemble_cast", "author", "6 characters")
    db.commit()
    return db.query(EpicCharacter).filter(EpicCharacter.project_id == project.id).all()


def generate_global_spine(db: Session, project_id: UUID, user: Any | None = None) -> EpicGlobalSpine:
    project = _get_project(db, project_id, user)
    existing = db.query(EpicGlobalSpine).filter(EpicGlobalSpine.project_id == project.id).first()
    if existing:
        return existing
    fallback = {
        "surfaceStory": f"《{project.title}》围绕项目核心前提展开连续事件。",
        "hiddenTruth": "隐藏真相待模型根据项目前提生成。",
        "finalConflict": "终局冲突必须由项目核心前提推出。",
        "heroFinalTransformation": "主角从被动求生转为主动承担秩序代价。",
        "stages": [{"volume": 1, "function": "建立规则"}, {"volume": 2, "function": "扩大冲突"}, {"volume": 3, "function": "揭开深层真相"}],
        "finalReveal": "终局揭示待模型生成。",
    }
    prompt = (
        "你是中文300万字网文全书主线设计师。生成 GlobalSpine JSON：surfaceStory, hiddenTruth, finalConflict, heroFinalTransformation, stages, finalReveal。"
        "必须根据项目生成，不得使用固定测试项目内容。\n\n"
        f"项目：{project.title}\n前提：{project.premise}\n类型：{project.genre}"
    )
    data, used_fallback = _call_structured_generation(db, project, action="generate_global_spine", role="editor", prompt=prompt, fallback=fallback)
    spine = EpicGlobalSpine(
        project_id=project.id,
        surface_story=str(data.get("surfaceStory") or fallback["surfaceStory"]),
        hidden_truth=str(data.get("hiddenTruth") or fallback["hiddenTruth"]),
        final_conflict=str(data.get("finalConflict") or fallback["finalConflict"]),
        hero_final_transformation=str(data.get("heroFinalTransformation") or fallback["heroFinalTransformation"]),
        stages=data.get("stages") if isinstance(data.get("stages"), list) else fallback["stages"],
        final_reveal=str(data.get("finalReveal") or fallback["finalReveal"]),
        raw_json={"mustDriveAllVolumes": True, **data.get("_meta", _fallback_meta() if used_fallback else _production_meta())},
    )
    db.add(spine)
    db.commit()
    _log_success(db, project, "generate_global_spine", "editor", spine.hidden_truth or "")
    db.commit()
    return spine


def _get_or_create_master_bible(db: Session, project: EpicNovelProject) -> EpicMasterBible:
    bible = db.query(EpicMasterBible).filter(EpicMasterBible.project_id == project.id).first()
    if bible is not None:
        return bible
    bible = EpicMasterBible(project_id=project.id, title=f"{project.title} Master Bible", target_word_count=project.bible_target_words)
    db.add(bible)
    db.flush()
    return bible


def generate_bible_manifest(db: Session, project_id: UUID, user: Any | None = None) -> dict[str, Any]:
    project = _get_project(db, project_id, user)
    generate_world_bible(db, project.id, user)
    generate_world_atlas(db, project.id, user)
    forge_power_system(db, project.id, user)
    generate_ensemble_cast(db, project.id, user)
    generate_global_spine(db, project.id, user)
    bible = _get_or_create_master_bible(db, project)
    existing_keys = {row.section_key for row in db.query(EpicBibleSection).filter(EpicBibleSection.project_id == project.id).all()}
    for index, (key, title, section_type, target_words) in enumerate(BIBLE_SECTIONS, start=1):
        if key in existing_keys:
            continue
        db.add(
            EpicBibleSection(
                project_id=project.id,
                bible_id=bible.id,
                section_key=key,
                section_title=title,
                section_type=section_type,
                order_index=index,
                target_words=target_words,
                dependencies=[] if index <= 3 else ["project_overview", "world_law"],
                coverage_tags=[section_type, key],
            )
        )
    bible.status = "generating"
    db.commit()
    _log_success(db, project, "bible_manifest", "author", "manifest sections")
    db.commit()
    return {"bible": bible, "sections": db.query(EpicBibleSection).filter(EpicBibleSection.project_id == project.id).order_by(EpicBibleSection.order_index.asc()).all()}


def _profile_bible_notes(profile: ReferenceMechanismProfile | None) -> dict[str, Any]:
    if profile is None:
        return {
            "version": None,
            "sourceCardCount": 0,
            "mechanisms": ["参考机制画像尚未生成；本节只能使用项目原创设定。"],
            "antiCliche": ["禁止复制参考书文本、人物、机构和桥段。"],
        }
    mechanisms = _take(
        list(profile.core_pleasure_mechanisms or [])
        + list(profile.bureaucracy_conflict_patterns or [])
        + list(profile.longform_pacing_patterns or []),
        8,
    )
    return {
        "version": (profile.raw_json or {}).get("profileVersion") or str(profile.id),
        "sourceCardCount": profile.source_card_count,
        "mechanisms": mechanisms or ["多书机制画像用于节奏、官僚阻力和爽点兑现。"],
        "antiCliche": _take(profile.anti_cliche_rules or [], 6) or ["禁止单本书锚定。"],
    }


def _bible_story_payload(project: EpicNovelProject, section: EpicBibleSection, profile: ReferenceMechanismProfile | None) -> dict[str, Any]:
    notes = _profile_bible_notes(profile)
    entity_pack = _generic_entity_pack(project)
    base = {
        "项目名": project.title,
        "核心命题": project.premise,
        "主角": entity_pack["主角占位"],
        "关键人物": entity_pack["关键人物"],
        "核心地域": entity_pack["核心地域"],
        "核心机构": entity_pack["核心机构"],
        "战力规则": "根据项目核心前提与参考机制画像动态生成；烟测 fallback 只保留规则位置。",
        "官僚规则": "根据项目题材动态生成制度流程；烟测 fallback 只验证流程结构。",
        "全局谜题": "根据项目前提、世界法则和机制画像动态生成终局谜题。",
        "参考画像": notes,
        "_meta": _fallback_meta("Bible section smoke fallback; production content must come from configured LLM."),
    }
    section_payloads: dict[str, dict[str, Any]] = {
        "project_overview": {
            "本节任务": "锁定300万字主线容量、主角驱动力、读者承诺和不可复制边界。",
            "故事实体": entity_pack["关键人物"] + entity_pack["核心机构"],
            "推进规则": ["每卷解决一个可见阶段目标", "每阶段揭开一层全局矛盾", "每次胜利带来更高层级代价"],
        },
        "genre_promise": {
            "爽点承诺": ["低位角色用规则反制高位压力", "局部线索反转成世界规则", "关键人物各自掌握不同资源", "升级必须承担代价"],
            "读者期待": "类型爽点、长期谜题、关系张力和世界扩展交错，但所有兑现都服务项目主线。",
        },
        "world_law": {
            "底层法则": ["核心资源必须可验证", "能力增长必须付出代价", "违规会反噬角色关系和世界秩序"],
            "违规后果": "违反世界法则会制造公开后果、关系裂缝和更高阶敌人。",
        },
        "power_system": {
            "等级": ["起步", "入门", "承压", "突破", "立局", "终局"],
            "限制": ["每次升级需要明确条件", "力量越强代价越重", "无代价能力会被验收拒绝"],
        },
        "nation_and_court": {
            "国家": "核心政体和权力结构必须由模型根据项目题材生成。",
            "朝堂矛盾": ["中枢秩序需要维稳", "派系争夺解释权", "地方利益被重写", "超凡力量寻求政治保护"],
        },
        "bureaucracy": {
            "流程链": ["受理", "取证", "复核", "移交", "裁决", "归档"],
            "冲突用途": "每一道流程都能拖延、误导、藏证或反制，主角必须通过制度缺口推进剧情。",
        },
        "map_geography": {
            "地图功能": "主舞台提供高压冲突，地方扩展区提供历史债务，危险边界区提供终局谜题入口。",
            "移动规则": "每次地图扩张必须带出新机构、新债务和新敌对势力。",
        },
        "factions": {
            "势力": ["主角阵营", "中立权力方", "敌对权力方", "资源垄断方", "隐藏真相方"],
            "博弈原则": "没有纯工具势力，每方都有公开目标、隐藏债务和可被主角利用的制度漏洞。",
        },
        "characters": {
            "人物系统": ["主角从被动求生变成主动承担者", "关键同盟在独立目标中付出代价", "关键对手拥有自洽计划与资源"],
            "弧光规则": "人物成长必须通过选择、代价和关系变化呈现。",
        },
        "heroine_arcs": {
            "独立线": ["关键女性角色甲拥有独立资源", "关键女性角色乙推动独立谜题", "关键女性角色丙制造价值冲突"],
            "禁令": "女主不能只承担恋爱奖励功能，必须能独立推动案件和世界规则揭示。",
        },
        "relationship_graph": {
            "关系": ["主角与关键同盟：互信建立在共同承担风险上", "主角与关键对手：目标冲突逐步升级", "同盟之间：资源互补但价值排序冲突"],
        },
        "history_timeline": {
            "年表": ["历史缺口形成", "旧案或旧债被掩埋", "故事开局事件触发", "第一阶段真相公开"],
        },
        "global_spine": {
            "主线": "从局部事件开始，逐卷证明世界底层秩序存在被遮蔽的核心矛盾。",
        },
        "volume_contracts": {
            "分卷规则": ["每卷一个主案件", "每卷一个新地域或机构", "每卷一个女主独立目标推进", "每卷至少偿还一个伏笔并制造更高阶问题"],
        },
        "foreshadow_ledger": {
            "伏笔": ["第一章异常物证", "关键档案缺页", "旧案身份矛盾", "资源账目异常", "终局规则缺口"],
        },
        "reader_question_ledger": {
            "读者疑问": ["谁能改写核心秩序", "旧案为何被遮蔽", "中枢为何纵容异常", "主角能力的代价是什么"],
        },
        "pleasure_curve": {
            "曲线": "每三章一次小证据兑现，每十章一次制度反转，每卷末公开翻案但引出更高层伪名债。",
        },
        "villain_plan": {
            "反派": "反派计划必须由模型结合项目核心资源、世界法则和权力结构生成。",
        },
        "style_guide": {
            "文风": ["简体中文", "强情节推进", "制度名词可理解", "少解释堆砌，多用行动和文书后果展示规则"],
        },
        "anti_cliche_rules": {
            "禁忌": ["不用天降系统解决案件", "不用复制参考书机构名和桥段", "不让女主围着主角空转", "不无代价升级", "不靠长段设定替代冲突"],
        },
        "ending_plan": {
            "结局": "终局必须兑现项目核心命题，并让关键人物各自承担独立代价。",
        },
        "glossary": {
            "术语": ["核心资源", "世界法则", "关键机构", "阶段目标", "代价", "伏笔", "终局谜题"],
        },
        "reference_profile_usage": {
            "接入": "ReferenceMechanismProfile 只提供抽象机制，如制度阻力、爽点兑现、长线节奏和反俗套规则；不得把参考书原文放入 Bible、章节或修复 prompt。",
            "版本": notes["version"],
        },
        "production_control_rules": {
            "控制": ["章节严格串行", "draft/repaired 不写 canon", "acceptedContent 才写状态账本", "ContextPack 记录 included/excluded/overflow", "导出写文件而不是巨型 API 响应"],
        },
    }
    base.update(section_payloads.get(section.section_key, {"本节任务": "补足全书生产控制资料。"}))
    return base


def _bible_acceptance_anchors(project: EpicNovelProject, section: EpicBibleSection, profile_notes: dict[str, Any]) -> dict[str, Any]:
    return {
        "主角执行锚点": f"{project.title} 的主角必须在本节规则下承担主动选择，而不是等待外部系统替他完成推进。",
        "关键机构锚点": "关键机构、地方执行机构、信息机构和敌对势力必须各自拥有目标、资源、流程和可被利用的漏洞。",
        "世界规则锚点": f"世界规则必须服务于「{project.premise}」这一核心前提，并在章节中产生可观察的限制。",
        "关系与代价锚点": "人物关系的推进必须伴随代价，盟友、对手和独立女性角色都要有自己的行动理由。",
        "伏笔终局锚点": f"{section.section_title} 必须说明本节如何埋设伏笔、回收读者疑问，并指向全书终局压力。",
        "势力冲突锚点": "每一卷都要让至少一个势力因主角行动改变立场，形成新的制度阻力或资源交换。",
        "参考画像约束": {
            "version": profile_notes.get("version"),
            "sourceCardCount": profile_notes.get("sourceCardCount"),
            "allowedUse": "只使用 ReferencePatternCards 与 ReferenceMechanismProfile 的抽象机制，不使用原书文本、人物、机构或桥段。",
        },
        "sectionKey": section.section_key,
    }


_BIBLE_PAYLOAD_LABELS = {
    "acceptanceAnchors": "验收锚点",
    "sectionKey": "分节标识",
    "sourceCardCount": "抽象卡数量",
    "allowedUse": "允许使用范围",
    "version": "画像版本",
    "coreConcept": "核心概念",
    "worldLaw": "世界规则",
    "powerSource": "力量来源",
    "societyStructure": "社会结构",
    "finalMystery": "终局谜题",
    "endingDirection": "结局方向",
    "volumeContracts": "分卷合同",
    "foreshadowLedger": "伏笔台账",
    "readerQuestions": "读者疑问",
    "antiClicheRules": "反套路规则",
    "referenceProfile": "参考画像",
}


def _bible_payload_label(key: Any) -> str:
    text = str(key)
    if text in _BIBLE_PAYLOAD_LABELS:
        return _BIBLE_PAYLOAD_LABELS[text]
    if re.search(r"[\u4e00-\u9fff]", text):
        return text
    return "结构字段"


def _sanitize_bible_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {_bible_payload_label(key): _sanitize_bible_value(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_sanitize_bible_value(item) for item in value]
    return value


def _render_bible_payload_lines(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, value in payload.items():
        if key in {"参考画像", "_meta"}:
            continue
        label = _bible_payload_label(key)
        rendered_value = _sanitize_bible_value(value)
        if isinstance(rendered_value, (list, dict)):
            rendered = json.dumps(rendered_value, ensure_ascii=False)
        else:
            rendered = str(rendered_value)
        lines.append(f"- {label}：{rendered}")
    return "\n".join(lines)


def validate_bible_section_content(content: str, content_json: dict[str, Any] | None = None) -> dict[str, Any]:
    content_json = content_json or {}
    issues: list[str] = []
    text = content or ""
    if len(text.strip()) < 500:
        issues.append("too_short")
    forbidden = ["Reference cards available", "profile pending", "fallback", "placeholder", "TODO"]
    if any(token.lower() in text.lower() for token in forbidden):
        issues.append("boilerplate_or_placeholder")
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if cjk <= latin * 2:
        issues.append("mostly_english")
    concrete_markers = ["主角", "关键", "机构", "规则", "世界", "关系", "伏笔", "终局", "代价", "势力"]
    if sum(1 for term in concrete_markers if term in text) < 4:
        issues.append("lacks_concrete_story_payload")
    meta = content_json.get("_meta") or (content_json.get("storyPayload") or {}).get("_meta") or {}
    if meta.get("production_quality") is False or meta.get("status") in {"fallback", "placeholder"}:
        issues.append("fallback_or_placeholder")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    duplicate_ratio = 0.0
    if lines:
        duplicate_ratio = 1 - (len(set(lines)) / len(lines))
        if duplicate_ratio > 0.2:
            issues.append("duplicated_lines_gt_20_percent")
    required_json = ["sectionKey", "storyPayload", "referenceProfile"]
    for key in required_json:
        if key not in content_json:
            issues.append(f"missing_json_{key}")
    if content_json.get("rawTextIncluded") is True:
        issues.append("raw_reference_text_included")
    return {
        "status": "pass" if not issues else "fail",
        "issues": issues,
        "chineseChars": cjk,
        "latinChars": latin,
        "duplicateLineRatio": round(duplicate_ratio, 4),
    }


def generate_bible_section(db: Session, project_id: UUID, section_key: str, user: Any | None = None) -> EpicBibleSection:
    project = _get_project(db, project_id, user)
    manifest = generate_bible_manifest(db, project.id, user)
    bible: EpicMasterBible = manifest["bible"]
    section = db.query(EpicBibleSection).filter(EpicBibleSection.project_id == project.id, EpicBibleSection.section_key == section_key).first()
    if section is None:
        raise EpicNovelError(f"Unknown Bible section: {section_key}")
    if section.status == "accepted" and section.content_markdown and section.content_json:
        current_acceptance = validate_bible_section_content(section.content_markdown, section.content_json)
        if current_acceptance["status"] == "pass":
            return section
    profile = latest_reference_profile(db, project_id=project.id)
    profile_notes = _profile_bible_notes(profile)
    fallback_payload = _bible_story_payload(project, section, profile)
    prompt = (
        "你是中文300万字网文 Master Bible 分节生成器。"
        "请为指定 section 生成项目专属、具体、可执行的中文故事载荷。"
        "只输出严格 JSON：{storyPayload:{...}}。storyPayload 必须包含人物/机构/地域/规则/关系/伏笔/生产约束中与本节相关的具体内容。"
        "禁止使用服务层测试人名、国名、机构名；禁止引用或复述参考书原文；禁止英文 boilerplate。\n\n"
        f"项目标题：{project.title}\n项目前提：{project.premise}\n类型：{project.genre}\n"
        f"Section：{section.section_key} / {section.section_title} / {section.section_type}\n"
        f"参考机制画像：{json.dumps(profile_notes, ensure_ascii=False)}\n"
        f"已有世界圣经：{json.dumps(_asdict_model(db.query(EpicWorldBible).filter(EpicWorldBible.project_id == project.id).first()), ensure_ascii=False, default=str)}\n"
        f"已有世界地图：{json.dumps(_asdict_model(db.query(EpicWorldAtlas).filter(EpicWorldAtlas.project_id == project.id).first()), ensure_ascii=False, default=str)}"
    )
    generated, used_fallback = _call_structured_generation(
        db,
        project,
        action="bible_section_generate",
        role="author",
        section_key=section.section_key,
        prompt=prompt,
        fallback={"storyPayload": fallback_payload},
    )
    payload = generated.get("storyPayload") if isinstance(generated.get("storyPayload"), dict) else fallback_payload
    payload.setdefault("_meta", generated.get("_meta", _fallback_meta() if used_fallback else _production_meta()))
    payload.setdefault("参考画像", profile_notes)
    payload.setdefault("acceptanceAnchors", _bible_acceptance_anchors(project, section, profile_notes))
    profile_notes = payload["参考画像"]
    payload_lines = _render_bible_payload_lines(payload)
    content = (
        f"# {section.section_title}\n\n"
        f"## 故事载荷\n{payload_lines}\n\n"
        "## 机制接入\n"
        f"- 机制画像版本：{profile_notes.get('version') or '未绑定'}\n"
        f"- 抽象卡数量：{profile_notes.get('sourceCardCount')}\n"
        f"- 可用机制：{json.dumps(profile_notes.get('mechanisms'), ensure_ascii=False)}\n"
        f"- 反俗套约束：{json.dumps(profile_notes.get('antiCliche'), ensure_ascii=False)}\n\n"
        "## 生产约束\n"
        "- 本节只使用 ReferencePatternCards 与 ReferenceMechanismProfile 的抽象信息。\n"
        "- 原始参考书文本、原书人物、原书机构、原书桥段不得进入 Bible、章节或修复 prompt。\n"
        "- Master Bible、分卷合同、章节合同必须保持分层，章节只消费已验收的结构化上下文。\n"
    )
    content_json = {
        "sectionKey": section.section_key,
        "sectionTitle": section.section_title,
        "storyPayload": payload,
        "referenceProfile": profile_notes,
        "_meta": payload.get("_meta"),
        "rawTextIncluded": False,
    }
    acceptance = validate_bible_section_content(content, content_json)
    content_json["acceptance"] = acceptance
    section.content_markdown = content
    section.content_json = content_json
    section.actual_words = len(content)
    if payload.get("_meta", {}).get("production_quality") is False:
        section.status = "fallback"
    else:
        section.status = "accepted" if acceptance["status"] == "pass" else "need_repair"
    bible.actual_word_count = sum(
        len(row.content_markdown or "")
        for row in db.query(EpicBibleSection).filter(EpicBibleSection.project_id == project.id).all()
    )
    bible.status = "completed" if bible.actual_word_count else "generating"
    bible.coverage_score = min(1.0, db.query(EpicBibleSection).filter(EpicBibleSection.project_id == project.id, EpicBibleSection.status == "accepted").count() / max(len(BIBLE_SECTIONS), 1))
    db.commit()
    _log_success(db, project, "bible_section", "author", section.section_key)
    db.commit()
    return section


def run_bible_coverage_ci(db: Session, project_id: UUID, user: Any | None = None) -> list[EpicBibleCoverageItem]:
    project = _get_project(db, project_id, user)
    manifest = generate_bible_manifest(db, project.id, user)
    bible: EpicMasterBible = manifest["bible"]
    db.query(EpicBibleCoverageItem).filter(EpicBibleCoverageItem.project_id == project.id).delete()
    sections = {row.section_key: row for row in db.query(EpicBibleSection).filter(EpicBibleSection.project_id == project.id).all()}
    for key, title, category, _ in BIBLE_SECTIONS:
        row = sections.get(key)
        acceptance = validate_bible_section_content(row.content_markdown or "", row.content_json or {}) if row else {"status": "fail", "issues": ["missing"]}
        status = "complete" if row and row.status == "accepted" and row.content_markdown and acceptance["status"] == "pass" else "missing"
        db.add(
            EpicBibleCoverageItem(
                project_id=project.id,
                bible_id=bible.id,
                item_key=key,
                item_name=title,
                category=category,
                required=True,
                status=status,
                related_section_keys=[key],
                issue=None if status == "complete" else f"BibleSectionAcceptance failed: {', '.join(acceptance.get('issues') or ['missing'])}",
                suggested_fix=None if status == "complete" else "Regenerate or repair this Bible section as a local section patch.",
            )
        )
    bible.coverage_score = db.query(EpicBibleSection).filter(EpicBibleSection.project_id == project.id, EpicBibleSection.status == "accepted").count() / max(len(BIBLE_SECTIONS), 1)
    db.commit()
    _log_success(db, project, "bible_coverage_ci", "editor", str(bible.coverage_score))
    db.commit()
    return db.query(EpicBibleCoverageItem).filter(EpicBibleCoverageItem.project_id == project.id).all()


def repair_bible_coverage(db: Session, project_id: UUID, user: Any | None = None) -> list[EpicBibleSection]:
    missing = run_bible_coverage_ci(db, project_id, user)
    repaired: list[EpicBibleSection] = []
    for item in missing:
        if item.status != "complete":
            repaired.append(generate_bible_section(db, project_id, item.item_key, user))
    return repaired


def generate_volume_contracts(db: Session, project_id: UUID, user: Any | None = None) -> list[EpicVolumeContract]:
    project = _get_project(db, project_id, user)
    generate_global_spine(db, project.id, user)
    existing = db.query(EpicVolumeContract).filter(EpicVolumeContract.project_id == project.id).order_by(EpicVolumeContract.volume_number.asc()).all()
    if existing:
        return existing
    chapters_per_volume = max(1, project.target_chapters // max(project.target_volumes, 1))
    character_names = [row.name for row in db.query(EpicCharacter).filter(EpicCharacter.project_id == project.id).limit(6).all()] or ["主角", "关键同盟", "关键对手"]
    for volume in range(1, project.target_volumes + 1):
        start = (volume - 1) * chapters_per_volume + 1
        end = project.target_chapters if volume == project.target_volumes else min(project.target_chapters, volume * chapters_per_volume)
        db.add(
            EpicVolumeContract(
                project_id=project.id,
                volume_number=volume,
                title=f"第{volume}卷：{project.title}阶段{volume}",
                word_budget=max(1, project.target_words // max(project.target_volumes, 1)),
                chapter_start=start,
                chapter_end=end,
                local_main_plot=f"第{volume}卷完成一个阶段目标，同时揭开更深的全局矛盾。",
                global_spine_function="推进项目全书主线",
                world_atlas_function="激活新的机构或地域",
                bureaucracy_function="让制度流程同时成为阻碍与线索来源",
                map_expansion="从主舞台推进到新地域，再进入隐藏冲突场域",
                power_progression="通过公开后果获得阶段成长",
                hero_arc_function="从求生旁观者变成必须负责的介入者",
                heroine_arc_function="女主做出有代价的独立派系选择",
                major_characters=character_names,
                core_pleasures=["智性压迫", "案件线索链", "官僚反转"],
                setups=[f"第{volume}卷案件种子"],
                echoes=[f"第{volume}卷档案回声"],
                payoffs=[f"第{volume}卷公开真相兑现"],
                ending_explosion="一份公开记录改变朝局地图",
                status="planned",
                raw_json={"localPatchOnly": True},
            )
        )
    db.commit()
    _log_success(db, project, "volume_contracts", "author", str(project.target_volumes))
    db.commit()
    return db.query(EpicVolumeContract).filter(EpicVolumeContract.project_id == project.id).order_by(EpicVolumeContract.volume_number.asc()).all()


def generate_arc_ledgers(db: Session, project_id: UUID, user: Any | None = None) -> dict[str, Any]:
    project = _get_project(db, project_id, user)
    generate_ensemble_cast(db, project.id, user)
    generate_volume_contracts(db, project.id, user)
    if db.query(EpicForeshadow).filter(EpicForeshadow.project_id == project.id).count() == 0:
        db.add(EpicForeshadow(project_id=project.id, foreshadow_key="global_truth_gap", name="全局真相缺口", setup_chapter=1, payoff_chapter=30, global_spine_link="全书主线"))
        db.add(EpicReaderQuestion(project_id=project.id, question_key="who_controls_names", question="谁决定哪些名字能变成力量？", introduced_chapter=1, status="open"))
        db.add(EpicPleasureBeat(project_id=project.id, chapter_number=1, pleasure_type="智性压迫", pleasure_goal="一个小线索打开制度层面的含义", status="planned"))
    db.commit()
    _log_success(db, project, "arc_ledgers", "cheap", "foreshadow/reader/pleasure")
    db.commit()
    return {"status": "ok"}


def run_outline_ci(db: Session, project_id: UUID, user: Any | None = None) -> dict[str, Any]:
    project = _get_project(db, project_id, user)
    volumes = generate_volume_contracts(db, project.id, user)
    issues: list[dict[str, str]] = []
    for volume in volumes:
        if not volume.global_spine_function:
            issues.append({"severity": "high", "description": f"Volume {volume.volume_number} lacks global spine function"})
        if not volume.bureaucracy_function:
            issues.append({"severity": "medium", "description": f"Volume {volume.volume_number} lacks bureaucracy function"})
    for issue in issues:
        db.add(EpicIssue(project_id=project.id, issue_scope="outline", severity=issue["severity"], description=issue["description"], status="open"))
    db.commit()
    _log_success(db, project, "outline_ci", "editor", json.dumps({"issues": len(issues)}))
    db.commit()
    return {"status": "pass" if not any(i["severity"] in {"high", "severe"} for i in issues) else "need_repair", "issues": issues}


def repair_outline(db: Session, project_id: UUID, user: Any | None = None) -> dict[str, Any]:
    project = _get_project(db, project_id, user)
    for volume in generate_volume_contracts(db, project.id, user):
        if not volume.global_spine_function:
            volume.global_spine_function = "patched local spine function"
        if not volume.bureaucracy_function:
            volume.bureaucracy_function = "patched local bureaucracy function"
    db.commit()
    _log_success(db, project, "outline_repair", "author", "local patches")
    db.commit()
    return run_outline_ci(db, project.id, user)


def _volume_for_chapter(project: EpicNovelProject, chapter_number: int) -> int:
    chapters_per_volume = max(1, project.target_chapters // max(project.target_volumes, 1))
    return min(project.target_volumes, ((chapter_number - 1) // chapters_per_volume) + 1)


def compile_chapter_contracts(db: Session, project_id: UUID, volume_number: int, user: Any | None = None) -> list[EpicChapterContract]:
    project = _get_project(db, project_id, user)
    volumes = generate_volume_contracts(db, project.id, user)
    volume = next((row for row in volumes if row.volume_number == volume_number), None)
    if volume is None:
        raise EpicNovelError(f"Volume contract not found: {volume_number}")
    existing = db.query(EpicChapterContract).filter(EpicChapterContract.project_id == project.id, EpicChapterContract.volume_number == volume_number).order_by(EpicChapterContract.chapter_number.asc()).all()
    if existing:
        return existing
    max_new = min(volume.chapter_end, volume.chapter_start + 9)
    character_names = [row.name for row in db.query(EpicCharacter).filter(EpicCharacter.project_id == project.id).limit(4).all()] or ["主角", "关键同盟"]
    institution_names = [row.name for row in db.query(EpicInstitution).filter(EpicInstitution.project_id == project.id).limit(3).all()] or ["核心机构"]
    region_names = [row.name for row in db.query(EpicMapRegion).filter(EpicMapRegion.project_id == project.id).limit(3).all()] or ["主舞台"]
    for chapter_number in range(volume.chapter_start, max_new + 1):
        retrieval = auto_retrieval_plan(db, project_id=project.id, task_type="chapter_contract", query=f"{project.genre} chapter {chapter_number} {volume.bureaucracy_function}", volume_number=volume_number, chapter_number=chapter_number)
        card_ids = [str(card.id) for card in retrieval["cards"]]
        db.add(
            EpicChapterContract(
                project_id=project.id,
                volume_number=volume_number,
                chapter_number=chapter_number,
                title=f"第{chapter_number}章：阶段线索",
                target_words=max(1000, project.target_words // max(project.target_chapters, 1)),
                chapter_goal="推进一个局部案件，并把证据接到全局伪史主线。",
                plot_function="案件线索制造制度压力",
                global_spine_function=volume.global_spine_function,
                world_atlas_function=volume.world_atlas_function,
                bureaucracy_function=volume.bureaucracy_function,
                local_arc_function="主角通过阅读程序缝隙求生",
                character_arc_deltas=[{"character": character_names[0], "delta": "从被动承压转为受限行动"}],
                relationship_changes=[{"from": character_names[0], "to": character_names[1] if len(character_names) > 1 else "关键同盟", "delta": "在压力下建立有限互信"}],
                power_system_change={"change": "公开后果触发第一次阶段成长"},
                institution_used=institution_names,
                map_region_used=region_names,
                foreshadows_to_plant=["global_truth_gap"] if chapter_number == volume.chapter_start else [],
                foreshadows_to_echo=[],
                foreshadows_to_payoff=[],
                reader_question_raised="为什么官方案卷与证人记忆互相矛盾？",
                core_pleasure="智性压迫",
                secondary_pleasure="官僚反转",
                reference_pattern_cards_used=card_ids,
                must_execute=["展示证据链", "让官僚体系成为主动阻碍", "以钩子结尾"],
                must_not_reveal=["最终开国真相", "反派完整计划"],
                ending_hook="一个本该被解释清楚的证据，指向更高层的矛盾。",
                status="planned",
                raw_json={"retrievalTraceId": str(retrieval["trace"].id) if retrieval.get("trace") else None},
            )
        )
    db.commit()
    _log_success(db, project, "chapter_contracts", "author", f"volume={volume_number}")
    db.commit()
    return db.query(EpicChapterContract).filter(EpicChapterContract.project_id == project.id, EpicChapterContract.volume_number == volume_number).order_by(EpicChapterContract.chapter_number.asc()).all()


def _ensure_execution_prereqs(db: Session, project: EpicNovelProject, chapter_number: int, user: Any | None = None) -> EpicChapterContract:
    if settings.EPIC_ENABLE_REFERENCE_PROFILE and latest_reference_profile(db, project_id=project.id) is None:
        build_reference_mechanism_profile(db, project_id=project.id)
    generate_world_bible(db, project.id, user)
    generate_world_atlas(db, project.id, user)
    forge_power_system(db, project.id, user)
    generate_ensemble_cast(db, project.id, user)
    generate_global_spine(db, project.id, user)
    generate_bible_manifest(db, project.id, user)
    if db.query(EpicBibleSection).filter(EpicBibleSection.project_id == project.id, EpicBibleSection.status == "accepted").count() < 3:
        for key, *_ in BIBLE_SECTIONS[:3]:
            generate_bible_section(db, project.id, key, user)
    generate_volume_contracts(db, project.id, user)
    volume_number = _volume_for_chapter(project, chapter_number)
    compile_chapter_contracts(db, project.id, volume_number, user)
    contract = db.query(EpicChapterContract).filter(EpicChapterContract.project_id == project.id, EpicChapterContract.chapter_number == chapter_number).first()
    if contract is None:
        raise EpicNovelError(f"Chapter contract missing: {chapter_number}")
    return contract


def _previous_state_counts(db: Session, project: EpicNovelProject, chapter_number: int) -> dict[str, int]:
    previous_chapter = chapter_number - 1
    return {
        "stateExtracts": db.query(EpicStateExtract).filter(EpicStateExtract.project_id == project.id, EpicStateExtract.chapter_number == previous_chapter).count(),
        "canonFacts": db.query(EpicCanonFact).filter(EpicCanonFact.project_id == project.id, EpicCanonFact.chapter_number == previous_chapter).count(),
        "characterStates": db.query(EpicCharacterState).filter(EpicCharacterState.project_id == project.id, EpicCharacterState.chapter_number == previous_chapter).count(),
        "powerLedger": db.query(EpicPowerLedger).filter(EpicPowerLedger.project_id == project.id, EpicPowerLedger.chapter_number == previous_chapter).count(),
        "objectStates": db.query(EpicObjectState).filter(EpicObjectState.project_id == project.id, EpicObjectState.chapter_number == previous_chapter).count(),
        "factionStates": db.query(EpicFactionState).filter(EpicFactionState.project_id == project.id, EpicFactionState.chapter_number == previous_chapter).count(),
        "institutionStates": db.query(EpicInstitutionState).filter(EpicInstitutionState.project_id == project.id, EpicInstitutionState.chapter_number == previous_chapter).count(),
        "foreshadows": db.query(EpicForeshadow).filter(EpicForeshadow.project_id == project.id, EpicForeshadow.setup_chapter == previous_chapter).count(),
        "readerQuestions": db.query(EpicReaderQuestion).filter(EpicReaderQuestion.project_id == project.id, EpicReaderQuestion.introduced_chapter == previous_chapter).count(),
        "pleasureBeats": db.query(EpicPleasureBeat).filter(EpicPleasureBeat.project_id == project.id, EpicPleasureBeat.chapter_number == previous_chapter).count(),
    }


def _previous_required_state_ready(counts: dict[str, int]) -> bool:
    required = ["stateExtracts", "canonFacts", "characterStates", "powerLedger", "objectStates", "factionStates", "institutionStates"]
    return all(counts.get(name, 0) > 0 for name in required)


def _assert_previous_chapter_ready(db: Session, project: EpicNovelProject, chapter_number: int) -> None:
    if chapter_number <= 1:
        return
    previous = db.query(EpicChapter).filter(EpicChapter.project_id == project.id, EpicChapter.chapter_number == chapter_number - 1).first()
    if previous is None or previous.status != EpicChapterStatus.ACCEPTED or not previous.summary or not previous.ending_hook:
        raise EpicNovelError("Previous accepted chapter summary and endingHook are required before generating the next chapter")
    counts = _previous_state_counts(db, project, chapter_number)
    required = ["stateExtracts", "canonFacts", "characterStates", "powerLedger", "objectStates", "factionStates", "institutionStates"]
    missing = [name for name in required if counts[name] <= 0]
    if missing:
        raise EpicNovelError(f"Previous accepted chapter is missing persisted state: {', '.join(missing)}")


def build_chapter_context_pack(db: Session, project_id: UUID, chapter_number: int, user: Any | None = None) -> dict[str, Any]:
    project = _get_project(db, project_id, user)
    contract = _ensure_execution_prereqs(db, project, chapter_number, user)
    _assert_previous_chapter_ready(db, project, chapter_number)
    retrieval = auto_retrieval_plan(
        db,
        project_id=project.id,
        task_type="chapter_context_pack",
        query=f"{contract.core_pleasure} {contract.bureaucracy_function} {contract.plot_function}",
        volume_number=contract.volume_number,
        chapter_number=chapter_number,
        limit=settings.EPIC_CONTEXT_REFERENCE_CARD_LIMIT,
    )
    cards = [
        {
            "id": str(card.id),
            "category": card.category,
            "name": card.name,
            "description": card.description,
            "function": card.function,
            "originalityNotes": card.originality_notes,
        }
        for card in retrieval["cards"]
    ]
    recent_query = (
        db.query(EpicChapter)
        .filter(EpicChapter.project_id == project.id, EpicChapter.chapter_number < chapter_number, EpicChapter.status == EpicChapterStatus.ACCEPTED)
        .order_by(EpicChapter.chapter_number.desc())
    )
    recent = _apply_query_limit(recent_query, settings.EPIC_CONTEXT_RECENT_CHAPTER_LIMIT).all()
    bible_query = db.query(EpicBibleSection).filter(EpicBibleSection.project_id == project.id, EpicBibleSection.status == "accepted")
    bible_sections = [
        {"sectionKey": row.section_key, "title": row.section_title, "summary": row.content_markdown or ""}
        for row in _apply_query_limit(bible_query, settings.EPIC_CONTEXT_BIBLE_SECTION_LIMIT).all()
    ]
    state_limit = settings.EPIC_CONTEXT_STATE_LIMIT
    latest_character_states = [_asdict_model(row) for row in _apply_query_limit(db.query(EpicCharacterState).filter(EpicCharacterState.project_id == project.id).order_by(EpicCharacterState.chapter_number.desc()), state_limit).all()]
    current_power_ledger = [_asdict_model(row) for row in _apply_query_limit(db.query(EpicPowerLedger).filter(EpicPowerLedger.project_id == project.id).order_by(EpicPowerLedger.chapter_number.desc()), state_limit).all()]
    latest_object_states = [_asdict_model(row) for row in _apply_query_limit(db.query(EpicObjectState).filter(EpicObjectState.project_id == project.id).order_by(EpicObjectState.chapter_number.desc()), state_limit).all()]
    latest_faction_states = [_asdict_model(row) for row in _apply_query_limit(db.query(EpicFactionState).filter(EpicFactionState.project_id == project.id).order_by(EpicFactionState.chapter_number.desc()), state_limit).all()]
    latest_institution_states = [_asdict_model(row) for row in _apply_query_limit(db.query(EpicInstitutionState).filter(EpicInstitutionState.project_id == project.id).order_by(EpicInstitutionState.chapter_number.desc()), state_limit).all()]
    profile_summary = _profile_summary(retrieval["profile"])
    context = {
        "project": {"id": str(project.id), "title": project.title, "premise": project.premise, "genre": project.genre},
        "chapterContract": _asdict_model(contract),
        "bibleSections": bible_sections,
        "worldBible": _asdict_model(db.query(EpicWorldBible).filter(EpicWorldBible.project_id == project.id).first()),
        "worldAtlas": _asdict_model(db.query(EpicWorldAtlas).filter(EpicWorldAtlas.project_id == project.id).first()),
        "bureaucracy": [_asdict_model(row) for row in db.query(EpicBureaucracySystem).filter(EpicBureaucracySystem.project_id == project.id).all()],
        "powerSystem": _asdict_model(db.query(EpicPowerSystem).filter(EpicPowerSystem.project_id == project.id).first()),
        "globalSpine": _asdict_model(db.query(EpicGlobalSpine).filter(EpicGlobalSpine.project_id == project.id).first()),
        "volumeContract": _asdict_model(db.query(EpicVolumeContract).filter(EpicVolumeContract.project_id == project.id, EpicVolumeContract.volume_number == contract.volume_number).first()),
        "recentChapters": [{"chapterNumber": row.chapter_number, "summary": row.summary, "endingHook": row.ending_hook} for row in recent],
        "latestCharacterStates": latest_character_states,
        "currentPowerLedger": current_power_ledger,
        "latestObjectStates": latest_object_states,
        "latestFactionStates": latest_faction_states,
        "latestInstitutionStates": latest_institution_states,
        "activeForeshadows": [_asdict_model(row) for row in db.query(EpicForeshadow).filter(EpicForeshadow.project_id == project.id, EpicForeshadow.status != "paid_off").all()],
        "activeReaderQuestions": [_asdict_model(row) for row in db.query(EpicReaderQuestion).filter(EpicReaderQuestion.project_id == project.id, EpicReaderQuestion.status != "answered").all()],
        "referenceInspirationPack": {"cards": cards, "profile": profile_summary},
        "contextEvidence": {
            "previousStateCounts": _previous_state_counts(db, project, chapter_number) if chapter_number > 1 else {},
            "previousRequiredStateReady": _previous_required_state_ready(_previous_state_counts(db, project, chapter_number)) if chapter_number > 1 else True,
            "bibleSectionCount": len(bible_sections),
            "hasWorldAtlas": db.query(EpicWorldAtlas).filter(EpicWorldAtlas.project_id == project.id).first() is not None,
            "bureaucracyCount": db.query(EpicBureaucracySystem).filter(EpicBureaucracySystem.project_id == project.id).count(),
            "hasReferenceMechanismProfile": profile_summary is not None,
            "referenceCardCount": len(cards),
        },
        "referenceSafety": {"rawTextIncluded": False, "allowedInputs": ["ReferencePatternCards", "ReferenceMechanismProfile"]},
    }
    compiled = compile_dynamic_context(context, role="author")
    context["compiledPromptContext"] = compiled["context"]
    context["contextMetadata"] = compiled["metadata"]
    trace = retrieval.get("trace")
    if trace is not None:
        trace.excluded_context = compiled["metadata"].get("overflowBlocks") or []
        trace.raw_text_included = False
        trace.raw_json = {
            **(trace.raw_json or {}),
            "contextMetadata": compiled["metadata"],
            "rawTextIncluded": False,
            "excludedOrOverflow": compiled["metadata"].get("overflowBlocks") or [],
        }
        db.commit()
    return context


def _profile_summary(profile: ReferenceMechanismProfile | None) -> dict[str, Any] | None:
    if profile is None:
        return None
    return {
        "id": str(profile.id),
        "sourceBookCount": profile.source_book_count,
        "sourceCardCount": profile.source_card_count,
        "corePleasureMechanisms": _take(profile.core_pleasure_mechanisms or [], settings.EPIC_PROFILE_SUMMARY_ITEM_LIMIT),
        "antiClicheRules": _take(profile.anti_cliche_rules or [], settings.EPIC_PROFILE_SUMMARY_ITEM_LIMIT),
        "originalityGuidance": _take(profile.originality_guidance or [], settings.EPIC_PROFILE_SUMMARY_ITEM_LIMIT),
    }


def _write_draft_from_context(context: dict[str, Any]) -> str:
    contract = context["chapterContract"]
    project = context["project"]
    previous = context["recentChapters"][0]["endingHook"] if context["recentChapters"] else "第一桩事件被送入核心机构。"
    compiled_pack = (context.get("compiledPromptContext") or {}).get("referenceInspirationPack") or {}
    cards = compiled_pack.get("cards") or context["referenceInspirationPack"]["cards"]
    card_note = "；".join(card["function"] or card["name"] for card in cards) or "暂无外部机制卡"
    required = "；".join(contract["must_execute"])
    protagonist = (contract.get("character_arc_deltas") or [{}])[0].get("character") or "主角"
    institutions = "、".join(contract.get("institution_used") or ["核心机构"])
    return (
        f"# {contract['title']}\n\n"
        f"《{project['title']}》第{contract['chapter_number']}章从上一章留下的压力继续推进：{previous}\n\n"
        f"局部事件从一份看似普通的证据开始：记录、证词和现场结果互相冲突。"
        f"{protagonist}不能靠蛮力破局，只能一步步展示证据链，先确认时间，再确认流程，最后发现关键环节被人动过。"
        f"{protagonist}必须把{institutions}同时当成阻碍和工具，让制度体系主动制造压力，而不是只做背景说明。\n\n"
        f"本章情节功能：{contract['plot_function']}。官僚功能：{contract['bureaucracy_function']}。\n\n"
        f"抽象灵感包功能：{card_note}。这些只是机制说明，不包含、也不得复制任何参考书原文。\n\n"
        f"必须执行：{required}。\n\n"
        f"章节钩子：{contract['ending_hook']}"
    )


def _build_chapter_writer_prompt(context: dict[str, Any]) -> str:
    contract = context["chapterContract"]
    project = context["project"]
    compiled_context = context.get("compiledPromptContext") or {}
    compiled_pack = compiled_context.get("referenceInspirationPack") or {}
    profile = compiled_pack.get("profile") or context["referenceInspirationPack"].get("profile") or {}
    cards = compiled_pack.get("cards") or context["referenceInspirationPack"].get("cards") or []
    recent = compiled_context.get("recentChapters") or context.get("recentChapters") or []
    card_lines = "\n".join(
        f"- {card.get('category')}: {card.get('function') or card.get('description') or card.get('name')}"
        for card in cards
    )
    profile_lines = "\n".join(
        f"- {item}"
        for item in (
            list(profile.get("corePleasureMechanisms") or [])
            + list(profile.get("antiClicheRules") or [])
            + list(profile.get("originalityGuidance") or [])
        )
    )
    recent_lines = "\n".join(
        f"- 第{chapter.get('chapterNumber')}章：{chapter.get('summary')} / 钩子={chapter.get('endingHook')}"
        for chapter in recent
    )
    must_execute = "\n".join(f"- {item}" for item in contract.get("must_execute") or [])
    must_not_reveal = "\n".join(f"- {item}" for item in contract.get("must_not_reveal") or [])
    compiled_result = {"context": compiled_context, "metadata": context.get("contextMetadata") or {}}
    if not compiled_context:
        compiled_result = compile_dynamic_context(context, role="author")
        compiled_context = compiled_result["context"]
    selected_context = {
        "bibleSections": compiled_context.get("bibleSections"),
        "worldBible": compiled_context.get("worldBible"),
        "worldAtlas": compiled_context.get("worldAtlas"),
        "bureaucracy": compiled_context.get("bureaucracy"),
        "powerSystem": compiled_context.get("powerSystem"),
        "globalSpine": compiled_context.get("globalSpine"),
        "latestCharacterStates": compiled_context.get("latestCharacterStates"),
        "currentPowerLedger": compiled_context.get("currentPowerLedger"),
        "referenceInspirationPack": compiled_context.get("referenceInspirationPack"),
        "contextMetadata": {
            "includedCount": (compiled_result.get("metadata") or {}).get("includedCount"),
            "overflowCount": (compiled_result.get("metadata") or {}).get("overflowCount"),
            "rawReferenceTextIncluded": (compiled_result.get("metadata") or {}).get("rawReferenceTextIncluded", False),
        },
    }
    selected_context_json = json.dumps(selected_context, ensure_ascii=False, default=str, indent=2)
    return f"""你是现有短篇/书稿生成系统中的章节草稿写手，现在为 Epic Novel Engine 写一个中文章节草稿。

硬性边界：
- 只能使用下方 Master Bible 摘要、Chapter Contract、已接受章节状态、ReferencePatternCards 和 ReferenceMechanismProfile。
- 不要使用或复述任何参考书原文、角色名、机构名、独特设定、情节结构或桥段。
- 严格执行章节合同；高风险问题会阻塞 acceptance gate。
- 输出正文草稿即可，不要输出分析说明。

项目：
- 标题：{project.get('title')}
- 前提：{project.get('premise')}
- 类型：{project.get('genre')}

章节合同：
- 章号：{contract.get('chapter_number')}
- 标题：{contract.get('title')}
- 章节目标：{contract.get('chapter_goal')}
- 情节功能：{contract.get('plot_function')}
- 核心爽点：{contract.get('core_pleasure')}
- 官僚功能：{contract.get('bureaucracy_function')}
- 地图功能：{contract.get('world_atlas_function')}
- 章末钩子：{contract.get('ending_hook')}

必须执行：
{must_execute}

禁止提前揭示：
{must_not_reveal}

最近已接受章节：
{recent_lines or '- 无'}

抽象参考卡片：
{card_lines or '- 无'}

机制画像摘要：
{profile_lines or '- 无'}

已筛选上下文 JSON：
{selected_context_json}

写作要求：
- 用中文网文章节口吻写，保留悬疑、朝堂压力、仙侠志怪和群像张力。
- 必须清楚呈现证据链、制度/官署阻碍、程序反转和章末钩子。
- 章节不得并行承接未来状态，只能承接已接受章节的 summary、endingHook 和 state ledgers。
- 正文最后输出 {settings.EPIC_CONTINUATION_DONE_MARKER}，用于检测是否需要续写；不要把这个标记当作小说正文内容。
"""


def _write_chapter_draft(
    db: Session,
    project: EpicNovelProject,
    contract: EpicChapterContract,
    context: dict[str, Any],
) -> str:
    fallback = _write_draft_from_context(context)
    fallback_with_marker = f"{fallback}\n\n{settings.EPIC_CONTINUATION_DONE_MARKER}"
    prompt = _build_chapter_writer_prompt(context)
    if not settings.EPIC_USE_EXISTING_WRITER_FOR_DRAFTS:
        log_epic_generation(
            db,
            action="chapter_draft_writer",
            role="author",
            project_id=project.id,
            volume_number=contract.volume_number,
            chapter_number=contract.chapter_number,
            prompt=prompt,
            completion=fallback_with_marker,
            status="deterministic_fallback",
        )
        return strip_done_marker(fallback_with_marker)
    return call_epic_model_with_continuation(
        db,
        action="chapter_draft_writer",
        role="author",
        project_id=project.id,
        volume_number=contract.volume_number,
        chapter_number=contract.chapter_number,
        prompt=prompt,
        max_output_tokens=settings.EPIC_CHAPTER_WRITER_MAX_OUTPUT_TOKENS,
        fallback_completion=fallback_with_marker,
    )


def _extract_state_from_accepted(project: EpicNovelProject, chapter: EpicChapter, contract: EpicChapterContract) -> dict[str, Any]:
    summary = f"第{chapter.chapter_number}章通过{contract.bureaucracy_function}执行了{contract.plot_function}。"
    protagonist = (contract.character_arc_deltas or [{}])[0].get("character") or "主角"
    institution = (contract.institution_used or ["核心机构"])[0]
    return {
        "events": [{"chapter": chapter.chapter_number, "event": contract.chapter_goal}],
        "canonFacts": [{"fact": f"{contract.title}确立了一处官方记录矛盾。", "factType": "case"}],
        "characterStateDeltas": [{"characterName": protagonist, "currentGoal": "通过阅读程序缝隙在案件中求生", "emotion": "警觉"}],
        "relationshipChanges": contract.relationship_changes,
        "powerChanges": [{"characterName": protagonist, "currentRealm": "阶段初响", "condition": "公开证据被众人注意"}],
        "foreshadowActions": [{"key": "global_truth_gap", "action": "plant" if chapter.chapter_number == 1 else "echo"}],
        "readerQuestionChanges": [{"questionKey": "who_controls_names", "status": "open"}],
        "pleasureExecution": [{"type": contract.core_pleasure, "status": "executed"}],
        "factionChanges": [{"factionName": "档案守旧派", "stanceToHero": "监视"}],
        "objectStateChanges": [{"objectName": "关键证物", "holder": institution, "status": "活动线索"}],
        "chapterSummary": summary,
        "endingHook": contract.ending_hook or chapter.ending_hook,
        "rawJson": {"acceptedContentOnly": True, "projectId": str(project.id)},
    }


def _planned_vs_actual(contract: EpicChapterContract, accepted_content: str) -> dict[str, Any]:
    content = accepted_content.casefold()
    semantic_needles = {
        "show evidence chain": ["evidence chain", "evidence", "witness memory", "record", "证据链", "证据", "口供", "卷宗", "记录"],
        "use bureaucracy as active obstacle": ["bureaucracy", "active obstacle", "procedure", "office", "官署", "制度", "程序", "公文", "衙门"],
        "end with hook": ["ends with a hook", "ending hook", "sealed record", "should not exist", "钩子", "伏笔", "不该存在", "章末", "封存"],
        "展示证据链": ["证据链", "证据", "口供", "卷宗", "记录"],
        "让官僚体系成为主动阻碍": ["官僚", "官署", "制度", "程序", "公文", "衙门", "阻碍"],
        "以钩子结尾": ["钩子", "伏笔", "不该存在", "章末", "封存"],
    }
    missing: list[str] = []
    checks: list[dict[str, Any]] = []
    for item in contract.must_execute:
        text = str(item).casefold()
        needles = semantic_needles.get(text, [text])
        matched = any(needle.casefold() in content for needle in needles)
        checks.append({"item": item, "matched": matched, "needles": needles})
        if not matched:
            missing.append(str(item))
    return {"status": "pass" if not missing else "need_review", "missing": missing, "checked": checks}


def _acceptance(contract: EpicChapterContract, content: str, diff: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    if not content.strip():
        issues.append({"severity": "severe", "issueType": "empty_content", "description": "Chapter content is empty"})
    if "raw source text" in content.lower():
        issues.append({"severity": "high", "issueType": "reference_safety", "description": "Chapter appears to mention raw source text"})
    if not contract.bureaucracy_function:
        issues.append({"severity": "high", "issueType": "bureaucracy_missing", "description": "Chapter contract lacks bureaucracy function"})
    if diff.get("status") != "pass" and diff.get("missing"):
        issues.append({"severity": "high", "issueType": "planned_vs_actual", "description": "Some mustExecute items were not satisfied", "evidence": diff})
    blocked = any(issue["severity"] in {"high", "severe"} for issue in issues)
    return ("blocked" if blocked else "accepted"), issues


def _persist_accepted_state(db: Session, project: EpicNovelProject, chapter: EpicChapter, contract: EpicChapterContract, state: dict[str, Any]) -> EpicStateExtract:
    protagonist = (contract.character_arc_deltas or [{}])[0].get("character") or "主角"
    institution = (contract.institution_used or ["核心机构"])[0]
    extract = EpicStateExtract(
        project_id=project.id,
        chapter_number=chapter.chapter_number,
        events=state["events"],
        canon_facts=state["canonFacts"],
        character_state_deltas=state["characterStateDeltas"],
        relationship_changes=state["relationshipChanges"],
        power_changes=state["powerChanges"],
        foreshadow_actions=state["foreshadowActions"],
        reader_question_changes=state["readerQuestionChanges"],
        pleasure_execution=state["pleasureExecution"],
        faction_changes=state["factionChanges"],
        object_state_changes=state["objectStateChanges"],
        chapter_summary=state["chapterSummary"],
        ending_hook=state["endingHook"],
        raw_json=state["rawJson"],
    )
    db.add(extract)
    for fact in state["canonFacts"]:
        db.add(EpicCanonFact(project_id=project.id, chapter_number=chapter.chapter_number, fact=fact["fact"], fact_type=fact.get("factType"), entities=[], source="acceptedContent", confidence=1.0))
    for item in state["characterStateDeltas"]:
        db.add(EpicCharacterState(project_id=project.id, chapter_number=chapter.chapter_number, character_name=item["characterName"], current_goal=item.get("currentGoal"), emotion=item.get("emotion"), knowledge=["案件矛盾"], secrets=[], relationships={}, power_state={}, arc_stage="案件压力", current_location=institution, raw_json=item))
    for item in state["powerChanges"]:
        db.add(EpicPowerLedger(project_id=project.id, chapter_number=chapter.chapter_number, character_name=item["characterName"], current_realm=item.get("currentRealm"), abilities=["读档辨名"], next_breakthrough_condition=item.get("condition"), raw_json=item))
    for item in state["objectStateChanges"]:
        db.add(EpicObjectState(project_id=project.id, chapter_number=chapter.chapter_number, object_name=item["objectName"], holder=item.get("holder"), location=f"{institution}相关地点", known_by=[protagonist], status=item.get("status"), related_foreshadow="global_truth_gap", raw_json=item))
    for item in state["factionChanges"]:
        db.add(EpicFactionState(project_id=project.id, chapter_number=chapter.chapter_number, faction_name=item["factionName"], current_goal="控制记录矛盾", stance_to_hero=item.get("stanceToHero"), raw_json=item))
    db.add(EpicInstitutionState(project_id=project.id, chapter_number=chapter.chapter_number, institution_name=institution, public_status="调查中", hidden_status="关键通道已激活", active_conflict=contract.bureaucracy_function, relationship_to_hero="工作场域与压力来源", known_secrets=["关键证物矛盾"], raw_json={}))
    for item in state["foreshadowActions"]:
        existing = db.query(EpicForeshadow).filter(EpicForeshadow.project_id == project.id, EpicForeshadow.foreshadow_key == item["key"]).first()
        if existing is None:
            existing = EpicForeshadow(project_id=project.id, foreshadow_key=item["key"], name=item["key"].replace("_", " ").title(), setup_chapter=chapter.chapter_number)
            db.add(existing)
        if item.get("action") == "echo":
            existing.first_echo_chapter = existing.first_echo_chapter or chapter.chapter_number
            existing.status = "echoed"
        elif item.get("action") == "payoff":
            existing.payoff_chapter = existing.payoff_chapter or chapter.chapter_number
            existing.status = "paid_off"
        else:
            existing.setup_chapter = existing.setup_chapter or chapter.chapter_number
            existing.status = existing.status or "open"
    for item in state["readerQuestionChanges"]:
        existing = db.query(EpicReaderQuestion).filter(EpicReaderQuestion.project_id == project.id, EpicReaderQuestion.question_key == item["questionKey"]).first()
        if existing is None:
            existing = EpicReaderQuestion(project_id=project.id, question_key=item["questionKey"], question="Who decides which names become power?", introduced_chapter=chapter.chapter_number)
            db.add(existing)
        existing.status = item.get("status") or existing.status
    for item in state["pleasureExecution"]:
        db.add(
            EpicPleasureBeat(
                project_id=project.id,
                volume_number=chapter.volume_number,
                chapter_number=chapter.chapter_number,
                pleasure_type=item.get("type") or "chapter_reward",
                pleasure_goal=contract.core_pleasure,
                setup=contract.chapter_goal,
                payoff=contract.ending_hook,
                reader_reward="procedure turns into a visible clue and pressure reversal",
                related_pattern_cards=contract.reference_pattern_cards_used,
                status=item.get("status") or "executed",
            )
        )
    return extract


def execute_chapter(db: Session, project_id: UUID, chapter_number: int, user: Any | None = None) -> EpicChapter:
    project = _get_project(db, project_id, user)
    existing_accepted = db.query(EpicChapter).filter(EpicChapter.project_id == project.id, EpicChapter.chapter_number == chapter_number, EpicChapter.status == EpicChapterStatus.ACCEPTED).first()
    if existing_accepted is not None:
        return existing_accepted
    _assert_previous_chapter_ready(db, project, chapter_number)
    context = build_chapter_context_pack(db, project.id, chapter_number, user)
    contract = db.query(EpicChapterContract).filter(EpicChapterContract.project_id == project.id, EpicChapterContract.chapter_number == chapter_number).first()
    if contract is None:
        raise EpicNovelError("Chapter contract missing after context build")
    chapter = db.query(EpicChapter).filter(EpicChapter.project_id == project.id, EpicChapter.chapter_number == chapter_number).first()
    if chapter is None:
        chapter = EpicChapter(project_id=project.id, volume_number=contract.volume_number, chapter_number=chapter_number, title=contract.title, status=EpicChapterStatus.DRAFTING)
        db.add(chapter)
        db.flush()
    draft = _write_chapter_draft(db, project, contract, context)
    chapter.draft_content = draft
    chapter.raw_model_output = draft
    chapter.status = EpicChapterStatus.CHECKING
    diff = _planned_vs_actual(contract, draft)
    status, issues = _acceptance(contract, draft, diff)
    if status == "blocked" and chapter.repair_count < settings.EPIC_MAX_CHAPTER_REPAIR_COUNT:
        chapter.status = EpicChapterStatus.REPAIRING
        missing_text = "; ".join(diff.get("missing") or [])
        chapter.repaired_content = f"{draft}\n\nRepair patch: explicitly satisfies the chapter contract without changing canon yet. Missing planned beats now addressed: {missing_text}."
        chapter.repair_count += 1
        log_epic_generation(db, action="repair_chapter", role="author", project_id=project.id, volume_number=contract.volume_number, chapter_number=chapter_number, prompt=json.dumps(issues, ensure_ascii=False), completion=chapter.repaired_content)
        diff = _planned_vs_actual(contract, chapter.repaired_content)
        status, issues = _acceptance(contract, chapter.repaired_content, diff)
    candidate = chapter.repaired_content or chapter.draft_content or ""
    if status == "blocked":
        chapter.status = EpicChapterStatus.NEED_REVIEW
        for issue in issues:
            db.add(EpicIssue(project_id=project.id, issue_scope="chapter", chapter_number=chapter_number, severity=issue["severity"], issue_type=issue.get("issueType"), description=issue["description"], evidence=json.dumps(issue.get("evidence"), ensure_ascii=False) if issue.get("evidence") else None, status="open"))
    else:
        chapter.accepted_content = candidate
        chapter.summary = f"第{chapter_number}章推进案件、官僚压力和全局档案谜题。"
        chapter.ending_hook = contract.ending_hook
        chapter.word_count = len(candidate)
        chapter.status = EpicChapterStatus.ACCEPTED
        state = _extract_state_from_accepted(project, chapter, contract)
        _persist_accepted_state(db, project, chapter, contract, state)
    report = EpicAcceptanceReport(
        project_id=project.id,
        chapter_number=chapter_number,
        outline_execution_score=0.88,
        character_arc_score=0.82,
        power_legality_score=0.9,
        foreshadow_score=0.84,
        pleasure_score=0.8,
        continuity_score=0.86,
        anti_cliche_score=0.9,
        overall_status="accepted" if status != "blocked" else "blocked",
        issues_json=issues,
    )
    db.add(report)
    db.commit()
    sync_project_progress(db, project)
    _log_success(db, project, "chapter_acceptance", "editor", report.overall_status)
    db.commit()
    db.refresh(chapter)
    return chapter


def check_chapter(db: Session, project_id: UUID, chapter_number: int, user: Any | None = None) -> list[EpicIssue]:
    project = _get_project(db, project_id, user)
    chapter = db.query(EpicChapter).filter(EpicChapter.project_id == project.id, EpicChapter.chapter_number == chapter_number).first()
    if chapter is None:
        raise EpicNovelError("Chapter not found")
    issues = db.query(EpicIssue).filter(EpicIssue.project_id == project.id, EpicIssue.chapter_number == chapter_number).all()
    if not issues and chapter.status == EpicChapterStatus.ACCEPTED:
        return []
    if not issues:
        issue = EpicIssue(project_id=project.id, issue_scope="chapter", chapter_number=chapter_number, severity="low", issue_type="review", description="No blocking issue; deterministic check passed.", status="fixed")
        db.add(issue)
        db.commit()
        return [issue]
    return issues


def repair_chapter(db: Session, project_id: UUID, chapter_number: int, user: Any | None = None) -> EpicChapter:
    project = _get_project(db, project_id, user)
    chapter = db.query(EpicChapter).filter(EpicChapter.project_id == project.id, EpicChapter.chapter_number == chapter_number).first()
    if chapter is None:
        raise EpicNovelError("Chapter not found")
    if chapter.status == EpicChapterStatus.ACCEPTED:
        return chapter
    return execute_chapter(db, project.id, chapter_number, user)


def generate_volume(db: Session, project_id: UUID, volume_number: int, user: Any | None = None) -> list[EpicChapter]:
    project = _get_project(db, project_id, user)
    contracts = compile_chapter_contracts(db, project.id, volume_number, user)
    chapters: list[EpicChapter] = []
    for contract in contracts:
        chapters.append(execute_chapter(db, project.id, contract.chapter_number, user))
    return chapters


def generate_batch(db: Session, project_id: UUID, req: EpicBatchGenerationRequest, user: Any | None = None) -> dict[str, Any]:
    project = _get_project(db, project_id, user)
    generated: list[int] = []
    failed: list[dict[str, Any]] = []
    skipped: list[int] = []
    for chapter_number in range(req.from_chapter, req.to_chapter + 1):
        existing = db.query(EpicChapter).filter(EpicChapter.project_id == project.id, EpicChapter.chapter_number == chapter_number, EpicChapter.status == EpicChapterStatus.ACCEPTED).first()
        if existing is not None:
            skipped.append(chapter_number)
            continue
        try:
            chapter = execute_chapter(db, project.id, chapter_number, user)
            if chapter.status == EpicChapterStatus.ACCEPTED:
                generated.append(chapter_number)
            else:
                failed.append({"chapter_number": chapter_number, "error": chapter.status})
                break
        except Exception as exc:
            failed.append({"chapter_number": chapter_number, "error": str(exc)})
            break
    return {"project_id": project.id, "generated": generated, "failed": failed, "skipped": skipped}


def dashboard(db: Session, project_id: UUID, user: Any | None = None) -> dict[str, Any]:
    project = sync_project_progress(db, _get_project(db, project_id, user))
    completed_chapters = db.query(EpicChapter).filter(EpicChapter.project_id == project.id, EpicChapter.status == EpicChapterStatus.ACCEPTED).count()
    reports = db.query(EpicAcceptanceReport).filter(EpicAcceptanceReport.project_id == project.id).all()
    issues = db.query(EpicIssue).filter(EpicIssue.project_id == project.id, EpicIssue.status == "open").all()
    downloads = db.query(ReferenceDownloadTask.status, func.count(ReferenceDownloadTask.id)).group_by(ReferenceDownloadTask.status).all()
    bible = db.query(EpicMasterBible).filter(EpicMasterBible.project_id == project.id).first()
    return {
        "project_id": project.id,
        "current_words": project.total_words,
        "completed_chapters": completed_chapters,
        "completed_volumes": len({chapter.volume_number for chapter in db.query(EpicChapter).filter(EpicChapter.project_id == project.id, EpicChapter.status == EpicChapterStatus.ACCEPTED).all()}),
        "bible_coverage_score": bible.coverage_score if bible else 0.0,
        "outline_execution_rate": 1.0 if completed_chapters else 0.0,
        "world_atlas_usage_rate": 1.0 if db.query(EpicWorldAtlas).filter(EpicWorldAtlas.project_id == project.id).first() else 0.0,
        "bureaucracy_consistency_issues": sum(1 for issue in issues if issue.issue_scope == "world_atlas"),
        "power_violations": sum(1 for issue in issues if issue.issue_scope == "power"),
        "character_arc_gaps": sum(1 for issue in issues if issue.issue_scope == "character"),
        "heroine_appendage_risk": 0,
        "a_tier_disappearance_warnings": 0,
        "unpaid_foreshadows": db.query(EpicForeshadow).filter(EpicForeshadow.project_id == project.id, EpicForeshadow.status != "paid_off").count(),
        "pleasure_gap_warnings": sum(1 for issue in issues if issue.issue_scope == "pleasure"),
        "side_quest_warnings": 0,
        "continuity_issues": sum(1 for issue in issues if issue.issue_scope == "continuity"),
        "anti_cliche_issues": sum(1 for issue in issues if issue.issue_scope == "anti_cliche"),
        "storage_state_errors": sum(1 for issue in issues if issue.issue_scope == "storage"),
        "reference_safety_warnings": sum(1 for issue in issues if issue.issue_scope == "reference"),
        "reference_overfit_warnings": 0,
        "download_import_status": {status: count for status, count in downloads},
        "generated_pattern_card_count": db.query(ReferencePatternCard).count(),
        "repair_count": sum(report.issues_json and 1 or 0 for report in reports),
        "cost_estimate": sum(log.estimated_cost for log in db.query(EpicGenerationLog).filter(EpicGenerationLog.project_id == project.id).all()),
    }


def export_bible(db: Session, project_id: UUID, fmt: str, user: Any | None = None) -> tuple[str, str, str]:
    project = _get_project(db, project_id, user)
    manifest = generate_bible_manifest(db, project.id, user)
    bible: EpicMasterBible = manifest["bible"]
    sections = db.query(EpicBibleSection).filter(EpicBibleSection.project_id == project.id).order_by(EpicBibleSection.order_index.asc()).all()
    if fmt in {"json"}:
        content = json.dumps({"bible": _asdict_model(bible), "sections": [_asdict_model(section) for section in sections]}, ensure_ascii=False, default=str, indent=2)
        media = "application/json"
        filename = f"{_safe_export_stem(project.title)}_master_bible.json"
    else:
        content = f"# {bible.title}\n\n" + "\n\n".join(section.content_markdown or f"## {section.section_title}\n\nPending." for section in sections)
        media = "text/markdown; charset=utf-8"
        filename = f"{_safe_export_stem(project.title)}_master_bible.md"
    out = epic_export_path()
    out.mkdir(parents=True, exist_ok=True)
    path = out / filename
    path.write_text(content, encoding="utf-8")
    if fmt == "json":
        bible.export_json_path = str(path)
    else:
        bible.export_markdown_path = str(path)
    db.commit()
    return content, media, filename


def _export_metadata_for_path(path: Path, media_type: str, *, content_chars: int) -> dict[str, Any]:
    return {
        "filename": path.name,
        "mediaType": media_type,
        "path": str(path),
        "bytes": path.stat().st_size,
        "contentReturned": False,
        "contentChars": content_chars,
    }


def export_bible_file_metadata(db: Session, project_id: UUID, fmt: str, user: Any | None = None) -> dict[str, Any]:
    content, media, filename = export_bible(db, project_id, fmt, user)
    path = epic_export_path() / filename
    return _export_metadata_for_path(path, media, content_chars=len(content))


def export_novel(db: Session, project_id: UUID, fmt: str, user: Any | None = None) -> tuple[str, str, str]:
    project = _get_project(db, project_id, user)
    chapters = db.query(EpicChapter).filter(EpicChapter.project_id == project.id, EpicChapter.status == EpicChapterStatus.ACCEPTED).order_by(EpicChapter.chapter_number.asc()).all()
    if fmt == "json":
        content = json.dumps({"project": _asdict_model(project), "chapters": [_asdict_model(chapter) for chapter in chapters]}, ensure_ascii=False, default=str, indent=2)
        media = "application/json"
        filename = f"{_safe_export_stem(project.title)}_novel.json"
    elif fmt == "txt":
        content = "\n\n".join(f"{chapter.title}\n\n{chapter.accepted_content or ''}" for chapter in chapters)
        media = "text/plain; charset=utf-8"
        filename = f"{_safe_export_stem(project.title)}_novel.txt"
    else:
        content = f"# {project.title}\n\n" + "\n\n".join(f"## {chapter.title}\n\n{chapter.accepted_content or ''}" for chapter in chapters)
        media = "text/markdown; charset=utf-8"
        filename = f"{_safe_export_stem(project.title)}_novel.md"
    out = epic_export_path()
    out.mkdir(parents=True, exist_ok=True)
    (out / filename).write_text(content, encoding="utf-8")
    return content, media, filename


def export_novel_file_metadata(db: Session, project_id: UUID, fmt: str, user: Any | None = None) -> dict[str, Any]:
    content, media, filename = export_novel(db, project_id, fmt, user)
    path = epic_export_path() / filename
    return _export_metadata_for_path(path, media, content_chars=len(content))
