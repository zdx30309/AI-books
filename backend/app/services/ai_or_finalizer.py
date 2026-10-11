from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import re
import time
import zipfile
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from zoneinfo import ZoneInfo
from xml.etree import ElementTree as ET

import httpx
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side

from app.core.config import settings
from app.services.aigc_quality_gate import AigcManualReviewRequired
from app.services.deepseek import get_deepseek_client
from app.services.general_longform_runtime import atomic_json, atomic_text
from app.utils import paid_api_guard
from app.utils.distributed_limiter import distributed_semaphore

logger = logging.getLogger(__name__)
PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
VERSION = "ai-or-final-v1"
BASE = "https://api.ai-or.com/c/api/open/workflow"
WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
ET.register_namespace("w", WORD)


class FinalizerHold(AigcManualReviewRequired):
    pass


def enabled_for(created_at: datetime | None) -> bool:
    if not settings.AI_OR_FINALIZER_ENABLED:
        return False
    start = settings.AI_OR_FINALIZER_START_AT
    if not start:
        return True
    if created_at is None:
        return False
    boundary = datetime.fromisoformat(start.replace("Z", "+00:00"))
    return created_at.replace(tzinfo=created_at.tzinfo or timezone.utc) >= boundary


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_chunks(text: str, limit: int) -> list[str]:
    if not 1000 <= limit <= 50000:
        raise FinalizerHold("降AI分段上限必须为1000至50000字符")
    parts: list[str] = []
    buffer = ""
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith(("#", "![")):
            if buffer:
                parts.append(buffer)
                buffer = ""
            parts.append(line)
            continue
        if len(line) > limit:
            raise FinalizerHold("存在超长单段，需人工分段；尚未调用降AI服务")
        if buffer and len(buffer) + len(line) > limit:
            parts.append(buffer)
            buffer = ""
        buffer += line
    if buffer:
        parts.append(buffer)
    return parts


def structure(text: str) -> list[str]:
    return [line.strip() if line.strip().startswith(("#", "![")) else line.strip()[0]
            for line in text.splitlines() if line.strip().startswith(("#", "@", "！", "!["))]


def validate_rewrite(original: str, rewritten: str) -> None:
    if not rewritten.strip() or len(rewritten) < len(original) * 0.7:
        raise FinalizerHold("降AI结果为空或疑似截断，已保存结果，等待人工确认")
    if structure(original) != structure(rewritten):
        raise FinalizerHold("降AI改变了标题、图像或加工标记段落，已保存结果，等待人工确认")


def parse_issues(raw: str, text: str) -> tuple[str, list[dict]]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    value = json.loads(raw)
    issues = value.get("issues")
    if not isinstance(issues, list):
        raise FinalizerHold("纠错模型未返回逐项纠错记录")
    spans = []
    for issue in issues:
        if not isinstance(issue, dict):
            raise FinalizerHold("纠错记录格式错误")
        before, after = issue.get("original"), issue.get("replacement")
        if (not isinstance(before, str) or not before or not isinstance(after, str)
                or not isinstance(issue.get("remark"), str)
                or not isinstance(issue.get("severe"), bool)
                or max(len(before), len(after), len(issue["remark"])) > 2000
                or text.count(before) != 1 or before == after):
            raise FinalizerHold("纠错记录无法唯一定位或超出可读表格长度，已保存模型输出")
        position = text.index(before)
        spans.append((position, position + len(before), after, issue))
    spans.sort(key=lambda item: item[0])
    if any(left[1] > right[0] for left, right in zip(spans, spans[1:])):
        raise FinalizerHold("纠错记录重叠，不能安全自动应用")
    final = text
    for start, end, replacement, _issue in reversed(spans):
        final = final[:start] + replacement + final[end:]
    if structure(final) != structure(text):
        raise FinalizerHold("纠错建议改变加工符号或标题，需要人工确认")
    return final, issues


def call_rewrite(text: str) -> dict:
    if not settings.AI_OR_ACCESS_KEY or not settings.AI_OR_ACCESS_SECRET:
        raise FinalizerHold("未配置降AI服务密钥，原稿已保留")
    try:
        with httpx.Client(timeout=httpx.Timeout(settings.AI_OR_TIMEOUT_SECONDS, connect=30)) as client:
            response = client.post(BASE + "/zhuque-direct", headers={
                "X-Aigc-Platform": "api", "X-Aigc-Site": "www",
            }, json={"accessKey": settings.AI_OR_ACCESS_KEY,
                     "accessSecret": settings.AI_OR_ACCESS_SECRET,
                     "inputText": text, "repair_paragraphs": True})
            response.raise_for_status()
            payload = response.json()
        result = payload.get("data", payload)
        if not isinstance(result, dict) or result.get("status") != 1:
            raise FinalizerHold("降AI接口未返回成功状态；请查看平台使用记录，禁止自动重试")
        if not isinstance(result.get("outputText"), str):
            raise FinalizerHold("降AI接口没有返回正文")
        return {key: result.get(key) for key in (
            "taskId", "status", "outputText", "consumeAmount", "consumeCharCount", "executeTime")}
    except FinalizerHold:
        raise
    except Exception as exc:
        raise FinalizerHold("降AI请求结果不确定，已暂停避免重复扣费；请核对平台记录") from exc


def call_correction(original: str, rewritten: str) -> dict:
    rules = (PROMPTS / "ai_or_dual_manuscript_correction.md").read_text(encoding="utf-8")
    transport = (PROMPTS / "ai_or_correction_transport.md").read_text(encoding="utf-8")
    prompt = rules + "\n\n" + transport + "\n\n" + json.dumps(
        {"原稿": original, "降AI后": rewritten}, ensure_ascii=False)
    model = settings.AI_OR_CORRECTION_MODEL or settings.DEEPSEEK_MODEL
    with distributed_semaphore("deepseek-format", limit=settings.DEEPSEEK_FORMAT_MAX_CONCURRENT_CALLS):
        paid_api_guard.guard("deepseek")
        result = get_deepseek_client().with_options(max_retries=0).chat.completions.create(
            model=model, messages=[{"role": "user", "content": prompt}],
            temperature=0.1, response_format={"type": "json_object"},
        )
    choice = result.choices[0]
    if choice.finish_reason != "stop":
        raise FinalizerHold("双稿纠错输出不完整；已暂停，不自动重放付费调用")
    return {"text": choice.message.content or "", "model": model,
            "usage": result.usage.model_dump() if result.usage else None, "cost": None}


def word_document(before: str, after: str | None = None) -> bytes:
    from io import BytesIO

    root = ET.Element(f"{{{WORD}}}document")
    body = ET.SubElement(root, f"{{{WORD}}}body")
    stamp = datetime.now(timezone.utc).isoformat()
    revision_id = 0

    def paragraph(text: str, revision: str | None = None) -> None:
        nonlocal revision_id
        node = ET.SubElement(body, f"{{{WORD}}}p")
        container = node
        if revision:
            revision_id += 1
            attrs = {f"{{{WORD}}}id": str(revision_id), f"{{{WORD}}}author": "降AI后纠错",
                     f"{{{WORD}}}date": stamp}
            properties = ET.SubElement(node, f"{{{WORD}}}pPr")
            mark = ET.SubElement(properties, f"{{{WORD}}}rPr")
            ET.SubElement(mark, f"{{{WORD}}}{revision}", attrs)
            container = ET.SubElement(node, f"{{{WORD}}}{revision}", attrs)
        run = ET.SubElement(container, f"{{{WORD}}}r")
        run_properties = ET.SubElement(run, f"{{{WORD}}}rPr")
        ET.SubElement(run_properties, f"{{{WORD}}}rFonts", {
            f"{{{WORD}}}ascii": "Arial Unicode MS", f"{{{WORD}}}hAnsi": "Arial Unicode MS",
            f"{{{WORD}}}eastAsia": "Arial Unicode MS"})
        child = ET.SubElement(run, f"{{{WORD}}}{'delText' if revision == 'del' else 't'}")
        child.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        child.text = text

    previous = before.split("\n")
    if after is None:
        for line in previous:
            paragraph(line)
    else:
        current = after.split("\n")
        for action, left_start, left_end, right_start, right_end in SequenceMatcher(
                None, previous, current, autojunk=False).get_opcodes():
            if action == "equal":
                for line in previous[left_start:left_end]:
                    paragraph(line)
            else:
                for line in previous[left_start:left_end]:
                    paragraph(line, "del")
                for line in current[right_start:right_end]:
                    paragraph(line, "ins")
    settings_xml = f'<w:settings xmlns:w="{WORD}"><w:trackRevisions/></w:settings>'
    content_types = '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/></Types>'
    root_rels = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    document_rels = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/></Relationships>'
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in {"[Content_Types].xml": content_types, "_rels/.rels": root_rels,
                              "word/document.xml": ET.tostring(root, encoding="utf-8", xml_declaration=True),
                              "word/settings.xml": settings_xml, "word/_rels/document.xml.rels": document_rels}.items():
            archive.writestr(name, content)
    return stream.getvalue()


def write_delivery(directory: Path, original: str, rewritten: str, final: str,
                   issues: list[dict], title: str, stamp: str) -> dict:
    safe_title = re.sub(r'[\s/\\:*?"<>|%]+', "", title)[:80] or "书稿"
    stem = f"【降AI后纠错】_{safe_title}_{stamp}"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "双稿纠错"
    sheet.append(["原文是什么", "建议改成什么", "备注"])
    for issue in issues:
        remark = issue["remark"]
        if issue["severe"] and "严重问题" not in remark:
            remark = "严重问题；" + remark
        sheet.append([issue["original"], issue["replacement"], remark])
        if issue["severe"]:
            sheet.cell(sheet.max_row, 1).font = Font(name="微软雅黑", size=10, color="FF0000")
    if not issues:
        sheet.append(["未发现需要修改的明显错误或局部表达问题，文稿可以进入电子书制作。", "", ""])
    border = Border(left=Side(style="thin"), right=Side(style="thin"),
                    top=Side(style="thin"), bottom=Side(style="thin"))
    for row in sheet:
        for cell in row:
            if isinstance(cell.value, str):
                cell.data_type = "s"
            color = "FF0000" if cell.font.color and cell.font.color.type == "rgb" and cell.font.color.rgb == "00FF0000" else None
            cell.font = Font(name="微软雅黑", size=10, bold=cell.row == 1, color=color)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            cell.border = border
        sheet.row_dimensions[row[0].row].height = min(409, max(30, max(
            len(str(cell.value or "")) / (28 if cell.column < 3 else 16) for cell in row) * 15 + 15))
    for column, width in [("A", 60), ("B", 60), ("C", 36)]:
        sheet.column_dimensions[column].width = width
    workbook.save(directory / (stem + ".xlsx"))
    (directory / (stem + ".docx")).write_bytes(word_document(rewritten, final))
    (directory / "原始文稿.docx").write_bytes(word_document(original))
    atomic_text(directory / "原始文稿.md", original)
    atomic_text(directory / "降AI后原稿.md", rewritten)
    atomic_text(directory / "final.md", final)
    filenames = [stem + ".xlsx", stem + ".docx", "原始文稿.docx", "原始文稿.md", "降AI后原稿.md", "final.md"]
    with zipfile.ZipFile(directory / "delivery.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name in filenames:
            archive.write(directory / name, name)
    return {"docx": stem + ".docx", "xlsx": stem + ".xlsx", "zip": "delivery.zip"}


def finalize_manuscript(text: str, output_dir: Path, *, title: str = "书稿", progress=None) -> str:
    directory = output_dir / "ai_or_finalizer"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "workflow.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FinalizerHold("该书降AI交付步骤正在运行，禁止并行重复扣费") from exc
        manifest_path = directory / "manifest.json"
        state = json.loads(manifest_path.read_text()) if manifest_path.exists() else {
            "version": VERSION, "source_hash": digest(text), "status": "running",
            "stamp": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y年%-m月%-d日_%H时%M分%S秒"),
            "chunks": [], "detection": "not_requested"}
        if state["source_hash"] != digest(text) or state.get("version") != VERSION:
            raise FinalizerHold("原稿或处理版本已变更，已有付费断点不能覆盖；请人工确认")
        if state["status"] == "completed":
            final = (directory / "final.md").read_text(encoding="utf-8")
            if digest(final) != state["final_hash"]:
                raise FinalizerHold("最终交付文件校验失败")
            return final
        atomic_text(directory / "source.md", text)
        atomic_json(manifest_path, state)
        chunks = split_chunks(text, settings.AI_OR_CHUNK_CHARS)
        rewrites, finals, issues = [], [], []
        try:
            for index, chunk in enumerate(chunks):
                if not chunk.strip() or chunk.lstrip().startswith(("#", "![")):
                    rewrites.append(chunk)
                    finals.append(chunk)
                    if len(state["chunks"]) <= index:
                        state["chunks"].append({"hash": digest(chunk), "actions": {}, "protected": True})
                    continue
                if progress:
                    progress(f"降AI及双稿纠错 {index + 1}/{len(chunks)}")
                if len(state["chunks"]) <= index:
                    state["chunks"].append({"hash": digest(chunk), "actions": {}})
                entry = state["chunks"][index]
                if entry["hash"] != digest(chunk):
                    raise FinalizerHold("分段断点与原稿不一致")
                for action in ("rewrite", "correction"):
                    artifact = directory / f"{index:05d}_{action}.json"
                    if artifact.exists():
                        result = json.loads(artifact.read_text())
                    else:
                        if entry["actions"].get(action) == "inflight":
                            raise FinalizerHold("上次付费请求结果不确定；请核对平台记录后人工恢复")
                        entry["actions"][action] = "inflight"
                        atomic_json(manifest_path, state)
                        started = time.monotonic()
                        result = call_rewrite(chunk) if action == "rewrite" else call_correction(chunk, rewritten)
                        atomic_json(artifact, result)
                        logger.info("finalizer action=%s model=%s cost=%s status=completed elapsed=%.2f",
                                    action, result.get("model", "zhuque-v1-paragraph-enhanced"),
                                    result.get("consumeAmount", result.get("cost")), time.monotonic() - started)
                    entry["actions"][action] = "saved"
                    atomic_json(manifest_path, state)
                    if action == "rewrite":
                        ending = chunk[len(chunk.rstrip("\n")):]
                        rewritten = result["outputText"].rstrip("\n") + ending
                        validate_rewrite(chunk, rewritten)
                    else:
                        corrected, chunk_issues = parse_issues(result["text"], rewritten)
                rewrites.append(rewritten)
                finals.append(corrected)
                issues.extend(chunk_issues)
            rewritten = "".join(rewrites)
            final = "".join(finals)
            if any(issue["severe"] for issue in issues):
                atomic_json(directory / "issues.json", issues)
            artifacts = write_delivery(directory, text, rewritten, final, issues, title, state["stamp"])
            if any(issue["severe"] for issue in issues):
                raise FinalizerHold("双稿纠错发现严重问题，已生成标红Excel和修订Word，需人工确认后交付")
            state.update(status="completed", final_hash=digest(final), artifacts=artifacts,
                         issue_count=len(issues), correction_model=settings.AI_OR_CORRECTION_MODEL or settings.DEEPSEEK_MODEL)
            atomic_json(manifest_path, state)
            return final
        except Exception as exc:
            state.update(status="manual_review", error=str(exc) if isinstance(exc, FinalizerHold) else type(exc).__name__)
            atomic_json(manifest_path, state)
            logger.error("finalizer status=manual_review error_type=%s", type(exc).__name__)
            if isinstance(exc, FinalizerHold):
                raise
            raise FinalizerHold("降AI或双稿纠错未完成，原稿与断点已保存，需人工核对") from exc


def finalize_task_manuscript(task, text: str, output_dir: Path, *, progress=None) -> str:
    if not enabled_for(task.created_at):
        return text
    cfg = dict(task.config or {})
    heading = next((line.lstrip("#").strip() for line in text.splitlines() if line.startswith("#")), "书稿")
    final = finalize_manuscript(text, output_dir, title=str(cfg.get("title") or heading), progress=progress)
    cfg.update(ai_or_finalizer_status="completed", ai_or_detection_status="not_requested",
               ai_or_correction_model=settings.AI_OR_CORRECTION_MODEL or settings.DEEPSEEK_MODEL)
    task.config = cfg
    return final
