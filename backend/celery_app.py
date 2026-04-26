"""
Gravity Singularity - Celery 实例
Broker / Backend 都用 Redis (Upstash 兼容)。

启动 worker (Windows / macOS / Linux 通用):
  # 开发环境: solo 池避免 Windows fork 限制
  celery -A celery_app.celery_app worker --loglevel=info --pool=solo

  # 生产环境 (Linux):
  celery -A celery_app.celery_app worker --loglevel=info --concurrency=4
"""
from celery import Celery

from app.core.config import settings

redis_ssl_query = "?ssl_cert_reqs=none"
broker_url = settings.CELERY_BROKER_URL if "ssl_cert_reqs=" in settings.CELERY_BROKER_URL else f"{settings.CELERY_BROKER_URL}{redis_ssl_query}"
backend_url = settings.CELERY_RESULT_BACKEND if "ssl_cert_reqs=" in settings.CELERY_RESULT_BACKEND else f"{settings.CELERY_RESULT_BACKEND}{redis_ssl_query}"

celery_app = Celery(
    "gravity_singularity",
    broker=broker_url,
    backend=backend_url,
    include=["app.tasks.tasks", "app.tasks.system_tasks"],
)

import ssl

celery_app.conf.broker_use_ssl = {"ssl_cert_reqs": ssl.CERT_NONE}
celery_app.conf.redis_backend_use_ssl = {"ssl_cert_reqs": ssl.CERT_NONE}

celery_app.autodiscover_tasks(["app.tasks"], force=True)

celery_app.conf.update(
    # --- 序列化 ---
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",

    # --- 时区 ---
    timezone="Asia/Shanghai",
    enable_utc=True,

    # --- 可靠性 ---
    task_track_started=True,                  # 让 PENDING/STARTED/SUCCESS 都可见
    task_acks_late=True,                      # worker 崩溃前未完成的任务会重投
    worker_prefetch_multiplier=1,             # LLM 任务长，避免一个 worker 抢一堆
    broker_connection_retry_on_startup=True,  # 兼容 Upstash 偶发抖动

    # --- 时间限制 (5000字章节循环最长场景) ---
    task_time_limit=60 * 60,        # 1 小时硬超时 (KILL)
    task_soft_time_limit=55 * 60,   # 55 分钟软超时 (raise SoftTimeLimitExceeded)

    # --- 结果保留 ---
    result_expires=60 * 60 * 24,    # 24h
)

# 让 `from celery_app import celery_app` 与 `celery -A celery_app worker` 都能识别
__all__ = ["celery_app"]
