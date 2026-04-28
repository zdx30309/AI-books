from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.workflow import run_full_workflow


async def main() -> None:
    print("引力奇点 · 工业级长文生成引擎")
    anxiety_point = input("核心焦虑议题: ").strip()
    raw_text_path = input("原始素材文件路径: ").strip()
    raw_text = Path(raw_text_path).read_text(encoding="utf-8")
    output_dir = Path("./output") / anxiety_point[:20]

    await run_full_workflow(anxiety_point, raw_text, output_dir)


if __name__ == "__main__":
    asyncio.run(main())
