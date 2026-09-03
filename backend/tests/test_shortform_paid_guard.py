from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import services


def _disable_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services.paid_api_guard, "guard", lambda _provider: None)
    monkeypatch.setattr(services.paid_api_guard, "trip_if_quota", lambda *_args: False)


def test_shortform_chat_transport_failure_is_not_replayed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    class Completions:
        def create(self, **_kwargs: object):
            nonlocal calls
            calls += 1
            raise RuntimeError("read timeout after upstream accepted request")

    _disable_guard(monkeypatch)
    monkeypatch.setattr(services, "_text_client", SimpleNamespace(chat=SimpleNamespace(completions=Completions())))

    with pytest.raises(services.OpenAIDeliveryUncertainError):
        services._chat_with_client(  # noqa: SLF001
            services._text_client,
            "gpt-test",
            "system",
            "user",
            temperature=0.2,
        )

    assert calls == 1


def test_shortform_chat_success_keeps_original_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, object]] = []

    class Completions:
        def create(self, **kwargs: object):
            captured.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="完成稿"))]
            )

    _disable_guard(monkeypatch)
    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    monkeypatch.setattr(services, "_text_client", client)

    result = services._chat_with_client(  # noqa: SLF001
        client,
        "gpt-test",
        "system",
        "user",
        temperature=0.75,
        json_mode=True,
        max_tokens=4096,
    )

    assert result == "完成稿"
    assert captured == [
        {
            "model": "gpt-test",
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "user"},
            ],
            "temperature": 0.75,
            "response_format": {"type": "json_object"},
            "max_tokens": 4096,
        }
    ]


def test_shortform_image_transport_failure_is_not_replayed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    class Images:
        def generate(self, **_kwargs: object):
            nonlocal calls
            calls += 1
            raise RuntimeError("connection closed after image request")

    _disable_guard(monkeypatch)
    monkeypatch.setattr(services, "_image_client", SimpleNamespace(images=Images()))

    with pytest.raises(services.OpenAIDeliveryUncertainError):
        services.generate_cover_image("a book cover")

    assert calls == 1
