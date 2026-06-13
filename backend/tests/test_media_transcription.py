from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import media_transcription
from app.services.media_transcription import MediaTranscriptionError


def test_transcribe_audio_falls_back_when_model_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    class FakeTranscriptions:
        def create(self, *, model: str, file, response_format: str) -> str:
            calls.append(model)
            if model == "gpt-4o-mini-transcribe":
                raise RuntimeError("model_not_found: you do not have access to this model")
            return "fallback transcript"

    class FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            self.audio = SimpleNamespace(transcriptions=FakeTranscriptions())

    audio_path = tmp_path / "audio.mp3"
    audio_path.write_bytes(b"fake audio")
    monkeypatch.setattr(media_transcription, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(media_transcription.settings, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(media_transcription.settings, "OPENAI_HTTPS_PROXY", "")
    monkeypatch.setattr(media_transcription.settings, "AUDIO_TRANSCRIPTION_FALLBACK_MODELS", "whisper-1")

    text = media_transcription._transcribe_audio(  # noqa: SLF001
        audio_path,
        timeout=30,
        model="gpt-4o-mini-transcribe",
    )

    assert text == "fallback transcript"
    assert calls == ["gpt-4o-mini-transcribe", "whisper-1"]


def test_transcribe_audio_does_not_fallback_for_invalid_api_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    class FakeTranscriptions:
        def create(self, *, model: str, file, response_format: str) -> str:
            calls.append(model)
            raise RuntimeError("Incorrect API key provided")

    class FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            self.audio = SimpleNamespace(transcriptions=FakeTranscriptions())

    audio_path = tmp_path / "audio.mp3"
    audio_path.write_bytes(b"fake audio")
    monkeypatch.setattr(media_transcription, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(media_transcription.settings, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(media_transcription.settings, "OPENAI_HTTPS_PROXY", "")
    monkeypatch.setattr(media_transcription.settings, "AUDIO_TRANSCRIPTION_FALLBACK_MODELS", "whisper-1")

    with pytest.raises(MediaTranscriptionError):
        media_transcription._transcribe_audio(  # noqa: SLF001
            audio_path,
            timeout=30,
            model="gpt-4o-mini-transcribe",
        )

    assert calls == ["gpt-4o-mini-transcribe"]
