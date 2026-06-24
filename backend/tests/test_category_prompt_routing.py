from __future__ import annotations

from pathlib import Path
import sys
import asyncio

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from app.prompts.goal_driven_templates import (
    INTRO_BODY_REFINE_GUARD_PROMPT,
    INTRO_BODY_SEPARATION_PROMPT,
    POLISH_FIXED_PHRASE_GUARD_PROMPT,
    SECTION_TITLE_REFINE_GUARD_PROMPT,
    SECTION_TITLE_REQUIREMENT_PROMPT,
    SUPPORTED_CATEGORIES,
    get_category_meta,
    get_prompts,
    normalize_category,
)
from app.services.classical_chinese import (
    build_classical_prompt_source,
    classical_paragraph_marker,
    get_classical_variant_meta,
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

WRITER_ROUTING_CATEGORIES = [
    category
    for category in SUPPORTED_CATEGORIES
    if category not in {"comic_series", "healing_comic", "finance_technology", "finance_technology_advice"}
]


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
    assert "在不删减内容的前提下重新分段，每段 50~100 字" in refine
    assert "三个备选书名（小节标题：@三个备选书名）" in formatter
    assert "每个章节的标题统一格式为：！！！N. 章节名" in formatter
    assert "每一个标题文字前面加一个 ！！！ 符号作为居中标记" in formatter
    assert "正文只允许使用正式章节标题：@N. 章节名" not in formatter


def test_life_philosophy_self_rescue_keeps_dedicated_writer() -> None:
    prompt = get_prompts("life_philosophy_self_rescue")["full_book_writer"]

    assert "人生哲学（困境自救版）" in prompt
    assert "@最终书名" in prompt
    assert "@三个备选书名" not in prompt


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
            "emotion_relationship_unique_view",
            "心理情感（情感·关系）（独特观点版）",
            "让最终成稿与原视频文稿在结构、案例、表达和推进方式上都有明显区别",
        ),
        (
            "health_wellness_daily_pain",
            "健康养生（日常痛点版）",
            "必须单独列出一章，详细讲解什么状况需要寻求专业帮助",
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
    assert normalize_category("情感关系独特观点版") == "emotion_relationship_unique_view"
    assert normalize_category("健康养生日常痛点版") == "health_wellness_daily_pain"
    assert normalize_category("古文白话（原文翻译穿插版）") == "classical_chinese_interleaved"
    assert normalize_category("财经科技（观点建议版）") == "finance_technology_advice"


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


def test_finance_technology_advice_category_uses_viewpoint_advice_prompt() -> None:
    meta = get_category_meta("finance_technology_advice")
    prompt = get_prompts("finance_technology_advice")["full_book_writer"]
    rendered = prompt.format(raw_text="观点建议版测试资料")

    assert meta["label"] == "财经科技（观点建议版）"
    assert "财经科技来源表述专项要求" in prompt
    assert "财经科技联网检索与最新例子硬性要求" in prompt
    assert "必须调用 API 联网检索" in prompt
    assert "不要退而求其次使用 2025 年、2024 年、2023 年或更早案例" in prompt
    assert "你需要先从参照材料中提炼出最值得大众关心的核心问题、现实矛盾和基本判断。" in prompt
    assert "每个核心问题都要形成明确、具体的独立判断" not in prompt
    assert "从被忽略的变量、利益关系、因果链条、适用条件或现实代价中寻找新的解释角度" not in prompt
    assert "先识别常见说法和大众默认认知" not in prompt
    assert "不得为了显得独特而刻意反常识" not in prompt
    assert "对于时下（2026年）某领域的前景预估，必须使用最新的例子" not in prompt
    assert "不要像一套分析模板从头写到底" in prompt
    assert "必须设置一个独立小节" in prompt
    assert "具体、可执行、有适用条件的建议" in prompt
    assert "如何辨别误导信息、选择可靠渠道或更科学地获取和使用相关知识" in prompt
    assert "正文字数不设人为上限" in prompt
    assert "[参考资料全文]" in prompt
    assert "观点建议版测试资料" in rendered


def test_finance_technology_aliases_resolve() -> None:
    assert normalize_category("财经") == "finance_technology"
    assert normalize_category("财经科技") == "finance_technology"
    assert normalize_category("财经·科技") == "finance_technology"
    assert normalize_category("finance_tech") == "finance_technology"
    assert normalize_category("财经科技（观点建议版）") == "finance_technology_advice"
    assert normalize_category("财经科技观点建议版") == "finance_technology_advice"
    assert normalize_category("财经·科技（观点建议版）") == "finance_technology_advice"
    assert normalize_category("finance_tech_advice") == "finance_technology_advice"


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


def test_finance_advice_category_always_uses_finance_workflow() -> None:
    assert content_engine._should_use_finance_longform(
        "finance_technology_advice",
        "很短的财经科技观点建议版资料。",
        source_duration_seconds=60,
    )
    assert content_engine._should_use_finance_longform(
        "finance_technology",
        "很短的财经科技资料。",
        source_duration_seconds=60,
    )


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


def test_finance_technology_advice_longform_uses_finance_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer_prompts: list[str] = []
    polish_kwargs: list[dict[str, object]] = []
    format_kwargs: list[dict[str, object]] = []
    writer_output = _long_finance_manuscript()

    async def fake_route_writer_call(prompt: str, **kwargs: object) -> str:
        writer_prompts.append(prompt)
        return writer_output

    def fake_polish_book_text(text: str, **kwargs: object) -> str:
        polish_kwargs.append(dict(kwargs))
        return text

    def fake_format_book_text(text: str, **kwargs: object) -> str:
        format_kwargs.append(dict(kwargs))
        return text

    _patch_finance_latest_research(monkeypatch)
    monkeypatch.setattr(content_engine, "_route_writer_call", fake_route_writer_call)
    monkeypatch.setattr(content_engine, "polish_book_text", fake_polish_book_text)
    monkeypatch.setattr(content_engine, "refine_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "format_book_text", fake_format_book_text)

    result = asyncio.run(
        content_engine.generate_full_book(
            "财经科技观点建议版测试文稿，涉及产业链、资产误判、职业选择和普通人信息判断。",
            category="finance_technology_advice",
            output_dir=tmp_path / "finance_advice_longform",
            source_duration_seconds=30 * 60,
        )
    )

    assert "@最终书名" in result
    assert len(writer_prompts) == 1
    assert "财经科技长视频动态长文强约束" in writer_prompts[0]
    assert "你需要先从参照材料中提炼出最值得大众关心的核心问题、现实矛盾和基本判断。" in writer_prompts[0]
    assert "每个核心问题都要形成明确、具体的独立判断" not in writer_prompts[0]
    assert "具体、可执行、有适用条件的建议" in writer_prompts[0]
    assert "财经科技来源表述专项要求" in writer_prompts[0]
    assert "财经科技联网检索与最新例子硬性要求" in writer_prompts[0]
    assert "已检索到 2026 年当前最新例子" in writer_prompts[0]
    assert polish_kwargs[0]["disable_max_output_tokens"] is True
    assert "财经科技超长文整稿处理保护" in str(polish_kwargs[0]["system_instruction"])
    assert "财经科技联网检索与最新例子硬性要求" in str(polish_kwargs[0]["system_instruction"])
    assert format_kwargs[0]["max_output_tokens"] == 0
    assert "财经科技超长文整稿格式保护" in str(format_kwargs[0]["prompt_template"])
    assert "财经科技联网检索与最新例子硬性要求" in str(format_kwargs[0]["prompt_template"])


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
            assert "不得出现正文内小节标题" in prompts["final_book_format"]
            assert "正文内小节标题，标题正文必须限制" not in prompts["final_book_format"]


def test_section_title_rules_forbid_body_subheadings() -> None:
    prompt = get_prompts("social_science")["full_book_writer"]

    assert "正文只允许使用正式章节标题" in prompt
    assert "不得在章节内部再生成小节标题" in prompt
    assert "Markdown 粗体标题" in prompt


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
        "家人捉双卫来寻生。\n婢子不宜好相识。",
        "@正文\n【P0001】家里的仆人带着两个侍卫来找他。\n【P0002】这丫头，确实不该结交什么好相识。",
    )

    assert any("缺少原文段落编号" in issue for issue in missing)
    assert any("Markdown 伪小标题" in issue for issue in missing)
    assert any("双卫" in issue for issue in mistranslation)
    assert any("婢子不宜好相识" in issue for issue in mistranslation)


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


def test_zero_gemini_chunk_timeout_is_bounded_wait_not_output_cap() -> None:
    timeout = content_engine._effective_gemini_chunk_timeout(0, 180)

    assert timeout > 0
    assert timeout <= 180


def test_long_single_paragraph_is_split_before_gemini() -> None:
    chunks = content_engine._split_text_by_paragraph("A" * 2500, target_chars=1000)

    assert len(chunks) == 3
    assert max(len(chunk) for chunk in chunks) <= 1000
    assert "".join(chunks) == "A" * 2500
