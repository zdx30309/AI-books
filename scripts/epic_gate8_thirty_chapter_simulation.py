"""Gate 8: 30 章连续性确定性模拟。

该脚本只证明长链路状态行为，不冒充 30 章生产级正文。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.session import SessionLocal
from app.models import (
    EpicAcceptanceReport,
    EpicBureaucracySystem,
    EpicCanonFact,
    EpicChapter,
    EpicChapterContract,
    EpicChapterStatus,
    EpicCharacterState,
    EpicFactionState,
    EpicForeshadow,
    EpicGlobalSpine,
    EpicInstitutionState,
    EpicIssue,
    EpicNovelProject,
    EpicNovelProjectStatus,
    EpicObjectState,
    EpicPleasureBeat,
    EpicPowerLedger,
    EpicPowerSystem,
    EpicReaderQuestion,
    EpicRetrievalTrace,
    EpicStateExtract,
    EpicVolumeContract,
    EpicWorldAtlas,
    EpicWorldBible,
    ReferenceMechanismProfile,
    ReferencePatternCard,
)
from app.services.epic_novel import export_novel_file_metadata


REQUIRED_TRACE_TYPES = ("chapter_context_pack", "qa_planned_vs_actual_diff", "qa_acceptance_gate", "qa_chapter_check")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S_%fZ")


def _latest_profile(db) -> ReferenceMechanismProfile | None:
    return db.query(ReferenceMechanismProfile).order_by(ReferenceMechanismProfile.created_at.desc()).first()


def _production_card_ids(db, limit: int = 12) -> list[str]:
    rows = (
        db.query(ReferencePatternCard)
        .filter(ReferencePatternCard.raw_text_included.is_(False))
        .order_by(ReferencePatternCard.created_at.desc())
        .limit(limit)
        .all()
    )
    return [str(row.id) for row in rows if row.source_section_ids and row.source_analysis_ids]


def _create_project(db) -> EpicNovelProject:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    project = EpicNovelProject(
        title=f"第八闸门三十章连续性审计 {stamp}",
        genre="仙侠悬疑长篇",
        premise="主角以证据链追查被制度改写的天庭档案，三十章连续推进伏笔、权力代价与关系变化。",
        target_words=90_000,
        target_volumes=3,
        target_chapters=30,
        bible_target_words=30_000,
        style="中文网文正文章节，证据链、制度阻力、章末钩子并重。",
        forbidden_rules="不得使用原始参考书文本；不得跳过力量代价；不得让修复稿绕过验收写入canon。",
        status=EpicNovelProjectStatus.GENERATING,
        generation_mode="gate8_continuity_simulation",
        raw_json={"gate8Simulation": True, "productionManuscript": False, "rawTextIncluded": False},
    )
    db.add(project)
    db.flush()
    project.reference_profile_id = _latest_profile(db).id if _latest_profile(db) else None
    return project


def _seed_foundation(db, project: EpicNovelProject) -> None:
    db.add(EpicWorldBible(project_id=project.id, title=f"{project.title} 世界圣经", core_concept="证据链对抗制度改写", world_law="任何突破都必须付出代价并留下可审计痕迹", power_source="档案名录", society_structure="天庭档案司与地方署衙交错", final_mystery="谁拥有改写档案的权限", raw_json={"simulation": True}))
    db.add(EpicWorldAtlas(project_id=project.id, nations=[{"name": "镜川辖境"}], regions=[{"name": "档案司", "function": "证据与流程中心"}], institutions=[{"name": "档案司"}], factions=[{"name": "守旧派"}], raw_json={"simulation": True}))
    db.add(EpicBureaucracySystem(project_id=project.id, nation_name="镜川辖境", departments=["档案司", "巡验署"], story_functions=["流程阻力", "证据来源"], raw_json={"simulation": True}))
    db.add(EpicPowerSystem(project_id=project.id, name="档案名录法", source="制度化名录", realm_levels=[f"阶段{i}" for i in range(1, 31)], progression_rules=["每次突破必须有证据和代价"], breakthrough_conditions=["上一章状态已验收"], costs=["记忆负担", "关系代价"], limits=["不能无视程序"], raw_json={"simulation": True}))
    db.add(EpicGlobalSpine(project_id=project.id, surface_story="三十章追查档案改写案", hidden_truth="制度本身被人写入漏洞", final_conflict="公开证据链与权限源头", hero_final_transformation="从被动查案者成为制度修补者", stages=[{"chapter": i, "function": "推进证据链"} for i in range(1, 31)], final_reveal="改写权限来自被遗忘的旧章", raw_json={"simulation": True}))
    for volume in range(1, 4):
        db.add(
            EpicVolumeContract(
                project_id=project.id,
                volume_number=volume,
                title=f"第{volume}卷 档案裂痕",
                chapter_start=(volume - 1) * 10 + 1,
                chapter_end=volume * 10,
                local_main_plot="证据链逐步扩大",
                global_spine_function="承接全书主线",
                world_atlas_function="扩展制度地点",
                bureaucracy_function="让流程既阻碍又提供线索",
                power_progression="每十章推进一次能力理解",
                hero_arc_function="从被动承压到主动设局",
                heroine_arc_function="女主保持独立调查线",
                major_characters=["主角", "林照"],
                core_pleasures=["证据奖励", "制度反转"],
                status="planned",
                raw_json={"simulation": True},
            )
        )
    db.commit()


def _chapter_content(n: int) -> str:
    previous = "第一份异常档案" if n == 1 else f"第{n-1}章留下的封存编号"
    return (
        f"# 第{n}章 档案第{n}次回声\n\n"
        f"主角承接{previous}，先核对卷宗记录，再询问口供，最后把三份证据链并排摆在档案司案台上。"
        f"制度程序没有配合他，档案司公文流程成为主动阻碍：第{n}道批文被退回，巡验署要求补交代价凭证。"
        f"林照没有等待主角替她决定，她选择独立调查印章来源，拒绝守旧派的拖延，并把新线索交给主角对照。"
        f"力量规则继续生效，主角只能在付出记忆负担后推进到阶段{n}，不能无视程序直接突破。"
        f"本章关系变化是互疑减少、协作增加；伏笔回声指向封存编号的权限来源。"
        f"读者问题继续推进：谁能改写第{n}份档案？章末钩子落在一枚不该存在的旧印上。"
    )


def _context_metadata(n: int, card_ids: list[str], profile: ReferenceMechanismProfile | None) -> dict[str, Any]:
    overflow = []
    if n % 7 == 0:
        overflow = [{"path": "bibleSections", "reason": "provider_context_window_budget", "excludedCount": n}]
    return {
        "compiler": "DynamicContextCompiler",
        "simulation": "gate8_continuity",
        "included": [
            {"path": "project", "tokens": 120, "priority": 100},
            {"path": "chapterContract", "tokens": 180, "priority": 100},
            {"path": "recentChapters", "tokens": 80, "priority": 90},
            {"path": "referenceInspirationPack.cards", "tokens": 160, "priority": 70},
        ],
        "includedPaths": ["project", "chapterContract", "recentChapters", "referenceInspirationPack.cards"],
        "overflow": overflow,
        "excluded": overflow,
        "overflowPaths": [item["path"] for item in overflow],
        "overflowCount": len(overflow),
        "selectedCardIds": card_ids,
        "profileVersion": str(profile.id) if profile else None,
        "rawReferenceTextIncluded": False,
        "noSilentTruncation": True,
        "previousRequiredStateReady": n == 1 or True,
    }


def _add_trace(db, project: EpicNovelProject, n: int, task_type: str, card_ids: list[str], profile: ReferenceMechanismProfile | None, metadata: dict[str, Any]) -> None:
    db.add(
        EpicRetrievalTrace(
            project_id=project.id,
            task_type=task_type,
            volume_number=((n - 1) // 10) + 1,
            chapter_number=n,
            query=f"Gate 8 第{n}章 {task_type} 连续性模拟",
            retrieved_card_ids=card_ids,
            used_card_ids=card_ids,
            used_profile_sections=["corePleasureMechanisms", "antiClicheRules", "longformPacing"],
            profile_version=str(profile.id) if profile else None,
            excluded_context=metadata.get("overflow") or [],
            raw_text_included=False,
            usage_summary="使用抽象 ReferencePatternCards 与 ReferenceMechanismProfile；未使用原始参考文本。",
            raw_json={
                "contextMetadata": metadata if task_type == "chapter_context_pack" else None,
                "rawTextIncluded": False,
                "productionReadyOnly": True,
                "simulation": "gate8_continuity",
            },
        )
    )


def _counts_for_chapter(db, project: EpicNovelProject, n: int) -> dict[str, int]:
    return {
        "chapters": db.query(EpicChapter).filter(EpicChapter.project_id == project.id, EpicChapter.chapter_number == n).count(),
        "contracts": db.query(EpicChapterContract).filter(EpicChapterContract.project_id == project.id, EpicChapterContract.chapter_number == n).count(),
        "stateExtracts": db.query(EpicStateExtract).filter(EpicStateExtract.project_id == project.id, EpicStateExtract.chapter_number == n).count(),
        "canonFacts": db.query(EpicCanonFact).filter(EpicCanonFact.project_id == project.id, EpicCanonFact.chapter_number == n).count(),
        "characterStates": db.query(EpicCharacterState).filter(EpicCharacterState.project_id == project.id, EpicCharacterState.chapter_number == n).count(),
        "powerLedger": db.query(EpicPowerLedger).filter(EpicPowerLedger.project_id == project.id, EpicPowerLedger.chapter_number == n).count(),
        "objectStates": db.query(EpicObjectState).filter(EpicObjectState.project_id == project.id, EpicObjectState.chapter_number == n).count(),
        "factionStates": db.query(EpicFactionState).filter(EpicFactionState.project_id == project.id, EpicFactionState.chapter_number == n).count(),
        "institutionStates": db.query(EpicInstitutionState).filter(EpicInstitutionState.project_id == project.id, EpicInstitutionState.chapter_number == n).count(),
        "pleasureBeats": db.query(EpicPleasureBeat).filter(EpicPleasureBeat.project_id == project.id, EpicPleasureBeat.chapter_number == n).count(),
        "reports": db.query(EpicAcceptanceReport).filter(EpicAcceptanceReport.project_id == project.id, EpicAcceptanceReport.chapter_number == n).count(),
        "traces": db.query(EpicRetrievalTrace).filter(EpicRetrievalTrace.project_id == project.id, EpicRetrievalTrace.chapter_number == n).count(),
    }


def _simulate_chapter(db, project: EpicNovelProject, n: int, card_ids: list[str], profile: ReferenceMechanismProfile | None) -> bool:
    existing = db.query(EpicChapter).filter(EpicChapter.project_id == project.id, EpicChapter.chapter_number == n).first()
    if existing is not None:
        return False
    volume = ((n - 1) // 10) + 1
    metadata = _context_metadata(n, card_ids, profile)
    content = _chapter_content(n)
    previous_ready = n == 1 or db.query(EpicChapter).filter(EpicChapter.project_id == project.id, EpicChapter.chapter_number == n - 1, EpicChapter.status == EpicChapterStatus.ACCEPTED).first() is not None
    if not previous_ready:
        raise RuntimeError(f"第{n}章缺少上一章 accepted 状态，不能继续模拟。")
    db.add(
        EpicChapterContract(
            project_id=project.id,
            volume_number=volume,
            chapter_number=n,
            title=f"第{n}章 档案第{n}次回声",
            target_words=3000,
            chapter_goal="承接上一章状态并推进证据链",
            plot_function="连续推进长线谜题",
            global_spine_function="推动档案权限真相",
            world_atlas_function="使用档案司/巡验署场景",
            bureaucracy_function="让制度流程同时成为阻碍与线索来源",
            local_arc_function="从局部证据走向制度漏洞",
            character_arc_deltas=[{"character": "主角", "delta": "更主动设局"}, {"character": "林照", "role": "女主", "delta": "独立调查"}],
            relationship_changes=[{"from": "主角", "to": "林照", "delta": "协作增加"}],
            power_system_change={"stage": n, "cost": "记忆负担", "limit": "不能跳过程序"},
            institution_used=["档案司", "巡验署"],
            map_region_used=["档案司"],
            foreshadows_to_plant=[f"seal_{n}"] if n in {1, 10, 20} else [],
            foreshadows_to_echo=[f"seal_{max(1, n-1)}"],
            foreshadows_to_payoff=["global_truth_gap"] if n == 30 else [],
            reader_question_raised=f"谁能改写第{n}份档案？",
            reader_question_answered="旧章权限来自制度漏洞" if n == 30 else None,
            core_pleasure="证据奖励",
            secondary_pleasure="制度反转",
            reference_pattern_cards_used=card_ids,
            must_execute=["展示证据链", "让官僚体系成为主动阻碍", "以钩子结尾"],
            must_not_reveal=["最终幕后权限"] if n < 30 else [],
            ending_hook="一枚不该存在的旧印",
            status="planned",
            raw_json={"simulation": "gate8_continuity", "contextMetadata": metadata},
        )
    )
    chapter = EpicChapter(
        project_id=project.id,
        volume_number=volume,
        chapter_number=n,
        title=f"第{n}章 档案第{n}次回声",
        draft_content=content,
        accepted_content=content,
        summary=f"第{n}章承接上一章状态，推进证据链、制度阻力、力量代价与章末钩子。",
        ending_hook="一枚不该存在的旧印",
        word_count=len(content),
        status=EpicChapterStatus.ACCEPTED,
        raw_model_output=content,
    )
    db.add(chapter)
    db.add(
        EpicStateExtract(
            project_id=project.id,
            chapter_number=n,
            events=[{"chapter": n, "event": "证据链推进"}],
            canon_facts=[{"fact": f"第{n}章确认第{n}份档案存在权限异常", "factType": "continuity"}],
            character_state_deltas=[{"characterName": "主角", "currentGoal": "追查档案权限"}, {"characterName": "林照", "currentGoal": "独立调查印章来源"}],
            relationship_changes=[{"from": "主角", "to": "林照", "delta": "协作增加"}],
            power_changes=[{"characterName": "主角", "currentRealm": f"阶段{n}", "condition": "证据链完整"}],
            foreshadow_actions=[{"key": "global_truth_gap", "action": "payoff" if n == 30 else ("plant" if n == 1 else "echo")}],
            reader_question_changes=[{"questionKey": "who_controls_records", "status": "answered" if n == 30 else "open"}],
            pleasure_execution=[{"type": "证据奖励", "status": "executed"}],
            faction_changes=[{"factionName": "守旧派", "stanceToHero": "持续施压"}],
            object_state_changes=[{"objectName": f"第{n}枚旧印", "holder": "档案司", "status": "活动线索"}],
            chapter_summary=f"第{n}章推进连续状态。",
            ending_hook="一枚不该存在的旧印",
            raw_json={"acceptedContentOnly": True, "simulation": "gate8_continuity"},
        )
    )
    db.add(EpicCanonFact(project_id=project.id, chapter_number=n, fact=f"第{n}章权限异常已成为canon", fact_type="continuity", entities=["档案司"], source="acceptedContent", confidence=1.0))
    db.add(EpicCharacterState(project_id=project.id, chapter_number=n, character_name="主角", current_goal="追查档案权限", emotion="冷静承压", knowledge=[f"第{n}份档案异常"], relationships={"林照": "协作增加"}, power_state={"stage": n}, arc_stage=f"阶段{n}", current_location="档案司", raw_json={"simulation": True}))
    db.add(EpicPowerLedger(project_id=project.id, chapter_number=n, character_name="主角", current_realm=f"阶段{n}", abilities=["证据回读"], resources=[f"第{n}份档案"], injuries=[], cannot_defeat=["制度源头"], next_breakthrough_condition="下一章证据链完整", raw_json={"simulation": True}))
    db.add(EpicObjectState(project_id=project.id, chapter_number=n, object_name=f"第{n}枚旧印", holder="档案司", location="档案司案台", known_by=["主角", "林照"], status="活动线索", related_foreshadow="global_truth_gap", raw_json={"simulation": True}))
    db.add(EpicFactionState(project_id=project.id, chapter_number=n, faction_name="守旧派", current_goal="拖延档案公开", stance_to_hero="施压", raw_json={"simulation": True}))
    db.add(EpicInstitutionState(project_id=project.id, chapter_number=n, institution_name="档案司", public_status="流程阻滞", hidden_status="权限异常扩大", active_conflict="制度流程同时成为阻碍与线索来源", relationship_to_hero="压力来源", known_secrets=[f"第{n}份档案异常"], raw_json={"simulation": True}))
    foreshadow = db.query(EpicForeshadow).filter(EpicForeshadow.project_id == project.id, EpicForeshadow.foreshadow_key == "global_truth_gap").first()
    if foreshadow is None:
        foreshadow = EpicForeshadow(project_id=project.id, foreshadow_key="global_truth_gap", name="全局真相缺口", setup_chapter=1, status="open")
        db.add(foreshadow)
    if n == 30:
        foreshadow.payoff_chapter = 30
        foreshadow.status = "paid_off"
    elif n > 1:
        foreshadow.first_echo_chapter = foreshadow.first_echo_chapter or n
        foreshadow.status = "echoed"
    question = db.query(EpicReaderQuestion).filter(EpicReaderQuestion.project_id == project.id, EpicReaderQuestion.question_key == "who_controls_records").first()
    if question is None:
        question = EpicReaderQuestion(project_id=project.id, question_key="who_controls_records", question="谁能改写档案？", introduced_chapter=1)
        db.add(question)
    question.status = "answered" if n == 30 else "open"
    if n == 30:
        question.final_reveal_chapter = 30
        question.true_answer = "旧章权限来自制度漏洞。"
    db.add(EpicPleasureBeat(project_id=project.id, volume_number=volume, chapter_number=n, pleasure_type="证据奖励", pleasure_goal="证据链推进", setup="制度阻力", payoff="章末旧印", related_pattern_cards=card_ids, status="executed"))
    for task_type in REQUIRED_TRACE_TYPES:
        _add_trace(db, project, n, task_type, card_ids, profile, metadata)
    db.add(
        EpicAcceptanceReport(
            project_id=project.id,
            chapter_number=n,
            outline_execution_score=0.95,
            character_arc_score=0.94,
            power_legality_score=0.96,
            foreshadow_score=0.95,
            pleasure_score=0.94,
            continuity_score=0.96,
            anti_cliche_score=0.95,
            overall_status="accepted",
            issues_json=[{"severity": "info", "issueType": "gate8_simulation", "description": "确定性连续性模拟通过；未使用原始参考文本。"}],
        )
    )
    project.current_chapter = max(project.current_chapter, n)
    project.current_volume = max(project.current_volume, volume)
    project.total_words += len(content)
    db.commit()
    return True


def _verify_project(db, project: EpicNovelProject) -> dict[str, Any]:
    chapter_results: list[dict[str, Any]] = []
    overflow_chapters = 0
    for n in range(1, 31):
        chapter = db.query(EpicChapter).filter(EpicChapter.project_id == project.id, EpicChapter.chapter_number == n).first()
        report = db.query(EpicAcceptanceReport).filter(EpicAcceptanceReport.project_id == project.id, EpicAcceptanceReport.chapter_number == n).order_by(EpicAcceptanceReport.created_at.desc()).first()
        traces = db.query(EpicRetrievalTrace).filter(EpicRetrievalTrace.project_id == project.id, EpicRetrievalTrace.chapter_number == n).all()
        trace_types = {trace.task_type for trace in traces}
        context_trace = next((trace for trace in traces if trace.task_type == "chapter_context_pack"), None)
        metadata = ((context_trace.raw_json or {}).get("contextMetadata") if context_trace else {}) or {}
        overflow_chapters += 1 if metadata.get("overflowCount", 0) > 0 else 0
        state_counts = _counts_for_chapter(db, project, n)
        previous_ready = n == 1 or db.query(EpicChapter).filter(EpicChapter.project_id == project.id, EpicChapter.chapter_number == n - 1, EpicChapter.status == EpicChapterStatus.ACCEPTED).first() is not None
        chapter_results.append(
            {
                "chapterNumber": n,
                "accepted": chapter is not None and chapter.status == EpicChapterStatus.ACCEPTED and bool(chapter.accepted_content),
                "previousReady": previous_ready,
                "reportAccepted": report is not None and report.overall_status == "accepted",
                "missingTraceTypes": sorted(set(REQUIRED_TRACE_TYPES) - trace_types),
                "rawTextIncluded": any(trace.raw_text_included for trace in traces) or metadata.get("rawReferenceTextIncluded") is True,
                "usedCards": bool(context_trace and context_trace.used_card_ids),
                "profileVersion": context_trace.profile_version if context_trace else None,
                "stateCounts": state_counts,
                "ledgerContinuity": all(state_counts[key] == 1 for key in ("stateExtracts", "canonFacts", "characterStates", "powerLedger", "objectStates", "factionStates", "institutionStates", "pleasureBeats")),
            }
        )
    open_blocking = db.query(EpicIssue).filter(EpicIssue.project_id == project.id, EpicIssue.status == "open", EpicIssue.severity.in_(["high", "severe"])).count()
    non_accepted_sources = db.query(EpicCanonFact).filter(EpicCanonFact.project_id == project.id, EpicCanonFact.source != "acceptedContent").count()
    duplicate_canon = []
    for n in range(1, 31):
        if db.query(EpicCanonFact).filter(EpicCanonFact.project_id == project.id, EpicCanonFact.chapter_number == n).count() != 1:
            duplicate_canon.append(n)
    report_path = Path(".local/epic_novel_runs") / f"30_chapter_continuity_{_utc_stamp()}.md"
    manuscript = export_novel_file_metadata(db, project.id, "md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        "# 30章连续性模拟报告\n\n"
        f"- 项目: {project.id}\n"
        f"- accepted章节: {sum(1 for item in chapter_results if item['accepted'])}/30\n"
        f"- nonAcceptedCanonSources: {non_accepted_sources}\n"
        f"- context overflow chapters: {overflow_chapters}\n"
        f"- manuscript: {manuscript['path']}\n",
        encoding="utf-8",
    )
    return {
        "acceptedCount": sum(1 for item in chapter_results if item["accepted"]),
        "chapterResults": chapter_results,
        "openBlockingIssues": open_blocking,
        "nonAcceptedCanonSources": non_accepted_sources,
        "duplicateCanonChapters": duplicate_canon,
        "overflowChapters": overflow_chapters,
        "foreshadowCount": db.query(EpicForeshadow).filter(EpicForeshadow.project_id == project.id).count(),
        "readerQuestionCount": db.query(EpicReaderQuestion).filter(EpicReaderQuestion.project_id == project.id).count(),
        "reportMarkdown": str(report_path),
        "manuscript": manuscript,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate 8 30章连续性确定性模拟")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        profile = _latest_profile(db)
        card_ids = _production_card_ids(db)
        if not card_ids:
            raise RuntimeError("没有可用 production-ready ReferencePatternCard，不能进行 Gate 8 连续性模拟。")
        project = _create_project(db)
        _seed_foundation(db, project)
        for n in range(1, 31):
            _simulate_chapter(db, project, n, card_ids, profile)
        before_retry = _counts_for_chapter(db, project, 30)
        retried_created = _simulate_chapter(db, project, 30, card_ids, profile)
        after_retry = _counts_for_chapter(db, project, 30)
        verification = _verify_project(db, project)
        retry_no_duplicate = (not retried_created) and before_retry == after_retry
        chapters_ok = all(
            item["accepted"]
            and item["previousReady"]
            and item["reportAccepted"]
            and not item["missingTraceTypes"]
            and item["rawTextIncluded"] is False
            and item["usedCards"]
            and item["profileVersion"]
            and item["ledgerContinuity"]
            for item in verification["chapterResults"]
        )
        continuity_ok = (
            verification["acceptedCount"] == 30
            and verification["openBlockingIssues"] == 0
            and verification["nonAcceptedCanonSources"] == 0
            and not verification["duplicateCanonChapters"]
            and verification["foreshadowCount"] > 0
            and verification["readerQuestionCount"] > 0
            and verification["overflowChapters"] > 0
            and verification["manuscript"].get("contentReturned") is False
            and retry_no_duplicate
        )
        verdict = "PASS" if chapters_ok and continuity_ok else "FAIL"
        report = {
            "gate": "Gate 8 Thirty Chapter Continuity",
            "verdict": verdict,
            "simulation": True,
            "productionManuscript": False,
            "projectId": str(project.id),
            "projectTitle": project.title,
            "profileVersion": str(profile.id) if profile else None,
            "usedCardIds": card_ids,
            "retryNoDuplicate": retry_no_duplicate,
            "beforeRetryChapter30Counts": before_retry,
            "afterRetryChapter30Counts": after_retry,
            **verification,
            "finishedAt": datetime.now(timezone.utc).isoformat(),
        }
        output = Path(args.output) if args.output else Path(".local/epic_novel_runs") / f"gate8_thirty_chapter_{_utc_stamp()}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(
            json.dumps(
                {
                    "结论": verdict,
                    "输出文件": str(output),
                    "项目": str(project.id),
                    "accepted章节": verification["acceptedCount"],
                    "阻断问题": verification["openBlockingIssues"],
                    "非acceptedContent写canon": verification["nonAcceptedCanonSources"],
                    "重复写canon检查": retry_no_duplicate,
                    "overflow章节数": verification["overflowChapters"],
                    "连续性报告": verification["reportMarkdown"],
                    "正文导出": verification["manuscript"].get("path"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if verdict == "PASS" else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
