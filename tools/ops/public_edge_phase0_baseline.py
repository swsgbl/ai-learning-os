"""M14-182 Phase 0 公网边缘基线采样编排器 —— plan / execute / aggregate。

M14-181 设计的 Phase 0 落地工具：编排（不重复实现）既有
tools/ops/public_edge_stability_probe.py 完成**一个**有界采样窗，并在
执行前后强制 M14-181 §3 的预算与统计口径。

契约（fail-closed，违反任何一条即拒绝且零探针调用）：
- 默认 plan-only：打印将执行的探针命令与预算预检结果，不发任何请求、
  不写任何文件；网络执行必须同时给 --execute 与精确确认短语
  --confirm-phrase "EXECUTE PUBLIC EDGE PHASE0 WINDOW"（逐字符相等）；
- 一次调用 = 一个有界窗：--samples 默认且上限 8；--interval 下限 1.0s；
  --timeout 上限 15s；HTTP 版本恒 1.1（本工具不暴露 h2 选项——Phase 0
  契约）；**绝不**传 --large-asset-*（APK 是 §5 独立预算路径）、绝不传
  --ssl-no-revoke（诊断模式不入基线）；
- 预算门（M14-181 §3）：执行前解析证据目录中全部既有窗报告，
  manifest 请求总数 ≤ 72 且**同本地日期** ≤ 24（直连+代理合计）；
  任何不可解析/未知 schema/域外值的文件（畸形或歧义历史，含 bool 冒充
  数值与 NaN/inf）都拒绝执行；plan/execute 把不存在的证据目录视为
  零历史（首个窗合法起点），**aggregate 拒绝不存在的目录**（不编造
  空聚合）；
- 输出碰撞保护：执行前以 O_CREAT|O_EXCL 独占预约 `<output>.reserve`，
  已有报告或已有预约残留都零请求拒绝；**失败后预约保留**——同一输出
  路径不可复用（防部分写入/同秒重跑掩盖失败），成功才删除预约；
- 零重试：探针一次运行（其自身也是零重试契约）；窗失败如实退出，
  绝不自动补跑；
- 输出到调用者指定的 gitignored 证据目录：窗报告由探针原子写为
  phase0-window-<stamp>-<mode>.json；聚合报告原子写为
  phase0-aggregate-<stamp>.json；落盘 JSON 不含本机绝对路径、代理
  凭据面（代理入口校验拒绝 userinfo）或任何 secret；
- aggregate 只读：零请求、不改动既有文件，仅计算描述性统计——样本数、
  失败数、慢窗频率（TTFB > 2500ms）、p50/p95/max（nearest-rank，单一
  事实源 = monitoring_history.percentile）按 overall/direct/proxy/本地
  日期分组；**明确不输出 p99 或 99.5% 成功率门**（M14-181 统计诚实
  边界：≤24 样本/日不支持尾部/低频事件门禁）。

Exit codes: 0 = plan 通过 / execute 完成（透传探针 0=全绿 1=存在失败
样本）/ aggregate 完成；2 = 参数非法、确认短语不符、历史畸形/歧义、
预算越界、探针工具失败（exit ≥2）或窗报告写出后校验失败。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

try:  # 直接脚本运行：tools/ops 自身在 sys.path
    import monitoring_history as _history
    import public_edge_stability_probe as _edge_probe
except ImportError:  # 经 importlib 按路径加载（契约测试形态）
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import monitoring_history as _history
    import public_edge_stability_probe as _edge_probe

CONFIRM_PHRASE = "EXECUTE PUBLIC EDGE PHASE0 WINDOW"
DEFAULT_SAMPLES = 8
MAX_SAMPLES_PER_WINDOW = 8
TOTAL_BUDGET = 72
DAILY_BUDGET = 24
MIN_INTERVAL_S = 1.0
MAX_INTERVAL_S = 60.0
MAX_TIMEOUT_S = 15.0
PROBE_TIMEOUT_GRACE_S = 120.0  # subprocess 兜底超时：8 样本 × (15s+1s) + 余量
SLOW_TTFB_MS = 2500.0

WINDOW_SCHEMA = _edge_probe.SCHEMA  # aios-public-edge-stability-probe/1
AGGREGATE_SCHEMA = "aios-public-edge-phase0-aggregate/1"
WINDOW_PREFIX = "phase0-window-"
AGGREGATE_PREFIX = "phase0-aggregate-"
WINDOW_GLOB = "*.json"

MODE_DIRECT = _edge_probe.MODE_DIRECT
MODE_PROXY = _edge_probe.MODE_PROXY


class Phase0Error(Exception):
    """参数/历史/预算非法（fail-closed，零探针调用路径）。"""


def _finite_number(
    value: Any, *, field: str, path_name: str, allow_none: bool = False
) -> float | None:
    """数值域校验：bool 一律拒绝（bool 是 int 子类，不显式排除会漏过）；
    NaN/inf/-inf 一律拒绝（JSON 解析可产生它们，统计与预算不容纳）。"""
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Phase0Error(f"{path_name}: {field} 必须是数值（bool 拒绝）")
    number = float(value)
    if not math.isfinite(number):
        raise Phase0Error(f"{path_name}: {field} 必须是有限值（NaN/inf 拒绝）")
    return number


def _positive_int(value: Any, *, field: str, path_name: str) -> int:
    """整数域校验（bool 拒绝）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise Phase0Error(f"{path_name}: {field} 必须是整数（bool 拒绝）")
    return value


# ---------------------------------------------------------------- 历史解析


@dataclass(frozen=True)
class SampleFacts:
    local_date: str
    probe_mode: str
    ttfb_ms: float | None
    elapsed_ms: float | None
    failure_category: str | None


@dataclass(frozen=True)
class WindowReport:
    path_name: str
    samples: tuple[SampleFacts, ...]


@dataclass(frozen=True)
class PhaseHistory:
    windows: tuple[WindowReport, ...] = field(default_factory=tuple)
    total_manifest_requests: int = 0
    per_local_date: dict[str, int] = field(default_factory=dict)


def _local_date(iso_started_at: str) -> str:
    """样本 started_at（ISO8601 带偏移）→ 本地日期（预算按本地日历计）。"""
    try:
        moment = datetime.fromisoformat(iso_started_at)
    except ValueError as cause:
        raise Phase0Error(f"started_at 不可解析: {iso_started_at!r}") from cause
    if moment.tzinfo is None:
        raise Phase0Error("started_at 缺少时区偏移")
    return moment.astimezone().date().isoformat()  # astimezone() 无参 = 本地时区


def parse_window_report(payload: Any, path_name: str) -> WindowReport:
    """校验一份窗报告的完整域（schema/config/samples 逐字段）。

    任何域外值（schema 不符、含 large 资产、h2、ssl-no-revoke、样本数
    >8、interval<1、timeout>15、缺 started_at 等）都视为畸形历史——
    调用方以 Phase0Error 拒绝执行（零探针调用）。
    """
    if not isinstance(payload, dict):
        raise Phase0Error(f"{path_name}: 不是 JSON 对象")
    if payload.get("schema") != WINDOW_SCHEMA or payload.get("tool") != _edge_probe.TOOL_NAME:
        raise Phase0Error(f"{path_name}: schema/tool 不是本工具的窗报告")
    config = payload.get("config")
    if not isinstance(config, dict):
        raise Phase0Error(f"{path_name}: 缺 config")
    if config.get("large_asset") is not None:
        raise Phase0Error(f"{path_name}: Phase 0 窗报告不得含大资产样本")
    if config.get("http_version_requested") != _edge_probe.HTTP11:
        raise Phase0Error(f"{path_name}: Phase 0 窗报告必须全程 HTTP/1.1")
    if config.get("ssl_no_revoke") is not False:
        raise Phase0Error(f"{path_name}: Phase 0 窗报告不得开启 ssl-no-revoke")
    if config.get("probe_mode") not in (MODE_DIRECT, MODE_PROXY):
        raise Phase0Error(f"{path_name}: probe_mode 非法")
    samples_n = _positive_int(config.get("samples"), field="config.samples", path_name=path_name)
    if not 1 <= samples_n <= MAX_SAMPLES_PER_WINDOW:
        raise Phase0Error(f"{path_name}: config.samples 越界（1-{MAX_SAMPLES_PER_WINDOW}）")
    interval = _finite_number(config.get("interval_s"), field="interval_s", path_name=path_name)
    if interval is None or interval < MIN_INTERVAL_S:
        raise Phase0Error(f"{path_name}: interval_s 违反下限 {MIN_INTERVAL_S}s")
    timeout = _finite_number(config.get("timeout_s"), field="timeout_s", path_name=path_name)
    if timeout is None or timeout > MAX_TIMEOUT_S:
        raise Phase0Error(f"{path_name}: timeout_s 违反上限 {MAX_TIMEOUT_S}s")
    rows = payload.get("samples")
    if not isinstance(rows, list) or len(rows) != samples_n:
        raise Phase0Error(f"{path_name}: samples 长度与 config.samples 不一致")
    facts: list[SampleFacts] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("asset") != "primary":
            raise Phase0Error(f"{path_name}: 含非 primary 资产样本（Phase 0 禁止）")
        category = row.get("failure_category")
        if category is not None and not isinstance(category, str):
            raise Phase0Error(f"{path_name}: failure_category 非法")
        ttfb = _finite_number(row.get("ttfb_ms"), field="ttfb_ms", path_name=path_name, allow_none=True)
        elapsed = _finite_number(row.get("elapsed_ms"), field="elapsed_ms", path_name=path_name, allow_none=True)
        started = row.get("started_at")
        if not isinstance(started, str):
            raise Phase0Error(f"{path_name}: 样本缺 started_at")
        mode = row.get("probe_mode")
        if mode not in (MODE_DIRECT, MODE_PROXY):
            raise Phase0Error(f"{path_name}: 样本 probe_mode 非法")
        facts.append(
            SampleFacts(
                local_date=_local_date(started),
                probe_mode=mode,
                ttfb_ms=ttfb,
                elapsed_ms=elapsed,
                failure_category=category,
            )
        )
    return WindowReport(path_name=path_name, samples=tuple(facts))


def load_history(evidence_dir: Path, *, allow_missing: bool = False) -> PhaseHistory:
    """解析证据目录全部 JSON：窗报告入账、聚合报告跳过、其余一律拒绝。

    allow_missing 语义（显式选择，不静默改全部调用方）：
    - True（plan/execute 预检路径）：目录尚不存在 = 零历史——首个窗的
      合法起点；
    - False（默认，aggregate 路径）：目录不存在即拒绝——聚合绝不为
      不存在的目录编造空聚合（零样本的 p50/max 全 None 是误导性输出）。
    """
    if not evidence_dir.is_dir():
        if allow_missing:
            return PhaseHistory()
        raise Phase0Error(f"证据目录不存在（aggregate 拒绝空目录）: {evidence_dir.name!r}")
    windows: list[WindowReport] = []
    for path in sorted(evidence_dir.glob(WINDOW_GLOB)):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as cause:
            raise Phase0Error(f"{path.name}: 不可解析（{type(cause).__name__}）") from cause
        if isinstance(payload, dict) and payload.get("schema") == AGGREGATE_SCHEMA:
            continue  # 聚合报告是合法目录成员（只读产物），不入预算账
        windows.append(parse_window_report(payload, path.name))
    per_date: dict[str, int] = {}
    for window in windows:
        for sample in window.samples:
            per_date[sample.local_date] = per_date.get(sample.local_date, 0) + 1
    return PhaseHistory(
        windows=tuple(windows),
        total_manifest_requests=sum(len(w.samples) for w in windows),
        per_local_date=per_date,
    )


def check_budget(history: PhaseHistory, new_samples: int, today: str) -> None:
    """总 ≤72 且同本地日期 ≤24；越界抛 Phase0Error（零探针调用）。"""
    projected_total = history.total_manifest_requests + new_samples
    if projected_total > TOTAL_BUDGET:
        raise Phase0Error(
            f"总预算越界：既有 {history.total_manifest_requests} + 本次 {new_samples}"
            f" = {projected_total} > {TOTAL_BUDGET}"
        )
    projected_today = history.per_local_date.get(today, 0) + new_samples
    if projected_today > DAILY_BUDGET:
        raise Phase0Error(
            f"当日（{today}）预算越界：既有 {history.per_local_date.get(today, 0)}"
            f" + 本次 {new_samples} = {projected_today} > {DAILY_BUDGET}"
        )


# ---------------------------------------------------------------- 探针调用（注入点）


class ProbeInvoker(Protocol):
    """探针 CLI 子进程注入点：真实实现走 subprocess，测试注入假实现。"""

    def run(self, argv: list[str], *, timeout: float) -> tuple[int, str, str]:
        """执行一次探针 CLI：返回 (退出码, stdout, stderr)。"""


class RealProbeInvoker:
    """subprocess 直调探针 CLI（python tools/ops/public_edge_stability_probe.py）。"""

    def run(self, argv: list[str], *, timeout: float) -> tuple[int, str, str]:
        try:
            result = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout, check=False
            )
        except (subprocess.TimeoutExpired, OSError) as cause:
            raise Phase0Error(f"探针进程故障: {type(cause).__name__}") from cause
        return result.returncode, result.stdout, result.stderr


def build_probe_argv(
    *,
    probe_script: Path,
    url: str,
    samples: int,
    interval_s: float,
    timeout_s: float,
    proxy_display: str | None,
    output_path: Path,
) -> list[str]:
    """构造探针 CLI argv（纯函数）：恒 HTTP/1.1、恒无大资产/ssl-no-revoke。"""
    argv = [
        sys.executable,
        str(probe_script),
        "--url",
        url,
        "--samples",
        str(samples),
        "--interval",
        f"{interval_s:g}",
        "--timeout",
        f"{timeout_s:g}",
        "--http-version",
        _edge_probe.HTTP11,
        "--output",
        str(output_path),
    ]
    if proxy_display is not None:
        argv += ["--proxy", proxy_display]
    return argv


# ---------------------------------------------------------------- 描述性统计


def describe(values: list[float]) -> dict[str, float | int | None]:
    """单一口径描述统计（nearest-rank percentile，无 p99/成功率门）。"""
    ordered = sorted(values)
    return {
        "samples": len(ordered),
        "p50": _history.percentile(ordered, 0.50) if ordered else None,
        "p95": _history.percentile(ordered, 0.95) if ordered else None,
        "max": ordered[-1] if ordered else None,
    }


def group_summary(samples: list[SampleFacts]) -> dict[str, Any]:
    """一组样本的描述性汇总：计数/失败/慢窗/TTFB 与 elapsed 的 p50/p95/max。"""
    ttfb_values = [s.ttfb_ms for s in samples if s.ttfb_ms is not None]
    elapsed_values = [s.elapsed_ms for s in samples if s.elapsed_ms is not None]
    return {
        "samples": len(samples),
        "failures": sum(1 for s in samples if s.failure_category is not None),
        "slow_window_samples": sum(
            1 for s in samples if s.ttfb_ms is not None and s.ttfb_ms > SLOW_TTFB_MS
        ),
        "ttfb_ms_missing": len(samples) - len(ttfb_values),
        "ttfb_ms": describe(ttfb_values),
        "elapsed_ms": describe(elapsed_values),
    }


def aggregate(history: PhaseHistory) -> dict[str, Any]:
    """只读聚合：overall + 按模式 + 按本地日期；明确不设 p99/成功率门。"""
    all_samples = [s for w in history.windows for s in w.samples]
    by_mode: dict[str, list[SampleFacts]] = {MODE_DIRECT: [], MODE_PROXY: []}
    by_date: dict[str, list[SampleFacts]] = {}
    for sample in all_samples:
        by_mode[sample.probe_mode].append(sample)
        by_date.setdefault(sample.local_date, []).append(sample)
    return {
        "schema": AGGREGATE_SCHEMA,
        "tool": "public_edge_phase0_baseline",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "statistical_scope": "descriptive-only",
        "note": (
            "描述性统计（M14-181 Phase 0 口径）：慢窗 = TTFB > 2500ms；"
            "不设 p99 或 99.5% 成功率门——该样本量不支持尾部/低频事件门禁，"
            "稳态 SLO 门禁化前提见 M14-181 设计文档"
        ),
        "windows_parsed": len(history.windows),
        "total_manifest_requests": history.total_manifest_requests,
        "overall": group_summary(all_samples),
        "by_probe_mode": {mode: group_summary(rows) for mode, rows in by_mode.items() if rows},
        "by_local_date": {date: group_summary(rows) for date, rows in sorted(by_date.items())},
    }


# ---------------------------------------------------------------- 模式编排


@dataclass(frozen=True)
class WindowPlan:
    url: str
    samples: int
    interval_s: float
    timeout_s: float
    proxy_display: str | None
    evidence_dir: Path
    output_name: str

    @property
    def output_path(self) -> Path:
        return self.evidence_dir / self.output_name


def plan_window(
    *,
    url: str,
    samples: int,
    interval_s: float,
    timeout_s: float,
    proxy: str | None,
    evidence_dir: Path,
) -> WindowPlan:
    """参数与预算预检（零请求零写入）；通过返回窗口计划。"""
    asset = _edge_probe.parse_public_asset_url(url)  # 非法即抛（入口 fail-closed）
    proxy_target = _edge_probe.parse_proxy_url(proxy) if proxy else None
    mode = MODE_PROXY if proxy_target else MODE_DIRECT
    # 秒级 stamp 在快速连续规划时会同模式同名（预约门会拒）；微秒级
    # 精度让连续窗天然获得不同输出路径，O_EXCL 预约仍是最终裁决者。
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S-%f")
    plan = WindowPlan(
        url=asset.url,
        samples=samples,
        interval_s=interval_s,
        timeout_s=timeout_s,
        proxy_display=proxy_target.display if proxy_target else None,
        evidence_dir=evidence_dir,
        output_name=f"{WINDOW_PREFIX}{stamp}-{mode}.json",
    )
    check_budget(
        load_history(evidence_dir, allow_missing=True),
        samples,
        datetime.now(timezone.utc).astimezone().date().isoformat(),
    )
    return plan


def _reserve_output_path(output_path: Path) -> Path:
    """独占预约窗输出路径（O_CREAT|O_EXCL）；任何冲突都零请求拒绝。

    - 输出 JSON 已存在（陈旧/重复报告）→ 拒绝；
    - `<output>.reserve` 已存在（上次失败残留或路径复用）→ 拒绝——失败
      后**故意保留**预约，同一输出路径不可复用（防部分写入/同秒重跑
      掩盖失败）；成功后删除预约。
    """
    if output_path.exists():
        raise Phase0Error(f"输出路径已有报告（拒绝复用）: {output_path.name}")
    reserve = output_path.with_name(output_path.name + ".reserve")
    try:
        descriptor = os.open(reserve, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as cause:
        raise Phase0Error(
            f"输出路径已有失败残留预约（拒绝复用，人工核查后清理）: {reserve.name}"
        ) from cause
    except OSError as cause:
        raise Phase0Error(f"预约文件创建失败: {type(cause).__name__}") from cause
    os.close(descriptor)
    if output_path.exists():  # 预约后的竞态复查：极端窗口内报告出现即放弃
        os.unlink(reserve)
        raise Phase0Error(f"输出路径在预约后出现报告（竞态拒绝）: {output_path.name}")
    return reserve


def execute_window(plan: WindowPlan, invoker: ProbeInvoker, probe_script: Path) -> int:
    """执行恰一个窗（零重试）：预约输出 → 探针运行 → 报告校验入账。

    失败（探针工具失败或报告不可入账）保留 .reserve——同路径不可复用；
    成功删除 .reserve。任何预约冲突都零探针调用（Phase0Error 由调用方
    转 exit 2）。
    """
    plan.evidence_dir.mkdir(parents=True, exist_ok=True)
    reserve = _reserve_output_path(plan.output_path)
    argv = build_probe_argv(
        probe_script=probe_script,
        url=plan.url,
        samples=plan.samples,
        interval_s=plan.interval_s,
        timeout_s=plan.timeout_s,
        proxy_display=plan.proxy_display,
        output_path=plan.output_path,
    )
    code, stdout, _stderr = invoker.run(argv, timeout=PROBE_TIMEOUT_GRACE_S)
    if code >= 2:
        # 探针自身失败（参数/能力门/运行器）：报告大概率未写出——零重试、
        # 保留预约（同路径不可复用），明确告知操作者该窗未入账。
        print(
            f"[phase0] FAIL: 探针工具失败 exit={code}（该窗未入账，不重试；"
            f"预约保留: {reserve.name}）",
            file=sys.stderr,
        )
        print(stdout.strip(), file=sys.stderr)
        return 2
    try:
        payload = json.loads(plan.output_path.read_text(encoding="utf-8"))
        parse_window_report(payload, plan.output_path.name)
        history = load_history(plan.evidence_dir)  # 全目录复检并取入账后预算
    except (OSError, UnicodeError, json.JSONDecodeError, Phase0Error) as cause:
        print(
            f"[phase0] FAIL: 窗报告缺失或不可入账（{type(cause).__name__}: {cause}）"
            f"——该窗请求可能已发出但未记账；预约保留 {reserve.name}，"
            "人工核查后再继续",
            file=sys.stderr,
        )
        return 2
    os.unlink(reserve)  # 成功入账后释放预约
    print(
        f"[phase0] window done: {plan.output_path.name} exit={code}"
        f" | budget: total {history.total_manifest_requests}/{TOTAL_BUDGET}"
        f" | windows={len(history.windows)}"
    )
    return code


# ---------------------------------------------------------------- CLI


def _bounded_int(low: int, high: int, what: str) -> Any:
    def validate(raw: str) -> int:
        try:
            value = int(raw)
        except ValueError as cause:
            raise argparse.ArgumentTypeError(f"{what} 必须是整数") from cause
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"{what} 越界（{low}-{high}）")
        return value

    return validate


def _bounded_float(low: float, high: float, what: str) -> Any:
    def validate(raw: str) -> float:
        try:
            value = float(raw)
        except ValueError as cause:
            raise argparse.ArgumentTypeError(f"{what} 必须是数值") from cause
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"{what} 越界（{low:g}-{high:g}）")
        return value

    return validate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="public_edge_phase0_baseline",
        description=(
            "Phase 0 公网边缘基线采样编排器（M14-181 设计落地）：plan（默认，"
            "零请求）/ execute（需 --execute + 精确确认短语，一个有界窗）/ "
            "aggregate（只读描述性统计）。预算：总 ≤72、同本地日期 ≤24、"
            "每窗 ≤8 个 manifest 样本、恒 HTTP/1.1、无 APK、无 ssl-no-revoke。"
        ),
    )
    parser.add_argument("--url", help="manifest 公网 https URL（plan/execute 必填）")
    parser.add_argument(
        "--samples",
        type=_bounded_int(1, MAX_SAMPLES_PER_WINDOW, "--samples"),
        default=DEFAULT_SAMPLES,
        help=f"本窗样本数（默认 {DEFAULT_SAMPLES}，上限 {MAX_SAMPLES_PER_WINDOW}）",
    )
    parser.add_argument(
        "--interval",
        type=_bounded_float(MIN_INTERVAL_S, MAX_INTERVAL_S, "--interval"),
        default=MIN_INTERVAL_S,
        help=f"样本间隔秒（默认 {MIN_INTERVAL_S:g}，Phase 0 下限）",
    )
    parser.add_argument(
        "--timeout",
        type=_bounded_float(1.0, MAX_TIMEOUT_S, "--timeout"),
        default=MAX_TIMEOUT_S,
        help=f"单请求超时秒（默认 {MAX_TIMEOUT_S:g}，Phase 0 上限）",
    )
    parser.add_argument("--proxy", help="显式代理 scheme://host:port（缺省=直连）")
    parser.add_argument(
        "--evidence-dir", required=True, help="gitignored 证据目录（窗/聚合报告所在地）"
    )
    parser.add_argument("--execute", action="store_true", help="执行网络采样（默认仅 plan）")
    parser.add_argument("--confirm-phrase", help=f"执行确认短语（必须逐字符等于 {CONFIRM_PHRASE!r}）")
    parser.add_argument("--aggregate", action="store_true", help="只读聚合描述性统计（与其他模式互斥）")
    return parser


def main(argv: list[str] | None = None, invoker: ProbeInvoker | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    evidence_dir = Path(args.evidence_dir)
    invoker = invoker or RealProbeInvoker()

    if args.aggregate:
        if args.execute or args.url or args.proxy:
            parser.error("--aggregate 与 --execute/--url/--proxy 互斥")
        try:
            report = aggregate(load_history(evidence_dir))
        except Phase0Error as cause:
            print(f"[phase0] FAIL: {cause}", file=sys.stderr)
            return 2
        output_path = evidence_dir / (
            f"{AGGREGATE_PREFIX}{datetime.now(timezone.utc).astimezone().strftime('%Y%m%d-%H%M%S')}.json"
        )
        evidence_dir.mkdir(parents=True, exist_ok=True)
        try:
            _edge_probe._write_report_atomic(str(output_path), report)
        except OSError as cause:
            print(f"[phase0] FAIL: 聚合报告写出失败: {type(cause).__name__}", file=sys.stderr)
            return 2
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        print(f"[phase0] aggregate written: {output_path.name} -> exit 0")
        return 0

    if not args.url:
        parser.error("plan/execute 需要 --url")
    try:
        plan = plan_window(
            url=args.url,
            samples=args.samples,
            interval_s=args.interval,
            timeout_s=args.timeout,
            proxy=args.proxy,
            evidence_dir=evidence_dir,
        )
    except Phase0Error as cause:
        print(f"[phase0] FAIL: {cause}", file=sys.stderr)
        return 2

    if not args.execute:
        argv_preview = build_probe_argv(
            probe_script=Path("tools/ops/public_edge_stability_probe.py"),
            url=plan.url,
            samples=plan.samples,
            interval_s=plan.interval_s,
            timeout_s=plan.timeout_s,
            proxy_display=plan.proxy_display,
            output_path=plan.output_path,
        )
        print(f"[phase0] plan-only（零请求）：将执行 {plan.samples} 个 manifest 样本")
        print(f"[phase0] output: {plan.output_path.name}")
        print(f"[phase0] probe argv: {' '.join(argv_preview[1:])}")
        print("[phase0] 预算预检通过；执行需 --execute --confirm-phrase " f"{CONFIRM_PHRASE!r}")
        return 0

    if args.confirm_phrase != CONFIRM_PHRASE:
        print(
            f"[phase0] FAIL: 确认短语不符（零请求）；必须逐字符等于 {CONFIRM_PHRASE!r}",
            file=sys.stderr,
        )
        return 2
    try:
        return execute_window(
            plan, invoker, Path(__file__).resolve().parent / "public_edge_stability_probe.py"
        )
    except Phase0Error as cause:
        print(f"[phase0] FAIL: {cause}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
