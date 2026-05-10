"""一键启动 uvicorn + Celery worker。

用法：
    cd backend
    python run.py

行为：
- 同时拉起 FastAPI (uvicorn :8000) 与 Celery worker (--pool=threads --concurrency=10)
- worker 关闭 gossip/mingle/heartbeat，减少 Upstash Redis 长连接抖动
- 任意一个进程崩溃，会把另一个一并杀掉，避免半死状态
- Ctrl+C 干净退出双进程
- 默认不启用 uvicorn --reload，避免文件保存触发重载时把正在跑的 Celery 任务一起中断。
  如需本地接口热重载，可先设置环境变量 RUN_UVICORN_RELOAD=1。

如果只想要传统两窗口模式，可以分别运行：
    uvicorn main:app --reload --port 8000
    celery -A celery_app.celery_app worker --pool=threads --concurrency=10 --loglevel=info --without-gossip --without-mingle --without-heartbeat
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _worker_concurrency() -> str:
    raw = os.getenv("CELERY_WORKER_CONCURRENCY", "10").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 10
    return str(max(1, value))


def _spawn(name: str, cmd: list[str]) -> subprocess.Popen[bytes]:
    print(f"[run] 启动 {name}: {' '.join(cmd)}", flush=True)
    creationflags = 0
    if sys.platform == "win32":
        # 让 Ctrl+C 在 Windows 下能正确传播给子进程
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(
        cmd,
        cwd=HERE,
        stdout=sys.stdout,
        stderr=sys.stderr,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        creationflags=creationflags,
    )


def _terminate(proc: subprocess.Popen[bytes], name: str) -> None:
    if proc.poll() is not None:
        return
    print(f"[run] 终止 {name} (pid={proc.pid})", flush=True)
    try:
        if sys.platform == "win32":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.terminate()
    except Exception as exc:  # noqa: BLE001
        print(f"[run] 终止 {name} 失败: {exc}", flush=True)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def main() -> int:
    worker_cmd = [
        sys.executable,
        "-m",
        "celery",
        "-A",
        "celery_app.celery_app",
        "worker",
        "--pool=threads",
        f"--concurrency={_worker_concurrency()}",
        "--loglevel=info",
        "--without-gossip",
        "--without-mingle",
        "--without-heartbeat",
    ]
    api_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ]
    if os.getenv("RUN_UVICORN_RELOAD", "").lower() in {"1", "true", "yes"}:
        api_cmd.insert(4, "--reload")

    worker = _spawn("celery worker", worker_cmd)
    # 给 worker 一秒注册 broker 连接，避免日志交错难读
    time.sleep(1.0)
    api = _spawn("uvicorn", api_cmd)

    procs = [(worker, "celery worker"), (api, "uvicorn")]

    try:
        # 任何一方退出，全部退出
        while all(p.poll() is None for p, _ in procs):
            time.sleep(0.5)
        for p, name in procs:
            if p.poll() is not None:
                print(f"[run] {name} 退出 (code={p.returncode})", flush=True)
    except KeyboardInterrupt:
        print("[run] 收到 Ctrl+C，正在退出双进程", flush=True)
    finally:
        for p, name in procs:
            _terminate(p, name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
