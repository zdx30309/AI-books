from __future__ import annotations

from contextlib import nullcontext

import httpx
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

    class Client:
        def post(self, url: str, *, headers: dict[str, str], json: dict[str, object]):
            captured.append({"url": url, "headers": headers, "json": json})
            return httpx.Response(
                200,
                json={"content": [{"type": "text", "text": "处理后的正文"}]},
                request=httpx.Request("POST", url),
            )

    fake_client = Client()
    monkeypatch.setattr(claude, "_get_client", lambda _timeout: fake_client)
    monkeypatch.setattr(claude, "distributed_semaphore", lambda *_a, **_k: nullcontext())
    monkeypatch.setattr(claude.paid_api_guard, "guard", lambda _provider: None)
    monkeypatch.setattr(claude.paid_api_guard, "trip_if_quota", lambda *_a: False)
    monkeypatch.setattr(settings, "CLAUDE_MODEL", "claude-opus-5")
    monkeypatch.setattr(settings, "CLAUDE_API_KEY", "test-relay-key")
    monkeypatch.setattr(settings, "CLAUDE_ANTHROPIC_BETA", "context-1m-2025-08-07")

    result = claude.generate_text(
        "正文",
        system_instruction="只做轻微润色",
        request_timeout_seconds=60,
    )

    assert result == "处理后的正文"
    assert len(captured) == 1
    assert captured[0]["url"] == "https://anyrouter.top/v1/messages"
    assert captured[0]["headers"]["x-api-key"]
    assert captured[0]["headers"]["anthropic-version"] == "2023-06-01"
    assert captured[0]["headers"]["anthropic-beta"] == "context-1m-2025-08-07"
    assert captured[0]["json"]["model"] == "claude-opus-5"
    assert captured[0]["json"]["system"] == "只做轻微润色"
    assert captured[0]["json"]["messages"] == [{"role": "user", "content": "正文"}]


def test_claude_relay_rejects_non_claude_model_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def forbidden_client(_timeout: int):
        nonlocal calls
        calls += 1
        raise AssertionError("non-Claude model must be rejected before creating a client")

    monkeypatch.setattr(claude, "_get_client", forbidden_client)
    with pytest.raises(claude.ClaudeRequestRejectedError, match="non-Claude model"):
        claude.generate_text("正文", model="gpt-5.6-sol")
    assert calls == 0


def test_claude_delivery_failure_is_not_retried_or_switched(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    class Client:
        def post(self, *_args: object, **_kwargs: object):
            nonlocal calls
            calls += 1
            raise RuntimeError("read timeout after upstream accepted request")

    fake_client = Client()
    monkeypatch.setattr(claude, "_get_client", lambda _timeout: fake_client)
    monkeypatch.setattr(claude, "distributed_semaphore", lambda *_a, **_k: nullcontext())
    monkeypatch.setattr(claude.paid_api_guard, "guard", lambda _provider: None)
    monkeypatch.setattr(claude.paid_api_guard, "trip_if_quota", lambda *_a: False)

    with pytest.raises(claude.ClaudeDeliveryUncertainError):
        claude.generate_text("正文", retries=5)

    assert calls == 1


def test_claude_explicit_permission_rejection_is_not_marked_delivery_uncertain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PermissionError(Exception):
        status_code = 403

        def __str__(self) -> str:
            return "PermissionDenied: model is not enabled"

    class Client:
        def post(self, *_args: object, **_kwargs: object):
            raise PermissionError()

    fake_client = Client()
    monkeypatch.setattr(claude, "_get_client", lambda _timeout: fake_client)
    monkeypatch.setattr(claude, "distributed_semaphore", lambda *_a, **_k: nullcontext())
    monkeypatch.setattr(claude.paid_api_guard, "guard", lambda _provider: None)
    monkeypatch.setattr(claude.paid_api_guard, "trip_if_quota", lambda *_a: False)

    with pytest.raises(claude.ClaudeRequestRejectedError) as exc_info:
        claude.generate_text("正文")

    assert "paid_call_delivery_uncertain" not in str(exc_info.value)


def test_claude_anyrouter_free_quota_is_a_hard_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("POST", "https://anyrouter.top/v1/messages")
    response = httpx.Response(
        400,
        json={"error": "1m 免费额度已经全部用完，请充值 1m 额度的余额"},
        request=request,
    )

    class Client:
        def post(self, *_args: object, **_kwargs: object):
            return response

    trips: list[str] = []
    monkeypatch.setattr(claude, "_get_client", lambda _timeout: Client())
    monkeypatch.setattr(claude, "distributed_semaphore", lambda *_a, **_k: nullcontext())
    monkeypatch.setattr(claude.paid_api_guard, "guard", lambda _provider: None)
    monkeypatch.setattr(claude.paid_api_guard, "trip_if_quota", lambda *_a: False)
    monkeypatch.setattr(claude.paid_api_guard, "trip", lambda _provider, reason: trips.append(str(reason)))

    with pytest.raises(paid_api_guard.PaidApiQuotaExhausted):
        claude.generate_text("正文")

    assert len(trips) == 1
    assert "免费额度" in trips[0]
