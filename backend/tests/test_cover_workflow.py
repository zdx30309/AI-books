from __future__ import annotations

import base64
import json
import time
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
CRAYON_ILLUSTRATION_PROMPT = (
    "治愈蜡笔：日韩治愈系绘本封面，手绘感，像私人日记。"
    "高饱和纯色背景，背景颜色不要渐变，简洁构图，大留白。"
    "动物或人物插画，线条松弛笨拙，保留真实手绘颗粒感。"
    "超大手写标题，像随手写在画面上。"
    "整体温柔、孤独、可爱、有陪伴感。"
    "避免商业心理书、PPT感、UI插画风、过度精致和AI感。"
)
LITERATURE_PUBLISHING_PROMPT = (
    "文艺文学感书封，像豆瓣高分文学与微信读书精选封面。"
    "现代出版设计风格，简洁、克制、有留白，不要古风小说海报感。"
    "弱化剧情感与人物主体，强化氛围、平面构成与书籍气质。"
    "可使用自然、城市、旅行、人物、植物、建筑、光影等意象元素，少而精。"
    "平面插画或轻水彩质感，轻微颗粒与纸张肌理。"
    "配色不要灰蒙蒙与做旧感，使用更有识别度和情绪感的颜色，颜色克制但能第一眼吸引人。"
    "标题排版高级、稀疏、克制，像真正出版社的文学书设计，而不是AI书法字。"
    "整体安静、诗意、有孤独感与呼吸感，同时保留现代感与视觉吸引力。"
    "避免古风模板、避免影视海报感、避免晋江小说封面、避免厚重书法字、避免PPT感、避免廉价AI插画感。"
)


@pytest.fixture(autouse=True)
def disable_external_cover_reference_calls(monkeypatch) -> None:
    monkeypatch.setattr(cover.settings, "COVER_DEEPSEEK_STYLE_ANALYSIS_ENABLED", False)
    monkeypatch.setattr(cover.settings, "COVER_REFERENCE_FETCH_ENABLED", False)
    monkeypatch.setattr(cover.settings, "COVER_REFERENCE_DOWNLOAD_IMAGES", False)


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


def test_cover_category_registry_only_exposes_requested_four_categories() -> None:
    assert [(item["id"], item["label"]) for item in list_cover_categories()] == [
        ("social_science", "视觉冲击"),
        ("illustration", "饱和插画"),
        ("illustration_crayon", "萌系治愈"),
        ("literature", "正统出版"),
    ]


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


def test_literature_cover_category_has_dedicated_cover_prompt() -> None:
    cover_input = cover.prepare_cover_input(
        "风经过房间",
        "@最终简介\n一本关于城市、房间、旅行和自我告别的文学随笔。",
        author="作者L",
        category="文学类",
    )
    brief = cover.build_cover_style_brief(cover_input)
    prompt = cover.build_draft_cover_prompt(cover_input, brief)
    refine_prompt = cover.build_refine_cover_prompt(cover_input, brief)
    adjustment = cover.get_cover_category_prompt_adjustment("literature")

    assert cover_input.category == "literature"
    assert cover_input.category_label == "正统出版"
    assert LITERATURE_PUBLISHING_PROMPT in brief.generation_prompt_addendum
    assert LITERATURE_PUBLISHING_PROMPT in prompt
    assert LITERATURE_PUBLISHING_PROMPT in refine_prompt
    assert adjustment["style_brief_rules"][0] == LITERATURE_PUBLISHING_PROMPT
    assert adjustment["draft_rules"][0] == LITERATURE_PUBLISHING_PROMPT
    assert adjustment["refine_rules"][0] == f"继续保持并强化：{LITERATURE_PUBLISHING_PROMPT}"
    assert "豆瓣高分文学与微信读书精选封面" in prompt
    assert brief.reference_site.startswith("本地正统出版参考文件")
    assert brief.reference_brands == ()
    assert brief.reference_samples == ()
    assert "长期参考文件摘要" in brief.generation_prompt_addendum
    assert "现代出版设计风格，简洁、克制、有留白" in prompt
    assert "避免古风模板" in prompt
    assert "微信读书/得到系爆款风" not in prompt
    assert "高饱和、强识别" not in prompt
    assert "当当网" not in prompt
    assert get_cover_category_meta("literature")["label"] == "正统出版"


def test_crayon_illustration_prompt_does_not_leak_to_other_cover_categories() -> None:
    for item in list_cover_categories():
        if item["id"] == "illustration_crayon":
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
    assert "文艺文学感书封，像豆瓣高分文学与微信读书精选封面" in prompt
    assert "现代出版设计风格，简洁、克制、有留白" in prompt
    assert "弱化剧情感与人物主体" in prompt
    assert "标题排版高级、稀疏、克制" in prompt
    assert "避免古风模板、避免影视海报感、避免晋江小说封面" in prompt
    assert "微信读书/得到系爆款风" not in prompt
    assert "当当网" not in prompt


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
    assert result.read_bytes() == PNG_BYTES


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
    assert result.read_bytes() == PNG_BYTES
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
    assert public["items"][1]["error"] == "no duplicate billing"


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
