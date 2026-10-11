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
    monkeypatch.setattr(service, "call_correction", lambda original, text: {
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
    [{"original": "甲甲", "replacement": "乙", "remark": "错字", "severe": "false"}],
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
    monkeypatch.setattr(service, "call_correction", lambda *args: {"text": json.dumps({"issues": [
        {"original": "他认为认为这很好。", "replacement": "他认为这很好。", "remark": "严重问题", "severe": True}]})})
    with pytest.raises(service.FinalizerHold):
        service.finalize_manuscript("他认为认为这很好。", tmp_path)
    assert (tmp_path / "ai_or_finalizer/delivery.zip").exists()
