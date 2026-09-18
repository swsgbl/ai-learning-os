#!/usr/bin/env python
r"""M14-55 审计归档输入更新器（fail-closed，单文件纯标准库）。

从操作者**显式**给出的四个输入计算 M14-50 调度器消费的 policy/state
文档：审计锚点 JSONL（恰一个 sequence=0 的 genesis 锚）、M14-43 WORM
verify 成功报告、M14-49 离线副本 verify 成功报告、当前时刻。
``plan``（Stage A1）把确定性 JSON 打印到 stdout——零文件写入；
``apply``（Stage A2）在同一条校验链通过后，把 canonical policy 字节
与 canonical state JSON 原子落盘到**显式**给出的两个输出路径。

输入纪律（全程边界）：``--anchor`` / ``--worm-report`` /
``--offline-report`` / ``--now`` 四参数全部显式必填——零自动发现、
零网络、零环境变量读取、零子进程、零 DB、零 S3、零 WORM 访问、零
离线根访问、零调度器访问；``plan`` 除五个输入文件（锚 + 两报告 +
两 sidecar）外不读任何路径、除 stdout 外不写任何字节；``apply``
额外只访问两个显式输出路径（存在时读取比对，全部预检通过后写入）。

校验链（顺序即防线，首个失败即受控拒绝并短路）：

1. ``--now`` 必须可按 ISO-8601 tz-aware 解析（拒绝 naive——时序判定
   需要无歧义时区）；
2. 文件门（五个输入同一标准）：常规文件、非 symlink、非空、
   ≤1 MiB；
3. 锚 JSONL：恰一行 strict JSON 对象（拒绝重复键 / NaN / Infinity，
   含 ``1e999`` 溢出为 inf 的形态）——schema_version=1、
   algorithm=sha256、sequence=0、head_hash 与 previous_anchor_hash
   均为 genesis 常量、三个哈希字段为 64 位小写 hex、anchored_at 可
   按 tz-aware ISO-8601 解析、行字节恰为 canonical JSON、
   anchor_hash 按 canonical 载荷重算一致（与 M14-42/M14-43/M14-49
   同契约，收紧两点：恰一个 sequence=0 锚；anchored_at 必须
   tz-aware）；
4. verify 报告（WORM/离线同链）：strict JSON 对象；sidecar
   ``<report>.json.sha256`` 为常规非 symlink 文件且首 token 与报告
   字节 SHA-256 一致；status=pass；problems=[]；各自旗标
   （WORM 报告 ``worm_verified``、离线报告 ``offline_verified``）
   为 true；ended_at_utc 可按 tz-aware ISO-8601 解析；
   source.sha256 与锚文件 SHA-256 一致——三方绑定同一锚；
5. 时序：anchored_at ≤ WORM ended_at_utc ≤ 离线 ended_at_utc，
   且三个时刻均不得晚于 ``--now``。

plan 输出（确定性）：status / problems / now / anchor 摘要 +
``policy``（schema v1 常量策略：anchor 30d、worm_archive 90d、
offline_copy 180d、grace_hours 24）+ ``policy_bytes``（policy 的
canonical sorted JSON 精确字节）+ ``state``（schema v1：anchor /
worm_archive / offline_copy 三任务各含 last_success_at /
source_sha256 / facts——anchor 任务取 anchored_at，两报告任务取
各自 ended_at_utc；source_sha256 三方同锚；facts 只记已校验的
标量事实）。全部时刻规范化为 UTC isoformat；输出是纯输入的
函数——同输入必出同字节，不含路径、不含时钟、不含随机性，全文
ASCII（problems detail 亦然），任何 locale 下逐字节稳定。

apply 安全契约（Stage A2；全部预检门通过后才产生第一次写入）：

1. ``--confirm`` 必须逐字等于 ``EXECUTE AUDIT ARCHIVE INPUT
   UPDATE``——任何其他值在任何输出路径被访问之前拒绝；
2. 先按与 plan 完全相同的校验链构建同一 plan 载荷——校验失败时
   零输出访问、零写入；
3. 输出路径门（两输出同一标准）：非 symlink、非目录、父目录必须
   已存在（本工具绝不创建目录）、解析后不得与任何输入 / sidecar /
   另一输出路径相同；任何输出 payload 与 problem detail 都不
   序列化路径；
4. 既有 policy 输出若存在：必须是非 symlink 常规文件且字节恰为
   POLICY_BYTES——字节漂移即拒绝，绝不静默覆盖；
5. 既有 state 输出若存在：以同一 strict JSON 规则（拒重复键 /
   NaN / Infinity、≤1 MiB）装载，要求 schema 1 恰三任务形状（每
   任务恰 last_success_at / source_sha256 / facts 三字段，时刻
   tz-aware 可解析），且任一新任务 last_success_at 不得早于既有
   同任务时刻——回滚与畸形既有 state 一律拒绝；
6. 写入：每目标一个位于目标父目录的临时文件 → 写字节 →
   flush + fsync → 原子替换既有目标；任何失败路径都清理全部临时
   文件。逐文件原子，**诚实声明不承诺跨文件事务**——两输出各自
   独立原子替换，后写者失败时先写者可能已生效。

apply 输出（确定性 JSON）：plan 事实（now + anchor 摘要）+
policy/state 各自 ``already_matched`` / ``written`` 布尔 +
``cross_file_transaction: false``；canonical 输出即 plan state 的
canonical JSON 字节与 POLICY_BYTES；既有输出逐字节相等时零写入
（成功重跑为纯 no-op）。输出不含绝对路径、端点、秘密、版本 ID
或原始输入数据。

退出码：``0`` 成功；``2`` 一切拒绝——受控拒绝把 fail JSON 打印到
stdout（含首个 problem 的 code/detail）；意外异常只向 stderr 记
异常类型名（argparse 的 SystemExit 属 BaseException，不受影响），
绝不上抛原始 traceback。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

MILESTONE = "M14-55"
TOOL_NAME = "audit_archive_input"

#: 单输入文件字节上限（锚 / 报告 / sidecar 同一标准）
MAX_INPUT_BYTES = 1024 * 1024

#: 与锚点生产端（app.domain.audit_chain）同源的锚行常量
ANCHOR_SCHEMA_VERSION = 1
ANCHOR_ALGORITHM = "sha256"
GENESIS_HASH = "0" * 64
ANCHOR_REQUIRED_SEQUENCE = 0
ANCHOR_FIELDS = frozenset(
    {"schema_version", "algorithm", "sequence", "head_hash", "anchored_at",
     "previous_anchor_hash", "anchor_hash"})

#: M14-50 调度器消费的 policy 文档（schema v1 常量策略）
POLICY_DOCUMENT = {
    "schema_version": 1,
    "intervals": {"anchor": {"days": 30}, "worm_archive": {"days": 90},
                  "offline_copy": {"days": 180}},
    "grace_hours": 24,
}

#: WORM / 离线报告各自的通过旗标
REPORT_FLAGS = {"worm": "worm_verified", "offline": "offline_verified"}

EXIT_OK = 0
EXIT_REJECT = 2

_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")


class _PlanReject(Exception):
    """受控拒绝（code + 纯 ASCII detail；由 main 折叠为 fail JSON）。"""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


def _reject(code: str, detail: str) -> NoReturn:
    raise _PlanReject(code, detail)


# ---------------------------------------------------------------- 通用工具


def _canonical_json_bytes(payload: dict) -> bytes:
    """与锚点生产端同源的 canonical JSON（sort_keys/紧凑分隔/utf-8）。"""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


#: policy 的 canonical sorted JSON 精确字节（apply 阶段落盘即用此字节）
POLICY_BYTES = _canonical_json_bytes(POLICY_DOCUMENT)


def _parse_iso_utc(raw: str | None) -> datetime | None:
    """ISO-8601 → tz-aware UTC datetime；naive / 不可解析返回 None。"""
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None or dt.utcoffset() is None:
        return None
    return dt.astimezone(UTC)


def _format_utc(moment: datetime) -> str:
    """与 M14-50 调度器同源的 canonical UTC ISO-8601 渲染。"""
    return moment.astimezone(UTC).isoformat()


def _reject_constant(name: str) -> None:
    raise ValueError(f"non-finite constant: {name}")


def _reject_duplicate_keys(pairs):
    obj: dict = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate key: {key}")
        obj[key] = value
    return obj


def _parse_finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"non-finite number: {text}")
    return value


def _strict_json_loads(data: bytes):
    """strict JSON：拒绝重复键 / NaN / Infinity（含 1e999 溢出形态）。"""
    return json.loads(
        data.decode("utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_constant,
        parse_float=_parse_finite_float,
    )


def _read_input_file(path_str: str, label: str) -> bytes:
    """文件门：常规文件、非 symlink、非空、≤1 MiB；返回原始字节。"""
    path = Path(path_str)
    if path.is_symlink():
        _reject(f"{label}-symlink", f"{label} input must not be a symlink")
    if not path.exists():
        _reject(f"{label}-missing", f"{label} input file does not exist")
    if not path.is_file():
        _reject(f"{label}-not-regular", f"{label} input is not a regular file")
    try:
        size = path.stat().st_size
    except OSError:
        _reject(f"{label}-unreadable", f"{label} input metadata unreadable")
    if size > MAX_INPUT_BYTES:
        _reject(f"{label}-too-large",
                "input exceeds the 1 MiB cap")
    try:
        data = path.read_bytes()
    except OSError:
        _reject(f"{label}-unreadable", f"{label} input unreadable")
    if len(data) > MAX_INPUT_BYTES:
        _reject(f"{label}-too-large", "input exceeds the 1 MiB cap")
    if not data:
        _reject(f"{label}-empty", f"{label} input file is empty")
    return data


# ---------------------------------------------------------------- 锚校验


@dataclass(frozen=True)
class AnchorFacts:
    """锚文件全部校验通过后的事实快照。"""

    sha256: str
    size_bytes: int
    algorithm: str
    sequence: int
    anchor_hash: str
    anchored_at: datetime


def _load_anchor(data: bytes) -> AnchorFacts:
    """恰一行 sequence=0 genesis 锚的完整校验（M14-42 契约收紧版）。"""
    if not data.endswith(b"\n"):
        _reject("anchor-no-trailing-newline",
                "anchor JSONL must end with a single newline")
    rows = data[:-1].split(b"\n")
    if len(rows) != 1:
        _reject("anchor-not-single-line",
                "anchor JSONL must contain exactly one line")
    row = rows[0]
    if not row:
        _reject("anchor-line-empty", "anchor line is empty")
    try:
        anchor = _strict_json_loads(row)
    except ValueError:
        _reject("anchor-line-not-strict-json",
                "anchor line is not strict JSON (duplicate keys and "
                "NaN/Infinity are rejected)")
    if not isinstance(anchor, dict):
        _reject("anchor-line-not-object", "anchor line is not a JSON object")
    if set(anchor) != ANCHOR_FIELDS:
        _reject("anchor-field-set", "anchor field set does not match the "
                "seven-field contract")
    if type(anchor["schema_version"]) is not int \
            or anchor["schema_version"] != ANCHOR_SCHEMA_VERSION:
        _reject("anchor-schema-version",
                "anchor schema_version must be the integer 1")
    if anchor["algorithm"] != ANCHOR_ALGORITHM:
        _reject("anchor-algorithm",
                "anchor algorithm must be sha256")
    if type(anchor["sequence"]) is not int \
            or anchor["sequence"] != ANCHOR_REQUIRED_SEQUENCE:
        _reject("anchor-sequence-not-zero",
                "anchor sequence must be exactly 0 (Stage A1 accepts one "
                "genesis anchor only)")
    for field in ("head_hash", "anchored_at", "previous_anchor_hash",
                  "anchor_hash"):
        if type(anchor[field]) is not str:
            _reject("anchor-field-type",
                    f"anchor field {field} must be a string")
    anchored_at = _parse_iso_utc(anchor["anchored_at"])
    if anchored_at is None:
        _reject("anchor-anchored-at-invalid",
                "anchor anchored_at is not a tz-aware ISO-8601 timestamp")
    for field in ("head_hash", "previous_anchor_hash", "anchor_hash"):
        if _HEX64.fullmatch(anchor[field]) is None:
            _reject("anchor-hash-format",
                    f"anchor field {field} is not 64 lowercase hex characters")
    if row != _canonical_json_bytes(anchor):
        _reject("anchor-not-canonical",
                "anchor line bytes are not canonical sorted compact JSON")
    payload = {k: v for k, v in anchor.items() if k != "anchor_hash"}
    recomputed = hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()
    if recomputed != anchor["anchor_hash"]:
        _reject("anchor-hash-mismatch",
                "anchor_hash does not match the canonical payload digest")
    if anchor["previous_anchor_hash"] != GENESIS_HASH:
        _reject("anchor-genesis-previous",
                "single-anchor previous_anchor_hash must be the genesis "
                "constant")
    if anchor["head_hash"] != GENESIS_HASH:
        _reject("anchor-genesis-head",
                "sequence-0 head_hash must be the genesis constant")
    return AnchorFacts(sha256=hashlib.sha256(data).hexdigest(),
                       size_bytes=len(data),
                       algorithm=anchor["algorithm"],
                       sequence=anchor["sequence"],
                       anchor_hash=anchor["anchor_hash"],
                       anchored_at=anchored_at)


# ---------------------------------------------------------------- 报告校验


@dataclass(frozen=True)
class ReportFacts:
    """verify 成功报告装载并绑定锚 SHA-256 后的事实快照。"""

    sha256: str
    size_bytes: int
    ended_at: datetime


def _check_sidecar(report_path_str: str, report_bytes: bytes,
                   label: str) -> None:
    """sidecar ``<report>.json.sha256``：文件门 + 首 token 摘要一致。"""
    sidecar_bytes = _read_input_file(report_path_str + ".sha256",
                                     f"{label}-sidecar")
    try:
        text = sidecar_bytes.decode("utf-8")
    except UnicodeDecodeError:
        _reject(f"{label}-sidecar-not-utf8",
                f"{label} sidecar is not valid UTF-8")
    tokens = text.split()
    if not tokens or _HEX64.fullmatch(tokens[0]) is None:
        _reject(f"{label}-sidecar-format",
                f"{label} sidecar must start with a 64-hex digest token")
    digest = hashlib.sha256(report_bytes).hexdigest()
    if tokens[0] != digest:
        _reject(f"{label}-sidecar-mismatch",
                f"{label} sidecar digest does not match the report bytes")


def _load_report(data: bytes, *, label: str, flag: str,
                 anchor_sha256: str) -> ReportFacts:
    """verify 成功报告校验 + 绑定同一锚 SHA-256。"""
    try:
        raw = _strict_json_loads(data)
    except ValueError:
        _reject(f"{label}-not-strict-json",
                f"{label} report is not strict JSON (duplicate keys and "
                "NaN/Infinity are rejected)")
    if not isinstance(raw, dict):
        _reject(f"{label}-not-object",
                f"{label} report is not a JSON object")
    if raw.get("status") != "pass":
        _reject(f"{label}-status-not-pass",
                f"{label} report status must be pass")
    if raw.get("problems") != []:
        _reject(f"{label}-problems-not-empty",
                f"{label} report must carry an empty problems list")
    if raw.get(flag) is not True:
        _reject(f"{label}-{flag}-not-true",
                f"{label} report must have {flag}=true")
    ended_raw = raw.get("ended_at_utc")
    if not isinstance(ended_raw, str) or not ended_raw:
        _reject(f"{label}-ended-at-missing",
                f"{label} report is missing ended_at_utc")
    ended_at = _parse_iso_utc(ended_raw)
    if ended_at is None:
        _reject(f"{label}-ended-at-invalid",
                f"{label} report ended_at_utc is not a tz-aware ISO-8601 "
                "timestamp")
    source = raw.get("source")
    if not isinstance(source, dict):
        _reject(f"{label}-source-missing",
                f"{label} report is missing the source facts block")
    if source.get("sha256") != anchor_sha256:
        _reject(f"{label}-anchor-binding-mismatch",
                f"{label} report source.sha256 does not bind the given "
                "anchor file")
    return ReportFacts(sha256=hashlib.sha256(data).hexdigest(),
                       size_bytes=len(data), ended_at=ended_at)


# ---------------------------------------------------------------- 时序


def _enforce_ordering(anchor_at: datetime, worm_end: datetime,
                      offline_end: datetime, now: datetime) -> None:
    """anchored_at ≤ WORM end ≤ 离线 end，且任何时刻不得晚于 now。"""
    if anchor_at > worm_end:
        _reject("ordering-anchor-after-worm",
                "anchor anchored_at must not be after the WORM report end")
    if worm_end > offline_end:
        _reject("ordering-worm-after-offline",
                "WORM report end must not be after the offline report end")
    if anchor_at > now:
        _reject("future-anchor-anchored-at",
                "anchor anchored_at is in the future relative to --now")
    if worm_end > now:
        _reject("future-worm-ended-at",
                "WORM report ended_at_utc is in the future relative to --now")
    if offline_end > now:
        _reject("future-offline-ended-at",
                "offline report ended_at_utc is in the future relative to "
                "--now")


# ---------------------------------------------------------------- plan


def _build_plan(now: datetime, anchor: AnchorFacts, worm: ReportFacts,
                offline: ReportFacts) -> dict:
    """确定性 plan 载荷（纯输入的函数：零时钟、零路径、零随机）。"""
    state = {
        "schema_version": 1,
        "tasks": {
            "anchor": {
                "last_success_at": _format_utc(anchor.anchored_at),
                "source_sha256": anchor.sha256,
                "facts": {"algorithm": anchor.algorithm,
                          "anchor_hash": anchor.anchor_hash,
                          "sequence": anchor.sequence},
            },
            "worm_archive": {
                "last_success_at": _format_utc(worm.ended_at),
                "source_sha256": anchor.sha256,
                "facts": {"report_sha256": worm.sha256,
                          "report_size_bytes": worm.size_bytes},
            },
            "offline_copy": {
                "last_success_at": _format_utc(offline.ended_at),
                "source_sha256": anchor.sha256,
                "facts": {"report_sha256": offline.sha256,
                          "report_size_bytes": offline.size_bytes},
            },
        },
    }
    return {
        "schema_version": 1,
        "milestone": MILESTONE,
        "tool": TOOL_NAME,
        "command": "plan",
        "status": "pass",
        "problems": [],
        "now": _format_utc(now),
        "anchor": {"sha256": anchor.sha256,
                   "size_bytes": anchor.size_bytes,
                   "algorithm": anchor.algorithm,
                   "sequence": anchor.sequence,
                   "anchor_hash": anchor.anchor_hash,
                   "anchored_at": _format_utc(anchor.anchored_at)},
        "policy": POLICY_DOCUMENT,
        "policy_bytes": POLICY_BYTES.decode("utf-8"),
        "state": state,
    }


def _validate_inputs(args) -> tuple[datetime, AnchorFacts, ReportFacts,
                                    ReportFacts]:
    """plan/apply 共用的完整输入校验链（顺序即防线，首个失败即短路）。"""
    now = _parse_iso_utc(args.now)
    if now is None:
        _reject("now-invalid",
                "--now is not a tz-aware ISO-8601 timestamp")
    anchor_data = _read_input_file(args.anchor, "anchor")
    anchor = _load_anchor(anchor_data)
    worm_data = _read_input_file(args.worm_report, "worm-report")
    _check_sidecar(args.worm_report, worm_data, "worm")
    worm = _load_report(worm_data, label="worm-report",
                        flag=REPORT_FLAGS["worm"],
                        anchor_sha256=anchor.sha256)
    offline_data = _read_input_file(args.offline_report, "offline-report")
    _check_sidecar(args.offline_report, offline_data, "offline")
    offline = _load_report(offline_data, label="offline-report",
                           flag=REPORT_FLAGS["offline"],
                           anchor_sha256=anchor.sha256)
    _enforce_ordering(anchor.anchored_at, worm.ended_at, offline.ended_at,
                      now)
    return now, anchor, worm, offline


def _cmd_plan(args) -> int:
    now, anchor, worm, offline = _validate_inputs(args)
    _print_json(_build_plan(now, anchor, worm, offline))
    return EXIT_OK


# ---------------------------------------------------------------- apply

#: apply 的人肉确认语——必须逐字一致，任何其他值在输出访问前拒绝
CONFIRM_PHRASE = "EXECUTE AUDIT ARCHIVE INPUT UPDATE"

#: state 文档三任务名（与 M14-50 调度器 / _build_plan 同源）
STATE_TASK_NAMES = ("anchor", "worm_archive", "offline_copy")


def _preflight_output_path(path_str: str, label: str,
                           forbidden: set[Path]) -> Path:
    """输出路径门：非 symlink、非目录、父目录存在、不撞禁用解析路径。"""
    path = Path(path_str)
    if path.is_symlink():
        _reject(f"apply-{label}-output-symlink",
                f"{label} output path must not be a symlink")
    if not path.parent.is_dir():
        _reject(f"apply-{label}-output-parent-missing",
                f"{label} output parent directory does not exist")
    if path.is_dir():
        _reject(f"apply-{label}-output-is-directory",
                f"{label} output path is an existing directory")
    if path.exists() and not path.is_file():
        _reject(f"apply-{label}-output-not-regular",
                f"{label} output exists but is not a regular file")
    resolved = path.resolve()
    if resolved in forbidden:
        _reject("apply-output-collision",
                f"{label} output resolves to an input, sidecar, or the "
                "other output path")
    return resolved


def _parse_existing_state(data: bytes) -> dict:
    """既有 state 输出的 strict 装载 + schema 1 三任务形状门。"""
    if len(data) > MAX_INPUT_BYTES:
        _reject("apply-state-too-large",
                "existing state output exceeds the 1 MiB cap")
    try:
        state = _strict_json_loads(data)
    except ValueError:
        _reject("apply-state-not-strict-json",
                "existing state is not strict JSON (duplicate keys and "
                "NaN/Infinity are rejected)")
    if not isinstance(state, dict) \
            or type(state.get("schema_version")) is not int \
            or state["schema_version"] != 1:
        _reject("apply-state-shape",
                "existing state does not match the schema-1 three-task "
                "shape")
    tasks = state.get("tasks")
    if not isinstance(tasks, dict) or set(tasks) != set(STATE_TASK_NAMES):
        _reject("apply-state-shape",
                "existing state does not match the schema-1 three-task "
                "shape")
    task_fields = {"last_success_at", "source_sha256", "facts"}
    for name in STATE_TASK_NAMES:
        task = tasks[name]
        if not isinstance(task, dict) or set(task) != task_fields:
            _reject("apply-state-shape",
                    "existing state does not match the schema-1 three-task "
                    "shape")
        if not isinstance(task["source_sha256"], str) \
                or not isinstance(task["facts"], dict):
            _reject("apply-state-shape",
                    "existing state does not match the schema-1 three-task "
                    "shape")
        last = task["last_success_at"]
        if not isinstance(last, str) or _parse_iso_utc(last) is None:
            _reject("apply-state-shape",
                    "existing state does not match the schema-1 three-task "
                    "shape")
    return state


def _enforce_no_state_rollback(existing: dict, planned: dict) -> None:
    """任一新任务 last_success_at 早于既有同任务时刻即拒绝。"""
    for name in STATE_TASK_NAMES:
        existing_at = _parse_iso_utc(
            existing["tasks"][name]["last_success_at"])
        planned_at = _parse_iso_utc(
            planned["tasks"][name]["last_success_at"])
        if planned_at < existing_at:
            _reject("apply-state-rollback",
                    "new state would set a task last_success_at earlier "
                    "than in the existing state output")


def _atomic_write(target: Path, data: bytes) -> None:
    """单目标原子写：目标父目录 tmp → 字节 → flush+fsync → 原子替换。

    逐文件原子；失败路径（含替换失败）清理本 tmp 后上抛，交由 main
    折叠为终防线 exit 2。
    """
    handle_fd, tmp_name = tempfile.mkstemp(
        dir=target.parent, prefix=".audit-archive-input-", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(handle_fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def _cmd_apply(args) -> int:
    if args.confirm != CONFIRM_PHRASE:
        _reject("apply-confirm-phrase",
                "--confirm must be exactly EXECUTE AUDIT ARCHIVE INPUT "
                "UPDATE")
    now, anchor, worm, offline = _validate_inputs(args)
    plan = _build_plan(now, anchor, worm, offline)
    forbidden = {Path(args.anchor).resolve(),
                 Path(args.worm_report).resolve(),
                 Path(args.offline_report).resolve(),
                 Path(args.worm_report + ".sha256").resolve(),
                 Path(args.offline_report + ".sha256").resolve()}
    policy_path = _preflight_output_path(args.policy_output, "policy",
                                         forbidden)
    forbidden.add(policy_path)
    state_path = _preflight_output_path(args.state_output, "state",
                                        forbidden)

    policy_already = False
    if policy_path.exists():
        try:
            existing_policy = policy_path.read_bytes()
        except OSError:
            _reject("apply-policy-unreadable",
                    "existing policy output is unreadable")
        if existing_policy != POLICY_BYTES:
            _reject("apply-policy-drift",
                    "existing policy output bytes differ from the "
                    "canonical policy document")
        policy_already = True

    state_bytes = _canonical_json_bytes(plan["state"])
    state_already = False
    if state_path.exists():
        try:
            existing_state_bytes = state_path.read_bytes()
        except OSError:
            _reject("apply-state-unreadable",
                    "existing state output is unreadable")
        existing_state = _parse_existing_state(existing_state_bytes)
        _enforce_no_state_rollback(existing_state, plan["state"])
        state_already = existing_state_bytes == state_bytes

    if not policy_already:
        _atomic_write(policy_path, POLICY_BYTES)
    if not state_already:
        _atomic_write(state_path, state_bytes)
    _print_json({
        "schema_version": 1,
        "milestone": MILESTONE,
        "tool": TOOL_NAME,
        "command": "apply",
        "status": "pass",
        "problems": [],
        "now": plan["now"],
        "anchor": plan["anchor"],
        "policy": {"already_matched": policy_already,
                   "written": not policy_already},
        "state": {"already_matched": state_already,
                  "written": not state_already},
        "cross_file_transaction": False,
    })
    return EXIT_OK


# ---------------------------------------------------------------- CLI


def _print_json(payload: dict) -> None:
    """stdout 确定性序列化（sort_keys + 单尾换行；全文 ASCII）。"""
    sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False,
                                sort_keys=True) + "\n")


def _fail_payload(command: str | None, rejection: _PlanReject) -> dict:
    return {"schema_version": 1, "milestone": MILESTONE, "tool": TOOL_NAME,
            "command": command, "status": "fail",
            "problems": [{"code": rejection.code,
                          "detail": rejection.detail}]}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audit_archive_input.py",
        description="M14-55 审计归档输入更新器（plan 只读计算 / apply "
                    "原子落盘；零发现/零网络/零 env/零子进程/零 DB/零 "
                    "S3/零 WORM/零离线根/零调度器访问）")
    sub = parser.add_subparsers(dest="command")
    plan = sub.add_parser("plan", help="校验输入并打印确定性 plan JSON"
                          "（零文件写入）")
    plan.add_argument("--anchor", required=True, metavar="PATH",
                      help="审计锚点 JSONL 文件（恰一行 sequence=0 锚）")
    plan.add_argument("--worm-report", required=True, metavar="PATH",
                      help="M14-43 WORM verify 成功报告 JSON"
                      "（须含 .json.sha256 sidecar）")
    plan.add_argument("--offline-report", required=True, metavar="PATH",
                      help="M14-49 离线副本 verify 成功报告 JSON"
                      "（须含 .json.sha256 sidecar）")
    plan.add_argument("--now", required=True, metavar="ISO8601",
                      help="当前时刻（tz-aware ISO-8601；显式注入，"
                      "零时钟读取）")
    apply_cmd = sub.add_parser(
        "apply", help="同一校验链通过后原子落盘 policy/state（逐文件"
        "原子，不承诺跨文件事务）")
    apply_cmd.add_argument("--anchor", required=True, metavar="PATH",
                           help="审计锚点 JSONL 文件（恰一行 sequence=0 锚）")
    apply_cmd.add_argument("--worm-report", required=True, metavar="PATH",
                           help="M14-43 WORM verify 成功报告 JSON"
                           "（须含 .json.sha256 sidecar）")
    apply_cmd.add_argument("--offline-report", required=True, metavar="PATH",
                           help="M14-49 离线副本 verify 成功报告 JSON"
                           "（须含 .json.sha256 sidecar）")
    apply_cmd.add_argument("--now", required=True, metavar="ISO8601",
                           help="当前时刻（tz-aware ISO-8601；显式注入，"
                           "零时钟读取）")
    apply_cmd.add_argument("--policy-output", required=True, metavar="PATH",
                           help="policy 落盘路径（已存在时须与 canonical "
                           "policy 字节逐字节一致）")
    apply_cmd.add_argument("--state-output", required=True, metavar="PATH",
                           help="state 落盘路径（已存在时须 schema 1 三"
                           "任务形状且无时刻回滚）")
    apply_cmd.add_argument(
        "--confirm", required=True,
        metavar="EXECUTE AUDIT ARCHIVE INPUT UPDATE",
        help="确认知语，必须逐字为 EXECUTE AUDIT ARCHIVE INPUT UPDATE")
    return parser


def main(argv=None) -> int:
    """CLI 入口。受控拒绝 → fail JSON + exit 2；意外异常折叠为 exit 2
    （stderr 只记异常类型名，绝不上抛原始 traceback）。"""
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "plan":
            return _cmd_plan(args)
        if args.command == "apply":
            return _cmd_apply(args)
    except _PlanReject as rejection:
        _print_json(_fail_payload(args.command, rejection))
        return EXIT_REJECT
    except Exception as exc:  # noqa: BLE001 —— 终防线 fail-closed（类型名之外零信息）
        print(f"{TOOL_NAME}: unexpected {type(exc).__name__}; fail-closed "
              "exit 2", file=sys.stderr)
        return EXIT_REJECT
    return EXIT_REJECT


if __name__ == "__main__":
    sys.exit(main())
