"""Word exports for one category only; literal company heading protocol."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import unquote, urlsplit
from xml.sax.saxutils import escape, quoteattr
import xml.etree.ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

TZ = timezone(timedelta(hours=8))
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"w": W}
WORD_TOKEN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0003134f]|[A-Za-z]+(?:['’-][A-Za-z]+)*|\d+(?:[.,]\d+)*")


def word_count(text: str) -> int:
    """Han characters + Latin words + numeric groups; no headings/punctuation."""
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    return len(WORD_TOKEN.findall(body))


def visible_count(text: str) -> int:
    return len(re.sub(r"\s", "", text))


def safe_name(text: str) -> str:
    text = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", unquote(str(text)))
    return text.strip(". ")[:50] or "通用长篇"


def timestamp(now: datetime | None = None) -> str:
    now = (now or datetime.now(TZ)).astimezone(TZ)
    return f"{now.year}年{now.month}月{now.day}日_{now:%H时%M分%S秒}"


def _run(text: str) -> str:
    return '<w:r><w:t xml:space="preserve">' + escape(text) + "</w:t></w:r>"


def _paragraph(text: str, style: str, links: list[tuple[str, str]]) -> str:
    # XML 1.0 has no representation for these control characters.
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text):
        raise ValueError("正文含有 Word XML 不支持的控制字符")
    runs, pos = [], 0
    for match in re.finditer(r"https?://[^\s<>]+", text):
        url = match.group().rstrip("。，；;)")
        if urlsplit(url).scheme not in {"http", "https"}:
            continue
        runs.append(_run(text[pos:match.start()]))
        rid = f"link{len(links) + 1}"
        links.append((rid, url))
        runs.append(f'<w:hyperlink r:id="{rid}">{_run(url)}</w:hyperlink>')
        pos = match.start() + len(url)
    runs.append(_run(text[pos:]))
    return f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>{"".join(runs)}</w:p>'


def create_docx(paragraphs: list[tuple[str, str]], metadata: dict[str, Any] | None = None) -> bytes:
    """Minimal standards-compliant DOCX; styles, hyperlinks and page numbers."""
    links: list[tuple[str, str]] = []
    body = []
    text_lines = []
    for text, style in paragraphs:
        if style == "PageBreak":
            body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
            continue
        if style not in {"Normal", "Heading1", "Heading2", "Title"}:
            raise ValueError(f"Unsupported style: {style}")
        text_lines.append(text)
        body.append(_paragraph(text, style, links))
    meta = dict(metadata or {})
    meta["document_text_sha256"] = sha256("\n".join(text_lines).encode()).hexdigest()
    meta["format_version"] = 1
    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{W}" xmlns:r="{R}"><w:body>'
        + "".join(body)
        + '<w:sectPr><w:footerReference w:type="default" r:id="footer"/>'
        '<w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0"/>'
        '</w:sectPr></w:body></w:document>'
    )
    styles = f'''<?xml version="1.0" encoding="UTF-8"?><w:styles xmlns:w="{W}">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="宋体"/><w:sz w:val="24"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="360" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:qFormat/><w:rPr><w:b/><w:sz w:val="36"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:keepLines/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:keepLines/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="28"/></w:rPr></w:style></w:styles>'''
    rels = f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="styles" Type="{R}/styles" Target="styles.xml"/><Relationship Id="footer" Type="{R}/footer" Target="footer1.xml"/>'
    for rid, url in links:
        rels += f'<Relationship Id="{rid}" Type="{R}/hyperlink" Target={quoteattr(url)} TargetMode="External"/>'
    rels += "</Relationships>"
    types = '''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="json" ContentType="application/json"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/></Types>'''
    out = BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", types)
        archive.writestr("_rels/.rels", f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="document" Type="{R}/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)
        archive.writestr("word/_rels/document.xml.rels", rels)
        archive.writestr("word/footer1.xml", f'<w:ftr xmlns:w="{W}"><w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:fldSimple w:instr="PAGE"/></w:p></w:ftr>')
        archive.writestr("customXml/general_longform.json", json.dumps(meta, ensure_ascii=False))
    return out.getvalue()


def inspect_docx(content: bytes) -> tuple[list[tuple[str, str]], dict[str, Any] | None]:
    with ZipFile(BytesIO(content)) as archive:
        if sum(info.file_size for info in archive.infolist()) > 30_000_000:
            raise ValueError("Word 解压内容超过 30 MB")
        root = ET.fromstring(archive.read("word/document.xml"))
        paragraphs = []
        for p in root.findall(".//w:body//w:p", NS):
            text = "".join((n.text or "") if n.tag == f"{{{W}}}t" else "\n" if n.tag == f"{{{W}}}br" and n.get(f"{{{W}}}type") != "page" else "\t" if n.tag == f"{{{W}}}tab" else "" for n in p.iter())
            if not text:
                continue
            style = p.find("w:pPr/w:pStyle", NS)
            paragraphs.append((text, style.get(f"{{{W}}}val", "Normal") if style is not None else "Normal"))
        metadata = None
        if "customXml/general_longform.json" in archive.namelist():
            candidate = json.loads(archive.read("customXml/general_longform.json"))
            actual = sha256("\n".join(text for text, _ in paragraphs).encode()).hexdigest()
            if isinstance(candidate, dict) and candidate.get("document_text_sha256") == actual:
                metadata = candidate
    return paragraphs, metadata


def manuscript_paragraphs(content: str) -> list[tuple[str, str]]:
    paragraphs = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("###"):
            raise ValueError("通用长篇不接受第三层标题")
        style = "Heading1" if line.startswith("##") else "Heading2" if line.startswith("#") else "Normal"
        paragraphs.append((line, style))
    return paragraphs


def bibliography(sources: list[dict[str, Any]]) -> list[tuple[str, str]]:
    paragraphs = [("文章参考来源", "Heading1")]
    seen = set()
    for source in sources:
        entry = str(source.get("bibliography") or "").strip()
        entry = re.sub(r"^\s*(?:\[\d+\]|［\d+］|【\d+】|\d+[.、)]|[一二三四五六七八九十]+[、.）)]|[-*•])\s*", "", entry)
        entry = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r"\1 \2", entry)
        # Work notes stay in the editorial record; never strip formal volume/issue parentheses.
        entry = re.sub(r"[（(][^()（）]{0,32}已读[^()（）]{0,16}[）)]", "", entry).strip()
        entry = re.sub(r"(?:[；;，,。\s]+)(?:全文|网页|摘要|主要内容|关键页)[^。；;]{0,16}已读[。；;\s]*$", "", entry).strip()
        url = str(source.get("url") or "")
        key = str(source.get("doi") or url or entry).lower().rstrip("/")
        if not entry or key in seen:
            continue
        if url and url not in entry:
            entry += " " + url
        seen.add(key)
        paragraphs.append((entry, "Normal"))
    return paragraphs


def write_artifact(directory: Path, filename: str, paragraphs: list[tuple[str, str]], metadata: dict[str, Any]) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    # A replay can finish the other file of a pair after a crash, but never
    # overwrite a previously delivered version with different text/metadata.
    if path.exists():
        data = path.read_bytes()
        old_paragraphs, old_meta = inspect_docx(data)
        if old_paragraphs != [(t, s) for t, s in paragraphs if s != "PageBreak" and t] or any((old_meta or {}).get(key) != value for key, value in metadata.items()):
            raise FileExistsError(f"导出文件已存在且内容不同：{filename}")
    else:
        data = create_docx(paragraphs, metadata)
        temp = path.with_suffix(".docx.tmp")
        with temp.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    actual, embedded = inspect_docx(data)
    if actual != [(text, style) for text, style in paragraphs if style != "PageBreak" and text]:
        raise ValueError("Word 写入后段落核验失败")
    if embedded is None:
        raise ValueError("Word 元数据校验失败")
    return {"filename": filename, "sha256": sha256(data).hexdigest(), "bytes": len(data),
            "kind": metadata["kind"], "version": metadata.get("version"), "number": metadata.get("number"),
            "accepted": metadata.get("accepted"), "created_at": metadata.get("exported_at") or datetime.now(TZ).isoformat()}


def _bundle(directory: Path, key: str, factory: Any) -> list[dict[str, Any]]:
    """Persist the exact pair before publishing either file; resume idempotently."""
    from app.services.general_longform_runtime import atomic_json, read_json
    journal_path = directory / ".batches" / f"{sha256(key.encode()).hexdigest()}.json"
    journal = read_json(journal_path)
    if journal is None:
        now = datetime.now(TZ)
        files = factory(timestamp(now))
        for item in files:
            item["metadata"]["exported_at"] = now.isoformat()
        journal = {"key": key, "files": files}
        atomic_json(journal_path, journal)
    records = [write_artifact(directory, item["filename"], [(t, s) for t, s in item["paragraphs"]], item["metadata"]) for item in journal["files"]]
    return records


def export_outline(directory: Path, state: dict[str, Any], *, confirmed: bool) -> dict[str, Any]:
    outline = state["outline"]
    version = state["outline_version"]
    packaging = outline["packaging"]
    chosen = packaging[outline["recommended_index"]]
    status = "确认版大纲" if confirmed else "待确认大纲"
    paragraphs = [(f"{status} v{version:02d}", "Title"), (f"项目 {state['project_id']} · {datetime.now(TZ).isoformat()}", "Normal")]
    paragraphs.append((f"固定提示词版本：{state.get('prompt_version', '')}；原文 SHA-256：{state.get('prompt_source_sha256', '')}", "Normal"))
    for key, label in [("domain", "主领域"), ("topic", "选题"), ("audience", "读者"), ("central_question", "全书主问题"), ("voice", "叙述方式")]:
        paragraphs.append((f"{label}：{outline.get(key, '')}", "Normal"))
    paragraphs.append(("不纳入本书：" + "；".join(outline.get("scope_exclusions") or []), "Normal"))
    if not state.get("source_text"):
        paragraphs.append(("本次为自主选题，未提供视频参考", "Normal"))
    for index, package in enumerate([chosen] if confirmed else packaging):
        paragraphs.extend([(f"@方案{'一二三四五'[index]}" if not confirmed else "已确认包装", "Normal"),
                           ("包装方向：" + package["direction"], "Normal"), ("书名：" + package["title"], "Normal"),
                           ("简介：" + package["description"], "Normal")])
    if not confirmed:
        paragraphs.append((f"推荐方案：{outline['recommended_index'] + 1}；{outline.get('recommendation', '')}", "Normal"))
        paragraphs.append(("包装实测字数：" + "；".join(f"方案{i+1} 书名{visible_count(p['title'])} 简介{visible_count(p['description'])}" for i, p in enumerate(packaging)), "Normal"))
    paragraphs.append((f"正文计划合计 {outline['target_words']} 字；{len(outline['chapters'])} 章", "Normal"))
    paragraphs.append(("全书章节与字数总表", "Normal"))
    paragraphs.extend((f"第{c['number']}章 {c['title']}：{c['target_words']} 字", "Normal") for c in outline["chapters"])
    for chapter in outline["chapters"]:
        paragraphs.append((f"##第{chapter['number']}章 {chapter['title']}", "Heading1"))
        for key, label in [("question", "核心问题"), ("new_information", "新增信息"), ("previous_link", "前章衔接"), ("next_link", "后章衔接"), ("target_words", "计划字数")]:
            paragraphs.append((f"{label}：{chapter.get(key, '')}", "Normal"))
        paragraphs.append(("不重复讲：" + "；".join(chapter.get("do_not_repeat") or []), "Normal"))
        for section in chapter["sections"]:
            paragraphs.append(("#" + section["title"], "Heading2"))
            for key, label in [("task", "任务"), ("argument", "观点与推导"), ("material", "材料及读取范围"), ("target_words", "计划字数")]:
                paragraphs.append((f"{label}：{section.get(key, '')}", "Normal"))
    paragraphs.extend(bibliography(list(state.get("sources", {}).values())))
    paragraphs.extend([("研究缺口：" + "；".join(outline.get("research_gaps") or []), "Normal"),
                       ("跨对话执行资料（不属于书稿正文）", "Normal"),
                       (json.dumps(outline.get("handoff", {}), ensure_ascii=False), "Normal")])
    return _bundle(directory, f"outline:{version}:{confirmed}", lambda stamp: [{
        "filename": f"{safe_name(chosen['title'])}_{status}_v{version:02d}_{stamp}.docx", "paragraphs": paragraphs,
        "metadata": {"kind": "outline", "project_id": state["project_id"], "version": version, "confirmed": confirmed,
                     "prompt_version": state.get("prompt_version"), "prompt_source_sha256": state.get("prompt_source_sha256"),
                     "confirmed_at": state.get("confirmed_at"), "outline": outline, "sources": state.get("sources", {})},
    }])[0]


def export_chapter(directory: Path, state: dict[str, Any], chapter: dict[str, Any], content: str, *, accepted: bool) -> list[dict[str, Any]]:
    key = f"chapter:{chapter['number']}:{chapter['version']}:{accepted}:{'' if accepted else chapter.get('request_id', '')}"
    return _bundle(directory, key, lambda stamp: _chapter_files(state, chapter, content, accepted=accepted, stamp=stamp))


def _chapter_files(state: dict[str, Any], chapter: dict[str, Any], content: str, *, accepted: bool, stamp: str) -> list[dict[str, Any]]:
    outline = state["outline"]
    title = outline["packaging"][outline["recommended_index"]]["title"]
    version = chapter["version"]
    prefix = f"{safe_name(title)}_第{chapter['number']:02d}章_{safe_name(chapter['title'])}"
    suffix = "" if accepted else "_未完成"
    body_name = f"{prefix}_正文{suffix}_v{version:02d}_{stamp}.docx"
    record_name = f"{prefix}_参考来源与编辑记录{suffix}_v{version:02d}_{stamp}.docx"
    audit = chapter.get("audit") or {}
    sources = [state["sources"][key] for key in audit.get("used_source_ids", []) if key in state.get("sources", {})]
    if not sources and not accepted:
        sources = list(chapter.get("research", {}).get("sources", {}).values())
    meta = {"kind": "chapter", "project_id": state["project_id"], "number": chapter["number"], "title": chapter["title"],
            "prompt_version": state.get("prompt_version"), "prompt_source_sha256": state.get("prompt_source_sha256"),
            "version": version, "accepted": accepted, "outline_version": state["outline_version"], "audit": audit, "sources": sources}
    # Invalid third-level headings in an unfinished candidate remain visible as
    # ordinary paragraphs in its partial delivery, not fabricated valid headings.
    export_content = content if accepted else re.sub(r"(?m)^\s*#{3,}\s*", "", content)
    body = {"filename": body_name, "paragraphs": manuscript_paragraphs(export_content), "metadata": meta}
    paragraphs = bibliography(sources) + [("", "PageBreak"), ("编辑记录（不属于书稿正文）", "Normal")]
    records = {
        "项目": state["project_id"], "章号": chapter["number"], "章名": chapter["title"], "版本": version,
        "固定提示词版本": state.get("prompt_version", ""), "指令原文SHA256": state.get("prompt_source_sha256", ""),
        "正文文件名": body_name, "配套文件名": record_name, "同批输出时间": stamp,
        "完成状态": "已验收" if accepted else "未完成，待续写或修订", "字数口径": "汉字字符＋英文单词＋数字组；不计标题、标点和空格",
        "实际正文数": word_count(content), "累计有效正文数": state.get("total_words", 0),
        "本章新增结论": audit.get("new_information", []), "已使用案例": audit.get("cases", []),
        "术语": audit.get("terms", {}), "前后衔接": audit.get("endingHook", ""),
        "待核实事项": chapter.get("issues", []), "下一章任务": next((c["question"] for c in outline["chapters"] if c["number"] == chapter["number"] + 1), "全书终审"),
        "累计章节与案例索引": state.get("accepted_state", {}),
        "读取范围与来源记录": sources, "断点": chapter.get("checkpoint", {}),
        "编辑阶段记录": chapter.get("stage_rejections", []),
        "正反方法与语境复核": audit.get("style_review", "此提示词版本没有单独的正反风格复核字段"),
        "原稿格式提醒": "部分稿中的第三层标题仅以普通段落呈现；原始候选文字完整保存在项目中" if export_content != content else "",
    }
    paragraphs.extend((f"{key}：{json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value}", "Normal") for key, value in records.items())
    companion = {"filename": record_name, "paragraphs": paragraphs, "metadata": {**meta, "kind": "editorial", "records": records}}
    return [body, companion]


def export_book(directory: Path, state: dict[str, Any], chapters: list[tuple[dict[str, Any], str]], *, operation_id: str = "") -> dict[str, Any]:
    outline = state["outline"]
    package = outline["packaging"][outline["recommended_index"]]
    paragraphs = [(package["title"], "Title"), (package["description"], "Normal"), ("目录", "Normal")]
    for chapter in outline["chapters"]:
        paragraphs.append((f"第{chapter['number']}章 {chapter['title']}", "Normal"))
        paragraphs.extend((section["title"], "Normal") for section in chapter["sections"])
    used = []
    for chapter, content in chapters:
        paragraphs.extend(manuscript_paragraphs(content))
        used.extend(state["sources"][key] for key in chapter["audit"]["used_source_ids"])
    paragraphs.extend(bibliography(used))
    version = int(state.get("book_version", 0)) + 1
    return _bundle(directory, f"book:{operation_id or version}", lambda stamp: [{
        "filename": f"{safe_name(package['title'])}_全书_v{version:02d}_{stamp}.docx", "paragraphs": paragraphs,
        "metadata": {"kind": "book", "project_id": state["project_id"], "version": version, "word_count": state["total_words"],
                     "prompt_version": state.get("prompt_version"), "prompt_source_sha256": state.get("prompt_source_sha256"),
                     "final_audit": state.get("final_audit", {})},
    }])[0]
