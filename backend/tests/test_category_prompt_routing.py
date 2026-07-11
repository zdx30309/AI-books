from __future__ import annotations

from pathlib import Path
import sys
import asyncio
import inspect
import json
import re

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from app.core.quality_metrics import detect_final_hard_errors
from app.prompts.goal_driven_templates import (
    CLASSICAL_NOVEL_STYLE_LOCK_PROMPT,
    CLASSICAL_REFERENCE_REWRITE_LITERARY_POLISH_PROMPT,
    CLASSICAL_REFERENCE_REWRITE_PROFILE,
    CLASSICAL_REFERENCE_REWRITE_SEMANTIC_LOCK_PROMPT,
    INTRO_BODY_REFINE_GUARD_PROMPT,
    INTRO_BODY_SEPARATION_PROMPT,
    POLISH_FIXED_PHRASE_GUARD_PROMPT,
    SECTION_TITLE_REFINE_GUARD_PROMPT,
    SECTION_TITLE_REQUIREMENT_PROMPT,
    SUPPORTED_CATEGORIES,
    get_category_meta,
    get_prompts,
    list_categories,
    normalize_category,
)
from app.services.classical_chinese import (
    BUDDHIST_SCRIPTURE_STORY_CATEGORY,
    CLASSICAL_NOVEL_STYLE_CATEGORY,
    CLASSICAL_REFERENCE_REWRITE_CATEGORY,
    build_buddhist_scripture_story_source,
    build_classical_prompt_source,
    classical_paragraph_marker,
    classical_variant_rules,
    classical_variant_for_category,
    get_classical_variant_meta,
    split_buddhist_story_units,
    strip_classical_paragraph_markers,
)
from app.services import content_engine
from app.services.gemini import GeminiMaxTokensError


MINIMAL_MANUSCRIPT = """@最终书名
测试书名
@最终简介
测试简介
@目录
1. 测试章节
@导言
测试导言。
@正文
@1. 测试章节
测试正文。
"""


EXPECTED_CLASSICAL_REFERENCE_REWRITE_PROFILE = """【参照译写默认范式】
- 这个分类现在只做“原文一段、翻译一段”的干净对照稿，不再参考《菜根谭》类全注全译、注释、点评或讲解体例。
- 参考译本只用于校准释义、术语译法、断句、称谓和语气准确性；如果系统提供“当前原文对应参考译文召回”，必须优先用召回片段校准本段真正含义。
- 默认范本是“菜根谭-中华书局参照范本”时，凡召回片段覆盖到当前原文，一律以中华书局版本的断句、关键字词释义、术语方向和语气密度为第一依据；古汉语词典只负责核对、补足未覆盖字词，不得推翻已命中的中华书局释义。
- 每个原文段落必须先保留原文，再紧跟对应现代白话翻译；不得把全部原文集中放前面，也不得把全部翻译集中放后面。
- 最终正文只允许出现“（原文）”和“（翻译）”两类成对段落。
- 输出目标不是古典直译腔，也不是现代励志文、鸡汤文或讲义文，而是在中华书局释义基础上，译成清楚、自然、有文气的现代白话。
- 古典比喻原则上要保留其核心意象和比喻方向，不要轻易抹掉。只有在现代读者确实难以理解时，才可以补充为更清楚的白话表达。比如“精金美玉”应优先译为“精金美玉般纯正坚实的人品”，不宜直接改成“过硬的好品格”；“薄冰上履过”应保留“如履薄冰”的艰险与戒慎，不宜泛化成“压力下走好每一步”。
- 所有翻译落笔前，必须在做联网检索交叉验证。检索中华书局等可靠整理本、古汉语词典、权威注释、学术资料。
- 落不能望文生义，不能用现代汉语常见意思拆字理解原文。遇到“修业、要名、结好、植节、标异见奇、非心、尘氛、芥蒂、圆机”等词语，必须先按古汉语义项和权威注释核定含义，再转成白话。不得把“修业”直接译成现代商业语境的“经营事业”，不得把“要名”译成“索取名声”，不得把“标异见奇”简化成现代意义上的“标新立异”。"""

EXPECTED_CLASSICAL_REFERENCE_REWRITE_SEMANTIC_LOCK = """【古文白话参照译写版语义锁】
- 本稿的翻译段已经按中华书局或用户指定权威参照译本校准。你现在只能做语言顺滑、标点整理、轻微去翻译腔和格式保护；不得重新翻译古文，不得自行重断句，不得自行改义项。
- 中华书局对应译文覆盖到的地方，断句、关键字词释义、术语方向、主谓宾关系、比喻方向、承接关系、语气轻重都按中华书局为第一依据。
- 终稿目标是在中华书局释义基础上更大白话一点：允许把古典直译腔改成普通读者能直接理解的现代说法，但不得改变义项、逻辑、轻重、比喻方向。
- “便觉、始觉、方觉、乃觉、忽觉”里的“觉”按“觉得、感到、发觉”处理，不得译成“导致、造成、引发、致使”。
- 不得新增注释、点评、赏析、解释表、译者按或圈号说明；最终仍然只保留“（原文）/（翻译）”成对段落。
"""

EXPECTED_CLASSICAL_REFERENCE_REWRITE_LITERARY_POLISH = """【古文白话参照译写版文气润色层】
- 这一步只做“译后润色”，不重新翻译，不新增注释、点评、赏析或人生总结。
- 翻译段要从“语文教材式解释”改成“普通读者能顺着读下去的大白话”，有气口、有停顿、有一点文气，但不能油滑、煽情或鸡汤。
- 不要使用“这句话的意思是”“这说明”“所谓”“也就是说”“我们可以看出”“由此可见”等讲义腔开头。
- 每个翻译段都要像一段自然现代汉语：先把原文的判断说清，再把古典比喻落到现实经验里。
- 中华书局或用户指定参考译文命中的义项、断句和术语仍是第一依据。
- 输出结构仍然只保留“（原文）/（翻译）”成对段落，不得加入新标题、新栏目或说明。
"""

EXPECTED_CLASSICAL_NOVEL_STYLE_LOCK = """【古文白话小说风格版锁定规则】
- 当前稿件是古文故事的现代白话小说重写版，不是逐句翻译稿。最终正文必须是完整、顺畅、有故事推进感的现代白话叙事，不得恢复原文穿插，不得加入“（原文）/（翻译）”、P 编号、注释、译者按或翻译过程。
- 必须先保证人物关系、事件事实、因果逻辑、行动顺序和结局不变；这里的“准确”指剧情事实准确，不是要求逐字逐句对译。叙事型原文要改写成现代白话小说，不得改变原文事实；抽象格言型原文必须用故事载体表达原文道理。
- 若本锁定规则与通用古文白话“一段原文一段翻译、逐句解释、不得补动作”等规则冲突，以小说风格版为准：最终必须是故事化白话叙事，不是译文说明。
- 叙事型原文可以补足必要的动作承接、人物反应、场景气息、对白语气和转折过渡，让故事读起来连贯、有画面。但不得新增会改变剧情事实的人物、地点、事件、冲突、支线、结局、现代设定或价值判断。允许增加“非剧情事实型细节”，例如停顿、抬头等，用来增强画面和节奏，但不能改变原文事件走向。
- 叙事型古文，尤其《杨家将》这类已有故事的文本，要按现代通俗小说方式重写：开头尽快进入人物处境和主要冲突，压缩交代，强化选择、争执、危机和后果。不要只把古文逐句翻成说明文，也不要写成“某人说、某人又说、某人听罢”的流水账。每一段都要推动情节、加深冲突或完成转折。
- 抽象格言、议论、训诫、修身判断若本身没有人物事件，允许引入无名人物、普通处境、少量动作和心理转折作为故事化表达载体。
- 抽象格言型原文必须出现人物处境、一次具体选择、行动变化或心理转折，让道理从故事里显出来；不能只写“一个人要怎样”“这说明什么”“这句话是在说什么”。
- 不得把稿件改回普通直译、大白话句义解释或资料讲义；必须保留故事推进感。
- 不得删减原文关键信息，不得只保留故事梗概；但不要求逐字逐句覆盖原文。可以合并重复信息、调整句式顺序、压缩低价值铺陈，把原文的关键信息自然融入新的叙事段落中。保留的是人物、事件、因果、选择、后果和关键对白，不是保留古文句子的表面结构。
"""

EXPECTED_CLASSICAL_NOVEL_STYLE_VARIANT = """- 这不是把文言文逐句翻成大白话，也不是翻译后轻微润色；要先彻底理解原文讲了什么，再以现代通俗小说的方式重新组织语言。重写时可以改句式、调节奏、合并重复、补过渡、强化冲突，但必须忠于原文的人物关系、事件事实、因果顺序和结局。
- 第一优先级是准确和完整：叙事型原文的人物、关系、事件、关键动作、关键对白、因果、顺序和结局不能改；但普通叙述句、动作承接、心理反应、场面描写和对白表达方式可以重新写。抽象型原文的核心判断、因果、转折和轻重不能改。
- 第二优先级是故事感：叙事型要写成有画面、有冲突、有节奏、有转折的现代白话故事。人物开口要有立场，场面推进要有压力，危机到来要有速度，转折发生要有冲击。
- 小说化要有边界：不能新增具体史实、名人、朝代、地名、复杂冲突、支线、结局反转或新观点。
- 不要把抽象格言写成讲义、注释、鸡汤或“这句话的意思是……”式解释。"""

WRITER_ROUTING_CATEGORIES = [
    category
    for category in SUPPORTED_CATEGORIES
    if category
    not in {
        "comic_series",
        "healing_comic",
        "finance_technology",
        "finance_technology_recommendation",
    }
]


def test_classical_category_prompt_fragments_match_word_versions_exactly() -> None:
    assert CLASSICAL_REFERENCE_REWRITE_PROFILE == EXPECTED_CLASSICAL_REFERENCE_REWRITE_PROFILE
    assert (
        CLASSICAL_REFERENCE_REWRITE_SEMANTIC_LOCK_PROMPT
        == EXPECTED_CLASSICAL_REFERENCE_REWRITE_SEMANTIC_LOCK
    )
    assert (
        CLASSICAL_REFERENCE_REWRITE_LITERARY_POLISH_PROMPT
        == EXPECTED_CLASSICAL_REFERENCE_REWRITE_LITERARY_POLISH
    )
    assert CLASSICAL_NOVEL_STYLE_LOCK_PROMPT == EXPECTED_CLASSICAL_NOVEL_STYLE_LOCK
    assert classical_variant_rules("novel_style") == EXPECTED_CLASSICAL_NOVEL_STYLE_VARIANT


@pytest.mark.parametrize("category", WRITER_ROUTING_CATEGORIES)
def test_generate_full_book_routes_writer_prompt_by_category(
    category: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_text = "这是一段用于本地路由测试的原始文稿。"
    writer_prompts: list[str] = []
    expected_raw_text = raw_text
    if category == "classical_chinese":
        expected_raw_text = build_classical_prompt_source(raw_text)
    elif category == "classical_chinese_interleaved":
        expected_raw_text = build_classical_prompt_source(raw_text, interleaved=True)
    elif category == CLASSICAL_NOVEL_STYLE_CATEGORY:
        expected_raw_text = build_classical_prompt_source(raw_text, variant="novel_style")
    elif category == CLASSICAL_REFERENCE_REWRITE_CATEGORY:
        expected_raw_text = build_classical_prompt_source(raw_text, variant="reference_translation")
    elif category == BUDDHIST_SCRIPTURE_STORY_CATEGORY:
        expected_raw_text = build_buddhist_scripture_story_source(raw_text)
    expected_prompt = get_prompts(category)["full_book_writer"].format(raw_text=expected_raw_text)

    async def fake_route_writer_call(prompt: str, **_: object) -> str:
        if prompt == expected_prompt:
            writer_prompts.append(prompt)
        return MINIMAL_MANUSCRIPT

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "polish_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "refine_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "format_book_text", lambda text, **_: text)

    result = asyncio.run(
        content_engine.generate_full_book(
            raw_text,
            category=category,
            output_dir=tmp_path / category,
        )
    )

    assert "@最终书名" in result
    assert writer_prompts == [expected_prompt]


def test_every_supported_category_has_ready_prompt_metadata() -> None:
    for category in SUPPORTED_CATEGORIES:
        meta = get_category_meta(category)
        prompts = get_prompts(category)

        assert meta["prompt_status"] == "ready"
        assert "{raw_text}" in prompts["full_book_writer"]
        assert "full_book_writer" in prompts
        assert "gemini_fast_polish" in prompts
        assert "gemini_fast_refine" in prompts
        assert "final_book_format" in prompts


def test_masterpiece_interpretation_uses_requested_writer_prompt_and_generic_later_stages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    category = "masterpiece_interpretation"
    raw_text = "\u8fd9\u662f\u4e00\u6bb5\u540d\u4f5c\u89e3\u8bfb\u6d4b\u8bd5\u6587\u7a3f\u3002"
    prompts = get_prompts(category)
    generic_prompts = get_prompts("social_science")
    calls: list[str] = []
    writer_prompts: list[str] = []
    polish_instructions: list[str | None] = []
    refine_instructions: list[str | None] = []
    format_templates: list[str | None] = []
    events: list[str] = []

    async def fake_route_writer_call(prompt: str, **_: object) -> str:
        calls.append("writer")
        writer_prompts.append(prompt)
        return MINIMAL_MANUSCRIPT

    def fake_polish_book_text(text: str, **kwargs: object) -> str:
        calls.append("polish")
        polish_instructions.append(kwargs.get("system_instruction"))
        assert text == MINIMAL_MANUSCRIPT.strip()
        return text + "\nPOLISHED_MARKER"

    def fake_refine_book_text(text: str, **kwargs: object) -> str:
        calls.append("refine")
        refine_instructions.append(kwargs.get("system_instruction"))
        assert "POLISHED_MARKER" in text
        return text + "\nREFINED_MARKER"

    def fake_format_book_text(text: str, **kwargs: object) -> str:
        calls.append("format")
        format_templates.append(kwargs.get("prompt_template"))
        assert "REFINED_MARKER" in text
        return text + "\nFORMATTED_MARKER"

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "polish_book_text", fake_polish_book_text)
    monkeypatch.setattr(content_engine, "refine_book_text", fake_refine_book_text)
    monkeypatch.setattr(content_engine, "format_book_text", fake_format_book_text)

    result = asyncio.run(
        content_engine.generate_full_book(
            raw_text,
            category=category,
            output_dir=tmp_path / category,
            on_stage=lambda stage, info: events.append(stage),
        )
    )

    assert calls == ["writer", "polish", "refine", "format"]
    assert writer_prompts == [prompts["full_book_writer"].format(raw_text=raw_text)]
    assert "\u540d\u4f5c\u89e3\u8bfb" in writer_prompts[0]
    assert "\u6210\u719f\u7684\u4eba\u6587\u8bfb\u7269\u5199\u4f5c\u8005" in writer_prompts[0]
    assert "\u76f4\u63a5\u751f\u6210\u4e00\u7bc7\u5b8c\u6574\u7684\u6df1\u5ea6\u957f\u6587" in writer_prompts[0]
    assert "只能作为“参照材料”，不能作为逐段改写的底稿" in writer_prompts[0]
    assert "写作要把文学人物、经典情节、作品主题，放回今天普通人的处境" in writer_prompts[0]
    assert "不要过于口语化" in writer_prompts[0]
    assert "不要像唠嗑，不要空话，不要学术腔，不要翻译腔" in writer_prompts[0]
    assert "不要做心理和情绪刻画，不要使用比喻修辞" in writer_prompts[0]
    assert "不要使用“咱们”“说实话”“挺……的”" in writer_prompts[0]
    assert raw_text in writer_prompts[0]
    assert polish_instructions == [prompts["gemini_fast_polish"]]
    assert refine_instructions == [prompts["gemini_fast_refine"]]
    assert prompts["gemini_fast_polish"] == generic_prompts["gemini_fast_polish"]
    assert prompts["gemini_fast_refine"] == generic_prompts["gemini_fast_refine"]
    assert prompts["final_book_format"] == generic_prompts["final_book_format"]
    assert "名作解读文风保护" not in polish_instructions[0]
    assert "文风一致性硬约束" not in refine_instructions[0]
    assert format_templates == [prompts["final_book_format"]]
    assert "FORMATTED_MARKER" in result
    assert events[:2] == ["writer_start", "writer_done"]
    assert "polish_start" in events
    assert "polish_done" in events
    assert "refine_start" in events
    assert "refine_done" in events
    assert "format_start" in events
    assert "format_done" in events


def test_writer_stream_progress_is_forwarded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict[str, object]]] = []

    async def fake_route_writer_call(prompt: str, **kwargs: object) -> str:
        on_progress = kwargs.get("on_progress")
        assert callable(on_progress)
        on_progress(2400)
        return MINIMAL_MANUSCRIPT

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "polish_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "refine_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "format_book_text", lambda text, **_: text)

    asyncio.run(
        content_engine.generate_full_book(
            "writer progress input",
            category="social_science",
            output_dir=tmp_path / "writer_progress",
            on_stage=lambda stage, info: events.append((stage, info)),
        )
    )

    assert ("writer_progress", {"chars": 2400}) in events


def test_format_stream_progress_is_forwarded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict[str, object]]] = []

    async def fake_route_writer_call(prompt: str, **_: object) -> str:
        return MINIMAL_MANUSCRIPT

    def fake_format_book_text(text: str, **kwargs: object) -> str:
        on_progress = kwargs.get("on_progress")
        assert callable(on_progress)
        on_progress(3600)
        return text

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "polish_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "refine_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "format_book_text", fake_format_book_text)

    asyncio.run(
        content_engine.generate_full_book(
            "format progress input",
            category="social_science",
            output_dir=tmp_path / "format_progress",
            on_stage=lambda stage, info: events.append((stage, info)),
        )
    )

    assert ("format_progress", {"chars": 3600}) in events


def test_web_short_story_is_history_test_prompt_sync() -> None:
    history_prompts = get_prompts("history_culture")
    web_short_story_prompts = get_prompts("web_short_story")

    assert web_short_story_prompts == history_prompts
    assert get_category_meta("web_short_story")["label"] == "历史测试版"


def test_social_science_test_prompt_sync() -> None:
    social_prompts = get_prompts("social_science")
    social_test_prompts = get_prompts("social_science_test")

    assert social_test_prompts == social_prompts
    assert get_category_meta("social_science_test")["label"] == "社会痛点测试版"


def test_life_philosophy_category_uses_three_alternative_prompt_flow() -> None:
    prompts = get_prompts("life_philosophy")
    writer = prompts["full_book_writer"]
    polish = prompts["gemini_fast_polish"]
    refine = prompts["gemini_fast_refine"]
    formatter = prompts["final_book_format"]
    rendered = writer.format(raw_text="原始视频文稿")

    assert "@三个备选书名" in writer
    assert "@三个备选简介" in writer
    assert "@最终书名" not in writer
    assert "@最终简介" not in writer
    assert "导言必须放在目录之后、正文之前，长度 200~400 字" in writer
    assert "最终正文长度控制在约12000字，允许小幅浮动" in writer
    assert "原始视频文稿" in rendered

    assert "将我的文本重新表达为自然流畅的现代汉语" in polish
    assert "晚上十一点" in polish
    assert "凌晨两点" in polish
    assert "并不" in polish
    assert "真正的" in polish
    assert "看似……，却……" in polish
    assert "听起来似乎……，实际上……" in polish
    assert "不得把具体判断改成空话" in polish
    assert "在不删减内容的前提下重新分段，每段 50~100 字" in refine
    assert "晚上十一点" in refine
    assert "凌晨两点" in refine
    assert "并不" in refine
    assert "真正的" in refine
    assert "看似……，却……" in refine
    assert "听起来似乎……，实际上……" in refine
    assert "不得降低文稿质量" in refine
    assert "三个备选书名（小节标题：@三个备选书名）" in formatter
    assert "每个章节的标题统一格式为：！！！N. 章节名" in formatter
    assert "每一个标题文字前面加一个 ！！！ 符号作为居中标记" in formatter
    assert "正文只允许使用正式章节标题：@N. 章节名" not in formatter


def test_life_philosophy_self_rescue_keeps_dedicated_writer() -> None:
    prompt = get_prompts("life_philosophy_self_rescue")["full_book_writer"]

    assert "人生哲学（困境自救版）" in prompt
    assert "@最终书名" in prompt
    assert "@三个备选书名" not in prompt


def test_life_philosophy_prompt_avoids_fixed_scene_and_loop_formula() -> None:
    prompt = get_prompts("life_philosophy")["full_book_writer"]

    assert "场景切片" not in prompt
    assert "现实处境 → 常见反应 → 隐含误区 → 长期代价 → 替代认知 → 行动原则" not in prompt
    assert "不要机械套用同一条六步路径" in prompt
    assert "开篇不得总是写成“一个虚构人名遇到心理困难”" in prompt
    assert "章节开头和小节开头不要反复用具体钟点制造气氛" in prompt
    assert "晚上十一点" in prompt
    assert "凌晨两点" in prompt
    assert "不得固定放在每章倒数第二段" in prompt
    assert "不要统一写成“某某是有代价的”" in prompt
    assert "前者……，后者……" in prompt
    assert "看似……，却……" in prompt
    assert "听起来似乎……，实际上……" in prompt
    assert "控制“并不”“真正”“真正的”的密度" in prompt
    assert "回避上述词句时不能降低文稿质量" in prompt
    assert "不得出现中文逗号连用“，，”" in prompt


def test_life_philosophy_self_rescue_prompt_uses_anti_template_rules() -> None:
    prompt = get_prompts("life_philosophy_self_rescue")["full_book_writer"]

    assert "场景切片" not in prompt
    assert "现实处境 → 常见反应 → 隐含误区 → 长期消耗 → 重新理解 → 行动原则" not in prompt
    assert "人生哲学反模板约束" in prompt
    assert "生活场面不要只放在每篇开头和结尾" in prompt
    assert "开篇不得总是写成“一个虚构人名遇到心理困难”" in prompt
    assert "章节开头和小节开头不要反复用具体钟点制造气氛" in prompt
    assert "晚上十一点" in prompt
    assert "凌晨两点" in prompt
    assert "收束不要总是写成“你在一个平静时刻继续面对生活”" in prompt
    assert "不得固定放在每章倒数第二段" in prompt
    assert "避免在章节标题和正文里反复使用：自救、失控、消耗、成熟、成年人、审判、判决" in prompt
    assert "控制“并不”“真正”“真正的”的密度" in prompt
    assert "看似……，却……" in prompt
    assert "听起来似乎……，实际上……" in prompt
    assert "回避上述词句时不能降低文稿质量" in prompt
    assert "慢下来、边界、自责、责任、秩序、债务、陷阱、代价、某某瘾" in prompt


def test_longform_variant_categories_use_dedicated_prompts() -> None:
    cases = [
        (
            "social_science_core_conflict",
            "社会痛点（核心矛盾版）",
            "写作时先判断原文最值得展开的一个核心矛盾",
        ),
        (
            "life_philosophy_self_rescue",
            "人生哲学（困境自救版）",
            "一个人为什么会反复被同一种困境困住",
        ),
        (
            "popular_science_cognitive_upgrade",
            "科普读物（认知升级版）",
            "让读者产生“原来如此”“这件事和我有关”的阅读体验",
        ),
        (
            "wisdom_stories_causal_philosophy",
            "故事·智慧（因果哲思版）",
            "让读者从人物的选择和后果里，看见人生运行中的因果关系",
        ),
        (
            "wisdom_stories_historical",
            "故事·智慧类（历史故事版）",
            "一个人如何在具体时代里做出选择",
        ),
        (
            "emotion_relationship_unique_view",
            "心理情感（情感·关系）（独特观点版）",
            "让最终成稿与原视频文稿在结构、案例、表达和推进方式上都有明显区别",
        ),
        (
            "health_wellness_daily_pain",
            "健康养生（日常痛点版）",
            "必须单独列出一章，详细讲解什么状况需要寻求专业帮助",
        ),
        (
            CLASSICAL_REFERENCE_REWRITE_CATEGORY,
            "古文白话（参照译写版）",
            "如果任务包、笔锋参考或补充要求里包含别人已经翻译好的白话译文，只能作为释义参考",
        ),
    ]

    for category, label, needle in cases:
        meta = get_category_meta(category)
        prompt = get_prompts(category)["full_book_writer"]
        rendered = prompt.format(raw_text="原始视频文稿")

        assert meta["label"] == label
        assert label in prompt
        assert needle in prompt
        assert "{raw_text}" in prompt
        assert "原始视频文稿" in rendered
        assert "@最终书名" in prompt
        assert "@最终简介" in prompt
        assert "@目录" in prompt
        assert "@导言" in prompt
        assert "@正文" in prompt


def test_longform_variant_aliases_resolve() -> None:
    assert normalize_category("社会痛点核心矛盾版") == "social_science_core_conflict"
    assert normalize_category("人生哲学困境自救版") == "life_philosophy_self_rescue"
    assert normalize_category("科普读物认知升级版") == "popular_science_cognitive_upgrade"
    assert normalize_category("故事智慧因果哲思版") == "wisdom_stories_causal_philosophy"
    assert normalize_category("故事智慧类历史故事版") == "wisdom_stories_historical"
    assert normalize_category("故事·智慧类（历史故事版）") == "wisdom_stories_historical"
    assert normalize_category("情感关系独特观点版") == "emotion_relationship_unique_view"
    assert normalize_category("健康养生日常痛点版") == "health_wellness_daily_pain"
    assert normalize_category("古文白话（原文翻译穿插版）") == "classical_chinese_interleaved"
    assert normalize_category("古文白话小说风格版") == CLASSICAL_NOVEL_STYLE_CATEGORY
    assert normalize_category("古文白话（小说风格版）") == CLASSICAL_NOVEL_STYLE_CATEGORY
    assert normalize_category("古文白话参照译写版") == CLASSICAL_REFERENCE_REWRITE_CATEGORY
    assert normalize_category("古文白话（参照译写版）") == CLASSICAL_REFERENCE_REWRITE_CATEGORY
    assert normalize_category("古文白话抄袭版") == CLASSICAL_REFERENCE_REWRITE_CATEGORY
    assert normalize_category("佛经白话") == "buddhist_scripture_story"
    assert normalize_category("佛经白话故事版") == "buddhist_scripture_story"


def test_classical_novel_style_prompt_uses_novel_polish_rules() -> None:
    meta = get_category_meta(CLASSICAL_NOVEL_STYLE_CATEGORY)
    prompts = get_prompts(CLASSICAL_NOVEL_STYLE_CATEGORY)
    writer = prompts["full_book_writer"]
    polish = prompts["gemini_fast_polish"]
    refine = prompts["gemini_fast_refine"]
    formatter = prompts["final_book_format"]
    source_pack = build_classical_prompt_source(
        "其人夜入古寺，闻壁间有声。",
        variant=classical_variant_for_category(CLASSICAL_NOVEL_STYLE_CATEGORY),
        original_title="志怪录",
    )

    assert meta["label"] == "古文白话小说风格版"
    assert "纯白话故事化小说风格稿" in writer
    assert "小说风格版专属工作流" in writer
    assert "原文理解" in writer
    assert "故事化白话改写" in writer
    assert "小故事式白话叙事" in writer
    assert "抽象格言型" in writer
    assert "无名人物" in writer
    assert "故事载体" in writer
    assert "最终只输出纯白话成稿" in writer
    assert "小说风格版" in source_pack
    assert "不是把文言文逐句翻成大白话" in source_pack
    assert "忠于原文的小故事式白话叙事" in source_pack
    assert "小说风格版段落硬规则" in source_pack
    assert "可以把同一个 P 编号对应的白话内容拆成两个或多个自然段" in source_pack
    assert "抽象格言" in source_pack
    assert "不能只解释句义" in source_pack
    for stage_prompt in (polish, refine, formatter):
        assert "古文白话小说风格版锁定规则" in stage_prompt
        assert "剧情事实准确，不是要求逐字逐句对译" in stage_prompt
        assert "普通直译" in stage_prompt
        assert "抽象格言" in stage_prompt
        assert "无名人物" in stage_prompt


def test_classical_novel_style_segment_writer_uses_story_rewrite_mode() -> None:
    prompt = content_engine._classical_segment_writer_prompt(
        segment_text="其人夜入古寺，闻壁间有声。",
        all_lines=["其人夜入古寺，闻壁间有声。"],
        start_index=1,
        variant=classical_variant_for_category(CLASSICAL_NOVEL_STYLE_CATEGORY),
        original_title="志怪录",
    )

    assert "小故事式白话叙事" in prompt
    assert "不能只是逐字逐句翻译成大白话" in prompt
    assert "抽象格言" in prompt
    assert "无名人物和简短场景" in prompt
    assert "逐段完整白话化" not in prompt


def test_classical_novel_style_prompt_allows_abstract_maxim_story_carrier() -> None:
    source_pack = build_classical_prompt_source(
        "昨日之非不可留，留之则根烬复萌，而尘情终累乎理趣；今日之是不可执，执之则渣滓未化，而理趣反转为欲根。",
        variant=classical_variant_for_category(CLASSICAL_NOVEL_STYLE_CATEGORY),
        original_title="菜根谭",
    )
    prompts = get_prompts(CLASSICAL_NOVEL_STYLE_CATEGORY)

    assert "抽象格言" in source_pack
    assert "无名人物" in source_pack
    assert "普通处境" in source_pack
    assert "行动选择" in source_pack
    assert "心理转折" in source_pack
    assert "不得只写自然观点推进或生活化解释" in source_pack
    assert "抽象格言型原文必须用故事载体表达原文道理" in prompts["gemini_fast_polish"]


def test_classical_reference_rewrite_prompt_uses_safe_reference_rules() -> None:
    meta = get_category_meta(CLASSICAL_REFERENCE_REWRITE_CATEGORY)
    prompts = get_prompts(CLASSICAL_REFERENCE_REWRITE_CATEGORY)
    writer = prompts["full_book_writer"]
    polish = prompts["gemini_fast_polish"]
    refine = prompts["gemini_fast_refine"]
    formatter = prompts["final_book_format"]
    source_pack = build_classical_prompt_source(
        "子曰：学而时习之，不亦说乎。",
        variant=classical_variant_for_category(CLASSICAL_REFERENCE_REWRITE_CATEGORY),
        original_title="论语",
    )

    assert meta["label"] == "古文白话（参照译写版）"
    assert meta["core_feature"] == "参考已有译文的释义和术语，结合古汉语词典，重新写成更大白话一点的自然白话"
    assert "{raw_text}" in writer
    assert "原文翻译对照稿" in writer
    assert "若参考到已有译文，只能吸收释义判断、术语、断句和语气密度" in writer
    assert "最终正文只输出原文与翻译成对段落" in writer
    assert "原文一段、翻译一段" in writer
    assert "不再参考《菜根谭》类全注全译" in writer
    assert "参照译写安全硬规则" in writer
    assert "不得逐句模仿" in writer
    assert "权威参照译本和古汉语词典三方校准" in writer
    assert "联网检索专业网站" in writer
    assert "不得用百科摘抄、论坛、营销号" in writer
    assert "不能望文生义" in writer
    assert "现代汉语常见意思拆字" in writer
    assert "更大白话一点" in writer
    assert "精金美玉般纯正坚实的人品" in writer
    assert "如履薄冰”的艰险与戒慎" in writer
    assert "菜根谭-中华书局参照范本" in writer
    assert "便觉百行皆非" in writer
    assert "不得输出【注释】【译文】【点评】【原文定位】" in writer
    assert "知名译本参照" in source_pack
    assert "参考已有译本或白话稿时" in source_pack
    assert "最终输出形态只做原文与翻译对照" in source_pack
    assert "【P0001】（原文）原文内容" in source_pack
    assert "（翻译）现代白话译文" in source_pack
    assert "不得照搬、近似改写或按句同义替换参考译文" in source_pack
    assert "以该权威译本的断句、关键字词释义" in source_pack
    assert "联网检索专业网站做交叉验证" in source_pack
    assert "不得用百科摘抄、论坛、营销号" in source_pack
    assert "不得按现代汉语常见意思拆字理解原文" in source_pack
    assert "便觉百行皆非" in source_pack
    assert "【注释】【译文】【点评】必须单独成行" not in source_pack
    for stage_prompt in (polish, refine, formatter):
        assert "古文白话参照译写版语义锁" in stage_prompt
        assert "不得重新翻译古文" in stage_prompt
        assert "更大白话一点" in stage_prompt
        assert "便觉、始觉、方觉" in stage_prompt
        assert "语文教材式解释" in stage_prompt
        assert "中华书局或用户指定参考译文命中的义项" in stage_prompt


def test_classical_reference_rewrite_style_notes_append_to_segment_prompt() -> None:
    segment_prompt = content_engine._classical_segment_writer_prompt(
        segment_text="子曰：学而时习之，不亦说乎。",
        all_lines=["子曰：学而时习之，不亦说乎。"],
        start_index=1,
        variant=classical_variant_for_category(CLASSICAL_REFERENCE_REWRITE_CATEGORY),
        original_title="论语",
    )
    with_reference = content_engine._append_style_reference_prompt(
        segment_prompt,
        category=CLASSICAL_REFERENCE_REWRITE_CATEGORY,
        style_reference="某知名译本",
        style_notes="参考译文：孔子说，学习以后常常温习。",
    )

    assert "笔锋参考要求" in with_reference
    assert "古文白话参照译写版专用的参照译本输入项" in with_reference
    assert "参考对象：某知名译本" in with_reference
    assert "补充要求：参考译文：孔子说，学习以后常常温习。" in with_reference
    assert "校准释义、术语、断句、称谓、语气密度和关键字词准确性" in with_reference
    assert "菜根谭-中华书局参照范本" in with_reference
    assert "最终输出只允许每个原文段后紧跟一个翻译段" in with_reference
    assert "不要学习其注释、点评、章节讲解或排版体例" in with_reference
    assert "不得逐句复刻、同义替换、搬运参考译文" in with_reference
    assert "权威译本方向，再用联网专业资料和古汉语词典补缺" in with_reference
    assert "联网检索专业网站交叉验证" in with_reference
    assert "不得按现代汉语常见意思拆字理解原文" in with_reference
    assert "更大白话一点" in with_reference
    assert "精金美玉、烈火、如履薄冰" in with_reference
    assert "便觉、始觉、方觉" in with_reference


def test_classical_reference_rewrite_generation_chain_uses_word_prompt_layers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}
    final_text = """@最终书名
菜根谭（白话版）
@最终简介
测试简介。
@目录
1. 测试
@导言
测试导言。
@正文
（原文）欲做精金美玉的人品，定从烈火中煅来。

（翻译）一个人要想拥有真正过硬的好品格，就必须在现实的严酷考验中不断捶打自己。
"""

    async def fake_route_writer_call(prompt: str, **_: object) -> str:
        captured["writer"] = prompt
        return final_text

    async def fake_polish_chunks(text: str, **kwargs: object) -> str:
        captured["polish_text"] = text
        captured["polish_system"] = str(kwargs["system_instruction"])
        return text

    async def fake_refine_chunks(text: str, **kwargs: object) -> str:
        captured["refine_text"] = text
        captured["refine_system"] = str(kwargs["system_instruction"])
        return text

    def fake_format_book_text(text: str, **kwargs: object) -> str:
        captured["format_text"] = text
        captured["format_prompt"] = str(kwargs["prompt_template"])
        return text

    async def fake_repair_classical_quality_once(text: str, **_: object) -> str:
        return text

    async def fake_repair_final_hard_errors_once(text: str, **_: object) -> str:
        return text

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "_polish_by_paragraph_chunks_strict", fake_polish_chunks)
    monkeypatch.setattr(content_engine, "_refine_by_paragraph_chunks_strict", fake_refine_chunks)
    monkeypatch.setattr(content_engine, "format_book_text", fake_format_book_text)
    monkeypatch.setattr(content_engine, "_repair_classical_quality_once", fake_repair_classical_quality_once)
    monkeypatch.setattr(content_engine, "_repair_final_hard_errors_once", fake_repair_final_hard_errors_once)

    result = asyncio.run(
        content_engine.generate_full_book(
            "欲做精金美玉的人品，定从烈火中煅来。",
            category="古文白话参照译写版",
            output_dir=tmp_path / "classical-reference-chain",
            style_reference="中华书局参照译本",
            style_notes="参考译文：想要磨炼出如精金美玉般纯粹的人品，定要经过烈火般的煅烧。",
            classical_original_title="菜根谭",
        )
    )

    assert "菜根谭（白话版）" in result
    assert "联网检索专业网站做交叉验证" in captured["writer"]
    assert "不得用百科摘抄、论坛、营销号" in captured["writer"]
    assert "不得按现代汉语常见意思拆字理解原文" in captured["writer"]
    assert "参考对象：中华书局参照译本" in captured["writer"]
    assert "参考译文：想要磨炼出如精金美玉般纯粹的人品" in captured["writer"]

    assert "polish_system" not in captured
    assert "refine_system" not in captured
    assert "古文白话参照译写版语义锁" in captured["format_prompt"]
    assert "不得重新翻译古文" in captured["format_prompt"]
    assert "语文教材式解释" in captured["format_prompt"]
    assert "中华书局或用户指定参考译文命中的义项" in captured["format_prompt"]


@pytest.mark.parametrize(
    ("category", "original_title", "body"),
    [
        (
            CLASSICAL_NOVEL_STYLE_CATEGORY,
            "杨家将",
            "杨业披甲出帐，众将随他奔向城门。",
        ),
        (
            CLASSICAL_REFERENCE_REWRITE_CATEGORY,
            "菜根谭",
            "（原文）欲做精金美玉的人品，定从烈火中煅来。\n\n"
            "（翻译）想磨炼出精金美玉般坚实的人品，就要经得住烈火般的考验。",
        ),
    ],
)
def test_selected_classical_categories_skip_both_gemini_rewrite_stages(
    category: str,
    original_title: str,
    body: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manuscript = f"""@最终书名
{original_title}（白话版）
@最终简介
测试简介。
@目录
1. 第一章
@导言
测试导言。
@正文
{body}
"""
    captured: dict[str, str] = {}
    stages: list[str] = []

    async def fake_route_writer_call(prompt: str, **_: object) -> str:
        captured["writer_prompt"] = prompt
        return manuscript

    async def fail_polish(*_: object, **__: object) -> str:
        raise AssertionError("直出分类不得调用 Gemini 一润")

    async def fail_refine(*_: object, **__: object) -> str:
        raise AssertionError("直出分类不得调用 Gemini 二润")

    def fake_format_book_text(text: str, **_: object) -> str:
        captured["format_input"] = text
        return text

    async def keep_classical_text(text: str, **_: object) -> str:
        return text

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "_polish_by_paragraph_chunks_strict", fail_polish)
    monkeypatch.setattr(content_engine, "_refine_by_paragraph_chunks_strict", fail_refine)
    monkeypatch.setattr(content_engine, "format_book_text", fake_format_book_text)
    monkeypatch.setattr(content_engine, "_repair_classical_quality_once", keep_classical_text)
    monkeypatch.setattr(content_engine, "_repair_final_hard_errors_once", keep_classical_text)

    output_dir = tmp_path / category
    result = asyncio.run(
        content_engine.generate_full_book(
            "测试古文原文。",
            category=category,
            classical_original_title=original_title,
            output_dir=output_dir,
            on_stage=lambda stage, _: stages.append(stage),
        )
    )

    assert original_title in result
    assert captured["format_input"] == manuscript
    assert (output_dir / "01_writer.md").exists()
    assert not (output_dir / "02_polish.md").exists()
    assert not (output_dir / "03_refine.md").exists()
    assert (output_dir / "04_format.md").exists()
    assert not any(stage.startswith("polish") for stage in stages)
    assert not any(stage.startswith("refine") for stage in stages)


def test_classical_reference_notes_match_current_source_instead_of_head_tail() -> None:
    notes = "\n".join(
        [
            "前言",
            "这是一段很长但不相关的参考说明。" * 80,
            "一",
            "欲做精金美玉的人品①，定从烈火中煅来②；思立掀天揭地的事功③，须向薄冰上履过④。",
            "【注释】",
            "①精金美玉：比喻纯洁完美的人或事物。",
            "②煅：锻炼，烧制。",
            "【译文】",
            "若想成就精金美玉般完美无瑕的人格品行，一定要经受烈火煅烧般的历练。",
            "【点评】",
            "这一段点评不应该被召回给模型学习排版体例。",
            "二",
            "完全无关的后续条目。",
        ]
    )

    excerpt = content_engine._style_reference_notes_for_classical_segment(  # noqa: SLF001
        notes,
        start_index=1,
        end_index=1,
        total_source_lines=1,
        source_segment_text="欲做精金美玉的人品，定从烈火中煅来；思立掀天揭地的事功，须向薄冰上履过。",
    )

    assert "当前原文对应参考译文召回" in excerpt
    assert "以这些片段的释义和断句为第一依据" in excerpt
    assert "权威释义定准后的大白话复写" in excerpt
    assert "精金美玉" in excerpt
    assert "完美无瑕的人格品行" in excerpt
    assert "这是一段很长但不相关的参考说明" not in excerpt
    assert "这一段点评不应该被召回" not in excerpt


def test_finance_technology_category_uses_dedicated_prompt() -> None:
    meta = get_category_meta("finance_technology")
    prompt = get_prompts("finance_technology")["full_book_writer"]
    rendered = prompt.format(raw_text="测试参考资料")

    assert meta["label"] == "财经·科技"
    assert "直接生成一篇完整的深度长文" in prompt
    assert "只能作为“参照材料”，不能作为逐段改写的底稿" in prompt
    assert "不得沿用原文的开篇方式、段落顺序、案例组合、论证路径、表达节奏和关键句式" in prompt
    assert "抓住1个主矛盾和2—4个核心问题" in prompt
    assert "不要做具体投资预测" in prompt
    assert "如果美元走弱" in prompt
    assert "每个重要概念、事件、政策、技术、公司动作或市场变化" in prompt
    assert "正文字数不设人为上限" in prompt
    assert "最终正文长度控制在约12000字" not in prompt
    assert "财经科技来源表述专项要求" in prompt
    assert "财经科技联网检索与最新例子硬性要求" in prompt
    assert "必须调用 API 联网检索" in prompt
    assert "不要退而求其次使用 2025 年、2024 年、2023 年或更早案例" in prompt
    assert "视频材料中提到" in prompt
    assert "根据《来源名》" in prompt
    assert "根据公开资料" in prompt
    assert "[参考资料全文]" in prompt
    assert "[原始视频文稿]" not in prompt
    assert "【小节标题全局要求】" in prompt
    assert "【导言与正文分工全局要求】" in prompt
    assert "测试参考资料" in rendered


def test_finance_technology_advice_category_is_removed() -> None:
    assert "finance_technology_advice" not in SUPPORTED_CATEGORIES
    assert all(item["id"] != "finance_technology_advice" for item in list_categories())
    with pytest.raises(ValueError):
        normalize_category("finance_technology_advice")
    with pytest.raises(ValueError):
        normalize_category("财经科技（观点建议版）")


def test_finance_technology_recommendation_category_uses_suggestion_prompt() -> None:
    meta = get_category_meta("finance_technology_recommendation")
    prompt = get_prompts("finance_technology_recommendation")["full_book_writer"]
    rendered = prompt.format(raw_text="建议版测试资料")

    assert meta["label"] == "财经科技（建议版）"
    assert "财经科技来源表述专项要求" not in prompt
    assert "财经科技联网检索与最新例子硬性要求" not in prompt
    assert "你需要先从参照材料中提炼出最值得大众关心的核心问题、现实矛盾和基本判断。" in prompt
    assert "财经科技（建议版）的本段专属要求优先级最高" in prompt
    assert "这是建议版，不是资料汇编版" in prompt
    assert "不得作为可铺陈、可罗列、可反复展开的材料库" in prompt
    assert "每条事实、数据、案例、公司动作或政策背景，只能在最能支撑核心判断的位置展开一次" in prompt
    assert "凡话题与普通人的生活、工作、消费、资产、职业或信息判断有关，必须设置一个独立小节" in prompt
    assert "具体、可执行、有适用条件的建议" in prompt
    assert "最终正文长度控制在约12000字" in prompt
    assert "约12000字是明确写作目标" in prompt
    assert "不得为了接近字数反复堆材料" in prompt
    assert "正文字数不设人为上限" not in prompt
    assert "[参考资料全文]" in prompt
    assert "建议版测试资料" in rendered


def test_finance_technology_aliases_resolve() -> None:
    assert normalize_category("财经") == "finance_technology"
    assert normalize_category("财经科技") == "finance_technology"
    assert normalize_category("财经·科技") == "finance_technology"
    assert normalize_category("finance_tech") == "finance_technology"
    assert normalize_category("财经科技（建议版）") == "finance_technology_recommendation"
    assert normalize_category("财经科技建议版") == "finance_technology_recommendation"
    assert normalize_category("财经·科技（建议版）") == "finance_technology_recommendation"
    assert normalize_category("finance_tech_recommendation") == "finance_technology_recommendation"


def test_finance_dynamic_target_words_by_duration() -> None:
    assert 17_000 <= content_engine._finance_dynamic_target_words(
        "财经科技测试文稿",
        source_duration_seconds=10 * 60,
    ) <= 18_000
    assert content_engine._finance_dynamic_target_words(
        "财经科技测试文稿",
        source_duration_seconds=20 * 60,
    ) >= 35_000
    assert content_engine._finance_dynamic_target_words(
        "财经科技测试文稿",
        source_duration_seconds=30 * 60,
    ) == 50_000
    assert content_engine._finance_dynamic_target_words(
        "财经科技测试文稿",
        source_duration_seconds=45 * 60,
    ) == 50_000
    assert content_engine._finance_dynamic_target_words(
        "财经科技测试文稿",
        source_duration_seconds=90 * 60,
    ) == 75_000
    assert content_engine._finance_dynamic_target_words("财" * 8400) == 50_000


def test_finance_technology_category_uses_finance_workflow() -> None:
    assert content_engine._should_use_finance_longform(
        "finance_technology",
        "很短的财经科技资料。",
        source_duration_seconds=60,
    )


def test_finance_recommendation_category_does_not_use_finance_longform_workflow() -> None:
    assert not content_engine._should_use_finance_longform(
        "finance_technology_recommendation",
        "很短的财经科技建议版资料。",
        source_duration_seconds=60,
    )
    assert "finance_technology_recommendation" not in content_engine.FINANCE_LONGFORM_CATEGORIES


def test_finance_recommendation_generation_uses_standard_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer_prompts: list[str] = []
    polish_instructions: list[str] = []
    refine_instructions: list[str] = []
    format_templates: list[str] = []

    async def fail_finance_longform(*_: object, **__: object) -> str:
        raise AssertionError("finance recommendation must not use finance longform workflow")

    async def fail_latest_research_pack(*_: object, **__: object) -> str:
        raise AssertionError("finance recommendation must not call web search")

    async def fake_route_writer_call(prompt: str, **_: object) -> str:
        writer_prompts.append(prompt)
        return MINIMAL_MANUSCRIPT

    async def fake_polish_chunks(text: str, **kwargs: object) -> str:
        polish_instructions.append(str(kwargs.get("system_instruction") or ""))
        return text

    def fake_refine_book_text(text: str, **kwargs: object) -> str:
        refine_instructions.append(str(kwargs.get("system_instruction") or ""))
        return text

    def fake_format_book_text(text: str, **kwargs: object) -> str:
        format_templates.append(str(kwargs.get("prompt_template") or ""))
        return text

    monkeypatch.setattr(content_engine, "_generate_finance_longform_book", fail_finance_longform)
    monkeypatch.setattr(content_engine, "_finance_latest_research_pack", fail_latest_research_pack)
    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "_polish_by_paragraph_chunks_strict", fake_polish_chunks)
    monkeypatch.setattr(content_engine, "refine_book_text", fake_refine_book_text)
    monkeypatch.setattr(content_engine, "format_book_text", fake_format_book_text)

    result = asyncio.run(
        content_engine.generate_full_book(
            "财经科技建议版资料。",
            category="finance_technology_recommendation",
            output_dir=tmp_path / "finance_recommendation_standard",
            source_duration_seconds=30 * 60,
        )
    )

    assert "@最终书名" in result
    assert len(writer_prompts) == 1
    assert "财经科技观点建议版密度强约束" not in writer_prompts[0]
    assert "财经科技长视频动态长文强约束" not in writer_prompts[0]
    assert "财经科技建议版联网事实资料" not in writer_prompts[0]
    assert "财经科技联网检索与最新例子硬性要求" not in writer_prompts[0]
    assert "找到 2026 年当前例子" not in writer_prompts[0]
    assert "联网检索结果只能作为论据，不作为材料库" not in writer_prompts[0]
    assert "财经科技（建议版）的本段专属要求优先级最高" in writer_prompts[0]
    assert "这是建议版，不是资料汇编版" in writer_prompts[0]
    assert "只能在最能支撑核心判断的位置展开一次" in writer_prompts[0]
    assert "建议小节只能把前文判断转成行动边界" in writer_prompts[0]
    assert "最终正文长度控制在约12000字" in writer_prompts[0]
    assert "约12000字是明确写作目标" in writer_prompts[0]
    assert "不得为了接近字数反复堆材料" in writer_prompts[0]
    assert polish_instructions and "财经科技超长文整稿处理保护" not in polish_instructions[0]
    assert refine_instructions and "财经科技超长文整稿处理保护" not in refine_instructions[0]
    assert format_templates and "财经科技超长文整稿格式保护" not in format_templates[0]


def test_finance_recommendation_web_search_pack_is_disabled(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="财经科技（建议版）已关闭联网检索"):
        asyncio.run(
            content_engine._finance_latest_research_pack(
                "财经科技建议版资料。",
                category="finance_technology_recommendation",
                output_dir=tmp_path / "finance_recommendation_no_web",
            )
        )


def test_finance_recommendation_resume_reuses_writer_without_web_search(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "finance_recommendation_resume"
    output_dir.mkdir(parents=True)
    (output_dir / "01_writer.md").write_text(MINIMAL_MANUSCRIPT, encoding="utf-8")

    async def fail_latest_research_pack(*_: object, **__: object) -> str:
        raise AssertionError("resume with existing writer must not call finance web search again")

    async def fake_polish_chunks(text: str, **_: object) -> str:
        return text

    monkeypatch.setattr(content_engine, "_finance_latest_research_pack", fail_latest_research_pack)
    monkeypatch.setattr(content_engine, "_polish_by_paragraph_chunks_strict", fake_polish_chunks)
    monkeypatch.setattr(content_engine, "refine_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "format_book_text", lambda text, **_: text)

    result = asyncio.run(
        content_engine.generate_full_book(
            "财经科技建议版资料。",
            category="finance_technology_recommendation",
            output_dir=output_dir,
        )
    )

    assert "@最终书名" in result


def _long_finance_manuscript(repeat: int = 5200) -> str:
    body = "财经科技影响链条需要回到视频信息点、产业条件、政策约束和普通人的现实选择中展开。" * repeat
    return MINIMAL_MANUSCRIPT.replace("测试正文。", body)


def _finance_continuation_fragment(repeat: int = 1800) -> str:
    body = "这一层需要补足产业链、公司行为、政策条件、资金周期、普通人影响和风险边界之间的传导关系。" * repeat
    return f"@1. 全局传导\n{body}"


def _patch_finance_latest_research(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_latest_research_pack(*_: object, **__: object) -> str:
        return (
            "【财经科技联网检索结果】\n"
            "- 检索结论：已检索到 2026 年当前最新例子；未检索到的主题不得使用过去几年案例替代。\n"
            "- 可使用的最新例子：\n"
            "  1. 2026-06｜公开资料｜测试公司｜测试公司发布当前产业动作｜测试主题｜只能用于当前例子。\n"
            "- 禁写提醒：没有 2026 年最新例子的主题不要写最新例子。"
        )

    monkeypatch.setattr(content_engine, "_finance_latest_research_pack", fake_latest_research_pack)


def test_finance_web_search_call_requires_required_tool_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class FakeResponses:
        def create(self, **kwargs: object) -> dict[str, str]:
            calls.append(dict(kwargs))
            return {"output_text": "【财经科技联网检索结果】\n- 检索结论：测试结果。"}

    class FakeClient:
        responses = FakeResponses()

    monkeypatch.setattr(content_engine, "_resolve_openai_api_key", lambda: "sk-test")
    monkeypatch.setattr(content_engine, "_get_openai_writer_client", lambda: FakeClient())

    result = content_engine._openai_finance_web_search_call("检索提示")

    assert "测试结果" in result
    assert calls
    assert calls[0]["tool_choice"] == "required"
    assert calls[0]["tools"] == [{"type": "web_search", "search_context_size": "medium"}]


def test_finance_latest_research_pack_uses_cache_without_api_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "finance_research_cache"
    output_dir.mkdir()
    cached = "【财经科技联网检索结果】\n- 检索结论：缓存结果，包含 2026 年最新例子。"
    (output_dir / content_engine.FINANCE_LATEST_RESEARCH_STAGE_FILE).write_text(
        cached,
        encoding="utf-8",
    )

    def fail_web_search(_: str) -> str:
        raise AssertionError("cached finance research must not call API again")

    monkeypatch.setattr(content_engine, "_openai_finance_web_search_call", fail_web_search)

    result = asyncio.run(
        content_engine._finance_latest_research_pack(
            "财经科技资料",
            category="finance_technology",
            output_dir=output_dir,
        )
    )

    assert result == cached


def test_content_engine_prompt_cache_key_is_stable_and_input_sensitive() -> None:
    key1 = content_engine._content_prompt_cache_key(  # noqa: SLF001
        "social_science",
        "same source",
        input_source=None,
        classical_variant=None,
    )
    key2 = content_engine._content_prompt_cache_key(  # noqa: SLF001
        "social_science",
        "same source",
        input_source=None,
        classical_variant=None,
    )
    key3 = content_engine._content_prompt_cache_key(  # noqa: SLF001
        "social_science",
        "different source",
        input_source=None,
        classical_variant=None,
    )
    key4 = content_engine._content_prompt_cache_key(  # noqa: SLF001
        "classical_chinese",
        "same source",
        input_source=None,
        classical_variant="novel_style",
    )

    assert key1 == key2
    assert key1 != key3
    assert key1 != key4
    assert key1.startswith("ce:social_science:")


def test_content_engine_openai_writer_paths_thread_prompt_cache_key() -> None:
    source = "\n".join(
        inspect.getsource(func)
        for func in [
            content_engine.generate_full_book,
            content_engine._generate_finance_longform_book,  # noqa: SLF001
            content_engine._finance_expand_writer_to_target,  # noqa: SLF001
            content_engine._generate_classical_segmented_writer,  # noqa: SLF001
            content_engine._generate_buddhist_story_unit_writer,  # noqa: SLF001
            content_engine._repair_final_hard_errors_once,  # noqa: SLF001
            content_engine._repair_front_matter,  # noqa: SLF001
        ]
    )

    assert "task_prompt_cache_key = _content_prompt_cache_key" in source
    assert source.count("prompt_cache_key=task_prompt_cache_key") >= 12
    assert source.count("prompt_cache_key=prompt_cache_key") >= 10


def test_finance_longform_uses_single_whole_book_chain_without_output_caps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_text = "视频提到芯片价格、云厂商资本开支、政策约束和普通人的设备成本变化。"
    writer_prompts: list[str] = []
    writer_kwargs: list[dict[str, object]] = []
    writer_output = _long_finance_manuscript()
    polish_inputs: list[str] = []
    polish_kwargs: list[dict[str, object]] = []
    refine_inputs: list[str] = []
    refine_kwargs: list[dict[str, object]] = []
    format_inputs: list[str] = []
    format_kwargs: list[dict[str, object]] = []

    async def fake_route_writer_call(prompt: str, **kwargs: object) -> str:
        writer_prompts.append(prompt)
        writer_kwargs.append(dict(kwargs))
        return writer_output

    def fake_polish_book_text(text: str, **kwargs: object) -> str:
        polish_inputs.append(text)
        polish_kwargs.append(dict(kwargs))
        return text

    def fake_refine_book_text(text: str, **kwargs: object) -> str:
        refine_inputs.append(text)
        refine_kwargs.append(dict(kwargs))
        return text

    def fake_format_book_text(text: str, **kwargs: object) -> str:
        format_inputs.append(text)
        format_kwargs.append(dict(kwargs))
        return text

    async def fail_chunked_gemini(*_: object, **__: object) -> str:
        raise AssertionError("finance longform must not use chunked Gemini path")

    _patch_finance_latest_research(monkeypatch)
    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "polish_book_text", fake_polish_book_text)
    monkeypatch.setattr(content_engine, "refine_book_text", fake_refine_book_text)
    monkeypatch.setattr(content_engine, "format_book_text", fake_format_book_text)
    monkeypatch.setattr(content_engine, "_polish_by_paragraph_chunks_strict", fail_chunked_gemini)

    result = asyncio.run(
        content_engine.generate_full_book(
            raw_text,
            category="finance_technology",
            output_dir=tmp_path / "finance_longform",
            source_duration_seconds=30 * 60,
        )
    )

    assert "@最终书名" in result
    assert len(writer_prompts) == 1
    assert "财经科技长视频动态长文强约束" in writer_prompts[0]
    assert "一次性输出完整书稿" in writer_prompts[0]
    assert "不要分章分批生成" in writer_prompts[0]
    assert "财经科技来源表述专项要求" in writer_prompts[0]
    assert "财经科技联网检索与最新例子硬性要求" in writer_prompts[0]
    assert "已检索到 2026 年当前最新例子" in writer_prompts[0]
    assert "参考资料信息点观察清单" in writer_prompts[0]
    assert "必须按参考资料中的事实与信息点展开" in writer_prompts[0]
    assert "视频信息点观察清单" not in writer_prompts[0]
    assert "禁止重复观点" in writer_prompts[0]
    assert "禁止为了凑字数堆无关信息" in writer_prompts[0]
    expected_writer_output = content_engine.normalize_final_markdown(
        writer_output,
        category="finance_technology",
    )
    assert polish_inputs[0] == expected_writer_output
    assert refine_inputs[0] == expected_writer_output
    assert format_inputs[0] == expected_writer_output
    assert writer_kwargs[0]["max_output_tokens"] == 0
    assert writer_kwargs[0]["retry_on_failure"] is False
    assert polish_kwargs[0]["disable_max_output_tokens"] is True
    assert polish_kwargs[0]["retries"] == 1
    assert "财经科技超长文整稿处理保护" in str(polish_kwargs[0]["system_instruction"])
    assert "财经科技来源表述专项要求" in str(polish_kwargs[0]["system_instruction"])
    assert "财经科技联网检索与最新例子硬性要求" in str(polish_kwargs[0]["system_instruction"])
    assert "不能只处理开头、前半部分或前 20000 字" in str(polish_kwargs[0]["system_instruction"])
    assert refine_kwargs[0]["disable_max_output_tokens"] is True
    assert refine_kwargs[0]["retries"] == 1
    assert "财经科技超长文整稿处理保护" in str(refine_kwargs[0]["system_instruction"])
    assert "财经科技来源表述专项要求" in str(refine_kwargs[0]["system_instruction"])
    assert "财经科技联网检索与最新例子硬性要求" in str(refine_kwargs[0]["system_instruction"])
    assert "不能只处理开头、前半部分或前 20000 字" in str(refine_kwargs[0]["system_instruction"])
    assert format_kwargs[0]["max_output_tokens"] == 0
    assert format_kwargs[0]["fail_on_max_tokens"] is True
    assert format_kwargs[0]["retries"] == 1
    assert "财经科技超长文整稿格式保护" in str(format_kwargs[0]["prompt_template"])
    assert "财经科技来源表述专项要求" in str(format_kwargs[0]["prompt_template"])
    assert "财经科技联网检索与最新例子硬性要求" in str(format_kwargs[0]["prompt_template"])
    assert "不能只输出前半部分或前 20000 字" in str(format_kwargs[0]["prompt_template"])


def test_finance_longform_short_writer_uses_targeted_continuation_not_redraft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer_prompts: list[str] = []
    writer_kwargs: list[dict[str, object]] = []

    async def fake_route_writer_call(prompt: str, **kwargs: object) -> str:
        writer_prompts.append(prompt)
        writer_kwargs.append(dict(kwargs))
        if len(writer_prompts) == 1:
            return MINIMAL_MANUSCRIPT
        return _finance_continuation_fragment()

    _patch_finance_latest_research(monkeypatch)
    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "polish_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "refine_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "format_book_text", lambda text, **_: text)

    result = asyncio.run(
        content_engine.generate_full_book(
            "视频讲到算力价格、AI 服务器供应链、政策条件和企业投入节奏。",
            category="finance_technology",
            output_dir=tmp_path / "finance_completion",
            source_duration_seconds=30 * 60,
        )
    )

    assert "@最终书名" in result
    assert len(writer_prompts) == 2
    assert "财经科技长视频动态长文强约束" in writer_prompts[0]
    assert "财经科技长文同阶段定向续写任务" in writer_prompts[1]
    assert "财经科技来源表述专项要求" in writer_prompts[1]
    assert "财经科技联网检索与最新例子硬性要求" in writer_prompts[1]
    assert "已检索到 2026 年当前最新例子" in writer_prompts[1]
    assert "全局扩写蓝图" in writer_prompts[1]
    assert "只输出“新增正文正式章节”" in writer_prompts[1]
    assert "[参考资料全文]" in writer_prompts[1]
    assert "[参考资料信息点观察清单]" in writer_prompts[1]
    assert "[原始视频文稿]" not in writer_prompts[1]
    assert "财经科技长文完整补足任务" not in "\n".join(writer_prompts)
    assert all(kwargs["max_output_tokens"] == 0 for kwargs in writer_kwargs)
    assert all(kwargs["retry_on_failure"] is False for kwargs in writer_kwargs)
    assert content_engine._finance_body_chars(result) >= 50_000
    assert "@目录" in result
    assert "2. 全局传导" in result


def test_finance_longform_deepseek_compression_keeps_refined_full_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_manuscript = _long_finance_manuscript()

    async def fake_route_writer_call(prompt: str, **kwargs: object) -> str:
        return long_manuscript

    def fake_format_book_text(text: str, **kwargs: object) -> str:
        return MINIMAL_MANUSCRIPT

    _patch_finance_latest_research(monkeypatch)
    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "polish_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "refine_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "format_book_text", fake_format_book_text)

    result = asyncio.run(
        content_engine.generate_full_book(
            "视频讲到算力价格、AI 服务器供应链、政策条件和企业投入节奏。",
            category="finance_technology",
            output_dir=tmp_path / "finance_deepseek_compression",
            source_duration_seconds=30 * 60,
        )
    )

    assert content_engine._finance_body_chars(result) == content_engine._finance_body_chars(long_manuscript)
    assert (tmp_path / "finance_deepseek_compression" / "04_format_rejected_compressed.md").exists()


def test_finance_openai_writer_failure_is_non_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_route_writer_call(prompt: str, **kwargs: object) -> str:
        raise RuntimeError("connection reset")

    _patch_finance_latest_research(monkeypatch)
    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)

    with pytest.raises(RuntimeError, match="automatic retry stopped to avoid duplicate billing"):
        asyncio.run(
            content_engine.generate_full_book(
                "视频讲到算力价格、AI 服务器供应链、政策条件和企业投入节奏。",
                category="finance_technology",
                output_dir=tmp_path / "finance_writer_failure",
                source_duration_seconds=30 * 60,
            )
        )


def test_finance_short_final_resume_is_not_reused_before_writer_expansion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "finance_stale_resume"
    output_dir.mkdir()
    (output_dir / "01_writer.md").write_text(MINIMAL_MANUSCRIPT, encoding="utf-8")
    (output_dir / "04_format.md").write_text(MINIMAL_MANUSCRIPT, encoding="utf-8")
    writer_prompts: list[str] = []
    polish_inputs: list[str] = []
    format_inputs: list[str] = []

    async def fake_route_writer_call(prompt: str, **kwargs: object) -> str:
        writer_prompts.append(prompt)
        return _finance_continuation_fragment()

    def fake_polish_book_text(text: str, **kwargs: object) -> str:
        polish_inputs.append(text)
        return text

    def fake_format_book_text(text: str, **kwargs: object) -> str:
        format_inputs.append(text)
        return text

    _patch_finance_latest_research(monkeypatch)
    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "polish_book_text", fake_polish_book_text)
    monkeypatch.setattr(content_engine, "refine_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "format_book_text", fake_format_book_text)

    result = asyncio.run(
        content_engine.generate_full_book(
            "视频讲到算力价格、AI 服务器供应链、政策条件和企业投入节奏。",
            category="finance_technology",
            output_dir=output_dir,
            source_duration_seconds=30 * 60,
        )
    )

    assert writer_prompts
    assert "财经科技长文同阶段定向续写任务" in writer_prompts[0]
    assert content_engine._finance_body_chars(result) >= 50_000
    assert polish_inputs and format_inputs
    assert (output_dir / "04_format_stale_short.md").exists()


def test_writer_prompts_include_punctuation_usage_rules() -> None:
    prompt = get_prompts("social_science")["full_book_writer"]

    assert "【标点符号专项要求】" in prompt
    assert "句号只用于完整意思已经结束的位置" in prompt
    assert "逗号用于句内一般停顿" in prompt
    assert "顿号用于短并列词语" in prompt
    assert "分号用于较长并列分句" in prompt
    assert "省略号统一使用中文六点“……”" in prompt
    assert "不要混用中英文标点" in prompt


def test_all_category_generation_prompts_include_section_title_rules() -> None:
    for category in SUPPORTED_CATEGORIES:
        prompts = get_prompts(category)

        assert SECTION_TITLE_REQUIREMENT_PROMPT in prompts["full_book_writer"]
        assert SECTION_TITLE_REQUIREMENT_PROMPT in prompts["gemini_fast_polish"]
        assert SECTION_TITLE_REFINE_GUARD_PROMPT in prompts["gemini_fast_refine"]
        if prompts["final_book_format"]:
            if category == "life_philosophy":
                assert "每个章节的标题统一格式为：！！！N. 章节名" in prompts["final_book_format"]
                assert "不得改写、增删、合并、拆分、改序、重述任何正文内容" in prompts["final_book_format"]
                continue
            assert SECTION_TITLE_REQUIREMENT_PROMPT in prompts["final_book_format"]
            assert prompts["final_book_format"].find(SECTION_TITLE_REQUIREMENT_PROMPT) < prompts[
                "final_book_format"
            ].find("{text}")
            assert "`###短标题`" in prompts["final_book_format"]
            assert "不得出现“短标题，正文”" in prompts["final_book_format"]
            assert "正文内小节标题，标题正文必须限制" not in prompts["final_book_format"]


def test_section_title_rules_forbid_body_subheadings() -> None:
    prompt = get_prompts("social_science")["full_book_writer"]

    assert "正文正式章节标题只能使用" in prompt
    assert "`###短标题`" in prompt
    assert "不得写成“短标题，正文”" in prompt


def test_all_category_generation_prompts_include_intro_body_separation_rules() -> None:
    for category in SUPPORTED_CATEGORIES:
        prompts = get_prompts(category)

        assert INTRO_BODY_SEPARATION_PROMPT in prompts["full_book_writer"]
        assert INTRO_BODY_REFINE_GUARD_PROMPT in prompts["gemini_fast_polish"]
        assert INTRO_BODY_REFINE_GUARD_PROMPT in prompts["gemini_fast_refine"]
        if prompts["final_book_format"]:
            assert INTRO_BODY_SEPARATION_PROMPT in prompts["final_book_format"]
            assert prompts["final_book_format"].find(INTRO_BODY_SEPARATION_PROMPT) < prompts[
                "final_book_format"
            ].find("{text}")


def test_all_category_gemini_polish_includes_fixed_phrase_guard_only_once() -> None:
    for category in SUPPORTED_CATEGORIES:
        prompts = get_prompts(category)

        assert POLISH_FIXED_PHRASE_GUARD_PROMPT in prompts["gemini_fast_polish"]
        assert prompts["gemini_fast_polish"].count("【一润固定化表述删改要求】") == 1
        assert "很硬的" in prompts["gemini_fast_polish"]
        assert "……的边界" in prompts["gemini_fast_polish"]
        assert "并不神秘" in prompts["gemini_fast_polish"]
        assert POLISH_FIXED_PHRASE_GUARD_PROMPT not in prompts["full_book_writer"]
        assert POLISH_FIXED_PHRASE_GUARD_PROMPT not in prompts["gemini_fast_refine"]
        assert POLISH_FIXED_PHRASE_GUARD_PROMPT not in prompts["final_book_format"]


def test_front_matter_repair_prompt_includes_section_title_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repair_prompts: list[str] = []

    async def fake_route_writer_call(prompt: str, **_: object) -> str:
        repair_prompts.append(prompt)
        return MINIMAL_MANUSCRIPT

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)

    asyncio.run(content_engine._repair_front_matter("原始文稿", "缺失前置块"))

    assert repair_prompts
    assert SECTION_TITLE_REQUIREMENT_PROMPT in repair_prompts[0]
    assert INTRO_BODY_SEPARATION_PROMPT in repair_prompts[0]


def test_crayon_illustration_is_not_a_body_writing_category() -> None:
    assert "illustration" not in SUPPORTED_CATEGORIES
    assert "illustration_crayon" not in SUPPORTED_CATEGORIES
    with pytest.raises(ValueError):
        get_category_meta("illustration")
    with pytest.raises(ValueError):
        get_category_meta("illustration_crayon")


def test_classical_chinese_has_dedicated_translation_direction() -> None:
    prompt = get_prompts("classical_chinese")["full_book_writer"]
    task_source = build_classical_prompt_source(
        "子曰：学而时习之。",
        variant="zhiguai_web",
        original_title="论语",
    )
    rendered = prompt.format(raw_text=task_source)

    assert "古文白话大板块" in rendered
    assert "最终书名必须逐字使用：论语（白话版）" in rendered
    assert "上方通用书名规则在本题材中不适用" in rendered
    assert "适合在微信读书投放，文学性强一点" in rendered
    assert "不要为了阅读节奏强制分段" in rendered
    assert "不要输出只有空格或单独标点的空段" in rendered
    assert "正文标点必须使用中文全角标点" in rendered
    assert "【P0001】" in rendered
    assert "原文结构清单" in rendered
    assert "不得删除、改写、跳号、重号或调换顺序" in rendered
    assert "婢子不宜好相识" in rendered
    assert "双卫" in rendered
    assert "不得译成侍卫、保镖或护卫" in rendered
    assert "形似神似，活灵活现" in rendered
    assert "不要脑补剧情，不要跳剧情，不要自己加开头和结尾" in rendered
    assert "志怪小说爽读版" in rendered
    assert "以剧情和人物对话为核心" in rendered
    assert "【古汉语词典摘录】" in rendered
    assert get_classical_variant_meta("武侠小说爽读版")["id"] == "wuxia_web"


def test_buddhist_scripture_story_uses_dedicated_story_prompt() -> None:
    meta = get_category_meta("buddhist_scripture_story")
    prompts = get_prompts("buddhist_scripture_story")
    prompt = prompts["full_book_writer"]
    task_source = build_buddhist_scripture_story_source(
        "如是我闻。一时佛在舍卫国祇树给孤独园，与大比丘众俱。",
        original_title="金刚经",
    )
    rendered = prompt.format(raw_text=task_source)

    assert meta["label"] == "佛经白话"
    assert "最终书名必须逐字使用：金刚经（白话版）" in rendered
    assert "佛经小故事改写作者" in rendered
    assert "不是翻译佛经，也不是写佛经讲义" in rendered
    assert "先把佛经原文里的“事件轴、观照过程、问答关系、说法推进、譬喻和收束”提炼出来" in rendered
    assert "成稿必须像小故事：有开端，有推进，有转折或层层深入，有收束" in rendered
    assert "不是逐段直译" in rendered
    assert "不要按原文段落一一对应输出" in rendered
    assert "不要保留 P 编号" in rendered
    assert "不要写成“原文一句、翻译一句”的资料稿" in rendered
    assert "故事感必须来自原文已有的问答、动作、譬喻、说法顺序和人物关系" in rendered
    assert "第一章必须从原文已有的动作、观照、提问或说法关系进入" in rendered
    assert "长佛经必须覆盖全本，不能只写一个总故事、总论或代表性段落" in rendered
    assert "按原文顺序拆成多个故事单元/说法单元" in rendered
    assert "一个原文故事单元只能对应一个正文正式章节" in rendered
    assert "不得把同一则因缘、譬喻或故事拆成“故事章”和“道理章”两章" in rendered
    assert "《百喻经》全本是九十八则譬喻" in rendered
    assert "不得扩成一百九十多章、二百章或二百零四章" in rendered
    assert "不得生成只有一句道理、几句话评价或寥寥数字的空章节" in rendered
    assert "长文本的章节数量不设固定上限" in rendered
    assert "目录要体现原文从头到尾的故事单元顺序" in rendered
    assert "目录和正文的正式章节数量必须以原文故事单元数为准" in rendered
    assert "长佛经按全本顺序拆成足够多的故事单元/说法单元，不设固定章节上限" in rendered
    assert "长佛经不能把一个故事单元拆成“故事章+寓意章”" in rendered
    assert "正确方向是“观自在菩萨深行般若时，先照见五蕴皆空" in rendered
    assert "错误方向是“逐句解释五蕴、色空、无明、老死分别是什么意思”" in rendered
    assert "改动幅度不要太大" in rendered
    assert "不要编造原文没有的人物、地点、事件、对白、神通、因果细节、结局或教义解释" in rendered
    assert "白话正文必须按原文段落一一对应输出" not in rendered
    assert "【P0001】" not in rendered
    assert "原文结构清单" not in rendered
    assert "古文白话大板块" not in rendered
    assert "1-6 个章节" not in rendered
    assert "输出字数必须与原文字数基本一致" not in prompts["gemini_fast_polish"]
    assert "输出字数必须与原文字数基本一致" not in prompts["gemini_fast_refine"]
    assert "不得把故事稿润成术语解释稿或白话讲义" in prompts["gemini_fast_polish"]
    assert "不得把故事稿改成术语解释稿或白话讲义" in prompts["gemini_fast_refine"]
    assert "长佛经稿不得合并不同故事单元" in prompts["gemini_fast_polish"]
    assert "长佛经稿不得合并不同故事单元" in prompts["gemini_fast_refine"]
    assert "不设固定字数标准" in rendered
    assert "讲清楚就自然收住" in rendered
    assert "菜、菜肴写“好吃、味道好、鲜美”" in rendered
    assert "350 到 600 汉字" not in rendered
    assert "350 到 600 汉字" not in prompts["gemini_fast_polish"]
    assert "350 到 600 汉字" not in prompts["gemini_fast_refine"]
    assert "完整故事形态" in prompts["gemini_fast_polish"]
    assert "完整故事形态" in prompts["gemini_fast_refine"]


def test_buddhist_story_unit_splitter_prefers_body_run_over_catalog() -> None:
    catalog = "\n".join(f"{idx}. 目录标题{idx}" for idx in range(1, 16))
    body = "\n".join(
        f"{idx}. 第{idx}喻\n这是第{idx}则譬喻的原文内容，包含愚人的选择、事情的发展和最后的警醒。"
        for idx in range(1, 16)
    )
    raw_text = f"@目录\n{catalog}\n@正文\n{body}"

    units = split_buddhist_story_units(raw_text, original_title="百喻经")

    assert [unit.index for unit in units] == list(range(1, 16))
    assert units[0].title == "第1喻"
    assert "原文内容" in units[0].source
    assert "目录标题" not in units[0].source


def test_buddhist_story_unit_splitter_handles_positional_chinese_numerals() -> None:
    numerals = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "一〇", "一一", "一二"]
    raw_text = "\n".join(
        f"（{numeral}）第{index}喻\n昔有愚人做事颠倒，事情发展之后显出修行上的警醒。"
        for index, numeral in enumerate(numerals, start=1)
    )

    units = split_buddhist_story_units(raw_text, original_title="百喻经")

    assert [unit.index for unit in units] == list(range(1, 13))
    assert units[9].title == "第10喻"
    assert units[10].title == "第11喻"


def test_buddhist_story_unit_splitter_ignores_baiyujing_volume_headings() -> None:
    numerals = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "一〇", "一一", "一二"]
    first_volume = "\n".join(
        f"（{numeral}）第{index}喻\n昔有愚人做事颠倒，事情发展之后显出修行上的警醒。"
        for index, numeral in enumerate(numerals[:6], start=1)
    )
    second_volume = "\n".join(
        f"（{numeral}）第{index}喻\n昔有愚人做事颠倒，事情发展之后显出修行上的警醒。"
        for index, numeral in enumerate(numerals[6:], start=7)
    )
    raw_text = f"百喻经卷第一\n{first_volume}\n百喻经卷第二\n{second_volume}"

    units = split_buddhist_story_units(raw_text, original_title="百喻经")

    assert [unit.index for unit in units] == list(range(1, 13))
    assert all(unit.index != 100 for unit in units)


def test_buddhist_scripture_story_generation_avoids_classical_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_text = "如是我闻。一时佛在舍卫国祇树给孤独园，与大比丘众俱。"
    writer_prompts: list[str] = []
    writer_max_tokens: list[object] = []
    expected_source = build_buddhist_scripture_story_source(raw_text, original_title="金刚经")
    expected_prompt = get_prompts("buddhist_scripture_story")["full_book_writer"].format(
        raw_text=expected_source
    )
    manuscript = """@最终书名
模型乱起的名字
@最终简介
测试简介
@目录
1. 林中说法
@导言
测试导言。
@正文
@1. 林中说法
那一天，佛在舍卫国祇树给孤独园停留，身边有许多比丘一起听他说法。
"""

    async def fake_route_writer_call(prompt: str, **kwargs: object) -> str:
        writer_prompts.append(prompt)
        writer_max_tokens.append(kwargs.get("max_output_tokens"))
        return manuscript

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "polish_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "refine_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "format_book_text", lambda text, **_: text)

    result = asyncio.run(
        content_engine.generate_full_book(
            raw_text,
            category="buddhist_scripture_story",
            output_dir=tmp_path / "buddhist",
            classical_original_title="金刚经",
        )
    )

    assert writer_prompts == [expected_prompt]
    assert writer_max_tokens == [None]
    assert "@最终书名\n金刚经（白话版）" in result
    assert "那一天，佛在舍卫国祇树给孤独园停留" in result
    assert "白话正文必须按原文段落一一对应输出" not in writer_prompts[0]
    assert "【P0001】" not in writer_prompts[0]
    assert "原文结构清单" not in writer_prompts[0]


def test_buddhist_scripture_story_long_parables_use_story_unit_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_text = "\n".join(
        f"{idx}. 第{idx}喻\n从前有个愚人做了一件颠倒的事。事情一步步发展，最后显出修行上的警醒。"
        for idx in range(1, 21)
    )
    writer_prompts: list[str] = []

    async def fake_route_writer_call(prompt: str, **kwargs: object) -> str:
        writer_prompts.append(prompt)
        chapters = re.findall(r"@(\d+)\. ([^,，\n]+)", prompt)
        if not chapters:
            raise AssertionError("story unit writer prompt should list target chapters")
        story = (
            "从前有个愚人听见一句话，便急着照做，却没有先看清事情的来龙去脉。"
            "他先做了一个选择，又因为这个选择不断补救，越补越偏。旁人提醒他时，他仍旧执着在最初的想法里，"
            "直到结果摆在眼前，才发现自己抓住的是表面的方便，丢掉的是事情真正的根本。"
            "这则譬喻不是为了嘲笑愚人，而是提醒修行者：听到一个道理之后，要回到因缘和次第里理解，"
            "不能把一句话孤零零拿出来，当成可以立刻套用的答案。"
            "若只是急着拿走结论，故事就会变成一句空话；只有看见愚人如何一步步走偏，"
            "读者才会明白原文真正要警醒的地方。"
            "所以这一章最后仍然落回原文的本意：修行不是抓住一个漂亮说法就够了，"
            "还要看见自己在具体处境里怎样起心、怎样误判、怎样把小小的执著养成后来的苦果。"
            "故事说完时，那个人的荒唐选择已经显出后果，读者也能顺着这个后果明白，"
            "佛经里的譬喻不是短短一句结论，而是一面照见心念的镜子。"
        )
        return "\n\n".join(
            f"@{number}. {title}\n{story}"
            for number, title in chapters
        )

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "polish_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "refine_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "format_book_text", lambda text, **_: text)

    result = asyncio.run(
        content_engine.generate_full_book(
            raw_text,
            category="buddhist_scripture_story",
            output_dir=tmp_path / "buddhist-long",
            classical_original_title="佛说譬喻集",
        )
    )

    assert len(writer_prompts) == 3
    assert all("佛经白话小故事改写作者" in prompt for prompt in writer_prompts)
    assert "@20. 第20喻" in result
    assert "第21喻" not in result


def test_classical_chinese_interleaved_prompt_preserves_original_and_translation() -> None:
    meta = get_category_meta("classical_chinese_interleaved")
    prompt = get_prompts("classical_chinese_interleaved")["full_book_writer"]
    task_source = build_classical_prompt_source(
        "南阳宋定伯，年少时，夜行，逢鬼，问之。",
        variant="plain",
        original_title="宋定伯捉鬼",
        interleaved=True,
    )
    rendered = prompt.format(raw_text=task_source)

    assert meta["label"] == "古文白话（原文翻译穿插版）"
    assert "原文翻译穿插正文" in rendered
    assert "【原文翻译穿插版硬规则】" in rendered
    assert "【P0001】（原文）" in rendered
    assert "（翻译）现代汉语译文" in rendered
    assert "原文段中的原文内容必须逐字保留" in rendered
    assert "不得先集中输出全部原文再集中输出全部翻译" in rendered


def test_classical_chinese_enforces_original_title_suffix_and_compacts_blank_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_text = "第一段文言。\n\n第二段文言。"
    expected_source = build_classical_prompt_source(
        raw_text,
        original_title="十叶野闻",
    )
    expected_prompt = get_prompts("classical_chinese")["full_book_writer"].format(
        raw_text=expected_source
    )
    writer_prompts: list[str] = []
    manuscript = """@最终书名
模型乱起的名字
@最终简介
测试简介
@目录
1. 全文
@导言
测试导言。
@正文
@1. 全文
【P0001】第一段白话。

【P0002】第二段白话。
"""

    async def fake_route_writer_call(prompt: str, **_: object) -> str:
        writer_prompts.append(prompt)
        return manuscript

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "polish_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "refine_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "format_book_text", lambda text, **_: text)

    result = asyncio.run(
        content_engine.generate_full_book(
            raw_text,
            category="classical_chinese",
            output_dir=tmp_path / "classical",
            classical_original_title="十叶野闻",
        )
    )

    assert writer_prompts == [expected_prompt]
    assert "@最终书名\n十叶野闻（白话版）" in result
    assert "第一段白话。\n第二段白话。" in result
    assert "【P0001】" not in result
    assert "【P0002】" not in result
    assert "第一段白话。\n\n第二段白话。" not in result


def test_classical_chinese_interleaved_outputs_original_translation_pairs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_text = "南阳宋定伯，年少时，夜行，逢鬼，问之。\n定伯诳之，言：“我亦鬼。”"
    expected_source = build_classical_prompt_source(
        raw_text,
        original_title="宋定伯捉鬼",
        interleaved=True,
    )
    expected_prompt = get_prompts("classical_chinese_interleaved")["full_book_writer"].format(
        raw_text=expected_source
    )
    writer_prompts: list[str] = []
    manuscript = """@最终书名
宋定伯捉鬼（白话版）
@最终简介
测试简介
@目录
1. 全文
@导言
测试导言。
@正文
@1. 全文
【P0001】（原文）南阳宋定伯，年少时，夜行，逢鬼，问之。
（翻译）南阳有个叫宋定伯的人，年轻时夜里赶路，遇见了鬼，便开口询问它。
【P0002】（原文）定伯诳之，言：“我亦鬼。”
（翻译）宋定伯骗它说：“我也是鬼。”
"""

    async def fake_route_writer_call(prompt: str, **_: object) -> str:
        writer_prompts.append(prompt)
        return manuscript

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "polish_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "refine_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "format_book_text", lambda text, **_: text)

    result = asyncio.run(
        content_engine.generate_full_book(
            raw_text,
            category="classical_chinese_interleaved",
            output_dir=tmp_path / "classical_interleaved",
            classical_original_title="宋定伯捉鬼",
        )
    )

    assert writer_prompts == [expected_prompt]
    assert "@最终书名\n宋定伯捉鬼（白话版）" in result
    assert "【P0001】" not in result
    assert "【P0002】" not in result
    assert "（原文）南阳宋定伯，年少时，夜行，逢鬼，问之。\n（翻译）南阳有个叫宋定伯的人" in result
    assert "（原文）定伯诳之，言：“我亦鬼。”\n（翻译）宋定伯骗它说：“我也是鬼。”" in result


def test_classical_interleaved_quality_detects_missing_translation_pair() -> None:
    issues = content_engine._classical_quality_issues(  # noqa: SLF001
        "@正文\n【P0001】（原文）南阳宋定伯，年少时，夜行，逢鬼，问之。\n【P0002】（原文）定伯诳之，言：“我亦鬼。”\n（翻译）宋定伯骗它说：“我也是鬼。”",
        expected_count=2,
        source_text="南阳宋定伯，年少时，夜行，逢鬼，问之。\n定伯诳之，言：“我亦鬼。”",
        interleaved=True,
    )

    assert any("P0001 缺少紧随其后的（翻译）段" in issue for issue in issues)


def test_classical_interleaved_quality_detects_duplicate_translation_variants() -> None:
    issues = content_engine._classical_quality_issues(  # noqa: SLF001
        "@正文\n"
        "【P0001】（原文）文学对曰：窃闻治人之道。\n"
        "（翻译）文学之士回答说：私下听闻治理百姓的道理。\n"
        "（翻译）文学之士回答说：我听说治理国家的道理。\n"
        "【P0002】（原文）定伯诳之，言：“我亦鬼。”\n"
        "（翻译）宋定伯骗它说：“我也是鬼。”",
        expected_count=2,
        source_text="文学对曰：窃闻治人之道。\n定伯诳之，言：“我亦鬼。”",
        interleaved=True,
    )

    assert any("P0001 出现连续多个（翻译）段" in issue for issue in issues)


def test_classical_interleaved_normalize_removes_duplicate_translation_variants() -> None:
    manuscript = """@最终书名
盐铁论（白话版）
@正文
@1. 全文
【P0001】（原文）文学对曰：窃闻治人之道。
（翻译）文学之士回答说：私下听闻治理百姓的道理。
（翻译）文学之士回答说：我听说治理国家的道理。
（翻译）文学之士回答说：我私下听说治理百姓的办法。
【P0002】（原文）定伯诳之，言：“我亦鬼。”
（翻译）宋定伯骗它说：“我也是鬼。”
"""

    result = content_engine.normalize_final_markdown(
        manuscript,
        category="classical_chinese_interleaved",
    )

    assert result.count("（原文）") == 2
    assert result.count("（翻译）") == 2
    assert "私下听闻治理百姓的道理" in result
    assert "我听说治理国家的道理" not in result
    assert "我私下听说治理百姓的办法" not in result


def test_classical_interleaved_repair_removes_body_frontmatter_noise_without_llm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    noisy = """@正文
【P0001】（原文）南阳宋定伯，年少时，夜行，逢鬼，问之。
@导言
南阳有个叫宋定伯的人，年轻时夜里赶路，遇见了鬼，便开口问它。

###不该出现的小标题
【P0002】（原文）定伯诳之，言：“我亦鬼。”
@正文
（翻译）宋定伯骗它说：“我也是鬼。”
请问您是否需要接下来的章节概要或微调？
【P0001】这是一段模型误插入的重复编号内容，应当被删除。"""

    async def fail_route_writer_call(*_: object, **__: object) -> str:
        raise AssertionError("deterministic cleanup should avoid full-manuscript LLM repair")

    monkeypatch.setattr(content_engine, "_route_writer_call", fail_route_writer_call)

    result = asyncio.run(
        content_engine._repair_classical_quality_once(
            noisy,
            marked_source_text="",
            source_text="南阳宋定伯，年少时，夜行，逢鬼，问之。\n定伯诳之，言：“我亦鬼。”",
            expected_count=2,
            stage="polish",
            output_dir=tmp_path,
            interleaved=True,
        )
    )

    assert "@导言" not in result
    assert "###不该出现的小标题" not in result
    assert result.count("【P0001】") == 1
    assert result.count("【P0002】") == 1
    assert "（翻译）南阳有个叫宋定伯的人" in result
    assert "请问您是否需要" not in result
    assert "模型误插入" not in result
    assert content_engine._classical_quality_issues(
        result,
        expected_count=2,
        source_text="南阳宋定伯，年少时，夜行，逢鬼，问之。\n定伯诳之，言：“我亦鬼。”",
        interleaved=True,
    ) == []


def test_classical_interleaved_repair_removes_duplicate_translation_variants_without_llm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    noisy = """@正文
【P0001】（原文）文学对曰：窃闻治人之道。
（翻译）文学之士回答说：私下听闻治理百姓的道理。
（翻译）文学之士回答说：我听说治理国家的道理。
【P0002】（原文）定伯诳之，言：“我亦鬼。”
（翻译）宋定伯骗它说：“我也是鬼。”
"""

    async def fail_route_writer_call(*_: object, **__: object) -> str:
        raise AssertionError("duplicate translation cleanup should not call LLM")

    monkeypatch.setattr(content_engine, "_route_writer_call", fail_route_writer_call)

    result = asyncio.run(
        content_engine._repair_classical_quality_once(
            noisy,
            marked_source_text="",
            source_text="文学对曰：窃闻治人之道。\n定伯诳之，言：“我亦鬼。”",
            expected_count=2,
            stage="polish",
            output_dir=tmp_path,
            interleaved=True,
        )
    )

    assert result.count("（翻译）") == 2
    assert "私下听闻治理百姓的道理" in result
    assert "我听说治理国家的道理" not in result
    assert content_engine._classical_quality_issues(
        result,
        expected_count=2,
        source_text="文学对曰：窃闻治人之道。\n定伯诳之，言：“我亦鬼。”",
        interleaved=True,
    ) == []


def test_long_classical_interleaved_full_repair_does_not_call_llm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = (
        "@正文\n"
        "【P0001】（原文）南阳宋定伯，年少时，夜行，逢鬼，问之。\n"
        "【P0002】（原文）定伯诳之，言：“我亦鬼。”\n"
        "（翻译）宋定伯骗它说：“我也是鬼。”\n"
        + "补长文本。" * 80
    )

    async def fail_route_writer_call(*_: object, **__: object) -> str:
        raise AssertionError("long full-manuscript repair should fail before LLM")

    monkeypatch.setattr(content_engine, "_route_writer_call", fail_route_writer_call)
    monkeypatch.setattr(content_engine, "CLASSICAL_FULL_REPAIR_LLM_MAX_CHARS", 100)

    with pytest.raises(RuntimeError, match="结构保护失败"):
        asyncio.run(
            content_engine._repair_classical_quality_once(
                bad,
                marked_source_text="",
                source_text="南阳宋定伯，年少时，夜行，逢鬼，问之。\n定伯诳之，言：“我亦鬼。”",
                expected_count=2,
                stage="polish",
                output_dir=tmp_path,
                interleaved=True,
            )
        )


def test_classical_chinese_uses_no_application_output_caps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer_kwargs: list[dict[str, object]] = []
    polish_kwargs: list[dict[str, object]] = []
    refine_kwargs: list[dict[str, object]] = []
    format_kwargs: list[dict[str, object]] = []

    async def fake_route_writer_call(prompt: str, **kwargs: object) -> str:
        writer_kwargs.append(dict(kwargs))
        return MINIMAL_MANUSCRIPT

    def fake_format_book_text(text: str, **kwargs: object) -> str:
        format_kwargs.append(dict(kwargs))
        return text

    def fake_polish_book_text(text: str, **kwargs: object) -> str:
        polish_kwargs.append(dict(kwargs))
        return text

    def fake_refine_book_text(text: str, **kwargs: object) -> str:
        refine_kwargs.append(dict(kwargs))
        return text

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "polish_book_text", fake_polish_book_text)
    monkeypatch.setattr(content_engine, "refine_book_text", fake_refine_book_text)
    monkeypatch.setattr(content_engine, "format_book_text", fake_format_book_text)

    asyncio.run(
        content_engine.generate_full_book(
            "第一段文言。",
            category="classical_chinese",
            output_dir=tmp_path / "classical_budget",
            classical_original_title="人间有鬼",
        )
    )

    assert writer_kwargs[0]["max_output_tokens"] == 0
    assert polish_kwargs
    assert refine_kwargs
    assert all(kwargs["disable_max_output_tokens"] is True for kwargs in polish_kwargs)
    assert all(kwargs["disable_max_output_tokens"] is True for kwargs in refine_kwargs)
    assert all("max_output_tokens" not in kwargs for kwargs in polish_kwargs)
    assert all("max_output_tokens" not in kwargs for kwargs in refine_kwargs)
    assert format_kwargs[0]["max_output_tokens"] == 0
    assert format_kwargs[0]["fail_on_max_tokens"] is True


def test_classical_chinese_deepseek_max_tokens_fallback_preserves_tail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tail = "尾部必须完整保留"
    manuscript = MINIMAL_MANUSCRIPT.replace("测试正文。", f"{'长正文。' * 1800}{tail}。")

    async def fake_route_writer_call(prompt: str, **_: object) -> str:
        return manuscript

    def fake_format_book_text(text: str, **kwargs: object) -> str:
        raise content_engine.DeepSeekMaxTokensError(
            partial_text=text[:1000],
            max_output_tokens=int(kwargs["max_output_tokens"]),
        )

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "polish_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "refine_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "format_book_text", fake_format_book_text)

    result = asyncio.run(
        content_engine.generate_full_book(
            "第一段文言。",
            category="classical_chinese",
            output_dir=tmp_path / "classical_fallback",
            classical_original_title="人间有鬼",
        )
    )

    assert tail in result


def test_classical_chinese_detects_structure_and_known_translation_issues() -> None:
    missing = content_engine._classical_structure_issues(  # noqa: SLF001
        "@正文\n【P0001】第一段。\n###帝后遗容如生\n第三段。",
        expected_count=3,
    )
    mistranslation = content_engine._classical_translation_issues(  # noqa: SLF001
        "家人捉双卫来寻生。\n婢子不宜好相识。\n一念错，便觉百行皆非。",
        "@正文\n【P0001】家里的仆人带着两个侍卫来找他。\n【P0002】这丫头，确实不该结交什么好相识。\n【P0003】一念之差，往往会导致所有行为都变成错误。",
    )
    clean_translation = content_engine._classical_translation_issues(  # noqa: SLF001
        "一念错，便觉百行皆非。",
        "@正文\n【P0001】一念起错，便会觉得许多行为都不对。",
    )
    hard_errors = detect_final_hard_errors(
        "@正文\n【P0001】（原文）一念错，便觉百行皆非。\n（翻译）一念之差，往往会导致所有行为都变成错误。",
        category=CLASSICAL_REFERENCE_REWRITE_CATEGORY,
    )

    assert any("缺少原文段落编号" in issue for issue in missing)
    assert any("Markdown 伪小标题" in issue for issue in missing)
    assert any("双卫" in issue for issue in mistranslation)
    assert any("婢子不宜好相识" in issue for issue in mistranslation)
    assert any("便觉" in issue for issue in mistranslation)
    assert not clean_translation
    assert any("便觉" in name for name in hard_errors)


def test_classical_paragraph_markers_support_100w_scale_counts() -> None:
    marker = classical_paragraph_marker(10000)

    assert marker == "【P10000】"
    assert strip_classical_paragraph_markers(f"{marker}第万段。") == "第万段。"
    assert not content_engine._classical_structure_issues(  # noqa: SLF001
        "@正文\n【P10000】第万段白话。",
        expected_count=10000,
        expected_markers=[10000],
    )


def test_classical_marker_line_splitter_preserves_p_lines() -> None:
    text = "【P0001】第一段白话。\n【P0002】第二段白话很长很长。\n【P10000】第万段白话。"

    chunks = content_engine._split_text_by_classical_marker_lines(  # noqa: SLF001
        text,
        target_chars=18,
    )

    assert "".join(chunk.replace("\n", "") for chunk in chunks) == text.replace("\n", "")
    assert any("【P10000】第万段白话。" in chunk for chunk in chunks)
    assert all("【P" in line for chunk in chunks for line in chunk.splitlines())


def test_classical_source_segmenter_handles_100w_chars_without_dropping_lines() -> None:
    lines = ["甲" * 1000 for _ in range(1000)]

    segments = content_engine._classical_segment_source_lines(  # noqa: SLF001
        lines,
        target_chars=12_000,
    )
    flattened = [line for _, segment_lines in segments for line in segment_lines]

    assert sum(len(line) for line in lines) == content_engine.CLASSICAL_LONG_SOURCE_DESIGN_TARGET_CHARS
    assert flattened == lines
    assert [start for start, _ in segments] == sorted(start for start, _ in segments)
    assert len(segments) > 50


def test_classical_segmented_writer_caches_completed_segments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_text = "\n".join(f"第{i}段文言。" + ("春秋旧事。" * 80) for i in range(1, 13))
    writer_calls: list[str] = []

    async def fake_route_writer_call(prompt: str, **kwargs: object) -> str:
        writer_calls.append(prompt)
        assert kwargs["max_output_tokens"] == 0
        assert kwargs["retry_on_failure"] is False
        if "你只负责生成前置信息" in prompt:
            return """@最终书名
百段书（白话版）
@最终简介
测试简介。
@目录
1. 全文
@导言
测试导言。
@正文
@1. 全文"""
        source = prompt.split("【待处理原文】", 1)[1]
        markers = [int(item) for item in content_engine.CLASSICAL_PARAGRAPH_MARKER_RE.findall(source)]
        return "\n".join(f"【P{marker:04d}】第{marker}段白话。" for marker in markers)

    monkeypatch.setattr(content_engine, "CLASSICAL_SEGMENTED_WRITER_MIN_SOURCE_CHARS", 1)
    monkeypatch.setattr(content_engine, "CLASSICAL_SEGMENTED_WRITER_MIN_PARAGRAPHS", 1)
    monkeypatch.setattr(content_engine, "CLASSICAL_SEGMENTED_WRITER_TARGET_SOURCE_CHARS", 16)
    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "polish_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "refine_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "format_book_text", lambda text, **_: text)

    result = asyncio.run(
        content_engine.generate_full_book(
            raw_text,
            category="classical_chinese",
            output_dir=tmp_path / "classical_segmented",
            classical_original_title="百段书",
        )
    )

    assert "@最终书名\n百段书（白话版）" in result
    for marker in range(1, 13):
        assert f"第{marker}段白话。" in result
        assert f"【P{marker:04d}】" not in result
    assert len([prompt for prompt in writer_calls if "当前分段范围" in prompt]) >= 3

    first_call_count = len(writer_calls)
    for stage_file in ("01_writer.md", "02_polish.md", "03_refine.md", "04_format.md"):
        (tmp_path / "classical_segmented" / stage_file).unlink()
    asyncio.run(
        content_engine.generate_full_book(
            raw_text,
            category="classical_chinese",
            output_dir=tmp_path / "classical_segmented",
            classical_original_title="百段书",
        )
    )

    assert len(writer_calls) == first_call_count


def test_web_short_story_style_reference_keeps_history_prompt_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_text = "女主醒来发现自己穿进了短剧反派剧本。"
    writer_prompts: list[str] = []

    async def fake_route_writer_call(prompt: str, **_: object) -> str:
        writer_prompts.append(prompt)
        return MINIMAL_MANUSCRIPT

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "polish_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "refine_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "format_book_text", lambda text, **_: text)

    asyncio.run(
        content_engine.generate_full_book(
            raw_text,
            category="web_short_story",
            output_dir=tmp_path / "web_short_story",
            style_reference="冷峻悬疑作家",
            style_notes="第一人称，反转更密。",
        )
    )

    assert writer_prompts[0].startswith(
        get_prompts("history_culture")["full_book_writer"].format(raw_text=raw_text)
    )
    assert "笔锋参考要求" in writer_prompts[0]
    assert "参考对象：冷峻悬疑作家" in writer_prompts[0]
    assert "补充要求：第一人称，反转更密。" in writer_prompts[0]
    assert "不要复刻、改写或搬运任何现成作品句子" in writer_prompts[0]


def test_social_science_test_style_reference_keeps_social_prompt_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_text = "普通社会议题素材。"
    writer_prompts: list[str] = []

    async def fake_route_writer_call(prompt: str, **_: object) -> str:
        writer_prompts.append(prompt)
        return MINIMAL_MANUSCRIPT

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "polish_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "refine_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "format_book_text", lambda text, **_: text)

    asyncio.run(
        content_engine.generate_full_book(
            raw_text,
            category="social_science_test",
            output_dir=tmp_path / "social_science_test",
            style_reference="冷峻非虚构笔法",
            style_notes="多一点现实细节和判断锋利度。",
        )
    )

    assert writer_prompts[0].startswith(
        get_prompts("social_science")["full_book_writer"].format(raw_text=raw_text)
    )
    assert "笔锋参考要求" in writer_prompts[0]
    assert "参考对象：冷峻非虚构笔法" in writer_prompts[0]
    assert "补充要求：多一点现实细节和判断锋利度。" in writer_prompts[0]
    assert "仍要严格遵守社会痛点类当前 prompt" in writer_prompts[0]


def test_style_reference_only_applies_to_test_categories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_text = "普通社会议题素材。"
    writer_prompts: list[str] = []

    async def fake_route_writer_call(prompt: str, **_: object) -> str:
        writer_prompts.append(prompt)
        return MINIMAL_MANUSCRIPT

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "polish_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "refine_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "format_book_text", lambda text, **_: text)

    asyncio.run(
        content_engine.generate_full_book(
            raw_text,
            category="social_science",
            output_dir=tmp_path / "social_science",
            style_reference="冷峻悬疑作家",
            style_notes="第一人称，反转更密。",
        )
    )

    assert "笔锋参考要求" not in writer_prompts[0]


def test_reference_article_mode_appends_emotion_originality_rules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_text = "这是一篇参考文章，讨论亲密关系里的情绪消耗。"
    writer_prompts: list[str] = []
    base_prompt = get_prompts("emotion_relationship")["full_book_writer"].format(raw_text=raw_text)

    async def fake_route_writer_call(prompt: str, **_: object) -> str:
        writer_prompts.append(prompt)
        return MINIMAL_MANUSCRIPT

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "polish_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "refine_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "format_book_text", lambda text, **_: text)

    asyncio.run(
        content_engine.generate_full_book(
            raw_text,
            category="emotion_relationship",
            output_dir=tmp_path / "reference_article",
            input_source="reference_article",
        )
    )

    assert writer_prompts[0].startswith(base_prompt)
    assert "参考文章生成专项要求" in writer_prompts[0]
    assert "只学习主题、问题意识和关系困境" in writer_prompts[0]
    assert "不能改写、洗稿或同义替换原文" in writer_prompts[0]
    assert "正文字数不设人为上限" in writer_prompts[0]


def test_reference_article_mode_only_supports_emotion_relationship() -> None:
    with pytest.raises(ValueError, match="参考文章生成当前只支持情感·关系分类"):
        asyncio.run(
            content_engine.generate_full_book(
                "普通社会议题素材。",
                category="social_science",
                input_source="reference_article",
            )
        )


def test_gemini_refine_max_tokens_falls_back_to_chunked_refine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_text = "用于测试长文二润 MAX_TOKENS 兜底。"
    calls: list[str] = []

    async def fake_route_writer_call(prompt: str, **_: object) -> str:
        calls.append("writer")
        return MINIMAL_MANUSCRIPT

    async def fake_chunk_processor(text: str, **kwargs: object) -> str:
        if kwargs.get("progress_stage") == "refine_chunk":
            calls.append("chunked_refine")
            return text + "\n分块二润完成。"
        calls.append("polish")
        return text

    def max_tokens_refine(*_: object, **__: object) -> str:
        raise GeminiMaxTokensError(
            "Gemini 返回空响应 finish_reason=FinishReason.MAX_TOKENS thoughts_tokens=None"
        )

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "_polish_by_paragraph_chunks_strict", fake_chunk_processor)
    monkeypatch.setattr(content_engine, "refine_book_text", max_tokens_refine)
    monkeypatch.setattr(content_engine, "format_book_text", lambda text, **_: text)

    result = asyncio.run(
        content_engine.generate_full_book(
            raw_text,
            category="social_science",
            output_dir=tmp_path / "max_tokens",
        )
    )

    assert "分块二润完成。" in result
    assert calls == ["writer", "polish", "chunked_refine"]


def test_gemini_polish_max_tokens_retries_smaller_subchunks(tmp_path: Path) -> None:
    raw_text = "A" * 1200
    calls: list[int] = []
    events: list[tuple[str, dict[str, object]]] = []

    def fake_polish(chunk: str, **_: object) -> str:
        calls.append(len(chunk))
        if len(chunk) > 500:
            raise GeminiMaxTokensError(
                "Gemini returned empty response finish_reason=FinishReason.MAX_TOKENS"
            )
        return f"polished:{len(chunk)}"

    result = asyncio.run(
        content_engine._polish_by_paragraph_chunks_strict(
            raw_text,
            target_chars=2000,
            output_dir=tmp_path,
            chunk_func=fake_polish,
            call_timeout_seconds=0,
            on_stage=lambda stage, info: events.append((stage, info)),
        )
    )

    assert result.count("polished:") == 3
    assert sorted(size for size in calls if size <= 500) == [350, 350, 500]
    assert calls[0] == 1200
    assert 700 in calls
    assert "AAAAA" not in result
    assert len(events) == 2
    assert events[0][0] == "polish_chunk"
    assert events[0][1]["phase"] == "started"
    assert events[1][0] == "polish_chunk"
    assert events[1][1]["phase"] == "completed"
    assert events[0][1]["total"] == 1
    cache_files = list((tmp_path / "02_polish_chunks").glob("*.md"))
    assert len(cache_files) == 1
    assert "polished:500" in cache_files[0].read_text(encoding="utf-8")


def test_classical_chunk_fallback_continues_after_gemini_error(tmp_path: Path) -> None:
    raw_text = "alpha\n\nbeta"
    events: list[tuple[str, dict[str, object]]] = []

    def failing_polish(chunk: str, **_: object) -> str:
        raise RuntimeError(f"blocked chunk: {chunk}")

    result = asyncio.run(
        content_engine._polish_by_paragraph_chunks_strict(
            raw_text,
            target_chars=5,
            output_dir=tmp_path,
            chunk_func=failing_polish,
            allow_chunk_fallback=True,
            call_timeout_seconds=0,
            on_stage=lambda stage, info: events.append((stage, info)),
        )
    )

    assert "alpha" in result
    assert "beta" in result
    assert any(info.get("phase") == "fallback" for _, info in events)


def test_validated_chunk_fallback_is_cached_for_resume(tmp_path: Path) -> None:
    raw_text = "@77. Salt\nA smooth parable body."
    bad_output = "@7. Salt\nGemini changed the chapter marker."
    events: list[tuple[str, dict[str, object]]] = []
    calls = 0

    def bad_polish(chunk: str, **_: object) -> str:
        nonlocal calls
        calls += 1
        assert chunk == raw_text
        return bad_output

    result = asyncio.run(
        content_engine._polish_by_paragraph_chunks_strict(
            raw_text,
            target_chars=1000,
            output_dir=tmp_path,
            chunk_func=bad_polish,
            allow_chunk_fallback=True,
            call_timeout_seconds=0,
            splitter=lambda text, **_: [text],
            chunk_validator=content_engine._buddhist_chapter_marker_issues,
            on_stage=lambda stage, info: events.append((stage, info)),
        )
    )

    assert result == raw_text
    assert calls == 1
    assert any(info.get("phase") == "fallback" for _, info in events)

    def should_not_call_gemini(_: str, **__: object) -> str:
        raise AssertionError("fallback chunk should be reused from cache")

    resumed = asyncio.run(
        content_engine._polish_by_paragraph_chunks_strict(
            raw_text,
            target_chars=1000,
            output_dir=tmp_path,
            chunk_func=should_not_call_gemini,
            allow_chunk_fallback=True,
            call_timeout_seconds=0,
            splitter=lambda text, **_: [text],
            chunk_validator=content_engine._buddhist_chapter_marker_issues,
        )
    )

    assert resumed == raw_text


def test_zero_gemini_chunk_timeout_is_bounded_wait_not_output_cap() -> None:
    timeout = content_engine._effective_gemini_chunk_timeout(0, 180)

    assert timeout > 0
    assert timeout <= 180


def test_long_single_paragraph_is_split_before_gemini() -> None:
    chunks = content_engine._split_text_by_paragraph("A" * 2500, target_chars=1000)

    assert len(chunks) == 3
    assert max(len(chunk) for chunk in chunks) <= 1000
    assert "".join(chunks) == "A" * 2500
