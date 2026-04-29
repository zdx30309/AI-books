from __future__ import annotations

import json
from pathlib import Path

from app.services.content_engine import generate_full_book


async def run_full_workflow(
    raw_text: str,
    output_dir: Path,
    *,
    category: str = "social_science",
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    markdown = await generate_full_book(raw_text, category, output_dir=output_dir)
    book_path = output_dir / "book.md"
    book_path.write_text(markdown, encoding="utf-8")
    return book_path
