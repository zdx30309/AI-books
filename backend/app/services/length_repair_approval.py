import json
from pathlib import Path

from app.services.general_longform_runtime import atomic_json, atomic_text
from app.services.general_short_length import (
    LengthHold, apply_approved_length_reduction, digest, manuscript_parts, word_count,
)


def _inputs(task, output_dir):
    root = Path(output_dir)
    if (task.config or {}).get("category") != "general_short":
        raise LengthHold("此入口只批准通用短篇的局部篇幅稿")
    directory = root / "approved_length_repair"
    initial = (root / "01_writer.md").read_text().strip()
    source = (task.config or {}).get("raw_text")
    if not isinstance(source, str) or not source.strip():
        raise LengthHold("缺少原始资料，不能批准局部稿")
    base = (directory / "base.md").read_text()
    if [(part[0], part[1]) for part in manuscript_parts(initial)] != [(part[0], part[1]) for part in manuscript_parts(base)]:
        raise LengthHold("局部稿目录与初稿不符")
    return directory, initial, source, base


def prepare_candidate(task, output_dir, response, chapter_number=8):
    directory, initial, source, base = _inputs(task, output_dir)
    response = response.strip()
    result = apply_approved_length_reduction(base, response, chapter_number)
    record = {"status": "ready", "chapter_number": chapter_number,
              "initial_sha256": digest(initial), "source_sha256": digest(source), "base_sha256": digest(base),
              "response_sha256": digest(response), "result_sha256": digest(result),
              "response_filename": "accepted_response.md",
              "body_words": sum(word_count(part[2]) for part in manuscript_parts(result))}
    atomic_text(directory / "accepted_response.md", response)
    atomic_text(directory / "result.md", result)
    atomic_json(directory / "candidate.json", record)
    return record


def verified_candidate(task, output_dir):
    directory, initial, source, base = _inputs(task, output_dir)
    root = Path(output_dir)
    if any((root / name).exists() for name in ("02_polish.md", "03_refine.md", "04_format.md", "editor_v05")):
        raise LengthHold("已有旧下游付费产物，需先核对归档，不能直接批准新稿覆盖或绕过")
    for path in directory.glob("*request*.json"):
        request = json.loads(path.read_text())
        if not isinstance(request, dict) or request.get("status") not in {"response_saved", "budget_blocked"}:
            raise LengthHold("局部压缩仍有未核对的付费请求，不能批准")
        if request["status"] == "response_saved":
            name = "response.md" if path.name == "request.json" else path.name.replace("_request.json", "_response.md")
            if name == path.name or request.get("response_sha256") != digest((directory / name).read_text().strip()):
                raise LengthHold("局部压缩付费响应或hash不完整，不能批准")
    record = json.loads((directory / "candidate.json").read_text())
    if not isinstance(record, dict) or record.get("status") != "ready" or record.get("response_filename") != "accepted_response.md":
        raise LengthHold("局部稿尚未准备就绪")
    response = (directory / "accepted_response.md").read_text().strip()
    result = (directory / "result.md").read_text()
    expected = {"initial_sha256": digest(initial), "source_sha256": digest(source), "base_sha256": digest(base),
                "response_sha256": digest(response), "result_sha256": digest(result)}
    if any(record.get(key) != value for key, value in expected.items()):
        raise LengthHold("局部稿或原资料hash变化，禁止批准")
    if apply_approved_length_reduction(base, response, record.get("chapter_number")) != result:
        raise LengthHold("修复稿并非只修改指定章节")
    return {**record, "body_words": sum(word_count(part[2]) for part in manuscript_parts(result))}


def candidate_summary(task, output_dir):
    directory = Path(output_dir) / "approved_length_repair"
    if not directory.is_dir():
        return {"status": "missing", "reason": "未准备局部压缩稿"}
    try:
        record = verified_candidate(task, output_dir)
        return {key: record[key] for key in ("status", "result_sha256", "chapter_number", "body_words")}
    except (OSError, ValueError, KeyError, TypeError, LengthHold):
        return {"status": "invalid", "reason": "已有篇幅断点，但尚无通过全书校验的压缩稿，不能批准续跑"}
