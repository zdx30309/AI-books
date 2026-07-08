from __future__ import annotations

import re
from dataclasses import dataclass, field

ELLIPSIS_RE = r"(?:…+|\.{3,})"
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

    # ===== 排版污染 =====
    format_noise_hits: dict[str, list[str]] = field(default_factory=dict)

    # ===== 终稿硬伤闸门（高置信度，命中后不应直接交付） =====
    final_hard_error_hits: dict[str, list[str]] = field(default_factory=dict)

    # ===== 名作解读专项文风污染 =====
    masterpiece_colloquial_hits: dict[str, list[str]] = field(default_factory=dict)

    # ===== 财经科技专项来源穿帮 =====
    finance_source_leak_hits: dict[str, list[str]] = field(default_factory=dict)

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
    "X长出来": rf".{{1,15}}(?:{ELLIPSIS_RE})?长出来",
    "X接住": rf".{{1,15}}(?:{ELLIPSIS_RE})?接住",
    "X压下来": rf".{{1,15}}(?:{ELLIPSIS_RE})?压下来",
    "往X压": rf"往.{{1,15}}(?:{ELLIPSIS_RE})?压",
    "把X送过去": rf"把.{{1,15}}(?:{ELLIPSIS_RE})?送过去",
    "如果只X分量就轻了": r"如果只.{1,30}?分量就轻了",
    "一步步把X拉直": r"一步步把.{1,20}?拉直",
    "把这条链条慢慢展开": r"把这条链条慢慢展开",
    "在X沉下去": rf"在.{{1,15}}(?:{ELLIPSIS_RE})?沉下去",
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

MASTERPIECE_COLLOQUIAL_RULES: dict[str, str] = {
    "这事儿": "这事儿",
    "那事儿": "那事儿",
    "一点儿": "一点儿",
    "有点儿": "有点儿",
    "那味儿": "那味儿",
    "这味儿": "这味儿",
    "咱们": "咱们",
    "没啥": "没啥",
    "啥": "啥",
    "说白了": "说白了",
    "说实话": "说实话",
    "老实说": "老实说",
    "坦白说": "坦白说",
    "其实吧": "其实吧",
    "你看": "你看",
    "对吧": "对吧",
    "是不是": "是不是",
    "挺...的": r"re:挺[^。！？；，,\n]{1,16}的",
    "聊一聊": "聊一聊",
}
MASTERPIECE_PROTECTED_PAIRS = {"“": "”", "‘": "’", "《": "》", "「": "」", "『": "』"}


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
    paragraphs = [p.strip() for p in text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if p.strip()]
    if not paragraphs:
        return {}
    tail = paragraphs[-1]
    if len(tail) > 200:
        return {}
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


_FRONT_SECTION_RE = re.compile(
    r"^(?:！！！)?@?\s*(最终书名|最终简介|三个备选书名|三个备选简介|目录|导言|文前|正文)\s*$"
)
_BODY_CHAPTER_RE = re.compile(r"^(?:！！！@?|@)\s*\d+[.．、]\s*.+$")
_FINANCE_SOURCE_LEAK_RE = re.compile(
    r"(?:^|[。！？；，,、\s])"
    r"(?:(?:根据|据|按照)?\s*(?:《[^》]{1,80}》)(?:中|里)?"
    r"|(?:根据|据|按照)?\s*(?:这段文稿|这份材料|这份素材|这份资料|原始视频文稿|视频材料|视频文稿|原视频|"
    r"参考资料|参考材料|输入材料|视频|文稿|材料|素材|资料)(?:中|里)?)"
    r"(?:提到|提及|指出|显示|写到|说到|说|认为|表明|给出|呈现|说明)"
)
_EDITORIAL_COMMENT_RE = re.compile(
    r"^@?\s*(?:"
    r"这几句话|这句话|这段|这一段|这里|这部分|"
    r"逻辑自洽|普通人.*出口|可以量化.*解决|读者可以|"
    r"AI习惯|AI式|审稿|批注|修改建议|改成|建议改"
    r")"
)
_CONTINUATION_PREFIXES = (
    "以及",
    "和",
    "与",
    "还有",
    "包括",
    "比如",
    "例如",
    "尤其",
    "同时",
    "并且",
    "但",
    "但是",
    "而",
    "却",
    "其中",
    "这些",
    "这种",
    "这类",
)
_FINAL_QA_EXCLUDED_CATEGORIES = {"comic_series", "healing_comic"}
_CLASSICAL_INTERLEAVED_CATEGORY = "classical_chinese_interleaved"
_CLASSICAL_PAIRED_TRANSLATION_CATEGORIES = {
    _CLASSICAL_INTERLEAVED_CATEGORY,
    "classical_chinese_reference_rewrite",
}
_CLASSICAL_LABEL_LINE_RE = re.compile(
    r"^(?:\u3010P\d{4}\u3011\s*)?"
    r"(?:\uff08\u539f\u6587\uff09|\uff08\u7ffb\u8bd1\uff09)"
)
_INLINE_SUBHEADING_KEYWORDS = (
    "代价",
    "难题",
    "局限",
    "风险",
    "真相",
    "困境",
    "陷阱",
    "选择",
    "转折",
    "退路",
    "误判",
    "成本",
    "边界",
    "启示",
    "根源",
    "答案",
    "分水岭",
    "账本",
    "底气",
    "诉求",
    "策略",
    "模式",
    "路径",
    "危机",
    "余波",
    "裂缝",
    "规则",
    "逻辑",
    "竞争",
    "转化",
)
_INLINE_SUBHEADING_SENTENCE_HINTS = (
    "我们",
    "你",
    "他",
    "她",
    "它",
    "大家",
    "人们",
    "可能",
    "可以",
    "能够",
    "能不能",
    "能否",
    "应该",
    "必须",
    "需要",
    "值得",
    "不是",
    "不会",
    "不能",
    "只会",
    "才会",
    "才可能",
    "帮",
    "让",
    "把",
    "被",
    "成为",
    "变成",
    "意味着",
    "取决于",
)
_SEMANTIC_COLLOCATION_RULES: dict[str, str] = {
    "语义搭配硬伤：菜/饭菜不应写成好喝": (
        r"re:(?:菜|饭菜|菜肴|菜品|饭|米饭|面条|馒头|包子|点心|肉|鱼)"
        r"[^。！？；\n]{0,40}好喝"
    ),
    "语义搭配硬伤：固体食物不应写成喝起来": (
        r"re:(?:菜|饭菜|菜肴|菜品|饭|米饭|面条|馒头|包子|点心|肉|鱼)"
        r"[^。！？；\n]{0,24}喝起来"
    ),
    "语义搭配硬伤：汤/茶/水不应写成好吃": (
        r"re:(?:汤|茶|水|酒|饮料|奶茶|咖啡|药汤|汤药|药水)"
        r"[^。！？；\n]{0,24}好吃"
    ),
    "语义搭配硬伤：可读文本不应写成读起来好听": (
        r"re:(?:书|文章|文字|文本|稿子|章节|小说|佛经)"
        r"读起来(?:很|非常|特别|也|并不|不)?好听"
    ),
}
_CLASSICAL_TRANSLATION_HARD_ERROR_RULES: dict[str, str] = {
    "古文误译硬伤：便觉等感知词不应译成导致": (
        r"re:(?:导致|造成|引发|致使|酿成)"
        r"[^。！？；\n]{0,40}(?:百行皆非|皆非|全都错|全错|都错|所有行为|一切行为)"
    ),
}
_CLASSICAL_REFERENCE_DIRECT_TRANSLATION_RULES: dict[str, str] = {
    "古文参照译写硬伤：精金美玉句停留在直译腔": (
        r"re:(?:如精金美玉般纯粹的人品|纯粹的人品[^。！？；\n]{0,24}烈火般的煅烧|"
        r"惊天动地的功业[^。！？；\n]{0,40}脚踩薄冰|像脚踩薄冰一样[^。！？；\n]{0,40}走过险境)"
    ),
    "古文参照译写硬伤：古典意象未转成大白话": (
        r"re:(?:磨炼出如精金美玉|经过烈火般的煅烧|建立惊天动地的功业|谨慎地走过险境)"
    ),
}
_PROMPT_RESIDUAL_RULES: dict[str, str] = {
    "输出要求残留": r"re:^(?:输出要求|任务|要求)[:：]",
    "提示词残留": r"re:(?:只输出成稿|不要解释|不要输出分析|以下是根据|我将为你(?:生成|整理|撰写|分析|改写|输出))",
}


def _cjk_ratio(text: str) -> float:
    stripped = text.strip()
    if not stripped:
        return 0.0
    cjk = sum(1 for char in stripped if "\u3400" <= char <= "\u9fff")
    return cjk / max(len(stripped), 1)


def _add_noise_hit(results: dict[str, list[str]], name: str, sample: str) -> None:
    bucket = results.setdefault(name, [])
    if len(bucket) < 10:
        bucket.append(sample[:120])


def _looks_like_naked_subheading(line: str) -> bool:
    stripped = line.strip().strip("*`# ")
    if not stripped or len(stripped) > 16:
        return False
    if _cjk_ratio(stripped) < 0.6:
        return False
    if re.search(r"[。！？；，、,.!?;：:]", stripped):
        return False
    if stripped.startswith(_CONTINUATION_PREFIXES):
        return False
    if stripped.endswith(("的", "和", "与", "及", "或", "把", "被", "让", "在", "对", "给")):
        return False
    return True


def _is_allowed_internal_subheading(line: str) -> bool:
    match = re.match(r"^###\s*([^#].*?)\s*$", line.strip())
    return bool(match and _looks_like_naked_subheading(match.group(1)))


def _looks_like_inline_pseudo_subheading_title(title: str) -> bool:
    title = title.strip().lstrip("@").strip()
    if not title or _cjk_ratio(title) < 0.55:
        return False
    if not any(keyword in title for keyword in _INLINE_SUBHEADING_KEYWORDS):
        return False
    if title.startswith(("这个", "这则", "这一", "这种", "这类", "那则", "那个")):
        return False
    # Inline pseudo-headings are usually short noun labels such as
    # “扎堆竞争的代价，...”. Normal topic sentences can also contain words like
    # “风险/规则”, so do not block long predicate-bearing sentences.
    if len(title) > 12 and any(hint in title for hint in _INLINE_SUBHEADING_SENTENCE_HINTS):
        return False
    return True


def _is_classical_interleaved_label_line(line: str, *, category: str | None = None) -> bool:
    if (category or "").strip().lower() not in _CLASSICAL_PAIRED_TRANSLATION_CATEGORIES:
        return False
    return bool(_CLASSICAL_LABEL_LINE_RE.match(line.strip()))


def _detect_format_noise(text: str, *, category: str | None = None) -> dict[str, list[str]]:
    results: dict[str, list[str]] = {}
    in_body = False
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        section = _FRONT_SECTION_RE.match(line)
        if section:
            in_body = section.group(1) == "正文"
            continue
        if not in_body:
            continue
        if _is_classical_interleaved_label_line(line, category=category):
            continue
        if _BODY_CHAPTER_RE.match(line) or re.match(r"^!\[[^\]]*\]\([^)]+\)\s*$", line):
            continue
        if _is_allowed_internal_subheading(line):
            continue
        if "**" in line or "__" in line:
            _add_noise_hit(results, "Markdown 粗体残留", line)
        if line.startswith("#"):
            _add_noise_hit(results, "# 标题残留", line)
        if line.startswith("@"):
            _add_noise_hit(results, "非法正文 @ 段首", line)
        if re.match(r"^(?:\d+|[一二三四五六七八九十百]+)[.．、]\s*\S+", line):
            _add_noise_hit(results, "正文内编号伪标题", line)
        if (line.startswith("@") and _EDITORIAL_COMMENT_RE.search(line)) or re.search(
            r"逻辑自洽|审稿|批注|修改建议|建议改|AI习惯|AI式",
            line,
        ):
            _add_noise_hit(results, "审稿批注式残留", line)
        if _looks_like_naked_subheading(line):
            _add_noise_hit(results, "裸短小标题", line)
    return results


def _detect_inline_pseudo_subheading(text: str, *, category: str | None = None) -> dict[str, list[str]]:
    results: dict[str, list[str]] = {}
    in_body = False
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        section = _FRONT_SECTION_RE.match(line)
        if section:
            in_body = section.group(1) == "正文"
            continue
        if not in_body:
            continue
        if _is_classical_interleaved_label_line(line, category=category):
            continue
        if _BODY_CHAPTER_RE.match(line) or _is_allowed_internal_subheading(line):
            continue
        match = re.match(r"^([^。！？；，、,.!?;：:\n]{3,16})[，,：:](.{8,})$", line)
        if not match:
            continue
        title = match.group(1)
        if _looks_like_inline_pseudo_subheading_title(title):
            _add_noise_hit(results, "正文内小标题未标准化为 ###", line)
    return results


def _detect_empty_front_sections(text: str) -> dict[str, list[str]]:
    results: dict[str, list[str]] = {}
    current_section: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        if current_section in {"最终书名", "最终简介", "目录", "导言", "正文"}:
            meaningful = [line.strip() for line in current_lines if line.strip()]
            if not meaningful:
                _add_noise_hit(results, "空前置块或空正文", f"@{current_section}")

    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        section = _FRONT_SECTION_RE.match(line)
        if section:
            flush()
            current_section = section.group(1)
            current_lines = []
            continue
        if current_section:
            current_lines.append(raw_line)
    flush()
    return results


def _detect_duplicate_body_headings(text: str) -> dict[str, list[str]]:
    results: dict[str, list[str]] = {}
    seen_chapters: set[str] = set()
    previous_internal_heading: str | None = None
    in_body = False
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        section = _FRONT_SECTION_RE.match(line)
        if section:
            in_body = section.group(1) == "正文"
            continue
        if not in_body:
            continue
        if _BODY_CHAPTER_RE.match(line):
            key = re.sub(r"\s+", "", line)
            if key in seen_chapters:
                _add_noise_hit(results, "重复章节头", line)
            seen_chapters.add(key)
            previous_internal_heading = None
            continue
        if _is_allowed_internal_subheading(line):
            key = re.sub(r"\s+", "", line)
            if previous_internal_heading == key:
                _add_noise_hit(results, "连续重复小标题", line)
            previous_internal_heading = key
            continue
        previous_internal_heading = None
    return results


def _body_chapters(text: str) -> list[tuple[str, list[str]]]:
    chapters: list[tuple[str, list[str]]] = []
    in_body = False
    current_heading: str | None = None
    current_lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        section = _FRONT_SECTION_RE.match(line)
        if section:
            if current_heading:
                chapters.append((current_heading, current_lines))
            in_body = section.group(1) == "正文"
            current_heading = None
            current_lines = []
            continue
        if not in_body:
            continue
        if _BODY_CHAPTER_RE.match(line):
            if current_heading:
                chapters.append((current_heading, current_lines))
            current_heading = line
            current_lines = []
            continue
        if current_heading:
            current_lines.append(line)
    if current_heading:
        chapters.append((current_heading, current_lines))
    return chapters


def _cjk_count(text: str) -> int:
    return sum(1 for char in text if "\u3400" <= char <= "\u9fff")


def _detect_buddhist_story_shape_errors(text: str, *, category: str | None = None) -> dict[str, list[str]]:
    if (category or "").strip().lower() != "buddhist_scripture_story":
        return {}
    chapters = _body_chapters(text)
    if not chapters:
        return {}

    results: dict[str, list[str]] = {}
    chapter_count = len(chapters)
    if "百喻经" in text and chapter_count != 98 and chapter_count >= 90:
        _add_noise_hit(
            results,
            "《百喻经》章节数量异常",
            f"检测到 {chapter_count} 个正文正式章节；《百喻经》全本应按九十八则譬喻组织，不能多出总结章、寓意章或把同一则拆成多章。",
        )
    if "百喻经" in text and chapter_count < 90:
        _add_noise_hit(
            results,
            "《百喻经》章节覆盖不足",
            f"检测到 {chapter_count} 个正文正式章节；《百喻经》全本应覆盖九十八则譬喻，不能只生成前半部或提前收束。",
        )

    thin_samples: list[str] = []
    short_story_samples: list[str] = []
    for heading, lines in chapters:
        prose_lines = [line for line in lines if not _is_allowed_internal_subheading(line)]
        body = "".join(prose_lines)
        cjk_chars = _cjk_count(body)
        if cjk_chars < 45:
            preview = " ".join(prose_lines)[:90]
            thin_samples.append(f"{heading}｜{preview or '正文为空'}")
        if cjk_chars < 100:
            preview = " ".join(prose_lines)[:90]
            short_story_samples.append(f"{heading}｜{cjk_chars} 字｜{preview or '正文为空'}")

    if chapter_count >= 20 and len(thin_samples) >= max(5, chapter_count // 5):
        for sample in thin_samples[:10]:
            _add_noise_hit(results, "佛经白话章节过短，疑似故事和寓意被拆章", sample)
    if chapter_count >= 20 and len(short_story_samples) >= max(8, chapter_count // 3):
        for sample in short_story_samples[:10]:
            _add_noise_hit(results, "佛经白话章节过短，故事展开不足", sample)
    return results


def detect_final_hard_errors(markdown: str, *, category: str | None = None) -> dict[str, list[str]]:
    """Detect high-confidence final manuscript errors that should block delivery."""
    category_key = (category or "").strip().lower()
    if category_key in _FINAL_QA_EXCLUDED_CATEGORIES:
        return {}

    results: dict[str, list[str]] = {}

    def merge(hits: dict[str, list[str]]) -> None:
        for name, samples in hits.items():
            bucket = results.setdefault(name, [])
            for sample in samples:
                if sample not in bucket and len(bucket) < 10:
                    bucket.append(sample)

    for name, pattern in _SEMANTIC_COLLOCATION_RULES.items():
        hits = _detect(markdown, pattern)
        if hits:
            results[name] = hits
    for name, pattern in _PROMPT_RESIDUAL_RULES.items():
        hits = _detect(markdown, pattern)
        if hits:
            results[name] = hits
    if category_key in _CLASSICAL_PAIRED_TRANSLATION_CATEGORIES or category_key == "classical_chinese":
        for name, pattern in _CLASSICAL_TRANSLATION_HARD_ERROR_RULES.items():
            hits = _detect(markdown, pattern)
            if hits:
                results[name] = hits
    if category_key == "classical_chinese_reference_rewrite":
        for name, pattern in _CLASSICAL_REFERENCE_DIRECT_TRANSLATION_RULES.items():
            hits = _detect(markdown, pattern)
            if hits:
                results[name] = hits
    merge(_detect_format_noise(markdown, category=category_key))
    merge(_detect_inline_pseudo_subheading(markdown, category=category_key))
    merge(_detect_empty_front_sections(markdown))
    merge(_detect_duplicate_body_headings(markdown))
    merge(_detect_buddhist_story_shape_errors(markdown, category=category_key))
    return results


def format_final_hard_error_hits(hits: dict[str, list[str]], *, top_k: int = 8) -> str:
    lines: list[str] = []
    for name, samples in sorted(hits.items(), key=lambda item: item[0]):
        lines.append(f"- {name}: {len(samples)} 处")
        for sample in samples[:top_k]:
            lines.append(f"  - {sample[:160]}")
    return "\n".join(lines)


def _masterpiece_narrative_text(markdown: str) -> str:
    lines: list[str] = []
    in_target_section = False
    for raw_line in markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        section = _FRONT_SECTION_RE.match(line)
        if section:
            in_target_section = section.group(1) in {"导言", "正文"}
            continue
        if not in_target_section:
            continue
        if _BODY_CHAPTER_RE.match(line) or re.match(r"^!\[[^\]]*\]\([^)]+\)\s*$", line):
            continue
        lines.append(line)
    return "\n".join(lines)


def _outside_masterpiece_protected_segments(text: str) -> list[str]:
    segments: list[str] = []
    index = 0
    while index < len(text):
        opener = text[index]
        closer = MASTERPIECE_PROTECTED_PAIRS.get(opener)
        if closer:
            end = text.find(closer, index + 1)
            if end != -1:
                index = end + 1
                continue

        start = index
        while index < len(text) and text[index] not in MASTERPIECE_PROTECTED_PAIRS:
            index += 1
        if index > start:
            segments.append(text[start:index])
    return segments


def _detect_masterpiece_colloquial(markdown: str) -> dict[str, list[str]]:
    target_text = _masterpiece_narrative_text(markdown)
    if not target_text:
        return {}

    results: dict[str, list[str]] = {}
    for segment in _outside_masterpiece_protected_segments(target_text):
        if not segment.strip():
            continue
        for name, pattern in MASTERPIECE_COLLOQUIAL_RULES.items():
            hits = _detect(segment, pattern)
            if not hits:
                continue
            bucket = results.setdefault(name, [])
            for sample in hits:
                if len(bucket) >= 10:
                    break
                bucket.append(sample[:120])
    return results


def _detect_finance_source_leaks(markdown: str) -> dict[str, list[str]]:
    results: dict[str, list[str]] = {}
    in_target_section = False
    for raw_line in markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        section = _FRONT_SECTION_RE.match(line)
        if section:
            in_target_section = section.group(1) in {"最终简介", "导言", "正文"}
            continue
        if not in_target_section:
            continue
        if _BODY_CHAPTER_RE.match(line) or re.match(r"^!\[[^\]]*\]\([^)]+\)\s*$", line):
            continue
        if _FINANCE_SOURCE_LEAK_RE.search(line):
            _add_noise_hit(results, "财经来源穿帮表述残留", line)
    return results


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
        + sum(len(v) for v in report.format_noise_hits.values())
        + sum(len(v) for v in report.final_hard_error_hits.values())
        + sum(len(v) for v in report.masterpiece_colloquial_hits.values())
        + sum(len(v) for v in report.finance_source_leak_hits.values())
    )
    report.total_violations = total
    report.violation_density_per_kchar = total / max(report.total_chars, 1) * 1000


def measure_quality(markdown: str, *, category: str | None = None) -> QualityReport:
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
    report.format_noise_hits = _detect_format_noise(markdown)
    report.final_hard_error_hits = detect_final_hard_errors(markdown, category=category)
    category_key = (category or "").strip().lower()
    if category_key == "masterpiece_interpretation":
        report.masterpiece_colloquial_hits = _detect_masterpiece_colloquial(markdown)
    if category_key in {
        "finance_technology",
        "finance_technology_recommendation",
    }:
        report.finance_source_leak_hits = _detect_finance_source_leaks(markdown)

    marks = _count_marks(markdown)
    if category_key == "life_philosophy":
        centered_markers = len(re.findall(r"^！！！(?:@|\d+[.．、])", markdown, re.MULTILINE))
        marks["double_excl"] = max(0, marks["double_excl"] - centered_markers)
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
    _render_section("排版污染（小标题 / Markdown / 批注残留）", report.format_noise_hits)
    _render_section("终稿硬伤闸门（命中后不应直接交付）", report.final_hard_error_hits)
    _render_section("名作解读口语化残留", report.masterpiece_colloquial_hits)
    _render_section("财经来源穿帮表述残留", report.finance_source_leak_hits)

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
