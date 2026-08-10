"""Reference corpus, downloader, RAG, and retrieval planning services."""
from __future__ import annotations

import csv
import hashlib
import html
import json
import os
import re
import shutil
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, build_opener
from uuid import UUID, uuid4

from sqlalchemy import func, or_
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT, settings
from app.models import (
    EpicRetrievalTrace,
    ReferenceBook,
    ReferenceBooklistItem,
    ReferenceDownloadStatus,
    ReferenceDownloadTask,
    ReferenceIngestionJob,
    ReferenceMechanismProfile,
    ReferencePatternCard,
    ReferenceSection,
    ReferenceSectionAnalysis,
)
from app.prompts.epic_novel_prompts import get_epic_prompt
from app.services.epic_model_router import call_epic_model, estimate_tokens, log_epic_generation
from app.services.epic_storage import (
    compute_file_hash,
    ensure_epic_storage,
    epic_run_artifact_path,
    reference_processed_path,
    reference_raw_path,
    resolve_repo_path,
    tier_folder,
)


NO_PROXY_OPENER = build_opener(ProxyHandler({}))


SUPPORTED_SEED_PATHS = [
    ".local/novel_ref_corpus/booklist_seed.md",
    ".local/novel_ref_corpus/booklist.csv",
    ".local/novel_ref_corpus/booklist.json",
    "docs/epic_novel/reference_booklist_seed.md",
    "docs/epic_novel/reference_booklist.csv",
    "docs/epic_novel/reference_booklist.json",
]

REFERENCE_CARD_CATEGORIES = [
    "pleasure",
    "plot_structure",
    "character_arc",
    "heroine_arc",
    "villain_arc",
    "foreshadow",
    "volume_structure",
    "power_system",
    "bureaucracy",
    "faction",
    "map",
    "worldbuilding",
    "anti_cliche",
    "longform_pacing",
]

GATE2A_ANALYSIS_KEYS = [
    "sectionSummary",
    "plotFunction",
    "pleasureFunction",
    "characterArcMove",
    "heroineAgencyMove",
    "mysteryClueForeshadowActions",
    "bureaucracyCourtFactionFunction",
    "powerSystemMove",
    "pacingRole",
    "antiClicheNotes",
    "reusableMechanisms",
    "notToCopyNotes",
]

GATE2A_FORBIDDEN_ENGLISH_LABELS = [
    "Pleasure Pattern",
    "reader reward rhythm",
    "case-to-arc escalation",
    "institutional obstacle",
]

REFERENCE_CATEGORY_LABELS = {
    "pleasure": "读者爽点",
    "plot_structure": "情节结构",
    "character_arc": "人物弧线",
    "heroine_arc": "女性能动性",
    "villain_arc": "反派压力",
    "foreshadow": "伏笔回收",
    "volume_structure": "分卷节奏",
    "power_system": "战力推进",
    "bureaucracy": "官僚阻力",
    "faction": "势力博弈",
    "map": "地图功能",
    "worldbuilding": "世界规则",
    "anti_cliche": "反套路",
    "longform_pacing": "长篇节奏",
}


class EpicReferenceError(RuntimeError):
    """Raised for expected reference workflow errors."""


def _is_retryable_disconnect(exc: OperationalError) -> bool:
    if getattr(exc, "connection_invalidated", False):
        return True
    message = str(getattr(exc, "orig", exc)).lower()
    return any(
        marker in message
        for marker in (
            "ssl connection has been closed unexpectedly",
            "server closed the connection unexpectedly",
            "connection not open",
            "connection refused",
            "connection reset",
            "connection was closed",
        )
    )


def _reset_db_session(db: Session) -> None:
    try:
        db.rollback()
    except Exception:
        pass
    invalidate = getattr(db, "invalidate", None)
    if callable(invalidate):
        try:
            invalidate()
            return
        except Exception:
            pass
    db.close()


def _run_db_retry(db: Session, fn: Callable[[], Any], *, attempts: int = 3) -> Any:
    last_error: OperationalError | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return fn()
        except OperationalError as exc:
            last_error = exc
            if attempt >= attempts or not _is_retryable_disconnect(exc):
                raise
            _reset_db_session(db)
            time.sleep(min(0.6, 0.15 * attempt))
    if last_error is not None:
        raise last_error
    raise RuntimeError("database retry helper exited unexpectedly")


@dataclass(frozen=True)
class ParsedBookSeed:
    title: str
    author: str | None = None
    notes: str = ""
    source_path: str | None = None
    tier_hint: int | None = None


def _read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _clean_title(value: str) -> str:
    value = re.sub(r"^[\s\-*+0-9.、，,]+", "", value or "").strip()
    value = re.sub(r"[《》「」『』]", "", value)
    return value.strip()


def _split_title_author(value: str) -> tuple[str, str | None]:
    value = _clean_title(value)
    match = re.match(r"^(?P<title>.+?)[（(](?P<author>[^()（）]{1,80})[)）]\s*$", value)
    if match:
        return _clean_title(match.group("title")), match.group("author").strip()
    for sep in (" - ", " -- ", " 作者：", " 作者:", " by "):
        if sep in value:
            left, right = value.split(sep, 1)
            return _clean_title(left), right.strip() or None
    return value, None


def _clean_title(value: str) -> str:
    value = re.sub(r"^[\s\-*+0-9.、，,]+", "", value or "").strip()
    return value.strip("《》「」『』“”\"' ")


def _clean_author(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.split(r"[（(：:，,；;|]", value, maxsplit=1)[0].strip()
    return cleaned[:80] or None


def _split_title_author(value: str) -> tuple[str, str | None]:
    value = _clean_title(value)
    for sep in (" - ", " -- ", " 作者：", " 作者:", " by "):
        if sep in value:
            left, right = value.split(sep, 1)
            return _clean_title(left), _clean_author(right)
    match = re.match(r"^(?P<title>.+?)[（(](?P<author>[^()（）]{1,80})[）)]\s*$", value)
    if match:
        return _clean_title(match.group("title")), _clean_author(match.group("author"))
    return value, None


def _tier_from_text(text: str, default: int = 4) -> int:
    lower = text.lower()
    section_match = re.search(r"第\s*([一二三四1234])\s*梯队", text)
    if section_match:
        return {"一": 1, "1": 1, "二": 2, "2": 2, "三": 3, "3": 3, "四": 4, "4": 4}[section_match.group(1)]
    if re.search(r"(tier\s*1|一|核心|core)", lower):
        return 1
    if re.search(r"(tier\s*2|二|探案|悬疑|case|mystery)", lower):
        return 2
    if re.search(r"(tier\s*3|三|朝堂|权谋|官场|court|bureaucracy)", lower):
        return 3
    if re.search(r"(tier\s*4|四|仙侠|玄幻|longform|xianxia)", lower):
        return 4
    return default


def _classification_for(seed: ParsedBookSeed) -> dict[str, Any]:
    text = f"{seed.title} {seed.author or ''} {seed.notes}".lower()
    tier = seed.tier_hint or _tier_from_text(text)
    if tier == 1:
        category = "core_similarity"
        roles = ["plot_structure", "pleasure", "character_arc", "foreshadow"]
        likely_use = ["small case to larger conspiracy", "low-status official climb", "dense reader reward"]
        priority = 1
    elif tier == 2:
        category = "case_mystery"
        roles = ["plot_structure", "foreshadow", "anti_cliche"]
        likely_use = ["case mechanism", "clue chain", "partial reveal"]
        priority = 2
    elif tier == 3:
        category = "court_power"
        roles = ["bureaucracy", "faction", "worldbuilding"]
        likely_use = ["court institution pressure", "faction escalation", "statecraft conflict"]
        priority = 2
    else:
        category = "longform_xianxia"
        roles = ["power_system", "longform_pacing", "heroine_arc"]
        likely_use = ["long arc expansion", "power progression", "ensemble relationship pacing"]
        priority = 3
    confidence = 0.75 if seed.tier_hint else 0.55
    return {
        "tier": tier,
        "category": category,
        "corpus_role": roles,
        "priority": priority,
        "likely_use": likely_use,
        "confidence": confidence,
        "needs_verification": True,
        "tags": [category, *roles],
    }


def _parse_markdown_seed(text: str, source_path: str | None = None) -> list[ParsedBookSeed]:
    records: list[ParsedBookSeed] = []
    current_tier: int | None = 1
    normalized_text = re.sub(r"(?<!^)\s+(?=\d{1,3}[.、]\s*《)", "\n", text)
    normalized_text = re.sub(r"(?<!^)\s+(?=第[一二三四五六七八九十]+梯队)", "\n", normalized_text)
    for raw_line in normalized_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            current_tier = _tier_from_text(line, current_tier or 4)
            continue
        if re.match(r"^\|?\s*(title|书名|作品)", line, re.I):
            continue
        if re.match(r"^第[一二三四五六七八九十]+梯队", line):
            current_tier = _tier_from_text(line, current_tier or 4)
            continue
        line = re.sub(r"^\d{1,3}[.、]\s*", "", line)
        line = line.strip("|").strip()
        if not line or set(line) <= {"-", "|", ":"}:
            continue
        if "|" in line:
            cells = [cell.strip() for cell in line.split("|") if cell.strip()]
            if not cells:
                continue
            title, author = _split_title_author(cells[0])
            if len(cells) > 1 and not author:
                author = cells[1].strip() or None
            notes = " | ".join(cells[2:]) if len(cells) > 2 else ""
        else:
            title, author = _split_title_author(line)
            notes = ""
        if len(title) < 2 or title.lower() in {"title", "book"}:
            continue
        records.append(ParsedBookSeed(title=title, author=author, notes=notes, source_path=source_path, tier_hint=current_tier))
    return records


def _parse_csv_seed(path: Path) -> list[ParsedBookSeed]:
    rows: list[ParsedBookSeed] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            title = _clean_title(row.get("title") or row.get("书名") or row.get("name") or "")
            if not title:
                continue
            rows.append(
                ParsedBookSeed(
                    title=title,
                    author=(row.get("author") or row.get("作者") or "").strip() or None,
                    notes=(row.get("notes") or row.get("备注") or "").strip(),
                    source_path=str(path),
                    tier_hint=int(row["tier"]) if str(row.get("tier") or "").isdigit() else None,
                )
            )
    return rows


def _parse_json_seed(path: Path) -> list[ParsedBookSeed]:
    data = json.loads(_read_text(path))
    if isinstance(data, dict):
        data = data.get("items") or data.get("books") or []
    rows: list[ParsedBookSeed] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        title = _clean_title(str(item.get("title") or item.get("name") or item.get("书名") or ""))
        if not title:
            continue
        tier_raw = item.get("tier")
        rows.append(
            ParsedBookSeed(
                title=title,
                author=str(item.get("author") or item.get("作者") or "").strip() or None,
                notes=str(item.get("notes") or item.get("备注") or "").strip(),
                source_path=str(path),
                tier_hint=int(tier_raw) if str(tier_raw or "").isdigit() else None,
            )
        )
    return rows


def find_seed_path(seed_path: str | None = None) -> Path | None:
    if seed_path:
        path = resolve_repo_path(seed_path)
        return path if path.exists() else None
    for candidate in SUPPORTED_SEED_PATHS:
        path = resolve_repo_path(candidate)
        if path.exists():
            return path
    return None


def parse_booklist_seed(seed_path: str | None = None, raw_markdown: str | None = None) -> list[ParsedBookSeed]:
    if raw_markdown and raw_markdown.strip():
        return _parse_markdown_seed(raw_markdown, source_path="inline")
    path = find_seed_path(seed_path)
    if not path:
        raise EpicReferenceError("Reference booklist seed not found")
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _parse_csv_seed(path)
    if suffix == ".json":
        return _parse_json_seed(path)
    return _parse_markdown_seed(_read_text(path), source_path=str(path))


def normalize_booklist(
    db: Session,
    *,
    seed_path: str | None = None,
    raw_markdown: str | None = None,
    create_tasks: bool = True,
) -> list[ReferenceBooklistItem]:
    ensure_epic_storage()
    seeds = parse_booklist_seed(seed_path=seed_path, raw_markdown=raw_markdown)
    items: list[ReferenceBooklistItem] = []
    seen: set[tuple[str, str]] = set()
    for seed in seeds:
        key = (seed.title.casefold(), (seed.author or "").casefold())
        if key in seen:
            continue
        seen.add(key)
        cls = _classification_for(seed)
        item = (
            db.query(ReferenceBooklistItem)
            .filter(ReferenceBooklistItem.title == seed.title, ReferenceBooklistItem.author == seed.author)
            .first()
        )
        if item is None:
            item = ReferenceBooklistItem(title=seed.title, author=seed.author)
            db.add(item)
        item.tier = cls["tier"]
        item.category = cls["category"]
        item.genre = "ancient_transmigration_mystery_xianxia"
        item.source_type = "sonovel_search"
        item.source_path = seed.source_path
        item.corpus_role = cls["corpus_role"]
        item.priority = cls["priority"]
        item.tags = cls["tags"]
        item.notes = seed.notes
        item.likely_use = cls["likely_use"]
        item.confidence = cls["confidence"]
        item.needs_verification = cls["needs_verification"]
        item.ingest_allowed = True
        items.append(item)
    db.flush()
    if create_tasks:
        for item in items:
            existing = db.query(ReferenceDownloadTask).filter(ReferenceDownloadTask.booklist_item_id == item.id).first()
            if existing is None:
                db.add(
                    ReferenceDownloadTask(
                        booklist_item_id=item.id,
                        provider=settings.REFERENCE_DOWNLOADER_PROVIDER,
                        title=item.title,
                        author=item.author,
                        query=f"{item.title} {item.author or ''}".strip(),
                        status=ReferenceDownloadStatus.PENDING,
                    )
                )
    db.commit()
    _write_normalized_booklist(items)
    return items


def _write_normalized_booklist(items: list[ReferenceBooklistItem]) -> None:
    root = reference_raw_path().parent
    root.mkdir(parents=True, exist_ok=True)
    data = [
        {
            "id": str(item.id),
            "title": item.title,
            "author": item.author,
            "tier": item.tier,
            "category": item.category,
            "genre": item.genre,
            "sourceType": item.source_type,
            "corpusRole": item.corpus_role,
            "priority": item.priority,
            "tags": item.tags,
            "notes": item.notes,
            "downloadStatus": item.download_status,
            "ingestAllowed": item.ingest_allowed,
            "needsVerification": item.needs_verification,
        }
        for item in items
    ]
    (root / "booklist.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    with (root / "booklist.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(data[0].keys()) if data else ["title"])
        writer.writeheader()
        writer.writerows(data)


class ExternalDownloaderAdapter:
    """Common contract for trusted local reference download providers."""

    provider_name = "external"

    def detect_installation(self) -> dict[str, Any]:
        raise NotImplementedError

    def get_config(self) -> dict[str, Any]:
        raise NotImplementedError

    def search_book(self, title: str, author: str | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def download_result(self, selected_result: dict[str, Any], *, format: str | None = None, timeout: int = 1800) -> dict[str, Any]:
        raise NotImplementedError

    def list_downloaded_files(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def import_downloaded_file(self, db: Session, file_path: str | Path) -> ReferenceBook:
        return import_reference_file(db, file_path=file_path)

    def get_logs(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def validate_output(self, path: str | Path, db: Session | None = None) -> dict[str, Any]:
        raise NotImplementedError


class SoNovelAdapter(ExternalDownloaderAdapter):
    provider_name = "sonovel"

    def __init__(self, home: str | None = None, download_path: str | None = None, web_base_url: str | None = None) -> None:
        self.home = Path(home or settings.SONOVEL_HOME)
        self.download_path = Path(download_path or settings.SONOVEL_DOWNLOAD_PATH)
        self.web_base_url = (web_base_url or settings.SONOVEL_WEB_BASE_URL).rstrip("/")

    def _request_json(self, path: str, params: dict[str, Any] | None = None, timeout: int = 60) -> dict[str, Any]:
        query = f"?{urlencode({k: v for k, v in (params or {}).items() if v is not None})}" if params else ""
        url = f"{self.web_base_url}{path}{query}"
        try:
            with NO_PROXY_OPENER.open(url, timeout=timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise EpicReferenceError(f"SoNovel HTTP {exc.code} for {path}: {detail[:500]}") from exc
        except URLError as exc:
            raise EpicReferenceError(f"SoNovel WebUI unavailable at {self.web_base_url}: {exc.reason}") from exc
        except OSError as exc:
            raise EpicReferenceError(f"SoNovel connection error for {path}: {exc}") from exc
        if not body.strip():
            return {"code": 204, "message": "empty response", "data": None}
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise EpicReferenceError(f"SoNovel returned non-JSON response for {path}: {body[:500]}") from exc
        if isinstance(data, dict):
            return data
        raise EpicReferenceError(f"SoNovel returned unexpected response for {path}")

    def _webui_available(self) -> bool:
        try:
            with NO_PROXY_OPENER.open(self.web_base_url, timeout=5) as response:
                return 200 <= response.status < 500
        except Exception:
            return False

    def _score_result(self, item: dict[str, Any], title: str, author: str | None = None) -> int:
        normalized_title = _clean_title(title).casefold()
        normalized_author = (author or "").strip().casefold()
        item_title = str(item.get("bookName") or item.get("title") or "").strip().casefold()
        item_author = str(item.get("author") or "").strip().casefold()
        value = 0
        if item_title == normalized_title:
            value += 100
        elif normalized_title and normalized_title in item_title:
            value += 75
        else:
            ratio = SequenceMatcher(None, normalized_title, item_title).ratio() if normalized_title and item_title else 0
            if ratio >= 0.92:
                value += 55
            elif ratio >= 0.86:
                value += 25
        if normalized_author:
            if item_author == normalized_author:
                value += 35
            elif normalized_author in item_author or (item_author and item_author in normalized_author):
                value += 15
        return value

    def select_candidates(
        self,
        results: list[dict[str, Any]],
        title: str,
        author: str | None = None,
        *,
        limit: int = 3,
        min_score: int = 80,
    ) -> list[dict[str, Any]]:
        scored = [(self._score_result(item, title, author), item) for item in results if isinstance(item, dict)]
        scored = [(score, item) for score, item in scored if score >= min_score]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[: max(1, limit)]]

    def _select_best_result(self, results: list[dict[str, Any]], title: str, author: str | None = None) -> dict[str, Any] | None:
        candidates = self.select_candidates(results, title, author, limit=1)
        return candidates[0] if candidates else None

    def detect_installation(self) -> dict[str, Any]:
        home_exists = self.home.exists()
        files = {name: self.home / name for name in ["sonovel.exe", "sonovel", "app.jar", "config.ini", "readme.txt", "readme.md"]}
        dirs = {name: self.home / name for name in ["downloads", "logs", "rules", "runtime"]}
        executable_paths = [path for path in files.values() if path.name in {"sonovel.exe", "sonovel"} and path.exists()]
        jar_paths = [path for path in files.values() if path.suffix == ".jar" and path.exists()]
        runtime_markers = executable_paths + jar_paths
        detected = {
            "provider": self.provider_name,
            "home": str(self.home),
            "homeExists": home_exists,
            "downloadPath": str(self.download_path),
            "downloadPathExists": self.download_path.exists(),
            "webBaseUrl": self.web_base_url,
            "webUiAvailable": self._webui_available(),
            "outputFormat": settings.SONOVEL_OUTPUT_FORMAT,
            "files": {name: {"path": str(path), "exists": path.exists()} for name, path in files.items()},
            "directories": {name: {"path": str(path), "exists": path.exists()} for name, path in dirs.items()},
            "runtimeCandidates": [str(path) for path in runtime_markers],
            "canInvokeSafely": False,
            "manualRequired": True,
            "manualCommandSuggestion": r".\sonovel.exe -h",
        }
        if home_exists and (runtime_markers or self.download_path.exists() or files["config.ini"].exists()):
            detected["status"] = "detected_manual_required"
            if detected["webUiAvailable"]:
                detected["status"] = "detected_webui_ready"
                detected["canInvokeSafely"] = True
                detected["manualRequired"] = False
                detected["manualCommandSuggestion"] = ""
        else:
            detected["status"] = "not_detected"
        return detected

    def get_config(self) -> dict[str, Any]:
        config_path = self.home / "config.ini"
        config_text = _read_text(config_path) if config_path.exists() else ""
        return {
            "provider": self.provider_name,
            "home": str(self.home),
            "downloadPath": str(self.download_path),
            "outputFormat": settings.SONOVEL_OUTPUT_FORMAT,
            "webBaseUrl": self.web_base_url,
            "configPath": str(config_path),
            "configPreview": config_text[:4000],
        }

    def search_book(self, title: str, author: str | None = None) -> dict[str, Any]:
        query = title.strip()
        try:
            data = self._request_json("/search/aggregated", {"kw": query}, timeout=90)
        except EpicReferenceError as exc:
            return {
                "status": ReferenceDownloadStatus.MANUAL_REQUIRED,
                "query": f"{title} {author or ''}".strip(),
                "results": [],
                "manualCommandSuggestion": str(exc),
            }
        results = data.get("data") or []
        if not results and author:
            data = self._request_json("/search/aggregated", {"kw": author}, timeout=90)
            results = data.get("data") or []
        best = self._select_best_result(results, title, author)
        return {
            "status": ReferenceDownloadStatus.FOUND if best else ReferenceDownloadStatus.NOT_FOUND,
            "query": f"{title} {author or ''}".strip(),
            "results": results,
            "selectedResult": best,
            "manualCommandSuggestion": "" if best else "No matching SoNovel search result; try the title or author manually.",
        }

    def download_result(self, selected_result: dict[str, Any], *, format: str | None = None, timeout: int = 1800) -> dict[str, Any]:
        if not selected_result:
            raise EpicReferenceError("selected_result is required for SoNovel download")
        before = {path.name for path in self.download_path.glob("*") if path.is_file()} if self.download_path.exists() else set()
        params = dict(selected_result)
        params["format"] = format or settings.SONOVEL_OUTPUT_FORMAT
        data = self._request_json("/book-fetch", params, timeout=timeout)
        after_files = [path for path in self.download_path.glob("*") if path.is_file()] if self.download_path.exists() else []
        new_files = [path for path in after_files if path.name not in before]
        expected_title = str(selected_result.get("bookName") or selected_result.get("title") or "")
        matching_files = [path for path in after_files if expected_title and expected_title in path.stem]
        latest = max(new_files or matching_files, key=lambda path: path.stat().st_mtime, default=None)
        return {
            "status": ReferenceDownloadStatus.DOWNLOADED if latest else ReferenceDownloadStatus.FAILED,
            "response": data,
            "localPath": str(latest) if latest else None,
            "fileHash": compute_file_hash(latest) if latest and latest.exists() else None,
        }

    def list_downloaded_files(self) -> list[dict[str, Any]]:
        if not self.download_path.exists():
            return []
        files: list[dict[str, Any]] = []
        for path in sorted(self.download_path.rglob("*")):
            if path.is_file() and path.suffix.lower() in {".epub", ".txt", ".md", ".html", ".json"}:
                title, author = _split_title_author(path.stem)
                files.append(
                    {
                        "path": str(path),
                        "filename": path.name,
                        "title": title,
                        "author": author,
                        "size": path.stat().st_size,
                        "extension": path.suffix.lower(),
                        "fileHash": compute_file_hash(path),
                    }
                )
        return files

    def get_logs(self) -> list[dict[str, Any]]:
        log_dir = self.home / "logs"
        if not log_dir.exists():
            return []
        return [
            {"path": str(path), "filename": path.name, "size": path.stat().st_size}
            for path in sorted(log_dir.glob("*"))[:50]
            if path.is_file()
        ]

    def validate_output(self, path: str | Path, db: Session | None = None) -> dict[str, Any]:
        file_path = Path(path)
        supported = {".epub", ".txt", ".md", ".html", ".json"}
        exists = file_path.exists() and file_path.is_file()
        title, author = _split_title_author(file_path.stem) if exists else ("", None)
        file_hash = compute_file_hash(file_path) if exists and file_path.stat().st_size > 0 and file_path.suffix.lower() in supported else None
        duplicate_book = db.query(ReferenceBook).filter(ReferenceBook.file_hash == file_hash).first() if db is not None and file_hash else None
        valid = bool(file_hash and title and duplicate_book is None)
        return {
            "path": str(file_path),
            "exists": exists,
            "size": file_path.stat().st_size if exists else 0,
            "extension": file_path.suffix.lower(),
            "title": title or None,
            "author": author,
            "titleInferred": bool(title),
            "duplicate": duplicate_book is not None,
            "duplicateBookId": str(duplicate_book.id) if duplicate_book is not None else None,
            "valid": valid,
            "fileHash": file_hash,
        }


def detect_sonovel() -> dict[str, Any]:
    adapter = SoNovelAdapter()
    detected = adapter.detect_installation()
    detected["config"] = adapter.get_config()
    detected["logs"] = adapter.get_logs()
    return detected


def create_reference_search_task(db: Session, *, title: str, author: str | None = None) -> ReferenceDownloadTask:
    adapter = SoNovelAdapter()
    result = adapter.search_book(title, author)
    task = ReferenceDownloadTask(
        provider=adapter.provider_name,
        title=title,
        author=author,
        query=result["query"],
        status=result["status"],
        search_results=result["results"],
        selected_result=result.get("selectedResult"),
        error=result.get("manualCommandSuggestion"),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def search_reference_download_task(db: Session, task: ReferenceDownloadTask) -> ReferenceDownloadTask:
    adapter = SoNovelAdapter()
    result = adapter.search_book(task.title, task.author)
    task.provider = adapter.provider_name
    task.query = result["query"]
    task.status = result["status"]
    task.search_results = result["results"]
    task.selected_result = result.get("selectedResult")
    task.error = result.get("manualCommandSuggestion")
    db.commit()
    db.refresh(task)
    return task


def download_reference_task(
    db: Session,
    task: ReferenceDownloadTask,
    *,
    selected_result: dict[str, Any] | None = None,
    format: str | None = None,
) -> ReferenceDownloadTask:
    adapter = SoNovelAdapter()
    chosen = selected_result or task.selected_result
    if chosen is None:
        task = search_reference_download_task(db, task)
        chosen = task.selected_result
    if chosen is None:
        task.status = ReferenceDownloadStatus.MANUAL_REQUIRED
        task.error = "No SoNovel candidate selected; search manually or provide selected_result."
        db.commit()
        db.refresh(task)
        return task
    task.status = ReferenceDownloadStatus.DOWNLOADING
    task.selected_result = chosen
    task.error = None
    db.commit()
    try:
        result = adapter.download_result(chosen, format=format)
        task.status = result["status"]
        task.local_download_path = result.get("localPath")
        task.file_hash = result.get("fileHash")
        task.error = None if task.status == ReferenceDownloadStatus.DOWNLOADED else json.dumps(result.get("response"), ensure_ascii=False)
    except EpicReferenceError as exc:
        task.status = ReferenceDownloadStatus.FAILED
        task.error = str(exc)
    db.commit()
    db.refresh(task)
    return task


def list_reference_download_tasks(db: Session, limit: int = 500) -> list[ReferenceDownloadTask]:
    return _run_db_retry(
        db,
        lambda: db.query(ReferenceDownloadTask).order_by(ReferenceDownloadTask.created_at.desc()).limit(limit).all(),
    )


def list_reference_books(db: Session, limit: int = 500) -> list[ReferenceBook]:
    return _run_db_retry(
        db,
        lambda: (
            db.query(ReferenceBook)
            .filter(ReferenceBook.status != "detached")
            .order_by(ReferenceBook.updated_at.desc(), ReferenceBook.created_at.desc())
            .limit(limit)
            .all()
        ),
    )


def delete_reference_book(db: Session, book_id: UUID) -> dict[str, Any]:
    book = db.get(ReferenceBook, book_id)
    if book is None:
        raise EpicReferenceError(f"ReferenceBook not found: {book_id}")
    source_path = book.source_path
    title = book.title
    booklist_item = book.booklist_item
    if booklist_item is not None and booklist_item.local_raw_path == source_path:
        booklist_item.local_raw_path = None
        booklist_item.download_status = ReferenceDownloadStatus.PENDING
    db.delete(book)
    db.commit()
    return {
        "status": "deleted",
        "bookId": str(book_id),
        "title": title,
        "localFilePreserved": True,
        "sourcePath": source_path,
    }


def retry_reference_download_task(db: Session, task_id: UUID) -> ReferenceDownloadTask:
    task = db.get(ReferenceDownloadTask, task_id)
    if task is None:
        raise EpicReferenceError(f"ReferenceDownloadTask not found: {task_id}")
    task.status = ReferenceDownloadStatus.PENDING
    task.error = None
    task.generated_card_count = 0
    if task.booklist_item is not None:
        task.booklist_item.download_status = ReferenceDownloadStatus.PENDING
        task.booklist_item.error = None
    db.commit()
    db.refresh(task)
    return task


def reference_readiness_summary(db: Session) -> dict[str, Any]:
    profile = latest_reference_profile(db)
    total_books = db.query(ReferenceBook).filter(ReferenceBook.status != "detached").count()
    total_cards = db.query(ReferencePatternCard).count()
    production_ready_cards = len(_production_ready_cards(db))
    downloads = db.query(ReferenceDownloadTask.status, func.count(ReferenceDownloadTask.id)).group_by(ReferenceDownloadTask.status).all()
    imported_books = db.query(ReferenceBook).filter(ReferenceBook.status != "detached").count()
    return {
        "ready": bool(profile and production_ready_cards > 0),
        "profileReady": bool(profile),
        "profileId": str(profile.id) if profile else None,
        "sourceBookCount": profile.source_book_count if profile else total_books,
        "sourceCardCount": profile.source_card_count if profile else total_cards,
        "totalBooks": total_books,
        "importedBooks": imported_books,
        "totalCards": total_cards,
        "productionReadyCards": production_ready_cards,
        "downloadStatus": {status: count for status, count in downloads},
        "rawTextIncludedInGeneration": False,
        "minimumViable": production_ready_cards > 0,
    }


def list_reference_download_task_summaries(db: Session, limit: int = 120) -> list[dict[str, Any]]:
    rows = _run_db_retry(
        db,
        lambda: (
            db.query(
                ReferenceDownloadTask.id,
                ReferenceDownloadTask.booklist_item_id,
                ReferenceDownloadTask.provider,
                ReferenceDownloadTask.title,
                ReferenceDownloadTask.author,
                ReferenceDownloadTask.query,
                ReferenceDownloadTask.status,
                ReferenceDownloadTask.local_download_path,
                ReferenceDownloadTask.imported_raw_path,
                ReferenceDownloadTask.file_hash,
                ReferenceDownloadTask.generated_card_count,
                ReferenceDownloadTask.error,
                ReferenceDownloadTask.created_at,
                ReferenceDownloadTask.updated_at,
            )
            .order_by(ReferenceDownloadTask.created_at.desc())
            .limit(limit)
            .all()
        ),
    )
    return [
        {
            "id": row.id,
            "booklist_item_id": row.booklist_item_id,
            "provider": row.provider,
            "title": row.title,
            "author": row.author,
            "query": row.query,
            "status": row.status,
            "search_results": [],
            "selected_result": None,
            "local_download_path": row.local_download_path,
            "imported_raw_path": row.imported_raw_path,
            "file_hash": row.file_hash,
            "generated_card_count": row.generated_card_count,
            "error": row.error,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
        for row in rows
    ]


def scan_sonovel_downloads(db: Session | None = None) -> list[dict[str, Any]]:
    adapter = SoNovelAdapter()
    files = adapter.list_downloaded_files()
    if db is not None:
        for file in files:
            item = (
                db.query(ReferenceBooklistItem)
                .filter(ReferenceBooklistItem.title == file["title"])
                .first()
            )
            file["matchedBooklistItemId"] = str(item.id) if item else None
    return files


def import_reference_file(
    db: Session,
    *,
    file_path: str | Path,
    booklist_item: ReferenceBooklistItem | None = None,
    download_task: ReferenceDownloadTask | None = None,
) -> ReferenceBook:
    ensure_epic_storage()
    source = Path(file_path)
    if not source.exists() or not source.is_file():
        raise EpicReferenceError(f"Reference file not found: {source}")
    if source.suffix.lower() not in {".epub", ".txt", ".md", ".html", ".json"}:
        raise EpicReferenceError(f"Unsupported reference file extension: {source.suffix}")
    file_hash = compute_file_hash(source)
    existing = db.query(ReferenceBook).filter(ReferenceBook.file_hash == file_hash).first()
    if existing is not None:
        if booklist_item is not None:
            booklist_item.local_raw_path = existing.source_path
            booklist_item.download_status = ReferenceDownloadStatus.IMPORTED
        if download_task is not None:
            download_task.imported_raw_path = existing.source_path
            download_task.file_hash = file_hash
            download_task.status = ReferenceDownloadStatus.IMPORTED
        if booklist_item is not None or download_task is not None:
            db.commit()
            db.refresh(existing)
        return existing
    title, author = _split_title_author(source.stem)
    item = booklist_item
    if item is None:
        item = db.query(ReferenceBooklistItem).filter(ReferenceBooklistItem.title == title).first()
    tier = item.tier if item is not None else 4
    raw_dir = reference_raw_path() / tier_folder(tier)
    raw_dir.mkdir(parents=True, exist_ok=True)
    dest = raw_dir / source.name
    if source.resolve() != dest.resolve():
        shutil.copy2(source, dest)
    book = ReferenceBook(
        booklist_item_id=item.id if item else None,
        download_task_id=download_task.id if download_task else None,
        title=item.title if item else title,
        author=item.author if item and item.author else author,
        genre=item.genre if item else "ancient_transmigration_mystery_xianxia",
        tier=tier,
        category=item.category if item else "metadata_only",
        source_path=str(dest),
        storage_type="local",
        file_hash=file_hash,
        language="zh",
        status="uploaded",
    )
    db.add(book)
    if item is not None:
        item.local_raw_path = str(dest)
        item.download_status = ReferenceDownloadStatus.IMPORTED
    if download_task is not None:
        download_task.imported_raw_path = str(dest)
        download_task.file_hash = file_hash
        download_task.status = ReferenceDownloadStatus.IMPORTED
    db.commit()
    db.refresh(book)
    return book


def import_downloaded_references(db: Session, *, include_unmatched: bool = False) -> list[ReferenceBook]:
    imported: list[ReferenceBook] = []
    for file in scan_sonovel_downloads(db):
        item = None
        task = None
        if file.get("matchedBooklistItemId"):
            item = db.get(ReferenceBooklistItem, UUID(file["matchedBooklistItemId"]))
        if item is None and not include_unmatched:
            continue
        if item is not None:
            task = (
                db.query(ReferenceDownloadTask)
                .filter(ReferenceDownloadTask.booklist_item_id == item.id)
                .order_by(ReferenceDownloadTask.created_at.desc())
                .first()
            )
        imported.append(import_reference_file(db, file_path=file["path"], booklist_item=item, download_task=task))
    return imported


def parse_reference_text(path: str | Path) -> str:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix in {".txt", ".md"}:
        return _read_text(source)
    if suffix == ".json":
        data = json.loads(_read_text(source))
        return json.dumps(data, ensure_ascii=False)
    if suffix == ".html":
        text = _read_text(source)
        return clean_text(re.sub(r"<[^>]+>", "\n", html.unescape(text)))
    if suffix == ".epub":
        parts: list[str] = []
        with zipfile.ZipFile(source) as archive:
            for name in archive.namelist():
                if name.lower().endswith((".xhtml", ".html", ".htm", ".txt")):
                    raw = archive.read(name)
                    try:
                        text = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        text = raw.decode("gb18030", errors="replace")
                    text = re.sub(r"<[^>]+>", "\n", html.unescape(text))
                    parts.append(text)
        return clean_text("\n".join(parts))
    raise EpicReferenceError(f"Unsupported reference file: {source}")


def clean_text(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _json_hash(data: Any) -> str:
    return _hash_text(json.dumps(data, ensure_ascii=False, sort_keys=True))


def split_by_chapter(text: str, limit: int = 0) -> list[dict[str, Any]]:
    section_limit = int(limit or 0)
    chunk_chars = max(1000, int(settings.EPIC_REFERENCE_SECTION_CHARS or 5000))
    lines = clean_text(text).splitlines()
    sections: list[dict[str, str]] = []
    current_title = "opening"
    current: list[str] = []
    heading_re = re.compile(r"^\s*(第[一二三四五六七八九十百千万0-9]+[章节回卷].{0,80}|chapter\s+\d+.{0,80})\s*$", re.I)
    for line in lines:
        if heading_re.match(line) and current:
            sections.append({"title": current_title, "text": "\n".join(current).strip()})
            current_title = line.strip()
            current = []
            if section_limit > 0 and len(sections) >= section_limit:
                break
        else:
            current.append(line)
    if current and (section_limit <= 0 or len(sections) < section_limit):
        sections.append({"title": current_title, "text": "\n".join(current).strip()})
    if not sections:
        text = clean_text(text)
        for index in range(0, len(text), 5000):
            sections.append({"title": f"section_{len(sections) + 1}", "text": text[index : index + 5000]})
            if section_limit > 0 and len(sections) >= section_limit:
                break
    chunked: list[dict[str, str]] = []
    for section in sections:
        body = section["text"]
        if len(body) <= chunk_chars:
            chunked.append(section)
            continue
        for index in range(0, len(body), chunk_chars):
            chunked.append({"title": f"{section['title']} part {index // chunk_chars + 1}", "text": body[index : index + chunk_chars]})
            if section_limit > 0 and len(chunked) >= section_limit:
                break
        if section_limit > 0 and len(chunked) >= section_limit:
            break
    normalized: list[dict[str, Any]] = []
    cursor = 0
    for index, section in enumerate(chunked):
        body = section["text"]
        if not body:
            continue
        start = cursor
        end = start + len(body)
        section_hash = _hash_text(body)
        normalized.append(
            {
                "title": section["title"],
                "text": body,
                "sectionIndex": index,
                "sourceSectionId": f"section_{index + 1:06d}_{section_hash[:12]}",
                "sectionHash": section_hash,
                "startOffset": start,
                "endOffset": end,
            }
        )
        cursor = end
    return normalized


SECTION_ANALYSIS_REQUIRED_KEYS = [
    "sectionSummary",
    "plotFunction",
    "pleasureFunction",
    "characterArcMove",
    "heroineAgencyMove",
    "mysteryClueForeshadowActions",
    "bureaucracyCourtFactionFunction",
    "powerSystemMove",
    "pacingRole",
    "antiClicheNotes",
    "reusableMechanisms",
    "notToCopyNotes",
]


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw, flags=re.I).strip()
        raw = re.sub(r"```$", "", raw).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(raw[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("reference analysis output must be a JSON object")
    return parsed


def _has_cjk(value: Any) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", json.dumps(value, ensure_ascii=False, default=str)))


def _mostly_chinese_text(value: Any) -> bool:
    def collect_leaf_text(item: Any) -> list[str]:
        if isinstance(item, dict):
            return [part for child in item.values() for part in collect_leaf_text(child)]
        if isinstance(item, list):
            return [part for child in item for part in collect_leaf_text(child)]
        return [str(item)]

    text = value if isinstance(value, str) else " ".join(collect_leaf_text(value))
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    return cjk > max(20, latin * 2)


def _contains_forbidden_english_label(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False, default=str)
    lowered = text.casefold()
    return any(label.casefold() in lowered for label in GATE2A_FORBIDDEN_ENGLISH_LABELS)


def _as_list(value: Any, fallback: list[Any] | None = None) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return list(fallback or [])


def _stable_source_section_id(book: ReferenceBook, section: dict[str, Any]) -> str:
    section_hash = str(section.get("sectionHash") or _hash_text(str(section.get("text") or "")))
    book_hash = (book.file_hash or str(book.id)).replace("-", "")[:12]
    index = int(section.get("sectionIndex") or 0) + 1
    return f"book_{book_hash}_section_{index:06d}_{section_hash[:12]}"


def _analysis_hash(data: dict[str, Any]) -> str:
    comparable = {
        key: data.get(key)
        for key in GATE2A_ANALYSIS_KEYS
    }
    comparable["sourceBookId"] = data.get("sourceBookId")
    comparable["sourceSectionId"] = data.get("sourceSectionId")
    comparable["sectionHash"] = data.get("sectionHash")
    return _json_hash(comparable)


def _fallback_section_analysis(book: ReferenceBook, section: dict[str, Any]) -> dict[str, Any]:
    title = str(section.get("title") or "opening")
    chars = len(str(section.get("text") or ""))
    density = "高密度" if chars > 3500 else "中密度" if chars > 1200 else "低密度"
    category_label = {
        "core_similarity": "穿越探案与小案牵大局",
        "case_mystery": "案件谜题与线索递进",
        "court_power": "朝堂官僚与权力博弈",
        "longform_xianxia": "长线升级与世界扩展",
    }.get(book.category, "长篇结构")
    return {
        "sectionSummary": f"{book.title} 的《{title[:48]}》被抽象为{density}结构片段，仅保留机制用途。",
        "plotFunction": f"{category_label}中的局部推进节点，用于制造约束、选择和后果。",
        "pleasureFunction": "通过信息差、制度阻力、阶段性反转和可见收益形成读者爽点。",
        "characterArcMove": "角色在压力中从被动理解规则转为主动利用规则，但必须付出代价。",
        "heroineAgencyMove": "女主/关键女性角色应有独立目标、资源和选择，不作为奖励或旁观说明。",
        "mysteryActions": ["设置可验证线索", "延迟解释关键矛盾", "让局部答案指向更大结构"],
        "bureaucracyFunction": "程序、档案、职位、规章或派系利益成为情节压力源，而不是背景板。",
        "powerSystemMove": "能力推进必须受规则、代价和社会承认约束，避免无成本跃迁。",
        "pacingRole": "小高潮后保留未解问题，为下一节提供钩子，同时不提前揭开主谜底。",
        "antiClicheNotes": ["不复制原文表达、人物名、场景或反转", "不把单本书当锚点", "不使用天降便利解决冲突"],
        "reusableMechanisms": [
            {"name": "制度阻力转线索", "function": "把办事流程中的阻碍转化为推理证据"},
            {"name": "局部反转挂全局", "function": "章节答案解决眼前问题，同时暴露世界底层矛盾"},
            {"name": "有代价的爽点兑现", "function": "胜利带来新责任、新敌人或新限制"},
        ],
        "notToCopy": ["原书措辞", "原书人物/机构名", "原书案件、笑点、桥段、章节结构"],
        "rawTextIncluded": False,
        "sourceBookId": str(book.id),
        "sourceSectionId": section.get("sourceSectionId"),
        "sectionHash": section.get("sectionHash"),
        "language": settings.EPIC_REFERENCE_EXTRACTION_LANGUAGE,
    }


def _normalize_section_analysis(book: ReferenceBook, section: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    fallback = _fallback_section_analysis(book, section)
    normalized = {key: data.get(key, fallback[key]) for key in SECTION_ANALYSIS_REQUIRED_KEYS}
    for key in ("mysteryActions", "antiClicheNotes", "reusableMechanisms", "notToCopy"):
        value = normalized.get(key)
        if isinstance(value, str):
            normalized[key] = [value]
        elif not isinstance(value, list):
            normalized[key] = fallback[key]
    normalized["rawTextIncluded"] = False
    normalized["sourceBookId"] = str(book.id)
    normalized["sourceSectionId"] = section.get("sourceSectionId")
    normalized["sectionHash"] = section.get("sectionHash")
    normalized["language"] = settings.EPIC_REFERENCE_EXTRACTION_LANGUAGE
    return normalized


def _build_section_analysis_prompt(book: ReferenceBook, section: dict[str, Any]) -> str:
    text_budget_chars = int(settings.EPIC_REFERENCE_BATCH_TOKEN_BUDGET or 60000) * 3
    return get_epic_prompt("reference_section_analysis_prompt").format(
        source_book_id=book.id,
        title=book.title,
        author=book.author or "",
        tier=book.tier,
        category=book.category,
        source_section_id=section.get("sourceSectionId"),
        chapter_title=section.get("title"),
        section_hash=section.get("sectionHash"),
        reference_text=str(section.get("text") or "")[:text_budget_chars],
    )


# Gate 2A overrides: keep the legacy names so existing callers use the
# section-aware, Chinese, production/fallback-separated implementation below.
def _fallback_section_analysis(book: ReferenceBook, section: dict[str, Any]) -> dict[str, Any]:
    title = str(section.get("title") or "未命名章节")
    chars = len(str(section.get("text") or ""))
    density = "高密度" if chars > 3500 else "中密度" if chars > 1200 else "低密度"
    category_label = {
        "core_similarity": "小案牵大局与低位破局",
        "case_mystery": "线索递进与局部真相",
        "court_power": "官僚流程与势力压迫",
        "longform_xianxia": "长线升级与世界扩展",
    }.get(book.category, "长篇结构")
    section_id = str(section.get("sourceSectionId") or _stable_source_section_id(book, section))
    section_hash = str(section.get("sectionHash") or _hash_text(str(section.get("text") or "")))
    analysis = {
        "sectionSummary": f"《{book.title}》的“{title[:48]}”被本地兜底抽象为{density}结构片段，只能作为烟测占位。",
        "plotFunction": f"{category_label}中的局部推进节点，用于制造限制、选择和后果。",
        "pleasureFunction": "通过信息差、制度阻力、阶段反转和可见收益形成读者爽点。",
        "characterArcMove": "角色在压力中从被动理解规则转为主动利用规则，但必须付出代价。",
        "heroineAgencyMove": "关键女性角色应有独立目标、资源和选择，不作为奖励或旁观说明。",
        "mysteryClueForeshadowActions": ["设置可验证线索", "延迟解释关键矛盾", "让局部答案指向更大结构"],
        "bureaucracyCourtFactionFunction": "程序、档案、职权、规章或派系利益成为情节压力源，而不是背景板。",
        "powerSystemMove": "能力推进必须受规则、代价和社会承认约束，避免无成本跃迁。",
        "pacingRole": "小高潮后保留未解问题，为下一节提供钩子，同时不提前揭开主谜底。",
        "antiClicheNotes": ["不复制原文表达、人物名、场景或反转", "不把单本书当锚点", "不使用天降便利解决冲突"],
        "reusableMechanisms": [
            {"name": "制度阻力转线索", "function": "把办事流程中的阻碍转化为推理证据"},
            {"name": "局部反转挂全局", "function": "章节答案解决眼前问题，同时暴露世界底层矛盾"},
            {"name": "有代价的爽点兑现", "function": "胜利带来新责任、新敌人或新限制"},
        ],
        "notToCopyNotes": ["原书措辞", "原书人物或机构名", "原书案件、笑点、桥段、章节结构"],
        "rawTextIncluded": False,
        "sourceBookId": str(book.id),
        "sourceSectionId": section_id,
        "sectionHash": section_hash,
        "language": settings.EPIC_REFERENCE_EXTRACTION_LANGUAGE,
        "production_quality": False,
        "status": "fallback",
    }
    analysis["mysteryActions"] = analysis["mysteryClueForeshadowActions"]
    analysis["bureaucracyFunction"] = analysis["bureaucracyCourtFactionFunction"]
    analysis["notToCopy"] = analysis["notToCopyNotes"]
    return analysis


def _normalize_section_analysis(book: ReferenceBook, section: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    fallback = _fallback_section_analysis(book, section)
    aliases = {
        "mysteryClueForeshadowActions": ["mysteryClueForeshadowActions", "mysteryActions"],
        "bureaucracyCourtFactionFunction": ["bureaucracyCourtFactionFunction", "bureaucracyFunction"],
        "notToCopyNotes": ["notToCopyNotes", "notToCopy"],
    }
    normalized: dict[str, Any] = {}
    for key in SECTION_ANALYSIS_REQUIRED_KEYS:
        value = None
        for candidate in aliases.get(key, [key]):
            if candidate in data:
                value = data.get(candidate)
                break
        normalized[key] = value if value not in (None, "", []) else fallback[key]
    for key in ("mysteryClueForeshadowActions", "antiClicheNotes", "reusableMechanisms", "notToCopyNotes"):
        normalized[key] = _as_list(normalized.get(key), fallback[key])
    normalized["rawTextIncluded"] = False
    normalized["sourceBookId"] = str(book.id)
    normalized["sourceSectionId"] = str(section.get("sourceSectionId") or _stable_source_section_id(book, section))
    normalized["sectionHash"] = str(section.get("sectionHash") or _hash_text(str(section.get("text") or "")))
    normalized["language"] = settings.EPIC_REFERENCE_EXTRACTION_LANGUAGE
    normalized["production_quality"] = _mostly_chinese_text(normalized) and not _contains_forbidden_english_label(normalized)
    normalized["status"] = "success" if normalized["production_quality"] else "needs_review"
    normalized["mysteryActions"] = normalized["mysteryClueForeshadowActions"]
    normalized["bureaucracyFunction"] = normalized["bureaucracyCourtFactionFunction"]
    normalized["notToCopy"] = normalized["notToCopyNotes"]
    return normalized


def _build_section_analysis_prompt(book: ReferenceBook, section: dict[str, Any]) -> str:
    text_budget_chars = int(settings.EPIC_REFERENCE_BATCH_TOKEN_BUDGET or 60000) * 3
    return get_epic_prompt("reference_section_analysis_prompt").format(
        source_book_id=book.id,
        title=book.title,
        author=book.author or "",
        tier=book.tier,
        category=book.category,
        source_section_id=section.get("sourceSectionId"),
        chapter_title=section.get("title"),
        section_hash=section.get("sectionHash"),
        reference_text=str(section.get("text") or "")[:text_budget_chars],
    )


def _ensure_reference_section(db: Session, book: ReferenceBook, section: dict[str, Any]) -> ReferenceSection:
    source_section_id = str(section.get("sourceSectionId"))
    row = (
        db.query(ReferenceSection)
        .filter(ReferenceSection.source_book_id == book.id, ReferenceSection.source_section_id == source_section_id)
        .first()
    )
    if row is None:
        row = ReferenceSection(source_book_id=book.id, source_section_id=source_section_id)
        db.add(row)
    row.chapter_title = str(section.get("title") or "opening")[:500]
    row.section_index = int(section.get("sectionIndex") or 0)
    row.section_hash = str(section.get("sectionHash") or _hash_text(str(section.get("text") or "")))
    row.start_offset = int(section.get("startOffset")) if section.get("startOffset") is not None else None
    row.end_offset = int(section.get("endOffset")) if section.get("endOffset") is not None else None
    row.text_chars = len(str(section.get("text") or ""))
    row.status = "parsed"
    row.raw_json = {
        "sourceBookId": str(book.id),
        "sourceSectionId": source_section_id,
        "chapterTitle": row.chapter_title,
        "sectionIndex": row.section_index,
        "sectionHash": row.section_hash,
        "startOffset": row.start_offset,
        "endOffset": row.end_offset,
        "rawTextStored": False,
    }
    db.flush()
    return row


def analyze_reference_section(
    db: Session,
    *,
    book: ReferenceBook,
    section: dict[str, Any],
    section_row: ReferenceSection,
    use_llm: bool | None = None,
    force: bool | None = None,
) -> ReferenceSectionAnalysis:
    use_llm = settings.EPIC_REFERENCE_ENABLE_LLM_EXTRACTION if use_llm is None else bool(use_llm)
    force = settings.EPIC_REFERENCE_FORCE_REINGEST if force is None else bool(force)
    provider = settings.EPIC_REFERENCE_MODEL_PROVIDER or "deepseek"
    model_name = settings.EPIC_REFERENCE_MODEL_NAME or "deepseek-v4-pro"
    existing = db.query(ReferenceSectionAnalysis).filter(ReferenceSectionAnalysis.section_id == section_row.id).first()
    cache_ok = (
        existing is not None
        and settings.EPIC_REFERENCE_CACHE_BY_HASH
        and not force
        and existing.section_hash == section_row.section_hash
        and existing.status in {"completed", "completed_local", "completed_fallback"}
    )
    if cache_ok:
        section_row.analysis_status = existing.status
        db.flush()
        return existing

    prompt = _build_section_analysis_prompt(book, section)
    fallback = _fallback_section_analysis(book, section)
    status = "completed_local"
    error = None
    if use_llm:
        try:
            completion = call_epic_model(
                db,
                action="reference_section_analysis",
                role="reference",
                prompt=prompt,
                max_output_tokens=0,
            )
            parsed = _extract_json_object(completion)
            analysis_json = _normalize_section_analysis(book, section, parsed)
            status = "completed"
            completion_tokens = estimate_tokens(completion)
        except Exception as exc:  # noqa: BLE001 - ingestion should record failure and keep a safe abstract fallback.
            analysis_json = fallback
            status = "completed_fallback"
            error = str(exc)[:1000]
            completion_tokens = estimate_tokens(json.dumps(fallback, ensure_ascii=False))
            log_epic_generation(
                db,
                action="reference_section_analysis_fallback",
                role="reference",
                prompt="reference section analysis fallback",
                completion=json.dumps({"sourceSectionId": section_row.source_section_id, "error": error}, ensure_ascii=False),
                status="error",
                error=error,
            )
    else:
        analysis_json = fallback
        completion_tokens = estimate_tokens(json.dumps(fallback, ensure_ascii=False))

    if existing is None:
        existing = ReferenceSectionAnalysis(section_id=section_row.id, source_book_id=book.id)
        db.add(existing)
    existing.source_book_id = book.id
    existing.section_hash = section_row.section_hash
    existing.model_provider = provider
    existing.model_name = model_name
    existing.status = status
    existing.analysis_json = analysis_json
    existing.output_hash = _json_hash(analysis_json)
    existing.raw_text_included = False
    prompt_token_count = estimate_tokens(prompt)
    existing.prompt_tokens = prompt_token_count
    existing.completion_tokens = completion_tokens
    existing.estimated_cost = _estimate_reference_model_cost(prompt_token_count, completion_tokens)
    existing.error = error
    section_row.analysis_status = status
    db.flush()
    return existing


def estimate_reference_extraction_cost(db: Session, *, book_id: UUID | None = None) -> dict[str, Any]:
    query = db.query(ReferenceBook)
    if book_id is not None:
        query = query.filter(ReferenceBook.id == book_id)
    books = query.order_by(ReferenceBook.tier, ReferenceBook.title).all()
    total_sections = 0
    total_prompt_tokens = 0
    total_chars = 0
    per_book: list[dict[str, Any]] = []
    for book in books:
        try:
            sections = split_by_chapter(parse_reference_text(book.source_path), limit=settings.EPIC_REFERENCE_INGEST_SECTION_LIMIT)
        except Exception as exc:  # noqa: BLE001
            per_book.append({"bookId": str(book.id), "title": book.title, "error": str(exc)[:300]})
            continue
        prompt_tokens = 0
        chars = 0
        for section in sections:
            chars += len(str(section.get("text") or ""))
            prompt_tokens += estimate_tokens(_build_section_analysis_prompt(book, section))
        total_sections += len(sections)
        total_prompt_tokens += prompt_tokens
        total_chars += chars
        per_book.append(
            {
                "bookId": str(book.id),
                "title": book.title,
                "sections": len(sections),
                "chars": chars,
                "estimatedPromptTokens": prompt_tokens,
            }
        )
    batch_budget = max(1, int(settings.EPIC_REFERENCE_BATCH_TOKEN_BUDGET or 60000))
    return {
        "books": len(books),
        "sections": total_sections,
        "chars": total_chars,
        "estimatedPromptTokens": total_prompt_tokens,
        "batchTokenBudget": batch_budget,
        "estimatedBatches": (total_prompt_tokens + batch_budget - 1) // batch_budget,
        "modelProvider": settings.EPIC_REFERENCE_MODEL_PROVIDER,
        "modelName": settings.EPIC_REFERENCE_MODEL_NAME,
        "estimatedCost": 0.0,
        "perBook": per_book,
    }


def reference_extraction_control_path() -> Path:
    ensure_epic_storage()
    return epic_run_artifact_path() / "reference_extraction_control.json"


def set_reference_extraction_control(state: str) -> dict[str, str]:
    if state not in {"running", "paused", "stopped"}:
        raise EpicReferenceError(f"Unknown extraction control state: {state}")
    path = reference_extraction_control_path()
    payload = {"state": state}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def get_reference_extraction_control() -> dict[str, str]:
    path = reference_extraction_control_path()
    if not path.exists():
        return {"state": "running"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"state": "running"}
    state = data.get("state")
    return {"state": state if state in {"running", "paused", "stopped"} else "running"}


def _card_categories_for_section(book: ReferenceBook, section_index: int) -> list[str]:
    categories = REFERENCE_CARD_CATEGORIES[section_index % len(REFERENCE_CARD_CATEGORIES) : section_index % len(REFERENCE_CARD_CATEGORIES) + 1]
    if section_index < 4:
        categories = [book.category if book.category in REFERENCE_CARD_CATEGORIES else "plot_structure", "pleasure"]
    return categories


def _abstract_card_data(book: ReferenceBook, section: dict[str, Any], category: str, index: int, analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    title = section["title"]
    text = section["text"]
    analysis = analysis or _fallback_section_analysis(book, section)
    length = len(text)
    density = "high" if length > 3500 else "medium" if length > 1200 else "low"
    function_map = {
        "pleasure": analysis.get("pleasureFunction") or "读者爽点节奏",
        "plot_structure": analysis.get("plotFunction") or "小案牵大局结构",
        "character_arc": analysis.get("characterArcMove") or "压力下的角色选择",
        "heroine_arc": analysis.get("heroineAgencyMove") or "独立能动性节点",
        "villain_arc": "对立意志必须有计划、资源和代价",
        "foreshadow": "铺垫、延迟兑现与误导纠偏",
        "volume_structure": "分卷级升级、阶段兑现和新问题转场",
        "power_system": analysis.get("powerSystemMove") or "有边界的能力推进压力",
        "bureaucracy": analysis.get("bureaucracyFunction") or "制度阻力",
        "faction": "群体利益碰撞与公开/隐秘目标分裂",
        "map": "地理作为限制、线索和机会",
        "worldbuilding": "世界规则转化为情节压力",
        "anti_cliche": "; ".join(map(str, analysis.get("antiClicheNotes") or [])) or "避免轻易胜利和解释堆砌",
        "longform_pacing": analysis.get("pacingRole") or "连载节奏中的升级与重置控制",
    }
    section_id = str(section.get("sourceSectionId") or f"section_{index + 1:06d}")
    mechanisms = analysis.get("reusableMechanisms") if isinstance(analysis, dict) else []
    mechanism_names = [
        item.get("name") if isinstance(item, dict) else str(item)
        for item in mechanisms or []
        if item
    ]
    return {
        "pattern_key": f"{book.id}:{category}:{index}",
        "category": category,
        "name": f"{category.replace('_', ' ').title()} 机制 {index + 1}",
        "description": (
            f"从《{title[:40]}》对应的{density}密度片段抽象出的机制卡。"
            f"只可使用结构功能，禁止复制来源文本、人物、场景、桥段或反转。"
        ),
        "genre": book.genre,
        "function": function_map[category],
        "conditions": [f"当章节需要{function_map[category]}时使用", f"必须绑定原创世界规则和角色目标"],
        "beats": mechanism_names[:3] or ["建立压力", "受限选择", "可见后果"],
        "common_pitfalls": list(analysis.get("notToCopy") or [])[:6] or ["复制来源表达", "单本书过拟合", "无代价升级"],
        "best_use": [book.category, f"tier_{book.tier}"],
        "required_global_link": "必须接入项目自己的全局主线、官僚制度或战力规则。",
        "originality_notes": "卡片只保存抽象机制；生成 prompt 不可访问原始参考文本。",
        "tags": [category, book.category, f"tier_{book.tier}"],
        "source_section_ids": [section_id],
        "source_card_ids": [],
        "evidence_count": 1,
        "raw_text_included": False,
        "extraction_model_provider": settings.EPIC_REFERENCE_MODEL_PROVIDER or "deepseek",
        "extraction_model_name": settings.EPIC_REFERENCE_MODEL_NAME or "deepseek-v4-pro",
        "output_hash": _json_hash({"book": str(book.id), "section": section_id, "category": category, "analysis": analysis}),
        "raw_json": {
            "sectionTitle": title,
            "sourceBookId": str(book.id),
            "sourceSectionIds": [section_id],
            "sectionHash": section.get("sectionHash"),
            "abstractOnly": True,
            "rawTextIncluded": False,
            "analysisHash": _json_hash(analysis),
        },
    }


def ingest_reference_book(
    db: Session,
    *,
    book_id: UUID | None = None,
    source_path: str | None = None,
    use_llm_extraction: bool | None = None,
    force_reingest: bool | None = None,
) -> dict[str, Any]:
    if book_id is not None:
        book = db.get(ReferenceBook, book_id)
        if book is None:
            raise EpicReferenceError(f"ReferenceBook not found: {book_id}")
    elif source_path:
        book = import_reference_file(db, file_path=source_path)
    else:
        raise EpicReferenceError("book_id or source_path is required")
    text = parse_reference_text(book.source_path)
    sections = split_by_chapter(text, limit=settings.EPIC_REFERENCE_INGEST_SECTION_LIMIT)
    job = ReferenceIngestionJob(source_path=book.source_path, status="processing", total_files=1)
    db.add(job)
    db.flush()
    generated = 0
    existing_keys: set[str] = set()
    for index, section in enumerate(sections):
        section_row = _ensure_reference_section(db, book, section)
        analysis_row = analyze_reference_section(
            db,
            book=book,
            section=section,
            section_row=section_row,
            use_llm=use_llm_extraction,
            force=force_reingest,
        )
        analysis_json = analysis_row.analysis_json or _fallback_section_analysis(book, section)
        categories = _card_categories_for_section(book, index)
        section_generated = 0
        for category in categories:
            data = _abstract_card_data(book, section, category, index, analysis_json)
            if data["pattern_key"] in existing_keys:
                continue
            if _find_pattern_card_by_key(db, data["pattern_key"]) is not None:
                existing_keys.add(data["pattern_key"])
                continue
            db.add(ReferencePatternCard(source_book_id=book.id, **data))
            existing_keys.add(data["pattern_key"])
            generated += 1
            section_generated += 1
        section_row.card_count = max(section_row.card_count or 0, section_generated)
        section_row.status = "indexed"
    book.status = "indexed"
    if book.download_task is not None:
        book.download_task.generated_card_count = generated
    job.status = "completed"
    job.processed_files = 1
    job.generated_cards = generated
    db.commit()
    log_epic_generation(
        db,
        action="reference_pattern_extract",
        role="cheap",
        prompt=f"abstract-only extraction for {book.title}",
        completion=f"generated_cards={generated}",
    )
    db.commit()
    return {
        "book": {column.name: getattr(book, column.name) for column in book.__table__.columns},
        "generated_cards": generated,
        "sections": len(sections),
        "section_analyses": db.query(ReferenceSectionAnalysis).filter(ReferenceSectionAnalysis.source_book_id == book.id).count(),
    }


def search_pattern_cards(
    db: Session,
    *,
    query: str = "",
    category: str | None = None,
    limit: int = 0,
) -> list[ReferencePatternCard]:
    q = db.query(ReferencePatternCard)
    if category:
        q = q.filter(ReferencePatternCard.category == category)
    ordered = q.order_by(ReferencePatternCard.created_at.desc())
    cards = ordered.limit(max(limit * 4, limit)).all() if limit and limit > 0 else ordered.all()
    if not query:
        return cards[:limit] if limit and limit > 0 else cards
    terms = [term.casefold() for term in re.findall(r"[\w\u4e00-\u9fff]+", query) if len(term) > 1]
    scored: list[tuple[int, ReferencePatternCard]] = []
    for card in cards:
        haystack = " ".join([card.name, card.description, card.category, " ".join(map(str, card.tags or []))]).casefold()
        score = sum(1 for term in terms if term in haystack)
        scored.append((score, card))
    scored.sort(key=lambda item: item[0], reverse=True)
    ranked = [card for _, card in scored]
    return ranked[:limit] if limit and limit > 0 else ranked


def build_reference_mechanism_profile(
    db: Session,
    *,
    project_id: UUID | None = None,
    use_llm_extraction: bool | None = None,
) -> ReferenceMechanismProfile:
    cards = db.query(ReferencePatternCard).all()
    books = {card.source_book_id for card in cards if card.source_book_id}
    by_category: dict[str, list[str]] = {}
    tier_weights: dict[str, int] = {}
    source_card_ids: list[str] = []
    source_book_ids: set[str] = set()
    for card in cards:
        by_category.setdefault(card.category, []).append(card.name)
        source_card_ids.append(str(card.id))
        source = card.source_book
        if source is not None:
            key = f"tier_{source.tier}"
            tier_weights[key] = tier_weights.get(key, 0) + 1
            source_book_ids.add(str(source.id))
    fallback_profile = {
        "corePleasureMechanisms": by_category.get("pleasure", [])[:200],
        "caseStructurePatterns": by_category.get("plot_structure", [])[:200],
        "courtPowerPatterns": by_category.get("faction", [])[:200],
        "bureaucracyConflictPatterns": by_category.get("bureaucracy", [])[:200],
        "heroineAgencyPatterns": by_category.get("heroine_arc", [])[:200],
        "characterArcPatterns": by_category.get("character_arc", [])[:200],
        "foreshadowPatterns": by_category.get("foreshadow", [])[:200],
        "powerProgressionPatterns": by_category.get("power_system", [])[:200],
        "longformPacingPatterns": by_category.get("longform_pacing", [])[:200],
        "antiClicheRules": by_category.get("anti_cliche", [])[:200] or ["禁止无代价升级", "禁止复制参考书人物/机构/桥段", "禁止单本书锚定"],
        "commonPitfalls": ["复制来源文本", "不同书籍来源混淆", "只堆设定不形成章节压力", "爽点无代价"],
        "originalityGuidance": ["只吸收机制层", "每个机制必须绑定原创世界法则/官僚系统/角色目标", "用多书证据抵消单本过拟合"],
    }
    llm_profile = fallback_profile
    llm_status = "completed_local"
    sample_cards = [
        {
            "id": str(card.id),
            "category": card.category,
            "name": card.name,
            "function": card.function,
            "sourceBookId": str(card.source_book_id) if card.source_book_id else None,
            "sourceSectionIds": card.source_section_ids or [],
        }
        for card in cards[: min(len(cards), 240)]
    ]
    use_llm = settings.EPIC_REFERENCE_ENABLE_LLM_EXTRACTION if use_llm_extraction is None else bool(use_llm_extraction)
    if use_llm and cards:
        prompt = get_epic_prompt("reference_mechanism_profile_prompt").format(
            source_book_count=len(books),
            source_card_count=len(cards),
            source_section_count=len(_unique_strings([sid for card in cards for sid in card.source_section_ids or []])),
            tier_weights=json.dumps(tier_weights, ensure_ascii=False),
            category_counts=json.dumps({key: len(value) for key, value in by_category.items()}, ensure_ascii=False),
            sample_cards=json.dumps(sample_cards, ensure_ascii=False),
        )
        try:
            completion = call_epic_model(
                db,
                action="reference_mechanism_profile_generate",
                role="reference",
                prompt=prompt,
                project_id=project_id,
                max_output_tokens=0,
            )
            parsed = _extract_json_object(completion)
            for key, value in fallback_profile.items():
                if key not in parsed or not isinstance(parsed[key], list):
                    parsed[key] = value
            llm_profile = parsed
            llm_status = "completed"
        except Exception as exc:  # noqa: BLE001
            llm_status = "completed_fallback"
            log_epic_generation(
                db,
                action="reference_mechanism_profile_fallback",
                role="reference",
                project_id=project_id,
                prompt="reference mechanism profile fallback",
                completion=json.dumps({"error": str(exc)[:1000]}, ensure_ascii=False),
                status="error",
                error=str(exc)[:1000],
            )
    profile = ReferenceMechanismProfile(
        project_id=project_id,
        profile_name="Epic Novel Corpus Mechanism Profile",
        source_book_count=len(books),
        source_card_count=len(cards),
        genre="ancient_transmigration_mystery_xianxia",
        tier_weights=tier_weights,
        core_pleasure_mechanisms=llm_profile["corePleasureMechanisms"],
        case_structure_patterns=llm_profile["caseStructurePatterns"],
        court_power_patterns=llm_profile["courtPowerPatterns"],
        bureaucracy_conflict_patterns=llm_profile["bureaucracyConflictPatterns"],
        heroine_agency_patterns=llm_profile["heroineAgencyPatterns"],
        character_arc_patterns=llm_profile["characterArcPatterns"],
        foreshadow_patterns=llm_profile["foreshadowPatterns"],
        power_progression_patterns=llm_profile["powerProgressionPatterns"],
        longform_pacing_patterns=llm_profile["longformPacingPatterns"],
        anti_cliche_rules=llm_profile["antiClicheRules"],
        common_pitfalls=llm_profile["commonPitfalls"],
        originality_guidance=llm_profile["originalityGuidance"],
        raw_json={
            "categories": {key: len(value) for key, value in by_category.items()},
            "abstractOnly": True,
            "rawTextIncluded": False,
            "language": settings.EPIC_REFERENCE_EXTRACTION_LANGUAGE,
            "referenceModelProvider": settings.EPIC_REFERENCE_MODEL_PROVIDER,
            "referenceModelName": settings.EPIC_REFERENCE_MODEL_NAME,
            "generationStatus": llm_status,
            "sourceBookIds": sorted(source_book_ids),
            "sourceCardIdsSample": source_card_ids[:1000],
            "sourceCardCount": len(source_card_ids),
            "profileVersion": _json_hash(
                {
                    "books": sorted(source_book_ids),
                    "cards": source_card_ids,
                    "categoryCounts": {key: len(value) for key, value in by_category.items()},
                }
            ),
        },
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    log_epic_generation(
        db,
        action="reference_mechanism_profile",
        role="editor",
        project_id=project_id,
        prompt="aggregate ReferencePatternCards",
        completion=f"cards={len(cards)} books={len(books)}",
    )
    db.commit()
    _write_profile_artifact(profile)
    return profile


def latest_reference_profile(db: Session, *, project_id: UUID | None = None) -> ReferenceMechanismProfile | None:
    q = db.query(ReferenceMechanismProfile)
    if project_id is not None:
        q = q.filter((ReferenceMechanismProfile.project_id == project_id) | (ReferenceMechanismProfile.project_id.is_(None)))
    return q.order_by(ReferenceMechanismProfile.created_at.desc()).first()


def _write_profile_artifact(profile: ReferenceMechanismProfile) -> None:
    out_dir = reference_processed_path() / "mechanism_profiles"
    out_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "id": str(profile.id),
        "profileName": profile.profile_name,
        "sourceBookCount": profile.source_book_count,
        "sourceCardCount": profile.source_card_count,
        "tierWeights": profile.tier_weights,
        "antiClicheRules": profile.anti_cliche_rules,
        "originalityGuidance": profile.originality_guidance,
        "rawTextIncluded": False,
        "referenceModelProvider": (profile.raw_json or {}).get("referenceModelProvider"),
        "referenceModelName": (profile.raw_json or {}).get("referenceModelName"),
        "profileVersion": (profile.raw_json or {}).get("profileVersion"),
    }
    (out_dir / f"{profile.id}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Gate 2A Reference Intelligence implementation.
#
# The functions below intentionally keep the public names used by the existing
# API/service layer. Python resolves globals at call time, so this block upgrades
# the reference workflow without touching the short-form generation system.


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _unique_strings(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    source = values if isinstance(values, list) else [values]
    for value in source:
        if value is None:
            continue
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _plain_text_key(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip().casefold()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


def _analysis_is_production_ready(row: ReferenceSectionAnalysis | None) -> bool:
    if row is None or row.status != "success" or row.raw_text_included:
        return False
    data = row.analysis_json or {}
    if not isinstance(data, dict):
        return False
    return (
        data.get("production_quality") is True
        and data.get("rawTextIncluded") is False
        and bool(data.get("sourceBookId"))
        and bool(data.get("sourceSectionId"))
        and _mostly_chinese_text(data)
        and not _contains_forbidden_english_label(data)
    )


def _card_is_production_ready(card: ReferencePatternCard) -> bool:
    raw = card.raw_json or {}
    return (
        card.raw_text_included is False
        and raw.get("rawTextIncluded") is False
        and raw.get("production_quality") is True
        and bool(card.source_section_ids)
        and bool(getattr(card, "source_analysis_ids", None))
        and bool(getattr(card, "source_book_ids", None) or card.source_book_id)
        and not _contains_forbidden_english_label(
            {
                "name": card.name,
                "function": card.function,
                "description": card.description,
                "raw": raw,
            }
        )
    )


def _production_ready_card_query(db: Session):
    # Gate 2B can coexist with a large legacy card table. Narrow the DB query to
    # cards that have the new source metadata before applying the stricter
    # Python-side production-quality predicate.
    return (
        db.query(ReferencePatternCard)
        .filter(ReferencePatternCard.raw_text_included.is_(False))
        .filter(ReferencePatternCard.output_hash.isnot(None))
        .filter(ReferencePatternCard.extraction_model_provider.isnot(None))
        .filter(ReferencePatternCard.evidence_count > 0)
        .filter(ReferencePatternCard.source_section_ids.isnot(None))
        .filter(ReferencePatternCard.source_analysis_ids.isnot(None))
        .filter(ReferencePatternCard.source_book_ids.isnot(None))
    )


def _production_ready_cards(db: Session) -> list[ReferencePatternCard]:
    return [card for card in _production_ready_card_query(db).yield_per(500) if _card_is_production_ready(card)]


def _find_pattern_card_by_key(db: Session, pattern_key: str) -> ReferencePatternCard | None:
    return db.query(ReferencePatternCard).filter(ReferencePatternCard.pattern_key == pattern_key).first()


def _old_source_less_card_count(db: Session) -> int:
    return db.query(ReferencePatternCard).filter(
        or_(
            ReferencePatternCard.output_hash.is_(None),
            ReferencePatternCard.source_section_ids.is_(None),
            ReferencePatternCard.source_analysis_ids.is_(None),
            ReferencePatternCard.source_book_ids.is_(None),
        )
    ).count()


def _card_book_ids(card: ReferencePatternCard) -> list[str]:
    ids = _unique_strings(getattr(card, "source_book_ids", None) or [])
    if card.source_book_id:
        ids.append(str(card.source_book_id))
    return _unique_strings(ids)


def _card_merge_key(card: ReferencePatternCard) -> str:
    raw = card.raw_json or {}
    payload = {
        "category": card.category,
        "function": _plain_text_key(card.function),
        "bestUse": _unique_strings(card.best_use or [])[:5],
        "tags": _unique_strings(card.tags or [])[:5],
        "mechanismNames": [
            _plain_text_key(item.get("name") if isinstance(item, dict) else item)
            for item in (raw.get("reusableMechanisms") or [])[:3]
        ],
    }
    return _json_hash(payload)


def _merge_card_group_data(cards: list[ReferencePatternCard]) -> dict[str, Any]:
    first = cards[0]
    source_book_ids: list[str] = []
    source_section_ids: list[str] = []
    source_analysis_ids: list[str] = []
    source_card_ids: list[str] = []
    conditions: list[str] = []
    beats: list[str] = []
    pitfalls: list[str] = []
    best_use: list[str] = []
    tags: list[str] = []
    for card in cards:
        source_book_ids.extend(_card_book_ids(card))
        source_section_ids.extend(_unique_strings(card.source_section_ids or []))
        source_analysis_ids.extend(_unique_strings(getattr(card, "source_analysis_ids", None) or []))
        source_card_ids.append(str(card.id))
        conditions.extend(_unique_strings(card.conditions or []))
        beats.extend(_unique_strings(card.beats or []))
        pitfalls.extend(_unique_strings(card.common_pitfalls or []))
        best_use.extend(_unique_strings(card.best_use or []))
        tags.extend(_unique_strings(card.tags or []))
    source_book_ids = _unique_strings(source_book_ids)
    source_section_ids = _unique_strings(source_section_ids)
    source_analysis_ids = _unique_strings(source_analysis_ids)
    source_card_ids = _unique_strings(source_card_ids)
    label = REFERENCE_CATEGORY_LABELS.get(first.category, "参考机制")
    evidence_count = max(len(source_section_ids), sum(max(1, int(card.evidence_count or 1)) for card in cards))
    raw_json = {
        "production_quality": True,
        "merged": True,
        "mergeStrategy": "category_function_best_use_tags",
        "sourceBookIds": source_book_ids,
        "sourceSectionIds": source_section_ids,
        "sourceAnalysisIds": source_analysis_ids,
        "sourceCardIds": source_card_ids,
        "evidenceCount": evidence_count,
        "rawTextIncluded": False,
        "abstractOnly": True,
        "generatedAt": _utc_now_iso(),
    }
    return {
        "pattern_key": f"gate2a_merged:{_card_merge_key(first)}",
        "category": first.category,
        "name": f"{label}合并机制卡-{len(cards)}源",
        "description": f"由 {len(cards)} 张生产级抽象卡片合并，保留机制层共性和来源证据，不含参考原文。",
        "genre": first.genre,
        "function": first.function,
        "conditions": _unique_strings(conditions)[:12],
        "beats": _unique_strings(beats)[:12],
        "common_pitfalls": _unique_strings(pitfalls)[:12],
        "best_use": _unique_strings(best_use)[:12],
        "required_global_link": "使用时必须绑定项目原创主线、制度压力、世界规则或角色目标。",
        "originality_notes": "合并卡只保留抽象机制，不允许回填或推断任何参考原文。",
        "tags": _unique_strings(tags + ["gate2a", "production_ready", "merged"])[:16],
        "source_section_ids": source_section_ids,
        "source_analysis_ids": source_analysis_ids,
        "source_book_ids": source_book_ids,
        "source_card_ids": source_card_ids,
        "evidence_count": evidence_count,
        "raw_text_included": False,
        "extraction_model_provider": first.extraction_model_provider,
        "extraction_model_name": first.extraction_model_name,
        "output_hash": _json_hash(raw_json),
        "raw_json": raw_json,
    }


def _build_section_analysis_prompt(book: ReferenceBook, section: dict[str, Any]) -> str:
    text_budget_chars = int(settings.EPIC_REFERENCE_BATCH_TOKEN_BUDGET or 60000) * 3
    template = get_epic_prompt("reference_section_analysis_prompt")
    return template.format(
        source_book_id=book.id,
        title=book.title,
        author=book.author or "",
        tier=book.tier,
        category=book.category,
        source_section_id=section.get("sourceSectionId"),
        chapter_title=section.get("title"),
        section_hash=section.get("sectionHash"),
        reference_text=str(section.get("text") or "")[:text_budget_chars],
    )


def _ensure_reference_section(db: Session, book: ReferenceBook, section: dict[str, Any]) -> ReferenceSection:
    text = str(section.get("text") or "")
    section_hash = str(section.get("sectionHash") or _hash_text(text))
    section["sectionHash"] = section_hash
    source_section_id = _stable_source_section_id(book, section)
    section["sourceSectionId"] = source_section_id
    section_index = int(section.get("sectionIndex") or 0)
    row = (
        db.query(ReferenceSection)
        .filter(ReferenceSection.source_book_id == book.id, ReferenceSection.source_section_id == source_section_id)
        .first()
    )
    if row is None:
        row = ReferenceSection(source_book_id=book.id, source_section_id=source_section_id)
        db.add(row)
    row.chapter_title = str(section.get("title") or "opening")[:500]
    row.section_index = section_index
    row.section_hash = section_hash
    row.start_offset = int(section.get("startOffset")) if section.get("startOffset") is not None else None
    row.end_offset = int(section.get("endOffset")) if section.get("endOffset") is not None else None
    row.text_chars = len(text)
    row.token_estimate = estimate_tokens(text)
    row.status = "parsed"
    row.raw_json = {
        "sourceBookId": str(book.id),
        "sourceSectionId": source_section_id,
        "chapterTitle": row.chapter_title,
        "sectionIndex": section_index,
        "sectionHash": section_hash,
        "startOffset": row.start_offset,
        "endOffset": row.end_offset,
        "charCount": row.text_chars,
        "tokenEstimate": row.token_estimate,
        "rawTextIncluded": False,
        "rawTextStored": False,
        "stableIdentity": {
            "bookHash": book.file_hash,
            "sectionHash": section_hash,
        },
    }
    db.flush()
    return row


def analyze_reference_section(
    db: Session,
    *,
    book: ReferenceBook,
    section: dict[str, Any],
    section_row: ReferenceSection,
    use_llm: bool | None = None,
    force: bool | None = None,
) -> ReferenceSectionAnalysis:
    use_llm = settings.EPIC_REFERENCE_ENABLE_LLM_EXTRACTION if use_llm is None else bool(use_llm)
    force = settings.EPIC_REFERENCE_FORCE_REINGEST if force is None else bool(force)
    provider = settings.EPIC_REFERENCE_MODEL_PROVIDER or "deepseek"
    model_name = settings.EPIC_REFERENCE_MODEL_NAME or "deepseek-v4-pro"
    existing = db.query(ReferenceSectionAnalysis).filter(ReferenceSectionAnalysis.section_id == section_row.id).first()
    if existing is not None and not force and settings.EPIC_REFERENCE_CACHE_BY_HASH and existing.section_hash == section_row.section_hash:
        if existing.status == "success" or not settings.EPIC_REFERENCE_RETRY_FAILED_ONLY:
            if isinstance(existing.analysis_json, dict) and not existing.analysis_json.get("sourceAnalysisId"):
                existing.analysis_json = {**existing.analysis_json, "sourceAnalysisId": str(existing.id)}
                existing.analysis_hash = _analysis_hash(existing.analysis_json)
                existing.output_hash = existing.analysis_hash
            section_row.analysis_status = existing.status
            db.flush()
            return existing

    analysis_id = existing.id if existing is not None else uuid4()

    prompt = _build_section_analysis_prompt(book, section)
    fallback = _fallback_section_analysis(book, section)
    fallback["sourceAnalysisId"] = str(analysis_id)
    analysis_json = fallback
    status = "fallback"
    error: str | None = None
    completion_tokens = estimate_tokens(json.dumps(fallback, ensure_ascii=False))

    if use_llm:
        try:
            completion = call_epic_model(
                db,
                action="reference_section_analysis",
                role="reference",
                prompt=prompt,
                max_output_tokens=0,
            )
            parsed = _extract_json_object(completion)
            normalized = _normalize_section_analysis(book, section, parsed)
            normalized["sourceAnalysisId"] = str(analysis_id)
            normalized["modelProvider"] = provider
            normalized["modelName"] = model_name
            normalized["rawTextIncluded"] = False
            analysis_json = normalized
            status = "success" if normalized.get("production_quality") is True else "error"
            if status == "error":
                error = "model_output_not_production_quality"
            completion_tokens = estimate_tokens(completion)
        except Exception as exc:  # noqa: BLE001 - section failures must be resumable.
            error = str(exc)[:1000]
            log_epic_generation(
                db,
                action="reference_section_analysis_fallback",
                role="reference",
                prompt="reference section analysis fallback",
                completion=json.dumps({"sourceSectionId": section_row.source_section_id, "error": error}, ensure_ascii=False),
                status="error",
                error=error,
            )
    else:
        error = "llm_disabled"

    if existing is None:
        existing = ReferenceSectionAnalysis(id=analysis_id, section_id=section_row.id, source_book_id=book.id)
        db.add(existing)

    analysis_hash = _analysis_hash(analysis_json)
    existing.source_book_id = book.id
    existing.section_hash = section_row.section_hash
    existing.model_provider = provider
    existing.model_name = model_name
    existing.status = status
    existing.analysis_json = {
        **analysis_json,
        "analysisHash": analysis_hash,
        "status": status,
        "modelProvider": provider,
        "modelName": model_name,
        "rawTextIncluded": False,
    }
    existing.analysis_hash = analysis_hash
    existing.output_hash = analysis_hash
    existing.raw_text_included = False
    prompt_token_count = estimate_tokens(prompt)
    existing.prompt_tokens = prompt_token_count
    existing.completion_tokens = completion_tokens
    existing.estimated_cost = _estimate_reference_model_cost(prompt_token_count, completion_tokens)
    existing.error = error
    section_row.analysis_status = status
    db.flush()
    return existing


def _abstract_card_data(
    book: ReferenceBook,
    section: dict[str, Any],
    category: str,
    index: int,
    analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    analysis = analysis or _fallback_section_analysis(book, section)
    source_section_id = str(analysis.get("sourceSectionId") or section.get("sourceSectionId") or _stable_source_section_id(book, section))
    source_analysis_id = str(analysis.get("sourceAnalysisId") or "")
    analysis_hash = str(analysis.get("analysisHash") or _analysis_hash(analysis))
    mechanisms = analysis.get("reusableMechanisms") if isinstance(analysis, dict) else []
    mechanism_names = [
        str(item.get("name") if isinstance(item, dict) else item).strip()
        for item in mechanisms or []
        if item
    ]
    mechanism_functions = [
        str(item.get("function")).strip()
        for item in mechanisms or []
        if isinstance(item, dict) and item.get("function")
    ]
    function_map = {
        "pleasure": analysis.get("pleasureFunction") or "通过压力、反转和兑现形成读者爽点。",
        "plot_structure": analysis.get("plotFunction") or "把局部问题推进成长期主线压力。",
        "character_arc": analysis.get("characterArcMove") or "让角色在选择代价中完成阶段变化。",
        "heroine_arc": analysis.get("heroineAgencyMove") or "让女性角色以独立目标和资源参与主线。",
        "villain_arc": "反派压力来自目标、资源、制度位置和可见代价。",
        "foreshadow": "线索、误导、延迟解释和阶段回收形成长期悬念。",
        "volume_structure": "以阶段目标、阶段兑现和新问题承接分卷节奏。",
        "power_system": analysis.get("powerSystemMove") or "能力推进必须受规则、代价和社会承认约束。",
        "bureaucracy": analysis.get("bureaucracyCourtFactionFunction") or "制度流程与派系利益制造情节阻力。",
        "faction": analysis.get("bureaucracyCourtFactionFunction") or "势力目标冲突推动公开与隐性博弈。",
        "map": "地点作为限制、线索、资源和风险的组织方式。",
        "worldbuilding": "世界规则必须转化为角色可感知的压力和机会。",
        "anti_cliche": "避免复制来源表达、无代价胜利、单本过拟合和解释堆砌。",
        "longform_pacing": analysis.get("pacingRole") or "在兑现后保留未解问题，维持连载推进。",
    }
    label = REFERENCE_CATEGORY_LABELS.get(category, "参考机制")
    title = str(section.get("title") or "未命名章节")
    production_quality = analysis.get("production_quality") is True and analysis.get("rawTextIncluded") is False
    raw_json = {
        "sectionTitle": title,
        "sourceBookId": str(book.id),
        "sourceBookIds": [str(book.id)],
        "sourceSectionIds": [source_section_id],
        "sourceAnalysisIds": [source_analysis_id] if source_analysis_id else [],
        "sectionHash": section.get("sectionHash") or analysis.get("sectionHash"),
        "analysisHash": analysis_hash,
        "abstractOnly": True,
        "rawTextIncluded": False,
        "production_quality": production_quality,
        "generatedBy": "ReferenceSectionAnalysis",
        "reusableMechanisms": mechanisms or [],
        "generatedAt": _utc_now_iso(),
    }
    return {
        "pattern_key": f"gate2a:{book.id}:{source_section_id}:{analysis_hash[:16]}:{category}",
        "category": category,
        "name": f"{label}机制卡-{index + 1:04d}",
        "description": f"从《{book.title}》的“{title[:40]}”成功分节分析中抽象出的机制卡；仅保留机制层证据，不含参考原文。",
        "genre": book.genre,
        "function": str(function_map[category]),
        "conditions": [
            f"当前章节需要调用{label}时使用",
            "必须绑定项目原创世界规则、官僚制度、角色目标或主线谜团",
        ],
        "beats": _unique_strings(mechanism_names + mechanism_functions)[:6] or ["建立压力", "制造受限选择", "兑现后留下新问题"],
        "common_pitfalls": _unique_strings(_as_list(analysis.get("notToCopyNotes")) + _as_list(analysis.get("antiClicheNotes")))[:8]
        or ["不得复制来源表达", "不得照搬角色、机构、案件、桥段或反转", "不得把单本书当作风格锚点"],
        "best_use": _unique_strings([book.category, f"tier_{book.tier}", label, "gate2a"]),
        "required_global_link": "必须接入项目自己的全局主线、官僚系统、战力规则或角色目标。",
        "originality_notes": "卡片只保留抽象机制；生成 prompt 不可访问原始参考文本。",
        "tags": _unique_strings([category, book.category, f"tier_{book.tier}", "gate2a", "production_ready"]),
        "source_section_ids": [source_section_id],
        "source_analysis_ids": [source_analysis_id] if source_analysis_id else [],
        "source_book_ids": [str(book.id)],
        "source_card_ids": [],
        "evidence_count": 1,
        "raw_text_included": False,
        "extraction_model_provider": settings.EPIC_REFERENCE_MODEL_PROVIDER or "deepseek",
        "extraction_model_name": settings.EPIC_REFERENCE_MODEL_NAME or "deepseek-v4-pro",
        "output_hash": _json_hash(raw_json),
        "raw_json": raw_json,
    }


def ingest_reference_book(
    db: Session,
    *,
    book_id: UUID | None = None,
    source_path: str | None = None,
    use_llm_extraction: bool | None = None,
    force_reingest: bool | None = None,
) -> dict[str, Any]:
    if book_id is not None:
        book = db.get(ReferenceBook, book_id)
        if book is None:
            raise EpicReferenceError(f"ReferenceBook not found: {book_id}")
    elif source_path:
        book = import_reference_file(db, file_path=source_path)
    else:
        raise EpicReferenceError("book_id or source_path is required")

    text = parse_reference_text(book.source_path)
    sections = split_by_chapter(text, limit=settings.EPIC_REFERENCE_INGEST_SECTION_LIMIT)
    job = ReferenceIngestionJob(source_path=book.source_path, status="processing", total_files=1)
    db.add(job)
    db.flush()
    generated = 0
    successful_analyses = 0
    fallback_analyses = 0
    error_analyses = 0
    existing_keys: set[str] = set()
    for index, section in enumerate(sections):
        control = get_reference_extraction_control().get("state", "running")
        if control in {"paused", "stopped"}:
            job.status = control
            break
        section_row = _ensure_reference_section(db, book, section)
        analysis_row = analyze_reference_section(
            db,
            book=book,
            section=section,
            section_row=section_row,
            use_llm=use_llm_extraction,
            force=force_reingest,
        )
        if analysis_row.status == "success":
            successful_analyses += 1
        elif analysis_row.status == "fallback":
            fallback_analyses += 1
        else:
            error_analyses += 1
        section_generated = 0
        if _analysis_is_production_ready(analysis_row):
            analysis_json = analysis_row.analysis_json or {}
            for category in _card_categories_for_section(book, index):
                data = _abstract_card_data(book, section, category, index, analysis_json)
                if data["pattern_key"] in existing_keys:
                    continue
                if _find_pattern_card_by_key(db, data["pattern_key"]) is not None:
                    existing_keys.add(data["pattern_key"])
                    continue
                db.add(ReferencePatternCard(source_book_id=book.id, **data))
                existing_keys.add(data["pattern_key"])
                generated += 1
                section_generated += 1
        section_row.card_count = section_generated
        section_row.status = "indexed" if section_generated else "analyzed"
    if job.status == "processing":
        job.status = "completed"
    book.status = "indexed" if generated else "analyzed"
    if book.download_task is not None:
        book.download_task.generated_card_count = generated
    job.processed_files = 1
    job.generated_cards = generated
    db.commit()
    log_epic_generation(
        db,
        action="reference_pattern_extract",
        role="cheap",
        prompt=f"section-aware abstract-only extraction for {book.title}",
        completion=json.dumps(
            {
                "generatedCards": generated,
                "successfulAnalyses": successful_analyses,
                "fallbackAnalyses": fallback_analyses,
                "errorAnalyses": error_analyses,
            },
            ensure_ascii=False,
        ),
    )
    db.commit()
    return {
        "book": {column.name: getattr(book, column.name) for column in book.__table__.columns},
        "generated_cards": generated,
        "sections": len(sections),
        "successful_section_analyses": successful_analyses,
        "fallback_section_analyses": fallback_analyses,
        "error_section_analyses": error_analyses,
        "production_ready_cards": len(
            [
                card
                for card in db.query(ReferencePatternCard).filter(ReferencePatternCard.source_book_id == book.id).all()
                if _card_is_production_ready(card)
            ]
        ),
        "section_analyses": db.query(ReferenceSectionAnalysis).filter(ReferenceSectionAnalysis.source_book_id == book.id).count(),
    }


def search_pattern_cards(
    db: Session,
    *,
    query: str = "",
    category: str | None = None,
    limit: int = 0,
    production_ready_only: bool = False,
) -> list[ReferencePatternCard]:
    q = _production_ready_card_query(db) if production_ready_only else db.query(ReferencePatternCard)
    if category:
        q = q.filter(ReferencePatternCard.category == category)
    if production_ready_only and not query and limit and limit > 0:
        candidates = q.order_by(ReferencePatternCard.created_at.desc()).limit(max(limit * 4, limit)).all()
    else:
        candidates = q.order_by(ReferencePatternCard.created_at.desc()).all()
    cards = candidates
    if production_ready_only:
        cards = [card for card in cards if _card_is_production_ready(card)]
    if not query:
        return cards[:limit] if limit and limit > 0 else cards
    terms = [term.casefold() for term in re.findall(r"[\w\u4e00-\u9fff]+", query) if len(term) > 1]
    scored: list[tuple[int, ReferencePatternCard]] = []
    for card in cards:
        haystack = " ".join([card.name, card.description, card.category, " ".join(map(str, card.tags or []))]).casefold()
        score = sum(1 for term in terms if term in haystack)
        scored.append((score, card))
    scored.sort(key=lambda item: item[0], reverse=True)
    ranked = [card for _, card in scored]
    return ranked[:limit] if limit and limit > 0 else ranked


def dedupe_production_pattern_cards(db: Session, *, card_ids: list[UUID] | None = None) -> dict[str, Any]:
    candidates = _production_ready_cards(db)
    if card_ids:
        allowed = {str(item) for item in card_ids}
        candidates = [card for card in candidates if str(card.id) in allowed or str((card.raw_json or {}).get("sourceCardIds", "")) in allowed]
    groups: dict[str, list[ReferencePatternCard]] = {}
    for card in candidates:
        if (card.raw_json or {}).get("merged") is True:
            continue
        groups.setdefault(_card_merge_key(card), []).append(card)
    merged = 0
    merged_source_cards = 0
    for key, group in groups.items():
        if len(group) < 2:
            continue
        data = _merge_card_group_data(group)
        existing = db.query(ReferencePatternCard).filter(ReferencePatternCard.pattern_key == data["pattern_key"]).first()
        if existing is None:
            existing = ReferencePatternCard(source_book_id=None, **data)
            db.add(existing)
            merged += 1
        else:
            for field, value in data.items():
                setattr(existing, field, value)
        merged_source_cards += len(group)
    db.commit()
    return {
        "candidateCards": len(candidates),
        "mergeGroups": sum(1 for group in groups.values() if len(group) > 1),
        "mergedCardsCreatedOrUpdated": merged,
        "mergedSourceCards": merged_source_cards,
        "productionReadyCardsAfterDedupe": len(_production_ready_cards(db)),
    }


def _profile_raw_json_payload(
    *,
    generation_status: str,
    source_book_ids: list[str],
    source_card_ids: list[str],
    source_section_ids: list[str],
    categories: dict[str, int],
    evidence_counts: list[int],
    production_ready_only: bool,
) -> dict[str, Any]:
    return {
        "generationStatus": generation_status,
        "profileVersion": _json_hash(
            {
                "books": source_book_ids,
                "cards": source_card_ids,
                "sections": source_section_ids,
                "categories": categories,
                "productionReadyOnly": production_ready_only,
            }
        ),
        "provider": settings.EPIC_REFERENCE_MODEL_PROVIDER,
        "model": settings.EPIC_REFERENCE_MODEL_NAME,
        "referenceModelProvider": settings.EPIC_REFERENCE_MODEL_PROVIDER,
        "referenceModelName": settings.EPIC_REFERENCE_MODEL_NAME,
        "sourceBookIds": source_book_ids,
        "sourceBookCount": len(source_book_ids),
        "sourceCardCount": len(source_card_ids),
        "sourceCardIdsSample": source_card_ids[:1000],
        "sourceSectionIdsSample": source_section_ids[:1000],
        "generatedAt": _utc_now_iso(),
        "rawTextIncluded": False,
        "abstractOnly": True,
        "buildMode": "production_ready_cards" if production_ready_only else "all_cards",
        "categoriesCovered": sorted(categories),
        "categories": categories,
        "evidenceCounts": {
            "min": min(evidence_counts) if evidence_counts else 0,
            "max": max(evidence_counts) if evidence_counts else 0,
            "total": sum(evidence_counts),
            "cardCount": len(evidence_counts),
        },
        "productionReadyOnly": production_ready_only,
        "productionReadyCardCount": len(source_card_ids) if production_ready_only else None,
        "oldSourceLessCardsExcluded": bool(production_ready_only),
    }


def build_reference_mechanism_profile(
    db: Session,
    *,
    project_id: UUID | None = None,
    use_llm_extraction: bool | None = None,
    production_ready_only: bool = False,
) -> ReferenceMechanismProfile:
    cards = _production_ready_cards(db) if production_ready_only else db.query(ReferencePatternCard).all()
    by_category: dict[str, list[str]] = {}
    tier_weights: dict[str, int] = {}
    source_card_ids: list[str] = []
    source_book_ids: list[str] = []
    source_section_ids: list[str] = []
    evidence_counts: list[int] = []
    for card in cards:
        card_text = str(card.function or card.name)
        by_category.setdefault(card.category, []).append(card_text)
        source_card_ids.append(str(card.id))
        source_book_ids.extend(_card_book_ids(card))
        source_section_ids.extend(_unique_strings(card.source_section_ids or []))
        evidence_counts.append(max(1, int(card.evidence_count or 1)))
        source = card.source_book
        if source is not None:
            key = f"tier_{source.tier}"
            tier_weights[key] = tier_weights.get(key, 0) + 1
    source_book_ids = _unique_strings(source_book_ids)
    source_section_ids = _unique_strings(source_section_ids)
    source_card_ids = _unique_strings(source_card_ids)
    fallback_profile = {
        "corePleasureMechanisms": by_category.get("pleasure", [])[:200] or ["通过压力、反转、代价和阶段兑现形成读者爽点。"],
        "caseStructurePatterns": by_category.get("plot_structure", [])[:200] or ["局部问题必须牵引更大主线，而不是只解决眼前事件。"],
        "courtPowerPatterns": (by_category.get("faction", []) + by_category.get("bureaucracy", []))[:200]
        or ["官僚流程、派系利益和制度身份共同制造可持续阻力。"],
        "bureaucracyConflictPatterns": by_category.get("bureaucracy", [])[:200] or ["程序、档案、职权和规章要成为情节压力源。"],
        "heroineAgencyPatterns": by_category.get("heroine_arc", [])[:200] or ["女性角色必须拥有独立目标、资源、判断和后果。"],
        "characterArcPatterns": by_category.get("character_arc", [])[:200] or ["人物变化来自受限选择及其代价。"],
        "foreshadowPatterns": by_category.get("foreshadow", [])[:200] or ["线索应可验证、可误导，并在后续以新代价回收。"],
        "powerProgressionPatterns": by_category.get("power_system", [])[:200] or ["战力推进必须受规则、代价、资源和社会承认限制。"],
        "longformPacingPatterns": by_category.get("longform_pacing", [])[:200] or ["每次阶段兑现后必须保留新问题和下一步压力。"],
        "antiClicheRules": by_category.get("anti_cliche", [])[:200]
        or ["禁止复制参考书人物、机构、桥段、反转或原文表达。", "禁止无代价升级和天降便利。", "禁止把单本书作为风格锚点。"],
        "commonPitfalls": ["复制来源文本", "证据来源混杂但不保留来源 ID", "只堆设定不制造章节压力", "爽点无代价"],
        "originalityGuidance": ["只吸收机制层", "每个机制必须绑定原创世界规则、官僚系统或角色目标", "用多书证据抵消单本过拟合"],
    }
    llm_profile = fallback_profile
    llm_status = "local_summary"
    sample_cards = [
        {
            "id": str(card.id),
            "category": card.category,
            "name": card.name,
            "function": card.function,
            "sourceBookIds": _card_book_ids(card),
            "sourceSectionIds": card.source_section_ids or [],
            "sourceAnalysisIds": getattr(card, "source_analysis_ids", None) or [],
            "evidenceCount": card.evidence_count,
        }
        for card in cards[: min(len(cards), 240)]
    ]
    use_llm = settings.EPIC_REFERENCE_ENABLE_LLM_EXTRACTION if use_llm_extraction is None else bool(use_llm_extraction)
    if use_llm and cards:
        prompt = get_epic_prompt("reference_mechanism_profile_prompt").format(
            source_book_count=len(source_book_ids),
            source_card_count=len(cards),
            source_section_count=len(source_section_ids),
            tier_weights=json.dumps(tier_weights, ensure_ascii=False),
            category_counts=json.dumps({key: len(value) for key, value in by_category.items()}, ensure_ascii=False),
            sample_cards=json.dumps(sample_cards, ensure_ascii=False),
        )
        try:
            completion = call_epic_model(
                db,
                action="reference_mechanism_profile_generate",
                role="reference",
                prompt=prompt,
                project_id=project_id,
                max_output_tokens=0,
            )
            parsed = _extract_json_object(completion)
            for key, value in fallback_profile.items():
                if key not in parsed or not isinstance(parsed[key], list):
                    parsed[key] = value
            if _mostly_chinese_text(parsed) and not _contains_forbidden_english_label(parsed):
                llm_profile = parsed
                llm_status = "success"
            else:
                llm_status = "fallback"
        except Exception as exc:  # noqa: BLE001
            llm_status = "fallback"
            log_epic_generation(
                db,
                action="reference_mechanism_profile_fallback",
                role="reference",
                project_id=project_id,
                prompt="reference mechanism profile fallback",
                completion=json.dumps({"error": str(exc)[:1000]}, ensure_ascii=False),
                status="error",
                error=str(exc)[:1000],
            )
    raw_json = _profile_raw_json_payload(
        generation_status=llm_status,
        source_book_ids=source_book_ids,
        source_card_ids=source_card_ids,
        source_section_ids=source_section_ids,
        categories={key: len(value) for key, value in by_category.items()},
        evidence_counts=evidence_counts,
        production_ready_only=production_ready_only,
    )
    profile = ReferenceMechanismProfile(
        project_id=project_id,
        profile_name="长篇小说参考机制画像",
        source_book_count=len(source_book_ids),
        source_card_count=len(source_card_ids),
        genre="ancient_transmigration_mystery_xianxia",
        tier_weights=tier_weights,
        core_pleasure_mechanisms=llm_profile["corePleasureMechanisms"],
        case_structure_patterns=llm_profile["caseStructurePatterns"],
        court_power_patterns=llm_profile["courtPowerPatterns"],
        bureaucracy_conflict_patterns=llm_profile["bureaucracyConflictPatterns"],
        heroine_agency_patterns=llm_profile["heroineAgencyPatterns"],
        character_arc_patterns=llm_profile["characterArcPatterns"],
        foreshadow_patterns=llm_profile["foreshadowPatterns"],
        power_progression_patterns=llm_profile["powerProgressionPatterns"],
        longform_pacing_patterns=llm_profile["longformPacingPatterns"],
        anti_cliche_rules=llm_profile["antiClicheRules"],
        common_pitfalls=llm_profile["commonPitfalls"],
        originality_guidance=llm_profile["originalityGuidance"],
        raw_json=raw_json,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    log_epic_generation(
        db,
        action="reference_mechanism_profile",
        role="editor",
        project_id=project_id,
        prompt="aggregate production-ready ReferencePatternCards",
        completion=f"cards={len(source_card_ids)} books={len(source_book_ids)} sections={len(source_section_ids)}",
    )
    db.commit()
    _write_profile_artifact(profile)
    return profile


def _write_profile_artifact(profile: ReferenceMechanismProfile) -> None:
    out_dir = reference_processed_path() / "mechanism_profiles"
    out_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "id": str(profile.id),
        "profileName": profile.profile_name,
        "sourceBookCount": profile.source_book_count,
        "sourceCardCount": profile.source_card_count,
        "tierWeights": profile.tier_weights,
        "corePleasureMechanisms": profile.core_pleasure_mechanisms,
        "antiClicheRules": profile.anti_cliche_rules,
        "originalityGuidance": profile.originality_guidance,
        "rawTextIncluded": False,
        "rawJson": profile.raw_json or {},
    }
    (out_dir / f"{profile.id}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _deepseek_reference_credentials_available() -> bool:
    return bool((settings.DEEPSEEK_API_KEY or os.getenv("DEEPSEEK_API_KEY", "")).strip())


def _estimate_reference_model_cost(prompt_tokens: int, completion_tokens: int = 0) -> float:
    input_rate = float(os.getenv("EPIC_REFERENCE_INPUT_COST_PER_1K", "0.00014") or 0.00014)
    output_rate = float(os.getenv("EPIC_REFERENCE_OUTPUT_COST_PER_1K", "0.00028") or 0.00028)
    return round((max(0, prompt_tokens) / 1000.0 * input_rate) + (max(0, completion_tokens) / 1000.0 * output_rate), 8)


def _gate2b_should_process_section(
    existing_status: str | None,
    *,
    resume: bool,
    retry_failed_only: bool,
    force: bool,
) -> tuple[bool, str]:
    if force:
        return True, "force"
    if retry_failed_only:
        if existing_status in {"fallback", "error"}:
            return True, "retry_failed"
        return False, "retry_failed_only_skip"
    if resume and existing_status == "success":
        return False, "already_success"
    if resume and existing_status in {"fallback", "error"}:
        return True, "resume_retry_failed"
    return True, "new_or_uncached"


def _gate2b_verdict(metrics: dict[str, Any]) -> str:
    if metrics.get("useLlmExtraction") and not metrics.get("deepseekCredentialsAvailable"):
        return "UNKNOWN_NEEDS_EVIDENCE"
    if metrics.get("dryRun"):
        return "UNKNOWN_NEEDS_EVIDENCE"
    staged_ok = (
        int(metrics.get("cumulativeSuccessfulAnalyses") or 0) > int(metrics.get("gate2aBaselineSuccessfulAnalyses") or 0)
        and int(metrics.get("cumulativeProductionReadyCards") or 0) > int(metrics.get("gate2aBaselineProductionReadyCards") or 0)
        and int(metrics.get("sourceBookIdsPreserved") or 0) >= 2
        and int(metrics.get("sourceSectionIdsPreserved") or 0) > int(metrics.get("gate2aBaselineSourceSections") or 0)
        and int(metrics.get("sourceAnalysisIdsPreserved") or 0) > int(metrics.get("gate2aBaselineSourceAnalyses") or 0)
        and metrics.get("profileRawTextIncluded") is False
        and bool(metrics.get("profileChinese"))
    )
    full_done = (
        int(metrics.get("cumulativeSuccessfulAnalyses") or 0) >= int(metrics.get("estimatedImportedCorpusSections") or 0) > 0
        and int(metrics.get("processedBookCount") or 0) >= int(metrics.get("importedBookCount") or 0) > 0
    )
    if staged_ok and full_done:
        return "PASS"
    if staged_ok:
        return "PARTIAL"
    if metrics.get("failures") or metrics.get("blockers"):
        return "PARTIAL"
    return "FAIL"


def run_gate2a_reference_intelligence_proof(
    db: Session,
    *,
    book_limit: int = 1,
    section_limit: int = 50,
    use_llm_extraction: bool | None = None,
    force: bool = False,
    rebuild_profile: bool = True,
) -> dict[str, Any]:
    book_limit = max(1, int(book_limit or 1))
    section_limit = max(1, int(section_limit or 50))
    use_llm = settings.EPIC_REFERENCE_ENABLE_LLM_EXTRACTION if use_llm_extraction is None else bool(use_llm_extraction)
    books = (
        db.query(ReferenceBook)
        .filter(ReferenceBook.source_path.isnot(None))
        .order_by(ReferenceBook.tier.asc(), ReferenceBook.title.asc())
        .limit(book_limit)
        .all()
    )
    started_at = _utc_now_iso()
    metrics: dict[str, Any] = {
        "gate": "2A",
        "scope": "bounded_reference_intelligence_proof",
        "startedAt": started_at,
        "bookLimit": book_limit,
        "sectionLimit": section_limit,
        "provider": settings.EPIC_REFERENCE_MODEL_PROVIDER,
        "model": settings.EPIC_REFERENCE_MODEL_NAME,
        "deepseekCredentialsAvailable": _deepseek_reference_credentials_available(),
        "useLlmExtraction": use_llm,
        "selectedBooks": [],
        "proofBookCount": len(books),
        "proofSectionCount": 0,
        "referenceSectionRows": 0,
        "referenceSectionAnalysisRows": 0,
        "successfulDeepSeekAnalyses": 0,
        "fallbackAnalyses": 0,
        "errorAnalyses": 0,
        "productionReadyCardsBeforeDedupe": 0,
        "productionReadyCardsAfterDedupe": 0,
        "mergedCardsCreatedOrUpdated": 0,
        "sourceBookIdsPreserved": 0,
        "sourceSectionIdsPreserved": 0,
        "sourceAnalysisIdsPreserved": 0,
        "promptTokens": 0,
        "completionTokens": 0,
        "estimatedCost": 0.0,
        "failures": [],
        "blockers": [],
    }
    if not books:
        metrics["blockers"].append("no_imported_reference_books")
        metrics["verdict"] = "UNKNOWN_NEEDS_EVIDENCE"
        metrics["finishedAt"] = _utc_now_iso()
        return metrics
    if use_llm and not metrics["deepseekCredentialsAvailable"]:
        metrics["blockers"].append("DEEPSEEK_API_KEY 未配置，不能证明 DeepSeek V4 Pro 分节分析")
    selected_analysis_ids: list[str] = []
    selected_card_ids: list[UUID] = []
    remaining = section_limit
    for book in books:
        if remaining <= 0:
            break
        try:
            sections = split_by_chapter(parse_reference_text(book.source_path), limit=remaining)
        except Exception as exc:  # noqa: BLE001
            metrics["failures"].append({"bookId": str(book.id), "title": book.title, "error": str(exc)[:500]})
            continue
        sections = sections[:remaining]
        metrics["selectedBooks"].append({"bookId": str(book.id), "title": book.title, "sections": len(sections)})
        existing_keys: set[str] = set()
        for index, section in enumerate(sections):
            control = get_reference_extraction_control().get("state", "running")
            if control in {"paused", "stopped"}:
                metrics["blockers"].append(f"reference_extraction_control={control}")
                remaining = 0
                break
            section_row = _ensure_reference_section(db, book, section)
            metrics["referenceSectionRows"] += 1
            metrics["proofSectionCount"] += 1
            analysis_row = analyze_reference_section(
                db,
                book=book,
                section=section,
                section_row=section_row,
                use_llm=use_llm,
                force=force,
            )
            selected_analysis_ids.append(str(analysis_row.id))
            metrics["referenceSectionAnalysisRows"] += 1
            metrics["promptTokens"] += int(analysis_row.prompt_tokens or 0)
            metrics["completionTokens"] += int(analysis_row.completion_tokens or 0)
            metrics["estimatedCost"] += float(analysis_row.estimated_cost or 0.0)
            if analysis_row.status == "success":
                metrics["successfulDeepSeekAnalyses"] += 1
            elif analysis_row.status == "fallback":
                metrics["fallbackAnalyses"] += 1
            else:
                metrics["errorAnalyses"] += 1
            section_generated = 0
            if _analysis_is_production_ready(analysis_row):
                for category in _card_categories_for_section(book, index):
                    data = _abstract_card_data(book, section, category, index, analysis_row.analysis_json or {})
                    if data["pattern_key"] in existing_keys:
                        existing_card = db.query(ReferencePatternCard).filter(ReferencePatternCard.pattern_key == data["pattern_key"]).first()
                        if existing_card is not None and _card_is_production_ready(existing_card):
                            selected_card_ids.append(existing_card.id)
                        continue
                    existing_card = _find_pattern_card_by_key(db, data["pattern_key"])
                    if existing_card is not None:
                        if _card_is_production_ready(existing_card):
                            selected_card_ids.append(existing_card.id)
                        existing_keys.add(data["pattern_key"])
                        continue
                    card = ReferencePatternCard(source_book_id=book.id, **data)
                    db.add(card)
                    db.flush()
                    existing_keys.add(data["pattern_key"])
                    selected_card_ids.append(card.id)
                    section_generated += 1
            section_row.card_count = section_generated
            section_row.status = "indexed" if section_generated else "analyzed"
            remaining -= 1
            if remaining <= 0:
                break
        db.commit()
    production_cards_before = _production_ready_cards(db)
    metrics["productionReadyCardsBeforeDedupe"] = len(production_cards_before)
    selected_card_ids.extend([card.id for card in production_cards_before if card.id not in selected_card_ids])
    dedupe_result = dedupe_production_pattern_cards(db, card_ids=selected_card_ids)
    metrics["mergedCardsCreatedOrUpdated"] = dedupe_result["mergedCardsCreatedOrUpdated"]
    metrics["productionReadyCardsAfterDedupe"] = dedupe_result["productionReadyCardsAfterDedupe"]
    production_cards_after = _production_ready_cards(db)
    metrics["sourceBookIdsPreserved"] = len(_unique_strings([book_id for card in production_cards_after for book_id in _card_book_ids(card)]))
    metrics["sourceSectionIdsPreserved"] = len(_unique_strings([section_id for card in production_cards_after for section_id in card.source_section_ids or []]))
    metrics["sourceAnalysisIdsPreserved"] = len(
        _unique_strings([analysis_id for card in production_cards_after for analysis_id in getattr(card, "source_analysis_ids", None) or []])
    )
    if rebuild_profile:
        profile = build_reference_mechanism_profile(db, use_llm_extraction=False, production_ready_only=True)
        raw = profile.raw_json or {}
        metrics["profileId"] = str(profile.id)
        metrics["profileBuildMode"] = "local_from_production_ready_cards"
        metrics["profileVersion"] = raw.get("profileVersion")
        metrics["profileGenerationStatus"] = raw.get("generationStatus")
        metrics["profileSourceBookCount"] = raw.get("sourceBookCount")
        metrics["profileSourceCardCount"] = raw.get("sourceCardCount")
        metrics["profileSourceSectionSampleCount"] = len(raw.get("sourceSectionIdsSample") or [])
        metrics["profileCategoriesCovered"] = raw.get("categoriesCovered") or []
        metrics["profileEvidenceCounts"] = raw.get("evidenceCounts") or {}
        metrics["profileRawTextIncluded"] = raw.get("rawTextIncluded")
        profile_language_payload = {
            "core": profile.core_pleasure_mechanisms,
            "case": profile.case_structure_patterns,
            "court": profile.court_power_patterns,
            "bureaucracy": profile.bureaucracy_conflict_patterns,
            "heroine": profile.heroine_agency_patterns,
            "character": profile.character_arc_patterns,
            "foreshadow": profile.foreshadow_patterns,
            "power": profile.power_progression_patterns,
            "pacing": profile.longform_pacing_patterns,
            "anti": profile.anti_cliche_rules,
            "pitfalls": profile.common_pitfalls,
            "guidance": profile.originality_guidance,
        }
        metrics["profileChinese"] = _mostly_chinese_text(profile_language_payload) and not _contains_forbidden_english_label(profile_language_payload)
    pass_conditions = [
        metrics["referenceSectionRows"] > 0,
        metrics["referenceSectionAnalysisRows"] > 0,
        metrics["successfulDeepSeekAnalyses"] > 0,
        metrics["productionReadyCardsAfterDedupe"] > 0,
        metrics["sourceSectionIdsPreserved"] > 0,
        metrics["sourceAnalysisIdsPreserved"] > 0,
        metrics.get("profileRawTextIncluded") is False,
        bool(metrics.get("profileVersion")),
        bool(metrics.get("profileChinese")),
    ]
    if all(pass_conditions):
        metrics["verdict"] = "PASS"
    elif use_llm and not metrics["deepseekCredentialsAvailable"]:
        metrics["verdict"] = "UNKNOWN_NEEDS_EVIDENCE"
    elif metrics["successfulDeepSeekAnalyses"] <= 0:
        metrics["verdict"] = "UNKNOWN_NEEDS_EVIDENCE" if use_llm else "PARTIAL"
    elif any(pass_conditions):
        metrics["verdict"] = "PARTIAL"
    else:
        metrics["verdict"] = "FAIL"
    metrics["finishedAt"] = _utc_now_iso()
    return metrics


def _reference_section_existing_analysis(
    db: Session,
    *,
    book: ReferenceBook,
    section: dict[str, Any],
) -> tuple[ReferenceSection | None, ReferenceSectionAnalysis | None]:
    section_id = _stable_source_section_id(book, section)
    section_row = (
        db.query(ReferenceSection)
        .filter(ReferenceSection.source_book_id == book.id, ReferenceSection.source_section_id == section_id)
        .first()
    )
    if section_row is None:
        return None, None
    analysis_row = db.query(ReferenceSectionAnalysis).filter(ReferenceSectionAnalysis.section_id == section_row.id).first()
    return section_row, analysis_row


def _summarize_reference_scope(db: Session) -> dict[str, Any]:
    books = db.query(ReferenceBook).order_by(ReferenceBook.tier.asc(), ReferenceBook.title.asc()).all()
    total_sections = 0
    failures: list[dict[str, str]] = []
    for book in books:
        try:
            total_sections += len(split_by_chapter(parse_reference_text(book.source_path)))
        except Exception as exc:  # noqa: BLE001
            failures.append({"bookId": str(book.id), "title": book.title, "error": str(exc)[:300]})
    return {
        "importedBookCount": len(books),
        "estimatedImportedCorpusSections": total_sections,
        "estimateFailures": failures[:20],
    }


def run_gate2b_reference_staged_expansion(
    db: Session,
    *,
    book_limit: int = 3,
    section_limit: int = 200,
    sections_per_book: int = 0,
    tier: int | None = None,
    category: str | None = None,
    resume: bool = True,
    retry_failed_only: bool = False,
    force: bool = False,
    dry_run: bool = False,
    max_cost: float | None = None,
    stop_on_cost_limit: bool = False,
    max_runtime_seconds: int = 0,
    rebuild_profile: bool = True,
    use_llm_extraction: bool | None = None,
    include_full_estimate: bool = True,
) -> dict[str, Any]:
    started_at = _utc_now_iso()
    start_monotonic = time.monotonic()
    book_limit = max(1, int(book_limit or 3))
    section_limit = max(1, int(section_limit or 200))
    sections_per_book = max(0, int(sections_per_book or 0))
    use_llm = settings.EPIC_REFERENCE_ENABLE_LLM_EXTRACTION if use_llm_extraction is None else bool(use_llm_extraction)
    effective_max_cost = max_cost
    if effective_max_cost is None and float(settings.EPIC_MAX_PROJECT_COST or 0.0) > 0:
        effective_max_cost = float(settings.EPIC_MAX_PROJECT_COST)

    query = db.query(ReferenceBook).filter(ReferenceBook.source_path.isnot(None))
    if tier is not None:
        query = query.filter(ReferenceBook.tier == int(tier))
    if category:
        query = query.filter(ReferenceBook.category == category)
    books = query.order_by(ReferenceBook.tier.asc(), ReferenceBook.title.asc()).limit(book_limit).all()

    all_cards_before = db.query(ReferencePatternCard).count()
    production_ready_before = len(_production_ready_cards(db))
    scope_summary = _summarize_reference_scope(db) if include_full_estimate else {
        "importedBookCount": db.query(ReferenceBook).count(),
        "estimatedImportedCorpusSections": 0,
        "estimateFailures": [],
    }
    metrics: dict[str, Any] = {
        "gate": "2B",
        "scope": "reference_intelligence_staged_expansion",
        "startedAt": started_at,
        "bookLimit": book_limit,
        "sectionLimit": section_limit,
        "sectionsPerBook": sections_per_book,
        "tier": tier,
        "category": category,
        "resume": resume,
        "retryFailedOnly": retry_failed_only,
        "force": force,
        "dryRun": dry_run,
        "maxCost": effective_max_cost,
        "stopOnCostLimit": stop_on_cost_limit,
        "maxRuntimeSeconds": max_runtime_seconds,
        "provider": settings.EPIC_REFERENCE_MODEL_PROVIDER,
        "model": settings.EPIC_REFERENCE_MODEL_NAME,
        "deepseekCredentialsAvailable": _deepseek_reference_credentials_available(),
        "useLlmExtraction": use_llm,
        "gate2aBaselineSuccessfulAnalyses": 5,
        "gate2aBaselineProductionReadyCards": 18,
        "gate2aBaselineSourceSections": 5,
        "gate2aBaselineSourceAnalyses": 5,
        "importedBookCount": scope_summary["importedBookCount"],
        "estimatedImportedCorpusSections": scope_summary["estimatedImportedCorpusSections"],
        "fullEstimateFailures": scope_summary["estimateFailures"],
        "selectedBooks": [],
        "candidateBookCount": len(books),
        "candidateSectionCount": 0,
        "processedBookIds": [],
        "processedBookCount": 0,
        "processedSections": 0,
        "createdOrTouchedSections": 0,
        "analysisRowsTouched": 0,
        "successfulAnalysesThisRun": 0,
        "fallbackAnalysesThisRun": 0,
        "errorAnalysesThisRun": 0,
        "skippedAlreadySuccess": 0,
        "skippedRetryOnly": 0,
        "skippedCostLimit": 0,
        "skippedRuntimeLimit": 0,
        "promptTokens": 0,
        "completionTokens": 0,
        "estimatedCost": 0.0,
        "estimatedPromptTokensDryRun": 0,
        "estimatedCostDryRun": 0.0,
        "productionReadyCardsBefore": production_ready_before,
        "allCardsBefore": all_cards_before,
        "newProductionReadyCards": 0,
        "productionReadyCardsAfterDedupe": production_ready_before,
        "cumulativeSuccessfulAnalyses": db.query(ReferenceSectionAnalysis).filter(ReferenceSectionAnalysis.status == "success").count(),
        "cumulativeProductionReadyCards": production_ready_before,
        "sourceBookIdsPreserved": 0,
        "sourceSectionIdsPreserved": 0,
        "sourceAnalysisIdsPreserved": 0,
        "failures": [],
        "blockers": [],
        "stopReason": None,
    }
    if not books:
        metrics["blockers"].append("没有匹配的已导入参考书")
        metrics["verdict"] = "UNKNOWN_NEEDS_EVIDENCE"
        metrics["finishedAt"] = _utc_now_iso()
        return metrics
    if use_llm and not metrics["deepseekCredentialsAvailable"]:
        metrics["blockers"].append("DEEPSEEK_API_KEY 未配置，不能证明 DeepSeek V4 Pro 扩展抽取")

    remaining = section_limit
    selected_card_ids: list[UUID] = []
    processed_book_ids: set[str] = set()
    existing_keys: set[str] = set()

    for book in books:
        if remaining <= 0 or metrics.get("stopReason"):
            break
        try:
            limit_for_book = sections_per_book if sections_per_book > 0 else remaining
            sections = split_by_chapter(parse_reference_text(book.source_path), limit=limit_for_book)
        except Exception as exc:  # noqa: BLE001
            metrics["failures"].append({"bookId": str(book.id), "title": book.title, "error": str(exc)[:500]})
            continue
        if sections_per_book > 0:
            sections = sections[:sections_per_book]
        else:
            sections = sections[:remaining]
        book_summary = {
            "bookId": str(book.id),
            "title": book.title,
            "tier": book.tier,
            "category": book.category,
            "candidateSections": len(sections),
            "processedSections": 0,
            "skippedAlreadySuccess": 0,
            "skippedRetryOnly": 0,
            "failures": [],
        }
        metrics["selectedBooks"].append(book_summary)
        metrics["candidateSectionCount"] += len(sections)
        for index, section in enumerate(sections):
            if remaining <= 0:
                break
            control = get_reference_extraction_control().get("state", "running")
            if control in {"paused", "stopped"}:
                metrics["blockers"].append(f"reference_extraction_control={control}")
                metrics["stopReason"] = control
                break
            if max_runtime_seconds and time.monotonic() - start_monotonic >= max_runtime_seconds:
                metrics["skippedRuntimeLimit"] += 1
                metrics["stopReason"] = "runtime_limit"
                break
            section["sourceSectionId"] = _stable_source_section_id(book, section)
            _, existing_analysis = _reference_section_existing_analysis(db, book=book, section=section)
            existing_status = existing_analysis.status if existing_analysis is not None else None
            should_process, reason = _gate2b_should_process_section(
                existing_status,
                resume=resume,
                retry_failed_only=retry_failed_only,
                force=force,
            )
            prompt_tokens = estimate_tokens(_build_section_analysis_prompt(book, section))
            prompt_cost = _estimate_reference_model_cost(prompt_tokens, 0)
            metrics["estimatedPromptTokensDryRun"] += prompt_tokens
            metrics["estimatedCostDryRun"] = round(float(metrics["estimatedCostDryRun"]) + prompt_cost, 8)
            if not should_process:
                if reason == "already_success":
                    metrics["skippedAlreadySuccess"] += 1
                    book_summary["skippedAlreadySuccess"] += 1
                else:
                    metrics["skippedRetryOnly"] += 1
                    book_summary["skippedRetryOnly"] += 1
                continue
            if effective_max_cost is not None and stop_on_cost_limit:
                projected = float(metrics["estimatedCost"]) + prompt_cost
                if projected > float(effective_max_cost):
                    metrics["skippedCostLimit"] += 1
                    metrics["stopReason"] = "cost_limit"
                    metrics["blockers"].append(
                        f"预计成本 {projected:.8f} 超过 max_cost={float(effective_max_cost):.8f}，已安全停止"
                    )
                    break
            if dry_run:
                remaining -= 1
                continue
            section_row = _ensure_reference_section(db, book, section)
            metrics["createdOrTouchedSections"] += 1
            analysis_row = analyze_reference_section(
                db,
                book=book,
                section=section,
                section_row=section_row,
                use_llm=use_llm,
                force=force,
            )
            metrics["analysisRowsTouched"] += 1
            metrics["promptTokens"] += int(analysis_row.prompt_tokens or 0)
            metrics["completionTokens"] += int(analysis_row.completion_tokens or 0)
            metrics["estimatedCost"] = round(float(metrics["estimatedCost"]) + float(analysis_row.estimated_cost or 0.0), 8)
            if analysis_row.status == "success":
                metrics["successfulAnalysesThisRun"] += 1
            elif analysis_row.status == "fallback":
                metrics["fallbackAnalysesThisRun"] += 1
            else:
                metrics["errorAnalysesThisRun"] += 1
            section_generated = 0
            if _analysis_is_production_ready(analysis_row):
                for card_category in _card_categories_for_section(book, int(section.get("sectionIndex") or index)):
                    data = _abstract_card_data(book, section, card_category, int(section.get("sectionIndex") or index), analysis_row.analysis_json or {})
                    if data["pattern_key"] in existing_keys:
                        existing_card = _find_pattern_card_by_key(db, data["pattern_key"])
                        if existing_card is not None and _card_is_production_ready(existing_card):
                            selected_card_ids.append(existing_card.id)
                        continue
                    existing_card = _find_pattern_card_by_key(db, data["pattern_key"])
                    if existing_card is not None:
                        if _card_is_production_ready(existing_card):
                            selected_card_ids.append(existing_card.id)
                        existing_keys.add(data["pattern_key"])
                        continue
                    card = ReferencePatternCard(source_book_id=book.id, **data)
                    db.add(card)
                    db.flush()
                    existing_keys.add(data["pattern_key"])
                    selected_card_ids.append(card.id)
                    section_generated += 1
            section_row.card_count = section_generated
            section_row.status = "indexed" if section_generated else "analyzed"
            processed_book_ids.add(str(book.id))
            book_summary["processedSections"] += 1
            metrics["processedSections"] += 1
            remaining -= 1
        db.commit()

    if not dry_run:
        production_cards_before_dedupe = _production_ready_cards(db)
        selected_card_ids.extend([card.id for card in production_cards_before_dedupe if card.id not in selected_card_ids])
        dedupe_result = dedupe_production_pattern_cards(db, card_ids=selected_card_ids)
        metrics["productionReadyCardsAfterDedupe"] = dedupe_result["productionReadyCardsAfterDedupe"]
        production_cards_after = _production_ready_cards(db)
        metrics["cumulativeProductionReadyCards"] = len(production_cards_after)
        metrics["newProductionReadyCards"] = max(0, len(production_cards_after) - production_ready_before)
        metrics["cumulativeSuccessfulAnalyses"] = db.query(ReferenceSectionAnalysis).filter(ReferenceSectionAnalysis.status == "success").count()
        metrics["sourceBookIdsPreserved"] = len(_unique_strings([book_id for card in production_cards_after for book_id in _card_book_ids(card)]))
        metrics["sourceSectionIdsPreserved"] = len(_unique_strings([section_id for card in production_cards_after for section_id in card.source_section_ids or []]))
        metrics["sourceAnalysisIdsPreserved"] = len(
            _unique_strings([analysis_id for card in production_cards_after for analysis_id in getattr(card, "source_analysis_ids", None) or []])
        )
        if rebuild_profile:
            profile = build_reference_mechanism_profile(db, use_llm_extraction=False, production_ready_only=True)
            raw = profile.raw_json or {}
            metrics["profileId"] = str(profile.id)
            metrics["profileBuildMode"] = raw.get("buildMode")
            metrics["profileGenerationStatus"] = raw.get("generationStatus")
            metrics["profileVersion"] = raw.get("profileVersion")
            metrics["profileSourceBookCount"] = raw.get("sourceBookCount")
            metrics["profileSourceCardCount"] = raw.get("sourceCardCount")
            metrics["profileSourceSectionSampleCount"] = len(raw.get("sourceSectionIdsSample") or [])
            metrics["profileCategoriesCovered"] = raw.get("categoriesCovered") or []
            metrics["profileEvidenceCounts"] = raw.get("evidenceCounts") or {}
            metrics["profileRawTextIncluded"] = raw.get("rawTextIncluded")
            metrics["profileProductionReadyCardCount"] = raw.get("productionReadyCardCount")
            metrics["profileOldSourceLessCardsExcluded"] = raw.get("oldSourceLessCardsExcluded")
            profile_language_payload = {
                "core": profile.core_pleasure_mechanisms,
                "case": profile.case_structure_patterns,
                "court": profile.court_power_patterns,
                "bureaucracy": profile.bureaucracy_conflict_patterns,
                "heroine": profile.heroine_agency_patterns,
                "character": profile.character_arc_patterns,
                "foreshadow": profile.foreshadow_patterns,
                "power": profile.power_progression_patterns,
                "pacing": profile.longform_pacing_patterns,
                "anti": profile.anti_cliche_rules,
                "pitfalls": profile.common_pitfalls,
                "guidance": profile.originality_guidance,
            }
            metrics["profileChinese"] = _mostly_chinese_text(profile_language_payload) and not _contains_forbidden_english_label(profile_language_payload)

    metrics["processedBookIds"] = sorted(processed_book_ids)
    metrics["processedBookCount"] = len(processed_book_ids)
    metrics["allCardsAfter"] = db.query(ReferencePatternCard).count()
    metrics["oldSourceLessCardsExcluded"] = _old_source_less_card_count(db)
    metrics["remainingImportedBooks"] = max(0, int(metrics["importedBookCount"]) - int(metrics["processedBookCount"]))
    metrics["remainingEstimatedSections"] = max(0, int(metrics["estimatedImportedCorpusSections"] or 0) - int(metrics["cumulativeSuccessfulAnalyses"] or 0))
    metrics["verdict"] = _gate2b_verdict(metrics)
    metrics["finishedAt"] = _utc_now_iso()
    return metrics


def auto_retrieval_plan(
    db: Session,
    *,
    project_id: UUID | None,
    task_type: str,
    query: str,
    volume_number: int | None = None,
    chapter_number: int | None = None,
    section_key: str | None = None,
    limit: int = 0,
) -> dict[str, Any]:
    cards = search_pattern_cards(db, query=query, limit=limit, production_ready_only=True)
    profile = latest_reference_profile(db, project_id=project_id)
    card_ids = [str(card.id) for card in cards]
    excluded_old_cards = db.query(ReferencePatternCard).filter(ReferencePatternCard.source_section_ids == []).count()
    trace = None
    if project_id is not None:
        profile_sections = []
        profile_version = None
        if profile is not None:
            profile_sections = [
                key
                for key in [
                    "core_pleasure_mechanisms",
                    "case_structure_patterns",
                    "bureaucracy_conflict_patterns",
                    "heroine_agency_patterns",
                    "anti_cliche_rules",
                    "originality_guidance",
                ]
                if getattr(profile, key, None)
            ]
            profile_version = str((profile.raw_json or {}).get("profileVersion") or profile.id)
        trace = EpicRetrievalTrace(
            project_id=project_id,
            task_type=task_type,
            volume_number=volume_number,
            chapter_number=chapter_number,
            section_key=section_key,
            query=query,
            retrieved_card_ids=card_ids,
            used_card_ids=card_ids,
            used_profile_sections=profile_sections,
            profile_version=profile_version,
            excluded_context=[],
            raw_text_included=False,
            usage_summary="使用 production-ready 抽象机制卡与机制画像；原始参考文本和旧 source-less 卡均不进入生成 prompt。",
            raw_json={
                "usageSummary": "使用抽象机制卡与机制画像；原始参考文本不进入生成 prompt。",
                "whyUsed": "为 Bible、规划、章节执行和 QA 提供多书机制层参考，避免单本锚定。",
                "rawTextIncluded": False,
                "retrievedCardIds": card_ids,
                "usedCardIds": card_ids,
                "usedProfileSections": profile_sections,
                "profileVersion": profile_version,
                "productionReadyOnly": True,
                "oldSourceLessCardsExcluded": excluded_old_cards,
                "excludedOrOverflow": [],
            },
        )
        db.add(trace)
        db.commit()
    return {
        "shouldRetrieve": bool(cards or profile),
        "retrievalIntents": [
            {
                "intentType": task_type,
                "whyNeeded": "Use corpus-level abstract mechanisms to avoid single-title anchoring.",
                "usageConstraint": "ReferencePatternCards and ReferenceMechanismProfile only; no raw text.",
                "mustNotCopy": True,
            }
        ],
        "query": query,
        "cards": cards,
        "profile": profile,
        "trace": trace,
    }
