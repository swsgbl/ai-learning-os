#!/usr/bin/env python3
"""M14-26 WSL 语音健康只读 sidecar：18010/18011 双端口窄代理。

背景（M14-25 回填实证，2026-09-14）：Windows→WSL loopback 转发不稳
（wslrelay.exe 间歇 5s 超时、WSL/Service 0x8007274c、TimeoutExpired——
7 轮失败窗口），而 FunASR（127.0.0.1:8010）/CosyVoice（127.0.0.1:8011）
只绑 WSL loopback，Windows 侧监控探针持续超时。本 sidecar 跑在 WSL 内、
显式绑 eth0 私网 IPv4，为 Windows 侧提供**不经 wslrelay 的直达健康路径**。
不改动现有引擎与 relay（部署面零侵入，失败可整体回收）。

安全模型（窄而硬，全部 fail-closed）：
- 绑定地址：解析 /proc/net/route 取默认路由接口（eth0）及其网段，UDP
  connect 本地路由探测源地址（零发包，仅查内核路由表）；仅接受 RFC1918
  私网 IPv4，显式拒绝 0.0.0.0 / 回环 / 链路本地 / 公网 / 接口外地址——
  **listen 之前**校验失败即退出，绝不退回 0.0.0.0；
- 请求面：18010 仅放行精确 GET /health → http://127.0.0.1:8010/health；
  18011 仅放行精确 GET /health 与 GET /health/live → 127.0.0.1:8011 同
  路径；其余方法（405）/路径（404）/查询串（400）一律拒绝。上游 URL
  恒为模块常量表 PORT_ROUTES，绝不取自请求——结构上不存在通用
  TCP/HTTP 转发能力；
- 上游：socket 超时有界（UPSTREAM_TIMEOUT_SECONDS）+ 响应体读取有界
  （MAX_UPSTREAM_BODY_BYTES）；上游非 2xx 原状态码透传（503 如实可见），
  超时→504、连接失败→502、未知异常→502，错误体为固定文案——不泄密、
  不搬运异常细节、不读生产日志；上游探测经零代理 opener（继承的
  HTTP_PROXY 等不得劫持 127.0.0.1 探测，同 voice_service_control 口径）。

纯标准库；仅在 WSL Linux 内运行（Windows 侧由
voice_health_sidecar_control.py 经 wsl.exe 编排启动）。状态文件
（PID/绑定/端口，schema 版本化 + 原子写）供控制器回读核验。

用法（WSL 内，仓库根 cwd；由控制器调用，一般不手工执行）：
  python3 tools/voice/voice_health_sidecar.py \
      --status-file .verify/artifacts/m14-26-voice-health-sidecar/sidecar-status.json

开发约束（M14-26 委托）：开发期不得启动本 sidecar、不得真实访问
/proc/net/route 或网络——全部行为经契约测试以 fake 数据锁定。
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import struct
import sys
import threading
from collections import namedtuple
from collections.abc import Sequence
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

SIDECAR_TAG = "[voice-health-sidecar]"

#: 双端口：18010 = FunASR 健康窄代理；18011 = CosyVoice 健康窄代理
PORT_FUNASR = 18010
PORT_COSYVOICE = 18011
UPSTREAM_FUNASR_HEALTH = "http://127.0.0.1:8010/health"
UPSTREAM_COSYVOICE_HEALTH = "http://127.0.0.1:8011/health"
UPSTREAM_COSYVOICE_LIVE = "http://127.0.0.1:8011/health/live"

#: 端口 → {精确路径: 固定上游 URL}——唯一的上游事实源（常量，非请求派生）
PORT_ROUTES: dict[int, dict[str, str]] = {
    PORT_FUNASR: {"/health": UPSTREAM_FUNASR_HEALTH},
    PORT_COSYVOICE: {
        "/health": UPSTREAM_COSYVOICE_HEALTH,
        "/health/live": UPSTREAM_COSYVOICE_LIVE,
    },
}

#: 上游 socket 超时（对齐监控 GET 5s 口径：必须小于它才有区分度）
UPSTREAM_TIMEOUT_SECONDS = 5.0
#: 上游响应体读取上限（字节）——健康端点为小 JSON，超限截断
MAX_UPSTREAM_BODY_BYTES = 65536

#: 唯一合法绑定域：RFC1918 三个私网块（WSL2 NAT 常见 172.16/12；镜像模式
#: 常见 192.168/16）。回环/链路本地/公网/0.0.0.0 一律不属于此域。
PRIVATE_V4_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)

PROC_NET_ROUTE = "/proc/net/route"
STATUS_SCHEMA_VERSION = 1
STATUS_SERVICE_NAME = "voice-health-sidecar"
DEFAULT_STATUS_RELPATH = (
    ".verify/artifacts/m14-26-voice-health-sidecar/sidecar-status.json"
)

#: 固定错误文案（不携带任何内部细节/异常串——只读代理的安全输出面）
BODY_UPSTREAM_TIMEOUT = b'{"detail": "upstream timeout"}'
BODY_UPSTREAM_UNAVAILABLE = b'{"detail": "upstream connection failed"}'
BODY_UPSTREAM_ERROR = b'{"detail": "upstream error"}'
BODY_NOT_FOUND = b'{"detail": "not found"}'
BODY_METHOD_NOT_ALLOWED = b'{"detail": "method not allowed"}'
BODY_BAD_REQUEST = b'{"detail": "bad request"}'


class BindAddressError(ValueError):
    """绑定地址不安全/不可解析——reason 为机器可读类别（listen 前抛出）。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class StatusFileError(RuntimeError):
    """状态文件路径不安全（越界/符号链接）或写入失败。"""


# ---------- 绑定地址发现：/proc/net/route + UDP 本地路由探测（零发包） ----------

RouteRow = namedtuple("RouteRow", ["iface", "dest", "mask", "metric"])


def _hex_to_ipv4(word: str) -> ipaddress.IPv4Address | None:
    """/proc/net/route 的十六进制小端字段 → IPv4Address；非法返回 None。"""
    try:
        raw = int(word, 16)
    except ValueError:
        return None
    if raw < 0 or raw > 0xFFFFFFFF:
        return None
    return ipaddress.ip_address(socket.inet_ntoa(struct.pack("<I", raw)))


def parse_route_table(text: str) -> list[RouteRow]:
    """解析 /proc/net/route 文本；表头/坏行跳过（返回可解析行）。"""
    rows: list[RouteRow] = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 8:
            continue
        dest = _hex_to_ipv4(fields[1])
        mask = _hex_to_ipv4(fields[7])
        if dest is None or mask is None:
            continue  # 表头（Iface Destination ...）与坏行
        try:
            metric = int(fields[6])
        except ValueError:
            continue
        rows.append(RouteRow(fields[0], dest, mask, metric))
    return rows


def default_route_interface(rows: Sequence[RouteRow]) -> str | None:
    """默认路由（dest=0.0.0.0/mask=0.0.0.0）中 metric 最小的接口名。"""
    defaults = [
        row for row in rows
        if row.dest == ipaddress.ip_address("0.0.0.0")
        and row.mask == ipaddress.ip_address("0.0.0.0")
    ]
    if not defaults:
        return None
    return min(defaults, key=lambda row: row.metric).iface


def interface_networks(rows: Sequence[RouteRow], iface: str) -> list[ipaddress.IPv4Network]:
    """指定接口的非默认路由网段（eth0 的本地子网，用于地址归属核验）。"""
    networks: list[ipaddress.IPv4Network] = []
    for row in rows:
        if row.iface != iface:
            continue
        if row.dest == ipaddress.ip_address("0.0.0.0"):
            continue
        if row.mask == ipaddress.ip_address("0.0.0.0"):
            continue
        prefix = int(row.mask).bit_count()
        try:
            networks.append(
                ipaddress.ip_network(f"{row.dest}/{prefix}", strict=False)
            )
        except ValueError:
            continue
    return networks


def probe_source_address() -> str | None:
    """UDP connect 探测默认路由源地址——零发包（内核路由表查询）。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            # TEST-NET-1：仅作路由查询目标，UDP connect 不发送任何报文
            sock.connect(("192.0.2.1", 9))
            return sock.getsockname()[0]
    except OSError:
        return None


def validate_bind_address(
    probed_source: str | None,
    rows: Sequence[RouteRow],
) -> str:
    """校验候选绑定地址；非法即抛 BindAddressError（listen 之前调用）。

    校验链：探测可用 → IPv4 → 非 0.0.0.0 → 非回环 → 非链路本地 →
    RFC1918 私网 → 落在默认路由接口的网段内。任何一环失败都不 listen。
    """
    if not probed_source:
        raise BindAddressError("source-probe-unavailable")
    try:
        address = ipaddress.ip_address(probed_source)
    except ValueError:
        raise BindAddressError("invalid-address") from None
    if address.version != 4:
        raise BindAddressError("invalid-address")
    if address.is_unspecified:
        raise BindAddressError("unspecified-address")
    if address.is_loopback:
        raise BindAddressError("loopback-address")
    if address.is_link_local:
        raise BindAddressError("link-local-address")
    if not any(address in network for network in PRIVATE_V4_NETWORKS):
        raise BindAddressError("not-rfc1918-private")
    iface = default_route_interface(rows)
    if iface is None:
        raise BindAddressError("no-default-route")
    networks = interface_networks(rows, iface)
    if not any(address in network for network in networks):
        raise BindAddressError("outside-default-interface")
    return str(address)


def resolve_bind_address(route_text: str, probed_source: str | None) -> str:
    """组合入口：/proc/net/route 文本 + 探测源地址 → 安全绑定地址。"""
    rows = parse_route_table(route_text)
    if not rows:
        raise BindAddressError("route-table-unavailable")
    return validate_bind_address(probed_source, rows)


# ---------- 上游传输抽象（真实实现零代理 + 有界；测试注入 fake） ----------


class UpstreamTimeout(Exception):
    """上游 socket 超时（→ 504）。"""


class UpstreamConnectionError(Exception):
    """上游连接失败（→ 502）。"""


#: 零代理 opener：继承的 HTTP_PROXY/http_proxy/系统代理不得劫持
#: 127.0.0.1 上游探测（同 voice_service_control 修正轮 2 实证口径）
_UPSTREAM_OPENER = urllib_request.build_opener(urllib_request.ProxyHandler({}))


class UpstreamTransport:
    """固定 URL 的上游 GET：返回 (状态码, Content-Type, 有界响应体)。"""

    def fetch(
        self, url: str, timeout: float = UPSTREAM_TIMEOUT_SECONDS
    ) -> tuple[int, str, bytes]:
        try:
            with _UPSTREAM_OPENER.open(url, timeout=timeout) as response:
                body = response.read(MAX_UPSTREAM_BODY_BYTES + 1)
                ctype = response.headers.get("Content-Type", "application/json")
                return int(response.status), ctype, body[:MAX_UPSTREAM_BODY_BYTES]
        except urllib_error.HTTPError as cause:
            # 上游非 2xx（含 503 loading）：状态码如实透传，体有界
            body = b""
            if cause.fp:
                body = cause.read(MAX_UPSTREAM_BODY_BYTES + 1)[:MAX_UPSTREAM_BODY_BYTES]
            ctype = cause.headers.get("Content-Type", "application/json")
            return int(cause.code), ctype, body
        except urllib_error.URLError as cause:
            reason = cause.reason
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise UpstreamTimeout(str(reason)) from cause
            raise UpstreamConnectionError(type(reason).__name__) from cause
        except TimeoutError as cause:
            raise UpstreamTimeout(type(cause).__name__) from cause
        except OSError as cause:
            raise UpstreamConnectionError(type(cause).__name__) from cause


# ---------- HTTP 面：精确 allowlist 处理器（无通用转发能力） ----------


class SidecarRequestHandler(BaseHTTPRequestHandler):
    """精确 GET allowlist：路径→固定上游 URL（PORT_ROUTES），其余拒绝。"""

    server_version = "VoiceHealthSidecar/1"
    protocol_version = "HTTP/1.1"

    # BaseHTTPRequestHandler 按方法名分发 do_<METHOD>；未定义的生僻方法
    # 由基类回 501——同样不代理，allowlist 之外无任何放行路径
    def do_GET(self) -> None:
        parsed = urllib_parse.urlsplit(self.path)
        if parsed.query or parsed.fragment:
            # 查询串一律拒绝（哪怕 allowlist 路径）：防参数走私
            self._respond_json(400, BODY_BAD_REQUEST)
            return
        upstream = self.server.routes.get(parsed.path)
        if upstream is None:
            self._respond_json(404, BODY_NOT_FOUND)
            return
        self._proxy(upstream)

    def do_POST(self) -> None:
        self._respond_json(405, BODY_METHOD_NOT_ALLOWED)

    def do_PUT(self) -> None:
        self._respond_json(405, BODY_METHOD_NOT_ALLOWED)

    def do_DELETE(self) -> None:
        self._respond_json(405, BODY_METHOD_NOT_ALLOWED)

    def do_PATCH(self) -> None:
        self._respond_json(405, BODY_METHOD_NOT_ALLOWED)

    def do_OPTIONS(self) -> None:
        self._respond_json(405, BODY_METHOD_NOT_ALLOWED)

    def do_HEAD(self) -> None:
        self._respond_json(405, BODY_METHOD_NOT_ALLOWED)

    def _proxy(self, upstream_url: str) -> None:
        try:
            status, content_type, body = self.server.transport.fetch(
                upstream_url
            )
        except UpstreamTimeout:
            self._respond_json(504, BODY_UPSTREAM_TIMEOUT)
            return
        except UpstreamConnectionError:
            self._respond_json(502, BODY_UPSTREAM_UNAVAILABLE)
            return
        except Exception:  # noqa: BLE001 — 未知上游异常一律安全降级，不外泄细节
            self._respond_json(502, BODY_UPSTREAM_ERROR)
            return
        self._respond(status, body, content_type)

    def _respond_json(self, status: int, body: bytes) -> None:
        self._respond(status, body, "application/json")

    def _respond(self, status: int, body: bytes, content_type: str) -> None:
        if status >= 400:
            self.close_connection = True  # 拒绝后不复用连接（防未读请求体串流）
        self.send_response(status)
        if status == 405:
            self.send_header("Allow", "GET")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        # 短行审计日志（stderr → 控制器日志文件）；行长有界，不落查询串细节
        line = (format % args)[:300]
        sys.stderr.write(f"{SIDECAR_TAG} {self.address_string()} {line}\n")


class SidecarHTTPServer(ThreadingHTTPServer):
    """携带 (routes, transport) 的窄代理服务器（daemon 线程模型）。"""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        routes: dict[str, str],
        transport: UpstreamTransport,
    ) -> None:
        self.routes = routes
        self.transport = transport
        super().__init__(address, SidecarRequestHandler)


# ---------- 状态文件（PID/绑定/端口；schema 版本化 + 原子写 + 路径安全） ----------


def resolve_status_path(raw: Path) -> Path:
    """状态文件路径安全化：相对路径以 cwd（仓库根，控制器 --cd 设定）解析；
    越出仓库根（路径穿越）即拒绝——符号链接目标同样经 resolve 后核验。"""
    repo_root = Path(__file__).resolve().parents[2]
    path = raw if raw.is_absolute() else Path.cwd() / raw
    resolved = path.resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError:
        raise StatusFileError("status-file-outside-repo") from None
    return resolved


def write_status_file(path: Path, bind: str, ports: Sequence[int]) -> None:
    """原子写状态文件（tmp O_EXCL + os.replace）；符号链接目标拒绝。"""
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise StatusFileError("unsafe-status-target")
    payload = {
        "schema_version": STATUS_SCHEMA_VERSION,
        "service": STATUS_SERVICE_NAME,
        "pid": os.getpid(),
        "bind": bind,
        "ports": [int(port) for port in ports],
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    data = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp.{os.getpid()}"
    for _ in range(2):  # 残留 tmp（同 PID 极罕见）→ 清除后重试一次
        try:
            fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            tmp.unlink(missing_ok=True)
    else:  # pragma: no cover —— 两次 O_EXCL 冲突属异常环境
        raise StatusFileError("status-tmp-conflict")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(data)
    os.replace(tmp, path)


# ---------- 服务编排 ----------


def serve(
    bind: str,
    *,
    routes_by_port: dict[int, dict[str, str]] | None = None,
    transport: UpstreamTransport | None = None,
    status_file: Path | None = None,
) -> None:
    """绑定全部端口后写状态文件，再进入服务循环（阻塞直至进程终止）。

    双端口任一 bind 失败：已建 socket 全部回收并以 OSError 上抛（进程退出，
    不留半可用状态——监控看到的是明确的不可达而非半套真相）。
    """
    routes_by_port = PORT_ROUTES if routes_by_port is None else routes_by_port
    transport = UpstreamTransport() if transport is None else transport
    ports = sorted(routes_by_port)
    servers: list[SidecarHTTPServer] = []
    try:
        for port in ports:
            servers.append(
                SidecarHTTPServer((bind, port), routes_by_port[port], transport)
            )
    except OSError:
        for server in servers:
            server.server_close()
        raise
    if status_file is not None:
        write_status_file(status_file, bind, ports)
    workers = [
        threading.Thread(target=server.serve_forever, daemon=True)
        for server in servers[:-1]
    ]
    for worker in workers:
        worker.start()
    sys.stderr.write(
        f"{SIDECAR_TAG} serving bind={bind} ports={','.join(str(p) for p in ports)}"
        f" pid={os.getpid()}\n"
    )
    try:
        servers[-1].serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


def main(argv: Sequence[str] | None = None) -> int:
    if os.name != "posix":
        sys.stderr.write(f"{SIDECAR_TAG} 仅限 WSL/Linux 内运行（当前 {os.name}）\n")
        return 1
    parser = argparse.ArgumentParser(
        prog="voice_health_sidecar.py",
        description="WSL 语音健康只读 sidecar（18010/18011 窄代理；由控制器编排）",
    )
    parser.add_argument(
        "--status-file",
        type=Path,
        default=Path(DEFAULT_STATUS_RELPATH),
        help="状态文件路径（repo-relative；默认 .verify/artifacts/m14-26-voice-health-sidecar/）",
    )
    args = parser.parse_args(argv)
    try:
        route_text = Path(PROC_NET_ROUTE).read_text(encoding="ascii", errors="replace")
    except OSError:
        sys.stderr.write(f"{SIDECAR_TAG} 无法读取 {PROC_NET_ROUTE}（非 WSL/Linux 环境？）\n")
        return 1
    try:
        bind = resolve_bind_address(route_text, probe_source_address())
        status_path = resolve_status_path(args.status_file)
    except BindAddressError as cause:
        sys.stderr.write(f"{SIDECAR_TAG} 拒绝监听：不安全绑定地址（{cause.reason}）\n")
        return 2
    except StatusFileError as cause:
        sys.stderr.write(f"{SIDECAR_TAG} 状态文件路径不安全：{cause}\n")
        return 2
    try:
        serve(bind, status_file=status_path)
    except OSError as cause:
        sys.stderr.write(f"{SIDECAR_TAG} 端口绑定失败（{type(cause).__name__}）——已退出\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
