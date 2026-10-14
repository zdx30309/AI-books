import difflib
import json
import re

from app.services.ai_or_finalizer import FinalizerHold, digest
from app.services.general_short_similarity import normalize

#: 模糊锚定：ASR 原稿常有赘字（如“老虎基金这。他更大”），模型摘录会写成通顺版
#: （“老虎基金他更大”）。逐字匹配失败时，允许在原文里按相似度回找最贴近的片段，
#: 但要求足够相似且锚点唯一，避免张冠李戴。
FUZZY_ANCHOR_MIN_RATIO = 0.85


def quote_key(text):
    positions = [index for index, char in enumerate(text) if not char.isspace() and char not in "。！？；，、：“”‘’（）《》【】"]
    return "".join(text[index] for index in positions), positions


def _fuzzy_anchor(needle, haystack, positions, text):
    """Locate the source span whose de-noised form best matches ``needle``."""
    matcher = difflib.SequenceMatcher(None, needle, haystack, autojunk=False)
    match = matcher.find_longest_match(0, len(needle), 0, len(haystack))
    if match.size < max(8, int(len(needle) * 0.5)):
        return None
    block = needle[match.a:match.a + match.size]
    if haystack.count(block) != 1:
        return None
    low = max(0, match.b - match.a)
    high = min(len(haystack), match.b + (len(needle) - match.a))
    if high <= low:
        return None
    window = haystack[low:high]
    if difflib.SequenceMatcher(None, needle, window, autojunk=False).ratio() < FUZZY_ANCHOR_MIN_RATIO:
        return None
    return text[positions[low]:positions[high - 1] + 1]


def literal_quote(quote, text):
    if normalize(quote) in normalize(text):
        return quote
    needle, _positions = quote_key(quote)
    haystack, positions = quote_key(text)
    if needle and haystack.count(needle) == 1:
        start = haystack.index(needle)
        return text[positions[start]:positions[start + len(needle) - 1] + 1]
    if needle:
        fuzzy = _fuzzy_anchor(needle, haystack, positions, text)
        if fuzzy:
            return fuzzy
    raise FinalizerHold("相似度摘录无法唯一锚定原文，响应已保存，不自动重放")


def verified_payload(source, draft, payload, directory, response_path):
    result = json.loads(json.dumps(payload, ensure_ascii=False))
    changes = []
    patch_path = directory / "audit_evidence_patch.json"
    if patch_path.exists():
        patch = json.loads(patch_path.read_text())
        expected = {"source_hash": digest(source), "draft_hash": digest(draft), "response_hash": digest(response_path.read_text())}
        if any(patch.get(key) != value for key, value in expected.items()):
            raise FinalizerHold("引用核对补丁与两稿/原始响应hash不一致")
        for edit in patch.get("edits", []):
            table, index = edit.get("table"), edit.get("index")
            if table not in {"units", "data_points", "cases"} or type(index) is not int or not 0 <= index < len(result.get(table, [])):
                raise FinalizerHold("引用核对补丁定位无效")
            row = result[table][index]
            before, after = edit.get("from"), edit.get("to")
            if row.get("source_quote") != before or not isinstance(after, str) or not edit.get("reason"):
                raise FinalizerHold("引用核对补丁没有绑定原始摘录及原因")
            numbers = lambda value: re.findall(r"\d+(?:[.,]\d+)*%?", value)
            if numbers(before) != numbers(after) or normalize(after) not in normalize(source):
                raise FinalizerHold("引用核对不得改动数值或编造原稿片段")
            row["source_quote"] = after
            changes.append(edit)
    for table in ("units", "data_points", "cases"):
        rows = result.get(table)
        if not isinstance(rows, list):
            raise FinalizerHold("相似度依据表不完整")
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise FinalizerHold("相似度依据记录无效")
            for field, text in (("source_quote", source), ("draft_quote", draft)):
                quote = row.get(field)
                if not isinstance(quote, str) or not quote.strip():
                    continue
                actual = literal_quote(quote, text)
                if actual != quote:
                    row[field] = actual
                    changes.append({"table": table, "index": index, "field": field, "from": quote, "to": actual,
                                    "reason": "已锚定原文逐字片段（含 ASR 赘字/中文句读差异）；未改变判定或计数"})
    return result, changes
