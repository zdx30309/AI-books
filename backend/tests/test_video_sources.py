from __future__ import annotations

import pytest

from app.services import video_sources
from app.services.video_sources import (
    canonical_source_url,
    detect_video_source_platform,
    extract_source_url,
    is_wechat_channels_url,
    is_xiaohongshu_url,
    platform_label,
    resolve_bilibili_source_url,
    source_url_candidates,
)


@pytest.mark.parametrize(
    ("url", "platform", "label"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube", "YouTube"),
        ("https://youtu.be/dQw4w9WgXcQ", "youtube", "YouTube"),
        ("https://www.xiaohongshu.com/explore/abc", "xiaohongshu", "小红书"),
        ("https://xhslink.com/a/abc", "xiaohongshu", "小红书"),
        ("https://channels.weixin.qq.com/platform/post/abc", "wechat_channels", "微信视频号"),
        ("https://v.douyin.com/abc", "douyin", "抖音"),
        ("https://b23.tv/9Fo6EGl", "bilibili", "B站"),
        ("https://www.bilibili.com/video/BV1xx411c7mD/?spm_id_from=333", "bilibili", "B站"),
    ],
)
def test_detect_video_source_platform(url: str, platform: str, label: str) -> None:
    assert detect_video_source_platform(url) == platform
    assert platform_label(platform) == label


def test_platform_predicates() -> None:
    assert is_xiaohongshu_url("https://xhslink.com/a/abc")
    assert is_wechat_channels_url("https://channels.weixin.qq.com/platform/post/abc")
    assert not is_wechat_channels_url("https://mp.weixin.qq.com/s/abc")


def test_extracts_platform_from_share_text() -> None:
    share = "#鲍鹏山#文化#思想】https://weixin.qq.com/sph/A8yYHFLFrB"

    assert extract_source_url(share) == "https://weixin.qq.com/sph/A8yYHFLFrB"
    assert detect_video_source_platform(share) == "wechat_channels"
    assert canonical_source_url(share) == "https://weixin.qq.com/sph/A8yYHFLFrB"


def test_youtube_short_url_is_canonicalized() -> None:
    url = "https://youtu.be/dO43YU9XVqo?si=olMFy9YabWXncKVC"

    assert detect_video_source_platform(url) == "youtube"
    assert canonical_source_url(url) == "https://www.youtube.com/watch?v=dO43YU9XVqo"


def test_wechat_channels_candidates_include_sph_and_platform_post() -> None:
    candidates = source_url_candidates("#tag https://weixin.qq.com/sph/A8yYHFLFrB", "wechat_channels")

    assert candidates[:2] == [
        "https://weixin.qq.com/sph/A8yYHFLFrB",
        "https://channels.weixin.qq.com/platform/post/A8yYHFLFrB",
    ]


def test_bilibili_video_url_is_canonicalized() -> None:
    url = "https://www.bilibili.com/video/BV1xx411c7mD/?spm_id_from=333&vd_source=abc"

    assert canonical_source_url(url) == "https://www.bilibili.com/video/BV1xx411c7mD"
    assert source_url_candidates(url, "bilibili") == [
        "https://www.bilibili.com/video/BV1xx411c7mD",
        "https://m.bilibili.com/video/BV1xx411c7mD",
        "https://www.bilibili.com/video/BV1xx411c7mD/?spm_id_from=333&vd_source=abc",
    ]


def test_b23_short_url_resolves_to_bilibili_video(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        url = "https://www.bilibili.com/video/BV1yy411c7mE/?share_source=copy_web"
        headers: dict[str, str] = {}
        text = ""

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def request(self, method: str, url: str, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(video_sources.httpx, "Client", FakeClient)

    assert resolve_bilibili_source_url("https://b23.tv/9Fo6EGl") == "https://www.bilibili.com/video/BV1yy411c7mE"


def test_b23_resolution_failure_keeps_original(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def request(self, method: str, url: str, **kwargs):
            raise RuntimeError("network timeout")

    monkeypatch.setattr(video_sources.httpx, "Client", FakeClient)

    assert resolve_bilibili_source_url("https://b23.tv/slow") == "https://b23.tv/slow"
