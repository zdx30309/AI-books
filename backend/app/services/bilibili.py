"""Bilibili transcript extraction with subtitle-first, audio fallback behavior."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.services.media_transcription import MediaTranscriptionError, transcribe_media_url
from app.services.video_sources import canonical_source_url
from app.utils.distributed_limiter import distributed_semaphore

logger = logging.getLogger(__name__)

_BVID_RE = re.compile(r"(BV[A-Za-z0-9]+|av\d+)", re.IGNORECASE)


class BilibiliTranscriptError(RuntimeError):
    pass


@dataclass(frozen=True)
class BilibiliTranscriptResult:
    text: str
    bvid: str
    provider: str
    title: str | None = None
    duration: int | None = None


def extract_bilibili_text(source_url: str, *, config: dict[str, Any] | None = None) -> BilibiliTranscriptResult:
    """Fetch Bilibili transcript text after Qingdou fails."""
    if not settings.BILIBILI_TRANSCRIPT_FALLBACK_ENABLED:
        raise BilibiliTranscriptError("Bilibili transcript fallback is disabled")

    config = dict(config or {})
    canonical = canonical_source_url(source_url)
    bvid = extract_bilibili_video_id(canonical or source_url)
    headers = _request_headers(bvid)
    min_chars = int(config.get("bilibili_transcript_min_chars") or settings.BILIBILI_TRANSCRIPT_MIN_CHARS)
    timeout = int(config.get("bilibili_request_timeout_seconds") or 30)
    errors: list[str] = []

    try:
        with httpx.Client(timeout=max(10, timeout), follow_redirects=True, headers=headers) as client:
            view = _fetch_view(client, bvid)
            title = str(view.get("title") or "")
            cid = view.get("cid")
            duration = _safe_int(view.get("duration"))
            if not cid:
                raise BilibiliTranscriptError(f"Bilibili view response has no cid for {bvid}")

            subtitle_text = _fetch_subtitle_text(client, bvid=bvid, cid=int(cid), headers=headers)
            subtitle_text = _clean_lines(subtitle_text)
            if len(subtitle_text) >= min_chars:
                return BilibiliTranscriptResult(
                    text=_with_title(title, subtitle_text),
                    bvid=bvid,
                    provider="bilibili_subtitle",
                    title=title or None,
                    duration=duration,
                )
            if subtitle_text:
                errors.append(f"subtitle too short ({len(subtitle_text)} chars)")

            audio_url = _fetch_audio_url(client, bvid=bvid, cid=int(cid))
    except Exception as exc:
        if isinstance(exc, BilibiliTranscriptError):
            raise
        raise BilibiliTranscriptError(f"Bilibili metadata/subtitle fetch failed: {exc}") from exc

    try:
        with distributed_semaphore(
            "bilibili_audio_transcription",
            limit=int(
                config.get("bilibili_audio_transcription_max_concurrent")
                or settings.BILIBILI_AUDIO_TRANSCRIPTION_MAX_CONCURRENT
                or 1
            ),
            wait_timeout_seconds=int(
                config.get("bilibili_audio_gate_wait_timeout_seconds")
                or settings.BILIBILI_AUDIO_GATE_WAIT_TIMEOUT_SECONDS
                or settings.PAID_API_GATE_WAIT_TIMEOUT_SECONDS
            ),
        ):
            text = transcribe_media_url(
                audio_url,
                request_headers=headers,
                download_timeout=int(
                    config.get("bilibili_media_download_timeout_seconds")
                    or settings.BILIBILI_MEDIA_DOWNLOAD_TIMEOUT_SECONDS
                ),
                transcribe_timeout=int(
                    config.get("bilibili_transcription_timeout_seconds")
                    or settings.BILIBILI_TRANSCRIPTION_TIMEOUT_SECONDS
                ),
                max_media_bytes=int(config.get("bilibili_media_max_bytes") or settings.BILIBILI_MEDIA_MAX_BYTES),
                max_audio_bytes=int(config.get("bilibili_audio_max_bytes") or settings.BILIBILI_AUDIO_MAX_BYTES),
                model=str(
                    config.get("bilibili_audio_transcription_model")
                    or settings.BILIBILI_AUDIO_TRANSCRIPTION_MODEL
                    or settings.YOUTUBE_AUDIO_TRANSCRIPTION_MODEL
                ),
            )
    except MediaTranscriptionError as exc:
        if errors:
            raise BilibiliTranscriptError(
                "Bilibili subtitle unavailable and audio transcription failed; " + " | ".join(errors) + f" | {exc}"
            ) from exc
        raise BilibiliTranscriptError(f"Bilibili audio transcription failed: {exc}") from exc

    text = _clean_lines(text)
    if len(text) < min_chars:
        raise BilibiliTranscriptError(f"Bilibili audio transcript too short ({len(text)} chars < {min_chars})")
    return BilibiliTranscriptResult(
        text=_with_title(title, text),
        bvid=bvid,
        provider="openai_audio",
        title=title or None,
        duration=duration,
    )


def extract_bilibili_video_id(source_url: str) -> str:
    raw = canonical_source_url(source_url) or source_url
    match = _BVID_RE.search(raw)
    if match:
        return match.group(1)
    try:
        parsed = urlparse(raw)
    except Exception:
        parsed = None
    if parsed is not None:
        for part in (parsed.path or "").split("/"):
            match = _BVID_RE.fullmatch(part)
            if match:
                return match.group(1)
    raise BilibiliTranscriptError(f"could not extract Bilibili video id from URL: {source_url}")


def _request_headers(bvid: str) -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        ),
        "Referer": f"https://www.bilibili.com/video/{bvid}/",
        "Origin": "https://www.bilibili.com",
    }


def _fetch_view(client: httpx.Client, bvid: str) -> dict[str, Any]:
    response = client.get("https://api.bilibili.com/x/web-interface/view", params={"bvid": bvid})
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 0:
        raise BilibiliTranscriptError(f"Bilibili view API failed for {bvid}: {data}")
    view = data.get("data") or {}
    if not isinstance(view, dict):
        raise BilibiliTranscriptError(f"Bilibili view API returned invalid data for {bvid}: {data}")
    return view


def _fetch_subtitle_text(client: httpx.Client, *, bvid: str, cid: int, headers: dict[str, str]) -> str:
    response = client.get(
        "https://api.bilibili.com/x/player/v2",
        params={"bvid": bvid, "cid": cid},
    )
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 0:
        raise BilibiliTranscriptError(f"Bilibili subtitle API failed for {bvid}: {data}")
    subtitles = (((data.get("data") or {}).get("subtitle") or {}).get("subtitles") or [])
    errors: list[str] = []
    for item in subtitles:
        url = str((item or {}).get("subtitle_url") or "").strip()
        if not url:
            continue
        if url.startswith("//"):
            url = "https:" + url
        try:
            subtitle_response = client.get(url, headers=headers)
            subtitle_response.raise_for_status()
            text = _parse_subtitle_json(subtitle_response.json())
            if text.strip():
                return text
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")
    if errors:
        logger.warning("Bilibili subtitle tracks failed bvid=%s errors=%s", bvid, " | ".join(errors[:3]))
    return ""


def _parse_subtitle_json(data: Any) -> str:
    body = data.get("body") if isinstance(data, dict) else None
    if body is None and isinstance(data, dict):
        body = ((data.get("data") or {}).get("body") if isinstance(data.get("data"), dict) else None)
    lines: list[str] = []
    for item in body or []:
        if isinstance(item, dict):
            content = str(item.get("content") or "").strip()
            if content:
                lines.append(content)
    return "\n".join(lines)


def _fetch_audio_url(client: httpx.Client, *, bvid: str, cid: int) -> str:
    response = client.get(
        "https://api.bilibili.com/x/player/playurl",
        params={"bvid": bvid, "cid": cid, "fnval": 16, "fourk": 1},
    )
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 0:
        raise BilibiliTranscriptError(f"Bilibili playurl API failed for {bvid}: {data}")
    dash = ((data.get("data") or {}).get("dash") or {})
    audio_items = dash.get("audio") or []
    if not audio_items:
        raise BilibiliTranscriptError(f"Bilibili playurl returned no audio track for {bvid}")
    audio_items = sorted(audio_items, key=lambda item: int((item or {}).get("bandwidth") or 0), reverse=True)
    for item in audio_items:
        url = str((item or {}).get("baseUrl") or (item or {}).get("base_url") or "").strip()
        if url.startswith(("http://", "https://")):
            return url
    raise BilibiliTranscriptError(f"Bilibili playurl audio tracks have no usable URL for {bvid}")


def _clean_lines(text: str) -> str:
    lines: list[str] = []
    previous = ""
    for raw_line in (text or "").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or line == previous:
            continue
        lines.append(line)
        previous = line
    return "\n".join(lines).strip()


def _with_title(title: str, text: str) -> str:
    clean_title = (title or "").strip()
    clean_text = (text or "").strip()
    if not clean_title:
        return clean_text
    return f"视频标题：{clean_title}\n\n视频文稿：\n{clean_text}"


def _safe_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
