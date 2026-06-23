"""Small .docx text extraction helper."""
from __future__ import annotations

from dataclasses import dataclass
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO

DOCX_XML_FILES = ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml")
WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class DocxTextError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class DocxParagraph:
    text: str
    style_id: str = ""
    style_name: str = ""
    is_heading: bool = False
    is_bold: bool = False


def _open_docx_archive(content: bytes) -> zipfile.ZipFile:
    if not content:
        raise DocxTextError("Word 文件为空")
    try:
        return zipfile.ZipFile(BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise DocxTextError("请上传 .docx 格式的 Word 文件") from exc


def _word_val(element: ET.Element | None, name: str = "val") -> str:
    if element is None:
        return ""
    return str(element.attrib.get(f"{WORD_NS}{name}") or element.attrib.get(name) or "").strip()


def _bold_enabled(element: ET.Element | None) -> bool:
    if element is None:
        return False
    value = _word_val(element).lower()
    return value not in {"0", "false", "off", "none"}


def _style_maps(archive: zipfile.ZipFile) -> tuple[dict[str, str], set[str]]:
    try:
        xml_bytes = archive.read("word/styles.xml")
    except KeyError:
        return {}, set()
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return {}, set()

    names: dict[str, str] = {}
    bold_style_ids: set[str] = set()
    for style in root.iter(f"{WORD_NS}style"):
        style_id = _word_val(style, "styleId")
        if not style_id:
            continue
        name = _word_val(style.find(f"{WORD_NS}name"))
        if name:
            names[style_id] = name
        rpr = style.find(f"{WORD_NS}rPr")
        if rpr is not None and _bold_enabled(rpr.find(f"{WORD_NS}b")):
            bold_style_ids.add(style_id)
    return names, bold_style_ids


def _paragraph_text_and_bold(paragraph: ET.Element, *, style_bold: bool) -> tuple[str, bool]:
    parts: list[str] = []
    text_run_bold: list[bool] = []
    for run in paragraph.iter(f"{WORD_NS}r"):
        run_parts: list[str] = []
        for node in run.iter():
            if node.tag == f"{WORD_NS}t" and node.text:
                run_parts.append(node.text)
            elif node.tag == f"{WORD_NS}tab":
                run_parts.append("\t")
            elif node.tag == f"{WORD_NS}br":
                run_parts.append("\n")
        run_text = "".join(run_parts)
        if not run_text:
            continue
        parts.append(run_text)
        rpr = run.find(f"{WORD_NS}rPr")
        text_run_bold.append(style_bold or (rpr is not None and _bold_enabled(rpr.find(f"{WORD_NS}b"))))
    text = "".join(parts).strip()
    is_bold = bool(text_run_bold) and all(text_run_bold)
    return text, is_bold


def extract_docx_paragraphs(content: bytes) -> list[DocxParagraph]:
    archive = _open_docx_archive(content)
    paragraphs: list[DocxParagraph] = []
    with archive:
        style_names, bold_style_ids = _style_maps(archive)
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
                ppr = paragraph.find(f"{WORD_NS}pPr")
                pstyle = ppr.find(f"{WORD_NS}pStyle") if ppr is not None else None
                style_id = _word_val(pstyle)
                style_name = style_names.get(style_id, "")
                outline = ppr.find(f"{WORD_NS}outlineLvl") if ppr is not None else None
                is_heading = bool(
                    outline is not None
                    or style_id.lower().startswith("heading")
                    or style_name.lower().startswith("heading")
                    or "标题" in style_name
                )
                text, is_bold = _paragraph_text_and_bold(
                    paragraph,
                    style_bold=style_id in bold_style_ids,
                )
                if text:
                    paragraphs.append(
                        DocxParagraph(
                            text=text,
                            style_id=style_id,
                            style_name=style_name,
                            is_heading=is_heading,
                            is_bold=is_bold,
                        )
                    )
    if not paragraphs:
        raise DocxTextError("未能从 Word 文件中读取到正文")
    return paragraphs


def extract_docx_text(content: bytes) -> str:
    paragraphs = extract_docx_paragraphs(content)
    return "\n".join(item.text for item in paragraphs).strip()


def extract_classical_docx_text(content: bytes) -> str:
    paragraphs = extract_docx_paragraphs(content)
    lines: list[str] = []
    for item in paragraphs:
        text = item.text.strip()
        if not text:
            continue
        if item.is_bold and not (text.startswith("**") and text.endswith("**")):
            text = f"**{text}**"
        lines.append(text)
    result = "\n".join(lines).strip()
    if not result:
        raise DocxTextError("未能从 Word 文件中读取到正文")
    return result
