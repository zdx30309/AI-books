from __future__ import annotations

from uuid import uuid4

from app.models import EpicProductionJob
from app.services.epic_novel import (
    apply_epic_job_step,
    epic_job_snapshot,
    pause_epic_production_job,
    resume_epic_production_job,
    retry_epic_job_failed_only,
)


def _job(total: int = 3, max_cost: float | None = None) -> EpicProductionJob:
    return EpicProductionJob(
        id=uuid4(),
        project_id=uuid4(),
        job_type="chapter_generation",
        status="queued",
        progress_current=0,
        progress_total=total,
        max_estimated_cost=max_cost,
        estimated_cost=0.0,
        checkpoint_json={"completedSteps": [], "failedSteps": [], "events": []},
        continuation_chunks=[],
    )


def test_job_resume_after_failure_and_retry_failed_only() -> None:
    job = _job(total=2)

    assert apply_epic_job_step(job, step_key="chapter_1", estimated_cost=0.01)["advanced"] is True
    failed = apply_epic_job_step(job, step_key="chapter_2", estimated_cost=0.01, failed=True, error="temporary provider error")

    assert failed["reason"] == "failed"
    assert job.status == "failed"
    assert job.checkpoint_json["failedSteps"] == ["chapter_2"]

    resume_epic_production_job(job)
    retry = retry_epic_job_failed_only(job)
    assert retry["retryPendingSteps"] == ["chapter_2"]
    assert job.retry_failed_only is True

    assert apply_epic_job_step(job, step_key="chapter_2", estimated_cost=0.01)["advanced"] is True
    assert job.status == "completed"
    assert job.checkpoint_json["failedSteps"] == []


def test_paused_job_does_not_continue_until_resumed() -> None:
    job = _job(total=2)
    pause_epic_production_job(job)

    result = apply_epic_job_step(job, step_key="bible_section", estimated_cost=0.01)

    assert result["reason"] == "paused"
    assert job.progress_current == 0

    resume_epic_production_job(job)
    result = apply_epic_job_step(job, step_key="bible_section", estimated_cost=0.01)

    assert result["advanced"] is True
    assert job.progress_current == 1


def test_cost_cap_stops_safely_before_advancing() -> None:
    job = _job(total=2, max_cost=0.01)

    result = apply_epic_job_step(job, step_key="reference_batch_1", estimated_cost=0.02)

    assert result["reason"] == "cost_cap"
    assert job.status == "cost_capped"
    assert job.progress_current == 0
    assert job.checkpoint_json["blockedStep"] == "reference_batch_1"


def test_continuation_chunks_are_recorded_without_returning_content() -> None:
    job = _job(total=1)

    apply_epic_job_step(job, step_key="chapter_1", estimated_cost=0.01, continuation_chunk="长输出分片一")

    assert len(job.continuation_chunks) == 1
    assert job.continuation_chunks[0]["contentReturned"] is False
    assert job.continuation_chunks[0]["chars"] > 0


def test_job_snapshot_exposes_progress_cost_error_and_retry_status() -> None:
    job = _job(total=3)
    apply_epic_job_step(job, step_key="qa_1", estimated_cost=0.03)
    apply_epic_job_step(job, step_key="qa_2", estimated_cost=0.02, failed=True, error="qa blocked")
    retry_epic_job_failed_only(job)

    snapshot = epic_job_snapshot(job)

    assert snapshot is not None
    assert snapshot["progressRatio"] == 0.3333
    assert snapshot["estimatedCost"] == 0.05
    assert snapshot["estimatedRemainingCost"] == 0.1
    assert snapshot["failedSteps"] == ["qa_2"]
    assert snapshot["retryFailedOnly"] is True
