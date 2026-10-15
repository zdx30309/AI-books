from __future__ import annotations

import base64
import json
import time
import uuid
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from threading import Lock
from zipfile import ZipFile

import httpx
import pytest

from app.models.cover_batch import CoverBatchItem, CoverBatchJob
from app.services import cover, cover_batch_jobs, cover_jobs
from app.services.cover_categories import get_cover_category_meta, list_cover_categories
from app.tasks import cover_tasks
from app.services.dangdang_crawler import DANGDANG_REFERENCE_BRANDS, DangdangCoverCrawler


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def _image_size(path: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as image:
        return image.size


CRAYON_ILLUSTRATION_PROMPT = (
    "治愈蜡笔：日韩治愈系绘本封面，手绘感，像私人日记。"
    "高饱和纯色背景，背景颜色不要渐变，简洁构图，大留白。"
    "动物或人物插画，线条松弛笨拙，保留真实手绘颗粒感。"
    "超大手写标题，像随手写在画面上。"
    "整体温柔、孤独、可爱、有陪伴感。"
    "避免商业心理书、PPT感、UI插画风、过度精致和AI感。"
)
LITERATURE_PUBLISHING_PROMPT = cover._LITERATURE_PUBLISHING_STYLE_RULE  # noqa: SLF001


@pytest.fixture(autouse=True)
def disable_external_cover_reference_calls(monkeypatch) -> None:
    monkeypatch.setattr(cover.settings, "COVER_DEEPSEEK_STYLE_ANALYSIS_ENABLED", False)
    monkeypatch.setattr(cover.settings, "COVER_REFERENCE_FETCH_ENABLED", False)
    monkeypatch.setattr(cover.settings, "COVER_REFERENCE_DOWNLOAD_IMAGES", False)


def _patch_orthodox_sequence_state(monkeypatch, tmp_path: Path) -> Path:
    state_dir = tmp_path / "orthodox_reference_state"
    monkeypatch.setattr(cover, "_orthodox_reference_sequence_state_dir", lambda: state_dir)
    return state_dir


def test_write_cover_bytes_file_normalizes_final_cover_size(tmp_path: Path) -> None:
    output = tmp_path / "cover.png"

    cover._write_cover_bytes_file(output, PNG_BYTES)  # noqa: SLF001

    assert _image_size(output) == cover._FINAL_COVER_SIZE  # noqa: SLF001


def test_write_cover_bytes_file_normalizes_draft_cover_size(tmp_path: Path) -> None:
    output = tmp_path / "draft_cover.png"

    cover._write_cover_bytes_file(output, PNG_BYTES)  # noqa: SLF001

    assert _image_size(output) == cover._FINAL_COVER_SIZE  # noqa: SLF001


def test_write_cover_bytes_file_normalizes_comic_image_size(tmp_path: Path) -> None:
    output = tmp_path / "comic_section_01.png"

    cover._write_cover_bytes_file(output, PNG_BYTES, normalize_final_size=True)  # noqa: SLF001

    assert _image_size(output) == cover._FINAL_COVER_SIZE  # noqa: SLF001


def test_image_api_request_size_uses_budget_safe_supported_multiple(monkeypatch) -> None:
    monkeypatch.setattr(cover.settings, "IMAGE_SIZE", "2450x3500")

    request_size = cover._image_api_request_size()  # noqa: SLF001
    width, height = map(int, request_size.split("x"))

    assert request_size == "1664x2384"
    assert width % 16 == 0
    assert height % 16 == 0
    assert width * height <= 4_000_000


def test_image_generation_request_uses_api_safe_size(monkeypatch) -> None:
    captured: dict[str, object] = {}

    @contextmanager
    def fake_semaphore(*_: object, **__: object):
        yield

    def fake_post(*_: object, **kwargs: object) -> dict[str, object]:
        captured.update(kwargs["json_payload"])  # type: ignore[arg-type]
        return {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]}

    monkeypatch.setattr(cover.settings, "IMAGE_SIZE", "2450x3500")
    monkeypatch.setattr(cover, "_resolve_image_api_credentials", lambda: ("key", "https://api.openai.com/v1", ""))
    monkeypatch.setattr(cover, "distributed_semaphore", fake_semaphore)
    monkeypatch.setattr(cover, "_post_image_request_with_cancellation", fake_post)

    cover._request_image_generation_response("测试提示词")  # noqa: SLF001

    assert captured["size"] == "1664x2384"


def test_image_edit_request_uses_api_safe_size(tmp_path: Path, monkeypatch) -> None:
    image_path = tmp_path / "input.png"
    image_path.write_bytes(PNG_BYTES)
    captured: dict[str, object] = {}

    @contextmanager
    def fake_semaphore(*_: object, **__: object):
        yield

    def fake_post(*_: object, **kwargs: object) -> dict[str, object]:
        captured.update(kwargs["data"])  # type: ignore[arg-type]
        return {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]}

    monkeypatch.setattr(cover.settings, "IMAGE_SIZE", "2450x3500")
    monkeypatch.setattr(cover, "_resolve_image_api_credentials", lambda: ("key", "https://api.openai.com/v1", ""))
    monkeypatch.setattr(cover, "distributed_semaphore", fake_semaphore)
    monkeypatch.setattr(cover, "_post_image_request_with_cancellation", fake_post)

    cover._request_image_edit_response(image_path, "测试提示词")  # noqa: SLF001

    assert captured["size"] == "1664x2384"


def test_generate_image_file_from_prompt_normalizes_comic_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fake_generation(*_: object, **__: object) -> dict[str, object]:
        return {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]}

    monkeypatch.setattr(cover, "_request_image_generation_response", fake_generation)

    result = cover.generate_image_file_from_prompt("测试提示词", tmp_path / "comic_section_01.png")

    assert result == tmp_path / "comic_section_01.png"
    assert _image_size(result) == cover._FINAL_COVER_SIZE  # noqa: SLF001


def test_literature_reference_members_excludes_removed_suffixes(tmp_path: Path) -> None:
    zip_path = tmp_path / "reference_covers.zip"
    with ZipFile(zip_path, "w") as archive:
        archive.writestr("参考封面/album_temp_1779350883.jpg", PNG_BYTES)
        archive.writestr("参考封面/album_temp_1779350903.jpg", PNG_BYTES)
        archive.writestr("参考封面/album_temp_1779350904.jpg", PNG_BYTES)

    assert cover._literature_reference_members(zip_path) == ("参考封面/album_temp_1779350904.jpg",)  # noqa: SLF001


def test_literature_reference_zip_prefers_configured_external_path(tmp_path: Path, monkeypatch) -> None:
    zip_path = tmp_path / "external_reference_covers.zip"
    with ZipFile(zip_path, "w") as archive:
        archive.writestr("参考封面/album_temp_1779350904.jpg", PNG_BYTES)

    monkeypatch.setattr(cover.settings, "COVER_ORTHODOX_REFERENCE_ZIP_PATH", str(zip_path))

    assert cover._literature_reference_zip_path() == zip_path  # noqa: SLF001
    assert cover._literature_reference_members(cover._literature_reference_zip_path()) == (  # noqa: SLF001
        "参考封面/album_temp_1779350904.jpg",
    )


def test_style_brief_locks_reference_site_and_brands() -> None:
    cover_input = cover.prepare_cover_input(
        "沉默的账单",
        "@最终简介\n一本讨论普通人现实压力的社会痛点书。",
        author="测试作者",
        category="social_science",
    )

    brief = cover.build_cover_style_brief(cover_input)

    assert brief.reference_site.startswith("当当网")
    assert brief.reference_brands == ("酷威文化", "果麦文化", "时代华语")
    assert "视觉冲击" in brief.matched_category_style
    assert "高饱和" in brief.generation_prompt_addendum
    assert "时代华语" in brief.brand_style_direction
    assert "不要做成电商商品主图" in " ".join(brief.avoid_rules)
    assert {item["brand"] for item in brief.reference_samples} <= {"酷威文化", "果麦文化", "时代华语"}


def test_draft_and_refine_prompts_enforce_publication_cover_not_ecommerce() -> None:
    prompt = cover.build_cover_prompt(
        "醒来以后",
        "@最终简介\n一本帮助普通人处理焦虑和内耗的高饱和插画封面测试书。",
        author="测试作者",
        category="illustration",
    )

    assert "酷威文化" in prompt
    assert "果麦文化" in prompt
    assert "时代华语" in prompt
    assert "完整正面封面本体" in prompt
    assert "不要 3D 书模" in prompt
    assert "不要出现出版社 logo" in prompt
    assert "出版品牌 logo" in prompt
    assert "不要乱码中文" in prompt
    assert "参考出版社版本时也不能做得过黑过暗" in prompt


def test_draft_and_refine_prompts_lock_first_cover_generation_size() -> None:
    cover_input = cover.prepare_cover_input(
        "看清代价",
        "@最终简介\n一本讨论普通人困境和选择代价的社会痛点书。",
        author="测试作者",
        category="social_science",
    )
    brief = cover.build_cover_style_brief(cover_input)
    draft_prompt = cover.build_draft_cover_prompt(cover_input, brief)
    refine_prompt = cover.build_refine_cover_prompt(cover_input, brief)

    for prompt in (draft_prompt, refine_prompt):
        assert "OpenAI 第一步生成的封面初稿就必须严格按 2450*3500" in prompt
        assert "画布宽高比必须是 7:10" in prompt
        assert "不要横版、方图、拼贴图、多封面合集、书模、展示场景、外框画布、额外留边或非封面区域" in prompt


def test_visual_impact_cover_uses_early_high_saturation_workflow() -> None:
    cover_input = cover.prepare_cover_input(
        "沉默的账单",
        "@最终简介\n一本讨论普通人现实压力的社会痛点书。",
        author="测试作者",
        category="社会痛点",
    )
    brief = cover.build_cover_style_brief(cover_input)
    prompt = cover.build_draft_cover_prompt(cover_input, brief)
    refine_prompt = cover.build_refine_cover_prompt(cover_input, brief)

    assert cover_input.category == "social_science"
    assert cover_input.category_label == "视觉冲击"
    assert "早期封面 workflow" in brief.generation_prompt_addendum
    assert "视觉冲击类封面" in prompt
    assert "高饱和鲜艳" in prompt
    assert "强对比、大色块、巨大标题" in prompt
    assert "不要灰暗社科封面" in prompt
    assert "高饱和鲜艳、第一眼抓人的视觉冲击类" in refine_prompt


def test_all_cover_categories_have_prompt_adjustment_interface() -> None:
    for item in list_cover_categories():
        adjustment = cover.get_cover_category_prompt_adjustment(item["id"])
        assert set(adjustment) == {"style_brief_rules", "draft_rules", "refine_rules"}


def test_cover_category_registry_exposes_reference_zip_categories() -> None:
    assert [(item["id"], item["label"]) for item in list_cover_categories()] == [
        ("social_science", "视觉冲击"),
        ("finance_technology", "财经·科技"),
        ("finance_technology_recommendation", "财经科技（建议版）"),
        ("illustration", "饱和插画"),
        ("illustration_crayon", "萌系治愈"),
        ("literature", "正统出版"),
        ("psychology_cognition", "正统测试1"),
        ("literature_simple", "正统测试2"),
        ("social_science_simple", "正统测试3"),
    ]


def test_finance_technology_cover_category_uses_own_profile() -> None:
    cover_input = cover.prepare_cover_input(
        "周期里的误判",
        "@最终简介\n一本解释产业、技术、价格和普通人选择误区的财经科技书。",
        author="测试作者",
        category="财经科技",
    )
    brief = cover.build_cover_style_brief(cover_input)

    assert cover_input.category == "finance_technology"
    assert cover_input.category_label == "财经·科技"
    assert get_cover_category_meta("财经·科技")["id"] == "finance_technology"
    assert "财经科技 / 商业趋势出版封面" == brief.cover_type
    assert "荐股海报" in brief.generation_prompt_addendum
    assert "视觉冲击类封面" not in brief.generation_prompt_addendum


def test_finance_technology_recommendation_cover_category_reuses_finance_profile() -> None:
    cover_input = cover.prepare_cover_input(
        "周期里的建议",
        "@最终简介\n一本解释产业、技术、价格和普通人建议的财经科技书。",
        author="测试作者",
        category="财经科技（建议版）",
    )
    brief = cover.build_cover_style_brief(cover_input)

    assert cover_input.category == "finance_technology_recommendation"
    assert cover_input.category_label == "财经科技（建议版）"
    assert get_cover_category_meta("财经科技（建议版）")["id"] == "finance_technology_recommendation"
    assert "财经科技 / 商业趋势出版封面" == brief.cover_type
    assert "荐股海报" in brief.generation_prompt_addendum


def test_orthodox_test_cover_categories_use_orthodox_prompt_with_distinct_replacement_levels() -> None:
    expected = (
        ("psychology_cognition", "正统测试1", "测试1：轻度替换", "70%-80% 像母版"),
        ("literature_simple", "正统测试2", "测试2：中度替换", "55%-70% 像母版"),
        ("social_science_simple", "正统测试3", "测试3：强替换", "40%-55% 像母版"),
    )

    for category_id, label, level, ratio in expected:
        prompt = cover.build_cover_prompt(
            "参考封面测试",
            "@最终简介\n一本用于测试新封面分类的书。",
            author="测试作者",
            category=category_id,
        )

        assert "正统出版单封面 image-to-image 结构强参考工作流" in prompt
        assert label in prompt
        assert level in prompt
        assert ratio in prompt
        assert "参考封面(3).zip / 正统出版ZIP参考封面库" in prompt
        assert "唯一母版、书名标题主参考、版式主参考和色彩主参考" in prompt
        assert "核心元素和装饰替换是硬约束" in prompt
        assert "角标、边栏、贴纸、徽章、印章、圆点、箭头、气泡" in prompt
        assert "可以模仿它们在版式里的“功能、位置、数量级、疏密和节奏”" in prompt
        assert "必须换成新的形状、新的组合、新的局部语义" in prompt
        assert "书名标题排版必须 100% 抄到位" in prompt
        assert "2450*3500" in prompt
        assert "基础封面生成 prompt" not in prompt
        assert "本书差异化视觉方案" not in prompt
        assert "封面分类.zip" not in prompt
        assert "正统出版封面长期参考指令" not in prompt
        assert CRAYON_ILLUSTRATION_PROMPT not in prompt

    assert cover.prepare_cover_input("书名", "", category="文学类").category == "literature_simple"
    assert cover.prepare_cover_input("书名", "", category="文学").category == "literature"
    assert cover.prepare_cover_input("书名", "", category="社科类").category == "social_science_simple"
    assert cover.prepare_cover_input("书名", "", category="心理认知类").category == "psychology_cognition"
    assert cover.prepare_cover_input("书名", "", category="正统测试1").category == "psychology_cognition"
    assert cover.prepare_cover_input("书名", "", category="正统测试2").category == "literature_simple"
    assert cover.prepare_cover_input("书名", "", category="正统测试3").category == "social_science_simple"


def test_simple_reference_visual_plan_differs_by_book() -> None:
    cover_input_a = cover.prepare_cover_input(
        "温度有尽头吗",
        "@最终简介\n一本关于热量、熵增和绝对零度的认知科普书。",
        author="宇宙中转站",
        category="心理认知类",
    )
    cover_input_b = cover.prepare_cover_input(
        "你要的是真正被满足",
        "@最终简介\n一本关于身体、安全感、爱与被爱的需求地图。",
        author="张亮",
        category="心理认知类",
    )

    plan_a = cover._simple_reference_visual_plan(cover_input_a)  # noqa: SLF001
    plan_b = cover._simple_reference_visual_plan(cover_input_b)  # noqa: SLF001

    assert plan_a != plan_b
    assert set(plan_a) == {
        "semantic_anchor",
        "color_axis",
        "layout_axis",
        "title_axis",
        "visual_axis",
        "quote_axis",
    }
    assert "热量" in plan_a["semantic_anchor"]
    assert "安全感" in plan_b["semantic_anchor"]


def test_orthodox_test_cover_generation_uses_ordered_zip_reference_with_profile_prompts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[tuple[tuple[Path, ...], str, str | None]] = []
    state_dir = _patch_orthodox_sequence_state(monkeypatch, tmp_path)
    zip_path = tmp_path / "reference_covers.zip"
    with ZipFile(zip_path, "w") as archive:
        for index in range(5):
            archive.writestr(f"cover_{index:02d}.png", PNG_BYTES)

    def fail_simple_reference(*args, **kwargs):
        raise AssertionError("orthodox test categories should use the orthodox ZIP, not 封面分类.zip")

    def fake_generation(prompt: str, **kwargs) -> dict[str, object]:
        raise AssertionError("orthodox direct workflow should use image edit, not text generation")

    def fake_edit(image_paths, prompt: str, **kwargs) -> dict[str, object]:
        paths = tuple(image_paths)
        calls.append((paths, prompt, kwargs.get("stage")))
        return {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]}

    monkeypatch.setattr(cover, "_literature_reference_zip_path", lambda: zip_path)
    monkeypatch.setattr(cover, "_simple_reference_image_paths", fail_simple_reference)
    monkeypatch.setattr(cover, "_request_image_generation_response", fake_generation)
    monkeypatch.setattr(cover, "_request_image_edit_response", fake_edit)

    categories = (
        ("心理认知类", "正统测试1", "测试1：轻度替换", "70%-80% 像母版", "cover_00"),
        ("文学类", "正统测试2", "测试2：中度替换", "55%-70% 像母版", "cover_01"),
        ("社科类", "正统测试3", "测试3：强替换", "40%-55% 像母版", "cover_02"),
    )
    for index, (category, label, level, ratio, expected_title) in enumerate(categories):
        work_dir = tmp_path / f"case_{index}"
        result = cover.generate_cover_file(
            f"自卑与超越{index}",
            "@最终简介\n一本关于心理认知与自我成长的书。",
            work_dir,
            category=category,
            author="测试作者",
        )

        draft_call = calls[index * 2]
        refine_call = calls[index * 2 + 1]
        assert result == work_dir / "cover.png"
        assert len(draft_call[0]) == 1
        assert draft_call[0][0].name.startswith("01_")
        assert draft_call[2] == "draft_reference"
        assert refine_call[0][0] == work_dir / "draft_cover.png"
        assert refine_call[2] == "orthodox_refine"
        assert label in draft_call[1]
        assert level in draft_call[1]
        assert ratio in draft_call[1]
        assert "核心元素和装饰替换是硬约束" in draft_call[1]
        assert "基础封面生成 prompt" not in draft_call[1]
        assert "本书差异化视觉方案" not in draft_call[1]
        assert "封面分类.zip" not in draft_call[1]
        assert (work_dir / "orthodox_reference_samples.json").exists()
        assert not (work_dir / "cover_reference_samples.json").exists()
        assert (work_dir / "cover_refine_prompt.txt").exists()
        samples = json.loads((work_dir / "orthodox_reference_samples.json").read_text(encoding="utf-8"))
        assert samples[0]["title"] == expected_title
        style_payload = json.loads((work_dir / "cover_style_brief.json").read_text(encoding="utf-8"))
        assert style_payload["element_replacement_profile"]["label"] == label
        assert style_payload["element_replacement_profile"]["level"] == level

    state = json.loads((state_dir / "sequence.json").read_text(encoding="utf-8"))
    assert state["next_sequence_index"] == 4
    assert state["total_allocations"] == 3


def test_saturated_illustration_reuses_generic_cover_prompt() -> None:
    for category in ("illustration",):
        prompt = cover.build_cover_prompt(
            "普通封面",
            "@最终简介\n一本普通测试书。",
            author="测试作者",
            category=category,
        )

        assert CRAYON_ILLUSTRATION_PROMPT not in prompt
        assert LITERATURE_PUBLISHING_PROMPT not in prompt
        assert "视觉冲击类封面" not in prompt


def test_crayon_illustration_category_has_dedicated_cover_prompt() -> None:
    cover_input = cover.prepare_cover_input(
        "清醒地活",
        "@最终简介\n一本写给成年人情绪困境的温柔心理自助书。",
        author="测试作者",
        category="illustration_crayon",
    )
    brief = cover.build_cover_style_brief(cover_input)
    prompt = cover.build_draft_cover_prompt(cover_input, brief)
    refine_prompt = cover.build_refine_cover_prompt(cover_input, brief)
    adjustment = cover.get_cover_category_prompt_adjustment("illustration_crayon")

    assert "萌系治愈" in prompt
    assert CRAYON_ILLUSTRATION_PROMPT in brief.generation_prompt_addendum
    assert CRAYON_ILLUSTRATION_PROMPT in prompt
    assert CRAYON_ILLUSTRATION_PROMPT in refine_prompt
    assert adjustment["style_brief_rules"][0] == CRAYON_ILLUSTRATION_PROMPT
    assert adjustment["draft_rules"][0] == CRAYON_ILLUSTRATION_PROMPT
    assert adjustment["refine_rules"][0] == f"继续保持并强化：{CRAYON_ILLUSTRATION_PROMPT}"
    assert "治愈蜡笔：日韩治愈系绘本封面" in prompt
    assert "背景颜色不要渐变" in prompt
    assert "只能有1到2句很短的氛围金句" in prompt
    assert "简洁构图，大留白" in prompt
    assert "再增加一两句金句增加氛围" not in prompt
    assert "只能有1到2句很短的氛围金句" in prompt
    assert "禁止清单、段落、整页日记文字和多个便签文案" in prompt
    assert "私人日记是情绪气质" in prompt
    assert "背景必须是高饱和纯色" in prompt
    assert "花朵、植物、风、云、猫" not in prompt
    assert "微信读书/得到系爆款风" not in prompt
    assert "排版要像成熟商业出版物" not in prompt
    assert "微信读书/得到系爆款风" not in refine_prompt
    assert get_cover_category_meta("illustration_crayon")["label"] == "萌系治愈"
    assert any(item["id"] == "illustration_crayon" for item in list_cover_categories())


def test_crayon_illustration_keeps_large_title_and_author_on_cover() -> None:
    cover_input = cover.prepare_cover_input(
        "慢慢好起来",
        "@最终简介\n一本写给成年人情绪困境的温柔书。",
        author="作者A",
        category="illustration_crayon",
    )
    brief = cover.build_cover_style_brief(cover_input)
    prompt = cover.build_draft_cover_prompt(cover_input, brief)
    refine_prompt = cover.build_refine_cover_prompt(cover_input, brief)

    assert CRAYON_ILLUSTRATION_PROMPT in brief.generation_prompt_addendum
    assert cover._CRAYON_ILLUSTRATION_STYLE_RULE == CRAYON_ILLUSTRATION_PROMPT  # noqa: SLF001
    assert "书名「慢慢好起来」必须是封面最大、最显眼的文字" in prompt
    assert "像随手写在画面上的超大手写标题" in prompt
    assert "必须把作者名「作者A」放到封面上，不可省略" in prompt
    assert "必须把作者名「作者A」放到封面上，不可省略" in refine_prompt
    assert "作者名留白" not in prompt


def test_crayon_illustration_uses_ai_visual_plan_before_generation(monkeypatch) -> None:
    ai_plan = (
        "主体意象：雨后窗台上的橙色纸船；构图方式：巨大手写书名绕过纸船；"
        "季节：初夏雨后；空间：明亮窗边花影；视角：近景俯视；"
        "画面叙事：纸船像把情绪送走；色彩主轴：天空蓝、橙花色、嫩草绿。"
    )

    def fake_market_match(_cover_input: cover.CoverInput) -> cover.CoverMarketMatch:
        return cover.CoverMarketMatch(
            category_label="萌系治愈",
            search_keywords="女性治愈 绘本",
            analysis="日韩治愈系绘本封面。",
            visual_plan=ai_plan,
        )

    monkeypatch.setattr(cover, "analyze_cover_market_match", fake_market_match)
    cover_input = cover.prepare_cover_input(
        "雨会停在今天",
        "@最终简介\n一个关于走出低落、重新感受生活的温柔故事。",
        author="作者B",
        category="illustration_crayon",
    )
    brief = cover.build_cover_style_brief(cover_input)
    prompt = cover.build_draft_cover_prompt(cover_input, brief)
    refine_prompt = cover.build_refine_cover_prompt(cover_input, brief)

    assert CRAYON_ILLUSTRATION_PROMPT in prompt
    assert "本书差异化视觉判断" in brief.generation_prompt_addendum
    assert ai_plan in brief.visual_variation_directive
    assert ai_plan in prompt
    assert ai_plan in refine_prompt
    assert "不要把画面拉回通用治愈模板" in refine_prompt
    assert "如果草稿是桌面、摊开的日记本、杯子、台灯、便签、碎纸和窗边静物的通用组合" in refine_prompt
    assert "只保留或补上1到2句很短的氛围金句" in refine_prompt


def test_crayon_illustration_fallback_visual_plan_differs_by_book() -> None:
    cover_input_a = cover.prepare_cover_input(
        "把春天装进口袋",
        "@最终简介\n关于重新发现日常小确幸和轻盈关系的温柔绘本。",
        author="作者A",
        category="illustration_crayon",
    )
    cover_input_b = cover.prepare_cover_input(
        "雨天里的小事",
        "@最终简介\n关于孤独时刻、房间、灯光和自我陪伴的私人日记。",
        author="作者B",
        category="illustration_crayon",
    )
    brief_a = cover.build_cover_style_brief(cover_input_a)
    brief_b = cover.build_cover_style_brief(cover_input_b)
    prompt_a = cover.build_draft_cover_prompt(cover_input_a, brief_a)

    assert "主体意象" in prompt_a
    assert "构图方式" in prompt_a
    assert "季节、空间、视角、画面叙事、纯色背景颜色" in prompt_a
    assert "不要渐变" in prompt_a
    assert "只在封面上写1到2句很短的氛围金句" in prompt_a
    assert "不要默认套用孤独小人、蓝天色块、植物边框" in prompt_a
    assert "尤其不要把不同书都画成桌面、摊开日记本、杯子、台灯、便签、碎纸和窗边静物" in prompt_a
    assert brief_a.visual_variation_directive != brief_b.visual_variation_directive


def test_crayon_illustration_label_aliases_route_to_dedicated_prompt() -> None:
    aliases = [
        "illustration_crayon",
        "萌系治愈",
        "治愈插画（蜡笔）",
        "治愈插画(蜡笔)",
        "治愈插画（蜡笔)",
        "分类插画（蜡笔）",
        "分类插画(蜡笔)",
    ]
    for alias in aliases:
        cover_input = cover.prepare_cover_input(
            "慢慢好起来",
            "@最终简介\n一本写给成年人情绪困境的温柔心理自助书。",
            author="测试作者",
            category=alias,
        )
        prompt = cover.build_cover_prompt(
            "慢慢好起来",
            "@最终简介\n一本写给成年人情绪困境的温柔心理自助书。",
            author="测试作者",
            category=alias,
        )
        assert cover_input.category == "illustration_crayon"
        assert cover_input.category_label == "萌系治愈"
        assert CRAYON_ILLUSTRATION_PROMPT in prompt


def test_crayon_default_category_overrides_spreadsheet_row_category_for_prompt() -> None:
    rows = cover_jobs.parse_cover_spreadsheet(
        "covers.csv",
        "书名,作者,分类,简介\n慢慢好起来,作者A,心理自助,一本写给成年人情绪困境的温柔书\n".encode(
            "utf-8"
        ),
        default_category="萌系治愈",
    )
    row = rows[0]
    prompt = cover.build_cover_prompt(
        str(row["title"]),
        f"@最终简介\n{row['intro']}",
        author=str(row["author"]),
        category=str(row["category"]),
    )

    assert row["category"] == "illustration_crayon"
    assert row["category_label"] == "萌系治愈"
    assert CRAYON_ILLUSTRATION_PROMPT in prompt
    assert "做成时代华语式心理自助书封面" not in prompt


def test_selected_cover_category_overrides_spreadsheet_row_category() -> None:
    rows = cover_jobs.parse_cover_spreadsheet(
        "covers.csv",
        "书名,作者,分类,简介\n醒来以后,作者A,心理自助,一本帮助普通人处理焦虑和内耗的书\n".encode(
            "utf-8"
        ),
        default_category="social_science",
    )

    assert rows[0]["category"] == "social_science"
    assert rows[0]["category_label"] == "视觉冲击"


def test_cover_job_allows_same_title_and_author_reupload(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cover_jobs, "COVER_JOBS_ROOT", tmp_path)
    rows = [
        {"title": "第一本书", "author": "作者A", "category": "social_science", "category_label": "视觉冲击"}
    ]

    first = cover_jobs.create_cover_job("user-1", "covers.csv", rows, default_category="social_science")
    second = cover_jobs.create_cover_job("user-1", "covers.csv", rows, default_category="social_science")

    assert first["job_id"] != second["job_id"]
    assert first["items"][0]["title"] == second["items"][0]["title"] == "第一本书"


def test_orthodox_cover_uses_chatgpt_local_reference_prompt() -> None:
    prompt = cover.build_cover_prompt(
        "风经过房间",
        "@最终简介\n一本关于城市、房间、旅行和自我告别的文学随笔。",
        author="作者L",
        category="正统出版",
    )

    assert cover.prepare_cover_input("风经过房间", "", category="正统出版").category == "literature"
    assert get_cover_category_meta("literature")["label"] == "正统出版"
    assert "正统出版单封面 image-to-image 结构强参考工作流" in prompt
    assert "参考输入与轮转规则" in prompt
    assert "按 ZIP 内排序全局轮转" in prompt
    assert "不要泛泛学习大图库" in prompt
    assert "标题字形气质、字重、字号比例、分行节奏" in prompt
    assert "真实书名、原作者、出版社、平台标识、出版社 logo、二维码、价格、水印" in prompt
    assert "正统出版封面长期参考指令｜高仿执行版" not in prompt
    assert "DeepSeek 分类/市场分析" not in prompt
    assert "当当网" not in prompt


def test_crayon_illustration_prompt_does_not_leak_to_other_cover_categories() -> None:
    for item in list_cover_categories():
        if item["id"] in {"illustration_crayon", "literature"}:
            continue
        prompt = cover.build_cover_prompt(
            "普通封面",
            "@最终简介\n一本普通测试书。",
            author="测试作者",
            category=item["id"],
        )
        assert CRAYON_ILLUSTRATION_PROMPT not in prompt
        assert "封面最大、最显眼的文字" not in prompt
        assert "必须把作者名" not in prompt
        assert "本书差异化视觉判断" not in prompt


def test_illustration_cover_category_has_no_dedicated_prompt() -> None:
    prompt = cover.build_cover_prompt(
        "松弛地活",
        "@最终简介\n一本帮助普通人处理焦虑和内耗的心理自助书。",
        author="测试作者",
        category="illustration",
    )

    assert "分类：饱和插画" in prompt
    assert "做成时代华语式心理自助书封面" in prompt
    assert "治愈系手绘封面，日韩治愈插画风，图片避免抄袭和侵权。" not in prompt
    assert CRAYON_ILLUSTRATION_PROMPT not in prompt
    assert LITERATURE_PUBLISHING_PROMPT not in prompt
    assert get_cover_category_meta("illustration")["label"] == "饱和插画"
    assert any(item["id"] == "illustration" for item in list_cover_categories())


def test_literature_cover_category_uses_modern_publishing_prompt() -> None:
    prompt = cover.build_cover_prompt(
        "远方的光",
        "@最终简介\n一本关于城市、旅行与孤独感的现代文学作品。",
        author="测试作者",
        category="文学",
    )

    assert get_cover_category_meta("literature")["label"] == "正统出版"
    assert cover.prepare_cover_input("远方的光", "", category="正统出版").category == "literature"
    assert "分类：正统出版" in prompt
    assert "正统出版单封面 image-to-image 结构强参考工作流" in prompt
    assert "唯一母版、书名标题主参考、版式主参考和色彩主参考" in prompt
    assert "不要走传统 style_brief、draft、refine 旧链路" in prompt
    assert "正统出版专用 image-to-image 精修步骤继续打磨排版细节" in prompt
    assert "不要做自由发散的自主视觉方案" in prompt
    assert "封面主书名必须与“远方的光”逐字完全一致" in prompt
    assert "不能自己添加前缀、后缀、副标题、系列名、解释性短语、营销词" in prompt
    assert "不能删字、换字、改词序、改标点" in prompt
    assert "参考封面原始文字信息必须零继承" in prompt
    assert "原封面上的所有文字、短句、副标题、金句、腰封文案" in prompt
    assert "都不能与新封面文字信息重合" in prompt
    assert "如果某个词、短句或英文来自参考图原文，必须删除或改写到完全不可识别" in prompt
    assert "书名标题排版必须 100% 抄到位" in prompt
    assert "版式骨架必须贴着母版" in prompt
    assert "色彩结构必须贴着母版" in prompt
    assert "这部分保留抄袭的魂魄和大部分外形" in prompt
    assert "主视觉必须替换，不能直接沿用母版里的具体视觉元素" in prompt
    assert "核心元素和装饰替换是硬约束" in prompt
    assert "角标、边栏、贴纸、徽章、印章、圆点、箭头、气泡" in prompt
    assert "可以模仿它们在版式里的“功能、位置、数量级、疏密和节奏”" in prompt
    assert "必须换成新的形状、新的组合、新的局部语义" in prompt
    assert "在主正统出版分类中，只允许抄母版的排版、配色和字体/标题排版设计语法" in prompt
    assert "允许强参考的范围只有：版心结构、留白比例、标题字形气质" in prompt
    assert "除此之外所有元素都必须更换" in prompt
    assert "不能像母版换皮" in prompt
    assert "只保留“视觉角色”，不保留“具体身份”" in prompt
    assert "最终不能让人一眼认出原参考封面里的猫、椅子、头像" in prompt
    assert "如果能被认出是某张参考封面的换皮、描摹或套壳，也视为失败" in prompt
    assert "当前参考库共有" in prompt
    assert "从 ZIP 第 1 张重新开始" in prompt
    assert "真实品牌资产、真实摄影作品、已有插画作品" in prompt
    assert "可识别主视觉元素" in prompt
    assert "#FFFFFF 数字白" in prompt
    assert "不要米白、奶油白、暖白、灰白或纸白" in prompt
    assert "不要任何纸张纹理、纤维感、印刷颗粒、噪点、做旧感、扫描感" in prompt
    assert "不要模拟实体纸书摄影效果" in prompt
    assert "文字区域必须纯净清晰" in prompt
    assert "高级感灰滤镜" in prompt
    assert "纸张质感可以有，但必须干净、平整、细腻" not in prompt
    assert "允许轻微纸张肌理" not in prompt
    assert "本书差异化视觉方案" not in prompt
    assert "正统出版风格 DNA" not in prompt

    assert "当当网" not in prompt


def test_literature_cover_visual_plan_differs_by_book_and_blocks_sun_road_template() -> None:
    cover_input_a = cover.prepare_cover_input(
        "假如今天是生命的最后一天",
        "@最终简介\n一本关于临终反思、日常选择和重新整理人生优先级的书。",
        author="作者A",
        category="正统出版",
    )
    cover_input_b = cover.prepare_cover_input(
        "允许一切发生",
        "@最终简介\n一本关于松弛感、关系边界、自我允许和情绪流动的书。",
        author="作者B",
        category="正统出版",
    )
    plan_a = cover._orthodox_reference_visual_plan(cover_input_a)  # noqa: SLF001
    plan_b = cover._orthodox_reference_visual_plan(cover_input_b)  # noqa: SLF001
    prompt_a = cover.build_cover_prompt(
        cover_input_a.title,
        "@最终简介\n一本关于临终反思、日常选择和重新整理人生优先级的书。",
        author=cover_input_a.author,
        category="正统出版",
    )

    assert plan_a != plan_b
    assert "本书差异化视觉判断" not in prompt_a
    assert "本书差异化视觉方案" not in prompt_a
    assert "唯一母版、书名标题主参考、版式主参考和色彩主参考" in prompt_a


def test_orthodox_reference_visual_plan_differs_by_book() -> None:
    cover_input_a = cover.prepare_cover_input(
        "风经过房间",
        "@最终简介\n一本关于城市房间、记忆和自我告别的文学随笔。",
        author="作者A",
        category="正统出版",
    )
    cover_input_b = cover.prepare_cover_input(
        "经济学的思维方式",
        "@最终简介\n一本讲商业判断、市场趋势和普通人决策方法的社科书。",
        author="作者B",
        category="正统出版",
    )

    plan_a = cover._orthodox_reference_visual_plan(cover_input_a)  # noqa: SLF001
    plan_b = cover._orthodox_reference_visual_plan(cover_input_b)  # noqa: SLF001

    assert plan_a != plan_b
    assert set(plan_a) == {
        "semantic_anchor",
        "color_axis",
        "layout_axis",
        "title_axis",
        "visual_axis",
        "text_density_axis",
    }
    assert "城市房间" in plan_a["semantic_anchor"]
    assert "市场趋势" in plan_b["semantic_anchor"]


def test_generate_cover_file_writes_three_stage_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[tuple[str, str, str | None]] = []

    def fake_generation(prompt: str, **kwargs) -> dict[str, object]:
        calls.append(("generation", prompt, None))
        return {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]}

    def fake_edit(image_path: Path, prompt: str, **kwargs) -> dict[str, object]:
        assert image_path.name == "draft_cover.png"
        assert image_path.exists()
        calls.append(("edit", prompt, image_path.name))
        return {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]}

    monkeypatch.setattr(cover, "_request_image_generation_response", fake_generation)
    monkeypatch.setattr(cover, "_request_image_edit_response", fake_edit)

    result = cover.generate_cover_file(
        "醒来以后",
        "@最终简介\n一本帮助普通人处理焦虑和内耗的心理自助书。",
        tmp_path,
        category="illustration",
        author="测试作者",
    )

    assert result == tmp_path / "cover.png"
    assert (tmp_path / "draft_cover.png").exists()
    assert (tmp_path / "cover.png").exists()
    assert (tmp_path / "cover_draft_prompt.txt").exists()
    assert (tmp_path / "cover_refine_prompt.txt").exists()

    brief = json.loads((tmp_path / "cover_style_brief.json").read_text(encoding="utf-8"))
    assert brief["reference_site"].startswith("当当网")
    assert brief["reference_brands"] == ["酷威文化", "果麦文化", "时代华语"]
    assert {item["brand"] for item in brief["reference_samples"]} <= {"酷威文化", "果麦文化", "时代华语"}
    assert "时代华语" in brief["brand_style_direction"]
    assert "做成时代华语式心理自助书封面" in brief["generation_prompt_addendum"]
    assert "治愈系手绘封面，日韩治愈插画风，图片避免抄袭和侵权。" not in brief["generation_prompt_addendum"]
    assert calls[0][0] == "generation"
    assert calls[1][0] == "edit"
    assert "可直接商用、具有成熟出版质感" in calls[1][1]
    assert "删除所有出版社 logo" in calls[1][1]
    assert "参考出版社版本时也不能做得过黑过暗" in calls[1][1]


def test_literature_cover_generation_uses_ordered_zip_reference(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[tuple[tuple[Path, ...], str, str | None]] = []
    state_dir = _patch_orthodox_sequence_state(monkeypatch, tmp_path)
    zip_path = tmp_path / "reference_covers.zip"
    with ZipFile(zip_path, "w") as archive:
        for index in range(20):
            archive.writestr(f"covers/reference_{index + 1:02d}.png", PNG_BYTES)

    def fail_style_brief(*args, **kwargs):
        raise AssertionError("literature direct workflow should not build the old style brief")

    def fake_generation(prompt: str, **kwargs) -> dict[str, object]:
        raise AssertionError("literature direct workflow should use multi-image edit, not pure text generation")

    def fake_edit(image_paths, prompt: str, **kwargs) -> dict[str, object]:
        paths = tuple(image_paths)
        calls.append((paths, prompt, kwargs.get("stage")))
        return {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]}

    monkeypatch.setattr(cover, "_literature_reference_zip_path", lambda: zip_path)
    monkeypatch.setattr(cover, "build_cover_style_brief", fail_style_brief)
    monkeypatch.setattr(cover, "build_refine_cover_prompt", fail_style_brief)
    monkeypatch.setattr(cover, "_request_image_generation_response", fake_generation)
    monkeypatch.setattr(cover, "_request_image_edit_response", fake_edit)

    result = cover.generate_cover_file(
        "风经过房间",
        "@最终简介\n一本关于城市房间和自我告别的文学随笔。",
        tmp_path,
        category="正统出版",
        author="作者L",
    )

    assert result == tmp_path / "cover.png"
    assert len(calls) == 2
    assert len(calls[0][0]) == 1
    assert calls[0][0][0].name.startswith("01_")
    assert calls[0][0][0].suffix == ".png"
    assert calls[0][2] == "draft_reference"
    assert len(calls[1][0]) == 1
    assert calls[1][0][0] == tmp_path / "draft_cover.png"
    assert calls[1][2] == "orthodox_refine"
    assert "正统出版单封面 image-to-image 结构强参考工作流" in calls[0][1]
    assert "参考封面(3).zip" in calls[0][1]
    assert "按 ZIP 内排序全局轮转，不按单个任务重置，不随机抽样" in calls[0][1]
    assert "当前参考库共有 20 张可用封面" in calls[0][1]
    assert "全局第 20 张模仿 ZIP 第 20 张" in calls[0][1]
    assert "全局第 21 张从 ZIP 第 1 张重新开始" in calls[0][1]
    assert "唯一母版、书名标题主参考、版式主参考和色彩主参考" in calls[0][1]
    assert "不要再选择别的方向" in calls[0][1]
    assert "不要泛泛学习大图库" in calls[0][1]
    assert "不要混合重组" in calls[0][1]
    assert "不要自由发明新构图" in calls[0][1]
    assert "不要走传统 style_brief、draft、refine 旧链路" in calls[0][1]
    assert "后续会有正统出版专用 image-to-image 精修步骤继续打磨排版细节" in calls[0][1]
    assert "不要做自由发散的自主视觉方案" in calls[0][1]
    assert "封面主书名必须与“风经过房间”逐字完全一致" in calls[0][1]
    assert "不能自己添加前缀、后缀、副标题、系列名、解释性短语、营销词" in calls[0][1]
    assert "不能删字、换字、改词序、改标点" in calls[0][1]
    assert "参考封面原始文字信息必须零继承" in calls[0][1]
    assert "原封面上的所有文字、短句、副标题、金句、腰封文案" in calls[0][1]
    assert "都不能与新封面文字信息重合" in calls[0][1]
    assert "如果某个词、短句或英文来自参考图原文，必须删除或改写到完全不可识别" in calls[0][1]
    assert "书名标题排版必须 100% 抄到位" in calls[0][1]
    assert "版式骨架必须贴着母版" in calls[0][1]
    assert "色彩结构必须贴着母版" in calls[0][1]
    assert "这部分保留抄袭的魂魄和大部分外形" in calls[0][1]
    assert "主视觉必须替换，不能直接沿用母版里的具体视觉元素" in calls[0][1]
    assert "核心元素和装饰替换是硬约束" in calls[0][1]
    assert "在主正统出版分类中，只允许抄母版的排版、配色和字体/标题排版设计语法" in calls[0][1]
    assert "允许强参考的范围只有：版心结构、留白比例、标题字形气质" in calls[0][1]
    assert "除此之外所有元素都必须更换" in calls[0][1]
    assert "不能像母版换皮" in calls[0][1]
    assert "角标、边栏、贴纸、徽章、印章、圆点、箭头、气泡" in calls[0][1]
    assert "必须换成新的形状、新的组合、新的局部语义" in calls[0][1]
    assert "只保留“视觉角色”，不保留“具体身份”" in calls[0][1]
    assert "最终不能让人一眼认出原参考封面里的猫、椅子、头像" in calls[0][1]
    assert "如果能被认出是某张参考封面的换皮、描摹或套壳，也视为失败" in calls[0][1]
    assert "最多 0 到 1 句短金句" in calls[0][1]
    assert "禁止多段营销文案、课程口号、长清单和密集小字" in calls[0][1]
    assert "版权替换规则" in calls[0][1]
    assert "不要做成随机 AI 图、PPT 课程封面、知识付费海报、公众号长图、电商主图、网文封面" in calls[0][1]
    assert "不要脏点、坑洼、破损、斑驳、划痕、掉墨、褶皱、旧纸感或做旧效果" in calls[0][1]
    assert "标题字形气质、字重、字号比例、分行节奏、横排/竖排/半竖排关系、字距行距" in calls[0][1]
    assert "当前书的特色只来自书名、作者、简介语义和重画后的主视觉内容" in calls[0][1]
    assert "#FFFFFF 数字白" in calls[0][1]
    assert "不要米白、奶油白、暖白、灰白或纸白" in calls[0][1]
    assert "不要任何纸张纹理、纤维感、印刷颗粒、噪点、做旧感、扫描感" in calls[0][1]
    assert "不要模拟实体纸书摄影效果" in calls[0][1]
    assert "高级感灰滤镜" in calls[0][1]
    assert "2450*3500" in calls[0][1]
    assert "允许轻微纸张肌理" not in calls[0][1]
    assert "本书差异化视觉方案" not in calls[0][1]
    assert "正统出版风格 DNA" not in calls[0][1]
    assert "可以选择其中一张或几张作为版式方向参考" not in calls[0][1]
    assert "先选母版 → 再抄版式" not in calls[0][1]
    assert "选择它作为主要版式母版" not in calls[0][1]
    assert "正统出版封面第二次 image-to-image 精修工作流" in calls[1][1]
    assert "增加 0 到 1 处很小的英文辅助排版" in calls[1][1]
    assert "核心元素和装饰复查" in calls[1][1]
    assert "精修时执行主正统出版的最终复查" in calls[1][1]
    assert "只能保留排版、配色和字体/标题排版" in calls[1][1]
    assert "2450*3500" in calls[1][1]
    assert "书名英文翻译、主题英文关键词或极短英文副标题" in calls[1][1]
    assert "建议 1 到 4 个英文单词" in calls[1][1]
    assert "中文主书名必须仍然是“风经过房间”，逐字完全一致" in calls[1][1]
    assert "精修时必须再次清除参考封面原始文字信息" in calls[1][1]
    assert "不得与原封面文字内容重合" in calls[1][1]
    assert "不要多字，不要少字，不要改标点" in calls[1][1]
    assert "英文辅助信息只能是很小的装饰性副信息" in calls[1][1]
    assert "不能替代、覆盖、扩写或改写中文主书名" in calls[1][1]
    assert "微调字距、行距、基线、标题块边界" in calls[1][1]
    assert "不要重新构图，不要换风格方向" in calls[1][1]
    assert "不要把它改回参考封面的具体猫、椅子、头像" in calls[1][1]

    assert (tmp_path / "orthodox_reference_samples.json").exists()
    assert (tmp_path / "orthodox_reference_sequence.json").exists()
    samples = json.loads((tmp_path / "orthodox_reference_samples.json").read_text(encoding="utf-8"))
    assert len(samples) == 1
    assert samples[0]["title"] == "reference_01"
    sequence = json.loads((tmp_path / "orthodox_reference_sequence.json").read_text(encoding="utf-8"))
    assert sequence["policy"] == "global_zip_order_single_reference"
    assert sequence["selected_sequence_index"] == 1
    state = json.loads((state_dir / "sequence.json").read_text(encoding="utf-8"))
    assert state["policy"] == "global_zip_order_cycle"
    assert state["last_sequence_index"] == 1
    assert state["next_sequence_index"] == 2
    assert state["total_allocations"] == 1
    assert (tmp_path / "draft_cover.png").exists()
    assert (tmp_path / "cover.png").exists()
    assert (tmp_path / "cover_refine_prompt.txt").exists()
    assert (tmp_path / "cover_refine_response.json").exists()
    assert not (tmp_path / "orthodox_reference_board.png").exists()
    style_payload = json.loads((tmp_path / "cover_style_brief.json").read_text(encoding="utf-8"))
    assert style_payload["workflow"] == "orthodox_direct_single_reference_imitation"
    assert style_payload["reference_site"] == "本地正统出版ZIP参考封面库 + 全局顺序单母版结构强参考"
    assert "只抄排版、配色和字体/标题排版" in style_payload["brand_style_direction"]
    assert "重画替换核心视觉元素和装饰元素" in style_payload["brand_style_direction"]
    assert style_payload["element_replacement_profile"]["label"] == "正统出版"
    assert style_payload["element_replacement_profile"]["level"] == "主版本：平衡替换"
    assert style_payload["refine_step"]["workflow"] == "orthodox_direct_image_edit_refine"
    assert "少量英文" in style_payload["generation_prompt_addendum"]
    assert "visual_plan" not in style_payload


def test_literature_cover_refine_uncertain_uses_draft_as_final(

    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[str | None] = []
    _patch_orthodox_sequence_state(monkeypatch, tmp_path)
    zip_path = tmp_path / "reference_covers.zip"
    with ZipFile(zip_path, "w") as archive:
        archive.writestr("cover_00.png", PNG_BYTES)

    def fail_style_brief(*args, **kwargs):
        raise AssertionError("literature direct workflow should not build the old style brief")

    def fake_generation(prompt: str, **kwargs) -> dict[str, object]:
        raise AssertionError("literature direct workflow should use image edit, not text generation")

    def fake_edit(image_paths, prompt: str, **kwargs) -> dict[str, object]:
        stage = kwargs.get("stage")
        calls.append(stage)
        if stage == "orthodox_refine":
            raise cover.ImageApiUncertainError("server disconnected")
        return {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]}

    monkeypatch.setattr(cover, "_literature_reference_zip_path", lambda: zip_path)
    monkeypatch.setattr(cover, "build_cover_style_brief", fail_style_brief)
    monkeypatch.setattr(cover, "build_refine_cover_prompt", fail_style_brief)
    monkeypatch.setattr(cover, "_request_image_generation_response", fake_generation)
    monkeypatch.setattr(cover, "_request_image_edit_response", fake_edit)

    result = cover.generate_cover_file(
        "风经过房间",
        "@最终简介\n一本关于城市房间和自我告别的文学随笔。",
        tmp_path,
        category="正统出版",
        author="作者A",
    )

    assert result == tmp_path / "cover.png"
    assert calls == ["draft_reference", "orthodox_refine"]
    assert (tmp_path / "draft_cover.png").exists()
    assert (tmp_path / "cover.png").exists()
    assert _image_size(tmp_path / "cover.png") == cover._FINAL_COVER_SIZE  # noqa: SLF001
    refine_error = tmp_path / "cover_refine_error.txt"
    assert refine_error.exists()
    assert "二次精修结果不确定，已使用第一步初稿作为最终封面" in refine_error.read_text(encoding="utf-8")


def test_literature_cover_direct_reuses_ordered_reference_until_force(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = _patch_orthodox_sequence_state(monkeypatch, tmp_path)
    zip_path = tmp_path / "reference_covers.zip"
    with ZipFile(zip_path, "w") as archive:
        for index in range(18):
            archive.writestr(f"cover_{index:02d}.png", PNG_BYTES)


    edit_calls = 0

    def fake_edit(image_paths, prompt: str, **kwargs) -> dict[str, object]:
        nonlocal edit_calls
        edit_calls += 1
        if edit_calls == 1:
            raise cover.ImageApiTransientError("temporary image failure")
        return {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]}

    monkeypatch.setattr(cover, "_literature_reference_zip_path", lambda: zip_path)
    monkeypatch.setattr(cover, "_request_image_edit_response", fake_edit)
    monkeypatch.setattr(
        cover,
        "_request_image_generation_response",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("generation should not run")),
    )

    with pytest.raises(cover.ImageApiTransientError):
        cover.generate_cover_file(
            "风经过房间",
            "@最终简介\n一本关于城市房间和自我告别的文学随笔。",
            tmp_path,
            category="正统出版",
            author="作者L",
        )

    first_samples = json.loads((tmp_path / "orthodox_reference_samples.json").read_text(encoding="utf-8"))
    assert len(first_samples) == 1
    assert first_samples[0]["title"] == "cover_00"
    state_after_failure = json.loads((state_dir / "sequence.json").read_text(encoding="utf-8"))
    assert state_after_failure["last_sequence_index"] == 1
    assert state_after_failure["next_sequence_index"] == 2
    assert state_after_failure["total_allocations"] == 1


    result = cover.generate_cover_file(
        "风经过房间",
        "@最终简介\n一本关于城市房间和自我告别的文学随笔。",
        tmp_path,
        category="正统出版",
        author="作者L",
    )

    second_samples = json.loads((tmp_path / "orthodox_reference_samples.json").read_text(encoding="utf-8"))
    assert result == tmp_path / "cover.png"
    assert second_samples == first_samples
    state_after_retry = json.loads((state_dir / "sequence.json").read_text(encoding="utf-8"))
    assert state_after_retry == state_after_failure


    cover.generate_cover_file(
        "风经过房间",
        "@最终简介\n一本关于城市房间和自我告别的文学随笔。",
        tmp_path,
        category="正统出版",
        author="作者L",
        force=True,
    )

    forced_samples = json.loads((tmp_path / "orthodox_reference_samples.json").read_text(encoding="utf-8"))
    assert forced_samples != first_samples
    assert forced_samples[0]["title"] == "cover_01"
    state_after_force = json.loads((state_dir / "sequence.json").read_text(encoding="utf-8"))
    assert state_after_force["last_sequence_index"] == 2
    assert state_after_force["next_sequence_index"] == 3
    assert state_after_force["total_allocations"] == 2


def test_literature_cover_direct_does_not_reuse_cached_image_when_prompt_changed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _patch_orthodox_sequence_state(monkeypatch, tmp_path)
    zip_path = tmp_path / "reference_covers.zip"
    with ZipFile(zip_path, "w") as archive:
        archive.writestr("cover_00.png", PNG_BYTES)

    edit_calls = 0

    def fake_edit(image_paths, prompt: str, **kwargs) -> dict[str, object]:
        nonlocal edit_calls
        edit_calls += 1
        return {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]}

    monkeypatch.setattr(cover, "_literature_reference_zip_path", lambda: zip_path)
    monkeypatch.setattr(cover, "_request_image_edit_response", fake_edit)
    (tmp_path / "cover_draft_prompt.txt").write_text("old prompt from previous workflow", encoding="utf-8")
    (tmp_path / "cover_draft_response.json").write_text(
        json.dumps({"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]}),
        encoding="utf-8",
    )

    cover.generate_cover_file(
        "风经过房间",
        "@最终简介\n一本关于城市房间和自我告别的文学随笔。",
        tmp_path,
        category="正统出版",
        author="作者L",
    )

    assert edit_calls == 2
    assert "old prompt from previous workflow" not in (tmp_path / "cover_draft_prompt.txt").read_text(encoding="utf-8")


def test_literature_cover_direct_cycles_zip_references_globally_across_new_tasks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = _patch_orthodox_sequence_state(monkeypatch, tmp_path)
    zip_path = tmp_path / "reference_covers.zip"
    with ZipFile(zip_path, "w") as archive:
        for index in range(3):
            archive.writestr(f"cover_{index:02d}.png", PNG_BYTES)

    monkeypatch.setattr(cover, "_literature_reference_zip_path", lambda: zip_path)
    monkeypatch.setattr(
        cover,
        "_request_image_edit_response",
        lambda *args, **kwargs: {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]},
    )
    monkeypatch.setattr(
        cover,
        "_request_image_generation_response",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("generation should not run")),
    )

    cases = (
        ("job_a", "001_测试书.png", "cover_00", 1),
        ("job_b", "001_测试书.png", "cover_01", 2),
        ("job_c", "001_测试书.png", "cover_02", 3),
        ("job_d", "001_测试书.png", "cover_00", 1),
    )
    for job_name, filename, expected_title, expected_index in cases:
        job_dir = tmp_path / job_name
        job_dir.mkdir()
        output_path = job_dir / filename
        cover.generate_cover_file_from_metadata(
            {
                "title": f"测试书{job_name}",
                "author": "作者",
                "category": "正统出版",
                "intro": "用于验证正统出版 ZIP 顺序轮转。",
                "item_index": 1,
            },
            output_path,
        )
        work_dir = output_path.parent / f".{output_path.stem}_work"
        samples = json.loads((work_dir / "orthodox_reference_samples.json").read_text(encoding="utf-8"))
        metadata_used = json.loads((work_dir / "metadata_used.json").read_text(encoding="utf-8"))
        sequence = json.loads((work_dir / "orthodox_reference_sequence.json").read_text(encoding="utf-8"))
        assert len(samples) == 1
        assert samples[0]["title"] == expected_title
        assert sequence["selected_sequence_index"] == expected_index
        assert "item_index" in metadata_used["source_metadata"]

    state = json.loads((state_dir / "sequence.json").read_text(encoding="utf-8"))
    assert state["members_count"] == 3
    assert isinstance(state["zip_fingerprint"], str)
    assert state["last_sequence_index"] == 1
    assert state["next_sequence_index"] == 2
    assert state["total_allocations"] == 4


def test_literature_cover_direct_resets_global_sequence_when_reference_zip_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = _patch_orthodox_sequence_state(monkeypatch, tmp_path)
    zip_path = tmp_path / "reference_covers.zip"
    with ZipFile(zip_path, "w") as archive:
        for index in range(3):
            archive.writestr(f"old_cover_{index:02d}.png", PNG_BYTES)

    monkeypatch.setattr(cover, "_literature_reference_zip_path", lambda: zip_path)
    monkeypatch.setattr(
        cover,
        "_request_image_edit_response",
        lambda *args, **kwargs: {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]},
    )
    monkeypatch.setattr(
        cover,
        "_request_image_generation_response",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("generation should not run")),
    )

    for index in range(2):
        cover.generate_cover_file(
            f"旧库测试{index}",
            "@最终简介\n旧库顺序状态。",
            tmp_path / f"old_job_{index}",
            category="正统出版",
            author="作者",
        )

    old_state = json.loads((state_dir / "sequence.json").read_text(encoding="utf-8"))
    assert old_state["members_count"] == 3
    assert old_state["next_sequence_index"] == 3
    assert old_state["total_allocations"] == 2

    with ZipFile(zip_path, "w") as archive:
        for index in range(2):
            archive.writestr(f"new_cover_{index:02d}.png", PNG_BYTES)

    cover.generate_cover_file(
        "新库测试",
        "@最终简介\n新库应从第一张开始。",
        tmp_path / "new_job",
        category="正统出版",
        author="作者",
    )

    samples = json.loads((tmp_path / "new_job" / "orthodox_reference_samples.json").read_text(encoding="utf-8"))
    sequence = json.loads((tmp_path / "new_job" / "orthodox_reference_sequence.json").read_text(encoding="utf-8"))
    new_state = json.loads((state_dir / "sequence.json").read_text(encoding="utf-8"))
    assert samples[0]["title"] == "new_cover_00"
    assert sequence["members_count"] == 2
    assert sequence["selected_sequence_index"] == 1
    assert sequence["zip_fingerprint"] != old_state["zip_fingerprint"]
    assert new_state["members_count"] == 2
    assert new_state["last_sequence_index"] == 1
    assert new_state["next_sequence_index"] == 2
    assert new_state["total_allocations"] == 1


def test_literature_cover_direct_global_sequence_lock_allocates_unique_references(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = _patch_orthodox_sequence_state(monkeypatch, tmp_path)
    zip_path = tmp_path / "reference_covers.zip"
    with ZipFile(zip_path, "w") as archive:
        for index in range(10):
            archive.writestr(f"cover_{index:02d}.png", PNG_BYTES)

    monkeypatch.setattr(cover, "_literature_reference_zip_path", lambda: zip_path)

    def reserve(index: int) -> str:
        work_dir = tmp_path / f"parallel_{index}"
        work_dir.mkdir()
        selected = cover._selected_literature_direct_reference_members(  # noqa: SLF001
            work_dir,
            force=True,
        )
        return Path(selected[0]).stem

    with ThreadPoolExecutor(max_workers=8) as executor:
        selected_titles = list(executor.map(reserve, range(8)))

    assert set(selected_titles) == {f"cover_{index:02d}" for index in range(8)}
    state = json.loads((state_dir / "sequence.json").read_text(encoding="utf-8"))
    assert state["members_count"] == 10
    assert state["total_allocations"] == 8
    assert state["next_sequence_index"] == 9


def test_generate_cover_file_from_metadata_force_bypasses_existing_cover(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output_path = tmp_path / "001_测试书.png"
    output_path.write_bytes(b"old cover")
    calls: list[bool] = []

    def fake_generate_cover_file(*, output_dir: Path, force: bool, **kwargs) -> Path:
        calls.append(force)
        output_dir.mkdir(parents=True, exist_ok=True)
        generated = output_dir / "cover.png"
        generated.write_bytes(b"new cover")
        return generated

    monkeypatch.setattr(cover, "generate_cover_file", fake_generate_cover_file)

    result = cover.generate_cover_file_from_metadata(
        {"title": "测试书", "author": "作者", "category": "正统出版", "intro": "简介"},
        output_path,
        force=True,
    )

    assert result == output_path
    assert calls == [True]
    assert output_path.read_bytes() == b"new cover"



def test_generate_cover_file_does_not_downgrade_to_draft_on_refine_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fake_generation(prompt: str, **kwargs) -> dict[str, object]:
        return {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]}

    def fake_edit(image_path: Path, prompt: str, **kwargs) -> dict[str, object]:
        raise RuntimeError("edit failed")

    monkeypatch.setattr(cover, "_request_image_generation_response", fake_generation)
    monkeypatch.setattr(cover, "_request_image_edit_response", fake_edit)

    try:
        cover.generate_cover_file(
            "醒来以后",
            "@最终简介\n一本心理自助书。",
            tmp_path,
            category="illustration",
            author="测试作者",
        )
    except RuntimeError as exc:
        assert "封面精修失败" in str(exc)
    else:
        raise AssertionError("refine failure should not be silently downgraded")

    assert (tmp_path / "draft_cover.png").exists()
    assert not (tmp_path / "cover.png").exists()
    assert (tmp_path / "cover_refine_error.txt").exists()


def test_generate_cover_file_cancels_before_refine_without_extra_image_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cancelled = False
    edit_called = False

    def fake_generation(prompt: str, **kwargs) -> dict[str, object]:
        nonlocal cancelled
        cancelled = True
        return {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]}

    def fake_edit(image_path: Path, prompt: str, **kwargs) -> dict[str, object]:
        nonlocal edit_called
        edit_called = True
        return {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]}

    monkeypatch.setattr(cover, "_request_image_generation_response", fake_generation)
    monkeypatch.setattr(cover, "_request_image_edit_response", fake_edit)

    with pytest.raises(cover.CoverGenerationCancelled):
        cover.generate_cover_file(
            "醒来以后",
            "@最终简介\n一本心理自助书。",
            tmp_path,
            category="illustration",
            author="测试作者",
            should_abort=lambda: cancelled,
        )

    assert not edit_called
    assert not (tmp_path / "cover.png").exists()


def test_cancellable_image_request_closes_client_and_releases_wait(monkeypatch) -> None:
    class FakeHttp:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    fake = FakeHttp()

    def slow_post(*args, **kwargs):
        time.sleep(5)
        return {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]}

    monkeypatch.setattr(cover, "_post_image_request", slow_post)

    with pytest.raises(cover.CoverGenerationCancelled):
        cover._post_image_request_with_cancellation(  # noqa: SLF001
            fake,
            "https://api.example.test/images/generations",
            "key",
            json_payload={"prompt": "x"},
            should_abort=lambda: True,
            stage="unit-test",
        )

    assert fake.closed


def test_image_request_hard_timeout_closes_client(monkeypatch) -> None:
    class FakeHttp:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    fake = FakeHttp()

    def slow_post(*args, **kwargs):
        time.sleep(3)
        return {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]}

    monkeypatch.setattr(cover.settings, "IMAGE_API_HARD_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(cover, "_post_image_request", slow_post)

    with pytest.raises(cover.ImageApiUncertainError, match="hard timeout"):
        cover._post_image_request_with_cancellation(  # noqa: SLF001
            fake,
            "https://api.example.test/images/generations",
            "key",
            json_payload={"prompt": "x"},
            stage="unit-test",
        )

    assert fake.closed


def test_image_request_retries_transient_520(monkeypatch) -> None:
    class FakeHttp:
        def __init__(self) -> None:
            self.calls = 0

        def post(self, endpoint: str, **kwargs):
            self.calls += 1
            request = httpx.Request("POST", endpoint)
            if self.calls == 1:
                return httpx.Response(520, text="temporary edge error", request=request)
            return httpx.Response(
                200,
                json={"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]},
                request=request,
            )

    fake = FakeHttp()
    monkeypatch.setattr(cover.settings, "IMAGE_API_MAX_RETRIES", 2)
    monkeypatch.setattr(cover.time, "sleep", lambda _seconds: None)

    response_data = cover._post_image_request(fake, "https://api.example.test/images/edits", "key")  # noqa: SLF001
    result = cover._decode_image_response(response_data)  # noqa: SLF001

    assert result == PNG_BYTES
    assert fake.calls == 2


def test_decode_image_response_without_image_is_permanent() -> None:
    with pytest.raises(cover.ImageApiPermanentError, match="neither b64_json nor url"):
        cover._decode_image_response({"data": [{}]})  # noqa: SLF001

    with pytest.raises(cover.ImageApiPermanentError, match="no image data"):
        cover._decode_image_response({"data": []})  # noqa: SLF001


def test_image_generation_sends_full_gpt_image_prompt_limit(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class DummyGate:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_gate(*args, **kwargs):
        return DummyGate()

    def fake_credentials():
        return "key", "https://api.example.test/v1", ""

    def fake_post(_http, _endpoint, _api_key, *, json_payload=None, **_kwargs):
        captured["prompt"] = json_payload["prompt"]
        return {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]}

    monkeypatch.setattr(cover, "distributed_semaphore", fake_gate)
    monkeypatch.setattr(cover, "_resolve_image_api_credentials", fake_credentials)
    monkeypatch.setattr(cover, "_post_image_request", fake_post)

    cover._request_image_generation("x" * 40000)  # noqa: SLF001

    assert len(captured["prompt"]) == 32000


def test_image_edit_sends_multiple_images_as_array_fields(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}
    image_a = tmp_path / "a.png"
    image_b = tmp_path / "b.jpg"
    image_a.write_bytes(PNG_BYTES)
    image_b.write_bytes(PNG_BYTES)

    class DummyGate:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_gate(*args, **kwargs):
        return DummyGate()

    def fake_credentials():
        return "key", "https://api.example.test/v1", ""

    def fake_post(_http, _endpoint, _api_key, *, data=None, files=None, **_kwargs):
        captured["prompt"] = data["prompt"]
        captured["files"] = files
        return {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]}

    monkeypatch.setattr(cover, "distributed_semaphore", fake_gate)
    monkeypatch.setattr(cover, "_resolve_image_api_credentials", fake_credentials)
    monkeypatch.setattr(cover, "_post_image_request", fake_post)

    result = cover._request_image_edit((image_a, image_b), "x")  # noqa: SLF001

    assert result == PNG_BYTES
    files = captured["files"]
    assert isinstance(files, list)
    assert [field for field, _payload in files] == ["image[]", "image[]"]
    assert files[0][1][2] == "image/png"
    assert files[1][1][2] == "image/jpeg"


def test_generate_cover_file_retries_refine_from_existing_draft_without_rebuilding_brief(
    tmp_path: Path,
    monkeypatch,
) -> None:
    draft = tmp_path / "draft_cover.png"
    refine_prompt = tmp_path / "cover_refine_prompt.txt"
    draft.write_bytes(PNG_BYTES)
    refine_prompt.write_text("existing refine prompt", encoding="utf-8")

    def fail_style_brief(*args, **kwargs):
        raise AssertionError("style brief should not be rebuilt when retrying refine")

    def fake_edit(image_path: Path, prompt: str, **kwargs) -> dict[str, object]:
        assert image_path == draft
        assert prompt == "existing refine prompt"
        return {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]}

    monkeypatch.setattr(cover, "build_cover_style_brief", fail_style_brief)
    monkeypatch.setattr(cover, "_request_image_edit_response", fake_edit)

    result = cover.generate_cover_file(
        "测试书名",
        "@最终简介\n测试简介",
        tmp_path,
        category="illustration_crayon",
        author="测试作者",
    )

    assert result == tmp_path / "cover.png"
    assert _image_size(result) == cover._FINAL_COVER_SIZE  # noqa: SLF001


def test_generate_cover_file_reuses_cached_refine_url_without_new_image_api(
    tmp_path: Path,
    monkeypatch,
) -> None:
    generation_calls = 0
    edit_calls = 0
    download_calls = 0

    def fake_generation(prompt: str, **kwargs) -> dict[str, object]:
        nonlocal generation_calls
        generation_calls += 1
        return {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]}

    def fake_edit(image_path: Path, prompt: str, **kwargs) -> dict[str, object]:
        nonlocal edit_calls
        edit_calls += 1
        return {"data": [{"url": "https://cdn.example.test/cover.png"}]}

    def fake_download(url: str, **kwargs) -> bytes:
        nonlocal download_calls
        assert url == "https://cdn.example.test/cover.png"
        download_calls += 1
        if download_calls == 1:
            raise cover.ImageApiTransientError("cdn not ready")
        return PNG_BYTES

    monkeypatch.setattr(cover, "_request_image_generation_response", fake_generation)
    monkeypatch.setattr(cover, "_request_image_edit_response", fake_edit)
    monkeypatch.setattr(cover, "_download_image_result", fake_download)

    with pytest.raises(cover.ImageApiTransientError, match="cdn not ready"):
        cover.generate_cover_file(
            "cached retry cover",
            "@final intro\ncached retry intro",
            tmp_path,
            category="illustration_crayon",
            author="test author",
        )

    assert generation_calls == 1
    assert edit_calls == 1
    assert download_calls == 1
    assert (tmp_path / "cover_refine_response.json").exists()
    assert not (tmp_path / "cover.png").exists()

    result = cover.generate_cover_file(
        "cached retry cover",
        "@final intro\ncached retry intro",
        tmp_path,
        category="illustration_crayon",
        author="test author",
    )

    assert result == tmp_path / "cover.png"
    assert _image_size(result) == cover._FINAL_COVER_SIZE  # noqa: SLF001
    assert generation_calls == 1
    assert edit_calls == 1
    assert download_calls == 2


def test_delete_cover_job_items_marks_single_cover_deleted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(cover_jobs, "COVER_JOBS_ROOT", tmp_path)
    status = cover_jobs.create_cover_job(
        "user-1",
        "covers.csv",
        [
            {"title": "第一本书", "author": "作者A", "category": "social_science", "category_label": "视觉冲击"},
            {"title": "第二本书", "author": "作者B", "category": "life_philosophy", "category_label": "人生哲学"},
        ],
        default_category="social_science",
    )
    job_id = status["job_id"]
    image = cover_jobs.image_path(job_id, 1, "第一本书")
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(PNG_BYTES)

    updated = cover_jobs.delete_cover_job_items(job_id, [1])
    public = cover_jobs.public_cover_job_status(updated)
    raw = cover_jobs.read_status(job_id)

    assert not image.exists()
    assert raw["items"][0]["status"] == "deleted"
    assert [item["title"] for item in public["items"]] == ["第二本书"]
    assert public["total"] == 1


def test_retry_cover_job_items_preserves_draft_and_requeues_failed_cover(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(cover_jobs, "COVER_JOBS_ROOT", tmp_path)
    status = cover_jobs.create_cover_job(
        "user-1",
        "covers.csv",
        [
            {"title": "第一本书", "author": "作者A", "category": "social_science", "category_label": "视觉冲击"},
        ],
        default_category="social_science",
    )
    job_id = status["job_id"]
    image = cover_jobs.image_path(job_id, 1, "第一本书")
    work_dir = image.parent / f".{image.stem}_work"
    work_dir.mkdir(parents=True, exist_ok=True)
    draft = work_dir / "draft_cover.png"
    draft.write_bytes(PNG_BYTES)
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(PNG_BYTES)

    status["items"][0].update(status="failed", error="temporary 520", auto_retry_count=3)
    cover_jobs.write_status(job_id, status)

    updated = cover_jobs.retry_cover_job_items(job_id, [1])

    assert updated["items"][0]["status"] == "pending"
    assert updated["items"][0]["error"] is None
    assert updated["items"][0]["auto_retry_count"] == 0
    assert not image.exists()
    assert draft.exists()


def test_regenerate_cover_job_item_forces_new_generation_even_if_old_image_remains(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(cover_jobs, "COVER_JOBS_ROOT", tmp_path)
    monkeypatch.setattr(cover_tasks, "COVER_JOBS_ROOT", tmp_path)
    status = cover_jobs.create_cover_job(
        "user-1",
        "covers.csv",
        [
            {"title": "第一本书", "author": "作者A", "category": "正统出版", "category_label": "正统出版"},
        ],
        default_category="literature",
    )
    job_id = status["job_id"]
    image = cover_jobs.image_path(job_id, 1, "第一本书")
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"old cover")
    status["items"][0].update(status="completed", image_url=f"/api/v1/covers/jobs/{job_id}/items/1/image")
    cover_jobs.write_status(job_id, status)

    regenerated = cover_jobs.regenerate_cover_job_items(job_id, [1])
    # Simulate a stale file left behind by a cache/path race; worker must still force a new image.
    image.write_bytes(b"old cover")
    seen_force: list[bool] = []

    def fake_generate_cover_file_from_metadata(metadata: dict[str, str], output_path: Path, **kwargs) -> Path:
        seen_force.append(bool(kwargs.get("force")))
        output_path.write_bytes(b"new cover")
        return output_path

    monkeypatch.setattr(cover_tasks, "generate_cover_file_from_metadata", fake_generate_cover_file_from_metadata)

    result = cover_tasks.generate_cover_job.run(regenerated["job_id"])
    final = cover_jobs.read_status(job_id)

    assert result["status"] == "completed"
    assert seen_force == [True]
    assert image.read_bytes() == b"new cover"
    assert final["items"][0]["status"] == "completed"
    assert "force_regenerate" not in final["items"][0]


def test_cover_job_zip_uses_title_names_without_leading_index(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cover_jobs, "COVER_JOBS_ROOT", tmp_path)
    status = cover_jobs.create_cover_job(
        "user-1",
        "covers.csv",
        [
            {"title": "第一本书", "author": "作者A", "category": "social_science", "category_label": "视觉冲击"},
            {"title": "第一本书", "author": "作者A", "category": "social_science", "category_label": "视觉冲击"},
        ],
        default_category="social_science",
    )
    job_id = status["job_id"]
    for item in status["items"]:
        item["status"] = "completed"
        image = cover_jobs.image_path(job_id, int(item["index"]), str(item["title"]))
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(PNG_BYTES)
    cover_jobs.write_status(job_id, status)

    archive_bytes = cover_jobs.build_zip(job_id, cover_jobs.read_status(job_id))

    with ZipFile(BytesIO(archive_bytes)) as archive:
        assert archive.namelist() == ["第一本书.png", "第一本书_2.png"]


def test_cover_batch_public_status_exposes_uncertain_items_without_auto_completion() -> None:
    now = datetime.now(timezone.utc)
    job = CoverBatchJob(
        id="batch123",
        status="failed",
        progress=0,
        total=3,
        completed=0,
        failed=0,
        uncertain=0,
        current_step="done with uncertainty",
        source_filename="covers.csv",
        default_category="social_science",
        default_category_label="视觉冲击",
        created_at=now,
        updated_at=now,
    )
    job.items = [
        CoverBatchItem(item_index=1, title="完成", status="completed", source_metadata={}),
        CoverBatchItem(item_index=2, title="待确认", status="uncertain", error="no duplicate billing", source_metadata={}),
        CoverBatchItem(item_index=3, title="失败", status="failed", source_metadata={}),
    ]

    public = cover_batch_jobs.public_cover_batch_job_status(job)

    assert public["completed"] == 1
    assert public["failed"] == 1
    assert public["uncertain"] == 1
    assert public["progress"] == 100
    assert public["items"][1]["status"] == "uncertain"
    assert public["items"][1]["error"] is None


def test_cover_batch_public_status_hides_internal_retry_errors_for_active_items() -> None:
    now = datetime.now(timezone.utc)
    job = CoverBatchJob(
        id="batch-retry",
        status="processing",
        progress=0,
        total=2,
        completed=0,
        failed=0,
        uncertain=0,
        source_filename="covers.csv",
        created_at=now,
        updated_at=now,
    )
    job.items = [
        CoverBatchItem(
            item_index=1,
            title="等待重试",
            status="pending",
            error="图像服务连接波动，系统自动重试中（第 1 次）",
            source_metadata={},
        ),
        CoverBatchItem(
            item_index=2,
            title="真正失败",
            status="failed",
            error="真实失败原因",
            source_metadata={},
        ),
    ]

    public = cover_batch_jobs.public_cover_batch_job_status(job)

    assert public["items"][0]["status"] == "pending"
    assert public["items"][0]["error"] is None
    assert public["items"][1]["status"] == "failed"
    assert public["items"][1]["error"] == "真实失败原因"


def test_cover_batch_uncertain_result_uses_existing_retry_queue(monkeypatch) -> None:
    calls: list[tuple[str, uuid.UUID, str]] = []
    item_id = uuid.uuid4()

    def fake_retryable(db, item_uuid, exc):
        calls.append(("retryable", item_uuid, str(exc)))
        return "retry"

    def fake_uncertain(*args, **kwargs):
        raise AssertionError("uncertain should be routed through the existing retryable queue")

    monkeypatch.setattr(cover_tasks.settings, "COVER_BATCH_UNCERTAIN_AUTO_RETRY", True)
    monkeypatch.setattr(cover_tasks, "mark_cover_item_retryable", fake_retryable)
    monkeypatch.setattr(cover_tasks, "mark_cover_item_uncertain", fake_uncertain)

    result = cover_tasks._mark_cover_batch_uncertain_result(  # noqa: SLF001
        object(),
        item_id,
        cover.ImageApiUncertainError("server disconnected"),
    )

    assert result == "retry"
    assert calls == [("retryable", item_id, "server disconnected")]


def test_cover_batch_uncertain_result_can_still_mark_uncertain_when_disabled(monkeypatch) -> None:
    calls: list[tuple[str, uuid.UUID, str]] = []
    item_id = uuid.uuid4()

    def fake_retryable(*args, **kwargs):
        raise AssertionError("retryable queue should not be used when uncertain auto retry is disabled")

    def fake_uncertain(db, item_uuid, exc):
        calls.append(("uncertain", item_uuid, str(exc)))
        return "failed"

    monkeypatch.setattr(cover_tasks.settings, "COVER_BATCH_UNCERTAIN_AUTO_RETRY", False)
    monkeypatch.setattr(cover_tasks, "mark_cover_item_retryable", fake_retryable)
    monkeypatch.setattr(cover_tasks, "mark_cover_item_uncertain", fake_uncertain)

    result = cover_tasks._mark_cover_batch_uncertain_result(  # noqa: SLF001
        object(),
        item_id,
        cover.ImageApiUncertainError("server disconnected"),
    )

    assert result == "failed"
    assert calls == [("uncertain", item_id, "server disconnected")]


def test_cover_batch_requeues_existing_uncertain_items_with_retry_scheduler(monkeypatch) -> None:
    item_ids = [uuid.uuid4(), uuid.uuid4()]
    calls: list[uuid.UUID] = []

    class FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def all(self):
            return [(item_ids[0],), (item_ids[1],)]

    class FakeDb:
        def query(self, *args, **kwargs):
            return FakeQuery()

    def fake_retryable(db, item_uuid, exc):
        assert isinstance(exc, cover.ImageApiUncertainError)
        calls.append(item_uuid)
        return "retry"

    monkeypatch.setattr(cover_tasks.settings, "COVER_BATCH_UNCERTAIN_AUTO_RETRY", True)
    monkeypatch.setattr(cover_tasks, "mark_cover_item_retryable", fake_retryable)

    requeued = cover_tasks._requeue_uncertain_cover_batch_items(FakeDb(), "job-1")  # noqa: SLF001

    assert requeued == 2
    assert calls == item_ids


def test_cover_batch_zip_uses_title_names_without_leading_index(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cover_batch_jobs, "COVER_BATCH_ROOT", tmp_path)
    now = datetime.now(timezone.utc)
    job_id = "batchzip"
    job = CoverBatchJob(
        id=job_id,
        status="completed",
        current_step="done",
        created_at=now,
        updated_at=now,
    )
    job.items = [
        CoverBatchItem(item_index=1, title="同名书", status="completed", source_metadata={}),
        CoverBatchItem(item_index=2, title="同名书", status="completed", source_metadata={}),
        CoverBatchItem(item_index=3, title="失败书", status="failed", source_metadata={}),
    ]
    for item in job.items[:2]:
        image = cover_batch_jobs.image_path(job_id, int(item.item_index), str(item.title))
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(PNG_BYTES)
        item.image_path = str(image)

    archive_bytes = cover_batch_jobs.build_zip(job_id, job)

    with ZipFile(BytesIO(archive_bytes)) as archive:
        assert archive.namelist() == ["同名书.png", "同名书_2.png"]


def test_cover_job_advances_multiple_items_concurrently_without_image_api(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(cover_jobs, "COVER_JOBS_ROOT", tmp_path)
    monkeypatch.setattr(cover_tasks.settings, "COVER_JOB_MAX_CONCURRENT_ITEMS", 3)
    monkeypatch.setattr(cover_tasks.settings, "IMAGE_MAX_CONCURRENT_CALLS", 3)
    monkeypatch.setattr(cover_tasks.settings, "COVER_JOB_MAX_ITEMS_PER_RUN", 6)
    status = cover_jobs.create_cover_job(
        "user-1",
        "covers.csv",
        [
            {"title": f"第{i}本书", "author": "作者A", "category": "illustration_crayon", "category_label": "萌系治愈"}
            for i in range(1, 7)
        ],
        default_category="illustration_crayon",
    )

    active = 0
    peak = 0
    lock = Lock()

    def fake_generate_cover_file_from_metadata(metadata: dict[str, str], output_path: Path, **kwargs) -> Path:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.05)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(PNG_BYTES)
            return output_path
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(
        cover_tasks,
        "generate_cover_file_from_metadata",
        fake_generate_cover_file_from_metadata,
    )

    result = cover_tasks.generate_cover_job.run(status["job_id"])
    final = cover_jobs.read_status(status["job_id"])

    assert result["status"] == "completed"
    assert final["completed"] == 6
    assert final["failed"] == 0
    assert peak == 3


def test_cover_job_processes_large_uploads_in_bounded_batches(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(cover_jobs, "COVER_JOBS_ROOT", tmp_path)
    monkeypatch.setattr(cover_tasks.settings, "COVER_JOB_MAX_CONCURRENT_ITEMS", 2)
    monkeypatch.setattr(cover_tasks.settings, "IMAGE_MAX_CONCURRENT_CALLS", 2)
    monkeypatch.setattr(cover_tasks.settings, "COVER_JOB_MAX_ITEMS_PER_RUN", 2)
    status = cover_jobs.create_cover_job(
        "user-1",
        "covers.csv",
        [
            {
                "title": f"批量书 {i}",
                "author": "作者A",
                "category": "illustration_crayon",
                "category_label": "萌系治愈",
            }
            for i in range(1, 6)
        ],
        default_category="illustration_crayon",
    )
    dispatched: list[str] = []

    def fake_generate_cover_file_from_metadata(metadata: dict[str, str], output_path: Path, **kwargs) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(PNG_BYTES)
        return output_path

    monkeypatch.setattr(
        cover_tasks,
        "generate_cover_file_from_metadata",
        fake_generate_cover_file_from_metadata,
    )
    monkeypatch.setattr(cover_tasks.generate_cover_job, "delay", dispatched.append)

    result = cover_tasks.generate_cover_job.run(status["job_id"])
    final = cover_jobs.read_status(status["job_id"])

    assert result["reason"] == "next_batch_dispatched"
    assert dispatched == [status["job_id"]]
    assert final["status"] == "pending"
    assert final["completed"] == 2
    assert sum(1 for item in final["items"] if item["status"] == "pending") == 3


def test_cover_job_continues_ready_items_when_one_item_waits_to_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(cover_jobs, "COVER_JOBS_ROOT", tmp_path)
    monkeypatch.setattr(cover_tasks.settings, "COVER_JOB_MAX_CONCURRENT_ITEMS", 2)
    monkeypatch.setattr(cover_tasks.settings, "IMAGE_MAX_CONCURRENT_CALLS", 2)
    monkeypatch.setattr(cover_tasks.settings, "COVER_JOB_MAX_ITEMS_PER_RUN", 2)
    status = cover_jobs.create_cover_job(
        "user-1",
        "covers.csv",
        [
            {"title": f"retry book {i}", "author": "A", "category": "illustration_crayon", "category_label": "萌系治愈"}
            for i in range(1, 5)
        ],
        default_category="illustration_crayon",
    )
    dispatched: list[str] = []

    def fake_generate_cover_file_from_metadata(metadata: dict[str, str], output_path: Path, **kwargs) -> Path:
        if metadata["title"] == "retry book 1":
            raise cover.ImageApiTransientError("cdn not ready")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(PNG_BYTES)
        return output_path

    monkeypatch.setattr(cover_tasks, "generate_cover_file_from_metadata", fake_generate_cover_file_from_metadata)
    monkeypatch.setattr(cover_tasks.generate_cover_job, "delay", dispatched.append)

    result = cover_tasks.generate_cover_job.run(status["job_id"])
    final = cover_jobs.read_status(status["job_id"])

    assert result["reason"] == "next_batch_dispatched"
    assert dispatched == [status["job_id"]]
    assert final["items"][0]["status"] == "pending"
    assert final["items"][0]["retry_after"]
    assert final["items"][1]["status"] == "completed"
    assert final["items"][2]["status"] == "pending"
    assert final["items"][3]["status"] == "pending"


def test_cover_job_schedules_waiting_retry_without_celery_task_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(cover_jobs, "COVER_JOBS_ROOT", tmp_path)
    monkeypatch.setattr(cover_tasks.settings, "COVER_JOB_MAX_CONCURRENT_ITEMS", 1)
    monkeypatch.setattr(cover_tasks.settings, "IMAGE_MAX_CONCURRENT_CALLS", 1)
    monkeypatch.setattr(cover_tasks.settings, "COVER_TASK_RETRY_SECONDS", 12)
    status = cover_jobs.create_cover_job(
        "user-1",
        "covers.csv",
        [{"title": "retry only", "author": "A", "category": "illustration_crayon", "category_label": "萌系治愈"}],
        default_category="illustration_crayon",
    )
    scheduled: list[dict[str, object]] = []

    def fake_generate_cover_file_from_metadata(metadata: dict[str, str], output_path: Path, **kwargs) -> Path:
        raise cover.ImageApiTransientError("cdn not ready")

    def fake_apply_async(*args, **kwargs):
        scheduled.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(cover_tasks, "generate_cover_file_from_metadata", fake_generate_cover_file_from_metadata)
    monkeypatch.setattr(cover_tasks.generate_cover_job, "apply_async", fake_apply_async)

    result = cover_tasks.generate_cover_job.run(status["job_id"])
    final = cover_jobs.read_status(status["job_id"])

    assert result["reason"] == "retry_wait_scheduled"
    assert 10 <= result["countdown"] <= 12
    assert scheduled
    assert scheduled[0]["kwargs"]["args"] == (status["job_id"],)
    assert 10 <= scheduled[0]["kwargs"]["countdown"] <= 12
    assert final["items"][0]["status"] == "pending"
    assert final["items"][0]["retry_after"]


def test_cover_job_deleted_item_cancels_without_requeue(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(cover_jobs, "COVER_JOBS_ROOT", tmp_path)
    status = cover_jobs.create_cover_job(
        "user-1",
        "covers.csv",
        [
            {"title": "第一本书", "author": "作者A", "category": "illustration_crayon", "category_label": "萌系治愈"}
        ],
        default_category="illustration_crayon",
    )
    job_id = status["job_id"]

    def fake_generate_cover_file_from_metadata(metadata: dict[str, str], output_path: Path, **kwargs) -> Path:
        cover_jobs.delete_cover_job_items(job_id, [1])
        raise cover.CoverGenerationCancelled("deleted during generation")

    monkeypatch.setattr(
        cover_tasks,
        "generate_cover_file_from_metadata",
        fake_generate_cover_file_from_metadata,
    )

    result = cover_tasks.generate_cover_job.run(job_id)
    final = cover_jobs.read_status(job_id)

    assert result["status"] == "completed"
    assert final["items"][0]["status"] == "deleted"
    assert final["total"] == 0
    assert final["failed"] == 0


def test_stale_cover_job_returns_processing_items_to_queue(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(cover_jobs, "COVER_JOBS_ROOT", tmp_path)
    monkeypatch.setattr(cover_tasks, "COVER_JOBS_ROOT", tmp_path)
    monkeypatch.setattr(cover_tasks.settings, "COVER_PROCESSING_STALE_SECONDS", 900)
    status = cover_jobs.create_cover_job(
        "user-1",
        "covers.csv",
        [
            {
                "title": "迟迟不结束的书",
                "author": "作者A",
                "category": "illustration_crayon",
                "category_label": "萌系治愈",
            }
        ],
        default_category="illustration_crayon",
    )
    job_id = status["job_id"]
    old_run_id = "old-run"
    status["status"] = "processing"
    status["active_run_id"] = old_run_id
    status["items"][0].update(
        status="processing",
        run_id=old_run_id,
        started_at=(datetime.now(timezone.utc) - timedelta(seconds=1200)).isoformat(),
        error=None,
    )
    cover_jobs.write_status(job_id, status)
    dispatched: list[str] = []
    monkeypatch.setattr(cover_tasks.generate_cover_job, "delay", dispatched.append)

    recovered = cover_tasks.requeue_stale_cover_jobs()
    final = cover_jobs.read_status(job_id)

    assert recovered == 1
    assert dispatched == [job_id]
    assert final["status"] == "pending"
    assert final["active_run_id"] is None
    assert final["items"][0]["status"] == "pending"
    assert "run_id" not in final["items"][0]


def test_stale_cover_job_keeps_active_item_with_recent_heartbeat(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(cover_jobs, "COVER_JOBS_ROOT", tmp_path)
    monkeypatch.setattr(cover_tasks, "COVER_JOBS_ROOT", tmp_path)
    monkeypatch.setattr(cover_tasks.settings, "COVER_PROCESSING_STALE_SECONDS", 900)
    status = cover_jobs.create_cover_job(
        "user-1",
        "covers.csv",
        [
            {
                "title": "姝ｅ湪绮句慨鐨勪功",
                "author": "浣滆€匒",
                "category": "illustration_crayon",
                "category_label": "娌绘剤鎻掔敾锛堣湣绗旓級",
            }
        ],
        default_category="illustration_crayon",
    )
    job_id = status["job_id"]
    run_id = "active-run"
    now = datetime.now(timezone.utc).isoformat()
    status["status"] = "processing"
    status["active_run_id"] = run_id
    status["items"][0].update(
        status="processing",
        run_id=run_id,
        started_at=(datetime.now(timezone.utc) - timedelta(seconds=2400)).isoformat(),
        stage_started_at=now,
        last_heartbeat_at=now,
        current_stage="refine",
        error=None,
    )
    cover_jobs.write_status(job_id, status)

    recovered = cover_tasks.requeue_stale_cover_jobs()
    final = cover_jobs.read_status(job_id)

    assert recovered == 0
    assert final["status"] == "processing"
    assert final["active_run_id"] == run_id
    assert final["items"][0]["status"] == "processing"


def test_dangdang_crawler_extracts_reference_cover_metadata(tmp_path: Path) -> None:
    crawler = DangdangCoverCrawler(cache_dir=tmp_path, timeout_seconds=3)
    markup = """
    <ul>
      <li id="p29910669">
        <a title="蛤蟆先生去看心理医生" href="//product.dangdang.com/29910669.html"></a>
        <img data-original="//img3m9.ddimg.cn/71/33/29910669-1_b_1751602431.jpg" />
      </li>
    </ul>
    """

    refs = crawler._parse_search_results(  # noqa: SLF001
        markup,
        brand="果麦文化",
        source_page="https://search.dangdang.com/?key=test&act=input",
    )

    assert len(refs) == 1
    assert refs[0].brand == "果麦文化"
    assert refs[0].product_id == "29910669"
    assert refs[0].title == "蛤蟆先生去看心理医生"
    assert refs[0].image_url.startswith("https://img3m9.ddimg.cn/")


def test_dangdang_crawler_does_not_write_cache_when_disabled(tmp_path: Path, monkeypatch) -> None:
    crawler = DangdangCoverCrawler(cache_dir=tmp_path, timeout_seconds=3)
    brand = next(iter(DANGDANG_REFERENCE_BRANDS))
    markup = """
    <ul>
      <li id="p29910669">
        <a title="reference title" href="//product.dangdang.com/29910669.html"></a>
        <img data-original="//img3m9.ddimg.cn/71/33/29910669-1_b_1751602431.jpg" />
      </li>
    </ul>
    """

    monkeypatch.setattr(crawler, "_fetch_search_page", lambda url: markup)

    refs = crawler.collect(
        title="test title",
        intro="test intro",
        category_label="test category",
        allowed_brands=[brand],
        limit=1,
        fetch_enabled=True,
        download_images=True,
        cache_enabled=False,
    )

    assert len(refs) == 1
    assert refs[0].local_image_path == ""
    assert list(tmp_path.rglob("*")) == []
