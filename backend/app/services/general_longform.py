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
import os
from pathlib import Path
import re
from typing import Any, Callable
from uuid import UUID

from app.prompts.general_longform_prompts import CATEGORY, available_versions, get_manifest
from app.services import general_longform_documents as documents
from app.services.general_longform_reference import approve_reference, contrastive, style_issues, validate_cached_reference
from app.services.general_longform_runtime import (
    GeneralLongformError, GeneralLongformPaused, GeneralLongformReview, ModelGateway,
    _error_text, atomic_json, atomic_text, canonical_url, now_iso, read_json, research,
)

ROOT = Path(__file__).resolve().parents[3]
DOMAINS = {"小白理财", "女性成长", "医疗健康与体重管理", "两性关系与婚育", "情绪心理与高情商沟通",
           "认知成长与效能提升", "生活方式与人生选择", "职场成长与职业突围", "人性洞察与传统智慧"}
ACTIONS = {"plan", "repair_outline_candidate", "revise_outline", "revise_outline_local", "repair_outline_revision", "confirm_outline", "next_chapter", "revise_chapter", "assemble", "import_materials", "set_auto_run", "set_source_policy"}
# Default, editable requirement used by ``revise_outline_local``: remove what no
# verified source supports instead of inventing support for it.
_LOCAL_REVISION_FEEDBACK = ("请只做一件事：把章节计划里没有已核验来源支撑的具体数据、结论或承诺删掉，"
                            "改成现有已核验来源能够支撑的表述，并清空 research_gaps；其余结构与书名方案保持原样。")

# A first outline that only fails local structure checks is recovered with the
# existing outline_revision stage, so one format slip cannot strand a project in
# a dead end. The validator output is the data passed as feedback; no prompt
# asset, stage list or acceptance rule changes, and the repaired outline still
# has to pass exactly the same validation.
_OUTLINE_REPAIR_FEEDBACK_LEAD = "自动结构校验未通过，请只按下述问题做最小必要修正，其余内容保持原样、不要完整重写："
# These cannot be produced by a local patch, so do not spend a call on them.
_OUTLINE_UNPATCHABLE_ERRORS = ("大纲不是结构化对象", "全书需要12—15章")
_TOP_LEVEL_PATCH_FIELDS = ("topic", "domain", "audience", "central_question", "scope_exclusions", "voice",
                            "packaging", "recommended_index", "recommendation", "target_words", "handoff", "research_gaps")
_CHAPTER_PATCH_FIELDS = ("title", "target_words", "question", "new_information", "previous_link", "next_link",
                         "do_not_repeat", "source_ids", "sections")
_REVISION_SCHEMA_REMINDER = (
    "上一次补丁没有通过结构校验。请只输出这样的 JSON："
    '{"chapter_patches":[{"number":章号,"changes":{...}}],"top_level_changes":{...},"reason":"修改理由"}；'
    "top_level_changes 里只允许出现这些字段：" + "、".join(_TOP_LEVEL_PATCH_FIELDS) + "；"
    "chapter_patches[].changes 里只允许出现这些字段：" + "、".join(_CHAPTER_PATCH_FIELDS) + "；"
    "不要附带其它顶层键，不要返回整本书结构，不要新增或删除章号。仍然只解决下面的问题："
)


def outline_repair_attempts() -> int:
    """Bounded auto-repair budget for one invalid first outline (default 1)."""
    raw = os.environ.get("GENERAL_LONGFORM_OUTLINE_REPAIR_ATTEMPTS", "1")
    try:
        return max(0, min(3, int(str(raw).strip() or "0")))
    except (TypeError, ValueError):
        return 1


def work_dir(task_id: UUID | str) -> Path:
    return ROOT / "backend" / "output" / f"task_{UUID(str(task_id))}" / CATEGORY


def initial_state(project_id: str, source_text: str = "", *, version: str | None = None) -> dict[str, Any]:
    manifest = get_manifest(version)
    return {"schema_version": 1, "project_id": str(UUID(project_id)), "category": CATEGORY,
            "prompt_version": manifest["version"], "prompt_source_sha256": manifest["source_sha256"],
            "phase": "needs_outline", "source_text": source_text, "outline": None, "source_policy": "verified",
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


SOURCE_POLICIES = {"verified", "search_trusted"}


def trusts_search(state: dict[str, Any]) -> bool:
    """Whether the author chose to write from the live search as-is.

    ``verified`` keeps the original rule: an outline resting on material no
    readable source supports cannot be confirmed. ``search_trusted`` is the
    author's explicit decision that search results are enough for this book, so
    the same outline can be confirmed and written. The unverified points stay
    recorded either way; this only stops them from blocking the author.
    """
    return str(state.get("source_policy") or "verified") == "search_trusted"


# 「来源覆盖不足」类问题只说明已读来源没有覆盖某个说法，并不指出正文与已读来源冲突
# 或编造。作者选择 search_trusted 时，这类问题按已记录的豁免处理：保留在验收记录里，
# 但不再把整章钉死在 needs_review。默认的 verified 策略下这段逻辑不生效。
COVERAGE_ONLY_MARKERS = (
    "没有完整列出", "未完整列出", "没有完整说明", "未完整说明", "没有列出", "未列出",
    "没有来自当前实际读取来源", "没有来自当前来源", "未由成功读取的页面", "未被成功读取",
    "本轮实际读取", "本次实际读取", "实际读取的", "没有实际搜索或引用工具证据",
    "research.gaps", "研究缺口", "未能核实", "无法核实", "未取得原文", "未读取", "没有读取",
    "没有可读全文", "未覆盖", "没有覆盖", "而未覆盖", "不在本轮", "未包含", "没有包含",
    "未提供", "没有提供", "尚未由", "只确认", "未说明", "没有说明", "未展开",
)
CONTRADICTION_MARKERS = (
    "不符", "不一致", "冲突", "矛盾", "相反", "编造", "不实", "事实错误", "存在错误",
    "张冠李戴", "与来源相悖", "来源中不存在", "文不对题",
)


# 同一件事模型每轮会换措辞（「未完整列出」→「未成功读取」→「尚未取得原文」），
# 逐条穷举的清单永远会漏。这里把「否定 + 读取/检索/取得/覆盖/提供/支持」这一族统一
# 识别出来，措辞变了也不会再阻断一轮；真正的冲突、编造、事实错误先由
# CONTRADICTION_MARKERS 拦下，不受这里影响。
COVERAGE_NEGATION = re.compile(
    r"(?:未曾|尚未|仍未|暂未|均未|也未|还没|没能|未能|不能|无法|未|没有)"
    r"[^。；;\n]{0,8}?(?:成功)?"
    r"(?:读取|取得|获取|取回|检索|搜到|搜索到|抓到|拿到|读到|找到|核实|核对|覆盖|包含|提供|列出|说明|展开|支持|佐证)"
)


def coverage_limited_fact(issue: Any) -> bool:
    """True when a fact issue only reports a gap in what the sources we read cover."""
    if not isinstance(issue, dict) or str(issue.get("type") or "") != "fact":
        return False
    if str(issue.get("severity") or "").lower() not in {"high", "severe", "blocking"}:
        return False
    description = str(issue.get("description") or "")
    if any(marker in description for marker in CONTRADICTION_MARKERS):
        return False
    if any(marker in description for marker in COVERAGE_ONLY_MARKERS):
        return True
    return bool(COVERAGE_NEGATION.search(description))


# 验收回复自身不自洽时的信号：模型给的证据不在本章正文里、引用了未知卡片、或
# Planned-vs-Actual 与本章对不上。这类问题要重问验收，而不是拿正文去当替罪羊。
AUDIT_DEFECT_STYLE_MARKERS = (
    "风格复核证据须为", "风格复核使用了未知卡片", "风格复核条目结构不正确",
    "缺少正反方法风格复核", "风格复核须同时提供", "正反风格复核未以真实证据覆盖",
    "普通词复核应", "普通词语境复核缺少", "普通词语境复核未覆盖", "历史样稿检测报告不应",
)
AUDIT_REASK_LIMIT = 1


def audit_defect_only(issues: list[dict[str, Any]]) -> bool:
    """True when every blocking issue is a defect of the audit reply, not of the prose."""
    blocking_items = [item for item in issues
                      if not (isinstance(item, dict) and str(item.get("severity") or "").lower() in {"low", "medium"})]
    if not blocking_items:
        return False
    for item in blocking_items:
        if not isinstance(item, dict):
            return False
        kind, description = str(item.get("type") or ""), str(item.get("description") or "")
        if kind == "acceptance":
            continue
        if kind == "style" and any(marker in description for marker in AUDIT_DEFECT_STYLE_MARKERS):
            continue
        return False
    return True


def apply_source_policy(issues: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep the chapter gate consistent with the source policy the author already chose."""
    if not trusts_search(state):
        return issues
    waived_notes = [str(item.get("description") or "") for item in issues if coverage_limited_fact(item)]
    resolved: list[dict[str, Any]] = []
    for issue in issues:
        reason = ""
        if coverage_limited_fact(issue):
            reason = ("作者已选择直接采用联网检索结果；本条只说明已读来源覆盖不足，"
                      "没有指出正文与已读来源冲突或编造，按已记录豁免保留为 medium。")
        elif (isinstance(issue, dict) and str(issue.get("type") or "") == "acceptance" and issue.get("section")
              and any(str(issue.get("section")) in note for note in waived_notes)):
            # The section is only short of the same source coverage that is
            # already waived for this book; it is recorded, never hidden, and it
            # no longer re-flags whichever section the auditor picks each round.
            reason = ("作者已选择直接采用联网检索结果；该小节的计划缺口正是已记录、已豁免的来源覆盖不足，"
                      "不是正文没写，按同一条豁免保留为 medium。")
        if reason:
            resolved.append({**issue, "severity": "medium", "downgraded_by": "search_trusted", "downgraded_reason": reason})
        else:
            resolved.append(issue)
    return resolved


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
    if not isinstance(patch, dict):
        raise GeneralLongformReview("大纲补丁必须是 JSON 对象")
    result = deepcopy(outline)
    allowed = {"topic", "domain", "audience", "central_question", "scope_exclusions", "voice", "packaging",
               "recommended_index", "recommendation", "target_words", "handoff", "research_gaps"}
    changes = patch.get("top_level_changes", {})
    if not isinstance(changes, dict):
        # A model sometimes puts the whole book structure here. Drop the
        # unusable shape instead of failing the round; a patch with no usable
        # content at all is still refused below.
        changes = {}
    unknown = set(changes) - allowed
    if unknown:
        # 模型偶尔会把整本书结构塞进 top_level_changes。这里只丢弃不认识/不允许的字段，
        # 保留认识的部分；结果仍要过 validate_outline 并等用户确认，不降低任何验收门。
        changes = {key: value for key, value in changes.items() if key in allowed}
    patches = patch.get("chapter_patches") or []
    if not changes and not (isinstance(patches, list) and patches):
        # Nothing usable arrived (unknown keys only, or an unusable shape).
        # Refuse loudly instead of reporting a revision that changed nothing.
        raise GeneralLongformReview("大纲补丁包含未允许的全局结构改写，且没有可用的章节补丁")
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
    structured = isinstance(diffs, list) and [item.get("section") for item in diffs if isinstance(item, dict)] == names
    if not structured or any(not isinstance(item, dict) or not item.get("evidence") for item in diffs):
        add("Planned-vs-Actual Diff 缺失或存在未完成任务")
    else:
        for item in diffs:
            if item.get("fulfilled") is not True:
                errors.append({"severity": "high", "type": "acceptance", "section": str(item.get("section")),
                               "description": f"Planned-vs-Actual：小节「{item.get('section')}」的计划任务未落实"})
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


# The generation context carries the book, not the bookkeeping. Prose,
# summaries, hooks and citations stay complete; per-chapter QA reports, tool
# hashes and journal paths are dropped because no stage prompt uses them and a
# thirteen-chapter book otherwise spends its window on records.
_GENERATION_CHAPTER_FIELDS = ("number", "title", "word_count", "content")
_GENERATION_SOURCE_FIELDS = ("id", "url", "title", "authors", "year", "publication", "doi", "bibliography",
                             "approved_summary", "supported_claims", "limitations")


def generation_chapters(chapters: Any) -> list[dict[str, Any]]:
    """Return the writing view of previous chapters: full text plus the continuity fields."""

    result = []
    for item in chapters or []:
        if not isinstance(item, dict):
            continue
        audit = item.get("audit") if isinstance(item.get("audit"), dict) else {}
        result.append({**{key: item.get(key) for key in _GENERATION_CHAPTER_FIELDS},
                       "summary": audit.get("summary"), "endingHook": audit.get("endingHook")})
    return result


def generation_sources(sources: Any) -> dict[str, Any]:
    """Return the citation view of the book's sources, without tool records."""

    if not isinstance(sources, dict):
        return sources
    return {key: ({field: value.get(field) for field in _GENERATION_SOURCE_FIELDS}
                  if isinstance(value, dict) else value) for key, value in sources.items()}


class GeneralLongformRunner:
    def __init__(self, directory: Path, state: dict[str, Any], request: dict[str, Any], *,
                 gateway: ModelGateway | None = None, checkpoint: Callable[[], None] | None = None,
                 progress: Callable[[int, str], None] | None = None):
        self.directory, self.state, self.request = directory, state, request
        self.checkpoint = checkpoint or (lambda: None)
        self.progress = progress or (lambda _number, _text: None)
        self.gateway = gateway or ModelGateway(directory, str(UUID(request["id"])), check_active=self.checkpoint,
                                               retry_uncertain=bool(request.get("retry_uncertain")), prompt_version=state.get("prompt_version"))
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
        payload = {"current_time": self.request.get("created_at") or self.state.get("created_at"), "project_id": self.state["project_id"],
                   "prompt_version": self.state["prompt_version"],
                   "source_summary": self.state.get("source_summary", ""), "outline": self.state.get("outline"),
                   "accepted_state": self.state["accepted_state"], "reference": self.state.get("reference", {}),
                   "sources": generation_sources(self.state["sources"]), **values}
        if "previous_chapters" in payload:
            # A projection, never a different book: every previous chapter keeps
            # its complete prose, title, summary and ending hook. Only the
            # per-chapter bookkeeping (QA reports, tool hashes, journals) is left
            # out, so the writer window is spent on the book instead of on
            # records it cannot use. Thirteen chapters of that bookkeeping used
            # to push a late chapter over the context guard and stop the run.
            payload["previous_chapters"] = generation_chapters(payload["previous_chapters"])
        return payload

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
            validate_cached_reference(self.state["reference"], self.state["prompt_version"])
            return
        version = self.state["prompt_version"]
        manifest = get_manifest(version)
        self.progress(5, "通用长篇：按固定角色抽象内置参考，不复制样稿")
        data = self.gateway.json("reference_analysis", {"purpose": "只抽象编辑机制", "prompt_version": version,
            "reference_examples": manifest.get("reference_examples", []), "reference_policy": manifest.get("reference_policy", {})},
            max_tokens=10000 if contrastive(version) else 6000)
        self.state["reference"] = approve_reference(data, version)
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
            # Keep the paid candidate as the breakpoint, then try the documented
            # bounded local repair before reporting a blocking item.
            plan, errors = self.repair_outline(plan, errors, evidence)
        if errors:
            self.state["outline_candidate"] = plan
            raise GeneralLongformReview("；".join(errors))
        self.adopt_first_outline(plan)

    def adopt_first_outline(self, plan: dict[str, Any]) -> None:
        """Publish the first outline version and stop for user confirmation."""
        self.state.update(outline=plan, outline_version=1, confirmed=False, phase="awaiting_outline_confirmation",
                          outline_candidate=None, planned_request_id=self.request["id"])
        self.save()
        self._outline_export(confirmed=False)

    def repair_outline_candidate(self) -> None:
        """Finish a blocked first outline without paying for a second research round.

        Only evidence this book already verified is reused, and the repaired
        outline still has to pass exactly the same ``validate_outline`` gate. A
        candidate without stored evidence is sent to the documented
        ``revise_outline`` path instead, so nothing is silently degraded.
        """
        if self.state.get("candidate_repair_request_id") == self.request["id"]:
            if self.state.get("outline"):
                self._outline_export(confirmed=False)
            return
        if self.state.get("outline"):
            raise GeneralLongformReview("已有大纲；请使用局部修订或确认大纲")
        plan = self.state.get("outline_candidate")
        if not isinstance(plan, dict):
            raise GeneralLongformReview("没有可修复的大纲候选；请先生成或导入大纲")
        stored = self.state.get("last_research")
        if not isinstance(stored, dict) or not stored.get("web_call_completed") or not self.state.get("sources"):
            raise GeneralLongformReview("本项目没有可直接复用的检索证据；请使用大纲局部修订")
        errors = validate_outline(plan, self.state["sources"])
        if errors:
            evidence = {**stored, "sources": self.state["sources"]}
            plan, errors = self.repair_outline(plan, errors, evidence)
        if errors:
            self.state["outline_candidate"] = plan
            raise GeneralLongformReview("；".join(errors))
        self.state["candidate_repair_request_id"] = self.request["id"]
        self.adopt_first_outline(plan)

    def repair_outline(self, plan: Any, errors: list[str], evidence: dict[str, Any]) -> tuple[Any, list[str]]:
        """Locally repair a structurally invalid first outline, keeping the gate.

        Reuses the existing ``outline_revision`` stage and the evidence already
        fetched for this plan request, so nothing is re-searched and no prompt
        asset changes. If the repaired outline still fails validation, the
        repaired version and its remaining errors are returned so the caller
        records the most advanced candidate; rejections and blocked patches
        return the previous plan and errors untouched.
        """
        attempts = outline_repair_attempts()
        if not attempts or not isinstance(plan, dict) or any(error.startswith(_OUTLINE_UNPATCHABLE_ERRORS) for error in errors):
            return plan, errors
        feedback = _OUTLINE_REPAIR_FEEDBACK_LEAD + "；".join(errors)
        for attempt in range(1, attempts + 1):
            self.progress(20, f"通用长篇：大纲结构校验未通过，正在按校验问题做第{attempt}次局部修复")
            audit = {"attempt": attempt, "errors_before": list(errors), "previous": plan}
            try:
                patch = self.gateway.json(
                    "outline_revision",
                    self.context(outline=plan, feedback=feedback, research=evidence),
                    max_tokens=22000, slot=f"outline-repair-{attempt}")
                updated = apply_outline_patch(plan, patch, accepted_numbers=set())
                repaired = validate_outline(updated, self.state["sources"])
            except GeneralLongformReview as exc:
                errors = [_error_text(exc)]
                atomic_json(self.directory / "planning" / f"outline_repair_{attempt}.json",
                            {**audit, "adopted": False, "reason": errors[0]})
                if attempt < attempts:
                    feedback = _OUTLINE_REPAIR_FEEDBACK_LEAD + "；".join(errors) + "\n" + _REVISION_SCHEMA_REMINDER
                    continue
                return plan, errors
            atomic_json(self.directory / "planning" / f"outline_repair_{attempt}.json",
                        {**audit, "adopted": not repaired, "patch": patch, "result": updated, "errors_after": repaired})
            plan, errors = updated, repaired
            if not errors:
                break
            feedback = _OUTLINE_REPAIR_FEEDBACK_LEAD + "；".join(errors)
        return plan, errors

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
        # Keep this revision's paid evidence: a malformed patch is a shape
        # problem, not a research problem, so it can be retried without paying
        # for the same search twice.
        self.state["outline_revision_context"] = {"feedback": feedback, "evidence": evidence,
                                                  "outline_version": self.state["outline_version"]}
        self.save()
        self._apply_outline_revision(plan, feedback, evidence, idempotency_key="revised_outline_request_id")

    def revise_outline_local(self) -> None:
        """Revise the outline with evidence this book already paid for.

        Used for blockers that are local: a chapter plan resting on content no
        verified source supports. It reuses the stored research result and the
        already verified sources, so it never re-runs the paid web search, and it
        still has to pass exactly the same ``validate_outline`` gate and the same
        user confirmation as any other revision. When the outline carries
        ``research_gaps`` this revision is only accepted once they are gone, so a
        blocked confirmation cannot come back unchanged.
        """
        if self.state.get("revised_outline_local_request_id") == self.request["id"]:
            self._outline_export(confirmed=False)
            return
        plan = self.state.get("outline") or self.state.get("outline_candidate")
        if not isinstance(plan, dict):
            raise GeneralLongformReview("先生成或导入大纲")
        stored = self.state.get("last_research")
        if not isinstance(stored, dict) or not stored.get("web_call_completed") or not self.state.get("sources"):
            raise GeneralLongformReview("本项目没有可直接复用的检索证据；请改用「按我的要求修订大纲」（会重新核查来源）")
        feedback = str(self.request.get("feedback") or "").strip() or _LOCAL_REVISION_FEEDBACK
        evidence = {**stored, "sources": self.state["sources"]}
        self.state["outline_revision_context"] = {"feedback": feedback, "evidence": evidence,
                                                  "outline_version": self.state["outline_version"]}
        self.save()
        self._apply_outline_revision(plan, feedback, evidence,
                                     idempotency_key="revised_outline_local_request_id",
                                     require_no_gaps=bool(plan.get("research_gaps")) and not trusts_search(self.state))

    def set_auto_run(self) -> None:
        """Turn automatic continuous running on/off for this book (no model call)."""
        self.state["auto_run"] = bool(self.request.get("enabled", True))
        self.state["auto_run_changed_at"] = now_iso()

    def set_source_policy(self) -> None:
        """Choose how this book treats material the live search could not open (no model call).

        ``verified`` is the default. ``search_trusted`` records that the author
        accepts what the search returned without a second verification pass, so
        the outstanding points stop blocking confirmation. Nothing is spent and
        nothing is deleted: the point list stays on the outline for the record.
        """
        policy = str(self.request.get("policy") or "").strip()
        if policy not in SOURCE_POLICIES:
            raise GeneralLongformReview("来源策略只能是 verified 或 search_trusted")
        gaps = (self.state.get("outline") or {}).get("research_gaps")
        gaps = [str(item) for item in gaps if str(item).strip()] if isinstance(gaps, list) else []
        self.state["source_policy"] = policy
        self.state["source_policy_changed_at"] = now_iso()
        if policy == "search_trusted":
            self.state["source_policy_note"] = ("已按作者选择直接采用联网检索结果，不再要求二次核实；"
                                                "本书记录了 " + str(len(gaps)) + " 条未取得原文的要点，仅作留痕。")
        else:
            self.state["source_policy_note"] = "已恢复二次核实门：大纲仍有未核实要点时不能确认。"

    def repair_outline_revision(self) -> None:
        """Retry one outline revision against the evidence already paid for.

        A model returning an unusable patch is a shape problem, not a research
        problem, so this reuses the stored outline_revision_context (same
        outline version, same feedback, same verified evidence) and only re-runs
        the outline_revision generation. The result still has to pass exactly
        the same validate_outline gate and still waits for user confirmation.
        """
        if self.state.get("outline_revision_repair_request_id") == self.request["id"]:
            if self.state.get("outline"):
                self._outline_export(confirmed=False)
            return
        context = self.state.get("outline_revision_context")
        if not isinstance(context, dict) or not isinstance(context.get("evidence"), dict) or not context["evidence"]:
            raise GeneralLongformReview("没有可复用的上次修订检索；请改用「修订并导出新版大纲」")
        if context.get("outline_version") != self.state["outline_version"]:
            raise GeneralLongformReview("大纲版本已变化；请重新填写并提交修订要求")
        plan = self.state.get("outline") or self.state.get("outline_candidate")
        if not isinstance(plan, dict):
            raise GeneralLongformReview("先生成或导入大纲")
        feedback = str(context.get("feedback") or "").strip()
        if not feedback:
            raise GeneralLongformReview("上次的修订要求已丢失；请重新提交")
        self._apply_outline_revision(plan, feedback, context["evidence"],
                                     idempotency_key="outline_revision_repair_request_id")

    def _apply_outline_revision(self, plan: dict[str, Any], feedback: str, evidence: dict[str, Any], *,
                                idempotency_key: str, require_no_gaps: bool = False) -> None:
        accepted_numbers = {int(key) for key, slot in self.state["chapters"].items() if slot.get("accepted")}
        final_packaging = bool(self.state["confirmed"] or self.state.get("imported_outline_confirmed") or self.state.get("outline_candidate_confirmed"))
        patch = self.gateway.json("outline_revision", self.context(outline=plan, feedback=feedback, research=evidence), max_tokens=22000)
        updated, errors, patch = self._revise_until_valid(
            plan, patch, feedback, evidence, accepted_numbers=accepted_numbers, final_packaging=final_packaging,
            require_no_gaps=require_no_gaps,
        )
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
        self.state.pop("outline_revision_context", None)
        self.state.update(outline=updated, outline_version=version, confirmed=False, phase="awaiting_outline_confirmation", outline_candidate=None)
        self.state[idempotency_key] = self.request["id"]
        self.save()
        self._outline_export(confirmed=False)

    def _revise_until_valid(self, plan: dict[str, Any], patch: Any, feedback: str, evidence: dict[str, Any], *,
                            accepted_numbers: set[int], final_packaging: bool,
                            require_no_gaps: bool = False) -> tuple[dict[str, Any], list[str], Any]:
        """Apply an outline revision patch; retry a bounded number of times when the model returns an unusable shape.

        Reuses the research evidence already fetched for this request, so a retry only costs one
        ``outline_revision`` call and never re-runs the paid search. The repaired outline still has to
        pass exactly the same ``validate_outline`` gate and still waits for the user to confirm.
        """
        attempts = outline_repair_attempts()
        updated: dict[str, Any] | None = None
        errors: list[str] = []
        last_error = ""
        for attempt in range(0, max(1, attempts + 1)):
            updated, errors, last_error = None, [], ""
            try:
                updated = apply_outline_patch(plan, patch, accepted_numbers=accepted_numbers)
                errors = validate_outline(updated, self.state["sources"], confirmed=final_packaging)
                if require_no_gaps and updated.get("research_gaps"):
                    # A local revision asked to clear the gaps must actually clear
                    # them, otherwise the next confirmation would be blocked again
                    # by the very same rule.
                    errors = errors + ["待核实材料仍未清空（research_gaps 还剩 " +
                                       str(len(updated["research_gaps"])) + " 条）：没有已核验来源支撑的内容必须删除或改写"]
            except GeneralLongformReview as exc:
                updated, errors, last_error = None, [], _error_text(exc)
            if updated is not None and not errors and updated != plan:
                return updated, [], patch
            if updated is not None and not errors:
                # Silence is not success: a patch that changed nothing (empty
                # top_level_changes, dropped unknown keys, no chapter patches)
                # must not report a revision the user never got.
                errors = ["大纲补丁没有产生任何有效修改：顶层字段与章节补丁为空或都不可用；请把修改要求写得更具体"]
            atomic_json(self.directory / "planning" / f"outline_revision_attempt_{attempt}.json",
                        {"attempt": attempt, "patch": patch, "errors": errors, "last_error": last_error})
            if attempt >= attempts:
                break
            self.progress(18, "通用长篇：大纲补丁未通过结构校验，按允许字段重做一次局部修订")
            retry_feedback = feedback + "\n" + _REVISION_SCHEMA_REMINDER + ("；".join(errors) or last_error)
            patch = self.gateway.json(
                "outline_revision",
                self.context(outline=plan, feedback=retry_feedback, research=evidence),
                max_tokens=22000,
                slot=f"outline-revision-retry-{attempt + 1}",
            )
        if updated is None:
            raise GeneralLongformReview(last_error or "大纲补丁无法应用")
        return updated, errors, patch

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
        gaps = [str(item) for item in (updated.get("research_gaps") or []) if str(item).strip()]
        if gaps and not trusts_search(self.state):
            errors.append("策划仍有待核实材料，请补充来源或局部调整大纲后再确认")
        if errors:
            raise GeneralLongformReview("；".join(errors))
        if gaps:
            # The author accepted the live search as-is. Keep the waiver on the
            # record so the acceptance trail still shows what was not verified.
            self.state["gap_waiver"] = {"policy": "search_trusted", "count": len(gaps), "at": now_iso(),
                                        "request_id": self.request["id"], "gaps": [item[:300] for item in gaps]}
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
            text = self._restore_shortened_sections(text, original, contract, base, folder, candidate)
            if text != self.content(candidate["repairedContent"]):
                candidate["repairedContent"] = self.put(f"{folder}/revision_balanced.txt", text)
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

    def _acceptance_audit(self, context: dict[str, Any], *, slot: str) -> dict[str, Any]:
        """Read the acceptance verdict; an unusable payload never dead-ends the chapter.

        The first read always reuses the persisted paid payload. Only when that
        payload cannot be parsed at all does this make exactly one extra,
        explicitly labelled call for the same slot, so a malformed reply costs a
        retry instead of a whole chapter.
        """
        try:
            return self.gateway.json("acceptance", context, max_tokens=12000, slot=slot)
        except GeneralLongformReview as exc:
            if "JSON" not in str(exc):
                raise
            self.state.setdefault("json_retries", []).append({"slot": slot, "at": now_iso(), "reason": str(exc)})
            self.save()
            return self.gateway.json("acceptance", context, max_tokens=12000, slot=f"{slot}-json-retry")

    def _chapter_issues(self, audit: dict[str, Any], contract: dict[str, Any], text: str, previous: list[dict[str, Any]], number: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        local = chapter_issues(text, contract, self.state["outline"], previous)
        issues = local + validate_audit(audit, contract, self.state["sources"])
        issues += audit.get("issues") if isinstance(audit.get("issues"), list) else []
        issues += style_issues(audit, self.state["reference"], {number: text}, self.state["prompt_version"])
        return local, apply_source_policy(issues, self.state)

    def _audited_chapter(self, candidate: dict[str, Any], contract: dict[str, Any], text: str, previous: list[dict[str, Any]], base: dict[str, Any], *, attempt: int, slot_suffix: str = "") -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        """Get a verdict that is actually about this chapter.

        A reply whose own evidence does not come from the chapter under review is
        not a verdict on the prose, so it is re-asked (bounded) instead of
        triggering a paid repair of text nobody faulted.
        """
        slot = f"chapter-{candidate['number']}-attempt-{attempt}{slot_suffix}"
        verdict = None
        for index in range(AUDIT_REASK_LIMIT + 1):
            audit = self._acceptance_audit({**base, "candidate": text, "local_issues": chapter_issues(text, contract, self.state["outline"], previous)},
                                           slot=slot if not index else f"{slot}-audit-retry-{index}")
            local, issues = self._chapter_issues(audit, contract, text, previous, candidate["number"])
            verdict = (audit, local, issues)
            if not (blocking(issues) and audit_defect_only(issues)):
                return verdict
            self.state.setdefault("audit_reasks", []).append(
                {"slot": slot, "at": now_iso(),
                 "reason": "验收回复与本章正文对不上（证据/Planned-vs-Actual 自查不一致），已重问验收，未修改正文"})
            self.save()
        return verdict

    @staticmethod
    def _fit_extended_section(text: str, floor: int, allowed: int) -> str:
        """Drop whole trailing paragraphs while an extension cannot fit the cap."""

        paragraphs = text.split("\n\n")
        while len(paragraphs) > 1 and documents.word_count("\n\n".join(paragraphs)) > allowed:
            trimmed = paragraphs[:-1]
            if documents.word_count("\n\n".join(trimmed)) < floor:
                break
            paragraphs = trimmed
        return "\n\n".join(paragraphs)

    def _restore_shortened_sections(self, text: str, previous_text: str, contract: dict[str, Any],
                                    base: dict[str, Any], folder: str, candidate: dict[str, Any]) -> str:
        """Extend sections a whole-chapter repair shortened below its own floors.

        The repair reply rewrites the whole chapter, so it can satisfy the
        auditor's "too long" verdict and still push a section under the floor the
        gate enforces, or drop the chapter under the minimum that same gate
        enforces.  Re-auditing that text blocks the chapter and the author pays for
        another rewrite.  Instead the sections that were healthy before the reply
        are extended in place with bounded continuation calls, and the chapter is
        never allowed past its own length cap.
        """

        sections = contract["sections"]
        parts = split_sections(text)
        previous_parts = split_sections(previous_text or "")
        if len(parts) != len(sections) or len(previous_parts) != len(parts):
            return text
        outline_target = int(self.state.get("outline", {}).get("target_words") or 0)
        minimum = max(math.ceil(contract["target_words"] * .97),
                      math.ceil(contract["target_words"] * 100000 / outline_target) if outline_target else 0)
        cap = math.ceil(contract["target_words"] * 1.12)
        bodies = [body for _name, body in parts]
        words = [documents.word_count(body) for body in bodies]
        floors = [int(section["target_words"] * .8) for section in sections]
        total = documents.word_count(text)
        # Only sections this reply shortened are fair game: a shortfall that was
        # already there is the author's explicit revision, not a repair side effect.
        eligible = [index for index in range(len(sections))
                    if documents.word_count(previous_parts[index][1]) >= floors[index]]
        needed = [index for index in eligible if words[index] < floors[index]]
        # A chapter-level shortfall is only a repair side effect when the reply
        # actually lost the words; an already short chapter stays the author's call.
        chapter_shortfall_is_new = documents.word_count(previous_text or "") >= minimum
        if not needed and not (total < minimum and chapter_shortfall_is_new):
            return text
        pending = needed + [index for index in sorted(eligible, key=lambda item: words[item]) if index not in needed]
        tries: dict[int, int] = {}
        extended: list[dict[str, Any]] = []
        while pending:
            index = pending.pop(0)
            if words[index] >= floors[index] and total >= minimum:
                continue
            tries[index] = tries.get(index, 0) + 1
            if tries[index] > 2:
                candidate.setdefault("rebalance_notes", []).append({"section": sections[index]["title"], "action": "exhausted",
                                                                  "reason": "延伸两次仍未补足小节下限", "before": words[index]})
                continue
            section = sections[index]
            room = cap - (total - words[index])
            ask = max(600, min(floors[index] - words[index] + 300, room))
            context = {**base, "section": section, "current_chapter_sections": list(bodies)}
            output = self.gateway.call("continue", {**context, "partial_section": bodies[index], "continuation": True},
                                       max_tokens=min(24000, section["target_words"] * 3 + 3000, int(ask * 1.6)),
                                       slot=f"chapter-{candidate['number']}-section-{index}")["text"].strip()
            grown = bodies[index].rstrip() + "\n\n" + output
            if documents.word_count(grown) > room:
                grown = self._fit_extended_section(grown, floors[index], room)
            after = documents.word_count(grown)
            if after <= words[index] or after > room:
                candidate.setdefault("rebalance_notes", []).append({"section": section["title"], "action": "skipped",
                                                                  "reason": "延伸后超出本章篇幅上限",
                                                                  "before": words[index], "after": after})
                continue
            extended.append({"section": section["title"], "before": words[index], "after": after})
            total = total - words[index] + after
            words[index] = after
            bodies[index] = grown
            if words[index] < floors[index] or total < minimum:
                pending.append(index)
        if not extended:
            return text
        candidate["section_extensions"] = extended
        return self._assemble_sections(contract, bodies)

    def _check_and_accept(self, candidate: dict[str, Any], contract: dict[str, Any], text: str, previous: list[dict[str, Any]], base: dict[str, Any]) -> None:
        folder = f"chapters/{candidate['number']:02d}/v{candidate['version']:02d}"
        slot_suffix = ""
        for attempt in range(2):
            self.progress(82, f"通用长篇：第{candidate['number']}章计划实绩对比与验收")
            audit, local, issues = self._audited_chapter(candidate, contract, text, previous, base, attempt=attempt, slot_suffix=slot_suffix)
            downgraded = [{"severity": item.get("severity"), "type": item.get("type"), "description": item.get("description")}
                          for item in issues if isinstance(item, dict) and item.get("downgraded_by")]
            candidate.update(audit=audit, issues=issues)
            if downgraded:
                candidate["policy_downgrades"] = downgraded
            atomic_json(self.directory / folder / f"acceptance_{self.request['id']}_{attempt}.json", {"content_sha256": sha256(text.encode()).hexdigest(), "audit": audit, "local_issues": local, "issues": issues, "policy_downgrades": downgraded})
            self.save()
            if not blocking(issues):
                break
            if attempt == 1:
                candidate["status"] = "needs_review"
                self.save()
                raise GeneralLongformReview("本章高/严重问题仍未消除，未写入验收状态：" + "；".join(str(item.get("description", "")) for item in issues if isinstance(item, dict))[:1800])
            self.progress(85, f"通用长篇：第{candidate['number']}章局部修复，随后重新验收")
            previous_text = text
            text = self.gateway.call("repair", {**base, "candidate": text, "issues": issues}, max_tokens=26000, slot=f"chapter-{candidate['number']}-qa-repair")["text"].strip()
            candidate["repairedContent"] = self.put(f"{folder}/qa_repair.txt", text)
            balanced = self._restore_shortened_sections(text, previous_text, contract, base, folder, candidate)
            if balanced != text:
                candidate["repairedContent"] = self.put(f"{folder}/qa_repair_balanced.txt", balanced)
                # The persisted verdict for this attempt was paid for against the
                # text the repair returned.  The rebalanced chapter is a different
                # manuscript, so it is judged under its own slot instead of
                # inheriting a conclusion about prose nobody is looking at now.
                slot_suffix = "-rebalanced"
            text = balanced
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
        self.ensure_reference()
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
            issues.extend(style_issues(item["audit"], self.state["reference"], {item["number"]: item["content"]}, self.state["prompt_version"]))
        audit = self.gateway.json("final", self.context(chapters=accepted, actual_words=total, local_issues=issues), max_tokens=12000)
        if not isinstance(audit.get("issues"), list):
            raise GeneralLongformReview("全书终审未返回有效问题清单")
        issues += audit["issues"]
        issues += style_issues(audit, self.state["reference"], {item["number"]: item["content"] for item in accepted}, self.state["prompt_version"])
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
            instruction_version = next((version for version in available_versions() if material.get("sha256") == get_manifest(version)["source_sha256"]), None)
            if instruction_version:
                material.update(role="instruction", instruction_version=instruction_version, imported=True, status="imported", error=None)
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
                if confirmed and plan.get("research_gaps") and not trusts_search(self.state):
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
        if self.request["id"] in self.state["completed_requests"]:
            return self.state
        self.state["last_request"] = deepcopy(self.request)
        self.state["last_error"] = None
        try:
            try:
                manifest = get_manifest(self.state["prompt_version"])
            except (KeyError, ValueError, RuntimeError) as exc:
                raise GeneralLongformReview(f"项目固定提示词版本核验失败：{exc}") from exc
            if self.state["prompt_source_sha256"] != manifest["source_sha256"]:
                raise GeneralLongformReview("项目 prompt 源文件哈希与其固定版本不一致，已保留断点")
            self.save()
            if action == "plan":
                self.plan()
            elif action == "repair_outline_candidate":
                self.repair_outline_candidate()
            elif action == "revise_outline":
                self.revise_outline()
            elif action == "revise_outline_local":
                self.revise_outline_local()
            elif action == "set_auto_run":
                self.set_auto_run()
            elif action == "set_source_policy":
                self.set_source_policy()
            elif action == "repair_outline_revision":
                self.repair_outline_revision()
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
