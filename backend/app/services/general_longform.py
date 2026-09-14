"""Serial general-longform control plane layered over the existing Task writer.

Planning, raw inputs, drafts, accepted text/state, research traces, QA and Word
artifacts live in separate versioned files on the existing shared output volume.
The normal Task run lease owns every write; only accepted text reaches Chapter.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from typing import Any, Callable
from uuid import UUID

from app.prompts.general_longform_prompts import BUNDLE, CATEGORY, get_manifest
from app.services import general_longform_documents as documents
from app.services.general_longform_runtime import (
    GeneralLongformError, GeneralLongformPaused, GeneralLongformReview, ModelGateway,
    _error_text, atomic_json, atomic_text, canonical_url, now_iso, read_json, research,
)

ROOT = Path(__file__).resolve().parents[3]
DOMAINS = {"小白理财", "女性成长", "医疗健康与体重管理", "两性关系与婚育", "情绪心理与高情商沟通",
           "认知成长与效能提升", "生活方式与人生选择", "职场成长与职业突围", "人性洞察与传统智慧"}
ACTIONS = {"plan", "revise_outline", "confirm_outline", "next_chapter", "revise_chapter", "assemble", "import_materials"}


def work_dir(task_id: UUID | str) -> Path:
    return ROOT / "backend" / "output" / f"task_{UUID(str(task_id))}" / CATEGORY


def initial_state(project_id: str, source_text: str = "") -> dict[str, Any]:
    manifest = get_manifest()
    return {"schema_version": 1, "project_id": str(UUID(project_id)), "category": CATEGORY,
            "prompt_version": manifest["version"], "prompt_source_sha256": manifest["source_sha256"],
            "phase": "needs_outline", "source_text": source_text, "outline": None,
            "outline_version": 0, "confirmed": False, "sources": {}, "chapters": {},
            "accepted_state": {}, "total_words": 0, "artifacts": [], "materials": [],
            "completed_requests": [], "request_bindings": {}, "created_at": now_iso(), "last_error": None}


def load_state(directory: Path) -> dict[str, Any] | None:
    return read_json(directory / "state.json")


def title_valid(title: Any) -> bool:
    value = title.strip() if isinstance(title, str) else ""
    return bool(value) and len(value) <= 80 and not re.search(r"[#\r\n:：—–（）()]|^\d+(?:[.、]\d*)|^[一二三四五六七八九十]+[、.]", value)


def valid_source_ids(ids: Any, sources: dict[str, Any]) -> bool:
    return isinstance(ids, list) and bool(ids) and all(isinstance(sid, str) and sid in sources and sources[sid].get("verification") == "page_read_and_abstracted" for sid in ids)


def validate_outline(outline: dict[str, Any], sources: dict[str, Any], *, confirmed: bool = False) -> list[str]:
    errors: list[str] = []
    if not isinstance(outline, dict):
        return ["大纲不是结构化对象"]
    for field in ("topic", "domain", "audience", "central_question", "voice"):
        if not isinstance(outline.get(field), str) or not outline[field].strip():
            errors.append(f"大纲缺少 {field}")
    if outline.get("domain") not in DOMAINS:
        errors.append("主领域不在九领域清单中")
    packaging = outline.get("packaging")
    if not isinstance(packaging, list) or len(packaging) not in ({1, 5} if confirmed else {5}):
        errors.append("待确认大纲必须有五组完整包装；已确认的外部大纲可只有最终包装")
        packaging = []
    for index, package in enumerate(packaging, 1):
        if not isinstance(package, dict):
            errors.append(f"方案{index}字段不完整")
            continue
        if not package.get("direction"):
            errors.append(f"方案{index}缺少包装方向")
        if not 10 <= documents.visible_count(str(package.get("title", ""))) <= 18:
            errors.append(f"方案{index}书名需要10—18个可见字符")
        if not 200 <= documents.visible_count(str(package.get("description", ""))) <= 300:
            errors.append(f"方案{index}简介需要200—300个可见字符")
    recommended = outline.get("recommended_index")
    if type(recommended) is not int or not 0 <= recommended < len(packaging):
        errors.append("推荐包装序号不合法")
    chapters = outline.get("chapters")
    if not isinstance(chapters, list) or not 12 <= len(chapters) <= 15:
        errors.append("全书需要12—15章")
        chapters = []
    total = 0
    titles = set()
    for index, chapter in enumerate(chapters, 1):
        if not isinstance(chapter, dict):
            errors.append(f"第{index}章不是有效对象")
            continue
        if chapter.get("number") != index:
            errors.append("章号必须从1连续排列")
        title = chapter.get("title")
        if not title_valid(title) or str(title) in titles:
            errors.append(f"第{index}章标题非法或重复")
        titles.add(str(title))
        for field in ("question", "new_information"):
            if not chapter.get(field):
                errors.append(f"第{index}章缺少{field}")
        sections = chapter.get("sections")
        if not isinstance(sections, list) or len(sections) < 2:
            errors.append(f"第{index}章需要至少两个有任务的小节")
            sections = []
        subtotal, names = 0, set()
        for section in sections:
            if not isinstance(section, dict):
                errors.append(f"第{index}章小节结构非法")
                continue
            name = section.get("title")
            if not title_valid(name) or str(name) in names:
                errors.append(f"第{index}章小节标题非法或重复")
            names.add(str(name))
            if not section.get("task") or not section.get("argument") or not section.get("material"):
                errors.append(f"第{index}章小节任务/观点/材料不完整")
            words = section.get("target_words")
            if type(words) is not int or not 300 <= words <= 12000:
                errors.append(f"第{index}章小节字数需要300—12000的整数")
            else:
                subtotal += words
            ids = section.get("source_ids")
            if not valid_source_ids(ids, sources):
                errors.append(f"第{index}章小节来源缺失或未核查")
        words = chapter.get("target_words")
        if type(words) is not int or words != subtotal:
            errors.append(f"第{index}章小节字数合计与章字数不一致")
        else:
            total += words
        ids = chapter.get("source_ids")
        if not valid_source_ids(ids, sources):
            errors.append(f"第{index}章来源缺失或未核查")
    if total != outline.get("target_words") or not 100000 <= total <= 110000:
        errors.append(f"字数分配合计{total}，需与总目标一致且在100000—110000之间")
    if not isinstance(outline.get("handoff"), dict) or not outline["handoff"]:
        errors.append("缺少跨对话执行资料")
    return list(dict.fromkeys(errors))


def apply_outline_patch(outline: dict[str, Any], patch: dict[str, Any], *, accepted_numbers: set[int]) -> dict[str, Any]:
    result = deepcopy(outline)
    allowed = {"topic", "domain", "audience", "central_question", "scope_exclusions", "voice", "packaging",
               "recommended_index", "recommendation", "target_words", "handoff", "research_gaps"}
    changes = patch.get("top_level_changes", {})
    if not isinstance(changes, dict) or set(changes) - allowed:
        raise GeneralLongformReview("大纲补丁包含未允许的全局结构改写")
    if accepted_numbers and set(changes) & {"topic", "domain", "audience", "central_question", "voice", "packaging", "recommended_index"}:
        raise GeneralLongformReview("已有验收正文，书名、定位及主问题保持固定")
    result.update(changes)
    seen = set()
    patches = patch.get("chapter_patches") or []
    if not isinstance(patches, list) or any(not isinstance(item, dict) for item in patches):
        raise GeneralLongformReview("章节补丁需要结构化列表")
    for item in patches:
        number = item.get("number")
        changes = item.get("changes")
        if type(number) is not int or not 1 <= number <= len(result["chapters"]) or number in seen:
            raise GeneralLongformReview("大纲补丁章号非法或重复")
        if number in accepted_numbers:
            raise GeneralLongformReview("大纲补丁不得改写已验收章节")
        allowed_chapter = {"title", "target_words", "question", "new_information", "previous_link", "next_link", "do_not_repeat", "source_ids", "sections"}
        if not isinstance(changes, dict) or set(changes) - allowed_chapter:
            raise GeneralLongformReview("章补丁字段非法，不允许新增/删除章号")
        seen.add(number)
        result["chapters"][number - 1].update(changes)
    return result


def heading_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.lstrip().startswith("#")]


def expected_headings(contract: dict[str, Any]) -> list[str]:
    return [f"##第{contract['number']}章 {contract['title']}"] + ["#" + section["title"] for section in contract["sections"]]


def split_sections(text: str) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    name, lines = "", []
    for line in text.splitlines():
        if line.startswith("#") and not line.startswith("##"):
            if name:
                items.append((name, "\n".join(lines)))
            name, lines = line[1:].strip(), []
        elif not line.startswith("##"):
            lines.append(line)
    if name:
        items.append((name, "\n".join(lines)))
    return items


def chapter_issues(text: str, contract: dict[str, Any], outline: dict[str, Any], previous: list[dict[str, Any]]) -> list[dict[str, str]]:
    issues = []
    def add(kind: str, description: str) -> None:
        issues.append({"severity": "high", "type": kind, "description": description})
    if heading_lines(text) != expected_headings(contract):
        add("format", "章/小节标题必须与确认大纲逐字一致，使用##章、#小节，无第三层")
    if not text.strip().startswith(expected_headings(contract)[0] + "\n"):
        add("format", "章首标题或正文边界不正确")
    words = documents.word_count(text)
    minimum = max(math.ceil(contract["target_words"] * .97), math.ceil(contract["target_words"] * 100000 / outline["target_words"]))
    if words < minimum:
        add("length", f"本章实测{words}字，至少需要{minimum}字，未达成计划")
    if words > math.ceil(contract["target_words"] * 1.12):
        add("length", f"本章实测{words}字，明显超出计划，需局部收敛而不吞并其他章")
    for (_, body), section in zip(split_sections(text), contract["sections"]):
        if documents.word_count(body) < int(section["target_words"] * .8):
            add("coverage", f"小节{section['title']}严重不足，需要实质展开")
    if re.search(r"\[\d+(?:[,，\-]\d+)*\]|［\d+］|【\d+】|||\[\^\d+\]|[⁰¹²³⁴⁵⁶⁷⁸⁹]|https?://|\bDOI\s*:", text):
        add("format", "正文包含文献号、引用代码或出处链接；来源应进入独立配套文件")
    if re.search(r"[（(](?:[A-Z][A-Za-z .,&-]{1,80}|[\u4e00-\u9fff]{2,12})(?:等|et al\.)?\s*[,，;；]\s*(?:19|20)\d{2}[a-z]?[）)]", text):
        add("format", "正文含作者年份式出处括注，应移入独立来源文件")
    if re.search(r"(?:文章参考来源|编辑记录|作为\s*AI|本节已完成|以下为生成内容|字数统计|全文共计|待续|此处省略)", text):
        add("format", "正文混入来源、工作记录或未完成标记")
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\u200b-\u200f\u202a-\u202e\ufeff]|\ufffd", text):
        add("format", "正文存在隐藏字符或乱码")
    paragraphs = [re.sub(r"\s", "", p) for p in text.splitlines() if documents.word_count(p) >= 60]
    existing = {re.sub(r"\s", "", p) for chapter in previous for p in chapter["content"].splitlines() if documents.word_count(p) >= 60}
    seen = set()
    for paragraph in paragraphs:
        if paragraph in seen or paragraph in existing:
            add("repetition", "发现完整段落重复：" + paragraph[:55])
            break
        seen.add(paragraph)
    return issues


def blocking(issues: list[dict[str, Any]]) -> bool:
    return any(not isinstance(item, dict) or not isinstance(item.get("severity"), str) or item["severity"] not in {"low", "medium"} for item in issues)


def validate_audit(audit: dict[str, Any], contract: dict[str, Any], sources: dict[str, Any]) -> list[dict[str, str]]:
    errors = []
    def add(description: str) -> None:
        errors.append({"severity": "high", "type": "acceptance", "description": description})
    if any(not isinstance(audit.get(key), str) or not audit[key].strip() for key in ("summary", "endingHook")):
        add("状态抽取缺少 summary 或 endingHook")
    names = [section["title"] for section in contract["sections"]]
    if audit.get("completed_sections") != names:
        add("验收未确认所有小节完整执行")
    diffs = audit.get("planned_vs_actual")
    if not isinstance(diffs, list) or [item.get("section") for item in diffs if isinstance(item, dict)] != names or any(not isinstance(item, dict) or item.get("fulfilled") is not True or not item.get("evidence") for item in diffs):
        add("Planned-vs-Actual Diff 缺失或存在未完成任务")
    ids = audit.get("used_source_ids")
    if not valid_source_ids(ids, sources):
        add("实际使用来源不完整或没有真实页面读取证据")
    if not isinstance(audit.get("issues"), list):
        add("验收缺少 issues 清单")
    if not isinstance(audit.get("new_information"), list) or not audit["new_information"] or any(not isinstance(value, str) or not value.strip() for value in audit["new_information"]):
        add("本章没有明确新增信息")
    if not isinstance(audit.get("terms"), dict) or not isinstance(audit.get("cases"), list):
        add("术语与案例状态结构缺失")
    return errors


def normalize_imported_headings(plan: dict[str, Any]) -> dict[str, Any]:
    """The source explicitly permits formatting-only repair of old outlines."""
    plan = deepcopy(plan)
    aliases = []
    for chapter in plan.get("chapters", []):
        for target in [chapter, *chapter.get("sections", [])]:
            old = target.get("title", "")
            if not isinstance(old, str):
                continue
            clean = re.sub(r"^\s*#+\s*", "", old).strip()
            clean = re.sub(r"^(?:第[\d一二三四五六七八九十]+章\s*|\d+(?:\.\d+)+[.、\s]*|[（(][一二三四五六七八九十\d]+[）)]\s*|[一二三四五六七八九十]+、\s*)", "", clean)
            clean = re.split(r"[:：—–（(]", clean, maxsplit=1)[0].strip()
            if clean != old and clean:
                target["title"] = clean
                aliases.append({"old": old, "new": clean, "chapter": chapter.get("number")})
    if aliases:
        plan.setdefault("handoff", {}).setdefault("heading_aliases", []).extend(aliases)
    return plan


def normalize_imported_chapter(text: str, contract: dict[str, Any], aliases: list[dict[str, Any]]) -> str:
    replacements = {contract["title"]: expected_headings(contract)[0], f"第{contract['number']}章 {contract['title']}": expected_headings(contract)[0]}
    replacements.update({section["title"]: "#" + section["title"] for section in contract["sections"]})
    for alias in aliases:
        if alias.get("chapter") == contract["number"] and alias.get("new") in replacements:
            replacements[str(alias["old"]).lstrip("#").strip()] = replacements[alias["new"]]
    lines = []
    for line in text.splitlines():
        plain = line.lstrip("#").strip()
        numbered = re.sub(r"^(?:\d+(?:\.\d+)+[.、\s]*|[（(][一二三四五六七八九十\d]+[）)]\s*)", "", plain)
        lines.append(replacements.get(plain) or replacements.get(numbered) or (plain if line.startswith("###") else line))
    result = "\n".join(lines).strip()
    if not result.startswith(expected_headings(contract)[0]):
        result = expected_headings(contract)[0] + "\n\n" + result
    return result


class GeneralLongformRunner:
    def __init__(self, directory: Path, state: dict[str, Any], request: dict[str, Any], *,
                 gateway: ModelGateway | None = None, checkpoint: Callable[[], None] | None = None,
                 progress: Callable[[int, str], None] | None = None):
        self.directory, self.state, self.request = directory, state, request
        self.checkpoint = checkpoint or (lambda: None)
        self.progress = progress or (lambda _number, _text: None)
        self.gateway = gateway or ModelGateway(directory, str(UUID(request["id"])), check_active=self.checkpoint,
                                               retry_uncertain=bool(request.get("retry_uncertain")))
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self) -> None:
        self.checkpoint()
        self.state["updated_at"] = now_iso()
        atomic_json(self.directory / "state.json", self.state)

    def content(self, path: str) -> str:
        resolved = (self.directory / path).resolve()
        if not resolved.is_relative_to(self.directory.resolve()):
            raise GeneralLongformError("正文路径越出项目目录")
        return resolved.read_text(encoding="utf-8")

    def put(self, relative: str, text: str) -> str:
        self.checkpoint()
        path = (self.directory / relative).resolve()
        if not path.is_relative_to(self.directory.resolve()):
            raise GeneralLongformError("产物路径越出项目目录")
        atomic_text(path, text)
        return relative

    def accepted(self, *, exclude: int | None = None) -> list[dict[str, Any]]:
        result = []
        for number, slot in sorted(self.state["chapters"].items(), key=lambda item: int(item[0])):
            record = slot.get("accepted")
            if not record or int(number) == exclude:
                continue
            content = self.content(record["acceptedContent"])
            digest = sha256(content.encode()).hexdigest()
            if digest != record.get("content_sha256") or record.get("state_source_sha256") != digest:
                raise GeneralLongformReview(f"第{number}章验收正文与状态哈希不一致")
            if not record.get("audit", {}).get("summary") or not record["audit"].get("endingHook") or not record.get("state_persisted"):
                raise GeneralLongformReview(f"第{number}章缺少摘要、衔接或已持久化状态")
            ledger = self.state["accepted_state"].get(str(number), {})
            if ledger.get("source") != "acceptedContent" or ledger.get("content_sha256") != digest or ledger.get("summary") != record["audit"]["summary"] or ledger.get("endingHook") != record["audit"]["endingHook"]:
                raise GeneralLongformReview(f"第{number}章验收状态记录与正文不一致")
            result.append({**record, "content": content})
        return result

    def context(self, **values: Any) -> dict[str, Any]:
        return {"current_time": self.request.get("created_at") or self.state.get("created_at"), "project_id": self.state["project_id"],
                "source_summary": self.state.get("source_summary", ""), "outline": self.state.get("outline"),
                "accepted_state": self.state["accepted_state"], "reference": self.state.get("reference", {}),
                "sources": self.state["sources"], **values}

    def ensure_source_summary(self) -> None:
        text = self.state.get("source_text", "")
        digest = sha256(text.encode()).hexdigest()
        if not text or self.state.get("source_summary_sha256") == digest:
            return
        data = self.gateway.json("materials", {"material": {"role": "source", "filename": "当前用户素材",
            "lines": [{"line": i, "text": line} for i, line in enumerate(text.splitlines(), 1)]}}, max_tokens=8000, slot=f"source-{digest}")
        summary = data.get("approved_summary")
        if not isinstance(summary, str) or not summary.strip():
            raise GeneralLongformReview("用户素材尚未形成摘要；原始材料未送入正文生成")
        self.state.update(source_summary=summary, source_summary_sha256=digest)
        self.save()

    def ensure_reference(self) -> None:
        if self.state.get("reference"):
            return
        self.progress(5, "通用长篇：抽象内置参考，不复制旧样稿")
        data = self.gateway.json("reference_analysis", {"purpose": "只抽象编辑机制"}, max_tokens=6000)
        cards, profile = data.get("ReferencePatternCards"), data.get("ApprovedEditingSummary")
        if set(data) != {"ReferencePatternCards", "ApprovedEditingSummary"} or not isinstance(cards, list) or not cards or not isinstance(profile, dict) or not profile:
            raise GeneralLongformReview("参考机制抽象不完整")
        allowed = {"id", "function", "mechanism", "avoid"}
        if len(cards) > 12 or any(not isinstance(card, dict) or set(card) - allowed or not card.get("mechanism") for card in cards):
            raise GeneralLongformReview("参考卡包含未批准字段或缺少抽象机制")
        serialized = json.dumps(data, ensure_ascii=False)
        raw = "\n".join((BUNDLE / item["file"]).read_text(encoding="utf-8") for item in get_manifest()["sections"] if item["role"] == "reference_only")
        compact = re.sub(r"\s", "", serialized)
        raw_compact = re.sub(r"\s", "", raw)
        if any(compact[index:index + 32] in raw_compact for index in range(max(0, len(compact) - 31))):
            raise GeneralLongformReview("参考抽象混入原文长片段，已阻止进入生成上下文")
        self.state["reference"] = {**data, "source_sha256": self.state["prompt_source_sha256"], "rawTextIncluded": False, "generated_card_count": len(cards)}
        self.save()

    def research(self, *, slot: str = "plan", **values: Any) -> dict[str, Any]:
        self.progress(10, "通用长篇：实际搜索、打开来源并核验摘要")
        result = research(self.gateway, self.context(**values), slot=slot)
        self.state["sources"].update(result["sources"])
        self.state["last_research"] = {key: value for key, value in result.items() if key != "sources"}
        self.save()
        return result

    def add_artifacts(self, records: list[dict[str, Any]]) -> None:
        for record in records:
            record["id"] = sha256(record["filename"].encode()).hexdigest()[:24]
            record["download_url"] = f"/api/v1/general-longform/{self.state['project_id']}/artifacts/{record['id']}"
            existing = next((item for item in self.state["artifacts"] if item["id"] == record["id"]), None)
            if existing and existing["sha256"] != record["sha256"]:
                raise GeneralLongformReview("同名文件版本哈希发生变化，已保留旧文件记录")
            if not existing:
                self.state["artifacts"].append(record)
        self.save()

    def plan(self) -> None:
        if self.state.get("outline"):
            if self.state.get("planned_request_id") == self.request["id"]:
                self._outline_export(confirmed=False)
                return
            raise GeneralLongformReview("已有大纲，请使用局部修改而不是重新生成整份规划")
        if self.state.get("outline_candidate"):
            raise GeneralLongformReview("已有待补齐的大纲候选，请局部修订，保留未受影响的章节")
        self.ensure_reference()
        self.ensure_source_summary()
        evidence = self.research()
        self.progress(20, "通用长篇：策划五组包装和十万字大纲")
        plan = self.gateway.json("outline", self.context(research=evidence), max_tokens=22000)
        atomic_json(self.directory / "planning" / f"candidate_{self.request['id']}.json", plan)
        errors = validate_outline(plan, self.state["sources"])
        if errors:
            self.state["outline_candidate"] = plan
            raise GeneralLongformReview("；".join(errors))
        self.state.update(outline=plan, outline_version=1, confirmed=False, phase="awaiting_outline_confirmation", outline_candidate=None, planned_request_id=self.request["id"])
        self.save()
        self._outline_export(confirmed=False)

    def _outline_export(self, *, confirmed: bool) -> None:
        key = f"{self.state['outline_version']}:{confirmed}"
        if key in self.state.setdefault("outline_exports", []):
            return
        self.add_artifacts([documents.export_outline(self.directory / "exports", self.state, confirmed=confirmed)])
        self.state["outline_exports"].append(key)
        self.save()

    def revise_outline(self) -> None:
        if self.state.get("revised_outline_request_id") == self.request["id"]:
            self._outline_export(confirmed=False)
            return
        plan = self.state.get("outline") or self.state.get("outline_candidate")
        if not plan:
            raise GeneralLongformReview("先生成或导入大纲")
        feedback = str(self.request.get("feedback") or "").strip()
        if not feedback:
            raise GeneralLongformReview("请填写需要局部修改的大纲内容")
        self.ensure_reference()
        self.ensure_source_summary()
        evidence = self.research(slot="outline-revision", outline=plan, feedback=feedback)
        patch = self.gateway.json("outline_revision", self.context(outline=plan, feedback=feedback, research=evidence), max_tokens=22000)
        accepted_numbers = {int(key) for key, slot in self.state["chapters"].items() if slot.get("accepted")}
        updated = apply_outline_patch(plan, patch, accepted_numbers=accepted_numbers)
        final_packaging = bool(self.state["confirmed"] or self.state.get("imported_outline_confirmed") or self.state.get("outline_candidate_confirmed"))
        errors = validate_outline(updated, self.state["sources"], confirmed=final_packaging)
        if errors:
            raise GeneralLongformReview("；".join(errors))
        version = self.state["outline_version"] + 1
        atomic_json(self.directory / "planning" / f"outline_v{version:02d}.json", {"outline": updated, "patch": patch, "previous": plan})
        candidate_material_id = self.state.pop("outline_candidate_material_id", None)
        if candidate_material_id:
            # Adopting a locally repaired import consumes that exact material.
            # A later import action must not try to overwrite the repaired plan.
            self.state.update(imported_outline_id=candidate_material_id, imported_outline_confirmed=final_packaging)
            for material in self.state["materials"]:
                if material["id"] == candidate_material_id:
                    material.update(imported=True, status="imported", error=None, adopted_outline_version=version)
        self.state.pop("outline_candidate_confirmed", None)
        self.state.update(outline=updated, outline_version=version, confirmed=False, phase="awaiting_outline_confirmation", outline_candidate=None, revised_outline_request_id=self.request["id"])
        self.save()
        self._outline_export(confirmed=False)

    def confirm(self) -> None:
        plan = self.state.get("outline")
        if not plan:
            raise GeneralLongformReview("尚无有效大纲可确认")
        index = self.request.get("packaging_index", plan["recommended_index"])
        if type(index) is not int or not 0 <= index < len(plan["packaging"]):
            raise GeneralLongformReview("请选择有效包装方案")
        if self.accepted() and index != plan["recommended_index"]:
            raise GeneralLongformReview("正文生产中保持已经确定的书名与简介")
        if self.state["confirmed"]:
            if index != plan["recommended_index"]:
                raise GeneralLongformReview("这份大纲已经确认；更换包装请创建局部修订版本，保留原确认文件")
            self._outline_export(confirmed=True)
            return
        updated = deepcopy(plan)
        updated["recommended_index"] = index
        errors = validate_outline(updated, self.state["sources"], confirmed=True)
        if updated.get("research_gaps"):
            errors.append("策划仍有待核实材料，请补充来源或局部调整大纲后再确认")
        if errors:
            raise GeneralLongformReview("；".join(errors))
        self.state.update(outline=updated, confirmed=True, confirmed_at=now_iso(), phase="ready_for_chapter")
        self.save()
        self._outline_export(confirmed=True)

    def next_number(self) -> int:
        number = 1
        while self.state["chapters"].get(str(number), {}).get("accepted"):
            number += 1
        if any(slot.get("accepted") for key, slot in self.state["chapters"].items() if int(key) > number):
            raise GeneralLongformReview(f"第{number}章缺失，先补齐有效前文再继续")
        return number

    def generate_chapter(self, *, revise: bool = False) -> None:
        if not self.state["confirmed"]:
            raise GeneralLongformReview("先确认大纲，再开始逐章正文")
        bindings = self.state.setdefault("request_bindings", {})
        number = bindings.get(self.request["id"])
        if number is None:
            number = int(self.request.get("chapter_number") or 0) if revise else self.next_number()
            bindings[self.request["id"]] = number
            self.save()
        if not 1 <= number <= len(self.state["outline"]["chapters"]):
            raise GeneralLongformReview("所有计划章节已写完，请执行全书终审与合稿")
        previous = self.accepted(exclude=number)
        if {item["number"] for item in previous if item["number"] < number} != set(range(1, number)):
            raise GeneralLongformReview("前章的验收正文、摘要、衔接及持久化状态不齐，已阻止跳章")
        self.ensure_reference()
        self.ensure_source_summary()
        contract = self.state["outline"]["chapters"][number - 1]
        slot = self.state["chapters"].setdefault(str(number), {"history": []})
        if slot.get("accepted", {}).get("request_id") == self.request["id"]:
            self._chapter_export(slot["accepted"])
            return
        candidate = slot.get("candidate")
        revision_source = self._candidate_text(candidate) if candidate else ""
        if not candidate or candidate.get("request_id") != self.request["id"]:
            versions = [item.get("version", 0) for item in slot.get("history", [])] + [slot.get("accepted", {}).get("version", 0), (candidate or {}).get("version", 0)]
            old_candidate = deepcopy(candidate) if candidate else None
            candidate = {"number": number, "title": contract["title"], "version": max(versions) + 1,
                         "request_id": self.request["id"], "sections": [], "status": "draft", "issues": []}
            if old_candidate:
                slot.setdefault("candidate_history", []).append(old_candidate)
                if not revise:
                    candidate["sections"] = old_candidate.get("sections", [])
                if revision_source:
                    candidate["revisionSource"] = self.put(f"chapters/{number:02d}/v{candidate['version']:02d}/revision_source.txt", revision_source)
            slot["candidate"] = candidate
        else:
            candidate["request_id"] = self.request["id"]
        folder = f"chapters/{number:02d}/v{candidate['version']:02d}"
        self.state["phase"] = "writing_chapter"
        self.save()
        if candidate.get("research_request_id") != self.request["id"]:
            candidate["research"] = self.research(slot=f"chapter-{number}", chapter=contract)
            candidate["research_request_id"] = self.request["id"]
            self.save()
        editorial = [item.get("approved_notes") for item in self.state["materials"] if item.get("approved_notes")]
        base = self.context(chapter=contract, previous_chapters=previous, research=candidate["research"],
                            total_accepted_words=self.state["total_words"], editorial_notes=editorial)
        if revise:
            accepted = slot.get("accepted")
            original = self.content(accepted["acceptedContent"]) if accepted else self.content(candidate["revisionSource"]) if candidate.get("revisionSource") else revision_source or self._candidate_text(candidate)
            if not original:
                raise GeneralLongformReview("没有可局部修订的正文")
            feedback = str(self.request.get("feedback") or "").strip()
            if not feedback:
                raise GeneralLongformReview("请说明当前章的具体修订范围")
            text = self.gateway.call("repair", {**base, "candidate": original, "issues": candidate.get("issues", []), "feedback": feedback}, max_tokens=26000, slot=f"chapter-{number}-revision")["text"].strip()
            candidate["repairedContent"] = self.put(f"{folder}/revision.txt", text)
            self.save()
        else:
            texts = []
            for index, section in enumerate(contract["sections"]):
                self.progress(25 + int(30 * index / len(contract["sections"])), f"通用长篇：第{number}章 / {section['title']}")
                existing = next((item for item in candidate["sections"] if item["index"] == index), None)
                text = self.content(existing["path"]) if existing else ""
                if documents.word_count(text) < math.ceil(section["target_words"] * .98):
                    context = {**base, "section": section, "current_chapter_sections": texts}
                    if not text:
                        text = self.gateway.call("draft", context, max_tokens=min(30000, section["target_words"] * 3 + 3000), slot=f"chapter-{number}-section-{index}")["text"].strip()
                        relative = self.put(f"{folder}/section_{index + 1:02d}_draft.txt", text)
                        item = {"index": index, "path": relative, "words": documents.word_count(text)}
                        if existing:
                            candidate["sections"].remove(existing)
                        candidate["sections"].append(item)
                    else:
                        item = existing
                    candidate["checkpoint"] = {"section": index + 1, "words": documents.word_count(text), "target": section["target_words"]}
                    candidate["draftContent"] = self.put(f"{folder}/draft.txt", self._assemble_sections(contract, texts + [text]))
                    self.save()
                    if documents.word_count(text) < math.ceil(section["target_words"] * .98) and item.get("continuation_request_id") != self.request["id"]:
                        # One bounded continuation per run; another explicit run
                        # may resume the partial section instead of rewriting it.
                        output = self.gateway.call("continue", {**context, "partial_section": text, "continuation": True}, max_tokens=min(24000, section["target_words"] * 3 + 3000), slot=f"chapter-{number}-section-{index}")["text"].strip()
                        text += "\n\n" + output
                        item["path"] = self.put(f"{folder}/section_{index + 1:02d}_continued.txt", text)
                        item["continuation_request_id"] = self.request["id"]
                        item["words"] = documents.word_count(text)
                        candidate["checkpoint"].update(words=item["words"])
                        candidate["draftContent"] = self.put(f"{folder}/draft.txt", self._assemble_sections(contract, texts + [text]))
                        self.save()
                    if documents.word_count(text) < math.ceil(section["target_words"] * .98):
                        raise GeneralLongformReview(f"第{number}章{section['title']}仍未写足；部分稿已保存，下次从本小节断点继续")
                texts.append(text)
            text = self._assemble_sections(contract, texts)
            candidate["draftContent"] = self.put(f"{folder}/draft.txt", text)
            self.save()
            for stage, minimum, maximum in [("polish", .95, 1.05), ("refine", .90, 1.10)]:
                self.progress(60 if stage == "polish" else 72, f"通用长篇：第{number}章 Gemini {'一润' if stage == 'polish' else '二润'}")
                output = self.gateway.call(stage, {**base, "candidate": text}, provider="gemini", max_tokens=26000, slot=f"chapter-{number}")["text"].strip()
                ratio = documents.word_count(output) / max(1, documents.word_count(text))
                if heading_lines(output) != expected_headings(contract) or not minimum <= ratio <= maximum:
                    # Keep the intact previous stage. This is a recorded failed
                    # editing attempt, never silently a successful Gemini pass.
                    candidate.setdefault("stage_rejections", []).append({"stage": stage, "ratio": ratio, "reason": "标题序列或篇幅保护不通过，保留上一完整稿"})
                    self.put(f"{folder}/{stage}_rejected.txt", output)
                else:
                    text = output
                    candidate["repairedContent"] = self.put(f"{folder}/{stage}.txt", text)
                self.save()
        self._check_and_accept(candidate, contract, text, previous, base)

    @staticmethod
    def _assemble_sections(contract: dict[str, Any], texts: list[str]) -> str:
        return expected_headings(contract)[0] + "\n\n" + "\n\n".join("#" + section["title"] + "\n\n" + text for section, text in zip(contract["sections"], texts))

    def _candidate_text(self, candidate: dict[str, Any]) -> str:
        path = candidate.get("repairedContent") or candidate.get("draftContent")
        if path:
            return self.content(path)
        sections = sorted(candidate.get("sections", []), key=lambda item: item["index"])
        if sections and self.state.get("outline"):
            contract = self.state["outline"]["chapters"][candidate["number"] - 1]
            return self._assemble_sections(contract, [self.content(item["path"]) for item in sections])
        return ""

    def _check_and_accept(self, candidate: dict[str, Any], contract: dict[str, Any], text: str, previous: list[dict[str, Any]], base: dict[str, Any]) -> None:
        folder = f"chapters/{candidate['number']:02d}/v{candidate['version']:02d}"
        for attempt in range(2):
            self.progress(82, f"通用长篇：第{candidate['number']}章计划实绩对比与验收")
            local = chapter_issues(text, contract, self.state["outline"], previous)
            audit = self.gateway.json("acceptance", {**base, "candidate": text, "local_issues": local}, max_tokens=12000, slot=f"chapter-{candidate['number']}-attempt-{attempt}")
            issues = local + validate_audit(audit, contract, self.state["sources"]) + (audit.get("issues") if isinstance(audit.get("issues"), list) else [])
            candidate.update(audit=audit, issues=issues)
            atomic_json(self.directory / folder / f"acceptance_{self.request['id']}_{attempt}.json", {"content_sha256": sha256(text.encode()).hexdigest(), "audit": audit, "local_issues": local, "issues": issues})
            self.save()
            if not blocking(issues):
                break
            if attempt == 1:
                candidate["status"] = "needs_review"
                self.save()
                raise GeneralLongformReview("本章高/严重问题仍未消除，未写入验收状态：" + "；".join(str(item.get("description", "")) for item in issues if isinstance(item, dict))[:1800])
            self.progress(85, f"通用长篇：第{candidate['number']}章局部修复，随后重新验收")
            text = self.gateway.call("repair", {**base, "candidate": text, "issues": issues}, max_tokens=26000, slot=f"chapter-{candidate['number']}-qa-repair")["text"].strip()
            candidate["repairedContent"] = self.put(f"{folder}/qa_repair.txt", text)
            self.save()
        self.checkpoint()
        digest = sha256(text.encode()).hexdigest()
        accepted = {**deepcopy(candidate), "status": "accepted", "acceptedContent": self.put(f"{folder}/accepted.txt", text),
                    "content_sha256": digest, "state_source_sha256": digest, "state_persisted": True,
                    "word_count": documents.word_count(text), "accepted_at": now_iso()}
        number = str(candidate["number"])
        slot = self.state["chapters"][number]
        if slot.get("accepted"):
            slot["history"].append(slot["accepted"])
        slot["accepted"] = accepted
        slot["candidate"] = None
        self.state["accepted_state"][number] = {"source": "acceptedContent", "content_sha256": digest,
            "summary": accepted["audit"]["summary"], "endingHook": accepted["audit"]["endingHook"],
            "terms": accepted["audit"].get("terms", {}), "cases": accepted["audit"].get("cases", []),
            "new_information": accepted["audit"]["new_information"], "used_source_ids": accepted["audit"]["used_source_ids"]}
        self.state["total_words"] = sum(item["accepted"]["word_count"] for item in self.state["chapters"].values() if item.get("accepted"))
        self.state["phase"] = "ready_for_assembly" if self.next_number() > len(self.state["outline"]["chapters"]) else "ready_for_chapter"
        self.save()
        self._chapter_export(accepted)
        self.progress(95, f"第{number}章已验收并持久化，已停止，等待下一章操作")

    def _chapter_export(self, accepted: dict[str, Any]) -> None:
        if accepted.get("exported"):
            return
        self.add_artifacts(documents.export_chapter(self.directory / "exports", self.state, accepted, self.content(accepted["acceptedContent"]), accepted=True))
        accepted["exported"] = True
        self.save()

    def assemble(self) -> None:
        if not self.state["confirmed"] or not self.state.get("outline"):
            raise GeneralLongformReview("先确认大纲并完成全部正文")
        accepted = self.accepted()
        count = len(self.state["outline"]["chapters"])
        if [item["number"] for item in accepted] != list(range(1, count + 1)):
            raise GeneralLongformReview("实际验收正文尚未覆盖全部章节，未用摘要合成全书")
        total = sum(documents.word_count(item["content"]) for item in accepted)
        if not 100000 <= total <= 110000:
            raise GeneralLongformReview(f"全书真实正文为{total}字，请局部调整至100000—110000字后合稿")
        issues = []
        for item, contract in zip(accepted, self.state["outline"]["chapters"]):
            issues.extend(chapter_issues(item["content"], contract, self.state["outline"], [old for old in accepted if old["number"] < item["number"]]))
        audit = self.gateway.json("final", self.context(chapters=accepted, actual_words=total, local_issues=issues), max_tokens=12000)
        if not isinstance(audit.get("issues"), list):
            raise GeneralLongformReview("全书终审未返回有效问题清单")
        issues += audit["issues"]
        self.state["final_audit"] = {**audit, "issues": issues, "actual_words": total, "checked_at": now_iso()}
        self.save()
        if blocking(issues):
            raise GeneralLongformReview("全书终审存在高/严重问题，请按报告局部修订对应章")
        self.state["total_words"] = total
        artifact = documents.export_book(self.directory / "exports", self.state, [(item, item["content"]) for item in accepted], operation_id=self.request["id"])
        self.state["book_version"] = artifact["version"]
        package = self.state["outline"]["packaging"][self.state["outline"]["recommended_index"]]
        source_rows = documents.bibliography([self.state["sources"][sid] for item in accepted for sid in item["audit"]["used_source_ids"]])
        markdown = "@最终书名\n" + package["title"] + "\n\n@最终简介\n" + package["description"] + "\n\n@目录\n" + "\n".join(f"第{c['number']}章 {c['title']}\n" + "\n".join(section["title"] for section in c["sections"]) for c in self.state["outline"]["chapters"]) + "\n\n@正文\n" + "\n\n".join(item["content"] for item in accepted) + "\n\n" + "\n\n".join(row[0] for row in source_rows)
        self.put("manuscript.md", markdown)
        self.add_artifacts([artifact])
        self.state.update(phase="completed", completed_at=now_iso())
        self.save()

    def import_materials(self) -> None:
        pending = [item for item in self.state["materials"] if not item.get("imported")]
        if not pending and self.request["id"] not in self.state.get("import_requests", []):
            raise GeneralLongformReview("请先上传材料")
        self.ensure_reference()
        if self.request["id"] not in self.state.setdefault("import_requests", []):
            self.state["import_requests"].append(self.request["id"])
            self.save()
        # Identify everything first, so upload order does not put chapter 2
        # ahead of its outline/chapter 1 or leave an editorial file unread.
        for material in pending:
            self.state["current_material_id"] = material["id"]
            text = self.content(material["text_path"])
            metadata = material.get("metadata") or {}
            role = material.get("role", "auto")
            if material.get("sha256") == self.state["prompt_source_sha256"]:
                material.update(role="instruction", imported=True, status="imported", error=None)
                self.save()
                continue
            if material.get("identified"):
                continue
            if metadata.get("kind") in {"outline", "chapter"} and role in {"auto", metadata["kind"]}:
                role = metadata["kind"]
                identified = metadata
            else:
                identified = self.gateway.json("materials", self.context(material={"role": role, "confirmed": material.get("confirmed", False),
                    "filename": material["filename"], "lines": [{"line": i, "text": line} for i, line in enumerate(text.splitlines(), 1)]}), max_tokens=24000, slot=f"identify-{material['id']}")
                role = identified.get("role", role) if role == "auto" else role
            material.update(role=role, identified=identified)
            self.save()
        order = {"source": 0, "outline": 1, "editorial": 2, "report": 2, "instruction": 2, "chapter": 3}
        def sort_key(item: dict[str, Any]) -> tuple[int, int]:
            data = item.get("identified", {})
            number = data.get("number") or next(iter(data.get("chapters") or [{}])).get("number") or 0
            return order.get(item["role"], 4), number if type(number) is int else 0
        for material in sorted((item for item in pending if not item.get("imported")), key=sort_key):
            self.state["current_material_id"] = material["id"]
            text = self.content(material["text_path"])
            identified, role = material["identified"], material["role"]
            if role in {"outline", "chapter"}:
                self.state.setdefault("import_continue_requests", [])
                if self.request["id"] not in self.state["import_continue_requests"]:
                    self.state["import_continue_requests"].append(self.request["id"])
            if role == "source":
                self.state["source_text"] = (self.state.get("source_text", "") + "\n\n" + text).strip()
            elif role == "outline":
                if self.state.get("imported_outline_id") == material["id"]:
                    self._outline_export(confirmed=self.state["confirmed"])
                    material.update(imported=True, status="imported", error=None)
                    self.save()
                    continue
                if self.state.get("outline") or self.accepted():
                    raise GeneralLongformReview("已有有效大纲；请走局部修订，避免导入覆盖当前版本")
                plan = identified.get("outline")
                if not isinstance(plan, dict):
                    raise GeneralLongformReview("材料没有可恢复的大纲结构，请补齐原大纲文件")
                plan = normalize_imported_headings(plan)
                confirmed = bool(material.get("confirmed") or identified.get("confirmed"))
                self.state.update(outline_candidate=plan, outline_candidate_material_id=material["id"], outline_candidate_confirmed=confirmed)
                self.save()
                # Research receipts in uploaded files are data, not authority.
                # Re-read their source URLs through a fresh real research step.
                self.ensure_source_summary()
                evidence = self.research(slot=f"import-outline-{material['id']}", imported_outline=plan, source_hints=identified.get("sources", {}))
                old_sources = identified.get("sources") or {}
                if isinstance(old_sources, dict):
                    for old_id, old in old_sources.items():
                        if not isinstance(old, dict):
                            continue
                        replacement = next((value for value in evidence["sources"].values() if canonical_url(value.get("url", "")) == canonical_url(old.get("url", ""))), None)
                        if replacement:
                            self.state["sources"][old_id] = {**replacement, "id": old_id}
                targets = [target for chapter in plan.get("chapters", []) for target in [chapter, *chapter.get("sections", [])]]
                if any(not valid_source_ids(target.get("source_ids"), self.state["sources"]) for target in targets):
                    bindings = self.gateway.json("import_bindings", self.context(outline=plan, research=evidence), max_tokens=16000, slot=material["id"])
                    if bindings.get("gaps") or not isinstance(bindings.get("chapters"), list) or len(bindings["chapters"]) != len(plan.get("chapters", [])):
                        raise GeneralLongformReview("导入大纲的研究依据仍有缺口，请补充材料后再继续")
                    for chapter, binding in zip(plan["chapters"], bindings["chapters"]):
                        if not isinstance(binding, dict) or binding.get("number") != chapter["number"] or not isinstance(binding.get("sections"), list) or [item.get("title") for item in binding["sections"] if isinstance(item, dict)] != [s["title"] for s in chapter["sections"]]:
                            raise GeneralLongformReview("大纲来源绑定改变了章/小节结构，未采用")
                        chapter["source_ids"] = binding.get("source_ids")
                        for section, bound_section in zip(chapter["sections"], binding["sections"]):
                            section["source_ids"] = bound_section.get("source_ids")
                errors = validate_outline(plan, self.state["sources"], confirmed=confirmed)
                if confirmed and plan.get("research_gaps"):
                    errors.append("确认大纲仍有待核实材料")
                if errors:
                    self.state["outline_candidate"] = plan
                    self.save()
                    raise GeneralLongformReview("导入大纲待补齐：" + "；".join(errors))
                self.state.update(outline=plan, outline_version=1, confirmed=confirmed, imported_outline_id=material["id"],
                                  imported_outline_confirmed=confirmed, outline_candidate=None,
                                  confirmed_at=now_iso() if confirmed else None,
                                  phase="ready_for_chapter" if confirmed else "awaiting_outline_confirmation")
                self.state.pop("outline_candidate_material_id", None)
                self.state.pop("outline_candidate_confirmed", None)
                self.save()
                self._outline_export(confirmed=confirmed)
            elif role == "chapter":
                if not self.state.get("outline") or not self.state["confirmed"]:
                    raise GeneralLongformReview("先导入或确认大纲，再导入真实已写正文")
                slices = identified.get("chapters") or [{"number": identified.get("number"), "title": identified.get("title"), "start_line": 1, "end_line": len(text.splitlines())}]
                if not isinstance(slices, list) or any(not isinstance(item, dict) for item in slices):
                    raise GeneralLongformReview("正文材料未识别出有效的章范围")
                for item in slices:
                    number = item.get("number")
                    slot = self.state["chapters"].get(str(number), {})
                    if slot.get("accepted", {}).get("import_material_id") == material["id"]:
                        self._chapter_export(slot["accepted"])
                        continue
                    if type(number) is not int or number != self.next_number() or number > len(self.state["outline"]["chapters"]):
                        raise GeneralLongformReview("导入章节必须连续，重复版本需走指定章修订")
                    lines = text.splitlines()
                    start, end = item.get("start_line"), item.get("end_line")
                    if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
                        raise GeneralLongformReview("材料正文行号范围不合法")
                    content = "\n".join(lines[start - 1:end]).strip()
                    contract = self.state["outline"]["chapters"][number - 1]
                    content = normalize_imported_chapter(content, contract, self.state["outline"]["handoff"].get("heading_aliases", []))
                    evidence = self.research(slot=f"chapter-{number}", chapter=contract)
                    if slot.get("candidate") and slot["candidate"].get("import_material_id") != material["id"]:
                        raise GeneralLongformReview("当前章已有候选版本，请先处理该版本再导入另一份")
                    candidate = {"number": number, "title": contract["title"], "version": 1, "status": "draft", "request_id": self.request["id"], "research": evidence,
                                 "import_material_id": material["id"], "research_request_id": self.request["id"], "sections": [], "issues": [],
                                 "draftContent": self.put(f"chapters/{number:02d}/v01/imported.txt", content)}
                    self.state["chapters"][str(number)] = {"history": [], "candidate": candidate}
                    self.save()
                    # Real partial prose is a continuation checkpoint, not an
                    # outline or an accepted chapter; never ask QA to invent it.
                    if documents.word_count(content) < contract["target_words"] * .97:
                        sections = split_sections(content)
                        if [name for name, _ in sections] != [s["title"] for s in contract["sections"][:len(sections)]] or not sections:
                            raise GeneralLongformReview("部分稿的小节位置不明确，请核对标题或上传完整的当前版本")
                        for index, (_, body) in enumerate(sections):
                            candidate["sections"].append({"index": index, "path": self.put(f"chapters/{number:02d}/v01/import_section_{index}.txt", body), "words": documents.word_count(body)})
                        material.setdefault("partial_chapters", []).append(number)
                        self.save()
                        continue
                    previous = self.accepted()
                    self._check_and_accept(candidate, contract, content, previous, self.context(chapter=contract, previous_chapters=previous, research=evidence))
            elif role in {"editorial", "report", "instruction"}:
                # Supplementary records can inform explicit revisions, but
                # never turn a summary or a detector report into accepted prose.
                material["notes"] = identified.get("notes", "配套资料已归档，不计入正文或完成章节")
                if role != "instruction":
                    material["approved_notes"] = identified.get("approved_notes") or material["notes"]
            else:
                raise GeneralLongformReview("材料角色不明确，请指定来源、大纲、正文或配套记录")
            material.update(role=role, imported=True, status="imported", error=None)
            self.save()
        self.state.pop("current_material_id", None)
        if not self.state.get("outline"):
            self.plan()
        elif self.state["confirmed"] and self.request["id"] in self.state.get("import_continue_requests", []):
            # This is the document's new-conversation handoff: recognize the
            # supplied progress, then produce exactly one next/partial chapter.
            bound = self.state.get("request_bindings", {}).get(self.request["id"])
            if bound or self.next_number() <= len(self.state["outline"]["chapters"]):
                self.generate_chapter()
            else:
                self.assemble()

    def run(self) -> dict[str, Any]:
        action = self.request.get("action")
        if action not in ACTIONS:
            raise GeneralLongformError("未支持的通用长篇动作")
        if self.state["prompt_source_sha256"] != get_manifest()["source_sha256"]:
            raise GeneralLongformReview("项目 prompt 版本与当前部署不一致，先核对版本再续写")
        if self.request["id"] in self.state["completed_requests"]:
            return self.state
        self.state["last_request"] = deepcopy(self.request)
        self.state["last_error"] = None
        try:
            self.save()
            if action == "plan":
                self.plan()
            elif action == "revise_outline":
                self.revise_outline()
            elif action == "confirm_outline":
                self.confirm()
            elif action == "next_chapter":
                self.generate_chapter()
            elif action == "revise_chapter":
                self.generate_chapter(revise=True)
            elif action == "assemble":
                self.assemble()
            elif action == "import_materials":
                self.import_materials()
        except GeneralLongformPaused as exc:
            # The task lease still belongs to this worker. Flush only durable
            # state, then release it through the task bridge, with no new calls.
            self.checkpoint = lambda: None
            self.state.update(phase="paused", last_error=None, pause_message=str(exc))
            self.save()
            self.export_partial()
            return self.state
        except Exception as exc:
            self.state.update(phase="needs_review", last_error=_error_text(exc))
            for material in self.state["materials"]:
                if material["id"] == self.state.get("current_material_id") and not material.get("imported"):
                    material.update(status="needs_review", error=_error_text(exc))
            self.save()
            self.export_partial()
            if isinstance(exc, GeneralLongformReview):
                return self.state
            raise
        self.state["completed_requests"].append(self.request["id"])
        self.save()
        return self.state

    def export_partial(self) -> None:
        for slot in self.state["chapters"].values():
            candidate = slot.get("candidate")
            if candidate and candidate.get("request_id") == self.request["id"] and not candidate.get("partial_export_request"):
                content = self._candidate_text(candidate)
                if content:
                    try:
                        if not candidate.get("draftContent"):
                            candidate["draftContent"] = self.put(f"chapters/{candidate['number']:02d}/v{candidate['version']:02d}/partial.txt", content)
                        self.add_artifacts(documents.export_chapter(self.directory / "exports", self.state, candidate, content, accepted=False))
                        candidate["partial_export_request"] = self.request["id"]
                    except (OSError, ValueError) as exc:
                        self.state["export_error"] = _error_text(exc)
                    self.save()


def public_state(state: dict[str, Any]) -> dict[str, Any]:
    """Metadata by default; raw uploaded material and call prompts stay private."""
    return {key: deepcopy(value) for key, value in state.items() if key not in {"source_text", "completed_requests"}} | {
        "materials": [{key: value for key, value in item.items() if key not in {"metadata", "identified", "text_path", "file_path"}} for item in state["materials"]],
        "can_resume": bool(state.get("last_request")) and state["last_request"]["id"] not in state["completed_requests"],
    }
