from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
import socket
import sys
import threading
from types import SimpleNamespace
import uuid
from zipfile import ZipFile

import pytest
from fastapi import HTTPException
from sqlalchemy import JSON, MetaData, create_engine
from sqlalchemy.orm import sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.api.routers import tasks as task_router
from app.models import Chapter, Task, TaskStatus, User
from app.services import content_engine
from app.services.batch_spend import BatchBudgetStop
from app.tasks import tasks as generation_tasks
from app.utils import distributed_limiter


@pytest.fixture(autouse=True)
def _disable_final_length_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    # 这些用例用极短的假书稿验证调度可靠性；篇幅门禁另有专门测试。
    from app.core.config import settings

    monkeypatch.setattr(settings, "FINAL_LENGTH_GATE_ENABLED", False)


@pytest.fixture
def isolated_generation(tmp_path, monkeypatch):
    def block_network(*args, **kwargs):
        raise AssertionError("Offline generation tests must not access external services")

    monkeypatch.setattr(socket.socket, "connect", block_network)
    database_engine = create_engine(
        "sqlite:///" + str(tmp_path / "generation.sqlite"),
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    metadata = MetaData()
    for model in (User, Task, Chapter):
        table = model.__table__.to_metadata(metadata)
        if model is Task:
            table.c.config.type = JSON()
    metadata.create_all(database_engine)
    sessions = sessionmaker(bind=database_engine, expire_on_commit=False, autoflush=False)
    calls = Counter()
    calls_lock = threading.Lock()
    failure_markers = set()
    markers = ("隔离标记甲", "隔离标记乙", "隔离标记丙", "隔离标记丁")

    def record(stage, text):
        marker = next(marker for marker in markers if marker in text)
        with calls_lock:
            calls[stage, marker] += 1
        return marker

    async def writer(prompt, **kwargs):
        marker = record("writer", prompt)
        return (
            "@最终书名\n独立测试书\n@最终简介\n本书用于离线检查任务隔离。\n"
            "@目录\n1. 开始\n2. 结束\n@导言\n本书介绍两个阶段。\n@正文\n"
            "@1. 开始\n" + marker + "，这是第一章独立保存的正文。\n"
            "@2. 结束\n" + marker + "，这是第二章独立保存的正文。\n"
        )

    def polish(text, **kwargs):
        marker = record("polish", text)
        if marker in failure_markers:
            raise ConnectionError("429 offline injected rate limit")
        return text

    def refine(text, **kwargs):
        record("refine", text)
        return text

    def format_text(text, **kwargs):
        record("format", text)
        return text

    def heartbeat(*args):
        stop = threading.Event()
        thread = threading.Thread(target=lambda: None)
        thread.start()
        return stop, thread

    monkeypatch.setattr(generation_tasks, "SessionLocal", sessions)
    monkeypatch.setattr(generation_tasks, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(task_router, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(generation_tasks, "_start_active_run_heartbeat", heartbeat)
    monkeypatch.setattr(generation_tasks, "clear_task_dispatch_guard", lambda *args: None)
    monkeypatch.setattr(generation_tasks, "acquire_task_dispatch_guard", lambda *args, **kwargs: True)
    monkeypatch.setattr(generation_tasks.settings, "COVER_BOOK_TASK_GENERATION_ENABLED", False)
    monkeypatch.setattr(content_engine, "_route_writer_call", writer)
    monkeypatch.setattr(content_engine, "polish_book_text", polish)
    monkeypatch.setattr(content_engine, "refine_book_text", refine)
    monkeypatch.setattr(content_engine, "format_book_text", format_text)

    def create_task(marker, *, status=TaskStatus.PENDING, **config):
        with sessions() as database:
            task = Task(
                id=uuid.uuid4(), source_url="https://offline.invalid/video", status=status,
                config={"raw_text": marker + "的测试来源正文。", "category": "social_science", **config},
            )
            database.add(task)
            database.commit()
            return task.id

    yield sessions, create_task, calls, failure_markers, markers, tmp_path
    database_engine.dispose()


def test_four_books_keep_stages_and_persisted_chapters_isolated(isolated_generation):
    sessions, create_task, calls, _, markers, root = isolated_generation
    task_ids = [create_task(marker) for marker in markers]
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda task_id: generation_tasks.process_video.run(str(task_id)), task_ids))
    assert all(result["status"] == TaskStatus.COMPLETED and result["chapters"] == 2 for result in results)
    with sessions() as database:
        for task_id, marker in zip(task_ids, markers):
            task = database.get(Task, task_id)
            assert task.progress == 100
            assert "active_run_id" not in task.config
            assert len(task.chapters) == 2
            assert all(marker in chapter.content for chapter in task.chapters)
            for filename in ("01_writer.md", "02_polish.md", "03_refine.md", "04_format.md"):
                text = (root / "backend/output" / ("task_" + str(task_id)) / filename).read_text()
                assert marker in text
                assert all(other not in text for other in markers if other != marker)
            assert all(calls[stage, marker] == 1 for stage in ("writer", "polish", "refine", "format"))
            markdown_response = task_router._build_single_download_response(database, task, "md")
            assert marker in Path(markdown_response.path).read_text()
            docx_response = task_router._build_single_download_response(database, task, "docx")
            with ZipFile(docx_response.path) as archive:
                assert archive.testzip() is None
                document = archive.read("word/document.xml").decode("utf-8")
                assert marker in document
                assert all(other not in document for other in markers if other != marker)


def test_classic_task_persists_historical_delivery_and_keeps_new_gate_out(isolated_generation, monkeypatch):
    sessions, create_task, calls, _, markers, root = isolated_generation
    from app.prompts.classic_text import VERSION
    marker = markers[0]
    task_id = create_task(marker, category="life_philosophy", text_workflow_version=VERSION)
    monkeypatch.setattr(generation_tasks.settings, "FINAL_LENGTH_GATE_ENABLED", True)
    result = generation_tasks.process_video.run(str(task_id))
    assert result["status"] == TaskStatus.COMPLETED
    with sessions() as database:
        task = database.get(Task, task_id)
        assert task.config["text_workflow_version"] == VERSION
        assert "！！！" not in task.config["markdown"]
        assert len(task.chapters) == 2
        assert marker in task.config["markdown"]
        assert "！！！" not in task_router._read_task_markdown(database, task)
        response = task_router._build_single_download_response(database, task, "md")
        assert "！！！" not in Path(response.path).read_text()
        response = task_router._build_single_download_response(database, task, "docx")
        with ZipFile(response.path) as archive:
            assert marker in archive.read("word/document.xml").decode("utf-8")
    assert all(calls[stage, marker] == 1 for stage in ("writer", "polish", "refine", "format"))


def test_unknown_regeneration_subject_preserves_existing_files(isolated_generation):
    sessions, create_task, _, _, markers, root = isolated_generation
    task_id = create_task(markers[0], category="unknown_historical_subject")
    output = root / "backend/output" / ("task_" + str(task_id))
    output.mkdir(parents=True)
    (output / "04_format.md").write_text("已付费书稿")
    with sessions() as database:
        task = database.get(Task, task_id)
        with pytest.raises(ValueError, match="No historical"):
            task_router._prepare_task_for_regeneration(database, task)
    assert (output / "04_format.md").read_text() == "已付费书稿"


def test_transient_failure_resumes_without_regenerating_saved_draft(isolated_generation):
    sessions, create_task, calls, failures, markers, root = isolated_generation
    marker = markers[0]
    task_id = create_task(marker)
    failures.add(marker)
    with pytest.raises(RuntimeError, match="retryable"):
        generation_tasks.process_video.run(str(task_id))
    with sessions() as database:
        task = database.get(Task, task_id)
        assert task.status == TaskStatus.PENDING
        assert len(task.chapters) == 0
        assert "active_run_id" not in task.config
    assert (root / "backend/output" / ("task_" + str(task_id)) / "01_writer.md").exists()
    failures.clear()
    result = generation_tasks.process_video.run(str(task_id))
    assert result["status"] == TaskStatus.COMPLETED
    assert calls["writer", marker] == 1
    assert calls["polish", marker] == 2
    with sessions() as database:
        assert len(database.get(Task, task_id).chapters) == 2


def test_completed_redelivery_does_not_call_models_or_duplicate_chapters(isolated_generation):
    sessions, create_task, calls, _, markers, _ = isolated_generation
    task_id = create_task(markers[0])
    generation_tasks.process_video.run(str(task_id))
    previous_calls = calls.copy()
    with sessions() as database:
        chapter_ids = [chapter.id for chapter in database.get(Task, task_id).chapters]
    result = generation_tasks.process_video.run(str(task_id))
    assert result["status"] == TaskStatus.COMPLETED
    assert calls == previous_calls
    with sessions() as database:
        assert [chapter.id for chapter in database.get(Task, task_id).chapters] == chapter_ids


def test_one_book_failure_does_not_cancel_other_books(isolated_generation):
    sessions, create_task, calls, failures, markers, _ = isolated_generation
    task_ids = [create_task(marker) for marker in markers]
    failures.add(markers[0])
    with ThreadPoolExecutor(max_workers=4) as workers:
        futures = [workers.submit(generation_tasks.process_video.run, str(task_id)) for task_id in task_ids]
        with pytest.raises(RuntimeError, match="retryable"):
            futures[0].result()
        assert all(future.result()["status"] == TaskStatus.COMPLETED for future in futures[1:])
    with sessions() as database:
        assert database.get(Task, task_ids[0]).status == TaskStatus.PENDING
        assert all(database.get(Task, task_id).status == TaskStatus.COMPLETED for task_id in task_ids[1:])
    failures.clear()
    assert generation_tasks.process_video.run(str(task_ids[0]))["status"] == TaskStatus.COMPLETED
    assert all(calls["writer", marker] == 1 for marker in markers)


@pytest.mark.parametrize("case", ("paused", "owned", "held", "manual_review"))
def test_non_runnable_tasks_do_not_call_models(isolated_generation, case):
    sessions, create_task, calls, _, markers, _ = isolated_generation
    configs = {
        "paused": {"status": TaskStatus.PAUSED},
        "owned": {"status": TaskStatus.PROCESSING, "active_run_id": "different-run"},
        "held": {"generation_hold": True},
        "manual_review": {"status": "manual_review"},
    }
    task_id = create_task(markers[0], **configs[case])
    result = generation_tasks.process_video.run(str(task_id))
    assert result["status"] != TaskStatus.COMPLETED
    assert not calls
    with sessions() as database:
        assert not database.get(Task, task_id).chapters


def test_exhausted_retry_remains_failed_instead_of_false_completion(isolated_generation, monkeypatch):
    sessions, create_task, _, failures, markers, _ = isolated_generation
    monkeypatch.setattr(generation_tasks.settings, "GENERATION_TASK_MAX_RETRIES", 0)
    failures.add(markers[0])
    task_id = create_task(markers[0])
    with pytest.raises(RuntimeError, match="retryable"):
        generation_tasks.process_video.run(str(task_id))
    with sessions() as database:
        task = database.get(Task, task_id)
        assert task.status == TaskStatus.FAILED
        assert task.progress < 100
        assert task.config.get("auto_retry_exhausted_at")
        assert not task.chapters


def test_paid_api_gate_fails_closed_without_contacting_provider(monkeypatch):
    class UnavailableRedis:
        def eval(self, *args):
            raise ConnectionError("offline Redis unavailable")

    monkeypatch.setattr(distributed_limiter, "_redis_client", lambda: UnavailableRedis())
    contacted_provider = False
    with pytest.raises(distributed_limiter.DistributedLimitError, match="unavailable"):
        with distributed_limiter.distributed_semaphore("offline-test", limit=1):
            contacted_provider = True
    assert not contacted_provider


@pytest.mark.parametrize("category", ("general_short", "standard_long", "future_category"))
def test_historical_categories_can_be_read_and_exported_without_rewriting_config(isolated_generation, category):
    sessions, create_task, _, _, markers, _ = isolated_generation
    marker = markers[0]
    markdown = "@最终书名\n历史书籍\n@正文\n@1. 原文\n" + marker + "，保留原有书稿。\n"
    task_id = create_task(marker, status=TaskStatus.COMPLETED, category=category, markdown=markdown)
    owner = SimpleNamespace(id=None)
    with sessions() as database:
        task = database.get(Task, task_id)
        result = task_router.get_markdown(task_id, db=database, user=owner)
        assert marker in result.markdown
        for fmt in ("md", "docx"):
            response = task_router._build_single_download_response(database, task, fmt)
            assert Path(response.path).stat().st_size > 0
            batch = task_router._build_batch_download_response([task_id], fmt=fmt, db=database, user=owner)
            with ZipFile(BytesIO(batch.body)) as archive:
                assert archive.testzip() is None
                assert len(archive.namelist()) == 1
                content = archive.read(archive.namelist()[0])
                if fmt == "docx":
                    with ZipFile(BytesIO(content)) as document:
                        assert marker in document.read("word/document.xml").decode("utf-8")
                else:
                    assert marker in content.decode("utf-8")
        assert task.config["category"] == category
        assert task.config["markdown"] == markdown


@pytest.mark.parametrize("category", ("general_short", "standard_long", "future_category"))
def test_unknown_categories_still_rejected_for_new_generation(category):
    with pytest.raises(HTTPException) as result:
        task_router._normalize_task_config({"category": category})
    assert result.value.status_code == 400


def test_budget_stop_pauses_task_without_retrying_or_completing(isolated_generation, monkeypatch):
    sessions, create_task, _, _, markers, _ = isolated_generation

    def stop_format(*args, **kwargs):
        raise BatchBudgetStop("100元批次预算保护测试")

    monkeypatch.setattr(content_engine, "format_book_text", stop_format)
    task_id = create_task(markers[0])
    result = generation_tasks.process_video.run(str(task_id))
    assert result["status"] == TaskStatus.PAUSED
    with sessions() as database:
        task = database.get(Task, task_id)
        assert task.status == TaskStatus.PAUSED
        assert task.config["generation_hold"]
        assert "active_run_id" not in task.config
        assert not task.chapters


def test_stale_budget_stop_preserves_newer_run(isolated_generation, monkeypatch):
    sessions, create_task, _, _, markers, _ = isolated_generation
    task_id = create_task(markers[0])

    def replace_run_then_stop(*args, **kwargs):
        with sessions() as database:
            task = database.get(Task, task_id)
            task.config = {**task.config, "active_run_id": "newer-run", "approved": True}
            database.commit()
        raise BatchBudgetStop("旧运行费用保护测试")

    monkeypatch.setattr(content_engine, "format_book_text", replace_run_then_stop)
    generation_tasks.process_video.run(str(task_id))
    with sessions() as database:
        task = database.get(Task, task_id)
        assert task.config.get("active_run_id") == "newer-run", task.config
        assert task.config["approved"]
        assert not task.config.get("generation_hold")
        assert task.status != TaskStatus.PAUSED
