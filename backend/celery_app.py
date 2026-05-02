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


def _with_redis_ssl_options(url: str) -> str:
    if not url.startswith("rediss://") or "ssl_cert_reqs=" in url:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}ssl_cert_reqs=none"


def _socket_timeout(value: int) -> int | None:
    return None if value <= 0 else value


broker_url = _with_redis_ssl_options(settings.CELERY_BROKER_URL)
backend_url = _with_redis_ssl_options(settings.CELERY_RESULT_BACKEND)

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
    task_track_started=True,                    # 让 PENDING/STARTED/SUCCESS 都可见
    task_acks_late=True,                        # worker 崩溃前未完成的任务会重投
    task_acks_on_failure_or_timeout=True,        # 失败/超时也尽量回收 ack 状态
    task_reject_on_worker_lost=True,             # worker 异常丢失时让任务重新入队
    worker_prefetch_multiplier=1,               # LLM 任务长，避免一个 worker 抢一堆
    worker_max_tasks_per_child=1,               # 任务跑久后重启子进程，降低连接/内存漂移
    worker_cancel_long_running_tasks_on_connection_loss=settings.CELERY_WORKER_CANCEL_LONG_RUNNING_TASKS_ON_CONNECTION_LOSS,
    worker_enable_remote_control=False,          # Upstash 长连接下禁用 pidbox，减少 pub/sub 断连
    worker_send_task_events=False,
    broker_connection_retry_on_startup=settings.CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP,
    broker_connection_retry=True,
    broker_connection_max_retries=None,
    broker_heartbeat=settings.CELERY_BROKER_HEARTBEAT,
    broker_pool_limit=settings.CELERY_BROKER_POOL_LIMIT,
    broker_transport_options={
        "visibility_timeout": 86400,
        "socket_connect_timeout": settings.CELERY_BROKER_SOCKET_CONNECT_TIMEOUT,
        "socket_timeout": _socket_timeout(settings.CELERY_BROKER_SOCKET_TIMEOUT),
        "socket_keepalive": True,
        "tcp_keepalive": True,
        "retry_on_timeout": True,
        "health_check_interval": settings.CELERY_REDIS_HEALTH_CHECK_INTERVAL,
        "max_retries": settings.CELERY_REDIS_MAX_RETRIES,
    },
    result_backend_transport_options={
        "visibility_timeout": 86400,
        "socket_connect_timeout": settings.CELERY_BROKER_SOCKET_CONNECT_TIMEOUT,
        "socket_timeout": _socket_timeout(settings.CELERY_BROKER_SOCKET_TIMEOUT),
        "socket_keepalive": True,
        "tcp_keepalive": True,
        "retry_on_timeout": True,
        "health_check_interval": settings.CELERY_REDIS_HEALTH_CHECK_INTERVAL,
        "max_retries": settings.CELERY_REDIS_MAX_RETRIES,
    },

    # --- 时间限制 (5000字章节循环最长场景) ---
    task_time_limit=60 * 60,        # 1 小时硬超时 (KILL)
    task_soft_time_limit=55 * 60,   # 55 分钟软超时 (raise SoftTimeLimitExceeded)

    # --- 结果保留 ---
    result_expires=60 * 60 * 24,    # 24h
)

# 让 `from celery_app import celery_app` 与 `celery -A celery_app worker` 都能识别
__all__ = ["celery_app"]
