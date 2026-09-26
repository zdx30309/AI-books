from __future__ import annotations

import asyncio
from html import escape
from uuid import uuid4
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import UploadFile
import pytest

from app.api.routers import tasks as task_router
from app.models import TaskStatus
from app.services import classical_reference_templates as ref_templates
from app.tasks import tasks as task_tasks
from app.utils.docx_text import (
    DocxTextError,
    extract_classical_document_text,
    extract_classical_docx_text,
    extract_docx_text,
    extract_pdf_text,
)


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


def _pdf_bytes(text: str) -> bytes:
    safe_text = (
        text.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .encode("latin-1", errors="replace")
        .decode("latin-1")
    )
    stream = f"BT /F1 12 Tf 72 720 Td ({safe_text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(data))
        data.extend(f"{idx} 0 obj\n".encode("ascii"))
        data.extend(obj)
        data.extend(b"\nendobj\n")
    xref_pos = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    data.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    data.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(data)


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


def test_extract_pdf_text_reads_selectable_text() -> None:
    text = extract_pdf_text(_pdf_bytes("Yang Jia Jiang first chapter"))

    assert "Yang Jia Jiang first chapter" in text


def test_extract_classical_document_text_routes_pdf() -> None:
    text = extract_classical_document_text(
        _pdf_bytes("Yang Jia Jiang classical source"),
        "yangjiajiang.pdf",
    )

    assert "Yang Jia Jiang classical source" in text


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


def test_create_classical_chinese_from_pdf_upload_externalizes_raw_text(tmp_path, monkeypatch) -> None:
    class FakeDb:
        def add(self, task):
            self.task = task

        def commit(self):
            pass

        def refresh(self, task):
            if not task.id:
                task.id = uuid4()

        def query(self, *_args):
            return self

        def filter(self, *_args):
            return self

        def first(self):
            return self.task

    db = FakeDb()
    user = type("User", (), {"id": uuid4()})()
    monkeypatch.setattr(task_router, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(task_router, "_ensure_generation_capacity", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(task_router, "_dispatch_task", lambda task: None)

    response = asyncio.run(
        task_router.create_classical_chinese_from_docx(
            file=UploadFile(file=BytesIO(_pdf_bytes("Yang Jia Jiang first chapter")), filename="yangjiajiang.pdf"),
            classical_variant="novel_style",
            category="classical_chinese_novel_style",
            title="杨家将",
            style_reference="",
            style_notes="",
            reference_file=None,
            db=db,
            user=user,
        )
    )

    assert response.status == TaskStatus.PENDING
    assert db.task.config["category"] == "classical_chinese_novel_style"
    assert db.task.config["source_filename"] == "yangjiajiang.pdf"
    assert db.task.config["input_source"] == "document_upload"
    stored_path = tmp_path / db.task.config["source_text_path"]
    assert "Yang Jia Jiang first chapter" in stored_path.read_text(encoding="utf-8")


def test_create_reference_rewrite_from_pdf_and_reference_pdf(tmp_path, monkeypatch) -> None:
    class FakeDb:
        def add(self, task):
            self.task = task

        def commit(self):
            pass

        def refresh(self, task):
            if not task.id:
                task.id = uuid4()

        def query(self, *_args):
            return self

        def filter(self, *_args):
            return self

        def first(self):
            return self.task

    db = FakeDb()
    user = type("User", (), {"id": uuid4()})()
    monkeypatch.setattr(task_router, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(task_router, "_ensure_generation_capacity", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(task_router, "_dispatch_task", lambda task: None)

    response = asyncio.run(
        task_router.create_classical_chinese_from_docx(
            file=UploadFile(file=BytesIO(_pdf_bytes("Original classical source")), filename="source.pdf"),
            reference_file=UploadFile(file=BytesIO(_pdf_bytes("Authoritative translation reference")), filename="reference.pdf"),
            classical_variant="reference_translation",
            category="classical_chinese_reference_rewrite",
            title="参照译写测试",
            style_reference="",
            style_notes="",
            db=db,
            user=user,
        )
    )

    assert response.status == TaskStatus.PENDING
    assert db.task.config["category"] == "classical_chinese_reference_rewrite"
    assert db.task.config["style_reference_filename"] == "reference.pdf"
    assert "【参考译文文件：reference.pdf】" in db.task.config["style_notes"]
    assert "Authoritative translation reference" in db.task.config["style_notes"]
    stored_path = tmp_path / db.task.config["source_text_path"]
    assert "Original classical source" in stored_path.read_text(encoding="utf-8")


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


def test_create_classical_reference_rewrite_task_externalizes_long_style_notes(tmp_path, monkeypatch) -> None:
    class FakeDb:
        def add(self, task):
            self.task = task

        def commit(self):
            pass

        def refresh(self, task):
            if not task.id:
                task.id = uuid4()

    raw_text = "子曰：学而时习之，不亦说乎。"
    style_notes = "参考译文：孔子说，学习以后常常温习。\n" * 500
    db = FakeDb()
    user = type("User", (), {"id": uuid4()})()
    monkeypatch.setattr(task_router, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(task_router, "_dispatch_task", lambda task: None)

    response = task_router._create_classical_text_task(  # noqa: SLF001
        source_url="manual://classical_chinese_reference_rewrite",
        config={
            "category": "classical_chinese_reference_rewrite",
            "raw_text": raw_text,
            "style_reference": "某知名译本",
            "style_notes": style_notes,
            "classical_original_title": "论语",
        },
        db=db,
        user=user,
    )

    assert response.status == TaskStatus.PENDING
    assert "raw_text" not in db.task.config
    assert len(db.task.config["style_notes"]) == task_router.STYLE_NOTES_INLINE_LIMIT
    assert db.task.config["style_notes_chars"] == len(style_notes.strip())
    style_path = tmp_path / db.task.config["style_notes_path"]
    assert style_path.read_text(encoding="utf-8") == style_notes.strip()


def test_default_classical_reference_template_can_attach_to_config(tmp_path, monkeypatch) -> None:
    reference_dir = tmp_path / "backend" / "output" / "reference_templates" / "classical_reference_rewrite"
    reference_dir.mkdir(parents=True)
    text_path = reference_dir / "default_reference.txt"
    meta_path = reference_dir / "default_reference.json"
    text_path.write_text("原文：栖守道德者，寂寞一时。\n译文：守住道德的人，也许会一时寂寞。", encoding="utf-8")
    meta_path.write_text(
        '{"name":"菜根谭-中华书局参照范本","source_filename":"菜根谭-中华书局.docx"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(ref_templates, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(ref_templates, "DEFAULT_REFERENCE_TEXT_PATH", text_path)
    monkeypatch.setattr(ref_templates, "DEFAULT_REFERENCE_META_PATH", meta_path)

    cfg = task_router._apply_default_classical_reference_template(  # noqa: SLF001
        {"category": "classical_chinese_reference_rewrite"}
    )

    assert cfg["default_style_reference_enabled"] is True
    assert cfg["style_reference"] == "菜根谭-中华书局参照范本"
    assert cfg["style_notes_path"].endswith("default_reference.txt")
    assert "固定参照译本字词校准范本" in cfg["style_notes"]
    assert "联网检索专业网站交叉验证" in cfg["style_notes"]
    assert "不得望文生义" in cfg["style_notes"]


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
    # Uploads are persisted under backend/output/task_inputs; reads outside backend/ are refused.
    path = tmp_path / "backend" / "output" / "task_inputs" / "source.txt"
    path.parent.mkdir(parents=True)
    text = "古文白话测试。" * 50_000
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(task_tasks, "PROJECT_ROOT", tmp_path)

    loaded = task_tasks._source_text_from_config(  # noqa: SLF001
        {"source_text_path": "backend/output/task_inputs/source.txt"},
        task_label="test",
    )

    assert loaded == text


def test_style_notes_path_can_feed_long_reference_translation(tmp_path, monkeypatch) -> None:
    path = tmp_path / "backend" / "output" / "task_inputs" / "style_reference.txt"
    path.parent.mkdir(parents=True)
    text = "参考译文：孔子说，学习以后常常温习。\n" * 500
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(task_tasks, "PROJECT_ROOT", tmp_path)

    loaded = task_tasks._style_notes_from_config(  # noqa: SLF001
        {"style_notes_path": "backend/output/task_inputs/style_reference.txt", "style_notes": "短预览"},
        task_label="test",
    )

    assert loaded == text.strip()


def test_default_reference_template_feeds_reference_rewrite_worker(tmp_path, monkeypatch) -> None:
    reference_dir = tmp_path / "backend" / "output" / "reference_templates" / "classical_reference_rewrite"
    reference_dir.mkdir(parents=True)
    text_path = reference_dir / "default_reference.txt"
    meta_path = reference_dir / "default_reference.json"
    text_path.write_text("原文：栖守道德者，寂寞一时。\n译文：守住道德的人，也许会一时寂寞。", encoding="utf-8")
    meta_path.write_text('{"name":"菜根谭-中华书局参照范本"}', encoding="utf-8")
    monkeypatch.setattr(ref_templates, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(ref_templates, "DEFAULT_REFERENCE_TEXT_PATH", text_path)
    monkeypatch.setattr(ref_templates, "DEFAULT_REFERENCE_META_PATH", meta_path)
    monkeypatch.setattr(task_tasks, "PROJECT_ROOT", tmp_path)

    loaded = task_tasks._style_notes_from_config(  # noqa: SLF001
        {"category": "classical_chinese_reference_rewrite"},
        task_label="test",
    )

    assert "固定参照译本字词校准范本" in loaded
    assert "联网检索专业网站交叉验证" in loaded
    assert "不得望文生义" in loaded
    assert "原文：栖守道德者" in loaded
    assert "译文：守住道德的人" in loaded
