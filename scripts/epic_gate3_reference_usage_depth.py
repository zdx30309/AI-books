"""Gate 3: 参考机制使用深度 live smoke。

该脚本只验证 ReferencePatternCards / ReferenceMechanismProfile 的接入深度，
不重新跑全量参考抽取，也不触发大规模章节生成。
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import (
    EpicBibleSection,
    EpicChapter,
    EpicChapterStatus,
    EpicNovelProject,
    EpicRetrievalTrace,
    ReferencePatternCard,
)
from app.services.epic_novel import (
    build_chapter_context_pack,
    check_chapter,
    compile_chapter_contracts,
    execute_chapter,
    forge_power_system,
    generate_bible_manifest,
    generate_bible_section,
    generate_ensemble_cast,
    generate_global_spine,
    generate_volume_contracts,
    generate_world_atlas,
    generate_world_bible,
    repair_chapter,
    repair_outline,
    run_bible_coverage_ci,
    run_outline_ci,
    run_worldview_tournament,
)
from app.services.epic_reference import search_pattern_cards


REQUIRED_TASK_TYPES = [
    "genesis_worldview_tournament",
    "world_bible",
    "world_atlas",
    "bureaucracy_system",
    "power_system",
    "ensemble_cast",
    "global_spine",
    "master_bible_manifest",
    "master_bible_section",
    "volume_contracts",
    "chapter_contract",
    "chapter_context_pack",
    "qa_bible_coverage_ci",
    "qa_outline_ci",
    "qa_chapter_check",
    "qa_planned_vs_actual_diff",
    "qa_acceptance_gate",
    "repair_outline",
    "repair_chapter_entry",
]


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S_%fZ")


def _find_project(db, project_id: str | None) -> EpicNovelProject:
    if project_id:
        project = db.query(EpicNovelProject).filter(EpicNovelProject.id == UUID(project_id)).first()
        if project is None:
            raise RuntimeError(f"找不到指定 EpicNovelProject: {project_id}")
        return project
    candidates = db.query(EpicNovelProject).order_by(EpicNovelProject.created_at.desc()).all()
    for project in candidates:
        accepted_sections = db.query(EpicBibleSection).filter(EpicBibleSection.project_id == project.id, EpicBibleSection.status == "accepted").count()
        accepted_chapters = db.query(EpicChapter).filter(EpicChapter.project_id == project.id, EpicChapter.status == EpicChapterStatus.ACCEPTED).count()
        if accepted_sections >= 3 and accepted_chapters >= 3:
            return project
    raise RuntimeError("没有找到可复跑 Gate 3 的项目：需要至少 3 个 accepted Bible section 和 3 个 accepted chapter。")


def _trace_ok(trace: EpicRetrievalTrace) -> tuple[bool, list[str]]:
    issues: list[str] = []
    if not trace.used_card_ids:
        issues.append("usedCardIds 为空")
    if not trace.used_profile_sections:
        issues.append("usedProfileSections 为空")
    if not trace.profile_version:
        issues.append("profileVersion 缺失")
    if trace.raw_text_included:
        issues.append("rawTextIncluded=true")
    if not trace.usage_summary:
        issues.append("usageSummary 缺失")
    raw = trace.raw_json or {}
    if raw.get("rawTextIncluded") is not False:
        issues.append("raw_json.rawTextIncluded 不是 false")
    if raw.get("productionReadyOnly") is not True:
        issues.append("productionReadyOnly 不是 true")
    return not issues, issues


def _latest_trace_by_type(db, project_id) -> dict[str, EpicRetrievalTrace]:
    rows = (
        db.query(EpicRetrievalTrace)
        .filter(EpicRetrievalTrace.project_id == project_id)
        .order_by(EpicRetrievalTrace.created_at.desc())
        .all()
    )
    latest: dict[str, EpicRetrievalTrace] = {}
    for row in rows:
        latest.setdefault(row.task_type, row)
    return latest


def _run_step(project_id, name: str, fn, *, retries: int = 1) -> dict[str, Any]:
    last_error: str | None = None
    for attempt in range(retries + 1):
        db = SessionLocal()
        started = time.time()
        try:
            fn(db, project_id)
            return {"name": name, "status": "pass", "seconds": round(time.time() - started, 3), "attempt": attempt + 1}
        except OperationalError as exc:
            db.rollback()
            last_error = str(exc)[:600]
            if attempt >= retries:
                return {
                    "name": name,
                    "status": "fail",
                    "seconds": round(time.time() - started, 3),
                    "attempt": attempt + 1,
                    "error": last_error,
                }
            time.sleep(2)
        finally:
            db.close()
    return {"name": name, "status": "fail", "seconds": 0, "attempt": retries + 1, "error": last_error or "unknown"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate 3 参考机制使用深度 smoke")
    parser.add_argument("--project-id", default=None)
    parser.add_argument("--chapter", type=int, default=4)
    parser.add_argument("--output", default=None)
    parser.add_argument("--no-execute", action="store_true", help="只补前置层和上下文 trace，不执行新章节")
    args = parser.parse_args()

    settings.EPIC_USE_EXISTING_WRITER_FOR_DRAFTS = False
    db = SessionLocal()
    try:
        project = _find_project(db, args.project_id)
        production_cards = search_pattern_cards(db, production_ready_only=True)
        old_source_less_cards = db.query(ReferencePatternCard).filter(ReferencePatternCard.source_section_ids == []).count()
        before_trace_count = db.query(EpicRetrievalTrace).filter(EpicRetrievalTrace.project_id == project.id).count()
        project_id = project.id
    finally:
        db.close()

    step_results: list[dict[str, Any]] = []
    steps = [
        ("worldview", lambda step_db, pid: run_worldview_tournament(step_db, pid)),
        ("world_bible", lambda step_db, pid: generate_world_bible(step_db, pid)),
        ("world_atlas", lambda step_db, pid: generate_world_atlas(step_db, pid)),
        ("power", lambda step_db, pid: forge_power_system(step_db, pid)),
        ("cast", lambda step_db, pid: generate_ensemble_cast(step_db, pid)),
        ("spine", lambda step_db, pid: generate_global_spine(step_db, pid)),
        ("manifest", lambda step_db, pid: generate_bible_manifest(step_db, pid)),
        (
            "section",
            lambda step_db, pid: generate_bible_section(
                step_db,
                pid,
                step_db.query(EpicBibleSection).filter(EpicBibleSection.project_id == pid).order_by(EpicBibleSection.order_index.asc()).first().section_key,
            ),
        ),
        ("volumes", lambda step_db, pid: generate_volume_contracts(step_db, pid)),
        ("contracts", lambda step_db, pid: compile_chapter_contracts(step_db, pid, 1)),
        ("context1", lambda step_db, pid: build_chapter_context_pack(step_db, pid, 1)),
        ("bible_ci", lambda step_db, pid: run_bible_coverage_ci(step_db, pid)),
        ("outline_ci", lambda step_db, pid: run_outline_ci(step_db, pid)),
        ("repair_outline", lambda step_db, pid: repair_outline(step_db, pid)),
        ("check_chapter", lambda step_db, pid: check_chapter(step_db, pid, 1)),
        ("repair_chapter", lambda step_db, pid: repair_chapter(step_db, pid, 1)),
    ]
    if not args.no_execute:
        steps.append(("execute_chapter", lambda step_db, pid: execute_chapter(step_db, pid, args.chapter)))

    for name, fn in steps:
        result = _run_step(project_id, name, fn, retries=1)
        step_results.append(result)
        print(json.dumps({"步骤": name, **result}, ensure_ascii=False), flush=True)
        if result["status"] != "pass":
            break

    db = SessionLocal()
    try:
        project = db.query(EpicNovelProject).filter(EpicNovelProject.id == project_id).first()
        if project is None:
            raise RuntimeError(f"项目消失: {project_id}")
        after_trace_count = db.query(EpicRetrievalTrace).filter(EpicRetrievalTrace.project_id == project.id).count()
        latest = _latest_trace_by_type(db, project.id)
        trace_checks: dict[str, dict[str, Any]] = {}
        missing_task_types: list[str] = []
        failing_task_types: list[str] = []
        for task_type in REQUIRED_TASK_TYPES:
            trace = latest.get(task_type)
            if trace is None:
                missing_task_types.append(task_type)
                trace_checks[task_type] = {"status": "missing"}
                continue
            ok, issues = _trace_ok(trace)
            if not ok:
                failing_task_types.append(task_type)
            trace_checks[task_type] = {
                "status": "pass" if ok else "fail",
                "issues": issues,
                "traceId": str(trace.id),
                "usedCardCount": len(trace.used_card_ids or []),
                "usedProfileSections": trace.used_profile_sections,
                "profileVersion": trace.profile_version,
                "rawTextIncluded": trace.raw_text_included,
                "overflowCount": len(trace.excluded_context or []),
            }

        step_failures = [item for item in step_results if item["status"] != "pass"]
        verdict = "PASS" if not step_failures and not missing_task_types and not failing_task_types and production_cards else "FAIL"
        report = {
            "gate": "Gate 3 Reference Usage Depth",
            "verdict": verdict,
            "projectId": str(project.id),
            "projectTitle": project.title,
            "chapterChecked": args.chapter,
            "productionReadyCardCount": len(production_cards),
            "oldSourceLessCardsExcluded": old_source_less_cards,
            "traceCountBefore": before_trace_count,
            "traceCountAfter": after_trace_count,
            "newTraceCount": after_trace_count - before_trace_count,
            "stepResults": step_results,
            "stepFailures": step_failures,
            "requiredTaskTypes": REQUIRED_TASK_TYPES,
            "missingTaskTypes": missing_task_types,
            "failingTaskTypes": failing_task_types,
            "traceChecks": trace_checks,
            "rawReferenceTextIncluded": any((trace.raw_text_included for trace in latest.values())),
            "finishedAt": datetime.now(timezone.utc).isoformat(),
        }
        output = Path(args.output) if args.output else Path(".local/epic_novel_runs") / f"gate3_reference_usage_depth_{_utc_stamp()}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(
            json.dumps(
                {
                    "结论": verdict,
                    "输出文件": str(output),
                    "项目": str(project.id),
                    "新增Trace": report["newTraceCount"],
                    "缺失任务": missing_task_types,
                    "失败任务": failing_task_types,
                    "productionReadyCardCount": len(production_cards),
                    "oldSourceLessCardsExcluded": old_source_less_cards,
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
