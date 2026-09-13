#!/usr/bin/env python
"""M14-15 监控历史洞察/告警摘要第 1 切片：M14-13 history.jsonl（或
M14-12 monitor 工件目录）→ 安全 JSON+MD 洞察摘要。

设计（与 tools/ops/monitoring_history.py 同款纪律：单文件、纯标准库、
零第三方依赖；默认零网络/零子进程/零生产读取/零计划任务——一切 I/O 经
Store 注入，测试注入 FakeStore；本工具只读输入面、只写输出目录，
**绝不改动/删除任何输入工件**）：

- 双模式：默认 **plan（完全惰性）**——零读取/零写入/零 Store 构造，仅打印
  计划与门禁提示；**execute** 需同时满足「旗标 + 精确确认短语」
  （``--execute`` + ``--confirm "EXECUTE READ-ONLY MONITORING INSIGHTS"``
  一字不差），缺一/近似即 EXIT 2 且零读取。
- 输入面（三形态，固定画像）：``--source`` 可为 ① history.jsonl 文件本体；
  ② 含 history.jsonl 的目录（M14-13 历史输出目录形态；同目录混入
  monitor-*.json → mixed-inputs 拒绝）；③ M14-12 monitor 工件目录（仅
  monitor-*.json——发现/严格校验/去重/排序**委托同仓 M14-13
  monitoring_history.py 的已测函数**（单一 schema 事实源），其固定词汇
  拒绝原因原样透传）。默认输入 = 同仓 monitoring_history 的 canonical
  输出目录 ``.verify/artifacts/m14-13-monitoring-history/``（M14-21 起
  直接引用其 ``DEFAULT_OUTPUT_DIR`` 常量——单一事实源，持续管道
  monitor → history → insights 持续更新，本目录恒有写入者）。
- 严格校验（fail-closed，任何拒绝输出零写入）：history 行必须为 M14-13
  记录 schema（schema_version==1 / artifact_sha256 64 位十六进制 / stem
  白名单 / UTC 时间戳 / project 白名单 / overall_status∈{ok,warn,critical}
  / partial 恒 false / 阈值计数 / 六服务 health+restart / 五端点
  http_status+**有限非负**延迟 / 六日志 error_total）；行序必须严格递增
  (collected_at, source_stem)——**时间乱序/重复行拒绝**；跨 project 混档
  拒绝；样本数 1–5000（超界拒绝，绝不静默截断）。
- 洞察最小集：时间范围+样本数+时长、overall_status 计数、availability
  （计数+比率）、degraded/critical 事件列表（有界：``--event-limit``
  默认 50、1–500；超界截断计数显式，列表保最新 N 条）、逐端点延迟
  min/p50/p95/max（nearest-rank，与 M14-13 同款）+非 200 计数、逐服务
  restart/日志 error 总计+非 healthy 样本数、连续失败/恢复（当前连胜、
  最长 non-ok 连败区间、失败/恢复转移计数+有界时间戳列表、逐端点当前
  连续失败）、最近样本状态。
- 输出（默认 gitignored ``.verify/artifacts/m14-15-monitoring-insights/``，
  ``--output-dir`` 为操作者显式自选）：``insights.json`` +
  ``insights-summary.md``；同目录 tmp+fsync+os.replace 原子落盘；symlink
  组件一律拒绝；**仅在全部输入校验通过之后才写任何输出**。生成时间戳取
  自最新样本 collected_at——**零墙钟，两次运行逐字节相同**。
- 报告卫生：stdout/输出**绝无绝对本机路径**（路径显示恒为仓库相对或纯
  名）、原始日志行、密钥/secret、生产容器 ID；拒绝原因固定词汇不回显
  被拒值。
- 退出码：0 plan 成功 / execute 成功；2 任何拒绝（门禁、参数超界、源
  缺失/symlink、零样本、malformed、乱序、混档、超 5000、写失败）。

用法（仓库根）：
  python tools/ops/monitoring_insights.py                 # plan（默认，零读取）
  python tools/ops/monitoring_insights.py --execute \
      --confirm "EXECUTE READ-ONLY MONITORING INSIGHTS"   # execute（只读洞察）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from itertools import pairwise
from pathlib import Path
from typing import Protocol

try:  # 直接脚本运行：tools/ops 自身在 sys.path
    import monitoring_history as _history
except ImportError:  # 经 importlib 按路径加载（契约测试形态）
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import monitoring_history as _history

EXIT_OK = 0
EXIT_REFUSED = 2

REPO_ROOT = Path(__file__).resolve().parents[2]
#: M14-21：默认输入 = 同仓 monitoring_history 的 canonical 输出目录（单一
#: 事实源——持续管道 monitor → history → insights 持续产出，本目录恒有写入者）
DEFAULT_SOURCE = _history.DEFAULT_OUTPUT_DIR
DEFAULT_OUTPUT_DIR = REPO_ROOT / ".verify" / "artifacts" / "m14-15-monitoring-insights"
INSIGHTS_JSON_NAME = "insights.json"
INSIGHTS_MD_NAME = "insights-summary.md"
HISTORY_FILE_NAME = "history.jsonl"

TAG = "[insights]"
INSIGHTS_SCHEMA_VERSION = 1
TOOL_NAME = "tools/ops/monitoring_insights.py"
MILESTONE = "M14-15"
CONFIRM_PHRASE = "EXECUTE READ-ONLY MONITORING INSIGHTS"

#: 画像与 schema 常量：单一事实源 = 同仓 M14-13 工具
STACK_SERVICES: tuple[str, ...] = _history.STACK_SERVICES
ENDPOINT_IDS: tuple[str, ...] = _history.ENDPOINT_IDS
HISTORY_SCHEMA_VERSION = _history.HISTORY_SCHEMA_VERSION
ARTIFACT_STEM_RE = _history.ARTIFACT_STEM_RE
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: 样本量界（超界拒绝，绝不静默截断）；事件列表界（默认 50，1–500）
MIN_SAMPLES = 1
MAX_SAMPLES = 5000
DEFAULT_EVENT_LIMIT = 50
MIN_EVENT_LIMIT = 1
MAX_EVENT_LIMIT = 500


class InsightsError(RuntimeError):
    """fail-closed 拒绝（固定词汇原因；绝不携带文件内容文本）。"""


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_finite_number(value: object) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


# ---------------------------------------------------------------- Store（注入点）


class Store(Protocol):
    def exists(self, path: Path) -> bool: ...
    def is_dir(self, path: Path) -> bool: ...
    def is_symlink(self, path: Path) -> bool: ...
    def list_dir(self, directory: Path) -> list[str]: ...
    def read_bytes(self, path: Path) -> bytes: ...
    def mkdirs(self, directory: Path) -> None: ...
    def write_atomic(self, path: Path, text: str) -> None: ...


class RealStore:
    """真实文件面：只读输入 + 原子写输出（同目录 tmp + fsync + os.replace）。"""

    def exists(self, path: Path) -> bool:
        return path.exists()

    def is_dir(self, path: Path) -> bool:
        return path.is_dir()

    def is_symlink(self, path: Path) -> bool:
        return path.is_symlink()

    def list_dir(self, directory: Path) -> list[str]:
        return sorted(entry.name for entry in directory.iterdir())

    def read_bytes(self, path: Path) -> bytes:
        return path.read_bytes()

    def mkdirs(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)

    def write_atomic(self, path: Path, text: str) -> None:
        tmp_path = path.with_name(f".{path.name}.tmp")
        if tmp_path.is_symlink():
            raise InsightsError("symlink-output-tmp")
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.replace(tmp_path, path)
        except OSError:
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise


def reject_symlinked_path(store: Store, *paths: Path) -> None:
    """拒绝 symlink：目标自身 + 现存祖先组件（防静默越界重定向）。"""
    for path in paths:
        if store.is_symlink(path):
            raise InsightsError("symlink-target")
        for ancestor in path.parents:
            if store.exists(ancestor) and store.is_symlink(ancestor):
                raise InsightsError("symlink-in-path")


def display_path(path: Path) -> str:
    """stdout 路径显示：仓库相对（POSIX 分隔）；仓外仅纯名——绝无绝对路径。"""
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.name


# ---------------------------------------------------------------- 行校验（纯）


def validate_history_row(data: object) -> dict[str, object]:
    """纯函数：严格校验单条 M14-13 history 记录 → 规范化字段字典；违规抛
    InsightsError（固定词汇原因）。partial/incomplete/非有限延迟一律拒绝。"""
    row = data if isinstance(data, dict) else None
    if row is None:
        raise InsightsError("history-row")
    if row.get("schema_version") != HISTORY_SCHEMA_VERSION:
        raise InsightsError("schema-version")
    sha = row.get("artifact_sha256")
    if not isinstance(sha, str) or SHA256_RE.match(sha) is None:
        raise InsightsError("artifact-sha256")
    stem = row.get("source_stem")
    if not isinstance(stem, str) or ARTIFACT_STEM_RE.match(stem) is None:
        raise InsightsError("source-stem")
    collected_at = row.get("collected_at")
    if not isinstance(collected_at, str):
        raise InsightsError("collected-at")
    try:
        collected_dt = _history.parse_timestamp(collected_at)
    except _history.HistoryError:
        raise InsightsError("collected-at") from None
    project = row.get("project")
    if not isinstance(project, str) or _history.PROJECT_NAME_RE.match(project) is None:
        raise InsightsError("project")
    overall = row.get("overall_status")
    if overall not in _history.ACCEPTED_OVERALL_STATUSES:
        raise InsightsError("overall-status")
    if row.get("partial") is not False:
        raise InsightsError("partial")
    counts_raw = row.get("threshold_counts")
    if not isinstance(counts_raw, dict):
        raise InsightsError("threshold-counts")
    counts: dict[str, int] = {}
    for key in ("ok", "warn", "critical"):
        value = counts_raw.get(key)
        if not _is_int(value) or value < 0:
            raise InsightsError("threshold-counts")
        counts[key] = value
    healths_raw = row.get("compose_service_health")
    if not isinstance(healths_raw, dict):
        raise InsightsError("service-health")
    healths: dict[str, str] = {}
    for service in STACK_SERVICES:
        health = healths_raw.get(service)
        if not isinstance(health, str):
            raise InsightsError("service-health")
        healths[service] = health
    restarts_raw = row.get("restart_counts")
    if not isinstance(restarts_raw, dict):
        raise InsightsError("restart-counts")
    restarts: dict[str, int] = {}
    for service in STACK_SERVICES:
        value = restarts_raw.get(service)
        if not _is_int(value) or value < 0:
            raise InsightsError("restart-counts")
        restarts[service] = value
    endpoints_raw = row.get("endpoints")
    if not isinstance(endpoints_raw, dict):
        raise InsightsError("endpoint-facts")
    endpoints: dict[str, dict[str, object]] = {}
    for endpoint_id in ENDPOINT_IDS:
        item = endpoints_raw.get(endpoint_id)
        if not isinstance(item, dict):
            raise InsightsError("endpoint-facts")
        status = item.get("http_status")
        latency = item.get("latency_ms")
        if not _is_int(status) or not _is_finite_number(latency) or latency < 0:
            raise InsightsError("endpoint-facts")
        endpoints[endpoint_id] = {"http_status": status, "latency_ms": latency}
    logs_raw = row.get("log_error_totals")
    if not isinstance(logs_raw, dict):
        raise InsightsError("log-error-totals")
    log_errors: dict[str, int] = {}
    for service in STACK_SERVICES:
        value = logs_raw.get(service)
        if not _is_int(value) or value < 0:
            raise InsightsError("log-error-totals")
        log_errors[service] = value
    return {
        "stem": stem, "collected_at": collected_at, "collected_dt": collected_dt,
        "project": project, "overall_status": overall, "counts": counts,
        "healths": healths, "restarts": restarts, "endpoints": endpoints,
        "log_errors": log_errors,
    }


def _check_sequence(rows: list[dict[str, object]]) -> None:
    """行序（严格递增 (collected_dt, source_stem)）、单一 project、样本量界。"""
    previous: tuple[object, str] | None = None
    for row in rows:
        key = (row["collected_dt"], row["stem"])  # type: ignore[operator]
        if previous is not None and key <= previous:
            raise InsightsError("history-order")
        previous = key
        if row["project"] != rows[0]["project"]:
            raise InsightsError("mixed-project")
    if not MIN_SAMPLES <= len(rows) <= MAX_SAMPLES:
        raise InsightsError("sample-count")


def parse_history_text(text: str) -> list[dict[str, object]]:
    """history.jsonl 文本 → 已验证行（升序、单一 project、量界内）。"""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines:
        raise InsightsError("no-samples")
    rows: list[dict[str, object]] = []
    for line in lines:
        if line.strip() == "":
            raise InsightsError("history-row")
        try:
            data = json.loads(line)
        except ValueError:
            raise InsightsError("not-json") from None
        rows.append(validate_history_row(data))
    _check_sequence(rows)
    return rows


# ---------------------------------------------------------------- 输入解析（三形态）


def _load_history_bytes(store: Store, path: Path) -> tuple[list[dict[str, object]], str]:
    """读单文件面：symlink/目录形态拒绝 → 字节读取（SHA-256 同源）→ 解析校验。"""
    reject_symlinked_path(store, path)
    if store.is_dir(path):
        raise InsightsError("source-kind")
    raw = store.read_bytes(path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise InsightsError("not-json") from None
    return parse_history_text(text), hashlib.sha256(raw).hexdigest()


def _records_from_monitor_dir(store: Store, source: Path) -> tuple[list[dict[str, object]], int]:
    """monitor 工件目录 → M14-13 已测管道（发现/校验/去重/排序）→ 记录。"""
    try:
        samples = _history.build_samples(store, source)
        unique, duplicates = _history.dedupe_and_sort(samples)
    except _history.HistoryError as cause:
        raise InsightsError(str(cause)) from None
    rows = [validate_history_row(_history.build_record(sample)) for sample in unique]
    _check_sequence(rows)
    return rows, duplicates


def load_rows(store: Store, source: Path) -> dict[str, object]:
    """三形态输入解析 → {rows, source_kind, source_sha256, duplicate_count}。"""
    if not store.exists(source):
        raise InsightsError("source-missing")
    if store.is_symlink(source):
        raise InsightsError("symlink-source")
    if store.is_dir(source):
        reject_symlinked_path(store, source)
        names = store.list_dir(source)
        has_history = HISTORY_FILE_NAME in names
        has_monitor = any(n.startswith("monitor-") and n.endswith(".json") for n in names)
        if has_history and has_monitor:
            raise InsightsError("mixed-inputs")
        if has_history:
            rows, digest = _load_history_bytes(store, source / HISTORY_FILE_NAME)
            return {"rows": rows, "source_kind": "history-dir",
                    "source_sha256": digest, "duplicate_count": 0}
        if has_monitor:
            rows, duplicates = _records_from_monitor_dir(store, source)
            return {"rows": rows, "source_kind": "monitor-artifacts",
                    "source_sha256": None, "duplicate_count": duplicates}
        raise InsightsError("no-sources")
    if source.name != HISTORY_FILE_NAME:
        raise InsightsError("source-kind")
    rows, digest = _load_history_bytes(store, source)
    return {"rows": rows, "source_kind": "history-file",
            "source_sha256": digest, "duplicate_count": 0}


# ---------------------------------------------------------------- 洞察计算（纯）


def _percentile(sorted_values: list[float], fraction: float) -> float:
    return _history.percentile(sorted_values, fraction)


def _non_ok(row: dict[str, object]) -> bool:
    return row["overall_status"] != "ok"


def _compute_streaks(rows: list[dict[str, object]], event_limit: int) -> dict[str, object]:
    latest = rows[-1]
    current_length = 1
    for row in reversed(rows[:-1]):
        if row["overall_status"] != latest["overall_status"]:
            break
        current_length += 1
    non_ok_length = 0
    if _non_ok(latest):
        non_ok_length = 1
        for row in reversed(rows[:-1]):
            if not _non_ok(row):
                break
            non_ok_length += 1
    best_length, best_first, best_last = 0, None, None
    run_length, run_first = 0, None
    for row in rows:
        if _non_ok(row):
            run_length += 1
            if run_first is None:
                run_first = row["collected_at"]
            if run_length > best_length:
                best_length, best_first, best_last = run_length, run_first, row["collected_at"]
        else:
            run_length, run_first = 0, None
    failures: list[dict[str, str]] = []
    recoveries: list[dict[str, str]] = []
    for previous, current in pairwise(rows):
        if not _non_ok(previous) and _non_ok(current):
            failures.append({"at": current["collected_at"],
                             "to_status": current["overall_status"]})
        elif _non_ok(previous) and not _non_ok(current):
            recoveries.append({"at": current["collected_at"],
                               "from_status": previous["overall_status"]})
    endpoint_streaks: dict[str, int] = {}
    for endpoint_id in ENDPOINT_IDS:
        streak = 0
        for row in reversed(rows):
            if row["endpoints"][endpoint_id]["http_status"] != 200:  # type: ignore[index]
                streak += 1
            else:
                break
        endpoint_streaks[endpoint_id] = streak
    return {
        "current": {"status": latest["overall_status"], "length": current_length},
        "current_non_ok_length": non_ok_length,
        "longest_non_ok": {"length": best_length,
                           "first_at": best_first, "last_at": best_last},
        "failure_transition_count": len(failures),
        "recovery_transition_count": len(recoveries),
        "failure_transitions": failures[-event_limit:],
        "recovery_transitions": recoveries[-event_limit:],
        "endpoint_current_failure_streaks": endpoint_streaks,
    }


def build_insights(rows: list[dict[str, object]], *, source_kind: str,
                   source_sha256: str | None, duplicate_count: int,
                   event_limit: int) -> dict[str, object]:
    """确定性洞察（生成时间戳取自最新样本 collected_at——零墙钟）。"""
    oldest, newest = rows[0], rows[-1]
    status_counts = {"ok": 0, "warn": 0, "critical": 0}
    for row in rows:
        status_counts[row["overall_status"]] += 1  # type: ignore[index]
    total = len(rows)
    events = [row for row in rows if _non_ok(row)]
    event_rows = [{
        "collected_at": row["collected_at"],
        "overall_status": row["overall_status"],
        "unhealthy_services": [s for s in STACK_SERVICES
                               if row["healths"][s] != "healthy"],  # type: ignore[index]
        "failing_endpoints": [e for e in ENDPOINT_IDS
                              if row["endpoints"][e]["http_status"] != 200],  # type: ignore[index]
    } for row in events]
    endpoint_stats: dict[str, dict[str, object]] = {}
    for endpoint_id in ENDPOINT_IDS:
        values = sorted(float(row["endpoints"][endpoint_id]["latency_ms"])  # type: ignore[index]
                        for row in rows)
        endpoint_stats[endpoint_id] = {
            "samples": len(values),
            "non_ok_count": sum(1 for row in rows
                                if row["endpoints"][endpoint_id]["http_status"] != 200),  # type: ignore[index]
            "min": values[0], "p50": _percentile(values, 0.50),
            "p95": _percentile(values, 0.95), "max": values[-1],
        }
    service_summary = {service: {
        "restart_total": sum(row["restarts"][service] for row in rows),  # type: ignore[index]
        "log_error_total": sum(row["log_errors"][service] for row in rows),  # type: ignore[index]
        "unhealthy_samples": sum(1 for row in rows
                                 if row["healths"][service] != "healthy"),  # type: ignore[index]
    } for service in STACK_SERVICES}
    duration = int((newest["collected_dt"] - oldest["collected_dt"]).total_seconds())  # type: ignore[operator]
    return {
        "schema_version": INSIGHTS_SCHEMA_VERSION,
        "tool": TOOL_NAME, "milestone": MILESTONE, "mode": "execute",
        "source_kind": source_kind, "source_sha256": source_sha256,
        "duplicate_count": duplicate_count,
        "generated_from_newest_at": newest["collected_at"],
        "project": rows[0]["project"],
        "sample_count": total,
        "first_collected_at": oldest["collected_at"],
        "last_collected_at": newest["collected_at"],
        "duration_seconds": duration,
        "status_counts": status_counts,
        "availability_count": status_counts["ok"],
        "availability_ratio": round(status_counts["ok"] / total, 6),
        "degraded_count": status_counts["warn"],
        "critical_count": status_counts["critical"],
        "degraded_critical_events": {
            "events_total": len(event_rows),
            "events_listed": min(len(event_rows), event_limit),
            "events_truncated": max(0, len(event_rows) - event_limit),
            "events": event_rows[-event_limit:],
        },
        "endpoint_latency_ms": endpoint_stats,
        "service_summary": service_summary,
        "streaks": _compute_streaks(rows, event_limit),
        "latest_sample": {
            "collected_at": newest["collected_at"],
            "overall_status": newest["overall_status"],
            "threshold_counts": dict(newest["counts"]),  # type: ignore[arg-type]
            "service_health": dict(newest["healths"]),  # type: ignore[arg-type]
            "endpoint_http_status": {e: newest["endpoints"][e]["http_status"]  # type: ignore[index]
                                     for e in ENDPOINT_IDS},
        },
    }


# ---------------------------------------------------------------- Markdown 渲染


def _fmt_ms(value: object) -> str:
    return f"{value:.3f}" if isinstance(value, (int, float)) else "-"


def render_summary_markdown(insights: dict[str, object]) -> str:
    """Markdown 摘要（固定词汇表；绝无路径/原始日志/密钥/容器 ID）。"""
    status = insights["status_counts"]
    assert isinstance(status, dict)
    events = insights["degraded_critical_events"]
    assert isinstance(events, dict)
    latency = insights["endpoint_latency_ms"]
    assert isinstance(latency, dict)
    services = insights["service_summary"]
    assert isinstance(services, dict)
    streaks = insights["streaks"]
    assert isinstance(streaks, dict)
    latest = insights["latest_sample"]
    assert isinstance(latest, dict)
    sha = insights["source_sha256"]
    source_note = (f"{insights['source_kind']}（history.jsonl SHA-256 {sha[:12]}…）"
                   if isinstance(sha, str)
                   else f"{insights['source_kind']}（M14-13 管道内联转换，重复内容 {insights['duplicate_count']} 条）")
    lines: list[str] = [
        f"# M14-15 监控历史洞察摘要（schema_version={insights['schema_version']}）",
        "",
        f"- 生成时间戳（取自最新样本 collected_at）：{insights['generated_from_newest_at']}",
        f"- 源：{source_note}（只读，输入工件零改动/零删除）",
        (f"- 项目 {insights['project']}：样本 {insights['sample_count']} 条"
         f"（{insights['first_collected_at']} ~ {insights['last_collected_at']}，"
         f"时长 {insights['duration_seconds']}s）"),
        (f"- 状态：ok={status['ok']} warn={status['warn']} critical={status['critical']}"
         f"（availability={insights['availability_count']}/{insights['sample_count']}"
         f" = {insights['availability_ratio']:.6f}，degraded={insights['degraded_count']}，"
         f"critical={insights['critical_count']}）"),
        "",
        (f"## degraded/critical 事件（共 {events['events_total']} 条，列出"
         f" {events['events_listed']} 条，截断 {events['events_truncated']} 条；时间升序）"),
        "",
    ]
    event_rows = events["events"]
    assert isinstance(event_rows, list)
    if event_rows:
        lines += ["| 时间 | 状态 | 非 healthy 服务 | 非 200 端点 |", "|---|---|---|---|"]
        for event in event_rows:
            assert isinstance(event, dict)
            unhealthy = ", ".join(event["unhealthy_services"]) or "-"  # type: ignore[arg-type]
            failing = ", ".join(event["failing_endpoints"]) or "-"  # type: ignore[arg-type]
            lines.append(f"| {event['collected_at']} | {event['overall_status']} "
                         f"| {unhealthy} | {failing} |")
    else:
        lines.append("- 无 degraded/critical 事件")
    lines += ["", "## 端点延迟（ms，nearest-rank）", "",
              "| 端点 | 样本 | 非200 | min | p50 | p95 | max |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for endpoint_id in ENDPOINT_IDS:
        stats = latency[endpoint_id]
        assert isinstance(stats, dict)
        lines.append(f"| {endpoint_id} | {stats['samples']} | {stats['non_ok_count']} "
                     f"| {_fmt_ms(stats['min'])} | {_fmt_ms(stats['p50'])} "
                     f"| {_fmt_ms(stats['p95'])} | {_fmt_ms(stats['max'])} |")
    lines += ["", "## 服务汇总", "", "| 服务 | restart 总计 | 日志 error 总计 | 非 healthy 样本 |",
              "|---|---:|---:|---:|"]
    for service in STACK_SERVICES:
        stats = services[service]
        assert isinstance(stats, dict)
        lines.append(f"| {service} | {stats['restart_total']} | {stats['log_error_total']} "
                     f"| {stats['unhealthy_samples']} |")
    current = streaks["current"]
    assert isinstance(current, dict)
    longest = streaks["longest_non_ok"]
    assert isinstance(longest, dict)
    failure_list = "、".join(f"{item['at']}→{item['to_status']}"  # type: ignore[index]
                             for item in streaks["failure_transitions"]) or "-"
    recovery_list = "、".join(f"{item['at']}←{item['from_status']}"  # type: ignore[index]
                              for item in streaks["recovery_transitions"]) or "-"
    endpoint_streak_map = streaks["endpoint_current_failure_streaks"]
    assert isinstance(endpoint_streak_map, dict)
    endpoint_failures_note = "、".join(f"{e} × {endpoint_streak_map[e]}"
                                       for e in ENDPOINT_IDS
                                       if endpoint_streak_map[e] > 0) or "无"
    lines += [
        "",
        "## 连续失败/恢复",
        "",
        (f"- 当前连胜：{current['status']} × {current['length']}"
         f"（当前连续 non-ok：{streaks['current_non_ok_length']}）"),
        (f"- 最长 non-ok 连败：{longest['length']} 条"
         + (f"（{longest['first_at']} ~ {longest['last_at']}）" if longest["length"] else "")),
        (f"- 失败转移 {streaks['failure_transition_count']} 次（{failure_list}）；"
         f"恢复转移 {streaks['recovery_transition_count']} 次（{recovery_list}）"),
        f"- 端点当前连续失败：{endpoint_failures_note}",
    ]
    latest_health = latest["service_health"]
    assert isinstance(latest_health, dict)
    latest_status = latest["endpoint_http_status"]
    assert isinstance(latest_status, dict)
    unhealthy_now = [s for s, health in latest_health.items() if health != "healthy"]
    bad_endpoints_now = [e for e, code in latest_status.items() if code != 200]
    counts = latest["threshold_counts"]
    assert isinstance(counts, dict)
    lines += [
        "",
        "## 最近样本",
        "",
        (f"- {latest['collected_at']} overall={latest['overall_status']}"
         f"（阈值计数 ok={counts['ok']} warn={counts['warn']} critical={counts['critical']}）"),
        f"- 服务 health：{('、'.join(unhealthy_now)) if unhealthy_now else '6 服务全 healthy'}",
        f"- 端点：{('、'.join(bad_endpoints_now)) if bad_endpoints_now else '5 端点全 200'}",
        "",
        "边界：",
        "- 仅本地只读工件洞察：不接外部告警系统，不构成 production readiness 宣称",
        "- 输入零改动/零删除；输出落 gitignored 目录，绝不入库",
        "- 零子进程/零网络/零容器面/零计划任务/零墙钟（生成时间戳取自最新样本）",
        "- 绝无原始日志行/密钥/secret/生产容器 ID/绝对本机路径",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 写出（原子）


def write_outputs(store: Store, output_dir: Path,
                  insights: dict[str, object]) -> tuple[Path, Path]:
    """全部输入校验通过后才可到达此处；两输出同目录原子落盘。

    先于 mkdir 拒绝 output_dir 自身/现存祖先 symlink（mkdir(parents=True)
    会穿越 symlink 建目录）；mkdir 后再复查（TOCTOU 窗口防御）。"""
    reject_symlinked_path(store, output_dir)
    store.mkdirs(output_dir)
    json_path = output_dir / INSIGHTS_JSON_NAME
    md_path = output_dir / INSIGHTS_MD_NAME
    reject_symlinked_path(store, json_path, md_path, output_dir)
    json_text = json.dumps(insights, ensure_ascii=False, indent=2) + "\n"
    md_text = render_summary_markdown(insights)
    store.write_atomic(json_path, json_text)
    store.write_atomic(md_path, md_text)
    return json_path, md_path


def run_insights(*, store: Store, source: Path, output_dir: Path,
                 event_limit: int) -> dict[str, object]:
    """主管道：解析输入→校验→洞察→写出（拒绝时零输出）。"""
    loaded = load_rows(store, source)
    insights = build_insights(loaded["rows"],  # type: ignore[arg-type]
                              source_kind=loaded["source_kind"],  # type: ignore[arg-type]
                              source_sha256=loaded["source_sha256"],  # type: ignore[arg-type]
                              duplicate_count=loaded["duplicate_count"],  # type: ignore[arg-type]
                              event_limit=event_limit)
    write_outputs(store, output_dir, insights)
    return insights


# ---------------------------------------------------------------- CLI


def validate_event_limit(value: int) -> str | None:
    if not MIN_EVENT_LIMIT <= value <= MAX_EVENT_LIMIT:
        return f"event-limit 必须在 {MIN_EVENT_LIMIT}-{MAX_EVENT_LIMIT}（收到 {value}）"
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monitoring_insights.py",
        description="M14-15 监控历史洞察：history.jsonl / monitor 工件 → 安全 JSON+MD 摘要"
                    "（默认 plan 零读取；execute 需旗标+精确确认短语；零子进程/零网络/零墙钟）",
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                        help=f"输入：history.jsonl 文件 / 含它的目录 / M14-12 monitor 工件目录"
                             f"（默认 {DEFAULT_SOURCE}，gitignored；只读）")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                        help=f"输出目录（默认 {DEFAULT_OUTPUT_DIR}，gitignored；"
                             f"自定义路径为操作者显式自选）")
    parser.add_argument("--event-limit", type=int, default=DEFAULT_EVENT_LIMIT,
                        help=f"degraded/critical 事件与转移列表上限 "
                             f"{MIN_EVENT_LIMIT}-{MAX_EVENT_LIMIT}（默认 {DEFAULT_EVENT_LIMIT}，"
                             f"超界截断计数显式）")
    parser.add_argument("--execute", action="store_true",
                        help="真实只读洞察（默认 plan：零读取/零写入/零 Store 构造）")
    parser.add_argument("--confirm", default="",
                        help="execute 确认短语（须与公开常量一字不差）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    problem = validate_event_limit(args.event_limit)
    if problem is not None:
        print(f"{TAG} 拒绝: {problem}", flush=True)
        return EXIT_REFUSED
    if not args.execute:
        print(f"{TAG} plan: 源（只读，绝不改动/删除输入）: {display_path(args.source)}", flush=True)
        print(f"{TAG} plan: 输出: {INSIGHTS_JSON_NAME} / {INSIGHTS_MD_NAME} -> "
              f"{display_path(args.output_dir)}", flush=True)
        print(f"{TAG} plan: 洞察: 时间范围/样本数、状态计数、availability、事件列表(默认 "
              f"{DEFAULT_EVENT_LIMIT})、端点延迟 min/p50/p95/max、服务 restart/日志 error、"
              f"连续失败/恢复、最近样本", flush=True)
        print(f"{TAG} plan: 零读取/零写入——execute 需: --execute --confirm \"{CONFIRM_PHRASE}\"",
              flush=True)
        return EXIT_OK
    if args.confirm != CONFIRM_PHRASE:
        print(f"{TAG} 拒绝: --execute 必配 --confirm \"{CONFIRM_PHRASE}\""
              f"（精确匹配，当前不匹配）——零读取", flush=True)
        return EXIT_REFUSED
    print(f"{TAG} 源: {display_path(args.source)}（只读，绝不改动/删除输入）", flush=True)
    try:
        insights = run_insights(store=RealStore(), source=args.source,
                                output_dir=args.output_dir,
                                event_limit=args.event_limit)
    except InsightsError as cause:
        print(f"{TAG} 拒绝: {cause}（fail-closed，输出零写入）", flush=True)
        return EXIT_REFUSED
    except OSError as cause:
        print(f"{TAG} 写失败: {type(cause).__name__}（输出保持原子，无 tmp 残留）", flush=True)
        return EXIT_REFUSED
    status = insights["status_counts"]
    assert isinstance(status, dict)
    streaks = insights["streaks"]
    assert isinstance(streaks, dict)
    current = streaks["current"]
    assert isinstance(current, dict)
    events = insights["degraded_critical_events"]
    assert isinstance(events, dict)
    print(f"{TAG} 样本: {insights['sample_count']}（{insights['first_collected_at']} ~ "
          f"{insights['last_collected_at']}，时长 {insights['duration_seconds']}s）", flush=True)
    print(f"{TAG} 状态: ok={status['ok']} warn={status['warn']} critical={status['critical']}"
          f"（availability={insights['availability_count']}/{insights['sample_count']}）", flush=True)
    print(f"{TAG} 事件: degraded/critical 共 {events['events_total']} 条（截断 "
          f"{events['events_truncated']}）；当前连胜 {current['status']} × {current['length']}"
          f"（最长连败 {streaks['longest_non_ok']['length']}）", flush=True)
    print(f"{TAG} 输出: {INSIGHTS_JSON_NAME} / {INSIGHTS_MD_NAME} -> "
          f"{display_path(args.output_dir)}", flush=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
