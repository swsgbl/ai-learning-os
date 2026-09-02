"""M9-01 多用户与认证：注册 / 登录 / me / status。

门禁策略（ADR 记录）：
- AUTH_SECRET 未配置 = 认证关闭，/api/v1/auth/status 如实透出 auth_enabled=false；
- 配置后全 /api/v1/* 业务路径要求 Bearer token（register/login/status 豁免——
  未登录可达；me 要求有效 token）。
- 登录失败统一「用户名或密码错误」，防用户名枚举。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.security import (
    AuthenticationError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_USERNAME_PATTERN = r"^[a-zA-Z0-9_]{3,32}$"
# bcrypt 只取前 72 字节；超长密码截断会静默变短——直接在边界拒绝
_PASSWORD_MAX_BYTES = 72


class CredentialsIn(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=_USERNAME_PATTERN)
    password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    id: str
    username: str
    created_at: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AuthStatusOut(BaseModel):
    auth_enabled: bool


def _user_repo(request: Request):
    repo = getattr(request.app.state, "users", None)
    if repo is None:  # 认证路由仅在 DB 模式可用；内存模式没有 users 表
        raise HTTPException(status_code=503, detail="Auth requires a database")
    return repo


def _current_user_id(request: Request) -> str:
    """从 Bearer token 解出 user_id；认证关闭时返回空串（门禁放行语义）。"""
    secret = get_settings().auth_secret
    if not secret:
        return ""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=401, detail="Missing bearer token", headers={"WWW-Authenticate": "Bearer"}
        )
    try:
        return decode_access_token(auth.removeprefix("Bearer ").strip(), secret=secret)
    except AuthenticationError as cause:
        raise HTTPException(
            status_code=401, detail=str(cause), headers={"WWW-Authenticate": "Bearer"}
        ) from cause


async def require_user(request: Request) -> None:
    """app 级门禁依赖：认证开启时业务路径必须带有效 token；豁免路径集中在此。"""
    if not get_settings().auth_secret:
        return
    path = request.url.path
    exempt = (
        path in (
            "/health",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/api/v1/version",
            "/api/v1/auth/status",
            "/api/v1/auth/register",
            "/api/v1/auth/login",
        )
    )
    if exempt:
        return
    _current_user_id(request)


@router.post("/register", response_model=UserOut, status_code=201)
async def register(payload: CredentialsIn, request: Request) -> UserOut:
    repo = _user_repo(request)
    if len(payload.password.encode("utf-8")) > _PASSWORD_MAX_BYTES:
        raise HTTPException(status_code=422, detail="密码超长（bcrypt 72 字节上限）")
    try:
        user = await repo.create(payload.username, hash_password(payload.password))
    except ValueError as cause:
        raise HTTPException(status_code=409, detail=str(cause)) from cause
    return UserOut(
        id=user.id, username=user.username, created_at=user.created_at.isoformat()
    )


@router.post("/login", response_model=TokenOut)
async def login(payload: CredentialsIn, request: Request) -> TokenOut:
    repo = _user_repo(request)
    found = await repo.find_by_username(payload.username)
    # 用户不存在与密码错误同文案同路径，防枚举；verify 仍执行以拉平常量时间
    password_hash = found[1] if found else _DUMMY_HASH
    ok = verify_password(payload.password, password_hash)
    if found is None or not ok:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    settings = get_settings()
    token = create_access_token(
        found[0].id,
        secret=settings.auth_secret or "",
        expires_minutes=settings.auth_token_expire_minutes,
    )
    return TokenOut(access_token=token)


@router.get("/me", response_model=UserOut)
async def me(request: Request) -> UserOut:
    user_id = _current_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    user = await _user_repo(request).get(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在或已删除")
    return UserOut(id=user.id, username=user.username, created_at=user.created_at.isoformat())


@router.get("/status", response_model=AuthStatusOut)
async def status() -> AuthStatusOut:
    """认证开关如实透出——不虚报受保护状态（前端据此决定是否展示登录）。"""
    return AuthStatusOut(auth_enabled=bool(get_settings().auth_secret))


_DUMMY_HASH = hash_password("timing-equalizer-dummy")  # 模块加载时算一次，登录路径常数时间对齐
