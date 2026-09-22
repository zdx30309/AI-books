"""Reusable writer/provider adapters, durable call journal and bounded research."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
from io import BytesIO
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit, urlunsplit
import uuid

import httpx

from app.prompts.general_longform_prompts import get_manifest, render_stage


class GeneralLongformError(ValueError):
    pass


class GeneralLongformReview(GeneralLongformError):
    """Persist the candidate, stop serial advancement, and show the exact issue."""


class GeneralLongformPaused(GeneralLongformError):
    """Stop at a durable boundary without marking the manuscript invalid."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep atomic temporary names short on Windows even for hash-keyed journals.
    temp = path.with_name(f".atomic-{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def atomic_json(path: Path, data: Any) -> None:
    atomic_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path, default: Any = None) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


JSON_INCOMPLETE = "模型返回的 JSON 不完整，原始结果已留存，未推进下一阶段"
JSON_OBJECT_ONLY = "本节点需要 JSON 对象，原始结果已留存"


def _strip_json_fence(text: str) -> str:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.IGNORECASE)
    return value.strip()


def _close_open_brackets(text: str) -> str | None:
    """Close an unterminated object at its last complete value; never invent content."""
    stack: list[str] = []
    in_string = escaped = False
    safe_end = 0
    string_start = 0
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
                safe_end = index + 1
            continue
        if char == '"':
            in_string = True
            string_start = index
        elif char in "{[":
            stack.append(char)
            safe_end = index + 1
        elif char in "}]":
            if stack:
                stack.pop()
            safe_end = index + 1
        elif char in ",:":
            safe_end = index + 1
        elif not char.isspace():
            safe_end = index + 1
    if not stack:
        return None
    if in_string:
        # A cut inside a string drops that one incomplete element, never the
        # complete ones before it.
        boundary = text.rfind(",", 0, string_start)
        if boundary < 0:
            return None
        safe_end = boundary
    body = re.sub(r"[,:]\s*$", "", text[:safe_end].rstrip())
    if not body or body[-1] in "{[,":
        # Closing here would invent an empty object/array that the model never
        # produced; leave the payload unusable so the caller can re-ask.
        return None
    for opener in reversed(stack):
        body += "}" if opener == "{" else "]"
    return body if body.strip() not in {"", "{}", "[]"} else None


def _json_repair_candidates(text: str) -> list[str]:
    """Repair JSON framing only: framing noise, trailing prose, trailing commas, cut tails."""
    candidates: list[str] = []

    def push(value: str) -> None:
        value = value.strip()
        if value and value not in candidates:
            candidates.append(value)

    push(text)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0:
        push(text[start:end + 1] if end > start else text[start:])
    for value in list(candidates):
        push(re.sub(r",\s*([}\]])", r"\1", value))
    # A cut tail keeps almost the whole payload; a two-character stub is not a repair.
    for value in list(candidates):
        closed = _close_open_brackets(value)
        # A repair must keep a substantial object: refuse two-character stubs
        # that would silently turn a broken reply into an empty verdict.
        if closed and len(closed) >= 40 and len(closed) >= int(len(value) * 0.4):
            push(closed)
    return candidates


def parse_json_detail(text: str) -> tuple[dict[str, Any], bool]:
    """Return the model JSON object plus whether framing (not prose) had to be repaired."""
    fenced = _strip_json_fence(text or "")
    for index, candidate in enumerate(_json_repair_candidates(fenced)):
        try:
            value = json.loads(candidate)
        except (TypeError, ValueError):
            value = None
        if isinstance(value, dict):
            return value, index > 0
    decoder = json.JSONDecoder()
    start = fenced.find("{")
    if start >= 0:
        try:
            value, _end = decoder.raw_decode(fenced[start:])
        except ValueError:
            value = None
        if isinstance(value, dict):
            return value, True
    text_check = (text or "").strip()
    if text_check.startswith("{") or text_check.startswith("```"):
        raise GeneralLongformReview(JSON_INCOMPLETE)
    raise GeneralLongformReview(JSON_OBJECT_ONLY)


def parse_json(text: str) -> dict[str, Any]:
    return parse_json_detail(text)[0]


def _error_text(exc: BaseException) -> str:
    return re.sub(r"(?:sk-|AIza)[A-Za-z0-9_-]{12,}", "[redacted]", f"{type(exc).__name__}: {exc}")[:2000]


_PROVIDER_FAILURE_STATUS = re.compile(r"status=(failed|incomplete|cancelled|expired)")


def provider_failure_status(exc: BaseException) -> str:
    """Which terminal state the provider itself reported, when it reported one.

    A response the provider ended as failed/incomplete/cancelled carries no
    deliverable content, so after the author confirms the retry this paid slot
    may issue exactly one replacement request. A polling interruption does not
    match here, so a resume still never re-POSTs a response that may exist.
    """
    match = _PROVIDER_FAILURE_STATUS.search(str(exc))
    return match.group(1) if match else ""


class ModelGateway:
    def __init__(self, directory: Path, request_id: str, *, check_active: Callable[[], None] | None = None, retry_uncertain: bool = False,
                 prompt_version: str | None = None):
        self.directory = directory
        self.request_id = request_id
        self.check_active = check_active or (lambda: None)
        self.retry_uncertain = retry_uncertain
        self.prompt_version = prompt_version

    def call(self, stage: str, context: dict[str, Any], *, provider: str = "openai", max_tokens: int = 12000, web: bool = False, slot: str = "") -> dict[str, Any]:
        from app.core.config import settings
        from app.services import content_engine, gemini
        from app.utils import paid_api_guard

        self.check_active()
        # Logical slots, not changing timestamps/research/state, identify paid
        # work. A resume always reuses the original persisted prompt/response.
        key = sha256(json.dumps([stage, slot], ensure_ascii=False).encode()).hexdigest()
        folder = self.directory / "calls" / self.request_id
        path = self.record_path(stage, slot)
        prompt_path = folder / f"{stage}_{key}.prompt.txt"
        existing = read_json(path, {})
        if existing.get("status") == "completed":
            return existing
        prompt = prompt_path.read_text(encoding="utf-8") if existing and prompt_path.exists() else render_stage(stage, context, version=self.prompt_version)
        if len(prompt) > 350_000:
            raise GeneralLongformReview(f"本节点上下文为 {len(prompt)} 字符，超过 350000 限额；未截断原文或前文")
        response_id = existing.get("response_id")
        model = (existing.get("model") if response_id else None) or (settings.GEMINI_MODEL if provider == "gemini" else settings.OPENAI_WRITER_MODEL)
        if existing.get("status") in {"started", "uncertain"} and not response_id and not self.retry_uncertain:
            raise GeneralLongformReview("上次付费调用交付状态待核对；已保留断点，请明确确认后重试，避免重复扣费")
        if existing.get("response_status") in {"incomplete", "failed", "cancelled"}:
            if not self.retry_uncertain:
                raise GeneralLongformReview("上次来源检索未完整交付；请核对调用记录后明确确认重新请求")
            response_id = None
        record = {"action": stage, "provider": provider, "model": model, "request_id": self.request_id,
                  "prompt_version": existing.get("prompt_version") or get_manifest(self.prompt_version)["version"],
                  "prompt_source_sha256": existing.get("prompt_source_sha256") or get_manifest(self.prompt_version)["source_sha256"],
                  "slot": slot, "status": "started", "started_at": now_iso(), "prompt_sha256": sha256(prompt.encode()).hexdigest(),
                  "prompt_characters": len(prompt), "estimated_cost": None, "error": None,
                  "attempt": int(existing.get("attempt", 0)) + 1, "response_id": response_id}
        if existing:
            atomic_json(folder / f"{stage}_{key}.attempt-{int(existing.get('attempt', 1))}.history", existing)
        atomic_text(prompt_path, prompt)
        atomic_json(path, record)

        def remember(value: str) -> None:
            record["response_id"] = value
            atomic_json(path, record)

        try:
            if web:
                paid_api_guard.guard("openai")
                client = content_engine._get_openai_writer_client().with_options(max_retries=0)
                from app.utils.distributed_limiter import distributed_semaphore
                with distributed_semaphore("openai-writer", limit=settings.OPENAI_WRITER_MAX_CONCURRENT_CALLS):
                    response = client.responses.retrieve(response_id) if response_id else client.responses.create(
                        model=model, input=prompt, reasoning={"effort": "low"},
                        tools=[{"type": "web_search", "search_context_size": "high"}],
                        tool_choice="required", include=["web_search_call.action.sources"], max_output_tokens=max_tokens,
                    )
                if getattr(response, "id", None):
                    remember(response.id)
                text = content_engine._extract_openai_response_text(response)
                record["web_calls"] = [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in (response.output or [])]
                usage = response.usage.model_dump() if getattr(response, "usage", None) else {}
                record.update(response_status=getattr(response, "status", "completed"), text=text, usage=usage)
                if record["response_status"] != "completed":
                    raise GeneralLongformReview("联网检索响应未完成，保留已收到内容")
            elif provider == "gemini":
                text = gemini.generate_text(prompt, model=model, max_output_tokens=max_tokens, retries=1)
                usage = {"input_tokens": math.ceil(len(prompt) / 2), "output_tokens": math.ceil(len(text) / 2), "estimated": True}
            else:
                # This is the same short-form Chapter Draft Writer, not another
                # SDK stack. Background response IDs survive worker restarts.
                text, usage = content_engine._openai_writer_call_result(
                    prompt, max_output_tokens=max_tokens, reasoning_effort="low", verbosity="high",
                    background=True, background_response_id=response_id,
                    on_background_response_id=remember, retry_on_failure=False,
                    prompt_cache_key=f"general-longform-{self.directory.parent.name}",
                )
            if not str(text or "").strip():
                raise GeneralLongformReview("模型未返回有效内容，未推进下一阶段")
            record.update(text=text, usage=usage, status="completed", finished_at=now_iso())
            input_rate = float(getattr(settings, "LONG_NOVEL_GEMINI_INPUT_COST_PER_1M" if provider == "gemini" else "LONG_NOVEL_INPUT_COST_PER_1M", 0) or 0)
            output_rate = float(getattr(settings, "LONG_NOVEL_GEMINI_OUTPUT_COST_PER_1M" if provider == "gemini" else "LONG_NOVEL_OUTPUT_COST_PER_1M", 0) or 0)
            record["estimated_cost"] = round((int(usage.get("input_tokens", 0)) * input_rate + int(usage.get("output_tokens", 0)) * output_rate) / 1_000_000, 8) if input_rate or output_rate else None
            record["cost_status"] = "estimated" if record["estimated_cost"] is not None else "rate_not_configured"
            record["cost_scope"] = "configured_model_token_estimate_excludes_web_tool_charges"
        except BaseException as exc:
            status_code, error_code = content_engine._openai_error_details(exc)
            # A 401 on a fresh POST is rejected, not delivery-uncertain. A 401
            # while retrieving an existing response does NOT erase that paid
            # response or authorize a replacement POST. HTTP 408 stays uncertain.
            rejected = (
                not record.get("response_id")
                and not isinstance(exc, (content_engine.OpenAIDeliveryUncertainError, content_engine.GeminiDeliveryUncertainError))
                and isinstance(status_code, int) and 400 <= status_code < 500 and status_code != 408
            )
            error = _error_text(exc)
            if provider == "openai" and status_code in {401, 403} and not isinstance(exc, content_engine.OpenAIDeliveryUncertainError):
                error = content_engine._openai_writer_connection_error_message(exc)
            record.update(status="failed" if rejected or isinstance(exc, GeneralLongformError) else "uncertain",
                          error=error, http_status=status_code, error_code=error_code, finished_at=now_iso())
            # Record the provider's own terminal state so an explicitly confirmed
            # retry can clear the unusable response id and send exactly one
            # replacement request, instead of re-reading the same dead response.
            provider_status = provider_failure_status(exc)
            if provider_status:
                record["response_status"] = provider_status
            if rejected:
                record["failure_kind"] = "authentication" if status_code in {401, 403} else "request_rejected"
                record["delivery_status"] = "rejected_before_generation"
                record["estimated_cost"] = 0
                record["cost_status"] = "request_rejected_no_generation"
            atomic_json(path, record)
            if isinstance(exc, Exception):
                paid_api_guard.trip_if_quota(provider, exc)
                if provider == "openai" and rejected and not isinstance(exc, content_engine.OpenAIRequestRejectedError):
                    raise content_engine._openai_writer_failure(exc) from exc
            raise
        atomic_json(path, record)
        self.check_active()
        return record

    def json(self, stage: str, context: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        record = self.call(stage, context, **kwargs)
        parsed, repaired = parse_json_detail(record["text"])
        if repaired and not record.get("parse_repaired"):
            # Framing-only repair: the paid payload is reused instead of being
            # re-requested, and the trail says exactly what was repaired.
            record["parse_repaired"] = True
            record["parse_repair_note"] = "只修复 JSON 框架（尾随字符/尾逗号/未闭合括号），未改写任何正文或结论"
            atomic_json(self.record_path(stage, str(kwargs.get("slot") or "")), record)
        return parsed

    def record_path(self, stage: str, slot: str = "") -> Path:
        key = sha256(json.dumps([stage, slot], ensure_ascii=False).encode()).hexdigest()
        return self.directory / "calls" / self.request_id / f"{stage}_{key}.json"


def canonical_url(url: str) -> str:
    split = urlsplit(url)
    return urlunsplit((split.scheme.lower(), split.netloc.lower(), split.path or "/", split.query, ""))


def validate_public_url(url: str) -> list[str]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise GeneralLongformError("来源必须是无凭据的公开 HTTP(S) 页面")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise GeneralLongformError("来源端口不合法") from exc
    if port not in {80, 443}:
        raise GeneralLongformError("来源只读取公开网页端口")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise GeneralLongformError("来源域名解析失败") from exc
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise GeneralLongformError("来源地址不是公开网络地址")
    return list(dict.fromkeys(item[4][0] for item in addresses))


class _PageText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in {"script", "style", "noscript"}:
            self.skip += 1
        if tag in {"p", "div", "br", "h1", "h2", "h3", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"}:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def fetch_public_page(url: str) -> dict[str, Any]:
    """Bounded, explicit source reader: max 8 sources per research run, no crawl."""
    initial = url
    with httpx.Client(timeout=25, follow_redirects=False, headers={"User-Agent": "GeneralLongformSourceReader/1.0"}) as client:
        for _ in range(4):
            addresses = validate_public_url(url)
            parsed = urlsplit(url)
            address = next((value for value in addresses if ":" not in value), addresses[0])
            authority = f"[{address}]" if ":" in address else address
            if parsed.port:
                authority += f":{parsed.port}"
            pinned = urlunsplit((parsed.scheme, authority, parsed.path or "/", parsed.query, ""))
            # Pin the validated public address through the actual connection,
            # retaining Host and TLS SNI; no second DNS lookup/rebinding window.
            with client.stream("GET", pinned, headers={"Host": parsed.netloc}, extensions={"sni_hostname": parsed.hostname}) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get("location", ""))
                    continue
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 6_000_000:
                        raise GeneralLongformError("来源超过单页 6 MB 限额，已停止读取")
                    chunks.append(chunk)
                data = b"".join(chunks)
                content_type = response.headers.get("content-type", "")
                encoding = response.encoding or "utf-8"
                break
        else:
            raise GeneralLongformError("来源重定向超过 3 次")
    if "pdf" in content_type or data.startswith(b"%PDF-"):
        import pdfplumber
        with pdfplumber.open(BytesIO(data)) as pdf:
            count = min(40, len(pdf.pages))
            text = "\n".join(page.extract_text() or "" for page in pdf.pages[:count])
            scope = f"实际读取 PDF 第 1—{count} 页，共 {len(pdf.pages)} 页"
    elif "html" in content_type or b"<html" in data[:1000].lower():
        parser = _PageText()
        parser.feed(data.decode(encoding, errors="replace"))
        text = "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())
        scope = "实际读取网页可见文字；不含脚本和受限内容"
    elif content_type.startswith("text/"):
        text = data.decode(encoding, errors="replace")
        scope = "实际读取文本页面"
    else:
        raise GeneralLongformError("来源不是可读取的 HTML、文本或 PDF")
    if len(text.strip()) < 80:
        raise GeneralLongformError("来源实际可读内容不足，未算作已核实")
    total = len(text)
    if total > 60000:
        text = text[:60000]
        scope += f"；正文共 {total} 字符，本次仅前 60000 字符，属于部分读取"
    return {"url": url, "requested_url": initial, "text": text, "read_scope": scope,
            "retrieved_at": now_iso(), "file_sha256": sha256(data).hexdigest(), "text_sha256": sha256(text.encode()).hexdigest(), "bytes": len(data)}


def _tool_source_urls(record: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    completed = False
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("url"), str):
                try:
                    result.add(canonical_url(value["url"]))
                except ValueError:
                    pass
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    for item in record.get("web_calls", []):
        if isinstance(item, dict) and item.get("type") == "web_search_call" and item.get("status") == "completed":
            completed = True
            walk(item.get("action", {}))
        elif isinstance(item, dict) and item.get("type") == "message":
            for part in item.get("content", []):
                walk(part.get("annotations", []))
    if not completed:
        raise GeneralLongformReview("没有实际完成的联网工具记录，未把模型自述算作联网")
    return result


# A source abstraction that drops every entry is retried against the pages this
# book already paid to read: the failure reason list is passed back as data, the
# stage prompt is untouched, and the acceptance rule for an approved source is
# byte-for-byte the same. Total attempts per research slot stay bounded so a
# hopeless retrieval cannot spend without limit.
_EVIDENCE_ATTEMPT_CAP = 3
_EVIDENCE_RETRY_NOTE = "上一次返回的条目未通过核验，已附上被排除的条目与原因；请沿用实际读取页面的 id 与 url，补齐被拒条目缺失的字段，不要新增未阅读的来源。"


def evidence_retry_attempts() -> int:
    """Extra abstraction attempts inside one run (default 1)."""
    raw = os.environ.get("GENERAL_LONGFORM_EVIDENCE_RETRY_ATTEMPTS", "1")
    try:
        return max(0, min(2, int(str(raw).strip() or "0")))
    except (TypeError, ValueError):
        return 1


def _completed_evidence_attempts(gateway: ModelGateway, slot: str) -> int:
    """Count abstraction attempts already journaled for this research slot.

    Reading the gateway journal instead of storing a counter keeps a resumed
    request honest: a replay continues at the next attempt slot, so every resume
    costs at most one abstraction call and never repeats the web search.
    """
    folder = gateway.directory / "calls" / gateway.request_id
    pattern = re.compile(re.escape(slot) + r"(-evidence-retry-(\d+))?-batch-\d+$")
    attempts = set()
    if folder.is_dir():
        for path in folder.glob("evidence_*.json"):
            record = read_json(path, {})
            if record.get("status") != "completed":
                continue
            match = pattern.match(str(record.get("slot") or ""))
            if match:
                attempts.add(int(match.group(2) or 0))
    return len(attempts)


def approve_abstracted_sources(payload: dict[str, Any], pages: dict[str, dict[str, Any]],
                               originals: dict[str, dict[str, Any]], *, directory: Path, project_dir: Path,
                               query: str) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Accept only entries that describe a page this run really read.

    Returns the approved map plus a deterministic reason list for every dropped
    entry, so a retry (and the user-facing error) can name the actual gap. The
    acceptance rule is exactly the previous inline filter.
    """
    approved: dict[str, dict[str, Any]] = {}
    dropped: list[dict[str, Any]] = []
    for source in payload.get("sources") or []:
        if not isinstance(source, dict):
            dropped.append({"id": None, "url": None, "reason": "条目不是对象"})
            continue
        sid = source.get("id")
        if not isinstance(sid, str) or sid not in pages:
            dropped.append({"id": sid if isinstance(sid, str) else None, "url": source.get("url"), "reason": "id 不匹配任何实际读取的页面"})
            continue
        if canonical_url(str(source.get("url", ""))) != canonical_url(pages[sid]["url"]):
            dropped.append({"id": sid, "url": source.get("url"), "reason": "url 与实际读取的页面不一致"})
            continue
        missing = next((field for field, kind in (("approved_summary", str), ("supported_claims", list), ("bibliography", str))
                        if not isinstance(source.get(field), kind)
                        or (isinstance(source.get(field), str) and not source[field].strip())
                        or (isinstance(source.get(field), list) and not source[field])), None)
        if missing:
            dropped.append({"id": sid, "url": source.get("url"), "reason": f"{missing} 缺失或为空"})
            continue
        entry = {key: value for key, value in {**originals[sid], **source}.items() if key in {
            "id", "url", "title", "authors", "year", "publication", "doi", "bibliography", "approved_summary", "supported_claims", "limitations"}}
        entry.update({key: pages[sid][key] for key in ("read_scope", "retrieved_at", "file_sha256", "text_sha256")})
        entry["verification"] = "page_read_and_abstracted"
        entry.update(provider="openai_web_search+public_page_reader", query=query or "",
                     local_path=str((directory / f"{sid}.json").relative_to(project_dir)), status="imported",
                     error=None, generated_card_count=0)
        approved[sid] = entry
    return approved, dropped


def evidence_failure_message(pages_read: int, dropped: list[dict[str, Any]], exhausted: bool) -> str:
    reasons = "；".join(str(item.get("reason")) for item in dropped[:6] if isinstance(item, dict))
    detail = f"；{len(dropped)} 个条目未通过核验（{reasons}）" if dropped else ""
    tail = ("；本轮来源抽象尝试已达上限，需要完成一次新的联网检索才能继续"
            if exhausted else "；已保留检索与已读页面，重试本阶段会复用它们而不重新联网")
    return f"实际读取材料未形成可靠的来源摘要，未开始正文（已实际读取 {pages_read} 个页面{detail}）{tail}"


def research(gateway: ModelGateway, context: dict[str, Any], *, fetch: Callable[[str], dict[str, Any]] = fetch_public_page, slot: str = "") -> dict[str, Any]:
    directory = gateway.directory / "research" / gateway.request_id / (sha256(slot.encode()).hexdigest()[:16] if slot else "plan")
    cached = read_json(directory / "approved.json")
    if cached:
        return cached
    call = gateway.call("research", context, web=True, max_tokens=8000, slot=slot)
    proposed = parse_json(call["text"])
    allowed = _tool_source_urls(call)
    pages, source_info, errors = [], [], []
    proposed_sources = proposed.get("sources")
    if not isinstance(proposed_sources, list):
        raise GeneralLongformReview("联网检索缺少结构化来源列表")
    for source in proposed_sources[:8]:
        if not isinstance(source, dict):
            continue
        url = str(source.get("url") or "")
        try:
            normalized_url = canonical_url(url)
        except ValueError:
            errors.append({"url": url, "error": "来源 URL 格式不合法"})
            continue
        if normalized_url not in allowed:
            errors.append({"url": url, "error": "URL 没有实际搜索或引用工具证据"})
            continue
        sid = "S" + sha256(normalized_url.encode()).hexdigest()[:12]
        page_path = directory / f"{sid}.json"
        try:
            page = read_json(page_path)
            if page is None:
                gateway.check_active()
                page = fetch(url)
                page["id"] = sid
                atomic_json(page_path, page)
            if not page.get("text") or not page.get("file_sha256"):
                raise GeneralLongformError("来源读取记录缺少正文或文件哈希")
            pages.append(page)
            source_info.append({**source, "id": sid, "url": page["url"]})
        except (httpx.HTTPError, OSError, ValueError) as exc:
            errors.append({"url": url, "error": _error_text(exc)})
    if not pages:
        atomic_json(directory / "errors.json", errors)
        raise GeneralLongformReview("联网来源均未成功读取，已保留检索与错误；补充来源后再继续")
    # No raw-page truncation to satisfy the model window: group complete,
    # explicitly scoped page reads into bounded abstraction calls instead.
    groups, group, size = [], [], 0
    for page in pages:
        if group and size + len(page["text"]) > 180000:
            groups.append(group)
            group, size = [], 0
        group.append(page)
        size += len(page["text"])
    if group:
        groups.append(group)
    by_id = {item["id"]: item for item in pages}
    originals = {item["id"]: item for item in source_info}
    query = proposed.get("search_summary") or proposed.get("topic", "")
    used = _completed_evidence_attempts(gateway, slot)
    budget = max(0, min(_EVIDENCE_ATTEMPT_CAP, used + evidence_retry_attempts() + 1) - used)
    abstracted: dict[str, list] = {"sources": [], "gaps": []}
    approved: dict[str, dict[str, Any]] = {}
    dropped = read_json(directory / "rejected.json", []) or []
    for step in range(budget):
        attempt = used + step
        rejected = dropped if attempt else []
        abstracted = {"sources": [], "gaps": []}
        for index, group in enumerate(groups):
            page_ids = {page["id"] for page in group}
            part_context: dict[str, Any] = {"proposed_sources": [source for source in source_info if source["id"] in page_ids], "pages": group}
            if rejected:
                part_context.update(rejected_entries=rejected, rejection_note=_EVIDENCE_RETRY_NOTE)
            suffix = f"-evidence-retry-{attempt}" if attempt else ""
            part = gateway.json("evidence", part_context, max_tokens=10000, slot=f"{slot}{suffix}-batch-{index}")
            if not isinstance(part.get("sources"), list) or not isinstance(part.get("gaps", []), list):
                raise GeneralLongformReview("来源摘要的结构不完整")
            abstracted["sources"].extend(part["sources"])
            abstracted["gaps"].extend(part.get("gaps", []))
        approved, dropped = approve_abstracted_sources(abstracted, by_id, originals, directory=directory,
                                                      project_dir=gateway.directory, query=query)
        atomic_json(directory / "rejected.json", dropped)
        if approved:
            break
    if not approved:
        raise GeneralLongformReview(evidence_failure_message(len(pages), dropped, budget == 0 or used + budget >= _EVIDENCE_ATTEMPT_CAP))
    result = {"topic": proposed.get("topic", ""), "domain": proposed.get("domain", ""), "sources": approved,
              "gaps": abstracted.get("gaps", []), "errors": errors, "web_call_completed": True,
              "rawTextIncluded": False, "retrieved_at": now_iso()}
    atomic_json(directory / "approved.json", result)
    return result
