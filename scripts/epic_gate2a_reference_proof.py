"""Run the bounded Gate 2A Reference Intelligence proof batch."""
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
from app.services.epic_storage import epic_run_artifact_path  # noqa: E402
from app.services.epic_reference import run_gate2a_reference_intelligence_proof  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book-limit", type=int, default=1, help="Proof book count.")
    parser.add_argument("--section-limit", type=int, default=50, help="Proof section count across selected books.")
    parser.add_argument("--disable-llm-extraction", action="store_true", help="Do not call the configured reference model.")
    parser.add_argument("--force", action="store_true", help="Re-run cached section analyses.")
    parser.add_argument("--no-profile", action="store_true", help="Skip rebuilding the production-ready profile.")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON report path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    init_db()
    db = SessionLocal()
    try:
        report = run_gate2a_reference_intelligence_proof(
            db,
            book_limit=args.book_limit,
            section_limit=args.section_limit,
            use_llm_extraction=not args.disable_llm_extraction,
            force=args.force,
            rebuild_profile=not args.no_profile,
        )
    finally:
        db.close()

    output = args.output
    if output is None:
        out_dir = epic_run_artifact_path()
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_started = str(report.get("startedAt", "gate2a")).replace(":", "").replace("+", "_").replace(".", "_")
        output = out_dir / f"gate2a_reference_intelligence_{safe_started}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"verdict": report.get("verdict"), "output": str(output), "metrics": report}, ensure_ascii=False), flush=True)
    return 0 if report.get("verdict") in {"PASS", "PARTIAL", "UNKNOWN_NEEDS_EVIDENCE"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
