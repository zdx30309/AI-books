"""Gate 9: 第一卷全卷验收。"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import (
    EpicAcceptanceReport,
    EpicCanonFact,
    EpicChapter,
    EpicChapterContract,
    EpicChapterStatus,
    EpicCharacterState,
    EpicFactionState,
    EpicForeshadow,
    EpicInstitutionState,
    EpicIssue,
    EpicNovelProject,
    EpicObjectState,
    EpicPleasureBeat,
    EpicPowerLedger,
    EpicReaderQuestion,
    EpicRetrievalTrace,
    EpicStateExtract,
    EpicVolumeContract,
)
from app.services.epic_novel import _display_project_title, check_chapter, compile_chapter_contracts, epic_export_path, execute_chapter


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
        if db.query(EpicVolumeContract).filter(EpicVolumeContract.project_id == project.id, EpicVolumeContract.volume_number == 1).first():
            return project
    raise RuntimeError("没有找到可用于 Gate 9 的项目。")


def _state_counts(db, project_id, chapter_number: int) -> dict[str, int]:
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


def _latest_traces(db, project_id, chapter_number: int) -> dict[str, EpicRetrievalTrace]:
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


def _chapter_evidence(db, project: EpicNovelProject, chapter_number: int) -> dict[str, Any]:
    chapter = db.query(EpicChapter).filter(EpicChapter.project_id == project.id, EpicChapter.chapter_number == chapter_number).first()
    report = (
        db.query(EpicAcceptanceReport)
        .filter(EpicAcceptanceReport.project_id == project.id, EpicAcceptanceReport.chapter_number == chapter_number)
        .order_by(EpicAcceptanceReport.created_at.desc())
        .first()
    )
    open_blocking = (
        db.query(EpicIssue)
        .filter(
            EpicIssue.project_id == project.id,
            EpicIssue.chapter_number == chapter_number,
            EpicIssue.status == "open",
            EpicIssue.severity.in_(["high", "severe"]),
        )
        .all()
    )
    traces = _latest_traces(db, project.id, chapter_number)
    context_trace = traces.get("chapter_context_pack")
    metadata = ((context_trace.raw_json or {}).get("contextMetadata") if context_trace else {}) or {}
    counts = _state_counts(db, project.id, chapter_number)
    return {
        "chapterNumber": chapter_number,
        "status": chapter.status if chapter else None,
        "acceptedContentPresent": bool(chapter.accepted_content) if chapter else False,
        "summaryPresent": bool(chapter.summary) if chapter else False,
        "endingHookPresent": bool(chapter.ending_hook) if chapter else False,
        "latestReportStatus": report.overall_status if report else None,
        "openBlockingIssues": [(issue.severity, issue.issue_type) for issue in open_blocking],
        "missingTraceTypes": sorted(REQUIRED_TRACE_TYPES - set(traces)),
        "rawTextIncluded": any(trace.raw_text_included for trace in traces.values()) or metadata.get("rawReferenceTextIncluded") is True,
        "usedCards": bool(context_trace and context_trace.used_card_ids),
        "profileVersion": context_trace.profile_version if context_trace else None,
        "stateCounts": counts,
        "ledgersPresent": all(counts[key] > 0 for key in counts),
    }


def _export_volume_markdown(project: EpicNovelProject, volume: EpicVolumeContract, chapters: list[EpicChapter]) -> dict[str, Any]:
    out = epic_export_path()
    out.mkdir(parents=True, exist_ok=True)
    filename = f"{_display_project_title(project)} 第{volume.volume_number}卷 正文.md"
    path = out / filename
    content = (
        f"# {_display_project_title(project)} 第{volume.volume_number}卷\n\n"
        f"## 卷目标\n{volume.local_main_plot or '推进本卷主线'}\n\n"
        + "\n\n".join(f"## {chapter.title}\n\n{chapter.accepted_content or ''}" for chapter in chapters)
    )
    path.write_text(content, encoding="utf-8")
    return {"path": str(path), "filename": filename, "bytes": path.stat().st_size, "contentReturned": False, "chapterCount": len(chapters)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate 9 第一卷全卷验收")
    parser.add_argument("--project-id", default=None)
    parser.add_argument("--volume", type=int, default=1)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    settings.EPIC_USE_EXISTING_WRITER_FOR_DRAFTS = False
    db = SessionLocal()
    try:
        project = _find_project(db, args.project_id)
        volume = db.query(EpicVolumeContract).filter(EpicVolumeContract.project_id == project.id, EpicVolumeContract.volume_number == args.volume).first()
        if volume is None:
            compile_chapter_contracts(db, project.id, args.volume)
            volume = db.query(EpicVolumeContract).filter(EpicVolumeContract.project_id == project.id, EpicVolumeContract.volume_number == args.volume).first()
        if volume is None:
            raise RuntimeError(f"第{args.volume}卷缺少 VolumeContract。")
        if "acceptance smoke" in (volume.title or "").lower():
            volume.title = f"第{args.volume}卷 阶段主线"
            db.commit()
        contracts = (
            db.query(EpicChapterContract)
            .filter(EpicChapterContract.project_id == project.id, EpicChapterContract.chapter_number >= volume.chapter_start, EpicChapterContract.chapter_number <= volume.chapter_end)
            .order_by(EpicChapterContract.chapter_number.asc())
            .all()
        )
        if len(contracts) < (volume.chapter_end - volume.chapter_start + 1):
            compile_chapter_contracts(db, project.id, args.volume)
        step_results: list[dict[str, Any]] = []
        for chapter_number in range(volume.chapter_start, volume.chapter_end + 1):
            chapter = db.query(EpicChapter).filter(EpicChapter.project_id == project.id, EpicChapter.chapter_number == chapter_number, EpicChapter.status == EpicChapterStatus.ACCEPTED).first()
            if chapter is None:
                chapter = execute_chapter(db, project.id, chapter_number)
            issues = check_chapter(db, project.id, chapter_number)
            evidence = _chapter_evidence(db, project, chapter_number)
            step_results.append({"chapterNumber": chapter_number, "status": evidence["status"], "blockingIssues": evidence["openBlockingIssues"]})
            if evidence["status"] != EpicChapterStatus.ACCEPTED or evidence["openBlockingIssues"]:
                break

        chapters = (
            db.query(EpicChapter)
            .filter(EpicChapter.project_id == project.id, EpicChapter.chapter_number >= volume.chapter_start, EpicChapter.chapter_number <= volume.chapter_end)
            .order_by(EpicChapter.chapter_number.asc())
            .all()
        )
        chapter_evidence = [_chapter_evidence(db, project, n) for n in range(volume.chapter_start, volume.chapter_end + 1)]
        accepted_count = sum(1 for item in chapter_evidence if item["status"] == EpicChapterStatus.ACCEPTED)
        non_accepted_sources = (
            db.query(EpicCanonFact)
            .filter(EpicCanonFact.project_id == project.id, EpicCanonFact.chapter_number >= volume.chapter_start, EpicCanonFact.chapter_number <= volume.chapter_end, EpicCanonFact.source != "acceptedContent")
            .count()
        )
        foreshadow_count = db.query(EpicForeshadow).filter(EpicForeshadow.project_id == project.id).count()
        reader_question_count = db.query(EpicReaderQuestion).filter(EpicReaderQuestion.project_id == project.id).count()
        volume_export = _export_volume_markdown(project, volume, [chapter for chapter in chapters if chapter.status == EpicChapterStatus.ACCEPTED])
        report_md = Path(".local/epic_novel_runs") / f"full_volume_acceptance_{_utc_stamp()}.md"
        report_md.parent.mkdir(parents=True, exist_ok=True)
        report_md.write_text(
            "# 第一卷全卷验收报告\n\n"
            f"- 项目: {project.id}\n"
            f"- 卷: {args.volume}\n"
            f"- accepted章节: {accepted_count}/{volume.chapter_end - volume.chapter_start + 1}\n"
            f"- nonAcceptedCanonSources: {non_accepted_sources}\n"
            f"- export: {volume_export['path']}\n",
            encoding="utf-8",
        )
        volume_ok = (
            accepted_count == (volume.chapter_end - volume.chapter_start + 1)
            and all(
                item["acceptedContentPresent"]
                and item["summaryPresent"]
                and item["endingHookPresent"]
                and item["latestReportStatus"] == "accepted"
                and not item["openBlockingIssues"]
                and not item["missingTraceTypes"]
                and item["rawTextIncluded"] is False
                and item["usedCards"]
                and item["profileVersion"]
                and item["ledgersPresent"]
                for item in chapter_evidence
            )
            and non_accepted_sources == 0
            and foreshadow_count > 0
            and reader_question_count > 0
            and volume_export["contentReturned"] is False
        )
        verdict = "PASS" if volume_ok else "FAIL"
        report = {
            "gate": "Gate 9 Full Volume Acceptance",
            "verdict": verdict,
            "projectId": str(project.id),
            "volumeNumber": args.volume,
            "volumeContract": {
                "title": volume.title,
                "chapterStart": volume.chapter_start,
                "chapterEnd": volume.chapter_end,
                "localMainPlot": volume.local_main_plot,
                "globalSpineFunction": volume.global_spine_function,
                "bureaucracyFunction": volume.bureaucracy_function,
            },
            "stepResults": step_results,
            "acceptedCount": accepted_count,
            "chapterEvidence": chapter_evidence,
            "nonAcceptedCanonSources": non_accepted_sources,
            "foreshadowCount": foreshadow_count,
            "readerQuestionCount": reader_question_count,
            "volumeExport": volume_export,
            "reportMarkdown": str(report_md),
            "finishedAt": datetime.now(timezone.utc).isoformat(),
        }
        output = Path(args.output) if args.output else Path(".local/epic_novel_runs") / f"gate9_full_volume_{_utc_stamp()}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(
            json.dumps(
                {
                    "结论": verdict,
                    "输出文件": str(output),
                    "项目": str(project.id),
                    "卷": args.volume,
                    "accepted章节": accepted_count,
                    "非acceptedContent写canon": non_accepted_sources,
                    "卷稿导出": volume_export["path"],
                    "报告": str(report_md),
                    "失败步骤": [item for item in step_results if item["status"] != EpicChapterStatus.ACCEPTED or item["blockingIssues"]],
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
