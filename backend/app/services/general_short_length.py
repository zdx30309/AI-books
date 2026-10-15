import hashlib
import json
import math
import re
from pathlib import Path

from app.core.config import settings
from app.services.aigc_quality_gate import AigcManualReviewRequired
from app.services.general_longform_runtime import atomic_json, atomic_text
from app.services.generation_spend import GenerationBudgetExceeded


class LengthHold(AigcManualReviewRequired):
    pass


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


GENERAL_SHORT = "general_short"


def contract(category=GENERAL_SHORT):
    if category == GENERAL_SHORT:
        return {"label": "通用短篇", "min": 18000, "max": 22000, "target": 21000, "supplement": 20000,
                "chapters": (8, 10), "ceiling": 3000, "prompt": "editor_v05/length_repair.md",
                "action": "general_short_length_repair"}
    from app.services.content_engine import _STANDARD_LONGFORM_CATEGORY_IDS

    if category not in _STANDARD_LONGFORM_CATEGORY_IDS:
        raise ValueError("该分类不适用标准长文篇幅契约")
    low = int(getattr(settings, "LONGFORM_MIN_WORDS", 11000))
    high = int(getattr(settings, "LONGFORM_MAX_WORDS", 25000))
    if not 0 < low <= high:
        raise LengthHold("标准长文篇幅配置无效")
    target = min(max(int(getattr(settings, "LONGFORM_TARGET_WORDS", 14000)), low), high)
    return {"label": "标准长文", "min": low, "max": high, "target": target,
            "supplement": low + (target - low) // 2, "chapters": (1, 40), "ceiling": 3000,
            "prompt": "longform_length/length_repair.md", "action": "longform_length_repair"}


def issues(markdown, category=GENERAL_SHORT):
    rule = contract(category)
    chapters = manuscript_parts(markdown)
    errors = []
    low, high = rule["chapters"]
    if not low <= len(chapters) <= high or [part[0] for part in chapters] != list(range(1, len(chapters) + 1)):
        errors.append(f"{rule['label']}须有连续编号的{low}至{high}章")
    total = sum(word_count(part[2]) for part in chapters)
    if not rule["min"] <= total <= rule["max"]:
        errors.append(f"{rule['label']}实际正文{total}词，须为{rule['min']}至{rule['max']}词（不含标题目录及标点）")
    return errors


def require_length(markdown, category=GENERAL_SHORT):
    errors = issues(markdown, category)
    if errors:
        raise LengthHold("；".join(errors))


def supplement(response, source_text, chapters, number, minimum, maximum, directory, accepted_maximum=None,
               action="general_short_length_supplement", prompt="editor_v05/length_supplement.md"):
    from app.services import deepseek

    instruction = (Path(__file__).parents[1] / "prompts" / prompt).read_text()
    for attempt in range(1, 4):
        count = word_count(response)
        if count >= minimum:
            return response
        payload = json.dumps({"source_text": source_text, "chapter_body": response,
                              "chapter_number": number, "chapters": [{"number": part[0], "title": part[1]} for part in chapters],
                              "minimum_words": minimum - count, "maximum_words": maximum - count}, ensure_ascii=False)
        signature = digest(payload + instruction)
        stem = f"{number:02d}_supplement_{attempt}_{signature[:16]}"
        request_path = directory / (stem + "_request.json")
        response_path = directory / (stem + "_response.json")
        if request_path.exists():
            record = json.loads(request_path.read_text())
            if record.get("request_sha256") != signature:
                raise LengthHold("补写增补断点输入变化")
        else:
            record = {}
        if response_path.exists():
            raw = response_path.read_text()
        else:
            if record.get("status") not in (None, "budget_blocked"):
                raise LengthHold("局部增补付费结果不确定，不自动重放")
            record = {"request_sha256": signature, "model": settings.DEEPSEEK_WRITER_MODEL or settings.DEEPSEEK_MODEL,
                      "action": action, "status": "inflight", "cost": None}
            atomic_json(request_path, record)
            try:
                raw = deepseek.generate_text(payload, system_instruction=instruction, model=record["model"],
                                              max_output_tokens=3072, thinking=False, fail_on_max_tokens=True)
            except GenerationBudgetExceeded:
                record["status"] = "budget_blocked"
                atomic_json(request_path, record)
                raise
            except Exception as exc:
                record.update(status="uncertain_or_failed", error=str(exc))
                atomic_json(request_path, record)
                raise
            atomic_text(response_path, raw)
            record.update(status="response_saved", response_sha256=digest(raw))
            atomic_json(request_path, record)
        if record.get("response_sha256") != digest(raw):
            raise LengthHold("局部增补响应hash不一致")
        try:
            result = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
            anchor, addition = result["after"], result["content"]
            if not isinstance(anchor, str) or not isinstance(addition, str) or not anchor.strip() or response.count(anchor) != 1:
                raise ValueError("增补定位不唯一")
            if word_count(addition) < 30 or re.search(r"(?m)^\s*(?:@|！！！|```)", addition):
                raise ValueError("增补正文无效")
            candidate = response.replace(anchor, anchor + "\n\n" + addition.strip(), 1)
            if word_count(candidate) > (accepted_maximum or maximum):
                raise ValueError("增补后超出章节上限")
        except (KeyError, TypeError, ValueError) as exc:
            raise LengthHold("局部增补响应未通过核验，已保存，不自动付费重试：" + str(exc)) from exc
        response = candidate
    if word_count(response) < minimum:
        raise LengthHold("最多三次局部增补后篇幅仍不足，需人工复核")
    return response


def apply_approved_length_reduction(base, response, chapter_number):
    chapters = manuscript_parts(base)
    if type(chapter_number) is not int or chapter_number not in [part[0] for part in chapters]:
        raise LengthHold("局部篇幅修复章节定位无效")
    original = next(part[2] for part in chapters if part[0] == chapter_number)
    if not 1000 <= word_count(response) <= min(3000, word_count(original)):
        raise LengthHold("局部压缩结果不符合章节篇幅要求")
    if re.search(r"(?m)^\s*(?:@|#|！！！|```)", response):
        raise LengthHold("局部压缩结果包含标题或其他章节标记")
    prefix, body = base.split("@正文", 1)
    ending = re.search(r"(?m)^@(?:参考|编辑|全书完成|附录).*$", body)
    suffix = body[ending.start():] if ending else ""
    result = prefix + "@正文\n\n" + "\n\n".join(
        f"@{number}. {title}\n\n{response.strip() if number == chapter_number else content}"
        for number, title, content in chapters)
    result += "\n\n" + suffix
    require_length(result)
    return result


def load_approved_length_repair(markdown, source_text, output_dir, expected_sha256):
    directory = Path(output_dir) / "approved_length_repair"
    record = json.loads((directory / "approval.json").read_text())
    base = (directory / "base.md").read_text()
    response_name = record.get("response_filename", "response.md")
    if response_name not in {"response.md", "accepted_response.md"}:
        raise LengthHold("批准稿的局部压缩响应文件名无效")
    response = (directory / response_name).read_text().strip()
    result = (directory / "result.md").read_text()
    expected = {"initial_sha256": digest(markdown), "source_sha256": digest(source_text),
                "base_sha256": digest(base), "response_sha256": digest(response), "result_sha256": digest(result)}
    if record.get("status") != "approved" or not record.get("human_approval"):
        raise LengthHold("局部篇幅修复缺少明确人工批准")
    if expected_sha256 != expected["result_sha256"] or any(record.get(key) != value for key, value in expected.items()):
        raise LengthHold("局部篇幅修复与原稿、资料或批准hash不一致")
    initial_parts, base_parts = manuscript_parts(markdown), manuscript_parts(base)
    if [(part[0], part[1]) for part in initial_parts] != [(part[0], part[1]) for part in base_parts]:
        raise LengthHold("局部篇幅修复改变了原始章节目录")
    if apply_approved_length_reduction(base, response, record.get("chapter_number")) != result:
        raise LengthHold("批准稿并非仅局部压缩指定章节")
    return result


def repair_length(markdown, source_text, output_dir, on_stage=None, category=GENERAL_SHORT, *, approved_sha256=None):
    from app.services import deepseek

    if approved_sha256:
        if category != GENERAL_SHORT:
            raise LengthHold("通用短篇批准稿不能用于标准长文")
        return load_approved_length_repair(markdown, source_text, output_dir, approved_sha256)
    rule = contract(category)
    if not issues(markdown, category):
        return markdown
    chapters = manuscript_parts(markdown)
    low, high = rule["chapters"]
    if not low <= len(chapters) <= high or [part[0] for part in chapters] != list(range(1, len(chapters) + 1)):
        raise AigcManualReviewRequired(f"章节结构不满足{low}至{high}章，不能自动补写篇幅")
    if sum(word_count(part[2]) for part in chapters) > rule["max"]:
        raise AigcManualReviewRequired("正文超长，需编辑确认局部删改，不自动整篇压缩")
    if output_dir is None or "@正文" not in markdown:
        raise AigcManualReviewRequired("篇幅补写需要完整前置结构和持久断点目录")
    if category != GENERAL_SHORT and len(chapters) * rule["ceiling"] < rule["min"]:
        raise LengthHold("现有章节无法在局部补写边界内满足全书下限，需人工确认，不自动付费重写")
    instruction = (Path(__file__).parents[1] / "prompts" / rule["prompt"]).read_text()
    identity = digest(json.dumps({"draft": digest(markdown), "source": digest(source_text),
                                  "prompt": digest(instruction), "model": settings.DEEPSEEK_WRITER_MODEL or settings.DEEPSEEK_MODEL}, sort_keys=True))
    directory = Path(output_dir) / "length_repair" / identity
    directory.mkdir(parents=True, exist_ok=True)
    atomic_text(directory / "source.md", markdown)
    minimum = min(rule["ceiling"], math.ceil(rule["target"] / len(chapters)))
    maximum = min(rule["ceiling"], math.floor(rule["max"] / len(chapters))) if category != GENERAL_SHORT else math.floor(rule["max"] / len(chapters))
    repaired = []
    for number, title, original in chapters:
        if word_count(original) >= minimum and (category != GENERAL_SHORT or word_count(original) <= maximum):
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
                      "action": rule["action"], "status": "inflight", "cost": None}
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
        chapter_ceiling = rule["ceiling"]
        if 600 <= count < minimum:
            response = supplement(response, source_text, chapters, number,
                                  min(math.ceil(rule["supplement"] / len(chapters)), chapter_ceiling),
                                  min(maximum, chapter_ceiling), directory, chapter_ceiling,
                                  action=rule["action"].replace("_repair", "_supplement"),
                                  prompt=rule["prompt"].replace("length_repair", "length_supplement"))
            count = word_count(response)
        if not math.ceil(rule["min"] / len(chapters)) <= count <= chapter_ceiling or re.search(r"(?m)^\s*(?:@|！！！|```)", response):
            raise LengthHold(f"第{number}章补写结果{count}词，未满足章节篇幅契约；响应已保存，不自动付费重试")
        if category != GENERAL_SHORT and count < word_count(original):
            raise LengthHold(f"第{number}章补写缩短原章，响应已保存，不自动付费重试")
        repaired.append((number, title, response))
    omitted = []
    total = sum(word_count(part[2]) for part in repaired)
    if total > rule["max"]:
        for artifact in sorted(directory.glob("*_supplement_*_response.json")):
            addition = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", artifact.read_text().strip())).get("content")
            if not isinstance(addition, str) or not addition.strip():
                continue
            chapter_index = int(artifact.name.split("_", 1)[0]) - 1
            number, title, body = repaired[chapter_index]
            span = "\n\n" + addition.strip()
            if body.count(span) != 1:
                continue
            candidate = body.replace(span, "", 1)
            reduction = word_count(body) - word_count(candidate)
            if word_count(candidate) < 1000 or total - reduction < rule["min"]:
                continue
            repaired[chapter_index] = (number, title, candidate)
            total -= reduction
            omitted.append({"chapter": number, "artifact": artifact.name, "words": reduction})
            if total <= rule["max"]:
                break
    prefix, body = markdown.split("@正文", 1)
    ending = re.search(r"(?m)^@(?:参考|编辑|全书完成|附录).*$", body)
    suffix = body[ending.start():] if ending else ""
    result = prefix + "@正文\n\n" + "\n\n".join(f"@{number}. {title}\n\n{content}" for number, title, content in repaired)
    result += "\n\n" + suffix
    require_length(result, category)
    atomic_text(directory / "result.md", result)
    atomic_json(directory / "result.json", {"sha256": digest(result), "status": "length_checked",
                                            "body_words": sum(word_count(part[2]) for part in repaired),
                                            "omitted_optional_supplements": omitted})
    return result
