from __future__ import annotations

from pathlib import Path
from uuid import uuid4
import zipfile

from app.api.routers import tasks as task_router
from app.models import Task, TaskStatus
from app.utils.docx_text import extract_docx_text


def test_completed_public_domain_task_downloads_valid_docx(
    monkeypatch,
    tmp_path: Path,
) -> None:
    task = Task(
        id=uuid4(),
        source_url="manual://public-domain-rewrite",
        status=TaskStatus.COMPLETED,
        config={
            "title": "公版融合测试稿",
            "category": "public_domain_rewrite",
            "public_domain_rewrite": True,
            "markdown": "@最终书名\n公版融合测试稿\n@正文\n第一章\n这是应当完整进入 Word 的正文。",
        },
    )
    monkeypatch.setattr(task_router, "_task_output_dir", lambda _task_id: tmp_path)

    response = task_router._build_single_download_response(  # noqa: SLF001
        None,  # Markdown is already stored in task.config, so no DB query is needed.
        task,
        "docx",
    )

    path = Path(response.path)
    assert path == tmp_path / "downloads" / "final.docx"
    assert path.exists()
    assert zipfile.is_zipfile(path)
    assert "这是应当完整进入 Word 的正文" in extract_docx_text(path.read_bytes())


def test_independent_category_markdown_download_uses_generic_normalizer() -> None:
    result = task_router._normalize_download_markdown(  # noqa: SLF001
        "@最终书名\n公版稿\n\n@正文\n\n正文内容。",
        category="public_domain_rewrite",
    )

    assert "@最终书名" in result
    assert "正文内容。" in result
