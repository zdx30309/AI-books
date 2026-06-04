from __future__ import annotations

import pytest

from app.services.video_sources import (
    canonical_source_url,
    detect_video_source_platform,
    extract_source_url,
    is_wechat_channels_url,
    is_xiaohongshu_url,
    platform_label,
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
