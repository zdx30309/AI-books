"""Gate 11: 生产调度、成本与恢复验收。"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from app.db.session import SessionLocal, init_db
from app.models import EpicCanonFact, EpicGenerationLog, EpicNovelProject
from app.services.epic_novel import (
    create_epic_production_job,
    dashboard,
    epic_job_snapshot,
    execute_chapter,
    pause_epic_production_job,
    record_epic_job_step,
    resume_epic_production_job,
    retry_epic_job_failed_only,
    stop_epic_production_job,
)
from app.services.epic_reference import get_reference_extraction_control, set_reference_extraction_control


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
        raise RuntimeError("没有找到可用于 Gate 11 的 EpicNovelProject。")
    return project


def _canon_count(db, project: EpicNovelProject, chapter_number: int = 1) -> int:
    return db.query(EpicCanonFact).filter(EpicCanonFact.project_id == project.id, EpicCanonFact.chapter_number == chapter_number).count()


def _recent_cost_logs(db, project: EpicNovelProject, action: str) -> list[dict[str, Any]]:
    rows = (
        db.query(EpicGenerationLog)
        .filter(EpicGenerationLog.project_id == project.id, EpicGenerationLog.action == action)
        .order_by(EpicGenerationLog.created_at.desc())
        .limit(20)
        .all()
    )
    return [
        {
            "id": str(row.id),
            "action": row.action,
            "provider": row.provider,
            "model": row.model,
            "promptTokens": row.prompt_tokens,
            "completionTokens": row.completion_tokens,
            "estimatedCost": row.estimated_cost,
            "actualCost": row.actual_cost,
            "status": row.status,
            "error": row.error,
        }
        for row in rows
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate 11 生产调度、成本与恢复验收")
    parser.add_argument("--project-id", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        project = _find_project(db, args.project_id)

        reference_pause = set_reference_extraction_control("paused")
        paused_control = get_reference_extraction_control()
        reference_resume = set_reference_extraction_control("running")
        resumed_control = get_reference_extraction_control()

        main_job = create_epic_production_job(
            db,
            project.id,
            job_type="chapter_generation",
            progress_total=6,
            max_estimated_cost=0.20,
            action="gate11_scheduler",
        )
        step_results: list[dict[str, Any]] = []
        step_results.append({"step": "reference_extraction", **record_epic_job_step(db, main_job, step_key="reference_extraction", estimated_cost=0.01, actual_cost=0.009)})
        pause_epic_production_job(main_job)
        db.commit()
        paused_attempt = record_epic_job_step(db, main_job, step_key="bible_generation", estimated_cost=0.01)
        paused_progress_after_attempt = main_job.progress_current
        resume_epic_production_job(main_job)
        db.commit()
        step_results.append({"step": "bible_generation", **record_epic_job_step(db, main_job, step_key="bible_generation", estimated_cost=0.015, actual_cost=0.014)})
        failed_step = record_epic_job_step(db, main_job, step_key="chapter_generation", estimated_cost=0.02, failed=True, error="模拟供应商临时中断")
        retry_result = retry_epic_job_failed_only(main_job)
        db.commit()
        resumed_after_failure = record_epic_job_step(
            db,
            main_job,
            step_key="chapter_generation",
            estimated_cost=0.02,
            actual_cost=0.018,
            continuation_chunk="长章节输出分片一，文件存储，不返回巨大响应。",
        )
        step_results.append({"step": "chapter_generation", **resumed_after_failure})
        step_results.append({"step": "qa", **record_epic_job_step(db, main_job, step_key="qa", estimated_cost=0.005)})
        step_results.append({"step": "repair", **record_epic_job_step(db, main_job, step_key="repair", estimated_cost=0.005)})
        step_results.append({"step": "export", **record_epic_job_step(db, main_job, step_key="export", estimated_cost=0.002)})

        stopped_job = create_epic_production_job(db, project.id, job_type="export", progress_total=1, action="gate11_scheduler_stop")
        stop_epic_production_job(stopped_job)
        db.commit()
        stopped_attempt = record_epic_job_step(db, stopped_job, step_key="export", estimated_cost=0.001)

        cost_cap_job = create_epic_production_job(db, project.id, job_type="reference_extraction", progress_total=1, max_estimated_cost=0.001, action="gate11_scheduler_cost_cap")
        cost_cap_result = record_epic_job_step(db, cost_cap_job, step_key="reference_batch_1", estimated_cost=0.01)

        before_canon = _canon_count(db, project, 1)
        execute_chapter(db, project.id, 1)
        execute_chapter(db, project.id, 1)
        after_canon = _canon_count(db, project, 1)

        main_snapshot = epic_job_snapshot(main_job)
        dashboard_payload = dashboard(db, project.id)
        cost_logs = _recent_cost_logs(db, project, "gate11_scheduler")
        report_md = Path(".local/epic_novel_runs") / f"production_scheduler_{_utc_stamp()}.md"
        report_md.parent.mkdir(parents=True, exist_ok=True)
        report_md.write_text(
            "# 生产调度、成本与恢复验收报告\n\n"
            f"- 项目: {project.id}\n"
            f"- 主任务: {main_job.id}\n"
            f"- 主任务状态: {main_job.status}\n"
            f"- 暂停尝试: {paused_attempt.get('reason')}\n"
            f"- 失败重试: {retry_result}\n"
            f"- 成本上限任务: {cost_cap_result.get('reason')}\n"
            f"- canon重试前后: {before_canon} -> {after_canon}\n",
            encoding="utf-8",
        )
        checks = {
            "referencePauseResumeControl": paused_control.get("state") == "paused" and resumed_control.get("state") == "running",
            "jobResumeAfterFailure": failed_step.get("reason") == "failed" and resumed_after_failure.get("advanced") is True and main_job.status == "completed",
            "retryFailedOnly": retry_result.get("retryFailedOnly") is True and retry_result.get("retryPendingSteps") == ["chapter_generation"],
            "costLogsRecorded": bool(cost_logs) and all(row["provider"] and row["model"] and row["promptTokens"] >= 0 and row["completionTokens"] >= 0 for row in cost_logs),
            "positiveEstimatedCostRecorded": any(float(row["estimatedCost"] or 0.0) > 0 for row in cost_logs),
            "continuationChunksRecorded": len(main_job.continuation_chunks or []) > 0,
            "noDuplicateCanonWritesAfterRetry": before_canon == after_canon,
            "pausedJobDoesNotContinue": paused_attempt.get("reason") == "paused" and paused_progress_after_attempt == 1,
            "resumedJobContinuesFromCheckpoint": "chapter_generation" in (main_job.checkpoint_json or {}).get("completedSteps", []),
            "costCapStopsSafely": cost_cap_result.get("reason") == "cost_cap" and cost_cap_job.progress_current == 0,
            "stoppedJobDoesNotContinue": stopped_attempt.get("reason") == "stopped" and stopped_job.progress_current == 0,
            "dashboardExposesJob": dashboard_payload.get("current_job") is not None and "estimatedRemainingCost" in dashboard_payload["current_job"],
            "reportWritten": report_md.exists() and report_md.stat().st_size > 0,
        }
        verdict = "PASS" if all(checks.values()) else "FAIL"
        report = {
            "gate": "Gate 11 Production Scheduler Cost Recovery",
            "verdict": verdict,
            "projectId": str(project.id),
            "mainJob": main_snapshot,
            "pausedAttempt": paused_attempt,
            "pausedProgressAfterAttempt": paused_progress_after_attempt,
            "failedStep": failed_step,
            "retryResult": retry_result,
            "stoppedAttempt": stopped_attempt,
            "costCapResult": cost_cap_result,
            "referenceControl": {"paused": reference_pause, "resumed": reference_resume},
            "canonCounts": {"before": before_canon, "after": after_canon},
            "costLogs": cost_logs,
            "dashboard": {
                "currentJob": dashboard_payload.get("current_job"),
                "jobProgress": dashboard_payload.get("job_progress"),
                "estimatedRemainingCost": dashboard_payload.get("estimated_remaining_cost"),
                "jobErrors": dashboard_payload.get("job_errors"),
                "retryStatus": dashboard_payload.get("retry_status"),
            },
            "checks": checks,
            "reportMarkdown": str(report_md),
            "boundary": "证明长任务控制、成本日志和恢复机制，不执行 300万字全文生成。",
        }
        output_path = Path(args.output) if args.output else Path(".local/epic_novel_runs") / f"gate11_scheduler_{_utc_stamp()}.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps({"verdict": verdict, "report": str(output_path), "checks": checks}, ensure_ascii=False))
        return 0 if verdict == "PASS" else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
