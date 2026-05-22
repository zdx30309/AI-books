"""Classical Chinese prompt helpers and local dictionary context."""
from __future__ import annotations

import json
import logging
import lzma
import re
from collections import Counter
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
}

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_PUA_MARKER_RE = re.compile(r"\{\[[0-9a-fA-F]+\]\}")
_LINK_MARK_RE = re.compile(r"[`~]")


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


def build_classical_prompt_source(raw_text: str, *, variant: object = None) -> str:
    meta = get_classical_variant_meta(variant)
    dictionary_context = build_classical_dictionary_context(raw_text)
    return f"""【古文白话总方向】
形似神似，活灵活现。必须在故事自己的基础上，把原文的骨架、情节、人物关系、语气和意味都保住，同时让现代读者读起来顺畅、自然、有画面。不要把古文改成另一个故事，也不要为了好看而擅自加戏。

【细分方向】
{meta["label"]}：{meta["description"]}

【分类翻译要求】
{classical_variant_rules(meta["id"])}

【古汉语词典摘录】
{dictionary_context}

【待处理原文】
{raw_text.strip()}"""


__all__ = [
    "CLASSICAL_VARIANT_OPTIONS",
    "build_classical_dictionary_context",
    "build_classical_prompt_source",
    "classical_variant_rules",
    "get_classical_variant_meta",
    "normalize_classical_variant",
]
