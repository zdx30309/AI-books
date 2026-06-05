from __future__ import annotations

import pytest

from app.services import wechat_channels
from app.services.wechat_channels import WeChatChannelsError, WeChatChannelsMedia, WeChatChannelsResult
from app.tasks import tasks as task_tasks


def test_extract_wechat_channels_text_from_media_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_resolve(provider: str, source_url: str, *, timeout: int) -> WeChatChannelsMedia:
        assert provider == "tikhub"
        assert source_url == "https://weixin.qq.com/sph/A8yYHFLFrB"
        return WeChatChannelsMedia(
            provider="tikhub",
            media_url="https://finder.video.qq.com/test.mp4?token=abc",
            decode_key="123456",
            raw={"data": {"description": "desc text #topic"}},
        )

    def fake_transcribe(media_url: str, **kwargs) -> str:
        assert media_url == "https://finder.video.qq.com/test.mp4?token=abc"
        assert kwargs["decode_key"] == "123456"
        return "first spoken line\nsecond spoken line"

    monkeypatch.setattr(wechat_channels.settings, "WECHAT_CHANNELS_PROVIDERS", "tikhub")
    monkeypatch.setattr(wechat_channels, "_resolve_media", fake_resolve)
    monkeypatch.setattr(wechat_channels, "transcribe_media_url", fake_transcribe)

    result = wechat_channels.extract_wechat_channels_text("https://weixin.qq.com/sph/A8yYHFLFrB")

    assert result.source_id == "A8yYHFLFrB"
    assert result.provider == "tikhub"
    assert result.decode_key == "123456"
    assert "desc text #topic" in result.text
    assert "first spoken line" in result.text


def test_extract_media_url_prefers_tikhub_media_shape() -> None:
    payload = {
        "code": 200,
        "data": {
            "object_desc": {
                "media": [
                    {
                        "url": "https://finder.video.qq.com/path/video.mp4",
                        "url_token": "?token=abc",
                        "decode_key": "987654",
                    }
                ]
            }
        },
    }

    media_url, decode_key = wechat_channels._extract_media_url_and_decode_key(payload)  # noqa: SLF001

    assert media_url == "https://finder.video.qq.com/path/video.mp4?token=abc"
    assert decode_key == "987654"


def test_tikhub_share_resolver_follows_export_id_to_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_get(url: str, *, params: dict[str, str], headers: dict[str, str], timeout: int) -> dict:
        calls.append((url, params))
        if url.endswith("/fetch_video_by_share_url"):
            return {
                "code": 200,
                "data": {
                    "data": {
                        "sceneInfo": {"dynamicExportId": "export/test-id"},
                        "feedInfo": {"description": "preview only"},
                    }
                },
            }
        if url.endswith("/fetch_video_detail"):
            assert params == {"exportId": "export/test-id"}
            return {
                "code": 200,
                "data": {
                    "object_desc": {
                        "media": [
                            {
                                "url": "https://finder.video.qq.com/path/video.mp4",
                                "url_token": "?token=abc",
                                "decode_key": "987654",
                            }
                        ]
                    }
                },
            }
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(wechat_channels.settings, "WECHAT_CHANNELS_TIKHUB_API_KEY", "test-key")
    monkeypatch.setattr(wechat_channels, "_http_get_json", fake_get)

    media = wechat_channels._resolve_with_tikhub("https://weixin.qq.com/sph/A8yYHFLFrB", timeout=10)  # noqa: SLF001

    assert len(calls) == 2
    assert media.media_url == "https://finder.video.qq.com/path/video.mp4?token=abc"
    assert media.decode_key == "987654"


def test_tikhub_detail_falls_back_to_wechat_preview_export_id(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, params: dict[str, str], headers: dict[str, str], timeout: int) -> dict:
        if url.endswith("/fetch_video_by_share_url"):
            return {"code": 200, "data": {"sceneInfo": {"dynamicExportId": "export/bad-id"}}}
        if url.endswith("/fetch_video_detail") and params == {"exportId": "export/bad-id"}:
            raise WeChatChannelsError("bad export id")
        if url.endswith("/fetch_video_detail") and params == {"exportId": "export/good-id"}:
            return {
                "code": 200,
                "data": {
                    "object_desc": {
                        "media": [
                            {
                                "url": "https://finder.video.qq.com/path/video.mp4",
                                "url_token": "?token=abc",
                                "decode_key": "987654",
                            }
                        ]
                    }
                },
            }
        raise AssertionError(f"unexpected call: {url} {params}")

    monkeypatch.setattr(wechat_channels.settings, "WECHAT_CHANNELS_TIKHUB_API_KEY", "test-key")
    monkeypatch.setattr(wechat_channels, "_http_get_json", fake_get)
    monkeypatch.setattr(wechat_channels, "_fetch_wechat_preview_export_id", lambda *args, **kwargs: "export/good-id")

    media = wechat_channels._resolve_with_tikhub("https://weixin.qq.com/sph/A8yYHFLFrB", timeout=10)  # noqa: SLF001

    assert media.media_url == "https://finder.video.qq.com/path/video.mp4?token=abc"
    assert media.decode_key == "987654"


def test_fetch_video_text_uses_wechat_channels_media_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_qingdou(*args, **kwargs):
        raise RuntimeError("qingdou failed")

    def ok_fallback(source_url: str, **kwargs):
        assert source_url == "https://weixin.qq.com/sph/A8yYHFLFrB"
        return WeChatChannelsResult(
            text="wechat transcript from media",
            source_id="A8yYHFLFrB",
            provider="tikhub",
            media_url="https://finder.video.qq.com/test.mp4",
            decode_key=None,
            raw={},
        )

    monkeypatch.setattr(task_tasks, "extract_video_text_with_qingdou", fail_qingdou)
    monkeypatch.setattr(task_tasks, "extract_wechat_channels_text", ok_fallback)

    text = task_tasks._fetch_video_text(  # noqa: SLF001
        "#tag https://weixin.qq.com/sph/A8yYHFLFrB",
        {"source_platform": "wechat_channels"},
    )

    assert text == "wechat transcript from media"


def test_fetch_video_text_fails_when_wechat_resolver_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_qingdou(*args, **kwargs):
        raise RuntimeError("qingdou failed")

    def fail_fallback(*args, **kwargs):
        raise WeChatChannelsError("no resolver configured")

    monkeypatch.setattr(task_tasks, "extract_video_text_with_qingdou", fail_qingdou)
    monkeypatch.setattr(task_tasks, "extract_wechat_channels_text", fail_fallback)

    with pytest.raises(RuntimeError, match="微信视频号解析失败"):
        task_tasks._fetch_video_text(  # noqa: SLF001
            "#tag https://weixin.qq.com/sph/A8yYHFLFrB",
            {"source_platform": "wechat_channels"},
        )


def test_legacy_wechat_preview_metadata_cache_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_qingdou(*args, **kwargs):
        raise RuntimeError("qingdou failed")

    def ok_fallback(*args, **kwargs):
        return WeChatChannelsResult(
            text="real wechat transcript",
            source_id="A8yYHFLFrB",
            provider="tikhub",
            media_url="https://finder.video.qq.com/test.mp4",
            decode_key=None,
            raw={},
        )

    legacy_metadata = "\n".join(
        [
            "微信视频号公开视频信息",
            "来源链接：https://weixin.qq.com/sph/A8yYHFLFrB",
            "视频描述：desc",
            "说明：微信视频号公开预览接口未返回字幕或可下载音轨，以上为可公开读取的视频号元数据。",
        ]
    )

    monkeypatch.setattr(task_tasks, "extract_video_text_with_qingdou", fail_qingdou)
    monkeypatch.setattr(task_tasks, "extract_wechat_channels_text", ok_fallback)

    text = task_tasks._fetch_video_text(  # noqa: SLF001
        "https://weixin.qq.com/sph/A8yYHFLFrB",
        {"source_platform": "wechat_channels", "raw_text": legacy_metadata},
    )

    assert text == "real wechat transcript"
