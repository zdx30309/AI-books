"""Stage matched SoNovel downloads into the local Epic reference corpus tree."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.epic_storage import reference_raw_path, tier_folder  # noqa: E402


BOOKLIST_JSON = REPO_ROOT / ".local" / "novel_ref_corpus" / "booklist.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", help="Path to reference_manifest_download_*.json")
    parser.add_argument("--dry-run", action="store_true", help="Print planned copies without writing files.")
    return parser.parse_args()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def safe_filename(path: Path, title: str, author: str | None) -> str:
    suffix = path.suffix or ".epub"
    if title in path.stem:
        return path.name
    author_part = f"({author})" if author else ""
    return f"{title}{author_part}{suffix}"


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest)
    manifest = load_json(manifest_path)
    booklist = load_json(BOOKLIST_JSON) if BOOKLIST_JSON.exists() else []
    by_title = {str(item.get("title")): item for item in booklist if item.get("title")}

    copied = 0
    skipped = 0
    missing = 0
    for entry in manifest.get("items", []):
        if entry.get("status") not in {"downloaded", "skipped"}:
            continue
        source_value = entry.get("localPath")
        if not source_value:
            missing += 1
            continue
        source = Path(source_value)
        title = str(entry.get("title") or "").strip()
        book_meta = by_title.get(title, {})
        tier = int(book_meta.get("tier") or entry.get("tier") or 4)
        if not source.exists():
            missing += 1
            continue
        dest_dir = reference_raw_path() / tier_folder(tier)
        dest = dest_dir / safe_filename(source, title, entry.get("author"))
        print(f"{source} -> {dest}")
        if args.dry_run:
            skipped += 1
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.stat().st_size == source.stat().st_size:
            skipped += 1
            continue
        shutil.copy2(source, dest)
        copied += 1

    print(json.dumps({"copied": copied, "skipped": skipped, "missing": missing}, ensure_ascii=False))
    return 0 if missing == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
