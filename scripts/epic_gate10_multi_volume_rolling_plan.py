"""Gate 10: 多卷滚动规划验收。"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from app.db.session import SessionLocal
from app.models import EpicNovelProject, EpicRetrievalTrace
from app.services.epic_novel import _display_project_title, build_multi_volume_rolling_plan, epic_export_path


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
        raise RuntimeError("没有找到可用于 Gate 10 的 EpicNovelProject。")
    return project


def _write_plan_exports(project: EpicNovelProject, plan: dict[str, Any]) -> dict[str, Any]:
    out = epic_export_path()
    out.mkdir(parents=True, exist_ok=True)
    title = _display_project_title(project)
    json_path = out / f"{title} 多卷滚动规划.json"
    md_path = out / f"{title} 多卷滚动规划.md"
    json_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    lines = [
        f"# {title} 多卷滚动规划",
        "",
        f"- 项目: {project.id}",
        f"- 验证状态: {plan.get('validation', {}).get('status')}",
        f"- 分卷数: {len(plan.get('volumes') or [])}",
        f"- 原始参考文本进入计划: {plan.get('rollingContext', {}).get('rawReferenceTextIncluded')}",
        "",
        "## 分卷滚动链",
        "",
    ]
    for volume in plan.get("volumes") or []:
        lines.extend(
            [
                f"### 第{volume.get('volumeNumber')}卷 {volume.get('title')}",
                f"- 全局职责: {volume.get('globalSpineRole')}",
                f"- 本卷案件/谜团: {volume.get('localCaseMystery')}",
                f"- 世界扩张: {volume.get('worldExpansion')}",
                f"- 官僚/势力: {json.dumps(volume.get('bureaucracyFactionUse'), ensure_ascii=False)}",
                f"- 伏笔流: {json.dumps(volume.get('foreshadowFlow'), ensure_ascii=False)}",
                f"- 读者疑问流: {json.dumps(volume.get('readerQuestionFlow'), ensure_ascii=False)}",
                f"- 卷尾爆点: {volume.get('endingExplosion')}",
                "",
            ]
        )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "jsonPath": str(json_path),
        "markdownPath": str(md_path),
        "jsonBytes": json_path.stat().st_size,
        "markdownBytes": md_path.stat().st_size,
        "contentReturned": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate 10 多卷滚动规划验收")
    parser.add_argument("--project-id", default=None)
    parser.add_argument("--min-volumes", type=int, default=3)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        project = _find_project(db, args.project_id)
        plan = build_multi_volume_rolling_plan(db, project.id)
        validation = plan.get("validation") or {}
        trace = None
        if plan.get("traceId"):
            trace = db.query(EpicRetrievalTrace).filter(EpicRetrievalTrace.id == UUID(str(plan["traceId"]))).first()
        export = _write_plan_exports(project, plan)
        rolling_context = plan.get("rollingContext") or {}
        old_state = rolling_context.get("oldStateCompression") or {}
        preserved_state_count = sum(
            len(old_state.get(key) or [])
            for key in [
                "activeFacts",
                "activeCharacterStates",
                "activePowerStates",
                "activeFactionStates",
                "activeInstitutionStates",
            ]
        )
        report_md = Path(".local/epic_novel_runs") / f"multi_volume_rolling_plan_{_utc_stamp()}.md"
        report_md.parent.mkdir(parents=True, exist_ok=True)
        report_md.write_text(
            "# 多卷滚动规划验收报告\n\n"
            f"- 项目: {project.id}\n"
            f"- 状态: {validation.get('status')}\n"
            f"- 分卷数: {len(plan.get('volumes') or [])}\n"
            f"- blocking issues: {validation.get('blockingIssueCount')}\n"
            f"- trace: {plan.get('traceId')}\n"
            f"- export: {export['markdownPath']}\n",
            encoding="utf-8",
        )
        checks = {
            "volumeCountOk": len(plan.get("volumes") or []) >= args.min_volumes,
            "validationPass": validation.get("status") == "pass",
            "traceExists": trace is not None,
            "traceRawTextExcluded": bool(trace is not None and trace.raw_text_included is False),
            "traceUsesReference": bool(trace is not None and (trace.used_card_ids or trace.profile_version)),
            "rawReferenceExcluded": rolling_context.get("rawReferenceTextIncluded") is False,
            "oldStateCompressionPreserved": preserved_state_count > 0,
            "activeUnresolvedLedgerCarried": bool((rolling_context.get("activeUnresolvedLedger") or {}).get("foreshadows") or (rolling_context.get("activeUnresolvedLedger") or {}).get("readerQuestions")),
            "futureRevealConstraintsPresent": bool(rolling_context.get("futureRevealConstraints")),
            "exportWritten": export["contentReturned"] is False and export["jsonBytes"] > 0 and export["markdownBytes"] > 0,
            "reportWritten": report_md.exists() and report_md.stat().st_size > 0,
        }
        verdict = "PASS" if all(checks.values()) else "FAIL"
        report = {
            "gate": "Gate 10 Multi-Volume Rolling Plan",
            "verdict": verdict,
            "projectId": str(project.id),
            "volumeCount": len(plan.get("volumes") or []),
            "validation": validation,
            "checks": checks,
            "traceId": plan.get("traceId"),
            "export": export,
            "reportMarkdown": str(report_md),
            "boundary": "证明 2-3 个已规划分卷的滚动上下文与长线连续性，不证明 300万字全量生成。",
        }
        output_path = Path(args.output) if args.output else Path(".local/epic_novel_runs") / f"gate10_multi_volume_{_utc_stamp()}.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps({"verdict": verdict, "report": str(output_path), "planExport": export, "checks": checks}, ensure_ascii=False, default=str))
        return 0 if verdict == "PASS" else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
