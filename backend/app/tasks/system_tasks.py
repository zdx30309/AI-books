from celery_app import celery_app
import time


@celery_app.task(name="test_ping_task")
def ping_task(word: str):
    time.sleep(2)  # 模拟耗时任务
    return f"Pong! Received: {word}"
