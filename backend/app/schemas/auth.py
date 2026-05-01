"""鉴权请求 / 响应 schema。"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

# 简单的中国大陆手机号校验：1 开头 + 10 位数字
_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


class RegisterRequest(BaseModel):
    """email / phone 任填一个，password 必填。"""
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, min_length=11, max_length=11)
    password: str = Field(..., min_length=6, max_length=64)
    name: Optional[str] = Field(None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def _check_identifier(self):
        if not self.email and not self.phone:
            raise ValueError("邮箱与手机号至少需要填写一个")
        if self.phone and not _PHONE_RE.match(self.phone):
            raise ValueError("手机号格式不正确（需 11 位 1[3-9]xxxxxxxxx）")
        return self


class LoginRequest(BaseModel):
    """identifier 是邮箱或手机号，password 必填。"""
    identifier: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=64)


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: Optional[str] = None
    phone: Optional[str] = None
    name: str
    avatar_url: Optional[str] = None
    is_vip: bool
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    user: UserPublic
