"""WeChat Channels media resolution and transcription."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx

from app.core.config import settings
from app.services.media_transcription import transcribe_media_url
from app.services.video_sources import canonical_source_url

logger = logging.getLogger(__name__)


class WeChatChannelsError(RuntimeError):
    pass


@dataclass(frozen=True)
class WeChatChannelsMedia:
    provider: str
    media_url: str
    decode_key: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class WeChatChannelsResult:
    text: str
    source_id: str
    provider: str
    media_url: str
    decode_key: str | None
    raw: dict[str, Any]


def extract_wechat_channels_text(source_url: str, *, timeout: int = 30) -> WeChatChannelsResult:
    """Resolve a WeChat Channels share URL through configured providers and transcribe its media."""
    source_id = _wechat_channels_short_id(source_url)
    if not source_id:
        raise WeChatChannelsError(f"could not extract WeChat Channels short id from URL: {source_url}")

    providers = _provider_preferences()
    if not providers:
        raise WeChatChannelsError(
            "no WeChat Channels resolver provider configured; set WECHAT_CHANNELS_PROVIDERS and provider API key"
        )

    errors: list[str] = []
    for provider in providers:
        try:
            media = _resolve_media(provider, source_url, timeout=timeout)
            text = transcribe_media_url(
                media.media_url,
                decode_key=media.decode_key,
                request_headers=_media_request_headers(provider),
                download_timeout=int(settings.WECHAT_CHANNELS_MEDIA_DOWNLOAD_TIMEOUT_SECONDS or timeout),
                transcribe_timeout=int(settings.WECHAT_CHANNELS_TRANSCRIPTION_TIMEOUT_SECONDS or 300),
                max_media_bytes=int(settings.WECHAT_CHANNELS_MEDIA_MAX_BYTES or 200_000_000),
                max_audio_bytes=int(settings.WECHAT_CHANNELS_AUDIO_MAX_BYTES or settings.YOUTUBE_AUDIO_MAX_BYTES),
                model=settings.WECHAT_CHANNELS_AUDIO_TRANSCRIPTION_MODEL,
            )
            text = _clean_transcript_text(text)
            min_chars = int(settings.WECHAT_CHANNELS_TRANSCRIPT_MIN_CHARS or 20)
            if len(text) < min_chars:
                raise WeChatChannelsError(f"{provider} transcription too short ({len(text)} chars < {min_chars})")
            logger.info(
                "WeChat Channels resolved provider=%s source_id=%s chars=%d decode_key=%s",
                media.provider,
                source_id,
                len(text),
                bool(media.decode_key),
            )
            return WeChatChannelsResult(
                text=_merge_description_with_transcript(media.raw, text),
                source_id=source_id,
                provider=media.provider,
                media_url=media.media_url,
                decode_key=media.decode_key,
                raw=media.raw,
            )
        except Exception as exc:
            logger.warning("WeChat Channels provider failed provider=%s source_id=%s error=%s", provider, source_id, exc)
            errors.append(f"{provider}: {type(exc).__name__}: {exc}")
    raise WeChatChannelsError("all WeChat Channels providers failed; " + " | ".join(errors[-5:]))


def _provider_preferences() -> list[str]:
    raw = (settings.WECHAT_CHANNELS_PROVIDERS or "").strip()
    providers = [item.strip().lower() for item in raw.split(",") if item.strip()]
    valid = {"tikhub", "wechatvideodownloader", "generic"}
    return [provider for provider in providers if provider in valid]


def _resolve_media(provider: str, source_url: str, *, timeout: int) -> WeChatChannelsMedia:
    if provider == "tikhub":
        return _resolve_with_tikhub(source_url, timeout=timeout)
    if provider == "wechatvideodownloader":
        return _resolve_with_wechat_video_downloader(source_url, timeout=timeout)
    if provider == "generic":
        return _resolve_with_generic(source_url, timeout=timeout)
    raise WeChatChannelsError(f"unknown WeChat Channels provider: {provider}")


def _resolve_with_tikhub(source_url: str, *, timeout: int) -> WeChatChannelsMedia:
    api_key = (settings.WECHAT_CHANNELS_TIKHUB_API_KEY or "").strip()
    if not api_key:
        raise WeChatChannelsError("WECHAT_CHANNELS_TIKHUB_API_KEY is not configured")
    base_url = (settings.WECHAT_CHANNELS_TIKHUB_BASE_URL or "https://api.tikhub.io").strip().rstrip("/")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "Mozilla/5.0",
    }
    endpoint = urljoin(base_url + "/", "api/v1/wechat_channels/fetch_video_by_share_url")
    data = _http_get_json(endpoint, params={"share_url": canonical_source_url(source_url)}, headers=headers, timeout=timeout)
    _assert_resolver_success(data, provider="tikhub")
    media_url, decode_key = _extract_media_url_and_decode_key(data)
    if not media_url:
        export_id = _extract_tikhub_export_id(data)
        if export_id:
            detail_endpoint = urljoin(base_url + "/", "api/v1/wechat_channels/fetch_video_detail")
            data = _http_get_json(
                detail_endpoint,
                params={"exportId": export_id},
                headers=headers,
                timeout=timeout,
            )
            _assert_resolver_success(data, provider="tikhub")
            media_url, decode_key = _extract_media_url_and_decode_key(data)
    if not media_url:
        raise WeChatChannelsError(f"TikHub returned no media URL: {_summarize_payload(data)}")
    return WeChatChannelsMedia(provider="tikhub", media_url=media_url, decode_key=decode_key, raw=data)


def _resolve_with_wechat_video_downloader(source_url: str, *, timeout: int) -> WeChatChannelsMedia:
    api_key = (settings.WECHAT_CHANNELS_DOWNLOADER_API_KEY or "").strip()
    if not api_key:
        raise WeChatChannelsError("WECHAT_CHANNELS_DOWNLOADER_API_KEY is not configured")
    endpoint = (settings.WECHAT_CHANNELS_DOWNLOADER_BASE_URL or "https://api.wechatvideodownloader.com/sph/").strip()
    headers = {
        "Authorization": api_key,
        "User-Agent": "Mozilla/5.0",
    }
    data = _http_get_json(endpoint, params={"url": canonical_source_url(source_url)}, headers=headers, timeout=timeout)
    _assert_resolver_success(data, provider="wechatvideodownloader")
    media_url, decode_key = _extract_media_url_and_decode_key(data)
    if not media_url:
        raise WeChatChannelsError(f"WeChat video downloader returned no media URL: {_summarize_payload(data)}")
    return WeChatChannelsMedia(
        provider="wechatvideodownloader",
        media_url=media_url,
        decode_key=decode_key,
        raw=data,
    )


def _resolve_with_generic(source_url: str, *, timeout: int) -> WeChatChannelsMedia:
    endpoint = (settings.WECHAT_CHANNELS_GENERIC_RESOLVER_URL or "").strip()
    if not endpoint:
        raise WeChatChannelsError("WECHAT_CHANNELS_GENERIC_RESOLVER_URL is not configured")
    method = (settings.WECHAT_CHANNELS_GENERIC_RESOLVER_METHOD or "GET").strip().upper()
    headers = {"User-Agent": "Mozilla/5.0"}
    auth_header = (settings.WECHAT_CHANNELS_GENERIC_RESOLVER_AUTH_HEADER or "Authorization").strip()
    api_key = (settings.WECHAT_CHANNELS_GENERIC_RESOLVER_API_KEY or "").strip()
    if api_key and auth_header:
        headers[auth_header] = api_key
    params = {"url": canonical_source_url(source_url)}
    if method == "POST":
        data = _http_post_json(endpoint, json_payload=params, headers=headers, timeout=timeout)
    else:
        data = _http_get_json(endpoint, params=params, headers=headers, timeout=timeout)
    _assert_resolver_success(data, provider="generic")
    media_url, decode_key = _extract_media_url_and_decode_key(data)
    if not media_url:
        raise WeChatChannelsError(f"generic resolver returned no media URL: {_summarize_payload(data)}")
    return WeChatChannelsMedia(provider="generic", media_url=media_url, decode_key=decode_key, raw=data)


def _http_get_json(url: str, *, params: dict[str, str], headers: dict[str, str], timeout: int) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=max(5, timeout), follow_redirects=True, headers=headers) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        raise WeChatChannelsError(f"resolver GET failed url={url}: {exc}") from exc


def _http_post_json(url: str, *, json_payload: dict[str, str], headers: dict[str, str], timeout: int) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=max(5, timeout), follow_redirects=True, headers=headers) as client:
            response = client.post(url, json=json_payload)
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        raise WeChatChannelsError(f"resolver POST failed url={url}: {exc}") from exc


def _assert_resolver_success(data: dict[str, Any], *, provider: str) -> None:
    code = data.get("code", data.get("status_code", data.get("status")))
    if isinstance(code, dict):
        code = code.get("code")
    if code in (None, 0, 200, "0", "200", "success", "ok"):
        return
    message = data.get("message") or data.get("msg") or data.get("error") or data.get("message_zh")
    raise WeChatChannelsError(f"{provider} resolver returned code={code}: {message or _summarize_payload(data)}")


def _extract_media_url_and_decode_key(data: Any) -> tuple[str, str | None]:
    for node in _walk_dicts(data):
        url = _first_string(
            node,
            "fullVideoUrl",
            "full_video_url",
            "video_url",
            "videoUrl",
            "download_url",
            "downloadUrl",
            "play_url",
            "playUrl",
            "media_url",
            "mediaUrl",
            "url",
        )
        if not url or not _looks_like_media_url(url):
            continue
        token = _first_string(node, "url_token", "urlToken", "token")
        decode_key = _first_string(node, "decode_key", "decodeKey", "decode_key_v2")
        return url + (token or ""), decode_key
    return "", None


def _extract_tikhub_export_id(data: Any) -> str:
    for node in _walk_dicts(data):
        export_id = _first_string(node, "dynamicExportId", "dynamic_export_id", "exportId", "export_id")
        if export_id:
            return export_id
    return ""


def _walk_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            found.append(item)
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return found


def _first_string(node: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = node.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _looks_like_media_url(url: str) -> bool:
    lower = (url or "").lower()
    if not lower.startswith(("http://", "https://")):
        return False
    if "weixin.qq.com/sph/" in lower or "channels.weixin.qq.com/" in lower:
        return False
    media_hints = (
        ".mp4",
        ".m4a",
        ".mp3",
        ".m3u8",
        "finder.video.qq.com",
        "wxapp.tc.qq.com",
        "video.qq.com",
        "vod",
    )
    return any(hint in lower for hint in media_hints)


def _media_request_headers(provider: str) -> dict[str, str]:
    if provider == "tikhub":
        return {"Referer": "https://channels.weixin.qq.com/"}
    return {}


def _merge_description_with_transcript(raw: dict[str, Any], transcript: str) -> str:
    description = _extract_description(raw)
    parts: list[str] = []
    if description:
        parts.append(f"视频描述：{description}")
    parts.append("视频音频转写：")
    parts.append(transcript)
    return "\n".join(parts).strip()


def _extract_description(raw: Any) -> str:
    for node in _walk_dicts(raw):
        description = _first_string(node, "description", "desc", "title", "videoTitle", "video_title")
        if description and not description.startswith(("http://", "https://")):
            return description[:1000]
    return ""


def _clean_transcript_text(text: str) -> str:
    lines: list[str] = []
    previous = ""
    for raw_line in (text or "").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or line == previous:
            continue
        lines.append(line)
        previous = line
    return "\n".join(lines).strip()


def _wechat_channels_short_id(source_url: str) -> str:
    url = canonical_source_url(source_url)
    match = re.search(r"/sph/([A-Za-z0-9]+)", url)
    if match:
        return match.group(1)
    match = re.search(r"[?&]id=([A-Za-z0-9]+)", url)
    if match:
        return match.group(1)
    return ""


def _summarize_payload(data: Any) -> str:
    text = str(data)
    return text[:1000] + ("..." if len(text) > 1000 else "")
