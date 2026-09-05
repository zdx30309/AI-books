from __future__ import annotations

import asyncio
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import content_engine, gemini


BASE_MANUSCRIPT = """@最终书名
二润断点测试
@最终简介
用于验证二润阶段必须完整执行并落盘。
@目录
1. 第一章
2. 第二章
@导言
这是测试导言。
@正文
@1. 第一章
第一章正文保留全部内容。
@2. 第二章
第二章正文保留全部内容。
"""


def test_gemini_nonempty_max_tokens_response_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class Models:
        def generate_content(self, **_kwargs: object):
            nonlocal calls
            calls += 1
            return SimpleNamespace(
                text="这是已经被截断的半截稿",
                candidates=[SimpleNamespace(finish_reason="MAX_TOKENS")],
            )

    monkeypatch.setattr(gemini.settings, "GOOGLE_GENAI_USE_VERTEXAI", False)
    monkeypatch.setattr(gemini, "_resolve_google_api_key", lambda: "test-key")
    monkeypatch.setattr(
        gemini,
        "_get_api_key_client",
        lambda _timeout: SimpleNamespace(models=Models()),
    )
    monkeypatch.setattr(gemini, "_gemini_call_gate", lambda: nullcontext())
    monkeypatch.setattr(gemini, "_google_proxy_env", lambda: nullcontext())
    monkeypatch.setattr(gemini.paid_api_guard, "guard", lambda _provider: None)
    monkeypatch.setattr(gemini.paid_api_guard, "trip_if_quota", lambda *_args: False)

    with pytest.raises(gemini.GeminiMaxTokensError) as exc_info:
        gemini.generate_text("prompt", retries=3)

    assert calls == 1
    assert exc_info.value.partial_text == "这是已经被截断的半截稿"


def _patch_pipeline_for_checkpoint_test(
    monkeypatch: pytest.MonkeyPatch,
    *,
    output_dir: Path,
    refine_result: str | None = None,
    format_result: str | None = None,
    format_callback=None,
) -> list[str]:
    calls: list[str] = []

    async def fake_writer(_prompt: str, **_kwargs: object) -> str:
        calls.append("writer")
        return BASE_MANUSCRIPT

    async def fake_polish_chunks(text: str, **_kwargs: object) -> str:
        calls.append("polish")
        return text + "\n一润标记"

    def fake_refine(text: str, **_kwargs: object) -> str:
        calls.append("refine")
        return refine_result if refine_result is not None else text + "\n二润标记"

    def fake_format(text: str, **_kwargs: object) -> str:
        calls.append("format")
        if format_callback is not None:
            format_callback(text)
        return format_result if format_result is not None else text

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_writer)
    monkeypatch.setattr(content_engine, "_polish_by_paragraph_chunks_strict", fake_polish_chunks)
    monkeypatch.setattr(content_engine, "refine_book_text", fake_refine)
    monkeypatch.setattr(content_engine, "format_book_text", fake_format)
    return calls


def test_refine_checkpoint_is_written_before_format_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "checkpoint"
    observed: dict[str, str] = {}

    def inspect_checkpoint(text: str) -> None:
        path = output_dir / "03_refine.md"
        metadata_path = output_dir / ".03_refine.md.meta.json"
        assert path.is_file()
        assert metadata_path.is_file()
        observed["file"] = path.read_text(encoding="utf-8")
        observed["input"] = text

    calls = _patch_pipeline_for_checkpoint_test(
        monkeypatch,
        output_dir=output_dir,
        format_callback=inspect_checkpoint,
    )

    result = asyncio.run(
        content_engine.generate_full_book(
            BASE_MANUSCRIPT,
            category="social_science",
            output_dir=output_dir,
        )
    )

    assert calls == ["writer", "polish", "refine", "format"]
    assert observed["file"].strip() == observed["input"].strip()
    assert "二润标记" in result
    metadata = json.loads((output_dir / ".03_refine.md.meta.json").read_text(encoding="utf-8"))
    assert metadata["stage"] == "refine"
    assert metadata["status"] == "complete"
    assert metadata["result_kind"] == "generated"


def test_compressed_whole_book_refine_uses_chunked_fallback_and_persists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "fallback"
    long_manuscript = BASE_MANUSCRIPT.replace(
        "第一章正文保留全部内容。",
        "第一章正文保留全部内容。" * 700,
    ).replace(
        "第二章正文保留全部内容。",
        "第二章正文保留全部内容。" * 700,
    )
    calls: list[str] = []
    chunked_kwargs: dict[str, object] = {}

    async def fake_writer(_prompt: str, **_kwargs: object) -> str:
        calls.append("writer")
        return long_manuscript

    async def fake_polish_chunks(text: str, **_kwargs: object) -> str:
        calls.append("polish")
        return text

    def fake_whole_refine(text: str, **_kwargs: object) -> str:
        calls.append("whole_refine")
        return text[:500]

    async def fake_chunked_refine(text: str, **_kwargs: object) -> str:
        calls.append("chunked_refine")
        chunked_kwargs.update(_kwargs)
        return text + "\n分块二润完成"

    def fake_format(text: str, **_kwargs: object) -> str:
        calls.append("format")
        assert (output_dir / "03_refine.md").is_file()
        assert (output_dir / "03_refine.md").read_text(encoding="utf-8").strip() == text.strip()
        return text

    monkeypatch.setattr(content_engine, "_route_writer_call", fake_writer)
    monkeypatch.setattr(content_engine, "_polish_by_paragraph_chunks_strict", fake_polish_chunks)
    monkeypatch.setattr(content_engine, "refine_book_text", fake_whole_refine)
    monkeypatch.setattr(content_engine, "_refine_by_paragraph_chunks_strict", fake_chunked_refine)
    monkeypatch.setattr(content_engine, "format_book_text", fake_format)

    result = asyncio.run(
        content_engine.generate_full_book(
            long_manuscript,
            category="social_science",
            output_dir=output_dir,
        )
    )

    assert calls == ["writer", "polish", "whole_refine", "chunked_refine", "format"]
    assert chunked_kwargs["chunk_func"] is content_engine._refine_book_text_without_output_cap
    assert "分块二润完成" in result
    assert (output_dir / "03_refine.md").stat().st_size > 4_000


def test_refine_source_change_invalidates_cached_checkpoint(tmp_path: Path) -> None:
    output_dir = tmp_path / "stale"
    prompt = "二润 prompt"
    source_one = BASE_MANUSCRIPT
    source_two = BASE_MANUSCRIPT + "\n来源已变化。"
    content_engine._persist_refine_stage(  # noqa: SLF001
        output_dir,
        source_one + "\n二润标记",
        source_text=source_one,
        system_instruction=prompt,
        category="social_science",
    )

    assert content_engine._refine_stage_cache_issue(  # noqa: SLF001
        output_dir,
        source_one + "\n二润标记",
        source_text=source_one,
        system_instruction=prompt,
        category="social_science",
    ) is None
    issue = content_engine._refine_stage_cache_issue(  # noqa: SLF001
        output_dir,
        source_one + "\n二润标记",
        source_text=source_two,
        system_instruction=prompt,
        category="social_science",
    )
    assert issue is not None
    assert "来源稿已变化" in issue


def test_refine_integrity_allows_normalized_duplicate_chapter_markers() -> None:
    source = BASE_MANUSCRIPT.replace(
        "@2. 第二章\n第二章正文保留全部内容。",
        "@2. 第二章\n第二章正文保留全部内容。\n\n"
        "@1. 第一章内部小标题\n第一章正文保留全部内容。",
    )
    candidate = BASE_MANUSCRIPT.replace(
        "@1. 第一章\n第一章正文保留全部内容。",
        "1. 第一章\n第一章正文保留全部内容。\n第一章正文保留全部内容。",
    ).replace(
        "@2. 第二章\n第二章正文保留全部内容。",
        "2. 第二章\n第二章正文保留全部内容。",
    )

    assert content_engine._refine_output_integrity_issue(  # noqa: SLF001
        source,
        candidate,
        category="social_science",
    ) is None


def test_refine_integrity_rejects_a_missing_chapter_number() -> None:
    candidate = BASE_MANUSCRIPT.replace(
        "@1. 第一章\n第一章正文保留全部内容。\n",
        "",
    )

    issue = content_engine._refine_output_integrity_issue(  # noqa: SLF001
        BASE_MANUSCRIPT,
        candidate,
        category="social_science",
    )

    assert issue is not None
    assert "缺少原稿章节号" in issue


def test_finance_normalize_restores_plain_catalog_chapter_markers() -> None:
    source = """@最终书名
财经章节标记测试
@最终简介
用于验证目录匹配的正式章节不会被格式清洗误判为小标题。
@目录
1. 第一章
2. 第二章
@导言
这是测试导言。
@正文
@1. 第一章
第一章完整正文。
@2. 第二章
第二章完整正文。
"""
    provider_output = source.replace("@1. 第一章", "1. 第一章").replace(
        "@2. 第二章", "2. 第二章"
    )

    normalized = content_engine.normalize_final_markdown(
        provider_output,
        category="finance_technology_ai_optimized",
    )

    assert "@1. 第一章" in normalized
    assert "@2. 第二章" in normalized
    assert content_engine._stage_chapter_numbers(normalized) == [1, 2]  # noqa: SLF001
    assert content_engine._refine_output_integrity_issue(  # noqa: SLF001
        source,
        normalized,
        category="finance_technology_ai_optimized",
    ) is None


def test_catalog_marker_restore_does_not_promote_an_incomplete_numbered_list() -> None:
    source = BASE_MANUSCRIPT
    incomplete = source.replace("@1. 第一章", "1. 第一章").replace(
        "@2. 第二章\n第二章正文保留全部内容。", "1. 章内清单\n第二章正文保留全部内容。"
    )

    normalized = content_engine.normalize_final_markdown(
        incomplete,
        category="finance_technology_ai_optimized",
    )

    assert "@2. 第二章" not in normalized
    issue = content_engine._refine_output_integrity_issue(  # noqa: SLF001
        source,
        normalized,
        category="finance_technology_ai_optimized",
    )
    assert issue is not None
    assert "缺少原稿章节号" in issue


def test_refine_write_failure_blocks_format_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "write_failure"
    calls = _patch_pipeline_for_checkpoint_test(monkeypatch, output_dir=output_dir)
    real_atomic_write = content_engine._atomic_write_stage_text

    def fail_refine_write(path: Path, content: str) -> None:
        if path.name == "03_refine.md":
            raise OSError("simulated disk failure")
        real_atomic_write(path, content)

    monkeypatch.setattr(content_engine, "_atomic_write_stage_text", fail_refine_write)

    with pytest.raises(content_engine.StageArtifactWriteError):
        asyncio.run(
            content_engine.generate_full_book(
                BASE_MANUSCRIPT,
                category="social_science",
                output_dir=output_dir,
            )
        )

    assert calls == ["writer", "polish", "refine"]
    assert not (output_dir / "04_format.md").exists()


def test_truncated_format_falls_back_to_complete_refine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "truncated_format"
    truncated = BASE_MANUSCRIPT.replace(
        "@2. 第二章\n第二章正文保留全部内容。\n",
        "",
    )
    calls = _patch_pipeline_for_checkpoint_test(
        monkeypatch,
        output_dir=output_dir,
        format_result=truncated,
    )

    result = asyncio.run(
        content_engine.generate_full_book(
            BASE_MANUSCRIPT,
            category="social_science",
            output_dir=output_dir,
        )
    )

    assert calls == ["writer", "polish", "refine", "format"]
    assert "@2. 第二章" in result
    assert "二润标记" in result
    assert (output_dir / "04_format_rejected_incomplete.md").exists()
    assert (output_dir / "04_format.md").read_text(encoding="utf-8").strip() == result.strip()
