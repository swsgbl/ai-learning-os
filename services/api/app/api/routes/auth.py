"""M9-01 多用户与认证：注册 / 登录 / me / status / logout（M10-03 cookie 升级）。

门禁策略（ADR 记录）：
- AUTH_SECRET 未配置 = 认证关闭，/api/v1/auth/status 如实透出 auth_enabled=false；
- 配置后全 /api/v1/* 业务路径要求凭据——M10-03 起同时接受 Bearer header（CLI/API）
  与 login 设置的 HttpOnly cookie（浏览器）；register/login/status/logout 豁免。
- 登录失败统一「用户名或密码错误」，防用户名枚举。

M10-03 cookie 边界：
- cookie 值仍是同一 JWT（验签/过期/幽灵用户语义与 Bearer 完全一致）；
- HttpOnly + SameSite=Lax 默认（跨站 POST 不携带 cookie = CSRF 边界），
  Secure 经 AUTH_COOKIE_SECURE 配置（HTTPS 部署开启），有效期与 token TTL 同源。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
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
    role: str  # learner | admin（M10-02：前端据此渲染治理入口；安全边界仍在 require_admin）
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
    """解出 user_id：Bearer header 优先，其次 HttpOnly cookie（M10-03）。

    认证关闭时返回空串（门禁放行语义）；两种载体共用同一验签/过期路径。
    """
    secret = get_settings().auth_secret
    if not secret:
        return ""
    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
    if not token:
        cookie_name = get_settings().auth_cookie_name
        token = request.cookies.get(cookie_name, "")
    if not token:
        raise HTTPException(
            status_code=401, detail="Missing bearer token", headers={"WWW-Authenticate": "Bearer"}
        )
    try:
        return decode_access_token(token, secret=secret)
    except AuthenticationError as cause:
        raise HTTPException(
            status_code=401, detail=str(cause), headers={"WWW-Authenticate": "Bearer"}
        ) from cause


async def require_user(request: Request) -> None:
    """app 级门禁依赖：认证开启时业务路径必须带有效 token；豁免路径集中在此。

    M9-05: token 验签后还确认用户仍存在（deleted/ghost fail-closed）。
    """
    if not get_settings().auth_secret:
        return
    path = request.url.path
    exempt = path in (
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/api/v1/version",
        "/api/v1/auth/status",
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",  # M10-03: 清 cookie 无需有效凭据（幂等，仅作用于调用方自身）
    )
    if exempt:
        return
    user_id = _current_user_id(request)
    repo = getattr(request.app.state, "users", None)
    if repo is not None and await repo.get_role(user_id) is None:
        raise HTTPException(
            status_code=401,
            detail="用户不存在或已删除",
            headers={"WWW-Authenticate": "Bearer"},
        )


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
        id=user.id,
        username=user.username,
        role=user.role,
        created_at=user.created_at.isoformat(),
    )


@router.post("/login", response_model=TokenOut)
async def login(payload: CredentialsIn, request: Request, response: Response) -> TokenOut:
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
    # M10-03: 浏览器登录态走 HttpOnly cookie（页面 JS 不再读取/保存 token；
    # body 里的 access_token 保留给 CLI/API 客户端）
    set_auth_cookie(response, token, settings)
    return TokenOut(access_token=token)


def set_auth_cookie(response: Response, token: str, settings=None) -> None:
    """M10-03: 登录态 cookie 统一落点（HttpOnly + SameSite=Lax 默认 + 可配置名/Secure）。"""
    settings = settings or get_settings()
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=settings.auth_token_expire_minutes * 60,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        path="/",
    )


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response) -> None:
    """M10-03: 清除登录 cookie（幂等；无需有效凭据——只作用于调用方自己的 cookie）。"""
    settings = get_settings()
    # 属性镜像 set_auth_cookie：Secure cookie 的删除指令同样携带 Secure，
    # 避免部分 hardened 浏览器丢弃不带 Secure 的覆写（RFC 6265bis 语义）。
    response.delete_cookie(
        key=settings.auth_cookie_name,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite=settings.auth_cookie_samesite,
    )


@router.get("/me", response_model=UserOut)
async def me(request: Request) -> UserOut:
    user_id = _current_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    user = await _user_repo(request).get(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在或已删除")
    return UserOut(
        id=user.id,
        username=user.username,
        role=user.role,
        created_at=user.created_at.isoformat(),
    )


def current_owner_id(request: Request) -> str | None:
    """M9-02 归属上下文：auth on -> 当前用户 id；auth off -> None（无主模式）。

    语义（ADR 66）：None owner 写入 NULL、读取不过滤（本地调试=现状）；
    有 owner 写入 me、读取严格 ==me（NULL 行不可见，他人资源 404 不暴露存在性）。
    """
    secret = get_settings().auth_secret
    if not secret:
        return None
    return _current_user_id(request) or None


async def current_is_admin(request: Request) -> bool:
    """auth off -> True（本地单用户）；auth on -> 实时 role==admin。无效 token False。"""
    if not get_settings().auth_secret:
        return True
    try:
        user_id = _current_user_id(request)
    except HTTPException:
        return False
    repo = getattr(request.app.state, "users", None)
    if repo is None:
        return False
    return await repo.get_role(user_id) == "admin"


async def require_admin(request: Request) -> None:
    """M9-04 治理门禁：auth on 时要求 admin 角色（403），auth off 本地模式放行。

    角色每次实时读库（撤销提升即时生效）；无效 token 一律 401。
    """
    secret = get_settings().auth_secret
    if not secret:
        return
    user_id = _current_user_id(request)
    repo = getattr(request.app.state, "users", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Auth requires a database")
    role = await repo.get_role(user_id)
    if role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


async def build_audit_payload(
    request: Request,
    *,
    action: str,
    target_type: str,
    target_id: str,
    before=None,
    after=None,
) -> dict | None:
    """M9-05: 构造审计 payload（不写库）——由仓储 mutation 在业务同一事务内落库。

    审计与业务同事务 = 写入失败整体回滚（fail-closed），不再吞异常。
    无审计仓储（内存模式）返回 None。
    """
    if getattr(request.app.state, "audit", None) is None:
        return None
    owner = current_owner_id(request)
    username = None
    if owner:
        user = await request.app.state.users.get(owner)
        username = user.username if user else None
    return {
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "request_id": getattr(request.state, "request_id", "unknown"),
        "actor_id": owner,
        "actor_username": username,
        "before": before,
        "after": after,
    }

@router.get("/status", response_model=AuthStatusOut)
async def status() -> AuthStatusOut:
    """认证开关如实透出——不虚报受保护状态（前端据此决定是否展示登录）。"""
    return AuthStatusOut(auth_enabled=bool(get_settings().auth_secret))


_DUMMY_HASH = hash_password("timing-equalizer-dummy")  # 模块加载时算一次，登录路径常数时间对齐
