"""Download Epic Novel reference books from the normalized booklist without a DB connection."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models import ReferenceDownloadStatus  # noqa: E402
from app.services.epic_reference import EpicReferenceError, SoNovelAdapter, parse_booklist_seed  # noqa: E402


BOOKLIST_JSON = REPO_ROOT / ".local" / "novel_ref_corpus" / "booklist.json"
RUN_DIR = REPO_ROOT / ".local" / "epic_novel_runs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of items to process; 0 means all.")
    parser.add_argument("--start", type=int, default=1, help="1-based start index after filtering.")
    parser.add_argument("--tier", type=int, action="append", choices=[1, 2, 3, 4], help="Restrict to one or more tiers.")
    parser.add_argument("--title", action="append", help="Restrict processing to exact book titles; can be repeated.")
    parser.add_argument("--fallback-results", type=int, default=3, help="Try this many exact/high-confidence candidate sources.")
    parser.add_argument("--search-retries", type=int, default=2, help="Retry transient empty/manual SoNovel searches this many times.")
    parser.add_argument("--download-timeout", type=int, default=300, help="Seconds to wait for one SoNovel source download.")
    parser.add_argument("--sleep", type=float, default=0.5, help="Delay between books, in seconds.")
    parser.add_argument("--search-only", action="store_true", help="Search and write manifest without downloading.")
    parser.add_argument("--manifest", type=str, default="", help="Existing manifest to resume/update.")
    return parser.parse_args()


def load_booklist() -> list[dict[str, Any]]:
    if BOOKLIST_JSON.exists():
        data = json.loads(BOOKLIST_JSON.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    rows = parse_booklist_seed()
    return [
        {
            "title": row.title,
            "author": row.author,
            "tier": row.tier_hint or 4,
            "priority": row.tier_hint or 4,
            "category": "",
        }
        for row in rows
    ]


def existing_download(adapter: SoNovelAdapter, title: str) -> str | None:
    for file in adapter.list_downloaded_files():
        path = Path(file["path"])
        if title in path.stem:
            return str(path)
    return None


def load_manifest(path: Path | None) -> dict[str, Any]:
    if path and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "startedAt": datetime.now().isoformat(timespec="seconds"),
        "updatedAt": None,
        "items": [],
        "summary": {},
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    manifest["updatedAt"] = datetime.now().isoformat(timespec="seconds")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def item_key(item: dict[str, Any]) -> str:
    return f"{item.get('title') or ''}::{item.get('author') or ''}"


def summarize(entries: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"total": len(entries), "downloaded": 0, "skipped": 0, "not_found": 0, "manual_required": 0, "failed": 0, "searched": 0}
    for entry in entries:
        status = entry.get("status")
        if status in summary:
            summary[status] += 1
        if entry.get("searched"):
            summary["searched"] += 1
    return summary


def main() -> int:
    args = parse_args()
    adapter = SoNovelAdapter()
    detected = adapter.detect_installation()
    if not detected.get("webUiAvailable"):
        print(f"SoNovel WebUI unavailable: {detected.get('status')}", flush=True)
        return 2

    items = load_booklist()
    if args.tier:
        items = [item for item in items if int(item.get("tier") or 4) in args.tier]
    if args.title:
        wanted = set(args.title)
        items = [item for item in items if str(item.get("title") or "") in wanted]
    items.sort(key=lambda item: (int(item.get("tier") or 4), int(item.get("priority") or 9), str(item.get("title") or "")))
    if args.start > 1:
        items = items[args.start - 1 :]
    if args.limit > 0:
        items = items[: args.limit]

    manifest_path = Path(args.manifest) if args.manifest else RUN_DIR / f"reference_manifest_download_{datetime.now():%Y%m%d_%H%M%S}.json"
    manifest = load_manifest(manifest_path if args.manifest else None)
    by_key = {entry.get("key"): entry for entry in manifest.get("items", []) if entry.get("key")}

    for index, item in enumerate(items, start=args.start):
        title = str(item.get("title") or "").strip()
        author = str(item.get("author") or "").strip() or None
        key = item_key(item)
        entry = by_key.get(key) or {"key": key, "title": title, "author": author, "tier": item.get("tier")}
        by_key[key] = entry
        manifest["items"] = list(by_key.values())
        print(f"[{index}/{args.start + len(items) - 1}] tier={item.get('tier')} title={title} author={author or ''}", flush=True)

        existing = existing_download(adapter, title)
        if existing:
            entry.update({"status": "skipped", "localPath": existing, "error": None})
            print(f"  skip existing -> {existing}", flush=True)
            write_manifest(manifest_path, manifest)
            continue

        result = adapter.search_book(title, author)
        for _attempt in range(max(0, args.search_retries)):
            if result.get("status") != ReferenceDownloadStatus.MANUAL_REQUIRED and result.get("results"):
                break
            time.sleep(max(1.0, args.sleep))
            result = adapter.search_book(title, author)
        candidates = adapter.select_candidates(result.get("results") or [], title, author, limit=args.fallback_results)
        entry.update(
            {
                "searched": True,
                "searchStatus": result.get("status"),
                "candidateCount": len(result.get("results") or []),
                "usableCandidateCount": len(candidates),
                "selectedResult": candidates[0] if candidates else None,
            }
        )
        print(f"  search -> {result.get('status')} candidates={entry['candidateCount']} usable={len(candidates)}", flush=True)
        if args.search_only:
            entry["status"] = "searched"
            write_manifest(manifest_path, manifest)
            continue
        if result.get("status") == ReferenceDownloadStatus.MANUAL_REQUIRED:
            entry.update({"status": "manual_required", "error": result.get("manualCommandSuggestion") or "SoNovel manual intervention required"})
            write_manifest(manifest_path, manifest)
            continue
        if not candidates:
            entry.update({"status": "not_found", "error": "No exact/high-confidence candidate"})
            write_manifest(manifest_path, manifest)
            continue

        downloaded = False
        errors: list[str] = []
        for candidate_index, candidate in enumerate(candidates, start=1):
            try:
                download = adapter.download_result(candidate, timeout=args.download_timeout)
            except EpicReferenceError as exc:
                errors.append(str(exc))
                print(f"  download candidate {candidate_index}/{len(candidates)} -> error", flush=True)
                continue
            print(f"  download candidate {candidate_index}/{len(candidates)} -> {download.get('status')}", flush=True)
            if download.get("status") == ReferenceDownloadStatus.DOWNLOADED:
                entry.update({"status": "downloaded", "localPath": download.get("localPath"), "fileHash": download.get("fileHash"), "error": None})
                downloaded = True
                break
            errors.append(json.dumps(download.get("response"), ensure_ascii=False))
        if not downloaded:
            entry.update({"status": "failed", "error": " | ".join(errors[-3:])})
        write_manifest(manifest_path, manifest)
        if args.sleep:
            time.sleep(args.sleep)

    manifest["items"] = list(by_key.values())
    manifest["summary"] = summarize(manifest["items"])
    write_manifest(manifest_path, manifest)
    print(f"manifest={manifest_path}", flush=True)
    print("summary", manifest["summary"], flush=True)
    return 0 if manifest["summary"].get("failed", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
