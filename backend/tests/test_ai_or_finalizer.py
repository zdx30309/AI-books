import json
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from xml.etree import ElementTree as ET

import pytest
from openpyxl import load_workbook

from app.services import ai_or_finalizer as service


def configure(monkeypatch):
    monkeypatch.setattr(service.settings, "AI_OR_CHUNK_CHARS", 1000)
    monkeypatch.setattr(service, "call_rewrite", lambda text: {"outputText": text.replace("认为认为", "认为认为")})
    monkeypatch.setattr(service, "call_correction", lambda original, text, cap=None: {
        "text": json.dumps({"issues": [{"original": "他认为认为这很好。", "replacement": "他认为这很好。",
                                          "remark": "词语重复", "severe": False}]}, ensure_ascii=False)})


def test_chunks_preserve_source_and_protected_lines():
    source = "#标题\n\n" + "甲" * 600 + "\n" + "乙" * 600 + "\n![图](image.png)\n"
    chunks = service.split_chunks(source, 1000)
    assert "".join(chunks) == source
    assert all(len(chunk) <= 1000 for chunk in chunks)
    assert chunks[0] == "#标题\n"


def test_oversized_paragraph_stops_before_calls():
    with pytest.raises(service.FinalizerHold):
        service.split_chunks("甲" * 1001, 1000)


@pytest.mark.parametrize("issues", [
    [{"original": "不存在", "replacement": "修改", "remark": "错字", "severe": False}],
    [{"original": "甲", "replacement": "乙", "remark": "错字", "severe": False}],
])
def test_ambiguous_or_invalid_corrections_are_blocked(issues):
    with pytest.raises(service.FinalizerHold):
        service.parse_issues(json.dumps({"issues": issues}), "甲甲")


def test_overlap_is_blocked():
    issues = [{"original": original, "replacement": "乙", "remark": "重复", "severe": False}
              for original in ("甲丙丁", "丙丁")]
    with pytest.raises(service.FinalizerHold):
        service.parse_issues(json.dumps({"issues": issues}), "甲丙丁")


def test_resume_does_not_repeat_paid_calls(tmp_path, monkeypatch):
    configure(monkeypatch)
    source = "#标题\n他认为认为这很好。\n"
    final = service.finalize_manuscript(source, tmp_path)
    assert final == "#标题\n他认为这很好。\n"
    def forbidden(*args):
        raise AssertionError("paid call repeated")
    monkeypatch.setattr(service, "call_rewrite", forbidden)
    monkeypatch.setattr(service, "call_correction", forbidden)
    assert service.finalize_manuscript(source, tmp_path) == final
    manifest = json.loads((tmp_path / "ai_or_finalizer/manifest.json").read_text())
    assert manifest["detection"] == "not_requested"
    assert manifest["status"] == "completed"


def test_rewrite_chunk_budget_is_scoped_and_checkpoint_plan_frozen(tmp_path, monkeypatch):
    from app.services.generation_spend import GenerationBudgetExceeded

    monkeypatch.setattr(service.settings, "AI_OR_CHUNK_CHARS", 6000)
    calls = []
    monkeypatch.setattr(service, "call_rewrite", lambda text: calls.append(text) or {"outputText": text})
    def blocked(*args):
        raise GenerationBudgetExceeded("budget before request")
    monkeypatch.setattr(service, "call_correction", blocked)
    source = "甲" * 1200 + "\n" + "乙" * 1200 + "\n"
    with pytest.raises(GenerationBudgetExceeded):
        service.finalize_manuscript(source, tmp_path, rewrite_chunk_chars=1500)
    monkeypatch.setattr(service, "call_correction", lambda *args: {"text": '{"issues": []}'})
    assert service.finalize_manuscript(source, tmp_path, rewrite_chunk_chars=3000) == source
    state = json.loads((tmp_path / "ai_or_finalizer/manifest.json").read_text())
    assert state["rewrite_chunk_chars"] == 1500
    assert len(calls) == 2
    assert service.settings.AI_OR_CHUNK_CHARS == 6000


def test_uncertain_correction_never_replays(tmp_path, monkeypatch):
    configure(monkeypatch)
    attempts = []
    def fail(*args):
        attempts.append(True)
        raise TimeoutError()
    monkeypatch.setattr(service, "call_correction", fail)
    for _attempt in range(2):
        with pytest.raises(service.FinalizerHold):
            service.finalize_manuscript("他认为认为这很好。", tmp_path)
    assert len(attempts) == 1


def test_uncertain_rewrite_never_replays(tmp_path, monkeypatch):
    configure(monkeypatch)
    attempts = []
    def fail(*args):
        attempts.append(True)
        raise TimeoutError()
    monkeypatch.setattr(service, "call_rewrite", fail)
    for _attempt in range(2):
        with pytest.raises(service.FinalizerHold):
            service.finalize_manuscript("正文。", tmp_path)
    assert len(attempts) == 1


def test_changed_source_is_blocked(tmp_path, monkeypatch):
    configure(monkeypatch)
    service.finalize_manuscript("他认为认为这很好。", tmp_path)
    with pytest.raises(service.FinalizerHold):
        service.finalize_manuscript("新原稿", tmp_path)


def test_documents_contain_revisions_and_matching_names(tmp_path):
    issues = [{"original": "错文", "replacement": "正文", "remark": "严重问题", "severe": True}]
    artifacts = service.write_delivery(tmp_path, "原稿", "错文", "正文", issues, "测试书", "2026年10月10日_01时02分03秒")
    assert artifacts["xlsx"].removesuffix(".xlsx") == artifacts["docx"].removesuffix(".docx")
    workbook = load_workbook(tmp_path / artifacts["xlsx"])
    assert workbook.active["A2"].font.color.rgb.endswith("FF0000")
    assert workbook.active["A1"].font.name == "微软雅黑"
    with zipfile.ZipFile(BytesIO((tmp_path / artifacts["docx"]).read_bytes())) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
        namespace = {"w": service.WORD}
        assert root.find(".//w:del", namespace) is not None
        assert root.find(".//w:ins", namespace) is not None
        assert all(node.attrib[f"{{{service.WORD}}}author"] == "降AI后纠错"
                   for node in root.findall(".//w:ins", namespace) + root.findall(".//w:del", namespace))
        assert b"trackRevisions" in archive.read("word/settings.xml")


def test_only_new_tasks_are_enabled(monkeypatch):
    monkeypatch.setattr(service.settings, "AI_OR_FINALIZER_ENABLED", True)
    monkeypatch.setattr(service.settings, "AI_OR_FINALIZER_START_AT", "2026-10-10T00:00:00+00:00")
    assert not service.enabled_for(datetime(2026, 10, 9, tzinfo=timezone.utc))
    assert service.enabled_for(datetime(2026, 10, 10, tzinfo=timezone.utc))
    assert not service.enabled_for(None)


def test_severe_issue_requires_manual_review(tmp_path, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(service.settings, "AI_OR_SEVERE_BLOCKS_DELIVERY", True)
    monkeypatch.setattr(service, "call_correction", lambda *args: {"text": json.dumps({"issues": [
        {"original": "他认为认为这很好。", "replacement": "他认为这很好。", "remark": "严重问题", "severe": True}]})})
    with pytest.raises(service.FinalizerHold):
        service.finalize_manuscript("他认为认为这很好。", tmp_path)
    assert (tmp_path / "ai_or_finalizer/delivery.zip").exists()


def test_correction_pieces_rejoin_and_respect_limit():
    text = "".join(f"第{i}段" + "甲" * 300 + "\n" for i in range(10))
    pieces = service.correction_pieces(text, 1000)
    assert "".join(pieces) == text
    assert len(pieces) > 1
    assert all(len(piece) <= 1000 for piece in pieces)


def test_long_single_paragraph_correction_is_bounded_without_text_loss():
    text = "长句" * 800 + "。" + "无标点" * 1200 + "\n"
    pieces = service.correction_pieces(text, 1000)
    assert "".join(pieces) == text
    assert all(len(piece) <= 1000 for piece in pieces)


def test_correction_runs_per_piece_and_applies_each(tmp_path, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(service.settings, "AI_OR_CHUNK_CHARS", 5000)
    monkeypatch.setattr(service.settings, "AI_OR_CORRECTION_CHUNK_CHARS", 600)
    source = "他认为认为这很好。" + "甲" * 500 + "\n" + "她认为认为那不错。" + "乙" * 500 + "\n"
    seen = []

    def correct(original, piece, cap=None):
        seen.append((original, piece))
        before = "他认为认为这很好。" if "他认为认为" in piece else "她认为认为那不错。"
        return {"text": json.dumps({"issues": [{"original": before, "replacement": before.replace("认为认为", "认为"),
                                                  "remark": "词语重复", "severe": False}]}, ensure_ascii=False)}
    monkeypatch.setattr(service, "call_correction", correct)
    final = service.finalize_manuscript(source, tmp_path)
    assert len(seen) == 2 and all(original == source for original, _piece in seen)
    assert "认为认为" not in final and final.count("认为") == 2


def test_truncated_whole_chunk_resumes_with_split_without_paying_rewrite_again(tmp_path, monkeypatch):
    configure(monkeypatch)
    source = "他认为认为这很好。\n"
    rewrites = []
    monkeypatch.setattr(service, "call_rewrite", lambda text: rewrites.append(1) or {"outputText": text})

    def truncated(*args):
        raise service.CorrectionTruncated(service.TRUNCATED)
    monkeypatch.setattr(service, "call_correction", truncated)
    with pytest.raises(service.FinalizerHold):
        service.finalize_manuscript(source, tmp_path)
    # Simulate the manifest left by the earlier version: whole-chunk call still marked inflight.
    manifest_path = tmp_path / "ai_or_finalizer/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["chunks"][0]["actions"] = {"rewrite": "saved", "correction": "inflight"}
    manifest["chunks"][0].pop("correction_split", None)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False))
    for stale in (tmp_path / "ai_or_finalizer").glob("00000_correction_*"):
        stale.unlink()
    configure(monkeypatch)
    monkeypatch.setattr(service, "call_rewrite", lambda text: (_ for _ in ()).throw(AssertionError("rewrite repeated")))
    assert service.finalize_manuscript(source, tmp_path) == "他认为这很好。\n"
    assert len(rewrites) == 1


def test_uncertain_legacy_inflight_correction_still_blocks(tmp_path, monkeypatch):
    configure(monkeypatch)
    source = "他认为认为这很好。\n"
    monkeypatch.setattr(service, "call_correction", lambda *args: (_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(service.FinalizerHold):
        service.finalize_manuscript(source, tmp_path)
    manifest_path = tmp_path / "ai_or_finalizer/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["chunks"][0]["actions"]["correction"] = "inflight"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False))
    configure(monkeypatch)
    with pytest.raises(service.FinalizerHold):
        service.finalize_manuscript(source, tmp_path)


def test_truncated_piece_does_not_replay_until_limit_raised(tmp_path, monkeypatch):
    configure(monkeypatch)
    attempts = []

    def truncated(*args):
        attempts.append(1)
        raise service.CorrectionTruncated(service.TRUNCATED)
    monkeypatch.setattr(service, "call_correction", truncated)
    for _attempt in range(2):
        with pytest.raises(service.FinalizerHold):
            service.finalize_manuscript("他认为认为这很好。", tmp_path)
    assert len(attempts) == 1
    configure(monkeypatch)
    monkeypatch.setattr(service.settings, "AI_OR_CORRECTION_MAX_TOKENS", 16384)
    assert service.finalize_manuscript("他认为认为这很好。", tmp_path) == "他认为这很好。"


def test_rewrite_body_requires_work_mode(monkeypatch):
    monkeypatch.setattr(service.settings, "AI_OR_WORK_MODE_ID", 0)
    monkeypatch.setattr(service.settings, "AI_OR_WORK_MODE", "")
    with pytest.raises(service.FinalizerHold):
        service._rewrite_body("正文")


def test_rewrite_body_uses_work_mode_id_first(monkeypatch):
    monkeypatch.setattr(service.settings, "AI_OR_WORK_MODE_ID", 123)
    monkeypatch.setattr(service.settings, "AI_OR_WORK_MODE", "中文/降AI（学术）/大雅")
    body = service._rewrite_body("正文")
    assert body["workModeId"] == 123
    assert "workMode" not in body


def test_rewrite_body_falls_back_to_work_mode_path(monkeypatch):
    monkeypatch.setattr(service.settings, "AI_OR_WORK_MODE_ID", 0)
    monkeypatch.setattr(service.settings, "AI_OR_WORK_MODE", "中文/降AI（学术）/大雅")
    body = service._rewrite_body("正文")
    assert body["workMode"] == "中文/降AI（学术）/大雅"
    assert "workModeId" not in body
