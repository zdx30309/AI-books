"""Reference corpus, downloader, RAG, and retrieval planning services."""
from __future__ import annotations

import csv
import html
import json
import re
import shutil
import zipfile
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, build_opener
from uuid import UUID

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
)
from app.services.epic_model_router import log_epic_generation
from app.services.epic_storage import (
    compute_file_hash,
    ensure_epic_storage,
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


class EpicReferenceError(RuntimeError):
    """Raised for expected reference workflow errors."""


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


class SoNovelAdapter:
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
        files = {name: self.home / name for name in ["sonovel.exe", "app.jar", "config.ini", "readme.txt"]}
        dirs = {name: self.home / name for name in ["downloads", "logs", "rules", "runtime"]}
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
            "canInvokeSafely": False,
            "manualRequired": True,
            "manualCommandSuggestion": r".\sonovel.exe -h",
        }
        if home_exists and files["sonovel.exe"].exists() and files["app.jar"].exists():
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
        for path in sorted(self.download_path.glob("*")):
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

    def validate_output(self, path: str | Path) -> dict[str, Any]:
        file_path = Path(path)
        valid = file_path.exists() and file_path.is_file() and file_path.stat().st_size > 0 and file_path.suffix.lower() in {".epub", ".txt", ".md", ".html", ".json"}
        return {
            "path": str(file_path),
            "exists": file_path.exists(),
            "size": file_path.stat().st_size if file_path.exists() else 0,
            "extension": file_path.suffix.lower(),
            "valid": valid,
            "fileHash": compute_file_hash(file_path) if valid else None,
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
    return db.query(ReferenceDownloadTask).order_by(ReferenceDownloadTask.created_at.desc()).limit(limit).all()


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


def split_by_chapter(text: str, limit: int = 0) -> list[dict[str, str]]:
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
    return [section for section in chunked if section["text"]]


def _abstract_card_data(book: ReferenceBook, section: dict[str, str], category: str, index: int) -> dict[str, Any]:
    title = section["title"]
    text = section["text"]
    length = len(text)
    density = "high" if length > 3500 else "medium" if length > 1200 else "low"
    function_map = {
        "pleasure": "reader reward rhythm",
        "plot_structure": "case-to-arc escalation",
        "character_arc": "choice under pressure",
        "heroine_arc": "independent agency beat",
        "villain_arc": "opposing will with a costed plan",
        "foreshadow": "setup and delayed payoff",
        "volume_structure": "volume-scale escalation and resolution",
        "power_system": "bounded progression pressure",
        "bureaucracy": "institutional obstacle",
        "faction": "group interest collision",
        "map": "geography as constraint and opportunity",
        "worldbuilding": "world rule as plot pressure",
        "anti_cliche": "avoid easy victory or exposition dump",
        "longform_pacing": "serial escalation and reset control",
    }
    return {
        "pattern_key": f"{book.id}:{category}:{index}",
        "category": category,
        "name": f"{category.replace('_', ' ').title()} Pattern {index + 1}",
        "description": (
            f"Abstract mechanism inferred from a {density}-density section titled '{title[:40]}'. "
            f"Use only the structural function, never source prose, names, scenes, or twists."
        ),
        "genre": book.genre,
        "function": function_map[category],
        "conditions": [f"Useful when the chapter needs {function_map[category]}"],
        "beats": ["setup pressure", "constrained choice", "visible consequence"],
        "common_pitfalls": ["copying source expression", "single-title overfitting", "unearned escalation"],
        "best_use": [book.category, f"tier_{book.tier}"],
        "required_global_link": "Tie the mechanism to the project's own global spine.",
        "originality_notes": "Card is abstract only; raw reference text is not available to generation prompts.",
        "tags": [category, book.category, f"tier_{book.tier}"],
        "raw_json": {"sectionTitle": title, "sourceBookId": str(book.id), "abstractOnly": True},
    }


def ingest_reference_book(db: Session, *, book_id: UUID | None = None, source_path: str | None = None) -> dict[str, Any]:
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
    existing_keys = {
        row[0]
        for row in db.query(ReferencePatternCard.pattern_key)
        .filter(ReferencePatternCard.source_book_id == book.id)
        .all()
    }
    for index, section in enumerate(sections):
        categories = REFERENCE_CARD_CATEGORIES[index % len(REFERENCE_CARD_CATEGORIES) : index % len(REFERENCE_CARD_CATEGORIES) + 1]
        if index < 4:
            categories = [book.category if book.category in REFERENCE_CARD_CATEGORIES else "plot_structure", "pleasure"]
        for category in categories:
            data = _abstract_card_data(book, section, category, index)
            if data["pattern_key"] in existing_keys:
                continue
            db.add(ReferencePatternCard(source_book_id=book.id, **data))
            existing_keys.add(data["pattern_key"])
            generated += 1
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


def build_reference_mechanism_profile(db: Session, *, project_id: UUID | None = None) -> ReferenceMechanismProfile:
    cards = db.query(ReferencePatternCard).all()
    books = {card.source_book_id for card in cards if card.source_book_id}
    by_category: dict[str, list[str]] = {}
    tier_weights: dict[str, int] = {}
    for card in cards:
        by_category.setdefault(card.category, []).append(card.name)
        source = card.source_book
        if source is not None:
            key = f"tier_{source.tier}"
            tier_weights[key] = tier_weights.get(key, 0) + 1
    profile = ReferenceMechanismProfile(
        project_id=project_id,
        profile_name="Epic Novel Corpus Mechanism Profile",
        source_book_count=len(books),
        source_card_count=len(cards),
        genre="ancient_transmigration_mystery_xianxia",
        tier_weights=tier_weights,
        core_pleasure_mechanisms=by_category.get("pleasure", []),
        case_structure_patterns=by_category.get("plot_structure", []),
        court_power_patterns=by_category.get("faction", []),
        bureaucracy_conflict_patterns=by_category.get("bureaucracy", []),
        heroine_agency_patterns=by_category.get("heroine_arc", []),
        character_arc_patterns=by_category.get("character_arc", []),
        foreshadow_patterns=by_category.get("foreshadow", []),
        power_progression_patterns=by_category.get("power_system", []),
        longform_pacing_patterns=by_category.get("longform_pacing", []),
        anti_cliche_rules=by_category.get("anti_cliche", []) or ["No system-flow shortcut", "No sudden unearned protagonist power-up"],
        common_pitfalls=["Do not copy names/prose/scenes", "Do not anchor on one title", "Do not use raw source text in generation prompts"],
        originality_guidance=["Use mechanism-level patterns only", "Tie every borrowed mechanism to original world law and bureaucracy"],
        raw_json={"categories": {key: len(value) for key, value in by_category.items()}, "abstractOnly": True},
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
    }
    (out_dir / f"{profile.id}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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
    cards = search_pattern_cards(db, query=query, limit=limit)
    profile = latest_reference_profile(db, project_id=project_id)
    card_ids = [str(card.id) for card in cards]
    trace = None
    if project_id is not None:
        trace = EpicRetrievalTrace(
            project_id=project_id,
            task_type=task_type,
            volume_number=volume_number,
            chapter_number=chapter_number,
            section_key=section_key,
            query=query,
            retrieved_card_ids=card_ids,
            used_card_ids=card_ids,
            usage_summary="Use cards as abstract mechanisms only; raw reference text is excluded.",
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
