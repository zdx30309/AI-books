from __future__ import annotations

import asyncio
import re

import pytest

from app.prompts.goal_driven_templates import SUPPORTED_CATEGORIES
from app.services import content_engine
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


def test_finance_recommendation_normalize_cleans_source_leaks() -> None:
    markdown = """@最终书名
测试书
@正文
@1. 测试章节
视频材料中提到，SpaceX 提交了新的文件。
"""

    result = normalize_final_markdown(markdown, category="finance_technology_recommendation")

    assert "视频材料" not in result
    assert "根据公开资料" in result


def test_life_philosophy_normalize_keeps_centered_delivery_markers() -> None:
    markdown = """！！！@三个备选书名
1. 清醒的人生
2. 长期的选择
3. 边界的代价
！！！@三个备选简介
1. 测试简介。
2. 测试简介。
3. 测试简介。
！！！@目录
1. 第一章
！！！@导言
导言内容。
！！！@正文
！！！1. 第一章
这是第一段。
@2. 第二章
这是第二段。
"""

    result = normalize_final_markdown(markdown, category="life_philosophy")

    assert "！！！@三个备选书名" in result
    assert "！！！@三个备选简介" in result
    assert "！！！@目录" in result
    assert "！！！@导言" in result
    assert "！！！@正文" in result
    assert "！！！1. 第一章" in result
    assert "！！！2. 第二章" in result
    assert "\n@正文\n" not in result
    assert "\n@2. 第二章\n" not in result


def test_finance_normalize_rewrites_source_leak_attribution() -> None:
    markdown = """@最终书名
测试书
@最终简介
视频材料中提到，SpaceX 正在扩大算力投入。
@目录
1. 产业变化
@导言
参考材料显示，AI版计划同样极端。
@正文
@1. 产业变化
视频材料中提到，SpaceX 市值达到 5.565 万亿美元。
视频里提到《招股书》，SpaceX 同时建立地球轨道数据中心。
根据《招股书》显示，B 类股票有特殊安排。
参考材料显示，AI版计划同样极端。
前文提到，现金流才是更硬的约束。
"""

    result = normalize_final_markdown(markdown, category="finance_technology")

    assert "根据公开资料，SpaceX 正在扩大算力投入" in result
    assert "根据公开资料，AI版计划同样极端" in result
    assert "根据公开资料，SpaceX 市值达到 5.565 万亿美元" in result
    assert "根据《招股书》，SpaceX 同时建立地球轨道数据中心" in result
    assert "根据《招股书》，B 类股票有特殊安排" in result
    assert "前文提到，现金流才是更硬的约束" in result
    assert "视频材料中提到" not in result
    assert "视频里提到" not in result
    assert "参考材料显示" not in result


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
【P0001】宋定伯 随口 胡诌: "我是刚死的鬼, 所以身子还沉. "

  。
###帝后遗容如生
【P0002】鬼又问: "你打算去哪儿? "
"""

    result = normalize_final_markdown(markdown, category="classical_chinese")
    body = result.split("@正文", 1)[1]
    prose = "\n".join(line for line in body.splitlines() if not re.match(r"^@\d+\.", line))

    assert "\n\n" not in body
    assert "\n。\n" not in body
    assert "宋定伯随口胡诌：“我是刚死的鬼，所以身子还沉。”" in result
    assert "鬼又问：“你打算去哪儿？”" in result
    assert "帝后遗容如生" not in result
    assert "【P0001】" not in result
    assert "【P0002】" not in result
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
    assert "###年龄的真相" in result
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
    assert "###健康四件事\n每天记录四个关键点" in result


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
    assert "# 身体的能量账本" not in result
    assert "###身体的能量账本" in result
    assert "\n1. 起床之后\n" not in result
    assert "起床之后。\n起床之后先记录" not in result
    assert "起床之后先记录昨晚睡了几个小时" in result
    assert "普通段落应该只去掉非法 at 符号" in result
    assert "@普通段落" not in result


def test_normalize_standardizes_inline_pseudo_subheading_for_all_categories() -> None:
    markdown = """@正文
@1. 新药中国路
扎堆竞争的代价，临床资源就那么多，愿意参加试验的患者也有限。
@不过这种模式的局限性也摆在眼前：如果只是慢半拍的简单复制，留给你的窗口期会越来越短。
"""

    result = normalize_final_markdown(markdown, category="finance_technology")

    assert "###扎堆竞争的代价\n临床资源就那么多" in result
    assert "###不过这种模式的局限性也摆在眼前\n如果只是慢半拍" in result
    assert "扎堆竞争的代价，临床资源" not in result
    assert "@不过这种模式" not in result


def test_normalize_standardizes_inline_pseudo_subheading_across_text_categories() -> None:
    markdown = """@正文
@1. 新药中国路
扎堆竞争的代价，临床资源就那么多，愿意参加试验的患者也有限。
"""
    special_layout_categories = {
        "comic_series",
        "healing_comic",
        "classical_chinese",
        "classical_chinese_interleaved",
    }

    for category in SUPPORTED_CATEGORIES:
        if category in special_layout_categories:
            continue
        result = normalize_final_markdown(markdown, category=category)

        assert "###扎堆竞争的代价\n临床资源就那么多" in result, category
        assert "扎堆竞争的代价，临床资源" not in result, category


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


def test_final_hard_error_gate_repairs_flagged_semantic_issue(monkeypatch, tmp_path) -> None:
    markdown = """@最终书名
测试书
@正文
@1. 盐味成患
这人心里便想“菜之所以好喝，是因为加了盐。”
"""

    async def fake_route_writer_call(prompt: str, **kwargs) -> str:
        assert "终稿硬伤质检编辑" in prompt
        assert "菜之所以好喝" in prompt
        return markdown.replace("菜之所以好喝", "菜之所以好吃")

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)

    result = asyncio.run(
        content_engine._repair_final_hard_errors_once(
            markdown,
            category="buddhist_scripture_story",
            output_dir=tmp_path,
        )
    )

    assert "菜之所以好吃" in result
    assert "菜之所以好喝" not in result
    assert (tmp_path / "final_hard_error_issues.txt").exists()
    assert (tmp_path / "semantic_quality_repaired.md").exists()


def test_final_hard_error_gate_skips_clean_manuscript_without_llm(monkeypatch, tmp_path) -> None:
    markdown = """@最终书名
测试书
@正文
@1. 日常饮食
这碗汤很好喝。
这盘菜很好吃。
"""

    async def fail_if_called(*args, **kwargs) -> str:
        raise AssertionError("clean manuscript should not call the writer")

    monkeypatch.setattr(content_engine, "_route_writer_call", fail_if_called)

    result = asyncio.run(
        content_engine._repair_final_hard_errors_once(
            markdown,
            category="buddhist_scripture_story",
            output_dir=tmp_path,
        )
    )

    assert result == markdown
    assert not (tmp_path / "semantic_quality_repaired.md").exists()


def test_final_hard_error_gate_fails_when_repair_keeps_blocking_issue(monkeypatch, tmp_path) -> None:
    markdown = """@最终书名
测试书
@正文
@1. 盐味成患
这人心里便想“菜之所以好喝，是因为加了盐。”
"""

    async def fake_route_writer_call(prompt: str, **kwargs) -> str:
        return markdown

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)

    with pytest.raises(RuntimeError, match="终稿硬伤质检失败"):
        asyncio.run(
            content_engine._repair_final_hard_errors_once(
                markdown,
                category="buddhist_scripture_story",
                output_dir=tmp_path,
            )
        )


def test_final_hard_error_gate_refuses_non_repairable_buddhist_coverage_issue(
    monkeypatch,
    tmp_path,
) -> None:
    body = "从前有个愚人做错事，最后明白不可执著表面。"
    chapters = "\n".join(f"@{index}. 第{index}喻\n{body}" for index in range(1, 59))
    markdown = f"@最终书名\n百喻经（白话版）\n@正文\n{chapters}"

    async def fail_if_called(*args, **kwargs) -> str:
        raise AssertionError("coverage issues must not be locally repaired")

    monkeypatch.setattr(content_engine, "_route_writer_call", fail_if_called)

    with pytest.raises(RuntimeError, match="需重跑完整生成链路"):
        asyncio.run(
            content_engine._repair_final_hard_errors_once(
                markdown,
                category="buddhist_scripture_story",
                output_dir=tmp_path,
            )
        )


def test_buddhist_story_targeted_repair_reuses_existing_manuscript_and_removes_extra(
    monkeypatch,
    tmp_path,
) -> None:
    units = [
        content_engine.BuddhistStoryUnit(index=1, title="第一喻", source="昔有愚人执著表面，后为众人所笑。"),
        content_engine.BuddhistStoryUnit(index=2, title="第二喻", source="昔有愚人求取方便，反而失去本来利益。"),
        content_engine.BuddhistStoryUnit(index=3, title="第三喻", source="昔有愚人误认因缘，最后明白不可倒置。"),
    ]
    good_body = (
        "从前有个愚人听见一句话，便急着照做，却没有先看清事情的来龙去脉。"
        "他先做了一个选择，又因为这个选择不断补救，越补越偏。旁人提醒他时，"
        "他仍旧执着在最初的想法里，直到结果摆在眼前，才发现自己抓住的是表面的方便。"
        "这则譬喻不是为了嘲笑愚人，而是提醒修行者：听到一个道理之后，要回到因缘和次第里理解。"
        "只有看见愚人如何一步步走偏，读者才会明白原文真正要警醒的地方。"
        "故事说完时，那个人的荒唐选择已经显出后果，修行上的提醒也自然落回心里。"
        "这段收束不是另起一章讲道理，而是让读者顺着前面的动作和后果，看见执著怎样把小错推成大错。"
        "因此故事本身已经承担了说明的力量，最后的提醒只是把那层意思轻轻点明。"
    )
    markdown = f"""@最终书名
百喻经（白话版）
@正文
@1. 第一喻
{good_body}
@2. 第二喻
太短。
@3. 第三喻
{good_body}
@4. 多余总结
这不是原文的一则譬喻。
"""

    async def fake_route_writer_call(prompt: str, **kwargs) -> str:
        assert "单元修复作者" in prompt
        assert "@2. 第二喻" in prompt
        assert "@1. 第一喻" not in prompt.split("【当前稿件对应章节】", 1)[0]
        repaired_body = good_body + "这一次修复只补足第二则的故事，不改动其他已经合格的章节。"
        return f"@2. 第二喻\n{repaired_body}"

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)

    result = asyncio.run(
        content_engine._repair_buddhist_story_manuscript_against_units(
            markdown,
            units=units,
            output_dir=tmp_path,
            stage="resume",
            original_title="百喻经",
        )
    )

    chapters = content_engine._extract_buddhist_body_chapters(result)
    assert [number for number, _, _ in chapters] == [1, 2, 3]
    assert "@4. 多余总结" not in result
    assert "这一次修复只补足第二则" in result
    assert (tmp_path / "buddhist_story_resume_targeted_issues.txt").exists()
    assert (tmp_path / "buddhist_story_resume_targeted_repaired.md").exists()


def test_buddhist_story_targeted_repair_accepts_borderline_story_length() -> None:
    units = [
        content_engine.BuddhistStoryUnit(
            index=70,
            title="chapter",
            source="\u539f" * 40,
        )
    ]
    accepted = "@\u6b63\u6587\n@70. chapter\n" + ("\u6545" * 293)
    rejected = "@\u6b63\u6587\n@70. chapter\n" + ("\u6545" * 259)

    assert content_engine._buddhist_story_manuscript_issues(accepted, units) == []
    assert content_engine._buddhist_story_manuscript_issues(rejected, units)


def test_buddhist_story_targeted_repair_reuses_cached_chapters_across_batch_changes(
    monkeypatch,
    tmp_path,
) -> None:
    units = [
        content_engine.BuddhistStoryUnit(index=1, title="one", source="\u539f" * 40),
        content_engine.BuddhistStoryUnit(index=2, title="two", source="\u539f" * 40),
    ]
    cached_body = "\u6545" * 270
    repaired_body = "\u4fee" * 270
    (tmp_path / "buddhist_story_resume_targeted_repair_0001.md").write_text(
        f"@1. one\n{cached_body}",
        encoding="utf-8",
    )
    markdown = "@\u6b63\u6587\n@1. one\n\u77ed\n@2. two\n\u77ed"

    async def fake_route_writer_call(prompt: str, **kwargs) -> str:
        assert "@1. one" not in prompt.split("\u3010\u672c\u6279\u6b21\u539f\u6587\u6545\u4e8b\u5355\u5143\u3011", 1)[-1]
        assert "@2. two" in prompt
        return f"@2. two\n{repaired_body}"

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)

    result = asyncio.run(
        content_engine._repair_buddhist_story_manuscript_against_units(
            markdown,
            units=units,
            output_dir=tmp_path,
            stage="resume",
        )
    )

    assert cached_body in result
    assert repaired_body in result
    assert (tmp_path / "buddhist_story_resume_targeted_cache_reuse.txt").exists()
    assert list(tmp_path.glob("buddhist_story_resume_targeted_repair_*_U*.md"))
