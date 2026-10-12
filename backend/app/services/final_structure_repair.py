import hashlib
import json
import re
from pathlib import Path

from app.core.config import settings
from app.core.quality_metrics import detect_final_hard_errors
from app.services.aigc_quality_gate import AigcManualReviewRequired
from app.services.general_longform_runtime import atomic_json, atomic_text


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def apply_groups(source, groups, category):
    from app.services.content_engine import _finance_chapter_parts

    parts = _finance_chapter_parts(source)
    if not isinstance(groups, list) or len(groups) < 4:
        raise AigcManualReviewRequired("局部章节合并方案无效")
    sequence = []
    headings = []
    bodies = []
    for number, group in enumerate(groups, 1):
        ids = group.get("chapters")
        title = group.get("title")
        if not isinstance(ids, list) or not ids or any(type(index) is not int for index in ids):
            raise AigcManualReviewRequired("合并方案章节编号无效")
        if any(index < 1 or index > len(parts) for index in ids):
            raise AigcManualReviewRequired("合并方案章节越界")
        if not isinstance(title, str) or not title.strip() or any(marker in title for marker in ("\n", "\r", "@", "#", "[", "]")):
            raise AigcManualReviewRequired("合并方案标题无效")
        if len(ids) > 1 and any(index > 3 for index in ids):
            raise AigcManualReviewRequired("仅允许局部合并前三章")
        if len(ids) == 1 and title != parts[ids[0] - 1][1]:
            raise AigcManualReviewRequired("独立章节标题不得修改")
        sequence.extend(ids)
        headings.append(f"@{number}. {title.strip()}")
        bodies.append("\n\n".join(parts[index - 1][2] for index in ids))
    if sequence != list(range(1, len(parts) + 1)):
        raise AigcManualReviewRequired("合并方案遗漏、重复或改变章节顺序")
    prefix = source.split("@正文", 1)[0]
    if "@目录" not in prefix or "@导言" not in prefix:
        raise AigcManualReviewRequired("原稿前置结构不完整")
    prefix = re.sub(r"(?ms)^@目录\s*\n.*?(?=^@导言)", "@目录\n" + "\n".join(headings) + "\n\n", prefix)
    result = prefix + "@正文\n" + "\n\n".join(heading + "\n" + body for heading, body in zip(headings, bodies)) + "\n"
    issues = detect_final_hard_errors(result, category=category)
    if issues:
        raise AigcManualReviewRequired("局部合并后仍未通过终稿质检：" + json.dumps(issues, ensure_ascii=False))
    return result


def repair_structure(output_dir, category):
    from app.services import deepseek
    from app.services.content_engine import _finance_chapter_parts

    directory = Path(output_dir)
    source = (directory / "02_polish.md").read_text()
    backup = directory / "02_polish_before_structure_repair.md"
    if backup.exists() and backup.read_text() != source:
        raise AigcManualReviewRequired("局部修复原稿已变更，不能覆盖旧备份")
    if not backup.exists():
        atomic_text(backup, source)
    parts = _finance_chapter_parts(source)
    chapters = [{"number": number, "title": title, "cjk_count": len(re.findall(r"[\u4e00-\u9fff]", body)), "sample": body[:400]}
                for number, title, body in parts]
    instruction = (Path(__file__).parents[1] / "prompts/final_structure_merge.md").read_text()
    response_path = directory / "final_structure_merge_response.json"
    request_path = directory / "final_structure_merge_request.json"
    if response_path.exists():
        response = response_path.read_text()
    else:
        if request_path.exists():
            raise AigcManualReviewRequired("局部修复付费请求结果不确定，不自动重放")
        atomic_json(request_path, {"source_sha256": digest(source), "model": settings.DEEPSEEK_WRITER_MODEL, "status": "inflight"})
        response = deepseek.generate_text(json.dumps({"chapters": chapters}, ensure_ascii=False),
                                          system_instruction=instruction, model=settings.DEEPSEEK_WRITER_MODEL,
                                          max_output_tokens=1024, thinking=False, fail_on_max_tokens=True)
        atomic_text(response_path, response)
    try:
        plan = json.loads(response)
        result = apply_groups(source, plan.get("groups"), category)
    except (ValueError, TypeError, AttributeError) as exc:
        raise AigcManualReviewRequired("局部合并模型输出无效，已保存，不自动重试") from exc
    atomic_text(directory / "approved_final_structure.md", result)
    atomic_json(directory / "approved_final_structure.json", {
        "source_sha256": digest(source), "content_sha256": digest(result), "category": category,
        "model": settings.DEEPSEEK_WRITER_MODEL, "groups": plan["groups"], "status": "quality_checked",
    })
    return digest(result)


def load_approved_structure(output_dir, category, approved_hash):
    directory = Path(output_dir)
    source = (directory / "02_polish.md").read_text()
    metadata = json.loads((directory / "approved_final_structure.json").read_text())
    result = (directory / "approved_final_structure.md").read_text()
    if (metadata.get("category") != category or metadata.get("source_sha256") != digest(source)
            or metadata.get("content_sha256") != approved_hash or digest(result) != approved_hash
            or metadata.get("status") != "quality_checked"):
        raise AigcManualReviewRequired("用户批准的局部修复断点发生变化，禁止自动续跑")
    if apply_groups(source, metadata.get("groups"), category) != result:
        raise AigcManualReviewRequired("局部修复正文与合并方案不一致")
    return result
