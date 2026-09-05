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


def test_transcribe_audio_chunks_and_reuses_working_fallback_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []

    class FakeTranscriptions:
        def create(self, *, model: str, file, response_format: str) -> str:
            filename = Path(file.name).name
            calls.append((model, filename))
            if model == "gpt-4o-mini-transcribe":
                raise RuntimeError("model_not_found: you do not have access to this model")
            return f"text from {filename}"

    class FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            self.audio = SimpleNamespace(transcriptions=FakeTranscriptions())

    first = tmp_path / "segment_001.mp3"
    second = tmp_path / "segment_002.mp3"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    audio_path = tmp_path / "audio.mp3"
    audio_path.write_bytes(b"source")

    monkeypatch.setattr(media_transcription, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(media_transcription.settings, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(media_transcription.settings, "OPENAI_HTTPS_PROXY", "")
    monkeypatch.setattr(media_transcription.settings, "AUDIO_TRANSCRIPTION_FALLBACK_MODELS", "whisper-1")
    monkeypatch.setattr(
        media_transcription,
        "_prepare_audio_segments",
        lambda audio_path, *, timeout: ([first, second], None),
    )

    text = media_transcription._transcribe_audio(  # noqa: SLF001
        audio_path,
        timeout=30,
        model="gpt-4o-mini-transcribe",
    )

    assert text == "text from segment_001.mp3\ntext from segment_002.mp3"
    assert calls == [
        ("gpt-4o-mini-transcribe", "segment_001.mp3"),
        ("whisper-1", "segment_001.mp3"),
        ("whisper-1", "segment_002.mp3"),
    ]


def test_transcribe_media_url_can_force_direct_media_download(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    seen: dict[str, object] = {}

    def fake_download(url, output_path, *, request_headers, download_proxy, timeout, max_bytes):
        seen["download_proxy"] = download_proxy
        output_path.write_bytes(b"media")

    monkeypatch.setattr(media_transcription.settings, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(media_transcription, "_download_media", fake_download)
    monkeypatch.setattr(media_transcription, "_extract_audio", lambda *args, **kwargs: None)
    monkeypatch.setattr(media_transcription, "_transcribe_audio", lambda *args, **kwargs: "text")

    result = media_transcription.transcribe_media_url(
        "https://cdn.example.test/video.m4s",
        download_proxy="",
    )

    assert result == "text"
    assert seen["download_proxy"] == ""
