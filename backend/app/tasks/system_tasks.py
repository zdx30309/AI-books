from celery_app import celery_app

@celery_app.task(name="test_ping_task")
def ping_task(word: str):
    return f"Pong! Received: {word}"


@celery_app.task(name="tasks.requeue_stale_processing")
def requeue_stale_processing_task() -> int:
    from app.tasks.tasks import (
        recover_retryable_failed_tasks,
        requeue_due_pending_retry_tasks,
        requeue_stale_processing_tasks,
    )
    from app.tasks.cover_tasks import requeue_stale_cover_jobs

    return (
        requeue_stale_processing_tasks()
        + requeue_due_pending_retry_tasks()
        + recover_retryable_failed_tasks()
        + requeue_stale_cover_jobs()
    )
