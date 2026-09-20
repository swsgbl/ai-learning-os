#!/usr/bin/env python
"""M14-72 长稳审计：M14-13 history.jsonl → 真实连续 24 小时稳定窗口判定。

设计（与 tools/ops/monitoring_history.py 同款纪律：单文件、纯标准库、
零第三方依赖；一切 I/O 经 Store 注入；零子进程/零网络/零计划任务/
零 env 读取/零墙钟；本工具只读 history 输入、只写输出目录）：

- 输入（固定画像）：默认 canonical gitignored
  ``.verify/artifacts/m14-13-monitoring-history/history.jsonl``（可用
  ``--history`` 覆盖为该文件本身或包含它的目录）。逐行**严格解析**：
  每行必须为合法 JSON object 且至少携带本审计所需五字段
  schema_version/collected_at/project/overall_status/partial（与
  monitoring_history 单一 schema 事实源——常量/时间戳解析/project 白名单
  复用其已测函数）。malformed / 非 JSON / 时间戳非法 / project 不合规 /
  overall_status 超词汇 / partial 非 bool → 一律 fail-closed（输出零写入）。
  未知多余字段不进入任何输出（仅计数/状态/时间戳面）。
- 留存契约：``--retention``（同义词 ``--max-rows``）默认 500、下限 1、
  硬顶 5000（与 monitoring_history 当前有界历史契约一致）；文件总行数
  超硬顶 → fail-closed（row-limit-exceeded）。分析面取最新 N 行，更早
  行仅计 ``omitted_older_count``；**全局时序/唯一性/项目一致性校验覆盖
  全部行**（含被省略行）。
- 锚点：最新有效样本的 collected_at 为审计锚。pass 必须覆盖「锚 −
  所需窗口」或更早，直至锚。
- 分类（fail-closed，固定词汇原因）：
  - ``pass``（exit 0）：所需窗口内全部样本 overall_status=ok 且
    partial=false、全史全局时序、时间戳唯一、窗口内相邻间隔 ≤
    max-gap、样本数 ≥ 闭区间最小样本数（window // interval + 1——
    24h/15m 即 97，含窗口两端）、且存在不晚于窗口起点的样本。**绝不
    从总历史跨度/insights 聚合/合成 soak 时长/墙钟推导 pass**。
  - ``pending``（exit 1）：数据本身合法但干净覆盖不足——窗口内干净
    覆盖不足（insufficient-clean-coverage）或样本数低于闭区间最小
    样本数（insufficient-sample-count；96/97 边界即此处）。
  - ``blocked``（exit 2，**数据域审计结论**）：窗口内出现 warn/
    critical/partial（non-ok-status-in-window）或窗口内间隔超限
    （excessive-gap-in-window）——输入本身合法，照常写出确定性
    JSON+Markdown blocked 报告后退出 2。
  - 输入拒绝（exit 2，**零输出**）：时间戳重复（duplicate-timestamp）、
    全局非时序（non-chronological-history）、项目冲突
    （conflicting-project）、malformed 行、行数超硬顶、输入路径缺失/
    symlink、空历史、参数超界、输出写失败——任何输出写出前即拒绝。
- 退出码：0 pass；1 pending；2 blocked 与输入拒绝（参数超界亦 2）。
- 输出（默认 gitignored ``.verify/m14-72-long-soak-audit/``，
  ``--output-dir`` 为操作者显式自选）：确定性 JSON 与 Markdown 报告
  （schema/gate/tool/version、输入 SHA-256 与字节大小、行数、窗口/间隔/gap
  设置、锚点、窗口起点、入选行数、状态计数、最大观测间隔、分类与
  固定词汇原因；**绝无原始日志行/密钥/secret/env 值/URL/token/主机
  标识**——仅已脱敏 project 字段与计数/时间戳面）。生成时间戳取自锚
  样本——零墙钟，输出逐字节可复现。两文件同目录 tmp + fsync +
  os.replace 原子落盘；symlink 组件/越界路径一律拒绝；仅在**全部输入
  校验与分类完成之后**才写任何输出（数据域 blocked 亦落盘报告；输入
  拒绝零写出）。

用法（仓库根）：
  python tools/ops/soak_stability_audit.py             # 默认输入/输出
  python tools/ops/soak_stability_audit.py --history <dir-or-file>
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

EXIT_PASS = 0
EXIT_PENDING = 1
EXIT_REFUSED = 2

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HISTORY_PATH = (REPO_ROOT / ".verify" / "artifacts"
                        / "m14-13-monitoring-history" / "history.jsonl")
DEFAULT_OUTPUT_DIR = REPO_ROOT / ".verify" / "m14-72-long-soak-audit"
HISTORY_FILE_NAME = "history.jsonl"
REPORT_JSON_NAME = "soak-audit-report.json"
REPORT_MD_NAME = "soak-audit-report.md"

TAG = "[soak-audit]"
#: M14-73 起 v2：报告新增顶层 gate 字段供 release-readiness long-soak 门
#: 自声明绑定（v1 报告不再被该门接受）
AUDIT_SCHEMA_VERSION = 2
TOOL_NAME = "tools/ops/soak_stability_audit.py"
#: 报告自声明的发布门 id（M14-73：release-readiness 必需门 long-soak 的
#: 证据文件 long-soak.json 由本报告逐字节复制而来）
GATE_NAME = "long-soak"

#: 分类与固定词汇原因（绝不携带文件内容文本）
CLASS_PASS = "pass"
CLASS_PENDING = "pending"
CLASS_BLOCKED = "blocked"

#: 参数：默认 24h 窗口 / 15 分钟期望间隔 / 20 分钟最大间隔
DEFAULT_WINDOW_MINUTES = 1440
DEFAULT_EXPECTED_INTERVAL_MINUTES = 15
DEFAULT_MAX_GAP_MINUTES = 20

#: 留存契约复用 monitoring_history（默认 500 / 1-5000）
DEFAULT_RETENTION = _history.DEFAULT_RETENTION
MIN_RETENTION = _history.MIN_RETENTION
MAX_RETENTION = _history.MAX_RETENTION

#: 行内 overall_status 词汇复用 monitoring_history
ACCEPTED_OVERALL_STATUSES = _history.ACCEPTED_OVERALL_STATUSES


class AuditError(RuntimeError):
    """fail-closed 拒绝（固定词汇原因；绝不携带文件内容文本）。"""


# ---------------------------------------------------------------- 输入解析


@dataclass(frozen=True)
class HistoryRow:
    """已验证的历史行（仅审计所需字段；多余字段绝不进入任何输出）。"""

    collected_dt: object  # datetime（复用 monitoring_history.parse_timestamp）
    collected_at: str
    project: str
    overall_status: str
    partial: bool


def _require_mapping(value: object, reason: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise AuditError(reason)
    return value


def validate_row(data: object, line_number: int) -> HistoryRow:
    """严格校验单行（审计所需五字段；违规 fail-closed）。

    原因固定词汇；行号仅用于拒绝原因中的整数定位，绝不回显行内容。"""
    row = _require_mapping(data, f"malformed-row:{line_number}:not-an-object")
    if row.get("schema_version") != _history.HISTORY_SCHEMA_VERSION:
        raise AuditError(f"malformed-row:{line_number}:schema-version")
    collected_at = row.get("collected_at")
    try:
        collected_dt = _history.parse_timestamp(collected_at)
    except _history.HistoryError:
        raise AuditError(f"malformed-row:{line_number}:collected-at") from None
    project = row.get("project")
    if not isinstance(project, str) or _history.PROJECT_NAME_RE.match(project) is None:
        raise AuditError(f"malformed-row:{line_number}:project")
    overall = row.get("overall_status")
    #: 先 isinstance 再成员测试——list/dict 等非 str 值对 frozenset 的
    #: `in` 会抛 TypeError；必须落入受控拒绝而非崩溃。
    if not isinstance(overall, str) or overall not in ACCEPTED_OVERALL_STATUSES:
        raise AuditError(f"malformed-row:{line_number}:overall-status")
    partial = row.get("partial")
    if not isinstance(partial, bool):
        raise AuditError(f"malformed-row:{line_number}:partial")
    return HistoryRow(collected_dt=collected_dt, collected_at=str(collected_at),
                      project=project, overall_status=overall, partial=partial)


def resolve_history_path(store: _history.Store, history: Path) -> Path:
    """--history 接受文件本身或包含 history.jsonl 的目录；symlink 一律拒绝。"""
    if not store.exists(history):
        raise AuditError("history-path-missing")
    _history.reject_symlinked_path(store, history)
    if store.is_symlink(history):
        raise AuditError("symlink-target")
    if history.name != HISTORY_FILE_NAME:
        candidate = history / HISTORY_FILE_NAME
        if not store.exists(candidate):
            raise AuditError("history-path-missing")
        _history.reject_symlinked_path(store, candidate)
        return candidate
    return history


def parse_history(raw: bytes) -> list[HistoryRow]:
    """逐行严格解析（UTF-8 严格解码；空文件 → empty-history 拒绝）。"""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise AuditError("history-not-utf8") from None
    lines = text.splitlines()
    if not lines:
        raise AuditError("empty-history")
    rows: list[HistoryRow] = []
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            raise AuditError(f"malformed-row:{index}:empty-line")
        try:
            data = json.loads(line)
        except ValueError:
            raise AuditError(f"malformed-row:{index}:not-json") from None
        rows.append(validate_row(data, index))
    if len(rows) > MAX_RETENTION:
        raise AuditError("row-limit-exceeded")
    return rows


# ---------------------------------------------------------------- 全局校验


def validate_global(rows: list[HistoryRow]) -> None:
    """全局时序（升序）、时间戳唯一、项目单一；违规 fail-closed。"""
    previous_dt = None
    seen_at: set[str] = set()
    for row in rows:
        if row.collected_at in seen_at:
            raise AuditError("duplicate-timestamp")
        seen_at.add(row.collected_at)
        if previous_dt is not None and row.collected_dt < previous_dt:  # type: ignore[operator]
            raise AuditError("non-chronological-history")
        previous_dt = row.collected_dt
    projects = {row.project for row in rows}
    if len(projects) > 1:
        raise AuditError("conflicting-project")


# ---------------------------------------------------------------- 分类（纯）


@dataclass(frozen=True)
class AuditResult:
    classification: str
    reasons: tuple[str, ...]
    anchor_at: str
    window_start_at: str
    selected_count: int
    window_status_counts: dict[str, int]
    window_non_ok_count: int
    max_observed_gap_minutes: float
    selected_span_minutes: float


def classify(rows: list[HistoryRow], *, window_minutes: int,
             expected_interval_minutes: int, max_gap_minutes: int) -> AuditResult:
    """纯分类：pass 仅源于窗口内逐样本干净 + 覆盖 + 间隔 + 最小样本数。

    绝不从总历史跨度、insights 聚合、合成 soak 时长或墙钟推导。"""
    anchor = rows[-1]
    window_start_dt = anchor.collected_dt - timedelta(minutes=window_minutes)
    selected = [row for row in rows if row.collected_dt >= window_start_dt]  # type: ignore[operator]
    reasons: list[str] = []
    counts = {"ok": 0, "warn": 0, "critical": 0}
    non_ok = 0
    for row in selected:
        if row.overall_status in counts:
            counts[row.overall_status] += 1
        if row.overall_status != "ok" or row.partial:
            non_ok += 1
    max_gap = 0.0
    for first, second in itertools.pairwise(selected):
        gap = ((second.collected_dt - first.collected_dt)  # type: ignore[operator]
               .total_seconds() / 60.0)
        max_gap = max(max_gap, gap)
    span = (((selected[-1].collected_dt - selected[0].collected_dt)  # type: ignore[operator]
             .total_seconds() / 60.0) if len(selected) >= 2 else 0.0)
    #: 窗口起点之前的最早分析行存在 → 覆盖自「锚 − 窗口」或更早开始
    covered_from_window_start = rows[0].collected_dt <= window_start_dt  # type: ignore[operator]
    #: 闭区间最小样本数：完整 24h/15m 闭区间序列 = 97（含窗口两端）
    min_required_count = window_minutes // expected_interval_minutes + 1

    if non_ok:
        reasons.append("non-ok-status-in-window")
        classification = CLASS_BLOCKED
    elif max_gap > max_gap_minutes:
        reasons.append("excessive-gap-in-window")
        classification = CLASS_BLOCKED
    elif not covered_from_window_start:
        reasons.append("insufficient-clean-coverage")
        classification = CLASS_PENDING
    elif len(selected) < min_required_count:
        reasons.append("insufficient-sample-count")
        classification = CLASS_PENDING
    else:
        classification = CLASS_PASS
    return AuditResult(
        classification=classification,
        reasons=tuple(reasons),
        anchor_at=anchor.collected_at,
        window_start_at=_history.datetime.strftime(
            window_start_dt, _history.TIMESTAMP_FORMAT),  # type: ignore[arg-type]
        selected_count=len(selected),
        window_status_counts=counts,
        window_non_ok_count=non_ok,
        max_observed_gap_minutes=max_gap,
        selected_span_minutes=span,
    )


# ---------------------------------------------------------------- 报告


def build_report(*, input_sha256: str, input_bytes: int, row_count: int,
                 analyzed_count: int, omitted_older: int, window_minutes: int,
                 expected_interval_minutes: int, max_gap_minutes: int,
                 retention: int, result: AuditResult) -> dict[str, object]:
    return {
        "schema_version": _history.HISTORY_SCHEMA_VERSION,
        "audit_schema_version": AUDIT_SCHEMA_VERSION,
        "gate": GATE_NAME,
        "tool": TOOL_NAME,
        "input": {
            "sha256": input_sha256,
            "byte_size": input_bytes,
        },
        "row_count": row_count,
        "analyzed_row_count": analyzed_count,
        "omitted_older_count": omitted_older,
        "settings": {
            "window_minutes": window_minutes,
            "expected_interval_minutes": expected_interval_minutes,
            "max_gap_minutes": max_gap_minutes,
            "retention": retention,
        },
        "anchor_collected_at": result.anchor_at,
        "window_start_collected_at": result.window_start_at,
        "selected_row_count": result.selected_count,
        "window_status_counts": result.window_status_counts,
        "window_non_ok_count": result.window_non_ok_count,
        "max_observed_gap_minutes": round(result.max_observed_gap_minutes, 3),
        "selected_span_minutes": round(result.selected_span_minutes, 3),
        "classification": result.classification,
        "reasons": list(result.reasons),
    }


def render_report_markdown(report: dict[str, object]) -> str:
    settings = report["settings"]
    assert isinstance(settings, dict)
    counts = report["window_status_counts"]
    assert isinstance(counts, dict)
    input_info = report["input"]
    assert isinstance(input_info, dict)
    lines: list[str] = [
        (f"# M14-72 长稳稳定性审计（audit_schema_version="
         f"{report['audit_schema_version']}）"),
        "",
        f"- 分类：**{report['classification']}**"
        + (f"（原因：{', '.join(report['reasons'])}）" if report["reasons"] else ""),
        f"- 输入：SHA-256 `{input_info['sha256']}`，{input_info['byte_size']} bytes",
        (f"- 发布门绑定：gate={report['gate']}"
         f"（audit_schema_version={report['audit_schema_version']}；"
         "release-readiness 证据 long-soak.json 由本报告逐字节复制而来）"),
        (f"- 行数：总 {report['row_count']} / 分析 {report['analyzed_row_count']}"
         f"（省略更早 {report['omitted_older_count']} 条）"),
        (f"- 设置：窗口 {settings['window_minutes']} 分钟 / 期望间隔 "
         f"{settings['expected_interval_minutes']} 分钟 / 最大间隔 "
         f"{settings['max_gap_minutes']} 分钟 / 留存 {settings['retention']}"),
        (f"- 锚点（最新有效样本）：{report['anchor_collected_at']}；"
         f"窗口起点：{report['window_start_collected_at']}"),
        (f"- 窗口内：入选 {report['selected_row_count']} 行，"
         f"ok={counts['ok']} warn={counts['warn']} critical={counts['critical']}"
         f"（非干净 {report['window_non_ok_count']}），"
         f"最大观测间隔 {report['max_observed_gap_minutes']} 分钟，"
         f"入选跨度 {report['selected_span_minutes']} 分钟"),
        "",
        "边界：",
        "- pass 仅源于窗口内逐样本 ok/partial=false + 覆盖 + 间隔 + 闭区间",
        "  最小样本数（window/interval + 1；24h/15m = 97，含窗口两端）",
        "- 绝不从总历史跨度、insights 聚合、合成 soak 时长或墙钟推导 pass",
        "- 覆盖不足或样本数不足 = pending（非 pass）",
        "- 数据域 blocked（warn/critical/partial/间隔超限）= 写出本报告后 exit 2",
        "- 输入拒绝（缺失/symlink/解析/重复/非时序/项目冲突/行数/参数/写失败）",
        "  = 零输出 exit 2",
        "- 报告绝无原始日志行/密钥/secret/env 值/URL/token/主机标识",
        "- 零墙钟——生成时间戳取自锚样本，输出逐字节可复现",
        "- 本工具零子进程/零网络/零容器面/零计划任务/零 env 读取",
        "- 审计门禁不构成 production readiness 宣称（production_ready=false）",
        "- 退出码：0 pass / 1 pending / 2 blocked",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 写出（原子）


def write_outputs(store: _history.Store, output_dir: Path,
                  report: dict[str, object]) -> tuple[Path, Path]:
    """全部输入校验与分类完成后才可到达此处；两输出同目录原子落盘。

    先于 mkdir 拒绝 output_dir 自身/现存祖先 symlink；mkdir 后复查两输出
    目标（TOCTOU 窗口防御）。"""
    _history.reject_symlinked_path(store, output_dir)
    store.mkdirs(output_dir)
    json_path = output_dir / REPORT_JSON_NAME
    md_path = output_dir / REPORT_MD_NAME
    _history.reject_symlinked_path(store, json_path, md_path, output_dir)
    store.write_atomic(json_path, json.dumps(report, ensure_ascii=False,
                                             indent=2, sort_keys=True) + "\n")
    store.write_atomic(md_path, render_report_markdown(report))
    return json_path, md_path


# ---------------------------------------------------------------- CLI


def validate_settings(*, window_minutes: int, expected_interval_minutes: int,
                      max_gap_minutes: int, retention: int) -> str | None:
    if window_minutes <= 0:
        return f"window-minutes 必须为正（收到 {window_minutes}）"
    if expected_interval_minutes <= 0:
        return (f"expected-interval-minutes 必须为正"
                f"（收到 {expected_interval_minutes}）")
    if max_gap_minutes < expected_interval_minutes:
        return (f"max-gap-minutes（{max_gap_minutes}）必须 ≥ "
                f"expected-interval-minutes（{expected_interval_minutes}）")
    if not MIN_RETENTION <= retention <= MAX_RETENTION:
        return f"retention 必须在 {MIN_RETENTION}-{MAX_RETENTION}（收到 {retention}）"
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="soak_stability_audit.py",
        description=("M14-72 长稳审计：M14-13 history.jsonl → 真实连续 24h "
                     "稳定窗口判定（pass/pending/blocked；零子进程/零网络/零墙钟）"),
    )
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY_PATH,
                        help=(f"history.jsonl 文件或包含它的目录"
                              f"（默认 {DEFAULT_HISTORY_PATH}，gitignored；只读）"))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                        help=(f"输出目录（默认 {DEFAULT_OUTPUT_DIR}，gitignored；"
                              f"自定义路径为操作者显式自选）"))
    parser.add_argument("--window-minutes", type=int, default=DEFAULT_WINDOW_MINUTES,
                        help=f"所需连续稳定窗口分钟数，正数（默认 {DEFAULT_WINDOW_MINUTES} = 24h）")
    parser.add_argument("--expected-interval-minutes", type=int,
                        default=DEFAULT_EXPECTED_INTERVAL_MINUTES,
                        help=(f"期望采样间隔分钟数，正数"
                              f"（默认 {DEFAULT_EXPECTED_INTERVAL_MINUTES}）"))
    parser.add_argument("--max-gap-minutes", type=int, default=DEFAULT_MAX_GAP_MINUTES,
                        help=(f"窗口内相邻样本最大间隔分钟数，须 ≥ 期望间隔"
                              f"（默认 {DEFAULT_MAX_GAP_MINUTES}）"))
    parser.add_argument("--retention", "--max-rows", dest="retention", type=int,
                        default=DEFAULT_RETENTION,
                        help=(f"分析留存条数 {MIN_RETENTION}-{MAX_RETENTION}"
                              f"（默认 {DEFAULT_RETENTION}，保留最新 N 行）"))
    return parser


def run_audit(*, store: _history.Store, history: Path, output_dir: Path,
              window_minutes: int, expected_interval_minutes: int,
              max_gap_minutes: int, retention: int) -> dict[str, object]:
    """主管道：解析路径→读字节→逐行严格校验→全局校验→留存→分类→写出。

    数据域 blocked（non-ok-status-in-window / excessive-gap-in-window）
    是合法审计结论——照常写出报告；输入拒绝（AuditError）在任何输出
    写出前 raise，零输出。"""
    history_path = resolve_history_path(store, history)
    raw = store.read_bytes(history_path)
    input_sha256 = hashlib.sha256(raw).hexdigest()
    rows = parse_history(raw)
    validate_global(rows)
    omitted_older = max(0, len(rows) - retention)
    analyzed = rows[len(rows) - retention:] if omitted_older else rows
    result = classify(analyzed, window_minutes=window_minutes,
                      expected_interval_minutes=expected_interval_minutes,
                      max_gap_minutes=max_gap_minutes)
    report = build_report(
        input_sha256=input_sha256, input_bytes=len(raw), row_count=len(rows),
        analyzed_count=len(analyzed), omitted_older=omitted_older,
        window_minutes=window_minutes,
        expected_interval_minutes=expected_interval_minutes,
        max_gap_minutes=max_gap_minutes, retention=retention, result=result)
    write_outputs(store, output_dir, report)
    return report


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(f"{TAG} 输入: {args.history}（只读）", flush=True)
    problem = validate_settings(
        window_minutes=args.window_minutes,
        expected_interval_minutes=args.expected_interval_minutes,
        max_gap_minutes=args.max_gap_minutes, retention=args.retention)
    if problem is not None:
        print(f"{TAG} 拒绝: {problem}", flush=True)
        return EXIT_REFUSED
    try:
        report = run_audit(
            store=_history.RealStore(), history=args.history,
            output_dir=args.output_dir, window_minutes=args.window_minutes,
            expected_interval_minutes=args.expected_interval_minutes,
            max_gap_minutes=args.max_gap_minutes, retention=args.retention)
    except (AuditError, _history.HistoryError) as cause:
        print(f"{TAG} 拒绝: {cause}（fail-closed，输出零写入）", flush=True)
        return EXIT_REFUSED
    except OSError as cause:
        print(f"{TAG} 写失败: {type(cause).__name__}（输出保持原子，无 tmp 残留）",
              flush=True)
        return EXIT_REFUSED
    print(f"{TAG} 分类: {report['classification']}"
          + (f"（{', '.join(report['reasons'])}）" if report["reasons"] else ""),
          flush=True)
    print(f"{TAG} 锚点: {report['anchor_collected_at']} / "
          f"窗口起点: {report['window_start_collected_at']} / "
          f"入选 {report['selected_row_count']} 行", flush=True)
    print(f"{TAG} 输出: {REPORT_JSON_NAME} / {REPORT_MD_NAME} -> {args.output_dir}",
          flush=True)
    if report["classification"] == CLASS_PASS:
        return EXIT_PASS
    if report["classification"] == CLASS_PENDING:
        return EXIT_PENDING
    return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
