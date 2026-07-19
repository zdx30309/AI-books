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
            "truncation": "disabled",
            "max_output_tokens": 123,
        }
    ]


def test_openai_call_retries_empty_response_once_without_hidden_truncation(
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
        action="testing_reference_chunk_compression",
        max_output_tokens=2500,
    )

    assert text == "style note"
    assert calls[0]["reasoning"] == {"effort": "medium"}
    assert calls[0]["max_output_tokens"] == 6000
    assert calls[0]["truncation"] == "disabled"
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
            action="testing_reference_chunk_compression",
            max_output_tokens=2500,
        )

    assert exc_info.value.status_code == 502
    assert "max_output_tokens" in str(exc_info.value)
    assert "自动降低推理强度并重试一次" in str(exc_info.value)
    assert len(calls) == 2


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


def test_reference_book_chunk_size_has_a_safe_upper_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(testing_endpoint.settings, "TESTING_ENDPOINT_BOOK_CHUNK_CHARS", 80_000)

    assert testing_endpoint._book_chunk_chars() == 50_000


def test_reference_upload_persists_compression_but_not_raw_book(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_text = "这是绝不能持久化的参考书原文。" * 100
    book = testing_endpoint.UploadedReferenceBook(
        filename="human.txt",
        text=raw_text,
        sha256="a" * 64,
    )
    monkeypatch.setattr(testing_endpoint, "_require_openai_credentials", lambda: "test-key")
    monkeypatch.setattr(testing_endpoint, "_build_reference_compression", lambda books: (
        "可迁移写作参照；禁止模仿具体作者风格。",
        [{"action": "testing_reference_chunk_compression"}],
        {
            "book_count": 1,
            "source_chars": len(raw_text),
            "chunk_count": 1,
            "chunk_chars": 50_000,
            "compressed_chars": 20,
            "raw_text_persisted": False,
            "specific_style_imitation_allowed": False,
        },
    ))

    task = testing_endpoint.analyze_reference_books(
        _FakeDb(),
        user=SimpleNamespace(id=uuid.uuid4()),
        books=[book],
    )

    persisted = task.config["testing_endpoint"]
    assert persisted["organization_policy_version"] == "information-organization-v4-chunk-compressed-no-style-imitation"
    assert persisted["reference_input_mode"] == "chunk_compressed"
    assert persisted["reference_compression"].startswith("可迁移写作参照")
    assert persisted["compression_metadata"]["specific_style_imitation_allowed"] is False
    assert "style_profile" not in persisted
    assert "style_feature_version" not in persisted
    assert "reference_statistics" not in persisted
    assert raw_text not in str(task.config)


def test_reference_compression_reads_every_book_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts = []
    books = [
        testing_endpoint.UploadedReferenceBook("one.epub", "第一本开头。\n\n第一本结尾。", "a" * 64),
        testing_endpoint.UploadedReferenceBook("two.pdf", "第二本开头。\n\n第二本最后一句。", "b" * 64),
    ]
    def fake_call(prompt: str, *, action: str, max_output_tokens: int | None):
        prompts.append((action, prompt))
        if action == "testing_reference_compression_synthesis":
            return "最终压缩结果：只保留可迁移参照，禁止模仿具体作者风格。", {"action": action}
        return f"分片压缩{len(prompts)}", {"action": action}

    monkeypatch.setattr(testing_endpoint, "_call_openai_testing", fake_call)
    compression, calls, metadata = testing_endpoint._build_reference_compression(books)

    assert compression.startswith("最终压缩结果")
    assert metadata["book_count"] == 2
    assert metadata["chunk_count"] == 2
    assert len(calls) == 3
    assert "第一本开头" in prompts[0][1]
    assert "第一本结尾" in prompts[0][1]
    assert "第二本开头" in prompts[1][1]
    assert "第二本最后一句" in prompts[1][1]
    assert "分片压缩1" in prompts[2][1]
    assert "分片压缩2" in prompts[2][1]


def test_full_article_rewrite_uses_chunk_compression_without_style_imitation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "原稿保留事实。这个句子继续说明同一件事，不新增材料。" * 60
    candidate = "事实仍在。原有说明换了节奏，但没有另添材料。\n\n这一层意思，仍旧如此。" * 45
    prompts: list[tuple[str, str]] = []

    def fake_call(prompt: str, *, action: str, max_output_tokens: int | None):
        prompts.append((action, prompt))
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
                "reference_compression": "标点、推进、句长等可迁移写作参照。禁止模仿作者具体风格。",
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
    assert result["organization_policy_version"] == "information-organization-v4-chunk-compressed-no-style-imitation"
    assert result["surface_change_percent"] > 0
    assert result["original_sha256"] != result["candidate_sha256"]
    assert "style_feature_version" not in result
    assert "reference_statistics" not in result
    assert "original_distance" not in result
    assert [item[0] for item in prompts] == ["testing_completed_article_rewrite"]
    for _, rendered_prompt in prompts:
        complete_rules = testing_endpoint._render_prompt(
            "testing_endpoint_source_organization_rules.md"
        ).strip()
        assert complete_rules in rendered_prompt
        assert "信息组织规则（最高优先级）" in rendered_prompt
        assert "发生冲突时必须舍弃参考书参照" in rendered_prompt
        assert "由参考书全部分片逐层压缩得到的写作参照" in rendered_prompt
        assert "不得声称、暗示或尝试“像参考书作者一样写”" in rendered_prompt
        assert "词语搭配" in rendered_prompt
        assert "语法和句子结构" in rendered_prompt
        assert "判断方式" in rendered_prompt
        assert "论证习惯" in rendered_prompt
        assert "不要为了逻辑更顺而调整段落顺序" in rendered_prompt
        assert "不要把每一段都写成“完整单元”" in rendered_prompt
        assert "修改后的文章完成度，不应明显高于原稿" in rendered_prompt
        assert "保持原作者的“思考轨迹”" in rendered_prompt
        assert "你的任务是让文章更像作者本人重新写了一遍" in rendered_prompt
        assert "{{SOURCE_ORGANIZATION_RULES}}" not in rendered_prompt
        assert "可以重新切分或组合句子" in rendered_prompt
        assert "保留全部事实、数据、案例、人物、核心观点" in rendered_prompt
        assert "genuine full-document recomposition" not in rendered_prompt
        assert "change the full article's statistical shape" not in rendered_prompt
        assert "Reconsider the full document" not in rendered_prompt
        assert "REFERENCE_STATISTICS" not in rendered_prompt
        assert "ORIGINAL_STATISTICS" not in rendered_prompt
    assert "STYLE_PROFILE" not in prompts[0][1]
    assert "参考书全文已逐片读取并压缩" in result["audit_notes"]
    persisted = task.config["testing_endpoint"]["last_rewrite_experiment"]
    assert persisted["original_text"] == source
    assert persisted["candidate_text"] == candidate


def test_from_scratch_generation_uses_reversed_rules_and_chunk_compression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[tuple[str, str]] = []

    def fake_call(prompt: str, *, action: str, max_output_tokens: int | None):
        prompts.append((action, prompt))
        if action == "testing_video_digest":
            return "事实摘要：先出现甲，随后转到乙。", {"action": action, "model": "gpt-test"}
        return "@最终书名\n测试\n@最终简介\n简介\n@目录\n一\n@导言\n导言\n@正文\n正文", {
            "action": action,
            "model": "gpt-test",
        }

    monkeypatch.setattr(testing_endpoint, "_require_openai_credentials", lambda: "test-key")
    monkeypatch.setattr(testing_endpoint, "_call_openai_testing", fake_call)
    task = Task(
        id=uuid.uuid4(),
        source_url="testing://reference-books",
        status=TaskStatus.COMPLETED,
        current_step="Analysis Complete",
        config={
            "category": "testing_endpoint",
            "testing_endpoint": {
                "status": "analysis_complete",
                "reference_compression": "只含非作者专属的可迁移写作参照。",
                "openai_calls": [],
            },
        },
    )

    articles = testing_endpoint.generate_testing_article(
        _FakeDb(),
        task=task,
        video_urls=["https://example.test/video"],
        raw_video_text="原始素材中的事实和顺序。",
    )

    assert len(articles) == 1
    assert [action for action, _ in prompts] == [
        "testing_video_digest",
        "testing_article_generation",
    ]
    writer_prompt = prompts[1][1]
    assert "从头生成的信息组织规则（由原稿保序规则反向生成，最高优先级）" in writer_prompt
    assert "先在内部形成一条自然、可读、但不被整理成“标准答案”的信息展开轨迹" in writer_prompt
    assert "不要让每个段落都成为完整单元" in writer_prompt
    assert "逐章反向检查" in writer_prompt
    assert "compression built from every chunk" in writer_prompt
    assert "not an instruction to imitate the reference author's style" in writer_prompt
    assert "collocations" in writer_prompt
    assert "grammar" in writer_prompt
    assert "sentence structure" in writer_prompt
    assert "judgment, and argumentation" in writer_prompt
    assert "{{GENERATED_ORGANIZATION_RULES}}" not in writer_prompt
    assert "{{STYLE_PROFILE}}" not in writer_prompt
    assert (
        task.config["testing_endpoint"]["generation_policy_version"]
        == "information-organization-v4-chunk-compressed-no-style-imitation"
    )


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
    assert "style_feature_delta" not in observation
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
        "【6700亿的低空经济，是真风口？】 https://example.test/a，",
        "https://example.test/b",
        "https://example.test/a",
        "只有标题，没有链接",
        "",
    ])

    assert urls == ["https://example.test/a", "https://example.test/b"]
    assert duplicates == ["https://example.test/a"]
