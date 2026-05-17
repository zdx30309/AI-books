"""YouTube transcript extraction without paid model APIs."""
from __future__ import annotations

import html
import json
import logging
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from app.core.config import PROJECT_ROOT, settings

logger = logging.getLogger(__name__)

_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
    "youtu.be",
}
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_TIMESTAMP_RE = re.compile(r"^\s*\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s+-->\s+")
_VTT_TIMESTAMP_RE = re.compile(r"^\s*\d{2}:\d{2}\.\d{3}\s+-->\s+")
_TAG_RE = re.compile(r"<[^>]+>")
_BRACKET_NOISE_RE = re.compile(
    r"^\s*[\[(](?:music|applause|laughter|laughs|silence|noise|inaudible|foreign language|音乐|掌声|笑声)[\])]\s*$",
    re.IGNORECASE,
)


class YouTubeTranscriptError(RuntimeError):
    """Raised when all local/free YouTube transcript providers fail."""


class _TimeoutSessionMixin:
    def __init__(self, *args: Any, request_timeout: int = 45, **kwargs: Any) -> None:
        self._youtube_request_timeout = request_timeout
        super().__init__(*args, **kwargs)

    def request(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", self._youtube_request_timeout)
        return super().request(*args, **kwargs)


@dataclass(frozen=True)
class YouTubeTranscriptResult:
    text: str
    video_id: str
    provider: str
    language: str | None = None
    is_generated: bool | None = None


def is_youtube_url(source_url: str) -> bool:
    """Return true for common YouTube URL shapes."""
    try:
        parsed = urlparse((source_url or "").strip())
    except Exception:
        return False
    host = (parsed.netloc or "").lower().split("@")[-1].split(":")[0]
    return host in _YOUTUBE_HOSTS or host.endswith(".youtube.com")


def extract_youtube_video_id(source_url: str) -> str:
    """Extract the canonical 11-character YouTube video id."""
    raw = (source_url or "").strip()
    if _VIDEO_ID_RE.match(raw):
        return raw

    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower().split("@")[-1].split(":")[0]
    path_parts = [part for part in (parsed.path or "").split("/") if part]

    if host == "youtu.be" and path_parts:
        candidate = path_parts[0]
        if _VIDEO_ID_RE.match(candidate):
            return candidate

    if host in _YOUTUBE_HOSTS or host.endswith(".youtube.com"):
        query_id = parse_qs(parsed.query).get("v", [""])[0]
        if _VIDEO_ID_RE.match(query_id):
            return query_id
        if path_parts:
            if path_parts[0] in {"shorts", "embed", "live", "v"} and len(path_parts) > 1:
                candidate = path_parts[1]
            else:
                candidate = path_parts[-1]
            if _VIDEO_ID_RE.match(candidate):
                return candidate

    match = re.search(r"(?:v=|/shorts/|/embed/|youtu\.be/)([A-Za-z0-9_-]{11})", raw)
    if match:
        return match.group(1)

    raise YouTubeTranscriptError(f"could not extract YouTube video id from URL: {source_url}")


def extract_youtube_text(source_url: str, *, config: dict[str, Any] | None = None) -> YouTubeTranscriptResult:
    """Fetch transcript text through multiple free providers, fastest first."""
    config = dict(config or {})
    video_id = extract_youtube_video_id(source_url)
    languages = _language_preferences(config)
    timeout = int(config.get("youtube_transcript_timeout_seconds") or settings.YOUTUBE_TRANSCRIPT_TIMEOUT_SECONDS)
    total_timeout = int(
        config.get("youtube_transcript_total_timeout_seconds")
        or settings.YOUTUBE_TRANSCRIPT_TOTAL_TIMEOUT_SECONDS
        or timeout
    )
    deadline = time.monotonic() + total_timeout if total_timeout > 0 else None
    providers = _provider_preferences(config)
    min_chars = int(config.get("youtube_transcript_min_chars") or settings.YOUTUBE_TRANSCRIPT_MIN_CHARS)
    errors: list[str] = []

    for idx, provider in enumerate(providers):
        try:
            remaining = _remaining_timeout(deadline)
            if remaining is not None and remaining <= 0:
                raise YouTubeTranscriptError(f"overall YouTube transcript timeout after {total_timeout}s")
            remaining_providers = max(1, len(providers) - idx)
            attempt_timeout = _provider_attempt_timeout(
                provider_timeout=timeout,
                remaining_timeout=remaining,
                remaining_providers=remaining_providers,
            )
            if provider == "youtube_transcript_api":
                result = _from_youtube_transcript_api(video_id, languages=languages, timeout=attempt_timeout)
            elif provider == "yt_dlp":
                result = _from_yt_dlp(
                    source_url,
                    video_id=video_id,
                    languages=languages,
                    timeout=attempt_timeout,
                    deadline=deadline,
                )
            elif provider == "pytubefix":
                result = _from_pytubefix(source_url, video_id=video_id, languages=languages)
            else:
                errors.append(f"{provider}: unknown provider")
                continue
            text = _clean_transcript_text(result.text)
            if len(text) < min_chars:
                raise YouTubeTranscriptError(
                    f"{provider} transcript too short ({len(text)} chars < {min_chars})"
                )
            logger.info(
                "YouTube transcript resolved provider=%s video_id=%s language=%s generated=%s chars=%d",
                result.provider,
                result.video_id,
                result.language,
                result.is_generated,
                len(text),
            )
            return YouTubeTranscriptResult(
                text=text,
                video_id=result.video_id,
                provider=result.provider,
                language=result.language,
                is_generated=result.is_generated,
            )
        except Exception as exc:
            logger.warning("YouTube transcript provider failed provider=%s video_id=%s error=%s", provider, video_id, exc)
            errors.append(f"{provider}: {type(exc).__name__}: {exc}")

    raise YouTubeTranscriptError(
        "all YouTube transcript providers failed; " + " | ".join(errors[:6])
    )


def _provider_preferences(config: dict[str, Any]) -> list[str]:
    raw = str(config.get("youtube_transcript_providers") or settings.YOUTUBE_TRANSCRIPT_PROVIDERS or "")
    providers = [item.strip().lower() for item in raw.split(",") if item.strip()]
    valid = {"youtube_transcript_api", "yt_dlp", "pytubefix"}
    return [item for item in providers if item in valid] or ["youtube_transcript_api", "yt_dlp"]


def _language_preferences(config: dict[str, Any]) -> list[str]:
    raw = config.get("youtube_transcript_languages") or settings.YOUTUBE_TRANSCRIPT_LANGUAGES
    if isinstance(raw, (list, tuple)):
        values = [str(item).strip() for item in raw]
    else:
        values = [item.strip() for item in str(raw or "").split(",")]
    languages = [item for item in values if item]
    return languages or ["zh-Hans", "zh-Hant", "zh", "en", "en-US", "en-GB"]


def _remaining_timeout(deadline: float | None) -> int | None:
    if deadline is None:
        return None
    return max(0, int(deadline - time.monotonic()))


def _provider_attempt_timeout(
    *,
    provider_timeout: int,
    remaining_timeout: int | None,
    remaining_providers: int,
) -> int:
    timeout = max(1, int(provider_timeout or 1))
    if remaining_timeout is None:
        return timeout
    return max(1, min(timeout, int(remaining_timeout / max(1, remaining_providers)) or 1))


def _clean_transcript_text(text: str) -> str:
    lines: list[str] = []
    previous = ""
    for raw_line in (text or "").splitlines():
        line = html.unescape(raw_line)
        line = _TAG_RE.sub("", line)
        line = re.sub(r"\s+", " ", line).strip()
        if not line or _BRACKET_NOISE_RE.match(line):
            continue
        if line == previous:
            continue
        lines.append(line)
        previous = line
    return "\n".join(lines).strip()


def _segments_to_text(items: Any) -> str:
    lines: list[str] = []
    if hasattr(items, "to_raw_data"):
        items = items.to_raw_data()
    for item in items or []:
        if isinstance(item, dict):
            text = item.get("text")
        else:
            text = getattr(item, "text", None)
        if text:
            lines.append(str(text))
    return "\n".join(lines)


def _from_youtube_transcript_api(video_id: str, *, languages: list[str], timeout: int) -> YouTubeTranscriptResult:
    proxy = _proxy_url()
    payload = {
        "video_id": video_id,
        "languages": languages,
        "timeout": max(1, int(timeout or 1)),
        "proxy": proxy,
    }
    code = r"""
import json
import sys

payload = json.loads(sys.stdin.read() or "{}")
video_id = payload["video_id"]
languages = payload.get("languages") or ["zh-Hans", "zh-Hant", "zh", "en", "en-US", "en-GB"]
timeout = max(1, int(payload.get("timeout") or 1))
proxy = (payload.get("proxy") or "").strip()

from requests import Session
from youtube_transcript_api import YouTubeTranscriptApi


class TimeoutSession(Session):
    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return super().request(*args, **kwargs)


session = TimeoutSession()
if proxy:
    session.proxies.update({"http": proxy, "https": proxy})

try:
    api = YouTubeTranscriptApi(http_client=session)
except TypeError:
    api = YouTubeTranscriptApi()

try:
    if hasattr(api, "fetch"):
        fetched = api.fetch(video_id, languages=languages)
    else:
        fetched = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)
except TypeError:
    fetched = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)

items = fetched.to_raw_data() if hasattr(fetched, "to_raw_data") else fetched
lines = []
for item in items or []:
    if isinstance(item, dict):
        text = item.get("text")
    else:
        text = getattr(item, "text", None)
    if text:
        lines.append(str(text))

print(json.dumps({
    "text": "\n".join(lines),
    "language": getattr(fetched, "language_code", None),
    "is_generated": getattr(fetched, "is_generated", None),
}, ensure_ascii=False))
"""
    try:
        completed = subprocess.run(
            [sys.executable, "-c", code],
            input=json.dumps(payload),
            check=False,
            capture_output=True,
            text=True,
            timeout=max(2, int(timeout or 1)),
        )
    except subprocess.TimeoutExpired as exc:
        raise YouTubeTranscriptError(f"youtube-transcript-api timed out after {timeout}s") from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        if "No module named" in stderr and "youtube_transcript_api" in stderr:
            raise YouTubeTranscriptError("youtube-transcript-api is not installed")
        raise YouTubeTranscriptError(stderr[-700:] or f"youtube-transcript-api failed rc={completed.returncode}")

    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise YouTubeTranscriptError("youtube-transcript-api returned invalid JSON") from exc

    return YouTubeTranscriptResult(
        text=str(data.get("text") or ""),
        video_id=video_id,
        provider="youtube_transcript_api",
        language=data.get("language"),
        is_generated=data.get("is_generated"),
    )


def _from_yt_dlp(
    source_url: str,
    *,
    video_id: str,
    languages: list[str],
    timeout: int,
    deadline: float | None = None,
) -> YouTubeTranscriptResult:
    info = _yt_dlp_info(source_url, timeout=timeout)

    captions = _choose_caption(info.get("subtitles") or {}, languages)
    generated = False
    if captions is None:
        captions = _choose_caption(info.get("automatic_captions") or {}, languages)
        generated = True
    if captions is None:
        raise YouTubeTranscriptError("yt-dlp found no usable subtitle track")

    language, entry = captions
    subtitle_url = entry.get("url")
    if not subtitle_url:
        raise YouTubeTranscriptError(f"yt-dlp subtitle track has no URL for language={language}")
    ext = str(entry.get("ext") or "").lower()
    remaining = _remaining_timeout(deadline)
    raw = _http_get_text(subtitle_url, timeout=max(1, min(timeout, remaining)) if remaining is not None else timeout)
    return YouTubeTranscriptResult(
        text=_parse_subtitle_payload(raw, ext),
        video_id=video_id,
        provider="yt_dlp",
        language=language,
        is_generated=generated,
    )


def _yt_dlp_info(source_url: str, *, timeout: int) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--dump-single-json",
        "--skip-download",
        "--no-warnings",
        "--no-playlist",
        "--socket-timeout",
        str(max(1, timeout)),
        "--retries",
        "1",
        "--fragment-retries",
        "1",
        "--extractor-args",
        "youtube:skip=hls,dash",
    ]
    proxy = _proxy_url()
    if proxy:
        command.extend(["--proxy", proxy])
    cookies = _cookies_file()
    if cookies:
        command.extend(["--cookies", str(cookies)])
    command.append(source_url)

    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(2, timeout),
        )
    except subprocess.TimeoutExpired as exc:
        raise YouTubeTranscriptError(f"yt-dlp timed out after {timeout}s") from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        raise YouTubeTranscriptError(f"yt-dlp failed rc={completed.returncode}: {stderr[-500:]}")

    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise YouTubeTranscriptError("yt-dlp returned invalid JSON") from exc


def _choose_caption(caption_map: dict[str, Any], languages: list[str]) -> tuple[str, dict[str, Any]] | None:
    if not caption_map:
        return None
    ordered_languages = _ordered_caption_languages(caption_map, languages)
    preferred_exts = ("json3", "srv3", "vtt", "srt", "ttml")
    for language in ordered_languages:
        entries = caption_map.get(language) or []
        if isinstance(entries, dict):
            entries = [entries]
        for ext in preferred_exts:
            for entry in entries:
                if str(entry.get("ext") or "").lower() == ext and entry.get("url"):
                    return language, entry
        for entry in entries:
            if entry.get("url"):
                return language, entry
    return None


def _ordered_caption_languages(caption_map: dict[str, Any], languages: list[str]) -> list[str]:
    available = list(caption_map.keys())
    ordered: list[str] = []
    for wanted in languages:
        wanted_lower = wanted.lower()
        for candidate in available:
            candidate_lower = candidate.lower()
            if candidate in ordered:
                continue
            if candidate_lower == wanted_lower or candidate_lower.startswith(wanted_lower + "-"):
                ordered.append(candidate)
        for candidate in available:
            if candidate in ordered:
                continue
            if wanted_lower in candidate.lower():
                ordered.append(candidate)
    ordered.extend([candidate for candidate in available if candidate not in ordered and candidate != "live_chat"])
    return ordered


def _http_get_text(url: str, *, timeout: int) -> str:
    headers = {"User-Agent": "Mozilla/5.0"}
    proxy = _proxy_url()
    with httpx.Client(timeout=timeout, follow_redirects=True, proxy=proxy or None, headers=headers) as client:
        response = client.get(url)
        response.raise_for_status()
        response.encoding = response.encoding or "utf-8"
        return response.text


def _parse_subtitle_payload(payload: str, ext: str) -> str:
    ext = (ext or "").lower()
    if ext == "json3":
        return _parse_json3(payload)
    if ext in {"srv3", "xml", "ttml"}:
        parsed = _parse_xml_subtitles(payload)
        if parsed:
            return parsed
    return _parse_vtt_or_srt(payload)


def _parse_json3(payload: str) -> str:
    data = json.loads(payload)
    lines: list[str] = []
    for event in data.get("events") or []:
        text = "".join(str(seg.get("utf8") or "") for seg in event.get("segs") or [])
        text = text.replace("\n", " ").strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def _parse_xml_subtitles(payload: str) -> str:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return ""
    lines: list[str] = []
    for node in root.iter():
        if node.text and node.text.strip():
            lines.append(node.text.strip())
    return "\n".join(lines)


def _parse_vtt_or_srt(payload: str) -> str:
    lines: list[str] = []
    for raw in payload.splitlines():
        line = raw.strip().lstrip("\ufeff")
        if not line:
            continue
        if line.upper().startswith("WEBVTT") or line.upper().startswith("NOTE"):
            continue
        if line.isdigit() or _TIMESTAMP_RE.match(line) or _VTT_TIMESTAMP_RE.match(line) or "-->" in line:
            continue
        if line.startswith("{") and line.endswith("}"):
            continue
        lines.append(line)
    return "\n".join(lines)


def _from_pytubefix(source_url: str, *, video_id: str, languages: list[str]) -> YouTubeTranscriptResult:
    try:
        from pytubefix import YouTube
    except Exception as exc:  # pragma: no cover - dependency guard
        raise YouTubeTranscriptError("pytubefix is not installed") from exc

    kwargs: dict[str, Any] = {}
    proxy = _proxy_url()
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    yt = YouTube(source_url, **kwargs)
    captions = getattr(yt, "captions", None)
    if captions is None:
        raise YouTubeTranscriptError("pytubefix found no captions collection")
    caption = _pick_pytubefix_caption(captions, languages)
    if caption is None:
        raise YouTubeTranscriptError("pytubefix found no usable caption track")

    language = (
        getattr(caption, "code", None)
        or getattr(caption, "language_code", None)
        or getattr(caption, "name", None)
    )
    for method in ("generate_srt_captions", "generate_vtt_captions"):
        if hasattr(caption, method):
            return YouTubeTranscriptResult(
                text=_parse_vtt_or_srt(getattr(caption, method)()),
                video_id=video_id,
                provider="pytubefix",
                language=str(language) if language else None,
                is_generated=None,
            )
    xml = getattr(caption, "xml_captions", None)
    if xml:
        return YouTubeTranscriptResult(
            text=_parse_xml_subtitles(str(xml)),
            video_id=video_id,
            provider="pytubefix",
            language=str(language) if language else None,
            is_generated=None,
        )
    raise YouTubeTranscriptError("pytubefix caption object exposes no text formatter")


def _pick_pytubefix_caption(captions: Any, languages: list[str]) -> Any | None:
    for language in languages:
        if hasattr(captions, "get_by_language_code"):
            try:
                caption = captions.get_by_language_code(language)
                if caption is not None:
                    return caption
            except Exception:
                pass
        try:
            caption = captions[language]
            if caption is not None:
                return caption
        except Exception:
            pass

    values: list[Any] = []
    if hasattr(captions, "caption_tracks"):
        values = list(getattr(captions, "caption_tracks") or [])
    elif hasattr(captions, "values"):
        values = list(captions.values())
    else:
        try:
            values = list(captions)
        except Exception:
            values = []
    return values[0] if values else None


def _proxy_url() -> str | None:
    return (
        settings.YOUTUBE_HTTPS_PROXY
        or settings.GOOGLE_HTTPS_PROXY
        or settings.OPENAI_HTTPS_PROXY
        or ""
    ).strip() or None


def _cookies_file() -> Path | None:
    raw = (settings.YOUTUBE_COOKIES_FILE or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        cache_dir = Path(settings.YOUTUBE_CACHE_DIR or tempfile.gettempdir())
        if not cache_dir.is_absolute():
            cache_dir = PROJECT_ROOT / cache_dir
        path = cache_dir / path
    return path if path.exists() else None
