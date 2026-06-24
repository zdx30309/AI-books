from __future__ import annotations

from app.services.qingdou import QingdouClient, QingdouTaskResult
from app.tasks import tasks as task_tasks


def test_xiaohongshu_video_title_is_used_when_video_content_is_missing() -> None:
    text = QingdouClient._extract_xiaohongshu_note_text(  # noqa: SLF001
        {
            "platformName": "xiaohongshu",
            "videoContent": None,
            "videoTitle": "晚餐决定第二天掉称速度||晚餐决定第二天掉称速度\n#减脂 #饮食",
        }
    )

    assert text == "晚餐决定第二天掉称速度\n#减脂 #饮食"


def test_video_title_fallback_is_limited_to_xiaohongshu() -> None:
    text = QingdouClient._extract_xiaohongshu_note_text(  # noqa: SLF001
        {
            "platformName": "bilibili",
            "videoContent": None,
            "videoTitle": "只有标题，不能当成B站字幕",
        }
    )

    assert text is None


def test_poll_accepts_xiaohongshu_note_text_from_terminal_qingdou_status() -> None:
    client = object.__new__(QingdouClient)

    def fake_get_task_result(batch_id: int) -> QingdouTaskResult:
        return QingdouTaskResult(
            batch_id=batch_id,
            task_id=123,
            status_code=1001,
            status_msg="Success",
            video_time=30,
            video_content="小红书笔记正文\n#话题",
            content_source="xiaohongshu.videoTitle",
            raw={"result": {"batchStatus": -2, "list": [{"status": 1113}]}},
        )

    client.get_task_result = fake_get_task_result  # type: ignore[method-assign]

    result = client.poll_text_result(5845126, timeout_seconds=1, poll_interval_seconds=0.01)

    assert result.video_content == "小红书笔记正文\n#话题"
    assert result.content_source == "xiaohongshu.videoTitle"


def test_xiaohongshu_share_token_is_not_reported_as_model_api_key_error() -> None:
    exc = RuntimeError(
        "小红书 video parse failed after 1 candidate URL(s): "
        "https://www.xiaohongshu.com/discovery/item/abc?xsec_token=abc401def: "
        "QingdouError: 轻抖任务失败 batchStatus=-2 raw={'videoContent': None}"
    )

    message = task_tasks._user_facing_generation_error(exc)  # noqa: SLF001

    assert "模型 API Key" not in message
    assert "QingdouError" in message
    assert task_tasks._is_retryable_generation_error(exc) is True  # noqa: SLF001


def test_terminal_qingdou_empty_content_failure_is_not_retried() -> None:
    exc = RuntimeError(
        "小红书 video parse failed after 1 candidate URL(s): "
        "QingdouError: 轻抖任务失败 batchStatus=2 "
        "raw={'result': {'batchStatus': 2, 'list': [{'status': 1120, 'videoContent': None}]}}"
    )

    message = task_tasks._user_facing_generation_error(exc)  # noqa: SLF001

    assert "粘贴文稿" in message
    assert task_tasks._is_retryable_generation_error(exc) is False  # noqa: SLF001
    assert task_tasks._is_retryable_error_message(str(exc)) is False  # noqa: SLF001


def test_terminal_qingdou_empty_content_with_other_status_is_not_retried() -> None:
    exc = RuntimeError(
        "抖音 video parse failed after 1 candidate URL(s): "
        "QingdouError: 轻抖任务失败 batchStatus=2 "
        "raw={'result': {'batchStatus': 2, 'list': [{'status': 1010, 'videoContent': None}]}}"
    )

    assert task_tasks._is_retryable_generation_error(exc) is False  # noqa: SLF001
    assert task_tasks._is_retryable_error_message(str(exc)) is False  # noqa: SLF001


def test_user_facing_openai_rate_limit_message_remains_recoverable() -> None:
    message = "OpenAI 请求被限流，请稍后重试，或降低并发/更换可用模型配置。"

    assert task_tasks._is_retryable_error_message(message) is True  # noqa: SLF001


def test_exhausted_retry_step_is_not_waiting_auto_recovery_label() -> None:
    step = task_tasks._retry_exhausted_step("OpenAI 请求被限流，请稍后重试。")  # noqa: SLF001

    assert step.startswith("自动重试已用尽")
    assert "等待自动恢复" not in step
