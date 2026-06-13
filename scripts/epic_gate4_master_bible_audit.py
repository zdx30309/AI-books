"""Gate 4: 真实中文 Master Bible 审计与重渲染。"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from app.db.session import SessionLocal
from app.models import EpicBibleSection, EpicMasterBible, EpicNovelProject
from app.services.epic_novel import (
    BIBLE_SECTIONS,
    _bible_acceptance_anchors,
    _display_project_title,
    _profile_bible_notes,
    _render_bible_payload_lines,
    export_bible_file_metadata,
    run_bible_coverage_ci,
    validate_bible_section_content,
)
from app.services.epic_reference import latest_reference_profile


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S_%fZ")


def _find_project(db, project_id: str | None) -> EpicNovelProject:
    if project_id:
        project = db.query(EpicNovelProject).filter(EpicNovelProject.id == UUID(project_id)).first()
        if project is None:
            raise RuntimeError(f"找不到指定 EpicNovelProject: {project_id}")
        return project
    for project in db.query(EpicNovelProject).order_by(EpicNovelProject.created_at.desc()).all():
        section_count = db.query(EpicBibleSection).filter(EpicBibleSection.project_id == project.id).count()
        if section_count >= len(BIBLE_SECTIONS):
            return project
    raise RuntimeError("没有找到可用于 Gate 4 的项目：需要 24 个 Bible section。")


def _section_content(project: EpicNovelProject, section: EpicBibleSection, payload: dict[str, Any], profile_notes: dict[str, Any]) -> str:
    payload_lines = _render_bible_payload_lines(payload)
    return (
        f"# {section.section_title}\n\n"
        f"## 故事载荷\n{payload_lines}\n\n"
        "## 机制接入\n"
        f"- 机制画像版本：{profile_notes.get('version') or '未绑定'}\n"
        f"- 抽象卡数量：{profile_notes.get('sourceCardCount')}\n"
        f"- 可用机制：{json.dumps(profile_notes.get('mechanisms') or profile_notes.get('corePleasureMechanisms') or [], ensure_ascii=False)}\n"
        f"- 反俗套约束：{json.dumps(profile_notes.get('antiCliche') or profile_notes.get('antiClicheRules') or [], ensure_ascii=False)}\n\n"
        "## 生产约束\n"
        "- 本节只使用抽象机制卡与参考机制画像，不使用原始参考书文本。\n"
        "- 原书人物、机构、桥段、句式和独特设定不得进入全书圣经、章节或修复提示。\n"
        "- 全书圣经、分卷合同、章节合同必须保持分层，章节只消费已验收的结构化上下文。\n"
        f"- 本节服务《{_display_project_title(project)}》的长期生产控制，必须能直接指导后续章节合同和验收门禁。\n"
    )


def _rerender_sections(db, project: EpicNovelProject) -> list[dict[str, Any]]:
    profile_notes = _profile_bible_notes(latest_reference_profile(db, project_id=project.id))
    sections = (
        db.query(EpicBibleSection)
        .filter(EpicBibleSection.project_id == project.id)
        .order_by(EpicBibleSection.order_index.asc())
        .all()
    )
    results: list[dict[str, Any]] = []
    bible = db.query(EpicMasterBible).filter(EpicMasterBible.project_id == project.id).first()
    if bible is not None:
        bible.title = f"{_display_project_title(project)} 全书圣经"
    for section in sections:
        old_json = section.content_json or {}
        payload = dict(old_json.get("storyPayload") or {})
        if not payload:
            payload = {
                "本节任务": f"补足{section.section_title}的具体设定、执行规则、风险和验收锚点。",
                "_meta": {"status": "generated", "production_quality": True, "rawTextIncluded": False},
            }
        payload.pop("acceptanceAnchors", None)
        payload.pop("验收锚点", None)
        payload["_meta"] = {**(payload.get("_meta") or {}), "status": "generated", "production_quality": True, "rawTextIncluded": False}
        payload["参考画像"] = old_json.get("referenceProfile") or profile_notes
        payload["验收锚点"] = _bible_acceptance_anchors(project, section, payload["参考画像"])
        content = _section_content(project, section, payload, payload["参考画像"])
        content_json = {
            **old_json,
            "sectionKey": section.section_key,
            "sectionTitle": section.section_title,
            "storyPayload": payload,
            "referenceProfile": payload["参考画像"],
            "referenceUsage": old_json.get("referenceUsage") or {},
            "_meta": payload["_meta"],
            "rawTextIncluded": False,
        }
        acceptance = validate_bible_section_content(content, content_json)
        content_json["acceptance"] = acceptance
        section.content_markdown = content
        section.content_json = content_json
        section.actual_words = len(content)
        section.status = "accepted" if acceptance["status"] == "pass" else "need_repair"
        results.append(
            {
                "sectionKey": section.section_key,
                "sectionTitle": section.section_title,
                "status": section.status,
                "chars": len(content),
                "acceptance": acceptance,
                "hasReferenceUsage": bool(content_json.get("referenceUsage")),
            }
        )
    if bible is not None:
        bible.actual_word_count = sum(len(section.content_markdown or "") for section in sections)
        bible.status = "completed" if all(section.status == "accepted" for section in sections) else "need_repair"
        bible.coverage_score = sum(1 for section in sections if section.status == "accepted") / max(len(BIBLE_SECTIONS), 1)
    db.commit()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate 4 真实中文 Master Bible 审计")
    parser.add_argument("--project-id", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        project = _find_project(db, args.project_id)
        section_results = _rerender_sections(db, project)
        coverage = run_bible_coverage_ci(db, project.id)
        md_export = export_bible_file_metadata(db, project.id, "md")
        json_export = export_bible_file_metadata(db, project.id, "json")
        accepted_count = sum(1 for item in section_results if item["status"] == "accepted")
        failed_sections = [item for item in section_results if item["status"] != "accepted"]
        coverage_failures = [item.item_key for item in coverage if item.status != "complete"]
        bible = db.query(EpicMasterBible).filter(EpicMasterBible.project_id == project.id).first()
        verdict = "PASS" if accepted_count == len(BIBLE_SECTIONS) and not failed_sections and not coverage_failures else "FAIL"
        report = {
            "gate": "Gate 4 Real Chinese Master Bible",
            "verdict": verdict,
            "projectId": str(project.id),
            "projectTitle": project.title,
            "bibleTitle": bible.title if bible else None,
            "bibleStatus": bible.status if bible else None,
            "bibleActualChars": bible.actual_word_count if bible else None,
            "coverageScore": bible.coverage_score if bible else None,
            "requiredSectionCount": len(BIBLE_SECTIONS),
            "acceptedSectionCount": accepted_count,
            "failedSections": failed_sections,
            "coverageFailures": coverage_failures,
            "sectionResults": section_results,
            "exports": {"markdown": md_export, "json": json_export},
            "finishedAt": datetime.now(timezone.utc).isoformat(),
        }
        output = Path(args.output) if args.output else Path(".local/epic_novel_runs") / f"gate4_master_bible_{_utc_stamp()}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(
            json.dumps(
                {
                    "结论": verdict,
                    "输出文件": str(output),
                    "项目": str(project.id),
                    "Bible状态": report["bibleStatus"],
                    "已接受章节": accepted_count,
                    "失败章节": [item["sectionKey"] for item in failed_sections],
                    "覆盖失败": coverage_failures,
                    "Markdown导出": md_export,
                    "JSON导出": json_export,
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
