from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import os
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import JSON, MetaData, create_engine, event, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, CreateTable, DropSchema

from app.models import Chapter, Task, TaskStatus
from app.services.task_write_guard import StaleTaskRun, TaskWriteGuard, lock_pending_retry
from app.tasks import tasks
from app.utils.task_status import public_task_error, public_task_step


@pytest.fixture
def database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'guard.sqlite'}")
    metadata = MetaData()
    task_table = Task.__table__.to_metadata(metadata)
    task_table.c.config.type = JSON()
    chapter_table = Chapter.__table__.to_metadata(metadata)
    with engine.begin() as connection:
        connection.execute(CreateTable(task_table, include_foreign_key_constraints=[]))
        connection.execute(CreateTable(chapter_table, include_foreign_key_constraints=[]))
    factory = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    task_id = uuid4()
    with factory() as session:
        session.add(Task(id=task_id, source_url="manual://guard-test", status=TaskStatus.PENDING,
                         progress=0, config={"category": "social_science", "raw_text": "测试文案"}))
        session.commit()
    yield factory, task_id
    engine.dispose()


def human_update(factory, task_id, patch, *, status=None, remove=()):
    with factory() as session:
        task = session.get(Task, task_id)
        config = deepcopy(task.config)
        for key in remove:
            config.pop(key, None)
        config.update(patch)
        task.config = config
        if status is not None:
            task.status = status
        session.commit()


@pytest.fixture
def worker(database, monkeypatch, tmp_path):
    factory, task_id = database
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(tasks, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(tasks, "clear_task_dispatch_guard", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks, "_start_active_run_heartbeat", lambda *args: (None, None))
    monkeypatch.setattr(tasks.settings, "COVER_BOOK_TASK_GENERATION_ENABLED", False)
    monkeypatch.setattr(tasks.settings, "LONGFORM_LENGTH_GUARD_ENABLED", False)
    monkeypatch.setattr(tasks, "enabled_for", lambda *args: False)
    monkeypatch.setattr(tasks, "finalize_task_manuscript", lambda task, text, *args, **kwargs: text)
    monkeypatch.setattr(tasks, "_resolve_video_text_for_run", lambda *args: ("测试文案", None))
    async def manuscript(*args, **kwargs):
        return "@最终书名\n测试书\n@正文\n@1. 测试章节\n测试正文。"
    monkeypatch.setattr(tasks, "generate_full_book", manuscript)
    retries = []
    wakeups = []
    monkeypatch.setattr(tasks, "_schedule_pending_task_wakeup", lambda *args, **kwargs: wakeups.append(kwargs))
    def retry(**kwargs):
        retries.append(kwargs)
        return RuntimeError("mock celery retry")
    runner = SimpleNamespace(request=SimpleNamespace(id="worker-run", retries=0), retry=retry)
    return factory, task_id, runner, retries, wakeups


def execute_worker(worker):
    return tasks.process_video.run.__func__(worker[2], str(worker[1]))


def claim(factory, task_id):
    session = factory()
    task = tasks._claim_task_run(session, str(task_id), "worker-run")
    guard = TaskWriteGuard(session, task, "worker-run")
    return session, task, guard


def test_two_sessions_merge_delta_and_preserve_manual_controls(worker):
    factory, task_id, *_ = worker
    session, task, guard = claim(factory, task_id)
    try:
        tasks._patch_config(task, stage_output="new output")
        human_update(factory, task_id, {
            "approved_source": {"sha": "human"}, "generation_budget_cny": 20,
            "generation_round": 3, "regenerated_at": "new", "operator_note": "keep",
        })
        session.commit()
        assert task.config["stage_output"] == "new output"
        assert task.config["generation_budget_cny"] == 20
        stale_config = {"active_run_id": "worker-run", "stage_output": "next output"}
        task.config = stale_config
        session.commit()
        with factory() as reader:
            saved = reader.get(Task, task_id).config
            assert saved["approved_source"] == {"sha": "human"}
            assert saved["generation_round"] == 3
            assert saved["generation_budget_cny"] == 20
            assert saved["regenerated_at"] == "new"
            assert saved["stage_output"] == "next output"
    finally:
        guard.close()
        session.close()


def test_two_sessions_nested_delta_preserves_unrelated_updates(worker):
    factory, task_id, *_ = worker
    human_update(factory, task_id, {"stage": {"done": False, "note": "old"}, "removable": True})
    session, task, guard = claim(factory, task_id)
    try:
        proposed = deepcopy(task.config)
        proposed["stage"]["done"] = True
        proposed.pop("removable")
        task.config = proposed
        human_update(factory, task_id, {"stage": {"done": False, "note": "human"}})
        session.commit()
        assert task.config["stage"] == {"done": True, "note": "human"}
        assert "removable" not in task.config
    finally:
        guard.close()
        session.close()


def test_reused_broker_run_id_cannot_bypass_replaced_lease(worker):
    factory, task_id, *_ = worker
    session, task, guard = claim(factory, task_id)
    try:
        tasks._patch_config(task, stale_output="must not persist")
        human_update(factory, task_id, {"active_run_started_at": "replacement-lease"})
        with pytest.raises(StaleTaskRun, match="lease was replaced"):
            session.commit()
        session.rollback()
        with factory() as reader:
            assert "stale_output" not in reader.get(Task, task_id).config
    finally:
        guard.close()
        session.close()


@pytest.mark.parametrize("replacement", [None, "new-run"])
@pytest.mark.parametrize("operation", ["progress", "complete", "release", "chapters"])
def test_two_sessions_stale_writes_are_fenced(worker, replacement, operation):
    factory, task_id, *_ = worker
    session, task, guard = claim(factory, task_id)
    try:
        human_update(factory, task_id, {"active_run_id": replacement, "approved_new": True}, status=TaskStatus.PENDING)
        with pytest.raises(StaleTaskRun):
            if operation == "progress":
                tasks._update_progress(session, task, progress=99, step="old progress")
            elif operation == "complete":
                task.status = TaskStatus.COMPLETED
                session.commit()
            elif operation == "release":
                tasks._clear_task_run(task, "worker-run")
                session.commit()
            else:
                tasks._persist_chapters_from_markdown(session, task, "old manuscript")
        session.rollback()
        with factory() as reader:
            saved = reader.get(Task, task_id)
            assert saved.status == TaskStatus.PENDING
            assert saved.config["active_run_id"] == replacement
            assert saved.config["approved_new"] is True
            assert saved.chapters == []
    finally:
        guard.close()
        session.close()


@pytest.mark.parametrize("error", [RuntimeError("429 rate limit"), tasks.FinalizerHold("manual hold"), tasks.ComicGenerationPaused("pause")])
@pytest.mark.parametrize("replacement", [None, "new-run"])
def test_stale_exception_never_changes_state_or_enqueues(worker, monkeypatch, error, replacement):
    factory, task_id, _, retries, wakeups = worker
    def fail(*args):
        human_update(factory, task_id, {"active_run_id": replacement, "approved_manual": True}, status=TaskStatus.PENDING)
        raise error
    monkeypatch.setattr(tasks, "_resolve_video_text_for_run", fail)
    result = execute_worker(worker)
    assert result["status"] == TaskStatus.PENDING
    with factory() as reader:
        saved = reader.get(Task, task_id)
        assert saved.config["active_run_id"] == replacement
        assert "generation_hold" not in saved.config
        assert "last_auto_retry_error" not in saved.config
        assert saved.status == TaskStatus.PENDING
    assert not retries and not wakeups


@pytest.mark.parametrize("safe_point", ["progress", "exception", "manual_review", "completion"])
def test_requested_pause_is_acknowledged_and_releases_owner(worker, monkeypatch, safe_point):
    factory, task_id, _, retries, wakeups = worker
    def resolve(*args):
        if safe_point != "completion":
            human_update(factory, task_id, {"pause_requested": True, "approved_manual": True})
        if safe_point == "exception":
            raise RuntimeError("429 rate limit")
        if safe_point == "manual_review":
            raise tasks.FinalizerHold("manual hold")
        return "测试文案", None
    monkeypatch.setattr(tasks, "_resolve_video_text_for_run", resolve)
    if safe_point == "completion":
        original = tasks._update_progress
        def progress(*args, **kwargs):
            if kwargs.get("status") == TaskStatus.COMPLETED:
                human_update(factory, task_id, {"pause_requested": True})
            return original(*args, **kwargs)
        monkeypatch.setattr(tasks, "_update_progress", progress)
    result = execute_worker(worker)
    assert result["status"] == TaskStatus.PAUSED
    with factory() as reader:
        saved = reader.get(Task, task_id)
        assert saved.status == TaskStatus.PAUSED
        assert not saved.config["pause_requested"]
        assert "active_run_id" not in saved.config
    assert not retries and not wakeups


def test_owner_change_between_error_check_and_flush_cannot_retry(worker, monkeypatch):
    factory, task_id, _, retries, wakeups = worker
    original = tasks._clear_task_run
    def clear(task, run_id):
        original(task, run_id)
        human_update(factory, task_id, {"active_run_id": "new-run"}, status=TaskStatus.PENDING)
    monkeypatch.setattr(tasks, "_clear_task_run", clear)
    monkeypatch.setattr(tasks, "_resolve_video_text_for_run", lambda *args: (_ for _ in ()).throw(RuntimeError("429 rate limit")))
    assert execute_worker(worker)["status"] == TaskStatus.PENDING
    assert not retries and not wakeups


def test_owner_change_after_retry_commit_cannot_enqueue(worker, monkeypatch):
    factory, task_id, _, retries, wakeups = worker
    original = tasks.lock_pending_retry
    def lock(session, identifier, run_id):
        human_update(factory, task_id, {"active_run_id": "new-run"}, status=TaskStatus.PROCESSING)
        return original(session, identifier, run_id)
    monkeypatch.setattr(tasks, "lock_pending_retry", lock)
    monkeypatch.setattr(tasks, "_resolve_video_text_for_run", lambda *args: (_ for _ in ()).throw(RuntimeError("429 rate limit")))
    assert execute_worker(worker)["status"] == TaskStatus.PROCESSING
    assert not retries and not wakeups


def test_normal_completion_releases_lease(worker):
    assert execute_worker(worker)["status"] == TaskStatus.COMPLETED
    with worker[0]() as reader:
        saved = reader.get(Task, worker[1])
        assert saved.progress == 100
        assert len(saved.chapters) == 1
        assert "active_run_id" not in saved.config


def test_completed_cover_registration_then_lease_release_is_allowed(worker, monkeypatch):
    monkeypatch.setattr(tasks.settings, "COVER_BOOK_TASK_GENERATION_ENABLED", True)
    def register_existing_cover(session, task, **kwargs):
        assert task.status == TaskStatus.COMPLETED
        tasks._patch_config(task, draft_cover_path="existing_cover.png", cover_status="completed")
        session.commit()
    monkeypatch.setattr(tasks, "enqueue_cover_generation", register_existing_cover)
    assert execute_worker(worker)["status"] == TaskStatus.COMPLETED
    with worker[0]() as reader:
        config = reader.get(Task, worker[1]).config
        assert config["draft_cover_path"] == "existing_cover.png"
        assert not any(key in config for key in ("active_run_id", "active_run_started_at", "active_run_heartbeat_at"))


def test_standard_category_is_checked_after_finalizer(worker, monkeypatch):
    from app.services import content_engine, general_short_length

    category = "social_science_core_conflict"
    human_update(worker[0], worker[1], {"category": category})
    checked = []
    monkeypatch.setattr(content_engine, "_length_guard_category", lambda name, enabled: name)
    monkeypatch.setattr(tasks, "finalize_task_manuscript", lambda *args, **kwargs: "finalizer output")
    def require_length(markdown, contract):
        checked.append((markdown, contract))
        raise tasks.LengthHold("标准分类终稿篇幅不足，停止交付")
    monkeypatch.setattr(general_short_length, "require_length", require_length)
    assert execute_worker(worker)["status"] == TaskStatus.MANUAL_REVIEW
    assert checked == [("finalizer output", category)]
    with worker[0]() as reader:
        assert not reader.get(Task, worker[1]).chapters


def test_hard_quota_backoff_and_unknown_paid_hold_are_preserved(worker, monkeypatch):
    def fail(*args):
        raise tasks.DeepSeekDeliveryUncertainError("paid delivery uncertain")
    monkeypatch.setattr(tasks, "_resolve_video_text_for_run", fail)
    with pytest.raises(tasks.DeepSeekDeliveryUncertainError):
        execute_worker(worker)
    with worker[0]() as reader:
        saved = reader.get(Task, worker[1])
        assert saved.config["generation_hold"]
        assert saved.config["paid_call_delivery_uncertain"]
        assert "active_run_id" not in saved.config
    assert not worker[3] and not worker[4]


def test_hard_quota_uses_backoff_without_celery_retry(worker, monkeypatch):
    def fail(*args):
        raise RuntimeError("insufficient_quota")
    monkeypatch.setattr(tasks, "_resolve_video_text_for_run", fail)
    assert execute_worker(worker)["status"] == TaskStatus.PENDING
    with worker[0]() as reader:
        saved = reader.get(Task, worker[1])
        assert saved.config["paid_api_backoff"]
        assert saved.config["auto_retry_not_before"]
        assert "auto_retry_exhausted_at" not in saved.config
    assert not worker[3] and len(worker[4]) == 1


def test_429_retry_cap_stops_recovery_and_public_text_is_truthful(worker, monkeypatch):
    factory, task_id, runner, retries, wakeups = worker
    runner.request.retries = tasks.settings.GENERATION_TASK_MAX_RETRIES
    def fail(*args):
        raise RuntimeError("429 rate limit")
    monkeypatch.setattr(tasks, "_resolve_video_text_for_run", fail)
    with pytest.raises(RuntimeError, match="429"):
        execute_worker(worker)
    with factory() as reader:
        saved = reader.get(Task, task_id)
        assert saved.status == TaskStatus.FAILED
        assert saved.config["auto_retry_exhausted_at"]
        assert "429" in saved.config["last_auto_retry_error"]
        for message in (public_task_step(saved.current_step, status=saved.status), public_task_error(saved.error_message, status=saved.status)):
            assert "已停止自动重试" in message and "人工" in message
    monkeypatch.setattr(tasks.settings, "FAILED_TASK_AUTO_RECOVERY_ENABLED", True)
    assert tasks.recover_retryable_failed_tasks() == 0
    assert not retries and not wakeups


def test_recovery_prefers_internal_error_to_display_message(worker, monkeypatch):
    factory, task_id, *_ = worker
    human_update(factory, task_id, {"last_auto_retry_error": "RuntimeError: 429 rate limit"}, status=TaskStatus.FAILED)
    with factory() as session:
        task = session.get(Task, task_id)
        task.error_message = "生成暂时中断，已保留断点"
        session.commit()
    monkeypatch.setattr(tasks.settings, "FAILED_TASK_AUTO_RECOVERY_ENABLED", True)
    monkeypatch.setattr(tasks, "_dispatch_task_ids", lambda identifiers, **kwargs: len(identifiers))
    assert tasks.recover_retryable_failed_tasks() == 1


def test_pending_retry_transition_requires_matching_run(worker):
    factory, task_id, *_ = worker
    human_update(factory, task_id, {"auto_retry_scheduled_by": "new-run"})
    with factory() as session, pytest.raises(StaleTaskRun):
        lock_pending_retry(session, task_id, "worker-run")


def test_no_row_lock_spans_generation_call(worker, monkeypatch):
    factory, task_id, *_ = worker
    async def generate(*args, **kwargs):
        human_update(factory, task_id, {"approved_during_generation": True})
        return "@正文\n@1. 标题\n测试正文"
    monkeypatch.setattr(tasks, "generate_full_book", generate)
    assert execute_worker(worker)["status"] == TaskStatus.COMPLETED


def test_guard_locks_with_for_update_before_task_update(worker):
    factory, task_id, *_ = worker
    session, task, guard = claim(factory, task_id)
    statements = []
    def statement(connection, clause, *args):
        statements.append(str(clause))
    event.listen(session.bind, "before_execute", statement)
    try:
        task.current_step = "guarded"
        session.commit()
        lock_index = next(index for index, sql in enumerate(statements) if "FOR UPDATE" in sql)
        write_index = next(index for index, sql in enumerate(statements) if sql.startswith("UPDATE tasks"))
        assert lock_index < write_index
    finally:
        event.remove(session.bind, "before_execute", statement)
        guard.close()
        session.close()


@pytest.mark.skipif(not os.environ.get("TASK_WRITE_GUARD_TEST_PG_URL"),
                    reason="opt in with TASK_WRITE_GUARD_TEST_PG_URL; isolated schema only")
@pytest.mark.parametrize("human_action", ["approve", "pause", "replace_owner"])
def test_postgres_concurrent_worker_waits_for_manual_row_lock(human_action):
    engine = create_engine(os.environ["TASK_WRITE_GUARD_TEST_PG_URL"])
    schema = f"task_guard_test_{uuid4().hex}"
    isolated = engine.execution_options(schema_translate_map={None: schema})
    factory = sessionmaker(isolated, autoflush=False, expire_on_commit=False)
    task_id = uuid4()
    snapshot_ready = threading.Event()
    human_locked = threading.Event()
    backend_pid = []
    worker_writing = threading.Event()
    human_session = None
    pool = ThreadPoolExecutor(max_workers=1)
    future = None
    created = False
    try:
        with engine.begin() as connection:
            connection.execute(CreateSchema(schema))
            connection.execute(text(f'CREATE TABLE "{schema}".tasks (LIKE public.tasks INCLUDING ALL)'))
        created = True
        with factory() as session:
            session.add(Task(id=task_id, source_url="manual://isolated-guard-test",
                             status=TaskStatus.PROCESSING, progress=1,
                             config={"active_run_id": "worker-run", "generation_budget_cny": 10}))
            session.commit()

        def old_worker():
            with factory() as session:
                task = session.get(Task, task_id)
                session.commit()
                guard = TaskWriteGuard(session, task, "worker-run")
                snapshot_ready.set()
                try:
                    assert human_locked.wait(10)
                    session.execute(text("SET LOCAL lock_timeout = '10s'"))
                    backend_pid.append(session.execute(text("SELECT pg_backend_pid()")).scalar_one())
                    worker_writing.set()
                    task.config = {**task.config, "worker_output": "saved"}
                    task.progress = 90
                    session.commit()
                    return "written"
                except StaleTaskRun:
                    session.rollback()
                    return "fenced"
                finally:
                    guard.close()

        future = pool.submit(old_worker)
        assert snapshot_ready.wait(10)
        human_session = factory()
        task = human_session.execute(
            select(Task).where(Task.id == task_id).with_for_update().execution_options(populate_existing=True)
        ).scalar_one()
        patch = {"approved_source": {"sha256": "human"}, "generation_budget_cny": 20,
                 "generation_round": 2}
        if human_action == "pause":
            patch["pause_requested"] = True
        elif human_action == "replace_owner":
            patch["active_run_id"] = "new-run"
            task.status = TaskStatus.PENDING
        task.config = {**task.config, **patch}
        human_session.flush()
        human_locked.set()
        assert worker_writing.wait(10)
        blocked = False
        deadline = time.monotonic() + 8
        with engine.connect() as observer:
            while time.monotonic() < deadline:
                blocked = observer.execute(text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                                           {"pid": backend_pid[0]}).scalar_one()
                if blocked:
                    break
                time.sleep(0.02)
        assert blocked, "worker must really block on the human-held PostgreSQL row lock"
        human_session.commit()
        assert future.result(timeout=10) == ("written" if human_action == "approve" else "fenced")
        with factory() as reader:
            saved = reader.get(Task, task_id)
            for key, value in patch.items():
                assert saved.config[key] == value
            assert saved.progress == (90 if human_action == "approve" else 1)
            assert ("worker_output" in saved.config) == (human_action == "approve")
    finally:
        human_locked.set()
        if human_session is not None:
            human_session.rollback()
            human_session.close()
        pool.shutdown(wait=True, cancel_futures=True)
        try:
            if created:
                with engine.begin() as connection:
                    connection.execute(DropSchema(schema, cascade=True))
        finally:
            engine.dispose()
