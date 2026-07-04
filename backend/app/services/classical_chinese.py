"""Classical Chinese prompt helpers and local dictionary context."""
from __future__ import annotations

import json
import logging
import lzma
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT, settings

logger = logging.getLogger(__name__)

CLASSICAL_VARIANT_OPTIONS: tuple[dict[str, str], ...] = (
    {
        "id": "plain",
        "label": "通俗白话",
        "description": "通俗连贯地重新表达古文内容，忠实原意，避免翻译腔。",
    },
    {
        "id": "zhiguai_web",
        "label": "志怪小说爽读版",
        "description": "保留志怪故事主线，强化剧情、对白、冲突和悬念。",
    },
    {
        "id": "wuxia_web",
        "label": "武侠小说爽读版",
        "description": "保留武侠情节与人物行动，语言更白话但要有武侠味。",
    },
    {
        "id": "poetry",
        "label": "诗歌翻译",
        "description": "把诗歌译成一整段华丽但易懂的现代汉语。",
    },
    {
        "id": "buddhist_story",
        "label": "佛经白话故事版",
        "description": "把佛经经文译成连贯白话，适度故事化表达，忠于原文讲述和道理。",
    },
    {
        "id": "reference_translation",
        "label": "知名译本参照",
        "description": "参考已有译文的释义、术语和断句，结合古汉语词典，重新写成自然原创白话。",
    },
)

_VARIANT_ALIASES: dict[str, str] = {
    "": "plain",
    "default": "plain",
    "general": "plain",
    "plain": "plain",
    "通俗白话": "plain",
    "白话": "plain",
    "志怪": "zhiguai_web",
    "志怪小说": "zhiguai_web",
    "志怪小说爽读版": "zhiguai_web",
    "zhiguai": "zhiguai_web",
    "zhiguai_web": "zhiguai_web",
    "武侠": "wuxia_web",
    "武侠小说": "wuxia_web",
    "武侠小说爽读版": "wuxia_web",
    "wuxia": "wuxia_web",
    "wuxia_web": "wuxia_web",
    "诗歌": "poetry",
    "诗歌翻译": "poetry",
    "poetry": "poetry",
    "佛经": "buddhist_story",
    "佛经白话": "buddhist_story",
    "佛经白话故事版": "buddhist_story",
    "buddhist": "buddhist_story",
    "buddhist_story": "buddhist_story",
    "知名译本": "reference_translation",
    "知名译本参照": "reference_translation",
    "参考译本": "reference_translation",
    "参照译写": "reference_translation",
    "reference_translation": "reference_translation",
}

_VARIANT_RULES: dict[str, str] = {
    "plain": """- 参考项目文件里的古汉语词典摘录，联系上下文翻译，不确定的词义要保守处理。
- 我发给你什么内容，你就改写什么；不要脑补剧情，不要跳剧情，不要自己加开头和结尾。
- 通俗连贯地重新表达原文，调整用词和语序，但不得脱离原意。
- 不要直接机翻，不要翻译腔；对白要像人正常说话，不要像书面语硬译。
- 不随意添加细节，不臆测场景，不添加新观点，只对语言做通俗、连贯的表达。""",
    "zhiguai_web": """- 请将志怪小说原文改写为网文爽读风格。
- 以剧情和人物对话为核心，对白鲜活、简短有力，推动情节发展。
- 保留故事主线，不篡改剧情，不自己加剧情，不跳剧情。
- 删去冗长环境描写和无关枝节；场景描写点到为止，用几句渲染氛围即可。
- 叙事简洁明快，注重冲突、动作和悬念，符合网文阅读节奏。
- 人物语言接地气、个性分明，避免生硬书面语和翻译腔。
- 不要有太多神态描写，点到即止；不要太频繁分段。""",
    "wuxia_web": """- 请将武侠小说原文改写为网文爽读风格。
- 以剧情、动作和人物对话为核心，对白鲜活、简短有力，推动情节发展。
- 保留故事主线，不篡改剧情，不自己加剧情，不跳剧情。
- 文风更白话，但保留武侠小说的语言色彩和江湖气。
- 场景描写点到为止，不可喧宾夺主；不要把内容改得太简略。
- 一个人说的话内部不要频繁分段；换人说话时再分段，长话可按语义分成几段。
- 对白要像人正常说话，不要书面语味道；神态描写点到即止。""",
    "poetry": """- 把诗歌翻译成一整段现代汉语，不必分行，不要加粗。
- 用语可以华丽一点，但必须贴着诗歌原意，不能脱离原诗乱发挥。
- 可以适当补充必要语义，让普通读者一眼读懂。
- 不要用太生僻的字，不要把诗意翻成空泛抒情或现代鸡汤。
- 保留诗歌中的意象、情绪方向、时间空间关系和语气转折。""",
    "buddhist_story": """- 请将佛经经文翻译成自然、连贯、能读下去的白话文，整体读感像一个平稳讲述的故事。
- 改动幅度不要太大，只做必要的语序调整、指代补足和衔接润色，让现代读者能顺着人物、问答、因缘和说法过程读懂。
- 不要编造原文没有的人物、地点、事件、对白、神通、因果细节、结局或教义解释。
- 可以把经文里的问答、譬喻、动作和说法过程写得更顺，但必须契合原文讲述的事情和道理。
- 语言要有故事感，但不要改成小说爽文、玄幻故事、心灵鸡汤、现代心理学文章或宗教宣传口号。
- 遇到佛教术语、名号、地名、人名和核心教义，不确定时保守处理，可保留原词并用上下文自然解释，不要乱翻。""",
    "reference_translation": """- 参考已有译本或白话稿时，只允许吸收释义判断、术语译法、断句思路、人物称谓和语气密度；不得照搬、近似改写或按句同义替换参考译文。
- 最终输出形态只做原文与翻译对照：每个原文段落先输出“（原文）原文内容”，紧跟输出“（翻译）现代白话译文”。
- 不再采用《菜根谭》类全注全译体例；不得输出【注释】【译文】【点评】【原文定位】、赏析、解析、译者按或圈号注释。
- 原文段必须忠实保留输入原文，不改字、不删句、不调换顺序；翻译段必须完整覆盖上一段原文。
- 最终正文必须重新组织翻译句子，让读者读到的是新的白话译文，而不是某个译本的洗稿版。
- 参考译文与古汉语词典冲突时，优先结合原文上下文和词典保守判断；不要为了追随参考译文而改错原意。
- 语言要比普通直译更连贯、自然、少 AI 腔，保留古文意味，但不用现代口水话稀释原文。
- 不随意补写新情节，不新增原文没有的人物关系、事件、结论和评价。""",
}

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_PUA_MARKER_RE = re.compile(r"\{\[[0-9a-fA-F]+\]\}")
_LINK_MARK_RE = re.compile(r"[`~]")
CLASSICAL_PARAGRAPH_MARKER_RE = re.compile(r"【P(\d{4,})】\s*")
_TITLE_SUFFIX = "（白话版）"
BUDDHIST_SCRIPTURE_DEFAULT_TITLE = "佛经白话"
CLASSICAL_INTERLEAVED_CATEGORY = "classical_chinese_interleaved"
CLASSICAL_REFERENCE_REWRITE_CATEGORY = "classical_chinese_reference_rewrite"
BUDDHIST_SCRIPTURE_STORY_CATEGORY = "buddhist_scripture_story"
BUDDHIST_SCRIPTURE_VARIANT = "buddhist_story"
CLASSICAL_CHINESE_CATEGORY_IDS = frozenset(
    {
        "classical_chinese",
        CLASSICAL_INTERLEAVED_CATEGORY,
        CLASSICAL_REFERENCE_REWRITE_CATEGORY,
        BUDDHIST_SCRIPTURE_STORY_CATEGORY,
    }
)


@dataclass(frozen=True)
class BuddhistStoryUnit:
    index: int
    title: str
    source: str


def classical_variant_for_category(category: object, variant: object = None) -> str:
    if str(category or "").strip() == BUDDHIST_SCRIPTURE_STORY_CATEGORY:
        return BUDDHIST_SCRIPTURE_VARIANT
    if str(category or "").strip() == CLASSICAL_REFERENCE_REWRITE_CATEGORY and not str(variant or "").strip():
        return "reference_translation"
    return normalize_classical_variant(variant)


def normalize_classical_variant(raw: object) -> str:
    key = str(raw or "").strip()
    normalized = _VARIANT_ALIASES.get(key)
    if normalized:
        return normalized
    ids = {item["id"] for item in CLASSICAL_VARIANT_OPTIONS}
    return key if key in ids else "plain"


def get_classical_variant_meta(raw: object) -> dict[str, str]:
    variant_id = normalize_classical_variant(raw)
    for item in CLASSICAL_VARIANT_OPTIONS:
        if item["id"] == variant_id:
            return dict(item)
    return dict(CLASSICAL_VARIANT_OPTIONS[0])


def classical_variant_rules(raw: object) -> str:
    return _VARIANT_RULES[normalize_classical_variant(raw)]


def classical_final_title(original_title: object) -> str:
    title = re.sub(r"\s+", " ", str(original_title or "")).strip(" \t\r\n《》")
    if not title:
        title = "古文白话"
    if title.endswith(_TITLE_SUFFIX):
        return title
    return f"{title}{_TITLE_SUFFIX}"


def buddhist_scripture_final_title(original_title: object = None) -> str:
    title = re.sub(r"\s+", " ", str(original_title or "")).strip(" \t\r\n《》")
    if not title:
        title = BUDDHIST_SCRIPTURE_DEFAULT_TITLE
    if title.endswith(_TITLE_SUFFIX):
        return title
    return f"{title}{_TITLE_SUFFIX}"


def _dictionary_path() -> Path:
    configured = Path((settings.CLASSICAL_DICTIONARY_PATH or "").strip())
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured


def _read_dictionary(path: Path) -> Any:
    if path.suffix == ".xz":
        with lzma.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=1)
def _dictionary_index() -> dict[str, dict[str, Any]]:
    path = _dictionary_path()
    if not path.exists():
        logger.info("classical dictionary not found: %s", path)
        return {}
    try:
        data = _read_dictionary(path)
    except Exception as exc:
        logger.warning("failed to load classical dictionary %s: %s", path, exc)
        return {}
    if isinstance(data, dict):
        entries = data.get("entries") or data.get("data") or []
    else:
        entries = data
    index: dict[str, dict[str, Any]] = {}
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title") or entry.get("t") or "").strip()
            if title:
                index[title] = entry
    logger.info("loaded classical dictionary entries=%d path=%s", len(index), path)
    return index


def _clean_dictionary_text(value: object) -> str:
    text = str(value or "").strip()
    text = _PUA_MARKER_RE.sub("", text)
    text = _LINK_MARK_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _candidate_terms(raw_text: str) -> list[str]:
    counts: Counter[str] = Counter()
    order: dict[str, int] = {}
    for block in _CJK_RE.findall(raw_text or ""):
        if len(order) > 3000:
            break
        block = block[:3000]
        for size in (4, 3, 2, 1):
            if len(block) < size:
                continue
            for idx in range(0, len(block) - size + 1):
                term = block[idx : idx + size]
                counts[term] += 1
                order.setdefault(term, len(order))
                if len(order) > 3000:
                    break
            if len(order) > 3000:
                break
    return sorted(counts, key=lambda term: (-len(term), -counts[term], order[term]))


def _format_entry(entry: dict[str, Any], *, max_chars: int = 260) -> str:
    title = _clean_dictionary_text(entry.get("title") or entry.get("t"))
    fragments: list[str] = []
    for heteronym in entry.get("heteronyms") or entry.get("h") or []:
        if not isinstance(heteronym, dict):
            continue
        for definition in heteronym.get("definitions") or heteronym.get("d") or []:
            if not isinstance(definition, dict):
                continue
            part = _clean_dictionary_text(definition.get("def") or definition.get("f"))
            if not part:
                continue
            kind = _clean_dictionary_text(definition.get("type"))
            if kind:
                part = f"{kind}：{part}"
            fragments.append(part)
            if len(fragments) >= 3:
                break
        if len(fragments) >= 3:
            break
    if not title or not fragments:
        return ""
    line = f"{title}：{'；'.join(fragments)}"
    return line[:max_chars]


def build_classical_dictionary_context(raw_text: str) -> str:
    index = _dictionary_index()
    if not index:
        return "未检测到本地古汉语词典文件；仍须严格依据原文上下文谨慎翻译，不得凭空断义。"

    max_chars = max(800, int(settings.CLASSICAL_DICTIONARY_CONTEXT_MAX_CHARS or 4500))
    max_entries = max(1, int(settings.CLASSICAL_DICTIONARY_MAX_ENTRIES or 80))
    lines: list[str] = []
    used = 0
    for term in _candidate_terms(raw_text):
        entry = index.get(term)
        if not entry:
            continue
        line = _format_entry(entry)
        if not line:
            continue
        projected = used + len(line) + 3
        if projected > max_chars or len(lines) >= max_entries:
            break
        lines.append(f"- {line}")
        used = projected
    if not lines:
        return "本段未命中本地词典高价值词条；仍须联系上下文谨慎翻译。"
    return "\n".join(lines)


def classical_source_lines(raw_text: str) -> list[str]:
    return [line.strip() for line in (raw_text or "").splitlines() if line.strip()]


def classical_expected_paragraph_count(raw_text: str) -> int:
    return len(classical_source_lines(raw_text))


def classical_paragraph_marker(index: int) -> str:
    return f"【P{index:04d}】"


def strip_classical_paragraph_markers(text: str) -> str:
    return CLASSICAL_PARAGRAPH_MARKER_RE.sub("", text or "")


def _strip_bold_marks(text: str) -> str:
    return re.sub(r"^\*\*(.+?)\*\*$", r"\1", (text or "").strip())


def _classical_line_role(line: str) -> str:
    stripped = _strip_bold_marks(line)
    if line.strip().startswith("**") and line.strip().endswith("**"):
        return "作者或署名行，必须保留并保持加粗标记"
    if len(stripped) <= 24 and not re.search(r"[，。！？；：“”‘’]", stripped):
        return "辑名、篇名或结构标题，必须保留标题含义，不得改名或丢失"
    if re.match(r"^(序|引|导言|题记|按语|凡例|小引|卷首)", stripped):
        return "导言、引语、题记或按语，若原本已是白话，只做标点整理，不得重写"
    return "正文段落，必须完整白话化，不得漏句、漏半句或跳译"


def build_classical_marked_source(raw_text: str, *, start_index: int = 1) -> str:
    lines = classical_source_lines(raw_text)
    return "\n".join(
        f"{classical_paragraph_marker(idx)} {line}"
        for idx, line in enumerate(lines, start=max(1, int(start_index or 1)))
    )


def build_classical_structure_checklist(
    raw_text: str,
    *,
    max_lines: int = 120,
    start_index: int = 1,
) -> str:
    lines = classical_source_lines(raw_text)
    if not lines:
        return "未检测到有效原文段落。"
    rendered: list[str] = []
    for idx, line in enumerate(lines[:max_lines], start=max(1, int(start_index or 1))):
        clean = _strip_bold_marks(line)
        preview = clean[:36] + ("…" if len(clean) > 36 else "")
        rendered.append(f"- P{idx:04d}：{_classical_line_role(line)}：{preview}")
    if len(lines) > max_lines:
        rendered.append(f"- 其余 {len(lines) - max_lines} 个段落同样必须按 P 编号连续处理，不得省略。")
    return "\n".join(rendered)


def build_classical_prompt_source(
    raw_text: str,
    *,
    variant: object = None,
    original_title: object = None,
    start_index: int = 1,
    interleaved: bool = False,
) -> str:
    meta = get_classical_variant_meta(variant)
    is_reference_translation = meta["id"] == "reference_translation"
    dictionary_context = build_classical_dictionary_context(raw_text)
    final_title = classical_final_title(original_title)
    marked_source = build_classical_marked_source(raw_text, start_index=start_index)
    structure_checklist = build_classical_structure_checklist(raw_text, start_index=start_index)
    paragraph_rules = (
        """【参照译写排版硬规则】
- 本任务只输出原文与翻译对照，不再输出注释、点评或讲解体例。
- @正文 下的每个原文段落必须输出两个连续自然段，顺序固定为：先原文，后翻译。
- 每个原文段必须以对应 P 编号开头，格式为：【P0001】（原文）原文内容。
- 每个翻译段必须紧跟对应原文段，格式为：（翻译）现代白话译文。
- 原文段中的原文内容必须逐字保留任务包里的对应原文，不得改字、删句、改标点、改顺序，也不得把多个原文段合并到一起。
- 翻译段必须完整覆盖上一段原文的全部内容，不得漏译、跳译、只概括大意或另起发挥。
- 不得输出【注释】【译文】【点评】【原文定位】、赏析、解析、译者按、圈号注释或资料说明。
- 参考译本只用于校准字词、术语、断句、称谓和语气准确性；最终表达必须回到原文和古汉语词典重新组织，不得复制或贴近改写参考译文。
- 正文标点统一使用中文全角标点和中文引号；中文之间、中文和标点之间不要加排版空格。"""
        if is_reference_translation
        else """【段落硬规则】
- 原文段落边界以换行分隔为准。白话正文必须按原文段落一一对应输出：原文第几段，白话正文就第几段。
- 不要为了阅读节奏强制分段，不要合并原文段落，不要拆散原文段落，不要重排段落顺序。
- 白话正文段落之间不要插入空白行，不要输出只有空格或单独标点的空段。
- 段内不要保留排版空格；中文之间、中文和标点之间不要加空格。
- 正文标点必须使用中文全角标点：，。！？；：“”‘’（）《》；不要使用英文逗号、句号、问号、叹号、冒号、分号和英文引号。
- @正文 下的每个原文对应段落，必须在段首保留对应内部编号，如【P0001】、【P0002】。这些编号是系统校验用，最终会由系统自动移除；你不得删除、改写、跳号、重号或调换顺序。
- 辑名、篇名、单独标题、作者行、署名行、导言、引语、题记、按语、凡例都算独立段落，必须按编号保留。原文已经是白话的导言、引语、题记或按语，只允许整理中文标点和明显错别字，不得重新改写。
- 原文中整段加粗的作者或署名行会以 **作者名** 形式出现，输出时必须保留加粗标记；其他段落不要自行加粗。
- 不得凭空增加没有 P 编号的小标题、Markdown 标题、资料型提示或解释性小节；不得出现 ### 这类标题符号。
- 如果某段无法完全确定词义，必须保守表达，并尽量保留原句关系，不得为了顺口而改成另一层意思。"""
    )
    interleaved_rules = (
        """
【原文翻译穿插版硬规则】
- 本任务是“白话原文翻译穿插版”，不是普通纯白话版。
- @正文 下每个原文段落必须输出两个连续自然段，顺序固定为：先原文，后翻译。
- 每个原文段必须以对应 P 编号开头，格式为：【P0001】（原文）原文内容。
- 每个翻译段必须紧跟对应原文段，格式为：（翻译）现代汉语译文。
- 原文段中的原文内容必须逐字保留任务包里的对应原文，不得改字、删句、改标点、改顺序，也不得把多个原文段合并到一起。
- 翻译段必须完整覆盖上一段原文的全部内容，不得漏句、漏半句、跳译、概括代替翻译。
- 不得先集中输出全部原文再集中输出全部翻译；不得把原文和翻译混在同一个自然段里；不得额外添加“注释”“赏析”“解析”“译者按”等内容。
- 示例格式：
  【P0001】（原文）南阳宋定伯，年少时，夜行，逢鬼，问之。
  （翻译）南阳有个叫宋定伯的人，年轻时有一晚赶路，遇见了鬼，便开口询问它。
"""
        if interleaved
        else ""
    )
    return f"""【古文白话总方向】
形似神似，活灵活现。必须在故事自己的基础上，把原文的骨架、情节、人物关系、语气和意味都保住，同时让现代读者读起来顺畅、自然、有画面。不要把古文改成另一个故事，也不要为了好看而擅自加戏。

【书名硬规则】
- 最终书名必须逐字使用：{final_title}
- 不要生成新书名，不要改写原书名，不要扩写、缩短或润色书名；只允许在原书名后保留“（白话版）”。

【简介硬规则】
- @最终简介要适合在微信读书投放，文学性强一点，像认真图书的封底简介。
- 简介要有古典文本的气息、人物与命运的余味、阅读质感；不需要“抓人研究”、爆款钩子、标题党、营销口号或论文式研究价值。

{paragraph_rules}

【古今异义与误译防护】
- 遇到古今异义词、器物名、职官名、旧称、俗称，必须结合上下文和词典摘录判断，不得机械套现代常见义。
- 例如“婢子不宜好相识”一类句子，应按人物关系和语境理解为“不宜同她交好”或“不宜对她太客气”，不得写成“结交什么好相识”。
- 例如“家人捉双卫来寻生”中，“卫”在该语境下应按坐骑、驴子一类理解，不得译成侍卫、保镖或护卫。
- 遇到不确定词义，宁可写得保守，也不要写成看似顺口但改变事实的现代解释。
{interleaved_rules}

【原文结构清单】
{structure_checklist}

【细分方向】
{meta["label"]}：{meta["description"]}

【分类翻译要求】
{classical_variant_rules(meta["id"])}

【古汉语词典摘录】
{dictionary_context}

【待处理原文】
{marked_source}"""


def build_buddhist_scripture_story_source(
    raw_text: str,
    *,
    original_title: object = None,
) -> str:
    final_title = buddhist_scripture_final_title(original_title)
    source_text = (raw_text or "").strip()
    return f"""【佛经小故事改写任务包】
本任务不是逐段直译，也不是佛经注释、讲义、心灵鸡汤或现代心理学文章。请先在内部提炼经文的故事骨架：谁在修行/提问/听法，发生了什么观照或问答，说法如何一层层推进，最后落在哪里。不要输出这个骨架，只用它来写成一篇读起来像小故事的白话稿。

【书名硬规则】
- 最终书名必须逐字使用：{final_title}
- 不要生成新书名，不要改写原书名，不要扩写、缩短或润色书名；只允许在原书名后保留“（白话版）”。

【核心改写规则】
- 成稿必须像小故事：有开端，有推进，有转折或层层深入，有收束。读者应该感觉自己在读一个说法过程或观照过程，而不是读一篇词语解释。
- 先理解经文讲了什么，再把原文已有的场景、人物、问答、动作、譬喻、因缘和说法顺序扩写成读者能顺着读下去的小故事。
- 故事感必须来自原文已有的问答、动作、譬喻、说法顺序和人物关系，不得靠新增剧情制造戏剧性。
- 改动幅度不要太大，指的是不改原文事实和道理；但表达形态必须明显不同于直译，要把原文里压缩的意思展开为可阅读的叙事。
- 短经也要根据内容自然扩写，但没有固定字数标准；以故事是否完整、是否有推进和收束为准。不能贴着原句逐句翻译。
- 长佛经必须覆盖全本，不能只写一个总故事、总论或代表性段落。必须按原文顺序拆成多个故事单元/说法单元；每个单元负责覆盖一段原文内容，合起来从开头覆盖到结尾。
- 如果原文包含多个独立因缘、譬喻、问答、品、卷、章节或场景，每一类内容都要各自写成不同的小故事或说法故事，不能合并成一篇概括文。
- 一个原文故事单元只能对应一个正文正式章节。不得把同一则因缘、譬喻或故事拆成“故事章”和“道理章”两章；道理、寓意、修行提醒必须放回同一章末尾自然收束。
- 《百喻经》全本是九十八则譬喻，正文正式章节应按九十八则左右组织；不得扩成一百九十多章、二百章或二百零四章。目录中一则譬喻只能占一条，不能为同一则故事再单独列一条“寓意”“启示”“代价”“智慧”章节。
- 每个正文正式章节都必须是一则完整小故事或说法单元，至少包含故事进入、人物/动作/譬喻推进和同章内的自然收束；不得生成只有一句道理、几句话评价或寥寥数字的空章节。
- 每个故事单元不设固定字数标准，以把譬喻讲明白、有故事推进、有道理收束为准。讲清楚就自然收住，不清楚就继续展开；不要为了省篇幅写成梗概，也不要为了凑字数硬扩写。
- 百喻经这类短譬喻通常可以写成“故事进入 → 愚人误判或执著 → 后果显现 → 同章内收束寓意”的自然小故事；语言要顺滑，不要把短故事拖成重复解释。
- 语义搭配必须符合中文常识：饭、菜、菜肴写“好吃、味道好、鲜美”，汤、茶、水写“好喝”；避免“菜好喝、汤好吃”等低级错配。
- 不要按原文段落一一对应输出，不要保留内部 P 编号，不要把正文写成“原文一句、翻译一句”的资料稿。
- 不要逐句解释“某某意思是……”。要写成讲述：谁在修行或开示，看到什么，如何一步步说明，这场说法如何推进，又如何收束。
- 正文主体不能是术语词典或白话讲义。少用“所谓、指的是、意思是、也就是说、这说明”这类解释腔句式；必须先有叙事动作和说法推进，再自然带出道理。
- 不要编造原文没有的人物、地点、事件、对白、神通、因果细节、结局或教义解释。
- 可以补足必要的叙事承接、指代和心理理解，例如“这时”“于是”“他继续说明”“这句话的意思慢慢展开”，但不能新增具体地点、人物、事件或结局。
- 可以把经文里的问答、譬喻、动作和说法过程写得更顺、更完整，但必须契合原文讲述的事情和道理。
- 遇到佛教术语、名号、地名、人名和核心教义，不确定时保守处理，可保留原词并用上下文自然解释，不要乱翻。
- 如果原文主要是偈颂或教义阐述，也不能只做白话翻译；要写成一场平稳的观照或说法过程，把抽象句子展开为连续的理解过程。
- 如果原文出现“舍利子”“须菩提”等称呼，说明经文里有说法对象；正文必须把它写成开示或半对白的讲述过程，而不是单纯概念解释。

【小故事形态硬规则】
- 第一章必须从原文已有的动作、观照、提问或说法关系进入，不能从概念定义进入。
- 每一章都要有叙事推进句，例如“他看见……”“他转而告诉……”“这层意思继续展开……”“说法到这里收束到……”；不要连续三段都在解释术语。
- 长文本的章节数量不设固定上限；需要多少个故事单元才能讲清楚、覆盖全本，就写多少个。不要为了凑少数章节而压缩、跳过或混合不同故事。
- 目录要体现原文从头到尾的故事单元顺序。正文每个正式章节都必须承担一个明确覆盖范围：一个因缘、一个譬喻、一个问答推进、一个说法层次或一个收束。
- 目录和正文的正式章节数量必须以原文故事单元数为准，不以“故事+寓意”双倍计算；同一则故事的寓意只能写在同一章内部，不能另起正式章节。
- 如果原文没有具体地点、天气、钟声、旁观者，就不要编这些外部场景；可以写“这场故事发生在一次照见里”“这场开示从某个称呼开始”这类不新增事实的叙事入口。
- 例如遇到《心经》：正确方向是“观自在菩萨深行般若时，先照见五蕴皆空；他把这次照见转向舍利子，用一层层开示把色空、六根六尘、十二因缘、四谛、无所得讲成一条放下挂碍的路，最后以咒语收束”。错误方向是“逐句解释五蕴、色空、无明、老死分别是什么意思”。

【输出形态】
- 严格按以下顺序输出：@最终书名、@最终简介、@目录、@导言、@正文。
- @最终简介写得像认真图书的封底简介，克制、清楚、有阅读质感，不要营销口号。
- @导言简短交代经文的进入方式和核心意味，不要提前总结成教义论文。
- @正文按原文内容自然分章。短经通常可以拆成 2-4 个正文短章；长佛经按全本顺序拆成足够多的故事单元/说法单元，不设固定章节上限。
- 正文要连贯、有故事感，但不要改成小说爽文、玄幻故事、宗教宣传口号或现代励志文。
- @正文必须比直译更有故事形态。短经通常可以拆成 2-4 个正文短章；每章要有连续段落，不要只给几句白话译文。
- 长佛经必须优先完整覆盖，而不是优先少章、短篇或好看。宁可多写几个不同小故事，也不能漏掉原文里的关键因缘、譬喻、问答和说法推进。
- 长佛经不能把一个故事单元拆成“故事章+寓意章”。如果是《百喻经》这类一则一喻的文本，每一则譬喻就是一个正式章节，章节内部同时完成故事和寓意收束。
- 每个故事单元都要写成完整、好读的小故事，不能写成几十字的梗概；以把动作、误判、结果和警醒讲清楚为准，讲清楚就自然收住，不要硬拉长。
- 每个正文短章都要有一个推进功能：进入修行或说法场面、照见或提出问题、层层开示、落到行动/心境/咒语收束。不要每章都写成概念释义。
- 不要加粗，不要代码块，不要元信息，不要解释过程；直接输出完整成稿。

【佛经原文】
{source_text}"""


_BUDDHIST_ARABIC_HEADING_RE = re.compile(r"^\s*@?\s*(\d{1,3})[.．、]\s*(.{1,48})\s*$")
_BUDDHIST_PAREN_HEADING_RE = re.compile(r"^\s*[（(]\s*(\d{1,3}|[一二三四五六七八九十百零〇两]{1,8})\s*[）)]\s*(.{1,48})\s*$")
_BUDDHIST_CHINESE_HEADING_RE = re.compile(
    r"^\s*(?:第\s*)?([一二三四五六七八九十百零〇两]{1,8})\s*(?:则|篇|喻)[：:、.．\s]*(.{0,48})\s*$"
)
_BUDDHIST_HEADING_BAD_PUNCT_RE = re.compile(r"[。！？!?；;]")


def _chinese_numeral_to_int(text: str) -> int | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        value = int(raw)
        return value if value > 0 else None
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if all(char in digits for char in raw):
        value = 0
        for char in raw:
            value = value * 10 + digits[char]
        return value if value > 0 else None
    units = {"十": 10, "百": 100}
    total = 0
    number = 0
    seen = False
    for char in raw:
        if char in digits:
            number = digits[char]
            seen = True
            continue
        unit = units.get(char)
        if unit is None:
            return None
        seen = True
        total += (number or 1) * unit
        number = 0
    if not seen:
        return None
    total += number
    return total if total > 0 else None


def _buddhist_heading_candidate(line: str) -> tuple[int, str] | None:
    stripped = (line or "").strip()
    if not stripped or len(stripped) > 72:
        return None
    if re.search(r"经卷第[一二三四五六七八九十百零〇两\d]+", stripped):
        return None
    match = _BUDDHIST_ARABIC_HEADING_RE.match(stripped)
    if not match:
        match = _BUDDHIST_PAREN_HEADING_RE.match(stripped)
    if not match:
        match = _BUDDHIST_CHINESE_HEADING_RE.match(stripped)
    if not match:
        return None
    number = _chinese_numeral_to_int(match.group(1))
    title = re.sub(r"\s+", " ", (match.group(2) or "").strip())
    title = title.strip("：:，,、.． ")
    if number is None or number > 300:
        return None
    if title and (_BUDDHIST_HEADING_BAD_PUNCT_RE.search(title) or len(title) > 48):
        return None
    if title and len(_CJK_RE.findall(title)) == 0:
        return None
    return number, title or f"第{number}则"


def _buddhist_cjk_count(text: str) -> int:
    return sum(1 for char in text or "" if "\u3400" <= char <= "\u9fff")


def split_buddhist_story_units(raw_text: str, *, original_title: object = None) -> list[BuddhistStoryUnit]:
    """Split numbered Buddhist parable collections into story units.

    This is intentionally conservative: when there is no reliable numbered run,
    the caller should use the normal single-piece Buddhist writer.
    """
    lines = classical_source_lines(raw_text)
    if not lines:
        return []

    candidates: list[tuple[int, int, str]] = []
    for line_index, line in enumerate(lines):
        candidate = _buddhist_heading_candidate(line)
        if candidate is None:
            continue
        number, title = candidate
        candidates.append((line_index, number, title))
    if len(candidates) < 12:
        return []

    runs: list[list[tuple[int, int, str]]] = []
    current: list[tuple[int, int, str]] = []
    for candidate in candidates:
        _, number, _ = candidate
        if not current:
            current = [candidate] if number == 1 else []
            continue
        previous_number = current[-1][1]
        if number == previous_number + 1:
            current.append(candidate)
            continue
        if len(current) >= 12:
            runs.append(current)
        current = [candidate] if number == 1 else []
    if len(current) >= 12:
        runs.append(current)
    if not runs:
        return []

    next_heading_by_line = {
        candidates[pos][0]: (candidates[pos + 1][0] if pos + 1 < len(candidates) else len(lines))
        for pos in range(len(candidates))
    }

    def run_body_chars(run: list[tuple[int, int, str]]) -> int:
        total = 0
        for line_index, _, _ in run:
            end = next_heading_by_line.get(line_index, len(lines))
            body = "\n".join(lines[line_index + 1 : end])
            total += _buddhist_cjk_count(body)
        return total

    best_run = max(runs, key=lambda run: (run_body_chars(run), len(run)))
    if len(best_run) < 20 and "百喻经" not in f"{original_title or ''}\n{raw_text[:500]}":
        return []

    units: list[BuddhistStoryUnit] = []
    for line_index, number, title in best_run:
        end = next_heading_by_line.get(line_index, len(lines))
        source_lines = [lines[line_index]] + lines[line_index + 1 : end]
        source = "\n".join(source_lines).strip()
        units.append(BuddhistStoryUnit(index=number, title=title, source=source))
    return units


__all__ = [
    "CLASSICAL_VARIANT_OPTIONS",
    "CLASSICAL_PARAGRAPH_MARKER_RE",
    "CLASSICAL_CHINESE_CATEGORY_IDS",
    "CLASSICAL_INTERLEAVED_CATEGORY",
    "CLASSICAL_REFERENCE_REWRITE_CATEGORY",
    "BUDDHIST_SCRIPTURE_STORY_CATEGORY",
    "BUDDHIST_SCRIPTURE_VARIANT",
    "BUDDHIST_SCRIPTURE_DEFAULT_TITLE",
    "BuddhistStoryUnit",
    "buddhist_scripture_final_title",
    "build_buddhist_scripture_story_source",
    "build_classical_dictionary_context",
    "build_classical_marked_source",
    "build_classical_prompt_source",
    "build_classical_structure_checklist",
    "classical_expected_paragraph_count",
    "classical_paragraph_marker",
    "classical_source_lines",
    "classical_variant_rules",
    "classical_variant_for_category",
    "classical_final_title",
    "get_classical_variant_meta",
    "normalize_classical_variant",
    "split_buddhist_story_units",
    "strip_classical_paragraph_markers",
]
