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
    SECTION_TITLE_REFINE_GUARD_PROMPT,
    SECTION_TITLE_REQUIREMENT_PROMPT,
    SUPPORTED_CATEGORIES,
    get_category_meta,
    get_prompts,
    normalize_category,
)
from app.services.classical_chinese import (
    build_classical_prompt_source,
    get_classical_variant_meta,
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
    category for category in SUPPORTED_CATEGORIES if category not in {"comic_series", "healing_comic"}
]


@pytest.mark.parametrize("category", WRITER_ROUTING_CATEGORIES)
def test_generate_full_book_routes_writer_prompt_by_category(
    category: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_text = "这是一段用于本地路由测试的原始文稿。"
    writer_prompts: list[str] = []
    expected_raw_text = (
        build_classical_prompt_source(raw_text)
        if category == "classical_chinese"
        else raw_text
    )
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


def test_finance_technology_category_uses_dedicated_prompt() -> None:
    meta = get_category_meta("finance_technology")
    prompt = get_prompts("finance_technology")["full_book_writer"]
    rendered = prompt.format(raw_text="原始视频文稿")

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
    assert "【小节标题全局要求】" in prompt
    assert "【导言与正文分工全局要求】" in prompt
    assert "原始视频文稿" in rendered


def test_finance_technology_aliases_resolve() -> None:
    assert normalize_category("财经") == "finance_technology"
    assert normalize_category("财经科技") == "finance_technology"
    assert normalize_category("财经·科技") == "finance_technology"
    assert normalize_category("finance_tech") == "finance_technology"


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


def _long_finance_manuscript(repeat: int = 5200) -> str:
    body = "财经科技影响链条需要回到视频信息点、产业条件、政策约束和普通人的现实选择中展开。" * repeat
    return MINIMAL_MANUSCRIPT.replace("测试正文。", body)


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
    assert "必须按视频信息点展开" in writer_prompts[0]
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
    assert "不能只处理开头、前半部分或前 20000 字" in str(polish_kwargs[0]["system_instruction"])
    assert refine_kwargs[0]["disable_max_output_tokens"] is True
    assert refine_kwargs[0]["retries"] == 1
    assert "财经科技超长文整稿处理保护" in str(refine_kwargs[0]["system_instruction"])
    assert "不能只处理开头、前半部分或前 20000 字" in str(refine_kwargs[0]["system_instruction"])
    assert format_kwargs[0]["max_output_tokens"] == 0
    assert format_kwargs[0]["fail_on_max_tokens"] is True
    assert format_kwargs[0]["retries"] == 1
    assert "财经科技超长文整稿格式保护" in str(format_kwargs[0]["prompt_template"])
    assert "不能只输出前半部分或前 20000 字" in str(format_kwargs[0]["prompt_template"])


def test_finance_longform_short_writer_does_not_call_openai_twice_or_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer_prompts: list[str] = []
    writer_kwargs: list[dict[str, object]] = []

    async def fake_route_writer_call(prompt: str, **kwargs: object) -> str:
        writer_prompts.append(prompt)
        writer_kwargs.append(dict(kwargs))
        return MINIMAL_MANUSCRIPT

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
    assert len(writer_prompts) == 1
    assert "财经科技长文完整补足任务" not in writer_prompts[0]
    assert all(kwargs["max_output_tokens"] == 0 for kwargs in writer_kwargs)
    assert all(kwargs["retry_on_failure"] is False for kwargs in writer_kwargs)
    assert content_engine._finance_body_chars(result) < 50_000


def test_finance_longform_deepseek_compression_keeps_refined_full_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_manuscript = _long_finance_manuscript()

    async def fake_route_writer_call(prompt: str, **kwargs: object) -> str:
        return long_manuscript

    def fake_format_book_text(text: str, **kwargs: object) -> str:
        return MINIMAL_MANUSCRIPT

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
    assert "形似神似，活灵活现" in rendered
    assert "不要脑补剧情，不要跳剧情，不要自己加开头和结尾" in rendered
    assert "志怪小说爽读版" in rendered
    assert "以剧情和人物对话为核心" in rendered
    assert "【古汉语词典摘录】" in rendered
    assert get_classical_variant_meta("武侠小说爽读版")["id"] == "wuxia_web"


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
第一段白话。

第二段白话。
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
    assert "第一段白话。\n\n第二段白话。" not in result


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
