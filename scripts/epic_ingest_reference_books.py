"""Batch-ingest downloaded Epic reference books into PatternCards."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.session import SessionLocal, init_db  # noqa: E402
from app.models import ReferenceBook, ReferenceMechanismProfile, ReferencePatternCard  # noqa: E402
from app.services.epic_reference import build_reference_mechanism_profile, ingest_reference_book  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="Maximum books to ingest; 0 means all missing books.")
    parser.add_argument("--reingest-indexed", action="store_true", help="Also process books that already have PatternCards, adding only missing cards.")
    parser.add_argument("--skip-at-least-cards", type=int, default=0, help="Skip books that already have at least this many cards.")
    parser.add_argument("--exclude-title", action="append", default=[], help="Skip exact title; can be repeated.")
    parser.add_argument("--rebuild-profile", action="store_true", default=True, help="Build a ReferenceMechanismProfile after ingest.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    init_db()
    failures: list[dict[str, str]] = []
    attempted = 0
    generated = 0
    skipped = 0
    index_db = SessionLocal()
    try:
        book_rows = [(book.id, book.title) for book in index_db.query(ReferenceBook).order_by(ReferenceBook.tier, ReferenceBook.title).all()]
    finally:
        index_db.close()

    excluded_titles = set(args.exclude_title or [])
    for book_id, title in book_rows:
        if title in excluded_titles:
            skipped += 1
            continue
        db = SessionLocal()
        try:
            existing = db.query(ReferencePatternCard).filter(ReferencePatternCard.source_book_id == book_id).count()
            if args.skip_at_least_cards and existing >= args.skip_at_least_cards:
                skipped += 1
                continue
            if existing and not args.reingest_indexed:
                skipped += 1
                continue
            if args.limit and attempted >= args.limit:
                break
            attempted += 1
            result = ingest_reference_book(db, book_id=book_id)
            generated += int(result.get("generated_cards") or 0)
            print(
                f"ingested {attempted}: {title} cards={result.get('generated_cards')} sections={result.get('sections')}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001 - batch job should continue and report every bad file.
            db.rollback()
            failures.append({"title": title, "error": str(exc)[:500]})
            print(f"failed {title}: {exc}", flush=True)
            continue
        finally:
            db.close()

    summary_db = SessionLocal()
    try:
        profile_id = None
        if args.rebuild_profile:
            profile = build_reference_mechanism_profile(summary_db)
            profile_id = str(profile.id)
        summary = {
            "attempted": attempted,
            "skipped": skipped,
            "generated": generated,
            "failures": failures,
            "cards": summary_db.query(ReferencePatternCard).count(),
            "profiles": summary_db.query(ReferenceMechanismProfile).count(),
            "profileId": profile_id,
        }
        print("summary", json.dumps(summary, ensure_ascii=False), flush=True)
        return 0 if not failures else 1
    finally:
        summary_db.close()


if __name__ == "__main__":
    raise SystemExit(main())
