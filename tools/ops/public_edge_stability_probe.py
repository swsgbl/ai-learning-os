"""M14-180 公网边缘稳定性探针 —— 只读、有界、fail-closed 的公开静态资产采样诊断。

背景（M14-179 docs/evidence/m14-179-production-rollout-evidence/README.md §6）：
最终公共浏览器验收（直连 Chrome）全绿，但更早经系统代理的本地探针出现过
瞬态 HTTP/2 / MIME 资源错误，且公共 manifest 水合可能超过 5 秒。这些现象
当时"未采纳"就丢了细节——本工具把每一次尝试的事实结构化落档：状态码、
实际 HTTP 版本、各段耗时（DNS/连接/TLS/TTFB/总耗时）、字节数、SHA256、
Content-Type、失败类别与探针模式，让"瞬态"变成可统计的证据。

传输层选择 curl 子进程（而非 Python http.client）：HTTP/1.1 与 HTTP/2 的
显式请求（--http1.1 / --http2 经 ALPN 协商）、代理的确定性钉扎（-x）与
实际协议版本回读（%{http_version}）只有在这条路径上才可靠；每次尝试的
全部事实来自 curl write-out 与响应头/响应体转储，而非假设。

HTTP 版本能力门（采样前 fail-fast）：默认请求 HTTP/1.1（任何 curl 构建都
可靠支持）。显式 --http-version 2 时，先解析 `curl --version` 的
Features 行——构建不含 HTTP2 特性就把绝请求发出去（exit 2），绝不产生
curl-exit-2（"不支持的功能"）冒充的传输失败样本。

--ssl-no-revoke 诊断模式（默认关闭）：Windows Schannel 构建的 curl 默认
做证书吊销检查，吊销分发点不可达时 TLS 会以 exit 35 失败——该开关透传
curl --ssl-no-revoke 跳过吊销检查，专用于把"吊销检查不可达"与"边缘
TLS 本身故障"分离开。诚实边界：该模式**削弱证书校验强度，只用于诊断，
不得用于验收**；每个样本与报告 config 都显式记录该模式是否开启。

Fail-closed 契约（违反任何一条都不放行）：
- 只读：仅 GET 公开静态资产；无认证、无 cookie、不跟随重定向、不写任何
  远端状态；
- 零重试：一个样本 = 恰一次 curl 调用；失败如实记录失败类别，绝不重试、
  绝不静默跳过（重试会掩盖瞬态失败，而瞬态失败正是本工具要捕捉的对
  象）；非零退出的部分传输同样记账——body dump 存在即记实际字节数与
  SHA256 并标 body_complete=false，失败类别仍以传输错误为准；
- 计时口径诚实：curl 的 time_* 全部是累计时间戳（appconnect 含 TCP，
  starttransfer 含 TLS），报告保留 elapsed/ttfb 两个累计值，dns/tcp/
  tls/server_wait 阶段时长一律相减派生，绝不把累计值冒充阶段值；
- 请求负载有界：--samples 默认 5、硬上限 50；--interval 默认 1.0s、下限
  0.5s（样本之间强制间隔）；单请求 --timeout 默认 15s；重复采样只允许
  打小静态资产（如 /aios/download-manifest.json）；
- 大资产（APK）至多下载一次：--large-asset-url/sha256/size 三参必须同时
  提供，恰在采样循环之后执行**单次**校验下载（结构上不可能进入重复
  采样循环），SHA256/字节数与期望值不符即记录 checksum-mismatch /
  size-mismatch，不重试；
- URL 入口校验：仅 https + 公网 host（loopback/私网/RFC 5737/RFC 2606
  占位域全拒绝）+ 无 userinfo/query/fragment + 无 dot segments/编码字符/
  双斜杠的纯静态资产路径；代理 URL 仅 scheme+host+port（显式端口），
  userinfo（内嵌凭据）一律入口拒绝——本工具永不读取代理环境变量，直连
  模式用 --noproxy "*" 结构性绕开系统代理；
- 输出只含白名单字段：报告不含本机绝对路径、不含 curl stderr 原文
  （可能内嵌 URL/代理串）、不含响应头原文（只记解析后的 Content-Type）、
  不含任何 secret（入口已拒绝一切凭据面）。

Exit codes: 0 = 全部尝试零失败类别；1 = 探针已完成但存在 ≥1 条失败类别
记录（证据照常写出）；2 = 参数非法 / 工具无法运行（curl 缺失或版本低于
7.75、请求的 HTTP/2 超出本机 curl 能力、运行器故障、报告写出失败）——
argparse 参数错误沿用其默认码 2。
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import urlsplit

SCHEMA = "aios-public-edge-stability-probe/1"
TOOL_NAME = "public_edge_stability_probe"
USER_AGENT = f"{TOOL_NAME}/1 (read-only)"

DEFAULT_SAMPLES = 5
MAX_SAMPLES = 50
DEFAULT_INTERVAL_S = 1.0
MIN_INTERVAL_S = 0.5
DEFAULT_TIMEOUT_S = 15.0
MIN_TIMEOUT_S = 1.0
MAX_TIMEOUT_S = 120.0
# write-out 需要 %{http_version}（curl 7.50）与 %{exitcode}（curl 7.75）
# ——低于 7.75 拒绝运行（fail-closed，避免静默丢失事实）。
MIN_CURL_VERSION = (7, 75)
SUBPROCESS_GRACE_S = 10.0  # subprocess 超时 = 请求超时 + 余量（防进程悬挂）

MODE_DIRECT = "direct"
MODE_PROXY = "proxy"
HTTP11 = "1.1"
HTTP2 = "2"

# build_curl_argv 的 argv[0] 占位符：RealCurlRunner 以真实 curl 路径替换，
# 测试假运行器断言 argv[1:] 的形状（不依赖本机 curl 存在）。
CURL_PLACEHOLDER = "curl"

# RFC 2606/6761 保留：占位域名不得作为"公网资产"参与探测（裸后缀匹配，
# 覆盖裸域名与其全部子孙域）。与 tools/ops/public_edge_preflight.py 同源约定。
RESERVED_DOMAIN_SUFFIXES = (
    "example.com",
    "example.net",
    "example.org",
    "example",
    "invalid",
    "localhost",
    "test",
)

# curl 退出码 → 失败类别（确定性映射；未知码回落为 curl-exit-N）。
# 语义来源：curl 文档 "Exit Codes" 章节。
CURL_EXIT_CATEGORIES: dict[int, str] = {
    5: "proxy-dns-error",
    6: "dns-error",
    7: "connect-error",
    8: "malformed-server-reply",
    16: "http2-error",
    28: "timeout",
    35: "tls-error",
    55: "send-error",
    56: "recv-error",
    92: "http2-error",  # HTTP/2 stream 层错误
    93: "http3-error",
    94: "http3-error",
    95: "http3-error",
    97: "proxy-error",
}

# 派生失败类别（curl exit 0 之后按此顺序判定，首个命中即记录）。
CATEGORY_HTTP_ERROR = "http-error-status"
CATEGORY_REDIRECT = "http-redirect"
CATEGORY_CONTENT_TYPE = "content-type-mismatch"
CATEGORY_BYTE_COUNT = "byte-count-mismatch"
CATEGORY_SIZE = "size-mismatch"
CATEGORY_CHECKSUM = "checksum-mismatch"

# write-out 模板：只含数值与受限字符集变量（http_version/remote_ip 的取值
# 字符集由 curl 保证，绝无用户可控字符串进入 JSON 文本）。
_WRITE_OUT = (
    '{"response_code":%{response_code},"http_version":"%{http_version}",'
    '"exitcode":%{exitcode},"time_namelookup":%{time_namelookup},'
    '"time_connect":%{time_connect},"time_appconnect":%{time_appconnect},'
    '"time_starttransfer":%{time_starttransfer},"time_total":%{time_total},'
    '"size_download":%{size_download},"num_redirects":%{num_redirects},'
    '"remote_ip":"%{remote_ip}"}'
)


class ProbeError(Exception):
    """参数/环境非法（fail-closed，直接进入退出码 2 路径，不发任何请求）。"""


# ---------------------------------------------------------------- 入口校验


def is_public_host(host: str) -> bool:
    """公网主机判定：保留域与任何非全局地址一律拒绝（探针只打公网边缘）。"""
    lowered = host.rstrip(".").lower()
    if not lowered:
        return False
    for suffix in RESERVED_DOMAIN_SUFFIXES:
        if lowered == suffix or lowered.endswith("." + suffix):
            return False
    try:
        ip = ipaddress.ip_address(lowered)
    except ValueError:
        return True  # 普通主机名：通过（可达性由 curl 探测验证）
    return ip.is_global


def _reject_path_shape(path: str) -> str | None:
    """静态资产路径形状校验：返回拒绝原因（None = 通过）。

    允许：以 `/` 开头的普通路径段。拒绝：dot segments（`.`/`..`）、百分号
    编码、反斜杠、空段（双斜杠）——归一化/解码一律不做，形状不对即拒绝。
    """
    if not path.startswith("/"):
        return "path 必须以 / 开头"
    if "%" in path or "\\" in path:
        return "path 含编码字符或反斜杠"
    segments = path.split("/")[1:]
    if any(segment in (".", "..") for segment in segments):
        return "path 含 dot segment"
    if any(segment == "" for segment in segments[:-1]) or (len(segments) > 1 and segments[-1] == ""):
        return "path 含空段（双斜杠或尾斜杠）"
    return None


@dataclass(frozen=True)
class AssetUrl:
    """已校验的公网 https 静态资产 URL（canonical 重构值，无任何凭据面）。

    `host` 存裸主机值（urlsplit hostname 语义，IPv6 不带方括号）；
    `url` 是 canonical 重构值——IPv6 字面量**必须**带方括号（RFC 3986
    host 生产式），否则 `https://2606:4700::6810:85e5/a.json` 里冒号
    会被误读为端口分隔符。
    """

    url: str
    host: str
    port: int


def format_canonical_host(scheme: str, host: str, port: int, default_port: int) -> str:
    """共享 canonical origin 格式化：scheme://host[:port]，IPv6 加方括号。

    资产 URL 与代理 display 共用——IPv6 字面量（裸值含 `:`）必须包裹
    `[...]`（否则 origin 内冒号歧义）；IPv4/域名原样；port 等于
    default_port 时省略（仅资产 443 语义，代理恒显式端口）。
    """
    bracketed = f"[{host}]" if ":" in host else host
    suffix = "" if port == default_port else f":{port}"
    return f"{scheme}://{bracketed}{suffix}"


def parse_public_asset_url(raw: str) -> AssetUrl:
    """解析并校验公开静态资产 URL；非法即抛 ProbeError（不回显原始输入）。"""
    if not raw or not raw.strip():
        raise ProbeError("空 URL")
    parts = urlsplit(raw.strip())
    if parts.scheme != "https":
        raise ProbeError("必须是 https URL")
    if parts.username or parts.password:
        raise ProbeError("URL 不得携带 userinfo")
    host = (parts.hostname or "").lower()
    if not host:
        raise ProbeError("URL 缺少主机名")
    if not is_public_host(host):
        raise ProbeError("主机不是公网地址（loopback/私网/保留段/占位域均拒绝）")
    try:
        port = parts.port
    except ValueError as cause:
        raise ProbeError("端口非法") from cause
    port = 443 if port is None else port
    if not 1 <= port <= 65535:
        raise ProbeError("端口越界（1-65535）")
    reason = _reject_path_shape(parts.path)
    if reason is not None:
        raise ProbeError(f"URL path 非法：{reason}")
    if not parts.path or parts.path == "/":
        raise ProbeError("URL 必须指向具体静态资产路径（不接受裸 origin）")
    if parts.query:
        raise ProbeError("URL 不得携带 query")
    if parts.fragment:
        raise ProbeError("URL 不得携带 fragment")
    canonical = format_canonical_host("https", host, port, default_port=443) + parts.path
    return AssetUrl(url=canonical, host=host, port=port)


@dataclass(frozen=True)
class ProxyTarget:
    """已校验的显式代理（scheme + host + 显式 port；host 允许 loopback）。

    `display` 是 canonical 重构值：IPv6 字面量（如 `[::1]`）带方括号，
    端口恒显式（代理无默认端口省略语义）。
    """

    display: str  # canonical scheme://[host]:port（无凭据面）


def parse_proxy_url(raw: str) -> ProxyTarget:
    """解析并校验代理 URL：仅 scheme+host+显式 port，userinfo/path/query 全拒。"""
    if not raw or not raw.strip():
        raise ProbeError("空代理 URL")
    parts = urlsplit(raw.strip())
    if parts.scheme not in ("http", "https"):
        raise ProbeError("代理 scheme 仅允许 http/https")
    if parts.username or parts.password:
        raise ProbeError("代理 URL 不得携带 userinfo（凭据一律入口拒绝）")
    host = (parts.hostname or "").lower()
    if not host:
        raise ProbeError("代理 URL 缺少主机名")
    try:
        port = parts.port
    except ValueError as cause:
        raise ProbeError("代理端口非法") from cause
    if port is None:
        raise ProbeError("代理 URL 必须显式给出端口（如 http://127.0.0.1:7892）")
    if not 1 <= port <= 65535:
        raise ProbeError("代理端口越界（1-65535）")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ProbeError("代理 URL 仅允许 scheme+host+port")
    # default_port=0：代理端口恒显式（0 不是合法端口，永不触发省略分支）
    return ProxyTarget(display=format_canonical_host(parts.scheme, host, port, default_port=0))


# ---------------------------------------------------------------- 失败类别


def categorize_curl_exit(exit_code: int) -> str:
    """curl 退出码 → 确定性失败类别；未知码回落 curl-exit-N。"""
    if exit_code == 0:
        raise ValueError("exit 0 不映射失败类别（走派生判定）")
    return CURL_EXIT_CATEGORIES.get(exit_code, f"curl-exit-{exit_code}")


def parse_content_type(header_text: str) -> str | None:
    """从响应头转储解析 Content-Type（大小写不敏感；无则 None）。"""
    for line in header_text.splitlines():
        if line.lower().startswith("content-type:"):
            return line.split(":", 1)[1].strip()
    return None


def media_type_matches(content_type: str | None, expected: str) -> bool:
    """期望媒体类型前缀匹配：比较 `;` 前的小写 media type（charset 等参数忽略）。"""
    if not content_type:
        return False
    media = content_type.split(";", 1)[0].strip().lower()
    return media == expected.strip().lower()


def derive_failure_category(
    *,
    exit_code: int,
    status: int | None,
    content_type: str | None,
    expect_content_type: str | None,
    body_size: int | None,
    reported_size: int | None,
    expected_size: int | None,
    sha256: str | None,
    expected_sha256: str | None,
) -> str | None:
    """单次尝试的最终失败类别（纯函数；首个命中即返回，None = 无失败）。"""
    if exit_code != 0:
        return categorize_curl_exit(exit_code)
    if status is None:
        return "no-response"
    if status >= 400:
        return CATEGORY_HTTP_ERROR
    if 300 <= status < 400:
        return CATEGORY_REDIRECT  # 静态资产必须 200 直出，重定向即稳定性问题
    if expect_content_type is not None and not media_type_matches(content_type, expect_content_type):
        return CATEGORY_CONTENT_TYPE
    if body_size is not None and reported_size is not None and body_size != reported_size:
        return CATEGORY_BYTE_COUNT
    if expected_size is not None and body_size != expected_size:
        return CATEGORY_SIZE
    if expected_sha256 is not None and sha256 != expected_sha256:
        return CATEGORY_CHECKSUM
    return None


# ---------------------------------------------------------------- curl 运行器（注入点）


class CurlRunner(Protocol):
    """curl 子进程注入点：真实实现走 subprocess，测试注入假实现（零网络）。"""

    def version(self) -> str:
        """返回 `curl --version` 完整输出（版本行 + Features 行）；失败抛 RunnerError。"""

    def probe(self, argv: list[str], *, timeout: float) -> tuple[int, str]:
        """执行一次探针调用：返回 (退出码, stdout=write-out JSON 文本)。"""


class RunnerError(Exception):
    """运行器自身故障（进程缺失/超时）；类别化兜底，文本不进报告。"""


class RealCurlRunner:
    """subprocess 直调 curl；调用方负责 argv 已由 build_curl_argv 构造。"""

    def __init__(self, curl_path: str) -> None:
        self._curl = curl_path

    def version(self) -> str:
        try:
            result = subprocess.run(
                [self._curl, "--version"], capture_output=True, text=True,
                timeout=30.0, check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as cause:
            raise RunnerError(type(cause).__name__) from cause
        if result.returncode != 0 or not result.stdout.strip():
            raise RunnerError("curl --version failed")
        return result.stdout.strip()

    def probe(self, argv: list[str], *, timeout: float) -> tuple[int, str]:
        real_argv = [self._curl, *argv[1:]]  # 占位符 argv[0] → 真实 curl 路径
        try:
            result = subprocess.run(
                real_argv, capture_output=True, text=True,
                timeout=timeout + SUBPROCESS_GRACE_S, check=False,
            )
        except subprocess.TimeoutExpired as cause:
            raise RunnerError("subprocess-timeout") from cause
        except OSError as cause:
            raise RunnerError(type(cause).__name__) from cause
        return result.returncode, result.stdout


def parse_curl_version(first_line: str) -> tuple[int, int]:
    """`curl 8.21.0 (...)` → (8, 21)；解析不出抛 ValueError。"""
    match = re.match(r"^curl\s+(\d+)\.(\d+)", first_line.strip())
    if not match:
        raise ValueError("unrecognized curl version line")
    return int(match.group(1)), int(match.group(2))


def parse_curl_features(version_text: str) -> set[str]:
    """`curl --version` 完整输出 → Features 词集（无 Features 行则空集）。

    用于 HTTP/2 能力门：构建不含 HTTP2 特性时 --http2 会以 curl exit 2
    （不支持的功能）失败——那不是传输失败，必须在采样前拦下。
    """
    for line in version_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Features:"):
            return set(stripped.split(":", 1)[1].split())
    return set()


def build_curl_argv(
    url: str,
    *,
    proxy: str | None,
    http_version: str,
    timeout_s: float,
    body_path: str,
    header_path: str,
    ssl_no_revoke: bool = False,
) -> list[str]:
    """构造一次探针调用的 curl argv（纯函数，测试直接断言其形状）。

    只读 GET、无重定向跟随、无认证；直连模式 --noproxy "*" 结构性绕开
    系统代理；代理模式 -x 钉扎显式代理（不读代理环境变量）；
    ssl_no_revoke 仅在显式诊断模式透传 --ssl-no-revoke（Schannel 吊销
    检查不可达的隔离诊断，削弱证书校验强度，不用于验收）。
    """
    argv = [
        CURL_PLACEHOLDER,  # RealCurlRunner 以真实路径替换 argv[0]
        "-sS",
        "--max-time",
        f"{timeout_s:g}",
        "--http1.1" if http_version == HTTP11 else "--http2",
        "-H",
        f"User-Agent: {USER_AGENT}",
        "-H",
        "Accept: */*",
        "-o",
        body_path,
        "-D",
        header_path,
        "-w",
        _WRITE_OUT,
    ]
    if ssl_no_revoke:
        argv.append("--ssl-no-revoke")
    if proxy is None:
        argv += ["--noproxy", "*"]
    else:
        argv += ["-x", proxy]
    argv.append(url)
    return argv


# ---------------------------------------------------------------- 单次尝试


def _sha256_file(path: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while chunk := handle.read(1 << 16):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _round_ms(seconds: float | None) -> float | None:
    return None if seconds is None else round(seconds * 1000.0, 3)


def derive_phase_timings(
    *,
    namelookup_s: float | None,
    connect_s: float | None,
    appconnect_s: float | None,
    starttransfer_s: float | None,
) -> dict[str, float | None]:
    """curl 累计计时 → 派生阶段时长（纯函数，毫秒、三位小数）。

    curl 的 time_* 全部是**累计**时间戳（后一阶段包含前一阶段）：
    time_namelookup = DNS 完成；time_connect = TCP 完成（含 DNS）；
    time_appconnect = TLS 完成（含 TCP）；time_starttransfer = 首字节
    （含 TLS）。阶段时长必须相减：
      dns_ms         = namelookup
      tcp_ms         = connect − namelookup
      tls_ms         = appconnect − connect（无 TLS/未到达则 None）
      server_wait_ms = starttransfer − appconnect（握手后到首字节的
                       服务器处理+网络等待；TLS 未完成/未到达 appconnect
                       时为 None——绝不回退为累计 starttransfer，那会与
                       ttfb_ms 重复且混入 DNS/TCP）
    相减结果为负（时钟异常/乱序）时该阶段记 None（fail-closed，不输出
    误导性的负时长）。
    """
    def phase(later: float | None, earlier: float | None) -> float | None:
        if later is None or earlier is None or later < 0 or earlier < 0:
            return None
        value = _round_ms(later - earlier)
        return value if value is not None and value >= 0 else None

    def server_wait() -> float | None:
        if (appconnect_s or 0) <= 0 or starttransfer_s is None or starttransfer_s < 0:
            return None  # 无 TLS 基准点：server_wait 不可派生（ttfb_ms 已单独保留累计值）
        return phase(starttransfer_s, appconnect_s)

    return {
        "dns_ms": _round_ms(namelookup_s) if namelookup_s is not None and namelookup_s >= 0 else None,
        "tcp_ms": phase(connect_s, namelookup_s),
        "tls_ms": phase(appconnect_s, connect_s) if (appconnect_s or 0) > 0 and (connect_s or 0) > 0 else None,
        "server_wait_ms": server_wait(),
    }


def run_attempt(
    runner: CurlRunner,
    *,
    index: int,
    asset: str,
    url: str,
    proxy: str | None,
    http_version: str,
    timeout_s: float,
    body_path: str,
    header_path: str,
    expect_content_type: str | None,
    expected_size: int | None,
    expected_sha256: str | None,
    ssl_no_revoke: bool,
    clock: Callable[[], str],
) -> dict[str, Any]:
    """执行恰一次 curl 调用并落一条样本记录（失败也落记录，绝不重试）。"""
    started_at = clock()
    argv = build_curl_argv(
        url,
        proxy=proxy,
        http_version=http_version,
        timeout_s=timeout_s,
        body_path=body_path,
        header_path=header_path,
        ssl_no_revoke=ssl_no_revoke,
    )
    exit_code, stdout = runner.probe(argv, timeout=timeout_s)
    facts: dict[str, Any] = {}
    try:
        parsed = json.loads(stdout.strip())
        if isinstance(parsed, dict):
            facts = parsed
    except (json.JSONDecodeError, ValueError):
        facts = {}  # write-out 不可解析 = 传输事实缺失，走 no-response 判定
    if facts.get("exitcode") not in (None, exit_code):
        # write-out 内嵌退出码与进程退出码不一致 = 事实不可信（fail-closed）
        facts = {}

    # curl write-out 用 0 表示"无响应/无协议"哨兵——归一为 None，不与真实
    # HTTP 200/1.1 混淆。
    raw_status = _as_int(facts.get("response_code"))
    status = raw_status if raw_status and raw_status > 0 else None
    raw_version = facts.get("http_version") if facts else None
    http_version_actual = raw_version if raw_version not in (None, "", "0") else None
    # 部分传输记账：非零退出（如超时）可能已在 -o 落盘部分字节——只要
    # dump 存在就记录实际字节数与 SHA256（诊断事实），body_complete 标明
    # 传输是否完整；失败类别仍以传输错误为准（authoritative）。
    body_sha: str | None = None
    body_size: int | None = None
    if os.path.exists(body_path):
        body_sha, body_size = _sha256_file(body_path)
    content_type = parse_content_type(_read_text(header_path)) if os.path.exists(header_path) else None
    category = derive_failure_category(
        exit_code=exit_code,
        status=status,
        content_type=content_type,
        expect_content_type=expect_content_type,
        body_size=body_size,
        reported_size=_as_int(facts.get("size_download")),
        expected_size=expected_size,
        sha256=body_sha,
        expected_sha256=expected_sha256,
    )
    phases = derive_phase_timings(
        namelookup_s=_as_float(facts.get("time_namelookup")),
        connect_s=_as_float(facts.get("time_connect")),
        appconnect_s=_as_float(facts.get("time_appconnect")),
        starttransfer_s=_as_float(facts.get("time_starttransfer")),
    )
    return {
        "index": index,
        "asset": asset,
        "started_at": started_at,
        "probe_mode": MODE_PROXY if proxy else MODE_DIRECT,
        "http_version_requested": http_version,
        "http_version": http_version_actual,
        "ssl_no_revoke": ssl_no_revoke,
        "status": status,
        # 累计口径保留两个有用的原始值：总耗时与 TTFB（starttransfer）。
        "elapsed_ms": _round_ms(_as_float(facts.get("time_total"))),
        "ttfb_ms": _round_ms(_as_float(facts.get("time_starttransfer"))),
        # 派生阶段口径（curl 计时全部为累计值，阶段时长必须相减得到）。
        **phases,
        "size_bytes": body_size,
        "sha256": body_sha,
        "body_complete": exit_code == 0,
        "content_type": content_type,
        "remote_ip": (facts.get("remote_ip") or None),
        "exit_code": exit_code,
        "failure_category": category,
    }


def _as_int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def _as_float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


# ---------------------------------------------------------------- 编排与汇总


@dataclass(frozen=True)
class ProbeConfig:
    """一次探针运行的完整配置（全部经入口校验后的 canonical 值）。"""

    url: AssetUrl
    proxy: ProxyTarget | None
    http_version: str
    samples: int
    interval_s: float
    timeout_s: float
    expect_content_type: str | None
    large_asset_url: AssetUrl | None
    large_asset_sha256: str | None
    large_asset_size: int | None
    ssl_no_revoke: bool = False


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """纯函数汇总：失败类别/状态/实际协议版本分布、SHA 漂移与耗时统计。"""
    ok = [r for r in records if r["failure_category"] is None]
    failed = [r for r in records if r["failure_category"] is not None]
    categories: dict[str, int] = {}
    for record in failed:
        categories[record["failure_category"]] = categories.get(record["failure_category"], 0) + 1
    statuses: dict[str, int] = {}
    versions: dict[str, int] = {}
    for record in records:
        if record["status"] is not None:
            key = str(record["status"])
            statuses[key] = statuses.get(key, 0) + 1
        if record["http_version"]:
            versions[record["http_version"]] = versions.get(record["http_version"], 0) + 1
    elapsed = [r["elapsed_ms"] for r in ok if r["elapsed_ms"] is not None]
    shas = sorted({r["sha256"] for r in records if r["asset"] == "primary" and r["sha256"]})
    ttfb = [r["ttfb_ms"] for r in ok if r["ttfb_ms"] is not None]
    return {
        "records_total": len(records),
        "ok": len(ok),
        "failed": len(failed),
        "failure_categories": categories,
        "statuses": statuses,
        "http_versions": versions,
        "distinct_primary_sha256": shas,
        "elapsed_ms_min": min(elapsed) if elapsed else None,
        "elapsed_ms_mean": (round(sum(elapsed) / len(elapsed), 3) if elapsed else None),
        "elapsed_ms_max": max(elapsed) if elapsed else None,
        "ttfb_ms_max": max(ttfb) if ttfb else None,
    }


def run_probe(
    config: ProbeConfig,
    runner: CurlRunner,
    sleeper: Callable[[float], None],
    clock: Callable[[], str],
) -> dict[str, Any]:
    """执行有界采样 + 可选单次大资产校验，返回完整报告 dict（纯编排）。"""
    proxy_display = config.proxy.display if config.proxy else None
    version_text = runner.version()
    first_line = version_text.splitlines()[0] if version_text.strip() else ""
    try:
        major, minor = parse_curl_version(first_line)
    except ValueError as cause:
        # curl --version 输出不可识别 = 无法证明版本能力 → fail-closed
        #（exit 2，零请求），绝不让 ValueError 裸奔成栈轨迹。
        raise ProbeError(f"无法解析 curl 版本行: {first_line!r}") from cause
    if (major, minor) < MIN_CURL_VERSION:
        raise ProbeError(
            f"curl 版本过低：需要 >= {MIN_CURL_VERSION[0]}.{MIN_CURL_VERSION[1]}"
            f"（write-out http_version/exitcode 支持），实测 {major}.{minor}"
        )
    features = parse_curl_features(version_text)
    has_http2 = "HTTP2" in features
    if config.http_version == HTTP2 and not has_http2:
        # 能力门（采样前 fail-fast）：构建不支持 --http2 时 curl 会以
        # exit 2（不支持的功能）失败——那是配置错误，不是传输失败，
        # 绝不让它冒充样本。
        raise ProbeError(
            "本机 curl 构建不含 HTTP2 特性，--http-version 2 不可用；"
            "请用默认 --http-version 1.1（可靠）"
        )
    records: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="aios-edge-probe-") as workdir:
        for index in range(config.samples):
            if index > 0:
                sleeper(config.interval_s)  # 样本间强制间隔（负载有界）
            records.append(
                run_attempt(
                    runner,
                    index=index,
                    asset="primary",
                    url=config.url.url,
                    proxy=proxy_display,
                    http_version=config.http_version,
                    timeout_s=config.timeout_s,
                    body_path=os.path.join(workdir, f"body-{index}.bin"),
                    header_path=os.path.join(workdir, f"headers-{index}.txt"),
                    expect_content_type=config.expect_content_type,
                    expected_size=None,
                    expected_sha256=None,
                    ssl_no_revoke=config.ssl_no_revoke,
                    clock=clock,
                )
            )
        if config.large_asset_url is not None:
            # 大资产（APK）恰一次校验下载：结构上不进入上面的采样循环。
            records.append(
                run_attempt(
                    runner,
                    index=config.samples,
                    asset="large",
                    url=config.large_asset_url.url,
                    proxy=proxy_display,
                    http_version=config.http_version,
                    timeout_s=config.timeout_s,
                    body_path=os.path.join(workdir, "body-large.bin"),
                    header_path=os.path.join(workdir, "headers-large.txt"),
                    expect_content_type=None,
                    expected_size=config.large_asset_size,
                    expected_sha256=config.large_asset_sha256,
                    ssl_no_revoke=config.ssl_no_revoke,
                    clock=clock,
                )
            )
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "tool": TOOL_NAME,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "curl_version": first_line.split(" (")[0].strip(),
        "curl_features_has_http2": has_http2,
        "config": {
            "url": config.url.url,
            "probe_mode": MODE_PROXY if proxy_display else MODE_DIRECT,
            "proxy": proxy_display,
            "http_version_requested": config.http_version,
            "ssl_no_revoke": config.ssl_no_revoke,
            "samples": config.samples,
            "interval_s": config.interval_s,
            "timeout_s": config.timeout_s,
            "expect_content_type": config.expect_content_type,
            "large_asset": (
                {
                    "url": config.large_asset_url.url,
                    "expected_sha256": config.large_asset_sha256,
                    "expected_size_bytes": config.large_asset_size,
                    "download_policy": "at-most-once",
                }
                if config.large_asset_url
                else None
            ),
        },
        "samples": records,
        "summary": summarize(records),
    }
    return report


# ---------------------------------------------------------------- CLI


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


def _bounded_int(low: int, high: int, what: str) -> Callable[[str], int]:
    def validate(raw: str) -> int:
        try:
            value = int(raw)
        except ValueError as cause:
            raise argparse.ArgumentTypeError(f"{what} 必须是整数") from cause
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"{what} 越界（{low}-{high}）")
        return value

    return validate


def _bounded_float(low: float, high: float, what: str) -> Callable[[str], float]:
    def validate(raw: str) -> float:
        try:
            value = float(raw)
        except ValueError as cause:
            raise argparse.ArgumentTypeError(f"{what} 必须是数值") from cause
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"{what} 越界（{low:g}-{high:g}）")
        return value

    return validate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "公网边缘稳定性探针（只读、有界、fail-closed）：对一个小静态资产"
            "重复采样，记录每次尝试的状态/HTTP 版本/耗时/字节数/SHA256/失败"
            "类别；可选对大资产（APK）做至多一次校验下载。零重试。"
        ),
    )
    parser.add_argument("--url", required=True, help="小静态资产 https URL（重复采样目标）")
    parser.add_argument(
        "--samples",
        type=_bounded_int(1, MAX_SAMPLES, "--samples"),
        default=DEFAULT_SAMPLES,
        help=f"采样次数（默认 {DEFAULT_SAMPLES}，上限 {MAX_SAMPLES}）",
    )
    parser.add_argument(
        "--interval",
        type=_bounded_float(MIN_INTERVAL_S, 3600.0, "--interval"),
        default=DEFAULT_INTERVAL_S,
        help=f"样本间隔秒（默认 {DEFAULT_INTERVAL_S}，下限 {MIN_INTERVAL_S}）",
    )
    parser.add_argument(
        "--timeout",
        type=_bounded_float(MIN_TIMEOUT_S, MAX_TIMEOUT_S, "--timeout"),
        default=DEFAULT_TIMEOUT_S,
        help=f"单请求超时秒（默认 {DEFAULT_TIMEOUT_S}）",
    )
    parser.add_argument(
        "--http-version",
        choices=(HTTP11, HTTP2),
        default=HTTP11,
        help=(
            "请求的 HTTP 版本：1.1 强制（默认，任何 curl 构建可靠）；"
            "2 经 ALPN 协商且要求 curl Features 含 HTTP2（否则采样前 exit 2），"
            "实际版本逐样本回读"
        ),
    )
    parser.add_argument(
        "--proxy",
        help="显式代理 scheme://host:port（如 http://127.0.0.1:7892）；缺省=直连（绕开系统代理）",
    )
    parser.add_argument(
        "--ssl-no-revoke",
        action="store_true",
        help=(
            "诊断模式：透传 curl --ssl-no-revoke（跳过 Schannel 证书吊销检查），"
            "用于把『吊销分发点不可达』与『边缘 TLS 故障』分离；"
            "削弱证书校验强度，只用于诊断，不得用于验收"
        ),
    )
    parser.add_argument(
        "--expect-content-type",
        help="期望 media type（如 application/json；`;` 参数忽略，不匹配记 content-type-mismatch）",
    )
    parser.add_argument("--large-asset-url", help="大资产（APK）https URL——至多下载一次做校验")
    parser.add_argument("--large-asset-sha256", help="大资产期望 SHA256（hex）")
    parser.add_argument(
        "--large-asset-size", type=_bounded_int(1, 4 << 30, "--large-asset-size"), help="大资产期望字节数"
    )
    parser.add_argument("--output", help="JSON 报告输出路径（原子写）")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        asset = parse_public_asset_url(args.url)
        proxy = parse_proxy_url(args.proxy) if args.proxy else None
        large_flags = (args.large_asset_url, args.large_asset_sha256, args.large_asset_size)
        if any(large_flags) and not all(large_flags):
            raise ProbeError("--large-asset-url/sha256/size 必须同时提供（三缺一拒绝）")
        large_asset = parse_public_asset_url(args.large_asset_url) if args.large_asset_url else None
        if large_asset is not None and large_asset.url == asset.url:
            raise ProbeError("大资产 URL 不得与重复采样 URL 相同（大资产至多下载一次）")
        sha256_pattern = re.fullmatch(r"[0-9a-fA-F]{64}", args.large_asset_sha256 or "")
        if args.large_asset_sha256 and sha256_pattern is None:
            raise ProbeError("--large-asset-sha256 必须是 64 位 hex")
    except ProbeError as cause:
        print(f"[probe] FAIL: {cause}", file=sys.stderr)
        return 2

    curl_path = shutil.which("curl")
    if not curl_path:
        print(f"[probe] FAIL: 未找到 curl（需要 curl >= {MIN_CURL_VERSION[0]}.{MIN_CURL_VERSION[1]} 于 PATH）", file=sys.stderr)
        return 2
    runner: CurlRunner = RealCurlRunner(curl_path)

    config = ProbeConfig(
        url=asset,
        proxy=proxy,
        http_version=args.http_version,
        samples=args.samples,
        interval_s=args.interval,
        timeout_s=args.timeout,
        expect_content_type=args.expect_content_type,
        large_asset_url=large_asset,
        large_asset_sha256=args.large_asset_sha256.lower() if args.large_asset_sha256 else None,
        large_asset_size=args.large_asset_size,
        ssl_no_revoke=args.ssl_no_revoke,
    )
    try:
        report = run_probe(config, runner, time.sleep, lambda: datetime.now(timezone.utc).isoformat(timespec="milliseconds"))
    except (ProbeError, RunnerError) as cause:
        print(f"[probe] FAIL: {type(cause).__name__}: {cause}", file=sys.stderr)
        return 2

    exit_code = 0 if report["summary"]["failed"] == 0 else 1
    report["exit_code"] = exit_code
    if args.output:
        try:
            _write_report_atomic(args.output, report)
        except OSError as cause:
            print(f"[probe] FAIL: 报告写出失败: {type(cause).__name__}", file=sys.stderr)
            return 2

    summary = report["summary"]
    print(
        f"[probe] mode={report['config']['probe_mode']} http_version_requested={config.http_version}"
        f" curl_has_http2={report['curl_features_has_http2']}"
        f" ssl_no_revoke={config.ssl_no_revoke}"
        f" samples={summary['ok']}ok/{summary['failed']}failed"
        f" versions={summary['http_versions']} statuses={summary['statuses']}"
        f" elapsed_ms[min/mean/max]={summary['elapsed_ms_min']}/{summary['elapsed_ms_mean']}"
        f"/{summary['elapsed_ms_max']} distinct_sha={len(summary['distinct_primary_sha256'])}"
    )
    for record in report["samples"]:
        marker = "OK " if record["failure_category"] is None else "ERR"
        print(
            f"[probe] {marker} #{record['index']} asset={record['asset']}"
            f" status={record['status']} http={record['http_version']}"
            f" elapsed_ms={record['elapsed_ms']} ttfb_ms={record['ttfb_ms']}"
            f" bytes={record['size_bytes']} sha={record['sha256'][:12] if record['sha256'] else '-'}"
            f" ct={record['content_type'] or '-'} category={record['failure_category'] or '-'}"
        )
    print(f"[probe] summary -> exit {exit_code}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
