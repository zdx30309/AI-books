from __future__ import annotations

import json

import pytest

from app.tasks import tasks as task_tasks
import app.services.youtube as youtube


@pytest.mark.parametrize(
    ("url", "video_id"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?si=abc", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("shared text https://youtu.be/dQw4w9WgXcQ?si=abc", "dQw4w9WgXcQ"),
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


def test_fetch_video_text_falls_back_to_qingdou_when_youtube_transcript_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fail_youtube(*args, **kwargs):
        calls.append("youtube")
        raise youtube.YouTubeTranscriptError("captions unavailable")

    def ok_qingdou(source_url: str, **kwargs):
        calls.append(source_url)
        return "轻抖兜底文案", 42, type("Result", (), {"batch_id": 1, "task_id": 2})()

    monkeypatch.setattr(task_tasks.settings, "YOUTUBE_TRANSCRIPT_ENABLED", True)
    monkeypatch.setattr(task_tasks.settings, "QINGDOU_API_KEY", "test-key")
    monkeypatch.setattr(task_tasks.settings, "QINGDOU_BASE_URL", "https://example.test/commitGetTextTask")
    monkeypatch.setattr(task_tasks, "extract_youtube_text", fail_youtube)
    monkeypatch.setattr(task_tasks, "extract_video_text_with_qingdou", ok_qingdou)

    text = task_tasks._fetch_video_text(  # noqa: SLF001
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        {"source_platform": "youtube", "youtube_transcript_min_chars": 5},
    )

    assert text == "轻抖兜底文案"
    assert calls == ["youtube", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"]


def test_fetch_video_text_sends_canonical_youtube_url_to_qingdou(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fail_youtube(*args, **kwargs):
        calls.append("youtube")
        raise youtube.YouTubeTranscriptError("bot check")

    def ok_qingdou(source_url: str, **kwargs):
        calls.append(source_url)
        return "qingdou transcript", 42, type("Result", (), {"batch_id": 1, "task_id": 2})()

    monkeypatch.setattr(task_tasks.settings, "YOUTUBE_TRANSCRIPT_ENABLED", True)
    monkeypatch.setattr(task_tasks.settings, "QINGDOU_API_KEY", "test-key")
    monkeypatch.setattr(task_tasks.settings, "QINGDOU_BASE_URL", "https://example.test/commitGetTextTask")
    monkeypatch.setattr(task_tasks, "extract_youtube_text", fail_youtube)
    monkeypatch.setattr(task_tasks, "extract_video_text_with_qingdou", ok_qingdou)

    text = task_tasks._fetch_video_text(  # noqa: SLF001
        "shared text https://youtu.be/dQw4w9WgXcQ?si=abc",
        {"source_platform": "youtube", "youtube_transcript_min_chars": 5},
    )

    assert text == "qingdou transcript"
    assert calls == ["youtube", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"]


def test_qingdou_wrapper_tries_wechat_channel_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_qingdou(source_url: str, **kwargs):
        calls.append(source_url)
        if source_url.startswith("https://weixin.qq.com/"):
            raise RuntimeError("first shape failed")
        return "wechat transcript", 60, type("Result", (), {"batch_id": 1, "task_id": 2})()

    monkeypatch.setattr(task_tasks, "_extract_video_text_with_qingdou_once", fake_qingdou)

    text, duration, _ = task_tasks.extract_video_text_with_qingdou(
        "#tag https://weixin.qq.com/sph/A8yYHFLFrB",
        timeout_seconds=1,
        poll_interval_seconds=0,
        max_video_duration=None,
    )

    assert text == "wechat transcript"
    assert duration == 60
    assert calls[:2] == [
        "https://weixin.qq.com/sph/A8yYHFLFrB",
        "https://channels.weixin.qq.com/platform/post/A8yYHFLFrB",
    ]
