from __future__ import annotations

from html import escape
from uuid import uuid4
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from app.api.routers import tasks as task_router
from app.models import TaskStatus
from app.tasks import tasks as task_tasks
from app.utils.docx_text import DocxTextError, extract_classical_docx_text, extract_docx_text


def _docx_bytes(paragraphs: list[str]) -> bytes:
    body = "".join(
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body>"
        "</w:document>"
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def _docx_bytes_with_bold(paragraphs: list[tuple[str, bool]]) -> bytes:
    body = "".join(
        (
            "<w:p><w:r><w:rPr><w:b/></w:rPr>"
            f"<w:t>{escape(text)}</w:t></w:r></w:p>"
            if is_bold
            else f"<w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p>"
        )
        for text, is_bold in paragraphs
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body>"
        "</w:document>"
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def test_extract_docx_text_reads_paragraphs() -> None:
    text = extract_docx_text(_docx_bytes(["子曰：学而时习之。", "有朋自远方来。"]))

    assert text == "子曰：学而时习之。\n有朋自远方来。"


def test_extract_classical_docx_text_preserves_bold_author_line() -> None:
    text = extract_classical_docx_text(
        _docx_bytes_with_bold(
            [
                ("卷一", False),
                ("蒲松龄", True),
                ("家人捉双卫来寻生。", False),
            ]
        )
    )

    assert text == "卷一\n**蒲松龄**\n家人捉双卫来寻生。"


def test_extract_docx_text_does_not_reject_long_classical_text() -> None:
    long_paragraph = "天地玄黄，宇宙洪荒。" * 30_000
    text = extract_docx_text(_docx_bytes([long_paragraph]))

    assert text == long_paragraph
    assert len(text) > 200_000


def test_extract_docx_text_rejects_invalid_file() -> None:
    with pytest.raises(DocxTextError) as exc_info:
        extract_docx_text(b"not a docx")

    assert exc_info.value.status_code == 400


def test_create_and_dispatch_task_returns_string_status(monkeypatch) -> None:
    class FakeDb:
        def add(self, task):
            self.task = task

        def commit(self):
            pass

        def refresh(self, task):
            task.id = uuid4()

    user = type("User", (), {"id": uuid4()})()
    monkeypatch.setattr(task_router, "_dispatch_task", lambda task: None)

    response = task_router._create_and_dispatch_task(
        source_url="manual://classical_chinese_docx/test.docx",
        config={
            "category": "classical_chinese",
            "raw_text": "子曰：学而时习之。",
            "classical_original_title": "论语",
        },
        db=FakeDb(),
        user=user,
    )

    assert response.status == TaskStatus.PENDING
    assert task_router._normalize_task_config(  # noqa: SLF001
        {
            "category": "classical_chinese",
            "title": "论语",
        }
    )["classical_original_title"] == "论语"
    interleaved_cfg = task_router._normalize_task_config(  # noqa: SLF001
        {
            "category": "classical_chinese_interleaved",
            "title": "宋定伯捉鬼",
        }
    )
    assert interleaved_cfg["category"] == "classical_chinese_interleaved"
    assert interleaved_cfg["classical_original_title"] == "宋定伯捉鬼"


def test_create_classical_text_task_externalizes_raw_text(tmp_path, monkeypatch) -> None:
    class FakeDb:
        def add(self, task):
            self.task = task

        def commit(self):
            pass

        def refresh(self, task):
            if not task.id:
                task.id = uuid4()

    raw_text = "子曰：学而时习之。" * 20_000
    db = FakeDb()
    user = type("User", (), {"id": uuid4()})()
    monkeypatch.setattr(task_router, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(task_router, "_dispatch_task", lambda task: None)

    response = task_router._create_classical_text_task(  # noqa: SLF001
        source_url="manual://classical_chinese",
        config={
            "category": "classical_chinese",
            "raw_text": raw_text,
            "classical_original_title": "论语",
        },
        db=db,
        user=user,
    )

    assert response.status == TaskStatus.PENDING
    assert "raw_text" not in db.task.config
    assert db.task.config["raw_text_chars"] == len(raw_text)
    stored_path = tmp_path / db.task.config["source_text_path"]
    assert stored_path.read_text(encoding="utf-8") == raw_text


def test_create_classical_interleaved_text_task_externalizes_raw_text(tmp_path, monkeypatch) -> None:
    class FakeDb:
        def add(self, task):
            self.task = task

        def commit(self):
            pass

        def refresh(self, task):
            if not task.id:
                task.id = uuid4()

    raw_text = "南阳宋定伯，年少时，夜行，逢鬼，问之。" * 1000
    db = FakeDb()
    user = type("User", (), {"id": uuid4()})()
    monkeypatch.setattr(task_router, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(task_router, "_dispatch_task", lambda task: None)

    response = task_router._create_classical_text_task(  # noqa: SLF001
        source_url="manual://classical_chinese_interleaved",
        config={
            "category": "classical_chinese_interleaved",
            "raw_text": raw_text,
            "classical_original_title": "宋定伯捉鬼",
        },
        db=db,
        user=user,
    )

    assert response.status == TaskStatus.PENDING
    assert db.task.config["category"] == "classical_chinese_interleaved"
    assert "raw_text" not in db.task.config
    assert db.task.config["raw_text_chars"] == len(raw_text)
    stored_path = tmp_path / db.task.config["source_text_path"]
    assert stored_path.read_text(encoding="utf-8") == raw_text


def test_source_text_path_can_feed_long_docx_text(tmp_path, monkeypatch) -> None:
    path = tmp_path / "source.txt"
    text = "古文白话测试。" * 50_000
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(task_tasks, "PROJECT_ROOT", tmp_path)

    loaded = task_tasks._source_text_from_config(  # noqa: SLF001
        {"source_text_path": "source.txt"},
        task_label="test",
    )

    assert loaded == text
