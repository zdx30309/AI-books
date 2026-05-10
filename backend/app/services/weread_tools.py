"""Utilities for WeRead recommendation exports."""
from __future__ import annotations

import csv
import html
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO, StringIO
from typing import Any
from urllib.parse import quote, urljoin
from xml.sax.saxutils import escape

import httpx


WEREAD_HOST = "https://weread.qq.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass
class CatalogBook:
    title: str
    author: str | None = None
    source_url: str | None = None


@dataclass
class WeReadBookResult:
    query_title: str
    query_author: str | None
    matched_title: str | None = None
    matched_author: str | None = None
    recommendation: float | None = None
    rating_count: str | None = None
    today_reading: str | None = None
    total_reading: str | None = None
    word_count: str | None = None
    source_url: str | None = None
    profit: float | None = None
    status: str = "pending"
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "query_title": self.query_title,
            "query_author": self.query_author,
            "matched_title": self.matched_title,
            "matched_author": self.matched_author,
            "recommendation": self.recommendation,
            "rating_count": self.rating_count,
            "today_reading": self.today_reading,
            "total_reading": self.total_reading,
            "word_count": self.word_count,
            "source_url": self.source_url,
            "profit": self.profit,
            "status": self.status,
            "error": self.error,
        }


def normalize_title(value: str | None) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[《》〈〉“”\"'（）()\[\]【】\s·•\-_:：，,。.!！?？]", "", text)
    return text


def parse_catalog(text: str) -> list[CatalogBook]:
    items: list[CatalogBook] = []
    seen: set[str] = set()

    for raw_line in (text or "").splitlines():
        line = raw_line.strip().strip("|")
        if not line:
            continue
        if re.search(r"^(书名|标题|title)(\s|,|，|\t|$)", line, re.I):
            continue

        url_match = re.search(r"https?://[^\s,，]+", line)
        source_url = url_match.group(0) if url_match else None
        clean_line = re.sub(r"^\s*\d+[\.\)、)]\s*", "", line)
        clean_line = re.sub(r"https?://[^\s,，]+", "", clean_line).strip(" ,，")

        title = ""
        author: str | None = None
        bracket_match = re.search(r"《([^》]+)》", clean_line)
        if bracket_match:
            title = bracket_match.group(1).strip()
            rest = clean_line[bracket_match.end() :].strip(" -—,，\t")
            author = rest or None
        else:
            row = _split_row(clean_line)
            if row:
                title = row[0]
                author = row[1] if len(row) > 1 else None
            elif source_url:
                title = source_url

        title = _clean_cell(title)
        author = _clean_cell(author) if author else None
        if not title and source_url:
            title = source_url
        if not title:
            continue

        key = normalize_title(title) or source_url or title
        if key in seen:
            continue
        seen.add(key)
        items.append(CatalogBook(title=title, author=author, source_url=source_url))

    return items


def parse_profit_table(text: str) -> dict[str, float]:
    profits: dict[str, float] = {}
    for raw_line in (text or "").splitlines():
        line = raw_line.strip().strip("|")
        if not line:
            continue
        if re.search(r"^(书名|标题|title).*(利润|profit)", line, re.I):
            continue

        row = _split_row(line)
        title = ""
        profit: float | None = None
        if len(row) >= 2:
            title = row[0]
            profit = _parse_float(row[-1])

        if profit is None:
            match = re.search(r"(.+?)[\s,，:：]+([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(?:元|¥|￥)?$", line)
            if match:
                title = match.group(1).strip()
                profit = _parse_float(match.group(2))

        if title and profit is not None:
            profits[normalize_title(title)] = profit
    return profits


async def analyze_weread_books(
    catalog_text: str,
    profit_text: str | None = None,
    limit: int = 80,
) -> list[WeReadBookResult]:
    catalog = parse_catalog(catalog_text)
    if not catalog:
        return []

    profits = parse_profit_table(profit_text or "")
    timeout = httpx.Timeout(12.0, connect=8.0)
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"}
    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
        results: list[WeReadBookResult] = []
        for item in catalog[:limit]:
            result = WeReadBookResult(query_title=item.title, query_author=item.author)
            try:
                if item.source_url:
                    result = await fetch_reader_result(client, item)
                else:
                    result = await fetch_search_result(client, item)
            except Exception as exc:  # pragma: no cover - defensive network boundary
                result.status = "failed"
                result.error = str(exc)[:240]

            result.profit = _match_profit(profits, result)
            results.append(result)
        return results


async def fetch_search_result(client: httpx.AsyncClient, item: CatalogBook) -> WeReadBookResult:
    url = f"{WEREAD_HOST}/web/search/books?keyword={quote(item.title)}"
    response = await client.get(url)
    response.raise_for_status()
    html_text = response.text
    cards = _parse_search_cards(html_text)
    if not cards:
        return WeReadBookResult(
            query_title=item.title,
            query_author=item.author,
            source_url=url,
            status="not_found",
            error="微信读书搜索页没有返回匹配书籍",
        )

    selected = _select_card(cards, item)
    result = WeReadBookResult(
        query_title=item.title,
        query_author=item.author,
        matched_title=selected.get("title"),
        matched_author=selected.get("author"),
        recommendation=_parse_float(selected.get("recommendation")),
        today_reading=selected.get("today_reading"),
        source_url=selected.get("source_url") or url,
        status="matched",
    )
    return result


async def fetch_reader_result(client: httpx.AsyncClient, item: CatalogBook) -> WeReadBookResult:
    response = await client.get(item.source_url or "")
    response.raise_for_status()
    body = response.text
    title = _first_match(body, [
        r'"name"\s*:\s*"([^"]+)"',
        r'<meta property="og:title" content="([^"]+)"',
        r"<title>(.*?)\s*-\s*.*?微信读书</title>",
    ])
    author = _first_match(body, [
        r'"author"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
        r"<title>.*?-\s*(.*?)\s*-\s*微信读书</title>",
    ])
    recommendation = _first_match(body, [r"微信读书推荐值\s*([0-9.]+%)"])
    rating_count = _first_match(body, [r"book_rating_item_detail_count[^>]*>.*?<span>(.*?)</span>"])
    total_reading = _extract_stat(body, "阅读")
    word_count = _extract_stat(body, "字数")

    clean_title = _clean_text(title) or item.title
    return WeReadBookResult(
        query_title=item.title,
        query_author=item.author,
        matched_title=clean_title,
        matched_author=_clean_text(author) or item.author,
        recommendation=_parse_float(recommendation),
        rating_count=_clean_text(rating_count),
        total_reading=total_reading,
        word_count=word_count,
        source_url=item.source_url,
        status="matched" if recommendation else "partial",
        error=None if recommendation else "已读取书籍页，但未找到推荐值",
    )


def build_weread_xlsx(rows: list[dict[str, Any]]) -> bytes:
    headers = [
        "原始书名",
        "原始作者",
        "匹配书名",
        "匹配作者",
        "微信读书推荐值",
        "今日阅读",
        "点评数",
        "总阅读",
        "字数",
        "利润",
        "匹配状态",
        "来源链接",
        "备注",
    ]
    body_rows = [headers]
    for row in rows:
        body_rows.append(
            [
                row.get("query_title"),
                row.get("query_author"),
                row.get("matched_title"),
                row.get("matched_author"),
                _format_percent(row.get("recommendation")),
                row.get("today_reading"),
                row.get("rating_count"),
                row.get("total_reading"),
                row.get("word_count"),
                row.get("profit"),
                _status_label(str(row.get("status") or "")),
                row.get("source_url"),
                row.get("error"),
            ]
        )

    shared_strings: list[str] = []
    shared_index: dict[str, int] = {}

    def sst_index(value: Any) -> int:
        text = "" if value is None else str(value)
        if text not in shared_index:
            shared_index[text] = len(shared_strings)
            shared_strings.append(text)
        return shared_index[text]

    sheet_rows: list[str] = []
    for r_idx, row in enumerate(body_rows, start=1):
        cells: list[str] = []
        for c_idx, value in enumerate(row, start=1):
            ref = f"{_col_name(c_idx)}{r_idx}"
            if isinstance(value, (int, float)) and value is not None:
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="s"><v>{sst_index(value)}</v></c>')
        sheet_rows.append(f'<row r="{r_idx}">{"".join(cells)}</row>')

    shared_xml = "".join(
        f"<si><t>{escape(value)}</t></si>" for value in shared_strings
    )
    sheet_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <cols>
    <col min="1" max="4" width="24" customWidth="1"/>
    <col min="5" max="5" width="18" customWidth="1"/>
    <col min="6" max="11" width="14" customWidth="1"/>
    <col min="12" max="12" width="48" customWidth="1"/>
    <col min="13" max="13" width="30" customWidth="1"/>
  </cols>
  <sheetData>{"".join(sheet_rows)}</sheetData>
</worksheet>"""

    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _content_types_xml())
        zf.writestr("_rels/.rels", _root_rels_xml())
        zf.writestr("xl/workbook.xml", _workbook_xml())
        zf.writestr("xl/_rels/workbook.xml.rels", _workbook_rels_xml())
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        zf.writestr(
            "xl/sharedStrings.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(shared_strings)}" uniqueCount="{len(shared_strings)}">{shared_xml}</sst>""",
        )
        zf.writestr("xl/styles.xml", _styles_xml())
        zf.writestr("docProps/core.xml", _core_xml())
        zf.writestr("docProps/app.xml", _app_xml())
    return output.getvalue()


def _parse_search_cards(html_text: str) -> list[dict[str, str | None]]:
    cards: list[dict[str, str | None]] = []
    for match in re.finditer(r'<li class="wr_bookList_item">([\s\S]*?)</li>', html_text):
        block = match.group(1)
        href = _first_match(block, [r'<a href="([^"]+)" class="wr_bookList_item_link"'])
        title = _first_match(block, [r'<p class="wr_bookList_item_title">([\s\S]*?)</p>'])
        author = _first_match(block, [r'<p class="wr_bookList_item_author">([\s\S]*?)</p>'])
        recommendation = _first_match(block, [r'wr_bookList_item_reading_percent">([^<]+)</span>'])
        today = _first_match(block, [r'wr_bookList_item_reading_number">([^<]+)</span>人今日阅读'])
        source_url = urljoin(WEREAD_HOST, href) if href else None
        cards.append(
            {
                "title": _clean_text(title),
                "author": _clean_text(author),
                "recommendation": _clean_text(recommendation),
                "today_reading": _clean_text(today),
                "source_url": source_url,
            }
        )
    return cards


def _select_card(cards: list[dict[str, str | None]], item: CatalogBook) -> dict[str, str | None]:
    wanted_title = normalize_title(item.title)
    wanted_author = normalize_title(item.author)
    for card in cards:
        if normalize_title(card.get("title")) == wanted_title:
            if not wanted_author or wanted_author in normalize_title(card.get("author")):
                return card
    for card in cards:
        card_title = normalize_title(card.get("title"))
        if wanted_title and (wanted_title in card_title or card_title in wanted_title):
            return card
    return cards[0]


def _match_profit(profits: dict[str, float], result: WeReadBookResult) -> float | None:
    keys = [
        normalize_title(result.query_title),
        normalize_title(result.matched_title),
    ]
    for key in keys:
        if key and key in profits:
            return profits[key]
    return None


def _split_row(line: str) -> list[str]:
    if not line:
        return []
    normalized = line.replace("，", ",")
    if "," in normalized or "\t" in normalized:
        try:
            row = next(csv.reader(StringIO(normalized)))
        except csv.Error:
            row = re.split(r"[\t,]", normalized)
    else:
        row = re.split(r"\s{2,}|[|｜]", normalized)
    return [_clean_cell(item) for item in row if _clean_cell(item)]


def _clean_cell(value: str | None) -> str:
    return (value or "").strip().strip('"').strip("'").strip()


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", "", value)
    return html.unescape(text).replace("\xa0", " ").strip()


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("%", "").replace(",", "").replace("¥", "").replace("￥", "")
    try:
        return float(text)
    except ValueError:
        return None


def _first_match(text: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.S)
        if match:
            return html.unescape(match.group(1)).strip()
    return None


def _extract_stat(text: str, label: str) -> str | None:
    pattern = (
        rf"{re.escape(label)}[\s\S]{{0,260}}?"
        r'(?:content_number|subTitle_number)[^>]*>([0-9.]+)</span><span[^>]*>([^<]+)</span>'
    )
    match = re.search(pattern, text)
    if match:
        return f"{match.group(1)}{_clean_text(match.group(2))}"
    return None


def _format_percent(value: Any) -> str:
    number = _parse_float(value)
    return "" if number is None else f"{number:.1f}%"


def _status_label(status: str) -> str:
    return {
        "matched": "已匹配",
        "partial": "部分匹配",
        "not_found": "未找到",
        "failed": "抓取失败",
    }.get(status, status or "")


def _col_name(index: int) -> str:
    result = ""
    while index:
        index, rem = divmod(index - 1, 26)
        result = chr(65 + rem) + result
    return result


def safe_export_filename(prefix: str = "微信读书推荐值") -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}.xlsx"


def _content_types_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>"""


def _root_rels_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""


def _workbook_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="推荐值与利润" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""


def _workbook_rels_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
</Relationships>"""


def _styles_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="1"><font><sz val="11"/><name val="Arial"/></font></fonts>
  <fills count="1"><fill><patternFill patternType="none"/></fill></fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def _core_xml() -> str:
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:creator>AI ScriptBook</dc:creator>
  <cp:lastModifiedBy>AI ScriptBook</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>
</cp:coreProperties>"""


def _app_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>AI ScriptBook</Application>
</Properties>"""
