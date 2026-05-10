"""WeRead recommendation analysis endpoints."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.routers.auth import get_current_user
from app.models import User
from app.services.weread_tools import (
    analyze_weread_books,
    build_weread_xlsx,
    safe_export_filename,
)

router = APIRouter(prefix="/api/v1/weread", tags=["WeRead"])


class WeReadAnalyzeRequest(BaseModel):
    catalog_text: str = Field(..., min_length=1, max_length=20_000)
    profit_text: str | None = Field(default=None, max_length=20_000)


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
    source_url: str | None = None
    profit: float | None = None
    status: str
    error: str | None = None


class WeReadAnalyzeResponse(BaseModel):
    total: int
    matched: int
    items: list[WeReadBookRow]


class WeReadExportRequest(BaseModel):
    rows: list[WeReadBookRow] = Field(..., min_length=1, max_length=100)


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
