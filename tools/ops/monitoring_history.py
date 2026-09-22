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
  一律 fail-closed**（输出零写入）——唯一例外（M14-20）：**识别 M14-12
  monitor 自产的历史 incomplete 工件类**（完整 monitor 身份 +
  ``overall_status=incomplete`` + ``partial=true``——monitor 契约中任一
  采集器失败即二者恒共现，且采集器事实可合法含 failed，无法经完整校验）
  → **整件跳过不入档**（``skipped_incomplete_count`` 显式计数于摘要与
  stdout；源文件绝不改动/删除）；其余任何 incomplete/partial 形态
  （身份不符、incomplete+partial=false、完整状态+partial=true 等矛盾
  组合）仍一律 fail-closed。候选全为 incomplete（零完整样本）→
  ``no-complete-sources`` 拒绝。
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
- M14-101 性能修复：run 内路径安全检查去重（``PathSafetyCache``）。
  基线实现（M14-13）对**每个候选文件**重复 walk 同一祖先链——源目录
  深度 d 时每文件 ~2d 次 stat，946 文件即 ~1.3 万次冗余 stat，冷缓存
  （计划任务唤醒后 OS 缓存被换出）下足以把 history 聚合拖到 90s 超时
  （M14-101 生产观测 90.202s）。现改为：``run_history`` 创建单一
  run 内缓存贯穿发现与写出，「已确认存在且非 symlink」的祖先目录
  不再重复 stat（逐文件祖先检查摊还 O(1)；每文件仅保留自身 symlink
  检查 + 内容读取）。fail-closed 语义不变：缓存只记录**正向结论**
  （存在且非 symlink），不存在/未验证路径永不缓存、下次仍走完整
  检查；mkdir 后的 TOCTOU 复查恒走无缓存完整检查（见 write_outputs）。
  必经 I/O 下界 = 每文件 1 次 read + 1 次 symlink 自查（+目录级常数）。
- M14-23 restart 增量评估入档：``threshold_results.restart_evaluation``
  为**可选加法字段**——缺省 = v1 旧工件（合法入档，记录不带新键，
  旧记录/旧消费者零破坏）；在场即严格校验（固定词汇
  state/reason/baseline status、六服务全集、delta 为 int≥0 或 None、
  baseline_source_stem stem 白名单、baseline_collected_at 时间戳格式；
  任何违规 fail-closed ``restart-evaluation``）。记录累计
  ``restart_counts`` 口径不变，另存规范化 ``restart_evaluation``
  （baseline 元数据 + 逐服务 states/reasons/deltas——重建/重置事件清晰
  留痕，不可比增量恒 None 绝不静默归零）；摘要新增
  restart_delta_totals/restart_event_totals/restart_delta_sample_count
  （诚实区分「无增量数据」与「测得为零」），**累计 restart_totals 口径
  不重定义**。
- 退出码：0 成功；2 任何拒绝（参数超界、源目录缺失/symlink、零源、
  零完整样本（no-complete-sources）、malformed、partial/incomplete
  （未识别类）、冲突重复、写失败）。

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

#: 完整样本面接受的采集状态（incomplete 恒拒绝；历史 incomplete 类经
#: is_recognized_incomplete 整件识别跳过——M14-20）
ACCEPTED_OVERALL_STATUSES = frozenset({"ok", "warn", "critical"})

#: M14-23 restart 增量评估固定词汇（与 production_monitor 契约对齐；
#: monitoring_insights 经本模块引用——单一 schema 事实源）
RESTART_EVALUATION_STATES = frozenset({"ok", "warn", "critical"})
RESTART_EVALUATION_REASONS = frozenset({
    "stable", "delta", "baseline-missing", "container-recreated",
    "counter-reset", "facts-missing",
})
RESTART_EVALUATION_BASELINE_STATUSES = frozenset({"ok", "missing", "unusable"})
#: baseline 元数据一致性（supervisor R1）：status ↔ reason/stem/collected 联动
#: ——ok 恒 reason=None + 白名单 stem + canonical collected_at；missing 恒
#: 固定词汇 reason + 空元数据；unusable 恒 no-usable-prior-artifacts + 空元数据
RESTART_EVALUATION_BASELINE_REASONS_MISSING = frozenset({
    "artifact-dir-missing", "artifact-dir-unreadable", "no-prior-artifacts",
    "baseline-not-resolved",
})
RESTART_EVALUATION_BASELINE_REASON_UNUSABLE = "no-usable-prior-artifacts"

#: 历史 incomplete 工件类（M14-20）：M14-12 monitor 自产的采集不完整时期
#: 工件状态对（monitor 契约：任一采集器失败 → partial=true +
#: overall_status=incomplete 恒共现；ok/warn/critical 恒 partial=false）
HISTORICAL_INCOMPLETE_STATUS = "incomplete"

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


class PathSafetyCache:
    """M14-101 run 内路径安全检查缓存：「已确认存在且非 symlink」的祖先
    目录在本次 run 内不再重复 stat。

    背景（基线实现的算法浪费）：逐候选文件对同一路径前缀重复 walk 祖先
    链——源目录深度 d 时每文件 ~2d 次 stat，全部候选共享同一前缀，仅最后
    一级文件名不同（文件名不是任何其他候选的祖先）。946 文件 × ~14 次冗
    余 stat ≈ 1.3 万次，冷缓存下即 M14-101 观测的 history 聚合 90s 超时。

    语义（fail-closed 不变）：与逐次完整重查等价——缓存只记录**正向结
    论**（存在且非 symlink）；不存在/未验证的路径永不缓存，下次仍走完整
    exists+is_symlink 检查。父目录已缓存 ⇒ 其全部现存祖先已随之验证
    （缓存写入时走的是整条链），直接短路。源目录在单次 run 内为只读快照
    （pipeline.lock + 单次 list_dir），与基线实现共享同一 TOCTOU 假设——
    缓存不扩大该窗口；mkdir 后复查等需要真实再验证的场景应使用无缓存的
    ``reject_symlinked_path``。

    计数器（self_checks/ancestor_checks）仅供测试断言有界操作数——生
    产路径零依赖。"""

    def __init__(self, store: Store) -> None:
        self._store = store
        self._verified_ancestors: set[Path] = set()
        self.self_checks = 0
        self.ancestor_checks = 0

    def reject_symlinked(self, path: Path) -> None:
        """与 reject_symlinked_path 同语义：目标自身 + 现存祖先组件。"""
        self.self_checks += 1
        if self._store.is_symlink(path):
            raise HistoryError("symlink-target")
        if path.parent in self._verified_ancestors:
            return  # 父目录已验证 ⇒ 其祖先链已随之验证——零重复 stat
        for ancestor in path.parents:
            if ancestor in self._verified_ancestors:
                continue
            self.ancestor_checks += 1
            if self._store.exists(ancestor):
                if self._store.is_symlink(ancestor):
                    raise HistoryError("symlink-in-path")
                self._verified_ancestors.add(ancestor)


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
    #: M14-23 可选加法字段：v1 旧工件为 None（记录不带该键）
    restart_evaluation: dict[str, object] | None = None


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


def _parse_restart_evaluation(threshold: dict[str, object]) -> dict[str, object] | None:
    """M14-23：``threshold_results.restart_evaluation`` 可选加法字段——缺省
    （None）= v1 旧工件，合法；在场即严格校验并规范化为记录形态。违规抛
    HistoryError("restart-evaluation")（固定词汇；绝不携带文件内容文本）。"""
    raw = threshold.get("restart_evaluation")
    if raw is None:
        return None
    evaluation = _require_mapping(raw, "restart-evaluation")
    baseline = _require_mapping(evaluation.get("baseline"), "restart-evaluation")
    baseline_status = baseline.get("status")
    if not isinstance(baseline_status, str) or baseline_status not in (
            RESTART_EVALUATION_BASELINE_STATUSES):
        raise HistoryError("restart-evaluation")
    baseline_reason = baseline.get("reason")
    if baseline_reason is not None and not isinstance(baseline_reason, str):
        raise HistoryError("restart-evaluation")
    baseline_stem = baseline.get("source_stem")
    if baseline_stem is not None and (not isinstance(baseline_stem, str)
                                      or ARTIFACT_STEM_RE.match(baseline_stem) is None):
        raise HistoryError("restart-evaluation")
    baseline_collected = baseline.get("collected_at")
    if baseline_collected is not None:
        if not isinstance(baseline_collected, str):
            raise HistoryError("restart-evaluation")
        try:
            parse_timestamp(baseline_collected)
        except HistoryError:
            raise HistoryError("restart-evaluation") from None
    # supervisor R1：baseline 元数据一致性（status ↔ reason/stem/collected 联动）
    if baseline_status == "ok":
        if baseline_reason is not None or baseline_stem is None or baseline_collected is None:
            raise HistoryError("restart-evaluation")
    elif baseline_status == "missing":
        if (not isinstance(baseline_reason, str)
                or baseline_reason not in RESTART_EVALUATION_BASELINE_REASONS_MISSING):
            raise HistoryError("restart-evaluation")
        if baseline_stem is not None or baseline_collected is not None:
            raise HistoryError("restart-evaluation")
    else:  # unusable
        if baseline_reason != RESTART_EVALUATION_BASELINE_REASON_UNUSABLE:
            raise HistoryError("restart-evaluation")
        if baseline_stem is not None or baseline_collected is not None:
            raise HistoryError("restart-evaluation")
    invalid_skipped = baseline.get("invalid_skipped_count")
    if not _is_int(invalid_skipped) or invalid_skipped < 0:  # type: ignore[operator]
        raise HistoryError("restart-evaluation")
    per_service = _require_mapping(evaluation.get("per_service"), "restart-evaluation")
    states: dict[str, str] = {}
    reasons: dict[str, str] = {}
    deltas: dict[str, int | None] = {}
    for service in STACK_SERVICES:
        item = _require_mapping(per_service.get(service), "restart-evaluation")
        state = item.get("state")
        reason = item.get("reason")
        delta = item.get("delta")
        if not isinstance(state, str) or state not in RESTART_EVALUATION_STATES:
            raise HistoryError("restart-evaluation")
        if not isinstance(reason, str) or reason not in RESTART_EVALUATION_REASONS:
            raise HistoryError("restart-evaluation")
        if delta is not None and (not _is_int(delta) or delta < 0):  # type: ignore[operator]
            raise HistoryError("restart-evaluation")
        states[service] = state  # type: ignore[assignment]
        reasons[service] = reason  # type: ignore[assignment]
        deltas[service] = delta  # type: ignore[assignment]
    return {
        "baseline_status": baseline_status,
        "baseline_reason": baseline_reason,
        "baseline_source_stem": baseline_stem,
        "baseline_collected_at": baseline_collected,
        "baseline_invalid_skipped_count": invalid_skipped,
        "states": states,
        "reasons": reasons,
        "deltas": deltas,
    }


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
    restart_evaluation = _parse_restart_evaluation(threshold)
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
    fields: dict[str, object] = {
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
    if restart_evaluation is not None:  # M14-23：旧工件不带该键
        fields["restart_evaluation"] = restart_evaluation
    return fields


# ---------------------------------------------------------------- 发现与加载


def is_recognized_incomplete(data: object) -> bool:
    """识别 M14-12 monitor 自产的历史 incomplete 工件类（M14-20）：要求
    **完整 monitor 身份**（schema_version/tool/milestone/mode）+ 状态对
    ``overall_status=incomplete`` 且 ``partial is True``（monitor 契约中
    二者恒共现）。识别后整件跳过不入档（仅计数）；跳过件内容绝不进入
    任何输出。其余任何 incomplete/partial 形态（身份不符/矛盾组合）返回
    False → 走 validate_report 严格 fail-closed。"""
    if not isinstance(data, dict):
        return False
    return (
        data.get("schema_version") == EXPECTED_MONITOR_SCHEMA_VERSION
        and data.get("tool") == MONITOR_TOOL_NAME
        and data.get("milestone") == EXPECTED_MILESTONE
        and data.get("mode") == "execute"
        and data.get("overall_status") == HISTORICAL_INCOMPLETE_STATUS
        and data.get("partial") is True
    )


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


def load_sample(store: Store, source_dir: Path, name: str) -> Sample | None:
    """读取 + 哈希 + 校验单个源工件（symlink/解析/校验违规均 fail-closed）。
    历史 incomplete 工件类（is_recognized_incomplete）返回 None 跳过不入档。"""
    path = source_dir / name
    if store.is_symlink(path):
        raise HistoryError("symlink-source")
    raw = store.read_bytes(path)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise HistoryError("not-json") from None
    if is_recognized_incomplete(data):
        return None
    fields = validate_report(data)
    sha256 = hashlib.sha256(raw).hexdigest()
    return Sample(
        stem=name[: -len(".json")], sha256=sha256, **fields,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------- 管道（去重/排序/留存）


def discover_and_classify(store: Store, source_dir: Path,
                          safety: PathSafetyCache | None = None
                          ) -> tuple[list[Sample], int]:
    """源目录 → （完整样本列表, 跳过的历史 incomplete 工件计数）。任何
    未识别类违规即抛错，先于任何输出写入；候选存在但全为 incomplete
    （零完整样本）→ no-complete-sources 拒绝（fail-closed，绝不从空集
    构建历史）。symlink 防御覆盖全部候选（含跳过件）。M14-101：逐候选
    的祖先检查经 ``safety``（缺省内建一次性 PathSafetyCache）run 内去
    重——fail-closed 语义不变，冗余 stat 消除。"""
    if not store.exists(source_dir):
        raise HistoryError("source-dir-missing")
    checker = safety if safety is not None else PathSafetyCache(store)
    checker.reject_symlinked(source_dir)
    candidates = discover_candidates(store.list_dir(source_dir))
    if not candidates:
        raise HistoryError("no-sources")
    samples: list[Sample] = []
    skipped_incomplete = 0
    for name in candidates:
        checker.reject_symlinked(source_dir / name)
        sample = load_sample(store, source_dir, name)
        if sample is None:
            skipped_incomplete += 1
        else:
            samples.append(sample)
    if not samples:
        raise HistoryError("no-complete-sources")
    return samples, skipped_incomplete


def build_samples(store: Store, source_dir: Path) -> list[Sample]:
    """委托兼容面（monitoring_insights monitor 工件目录形态——单一
    schema 事实源）：仅返回完整样本列表；历史 incomplete 跳过语义同样
    生效（固定词汇拒绝原因原样透传）。"""
    return discover_and_classify(store, source_dir)[0]


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
    """紧凑记录：规格字段全集；绝无原始日志行/密钥/secret。M14-23：源工件
    带 restart_evaluation 时附加规范化增量评估（旧工件记录形态零变化）。"""
    record: dict[str, object] = {
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
    if sample.restart_evaluation is not None:
        record["restart_evaluation"] = {
            "baseline_status": sample.restart_evaluation["baseline_status"],
            "baseline_reason": sample.restart_evaluation["baseline_reason"],
            "baseline_source_stem": sample.restart_evaluation["baseline_source_stem"],
            "baseline_collected_at": sample.restart_evaluation["baseline_collected_at"],
            "baseline_invalid_skipped_count":
                sample.restart_evaluation["baseline_invalid_skipped_count"],
            "states": dict(sample.restart_evaluation["states"]),  # type: ignore[arg-type]
            "reasons": dict(sample.restart_evaluation["reasons"]),  # type: ignore[arg-type]
            "deltas": dict(sample.restart_evaluation["deltas"]),  # type: ignore[arg-type]
        }
    return record


def percentile(sorted_values: list[float], fraction: float) -> float:
    """nearest-rank 百分位（升序非空输入，与 monitor/soak 同款）。"""
    rank = max(1, math.ceil(fraction * len(sorted_values)))
    return sorted_values[min(rank, len(sorted_values)) - 1]


def build_summary(*, retained: list[Sample], discovered: int, duplicates: int,
                  omitted_older: int, retention: int,
                  skipped_incomplete: int = 0) -> dict[str, object]:
    """确定性趋势摘要（生成时间戳取自最新源样本 collected_at——零墙钟）。
    skipped_incomplete：识别跳过的历史 incomplete 工件计数（M14-20，不入档）。"""
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
    # M14-23：当轮增量/事件口径（与累计 restart_totals 明确区分；旧工件
    # 不带 restart_evaluation → 不计入增量样本——诚实区分「无数据」与「测
    # 得为零」）
    restart_delta_totals = {service: 0 for service in STACK_SERVICES}
    restart_event_totals = {service: 0 for service in STACK_SERVICES}
    restart_delta_sample_count = 0
    for sample in retained:
        evaluation = sample.restart_evaluation
        if evaluation is None:
            continue
        restart_delta_sample_count += 1
        states = evaluation["states"]
        deltas = evaluation["deltas"]
        assert isinstance(states, dict) and isinstance(deltas, dict)
        for service in STACK_SERVICES:
            delta = deltas.get(service)
            if isinstance(delta, int):
                restart_delta_totals[service] += delta
            if states.get(service) != "ok":
                restart_event_totals[service] += 1
    oldest, newest = retained[0], retained[-1]
    return {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "generated_from_newest_at": newest.collected_at,
        "retention": retention,
        "records_discovered": discovered,
        "records_retained": len(retained),
        "duplicate_count": duplicates,
        "omitted_older_count": omitted_older,
        "skipped_incomplete_count": skipped_incomplete,
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
        "restart_delta_totals": restart_delta_totals,
        "restart_delta_total_sum": sum(restart_delta_totals.values()),
        "restart_event_totals": restart_event_totals,
        "restart_event_total_sum": sum(restart_event_totals.values()),
        "restart_delta_sample_count": restart_delta_sample_count,
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
        (
            f"- 历史不完整工件：跳过 {summary['skipped_incomplete_count']} 条"
            "（M14-12 partial=true 采集不完整时期工件，不入档；源文件未改动）"
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
        (
            f"- restart 增量评估（M14-23）：增量数据样本"
            f" {summary['restart_delta_sample_count']}/{summary['records_retained']}；"
            f"增量合计 {summary['restart_delta_total_sum']}；"
            f"事件合计 {summary['restart_event_total_sum']}"
            "（增量=当轮新增 restart，与上方累计 restart 总计口径不同）"
        ),
        "",
        "边界：",
        "- 源为 M14-12 monitor 只读 JSON 工件；原始工件永不改动/删除",
        (
            "- 历史 incomplete 工件（M14-12 monitor 自产 partial=true 类）识别后跳过"
            "不入档（计数显式）；其余任何 malformed/partial/incomplete 形态一律 fail-closed"
        ),
        "- 记录绝无原始日志行/密钥/secret/env 值（仅计数、状态、有限延迟数值）",
        "- 摘要零墙钟——生成时间戳取自最新源样本，输出逐字节可复现",
        "- 本工具零子进程/零网络/零容器面/零计划任务/零 env 读取",
        "- monitoring 历史可用性不构成 production readiness 宣称",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 写出（原子）


def write_outputs(store: Store, output_dir: Path, records: list[dict[str, object]],
                  summary: dict[str, object],
                  safety: PathSafetyCache | None = None) -> tuple[Path, Path]:
    """全部输入校验通过后才可到达此处；两输出同目录原子落盘。

    先于 mkdir 拒绝 output_dir 自身/现存祖先 symlink（mkdir(parents=True)
    会穿越 symlink 建目录——拒绝必须发生在任何创建之前）；mkdir 后再复查
    output_dir 与两输出目标（TOCTOU 窗口防御——**复查恒走无缓存完整检查**，
    mkdir 前的缓存结论不可复用于此）。M14-101：mkdir 前检查经 ``safety``
    run 内去重。"""
    checker = safety if safety is not None else PathSafetyCache(store)
    checker.reject_symlinked(output_dir)
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
    """主管道：发现→分类（历史 incomplete 跳过）→校验→去重→排序→留存→
    摘要→写出（拒绝时零输出）。M14-101：单一 run 内 PathSafetyCache 贯穿
    发现与写出（mkdir 前检查）——逐文件祖先检查摊还 O(1)。"""
    safety = PathSafetyCache(store)
    samples, skipped_incomplete = discover_and_classify(store, source_dir, safety=safety)
    discovered = len(samples)
    unique, duplicates = dedupe_and_sort(samples)
    retained, omitted = apply_retention(unique, retention)
    records = [build_record(sample) for sample in retained]
    summary = build_summary(retained=retained, discovered=discovered, duplicates=duplicates,
                            omitted_older=omitted, retention=retention,
                            skipped_incomplete=skipped_incomplete)
    write_outputs(store, output_dir, records, summary, safety=safety)
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
    skipped = summary["skipped_incomplete_count"]
    assert isinstance(skipped, int)
    if skipped > 0:
        print(f"{TAG} 跳过历史 incomplete 工件: {skipped}（不入档，源文件未改动）", flush=True)
    print(f"{TAG} 状态: ok={status['ok']} warn={status['warn']} critical={status['critical']}", flush=True)
    print(f"{TAG} 边界: {summary['oldest_retained_at']} ~ {summary['newest_retained_at']}"
          f"（生成时间戳取自最新源: {summary['generated_from_newest_at']}）", flush=True)
    print(f"{TAG} 输出: {HISTORY_OUTPUT_NAME} / {SUMMARY_OUTPUT_NAME} -> {args.output_dir}", flush=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
