"""运行 Gate 2B 参考智能分阶段扩展。"""
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
from app.services.epic_reference import run_gate2b_reference_staged_expansion  # noqa: E402
from app.services.epic_storage import epic_run_artifact_path  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book-limit", type=int, default=3, help="最多选择多少本已导入参考书。")
    parser.add_argument("--section-limit", type=int, default=200, help="全局候选 section 上限。")
    parser.add_argument("--sections-per-book", type=int, default=0, help="每本书最多处理多少个 section；0 表示不单独限制。")
    parser.add_argument("--tier", type=int, default=None, help="只处理指定 tier。")
    parser.add_argument("--category", default=None, help="只处理指定 category。")
    parser.add_argument("--no-resume", action="store_true", help="不跳过已成功 section。")
    parser.add_argument("--retry-failed-only", action="store_true", help="只重试 fallback/error section。")
    parser.add_argument("--force", action="store_true", help="强制重跑缓存 section。")
    parser.add_argument("--dry-run", action="store_true", help="只估算，不写入 DB，不调用模型。")
    parser.add_argument("--disable-llm-extraction", action="store_true", help="禁用 LLM 抽取，只走 fallback；不能作为生产 PASS。")
    parser.add_argument("--max-cost", type=float, default=None, help="本次运行估算成本上限。")
    parser.add_argument("--stop-on-cost-limit", action="store_true", help="预计超过 max-cost 时安全停止。")
    parser.add_argument("--max-runtime-seconds", type=int, default=0, help="本次运行秒级时间上限；0 表示不限制。")
    parser.add_argument("--no-profile", action="store_true", help="不重建生产级机制画像。")
    parser.add_argument("--skip-full-estimate", action="store_true", help="跳过全导入语料 section 估算。")
    parser.add_argument("--output", type=Path, default=None, help="可选 JSON 报告输出路径。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    init_db()
    db = SessionLocal()
    try:
        report = run_gate2b_reference_staged_expansion(
            db,
            book_limit=args.book_limit,
            section_limit=args.section_limit,
            sections_per_book=args.sections_per_book,
            tier=args.tier,
            category=args.category,
            resume=not args.no_resume,
            retry_failed_only=args.retry_failed_only,
            force=args.force,
            dry_run=args.dry_run,
            max_cost=args.max_cost,
            stop_on_cost_limit=args.stop_on_cost_limit,
            max_runtime_seconds=args.max_runtime_seconds,
            rebuild_profile=not args.no_profile,
            use_llm_extraction=not args.disable_llm_extraction,
            include_full_estimate=not args.skip_full_estimate,
        )
    finally:
        db.close()

    output = args.output
    if output is None:
        out_dir = epic_run_artifact_path()
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_started = str(report.get("startedAt", "gate2b")).replace(":", "").replace("+", "_").replace(".", "_")
        output = out_dir / f"gate2b_reference_staged_{safe_started}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print(
        json.dumps(
            {
                "结论": report.get("verdict"),
                "输出文件": str(output),
                "已处理书数": report.get("processedBookCount"),
                "已处理章节片段": report.get("processedSections"),
                "累计成功分析": report.get("cumulativeSuccessfulAnalyses"),
                "累计生产级卡片": report.get("cumulativeProductionReadyCards"),
                "停止原因": report.get("stopReason"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if report.get("verdict") in {"PASS", "PARTIAL", "UNKNOWN_NEEDS_EVIDENCE"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
