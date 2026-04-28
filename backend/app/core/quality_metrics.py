from __future__ import annotations

import re
from dataclasses import dataclass, field

ELLIPSIS_RE = r"(?:……|…|\.{3})"
SENTENCE_BREAKS = "。！？；\n"


@dataclass
class QualityReport:
    # ===== A1 单总猎杀清单（35 条字面禁词，分两组展示） =====
    a1_pattern_hits: dict[str, list[str]] = field(default_factory=dict)
    a1_fixed_hits: dict[str, list[str]] = field(default_factory=dict)

    # ===== A2 通用 AI 套话（30 条，按 6 类分组展示） =====
    a2_transition_hits: dict[str, list[str]] = field(default_factory=dict)
    a2_closing_hits: dict[str, list[str]] = field(default_factory=dict)
    a2_pseudo_deep_hits: dict[str, list[str]] = field(default_factory=dict)
    a2_empty_modifier_hits: dict[str, list[str]] = field(default_factory=dict)
    a2_chicken_soup_hits: dict[str, list[str]] = field(default_factory=dict)
    a2_cliche_metaphor_hits: dict[str, list[str]] = field(default_factory=dict)

    # ===== 颁奖词 / 神坛化（来自单总 9 项毛病第 5 条） =====
    award_word_hits: dict[str, list[str]] = field(default_factory=dict)

    # ===== 大模型残留口癖（来自文档 2 审校要求 + SCENE_CRITIC_PROMPT Part C2） =====
    llm_residual_hits: dict[str, list[str]] = field(default_factory=dict)

    # ===== 格式统计 =====
    half_width_punct_count: int = 0
    half_width_punct_samples: list[str] = field(default_factory=list)

    # ===== 排版加工符号统计 =====
    mark_double_excl: int = 0
    mark_excl_paren: int = 0
    mark_at: int = 0
    mark_tilde: int = 0
    mark_brackets: int = 0

    # ===== 文本统计 =====
    total_chars: int = 0
    total_paragraphs: int = 0
    avg_paragraph_length: float = 0.0

    # ===== 综合分（自动计算，构造时不传） =====
    total_violations: int = 0
    violation_density_per_kchar: float = 0.0


A1_PATTERN_RULES: dict[str, str] = {
    "不是X而是Y": r"不是.{1,30}?而是",
    "像是在X更像是Y": r"像是在.{1,30}?更像是",
    "常常会先被人...": r"常常会先被人.{0,20}",
    "真正使X进入Y": r"真正使.{1,20}?进入",
    "X长出来": rf".{{1,15}}{ELLIPSIS_RE}长出来",
    "X接住": rf".{{1,15}}{ELLIPSIS_RE}接住",
    "X压下来": rf".{{1,15}}{ELLIPSIS_RE}压下来",
    "往X压": rf"往.{{1,15}}{ELLIPSIS_RE}压",
    "把X送过去": rf"把.{{1,15}}{ELLIPSIS_RE}送过去",
    "如果只X分量就轻了": r"如果只.{1,30}?分量就轻了",
    "一步步把X拉直": r"一步步把.{1,20}?拉直",
    "把这条链条慢慢展开": r"把这条链条慢慢展开",
    "在X沉下去": rf"在.{{1,15}}{ELLIPSIS_RE}沉下去",
    "后来之所以总被反复提起": r"后来之所以总被反复提起",
}

A1_FIXED_STRINGS: list[str] = [
    "托住",
    "拧到了一起",
    "也就是说，",
    "把话说透",
    "把话说得再直白一点",
    "直白一点讲",
    "问题的关键",
    "得能滴出水",
    "我们的目标很直接",
    "再往深处看",
    "之所以值得写",
    "这个变化很能说明",
    "定得很死",
    "很有现实感的",
    "非常实际的",
    "非常硬的",
    "很硬的",
    "很多人一提到",
    "连起来",
    "往一起推",
    "会越来越顺",
]

A2_TRANSITION: dict[str, str] = {
    "随着社会的发展": "随着社会的发展",
    "随着时代的变迁": "随着时代的变迁",
    "随着X不断深入": r"re:随着[^。！？；\n]{1,15}不断深入",
    "在这个瞬息万变的时代": "在这个瞬息万变的时代",
    "在当今这个时代": "在当今这个时代",
    "在如今这个时代": "在如今这个时代",
    "与此同时(段首)": r"re:^与此同时",
    "让我们": "让我们",
    "让我们一起": "让我们一起",
    "让我们看": "让我们看",
    "让我们走进": "让我们走进",
    "接下来(段首)": r"re:^接下来",
    "接下去(段首)": r"re:^接下去",
}

A2_CLOSING: dict[str, str] = {
    "总而言之": "总而言之",
    "综上所述": "综上所述",
    "总的来说": "总的来说",
    "归根结底": "归根结底",
    "说到底": "说到底",
    "一言以蔽之": "一言以蔽之",
    "概而言之": "概而言之",
    "不难看出": "不难看出",
    "不难发现": "不难发现",
    "由此可见": "由此可见",
    "最终(段首)": r"re:^最终[，,、]",
    "最后(段首)": r"re:^最后[，,、]",
}

A2_PSEUDO_DEEP: dict[str, str] = {
    "与其说X不如说Y": r"re:与其说[^。！？；\n]{1,30}不如说",
    "看似X实则Y": r"re:看似[^。！？；\n]{1,30}实则",
    "既是X也是Y": r"re:既是[^。！？；\n]{1,20}也是",
    "表面上X本质上Y": r"re:表面上[^。！？；\n]{1,20}本质上",
}

A2_EMPTY_MODIFIER: dict[str, str] = {
    "显而易见": "显而易见",
    "不言而喻": "不言而喻",
    "众所周知": "众所周知",
    "不可否认": "不可否认",
    "毋庸置疑": "毋庸置疑",
    "无可争辩": "无可争辩",
    "值得深思": "值得深思",
    "引人深思": "引人深思",
    "发人深省": "发人深省",
    "在某种程度上": "在某种程度上",
    "在一定意义上": "在一定意义上",
    "从某种意义上": "从某种意义上",
    "一定程度上": "一定程度上",
    "某种程度上": "某种程度上",
}

A2_CHICKEN_SOUP: dict[str, str] = {
    "未来可期": "未来可期",
    "终将": "终将",
    "必将": "必将",
    "共勉": "共勉",
    "我们每个人": "我们每个人",
    "我们每一个人": "我们每一个人",
    "我们都": "我们都",
    "千万不要": "千万不要",
    "一定要": "一定要",
    "务必": "务必",
    "相信未来": "相信未来",
    "加油": "加油",
    "同行": "同行",
}

A2_CLICHE_METAPHOR: dict[str, str] = {
    "命运的齿轮": "命运的齿轮",
    "时代的洪流": "时代的洪流",
    "历史的车轮": "历史的车轮",
    "大潮汹涌": "大潮汹涌",
    "这一切的背后": "这一切的背后",
    "这背后": "这背后",
    "在这背后": "在这背后",
    "这一切都源于": "这一切都源于",
    "触及灵魂": "触及灵魂",
    "叩问灵魂": "叩问灵魂",
    "直击心灵": "直击心灵",
    "直抵人心": "直抵人心",
    "凤凰涅槃": "凤凰涅槃",
    "浴火重生": "浴火重生",
    "拨云见日": "拨云见日",
    "醍醐灌顶": "醍醐灌顶",
}

AWARD_WORDS: dict[str, str] = {
    "波澜壮阔": "波澜壮阔",
    "光辉历程": "光辉历程",
    "典范": "典范",
    "楷模": "楷模",
    "她的一生": "她的一生",
    "他的一生": "他的一生",
    "历史证明了": "历史证明了",
    "时代铭记了": "时代铭记了",
    "丰碑": "丰碑",
    "彪炳史册": "彪炳史册",
    "永垂不朽": "永垂不朽",
    "可歌可泣": "可歌可泣",
}

LLM_RESIDUAL_TOKENS: dict[str, str] = {
    "ChatGPT": "ChatGPT",
    "我是 AI": "我是 AI",
    "我是AI": "我是AI",
    "作为 AI 助手": "作为 AI 助手",
    "作为AI助手": "作为AI助手",
    "作为一个语言模型": "作为一个语言模型",
    "作为 Claude": "作为 Claude",
    "作为Claude": "作为Claude",
    "作为 GPT": "作为 GPT",
    "作为GPT": "作为GPT",
    "Claude 模型": "Claude 模型",
    "GPT 模型": "GPT 模型",
    "已按您的要求": "已按您的要求",
    "已经按您的要求": "已经按您的要求",
    "已按照您的要求": "已按照您的要求",
    "已经按照您的要求": "已经按照您的要求",
    "按照您的指示": "按照您的指示",
    "根据您的需求": "根据您的需求",
    "根据您的要求": "根据您的要求",
    "请随时告诉我": "请随时告诉我",
    "如果您还有需要": "如果您还有需要",
    "如果您还有": "如果您还有",
    "希望对您有帮助": "希望对您有帮助",
    "希望对你有帮助": "希望对你有帮助",
    "请您参考": "请您参考",
    "好的，下面是": "好的，下面是",
    "好的，以下是": "好的，以下是",
    "以下是本场景": "以下是本场景",
    "以下是为您": "以下是为您",
    "以下是为你": "以下是为你",
    "接下来我将": "接下来我将",
    "接下来我们来看": "接下来我们来看",
    "让我为您": "让我为您",
    "让我为你": "让我为你",
    "让我来": "让我来",
    "<audit>": "<audit>",
    "</audit>": "</audit>",
    "<final_text>": "<final_text>",
    "</final_text>": "</final_text>",
    "<thinking>": "<thinking>",
    "</thinking>": "</thinking>",
}


def _extract_containing_sentence(text: str, hit_start: int, hit_end: int) -> str:
    left = hit_start
    while left > 0 and text[left - 1] not in SENTENCE_BREAKS:
        left -= 1
        if hit_start - left > 200:
            break
    right = hit_end
    while right < len(text) and text[right] not in SENTENCE_BREAKS:
        right += 1
        if right - hit_end > 200:
            break
    return text[left:right].strip()


def _detect(text: str, pattern: str) -> list[str]:
    """统一检测：'re:' 开头走正则，否则走固定字串。返回命中所在整句，最多 10 条。"""
    sentences_with_hit: list[str] = []

    if pattern.startswith("re:"):
        try:
            regex = re.compile(pattern[3:], re.MULTILINE)
        except re.error:
            return []
        for match in regex.finditer(text):
            sentence = _extract_containing_sentence(text, match.start(), match.end())
            if sentence:
                sentences_with_hit.append(sentence)
            if len(sentences_with_hit) >= 10:
                break
    else:
        # 固定字串：用 str.find 循环
        start = 0
        while True:
            idx = text.find(pattern, start)
            if idx == -1:
                break
            sentence = _extract_containing_sentence(text, idx, idx + len(pattern))
            if sentence:
                sentences_with_hit.append(sentence)
            start = idx + len(pattern)
            if len(sentences_with_hit) >= 10:
                break

    return sentences_with_hit


def _detect_chicken_soup(text: str) -> dict[str, list[str]]:
    tail = text[-200:] if len(text) > 200 else text
    results: dict[str, list[str]] = {}
    for name, pattern in A2_CHICKEN_SOUP.items():
        hits = _detect(tail, pattern)
        if hits:
            results[name] = hits
    return results


def _detect_half_width_punct(text: str) -> tuple[int, list[str]]:
    banned = set(",.!?;:\\'\"`(){}[]<>")
    count = 0
    samples: list[str] = []
    for para in text.split("\n"):
        if not para.strip():
            continue
        chinese_chars = sum(1 for c in para if "\u4e00" <= c <= "\u9fff")
        if chinese_chars / len(para) < 0.6:
            continue
        for i, ch in enumerate(para):
            if ch not in banned:
                continue
            left_ch = para[i - 1] if i > 0 else ""
            right_ch = para[i + 1] if i + 1 < len(para) else ""
            if ch == "." and left_ch.isdigit() and right_ch.isdigit():
                continue
            if ch == "," and left_ch.isdigit() and right_ch.isdigit():
                continue
            count += 1
            if len(samples) < 5:
                samples.append(para[max(0, i - 15) : min(len(para), i + 15)])
    return count, samples


def _count_marks(text: str) -> dict[str, int]:
    return {
        "double_excl": len(re.findall(r"^！！", text, re.MULTILINE)),
        "excl_paren": len(re.findall(r"^！（", text, re.MULTILINE)),
        "at": len(re.findall(r"^@", text, re.MULTILINE)),
        "tilde": len(re.findall(r"^~", text, re.MULTILINE)),
        "brackets": len(re.findall(r"【[^】]+】", text)),
    }


def _calc_total(report: QualityReport) -> None:
    total = (
        sum(len(v) for v in report.a1_pattern_hits.values())
        + sum(len(v) for v in report.a1_fixed_hits.values())
        + sum(len(v) for v in report.a2_transition_hits.values())
        + sum(len(v) for v in report.a2_closing_hits.values())
        + sum(len(v) for v in report.a2_pseudo_deep_hits.values())
        + sum(len(v) for v in report.a2_empty_modifier_hits.values())
        + sum(len(v) for v in report.a2_chicken_soup_hits.values())
        + sum(len(v) for v in report.a2_cliche_metaphor_hits.values())
        + sum(len(v) for v in report.award_word_hits.values())
        + sum(len(v) for v in report.llm_residual_hits.values())
    )
    report.total_violations = total
    report.violation_density_per_kchar = total / max(report.total_chars, 1) * 1000


def measure_quality(markdown: str) -> QualityReport:
    report = QualityReport()
    report.total_chars = len(markdown)
    paragraphs = [p for p in markdown.split("\n") if p.strip()]
    report.total_paragraphs = len(paragraphs)
    if paragraphs:
        report.avg_paragraph_length = sum(len(p) for p in paragraphs) / len(paragraphs)

    for name, pattern in A1_PATTERN_RULES.items():
        hits = _detect(markdown, f"re:{pattern}")
        if hits:
            report.a1_pattern_hits[name] = hits

    for s in A1_FIXED_STRINGS:
        hits = _detect(markdown, s)
        if hits:
            report.a1_fixed_hits[s] = hits

    for name, pattern in A2_TRANSITION.items():
        hits = _detect(markdown, pattern)
        if hits:
            report.a2_transition_hits[name] = hits
    for name, pattern in A2_CLOSING.items():
        hits = _detect(markdown, pattern)
        if hits:
            report.a2_closing_hits[name] = hits
    for name, pattern in A2_PSEUDO_DEEP.items():
        hits = _detect(markdown, pattern)
        if hits:
            report.a2_pseudo_deep_hits[name] = hits
    for name, pattern in A2_EMPTY_MODIFIER.items():
        hits = _detect(markdown, pattern)
        if hits:
            report.a2_empty_modifier_hits[name] = hits
    for name, pattern in A2_CLICHE_METAPHOR.items():
        hits = _detect(markdown, pattern)
        if hits:
            report.a2_cliche_metaphor_hits[name] = hits

    report.a2_chicken_soup_hits = _detect_chicken_soup(markdown)

    for name, pattern in AWARD_WORDS.items():
        hits = _detect(markdown, pattern)
        if hits:
            report.award_word_hits[name] = hits

    for name, pattern in LLM_RESIDUAL_TOKENS.items():
        hits = _detect(markdown, pattern)
        if hits:
            report.llm_residual_hits[name] = hits

    report.half_width_punct_count, report.half_width_punct_samples = _detect_half_width_punct(markdown)

    marks = _count_marks(markdown)
    report.mark_double_excl = marks["double_excl"]
    report.mark_excl_paren = marks["excl_paren"]
    report.mark_at = marks["at"]
    report.mark_tilde = marks["tilde"]
    report.mark_brackets = marks["brackets"]

    _calc_total(report)
    return report


def format_report(report: QualityReport, *, top_k: int = 5) -> str:
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("                    质量度量报告")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"总字数:       {report.total_chars}")
    lines.append(f"段落数:       {report.total_paragraphs}")
    lines.append(f"平均段长:     {report.avg_paragraph_length:.1f} 字")
    lines.append("")
    lines.append(f"【综合】总违规: {report.total_violations} 处")
    lines.append(f"        违规密度: {report.violation_density_per_kchar:.2f} /千字")
    lines.append("")

    def _render_section(title: str, hits_dict: dict[str, list[str]], note: str = "") -> None:
        if not hits_dict:
            return
        total = sum(len(v) for v in hits_dict.values())
        lines.append(f"--- {title} ({total} 处) {note} ---")
        for name, samples in sorted(hits_dict.items(), key=lambda x: -len(x[1])):
            lines.append(f"  [{name}]  {len(samples)} 处")
            for s in samples[:top_k]:
                lines.append(f"      · {s[:80]}")
        lines.append("")

    _render_section("A1 句式型违规（高置信度）", report.a1_pattern_hits)
    _render_section("A1 固定字串违规（可能误伤，需人工核对）", report.a1_fixed_hits)
    _render_section("A2 塑料过渡句", report.a2_transition_hits)
    _render_section("A2 廉价收束词", report.a2_closing_hits)
    _render_section("A2 伪深刻平行句", report.a2_pseudo_deep_hits)
    _render_section("A2 空心修饰词", report.a2_empty_modifier_hits)
    _render_section("A2 鸡汤化收尾", report.a2_chicken_soup_hits, "(仅检测末尾 200 字)")
    _render_section("A2 套路化比喻", report.a2_cliche_metaphor_hits)
    _render_section("颁奖词 / 神坛化", report.award_word_hits)
    _render_section("大模型残留口癖 (任一命中即说明洗稿不彻底)", report.llm_residual_hits)

    lines.append("--- 排版加工符号使用统计 ---")
    lines.append(f"  ！！      : {report.mark_double_excl} 处")
    lines.append(f"  ！（）    : {report.mark_excl_paren} 处")
    lines.append(f"  @ 段首    : {report.mark_at} 处")
    lines.append(f"  ~ 段首    : {report.mark_tilde} 处")
    lines.append(f"  【】      : {report.mark_brackets} 处")
    lines.append("")
    lines.append("--- 标点 ---")
    lines.append(f"  半角标点违规: {report.half_width_punct_count} 处")
    if report.half_width_punct_samples:
        for sample in report.half_width_punct_samples:
            lines.append(f"      · {sample}")
    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)
