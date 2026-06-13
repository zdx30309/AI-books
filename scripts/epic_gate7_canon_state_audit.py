"""Gate 7: 章节 1-3 canon/state 严格执行审计。"""
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
    EpicChapterStatus,
    EpicCharacterState,
    EpicFactionState,
    EpicInstitutionState,
    EpicIssue,
    EpicObjectState,
    EpicPleasureBeat,
    EpicPowerLedger,
    EpicReaderQuestion,
    EpicRetrievalTrace,
    EpicStateExtract,
    EpicForeshadow,
    EpicNovelProject,
)
from app.services.epic_novel import build_chapter_context_pack, check_chapter, export_bible_file_metadata, export_novel_file_metadata


REQUIRED_TRACE_TYPES = {"chapter_context_pack", "qa_planned_vs_actual_diff", "qa_acceptance_gate", "qa_chapter_check"}


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S_%fZ")


def _find_project(db, project_id: str | None) -> EpicNovelProject:
    if project_id:
        project = db.query(EpicNovelProject).filter(EpicNovelProject.id == UUID(project_id)).first()
        if project is None:
            raise RuntimeError(f"找不到指定 EpicNovelProject: {project_id}")
        return project
    for project in db.query(EpicNovelProject).order_by(EpicNovelProject.created_at.desc()).all():
        accepted = (
            db.query(EpicChapter)
            .filter(EpicChapter.project_id == project.id, EpicChapter.status == EpicChapterStatus.ACCEPTED)
            .count()
        )
        if accepted >= 3:
            return project
    raise RuntimeError("没有找到至少 3 章 accepted 的 EpicNovelProject。")


def _state_counts_for_chapter(db, project_id, chapter_number: int) -> dict[str, int]:
    return {
        "stateExtracts": db.query(EpicStateExtract).filter(EpicStateExtract.project_id == project_id, EpicStateExtract.chapter_number == chapter_number).count(),
        "canonFacts": db.query(EpicCanonFact).filter(EpicCanonFact.project_id == project_id, EpicCanonFact.chapter_number == chapter_number).count(),
        "characterStates": db.query(EpicCharacterState).filter(EpicCharacterState.project_id == project_id, EpicCharacterState.chapter_number == chapter_number).count(),
        "powerLedger": db.query(EpicPowerLedger).filter(EpicPowerLedger.project_id == project_id, EpicPowerLedger.chapter_number == chapter_number).count(),
        "objectStates": db.query(EpicObjectState).filter(EpicObjectState.project_id == project_id, EpicObjectState.chapter_number == chapter_number).count(),
        "factionStates": db.query(EpicFactionState).filter(EpicFactionState.project_id == project_id, EpicFactionState.chapter_number == chapter_number).count(),
        "institutionStates": db.query(EpicInstitutionState).filter(EpicInstitutionState.project_id == project_id, EpicInstitutionState.chapter_number == chapter_number).count(),
        "pleasureBeats": db.query(EpicPleasureBeat).filter(EpicPleasureBeat.project_id == project_id, EpicPleasureBeat.chapter_number == chapter_number).count(),
    }


def _latest_traces_by_type(db, project_id, chapter_number: int) -> dict[str, EpicRetrievalTrace]:
    rows = (
        db.query(EpicRetrievalTrace)
        .filter(EpicRetrievalTrace.project_id == project_id, EpicRetrievalTrace.chapter_number == chapter_number)
        .order_by(EpicRetrievalTrace.created_at.desc())
        .all()
    )
    latest: dict[str, EpicRetrievalTrace] = {}
    for row in rows:
        latest.setdefault(row.task_type, row)
    return latest


def _chapter_check(db, project: EpicNovelProject, chapter_number: int, context: dict[str, Any] | None) -> dict[str, Any]:
    chapter = (
        db.query(EpicChapter)
        .filter(EpicChapter.project_id == project.id, EpicChapter.chapter_number == chapter_number)
        .first()
    )
    reports = (
        db.query(EpicAcceptanceReport)
        .filter(EpicAcceptanceReport.project_id == project.id, EpicAcceptanceReport.chapter_number == chapter_number)
        .order_by(EpicAcceptanceReport.created_at.desc())
        .all()
    )
    open_issues = (
        db.query(EpicIssue)
        .filter(EpicIssue.project_id == project.id, EpicIssue.chapter_number == chapter_number, EpicIssue.status == "open")
        .all()
    )
    state_counts = _state_counts_for_chapter(db, project.id, chapter_number)
    extract = (
        db.query(EpicStateExtract)
        .filter(EpicStateExtract.project_id == project.id, EpicStateExtract.chapter_number == chapter_number)
        .order_by(EpicStateExtract.id.desc())
        .first()
    )
    traces = _latest_traces_by_type(db, project.id, chapter_number)
    missing_traces = sorted(REQUIRED_TRACE_TYPES - set(traces))
    trace_metadata_ok = True
    for trace_type in REQUIRED_TRACE_TYPES:
        trace = traces.get(trace_type)
        if trace is None:
            trace_metadata_ok = False
            continue
        raw = trace.raw_json or {}
        if trace_type == "chapter_context_pack" and not raw.get("contextMetadata"):
            trace_metadata_ok = False
        if trace.raw_text_included:
            trace_metadata_ok = False
    latest_report = reports[0] if reports else None
    blocking = [issue for issue in open_issues if issue.severity in {"high", "severe"}]
    required_counts = ["stateExtracts", "canonFacts", "characterStates", "powerLedger", "objectStates", "factionStates", "institutionStates", "pleasureBeats"]
    context_evidence = (context or {}).get("contextEvidence") or {}
    recent = (context or {}).get("recentChapters") or []
    return {
        "chapterNumber": chapter_number,
        "status": chapter.status if chapter else None,
        "acceptedContentPresent": bool(chapter.accepted_content) if chapter else False,
        "summaryPresent": bool(chapter.summary) if chapter else False,
        "endingHookPresent": bool(chapter.ending_hook) if chapter else False,
        "latestReportStatus": latest_report.overall_status if latest_report else None,
        "latestReportId": str(latest_report.id) if latest_report else None,
        "openBlockingIssues": [(issue.severity, issue.issue_type) for issue in blocking],
        "openNonBlockingIssues": [(issue.severity, issue.issue_type) for issue in open_issues if issue.severity not in {"high", "severe"}],
        "stateCounts": state_counts,
        "allRequiredLedgersPresent": all(state_counts[name] > 0 for name in required_counts),
        "stateExtractHasForeshadow": bool((extract.foreshadow_actions if extract else []) or db.query(EpicForeshadow).filter(EpicForeshadow.project_id == project.id).count()),
        "stateExtractHasReaderQuestion": bool((extract.reader_question_changes if extract else []) or db.query(EpicReaderQuestion).filter(EpicReaderQuestion.project_id == project.id).count()),
        "missingTraceTypes": missing_traces,
        "traceMetadataOk": trace_metadata_ok,
        "traceIds": {key: str(value.id) for key, value in traces.items() if key in REQUIRED_TRACE_TYPES},
        "contextMetadataPresent": bool((context or {}).get("contextMetadata")),
        "contextRawTextIncluded": ((context or {}).get("contextMetadata") or {}).get("rawReferenceTextIncluded"),
        "contextEvidence": context_evidence,
        "recentChapterNumbers": [item.get("chapterNumber") for item in recent],
        "previousRequiredStateReady": context_evidence.get("previousRequiredStateReady"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate 7 章节 1-3 canon/state 严格执行审计")
    parser.add_argument("--project-id", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        project = _find_project(db, args.project_id)
        contexts: dict[int, dict[str, Any]] = {}
        qa_rerun_results: dict[int, list[tuple[str, str | None]]] = {}
        for chapter_number in (1, 2, 3):
            issues = check_chapter(db, project.id, chapter_number)
            qa_rerun_results[chapter_number] = [(issue.severity, issue.issue_type) for issue in issues]
            contexts[chapter_number] = build_chapter_context_pack(db, project.id, chapter_number)

        chapter_checks = [_chapter_check(db, project, chapter_number, contexts[chapter_number]) for chapter_number in (1, 2, 3)]
        non_accepted_sources = (
            db.query(EpicCanonFact)
            .filter(EpicCanonFact.project_id == project.id, EpicCanonFact.chapter_number.in_([1, 2, 3]), EpicCanonFact.source != "acceptedContent")
            .count()
        )
        context_chain_ok = (
            chapter_checks[1]["previousRequiredStateReady"] is True
            and 1 in chapter_checks[1]["recentChapterNumbers"]
            and chapter_checks[2]["previousRequiredStateReady"] is True
            and 2 in chapter_checks[2]["recentChapterNumbers"]
        )
        project_level_ledgers = {
            "foreshadows": db.query(EpicForeshadow).filter(EpicForeshadow.project_id == project.id).count(),
            "readerQuestions": db.query(EpicReaderQuestion).filter(EpicReaderQuestion.project_id == project.id).count(),
        }
        bible_export = export_bible_file_metadata(db, project.id, "md")
        novel_export = export_novel_file_metadata(db, project.id, "md")
        exports_ok = bible_export.get("contentReturned") is False and novel_export.get("contentReturned") is False
        chapters_ok = all(
            item["status"] == EpicChapterStatus.ACCEPTED
            and item["acceptedContentPresent"]
            and item["summaryPresent"]
            and item["endingHookPresent"]
            and item["latestReportStatus"] == "accepted"
            and not item["openBlockingIssues"]
            and item["allRequiredLedgersPresent"]
            and item["stateExtractHasForeshadow"]
            and item["stateExtractHasReaderQuestion"]
            and not item["missingTraceTypes"]
            and item["traceMetadataOk"]
            and item["contextMetadataPresent"]
            and item["contextRawTextIncluded"] is False
            for item in chapter_checks
        )
        verdict = "PASS" if chapters_ok and context_chain_ok and non_accepted_sources == 0 and exports_ok else "FAIL"
        report = {
            "gate": "Gate 7 Canon-State Execution 1-3",
            "verdict": verdict,
            "projectId": str(project.id),
            "qaRerunResults": qa_rerun_results,
            "chapterChecks": chapter_checks,
            "contextChainOk": context_chain_ok,
            "nonAcceptedCanonSources": non_accepted_sources,
            "projectLevelLedgers": project_level_ledgers,
            "exports": {"bibleMarkdown": bible_export, "novelMarkdown": novel_export, "exportsOk": exports_ok},
            "finishedAt": datetime.now(timezone.utc).isoformat(),
        }
        output = Path(args.output) if args.output else Path(".local/epic_novel_runs") / f"gate7_canon_state_{_utc_stamp()}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(
            json.dumps(
                {
                    "结论": verdict,
                    "输出文件": str(output),
                    "项目": str(project.id),
                    "章节通过": chapters_ok,
                    "上下文链路通过": context_chain_ok,
                    "非acceptedContent写canon": non_accepted_sources,
                    "导出文件化": exports_ok,
                    "章节检查": [
                        {
                            "章": item["chapterNumber"],
                            "状态": item["status"],
                            "阻断问题": item["openBlockingIssues"],
                            "缺失trace": item["missingTraceTypes"],
                            "contextRawTextIncluded": item["contextRawTextIncluded"],
                        }
                        for item in chapter_checks
                    ],
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
