#!/usr/bin/env python
"""M14-109 监控阈值标定/评估：M14-13 canonical history.jsonl → 离线只读
分位数/候选阈值建议/阈值评估面（stdout only）。

设计（与 tools/ops/monitoring_history_query.py 同款纪律：单文件、纯标准
库、零第三方依赖；本工具是**纯只读标定/评估面**——零网络、零子进程、
零计划任务、零 env 读取、零生产容器/DB/对象存储接触、**零文件写入**
（只读输入文件、只写 stdout；绝不改动/删除任何输入工件）：

- 单一事实源（零平行 schema）：schema/画像常量与行级加载**复用同仓既有
  已测面**——``monitoring_history``（六服务/五端点画像、
  HISTORY_SCHEMA_VERSION、nearest-rank percentile、退出码）与
  ``monitoring_insights.parse_history_text``（canonical history 行级
  严格校验 + 行序严格递增 + 单一 project + 样本量界 1–5000——固定
  词汇拒绝原因原样透传）。**既有阈值基准与配置界复用同仓
  ``production_monitor`` 常量**（DEFAULT_LATENCY_WARN_MS=1000 /
  DEFAULT_LATENCY_CRITICAL_MS=5000 / DEFAULT_LOG_ERROR_WARN=5 /
  DEFAULT_LOG_ERROR_CRITICAL=20 及 MIN/MAX 界——仅导入常量定义，
  本工具从不调用其任何采集/网络/子进程面）。本工具未改动
  M14-13/M14-15/M14-108 任何既有行为。
- 输入面：默认 = ``monitoring_history`` canonical 输出
  ``.verify/artifacts/m14-13-monitoring-history/history.jsonl``（单一
  事实源常量派生）；``--history`` 可显式覆盖。symlink 目标/现存 symlink
  祖先组件、缺失、目录形态一律拒绝（复用 insights 已测语义）。
- 有界样本窗口：``--samples``（默认 200、1–5000，字符串解析 + 固定词汇
  拒绝）；**全文件行级校验恒覆盖全部行**（malformed/乱序/重复/混档/超
  5000 先于任何窗口语义拒绝），窗口取**最新 N 条**实际观测样本
  （``truncated_older_count`` 显式；不伪造时序、不填充缺失点）。
- 明确评估配置：``--latency-warn-ms``/``--latency-critical-ms``/
  ``--log-error-warn``/``--log-error-critical``（字符串解析；缺省 =
  production_monitor 既有默认阈值，source="existing-defaults"；显式提供
  即 source="explicit"）。参数校验（有限性/界/严格 warn<critical）全部
  **先于任何读取**（参数拒绝路径 RealStore 零构造），非法一律 exit 2
  且零部分 stdout 正文输出。
- 标定/评估口径（全部输出显式声明）：
  - 分位数：min/p50/p90/p95/p99/max，**nearest-rank**（rank =
    ceil(fraction·n)，升序取第 rank 个——与 monitoring_history 同款）。
  - 候选阈值建议：warn = 窗口 p95、critical = 窗口 p99（规则字面 echo）；
    ``applicable`` 机械判定（p95 < p99 且双双落在 production_monitor
    配置界内），不适用固定词汇原因（degenerate-window / below-minimum /
    above-maximum）——**建议是候选值不是部署授权**。
  - 阈值评估（候选与配置阈值同面）：告警语义 **value >= warn /
    value >= critical（含边界，与 production_monitor 同口径）**；
    coverage_rate = (value < warn 占比)、alarm_rate = (value >= warn
    占比)、critical_rate 同理；**ok_status_conflict = 阈值评估结果与
    历史 overall_status==ok 的代理分歧（overall_status 由既有受监控
    阈值生成，不是独立事件真值——故此指标不是误报率/真值标注）**，
    ok_status_conflict_rate 分母 = 窗口样本总数。配置阈值（缺省即既有
    阈值）与建议阈值（applicable 时）双评估并排 = 建议与既有阈值对比。
- 标定域：五端点 latency_ms 与六服务 log_error_totals（每样本可观测
  gauge 且既有阈值存在）。**restart 阈值为 M14-23 增量语义，累计
  restart_counts 不作标定输入——诚实排除**；容器 health 为状态非
  gauge，不在标定面。
- 输出卫生（stdout only，零落盘）：``--format summary``（默认人读）/
  ``--format json``（单文档机器可读：query echo / window 计数与状态
  计数 / 逐端点延迟与逐服务日志错误的分布+建议+双评估 / 边界注记）。
  绝无原始日志行/密钥/secret/env 值/绝对本机路径（输入显示恒为仓库
  相对或纯名）；任何拒绝 → 固定词汇拒绝行 + exit 2，正文零部分输出。
- 零墙钟：输出不含任何生成时间戳——同参数两次运行 stdout 逐字节相同。
- 边界：**这是离线标定/评估工具，不改变生产阈值、不接外部告警、不解除
  provider-smoke/long-soak/release gates，不构成 production readiness
  宣称，不授权任何部署**；``production_ready=false`` 不变。
- 退出码：0 标定/评估成功；2 任何拒绝（参数超界/非法、输入缺失/
  symlink/目录形态、malformed、乱序、重复、混档、超 5000、读失败）。

用法（仓库根）：
  python tools/ops/monitoring_threshold_calibration.py                    # 默认窗口+既有阈值
  python tools/ops/monitoring_threshold_calibration.py --samples 100
  python tools/ops/monitoring_threshold_calibration.py --format json \
      --latency-warn-ms 800 --latency-critical-ms 4000
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

try:  # 直接脚本运行：tools/ops 自身在 sys.path
    import monitoring_history as _history
    import monitoring_insights as _insights
    import production_monitor as _monitor
except ImportError:  # 经 importlib 按路径加载（契约测试形态）
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import monitoring_history as _history
    import monitoring_insights as _insights
    import production_monitor as _monitor

EXIT_OK = 0
EXIT_REFUSED = 2

TAG = "[calibrate]"
CALIBRATION_SCHEMA_VERSION = 1
TOOL_NAME = "tools/ops/monitoring_threshold_calibration.py"

#: 画像与 schema 常量：单一事实源 = 同仓 M14-13 工具
STACK_SERVICES: tuple[str, ...] = _history.STACK_SERVICES
ENDPOINT_IDS: tuple[str, ...] = _history.ENDPOINT_IDS
HISTORY_SCHEMA_VERSION = _history.HISTORY_SCHEMA_VERSION

#: canonical history 输入面（单一事实源 = monitoring_history 输出目录）
DEFAULT_HISTORY_PATH = _history.DEFAULT_OUTPUT_DIR / _history.HISTORY_OUTPUT_NAME

#: 既有阈值基准与配置界（单一事实源 = 同仓 M14-12 production_monitor 常量；
#: 仅导入常量定义——本工具从不调用其任何采集/网络/子进程面）
LATENCY_MIN_MS = _monitor.MIN_LATENCY_MS
LATENCY_MAX_MS = _monitor.MAX_LATENCY_MS
LOG_ERROR_MIN = _monitor.MIN_LOG_ERROR_THRESHOLD
LOG_ERROR_MAX = _monitor.MAX_LOG_ERROR_THRESHOLD
EXISTING_LATENCY_WARN_MS = _monitor.DEFAULT_LATENCY_WARN_MS
EXISTING_LATENCY_CRITICAL_MS = _monitor.DEFAULT_LATENCY_CRITICAL_MS
EXISTING_LOG_ERROR_WARN = _monitor.DEFAULT_LOG_ERROR_WARN
EXISTING_LOG_ERROR_CRITICAL = _monitor.DEFAULT_LOG_ERROR_CRITICAL

#: 有界样本窗口：默认 200，1–5000（与 insights 样本量上界一致）
DEFAULT_SAMPLES = 200
MIN_SAMPLES = 1
MAX_SAMPLES = 5000

#: 口径字面（echo 进输出——操作者无需理解内部细节即可复核）
QUANTILE_METHOD = "nearest-rank"
SUGGESTION_RULE = "p95-warn/p99-critical"
ALARM_SEMANTICS = "value >= warn / value >= critical（含边界，与 production_monitor 同口径）"
OK_STATUS_CONFLICT_REFERENCE = (
    "阈值评估结果与历史 overall_status==ok 的代理分歧——overall_status 由既有受监控"
    "阈值生成，不是独立事件真值，故不是误报率/真值标注（分母 = 窗口样本总数）")

#: 固定边界注记（summary 与 json 双形态恒包含）
BOUNDARY_NOTES: tuple[str, ...] = (
    "离线标定/评估工具——不改变生产阈值、不接外部告警、不解除 provider-smoke/long-soak/release gates",
    "建议为分位数派生的候选值（applicable=false 时不可直接配置）——不授权任何部署",
    (
        "ok_status_conflict = 阈值评估结果与历史 overall_status==ok 的代理分歧，不是误报率/真值标注"
        "（overall_status 由既有受监控阈值生成；分母 = 窗口样本总数）"
    ),
    "restart 阈值为 M14-23 增量语义，累计 restart_counts 不作标定输入——诚实排除",
    "不伪造时序、不填充缺失点——仅评估窗口内实际观测样本",
    "零网络/零子进程/零 env 读取/零文件写入/零墙钟（同参数两次运行 stdout 逐字节相同）",
    "production_ready=false 不变；不构成 production readiness 宣称",
)

#: 行级校验委托面（单一事实源 = monitoring_insights 已测校验器）
parse_history_text = _insights.parse_history_text


class CalibrationError(RuntimeError):
    """fail-closed 拒绝（固定词汇原因；绝不携带文件内容文本）。"""


# ---------------------------------------------------------------- Store（只读注入点）


class Store(Protocol):
    def exists(self, path: Path) -> bool: ...
    def is_dir(self, path: Path) -> bool: ...
    def is_symlink(self, path: Path) -> bool: ...
    def read_bytes(self, path: Path) -> bytes: ...


class RealStore:
    """真实文件面（只读四方法——本工具协议层面即无写面）。"""

    def exists(self, path: Path) -> bool:
        return path.exists()

    def is_dir(self, path: Path) -> bool:
        return path.is_dir()

    def is_symlink(self, path: Path) -> bool:
        return path.is_symlink()

    def read_bytes(self, path: Path) -> bytes:
        return path.read_bytes()


# ---------------------------------------------------------------- 参数校验（纯，先于任何读取）


@dataclass(frozen=True)
class CalibrationOptions:
    """已校验标定/评估参数（阈值缺省 = production_monitor 既有默认）。"""

    samples: int
    latency_warn: float
    latency_critical: float
    log_error_warn: int
    log_error_critical: int
    thresholds_source: str  # "existing-defaults" | "explicit"


def _parse_int_param(value: str, reason: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise CalibrationError(reason) from None


def _parse_float_param(value: str, reason: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise CalibrationError(reason) from None
    if not math.isfinite(number):
        raise CalibrationError(reason)
    return number


def parse_options(args: argparse.Namespace) -> CalibrationOptions:
    """全部参数校验先于任何读取（零读取拒绝；固定词汇，不回显被拒值）。"""
    samples = _parse_int_param(args.samples, "samples")
    if not MIN_SAMPLES <= samples <= MAX_SAMPLES:
        raise CalibrationError("samples")
    explicit = any(value is not None for value in (
        args.latency_warn_ms, args.latency_critical_ms,
        args.log_error_warn, args.log_error_critical))
    latency_warn = (EXISTING_LATENCY_WARN_MS if args.latency_warn_ms is None
                    else _parse_float_param(args.latency_warn_ms, "threshold-latency"))
    latency_critical = (EXISTING_LATENCY_CRITICAL_MS if args.latency_critical_ms is None
                        else _parse_float_param(args.latency_critical_ms, "threshold-latency"))
    if not (LATENCY_MIN_MS <= latency_warn <= LATENCY_MAX_MS
            and LATENCY_MIN_MS <= latency_critical <= LATENCY_MAX_MS
            and latency_warn < latency_critical):
        raise CalibrationError("threshold-latency")
    log_error_warn = (EXISTING_LOG_ERROR_WARN if args.log_error_warn is None
                      else _parse_int_param(args.log_error_warn, "threshold-log-error"))
    log_error_critical = (EXISTING_LOG_ERROR_CRITICAL if args.log_error_critical is None
                          else _parse_int_param(args.log_error_critical, "threshold-log-error"))
    if not (LOG_ERROR_MIN <= log_error_warn <= LOG_ERROR_MAX
            and LOG_ERROR_MIN <= log_error_critical <= LOG_ERROR_MAX
            and log_error_warn < log_error_critical):
        raise CalibrationError("threshold-log-error")
    return CalibrationOptions(
        samples=samples, latency_warn=latency_warn,
        latency_critical=latency_critical, log_error_warn=log_error_warn,
        log_error_critical=log_error_critical,
        thresholds_source="explicit" if explicit else "existing-defaults")


# ---------------------------------------------------------------- 加载与行校验（委托单一事实源）


def load_history(store: Store, path: Path) -> list[dict[str, object]]:
    """只读加载：symlink 防御（复用 insights 已测语义）+ canonical 行级校验
    （parse_history_text 透传——固定词汇拒绝原因原样透传）。"""
    try:
        _insights.reject_symlinked_path(store, path)  # type: ignore[arg-type]
    except _insights.InsightsError as cause:
        raise CalibrationError(str(cause)) from None
    if not store.exists(path):
        raise CalibrationError("history-missing")
    if store.is_dir(path):
        raise CalibrationError("history-kind")
    try:
        text = store.read_bytes(path).decode("utf-8")
    except UnicodeDecodeError:
        raise CalibrationError("not-json") from None
    try:
        return parse_history_text(text)
    except _insights.InsightsError as cause:
        raise CalibrationError(str(cause)) from None


# ---------------------------------------------------------------- 标定/评估（纯）


def _quantiles(values: list[float]) -> dict[str, object]:
    """nearest-rank 分位数（口径与 monitoring_history.percentile 同款）。"""
    ordered = sorted(values)
    return {
        "min": ordered[0],
        "p50": _history.percentile(ordered, 0.50),
        "p90": _history.percentile(ordered, 0.90),
        "p95": _history.percentile(ordered, 0.95),
        "p99": _history.percentile(ordered, 0.99),
        "max": ordered[-1],
    }


def _suggest(distribution: dict[str, object], low_bound: float,
             high_bound: float) -> dict[str, object]:
    """候选阈值建议：warn=p95 / critical=p99；applicable 机械判定
    （严格 p95<p99 且双双落在 production_monitor 配置界内）——固定词汇
    原因，绝不静默修正/抬升值。"""
    warn = distribution["p95"]
    critical = distribution["p99"]
    assert isinstance(warn, (int, float)) and isinstance(critical, (int, float))
    reason: str | None = None
    if not warn < critical:
        reason = "degenerate-window"
    elif warn < low_bound:
        reason = "below-minimum"
    elif critical > high_bound:
        reason = "above-maximum"
    return {
        "rule": SUGGESTION_RULE,
        "warn": warn,
        "critical": critical,
        "applicable": reason is None,
        "reason": reason,
        "bounds": {"min": low_bound, "max": high_bound},
    }


def _evaluate(pairs: list[tuple[float, bool]], warn: float,
              critical: float) -> dict[str, object]:
    """阈值评估（告警语义 >= 含边界，与 production_monitor 同口径）；
    ok_status_conflict = 告警 ∧ overall_status==ok 的代理分歧（不是误报率/
    真值标注——overall_status 由既有受监控阈值生成），分母 = 窗口样本总数。"""
    total = len(pairs)
    warn_count = sum(1 for value, _ in pairs if value >= warn)
    critical_count = sum(1 for value, _ in pairs if value >= critical)
    ok_conflicts = sum(1 for value, is_ok in pairs if value >= warn and is_ok)
    return {
        "warn": warn,
        "critical": critical,
        "samples": total,
        "warn_count": warn_count,
        "critical_count": critical_count,
        "ok_status_conflict_count": ok_conflicts,
        "coverage_rate": round((total - warn_count) / total, 6),
        "alarm_rate": round(warn_count / total, 6),
        "critical_rate": round(critical_count / total, 6),
        "ok_status_conflict_rate": round(ok_conflicts / total, 6),
    }


def _calibrate_metric(pairs: list[tuple[float, bool]], *, warn: float,
                      critical: float, low_bound: float,
                      high_bound: float) -> dict[str, object]:
    """单指标标定：分布 + 建议 + 配置阈值评估 + 建议阈值评估（applicable 时）。"""
    distribution = _quantiles([value for value, _ in pairs])
    suggestion = _suggest(distribution, low_bound, high_bound)
    result: dict[str, object] = {
        "samples": len(pairs),
        "distribution": distribution,
        "suggestion": suggestion,
        "evaluation_configured": _evaluate(pairs, warn, critical),
        "evaluation_suggested": (
            _evaluate(pairs, suggestion["warn"], suggestion["critical"])  # type: ignore[arg-type]
            if suggestion["applicable"] else None),
    }
    return result


def build_result(*, rows: list[dict[str, object]],
                 options: CalibrationOptions,
                 history_path: Path) -> dict[str, object]:
    """窗口选择（最新 N 条，全文件校验已先行）→ 逐指标标定 → 确定性结果。
    零墙钟：无任何生成/查询时间戳（时间边界取自数据本身）。"""
    window_rows = rows[len(rows) - options.samples:] if (
        len(rows) > options.samples) else rows
    truncated = len(rows) - len(window_rows)
    oldest, newest = window_rows[0], window_rows[-1]
    status_counts = {"ok": 0, "warn": 0, "critical": 0}
    for row in window_rows:
        status_counts[row["overall_status"]] += 1  # type: ignore[index]
    endpoint_block: dict[str, object] = {}
    for endpoint_id in ENDPOINT_IDS:
        pairs = [
            (float(row["endpoints"][endpoint_id]["latency_ms"]),  # type: ignore[index,arg-type]
             row["overall_status"] == "ok")
            for row in window_rows]
        endpoint_block[endpoint_id] = _calibrate_metric(
            pairs, warn=options.latency_warn, critical=options.latency_critical,
            low_bound=LATENCY_MIN_MS, high_bound=LATENCY_MAX_MS)
    service_block: dict[str, object] = {}
    for service in STACK_SERVICES:
        pairs = [
            (float(row["log_errors"][service]),  # type: ignore[index]
             row["overall_status"] == "ok")
            for row in window_rows]
        service_block[service] = _calibrate_metric(
            pairs, warn=options.log_error_warn,
            critical=options.log_error_critical,
            low_bound=LOG_ERROR_MIN, high_bound=LOG_ERROR_MAX)
    duration = int((newest["collected_dt"] - oldest["collected_dt"]).total_seconds())  # type: ignore[operator]
    return {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "project": rows[0]["project"],
        "query": {
            "history_file": _insights.display_path(history_path),
            "samples": options.samples,
            "evaluation_thresholds": {
                "latency_warn_ms": options.latency_warn,
                "latency_critical_ms": options.latency_critical,
                "log_error_warn": options.log_error_warn,
                "log_error_critical": options.log_error_critical,
                "source": options.thresholds_source,
            },
        },
        "window": {
            "records_scanned": len(rows),
            "records_used": len(window_rows),
            "truncated_older_count": truncated,
            "oldest_used_at": oldest["collected_at"],
            "newest_used_at": newest["collected_at"],
            "duration_seconds": duration,
            "status_counts": status_counts,
        },
        "quantile_method": QUANTILE_METHOD,
        "alarm_semantics": ALARM_SEMANTICS,
        "ok_status_conflict_reference": OK_STATUS_CONFLICT_REFERENCE,
        "endpoint_latency_ms": endpoint_block,
        "log_error_totals": service_block,
        "boundaries": list(BOUNDARY_NOTES),
    }


def run_calibration(*, store: Store, history_path: Path,
                    options: CalibrationOptions) -> dict[str, object]:
    rows = load_history(store, history_path)
    return build_result(rows=rows, options=options, history_path=history_path)


# ---------------------------------------------------------------- 摘要渲染


def _fmt_ms(value: object) -> str:
    return f"{value:.3f}" if isinstance(value, (int, float)) else "-"


def _fmt_count(value: object) -> str:
    return f"{value}" if isinstance(value, int) else "-"


def _fmt_rate(value: object) -> str:
    return f"{value:.6f}" if isinstance(value, (int, float)) else "-"


def _render_evaluation(label: str, evaluation: dict[str, object] | None,
                       *, count_fmt) -> list[str]:
    if evaluation is None:
        return []
    return [(
        f"{TAG}   {label}: warn={count_fmt(evaluation['warn'])} "
        f"critical={count_fmt(evaluation['critical'])} → 告警 "
        f"{evaluation['warn_count']}/{evaluation['samples']}"
        f"（critical {evaluation['critical_count']}）"
        f"覆盖 {evaluation['coverage_rate']:.6f} "
        f"ok分歧 {evaluation['ok_status_conflict_count']}/"
        f"{evaluation['samples']}（rate {evaluation['ok_status_conflict_rate']:.6f}，"
        f"代理分歧非误报率）"
    )]


def render_summary(result: dict[str, object]) -> str:
    """人读摘要（stdout only；固定词汇 + 边界注记；零墙钟）。"""
    query = result["query"]
    window = result["window"]
    thresholds = query["evaluation_thresholds"]
    assert isinstance(query, dict) and isinstance(window, dict)
    assert isinstance(thresholds, dict)
    status = window["status_counts"]
    assert isinstance(status, dict)
    lines: list[str] = [
        f"{TAG} 输入: {query['history_file']}（只读，绝不改动）",
        (f"{TAG} 窗口: 扫描 {window['records_scanned']} / 使用 "
         f"{window['records_used']}（窗口=最新 {query['samples']} 条，"
         f"省略更早 {window['truncated_older_count']}）"
         f"{window['oldest_used_at']} ~ {window['newest_used_at']}"
         f"（时长 {window['duration_seconds']}s）"),
        (f"{TAG} 状态: ok={status['ok']} warn={status['warn']} "
         f"critical={status['critical']}（ok_status_conflict 参考 = overall_status==ok"
         f" 样本——代理分歧，不是误报率/真值标注）"),
        (f"{TAG} 评估配置: latency warn/critical = "
         f"{thresholds['latency_warn_ms']:g}/"
         f"{thresholds['latency_critical_ms']:g} ms、log-error warn/critical = "
         f"{thresholds['log_error_warn']}/{thresholds['log_error_critical']}"
         f"（来源: {thresholds['source']}）"),
        (f"{TAG} 口径: 分位数 {result['quantile_method']}；告警语义 "
         f"{result['alarm_semantics']}"),
    ]
    latency = result["endpoint_latency_ms"]
    assert isinstance(latency, dict)
    for endpoint_id, block in latency.items():
        assert isinstance(block, dict)
        stats = block["distribution"]
        assert isinstance(stats, dict)
        lines.append(
            f"{TAG} 端点 {endpoint_id} (ms): 样本 {block['samples']} "
            f"分布 min/p50/p90/p95/p99/max = {_fmt_ms(stats['min'])}/"
            f"{_fmt_ms(stats['p50'])}/{_fmt_ms(stats['p90'])}/"
            f"{_fmt_ms(stats['p95'])}/{_fmt_ms(stats['p99'])}/"
            f"{_fmt_ms(stats['max'])}")
        lines += _render_threshold_block(block, count_fmt=_fmt_ms)
    logs = result["log_error_totals"]
    assert isinstance(logs, dict)
    for service, block in logs.items():
        assert isinstance(block, dict)
        stats = block["distribution"]
        assert isinstance(stats, dict)
        lines.append(
            f"{TAG} 服务 {service}: 样本 {block['samples']} "
            f"分布 min/p50/p90/p95/p99/max = {_fmt_count(stats['min'])}/"
            f"{_fmt_count(stats['p50'])}/{_fmt_count(stats['p90'])}/"
            f"{_fmt_count(stats['p95'])}/{_fmt_count(stats['p99'])}/"
            f"{_fmt_count(stats['max'])}")
        lines += _render_threshold_block(block, count_fmt=_fmt_count)
    for note in result["boundaries"]:  # type: ignore[arg-type]
        assert isinstance(note, str)
        lines.append(f"{TAG} 边界: {note}")
    return "\n".join(lines) + "\n"


def _render_threshold_block(block: dict[str, object], *,
                            count_fmt) -> list[str]:
    """单指标的建议行 + 双评估行（配置阈值评估 + 建议阈值评估）。"""
    suggestion = block["suggestion"]
    assert isinstance(suggestion, dict)
    lines: list[str]
    if suggestion["applicable"]:
        lines = [(f"{TAG}   建议阈值: warn={count_fmt(suggestion['warn'])} "
                  f"critical={count_fmt(suggestion['critical'])}"
                  f"（{suggestion['rule']}，适用）")]
    else:
        lines = [(f"{TAG}   建议阈值: warn={count_fmt(suggestion['warn'])} "
                  f"critical={count_fmt(suggestion['critical'])}"
                  f"（{suggestion['rule']}，不适用: {suggestion['reason']}）")]
    lines += _render_evaluation("配置阈值评估",
                                block["evaluation_configured"],  # type: ignore[arg-type]
                                count_fmt=count_fmt)
    lines += _render_evaluation("建议阈值评估",
                                block["evaluation_suggested"],  # type: ignore[arg-type]
                                count_fmt=count_fmt)
    return lines


# ---------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monitoring_threshold_calibration.py",
        description="M14-109 监控阈值标定/评估：canonical history.jsonl 离线只读面"
                    "（零网络/零子进程/零文件写入/零墙钟；不改变生产阈值）",
    )
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY_PATH,
                        help=f"history.jsonl 路径（默认 canonical {DEFAULT_HISTORY_PATH}，只读）")
    parser.add_argument("--samples", default=str(DEFAULT_SAMPLES),
                        help=f"样本窗口（最新 N 条）{MIN_SAMPLES}-{MAX_SAMPLES}"
                             f"（默认 {DEFAULT_SAMPLES}；非法值固定词汇拒绝）")
    parser.add_argument("--latency-warn-ms", default=None,
                        help=f"评估用延迟 warn 阈值 ms（缺省 = 既有默认 "
                             f"{EXISTING_LATENCY_WARN_MS:g}；界 "
                             f"{LATENCY_MIN_MS:g}-{LATENCY_MAX_MS:g}）")
    parser.add_argument("--latency-critical-ms", default=None,
                        help=f"评估用延迟 critical 阈值 ms（缺省 = 既有默认 "
                             f"{EXISTING_LATENCY_CRITICAL_MS:g}）")
    parser.add_argument("--log-error-warn", default=None,
                        help=f"评估用日志错误 warn 阈值（缺省 = 既有默认 "
                             f"{EXISTING_LOG_ERROR_WARN}；界 "
                             f"{LOG_ERROR_MIN}-{LOG_ERROR_MAX}）")
    parser.add_argument("--log-error-critical", default=None,
                        help=f"评估用日志错误 critical 阈值（缺省 = 既有默认 "
                             f"{EXISTING_LOG_ERROR_CRITICAL}）")
    parser.add_argument("--format", choices=("summary", "json"), default="summary",
                        help="输出形态：summary（默认，人读）或 json（单文档机器可读）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        options = parse_options(args)  # 全参数校验先于任何读取（零读取拒绝）
        result = run_calibration(store=RealStore(), history_path=args.history,
                                 options=options)
    except CalibrationError as cause:
        print(f"{TAG} 拒绝: {cause}（fail-closed，零部分输出）", flush=True)
        return EXIT_REFUSED
    except OSError as cause:
        print(f"{TAG} 读失败: {type(cause).__name__}（fail-closed，零部分输出）",
              flush=True)
        return EXIT_REFUSED
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False), flush=True)
    else:
        print(render_summary(result), end="", flush=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
