"""M14-186 Phase 0 完成度与 go/no-go 决策门 —— 只读本地决策工具。

M14-181 设计 §3 Phase 0 的**收口判定工具**：读取既有 Phase 0 证据目录
（M14-182 编排器产出的 `phase0-window-*.json` 窗报告），校验完成度与
数据质量，输出确定性 JSON 决策。Phase 1 采样在 phase0_go 之前不可授权
——这是本工具存在的唯一目的。

契约（fail-closed：任何缺陷/不完整/越界都绝不输出 go）：
- **只读本地**：零网络请求、零探针执行、零子进程；不改动证据目录中
  任何文件。输入 = 一个证据目录；`*.json` 按内容分类（窗报告入账 /
  聚合报告（M14-182 独立 schema）跳过 / 其余一律计畸形），`.reserve`
  预约残留文件显式忽略（只计数不解析）；
- **复用 M14-182 语义**：窗报告逐字段域校验复用
  public_edge_phase0_baseline.parse_window_report（bool 冒充数值、
  NaN/inf、域外 config、非 primary 资产等 = 畸形）；本地日期归账复用
  同一 `_local_date`；分位数复用 monitoring_history.percentile
  （nearest-rank，经 baseline.describe 单一事实源）；
- **完成度检查**（M14-181 §3 预算）：恰 3 个本地日期 × 每日恰 3 窗 ×
  每窗恰 8 样本 = 每日 24、总计 72；且逐日 ≤24、总计 ≤72。数量不足 =
  incomplete；窗数/日期数超出计划或预算越界 = violation——两者都只能
  落 phase0_inconclusive，绝不 go；
- **质量检查**：零畸形文件、零失败样本（failure_category 非空）、零
  缺失 TTFB；"每窗 8 个成功样本"由结构完成度（每窗恰 8 样本）+ 零失败
  + 零缺失 TTFB 组合等价实现；分布成型 = 成功 TTFB 样本 ≥24（一个
  完整日的量，p50/p95 的最低辩护下限）；
- **统计诚实边界**（M14-181）：仅描述性统计——TTFB p50/p95/max、
  失败数、缺失 TTFB 数、慢样本（TTFB > 2500ms）、慢窗（含至少一个
  慢样本的窗）及两种频率；**不设 p99、不设 99.5% 成功率门**（该样本
  量不支持尾部/低频事件门禁，键域恒不存在）；
- **决策规则**（唯一事实源 = thresholds 常量，CLI 不可覆写）：
  phase0_go ⇔ 完整 ∧ 零缺陷 ∧ 分布成型 ∧ 慢窗频率 ≥5%；
  phase0_no_go_close ⇔ 完整 ∧ 零失败/零缺失 ∧ 慢窗频率 <1% ∧ TTFB
  p95 ≤2500ms；两者之间（含 1%≤频率<5% 中间带、频率 <1% 但 p95
  >2500ms）或任何数据缺陷 = phase0_inconclusive + 显式 reason codes。
  完成数据恰 9 窗时频率量化为 0% 或 ≥1/9≈11.1%，中间带经真实文件
  不可达，规则仍保留（防窗数契约演进）；
- **phase1_sampling_authorized 恒等于 (decision == phase0_go)**；
- 缺目录语义（与 aggregate 拒绝空目录不同）：目录不存在 = 尚未开始
  采样，是**可判定的** phase0_inconclusive（reason:
  evidence_directory_missing），不是运行错误——决策门对"零证据"
  的诚实回答就是"不完整、不授权"；
- 输出：默认打印单个可解析 JSON 文档到 stdout（信息行走 stderr）；
  `--output` 时按 M14-182 同款碰撞保护落盘（O_CREAT|O_EXCL 独占
  `<output>.reserve`，已有报告/预约残留拒绝，写出失败保留预约，成功
  释放），写复用探针原子写（temp + os.replace）；落盘 JSON 不含本机
  绝对路径、不含任何 secret。

Exit codes: 0 = 决策已产出（go/no_go_close/inconclusive 都是合法结论，
信息均含 reason codes）；2 = 运行失败（参数非法、证据路径不是目录、
目录列举失败、输出路径碰撞/写出失败）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:  # 直接脚本运行：tools/ops 自身在 sys.path
    import public_edge_phase0_baseline as _baseline
    import public_edge_stability_probe as _edge_probe
except ImportError:  # 经 importlib 按路径加载（契约测试形态）
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import public_edge_phase0_baseline as _baseline
    import public_edge_stability_probe as _edge_probe

DECISION_SCHEMA = "aios-public-edge-phase0-decision/1"
TOOL_NAME = "public_edge_phase0_decision"

# 完成度契约（M14-181 §3：每日 3 窗 × 8 样本 × 连续 3 天）。
REQUIRED_DATES = 3
REQUIRED_WINDOWS_PER_DATE = 3
REQUIRED_SAMPLES_PER_WINDOW = _baseline.MAX_SAMPLES_PER_WINDOW  # 8
REQUIRED_SAMPLES_PER_DATE = REQUIRED_WINDOWS_PER_DATE * REQUIRED_SAMPLES_PER_WINDOW  # 24
REQUIRED_TOTAL_SAMPLES = _baseline.TOTAL_BUDGET  # 72
DAILY_BUDGET_MAX = _baseline.DAILY_BUDGET  # 24
TOTAL_BUDGET_MAX = _baseline.TOTAL_BUDGET  # 72

# 决策阈值（M14-181 §3 Phase 0 门；不可经 CLI 覆写——门禁常量）。
SLOW_TTFB_MS = _baseline.SLOW_TTFB_MS  # 2500.0
GO_SLOW_WINDOW_FREQ_MIN = 0.05
NO_GO_SLOW_WINDOW_FREQ_MAX = 0.01
NO_GO_TTFB_P95_MAX_MS = 2500.0
DISTRIBUTION_MIN_TTFB_SAMPLES = 24  # 一个完整日的成功样本量 = 分布成型下限

DECISION_GO = "phase0_go"
DECISION_NO_GO_CLOSE = "phase0_no_go_close"
DECISION_INCONCLUSIVE = "phase0_inconclusive"

WINDOW_SCHEMA = _baseline.WINDOW_SCHEMA
AGGREGATE_SCHEMA = _baseline.AGGREGATE_SCHEMA

# reason codes（固定词表；malformed_report:<name> 为逐文件展开）
REASON_DIRECTORY_MISSING = "evidence_directory_missing"
REASON_DATES_BELOW = "distinct_dates_below_required"
REASON_DATES_ABOVE = "distinct_dates_above_required"
REASON_WINDOWS_BELOW = "windows_per_date_below_required"
REASON_WINDOWS_ABOVE = "windows_per_date_above_required"
REASON_WINDOW_SAMPLES_BELOW = "samples_per_window_below_required"
REASON_DAILY_BELOW = "daily_samples_below_required"
REASON_DAILY_EXCEEDED = "daily_budget_exceeded"
REASON_TOTAL_BELOW = "total_samples_below_required"
REASON_TOTAL_EXCEEDED = "total_budget_exceeded"
REASON_FAILURES = "window_failures_present"
REASON_TTFB_MISSING = "missing_ttfb_present"
REASON_DISTRIBUTION = "distribution_not_formed"
REASON_MIDDLE_BAND = "slow_window_frequency_in_middle_band"
REASON_P95_ABOVE = "ttfb_p95_above_no_go_threshold"
REASON_P95_UNAVAILABLE = "ttfb_p95_unavailable"
REASON_GO_MET = "slow_window_frequency_ge_go_threshold"
REASON_NO_GO_FREQ = "slow_window_frequency_below_no_go_threshold"
REASON_NO_GO_P95 = "ttfb_p95_within_no_go_threshold"

Phase0Error = _baseline.Phase0Error  # 与 M14-182 同一错误通道（fail-closed）


# ---------------------------------------------------------------- 证据扫描


@dataclass(frozen=True)
class MalformedFile:
    name: str
    error: str


@dataclass(frozen=True)
class EvidenceScan:
    directory_missing: bool
    windows: tuple[_baseline.WindowReport, ...]
    aggregates_skipped: tuple[str, ...]
    malformed: tuple[MalformedFile, ...]
    reserve_files_ignored: int


def scan_evidence(evidence_dir: Path) -> EvidenceScan:
    """只读扫描证据目录：*.json 按内容分类，.reserve 显式忽略。

    目录不存在 = 零证据（调用方转可判定 inconclusive）；路径存在但不是
    目录 = 运行错误（Phase0Error → exit 2）。畸形摘要只记异常类型名，
    绝不透传 OSError 消息文本（可能内嵌本机绝对路径，落盘卫生）。
    """
    if not evidence_dir.exists():
        return EvidenceScan(
            directory_missing=True,
            windows=(),
            aggregates_skipped=(),
            malformed=(),
            reserve_files_ignored=0,
        )
    if not evidence_dir.is_dir():
        raise Phase0Error(f"证据路径不是目录: {evidence_dir.name!r}")
    try:
        reserve_count = sum(1 for _ in evidence_dir.glob("*.reserve"))
        json_paths = sorted(evidence_dir.glob("*.json"))
    except OSError as cause:
        raise Phase0Error(f"证据目录列举失败: {type(cause).__name__}") from cause
    windows: list[_baseline.WindowReport] = []
    aggregates: list[str] = []
    malformed: list[MalformedFile] = []
    for path in json_paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as cause:
            malformed.append(MalformedFile(name=path.name, error=type(cause).__name__))
            continue
        if isinstance(payload, dict) and payload.get("schema") == AGGREGATE_SCHEMA:
            aggregates.append(path.name)  # 聚合报告是合法目录成员，不入决策账
            continue
        try:
            windows.append(_baseline.parse_window_report(payload, path.name))
        except Phase0Error as cause:
            malformed.append(MalformedFile(name=path.name, error=str(cause)))
    return EvidenceScan(
        directory_missing=False,
        windows=tuple(windows),
        aggregates_skipped=tuple(aggregates),
        malformed=tuple(malformed),
        reserve_files_ignored=reserve_count,
    )


# ---------------------------------------------------------------- 决策规则（纯函数）


def decide(
    *,
    directory_missing: bool = False,
    malformed_names: tuple[str, ...] = (),
    windows_per_date: tuple[int, ...] = (),
    samples_per_window: tuple[int, ...] = (),
    samples_per_date: tuple[int, ...] = (),
    total_samples: int = 0,
    failures: int = 0,
    ttfb_missing: int = 0,
    ttfb_samples: int = 0,
    slow_window_frequency: float = 0.0,
    ttfb_p95_ms: float | None = None,
) -> tuple[str, list[str], dict[str, bool]]:
    """完成度/质量 → (decision, reason codes, completion checks)。

    纯函数（不触盘不触钟）：规则表可独立单元测试（含真实文件量化
    不可达的 1%≤频率<5% 中间带）。reason 顺序确定：结构 → 缺陷 →
    分布 → 频率分支。
    """
    reasons: list[str] = []
    if directory_missing:
        reasons.append(REASON_DIRECTORY_MISSING)
    for name in malformed_names:
        reasons.append(f"malformed_report:{name}")

    # 日期集以样本归账为准（≥ 窗归期键集）：跨本地午夜的窗会把部分
    # 样本计入次日，形成"有样本无窗"的日历日——按样本计数更诚实，
    # 且该日窗数 0 < 3 自然落入不完整。
    distinct_dates = len(samples_per_date)
    exactly_dates = distinct_dates == REQUIRED_DATES
    exactly_windows = bool(windows_per_date) and all(
        count == REQUIRED_WINDOWS_PER_DATE for count in windows_per_date
    )
    exactly_window_samples = bool(samples_per_window) and all(
        count == REQUIRED_SAMPLES_PER_WINDOW for count in samples_per_window
    )
    exactly_daily_samples = bool(samples_per_date) and all(
        count == REQUIRED_SAMPLES_PER_DATE for count in samples_per_date
    )
    exactly_total = total_samples == REQUIRED_TOTAL_SAMPLES
    daily_within = all(count <= DAILY_BUDGET_MAX for count in samples_per_date)
    total_within = total_samples <= TOTAL_BUDGET_MAX

    if not exactly_dates:
        reasons.append(
            REASON_DATES_BELOW if distinct_dates < REQUIRED_DATES else REASON_DATES_ABOVE
        )
    if any(count < REQUIRED_WINDOWS_PER_DATE for count in windows_per_date):
        reasons.append(REASON_WINDOWS_BELOW)
    if any(count > REQUIRED_WINDOWS_PER_DATE for count in windows_per_date):
        reasons.append(REASON_WINDOWS_ABOVE)
    if any(count < REQUIRED_SAMPLES_PER_WINDOW for count in samples_per_window):
        reasons.append(REASON_WINDOW_SAMPLES_BELOW)
    if any(count < REQUIRED_SAMPLES_PER_DATE for count in samples_per_date):
        reasons.append(REASON_DAILY_BELOW)
    if any(count > DAILY_BUDGET_MAX for count in samples_per_date):
        reasons.append(REASON_DAILY_EXCEEDED)
    if total_samples < REQUIRED_TOTAL_SAMPLES:
        reasons.append(REASON_TOTAL_BELOW)
    if total_samples > TOTAL_BUDGET_MAX:
        reasons.append(REASON_TOTAL_EXCEEDED)
    if failures:
        reasons.append(REASON_FAILURES)
    if ttfb_missing:
        reasons.append(REASON_TTFB_MISSING)
    distribution_formed = ttfb_samples >= DISTRIBUTION_MIN_TTFB_SAMPLES
    if not distribution_formed:
        reasons.append(REASON_DISTRIBUTION)

    checks = {
        "exactly_3_distinct_local_dates": exactly_dates,
        "exactly_3_windows_per_date": exactly_windows,
        "exactly_8_samples_per_window": exactly_window_samples,
        "exactly_24_samples_per_date": exactly_daily_samples,
        "exactly_72_total_samples": exactly_total,
        "daily_budget_within_24": daily_within,
        "total_budget_within_72": total_within,
        "zero_malformed_reports": not malformed_names,
        "zero_failure_samples": failures == 0,
        "zero_missing_ttfb": ttfb_missing == 0,
        "distribution_formed": distribution_formed,
        "complete": (
            exactly_dates
            and exactly_windows
            and exactly_window_samples
            and exactly_daily_samples
            and exactly_total
        ),
    }
    has_defect = (
        directory_missing
        or bool(malformed_names)
        or failures
        or ttfb_missing
        or not daily_within
        or not total_within
        or not distribution_formed
    )
    if not checks["complete"] or has_defect:
        return DECISION_INCONCLUSIVE, reasons, checks
    if slow_window_frequency >= GO_SLOW_WINDOW_FREQ_MIN:
        return DECISION_GO, reasons + [REASON_GO_MET], checks
    if slow_window_frequency < NO_GO_SLOW_WINDOW_FREQ_MAX:
        if ttfb_p95_ms is None:
            return DECISION_INCONCLUSIVE, reasons + [REASON_P95_UNAVAILABLE], checks
        if ttfb_p95_ms <= NO_GO_TTFB_P95_MAX_MS:
            return (
                DECISION_NO_GO_CLOSE,
                reasons + [REASON_NO_GO_FREQ, REASON_NO_GO_P95],
                checks,
            )
        return DECISION_INCONCLUSIVE, reasons + [REASON_P95_ABOVE], checks
    return DECISION_INCONCLUSIVE, reasons + [REASON_MIDDLE_BAND], checks


# ---------------------------------------------------------------- 报告组装


def build_report(evidence_dir: Path) -> dict[str, Any]:
    """组装决策报告（确定性：同一输入唯一 JSON，generated_at 除外）。"""
    scan = scan_evidence(evidence_dir)
    windows = scan.windows
    all_samples = [sample for window in windows for sample in window.samples]
    ttfb_values = [s.ttfb_ms for s in all_samples if s.ttfb_ms is not None]
    ttfb_stats = _baseline.describe(list(ttfb_values))
    windows_per_date: dict[str, int] = {}
    samples_per_date: dict[str, int] = {}
    for window in windows:  # 窗归其首样本本地日期（窗恒 ≥1 样本，parser 保证）
        date = window.samples[0].local_date
        windows_per_date[date] = windows_per_date.get(date, 0) + 1
    for sample in all_samples:  # 样本逐个按自身本地日期入账（与预算门同口径）
        samples_per_date[sample.local_date] = samples_per_date.get(sample.local_date, 0) + 1
    slow_window_files = [
        window.path_name
        for window in windows
        if any(s.ttfb_ms is not None and s.ttfb_ms > SLOW_TTFB_MS for s in window.samples)
    ]
    failures = sum(1 for s in all_samples if s.failure_category is not None)
    ttfb_missing = len(all_samples) - len(ttfb_values)
    decision, reasons, checks = decide(
        directory_missing=scan.directory_missing,
        malformed_names=tuple(entry.name for entry in scan.malformed),
        windows_per_date=tuple(windows_per_date.values()),
        samples_per_window=tuple(len(window.samples) for window in windows),
        samples_per_date=tuple(samples_per_date.values()),
        total_samples=len(all_samples),
        failures=failures,
        ttfb_missing=ttfb_missing,
        ttfb_samples=len(ttfb_values),
        slow_window_frequency=(len(slow_window_files) / len(windows)) if windows else 0.0,
        ttfb_p95_ms=ttfb_stats["p95"],
    )
    return {
        "schema": DECISION_SCHEMA,
        "tool": TOOL_NAME,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "evidence_directory": evidence_dir.name or ".",
        "input_files": {
            "windows": [window.path_name for window in windows],
            "aggregates_skipped": list(scan.aggregates_skipped),
            "malformed": [
                {"file": entry.name, "error": entry.error} for entry in scan.malformed
            ],
            "reserve_files_ignored": scan.reserve_files_ignored,
        },
        "counts": {
            "windows": len(windows),
            "samples": len(all_samples),
            "successful_samples": len(all_samples) - failures,
            "failures": failures,
            "ttfb_missing": ttfb_missing,
            "ttfb_samples": len(ttfb_values),
            "slow_samples": sum(1 for value in ttfb_values if value > SLOW_TTFB_MS),
            "slow_windows": len(slow_window_files),
            "per_local_date": {
                date: {
                    "windows": windows_per_date.get(date, 0),
                    "samples": samples_per_date.get(date, 0),
                }
                for date in sorted(set(windows_per_date) | set(samples_per_date))
            },
        },
        "metrics": {
            "ttfb_ms": ttfb_stats,
            "slow_sample_frequency": (
                sum(1 for value in ttfb_values if value > SLOW_TTFB_MS) / len(ttfb_values)
                if ttfb_values
                else 0.0
            ),
            "slow_window_frequency": (
                len(slow_window_files) / len(windows) if windows else 0.0
            ),
            "slow_window_files": slow_window_files,
        },
        "thresholds": {
            "slow_ttfb_ms": SLOW_TTFB_MS,
            "slow_window_frequency_go_min": GO_SLOW_WINDOW_FREQ_MIN,
            "slow_window_frequency_no_go_max": NO_GO_SLOW_WINDOW_FREQ_MAX,
            "ttfb_p95_no_go_max_ms": NO_GO_TTFB_P95_MAX_MS,
            "distribution_min_ttfb_samples": DISTRIBUTION_MIN_TTFB_SAMPLES,
            "required": {
                "distinct_local_dates": REQUIRED_DATES,
                "windows_per_date": REQUIRED_WINDOWS_PER_DATE,
                "samples_per_window": REQUIRED_SAMPLES_PER_WINDOW,
                "samples_per_date": REQUIRED_SAMPLES_PER_DATE,
                "total_samples": REQUIRED_TOTAL_SAMPLES,
            },
            "budget": {"daily_max": DAILY_BUDGET_MAX, "total_max": TOTAL_BUDGET_MAX},
        },
        "completion_checks": checks,
        "decision": decision,
        "reason_codes": reasons,
        "phase1_sampling_authorized": decision == DECISION_GO,
        "honest_boundaries": {
            "statistical_scope": "descriptive-only",
            "no_p99_gate": True,
            "no_availability_rate_gate": True,
            "decision_gate": "slow-window frequency + TTFB p50/p95/max only (M14-181 Phase 0)",
            "slow_window_frequency_quantization": (
                "完成数据恰 9 窗时频率只能为 0% 或 >=1/9≈11.1%；[1%,5%) 中间带"
                "在该量化下经真实文件不可达，规则分支仍保留以防窗数契约演进"
            ),
            "note": (
                "M14-181 统计诚实边界：每日 ≤24 / 总计 72 的样本量只支持描述性"
                "结论，不设 p99 或 99.5% 成功率门；本工具只读本地证据、不发请求"
                "不执行探针，phase1_sampling_authorized 仅在 phase0_go 时为 true"
            ),
        },
    }


# ---------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Phase 0 完成度与 go/no-go 决策门（M14-186，只读本地工具）：读取"
            "证据目录中的 phase0-window-*.json 窗报告（忽略 .reserve 与聚合"
            "报告），零网络请求、零探针执行；校验完成度（3 本地日期 × 每日"
            " 3 窗 × 每窗 8 成功样本 = 每日 24、总计 72，且逐日 ≤24、总计"
            " ≤72）与数据质量（零畸形/零失败/零缺失 TTFB），输出确定性 JSON"
            " 决策 phase0_go / phase0_no_go_close / phase0_inconclusive 及"
            "显式 reason codes；仅 phase0_go 时 phase1_sampling_authorized"
            "=true。慢窗 = TTFB > 2500ms；门只用慢窗频率与 p50/p95/max"
            "（M14-181 统计诚实边界：不设 p99 / 99.5% 成功率门）。"
        ),
    )
    parser.add_argument(
        "--evidence-dir",
        required=True,
        help="gitignored 证据目录（M14-182 编排器产出的窗报告所在地）",
    )
    parser.add_argument(
        "--output",
        help="可选：把决策报告 JSON 原子写到该路径（O_EXCL 预约碰撞保护，"
        "已有报告/预约残留拒绝；缺省仅打印 stdout）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    evidence_dir = Path(args.evidence_dir)
    try:
        report = build_report(evidence_dir)
    except Phase0Error as cause:
        print(f"[phase0-decision] FAIL: {cause}", file=sys.stderr)
        return 2

    if args.output:
        output_path = Path(args.output)
        try:
            reserve = _baseline._reserve_output_path(output_path)
        except Phase0Error as cause:
            print(f"[phase0-decision] FAIL: {cause}", file=sys.stderr)
            return 2
        try:
            _edge_probe._write_report_atomic(str(output_path), report)
        except OSError as cause:
            # 写出失败保留预约（与 M14-182 同款：同路径不可复用）
            print(
                f"[phase0-decision] FAIL: 决策报告写出失败"
                f"（{type(cause).__name__}；预约保留: {reserve.name}）",
                file=sys.stderr,
            )
            return 2
        os.unlink(reserve)
        print(f"[phase0-decision] report written: {output_path.name}", file=sys.stderr)

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
