#!/usr/bin/env python
"""M14-79 长稳窗口锚定/重启门：history.jsonl 尾部连续 ok 前置条件满足才
允许锚定一个全新 24h soak 窗口；warn/critical/partial 仍留在尾部时必须
拒绝并逐条说明原因——绝不带病开窗、绝不改写历史、绝不伪造窗口起点。

背景事实（2026-09-21 生产）：M14-79 soak 基线审计
（``.verify/artifacts/m14-79-soak-baseline/``，锚点 2026-09-21T04:15:01Z）
blocked——窗口内 55 ok / 2 warn / 3 critical（non-ok-status-in-window）、
最大间隔 403.45 分钟；当前 history 尾部样本仍为 warn（容器重建告警 →
postgres error_total=6）。M14-73 起长稳门 long-soak 需要真实连续 24h
干净窗口；本工具把「何时允许重启一个干净窗口」从人工判断固化为可审计
的门禁：只有当尾部 N 个连续样本全部 ok 且 partial=false、相邻间隔 ≤
max-gap 时，才允许把**最新样本时间戳**锚定为新窗口起点（零墙钟——
锚点取自样本，不取自系统钟）。锚定记录（soak-window-anchor.json/.md）
是新窗口起点的唯一权威事实；24h 后由既有
``tools/ops/soak_stability_audit.py`` 判定该窗口是否真实达成——本工具
**不判定也不预示 soak 结果**，更不构成任何发布门通过。

设计（与 tools/ops/soak_stability_audit.py 同款纪律：单文件、纯标准库、
零第三方依赖；一切 I/O 经 Store 注入；零子进程/零网络/零计划任务/
零 env 读取/零墙钟；本工具只读 history 输入、只写输出目录）：

- 输入（固定画像）：默认 canonical gitignored
  ``.verify/artifacts/m14-13-monitoring-history/history.jsonl``（可用
  ``--history`` 覆盖为该文件本身或包含它的目录）。逐行严格解析（五字段
  schema，复用 monitoring_history 单一事实源）；全局时序/时间戳唯一/
  项目单一；malformed / 越词汇 / 非时序 / 重复 / 冲突 / 行数超硬顶 →
  fail-closed 拒绝（零输出）。留存契约 ``--retention``（默认 500、
  1-5000，与 monitoring_history 一致）：分析面取最新 N 行，更早行仅计
  ``omitted_older_count``；全局校验覆盖全部行。
- 门条件（纯函数，全部满足才 open）：
  1. 行数 ≥ ``--consecutive-ok N``（默认 8，1-5000）——不足即 closed
     （insufficient-history）；
  2. 尾部 N 行全部 overall_status=ok 且 partial=false——任何 warn/
     critical/partial 留在尾部即 closed（non-ok-in-tail），拒绝原因
     **逐条列出**每个非干净样本（collected_at + overall_status +
     partial——固定字段，绝无原文日志）；
  3. 尾部 N 行相邻间隔 ≤ ``--max-gap-minutes``（默认 20，须 ≥ 1）——
     超限即 closed（excessive-gap-in-tail，附最大观测间隔）。
- 锚定/重启工作流：
  - 检查模式（默认）：评估门并落盘门报告 ``soak-window-gate.json/.md``
    （open → exit 0；closed → exit 1——**可见结论，非崩溃**）。
  - ``--anchor``：门 open 时额外原子写出锚定记录
    ``soak-window-anchor.json/.md``（锚点 = 最新样本 collected_at、
    窗口设置、尾部指纹、最早可判定时间 = 锚点 + window、后续审计命令
    固定指引）；门 closed 时**只落门报告**（含逐条拒绝原因）、不写
    锚定记录、exit 1。
  - 重启保护：锚定记录已存在 → fail-closed 拒绝（anchor-exists，零
    写入，exit 2）——重启窗口必须由操作者显式归档/移走旧锚定记录后再
    锚定，防止静默重锚掩盖已破坏的窗口。
- 零墙钟：门报告与锚定记录的全部时间戳取自样本（生成锚 = 最新样本
  collected_at；最早可判定时间 = 锚点 + window 由样本时间推导）——
  两次运行输出逐字节相同。
- 退出码：0 门 open（--anchor 时锚定记录已写出）；1 门 closed（门报告
  已落盘、锚定记录零写出）；2 输入拒绝/参数越界/锚定记录已存在/写失败
  （零写入或输出保持原子）。
- 输出（默认 gitignored
  ``.verify/artifacts/m14-79-soak-window-anchor/``）：确定性 JSON +
  Markdown（仅状态/时间戳/计数/固定词汇原因/sha256——绝无原始日志行/
  密钥/secret/env 值/URL/token/主机标识）。tmp + fsync + os.replace
  原子落盘；symlink/越界路径一律拒绝；仅在全部输入校验与门评估完成
  之后才写任何输出。
- 诚实边界：锚定 ≠ soak 通过。新窗口在锚点后 24h 内出现任何非干净
  样本或间隔超限，最终审计仍会 blocked——门只保证「开窗时尾部干净」，
  不预示窗口结局；``release_ready=false`` / ``production_ready=false``
  不变；本工具零生产触碰。

用法（仓库根）：
  python tools/ops/soak_window_gate.py                     # 检查模式
  python tools/ops/soak_window_gate.py --anchor            # 门 open 时锚定
  python tools/ops/soak_window_gate.py --history <dir-or-file> --consecutive-ok 8
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

try:  # 直接脚本运行：tools/ops 自身在 sys.path
    import monitoring_history as _history
except ImportError:  # 经 importlib 按路径加载（契约测试形态）
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import monitoring_history as _history

EXIT_OPEN = 0
#: 1 = 门 closed（可见结论：门报告落盘、锚定记录零写出）
EXIT_CLOSED = 1
#: 2 = 输入拒绝/参数越界/锚定已存在/写失败（零写入）
EXIT_REFUSED = 2

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HISTORY_PATH = (REPO_ROOT / ".verify" / "artifacts"
                        / "m14-13-monitoring-history" / "history.jsonl")
DEFAULT_OUTPUT_DIR = (REPO_ROOT / ".verify" / "artifacts"
                      / "m14-79-soak-window-anchor")
HISTORY_FILE_NAME = "history.jsonl"

GATE_JSON_NAME = "soak-window-gate.json"
GATE_MD_NAME = "soak-window-gate.md"
ANCHOR_JSON_NAME = "soak-window-anchor.json"
ANCHOR_MD_NAME = "soak-window-anchor.md"

TAG = "[soak-window-gate]"
GATE_SCHEMA_VERSION = 1
ANCHOR_SCHEMA_VERSION = 1
TOOL_NAME = "tools/ops/soak_window_gate.py"
#: 后续审计固定指引（与 soak_stability_audit 默认口径一致）
AUDIT_TOOL_NAME = "tools/ops/soak_stability_audit.py"

#: 门状态与固定词汇原因（绝不携带文件内容文本）
GATE_OPEN = "open"
GATE_CLOSED = "closed"

#: 参数：尾部连续 ok 样本数默认 8（15 分钟节奏 = 2 小时干净尾部）；
#: 窗口默认 24h；尾部相邻间隔上限默认 20 分钟（与 soak 审计 max-gap 同口径）
DEFAULT_CONSECUTIVE_OK = 8
MIN_CONSECUTIVE_OK = 1
MAX_CONSECUTIVE_OK = 5000
DEFAULT_WINDOW_MINUTES = 1440
DEFAULT_MAX_GAP_MINUTES = 20

#: 留存契约复用 monitoring_history（默认 500 / 1-5000）
DEFAULT_RETENTION = _history.DEFAULT_RETENTION
MIN_RETENTION = _history.MIN_RETENTION
MAX_RETENTION = _history.MAX_RETENTION


class GateError(RuntimeError):
    """fail-closed 拒绝（固定词汇原因；绝不携带文件内容文本）。"""


# ---------------------------------------------------------------- 输入解析


@dataclass(frozen=True)
class HistoryRow:
    """已验证的历史行（仅门评估所需五字段；多余字段绝不进入任何输出）。"""

    collected_dt: object  # datetime（复用 monitoring_history.parse_timestamp）
    collected_at: str
    project: str
    overall_status: str
    partial: bool


def _require_mapping(value: object, reason: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise GateError(reason)
    return value


def validate_row(data: object, line_number: int) -> HistoryRow:
    """严格校验单行（与 soak_stability_audit 同款五字段面）。"""
    row = _require_mapping(data, f"malformed-row:{line_number}:not-an-object")
    if row.get("schema_version") != _history.HISTORY_SCHEMA_VERSION:
        raise GateError(f"malformed-row:{line_number}:schema-version")
    collected_at = row.get("collected_at")
    try:
        collected_dt = _history.parse_timestamp(collected_at)
    except _history.HistoryError:
        raise GateError(f"malformed-row:{line_number}:collected-at") from None
    project = row.get("project")
    if not isinstance(project, str) or _history.PROJECT_NAME_RE.match(project) is None:
        raise GateError(f"malformed-row:{line_number}:project")
    overall = row.get("overall_status")
    if (not isinstance(overall, str)
            or overall not in _history.ACCEPTED_OVERALL_STATUSES):
        raise GateError(f"malformed-row:{line_number}:overall-status")
    partial = row.get("partial")
    if not isinstance(partial, bool):
        raise GateError(f"malformed-row:{line_number}:partial")
    return HistoryRow(collected_dt=collected_dt, collected_at=str(collected_at),
                      project=project, overall_status=overall, partial=partial)


def resolve_history_path(store: _history.Store, history: Path) -> Path:
    """--history 接受文件本身或包含 history.jsonl 的目录；symlink 一律拒绝。"""
    if not store.exists(history):
        raise GateError("history-path-missing")
    _history.reject_symlinked_path(store, history)
    if store.is_symlink(history):
        raise GateError("symlink-target")
    if history.name != HISTORY_FILE_NAME:
        candidate = history / HISTORY_FILE_NAME
        if not store.exists(candidate):
            raise GateError("history-path-missing")
        _history.reject_symlinked_path(store, candidate)
        return candidate
    return history


def parse_history(raw: bytes) -> list[HistoryRow]:
    """逐行严格解析（UTF-8 严格解码；空文件 → empty-history 拒绝）。"""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise GateError("history-not-utf8") from None
    lines = text.splitlines()
    if not lines:
        raise GateError("empty-history")
    rows: list[HistoryRow] = []
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            raise GateError(f"malformed-row:{index}:empty-line")
        try:
            data = json.loads(line)
        except ValueError:
            raise GateError(f"malformed-row:{index}:not-json") from None
        rows.append(validate_row(data, index))
    if len(rows) > MAX_RETENTION:
        raise GateError("row-limit-exceeded")
    return rows


def validate_global(rows: list[HistoryRow]) -> None:
    """全局时序（升序）、时间戳唯一、项目单一；违规 fail-closed。"""
    previous_dt = None
    seen_at: set[str] = set()
    for row in rows:
        if row.collected_at in seen_at:
            raise GateError("duplicate-timestamp")
        seen_at.add(row.collected_at)
        if previous_dt is not None and row.collected_dt < previous_dt:  # type: ignore[operator]
            raise GateError("non-chronological-history")
        previous_dt = row.collected_dt
    if len({row.project for row in rows}) > 1:
        raise GateError("conflicting-project")


# ---------------------------------------------------------------- 门评估（纯）


@dataclass(frozen=True)
class NonOkSample:
    """尾部非干净样本的固定字段说明（时间戳/状态/partial——安全事实）。"""

    collected_at: str
    overall_status: str
    partial: bool


@dataclass(frozen=True)
class GateResult:
    status: str  # open / closed
    reasons: tuple[str, ...]
    anchor_collected_at: str
    tail_sample_count: int
    tail_non_ok: tuple[NonOkSample, ...]
    tail_max_gap_minutes: float
    tail_span_minutes: float
    earliest_audit_collected_at: str | None


def evaluate_gate(rows: list[HistoryRow], *, consecutive_ok: int,
                  max_gap_minutes: int, window_minutes: int) -> GateResult:
    """纯门评估：尾部 N 行全 ok/partial=false 且相邻间隔 ≤ max-gap 才 open。

    拒绝原因固定词汇；non-ok-in-tail 附逐条非干净样本清单（collected_at +
    overall_status + partial）。锚点恒为最新样本——零墙钟。"""
    anchor = rows[-1]
    tail = rows[len(rows) - consecutive_ok:] if len(rows) > consecutive_ok else rows
    reasons: list[str] = []
    non_ok = tuple(NonOkSample(row.collected_at, row.overall_status, row.partial)
                   for row in tail
                   if row.overall_status != "ok" or row.partial)
    if len(rows) < consecutive_ok:
        reasons.append("insufficient-history")
    if non_ok:
        reasons.append("non-ok-in-tail")
    max_gap = 0.0
    for first, second in itertools.pairwise(tail):
        gap = ((second.collected_dt - first.collected_dt).total_seconds()  # type: ignore[operator]
               / 60.0)
        max_gap = max(max_gap, gap)
    if len(tail) >= 2 and max_gap > max_gap_minutes:
        reasons.append("excessive-gap-in-tail")
    span = (((tail[-1].collected_dt - tail[0].collected_dt).total_seconds()  # type: ignore[operator]
             / 60.0) if len(tail) >= 2 else 0.0)
    status = GATE_OPEN if not reasons else GATE_CLOSED
    earliest = None
    if status == GATE_OPEN:
        earliest_dt = anchor.collected_dt + timedelta(minutes=window_minutes)  # type: ignore[operator]
        earliest = _history.datetime.strftime(earliest_dt,
                                              _history.TIMESTAMP_FORMAT)  # type: ignore[arg-type]
    return GateResult(
        status=status, reasons=tuple(reasons),
        anchor_collected_at=anchor.collected_at,
        tail_sample_count=len(tail), tail_non_ok=non_ok,
        tail_max_gap_minutes=max_gap, tail_span_minutes=span,
        earliest_audit_collected_at=earliest,
    )


# ---------------------------------------------------------------- 报告（纯）


GATE_BOUNDARIES: tuple[str, ...] = (
    (
        "the gate only certifies a clean tail at open time; it does not predict or imply the 24h soak outcome — any non-clean sample or excessive gap after the anchor still yields a blocked audit"
    ),
    (
        "the anchor timestamp is the latest history sample, never the system clock: zero wall clock, byte-identical outputs across reruns"
    ),
    (
        "refusal lists every warn/critical/partial sample remaining in the tail with collected_at/status/partial only — failures are explained, never masked and never rewritten"
    ),
    (
        "an existing anchor record blocks re-anchoring (anchor-exists); restarting a window requires the operator to explicitly archive the previous anchor"
    ),
    (
        "read-only over history input; zero child-process spawns, zero network, zero scheduler mutation, zero env reads, zero production touch"
    ),
    (
        "anchoring is not a release gate pass: release_ready and production_ready stay false; release approval remains human-only"
    ),
)


def build_gate_report(*, input_sha256: str, input_bytes: int, row_count: int,
                      analyzed_count: int, omitted_older: int,
                      consecutive_ok: int, max_gap_minutes: int,
                      window_minutes: int, retention: int,
                      result: GateResult) -> dict[str, object]:
    return {
        "schema_version": GATE_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "input": {"sha256": input_sha256, "byte_size": input_bytes},
        "row_count": row_count,
        "analyzed_row_count": analyzed_count,
        "omitted_older_count": omitted_older,
        "settings": {
            "consecutive_ok": consecutive_ok,
            "max_gap_minutes": max_gap_minutes,
            "window_minutes": window_minutes,
            "retention": retention,
        },
        "status": result.status,
        "reasons": list(result.reasons),
        "anchor_collected_at": result.anchor_collected_at,
        "tail_sample_count": result.tail_sample_count,
        "tail_non_ok_samples": [
            {"collected_at": s.collected_at, "overall_status": s.overall_status,
             "partial": s.partial} for s in result.tail_non_ok
        ],
        "tail_max_gap_minutes": round(result.tail_max_gap_minutes, 3),
        "tail_span_minutes": round(result.tail_span_minutes, 3),
        "earliest_audit_collected_at": result.earliest_audit_collected_at,
        "boundaries": list(GATE_BOUNDARIES),
    }


def render_gate_markdown(report: dict[str, object]) -> str:
    settings = report["settings"]
    assert isinstance(settings, dict)
    input_info = report["input"]
    assert isinstance(input_info, dict)
    lines: list[str] = [
        f"# M14-79 长稳窗口锚定门（schema_version={report['schema_version']}）",
        "",
        f"- 门状态：**{report['status']}**"
        + (f"（原因：{', '.join(report['reasons'])}）" if report["reasons"] else ""),
        f"- 输入：SHA-256 `{input_info['sha256']}`，{input_info['byte_size']} bytes",
        (f"- 行数：总 {report['row_count']} / 分析 {report['analyzed_row_count']}"
         f"（省略更早 {report['omitted_older_count']} 条）"),
        (f"- 设置：尾部连续 ok {settings['consecutive_ok']} / 尾部间隔上限 "
         f"{settings['max_gap_minutes']} 分钟 / 窗口 {settings['window_minutes']} 分钟"
         f" / 留存 {settings['retention']}"),
        (f"- 锚点（最新样本，零墙钟）：{report['anchor_collected_at']}；"
         f"尾部 {report['tail_sample_count']} 行，最大间隔 "
         f"{report['tail_max_gap_minutes']} 分钟，跨度 "
         f"{report['tail_span_minutes']} 分钟"),
    ]
    non_ok = report["tail_non_ok_samples"]
    assert isinstance(non_ok, list)
    if non_ok:
        lines += ["", "尾部残留非干净样本（逐条，拒绝即因此）：", ""]
        for sample in non_ok:
            assert isinstance(sample, dict)
            lines.append(f"- {sample['collected_at']} — "
                         f"{sample['overall_status']}"
                         + ("（partial=true）" if sample["partial"] else ""))
    if report["earliest_audit_collected_at"]:
        lines += ["",
                  (f"- 最早可判定时间（锚点 + 窗口，样本推导）："
                   f"{report['earliest_audit_collected_at']}"),
                  (f"- 后续审计命令（固定指引）：`python {AUDIT_TOOL_NAME}`"
                   "（其 soak-audit-report.json 逐字节复制为 long-soak.json "
                   "才构成门证据）")]
    lines += ["", "边界："] + [f"- {item}" for item in report["boundaries"]]
    return "\n".join(lines) + "\n"


def build_anchor_record(report: dict[str, object]) -> dict[str, object]:
    """锚定记录：新窗口起点的唯一权威事实（门 open 才可生成）。"""
    settings = report["settings"]
    assert isinstance(settings, dict)
    non_ok = report["tail_non_ok_samples"]
    assert isinstance(non_ok, list)
    return {
        "schema_version": ANCHOR_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "anchor_collected_at": report["anchor_collected_at"],
        "window_minutes": settings["window_minutes"],
        "earliest_audit_collected_at": report["earliest_audit_collected_at"],
        "precondition": {
            "consecutive_ok": settings["consecutive_ok"],
            "max_gap_minutes": settings["max_gap_minutes"],
            "tail_sample_count": report["tail_sample_count"],
            "tail_non_ok_count": len(non_ok),
            "tail_max_gap_minutes": report["tail_max_gap_minutes"],
            "input_sha256": (report["input"] or {}).get("sha256")
            if isinstance(report["input"], dict) else None,
        },
        "follow_up_audit_tool": AUDIT_TOOL_NAME,
        "generated_at": report["anchor_collected_at"],  # 零墙钟：锚样本即生成锚
        "boundaries": list(GATE_BOUNDARIES),
    }


def render_anchor_markdown(anchor: dict[str, object]) -> str:
    precondition = anchor["precondition"]
    assert isinstance(precondition, dict)
    lines: list[str] = [
        f"# M14-79 长稳窗口锚定记录（schema_version={anchor['schema_version']}）",
        "",
        f"- 锚点（新窗口起点，零墙钟）：**{anchor['anchor_collected_at']}**",
        (f"- 窗口：{anchor['window_minutes']} 分钟；最早可判定时间："
         f"{anchor['earliest_audit_collected_at']}"),
        (f"- 开窗前置：尾部连续 ok {precondition['consecutive_ok']}"
         f"（实测 {precondition['tail_sample_count']} 行全 ok、"
         f"非干净 {precondition['tail_non_ok_count']}、最大间隔 "
         f"{precondition['tail_max_gap_minutes']} 分钟、间隔上限 "
         f"{precondition['max_gap_minutes']} 分钟）"),
        (f"- 输入指纹：SHA-256 `{precondition['input_sha256']}`"),
        (f"- 后续审计：`python {anchor['follow_up_audit_tool']}`——锚定不预示"
         "窗口结局，最终以审计报告为准"),
        "",
        "边界：",
        "- 锚定 ≠ soak 通过；release_ready=false / production_ready=false 不变",
        "- 本记录由 soak_window_gate 在门 open 时原子写出；重启窗口须先归档本记录",
        "- 零子进程/零网络/零计划任务/零 env 读取/零墙钟/零生产触碰",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 写出（原子）


def write_pair(store: _history.Store, output_dir: Path, json_name: str,
               md_name: str, json_text: str, md_text: str) -> tuple[Path, Path]:
    """同目录双文件原子落盘；先于 mkdir 拒绝目录/祖先 symlink，mkdir 后
    复查目标（TOCTOU 防御）。"""
    _history.reject_symlinked_path(store, output_dir)
    store.mkdirs(output_dir)
    json_path = output_dir / json_name
    md_path = output_dir / md_name
    _history.reject_symlinked_path(store, json_path, md_path, output_dir)
    store.write_atomic(json_path, json_text)
    store.write_atomic(md_path, md_text)
    return json_path, md_path


# ---------------------------------------------------------------- CLI


def validate_settings(*, consecutive_ok: int, max_gap_minutes: int,
                      window_minutes: int, retention: int) -> str | None:
    if not MIN_CONSECUTIVE_OK <= consecutive_ok <= MAX_CONSECUTIVE_OK:
        return (f"consecutive-ok 必须在 {MIN_CONSECUTIVE_OK}-{MAX_CONSECUTIVE_OK}"
                f"（收到 {consecutive_ok}）")
    if consecutive_ok > retention:
        return (f"consecutive-ok（{consecutive_ok}）不得超过 retention"
                f"（{retention}）——尾部 N 行必须落在分析留存内")
    if max_gap_minutes < 1:
        return f"max-gap-minutes 必须为正（收到 {max_gap_minutes}）"
    if window_minutes <= 0:
        return f"window-minutes 必须为正（收到 {window_minutes}）"
    if not MIN_RETENTION <= retention <= MAX_RETENTION:
        return f"retention 必须在 {MIN_RETENTION}-{MAX_RETENTION}（收到 {retention}）"
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="soak_window_gate.py",
        description=("M14-79 长稳窗口锚定/重启门：尾部连续 ok 前置满足才允许锚定"
                     "全新 24h soak 窗口（warn/critical 残留即拒绝并逐条说明；"
                     "零子进程/零网络/零墙钟）"),
    )
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY_PATH,
                        help=(f"history.jsonl 文件或包含它的目录"
                              f"（默认 {DEFAULT_HISTORY_PATH}，gitignored；只读）"))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                        help=(f"输出目录（默认 {DEFAULT_OUTPUT_DIR}，gitignored；"
                              f"门报告与锚定记录同目录）"))
    parser.add_argument("--anchor", action="store_true",
                        help=("门 open 时额外写出锚定记录 soak-window-anchor.json/.md"
                              "（门 closed 时只落门报告；锚定记录已存在则拒绝）"))
    parser.add_argument("--consecutive-ok", type=int, default=DEFAULT_CONSECUTIVE_OK,
                        help=(f"尾部连续 ok 样本前置数 {MIN_CONSECUTIVE_OK}-"
                              f"{MAX_CONSECUTIVE_OK}（默认 {DEFAULT_CONSECUTIVE_OK}，"
                              f"15 分钟节奏即 2 小时干净尾部）"))
    parser.add_argument("--max-gap-minutes", type=int, default=DEFAULT_MAX_GAP_MINUTES,
                        help=(f"尾部相邻样本最大间隔分钟数（默认 "
                              f"{DEFAULT_MAX_GAP_MINUTES}，与 soak 审计 max-gap 同口径）"))
    parser.add_argument("--window-minutes", type=int, default=DEFAULT_WINDOW_MINUTES,
                        help=(f"锚定窗口分钟数，正数（默认 {DEFAULT_WINDOW_MINUTES} = 24h，"
                              f"仅用于推导最早可判定时间与固定审计指引）"))
    parser.add_argument("--retention", "--max-rows", dest="retention", type=int,
                        default=DEFAULT_RETENTION,
                        help=(f"分析留存条数 {MIN_RETENTION}-{MAX_RETENTION}"
                              f"（默认 {DEFAULT_RETENTION}，保留最新 N 行）"))
    return parser


def run_gate(*, store: _history.Store, history: Path, output_dir: Path,
             anchor: bool, consecutive_ok: int, max_gap_minutes: int,
             window_minutes: int, retention: int) -> tuple[dict[str, object], int]:
    """主门：解析路径→读字节→严格校验→留存→门评估→（可选）锚定写出。

    门 closed 是合法数据域结论——照常落盘门报告（含逐条拒绝原因），锚定
    记录零写出；输入拒绝（GateError）在任何输出写出前 raise，零输出。"""
    if anchor and store.exists(output_dir / ANCHOR_JSON_NAME):
        raise GateError("anchor-exists")
    history_path = resolve_history_path(store, history)
    raw = store.read_bytes(history_path)
    input_sha256 = hashlib.sha256(raw).hexdigest()
    rows = parse_history(raw)
    validate_global(rows)
    omitted_older = max(0, len(rows) - retention)
    analyzed = rows[len(rows) - retention:] if omitted_older else rows
    result = evaluate_gate(analyzed, consecutive_ok=consecutive_ok,
                           max_gap_minutes=max_gap_minutes,
                           window_minutes=window_minutes)
    report = build_gate_report(
        input_sha256=input_sha256, input_bytes=len(raw), row_count=len(rows),
        analyzed_count=len(analyzed), omitted_older=omitted_older,
        consecutive_ok=consecutive_ok, max_gap_minutes=max_gap_minutes,
        window_minutes=window_minutes, retention=retention, result=result)
    write_pair(store, output_dir, GATE_JSON_NAME, GATE_MD_NAME,
               json.dumps(report, ensure_ascii=False, indent=2,
                          sort_keys=True) + "\n",
               render_gate_markdown(report))
    if anchor and result.status == GATE_OPEN:
        anchor_record = build_anchor_record(report)
        write_pair(store, output_dir, ANCHOR_JSON_NAME, ANCHOR_MD_NAME,
                   json.dumps(anchor_record, ensure_ascii=False, indent=2,
                              sort_keys=True) + "\n",
                   render_anchor_markdown(anchor_record))
    return report, (EXIT_OPEN if result.status == GATE_OPEN else EXIT_CLOSED)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(f"{TAG} 输入: {args.history}（只读）", flush=True)
    problem = validate_settings(consecutive_ok=args.consecutive_ok,
                                max_gap_minutes=args.max_gap_minutes,
                                window_minutes=args.window_minutes,
                                retention=args.retention)
    if problem is not None:
        print(f"{TAG} 拒绝: {problem}", flush=True)
        return EXIT_REFUSED
    try:
        report, exit_code = run_gate(
            store=_history.RealStore(), history=args.history,
            output_dir=args.output_dir, anchor=args.anchor,
            consecutive_ok=args.consecutive_ok,
            max_gap_minutes=args.max_gap_minutes,
            window_minutes=args.window_minutes, retention=args.retention)
    except GateError as cause:
        print(f"{TAG} 拒绝: {cause}（fail-closed，输出零写入）", flush=True)
        return EXIT_REFUSED
    except _history.HistoryError as cause:
        print(f"{TAG} 拒绝: {cause}（fail-closed，输出零写入）", flush=True)
        return EXIT_REFUSED
    except OSError as cause:
        print(f"{TAG} 写失败: {type(cause).__name__}（输出保持原子，无 tmp 残留）",
              flush=True)
        return EXIT_REFUSED
    print(f"{TAG} 门状态: {report['status']}"
          + (f"（{', '.join(report['reasons'])}）" if report["reasons"] else ""),
          flush=True)
    print(f"{TAG} 锚点: {report['anchor_collected_at']} / "
          f"尾部 {report['tail_sample_count']} 行 / "
          f"残留非干净 {len(report['tail_non_ok_samples'])}", flush=True)
    print(f"{TAG} 输出: {GATE_JSON_NAME} / {GATE_MD_NAME} -> {args.output_dir}"
          + (f"（--anchor 且门 open：加写 {ANCHOR_JSON_NAME} / {ANCHOR_MD_NAME})"
             if args.anchor and report["status"] == GATE_OPEN else ""),
          flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
