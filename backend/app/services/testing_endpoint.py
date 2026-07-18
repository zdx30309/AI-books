"""OpenAI-only workflow for the Testing Endpoint."""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
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
from app.services.content_engine import _extract_openai_response_text
from app.tasks.tasks import _fetch_video_text
from app.utils.docx_text import DocxTextError, extract_docx_text, extract_pdf_text
from app.utils.official_api import official_openai_base_url, official_proxy

logger = logging.getLogger(__name__)

PROMPT_DIR = PROJECT_ROOT / "backend" / "app" / "prompts"
TESTING_CONFIG_KEY = "testing_endpoint"
ANALYSIS_COMPLETE_MESSAGE = "Analysis Complete"


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
        current_step="Analyzing reference books with OpenAI ChatGPT API",
        config={
            "category": "testing_endpoint",
            "category_label": "Testing Endpoint",
            TESTING_CONFIG_KEY: {
                "status": "analyzing",
                "analysis_message": None,
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
        profile, calls = _build_style_profile(books)
        config = dict(task.config or {})
        testing = dict(config.get(TESTING_CONFIG_KEY) or {})
        testing.update(
            {
                "status": "analysis_complete",
                "analysis_message": ANALYSIS_COMPLETE_MESSAGE,
                "style_profile": profile,
                "openai_calls": [*list(testing.get("openai_calls") or []), *calls],
            }
        )
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
        task.current_step = "Reference book analysis stopped"
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
    style_profile = str(testing.get("style_profile") or "").strip()
    if not style_profile:
        raise TestingEndpointError("请先完成参考书的 OpenAI 风格分析。", status_code=409)
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
                    STYLE_PROFILE=style_profile,
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


def _build_style_profile(books: list[UploadedReferenceBook]) -> tuple[str, list[dict[str, Any]]]:
    notes: list[str] = []
    calls: list[dict[str, Any]] = []
    for book in books:
        for index, chunk in enumerate(_split_text(book.text, _book_chunk_chars()), start=1):
            note, usage = _call_openai_testing(
                _render_prompt("testing_endpoint_style_analysis.md", REFERENCE_TEXT=chunk),
                action="testing_style_analysis",
                max_output_tokens=_style_note_max_tokens(),
            )
            usage.update({"source_filename": book.filename, "chunk_index": index})
            calls.append(usage)
            notes.append(f"[{book.filename} chunk {index}]\n{note}")

    profile, usage = _call_openai_testing(
        _render_prompt("testing_endpoint_style_synthesis.md", STYLE_NOTES="\n\n".join(notes)),
        action="testing_style_synthesis",
        max_output_tokens=_style_profile_max_tokens(),
    )
    calls.append(usage)
    return profile, calls


def _call_openai_testing(prompt: str, *, action: str, max_output_tokens: int | None) -> tuple[str, dict[str, Any]]:
    client = _testing_openai_client()
    model = _testing_model()
    kwargs: dict[str, Any] = {
        "model": model,
        "input": prompt,
        "reasoning": {"effort": _reasoning_effort()},
        "text": {"verbosity": _verbosity()},
    }
    if max_output_tokens and max_output_tokens > 0:
        kwargs["max_output_tokens"] = max_output_tokens
    response = client.responses.create(**kwargs)
    text = _extract_openai_response_text(response).strip()
    if not text:
        raise TestingEndpointError("OpenAI ChatGPT API 返回空内容，请检查模型访问权限或稍后重试。", status_code=502)
    usage = getattr(response, "usage", None)
    usage_payload = usage.model_dump() if hasattr(usage, "model_dump") else usage
    return text, {
        "provider": "openai",
        "model": model,
        "action": action,
        "status": "completed",
        "response_id": str(getattr(response, "id", "") or ""),
        "usage": usage_payload,
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


def _split_text(text: str, max_chars: int) -> list[str]:
    cleaned = "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip()
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


def _verbosity() -> str:
    return (settings.TESTING_ENDPOINT_OPENAI_VERBOSITY or settings.OPENAI_WRITER_VERBOSITY).strip()


def _book_chunk_chars() -> int:
    return max(10_000, int(settings.TESTING_ENDPOINT_BOOK_CHUNK_CHARS or 80_000))


def _style_note_max_tokens() -> int:
    return int(settings.TESTING_ENDPOINT_STYLE_NOTE_MAX_OUTPUT_TOKENS or 2500)


def _style_profile_max_tokens() -> int:
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
