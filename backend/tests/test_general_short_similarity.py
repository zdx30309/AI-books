"""通用短篇相似度审查：v09 第十节 + 附录六的逐条回归测试。"""
from __future__ import annotations

import json
from pathlib import Path

from app.services.general_short_similarity import (
    GRAM_SIZES,
    HIGH_RISK_THRESHOLD,
    JUST_OVER_MAX,
    NEAR_BOUNDARY_MIN,
    STRUCTURE_FALLBACK_THRESHOLD,
    audit_multi_source,
    audit_one_source,
    boundary_label,
    composite_score,
    denoise,
    structure_fallback_notice,
)
from app.services.general_short_report import (
    REPORT_DIR_NAME,
    build_report_docx,
    report_filename,
    write_report,
)

SRC = (
    "你有没有想过，为什么越省钱反而越穷？我认识一位朋友，他每个月记三千笔账。"
    "2023年央行数据显示居民储蓄率上升。比如日本失落的三十年，比如韩国的高负债家庭。"
    "因为收入停滞，所以消费收缩，导致经济下行。建议你第一步先做预算。"
) * 6


# ---- 字面层 ----

def test_literal_reports_every_required_gram_size() -> None:
    a = audit_one_source("s", SRC, SRC[:2000])
    assert [row.size for row in a.literal.ngrams] == list(GRAM_SIZES)


def test_composite_keeps_exactly_one_decimal() -> None:
    a = audit_one_source("s", SRC, SRC[:2000])
    text = f"{a.composite}"
    assert "." in text and len(text.split(".")[1]) == 1


def test_identical_files_short_circuit_at_full_similarity() -> None:
    a = audit_one_source("s", SRC, SRC)
    assert a.literal.is_copy is True
    assert a.composite == 100.0
    assert a.verdict == "高危"


def test_denoise_drops_timeline_and_wordcount_lines() -> None:
    noisy = "[00:00 - 00:40] 开头\n正文内容\n全文：1234 字\n"
    cleaned = denoise(noisy)
    assert "00:00" not in cleaned
    assert "全文" not in cleaned
    assert "正文内容" in cleaned


# ---- 标注与兜底 ----

def test_boundary_labels_match_the_spec() -> None:
    assert boundary_label(44.9) == ""
    assert boundary_label(45.0) == "【临界限】"
    assert boundary_label(49.9) == "【临界限】"
    assert boundary_label(50.0) == "【临界点】"
    assert boundary_label(52.3) == "【刚过线】"
    assert boundary_label(55.0) == "【刚过线】"
    assert boundary_label(55.1) == ""
    assert NEAR_BOUNDARY_MIN == 45.0 and JUST_OVER_MAX == 55.0


def test_structure_fallback_notice_is_mandatory_at_seventy() -> None:
    assert structure_fallback_notice(69.9) == ""
    assert "改骨架" in structure_fallback_notice(70.0)
    assert STRUCTURE_FALLBACK_THRESHOLD == 70.0


def test_weights_are_frozen() -> None:
    assert composite_score(100.0, 0.0) == 60.0
    assert composite_score(0.0, 100.0) == 40.0
    assert composite_score(50.0, 50.0) == 50.0
    assert HIGH_RISK_THRESHOLD == 50.0


# ---- 多来源 ----

def test_multi_source_gates_on_the_highest_composite() -> None:
    other = "完全无关的另一段材料，讨论的是别的主题。" * 20
    report = audit_multi_source([("视频A", SRC), ("视频B", other)], SRC)
    assert report.applicable is True
    assert len(report.sources) == 2
    assert report.max_composite == max(s.composite for s in report.sources)
    assert report.overall_verdict == "高危"


def test_no_source_marks_the_step_not_applicable() -> None:
    report = audit_multi_source([], SRC)
    assert report.applicable is False
    assert "不适用" in report.reason
    assert report.sources == []


# ---- Word 报告 ----

def test_report_filename_matches_the_required_pattern() -> None:
    name = report_filename("测试书名")
    assert name.startswith("测试书名_文本相似度审查报告_")
    assert name.endswith(".docx")
    assert "年" in name and "时" in name and "分" in name and "秒" in name


def test_report_docx_is_a_valid_zip_package() -> None:
    report = audit_multi_source([("视频A", SRC)], SRC[:1500], draft_name="测试书_合并版")
    data = build_report_docx(report, book_title="测试书")
    assert data[:2] == b"PK"
    import io, zipfile
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert "word/document.xml" in z.namelist()
        assert "word/styles.xml" in z.namelist()
        doc = z.read("word/document.xml").decode("utf-8")
        styles = z.read("word/styles.xml").decode("utf-8")
    # Appendix B skeleton: the six parts appear in order, conclusion first.
    order = ["第一部分　结论", "第二部分　打分明细",
             "第三部分　结构框架相似度逐条依据", "第四部分　素材相似度逐条依据",
             "第五部分　风险定性与建议", "第六部分　附录：检测口径与可复现说明"]
    positions = [doc.find(item) for item in order]
    assert all(p >= 0 for p in positions), positions
    assert positions == sorted(positions), positions

    for style_id in ("Heading1", "Heading2", "Heading3"):
        assert f'w:styleId="{style_id}"' in styles
    # Fonts per appendix six: 黑体 headings, 宋体 body.
    assert "黑体" in styles and "宋体" in styles

    # The five mandatory conclusion items.
    for marker in ("综合相似度：", "判定：", "结构框架相似度：", "素材相似度：", "一句话建议"):
        assert marker in doc, marker
    # The style words in appendix B name a Word style, not literal text.
    for leaked in ("标题 1", "标题 2", "标题 3"):
        assert leaked not in doc, leaked


def test_report_lands_in_the_dedicated_folder(tmp_path: Path) -> None:
    report = audit_multi_source([("视频A", SRC)], SRC[:1500], draft_name="书_合并版")
    path = write_report(tmp_path, report, book_title="书")
    assert path.parent.name == REPORT_DIR_NAME
    assert path.parent.is_dir()
    assert path.read_bytes()[:2] == b"PK"


def test_inapplicable_report_states_so_instead_of_inventing_scores() -> None:
    report = audit_multi_source([], "任意正文")
    data = build_report_docx(report, book_title="书")
    import io, zipfile
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        doc = z.read("word/document.xml").decode("utf-8")
    assert "不适用" in doc


def test_unit_table_reports_none_count() -> None:
    report = audit_multi_source([("视频A", SRC)], SRC[:1200])
    payload = report.to_dict()
    src = payload["sources"][0]
    assert "unit_none_count" in src
    assert src["unit_none_count"] == src["structure"]["unit_none"]
