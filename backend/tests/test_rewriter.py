from __future__ import annotations

import asyncio
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.banned_rules import get_all_rules, scan_violations
from app.core.rewriter import has_zero_tolerance


def test_n_gram_check() -> None:
    assert has_zero_tolerance("他用力托住了她") == ["托住"]


def test_key_cases_still_detected() -> None:
    assert scan_violations("这不是简单的问题，而是结构性的问题。")
    assert scan_violations("他们看似在合作，实则各怀心思。")
    assert scan_violations("正常文本没有违规。") == []


def test_catalog_has_enough_rules() -> None:
    assert len(get_all_rules()) >= 35


def test_rewrite_simple() -> None:
    # 这个测试只验证物理拦截器的入口可导入；实际 LLM 重写在集成环境中执行。
    text = "这不是 A 而是 B。"
    assert scan_violations(text)


def test_rewrite_keeps_meaning_manual_review_case() -> None:
    # 保留给人工核对的语义案例，不在单元测试里依赖外部 LLM。
    text = "他们看似在合作，实则各怀心思。"
    assert scan_violations(text)


async def _smoke_force_rewrite() -> None:
    from app.core import rewriter

    draft = (
        "这不是简单的问题，而是结构性的问题。"
        "很多人一提到这个话题就沉默。"
        "我们把这条链条慢慢展开。"
        "也就是说，这只是开始。"
        "真正使项目进入正轨的是执行。"
    )
    async def fake_rewrite_single_sentence(
        bad_sentence: str,
        rule,
        *,
        retry: bool = False,
    ) -> str | None:
        rewritten = bad_sentence
        for forbidden in rule.forbidden_substrings:
            rewritten = rewritten.replace(forbidden, "")
        rewritten = rewritten.strip("，。； ")
        return rewritten or "这句话已重写"

    original = rewriter._rewrite_single_sentence
    rewriter._rewrite_single_sentence = fake_rewrite_single_sentence
    try:
        rewritten, stats = await rewriter.force_rewrite_all(draft)
    finally:
        rewriter._rewrite_single_sentence = original
    assert isinstance(rewritten, str)
    assert stats.total_hits >= 1


def test_full_pipeline_smoke() -> None:
    asyncio.run(_smoke_force_rewrite())
