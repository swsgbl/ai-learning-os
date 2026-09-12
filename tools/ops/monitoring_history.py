#!/usr/bin/env python
"""M14-13 监控历史索引：M14-12 monitor JSON 工件 → 有界留存 history + 趋势摘要。

设计（与 tools/ops/production_monitor.py 同款纪律：单文件、纯标准库、
零第三方依赖；一切 I/O 经 Store 注入，测试注入 FakeStore——零真实
容器面/零网络/零计划任务/零 env 读取；本工具只读源目录、只写输出目录，
**绝不改动/删除任何 M14-12 原始工件**）：

- 输入面（固定画像）：默认 gitignored
  ``.verify/artifacts/m14-12-production-monitoring/``（可用 ``--source-dir``
  覆盖）。仅发现 ``monitor-*.json``；文件名 stem 严格白名单
  ``monitor-YYYYMMDD-HHMMSS``——glob 命中但 stem 不合规一律 fail-closed
  （被拒名不回显）。schema 严格校验（version/tool/milestone/mode=execute/
  project/双 UTC 时间戳与顺序/overall_status∈{ok,warn,critical}/
  partial 恒 false/阈值计数/六 compose 服务/六容器事实/五端点状态+延迟
  （有限数值）/六日志 error_total）；**malformed / partial / incomplete
  一律 fail-closed**（输出零写入）。
- 去重与排序：逐文件 SHA-256；同哈希 = 同内容 → 去重（保留排序后首个，
  计 duplicate_count）；同 (project, collected_at) 不同哈希 = 冲突 →
  fail-closed。唯一样本按（collected_at, 文件名 stem）确定性排序。
- 留存：默认 500、硬顶 5000、下限 1（CLI 超界 fail-closed）；保留最新
  N 条，显式 ``omitted_older_count`` 与 oldest/newest retained 边界；
  源文件永不改动/删除。
- 输出（默认 gitignored ``.verify/artifacts/m14-13-monitoring-history/``，
  ``--output-dir`` 为操作者显式自选）：``history.jsonl``（每行一条紧凑
  记录：artifact_sha256/source_stem/collected_at/project/overall_status/
  partial/threshold_counts/compose 服务 health/restart 计数/端点状态+
  延迟/日志 error 总计——**绝无原始日志行/密钥/secret**）与
  ``history-summary.md``（schema 版本化摘要：记录/状态计数、first/last
  时间戳、availability(ok)/degraded(warn)/critical 计数、逐端点延迟
  min/p50/p95/max（nearest-rank）、逐服务 restart/日志错误总计、
  duplicate/omitted 计数、留存边界）。**生成时间戳取自最新源样本的
  collected_at——全程零墙钟**（输出逐字节可复现）。两文件同目录 tmp +
  fsync + os.replace 原子落盘；symlink 组件/越界路径一律拒绝；仅在
  **全部输入校验通过之后**才写任何输出。
- 退出码：0 成功；2 任何拒绝（参数超界、源目录缺失/symlink、零源、
  malformed、partial/incomplete、冲突重复、写失败）。

用法（仓库根）：
  python tools/ops/monitoring_history.py                 # 默认源/输出目录
  python tools/ops/monitoring_history.py --retention 200
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

EXIT_OK = 0
EXIT_REFUSED = 2

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_DIR = REPO_ROOT / ".verify" / "artifacts" / "m14-12-production-monitoring"
DEFAULT_OUTPUT_DIR = REPO_ROOT / ".verify" / "artifacts" / "m14-13-monitoring-history"
HISTORY_OUTPUT_NAME = "history.jsonl"
SUMMARY_OUTPUT_NAME = "history-summary.md"

TAG = "[history]"
HISTORY_SCHEMA_VERSION = 1
TOOL_NAME = "tools/ops/monitoring_history.py"

#: 源工件契约（与 production_monitor.py 的报告 schema 对齐）
MONITOR_TOOL_NAME = "tools/ops/production_monitor.py"
EXPECTED_MONITOR_SCHEMA_VERSION = 1
EXPECTED_MILESTONE = "M14-12"
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

#: 六受管服务与五端点（与 production_monitor 同源画像）
STACK_SERVICES: tuple[str, ...] = ("postgres", "redis", "minio", "api", "web", "livekit")
ENDPOINT_IDS: tuple[str, ...] = (
    "web-root", "web-login", "api-health", "funasr-health", "cosyvoice-health",
)

#: 文件名 stem 严格白名单（被拒名不回显）
ARTIFACT_STEM_RE = re.compile(r"^monitor-[0-9]{8}-[0-9]{6}$")
PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

#: 历史接受的完整采集状态（incomplete 恒拒绝）
ACCEPTED_OVERALL_STATUSES = frozenset({"ok", "warn", "critical"})

#: 留存：默认 500，硬顶 5000，下限 1
DEFAULT_RETENTION = 500
MIN_RETENTION = 1
MAX_RETENTION = 5000


class HistoryError(RuntimeError):
    """fail-closed 拒绝（固定词汇原因；绝不携带文件内容文本）。"""


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_finite_number(value: object) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


# ---------------------------------------------------------------- Store（注入点）


class Store(Protocol):
    def exists(self, path: Path) -> bool: ...
    def is_symlink(self, path: Path) -> bool: ...
    def list_dir(self, directory: Path) -> list[str]: ...
    def read_bytes(self, path: Path) -> bytes: ...
    def mkdirs(self, directory: Path) -> None: ...
    def write_atomic(self, path: Path, text: str) -> None: ...


class RealStore:
    """真实文件面：只读源目录 + 原子写输出（同目录 tmp + fsync + os.replace）。"""

    def exists(self, path: Path) -> bool:
        return path.exists()

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
            raise HistoryError("symlink-output-tmp")
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
            raise HistoryError("symlink-target")
        for ancestor in path.parents:
            if store.exists(ancestor) and store.is_symlink(ancestor):
                raise HistoryError("symlink-in-path")


# ---------------------------------------------------------------- 校验（纯）


@dataclass(frozen=True)
class Sample:
    """已验证的源样本（紧凑化所需的全部字段；原文日志/密钥永不进入）。"""

    stem: str
    sha256: str
    collected_at: str
    collected_dt: datetime
    project: str
    overall_status: str
    counts: dict[str, int]
    compose_healths: dict[str, str]
    restart_counts: dict[str, int]
    endpoints: dict[str, dict[str, object]]
    log_error_totals: dict[str, int]


def parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise HistoryError("timestamp-format")
    try:
        return datetime.strptime(value, TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        raise HistoryError("timestamp-format") from None


def _require_mapping(value: object, reason: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise HistoryError(reason)
    return value


def validate_report(data: object) -> dict[str, object]:
    """纯函数：严格校验 monitor 报告 → 紧凑字段字典；违规抛 HistoryError
    （固定词汇原因）。partial/incomplete/非有限延迟一律拒绝。"""
    report = _require_mapping(data, "not-an-object")
    if report.get("schema_version") != EXPECTED_MONITOR_SCHEMA_VERSION:
        raise HistoryError("schema-version")
    if report.get("tool") != MONITOR_TOOL_NAME:
        raise HistoryError("tool")
    if report.get("milestone") != EXPECTED_MILESTONE:
        raise HistoryError("milestone")
    if report.get("mode") != "execute":
        raise HistoryError("mode")
    started = parse_timestamp(report.get("started_at_utc"))
    ended = parse_timestamp(report.get("ended_at_utc"))
    if started > ended:
        raise HistoryError("timestamp-order")
    config = _require_mapping(report.get("config"), "config")
    project = config.get("project")
    if not isinstance(project, str) or PROJECT_NAME_RE.match(project) is None:
        raise HistoryError("project")
    overall = report.get("overall_status")
    if overall not in ACCEPTED_OVERALL_STATUSES:
        raise HistoryError("overall-status")
    if report.get("partial") is not False:
        raise HistoryError("partial")
    threshold = _require_mapping(report.get("threshold_results"), "threshold-results")
    counts = _require_mapping(threshold.get("counts"), "threshold-counts")
    for key in ("ok", "warn", "critical"):
        if not _is_int(counts.get(key)) or counts[key] < 0:  # type: ignore[operator]
            raise HistoryError("threshold-counts")
    collectors = _require_mapping(report.get("collectors"), "collectors")
    compose_ps = _require_mapping(collectors.get("compose_ps"), "compose-ps")
    if compose_ps.get("status") != "ok":
        raise HistoryError("compose-ps")
    services = _require_mapping(compose_ps.get("services"), "compose-services")
    compose_healths: dict[str, str] = {}
    for service in STACK_SERVICES:
        row = _require_mapping(services.get(service), "compose-services")
        health = row.get("health")
        if not isinstance(health, str):
            raise HistoryError("compose-services")
        compose_healths[service] = health
    containers = _require_mapping(collectors.get("containers"), "containers")
    per_service = _require_mapping(containers.get("per_service"), "container-facts")
    restart_counts: dict[str, int] = {}
    for service in STACK_SERVICES:
        item = _require_mapping(per_service.get(service), "container-facts")
        if item.get("status") != "ok":
            raise HistoryError("container-facts")
        restart = item.get("restart_count")
        if not _is_int(restart) or restart < 0:  # type: ignore[operator]
            raise HistoryError("container-facts")
        restart_counts[service] = restart
    endpoint_collector = _require_mapping(collectors.get("endpoints"), "endpoints")
    per_endpoint = _require_mapping(endpoint_collector.get("per_endpoint"), "endpoint-facts")
    endpoints: dict[str, dict[str, object]] = {}
    for endpoint_id in ENDPOINT_IDS:
        item = _require_mapping(per_endpoint.get(endpoint_id), "endpoint-facts")
        if item.get("status") != "ok":
            raise HistoryError("endpoint-facts")
        status = item.get("http_status")
        latency = item.get("latency_ms")
        if not _is_int(status) or not _is_finite_number(latency) or latency < 0:  # type: ignore[operator]
            raise HistoryError("endpoint-facts")
        endpoints[endpoint_id] = {"http_status": status, "latency_ms": latency}
    logs = _require_mapping(collectors.get("logs"), "logs")
    per_log = _require_mapping(logs.get("per_service"), "log-summaries")
    log_error_totals: dict[str, int] = {}
    for service in STACK_SERVICES:
        item = _require_mapping(per_log.get(service), "log-summaries")
        if item.get("status") != "ok":
            raise HistoryError("log-summaries")
        total = item.get("error_total")
        if not _is_int(total) or total < 0:  # type: ignore[operator]
            raise HistoryError("log-summaries")
        log_error_totals[service] = total
    return {
        "collected_at": report["started_at_utc"],
        "collected_dt": started,
        "project": project,
        "overall_status": overall,
        "counts": {key: counts[key] for key in ("ok", "warn", "critical")},  # type: ignore[misc]
        "compose_healths": compose_healths,
        "restart_counts": restart_counts,
        "endpoints": endpoints,
        "log_error_totals": log_error_totals,
    }


# ---------------------------------------------------------------- 发现与加载


def discover_candidates(names: list[str]) -> list[str]:
    """仅 monitor-*.json 入选；glob 命中但 stem 不合规（含日历非法日期）→
    fail-closed（被拒名不回显）。"""
    candidates: list[str] = []
    for name in names:
        if not (name.startswith("monitor-") and name.endswith(".json")):
            continue
        stem = name[: -len(".json")]
        if ARTIFACT_STEM_RE.match(stem) is None or "/" in name or "\\" in name or ".." in name:
            raise HistoryError("nonallowlisted-artifact-name")
        try:
            datetime.strptime(stem[len("monitor-"):], "%Y%m%d-%H%M%S").replace(
                tzinfo=timezone.utc)
        except ValueError:
            raise HistoryError("nonallowlisted-artifact-name") from None
        candidates.append(name)
    return candidates


def load_sample(store: Store, source_dir: Path, name: str) -> Sample:
    """读取 + 哈希 + 校验单个源工件（symlink/解析/校验违规均 fail-closed）。"""
    path = source_dir / name
    if store.is_symlink(path):
        raise HistoryError("symlink-source")
    raw = store.read_bytes(path)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise HistoryError("not-json") from None
    fields = validate_report(data)
    sha256 = hashlib.sha256(raw).hexdigest()
    return Sample(
        stem=name[: -len(".json")], sha256=sha256, **fields,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------- 管道（去重/排序/留存）


def build_samples(store: Store, source_dir: Path) -> list[Sample]:
    """源目录 → 全部已验证样本（任何违规即抛错，先于任何输出写入）。"""
    if not store.exists(source_dir):
        raise HistoryError("source-dir-missing")
    reject_symlinked_path(store, source_dir)
    candidates = discover_candidates(store.list_dir(source_dir))
    if not candidates:
        raise HistoryError("no-sources")
    samples: list[Sample] = []
    for name in candidates:
        reject_symlinked_path(store, source_dir / name)
        samples.append(load_sample(store, source_dir, name))
    return samples


def dedupe_and_sort(samples: list[Sample]) -> tuple[list[Sample], int]:
    """同哈希去重（保留 (collected_dt, stem) 最小者）+ 冲突检测 + 确定性排序。
    返回（唯一样本升序, duplicate_count）；同 (project, collected_at) 不同
    哈希 → fail-closed（conflicting-duplicate）。"""
    by_hash: dict[str, Sample] = {}
    duplicates = 0
    for sample in sorted(samples, key=lambda s: (s.collected_dt, s.stem)):
        existing = by_hash.get(sample.sha256)
        if existing is None:
            by_hash[sample.sha256] = sample
        else:
            duplicates += 1
    unique = sorted(by_hash.values(), key=lambda s: (s.collected_dt, s.stem))
    seen_slots: dict[tuple[str, str], str] = {}
    for sample in unique:
        slot = (sample.project, sample.collected_at)
        previous = seen_slots.get(slot)
        if previous is not None and previous != sample.sha256:
            raise HistoryError("conflicting-duplicate")
        seen_slots[slot] = sample.sha256
    return unique, duplicates


def apply_retention(unique: list[Sample], retention: int) -> tuple[list[Sample], int]:
    """保留最新 N 条（升序返回）；返回（retained, omitted_older_count）。"""
    if len(unique) <= retention:
        return unique, 0
    omitted = len(unique) - retention
    return unique[len(unique) - retention:], omitted


# ---------------------------------------------------------------- 记录与摘要


def build_record(sample: Sample) -> dict[str, object]:
    """紧凑记录：规格字段全集；绝无原始日志行/密钥/secret。"""
    return {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "artifact_sha256": sample.sha256,
        "source_stem": sample.stem,
        "collected_at": sample.collected_at,
        "project": sample.project,
        "overall_status": sample.overall_status,
        "partial": False,
        "threshold_counts": dict(sample.counts),
        "compose_service_health": dict(sample.compose_healths),
        "restart_counts": dict(sample.restart_counts),
        "endpoints": {key: dict(value) for key, value in sample.endpoints.items()},
        "log_error_totals": dict(sample.log_error_totals),
    }


def percentile(sorted_values: list[float], fraction: float) -> float:
    """nearest-rank 百分位（升序非空输入，与 monitor/soak 同款）。"""
    rank = max(1, math.ceil(fraction * len(sorted_values)))
    return sorted_values[min(rank, len(sorted_values)) - 1]


def build_summary(*, retained: list[Sample], discovered: int, duplicates: int,
                  omitted_older: int, retention: int) -> dict[str, object]:
    """确定性趋势摘要（生成时间戳取自最新源样本 collected_at——零墙钟）。"""
    status_counts = {"ok": 0, "warn": 0, "critical": 0}
    for sample in retained:
        status_counts[sample.overall_status] += 1
    endpoint_stats: dict[str, dict[str, object]] = {}
    for endpoint_id in ENDPOINT_IDS:
        values = sorted(float(sample.endpoints[endpoint_id]["latency_ms"])  # type: ignore[arg-type]
                        for sample in retained)
        endpoint_stats[endpoint_id] = {
            "samples": len(values),
            "min": values[0],
            "p50": percentile(values, 0.50),
            "p95": percentile(values, 0.95),
            "max": values[-1],
        }
    restart_totals = {service: sum(s.restart_counts[service] for s in retained)
                      for service in STACK_SERVICES}
    log_totals = {service: sum(s.log_error_totals[service] for s in retained)
                  for service in STACK_SERVICES}
    oldest, newest = retained[0], retained[-1]
    return {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "generated_from_newest_at": newest.collected_at,
        "retention": retention,
        "records_discovered": discovered,
        "records_retained": len(retained),
        "duplicate_count": duplicates,
        "omitted_older_count": omitted_older,
        "oldest_retained_at": oldest.collected_at,
        "newest_retained_at": newest.collected_at,
        "status_counts": status_counts,
        "availability_count": status_counts["ok"],
        "degraded_count": status_counts["warn"],
        "critical_count": status_counts["critical"],
        "first_collected_at": oldest.collected_at,
        "last_collected_at": newest.collected_at,
        "endpoint_latency_ms": endpoint_stats,
        "restart_totals": restart_totals,
        "restart_total_sum": sum(restart_totals.values()),
        "log_error_totals": log_totals,
        "log_error_total_sum": sum(log_totals.values()),
    }


def _fmt_ms(value: object) -> str:
    return f"{value:.3f}" if isinstance(value, (int, float)) else "-"


def render_summary_markdown(summary: dict[str, object]) -> str:
    """Markdown 摘要（固定词汇表 + 渲染后统一脱敏式防御）。"""
    status = summary["status_counts"]
    assert isinstance(status, dict)
    latency = summary["endpoint_latency_ms"]
    assert isinstance(latency, dict)
    lines: list[str] = [
        f"# M14-13 监控历史摘要（schema_version={summary['schema_version']}）",
        "",
        f"- 生成时间戳（取自最新源样本 collected_at）：{summary['generated_from_newest_at']}",
        (
            f"- 记录：发现 {summary['records_discovered']} / 保留 {summary['records_retained']}"
            f"（留存上限 {summary['retention']}，省略更早 {summary['omitted_older_count']} 条，"
            f"重复内容 {summary['duplicate_count']} 条）"
        ),
        (f"- 保留边界：oldest {summary['oldest_retained_at']} ~ "
         f"newest {summary['newest_retained_at']}"),
        (f"- 状态计数：ok={status['ok']} warn={status['warn']} critical={status['critical']}"
         f"（availability={summary['availability_count']}，degraded={summary['degraded_count']}）"),
        "",
        "| 端点 | 样本数 | min(ms) | p50(ms) | p95(ms) | max(ms) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for endpoint_id in ENDPOINT_IDS:
        stats = latency[endpoint_id]
        assert isinstance(stats, dict)
        lines.append(
            f"| {endpoint_id} | {stats['samples']} | {_fmt_ms(stats['min'])} "
            f"| {_fmt_ms(stats['p50'])} | {_fmt_ms(stats['p95'])} | {_fmt_ms(stats['max'])} |"
        )
    restarts = summary["restart_totals"]
    assert isinstance(restarts, dict)
    logs = summary["log_error_totals"]
    assert isinstance(logs, dict)
    lines += [
        "",
        "| 服务 | restart 总计 | 日志 error 总计 |",
        "|---|---:|---:|",
    ]
    for service in STACK_SERVICES:
        lines.append(f"| {service} | {restarts[service]} | {logs[service]} |")
    lines += [
        "",
        f"- 合计：restart {summary['restart_total_sum']}；日志 error {summary['log_error_total_sum']}",
        "",
        "边界：",
        "- 源为 M14-12 monitor 只读 JSON 工件；原始工件永不改动/删除",
        "- 记录绝无原始日志行/密钥/secret/env 值（仅计数、状态、有限延迟数值）",
        "- 摘要零墙钟——生成时间戳取自最新源样本，输出逐字节可复现",
        "- 本工具零子进程/零网络/零容器面/零计划任务/零 env 读取",
        "- monitoring 历史可用性不构成 production readiness 宣称",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 写出（原子）


def write_outputs(store: Store, output_dir: Path, records: list[dict[str, object]],
                  summary: dict[str, object]) -> tuple[Path, Path]:
    """全部输入校验通过后才可到达此处；两输出同目录原子落盘。

    先于 mkdir 拒绝 output_dir 自身/现存祖先 symlink（mkdir(parents=True)
    会穿越 symlink 建目录——拒绝必须发生在任何创建之前）；mkdir 后再复查
    output_dir 与两输出目标（TOCTOU 窗口防御）。"""
    reject_symlinked_path(store, output_dir)
    store.mkdirs(output_dir)
    history_path = output_dir / HISTORY_OUTPUT_NAME
    summary_path = output_dir / SUMMARY_OUTPUT_NAME
    reject_symlinked_path(store, history_path, summary_path, output_dir)
    jsonl_text = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
    summary_text = render_summary_markdown(summary)
    store.write_atomic(history_path, jsonl_text)
    store.write_atomic(summary_path, summary_text)
    return history_path, summary_path


def run_history(*, store: Store, source_dir: Path, output_dir: Path,
                retention: int) -> dict[str, object]:
    """主管道：发现→校验→去重→排序→留存→摘要→写出（拒绝时零输出）。"""
    samples = build_samples(store, source_dir)
    discovered = len(samples)
    unique, duplicates = dedupe_and_sort(samples)
    retained, omitted = apply_retention(unique, retention)
    records = [build_record(sample) for sample in retained]
    summary = build_summary(retained=retained, discovered=discovered, duplicates=duplicates,
                            omitted_older=omitted, retention=retention)
    write_outputs(store, output_dir, records, summary)
    return summary


# ---------------------------------------------------------------- CLI


def validate_retention(value: int) -> str | None:
    if not MIN_RETENTION <= value <= MAX_RETENTION:
        return f"retention 必须在 {MIN_RETENTION}-{MAX_RETENTION}（收到 {value}）"
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monitoring_history.py",
        description="M14-13 监控历史索引：M14-12 monitor JSON → 有界留存 history.jsonl + 趋势摘要（零子进程/零网络/零墙钟）",
    )
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR,
                        help=f"源工件目录（默认 {DEFAULT_SOURCE_DIR}，gitignored；只读）")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                        help=f"输出目录（默认 {DEFAULT_OUTPUT_DIR}，gitignored；自定义路径为操作者显式自选）")
    parser.add_argument("--retention", type=int, default=DEFAULT_RETENTION,
                        help=f"留存条数 {MIN_RETENTION}-{MAX_RETENTION}（默认 {DEFAULT_RETENTION}，保留最新 N 条）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(f"{TAG} 源: {args.source_dir}（只读，绝不改动/删除源工件）", flush=True)
    problem = validate_retention(args.retention)
    if problem is not None:
        print(f"{TAG} 拒绝: {problem}", flush=True)
        return EXIT_REFUSED
    try:
        summary = run_history(store=RealStore(), source_dir=args.source_dir,
                              output_dir=args.output_dir, retention=args.retention)
    except HistoryError as cause:
        print(f"{TAG} 拒绝: {cause}（fail-closed，输出零写入）", flush=True)
        return EXIT_REFUSED
    except OSError as cause:
        print(f"{TAG} 写失败: {type(cause).__name__}（输出保持原子，无 tmp 残留）", flush=True)
        return EXIT_REFUSED
    status = summary["status_counts"]
    assert isinstance(status, dict)
    print(f"{TAG} 保留 {summary['records_retained']} 条（发现 {summary['records_discovered']}，"
          f"重复 {summary['duplicate_count']}，省略更早 {summary['omitted_older_count']}）", flush=True)
    print(f"{TAG} 状态: ok={status['ok']} warn={status['warn']} critical={status['critical']}", flush=True)
    print(f"{TAG} 边界: {summary['oldest_retained_at']} ~ {summary['newest_retained_at']}"
          f"（生成时间戳取自最新源: {summary['generated_from_newest_at']}）", flush=True)
    print(f"{TAG} 输出: {HISTORY_OUTPUT_NAME} / {SUMMARY_OUTPUT_NAME} -> {args.output_dir}", flush=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
