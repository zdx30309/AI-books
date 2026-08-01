"""Deterministic prose checks for the ordinary family long-novel pipeline.

These checks deliberately cover only mechanical defects that can be proved
locally.  They do not try to "improve" style by replacing words, because that
was one of the causes of damaged prose in earlier drafts.
"""
from __future__ import annotations

from collections import Counter
import re
from typing import Any


_ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\ufeff]")
_MODEL_NOISE_RE = re.compile(
    r"(?:^|\n)\s*(?:```|以下是(?:修改|润色|校对)后的|修改说明|校对说明|作为(?:AI|人工智能)|本章正文如下)",
    re.IGNORECASE,
)
_ASCII_QUOTE_RE = re.compile(r'(?<=[\u4e00-\u9fff，。！？；：、])\"|\"(?=[\u4e00-\u9fff])')
_QUOTE_RUN_RE = re.compile(r"(?:[\"'“”‘’「」『』]\s*){3,}")
_HARD_PUNCTUATION_RUN_RE = re.compile(r"[，。；：、]{2,}|[！？]{3,}|…{3,}")
_BAD_PUNCTUATION_ORDER_RE = re.compile(r"(?:，[。；：、]|。[，；：、]|；[，。；：、]|：[，。；：、])")
_QUOTE_END_PUNCTUATION_RE = re.compile(r"[”’」』][。！？]")
_BAD_PARAGRAPH_START_RE = re.compile(r"^[\s]*[，。；：、！？”’》」』）)]")
_LONG_SENTENCE_RE = re.compile(r"[^。！？\n]{181,}(?:[。！？]|$)")
_CRITICAL_ANCHOR_RE = re.compile(
    r"(?:人民币\s*)?\d+(?:\.\d+)?\s*(?:亿元|万元|元|块钱|块|岁|年|个月|月|日|号|天|小时|分钟|次|章|页|%|％)?"
)

_PAIR_RULES: tuple[tuple[str, str, str], ...] = (
    ("“", "”", "中文双引号"),
    ("‘", "’", "中文单引号"),
    ("「", "」", "直角引号"),
    ("『", "』", "双直角引号"),
    ("《", "》", "书名号"),
    ("（", "）", "中文圆括号"),
    ("(", ")", "英文圆括号"),
)


def normalize_family_novel_prose(text: str) -> str:
    """Apply only lossless typography cleanup; never replace prose words."""

    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\u00a0", " ").replace("\t", "    ")
    value = _ZERO_WIDTH_RE.sub("", value)
    value = "\n".join(line.rstrip() for line in value.split("\n"))
    value = re.sub(r"\n{4,}", "\n\n\n", value)
    return value.strip()


def _issue(
    issue_type: str,
    description: str,
    evidence: str,
    suggested_fix: str,
    *,
    severity: str = "high",
) -> dict[str, str]:
    return {
        "severity": severity,
        "issueType": issue_type,
        "description": description,
        "evidence": evidence[:240],
        "suggestedFix": suggested_fix,
    }


def inspect_family_novel_prose(text: str) -> list[dict[str, str]]:
    """Return only locally provable typography/readability defects."""

    value = normalize_family_novel_prose(text)
    issues: list[dict[str, str]] = []
    if not value:
        return [
            _issue("empty_chapter", "章节正文为空。", "", "恢复正文后重新校对。", severity="severe")
        ]
    if "\ufffd" in value:
        issues.append(
            _issue("encoding_damage", "正文含有编码替换符，文本可能已经损坏。", "�", "从原始模型输出恢复对应文字。", severity="severe")
        )
    noise = _MODEL_NOISE_RE.search(value)
    if noise:
        issues.append(
            _issue("model_process_noise", "正文混入模型说明或代码块标记。", noise.group(0), "删除说明，只保留小说正文。")
        )
    ascii_quote = _ASCII_QUOTE_RE.search(value)
    if ascii_quote:
        start = max(0, ascii_quote.start() - 30)
        issues.append(
            _issue("ascii_dialogue_quote", "中文对白使用了英文直引号。", value[start : ascii_quote.end() + 30], "按语义改为成对中文引号。")
        )

    for opening, closing, label in _PAIR_RULES:
        depth = 0
        unexpected_close = -1
        for index, char in enumerate(value):
            if char == opening:
                depth += 1
            elif char == closing:
                if depth <= 0:
                    unexpected_close = index
                    break
                depth -= 1
        if unexpected_close >= 0:
            start = max(0, unexpected_close - 35)
            issues.append(
                _issue(
                    "punctuation_pair_misordered",
                    f"{label}存在无对应开符号的闭符号。",
                    value[start : unexpected_close + 36],
                    "结合句意重配成对符号，不能机械删掉符号。",
                )
            )
        elif depth:
            issues.append(
                _issue(
                    "punctuation_pair_unclosed",
                    f"{label}未闭合。",
                    f"{opening} 比 {closing} 多 {depth} 个",
                    "结合对白边界补齐闭符号。",
                )
            )

    for pattern, issue_type, description in (
        (_QUOTE_RUN_RE, "quote_run", "正文出现连续堆叠的引号。"),
        (_HARD_PUNCTUATION_RUN_RE, "punctuation_run", "正文出现不合规范的连续标点。"),
        (_BAD_PUNCTUATION_ORDER_RE, "punctuation_order", "正文出现次序错误的标点组合。"),
        (_QUOTE_END_PUNCTUATION_RE, "dialogue_punctuation_outside_quote", "对白句末标点被放在闭引号外。"),
    ):
        match = pattern.search(value)
        if match:
            start = max(0, match.start() - 35)
            issues.append(
                _issue(issue_type, description, value[start : match.end() + 35], "按上下文语义重新断句并配置标点。")
            )

    for paragraph in value.split("\n"):
        if not paragraph.strip():
            continue
        match = _BAD_PARAGRAPH_START_RE.search(paragraph)
        if match:
            issues.append(
                _issue("stray_punctuation", "段首出现没有归属的闭合或停顿标点。", paragraph[:120], "核对上一句和本段句意后重新断句。")
            )
            break

    long_sentence = _LONG_SENTENCE_RE.search(value)
    if long_sentence:
        issues.append(
            _issue(
                "overlong_unpunctuated_sentence",
                "存在超过180字仍没有句末停顿的长句，容易形成错位和病句。",
                long_sentence.group(0)[:220],
                "按动作、说话人和语义边界拆句，不删减事实。",
            )
        )
    return issues


def inspect_editorial_candidate(original: str, candidate: str) -> list[dict[str, str]]:
    """Protect facts and chapter size while proofreading a full chapter."""

    original_value = normalize_family_novel_prose(original)
    candidate_value = normalize_family_novel_prose(candidate)
    issues = inspect_family_novel_prose(candidate_value)
    if original_value:
        ratio = len(candidate_value) / max(1, len(original_value))
        if ratio < 0.90 or ratio > 1.12:
            issues.append(
                _issue(
                    "editorial_length_drift",
                    "校对稿篇幅偏离原稿超过允许范围。",
                    f"length_ratio={ratio:.3f}",
                    "恢复被删内容或撤回新增情节，只做必要校对与局部人物刻画。",
                )
            )
        original_anchors = Counter(_CRITICAL_ANCHOR_RE.findall(original_value))
        candidate_anchors = Counter(_CRITICAL_ANCHOR_RE.findall(candidate_value))
        if original_anchors != candidate_anchors:
            missing = list((original_anchors - candidate_anchors).elements())[:8]
            added = list((candidate_anchors - original_anchors).elements())[:8]
            issues.append(
                _issue(
                    "critical_anchor_changed",
                    "校对稿改变了数字、金额、日期或数量锚点。",
                    f"missing={missing}; added={added}",
                    "逐项恢复原稿中的数字、金额、日期和数量表达。",
                    severity="severe",
                )
            )
    return issues


def compact_issue_payload(issues: list[dict[str, Any]], *, limit: int = 12) -> list[dict[str, str]]:
    """Bound issue text before placing it in a repair prompt."""

    result: list[dict[str, str]] = []
    for item in issues[:limit]:
        result.append(
            {
                "severity": str(item.get("severity") or "high")[:16],
                "issueType": str(item.get("issueType") or "prose_quality")[:64],
                "description": str(item.get("description") or "")[:300],
                "evidence": str(item.get("evidence") or "")[:300],
                "suggestedFix": str(item.get("suggestedFix") or "")[:300],
            }
        )
    return result
