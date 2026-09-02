from __future__ import annotations

import pytest

from app.services import qingdou
from app.tasks import tasks as task_tasks


def test_qingdou_lock_fails_closed_when_redis_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenRedis:
        def set(self, *args, **kwargs):
            raise RuntimeError("redis temporarily rate limited")

    monkeypatch.setattr(qingdou.settings, "QINGDOU_SERIAL_LOCK_ENABLED", True)
    monkeypatch.setattr(qingdou, "_redis_client", lambda: BrokenRedis())

    with pytest.raises(qingdou.QingdouTimeoutError, match="串行锁不可用"):
        with qingdou._qingdou_serial_lock(ttl_seconds=900):  # noqa: SLF001
            raise AssertionError("the upstream call must never run without the lock")


def test_busy_qingdou_result_does_not_try_the_next_url(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def busy_once(source_url: str, **kwargs):
        calls.append(source_url)
        raise qingdou.QingdouBusyError("commitGetTextTask 返回繁忙")

    monkeypatch.setattr(task_tasks, "_extract_video_text_with_qingdou_once", busy_once)

    with pytest.raises(qingdou.QingdouBusyError):
        task_tasks.extract_video_text_with_qingdou(
            "https://www.bilibili.com/video/BV1xx411c7mD",
            timeout_seconds=1,
            poll_interval_seconds=0,
        )

    assert len(calls) == 1


def test_qingdou_busy_and_lock_errors_are_retryable() -> None:
    assert task_tasks._is_retryable_generation_error(  # noqa: SLF001
        qingdou.QingdouBusyError("commitGetTextTask 返回繁忙")
    )
    assert task_tasks._is_retryable_generation_error(  # noqa: SLF001
        qingdou.QingdouTimeoutError("轻抖串行锁不可用，任务将自动重试")
    )
