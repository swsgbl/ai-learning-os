#!/usr/bin/env python
"""M14-119 监控告警外发分发最小闭环：既有 production_monitor JSON 报告 →
验证 → 判定 → 单一通用 HTTPS webhook 分发 + sanitized 分发台账。

设计（与 tools/ops/production_monitor.py、monitoring_history.py 同款纪律：
单文件、纯标准库、零第三方依赖；Store/Transport/Clock 全注入——开发回合
零真实网络、零真实分发，全部行为用 fake/stub 测试锁定；真实外发仅由
supervisor 在获准窗口运行）：

- **双模式**：默认 **plan/validate**——零网络、零分发（Transport 绝不
  构造），读取并严格校验既有 monitor 报告 + 判定是否会分发 + 校验
  secret 文件与 webhook URL（全部本地只读）；**execute** 需同时满足
  「``--execute`` + ``--confirm "EXECUTE MONITOR ALERT DISPATCH"``（一字
  不差）+ ``--secret-file`` 操作者提供的 JSON secret 文件」，缺一即
  EXIT 2 且零 Transport 构造（fail-closed）。
- **只消费权威 schema，绝不重判健康**：报告身份/告警面复用同仓
  ``monitoring_history``（M14-13）单一事实源常量（schema_version/tool/
  milestone/时间戳/stem 白名单）；本工具不重复任何阈值、不重分类健康
  ——仅校验报告自洽（counts ↔ alerts、overall ↔ counts/partial）后在
  **既有 alerts 之上**决定「分发与否」：存在 warn/critical alert 或
  ``overall_status=incomplete`` 才分发；ok 且零告警 → skipped-no-alerts
  （成功路径，exit 0，零发送）。
- **单一通用 HTTPS webhook sink**：endpoint URL 与可选 Bearer token 恒
  来自操作者 secret 文件（JSON ``{"url": ..., "token": ...}``，gitignored
  由操作者负责），绝不来自代码/git/evidence/日志/测试 fixture；URL
  fail-closed 校验（``validate_webhook_url``）：生产恒 https；http 仅经
  显式 ``--allow-loopback-http`` test-only 旗标放行且**仅限字面回环 IP**；
  字面私网（RFC1918）/链路本地/未指定/组播/保留 IP 与 ``localhost``
  名称一律拒绝（SSRF 收紧面）；userinfo/query/fragment/坏端口拒绝；
  **URL 与 token 绝不回显、绝不入任何输出**（stdout/报告/台账/payload）。
- **payload 版本化 + 固定词汇 + 有界**：出站 JSON 仅含 schema/tool/
  milestone/event/dispatched_at + 报告身份（白名单 stem + SHA-256 +
  canonical collected_at）与状态摘要（overall_status/partial/counts/
  ``check_id:subject:severity`` 形态 alert codes，≤64 条、超界截断计数
  显式）。**绝无**原始日志/env 值/端点/token/容器体/DB URL/绝对本地
  路径/threshold detail 文本。编码后字节数硬顶复检。
- **fail-closed**：malformed 报告（身份/时间戳/类型/counts-alerts 不自
  洽/overall 不自洽/字段越界）、缺失/oversize/invalid-JSON secret、
  unsafe URL、webhook 非 2xx/超时/连接异常、输出路径 symlink/防碰撞、
  台账 malformed/字段非法/重复分发（同报告 SHA-256 已 sent → 拒绝且零
  发送）一律可见拒绝；**失败的分发恒可见（dispatch_status=failed）且绝
  不入台账、绝不报告为 sent**；台账写入失败时发送已发生的事实照实入档
  （dispatch_status=sent-ledger-unrecorded）。
- **sanitized 分发台账（幂等 + 审计）**：``dispatch-ledger.jsonl``（固定
  名，gitignored 工件目录）仅在成功分发后原子追加（整读 + 追加 + tmp+
  fsync+os.replace 重写）；行 schema 版本化 + 固定词汇；同 report
  SHA-256 重复分发/重复记账一律 fail-closed 拒绝。每轮另落
  ``dispatch-<stamp>-<随机后缀>.json/.md`` 报告（存在性探测 + 递增换名，
  绝不覆盖既有工件）。
- 首片刻意最小：**单 sink、单次发送、零重试**（绝无 retry storm/退避/
  队列）、无 SMTP/Alertmanager/UI/调度集成。退出码：0 plan / execute
  成功（含 skipped-no-alerts）；2 一切拒绝（含分发失败）。本工具不构成
  production readiness 宣称，``production_ready=false`` 不变。

用法（仓库根）：
  python tools/ops/monitoring_alert_dispatch.py --report <monitor-*.json> \
      [--secret-file <secret.json>]              # plan（默认，零网络零分发）
  python tools/ops/monitoring_alert_dispatch.py --report <monitor-*.json> \
      --secret-file <secret.json> --execute \
      --confirm "EXECUTE MONITOR ALERT DISPATCH"  # execute（真实外发）
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

try:  # 直接脚本运行：tools/ops 自身在 sys.path
    import monitoring_history as _history
    import production_monitor as _monitor
except ImportError:  # 经 importlib 按路径加载（契约测试形态）
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import monitoring_history as _history
    import production_monitor as _monitor

EXIT_OK = 0
#: 2 = 一切 fail-closed 拒绝（与监控家族统一可见拒绝口径）
EXIT_REFUSED = 2

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = REPO_ROOT / ".verify" / "artifacts" / "m14-119-monitor-alert-dispatch"
LEDGER_NAME = "dispatch-ledger.jsonl"

TAG = "[alert-dispatch]"
DISPATCH_SCHEMA_VERSION = 1   # dispatch 报告 schema
PAYLOAD_SCHEMA_VERSION = 1    # 出站 payload schema
LEDGER_SCHEMA_VERSION = 1     # 台账行 schema
TOOL_NAME = "tools/ops/monitoring_alert_dispatch.py"
MILESTONE = "M14-119"
USER_AGENT = "aios-m14-119-monitor-alert-dispatch/1.0"
CONFIRM_PHRASE = "EXECUTE MONITOR ALERT DISPATCH"

#: 报告契约单一事实源 = 同仓 M14-13 工具（零平行 schema）
MONITOR_TOOL_NAME = _history.MONITOR_TOOL_NAME
EXPECTED_MONITOR_SCHEMA_VERSION = _history.EXPECTED_MONITOR_SCHEMA_VERSION
EXPECTED_MILESTONE = _history.EXPECTED_MILESTONE
ARTIFACT_STEM_RE = _history.ARTIFACT_STEM_RE
TIMESTAMP_FORMAT = _history.TIMESTAMP_FORMAT

#: 单一通用 HTTPS webhook sink（本切片唯一 sink 种类；URL/token 来自操作者 secret）
SINK_KIND = "generic-https-webhook"

#: 分发判定：alerts（warn/critical）非空或 overall_status=incomplete
ALERT_SEVERITIES = frozenset({"warn", "critical"})
INCOMPLETE_STATUS = "incomplete"
OVERALL_STATUSES = frozenset({"ok", "warn", "critical", INCOMPLETE_STATUS})

#: 有界（超界 fail-closed，绝不静默截断语义）
MAX_ALERT_FIELD_LENGTH = 64
MAX_ALERT_CODES = 64
MAX_PAYLOAD_BYTES = 16384
MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_SECRET_FILE_BYTES = 65536
MAX_SECRET_TOKEN_LENGTH = 4096

#: webhook 请求超时（秒）
MIN_TIMEOUT_SECONDS = 0.5
MAX_TIMEOUT_SECONDS = 30.0
DEFAULT_TIMEOUT_SECONDS = 10.0

#: 报告边界注记（固定词汇表：绝不包含 URL/token/路径等任何敏感面）
REPORT_BOUNDARIES: tuple[str, ...] = (
    "consumes existing production_monitor JSON reports only; thresholds and health classification are never re-evaluated",
    "dispatch triggers only when the report genuinely has warn/critical alerts or an incomplete overall status",
    "single generic HTTPS webhook sink; endpoint URL and optional bearer token come solely from an operator-supplied secret file and are never echoed or persisted",
    "outbound payload is versioned, bounded, fixed-vocabulary: summary counts, statuses, alert codes and report identity hashes only; never raw logs, env values, endpoints, tokens, container bodies, DB URLs or absolute local paths",
    "plain http is rejected unless the explicit loopback test-only flag is set, and then only for literal loopback IPs",
    "failed dispatches are visible (dispatch_status=failed), never recorded in the ledger and never reported as sent",
    "the sanitized dispatch ledger is appended only after a successful dispatch and rejects duplicate dispatches of the same report hash (idempotency)",
    "first slice is deliberately minimal: single sink, single attempt, no retries, no SMTP/Alertmanager/UI/scheduler integration",
    "no real external endpoint was contacted during development; production_ready remains false and this tool never claims production readiness",
)


class DispatchError(RuntimeError):
    """fail-closed 拒绝（固定词汇原因；绝不携带 URL/token/文件内容文本）。"""


# ---------------------------------------------------------------- Store（复用单一事实源）

#: I/O 面复用同仓 M14-13 Store 协议与真实实现（exists/is_symlink/list_dir/
#: read_bytes/mkdirs/write_atomic——同目录 tmp+fsync+os.replace 原子写）
Store = _history.Store
RealStore = _history.RealStore
reject_symlinked_path = _history.reject_symlinked_path
redact_secrets = _monitor.redact_secrets


class SafeLog:
    """行式日志：stdout 输出前经 redact_secrets 防御性脱敏终防线。"""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def say(self, message: str) -> None:
        line = redact_secrets(message)
        self.lines.append(line)
        print(f"{TAG} {line}", flush=True)


# ---------------------------------------------------------------- Clock（注入点）


class Clock(Protocol):
    def utc_now_iso(self) -> str: ...

    def stamp(self) -> str: ...

    def token_hex(self, nibbles: int) -> str: ...


class RealClock:
    def utc_now_iso(self) -> str:
        return _monitor.RealClock().utc_now_iso()

    def stamp(self) -> str:
        return _monitor.RealClock().stamp()

    def token_hex(self, nibbles: int) -> str:
        import secrets
        return secrets.token_hex(nibbles)


# ---------------------------------------------------------------- webhook URL（fail-closed）


def validate_webhook_url(url: str, *, allow_loopback_http: bool = False) -> str | None:
    """纯函数：webhook URL fail-closed 校验。返回拒绝原因（固定词汇）或 None。

    - 生产恒 ``https``；``http`` 仅经显式 test-only 旗标放行且**仅限字面
      回环 IP**（私网/链路本地/公网 IP 与 DNS 名称仍拒）；
    - 字面 IP 面（https）：回环/RFC1918 私网/链路本地/未指定/组播/保留
      地址一律拒绝（SSRF 收紧面）；
    - ``localhost`` 名称恒拒（零 DNS 解析面）；其余 DNS 主机名接受——
      **DNS 解析后落私网的面不在本切片防护范围（诚实边界，见 README）**；
    - userinfo/query/fragment/坏端口/无 host 一律拒绝。
    """
    parts = urlsplit(url)
    scheme_allowed = parts.scheme == "https"
    if parts.scheme == "http" and allow_loopback_http:
        scheme_allowed = True
    if not scheme_allowed:
        return "scheme-not-https"
    host = parts.hostname
    if host is None:
        return "no-host"
    try:
        port = parts.port
    except ValueError:
        return "bad-port"
    if port is not None and not 1 <= port <= 65535:
        return "bad-port"
    if parts.username is not None or parts.password is not None:
        return "userinfo-present"
    if parts.query or parts.fragment:
        return "query-or-fragment"
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        addr = None
    if parts.scheme == "http":
        # test-only 豁免面：仅字面回环 IP；DNS 名称/私网/公网 IP 恒不豁免
        if addr is None or not addr.is_loopback:
            return "host-not-loopback"
        return None
    if addr is None:
        if host == "localhost":
            return "host-localhost-name"
        return None  # DNS 主机名（边界：不做解析 pin，见模块 docstring）
    if (addr.is_loopback or addr.is_private or addr.is_link_local
            or addr.is_unspecified or addr.is_multicast or addr.is_reserved):
        return "host-not-public-ip"
    return None


# ---------------------------------------------------------------- secret 文件（操作者提供）


def load_webhook_secret(store: Store, path: Path) -> tuple[dict[str, str] | None, str | None]:
    """读取并严格校验操作者 secret 文件（{"url": str, "token"?: str}）。

    成功 → ({"url", "token"|None}, None)；任何失败 → (None, 固定安全
    类别)——**URL/token 值绝不回显**。symlink（自身+现存祖先）、缺失、
    非常规文件、大小硬顶双检（stat 预检 + 读后复检）、invalid JSON、
    非对象、缺 url、空 url、未知键、token 空串/超长一律拒绝。"""
    try:
        reject_symlinked_path(store, path)
    except _history.HistoryError as cause:
        return None, f"secret-file-{cause}"
    if not store.exists(path):
        return None, "secret-file-missing"
    try:
        if path.stat().st_size > MAX_SECRET_FILE_BYTES:
            return None, "secret-file-oversize"
        data = store.read_bytes(path)
    except OSError:
        return None, "secret-file-unreadable"
    if len(data) > MAX_SECRET_FILE_BYTES:
        return None, "secret-file-oversize"
    try:
        payload = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, "secret-file-invalid-json"
    if not isinstance(payload, dict):
        return None, "secret-file-invalid-json"
    unknown = set(payload) - {"url", "token"}
    if unknown:
        return None, "secret-unknown-key"
    url = payload.get("url")
    if not isinstance(url, str) or not url:
        return None, "secret-url-missing"
    token = payload.get("token")
    if token is not None:
        if not isinstance(token, str) or not token:
            return None, "secret-token-invalid"
        if len(token) > MAX_SECRET_TOKEN_LENGTH:
            return None, "secret-token-invalid"
    return {"url": url, "token": token}, None


# ---------------------------------------------------------------- 报告读取 + 校验（纯）


def _valid_canonical_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        _history.parse_timestamp(value)
    except _history.HistoryError:
        return False
    return True


def _is_nonneg_int(value: object) -> bool:
    """bool 不是 int（计数校验；与 monitoring_history 同口径的类型判断）。"""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def load_report(store: Store, path: Path) -> tuple[tuple[str, str, dict] | None, str | None]:
    """读取既有 monitor JSON 报告。成功 → ((stem, sha256, data), None)；
    失败 → (None, 固定类别)。stem 白名单 + symlink 防御 + 大小硬顶双检。"""
    name = path.name
    if not name.endswith(".json") or ARTIFACT_STEM_RE.match(name[: -len(".json")]) is None:
        return None, "report-stem-invalid"
    try:
        reject_symlinked_path(store, path)
    except _history.HistoryError as cause:
        return None, f"report-{cause}"
    if not store.exists(path):
        return None, "report-missing"
    try:
        if path.stat().st_size > MAX_REPORT_BYTES:
            return None, "report-oversize"
        raw = store.read_bytes(path)
    except OSError:
        return None, "report-unreadable"
    if len(raw) > MAX_REPORT_BYTES:
        return None, "report-oversize"
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, "report-invalid-json"
    if not isinstance(data, dict):
        return None, "report-not-object"
    return (name[: -len(".json")], hashlib.sha256(raw).hexdigest(), data), None


def validate_dispatch_report(data: object) -> dict[str, object]:
    """纯函数：monitor 报告 → 分发摘要（fail-closed，任何违规 raise
    DispatchError 固定词汇）。只校验身份与自洽性——**绝不重判阈值/健康**。"""
    if not isinstance(data, dict):
        raise DispatchError("report-not-object")
    if data.get("schema_version") != EXPECTED_MONITOR_SCHEMA_VERSION:
        raise DispatchError("report-schema-version")
    if data.get("tool") != MONITOR_TOOL_NAME:
        raise DispatchError("report-tool")
    if data.get("milestone") != EXPECTED_MILESTONE:
        raise DispatchError("report-milestone")
    if data.get("mode") != "execute":
        raise DispatchError("report-mode")
    collected = data.get("started_at_utc")
    if not _valid_canonical_timestamp(collected):
        raise DispatchError("report-timestamp")
    partial = data.get("partial")
    if not isinstance(partial, bool):
        raise DispatchError("report-partial-type")
    threshold = data.get("threshold_results")
    if not isinstance(threshold, dict):
        raise DispatchError("report-threshold-missing")
    counts = threshold.get("counts")
    if (not isinstance(counts, dict)
            or any(not _is_nonneg_int(counts.get(k))
                   for k in ("ok", "warn", "critical"))):
        raise DispatchError("report-counts")
    overall = data.get("overall_status")
    if overall not in OVERALL_STATUSES:
        raise DispatchError("report-overall-status")
    alerts = threshold.get("alerts")
    if not isinstance(alerts, list):
        raise DispatchError("report-alerts-type")
    warn_n = crit_n = 0
    for alert in alerts:
        if not isinstance(alert, dict):
            raise DispatchError("report-alert-field")
        check_id = alert.get("check_id")
        subject = alert.get("subject")
        severity = alert.get("severity")
        if (not isinstance(check_id, str) or not 1 <= len(check_id) <= MAX_ALERT_FIELD_LENGTH
                or not isinstance(subject, str) or not 1 <= len(subject) <= MAX_ALERT_FIELD_LENGTH
                or severity not in ALERT_SEVERITIES):
            raise DispatchError("report-alert-field")
        if severity == "warn":
            warn_n += 1
        else:
            crit_n += 1
    # 自洽：counts ↔ alerts（缺失/矛盾报告不可信，fail-closed）
    if counts["warn"] != warn_n or counts["critical"] != crit_n:  # type: ignore[index]
        raise DispatchError("report-counts-mismatch")
    # 自洽：overall ↔ counts/partial（重算必须一致；绝不重判，只对账）
    expected_overall = (INCOMPLETE_STATUS if partial
                        else "critical" if counts["critical"]  # type: ignore[index]
                        else "warn" if counts["warn"]  # type: ignore[index]
                        else "ok")
    if overall != expected_overall:
        raise DispatchError("report-overall-mismatch")
    return {
        "collected_at": collected,
        "overall_status": overall,
        "partial": partial,
        "counts": {k: counts[k] for k in ("ok", "warn", "critical")},  # type: ignore[index]
        "alert_codes": [f"{a['check_id']}:{a['subject']}:{a['severity']}"  # type: ignore[index]
                        for a in alerts],
    }


def dispatch_needed(summary: dict[str, object]) -> bool:
    """分发判定：存在 warn/critical alert 或 overall_status=incomplete。"""
    return bool(summary["alert_codes"]) or summary["overall_status"] == INCOMPLETE_STATUS


# ---------------------------------------------------------------- 出站 payload（版本化 + 固定词汇 + 有界）


def build_payload(*, stem: str, report_sha256: str,
                  summary: dict[str, object], dispatched_at: str) -> tuple[dict[str, object], int]:
    """纯函数：出站 payload（dict, 截断计数）。alert codes 超 ``MAX_ALERT_CODES``
    截断到界且截断计数显式。"""
    codes: list[str] = summary["alert_codes"]  # type: ignore[assignment]
    truncated = max(0, len(codes) - MAX_ALERT_CODES)
    report_block: dict[str, object] = {
        "stem": stem,
        "sha256": report_sha256,
        "collected_at": summary["collected_at"],
        "overall_status": summary["overall_status"],
        "partial": summary["partial"],
        "counts": summary["counts"],
        "alert_codes": codes[:MAX_ALERT_CODES],
        "alert_codes_truncated_count": truncated,
    }
    payload = {
        "schema_version": PAYLOAD_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "milestone": MILESTONE,
        "event": "monitor-alert",
        "dispatched_at": dispatched_at,
        "report": report_block,
    }
    return payload, truncated


def encode_payload(payload: dict[str, object]) -> bytes:
    """ASCII-safe 紧凑编码 + 字节数硬顶复检（超顶 fail-closed）。"""
    body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    if len(body) > MAX_PAYLOAD_BYTES:
        raise DispatchError("payload-oversize")
    return body


# ---------------------------------------------------------------- Transport（注入点；真实面仅 execute 构造）


@dataclass(frozen=True)
class DispatchResult:
    """单次分发结果：HTTP 状态码或安全错误类别（仅类别 + 异常类名，无文本）。"""

    status: int | None
    error_category: str | None
    error_class: str | None


class Transport(Protocol):
    def post_json(self, host: str, port: int, path: str, *, body: bytes,
                  headers: dict[str, str], timeout: float) -> DispatchResult: ...


class RealTransport:
    """真实出站面（仅 execute 分支构造）：``http.client`` 直连——该路径
    从不读取 proxy 环境变量/系统代理（结构性旁路）；单次 POST、固定头
    （Content-Type/Accept/User-Agent + 可选 Authorization）、不跟随重定
    向、不读响应体（取状态码后即关连接）。"""

    def __init__(self, scheme: str) -> None:
        if scheme not in ("https", "http"):
            raise ValueError("scheme")
        self._scheme = scheme

    def post_json(self, host: str, port: int, path: str, *, body: bytes,
                  headers: dict[str, str], timeout: float) -> DispatchResult:
        import http.client
        try:
            connection = (http.client.HTTPSConnection(host, port, timeout=timeout)
                          if self._scheme == "https"
                          else http.client.HTTPConnection(host, port, timeout=timeout))
            try:
                connection.request("POST", path or "/", body=body, headers=headers)
                response = connection.getresponse()
                status = response.status
            finally:
                connection.close()
        except Exception as exc:  # noqa: BLE001 —— 类别化兜底，文本不保留
            category, klass = _monitor.categorize_http_exception(exc)
            return DispatchResult(None, category, klass)
        return DispatchResult(status, None, None)


# ---------------------------------------------------------------- 分发台账（幂等 + 审计）


def load_ledger(store: Store, output_dir: Path) -> tuple[list[dict] | None, str | None]:
    """读取并逐行严格校验分发台账。成功 → (rows, None)；台账不存在 →
    ([], None)；任何行 malformed/字段非法 → (None, 固定类别)——绝不
    猜测、绝不静默跳过。重复 report_sha256 记账 → ambiguous 拒绝。"""
    ledger_path = output_dir / LEDGER_NAME
    if not store.exists(ledger_path):
        return [], None
    try:
        raw = store.read_bytes(ledger_path)
    except OSError:
        return None, "ledger-unreadable"
    seen: set[str] = set()
    rows: list[dict] = []
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            return None, "ledger-invalid-json"
        if (not isinstance(row, dict)
                or row.get("schema_version") != LEDGER_SCHEMA_VERSION
                or row.get("dispatch_status") != "sent"
                or not isinstance(row.get("report_sha256"), str)
                or len(row["report_sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in row["report_sha256"])):
            return None, "ledger-row-schema"
        sha = row["report_sha256"]
        if sha in seen:
            return None, "ledger-ambiguous-duplicate"
        seen.add(sha)
        rows.append(row)
    return rows, None


def check_duplicate(rows: list[dict], report_sha256: str) -> str | None:
    """幂等门：同报告 SHA-256 已成功分发 → duplicate-dispatch（拒绝重发）。"""
    if any(row["report_sha256"] == report_sha256 for row in rows):
        return "duplicate-dispatch"
    return None


def append_ledger(store: Store, output_dir: Path, rows: list[dict],
                  record: dict[str, object]) -> str | None:
    """原子追加台账行（整读 + 追加 + tmp+fsync+os.replace 重写）。
    返回错误类别或 None。"""
    lines = [json.dumps(row, ensure_ascii=True, separators=(",", ":")) for row in rows]
    lines.append(json.dumps(record, ensure_ascii=True, separators=(",", ":")))
    try:
        store.mkdirs(output_dir)
        store.write_atomic(output_dir / LEDGER_NAME, "\n".join(lines) + "\n")
    except (OSError, _history.HistoryError):
        return "ledger-write-error"
    return None


# ---------------------------------------------------------------- 输出路径（symlink + 防碰撞）


def ensure_output_dir_safe(store: Store, output_dir: Path) -> str | None:
    """输出目录防御：自身或现存祖先为 symlink → 拒绝（零写入）。"""
    try:
        if store.is_symlink(output_dir):
            return "output-dir-symlink"
        for ancestor in output_dir.parents:
            if store.exists(ancestor) and store.is_symlink(ancestor):
                return "output-dir-symlink-in-path"
    except OSError:
        return "output-dir-unverifiable"
    return None


def unique_report_paths(store: Store, directory: Path, base: str,
                        suffix: str) -> tuple[Path, Path] | None:
    """报告工件名防碰撞：``<base>-<suffix>`` 起步，存在即 ``-N`` 递增
    （≤99）；全部碰撞 → None（绝不覆盖既有工件）。json/md 双探测。"""
    for index in range(99):
        stem = base if index == 0 else f"{base}-{index + 1}"
        json_path = directory / f"{stem}.json"
        md_path = directory / f"{stem}.md"
        if not store.exists(json_path) and not store.exists(md_path):
            return json_path, md_path
    return None


def write_report_files(store: Store, directory: Path, base: str, suffix: str,
                       report: dict[str, object], markdown: str) -> tuple[Path, Path] | str:
    """落盘 dispatch/plan 报告（JSON+MD 原子写；redact 终防线；防碰撞）。"""
    paths = unique_report_paths(store, directory, base, suffix)
    if paths is None:
        return "report-name-collision"
    json_path, md_path = paths
    json_text = redact_secrets(json.dumps(report, ensure_ascii=False, indent=2))
    md_text = redact_secrets(markdown)
    try:
        store.mkdirs(directory)
        store.write_atomic(json_path, json_text)
        store.write_atomic(md_path, md_text)
    except (OSError, _history.HistoryError):
        return "report-write-error"
    return paths


# ---------------------------------------------------------------- 报告渲染


def build_dispatch_record(*, mode: str, generated_at: str, stem: str,
                          report_sha256: str, summary: dict[str, object],
                          dispatch_status: str, url_validation: str,
                          http_status: int | None,
                          transport_error: tuple[str, str] | None,
                          payload_sha256: str | None, payload_bytes: int | None,
                          alert_codes_truncated: int | None,
                          ledger_status: str, ledger_entries: int | None) -> dict[str, object]:
    """dispatch/plan 报告（固定词汇；绝无 URL/token/绝对本地路径）。"""
    record: dict[str, object] = {
        "schema_version": DISPATCH_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "milestone": MILESTONE,
        "mode": mode,
        "generated_at": generated_at,
        "report": {
            "stem": stem,
            "sha256": report_sha256,
            "collected_at": summary["collected_at"],
            "overall_status": summary["overall_status"],
            "partial": summary["partial"],
            "counts": summary["counts"],
        },
        "dispatch_status": dispatch_status,
        "sink": {"kind": SINK_KIND, "url_validation": url_validation},
        "http_status": http_status,
        "transport_error": ({"category": transport_error[0], "class": transport_error[1]}
                            if transport_error is not None else None),
        "payload": {
            "sha256": payload_sha256,
            "bytes": payload_bytes,
            "alert_codes_count": len(summary["alert_codes"]),  # type: ignore[arg-type]
            "alert_codes_truncated_count": alert_codes_truncated,
        },
        "ledger": {"status": ledger_status, "entries": ledger_entries},
        "boundaries": list(REPORT_BOUNDARIES),
    }
    return record


def render_markdown(record: dict[str, object]) -> str:
    counts = record["report"]["counts"]  # type: ignore[index]
    lines = [
        f"# {record['milestone']} 监控告警分发报告（mode={record['mode']}）",
        "",
        f"- 工具：`{record['tool']}`（schema_version={record['schema_version']}）",
        f"- 生成（UTC）：{record['generated_at']}",
        f"- 源报告：`{record['report']['stem']}`（sha256 {str(record['report']['sha256'])[:12]}…）",  # type: ignore[index]
        f"- overall_status=**{record['report']['overall_status']}** partial={record['report']['partial']}",  # type: ignore[index]
        f"- 检查计数：ok={counts['ok']} warn={counts['warn']} critical={counts['critical']}",  # type: ignore[index]
        f"- dispatch_status=**{record['dispatch_status']}**；sink={record['sink']['kind']}",  # type: ignore[index]
        f"- ledger：{record['ledger']['status']}（entries={record['ledger']['entries']}）",  # type: ignore[index]
    ]
    payload = record["payload"]
    assert isinstance(payload, dict)
    if payload["sha256"] is not None:
        lines.append(f"- payload：{payload['bytes']} bytes（sha256 {str(payload['sha256'])[:12]}…，"
                     f"alert codes {payload['alert_codes_count']} 条，截断 {payload['alert_codes_truncated_count']}）")
    lines += ["", "边界："]
    lines += [f"- {item}" for item in record["boundaries"]]  # type: ignore[arg-type]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monitoring_alert_dispatch.py",
        description="M14-119 监控告警外发分发最小闭环（默认 plan/validate 零网络零分发；"
                    "execute 需旗标+精确确认短语+操作者 secret 文件；单一通用 HTTPS webhook；"
                    "fail-closed、sanitized 台账幂等审计；production_ready=false 恒不变）",
    )
    parser.add_argument("--report", type=Path, required=True,
                        help="既有 production_monitor JSON 报告（stem 须为 monitor-YYYYMMDD-HHMMSS）")
    parser.add_argument("--secret-file", type=Path, default=None,
                        help='操作者提供的 webhook secret JSON 文件（{"url": "https://…", "token"?: "…"}；'
                             "gitignored 由操作者负责；值绝不回显/入档；execute 必需")
    parser.add_argument("--execute", action="store_true",
                        help="真实外发分发（默认 plan：零网络、零分发、Transport 绝不构造）")
    parser.add_argument("--confirm", default="",
                        help=f'execute 必配 --confirm "{CONFIRM_PHRASE}"（精确匹配）')
    parser.add_argument("--allow-loopback-http", action="store_true",
                        help="test-only：允许 http 且仅限字面回环 IP 的 webhook（生产禁用；"
                             "用于本机回环接收器联调）")
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS,
                        help=f"webhook 请求超时 {MIN_TIMEOUT_SECONDS}-{MAX_TIMEOUT_SECONDS}s"
                             f"（默认 {DEFAULT_TIMEOUT_SECONDS}）")
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                        help="报告与台账目录（默认 .verify/artifacts/m14-119-monitor-alert-dispatch，"
                             "gitignored；自定义路径为操作者显式自选，其位置与入库与否由操作者负责）")
    return parser


def _validate_timeout(value: float) -> str | None:
    if not math.isfinite(value):
        return "timeout-not-finite"
    if not MIN_TIMEOUT_SECONDS <= value <= MAX_TIMEOUT_SECONDS:
        return "timeout-out-of-range"
    return None


def _finish_record(store: Store, log: SafeLog, output_dir: Path, base: str,
                   suffix: str, record: dict[str, object]) -> int:
    """落盘报告并按结果定退出码（报告写入失败 = 证据不可失 → 拒绝）。"""
    outcome = write_report_files(store, output_dir, base, suffix,
                                 record, render_markdown(record))
    if isinstance(outcome, str):
        log.say(f"拒绝: 报告写入失败（{outcome}）——证据不可失")
        return EXIT_REFUSED
    json_path, md_path = outcome
    log.say(f"报告: {json_path.name} / {md_path.name}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log = SafeLog()
    store = RealStore()

    # 1) 数值面校验（plan/execute 同；超顶零读取拒绝）
    timeout_problem = _validate_timeout(args.timeout_seconds)
    if timeout_problem is not None:
        log.say(f"拒绝: --timeout-seconds {timeout_problem}")
        return EXIT_REFUSED

    # 2) 读取 + 校验既有 monitor 报告（本地只读；fail-closed）
    loaded, report_error = load_report(store, args.report)
    if report_error is not None:
        log.say(f"拒绝: 报告不可用（{report_error}）——零分发")
        return EXIT_REFUSED
    assert loaded is not None
    stem, report_sha256, data = loaded
    try:
        summary = validate_dispatch_report(data)
    except DispatchError as cause:
        log.say(f"拒绝: 报告校验失败（{cause}）——零分发")
        return EXIT_REFUSED
    counts = summary["counts"]
    assert isinstance(counts, dict)
    needed = dispatch_needed(summary)
    log.say(f"源报告: {stem}（overall={summary['overall_status']} "
            f"partial={summary['partial']} warn={counts['warn']} critical={counts['critical']}）")
    log.say(f"分发判定: {'需要分发（存在告警/不完整）' if needed else '无需分发（零告警且完整 ok）'}")

    # 3) secret 文件（提供即校验；URL 校验在 plan/execute 都做——本地零网络）
    secret: dict[str, str] | None = None
    if args.secret_file is not None:
        secret, secret_error = load_webhook_secret(store, args.secret_file)
        if secret_error is not None:
            log.say(f"拒绝: secret 文件不可用（{secret_error}）——值不回显")
            return EXIT_REFUSED
        assert secret is not None
        url_problem = validate_webhook_url(secret["url"],
                                           allow_loopback_http=args.allow_loopback_http)
        if url_problem is not None:
            log.say(f"拒绝: webhook URL 校验失败（{url_problem}）——URL 不回显")
            return EXIT_REFUSED
    url_validation = ("validated-https" if secret is not None and secret["url"].startswith("https://")
                      else "validated-loopback-http" if secret is not None else "not-provided")

    clock = RealClock()
    stamp = clock.stamp()
    suffix = clock.token_hex(4)

    # 4) plan 模式（默认）：零网络、零分发、零 Transport 构造
    if not args.execute:
        if secret is None:
            log.say("secret 文件未提供（plan 仅校验报告；execute 需 --secret-file）")
        log.say(f'执行需: --execute --confirm "{CONFIRM_PHRASE}" + --secret-file')
        record = build_dispatch_record(
            mode="plan", generated_at=clock.utc_now_iso(), stem=stem,
            report_sha256=report_sha256, summary=summary,
            dispatch_status="planned", url_validation=url_validation,
            http_status=None, transport_error=None, payload_sha256=None,
            payload_bytes=None, alert_codes_truncated=None,
            ledger_status="not-attempted", ledger_entries=None,
        )
        dir_problem = ensure_output_dir_safe(store, args.artifact_dir)
        if dir_problem is not None:
            log.say(f"拒绝: 输出目录不安全（{dir_problem}）")
            return EXIT_REFUSED
        return _finish_record(store, log, args.artifact_dir, f"plan-{stamp}", suffix, record)

    # 5) execute 门禁：精确确认短语（缺一/近似即拒，零 Transport 构造）
    if args.confirm != CONFIRM_PHRASE:
        log.say(f'拒绝: --execute 必配 --confirm "{CONFIRM_PHRASE}"（精确匹配，当前不匹配）——零分发')
        return EXIT_REFUSED
    if secret is None:
        log.say("拒绝: execute 需 --secret-file（操作者提供的 webhook secret JSON）——零分发")
        return EXIT_REFUSED
    assert secret is not None

    # 6) 输出目录安全（先于任何发送：路径问题绝不事后才发现）
    dir_problem = ensure_output_dir_safe(store, args.artifact_dir)
    if dir_problem is not None:
        log.say(f"拒绝: 输出目录不安全（{dir_problem}）——零分发")
        return EXIT_REFUSED

    # 7) 零告警且完整 → 不分发（成功路径；exit 0；台账/发送零触碰）
    if not needed:
        log.say("结果: skipped-no-alerts（ok 且零告警——零发送、零台账写入）")
        record = build_dispatch_record(
            mode="execute", generated_at=clock.utc_now_iso(), stem=stem,
            report_sha256=report_sha256, summary=summary,
            dispatch_status="skipped-no-alerts", url_validation=url_validation,
            http_status=None, transport_error=None, payload_sha256=None,
            payload_bytes=None, alert_codes_truncated=None,
            ledger_status="not-applicable", ledger_entries=None,
        )
        return _finish_record(store, log, args.artifact_dir, f"dispatch-{stamp}", suffix, record)

    # 8) 台账读取 + 幂等门（重复分发拒绝在发送之前——零 Transport 构造）
    rows, ledger_error = load_ledger(store, args.artifact_dir)
    if ledger_error is not None:
        log.say(f"拒绝: 分发台账不可用（{ledger_error}）——零分发")
        return EXIT_REFUSED
    assert rows is not None
    duplicate = check_duplicate(rows, report_sha256)
    if duplicate is not None:
        log.say(f"拒绝: {duplicate}（同报告 SHA-256 已成功分发）——零重发")
        return EXIT_REFUSED

    # 9) 构建出站 payload（版本化/固定词汇/有界）
    dispatched_at = clock.utc_now_iso()
    payload, truncated = build_payload(stem=stem, report_sha256=report_sha256,
                                       summary=summary, dispatched_at=dispatched_at)
    try:
        body = encode_payload(payload)
    except DispatchError as cause:
        log.say(f"拒绝: payload 越界（{cause}）——零分发")
        return EXIT_REFUSED
    payload_sha256 = hashlib.sha256(body).hexdigest()
    log.say(f"payload: {len(body)} bytes（alert codes {len(summary['alert_codes'])} 条"  # type: ignore[arg-type]
            f"，截断 {truncated}）——固定词汇，无任何原始日志/敏感值")

    # 10) 发送（Transport 注入；真实面仅此处构造——http.client 直连零代理）
    parts = urlsplit(secret["url"])
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": USER_AGENT}
    if secret["token"]:
        headers["Authorization"] = f"Bearer {secret['token']}"
    transport = RealTransport(parts.scheme)
    try:
        result = transport.post_json(host, port, parts.path or "/", body=body,
                                     headers=headers, timeout=args.timeout_seconds)
    except Exception as exc:  # noqa: BLE001 —— transport 自身缺陷兜底（类别化，文本不保留）
        category, klass = _monitor.categorize_http_exception(exc)
        result = DispatchResult(None, category, klass)

    base = f"dispatch-{stamp}"
    if result.status is not None and 200 <= result.status < 300:
        # 11) 成功：先台账后报告（台账失败 = 幂等面破损 → 如实可见，绝不谎报）
        ledger_row = {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "tool": TOOL_NAME,
            "milestone": MILESTONE,
            "dispatch_id": f"{stamp}-{suffix}",
            "dispatched_at": dispatched_at,
            "report_stem": stem,
            "report_sha256": report_sha256,
            "overall_status": summary["overall_status"],
            "counts": summary["counts"],
            "alert_codes_count": len(summary["alert_codes"]),  # type: ignore[arg-type]
            "dispatch_status": "sent",
            "sink": SINK_KIND,
            "http_status": result.status,
            "payload_sha256": payload_sha256,
            "payload_bytes": len(body),
        }
        ledger_write_error = append_ledger(store, args.artifact_dir, rows, ledger_row)
        status_word = "sent" if ledger_write_error is None else "sent-ledger-unrecorded"
        ledger_status = "appended" if ledger_write_error is None else "write-error"
        log.say(f"分发: http_status={result.status} → {status_word}"
                + (f"（台账写入失败：{ledger_write_error}——事实已发送，幂等面破损可见）"
                   if ledger_write_error is not None else ""))
        record = build_dispatch_record(
            mode="execute", generated_at=clock.utc_now_iso(), stem=stem,
            report_sha256=report_sha256, summary=summary,
            dispatch_status=status_word, url_validation=url_validation,
            http_status=result.status, transport_error=None,
            payload_sha256=payload_sha256, payload_bytes=len(body),
            alert_codes_truncated=truncated,
            ledger_status=ledger_status,
            ledger_entries=len(rows) + 1 if ledger_write_error is None else len(rows),
        )
        rc = _finish_record(store, log, args.artifact_dir, base, suffix, record)
        return rc if ledger_write_error is None else EXIT_REFUSED

    # 12) 失败：恒可见、绝不入台账、绝不报告为 sent
    transport_error = ((result.error_category, result.error_class or "")
                       if result.error_category is not None
                       else ("non-2xx-status", ""))
    log.say(f"分发失败: http_status={result.status} "
            f"error_category={result.error_category}——不入台账、不报告为 sent")
    record = build_dispatch_record(
        mode="execute", generated_at=clock.utc_now_iso(), stem=stem,
        report_sha256=report_sha256, summary=summary,
        dispatch_status="failed", url_validation=url_validation,
        http_status=result.status, transport_error=transport_error,
        payload_sha256=payload_sha256, payload_bytes=len(body),
        alert_codes_truncated=truncated,
        ledger_status="not-attempted", ledger_entries=len(rows),
    )
    _finish_record(store, log, args.artifact_dir, base, suffix, record)
    return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
