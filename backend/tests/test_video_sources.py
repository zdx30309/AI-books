from __future__ import annotations

import pytest

from app.services.video_sources import (
    detect_video_source_platform,
    is_wechat_channels_url,
    is_xiaohongshu_url,
    platform_label,
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
