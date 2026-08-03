"""Read-only public metadata for Tencent Zhuque's text detector.

The public detector does not expose its model weights or feature pipeline.  It
does expose a model-update timestamp that is useful for keeping black-box A/B
evidence from different detector generations separate.  This module only
reads that public timestamp; it never submits manuscripts or automates the
CAPTCHA-gated detection workflow.
"""
from __future__ import annotations

import hashlib
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from app.core.config import settings


PROFILE_VERSION = "zhuque-public-profile-v1"
PROVIDER_ID = "tencent_zhuque"
_CACHE_LOCK = threading.Lock()
_PROFILE_CACHE: dict[str, Any] | None = None
_PROFILE_CACHE_DEADLINE = 0.0


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_model_update_time(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("朱雀公开接口没有返回模型更新时间。")
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _generation_id(model_update_at: str) -> str:
    digest = hashlib.sha256(model_update_at.encode("utf-8")).hexdigest()[:12]
    return f"{PROVIDER_ID}@{model_update_at}#{digest}"


def _base_profile() -> dict[str, Any]:
    base_url = str(settings.AIGC_REVIEW_ZHUQUE_BASE_URL or "").rstrip("/")
    return {
        "profile_version": PROFILE_VERSION,
        "provider": PROVIDER_ID,
        "detector_url": f"{base_url}/ai-detect/ai_gen_txt/",
        "model_update_url": f"{base_url}/model/update_time",
        "minimum_text_chars": 350,
        "submission_mode": "manual_captcha_gated",
        "transport_observation": "captcha_gated_websocket",
        "result_contract": {
            "document_fields": ["confidence", "labels_ratio", "content_type"],
            "segment_fields": ["text", "label", "conf", "order", "position"],
            "labels": {"0": "human", "1": "ai", "2": "suspected"},
        },
        "scope_note": (
            "公开元数据只用于检测器代际隔离；不代表已取得服务端模型、特征权重或聚合公式。"
        ),
    }


def fetch_zhuque_detector_profile(
    *,
    http_get: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Fetch one uncached snapshot from Zhuque's public update endpoint."""

    profile = _base_profile()
    fetched_at = _iso_utc_now()
    getter = http_get or httpx.get
    try:
        response = getter(
            profile["model_update_url"],
            timeout=float(settings.AIGC_REVIEW_ZHUQUE_PROFILE_TIMEOUT_SECONDS),
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or payload.get("success") is not True or not isinstance(data, dict):
            raise ValueError("朱雀公开模型更新时间响应格式无效。")
        model_update_at = _normalize_model_update_time(data.get("updateDate"))
        return {
            **profile,
            "status": "available",
            "model_update_at": model_update_at,
            "model_generation": _generation_id(model_update_at),
            "fetched_at": fetched_at,
            "error": None,
        }
    except Exception as exc:
        return {
            **profile,
            "status": "unavailable",
            "model_update_at": None,
            "model_generation": None,
            "fetched_at": fetched_at,
            "error": f"{type(exc).__name__}: {exc}",
        }


def get_zhuque_detector_profile(*, force_refresh: bool = False) -> dict[str, Any]:
    """Return a short-lived cached public detector snapshot.

    A stale successful snapshot is preferable to silently treating detector
    generations as equal when the public endpoint has a transient outage.
    """

    global _PROFILE_CACHE, _PROFILE_CACHE_DEADLINE
    now = time.monotonic()
    with _CACHE_LOCK:
        if not force_refresh and _PROFILE_CACHE is not None and now < _PROFILE_CACHE_DEADLINE:
            return dict(_PROFILE_CACHE)

        previous = dict(_PROFILE_CACHE) if _PROFILE_CACHE else None
        current = fetch_zhuque_detector_profile()
        ttl = max(30, int(settings.AIGC_REVIEW_ZHUQUE_PROFILE_CACHE_SECONDS))
        _PROFILE_CACHE_DEADLINE = now + ttl
        if current.get("status") != "available" and previous and previous.get("model_generation"):
            current = {
                **previous,
                "status": "stale_cache",
                "fetched_at": current.get("fetched_at"),
                "error": current.get("error"),
            }
        _PROFILE_CACHE = current
        return dict(current)


def report_detector_snapshot(profile: dict[str, Any] | None) -> dict[str, Any] | None:
    """Reduce a live profile to immutable provenance safe to embed in reports."""

    if not isinstance(profile, dict):
        return None
    keys = (
        "profile_version",
        "provider",
        "status",
        "model_update_at",
        "model_generation",
        "fetched_at",
        "detector_url",
        "model_update_url",
        "scope_note",
    )
    snapshot = {key: profile.get(key) for key in keys}
    snapshot["captured_at"] = _iso_utc_now()
    snapshot["provenance_basis"] = "public_model_update_endpoint_observed_at_report_upload"
    return snapshot


def reset_zhuque_detector_profile_cache() -> None:
    """Test helper; production callers should use the normal TTL cache."""

    global _PROFILE_CACHE, _PROFILE_CACHE_DEADLINE
    with _CACHE_LOCK:
        _PROFILE_CACHE = None
        _PROFILE_CACHE_DEADLINE = 0.0
