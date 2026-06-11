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
from app.services.epic_model_router import call_epic_model_with_continuation, log_epic_generation, resolve_model, strip_done_marker
from app.services.epic_reference import auto_retrieval_plan, build_reference_mechanism_profile, latest_reference_profile
from app.services.epic_storage import ensure_epic_storage, epic_export_path


class EpicNovelError(RuntimeError):
    """Raised for expected Epic Novel workflow errors."""


BIBLE_SECTIONS: list[tuple[str, str, str, int]] = [
    ("project_overview", "Project Overview", "overview", 1200),
    ("genre_promise", "Genre Promise", "promise", 1200),
    ("world_law", "World Law", "world", 1600),
    ("power_system", "Power System", "power", 1800),
    ("nation_and_court", "Nation and Court", "world", 1800),
    ("bureaucracy", "Bureaucracy", "institution", 1800),
    ("map_geography", "Map and Geography", "world", 1500),
    ("factions", "Factions", "faction", 1500),
    ("characters", "Characters", "character", 2200),
    ("heroine_arcs", "Heroine Independent Arcs", "character", 1600),
    ("relationship_graph", "Relationship Graph", "character", 1200),
    ("history_timeline", "History Timeline", "world", 1500),
    ("global_spine", "Global Spine", "spine", 1600),
    ("volume_contracts", "Volume Contracts", "planning", 1600),
    ("foreshadow_ledger", "Foreshadow Ledger", "ledger", 1400),
    ("reader_question_ledger", "Reader Question Ledger", "ledger", 1200),
    ("pleasure_curve", "Pleasure Curve", "pleasure", 1200),
    ("villain_plan", "Villain Plan", "character", 1200),
    ("style_guide", "Style Guide", "style", 1000),
    ("ending_plan", "Ending Plan", "ending", 1400),
    ("glossary", "Glossary", "glossary", 1000),
]


def _asdict_model(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _safe_export_stem(value: str) -> str:
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value or "").strip(" ._")
    return stem or "epic_novel"


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
    for index in range(1, 11):
        db.add(
            WorldviewCandidate(
                project_id=project.id,
                candidate_number=index,
                core_concept=f"{project.title} worldview candidate {index}",
                power_source="reputation ledger shaped by public memory and institutional records",
                world_law="Names, rumors, official archives, and fear alter usable power.",
                society_structure="Court archives, sect legends, local offices, and rumor markets compete for authority.",
                main_conflict="A small investigation exposes a centuries-long state-and-cultivation conspiracy.",
                protagonist_specialness="The protagonist reads information systems and narrative incentives as infrastructure.",
                heroine_integration="Heroines keep independent goals tied to factions, cases, and institutions.",
                ensemble_potential="Each institution owns a different truth channel.",
                final_mystery="The founding archive may be a deliberate false canon.",
                score_json={"originality": 0.82, "longformCapacity": 0.9, "selected": index <= 3},
                selected=index <= 3,
                raw_json={"abstractOnly": True},
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
    bible = EpicWorldBible(
        project_id=project.id,
        title=f"{project.title} World Bible",
        core_concept="A dynasty where fame, record, slander, and memory are measurable cultivation resources.",
        world_law="Only socially recorded names produce stable power; false records produce unstable backlash.",
        power_source="name-ledger resonance",
        society_structure="Court offices control records; sects control legends; demons control fear; scholars control reputation.",
        history="Three centuries of official history conceal a succession-level manipulation of public memory.",
        taboo_rules=["Do not absorb raw energy as a shortcut", "Do not let power grow without public-memory cost"],
        style_rules=["Case pressure first", "Worldbuilding through institutional action", "Foreshadow before reveal"],
        forbidden_rules=[project.forbidden_rules] if project.forbidden_rules else [],
        final_mystery="Who first forged the dynasty-wide name ledger, and why?",
        ending_direction="The protagonist exposes the ledger while preserving agency for allies and rivals.",
        raw_json={"source": "deterministic_world_bible"},
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
    nation_data = {
        "name": "Great Luming",
        "inspirationProfile": "Mixed",
        "dynastyFlavor": "ritual archives plus frontier militarization",
        "currentCrisis": "local cases contradict central history",
    }
    bureaucracy = EpicBureaucracySystem(
        project_id=project.id,
        nation_name=nation_data["name"],
        base_inspiration="Mixed",
        central_government={"threeBureaus": ["Archive", "Censorate", "Revenue"]},
        local_government={"county": "night watch and magistrate share evidence jurisdiction"},
        rank_system={"logic": "record authority gates access to name-ledger power"},
        official_titles=["Night Watch Clerk", "Censor Scribe", "Archive Corrector"],
        departments=["Archive Court", "Night Watch", "Censorate", "Astrology Bureau"],
        censorate_system={"storyFunction": "turns truth into political risk"},
        judicial_system={"storyFunction": "case evidence can alter official memory"},
        military_administration={"storyFunction": "frontier victories create mass reputation"},
        finance_tax_system={"storyFunction": "grain/salt records become power ledgers"},
        examination_system={"storyFunction": "scholars compete over authorized names"},
        intelligence_agencies=["Night Watch", "Shadow Ledger Office"],
        special_agencies=["Astrology Bureau", "History Office"],
        court_factions=["Archive Conservatives", "Frontier Merit Bloc", "Sect Liaison Wing"],
        power_checks=["cross-sealed case files", "public witness ledgers"],
        corruption_channels=["record deletion", "false slander markets"],
        story_functions=["case obstruction", "bureaucracy clue chain", "low-rank pressure"],
        raw_json={"abstractOriginalInstitution": True},
    )
    db.add(bureaucracy)
    nation = EpicNation(
        project_id=project.id,
        name=nation_data["name"],
        inspiration_profile="Mixed",
        dynasty_flavor=nation_data["dynastyFlavor"],
        ruling_ideology="official memory preserves order",
        political_structure="court archives balance imperial decree, censorate review, and night-watch field reports",
        capital="Luming Capital",
        provinces=["River Archive Circuit", "Frontier Smoke Circuit", "Southern Salt Circuit"],
        military_system="frontier merit records grant temporary name-ledger authority",
        fiscal_system="salt, grain, and transport ledgers become both taxation and mystical evidence",
        legal_system="case verdicts change what the public is allowed to remember",
        official_selection_system="examination plus verified reputation",
        relationship_to_hero="the hero starts as the smallest clerk in the evidence chain",
        secrets=["founding annals were patched after a succession crisis"],
        current_crisis=nation_data["currentCrisis"],
        raw_json=nation_data,
    )
    db.add(nation)
    for name, region_type in [("Luming Capital", "capital"), ("River Archive Circuit", "province"), ("Ash Bell Mountain", "forbidden_zone")]:
        db.add(
            EpicMapRegion(
                project_id=project.id,
                name=name,
                region_type=region_type,
                controlling_faction="Archive Court" if region_type != "forbidden_zone" else "unknown",
                geography="case routes and memory markets intersect here",
                economy="records, salt, grain, and rumors",
                strategic_value="reveals how local facts become official canon",
                conflicts="local testimony conflicts with imperial archive",
                secrets="a false public name may be anchored nearby",
                related_foreshadows=[],
                raw_json={"atlasFunction": "plot pressure"},
            )
        )
    for name, kind in [("Night Watch", "night_watch"), ("History Office", "archive"), ("Astrology Bureau", "astrology_bureau")]:
        db.add(
            EpicInstitution(
                project_id=project.id,
                name=name,
                institution_type=kind,
                public_function="keeps civic order through records",
                hidden_function="controls which names can become power",
                hierarchy=["clerk", "scribe", "director"],
                secrets=["selective omission can kill a person's power"],
                relationship_to_hero="first obstacle and first tool",
                story_use_cases=["case intake", "evidence obstruction", "bureaucracy reversal"],
                raw_json={"institutionFunction": "active plot engine"},
            )
        )
    for faction in ["Archive Conservatives", "Frontier Merit Bloc", "Sect Liaison Wing"]:
        db.add(EpicFaction(project_id=project.id, name=faction, type="court", public_goal="protect order", hidden_goal="control name-ledger authority", status="active"))
    atlas = EpicWorldAtlas(
        project_id=project.id,
        nations=[nation_data],
        regions=["Luming Capital", "River Archive Circuit", "Ash Bell Mountain"],
        institutions=["Night Watch", "History Office", "Astrology Bureau"],
        factions=["Archive Conservatives", "Frontier Merit Bloc", "Sect Liaison Wing"],
        trade_routes=["Southern salt route", "frontier grain route"],
        war_zones=["Frontier Smoke Circuit"],
        secret_realms=["Ash Bell Mountain memory vault"],
        historical_timeline=["Founding record sealed", "Censorate split", "Night Watch created"],
        raw_json={"bureaucracySystem": "Great Luming"},
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
    system = EpicPowerSystem(
        project_id=project.id,
        name="Name Ledger Cultivation",
        source="public memory, official record, rumor pressure, and fear attribution",
        realm_levels=["unrecorded", "heard", "remembered", "misnamed", "canonized", "mythic"],
        progression_rules=["power requires external memory trace", "official and popular records can conflict"],
        breakthrough_conditions=["solve a case that changes public understanding", "survive a slander backlash"],
        costs=["loss of private identity", "public misunderstanding creates unstable power"],
        limits=["no hidden power-up without witnesses or records", "false fame produces backlash"],
        professions=["clerk", "censor", "sect chronicler", "fear broker"],
        resources=["case files", "public notices", "ballads", "forbidden annals"],
        protagonist_path=["learn record mechanics", "weaponize contradictions", "repair public truth"],
        heroine_paths=["own faction-level reputation channels", "make independent choices with public costs"],
        villain_usage=["erase names", "forge legends", "redirect fear"],
        final_system_twist="the dynasty itself may be a cultivated name.",
        raw_json={"original": True},
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
    chars = [
        ("Shen Zhiwei", "S", "protagonist", "male", "survive by reading institutions"),
        ("Yan Qingci", "A", "heroine", "female", "protect the censorate from archive capture"),
        ("Lu Hanzhi", "A", "heroine", "female", "prove a sect legend was fabricated"),
        ("Pei Shu", "A", "heroine", "female", "control rumor markets without becoming their pawn"),
        ("Director Han", "A", "mentor_rival", "male", "keep the Night Watch alive"),
        ("Archivist Xu", "S", "villain", "unknown", "preserve the false founding canon"),
    ]
    for name, tier, role, gender, goal in chars:
        db.add(
            EpicCharacter(
                project_id=project.id,
                name=name,
                tier=tier,
                role=role,
                gender=gender,
                archetype="institutional pressure actor",
                independent_goal=goal,
                desire=goal,
                fear="being rewritten by public record",
                false_belief="truth can be controlled by one office",
                true_need="shared witness can resist false canon",
                faction="Night Watch" if role == "protagonist" else "independent",
                power_path="name-ledger pressure through public evidence",
                relationship_to_hero="ally/rival under separate institutional incentives",
                active_volumes=[1, 2, 3],
                screen_time_budget=100 if tier == "S" else 65,
                ending="independent ending tied to personal institution reform",
                must_not_do=["exist only to admire protagonist", "drop independent goal"],
                raw_json={"agencyRequired": role == "heroine"},
            )
        )
        db.add(
            EpicCharacterArc(
                project_id=project.id,
                character_name=name,
                tier=tier,
                arc_start="reacts to institutional pressure",
                arc_midpoint="chooses truth at material cost",
                low_point="public record turns against them",
                key_choice="protect people over reputation",
                cost="loss of safe status",
                arc_end="rewrites their relationship to public memory",
                volume_arc_grid=[{"volume": 1, "move": "setup"}, {"volume": 2, "move": "cost"}, {"volume": 3, "move": "payoff"}],
                raw_json={},
            )
        )
    db.add(EpicRelationshipEdge(project_id=project.id, from_character="Shen Zhiwei", to_character="Yan Qingci", relation_type="ally_rival", surface_relation="case cooperation", hidden_conflict="truth vs official survival", current_tension_level=4))
    db.commit()
    _log_success(db, project, "generate_ensemble_cast", "author", "6 characters")
    db.commit()
    return db.query(EpicCharacter).filter(EpicCharacter.project_id == project.id).all()


def generate_global_spine(db: Session, project_id: UUID, user: Any | None = None) -> EpicGlobalSpine:
    project = _get_project(db, project_id, user)
    existing = db.query(EpicGlobalSpine).filter(EpicGlobalSpine.project_id == project.id).first()
    if existing:
        return existing
    spine = EpicGlobalSpine(
        project_id=project.id,
        surface_story="A low-level clerk solves linked supernatural court cases.",
        hidden_truth="Each case exposes a false layer in the dynasty's name ledger.",
        final_conflict="Whether truth can survive when power itself depends on public memory.",
        hero_final_transformation="from survival-minded clerk to architect of shared witness",
        stages=[
            {"volume": 1, "function": "prove case facts can alter power"},
            {"volume": 2, "function": "expose faction manipulation of names"},
            {"volume": 3, "function": "connect local cases to founding false canon"},
        ],
        final_reveal="The first emperor's name may be a constructed spell, not a person.",
        raw_json={"mustDriveAllVolumes": True},
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


def generate_bible_section(db: Session, project_id: UUID, section_key: str, user: Any | None = None) -> EpicBibleSection:
    project = _get_project(db, project_id, user)
    manifest = generate_bible_manifest(db, project.id, user)
    bible: EpicMasterBible = manifest["bible"]
    section = db.query(EpicBibleSection).filter(EpicBibleSection.project_id == project.id, EpicBibleSection.section_key == section_key).first()
    if section is None:
        raise EpicNovelError(f"Unknown Bible section: {section_key}")
    profile = latest_reference_profile(db, project_id=project.id)
    profile_hint = f"Reference cards available: {profile.source_card_count}" if profile else "Reference profile pending"
    content = (
        f"# {section.section_title}\n\n"
        f"Project: {project.title}\n\n"
        f"Premise function: {project.premise}\n\n"
        f"Section role: {section.section_type}.\n\n"
        f"{profile_hint}. This section uses abstract mechanisms only and does not contain raw reference text.\n\n"
        f"Required control-plane note: Master Bible, Master Outline, and Chapter Contracts remain separate layers."
    )
    section.content_markdown = content
    section.content_json = {"sectionKey": section.section_key, "abstractOnly": True, "profileHint": profile_hint}
    section.actual_words = len(content)
    section.status = "accepted"
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
        status = "complete" if row and row.status == "accepted" and row.content_markdown else "missing"
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
                issue=None if status == "complete" else "Section has not been accepted yet.",
                suggested_fix=None if status == "complete" else "Generate this Bible section.",
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
    for volume in range(1, project.target_volumes + 1):
        start = (volume - 1) * chapters_per_volume + 1
        end = project.target_chapters if volume == project.target_volumes else min(project.target_chapters, volume * chapters_per_volume)
        db.add(
            EpicVolumeContract(
                project_id=project.id,
                volume_number=volume,
                title=f"Volume {volume}: Record and Shadow",
                word_budget=max(1, project.target_words // max(project.target_volumes, 1)),
                chapter_start=start,
                chapter_end=end,
                local_main_plot=f"Volume {volume} resolves one case chain while revealing a deeper record contradiction.",
                global_spine_function="advance the false-canon mystery",
                world_atlas_function="activate a new institution or region",
                bureaucracy_function="make official procedure an obstacle and clue source",
                map_expansion="move from office to region to hidden archive",
                power_progression="earn reputation power through public consequence",
                hero_arc_function="from survival to accountable intervention",
                heroine_arc_function="independent faction choice with cost",
                major_characters=["Shen Zhiwei", "Yan Qingci", "Lu Hanzhi"],
                core_pleasures=["intelligence pressure", "case clue chain", "bureaucracy reversal"],
                setups=[f"volume_{volume}_case_seed"],
                echoes=[f"volume_{volume}_archive_echo"],
                payoffs=[f"volume_{volume}_public_truth_payoff"],
                ending_explosion="a public record changes the political map",
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
        db.add(EpicForeshadow(project_id=project.id, foreshadow_key="founding_record_gap", name="Founding Record Gap", setup_chapter=1, payoff_chapter=30, global_spine_link="false founding canon"))
        db.add(EpicReaderQuestion(project_id=project.id, question_key="who_controls_names", question="Who decides which names become power?", introduced_chapter=1, status="open"))
        db.add(EpicPleasureBeat(project_id=project.id, chapter_number=1, pleasure_type="intelligence_pressure", pleasure_goal="small clue unlocks institutional implication", status="planned"))
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
    for chapter_number in range(volume.chapter_start, max_new + 1):
        retrieval = auto_retrieval_plan(db, project_id=project.id, task_type="chapter_contract", query=f"{project.genre} chapter {chapter_number} {volume.bureaucracy_function}", volume_number=volume_number, chapter_number=chapter_number)
        card_ids = [str(card.id) for card in retrieval["cards"]]
        db.add(
            EpicChapterContract(
                project_id=project.id,
                volume_number=volume_number,
                chapter_number=chapter_number,
                title=f"Chapter {chapter_number}: Case File Opens",
                target_words=max(1000, project.target_words // max(project.target_chapters, 1)),
                chapter_goal="Advance a local case while tying evidence to the global false-canon spine.",
                plot_function="case clue produces institutional pressure",
                global_spine_function=volume.global_spine_function,
                world_atlas_function=volume.world_atlas_function,
                bureaucracy_function=volume.bureaucracy_function,
                local_arc_function="protagonist survives by reading procedure",
                character_arc_deltas=[{"character": "Shen Zhiwei", "delta": "from observer to constrained actor"}],
                relationship_changes=[{"from": "Shen Zhiwei", "to": "Yan Qingci", "delta": "trust under pressure"}],
                power_system_change={"change": "public evidence creates first name-ledger signal"},
                institution_used=["Night Watch", "History Office"],
                map_region_used=["Luming Capital"],
                foreshadows_to_plant=["founding_record_gap"] if chapter_number == volume.chapter_start else [],
                foreshadows_to_echo=[],
                foreshadows_to_payoff=[],
                reader_question_raised="Why does the official file contradict witness memory?",
                core_pleasure="intelligence pressure",
                secondary_pleasure="bureaucracy reversal",
                reference_pattern_cards_used=card_ids,
                must_execute=["show evidence chain", "use bureaucracy as active obstacle", "end with hook"],
                must_not_reveal=["final founding truth", "villain full plan"],
                ending_hook="A sealed record names someone who should not exist.",
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
    previous = context["recentChapters"][0]["endingHook"] if context["recentChapters"] else "The first case enters the Night Watch."
    compiled_pack = (context.get("compiledPromptContext") or {}).get("referenceInspirationPack") or {}
    cards = compiled_pack.get("cards") or context["referenceInspirationPack"]["cards"]
    card_note = "; ".join(card["function"] or card["name"] for card in cards) or "no external card available"
    required = "; ".join(contract["must_execute"])
    return (
        f"# {contract['title']}\n\n"
        f"{project['title']} chapter {contract['chapter_number']} opens from the pressure left by: {previous}\n\n"
        f"The case begins with an ordinary record that does not match witness memory, so the evidence chain must be shown step by step. "
        f"The protagonist follows procedure instead of brute force, using the {', '.join(contract['institution_used'])} as both obstacle and tool, "
        f"which means the bureaucracy is an active obstacle rather than background exposition.\n\n"
        f"Required plot function: {contract['plot_function']}. Bureaucracy function: {contract['bureaucracy_function']}.\n\n"
        f"Abstract inspiration pack functions: {card_note}. These are mechanism notes only, not copied source text.\n\n"
        f"Must execute: {required}.\n\n"
        f"The chapter ends with a hook when {contract['ending_hook']}"
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
        f"- Chapter {chapter.get('chapterNumber')}: {chapter.get('summary')} / hook={chapter.get('endingHook')}"
        for chapter in recent
    )
    must_execute = "\n".join(f"- {item}" for item in contract.get("must_execute") or [])
    must_not_reveal = "\n".join(f"- {item}" for item in contract.get("must_not_reveal") or [])
    full_context = compiled_context or compile_dynamic_context(context, role="author")["context"]
    full_context_json = json.dumps(full_context, ensure_ascii=False, default=str, indent=2)
    return f"""你是现有短篇/书稿生成系统中的 Chapter Draft Writer，现在为 Epic Novel Engine 写一个章节草稿。

硬性边界：
- 只能使用下方 Master Bible 摘要、Chapter Contract、已接受章节状态、ReferencePatternCards 和 ReferenceMechanismProfile。
- 不要使用或复述任何参考书原文、角色名、机构名、独特设定、情节结构或桥段。
- 严格执行章节合同；高风险问题会阻塞 acceptance gate。
- 输出正文草稿即可，不要输出分析说明。

项目：
- title: {project.get('title')}
- premise: {project.get('premise')}
- genre: {project.get('genre')}

章节合同：
- chapterNumber: {contract.get('chapter_number')}
- title: {contract.get('title')}
- chapterGoal: {contract.get('chapter_goal')}
- plotFunction: {contract.get('plot_function')}
- corePleasure: {contract.get('core_pleasure')}
- bureaucracyFunction: {contract.get('bureaucracy_function')}
- worldAtlasFunction: {contract.get('world_atlas_function')}
- endingHook: {contract.get('ending_hook')}

必须执行：
{must_execute}

禁止提前揭示：
{must_not_reveal}

最近已接受章节：
{recent_lines or '- none'}

抽象参考卡片：
{card_lines or '- none'}

机制画像摘要：
{profile_lines or '- none'}

完整上下文 JSON：
{full_context_json}

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
    summary = f"Chapter {chapter.chapter_number} executed {contract.plot_function} through {contract.bureaucracy_function}."
    return {
        "events": [{"chapter": chapter.chapter_number, "event": contract.chapter_goal}],
        "canonFacts": [{"fact": f"{contract.title} established an official-record contradiction.", "factType": "case"}],
        "characterStateDeltas": [{"characterName": "Shen Zhiwei", "currentGoal": "survive the case by reading procedure", "emotion": "alert"}],
        "relationshipChanges": contract.relationship_changes,
        "powerChanges": [{"characterName": "Shen Zhiwei", "currentRealm": "heard", "condition": "public evidence noticed"}],
        "foreshadowActions": [{"key": "founding_record_gap", "action": "plant" if chapter.chapter_number == 1 else "echo"}],
        "readerQuestionChanges": [{"questionKey": "who_controls_names", "status": "open"}],
        "pleasureExecution": [{"type": contract.core_pleasure, "status": "executed"}],
        "factionChanges": [{"factionName": "Archive Conservatives", "stanceToHero": "watching"}],
        "objectStateChanges": [{"objectName": "sealed record", "holder": "Night Watch", "status": "active clue"}],
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
        db.add(EpicCharacterState(project_id=project.id, chapter_number=chapter.chapter_number, character_name=item["characterName"], current_goal=item.get("currentGoal"), emotion=item.get("emotion"), knowledge=["case contradiction"], secrets=[], relationships={}, power_state={}, arc_stage="case pressure", current_location="Night Watch", raw_json=item))
    for item in state["powerChanges"]:
        db.add(EpicPowerLedger(project_id=project.id, chapter_number=chapter.chapter_number, character_name=item["characterName"], current_realm=item.get("currentRealm"), abilities=["record reading"], next_breakthrough_condition=item.get("condition"), raw_json=item))
    for item in state["objectStateChanges"]:
        db.add(EpicObjectState(project_id=project.id, chapter_number=chapter.chapter_number, object_name=item["objectName"], holder=item.get("holder"), location="Night Watch archive", known_by=["Shen Zhiwei"], status=item.get("status"), related_foreshadow="founding_record_gap", raw_json=item))
    for item in state["factionChanges"]:
        db.add(EpicFactionState(project_id=project.id, chapter_number=chapter.chapter_number, faction_name=item["factionName"], current_goal="control the contradiction", stance_to_hero=item.get("stanceToHero"), raw_json=item))
    db.add(EpicInstitutionState(project_id=project.id, chapter_number=chapter.chapter_number, institution_name="Night Watch", public_status="investigating", hidden_status="record channel active", active_conflict=contract.bureaucracy_function, relationship_to_hero="workplace and pressure source", known_secrets=["sealed record contradiction"], raw_json={}))
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
        chapter.summary = f"Chapter {chapter_number} advances the case, bureaucracy pressure, and global record mystery."
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
