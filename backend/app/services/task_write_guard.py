"""Instance-local fencing for a claimed worker's ORM writes."""
from __future__ import annotations

from copy import deepcopy

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import object_session

from app.models import Task, TaskStatus


class StaleTaskRun(RuntimeError):
    pass


class TaskPauseRequested(StaleTaskRun):
    pass


_MISSING = object()
_OWNER_KEYS = {"active_run_id", "active_run_started_at", "active_run_heartbeat_at"}


def _merge_delta(previous, proposed, latest):
    merged = deepcopy(latest)
    for key in previous.keys() | proposed.keys():
        before = previous.get(key, _MISSING)
        after = proposed.get(key, _MISSING)
        current = latest.get(key, _MISSING)
        if before == after:
            continue
        if isinstance(before, dict) and isinstance(after, dict) and isinstance(current, dict):
            merged[key] = _merge_delta(before, after, current)
        elif current == before:
            if after is _MISSING:
                merged.pop(key, None)
            else:
                merged[key] = deepcopy(after)
    return merged


def check_owner(task_id, status, config, run_id, *, allow_pause=False):
    if not run_id or config.get("active_run_id") != run_id:
        raise StaleTaskRun(f"task {task_id} is no longer owned by run {run_id}")
    if not allow_pause and (status == TaskStatus.PAUSED or config.get("pause_requested")):
        raise TaskPauseRequested(f"task {task_id} pause requested")
    if not allow_pause and any(config.get(key) for key in (
        "generation_hold", "paid_call_delivery_uncertain", "paid_call_manual_resume_required",
    )):
        raise StaleTaskRun(f"task {task_id} is held")


class TaskWriteGuard:
    """Only the registered session/task is fenced; API sessions are untouched."""

    def __init__(self, session, task, run_id):
        self.session = session
        self.task = task
        self.task_id = task.id
        self.run_id = run_id
        self.lease_started_at = (task.config or {}).get("active_run_started_at")
        self.releasing = False
        self.pausing = False
        session.info["task_write_guard"] = self
        event.listen(session, "before_flush", self._before_flush)

    def lock(self):
        with self.session.no_autoflush:
            row = self.session.execute(
                select(Task.status, Task.config).where(Task.id == self.task_id).with_for_update()
            ).one_or_none()
        if row is None:
            raise StaleTaskRun(f"task {self.task_id} was deleted")
        latest = dict(row.config or {})
        check_owner(self.task_id, row.status, latest, self.run_id, allow_pause=self.pausing)
        if latest.get("active_run_started_at") != self.lease_started_at:
            raise StaleTaskRun(f"task {self.task_id} lease was replaced")
        if not self.pausing and row.status not in {TaskStatus.PROCESSING, TaskStatus.COMPLETED}:
            raise StaleTaskRun(f"task {self.task_id} status changed to {row.status}")
        return row.status, latest

    def _before_flush(self, session, flush_context, instances):
        status, latest = self.lock()
        if self.task in session.deleted:
            raise StaleTaskRun("workers cannot delete their task")
        history = inspect(self.task).attrs.config.history
        proposed = dict(self.task.config or {})
        if history.has_changes() and not history.deleted:
            raise StaleTaskRun("config replacement requires a loaded baseline")
        previous = dict(history.deleted[0] or {}) if history.deleted else proposed
        changed = {key for key in previous.keys() | proposed.keys()
                   if previous.get(key, _MISSING) != proposed.get(key, _MISSING)}
        if status == TaskStatus.COMPLETED:
            other_columns = [attribute.key for attribute in inspect(self.task).mapper.column_attrs
                             if attribute.key not in {"config", "cover_url"}
                             and inspect(self.task).attrs[attribute.key].history.has_changes()]
            if other_columns or any(not key.startswith("cover_") and key not in _OWNER_KEYS
                                    and key != "draft_cover_path" for key in changed):
                raise StaleTaskRun("completed task only permits owned cover metadata/lease release")
        merged = _merge_delta(previous, proposed, latest)
        for key in latest.keys() | proposed.keys():
            if key.startswith(("approved_", "generation_budget", "generation_round", "generation_epoch", "regenerat")) or key in {
                "pause_requested", "pause_requested_at",
            }:
                if key in latest:
                    merged[key] = deepcopy(latest[key])
                else:
                    merged.pop(key, None)
        if self.releasing:
            for key in _OWNER_KEYS:
                merged.pop(key, None)
        elif proposed.get("active_run_id") != self.run_id:
            raise StaleTaskRun("lease release requires an explicit worker transition")
        if self.pausing:
            if self.task.status != TaskStatus.PAUSED or not self.releasing:
                raise StaleTaskRun("pause acknowledgment must pause and release together")
            merged["pause_requested"] = False
        self.task.config = merged

    def close(self):
        event.remove(self.session, "before_flush", self._before_flush)
        self.session.info.pop("task_write_guard", None)


def authorize_release(task, run_id, *, pausing=False):
    if inspect(task, raiseerr=False) is None:
        return
    session = object_session(task)
    guard = session.info.get("task_write_guard") if session is not None else None
    if guard is not None:
        if guard.task is not task or guard.run_id != run_id:
            raise StaleTaskRun("invalid lease release")
        guard.releasing = True
        guard.pausing = guard.pausing or pausing


def lock_pending_retry(session, task_id, run_id):
    row = session.execute(
        select(Task.status, Task.config).where(Task.id == task_id).with_for_update()
    ).one_or_none()
    config = dict(row.config or {}) if row else {}
    if row is None or row.status != TaskStatus.PENDING or config.get("active_run_id") or config.get("auto_retry_scheduled_by") != run_id or any(
        config.get(key) for key in (
            "pause_requested", "generation_hold", "paid_call_delivery_uncertain",
            "paid_call_manual_resume_required", "auto_retry_exhausted_at",
        )
    ):
        raise StaleTaskRun(f"task {task_id} retry transition superseded")
