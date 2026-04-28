from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.services.content_engine import generate_blueprint, generate_full_book


@dataclass
class ReviewDecision:
    approved: bool = False
    feedback: str | None = None
    cancelled: bool = False


def print_blueprint_summary(blueprint: dict) -> None:
    print("\n" + "=" * 70)
    print(f"【主选书名】{blueprint.get('title', '?')}")
    print("\n【三个备选包装方向】")
    for i, t in enumerate(blueprint.get("title_candidates", []), 1):
        print(f"  方向 {chr(64+i)}: {t}")

    print(f"\n【主选简介】（300 字内）")
    print(blueprint.get("intro", ""))

    print(f"\n【导言】（200 字）")
    print(blueprint.get("preface", ""))

    print(f"\n【全书宪法（global_context）】")
    print(blueprint.get("global_context", ""))

    chapters = blueprint.get("chapters", [])
    total_target = blueprint.get("total_target_length", "?")
    print(f"\n【章节结构】共 {len(chapters)} 章，目标总字数 ≈ {total_target}")
    for ch in chapters:
        target_len = ch.get("target_length", "?")
        layer = ch.get("chapter_layer", "?")
        print(f"\n  第{ch.get('chapter_num', '?')}章: {ch.get('chapter_title', '?')}")
        print(f"    层级: {layer} | 目标字数: {target_len}")
        print(f"    目标: {ch.get('chapter_goal', '?')[:120]}...")
        outline = ch.get("chapter_outline", "")
        if outline:
            preview = outline[:300].replace("\n", " ")
            print(f"    写作指南预览: {preview}...")
    print("=" * 70)


async def step1_generate_blueprint(
    anxiety_point: str,
    raw_text: str,
    output_dir: Path,
    *,
    category: str = "social_science",
    feedback: str | None = None,
) -> Path:
    """生成蓝图，保存为 JSON，返回文件路径。"""
    if feedback:
        augmented_raw = f"{raw_text}\n\n【上次蓝图的修改意见】\n{feedback}"
    else:
        augmented_raw = raw_text

    print("\n=== 蓝图阶段开始 ===")
    blueprint = await generate_blueprint(anxiety_point, augmented_raw, category)
    print("=== 蓝图阶段完成 ===")

    output_dir.mkdir(parents=True, exist_ok=True)
    blueprint_path = output_dir / "blueprint.json"
    blueprint_path.write_text(
        json.dumps(blueprint, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return blueprint_path


def step2_review_blueprint(blueprint_path: Path) -> ReviewDecision:
    """交互式审查蓝图。返回用户的决定。"""
    blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
    print_blueprint_summary(blueprint)

    print("\n=== 蓝图审查 ===")
    print("[1] 通过，开始生成全书")
    print("[2] 重做蓝图（输入修改意见）")
    print("[3] 取消")

    while True:
        choice = input(">>> ").strip()
        if choice == "1":
            return ReviewDecision(approved=True)
        elif choice == "2":
            feedback = input("请输入修改意见（一行）: ").strip()
            return ReviewDecision(approved=False, feedback=feedback)
        elif choice == "3":
            return ReviewDecision(approved=False, cancelled=True)
        else:
            print("无效输入，请输入 1/2/3")


async def step3_generate_book(
    anxiety_point: str,
    blueprint_path: Path,
    output_dir: Path,
    *,
    category: str = "social_science",
) -> Path:
    blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    print("\n=== 正文阶段开始 ===")
    markdown = await generate_full_book(anxiety_point, blueprint, category, output_dir=output_dir)
    print("=== 正文阶段完成 ===")

    book_path = output_dir / "book.md"
    book_path.write_text(markdown, encoding="utf-8")
    return book_path


async def run_full_workflow(
    anxiety_point: str,
    raw_text: str,
    output_dir: Path,
    *,
    category: str = "social_science",
) -> Path | None:
    """完整流程：蓝图 → 审查 → 重做循环 → 全书。"""
    feedback = None
    max_blueprint_rounds = 5

    for round_num in range(max_blueprint_rounds):
        blueprint_path = await step1_generate_blueprint(
            anxiety_point,
            raw_text,
            output_dir,
            category=category,
            feedback=feedback,
        )

        decision = step2_review_blueprint(blueprint_path)

        if decision.cancelled:
            print("已取消。")
            return None
        if decision.approved:
            break

        feedback = decision.feedback
        print(f"\n收到修改意见：{feedback}\n开始重做蓝图...")
    else:
        print(f"超过 {max_blueprint_rounds} 轮重做，自动退出。")
        return None

    print("\n=== 蓝图通过，开始生成全书 ===")
    book_path = await step3_generate_book(
        anxiety_point,
        blueprint_path,
        output_dir,
        category=category,
    )
    report_path = output_dir / "quality_report.txt"
    if report_path.exists():
        print(f"质量体检报告: {report_path}")
    print(f"\n✓ 完成: {book_path}")
    return book_path
