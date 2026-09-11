#!/usr/bin/env python
"""M14-11 生产 soak/并发彩排 harness：只读 GET、loopback-only、fail-closed。

设计（与 tools/ops/production_recovery.py 同款纪律：单文件、纯标准库、
零第三方依赖；网络/时钟经注入，测试注入 FakeTransport/FakeClock——开发
回合绝不打真实生产端口，真实执行仅由 supervisor 在获准窗口运行）：

- 双模式：默认 **plan（dry-run）**——只校验限制与目标、打印计划、落
  plan 报告，**零网络请求**；**execute** 需同时满足五要素（缺一即
  EXIT_USAGE 拒绝，零请求）：``--execute`` 显式旗标 +
  ``--confirm "EXECUTE READ-ONLY LOOPBACK SOAK"`` 精确确认短语 +
  有界 duration（≤120s）+ 有界 concurrency（≤8）+ 有界总请求上限
  （≤2000）。所有上限为保守硬顶，fail-closed（超限/非法值在发起任何
  请求之前可见拒绝）。
- 目标面固定（不可经 CLI 指定任意 URL）：Web ``http://127.0.0.1:3011/``
  与 ``/login``、API ``http://127.0.0.1:8000/health``；``--include-voice``
  才加入 FunASR/CosyVoice **低频 GET /health**（8010/8011，默认每目标
  ≥2s 一次，硬顶 ≥1s——绝不发音频/模型推理请求）。
- 网络纪律：GET-only、无认证、无 cookie、无 header/token、无 DB/对象
  写、无房间/会话创建、无 LLM/provider 调用；**仅接受字面 loopback IP**
  （127.0.0.0/8、::1）——主机名一律拒绝（零 DNS 解析），URL 带
  query/fragment/userinfo 一律拒绝。HTTP 客户端用 ``http.client`` 直连
  （该路径从不读取 proxy 环境变量/系统代理——loopback 代理旁路的结构性
  保证，测试以「恶意代理零连接」实证）；仅探测 proxy 环境变量**键名
  是否存在**写入报告注记，值绝不读取/记录。
- 统计：start/end UTC、限制、每目标 requests/success/failure/status
  计数、latency min/p50/p95/p99/max（nearest-rank）、吞吐、错误按安全
  类别归类（仅异常类名 + 类别，绝不记录异常文本/header/body/query/
  凭据/env 值）、deadline 语义（deadline 后零新发；在途请求限于单请求
  超时内完成，计入 ``completed_after_deadline``；部分结果如实入档）。
- 报告：schema 版本化 JSON + Markdown 落 gitignored
  ``.verify/artifacts/m14-11-production-soak/``（原始生产结果绝不入库）。

本工具绝不触碰：容器面、恢复 env、计划任务、FunASR/CosyVoice 进程
状态、代理与无关进程；零子进程执行、零调度器命令（源码契约测试锁定：
上述面在本文件中零字面量出现）。

退出码：0 计划打印成功 / 执行完成且有成功样本；1 执行完成但零成功样本
（栈疑似未起，如实可见）；2 用法/拒绝（确认缺失、限制超顶、目标非法）。

用法（仓库根）：
  python tools/ops/soak_rehearsal.py                      # plan（默认，零网络）
  python tools/ops/soak_rehearsal.py --execute \
      --confirm "EXECUTE READ-ONLY LOOPBACK SOAK" \
      --duration-seconds 60 --concurrency 4 --max-requests 300
"""
from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import math
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = REPO_ROOT / ".verify" / "artifacts" / "m14-11-production-soak"
TAG = "[soak]"
REPORT_SCHEMA_VERSION = 1
MILESTONE = "M14-11"
TOOL_NAME = "tools/ops/soak_rehearsal.py"
USER_AGENT = "aios-m14-11-soak-rehearsal/1.0"

#: execute 五要素之二：精确确认短语（一字不差）
CONFIRM_PHRASE = "EXECUTE READ-ONLY LOOPBACK SOAK"

#: 保守硬顶（fail-closed）——CLI 值超出即 EXIT_USAGE，零请求
MAX_DURATION_SECONDS = 120
MIN_DURATION_SECONDS = 1
MAX_CONCURRENCY = 8
MIN_CONCURRENCY = 1
MAX_TOTAL_REQUESTS = 2000
MIN_TOTAL_REQUESTS = 1
MAX_REQUEST_TIMEOUT_SECONDS = 10.0
MIN_REQUEST_TIMEOUT_SECONDS = 0.5
MIN_PACE_SECONDS = 0.05
MAX_PACE_SECONDS = 5.0
MIN_VOICE_INTERVAL_SECONDS = 1.0
MAX_VOICE_INTERVAL_SECONDS = 60.0

#: 默认值（全部落在硬顶内）
DEFAULT_DURATION_SECONDS = 60
DEFAULT_CONCURRENCY = 4
DEFAULT_TOTAL_REQUESTS = 300
DEFAULT_REQUEST_TIMEOUT_SECONDS = 5.0
DEFAULT_PACE_SECONDS = 0.1
DEFAULT_VOICE_INTERVAL_SECONDS = 2.0

#: 仅探测键名是否存在的 proxy 环境变量（值绝不读取/记录）
PROXY_ENV_KEYS: tuple[str, ...] = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
)


# ---------------------------------------------------------------- 目标面


@dataclass(frozen=True)
class Target:
    """固定目标画像成员（不可经 CLI 注入任意 URL）。"""

    target_id: str
    url: str
    group: str


WEB_ROOT = Target("web-root", "http://127.0.0.1:3011/", "web")
WEB_LOGIN = Target("web-login", "http://127.0.0.1:3011/login", "web")
API_HEALTH = Target("api-health", "http://127.0.0.1:8000/health", "api")
#: 语音面仅低频 GET /health（8010/8011）——绝不音频/推理请求
VOICE_TARGETS: tuple[Target, ...] = (
    Target("funasr-health", "http://127.0.0.1:8010/health", "voice"),
    Target("cosyvoice-health", "http://127.0.0.1:8011/health", "voice"),
)
BASE_TARGETS: tuple[Target, ...] = (WEB_ROOT, WEB_LOGIN, API_HEALTH)


def validate_target_url(url: str) -> str | None:
    """fail-closed 校验：http-only、字面 loopback IP、显式端口、
    无 query/fragment/userinfo。主机名一律拒绝（零 DNS 解析——防
    hosts 漂移把 loopback 名字指向外部）。返回拒绝原因或 None（放行）。"""
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
    path = parts.path
    if not path or not path.startswith("/"):
        return "bad-path"
    return None


def select_targets(include_voice: bool, only: list[str] | None) -> tuple[list[Target], str | None]:
    """按旗标选出固定画像子集；``--only`` 仅能在已启用集合内筛选。"""
    enabled = list(BASE_TARGETS) + (list(VOICE_TARGETS) if include_voice else [])
    if not only:
        return enabled, None
    known = {t.target_id for t in enabled}
    unknown = sorted(set(only) - known)
    if unknown:
        return [], f"unknown-or-disabled-target: {', '.join(unknown)}（可选: {', '.join(sorted(known))}）"
    return [t for t in enabled if t.target_id in set(only)], None


# ---------------------------------------------------------------- 限制


@dataclass(frozen=True)
class SoakLimits:
    duration_seconds: float
    concurrency: int
    max_requests: int
    request_timeout_seconds: float
    pace_seconds: float
    voice_min_interval_seconds: float

    def as_dict(self) -> dict[str, object]:
        return {
            "duration_seconds": self.duration_seconds,
            "concurrency": self.concurrency,
            "max_requests": self.max_requests,
            "request_timeout_seconds": self.request_timeout_seconds,
            "pace_seconds": self.pace_seconds,
            "voice_min_interval_seconds": self.voice_min_interval_seconds,
        }


def validate_limits(duration: float, concurrency: int, max_requests: int,
                    request_timeout: float, pace: float,
                    voice_interval: float) -> list[str]:
    """纯函数：返回违规清单（空 = 放行）。所有硬顶 fail-closed。"""
    problems: list[str] = []
    if not MIN_DURATION_SECONDS <= duration <= MAX_DURATION_SECONDS:
        problems.append(f"duration-seconds 必须在 {MIN_DURATION_SECONDS}-{MAX_DURATION_SECONDS}s（收到 {duration}）")
    if not MIN_CONCURRENCY <= concurrency <= MAX_CONCURRENCY:
        problems.append(f"concurrency 必须在 {MIN_CONCURRENCY}-{MAX_CONCURRENCY}（收到 {concurrency}）")
    if not MIN_TOTAL_REQUESTS <= max_requests <= MAX_TOTAL_REQUESTS:
        problems.append(f"max-requests 必须在 {MIN_TOTAL_REQUESTS}-{MAX_TOTAL_REQUESTS}（收到 {max_requests}）")
    if not MIN_REQUEST_TIMEOUT_SECONDS <= request_timeout <= MAX_REQUEST_TIMEOUT_SECONDS:
        problems.append(
            f"request-timeout-seconds 必须在 {MIN_REQUEST_TIMEOUT_SECONDS}-{MAX_REQUEST_TIMEOUT_SECONDS}（收到 {request_timeout}）"
        )
    if not MIN_PACE_SECONDS <= pace <= MAX_PACE_SECONDS:
        problems.append(f"pace-seconds 必须在 {MIN_PACE_SECONDS}-{MAX_PACE_SECONDS}（收到 {pace}）")
    if not MIN_VOICE_INTERVAL_SECONDS <= voice_interval <= MAX_VOICE_INTERVAL_SECONDS:
        problems.append(
            f"voice-min-interval-seconds 必须在 {MIN_VOICE_INTERVAL_SECONDS}-{MAX_VOICE_INTERVAL_SECONDS}（收到 {voice_interval}）"
        )
    return problems


# ---------------------------------------------------------------- transport（注入点）


@dataclass(frozen=True)
class HttpResult:
    """单请求结果：状态码或安全错误类别（仅类别 + 异常类名，无文本）。"""

    status: int | None
    elapsed_ms: float
    error_category: str | None
    error_class: str | None


class Transport(Protocol):
    def get(self, host: str, port: int, path: str, *, timeout: float) -> HttpResult: ...


def categorize_exception(exc: BaseException) -> tuple[str, str]:
    """异常 → （安全类别, 异常类名）。绝不保留 str(exc) 文本。"""
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
            category, klass = categorize_exception(exc)
            return HttpResult(None, elapsed_ms, category, klass)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return HttpResult(status, elapsed_ms, None, None)


# ---------------------------------------------------------------- clock（注入点）


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class RealClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


# ---------------------------------------------------------------- 引擎


@dataclass(frozen=True)
class Sample:
    target_id: str
    issued_at: float  # monotonic
    result: HttpResult


@dataclass(frozen=True)
class RunOutcome:
    stopped_reason: str
    issued_total: int
    samples: list[Sample]
    started_monotonic: float
    ended_monotonic: float
    deadline_monotonic: float


def run_soak(*, transport: Transport, targets: list[Target], limits: SoakLimits,
             clock: Clock, log: SafeLog) -> RunOutcome:
    """并发彩排引擎：N worker 轮转固定目标；deadline 后零新发、在途请求
    限于单请求超时；语音目标按最小间隔门控。返回部分/全部结果。"""
    endpoints = {t.target_id: (urlsplit(t.url).hostname or "", urlsplit(t.url).port or 80,
                               urlsplit(t.url).path) for t in targets}
    deadline = clock.monotonic() + limits.duration_seconds
    started = clock.monotonic()
    lock = threading.Lock()
    state = {"issued": 0}
    samples: list[Sample] = []
    last_voice_issue: dict[str, float] = {}

    def worker(slot: int) -> None:
        cursor = slot
        while True:
            with lock:
                now = clock.monotonic()
                if now >= deadline or state["issued"] >= limits.max_requests:
                    return
                chosen: Target | None = None
                issued_at = 0.0
                for offset in range(len(targets)):
                    candidate = targets[(cursor + offset) % len(targets)]
                    if candidate.group == "voice":
                        last = last_voice_issue.get(candidate.target_id)
                        if last is not None and (now - last) < limits.voice_min_interval_seconds:
                            continue  # 语音目标未到最小间隔——换下一个
                    chosen = candidate
                    if candidate.group == "voice":
                        last_voice_issue[candidate.target_id] = now
                    break
                if chosen is not None:
                    state["issued"] += 1
                    issued_at = now
            cursor += 1
            if chosen is None:
                clock.sleep(min(0.05, limits.voice_min_interval_seconds))
                continue
            host, port, path = endpoints[chosen.target_id]
            try:
                result = transport.get(host, port, path, timeout=limits.request_timeout_seconds)
            except Exception as exc:  # noqa: BLE001 —— transport 自身缺陷兜底
                category, klass = categorize_exception(exc)
                result = HttpResult(None, 0.0, category, klass)
            with lock:
                samples.append(Sample(chosen.target_id, issued_at, result))
            clock.sleep(limits.pace_seconds)

    threads = [threading.Thread(target=worker, args=(i,), name=f"soak-worker-{i}", daemon=True)
               for i in range(limits.concurrency)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()  # worker 必然退出：deadline 检查 + 单请求超时有界
    ended = clock.monotonic()
    reasons: list[str] = []
    if state["issued"] >= limits.max_requests:
        reasons.append("max-requests")
    if ended >= deadline:
        reasons.append("duration-deadline")
    stopped = "+".join(reasons) if reasons else "completed"
    log.say(f"引擎结束: issued={state['issued']} 样本={len(samples)} 停止原因={stopped}")
    return RunOutcome(stopped, state["issued"], samples, started, ended, deadline)


# ---------------------------------------------------------------- 统计


def percentile(sorted_values: list[float], fraction: float) -> float:
    """nearest-rank 百分位（sorted 升序输入，非空）。"""
    rank = max(1, math.ceil(fraction * len(sorted_values)))
    return sorted_values[min(rank, len(sorted_values)) - 1]


def summarize_target(target: Target, target_samples: list[Sample],
                     wall_seconds: float) -> dict[str, object]:
    """单目标统计（纯函数）：计数、status 分布、latency 分位、吞吐、错误类别。"""
    requests = len(target_samples)
    success = sum(1 for s in target_samples if s.result.status is not None and 200 <= s.result.status < 400)
    failure = requests - success
    status_counts: dict[str, int] = {}
    error_counts: dict[str, int] = {}
    latencies: list[float] = []
    for sample in target_samples:
        if sample.result.status is not None:
            key = str(sample.result.status)
            status_counts[key] = status_counts.get(key, 0) + 1
        if sample.result.error_category is not None:
            error_counts[sample.result.error_category] = error_counts.get(sample.result.error_category, 0) + 1
        latencies.append(sample.result.elapsed_ms)
    if latencies:
        ordered = sorted(latencies)
        latency_ms: dict[str, float] = {
            "min": ordered[0],
            "p50": percentile(ordered, 0.50),
            "p95": percentile(ordered, 0.95),
            "p99": percentile(ordered, 0.99),
            "max": ordered[-1],
        }
    else:
        latency_ms = {"min": None, "p50": None, "p95": None, "p99": None, "max": None}  # type: ignore[dict-item]
    throughput = round(requests / wall_seconds, 3) if wall_seconds > 0 else 0.0
    return {
        "url": target.url,
        "group": target.group,
        "requests": requests,
        "success": success,
        "failure": failure,
        "status_counts": status_counts,
        "error_counts": error_counts,
        "latency_ms": latency_ms,
        "throughput_rps": throughput,
    }


# ---------------------------------------------------------------- 脱敏（防御性）


#: 常见凭据形态 → 占位符（写任何文件/日志前的最终防线；本工具按构造
#: 不产生凭据文本，此层为纵深防御——测试以标记值锁定）
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


# ---------------------------------------------------------------- 报告


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_report(*, mode: str, started_utc: str, ended_utc: str, wall_seconds: float,
                 limits: SoakLimits, targets: list[Target], samples: list[Sample],
                 stopped_reason: str, deadline_monotonic: float,
                 started_monotonic: float, proxy_env_present: bool) -> dict[str, object]:
    """schema 版本化报告（纯数据；写盘前再经 redact_secrets）。"""
    wall = wall_seconds if wall_seconds > 0 else 0.0
    per_target: dict[str, object] = {}
    for target in targets:
        target_samples = [s for s in samples if s.target_id == target.target_id]
        per_target[target.target_id] = summarize_target(target, target_samples, wall)
    completed_after_deadline = sum(
        1 for s in samples
        if s.issued_at + s.result.elapsed_ms / 1000.0 > deadline_monotonic
    )
    total_requests = len(samples)
    total_success = sum(1 for s in samples
                        if s.result.status is not None and 200 <= s.result.status < 400)
    all_latencies = sorted(s.result.elapsed_ms for s in samples)
    if all_latencies:
        total_latency: dict[str, float] = {
            "min": all_latencies[0],
            "p50": percentile(all_latencies, 0.50),
            "p95": percentile(all_latencies, 0.95),
            "p99": percentile(all_latencies, 0.99),
            "max": all_latencies[-1],
        }
    else:
        total_latency = {"min": None, "p50": None, "p95": None, "p99": None, "max": None}  # type: ignore[dict-item]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "milestone": MILESTONE,
        "mode": mode,
        "started_at_utc": started_utc,
        "ended_at_utc": ended_utc,
        "wall_seconds": round(wall, 3),
        "limits": limits.as_dict(),
        "targets_selected": [t.target_id for t in targets],
        "stopped_reason": stopped_reason,
        "issued_total": total_requests,
        "completed_after_deadline": completed_after_deadline,
        "totals": {
            "requests": total_requests,
            "success": total_success,
            "failure": total_requests - total_success,
            "latency_ms": total_latency,
            "throughput_rps": round(total_requests / wall, 3) if wall > 0 else 0.0,
        },
        "per_target": per_target,
        "notes": [
            "read-only GET, unauthenticated, loopback-literal-only, no cookies/tokens/mutations",
            "http.client direct connection: proxy env never consulted (loopback proxy bypass)",
            f"proxy_env_keys_present={proxy_env_present}（仅键名存在性，值绝不读取/记录）",
            "no headers/bodies/query-strings/credentials/env-values recorded",
        ],
    }


def _fmt_latency(value: object) -> str:
    return "-" if value is None else f"{value:.1f}"


def render_markdown(report: dict[str, object]) -> str:
    """Markdown 摘要（固定词汇表 + 渲染后统一脱敏）。"""
    limits = report["limits"]
    assert isinstance(limits, dict)
    lines: list[str] = [
        f"# {report['milestone']} soak 彩排报告（mode={report['mode']}）",
        "",
        f"- 工具：`{report['tool']}`（schema_version={report['schema_version']}）",
        (f"- 开始（UTC）：{report['started_at_utc']}；结束（UTC）：{report['ended_at_utc']}；"
         f"墙钟 {report['wall_seconds']}s"),
        (f"- 限制：duration={limits['duration_seconds']}s concurrency={limits['concurrency']} "
         f"max_requests={limits['max_requests']} request_timeout={limits['request_timeout_seconds']}s "
         f"pace={limits['pace_seconds']}s voice_interval={limits['voice_min_interval_seconds']}s"),
        (f"- 停止原因：{report['stopped_reason']}；issued_total={report['issued_total']}；"
         f"completed_after_deadline={report['completed_after_deadline']}"),
        "",
        "| 目标 | 组 | requests | success | failure | p50(ms) | p95(ms) | p99(ms) | max(ms) | rps |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    per_target = report["per_target"]
    assert isinstance(per_target, dict)
    for target_id, raw in per_target.items():
        stats = raw if isinstance(raw, dict) else {}
        latency = stats.get("latency_ms") if isinstance(stats.get("latency_ms"), dict) else {}
        assert isinstance(latency, dict)
        lines.append(
            f"| {target_id} | {stats.get('group', '-')} | {stats.get('requests', 0)} "
            f"| {stats.get('success', 0)} | {stats.get('failure', 0)} | {_fmt_latency(latency.get('p50'))} "
            f"| {_fmt_latency(latency.get('p95'))} | {_fmt_latency(latency.get('p99'))} "
            f"| {_fmt_latency(latency.get('max'))} | {stats.get('throughput_rps', 0)} |"
        )
    totals = report["totals"]
    assert isinstance(totals, dict)
    lines += [
        "",
        (f"- 汇总：requests={totals['requests']} success={totals['success']} "
         f"failure={totals['failure']} throughput={totals['throughput_rps']} rps"),
        "",
        "注记：",
    ]
    lines += [f"- {note}" for note in report["notes"]]  # type: ignore[arg-type]
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, object], directory: Path, stem: str) -> tuple[Path, Path]:
    """JSON + Markdown 双写（内容先经 redact_secrets 终防线）。"""
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{stem}.json"
    md_path = directory / f"{stem}.md"
    json_path.write_text(redact_secrets(json.dumps(report, ensure_ascii=False, indent=2)),
                         encoding="utf-8", newline="\n")
    md_path.write_text(redact_secrets(render_markdown(report)), encoding="utf-8", newline="\n")
    return json_path, md_path


# ---------------------------------------------------------------- CLI


def proxy_env_present() -> bool:
    """仅探测 proxy 环境变量键名是否存在（键名入注记，值绝不读取）。"""
    return any(os.environ.get(key) for key in PROXY_ENV_KEYS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="soak_rehearsal.py",
        description="M14-11 生产 soak/并发彩排 harness（默认 plan 零网络；execute 需旗标+精确确认+有界限制）",
    )
    parser.add_argument("--execute", action="store_true",
                        help="真实执行（默认 plan：零网络请求）")
    parser.add_argument("--confirm", default="",
                        help=f'execute 必配 --confirm "{CONFIRM_PHRASE}"（精确匹配）')
    parser.add_argument("--duration-seconds", type=float, default=DEFAULT_DURATION_SECONDS,
                        help=f"有界时长 {MIN_DURATION_SECONDS}-{MAX_DURATION_SECONDS}s（默认 {DEFAULT_DURATION_SECONDS}）")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                        help=f"有界并发 {MIN_CONCURRENCY}-{MAX_CONCURRENCY}（默认 {DEFAULT_CONCURRENCY}）")
    parser.add_argument("--max-requests", type=int, default=DEFAULT_TOTAL_REQUESTS,
                        help=f"总请求硬顶 {MIN_TOTAL_REQUESTS}-{MAX_TOTAL_REQUESTS}（默认 {DEFAULT_TOTAL_REQUESTS}）")
    parser.add_argument("--request-timeout-seconds", type=float, default=DEFAULT_REQUEST_TIMEOUT_SECONDS,
                        help=f"单请求超时 {MIN_REQUEST_TIMEOUT_SECONDS}-{MAX_REQUEST_TIMEOUT_SECONDS}s（默认 {DEFAULT_REQUEST_TIMEOUT_SECONDS}）")
    parser.add_argument("--pace-seconds", type=float, default=DEFAULT_PACE_SECONDS,
                        help=f"worker 节拍 {MIN_PACE_SECONDS}-{MAX_PACE_SECONDS}s（默认 {DEFAULT_PACE_SECONDS}；"
                             f"聚合速率上限 = concurrency/pace）")
    parser.add_argument("--include-voice", action="store_true",
                        help="加入 FunASR/CosyVoice 低频 GET /health（8010/8011，默认关闭）")
    parser.add_argument("--voice-min-interval-seconds", type=float, default=DEFAULT_VOICE_INTERVAL_SECONDS,
                        help=f"语音目标最小间隔 {MIN_VOICE_INTERVAL_SECONDS}-{MAX_VOICE_INTERVAL_SECONDS}s（默认 {DEFAULT_VOICE_INTERVAL_SECONDS}）")
    parser.add_argument("--only", action="append", metavar="TARGET_ID",
                        help="仅打到指定目标（可重复；候选见 plan 输出）")
    parser.add_argument("--artifact-dir", type=Path, default=ARTIFACT_DIR,
                        help="报告目录（默认 .verify/artifacts/m14-11-production-soak，gitignored）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log = SafeLog()
    # 1) 限制校验（plan 与 execute 都校验；超顶零请求拒绝）
    problems = validate_limits(args.duration_seconds, args.concurrency, args.max_requests,
                               args.request_timeout_seconds, args.pace_seconds,
                               args.voice_min_interval_seconds)
    if problems:
        for problem in problems:
            log.say(f"拒绝: {problem}")
        return EXIT_USAGE
    # 2) 目标面（固定画像；--only 仅限已启用集合）
    targets, target_error = select_targets(args.include_voice, args.only)
    if target_error is not None:
        log.say(f"拒绝: {target_error}")
        return EXIT_USAGE
    invalid = [t.target_id for t in targets if validate_target_url(t.url) is not None]
    if invalid:
        log.say(f"拒绝: 目标 URL 校验失败: {', '.join(invalid)}（fail-closed，零请求）")
        return EXIT_USAGE
    limits = SoakLimits(
        duration_seconds=args.duration_seconds,
        concurrency=args.concurrency,
        max_requests=args.max_requests,
        request_timeout_seconds=args.request_timeout_seconds,
        pace_seconds=args.pace_seconds,
        voice_min_interval_seconds=args.voice_min_interval_seconds,
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    # 3) plan 模式（默认）：零网络，打印计划并落 plan 报告
    if not args.execute:
        log.say("=== M14-11 soak 彩排 PLAN（零网络请求） ===")
        for target in targets:
            log.say(f"目标: {target.target_id} -> {target.url}（组: {target.group}）")
        log.say(f"限制: {json.dumps(limits.as_dict(), ensure_ascii=False)}")
        log.say(f'执行需: --execute --confirm "{CONFIRM_PHRASE}" + 有界限制（硬顶 duration≤{MAX_DURATION_SECONDS}s '
                f"concurrency≤{MAX_CONCURRENCY} max-requests≤{MAX_TOTAL_REQUESTS}）")
        report = build_report(
            mode="plan", started_utc=utc_now_iso(), ended_utc=utc_now_iso(), wall_seconds=0.0,
            limits=limits, targets=targets, samples=[], stopped_reason="plan-only",
            deadline_monotonic=0.0, started_monotonic=0.0, proxy_env_present=proxy_env_present(),
        )
        try:
            json_path, md_path = write_report(report, args.artifact_dir, f"plan-{stamp}")
            log.say(f"plan 报告: {json_path.name} / {md_path.name}（目录 gitignored，不入库）")
        except OSError as cause:
            log.say(f"plan 报告写入失败（不影响退出码）: {type(cause).__name__}")
        return EXIT_OK
    # 4) execute 门禁：精确确认短语（缺一即拒，零请求）
    if args.confirm != CONFIRM_PHRASE:
        log.say(f'拒绝: --execute 必配 --confirm "{CONFIRM_PHRASE}"（精确匹配，当前不匹配）——零请求')
        return EXIT_USAGE
    # 5) execute（真实网络，仅 loopback 固定画像；由 supervisor 在获准窗口运行）
    log.say(f"=== M14-11 soak 彩排 EXECUTE: duration={limits.duration_seconds}s "
            f"concurrency={limits.concurrency} max-requests={limits.max_requests} "
            f"voice={'on' if any(t.group == 'voice' for t in targets) else 'off'} ===")
    started_utc = utc_now_iso()
    outcome = run_soak(transport=RealTransport(), targets=targets, limits=limits,
                       clock=RealClock(), log=log)
    ended_utc = utc_now_iso()
    report = build_report(
        mode="execute", started_utc=started_utc, ended_utc=ended_utc,
        wall_seconds=max(0.0, outcome.ended_monotonic - outcome.started_monotonic),
        limits=limits, targets=targets, samples=outcome.samples,
        stopped_reason=outcome.stopped_reason, deadline_monotonic=outcome.deadline_monotonic,
        started_monotonic=outcome.started_monotonic, proxy_env_present=proxy_env_present(),
    )
    try:
        json_path, md_path = write_report(report, args.artifact_dir, f"soak-{stamp}")
        log.say(f"报告: {json_path.name} / {md_path.name}（目录 gitignored，不入库）")
    except OSError as cause:
        log.say(f"报告写入失败（执行结果已在上方如实输出）: {type(cause).__name__}")
    totals = report["totals"]
    assert isinstance(totals, dict)
    log.say(f"=== 结果: requests={totals['requests']} success={totals['success']} "
            f"failure={totals['failure']} 停止={report['stopped_reason']} ===")
    if totals["success"] == 0:
        log.say("零成功样本——栈疑似未起（可见失败，交人工）")
        return EXIT_ERROR
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
