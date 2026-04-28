import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.content_engine import generate_blueprint, generate_full_book


async def main():
    anxiety_point = "你选一个真实议题"  # 比如 "为什么年轻人越来越不愿意生孩子"
    raw_text_path = ROOT / "测试用书.txt"
    raw_text = raw_text_path.read_text(encoding="utf-8")

    blueprint = await generate_blueprint(anxiety_point, raw_text)

    output_dir = ROOT / "output" / "baseline_2026-04-28"
    output_dir.mkdir(parents=True, exist_ok=True)
    book = await generate_full_book(
        anxiety_point,
        blueprint,
        output_dir=output_dir,  # ← 关键，传这个参数才会自动度量
    )

    # 把书也存下来
    (output_dir / "book.md").write_text(book, encoding="utf-8")
    print(f"完成。报告在 {output_dir}/quality_report.txt")


asyncio.run(main())
