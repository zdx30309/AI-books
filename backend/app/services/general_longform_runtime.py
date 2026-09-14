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

from app.prompts.general_longform_prompts import render_stage


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


def parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        result = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise GeneralLongformReview("模型返回的 JSON 不完整，原始结果已留存，未推进下一阶段") from exc
    if not isinstance(result, dict):
        raise GeneralLongformReview("本节点需要 JSON 对象，原始结果已留存")
    return result


def _error_text(exc: BaseException) -> str:
    return re.sub(r"(?:sk-|AIza)[A-Za-z0-9_-]{12,}", "[redacted]", f"{type(exc).__name__}: {exc}")[:2000]


class ModelGateway:
    def __init__(self, directory: Path, request_id: str, *, check_active: Callable[[], None] | None = None, retry_uncertain: bool = False):
        self.directory = directory
        self.request_id = request_id
        self.check_active = check_active or (lambda: None)
        self.retry_uncertain = retry_uncertain

    def call(self, stage: str, context: dict[str, Any], *, provider: str = "openai", max_tokens: int = 12000, web: bool = False, slot: str = "") -> dict[str, Any]:
        from app.core.config import settings
        from app.services import content_engine, gemini
        from app.utils import paid_api_guard

        self.check_active()
        # Logical slots, not changing timestamps/research/state, identify paid
        # work. A resume always reuses the original persisted prompt/response.
        key = sha256(json.dumps([stage, slot], ensure_ascii=False).encode()).hexdigest()
        folder = self.directory / "calls" / self.request_id
        path = folder / f"{stage}_{key}.json"
        prompt_path = folder / f"{stage}_{key}.prompt.txt"
        existing = read_json(path, {})
        if existing.get("status") == "completed":
            return existing
        prompt = prompt_path.read_text(encoding="utf-8") if existing and prompt_path.exists() else render_stage(stage, context)
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
            status_code = getattr(exc, "status_code", None)
            record.update(status="failed" if (isinstance(status_code, int) and 400 <= status_code < 500) or isinstance(exc, GeneralLongformError) else "uncertain",
                          error=_error_text(exc), finished_at=now_iso())
            atomic_json(path, record)
            if isinstance(exc, Exception):
                paid_api_guard.trip_if_quota(provider, exc)
            raise
        atomic_json(path, record)
        self.check_active()
        return record

    def json(self, stage: str, context: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return parse_json(self.call(stage, context, **kwargs)["text"])


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
    abstracted: dict[str, list] = {"sources": [], "gaps": []}
    for index, group in enumerate(groups):
        page_ids = {page["id"] for page in group}
        part = gateway.json("evidence", {"proposed_sources": [source for source in source_info if source["id"] in page_ids], "pages": group}, max_tokens=10000, slot=f"{slot}-batch-{index}")
        if not isinstance(part.get("sources"), list) or not isinstance(part.get("gaps", []), list):
            raise GeneralLongformReview("来源摘要的结构不完整")
        abstracted["sources"].extend(part["sources"])
        abstracted["gaps"].extend(part.get("gaps", []))
    by_id = {item["id"]: item for item in pages}
    originals = {item["id"]: item for item in source_info}
    approved = {}
    for source in abstracted.get("sources") or []:
        sid = source.get("id") if isinstance(source, dict) else None
        if not isinstance(sid, str) or sid not in by_id or canonical_url(str(source.get("url", ""))) != canonical_url(by_id[sid]["url"]):
            continue
        if not isinstance(source.get("approved_summary"), str) or not source["approved_summary"].strip() or not isinstance(source.get("supported_claims"), list) or not source["supported_claims"] or not isinstance(source.get("bibliography"), str) or not source["bibliography"].strip():
            continue
        approved[sid] = {key: value for key, value in {**originals[sid], **source}.items() if key in {
            "id", "url", "title", "authors", "year", "publication", "doi", "bibliography", "approved_summary", "supported_claims", "limitations"}}
        approved[sid].update({key: by_id[sid][key] for key in ("read_scope", "retrieved_at", "file_sha256", "text_sha256")})
        approved[sid]["verification"] = "page_read_and_abstracted"
        approved[sid].update(provider="openai_web_search+public_page_reader", query=proposed.get("search_summary") or proposed.get("topic", ""),
                             local_path=str((directory / f"{sid}.json").relative_to(gateway.directory)), status="imported", error=None, generated_card_count=0)
    if not approved:
        raise GeneralLongformReview("实际读取材料未形成可靠的来源摘要，未开始正文")
    result = {"topic": proposed.get("topic", ""), "domain": proposed.get("domain", ""), "sources": approved,
              "gaps": abstracted.get("gaps", []), "errors": errors, "web_call_completed": True,
              "rawTextIncluded": False, "retrieved_at": now_iso()}
    atomic_json(directory / "approved.json", result)
    return result
