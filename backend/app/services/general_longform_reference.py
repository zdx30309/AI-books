"""Versioned reference-role and evidence validation; no prompt text in logic."""
from __future__ import annotations

import json
import re
from typing import Any

from app.prompts.general_longform_prompts import get_bundle, get_manifest
from app.services.general_longform_runtime import GeneralLongformReview


def contrastive(version: str) -> bool:
    return get_manifest(version).get("reference_policy", {}).get("mode") == "contrastive"


def _short(value: Any, limit: int = 800) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _text_values(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _text_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _text_values(item)


def approve_reference(data: dict[str, Any], version: str) -> dict[str, Any]:
    """Keep the legacy schema; require role-bound abstract mechanisms for v02."""
    manifest = get_manifest(version)
    cards, summary = data.get("ReferencePatternCards"), data.get("ApprovedEditingSummary")
    if set(data) != {"ReferencePatternCards", "ApprovedEditingSummary"} or not isinstance(cards, list) or not cards or not isinstance(summary, dict) or not summary:
        raise GeneralLongformReview("参考机制抽象不完整")
    allowed = {"id", "function", "mechanism", "avoid"}
    if contrastive(version):
        allowed |= {"source_role", "source_section_id"}
    if len(cards) > 12 or any(not isinstance(card, dict) or set(card) - allowed or not card.get("mechanism") for card in cards):
        raise GeneralLongformReview("参考卡包含未批准字段或缺少抽象机制")
    if contrastive(version):
        roles = {example[key]: example["role"] for example in manifest["reference_examples"] for key in ("sample_section_id", "report_section_id")}
        ids = set()
        for card in cards:
            if set(card) != allowed or any(not _short(card.get(key)) for key in allowed):
                raise GeneralLongformReview("正反参考卡字段缺失、为空或过长")
            if card["id"] in ids:
                raise GeneralLongformReview("正反参考卡 id 重复")
            ids.add(card["id"])
            if card["source_role"] not in {"positive", "negative"} or roles.get(card["source_section_id"]) != card["source_role"]:
                raise GeneralLongformReview("正反样稿角色与来源分段不一致，已阻止颠倒")
        if {card["source_role"] for card in cards} != {"positive", "negative"}:
            raise GeneralLongformReview("参考抽象须同时覆盖正面方法与反面诊断")
        _validate_summary(summary, cards)
    serialized = json.dumps(data, ensure_ascii=False)
    raw = "\n".join((get_bundle(version) / item["file"]).read_text(encoding="utf-8") for item in manifest["sections"] if item["role"] == "reference_only")
    # Check text values, not JSON-escaped transport: quotes/newlines must not
    # hide a copied span from the raw-reference boundary check.
    raw_compact = re.sub(r"\s", "", raw)
    values = [re.sub(r"\s", "", value) for value in _text_values(data)]
    if any(value[index:index + 32] in raw_compact for value in values for index in range(max(0, len(value) - 31))):
        raise GeneralLongformReview("参考抽象混入原文长片段，已阻止进入生成上下文")
    if any(example["title"] in serialized for example in manifest.get("reference_examples", [])):
        raise GeneralLongformReview("参考摘要夹带样稿书名，须保留跨主题机制而非单书锚点")
    return {**data, "source_sha256": manifest["source_sha256"], "prompt_version": version,
            "rawTextIncluded": False, "generated_card_count": len(cards)}


def _validate_summary(summary: dict[str, Any], cards: list[dict[str, str]]) -> None:
    text_keys = {"pacing", "continuity", "anti_repetition", "evidence_boundaries"}
    fields = text_keys | {"positive_methods", "negative_diagnostics", "ordinary_word_policy", "report_scope", "sentence_examples_override_roles"}
    if set(summary) != fields or any(not _short(summary.get(key)) for key in text_keys):
        raise GeneralLongformReview("正反编辑摘要的结构或抽象说明不完整")
    for key, role, label in (("positive_methods", "positive", "method"), ("negative_diagnostics", "negative", "diagnostic")):
        entries = summary.get(key)
        expected = {card["id"] for card in cards if card["source_role"] == role}
        if not isinstance(entries, list) or not entries or any(not isinstance(item, dict) or set(item) != {"card_id", label} or not _short(item.get("card_id")) or not _short(item.get(label)) for item in entries):
            raise GeneralLongformReview("正面方法或反面诊断缺少可追溯的卡片说明")
        if {item["card_id"] for item in entries} != expected or len(entries) != len(expected):
            raise GeneralLongformReview("编辑摘要的正反卡片引用缺失、重复或角色颠倒")
    policy = summary.get("ordinary_word_policy")
    if not isinstance(policy, dict) or set(policy) != {"mechanical_ban", "evaluation_basis"} or policy.get("mechanical_ban") is not False or not _short(policy.get("evaluation_basis")):
        raise GeneralLongformReview("常用词须按语境评估，摘要不得引入机械禁词")
    if summary.get("report_scope") != "attachment_samples_only" or summary.get("sentence_examples_override_roles") is not False:
        raise GeneralLongformReview("历史报告只对应样稿；单句经验不得推翻整篇正反角色")


def validate_cached_reference(reference: dict[str, Any], version: str) -> None:
    allowed = {"ReferencePatternCards", "ApprovedEditingSummary", "source_sha256", "prompt_version", "rawTextIncluded", "generated_card_count"}
    if set(reference) - allowed or reference.get("source_sha256") != get_manifest(version)["source_sha256"] or reference.get("prompt_version", version) != version or reference.get("rawTextIncluded") is not False:
        raise GeneralLongformReview("参考摘要的固定版本或原文隔离标记不一致")
    approved = approve_reference({key: reference.get(key) for key in ("ReferencePatternCards", "ApprovedEditingSummary")}, version)
    if reference.get("generated_card_count") != approved["generated_card_count"]:
        raise GeneralLongformReview("参考摘要的卡片数量与记录不一致")


def style_issues(audit: dict[str, Any], reference: dict[str, Any], candidates: dict[int, str], version: str) -> list[dict[str, str]]:
    """Ground v02 review in actual candidate text, not historical sample scores."""
    if not contrastive(version):
        return []
    issues: list[dict[str, str]] = []

    def add(description: str) -> None:
        issues.append({"severity": "high", "type": "style", "description": description})

    review = audit.get("style_review")
    if not isinstance(review, dict):
        add("缺少正反方法风格复核 style_review")
        return issues
    cards = {card["id"]: card for card in reference.get("ReferencePatternCards", [])}
    bodies = {number: "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#")) for number, text in candidates.items()}

    def evidence_number(item: dict[str, Any]) -> int | None:
        number = item.get("chapter_number", next(iter(bodies)) if len(bodies) == 1 else None)
        quote = item.get("evidence")
        if type(number) is not int or number not in bodies or not _short(quote, 240) or len(quote.strip()) < 8 or quote.strip() not in bodies[number]:
            add("风格复核证据须为对应章实际正文中的 8—240 字符短片段，不接受标题或虚构引文")
            return None
        if not _short(item.get("reason")):
            add("风格复核须说明片段承担的信息任务或句子功能")
        return number

    for key, role in (("positive_application", "positive"), ("negative_checks", "negative")):
        entries = review.get(key)
        if not isinstance(entries, list) or not entries or len(entries) > 12 * len(bodies):
            add("风格复核须同时提供正面方法应用与反面问题排查")
            continue
        covered = set()
        for item in entries:
            if not isinstance(item, dict):
                add("风格复核条目结构不正确")
                continue
            card_id = item.get("card_id")
            card = cards.get(card_id) if isinstance(card_id, str) else None
            if not card or card.get("source_role") != role:
                add("风格复核使用了未知卡片或颠倒的正反角色")
            number = evidence_number(item)
            if number is not None:
                covered.add(number)
            if role == "negative" and item.get("finding") != "clear":
                add("反面诊断仍有问题或结论缺失，须局部修复后重新验收：" + str(item.get("reason", ""))[:300])
        if covered != set(bodies):
            add("正反风格复核未以真实证据覆盖全部待验收章节")
    policy = review.get("ordinary_word_review")
    if not isinstance(policy, dict) or policy.get("mechanical_ban_applied") is not False or not _short(policy.get("reason")):
        add("普通词复核应按语境判断并明确未采用机械禁词")
    else:
        entries = policy.get("examples")
        if not isinstance(entries, list) or not entries or len(entries) > 12 * len(bodies) or any(not isinstance(item, dict) for item in entries):
            add("普通词语境复核缺少实际正文证据")
        elif {evidence_number(item) for item in entries} != set(bodies):
            add("普通词语境复核未覆盖全部待验收章节")
    if review.get("historical_reports_used_as_current_result") is not False:
        add("历史样稿检测报告不应充作当前书稿结果或通过率承诺")
    return issues
