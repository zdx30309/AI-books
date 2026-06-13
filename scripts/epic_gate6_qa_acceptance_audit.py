"""Gate 6: QA / Acceptance Gate 活体审计。"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from app.db.session import SessionLocal
from app.models import (
    EpicAcceptanceReport,
    EpicCanonFact,
    EpicChapter,
    EpicChapterContract,
    EpicChapterStatus,
    EpicCharacterState,
    EpicFactionState,
    EpicInstitutionState,
    EpicIssue,
    EpicNovelProject,
    EpicObjectState,
    EpicPleasureBeat,
    EpicPowerLedger,
    EpicStateExtract,
)
from app.services.epic_novel import check_chapter


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S_%fZ")


def _find_project(db, project_id: str | None) -> EpicNovelProject:
    if project_id:
        project = db.query(EpicNovelProject).filter(EpicNovelProject.id == UUID(project_id)).first()
        if project is None:
            raise RuntimeError(f"找不到指定 EpicNovelProject: {project_id}")
        return project
    project = db.query(EpicNovelProject).order_by(EpicNovelProject.created_at.desc()).first()
    if project is None:
        raise RuntimeError("没有找到可用于 Gate 6 的 EpicNovelProject。")
    return project


def _state_counts(db, project_id) -> dict[str, int]:
    models = {
        "stateExtracts": EpicStateExtract,
        "canonFacts": EpicCanonFact,
        "characterStates": EpicCharacterState,
        "powerLedger": EpicPowerLedger,
        "objectStates": EpicObjectState,
        "factionStates": EpicFactionState,
        "institutionStates": EpicInstitutionState,
        "pleasureBeats": EpicPleasureBeat,
    }
    return {name: db.query(model).filter(model.project_id == project_id).count() for name, model in models.items()}


def _good_content() -> str:
    return (
        "主角在档案司调阅卷宗，先核对记录，再询问口供，最后把两份证据链并排摆出。"
        "制度程序没有配合他，公文流程反而成为主动阻碍，逼他付出代价并遵守力量规则。"
        "他决定设局追问：为什么封存编号会出现在不该存在的位置？"
        "章末钩子落在一页裂开的档案上，新的伏笔被埋下。"
    )


def _bad_content() -> str:
    return (
        "As an AI, this is a placeholder chapter outline. "
        "修复补丁：补足章节合同。这里复制原书原始参考文本。"
        "主角无需代价直接突破，全部真相已经揭开，一切都解释清楚。"
    )


def _create_contract_and_chapter(db, project: EpicNovelProject, chapter_number: int, *, repaired: bool) -> EpicChapter:
    contract = EpicChapterContract(
        project_id=project.id,
        volume_number=99,
        chapter_number=chapter_number,
        title=f"Gate 6 审计章节 {chapter_number}",
        target_words=1200,
        chapter_goal="用证据链逼近制度漏洞",
        plot_function="审计 QA 阻断能力",
        bureaucracy_function="让档案司流程成为主动阻力",
        world_atlas_function="展示都城档案区",
        character_arc_deltas=[{"character": "林照", "role": "女主", "delta": "独立调查"}],
        relationship_changes=[{"from": "主角", "to": "林照", "delta": "从互疑到协作"}],
        power_system_change={"change": "能力试探", "cost": "必须付出代价"},
        institution_used=["档案司"],
        foreshadows_to_plant=["封存编号"],
        foreshadows_to_echo=[],
        foreshadows_to_payoff=["封存编号"] if not repaired else [],
        reader_question_raised="谁改了档案",
        reader_question_answered="谁改了档案" if not repaired else None,
        core_pleasure="证据奖励",
        must_execute=["展示证据链", "让官僚体系成为主动阻碍", "以钩子结尾"],
        must_not_reveal=["幕后主谋"],
        ending_hook="封存编号不该存在",
    )
    chapter = EpicChapter(
        project_id=project.id,
        volume_number=99,
        chapter_number=chapter_number,
        title=contract.title,
        draft_content=_bad_content() if not repaired else "待检查草稿",
        repaired_content=_good_content() if repaired else None,
        status=EpicChapterStatus.NEED_REVIEW,
    )
    db.add(contract)
    db.add(chapter)
    db.commit()
    return chapter


def _chapter_evidence(db, project_id, chapter_number: int) -> dict[str, Any]:
    chapter = db.query(EpicChapter).filter(EpicChapter.project_id == project_id, EpicChapter.chapter_number == chapter_number).first()
    issues = (
        db.query(EpicIssue)
        .filter(EpicIssue.project_id == project_id, EpicIssue.chapter_number == chapter_number)
        .order_by(EpicIssue.created_at.desc())
        .all()
    )
    reports = (
        db.query(EpicAcceptanceReport)
        .filter(EpicAcceptanceReport.project_id == project_id, EpicAcceptanceReport.chapter_number == chapter_number)
        .order_by(EpicAcceptanceReport.created_at.desc())
        .all()
    )
    latest_report = reports[0] if reports else None
    return {
        "chapterStatus": chapter.status if chapter else None,
        "acceptedContentPresent": bool(chapter.accepted_content) if chapter else None,
        "issueCount": len(issues),
        "openBlockingIssueTypes": [
            issue.issue_type
            for issue in issues
            if issue.status == "open" and issue.severity in {"high", "severe"}
        ],
        "repairScopes": [issue.repair_scope for issue in issues if issue.status == "open"],
        "reportCount": len(reports),
        "latestReportStatus": latest_report.overall_status if latest_report else None,
        "latestReportIssues": latest_report.issues_json if latest_report else [],
        "latestScores": {
            "outline": latest_report.outline_execution_score if latest_report else None,
            "power": latest_report.power_legality_score if latest_report else None,
            "continuity": latest_report.continuity_score if latest_report else None,
            "antiCliche": latest_report.anti_cliche_score if latest_report else None,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate 6 QA / Acceptance Gate 活体审计")
    parser.add_argument("--project-id", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        project = _find_project(db, args.project_id)
        base = 906000 + int(datetime.now(timezone.utc).timestamp()) % 10000
        bad_chapter_number = base
        repaired_chapter_number = base + 1
        before_counts = _state_counts(db, project.id)

        _create_contract_and_chapter(db, project, bad_chapter_number, repaired=False)
        check_chapter(db, project.id, bad_chapter_number)
        bad = _chapter_evidence(db, project.id, bad_chapter_number)

        _create_contract_and_chapter(db, project, repaired_chapter_number, repaired=True)
        check_chapter(db, project.id, repaired_chapter_number)
        repaired = _chapter_evidence(db, project.id, repaired_chapter_number)

        after_counts = _state_counts(db, project.id)
        canon_unchanged = before_counts == after_counts
        bad_blocks = (
            bad["latestReportStatus"] == "blocked"
            and bool(bad["openBlockingIssueTypes"])
            and bad["chapterStatus"] != EpicChapterStatus.ACCEPTED
            and not bad["acceptedContentPresent"]
        )
        repair_scoped = all(
            scope and scope.get("mode") == "local_issue_targeted" and scope.get("rewrite") == "forbidden"
            for scope in bad["repairScopes"]
        )
        repaired_no_canon_bypass = (
            repaired["latestReportStatus"] in {"accepted", "blocked"}
            and repaired["chapterStatus"] != EpicChapterStatus.ACCEPTED
            and not repaired["acceptedContentPresent"]
            and canon_unchanged
        )
        verdict = "PASS" if bad_blocks and repair_scoped and repaired_no_canon_bypass and canon_unchanged else "FAIL"
        report = {
            "gate": "Gate 6 QA Acceptance Gate",
            "verdict": verdict,
            "projectId": str(project.id),
            "badChapterNumber": bad_chapter_number,
            "repairedChapterNumber": repaired_chapter_number,
            "beforeStateCounts": before_counts,
            "afterStateCounts": after_counts,
            "canonUnchanged": canon_unchanged,
            "badBlocks": bad_blocks,
            "repairScoped": repair_scoped,
            "repairedNoCanonBypass": repaired_no_canon_bypass,
            "badChapterEvidence": bad,
            "repairedChapterEvidence": repaired,
            "finishedAt": datetime.now(timezone.utc).isoformat(),
        }
        output = Path(args.output) if args.output else Path(".local/epic_novel_runs") / f"gate6_qa_acceptance_{_utc_stamp()}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(
            json.dumps(
                {
                    "结论": verdict,
                    "输出文件": str(output),
                    "项目": str(project.id),
                    "坏章节": bad_chapter_number,
                    "坏章节阻断": bad_blocks,
                    "修复范围局部": repair_scoped,
                    "修复稿未绕过canon": repaired_no_canon_bypass,
                    "canon计数未变化": canon_unchanged,
                    "阻断问题": bad["openBlockingIssueTypes"],
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
