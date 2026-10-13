import hashlib
import json
import math
import re
from pathlib import Path

from app.core.config import settings
from app.services.aigc_quality_gate import AigcManualReviewRequired
from app.services.general_longform_runtime import atomic_json, atomic_text
from app.services.generation_spend import GenerationBudgetExceeded


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def word_count(text):
    lines = [line for line in text.splitlines() if not re.match(r"^\s*(?:#|@|！！！)", line)]
    prose = "\n".join(lines)
    return len(re.findall(r"[\u3400-\u9fff]|[A-Za-z]+(?:['’-][A-Za-z]+)*|\d+(?:[.,]\d+)*", prose))


def manuscript_parts(markdown):
    from app.services.content_engine import _finance_chapter_parts

    body = markdown.split("@正文", 1)[-1]
    ending = re.search(r"(?m)^@(?:参考|编辑|全书完成|附录).*$", body)
    if ending:
        body = body[:ending.start()]
    return _finance_chapter_parts("@正文\n" + body)


def issues(markdown):
    chapters = manuscript_parts(markdown)
    errors = []
    if not 8 <= len(chapters) <= 10 or [part[0] for part in chapters] != list(range(1, len(chapters) + 1)):
        errors.append("通用短篇须有连续编号的8至10章")
    total = sum(word_count(part[2]) for part in chapters)
    if not 18000 <= total <= 22000:
        errors.append(f"通用短篇实际正文{total}词，须为18000至22000词（不含标题目录及标点）")
    return errors


def require_length(markdown):
    errors = issues(markdown)
    if errors:
        raise AigcManualReviewRequired("；".join(errors))


def repair_length(markdown, source_text, output_dir, on_stage=None):
    from app.services import deepseek

    if not issues(markdown):
        return markdown
    chapters = manuscript_parts(markdown)
    if not 8 <= len(chapters) <= 10 or [part[0] for part in chapters] != list(range(1, len(chapters) + 1)):
        raise AigcManualReviewRequired("章节结构不满足8至10章，不能自动补写篇幅")
    if sum(word_count(part[2]) for part in chapters) > 22000:
        raise AigcManualReviewRequired("正文超长，需编辑确认局部删改，不自动整篇压缩")
    if output_dir is None or "@正文" not in markdown:
        raise AigcManualReviewRequired("篇幅补写需要完整前置结构和持久断点目录")
    instruction = (Path(__file__).parents[1] / "prompts/editor_v05/length_repair.md").read_text()
    identity = digest(json.dumps({"draft": digest(markdown), "source": digest(source_text),
                                  "prompt": digest(instruction), "model": settings.DEEPSEEK_WRITER_MODEL or settings.DEEPSEEK_MODEL}, sort_keys=True))
    directory = Path(output_dir) / "length_repair" / identity
    directory.mkdir(parents=True, exist_ok=True)
    atomic_text(directory / "source.md", markdown)
    minimum = math.ceil(21000 / len(chapters))
    maximum = math.floor(22000 / len(chapters))
    repaired = []
    for number, title, original in chapters:
        if minimum <= word_count(original) <= maximum:
            repaired.append((number, title, original))
            continue
        payload = json.dumps({"source_text": source_text, "draft": markdown,
                              "chapters": [{"number": part[0], "title": part[1]} for part in chapters],
                              "chapter_number": number, "chapter_title": title, "chapter_body": original,
                              "minimum_words": minimum, "maximum_words": maximum}, ensure_ascii=False)
        response_path = directory / f"{number:02d}_response.md"
        request_path = directory / f"{number:02d}_request.json"
        request_hash = digest(payload)
        if request_path.exists():
            record = json.loads(request_path.read_text())
            if record.get("request_sha256") != request_hash:
                raise AigcManualReviewRequired("补写断点输入变化，禁止复用")
        else:
            record = {}
        if response_path.exists():
            response = response_path.read_text().strip()
        else:
            if record.get("status") not in (None, "budget_blocked"):
                raise AigcManualReviewRequired("章节补写付费请求结果不确定，禁止自动重放")
            record = {"request_sha256": request_hash, "model": settings.DEEPSEEK_WRITER_MODEL or settings.DEEPSEEK_MODEL,
                      "action": "general_short_length_repair", "status": "inflight", "cost": None}
            atomic_json(request_path, record)
            if on_stage:
                on_stage("length_repair_chapter", {"idx": number, "total": len(chapters)})
            try:
                response = deepseek.generate_text(payload, system_instruction=instruction, model=record["model"],
                                                   max_output_tokens=6144, thinking=False, fail_on_max_tokens=True).strip()
            except GenerationBudgetExceeded:
                record["status"] = "budget_blocked"
                atomic_json(request_path, record)
                raise
            except Exception as exc:
                record.update(status="uncertain_or_failed", error=str(exc))
                atomic_json(request_path, record)
                raise
            atomic_text(response_path, response)
            record.update(status="response_saved", response_sha256=digest(response))
            atomic_json(request_path, record)
        if record.get("response_sha256") != digest(response):
            raise AigcManualReviewRequired("补写响应hash不一致，禁止复用")
        count = word_count(response)
        if not math.ceil(18000 / len(chapters)) <= count <= maximum or re.search(r"(?m)^\s*(?:@|！！！|```)", response):
            raise AigcManualReviewRequired(f"第{number}章补写结果{count}词，未满足章节篇幅契约；响应已保存，不自动付费重试")
        repaired.append((number, title, response))
    prefix, body = markdown.split("@正文", 1)
    ending = re.search(r"(?m)^@(?:参考|编辑|全书完成|附录).*$", body)
    suffix = body[ending.start():] if ending else ""
    result = prefix + "@正文\n\n" + "\n\n".join(f"@{number}. {title}\n\n{content}" for number, title, content in repaired)
    result += "\n\n" + suffix
    require_length(result)
    atomic_text(directory / "result.md", result)
    atomic_json(directory / "result.json", {"sha256": digest(result), "status": "length_checked",
                                            "body_words": sum(word_count(part[2]) for part in repaired)})
    return result
