"""A retried chapter must never pay for the same web search twice."""
from hashlib import sha256
import json
from uuid import uuid4

from app.services import general_longform_runtime as runtime


class ResearchGateway:
    """Deterministic double: no network, no paid API, no quality evidence."""

    def __init__(self, directory, request_id=None, *, url="https://example.org/research", prompt_version=None):
        self.directory = directory
        self.request_id = request_id or str(uuid4())
        self.prompt_version = prompt_version
        self.check_active = lambda: None
        self.url = url
        self.calls = []

    def call(self, stage, context, **kwargs):
        self.calls.append(stage)
        return {"text": json.dumps({"topic": "测试", "domain": "认知成长与效能提升",
                                    "sources": [{"url": self.url, "title": "研究", "authors": "Institute", "year": "2026"}]}),
                "web_calls": [{"type": "web_search_call", "status": "completed",
                               "action": {"sources": [{"url": self.url}]}}]}

    def json(self, stage, context, **kwargs):
        self.calls.append(stage)
        return {"sources": [{"id": page["id"], "url": page["url"], "bibliography": "Institute. Study. 2026.",
                             "approved_summary": "依据与范围", "supported_claims": ["受限定的测试事实"], "limitations": []}
                            for page in context["pages"]], "gaps": []}


def page(url):
    return {"url": url, "text": "实际读取页面的内容" * 20, "read_scope": "测试页面",
            "retrieved_at": "2026-09-14T08:00:00Z", "file_sha256": "a" * 64, "text_sha256": "b" * 64}


CHAPTER = {"number": 7, "title": "观察与选择7", "source_ids": ["Sverified"]}
SLOT = "chapter-7"


def read(url, seen):
    seen.append(url)
    return page(url)


def test_a_retried_chapter_reuses_the_search_the_book_already_paid_for(tmp_path):
    first = ResearchGateway(tmp_path, "request-one")
    fetched = []
    result = runtime.research(first, {"chapter": CHAPTER}, fetch=lambda url: read(url, fetched), slot=SLOT)
    assert first.calls == ["research", "evidence"] and fetched == ["https://example.org/research"]

    second = ResearchGateway(tmp_path, "request-two")

    def never(url):
        raise AssertionError("a reused slot must not fetch a page again")

    reused = runtime.research(second, {"chapter": CHAPTER}, fetch=never, slot=SLOT)
    assert second.calls == [] and reused["reused_from_request"] == "request-one"
    assert reused["sources"] == result["sources"] and reused["web_call_completed"] is True
    stored = json.loads((tmp_path / "research" / "request-two" / sha256(SLOT.encode()).hexdigest()[:16] / "approved.json").read_text(encoding="utf-8"))
    assert stored["sources"] and stored["slot_signature"] == result["slot_signature"]


def test_a_changed_chapter_contract_pays_for_its_own_search(tmp_path):
    first = ResearchGateway(tmp_path, "request-one")
    runtime.research(first, {"chapter": CHAPTER}, fetch=lambda url: page(url), slot=SLOT)
    second = ResearchGateway(tmp_path, "request-two")
    runtime.research(second, {"chapter": {**CHAPTER, "title": "改过的标题"}}, fetch=lambda url: page(url), slot=SLOT)
    assert second.calls == ["research", "evidence"]


def test_changed_feedback_re_searches_instead_of_reusing_a_stale_answer(tmp_path):
    first = ResearchGateway(tmp_path, "request-one")
    runtime.research(first, {"outline": {"target_words": 1}, "feedback": "第一次要求"}, fetch=lambda url: page(url), slot="outline-revision")
    second = ResearchGateway(tmp_path, "request-two")
    runtime.research(second, {"outline": {"target_words": 1}, "feedback": "第二次要求"}, fetch=lambda url: page(url), slot="outline-revision")
    assert second.calls == ["research", "evidence"]


def test_a_neighbour_chapter_never_borrows_another_chapters_search(tmp_path):
    first = ResearchGateway(tmp_path, "request-one")
    runtime.research(first, {"chapter": CHAPTER}, fetch=lambda url: page(url), slot=SLOT)
    second = ResearchGateway(tmp_path, "request-two")
    runtime.research(second, {"chapter": CHAPTER}, fetch=lambda url: page(url), slot="chapter-8")
    assert second.calls == ["research", "evidence"]


def test_a_new_prompt_version_is_a_new_question(tmp_path):
    first = ResearchGateway(tmp_path, "request-one", prompt_version="v1")
    runtime.research(first, {"chapter": CHAPTER}, fetch=lambda url: page(url), slot=SLOT)
    second = ResearchGateway(tmp_path, "request-two", prompt_version="v2")
    runtime.research(second, {"chapter": CHAPTER}, fetch=lambda url: page(url), slot=SLOT)
    assert second.calls == ["research", "evidence"]
