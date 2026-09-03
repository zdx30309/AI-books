from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app.services import content_engine
from app.services import family_long_novel
from app.tasks import tasks


def _disable_paid_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(content_engine.paid_api_guard, "guard", lambda _provider: None)
    monkeypatch.setattr(content_engine.paid_api_guard, "trip_if_quota", lambda *_args: False)
    monkeypatch.setattr(content_engine, "distributed_semaphore", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(content_engine.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(content_engine, "_reset_openai_writer_client", lambda: None)


def test_stream_delivery_uncertain_never_submits_non_stream_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def create(**_kwargs: object):
        nonlocal calls
        calls += 1

        def events():
            yield SimpleNamespace(type="response.output_text.delta", delta="已经收到一部分")
            raise RuntimeError("peer closed connection")

        return events()

    _disable_paid_gate(monkeypatch)
    monkeypatch.setattr(content_engine.settings, "OPENAI_WRITER_STREAM", True)
    monkeypatch.setattr(content_engine.settings, "OPENAI_WRITER_MAX_RETRIES", 3)
    monkeypatch.setattr(
        content_engine,
        "_get_openai_writer_client",
        lambda: SimpleNamespace(responses=SimpleNamespace(create=create)),
    )

    with pytest.raises(content_engine.OpenAIDeliveryUncertainError):
        content_engine._openai_writer_call_result("prompt", retry_on_failure=True)

    assert calls == 1


def test_empty_response_does_not_retry_with_another_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def create(**_kwargs: object):
        nonlocal calls
        calls += 1
        return SimpleNamespace(id="resp_empty", status="completed", output_text="")

    _disable_paid_gate(monkeypatch)
    monkeypatch.setattr(content_engine.settings, "OPENAI_WRITER_STREAM", False)
    monkeypatch.setattr(content_engine.settings, "OPENAI_WRITER_MAX_RETRIES", 3)
    monkeypatch.setattr(
        content_engine,
        "_get_openai_writer_client",
        lambda: SimpleNamespace(responses=SimpleNamespace(create=create)),
    )

    with pytest.raises(content_engine.OpenAIDeliveryUncertainError):
        content_engine._openai_writer_call_result("prompt", retry_on_failure=True)

    assert calls == 1


def test_persisted_background_id_is_retrieved_even_when_mode_flag_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retrieved: list[str] = []

    def retrieve(response_id: str):
        retrieved.append(response_id)
        return SimpleNamespace(id=response_id, status="completed", output_text="已恢复")

    def create(**_kwargs: object):  # pragma: no cover - must never be called
        raise AssertionError("a persisted response must not create a new paid request")

    _disable_paid_gate(monkeypatch)
    monkeypatch.setattr(
        content_engine,
        "_get_openai_writer_client",
        lambda: SimpleNamespace(responses=SimpleNamespace(create=create, retrieve=retrieve)),
    )

    response = content_engine._create_openai_writer_response(
        "prompt",
        effort="high",
        verb="medium",
        max_tok=100,
        background=False,
        background_response_id="resp_already_billed",
        background_timeout_seconds=60,
        background_poll_interval_seconds=1,
    )

    assert response.output_text == "已恢复"
    assert retrieved == ["resp_already_billed"]


def test_delivery_uncertain_task_is_held_and_never_auto_retried() -> None:
    error = content_engine.OpenAIDeliveryUncertainError("connection dropped")
    task = SimpleNamespace(config={})

    assert not tasks._is_retryable_generation_error(error)
    tasks._mark_generation_hold_for_quota(task, error)

    assert task.config["generation_hold"] is True
    assert task.config["paid_call_delivery_uncertain"] is True


def test_generation_hold_stays_pending_when_stale_queue_message_arrives(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.tasks import tasks as task_module

    task = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        status=task_module.TaskStatus.PENDING,
        config={"generation_hold": True, "generation_hold_reason": "operator hold"},
        error_message=None,
        current_step=None,
    )

    class FakeDb:
        def execute(self, *_args: object, **_kwargs: object):
            class Result:
                def scalar_one_or_none(self):
                    return task

            return Result()

        def commit(self) -> None:
            return None

        def refresh(self, _obj: object) -> None:
            return None

    with pytest.raises(task_module.StaleTaskRun):
        task_module._claim_task_run(FakeDb(), str(task.id), "run-1")

    assert task.status == task_module.TaskStatus.PENDING
    assert task.config["generation_hold"] is True


def test_family_batch_pauses_after_first_failed_chapter(monkeypatch: pytest.MonkeyPatch) -> None:
    project_id = "00000000-0000-0000-0000-000000000001"
    project = SimpleNamespace(id=project_id, status=family_long_novel.LongNovelProjectStatus.ACTIVE)
    calls: list[int] = []

    class Query:
        def __init__(self, *, count_value: int = 1, first_value: object = None) -> None:
            self.count_value = count_value
            self.first_value = first_value

        def filter(self, *_args: object, **_kwargs: object) -> "Query":
            return self

        def count(self) -> int:
            return self.count_value

        def first(self) -> object:
            return self.first_value

    failed_chapter = SimpleNamespace(status=family_long_novel.LongNovelChapterStatus.PLANNED)

    class FakeDb:
        def query(self, model: object) -> Query:
            if model is family_long_novel.LongNovelChapterPlan:
                return Query(count_value=1)
            return Query(first_value=failed_chapter)

        def refresh(self, _obj: object) -> None:
            return None

        def add(self, _obj: object) -> None:
            return None

        def commit(self) -> None:
            return None

    monkeypatch.setattr(family_long_novel, "_get_project", lambda *_args, **_kwargs: project)
    monkeypatch.setattr(family_long_novel, "_ensure_bible", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(family_long_novel, "sync_family_project_progress", lambda *_args, **_kwargs: None)

    def fail_first(_db: object, _project_id: object, chapter_number: int, _user: object = None) -> None:
        calls.append(chapter_number)
        raise RuntimeError("paid_call_delivery_uncertain: upstream disconnected")

    monkeypatch.setattr(family_long_novel, "generate_family_chapter", fail_first)

    result = family_long_novel.generate_family_batch(
        FakeDb(),
        project_id,
        family_long_novel.FamilyLongNovelBatchRequest(from_chapter=1, to_chapter=3),
    )

    assert calls == [1]
    assert project.status == family_long_novel.LongNovelProjectStatus.PAUSED
    assert result["failed"][0]["chapter_number"] == 1


def test_sync_chat_entry_has_one_attempt_on_transport_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    # Import lazily: the legacy sync service imports the full prompt registry.
    from app.services import services

    calls = 0

    class Completions:
        def create(self, **_kwargs: object):
            nonlocal calls
            calls += 1
            raise RuntimeError("connection reset by peer")

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    monkeypatch.setattr(services, "_text_client", fake_client)
    monkeypatch.setattr(services.paid_api_guard, "guard", lambda _provider: None)
    monkeypatch.setattr(services.paid_api_guard, "trip_if_quota", lambda *_args: False)

    with pytest.raises(content_engine.OpenAIDeliveryUncertainError):
        services._chat_with_client(fake_client, "model", "system", "user", temperature=0.0)

    assert calls == 1


def test_sync_image_entry_has_one_attempt_on_transport_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import services

    calls = 0

    class Images:
        def generate(self, **_kwargs: object):
            nonlocal calls
            calls += 1
            raise RuntimeError("read timeout")

    monkeypatch.setattr(services, "_image_client", SimpleNamespace(images=Images()))
    monkeypatch.setattr(services.paid_api_guard, "guard", lambda _provider: None)
    monkeypatch.setattr(services.paid_api_guard, "trip_if_quota", lambda *_args: False)

    with pytest.raises(content_engine.OpenAIDeliveryUncertainError):
        services.generate_cover_image("cover prompt")

    assert calls == 1
