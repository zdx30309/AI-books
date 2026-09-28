"""Deterministic structure fingerprinting for the general_short category.

The v09 spec requires the writer to run a "structure mapping check" against the
inspiration transcript, but the check had no defined object because the section
that defined it (appendix six) was trimmed out of the writer prompt.  This module
builds that object locally, so the requirement has a concrete target and costs
no provider call.

The fingerprint is deliberately abstract: it records the *shape* of the source
(argument units, case order, narrative spine, keyword surface, domain guess) and
never copies a source sentence into the generation context.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re

#: Sentences per argument unit when merging a transcript.  800—1500 source
#: characters per unit is the range the audit spec uses for spoken material.
TARGET_UNIT_CHARS = 1000
MIN_UNITS = 6
MAX_UNITS = 80

_SENTENCE_END = "。！？；!?;"
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|倍|万|亿|元|美元|年|月|天|小时|分钟|人|次|例|项|家|个)")
_CAUSE_RE = re.compile(r"(?:因为|由于|所以|因此|导致|造成|使得|于是|从而|结果)")
_QUOTE_RE = re.compile(r"[“”\"『』《》]")
_TOPIC_STOPWORDS = frozenset(
    "的 了 是 在 和 与 及 或 而 也 就 都 很 更 最 一个 一种 这个 那个 我们 你们 他们 它 "
    "可以 需要 应该 必须 因为 所以 但是 如果 这样 那样 什么 怎么 为什么 时候 问题 事情 "
    "进行 通过 对于 关于 以及 并且 而且 不过 其实 可能 已经 还是 没有 不是 就是 这样 那样".split()
)

_DOMAIN_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("小白理财", ("理财", "基金", "储蓄", "资产", "投资", "收益", "保险", "负债", "记账", "养老金", "房贷")),
    ("女性成长", ("女性", "妈妈", "全职", "婚育", "独立", "第二曲线", "年纪", "退休", "她", "妻子")),
    ("医疗健康与体重管理", ("健康", "减重", "控糖", "睡眠", "肠道", "血压", "血糖", "医生", "营养", "运动", "抗衰")),
    ("两性关系与婚育", ("恋爱", "婚姻", "夫妻", "分手", "离婚", "出轨", "相亲", "结婚", "伴侣", "感情")),
    ("情绪心理与高情商沟通", ("焦虑", "内耗", "情绪", "心理", "沟通", "敏感", "社恐", "讨好", "抑郁", "边界感")),
    ("认知成长与效能提升", ("认知", "思维", "学习", "自律", "习惯", "专注", "效率", "决策", "拖延", "注意力")),
    ("生活方式与人生选择", ("生活", "选择", "旅行", "城市", "断舍离", "极简", "独居", "爱好", "人生")),
    ("职场成长与职业突围", ("职场", "公司", "同事", "老板", "晋升", "跳槽", "面试", "工作", "薪酬", "行业")),
    ("人性洞察与传统智慧", ("人性", "老子", "论语", "庄子", "古人", "智慧", "处世", "曾国藩", "历史", "佛经")),
)


@dataclass
class ArgumentUnit:
    index: int
    unit: str
    chars: int


@dataclass
class StructureFingerprint:
    core_topic: str = ""
    argument_units: list[ArgumentUnit] = field(default_factory=list)
    case_sequence: list[str] = field(default_factory=list)
    opening_device: str = ""
    causal_chain: str = ""
    ending_function: str = ""
    keyword_fingerprint: list[str] = field(default_factory=list)
    domain_guess: str = ""
    source_chars: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def render(self) -> str:
        """Render the fingerprint as compact text for the writer prompt."""
        lines = [
            f"- 原稿去空白字数：{self.source_chars}",
            f"- 所属领域（猜测）：{self.domain_guess or '未判定'}",
            f"- 开头切入方式：{self.opening_device or '未识别'}",
            f"- 因果推进链：{self.causal_chain or '未识别'}",
            f"- 结尾功能：{self.ending_function or '未识别'}",
        ]
        if self.keyword_fingerprint:
            lines.append("- 标志性说法： " + "、".join(self.keyword_fingerprint))
        if self.case_sequence:
            lines.append("- 案例推进次序（严禁沿用此顺序）： " + " → ".join(self.case_sequence))
        lines.append("- 论点单元序列（严禁逐条对应；每章核心问题不得落在同一单元上）：")
        for item in self.argument_units:
            lines.append(f"  {item.index}. {item.unit}")
        return "\n".join(lines)


def _split_sentences(text: str) -> list[str]:
    if not text:
        return []
    parts = re.split(rf"(?<=[{re.escape(_SENTENCE_END)}])", text)
    return [part.strip() for part in parts if part.strip()]


def _first_sentence(text: str, limit: int = 200) -> str:
    for sentence in _split_sentences(text):
        return sentence[:limit]
    return text.strip()[:limit]


def _normalize_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _digits_count(text: str) -> int:
    return len(_NUMBER_RE.findall(text))


def _summarize_unit(chunk: str, index: int) -> ArgumentUnit:
    """Summarize a merged transcript chunk as a structural function, not a quote."""
    sentences = _split_sentences(chunk)
    head = sentences[0] if sentences else chunk
    tail = sentences[-1] if len(sentences) > 1 else ""
    cue: list[str] = []
    if _digits_count(chunk) >= 2:
        cue.append("含可量化数据")
    if _CAUSE_RE.search(chunk):
        cue.append("含因果解释")
    if re.search(r"(?:比如|例如|举个例子|以.+为例|有个)", chunk):
        cue.append("含举例")
    if _QUOTE_RE.search(chunk):
        cue.append("含引述")
    if re.search(r"(?:但是|然而|不过|其实|并非|并不是)", chunk):
        cue.append("含转折/纠偏")
    if re.search(r"(?:建议|应该|可以试试|第一步|方法)", chunk):
        cue.append("含方法建议")
    label = "、".join(cue) if cue else "陈述性推进"
    summary = f"{label}：{_normalize_line(head)[:60]}"
    if tail and tail[:20] not in summary:
        summary += f" … 收束于“{_normalize_line(tail)[:40]}”"
    return ArgumentUnit(index=index, unit=summary[:200], chars=len(chunk))


def _merge_units(text: str) -> list[ArgumentUnit]:
    sentences = _split_sentences(text) or [_normalize_line(text)]
    units: list[ArgumentUnit] = []
    buffer: list[str] = []
    size = 0
    for sentence in sentences:
        buffer.append(sentence)
        size += len(sentence)
        if size >= TARGET_UNIT_CHARS:
            units.append(_summarize_unit("".join(buffer), len(units) + 1))
            buffer, size = [], 0
    if buffer:
        # Fold a small tail into the previous unit rather than creating a stub.
        if units and size < TARGET_UNIT_CHARS * 0.4:
            merged = units[-1]
            extra = _summarize_unit("".join(buffer), merged.index)
            merged.unit = f"{merged.unit}；补充：{extra.unit.split('：', 1)[-1]}"[:240]
            merged.chars += size
        else:
            units.append(_summarize_unit("".join(buffer), len(units) + 1))
    if len(units) > MAX_UNITS:
        units = units[:MAX_UNITS]
    return units


def _case_sequence(text: str, limit: int = 12) -> list[str]:
    """Extract ordered case markers without inventing or reordering them."""
    seen: list[str] = []
    markers = "比如|例如|再比如|举个例子|举例来说|又以|以|像|再看|先看|先说|接下来看|说到"
    # An explicit case marker followed by a short noun-like token.
    explicit = re.compile(
        rf"(?:{markers})([\u4e00-\u9fffA-Za-z0-9]{{2,12}}?)(?:为例|来说|为例证|为例来看|这件事|这个故事)",
    )
    # Named institutions, places and organisations that carry a case on their own.
    named = re.compile(
        r"([\u4e00-\u9fff]{2,8}(?:公司|集团|银行|大学|医院|研究院|政府|城市|国家|地区|协会|平台))"
    )
    # A bare "比如X。" style marker, kept short and cut at punctuation.
    bare = re.compile(rf"(?:{markers})([\u4e00-\u9fffA-Za-z0-9]{{2,10}}?)(?=[，。；、！？\s])")
    for pattern in (explicit, named, bare):
        for match in pattern.finditer(text):
            token = match.group(1).strip()
            if 2 <= len(token) <= 12 and token not in seen:
                seen.append(token)
            if len(seen) >= limit:
                return seen
    seen.sort(key=lambda token: text.index(token) if token in text else len(text))
    return seen[:limit]


def _keyword_fingerprint(text: str, limit: int = 15) -> list[str]:
    """Surface distinctive terms; stops at the source's own vocabulary."""
    quoted = [q for q in re.findall(r"[“\"『]([^”\"』]{2,18})[”\"』]", text)]
    counts: dict[str, int] = {}
    for token in re.findall(r"[\u4e00-\u9fff]{3,8}", text):
        if token in _TOPIC_STOPWORDS:
            continue
        counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0])))
    keywords: list[str] = []
    for token in quoted:
        if token not in keywords:
            keywords.append(token)
    for token, count in ranked:
        if len(keywords) >= limit:
            break
        if count >= 2 and token not in keywords:
            keywords.append(token)
    return keywords[:limit]


def guess_domain(text: str) -> str:
    scores: list[tuple[int, str]] = []
    for name, hints in _DOMAIN_HINTS:
        score = sum(text.count(hint) for hint in hints)
        scores.append((score, name))
    scores.sort(reverse=True)
    return scores[0][1] if scores and scores[0][0] > 0 else ""


def build_fingerprint(source_text: str) -> StructureFingerprint:
    """Build the structure fingerprint from an inspiration transcript."""
    text = _normalize_line(source_text or "")
    if not text:
        return StructureFingerprint()
    sentences = _split_sentences(text)
    opening = _first_sentence(text, 160)
    opening_device = "未识别"
    if re.search(r"[？?]", opening):
        opening_device = "设问切入"
    elif _digits_count(opening):
        opening_device = "数据/事实切入"
    elif re.search(r"(?:我|你|他|她|有个|一位)", opening):
        opening_device = "人物或场景切入"
    else:
        opening_device = "陈述性切入"

    causal = " → ".join(
        sorted(
            {match.group(0) for match in re.finditer(r"(?:因为|由于|所以|因此|导致|造成|使得|于是|从而)", text)},
            key=text.find,
        )
    )
    tail = "".join(sentences[-2:]) if sentences else text
    if re.search(r"(?:建议|行动|可以试着|第一步)", tail):
        ending_function = "含行动建议"
    elif re.search(r"(?:希望|愿|未来|终将)", tail):
        ending_function = "升华式收束"
    elif re.search(r"(?:问题|仍|尚待|留给)", tail):
        ending_function = "开放式收束"
    else:
        ending_function = "回扣式收束"

    units = _merge_units(text)
    if not units:
        units = [_summarize_unit(text, 1)]

    return StructureFingerprint(
        core_topic=_first_sentence(text, 80),
        argument_units=units,
        case_sequence=_case_sequence(text),
        opening_device=opening_device,
        causal_chain=causal,
        ending_function=ending_function,
        keyword_fingerprint=_keyword_fingerprint(text),
        domain_guess=guess_domain(text),
        source_chars=len(text),
    )


def fingerprint_json(source_text: str) -> str:
    return json.dumps(build_fingerprint(source_text).to_dict(), ensure_ascii=False, indent=2)
