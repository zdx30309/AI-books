"""Offline MVP test runner for blueprint-to-book generation."""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.services.content_engine import generate_blueprint, generate_full_book


def main() -> None:
    test_anxiety = "AI时代普通人的职业替代焦虑"
    test_raw_text = (
        "最近某大厂宣布裁员30%，全面引入AI辅助编程和运营，引发热议。很多打工人感觉自己成了系统的耗材，"
        "不知道未来该学什么才能不被淘汰。"
    )

    async def _run() -> None:
        print("正在生成蓝图...")
        blueprint = await generate_blueprint(test_anxiety, test_raw_text)

        print("正在生成全书正文...")
        book_markdown = await generate_full_book(test_anxiety, blueprint)

        output_path = Path(__file__).resolve().parent / "mvp_output_book.md"
        output_path.write_text(book_markdown, encoding="utf-8")
        print(f"已保存到: {output_path}")

    asyncio.run(_run())


if __name__ == "__main__":
    main()
