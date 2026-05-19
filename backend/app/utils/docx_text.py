"""Small .docx text extraction helper."""
from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO

DOCX_XML_FILES = ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml")
WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class DocxTextError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def extract_docx_text(content: bytes) -> str:
    if not content:
        raise DocxTextError("Word 文件为空")
    try:
        archive = zipfile.ZipFile(BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise DocxTextError("请上传 .docx 格式的 Word 文件") from exc

    paragraphs: list[str] = []
    with archive:
        for filename in DOCX_XML_FILES:
            try:
                xml_bytes = archive.read(filename)
            except KeyError:
                continue
            try:
                root = ET.fromstring(xml_bytes)
            except ET.ParseError:
                continue
            for paragraph in root.iter(f"{WORD_NS}p"):
                parts: list[str] = []
                for node in paragraph.iter():
                    if node.tag == f"{WORD_NS}t" and node.text:
                        parts.append(node.text)
                    elif node.tag == f"{WORD_NS}tab":
                        parts.append("\t")
                    elif node.tag == f"{WORD_NS}br":
                        parts.append("\n")
                line = "".join(parts).strip()
                if line:
                    paragraphs.append(line)

    text = "\n".join(paragraphs).strip()
    if not text:
        raise DocxTextError("未能从 Word 文件中读取到正文")
    return text
