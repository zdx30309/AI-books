"""Redis lease that prevents duplicate Celery dispatch for the same task.

The first implementation stored the literal value ``"queued"`` with a six
hour TTL.  That is enough to suppress accidental double clicks, but it also
means a publish failure, a worker crash, or a lost Redis delivery can leave a
task invisible to every later recovery pass for six hours.  The watchdog now
uses the timestamped/force path below to reclaim only an *old* lease.  The
database run claim remains the final duplicate-execution guard.
"""
from __future__ import annotations

import logging
import time
import uuid

from app.utils.distributed_limiter import _redis_client

logger = logging.getLogger(__name__)


def _key(task_id: str) -> str:
    return f"video-plan:task-dispatch:{task_id}"


def acquire_task_dispatch_guard(
    task_id: str,
    *,
    ttl_seconds: int = 21600,
    force: bool = False,
    stale_after_seconds: int = 900,
) -> bool:
    """Acquire a dispatch lease.

    Normal callers use ``SET NX`` and therefore retain the cheap, atomic
    de-duplication behaviour.  Recovery callers may set ``force=True`` after
    they have established that the database task is orphaned.  In that mode a
    lease is replaced only when its embedded timestamp is older than
    ``stale_after_seconds``.  Legacy literal leases are treated as stale in
    force mode so deployments can heal keys written by older versions.
    """

    ttl = max(60, int(ttl_seconds or 21600))
    client = _redis_client()
    key = _key(task_id)
    value = f"queued|{time.time():.6f}|{uuid.uuid4().hex}"

    if not force:
        acquired = bool(client.set(key, value, nx=True, ex=ttl))
    else:
        stale_after = max(60, int(stale_after_seconds or 900))
        # Compare-and-set in Redis so two maintenance workers cannot both
        # replace the same lease.  A missing timestamp is an old-format lease.
        reclaim_script = """
        local current = redis.call('GET', KEYS[1])
        if not current then
            redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
            return 1
        end
        local separator = string.find(current, '|', 1, true)
        local timestamp = nil
        if separator then
            local second = string.find(current, '|', separator + 1, true)
            if second then
                timestamp = tonumber(string.sub(current, separator + 1, second - 1))
            end
        end
        if (not timestamp) or timestamp <= tonumber(ARGV[3]) then
            redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
            return 1
        end
        return 0
        """
        acquired = bool(
            client.eval(
                reclaim_script,
                1,
                key,
                value,
                ttl,
                time.time() - stale_after,
            )
            or 0
        )

    if not acquired:
        logger.info("[task %s] dispatch skipped: an active dispatch lease exists", task_id)
    return acquired


def clear_task_dispatch_guard(task_id: str) -> None:
    try:
        _redis_client().delete(_key(task_id))
    except Exception:
        logger.warning("[task %s] dispatch guard cleanup failed", task_id, exc_info=True)
