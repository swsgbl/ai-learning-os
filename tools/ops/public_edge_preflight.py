"""M14-153 公网边缘部署 preflight —— 只对显式提供的公网端点做验收检查。

适用场景：VPS 边缘栈（Caddy + frps + LiveKit + coturn，见
infra/edge/ 与 docs/PUBLIC_EDGE_DEPLOYMENT.md）部署后的公网验收。
对照研究定版 docs/PUBLIC_EDGE_DEPLOYMENT_RESEARCH.md 的验收标准。

Fail-closed 契约（违反任何一条都不放行）：
- 只访问命令行显式给出的端点：本脚本不内置任何默认域名/IP/端口
  （--turn-port 除外——它是协议常量而非目标地址）；
- 端点 origin-only（Round 4）：只接受 scheme + 公网 host + port——
  userinfo、query、fragment、path 一律在入口拒绝；拒绝 http、
  loopback/私网/RFC 5737 文档段、RFC 2606 保留域（example.com/
  .invalid/.test/.localhost）——拿模板占位域名做"验收"直接 FAIL；
- 脱敏集中化（Round 4）：任何 URL 相关错误/报告只出现 canonical
  scheme://host[:port]（由 _safe_endpoint_display 重构，绝不截取原文）；
  原始端点输入（可能内嵌 userinfo 密码/query token）绝不进入异常文本、
  stderr、检查详情或 JSON 报告；secret 文件读取错误只透出错误类别，
  不回显文件路径；
- 请求直连（显式禁用系统代理），验收的是边缘本身而不是代理链路；
- 被要求验证的检查不可证即 FAIL，绝不静默跳过或降级为警告：
  * HTTPS/证书链：受信 CA + 域名匹配 + 剩余有效期 >= 14 天
    （默认验证上下文，自签/过期/错域名全部失败）；
  * 健康检查：GET /health 必须返回 200；
  * CORS：preflight 应答必须精确回显应用源（绝不接受 *），且
    Access-Control-Allow-Credentials: true（cookie 跨源语义）；
  * Cookie：真实登录响应的 Set-Cookie 必须 Secure + SameSite=None +
    HttpOnly（生产跨域拓扑）；不提供 --login-credentials-file 就无法
    证明 → FAIL（不是跳过）；
  * LiveKit：公网 URL 返回 200 只证明 TLS+反代+signal 存活；真正的
    WSS 升级握手（HTTP 101）必须提供 --livekit-token-file 才算验证过；
  * TURN/TLS：TLS 握手成功 + 未认证 STUN Allocate 必须得到 401
    （证明 TURN 协议在应答且认证强制；401 之外的响应不放过）；
  * 安全响应头：HSTS(max-age>=15552000)/X-Content-Type-Options/
    Referrer-Policy 缺一即 FAIL。
- 手机 4G/5G 验收是物理网络人工事项，脚本无法替代：报告始终输出
  人工清单；未提供 --mobile-attested-file 逐项签认时，即使全部自动
  检查通过，整体也只到 manual_pending（exit 3），不宣称生产可用。
- 输出绝不包含 secret：cookie 只记录属性不记录值，LiveKit token 与
  登录凭据只从文件读取且从不回显，报告不含本机绝对路径。

Exit codes: 0 = 全部通过且人工清单已签认；1 = 任一自动检查 FAIL
（或参数/端点非法）；3 = 自动检查全过但人工清单待签认。
"""
from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import os
import secrets
import socket
import ssl
import struct
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_MANUAL_PENDING = 3

SCHEMA = "aios-public-edge-preflight/1"
TOOL_NAME = "public_edge_preflight"
CERT_MIN_REMAIN_DAYS = 14
HSTS_MIN_MAX_AGE = 15552000  # 180 天（OWASP 建议至少 6 个月）
DEFAULT_TURN_TLS_PORT = 5349  # 协议常量（RFC 8489 之外的 TURN/TLS 惯例端口）

# RFC 2606/6761 保留：占位域名不得作为"公网端点"参与验收。
# 只存裸后缀——匹配时同时覆盖裸域名与其全部子孙域（foo.example.com、
# foo.invalid 等一律拒绝），杜绝把模板占位域名当真实端点做假验收。
RESERVED_DOMAIN_SUFFIXES = (
    "example.com",
    "example.net",
    "example.org",
    "example",
    "invalid",
    "localhost",
    "test",
)

STATUS_PASS = "pass"
STATUS_FAIL = "fail"

MANUAL_CHECKLIST: tuple[dict[str, str], ...] = (
    {
        "id": "mobile-4g5g-open",
        "title": "4G/5G 实网打开入口",
        "detail": "关闭 Wi-Fi 与一切 VPN/Tailscale/WARP，用蜂窝网络打开 Web 入口，可加载并登录",
    },
    {
        "id": "mobile-cross-origin-cookie",
        "title": "跨源 cookie 与 CORS",
        "detail": "登录后跨域 cookie 有效，API 请求 200，浏览器控制台无 CORS 错误",
    },
    {
        "id": "mobile-exam-flow",
        "title": "考试全流程",
        "detail": "考试计时、交卷、评分、错题复盘全流程可用",
    },
    {
        "id": "mobile-voice-connect",
        "title": "公网语音连接",
        "detail": "语音页连接 wss://<livekit 域名>，语音 token 有效，真实语音输入/输出正常",
    },
    {
        "id": "mobile-turn-relay",
        "title": "受限网络 TURN relay",
        "detail": "受限网络下 trickle-ice 得到 relay 候选（icetest.livekit.io 或等价工具），真实语音可用",
    },
    {
        "id": "mobile-android-apk",
        "title": "Android release APK",
        "detail": "release 签名 APK 可下载、SHA256 与下载站清单一致、安装后连通公网 API",
    },
    {
        "id": "mobile-harmony-pwa",
        "title": "HarmonyOS 签名链路 / iPhone PWA",
        "detail": "HarmonyOS signed HAP 经 AGC 可获取（或官网引导页可达）；iPhone 走 PWA 安装引导可用",
    },
)


class PreflightError(Exception):
    """参数/端点非法（fail-closed，直接进入失败路径，不发任何请求）。"""


@dataclass(frozen=True)
class Endpoint:
    """显式提供的公网 https 端点。"""

    url: str
    host: str
    port: int

    @property
    def origin(self) -> str:
        return f"https://{self.host}" if self.port == 443 else f"https://{self.host}:{self.port}"


def is_public_host(host: str) -> bool:
    """公网主机判定：保留域与任何非全局地址（loopback/私网/RFC5737 等）一律拒绝。

    保留域按"裸后缀"匹配：等于后缀或以 `.后缀` 结尾都拒绝——
    `foo.example.com` / `foo.invalid` 与裸 `example.com` 一样不可验收。
    """
    lowered = host.rstrip(".").lower()
    if not lowered:
        return False
    for suffix in RESERVED_DOMAIN_SUFFIXES:
        if lowered == suffix or lowered.endswith("." + suffix):
            return False
    try:
        ip = ipaddress.ip_address(lowered)
    except ValueError:
        return True  # 普通主机名：通过（后续 TLS/HTTP 探测再验证可达性）
    if not ip.is_global:
        return False
    reserved_doc_ranges = (
        ipaddress.ip_network("192.0.2.0/24"),   # RFC 5737 TEST-NET-1
        ipaddress.ip_network("198.51.100.0/24"),  # RFC 5737 TEST-NET-2
        ipaddress.ip_network("203.0.113.0/24"),  # RFC 5737 TEST-NET-3
    )
    return not any(ip in network for network in reserved_doc_ranges)


def _safe_endpoint_display(raw: str) -> str:
    """集中脱敏展示（Round 4）：端点输入只显示 canonical scheme://host[:port]。

    原始输入可能内嵌 userinfo 密码/query token/path——绝不能进入异常文本、
    stderr、检查详情或报告。本助手从解析结果**重构**展示串（非原文截取）：
    解析不出 host 时返回固定占位符，任何失败路径都不回显原文。
    """
    try:
        parts = urlsplit(raw.strip() if isinstance(raw, str) else "")
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return "<invalid-endpoint>"
    if not host:
        return "<invalid-endpoint>"
    if port in (None, 443):
        return f"https://{host}"
    return f"https://{host}:{port}"


def parse_public_https_url(raw: str) -> Endpoint:
    """解析并校验显式提供的公网 https 端点；非法即抛 PreflightError。

    origin-only（Round 4）：只接受 scheme + 公网 host + port——userinfo、
    query、fragment、path 一律拒绝（端点参数可能携带凭据，必须 fail-closed
    在入口拦截且不回显原文）。返回的 url/origin 是 canonical 重构值。
    """
    if not raw or not raw.strip():
        raise PreflightError("空端点")
    parts = urlsplit(raw.strip())
    if parts.scheme != "https":
        raise PreflightError(
            f"必须是 https 端点（收到 scheme={parts.scheme!r}）: {_safe_endpoint_display(raw)}"
        )
    if parts.username or parts.password:
        raise PreflightError(f"端点不得携带 userinfo: {_safe_endpoint_display(raw)}")
    host = (parts.hostname or "").lower()
    if not host:
        raise PreflightError("端点缺少主机名")
    if parts.path not in ("", "/"):
        raise PreflightError(f"端点不得携带 path（origin-only）: {_safe_endpoint_display(raw)}")
    if parts.query:
        raise PreflightError(f"端点不得携带 query（origin-only）: {_safe_endpoint_display(raw)}")
    if parts.fragment:
        raise PreflightError(f"端点不得携带 fragment: {_safe_endpoint_display(raw)}")
    if not is_public_host(host):
        raise PreflightError(
            f"主机不是公网地址（loopback/私网/保留段/占位域均拒绝）: {host}"
        )
    try:
        port = parts.port
    except ValueError as cause:  # urlsplit 对越界端口抛 ValueError
        raise PreflightError(f"端口非法: {cause}") from cause
    if port is None:
        port = 443
    if not 1 <= port <= 65535:
        raise PreflightError(f"端口越界（1-65535）: {port}")
    canonical = f"https://{host}" if port == 443 else f"https://{host}:{port}"
    return Endpoint(url=canonical, host=host, port=port)


def parse_public_turn_host(raw: str) -> str:
    """TURN/TLS 主机校验（Round 4）：host-only——拒绝 scheme/path/userinfo/query。

    输入必须是纯公网主机名/IPv4（无 `://` `/` `?` `#` `@` 空白）。
    返回 canonical 主机值；非法抛 PreflightError（不回显原文）。
    """
    if not raw or not raw.strip():
        raise PreflightError("空 TURN 主机")
    candidate = raw.strip()
    if any(marker in candidate for marker in ("://", "/", "?", "#", "@")) or any(ch.isspace() for ch in candidate):
        raise PreflightError(
            "TURN 主机必须是纯主机名/IPv4（不得携带 scheme/path/userinfo/query）"
        )
    lowered = candidate.lower().rstrip(".")
    if not is_public_host(lowered):
        raise PreflightError(f"TURN 主机不是公网地址: {lowered}")
    return lowered


# ---------------------------------------------------------------- 纯判定逻辑


def _header(headers: dict[str, str], name: str) -> str:
    """大小写不敏感取响应头（http.client 的头名保留原始大小写）。"""
    return (headers.get(name) or headers.get(name.lower()) or "").strip()


def evaluate_security_headers(headers: dict[str, str]) -> list[str]:
    """安全响应头判定：缺项/弱值返回问题清单（空清单 = 通过）。"""
    problems: list[str] = []

    hsts = _header(headers, "Strict-Transport-Security")
    if not hsts:
        problems.append("缺少 Strict-Transport-Security")
    else:
        max_age = 0
        for token in hsts.split(";"):
            key, _, value = token.strip().partition("=")
            if key.lower() == "max-age" and value.isdigit():
                max_age = int(value)
        if max_age < HSTS_MIN_MAX_AGE:
            problems.append(f"HSTS max-age={max_age} < {HSTS_MIN_MAX_AGE}（至少 6 个月）")

    if not _header(headers, "X-Content-Type-Options"):
        problems.append("缺少 X-Content-Type-Options")
    if not _header(headers, "Referrer-Policy"):
        problems.append("缺少 Referrer-Policy")
    return problems


def evaluate_cors_headers(app_origin: str, headers: dict[str, str]) -> list[str]:
    """CORS 判定：ACAO 必须精确回显应用源且凭据模式开启（跨源 cookie 拓扑）。"""
    problems: list[str] = []

    allow_origin = _header(headers, "Access-Control-Allow-Origin")
    if not allow_origin:
        problems.append("preflight 应答缺少 Access-Control-Allow-Origin")
    elif allow_origin == "*":
        problems.append("Access-Control-Allow-Origin 为 *（带凭据跨源拓扑禁止）")
    elif allow_origin != app_origin:
        problems.append(f"Access-Control-Allow-Origin={allow_origin} 与应用源 {app_origin} 不一致")
    allow_methods = _header(headers, "Access-Control-Allow-Methods")
    if not allow_methods:
        problems.append("preflight 应答缺少 Access-Control-Allow-Methods")
    if _header(headers, "Access-Control-Allow-Credentials").lower() != "true":
        problems.append("缺少 Access-Control-Allow-Credentials: true（HttpOnly cookie 跨源必需）")
    return problems


def evaluate_set_cookie(cookie_header: str) -> tuple[list[str], dict[str, Any]]:
    """Set-Cookie 判定（跨源生产拓扑）：必须 Secure + SameSite=None；HttpOnly 必需。

    只解析属性，绝不返回 cookie 值（报告侧也只消费属性结论）。
    """
    problems: list[str] = []
    attrs: dict[str, Any] = {}
    if not cookie_header:
        problems.append("响应未设置 Set-Cookie（无法证明登录 cookie 语义）")
        return problems, attrs
    parts = [p.strip() for p in cookie_header.split(";") if p.strip()]
    name_value = parts[0] if parts else ""
    name = name_value.split("=", 1)[0].strip()
    attrs["name"] = name  # 只记名字，值绝不进入 attrs/报告
    lowered = {p.lower() for p in parts[1:]}
    if "secure" not in lowered:
        problems.append("Set-Cookie 缺少 Secure")
    samesite = next((p for p in parts[1:] if p.lower().startswith("samesite=")), "")
    attrs["samesite"] = samesite.split("=", 1)[-1].strip().lower() if samesite else ""
    if attrs["samesite"] != "none":
        problems.append(f"Set-Cookie SameSite={attrs['samesite'] or '(缺省)'}（跨源 cookie 拓扑必须 None）")
    if "httponly" not in lowered:
        problems.append("Set-Cookie 缺少 HttpOnly")
    return problems, attrs


def build_stun_allocate(txid: bytes) -> bytes:
    """构造 STUN Allocate 请求（RFC 5766，无属性）——用于未认证探测。"""
    if len(txid) != 12:
        raise ValueError("STUN transaction id 必须是 12 字节")
    return struct.pack("!HHI", 0x0003, 0, 0x2112A442) + txid


def parse_stun_response(data: bytes, txid: bytes) -> tuple[int, int | None]:
    """解析 STUN 应答：返回 (消息类型, ERROR-CODE 属性值)；非法应答抛 ValueError。"""
    if len(data) < 20:
        raise ValueError("STUN 应答不足 20 字节头部")
    msg_type, length, magic = struct.unpack("!HHI", data[:8])
    if magic != 0x2112A442 or data[8:20] != txid:
        raise ValueError("STUN 应答 magic/transaction-id 不匹配")
    body = data[20 : 20 + length]
    code: int | None = None
    offset = 0
    while offset + 4 <= len(body):
        attr_type, attr_len = struct.unpack("!HH", body[offset : offset + 4])
        value = body[offset + 4 : offset + 4 + attr_len]
        if attr_type == 0x0009 and len(value) >= 4:  # ERROR-CODE
            code = value[2] * 100 + value[3]
        offset += 4 + ((attr_len + 3) // 4) * 4
    return msg_type, code


def build_manual_checklist() -> list[dict[str, str]]:
    return [dict(item) for item in MANUAL_CHECKLIST]


def validate_mobile_attestation(raw: Any) -> list[str]:
    """人工签认文件校验：必须逐项覆盖全部清单 id，缺一返回缺失清单。"""
    if not isinstance(raw, dict):
        raise PreflightError("人工签认文件必须是 JSON 对象")
    items = raw.get("attested_items")
    if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
        raise PreflightError("attested_items 必须是字符串数组")
    required = {item["id"] for item in MANUAL_CHECKLIST}
    missing = sorted(required - set(items))
    extra = sorted(set(items) - required)
    if extra:
        raise PreflightError(f"attested_items 含未知条目: {extra}")
    if not raw.get("attested_by"):
        raise PreflightError("缺少 attested_by（签认人）")
    return missing


# ---------------------------------------------------------------- 网络探测


def _direct_opener() -> Any:
    """禁用系统代理的 opener：验收的是边缘端点本身，不是代理链路。"""
    return build_opener(ProxyHandler({}))


def _http_request(
    endpoint: Endpoint,
    path: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict[str, str]]:
    """直连 HTTP(S) 请求：返回 (状态码, 响应头)；网络层错误抛 PreflightError。

    Round 4 脱敏：只接收**已校验**的 Endpoint + 内部受控 path——错误消息
    展示 canonical origin+path（两者均无凭据面），并对底层异常文本做
    原串剥离（urllib 异常可能内嵌完整 URL）。
    """
    url = f"{endpoint.url}{path}"
    display = f"{endpoint.url}{path}"
    request = Request(url, method=method, headers=headers or {}, data=data)
    try:
        with _direct_opener().open(request, timeout=timeout) as response:
            return int(response.status), {k: v for k, v in response.headers.items()}
    except HTTPError as cause:
        return int(cause.code), {k: v for k, v in cause.headers.items()}
    except (URLError, OSError, TimeoutError) as cause:
        detail = str(cause).replace(url, "<endpoint>")
        raise PreflightError(f"请求失败 {display}: {type(cause).__name__} {detail}") from cause


def tls_probe(endpoint: Endpoint, timeout: float = 10.0) -> dict[str, Any]:
    """TLS 探测：默认验证上下文（受信 CA + 域名匹配），并返回证书事实。

    任何证书问题（自签/过期/链不完整/主机名不匹配）都会抛
    ssl.SSLError/CertificateError——调用方转为 FAIL。
    """
    context = ssl.create_default_context()
    with (
        socket.create_connection((endpoint.host, endpoint.port), timeout=timeout) as sock,
        context.wrap_socket(sock, server_hostname=endpoint.host) as tls,
    ):
        cert = tls.getpeercert() or {}
    not_after = cert.get("notAfter")
    remain_days: int | None = None
    if not_after:
        expires = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        remain_days = (expires - datetime.now(timezone.utc)).days
    return {
        "subject": ", ".join("=".join(part) for part in cert.get("subject", ())),
        "issuer": ", ".join("=".join(part) for part in cert.get("issuer", ())),
        "not_after": not_after,
        "remain_days": remain_days,
    }


def _build_ws_upgrade_request(host: str, path_with_query: str) -> str:
    """构造 RFC 6455 升级请求（含随机 Sec-WebSocket-Key 与 livekit 子协议）。"""
    key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
    return (
        f"GET {path_with_query} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "Sec-WebSocket-Protocol: livekit\r\n"
        "\r\n"
    )


def ws_upgrade_probe(endpoint: Endpoint, path_with_query: str, timeout: float = 10.0) -> int:
    """真实 WSS 升级握手：返回 HTTP 状态行数值（101 = 升级成功）。"""
    request = _build_ws_upgrade_request(endpoint.host, path_with_query)
    context = ssl.create_default_context()
    with (
        socket.create_connection((endpoint.host, endpoint.port), timeout=timeout) as sock,
        context.wrap_socket(sock, server_hostname=endpoint.host) as tls,
    ):
        tls.sendall(request.encode("ascii"))
        status_line = tls.recv(1024).decode("utf-8", "replace").split("\r\n", 1)[0]
    try:
        return int(status_line.rsplit(" ", 1)[-1])
    except (IndexError, ValueError) as cause:
        raise PreflightError(f"WSS 握手应答异常: {status_line!r}") from cause


def turn_tls_probe(host: str, port: int, timeout: float = 10.0) -> tuple[dict[str, Any], list[str]]:
    """TURN/TLS 探测：TLS 握手（受信 CA + 域名匹配）+ 未认证 Allocate 应得 401。"""
    problems: list[str] = []
    context = ssl.create_default_context()
    txid = secrets.token_bytes(12)
    with (
        socket.create_connection((host, port), timeout=timeout) as sock,
        context.wrap_socket(sock, server_hostname=host) as tls,
    ):
        cert = tls.getpeercert() or {}
        tls.sendall(build_stun_allocate(txid))
        tls.settimeout(timeout)
        data = tls.recv(2048)
    facts: dict[str, Any] = {
        "issuer": ", ".join("=".join(part) for part in cert.get("issuer", ())),
        "not_after": cert.get("notAfter"),
    }
    msg_type, code = parse_stun_response(data, txid)
    facts["stun_message_type"] = f"0x{msg_type:04x}"
    facts["error_code"] = code
    if msg_type != 0x0113:
        problems.append(f"STUN 应答类型 0x{msg_type:04x} 不是 Allocate Error（期望未认证 401 应答）")
    if code != 401:
        problems.append(f"未认证 Allocate 应答 ERROR-CODE={code}（期望 401——认证必须强制）")
    return facts, problems


# ---------------------------------------------------------------- 检查编排


def _cert_check(endpoint: Endpoint, timeout: float, checks: list[dict[str, Any]]) -> None:
    """证书链检查：任何失败都必须落入 checks（fail-closed 不静默丢弃）。"""
    name = f"cert-chain:{endpoint.host}"
    try:
        facts = tls_probe(endpoint, timeout=timeout)
    except (ssl.SSLError, OSError, TimeoutError, ValueError) as cause:
        checks.append({"name": name, "status": STATUS_FAIL, "detail": f"TLS/证书验证失败: {cause}"})
        return
    detail = (
        f"issuer={facts['issuer']} not_after={facts['not_after']} "
        f"remain_days={facts['remain_days']}"
    )
    if facts["remain_days"] is not None and facts["remain_days"] < CERT_MIN_REMAIN_DAYS:
        checks.append(
            {"name": name, "status": STATUS_FAIL, "detail": detail + f"（剩余 <{CERT_MIN_REMAIN_DAYS} 天，先续期）"}
        )
        return
    checks.append({"name": name, "status": STATUS_PASS, "detail": detail})


def run_checks(
    app_url: str | None,
    api_url: str | None,
    livekit_url: str | None,
    turn_host: str | None,
    turn_port: int,
    livekit_token: str | None,
    login_credentials: dict[str, str] | None,
    timeout: float,
) -> list[dict[str, Any]]:
    """执行全部被要求的检查（端点未提供的组件不检查也不计入）。"""
    checks: list[dict[str, Any]] = []

    def record(name: str, problems: list[str], detail: str = "") -> None:
        status = STATUS_PASS if not problems else STATUS_FAIL
        text = "; ".join(problems) if problems else detail
        checks.append({"name": name, "status": status, "detail": text})

    if app_url:
        app = parse_public_https_url(app_url)
        _cert_check(app, timeout, checks)
        status, headers = _http_request(app, "/", timeout=timeout)
        if status != 200:
            record(f"app-https:{app.host}", [f"入口返回 {status}（期望 200）"])
        else:
            record(
                f"app-https:{app.host}",
                evaluate_security_headers(headers),
                detail="HTTPS 200 + 安全响应头齐全",
            )
        cookie = headers.get("Set-Cookie", "")
        if cookie:
            problems, _attrs = evaluate_set_cookie(cookie)
            record(f"app-cookie:{app.host}", problems, detail="入口 Set-Cookie 属性合规")

    if api_url:
        api = parse_public_https_url(api_url)
        _cert_check(api, timeout, checks)
        status, _headers = _http_request(api, "/health", timeout=timeout)
        record(
            f"api-health:{api.host}",
            [] if status == 200 else [f"/health 返回 {status}（期望 200）"],
            detail="/health 200",
        )
        app_origin = parse_public_https_url(app_url).origin if app_url else None
        if app_origin:
            preflight_headers = {
                "Origin": app_origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "content-type",
            }
            status, headers = _http_request(
                api, "/health", method="OPTIONS", headers=preflight_headers, timeout=timeout
            )
            problems = evaluate_cors_headers(app_origin, headers)
            if status == 405 or status == 400:  # 非预flight路径兜底：直接带 Origin GET
                status, headers = _http_request(
                    api, "/health", headers={"Origin": app_origin}, timeout=timeout
                )
                problems = evaluate_cors_headers(app_origin, headers)
            record(
                f"api-cors:{api.host}",
                problems,
                detail=f"preflight 精确回显 {app_origin} 且允许凭据",
            )
        if login_credentials is not None:
            body = json.dumps(login_credentials).encode("utf-8")
            status, headers = _http_request(
                api,
                "/api/v1/auth/login",
                method="POST",
                headers={"Content-Type": "application/json"},
                data=body,
                timeout=timeout,
            )
            if status != 200:
                record(
                    f"api-cookie:{api.host}",
                    [f"登录返回 {status}（期望 200；凭据或后端异常，不记录响应体）"],
                )
            else:
                problems, attrs = evaluate_set_cookie(headers.get("Set-Cookie", ""))
                record(
                    f"api-cookie:{api.host}",
                    problems,
                    detail=f"登录 Set-Cookie({attrs.get('name', '?')}) Secure/SameSite=None/HttpOnly",
                )
        else:
            record(
                f"api-cookie:{api.host}",
                ["未提供 --login-credentials-file，无法证明跨源 cookie 属性（fail-closed 不跳过）"],
            )

    if livekit_url:
        livekit = parse_public_https_url(livekit_url)
        _cert_check(livekit, timeout, checks)
        status, _headers = _http_request(livekit, "/", timeout=timeout)
        record(
            f"livekit-signal:{livekit.host}",
            [] if status == 200 else [f"signal 端点返回 {status}（期望 200）"],
            detail="公网 signal 端点 TLS+反代可达",
        )
        if livekit_token:
            query = f"/rtc?access_token={livekit_token}&auto_subscribe=true&sdk=js"
            try:
                upgrade_status = ws_upgrade_probe(livekit, query, timeout=timeout)
                record(
                    f"livekit-wss:{livekit.host}",
                    [] if upgrade_status == 101 else [f"WSS 升级握手返回 {upgrade_status}（期望 101）"],
                    detail="真实 WSS 升级握手 101",
                )
            except (PreflightError, OSError) as cause:
                detail = str(cause).replace(livekit_token, "<token>")
                record(f"livekit-wss:{livekit.host}", [f"WSS 探测失败: {type(cause).__name__} {detail}"])
        else:
            record(
                f"livekit-wss:{livekit.host}",
                ["未提供 --livekit-token-file，无法完成 WSS 升级握手（fail-closed 不跳过）"],
            )

    if turn_host:
        try:
            facts, problems = turn_tls_probe(turn_host, turn_port, timeout=timeout)
            record(
                f"turn-tls:{turn_host}:{turn_port}",
                problems,
                detail=(
                    f"TLS 验证通过；STUN Allocate 未认证应答 401"
                    f"（type={facts['stun_message_type']}）"
                ),
            )
        except (ssl.SSLError, OSError, TimeoutError, ValueError, PreflightError) as cause:
            record(f"turn-tls:{turn_host}:{turn_port}", [f"TURN/TLS 探测失败: {cause}"])

    return checks


# ---------------------------------------------------------------- CLI


def _read_secret_file(path: str, what: str) -> str:
    """读取单值 secret 文件（如 LiveKit token）：内容绝不回显。"""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except (OSError, UnicodeError) as cause:
        # Round 4/5：OSError 文本内嵌文件路径；UnicodeDecodeError 文本内嵌
        # 原始字节片段——两者都只透出错误类别，绝不回显路径/字节/内容
        raise PreflightError(f"无法读取{what}文件: {type(cause).__name__}") from cause


def _read_credentials_file(path: str) -> dict[str, str]:
    """读取登录凭据 JSON 文件（username/password）：内容绝不回显。"""
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError) as cause:
        # Round 4/5：同 _read_secret_file——路径/字节片段都不进异常文本
        raise PreflightError(f"登录凭据文件不可读: {type(cause).__name__}") from cause
    except json.JSONDecodeError as cause:
        # JSONDecodeError 文本只含行/列位置，不含路径——可安全透出
        raise PreflightError(f"登录凭据文件非法 JSON: {cause}") from cause
    if not isinstance(payload, dict) or not all(
        isinstance(payload.get(key), str) and payload.get(key) for key in ("username", "password")
    ):
        raise PreflightError("登录凭据文件必须含非空字符串字段 username/password")
    return {"username": payload["username"], "password": payload["password"]}


def _write_report_atomic(path: str, report: dict[str, Any]) -> None:
    """原子写报告（temp + os.replace），失败时不清除旧文件。"""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=directory, suffix=".tmp", delete=False
    ) as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temp_name = handle.name
    try:
        os.replace(temp_name, path)
    except OSError:
        try:
            os.unlink(temp_name)
        finally:
            raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "公网边缘部署 preflight（fail-closed）：只对显式提供的公网端点发起检查；"
            "不提供任何端点则拒绝运行。手机 4G/5G 验收为人工清单（--mobile-attested-file 签认）。"
        ),
    )
    parser.add_argument("--app-url", help="Web 入口（https://app.example.com）")
    parser.add_argument("--api-url", help="API 入口（https://api.example.com）")
    parser.add_argument("--livekit-url", help="LiveKit signal 入口（https://livekit.example.com）")
    parser.add_argument("--turn-host", help="TURN/TLS 主机名（turn.example.com）")
    parser.add_argument("--turn-port", type=int, default=DEFAULT_TURN_TLS_PORT,
                        help=f"TURN/TLS 端口（默认 {DEFAULT_TURN_TLS_PORT}，协议常量）")
    parser.add_argument("--livekit-token-file", help="含真实 LiveKit token 的文件（WSS 升级握手用，绝不回显）")
    parser.add_argument("--login-credentials-file",
                        help="登录凭据 JSON 文件 {username, password}（cookie 属性验证用，绝不回显）")
    parser.add_argument("--mobile-attested-file",
                        help="人工验收签认 JSON {attested_items, attested_by, attested_at}")
    parser.add_argument("--output", help="JSON 报告输出路径（原子写）")
    parser.add_argument("--timeout", type=float, default=10.0, help="单请求超时秒数（默认 10）")
    args = parser.parse_args(argv)

    provided = [name for name, value in (
        ("app-url", args.app_url), ("api-url", args.api_url),
        ("livekit-url", args.livekit_url), ("turn-host", args.turn_host),
    ) if value]
    if not provided:
        parser.error("必须至少显式提供一个端点（--app-url/--api-url/--livekit-url/--turn-host）")

    # Round 4：先解析后持久——报告只存 canonical 端点（scheme+公网 host+port），
    # 原始 CLI 参数（可能带 userinfo/query）绝不进入报告/日志。
    try:
        canonical: dict[str, str | int | None] = {
            "app_url": parse_public_https_url(args.app_url).url if args.app_url else None,
            "api_url": parse_public_https_url(args.api_url).url if args.api_url else None,
            "livekit_url": parse_public_https_url(args.livekit_url).url if args.livekit_url else None,
            "turn_host": parse_public_turn_host(args.turn_host) if args.turn_host else None,
            "turn_port": args.turn_port if args.turn_host else None,
        }
        livekit_token = _read_secret_file(args.livekit_token_file, "LiveKit token") if args.livekit_token_file else None
        credentials = _read_credentials_file(args.login_credentials_file) if args.login_credentials_file else None
    except PreflightError as cause:
        print(f"[preflight] FAIL: {cause}", file=sys.stderr)
        return EXIT_FAILURE

    report: dict[str, Any] = {
        "schema": SCHEMA,
        "tool": TOOL_NAME,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "endpoints": canonical,
        "checks": [],
        "manual_checklist": build_manual_checklist(),
        "mobile_attestation": {"status": "pending"},
        "summary": {},
    }

    exit_code = EXIT_OK
    try:
        checks = run_checks(
            args.app_url, args.api_url, args.livekit_url, args.turn_host,
            args.turn_port, livekit_token, credentials, args.timeout,
        )
    except PreflightError as cause:
        print(f"[preflight] FAIL: {cause}", file=sys.stderr)
        return EXIT_FAILURE

    report["checks"] = checks
    failures = [check for check in checks if check["status"] != STATUS_PASS]
    report["summary"] = {
        "total": len(checks),
        "passed": len(checks) - len(failures),
        "failed": len(failures),
    }

    if args.mobile_attested_file:
        try:
            with open(args.mobile_attested_file, encoding="utf-8") as handle:
                attestation = json.load(handle)
            missing = validate_mobile_attestation(attestation)
            report["mobile_attestation"] = {
                "status": "attested" if not missing else "incomplete",
                "missing_items": missing,
                "attested_by": attestation.get("attested_by"),
            }
            if missing:
                failures = failures or [{"name": "mobile-attestation"}]
        except (OSError, json.JSONDecodeError) as cause:
            print(f"[preflight] FAIL: 人工签认文件不可读: {cause}", file=sys.stderr)
            return EXIT_FAILURE
    else:
        report["mobile_attestation"] = {
            "status": "pending",
            "missing_items": [item["id"] for item in MANUAL_CHECKLIST],
        }

    if failures:
        exit_code = EXIT_FAILURE
    elif report["mobile_attestation"]["status"] != "attested":
        exit_code = EXIT_MANUAL_PENDING

    report["exit_code"] = exit_code
    if args.output:
        try:
            _write_report_atomic(args.output, report)
        except OSError as cause:
            print(f"[preflight] FAIL: 报告写出失败: {cause}", file=sys.stderr)
            return EXIT_FAILURE

    for check in checks:
        marker = "PASS" if check["status"] == STATUS_PASS else "FAIL"
        print(f"[preflight] {marker} {check['name']}: {check['detail']}")
    print(
        f"[preflight] summary: {report['summary']['passed']}/{report['summary']['total']} pass, "
        f"mobile={report['mobile_attestation']['status']} -> exit {exit_code}"
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
