from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

from PIL import Image, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.api.routers.tasks import _normalize_task_config
from app.services import comic_variants, content_engine
from app.services.comic_series import ComicSectionImage


def _joke_candidate(
    index: int,
    *,
    source_word: str = "放心",
    pun_word: str = "芳心",
    pinyin_pair: str = "fàng xīn / fāng xīn",
    place: str | None = None,
    domain: str | None = None,
) -> dict[str, object]:
    return {
        "candidate_id": f"J{index:03d}",
        "source_word": source_word,
        "pun_word": pun_word,
        "pinyin_pair": pinyin_pair,
        "semantic_domain": domain or f"领域{index}",
        "protagonist": f"原创主角{index}",
        "place": place or f"生活场所{index}",
        "relationship": "彼此熟悉的邻居",
        "key_prop": "一盆刚送到的花",
        "setup": "邻居托主角照看门口的花",
        "visual_turn": "主角连续三天抱着花守在门口",
        "payoff": "最后两张卡片才让花名和承诺连在一起",
        "target_behavior": "只会说漂亮话却不肯落实承诺",
        "roast_mode": "delayed_reveal",
        "ordinary_story": "邻居出门前把一盆花交给主角照看，主角每天都认真守着它。",
        "audience_expectation": "读者以为主角只是过分认真地替邻居看花。",
        "odd_repetition": "主角连续三天把花抱到门口等邻居回来。",
        "reveal_trigger": "邻居回来后问他为什么连睡觉都抱着花。",
        "reveal_line": f"主角说，他一直替邻居守着{pun_word}。",
        "final_callback": f"邻居点点头，这次总算可以{source_word}了。",
        "delayed_reveal_plan": "前六成只写照看花和等待，倒数两张才说出两个读音相近的词。",
        "human_reason": "普通故事先成立，最后一句才让前面的认真显得有点荒唐。",
        "delivery_mode": f"dry_delayed_{index}",
        "scores": {
            "naturalness": 9,
            "surprise": 8,
            "visual_payoff": 8,
            "freshness": 8,
            "instant_clarity": 9,
            "laugh_out_loud": 8,
            "story_logic": 9,
            "delayed_reveal": 9,
            "phonetic_payoff": 9,
            "human_rhythm": 9,
            "forcedness": 1,
            "harm_risk": 0,
        },
    }


def _story_cards() -> list[dict[str, str]]:
    return [
        {"card_role": "hook", "text": "雨停以后，他还站在旧车站。", "render_mode": "text_with_doodle", "visual": "少年站在站牌下"},
        {"card_role": "setup", "text": "他手里一直拎着两把伞。", "render_mode": "doodle_focus", "visual": "两把颜色不同的旧伞"},
        {"card_role": "detail", "text": "末班车开走了。", "render_mode": "text_only", "visual": ""},
        {"card_role": "delay", "text": "修车铺也关了门。", "render_mode": "text_only", "visual": ""},
        {"card_role": "turn", "text": "街角终于有人朝他跑来。", "render_mode": "text_with_doodle", "visual": "老人从街角跑来"},
        {"card_role": "ending", "text": "原来另一把伞，是替陌生人等的。", "render_mode": "text_with_doodle", "visual": "两个人各撑一把伞离开"},
    ]


def _homophone_cards(*, reveal_early: bool = False) -> list[dict[str, str]]:
    cards = [
        {"card_role": "hook", "text": "周一早会，经理说有件大事。", "render_mode": "text_with_doodle", "visual": "经理站在白板前"},
        {"card_role": "setup", "text": "他说，今天每个人都会有份。", "render_mode": "text_with_doodle", "visual": "员工一起抬头"},
        {"card_role": "delay", "text": "他把信封一个个放到桌上。", "render_mode": "text_with_doodle", "visual": "桌上排着信封"},
        {"card_role": "delay", "text": "办公室安静了三秒。", "render_mode": "text_only", "visual": ""},
        {"card_role": "question", "text": "终于有人先把信封拆开。", "render_mode": "text_with_doodle", "visual": "员工拆信封"},
        {"card_role": "answer", "text": "里面只有一块饼干。", "render_mode": "doodle_focus", "visual": "信封里是一块夹心饼干"},
        {"card_role": "reveal", "text": "经理管这叫“夹心”。", "render_mode": "text_with_doodle", "visual": "经理还在鼓掌"},
        {"card_role": "callback", "text": "这次加薪，确实挺夹心。", "render_mode": "text_only", "visual": ""},
    ]
    if reveal_early:
        cards[1]["text"] = "他说，今天要给大家加薪。"
    return cards


def _homophone_episode(*, reveal_early: bool = False) -> dict[str, object]:
    return {
        "title": "鼓鼓的信封",
        "source_word": "加薪",
        "pun_word": "夹心",
        "pinyin_pair": "jiā xīn / jiā xīn",
        "protagonist": "刚入职的小浣熊和狐狸经理",
        "place": "共享办公室",
        "relationship": "经理和员工",
        "key_prop": "一摞鼓鼓的信封",
        "roast_mode": "empty_promise",
        "ordinary_story": "经理给每个人发了信封，员工拆开后发现里面只有饼干。",
        "audience_expectation": "员工以为经理终于要兑现承诺。",
        "odd_repetition": "经理郑重地把信封逐一放到每个人桌前。",
        "reveal_trigger": "第一个员工拆开了信封。",
        "reveal_line": "经理管这叫夹心。",
        "final_callback": "这次加薪，确实挺夹心。",
        "delayed_reveal_plan": "前五张不说出加薪和夹心，最后两张才让两个词相遇。",
        "human_reason": "员工的期待和廉价实物形成直接落差。",
        "delivery_mode": "dry_delayed",
        "story_setup": "经理说今天每个人都会有份",
        "story_change": "他把信封逐一放到桌上",
        "story_ending": "信封里只有一块饼干",
        "cards": _homophone_cards(reveal_early=reveal_early),
    }


def test_variant_categories_are_independent_healing_family() -> None:
    assert comic_variants.is_healing_comic_family("healing_comic")
    assert comic_variants.is_healing_comic_family("story_comic")
    assert comic_variants.is_healing_comic_family("homophone_comic")
    assert not comic_variants.is_healing_comic_family("comic_series")


def test_story_and_homophone_prompts_require_swipeable_delayed_reveal() -> None:
    story_brief = "【图文故事工作台】\n故事起点：雨天拿错一把伞\n生活场域：城市日常"
    joke_brief = "【谐音梗编剧室】\n笑点场景：朋友互损\n喜剧口味：干巴荒诞"
    story = comic_variants.build_variant_writer_prompt(
        "story_comic",
        "给你讲个故事",
        "既有漫画研究摘要",
        panel_count=3,
        source_content=story_brief,
    )
    homophone = comic_variants.build_variant_writer_prompt(
        "homophone_comic",
        "今天也有好笑的事",
        "既有漫画研究摘要",
        panel_count=2,
        source_content=joke_brief,
        joke_bank='[{"source_word":"天意","pun_word":"添衣"}]',
    )
    ideas = comic_variants.build_homophone_idea_prompt(
        "今天也有好笑的事",
        panel_count=2,
        source_content=joke_brief,
    )
    assert "6 至 10 张" in story
    assert "一张卡片只推进一个信息点" in story
    assert story_brief in story
    assert "本次明确创作约束" in story
    assert "JOKE_BANK" in homophone
    assert "前 60%" in homophone
    assert "最后 2 至 3 张" in homophone
    assert "不复刻" in homophone
    assert joke_brief in homophone
    assert joke_brief in ideas
    assert "本轮硬约束" in ideas


def test_homophone_joke_bank_filters_stale_and_duplicate_contexts() -> None:
    candidates = [
        _joke_candidate(1),
        _joke_candidate(2, source_word="压力", pun_word="鸭力", pinyin_pair="yā lì / yā lì"),
        _joke_candidate(3, source_word="安心", pun_word="安薪", pinyin_pair="ān xīn / ān xīn", place="生活场所1"),
        _joke_candidate(4, source_word="天意", pun_word="添衣", pinyin_pair="tiān yì / tiān yī"),
    ]
    selected = comic_variants.parse_homophone_idea_output(
        json.dumps({"candidates": candidates}, ensure_ascii=False), panel_count=2
    )
    assert [item["candidate_id"] for item in selected] == ["J001", "J004"]


def test_homophone_joke_bank_requires_late_natural_reveal() -> None:
    candidate = _joke_candidate(1)
    candidate["reveal_line"] = "邻居回来后只问了一句。"
    assert comic_variants.parse_homophone_idea_output(
        json.dumps({"candidates": [candidate]}, ensure_ascii=False), panel_count=1
    ) == []


def test_story_writer_normalizes_to_six_swipe_cards() -> None:
    writer = {
        "title": "巷口的小故事",
        "intro": "一件普通小事。",
        "episodes": [{
            "title": "两把伞",
            "protagonist": "穿绿雨衣的少年",
            "place": "雨后街口",
            "relationship": "两个陌生人",
            "key_prop": "两把旧伞",
            "story_setup": "少年独自在车站等人",
            "story_change": "末班车开走后他还没有离开",
            "story_ending": "他把多带的一把伞交给陌生老人",
            "cards": _story_cards(),
        }],
    }
    _, _, episodes, _ = comic_variants.parse_variant_writer_output(
        "story_comic", json.dumps(writer, ensure_ascii=False), "巷口的小故事", panel_count=1
    )
    assert episodes[0]["layout"] == "carousel"
    assert len(episodes[0]["images"]) == 6
    assert [item["sub_index"] for item in episodes[0]["images"]] == list(range(1, 7))
    assert any(item["render_mode"] == "text_only" for item in episodes[0]["images"])


def test_homophone_reveal_is_hidden_until_last_cards() -> None:
    writer = {"episodes": [_homophone_episode()]}
    _, _, episodes, _ = comic_variants.parse_variant_writer_output(
        "homophone_comic", json.dumps(writer, ensure_ascii=False), "今天谁又画饼", panel_count=1
    )
    episode = episodes[0]
    texts = [str(card["text"]) for card in episode["images"]]
    early = "".join(texts[:4])
    late = "".join(texts[-3:])
    assert "加薪" not in early and "夹心" not in early
    assert "加薪" in late and "夹心" in late
    assert episode["title"] == "鼓鼓的信封"


def test_early_homophone_reveal_rolls_back_to_safe_aligned_fallback() -> None:
    writer = {"episodes": [_homophone_episode(reveal_early=True)]}
    _, _, episodes, _ = comic_variants.parse_variant_writer_output(
        "homophone_comic", json.dumps(writer, ensure_ascii=False), "今天谁又画饼", panel_count=1
    )
    episode = episodes[0]
    texts = [str(card["text"]) for card in episode["images"]]
    assert episode["title"] != "鼓鼓的信封"
    assert episode["source_word"] == "报销"
    assert episode["pun_word"] == "抱箱"
    assert "报销" not in "".join(texts[:4])
    assert "抱箱" not in "".join(texts[:4])
    assert "报销" in "".join(texts[-3:])
    assert "抱箱" in "".join(texts[-3:])


def test_one_planned_image_per_card_and_markdown_placeholder(tmp_path: Path) -> None:
    _, _, episodes, meta = comic_variants.parse_variant_writer_output(
        "story_comic",
        json.dumps({"episodes": [{"cards": _story_cards()}]}, ensure_ascii=False),
        "给你讲个故事",
        panel_count=1,
    )
    images = comic_variants.plan_variant_comic_images(
        "story_comic", episodes, tmp_path, title="给你讲个故事", intro="一件小事。", series_meta=meta
    )
    assert len(images) == 7
    assert [image.filename for image in images[1:]] == [
        f"comic_section_01_{index:02d}.png" for index in range(1, 7)
    ]
    markdown = comic_variants.assemble_variant_comic_markdown("给你讲个故事", "一件小事。", episodes)
    assert markdown.count("{{COMIC_IMAGE_1_") == 6


def test_image_prompt_requests_one_card_and_forbids_model_text() -> None:
    panel = comic_variants._normalize_episode({}, variant="story_comic", index=1)
    prompt = comic_variants.build_variant_panel_prompt(
        "story_comic", panel=panel, image=panel["images"][0], sub_index=1, index=1, title="给你讲个故事"
    )
    assert "单独卡片" in prompt
    assert "不是三格或四格" in prompt
    assert "绝对不要生成任何文字" in prompt
    assert "上半部至少 47%" in prompt
    assert "严禁上下分色" in prompt
    assert "至少自然出现三种" in prompt
    assert "儿童马克笔与蜡笔平涂小涂鸦" not in prompt
    assert "禁止泥灰" in prompt
    cover_prompt = comic_variants.build_variant_cover_prompt(
        "story_comic", title="给你讲个故事", intro="一件小事。", panels=[panel], series_meta={}
    )
    assert "后端程序会把书名" in cover_prompt
    assert "绝对不要生成任何文字" in cover_prompt
    assert "封面必须是一张连续完整的画面" in cover_prompt
    assert "统一棕黄滤镜" in cover_prompt
    assert "儿童马克笔与蜡笔平涂小涂鸦" not in cover_prompt


def test_homophone_prompt_and_cover_use_marker_doodle_direction() -> None:
    panel = comic_variants._normalize_episode({}, variant="homophone_comic", index=1)
    prompt = comic_variants.build_variant_panel_prompt(
        "homophone_comic",
        panel=panel,
        image=panel["images"][0],
        sub_index=1,
        index=1,
        title="今天也有好笑的事",
    )
    assert "下方至少 28%" in prompt
    assert "儿童马克笔与蜡笔平涂小涂鸦" in prompt
    assert "深炭灰轮廓粗而不完全均匀" in prompt
    assert any(color in prompt for color in ("天蓝色", "奶油黄色", "薄荷绿色", "珊瑚粉色", "淡紫色"))
    cover_prompt = comic_variants.build_variant_cover_prompt(
        "homophone_comic",
        title="今天也有好笑的事",
        intro="一个谐音梗。",
        panels=[panel],
        series_meta={},
    )
    assert "儿童马克笔与蜡笔平涂小涂鸦" in cover_prompt
    assert "下方至少 28%" in cover_prompt


def test_homophone_uses_bundled_open_font_and_stable_episode_background() -> None:
    font_path = comic_variants._homophone_cjk_font_path()
    assert font_path.name == "Xiaolai-Regular.ttf"
    assert font_path.exists()
    assert (font_path.parent / "OFL-Xiaolai.txt").exists()
    font = ImageFont.truetype(str(font_path), size=96)
    assert bytes(font.getmask("这")) != bytes(font.getmask("?"))
    first = comic_variants._variant_background_name("homophone_comic", "同一章")
    second = comic_variants._variant_background_name("homophone_comic", "同一章")
    assert first == second
    assert "#" in first


def test_variant_typesetter_has_no_hard_horizontal_color_seam(tmp_path: Path) -> None:
    card = tmp_path / "soft-transition.png"
    canvas = Image.new("RGB", (2450, 3500), (246, 239, 228))
    canvas.save(card)

    comic_variants._typeset_variant_card(
        card,
        "今天也有一件好笑的事。",
        render_mode="text_with_doodle",
        category="homophone_comic",
    )

    with Image.open(card).convert("RGB") as rendered:
        boundary = int(rendered.height * 0.70)
        above = rendered.getpixel((96, boundary - 2))
        below = rendered.getpixel((96, boundary + 2))
        assert max(abs(left - right) for left, right in zip(above, below)) <= 3
        assert rendered.getbbox() == (0, 0, 2450, 3500)


def test_text_only_card_is_typeset_locally_without_paid_image_call(monkeypatch, tmp_path: Path) -> None:
    paid_calls: list[int] = []

    def fake_paid(image: ComicSectionImage, **_kwargs) -> ComicSectionImage:
        paid_calls.append(image.index)
        Image.new("RGB", (2450, 3500), "white").save(image.path)
        return ComicSectionImage(**{**image.__dict__, "status": "completed", "error": None})

    monkeypatch.setattr(comic_variants, "_generate_image_with_comic_protection", fake_paid)
    panels = [{
        "title": "停一下",
        "images": [{
            "sub_index": 1,
            "card_role": "delay",
            "text": "办公室安静了三秒。",
            "render_mode": "text_only",
            "visual": "",
        }],
    }]
    results = comic_variants.generate_variant_comic_images(
        "story_comic", panels, tmp_path, title="测试", intro="测试", series_meta={}
    )
    assert paid_calls == [-1]
    assert results[1].path.exists() and results[1].path.stat().st_size > 0
    with Image.open(results[1].path) as image:
        assert image.size == (2450, 3500)


def test_old_variant_script_cache_is_invalidated(tmp_path: Path) -> None:
    (tmp_path / "02_comic_variant_script.json").write_text(
        json.dumps({"title": "旧稿", "panels": [{"title": "一张老图"}]}), encoding="utf-8"
    )
    assert content_engine._read_comic_variant_script_stage(tmp_path) is None
    (tmp_path / "02_comic_variant_script.json").write_text(
        json.dumps({
            "pipeline_version": comic_variants.COMIC_VARIANT_PIPELINE_VERSION,
            "title": "新稿",
            "panels": [{"title": "连续卡片", "images": _story_cards()}],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    assert content_engine._read_comic_variant_script_stage(tmp_path) is not None
    assert content_engine._read_comic_variant_script_stage(
        tmp_path,
        category="homophone_comic",
    ) is None
    payload = json.loads((tmp_path / "02_comic_variant_script.json").read_text(encoding="utf-8"))
    payload["pipeline_version"] = comic_variants.HOMOPHONE_COMIC_PIPELINE_VERSION
    (tmp_path / "02_comic_variant_script.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    assert content_engine._read_comic_variant_script_stage(
        tmp_path,
        category="homophone_comic",
    ) is not None


def test_new_comic_task_config_keeps_selected_variant() -> None:
    for category in ("story_comic", "homophone_comic"):
        cfg = _normalize_task_config({
            "category": category,
            "comic_title": "今天有一件小事",
            "comic_panel_count": 4,
            "comic_character_prompt": "原创角色",
        })
        assert cfg["category"] == category
        assert cfg["comic_panel_count"] == 4
        assert cfg["raw_text"].startswith("书名：今天有一件小事")


def test_story_variant_generation_writes_versioned_script_without_changing_healing(monkeypatch, tmp_path: Path) -> None:
    writer_output = json.dumps({
        "title": "给你讲个故事",
        "intro": "一件看得见的小事。",
        "episodes": [{"title": "两把伞", "cards": _story_cards()}],
    }, ensure_ascii=False)

    async def fake_writer(*_args, **_kwargs):
        return writer_output

    def fake_images(category, panels, output_dir, **kwargs):
        return comic_variants.plan_variant_comic_images(
            category, panels, output_dir, title=kwargs["title"], intro=kwargs["intro"], series_meta=kwargs["series_meta"]
        )

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_writer)
    monkeypatch.setattr(comic_variants, "generate_variant_comic_images", fake_images)
    monkeypatch.setattr(content_engine, "_healing_comic_research_context", lambda _title: "cached healing base")

    asyncio.run(content_engine.generate_full_book(
        "书名：给你讲个故事", category="story_comic", output_dir=tmp_path, healing_comic_panel_count=1
    ))
    payload = json.loads((tmp_path / "02_comic_variant_script.json").read_text(encoding="utf-8"))
    assert payload["pipeline_version"] == comic_variants.COMIC_VARIANT_PIPELINE_VERSION
    assert len(payload["panels"][0]["images"]) == 6
    assert not (tmp_path / "02_healing_comic_script.json").exists()


def test_homophone_generation_never_auto_retries_paid_writer_calls(monkeypatch, tmp_path: Path) -> None:
    writer_kwargs: list[dict[str, object]] = []

    async def fake_writer(*_args, **kwargs):
        writer_kwargs.append(dict(kwargs))
        return "{}" if len(writer_kwargs) == 1 else ""

    def fake_images(category, panels, output_dir, **kwargs):
        return comic_variants.plan_variant_comic_images(
            category, panels, output_dir, title=kwargs["title"], intro=kwargs["intro"], series_meta=kwargs["series_meta"]
        )

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_writer)
    monkeypatch.setattr(comic_variants, "generate_variant_comic_images", fake_images)
    monkeypatch.setattr(content_engine, "_healing_comic_research_context", lambda _title: "cached healing base")

    asyncio.run(content_engine.generate_full_book(
        "书名：今天谁又没说实话", category="homophone_comic", output_dir=tmp_path, healing_comic_panel_count=1
    ))
    assert len(writer_kwargs) == 2
    assert all(kwargs["retry_on_failure"] is False for kwargs in writer_kwargs)
    assert all(kwargs["reasoning_effort"] == "medium" for kwargs in writer_kwargs)
