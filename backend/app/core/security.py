"""JWT 编解码 + 密码哈希。所有鉴权工具集中在这里。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import bcrypt
import jwt

from app.core.config import settings

# bcrypt 硬限制：密码 ≤ 72 字节，超过截断
_BCRYPT_MAX_BYTES = 72


def _truncate_for_bcrypt(plain: str) -> bytes:
    raw = plain.encode("utf-8")
    if len(raw) <= _BCRYPT_MAX_BYTES:
        return raw
    # 防止把 UTF-8 多字节字符截一半
    truncated = raw[:_BCRYPT_MAX_BYTES]
    while truncated:
        try:
            truncated.decode("utf-8")
            break
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    return truncated or raw[:_BCRYPT_MAX_BYTES]


def hash_password(plain: str) -> str:
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(_truncate_for_bcrypt(plain), salt)
    return hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_truncate_for_bcrypt(plain), hashed.encode("utf-8"))
    except Exception:
        return False


_DEFAULT_JWT_SECRET = "change-me-please-very-long-random-secret-key-for-jwt-signing-only"


def _jwt_secret() -> str:
    """The default secret is public in the repository; production must not sign with it."""
    secret = settings.JWT_SECRET
    if settings.APP_ENV.strip().lower() == "production" and (not secret or secret == _DEFAULT_JWT_SECRET):
        raise RuntimeError("JWT_SECRET 未配置：生产环境拒绝使用仓库里的默认密钥签发或校验登录令牌")
    return secret


def create_access_token(user_id: UUID | str, *, extra: dict[str, Any] | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.JWT_EXPIRES_HOURS)).timestamp()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, _jwt_secret(), algorithm=settings.JWT_ALGORITHM)


class JWTDecodeError(Exception):
    pass


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise JWTDecodeError("token 已过期") from exc
    except jwt.InvalidTokenError as exc:
        raise JWTDecodeError(f"token 非法: {exc}") from exc
