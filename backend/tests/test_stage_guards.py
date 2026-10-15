from __future__ import annotations

import asyncio

import pytest

from app.core.config import settings
from app.services import content_engine as ce


def _book(chapters: dict[int, str], *, intro: str = "导言内容。" * 20) -> str:
    toc = "\n".join(f"{n}. 第{n}章标题" for n in chapters)
    body = "\n\n".join(f"@{n}. 第{n}章标题\n\n{text}" for n, text in chapters.items())
    return f"@最终书名\n测试书\n\n@最终简介\n简介。\n\n@目录\n{toc}\n\n@导言\n{intro}\n\n@正文\n{body}\n"


def _para(n: int) -> str:
    return "\n\n".join("这是一段用于测试篇幅保护的正文内容，句子完整。" * 4 for _ in range(n))


def test_chunk_validator_rejects_stray_markers_demotion_title_and_compression() -> None:
    src = "@3. 第三章\n\n" + _para(3)
    assert ce._standard_chunk_structure_issues(src, src) == []
    assert any("结构标记" in i for i in ce._standard_chunk_structure_issues(src, "@正文\n" + src))
    demoted = src.replace("@3. 第三章", "### 3. 第三章")
    issues = ce._standard_chunk_structure_issues(src, demoted)
    assert any("章节编号" in i for i in issues) and any("降级" in i for i in issues)
    assert any("一级标题" in i for i in ce._standard_chunk_structure_issues(src, "# 别的书名\n" + src))
    assert any("压缩" in i for i in ce._standard_chunk_structure_issues(src, src[: len(src) // 2]))


def test_stage_regression_detects_compression_and_short_chapter() -> None:
    source = _book({1: _para(12), 2: _para(12), 3: _para(12)})
    assert ce._standard_stage_regression_issues(source, source) == []
    shrunk = _book({1: _para(12), 2: _para(2), 3: _para(12)})
    issues = ce._standard_stage_regression_issues(source, shrunk)
    assert any("第 2 章" in i for i in issues)
    renumbered = source.replace("@3. 第3章标题", "### 3. 第3章标题")
    assert any("章节编号" in i for i in ce._standard_stage_regression_issues(source, renumbered))


def test_final_length_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "FINAL_LENGTH_GATE_ENABLED", True)
    short = _book({1: _para(2), 2: _para(30)})
    issues = ce.final_length_gate_issues(short, category="social_science")
    assert any("第 1 章" in i for i in issues)
    long_book = _book({n: _para(25) for n in range(1, 6)})
    assert ce.final_length_gate_issues(long_book, category="social_science") == []
    assert any("正文仅" in i for i in ce.final_length_gate_issues(_book({1: _para(25)}), category="social_science"))
    assert ce.final_length_gate_issues(short, category="healing_comic") == []


def test_stage_guards_reject_changed_title_with_unchanged_number() -> None:
    source = _book({1: _para(30)})
    polluted = source.replace("@1. 第1章标题", "@1. 名校仍有价值，但终极目标需要重排")
    assert "章节标题被改动" in ce._standard_chunk_structure_issues(source, polluted)
    assert "章节标题被改动" in ce._standard_stage_regression_issues(source, polluted)


def test_final_length_gate_rejects_long_body_without_chapters() -> None:
    source = _book({1: _para(180)}).replace("@1. 第1章标题", "")
    assert "正文缺少可识别的正文章节" in ce.final_length_gate_issues(source, category="popular_science")


def test_resume_rejects_compressed_final_without_rewriting_draft(tmp_path, monkeypatch) -> None:
    source = _book({1: _para(30), 2: _para(30), 3: _para(30)})
    for filename in ("01_writer.md", "02_polish.md", "03_refine.md"):
        (tmp_path / filename).write_text(source)
    (tmp_path / "04_format.md").write_text(_book({1: _para(2), 2: _para(2), 3: _para(2)}))
    calls = []

    async def no_writer(*args, **kwargs):
        raise AssertionError("saved draft must not be regenerated")

    def format_text(text, **kwargs):
        calls.append(text)
        return text

    monkeypatch.setattr(ce, "_route_writer_call", no_writer)
    monkeypatch.setattr(ce, "format_book_text", format_text)
    result = asyncio.run(ce.generate_full_book("原始资料", category="popular_science", output_dir=tmp_path))
    assert len(calls) == 1
    assert len(result) > len(source) * 0.9


def test_chapter_splitter_starts_chunks_at_boundaries_and_roundtrips() -> None:
    text = _book({1: _para(10), 2: _para(1), 3: _para(1)})
    chunks = ce._split_text_by_chapter_then_paragraph(text, target_chars=1000)
    for chunk in chunks:
        numbers = ce._STAGE_CHAPTER_NUM_RE.findall(chunk)
        # 每块要么不含章节标题，要么从章节/结构行开头
        if numbers:
            assert chunk.lstrip().startswith("@")
    assert "".join(c.replace("\n", "") for c in chunks) == text.replace("\n", "")


def test_invalid_chunk_is_rerun_then_kept_from_source() -> None:
    source = "@2. 第二章\n\n" + _para(3)
    calls = {"n": 0}

    def bad(text: str, **_: object) -> str:
        calls["n"] += 1
        return "@导言\n" + text[:50]

    result = asyncio.run(
        ce._polish_by_paragraph_chunks_strict(
            source,
            target_chars=5000,
            chunk_func=bad,
            splitter=ce._split_text_by_chapter_then_paragraph,
            chunk_validator=ce._standard_chunk_structure_issues,
            validation_retries=1,
            keep_source_on_validation_failure=True,
        )
    )
    assert calls["n"] == 2
    assert result == source.strip()
