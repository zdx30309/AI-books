from __future__ import annotations

from html import unescape
from io import BytesIO
from pathlib import Path
import re
import sys
import zipfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.api.routers.tasks import _markdown_to_docx


def _docx_text(content: bytes) -> str:
    with zipfile.ZipFile(BytesIO(content)) as docx:
        document_xml = docx.read("word/document.xml").decode("utf-8")
    parts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", document_xml)
    return "\n".join(unescape(part) for part in parts)


def _docx_document_xml(content: bytes) -> str:
    with zipfile.ZipFile(BytesIO(content)) as docx:
        return docx.read("word/document.xml").decode("utf-8")


def test_classical_docx_download_removes_intro_and_number_prefixes() -> None:
    markdown = """@最终书名
盐铁论白话
@最终简介
一本文言白话稿。
@目录
1. 本议起处
2、群臣之辩
@导言
这段导言不应进入下载 Word。
它的第二段也不应进入。
@正文
@1. 本议起处
这里是第一章正文。
@2、群臣之辩
这里是第二章正文。
"""

    text = _docx_text(_markdown_to_docx(markdown, category="classical_chinese"))

    assert "导言" not in text
    assert "这段导言不应进入下载 Word" not in text
    assert "1. 本议起处" not in text
    assert "2、群臣之辩" not in text
    assert "@1." not in text
    assert "@2、" not in text
    assert "本议起处" in text
    assert "群臣之辩" in text
    assert "这里是第一章正文" in text
    assert "这里是第二章正文" in text


def test_classical_docx_download_renders_bold_author_line() -> None:
    markdown = """@最终书名
聊斋志异（白话版）
@正文
@1. 全文
**蒲松龄**
这里是正文。
"""

    xml = _docx_document_xml(_markdown_to_docx(markdown, category="classical_chinese"))
    text = _docx_text(_markdown_to_docx(markdown, category="classical_chinese"))

    assert "**" not in text
    assert "蒲松龄" in text
    assert "<w:b/>" in xml


def test_classical_interleaved_docx_download_preserves_original_translation_pairs() -> None:
    markdown = """@最终书名
宋定伯捉鬼（白话版）
@导言
这段导言不进入 Word。
@正文
@1. 全文
【P0001】（原文）南阳宋定伯，年少时，夜行，逢鬼，问之。
（翻译）南阳有个叫宋定伯的人，年轻时夜里赶路，遇见了鬼，便询问它。
【P0002】（原文）定伯诳之，言：“我亦鬼。”
（翻译）宋定伯骗它说：“我也是鬼。”
"""

    text = _docx_text(_markdown_to_docx(markdown, category="classical_chinese_interleaved"))

    assert "这段导言不进入 Word" not in text
    assert "【P0001】" not in text
    assert "【P0002】" not in text
    assert "（原文）南阳宋定伯，年少时，夜行，逢鬼，问之。" in text
    assert "（翻译）南阳有个叫宋定伯的人" in text
    assert "（原文）定伯诳之，言：“我亦鬼。”" in text
    assert "（翻译）宋定伯骗它说：“我也是鬼。”" in text


def test_non_classical_docx_download_keeps_intro_and_number_prefixes() -> None:
    markdown = """@最终书名
测试书名
@目录
1. 第一章
@导言
普通分类导言保留。
@正文
@1. 第一章
正文。
"""

    text = _docx_text(_markdown_to_docx(markdown, category="social_science"))

    assert "导言" in text
    assert "普通分类导言保留" in text
    assert "@1. 第一章" in text
