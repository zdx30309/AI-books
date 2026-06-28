from __future__ import annotations

import asyncio
import zipfile
from io import BytesIO

from openpyxl import Workbook

from app.api.routers.weread import WeReadBookRow, WeReadExportRequest
from app.services import weread_tools
from app.services.weread_tools import (
    CatalogBook,
    WeReadBookResult,
    build_weread_xlsx,
    catalog_books_to_text,
    parse_catalog,
    parse_catalog_workbook,
)


def _xlsx_shared_text(content: bytes) -> str:
    with zipfile.ZipFile(BytesIO(content)) as zf:
        return zf.read("xl/sharedStrings.xml").decode("utf-8")


def test_parse_catalog_keeps_weread_link() -> None:
    rows = parse_catalog("https://weread.qq.com/web/reader/aea32010813ab9e28g016851")

    assert len(rows) == 1
    assert rows[0].source_url == "https://weread.qq.com/web/reader/aea32010813ab9e28g016851"


def test_parse_catalog_uses_book_title_from_copied_table_rows(monkeypatch) -> None:
    monkeypatch.setattr(
        weread_tools,
        "_PROJECT_INDEX_CACHE",
        [{"editor": "单华伟", "_title_key": "", "_url_key": ""}],
    )

    rows = parse_catalog("编辑名\t书名\n单华伟\t肉蒲团（白话版）")

    assert [row.title for row in rows] == ["肉蒲团（白话版）"]


def test_parse_catalog_skips_editor_name_only_lines(monkeypatch) -> None:
    monkeypatch.setattr(
        weread_tools,
        "_PROJECT_INDEX_CACHE",
        [{"editor": "单华伟", "_title_key": "", "_url_key": ""}],
    )

    rows = parse_catalog("单华伟\n肉蒲团（白话版）")

    assert [row.title for row in rows] == ["肉蒲团（白话版）"]


def test_parse_catalog_workbook_supports_project_progress_excel(monkeypatch) -> None:
    monkeypatch.setattr(
        weread_tools,
        "_PROJECT_INDEX_CACHE",
        [{"editor": "单华伟", "_title_key": "", "_url_key": ""}],
    )
    workbook = Workbook()
    ws = workbook.active
    ws.title = "已完成书目"
    ws.append(["编辑名", "书名", "原著作者", "微信读书链接", "推荐值", "阅读"])
    ws.append(["单华伟", "论老年 论友谊", "西塞罗", "https://weread.qq.com/web/reader/book001", 0.8, 623])
    ws.append([None, "论责任", "西塞罗", "https://weread.qq.com/web/reader/book002", 0.9, 176])
    ws.append([None, "单华伟", None, None, None, None])
    month = workbook.create_sheet("202606")
    month.append(["索引字段（导出时备份用，勿动）", "编辑", "书名", "简介", "父记录"])
    month.append(["单华伟", "单华伟", "职业网球到底多烧钱？", "简介", None])
    month.append(["单华伟", "单华伟", "论责任", "重复", None])

    output = BytesIO()
    workbook.save(output)

    rows = parse_catalog_workbook(output.getvalue())

    assert [(row.title, row.author, row.source_url) for row in rows] == [
        ("论老年 论友谊", "西塞罗", "https://weread.qq.com/web/reader/book001"),
        ("论责任", "西塞罗", "https://weread.qq.com/web/reader/book002"),
        ("职业网球到底多烧钱？", None, None),
    ]


def test_catalog_books_to_text_keeps_excel_fields_for_chunked_fetch() -> None:
    text = catalog_books_to_text(
        [
            CatalogBook("论责任", "西塞罗", "https://weread.qq.com/web/reader/book002"),
            CatalogBook("职业网球到底多烧钱？", None, None),
        ]
    )

    assert text.splitlines() == [
        "书名\t原著作者\t微信读书链接",
        "论责任\t西塞罗\thttps://weread.qq.com/web/reader/book002",
        "职业网球到底多烧钱？\t\t",
    ]


def test_project_index_match_adds_company_and_existing_excel_fields(monkeypatch) -> None:
    monkeypatch.setattr(
        weread_tools,
        "_PROJECT_INDEX_CACHE",
        [
            {
                "title": "黄金投资指南",
                "_title_key": weread_tools.normalize_title("黄金投资指南"),
                "project_weread_url": "https://weread.qq.com/web/reader/demo123",
                "_url_key": "reader:demo123",
                "editor": "单华伟",
                "company_name": "汇安",
                "project_recommendation": "0.805",
                "project_comment_count": "44",
                "project_reading_count": "623",
                "source_sheet": "已完成书目",
            }
        ],
    )
    result = WeReadBookResult(
        query_title="https://weread.qq.com/web/reader/demo123?from=share",
        query_author=None,
        source_url="https://weread.qq.com/web/reader/demo123?from=share",
        status="failed",
        error="network failed",
    )

    weread_tools._attach_project_match(result)

    assert result.company_name == "汇安"
    assert result.project_editor == "单华伟"
    assert result.project_match_status == "链接匹配"
    assert result.recommendation == 80.5
    assert result.reading_count == "623"
    assert result.comment_count == "44人点评"
    assert result.status == "matched"
    assert result.error is None


def test_extract_copyright_holder_from_weread_payload() -> None:
    body = '{"publisher":"广西师范大学出版社","copyrightOwner":"","copyrightInfo":{"id":1,"name":"简读互动"},"name":"肉蒲团"}'

    assert weread_tools._extract_copyright_holder(body) == "简读互动"


def test_extract_copyright_holder_from_book_detail_payload() -> None:
    body = 'bookInfo:{publisher:b,copyrightInfo:{id:u,name:"简读互动",userVid:907735518,role:l},title:n}'

    assert weread_tools._extract_copyright_holder(body) == "简读互动"


def test_extract_translator_from_weread_payload() -> None:
    body = '{"translator":"刘半农（译） 单华伟（编）","title":"1793乾隆英使觐见记"}'

    assert weread_tools._extract_translator(body) == "刘半农（译） 单华伟（编）"


def test_extract_nuxt_book_author_from_compressed_payload() -> None:
    body = 'window.__NUXT__=(function(a,b,c){return {data:[{bookInfo:{title:b,author:c}}]}}(0,"35岁以后，别急着认输","陈晨"));</script>'

    assert weread_tools._extract_nuxt_book_field(body, "title") == "35岁以后，别急着认输"
    assert weread_tools._extract_nuxt_book_field(body, "author") == "陈晨"


def test_extract_rating_count_normalizes_people_count() -> None:
    body = """
    <div class="new_book_rating_item_detail_count">
      <span><span class="rating_number_people">23</span>人点评</span>
    </div>
    """

    assert weread_tools._extract_rating_count(body) == "23人点评"
    assert weread_tools._normalize_comment_count("照微") is None
    assert weread_tools._normalize_comment_count("44") == "44人点评"


def test_project_url_key_uses_book_detail_id() -> None:
    url = "https://weread.qq.com/book-detail?type=1&senderVid=1&v=b5c32240813abb90ag01969c&wfrom=app"

    assert weread_tools._project_url_key(url) == "book-detail:b5c32240813abb90ag01969c"


def test_select_card_requires_author_match_when_author_is_provided() -> None:
    cards = [
        {"title": "同名书", "author": "错作者", "source_url": "https://weread.qq.com/web/reader/wrong"},
        {"title": "同名书", "author": "对作者", "source_url": "https://weread.qq.com/web/reader/right"},
    ]

    selected = weread_tools._select_card(cards, CatalogBook("同名书", "对作者"))

    assert selected is not None
    assert selected["source_url"] == "https://weread.qq.com/web/reader/right"
    assert weread_tools._select_card(cards[:1], CatalogBook("同名书", "对作者")) is None


def test_weread_export_contains_only_required_metric_columns() -> None:
    content = build_weread_xlsx(
        [
            {
                "query_title": "黄金投资指南",
                "query_author": "https://weread.qq.com/web/reader/demo123",
                "matched_title": "黄金投资指南",
                "translator": "刘半农（译）",
                "company_name": "广西师范大学出版社",
                "recommendation": 80.5,
                "reading_count": "623",
                "comment_count": "44",
                "project_editor": "单华伟",
                "project_title": "黄金投资指南",
                "project_source_sheet": "已完成书目",
                "project_match_status": "链接匹配",
                "status": "matched",
            }
        ]
    )
    shared_text = _xlsx_shared_text(content)

    assert "原始链接" in shared_text
    assert "原始作者" not in shared_text
    assert "https://weread.qq.com/web/reader/demo123" in shared_text
    assert "版权方（公司名字）" in shared_text
    assert "译者" in shared_text
    assert "阅读人数" in shared_text
    assert "评论数量" in shared_text
    assert "项目编辑" not in shared_text
    assert "项目书名" not in shared_text
    assert "利润" not in shared_text
    assert "来源链接" not in shared_text
    assert "匹配状态" not in shared_text
    assert "备注" not in shared_text
    assert "广西师范大学出版社" in shared_text
    assert "刘半农（译）" in shared_text


def test_weread_export_fills_empty_company_and_bad_comment_count() -> None:
    content = build_weread_xlsx(
        [
            {
                "query_title": "空版权方测试",
                "source_url": "https://weread.qq.com/book-detail?v=demo",
                "matched_title": "空版权方测试",
                "matched_author": "测试作者",
                "company_name": None,
                "recommendation": 0,
                "reading_count": "8人",
                "comment_count": "照微",
                "status": "matched",
            }
        ]
    )
    shared_text = _xlsx_shared_text(content)

    assert "无内容" in shared_text
    assert "0人点评" in shared_text
    assert ">照微<" not in shared_text


def test_analyze_weread_books_does_not_stop_at_80(monkeypatch) -> None:
    async def fake_fetch_search_result(client, item):
        return WeReadBookResult(
            query_title=item.title,
            query_author=item.author,
            matched_title=item.title,
            recommendation=80.0,
            status="matched",
        )

    monkeypatch.setattr(weread_tools, "fetch_search_result", fake_fetch_search_result)
    monkeypatch.setattr(weread_tools, "_attach_project_match", lambda result: None)

    catalog = "\n".join(f"测试书名{i}" for i in range(105))
    rows = asyncio.run(weread_tools.analyze_weread_books(catalog))

    assert len(rows) == 105


def test_weread_export_request_accepts_more_than_100_rows() -> None:
    row = WeReadBookRow(query_title="测试书名", status="matched")

    req = WeReadExportRequest(rows=[row for _ in range(120)])

    assert len(req.rows) == 120
