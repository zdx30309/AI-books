from __future__ import annotations

import inspect

from app.prompts.family_long_novel_prompts import (
    ZHOULI_CHAPTER_WRITE_PROMPT,
    ZHOULI_CONTINUITY_CHECK_PROMPT,
    ZHOULI_EDITORIAL_POLISH_PROMPT,
    ZHOULI_REPAIR_PROMPT,
    ZHOULI_TYPOGRAPHY_REPAIR_PROMPT,
)
from app.services import family_long_novel
from app.services.family_long_novel_prose_quality import (
    inspect_editorial_candidate,
    inspect_family_novel_prose,
    normalize_family_novel_prose,
)


def test_clean_chinese_dialogue_passes_mechanical_gate() -> None:
    text = """周建国把账单推到桌子中央，指尖没有立刻收回来。

“这笔钱是谁转的？”他问。

陈玉兰看了一眼日期，喉咙发紧。她本想先问丈夫，话到嘴边却改了主意：“我转的。四月十日那笔，也是我。”

门外有人敲了两下。周建国没有应声，只把《借款协议》翻到最后一页。"""

    assert inspect_family_novel_prose(text) == []


def test_screenshot_style_quote_and_punctuation_damage_is_blocked() -> None:
    text = """“明天爸爸拿到原件了给我发，“今晚亲戚群里已经够热闹了。

“你才不上班。你是谁？”“我是小芳啊。“我怎么记得你叫小美？

” ” ” ” ”
"""
    issue_types = {item["issueType"] for item in inspect_family_novel_prose(text)}

    assert "punctuation_pair_unclosed" in issue_types or "punctuation_pair_misordered" in issue_types
    assert "quote_run" in issue_types


def test_dialogue_sentence_mark_outside_quote_is_blocked() -> None:
    issue_types = {item["issueType"] for item in inspect_family_novel_prose("她说：“我今天不回去”。")}

    assert "dialogue_punctuation_outside_quote" in issue_types


def test_editorial_gate_rejects_changed_amounts_and_large_rewrite() -> None:
    original = "陈玉兰欠人民币40800元，每月十日前偿还1700元，共还24个月。" * 20
    candidate = "陈玉兰欠人民币48000元，每月十日前偿还2000元。"
    issue_types = {item["issueType"] for item in inspect_editorial_candidate(original, candidate)}

    assert "critical_anchor_changed" in issue_types
    assert "editorial_length_drift" in issue_types


def test_normalizer_only_cleans_invisible_whitespace() -> None:
    source = "陈玉兰\u200b没有改口。\r\n\r\n\r\n\r\n她把存折放回抽屉。  "
    normalized = normalize_family_novel_prose(source)

    assert "陈玉兰没有改口。" in normalized
    assert "她把存折放回抽屉。" in normalized
    assert "\u200b" not in normalized
    assert "\r" not in normalized


def test_family_prompts_cover_character_depth_typos_and_punctuation() -> None:
    assert "人物不能只剩动作和对白" in ZHOULI_CHAPTER_WRITE_PROMPT
    assert "逐句静默校对" in ZHOULI_CHAPTER_WRITE_PROMPT
    assert "连续堆叠引号" in ZHOULI_CHAPTER_WRITE_PROMPT
    assert "外界刺激、细微反应、利益判断" in ZHOULI_EDITORIAL_POLISH_PROMPT
    assert "不机械替换同义词" in ZHOULI_TYPOGRAPHY_REPAIR_PROMPT
    assert "错别字、同音字误用" in ZHOULI_CONTINUITY_CHECK_PROMPT
    assert "人物刻画、错别字、病句和标点问题" in ZHOULI_REPAIR_PROMPT


def test_editorial_gate_runs_before_aigc_and_state_extraction() -> None:
    source = inspect.getsource(family_long_novel._postprocess_family_chapter)

    assert source.index("_editorial_polish_family_chapter") < source.index("_apply_family_aigc_gate")
    assert source.index("_editorial_polish_family_chapter") < source.index("extract_family_chapter_state")


def test_writer_draft_is_preserved_until_editorial_gate() -> None:
    source = inspect.getsource(family_long_novel.generate_family_chapter)

    assert "chapter.raw_model_output = content" in source
    assert "chapter.pre_aigc_content = None" in source


def test_export_has_last_mechanical_prose_gate() -> None:
    source = inspect.getsource(family_long_novel.export_family_novel)

    assert "inspect_family_novel_prose" in source
    assert "已阻止导出" in source
