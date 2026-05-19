"""Data analysis spreadsheet endpoints."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.routers.auth import get_current_user
from app.models import User
from app.services.data_analysis import (
    analyze_book_spreadsheets,
    build_data_analysis_xlsx,
    safe_data_analysis_filename,
)

router = APIRouter(prefix="/api/v1/data-analysis", tags=["DataAnalysis"])


class DataAnalysisSummary(BaseModel):
    source_filename: str
    source_filenames: list[str] = Field(default_factory=list)
    report_date: str
    book_count: int
    matched_book_count: int
    company_field_detected: str | None
    file_count: int = 1
    notes: list[str]


class DataAnalysisRow(BaseModel):
    date_key: str
    weekday: str
    ai_duration: int | None = None
    ai_mom: float | None = None
    public_duration: int | None = None
    public_mom: float | None = None
    total_duration: int | None = None
    total_mom: float | None = None
    huian_ai: int | None = None
    huian_public: int | None = None
    wutu_ai: int | None = None
    wutu_public: int | None = None
    suite_duration: int | None = None


class DataAnalysisResponse(BaseModel):
    summary: DataAnalysisSummary
    rows: list[DataAnalysisRow]


def _analyze_or_400(files: list[tuple[str, bytes]]) -> dict:
    try:
        return analyze_book_spreadsheets(files)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/analyze",
    response_model=DataAnalysisResponse,
    summary="上传书籍信息 Excel 并生成数据分析预览",
)
async def analyze_data(
    files: list[UploadFile] = File(...),
    _: User = Depends(get_current_user),
) -> DataAnalysisResponse:
    payload = [(file.filename or f"book_info_{idx}.xlsx", await file.read()) for idx, file in enumerate(files, 1)]
    return DataAnalysisResponse.model_validate(_analyze_or_400(payload))


@router.post(
    "/export",
    summary="导出数据分析 Excel",
)
async def export_data(
    files: list[UploadFile] = File(...),
    _: User = Depends(get_current_user),
) -> Response:
    payload = [(file.filename or f"book_info_{idx}.xlsx", await file.read()) for idx, file in enumerate(files, 1)]
    result = _analyze_or_400(payload)
    filename = safe_data_analysis_filename(result["summary"]["report_date"])
    return Response(
        content=build_data_analysis_xlsx(result),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
        status_code=status.HTTP_200_OK,
    )
