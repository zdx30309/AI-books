import json

import pytest

from app.services import bilibili, content_engine, deepseek, deepseek_editor
from app.services.aigc_quality_gate import AigcManualReviewRequired
from app.services.general_short_length import LengthHold, contract, issues, repair_length, word_count
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


def test_saved_short_response_gets_local_insertions_not_rewritten(tmp_path, monkeypatch):
    calls = []
    def generate(payload, **kwargs):
        data = json.loads(payload)
        calls.append(data)
        if "draft" in data:
            return "已保存段落" + "文" * 1400
        return json.dumps({"after": data["chapter_body"], "content": "补" * 970}, ensure_ascii=False)
    monkeypatch.setattr(deepseek, "generate_text", generate)
    result = repair_length(book(700), "原始资料", tmp_path)
    assert not issues(result)
    assert len(calls) == 18
    assert result.count("已保存段落") == 9
    assert repair_length(book(700), "原始资料", tmp_path) == result
    assert len(calls) == 18


def test_chapter_target_is_not_an_artificial_total_book_limit(tmp_path, monkeypatch):
    def generate(payload, **kwargs):
        data = json.loads(payload)
        if "draft" in data:
            return "文" * 1883
        return json.dumps({"after": data["chapter_body"], "content": "补" * 616}, ensure_ascii=False)
    monkeypatch.setattr(deepseek, "generate_text", generate)
    result = repair_length(book(700), "原始资料", tmp_path)
    assert not issues(result)
    assert result.count("文" * 1883) == 9
    assert len(list(tmp_path.glob("length_repair/*/[0-9][0-9]_response.md"))) == 9
    assert json.loads(next(tmp_path.glob("length_repair/*/result.json")).read_text())["omitted_optional_supplements"]


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
    assert content_engine._standard_longform_stage_structure_issue(book(2000), book(1900), category="general_short") is None
    assert content_engine._standard_longform_stage_structure_issue(book(2000), book(1900), category="general_short", enforce_short_length=True)


def test_normalized_refine_markers_are_not_false_short_book_errors():
    source = book()
    candidate = source.replace("@1. 标题", "！！！1. 标题").replace("@2. 标题", "！！！2. 标题")
    normalized = content_engine.normalize_final_markdown(candidate, category="general_short")
    assert not issues(normalized)
    assert content_engine._refine_output_integrity_issue(source, normalized, category="general_short") is None


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


STANDARD_CATEGORY = "social_science_core_conflict"


@pytest.mark.parametrize("size,count,valid", [(2200, 5, True), (5000, 5, True), (2199, 5, False),
                                              (5001, 5, False), (1400, 9, True), (1000, 12, True)])
def test_standard_longform_body_range(size, count, valid):
    assert (not issues(book(size, count), STANDARD_CATEGORY)) == valid
    assert not issues(book(2200, 5) + "\n@参考资料\n" + "文" * 20000, STANDARD_CATEGORY)
    assert issues(book(2200, 5))


def test_standard_contract_config_is_validated_and_target_clamped(monkeypatch):
    monkeypatch.setattr(content_engine.settings, "LONGFORM_TARGET_WORDS", 90000)
    assert contract(STANDARD_CATEGORY)["target"] == 25000
    assert contract()["target"] == 21000
    monkeypatch.setattr(content_engine.settings, "LONGFORM_MIN_WORDS", 26000)
    with pytest.raises(LengthHold, match="配置无效"):
        contract(STANDARD_CATEGORY)
    with pytest.raises(ValueError, match="不适用"):
        contract("classical_chinese")


def test_standard_draft_serial_repair_preserves_paid_responses_and_original(tmp_path, monkeypatch):
    calls = []

    def generate(payload, **kwargs):
        data = json.loads(payload)
        calls.append(data["chapter_number"])
        assert "标准长文" in kwargs["system_instruction"]
        assert data["minimum_words"] == 2800 and data["maximum_words"] == 3000
        return "中" * 2800

    monkeypatch.setattr(deepseek, "generate_text", generate)
    draft = book(1400, 5)
    result = repair_length(draft, "原始资料", tmp_path, category=STANDARD_CATEGORY)
    assert not issues(result, STANDARD_CATEGORY)
    assert calls == [1, 2, 3, 4, 5]
    assert repair_length(draft, "原始资料", tmp_path, category=STANDARD_CATEGORY) == result
    assert calls == [1, 2, 3, 4, 5]
    assert next(tmp_path.glob("length_repair/*/source.md")).read_text() == draft
    records = [json.loads(artifact.read_text()) for artifact in tmp_path.glob("length_repair/*/*_request.json")]
    assert all(record["action"] == "longform_length_repair" and record["status"] == "response_saved"
               and record["model"] and "cost" in record for record in records)


@pytest.mark.parametrize("failure", ["invalid", "uncertain"])
def test_standard_invalid_or_uncertain_paid_response_never_replayed(tmp_path, monkeypatch, failure):
    calls = []

    def generate(*args, **kwargs):
        calls.append(1)
        if failure == "uncertain":
            raise deepseek.DeepSeekDeliveryUncertainError("timeout")
        return "不足"

    monkeypatch.setattr(deepseek, "generate_text", generate)
    for attempt in range(2):
        with pytest.raises((AigcManualReviewRequired, deepseek.DeepSeekDeliveryUncertainError)):
            repair_length(book(1400, 5), "资料", tmp_path, category=STANDARD_CATEGORY)
    assert calls == [1]


def test_standard_supplements_are_local_bounded_and_checkpointed(tmp_path, monkeypatch):
    calls = []

    def generate(payload, **kwargs):
        data = json.loads(payload)
        calls.append(data)
        assert "标准长文" in kwargs["system_instruction"]
        if "draft" in data:
            return "保存原段落" + "文" * 1400
        return json.dumps({"after": data["chapter_body"], "content": "补" * data["minimum_words"]}, ensure_ascii=False)

    monkeypatch.setattr(deepseek, "generate_text", generate)
    result = repair_length(book(1400, 5), "资料", tmp_path, category=STANDARD_CATEGORY)
    assert not issues(result, STANDARD_CATEGORY)
    assert result.count("保存原段落") == 5
    assert len(calls) == 10
    assert repair_length(book(1400, 5), "资料", tmp_path, category=STANDARD_CATEGORY) == result
    assert len(calls) == 10


def test_standard_budget_keeps_existing_reservations_and_can_resume_before_payment(tmp_path, monkeypatch):
    from app.services.generation_spend import generation_budget, reserve

    paid = []

    def generate(*args, **kwargs):
        reserve("deepseek-text", 0.1, "deepseek-flash")
        paid.append(1)
        return "文" * 2800

    monkeypatch.setattr(deepseek, "generate_text", generate)
    with generation_budget({"generation_budget_cny": 1}, tmp_path):
        reserve("previous-paid-result", 0.95, "deepseek-flash")
        with pytest.raises(GenerationBudgetExceeded):
            repair_length(book(1400, 5), "资料", tmp_path, category=STANDARD_CATEGORY)
    assert not paid
    with generation_budget({"generation_budget_cny": 2}, tmp_path):
        result = repair_length(book(1400, 5), "资料", tmp_path, category=STANDARD_CATEGORY)
        assert not issues(result, STANDARD_CATEGORY)
        assert repair_length(book(1400, 5), "资料", tmp_path, category=STANDARD_CATEGORY) == result
    ledger = json.loads((tmp_path / "generation_spend.json").read_text())
    assert ledger["reserved_cny"] == 1.45 and len(ledger["calls"]) == 6 and len(paid) == 5
    assert ledger["calls"][0]["action"] == "previous-paid-result"


def test_standard_approved_short_repair_cannot_be_reused_for_another_contract(tmp_path):
    with pytest.raises(LengthHold, match="不能用于标准长文"):
        repair_length(book(1400, 5), "资料", tmp_path, category=STANDARD_CATEGORY, approved_sha256="approved")


def test_standard_oversized_or_impossible_draft_holds_without_payment(tmp_path, monkeypatch):
    monkeypatch.setattr(deepseek, "generate_text", lambda *args, **kwargs: pytest.fail("不得付费压缩或整篇重写"))
    for draft in (book(5001, 5), book(2000, 3)):
        with pytest.raises(AigcManualReviewRequired):
            repair_length(draft, "资料", tmp_path, category=STANDARD_CATEGORY)


def test_standard_expansion_never_compresses_an_existing_long_chapter(tmp_path, monkeypatch):
    draft = book(1000, 5).replace("中" * 1000, "中" * 5000, 1)
    calls = []

    def generate(payload, **kwargs):
        calls.append(json.loads(payload)["chapter_number"])
        return "补" * 2800

    monkeypatch.setattr(deepseek, "generate_text", generate)
    result = repair_length(draft, "资料", tmp_path, category=STANDARD_CATEGORY)
    assert not issues(result, STANDARD_CATEGORY)
    assert "中" * 5000 in result
    assert calls == [2, 3, 4, 5]


def test_standard_length_guard_does_not_extend_to_other_categories(monkeypatch):
    monkeypatch.setattr(content_engine.settings, "LONGFORM_LENGTH_GUARD_ENABLED", False)
    assert content_engine._length_guard_category(STANDARD_CATEGORY) is None
    assert content_engine._length_guard_category("general_short", True) == "general_short"
    monkeypatch.setattr(content_engine.settings, "LONGFORM_LENGTH_GUARD_ENABLED", True)
    for category in content_engine._STANDARD_LONGFORM_CATEGORY_IDS - {"general_short"}:
        assert content_engine._length_guard_category(category) == category
    for category in ("general_short", "classical_chinese", "comic_series", "healing_comic", "epic_novel", "general_longform"):
        assert content_engine._length_guard_category(category) is None
    assert content_engine._length_guard_category("general_short", True) == "general_short"


def test_standard_edit_gate_preserves_body_range_only_when_enabled(monkeypatch):
    monkeypatch.setattr(content_engine.settings, "LONGFORM_LENGTH_GUARD_ENABLED", True)
    assert content_engine._standard_longform_stage_structure_issue(book(2400, 5), book(2000, 5), category=STANDARD_CATEGORY)
    assert content_engine._standard_longform_stage_structure_issue(book(2400, 5), book(2300, 5), category=STANDARD_CATEGORY) is None
    monkeypatch.setattr(content_engine.settings, "LONGFORM_LENGTH_GUARD_ENABLED", False)
    assert content_engine._standard_longform_stage_structure_issue(book(2400, 5), book(2000, 5), category=STANDARD_CATEGORY) is None
