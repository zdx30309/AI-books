"""Download and import Epic Novel reference books through a local SoNovel WebUI."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.session import SessionLocal, init_db  # noqa: E402
from app.models import ReferenceBooklistItem, ReferenceDownloadStatus, ReferenceDownloadTask  # noqa: E402
from app.services.epic_reference import (  # noqa: E402
    SoNovelAdapter,
    download_reference_task,
    import_downloaded_references,
    normalize_booklist,
    search_reference_download_task,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of booklist items to process; 0 means all.")
    parser.add_argument("--tier", type=int, action="append", choices=[1, 2, 3, 4], help="Restrict processing to one or more tiers.")
    parser.add_argument("--search-only", action="store_true", help="Only search and persist SoNovel candidates.")
    parser.add_argument("--skip-existing", action="store_true", default=True, help="Skip tasks whose local file already exists.")
    parser.add_argument("--fallback-results", type=int, default=3, help="Try this many candidate sources when downloading.")
    parser.add_argument("--sleep", type=float, default=0.5, help="Delay between books, in seconds.")
    parser.add_argument("--no-import", action="store_true", help="Do not import downloaded files into the reference corpus.")
    return parser.parse_args()


def ensure_task(db, item: ReferenceBooklistItem) -> ReferenceDownloadTask:
    task = (
        db.query(ReferenceDownloadTask)
        .filter(ReferenceDownloadTask.booklist_item_id == item.id)
        .order_by(ReferenceDownloadTask.created_at.desc())
        .first()
    )
    if task is not None:
        return task
    task = ReferenceDownloadTask(
        booklist_item_id=item.id,
        provider="sonovel",
        title=item.title,
        author=item.author,
        query=f"{item.title} {item.author or ''}".strip(),
        status=ReferenceDownloadStatus.PENDING,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def existing_download(task: ReferenceDownloadTask, item: ReferenceBooklistItem) -> str | None:
    for candidate in (task.local_download_path, task.imported_raw_path, item.local_raw_path):
        if candidate and Path(candidate).exists() and item.title in Path(candidate).stem:
            return candidate
    return None


def unique_candidates(task: ReferenceDownloadTask, item: ReferenceBooklistItem, limit: int) -> list[dict]:
    adapter = SoNovelAdapter()
    candidates: list[dict] = []
    seen: set[str] = set()
    ranked = adapter.select_candidates(task.search_results or [], item.title, item.author, limit=limit)
    for candidate in [task.selected_result, *ranked]:
        if not isinstance(candidate, dict):
            continue
        if adapter._score_result(candidate, item.title, item.author) < 80:
            continue
        key = str(candidate.get("url") or candidate)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
        if len(candidates) >= max(1, limit):
            break
    return candidates


def main() -> int:
    args = parse_args()
    init_db()
    db = SessionLocal()
    try:
        if db.query(ReferenceBooklistItem).count() == 0:
            normalize_booklist(db, create_tasks=True)

        query = db.query(ReferenceBooklistItem)
        if args.tier:
            query = query.filter(ReferenceBooklistItem.tier.in_(args.tier))
        items = query.order_by(ReferenceBooklistItem.tier, ReferenceBooklistItem.priority, ReferenceBooklistItem.title).all()
        if args.limit > 0:
            items = items[: args.limit]

        summary = {"searched": 0, "downloaded": 0, "skipped": 0, "failed": 0, "not_found": 0}
        total = len(items)
        for index, item in enumerate(items, start=1):
            task = ensure_task(db, item)
            print(f"[{index}/{total}] tier={item.tier} title={item.title} author={item.author or ''} status={task.status}", flush=True)

            if not task.search_results or task.status in {ReferenceDownloadStatus.PENDING, ReferenceDownloadStatus.NOT_FOUND, ReferenceDownloadStatus.MANUAL_REQUIRED}:
                task = search_reference_download_task(db, task)
                summary["searched"] += 1
                print(f"  search -> {task.status} candidates={len(task.search_results or [])}", flush=True)

            if args.search_only:
                continue

            existing_path = existing_download(task, item)
            if args.skip_existing and existing_path:
                summary["skipped"] += 1
                print(f"  skip existing -> {existing_path}", flush=True)
                continue

            candidates = unique_candidates(task, item, args.fallback_results)
            if not candidates:
                summary["not_found"] += 1
                print("  no candidates", flush=True)
                continue

            downloaded = False
            for candidate_index, candidate in enumerate(candidates, start=1):
                task = download_reference_task(db, task, selected_result=candidate)
                print(f"  download candidate {candidate_index}/{len(candidates)} -> {task.status}", flush=True)
                if task.status == ReferenceDownloadStatus.DOWNLOADED:
                    item.download_status = ReferenceDownloadStatus.DOWNLOADED
                    item.local_raw_path = task.local_download_path
                    db.commit()
                    summary["downloaded"] += 1
                    downloaded = True
                    break
            if not downloaded:
                summary["failed"] += 1
            if args.sleep:
                time.sleep(args.sleep)

        if not args.no_import and not args.search_only:
            imported = import_downloaded_references(db)
            print(f"imported={len(imported)}", flush=True)

        print("summary", summary, flush=True)
        return 0 if summary["failed"] == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
