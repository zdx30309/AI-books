import logging

from app.core.config import settings
from celery_app import celery_app

logger = logging.getLogger(__name__)

@celery_app.task(name="test_ping_task")
def ping_task(word: str):
    return f"Pong! Received: {word}"


@celery_app.task(
    name="tasks.resume_pending_task",
    queue="celery",
    max_retries=0,
)
def resume_pending_task(task_id: str) -> dict:
    """Wake one durable pending task without requiring the beat service."""

    from app.tasks.tasks import dispatch_pending_task_if_due

    return dispatch_pending_task_if_due(str(task_id))


@celery_app.task(
    name="tasks.requeue_stale_processing",
    queue=settings.CELERY_MAINTENANCE_QUEUE,
)
def requeue_stale_processing_task() -> int:
    """Run the recovery passes on the isolated maintenance queue.

    This task is deliberately a small orchestrator.  Each pass owns its own
    database session and catches failures locally, so a transient error in one
    recovery path cannot prevent later passes (or the cover watchdog) from
    running on the next beat tick.  The return value remains an integer for
    compatibility with the original watchdog task and is the total number of
    rows/jobs recovered or published by successful passes.
    """

    if not settings.STALE_TASK_WATCHDOG_ENABLED:
        logger.info("maintenance watchdog disabled by configuration")
        return 0

    from app.tasks.tasks import (
        recover_retryable_failed_tasks,
        revive_recoverable_terminal_input_reviews,
        requeue_due_pending_retry_tasks,
        requeue_orphan_pending_tasks,
        requeue_stale_processing_tasks,
    )
    from app.tasks.cover_tasks import requeue_stale_cover_jobs
    from app.tasks.recovery import (
        recover_completed_integrity_tasks,
        recover_completed_public_domain_tasks,
        recover_deterministic_final_format_tasks,
    )

    passes = (
        ("completed finance integrity", recover_completed_integrity_tasks),
        ("completed public-domain artifacts", recover_completed_public_domain_tasks),
        ("deterministic final-format artifacts", recover_deterministic_final_format_tasks),
        ("stale processing tasks", requeue_stale_processing_tasks),
        ("recoverable source reviews", revive_recoverable_terminal_input_reviews),
        ("due pending retries", requeue_due_pending_retry_tasks),
        ("orphan pending tasks", requeue_orphan_pending_tasks),
        ("retryable failed tasks", recover_retryable_failed_tasks),
        ("stale cover jobs", requeue_stale_cover_jobs),
    )
    total = 0
    results: dict[str, int] = {}
    for label, recovery_pass in passes:
        try:
            count = int(recovery_pass() or 0)
        except Exception:
            # Never let one broken pass starve the remaining recovery work.
            count = 0
            logger.exception("maintenance pass failed: %s", label)
        results[label] = count
        total += count
    logger.info("maintenance watchdog completed total=%d results=%s", total, results)
    return total
