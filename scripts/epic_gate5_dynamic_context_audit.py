"""Gate 5: 动态上下文编译器与提示词安全审计。"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from app.db.session import SessionLocal
from app.models import EpicChapterContract, EpicNovelProject, EpicRetrievalTrace
from app.services.epic_context import compile_dynamic_context, contains_raw_reference_text
from app.services.epic_novel import _build_chapter_writer_prompt, build_chapter_context_pack


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S_%fZ")


def _synthetic_context() -> dict[str, Any]:
    return {
        "project": {"title": "无名天书", "premise": "古代疑案牵出天庭档案真相", "genre": "仙侠悬疑"},
        "chapterContract": {
            "chapter_number": 2,
            "title": "第二章 档案裂痕",
            "chapter_goal": "主角用证据链逼近第一处制度漏洞",
            "plot_function": "承接上一章钩子并制造更大疑问",
            "core_pleasure": "线索奖励",
            "bureaucracy_function": "让程序本身制造阻力",
            "world_atlas_function": "展示都城档案区",
            "ending_hook": "关键文书被调包",
            "must_execute": ["展示证据链", "让机构流程制造阻力"],
            "must_not_reveal": ["不要揭示幕后主谋"],
            "character_arc_deltas": [{"character": "主角", "delta": "从被动追问转为主动设局"}],
            "institution_used": ["天庭档案司"],
        },
        "bibleSections": [{"sectionKey": f"s{i}", "content": "圣经内容" * 500} for i in range(40)],
        "worldBible": {"summary": "以官僚秩序和证据链推动长期悬疑。"},
        "worldAtlas": {"regions": ["都城档案区", "外城灰市"]},
        "bureaucracy": [{"name": "天庭档案司", "function": "掌管文书流转"}],
        "powerSystem": {"rules": ["不得绕过证据链直接破局"]},
        "globalSpine": {"arc": "主角逐步发现制度本身被改写。"},
        "recentChapters": [{"chapterNumber": 1, "summary": "第一份档案出现矛盾。", "endingHook": "封存编号不存在。"}],
        "referenceInspirationPack": {
            "cards": [
                {
                    "id": "card-gate5-1",
                    "category": "plot_structure",
                    "function": "用证据链逐级增压",
                    "rawReferenceText": "RAW CARD TEXT MUST NOT ENTER PROMPT",
                }
            ],
            "profile": {
                "version": "profile-gate5",
                "corePleasureMechanisms": ["证据奖励", "制度阻力"],
                "antiClicheRules": ["禁止复制原书表达", "禁止用外挂跳过流程"],
                "originalityGuidance": ["只借鉴抽象机制，不借鉴桥段"],
            },
        },
        "referenceSafety": {"rawTextIncluded": False, "allowedInputs": ["ReferencePatternCards", "ReferenceMechanismProfile"]},
        "rawReferenceText": "RAW BOOK TEXT MUST NOT ENTER PROMPT",
    }


def _find_project(db, project_id: str | None) -> EpicNovelProject:
    if project_id:
        project = db.query(EpicNovelProject).filter(EpicNovelProject.id == UUID(project_id)).first()
        if project is None:
            raise RuntimeError(f"找不到指定 EpicNovelProject: {project_id}")
        return project
    candidates = db.query(EpicNovelProject).order_by(EpicNovelProject.created_at.desc()).all()
    for project in candidates:
        has_contract = db.query(EpicChapterContract).filter(EpicChapterContract.project_id == project.id).first() is not None
        if has_contract:
            return project
    raise RuntimeError("没有找到可用于 Gate 5 的项目：需要至少存在 ChapterContract。")


def _latest_context_trace(db, project_id, chapter_number: int) -> EpicRetrievalTrace | None:
    return (
        db.query(EpicRetrievalTrace)
        .filter(
            EpicRetrievalTrace.project_id == project_id,
            EpicRetrievalTrace.task_type == "chapter_context_pack",
            EpicRetrievalTrace.chapter_number == chapter_number,
        )
        .order_by(EpicRetrievalTrace.created_at.desc())
        .first()
    )


def _synthetic_checks() -> dict[str, Any]:
    context = _synthetic_context()
    small = compile_dynamic_context(context, forced_prompt_budget_tokens=900)
    large = compile_dynamic_context(context, forced_prompt_budget_tokens=20_000)
    prompt = _build_chapter_writer_prompt(context)

    checks = {
        "smallOverflowCount": small["metadata"]["overflowCount"],
        "smallHasExcluded": bool(small["metadata"].get("excluded")),
        "smallHasIncludedPaths": bool(small["metadata"].get("includedPaths")),
        "smallHasOverflowPaths": bool(small["metadata"].get("overflowPaths")),
        "smallNoSilentTruncation": small["metadata"].get("noSilentTruncation") is True,
        "zeroMeansProviderBudget": compile_dynamic_context(context, forced_prompt_budget_tokens=0)["metadata"]["promptBudgetTokens"] >= 1_000,
        "largeRawTextIncluded": contains_raw_reference_text(large["context"]),
        "largeSelectedCardIds": large["metadata"].get("selectedCardIds") or [],
        "largeProfileVersion": large["metadata"].get("profileVersion"),
        "largeExcludedRawReferenceFields": large["metadata"].get("excludedRawReferenceFields") or [],
        "promptHasFullContextJson": "full_context_json" in prompt,
        "promptHasRawBookText": "RAW BOOK TEXT MUST NOT ENTER PROMPT" in prompt,
        "promptHasRawCardText": "RAW CARD TEXT MUST NOT ENTER PROMPT" in prompt,
        "promptHasSelectedCardId": "card-gate5-1" in prompt,
        "promptHasProfileVersion": "profile-gate5" in prompt,
        "promptHasOverflowMetadata": '"overflowPaths"' in prompt and '"includedPaths"' in prompt,
    }
    checks["pass"] = (
        checks["smallOverflowCount"] > 0
        and checks["smallHasExcluded"]
        and checks["smallHasIncludedPaths"]
        and checks["smallHasOverflowPaths"]
        and checks["smallNoSilentTruncation"]
        and checks["zeroMeansProviderBudget"]
        and checks["largeRawTextIncluded"] is False
        and checks["largeSelectedCardIds"] == ["card-gate5-1"]
        and checks["largeProfileVersion"] == "profile-gate5"
        and "rawReferenceText" in checks["largeExcludedRawReferenceFields"]
        and "referenceInspirationPack.cards[0].rawReferenceText" in checks["largeExcludedRawReferenceFields"]
        and checks["promptHasFullContextJson"] is False
        and checks["promptHasRawBookText"] is False
        and checks["promptHasRawCardText"] is False
        and checks["promptHasSelectedCardId"]
        and checks["promptHasProfileVersion"]
        and checks["promptHasOverflowMetadata"]
    )
    return checks


def _live_checks(db, project: EpicNovelProject, chapter_number: int) -> dict[str, Any]:
    before = _latest_context_trace(db, project.id, chapter_number)
    context = build_chapter_context_pack(db, project.id, chapter_number)
    trace = _latest_context_trace(db, project.id, chapter_number)
    prompt = _build_chapter_writer_prompt(context)
    metadata = context.get("contextMetadata") or {}
    raw_json = trace.raw_json if trace is not None else {}
    trace_metadata = raw_json.get("contextMetadata") if isinstance(raw_json, dict) else {}
    checks = {
        "projectId": str(project.id),
        "chapterNumber": chapter_number,
        "traceBefore": str(before.id) if before else None,
        "traceAfter": str(trace.id) if trace else None,
        "newTraceCreatedOrUpdated": trace is not None and (before is None or trace.id != before.id),
        "contextHasCompiledPromptContext": bool(context.get("compiledPromptContext")),
        "metadataIncludedCount": len(metadata.get("included") or []),
        "metadataOverflowCount": metadata.get("overflowCount"),
        "metadataHasIncludedPaths": bool(metadata.get("includedPaths")),
        "metadataHasExcludedOrOverflow": "excluded" in metadata and "overflow" in metadata,
        "metadataSelectedCardIds": metadata.get("selectedCardIds") or [],
        "metadataProfileVersion": metadata.get("profileVersion"),
        "metadataRawTextIncluded": metadata.get("rawReferenceTextIncluded"),
        "traceRawTextIncluded": trace.raw_text_included if trace else None,
        "traceUsedCardIds": trace.used_card_ids if trace else [],
        "traceExcludedContextCount": len(trace.excluded_context or []) if trace else 0,
        "traceMetadataIncludedCount": len((trace_metadata or {}).get("included") or []),
        "traceMetadataOverflowCount": (trace_metadata or {}).get("overflowCount"),
        "promptHasFullContextJson": "full_context_json" in prompt,
        "promptHasExcludedRawTextPlaceholder": "[excluded raw reference text]" in prompt,
    }
    checks["pass"] = (
        checks["contextHasCompiledPromptContext"]
        and checks["metadataIncludedCount"] > 0
        and checks["metadataHasIncludedPaths"]
        and checks["metadataHasExcludedOrOverflow"]
        and checks["metadataRawTextIncluded"] is False
        and checks["traceRawTextIncluded"] is False
        and bool(checks["traceUsedCardIds"])
        and checks["traceMetadataIncludedCount"] > 0
        and checks["promptHasFullContextJson"] is False
        and checks["promptHasExcludedRawTextPlaceholder"] is False
    )
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate 5 动态上下文编译器与提示词安全审计")
    parser.add_argument("--project-id", default=None)
    parser.add_argument("--chapter", type=int, default=1)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    synthetic = _synthetic_checks()
    db = SessionLocal()
    try:
        project = _find_project(db, args.project_id)
        live = _live_checks(db, project, args.chapter)
        verdict = "PASS" if synthetic["pass"] and live["pass"] else "FAIL"
        report = {
            "gate": "Gate 5 Dynamic Context Compiler",
            "verdict": verdict,
            "syntheticChecks": synthetic,
            "liveChecks": live,
            "finishedAt": datetime.now(timezone.utc).isoformat(),
        }
        output = Path(args.output) if args.output else Path(".local/epic_novel_runs") / f"gate5_dynamic_context_{_utc_stamp()}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(
            json.dumps(
                {
                    "结论": verdict,
                    "输出文件": str(output),
                    "项目": str(project.id),
                    "章节": args.chapter,
                    "合成检查通过": synthetic["pass"],
                    "活体检查通过": live["pass"],
                    "合成溢出项": synthetic["smallOverflowCount"],
                    "活体已用卡片": live["traceUsedCardIds"],
                    "活体元数据溢出项": live["metadataOverflowCount"],
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
