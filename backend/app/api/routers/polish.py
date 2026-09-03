"""段落级润色接口：编辑器圈选某段后调用，复用 Gemini 一润 prompt。"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.routers.auth import get_current_user
from app.models import User
from app.services.polish_provider import polish_book_text, polish_provider_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/polish", tags=["Polish"])


class PolishParagraphRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=20000, description="待润色段落原文")


class PolishParagraphResponse(BaseModel):
    original: str
    polished: str
    original_chars: int
    polished_chars: int


@router.post(
    "/paragraph",
    response_model=PolishParagraphResponse,
    summary="对单段文字调用已配置的一润模型重新润色",
)
async def polish_paragraph(
    req: PolishParagraphRequest,
    _user: User = Depends(get_current_user),
) -> PolishParagraphResponse:
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="待润色文本不能为空")

    try:
        polished = await asyncio.to_thread(polish_book_text, text)
    except Exception as exc:
        logger.exception("段落润色失败")
        raise HTTPException(
            status_code=502,
            detail=f"{polish_provider_name()} 润色调用失败: {exc}",
        ) from exc

    polished = (polished or "").strip()
    if not polished:
        raise HTTPException(status_code=502, detail=f"{polish_provider_name()} 返回为空")

    return PolishParagraphResponse(
        original=text,
        polished=polished,
        original_chars=len(text),
        polished_chars=len(polished),
    )
