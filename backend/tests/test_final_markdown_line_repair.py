from __future__ import annotations

import re

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


def test_classical_normalize_removes_spacers_spaces_and_half_width_punctuation() -> None:
    markdown = """@最终书名
人间有鬼（白话版）
@正文
@1. 全文
宋定伯 随口 胡诌: "我是刚死的鬼, 所以身子还沉. "

 。
鬼又问: "你打算去哪儿? "
"""

    result = normalize_final_markdown(markdown, category="classical_chinese")
    body = result.split("@正文", 1)[1]
    prose = "\n".join(line for line in body.splitlines() if not re.match(r"^@\d+\.", line))

    assert "\n\n" not in body
    assert "\n。\n" not in body
    assert "宋定伯随口胡诌：“我是刚死的鬼，所以身子还沉。”" in result
    assert "鬼又问：“你打算去哪儿？”" in result
    assert " " not in prose
    for punct in [",", ".", ":", ";", "?", "!", '"']:
        assert punct not in prose
