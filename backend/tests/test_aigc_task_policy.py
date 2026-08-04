from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.routers import tasks as task_router
from app.core.config import settings
from app.models import Task, TaskStatus
from app.services.aigc_quality_gate import (
    AIGC_EXCLUDED_CATEGORY_IDS,
    AIGC_POLICY_VERSION,
    aigc_category_eligible,
)


def test_new_tasks_snapshot_v2_but_comics_and_novels_are_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "AIGC_GATE_ENABLED", True)

    normal = task_router._stamp_aigc_policy({"category": "social_science"})  # noqa: SLF001

    assert normal["aigc_policy_version"] == AIGC_POLICY_VERSION
    assert normal["aigc_required"] is True
    assert normal["aigc_status"] == "pending"
    for category in AIGC_EXCLUDED_CATEGORY_IDS:
        excluded = task_router._stamp_aigc_policy({"category": category})  # noqa: SLF001
        assert excluded["aigc_policy_version"] == AIGC_POLICY_VERSION
        assert excluded["aigc_required"] is False
        assert excluded["aigc_status"] == "not_required"
        assert aigc_category_eligible(category) is False


def test_historical_task_retry_is_not_retroactively_stamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AIGC_GATE_ENABLED", True)
    monkeypatch.setattr(task_router, "_retry_resume_state", lambda _task_id: (0, "排队中（重试）"))
    historical = Task(
        id=uuid4(),
        source_url="manual://historical",
        status=TaskStatus.FAILED,
        config={"category": "social_science"},
    )

    task_router._prepare_task_for_retry(historical)  # noqa: SLF001

    assert "aigc_policy_version" not in historical.config
    assert "aigc_required" not in historical.config
    assert historical.status == TaskStatus.PENDING


def test_public_domain_retry_clears_stale_quality_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_router, "_retry_resume_state", lambda _task_id: (8, "从断点继续"))
    task = Task(
        id=uuid4(),
        source_url="manual://public-domain-retry",
        status=TaskStatus.MANUAL_REVIEW,
        config={
            "public_domain_longform_v2": True,
            "public_domain_quality_blocked": True,
            "public_domain_quality_reason": "旧目录被误识别成了正文",
        },
    )

    task_router._prepare_task_for_retry(task)  # noqa: SLF001

    assert task.config["public_domain_quality_blocked"] is False
    assert "public_domain_quality_reason" not in task.config
    assert task.status == TaskStatus.PENDING


def test_manual_review_task_cannot_be_downloaded() -> None:
    task = Task(
        id=uuid4(),
        source_url="manual://review",
        status=TaskStatus.MANUAL_REVIEW,
        config={"category": "social_science", "markdown": "unsafe candidate"},
    )

    with pytest.raises(HTTPException) as raised:
        task_router._build_single_download_response(None, task, "md")  # type: ignore[arg-type]  # noqa: SLF001

    assert raised.value.status_code == 409
    assert "尚未通过全部终稿闸门" in str(raised.value.detail)
