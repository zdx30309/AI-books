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
        "target_behavior": "只会说漂亮话却不肯落实承诺",
        "roast_mode": "empty_promise",
        "audience_expectation": "邻居以为对方真的会兑现此前的承诺",
        "misread_trigger": "对方当众强调这次绝对可以放心",
        "literal_action": "主角抱来一盆写着芳心的巨大花盆",
        "status_reversal": "原本说漂亮话的人被当场堵得说不出话",
        "target_reaction": "对方保持伸手姿势，笑容直接僵在脸上",
        "base_roast": "只会说漂亮话的人最终被自己没有兑现的承诺当场拆穿",
        "pun_advantage": "放心与芳心把空话变成一个能够被看见的荒唐物件",
        "delivery_mode": f"promise_receipt_{index}",
        "caption": f"邻居让他{source_word}，他却认真抱稳了那盆{pun_word}{index}。",
        "scores": {
            "naturalness": 9,
            "surprise": 8,
            "visual_payoff": 9,
            "freshness": 8,
            "instant_clarity": 9,
            "laugh_out_loud": 8,
            "roast_precision": 8,
            "status_reversal": 9,
            "memorability": 8,
            "base_comedy_strength": 8,
            "caption_punch": 8,
            "forcedness": 1,
            "harm_risk": 0,
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


def test_story_and_homophone_writer_contracts_are_separate() -> None:
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
    assert "治愈只保留为画面观感" in homophone
    assert "犀利吐槽" in homophone
    assert "地位反转" in homophone
    assert "压力/鸭力" not in homophone


def test_homophone_joke_bank_rejects_soft_production_pair_even_with_high_scores() -> None:
    candidate = _joke_candidate(
        1,
        source_word="鼓励",
        pun_word="谷粒",
        pinyin_pair="gǔ lì / gǔ lì",
    )
    payload = json.dumps({"candidates": [candidate]}, ensure_ascii=False)
    assert comic_variants.parse_homophone_idea_output(payload, panel_count=1) == []


def test_homophone_joke_bank_requires_both_words_in_caption() -> None:
    candidate = _joke_candidate(1)
    candidate["caption"] = "邻居只会说漂亮话，最后被花盆堵得一句也接不上。"
    payload = json.dumps({"candidates": [candidate]}, ensure_ascii=False)
    assert comic_variants.parse_homophone_idea_output(payload, panel_count=1) == []


def test_homophone_joke_bank_rejects_ai_three_clause_plot_summary() -> None:
    candidate = _joke_candidate(1)
    candidate["caption"] = "邻居让他放心，他端来一盆芳心，花盆最后替他认罪。"
    payload = json.dumps({"candidates": [candidate]}, ensure_ascii=False)
    assert comic_variants.parse_homophone_idea_output(payload, panel_count=1) == []


def test_invalid_homophone_writer_episode_falls_back_as_one_aligned_story() -> None:
    writer = {
        "episodes": [
            {
                "title": "软掉的梗",
                "layout": "three_panel",
                "caption": "大家互相鼓励，最后每只小鸟都收到了谷粒。",
                "source_word": "鼓励",
                "pun_word": "谷粒",
                "pinyin_pair": "gǔ lì / gǔ lì",
                "story_setup": "小鸟站在树下",
                "story_change": "朋友送来谷粒",
                "story_ending": "大家温柔地一起吃饭",
                "panel_script": [{"visual": "小鸟吃谷粒"}] * 3,
            }
        ]
    }
    _, _, episodes, _ = comic_variants.parse_variant_writer_output(
        "homophone_comic",
        json.dumps(writer, ensure_ascii=False),
        "今天谁又画饼",
        panel_count=1,
    )
    episode = episodes[0]
    assert episode["source_word"] == "加薪"
    assert episode["pun_word"] == "夹心"
    assert "夹心饼干" in episode["caption"]
    assert "经理" in episode["story_setup"]
    assert "经理" in episode["panel_script"][-1]["visual"]


def test_homophone_image_prompt_carries_roast_and_reaction_contract() -> None:
    panel = comic_variants._normalize_episode({}, variant="homophone_comic", index=1)
    prompt = comic_variants.build_variant_panel_prompt(
        "homophone_comic",
        panel=panel,
        index=1,
        title="今天谁又画饼",
    )
    assert "用漂亮承诺代替真实回报" in prompt
    assert "经理还保持鼓掌姿势" in prompt
    assert "不得画成双方温柔微笑" in prompt


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
