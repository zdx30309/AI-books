"""鉴权路由：注册 / 登录 / 当前用户。"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import (
    JWTDecodeError,
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db.session import get_db
from app.models import User
from app.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserPublic,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])


def _resolve_user(db: Session, identifier: str) -> Optional[User]:
    """identifier 可能是 email 或 phone，统一用 OR 查。"""
    return (
        db.query(User)
        .filter(or_(User.email == identifier, User.phone == identifier))
        .first()
    )


def get_current_user(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI 依赖：从 Authorization: Bearer xxx 解析出当前用户。"""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="缺少或非法的 Authorization 头")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_token(token)
    except JWTDecodeError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="token 缺少 sub")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="注册（邮箱或手机号 + 密码）",
)
def register(req: RegisterRequest, db: Session = Depends(get_db)) -> TokenResponse:
    # 唯一性预检（DB 唯一索引兜底）
    existing = (
        db.query(User)
        .filter(
            or_(
                User.email == req.email if req.email else False,
                User.phone == req.phone if req.phone else False,
            )
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="该邮箱或手机号已被注册")

    user = User(
        email=req.email,
        phone=req.phone,
        password_hash=hash_password(req.password),
        name=req.name or "墨语者",
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="该邮箱或手机号已被注册")
    db.refresh(user)

    token = create_access_token(user.id)
    return TokenResponse(access_token=token, user=UserPublic.model_validate(user))


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="登录（identifier 可填邮箱或手机号）",
)
def login(req: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = _resolve_user(db, req.identifier.strip())
    if user is None or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="账号或密码不正确")

    token = create_access_token(user.id)
    return TokenResponse(access_token=token, user=UserPublic.model_validate(user))


@router.get(
    "/me",
    response_model=UserPublic,
    summary="当前用户信息",
)
def me(user: User = Depends(get_current_user)) -> UserPublic:
    return UserPublic.model_validate(user)
