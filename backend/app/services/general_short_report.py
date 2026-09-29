"""Word report writer for the general_short similarity audit.

Produces the v09 appendix-six report: conclusion first, scoring detail, the
per-unit structure table, the four material tables, the differentiation list,
the three-layer risk statement and the reproducibility appendix. Formatting uses
real Word heading styles (标题1/2/3) in 黑体 with 宋体 body text, never bold-only
fake headings, so the file can be navigated and re-checked.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
import re
from xml.sax.saxutils import escape, quoteattr
from zipfile import ZIP_DEFLATED, ZipFile

from app.services.general_short_similarity import (
    GRAM_SIZES,
    HIGH_RISK_THRESHOLD,
    JUST_OVER_MAX,
    MIN_DRAFT_SENTENCE_CHARS,
    MIN_SOURCE_SENTENCE_CHARS,
    NEAR_BOUNDARY_MIN,
    MultiSourceReport,
    SourceAudit,
)

TZ = timezone(timedelta(hours=8))
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

REPORT_DIR_NAME = "04_文本相似度审查报告"
REPORT_EXT = ".docx"

#: Style ids emitted into the document part.  Heading3 exists because appendix
#: six requires three real heading levels for the report.
STYLE_IDS = {"Title", "Heading1", "Heading2", "Heading3", "Normal", "TableHeader", "TableCell"}


def _esc(text: str) -> str:
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text or ""):
        raise ValueError("文本含有 Word XML 不支持的控制字符")
    return escape(str(text))


def _run(text: str, *, bold: bool = False) -> str:
    rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{_esc(text)}</w:t></w:r>'


def _para(text: str, style: str = "Normal", *, bold: bool = False) -> str:
    if style not in STYLE_IDS:
        raise ValueError(f"Unsupported style: {style}")
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>'
    return f"<w:p>{ppr}{_run(text, bold=bold)}</w:p>"


def _table(rows: list[list[str]], *, header: bool = True) -> str:
    """A minimal bordered table; the first row is a real header row."""
    out = ['<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:type="auto" w:w="0"/>'
           '<w:tblBorders>'
           '<w:top w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
           '<w:left w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
           '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
           '<w:right w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
           '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
           '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
           '</w:tblBorders></w:tblPr><w:tblGrid>'
           + "".join("<w:gridCol/>" for _ in (rows[0] if rows else [""]))
           + "</w:tblGrid>"]
    for idx, row in enumerate(rows):
        cells = "".join(
            f'<w:tc><w:tcPr><w:tcW w:type="auto" w:w="0"/></w:tcPr>'
            f'{_para(cell, "TableHeader" if (header and idx == 0) else "TableCell")}</w:tc>'
            for cell in row
        )
        out.append(f"<w:tr>{cells}</w:tr>")
    out.append("</w:tbl>")
    return "".join(out)


def _styles_xml() -> str:
    """黑体 headings, 宋体 body, per appendix six."""
    return f'''<?xml version="1.0" encoding="UTF-8"?><w:styles xmlns:w="{W}">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="宋体"/><w:sz w:val="21"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="100" w:line="320" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:qFormat/><w:rPr><w:rFonts w:eastAsia="黑体"/><w:b/><w:sz w:val="36"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:keepLines/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:rFonts w:eastAsia="黑体"/><w:b/><w:sz w:val="32"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:keepLines/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:rFonts w:eastAsia="黑体"/><w:b/><w:sz w:val="28"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:keepLines/><w:outlineLvl w:val="2"/></w:pPr><w:rPr><w:rFonts w:eastAsia="黑体"/><w:b/><w:sz w:val="24"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="TableHeader"><w:name w:val="Table Header"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="0" w:line="260" w:lineRule="auto"/></w:pPr><w:rPr><w:rFonts w:eastAsia="黑体"/><w:b/><w:sz w:val="19"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="TableCell"><w:name w:val="Table Cell"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="0" w:line="260" w:lineRule="auto"/></w:pPr><w:rPr><w:rFonts w:eastAsia="宋体"/><w:sz w:val="19"/></w:rPr></w:style>
</w:styles>'''


def _package(body_xml: str, metadata: dict) -> bytes:
    import json

    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body>{body_xml}'
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
        'w:header="720" w:footer="720" w:gutter="0"/></w:sectPr></w:body></w:document>'
    )
    rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="styles" Type="{R}/styles" Target="styles.xml"/></Relationships>'
    )
    types = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        "</Types>"
    )
    out = BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", types)
        archive.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="document" Type="{R}/officeDocument" Target="word/document.xml"/></Relationships>',
        )
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", _styles_xml())
        archive.writestr("word/_rels/document.xml.rels", rels)
        archive.writestr("docProps/audit.json", json.dumps(metadata, ensure_ascii=False, indent=2))
    return out.getvalue()


def _pct(value: float) -> str:
    return f"{value:.1f}%"


def _risk_level(score: float) -> str:
    if score >= 80:
        return "高"
    if score >= 60:
        return "中"
    return "低"


def _conclusion(worst: SourceAudit, report: MultiSourceReport) -> str:
    """Part one: conclusion first, exactly the five items appendix B fixes.

    Appendix B writes its template as 标题 2　综合相似度：XX.X%　判定：安全／高危,
    where 标题 2 names the Word style and the rest is the literal line.  The
    style words are therefore never emitted as text.
    """
    parts: list[str] = []
    boundary = f"　{report.overall_boundary}" if report.overall_boundary else ""
    parts.append(_para(
        f"综合相似度：{_pct(report.max_composite)}　判定：{report.overall_verdict}{boundary}",
        "Heading2", bold=True,
    ))
    parts.append(_para(f"结构框架相似度：{_pct(worst.structure.structure_score)}", "Heading2"))
    parts.append(_para(
        f"A 论点单元对应率 {_pct(worst.structure.unit_match_score)}、"
        f"B 案例推进次序一致度 {_pct(worst.structure.case_order_score)}、"
        f"C 叙事主线与因果链沿用度 {_pct(worst.structure.narrative_spine_score)}。"
    ))
    parts.append(_para(f"素材相似度：{_pct(worst.material_score)}", "Heading2"))
    parts.append(_para(
        f"数据沿用率 {_pct(worst.material_data)}、举例与案例组合沿用度 {_pct(worst.material_case)}、"
        f"具名主体沿用度 {_pct(worst.material_entity)}、字面重合度 {_pct(worst.material_literal)}；"
        f"最长连续相同片段 {worst.literal.longest_common_chars} 字。"
    ))
    parts.append(_para("一句话建议", "Heading2"))
    parts.append(_para(worst.fix_directive))
    if report.overall_fallback_notice:
        parts.append(_para(report.overall_fallback_notice, bold=True))
    return "".join(parts)


def _scoring_detail(report: MultiSourceReport) -> str:
    """Part two: S and M sub-scores with weights and the composite working."""
    parts: list[str] = []
    for idx, src in enumerate(report.sources, start=1):
        label = f"来源 {idx}：{src.source_name}" if len(report.sources) > 1 else src.source_name
        parts.append(_para(label, "Heading3"))
        rows = [
            ["分项", "代号", "权重", "得分", "加权结果"],
            ["论点单元对应率", "A", "40%", _pct(src.structure.unit_match_score),
             _pct(src.structure.unit_match_score * 0.40)],
            ["案例推进次序一致度", "B", "30%", _pct(src.structure.case_order_score),
             _pct(src.structure.case_order_score * 0.30)],
            ["叙事主线与因果链沿用度", "C", "30%", _pct(src.structure.narrative_spine_score),
             _pct(src.structure.narrative_spine_score * 0.30)],
            ["结构框架相似度 S", "S", "—", _pct(src.structure.structure_score),
             _pct(src.structure.structure_score)],
            ["数据沿用率", "A", "30%", _pct(src.material_data), "—"],
            ["举例与案例组合沿用度", "B", "25%", _pct(src.material_case), "—"],
            ["具名主体沿用度", "C", "20%", _pct(src.material_entity), "—"],
            ["字面重合度", "D", "25%", _pct(src.material_literal), "—"],
            ["素材相似度 M", "M", "—", _pct(src.material_score), _pct(src.material_score)],
            ["综合相似度", "S×0.60＋M×0.40", "—", _pct(src.composite), _pct(src.composite)],
        ]
        parts.append(_table(rows))
        parts.append(_para(
            f"S ＝ {_pct(src.structure.unit_match_score)} × 0.40 ＋ "
            f"{_pct(src.structure.case_order_score)} × 0.30 ＋ "
            f"{_pct(src.structure.narrative_spine_score)} × 0.30 ＝ {_pct(src.structure.structure_score)}"
        ))
        parts.append(_para(
            f"M ＝ {_pct(src.material_data)} × 0.30 ＋ {_pct(src.material_case)} × 0.25 ＋ "
            f"{_pct(src.material_entity)} × 0.20 ＋ {_pct(src.material_literal)} × 0.25 ＝ {_pct(src.material_score)}"
        ))
        parts.append(_para(
            f"综合相似度 ＝ {_pct(src.structure.structure_score)} × 0.60 ＋ "
            f"{_pct(src.material_score)} × 0.40 ＝ {_pct(src.composite)}"
        ))
    return "".join(parts)


def _structure_evidence(report: MultiSourceReport) -> str:
    """Part three: the unit-by-unit mapping table plus the counts."""
    parts: list[str] = []
    for idx, src in enumerate(report.sources, start=1):
        if len(report.sources) > 1:
            parts.append(_para(f"来源 {idx}：{src.source_name}", "Heading3"))
        rows = [["序号", "原稿论点单元", "新稿对应位置", "对应程度"]]
        for item in src.structure.unit_map:
            rows.append([
                item["id"],
                item.get("unit", ""),
                item.get("evidence", "") or "无",
                item["level"],
            ])
        if len(rows) == 1:
            rows.append(["—", "—", "—", "—"])
        parts.append(_table(rows))
        total = max(src.structure.unit_total, 1)
        parts.append(_para(
            f"完整 {src.structure.unit_full} 条（{_pct(src.structure.unit_full / total * 100)}）、"
            f"部分 {src.structure.unit_partial} 条（{_pct(src.structure.unit_partial / total * 100)}）、"
            f"无 {src.structure.unit_none} 条（{_pct(src.structure.unit_none / total * 100)}）。"
            f"“无对应”条数：{src.unit_none_count}。"
        ))
        if src.structure.unit_none == 0:
            parts.append(_para("原稿的每一个论点都被接走了。"))
    return "".join(parts)


def _material_evidence(report: MultiSourceReport) -> str:
    """Part four: the four material tables appendix B requires."""
    parts: list[str] = []
    for idx, src in enumerate(report.sources, start=1):
        if len(report.sources) > 1:
            parts.append(_para(f"来源 {idx}：{src.source_name}", "Heading3"))

        parts.append(_para("数据沿用对照表", "Heading3"))
        rows = [["原稿数据点", "是否被新稿沿用"]]
        rows += [[d["point"], d["reused"] or "待人工确认"] for d in src.data_points] or [["（无）", "—"]]
        parts.append(_table(rows))

        parts.append(_para("举例与案例组合对照表", "Heading3"))
        rows = [["原稿案例", "是否被新稿沿用"]]
        rows += [[c["case"], c.get("reused", "") or "待人工确认"] for c in src.case_pairs] or [["（无）", "—"]]
        parts.append(_table(rows))

        parts.append(_para("具名主体出现次数对照表", "Heading3"))
        rows = [["具名主体", "原稿次数", "新稿次数"]]
        rows += [[e["entity"], e["source_count"], e["draft_count"]] for e in src.entity_counts] or [["（无）", "—", "—"]]
        parts.append(_table(rows))

        parts.append(_para("字面重合检测数据表", "Heading3"))
        rows = [["n-gram", "原稿片段数", "新稿片段数", "共有片段数", "重合率"]]
        for row in src.literal.ngrams:
            rows.append([f"{row.size}-gram", str(row.source_count), str(row.draft_count),
                         str(row.shared_count), f"{row.overlap:.4f}"])
        parts.append(_table(rows))
        parts.append(_para(
            f"最长连续相同片段：{src.literal.longest_common_chars} 字"
            + (f"（示例：{src.literal.longest_common_sample[:40]}…）"
               if src.literal.longest_common_sample else "")
        ))
        parts.append(_para(
            f"句级相似度最高值：{src.literal.max_sentence_similarity:.2f}；"
            f"句级相似度 ≥0.5 的句子占比：{src.literal.high_similarity_sentence_ratio:.2%}"
        ))
        rows = [["句级相似度区间", "句数"]] + [[k, str(v)] for k, v in src.literal.sentence_buckets.items()]
        parts.append(_table(rows))

    parts.append(_para("差异化内容清单", "Heading3"))
    listed = [item for src in report.sources for item in src.differentiation_list]
    if listed:
        for item in dict.fromkeys(listed):
            parts.append(_para("• " + item))
    else:
        parts.append(_para("（新稿未显示可单列的差异化条目，需人工复核）"))
    return "".join(parts)


def _risk_and_advice(report: MultiSourceReport) -> str:
    """Part five: three risk layers plus actionable, skeleton-first advice."""
    worst = max(report.sources, key=lambda s: s.composite)
    parts = [_table([
        ["风险层", "等级", "理由"],
        ["著作权风险", _risk_level(report.max_composite),
         f"综合相似度 {_pct(report.max_composite)}；最长连续相同片段 {worst.literal.longest_common_chars} 字。"],
        ["反不正当竞争风险", _risk_level(worst.structure.structure_score),
         f"结构框架相似度 {_pct(worst.structure.structure_score)}；"
         f"论点单元“无对应”{worst.unit_none_count} 条。"],
        ["平台原创审核风险", _risk_level(report.max_composite),
         "以平台原创度与洗稿识别规则为准；本报告只给风险等级，不产出法律结论。"],
    ])]
    parts.append(_para("改法建议（优先改骨架）：" + worst.fix_directive))
    return "".join(parts)


def _reproducibility(report: MultiSourceReport) -> str:
    """Part six: enough detail for a third party to recompute every score."""
    parts = [_para(
        f"预处理：删除时间轴标记、结尾字数统计行与纯排版空行；所有统计均在去除全部空白字符后的文本上进行。"
        f"n-gram 取值：{'/'.join(str(s) for s in GRAM_SIZES)}。"
        f"字面算法：difflib.SequenceMatcher(autojunk=False) 取最长连续相同片段与句级相似度；"
        f"句级比较时新稿保留 ≥{MIN_DRAFT_SENTENCE_CHARS} 字的句子，原稿保留 ≥{MIN_SOURCE_SENTENCE_CHARS} 字的句子。"
        f"论点单元划分：按每 800—1500 字合并为一条，总量 20—80 条。"
        f"结构分项：A ＝（完整×1.0 ＋ 部分×0.5）÷ 单元总数 × 100；"
        f"B 取 100／85／70／40／25／10；C 取 100／75／40／10。"
        f"字面重合度 D 按折算表取值：无重合且最长片段 <12 字且句级高相似占比 <2% 取 5；"
        f"仅事实性排列重合取 20；最长片段 12—29 字或占比 2%—10% 取 50；"
        f"最长片段 30—99 字或占比 10%—25% 取 75；存在整段搬用（≥100 字）或占比 >25% 取 100。"
        f"M ＝ A×0.30 ＋ B×0.25 ＋ C×0.20 ＋ D×0.25。"
        f"综合相似度 ＝ S×0.60 ＋ M×0.40，保留一位小数。"
        f"判定：<50.0% 安全；≥50.0% 高危；{NEAR_BOUNDARY_MIN:.1f}%—49.9% 标【临界限】；"
        f"50.1%—{JUST_OVER_MAX:.1f}% 标【刚过线】；S≥70% 必须给出结构兜底提示。"
    )]
    parts.append(_para(f"被审查新稿：{report.draft_name or '（未命名）'}"))
    parts.append(_para("被审查原稿：" + "；".join(s.source_name for s in report.sources)))
    parts.append(_para("生成时间：" + datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S") + "（UTC+8）"))
    return "".join(parts)


def build_report_docx(report: MultiSourceReport, *, book_title: str = "", draft_name: str = "") -> bytes:
    """Render the report on the appendix-B skeleton, conclusion first."""
    title = (book_title or "通用短篇").strip()
    body: list[str] = [_para(f"{title}_相似度审查报告", "Title")]

    body.append(_para("第一部分　结论", "Heading1"))
    if not report.applicable:
        body.append(_para(report.reason or "本步骤不适用。"))
        body.append(_para("本步骤标记为“不适用”，未生成百分比与风险判定。"))
        return _package("".join(body), {"applicable": False, "reason": report.reason})

    worst = max(report.sources, key=lambda s: s.composite)
    body.append(_conclusion(worst, report))

    if len(report.sources) > 1:
        body.append(_para("多来源总览（门槛取最高综合相似度）", "Heading2"))
        body.append(_table(
            [["来源", "结构框架 S", "素材 M", "综合相似度", "判定"]]
            + [[s.source_name, _pct(s.structure.structure_score), _pct(s.material_score),
                _pct(s.composite),
                s.verdict + (f" {s.boundary}" if s.boundary else "")] for s in report.sources]
        ))

    body.append(_para("第二部分　打分明细", "Heading1"))
    body.append(_scoring_detail(report))

    body.append(_para("第三部分　结构框架相似度逐条依据", "Heading1"))
    body.append(_structure_evidence(report))

    body.append(_para("第四部分　素材相似度逐条依据", "Heading1"))
    body.append(_material_evidence(report))

    body.append(_para("第五部分　风险定性与建议", "Heading1"))
    body.append(_risk_and_advice(report))

    body.append(_para("第六部分　附录：检测口径与可复现说明", "Heading1"))
    body.append(_reproducibility(report))

    return _package("".join(body), {
        "applicable": True,
        "book_title": title,
        "draft_name": report.draft_name or draft_name,
        "max_composite": report.max_composite,
        "overall_verdict": report.overall_verdict,
        "sources": [s.source_name for s in report.sources],
    })


def report_filename(book_title: str, now: datetime | None = None) -> str:
    """v09: <书名>_文本相似度审查报告_<YYYY年M月D日_HH时MM分SS秒>.docx"""
    now = (now or datetime.now(TZ)).astimezone(TZ)
    safe = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", (book_title or "通用短篇").strip()) or "通用短篇"
    return f"{safe}_文本相似度审查报告_{now.year}年{now.month}月{now.day}日_{now:%H时%M分%S秒}{REPORT_EXT}"


def write_report(
    output_dir: Path,
    report: MultiSourceReport,
    *,
    book_title: str = "",
    draft_name: str = "",
) -> Path:
    """Write the report into 04_文本相似度审查报告/ inside the project directory."""
    target_dir = output_dir / REPORT_DIR_NAME
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / report_filename(book_title or draft_name)
    path.write_bytes(build_report_docx(report, book_title=book_title, draft_name=draft_name))
    return path
