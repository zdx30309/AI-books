from __future__ import annotations

import time
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.quality_metrics import detect_final_hard_errors, format_report, measure_quality


def test_empty_string_all_zero() -> None:
    report = measure_quality("")
    assert report.total_chars == 0
    assert report.total_paragraphs == 0
    assert report.total_violations == 0
    assert report.violation_density_per_kchar == 0.0


def test_a1_pattern_hit() -> None:
    report = measure_quality("这不是简单问题，而是结构性的。")
    assert report.a1_pattern_hits.get("不是X而是Y")


def test_a1_fixed_hit() -> None:
    report = measure_quality("他用力托住那块石头。")
    assert report.a1_fixed_hits["托住"]


def test_ellipsis_compatibility() -> None:
    texts = [
        "新的秩序逐渐长出来。",
        "新的秩序…长出来。",
        "新的秩序……长出来。",
        "新的秩序...长出来。",
    ]
    for text in texts:
        report = measure_quality(text)
        assert report.a1_pattern_hits["X长出来"]


def test_a2_hits() -> None:
    report = measure_quality("综上所述，不可否认这件事。")
    assert report.a2_closing_hits["综上所述"]
    assert report.a2_empty_modifier_hits["不可否认"]


def test_llm_residual_and_meta_tags() -> None:
    report = measure_quality("请随时告诉我。<audit></audit></final_text>")
    assert report.llm_residual_hits["请随时告诉我"]
    assert report.llm_residual_hits["<audit>"]
    assert report.llm_residual_hits["</audit>"]
    assert report.llm_residual_hits["</final_text>"]


def test_chicken_soup_tail_sensitive() -> None:
    middle = "前面内容。" + ("中段文字。" * 80) + "未来可期。"
    report_mid = measure_quality(middle)
    assert "未来可期" not in report_mid.a2_chicken_soup_hits

    tail = "前面内容。" + ("中段文字。" * 10) + ("尾部内容。" * 10) + "未来可期。"
    report_tail = measure_quality(tail)
    assert report_tail.a2_chicken_soup_hits["未来可期"]


def test_transition_sentence_start_sensitive() -> None:
    report_start = measure_quality("与此同时，我们开始行动。")
    assert report_start.a2_transition_hits["与此同时(段首)"]

    report_mid = measure_quality("前文铺垫。与此同时，我们开始行动。")
    assert "与此同时(段首)" not in report_mid.a2_transition_hits


def test_half_width_punct_and_marks() -> None:
    report = measure_quality("这是一段中文,带半角逗号。\n！！这是强调。")
    assert report.half_width_punct_count >= 1
    assert report.mark_double_excl == 1


def test_format_noise_detects_markdown_heading_and_editorial_leftovers() -> None:
    markdown = """@最终书名
测试书
@正文
@1. 正式章节
**年龄的真相**30岁后的状态下滑。
@这几句话逻辑自洽，让普通人找到了出口。
# 身体的能量账本
1. 起床之后
财富自由的三种诉求
"""

    report = measure_quality(markdown)

    assert report.format_noise_hits["Markdown 粗体残留"]
    assert report.format_noise_hits["非法正文 @ 段首"]
    assert report.format_noise_hits["审稿批注式残留"]
    assert report.format_noise_hits["# 标题残留"]
    assert report.format_noise_hits["正文内编号伪标题"]
    assert report.format_noise_hits["裸短小标题"]
    assert report.total_violations >= 6


def test_format_noise_allows_standard_internal_subheading() -> None:
    markdown = """@最终书名
测试书
@正文
@1. 正式章节
###扎堆竞争的代价
临床资源就那么多。
"""

    report = measure_quality(markdown)

    assert "# 标题残留" not in report.format_noise_hits
    assert "裸短小标题" not in report.format_noise_hits


def test_final_hard_error_gate_detects_semantic_collocation_errors() -> None:
    markdown = """@最终书名
测试书
@正文
@1. 盐味成患
这人心里便想“菜之所以好喝，是因为加了盐。”
"""

    hits = detect_final_hard_errors(markdown, category="buddhist_scripture_story")
    report = measure_quality(markdown, category="buddhist_scripture_story")
    rendered = format_report(report)

    assert hits["语义搭配硬伤：菜/饭菜不应写成好喝"]
    assert report.final_hard_error_hits["语义搭配硬伤：菜/饭菜不应写成好喝"]
    assert "终稿硬伤闸门" in rendered


def test_final_hard_error_gate_does_not_flag_valid_food_or_drink_collocations() -> None:
    markdown = """@最终书名
测试书
@正文
@1. 日常饮食
这碗汤很好喝。
这盘菜很好吃。
"""

    hits = detect_final_hard_errors(markdown, category="buddhist_scripture_story")

    assert "语义搭配硬伤：菜/饭菜不应写成好喝" not in hits
    assert "语义搭配硬伤：汤/茶/水不应写成好吃" not in hits


def test_final_hard_error_gate_detects_illegal_at_pseudo_heading() -> None:
    markdown = """@最终书名
测试书
@正文
@1. 新药中国路
@不过这种模式的局限性也摆在眼前：如果只是慢半拍的简单复制，留给你的窗口期会越来越短。
"""

    hits = detect_final_hard_errors(markdown, category="finance_technology")

    assert hits["非法正文 @ 段首"]


def test_final_hard_error_gate_allows_standard_internal_subheading() -> None:
    markdown = """@最终书名
测试书
@正文
@1. 新药中国路
###扎堆竞争的代价
临床资源就那么多。
"""

    hits = detect_final_hard_errors(markdown, category="finance_technology")

    assert not hits


def test_final_hard_error_gate_allows_classical_interleaved_original_labels() -> None:
    markdown = """@最终书名
盐铁论（白话版）
@正文
@1. 盐铁论
【P0001】（原文）卷一
【P0002】（原文）本议第一
【P0003】（翻译）这是对应的白话解释。
"""

    hits = detect_final_hard_errors(markdown, category="classical_chinese_interleaved")

    assert "裸短小标题" not in hits
    assert not hits


def test_final_hard_error_gate_allows_classical_reference_original_translation_pairs() -> None:
    markdown = """@最终书名
菜根谭（白话版）
@正文
@1. 菜根谭
（原文）路径窄处，留一步与人行；滋味浓处，减三分让人嗜。此是涉世一极乐法。
（翻译）路走到狭窄的地方，要留出一步让别人同行；滋味正浓的时候，要减去三分让别人也能品尝。这是处世中一种很快乐的办法。
"""

    hits = detect_final_hard_errors(markdown, category="classical_chinese_reference_rewrite")

    assert "正文内小标题未标准化为 ###" not in hits
    assert "裸短小标题" not in hits
    assert not hits


def test_final_hard_error_gate_detects_classical_reference_stiff_direct_translation() -> None:
    markdown = """@最终书名
菜根谭（白话版）
@正文
@1. 菜根谭
（原文）欲做精金美玉的人品，定从烈火中煅来；思立掀天揭地的事功，须向薄冰上履过。
（翻译）想要磨炼出如精金美玉般纯粹的人品，定要经过烈火般的煅烧；想要建立惊天动地的功业，必须像脚踩薄冰一样，谨慎地走过险境。
"""

    hits = detect_final_hard_errors(markdown, category="classical_chinese_reference_rewrite")

    assert hits["古文参照译写硬伤：精金美玉句停留在直译腔"]


def test_final_hard_error_gate_allows_classical_reference_plain_modern_rewrite() -> None:
    markdown = """@最终书名
菜根谭（白话版）
@正文
@1. 菜根谭
（原文）欲做精金美玉的人品，定从烈火中煅来；思立掀天揭地的事功，须向薄冰上履过。
（翻译）一个人要想拥有真正过硬的好品格，就必须在现实的严酷考验中不断捶打自己；要想建立了不起的功业，就必须在如履薄冰的压力下，踏踏实实走好每一步，熬过所有的磨难。
"""

    hits = detect_final_hard_errors(markdown, category="classical_chinese_reference_rewrite")

    assert "古文参照译写硬伤：精金美玉句停留在直译腔" not in hits
    assert "古文参照译写硬伤：古典意象未转成大白话" not in hits
    assert not hits


def test_final_hard_error_gate_detects_buddhist_thin_moral_chapters() -> None:
    chapters: list[str] = []
    for index in range(1, 31):
        if index % 2:
            body = "有个愚人来到河边，听见别人说水深，便站在岸上犹豫不前。他看着水流过去，口渴越来越重，却始终不肯低头饮水。"
        else:
            body = "修行不可只听名相。"
        chapters.append(f"@{index}. 测试章节{index}\n{body}")
    markdown = "@最终书名\n佛经白话\n@正文\n" + "\n".join(chapters)

    hits = detect_final_hard_errors(markdown, category="buddhist_scripture_story")

    assert hits["佛经白话章节过短，疑似故事和寓意被拆章"]


def test_final_hard_error_gate_detects_baiyujing_over_split_chapter_count() -> None:
    body = "这个譬喻先写愚人的选择，再写事情如何一步步走向荒唐，最后在同一章里自然收束到修行上的提醒。"
    chapters = "\n".join(f"@{index}. 第{index}喻\n{body}" for index in range(1, 122))
    markdown = f"@最终书名\n百喻经（白话版）\n@正文\n{chapters}"

    hits = detect_final_hard_errors(markdown, category="buddhist_scripture_story")

    assert hits["《百喻经》章节数量异常"]


def test_final_hard_error_gate_detects_baiyujing_extra_summary_chapter() -> None:
    body = "这个譬喻先写愚人的选择，再写事情如何一步步走向荒唐，最后在同一章里自然收束到修行上的提醒。"
    chapters = "\n".join(f"@{index}. 第{index}喻\n{body}" for index in range(1, 100))
    markdown = f"@最终书名\n百喻经（白话版）\n@正文\n{chapters}"

    hits = detect_final_hard_errors(markdown, category="buddhist_scripture_story")

    assert hits["《百喻经》章节数量异常"]


def test_final_hard_error_gate_detects_baiyujing_missing_late_chapters() -> None:
    body = "这个譬喻先写愚人的选择，再写事情如何一步步走向荒唐，中间有动作和转折，最后在同一章里自然收束到修行上的提醒。"
    chapters = "\n".join(f"@{index}. 第{index}喻\n{body}" for index in range(1, 59))
    markdown = f"@最终书名\n百喻经（白话版）\n@正文\n{chapters}"

    hits = detect_final_hard_errors(markdown, category="buddhist_scripture_story")

    assert hits["《百喻经》章节覆盖不足"]


def test_final_hard_error_gate_detects_buddhist_story_chapters_too_short() -> None:
    body = "从前有个愚人做错事，最后明白不可执著表面。"
    chapters = "\n".join(f"@{index}. 第{index}喻\n{body}" for index in range(1, 31))
    markdown = f"@最终书名\n佛经白话\n@正文\n{chapters}"

    hits = detect_final_hard_errors(markdown, category="buddhist_scripture_story")

    assert hits["佛经白话章节过短，故事展开不足"]


def test_final_hard_error_gate_allows_clear_short_buddhist_parables() -> None:
    body = (
        "从前有个愚人到人家做客，嫌饭食太淡。主人添了一点盐，饭味立刻变得鲜美。"
        "愚人便以为美味全在盐里，回去后不再吃饭，只把盐大把吞下，结果口中苦涩，反受其害。"
        "这则譬喻提醒修行者，节制饮食本为调养身心，若执著到断绝饮食，反会把助道的方法变成伤身的偏见。"
    )
    chapters = "\n".join(f"@{index}. 第{index}喻\n{body}" for index in range(1, 31))
    markdown = f"@最终书名\n佛经白话\n@正文\n{chapters}"

    hits = detect_final_hard_errors(markdown, category="buddhist_scripture_story")

    assert "佛经白话章节过短，故事展开不足" not in hits
    assert "佛经白话章节过短，疑似故事和寓意被拆章" not in hits


def test_final_hard_error_gate_allows_baiyujing_single_chapter_per_parable() -> None:
    body = (
        "这个譬喻先写愚人的选择，再写事情如何一步步走向荒唐。愚人听见一句话，便急着照做，"
        "却没有先看清因缘和次第。他越想证明自己是对的，就越把小问题变成大麻烦。旁人提醒他时，"
        "他仍旧执著在最初的想法里，直到后果摆在眼前，才发现自己抓住的是表面的方便，丢掉的是事情真正的根本。"
        "故事最后在同一章里自然收束到修行上的提醒：听闻道理以后，不能只拿一句结论来套用，"
        "还要回到具体处境里照见自己的起心动念。若看不见这个过程，所谓修行就会变成另一种执著。"
        "这时故事已经不只是一个笑话，而是一条从选择到后果的路。读者能看见人如何在一点小利面前失去判断，"
        "也能看见佛经为什么要用譬喻来说法：抽象的教义容易被记成口号，具体的故事却会把人的颠倒照出来。"
    )
    chapters = "\n".join(f"@{index}. 第{index}喻\n{body}" for index in range(1, 99))
    markdown = f"@最终书名\n百喻经（白话版）\n@正文\n{chapters}"

    hits = detect_final_hard_errors(markdown, category="buddhist_scripture_story")

    assert "《百喻经》章节数量异常" not in hits
    assert "《百喻经》章节覆盖不足" not in hits
    assert "佛经白话章节过短，故事展开不足" not in hits
    assert "佛经白话章节过短，疑似故事和寓意被拆章" not in hits


def test_life_philosophy_centered_markers_are_not_counted_as_double_excl_noise() -> None:
    markdown = """！！！@三个备选书名
测试书
！！！@正文
！！！1. 正式章节
正文内容。
"""

    report = measure_quality(markdown, category="life_philosophy")

    assert report.mark_double_excl == 0
    assert not report.format_noise_hits


def test_finance_source_leak_hits_are_category_scoped() -> None:
    markdown = """@最终书名
测试书
@正文
@1. 正式章节
视频材料中提到，SpaceX 正在扩大算力投入。
根据《招股书》显示，SpaceX 设置了投票权安排。
根据公开资料，AI 服务器价格仍受供应链影响。
"""

    report = measure_quality(markdown, category="finance_technology")
    rendered = format_report(report)
    other_report = measure_quality(markdown, category="social_science")

    assert report.finance_source_leak_hits["财经来源穿帮表述残留"]
    assert "财经来源穿帮表述残留" in rendered
    assert not other_report.finance_source_leak_hits


def test_finance_recommendation_source_leak_hits_reuse_finance_quality_gate() -> None:
    markdown = """@最终书名
测试书
@正文
@1. 正式章节
视频材料中提到，某公司调整业务结构。
"""

    report = measure_quality(markdown, category="finance_technology_recommendation")

    assert report.finance_source_leak_hits["财经来源穿帮表述残留"]


def test_finance_source_leak_cleaned_text_no_longer_triggers_quality_hit() -> None:
    markdown = """@最终书名
测试书
@正文
@1. 正式章节
根据公开资料，SpaceX 正在扩大算力投入。
根据《招股书》，SpaceX 同时建立地球轨道数据中心。
"""

    report = measure_quality(markdown, category="finance_technology")

    assert not report.finance_source_leak_hits


def test_masterpiece_colloquial_hits_are_category_scoped() -> None:
    markdown = """@最终书名
咱们说白了这事儿
@正文
@1. 正式章节
说白了，这事儿挺复杂的，对吧。
原句说“这事儿”。
"""

    report = measure_quality(markdown, category="masterpiece_interpretation")
    rendered = format_report(report)
    other_report = measure_quality(markdown, category="social_science")

    assert report.masterpiece_colloquial_hits["说白了"]
    assert report.masterpiece_colloquial_hits["这事儿"]
    assert report.masterpiece_colloquial_hits["挺...的"]
    assert report.masterpiece_colloquial_hits["对吧"]
    assert "名作解读口语化残留" in rendered
    assert not other_report.masterpiece_colloquial_hits
    assert "咱们说白了这事儿" not in "\n".join(
        sample for samples in report.masterpiece_colloquial_hits.values() for sample in samples
    )


def test_decimal_not_counted() -> None:
    report = measure_quality("GDP 增长 3.5% 。")
    assert report.half_width_punct_count == 0


def test_performance_smoke() -> None:
    text = ("这是一个中文段落，没有太多违规内容。" * 2000)
    start = time.time()
    report = measure_quality(text)
    elapsed = time.time() - start
    assert report.total_chars == len(text)
    assert elapsed < 1.0
