from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.session import SessionLocal
from app.models import Chapter, Task, TaskStatus
from app.prompts.goal_driven_templates import normalize_category
from app.services.content_engine import normalize_final_markdown

_CHAPTER_HEADING_RE = re.compile(r"(?m)^@\s*(\d+)\.\s*(.+?)\s*$")
_BODY_SECTION_RE = re.compile(r"(?m)^@?\s*正文\s*$")
_SKIP_CATEGORIES = {"comic_series", "healing_comic", "classical_chinese"}


def _task_category(task: Task) -> str:
    raw = str((task.config or {}).get("category") or "").strip()
    if not raw:
        return ""
    try:
        return normalize_category(raw)
    except ValueError:
        return raw


def _render_from_chapters(chapters: list[Chapter]) -> str:
    lines = ["@正文"]
    for chapter in sorted(chapters, key=lambda item: item.chapter_num):
        title = (chapter.title or "").strip() or f"第{chapter.chapter_num}章"
        content = (chapter.content or "").strip()
        lines.extend([f"@{chapter.chapter_num}. {title}", content])
    return "\n".join(line for line in lines if line is not None).strip()


def _source_markdown(task: Task, chapters: list[Chapter]) -> str:
    cfg = task.config or {}
    markdown = str(cfg.get("markdown") or "").strip()
    if markdown:
        return markdown
    return _render_from_chapters(chapters)


def _replace_chapters(db, task: Task, markdown: str) -> int:
    body_match = _BODY_SECTION_RE.search(markdown or "")
    body_text = markdown[body_match.end() :] if body_match else markdown
    matches = list(_CHAPTER_HEADING_RE.finditer(body_text))

    db.query(Chapter).filter(Chapter.task_id == task.id).delete(synchronize_session=False)
    db.flush()

    if not matches:
        db.add(
            Chapter(
                task_id=task.id,
                chapter_num=1,
                title="全文",
                content=markdown.strip(),
                word_count=len(markdown.strip()),
            )
        )
        return 1

    seen: set[int] = set()
    deduped = []
    for match in reversed(matches):
        chapter_num = int(match.group(1))
        if chapter_num in seen:
            continue
        seen.add(chapter_num)
        deduped.append(match)
    matches = list(reversed(deduped))

    for index, match in enumerate(matches):
        chapter_num = int(match.group(1))
        title = match.group(2).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body_text)
        content = body_text[start:end].strip()
        db.add(
            Chapter(
                task_id=task.id,
                chapter_num=chapter_num or index + 1,
                title=title,
                content=content or markdown.strip(),
                word_count=len(content or markdown.strip()),
            )
        )
    return len(matches)


def repair_completed_tasks(*, apply: bool, limit: int | None, task_ids: list[UUID]) -> int:
    repaired = 0
    inspected = 0
    with SessionLocal() as db:
        query = db.query(Task).filter(Task.status == TaskStatus.COMPLETED)
        if task_ids:
            query = query.filter(Task.id.in_(task_ids))
        query = query.order_by(Task.created_at.desc())
        if limit:
            query = query.limit(limit)

        for task in query.all():
            category = _task_category(task)
            if category in _SKIP_CATEGORIES:
                continue
            chapters = (
                db.query(Chapter)
                .filter(Chapter.task_id == task.id)
                .order_by(Chapter.chapter_num.asc())
                .all()
            )
            source = _source_markdown(task, chapters)
            if not source:
                continue
            inspected += 1
            cleaned = normalize_final_markdown(source, category=category)
            if cleaned == source:
                continue
            repaired += 1
            print(f"repair_candidate task={task.id} category={category} chars={len(source)}->{len(cleaned)}")
            if not apply:
                continue

            cfg = dict(task.config or {})
            cfg["markdown"] = cleaned
            cfg["markdown_chars"] = len(cleaned)
            cfg["format_noise_repaired_at"] = datetime.now(timezone.utc).isoformat()
            cfg["format_noise_repair_reason"] = "deterministic_cleanup_no_llm"
            task.config = cfg
            _replace_chapters(db, task, cleaned)

        if apply:
            db.commit()
        else:
            db.rollback()

    print(f"inspected={inspected} repaired={repaired} apply={apply}")
    return repaired


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Repair completed manuscript markdown/chapter format noise without calling LLMs."
    )
    parser.add_argument("--apply", action="store_true", help="Write repaired markdown and chapters to DB.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum completed tasks to inspect.")
    parser.add_argument("--task-id", action="append", default=[], help="Restrict to one task id; may be repeated.")
    args = parser.parse_args()

    task_ids = [UUID(value) for value in args.task_id]
    repair_completed_tasks(apply=bool(args.apply), limit=args.limit, task_ids=task_ids)


if __name__ == "__main__":
    main()
