"""Shared quota circuit breaker for paid model providers.

The provider APIs use overlapping status codes for transient throttling and
for an exhausted account balance.  This module keeps those cases separate:
quota failures trip a short-lived Redis-backed breaker and are never retried.
The normal distributed concurrency gate still provides the fail-closed network
boundary for production calls; a local Redis read failure is therefore
fail-open here so unit tests and non-generation tooling remain usable.
"""

from __future__ import annotations

import logging
import threading
import time
from functools import lru_cache
from ssl import CERT_NONE
from typing import Any

import redis
import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

QUOTA_EXHAUSTED_MARKERS = (
    "insufficient_quota",
    "credit_balance_exhausted",
    "no credits remaining",
    "exceeded your current quota",
    "quota_exceeded",
    "quota exceeded",
    "quota exhausted",
    "billing_not_active",
    "billing unavailable",
    "billing hard limit",
    # Google Vertex/GenAI returns this deterministic 403 while the project is
    # in a billing dunning state.  It is not a transient permission failure:
    # retrying the same paid request only burns another attempt and cannot
    # change the project-level decision.
    "lightning dunning decision is deny",
    "dunning decision is deny",
    "permission_denied",
    "permission denied",
    "resource_exhausted",
    "resource exhausted",
    "余额不足",
    "额度不足",
    "额度已耗尽",
    "账单不可用",
    "配额耗尽",
)

_KEY_PREFIX = "video-plan:paid-api-breaker:"
_DEFAULT_TTL_SECONDS = 1800
_DEFAULT_RATE_LIMIT_TTL_SECONDS = 60
_local_breakers: dict[str, tuple[float, str]] = {}
_local_breakers_lock = threading.RLock()


class PaidApiQuotaExhausted(RuntimeError):
    """Raised before a paid request when a provider is quota-blocked."""


class PaidApiGuardUnavailable(RuntimeError):
    """Raised when production cannot verify the shared breaker state."""


def error_text(value: object) -> str:
    """Return a normalized exception/error string without assuming an SDK type."""

    if isinstance(value, BaseException):
        return f"{value.__class__.__name__}: {value}".lower()
    return str(value or "").lower()


def is_quota_exhausted(value: object) -> bool:
    """Recognize hard balance/quota failures, including provider 429 payloads."""

    message = error_text(value)
    return bool(message) and any(marker in message for marker in QUOTA_EXHAUSTED_MARKERS)


def is_transient_rate_limit(value: object) -> bool:
    """Return true for provider throttling that should recover quickly.

    Vertex uses ``429 RESOURCE_EXHAUSTED`` for a temporary request/token-rate
    window.  It is materially different from a billing denial such as
    ``insufficient_quota`` or a dunning decision.  Keep the broad quota
    predicate above for compatibility, but give this case a short breaker
    TTL so a brief upstream throttle cannot park every book for 30 minutes.
    """

    message = error_text(value)
    if not message:
        return False
    hard_markers = (
        "insufficient_quota",
        "credit_balance_exhausted",
        "no credits remaining",
        "billing_not_active",
        "billing unavailable",
        "billing hard limit",
        "dunning decision is deny",
        "permission_denied",
        "permission denied",
    )
    if any(marker in message for marker in hard_markers):
        return False
    return any(
        marker in message
        for marker in (
            "429",
            "resource_exhausted",
            "resource exhausted",
            "rate limit",
            "rate_limit",
            "too many requests",
            "try again later",
            "retry-after",
        )
    )


def is_safe_preflight_error(value: object) -> bool:
    """Return true only when a request could not have reached the provider.

    A connect failure occurs before an HTTP connection is established. Read,
    write, protocol, and generic timeout failures are deliberately excluded:
    the provider may have accepted the request and billed it already.
    OpenAI-compatible SDKs wrap the underlying ``httpx`` exception, so inspect
    the complete cause/context chain rather than matching a message string.
    """

    pending: list[object] = [value]
    seen: set[int] = set()
    while pending:
        current = pending.pop(0)
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        if isinstance(current, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError)):
            return True
        pending.extend(
            child
            for child in (
                getattr(current, "__cause__", None),
                getattr(current, "__context__", None),
            )
            if child is not None
        )
    return False


def _redis_url() -> str:
    return (
        str(
            getattr(settings, "PAID_API_GATE_REDIS_URL", "")
            or getattr(settings, "REDIS_URL", "")
            or getattr(settings, "CELERY_BROKER_URL", "")
            or ""
        )
        .strip()
    )


@lru_cache(maxsize=1)
def _redis_client() -> redis.Redis:
    url = _redis_url()
    if not url:
        raise RuntimeError("paid API breaker missing Redis URL")
    kwargs: dict[str, Any] = {
        "socket_connect_timeout": 5,
        "socket_timeout": 5,
        "decode_responses": True,
    }
    if url.startswith("rediss://"):
        kwargs["ssl_cert_reqs"] = CERT_NONE
    return redis.Redis.from_url(url, **kwargs)


def _key(provider: str) -> str:
    return f"{_KEY_PREFIX}{_provider_group(provider)}"


def _provider_group(provider: str) -> str:
    """Map endpoint labels to the billing account that actually pays them."""

    normalized = str(provider or "unknown").strip().lower() or "unknown"
    # The application can use several OpenAI endpoints/keys labels, but they
    # normally draw from the same account balance.  A quota response on one
    # endpoint must therefore stop all OpenAI paid endpoints.
    if normalized == "openai" or normalized.startswith("openai-"):
        return "openai"
    return normalized


def _local_reason(provider: str) -> str | None:
    group = _provider_group(provider)
    now = time.monotonic()
    with _local_breakers_lock:
        entry = _local_breakers.get(group)
        if entry is None:
            return None
        expires_at, detail = entry
        if expires_at <= now:
            _local_breakers.pop(group, None)
            return None
        return detail


def trip(provider: str, reason: object, *, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
    """Trip a breaker without extending an already-active cooldown.

    A queued task can observe an existing breaker and raise
    ``PaidApiQuotaExhausted`` before it reaches the provider.  Older code fed
    that derived exception back into ``trip`` and refreshed the full TTL on
    every worker retry, creating a permanent pending/processing loop.  The
    first provider signal owns the cooldown; later signals preserve its
    original expiry and reason.
    """

    detail = str(reason or "quota exhausted")[:500]
    ttl = max(60, int(ttl_seconds))
    group = _provider_group(provider)
    key = _key(group)
    # Redis is the cross-worker source of truth.  Reserve the key only when it
    # is absent (SET NX), so a blocked retry can never prolong the window.
    try:
        client = _redis_client()
        try:
            created = bool(client.set(key, detail, ex=ttl, nx=True))
        except TypeError:
            # Keep lightweight test/dummy Redis clients compatible while the
            # production client uses the atomic NX form above.
            if client.get(key) is not None:
                created = False
            else:
                client.set(key, detail, ex=ttl)
                created = True
        if not created:
            try:
                existing_ttl = int(client.ttl(key) or 0)
            except AttributeError:
                existing_ttl = -1
            existing_detail = client.get(key)
            with _local_breakers_lock:
                local = _local_breakers.get(group)
                if local is None or local[0] <= time.monotonic():
                    if existing_ttl > 0:
                        _local_breakers[group] = (
                            time.monotonic() + existing_ttl,
                            str(existing_detail or detail)[:500],
                        )
            logger.info(
                "paid API breaker already active provider=%s ttl_remaining=%ss; cooldown preserved",
                group,
                existing_ttl,
            )
            return
    except Exception:
        # The normal paid-api concurrency gate already fails closed when Redis
        # is unavailable.  Keep a process-local stop, but do not turn breaker
        # telemetry failure into a new application-wide outage.
        logger.warning("paid API breaker write failed provider=%s", provider, exc_info=True)

    # Keep a local stop even when Redis is temporarily unavailable.  Do not
    # refresh an active local cooldown either.
    with _local_breakers_lock:
        local = _local_breakers.get(group)
        if local is not None and local[0] > time.monotonic():
            return
        _local_breakers[group] = (time.monotonic() + ttl, detail)

    logger.error(
        "paid API quota breaker tripped provider=%s ttl=%ss reason=%s",
        group,
        ttl,
        detail[:240],
    )


def reason(provider: str) -> str | None:
    """Return the active breaker reason, or ``None`` when it is clear."""

    local = _local_reason(provider)
    if local:
        return local
    try:
        value = _redis_client().get(_key(provider))
    except Exception as exc:
        if _fail_closed():
            raise PaidApiGuardUnavailable(
                f"paid API breaker unavailable for {provider}; request blocked before network call"
            ) from exc
        logger.debug("paid API breaker read failed provider=%s", provider, exc_info=True)
        return None
    if value is None:
        return None
    return str(value)


def reset(provider: str) -> None:
    """Clear a breaker after an operator has verified provider credit."""

    group = _provider_group(provider)
    with _local_breakers_lock:
        _local_breakers.pop(group, None)
    try:
        _redis_client().delete(_key(group))
    except Exception:
        logger.warning("paid API breaker reset failed provider=%s", provider, exc_info=True)


def guard(provider: str) -> None:
    """Raise before a new paid request while the provider is breaker-blocked."""

    active_reason = reason(provider)
    if active_reason:
        raise PaidApiQuotaExhausted(
            f"{provider} quota exhausted; request blocked before network call: {active_reason}"
        )


def _fail_closed() -> bool:
    configured = getattr(settings, "PAID_API_GUARD_FAIL_CLOSED", None)
    if configured is not None:
        return bool(configured)
    return str(getattr(settings, "APP_ENV", "")).strip().lower() == "production"


def trip_if_quota(provider: str, value: object) -> bool:
    """Trip and report whether ``value`` represents a hard quota failure."""

    if not is_quota_exhausted(value):
        return False
    # This is a derived local exception, not a new provider response.  Calling
    # trip() again here used to refresh the Redis TTL on every retry.
    if isinstance(value, PaidApiQuotaExhausted):
        normalized = error_text(value)
        if "blocked before network call" in normalized or "blocked from retry" in normalized:
            return True
    ttl = (
        int(
            getattr(
                settings,
                "PAID_API_RATE_LIMIT_TTL_SECONDS",
                _DEFAULT_RATE_LIMIT_TTL_SECONDS,
            )
            or _DEFAULT_RATE_LIMIT_TTL_SECONDS
        )
        if is_transient_rate_limit(value)
        else _DEFAULT_TTL_SECONDS
    )
    trip(provider, value, ttl_seconds=ttl)
    return True


__all__ = [
    "PaidApiQuotaExhausted",
    "PaidApiGuardUnavailable",
    "QUOTA_EXHAUSTED_MARKERS",
    "error_text",
    "guard",
    "is_quota_exhausted",
    "is_transient_rate_limit",
    "is_safe_preflight_error",
    "reason",
    "reset",
    "trip",
    "trip_if_quota",
]
