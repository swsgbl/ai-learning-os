"""M9-01 多用户与认证基座：密码哈希与 JWT 签发/校验。

安全边界：
- 密码只存 bcrypt 哈希（cost 默认 12），永不落明文/日志；
- JWT HS256，secret 来自 AUTH_SECRET（部署 secret/.env，不入库不入码）；
- AUTH_SECRET 未配置 = 认证关闭，/api/v1/auth/status 如实透出 auth_enabled=false
  （与 voice/search provider「未配置如实降级」同款约定；本地调试与既有测试零破坏）。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

_ALGORITHM = "HS256"

# 防枚举：用户名不存在与密码错误用同一文案、同一退出路径。
_CREDENTIALS_MESSAGE = "用户名或密码错误"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        # 哈希格式损坏按校验失败处理，不向调用方泄内部状态
        return False


def create_access_token(user_id: str, *, secret: str, expires_minutes: int) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


class AuthenticationError(Exception):
    """凭据无效（统一文案，防枚举）。"""


def decode_access_token(token: str, *, secret: str) -> str:
    """校验并返回 sub（user_id）；无效/过期一律 AuthenticationError。"""
    try:
        payload = jwt.decode(token, secret, algorithms=[_ALGORITHM])
    except jwt.PyJWTError as cause:
        raise AuthenticationError(_CREDENTIALS_MESSAGE) from cause
    sub = payload.get("sub")
    if not sub:
        raise AuthenticationError(_CREDENTIALS_MESSAGE)
    return str(sub)
