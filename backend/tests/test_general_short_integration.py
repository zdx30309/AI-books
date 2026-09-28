"""通用短篇端到端接线：writer 前注入指纹、终稿后跑差异化审查。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.prompts.general_short_prompts import FINGERPRINT_TOKEN
from app.services import content_engine

RAW = (
    "你有没有想过，为什么越省钱反而越穷？我认识一位朋友，他每个月记三千笔账。"
    "2023年央行数据显示居民储蓄率上升。比如日本失落的三十年，比如韩国的高负债家庭。"
    "因为收入停滞，所以消费收缩，导致经济下行。再看德国制造业，它的中小企业一直很稳。"
    "建议你第一步先做预算，第二步记账，第三步复盘。"
) * 6

MANUSCRIPT = (
    "@最终书名 测试书名\n@最终简介 测试简介\n@目录\n1. 第一条\n2. 第二条\n"
    "@导言\n这是导言，提出全书的问题意识与阅读入口。\n@正文\n"
    "1. 第一条\n这里从完全不同的角度切入，讨论家庭账本与消费决策的关系。\n"
    "2. 第二条\n换一个独立案例，讨论储蓄行为与心理账户之间的联系，不沿用原稿的任何案例。\n"
)


def test_fingerprint_is_injected_into_the_writer_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []

    async def fake_writer(prompt: str, **_: object) -> str:
        captured.append(prompt)
        return MANUSCRIPT

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_writer)
    monkeypatch.setattr(content_engine, "polish_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "refine_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "format_book_text", lambda text, **_: text)

    asyncio.run(
        content_engine.generate_full_book(
            RAW, category="general_short", output_dir=tmp_path / "short"
        )
    )

    assert captured, "writer was never called"
    prompt = captured[0]
    assert FINGERPRINT_TOKEN not in prompt, "fingerprint token was not substituted"
    assert "论点单元序列" in prompt
    assert "严禁沿用此顺序" in prompt


def test_gate_writes_both_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "short"

    async def fake_writer(prompt: str, **_: object) -> str:
        return MANUSCRIPT

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_writer)
    monkeypatch.setattr(content_engine, "polish_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "refine_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "format_book_text", lambda text, **_: text)

    asyncio.run(content_engine.generate_full_book(RAW, category="general_short", output_dir=out))

    assert (out / "00_source_fingerprint.json").exists()
    assert (out / "05_differentiation_report.json").exists()
    report = json.loads((out / "05_differentiation_report.json").read_text(encoding="utf-8"))
    assert report["verdict"] in {"安全", "高危"}
    assert 0.0 <= report["composite"] <= 100.0
    fingerprint = json.loads((out / "00_source_fingerprint.json").read_text(encoding="utf-8"))
    assert fingerprint["argument_units"]
