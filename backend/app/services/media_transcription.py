"""Download remote media, extract audio with ffmpeg, and transcribe it."""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx
from openai import OpenAI

from app.core.config import settings
from app.services.wechat_video_crypto import decrypt_wechat_channels_video
from app.utils.official_api import official_openai_base_url, official_proxy

logger = logging.getLogger(__name__)


class MediaTranscriptionError(RuntimeError):
    pass


def transcribe_media_url(
    media_url: str,
    *,
    decode_key: str | int | None = None,
    request_headers: dict[str, str] | None = None,
    download_timeout: int = 120,
    transcribe_timeout: int = 300,
    max_media_bytes: int = 200_000_000,
    max_audio_bytes: int = 25_000_000,
    model: str = "",
) -> str:
    """Download a video/audio URL and return an OpenAI audio transcription."""
    if not (settings.OPENAI_API_KEY or "").strip():
        raise MediaTranscriptionError("OPENAI_API_KEY is required for media transcription")

    tmp_dir = Path(tempfile.mkdtemp(prefix="media-transcribe-"))
    media_path = tmp_dir / "source.media"
    audio_path = tmp_dir / "audio.mp3"
    try:
        _download_media(media_url, media_path, request_headers=request_headers, timeout=download_timeout, max_bytes=max_media_bytes)
        if decode_key not in (None, ""):
            _decrypt_wechat_media_file(media_path, decode_key)
        _extract_audio(media_path, audio_path, timeout=download_timeout, max_bytes=max_audio_bytes)
        text = _transcribe_audio(audio_path, timeout=transcribe_timeout, model=model)
        if not text.strip():
            raise MediaTranscriptionError("OpenAI audio transcription returned empty text")
        return text.strip()
    finally:
        for path in (audio_path, media_path):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                logger.debug("failed to remove temporary media file %s", path, exc_info=True)
        try:
            tmp_dir.rmdir()
        except Exception:
            logger.debug("failed to remove temporary media dir %s", tmp_dir, exc_info=True)


def _download_media(
    url: str,
    output_path: Path,
    *,
    request_headers: dict[str, str] | None,
    timeout: int,
    max_bytes: int,
) -> None:
    media_url = (url or "").strip()
    if not media_url.startswith(("http://", "https://")):
        raise MediaTranscriptionError(f"invalid media URL: {media_url[:120]}")

    max_bytes = max(1_000_000, int(max_bytes or 1))
    headers = {"User-Agent": "Mozilla/5.0"}
    headers.update(request_headers or {})
    proxy = _download_proxy()
    try:
        with httpx.Client(timeout=max(10, timeout), follow_redirects=True, proxy=proxy or None, headers=headers) as client:
            with client.stream("GET", media_url) as response:
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > max_bytes:
                    raise MediaTranscriptionError(
                        f"media exceeds limit ({content_length} > {max_bytes} bytes)"
                    )
                written = 0
                with output_path.open("wb") as fh:
                    for chunk in response.iter_bytes():
                        if not chunk:
                            continue
                        written += len(chunk)
                        if written > max_bytes:
                            raise MediaTranscriptionError(
                                f"media exceeds limit ({written} > {max_bytes} bytes)"
                            )
                        fh.write(chunk)
    except MediaTranscriptionError:
        raise
    except Exception as exc:
        raise MediaTranscriptionError(f"media download failed: {exc}") from exc

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise MediaTranscriptionError("media download produced empty file")


def _decrypt_wechat_media_file(media_path: Path, decode_key: str | int) -> None:
    try:
        decrypted = decrypt_wechat_channels_video(media_path.read_bytes(), decode_key)
    except Exception as exc:
        raise MediaTranscriptionError(f"WeChat Channels media decrypt failed: {exc}") from exc
    if len(decrypted) >= 8 and decrypted[4:8] != b"ftyp":
        raise MediaTranscriptionError(
            f"WeChat Channels media decrypt produced invalid MP4 header: {decrypted[:8].hex()}"
        )
    tmp_path = media_path.with_suffix(".decrypted")
    tmp_path.write_bytes(decrypted)
    os.replace(tmp_path, media_path)


def _extract_audio(media_path: Path, audio_path: Path, *, timeout: int, max_bytes: int) -> None:
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(media_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "64k",
        str(audio_path),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(10, int(timeout or 10)),
        )
    except FileNotFoundError as exc:
        raise MediaTranscriptionError("ffmpeg is not installed in the backend container") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaTranscriptionError(f"ffmpeg audio extraction timed out after {timeout}s") from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        raise MediaTranscriptionError(f"ffmpeg audio extraction failed: {stderr[-700:]}")
    if not audio_path.exists() or audio_path.stat().st_size == 0:
        raise MediaTranscriptionError("ffmpeg produced no audio")
    max_bytes = max(1_000_000, int(max_bytes or 1))
    if audio_path.stat().st_size > max_bytes:
        raise MediaTranscriptionError(
            f"extracted audio exceeds limit ({audio_path.stat().st_size} > {max_bytes} bytes)"
        )


def _transcribe_audio(audio_path: Path, *, timeout: int, model: str) -> str:
    proxy = official_proxy("openai", settings.OPENAI_HTTPS_PROXY)
    client_kwargs: dict[str, Any] = {
        "api_key": settings.OPENAI_API_KEY,
        "base_url": official_openai_base_url(settings.OPENAI_BASE_URL),
        "timeout": max(30, int(timeout or 30)),
    }
    if proxy:
        client_kwargs["http_client"] = httpx.Client(proxy=proxy, timeout=max(30, int(timeout or 30)))
    client = OpenAI(**client_kwargs)
    try:
        with audio_path.open("rb") as audio_file:
            response = client.audio.transcriptions.create(
                model=(model or settings.YOUTUBE_AUDIO_TRANSCRIPTION_MODEL or "gpt-4o-mini-transcribe"),
                file=audio_file,
                response_format="text",
            )
    except Exception as exc:
        raise MediaTranscriptionError(f"OpenAI audio transcription failed: {exc}") from exc
    finally:
        http_client = client_kwargs.get("http_client")
        if http_client is not None:
            http_client.close()

    if isinstance(response, str):
        return response.strip()
    return str(getattr(response, "text", "") or response or "").strip()


def _download_proxy() -> str | None:
    return (
        settings.YOUTUBE_HTTPS_PROXY
        or settings.OPENAI_HTTPS_PROXY
        or ""
    ).strip() or None
