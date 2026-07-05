"""WeRead recommendation analysis endpoints."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.routers.auth import get_current_user
from app.models import User
from app.services.weread_tools import (
    analyze_weread_company_books_report,
    analyze_weread_catalog,
    analyze_weread_books,
    build_weread_xlsx,
    catalog_books_to_text,
    parse_catalog_workbook,
    safe_export_filename,
)

router = APIRouter(prefix="/api/v1/weread", tags=["WeRead"])


class WeReadAnalyzeRequest(BaseModel):
    catalog_text: str = Field(..., min_length=1)
    profit_text: str | None = None


class WeReadCompanyRequest(BaseModel):
    company_name: str = Field(..., min_length=1)
    max_books: int | None = Field(default=500, ge=1, le=1000)


class WeReadBookRow(BaseModel):
    query_title: str
    query_author: str | None = None
    matched_title: str | None = None
    matched_author: str | None = None
    recommendation: float | None = None
    rating_count: str | None = None
    today_reading: str | None = None
    total_reading: str | None = None
    word_count: str | None = None
    reading_count: str | None = None
    comment_count: str | None = None
    company_name: str | None = None
    copyright_id: str | None = None
    translator: str | None = None
    source_url: str | None = None
    profit: float | None = None
    project_editor: str | None = None
    project_title: str | None = None
    project_word_count_10k: str | None = None
    project_finished_at: str | None = None
    project_summary: str | None = None
    project_pen_name: str | None = None
    project_new_title: str | None = None
    project_original_author: str | None = None
    project_recommendation: float | None = None
    project_comment_count: str | None = None
    project_tags: str | None = None
    project_reading_count: str | None = None
    project_weread_url: str | None = None
    project_negative_review: str | None = None
    project_parent_record: str | None = None
    project_status: str | None = None
    project_progress: str | None = None
    project_update_date: str | None = None
    project_start_date: str | None = None
    project_time_span: str | None = None
    project_note: str | None = None
    project_source_sheet: str | None = None
    project_match_status: str | None = None
    status: str
    error: str | None = None


class WeReadAnalyzeResponse(BaseModel):
    total: int
    matched: int
    items: list[WeReadBookRow]
    warnings: list[str] = Field(default_factory=list)


class WeReadCatalogItem(BaseModel):
    title: str
    author: str | None = None
    source_url: str | None = None


class WeReadExcelImportResponse(BaseModel):
    total: int
    catalog_text: str
    items: list[WeReadCatalogItem]


class WeReadExportRequest(BaseModel):
    rows: list[WeReadBookRow] = Field(..., min_length=1)


@router.post(
    "/analyze",
    response_model=WeReadAnalyzeResponse,
    summary="批量抓取微信读书推荐值，并按书名匹配利润",
)
async def analyze_books(
    req: WeReadAnalyzeRequest,
    _: User = Depends(get_current_user),
) -> WeReadAnalyzeResponse:
    rows = await analyze_weread_books(req.catalog_text, req.profit_text)
    return _rows_response(rows)


@router.post(
    "/company",
    response_model=WeReadAnalyzeResponse,
    summary="按版权方/公司名抓取微信读书公开书单与推荐值",
)
async def analyze_company_books(
    req: WeReadCompanyRequest,
    _: User = Depends(get_current_user),
) -> WeReadAnalyzeResponse:
    report = await analyze_weread_company_books_report(req.company_name, limit=req.max_books)
    items = [WeReadBookRow(**row.as_dict()) for row in report.rows]
    matched = sum(1 for item in items if item.status in {"matched", "partial"})
    return WeReadAnalyzeResponse(total=len(items), matched=matched, items=items, warnings=report.warnings)


@router.post(
    "/import-excel",
    response_model=WeReadExcelImportResponse,
    summary="上传项目进度 Excel 并快速解析可抓取目录",
)
async def import_books_excel(
    file: UploadFile = File(...),
    _: User = Depends(get_current_user),
) -> WeReadExcelImportResponse:
    catalog = _parse_excel_or_400(file.filename or "", await file.read())
    return WeReadExcelImportResponse(
        total=len(catalog),
        catalog_text=catalog_books_to_text(catalog),
        items=[
            WeReadCatalogItem(title=item.title, author=item.author, source_url=item.source_url)
            for item in catalog
        ],
    )


@router.post(
    "/analyze-excel",
    response_model=WeReadAnalyzeResponse,
    summary="上传项目进度 Excel 并批量抓取微信读书推荐值",
)
async def analyze_books_excel(
    file: UploadFile = File(...),
    _: User = Depends(get_current_user),
) -> WeReadAnalyzeResponse:
    catalog = _parse_excel_or_400(file.filename or "", await file.read())
    if len(catalog) > 60:
        raise HTTPException(
            status_code=400,
            detail=f"Excel 识别到 {len(catalog)} 本书，单次公网请求过长。请使用导入 Excel 抓取，系统会自动分批处理。",
        )
    rows = await analyze_weread_catalog(catalog)
    return _rows_response(rows)


def _parse_excel_or_400(filename: str, content: bytes):
    if not filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="请上传 .xlsx 格式的 Excel 文件")
    try:
        return parse_catalog_workbook(content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _rows_response(rows: list) -> WeReadAnalyzeResponse:
    if not rows:
        raise HTTPException(status_code=400, detail="请至少输入一本书名或微信读书链接")
    items = [WeReadBookRow(**row.as_dict()) for row in rows]
    matched = sum(1 for item in items if item.status in {"matched", "partial"})
    return WeReadAnalyzeResponse(total=len(items), matched=matched, items=items)


@router.post(
    "/export",
    summary="导出微信读书推荐值 Excel",
)
def export_books(
    req: WeReadExportRequest,
    _: User = Depends(get_current_user),
) -> Response:
    content = build_weread_xlsx([row.model_dump() for row in req.rows])
    filename = safe_export_filename()
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
        status_code=status.HTTP_200_OK,
    )
