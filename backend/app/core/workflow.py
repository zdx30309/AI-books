from __future__ import annotations

import json
from pathlib import Path

from app.services.content_engine import generate_blueprint, generate_full_book


async def run_full_workflow(
    raw_text: str,
    output_dir: Path,
    *,
    category: str = "social_science",
) -> Path:
    blueprint = await generate_blueprint(raw_text, category)
    output_dir.mkdir(parents=True, exist_ok=True)

    blueprint_path = output_dir / "blueprint.json"
    blueprint_path.write_text(json.dumps(blueprint, ensure_ascii=False, indent=2), encoding="utf-8")

    markdown = await generate_full_book(blueprint, category, output_dir=output_dir)
    book_path = output_dir / "book.md"
    book_path.write_text(markdown, encoding="utf-8")
    return book_path
