"""Helpers for recognizing video source platforms from shared URLs."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import httpx

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
_URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+", re.IGNORECASE)
_YOUTUBE_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_BILIBILI_VIDEO_ID_RE = re.compile(r"^(?:BV[A-Za-z0-9]+|av\d+)$", re.IGNORECASE)
_URL_TRAILING_CHARS = " \t\r\n\"'.,;:!?)]}>，。；：！？、）】》"

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


def extract_source_url(source_url: str) -> str:
    """Extract the first real URL from pasted share text."""
    raw = (source_url or "").strip()
    if not raw or raw.startswith("manual://"):
        return raw
    match = _URL_RE.search(raw)
    candidate = match.group(0) if match else raw
    return candidate.strip().rstrip(_URL_TRAILING_CHARS)


def _hostname(source_url: str) -> str:
    try:
        parsed = urlparse(extract_source_url(source_url))
    except Exception:
        return ""
    return (parsed.netloc or "").lower().split("@")[-1].split(":")[0]


def _is_host(host: str, hosts: set[str], suffix: str | None = None) -> bool:
    return host in hosts or bool(suffix and host.endswith(suffix))


def _youtube_video_id(source_url: str) -> str:
    raw = extract_source_url(source_url)
    if _YOUTUBE_VIDEO_ID_RE.match(raw):
        return raw
    try:
        parsed = urlparse(raw)
    except Exception:
        return ""
    host = (parsed.netloc or "").lower().split("@")[-1].split(":")[0]
    parts = [part for part in (parsed.path or "").split("/") if part]
    if host == "youtu.be" and parts and _YOUTUBE_VIDEO_ID_RE.match(parts[0]):
        return parts[0]
    if host in _YOUTUBE_HOSTS or host.endswith(".youtube.com"):
        query_id = parse_qs(parsed.query).get("v", [""])[0]
        if _YOUTUBE_VIDEO_ID_RE.match(query_id):
            return query_id
        if parts:
            candidate = parts[1] if parts[0] in {"shorts", "embed", "live", "v"} and len(parts) > 1 else parts[-1]
            if _YOUTUBE_VIDEO_ID_RE.match(candidate):
                return candidate
    match = re.search(r"(?:v=|/shorts/|/embed/|youtu\.be/)([A-Za-z0-9_-]{11})", raw)
    return match.group(1) if match else ""


def _wechat_channels_id(source_url: str) -> str:
    try:
        parsed = urlparse(extract_source_url(source_url))
    except Exception:
        return ""
    host = (parsed.netloc or "").lower().split("@")[-1].split(":")[0]
    parts = [part for part in (parsed.path or "").split("/") if part]
    if host in {"weixin.qq.com", "www.weixin.qq.com"} and len(parts) >= 2 and parts[0] == "sph":
        return parts[1]
    if host == "channels.weixin.qq.com" and len(parts) >= 3 and parts[-2] == "post":
        return parts[-1]
    return ""


def _bilibili_video_url(source_url: str) -> str:
    try:
        parsed = urlparse(extract_source_url(source_url))
    except Exception:
        return ""
    host = (parsed.netloc or "").lower().split("@")[-1].split(":")[0]
    if not _is_host(host, _BILIBILI_HOSTS, ".bilibili.com"):
        return ""
    parts = [part for part in (parsed.path or "").split("/") if part]
    for index, part in enumerate(parts):
        if part == "video" and index + 1 < len(parts):
            video_id = parts[index + 1].strip()
            if _BILIBILI_VIDEO_ID_RE.match(video_id):
                return f"https://www.bilibili.com/video/{video_id}"
    for part in parts:
        if _BILIBILI_VIDEO_ID_RE.match(part):
            return f"https://www.bilibili.com/video/{part}"
    return ""


def resolve_bilibili_source_url(source_url: str, *, timeout_seconds: float = 5.0) -> str:
    """Expand b23.tv short links before handing the URL to Qingdou."""
    url = extract_source_url(source_url)
    if not url or url.startswith("manual://"):
        return url
    direct = _bilibili_video_url(url)
    if direct:
        return direct
    if _hostname(url) != "b23.tv":
        return url

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        )
    }
    timeout = httpx.Timeout(timeout_seconds, connect=timeout_seconds)
    try:
        with httpx.Client(timeout=timeout, headers=headers) as client:
            for method in ("HEAD", "GET"):
                for follow_redirects in (False, True):
                    try:
                        response = client.request(method, url, follow_redirects=follow_redirects)
                    except Exception:
                        continue
                    for candidate in (response.headers.get("location") or "", str(response.url), response.text[:2000]):
                        expanded = _bilibili_video_url(candidate)
                        if expanded:
                            return expanded
    except Exception:
        pass
    return url


def canonical_source_url(source_url: str) -> str:
    """Return a parser-friendly URL while preserving non-video/manual inputs."""
    url = extract_source_url(source_url)
    if not url or url.startswith("manual://"):
        return url
    host = _hostname(url)
    if _is_host(host, _YOUTUBE_HOSTS, ".youtube.com"):
        video_id = _youtube_video_id(url)
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
    if _is_host(host, _WECHAT_CHANNELS_HOSTS, ".channels.weixin.qq.com"):
        sph_id = _wechat_channels_id(url)
        if sph_id:
            return f"https://weixin.qq.com/sph/{sph_id}"
    if _is_host(host, _BILIBILI_HOSTS, ".bilibili.com"):
        video_url = _bilibili_video_url(url)
        if video_url:
            return video_url
    return url


def source_url_candidates(source_url: str, platform: str | None = None) -> list[str]:
    """Build parser candidates for vendors that accept different share URL shapes."""
    original = (source_url or "").strip()
    canonical = canonical_source_url(original)
    detected = platform or detect_video_source_platform(canonical or original)
    candidates: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        url = (value or "").strip()
        if not url or url in seen:
            return
        seen.add(url)
        candidates.append(url)

    add(canonical)
    if detected == "youtube":
        video_id = _youtube_video_id(canonical or original)
        if video_id:
            add(f"https://www.youtube.com/watch?v={video_id}")
            add(f"https://youtu.be/{video_id}")
    elif detected == "wechat_channels":
        sph_id = _wechat_channels_id(canonical or original)
        if sph_id:
            add(f"https://weixin.qq.com/sph/{sph_id}")
            add(f"https://channels.weixin.qq.com/platform/post/{sph_id}")
    elif detected == "bilibili":
        video_url = _bilibili_video_url(canonical or original)
        if video_url:
            add(video_url)

    extracted = extract_source_url(original)
    add(extracted)
    if original and original != extracted and "http" in original.lower():
        add(original)
    return candidates


def detect_video_source_platform(source_url: str) -> str:
    """Return a stable platform id for a video/article share URL."""
    raw = extract_source_url(source_url)
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
