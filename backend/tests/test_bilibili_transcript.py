from __future__ import annotations

import pytest

from app.services import bilibili


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


def test_extract_bilibili_text_prefers_existing_subtitle(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def get(self, url: str, **kwargs):
            calls.append(url)
            if url.endswith("/x/web-interface/view"):
                return FakeResponse({"code": 0, "data": {"cid": 123, "title": "测试标题", "duration": 60}})
            if url.endswith("/x/player/v2"):
                return FakeResponse(
                    {
                        "code": 0,
                        "data": {
                            "subtitle": {
                                "subtitles": [
                                    {"subtitle_url": "https://subtitle.example.test/sub.json"},
                                ]
                            }
                        },
                    }
                )
            if url == "https://subtitle.example.test/sub.json":
                return FakeResponse({"body": [{"content": "第一句"}, {"content": "第二句"}]})
            raise AssertionError(f"unexpected URL: {url}")

    def fail_transcribe(*args, **kwargs):
        raise AssertionError("audio fallback should not run when subtitles exist")

    monkeypatch.setattr(bilibili.httpx, "Client", FakeClient)
    monkeypatch.setattr(bilibili, "transcribe_media_url", fail_transcribe)

    result = bilibili.extract_bilibili_text(
        "https://www.bilibili.com/video/BV1xx411c7mD",
        config={"bilibili_transcript_min_chars": 2},
    )

    assert result.provider == "bilibili_subtitle"
    assert result.bvid == "BV1xx411c7mD"
    assert "视频标题：测试标题" in result.text
    assert "第一句\n第二句" in result.text
    assert not any(url.endswith("/x/player/playurl") for url in calls)


def test_extract_bilibili_text_uses_audio_when_subtitle_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    gates: list[tuple[str, int]] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def get(self, url: str, **kwargs):
            if url.endswith("/x/web-interface/view"):
                return FakeResponse({"code": 0, "data": {"cid": 123, "title": "无字幕视频", "duration": 120}})
            if url.endswith("/x/player/v2"):
                return FakeResponse({"code": 0, "data": {"subtitle": {"subtitles": []}}})
            if url.endswith("/x/player/playurl"):
                return FakeResponse(
                    {
                        "code": 0,
                        "data": {
                            "dash": {
                                "audio": [
                                    {"bandwidth": 100, "baseUrl": "https://audio.example.test/a.m4s"},
                                ]
                            }
                        },
                    }
                )
            raise AssertionError(f"unexpected URL: {url}")

    def ok_transcribe(media_url: str, **kwargs):
        assert media_url == "https://audio.example.test/a.m4s"
        assert kwargs["model"] == "test-transcribe-model"
        assert kwargs["download_proxy"] == ""
        return "音频转写第一句\n音频转写第二句"

    class FakeGate:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            return None

    def fake_gate(name: str, **kwargs):
        gates.append((name, kwargs["limit"]))
        return FakeGate()

    monkeypatch.setattr(bilibili.httpx, "Client", FakeClient)
    monkeypatch.setattr(bilibili, "transcribe_media_url", ok_transcribe)
    monkeypatch.setattr(bilibili, "distributed_semaphore", fake_gate)

    result = bilibili.extract_bilibili_text(
        "https://www.bilibili.com/video/BV1xx411c7mD",
        config={
            "bilibili_transcript_min_chars": 2,
            "bilibili_audio_transcription_model": "test-transcribe-model",
            "bilibili_audio_transcription_max_concurrent": 1,
        },
    )

    assert result.provider == "openai_audio"
    assert "视频标题：无字幕视频" in result.text
    assert "音频转写第一句" in result.text
    assert gates == [("bilibili_audio_transcription", 1)]
