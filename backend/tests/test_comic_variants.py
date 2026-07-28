from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.api.routers.tasks import _normalize_task_config
from app.services import comic_variants, content_engine


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
        "relationship": "彼此照顾的邻居",
        "key_prop": "一盆刚送到的花",
        "setup": "邻居托主角照看门口的花",
        "visual_turn": "主角抱着花追上忘记带钥匙的邻居",
        "payoff": "花盆正好挡住写着放心的便签",
        "caption": f"邻居让他放心，他却认真抱稳了那盆芳心{index}。",
        "scores": {
            "naturalness": 9,
            "surprise": 8,
            "visual_payoff": 9,
            "warmth": 9,
            "freshness": 8,
            "instant_clarity": 9,
            "forcedness": 1,
        },
    }


def test_variant_categories_are_independent_healing_family() -> None:
    assert comic_variants.is_healing_comic_family("healing_comic")
    assert comic_variants.is_healing_comic_family("story_comic")
    assert comic_variants.is_healing_comic_family("homophone_comic")
    assert not comic_variants.is_healing_comic_family("comic_series")


def test_complete_caption_is_one_sentence_without_fragments_or_dash() -> None:
    caption = comic_variants.normalize_complete_caption(
        ["今晚灯很软", "她还是把伞送到了邻居门前"],
        fallback="雨停以前，她把那把伞送到了真正等它的人手里。",
    )
    assert caption.endswith("。")
    assert caption.count("。") == 1
    assert "—" not in caption
    assert "/" not in caption
    assert len(caption) >= 17


def test_homophone_joke_bank_filters_stale_forced_and_duplicate_contexts() -> None:
    candidates = [
        _joke_candidate(1),
        _joke_candidate(
            2,
            source_word="压力",
            pun_word="鸭力",
            pinyin_pair="yā lì / yā lì",
        ),
        _joke_candidate(
            3,
            source_word="安心",
            pun_word="安薪",
            pinyin_pair="ān xīn / ān xīn",
            place="生活场所1",
        ),
        _joke_candidate(
            4,
            source_word="天意",
            pun_word="添衣",
            pinyin_pair="tiān yì / tiān yī",
        ),
    ]
    payload = json.dumps({"candidates": candidates}, ensure_ascii=False)
    selected = comic_variants.parse_homophone_idea_output(payload, panel_count=2)
    assert [item["candidate_id"] for item in selected] == ["J001", "J004"]
    assert all(frozenset((str(item["source_word"]), str(item["pun_word"]))) != frozenset(("压力", "鸭力")) for item in selected)


def test_story_and_homophone_writer_contracts_are_separate_and_healing() -> None:
    story = comic_variants.build_variant_writer_prompt(
        "story_comic",
        "给你讲个故事",
        "既有治愈漫画研究摘要",
        panel_count=3,
    )
    homophone = comic_variants.build_variant_writer_prompt(
        "homophone_comic",
        "今天也有好笑的事",
        "既有治愈漫画研究摘要",
        panel_count=2,
        joke_bank='[{"source_word":"天意","pun_word":"添衣"}]',
    )
    assert "single_scene、three_panel、four_panel" in story
    assert "具体生活含义" in story
    assert "JOKE_BANK" in homophone
    assert "添衣" in homophone
    assert "治愈" in homophone
    assert "压力/鸭力" not in homophone


def test_variant_parser_makes_one_complete_caption_and_one_image_per_episode() -> None:
    writer = {
        "title": "巷口的小故事",
        "intro": "三件日常小事。",
        "episodes": [
            {
                "title": "雨伞",
                "layout": "three_panel",
                "caption": "雨停以后，她才发现有人把干燥的一面一直留给自己。",
                "protagonist": "穿绿雨衣的小女孩",
                "place": "学校门口",
                "relationship": "同桌",
                "key_prop": "旧雨伞",
                "story_setup": "两个人共撑一把伞",
                "story_change": "同桌悄悄把伞向她倾斜",
                "story_ending": "同桌的肩膀湿了",
                "panel_script": [{"visual": "并肩走出校门"}] * 3,
            }
        ],
    }
    _, _, episodes, _ = comic_variants.parse_variant_writer_output(
        "story_comic",
        json.dumps(writer, ensure_ascii=False),
        "巷口的小故事",
        panel_count=1,
    )
    assert len(episodes) == 1
    assert len(episodes[0]["images"]) == 1
    assert len(episodes[0]["panel_script"]) == 3
    assert episodes[0]["caption"].endswith("。")


def test_new_comic_task_config_keeps_selected_variant() -> None:
    for category in ("story_comic", "homophone_comic"):
        cfg = _normalize_task_config(
            {
                "category": category,
                "comic_title": "今天有一件小事",
                "comic_panel_count": 4,
                "comic_character_prompt": "原创治愈系角色",
            }
        )
        assert cfg["category"] == category
        assert cfg["comic_panel_count"] == 4
        assert cfg["raw_text"].startswith("书名：今天有一件小事")


def test_story_variant_generation_uses_variant_pipeline_without_changing_healing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    writer_output = json.dumps(
        {
            "title": "给你讲个故事",
            "intro": "一件看得见的小事。",
            "episodes": [
                {
                    "title": "借来的伞",
                    "layout": "single_scene",
                    "caption": "雨停以后，他才发现那把伞把两个人送回了同一条街。",
                    "protagonist": "原创邮差",
                    "place": "雨后街口",
                    "relationship": "邻居",
                    "key_prop": "旧伞",
                    "story_setup": "邮差借到一把伞",
                    "story_change": "他顺路送邻居回家",
                    "story_ending": "两个人停在同一扇门前",
                    "panel_script": [{"visual": "两个人在雨后街口收伞"}],
                }
            ],
        },
        ensure_ascii=False,
    )

    async def fake_writer(*_args, **_kwargs):
        return writer_output

    def fake_images(category, panels, output_dir, **kwargs):
        assert category == "story_comic"
        return comic_variants.plan_variant_comic_images(
            category,
            panels,
            output_dir,
            title=kwargs["title"],
            intro=kwargs["intro"],
            series_meta=kwargs["series_meta"],
        )

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_writer)
    monkeypatch.setattr(comic_variants, "generate_variant_comic_images", fake_images)
    monkeypatch.setattr(content_engine, "_healing_comic_research_context", lambda _title: "cached healing base")

    output = asyncio.run(
        content_engine.generate_full_book(
            "书名：给你讲个故事",
            category="story_comic",
            output_dir=tmp_path,
            healing_comic_panel_count=1,
        )
    )
    assert "给你讲个故事" in output
    assert "雨停以后" in output
    assert (tmp_path / "02_comic_variant_script.json").exists()
    assert not (tmp_path / "02_healing_comic_script.json").exists()
