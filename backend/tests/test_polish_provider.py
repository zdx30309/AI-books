from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services import claude, gemini, polish_provider
from app.utils import paid_api_guard


def test_polish_provider_routes_only_configured_stage_to_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "BACKEND_POLISH_PROVIDER", "claude")
    monkeypatch.setattr(settings, "BACKEND_REFINE_PROVIDER", "gemini")
    calls: list[str] = []

    monkeypatch.setattr(claude, "polish_book_text", lambda text, **_: calls.append(f"claude:{text}") or "c")
    monkeypatch.setattr(gemini, "polish_book_text", lambda text, **_: calls.append(f"gemini:{text}") or "g")
    monkeypatch.setattr(gemini, "refine_book_text", lambda text, **_: calls.append(f"gemini-refine:{text}") or "r")

    assert polish_provider.polish_book_text("one") == "c"
    assert polish_provider.refine_book_text("two") == "r"
    assert calls == ["claude:one", "gemini-refine:two"]


def test_invalid_polish_provider_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "BACKEND_POLISH_PROVIDER", "openai")
    with pytest.raises(ValueError, match="只支持 gemini 或 claude"):
        polish_provider.polish_book_text("text")


def test_claude_relay_submits_once_and_preserves_system_instruction(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, object]] = []

    class Completions:
        def create(self, **kwargs: object):
            captured.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="处理后的正文"))]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    monkeypatch.setattr(claude, "_get_client", lambda _timeout: fake_client)
    monkeypatch.setattr(claude, "distributed_semaphore", lambda *_a, **_k: nullcontext())
    monkeypatch.setattr(claude.paid_api_guard, "guard", lambda _provider: None)
    monkeypatch.setattr(claude.paid_api_guard, "trip_if_quota", lambda *_a: False)
    monkeypatch.setattr(settings, "CLAUDE_MODEL", "claude-5-opus")

    result = claude.generate_text(
        "正文",
        system_instruction="只做轻微润色",
        request_timeout_seconds=60,
    )

    assert result == "处理后的正文"
    assert len(captured) == 1
    assert captured[0]["model"] == "claude-5-opus"
    assert captured[0]["messages"] == [
        {"role": "system", "content": "只做轻微润色"},
        {"role": "user", "content": "正文"},
    ]


def test_claude_delivery_failure_is_not_retried_or_switched(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    class Completions:
        def create(self, **_kwargs: object):
            nonlocal calls
            calls += 1
            raise RuntimeError("read timeout after upstream accepted request")

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    monkeypatch.setattr(claude, "_get_client", lambda _timeout: fake_client)
    monkeypatch.setattr(claude, "distributed_semaphore", lambda *_a, **_k: nullcontext())
    monkeypatch.setattr(claude.paid_api_guard, "guard", lambda _provider: None)
    monkeypatch.setattr(claude.paid_api_guard, "trip_if_quota", lambda *_a: False)

    with pytest.raises(claude.ClaudeDeliveryUncertainError):
        claude.generate_text("正文", retries=5)

    assert calls == 1
