#!/usr/bin/env python
"""M14-12 生产监控 readiness：只读采集 → 阈值判定 → 证据报告（不接外部告警）。

设计（与 tools/ops/soak_rehearsal.py、production_recovery.py 同款纪律：单文件、
纯标准库、零第三方依赖；子进程经 Runner 注入 + 只读白名单门、HTTP 经
Transport 注入、UTC 时间经 Clock 注入——开发回合零真实 Docker/零生产
HTTP/零计划任务，全部行为用 fake/stub 测试锁定；真实采集仅由 supervisor
在获准窗口运行）：

- 双模式：默认 **plan（dry-run）**——零 subprocess、零网络、零生产读取，
  仅打印计划并落 plan 报告；**execute** 需同时满足「旗标 + 精确确认短语」
  （``--execute`` + ``--confirm "EXECUTE READ-ONLY PRODUCTION MONITORING"``
  一字不差），缺一即 EXIT 拒绝且**零采集**（fail-closed）。数值面硬性
  拒绝（plan 报告写入/采集之前）：非有限浮点（nan/inf/-inf）一律拒绝；
  compose 项目名严格白名单（ASCII 字母数字开头、仅字母数字/连字符/
  下划线、长度 ≤64——空/空白/控制/路径/换行/非 ASCII 一律拒绝，被拒值
  绝不回显）。
- 只读采集面（固定画像，不可经 CLI 注入任意目标）：compose project
  ``aios-m14-03-production-rehearsal``（--profile local）——
  ① ``docker compose ps --format json``（六受管服务 health/state）；
  ② 六受管容器（postgres/redis/minio/api/web/livekit）逐容器
  ``docker inspect``（state/health/RestartCount/image/started）；
  ③ 五默认端点 GET（Web ``/``、``/login``；API/FunASR/CosyVoice
  ``/health``）状态 + 延迟；④ 容器日志安全错误摘要（``docker logs
  --tail``，只记匹配计数/级别/安全类别，**绝不持久化原文**）。
- 子进程白名单门（结构性）：一切 docker 命令必经 ``ReadonlyRunner`` 的
  ``is_readonly_docker_command`` 校验——仅接受 compose ps / inspect /
  logs --tail 三形态，stop/rm/kill/restart/down/exec/up 等一律拒绝（拒
  绝发生在任何执行之前）；Windows 侧恒 CREATE_NO_WINDOW。
- 网络纪律（与 soak 同款）：GET-only、无认证、无 cookie、无 header/token、
  不读响应体、不跟随重定向；web/api 端点**恒字面 loopback IP**（127.0.0.0/8、
  ::1），主机名一律拒绝（零 DNS）；M14-27 sidecar 语音端点**仅由 canonical
  sidecar manifest 严格校验通过后派生**——字面 RFC1918 IPv4 + 固定端口
  18010/18011 + 精确 ``/health``，无 DNS/query/fragment/userinfo/任意 URL/
  清单路径注入/loopback 回退（清单不可用即 fail-closed 零采集）；
  ``http.client`` 直连从不读取 proxy 环境变量（结构性旁路），proxy env
  仅探测**键名存在性**入注记，值绝不读取/记录。
- 采集器部分失败如实入档：任一采集器失败 → ``partial=true`` + 失败安全
  类别（仅类别 + 异常类名，绝不保留文本）→ ``overall_status=incomplete``；
  **缺失绝不当作 healthy**（fail-closed：failed 项在阈值判定中恒为可见
  critical alert）。
- 阈值/状态：compose 6/6 healthy、五端点恒 200、容器 health/state、
  RestartCount、日志错误计数、端点延迟 warn/critical（全部含边界，warn
  恒可见不被隐藏）；输出 ``overall_status=ok|warn|critical|incomplete``、
  逐项 alert 与 ``monitoring_ready``（仅采集完整且无 critical/warn 时
  true）。``monitoring_ready`` ≠ production ready——本工具绝不宣称生产
  就绪。
- restart 增量语义（M14-23）：容器 ``RestartCount`` 是 Docker 的**累计**
  事实（照实入档不动）；阈值判定改用**当轮新增增量** = 当前累计 − 基线
  累计，基线 = 本轮工件目录内**最新合法的 prior 完整** monitor JSON 工件
  （纯本地只读 fail-safe 解析：零 shell/零网络/零写盘；非法候选显式计数
  ``invalid_skipped_count``，绝不静默当作零基线）。同容器实例
  （started_at 一致）且增量 0 → ok——健康栈不再因历史存量累计值永久
  warn；``started_at`` 变化 = 容器重建、累计下降 = 计数重置，各发**一轮
  可见 warn**（负增量绝不静默映射为零），下一稳定轮即恢复；无基线时
  count 0 → ok、达 warn/critical → 一次性 baseline-missing 可见告警（下一
  轮以本轮工件为基线即恢复）。报告加法字段
  ``threshold_results.restart_evaluation``（baseline 元数据 + 逐服务
  state/reason/delta）；check_id 仍为 ``container-restarts``，schema 向后
  兼容（v1 旧工件照常作基线与入档）。
- 日志错误时间界（M14-79）：``docker logs`` 恒带 ``--timestamps``（只读
  输出旗标，白名单放行），log-errors 阈值判定改为**当前区间计数**——仅
  统计时间戳 ≥ 基线（最新合法 prior 完整工件 started_at_utc，与 M14-23
  restart 基线同源）的错误行；早于基线的陈旧错误计入
  ``stale_error_lines`` 显式入档（绝不静默丢弃）；错误行时间戳不可解析
  → **fail-closed 恒计入当前区间**（``unparsed_error_lines`` 显式计数，
  recency 无法建立绝不排除）；基线缺失/不可用 → 记账基准回退
  ``full-tail``（= 修复前保守全尾口径）。``error_total_tail``（全尾
  行数）与 ``latest_error_at``（最新错误时刻）照实入档——陈旧错误的
  存在始终可见，只是不再永久阻塞恢复；阈值本身零变更。schema 向后
  兼容：旧工件（无新键）照常作基线与入档；history/insights 消费面
  ``error_total`` 键名与类型不变。
- 报告：schema 版本化 JSON + Markdown **原子写入**（同目录 tmp +
  os.replace）；默认目录 ``REPO_ROOT/.verify/artifacts/m14-12-production-monitoring``
  **gitignored**，``--artifact-dir`` 自定义路径为**操作者显式自选覆盖**
  （其位置与 gitignore 状态由操作者负责）；拒绝 symlink 组件与越界 stem；
  内容含 UTC 时间、配置、限制/边界、collector 状态、阈值结果。本里程碑
  **不做外部告警发送**。
- 退出码：0 plan 成功 / execute 完整采集且 ok|warn（warn 恒可见不隐藏）；
  2 非法或 fail-closed（确认缺失、限制超顶、非有限浮点、非法项目名、
  symlink/越界路径）或采集 incomplete 或存在 critical（含证据报告写入
  失败——证据不可失）。

用法（仓库根）：
  python tools/ops/production_monitor.py                        # plan（默认，零采集）
  python tools/ops/production_monitor.py --execute \
      --confirm "EXECUTE READ-ONLY PRODUCTION MONITORING"       # execute（只读采集）
"""
from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

EXIT_OK = 0
#: 2 = 非法/fail-closed/采集 incomplete/存在 critical（统一可见拒绝口径）
EXIT_USAGE = 2
EXIT_INCOMPLETE = 2
EXIT_CRITICAL = 2

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "infra" / "docker-compose.yml"
ARTIFACT_DIR = REPO_ROOT / ".verify" / "artifacts" / "m14-12-production-monitoring"
TAG = "[monitor]"
REPORT_SCHEMA_VERSION = 1
MILESTONE = "M14-12"
TOOL_NAME = "tools/ops/production_monitor.py"
USER_AGENT = "aios-m14-12-production-monitor/1.0"

#: execute 门禁之二：精确确认短语（一字不差）
CONFIRM_PHRASE = "EXECUTE READ-ONLY PRODUCTION MONITORING"

DEFAULT_PROJECT = "aios-m14-03-production-rehearsal"
DEFAULT_PROFILE = "local"

#: 六受管容器（与 production_recovery.EXPECTED_STACK_SERVICES 同源）
STACK_SERVICES: tuple[str, ...] = ("postgres", "redis", "minio", "api", "web", "livekit")

# ---------------------------------------------------------------- 限制（fail-closed 硬顶）

MIN_REQUEST_TIMEOUT_SECONDS = 0.5
MAX_REQUEST_TIMEOUT_SECONDS = 10.0
MIN_LATENCY_MS = 50.0
MAX_LATENCY_MS = 600000.0
MIN_RESTART_THRESHOLD = 0
MAX_RESTART_THRESHOLD = 1000
MIN_LOG_ERROR_THRESHOLD = 0
MAX_LOG_ERROR_THRESHOLD = 100000
MIN_LOG_TAIL = 10
MAX_LOG_TAIL = 2000

#: 默认值（全部落在硬顶内）
DEFAULT_REQUEST_TIMEOUT_SECONDS = 5.0
DEFAULT_LATENCY_WARN_MS = 1000.0
DEFAULT_LATENCY_CRITICAL_MS = 5000.0
DEFAULT_RESTART_WARN = 1
DEFAULT_RESTART_CRITICAL = 5
DEFAULT_LOG_ERROR_WARN = 5
DEFAULT_LOG_ERROR_CRITICAL = 20
DEFAULT_LOG_TAIL = 200

#: 子进程命令超时（秒）——compose ps 容器面查询/inspect/logs 各自上限
COMPOSE_PS_TIMEOUT_SECONDS = 60.0
INSPECT_TIMEOUT_SECONDS = 30.0
LOGS_TIMEOUT_SECONDS = 30.0

#: 仅探测键名是否存在的 proxy 环境变量（值绝不读取/记录）
PROXY_ENV_KEYS: tuple[str, ...] = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
)


# ---------------------------------------------------------------- 端点面（固定画像）


@dataclass(frozen=True)
class Endpoint:
    """固定端点画像成员（不可经 CLI 注入任意 URL）。"""

    endpoint_id: str
    url: str
    group: str


ENDPOINTS: tuple[Endpoint, ...] = (
    Endpoint("web-root", "http://127.0.0.1:3011/", "web"),
    Endpoint("web-login", "http://127.0.0.1:3011/login", "web"),
    Endpoint("api-health", "http://127.0.0.1:8000/health", "api"),
    Endpoint("funasr-health", "http://127.0.0.1:8010/health", "voice"),
    Endpoint("cosyvoice-health", "http://127.0.0.1:8011/health", "voice"),
)


def validate_target_url(url: str) -> str | None:
    """fail-closed 校验：http-only、字面 loopback IP、显式端口、无 query/
    fragment/userinfo（与 soak_rehearsal 同款矩阵）。返回拒绝原因或 None。"""
    parts = urlsplit(url)
    if parts.scheme != "http":
        return "scheme-not-http"
    host = parts.hostname
    if host is None:
        return "no-host"
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return "host-not-literal-ip"
    if not addr.is_loopback:
        return "host-not-loopback"
    try:
        port = parts.port
    except ValueError:
        return "bad-port"
    if port is None:
        return "no-explicit-port"
    if parts.username is not None or parts.password is not None:
        return "userinfo-present"
    if parts.query or parts.fragment:
        return "query-or-fragment"
    if not parts.path or not parts.path.startswith("/"):
        return "bad-path"
    return None


def select_endpoints(only: list[str] | None,
                     *, profile: list[Endpoint] | tuple[Endpoint, ...] | None = None,
                     ) -> tuple[list[Endpoint], str | None]:
    """``--only`` 仅能在画像内筛选（未知 ID 即拒绝）；``profile`` 缺省为
    固定五端点 loopback 画像（既有调用不变），sidecar 来源传入 sidecar 画像。"""
    base: list[Endpoint] | tuple[Endpoint, ...] = ENDPOINTS if profile is None else profile
    if not only:
        return list(base), None
    known = {e.endpoint_id for e in base}
    unknown = sorted(set(only) - known)
    if unknown:
        return [], f"unknown-endpoint: {', '.join(unknown)}（可选: {', '.join(sorted(known))}）"
    return [e for e in base if e.endpoint_id in set(only)], None


# ------------------------------------------------- sidecar 语音健康来源（M14-27）

#: M14-26 受控生命周期 sidecar 的规范清单路径（仓库根相对；由
#: tools/voice/voice_health_sidecar_control.py start 原子落盘）
SIDECAR_MANIFEST_RELPATH = Path(".verify/artifacts/m14-26-voice-health-sidecar") / "sidecar-manifest.json"
#: 本工具消费的规范绝对路径（测试经 monkeypatch 替换；生产恒为此常量）
SIDECAR_MANIFEST_PATH = REPO_ROOT / SIDECAR_MANIFEST_RELPATH
#: 清单体积硬顶（64 KiB——防失控文件读入内存；超顶即拒）
SIDECAR_MANIFEST_MAX_BYTES = 65536
#: 清单 schema 事实（与 voice_health_sidecar_control.Manifest 同源）
SIDECAR_MANIFEST_SCHEMA_VERSION = 1
SIDECAR_MANIFEST_SERVICE = "voice-health-sidecar"
#: sidecar 双端口（18010 = FunASR 窄代理；18011 = CosyVoice 窄代理——与
#: voice_health_sidecar.PORT_ROUTES 同源事实，恒为模块常量，绝不取自请求）
SIDECAR_PORT_FUNASR = 18010
SIDECAR_PORT_COSYVOICE = 18011
SIDECAR_PORTS: tuple[int, ...] = (SIDECAR_PORT_FUNASR, SIDECAR_PORT_COSYVOICE)
#: 唯一合法绑定域：RFC1918 三个私网块（与 voice_health_sidecar.
#: PRIVATE_V4_NETWORKS 同源）。回环/链路本地/公网/0.0.0.0 天然不在域内。
SIDECAR_PRIVATE_V4_NETWORKS: tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


def _sidecar_bind_problem(bind: str) -> str | None:
    """sidecar bind 严格校验：字面 IPv4 + RFC1918（显式排除 unspecified/
    loopback/link-local）。返回拒绝类别或 None。"""
    try:
        addr = ipaddress.ip_address(bind)
    except ValueError:
        return "bind-not-literal-ip"
    if addr.version != 4:
        return "bind-not-ipv4"
    if addr.is_unspecified or addr.is_loopback or addr.is_link_local:
        return "bind-not-rfc1918"
    if not any(addr in net for net in SIDECAR_PRIVATE_V4_NETWORKS):
        return "bind-not-rfc1918"
    return None


def load_sidecar_manifest(path: Path) -> tuple[str | None, str | None]:
    """读取并严格校验 canonical sidecar manifest。体积硬顶双检：读前按
    ``stat().st_size`` 预检（超顶零读取——绝不把失控文件读入内存），读后
    按 ``len(data)`` 复核（防 stat/读取间漂移）。成功 → (bind, None)；
    任何失败 → (None, 固定安全错误类别)——绝不回显文件内容/异常串。"""
    try:
        if path.is_symlink():
            return None, "sidecar-manifest-symlink"
        if not path.exists():
            return None, "sidecar-manifest-missing"
        if not path.is_file():
            return None, "sidecar-manifest-not-regular-file"
        if path.stat().st_size > SIDECAR_MANIFEST_MAX_BYTES:
            return None, "sidecar-manifest-oversize"
        data = path.read_bytes()
    except OSError:
        return None, "sidecar-manifest-unreadable"
    if len(data) > SIDECAR_MANIFEST_MAX_BYTES:
        return None, "sidecar-manifest-oversize"
    try:
        payload = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, "sidecar-manifest-invalid-json"
    if not isinstance(payload, dict):
        return None, "sidecar-manifest-invalid-json"
    schema = payload.get("schema_version")
    if isinstance(schema, bool) or not isinstance(schema, int) or schema != SIDECAR_MANIFEST_SCHEMA_VERSION:
        return None, "sidecar-manifest-schema-version"
    if payload.get("service") != SIDECAR_MANIFEST_SERVICE:
        return None, "sidecar-manifest-service"
    pid = payload.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None, "sidecar-manifest-pid"
    ports = payload.get("ports")
    if (not isinstance(ports, list) or len(ports) != len(SIDECAR_PORTS)
            or any(isinstance(p, bool) or not isinstance(p, int) for p in ports)
            or tuple(ports) != SIDECAR_PORTS):
        return None, "sidecar-manifest-ports"
    bind = payload.get("bind")
    if not isinstance(bind, str) or _sidecar_bind_problem(bind) is not None:
        return None, "sidecar-manifest-bind"
    return bind, None


def build_sidecar_endpoints(bind: str) -> list[Endpoint]:
    """sidecar 来源端点画像：web/api 端点原样保留，语音双端点替换为
    manifest bind + 固定窄代理端口 + 精确 /health（固定五端点顺序）。"""
    problem = _sidecar_bind_problem(bind) if isinstance(bind, str) else "bind-not-literal-ip"
    if problem is not None:
        raise ValueError(problem)
    return [
        *[e for e in ENDPOINTS if e.group != "voice"],
        Endpoint("funasr-health", f"http://{bind}:{SIDECAR_PORT_FUNASR}/health", "voice"),
        Endpoint("cosyvoice-health", f"http://{bind}:{SIDECAR_PORT_COSYVOICE}/health", "voice"),
    ]


def validate_sidecar_voice_url(url: str) -> str | None:
    """sidecar 语音 URL fail-closed 校验：http-only、字面 IPv4 RFC1918、
    显式端口且恒为 18010/18011、精确路径 /health、无 query/fragment/
    userinfo。返回拒绝原因或 None。"""
    parts = urlsplit(url)
    if parts.scheme != "http":
        return "scheme-not-http"
    host = parts.hostname
    if host is None:
        return "no-host"
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return "host-not-literal-ip"
    if addr.version != 4 or not any(addr in net for net in SIDECAR_PRIVATE_V4_NETWORKS):
        return "host-not-rfc1918"
    try:
        port = parts.port
    except ValueError:
        return "bad-port"
    if port is None:
        return "no-explicit-port"
    if port not in SIDECAR_PORTS:
        return "port-not-allowed"
    if parts.username is not None or parts.password is not None:
        return "userinfo-present"
    if parts.query or parts.fragment:
        return "query-or-fragment"
    if parts.path != "/health":
        return "path-not-allowed"
    return None


# ---------------------------------------------------------------- compose 项目名（严格白名单）

#: 项目名白名单：ASCII 字母数字开头，仅字母数字/连字符/下划线，长度 ≤64
#: （空白/控制/路径/换行/markdown 字符天然被白名单排除）
MAX_PROJECT_NAME_LENGTH = 64
PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def validate_project_name(name: str) -> str | None:
    """fail-closed 校验 ``--project``（任意 CLI 输入面）。返回拒绝原因
    （固定词汇，**绝不回显被拒值**）或 None（放行）。"""
    if not name:
        return "empty"
    if len(name) > MAX_PROJECT_NAME_LENGTH:
        return "too-long"
    if not (name[0].isascii() and name[0].isalnum()):
        return "first-char-not-alnum"
    if PROJECT_NAME_RE.match(name) is None:
        return "invalid-character"
    return None


# ---------------------------------------------------------------- 阈值


@dataclass(frozen=True)
class Thresholds:
    latency_warn_ms: float
    latency_critical_ms: float
    restart_warn: int
    restart_critical: int
    log_error_warn: int
    log_error_critical: int

    def as_dict(self) -> dict[str, object]:
        return {
            "latency_warn_ms": self.latency_warn_ms,
            "latency_critical_ms": self.latency_critical_ms,
            "restart_warn": self.restart_warn,
            "restart_critical": self.restart_critical,
            "log_error_warn": self.log_error_warn,
            "log_error_critical": self.log_error_critical,
        }


def validate_thresholds(*, request_timeout: float, latency_warn: float,
                        latency_critical: float, restart_warn: int, restart_critical: int,
                        log_error_warn: int, log_error_critical: int,
                        log_tail: int) -> list[str]:
    """纯函数：返回违规清单（空 = 放行）。所有硬顶 fail-closed（plan 同样校验）。
    非有限浮点（nan/inf/-inf）显式拒绝——不依赖比较语义的隐式行为。"""
    problems: list[str] = []
    for name, value in (("request-timeout-seconds", request_timeout),
                        ("latency-warn-ms", latency_warn),
                        ("latency-critical-ms", latency_critical)):
        if not math.isfinite(value):
            problems.append(f"{name} 必须为有限数值（nan/inf 一律拒绝）")
    if not MIN_REQUEST_TIMEOUT_SECONDS <= request_timeout <= MAX_REQUEST_TIMEOUT_SECONDS:
        problems.append(f"request-timeout-seconds 必须在 {MIN_REQUEST_TIMEOUT_SECONDS}-{MAX_REQUEST_TIMEOUT_SECONDS}s（收到 {request_timeout}）")
    if not MIN_LATENCY_MS <= latency_warn <= MAX_LATENCY_MS:
        problems.append(f"latency-warn-ms 必须在 {MIN_LATENCY_MS}-{MAX_LATENCY_MS}（收到 {latency_warn}）")
    if not MIN_LATENCY_MS <= latency_critical <= MAX_LATENCY_MS:
        problems.append(f"latency-critical-ms 必须在 {MIN_LATENCY_MS}-{MAX_LATENCY_MS}（收到 {latency_critical}）")
    if latency_warn >= latency_critical:
        problems.append(f"latency-warn-ms 必须严格小于 latency-critical-ms（收到 {latency_warn} / {latency_critical}）")
    if not MIN_RESTART_THRESHOLD <= restart_warn <= MAX_RESTART_THRESHOLD:
        problems.append(f"restart-warn 必须在 {MIN_RESTART_THRESHOLD}-{MAX_RESTART_THRESHOLD}（收到 {restart_warn}）")
    if not MIN_RESTART_THRESHOLD <= restart_critical <= MAX_RESTART_THRESHOLD:
        problems.append(f"restart-critical 必须在 {MIN_RESTART_THRESHOLD}-{MAX_RESTART_THRESHOLD}（收到 {restart_critical}）")
    if restart_warn > restart_critical:
        problems.append(f"restart-warn 不得大于 restart-critical（收到 {restart_warn} / {restart_critical}）")
    if not MIN_LOG_ERROR_THRESHOLD <= log_error_warn <= MAX_LOG_ERROR_THRESHOLD:
        problems.append(f"log-error-warn 必须在 {MIN_LOG_ERROR_THRESHOLD}-{MAX_LOG_ERROR_THRESHOLD}（收到 {log_error_warn}）")
    if not MIN_LOG_ERROR_THRESHOLD <= log_error_critical <= MAX_LOG_ERROR_THRESHOLD:
        problems.append(f"log-error-critical 必须在 {MIN_LOG_ERROR_THRESHOLD}-{MAX_LOG_ERROR_THRESHOLD}（收到 {log_error_critical}）")
    if log_error_warn > log_error_critical:
        problems.append(f"log-error-warn 不得大于 log-error-critical（收到 {log_error_warn} / {log_error_critical}）")
    if not MIN_LOG_TAIL <= log_tail <= MAX_LOG_TAIL:
        problems.append(f"log-tail 必须在 {MIN_LOG_TAIL}-{MAX_LOG_TAIL}（收到 {log_tail}）")
    return problems


# ---------------------------------------------------------------- Runner（注入点 + 只读白名单门）


class RunnerError(RuntimeError):
    """平台命令执行失败（不可执行/超时）——调用方转为安全类别，绝不保留文本。"""


class CommandNotAllowedError(RunnerError):
    """非白名单只读命令——在任何执行之前拒绝（fail-closed）。"""


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class Runner(Protocol):
    def run(self, argv: tuple[str, ...] | list[str], *, timeout: float = 60.0,
            encoding: str | None = None) -> CommandResult: ...


def os_windows() -> bool:
    return os.name == "nt"


class RealRunner:
    """真实子进程执行：capture + UTF-8 + Windows 侧恒 CREATE_NO_WINDOW（无弹窗）。"""

    def run(self, argv: tuple[str, ...] | list[str], *, timeout: float = 60.0,
            encoding: str | None = None) -> CommandResult:
        text_argv = [str(item) for item in argv]
        kwargs: dict[str, object] = {
            "capture_output": True,
            "text": True,
            "encoding": encoding or "utf-8",
            "errors": "replace",
        }
        if os_windows():
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        try:
            result = subprocess.run(
                text_argv, check=False, timeout=timeout, **kwargs  # type: ignore[arg-type]
            )
        except (OSError, subprocess.TimeoutExpired) as cause:
            raise RunnerError(f"命令不可执行/超时: {text_argv[0]}: {type(cause).__name__}") from cause
        return CommandResult(tuple(text_argv), result.returncode, result.stdout or "", result.stderr or "")


#: compose 子命令白名单（仅 ps）；compose 选项（值选项需跳过后随值）
_COMPOSE_VALUE_OPTIONS = frozenset({"-f", "-p", "--profile", "--env-file"})
_COMPOSE_FLAGS = frozenset({"--dry-run"})
_INSPECT_FLAGS = frozenset({"--format"})
#: M14-79：logs 面旗标拆分——--tail <n>（带值，必选）+ --timestamps（布尔
#: 只读输出旗标，可选；为日志错误时间界提供逐行 RFC3339 时间戳）；
#: 跟随（-f）/--since 等其余形态仍一律拒绝
_LOGS_VALUE_FLAGS = frozenset({"--tail"})
_LOGS_BOOL_FLAGS = frozenset({"--timestamps"})
_LOGS_FLAGS = _LOGS_VALUE_FLAGS | _LOGS_BOOL_FLAGS
#: 三形态之外的一切子命令面（含全部 mutation/交互/长驻形态）一律拒绝
_DOCKER_SUBCOMMAND_WHITELIST = frozenset({"compose", "inspect", "logs"})


def is_readonly_docker_command(argv: tuple[str, ...] | list[str]) -> bool:
    """结构性白名单：仅 docker compose ps / docker inspect --format /
    docker logs --tail（+ 可选 --timestamps，M14-79）只读形态放行；其余
    （含 stop/rm/kill/down/restart/exec/up/build 等一切 mutation 与交互面）
    一律 False。"""
    tokens = [str(item) for item in argv]
    if len(tokens) < 2 or tokens[0] != "docker":
        return False
    sub = tokens[1]
    if sub not in _DOCKER_SUBCOMMAND_WHITELIST:
        return False
    if sub == "compose":
        # 选项区（值选项成对跳过）之后的首个位置参数必须是子命令 "ps"
        index, position = 2, None
        while index < len(tokens):
            token = tokens[index]
            if token in _COMPOSE_VALUE_OPTIONS:
                index += 2
                continue
            if token in _COMPOSE_FLAGS or (token.startswith("-") and token != "-"):
                index += 1
                continue
            position = token
            break
        if position != "ps":
            return False
        # ps 之后仅允许 --format json
        return all(token in ("--format", "json") for token in tokens[index + 1:])
    if sub == "inspect":
        # --format <fmt> + 容器名；无其它旗标
        index, saw_format, names = 2, False, 0
        while index < len(tokens):
            token = tokens[index]
            if token in _INSPECT_FLAGS:
                index += 2
                saw_format = True
                continue
            if token.startswith("-"):
                return False
            names += 1
            index += 1
        return saw_format and names >= 1
    # logs：--tail <n>（必选，带值）+ --timestamps（可选布尔，M14-79）+
    # 恰一个容器名；其余旗标（-f 跟随/长驻、--since 等）一律拒绝
    index, saw_tail, names = 2, False, 0
    while index < len(tokens):
        token = tokens[index]
        if token in _LOGS_VALUE_FLAGS:
            index += 2
            saw_tail = True
            continue
        if token in _LOGS_BOOL_FLAGS:
            index += 1
            continue
        if token.startswith("-"):
            return False
        names += 1
        index += 1
    return saw_tail and names == 1


class ReadonlyRunner:
    """白名单门装饰器：非只读命令在任何执行之前拒绝（fail-closed）。"""

    def __init__(self, inner: Runner) -> None:
        self._inner = inner

    def run(self, argv: tuple[str, ...] | list[str], *, timeout: float = 60.0,
            encoding: str | None = None) -> CommandResult:
        if not is_readonly_docker_command(argv):
            raise CommandNotAllowedError("command-not-whitelisted")
        return self._inner.run(argv, timeout=timeout, encoding=encoding)


def categorize_runner_exception(exc: BaseException) -> tuple[str, str]:
    """Runner 异常 →（安全类别, 异常类名）。绝不保留 str(exc) 文本。"""
    if isinstance(exc, CommandNotAllowedError):
        category = "command-not-whitelisted"
    elif isinstance(exc, subprocess.TimeoutExpired):
        category = "command-timeout"
    elif isinstance(exc, (RunnerError, OSError)):
        category = "command-exec-error"
    else:
        category = "internal-error"
    return category, type(exc).__name__


# ---------------------------------------------------------------- Transport（注入点）


@dataclass(frozen=True)
class HttpResult:
    """单请求结果：状态码或安全错误类别（仅类别 + 异常类名，无文本）。"""

    status: int | None
    elapsed_ms: float
    error_category: str | None
    error_class: str | None


class Transport(Protocol):
    def get(self, host: str, port: int, path: str, *, timeout: float) -> HttpResult: ...


def categorize_http_exception(exc: BaseException) -> tuple[str, str]:
    """异常 →（安全类别, 异常类名）。绝不保留 str(exc) 文本。"""
    if isinstance(exc, ConnectionRefusedError):
        category = "connection-refused"
    elif isinstance(exc, TimeoutError):  # socket.timeout 即 TimeoutError（3.10+）
        category = "timeout"
    elif isinstance(exc, ConnectionResetError):
        category = "connection-reset"
    elif isinstance(exc, BrokenPipeError):
        category = "connection-broken-pipe"
    elif isinstance(exc, http.client.HTTPException):
        category = "http-protocol-error"
    elif isinstance(exc, OSError):
        category = "connection-error"
    else:
        category = "internal-error"
    return category, type(exc).__name__


class RealTransport:
    """直连 http.client：该路径不读取 proxy 环境变量/系统代理（结构性
    loopback 旁路）；仅发 GET、仅 User-Agent/Accept 两个固定头、无认证、
    无 cookie、不跟随重定向、不读响应体（取状态码后即关连接）。"""

    def get(self, host: str, port: int, path: str, *, timeout: float) -> HttpResult:
        started = time.perf_counter()
        try:
            connection = http.client.HTTPConnection(host, port, timeout=timeout)
            try:
                connection.request("GET", path, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
                response = connection.getresponse()
                status = response.status
            finally:
                connection.close()
        except Exception as exc:  # noqa: BLE001 —— 类别化兜底，文本不保留
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            category, klass = categorize_http_exception(exc)
            return HttpResult(None, elapsed_ms, category, klass)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return HttpResult(status, elapsed_ms, None, None)


# ---------------------------------------------------------------- Clock（注入点）


class Clock(Protocol):
    def utc_now_iso(self) -> str: ...

    def stamp(self) -> str: ...


class RealClock:
    def utc_now_iso(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def stamp(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


# ---------------------------------------------------------------- 采集器


@dataclass(frozen=True)
class Failure:
    """采集器失败：安全类别 + 异常类名（非异常失败类名为空），无文本。"""

    category: str
    error_class: str

    def as_dict(self) -> dict[str, str | None]:
        return {"failure_category": self.category, "error_class": self.error_class or None}


def container_name(project: str, service: str, suffix: int = 1) -> str:
    return f"{project}-{service}-{suffix}"


def _run_or_failure(runner: Runner, argv: list[str], *, timeout: float) -> tuple[CommandResult | None, Failure | None]:
    try:
        return runner.run(argv, timeout=timeout), None
    except Exception as exc:  # noqa: BLE001 —— 类别化兜底，文本不保留
        category, klass = categorize_runner_exception(exc)
        return None, Failure(category, klass)


def parse_compose_ps_rows(stdout: str) -> list[dict[str, str]]:
    """解析 compose ps --format json：兼容 JSON 数组 / 单对象 / JSONL 三形态；
    提取 Service/Health/State（其余字段不保留）。不可解析 → 空清单。"""
    text = stdout.strip()
    if not text:
        return []
    raw_rows: list[object] = []
    try:
        whole = json.loads(text)
        raw_rows = whole if isinstance(whole, list) else [whole]
    except ValueError:
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                raw_rows.append(json.loads(line))
            except ValueError:
                continue
    rows: list[dict[str, str]] = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        service = str(raw.get("Service") or "")
        if not service:
            continue  # 无 Service 键不可靠映射（Name 是全容器名），fail-closed 跳过
        rows.append({
            "service": service,
            "health": str(raw.get("Health") or ""),
            "state": str(raw.get("State") or ""),
        })
    return rows


def collect_compose_ps(runner: Runner, *, compose_file: Path, project: str,
                       profile: str) -> tuple[dict[str, dict[str, str]] | None, Failure | None]:
    """docker compose ps --format json → {service: {health, state}}（只读）。"""
    argv = ["docker", "compose", "-f", str(compose_file), "-p", project,
            "--profile", profile, "ps", "--format", "json"]
    result, failure = _run_or_failure(runner, argv, timeout=COMPOSE_PS_TIMEOUT_SECONDS)
    if failure is not None:
        return None, failure
    assert result is not None
    if result.returncode != 0:
        return None, Failure("compose-ps-nonzero-exit", "")
    rows = parse_compose_ps_rows(result.stdout)
    if not rows:
        return None, Failure("compose-ps-unparseable", "")
    services: dict[str, dict[str, str]] = {}
    for row in rows:
        services[row["service"]] = {"health": row["health"], "state": row["state"]}
    return services, None


#: 单容器 inspect：state/health/RestartCount/image/started 五事实（\t 分隔）
INSPECT_FORMAT = (
    "{{.State.Status}}\t"
    "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}\t"
    "{{.RestartCount}}\t{{.Config.Image}}\t{{.State.StartedAt}}"
)


@dataclass(frozen=True)
class ContainerFacts:
    service: str
    name: str
    state: str
    health: str
    restart_count: int
    image: str
    started_at: str


def collect_container_facts(runner: Runner, *, project: str,
                            service: str) -> tuple[ContainerFacts | None, Failure | None]:
    """docker inspect --format <五事实> <container>（只读；单容器）。"""
    name = container_name(project, service)
    argv = ["docker", "inspect", "--format", INSPECT_FORMAT, name]
    result, failure = _run_or_failure(runner, argv, timeout=INSPECT_TIMEOUT_SECONDS)
    if failure is not None:
        return None, failure
    assert result is not None
    if result.returncode != 0:
        return None, Failure("inspect-nonzero-exit", "")
    parts = result.stdout.strip().split("\t")
    if len(parts) != 5:
        return None, Failure("inspect-unparseable", "")
    try:
        restart_count = int(parts[2].strip())
    except ValueError:
        return None, Failure("inspect-unparseable", "ValueError")
    return ContainerFacts(service, name, parts[0].strip(), parts[1].strip(),
                          restart_count, parts[3].strip(), parts[4].strip()), None


def collect_endpoint(transport: Transport, endpoint: Endpoint, *,
                     timeout_seconds: float) -> HttpResult:
    """单端点 GET（transport 注入；预校验过的 loopback 画像）。"""
    parts = urlsplit(endpoint.url)
    host = parts.hostname or ""
    port = parts.port or 80
    try:
        return transport.get(host, port, parts.path, timeout=timeout_seconds)
    except Exception as exc:  # noqa: BLE001 —— transport 自身缺陷兜底
        category, klass = categorize_http_exception(exc)
        return HttpResult(None, 0.0, category, klass)


#: 日志级别安全类别（词边界、大小写不敏感；只计数，绝不保留原文）
LOG_LEVEL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (level, re.compile(pattern, re.IGNORECASE))
    for level, pattern in (
        ("fatal", r"\bfatal\b"),
        ("error", r"\berror\b"),
        ("critical", r"\bcritical\b"),
        ("warning", r"\bwarn(?:ing)?\b"),
        ("traceback", r"\btraceback\b"),
        ("panic", r"\bpanic\b"),
    )
)
#: 阈值判定的「错误级」合计口径：fatal + error + critical（warning 不计入）
LOG_ERROR_LEVELS = frozenset({"fatal", "error", "critical"})

#: ---------------------------------------------------------------- M14-79
#: 日志错误时间界（root cause 修复）：M14-12 起 log-errors 对
#: ``docker logs --tail`` 的错误行**全尾计数且无时间界**——一次事故的陈旧
#: 错误（如 2026-09-21 04:29:19Z 的 6 条 postgres 错误）只要仍留在 tail
#: 窗口内就永远计入 error_total，warn 永不消退（13:15/13:30 样本无新错误
#: 仍 warn 的实证）。修复语义：
#: - ``docker logs`` 恒带 ``--timestamps``（只读输出旗标）——每行获得
#:   RFC3339 前缀，recency 可逐行建立；
#: - 阈值判定的 ``error_total`` 改为**当前区间计数**：仅统计时间戳 ≥
#:   基线（最新合法 prior 完整工件的 started_at_utc——与 M14-23 restart
#:   基线同源同解析）的错误行；早于基线的错误行计入
#:   ``stale_error_count``（显式入档，绝不静默丢弃）；
#: - **fail-closed**：错误行时间戳不可解析（无 --timestamps 前缀/形态
#:   非法）→ 恒计入当前区间（``unparsed_error_lines`` 显式计数）——
#:   recency 无法建立时绝不排除任何错误行，绝不遮蔽；基线缺失/不可用
#:   → 记账基准回退 ``full-tail``（= 修复前保守口径，全尾计数）；
#: - 阈值本身（warn≥5/critical≥20）零变更；``error_total_tail`` 原样
#:   保留全尾行数、``latest_error_at`` 保留最新错误行时间戳——陈旧错误
#:   的存在始终可见，只是不再永久阻塞恢复。

#: docker logs --timestamps 行首前缀（UTC Z 形态，可选 1-9 位小数秒；
#: 空格分隔）。带时区偏移的形态不接受（docker 恒输出 Z）——不可解析即
#: fail-closed 计入当前区间。
DOCKER_LOG_TS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d{1,9})?Z ")

#: 前缀捕获组格式：**无 Z 后缀**（Z 由正则按字面匹配，不进捕获组）——
#: 与 canonical TIMESTAMP_FORMAT（带 Z）刻意区分，解析须用本格式
DOCKER_LOG_TS_FORMAT = "%Y-%m-%dT%H:%M:%S"

#: 记账基准固定词汇
LOG_ACCOUNTING_WINDOW = "since-window-start"
LOG_ACCOUNTING_FULL_TAIL = "full-tail"


def parse_docker_log_timestamp(line: str) -> datetime | None:
    """纯函数：docker --timestamps 行首前缀 → UTC datetime；不可解析 → None。

    捕获组不含 Z（正则按字面匹配 Z 后缀），故用 DOCKER_LOG_TS_FORMAT
    （无 Z）解析——与 TIMESTAMP_FORMAT 混用会把一切合法时间戳判为不可
    解析（fail-closed 全计入当前区间，恢复语义退化为 full-tail）。
    返回值仅用于 recency 比较；解析失败由调用方按 fail-closed 口径处理
    （计入当前区间，绝不排除）。"""
    match = DOCKER_LOG_TS_RE.match(line)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), DOCKER_LOG_TS_FORMAT).replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None


def summarize_log_lines(lines: list[str], *,
                        window_start_dt: datetime | None = None,
                        ) -> dict[str, object]:
    """纯函数：行 → 级别匹配计数 + M14-79 时间界记账（见模块常量注释）。

    - ``levels``：全尾级别匹配计数（语义不变，仅信息面）；
    - ``error_total_tail``：全尾**错误级行数**（行基——一行匹配多个错误级
      仍计 1 行；修复前按级别匹配数求和，单级别行两口径恒相等）；
    - ``error_total``：阈值判定口径——window 基准=当前区间错误行数
      （≥ window_start 计入；< window_start 计 stale；时间戳不可解析恒
      计入当前区间），full-tail 基准=全尾错误行数（保守回退）。
    原文行绝不进入返回值——调用方也无处可放。"""
    levels: dict[str, int] = {level: 0 for level, _ in LOG_LEVEL_PATTERNS}
    error_patterns = [pattern for level, pattern in LOG_LEVEL_PATTERNS
                      if level in LOG_ERROR_LEVELS]
    tail_error_lines = 0
    current_error_lines = 0
    stale_error_lines = 0
    unparsed_error_lines = 0
    latest_error_dt: datetime | None = None
    for line in lines:
        for level, pattern in LOG_LEVEL_PATTERNS:
            if pattern.search(line):
                levels[level] += 1
        if not any(pattern.search(line) for pattern in error_patterns):
            continue
        tail_error_lines += 1
        line_dt = parse_docker_log_timestamp(line)
        if line_dt is not None:
            if latest_error_dt is None or line_dt > latest_error_dt:
                latest_error_dt = line_dt
            if window_start_dt is None or line_dt >= window_start_dt:
                current_error_lines += 1
            else:
                stale_error_lines += 1
        else:
            # fail-closed：recency 无法建立 → 恒计入当前区间，绝不排除
            current_error_lines += 1
            unparsed_error_lines += 1
    if window_start_dt is None:
        basis, window_start_at = LOG_ACCOUNTING_FULL_TAIL, None
        error_total = tail_error_lines
    else:
        basis = LOG_ACCOUNTING_WINDOW
        window_start_at = datetime.strftime(window_start_dt, TIMESTAMP_FORMAT)
        error_total = current_error_lines
    return {
        "lines_scanned": len(lines),
        "levels": levels,
        "error_total": error_total,
        "error_total_tail": tail_error_lines,
        "stale_error_lines": stale_error_lines if window_start_dt is not None else 0,
        "unparsed_error_lines": unparsed_error_lines,
        "latest_error_at": (datetime.strftime(latest_error_dt, TIMESTAMP_FORMAT)
                            if latest_error_dt is not None else None),
        "accounting": {"basis": basis, "window_start_at": window_start_at},
    }


def collect_log_summary(runner: Runner, *, project: str, service: str,
                        tail: int,
                        window_start_dt: datetime | None = None,
                        ) -> tuple[dict[str, object] | None, Failure | None]:
    """docker logs --tail <n> --timestamps <container>（只读；stdout+stderr
    合并后仅留计数/时间界记账，原文绝不保留）。"""
    name = container_name(project, service)
    argv = ["docker", "logs", "--tail", str(tail), "--timestamps", name]
    result, failure = _run_or_failure(runner, argv, timeout=LOGS_TIMEOUT_SECONDS)
    if failure is not None:
        return None, failure
    assert result is not None
    if result.returncode != 0:
        return None, Failure("logs-nonzero-exit", "")
    lines = (result.stdout + result.stderr).splitlines()
    return summarize_log_lines(lines, window_start_dt=window_start_dt), None


def _sub_collector_status(items: dict[str, dict[str, object]]) -> str:
    return "ok" if all(item.get("status") == "ok" for item in items.values()) and items else "failed"


def collect_snapshot(*, runner: Runner, transport: Transport, endpoints: list[Endpoint],
                     project: str, profile: str, compose_file: Path, log_tail: int,
                     request_timeout_seconds: float,
                     log_window_start: str | None = None) -> dict[str, object]:
    """只读采集主入口：compose ps + 六容器 inspect + 端点 GET + 日志摘要。
    部分失败如实入档（partial 由整体汇总推导），缺失绝不标记 healthy。"""
    ps_services, ps_failure = collect_compose_ps(
        runner, compose_file=compose_file, project=project, profile=profile)
    if ps_failure is not None:
        compose_ps: dict[str, object] = {"status": "failed", **ps_failure.as_dict(), "services": {}}
    else:
        assert ps_services is not None
        compose_ps = {"status": "ok", "failure_category": None, "error_class": None,
                      "services": ps_services}

    per_service: dict[str, dict[str, object]] = {}
    for service in STACK_SERVICES:
        facts, failure = collect_container_facts(runner, project=project, service=service)
        if failure is not None:
            per_service[service] = {"status": "failed", **failure.as_dict()}
        else:
            assert facts is not None
            per_service[service] = {
                "status": "ok", "failure_category": None, "error_class": None,
                "name": facts.name, "state": facts.state, "health": facts.health,
                "restart_count": facts.restart_count, "image": facts.image,
                "started_at": facts.started_at,
            }
    containers = {"status": _sub_collector_status(per_service), "per_service": per_service}

    per_endpoint: dict[str, dict[str, object]] = {}
    for endpoint in endpoints:
        result = collect_endpoint(transport, endpoint, timeout_seconds=request_timeout_seconds)
        if result.error_category is not None:
            per_endpoint[endpoint.endpoint_id] = {
                "status": "failed", "failure_category": result.error_category,
                "error_class": result.error_class, "http_status": None, "latency_ms": None,
            }
        else:
            per_endpoint[endpoint.endpoint_id] = {
                "status": "ok", "failure_category": None, "error_class": None,
                "http_status": result.status, "latency_ms": round(result.elapsed_ms, 3),
            }
    endpoint_collector = {"status": _sub_collector_status(per_endpoint), "per_endpoint": per_endpoint}

    per_log: dict[str, dict[str, object]] = {}
    # M14-79：日志错误时间界（canonical 形态校验失败 → None = full-tail
    # 保守回退，方向恒为多计不少计）
    window_dt: datetime | None = None
    if log_window_start:
        try:
            window_dt = datetime.strptime(log_window_start, TIMESTAMP_FORMAT).replace(
                tzinfo=timezone.utc)
        except ValueError:
            window_dt = None
    for service in STACK_SERVICES:
        summary, failure = collect_log_summary(runner, project=project, service=service,
                                                tail=log_tail,
                                                window_start_dt=window_dt)
        if failure is not None:
            per_log[service] = {"status": "failed", **failure.as_dict()}
        else:
            assert summary is not None
            per_log[service] = {"status": "ok", "failure_category": None, "error_class": None, **summary}
    logs = {"status": _sub_collector_status(per_log), "per_service": per_log}

    return {
        "compose_ps": compose_ps,
        "containers": containers,
        "endpoints": endpoint_collector,
        "logs": logs,
    }


def snapshot_partial(collectors: dict[str, object]) -> bool:
    """部分失败判定：任一采集器非 ok 即 partial（缺失 ≠ healthy）。"""
    return any(collector.get("status") != "ok" for collector in collectors.values())


# ---------------------------------------------------------------- restart 增量基线（M14-23）

#: 基线候选工件 stem 白名单（与 monitoring_history 同款严格形态）
BASELINE_STEM_RE = re.compile(r"^monitor-[0-9]{8}-[0-9]{6}$")

#: canonical 时间戳形态（与 RealClock.utc_now_iso / monitoring_history 对齐）
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

#: restart 评估 reason 固定词汇（schema 面；绝不携带采集原文）
RESTART_REASONS: tuple[str, ...] = (
    "stable",                # 同实例、增量 0 → ok
    "delta",                 # 同实例、增量 > 0（达阈值）
    "baseline-missing",      # 无可用基线（一次性可见语义）
    "container-recreated",   # started_at 变化（一轮可见 warn）
    "counter-reset",         # 累计值下降（一轮可见 warn；负增量绝不静默归零）
    "facts-missing",         # 容器事实采集失败（恒 critical）
)


def _valid_canonical_timestamp(value: object) -> bool:
    """canonical 时间戳校验（``%Y-%m-%dT%H:%M:%SZ`` 且日历合法；supervisor
    R1——畸形/缺失值绝不复制进 baseline.collected_at）。"""
    if not isinstance(value, str):
        return False
    try:
        datetime.strptime(value, TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return True


def _valid_baseline_calendar(stem: str) -> bool:
    """stem 时间段日历合法性（monitor-YYYYMMDD-HHMMSS；与 history 同款）。"""
    try:
        datetime.strptime(stem[len("monitor-"):], "%Y%m%d-%H%M%S").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class RestartBaseline:
    """M14-23 restart 增量基线：最新合法 prior 完整 monitor 工件的六容器
    累计/started 事实。status：ok=可用；missing=无候选（含目录缺失/不可
    读）；unusable=有候选但全部不可用。invalid_skipped_count 显式记录被
    跳过的非法/不可用候选——绝不静默当作零基线。"""

    status: str
    reason: str | None
    source_stem: str | None
    collected_at: str | None
    restart_counts: dict[str, int]
    started_ats: dict[str, str]
    invalid_skipped_count: int

    def as_report_dict(self) -> dict[str, object]:
        """报告面安全元数据（固定词汇/白名单 stem，无任何采集原文）。"""
        return {
            "status": self.status,
            "reason": self.reason,
            "source_stem": self.source_stem,
            "collected_at": self.collected_at,
            "invalid_skipped_count": self.invalid_skipped_count,
        }


def parse_baseline_facts(data: object) -> dict[str, object] | None:
    """纯函数：monitor 报告对象 → 六容器 restart/started 基线事实；身份
    不符（schema/tool/**milestone**/mode）、**非完整工件**（partial 恒须为
    布尔 False——True/字符串/整数/缺失一律不可作基线）、started_at_utc
    非 canonical 时间戳（``%Y-%m-%dT%H:%M:%SZ`` 且日历合法——supervisor
    R1：畸形/缺失值绝不复制进 baseline.collected_at）、容器事实缺失或非法
    → None（该工件不可作基线）。v1 旧工件（无 restart_evaluation）同样
    合法——基线只依赖采集事实，不依赖评估字段。"""
    if not isinstance(data, dict):
        return None
    if data.get("schema_version") != REPORT_SCHEMA_VERSION:
        return None
    if data.get("tool") != TOOL_NAME or data.get("mode") != "execute":
        return None
    if data.get("milestone") != MILESTONE:
        return None
    if data.get("partial") is not False:  # 真正完整的 execute 工件才可作基线
        return None
    collected = data.get("started_at_utc")
    if not _valid_canonical_timestamp(collected):
        return None
    collectors = data.get("collectors")
    if not isinstance(collectors, dict):
        return None
    containers = collectors.get("containers")
    if not isinstance(containers, dict):
        return None
    per_service = containers.get("per_service")
    if not isinstance(per_service, dict):
        return None
    restart_counts: dict[str, int] = {}
    started_ats: dict[str, str] = {}
    for service in STACK_SERVICES:
        item = per_service.get(service)
        if not isinstance(item, dict) or item.get("status") != "ok":
            return None
        restart = item.get("restart_count")
        started = item.get("started_at")
        if not isinstance(restart, int) or isinstance(restart, bool) or restart < 0:
            return None
        if not isinstance(started, str) or not started:
            return None
        restart_counts[service] = restart
        started_ats[service] = started
    return {
        "collected_at": collected,
        "restart_counts": restart_counts,
        "started_ats": started_ats,
    }


def _artifact_dir_unsafe(directory: Path) -> bool:
    """symlink 防御（supervisor R1）：目录自身或任一现存祖先为 symlink 即
    视为不可读（固定词汇拒绝，绝不跟随——与写盘面 _reject_symlinks 同族
    纪律；is_symlink 探测自身异常同样按不安全处理）。"""
    try:
        if directory.is_symlink():
            return True
        for ancestor in directory.parents:
            if ancestor.exists() and ancestor.is_symlink():
                return True
    except OSError:
        return True
    return False


def resolve_restart_baseline(directory: Path) -> RestartBaseline:
    """从工件目录解析 restart 增量基线（最新合法 prior 完整工件）。

    纯本地只读 fail-safe：仅 os.listdir + 单文件 read_bytes + json 解析，
    零 shell=True/零网络/零写盘；symlinked 目录/祖先 → missing/
    artifact-dir-unreadable（绝不跟随）；目录缺失 → missing/
    artifact-dir-missing；目录不可读（含路径是文件）→ missing/
    artifact-dir-unreadable；任何读取/解析异常都只推进
    invalid_skipped_count，symlinked 候选文件同样计 invalid 而不跟随。
    候选按文件名降序（stem 形态保证字典序即时间序）取首个可解析合法件；
    全部不可用 → unusable（no-usable-prior-artifacts）——绝不静默回退为
    零基线。"""
    if _artifact_dir_unsafe(directory):
        return RestartBaseline("missing", "artifact-dir-unreadable", None, None, {}, {}, 0)
    try:
        names = sorted(os.listdir(directory), reverse=True)
    except FileNotFoundError:
        return RestartBaseline("missing", "artifact-dir-missing", None, None, {}, {}, 0)
    except OSError:
        return RestartBaseline("missing", "artifact-dir-unreadable", None, None, {}, {}, 0)
    candidates = [name for name in names
                  if name.startswith("monitor-") and name.endswith(".json")]
    invalid_skipped = 0
    for name in candidates:  # 降序：最新在前
        stem = name[: -len(".json")]
        if BASELINE_STEM_RE.match(stem) is None or not _valid_baseline_calendar(stem):
            invalid_skipped += 1
            continue
        path = directory / name
        try:
            if path.is_symlink():  # 候选 symlink：计 invalid，绝不跟随
                invalid_skipped += 1
                continue
            data = json.loads(path.read_bytes().decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            invalid_skipped += 1
            continue
        facts = parse_baseline_facts(data)
        if facts is None:
            invalid_skipped += 1
            continue
        return RestartBaseline(
            status="ok", reason=None, source_stem=stem,
            collected_at=facts["collected_at"],  # type: ignore[arg-type]
            restart_counts=facts["restart_counts"],  # type: ignore[arg-type]
            started_ats=facts["started_ats"],  # type: ignore[arg-type]
            invalid_skipped_count=invalid_skipped,
        )
    if candidates:
        return RestartBaseline("unusable", "no-usable-prior-artifacts", None, None,
                               {}, {}, invalid_skipped)
    return RestartBaseline("missing", "no-prior-artifacts", None, None, {}, {}, 0)


# ---------------------------------------------------------------- 阈值判定（纯）


def _check(check_id: str, subject: str, severity: str, detail: str) -> dict[str, str]:
    return {"check_id": check_id, "subject": subject, "severity": severity, "detail": detail}


def evaluate_restart_state(service: str, item: dict[str, object],
                           baseline: RestartBaseline | None,
                           thresholds: Thresholds) -> dict[str, object]:
    """纯函数（M14-23）：单服务 restart 增量评估 → {state, reason, delta}。

    - 基线可用（status=ok）：
      · ``started_at`` 与基线不一致 → container-recreated（一轮可见 warn，
        增量不可比，delta=None）；
      · 当前累计 < 基线累计（同 started_at）→ counter-reset（一轮可见
        warn——**负增量绝不静默映射为零**，delta=None）；
      · 否则 delta = 当前累计 − 基线累计，阈值含边界（delta≥critical →
        critical；≥warn → warn；否则 ok）。
    - 基线缺失/不可用（或该服务无基线事实）：count 0 → ok；count 达
      warn/critical → 一次性 baseline-missing 可见告警（下一轮以本轮工件
      为基线、count 不变即恢复 ok）。"""
    current = int(item.get("restart_count") or 0)
    started = str(item.get("started_at") or "")
    if (baseline is None or baseline.status != "ok"
            or service not in baseline.restart_counts):
        if current >= thresholds.restart_critical:
            state = "critical"
        elif current >= thresholds.restart_warn:
            state = "warn"
        else:
            state = "ok"
        return {"state": state, "reason": "baseline-missing", "delta": None}
    base_count = baseline.restart_counts[service]
    base_started = baseline.started_ats.get(service, "")
    if started and base_started and started != base_started:
        return {"state": "warn", "reason": "container-recreated", "delta": None}
    if current < base_count:
        return {"state": "warn", "reason": "counter-reset", "delta": None}
    delta = current - base_count
    if delta >= thresholds.restart_critical:
        state = "critical"
    elif delta >= thresholds.restart_warn:
        state = "warn"
    else:
        state = "ok"
    return {"state": state, "reason": "delta" if delta > 0 else "stable", "delta": delta}


def _restart_check_detail(restart_count: int, evaluation: dict[str, object],
                          baseline: RestartBaseline | None) -> str:
    """container-restarts 检查 detail（固定词汇 + 白名单 stem，无采集原文）。"""
    reason = str(evaluation["reason"])
    if reason == "baseline-missing":
        return f"restart_count={restart_count} baseline-missing"
    if reason == "container-recreated":
        return f"restart_count={restart_count} container-recreated started_at_changed"
    if reason == "counter-reset":
        return f"restart_count={restart_count} counter-reset"
    source = baseline.source_stem if baseline is not None else None
    return f"restart_count={restart_count} delta={evaluation['delta']} baseline={source}"


def _baseline_report_metadata(baseline: RestartBaseline | None) -> dict[str, object]:
    """评估结果的 baseline 元数据（None = 直接调用未解析——显式 missing）。"""
    if baseline is not None:
        return baseline.as_report_dict()
    return {"status": "missing", "reason": "baseline-not-resolved",
            "source_stem": None, "collected_at": None, "invalid_skipped_count": 0}


def evaluate_thresholds(collectors: dict[str, object], thresholds: Thresholds,
                        endpoints: tuple[Endpoint, ...] | list[Endpoint] = ENDPOINTS,
                        baseline: RestartBaseline | None = None,
                        ) -> dict[str, object]:
    """纯函数：collectors + thresholds（+ M14-23 restart 增量基线）→ 逐项
    检查/计数/alerts/总状态。fail-closed：采集失败的项恒为可见 critical
    （缺失绝不当作 healthy）。container-restarts 检查改评**当轮新增增量**
    （见 evaluate_restart_state）；累计 RestartCount 事实照实保留在
    collectors 中不变。端点检查覆盖所选端点子集（与报告 config.endpoints
    一致，计数自洽）。"""
    checks: list[dict[str, str]] = []

    compose_ps = collectors["compose_ps"]
    assert isinstance(compose_ps, dict)
    ps_services = compose_ps.get("services")
    assert isinstance(ps_services, dict)
    for service in STACK_SERVICES:
        row = ps_services.get(service)
        if not isinstance(row, dict):
            checks.append(_check("compose-service", service, "critical", "missing-from-compose-ps"))
        elif row.get("health") != "healthy":
            checks.append(_check("compose-service", service, "critical",
                                 f"health={row.get('health')!r}"))
        else:
            checks.append(_check("compose-service", service, "ok", "health=healthy"))
    if compose_ps.get("status") != "ok":
        checks.append(_check("collector:compose-ps", "compose-ps", "critical",
                             f"category={compose_ps.get('failure_category')}"))

    containers = collectors["containers"]
    assert isinstance(containers, dict)
    per_service = containers.get("per_service")
    assert isinstance(per_service, dict)
    restart_evaluation: dict[str, dict[str, object]] = {}
    for service in STACK_SERVICES:
        item = per_service.get(service)
        assert isinstance(item, dict)
        if item.get("status") != "ok":
            checks.append(_check("container-health", service, "critical", "facts-missing"))
            checks.append(_check("container-restarts", service, "critical", "facts-missing"))
            restart_evaluation[service] = {"state": "critical", "reason": "facts-missing",
                                           "delta": None}
            continue
        state = str(item.get("state"))
        health = str(item.get("health"))
        if state != "running":
            checks.append(_check("container-health", service, "critical", f"state={state}"))
        elif health == "healthy":
            checks.append(_check("container-health", service, "ok", "state=running health=healthy"))
        elif health == "unhealthy":
            checks.append(_check("container-health", service, "critical", f"health={health}"))
        else:  # starting / none / 未知——不可证 healthy，warn 可见
            checks.append(_check("container-health", service, "warn", f"health={health}"))
        evaluation = evaluate_restart_state(service, item, baseline, thresholds)
        restart_evaluation[service] = evaluation
        checks.append(_check(
            "container-restarts", service, str(evaluation["state"]),
            _restart_check_detail(int(item.get("restart_count") or 0), evaluation, baseline),
        ))

    endpoints_collector = collectors["endpoints"]
    assert isinstance(endpoints_collector, dict)
    per_endpoint = endpoints_collector.get("per_endpoint")
    assert isinstance(per_endpoint, dict)
    for endpoint in endpoints:
        item = per_endpoint.get(endpoint.endpoint_id)
        assert isinstance(item, dict)
        if item.get("status") != "ok":
            checks.append(_check("endpoint-status", endpoint.endpoint_id, "critical",
                                 f"category={item.get('failure_category')}"))
            continue
        status = item.get("http_status")
        if status != 200:
            checks.append(_check("endpoint-status", endpoint.endpoint_id, "critical",
                                 f"http_status={status}"))
            continue
        checks.append(_check("endpoint-status", endpoint.endpoint_id, "ok", "http_status=200"))
        latency = float(item.get("latency_ms") or 0.0)
        if latency >= thresholds.latency_critical_ms:
            severity = "critical"
        elif latency >= thresholds.latency_warn_ms:
            severity = "warn"
        else:
            severity = "ok"
        checks.append(_check("endpoint-latency", endpoint.endpoint_id, severity,
                             f"latency_ms={latency:g}"))

    logs = collectors["logs"]
    assert isinstance(logs, dict)
    per_log = logs.get("per_service")
    assert isinstance(per_log, dict)
    for service in STACK_SERVICES:
        item = per_log.get(service)
        assert isinstance(item, dict)
        if item.get("status") != "ok":
            checks.append(_check("log-errors", service, "critical", "summary-missing"))
            continue
        error_total = int(item.get("error_total") or 0)
        if error_total >= thresholds.log_error_critical:
            severity = "critical"
        elif error_total >= thresholds.log_error_warn:
            severity = "warn"
        else:
            severity = "ok"
        # M14-79：detail 显式携带时间界记账面（全尾/陈旧/不可解析/窗口
        # 起点/最新错误时刻）——恢复判定可审计，陈旧错误绝不静默消失
        tail_total = int(item.get("error_total_tail") or error_total)
        stale = int(item.get("stale_error_lines") or 0)
        unparsed = int(item.get("unparsed_error_lines") or 0)
        detail = (f"error_total={error_total} tail={tail_total} "
                  f"stale={stale} unparsed={unparsed}")
        latest_error = item.get("latest_error_at")
        if isinstance(latest_error, str) and latest_error:
            detail += f" latest_error_at={latest_error}"
        accounting = item.get("accounting")
        if (isinstance(accounting, dict)
                and accounting.get("basis") == LOG_ACCOUNTING_WINDOW):
            detail += f" basis=window start={accounting.get('window_start_at')}"
        else:
            detail += " basis=full-tail"
        checks.append(_check("log-errors", service, severity, detail))

    alerts = [check for check in checks if check["severity"] != "ok"]
    counts = {
        "ok": sum(1 for c in checks if c["severity"] == "ok"),
        "warn": sum(1 for c in checks if c["severity"] == "warn"),
        "critical": sum(1 for c in checks if c["severity"] == "critical"),
    }
    partial = snapshot_partial(collectors)
    if partial:
        overall = "incomplete"
    elif counts["critical"]:
        overall = "critical"
    elif counts["warn"]:
        overall = "warn"
    else:
        overall = "ok"
    return {
        "checks": checks,
        "counts": counts,
        "alerts": alerts,
        "partial": partial,
        "overall_status": overall,
        "monitoring_ready": not partial and not alerts,
        # M14-23 加法字段：baseline 元数据 + 逐服务增量评估（v1 旧消费者
        # 不读该键照常有效；旧工件作历史输入时同样可选）
        "restart_evaluation": {
            "baseline": _baseline_report_metadata(baseline),
            "per_service": restart_evaluation,
        },
    }


# ---------------------------------------------------------------- 脱敏（防御性）

SECRET_SHAPE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-[A-Za-z0-9_-]{8,}"), "[REDACTED:token]"),
    (re.compile(r"ghp_[A-Za-z0-9]{8,}"), "[REDACTED:token]"),
    (re.compile(r"gho_[A-Za-z0-9]{8,}"), "[REDACTED:token]"),
    (re.compile(r"AKIA[0-9A-Z]{12,}"), "[REDACTED:key]"),
    (re.compile(r"xoxb-[A-Za-z0-9-]{8,}"), "[REDACTED:token]"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"), "[REDACTED:token]"),
    (re.compile(r"(?i)(api[_-]?key|secret|password)\s*[=:]\s*\S{8,}"), "[REDACTED:credential]"),
)


def redact_secrets(text: str) -> str:
    for pattern, label in SECRET_SHAPE_PATTERNS:
        text = pattern.sub(label, text)
    return text


class SafeLog:
    """行式日志：stdout 输出前经 redact_secrets 防御性脱敏。"""

    def __init__(self, echo: bool = True) -> None:
        self.lines: list[str] = []
        self._echo = echo

    def say(self, message: str) -> None:
        line = redact_secrets(message)
        self.lines.append(line)
        if self._echo:
            print(f"{TAG} {line}", flush=True)


# ---------------------------------------------------------------- 报告（原子写 + symlink/越界拒绝）


def build_config(*, project: str, profile: str, compose_file: Path, endpoints: list[Endpoint],
                 log_tail: int, request_timeout_seconds: float,
                 thresholds: Thresholds, voice_health_source: str = "loopback",
                 log_error_window_start: str | None = None,
                 ) -> dict[str, object]:
    return {
        "project": project,
        "profile": profile,
        "compose_file": compose_file.name,
        "services": list(STACK_SERVICES),
        "endpoints": [{"endpoint_id": e.endpoint_id, "url": e.url, "group": e.group} for e in endpoints],
        "voice_health_source": voice_health_source,
        "log_tail": log_tail,
        "request_timeout_seconds": request_timeout_seconds,
        "thresholds": thresholds.as_dict(),
        "log_error_window_start": log_error_window_start,
    }


#: 报告边界注记（固定词汇表：绝不包含任何采集原文）
REPORT_BOUNDARIES: tuple[str, ...] = (
    "read-only collection: compose ps + docker inspect + docker logs --tail/--timestamps + GET-only HTTP; zero mutations",
    (
        "web/api health-check targets remain fixed literal loopback GET-only URLs"
        " (127.0.0.1 with explicit ports; no DNS, no query, no fragment, no userinfo)"
    ),
    (
        "sidecar voice health-check targets are derived only from the canonical sidecar"
        " manifest as literal RFC1918 IPv4 hosts on fixed ports 18010/18011 with the"
        " exact path /health: no DNS, no query, no fragment, no userinfo, no arbitrary"
        " URLs, no manifest path injection, and no loopback fallback (an invalid or"
        " missing manifest fails closed with zero collection)"
    ),
    "all docker commands pass the readonly whitelist gate (compose ps / inspect / logs --tail/--timestamps only)",
    "http.client direct connection: proxy env never consulted (loopback proxy bypass)",
    "container log summary: match counts and level categories only; raw log lines never persisted",
    (
        "log-error thresholds evaluate the current-interval count (M14-79): only error"
        " lines with a docker --timestamps prefix at or after the baseline (latest legal"
        " prior complete artifact) count toward error_total; older errors are recorded"
        " as stale_error_lines and never silently dropped; error lines whose timestamp"
        " cannot be parsed always count as current (fail closed); with no usable"
        " baseline the accounting falls back to full-tail (the pre-M14-79 conservative"
        " whole-tail count); thresholds themselves are unchanged"
    ),
    "collector failures are recorded as partial=true with a safe category; missing is never treated as healthy",
    (
        "restart thresholds evaluate the current-interval delta against the latest legal prior"
        " complete artifact in the artifact directory (M14-23); cumulative RestartCount facts"
        " are recorded unchanged; invalid baseline candidates are counted explicitly and never"
        " silently treated as a zero baseline"
    ),
    "no external alerting is performed in this milestone; reports are local evidence only",
    "monitoring_ready is not production readiness; this tool never claims production ready",
    (
        "default artifact directory is .verify/artifacts/m14-12-production-monitoring under the repo root and is gitignored;"
        " a custom --artifact-dir is an explicit operator selection whose location and gitignore status are the operator's responsibility"
    ),
)


def artifact_dir_note(directory: Path) -> str:
    """报告目录注记（固定词汇）：默认目录 gitignored；自定义路径为操作者
    显式自选——不对其 gitignore 状态作任何宣称。"""
    if directory == ARTIFACT_DIR:
        return "默认目录（gitignored，不入库）"
    return "自定义目录（操作者显式自选，位置与入库与否由操作者负责）"


def build_report(*, mode: str, started_utc: str, ended_utc: str, config: dict[str, object],
                 collectors: dict[str, object] | None, threshold_results: dict[str, object] | None,
                 proxy_env_keys_present: bool) -> dict[str, object]:
    """schema 版本化报告（纯数据；写盘前再经 redact_secrets 终防线）。"""
    report: dict[str, object] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "milestone": MILESTONE,
        "mode": mode,
        "started_at_utc": started_utc,
        "ended_at_utc": ended_utc,
        "config": config,
        "boundaries": list(REPORT_BOUNDARIES) + [
            f"proxy_env_keys_present={proxy_env_keys_present}（仅键名存在性，值绝不读取/记录）",
        ],
    }
    if collectors is not None:
        assert threshold_results is not None
        report["collectors"] = collectors
        report["partial"] = threshold_results["partial"]
        report["threshold_results"] = threshold_results
        report["overall_status"] = threshold_results["overall_status"]
        report["monitoring_ready"] = threshold_results["monitoring_ready"]
    else:
        report["collectors"] = {"status": "planned", "planned": [
            "compose-ps", "container-inspect", "endpoint-gets", "log-summaries",
        ]}
    return report


def render_markdown(report: dict[str, object]) -> str:
    """Markdown 摘要（固定词汇表 + 渲染后统一脱敏）。"""
    lines: list[str] = [
        f"# {report['milestone']} 生产监控报告（mode={report['mode']}）",
        "",
        f"- 工具：`{report['tool']}`（schema_version={report['schema_version']}）",
        f"- 开始（UTC）：{report['started_at_utc']}；结束（UTC）：{report['ended_at_utc']}",
        f"- 项目：{report['config']['project']}（profile {report['config']['profile']}，compose {report['config']['compose_file']}）",  # type: ignore[index]
    ]
    if report["mode"] == "execute":
        counts = report["threshold_results"]["counts"]  # type: ignore[index]
        assert isinstance(counts, dict)
        lines += [
            (
                f"- overall_status=**{report['overall_status']}**；monitoring_ready={report['monitoring_ready']}；"
                f"partial={report['partial']}"
            ),
            f"- 检查计数：ok={counts['ok']} warn={counts['warn']} critical={counts['critical']}",
        ]
        restart_eval = report["threshold_results"].get("restart_evaluation")  # type: ignore[union-attr]
        if isinstance(restart_eval, dict):
            baseline_meta = restart_eval.get("baseline")
            assert isinstance(baseline_meta, dict)
            per_service_eval = restart_eval.get("per_service")
            assert isinstance(per_service_eval, dict)
            warn_n = sum(1 for v in per_service_eval.values()
                         if isinstance(v, dict) and v.get("state") == "warn")
            critical_n = sum(1 for v in per_service_eval.values()
                             if isinstance(v, dict) and v.get("state") == "critical")
            source = baseline_meta.get("source_stem") or baseline_meta.get("status")
            lines.append(
                f"- restart 增量评估（M14-23）：baseline={source}"
                f"（增量 ok={len(per_service_eval) - warn_n - critical_n}"
                f" warn={warn_n} critical={critical_n}；阈值评当轮新增，"
                "累计 RestartCount 照实入档）"
            )
        lines += [
            "",
            "| 检查 | 对象 | 严重度 | 详情 |",
            "|---|---|---|---|",
        ]
        checks = report["threshold_results"]["checks"]  # type: ignore[index]
        assert isinstance(checks, list)
        for check in checks:
            assert isinstance(check, dict)
            lines.append(f"| {check['check_id']} | {check['subject']} | {check['severity']} | {check['detail']} |")
    lines += ["", "边界："]
    lines += [f"- {item}" for item in report["boundaries"]]  # type: ignore[arg-type]
    return "\n".join(lines) + "\n"


class ReportPathError(RuntimeError):
    """报告路径非法（symlink 组件 / 越界 stem）——可见拒绝，零写入。"""


_STEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _reject_symlinks(*paths: Path) -> None:
    """拒绝 symlink：目标文件自身 + 目录的全部现存祖先组件。"""
    for path in paths:
        if path.is_symlink():
            raise ReportPathError("symlink-target")
        for ancestor in path.parents:
            if ancestor.exists() and ancestor.is_symlink():
                raise ReportPathError("symlink-in-path")


def write_reports_atomic(report: dict[str, object], directory: Path,
                         stem: str) -> tuple[Path, Path]:
    """JSON + Markdown 双写：内容先经 redact_secrets 终防线，再同目录 tmp +
    os.replace 原子落盘；symlink/越界路径一律 ReportPathError（零写入）。"""
    if "/" in stem or "\\" in stem or ".." in stem or not _STEM_RE.match(stem):
        raise ReportPathError("bad-stem")
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{stem}.json"
    md_path = directory / f"{stem}.md"
    _reject_symlinks(json_path, md_path, directory)
    json_text = redact_secrets(json.dumps(report, ensure_ascii=False, indent=2))
    md_text = redact_secrets(render_markdown(report))
    for final_path, suffix, text in ((json_path, "json", json_text), (md_path, "md", md_text)):
        tmp_path = directory / f".{stem}.{suffix}.tmp"
        if tmp_path.is_symlink():
            raise ReportPathError("symlink-tmp")
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, final_path)
    return json_path, md_path


# ---------------------------------------------------------------- CLI


def proxy_env_present() -> bool:
    """仅探测 proxy 环境变量**键名存在性**（membership 语义；值绝不读取/记录）。"""
    return any(key in os.environ for key in PROXY_ENV_KEYS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="production_monitor.py",
        description="M14-12 生产监控 readiness（默认 plan 零采集；execute 需旗标+精确确认短语，只读采集+阈值判定+证据报告，不接外部告警）",
    )
    parser.add_argument("--execute", action="store_true",
                        help="真实只读采集（默认 plan：零 subprocess/零网络/零生产读取）")
    parser.add_argument("--confirm", default="",
                        help=f'execute 必配 --confirm "{CONFIRM_PHRASE}"（精确匹配）')
    parser.add_argument("--project", default=DEFAULT_PROJECT,
                        help=f"compose 项目名（默认 {DEFAULT_PROJECT}）")
    parser.add_argument("--request-timeout-seconds", type=float, default=DEFAULT_REQUEST_TIMEOUT_SECONDS,
                        help=f"端点 GET 超时 {MIN_REQUEST_TIMEOUT_SECONDS}-{MAX_REQUEST_TIMEOUT_SECONDS}s（默认 {DEFAULT_REQUEST_TIMEOUT_SECONDS}）")
    parser.add_argument("--latency-warn-ms", type=float, default=DEFAULT_LATENCY_WARN_MS,
                        help=f"端点延迟 warn 阈值 ms（默认 {DEFAULT_LATENCY_WARN_MS}，含边界）")
    parser.add_argument("--latency-critical-ms", type=float, default=DEFAULT_LATENCY_CRITICAL_MS,
                        help=f"端点延迟 critical 阈值 ms（默认 {DEFAULT_LATENCY_CRITICAL_MS}，含边界）")
    parser.add_argument("--restart-warn", type=int, default=DEFAULT_RESTART_WARN,
                        help=f"容器 RestartCount warn 阈值（默认 {DEFAULT_RESTART_WARN}，含边界）")
    parser.add_argument("--restart-critical", type=int, default=DEFAULT_RESTART_CRITICAL,
                        help=f"容器 RestartCount critical 阈值（默认 {DEFAULT_RESTART_CRITICAL}，含边界）")
    parser.add_argument("--log-error-warn", type=int, default=DEFAULT_LOG_ERROR_WARN,
                        help=f"日志错误计数 warn 阈值（默认 {DEFAULT_LOG_ERROR_WARN}，含边界）")
    parser.add_argument("--log-error-critical", type=int, default=DEFAULT_LOG_ERROR_CRITICAL,
                        help=f"日志错误计数 critical 阈值（默认 {DEFAULT_LOG_ERROR_CRITICAL}，含边界）")
    parser.add_argument("--log-tail", type=int, default=DEFAULT_LOG_TAIL,
                        help=f"docker logs --tail 行数 {MIN_LOG_TAIL}-{MAX_LOG_TAIL}（默认 {DEFAULT_LOG_TAIL}）")
    parser.add_argument("--only", action="append", metavar="ENDPOINT_ID",
                        help="仅采集指定端点（可重复；候选见 plan 输出；阈值恒评全五端点画像的已选子集）")
    parser.add_argument("--voice-health-source", choices=("loopback", "sidecar"),
                        default="loopback",
                        help="语音健康端点来源（默认 loopback=8010/8011 直采；sidecar=M14-26 窄代理"
                             " 18010/18011，URL 由 canonical sidecar manifest 严格校验派生，绝不回退）")
    parser.add_argument("--artifact-dir", type=Path, default=ARTIFACT_DIR,
                        help="报告目录（默认 .verify/artifacts/m14-12-production-monitoring，gitignored；"
                             "自定义路径为操作者显式自选，其位置与入库与否由操作者负责）")
    return parser


def _exit_code_for(results: dict[str, object]) -> int:
    if results["overall_status"] in ("incomplete", "critical"):
        return EXIT_INCOMPLETE if results["overall_status"] == "incomplete" else EXIT_CRITICAL
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log = SafeLog()
    # 1) 限制校验（plan 与 execute 都校验；超顶零采集拒绝）
    problems = validate_thresholds(
        request_timeout=args.request_timeout_seconds,
        latency_warn=args.latency_warn_ms, latency_critical=args.latency_critical_ms,
        restart_warn=args.restart_warn, restart_critical=args.restart_critical,
        log_error_warn=args.log_error_warn, log_error_critical=args.log_error_critical,
        log_tail=args.log_tail,
    )
    if problems:
        for problem in problems:
            log.say(f"拒绝: {problem}")
        return EXIT_USAGE
    # 1.5) compose 项目名严格白名单（plan 报告写入/采集之前；被拒值不回显）
    project_problem = validate_project_name(args.project)
    if project_problem is not None:
        log.say(f"拒绝: --project 名非法（原因: {project_problem}）——被拒值不回显")
        return EXIT_USAGE
    # 1.7) 语音健康来源（M14-27）：sidecar 需 canonical manifest 严格校验通过才
    # 放行——任何失败按固定词表拒绝（发生在报告写入与 Runner/Transport 构造
    # 之前，零采集零回退）；loopback 恒用固定五端点画像
    endpoint_profile: list[Endpoint] = list(ENDPOINTS)
    if args.voice_health_source == "sidecar":
        bind, manifest_error = load_sidecar_manifest(SIDECAR_MANIFEST_PATH)
        if manifest_error is not None:
            log.say(f"拒绝: sidecar 语音健康清单不可用（原因: {manifest_error}）——零采集/零报告")
            return EXIT_USAGE
        endpoint_profile = build_sidecar_endpoints(bind)
    # 2) 端点面（--only 仅限所选画像内集合）
    endpoints, endpoint_error = select_endpoints(args.only, profile=endpoint_profile)
    if endpoint_error is not None:
        log.say(f"拒绝: {endpoint_error}")
        return EXIT_USAGE
    invalid: list[str] = []
    for endpoint in endpoints:
        sidecar_voice = args.voice_health_source == "sidecar" and endpoint.group == "voice"
        problem = (validate_sidecar_voice_url(endpoint.url) if sidecar_voice
                   else validate_target_url(endpoint.url))
        if problem is not None:
            invalid.append(endpoint.endpoint_id)
    if invalid:
        log.say(f"拒绝: 端点 URL 校验失败: {', '.join(invalid)}（fail-closed，零采集）")
        return EXIT_USAGE
    thresholds = Thresholds(
        latency_warn_ms=args.latency_warn_ms, latency_critical_ms=args.latency_critical_ms,
        restart_warn=args.restart_warn, restart_critical=args.restart_critical,
        log_error_warn=args.log_error_warn, log_error_critical=args.log_error_critical,
    )
    clock = RealClock()
    config = build_config(
        project=args.project, profile=DEFAULT_PROFILE, compose_file=COMPOSE_FILE,
        endpoints=endpoints, log_tail=args.log_tail,
        request_timeout_seconds=args.request_timeout_seconds, thresholds=thresholds,
        voice_health_source=args.voice_health_source,
    )
    stamp = clock.stamp()
    # 3) plan 模式（默认）：零 subprocess、零网络、零生产读取
    if not args.execute:
        log.say("=== M14-12 生产监控 PLAN（零 subprocess / 零网络 / 零生产读取） ===")
        log.say(f"compose project: {args.project}（profile {DEFAULT_PROFILE}）")
        for service in STACK_SERVICES:
            log.say(f"受管容器: {container_name(args.project, service)}")
        for endpoint in endpoints:
            log.say(f"端点: {endpoint.endpoint_id} -> {endpoint.url}（组: {endpoint.group}）")
        log.say(f"阈值: {json.dumps(thresholds.as_dict(), ensure_ascii=False)}（全部含边界，warn 恒可见）")
        log.say(f"日志摘要: docker logs --tail {args.log_tail} --timestamps（M14-79：错误计数按"
                "基线时间界记账，仅计数/级别/类别/时间戳面，原文绝不持久化）")
        log.say(f'执行需: --execute --confirm "{CONFIRM_PHRASE}"')
        report = build_report(
            mode="plan", started_utc=clock.utc_now_iso(), ended_utc=clock.utc_now_iso(),
            config=config, collectors=None, threshold_results=None,
            proxy_env_keys_present=proxy_env_present(),
        )
        try:
            json_path, md_path = write_reports_atomic(report, args.artifact_dir, f"plan-{stamp}")
            log.say(f"plan 报告: {json_path.name} / {md_path.name}（{artifact_dir_note(args.artifact_dir)}）")
        except ReportPathError as cause:
            log.say(f"plan 报告路径非法: {type(cause).__name__}")
            return EXIT_USAGE
        except OSError as cause:
            log.say(f"plan 报告写入失败（不影响退出码）: {type(cause).__name__}")
        return EXIT_OK
    # 4) execute 门禁：精确确认短语（缺一即拒，零采集）
    if args.confirm != CONFIRM_PHRASE:
        log.say(f'拒绝: --execute 必配 --confirm "{CONFIRM_PHRASE}"（精确匹配，当前不匹配）——零采集')
        return EXIT_USAGE
    # 5) execute（只读采集；真实执行仅由 supervisor 在获准窗口运行）
    log.say(f"=== M14-12 生产监控 EXECUTE: project={args.project} 端点={len(endpoints)} ===")
    started_utc = clock.utc_now_iso()
    # M14-23/M14-79：解析基线（最新合法 prior 完整工件；纯本地只读
    # fail-safe，零 shell/零网络/零额外生产读取——先于采集解析：restart
    # 增量与日志错误时间界共用同一基线，候选恒为 prior 工件）
    baseline = resolve_restart_baseline(args.artifact_dir)
    baseline_note = baseline.source_stem if baseline.source_stem else baseline.reason
    log.say(f"restart 基线: {baseline.status}（{baseline_note}；"
            f"非法候选跳过 {baseline.invalid_skipped_count}）")
    # M14-79：日志错误时间界 = 基线 collected_at（基线不可用 → None =
    # full-tail 保守回退——方向恒为多计不少计，绝不因界缺失而排除错误行）
    log_window_start = baseline.collected_at if baseline.status == "ok" else None
    window_note = log_window_start if log_window_start else "full-tail（基线不可用，保守回退）"
    log.say(f"日志错误时间界: {window_note}")
    # execute 报告的 config 携带实际时间界（plan 报告恒 None——plan 零读取）
    config = build_config(
        project=args.project, profile=DEFAULT_PROFILE, compose_file=COMPOSE_FILE,
        endpoints=endpoints, log_tail=args.log_tail,
        request_timeout_seconds=args.request_timeout_seconds, thresholds=thresholds,
        voice_health_source=args.voice_health_source,
        log_error_window_start=log_window_start,
    )
    collectors = collect_snapshot(
        runner=ReadonlyRunner(RealRunner()), transport=RealTransport(),
        endpoints=endpoints, project=args.project, profile=DEFAULT_PROFILE,
        compose_file=COMPOSE_FILE, log_tail=args.log_tail,
        request_timeout_seconds=args.request_timeout_seconds,
        log_window_start=log_window_start,
    )
    ended_utc = clock.utc_now_iso()
    results = evaluate_thresholds(collectors, thresholds, endpoints=endpoints,
                                  baseline=baseline)
    report = build_report(
        mode="execute", started_utc=started_utc, ended_utc=ended_utc,
        config=config, collectors=collectors, threshold_results=results,
        proxy_env_keys_present=proxy_env_present(),
    )
    try:
        json_path, md_path = write_reports_atomic(report, args.artifact_dir, f"monitor-{stamp}")
        log.say(f"报告: {json_path.name} / {md_path.name}（{artifact_dir_note(args.artifact_dir)}）")
    except (ReportPathError, OSError) as cause:
        log.say(f"报告写入失败（证据不可失——按拒绝处理）: {type(cause).__name__}")
        return EXIT_USAGE
    for collector_name, collector in collectors.items():
        assert isinstance(collector, dict)
        log.say(f"采集器 {collector_name}: {collector.get('status')}")
    counts = results["counts"]
    assert isinstance(counts, dict)
    log.say(f"阈值计数: ok={counts['ok']} warn={counts['warn']} critical={counts['critical']}")
    for alert in results["alerts"]:
        assert isinstance(alert, dict)
        log.say(f"alert: {alert['check_id']} subject={alert['subject']} "
                f"severity={alert['severity']} {alert['detail']}")
    log.say(f"=== 结果: overall_status={results['overall_status']} "
            f"partial={results['partial']} monitoring_ready={results['monitoring_ready']}（≠ production ready） ===")
    return _exit_code_for(results)


if __name__ == "__main__":
    sys.exit(main())
