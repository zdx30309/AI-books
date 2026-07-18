from types import SimpleNamespace
from io import BytesIO
import uuid
import zipfile

import pytest

from app.models import Task, TaskStatus
from app.services import testing_endpoint


class _FakeUsage:
    def model_dump(self):
        return {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


class _FakeResponses:
    def __init__(self, calls):
        self.calls = calls

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text="internal result", id="resp_test", usage=_FakeUsage())


class _SequentialFakeResponses:
    def __init__(self, calls, responses):
        self.calls = calls
        self.responses = list(responses)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class _FakeDb:
    def add(self, value):
        self.added = value

    def commit(self):
        return None

    def refresh(self, value):
        return None


def test_openai_testing_call_uses_configured_openai_model(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(testing_endpoint.settings, "TESTING_ENDPOINT_OPENAI_MODEL", "gpt-test-endpoint")
    monkeypatch.setattr(testing_endpoint.settings, "TESTING_ENDPOINT_OPENAI_REASONING_EFFORT", "low")
    monkeypatch.setattr(testing_endpoint.settings, "TESTING_ENDPOINT_OPENAI_VERBOSITY", "medium")
    monkeypatch.setattr(
        testing_endpoint,
        "_testing_openai_client",
        lambda: SimpleNamespace(responses=_FakeResponses(calls)),
    )

    text, usage = testing_endpoint._call_openai_testing("prompt", action="unit_test", max_output_tokens=123)

    assert text == "internal result"
    assert usage["provider"] == "openai"
    assert usage["model"] == "gpt-test-endpoint"
    assert calls == [
        {
            "model": "gpt-test-endpoint",
            "input": "prompt",
            "reasoning": {"effort": "low"},
            "text": {"verbosity": "medium"},
            "max_output_tokens": 123,
        }
    ]


def test_reference_analysis_reserves_visible_output_and_retries_empty_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    empty_usage = SimpleNamespace(
        input_tokens=100,
        output_tokens=6000,
        total_tokens=6100,
        input_tokens_details=SimpleNamespace(cached_tokens=0),
        output_tokens_details=SimpleNamespace(reasoning_tokens=6000),
    )
    success_usage = SimpleNamespace(
        input_tokens=100,
        output_tokens=120,
        total_tokens=220,
        input_tokens_details=SimpleNamespace(cached_tokens=100),
        output_tokens_details=SimpleNamespace(reasoning_tokens=20),
    )
    responses = [
        SimpleNamespace(
            output_text="",
            id="resp_empty",
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            usage=empty_usage,
            output=[],
        ),
        SimpleNamespace(
            output_text="style note",
            id="resp_success",
            status="completed",
            incomplete_details=None,
            usage=success_usage,
        ),
    ]
    monkeypatch.setattr(testing_endpoint.settings, "TESTING_ENDPOINT_OPENAI_MODEL", "gpt-test-endpoint")
    monkeypatch.setattr(testing_endpoint.settings, "TESTING_ENDPOINT_OPENAI_REASONING_EFFORT", "xhigh")
    monkeypatch.setattr(testing_endpoint.settings, "TESTING_ENDPOINT_OPENAI_VERBOSITY", "medium")
    monkeypatch.setattr(
        testing_endpoint,
        "_testing_openai_client",
        lambda: SimpleNamespace(responses=_SequentialFakeResponses(calls, responses)),
    )

    text, usage = testing_endpoint._call_openai_testing(
        "prompt",
        action="testing_style_analysis",
        max_output_tokens=2500,
    )

    assert text == "style note"
    assert calls[0]["reasoning"] == {"effort": "medium"}
    assert calls[0]["max_output_tokens"] == 6000
    assert calls[1]["reasoning"] == {"effort": "low"}
    assert calls[1]["max_output_tokens"] == 12000
    assert usage["attempt_count"] == 2
    assert usage["attempts"][0]["incomplete_reason"] == "max_output_tokens"
    assert usage["usage"]["input_tokens"] == 200
    assert usage["usage"]["reasoning_tokens"] == 6020


def test_empty_openai_response_reports_actual_incomplete_reason_after_one_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    responses = [
        SimpleNamespace(
            output_text="",
            id=f"resp_empty_{index}",
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            usage=SimpleNamespace(
                input_tokens=10,
                output_tokens=6000,
                total_tokens=6010,
                input_tokens_details=SimpleNamespace(cached_tokens=0),
                output_tokens_details=SimpleNamespace(reasoning_tokens=6000),
            ),
            output=[],
        )
        for index in (1, 2)
    ]
    monkeypatch.setattr(testing_endpoint.settings, "TESTING_ENDPOINT_OPENAI_MODEL", "gpt-test-endpoint")
    monkeypatch.setattr(testing_endpoint.settings, "TESTING_ENDPOINT_OPENAI_REASONING_EFFORT", "xhigh")
    monkeypatch.setattr(testing_endpoint.settings, "TESTING_ENDPOINT_OPENAI_VERBOSITY", "medium")
    monkeypatch.setattr(
        testing_endpoint,
        "_testing_openai_client",
        lambda: SimpleNamespace(responses=_SequentialFakeResponses(calls, responses)),
    )

    with pytest.raises(testing_endpoint.TestingEndpointError) as exc_info:
        testing_endpoint._call_openai_testing(
            "prompt",
            action="testing_style_analysis",
            max_output_tokens=2500,
        )

    assert exc_info.value.status_code == 502
    assert "max_output_tokens" in str(exc_info.value)
    assert "自动降低推理强度并重试一次" in str(exc_info.value)
    assert len(calls) == 2


def test_reference_book_chunk_size_has_a_safe_upper_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(testing_endpoint.settings, "TESTING_ENDPOINT_BOOK_CHUNK_CHARS", 80_000)

    assert testing_endpoint._book_chunk_chars() == 50_000


def test_testing_payload_does_not_expose_internal_style_profile() -> None:
    task = Task(
        id=uuid.uuid4(),
        source_url="testing://reference-books",
        status=TaskStatus.COMPLETED,
        current_step="Analysis Complete",
        config={
            "category": "testing_endpoint",
            "testing_endpoint": {
                "status": "analysis_complete",
                "analysis_message": "Analysis Complete",
                "style_profile": "private profile",
                "article": "final article",
                "articles": [{"video_url": "https://example.test/1", "article": "final article", "status": "completed"}],
            },
        },
    )

    payload = testing_endpoint.testing_payload(task)

    assert payload["message"] == "Analysis Complete"
    assert payload["article"] == "final article"
    assert payload["articles"][0]["video_url"] == "https://example.test/1"
    assert "style_profile" not in payload
    assert "private profile" not in str(payload)


def test_reference_analysis_persists_numeric_profile_but_not_raw_book(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_text = "这是绝不能持久化的参考书原文。" * 100
    book = testing_endpoint.UploadedReferenceBook(
        filename="human.txt",
        text=raw_text,
        sha256="a" * 64,
    )
    monkeypatch.setattr(testing_endpoint, "_require_openai_credentials", lambda: "test-key")
    monkeypatch.setattr(
        testing_endpoint,
        "_build_style_profile",
        lambda books, reference_statistics: ("abstract profile", []),
    )

    task = testing_endpoint.analyze_reference_books(
        _FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        books=[book],
    )

    persisted = task.config["testing_endpoint"]
    assert persisted["style_feature_version"] == "style-probe-v2"
    assert persisted["reference_statistics"]["char_count"] > 0
    assert persisted["style_profile"] == "abstract profile"
    assert raw_text not in str(task.config)


def test_full_article_rewrite_uses_abstract_profile_and_returns_measured_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "原稿保留事实。这个句子继续说明同一件事，不新增材料。" * 60
    candidate = "事实仍在。原有说明换了节奏，但没有另添材料。\n\n这一层意思，仍旧如此。" * 45
    reference_text = "短句。忽然拉长一点，把前后几层意思放在同一句里说清楚。\n\n再停一下。" * 80
    prompts: list[tuple[str, str]] = []

    def fake_call(prompt: str, *, action: str, max_output_tokens: int | None):
        prompts.append((action, prompt))
        if action == "testing_completed_article_audit":
            return "## 审查\n需要全稿调整节奏。", {"action": action, "model": "gpt-test"}
        return candidate, {"action": action, "model": "gpt-test"}

    monkeypatch.setattr(testing_endpoint, "_require_openai_credentials", lambda: "test-key")
    monkeypatch.setattr(testing_endpoint, "_call_openai_testing", fake_call)
    monkeypatch.setattr(testing_endpoint, "_testing_model", lambda: "gpt-test")
    task = Task(
        id=uuid.uuid4(),
        source_url="testing://reference-books",
        status=TaskStatus.COMPLETED,
        current_step="Analysis Complete",
        config={
            "category": "testing_endpoint",
            "testing_endpoint": {
                "status": "analysis_complete",
                "style_profile": "只包含抽象节奏画像",
                "style_feature_version": "style-probe-v2",
                "reference_statistics": testing_endpoint.profile_style(reference_text),
                "openai_calls": [],
            },
        },
    )

    result = testing_endpoint.rewrite_completed_article(
        _FakeDb(),
        task=task,
        original_text=source,
        transfer_strength="maximum",
    )

    assert result["candidate_text"] == candidate
    assert result["style_feature_version"] == "style-probe-v2"
    assert result["surface_change_percent"] > 0
    assert result["selected_attempt"] == 1
    assert len(result["rewrite_attempts"]) == 2
    assert result["original_sha256"] != result["candidate_sha256"]
    assert result["original_distance"]["feature_count"] > 10
    assert result["candidate_distance"]["feature_count"] > 10
    assert [item[0] for item in prompts] == [
        "testing_completed_article_audit",
        "testing_completed_article_rewrite",
        "testing_completed_article_measured_refinement",
    ]
    assert "只包含抽象节奏画像" in prompts[1][1]
    assert "参考书原文" not in prompts[1][1]
    persisted = task.config["testing_endpoint"]["last_rewrite_experiment"]
    assert persisted["original_text"] == source
    assert persisted["candidate_text"] == candidate


def test_verified_zhuque_feedback_is_hash_bound_and_idempotent() -> None:
    original_hash = "a" * 64
    candidate_hash = "b" * 64
    task = Task(
        id=uuid.uuid4(),
        source_url="testing://reference-books",
        status=TaskStatus.COMPLETED,
        current_step="ready",
        config={
            "category": "testing_endpoint",
            "testing_endpoint": {
                "last_rewrite_experiment": {
                    "original_sha256": original_hash,
                    "candidate_sha256": candidate_hash,
                    "style_feature_version": "style-probe-v2",
                    "original_statistics": {"sentence_length_cv": 0.2},
                    "candidate_statistics": {"sentence_length_cv": 0.5},
                    "original_distance": {"overall_distance": 0.4},
                    "candidate_distance": {"overall_distance": 0.2},
                    "surface_change_percent": 45.0,
                    "model": "gpt-test",
                    "transfer_strength": "maximum",
                },
            },
        },
    )
    baseline = {
        "original_sha256": original_hash,
        "source_match_verified": True,
        "official_overview_verified": True,
        "official_human_percent": 0.0,
        "report_sha256": "c" * 64,
        "report_storage_id": "baseline.pdf",
    }
    candidate = {
        "original_sha256": candidate_hash,
        "source_match_verified": True,
        "official_overview_verified": True,
        "official_human_percent": 28.5,
        "report_sha256": "d" * 64,
        "report_storage_id": "candidate.pdf",
    }

    first = testing_endpoint.record_zhuque_feedback(
        _FakeDb(),
        task=task,
        baseline_report=baseline,
        candidate_report=candidate,
    )
    second = testing_endpoint.record_zhuque_feedback(
        _FakeDb(),
        task=task,
        baseline_report=baseline,
        candidate_report=candidate,
    )

    assert first["human_delta_percent_points"] == 28.5
    assert second["verified_observation_count"] == 1
    observation = task.config["testing_endpoint"]["verified_zhuque_observations"][0]
    assert observation["style_feature_delta"]["sentence_length_cv"] == 0.3
    assert observation["evidence_type"] == "real_zhuque_verified_pair"


def test_zhuque_feedback_rejects_report_for_another_candidate() -> None:
    task = Task(
        id=uuid.uuid4(),
        source_url="testing://reference-books",
        status=TaskStatus.COMPLETED,
        current_step="ready",
        config={
            "category": "testing_endpoint",
            "testing_endpoint": {
                "last_rewrite_experiment": {
                    "original_sha256": "a" * 64,
                    "candidate_sha256": "b" * 64,
                },
            },
        },
    )

    with pytest.raises(testing_endpoint.TestingEndpointError) as exc_info:
        testing_endpoint.record_zhuque_feedback(
            _FakeDb(),
            task=task,
            baseline_report={"original_sha256": "a" * 64},
            candidate_report={"original_sha256": "x" * 64},
        )

    assert exc_info.value.status_code == 409
    assert "候选" in str(exc_info.value)


def test_missing_openai_key_stops_with_actionable_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(testing_endpoint.settings, "OPENAI_API_KEY", "")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(testing_endpoint.TestingEndpointError) as exc_info:
        testing_endpoint._require_openai_credentials()

    assert exc_info.value.status_code == 401
    assert "OPENAI_API_KEY" in str(exc_info.value)


def test_uploaded_reference_text_accepts_plain_text() -> None:
    content = ("一段参考书正文。\n" * 80).encode("utf-8")

    book = testing_endpoint.uploaded_reference_book_from_bytes("book.txt", content)

    assert book.filename == "book.txt"
    assert len(book.text) >= 500
    assert len(book.sha256) == 64


def test_uploaded_reference_text_accepts_epub() -> None:
    body = "第一章 这是一本参考书正文。" * 80
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
            <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
              <rootfiles>
                <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
              </rootfiles>
            </container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<package xmlns="http://www.idpf.org/2007/opf">
              <manifest><item id="chap1" href="chap1.xhtml" media-type="application/xhtml+xml"/></manifest>
              <spine><itemref idref="chap1"/></spine>
            </package>""",
        )
        archive.writestr("OEBPS/chap1.xhtml", f"<html><body><p>{body}</p></body></html>")

    book = testing_endpoint.uploaded_reference_book_from_bytes("book.epub", buffer.getvalue())

    assert "第一章" in book.text
    assert len(book.text) >= 500


def test_normalize_candidate_urls_deduplicates_and_cleans() -> None:
    urls, duplicates = testing_endpoint._normalize_candidate_urls([
        "https://example.test/a，",
        "https://example.test/b",
        "https://example.test/a",
        "",
    ])

    assert urls == ["https://example.test/a", "https://example.test/b"]
    assert duplicates == ["https://example.test/a"]
