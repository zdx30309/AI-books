"""Maintenance-only recovery for durable manuscript checkpoints.

The normal Celery tasks are intentionally conservative around paid provider
calls.  This module contains the complementary *no-new-model-call* paths used
by the maintenance worker: promote a complete artifact that was never written
to the database, and repair only deterministic final-format noise left by an
older worker.

All decisions are based on local files, database state, and deterministic
quality checks.  In particular, this module must never call a model provider.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT, settings
from app.core.quality_metrics import detect_final_hard_errors, format_report, measure_quality
from app.db.session import SessionLocal
from app.models import Chapter, Task, TaskStatus
from app.prompts.goal_driven_templates import normalize_category
from app.services.content_engine import (
    _finance_chapter_parts,
    _finance_chapter_titles,
    _finance_catalog_titles,
    normalize_final_markdown,
)
from app.utils.task_dispatch_guard import clear_task_dispatch_guard

logger = logging.getLogger(__name__)


_DETERMINISTIC_FORMAT_ISSUE_NAMES = frozenset(
    {
        "Markdown 粗体残留",
        "# 标题残留",
        "非法正文 @ 段首",
        "正文内编号伪标题",
        "裸短小标题",
    }
)


def _task_module():
    """Import the task module lazily to avoid Celery discovery cycles."""

    from app.tasks import tasks as module

    return module


def _task_output_dir(task_id: object) -> Path:
    return PROJECT_ROOT / "backend" / "output" / f"task_{task_id}"


def _read_nonempty_artifact(path: Path) -> str | None:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return None
        value = path.read_text(encoding="utf-8").strip()
        if not value:
            return None
        normalized = value.lower()
        # A provider refusal/diagnostic is evidence, not a manuscript.  Do
        # not let it become the canonical final artifact during recovery.
        if len(value) < 2_000 and any(
            marker.lower() in normalized
            for marker in (
                "待修复终稿",
                "无法在不新增内容的前提下",
                "请补充缺失部分",
                "provider returned an empty body",
            )
        ):
            return None
        return value
    except (OSError, UnicodeError):
        logger.warning("failed to read recovery artifact %s", path, exc_info=True)
        return None


def _load_json_artifact(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeError, ValueError, TypeError):
        return None


def _front_section_payload(markdown: str, names: tuple[str, ...]) -> str:
    """Extract a front-matter section without interpreting body prose."""

    escaped = "|".join(re.escape(item) for item in names)
    marker = re.compile(rf"(?m)^(?:！{{3}})?@?\s*(?:{escaped})\s*$")
    match = marker.search(markdown or "")
    if match is None:
        return ""
    boundary = re.compile(
        r"(?m)^(?:！{3})?@?\s*(?:最终书名|三个备选书名|最终简介|三个备选简介|目录|正文)\s*$"
    )
    next_match = boundary.search(markdown, match.end())
    section = markdown[match.end() : next_match.start() if next_match else len(markdown)]
    cleaned: list[str] = []
    for line in section.splitlines():
        value = line.strip()
        if value.startswith("@"):
            value = value[1:].strip()
        if value:
            cleaned.append(value)
    return "\n".join(cleaned).strip()


def _strict_manuscript_parts(
    markdown: str,
    *,
    expected_chapters: int | None = None,
    minimum_chapter_chars: int = 40,
) -> tuple[str, str, list[tuple[int, str, str]], list[str]]:
    """Validate front matter, body markers, numbering, and non-empty chapters."""

    task_module = _task_module()
    errors: list[str] = []
    text = (markdown or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return "", "", [], ["empty manuscript"]

    title = _front_section_payload(text, ("最终书名", "三个备选书名"))
    summary = _front_section_payload(text, ("最终简介", "三个备选简介"))
    catalog = _front_section_payload(text, ("目录",))
    body_match = task_module._BODY_SECTION_RE.search(text)  # noqa: SLF001
    if not title:
        errors.append("missing title front section")
    if not summary:
        errors.append("missing summary front section")
    if not catalog:
        errors.append("missing catalog front section")
    if body_match is None:
        errors.append("missing body section")
        return text, title, [], errors

    body = text[body_match.end() :]
    matches = list(task_module._CHAPTER_HEADING_RE.finditer(body))  # noqa: SLF001
    if not matches:
        errors.append("missing chapter markers")
        return text, title, [], errors

    numbers = [int(item.group(1)) for item in matches]
    if numbers != list(range(1, len(numbers) + 1)):
        errors.append(f"chapter numbering is not contiguous: {numbers[:12]}")
    if expected_chapters and len(numbers) != int(expected_chapters):
        errors.append(
            f"chapter count {len(numbers)} does not match expected {int(expected_chapters)}"
        )

    chapters: list[tuple[int, str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        content = body[match.end() : end].strip()
        if len(re.sub(r"\s+", "", content)) < max(1, int(minimum_chapter_chars)):
            errors.append(f"chapter {match.group(1)} is empty or truncated")
        chapters.append((int(match.group(1)), match.group(2).strip(), content))

    catalog_numbers = [
        int(item.group(1))
        for item in re.finditer(r"(?m)^\s*@?\s*(\d+)[.、]\s*", catalog)
    ]
    if catalog_numbers and catalog_numbers != numbers:
        errors.append("catalog chapter numbering does not match body")
    return text, title, chapters, errors


def _format_failure_evidence(task: Task, output_dir: Path) -> bool:
    task_module = _task_module()
    evidence = task_module._task_failure_evidence(task)  # noqa: SLF001
    if any(name.lower() in evidence for name in _DETERMINISTIC_FORMAT_ISSUE_NAMES):
        return True
    for filename in (
        "final_hard_error_issues.txt",
        "final_hard_error_remaining.txt",
        "04_format_error.txt",
    ):
        value = _read_nonempty_artifact(output_dir / filename)
        if value and (
            any(name in value for name in _DETERMINISTIC_FORMAT_ISSUE_NAMES)
            or "终稿硬伤质检" in value
            or "format" in value.lower()
        ):
            return True
    return False


def _persist_recovered_manuscript(
    db: Session,
    task: Task,
    *,
    markdown: str,
    title: str,
    summary: str,
    chapters: list[tuple[int, str, str]],
    output_dir: Path,
    source_artifact: str,
    recovery_kind: str,
) -> None:
    """Promote a validated artifact and update its database rows in one pass."""

    canonical_path = output_dir / "04_format.md"
    if source_artifact != canonical_path.name:
        original = _read_nonempty_artifact(canonical_path)
        if original and original != markdown:
            (output_dir / "04_format_pre_recovery.md").write_text(original, encoding="utf-8")
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_path = canonical_path.with_suffix(canonical_path.suffix + ".recovery.tmp")
    temp_path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    temp_path.replace(canonical_path)

    db.query(Chapter).filter(Chapter.task_id == task.id).delete(synchronize_session=False)
    for number, chapter_title, content in chapters:
        db.add(
            Chapter(
                task_id=task.id,
                chapter_num=number,
                title=chapter_title[:512],
                content=content,
                word_count=len(content),
            )
        )
    db.flush()

    category = str((task.config or {}).get("category") or "")
    quality = measure_quality(markdown, category=category)
    report_path = output_dir / "quality_report.txt"
    report_path.write_text(format_report(quality), encoding="utf-8")
    recovery_report = {
        "kind": recovery_kind,
        "source_artifact": source_artifact,
        "chapter_count": len(chapters),
        "markdown_chars": len(markdown),
        "quality_violations": int(quality.total_violations),
        "recovered_at": datetime.now(timezone.utc).isoformat(),
    }
    (output_dir / "deterministic_recovery_report.json").write_text(
        json.dumps(recovery_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    cfg = dict(task.config or {})
    aigc_required = bool(cfg.get("aigc_required"))
    cfg.update(
        markdown=markdown,
        markdown_chars=len(markdown),
        title=(title or _task_module()._extract_book_title(markdown))[:120],  # noqa: SLF001
        summary=summary[:20_000],
        actual_chapters=len(chapters),
        actual_length=len(re.sub(r"\s+", "", "\n".join(item[2] for item in chapters))),
        quality_report=str(report_path),
        quality_report_summary=int(quality.total_violations),
        deterministic_recovery=True,
        deterministic_recovery_kind=recovery_kind,
        deterministic_recovery_at=datetime.now(timezone.utc).isoformat(),
        aigc_status="passed" if aigc_required else "not_required",
        aigc_passed=True,
        aigc_quality_passed=True,
        aigc_report_available=aigc_required,
    )
    for key in (
        "active_run_id",
        "active_run_started_at",
        "active_run_heartbeat_at",
        "auto_retry_not_before",
        "auto_retry_scheduled_by",
        "last_auto_retry_error",
        "auto_retry_exhausted_at",
        "generation_hold",
        "generation_hold_reason",
        "generation_hold_step",
        "generation_hold_at",
        "paid_call_delivery_uncertain",
        "paid_call_manual_resume_required",
        "paid_api_backoff",
        "paid_api_backoff_until",
        "paid_api_backoff_reason",
    ):
        cfg.pop(key, None)
    task_module = _task_module()
    if hasattr(task_module, "_clear_public_domain_failure_state"):
        cfg = task_module._clear_public_domain_failure_state(cfg)  # noqa: SLF001
    task.config = cfg
    task.status = TaskStatus.COMPLETED
    task.progress = 100
    task.current_step = f"完整生成：{len(chapters)} 章（断点安全恢复）"[:255]
    task.error_message = None
    db.commit()


def recover_deterministic_final_format_tasks(*, limit: int = 500) -> int:
    """Complete old normal tasks whose only remaining fault was formatting."""

    if not settings.STALE_TASK_WATCHDOG_ENABLED:
        return 0
    db: Session = SessionLocal()
    recovered = 0
    try:
        rows = (
            db.execute(
                select(Task)
                .where(Task.status.in_((TaskStatus.FAILED, TaskStatus.PENDING, TaskStatus.PROCESSING)))
                .with_for_update(skip_locked=True)
                .limit(max(1, limit))
            )
            .scalars()
            .all()
        )
        for task in rows:
            cfg = dict(task.config or {})
            if (
                bool(cfg.get("public_domain_longform_v2"))
                or bool(cfg.get("aigc_required"))
                or cfg.get("generation_hold")
                or cfg.get("paid_call_delivery_uncertain")
                or cfg.get("paid_call_manual_resume_required")
                or str(cfg.get("active_run_id") or "").strip()
                or str(cfg.get("category") or "") == "aigc_review"
                or task.status == TaskStatus.MANUAL_REVIEW
            ):
                continue
            output_dir = _task_output_dir(task.id)
            final_path = output_dir / "04_format.md"
            source_artifact = final_path.name
            raw = _read_nonempty_artifact(final_path)
            if raw is None:
                if not _format_failure_evidence(task, output_dir):
                    continue
                refine_path = output_dir / "03_refine.md"
                raw = _read_nonempty_artifact(refine_path)
                source_artifact = refine_path.name
                if raw is None:
                    continue
            elif task.status != TaskStatus.PROCESSING and not _format_failure_evidence(task, output_dir):
                # A completed final artifact with no failure evidence may be a
                # deliberately held/manual item; do not silently override it.
                continue

            category = str(cfg.get("category") or "")
            normalized = normalize_final_markdown(raw, category=category)
            issues = detect_final_hard_errors(normalized, category=category)
            if issues:
                # Normalization must eliminate every format-only hit. Anything
                # left is semantic and requires a model/operator decision.
                continue
            if source_artifact != final_path.name and not _format_failure_evidence(task, output_dir):
                continue
            text, title, chapters, errors = _strict_manuscript_parts(normalized)
            if errors or not chapters:
                continue
            try:
                _persist_recovered_manuscript(
                    db,
                    task,
                    markdown=text,
                    title=title,
                    summary=_front_section_payload(text, ("最终简介", "三个备选简介")),
                    chapters=chapters,
                    output_dir=output_dir,
                    source_artifact=source_artifact,
                    recovery_kind="deterministic_final_format",
                )
                clear_task_dispatch_guard(str(task.id))
                recovered += 1
            except Exception:
                db.rollback()
                logger.exception("failed to promote deterministic artifact for task %s", task.id)
    except Exception:
        db.rollback()
        logger.exception("deterministic final-format recovery failed")
    finally:
        db.close()
    return recovered


def _public_artifact_expected_chapters(
    task: Task, output_dir: Path, config: dict[str, Any]
) -> int | None:
    coverage = _load_json_artifact(output_dir / "05_classical_coverage_manifest.json")
    if coverage:
        expected = int(coverage.get("expected_chapters") or 0)
        translated = int(coverage.get("translated_chapters") or 0)
        if coverage.get("status") != "complete" or expected <= 0 or translated != expected:
            return -1
        return expected
    for key in ("actual_chapters", "target_chapters"):
        value = int(config.get(key) or 0)
        if value > 0:
            return value
    return None


def recover_completed_public_domain_tasks(*, limit: int = 500) -> int:
    """Promote already-generated public-domain artifacts without paid calls."""

    if not settings.STALE_TASK_WATCHDOG_ENABLED:
        return 0
    db: Session = SessionLocal()
    recovered = 0
    try:
        rows = (
            db.execute(
                select(Task)
                .where(Task.status.in_((TaskStatus.FAILED, TaskStatus.PENDING, TaskStatus.PROCESSING)))
                .with_for_update(skip_locked=True)
                .limit(max(1, limit))
            )
            .scalars()
            .all()
        )
        for task in rows:
            cfg = dict(task.config or {})
            if not (cfg.get("public_domain_longform_v2") and cfg.get("generation_complete")):
                continue
            if (
                task.status == TaskStatus.MANUAL_REVIEW
                or cfg.get("generation_hold")
                or cfg.get("paid_call_delivery_uncertain")
                or cfg.get("paid_call_manual_resume_required")
                or cfg.get("public_domain_quality_blocked")
                or str(cfg.get("active_run_id") or "").strip()
            ):
                continue

            output_dir = _task_output_dir(task.id)
            aigc_required = bool(cfg.get("aigc_required"))
            if aigc_required:
                artifact = output_dir / "05_aigc_final.md"
                if str(cfg.get("aigc_status") or "") != "passed":
                    continue
            else:
                artifact = output_dir / "04_final.md"
                if _read_nonempty_artifact(artifact) is None:
                    artifact = output_dir / "04_format.md"
            raw = _read_nonempty_artifact(artifact)
            if raw is None:
                continue

            category = str(cfg.get("category") or "public_domain_rewrite")
            normalized = normalize_final_markdown(raw, category=category)
            if detect_final_hard_errors(normalized, category=category):
                continue
            expected = _public_artifact_expected_chapters(task, output_dir, cfg)
            if expected == -1:
                continue
            text, title, chapters, errors = _strict_manuscript_parts(
                normalized, expected_chapters=expected, minimum_chapter_chars=20
            )
            if errors or not chapters:
                continue
            report = _load_json_artifact(output_dir / "quality_report.json")
            if report and (
                report.get("status") in {"needs_repair", "needs_review", "failed"}
                or report.get("passed") is False
            ):
                continue
            if aigc_required:
                aigc_report = _load_json_artifact(output_dir / "aigc_quality_report.json")
                if aigc_report and aigc_report.get("passed") is False:
                    continue
            try:
                _persist_recovered_manuscript(
                    db,
                    task,
                    markdown=text,
                    title=title,
                    summary=_front_section_payload(text, ("最终简介", "三个备选简介")),
                    chapters=chapters,
                    output_dir=output_dir,
                    source_artifact=artifact.name,
                    recovery_kind="public_domain_completed_artifact",
                )
                cfg = dict(task.config or {})
                cfg.update(
                    generation_complete=True,
                    public_domain_artifact_recovered=True,
                    public_domain_artifact_recovered_at=datetime.now(timezone.utc).isoformat(),
                )
                task.config = cfg
                db.commit()
                clear_task_dispatch_guard(str(task.id))
                recovered += 1
            except Exception:
                db.rollback()
                logger.exception("failed to promote public-domain artifact for task %s", task.id)
    except Exception:
        db.rollback()
        logger.exception("public-domain completed-artifact recovery failed")
    finally:
        db.close()
    return recovered


# ---------------------------------------------------------------------------
# Completed-manuscript integrity recovery
# ---------------------------------------------------------------------------

_FINANCE_INTEGRITY_CATEGORIES = frozenset(
    {
        "finance_technology",
        "finance_technology_ai_optimized",
        "finance_technology_recommendation",
    }
)
_INTEGRITY_RECOVERY_VERSION = 1
_INTEGRITY_RECOVERY_MAX_ATTEMPTS = 3
_FINANCE_STAGE_FILES = ("01_writer.md", "02_polish.md", "03_refine.md", "04_format.md")


def _is_finance_integrity_task(task: Task) -> bool:
    config = dict(getattr(task, "config", None) or {})
    raw_category = str(config.get("category") or "").strip()
    if not raw_category:
        return False
    try:
        category = normalize_category(raw_category)
    except Exception:
        category = raw_category
    return category in _FINANCE_INTEGRITY_CATEGORIES


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _numbers_are_contiguous(numbers: list[int]) -> bool:
    return bool(numbers) and numbers == list(range(1, len(numbers) + 1))


def _stage_numbers(markdown: str | None) -> list[int]:
    if not markdown:
        return []
    try:
        return [number for number, _title in _finance_chapter_titles(markdown)]
    except Exception:
        logger.warning("failed to parse finance stage chapters", exc_info=True)
        return []


def _finance_integrity_decision(
    task: Task,
    output_dir: Path,
    *,
    db_chapters: list[Chapter] | None = None,
) -> dict[str, Any]:
    """Inspect one completed finance task without mutating it.

    The returned dictionary intentionally contains the parsed final text and
    parts for the caller, but the public audit helper strips those fields.  A
    queue decision is made only when a complete, contiguous upstream
    checkpoint exists.  Ambiguous data is quarantined for manual review rather
    than replaying a paid request from scratch.
    """

    config = dict(getattr(task, "config", None) or {})
    result: dict[str, Any] = {
        "action": "skip",
        "reason": "",
        "stage_numbers": {},
        "source_stage": None,
        "final_source": None,
        "final_text": None,
        "final_parts": [],
        "expected_numbers": [],
        "db_mismatch": False,
    }

    if not _is_finance_integrity_task(task):
        result["reason"] = "not a finance integrity category"
        return result
    if (
        bool(config.get("public_domain_longform_v2"))
        or bool(config.get("aigc_required"))
        or str(config.get("aigc_status") or "") in {"manual_review", "technical_failed"}
        or config.get("aigc_passed") is False
        or str(config.get("category") or "") == "aigc_review"
        or any(
            marker
            in (
                str(config.get("error_message") or "")
                + " "
                + str(getattr(task, "error_message", "") or "")
            ).lower()
            for marker in ("delivery_uncertain", "duplicate billing", "paid_call")
        )
        or str(config.get("integrity_recovery_state") or "") == "manual_review"
        or bool(config.get("generation_hold"))
        or bool(config.get("paid_call_delivery_uncertain"))
        or bool(config.get("paid_call_manual_resume_required"))
        or str(config.get("active_run_id") or "").strip()
    ):
        result["reason"] = "protected/manual/other pipeline"
        return result

    stages: dict[str, str | None] = {}
    for filename in _FINANCE_STAGE_FILES:
        stages[filename] = _read_nonempty_artifact(output_dir / filename)
        result["stage_numbers"][filename] = _stage_numbers(stages[filename])

    writer = stages["01_writer.md"]
    writer_numbers = result["stage_numbers"]["01_writer.md"]
    writer_catalog = _finance_catalog_titles(writer or "") if writer else []
    catalog_numbers = [number for number, _title in writer_catalog]

    # A writer checkpoint is usable only when its own sequence is complete.
    # If the checkpoint disagrees with its catalog, there is no safe source for
    # a deterministic continuation, so do not start a paid replay.
    writer_invalid_reason = ""
    if writer and not writer_numbers:
        writer_invalid_reason = "01_writer.md has no recognizable chapter sequence"
    elif writer and not _numbers_are_contiguous(writer_numbers):
        writer_invalid_reason = (
            f"01_writer.md chapter sequence is not contiguous: {writer_numbers[:16]}"
        )
    elif catalog_numbers and writer_numbers != catalog_numbers:
        writer_invalid_reason = (
            "01_writer.md does not match its catalog: "
            f"writer={writer_numbers[:16]} catalog={catalog_numbers[:16]}"
        )

    baseline_numbers: list[int] = []
    if not writer_invalid_reason and writer_numbers:
        baseline_numbers = list(writer_numbers)
        result["source_stage"] = "01_writer.md"
    elif not writer:
        # A later stage can still be a durable source if the writer file was
        # lost during an older deployment.  It is safer than starting from
        # raw input, and generate_full_book already knows how to resume from
        # polish/refine checkpoints.
        for filename in ("02_polish.md", "03_refine.md"):
            numbers = result["stage_numbers"][filename]
            if numbers and _numbers_are_contiguous(numbers):
                baseline_numbers = list(numbers)
                result["source_stage"] = filename
                break

    final_raw = stages["04_format.md"]
    if final_raw is not None:
        result["final_source"] = "04_format.md"
    else:
        config_markdown = str(config.get("markdown") or "").strip()
        if config_markdown and not (
            len(config_markdown) < 2_000
            and any(
                marker.lower() in config_markdown.lower()
                for marker in (
                    "待修复终稿",
                    "无法在不新增内容的前提下",
                    "请补充缺失部分",
                    "provider returned an empty body",
                )
            )
        ):
            final_raw = config_markdown
            result["final_source"] = "config.markdown"

    category = str(config.get("category") or "")
    final_text = normalize_final_markdown(final_raw, category=category) if final_raw else None
    result["final_text"] = final_text
    final_parts = _finance_chapter_parts(final_text) if final_text else []
    result["final_parts"] = final_parts
    final_numbers = [number for number, _title, _content in final_parts]
    result["expected_numbers"] = list(baseline_numbers or final_numbers)

    reasons: list[str] = []
    if writer_invalid_reason:
        # Do not let an internally inconsistent writer checkpoint create an
        # endless recovery loop.  A valid final can still be left alone; an
        # invalid final requires an operator decision.
        if not final_parts or not _numbers_are_contiguous(final_numbers):
            result["action"] = "manual"
            result["reason"] = writer_invalid_reason
            return result

    if final_raw is None:
        reasons.append("04_format.md is missing or empty")
    elif not final_parts:
        reasons.append("04_format.md has no strict chapter sequence")
    elif not _numbers_are_contiguous(final_numbers):
        reasons.append(f"final chapter sequence is not contiguous: {final_numbers[:16]}")

    if baseline_numbers and final_numbers != baseline_numbers:
        reasons.append(
            "final chapter sequence differs from durable checkpoint: "
            f"checkpoint={baseline_numbers[:16]}/{len(baseline_numbers)} "
            f"final={final_numbers[:16]}/{len(final_numbers)}"
        )

    target_chapters = _safe_int(config.get("target_chapters"))
    if target_chapters > 0 and final_numbers and not baseline_numbers:
        if len(final_numbers) != target_chapters:
            reasons.append(
                f"final chapter count {len(final_numbers)} does not match target {target_chapters}"
            )

    short_parts = [
        number
        for number, _title, content in final_parts
        if len(re.sub(r"\s+", "", content or "")) < 20
    ]
    if final_parts and short_parts:
        reasons.append(f"final chapters are empty/truncated: {short_parts[:12]}")

    if reasons:
        if baseline_numbers:
            result["action"] = "queue"
            result["reason"] = "; ".join(reasons)
        else:
            result["action"] = "manual"
            result["reason"] = "; ".join(reasons) + "; no complete checkpoint is available"
        return result

    if not final_parts:
        result["action"] = "manual"
        result["reason"] = "no valid final manuscript and no complete checkpoint is available"
        return result

    if db_chapters is not None:
        expected = [(number, title, content) for number, title, content in final_parts]
        if len(db_chapters) != len(expected):
            result["db_mismatch"] = True
        else:
            for row, right in zip(db_chapters, expected):
                left = (
                    int(row.chapter_num),
                    str(row.title or "").strip(),
                    str(row.content or ""),
                )
                if (
                    left[0] != right[0]
                    or left[1] != right[1]
                    or left[2].strip() != right[2].strip()
                    or int(getattr(row, "word_count", 0) or 0) != len(right[2])
                ):
                    result["db_mismatch"] = True
                    break
        if result["db_mismatch"]:
            result["action"] = "db_repair"
            result["reason"] = "final artifact is complete but database chapters differ"
            return result

    result["action"] = "none"
    result["reason"] = "final artifact and database are structurally complete"
    return result


def _integrity_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_integrity_json(output_dir: Path, name: str, payload: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / name
    temp = output_dir / f".{name}.{_integrity_timestamp()}.tmp"
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(target)


def _backup_integrity_final(output_dir: Path) -> str | None:
    source = output_dir / "04_format.md"
    if not source.exists() or source.stat().st_size <= 0:
        return None
    destination = output_dir / f"04_format_pre_integrity_recovery_{_integrity_timestamp()}.md"
    suffix = 1
    while destination.exists():
        destination = output_dir / (
            f"04_format_pre_integrity_recovery_{_integrity_timestamp()}_{suffix}.md"
        )
        suffix += 1
    shutil.copy2(source, destination)
    return destination.name


def _replace_finance_chapters(
    db: Session,
    task: Task,
    parts: list[tuple[int, str, str]],
) -> None:
    db.query(Chapter).filter(Chapter.task_id == task.id).delete(synchronize_session=False)
    for number, title, content in parts:
        db.add(
            Chapter(
                task_id=task.id,
                chapter_num=int(number),
                title=str(title)[:512],
                content=str(content),
                word_count=len(str(content)),
            )
        )
    db.flush()


def _apply_finance_db_repair(
    db: Session,
    task: Task,
    decision: dict[str, Any],
    output_dir: Path,
) -> None:
    final_text = str(decision["final_text"] or "").strip()
    parts = list(decision["final_parts"] or [])
    if not final_text or not parts:
        raise ValueError("cannot repair database without a validated final artifact")

    final_path = output_dir / "04_format.md"
    existing_final = _read_nonempty_artifact(final_path)
    if existing_final != final_text:
        if existing_final:
            backup = output_dir / f"04_format_pre_integrity_db_repair_{_integrity_timestamp()}.md"
            suffix = 1
            while backup.exists():
                backup = output_dir / (
                    f"04_format_pre_integrity_db_repair_{_integrity_timestamp()}_{suffix}.md"
                )
                suffix += 1
            shutil.copy2(final_path, backup)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = final_path.with_suffix(final_path.suffix + ".integrity.tmp")
        temp_path.write_text(final_text.rstrip() + "\n", encoding="utf-8")
        temp_path.replace(final_path)

    _replace_finance_chapters(db, task, parts)
    now = datetime.now(timezone.utc).isoformat()
    cfg = dict(task.config or {})
    cfg.update(
        integrity_recovery_version=_INTEGRITY_RECOVERY_VERSION,
        integrity_recovery_state="resolved",
        integrity_recovery_kind="database_repair",
        integrity_recovery_at=now,
        integrity_recovery_reason=str(decision.get("reason") or "")[:4000],
        integrity_recovery_source_stage=str(decision.get("final_source") or "04_format.md"),
        markdown=final_text,
        markdown_chars=len(final_text),
        actual_chapters=len(parts),
        actual_length=len(re.sub(r"\s+", "", "\n".join(item[2] for item in parts))),
    )
    task.config = cfg
    task.status = TaskStatus.COMPLETED
    task.progress = 100
    task.current_step = f"完整性校验通过：{len(parts)}章（数据库已同步）"[:255]
    task.error_message = None
    _write_integrity_json(
        output_dir,
        "integrity_recovery_report.json",
        {
            "version": _INTEGRITY_RECOVERY_VERSION,
            "action": "database_repair",
            "task_id": str(task.id),
            "chapter_count": len(parts),
            "reason": decision.get("reason"),
            "recovered_at": now,
        },
    )


def _mark_finance_integrity_manual(
    db: Session,
    task: Task,
    decision: dict[str, Any],
    output_dir: Path,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    cfg = dict(task.config or {})
    attempts = int(cfg.get("integrity_recovery_attempts") or 0) + 1
    cfg.update(
        integrity_recovery_version=_INTEGRITY_RECOVERY_VERSION,
        integrity_recovery_state="manual_review",
        integrity_recovery_attempts=attempts,
        integrity_recovery_reason=str(decision.get("reason") or "")[:4000],
        integrity_recovery_at=now,
    )
    task.config = cfg
    task.status = TaskStatus.MANUAL_REVIEW
    task.progress = max(1, min(99, int(task.progress or 0)))
    task.current_step = "完整性校验发现无法安全恢复，已转人工审核"[:255]
    task.error_message = str(decision.get("reason") or "完整性校验失败")[:2000]
    _write_integrity_json(
        output_dir,
        "integrity_recovery_report.json",
        {
            "version": _INTEGRITY_RECOVERY_VERSION,
            "action": "manual_review",
            "task_id": str(task.id),
            "reason": decision.get("reason"),
            "attempts": attempts,
            "recovered_at": now,
        },
    )


def _queue_finance_integrity_recovery(
    db: Session,
    task: Task,
    decision: dict[str, Any],
    output_dir: Path,
) -> bool:
    cfg = dict(task.config or {})
    attempts = int(cfg.get("integrity_recovery_attempts") or 0) + 1
    if attempts > _INTEGRITY_RECOVERY_MAX_ATTEMPTS:
        _mark_finance_integrity_manual(db, task, decision, output_dir)
        return False

    backup_name = _backup_integrity_final(output_dir)
    now = datetime.now(timezone.utc).isoformat()
    source_stage = str(decision.get("source_stage") or "01_writer.md")
    progress = {"01_writer.md": 50, "02_polish.md": 72, "03_refine.md": 87}.get(
        source_stage, 50
    )
    cfg.update(
        integrity_recovery_version=_INTEGRITY_RECOVERY_VERSION,
        integrity_recovery_state="queued",
        integrity_recovery_attempts=attempts,
        integrity_recovery_source_stage=source_stage,
        integrity_recovery_reason=str(decision.get("reason") or "")[:4000],
        integrity_recovery_queued_at=now,
        integrity_recovery_backup=backup_name,
        auto_requeued_at=now,
        auto_requeue_reason="completed finance manuscript integrity recovery",
    )
    for key in (
        "active_run_id",
        "active_run_started_at",
        "active_run_heartbeat_at",
        "auto_retry_not_before",
        "auto_retry_scheduled_by",
        "last_auto_retry_error",
        "paid_api_backoff_until",
    ):
        cfg.pop(key, None)
    task.config = cfg
    task.status = TaskStatus.PENDING
    task.progress = progress
    task.current_step = f"完整性恢复排队：从{source_stage}继续"[:255]
    task.error_message = str(decision.get("reason") or "完整性校验失败")[:2000]
    _write_integrity_json(
        output_dir,
        "integrity_recovery_report.json",
        {
            "version": _INTEGRITY_RECOVERY_VERSION,
            "action": "queued_from_checkpoint",
            "task_id": str(task.id),
            "source_stage": source_stage,
            "reason": decision.get("reason"),
            "attempts": attempts,
            "backup": backup_name,
            "queued_at": now,
        },
    )
    clear_task_dispatch_guard(str(task.id))
    return True


def audit_completed_finance_integrity_tasks(*, limit: int = 500) -> list[dict[str, Any]]:
    """Return a read-only audit of completed finance manuscripts."""

    if not settings.STALE_TASK_WATCHDOG_ENABLED:
        return []
    db: Session = SessionLocal()
    findings: list[dict[str, Any]] = []
    try:
        rows = (
            db.execute(
                select(Task)
                .where(Task.status == TaskStatus.COMPLETED)
                .order_by(Task.created_at)
                .limit(max(1, limit))
            )
            .scalars()
            .all()
        )
        for task in rows:
            if not _is_finance_integrity_task(task):
                continue
            chapters = (
                db.execute(
                    select(Chapter)
                    .where(Chapter.task_id == task.id)
                    .order_by(Chapter.chapter_num)
                )
                .scalars()
                .all()
            )
            try:
                decision = _finance_integrity_decision(
                    task,
                    _task_output_dir(task.id),
                    db_chapters=chapters,
                )
            except Exception:
                logger.exception("finance integrity audit failed for task %s", task.id)
                continue
            if decision.get("action") not in {"none", "skip"}:
                findings.append(
                    {
                        "id": str(task.id),
                        "status": task.status,
                        "category": (task.config or {}).get("category"),
                        "action": decision.get("action"),
                        "reason": decision.get("reason"),
                        "source_stage": decision.get("source_stage"),
                        "final_source": decision.get("final_source"),
                        "stage_numbers": decision.get("stage_numbers"),
                        "expected_numbers": decision.get("expected_numbers"),
                        "db_mismatch": decision.get("db_mismatch"),
                    }
                )
    finally:
        db.close()
    return findings


def recover_completed_integrity_tasks(*, limit: int = 500) -> int:
    """Repair or requeue completed finance tasks with incomplete artifacts.

    This pass never calls an LLM.  It either synchronizes deterministic
    database rows, queues a task from an intact checkpoint, or moves ambiguous
    data to manual review.  Because queued tasks leave the ``completed`` set,
    the marker and attempt cap also make the watchdog idempotent.
    """

    if not settings.STALE_TASK_WATCHDOG_ENABLED:
        return 0
    db: Session = SessionLocal()
    changed = 0
    task_ids_to_dispatch: list[str] = []
    try:
        rows = (
            db.execute(
                select(Task)
                .where(Task.status == TaskStatus.COMPLETED)
                .with_for_update(skip_locked=True)
                .order_by(Task.created_at)
                .limit(max(1, limit))
            )
            .scalars()
            .all()
        )
        for task in rows:
            if not _is_finance_integrity_task(task):
                continue
            output_dir = _task_output_dir(task.id)
            chapters = (
                db.execute(
                    select(Chapter)
                    .where(Chapter.task_id == task.id)
                    .order_by(Chapter.chapter_num)
                )
                .scalars()
                .all()
            )
            try:
                decision = _finance_integrity_decision(task, output_dir, db_chapters=chapters)
            except Exception:
                db.rollback()
                logger.exception("finance integrity inspection failed for task %s", task.id)
                continue
            action = str(decision.get("action") or "skip")
            if action in {"skip", "none"}:
                continue
            try:
                if action == "db_repair":
                    _apply_finance_db_repair(db, task, decision, output_dir)
                    db.commit()
                    clear_task_dispatch_guard(str(task.id))
                    changed += 1
                elif action == "queue":
                    queued = _queue_finance_integrity_recovery(
                        db, task, decision, output_dir
                    )
                    db.commit()
                    if queued:
                        task_ids_to_dispatch.append(str(task.id))
                    changed += 1
                elif action == "manual":
                    _mark_finance_integrity_manual(db, task, decision, output_dir)
                    db.commit()
                    clear_task_dispatch_guard(str(task.id))
                    changed += 1
            except Exception:
                db.rollback()
                logger.exception("finance integrity recovery failed for task %s", task.id)
    except Exception:
        db.rollback()
        logger.exception("completed finance integrity recovery scan failed")
    finally:
        db.close()

    if task_ids_to_dispatch:
        task_module = _task_module()
        published = task_module._dispatch_task_ids(  # noqa: SLF001
            task_ids_to_dispatch,
            label="completed finance integrity recovery",
            force=True,
        )
        logger.info(
            "completed finance integrity recovery changed=%d selected=%d published=%d",
            changed,
            len(task_ids_to_dispatch),
            published,
        )
    return changed
