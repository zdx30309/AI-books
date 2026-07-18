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
