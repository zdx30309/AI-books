from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import PROJECT_ROOT, settings
from app.services.ai_or_finalizer import FinalizerHold, digest, finalize_manuscript
from app.services.general_longform_runtime import atomic_json, atomic_text
from app.services.general_short_report import REPORT_DIR_NAME, build_report_docx, report_filename
from app.services.general_short_similarity import (
    MultiSourceReport, SourceAudit, StructureLayer, boundary_label, composite_score,
    denoise, measure_literal, normalize, one_decimal, structure_fallback_notice,
)

logger = logging.getLogger(__name__)
PROMPTS = Path(__file__).resolve().parents[1] / "prompts/editor_v05"
VERSION = "editor-v0.5"


def applies(task) -> bool:
    if not settings.EDITOR_V05_ENABLED:
        return False
    cfg = task.config or {}
    category = str(cfg.get("category") or "social_science")
    from app.services.classical_chinese import CLASSICAL_CHINESE_CATEGORY_IDS

    if category in {"general_longform", "public_domain_rewrite", "comic_series", *CLASSICAL_CHINESE_CATEGORY_IDS} or "comic" in category:
        return False
    if settings.EDITOR_V05_START_AT:
        boundary = datetime.fromisoformat(settings.EDITOR_V05_START_AT.replace("Z", "+00:00"))
        created = task.created_at
        if created is None or created.replace(tzinfo=created.tzinfo or timezone.utc) < boundary:
            return False
    return str(getattr(task, "source_url", "")).startswith(("http://", "https://")) or cfg.get("input_source") in {
        "reference_article", "video_transcript", "manual_text",
    }


def source_text(task) -> str:
    cfg = task.config or {}
    for key in ("raw_text", "video_text", "transcript"):
        text = cfg.get(key)
        if isinstance(text, str) and text.strip():
            return text
    raw_path = cfg.get("source_text_path")
    if raw_path:
        path = Path(raw_path)
        path = (path if path.is_absolute() else PROJECT_ROOT / path).resolve()
        if not path.is_relative_to((PROJECT_ROOT / "backend").resolve()):
            raise FinalizerHold("相似度原稿路径超出允许范围")
        return path.read_text(encoding="utf-8")
    raise FinalizerHold("0.5版缺少完整视频文案或参考原稿，不能伪造相似度报告；请补充原稿")


def call_audit(source: str, draft: str) -> dict:
    from app.services.ai_or_finalizer import get_deepseek_client
    from app.services.generation_spend import reserve_deepseek
    from app.utils import paid_api_guard
    from app.utils.distributed_limiter import distributed_semaphore

    if max(len(source), len(draft)) > 200000:
        raise FinalizerHold("相似度两稿超过本版完整审查窗口，需人工处理；不会截断后给结论")
    rules = (PROMPTS / "similarity.md").read_text(encoding="utf-8")
    transport = (PROMPTS / "similarity_transport.md").read_text(encoding="utf-8")
    prompt = rules + "\n\n" + transport + "\n\n" + json.dumps({"原稿": source, "新稿": draft}, ensure_ascii=False)
    model = settings.AI_OR_CORRECTION_MODEL or settings.DEEPSEEK_MODEL
    cap = settings.EDITOR_V05_AUDIT_MAX_TOKENS
    cap, reservation_index = reserve_deepseek(prompt, None, model, cap, with_index=True)
    with distributed_semaphore("deepseek-format", limit=settings.DEEPSEEK_FORMAT_MAX_CONCURRENT_CALLS):
        paid_api_guard.guard("deepseek")
        response = get_deepseek_client().with_options(max_retries=0).chat.completions.create(
            model=model, messages=[{"role": "user", "content": prompt}], temperature=0,
            response_format={"type": "json_object"}, max_tokens=cap,
            extra_body={"thinking": {"type": "disabled"}},
        )
    choice = response.choices[0]
    result = {"text": choice.message.content or "", "model": model, "finish_reason": choice.finish_reason,
              "usage": response.usage.model_dump() if response.usage else None, "cost": None,
              "budget_reservation_index": reservation_index, "response_id": getattr(response, "id", None),
              "response_model": getattr(response, "model", None)}
    logger.info("editor-v05 action=similarity model=%s cost=null status=%s", model, choice.finish_reason)
    return result


def required_text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FinalizerHold(f"相似度依据缺少{label}，结果已保存供复核，不自动重放")
    return value


def evidence_rows(value, source: str, draft: str, *, levels: bool) -> list[dict]:
    if not isinstance(value, list):
        raise FinalizerHold("相似度依据表不完整")
    source_clean, draft_clean = normalize(source), normalize(draft)
    seen = set()
    for row in value:
        if not isinstance(row, dict):
            raise FinalizerHold("相似度依据记录格式不正确")
        quote = normalize(required_text(row.get("source_quote"), "原稿摘录"))
        if quote not in source_clean or quote in seen:
            raise FinalizerHold("相似度原稿摘录无法核验或重复，不能给出分数")
        seen.add(quote)
        if levels:
            level = row.get("level")
            if level not in {"完整", "部分", "无"}:
                raise FinalizerHold("相似度对应程度不正确")
            matched = level != "无"
        else:
            if not isinstance(row.get("reused"), bool):
                raise FinalizerHold("数据沿用标记不正确")
            matched = row["reused"]
        if matched and normalize(required_text(row.get("draft_quote"), "新稿摘录")) not in draft_clean:
            raise FinalizerHold("相似度新稿摘录无法核验，不能给出分数")
    return value


def scored_report(source: str, draft: str, payload: dict, title: str) -> MultiSourceReport:
    literal = measure_literal(normalize(denoise(source)), normalize(denoise(draft)), exact_metrics=True)
    units = evidence_rows(payload.get("units"), source, draft, levels=True)
    if not units or len(units) > 80:
        raise FinalizerHold("相似度论点单元表为空或超过80条")
    structure = StructureLayer(unit_total=len(units))
    for index, row in enumerate(units, 1):
        level = row["level"]
        structure.unit_full += level == "完整"
        structure.unit_partial += level == "部分"
        structure.unit_none += level == "无"
        location = required_text(row.get("location"), "新稿位置")
        structure.unit_map.append({"id": str(index), "unit": required_text(row.get("unit"), "论点") + "；原稿：" + row["source_quote"],
                                   "level": level, "evidence": location + "；" + str(row.get("draft_quote") or "无")})
    structure.unit_match_score = one_decimal((structure.unit_full + structure.unit_partial * 0.5) / len(units) * 100)
    case_order = payload.get("case_order_score")
    narrative = payload.get("narrative_score")
    if case_order not in {100, 85, 70, 40, 25, 10} or narrative not in {100, 75, 40, 10}:
        raise FinalizerHold("相似度结构档位不符合编辑评分表")
    structure.case_order_score, structure.narrative_spine_score = float(case_order), float(narrative)
    structure.structure_score = one_decimal(structure.unit_match_score * 0.4 + case_order * 0.3 + narrative * 0.3)
    data = evidence_rows(payload.get("data_points"), source, draft, levels=False)
    points = [required_text(row.get("point"), "数据点") for row in data]
    if len(set(points)) != len(points):
        raise FinalizerHold("数据点重复")
    cases = evidence_rows(payload.get("cases"), source, draft, levels=True)
    entities = payload.get("entities")
    if not isinstance(entities, list) or len(set(entities)) != len(entities):
        raise FinalizerHold("具名主体表缺失或重复")
    entity_counts = []
    source_clean, draft_clean = normalize(denoise(source)), normalize(denoise(draft))
    for entity in entities:
        name = normalize(required_text(entity, "具名主体"))
        if name not in source_clean:
            raise FinalizerHold("具名主体在原稿中不存在")
        entity_counts.append({"entity": entity, "source_count": str(source_clean.count(name)), "draft_count": str(draft_clean.count(name))})
    data_score = one_decimal(sum(row["reused"] for row in data) / len(data) * 100) if data else 0.0
    case_score = one_decimal(sum(1 if row["level"] == "完整" else 0.5 if row["level"] == "部分" else 0 for row in cases) / len(cases) * 100) if cases else 0.0
    entity_score = one_decimal(sum(int(row["draft_count"]) > 0 for row in entity_counts) / len(entity_counts) * 100) if entities else 0.0
    material = one_decimal(data_score * 0.3 + case_score * 0.25 + entity_score * 0.2 + literal.literal_grade_d * 0.25)
    composite = composite_score(structure.structure_score, material)
    differences = payload.get("differentiation_list")
    if not isinstance(differences, list) or not differences or not all(isinstance(item, str) and item.strip() for item in differences):
        raise FinalizerHold("相似度报告缺少差异化内容清单")
    risks = payload.get("risks")
    if not isinstance(risks, dict):
        raise FinalizerHold("相似度报告缺少三层风险说明")
    for name in ("著作权风险", "反不正当竞争风险", "平台原创审核风险"):
        risk = risks.get(name)
        if not isinstance(risk, dict) or risk.get("level") not in {"高", "中", "低"}:
            raise FinalizerHold("相似度风险等级不完整")
        required_text(risk.get("reason"), "风险理由")
    notes = "案例次序依据：" + required_text(payload.get("case_order_reason"), "案例序列说明")
    notes += "；主线依据：" + required_text(payload.get("narrative_reason"), "叙事主线说明")
    notes += "；不确定性：" + required_text(payload.get("notes"), "复核说明")
    notes += "；检测对象为降AI前完整成稿，后续降AI纠错稿不在该分数覆盖范围内。两稿以纯文本输入，不宣称DOCX文字提取率100%。接触来源成立，风险表述不构成法律结论。"
    audit = SourceAudit(
        source_name="视频文案或参考原稿", source_chars=literal.source_chars, literal=literal, structure=structure,
        material_score=material, composite=composite, verdict="高危" if composite >= 50 else "安全",
        boundary=boundary_label(composite), fallback_notice=structure_fallback_notice(structure.structure_score),
        unit_none_count=structure.unit_none, fix_directive=required_text(payload.get("fix_directive"), "下一步操作"),
        differentiation_list=differences, material_data=data_score, material_case=case_score,
        material_entity=entity_score, material_literal=literal.literal_grade_d,
        data_points=[{"point": required_text(row.get("point"), "数据点") + "；" + row["source_quote"],
                      "reused": "是；" + row["draft_quote"] if row["reused"] else "否"} for row in data],
        case_pairs=[{"case": required_text(row.get("case"), "案例") + "；" + row["source_quote"],
                     "reused": row["level"] + "；" + str(row.get("draft_quote") or "无")} for row in cases],
        entity_counts=entity_counts, editorial_risks=risks, editorial_notes=notes,
    )
    return MultiSourceReport(sources=[audit], max_composite=composite, overall_verdict=audit.verdict,
                             overall_boundary=audit.boundary, overall_fallback_notice=audit.fallback_notice,
                             draft_name=title + "_降AI前完整成稿")


def copy_report(source: str, title: str) -> MultiSourceReport:
    literal = measure_literal(normalize(denoise(source)), normalize(denoise(source)), exact_metrics=True)
    literal.length_ratio = 100.0
    structure = StructureLayer(unit_total=1, unit_full=1, unit_match_score=100,
                               case_order_score=100, narrative_spine_score=100, structure_score=100,
                               unit_map=[{"id": "1", "unit": "原稿全文（副本）", "level": "完整", "evidence": "新稿全文完全相同"}])
    audit = SourceAudit("视频文案或参考原稿", literal.source_chars, literal, structure, 100, 100, "高危", "",
                        structure_fallback_notice(100), 0, "两稿为同一份文本，不可作为独立创作发布；重写结构与素材。",
                        ["无新增内容，两稿为副本。"], 100, 100, 100, 100)
    return MultiSourceReport(sources=[audit], max_composite=100, overall_verdict="高危",
                             overall_fallback_notice=audit.fallback_notice, draft_name=title + "_降AI前完整成稿")


def audit_manuscript(source: str, draft: str, directory: Path, title: str) -> dict:
    if not source.strip() or not draft.strip():
        raise FinalizerHold("相似度审查必须提供两份完整文稿")
    if max(len(source), len(draft)) > 200000:
        raise FinalizerHold("相似度两稿超过本版完整审查窗口；不会截断后给结论")
    directory.mkdir(parents=True, exist_ok=True)
    signature = {"version": VERSION, "source_hash": digest(source), "draft_hash": digest(draft),
                 "prompt_hash": digest((PROMPTS / "similarity.md").read_text() + (PROMPTS / "similarity_transport.md").read_text())}
    path = directory / "audit_state.json"
    state = json.loads(path.read_text()) if path.exists() else {**signature, "status": "ready"}
    if any(state.get(key) != value for key, value in signature.items()):
        raise FinalizerHold("相似度输入或提示词变更，不能覆盖旧付费断点")
    response_path = directory / "audit_response.json"
    if normalize(denoise(source)) == normalize(denoise(draft)):
        report = copy_report(source, title)
    else:
        if not response_path.exists():
            if state["status"] == "inflight":
                raise FinalizerHold("相似度付费请求结果不确定；需人工核对，禁止重复扣费")
            state["status"] = "inflight"
            atomic_json(path, state)
            try:
                response = call_audit(source, draft)
            except Exception as exc:
                from app.services.generation_spend import GenerationBudgetExceeded

                if isinstance(exc, GenerationBudgetExceeded):
                    state["status"] = "ready"
                    atomic_json(path, state)
                logger.error("editor-v05 action=similarity status=failed error_type=%s cost=null", type(exc).__name__)
                raise
            atomic_json(response_path, response)
        response = json.loads(response_path.read_text())
        from app.services.generation_spend import settle_deepseek_usage

        settle_deepseek_usage(response.get("budget_reservation_index"), response_path)
        if response.get("finish_reason") != "stop":
            raise FinalizerHold("相似度模型报告输出不完整，已保存，不自动重放")
        from app.services.editor_evidence import verified_payload

        payload, changes = verified_payload(source, draft, json.loads(response["text"]), directory, response_path)
        if changes:
            payload["notes"] = required_text(payload.get("notes"), "复核说明") + "；摘录已逐字锚定实际原文，句读差异及限定ASR赘词引用核对另存记录；两稿全文、数值、对应程度、沿用标记及评分计数未改动。"
        atomic_json(directory / "audit_verified_evidence.json", {"source_hash": digest(source), "draft_hash": digest(draft),
                                                                "response_hash": digest(response_path.read_text()),
                                                                "changes": changes, "payload": payload})
        report = scored_report(source, draft, payload, title)
        state["verified_quote_changes"] = len(changes)
    report_data = report.to_dict()
    report_data.update(version=VERSION, compared_stage="pre_ai_rewrite", source_sha256=digest(source),
                       draft_sha256=digest(draft), draft_chars=len(normalize(denoise(draft))))
    report_dir = directory.parent / REPORT_DIR_NAME
    report_dir.mkdir(parents=True, exist_ok=True)
    atomic_text(report_dir / "原稿.md", source)
    atomic_text(report_dir / "审查新稿_降AI前.md", draft)
    name = state.get("report_filename") or report_filename(title)
    target = report_dir / name
    target.write_bytes(build_report_docx(report, book_title=title))
    atomic_json(directory.parent / "05_differentiation_report.json", report_data)
    state.update(status="completed", report_filename=name, report_sha256=hashlib.sha256(target.read_bytes()).hexdigest())
    atomic_json(path, state)
    return {"path": target, "data": report_data}


def finalize_editor_task(task, text: str, output_dir: Path, *, progress=None) -> str:
    try:
        return _finalize_editor_task(task, text, output_dir, progress=progress)
    except Exception as exc:
        from app.services.aigc_quality_gate import AigcManualReviewRequired

        task.config = {**(task.config or {}), "editor_v05_status": "manual_review"}
        logger.error("editor-v05 status=manual_review error_type=%s", type(exc).__name__)
        if isinstance(exc, AigcManualReviewRequired):
            raise
        raise FinalizerHold("0.5版处理未完成，原稿与付费结果已保存，需人工核对") from exc


def _finalize_editor_task(task, text: str, output_dir: Path, *, progress=None) -> str:
    directory = output_dir / "editor_v05"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "workflow.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FinalizerHold("0.5版正在执行，禁止并行重复扣费") from exc
        source = source_text(task)
        cfg = dict(task.config or {})
        from app.tasks.tasks import _extract_book_title

        title = _extract_book_title(text)
        if title == "未命名书稿":
            title = next((line.lstrip("#").strip() for line in text.splitlines() if line.startswith("#")), str(cfg.get("title") or "书稿"))
        if progress:
            progress("0.5版：全文结构框架与素材相似度审查")
        audit = audit_manuscript(source, text, directory, title)
        atomic_text(directory / "inspiration_source.md", source)
        cfg.update(editor_v05_status="similarity_completed", editor_v05_similarity=audit["data"]["max_composite"],
                   editor_v05_verdict=audit["data"]["overall_verdict"])
        task.config = cfg
        rewrite_chunk_chars = int(cfg.get("editor_v05_rewrite_chunk_chars") or min(settings.AI_OR_CHUNK_CHARS, 3000))
        final = finalize_manuscript(text, directory, title=title, progress=progress, profile=VERSION,
                                    rewrite_chunk_chars=rewrite_chunk_chars)
        delivery_dir = directory / "ai_or_finalizer"
        manifest_path = delivery_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        corrected = delivery_dir / manifest["artifacts"]["docx"]
        archive_path = delivery_dir / "delivery.v05.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(audit["path"], audit["path"].name)
            archive.write(corrected, corrected.name)
        with zipfile.ZipFile(archive_path) as archive:
            if archive.testzip() or len(archive.namelist()) != 2:
                raise FinalizerHold("0.5版双Word交付包未通过完整性校验")
        archive_path.replace(delivery_dir / "delivery.zip")
        manifest["artifacts"]["similarity_docx"] = audit["path"].name
        atomic_json(manifest_path, manifest)
        cfg.update(editor_v05_status="completed", ai_or_finalizer_status="completed", ai_or_detection_status="not_requested",
                   ai_or_severe_issue_count=manifest.get("severe_issue_count", 0),
                   ai_or_correction_model=manifest["correction_model"])
        task.config = cfg
        return final
