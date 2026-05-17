"""File-backed standalone cover generation jobs."""
from __future__ import annotations

import csv
import json
import re
import uuid
import zipfile
from datetime import datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from app.core.config import PROJECT_ROOT

COVER_JOBS_ROOT = PROJECT_ROOT / "backend" / "output" / "cover_jobs"
MAX_COVER_ROWS = 100

_TITLE_KEYS = {"书名", "标题", "title", "book_title", "book title", "name"}
_AUTHOR_KEYS = {"作者", "author", "writer"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job_id() -> str:
    return uuid.uuid4().hex


def job_dir(job_id: str) -> Path:
    return COVER_JOBS_ROOT / job_id


def status_path(job_id: str) -> Path:
    return job_dir(job_id) / "status.json"


def rows_path(job_id: str) -> Path:
    return job_dir(job_id) / "rows.json"


def _clean_cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalized_key(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def _row_title(row: dict[str, str]) -> str:
    lowered = {_normalized_key(k): v for k, v in row.items()}
    for key in _TITLE_KEYS:
        value = lowered.get(_normalized_key(key))
        if value:
            return value.strip()
    return ""


def _row_author(row: dict[str, str]) -> str:
    lowered = {_normalized_key(k): v for k, v in row.items()}
    for key in _AUTHOR_KEYS:
        value = lowered.get(_normalized_key(key))
        if value:
            return value.strip()
    return ""


def _with_common_keys(row: dict[str, str]) -> dict[str, str]:
    next_row = dict(row)
    title = _row_title(row)
    author = _row_author(row)
    if title:
        next_row.setdefault("title", title)
    if author:
        next_row.setdefault("author", author)
    return next_row


def _parse_csv(content: bytes) -> list[dict[str, str]]:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(StringIO(text))
    rows: list[dict[str, str]] = []
    for raw in reader:
        row = {str(k).strip(): _clean_cell(v) for k, v in raw.items() if k and str(k).strip()}
        if any(row.values()):
            rows.append(_with_common_keys(row))
    return rows


def _parse_xlsx(content: bytes) -> list[dict[str, str]]:
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    sheet = workbook.active
    iterator = sheet.iter_rows(values_only=True)
    try:
        headers = [_clean_cell(cell) for cell in next(iterator)]
    except StopIteration:
        return []
    headers = [header or f"column_{idx + 1}" for idx, header in enumerate(headers)]
    rows: list[dict[str, str]] = []
    for values in iterator:
        row = {
            headers[idx]: _clean_cell(values[idx] if idx < len(values) else "")
            for idx in range(len(headers))
            if headers[idx]
        }
        if any(row.values()):
            rows.append(_with_common_keys(row))
    return rows


def parse_cover_spreadsheet(filename: str, content: bytes) -> list[dict[str, str]]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        rows = _parse_csv(content)
    elif suffix in {".xlsx", ".xlsm"}:
        rows = _parse_xlsx(content)
    else:
        raise ValueError("请上传 .xlsx、.xlsm 或 .csv 文件")

    valid = [row for row in rows if _row_title(row)]
    if not valid:
        raise ValueError("Excel 至少需要一列书名，列名可为「书名」或 title")
    if len(valid) > MAX_COVER_ROWS:
        raise ValueError(f"一次最多生成 {MAX_COVER_ROWS} 张封面")
    return valid


def safe_image_name(index: int, title: str) -> str:
    safe = re.sub(r'[\\/:*?"<>|\r\n]+', "_", title).strip(" ._") or f"cover_{index}"
    return f"{index:03d}_{safe[:60]}.png"


def read_status(job_id: str) -> dict[str, Any]:
    path = status_path(job_id)
    if not path.exists():
        raise FileNotFoundError(job_id)
    return json.loads(path.read_text(encoding="utf-8"))


def write_status(job_id: str, data: dict[str, Any]) -> None:
    directory = job_dir(job_id)
    directory.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = now_iso()
    tmp = status_path(job_id).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(status_path(job_id))


def create_cover_job(owner_id: str, filename: str, rows: list[dict[str, str]]) -> dict[str, Any]:
    job_id = create_job_id()
    directory = job_dir(job_id)
    directory.mkdir(parents=True, exist_ok=True)
    rows_path(job_id).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    created_at = now_iso()
    items = [
        {
            "index": idx,
            "title": _row_title(row),
            "author": _row_author(row) or None,
            "status": "pending",
            "image_url": None,
            "error": None,
            "metadata": row,
        }
        for idx, row in enumerate(rows, start=1)
    ]
    data = {
        "job_id": job_id,
        "owner_id": owner_id,
        "status": "pending",
        "progress": 0,
        "total": len(rows),
        "completed": 0,
        "failed": 0,
        "current_step": "排队中",
        "source_filename": filename,
        "created_at": created_at,
        "updated_at": created_at,
        "items": items,
    }
    write_status(job_id, data)
    return data


def image_path(job_id: str, index: int, title: str) -> Path:
    return job_dir(job_id) / "images" / safe_image_name(index, title)


def build_zip(job_id: str, status: dict[str, Any]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        written = 0
        for item in status.get("items") or []:
            if item.get("status") != "completed":
                continue
            path = image_path(job_id, int(item["index"]), str(item.get("title") or "cover"))
            if path.exists():
                archive.write(path, arcname=path.name)
                written += 1
        if written == 0:
            raise FileNotFoundError("没有可下载的封面")
    return buffer.getvalue()
