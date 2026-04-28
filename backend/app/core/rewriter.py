from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
from typing import Iterable

from app.core.banned_rules import BannedRule, get_all_rules, scan_violations

logger = logging.getLogger(__name__)

ZERO_TOLERANCE_TOKENS = (
    "托住",
    "拧到了一起",
    "也就是说，",
    "得能滴出水",
    "把这条链条慢慢展开",
    "把话说透",
    "把话说得再直白",
    "我们的目标很直接",
    "之所以值得写",
)


@dataclass
class RewriteStats:
    total_hits: int = 0
    rewritten_success: int = 0
    rewritten_after_retry: int = 0
    kept_original: int = 0
    rules_triggered: dict[str, int] = field(default_factory=dict)


def has_zero_tolerance(text: str) -> list[str]:
    """纯 Python 扫描零容忍短语，返回命中列表。"""
    return [t for t in ZERO_TOLERANCE_TOKENS if t in text]


def _normalize_sentence(sentence: str) -> str:
    return sentence.strip().strip('"').strip("'").strip("「").strip("」")


def _build_prompt(bad_sentence: str, rule: BannedRule) -> str:
    forbidden = ", ".join(f"「{s}」" for s in rule.forbidden_substrings)
    return f"""你是中文改写工具。请重写下面这句话，保持原意不变。

【硬性要求】
- 重写结果必须**不含**以下任何字串：{forbidden}
- {rule.rewrite_instruction}
- 只输出改写结果，不要任何解释、不要引号、不要前后缀
- 改写后字数与原句相差不超过 20%

【原句】
{bad_sentence}

【改写结果】
"""


def _contains_forbidden(text: str, rule: BannedRule) -> bool:
    return any(bad in text for bad in rule.forbidden_substrings)


async def _rewrite_single_sentence(
    bad_sentence: str,
    rule: BannedRule,
    *,
    retry: bool = False,
) -> str | None:
    """把违规句喂给低温 LLM 重写。返回 None 表示重写失败。"""
    from app.services.content_engine import _chat

    temperature = 0.0 if retry else 0.1
    prompt = _build_prompt(bad_sentence, rule)
    logger.info("[%s] 发送单句重写请求 [retry=%s]", rule.rule_id, retry)
    rewritten = await _chat(
        prompt,
        json_mode=False,
        temperature=temperature,
        stream=False,
    )
    rewritten = _normalize_sentence(rewritten)

    if _contains_forbidden(rewritten, rule):
        logger.warning("[%s] 重写结果仍含禁词，判定失败", rule.rule_id)
        return None
    if not rewritten or len(rewritten) < 4:
        logger.warning("[%s] 重写结果过短或为空，判定失败", rule.rule_id)
        return None
    return rewritten


async def force_rewrite_all(text: str) -> tuple[str, RewriteStats]:
    """扫描文本，逐规则处理违规并物理重写。"""
    stats = RewriteStats()
    rules = get_all_rules()

    for rule in rules:
        matches = [m.group(0).strip() for m in rule.detector_regex.finditer(text)]
        if not matches:
            continue

        stats.rules_triggered[rule.rule_id] = len(matches)
        stats.total_hits += len(matches)
        logger.info("[%s] 命中 %d 处违规，开始逐句重写", rule.rule_id, len(matches))

        for bad_sentence in matches:
            bad_sentence = _normalize_sentence(bad_sentence)
            if not bad_sentence:
                continue

            rewritten = await _rewrite_single_sentence(bad_sentence, rule, retry=False)
            if rewritten is None:
                rewritten = await _rewrite_single_sentence(bad_sentence, rule, retry=True)
                if rewritten is not None:
                    stats.rewritten_after_retry += 1
            else:
                stats.rewritten_success += 1

            if rewritten is not None:
                text = text.replace(bad_sentence, rewritten, 1)
                logger.info(
                    "[%s] 重写成功: [%s...] → [%s...]",
                    rule.rule_id,
                    bad_sentence[:30],
                    rewritten[:30],
                )
            else:
                stats.kept_original += 1
                logger.warning("[%s] 重写失败，保留原句: %s", rule.rule_id, bad_sentence[:50])

    return text, stats


__all__ = [
    "RewriteStats",
    "force_rewrite_all",
    "has_zero_tolerance",
    "_rewrite_single_sentence",
]
