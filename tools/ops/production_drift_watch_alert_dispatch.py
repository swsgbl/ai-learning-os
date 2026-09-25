#!/usr/bin/env python
"""M14-135 production drift watch alert dispatch 第一片：既有 M14-127
execute 模式 drift-watch JSON 报告 → 严格校验 → 分发判定 → 单一通用
HTTPS webhook 分发 + sanitized 分发台账（补「drift=true 告警送达未实证」
缺口中第一个有界部分——本切片只交付**工具就绪**，不做调度集成）。

设计（复用 M14-119 ``monitoring_alert_dispatch`` 已实证的安全契约与
M14-133 ``production_drift_watch_history`` 的 M14-127 报告校验单一事实
源；单文件、纯标准库、零第三方依赖；Store/Transport/Clock 全注入——
开发回合零真实网络、零真实分发，全部行为用 fake 测试锁定；真实外发
仅由 supervisor 在获准窗口运行）：

- **双模式**：默认 **plan/validate**——零网络、零分发（Transport 绝不
  构造），读取并严格校验既有 drift-watch 报告 + 判定是否会分发 + 校验
  secret 文件与 webhook URL（全部本地只读）；**execute** 需同时满足
  「``--execute`` + ``--confirm "EXECUTE PRODUCTION DRIFT WATCH ALERT
  DISPATCH"``（一字不差）+ ``--secret-file`` 操作者提供的 JSON secret
  文件」，缺一或近似即 EXIT 2 且零 Transport 构造（fail-closed）。
- **只消费权威报告，绝不重判漂移**：本工具绝不运行任何 Docker/compose
  采集、绝不重算 drift——分发判定唯一依据是报告自身的 ``drift`` 布尔。
  仅接受**恰好一份**既有 M14-127 报告：文件名严格
  ``drift-watch-YYYYMMDD-HHMMSS.json`` 白名单、身份/schema 精确匹配
  （schema_version=1、tool、milestone、mode=execute）、UTC 时间戳严格
  形态且 ended>=started、文件名时间戳交叉校验（started ∈
  [stamp, stamp+2s]，匹配 M14-127 先取 stamp 后取 started 的实现序）、
  config（project 白名单形态 + api/web 锚点 tag+sha256 digest 形态）、
  counts ↔ checks 实际计数一致、drift ↔ (fail>0) 一致、drift_reasons
  固定词汇（M14-127 evaluate_drift 可产出的封闭集合）且非空 ⟺ drift。
  **plan 模式报告没有权威 drift 结论——恒拒绝，绝不猜测**。
- **分发判定（只依据报告）**：mode=execute 且 drift=true 才分发；
  合法 execute 报告 drift=false → skipped-no-alerts（成功路径，exit 0，
  零网络、零台账写入）。
- **M14-119 安全面原样复用（同一实现对象，非平行策略）**：
  ``validate_webhook_url`` / ``load_webhook_secret`` / ``RealTransport``
  / ``RealStore`` / ``RealClock`` / ``ensure_output_dir_safe`` /
  ``write_report_files`` 直接 import 复用——单一通用 HTTPS webhook
  sink；endpoint URL 与可选 Bearer token 恒来自操作者 secret 文件
  （gitignored 由操作者负责）；URL fail-closed 校验（生产恒 https；
  http 仅经显式 ``--allow-loopback-http`` test-only 旗标放行且仅限
  字面回环 IP；字面私网/链路本地/未指定/组播/保留 IP 与 localhost
  名称一律拒绝；userinfo/query/fragment/坏端口拒绝）；**URL 与 token
  绝不回显、绝不入任何输出**（stdout/报告/台账/payload）。
- **payload 版本化 + 固定词汇 + 有界**：出站 JSON 仅含 schema/tool/
  milestone/event/dispatched_at + 报告身份（stem + SHA-256 + 起止
  UTC + project）与状态摘要（drift 布尔、pass/fail 计数、固定词汇
  drift_reasons ≤32 条、超界截断计数显式）。**绝无**原始日志/env 值/
  URL/token/容器体/DB URL/绝对本地路径/threshold detail 文本。编码后
  字节数硬顶复检。
- **fail-closed**：malformed/自洽性破损报告、缺失/invalid secret、
  unsafe URL、webhook 非 2xx/超时/连接异常、输出路径 symlink/防碰撞、
  台账行键集不精确/字段非法（含他工具台账行与任何未知/多余键）、重复
  分发（同报告 SHA-256 已 sent → 拒绝且零发送）一律可见拒绝；**失败的分发恒可见
  （dispatch_status=failed）且绝不入台账、绝不报告为 sent**；台账
  写入失败时发送已发生的事实照实入档（dispatch_status=
  sent-ledger-unrecorded，exit 2）。
- **sanitized 分发台账（本里程碑独立 + 幂等 + 审计）**：
  ``drift-dispatch-ledger.jsonl``（固定名，gitignored 工件目录）仅在
  成功分发后原子追加（整读 + 追加 + tmp+fsync+os.replace 重写）；行
  schema **全字段严格校验**：键集与成功路径写入行精确一致（多键/少键
  一律拒绝）+ 逐字段类型/值域/固定词汇（schema_version/tool/
  milestone/dispatch_status/sink 精确匹配、report_sha256/
  payload_sha256 恰 64 位小写 hex、report_stem/dispatch_id 闭式形态
  + 可解析 stamp、dispatched_at/report_started_at_utc 严格 UTC 真实
  时刻、drift 布尔、counts 恰 pass/fail 非负 int、
  drift_reasons_count 有界非负 int、http_status ∈ 200..299、
  payload_bytes ∈ [1, 硬顶]；bool 绝不冒充 int 通过）。每轮另落
  ``plan-*/dispatch-*.json/.md`` 报告（存在性探测 + 递增换名，绝不
  覆盖既有工件）。
- 首片刻意最小：**单 sink、单次发送、零重试**、零调度集成（调度属
  后续切片）。退出码：0 plan / execute 成功（含 skipped-no-alerts）；
  2 一切拒绝（含分发失败与台账写入失败）。本工具不构成 alert delivery
  的 production readiness 宣称，``production_ready=false`` 不变，
  release-approval 仍是 human-only 门。

用法（仓库根）：
  python tools/ops/production_drift_watch_alert_dispatch.py \
      --report <drift-watch-*.json> [--secret-file <secret.json>]   # plan（默认，零网络零分发）
  python tools/ops/production_drift_watch_alert_dispatch.py \
      --report <drift-watch-*.json> --secret-file <secret.json> \
      --execute --confirm "EXECUTE PRODUCTION DRIFT WATCH ALERT DISPATCH"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

try:  # 直接脚本运行：tools/ops 自身在 sys.path
    import monitoring_alert_dispatch as _dispatch
    import monitoring_history as _history
    import production_monitor as _monitor
except ImportError:  # 经 importlib 按路径加载（契约测试形态）
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import monitoring_alert_dispatch as _dispatch
    import monitoring_history as _history
    import production_monitor as _monitor

EXIT_OK = 0
#: 2 = 一切 fail-closed 拒绝（与监控/drift-watch 家族统一可见拒绝口径）
EXIT_REFUSED = 2

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = (REPO_ROOT / ".verify" / "artifacts"
                      / "m14-135-drift-watch-alert-dispatch")
LEDGER_NAME = "drift-dispatch-ledger.jsonl"

TAG = "[drift-dispatch]"
DISPATCH_SCHEMA_VERSION = 1  # dispatch 报告 schema
PAYLOAD_SCHEMA_VERSION = 1   # 出站 payload schema
LEDGER_SCHEMA_VERSION = 1    # 台账行 schema
TOOL_NAME = "tools/ops/production_drift_watch_alert_dispatch.py"
MILESTONE = "M14-135"
USER_AGENT = "aios-m14-135-drift-watch-alert-dispatch/1.0"
CONFIRM_PHRASE = "EXECUTE PRODUCTION DRIFT WATCH ALERT DISPATCH"
PAYLOAD_EVENT = "drift-watch-alert"

#: 被消费的 M14-127 报告自描述事实源（与 M14-133 历史审计器常量同值：
#: schema_version=1、tool、milestone、mode=execute 严格相等才可信）
SOURCE_TOOL_NAME = "tools/ops/production_drift_watch.py"
SOURCE_MILESTONE = "M14-127"
SOURCE_SCHEMA_VERSION = 1
SOURCE_MODE_EXECUTE = "execute"
#: 文件名严格形态：drift-watch-YYYYMMDD-HHMMSS.json（伴生 .md 天然不匹配）
SOURCE_FILE_NAME_RE = re.compile(r"^drift-watch-(\d{8}-\d{6})\.json$")
#: 被接受源报告 stem 形态（= 文件名白名单去 .json；台账行 report_stem 同域）
SOURCE_STEM_RE = re.compile(r"^drift-watch-(\d{8}-\d{6})$")
FILE_STAMP_FORMAT = "%Y%m%d-%H%M%S"
#: dispatch_id 形态："<stamp YYYYMMDD-HHMMSS>-<token_hex(4) 即 8 位小写 hex>"
#: （成功路径 f"{clock.stamp()}-{clock.token_hex(4)}" 的封闭投影）
DISPATCH_ID_RE = re.compile(r"^(\d{8}-\d{6})-([0-9a-f]{8})$")
#: started_at_utc/ended_at_utc 严格形态（与 M14-127 RealClock 输出一致）
ISO_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
ISO_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
#: 文件名 stamp 先取、started_at 后取 → started 允许滞后 stamp 至多 2 秒
#: （与 M14-133 FILENAME_TS_TOLERANCE 同值）
FILENAME_TS_TOLERANCE = timedelta(seconds=2)

#: M14-127 固定画像服务全集 + 锚点服务（drift_reasons 词汇的组成部分）
STACK_SERVICES: tuple[str, ...] = (
    "postgres", "redis", "minio", "api", "web", "livekit", "searxng",
)
ANCHOR_SERVICES: tuple[str, ...] = ("api", "web")

#: drift_reasons 固定词汇 = M14-127 evaluate_drift 可产出的封闭集合：
#: 11 个前缀 × 服务域受限（全服务前缀仅配 STACK_SERVICES、锚点类前缀仅
#: 配 api/web）——任何其它字符串都是报告不可信，fail-closed 拒绝。
_ANY_SERVICE = "|".join(STACK_SERVICES)
_ANCHOR_SERVICE = "|".join(ANCHOR_SERVICES)
DRIFT_REASON_RE = re.compile(
    r"^(?:collector-failed:compose-ps"
    rf"|compose-service-missing:(?:{_ANY_SERVICE})"
    rf"|compose-service-unhealthy:(?:{_ANY_SERVICE})"
    rf"|container-facts-missing:(?:{_ANY_SERVICE})"
    rf"|container-not-healthy:(?:{_ANY_SERVICE})"
    rf"|anchor-facts-missing:(?:{_ANCHOR_SERVICE})"
    rf"|image-tag-mismatch:(?:{_ANCHOR_SERVICE})"
    rf"|image-digest-unobtainable:(?:{_ANCHOR_SERVICE})"
    rf"|image-digest-mismatch:(?:{_ANCHOR_SERVICE})"
    rf"|image-tag-resolution-unavailable:(?:{_ANCHOR_SERVICE})"
    rf"|image-tag-resolution-mismatch:(?:{_ANCHOR_SERVICE}))$"
)

#: compose 项目名白名单形态（与 M14-127 validate_project_name 同域）
PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

#: 单一通用 HTTPS webhook sink（复用 M14-119 契约；URL/token 来自操作者 secret）
SINK_KIND = "generic-https-webhook"

#: 有界（超界 fail-closed 或显式截断计数，绝不静默截断语义）
MAX_DRIFT_REASONS = 32          # payload 携带界（超出截断 + 计数显式）
MAX_DRIFT_REASONS_INPUT = 64    # 报告校验硬顶（超出 = 报告不可信，拒绝）
MAX_CHECKS = 128                # M14-127 单轮至多 28 检查；128 为 sanity 硬顶
MAX_CHECK_FIELD_LENGTH = 64
MAX_PAYLOAD_BYTES = _dispatch.MAX_PAYLOAD_BYTES
MAX_REPORT_BYTES = _dispatch.MAX_REPORT_BYTES

#: webhook 请求超时（秒）——与 M14-119 同界
MIN_TIMEOUT_SECONDS = _dispatch.MIN_TIMEOUT_SECONDS
MAX_TIMEOUT_SECONDS = _dispatch.MAX_TIMEOUT_SECONDS
DEFAULT_TIMEOUT_SECONDS = _dispatch.DEFAULT_TIMEOUT_SECONDS

#: 注入面（单一事实源复用 M14-119 实现对象；测试 monkeypatch 即本模块名）
Store = _dispatch.Store
RealStore = _dispatch.RealStore
RealTransport = _dispatch.RealTransport
RealClock = _dispatch.RealClock
DispatchResult = _dispatch.DispatchResult
validate_webhook_url = _dispatch.validate_webhook_url
load_webhook_secret = _dispatch.load_webhook_secret
reject_symlinked_path = _dispatch.reject_symlinked_path
redact_secrets = _dispatch.redact_secrets
ensure_output_dir_safe = _dispatch.ensure_output_dir_safe
write_report_files = _dispatch.write_report_files

#: 报告边界注记（固定词汇表：绝不包含 URL/token/路径等任何敏感面）
REPORT_BOUNDARIES: tuple[str, ...] = (
    ("consumes exactly one existing M14-127 execute-mode drift-watch JSON"
     " report; docker state and drift are never re-evaluated here"),
    ("a plan-mode drift-watch report has no authoritative drift conclusion"
     " and is always refused, never guessed"),
    ("dispatch happens only when the authoritative report says mode=execute"
     " and drift=true; a valid drift=false report exits 0 as"
     " skipped-no-alerts with zero network and zero ledger writes"),
    ("single generic HTTPS webhook sink reusing the M14-119 safety contract:"
     " operator-supplied gitignored JSON secret only, SSRF-restricted URL"
     " validation, plain http only behind the explicit test-only loopback"
     " flag, and the URL or token is never echoed or persisted"),
    ("outbound payload is versioned, bounded, fixed-vocabulary: report"
     " identity, project, drift boolean, pass/fail counts and drift reason"
     " codes only; never raw logs, env values, endpoints, tokens, container"
     " bodies, DB URLs, absolute paths or threshold/detail text"),
    ("failed dispatches are visible (dispatch_status=failed), never recorded"
     " in the ledger and never reported as sent; a successful dispatch"
     " followed by a ledger write failure is recorded as"
     " sent-ledger-unrecorded"),
    ("the sanitized drift-dispatch ledger is appended only after a successful"
     " dispatch, rejects duplicate dispatches of the same report hash before"
     " sending, and rejects rows belonging to any other tool"),
    ("first slice is deliberately minimal: single sink, single attempt, no"
     " retries, no scheduling integration; real dispatch remains a"
     " supervisor-approved manual step"),
    ("no real external endpoint was contacted during development (injected"
     " FakeTransport tests only); production_ready remains false and this"
     " tool never claims alert delivery is proven in production"),
)


class AlertDispatchError(RuntimeError):
    """fail-closed 拒绝（固定词汇原因；绝不携带 URL/token/文件内容文本）。"""


class SafeLog:
    """行式日志：stdout 输出前经 redact_secrets 防御性脱敏终防线。"""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def say(self, message: str) -> None:
        line = redact_secrets(message)
        self.lines.append(line)
        print(f"{TAG} {line}", flush=True)


# ---------------------------------------------------------------- 时间解析（严格）


def parse_iso_utc(value: object) -> datetime:
    """严格解析 ``YYYY-MM-DDTHH:MM:SSZ`` → aware datetime；违规 raise。"""
    if not isinstance(value, str) or ISO_TS_RE.match(value) is None:
        raise AlertDispatchError("report-timestamp")
    return datetime.strptime(value, ISO_TS_FORMAT).replace(tzinfo=timezone.utc)


def _is_nonneg_int(value: object) -> bool:
    """bool 不是 int（计数校验；与监控家族同口径的类型判断）。"""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


# ---------------------------------------------------------------- 报告读取 + 校验


def load_drift_report(store: Store, path: Path) -> tuple[tuple[str, str, str, dict] | None, str | None]:
    """读取既有 M14-127 drift-watch JSON 报告。成功 →
    ((stem, stamp, sha256, data), None)；失败 → (None, 固定类别)。
    文件名严格白名单 + symlink 防御 + 大小硬顶双检。"""
    name = path.name
    match = SOURCE_FILE_NAME_RE.match(name)
    if match is None:
        return None, "report-stem-invalid"
    stamp = match.group(1)
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
    return (name[: -len(".json")], stamp, hashlib.sha256(raw).hexdigest(), data), None


def validate_drift_report(stamp: str, data: object) -> dict[str, object]:
    """纯函数：M14-127 报告 → 分发摘要（fail-closed，任何违规 raise
    AlertDispatchError 固定词汇）。只校验身份与自洽性——**绝不重判
    Docker 状态或漂移**；drift 判定唯一依据是报告自身布尔。"""
    if not isinstance(data, dict):
        raise AlertDispatchError("report-not-object")
    if data.get("schema_version") != SOURCE_SCHEMA_VERSION:
        raise AlertDispatchError("report-schema-version")
    if data.get("tool") != SOURCE_TOOL_NAME:
        raise AlertDispatchError("report-tool")
    if data.get("milestone") != SOURCE_MILESTONE:
        raise AlertDispatchError("report-milestone")
    if data.get("mode") != SOURCE_MODE_EXECUTE:
        # plan 模式报告没有权威 drift 结论——恒拒绝，绝不猜测
        raise AlertDispatchError("report-mode")
    started = data.get("started_at_utc")
    ended = data.get("ended_at_utc")
    started_dt = parse_iso_utc(started)
    ended_dt = parse_iso_utc(ended)
    if ended_dt < started_dt:
        raise AlertDispatchError("report-ended-before-started")
    # 文件名 UTC 时间戳交叉校验（[stamp, stamp+2s]，匹配 M14-127 实现序）
    try:
        file_dt = datetime.strptime(stamp, FILE_STAMP_FORMAT).replace(
            tzinfo=timezone.utc)
    except ValueError:
        raise AlertDispatchError("report-stamp-invalid") from None
    if started_dt < file_dt or started_dt > file_dt + FILENAME_TS_TOLERANCE:
        raise AlertDispatchError("report-filename-timestamp-mismatch")
    config = data.get("config")
    if not isinstance(config, dict):
        raise AlertDispatchError("report-config")
    project = config.get("project")
    if not isinstance(project, str) or PROJECT_NAME_RE.match(project) is None:
        raise AlertDispatchError("report-project")
    anchors = config.get("anchors")
    if not isinstance(anchors, dict):
        raise AlertDispatchError("report-anchors")
    for service in ANCHOR_SERVICES:
        anchor = anchors.get(service)
        if (not isinstance(anchor, dict)
                or not isinstance(anchor.get("expected_tag"), str)
                or not anchor["expected_tag"]
                or not isinstance(anchor.get("expected_digest"), str)
                or DIGEST_RE.match(anchor["expected_digest"]) is None):
            raise AlertDispatchError("report-anchors")
    if not isinstance(data.get("collectors"), dict):
        raise AlertDispatchError("report-collectors")
    checks = data.get("checks")
    if not isinstance(checks, list) or len(checks) > MAX_CHECKS:
        raise AlertDispatchError("report-checks")
    pass_n = fail_n = 0
    for check in checks:
        if not isinstance(check, dict):
            raise AlertDispatchError("report-check-field")
        status = check.get("status")
        if (not isinstance(check.get("check_id"), str)
                or not 1 <= len(check["check_id"]) <= MAX_CHECK_FIELD_LENGTH
                or not isinstance(check.get("subject"), str)
                or not 1 <= len(check["subject"]) <= MAX_CHECK_FIELD_LENGTH
                or status not in ("pass", "fail")):
            raise AlertDispatchError("report-check-field")
        if status == "pass":
            pass_n += 1
        else:
            fail_n += 1
    counts = data.get("counts")
    if (not isinstance(counts, dict)
            or not _is_nonneg_int(counts.get("pass"))
            or not _is_nonneg_int(counts.get("fail"))):
        raise AlertDispatchError("report-counts")
    # 自洽：counts ↔ checks（缺失/矛盾报告不可信，fail-closed）
    if counts["pass"] != pass_n or counts["fail"] != fail_n:  # type: ignore[index]
        raise AlertDispatchError("report-counts-mismatch")
    drift = data.get("drift")
    if not isinstance(drift, bool):
        raise AlertDispatchError("report-drift-type")
    # 自洽：drift ↔ 失败检查数（绝不重判，只对账）
    if drift != (fail_n > 0):
        raise AlertDispatchError("report-drift-mismatch")
    reasons = data.get("drift_reasons")
    if not isinstance(reasons, list) or len(reasons) > MAX_DRIFT_REASONS_INPUT:
        raise AlertDispatchError("report-drift-reasons")
    for reason in reasons:
        if not isinstance(reason, str) or DRIFT_REASON_RE.match(reason) is None:
            raise AlertDispatchError("report-drift-reasons-vocabulary")
    # 自洽：非空 reasons ⟺ drift（M14-127 每个 fail 检查恒产出 ≥1 reason）
    if bool(reasons) != drift:
        raise AlertDispatchError("report-drift-reasons-mismatch")
    return {
        "started_at_utc": started,
        "ended_at_utc": ended,
        "project": project,
        "drift": drift,
        "counts": {"pass": pass_n, "fail": fail_n},
        "drift_reasons": list(reasons),
    }


def dispatch_needed(summary: dict[str, object]) -> bool:
    """分发判定：唯一依据是权威报告的 drift 布尔（绝不在此重判漂移）。"""
    return bool(summary["drift"])


# ---------------------------------------------------------------- 出站 payload（版本化 + 固定词汇 + 有界）


def build_payload(*, stem: str, report_sha256: str,
                  summary: dict[str, object], dispatched_at: str) -> tuple[dict[str, object], int]:
    """纯函数：出站 payload（dict, 截断计数）。drift_reasons 超
    ``MAX_DRIFT_REASONS`` 截断到界且截断计数显式。"""
    reasons: list[str] = summary["drift_reasons"]  # type: ignore[assignment]
    truncated = max(0, len(reasons) - MAX_DRIFT_REASONS)
    report_block: dict[str, object] = {
        "stem": stem,
        "sha256": report_sha256,
        "started_at_utc": summary["started_at_utc"],
        "ended_at_utc": summary["ended_at_utc"],
        "project": summary["project"],
        "drift": summary["drift"],
        "counts": summary["counts"],
        "drift_reasons": reasons[:MAX_DRIFT_REASONS],
        "drift_reasons_truncated_count": truncated,
    }
    payload = {
        "schema_version": PAYLOAD_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "milestone": MILESTONE,
        "event": PAYLOAD_EVENT,
        "dispatched_at": dispatched_at,
        "report": report_block,
    }
    return payload, truncated


def encode_payload(payload: dict[str, object]) -> bytes:
    """ASCII-safe 紧凑编码 + 字节数硬顶复检（超顶 fail-closed）。"""
    body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    if len(body) > MAX_PAYLOAD_BYTES:
        raise AlertDispatchError("payload-oversize")
    return body


# ---------------------------------------------------------------- 分发台账（本里程碑独立 + 幂等 + 审计）

#: 台账行固定键集（与成功路径写入的 ledger_row **完全一致**——多键/少键
#: 一律 fail-closed 拒绝，绝不静默放行未知/多余字段）
LEDGER_ROW_KEYS = frozenset({
    "schema_version", "tool", "milestone", "dispatch_id", "dispatched_at",
    "report_stem", "report_sha256", "report_started_at_utc", "drift",
    "counts", "drift_reasons_count", "dispatch_status", "sink",
    "http_status", "payload_sha256", "payload_bytes",
})


def _is_lower_hex64(value: object) -> bool:
    """恰 64 位小写 hex 字符串（sha256 指纹域；大写/短长/非 hex 恒 False）。"""
    return (isinstance(value, str) and len(value) == 64
            and all(c in "0123456789abcdef" for c in value))


def _is_strict_iso_utc(value: object) -> bool:
    """严格 YYYY-MM-DDTHH:MM:SSZ 形态且为真实日历时刻（9999-99-99 恒 False）。"""
    if not isinstance(value, str) or ISO_TS_RE.match(value) is None:
        return False
    try:
        datetime.strptime(value, ISO_TS_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return True


def _is_valid_ledger_row(row: object) -> bool:
    """纯函数：台账行**全字段**严格校验（键集精确 + 类型/值域/固定词汇，
    与成功路径写入行完全同构）。任何多余/缺失键、越类型值、越词汇值、
    越界整数、非法时间戳/指纹/stem/dispatch_id 形态 → False。"""
    if not isinstance(row, dict) or set(row) != LEDGER_ROW_KEYS:
        return False
    # 身份（bool 是 int 的子类：schema_version=true 绝不冒充 1 通过）
    if isinstance(row["schema_version"], bool) or row["schema_version"] != LEDGER_SCHEMA_VERSION:
        return False
    if row["tool"] != TOOL_NAME or row["milestone"] != MILESTONE:
        return False
    # 状态/固定词汇
    if row["dispatch_status"] != "sent" or row["sink"] != SINK_KIND:
        return False
    # 源报告 stem：被接受源文件名形态（白名单去 .json）+ 可解析 stamp
    stem = row["report_stem"]
    if not isinstance(stem, str) or SOURCE_STEM_RE.match(stem) is None:
        return False
    try:
        datetime.strptime(stem[len("drift-watch-"):], FILE_STAMP_FORMAT).replace(
            tzinfo=timezone.utc)
    except ValueError:
        return False
    # dispatch_id："<stamp>-<token_hex(4)>" 闭式 + 可解析 stamp
    dispatch_id = row["dispatch_id"]
    match = (DISPATCH_ID_RE.match(dispatch_id)
             if isinstance(dispatch_id, str) else None)
    if match is None:
        return False
    try:
        datetime.strptime(match.group(1), FILE_STAMP_FORMAT).replace(
            tzinfo=timezone.utc)
    except ValueError:
        return False
    # 时间戳：严格 UTC 形态 + 真实日历时刻
    if not _is_strict_iso_utc(row["dispatched_at"]):
        return False
    if not _is_strict_iso_utc(row["report_started_at_utc"]):
        return False
    # drift 布尔
    if not isinstance(row["drift"], bool):
        return False
    # counts：恰 pass/fail 两键的非负 int（bool 不冒充 int）
    counts = row["counts"]
    if (not isinstance(counts, dict) or set(counts) != {"pass", "fail"}
            or any(isinstance(counts[key], bool)
                   or not isinstance(counts[key], int) or counts[key] < 0
                   for key in ("pass", "fail"))):
        return False
    # drift_reasons_count：非负 int 且在工具有界域内
    reasons_count = row["drift_reasons_count"]
    if (isinstance(reasons_count, bool) or not isinstance(reasons_count, int)
            or not 0 <= reasons_count <= MAX_DRIFT_REASONS_INPUT):
        return False
    # 指纹：双 sha256 恰 64 位小写 hex
    if not _is_lower_hex64(row["report_sha256"]):
        return False
    if not _is_lower_hex64(row["payload_sha256"]):
        return False
    # http_status：真实成功态（200..299 的 int；bool/字符串/越界恒 False）
    status = row["http_status"]
    if isinstance(status, bool) or not isinstance(status, int) or not 200 <= status <= 299:
        return False
    # payload_bytes：正 int 且不超 payload 硬顶（末字段直接返回合取条件）
    payload_bytes = row["payload_bytes"]
    return (not isinstance(payload_bytes, bool)
            and isinstance(payload_bytes, int)
            and 1 <= payload_bytes <= MAX_PAYLOAD_BYTES)


def load_ledger(store: Store, output_dir: Path) -> tuple[list[dict] | None, str | None]:
    """读取并逐行严格校验 drift-dispatch 台账（**全字段** schema：键集
    精确 + 类型/值域/固定词汇，见 ``_is_valid_ledger_row``）。成功 →
    (rows, None)；台账不存在 → ([], None)；任何行 malformed/键集不精确/
    字段非法/属于其它工具 → (None, "ledger-row-schema")——绝不猜测、
    绝不静默跳过。重复 report_sha256 记账 → ambiguous 拒绝。"""
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
        if not _is_valid_ledger_row(row):
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


# ---------------------------------------------------------------- 报告渲染


def build_dispatch_record(*, mode: str, generated_at: str, stem: str,
                          report_sha256: str, summary: dict[str, object],
                          dispatch_decision: str, dispatch_status: str,
                          url_validation: str, http_status: int | None,
                          transport_error: tuple[str, str] | None,
                          payload_sha256: str | None, payload_bytes: int | None,
                          drift_reasons_truncated: int | None,
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
            "started_at_utc": summary["started_at_utc"],
            "ended_at_utc": summary["ended_at_utc"],
            "project": summary["project"],
            "drift": summary["drift"],
            "counts": summary["counts"],
        },
        "dispatch_decision": dispatch_decision,
        "dispatch_status": dispatch_status,
        "sink": {"kind": SINK_KIND, "url_validation": url_validation},
        "http_status": http_status,
        "transport_error": ({"category": transport_error[0], "class": transport_error[1]}
                            if transport_error is not None else None),
        "payload": {
            "sha256": payload_sha256,
            "bytes": payload_bytes,
            "drift_reasons_count": len(summary["drift_reasons"]),  # type: ignore[arg-type]
            "drift_reasons_truncated_count": drift_reasons_truncated,
        },
        "ledger": {"status": ledger_status, "entries": ledger_entries},
        "boundaries": list(REPORT_BOUNDARIES),
    }
    return record


def render_markdown(record: dict[str, object]) -> str:
    counts = record["report"]["counts"]  # type: ignore[index]
    lines = [
        f"# {record['milestone']} production drift watch 告警分发报告（mode={record['mode']}）",
        "",
        f"- 工具：`{record['tool']}`（schema_version={record['schema_version']}）",
        f"- 生成（UTC）：{record['generated_at']}",
        f"- 源报告：`{record['report']['stem']}`（sha256 {str(record['report']['sha256'])[:12]}…）",  # type: ignore[index]
        f"- 起止（UTC）：{record['report']['started_at_utc']} → {record['report']['ended_at_utc']}",  # type: ignore[index]
        f"- 项目：{record['report']['project']}",  # type: ignore[index]
        f"- drift=**{str(record['report']['drift']).lower()}**；检查计数：pass={counts['pass']} fail={counts['fail']}",  # type: ignore[index]
        (f"- dispatch_decision=**{record['dispatch_decision']}**；"
         f"dispatch_status=**{record['dispatch_status']}**；sink={record['sink']['kind']}"),  # type: ignore[index]
        f"- ledger：{record['ledger']['status']}（entries={record['ledger']['entries']}）",  # type: ignore[index]
    ]
    payload = record["payload"]
    assert isinstance(payload, dict)
    if payload["sha256"] is not None:
        lines.append(f"- payload：{payload['bytes']} bytes（sha256 {str(payload['sha256'])[:12]}…，"
                     f"drift reasons {payload['drift_reasons_count']} 条，"
                     f"截断 {payload['drift_reasons_truncated_count']}）")
    lines += ["", "边界："]
    lines += [f"- {item}" for item in record["boundaries"]]  # type: ignore[arg-type]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="production_drift_watch_alert_dispatch.py",
        description="M14-135 production drift watch 告警分片第一片：既有 M14-127 execute 模式"
                    "drift-watch JSON 报告 → 严格校验（绝不重判漂移）→ drift=true 才经单一通用"
                    " HTTPS webhook 分发（默认 plan 零网络零分发；execute 需旗标+精确确认短语+"
                    "操作者 secret 文件；fail-closed、sanitized 台账幂等审计；"
                    "production_ready=false 恒不变）",
    )
    parser.add_argument("--report", type=Path, required=True,
                        help="既有 M14-127 drift-watch JSON 报告（恰好一份；文件名须为"
                             " drift-watch-YYYYMMDD-HHMMSS.json 且 mode=execute）")
    parser.add_argument("--secret-file", type=Path, default=None,
                        help='操作者提供的 webhook secret JSON 文件（{"url": "https://…", "token"?: "…"}；'
                             "gitignored 由操作者负责；值绝不回显/入档；execute 必需）")
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
                        help="报告与台账目录（默认 .verify/artifacts/m14-135-drift-watch-alert-dispatch，"
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

    # 2) 读取 + 严格校验既有 M14-127 报告（本地只读；fail-closed；绝不重判漂移）
    loaded, report_error = load_drift_report(store, args.report)
    if report_error is not None:
        log.say(f"拒绝: 报告不可用（{report_error}）——零分发")
        return EXIT_REFUSED
    assert loaded is not None
    stem, stamp, report_sha256, data = loaded
    try:
        summary = validate_drift_report(stamp, data)
    except AlertDispatchError as cause:
        if str(cause) == "report-mode":
            log.say("拒绝: 报告 mode 非 execute（plan 报告无权威 drift 结论——"
                    "绝不猜测，零分发）")
        else:
            log.say(f"拒绝: 报告校验失败（{cause}）——零分发")
        return EXIT_REFUSED
    counts = summary["counts"]
    assert isinstance(counts, dict)
    needed = dispatch_needed(summary)
    log.say(f"源报告: {stem}（drift={str(summary['drift']).lower()} "
            f"pass={counts['pass']} fail={counts['fail']}）")
    log.say(f"分发判定: {'需要分发（权威报告 drift=true）' if needed else '无需分发（drift=false → skipped-no-alerts）'}")

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
    stamp_now = clock.stamp()
    suffix = clock.token_hex(4)

    # 4) plan 模式（默认）：零网络、零分发、零 Transport 构造
    if not args.execute:
        if secret is None:
            log.say("secret 文件未提供（plan 仅校验报告；execute 需 --secret-file）")
        log.say(f'执行需: --execute --confirm "{CONFIRM_PHRASE}" + --secret-file')
        record = build_dispatch_record(
            mode="plan", generated_at=clock.utc_now_iso(), stem=stem,
            report_sha256=report_sha256, summary=summary,
            dispatch_decision="dispatch" if needed else "skip",
            dispatch_status="planned", url_validation=url_validation,
            http_status=None, transport_error=None, payload_sha256=None,
            payload_bytes=None, drift_reasons_truncated=None,
            ledger_status="not-attempted", ledger_entries=None,
        )
        dir_problem = ensure_output_dir_safe(store, args.artifact_dir)
        if dir_problem is not None:
            log.say(f"拒绝: 输出目录不安全（{dir_problem}）")
            return EXIT_REFUSED
        return _finish_record(store, log, args.artifact_dir, f"plan-{stamp_now}", suffix, record)

    # 5) execute 门禁：精确确认短语 + secret（缺一/近似即拒，零 Transport 构造）
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

    # 7) drift=false → 不分发（成功路径；exit 0；台账/发送零触碰）
    if not needed:
        log.say("结果: skipped-no-alerts（权威报告 drift=false——零发送、零台账写入）")
        record = build_dispatch_record(
            mode="execute", generated_at=clock.utc_now_iso(), stem=stem,
            report_sha256=report_sha256, summary=summary,
            dispatch_decision="skip", dispatch_status="skipped-no-alerts",
            url_validation=url_validation,
            http_status=None, transport_error=None, payload_sha256=None,
            payload_bytes=None, drift_reasons_truncated=None,
            ledger_status="not-applicable", ledger_entries=None,
        )
        return _finish_record(store, log, args.artifact_dir, f"dispatch-{stamp_now}", suffix, record)

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
    except AlertDispatchError as cause:
        log.say(f"拒绝: payload 越界（{cause}）——零分发")
        return EXIT_REFUSED
    payload_sha256 = hashlib.sha256(body).hexdigest()
    log.say(f"payload: {len(body)} bytes（drift reasons {len(summary['drift_reasons'])} 条"  # type: ignore[arg-type]
            f"，截断 {truncated}）——固定词汇，无任何原始日志/敏感值")

    # 10) 发送（Transport 注入缝 = 模块级 RealTransport；真实面仅此处构造）
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

    base = f"dispatch-{stamp_now}"
    if result.status is not None and 200 <= result.status < 300:
        # 11) 成功：先台账后报告（台账失败 = 幂等面破损 → 如实可见，绝不谎报）
        ledger_row = {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "tool": TOOL_NAME,
            "milestone": MILESTONE,
            "dispatch_id": f"{stamp_now}-{suffix}",
            "dispatched_at": dispatched_at,
            "report_stem": stem,
            "report_sha256": report_sha256,
            "report_started_at_utc": summary["started_at_utc"],
            "drift": summary["drift"],
            "counts": summary["counts"],
            "drift_reasons_count": len(summary["drift_reasons"]),  # type: ignore[arg-type]
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
            dispatch_decision="dispatch", dispatch_status=status_word,
            url_validation=url_validation,
            http_status=result.status, transport_error=None,
            payload_sha256=payload_sha256, payload_bytes=len(body),
            drift_reasons_truncated=truncated,
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
        dispatch_decision="dispatch", dispatch_status="failed",
        url_validation=url_validation,
        http_status=result.status, transport_error=transport_error,
        payload_sha256=payload_sha256, payload_bytes=len(body),
        drift_reasons_truncated=truncated,
        ledger_status="not-attempted", ledger_entries=len(rows),
    )
    _finish_record(store, log, args.artifact_dir, base, suffix, record)
    return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
