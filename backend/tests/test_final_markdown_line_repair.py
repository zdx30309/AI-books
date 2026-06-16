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


def test_normalize_final_markdown_removes_intro_copied_at_body_start() -> None:
    intro = "很多人第一次听到这个问题时，会以为答案很简单。真正麻烦的是，生活里的选择往往没有标准答案。"
    markdown = f"""@最终书名
测试书
@最终简介
测试简介
@目录
1. 测试章节
@导言
{intro}
@正文
@1. 测试章节
{intro}
第一章应该从新的案例继续推进。
"""

    result = normalize_final_markdown(markdown, category="social_science")
    body = result.split("@正文", 1)[1]

    assert result.count(intro) == 1
    assert intro not in body
    assert "第一章应该从新的案例继续推进。" in body


def test_normalize_final_markdown_keeps_body_when_duplicate_is_only_content() -> None:
    intro = "这段导言如果也是唯一正文，后处理不能把正文删空。"
    markdown = f"""@导言
{intro}
@正文
@1. 测试章节
{intro}
"""

    result = normalize_final_markdown(markdown, category="social_science")
    body = result.split("@正文", 1)[1]

    assert intro in body


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


def test_normalize_removes_bold_prefix_pseudo_heading_from_body() -> None:
    markdown = """@正文
@1. 健康观察
**年龄的真相**30岁后的状态下滑，常被大家简单理解成衰老。
这是**重要**的一点，不应该留下 Markdown 标记。
"""

    result = normalize_final_markdown(markdown, category="health_wellness")

    assert "**" not in result
    assert "年龄的真相" not in result
    assert "30岁后的状态下滑" in result
    assert "这是重要的一点" in result
    assert "@1. 健康观察" in result


def test_normalize_removes_repeated_naked_pseudo_heading_without_gluing_text() -> None:
    markdown = """@正文
@1. 财富底气
财富自由的三种诉求
财富自由背后藏着三种完全不同的诉求。第一种是消费上的底气。
健康四件事
每天记录四个关键点，起床后、吃午饭前、下午四点和睡前。
"""

    result = normalize_final_markdown(markdown, category="social_science")

    assert "财富自由的三种诉求财富自由背后" not in result
    assert "财富自由的三种诉求\n" not in result
    assert "财富自由背后藏着三种完全不同的诉求" in result
    assert "健康四件事。\n每天记录四个关键点" in result


def test_normalize_removes_illegal_at_editorial_comment_and_heading_noise() -> None:
    markdown = """@正文
@1. 焦虑出口
@这几句话逻辑自洽，让普通人积压多年的焦虑，仿佛瞬间找到了一个可以量化、可以解决的出口。
# 身体的能量账本
1. 起床之后
起床之后先记录昨晚睡了几个小时。
@普通段落应该只去掉非法 at 符号。
"""

    result = normalize_final_markdown(markdown, category="popular_science")

    assert "这几句话逻辑自洽" not in result
    assert "#" not in result
    assert "\n1. 起床之后\n" not in result
    assert "身体的能量账本。" in result
    assert "起床之后。\n起床之后先记录" not in result
    assert "起床之后先记录昨晚睡了几个小时" in result
    assert "普通段落应该只去掉非法 at 符号" in result
    assert "@普通段落" not in result


def test_comic_normalize_keeps_image_markdown_unchanged() -> None:
    markdown = """@正文
@1. 第一辑
![配图](comic_images/comic_section_01.png)
短语：今晚月亮放下了手里的活。
"""

    result = normalize_final_markdown(markdown, category="comic_series")

    assert "![配图](comic_images/comic_section_01.png)" in result


def test_masterpiece_interpretation_does_not_use_local_colloquial_rewrite() -> None:
    markdown = """@最终书名
咱们说白了这事儿
@最终简介
咱们说白了这事儿。
@目录
1. 咱们说白了这事儿
@导言
说白了，这事儿挺复杂的，对吧。
原句说“这事儿”。
@正文
@1. 咱们说白了这事儿
你看，咱们一点儿也不能把那味儿写成唠嗑。
这事儿是不是已经说清楚。
"""

    result = normalize_final_markdown(markdown, category="masterpiece_interpretation")

    assert "@最终书名\n咱们说白了这事儿" in result
    assert "@最终简介\n咱们说白了这事儿。" in result
    assert "@1. 咱们说白了这事儿" in result
    assert "说白了，这事儿挺复杂的，对吧。" in result
    assert "原句说“这事儿”。" in result
    assert "咱们一点儿也不能把那味儿写成唠嗑。" in result
    assert "这事儿是不是已经说清楚。" in result
    assert "这件事相当复杂。" not in result
