"""Redis-backed distributed concurrency gates for paid upstream APIs."""
from __future__ import annotations

import logging
import threading
import time
import uuid
from contextlib import contextmanager
from functools import lru_cache
from ssl import CERT_NONE
from typing import Any, Callable, Iterator

import redis

from app.core.config import settings

logger = logging.getLogger(__name__)


class DistributedLimitError(RuntimeError):
    """Raised when a paid API gate cannot be acquired safely."""


def _redis_url() -> str:
    return (settings.REDIS_URL or settings.CELERY_BROKER_URL or "").strip()


@lru_cache(maxsize=1)
def _redis_client() -> redis.Redis:
    url = _redis_url()
    if not url:
        raise DistributedLimitError("paid API concurrency limiter missing REDIS_URL")
    kwargs: dict[str, Any] = {
        "socket_connect_timeout": 10,
        "socket_timeout": 10,
        "decode_responses": True,
    }
    if url.startswith("rediss://"):
        kwargs["ssl_cert_reqs"] = CERT_NONE
    return redis.Redis.from_url(url, **kwargs)


@contextmanager
def distributed_semaphore(
    name: str,
    *,
    limit: int,
    ttl_seconds: int | None = None,
    wait_timeout_seconds: int | None = None,
    poll_seconds: float = 2.0,
    should_abort: Callable[[], bool] | None = None,
    abort_exception_factory: Callable[[], BaseException] | None = None,
) -> Iterator[None]:
    """Acquire a Redis zset slot, failing closed if Redis is unavailable."""
    max_active = max(1, int(limit or 1))
    ttl = max(60, int(ttl_seconds or settings.PAID_API_GATE_TTL_SECONDS or 7200))
    wait_timeout = max(1, int(wait_timeout_seconds or settings.PAID_API_GATE_WAIT_TIMEOUT_SECONDS or 14400))
    key = f"video-plan:paid-api-gate:{name}"
    token = str(uuid.uuid4())
    deadline = time.monotonic() + wait_timeout
    client = _redis_client()
    acquired = False
    refresh_stop: threading.Event | None = None
    refresh_thread: threading.Thread | None = None
    acquire_script = """
    redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, ARGV[1])
    local active = redis.call('ZCARD', KEYS[1])
    if active < tonumber(ARGV[2]) then
        redis.call('ZADD', KEYS[1], ARGV[3], ARGV[4])
        redis.call('EXPIRE', KEYS[1], tonumber(ARGV[5]))
        return active + 1
    end
    return 0
    """

    def _raise_if_aborted() -> None:
        if should_abort is not None and should_abort():
            if abort_exception_factory is not None:
                raise abort_exception_factory()
            raise DistributedLimitError(f"paid API concurrency limiter wait aborted for {name}")

    while time.monotonic() < deadline:
        _raise_if_aborted()
        now = time.time()
        expire_before = now - ttl
        try:
            active_count = int(
                client.eval(
                    acquire_script,
                    1,
                    key,
                    expire_before,
                    max_active,
                    now,
                    token,
                    ttl,
                )
                or 0
            )
            if active_count > 0:
                acquired = True
                logger.info("paid API gate acquired name=%s active=%d/%d", name, active_count, max_active)
                break
        except Exception as exc:
            raise DistributedLimitError(f"paid API concurrency limiter unavailable for {name}: {exc}") from exc

        sleep_until = time.monotonic() + max(poll_seconds, 0.2)
        while time.monotonic() < sleep_until:
            _raise_if_aborted()
            time.sleep(min(0.2, max(0.0, sleep_until - time.monotonic())))

    if not acquired:
        raise DistributedLimitError(f"paid API concurrency limiter wait timeout for {name}, limit={max_active}")

    refresh_stop = threading.Event()

    def _refresh_slot() -> None:
        refresh_interval = max(15.0, min(60.0, ttl / 3))
        while not refresh_stop.wait(refresh_interval):
            try:
                now = time.time()
                client.zadd(key, {token: now})
                client.expire(key, ttl)
            except Exception:
                logger.warning("paid API gate refresh failed name=%s", name, exc_info=True)

    refresh_thread = threading.Thread(
        target=_refresh_slot,
        name=f"paid-api-gate-{name}",
        daemon=True,
    )
    refresh_thread.start()

    try:
        yield
    finally:
        if refresh_stop is not None:
            refresh_stop.set()
        if refresh_thread is not None:
            refresh_thread.join(timeout=2)
        try:
            client.zrem(key, token)
            logger.info("paid API gate released name=%s", name)
        except Exception:
            logger.exception("paid API gate release failed name=%s", name)
