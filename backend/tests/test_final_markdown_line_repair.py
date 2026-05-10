from __future__ import annotations

from app.services.content_engine import normalize_final_markdown


def test_normalize_final_markdown_joins_comma_broken_body_line() -> None:
    markdown = """@最终书名
测试书
@正文
@1. 测试章节
小说有社会痛点，
人物传记等。
下一段是完整句子。
"""

    result = normalize_final_markdown(markdown)

    assert "小说有社会痛点，人物传记等。" in result
    assert "小说有社会痛点，\n人物传记等。" not in result
    assert "下一段是完整句子。" in result


def test_normalize_final_markdown_softens_bad_period_before_fragment() -> None:
    markdown = """@正文
@1. 测试章节
小说有社会痛点。
人物传记等。
"""

    result = normalize_final_markdown(markdown)

    assert "小说有社会痛点，人物传记等。" in result
    assert "小说有社会痛点。\n人物传记等。" not in result


def test_normalize_final_markdown_preserves_chapter_boundaries() -> None:
    markdown = """@正文
@1. 第一章
这是第一段。
@2. 第二章
人物传记等。
"""

    result = normalize_final_markdown(markdown)

    assert "这是第一段。\n@2. 第二章\n人物传记等。" in result
