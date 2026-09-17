"""No-network regression for the production 401/account_deactivated incident."""
from contextlib import nullcontext
import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
from uuid import uuid4

import httpx
from openai import APIStatusError, AuthenticationError
import pytest

from app.services import content_engine as writer
from app.services import general_longform_runtime as runtime
from app.tasks import tasks
from app.utils import distributed_limiter, paid_api_guard


def rejection(status=401, code="account_deactivated"):
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
                              headers={"x-request-id": "req_auth_test"})
    body = {"message": "The OpenAI account associated with this API key has been deactivated.",
            "code": code, "type": "invalid_request_error"}
    cls = AuthenticationError if status == 401 else APIStatusError
    return cls(body["message"], response=response, body=body)


@pytest.fixture(autouse=True)
def no_network_or_wait(monkeypatch):
    monkeypatch.setattr(paid_api_guard, "guard", lambda *_: None)
    monkeypatch.setattr(paid_api_guard, "trip_if_quota", lambda *_: False)
    monkeypatch.setattr(writer, "distributed_semaphore", lambda *a, **kw: nullcontext())
    monkeypatch.setattr(distributed_limiter, "distributed_semaphore", lambda *a, **kw: nullcontext())
    monkeypatch.setattr(writer.time, "sleep", lambda *_: None)
    monkeypatch.setattr(writer, "_reset_openai_writer_client", lambda: None)
    monkeypatch.setattr(writer.settings, "OPENAI_WRITER_STREAM", False)
    monkeypatch.setattr(writer.settings, "OPENAI_WRITER_MAX_RETRIES", 3)


@pytest.mark.parametrize("background,stream", [(False, False), (True, False), (False, True)])
def test_writer_preserves_rejection_and_never_retries_it(monkeypatch, background, stream):
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        raise rejection()
    monkeypatch.setattr(writer.settings, "OPENAI_WRITER_STREAM", stream)
    monkeypatch.setattr(writer, "_get_openai_writer_client", lambda: SimpleNamespace(responses=SimpleNamespace(create=create)))
    with pytest.raises(writer.OpenAIRequestRejectedError) as caught:
        writer._openai_writer_call_result("same prompt", background=background, retry_on_failure=True)
    error = caught.value
    assert error.status_code == 401 and error.code == "account_deactivated"
    assert error.request_id == "req_auth_test" and isinstance(error.__cause__, AuthenticationError)
    assert len(calls) == 1
    assert "账户已停用" in str(error) and "OPENAI_API_KEY" in str(error)
    assert "初稿生成连接失败" not in str(error) and "请确认 OPENAI_HTTPS_PROXY" not in str(error)
    assert not tasks._is_retryable_generation_error(error)
    assert not tasks._is_retryable_error_message(str(error))


@pytest.mark.parametrize("status,code", [(400, "invalid_parameter"), (401, "invalid_api_key"), (403, "permission_denied"),
                                         (404, "model_not_found"), (409, "conflict"), (422, "invalid_input")])
def test_metadata_survives_chained_exceptions(status, code):
    root = rejection(status, code)
    wrapped = RuntimeError("outer wrapper")
    wrapped.__cause__ = root
    error = writer._openai_writer_failure(wrapped)
    assert isinstance(error, writer.OpenAIRequestRejectedError)
    assert (error.status_code, error.code) == (status, code)
    assert "请确认 OPENAI_HTTPS_PROXY" not in str(error)


def test_auth_diagnostic_does_not_disclose_key_or_proxy_credentials(monkeypatch):
    secret = "sk-shouldneverappear0123456789"
    monkeypatch.setattr(writer.settings, "OPENAI_HTTPS_PROXY", "http://user:private-password@proxy.example:7898")
    exc = rejection()
    exc.args = ("Incorrect API key: " + secret,)
    message = writer._openai_writer_connection_error_message(exc)
    assert secret not in message and "private-password" not in message and "proxy.example" not in message


def test_transport_failure_remains_distinct_from_auth(monkeypatch):
    monkeypatch.setattr(writer.settings, "OPENAI_HTTPS_PROXY", "http://proxy.example:7898")
    error = writer._openai_writer_failure(httpx.ConnectError("connection refused"))
    assert not isinstance(error, writer.OpenAIRequestRejectedError)
    assert "初稿生成连接失败" in str(error) and "OPENAI_HTTPS_PROXY" in str(error)


def test_failed_auth_journal_resumes_original_prompt_without_uncertain_confirmation(tmp_path, monkeypatch):
    prompts = []
    def call(prompt, **kwargs):
        prompts.append(prompt)
        if len(prompts) == 1:
            raise writer._openai_writer_failure(rejection())
        return "restored result", {"input_tokens": 5, "output_tokens": 3}
    monkeypatch.setattr(writer, "_openai_writer_call_result", call)
    gateway = runtime.ModelGateway(tmp_path, str(uuid4()))
    with pytest.raises(writer.OpenAIRequestRejectedError):
        gateway.call("reference_analysis", {})
    path = next(tmp_path.glob("calls/*/*.json"))
    failed = json.loads(path.read_text(encoding="utf-8"))
    assert failed["status"] == "failed" and failed["http_status"] == 401
    assert failed["error_code"] == "account_deactivated" and failed["failure_kind"] == "authentication"
    assert failed["delivery_status"] == "rejected_before_generation"
    assert failed["estimated_cost"] == 0 and not failed["response_id"]
    result = gateway.call("reference_analysis", {"different_context": True})
    assert result["status"] == "completed" and result["attempt"] == 2
    assert len(prompts) == 2 and prompts[0] == prompts[1]
    assert json.loads(next(tmp_path.glob("calls/*/*.history")).read_text(encoding="utf-8"))["status"] == "failed"


def test_existing_paid_response_is_preserved_on_auth_failure(tmp_path, monkeypatch):
    response_ids = []
    def call(prompt, **kwargs):
        response_ids.append(kwargs.get("background_response_id"))
        if len(response_ids) == 1:
            kwargs["on_background_response_id"]("resp_already_created")
            raise rejection()
        return "original paid response", {"input_tokens": 5, "output_tokens": 3}
    monkeypatch.setattr(writer, "_openai_writer_call_result", call)
    gateway = runtime.ModelGateway(tmp_path, str(uuid4()))
    with pytest.raises(AuthenticationError): gateway.call("outline", {})
    record = json.loads(next(tmp_path.glob("calls/*/*.json")).read_text(encoding="utf-8"))
    assert record["status"] == "uncertain" and record["response_id"] == "resp_already_created"
    assert record.get("estimated_cost") is None
    assert record.get("delivery_status") != "rejected_before_generation"
    assert gateway.call("outline", {})["status"] == "completed"
    assert response_ids == [None, "resp_already_created"]


@pytest.mark.parametrize("status", [408, 500])
def test_ambiguous_http_errors_stay_held(tmp_path, monkeypatch, status):
    def call(*args, **kwargs): raise rejection(status, "request_timeout")
    monkeypatch.setattr(writer, "_openai_writer_call_result", call)
    gateway = runtime.ModelGateway(tmp_path, str(uuid4()))
    with pytest.raises(APIStatusError): gateway.call("draft", {})
    record = json.loads(next(tmp_path.glob("calls/*/*.json")).read_text(encoding="utf-8"))
    assert record["status"] == "uncertain" and record.get("estimated_cost") is None
    with pytest.raises(runtime.GeneralLongformReview, match="待核对"): gateway.call("draft", {})


def test_uncertain_wrapper_is_not_downgraded_by_nested_auth_error(tmp_path, monkeypatch):
    def call(*args, **kwargs):
        raise writer.OpenAIDeliveryUncertainError("interrupted after submission") from rejection()
    monkeypatch.setattr(writer, "_openai_writer_call_result", call)
    gateway = runtime.ModelGateway(tmp_path, str(uuid4()))
    with pytest.raises(writer.OpenAIDeliveryUncertainError): gateway.call("draft", {})
    record = json.loads(next(tmp_path.glob("calls/*/*.json")).read_text(encoding="utf-8"))
    assert record["status"] == "uncertain" and record.get("estimated_cost") is None
    assert "paid_call_delivery_uncertain" in record["error"]


def test_web_auth_failure_has_same_classification(tmp_path, monkeypatch):
    def create(**kwargs): raise rejection()
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    client.with_options = lambda **kwargs: client
    monkeypatch.setattr(writer, "_get_openai_writer_client", lambda: client)
    with pytest.raises(writer.OpenAIRequestRejectedError):
        runtime.ModelGateway(tmp_path, str(uuid4())).call("research", {}, web=True)
    record = json.loads(next(tmp_path.glob("calls/*/*.json")).read_text(encoding="utf-8"))
    assert record["status"] == "failed" and record["failure_kind"] == "authentication"
    assert "账户已停用" in record["error"]


@pytest.mark.parametrize("mode,exit_code", [("ok", 0), ("rejected", 2), ("missing", 2)])
def test_read_only_auth_check_never_generates_or_prints_key(monkeypatch, capsys, mode, exit_code):
    secret = "sk-only-for-test-never-print-this"
    monkeypatch.setattr(writer.settings, "OPENAI_API_KEY", "" if mode == "missing" else secret)
    calls = []
    def models():
        calls.append("GET /models")
        if mode == "rejected": raise rejection()
        return SimpleNamespace(data=[SimpleNamespace(id=writer.settings.OPENAI_WRITER_MODEL)])
    client = SimpleNamespace(models=SimpleNamespace(list=models))
    client.with_options = lambda **kwargs: client
    monkeypatch.setattr(writer, "_get_openai_writer_client", lambda: client)
    script = Path(__file__).resolve().parents[2] / "scripts" / "check_openai_auth.py"
    monkeypatch.setattr(sys, "argv", [str(script), "--timeout", "2"])
    with pytest.raises(SystemExit) as caught:
        runpy.run_path(str(script), run_name="__main__")
    assert caught.value.code == exit_code
    output = capsys.readouterr().out
    assert secret not in output
    result = json.loads(output)
    assert result["generation_requests"] == 0
    assert len(calls) == (0 if mode == "missing" else 1)
    if mode == "rejected": assert result["error_code"] == "account_deactivated"
