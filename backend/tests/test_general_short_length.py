import json

import pytest

from app.services import bilibili, content_engine, deepseek, deepseek_editor
from app.services.aigc_quality_gate import AigcManualReviewRequired
from app.services.general_short_length import issues, repair_length, word_count
from app.services.generation_spend import GenerationBudgetExceeded


def book(size=2400, count=9):
    return "@最终书名\n测试书\n@目录\n" + "\n".join(f"@{number}. 标题" for number in range(1, count + 1)) + "\n@导言\n导言\n@正文\n" + "\n\n".join(f"@{number}. 标题\n" + "中" * size for number in range(1, count + 1))


def test_real_words_exclude_punctuation_headings_and_catalog():
    assert word_count("# 标题\n中文 ABC 1994，English-word!\n@目录") == 5
    assert issues(book(2000)) == []
    assert issues(book(2000) + "\n@参考文献\n" + "中" * 10000) == []
    assert issues(book(1999))
    assert issues(book(2500))
    assert issues(book(3000, 7))
    assert issues(book(2000) + "\n@9. 重复章\n正文")


def test_local_repair_serial_and_saved_paid_responses_reused(tmp_path, monkeypatch):
    calls = []
    def generate(payload, **kwargs):
        calls.append(json.loads(payload)["chapter_number"])
        return "中" * 2400
    monkeypatch.setattr(deepseek, "generate_text", generate)
    result = repair_length(book(700), "原始资料", tmp_path)
    assert calls == list(range(1, 10))
    assert issues(result) == []
    assert result.startswith(book(700).split("@正文")[0])
    assert repair_length(book(700), "原始资料", tmp_path) == result
    assert len(calls) == 9
    assert (next((tmp_path / "length_repair").iterdir()) / "source.md").read_text() == book(700)


def test_invalid_paid_response_not_replayed(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(deepseek, "generate_text", lambda *args, **kwargs: calls.append(1) or "不足")
    for attempt in range(2):
        with pytest.raises(AigcManualReviewRequired):
            repair_length(book(700), "原始资料", tmp_path)
    assert calls == [1]


def test_uncertain_paid_call_does_not_replay(tmp_path, monkeypatch):
    calls = []
    def generate(*args, **kwargs):
        calls.append(1)
        raise deepseek.DeepSeekDeliveryUncertainError("timeout")
    monkeypatch.setattr(deepseek, "generate_text", generate)
    with pytest.raises(deepseek.DeepSeekDeliveryUncertainError):
        repair_length(book(700), "原始资料", tmp_path)
    with pytest.raises(AigcManualReviewRequired):
        repair_length(book(700), "原始资料", tmp_path)
    assert calls == [1]


def test_budget_block_before_paid_request_can_resume(tmp_path, monkeypatch):
    def blocked(*args, **kwargs):
        raise GenerationBudgetExceeded("budget")
    monkeypatch.setattr(deepseek, "generate_text", blocked)
    with pytest.raises(GenerationBudgetExceeded):
        repair_length(book(700), "原始资料", tmp_path)
    monkeypatch.setattr(deepseek, "generate_text", lambda *args, **kwargs: "中" * 2400)
    assert not issues(repair_length(book(700), "原始资料", tmp_path))


def test_valid_length_is_preserved_through_edit_gate():
    assert content_engine._standard_longform_stage_structure_issue(book(), book(1000), category="general_short")
    assert content_engine._standard_longform_stage_structure_issue(book(), book(2300), category="general_short") is None


def test_edit_caps_are_bounded_and_explicit_caps_preserved(monkeypatch):
    calls = []
    monkeypatch.setattr(deepseek, "generate_text", lambda *args, **kwargs: calls.append(kwargs) or "done")
    deepseek_editor.polish_book_text("中" * 1000)
    deepseek_editor.refine_book_text("中" * 2400)
    deepseek_editor.polish_book_text("中" * 20000)
    deepseek_editor.polish_book_text("中", max_output_tokens=8000)
    assert [entry["max_output_tokens"] for entry in calls] == [4096, 5824, 32768, 8000]


def test_bilibili_bad_duration_uses_public_metadata_only(monkeypatch):
    monkeypatch.setattr(bilibili, "_fetch_view", lambda *args: {"duration": 1738})
    monkeypatch.setattr(bilibili, "extract_bilibili_text", lambda *args, **kwargs: pytest.fail("no transcription"))
    assert bilibili.verify_transcript_duration("https://www.bilibili.com/video/BV1hN596zEas", "文" * 11000, 2) == 1738
    monkeypatch.setattr(bilibili, "_fetch_view", lambda *args: pytest.fail("normal metadata is not fetched"))
    assert bilibili.verify_transcript_duration("https://www.bilibili.com/video/BV1hN596zEas", "文" * 11000, 1738) == 1738


def test_bad_duration_not_preserved_when_metadata_unavailable(monkeypatch):
    def fail(*args):
        raise bilibili.BilibiliTranscriptError("unavailable")
    monkeypatch.setattr(bilibili, "_fetch_view", fail)
    assert bilibili.verify_transcript_duration("https://www.bilibili.com/video/BV1hN596zEas", "文" * 11000, 2) is None
