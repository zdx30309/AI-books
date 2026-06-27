from __future__ import annotations

import zipfile
from io import BytesIO

from app.services import weread_tools
from app.services.weread_tools import WeReadBookResult, build_weread_xlsx, parse_catalog


def _xlsx_shared_text(content: bytes) -> str:
    with zipfile.ZipFile(BytesIO(content)) as zf:
        return zf.read("xl/sharedStrings.xml").decode("utf-8")


def test_parse_catalog_keeps_weread_link() -> None:
    rows = parse_catalog("https://weread.qq.com/web/reader/aea32010813ab9e28g016851")

    assert len(rows) == 1
    assert rows[0].source_url == "https://weread.qq.com/web/reader/aea32010813ab9e28g016851"


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
    assert result.comment_count == "44"
    assert result.status == "matched"
    assert result.error is None


def test_weread_export_contains_new_metric_and_project_columns() -> None:
    content = build_weread_xlsx(
        [
            {
                "query_title": "黄金投资指南",
                "matched_title": "黄金投资指南",
                "company_name": "汇安",
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

    assert "公司名字" in shared_text
    assert "阅读人数" in shared_text
    assert "评论数量" in shared_text
    assert "项目编辑" in shared_text
    assert "项目书名" in shared_text
    assert "汇安" in shared_text
