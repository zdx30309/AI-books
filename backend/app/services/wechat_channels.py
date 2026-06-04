"""WeChat Channels public preview extraction."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.services.video_sources import canonical_source_url


class WeChatChannelsError(RuntimeError):
    pass


@dataclass(frozen=True)
class WeChatChannelsResult:
    text: str
    source_id: str
    author: str | None
    description: str
    raw: dict[str, Any]


def extract_wechat_channels_text(source_url: str, *, timeout: int = 30) -> WeChatChannelsResult:
    source_id = _wechat_channels_short_id(source_url)
    if not source_id:
        raise WeChatChannelsError(f"could not extract WeChat Channels short id from URL: {source_url}")

    api_url = "https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info"
    page_url = f"https://channels.weixin.qq.com/finder-preview/pages/sph?id={source_id}"
    payload = {"baseReq": {"generalToken": ""}, "shortUri": source_id}
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": page_url,
        "Origin": "https://channels.weixin.qq.com",
        "Content-Type": "application/json",
    }
    try:
        response = httpx.post(api_url, json=payload, headers=headers, timeout=timeout, follow_redirects=True)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        raise WeChatChannelsError(f"WeChat Channels preview API failed: {exc}") from exc

    if int(data.get("errCode") or 0) != 0:
        raise WeChatChannelsError(f"WeChat Channels preview API returned error: {data}")

    body = data.get("data") or {}
    feed = body.get("feedInfo") or {}
    author_info = body.get("authorInfo") or {}
    description = str(feed.get("description") or "").strip()
    author = str(author_info.get("nickname") or "").strip() or None
    if not description:
        raise WeChatChannelsError(f"WeChat Channels preview API returned no description: {data}")

    lines = [
        "微信视频号公开视频信息",
        f"来源链接：{canonical_source_url(source_url)}",
    ]
    if author:
        lines.append(f"作者：{author}")
    lines.append(f"视频描述：{description}")

    location = str((feed.get("jumpInfo") or {}).get("wording") or "").strip()
    if location:
        lines.append(f"位置：{location}")
    for label, key in (
        ("点赞", "likeCountFmt"),
        ("收藏", "favCountFmt"),
        ("转发", "forwardCountFmt"),
        ("评论", "commentCountFmt"),
    ):
        value = str(feed.get(key) or "").strip()
        if value:
            lines.append(f"{label}：{value}")
    lines.append("说明：微信视频号公开预览接口未返回字幕或可下载音轨，以上为可公开读取的视频号元数据。")

    return WeChatChannelsResult(
        text="\n".join(lines),
        source_id=source_id,
        author=author,
        description=description,
        raw=data,
    )


def _wechat_channels_short_id(source_url: str) -> str:
    url = canonical_source_url(source_url)
    match = re.search(r"/sph/([A-Za-z0-9]+)", url)
    if match:
        return match.group(1)
    match = re.search(r"[?&]id=([A-Za-z0-9]+)", url)
    if match:
        return match.group(1)
    return ""
