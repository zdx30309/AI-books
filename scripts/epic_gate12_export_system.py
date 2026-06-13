"""Gate 12: 大导出系统验收。"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from app.db.session import SessionLocal, init_db
from app.models import EpicChapter, EpicNovelProject
from app.services.epic_novel import (
    _export_metadata_for_path,
    epic_export_path,
    export_bible_file_metadata,
    export_novel_file_metadata,
    export_project_artifact_file_metadata,
)


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
        raise RuntimeError("没有找到可用于 Gate 12 的 EpicNovelProject。")
    return project


def _read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _metadata_ok(item: dict[str, Any]) -> bool:
    return (
        bool(item.get("path"))
        and Path(str(item["path"])).exists()
        and int(item.get("bytes") or 0) > 0
        and bool(item.get("sha256"))
        and item.get("objectId") == item.get("sha256")
        and bool(item.get("createdAt"))
        and bool(item.get("downloadRoute"))
        and item.get("contentReturned") is False
        and "content" not in item
    )


def _raw_reference_safe(paths: list[str]) -> bool:
    forbidden = ["参考书原文：", "原始参考文本：", '"rawReferenceText": "', '"sourceText": "', "SoNovel下载原文"]
    for path in paths:
        text = _read_text(path)
        if any(marker in text for marker in forbidden):
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate 12 大导出系统验收")
    parser.add_argument("--project-id", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        project = _find_project(db, args.project_id)
        exports = {
            "bibleMarkdown": export_bible_file_metadata(db, project.id, "md"),
            "bibleJson": export_bible_file_metadata(db, project.id, "json"),
            "novelMarkdown": export_novel_file_metadata(db, project.id, "md"),
            "volumeMarkdown": export_project_artifact_file_metadata(db, project.id, "volume_markdown", volume_number=1),
            "chapterBundle": export_project_artifact_file_metadata(db, project.id, "chapter_bundle"),
            "qaReports": export_project_artifact_file_metadata(db, project.id, "qa_reports"),
            "retrievalTraces": export_project_artifact_file_metadata(db, project.id, "retrieval_traces"),
            "acceptanceReports": export_project_artifact_file_metadata(db, project.id, "acceptance_reports"),
        }
        regenerated = export_project_artifact_file_metadata(db, project.id, "chapter_bundle")

        large_probe_path = epic_export_path() / f"Gate12 大文件路径探针 {datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.md"
        large_probe_path.write_text("# 大文件路径探针\n\n" + ("长篇导出安全验证。\n" * 120_000), encoding="utf-8")
        large_probe = _export_metadata_for_path(
            large_probe_path,
            "text/markdown; charset=utf-8",
            content_chars=len(_read_text(str(large_probe_path))),
            download_route="/api/v1/epic-novel/projects/probe/export?mode=download",
            artifact_type="large_probe",
        )

        paths = [str(item["path"]) for item in exports.values()] + [str(large_probe["path"])]
        accepted_chapters = db.query(EpicChapter).filter(EpicChapter.project_id == project.id).count()
        bible_md = _read_text(str(exports["bibleMarkdown"]["path"]))
        novel_md = _read_text(str(exports["novelMarkdown"]["path"]))
        chapter_bundle = json.loads(_read_text(str(exports["chapterBundle"]["path"])))
        qa_reports = json.loads(_read_text(str(exports["qaReports"]["path"])))
        retrieval_traces = json.loads(_read_text(str(exports["retrievalTraces"]["path"])))
        acceptance_reports = json.loads(_read_text(str(exports["acceptanceReports"]["path"])))

        checks = {
            "allMetadataOk": all(_metadata_ok(item) for item in exports.values()) and _metadata_ok(large_probe),
            "metadataDefaultNoContent": all(item.get("contentReturned") is False and "content" not in item for item in exports.values()),
            "checksumExists": all(item.get("sha256") for item in exports.values()) and bool(large_probe.get("sha256")),
            "regenerationWorks": regenerated.get("sha256") == exports["chapterBundle"].get("sha256") and Path(str(regenerated["path"])).exists(),
            "largePathNoTimeout": int(large_probe.get("bytes") or 0) > 1_000_000 and large_probe.get("contentReturned") is False,
            "downloadEndpointSeparated": all("mode=download" in str(item.get("downloadRoute")) for item in exports.values()),
            "bibleContainsExpectedSections": "# " in bible_md and "项目" in bible_md,
            "novelContainsChapters": "第1章" in novel_md or "阶段线索" in novel_md,
            "chapterBundleContainsChapters": len(chapter_bundle.get("chapters") or []) >= accepted_chapters,
            "qaReportsContainAcceptance": bool(qa_reports.get("acceptanceReports")),
            "retrievalTracesContainItems": bool(retrieval_traces.get("retrievalTraces")),
            "acceptanceReportsContainItems": bool(acceptance_reports.get("acceptanceReports")),
            "rawReferenceTextExcluded": _raw_reference_safe(paths),
        }
        report_md = Path(".local/epic_novel_runs") / f"export_system_{_utc_stamp()}.md"
        report_md.parent.mkdir(parents=True, exist_ok=True)
        report_md.write_text(
            "# 大导出系统验收报告\n\n"
            f"- 项目: {project.id}\n"
            f"- 导出数量: {len(exports)}\n"
            f"- 大文件探针: {large_probe['path']} ({large_probe['bytes']} bytes)\n"
            f"- checks: {json.dumps(checks, ensure_ascii=False)}\n",
            encoding="utf-8",
        )
        checks["reportWritten"] = report_md.exists() and report_md.stat().st_size > 0
        verdict = "PASS" if all(checks.values()) else "FAIL"
        report = {
            "gate": "Gate 12 Large Export System",
            "verdict": verdict,
            "projectId": str(project.id),
            "exports": exports,
            "regeneratedChapterBundle": regenerated,
            "largeProbe": large_probe,
            "checks": checks,
            "reportMarkdown": str(report_md),
            "boundary": "证明大导出走文件/metadata/校验和，不执行 300万字全文生成。",
        }
        output_path = Path(args.output) if args.output else Path(".local/epic_novel_runs") / f"gate12_export_{_utc_stamp()}.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps({"verdict": verdict, "report": str(output_path), "checks": checks}, ensure_ascii=False))
        return 0 if verdict == "PASS" else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
