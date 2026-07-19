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


def test_parse_transcript_ai_markdown() -> None:
    payload = """# Transcript: sample
Source video: https://www.youtube.com/watch?v=dQw4w9WgXcQ

## Transcript
[0:02] first line

[0:04] second line
"""

    assert youtube._parse_transcript_ai_markdown(payload) == "first line\nsecond line"


def test_default_provider_preferences_include_managed_transcript_api() -> None:
    providers = youtube._provider_preferences({})

    assert providers[:2] == ["youtube_transcript_api", "youtube_transcript_ai"]


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


def test_extract_youtube_text_can_use_openai_audio_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fail_provider(*args, **kwargs):
        calls.append("captions")
        raise youtube.YouTubeTranscriptError("no captions")

    def ok_audio(*args, **kwargs):
        calls.append("audio")
        return youtube.YouTubeTranscriptResult(
            text="audio transcript line one\nline two",
            video_id="dQw4w9WgXcQ",
            provider="openai_audio",
            language=None,
            is_generated=True,
        )

    monkeypatch.setattr(youtube, "_provider_preferences", lambda config: ["youtube_transcript_api", "openai_audio"])
    monkeypatch.setattr(youtube, "_from_youtube_transcript_api", fail_provider)
    monkeypatch.setattr(youtube, "_from_openai_audio", ok_audio)

    result = youtube.extract_youtube_text(
        "https://youtu.be/dQw4w9WgXcQ?si=abc",
        config={"youtube_transcript_min_chars": 5, "youtube_transcript_total_timeout_seconds": 60},
    )

    assert calls == ["captions", "audio"]
    assert result.provider == "openai_audio"
    assert result.text == "audio transcript line one\nline two"


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


def test_fetch_video_text_with_duration_preserves_qingdou_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def ok_qingdou(source_url: str, **kwargs):
        return "轻抖文案", 1800, type("Result", (), {"batch_id": 1, "task_id": 2})()

    monkeypatch.setattr(task_tasks, "extract_video_text_with_qingdou", ok_qingdou)

    text, duration = task_tasks._fetch_video_text_with_duration(  # noqa: SLF001
        "https://www.douyin.com/video/123",
        {"source_platform": "douyin"},
    )

    assert text == "轻抖文案"
    assert duration == 1800
    assert task_tasks._fetch_video_text(  # noqa: SLF001
        "https://www.douyin.com/video/123",
        {"source_platform": "douyin"},
    ) == "轻抖文案"


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


def test_fetch_video_text_uses_bilibili_fallback_when_qingdou_has_no_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fail_qingdou(*args, **kwargs):
        raise RuntimeError(
            "B站 video parse failed after 3 candidate URL(s): "
            "QingdouError: 轻抖任务失败 batchId=1, batchStatus=-2, "
            "raw={'result': {'list': [{'status': 1113, 'videoContent': None}]}}"
        )

    def ok_bilibili(source_url: str, **kwargs):
        calls.append(source_url)
        return type(
            "Result",
            (),
            {
                "text": "bilibili fallback transcript",
                "provider": "openai_audio",
                "bvid": "BV1xx411c7mD",
            },
        )()

    monkeypatch.setattr(task_tasks, "extract_video_text_with_qingdou", fail_qingdou)
    monkeypatch.setattr(task_tasks, "extract_bilibili_text", ok_bilibili)

    text = task_tasks._fetch_video_text(  # noqa: SLF001
        "https://www.bilibili.com/video/BV1xx411c7mD/?share_source=copy_web",
        {"source_platform": "bilibili"},
    )

    assert text == "bilibili fallback transcript"
    assert calls == ["https://www.bilibili.com/video/BV1xx411c7mD/?share_source=copy_web"]


def test_bilibili_fallback_is_not_used_for_pure_qingdou_busy_error() -> None:
    assert task_tasks._should_try_bilibili_fallback(RuntimeError("QingdouBusyError: 返回繁忙")) is False  # noqa: SLF001
    assert task_tasks._should_try_bilibili_fallback(RuntimeError("batchStatus=2 status=1001 videoContent: None")) is False  # noqa: SLF001
    assert task_tasks._should_try_bilibili_fallback(  # noqa: SLF001
        RuntimeError("batchStatus=2 status=1100 videoContent: None")
    ) is True
    assert task_tasks._should_try_bilibili_fallback(  # noqa: SLF001
        RuntimeError("batchStatus=-2 status=1113 videoContent: None")
    )
    assert task_tasks._should_try_bilibili_fallback(  # noqa: SLF001
        RuntimeError("https://www.bilibili.com/video/BV1abc123456 batchStatus=-2 status=1113 videoContent: None")
    )
    assert task_tasks._should_try_bilibili_fallback(  # noqa: SLF001
        RuntimeError('{"batchStatus": -2, "status": 1113, "videoContent": None}')
    )
    assert task_tasks._should_try_bilibili_fallback(  # noqa: SLF001
        RuntimeError("batchStatus=-2 status=1113 videoContent: None; QingdouBusyError: 返回繁忙")
    ) is False
