from __future__ import annotations

import inspect

from app.core.config import Settings
from app.prompts.family_long_novel_prompts import (
    ZHOULI_CHAPTER_WRITE_PROMPT,
    ZHOULI_CONTINUITY_CHECK_PROMPT,
    ZHOULI_DEEPSEEK_COPYEDIT_PROMPT,
    ZHOULI_EDITORIAL_POLISH_PROMPT,
    ZHOULI_NARRATIVE_AUDIT_PROMPT,
    ZHOULI_NARRATIVE_REPAIR_PROMPT,
    ZHOULI_REPAIR_PROMPT,
    ZHOULI_TYPOGRAPHY_REPAIR_PROMPT,
)
from app.services import deepseek, family_long_novel, gemini
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
    assert "居委会" in ZHOULI_NARRATIVE_AUDIT_PROMPT
    assert "核心利益" in ZHOULI_NARRATIVE_REPAIR_PROMPT
    assert "不得新增新闻" in ZHOULI_DEEPSEEK_COPYEDIT_PROMPT


def test_fabricated_files_and_media_sources_are_blocked() -> None:
    text = "她把proof_03.png发进群里。据《中国青年报》报道，这笔钱已经有了结论。"
    issue_types = {item["issueType"] for item in inspect_family_novel_prose(text)}

    assert "unsupported_external_artifact" in issue_types


def test_repeated_model_cues_and_negative_parallelism_are_blocked() -> None:
    text = "\n".join(
        [
            "她声音不大，却让桌边的人都停了筷子。",
            "周建国声音不大，只问存折去了哪里。",
            "陈玉兰声音不大，把门重新关好。",
            "不是她不肯给，是小儿子一直拖着。",
            "不是钱没到，是名字还没改。",
            "不是老人偏心，是儿子会哄。",
            "不是没人养老，是谁都不肯先开口。",
            "不是账算不清，是有人不想算清。",
        ]
    )
    issue_types = {item["issueType"] for item in inspect_family_novel_prose(text)}

    assert "repeated_model_cue" in issue_types
    assert "repeated_negative_parallelism" in issue_types


def test_duplicate_sentences_and_repeated_accounting_are_blocked() -> None:
    sentence = "陈玉兰把借条压在碗底，等所有人坐齐后才推到桌子中央。"
    text = f"{sentence}\n{sentence}\n" + "40800元。" * 5
    issue_types = {item["issueType"] for item in inspect_family_novel_prose(text)}

    assert "duplicate_sentence" in issue_types
    assert "repeated_accounting_anchor" in issue_types


def test_editorial_candidate_cannot_add_external_evidence() -> None:
    original = "陈玉兰把原来的借条放在桌上，问两个儿子谁来偿还。"
    candidate = "陈玉兰把原来的借条放在桌上，又把recording.mp3发进群里，问两个儿子谁来偿还。"
    issue_types = {item["issueType"] for item in inspect_editorial_candidate(original, candidate)}

    assert "fabricated_external_evidence" in issue_types


def test_editorial_gate_runs_before_aigc_and_state_extraction() -> None:
    source = inspect.getsource(family_long_novel._postprocess_family_chapter)

    assert source.index("_editorial_polish_family_chapter") < source.index("_apply_family_aigc_gate")
    assert source.index("_editorial_polish_family_chapter") < source.index("extract_family_chapter_state")


def test_editorial_pipeline_uses_three_models_in_bounded_order() -> None:
    source = inspect.getsource(family_long_novel._editorial_polish_family_chapter)

    assert source.index("gemini_audit_family_chapter_draft") < source.index("gpt56_repair_family_narrative")
    assert source.index("gpt56_repair_family_narrative") < source.index("deepseek_copyedit_family_chapter")
    assert source.index("deepseek_copyedit_family_chapter") < source.index("gemini_verify_family_chapter_final")
    assert source.count("gpt56_repair_family_narrative") == 1
    assert source.count("deepseek_copyedit_family_chapter") == 1


def test_writer_draft_is_preserved_until_editorial_gate() -> None:
    source = inspect.getsource(family_long_novel.generate_family_chapter)

    assert "chapter.raw_model_output = content" in source
    assert "chapter.pre_aigc_content = None" in source


def test_export_has_last_mechanical_prose_gate() -> None:
    source = inspect.getsource(family_long_novel.export_family_novel)

    assert "inspect_family_novel_prose" in source
    assert "已阻止导出" in source


def test_requested_writer_and_specialist_model_defaults() -> None:
    fields = Settings.model_fields

    assert fields["OPENAI_WRITER_MODEL"].default == "gpt-5.6-sol"
    assert fields["OPENAI_WRITER_REASONING_EFFORT"].default == "xhigh"
    assert fields["GEMINI_MODEL"].default == "gemini-3.1-flash"
    assert fields["DEEPSEEK_MODEL"].default == "deepseek-v4-flash"


def test_gemini_36_does_not_send_deprecated_temperature() -> None:
    source = inspect.getsource(gemini.generate_text)

    assert "if temperature is not None and not uses_new_controls" in source
    assert "thinkingLevel=types.ThinkingLevel" in source


def test_family_copyedit_disables_deepseek_v4_thinking_mode() -> None:
    specialist_source = inspect.getsource(family_long_novel._call_specialist_text)
    client_source = inspect.getsource(deepseek.generate_text)

    assert "thinking=False" in specialist_source
    assert '"thinking": {"type": "enabled" if thinking else "disabled"}' in client_source
