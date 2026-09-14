from contextlib import nullcontext
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.services import general_longform_runtime as runtime
from app.services import content_engine, gemini
from app.utils import paid_api_guard, distributed_limiter


def test_logical_slot_reuses_original_prompt_and_completed_paid_call(tmp_path, monkeypatch):
    prompts = []
    def writer(prompt, **kwargs):
        prompts.append(prompt)
        return "完整正文", {"input_tokens": 12, "output_tokens": 3}
    monkeypatch.setattr(content_engine, "_openai_writer_call_result", writer)
    gateway = runtime.ModelGateway(tmp_path, str(uuid4()))
    first = gateway.call("draft", {"current_time": "before"}, slot="section-1")
    second = gateway.call("draft", {"current_time": "after", "sources": {"changed": True}}, slot="section-1")
    assert second == first and len(prompts) == 1
    assert first["prompt_sha256"] == sha256(prompts[0].encode()).hexdigest()
    assert all(key in first for key in ("action", "model", "estimated_cost", "status", "error"))
    gateway.call("draft", {}, slot="section-2")
    assert len(prompts) == 2


def test_uncertain_call_requires_explicit_retry_and_preserves_attempt_history(tmp_path, monkeypatch):
    calls = []
    def writer(prompt, **kwargs):
        calls.append(1)
        if len(calls) == 1: raise TimeoutError("delivery lost")
        return "新的返回", {"input_tokens": 1, "output_tokens": 1}
    monkeypatch.setattr(content_engine, "_openai_writer_call_result", writer)
    request_id = str(uuid4())
    gateway = runtime.ModelGateway(tmp_path, request_id)
    with pytest.raises(TimeoutError): gateway.call("draft", {})
    with pytest.raises(runtime.GeneralLongformReview, match="待核对"): gateway.call("draft", {"new": "context"})
    assert len(calls) == 1
    result = runtime.ModelGateway(tmp_path, request_id, retry_uncertain=True).call("draft", {})
    assert len(calls) == 2 and result["attempt"] == 2
    assert list((tmp_path / "calls" / request_id).glob("*.history"))


def test_background_resume_uses_durable_response_id_not_second_post(tmp_path, monkeypatch):
    calls = []
    def writer(prompt, **kwargs):
        calls.append(kwargs.get("background_response_id"))
        if not kwargs.get("background_response_id"):
            kwargs["on_background_response_id"]("resp_existing")
            raise TimeoutError("poll interrupted")
        return "原响应结果", {"input_tokens": 5, "output_tokens": 6}
    monkeypatch.setattr(content_engine, "_openai_writer_call_result", writer)
    gateway = runtime.ModelGateway(tmp_path, str(uuid4()))
    with pytest.raises(TimeoutError): gateway.call("outline", {})
    result = gateway.call("outline", {"changed": True})
    assert result["text"] == "原响应结果"
    assert calls == [None, "resp_existing"]


def test_gemini_single_attempt_and_provider_is_logged(tmp_path, monkeypatch):
    calls = []
    def generate(prompt, **kwargs):
        calls.append(kwargs)
        return "当前章正文"
    monkeypatch.setattr(gemini, "generate_text", generate)
    result = runtime.ModelGateway(tmp_path, str(uuid4())).call("polish", {}, provider="gemini")
    assert calls[0]["retries"] == 1
    assert result["provider"] == "gemini" and result["usage"]["estimated"]


def test_context_limit_does_not_silently_truncate_or_bill(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs): raise AssertionError("must not call provider")
    monkeypatch.setattr(content_engine, "_openai_writer_call_result", unexpected)
    with pytest.raises(runtime.GeneralLongformReview, match="未截断"):
        runtime.ModelGateway(tmp_path, str(uuid4())).call("draft", {"full_text": "文" * 360000})
    assert not list(tmp_path.glob("calls/**/*"))


@pytest.mark.parametrize("raw", ["[]", "not json", '{"broken":', '```json\n[1]\n```'])
def test_invalid_model_json_fails_closed(raw):
    with pytest.raises(runtime.GeneralLongformReview): runtime.parse_json(raw)


def test_web_search_incomplete_response_not_silently_resubmitted(tmp_path, monkeypatch):
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(id="web_resp", status="incomplete", output=[], usage=None, output_text="partial")
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    client.with_options = lambda **kwargs: client
    monkeypatch.setattr(content_engine, "_get_openai_writer_client", lambda: client)
    monkeypatch.setattr(content_engine, "_extract_openai_response_text", lambda response: response.output_text)
    monkeypatch.setattr(paid_api_guard, "guard", lambda *args: None)
    monkeypatch.setattr(distributed_limiter, "distributed_semaphore", lambda *args, **kwargs: nullcontext())
    gateway = runtime.ModelGateway(tmp_path, str(uuid4()))
    with pytest.raises(runtime.GeneralLongformReview): gateway.call("research", {}, web=True)
    with pytest.raises(runtime.GeneralLongformReview): gateway.call("research", {}, web=True)
    assert len(calls) == 1
    record = json.loads(next(tmp_path.glob("calls/*/*.json")).read_text(encoding="utf-8"))
    assert record["text"] == "partial" and record["response_id"] == "web_resp"


class ResearchGateway:
    def __init__(self, directory, *, tool_urls=None, proposed_urls=None, completed=True):
        self.directory, self.request_id = directory, str(uuid4())
        self.check_active = lambda: None
        self.tool_urls = tool_urls if tool_urls is not None else ["https://example.org/research"]
        self.proposed_urls = proposed_urls if proposed_urls is not None else self.tool_urls
        self.completed = completed
        self.calls = []

    def call(self, stage, context, **kwargs):
        self.calls.append(stage)
        return {"text": json.dumps({"topic": "测试", "domain": "认知成长与效能提升", "sources": [{"url": url, "title": "研究", "authors": "Institute", "year": "2026"} for url in self.proposed_urls]}),
                "web_calls": [{"type": "web_search_call", "status": "completed" if self.completed else "failed", "action": {"sources": [{"url": url} for url in self.tool_urls]}}]}

    def json(self, stage, context, **kwargs):
        self.calls.append(stage)
        return {"sources": [{"id": page["id"], "url": page["url"], "bibliography": "Institute. Study. 2026.", "approved_summary": "依据与范围", "supported_claims": ["受限定的测试事实"], "limitations": []} for page in context["pages"]], "gaps": []}


def page(url):
    return {"url": url, "text": "实际读取页面的内容" * 20, "read_scope": "测试页面", "retrieved_at": "2026-09-14T08:00:00Z", "file_sha256": "a" * 64, "text_sha256": "b" * 64}


def test_research_requires_actual_search_and_page_read_then_only_returns_abstracts(tmp_path):
    gateway = ResearchGateway(tmp_path)
    fetched = []
    def fetch(url): fetched.append(url); return page(url)
    result = runtime.research(gateway, {}, fetch=fetch)
    assert len(fetched) == 1 and result["web_call_completed"]
    source = next(iter(result["sources"].values()))
    assert source["verification"] == "page_read_and_abstracted" and source["file_sha256"]
    assert "text" not in source and "实际读取页面的内容" not in json.dumps(result, ensure_ascii=False)
    calls = list(gateway.calls)
    assert runtime.research(gateway, {"changed": True}, fetch=fetch) == result
    assert gateway.calls == calls and len(fetched) == 1


@pytest.mark.parametrize("completed,tool_urls,proposed_urls", [
    (False, ["https://example.org/research"], ["https://example.org/research"]),
    (True, ["https://example.org/research"], ["https://fabricated.invalid/"]),
])
def test_self_reported_web_research_and_invented_urls_are_not_evidence(tmp_path, completed, tool_urls, proposed_urls):
    gateway = ResearchGateway(tmp_path, completed=completed, tool_urls=tool_urls, proposed_urls=proposed_urls)
    with pytest.raises(runtime.GeneralLongformReview): runtime.research(gateway, {}, fetch=page)
    assert "evidence" not in gateway.calls


def test_failed_reads_are_recorded_not_fabricated(tmp_path):
    gateway = ResearchGateway(tmp_path)
    def fail(url): raise httpx.ConnectError("unreachable")
    with pytest.raises(runtime.GeneralLongformReview, match="未成功读取"):
        runtime.research(gateway, {}, fetch=fail)
    error_file = next(tmp_path.glob("research/**/errors.json"))
    assert "unreachable" in error_file.read_text(encoding="utf-8")


def test_large_sources_are_abstracted_in_complete_bounded_groups(tmp_path):
    gateway = ResearchGateway(tmp_path, tool_urls=[f"https://example.org/{i}" for i in range(8)])
    def fetch(url): return {**page(url), "text": "文" * 60000}
    result = runtime.research(gateway, {}, fetch=fetch)
    assert len(result["sources"]) == 8 and gateway.calls.count("evidence") == 3


@pytest.mark.parametrize("url", ["http://127.0.0.1", "http://169.254.169.254", "file:///etc/passwd", "https://name:pass@example.org", "http://example.org:9000"])
def test_reader_rejects_nonpublic_urls(monkeypatch, url):
    monkeypatch.setattr(runtime.socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("127.0.0.1", 80))])
    with pytest.raises(runtime.GeneralLongformError): runtime.validate_public_url(url)


def test_reader_pins_public_ip_and_retains_hostname_for_tls(monkeypatch):
    monkeypatch.setattr(runtime.socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("1.1.1.1", 443))])
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, headers={"Content-Type": "text/html; charset=utf-8"}, content=("<html><p>真正读取的公开材料" + "正文" * 100 + "</p></html>").encode())
    real_client = httpx.Client
    monkeypatch.setattr(runtime.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    result = runtime.fetch_public_page("https://example.org/research")
    assert requests[0].url.host == "1.1.1.1"
    assert requests[0].headers["Host"] == "example.org" and requests[0].extensions["sni_hostname"] == "example.org"
    assert result["url"] == "https://example.org/research" and result["file_sha256"]
