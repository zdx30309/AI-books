"""通用长篇体检：一条命令看清每个项目卡在哪、下一步该点什么。

用法（仓库根目录）:
  python scripts/general_longform_doctor.py                 # 列出全部通用长篇任务
  python scripts/general_longform_doctor.py --task <id>     # 只看一个
  python scripts/general_longform_doctor.py --all-longform  # 连其它长篇分类一起看
  python scripts/general_longform_doctor.py --json          # 机器可读

只读数据库和本地状态文件，不发任何模型调用、不改变任何任务。
指引文案与工作区里的「下一步」面板完全一致。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for path in (str(REPO_ROOT / "backend"), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models import Task  # noqa: E402
from app.services.general_longform import load_state, work_dir  # noqa: E402
from app.services.general_longform_guidance import build_guidance  # noqa: E402

PHASES = {
    "needs_outline": "准备策划",
    "awaiting_materials": "等待材料",
    "awaiting_outline_confirmation": "等待确认大纲",
    "ready_for_chapter": "等待下一章操作",
    "writing_chapter": "当前章写作中",
    "ready_for_assembly": "等待全书终审",
    "needs_review": "有待处理项",
    "paused": "断点已保存",
    "completed": "全书已验收",
}

HOLD_KEYS = ("generation_hold", "paid_call_delivery_uncertain", "paid_call_manual_resume_required", "pause_requested")


def _ts(value) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _status(task: Task) -> str:
    return str(getattr(task.status, "value", task.status) or "")


def advice(task: Task, cfg: dict, state: dict | None, *, stale_seconds: int) -> tuple[str, str, dict]:
    """返回 (状态判断, 下一步该做什么, 完整指引)。

    直接复用 backend 的 next-step 指引模块，所以命令行和工作区写的永远是同一句话。
    只读状态文件，不发模型调用。
    """
    result = build_guidance(state, task_status=_status(task), config=cfg, stale_seconds=stale_seconds)
    primary = result.get("primary")
    if primary:
        todo = primary["label"] + "：" + primary["reason"]
        if primary.get("cost"):
            todo += "（" + primary["cost"] + "）"
        if not primary.get("enabled") and primary.get("disabled_reason"):
            todo += "；当前不可用：" + primary["disabled_reason"]
    else:
        todo = "；".join(result.get("notes") or []) or result.get("detail") or ""
    return result.get("headline", ""), todo, result


def collect(*, task_id: str | None, all_longform: bool, stale_seconds: int) -> list[dict]:
    categories = {"general_longform"}
    if all_longform:
        categories |= {"long_novel", "public_domain_rewrite", "epic_novel"}
    rows: list[dict] = []
    with SessionLocal() as db:
        query = db.query(Task)
        if task_id:
            query = query.filter(Task.id == task_id)
        for task in query.order_by(Task.created_at.desc()).all():
            cfg = dict(task.config or {})
            if str(cfg.get("category") or "") not in categories:
                continue
            try:
                state = load_state(work_dir(task.id))
            except (OSError, ValueError):
                state = None
            judge, todo, plan = advice(task, cfg, state, stale_seconds=stale_seconds)
            rows.append(
                {
                    "id": str(task.id),
                    "stall_kind": plan.get("stall_kind"),
                    "next_action": (plan.get("primary") or {}).get("action"),
                    "next_notes": list(plan.get("notes") or []),
                    "category": cfg.get("category"),
                    "title": str(cfg.get("title") or "")[:60],
                    "source": str(task.source_url or "")[:80],
                    "status": _status(task),
                    "phase": cfg.get("general_longform_phase"),
                    "phase_label": PHASES.get(str(cfg.get("general_longform_phase") or ""), ""),
                    "progress": task.progress,
                    "current_step": task.current_step,
                    "accepted_chapters": cfg.get("general_longform_accepted_chapters"),
                    "outline_version": cfg.get("general_longform_outline_version"),
                    "last_action": (cfg.get("general_longform_request") or {}).get("action"),
                    "holds": [key for key in HOLD_KEYS if cfg.get(key)],
                    "error": str(task.error_message or "")[:300],
                    "judge": judge,
                    "todo": todo,
                    "created_at": task.created_at.isoformat() if task.created_at else "",
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="通用长篇体检（只读）")
    parser.add_argument("--task", default=None, help="只看某个任务 id")
    parser.add_argument("--all-longform", action="store_true", help="连其它长篇分类一起看")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    stale_seconds = max(60, int(getattr(settings, "STALE_PROCESSING_REQUEUE_SECONDS", 900) or 900))
    rows = collect(task_id=args.task, all_longform=args.all_longform, stale_seconds=stale_seconds)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("没有找到通用长篇任务。")
        return 0
    waiting = [item for item in rows if item["status"] not in ("completed", "processing", "queued", "pending")]
    print(f"共 {len(rows)} 个长篇任务：{len(waiting)} 个在等你操作，"
          f"{len([i for i in rows if i['status'] == 'processing'])} 个正在跑，"
          f"{len([i for i in rows if i['status'] == 'completed'])} 个已完成。")
    print(f"（看门狗阈值：处理中心跳超过 {stale_seconds // 60} 分钟自动重排）")
    for item in rows:
        print("=" * 78)
        print(f"[{item['judge']}] {item['id']}")
        print(f"  标题: {item['title'] or '（还没定书名）'}   来源: {item['source']}")
        print(f"  状态: {item['status']} | 阶段: {item['phase']}（{item['phase_label']}） | 进度: {item['progress']}%")
        print(f"  已验收章节: {item['accepted_chapters']} | 大纲版本: v{item['outline_version']} | 最近动作: {item['last_action']}")
        print(f"  当前步骤: {item['current_step']}")
        if item["holds"]:
            print(f"  ⚠ 人工闸门: {', '.join(item['holds'])}")
        if item["error"]:
            print(f"  错误: {item['error']}")
        print(f"  下一步: {item['todo']}")
        for note in item.get("next_notes") or []:
            print(f"    · {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
