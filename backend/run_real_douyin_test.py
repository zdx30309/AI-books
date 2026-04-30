"""Real-world end-to-end test runner for Douyin/Kuaishou video -> book generation."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from time import perf_counter

from app.core.workflow import run_douyin_workflow

_URL_RE = re.compile(r"https?://\S+")


def _read_input(prompt: str) -> str:
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("输入不能为空，请重新输入。")


def _extract_url_from_text(text: str) -> str:
    match = _URL_RE.search(text)
    if not match:
        raise ValueError("未检测到有效链接")
    return match.group(0).rstrip("，。；;),]』】'\"")


async def _run() -> None:
    douyin_input = _read_input("请输入抖音/快手视频分享链接 (douyin_url): ")

    try:
        douyin_url = _extract_url_from_text(douyin_input)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    print("解析成功，开始进入统一工作流...")
    output_path = Path(__file__).resolve().parent / "output" / "real_douyin_test"
    workflow_start = perf_counter()
    book_path = await run_douyin_workflow(douyin_url, output_path)
    workflow_cost = perf_counter() - workflow_start
    print(f"工作流耗时: {workflow_cost:.2f} 秒")
    report_path = output_path / "quality_report.txt"
    if report_path.exists():
        print(f"质量体检报告: {report_path}")
    if book_path is not None:
        print(f"\n已保存到: {book_path}")


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        print("\n用户取消执行。")
    except Exception as exc:
        print(f"\n执行失败：{exc}")


if __name__ == "__main__":
    main()
