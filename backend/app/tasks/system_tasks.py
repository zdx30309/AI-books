from celery_app import celery_app

@celery_app.task(name="test_ping_task")
def ping_task(word: str):
    return f"Pong! Received: {word}"
