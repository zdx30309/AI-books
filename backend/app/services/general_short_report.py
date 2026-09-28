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


def _source_section(a: SourceAudit, order: int) -> str:
    parts = [
        _para(f"标题 2　来源 {order}：{a.source_name}", "Heading2"),
        _para("标题 3　分项得分", "Heading3"),
    ]
    rows = [
        ["分项", "得分", "权重", "加权结果"],
        ["A 论点单元对应率", _pct(a.structure.unit_match_score), "0.40", _pct(a.structure.unit_match_score * 0.40)],
        ["B 案例推进次序一致度", _pct(a.structure.case_order_score), "0.30", _pct(a.structure.case_order_score * 0.30)],
        ["C 叙事主线与因果链沿用度", _pct(a.structure.narrative_spine_score), "0.30", _pct(a.structure.narrative_spine_score * 0.30)],
        ["结构框架相似度 S", _pct(a.structure.structure_score), "—", _pct(a.structure.structure_score)],
        ["数据沿用率", _pct(min(100.0, a.literal.literal_reuse_score * 0.9)), "0.30", "—"],
        ["举例与案例组合沿用度", _pct(a.structure.case_order_score), "0.25", "—"],
        ["具名主体沿用度", _pct(min(100.0, a.literal.high_similarity_sentence_ratio * 200)), "0.20", "—"],
        ["字面重合度", _pct(a.literal.literal_reuse_score), "0.25", "—"],
        ["素材相似度 M", _pct(a.material_score), "—", _pct(a.material_score)],
        ["综合相似度", _pct(a.composite), "S×0.60＋M×0.40", _pct(a.composite)],
    ]
    parts.append(_table(rows))

    parts.append(_para("标题 3　结构框架相似度逐条依据", "Heading3"))
    urows = [["序号", "原稿论点单元", "对应程度", "判定依据"]]
    for item in a.structure.unit_map:
        urows.append([item["id"], item.get("unit", ""), item["level"], item.get("evidence", "")])
    parts.append(_table(urows))
    total = max(a.structure.unit_total, 1)
    parts.append(_para(
        f"完整 {a.structure.unit_full} 条（{_pct(a.structure.unit_full / total * 100)}）、"
        f"部分 {a.structure.unit_partial} 条（{_pct(a.structure.unit_partial / total * 100)}）、"
        f"无对应 {a.structure.unit_none} 条（{_pct(a.structure.unit_none / total * 100)}）。"
        f"“无对应”条数：{a.unit_none_count}。"
    ))

    parts.append(_para("标题 3　素材相似度逐条依据", "Heading3"))
    parts.append(_para("数据沿用对照表", "Normal", bold=True))
    parts.append(_table([["原稿数据点", "是否被新稿沿用"]] + [[d["point"], d["reused"] or "待人工确认"] for d in a.data_points] or [["（无）", "—"]]))

    parts.append(_para("具名主体出现次数对照表", "Normal", bold=True))
    parts.append(_table([["具名主体", "原稿次数", "新稿次数"]] + [[e["entity"], e["source_count"], e["draft_count"]] for e in a.entity_counts] or [["（无）", "—", "—"]]))

    parts.append(_para("字面重合检测数据表", "Normal", bold=True))
    lrows = [["n-gram", "原稿片段数", "新稿片段数", "共有片段数", "重合率"]]
    for row in a.literal.ngrams:
        lrows.append([f"{row.size}-gram", str(row.source_count), str(row.draft_count), str(row.shared_count), f"{row.overlap:.4f}"])
    parts.append(_table(lrows))
    parts.append(_para(
        f"最长连续相同片段：{a.literal.longest_common_chars} 字"
        + (f"（示例：{a.literal.longest_common_sample[:40]}…）" if a.literal.longest_common_sample else "")
    ))
    parts.append(_para(f"句级相似度最高值：{a.literal.max_sentence_similarity:.2f}；"
                       f"句级相似度 ≥0.5 的句子占比：{a.literal.high_similarity_sentence_ratio:.2%}"))
    parts.append(_table([["句级相似度区间", "句数"]] + [[k, str(v)] for k, v in a.literal.sentence_buckets.items()]))

    parts.append(_para("标题 3　差异化内容清单", "Heading3"))
    if a.differentiation_list:
        for item in a.differentiation_list:
            parts.append(_para("• " + item))
    else:
        parts.append(_para("（新稿未显示可单列的差异化条目，需人工复核）"))
    return "".join(parts)


def build_report_docx(report: MultiSourceReport, *, book_title: str = "", draft_name: str = "") -> bytes:
    """Render the appendix-six report. Conclusion first, then detail."""
    body: list[str] = []
    title = (book_title or "通用短篇").strip()
    body.append(_para(f"{title}_相似度审查报告", "Title"))

    # ---- 第一部分 结论（必须最先，且在第一页内）----
    body.append(_para("标题 1　第一部分　结论", "Heading1"))
    if not report.applicable:
        body.append(_para(report.reason or "本步骤不适用。"))
        body.append(_para("本步骤标记为“不适用”，未生成百分比与风险判定。"))
        return _package("".join(body), {"applicable": False, "reason": report.reason})

    worst = max(report.sources, key=lambda s: s.composite)
    body.append(_para(f"综合相似度：{_pct(report.max_composite)}", "Heading2", bold=True))
    boundary = f"　{report.overall_boundary}" if report.overall_boundary else ""
    body.append(_para(f"判定结果：{report.overall_verdict}{boundary}（以 50.0% 为界；≥50.0% 判高危）", "Normal", bold=True))
    body.append(_para(f"结构框架相似度：{_pct(worst.structure.structure_score)}　主要依据："
                      f"A 论点单元对应率 {_pct(worst.structure.unit_match_score)}、"
                      f"B 案例次序 {_pct(worst.structure.case_order_score)}、"
                      f"C 叙事主线 {_pct(worst.structure.narrative_spine_score)}。"))
    body.append(_para(f"素材相似度：{_pct(worst.material_score)}　主要依据："
                      f"字面重合度 {_pct(worst.literal.literal_reuse_score)}、"
                      f"最长连续相同片段 {worst.literal.longest_common_chars} 字。"))
    body.append(_para(f"一句话建议：{worst.fix_directive}"))
    if report.overall_fallback_notice:
        body.append(_para(report.overall_fallback_notice, "Normal", bold=True))

    # ---- 多来源总览 ----
    if len(report.sources) > 1:
        body.append(_para("标题 2　多来源结果总览（取最高综合相似度为本轮门槛）", "Heading2"))
        body.append(_table(
            [["来源", "结构框架 S", "素材 M", "综合相似度", "判定"]]
            + [[s.source_name, _pct(s.structure.structure_score), _pct(s.material_score), _pct(s.composite),
                s.verdict + (f" {s.boundary}" if s.boundary else "")] for s in report.sources]
        ))

    # ---- 第二部分 打分明细 ----
    body.append(_para("标题 1　第二部分　打分明细", "Heading1"))
    body.append(_para(f"被审查新稿：{report.draft_name or draft_name or '（未命名）'}"))
    for idx, src in enumerate(report.sources, start=1):
        body.append(_para(f"标题 2　来源 {idx}：{src.source_name}", "Heading2"))
        body.append(_para(f"原稿去空白字数：{src.source_chars}"))
        body.append(_para(f"结构框架相似度 S={_pct(src.structure.structure_score)}；"
                          f"素材相似度 M={_pct(src.material_score)}；"
                          f"综合＝S×0.60＋M×0.40＝{_pct(src.composite)}"))

    # ---- 第三 / 四部分 逐条依据 ----
    body.append(_para("标题 1　第三部分　结构框架相似度逐条依据", "Heading1"))
    body.append(_para("标题 1　第四部分　素材相似度逐条依据", "Heading1"))
    for idx, src in enumerate(report.sources, start=1):
        body.append(_source_section(src, idx))

    # ---- 第五部分 风险定性 ----
    body.append(_para("标题 1　第五部分　风险定性与建议", "Heading1"))
    body.append(_table([
        ["风险层", "等级", "理由"],
        ["著作权风险", _risk_level(report.max_composite),
         f"综合相似度 {_pct(report.max_composite)}；字面最长连续相同 {worst.literal.longest_common_chars} 字。"],
        ["反不正当竞争风险", _risk_level(worst.structure.structure_score),
         f"结构框架相似度 {_pct(worst.structure.structure_score)}；论点单元“无对应”{worst.unit_none_count} 条。"],
        ["平台原创审核风险", _risk_level(report.max_composite),
         "以平台原创度与洗稿识别规则为准，本报告只给风险等级，不产出法律结论。"],
    ]))
    body.append(_para("改法建议（优先改骨架）：" + worst.fix_directive))

    # ---- 第六部分 可复现说明 ----
    body.append(_para("标题 1　第六部分　附录　检测口径与可复现说明", "Heading1"))
    body.append(_para(
        f"预处理：删除时间轴标记、结尾字数统计行与纯排版空行；所有统计在去除全部空白字符后的文本上进行。"
        f"n-gram 取值：{'/'.join(str(s) for s in GRAM_SIZES)}。"
        f"字面算法：SequenceMatcher(autojunk=False) 取最长连续相同片段与句级相似度。"
        f"结构分项：A=（完整×1.0＋部分×0.5）÷单元总数×100；B 取 100/85/70/40/25/10；C 取 100/75/40/10。"
        f"M 权重：数据 0.30／案例 0.25／具名主体 0.20／字面 0.25。"
        f"综合＝S×0.60＋M×0.40，保留一位小数。"
        f"判定：<50.0% 安全；≥50.0% 高危；{NEAR_BOUNDARY_MIN:.1f}%—49.9% 标【临界限】；"
        f"50.1%—{JUST_OVER_MAX:.1f}% 标【刚过线】；S≥70% 必须给出结构兜底提示。"
    ))
    body.append(_para("生成时间：" + datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S") + "（UTC+8）"))

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
