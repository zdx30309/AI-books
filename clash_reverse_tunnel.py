import os
import select
import socket
import threading
import time

import paramiko


HOST = os.environ["VIDEO_PLAN_SSH_HOST"]
USER = os.environ.get("VIDEO_PLAN_SSH_USER", "root")
PASSWORD = os.environ["VIDEO_PLAN_SSH_PASSWORD"]
REMOTE_BIND = os.environ.get("VIDEO_PLAN_REMOTE_BIND", "172.18.0.1")
REMOTE_PORT = int(os.environ.get("VIDEO_PLAN_REMOTE_PORT", "7897"))
LOCAL_HOST = os.environ.get("VIDEO_PLAN_LOCAL_PROXY_HOST", "127.0.0.1")
LOCAL_PORT = int(os.environ.get("VIDEO_PLAN_LOCAL_PROXY_PORT", "7897"))
LOG = os.environ.get("VIDEO_PLAN_TUNNEL_LOG", r"E:\video-plan\clash_reverse_tunnel.log")


def log(message: str) -> None:
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")


def bridge(channel) -> None:
    try:
        upstream = socket.create_connection((LOCAL_HOST, LOCAL_PORT), timeout=10)
    except Exception as exc:
        log(f"local proxy connect failed: {exc}")
        channel.close()
        return

    try:
        while True:
            readable, _, _ = select.select([upstream, channel], [], [], 60)
            if not readable:
                continue
            if upstream in readable:
                data = upstream.recv(32768)
                if not data:
                    break
                channel.sendall(data)
            if channel in readable:
                data = channel.recv(32768)
                if not data:
                    break
                upstream.sendall(data)
    except Exception as exc:
        log(f"bridge error: {exc}")
    finally:
        upstream.close()
        channel.close()


while True:
    client = None
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            HOST,
            username=USER,
            password=PASSWORD,
            timeout=20,
            banner_timeout=60,
            auth_timeout=30,
        )
        transport = client.get_transport()
        transport.request_port_forward(REMOTE_BIND, REMOTE_PORT)
        log(f"reverse tunnel up: {REMOTE_BIND}:{REMOTE_PORT} -> {LOCAL_HOST}:{LOCAL_PORT}")
        while transport.is_active():
            channel = transport.accept(30)
            if channel is not None:
                threading.Thread(target=bridge, args=(channel,), daemon=True).start()
    except Exception as exc:
        log(f"tunnel restart after error: {type(exc).__name__}: {exc}")
    finally:
        if client is not None:
            client.close()
    time.sleep(5)
