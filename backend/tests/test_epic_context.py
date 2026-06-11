from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.services import deepseek, epic_model_router
from app.services.epic_context import compile_dynamic_context, contains_raw_reference_text
from app.services.epic_model_router import assemble_continuation_chunks
from app.services.epic_novel import _export_metadata_for_path


def _context_with_large_bible() -> dict:
    return {
        "project": {"title": "无名天书", "premise": "ancient mystery"},
        "chapterContract": {"chapter_number": 2, "must_execute": ["show evidence chain"]},
        "bibleSections": [{"sectionKey": f"s{i}", "content": "圣经内容" * 200} for i in range(20)],
        "worldBible": {"summary": "world"},
        "worldAtlas": {"regions": ["capital"]},
        "bureaucracy": [{"name": "Night Watch"}],
        "recentChapters": [{"chapterNumber": 1, "summary": "accepted", "endingHook": "hook"}],
        "referenceInspirationPack": {
            "cards": [{"id": "card-1", "category": "plot_structure", "function": "evidence-chain pressure", "rawReferenceText": "CARD RAW TEXT"}],
            "profile": {"corePleasureMechanisms": ["case reward"], "antiClicheRules": ["no raw copying"]},
        },
        "referenceSafety": {"rawTextIncluded": False, "allowedInputs": ["ReferencePatternCards", "ReferenceMechanismProfile"]},
        "rawReferenceText": "THIS MUST NOT ENTER PROMPT",
    }


def test_max_output_tokens_zero_is_not_artificial_app_cap(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_generate_text(prompt: str, *, model: str | None = None, max_output_tokens: int | None = None, **_: object) -> str:
        captured["prompt"] = prompt
        captured["model"] = model
        captured["max_output_tokens"] = max_output_tokens
        return "ok"

    monkeypatch.setattr(deepseek, "generate_text", fake_generate_text)

    assert epic_model_router._call_deepseek("probe", model="deepseek-v4-pro", max_output_tokens=0) == "ok"
    assert captured["max_output_tokens"] is None


def test_dynamic_context_reports_included_and_overflow_without_raw_reference_text() -> None:
    result = compile_dynamic_context(_context_with_large_bible(), forced_prompt_budget_tokens=900)
    metadata = result["metadata"]

    assert metadata["included"]
    assert metadata["overflowCount"] > 0
    assert metadata["rawReferenceTextIncluded"] is False
    assert contains_raw_reference_text(result["context"]) is False
    assert any(item["path"] == "bibleSections" for item in metadata["overflow"])


def test_reference_cards_and_profile_are_allowed_prompt_sources() -> None:
    result = compile_dynamic_context(_context_with_large_bible(), forced_prompt_budget_tokens=10_000)
    compiled = result["context"]

    assert compiled["referenceInspirationPack"]["cards"][0]["function"] == "evidence-chain pressure"
    assert compiled["referenceInspirationPack"]["profile"]["corePleasureMechanisms"] == ["case reward"]
    assert "ReferencePatternCards" in compiled["contextMetadata"]["allowedPromptSources"]
    assert "ReferenceMechanismProfile" in compiled["contextMetadata"]["allowedPromptSources"]


def test_raw_reference_book_text_is_never_used_in_generation_context() -> None:
    result = compile_dynamic_context(_context_with_large_bible(), forced_prompt_budget_tokens=10_000)
    rendered = str(result["context"])

    assert "THIS MUST NOT ENTER PROMPT" not in rendered
    assert "CARD RAW TEXT" not in rendered
    assert "[excluded raw reference text]" in rendered
    assert result["metadata"]["rawReferenceTextExcluded"] is True


def test_long_chapter_generation_can_continue_until_done_marker() -> None:
    calls: list[str] = []

    def next_chunk(accumulated: str) -> str:
        calls.append(accumulated)
        return " middle [[DONE]]"

    text = assemble_continuation_chunks("start", next_chunk, marker="[[DONE]]", max_chunks=3)

    assert text == "start middle"
    assert len(calls) == 1


def test_export_metadata_points_to_file_without_returning_content() -> None:
    out_dir = Path(".local") / "test_exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"large_bible_{uuid4().hex}.md"
    path.write_text("# Bible\n\n" + "正文" * 1000, encoding="utf-8")

    metadata = _export_metadata_for_path(path, "text/markdown; charset=utf-8", content_chars=2007)

    assert metadata["contentReturned"] is False
    assert metadata["path"] == str(path)
    assert metadata["bytes"] == path.stat().st_size
    assert "content" not in metadata
