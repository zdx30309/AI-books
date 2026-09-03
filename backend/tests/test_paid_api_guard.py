from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services import content_engine
from app.services import deepseek, gemini
from app.tasks import tasks
from app.utils import paid_api_guard


@pytest.fixture(autouse=True)
def _clear_paid_breakers(monkeypatch: pytest.MonkeyPatch):
    paid_api_guard._local_breakers.clear()  # noqa: SLF001
    monkeypatch.setattr(settings, "PAID_API_GUARD_FAIL_CLOSED", False)
    yield
    paid_api_guard._local_breakers.clear()  # noqa: SLF001


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, key: str, value: str, *, ex: int) -> None:
        self.values[key] = value

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def delete(self, key: str) -> int:
        return int(self.values.pop(key, None) is not None)


def test_quota_markers_do_not_confuse_normal_rate_limit() -> None:
    assert paid_api_guard.is_quota_exhausted("429 insufficient_quota")
    assert paid_api_guard.is_quota_exhausted("RESOURCE_EXHAUSTED: no credits remaining")
    assert paid_api_guard.is_quota_exhausted(
        "403 PERMISSION_DENIED: Lightning dunning decision is deny for project"
    )
    assert not paid_api_guard.is_quota_exhausted("429 rate limit; retry after 2 seconds")


def test_openai_endpoint_labels_share_one_breaker(monkeypatch: pytest.MonkeyPatch) -> None:
    redis_client = _FakeRedis()
    monkeypatch.setattr(paid_api_guard, "_redis_client", lambda: redis_client)

    paid_api_guard.trip("openai", "insufficient_quota", ttl_seconds=120)

    with pytest.raises(paid_api_guard.PaidApiQuotaExhausted):
        paid_api_guard.guard("openai-image")
    assert list(redis_client.values) == ["video-plan:paid-api-breaker:openai"]

    paid_api_guard.reset("openai-ocr")
    paid_api_guard.guard("openai")


def test_production_guard_fails_closed_when_redis_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_client():
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(paid_api_guard, "_redis_client", broken_client)
    monkeypatch.setattr(settings, "PAID_API_GUARD_FAIL_CLOSED", True)

    with pytest.raises(paid_api_guard.PaidApiGuardUnavailable):
        paid_api_guard.guard("openai")


def test_local_breaker_still_blocks_when_redis_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenRedis:
        def set(self, *args, **kwargs):
            raise ConnectionError("redis write unavailable")

        def get(self, *args, **kwargs):
            raise ConnectionError("redis read unavailable")

    monkeypatch.setattr(paid_api_guard, "_redis_client", lambda: BrokenRedis())
    paid_api_guard.trip("openai", "credit_balance_exhausted", ttl_seconds=120)

    with pytest.raises(paid_api_guard.PaidApiQuotaExhausted):
        paid_api_guard.guard("openai-image")


def test_quota_errors_are_not_task_retryable() -> None:
    error = RuntimeError("HTTP 429: insufficient_quota")
    assert not tasks._is_retryable_generation_error(error)  # noqa: SLF001
    assert not tasks._is_retryable_error_message(str(error))  # noqa: SLF001
    assert not content_engine._is_retryable_openai_error(error)  # noqa: SLF001


def test_background_retry_reuses_created_response_id(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, object]] = []
    retrieved: list[str] = []
    waits = iter([RuntimeError("read timeout"), None])

    class Responses:
        def create(self, **kwargs: object) -> SimpleNamespace:
            created.append(kwargs)
            return SimpleNamespace(id="resp-one", status="queued")

        def retrieve(self, response_id: str) -> SimpleNamespace:
            retrieved.append(response_id)
            return SimpleNamespace(
                id=response_id,
                status="completed",
                output_text="completed text",
                service_tier="default",
            )

    client = SimpleNamespace(responses=Responses())

    def fake_wait(*args, **kwargs):
        outcome = next(waits)
        if outcome is not None:
            raise outcome
        return SimpleNamespace(
            id="resp-one",
            status="completed",
            output_text="completed text",
            service_tier="default",
        )

    monkeypatch.setattr(content_engine, "_get_openai_writer_client", lambda: client)
    monkeypatch.setattr(content_engine, "_reset_openai_writer_client", lambda: None)
    monkeypatch.setattr(content_engine, "_wait_for_openai_background_response", fake_wait)
    monkeypatch.setattr(content_engine, "distributed_semaphore", lambda *a, **k: nullcontext())
    monkeypatch.setattr(content_engine.paid_api_guard, "guard", lambda provider: None)
    monkeypatch.setattr(content_engine.paid_api_guard, "trip_if_quota", lambda provider, value: False)
    monkeypatch.setattr(content_engine.settings, "OPENAI_WRITER_MAX_RETRIES", 2)
    monkeypatch.setattr(content_engine.settings, "OPENAI_WRITER_RETRY_BACKOFF_SECONDS", 0)

    callback_ids: list[str] = []
    text, _usage = content_engine._openai_writer_call_result(  # noqa: SLF001
        "fixed prompt",
        background=True,
        retry_on_failure=True,
        on_background_response_id=callback_ids.append,
    )

    assert text == "completed text"
    assert len(created) == 1
    assert retrieved == ["resp-one"]
    assert callback_ids == ["resp-one"]


def test_quota_writer_failure_submits_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    class Responses:
        def create(self, **kwargs: object):
            nonlocal calls
            calls += 1
            raise RuntimeError("HTTP 429 insufficient_quota")

    monkeypatch.setattr(
        content_engine,
        "_get_openai_writer_client",
        lambda: SimpleNamespace(responses=Responses()),
    )
    monkeypatch.setattr(content_engine, "_reset_openai_writer_client", lambda: None)
    monkeypatch.setattr(content_engine, "distributed_semaphore", lambda *a, **k: nullcontext())
    monkeypatch.setattr(content_engine.paid_api_guard, "guard", lambda provider: None)
    monkeypatch.setattr(content_engine.paid_api_guard, "trip_if_quota", lambda provider, value: True)
    monkeypatch.setattr(content_engine.settings, "OPENAI_WRITER_MAX_RETRIES", 3)
    monkeypatch.setattr(content_engine.settings, "OPENAI_WRITER_RETRY_BACKOFF_SECONDS", 0)

    with pytest.raises(RuntimeError, match="insufficient_quota"):
        content_engine._openai_writer_call_result(  # noqa: SLF001
            "fixed prompt",
            retry_on_failure=True,
            fallback_service_tier="auto",
        )

    assert calls == 1


def test_deepseek_stream_delivery_uncertain_submits_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    class Stream:
        def __iter__(self):
            yield SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content="已收到一部分", reasoning_content=None),
                        finish_reason=None,
                    )
                ]
            )
            raise RuntimeError("peer closed connection")

    class Completions:
        def create(self, **_kwargs):
            nonlocal calls
            calls += 1
            return Stream()

    fake_get = lambda: SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    fake_get.cache_clear = lambda: None
    monkeypatch.setattr(deepseek, "get_deepseek_client", fake_get)
    monkeypatch.setattr(deepseek, "distributed_semaphore", lambda *_a, **_k: nullcontext())
    monkeypatch.setattr(deepseek.paid_api_guard, "guard", lambda _provider: None)
    monkeypatch.setattr(deepseek.paid_api_guard, "trip_if_quota", lambda *_a: False)

    with pytest.raises(deepseek.DeepSeekDeliveryUncertainError):
        deepseek.generate_text("prompt")

    assert calls == 1


def test_deepseek_empty_result_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    class Completions:
        def create(self, **_kwargs):
            nonlocal calls
            calls += 1
            return iter(())

    fake_get = lambda: SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    fake_get.cache_clear = lambda: None
    monkeypatch.setattr(deepseek, "get_deepseek_client", fake_get)
    monkeypatch.setattr(deepseek, "distributed_semaphore", lambda *_a, **_k: nullcontext())
    monkeypatch.setattr(deepseek.paid_api_guard, "guard", lambda _provider: None)
    monkeypatch.setattr(deepseek.paid_api_guard, "trip_if_quota", lambda *_a: False)

    with pytest.raises(deepseek.DeepSeekDeliveryUncertainError):
        deepseek.generate_text("prompt")

    assert calls == 1


def test_gemini_delivery_uncertain_does_not_switch_backend_or_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class Models:
        def generate_content(self, **_kwargs):
            nonlocal calls
            calls += 1
            raise RuntimeError("read timeout from upstream")

    client = SimpleNamespace(models=Models())
    monkeypatch.setattr(gemini.settings, "GOOGLE_GENAI_USE_VERTEXAI", False)
    monkeypatch.setattr(gemini, "_resolve_google_api_key", lambda: "test-key")
    monkeypatch.setattr(gemini, "_get_api_key_client", lambda _timeout: client)
    monkeypatch.setattr(gemini, "_gemini_call_gate", lambda: nullcontext())
    monkeypatch.setattr(gemini, "_google_proxy_env", lambda: nullcontext())
    monkeypatch.setattr(gemini.paid_api_guard, "guard", lambda _provider: None)
    monkeypatch.setattr(gemini.paid_api_guard, "trip_if_quota", lambda *_a: False)

    with pytest.raises(gemini.GeminiDeliveryUncertainError):
        gemini.generate_text("prompt", retries=3)

    assert calls == 1
