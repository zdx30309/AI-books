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
# The writer receives every rule section plus the two whole-book anchors the
# original designates (appendix two and its reports in appendix three). What
# must never reach any generation stage is the audit instruction, which lives in
# its own stage and is a separate task.
BANNED_IN_ALL_STAGES = (
    "第零部分　这份指令词要解决什么",
    "文本相似度审查指令词：结构框架与素材双维打分版",
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


def test_writer_prompt_carries_every_original_rule_without_the_sample_prose() -> None:
    prompt = get_prompts("general_short")["full_book_writer"]
    # The full v09 rule set is fed to the writer (sections one through nine plus
    # appendices one, four and five), so it is larger than the earlier trimmed
    # build but still far below the 61,044-character original mega-prompt that
    # additionally embedded two complete sample books and three reports.
    assert len(prompt) < 80000, len(prompt)
    for banned in BANNED_IN_ALL_STAGES:
        assert banned not in prompt, banned
    # Every original rule section and anchor must be present.
    for marker in ("一 执行身份与材料边界", "三 选题分类与内容定位",
                   "四 联网研究与证据规则", "五 书名与简介策划",
                   "六 大纲与约 2 万字分配", "八 人工表达",
                   "九 防重复、来源差异化", "十 最终文本相似度审查",
                   "十二 交付前验收", "附录一 微信读书选题清单",
                   "附录二 正反写作样稿全文", "附录三 正反案例检测报告",
                   "附录四 人工表达写作方法", "附录五 书名与简介风格参考"):
        assert marker in prompt, marker


def test_bundle_manifest_matches_files_on_disk() -> None:
    manifest = get_manifest()
    assert manifest["version"] == BUNDLE_VERSION
    for section in manifest["sections"]:
        assert (BUNDLE / section["file"]).exists()
    assert stage_source("draft").strip()
    assert stage_source("differentiation_audit") == ""
