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


# 公开默认占位 secret（仓库/文档/compose 示例中出现过的值）——生产环境禁用
PUBLIC_DEFAULT_SECRETS = frozenset(
    {
        "aios-local-dev-secret-7d21b9e4c8a3",
        "m9-test-secret",
        "m9-02-isolation-secret",
        "change-me",
        "secret",
        "ailos-local-dev-secret-0f4c9a1e7b2d",  # M9-06: compose LiveKit 占位
    }
)
_MIN_SECRET_BYTES = 32


def validate_auth_secret(secret: str | None, *, app_env: str) -> None:
    """生产环境 AUTH_SECRET fail-closed 校验（M9-04）。

    - production：必须配置、≥32 字节、且不得是公开默认占位值，否则拒绝启动；
    - 其他环境（development/docker 本地栈）：不强制（本地调试与冒烟兼容），
      但配置了却弱于 32 字节时给出明确错误（配了就要配够）。
    """
    if app_env == "production":
        if not secret:
            raise RuntimeError("生产环境必须配置 AUTH_SECRET（拒绝以未认证模式启动）")
        if len(secret.encode("utf-8")) < _MIN_SECRET_BYTES:
            raise RuntimeError(
                f"生产环境 AUTH_SECRET 至少 {_MIN_SECRET_BYTES} 字节（当前 {len(secret.encode('utf-8'))}）"
            )
        if secret in PUBLIC_DEFAULT_SECRETS:
            raise RuntimeError("生产环境禁止使用公开默认 AUTH_SECRET 占位值")
        return
    if secret and len(secret.encode("utf-8")) < _MIN_SECRET_BYTES:
        raise RuntimeError(
            f"AUTH_SECRET 已配置但不足 {_MIN_SECRET_BYTES} 字节（{len(secret.encode('utf-8'))}）——请加长或移除"
        )


def _is_loopback(ip: str) -> bool:
    return ip in ("127.0.0.1", "::1", "localhost")


def _strong_secret(value: str | None) -> bool:
    return bool(value) and len(value.encode("utf-8")) >= 32 and value not in PUBLIC_DEFAULT_SECRETS


def _cors_is_local_only(cors_origins: str) -> bool:
    """CORS 列表是否只含 localhost/127.0.0.1 源（公开服务时这是错误配置）。"""
    origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
    return bool(origins) and all(
        "localhost" in o or "127.0.0.1" in o for o in origins
    )


def validate_exposure(
    *,
    host_bind_ip: str,
    app_env: str,
    auth_secret: str | None,
    livekit_api_secret: str | None,
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000",
) -> None:
    """M9-06/M9-07 公开暴露 fail-closed：绑定非 loopback 时全部安全前置必须满足。

    - APP_ENV 必须 production（走 AUTH_SECRET 最严校验）；
    - LIVEKIT_API_SECRET 必须已配置、≥32 字节且非仓库默认占位值；
    - CORS 不得仍为 localhost-only（否则局域网/外部设备全被浏览器拦下——
      必须显式配置 AIOS_CORS_ORIGINS 含实际 Web origin）；
    任一不满足即 RuntimeError（启动失败），绝不以弱配置公开启动。
    """
    if _is_loopback(host_bind_ip):
        return
    if app_env != "production":
        raise RuntimeError(
            "公开绑定（非 loopback）要求 APP_ENV=production；"
            f"当前 APP_ENV={app_env!r}——拒绝以非生产配置公开启动"
        )
    validate_auth_secret(auth_secret, app_env="production")
    if not _strong_secret(livekit_api_secret):
        raise RuntimeError(
            "公开绑定必须配置强 LIVEKIT_API_SECRET（已配置、≥32 字节且非公开默认占位值）"
        )
    if _cors_is_local_only(cors_origins):
        raise RuntimeError(
            "公开绑定必须配置 AIOS_CORS_ORIGINS（含实际 Web origin）；"
            f"当前 CORS 仍为 localhost-only: {cors_origins!r}——拒绝以本地 CORS 对外服务"
        )
