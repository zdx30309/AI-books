from __future__ import annotations

import json

import pytest

import app.services.youtube as youtube


@pytest.mark.parametrize(
    ("url", "video_id"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?si=abc", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ],
)
def test_extract_youtube_video_id(url: str, video_id: str) -> None:
    assert youtube.extract_youtube_video_id(url) == video_id


def test_parse_json3_subtitles() -> None:
    payload = {
        "events": [
            {"segs": [{"utf8": "hello"}, {"utf8": " world"}]},
            {"segs": [{"utf8": "\n"}]},
            {"segs": [{"utf8": "next line"}]},
        ]
    }

    text = youtube._parse_json3(json.dumps(payload))

    assert text == "hello world\nnext line"


def test_extract_youtube_text_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fail_provider(*args, **kwargs):
        calls.append("first")
        raise youtube.YouTubeTranscriptError("no transcript")

    def ok_provider(*args, **kwargs):
        calls.append("second")
        return youtube.YouTubeTranscriptResult(
            text="line one\nline two\nline three",
            video_id="dQw4w9WgXcQ",
            provider="yt_dlp",
            language="en",
            is_generated=True,
        )

    monkeypatch.setattr(youtube, "_provider_preferences", lambda config: ["youtube_transcript_api", "yt_dlp"])
    monkeypatch.setattr(youtube, "_from_youtube_transcript_api", fail_provider)
    monkeypatch.setattr(youtube, "_from_yt_dlp", ok_provider)

    result = youtube.extract_youtube_text(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        config={"youtube_transcript_min_chars": 5},
    )

    assert calls == ["first", "second"]
    assert result.provider == "yt_dlp"
    assert result.text == "line one\nline two\nline three"


def test_extract_youtube_text_splits_total_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_timeouts: list[int] = []

    def fail_provider(*args, **kwargs):
        seen_timeouts.append(kwargs["timeout"])
        raise youtube.YouTubeTranscriptError("no transcript")

    def ok_provider(*args, **kwargs):
        seen_timeouts.append(kwargs["timeout"])
        return youtube.YouTubeTranscriptResult(
            text="line one\nline two\nline three",
            video_id="dQw4w9WgXcQ",
            provider="yt_dlp",
            language="en",
            is_generated=True,
        )

    monkeypatch.setattr(youtube, "_provider_preferences", lambda config: ["youtube_transcript_api", "yt_dlp"])
    monkeypatch.setattr(youtube, "_from_youtube_transcript_api", fail_provider)
    monkeypatch.setattr(youtube, "_from_yt_dlp", ok_provider)

    result = youtube.extract_youtube_text(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        config={
            "youtube_transcript_min_chars": 5,
            "youtube_transcript_timeout_seconds": 20,
            "youtube_transcript_total_timeout_seconds": 10,
        },
    )

    assert result.provider == "yt_dlp"
    assert seen_timeouts[0] <= 5
    assert seen_timeouts[1] <= 10
