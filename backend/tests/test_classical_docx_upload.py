from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from app.utils.docx_text import DocxTextError, extract_docx_text


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


def test_extract_docx_text_reads_paragraphs() -> None:
    text = extract_docx_text(_docx_bytes(["子曰：学而时习之。", "有朋自远方来。"]))

    assert text == "子曰：学而时习之。\n有朋自远方来。"


def test_extract_docx_text_rejects_invalid_file() -> None:
    with pytest.raises(DocxTextError) as exc_info:
        extract_docx_text(b"not a docx")

    assert exc_info.value.status_code == 400
