from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

import httpx
import pytest

from app.services import cover, cover_jobs
from app.services.cover_categories import get_cover_category_meta, list_cover_categories
from app.tasks import cover_tasks
from app.prompts.goal_driven_templates import list_categories
from app.services.dangdang_crawler import DANGDANG_REFERENCE_BRANDS, DangdangCoverCrawler


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
CRAYON_ILLUSTRATION_PROMPT = (
    "微信读书女性治愈系封面风格，日韩治愈手绘插画，油画棒/蜡笔/水粉质感。"
    "自然高饱和、明亮通透、不灰暗；元素少、留白多，像被年轻女生收藏的情绪绘本。"
    "书名非常大且极具存在感，手写感要自然融入插画，同时保持现代出版封面的清晰层级和手机端识别度。"
    "除书名和作者外，只放1到2句很短的氛围金句，不排太多文案。"
    "避免心理学商业封面、得到系大标题、信息图、PPT感、门和小路构图、桌面日记模板、UI插画风和过度AI感。"
)


@pytest.fixture(autouse=True)
def disable_external_cover_reference_calls(monkeypatch) -> None:
    monkeypatch.setattr(cover.settings, "COVER_DEEPSEEK_STYLE_ANALYSIS_ENABLED", False)
    monkeypatch.setattr(cover.settings, "COVER_REFERENCE_FETCH_ENABLED", False)
    monkeypatch.setattr(cover.settings, "COVER_REFERENCE_DOWNLOAD_IMAGES", False)


def test_style_brief_locks_reference_site_and_brands() -> None:
    cover_input = cover.prepare_cover_input(
        "暗处的时代",
        "@最终简介\n一个历史人物在制度缝隙中的命运转折。",
        author="测试作者",
        category="web_short_story",
    )

    brief = cover.build_cover_style_brief(cover_input)

    assert brief.reference_site.startswith("当当网")
    assert brief.reference_brands == ("酷威文化", "果麦文化", "时代华语")
    assert "历史" in brief.matched_category_style
    assert "果麦文化" in brief.brand_style_direction
    assert "不要做成电商商品主图" in " ".join(brief.avoid_rules)
    assert {item["brand"] for item in brief.reference_samples} <= {"酷威文化", "果麦文化", "时代华语"}


def test_draft_and_refine_prompts_enforce_publication_cover_not_ecommerce() -> None:
    prompt = cover.build_cover_prompt(
        "醒来以后",
        "@最终简介\n一本帮助普通人处理焦虑和内耗的心理自助书。",
        author="测试作者",
        category="psychology_self_help",
    )

    assert "酷威文化" in prompt
    assert "果麦文化" in prompt
    assert "时代华语" in prompt
    assert "完整正面封面本体" in prompt
    assert "不要 3D 书模" in prompt
    assert "不要出现出版社 logo" in prompt
    assert "出版品牌 logo" in prompt
    assert "不要生成乱码中文" in prompt
    assert "治愈系手绘封面，日韩治愈插画风，图片避免抄袭和侵权。" in prompt


def test_all_cover_categories_have_prompt_adjustment_interface() -> None:
    for item in list_categories():
        adjustment = cover.get_cover_category_prompt_adjustment(item["id"])
        assert set(adjustment) == {"style_brief_rules", "draft_rules", "refine_rules"}


def test_healing_handdrawn_prompt_only_applies_to_psychology_cover_category() -> None:
    prompt = cover.build_cover_prompt(
        "沉默的账单",
        "@最终简介\n一本讨论普通人现实压力的社会痛点书。",
        author="测试作者",
        category="social_science",
    )

    assert "治愈系手绘封面，日韩治愈插画风，图片避免抄袭和侵权。" not in prompt


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

    assert "治愈插画（蜡笔）" in prompt
    assert CRAYON_ILLUSTRATION_PROMPT in brief.generation_prompt_addendum
    assert CRAYON_ILLUSTRATION_PROMPT in prompt
    assert CRAYON_ILLUSTRATION_PROMPT in refine_prompt
    assert adjustment["style_brief_rules"][0] == CRAYON_ILLUSTRATION_PROMPT
    assert adjustment["draft_rules"][0] == CRAYON_ILLUSTRATION_PROMPT
    assert adjustment["refine_rules"][0] == f"继续保持并强化：{CRAYON_ILLUSTRATION_PROMPT}"
    assert "微信读书女性治愈系封面风格" in prompt
    assert "现代出版封面的清晰层级" in prompt
    assert "只放1到2句很短的氛围金句" in prompt
    assert "元素少、留白多" in prompt
    assert "再增加一两句金句增加氛围" not in prompt
    assert "只能有1到2句很短的氛围金句" in prompt
    assert "禁止清单、段落、整页日记文字和多个便签文案" in prompt
    assert "私人日记是情绪气质" in prompt
    assert "主色至少三种清晰亮色" in prompt
    assert "花朵、植物、风、云、猫" not in prompt
    assert "得到系大标题" in prompt
    assert "微信读书/得到系爆款风" not in prompt
    assert "排版要像成熟商业出版物" not in prompt
    assert "微信读书/得到系爆款风" not in refine_prompt
    assert get_cover_category_meta("illustration_crayon")["label"] == "治愈插画（蜡笔）"
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
    assert "即使保持手写感，也要在手机缩略图里一眼读清" in prompt
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
            category_label="治愈插画（蜡笔）",
            search_keywords="女性治愈 绘本",
            analysis="微信读书女性治愈系手绘封面。",
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
    assert "季节、空间、视角、画面叙事、色彩主轴" in prompt_a
    assert "只在封面上写1到2句很短的氛围金句" in prompt_a
    assert "不要默认套用孤独小人、蓝天色块、植物边框" in prompt_a
    assert "尤其不要把不同书都画成桌面、摊开日记本、杯子、台灯、便签、碎纸和窗边静物" in prompt_a
    assert brief_a.visual_variation_directive != brief_b.visual_variation_directive


def test_crayon_illustration_label_aliases_route_to_dedicated_prompt() -> None:
    aliases = [
        "illustration_crayon",
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
        assert cover_input.category_label == "治愈插画（蜡笔）"
        assert CRAYON_ILLUSTRATION_PROMPT in prompt


def test_crayon_default_category_overrides_spreadsheet_row_category_for_prompt() -> None:
    rows = cover_jobs.parse_cover_spreadsheet(
        "covers.csv",
        "书名,作者,分类,简介\n慢慢好起来,作者A,心理自助,一本写给成年人情绪困境的温柔书\n".encode(
            "utf-8"
        ),
        default_category="治愈插画（蜡笔）",
    )
    row = rows[0]
    prompt = cover.build_cover_prompt(
        str(row["title"]),
        f"@最终简介\n{row['intro']}",
        author=str(row["author"]),
        category=str(row["category"]),
    )

    assert row["category"] == "illustration_crayon"
    assert row["category_label"] == "治愈插画（蜡笔）"
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
    assert rows[0]["category_label"] == "社会痛点"


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


def test_illustration_cover_category_reuses_psychology_cover_prompt() -> None:
    prompt = cover.build_cover_prompt(
        "松弛地活",
        "@最终简介\n一本帮助普通人处理焦虑和内耗的心理自助书。",
        author="测试作者",
        category="illustration",
    )
    psychology_prompt = cover.build_cover_prompt(
        "松弛地活",
        "@最终简介\n一本帮助普通人处理焦虑和内耗的心理自助书。",
        author="测试作者",
        category="psychology_self_help",
    )

    assert "分类：治愈插画" in prompt
    assert "做成时代华语式心理自助书封面" in prompt
    assert "治愈系手绘封面，日韩治愈插画风，图片避免抄袭和侵权。" in prompt
    assert CRAYON_ILLUSTRATION_PROMPT not in prompt
    assert get_cover_category_meta("illustration")["label"] == "治愈插画"
    assert any(item["id"] == "illustration" for item in list_cover_categories())
    assert "做成时代华语式心理自助书封面" in psychology_prompt


def test_generate_cover_file_writes_three_stage_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[tuple[str, str, str | None]] = []

    def fake_generation(prompt: str, **kwargs) -> bytes:
        calls.append(("generation", prompt, None))
        return PNG_BYTES

    def fake_edit(image_path: Path, prompt: str, **kwargs) -> bytes:
        assert image_path.name == "draft_cover.png"
        assert image_path.exists()
        calls.append(("edit", prompt, image_path.name))
        return PNG_BYTES

    monkeypatch.setattr(cover, "_request_image_generation", fake_generation)
    monkeypatch.setattr(cover, "_request_image_edit", fake_edit)

    result = cover.generate_cover_file(
        "醒来以后",
        "@最终简介\n一本帮助普通人处理焦虑和内耗的心理自助书。",
        tmp_path,
        category="psychology_self_help",
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
    assert "治愈系手绘封面，日韩治愈插画风，图片避免抄袭和侵权。" in brief["generation_prompt_addendum"]
    assert calls[0][0] == "generation"
    assert calls[1][0] == "edit"
    assert "可直接商用、具有成熟出版质感" in calls[1][1]
    assert "删除所有出版社 logo" in calls[1][1]
    assert "治愈系手绘封面，日韩治愈插画风，图片避免抄袭和侵权。" in calls[1][1]


def test_generate_cover_file_does_not_downgrade_to_draft_on_refine_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fake_generation(prompt: str, **kwargs) -> bytes:
        return PNG_BYTES

    def fake_edit(image_path: Path, prompt: str, **kwargs) -> bytes:
        raise RuntimeError("edit failed")

    monkeypatch.setattr(cover, "_request_image_generation", fake_generation)
    monkeypatch.setattr(cover, "_request_image_edit", fake_edit)

    try:
        cover.generate_cover_file(
            "醒来以后",
            "@最终简介\n一本心理自助书。",
            tmp_path,
            category="psychology_self_help",
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

    def fake_generation(prompt: str, **kwargs) -> bytes:
        nonlocal cancelled
        cancelled = True
        return PNG_BYTES

    def fake_edit(image_path: Path, prompt: str, **kwargs) -> bytes:
        nonlocal edit_called
        edit_called = True
        return PNG_BYTES

    monkeypatch.setattr(cover, "_request_image_generation", fake_generation)
    monkeypatch.setattr(cover, "_request_image_edit", fake_edit)

    with pytest.raises(cover.CoverGenerationCancelled):
        cover.generate_cover_file(
            "醒来以后",
            "@最终简介\n一本心理自助书。",
            tmp_path,
            category="psychology_self_help",
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

    with pytest.raises(cover.ImageApiTransientError, match="hard timeout"):
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

    def fake_edit(image_path: Path, prompt: str, **kwargs) -> bytes:
        assert image_path == draft
        assert prompt == "existing refine prompt"
        return PNG_BYTES

    monkeypatch.setattr(cover, "build_cover_style_brief", fail_style_brief)
    monkeypatch.setattr(cover, "_request_image_edit", fake_edit)

    result = cover.generate_cover_file(
        "测试书名",
        "@最终简介\n测试简介",
        tmp_path,
        category="illustration_crayon",
        author="测试作者",
    )

    assert result == tmp_path / "cover.png"
    assert result.read_bytes() == PNG_BYTES


def test_delete_cover_job_items_marks_single_cover_deleted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(cover_jobs, "COVER_JOBS_ROOT", tmp_path)
    status = cover_jobs.create_cover_job(
        "user-1",
        "covers.csv",
        [
            {"title": "第一本书", "author": "作者A", "category": "social_science", "category_label": "社会痛点"},
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
            {"title": "第一本书", "author": "作者A", "category": "social_science", "category_label": "社会痛点"},
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
            {"title": f"第{i}本书", "author": "作者A", "category": "illustration_crayon", "category_label": "治愈插画（蜡笔）"}
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
                "category_label": "治愈插画（蜡笔）",
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


def test_cover_job_deleted_item_cancels_without_requeue(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(cover_jobs, "COVER_JOBS_ROOT", tmp_path)
    status = cover_jobs.create_cover_job(
        "user-1",
        "covers.csv",
        [
            {"title": "第一本书", "author": "作者A", "category": "illustration_crayon", "category_label": "治愈插画（蜡笔）"}
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
                "category_label": "治愈插画（蜡笔）",
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
