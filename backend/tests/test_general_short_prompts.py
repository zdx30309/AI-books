"""通用短篇分层提示词：分层装配与防污染回归测试。"""
from __future__ import annotations

import pytest

from app.prompts.general_short_prompts import (
    BUNDLE,
    FINGERPRINT_TOKEN,
    BUNDLE_VERSION,
    get_manifest,
    render_stage,
    stage_source,
    render_writer_prompt,
    writer_template,
)
from app.prompts.goal_driven_templates import get_prompts

# 两篇成品样稿、三份检测报告与审查规则，在任何阶段都不允许出现。
BANNED_IN_ALL_STAGES = (
    "中国白酒四十年",
    "越虐越上头",
    "附录二 正反写作样稿全文",
    "附录三 正反案例检测报告",
    "附录四 人工表达写作方法",
    "附录五 书名与简介风格参考",
    "附录六 文本相似度审查规则",
    "附录 A",
)


def test_writer_template_has_both_markers() -> None:
    template = writer_template()
    assert FINGERPRINT_TOKEN in template
    assert "{raw_text}" in template
    # The token must survive the shared ``.format(raw_text=...)`` routing call.
    assert FINGERPRINT_TOKEN in template.format(raw_text="RAW")


def test_writer_template_substitutes_in_order() -> None:
    rendered = render_writer_prompt("FP-BLOCK", "RAW-BLOCK")
    assert "FP-BLOCK" in rendered
    assert "RAW-BLOCK" in rendered
    assert FINGERPRINT_TOKEN not in rendered
    assert rendered.index("FP-BLOCK") < rendered.index("RAW-BLOCK")


def test_draft_stage_carries_the_differentiation_directive() -> None:
    rendered = render_stage("draft", {"fingerprint": "FP", "raw_text": "RAW"})
    assert "结构指纹" in rendered
    assert "反向可追溯自检" in rendered
    assert "严禁把原文的一级观点直接改成章节" in rendered


def test_draft_stage_uses_the_abstracted_fingerprint_not_raw_structure() -> None:
    rendered = render_stage("draft", {"fingerprint": "FP-BLOCK", "raw_text": "RAW-BLOCK"})
    assert "【灵感原文结构指纹（仅用于规避，禁止照抄其内容）】\nFP-BLOCK" in rendered


def test_structure_extract_stage_is_json_only() -> None:
    rendered = render_stage("structure_extract", {"raw_text": "RAW"})
    assert '"argument_units"' in rendered
    assert '"case_sequence"' in rendered
    assert "RAW" in rendered


def test_audit_stage_keeps_the_frozen_thresholds() -> None:
    rendered = render_stage(
        "differentiation_audit", {"source_text": "SRC", "draft_text": "DRF"}
    )
    assert "S = A" in rendered
    assert "S\u00d70.60 + M\u00d70.40" in rendered
    assert "SRC" in rendered and "DRF" in rendered


@pytest.mark.parametrize("stage", ["draft", "structure_extract", "differentiation_audit"])
def test_no_sample_books_or_audit_spec_enter_any_stage(stage: str) -> None:
    values = {"fingerprint": "FP", "raw_text": "RAW", "source_text": "S", "draft_text": "D"}
    rendered = render_stage(stage, values)
    for banned in BANNED_IN_ALL_STAGES:
        assert banned not in rendered, f"{stage} leaked {banned}"


def test_writer_prompt_is_cut_down_from_the_v09_mega_prompt() -> None:
    prompt = get_prompts("general_short")["full_book_writer"]
    # v09 writer prompt was 61,044 chars; the layered writer must be far smaller.
    assert len(prompt) < 20000, len(prompt)
    for banned in BANNED_IN_ALL_STAGES:
        assert banned not in prompt, banned


def test_bundle_manifest_matches_files_on_disk() -> None:
    manifest = get_manifest()
    assert manifest["version"] == BUNDLE_VERSION
    for section in manifest["sections"]:
        assert (BUNDLE / section["file"]).exists()
    assert stage_source("draft").strip()
    assert stage_source("differentiation_audit") == ""
