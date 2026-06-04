"""Helpers for recognizing video source platforms from shared URLs."""
from __future__ import annotations

from urllib.parse import urlparse

_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
    "youtu.be",
}
_XIAOHONGSHU_HOSTS = {
    "xiaohongshu.com",
    "www.xiaohongshu.com",
    "m.xiaohongshu.com",
    "xhslink.com",
    "www.xhslink.com",
    "xhs.cn",
    "www.xhs.cn",
}
_WECHAT_CHANNELS_HOSTS = {
    "channels.weixin.qq.com",
    "weixin.qq.com",
    "www.weixin.qq.com",
}
_DOUYIN_HOSTS = {
    "douyin.com",
    "www.douyin.com",
    "v.douyin.com",
    "iesdouyin.com",
    "www.iesdouyin.com",
}
_KUAISHOU_HOSTS = {
    "kuaishou.com",
    "www.kuaishou.com",
    "v.kuaishou.com",
    "gifshow.com",
    "www.gifshow.com",
}
_BILIBILI_HOSTS = {
    "bilibili.com",
    "www.bilibili.com",
    "m.bilibili.com",
    "b23.tv",
}

_PLATFORM_LABELS = {
    "youtube": "YouTube",
    "xiaohongshu": "小红书",
    "wechat_channels": "微信视频号",
    "wechat_article": "微信公众号文章",
    "douyin": "抖音",
    "kuaishou": "快手",
    "bilibili": "B站",
    "manual": "手动输入",
    "unknown": "未知平台",
}


def _hostname(source_url: str) -> str:
    try:
        parsed = urlparse((source_url or "").strip())
    except Exception:
        return ""
    return (parsed.netloc or "").lower().split("@")[-1].split(":")[0]


def _is_host(host: str, hosts: set[str], suffix: str | None = None) -> bool:
    return host in hosts or bool(suffix and host.endswith(suffix))


def detect_video_source_platform(source_url: str) -> str:
    """Return a stable platform id for a video/article share URL."""
    raw = (source_url or "").strip()
    if raw.startswith("manual://"):
        return "manual"

    host = _hostname(raw)
    if not host:
        return "unknown"
    if _is_host(host, _YOUTUBE_HOSTS, ".youtube.com"):
        return "youtube"
    if _is_host(host, _XIAOHONGSHU_HOSTS, ".xiaohongshu.com"):
        return "xiaohongshu"
    if host == "mp.weixin.qq.com":
        return "wechat_article"
    if _is_host(host, _WECHAT_CHANNELS_HOSTS, ".channels.weixin.qq.com"):
        return "wechat_channels"
    if _is_host(host, _DOUYIN_HOSTS, ".douyin.com"):
        return "douyin"
    if _is_host(host, _KUAISHOU_HOSTS, ".kuaishou.com"):
        return "kuaishou"
    if _is_host(host, _BILIBILI_HOSTS, ".bilibili.com"):
        return "bilibili"
    return "unknown"


def platform_label(platform: str) -> str:
    return _PLATFORM_LABELS.get(platform, _PLATFORM_LABELS["unknown"])


def is_xiaohongshu_url(source_url: str) -> bool:
    return detect_video_source_platform(source_url) == "xiaohongshu"


def is_wechat_channels_url(source_url: str) -> bool:
    return detect_video_source_platform(source_url) == "wechat_channels"
