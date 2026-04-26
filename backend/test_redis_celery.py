"""Redis / Celery 连通性硬核测试脚本。

运行前请确保打开两个终端窗口：

窗口一：启动 Celery Worker
  cd backend
  celery -A celery_app.celery_app worker --loglevel=info --pool=solo

窗口二：运行本脚本
  cd backend
  python test_redis_celery.py

测试流程：
  Step A: 直接使用 redis 库 ping Upstash Redis
  Step B: 调用 Celery 任务 ping_task.delay("Gravity") 并等待结果
"""
from __future__ import annotations

import os
import ssl
import sys
from pathlib import Path

from redis import Redis
from redis.exceptions import RedisError

# 让脚本从 backend 目录直接执行时，也能稳定导入项目模块
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from celery_app import celery_app  # noqa: E402
from app.tasks.system_tasks import ping_task  # noqa: E402
from app.core.config import settings  # noqa: E402


def raw_redis_ping() -> None:
    redis_url = os.getenv("REDIS_URL") or settings.REDIS_URL
    print(f"[Step A] Using REDIS_URL: {redis_url}")

    try:
        client = Redis.from_url(
            redis_url,
            ssl_cert_reqs=None,
            decode_responses=True,
        )
        if client.ping():
            print("Redis Ping Success!")
        else:
            raise RuntimeError("Redis ping returned False")
    except ValueError:
        fixed_url = redis_url.strip()
        if fixed_url.startswith('REDIS_URL='):
            fixed_url = fixed_url.split('=', 1)[1].strip().strip('"')
        fixed_url = fixed_url.replace('REDIS_URL=', '').strip().strip('"')
        print(f"[Step A] Retrying with fixed URL: {fixed_url}")
        client = Redis.from_url(
            fixed_url,
            ssl_cert_reqs=None,
            decode_responses=True,
        )
        if client.ping():
            print("Redis Ping Success!")
        else:
            raise RuntimeError("Redis ping returned False")
    except (RedisError, ssl.SSLError, TimeoutError, OSError, ConnectionError) as exc:
        raise RuntimeError(f"Redis ping failed: {type(exc).__name__}: {exc}") from exc


def celery_task_test() -> None:
    print("[Step B] Dispatching Celery task: ping_task.delay('Gravity')")
    try:
        async_result = ping_task.delay("Gravity")
        print(f"Task ID: {async_result.id}")
        result = async_result.get(timeout=10)
        print(f"Task Result: {result}")
    except Exception as exc:
        raise RuntimeError(f"Celery task test failed: {type(exc).__name__}: {exc}") from exc


def main() -> None:
    try:
        raw_redis_ping()
        celery_task_test()
        print("\nAll tests passed successfully.")
    except Exception as exc:
        print(f"\nERROR: {exc}")
        print("\n请确认：")
        print("1. 已在窗口一启动 Celery Worker。")
        print("2. .env 中的 REDIS_URL / CELERY_BROKER_URL / CELERY_RESULT_BACKEND 配置正确。")
        print("3. Upstash Redis 的 TLS/SSL 连接未被本地网络拦截。")
        raise


if __name__ == "__main__":
    main()
