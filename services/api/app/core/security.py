"""M9-01 多用户与认证基座：密码哈希与 JWT 签发/校验。

安全边界：
- 密码只存 bcrypt 哈希（cost 默认 12），永不落明文/日志；
- JWT HS256，secret 来自 AUTH_SECRET（部署 secret/.env，不入库不入码）；
- AUTH_SECRET 未配置 = 认证关闭，/api/v1/auth/status 如实透出 auth_enabled=false
  （与 voice/search provider「未配置如实降级」同款约定；本地调试与既有测试零破坏）。
"""
from __future__ import annotations

import ipaddress
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


def _resolve_livekit_bind_ip(
    value: str | None,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """AIOS_LIVEKIT_BIND_IP 解析与分类（stdlib ipaddress，M14-37 rework）。

    - None / 空串 → 未启用（None）：空串是 compose 默认部署的哨兵
      （`${AIOS_LIVEKIT_BIND_IP:-}` 未设置时向 API 透传 HOST_LIVEKIT_BIND_IP=""
      ，见 infra/docker-compose.yml），等价未配置、不触发媒体面门禁；
    - "localhost" → loopback（hostname 非 IP 字面量，保留存量契约）；
    - 其余值必须能被 ipaddress 解析：非法字面量（段数错误/空格/换行/误带端口）
      → RuntimeError（配了就必须是合法值，不靠字符串集合枚举）；
    - wildcard（is_unspecified：0.0.0.0、::，含解包后的 ::ffff:0.0.0.0）→
      RuntimeError：可作端口 bind 但不能作 LiveKit --node-ip 通告地址，
      放它进入公开分支是无效修复配置；
    - IPv4-mapped（::ffff:a.b.c.d，Windows dual-stack 常见形态）先解包为 IPv4
      再分类：Python ipaddress 对 mapped 地址的 is_loopback/is_unspecified 按
      IPv6 字面判定，直接用会把 ::ffff:127.0.0.1 误判为非 loopback。
    """
    if value is None or value == "":
        return None
    if value == "localhost":
        return ipaddress.IPv4Address("127.0.0.1")
    try:
        addr: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(value)
    except ValueError as cause:
        raise RuntimeError(
            f"AIOS_LIVEKIT_BIND_IP 必须是合法 IP 字面量（当前 {value!r}）——"
            "必须配置具体本机 LAN IP（如 192.168.1.50），或留空/不设置保持未启用"
        ) from cause
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if addr.is_unspecified:
        raise RuntimeError(
            f"AIOS_LIVEKIT_BIND_IP={value!r}（wildcard 绑定）不能作为 LiveKit "
            "--node-ip 通告地址（对端无法回连 wildcard 地址）——必须配置具体"
            "本机 LAN IP（如 192.168.1.50；端口绑定与通告地址用同一具体 IP）"
        )
    return addr


def _strong_secret(value: str | None) -> bool:
    return bool(value) and len(value.encode("utf-8")) >= 32 and value not in PUBLIC_DEFAULT_SECRETS


def _cors_is_local_only(cors_origins: str) -> bool:
    """CORS 列表是否只含 localhost/127.0.0.1 源（公开服务时这是错误配置）。

    M9-08: 用 urlsplit 精确解析 hostname——子串包含会把
    https://evil-localhost.attacker.com 这类误判为本地源。
    """
    from urllib.parse import urlsplit

    origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
    if not origins:
        return False
    hosts = []
    for origin in origins:
        parts = urlsplit(origin if "//" in origin else f"//{origin}", scheme="http")
        hosts.append((parts.hostname or "").lower())
    return all(h in ("localhost", "127.0.0.1", "::1") for h in hosts)


def _is_internal_livekit_url(url: str) -> bool:
    """LiveKit 地址是否容器内部/本机专用（局域网浏览器不可达）。"""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return host in ("livekit", "localhost", "127.0.0.1", "::1") or not host


def validate_exposure(
    *,
    host_bind_ip: str,
    app_env: str,
    auth_secret: str | None,
    livekit_api_secret: str | None,
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000",
    public_livekit_url: str | None = None,
    livekit_bind_ip: str | None = None,
) -> None:
    """M9-06/M9-07 公开暴露 fail-closed：绑定非 loopback 时全部安全前置必须满足。

    - APP_ENV 必须 production（走 AUTH_SECRET 最严校验）；
    - LIVEKIT_API_SECRET 必须已配置、≥32 字节且非仓库默认占位值；
    - CORS 不得仍为 localhost-only（否则局域网/外部设备全被浏览器拦下——
      必须显式配置 AIOS_CORS_ORIGINS 含实际 Web origin）；
    - PUBLIC_LIVEKIT_URL 必须为浏览器可达地址（M9-08，不允许静默降级到
      容器内部地址）。
    任一不满足即 RuntimeError（启动失败），绝不以弱配置公开启动。

    M14-37: livekit_bind_ip 支持 LiveKit 媒体面单独非 loopback 绑定
    （AIOS_LIVEKIT_BIND_IP——同机默认浏览器 ICE 稳定拓扑，API/Web 保持
    loopback）。该路径只要求 LiveKit 自身安全前置：强 LIVEKIT_API_SECRET +
    可达 PUBLIC_LIVEKIT_URL；API 未公开，APP_ENV/CORS 门禁不适用（本机
    localhost Web 源在该场景是合法配置）。
    M14-37 rework: livekit_bind_ip 先经 stdlib ipaddress 解析分类（空串=未启用
    哨兵、IPv4-mapped 解包；wildcard/非法字面量 fail-closed 拒绝且与 API/Web
    面状态无关）——wildcard 可作 bind 但不能作 --node-ip 通告地址。
    """
    lk_addr = _resolve_livekit_bind_ip(livekit_bind_ip)
    lk_public = lk_addr is not None and not lk_addr.is_loopback
    if _is_loopback(host_bind_ip) and not lk_public:
        return
    if not _is_loopback(host_bind_ip):
        # API/Web 面公开：M9-06/M9-07 原四项校验（语义零改动）
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
        # M9-08: 公开绑定时必须显式配置浏览器可达的 LiveKit 地址（不允许静默降级
        # 到容器内部地址——局域网浏览器拿到 ws://livekit:7880 无法连接）
        if not public_livekit_url or _is_internal_livekit_url(public_livekit_url):
            raise RuntimeError(
                "公开绑定必须配置 PUBLIC_LIVEKIT_URL 为局域网可达地址"
                "（如 ws://<LAN_IP或域名>:7880）；"
                f"当前值 {public_livekit_url!r} 缺失或为容器内部/本机地址——拒绝启动"
            )
        return
    # LiveKit 单独公开（M14-37）：API/Web 仍 loopback —— 只校验媒体面自身前置
    if not _strong_secret(livekit_api_secret):
        raise RuntimeError(
            "LiveKit 媒体面独立公开绑定（AIOS_LIVEKIT_BIND_IP 非 loopback）必须配置"
            "强 LIVEKIT_API_SECRET（已配置、≥32 字节且非公开默认占位值）"
        )
    if not public_livekit_url or _is_internal_livekit_url(public_livekit_url):
        raise RuntimeError(
            "LiveKit 媒体面独立公开绑定必须配置 PUBLIC_LIVEKIT_URL 为浏览器可达"
            "地址（如 ws://<本机LAN_IP>:7880）；"
            f"当前值 {public_livekit_url!r} 缺失或为容器内部/本机 loopback 地址——拒绝启动"
        )
