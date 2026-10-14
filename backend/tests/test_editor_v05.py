import json
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services import ai_or_finalizer as finalizer
from app.services import editor_v05 as service


SOURCE = "甲银行2020年花费10万元。案例是采购设备。第二个论点是节省费用。"
DRAFT = "#费用的边界\n甲银行2020年花费10万元。采购设备是一个案例。另一种方法是提高效率。"


def payload():
    return {
        "units": [
            {"source_quote": "甲银行2020年花费10万元。", "unit": "设备投入", "draft_quote": "甲银行2020年花费10万元。", "location": "正文第1段", "level": "完整"},
            {"source_quote": "第二个论点是节省费用。", "unit": "节省费用", "draft_quote": "", "location": "无", "level": "无"},
        ],
        "case_order_score": 25, "case_order_reason": "只保留采购设备案例，另增效率方案。",
        "narrative_score": 40, "narrative_reason": "投入论点保留，主线改为效率。",
        "data_points": [
            {"source_quote": "甲银行2020年", "point": "2020年", "draft_quote": "甲银行2020年", "reused": True},
            {"source_quote": "花费10万元", "point": "10万元", "draft_quote": "花费10万元", "reused": True},
        ],
        "cases": [{"source_quote": "案例是采购设备。", "case": "采购设备", "draft_quote": "采购设备是一个案例。", "level": "部分"}],
        "entities": ["甲银行"], "differentiation_list": ["新增提高效率方案"],
        "fix_directive": "按效率与投入重排骨架，替换采购设备案例。",
        "risks": {name: {"level": "中", "reason": "投入与案例部分沿用，需编辑判断。"}
                  for name in ("著作权风险", "反不正当竞争风险", "平台原创审核风险")},
        "notes": "语义分项需要人工复核",
    }


def configure(monkeypatch):
    monkeypatch.setattr(service.settings, "EDITOR_V05_ENABLED", True)
    monkeypatch.setattr(service.settings, "EDITOR_V05_START_AT", "2026-10-13T00:00:00Z")
    monkeypatch.setattr(service.settings, "AI_OR_FINALIZER_ENABLED", True)
    monkeypatch.setattr(service.settings, "AI_OR_FINALIZER_START_AT", "")
    monkeypatch.setattr(service.settings, "AI_OR_SEVERE_BLOCKS_DELIVERY", False)
    monkeypatch.setattr(service, "call_audit", lambda *args: {"text": json.dumps(payload(), ensure_ascii=False), "finish_reason": "stop", "model": "deepseek-flash"})
    monkeypatch.setattr(finalizer, "call_rewrite", lambda text: {"outputText": text})
    monkeypatch.setattr(finalizer, "call_correction", lambda *args: {"text": '{"issues": []}'})


def task(**config):
    return SimpleNamespace(created_at=datetime(2026, 10, 14, tzinfo=timezone.utc), source_url="https://example.org/video",
                           config={"category": "social_science_core_conflict", "raw_text": SOURCE, **config})


def test_pasted_video_share_uses_parsed_canonical_url_for_editor_gate(monkeypatch):
    configure(monkeypatch)
    current = task(source_canonical_url="https://www.bilibili.com/video/BV1hN596zEas")
    current.source_url = "【资本是如何接连引爆多个国家的金融危机的？-哔哩哔哩】 https://b23.tv/cdgDaff"
    assert service.applies(current)
    current.created_at = datetime(2026, 10, 12, tzinfo=timezone.utc)
    assert not service.applies(current)
    current.created_at = datetime(2026, 10, 14, tzinfo=timezone.utc)
    current.config["category"] = "general_longform"
    assert not service.applies(current)


def test_formulas_are_recomputed_from_evidence():
    report = service.scored_report(SOURCE, DRAFT, payload(), "费用的边界")
    audit = report.sources[0]
    assert audit.structure.unit_match_score == 50
    assert audit.structure.structure_score == 39.5
    assert audit.material_data == 100
    assert audit.material_case == 50
    assert audit.material_entity == 100
    assert audit.material_score == service.one_decimal(30 + 12.5 + 20 + audit.material_literal * .25)
    assert report.max_composite == service.composite_score(39.5, audit.material_score)
    assert audit.unit_none_count == 1


def test_absent_named_subject_is_retained():
    report = service.scored_report(SOURCE, DRAFT.replace("甲银行", "乙银行"), {
        **payload(), "units": [{"source_quote": SOURCE, "unit": "投入", "draft_quote": "", "location": "无", "level": "无"}],
        "data_points": [], "cases": [],
    }, "测试")
    assert report.sources[0].entity_counts[0]["draft_count"] == "0"
    assert report.sources[0].material_entity == 0


@pytest.mark.parametrize("change", [
    lambda value: value["units"][0].update(draft_quote="模型编造的新稿"),
    lambda value: value["units"][0].update(source_quote="模型编造的原稿"),
    lambda value: value.update(case_order_score=60),
    lambda value: value.update(narrative_score=90),
    lambda value: value.update(differentiation_list=[]),
    lambda value: value.update(risks={}),
])
def test_invalid_evidence_blocks_score(change):
    value = payload()
    change(value)
    with pytest.raises(finalizer.FinalizerHold):
        service.scored_report(SOURCE, DRAFT, value, "测试")


def test_data_point_label_can_summarize_exactly_quoted_fact():
    value = payload()
    value["data_points"][0]["point"] = "2020年设备投入的年份"
    report = service.scored_report(SOURCE, DRAFT, value, "测试")
    assert report.sources[0].material_data == 100


def test_regeneration_settles_new_audit_without_colliding_with_previous_receipt(tmp_path, monkeypatch):
    import shutil
    from app.services.generation_spend import generation_budget, reserve

    configure(monkeypatch)
    first = tmp_path / "first"
    second = tmp_path / "second"
    for generation, directory in ((1, first), (2, second)):
        directory.mkdir()
        if generation == 2:
            shutil.copy2(first / "generation_spend.json", second / "generation_spend.json")
        with generation_budget({"generation_budget_cny": 20}, directory):
            index = reserve("deepseek-text", 1, "deepseek-flash")
            monkeypatch.setattr(service, "call_audit", lambda *args: {
                "text": json.dumps(payload(), ensure_ascii=False), "finish_reason": "stop", "model": "deepseek-flash",
                "budget_reservation_index": index, "response_id": f"audit-{generation}",
                "usage": {"prompt_tokens": 100, "completion_tokens": 100},
            })
            service.audit_manuscript(SOURCE, DRAFT, directory / "editor_v05", "测试", generation_attempt=generation)
    ledger = json.loads((second / "generation_spend.json").read_text())
    assert len(ledger["calls"]) == 2
    assert all(call["status"] == "usage_verified_upper_bound_not_actual_invoice" for call in ledger["calls"])
    assert ledger["calls"][0]["receipt"] == "editor_v05/audit_response.json"
    assert ledger["calls"][1]["receipt"] == "editor_v05/audit_response_generation_2.json"


def test_source_hash_and_saved_result_prevent_paid_replay(tmp_path, monkeypatch):
    configure(monkeypatch)
    directory = tmp_path / "editor_v05"
    service.audit_manuscript(SOURCE, DRAFT, directory, "费用的边界")
    monkeypatch.setattr(service, "call_audit", lambda *args: pytest.fail("重复付费"))
    service.audit_manuscript(SOURCE, DRAFT, directory, "费用的边界")
    with pytest.raises(finalizer.FinalizerHold):
        service.audit_manuscript(SOURCE + "新信息", DRAFT, directory, "费用的边界")


def test_uncertain_audit_cannot_replay(tmp_path, monkeypatch):
    configure(monkeypatch)
    calls = []

    def fail(*args):
        calls.append(True)
        raise TimeoutError()

    monkeypatch.setattr(service, "call_audit", fail)
    for attempt in range(2):
        with pytest.raises((TimeoutError, finalizer.FinalizerHold)):
            service.audit_manuscript(SOURCE, DRAFT, tmp_path / "editor_v05", "测试")
    assert len(calls) == 1


def test_copy_short_circuits_at_100_without_model(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "call_audit", lambda *args: pytest.fail("副本不应付费"))
    audit = service.audit_manuscript(SOURCE, SOURCE, tmp_path / "editor_v05", "测试")
    assert audit["data"]["max_composite"] == 100
    assert audit["data"]["overall_verdict"] == "高危"


def test_missing_source_blocks_before_paid_calls(tmp_path, monkeypatch):
    configure(monkeypatch)
    current = task(raw_text="")
    monkeypatch.setattr(service, "call_audit", lambda *args: pytest.fail("没有原稿不能付费"))
    with pytest.raises(finalizer.FinalizerHold):
        service.finalize_editor_task(current, DRAFT, tmp_path)


def test_v05_two_word_delivery_and_resume(tmp_path, monkeypatch):
    # Delivery mechanics only; the similarity stop is covered in test_editor_v05_similarity_gate.py.
    monkeypatch.setattr(service.settings, "EDITOR_V05_SIMILARITY_GATE_ENABLED", False)
    configure(monkeypatch)
    current = task()
    assert finalizer.finalize_task_manuscript(current, DRAFT, tmp_path) == DRAFT
    directory = tmp_path / "editor_v05/ai_or_finalizer"
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["version"] == "editor-v0.5"
    assert manifest["correction_prompt_hash"]
    assert current.config["editor_v05_status"] == "completed"
    with zipfile.ZipFile(directory / "delivery.zip") as archive:
        assert len(archive.namelist()) == 2
        assert all(name.endswith(".docx") for name in archive.namelist())
        assert archive.testzip() is None
    assert list(directory.glob("*.xlsx"))
    monkeypatch.setattr(service, "call_audit", lambda *args: pytest.fail("重复审查"))
    monkeypatch.setattr(finalizer, "call_rewrite", lambda *args: pytest.fail("重复降AI"))
    monkeypatch.setattr(finalizer, "call_correction", lambda *args: pytest.fail("重复纠错"))
    assert finalizer.finalize_task_manuscript(current, DRAFT, tmp_path) == DRAFT


def test_low_similarity_completes_two_word_delivery_with_gate_enabled(tmp_path, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(service.settings, "EDITOR_V05_SIMILARITY_GATE_ENABLED", True)
    safe_draft = "#城市夜灯\n晚上街道亮起了灯，行人慢慢穿过广场。公园里有人练习合唱，歌声随着风飘远。"
    evidence = payload()
    for row in evidence["units"] + evidence["cases"]:
        row.update(level="无", draft_quote="", location="无")
    for row in evidence["data_points"]:
        row.update(reused=False, draft_quote="")
    evidence.update(case_order_score=10, narrative_score=10)
    monkeypatch.setattr(service, "call_audit", lambda *args: {
        "text": json.dumps(evidence, ensure_ascii=False), "finish_reason": "stop", "model": "deepseek-flash"})
    current = task()
    assert finalizer.finalize_task_manuscript(current, safe_draft, tmp_path) == safe_draft
    assert current.config["editor_v05_similarity"] < 50
    assert current.config["editor_v05_status"] == "completed"
    with zipfile.ZipFile(tmp_path / "editor_v05/ai_or_finalizer/delivery.zip") as archive:
        assert len(archive.namelist()) == 2
        assert all(name.endswith(".docx") for name in archive.namelist())
        assert archive.testzip() is None


def test_old_tasks_and_other_engines_are_not_migrated(monkeypatch):
    configure(monkeypatch)
    for category in ("general_longform", "public_domain_rewrite", "classical_chinese", "comic_series"):
        assert not service.applies(task(category=category))
    old = task()
    old.created_at = datetime(2026, 10, 12, tzinfo=timezone.utc)
    assert not service.applies(old)


def test_zhuque_request_has_no_academic_mode(monkeypatch):
    rewrite = finalizer.call_rewrite
    configure(monkeypatch)
    monkeypatch.setattr(service.settings, "AI_OR_ACCESS_KEY", "test")
    monkeypatch.setattr(service.settings, "AI_OR_ACCESS_SECRET", "test")
    requests = []

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            requests.append((url, kwargs["json"]))
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": {"status": 1, "outputText": "正文"}})

    monkeypatch.setattr(finalizer.httpx, "Client", Client)
    token = finalizer._profile.set("editor-v0.5")
    try:
        rewrite("正文")
    finally:
        finalizer._profile.reset(token)
    url, body = requests[0]
    assert url.endswith("/workflow/zhuque-direct")
    assert body["repair_paragraphs"] is False
    assert "workMode" not in body and "workModeId" not in body


def test_string_severity_and_no_op_do_not_abort():
    result, issues = finalizer.parse_issues(json.dumps({"issues": [
        {"original": "甲", "replacement": "甲", "remark": "无需修改"},
        {"original": "甲乙", "replacement": "丙", "remark": "错字", "severe": "false"},
    ]}), "甲乙")
    assert result == "丙" and issues[0]["severe"] is False and len(issues) == 1


def test_unicode_names_keep_title_and_replace_spaces(tmp_path):
    artifacts = finalizer.write_delivery(tmp_path, "原稿", "错字", "正文", [], "攒钱攻略: 把钱花在健康", "2026年10月13日_01时02分03秒")
    assert "攒钱攻略：_把钱花在健康" in artifacts["docx"]
    assert not any(char.isspace() for char in artifacts["docx"])
    assert "%" not in artifacts["docx"]


def test_packaging_and_real_book_title_are_not_rewritten(tmp_path, monkeypatch):
    configure(monkeypatch)
    source = "@最终书名\n费用的边界\n@最终简介\n简介保持原样\n@目录\n第1节 费用\n@正文\n#第1节 费用\n只有这句正文需要处理。\n"
    seen = []
    monkeypatch.setattr(finalizer, "call_rewrite", lambda text: seen.append(text) or {"outputText": text})
    assert finalizer.finalize_manuscript(source, tmp_path, profile=service.VERSION) == source
    assert seen == ["只有这句正文需要处理。\n"]


def test_v05_download_routes_use_two_word_artifacts(tmp_path, monkeypatch):
    # Delivery mechanics only; the similarity stop is covered in test_editor_v05_similarity_gate.py.
    monkeypatch.setattr(service.settings, "EDITOR_V05_SIMILARITY_GATE_ENABLED", False)
    from app.api.routers import tasks as router
    from app.models import TaskStatus

    configure(monkeypatch)
    current = task()
    current.id, current.owner_id = "test", "owner"
    current.status = TaskStatus.COMPLETED
    finalizer.finalize_task_manuscript(current, DRAFT, tmp_path)
    monkeypatch.setattr(router, "_task_output_dir", lambda *args: tmp_path)
    monkeypatch.setattr(router, "_read_task_markdown", lambda *args: DRAFT)
    response = router._build_single_download_response(None, current, "docx")
    assert "editor_v05/ai_or_finalizer" in str(response.path)
    assert response.filename.startswith("【降AI后纠错】_费用的边界_")
    response = router.download_ai_or_delivery("test", SimpleNamespace(get=lambda *args: current), SimpleNamespace(id="owner"))
    with zipfile.ZipFile(response.path) as archive:
        assert len(archive.namelist()) == 2
