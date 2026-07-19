"""OpenAI-only workflow for the Testing Endpoint."""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from collections import Counter
import hashlib
import logging
import os
import re
from typing import Any
import xml.etree.ElementTree as ET
import zipfile

import httpx
from openai import OpenAI
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT, settings
from app.models import Task, TaskStatus, User
from app.services.aigc_report_review import normalize_manuscript_text
from app.services.content_engine import (
    _extract_openai_response_text,
    _openai_response_debug_summary,
    _openai_usage_payload,
)
from app.tasks.tasks import _fetch_video_text
from app.utils.docx_text import DocxTextError, extract_docx_text, extract_pdf_text
from app.utils.official_api import official_openai_base_url, official_proxy

logger = logging.getLogger(__name__)

PROMPT_DIR = PROJECT_ROOT / "backend" / "app" / "prompts"
TESTING_CONFIG_KEY = "testing_endpoint"
ANALYSIS_COMPLETE_MESSAGE = "参考书全分片压缩完成"
ORGANIZATION_POLICY_VERSION = "information-organization-v4-chunk-compressed-no-style-imitation"


class TestingEndpointError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class UploadedReferenceBook:
    filename: str
    text: str
    sha256: str


def analyze_reference_books(
    db: Session,
    *,
    user: User,
    books: list[UploadedReferenceBook],
) -> Task:
    _require_openai_credentials()
    if not books:
        raise TestingEndpointError("请至少上传 1 本参考书。", status_code=400)
    task = Task(
        source_url="testing://reference-books",
        owner_id=user.id,
        status=TaskStatus.PROCESSING,
        progress=10,
        current_step="Compressing all reference-book chunks",
        config={
            "category": "testing_endpoint",
            "category_label": "Testing Endpoint",
            TESTING_CONFIG_KEY: {
                "status": "compressing_reference_chunks",
                "analysis_message": None,
                "organization_policy_version": ORGANIZATION_POLICY_VERSION,
                "books": [
                    {"filename": book.filename, "sha256": book.sha256, "chars": len(book.text)}
                    for book in books
                ],
                "openai_calls": [],
            },
        },
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    try:
        reference_compression, calls, compression_metadata = _build_reference_compression(books)
        config = dict(task.config or {})
        testing = dict(config.get(TESTING_CONFIG_KEY) or {})
        testing.update(
            {
                "status": "analysis_complete",
                "analysis_message": ANALYSIS_COMPLETE_MESSAGE,
                "organization_policy_version": ORGANIZATION_POLICY_VERSION,
                "reference_input_mode": "chunk_compressed",
                "reference_compression": reference_compression,
                "compression_metadata": compression_metadata,
                "openai_calls": [*list(testing.get("openai_calls") or []), *calls],
            }
        )
        testing.pop("style_profile", None)
        testing.pop("style_feature_version", None)
        testing.pop("reference_statistics", None)
        testing.pop("reference_file", None)
        config[TESTING_CONFIG_KEY] = testing
        task.config = config
        task.status = TaskStatus.COMPLETED
        task.progress = 100
        task.current_step = ANALYSIS_COMPLETE_MESSAGE
        task.error_message = None
        db.commit()
        db.refresh(task)
        return task
    except Exception as exc:
        message, status_code = _testing_issue_message(exc)
        config = dict(task.config or {})
        testing = dict(config.get(TESTING_CONFIG_KEY) or {})
        testing["status"] = "analysis_failed"
        config[TESTING_CONFIG_KEY] = testing
        task.config = config
        task.status = TaskStatus.FAILED
        task.progress = 100
        task.current_step = "Reference chunk compression stopped"
        task.error_message = message
        db.commit()
        raise TestingEndpointError(message, status_code=status_code) from exc


def generate_testing_article(
    db: Session,
    *,
    task: Task,
    video_urls: list[str],
    raw_video_text: str | None = None,
) -> list[dict[str, str]]:
    _require_openai_credentials()
    config = dict(task.config or {})
    testing = dict(config.get(TESTING_CONFIG_KEY) or {})
    reference_compression = str(testing.get("reference_compression") or "").strip()
    if not reference_compression:
        raise TestingEndpointError(
            "当前任务没有可用的参考书分片压缩结果，请重新上传参考书。",
            status_code=409,
        )
    unique_video_urls, duplicate_video_urls = _normalize_candidate_urls(video_urls)
    if not unique_video_urls:
        raise TestingEndpointError("请至少提供 1 个视频链接。", status_code=400)
    if len(unique_video_urls) > int(settings.TESTING_ENDPOINT_MAX_VIDEO_LINKS or 50):
        raise TestingEndpointError(
            f"Testing Endpoint 一次最多处理 {settings.TESTING_ENDPOINT_MAX_VIDEO_LINKS} 个视频链接。",
            status_code=400,
        )

    task.status = TaskStatus.PROCESSING
    task.progress = 20
    task.current_step = "Processing video content"
    task.error_message = None
    db.commit()

    try:
        generated_articles: list[dict[str, str]] = []
        new_calls: list[dict[str, Any]] = []
        for index, video_url in enumerate(unique_video_urls, start=1):
            task.current_step = f"Processing video content {index}/{len(unique_video_urls)}"
            task.progress = min(90, 20 + int((index - 1) / max(1, len(unique_video_urls)) * 70))
            db.commit()

            video_text = _resolve_video_text(video_url, raw_video_text=raw_video_text if len(unique_video_urls) == 1 else None)
            digest, digest_usage = _call_openai_testing(
                _render_prompt(
                    "testing_endpoint_video_digest.md",
                    VIDEO_URL=video_url,
                    VIDEO_TEXT=_limit_text(video_text, _video_max_chars()),
                ),
                action="testing_video_digest",
                max_output_tokens=_max_output_tokens(),
            )
            digest_usage.update({"video_url": video_url})
            new_calls.append(digest_usage)

            task.current_step = f"Generating article {index}/{len(unique_video_urls)} with OpenAI ChatGPT API"
            db.commit()
            article, article_usage = _call_openai_testing(
                _render_prompt(
                    "testing_endpoint_article_writer.md",
                    GENERATED_ORGANIZATION_RULES=_render_prompt(
                        "testing_endpoint_generated_organization_rules.md"
                    ).strip(),
                    REFERENCE_COMPRESSION=reference_compression,
                    VIDEO_DIGEST=digest,
                ),
                action="testing_article_generation",
                max_output_tokens=_max_output_tokens(),
            )
            article_usage.update({"video_url": video_url})
            new_calls.append(article_usage)
            generated_articles.append({"video_url": video_url, "article": article, "status": "completed"})

        testing.update(
            {
                "status": "article_complete",
                "video_url": unique_video_urls[0],
                "video_urls": unique_video_urls,
                "duplicate_video_urls": duplicate_video_urls,
                "generation_policy_version": ORGANIZATION_POLICY_VERSION,
                "article": generated_articles[0]["article"] if len(generated_articles) == 1 else None,
                "articles": generated_articles,
                "openai_calls": [
                    *list(testing.get("openai_calls") or []),
                    *new_calls,
                ],
            }
        )
        config[TESTING_CONFIG_KEY] = testing
        task.config = config
        task.source_url = unique_video_urls[0]
        task.status = TaskStatus.COMPLETED
        task.progress = 100
        task.current_step = "Article Complete"
        task.error_message = None
        db.commit()
        return generated_articles
    except Exception as exc:
        message, status_code = _testing_issue_message(exc)
        testing["status"] = "article_failed"
        testing["video_urls"] = unique_video_urls
        config[TESTING_CONFIG_KEY] = testing
        task.config = config
        task.status = TaskStatus.FAILED
        task.progress = 100
        task.current_step = "Article generation stopped"
        task.error_message = message
        db.commit()
        raise TestingEndpointError(message, status_code=status_code) from exc


def rewrite_completed_article(
    db: Session,
    *,
    task: Task,
    original_text: str,
    transfer_strength: str = "maximum",
) -> dict[str, Any]:
    """Rewrite one completed article from an all-chunk reference compression."""
    _require_openai_credentials()
    source = normalize_manuscript_text(original_text)
    if len(source) < 500:
        raise TestingEndpointError("已完成文章正文至少需要 500 字。", status_code=400)
    if transfer_strength not in {"strong", "maximum"}:
        raise TestingEndpointError("迁移强度只能是 strong 或 maximum。", status_code=400)

    config = dict(task.config or {})
    testing = dict(config.get(TESTING_CONFIG_KEY) or {})
    reference_compression = str(testing.get("reference_compression") or "").strip()
    if not reference_compression:
        raise TestingEndpointError(
            "当前任务没有可用的参考书分片压缩结果，请重新上传参考书。",
            status_code=409,
        )

    original_sha256 = _text_sha256(source)

    task.status = TaskStatus.PROCESSING
    task.progress = 20
    task.current_step = "Generating one source-order-preserving candidate"
    task.error_message = None
    db.commit()

    new_calls: list[dict[str, Any]] = []
    source_organization_rules = _render_prompt(
        "testing_endpoint_source_organization_rules.md"
    ).strip()
    try:
        audit_notes = (
            "参考书全文已逐片读取并压缩；改写请求只使用压缩结果、完整原稿和信息组织规则。"
            "明确禁止模仿或复刻参考书作者的具体风格。"
        )
        candidate_text, rewrite_usage = _call_openai_testing(
            _render_prompt(
                "testing_endpoint_completed_article_rewrite.md",
                SOURCE_ORGANIZATION_RULES=source_organization_rules,
                REFERENCE_COMPRESSION=reference_compression,
                ORIGINAL_ARTICLE=source,
            ),
            action="testing_completed_article_rewrite",
            max_output_tokens=_max_output_tokens(),
        )
        candidate_text = _strip_model_wrappers(candidate_text)
        if len(candidate_text) < 400:
            raise TestingEndpointError("OpenAI 返回的全稿候选过短，已拒绝保存。", status_code=502)
        rewrite_usage["source_chars"] = len(source)
        rewrite_usage["candidate_chars"] = len(candidate_text)
        new_calls.append(rewrite_usage)

        surface_change_percent = round(_surface_change_ratio(source, candidate_text) * 100, 3)
        candidate_sha256 = _text_sha256(candidate_text)
        result: dict[str, Any] = {
            "task_id": str(task.id),
            "status": "rewrite_complete",
            "audit_notes": audit_notes,
            "candidate_text": candidate_text,
            "original_sha256": original_sha256,
            "candidate_sha256": candidate_sha256,
            "organization_policy_version": ORGANIZATION_POLICY_VERSION,
            "surface_change_percent": round(surface_change_percent, 3),
            "surface_change_method": "CJK four-gram multiset distance",
            "model": _testing_model(),
            "openai_calls": new_calls,
        }

        previous_experiments = list(testing.get("rewrite_experiments") or [])
        previous_experiments.append(
            {
                **result,
                "transfer_strength": transfer_strength,
                "original_text": source,
            }
        )
        testing.update(
            {
                "status": "rewrite_complete",
                "organization_policy_version": ORGANIZATION_POLICY_VERSION,
                "last_rewrite_experiment": previous_experiments[-1],
                # Bound JSON growth while still retaining controlled A/B history.
                "rewrite_experiments": previous_experiments[-3:],
                "openai_calls": [
                    *list(testing.get("openai_calls") or []),
                    *new_calls,
                ],
            }
        )
        config[TESTING_CONFIG_KEY] = testing
        task.config = config
        task.status = TaskStatus.COMPLETED
        task.progress = 100
        task.current_step = "Completed article experiment ready for real Zhuque A/B re-check"
        task.error_message = None
        db.commit()
        return result
    except Exception as exc:
        message, status_code = _testing_issue_message(exc)
        testing["status"] = "rewrite_failed"
        config[TESTING_CONFIG_KEY] = testing
        task.config = config
        task.status = TaskStatus.FAILED
        task.progress = 100
        task.current_step = "Completed article experiment stopped"
        task.error_message = message
        db.commit()
        raise TestingEndpointError(message, status_code=status_code) from exc


def record_zhuque_feedback(
    db: Session,
    *,
    task: Task,
    baseline_report: dict[str, Any],
    candidate_report: dict[str, Any],
) -> dict[str, Any]:
    """Persist one verified real-Zhuque observation for later local learning."""
    config = dict(task.config or {})
    testing = dict(config.get(TESTING_CONFIG_KEY) or {})
    experiment = dict(testing.get("last_rewrite_experiment") or {})
    if not experiment:
        raise TestingEndpointError("当前任务还没有可复检的全稿实验候选。", status_code=409)

    expected_original_hash = str(experiment.get("original_sha256") or "")
    expected_candidate_hash = str(experiment.get("candidate_sha256") or "")
    if str(baseline_report.get("original_sha256") or "") != expected_original_hash:
        raise TestingEndpointError("原稿朱雀报告与本次实验原稿哈希不匹配。", status_code=409)
    if str(candidate_report.get("original_sha256") or "") != expected_candidate_hash:
        raise TestingEndpointError("候选稿朱雀报告与本次实验候选哈希不匹配。", status_code=409)
    if not bool(baseline_report.get("source_match_verified")):
        raise TestingEndpointError("原稿朱雀报告未通过正文匹配校验。", status_code=409)
    if not bool(candidate_report.get("source_match_verified")):
        raise TestingEndpointError("候选稿朱雀报告未通过正文匹配校验。", status_code=409)
    if not bool(baseline_report.get("official_overview_verified")):
        raise TestingEndpointError("原稿报告首页官方三项比例未可靠识别，不能作为学习证据。", status_code=409)
    if not bool(candidate_report.get("official_overview_verified")):
        raise TestingEndpointError("候选报告首页官方三项比例未可靠识别，不能作为学习证据。", status_code=409)
    try:
        baseline_human = float(baseline_report["official_human_percent"])
        candidate_human = float(candidate_report["official_human_percent"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TestingEndpointError("报告缺少官方人工特征比例。", status_code=409) from exc

    observation = {
        "evidence_type": "real_zhuque_verified_pair",
        "original_sha256": expected_original_hash,
        "candidate_sha256": expected_candidate_hash,
        "baseline_report_sha256": baseline_report.get("report_sha256"),
        "candidate_report_sha256": candidate_report.get("report_sha256"),
        "baseline_report_storage_id": baseline_report.get("report_storage_id"),
        "candidate_report_storage_id": candidate_report.get("report_storage_id"),
        "baseline_human_percent": round(baseline_human, 6),
        "candidate_human_percent": round(candidate_human, 6),
        "human_delta_percent_points": round(candidate_human - baseline_human, 6),
        "surface_change_percent": experiment.get("surface_change_percent"),
        "model": experiment.get("model"),
        "transfer_strength": experiment.get("transfer_strength"),
    }
    observations = list(testing.get("verified_zhuque_observations") or [])
    # The report hash pair is idempotent.
    report_pair = (
        str(observation.get("baseline_report_sha256") or ""),
        str(observation.get("candidate_report_sha256") or ""),
    )
    if not any(
        (
            str(item.get("baseline_report_sha256") or ""),
            str(item.get("candidate_report_sha256") or ""),
        )
        == report_pair
        for item in observations
    ):
        observations.append(observation)
    testing["verified_zhuque_observations"] = observations
    testing["status"] = "verified_feedback_recorded"
    config[TESTING_CONFIG_KEY] = testing
    task.config = config
    task.status = TaskStatus.COMPLETED
    task.current_step = "Verified Zhuque A/B feedback recorded"
    task.error_message = None
    db.commit()
    return {
        "task_id": str(task.id),
        "status": "verified_feedback_recorded",
        "baseline_human_percent": round(baseline_human, 6),
        "candidate_human_percent": round(candidate_human, 6),
        "human_delta_percent_points": round(candidate_human - baseline_human, 6),
        "experiment_candidate_sha256": expected_candidate_hash,
        "verified_observation_count": len(observations),
    }


def uploaded_reference_book_from_bytes(filename: str, content: bytes) -> UploadedReferenceBook:
    text = _extract_uploaded_text(filename, content)
    if len(text) < 500:
        raise TestingEndpointError(f"{filename} 可读取正文过短，请上传完整参考书文件。", status_code=400)
    return UploadedReferenceBook(filename=filename, text=text, sha256=hashlib.sha256(content).hexdigest())


def testing_payload(task: Task) -> dict[str, Any]:
    testing = dict((task.config or {}).get(TESTING_CONFIG_KEY) or {})
    return {
        "task_id": str(task.id),
        "status": str(testing.get("status") or task.status),
        "current_step": task.current_step,
        "message": testing.get("analysis_message"),
        "article": testing.get("article"),
        "articles": testing.get("articles") or [],
        "error_message": task.error_message,
    }


def _build_reference_compression(
    books: list[UploadedReferenceBook],
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Read every source chunk and compress it without author-style imitation."""
    notes: list[str] = []
    calls: list[dict[str, Any]] = []
    chunk_count = 0
    for book in books:
        for index, chunk in enumerate(_split_text(book.text, _book_chunk_chars()), start=1):
            note, usage = _call_openai_testing(
                _render_prompt(
                    "testing_endpoint_reference_chunk_compression.md",
                    REFERENCE_TEXT=chunk,
                ),
                action="testing_reference_chunk_compression",
                max_output_tokens=_reference_note_max_tokens(),
            )
            chunk_count += 1
            usage.update({"source_filename": book.filename, "chunk_index": index})
            calls.append(usage)
            notes.append(f"[{book.filename} chunk {index}]\n{note}")

    compression, usage = _call_openai_testing(
        _render_prompt(
            "testing_endpoint_reference_compression_synthesis.md",
            REFERENCE_NOTES="\n\n".join(notes),
        ),
        action="testing_reference_compression_synthesis",
        max_output_tokens=_reference_compression_max_tokens(),
    )
    calls.append(usage)
    return compression, calls, {
        "book_count": len(books),
        "source_chars": sum(len(book.text) for book in books),
        "chunk_count": chunk_count,
        "chunk_chars": _book_chunk_chars(),
        "compressed_chars": len(compression),
        "raw_text_persisted": False,
        "specific_style_imitation_allowed": False,
    }


def _call_openai_testing(
    prompt: str,
    *,
    action: str,
    max_output_tokens: int | None,
) -> tuple[str, dict[str, Any]]:
    client = _testing_openai_client()
    model = _testing_model()
    initial_effort = _reasoning_effort_for_action(action)
    initial_max_tokens = _output_budget_for_action(action, max_output_tokens)
    attempts: list[dict[str, Any]] = []
    total_usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
    }
    response: Any | None = None
    text = ""
    effort = initial_effort
    output_budget = initial_max_tokens

    for attempt_number in (1, 2):
        kwargs: dict[str, Any] = {
            "model": model,
            "input": prompt,
            "reasoning": {"effort": effort},
            "text": {"verbosity": _verbosity()},
            # Never silently truncate an oversized synthesis or manuscript.
            "truncation": "disabled",
        }
        if output_budget and output_budget > 0:
            kwargs["max_output_tokens"] = output_budget

        logger.info(
            "LLM action=%s provider=openai model=%s reasoning=%s attempt=%d status=started max_output_tokens=%s",
            action,
            model,
            effort,
            attempt_number,
            output_budget or "model_default",
        )
        response = client.responses.create(**kwargs)
        response_usage = _openai_usage_payload(response)
        for key in total_usage:
            total_usage[key] += int(response_usage.get(key) or 0)
        text = _extract_openai_response_text(response).strip()
        status = str(getattr(response, "status", "") or "unknown")
        details = getattr(response, "incomplete_details", None)
        incomplete_reason = str(getattr(details, "reason", "") or "")
        attempts.append(
            {
                "attempt": attempt_number,
                "reasoning_effort": effort,
                "max_output_tokens": output_budget,
                "response_id": str(getattr(response, "id", "") or ""),
                "response_status": status,
                "incomplete_reason": incomplete_reason or None,
                "text_returned": bool(text),
                "usage": response_usage,
            }
        )
        logger.info(
            "LLM action=%s provider=openai model=%s reasoning=%s attempt=%d status=%s "
            "text_returned=%s input_tokens=%d output_tokens=%d reasoning_tokens=%d incomplete_reason=%s",
            action,
            model,
            effort,
            attempt_number,
            status,
            bool(text),
            response_usage["input_tokens"],
            response_usage["output_tokens"],
            response_usage["reasoning_tokens"],
            incomplete_reason or "none",
        )
        if text:
            break
        if attempt_number == 1:
            logger.warning(
                "OpenAI Testing Endpoint empty response; retrying once with a lower reasoning effort "
                "and a safer output budget: action=%s %s",
                action,
                _openai_response_debug_summary(response),
            )
            effort = _fallback_reasoning_effort(effort)
            output_budget = _empty_response_retry_budget(action, output_budget)

    if not text:
        last_attempt = attempts[-1]
        reason = str(last_attempt.get("incomplete_reason") or last_attempt.get("response_status") or "unknown")
        reasoning_tokens = int((last_attempt.get("usage") or {}).get("reasoning_tokens") or 0)
        raise TestingEndpointError(
            "OpenAI 未生成可用正文；系统已自动降低推理强度并重试一次仍为空。"
            f"状态原因：{reason}，末次推理 token：{reasoning_tokens}。请稍后重试；若持续出现，请检查模型配额。",
            status_code=502,
        )

    return text, {
        "provider": "openai",
        "model": model,
        "action": action,
        "status": "completed",
        "cost_cny": None,
        "cost_status": "pricing_not_configured",
        "response_id": str(getattr(response, "id", "") or ""),
        "usage": total_usage,
        "attempt_count": len(attempts),
        "attempts": attempts,
        "reference_input_mode": "prompt_text",
    }


def _testing_openai_client() -> OpenAI:
    request_timeout = httpx.Timeout(float(settings.TESTING_ENDPOINT_OPENAI_TIMEOUT_SECONDS), connect=30.0)
    kwargs: dict[str, Any] = {
        "api_key": _require_openai_credentials(),
        "base_url": official_openai_base_url(settings.OPENAI_BASE_URL),
        "timeout": request_timeout,
    }
    proxy = official_proxy("openai", settings.OPENAI_HTTPS_PROXY)
    if proxy:
        kwargs["http_client"] = httpx.Client(proxy=proxy, timeout=request_timeout)
    return OpenAI(**kwargs)


def _require_openai_credentials() -> str:
    api_key = (settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise TestingEndpointError("缺少 OpenAI API Key：请配置 OPENAI_API_KEY 后继续。", status_code=401)
    return api_key


def _resolve_video_text(video_url: str, *, raw_video_text: str | None) -> str:
    if raw_video_text and raw_video_text.strip():
        return raw_video_text.strip()
    try:
        return _fetch_video_text(
            video_url,
            {
                "category": "testing_endpoint",
                "youtube_transcript_providers": settings.TESTING_ENDPOINT_YOUTUBE_TRANSCRIPT_PROVIDERS,
            },
        ).strip()
    except Exception as exc:
        raise TestingEndpointError(
            "视频内容获取失败：请确认链接可公开访问，或提供登录/授权/cookie/可访问链接；"
            "也可以先提供该视频的文字稿后继续生成。"
            f"原始错误：{exc}",
            status_code=422,
        ) from exc


def _extract_uploaded_text(filename: str, content: bytes) -> str:
    suffix = Path(filename or "").suffix.lower()
    try:
        if suffix in {".txt", ".md", ".markdown"}:
            return content.decode("utf-8", errors="replace").strip()
        if suffix == ".docx":
            return extract_docx_text(content)
        if suffix == ".pdf":
            return extract_pdf_text(content)
        if suffix == ".epub":
            return extract_epub_text(content)
    except DocxTextError as exc:
        raise TestingEndpointError(str(exc), status_code=exc.status_code) from exc
    raise TestingEndpointError(f"暂不支持 {suffix or '未知'} 文件格式，请上传 txt/md/docx/pdf/epub。", status_code=400)


def extract_epub_text(content: bytes) -> str:
    if not content:
        raise TestingEndpointError("EPUB 文件为空", status_code=400)
    try:
        with zipfile.ZipFile(_bytes_io(content)) as archive:
            names = archive.namelist()
            ordered = _epub_spine_files(archive, names) or [
                name
                for name in names
                if name.lower().endswith((".xhtml", ".html", ".htm"))
                and not name.lower().startswith("meta-inf/")
            ]
            parts: list[str] = []
            for name in ordered:
                try:
                    raw = archive.read(name)
                except KeyError:
                    continue
                text = _html_to_text(raw.decode("utf-8", errors="replace"))
                if text:
                    parts.append(text)
    except zipfile.BadZipFile as exc:
        raise TestingEndpointError("EPUB 文件无法读取，请确认文件未损坏。", status_code=400) from exc
    result = "\n\n".join(parts).strip()
    if not result:
        raise TestingEndpointError("未能从 EPUB 中读取到正文，请换用可复制文字版本。", status_code=400)
    return result


def _bytes_io(content: bytes):
    from io import BytesIO

    return BytesIO(content)


def _epub_spine_files(archive: zipfile.ZipFile, names: list[str]) -> list[str]:
    try:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
    except Exception:
        return []
    rootfile = ""
    for item in container.iter():
        if item.tag.lower().endswith("rootfile"):
            rootfile = str(item.attrib.get("full-path") or "").strip()
            break
    if not rootfile:
        return []
    try:
        opf = ET.fromstring(archive.read(rootfile))
    except Exception:
        return []
    manifest: dict[str, str] = {}
    spine_ids: list[str] = []
    for item in opf.iter():
        tag = item.tag.lower()
        if tag.endswith("item"):
            item_id = str(item.attrib.get("id") or "").strip()
            href = str(item.attrib.get("href") or "").strip()
            if item_id and href:
                manifest[item_id] = href
        elif tag.endswith("itemref"):
            item_id = str(item.attrib.get("idref") or "").strip()
            if item_id:
                spine_ids.append(item_id)
    base = str(Path(rootfile).parent)
    if base == ".":
        base = ""
    ordered: list[str] = []
    for item_id in spine_ids:
        href = manifest.get(item_id)
        if not href:
            continue
        candidate = str(Path(base) / href) if base else href
        candidate = candidate.replace("\\", "/")
        if candidate in names:
            ordered.append(candidate)
    return ordered


class _HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "svg"}:
            self._skip_depth += 1
        if tag.lower() in {"p", "div", "section", "article", "br", "h1", "h2", "h3", "h4", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "svg"} and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag.lower() in {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "li"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        return _normalize_extracted_plain_text(" ".join(self.parts))


def _html_to_text(markup: str) -> str:
    parser = _HtmlTextExtractor()
    parser.feed(markup)
    return parser.text()


def _normalize_extracted_plain_text(text: str) -> str:
    text = re.sub(r"[ \t\f\v]+", " ", text)
    lines = [line.strip() for line in text.replace("\r", "\n").split("\n")]
    cleaned: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if cleaned and not blank:
                cleaned.append("")
                blank = True
            continue
        cleaned.append(line)
        blank = False
    return "\n".join(cleaned).strip()


def _clean_candidate_url(raw: str) -> str:
    return (raw or "").strip().rstrip("，。；;、！？!?.,)）]】")


def _normalize_candidate_urls(urls: list[str]) -> tuple[list[str], list[str]]:
    seen: set[str] = set()
    unique: list[str] = []
    duplicates: list[str] = []
    for raw in urls:
        url = _clean_candidate_url(raw)
        if not url:
            continue
        if url in seen:
            duplicates.append(url)
            continue
        seen.add(url)
        unique.append(url)
    return unique, duplicates


def _render_prompt(filename: str, **values: str) -> str:
    template = (PROMPT_DIR / filename).read_text(encoding="utf-8")
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _strip_model_wrappers(text: str) -> str:
    value = (text or "").strip()
    fenced = re.fullmatch(r"```(?:markdown|text)?\s*(.*?)\s*```", value, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    return value


def _surface_change_ratio(original: str, candidate: str) -> float:
    """Fast multiset distance suited to long Chinese manuscripts."""
    before = "".join(re.findall(r"[\u3400-\u9fff]", original))
    after = "".join(re.findall(r"[\u3400-\u9fff]", candidate))
    size = 4
    if len(before) < size or len(after) < size:
        return 1.0 if before != after else 0.0
    before_counts = Counter(before[index:index + size] for index in range(len(before) - size + 1))
    after_counts = Counter(after[index:index + size] for index in range(len(after) - size + 1))
    overlap = sum((before_counts & after_counts).values())
    denominator = max(sum(before_counts.values()), sum(after_counts.values()), 1)
    return round(1.0 - overlap / denominator, 6)


def _split_text(text: str, max_chars: int) -> list[str]:
    cleaned = "\n".join(
        line.rstrip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ).strip()
    if len(cleaned) <= max_chars:
        return [cleaned]
    chunks: list[str] = []
    start = 0
    while start < len(cleaned):
        end = min(len(cleaned), start + max_chars)
        chunks.append(cleaned[start:end].strip())
        start = end
    return [chunk for chunk in chunks if chunk]


def _limit_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip()


def _testing_model() -> str:
    return (settings.TESTING_ENDPOINT_OPENAI_MODEL or settings.OPENAI_WRITER_MODEL).strip()


def _reasoning_effort() -> str:
    return (settings.TESTING_ENDPOINT_OPENAI_REASONING_EFFORT or settings.OPENAI_WRITER_REASONING_EFFORT).strip()


def _reasoning_effort_for_action(action: str) -> str:
    configured = _reasoning_effort().lower() or "medium"
    if action in {
        "testing_reference_chunk_compression",
        "testing_reference_compression_synthesis",
        "testing_video_digest",
    } and configured in {"high", "xhigh"}:
        return "medium"
    return configured


def _fallback_reasoning_effort(current_effort: str) -> str:
    normalized = str(current_effort or "").strip().lower()
    return "low" if normalized != "low" else normalized


def _output_budget_for_action(action: str, requested: int | None) -> int | None:
    minimums = {
        "testing_reference_chunk_compression": 6000,
        "testing_reference_compression_synthesis": 8000,
    }
    minimum = minimums.get(action, 0)
    parsed = max(0, int(requested or 0))
    effective = max(minimum, parsed)
    return effective if effective > 0 else None


def _empty_response_retry_budget(action: str, current: int | None) -> int | None:
    minimums = {
        "testing_reference_chunk_compression": 8000,
        "testing_reference_compression_synthesis": 10000,
    }
    minimum = minimums.get(action, 0)
    parsed = max(0, int(current or 0))
    if not parsed and not minimum:
        return None
    return min(16_000, max(minimum, parsed * 2))


def _verbosity() -> str:
    return (settings.TESTING_ENDPOINT_OPENAI_VERBOSITY or settings.OPENAI_WRITER_VERBOSITY).strip()


def _book_chunk_chars() -> int:
    configured = max(10_000, int(settings.TESTING_ENDPOINT_BOOK_CHUNK_CHARS or 50_000))
    return min(50_000, configured)


def _reference_note_max_tokens() -> int:
    return int(settings.TESTING_ENDPOINT_STYLE_NOTE_MAX_OUTPUT_TOKENS or 2500)


def _reference_compression_max_tokens() -> int:
    return int(settings.TESTING_ENDPOINT_STYLE_PROFILE_MAX_OUTPUT_TOKENS or 4000)


def _video_max_chars() -> int:
    return max(10_000, int(settings.TESTING_ENDPOINT_VIDEO_MAX_CHARS or 160_000))


def _max_output_tokens() -> int | None:
    value = int(settings.TESTING_ENDPOINT_OPENAI_MAX_OUTPUT_TOKENS or 0)
    return value if value > 0 else None


def _testing_issue_message(exc: Exception) -> tuple[str, int]:
    if isinstance(exc, TestingEndpointError):
        return str(exc), exc.status_code
    message = f"{type(exc).__name__}: {exc}"
    lower = message.lower()
    if "api key" in lower or "authentication" in lower or "unauthorized" in lower or "401" in lower:
        return "OpenAI API 认证失败：请检查 OPENAI_API_KEY 是否有效，或账号是否需要重新授权。", 401
    if "billing" in lower or "quota" in lower or "insufficient" in lower or "429" in lower:
        return "OpenAI API 额度或账单异常：请检查账户余额、用量限制或付款状态后继续。", 402
    if "model" in lower and ("access" in lower or "not found" in lower or "permission" in lower):
        return "OpenAI 模型访问受限：请确认该账号有 Testing Endpoint 配置模型的访问权限。", 403
    if "context" in lower or "token" in lower or "too large" in lower:
        return "上传内容超过当前 OpenAI 模型或服务器配置可处理范围，请减少材料长度或调整模型/输出 token 配置。", 413
    logger.exception("Testing Endpoint failed")
    return f"Testing Endpoint 已中断：{message}", 502
