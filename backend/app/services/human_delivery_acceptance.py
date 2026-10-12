import fcntl
import json
import zipfile
from datetime import datetime, timezone

from app.core.config import PROJECT_ROOT, settings
from app.core.quality_metrics import detect_final_hard_errors, measure_quality, format_report
from app.models import TaskStatus
from app.services.ai_or_finalizer import (
    FinalizerHold, correction_pieces, digest, parse_issues, split_chunks, validate_rewrite, word_document,
)
from app.services.general_longform_runtime import atomic_json, atomic_text


def verify_saved_delivery(directory, expected_hash, category):
    state = json.loads((directory / "manifest.json").read_text())
    source = (directory / "source.md").read_text()
    final = (directory / "final.md").read_text()
    if state["source_hash"] != digest(source) or digest(final) != expected_hash:
        raise FinalizerHold("审核稿或原稿已改变，不能采用旧确认")
    rebuilt, all_issues = [], []
    chunks = split_chunks(source, settings.AI_OR_CHUNK_CHARS)
    if len(state["chunks"]) != len(chunks):
        raise FinalizerHold("降AI和纠错断点不完整")
    for index, chunk in enumerate(chunks):
        entry = state["chunks"][index]
        if entry["hash"] != digest(chunk):
            raise FinalizerHold("分段源稿不匹配")
        if not chunk.strip() or chunk.lstrip().startswith(("#", "![")):
            rebuilt.append(chunk)
            continue
        receipt = json.loads((directory / f"{index:05d}_rewrite.json").read_text())
        ending = chunk[len(chunk.rstrip("\n")):]
        rewritten = receipt["outputText"].rstrip("\n") + ending
        validate_rewrite(chunk, rewritten)
        legacy = directory / f"{index:05d}_correction.json"
        if legacy.exists():
            corrected, issues = parse_issues(json.loads(legacy.read_text())["text"], rewritten)
            rebuilt.append(corrected)
            all_issues.extend(issues)
        else:
            plan = entry["correction_split"]
            pieces = correction_pieces(rewritten, plan["limit"])
            if plan["hashes"] != [digest(piece) for piece in pieces]:
                raise FinalizerHold("纠错窗口发生变化")
            for part, piece in enumerate(pieces):
                if not piece.strip():
                    rebuilt.append(piece)
                    continue
                result = json.loads((directory / f"{index:05d}_correction_{part:02d}.json").read_text())
                corrected, issues = parse_issues(result["text"], piece)
                rebuilt.append(corrected)
                all_issues.extend(issues)
    if "".join(rebuilt) != final or json.loads((directory / "issues.json").read_text()) != all_issues:
        raise FinalizerHold("审核稿与已保存纠错结果不一致")
    if detect_final_hard_errors(final, category=category):
        raise FinalizerHold("确认稿仍未通过终稿质检")
    with zipfile.ZipFile(directory / "delivery.zip") as archive:
        if archive.testzip() or archive.read("final.md").decode() != final:
            raise FinalizerHold("交付包校验失败")
    return state, final, all_issues


def accept_saved_delivery(db, task, expected_hash, reviewer):
    from app.tasks.tasks import _extract_book_title, _persist_chapters_from_markdown

    if not reviewer or task.status != TaskStatus.MANUAL_REVIEW or (task.config or {}).get("active_run_id"):
        raise FinalizerHold("只允许人工确认无运行中的审核任务")
    output_dir = PROJECT_ROOT / "backend/output" / f"task_{task.id}"
    directory = output_dir / "ai_or_finalizer"
    with (directory / "workflow.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        cfg = dict(task.config or {})
        category = cfg.get("category") or "social_science_core_conflict"
        state, final, issues = verify_saved_delivery(directory, expected_hash, category)
        approval = {"reviewer": reviewer, "at": datetime.now(timezone.utc).isoformat(),
                    "final_sha256": expected_hash, "source_sha256": state["source_hash"],
                    "issues_sha256": digest(json.dumps(issues, ensure_ascii=False, sort_keys=True)),
                    "severe_count": sum(issue["severe"] for issue in issues)}
        (directory / "正式书稿.docx").write_bytes(word_document(final))
        temporary_zip = directory / "delivery.accepted.zip"
        with zipfile.ZipFile(directory / "delivery.zip") as previous, zipfile.ZipFile(temporary_zip, "w", zipfile.ZIP_DEFLATED) as accepted:
            names = previous.namelist()
            for name in names:
                if name != "正式书稿.docx":
                    accepted.writestr(name, previous.read(name))
            accepted.write(directory / "正式书稿.docx", "正式书稿.docx")
        temporary_zip.replace(directory / "delivery.zip")
        state.update(status="completed", final_hash=expected_hash, issue_count=len(issues),
                     correction_model=settings.AI_OR_CORRECTION_MODEL or settings.DEEPSEEK_MODEL,
                     manual_approval=approval, artifacts={
                         "docx": next(name for name in names if name.startswith("【降AI后纠错】") and name.endswith(".docx")),
                         "xlsx": next(name for name in names if name.endswith(".xlsx")),
                         "zip": "delivery.zip", "accepted_docx": "正式书稿.docx"})
        if state.get("error"):
            state["last_hold_error"] = state.pop("error")
        atomic_json(directory / "manifest.json", state)
        count = _persist_chapters_from_markdown(db, task, final, category=category, commit=False)
        atomic_text(output_dir / "quality_report.txt", format_report(measure_quality(final, category=category)))
        for key in tuple(cfg):
            if key.startswith("generation_hold") or key.startswith("active_run"):
                cfg.pop(key)
        cfg.update(markdown=final, title=_extract_book_title(final), markdown_chars=len(final),
                   ai_or_finalizer_status="completed", ai_or_detection_status="not_requested",
                   ai_or_correction_model=state["correction_model"], human_delivery_approval=approval,
                   aigc_status="not_requested", quality_report=str(output_dir / "quality_report.txt"))
        task.config = cfg
        task.status = TaskStatus.COMPLETED
        task.progress = 100
        task.current_step = f"全部完成: {count} 章已入库（用户确认纠错稿）"
        task.error_message = None
        db.commit()
        return count
