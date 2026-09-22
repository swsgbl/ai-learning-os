#!/usr/bin/env python
"""M14-93 长稳到期审计/导出 runner：M14-79 权威锚定窗口到点后，把
「重跑 soak 审计 + 逐字节导出 long-soak 门证据」从人工命令拼装固化为
单一 fail-closed 仓库工具。到点前固定词汇 not-due 拒绝（零审计、零
证据导出）；到点后**原样复用** soak_stability_audit 的解析/校验/分类
语义审计；pending 拒绝导出；只有 pass/blocked 才把
soak-audit-report.json **逐字节复制**为 evidence/long-soak.json 并登记
哈希——绝不改写/绝不重序列化门证据，绝不伪造 pass。

设计（与 tools/ops/soak_window_gate.py / soak_stability_audit.py 同款
纪律：单文件、纯标准库、零第三方依赖；一切 I/O 经 Store 注入；零
子进程/零网络/零计划任务/零 env 读取/零墙钟；只读 anchor 与 history
输入、只写全新输出目录）：

- 输入（固定画像）：``--anchor`` M14-79 锚定记录
  soak-window-anchor.json（默认 canonical gitignored
  ``.verify/artifacts/m14-79-soak-window-anchor/``）；``--history``
  history.jsonl 文件或包含它的目录（默认与 soak 审计一致）；``--output-dir``
  全新输出目录（默认 gitignored
  ``.verify/artifacts/m14-93-long-soak-window-runner/``）。**输出目录必须
  不存在**（fresh——拒绝与旧证据混装；symlink 一律拒绝）。
- 锚定记录严格校验（fail-closed，固定词汇原因）：键集恰为
  soak_window_gate 写出的九键；schema_version/tool/follow_up_audit_tool
  必须逐字匹配产锚工具自声明；anchor_collected_at/earliest_audit_
  collected_at/generated_at 必须是合法时间戳且 earliest == anchor +
  window_minutes、generated_at == anchor（零墙钟产锚不变式——手改锚
  提前到期即拒绝）；window_minutes 必须等于审计策略窗口 1440（策略
  对齐，防「锚 12h / 审计 24h」错位）；precondition 键集/取值域严格
  （tail_non_ok_count 必须为 0——锚只可能产自 open 门；tail_sample_count
  必须等于 consecutive_ok；input_sha256 必须 64 位小写 hex）；boundaries
  必须与产锚工具固定边界逐字一致。
- 历史解析/校验/分类**零重复实现**：路径解析、逐行严格解析、全局
  时序/唯一/项目校验、留存契约、审计分类全部直接调用既有
  ``soak_stability_audit`` 函数（其自身复用 monitoring_history 单一
  schema 事实源）；审计策略固定为 release-readiness 接受口径
  1440/15/20/500（工具默认值），本 runner 不暴露任何策略参数——策略
  漂移即证据不可用。
- 到期判定（零墙钟）：**最新历史样本 collected_at** 对比锚定记录
  earliest_audit_collected_at（绝不读系统钟）。样本早于锚点本身 →
  拒绝（history-before-anchor——该历史不可能覆盖锚定窗口）；锚点 ≤
  最新样本 < 最早可判定时间 → not-due（exit 1，只落 runner 报告，
  零审计零证据）；最新样本 ≥ 最早可判定时间 → 到期，执行审计。
- 到期路径：先复用 ``soak_stability_audit.run_audit`` 把
  soak-audit-report.json/.md 原子写进输出目录（blocked/pending 亦照常
  落盘——数据域可见结论）；随后按分类分流：
  - ``pending`` → 拒绝证据导出（exit 2，固定词汇
    evidence-export-refused-pending + 审计原因）；
  - ``pass`` / ``blocked`` → 把 soak-audit-report.json 逐字节复制为
    ``evidence/long-soak.json``（先验证 utf-8 解码-再编码往返逐字节
    一致，写后重读复核；任何字节差异即拒绝并移除坏副本——绝不
    改写/重序列化门证据），runner 报告登记源/副本双哈希（必须相等）；
    pass → exit 0；blocked → exit 3（诚实 blocked 证据照常导出）。
- 输出仅安全元数据：状态/固定词汇原因/时间戳/计数/sha256/字节数/
  固定边界——**绝无原始日志行/密钥/secret/env 值/URL/token/主机标识/
  绝对路径**。CLI stdout 与 argparse help 同纪律：help 只作通用输入
  描述、绝不插值 DEFAULT_* 绝对路径值；stdout 只打印文件名/状态/
  时间戳/固定词汇原因，绝不回显 args 路径——默认路径行为不变（默认
  值仍注册在 parser 上）。生成时间戳取自最新历史样本——零墙钟，同
  输入两次运行输出逐字节相同。tmp + fsync + os.replace 原子落盘。
- 诚实边界：本 runner **不使 long-soak 通过**——blocked 窗口导出的
  就是 blocked 证据；``release_ready=false`` / ``production_ready=false``
  不变；发布审批仍是 human-only；零生产触碰。

用法（仓库根）：
  python tools/ops/long_soak_release_window.py             # 默认输入/输出
  python tools/ops/long_soak_release_window.py --anchor <soak-window-anchor.json> \
      --history <dir-or-file> --output-dir <fresh-dir>

退出码：0 到期 pass 且证据已导出；1 not-due（窗口未到期，零审计零
证据）；2 到期 pending（审计已跑，证据导出拒绝）；3 到期 blocked（审计
已跑，blocked 证据已逐字节导出）；4 输入拒绝/参数越界/输出目录已存在/
symlink/哈希不符/写失败（零输出或输出保持原子）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

try:  # 直接脚本运行：tools/ops 自身在 sys.path
    import monitoring_history as _history
    import soak_stability_audit as _audit
    import soak_window_gate as _gate
except ImportError:  # 经 importlib 按路径加载（契约测试形态）
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import monitoring_history as _history
    import soak_stability_audit as _audit
    import soak_window_gate as _gate

EXIT_EXPORTED_PASS = 0
#: 1 = 窗口未到期（可见结论：runner 报告落盘、零审计、零证据导出）
EXIT_NOT_DUE = 1
#: 2 = 到期但审计 pending（审计报告已落盘、证据导出拒绝）
EXIT_PENDING = 2
#: 3 = 到期且审计 blocked（blocked 证据已逐字节导出——诚实结论非崩溃）
EXIT_BLOCKED_EXPORTED = 3
#: 4 = 输入拒绝/参数越界/输出目录已存在/symlink/哈希不符/写失败（零输出）
EXIT_REFUSED = 4

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ANCHOR_PATH = (REPO_ROOT / ".verify" / "artifacts"
                       / "m14-79-soak-window-anchor"
                       / _gate.ANCHOR_JSON_NAME)
DEFAULT_HISTORY_PATH = _audit.DEFAULT_HISTORY_PATH
DEFAULT_OUTPUT_DIR = (REPO_ROOT / ".verify" / "artifacts"
                      / "m14-93-long-soak-window-runner")

RUNNER_JSON_NAME = "long-soak-window-runner.json"
RUNNER_MD_NAME = "long-soak-window-runner.md"
EVIDENCE_DIR_NAME = "evidence"
EVIDENCE_FILE_NAME = "long-soak.json"

TAG = "[long-soak-window]"
RUNNER_SCHEMA_VERSION = 1
TOOL_NAME = "tools/ops/long_soak_release_window.py"

#: runner 状态固定词汇（绝不携带文件内容文本）
STATUS_NOT_DUE = "not-due"
STATUS_PASS_EXPORTED = "pass-exported"
STATUS_BLOCKED_EXPORTED = "blocked-exported"
STATUS_PENDING_NO_EXPORT = "pending-no-export"
REASON_NOT_DUE = "window-not-due"
REASON_PENDING = "evidence-export-refused-pending"

#: 锚定记录键全集（与 soak_window_gate.build_anchor_record 逐键一致；
#: 多余/缺失键 = 手工拼装或篡改，fail-closed 拒绝）
ANCHOR_KEYS = frozenset({
    "schema_version", "tool", "anchor_collected_at", "window_minutes",
    "earliest_audit_collected_at", "precondition", "follow_up_audit_tool",
    "generated_at", "boundaries",
})
#: precondition 键全集（与 build_anchor_record 写出面一致）
PRECONDITION_KEYS = frozenset({
    "consecutive_ok", "max_gap_minutes", "tail_sample_count",
    "tail_non_ok_count", "tail_max_gap_minutes", "input_sha256",
})
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


class RunnerError(RuntimeError):
    """fail-closed 拒绝（固定词汇原因；绝不携带文件内容文本）。"""


# ---------------------------------------------------------------- 工具函数


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


# ---------------------------------------------------------------- 锚定校验


@dataclass(frozen=True)
class AnchorRecord:
    """已严格校验的锚定记录（仅到期判定所需字段；多余信息绝不进入输出）。"""

    collected_dt: object  # datetime（复用 monitoring_history.parse_timestamp）
    collected_at: str
    window_minutes: int
    earliest_dt: object  # datetime
    earliest_at: str


def _validate_precondition(pre: dict[str, object]) -> None:
    if set(pre) != set(PRECONDITION_KEYS):
        raise RunnerError("anchor-precondition-keys")
    consecutive = pre["consecutive_ok"]
    if (not _is_int(consecutive)
            or not _gate.MIN_CONSECUTIVE_OK <= consecutive
            <= _gate.MAX_CONSECUTIVE_OK):
        raise RunnerError("anchor-precondition-consecutive-ok")
    max_gap = pre["max_gap_minutes"]
    if not _is_int(max_gap) or max_gap < 1:
        raise RunnerError("anchor-precondition-max-gap")
    if not _is_int(pre["tail_sample_count"]) or pre["tail_sample_count"] != consecutive:
        raise RunnerError("anchor-precondition-tail-count")
    if not _is_int(pre["tail_non_ok_count"]) or pre["tail_non_ok_count"] != 0:
        #: 锚只可能产自 open 门——非干净残留计数 > 0 即手拼/篡改
        raise RunnerError("anchor-precondition-non-ok")
    tail_gap = pre["tail_max_gap_minutes"]
    if not _is_number(tail_gap) or not 0 <= tail_gap <= max_gap:
        raise RunnerError("anchor-precondition-tail-gap")
    if (not isinstance(pre["input_sha256"], str)
            or _HEX64_RE.match(pre["input_sha256"]) is None):
        raise RunnerError("anchor-precondition-sha256")


def load_anchor(store: _history.Store, path: Path) -> tuple[AnchorRecord, str, int]:
    """读取并严格校验锚定记录；返回（记录, sha256, 字节数）。

    一切拒绝在任何输出写出前 raise（固定词汇；绝不回显文件内容）。"""
    if not store.exists(path):
        raise RunnerError("anchor-path-missing")
    _history.reject_symlinked_path(store, path)
    raw = store.read_bytes(path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise RunnerError("anchor-not-utf8") from None
    try:
        data = json.loads(text)
    except ValueError:
        raise RunnerError("anchor-not-json") from None
    if not isinstance(data, dict):
        raise RunnerError("anchor-not-an-object")
    if set(data) != set(ANCHOR_KEYS):
        raise RunnerError("anchor-keys")
    if data["schema_version"] != _gate.ANCHOR_SCHEMA_VERSION:
        raise RunnerError("anchor-schema-version")
    if data["tool"] != _gate.TOOL_NAME:
        raise RunnerError("anchor-tool")
    if data["follow_up_audit_tool"] != _gate.AUDIT_TOOL_NAME:
        raise RunnerError("anchor-follow-up-tool")
    try:
        collected_dt = _history.parse_timestamp(data["anchor_collected_at"])
        earliest_dt = _history.parse_timestamp(data["earliest_audit_collected_at"])
        generated_dt = _history.parse_timestamp(data["generated_at"])
    except _history.HistoryError:
        raise RunnerError("anchor-timestamps") from None
    if not _is_int(data["window_minutes"]):
        raise RunnerError("anchor-window-minutes")
    window = data["window_minutes"]
    #: 窗口必须与审计策略窗口一致（release-readiness 接受口径）——
    #: 锚 12h / 审计 24h 的错位锚不构成到期事实
    if window != _audit.DEFAULT_WINDOW_MINUTES:
        raise RunnerError("anchor-window-minutes")
    derived = collected_dt + timedelta(minutes=window)  # type: ignore[arg-type]
    if earliest_dt != derived:  # type: ignore[operator]
        #: 手改锚把到期时间提前即在此拒绝（零墙钟产锚不变式）
        raise RunnerError("anchor-earliest-mismatch")
    if generated_dt != collected_dt:  # type: ignore[operator]
        raise RunnerError("anchor-generated-at-mismatch")
    if not isinstance(data["precondition"], dict):
        raise RunnerError("anchor-precondition-keys")
    _validate_precondition(data["precondition"])
    boundaries = data["boundaries"]
    if (not isinstance(boundaries, list)
            or list(boundaries) != list(_gate.GATE_BOUNDARIES)):
        raise RunnerError("anchor-boundaries")
    record = AnchorRecord(
        collected_dt=collected_dt,
        collected_at=str(data["anchor_collected_at"]),
        window_minutes=window,
        earliest_dt=earliest_dt,
        earliest_at=str(data["earliest_audit_collected_at"]),
    )
    return record, hashlib.sha256(raw).hexdigest(), len(raw)


# ---------------------------------------------------------------- 报告（纯）

RUNNER_BOUNDARIES: tuple[str, ...] = (
    (
        "due time is derived from the latest history sample versus the anchor's "
        "earliest_audit_collected_at — never the system clock: zero wall clock, "
        "byte-identical outputs across reruns"
    ),
    (
        "audit semantics are reused verbatim from tools/ops/soak_stability_audit.py "
        "at the release-readiness accepted policy (1440/15/20/500); this runner "
        "implements no classification logic of its own"
    ),
    (
        "pending classification refuses evidence export; only pass or blocked "
        "export, and evidence/long-soak.json is a byte-for-byte copy of "
        "soak-audit-report.json (never rewritten, never reserialized), verified "
        "by read-back hashing"
    ),
    (
        "this runner does not make long-soak pass: a blocked window exports honest "
        "blocked evidence; release_ready stays false, production_ready stays "
        "false, and release approval remains human-only"
    ),
    (
        "read-only over anchor and history inputs; a fresh output directory is "
        "required; zero child-process spawns, zero network, zero scheduler "
        "mutation, zero env reads, zero production touch"
    ),
)


def build_runner_report(*, status: str, reasons: tuple[str, ...],
                        anchor_sha256: str, anchor_bytes: int,
                        history_sha256: str, history_bytes: int,
                        row_count: int, anchor_at: str, earliest_at: str,
                        latest_at: str, audit_block: dict[str, object] | None
                        ) -> dict[str, object]:
    return {
        "schema_version": RUNNER_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "status": status,
        "reasons": list(reasons),
        "anchor": {"sha256": anchor_sha256, "byte_size": anchor_bytes},
        "history": {"sha256": history_sha256, "byte_size": history_bytes,
                    "row_count": row_count},
        "anchor_collected_at": anchor_at,
        "earliest_audit_collected_at": earliest_at,
        "latest_sample_collected_at": latest_at,
        "audit": audit_block,
        "settings": {
            "window_minutes": _audit.DEFAULT_WINDOW_MINUTES,
            "expected_interval_minutes": _audit.DEFAULT_EXPECTED_INTERVAL_MINUTES,
            "max_gap_minutes": _audit.DEFAULT_MAX_GAP_MINUTES,
            "retention": _audit.DEFAULT_RETENTION,
        },
        "generated_at": latest_at,  # 零墙钟：最新样本即生成锚
        "boundaries": list(RUNNER_BOUNDARIES),
    }


def render_runner_markdown(report: dict[str, object]) -> str:
    anchor_info = report["anchor"]
    history_info = report["history"]
    assert isinstance(anchor_info, dict) and isinstance(history_info, dict)
    lines: list[str] = [
        (f"# M14-93 长稳到期审计/导出 runner（schema_version="
         f"{report['schema_version']}）"),
        "",
        f"- 状态：**{report['status']}**"
        + (f"（原因：{', '.join(report['reasons'])}）" if report["reasons"] else ""),
        (f"- 锚定记录：SHA-256 `{anchor_info['sha256']}`，"
         f"{anchor_info['byte_size']} bytes；"
         f"锚点 {report['anchor_collected_at']}；"
         f"最早可判定时间 {report['earliest_audit_collected_at']}"),
        (f"- 历史：SHA-256 `{history_info['sha256']}`，"
         f"{history_info['byte_size']} bytes，{history_info['row_count']} 行；"
         f"最新样本 {report['latest_sample_collected_at']}（零墙钟到期判定"
         f"基准）"),
    ]
    audit_block = report["audit"]
    if isinstance(audit_block, dict):
        lines.append(f"- 审计分类：**{audit_block['classification']}**"
                     + (f"（{', '.join(audit_block['reasons'])}）"
                        if audit_block["reasons"] else ""))
        source = audit_block["soak_audit_report"]
        if isinstance(source, dict):
            lines.append(f"- 审计报告：SHA-256 `{source['sha256']}`，"
                         f"{source['byte_size']} bytes（soak-audit-report.json）")
        evidence = audit_block["long_soak_evidence"]
        if isinstance(evidence, dict):
            lines.append(f"- 门证据：evidence/long-soak.json = SHA-256 "
                         f"`{evidence['sha256']}`，{evidence['byte_size']} bytes"
                         "（逐字节复制，写后重读复核一致）")
        else:
            lines.append("- 门证据：**未导出**（pending 拒绝导出）")
    else:
        lines.append("- 审计：**未执行**（窗口未到期）")
    lines += ["", "边界："] + [f"- {item}" for item in report["boundaries"]]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 写出（原子）


def write_runner_report(store: _history.Store, output_dir: Path,
                        report: dict[str, object]) -> None:
    """runner 报告双文件原子落盘（先于 mkdir 拒绝目录/祖先 symlink，
    mkdir 后复查目标——TOCTOU 防御）。"""
    _history.reject_symlinked_path(store, output_dir)
    store.mkdirs(output_dir)
    json_path = output_dir / RUNNER_JSON_NAME
    md_path = output_dir / RUNNER_MD_NAME
    _history.reject_symlinked_path(store, json_path, md_path, output_dir)
    store.write_atomic(json_path, json.dumps(report, ensure_ascii=False,
                                             indent=2, sort_keys=True) + "\n")
    store.write_atomic(md_path, render_runner_markdown(report))


def export_evidence_copy(store: _history.Store,
                         output_dir: Path) -> tuple[str, int, str, int]:
    """soak-audit-report.json → evidence/long-soak.json 逐字节复制。

    先验证 utf-8 解码-再编码往返逐字节一致（证 write_atomic 对该内容
    字节保真），写后重读复核；任何差异拒绝并移除坏副本——绝不改写/
    重序列化门证据。返回（源 sha256, 源字节数, 副本 sha256, 副本字节数）。"""
    source_path = output_dir / _audit.REPORT_JSON_NAME
    source_bytes = store.read_bytes(source_path)
    evidence_dir = output_dir / EVIDENCE_DIR_NAME
    _history.reject_symlinked_path(store, evidence_dir)
    store.mkdirs(evidence_dir)
    evidence_path = evidence_dir / EVIDENCE_FILE_NAME
    _history.reject_symlinked_path(store, evidence_path, evidence_dir)
    try:
        text = source_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise RunnerError("audit-report-not-utf8") from None
    if text.encode("utf-8") != source_bytes:
        raise RunnerError("evidence-roundtrip-mismatch")
    store.write_atomic(evidence_path, text)
    copied = store.read_bytes(evidence_path)
    if copied != source_bytes:
        try:
            evidence_path.unlink()
        except OSError:
            pass
        raise RunnerError("evidence-byte-mismatch")
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    copied_sha = hashlib.sha256(copied).hexdigest()
    return source_sha, len(source_bytes), copied_sha, len(copied)


# ---------------------------------------------------------------- 主管道


def _audit_block(report: dict[str, object], *, source: dict[str, object] | None,
                 evidence: dict[str, object] | None) -> dict[str, object]:
    """runner 报告的 audit 摘要块（分类/原因/哈希——绝不改写审计报告本体）。"""
    return {
        "classification": report["classification"],
        "reasons": list(report["reasons"]),
        "soak_audit_report": source,
        "long_soak_evidence": evidence,
    }


def run_runner(*, store: _history.Store, anchor: Path, history: Path,
               output_dir: Path) -> tuple[dict[str, object], int]:
    """主管道：输出目录新鲜度→锚定校验→历史严格校验→到期判定→分流。

    not-due 是合法时序结论——只落 runner 报告（零审计零证据）；输入拒绝
    （RunnerError/审计拒绝）在任何输出写出前 raise，零输出。"""
    if store.exists(output_dir):
        raise RunnerError("output-dir-exists")
    _history.reject_symlinked_path(store, output_dir)
    anchor_record, anchor_sha, anchor_size = load_anchor(store, anchor)
    history_path = _audit.resolve_history_path(store, history)
    raw = store.read_bytes(history_path)
    history_sha = hashlib.sha256(raw).hexdigest()
    rows = _audit.parse_history(raw)
    _audit.validate_global(rows)
    latest = rows[-1]
    if latest.collected_dt < anchor_record.collected_dt:  # type: ignore[operator]
        raise RunnerError("history-before-anchor")
    if latest.collected_dt < anchor_record.earliest_dt:  # type: ignore[operator]
        report = build_runner_report(
            status=STATUS_NOT_DUE, reasons=(REASON_NOT_DUE,),
            anchor_sha256=anchor_sha, anchor_bytes=anchor_size,
            history_sha256=history_sha, history_bytes=len(raw),
            row_count=len(rows), anchor_at=anchor_record.collected_at,
            earliest_at=anchor_record.earliest_at,
            latest_at=latest.collected_at, audit_block=None)
        write_runner_report(store, output_dir, report)
        return report, EXIT_NOT_DUE
    audit_report = _audit.run_audit(
        store=store, history=history_path, output_dir=output_dir,
        window_minutes=_audit.DEFAULT_WINDOW_MINUTES,
        expected_interval_minutes=_audit.DEFAULT_EXPECTED_INTERVAL_MINUTES,
        max_gap_minutes=_audit.DEFAULT_MAX_GAP_MINUTES,
        retention=_audit.DEFAULT_RETENTION)
    input_meta = audit_report["input"]
    assert isinstance(input_meta, dict)
    if input_meta.get("sha256") != history_sha:
        #: 防御性后置断言：审计报告自声明输入哈希必须与本轮读取一致
        raise RunnerError("audit-input-hash-mismatch")
    classification = audit_report["classification"]
    if classification == _audit.CLASS_PENDING:
        report = build_runner_report(
            status=STATUS_PENDING_NO_EXPORT,
            reasons=(REASON_PENDING,) + tuple(audit_report["reasons"]),
            anchor_sha256=anchor_sha, anchor_bytes=anchor_size,
            history_sha256=history_sha, history_bytes=len(raw),
            row_count=len(rows), anchor_at=anchor_record.collected_at,
            earliest_at=anchor_record.earliest_at,
            latest_at=latest.collected_at,
            audit_block=_audit_block(audit_report, source=None, evidence=None))
        write_runner_report(store, output_dir, report)
        return report, EXIT_PENDING
    source_sha, source_size, copy_sha, copy_size = export_evidence_copy(
        store, output_dir)
    if source_sha != copy_sha or source_size != copy_size:
        raise RunnerError("evidence-hash-mismatch")
    exported_pass = classification == _audit.CLASS_PASS
    report = build_runner_report(
        status=(STATUS_PASS_EXPORTED if exported_pass else STATUS_BLOCKED_EXPORTED),
        reasons=(), anchor_sha256=anchor_sha, anchor_bytes=anchor_size,
        history_sha256=history_sha, history_bytes=len(raw),
        row_count=len(rows), anchor_at=anchor_record.collected_at,
        earliest_at=anchor_record.earliest_at,
        latest_at=latest.collected_at,
        audit_block=_audit_block(
            audit_report,
            source={"sha256": source_sha, "byte_size": source_size},
            evidence={"sha256": copy_sha, "byte_size": copy_size}))
    write_runner_report(store, output_dir, report)
    return report, (EXIT_EXPORTED_PASS if exported_pass else EXIT_BLOCKED_EXPORTED)


# ---------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    """argparse 构造：help 只作通用描述，绝不插值 DEFAULT_* 绝对路径值
    （默认路径行为不变——默认值本身注册在 parser 上，仅不进入任何输出）。"""
    parser = argparse.ArgumentParser(
        prog="long_soak_release_window.py",
        description=("M14-93 长稳到期审计/导出 runner：M14-79 锚定窗口到点后"
                     "复用 soak_stability_audit 语义审计并逐字节导出 long-soak "
                     "门证据（到点前 not-due 拒绝；pending 拒绝导出；零子进程/"
                     "零网络/零墙钟）"),
    )
    parser.add_argument("--anchor", type=Path, default=DEFAULT_ANCHOR_PATH,
                        help=("M14-79 锚定记录 soak-window-anchor.json"
                              "（默认为 canonical gitignored 锚定目录内同名"
                              "文件；只读；可用任意路径覆盖）"))
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY_PATH,
                        help=("history.jsonl 文件本身或包含它的目录"
                              "（默认为 canonical gitignored 监控历史；只读）"))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                        help=("全新输出目录，必须不存在"
                              "（默认为 canonical gitignored runner 输出目录）"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(f"{TAG} 输入: 锚定记录 soak-window-anchor.json + 历史 history.jsonl"
          "（只读）", flush=True)
    try:
        report, exit_code = run_runner(
            store=_history.RealStore(), anchor=args.anchor,
            history=args.history, output_dir=args.output_dir)
    except RunnerError as cause:
        print(f"{TAG} 拒绝: {cause}（fail-closed，输出零写入）", flush=True)
        return EXIT_REFUSED
    except (_audit.AuditError, _history.HistoryError) as cause:
        print(f"{TAG} 拒绝: {cause}（fail-closed，输出零写入）", flush=True)
        return EXIT_REFUSED
    except OSError as cause:
        print(f"{TAG} 写失败: {type(cause).__name__}（输出保持原子，无 tmp 残留）",
              flush=True)
        return EXIT_REFUSED
    print(f"{TAG} 状态: {report['status']}"
          + (f"（{', '.join(report['reasons'])}）" if report["reasons"] else ""),
          flush=True)
    print(f"{TAG} 最新样本: {report['latest_sample_collected_at']} / "
          f"最早可判定: {report['earliest_audit_collected_at']}", flush=True)
    print(f"{TAG} 输出: {RUNNER_JSON_NAME} / {RUNNER_MD_NAME}", flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
