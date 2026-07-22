"""Small, dependency-free EPUB text extractor used by public-domain ingestion."""
from __future__ import annotations

from html.parser import HTMLParser
from io import BytesIO
import posixpath
import xml.etree.ElementTree as ET
import zipfile


MAX_EPUB_UNCOMPRESSED_BYTES = 150 * 1024 * 1024
MAX_EPUB_MEMBERS = 50_000


class EpubTextError(ValueError):
    pass


class _TextParser(HTMLParser):
    _BREAK_TAGS = {
        "p",
        "div",
        "section",
        "article",
        "br",
        "li",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        lowered = tag.lower()
        if lowered in {"script", "style", "svg"}:
            self.ignored_depth += 1
        elif lowered in self._BREAK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "svg"} and self.ignored_depth:
            self.ignored_depth -= 1
        elif lowered in self._BREAK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth and data.strip():
            self.parts.append(data)


def extract_epub_text(content: bytes) -> str:
    if not content:
        raise EpubTextError("EPUB 文件为空")
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > MAX_EPUB_MEMBERS:
                raise EpubTextError("EPUB 内部文件数量异常，已拒绝解析")
            if sum(max(0, item.file_size) for item in members) > MAX_EPUB_UNCOMPRESSED_BYTES:
                raise EpubTextError("EPUB 解压后体积超过安全上限")
            names = archive.namelist()
            ordered = _spine_files(archive) or [
                name
                for name in names
                if name.lower().endswith((".xhtml", ".html", ".htm"))
                and not name.lower().startswith("meta-inf/")
            ]
            parts: list[str] = []
            for name in ordered:
                try:
                    raw = archive.read(name)
                except KeyError:
                    continue
                text = _html_to_text(raw.decode("utf-8", errors="replace"))
                if text:
                    parts.append(text)
    except zipfile.BadZipFile as exc:
        raise EpubTextError("EPUB 文件无法读取，请确认文件未损坏") from exc
    result = "\n\n".join(parts).strip()
    if not result:
        raise EpubTextError("未能从 EPUB 中读取到正文")
    return result


def _spine_files(archive: zipfile.ZipFile) -> list[str]:
    try:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
    except Exception:
        return []
    rootfile = next(
        (
            str(item.attrib.get("full-path") or "").strip()
            for item in container.iter()
            if item.tag.lower().endswith("rootfile")
        ),
        "",
    )
    if not rootfile:
        return []
    try:
        package = ET.fromstring(archive.read(rootfile))
    except Exception:
        return []
    manifest: dict[str, str] = {}
    spine: list[str] = []
    for item in package.iter():
        lowered = item.tag.lower()
        if lowered.endswith("item"):
            item_id = str(item.attrib.get("id") or "").strip()
            href = str(item.attrib.get("href") or "").strip()
            media_type = str(item.attrib.get("media-type") or "").lower()
            if item_id and href and ("html" in media_type or href.lower().endswith((".xhtml", ".html", ".htm"))):
                manifest[item_id] = href
        elif lowered.endswith("itemref"):
            item_id = str(item.attrib.get("idref") or "").strip()
            if item_id:
                spine.append(item_id)
    base = posixpath.dirname(rootfile)
    names = set(archive.namelist())
    result: list[str] = []
    for item_id in spine:
        href = manifest.get(item_id)
        if not href:
            continue
        path = posixpath.normpath(posixpath.join(base, href.split("#", 1)[0]))
        if path in names:
            result.append(path)
    return result


def _html_to_text(value: str) -> str:
    parser = _TextParser()
    parser.feed(value)
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line).strip()


__all__ = ["EpubTextError", "extract_epub_text"]
