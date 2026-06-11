"""Dynamic context compilation for Epic Novel prompts.

The compiler treats ``0`` limits as "no fixed Epic business cap" while still
respecting the selected provider/model context window. Overflow is recorded
explicitly so large context is never silently truncated.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.services.epic_model_router import EpicModelSelection, estimate_tokens, resolve_model


RAW_REFERENCE_KEYS = {
    "raw_reference_text",
    "rawReferenceText",
    "source_text",
    "sourceText",
    "book_text",
    "bookText",
    "raw_text",
    "rawText",
}


@dataclass(frozen=True)
class ContextBlock:
    path: str
    priority: int
    value: Any
    strategy: str = "full"


def effective_context_window_tokens(selection: EpicModelSelection | None = None) -> int:
    configured = int(settings.EPIC_CONTEXT_WINDOW_TOKENS or 0)
    if configured > 0:
        return configured
    selection = selection or resolve_model("author")
    provider = (selection.provider or "").lower()
    model = (selection.model or "").lower()
    if "gpt-5" in model or provider.startswith("openai"):
        return 400_000
    if "deepseek" in provider or "deepseek" in model:
        return 128_000
    return 128_000


def prompt_budget_tokens(selection: EpicModelSelection | None = None) -> int:
    ratio = float(settings.EPIC_CONTEXT_PROMPT_BUDGET_RATIO or 0.75)
    ratio = min(max(ratio, 0.1), 0.95)
    return max(1_000, int(effective_context_window_tokens(selection) * ratio))


def contains_raw_reference_text(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in RAW_REFERENCE_KEYS and item:
                if item == "[excluded raw reference text]":
                    continue
                return True
            if contains_raw_reference_text(item):
                return True
    elif isinstance(value, list):
        return any(contains_raw_reference_text(item) for item in value)
    return False


def sanitize_prompt_value(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if str(key) in RAW_REFERENCE_KEYS:
                cleaned[key] = "[excluded raw reference text]"
            else:
                cleaned[key] = sanitize_prompt_value(item)
        return cleaned
    if isinstance(value, list):
        return [sanitize_prompt_value(item) for item in value]
    return value


def _token_count(value: Any) -> int:
    return estimate_tokens(json.dumps(value, ensure_ascii=False, default=str))


def _summarize_overflow(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        return {
            "kind": "list",
            "count": len(value),
            "sampleKeys": sorted({key for item in value[:5] if isinstance(item, dict) for key in item.keys()})[:12],
        }
    if isinstance(value, dict):
        return {"kind": "object", "keys": sorted(map(str, value.keys()))[:24]}
    if isinstance(value, str):
        return {"kind": "text", "chars": len(value)}
    return {"kind": type(value).__name__}


def _block(path: str, priority: int, value: Any, strategy: str = "full") -> ContextBlock:
    return ContextBlock(path=path, priority=priority, value=sanitize_prompt_value(value), strategy=strategy)


def build_context_blocks(context: dict[str, Any]) -> list[ContextBlock]:
    pack = context.get("referenceInspirationPack") or {}
    return [
        _block("project", 100, context.get("project") or {}),
        _block("chapterContract", 100, context.get("chapterContract") or {}),
        _block("contextEvidence", 95, context.get("contextEvidence") or {}),
        _block("referenceSafety", 95, context.get("referenceSafety") or {}),
        _block("recentChapters", 90, context.get("recentChapters") or []),
        _block("worldBible", 85, context.get("worldBible") or {}),
        _block("worldAtlas", 85, context.get("worldAtlas") or {}),
        _block("bureaucracy", 85, context.get("bureaucracy") or []),
        _block("powerSystem", 80, context.get("powerSystem") or {}),
        _block("globalSpine", 80, context.get("globalSpine") or {}),
        _block("volumeContract", 80, context.get("volumeContract") or {}),
        _block("latestCharacterStates", 78, context.get("latestCharacterStates") or []),
        _block("currentPowerLedger", 78, context.get("currentPowerLedger") or []),
        _block("latestObjectStates", 76, context.get("latestObjectStates") or []),
        _block("latestFactionStates", 76, context.get("latestFactionStates") or []),
        _block("latestInstitutionStates", 76, context.get("latestInstitutionStates") or []),
        _block("activeForeshadows", 74, context.get("activeForeshadows") or []),
        _block("activeReaderQuestions", 74, context.get("activeReaderQuestions") or []),
        _block("referenceInspirationPack.profile", 72, pack.get("profile") or {}),
        _block("referenceInspirationPack.cards", 70, pack.get("cards") or []),
        _block("bibleSections", 68, context.get("bibleSections") or []),
    ]


def compile_dynamic_context(
    context: dict[str, Any],
    *,
    role: str = "author",
    prompt_overhead_tokens: int = 2_000,
    forced_prompt_budget_tokens: int | None = None,
) -> dict[str, Any]:
    selection = resolve_model(role)  # type: ignore[arg-type]
    budget = forced_prompt_budget_tokens or prompt_budget_tokens(selection)
    available = max(1_000, budget - max(0, prompt_overhead_tokens))
    compiled: dict[str, Any] = {}
    included: list[dict[str, Any]] = []
    overflow: list[dict[str, Any]] = []
    used = 0

    for block in sorted(build_context_blocks(context), key=lambda item: item.priority, reverse=True):
        if isinstance(block.value, list):
            selected: list[Any] = []
            selected_tokens = 2
            overflow_count = 0
            overflow_start = 0
            for index, item in enumerate(block.value):
                item_tokens = _token_count(item)
                if used + selected_tokens + item_tokens <= available:
                    selected.append(item)
                    selected_tokens += item_tokens
                    continue
                overflow_count = len(block.value) - index
                overflow_start = index
                break
            if selected:
                _assign_path(compiled, block.path, selected)
                included.append(
                    {
                        "path": block.path,
                        "tokens": selected_tokens,
                        "strategy": "partial-list" if overflow_count else block.strategy,
                        "priority": block.priority,
                        "includedCount": len(selected),
                    }
                )
                used += selected_tokens
            if overflow_count:
                overflow.append(
                    {
                        "path": block.path,
                        "tokens": None,
                        "priority": block.priority,
                        "reason": "provider_context_window_budget",
                        "excludedCount": overflow_count,
                        "firstExcludedIndex": overflow_start,
                        "summary": _summarize_overflow(block.value[overflow_start:]),
                    }
                )
            continue
        tokens = _token_count(block.value)
        if used + tokens <= available:
            _assign_path(compiled, block.path, block.value)
            included.append({"path": block.path, "tokens": tokens, "strategy": block.strategy, "priority": block.priority})
            used += tokens
            continue
        overflow.append(
            {
                "path": block.path,
                "tokens": tokens,
                "priority": block.priority,
                "reason": "provider_context_window_budget",
                "summary": _summarize_overflow(block.value),
            }
        )

    metadata = {
        "compiler": "DynamicContextCompiler",
        "rule": "0 means no fixed Epic business-layer cap; provider context window still applies.",
        "provider": selection.provider,
        "model": selection.model,
        "effectiveContextWindowTokens": effective_context_window_tokens(selection),
        "promptBudgetTokens": budget,
        "promptOverheadTokens": prompt_overhead_tokens,
        "includedTokens": used,
        "included": included,
        "overflow": overflow,
        "overflowCount": len(overflow),
        "rawReferenceTextIncluded": contains_raw_reference_text(compiled),
        "rawReferenceTextExcluded": contains_raw_reference_text(context),
        "allowedPromptSources": ["approved summaries", "ReferencePatternCards", "ReferenceMechanismProfile", "accepted state ledgers"],
    }
    compiled["contextMetadata"] = metadata
    return {"context": compiled, "metadata": metadata}


def _assign_path(target: dict[str, Any], dotted_path: str, value: Any) -> None:
    parts = dotted_path.split(".")
    cursor = target
    for part in parts[:-1]:
        next_value = cursor.get(part)
        if not isinstance(next_value, dict):
            next_value = {}
            cursor[part] = next_value
        cursor = next_value
    cursor[parts[-1]] = value
