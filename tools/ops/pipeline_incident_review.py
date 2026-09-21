#!/usr/bin/env python
"""M14-79 管道事件复核：pipeline 报告 × history.jsonl 交叉只读复盘——
区分「管道执行失败（瞬态/持续）」与「监控状态劣化（warn/critical）」，
记录超时与恢复语义，绝不遮蔽任何失败。

背景事实（2026-09-21 生产，全部可从 canonical 工件复核）：PT15M 监控
管道在 12:30（monitor exit 2 且**未写出样本工件**→该槽位零样本）、
12:00/12:45（monitor ok 但 history 步超时 45.206s/46.955s vs 45s）返回
exit 1；13:00 全链恢复（monitor 1.454s / history 7.185s / insights
0.158s）。同时 history 尾部样本仍为 warn（容器重建告警→postgres
error_total=6）。三件事语义不同：① monitor 非零退出但**写出了样本
工件**（如 03:44/03:45 的 critical）是**状态域裁决**——监控正常工作，
系统确实劣化；② monitor 非零退出且**无工件**（12:30）是**执行域失败**
——该槽位数据真空；③ monitor ok 而 history/insights 超时（12:00/12:45）
是**执行域瞬态失败**——样本工件已落盘，history 索引由下一次成功运行
补录（monitoring_history 增量索引工件，实证：04:00:01 与 04:48:13 两行
均由后续运行补入）。本工具把这三类从「pipeline exit 1」这一个数字里
拆开，逐运行归因并判定恢复，供长稳重启门（soak_window_gate）与人工
复核消费。

设计（与 tools/ops/soak_stability_audit.py 同款纪律：单文件、纯标准库、
零第三方依赖；一切 I/O 经 Store 注入；零子进程/零网络/零计划任务/
零 env 读取/零墙钟；本工具只读输入、只写输出目录）：

- 输入（固定画像，只读）：默认 canonical gitignored
  ``.verify/artifacts/m14-14-monitoring-pipeline/``（管道报告目录）与
  ``.verify/artifacts/m14-13-monitoring-history/history.jsonl``。
  报告目录只认 ``pipeline-YYYYMMDD-HHMMSS.json``（mode=execute）与
  ``plan-YYYYMMDD-HHMMSS.json``（mode=plan，只计数不复盘）；``.md``
  与 ``pipeline.lock`` 忽略；其它 ``*.json`` stem → fail-closed 拒绝。
  逐报告严格校验：schema_version、mode 与文件名一致、overall_status
  词汇、三步 stage 齐全、stage status 词汇、started_at_utc 可解析且
  全目录唯一、按时间严格递增、monitor 绝不允许 skipped（管道结构性
  不变量）。history.jsonl 逐行严格解析（五字段 schema，复用
  monitoring_history 单一事实源），全局时序/唯一/项目单一。
- 逐运行归因（纯函数，固定词汇）：
  - ``failure_domain`` ∈ none / execution / status / mixed——
    monitor 非零但**有工件** = 状态域（含其引发的 history/insights
    skipped——那是设计内的门控后果，不是执行失败）；超时/执行错误/
    非零且**无工件** = 执行域；两类并存 = mixed。
  - ``execution_failure_kinds``：固定词汇清单（monitor-no-artifact /
    monitor-timeout / monitor-error / history-timeout / history-error /
    history-nonzero / insights-timeout / insights-error /
    insights-nonzero），超时运行标注 ``timed_out`` 与时长。
  - ``monitoring_sample``：样本工件名（若写出）→ 推导 collected_at →
    在 history 中反查是否已索引及索引状态（ok/warn/critical）——
    把「history 步超时」与「样本最终入史」两件事分开陈述（超时可
    补录，补录事实如实呈现，绝不因补录而把超时改记为成功）。
- 事件聚合与恢复语义：相邻非 ok 运行合并为一个事件窗口
  （first/last/起止 UTC/成员名），恢复 = 其后首个 overall ok 运行
  （recovered_by/at）；最新运行仍非 ok → 事件开放。状态域事件与
  执行域事件分别成列。history 状态面：trailing consecutive ok、
  最新样本状态、最后一次非 ok 样本——最新样本非 ok = 劣化开放。
- 判定与退出码：0 = 复盘完成且无开放项（管道非 failing 且状态非
  degraded）；1 = 复盘完成但有开放项（**可见结论，非崩溃**——报告
  照常落盘）；2 = 输入拒绝/参数越界/写失败（**零输出**）。
- 输出（默认 gitignored
  ``.verify/artifacts/m14-79-pipeline-incident-review/``）：
  确定性 JSON + Markdown（逐运行表 + 事件 + 恢复 + 判定；仅状态/
  时间戳/计数/固定 stem/sha256 面——绝无原始日志行/密钥/secret/
  env 值/URL/token/主机标识）。生成时间戳取自输入锚（最新运行
  started_at 与最新样本 collected_at 的较大者）——零墙钟，两次运行
  输出逐字节相同。tmp + fsync + os.replace 原子落盘；symlink/越界
  路径一律拒绝；仅在全部校验与分类完成后才写任何输出。

用法（仓库根）：
  python tools/ops/pipeline_incident_review.py            # 默认输入/输出
  python tools/ops/pipeline_incident_review.py \
      --pipeline-dir <dir> --history <history.jsonl>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

try:  # 直接脚本运行：tools/ops 自身在 sys.path
    import monitoring_history as _history
except ImportError:  # 经 importlib 按路径加载（契约测试形态）
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import monitoring_history as _history

EXIT_OK = 0
#: 1 = 复盘完成但有开放项（管道最新运行仍失败 或 状态劣化开放）
EXIT_OPEN = 1
#: 2 = 输入拒绝/参数越界/写失败（零输出）
EXIT_REFUSED = 2

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PIPELINE_DIR = (REPO_ROOT / ".verify" / "artifacts"
                        / "m14-14-monitoring-pipeline")
DEFAULT_HISTORY_PATH = (REPO_ROOT / ".verify" / "artifacts"
                        / "m14-13-monitoring-history" / "history.jsonl")
DEFAULT_OUTPUT_DIR = (REPO_ROOT / ".verify" / "artifacts"
                      / "m14-79-pipeline-incident-review")

REPORT_JSON_NAME = "pipeline-incident-review.json"
REPORT_MD_NAME = "pipeline-incident-review.md"

TAG = "[incident-review]"
REVIEW_SCHEMA_VERSION = 1
MILESTONE = "M14-79"
TOOL_NAME = "tools/ops/pipeline_incident_review.py"

#: 报告目录固定画像：pipeline-*（execute）与 plan-*（plan）双形态
PIPELINE_STEM_RE = _history.re.compile(r"^pipeline-[0-9]{8}-[0-9]{6}$")
PLAN_STEM_RE = _history.re.compile(r"^plan-[0-9]{8}-[0-9]{6}$")
#: 管道报告自身 schema 事实源（monitoring_pipeline.REPORT_SCHEMA_VERSION）
PIPELINE_REPORT_SCHEMA_VERSION = 1
#: 管道报告 step_id 固定三元组与 status 词汇（monitoring_pipeline 契约）
STAGE_IDS: tuple[str, ...] = ("monitor", "history", "insights")
STAGE_STATUS_VOCAB = frozenset({"ok", "failed", "timeout", "error",
                                "skipped", "planned"})
OVERALL_STATUS_VOCAB = frozenset({"ok", "failed", "planned"})

#: 事件/归因固定词汇
DOMAIN_NONE = "none"
DOMAIN_EXECUTION = "execution"
DOMAIN_STATUS = "status"
DOMAIN_MIXED = "mixed"

#: 留存契约复用 monitoring_history（默认 500 / 1-5000）
DEFAULT_RETENTION = _history.DEFAULT_RETENTION
MIN_RETENTION = _history.MIN_RETENTION
MAX_RETENTION = _history.MAX_RETENTION
#: 报告复盘条数上限口径（--runs，与 history 留存同界）
DEFAULT_RUNS = 500
MIN_RUNS = 1
MAX_RUNS = 5000


class ReviewError(RuntimeError):
    """fail-closed 拒绝（固定词汇原因；绝不携带文件内容文本）。"""


# ---------------------------------------------------------------- history 解析


@dataclass(frozen=True)
class HistoryRow:
    """已验证的历史行（仅复核所需五字段；多余字段绝不进入任何输出）。"""

    collected_dt: object  # datetime（复用 monitoring_history.parse_timestamp）
    collected_at: str
    project: str
    overall_status: str
    partial: bool


def _require_mapping(value: object, reason: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ReviewError(reason)
    return value


def validate_history_row(data: object, line_number: int) -> HistoryRow:
    """严格校验单行（与 soak_stability_audit 同款五字段面）。"""
    row = _require_mapping(data, f"malformed-row:{line_number}:not-an-object")
    if row.get("schema_version") != _history.HISTORY_SCHEMA_VERSION:
        raise ReviewError(f"malformed-row:{line_number}:schema-version")
    collected_at = row.get("collected_at")
    try:
        collected_dt = _history.parse_timestamp(collected_at)
    except _history.HistoryError:
        raise ReviewError(f"malformed-row:{line_number}:collected-at") from None
    project = row.get("project")
    if not isinstance(project, str) or _history.PROJECT_NAME_RE.match(project) is None:
        raise ReviewError(f"malformed-row:{line_number}:project")
    overall = row.get("overall_status")
    if (not isinstance(overall, str)
            or overall not in _history.ACCEPTED_OVERALL_STATUSES):
        raise ReviewError(f"malformed-row:{line_number}:overall-status")
    partial = row.get("partial")
    if not isinstance(partial, bool):
        raise ReviewError(f"malformed-row:{line_number}:partial")
    return HistoryRow(collected_dt=collected_dt, collected_at=str(collected_at),
                      project=project, overall_status=overall, partial=partial)


def parse_history(raw: bytes) -> list[HistoryRow]:
    """逐行严格解析（UTF-8 严格；空文件 → empty-history 拒绝）。"""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ReviewError("history-not-utf8") from None
    lines = text.splitlines()
    if not lines:
        raise ReviewError("empty-history")
    rows: list[HistoryRow] = []
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            raise ReviewError(f"malformed-row:{index}:empty-line")
        try:
            data = json.loads(line)
        except ValueError:
            raise ReviewError(f"malformed-row:{index}:not-json") from None
        rows.append(validate_history_row(data, index))
    if len(rows) > MAX_RETENTION:
        raise ReviewError("row-limit-exceeded")
    return rows


def validate_history_global(rows: list[HistoryRow]) -> None:
    """全局时序（升序）、时间戳唯一、项目单一；违规 fail-closed。"""
    previous_dt = None
    seen_at: set[str] = set()
    for row in rows:
        if row.collected_at in seen_at:
            raise ReviewError("duplicate-timestamp")
        seen_at.add(row.collected_at)
        if previous_dt is not None and row.collected_dt < previous_dt:  # type: ignore[operator]
            raise ReviewError("non-chronological-history")
        previous_dt = row.collected_dt
    if len({row.project for row in rows}) > 1:
        raise ReviewError("conflicting-project")


# ---------------------------------------------------------------- pipeline 报告解析


@dataclass(frozen=True)
class PipelineRun:
    """已验证的 execute 报告（仅复核所需字段；config/boundaries 不消费）。"""

    report_name: str
    started_at: str
    started_dt: object  # datetime
    overall_status: str
    stages: dict[str, dict[str, object]]
    monitor_artifact_names: tuple[str, ...]


def _validate_stage(stage: object, stage_id: str) -> dict[str, object]:
    entry = _require_mapping(stage, f"malformed-stage:{stage_id}:not-an-object")
    status = entry.get("status")
    if not isinstance(status, str) or status not in STAGE_STATUS_VOCAB:
        raise ReviewError(f"malformed-stage:{stage_id}:status")
    exit_code = entry.get("exit_code")
    if exit_code is not None and not isinstance(exit_code, int):
        raise ReviewError(f"malformed-stage:{stage_id}:exit-code")
    if not isinstance(entry.get("timed_out"), bool):
        raise ReviewError(f"malformed-stage:{stage_id}:timed-out")
    return entry


def validate_pipeline_report(data: object, report_name: str) -> PipelineRun:
    """严格校验单份 execute 报告（消费面字段全量校验）。"""
    obj = _require_mapping(data, f"malformed-report:{report_name}:not-an-object")
    if obj.get("schema_version") != PIPELINE_REPORT_SCHEMA_VERSION:
        raise ReviewError(f"malformed-report:{report_name}:schema-version")
    if obj.get("mode") != "execute":
        raise ReviewError(f"malformed-report:{report_name}:mode")
    if obj.get("tool") != "tools/ops/monitoring_pipeline.py":
        raise ReviewError(f"malformed-report:{report_name}:tool")
    started_at = obj.get("started_at_utc")
    try:
        started_dt = _history.parse_timestamp(started_at)
    except _history.HistoryError:
        raise ReviewError(f"malformed-report:{report_name}:started-at") from None
    overall = obj.get("overall_status")
    if not isinstance(overall, str) or overall not in OVERALL_STATUS_VOCAB:
        raise ReviewError(f"malformed-report:{report_name}:overall-status")
    stages_obj = obj.get("stages")
    if (not isinstance(stages_obj, dict) or not stages_obj
            or "monitor" not in stages_obj
            or not set(stages_obj) <= set(STAGE_IDS)):
        # M14-21 前的合法两步形态（monitor+history，无 insights）可解析；
        # 未知 step_id / 缺 monitor / 非 dict → fail-closed
        raise ReviewError(f"malformed-report:{report_name}:stages")
    stages = {sid: _validate_stage(stages_obj[sid], sid)
              for sid in STAGE_IDS if sid in stages_obj}
    if stages["monitor"]["status"] == "skipped":
        # 管道结构性不变量：monitor 是首步，永不 skipped
        raise ReviewError(f"malformed-report:{report_name}:monitor-skipped-invalid")
    artifacts_obj = stages["monitor"].get("artifacts")
    artifact_names: list[str] = []
    if isinstance(artifacts_obj, list):
        for entry in artifacts_obj:
            if not isinstance(entry, dict):
                raise ReviewError(f"malformed-report:{report_name}:monitor-artifacts")
            name = entry.get("name")
            if not isinstance(name, str):
                raise ReviewError(f"malformed-report:{report_name}:monitor-artifacts")
            if _history.ARTIFACT_STEM_RE.match(name[:-len(".json")] or ""):
                artifact_names.append(name)
    elif artifacts_obj is not None and not isinstance(artifacts_obj, dict):
        raise ReviewError(f"malformed-report:{report_name}:monitor-artifacts")
    return PipelineRun(report_name=report_name, started_at=str(started_at),
                       started_dt=started_dt, overall_status=overall,
                       stages=stages,
                       monitor_artifact_names=tuple(sorted(artifact_names)))


def collect_execute_reports(store: _history.Store, pipeline_dir: Path,
                            ) -> tuple[list[PipelineRun], int, int]:
    """枚举报告目录：pipeline-*（execute，严格校验）+ plan-*（只计数）。

    其它 *.json stem 一律拒绝（fail-closed）；.md 与 pipeline.lock 忽略。"""
    if not store.exists(pipeline_dir):
        raise ReviewError("pipeline-dir-missing")
    _history.reject_symlinked_path(store, pipeline_dir)
    if store.is_symlink(pipeline_dir):
        raise ReviewError("symlink-target")
    try:
        names = store.list_dir(pipeline_dir)
    except OSError:
        raise ReviewError("pipeline-dir-unreadable") from None
    runs: list[PipelineRun] = []
    plan_count = 0
    for name in names:
        if name.endswith(".md") or name == "pipeline.lock":
            continue
        if not name.endswith(".json"):
            continue
        stem = name[:-len(".json")]
        if PIPELINE_STEM_RE.match(stem) is not None:
            try:
                raw = store.read_bytes(pipeline_dir / name)
            except OSError:
                raise ReviewError("report-unreadable") from None
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise ReviewError(f"malformed-report:{stem}:not-json") from None
            runs.append(validate_pipeline_report(data, stem))
        elif PLAN_STEM_RE.match(stem) is not None:
            plan_count += 1
        else:
            raise ReviewError(f"unknown-report-stem:{stem}")
    if not runs:
        raise ReviewError("no-execute-reports")
    return runs, plan_count, len(names)


def validate_runs_global(runs: list[PipelineRun]) -> None:
    """按 started_at 严格递增且唯一（同目录两报告同秒 = 结构非法）。"""
    seen: set[str] = set()
    previous_dt = None
    for run in sorted(runs, key=lambda r: r.started_at):
        if run.started_at in seen:
            raise ReviewError("duplicate-run-started-at")
        seen.add(run.started_at)
        if previous_dt is not None and run.started_dt <= previous_dt:  # type: ignore[operator]
            raise ReviewError("non-chronological-reports")
        previous_dt = run.started_dt


# ---------------------------------------------------------------- 逐运行归因（纯）


def artifact_name_to_collected_at(name: str) -> str | None:
    """monitor-YYYYMMDD-HHMMSS.json → collected_at（时间戳面，安全事实）。"""
    stem = name.removesuffix(".json")
    if _history.ARTIFACT_STEM_RE.match(stem) is None:
        return None
    try:
        parsed = _history.datetime.strptime(stem[len("monitor-"):],
                                            "%Y%m%d-%H%M%S")
    except ValueError:
        return None
    return _history.datetime.strftime(parsed, _history.TIMESTAMP_FORMAT)


@dataclass(frozen=True)
class RunClassification:
    report_name: str
    started_at: str
    overall_status: str
    failure_domain: str
    execution_failure_kinds: tuple[str, ...]
    timeout_stages: tuple[str, ...]
    monitor_sample_artifact: str | None
    monitor_sample_collected_at: str | None
    monitor_sample_indexed: bool
    monitor_sample_status: str | None


def classify_run(run: PipelineRun,
                 history_by_collected_at: dict[str, HistoryRow],
                 ) -> RunClassification:
    """单运行归因（纯）：状态域（monitor 非零但有工件）与执行域严格分开。

    monitor 非零退出引发的 history/insights skipped 是**设计内门控后果**，
    不计执行失败——它们的事实由各自 stage 条目与 skipped_reason 原样保留
    在输入中，本函数绝不改写。"""
    monitor = run.stages["monitor"]
    status_domain = False
    execution_kinds: list[str] = []
    timeout_stages: list[str] = []

    if monitor["status"] == "ok":
        pass  # 样本工件已写出，裁决交由 history 反查呈现
    elif monitor["status"] == "failed":
        if run.monitor_artifact_names:
            # 非零退出但写出了样本工件 = 状态域裁决（monitor 正常工作）
            status_domain = True
        else:
            execution_kinds.append("monitor-no-artifact")
    elif monitor["status"] in ("timeout", "error"):
        execution_kinds.append(f"monitor-{monitor['status']}")
    # monitor skipped 已在解析层拒绝

    # M14-21 前的两步形态：缺席的 step 属「该时代不存在」，不计失败
    for stage_id in ("history", "insights"):
        if stage_id not in run.stages:
            continue
        stage = run.stages[stage_id]
        status = stage["status"]
        if status == "skipped":
            continue  # 前置门控后果（monitor 非零 / history 非 ok），非执行失败
        if status in ("timeout", "error"):
            execution_kinds.append(f"{stage_id}-{status}")
        elif status == "failed":
            execution_kinds.append(f"{stage_id}-nonzero")
        if status == "timeout":
            timeout_stages.append(stage_id)

    artifact = (run.monitor_artifact_names[-1]
                if run.monitor_artifact_names else None)
    collected_at = artifact_name_to_collected_at(artifact) if artifact else None
    indexed_row = (history_by_collected_at.get(collected_at)
                   if collected_at else None)

    if not execution_kinds and not status_domain:
        domain = DOMAIN_NONE
    elif execution_kinds and status_domain:
        domain = DOMAIN_MIXED
    elif status_domain:
        domain = DOMAIN_STATUS
    else:
        domain = DOMAIN_EXECUTION
    return RunClassification(
        report_name=run.report_name,
        started_at=run.started_at,
        overall_status=run.overall_status,
        failure_domain=domain,
        execution_failure_kinds=tuple(sorted(execution_kinds)),
        timeout_stages=tuple(timeout_stages),
        monitor_sample_artifact=artifact,
        monitor_sample_collected_at=collected_at,
        monitor_sample_indexed=indexed_row is not None,
        monitor_sample_status=(indexed_row.overall_status
                               if indexed_row else None),
    )


# ---------------------------------------------------------------- 事件聚合（纯）


@dataclass(frozen=True)
class IncidentWindow:
    kind: str  # execution / status / mixed
    first_run: str
    last_run: str
    started_at: str
    ended_at: str
    run_names: tuple[str, ...]
    recovered_by: str | None
    recovered_at: str | None


def aggregate_incidents(classifications: list[RunClassification],
                        ) -> list[IncidentWindow]:
    """相邻非 ok 运行合并为一个事件窗口（域并存 → mixed）；
    恢复 = 其后首个 overall ok 运行。"""
    incidents: list[IncidentWindow] = []
    current: list[RunClassification] = []
    for run in classifications:
        if run.overall_status != "ok":
            current.append(run)
        elif current:
            incidents.append(_close_incident(current, recovered_by=run))
            current = []
    if current:
        incidents.append(_close_incident(current))
    return incidents


def _close_incident(members: list[RunClassification],
                    recovered_by: RunClassification | None = None,
                    ) -> IncidentWindow:
    kinds = {m.failure_domain for m in members}
    kind = DOMAIN_MIXED if len(kinds) > 1 or DOMAIN_MIXED in kinds else kinds.pop()
    return IncidentWindow(
        kind=kind,
        first_run=members[0].report_name,
        last_run=members[-1].report_name,
        started_at=members[0].started_at,
        ended_at=members[-1].started_at,
        run_names=tuple(m.report_name for m in members),
        recovered_by=(recovered_by.report_name if recovered_by else None),
        recovered_at=(recovered_by.started_at if recovered_by else None),
    )


@dataclass(frozen=True)
class HistoryStatusView:
    row_count: int
    analyzed_row_count: int
    omitted_older_count: int
    tail_status_counts: dict[str, int]
    trailing_consecutive_ok: int
    latest_collected_at: str
    latest_status: str
    last_non_ok_collected_at: str | None
    last_non_ok_status: str | None


def history_status_view(rows: list[HistoryRow], *, retention: int,
                        ) -> HistoryStatusView:
    """状态域视图：最新样本状态 + 尾随连续 ok + 最后一次非 ok（纯）。"""
    omitted = max(0, len(rows) - retention)
    analyzed = rows[len(rows) - retention:] if omitted else rows
    counts = {"ok": 0, "warn": 0, "critical": 0}
    for row in analyzed:
        if row.overall_status in counts:
            counts[row.overall_status] += 1
    trailing = 0
    for row in reversed(analyzed):
        if row.overall_status == "ok" and not row.partial:
            trailing += 1
        else:
            break
    last_non_ok = next((row for row in reversed(analyzed)
                        if row.overall_status != "ok" or row.partial), None)
    return HistoryStatusView(
        row_count=len(rows), analyzed_row_count=len(analyzed),
        omitted_older_count=omitted, tail_status_counts=counts,
        trailing_consecutive_ok=trailing,
        latest_collected_at=analyzed[-1].collected_at,
        latest_status=analyzed[-1].overall_status,
        last_non_ok_collected_at=(last_non_ok.collected_at
                                  if last_non_ok else None),
        last_non_ok_status=(last_non_ok.overall_status if last_non_ok else None),
    )


# ---------------------------------------------------------------- 判定与报告（纯）


def derive_verdict(classifications: list[RunClassification],
                   history_view: HistoryStatusView) -> dict[str, object]:
    """顶层判定：管道执行面与监控状态面分开陈述（绝不合并遮蔽）。"""
    if not classifications:
        raise ReviewError("no-classified-runs")
    latest = classifications[-1]
    ever_failed = any(c.overall_status != "ok" for c in classifications)
    if not ever_failed:
        pipeline_state = "never-failed"
    elif latest.overall_status != "ok":
        pipeline_state = "failing"
    else:
        pipeline_state = "recovered"
    latest_row_ok = (history_view.latest_status == "ok"
                     and history_view.trailing_consecutive_ok > 0)
    if history_view.last_non_ok_collected_at is None:
        status_state = "clean"
    elif latest_row_ok:
        status_state = "recovered"
    else:
        status_state = "degraded"
    open_items: list[str] = []
    if pipeline_state == "failing":
        open_items.append("pipeline-latest-run-failed")
    if status_state == "degraded":
        open_items.append("history-latest-sample-non-ok")
    return {
        "pipeline_execution_state": pipeline_state,
        "monitoring_status_state": status_state,
        "open_items": open_items,
        "all_clear": not open_items,
    }


def build_report(*, runs: list[dict[str, object]],
                 incidents: list[dict[str, object]],
                 history_view: HistoryStatusView,
                 verdict: dict[str, object], generated_at: str,
                 history_sha256: str, history_bytes: int,
                 plan_report_count: int, run_omitted_older_count: int,
                 settings: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "milestone": MILESTONE,
        "generated_at": generated_at,
        "settings": settings,
        "inputs": {
            "history_sha256": history_sha256,
            "history_byte_size": history_bytes,
            "plan_report_count": plan_report_count,
            "execute_report_count": (len(runs)
                                     + run_omitted_older_count),
            "reviewed_run_count": len(runs),
            "run_omitted_older_count": run_omitted_older_count,
        },
        "runs": runs,
        "incidents": incidents,
        "history": {
            "row_count": history_view.row_count,
            "analyzed_row_count": history_view.analyzed_row_count,
            "omitted_older_count": history_view.omitted_older_count,
            "tail_status_counts": history_view.tail_status_counts,
            "trailing_consecutive_ok": history_view.trailing_consecutive_ok,
            "latest_collected_at": history_view.latest_collected_at,
            "latest_status": history_view.latest_status,
            "last_non_ok_collected_at": history_view.last_non_ok_collected_at,
            "last_non_ok_status": history_view.last_non_ok_status,
        },
        "verdict": verdict,
        "boundaries": list(REVIEW_BOUNDARIES),
    }


REVIEW_BOUNDARIES: tuple[str, ...] = (
    (
        "monitor exit nonzero WITH a written sample artifact is a status-domain verdict (monitoring worked, system degraded); exit nonzero WITHOUT an artifact is an execution-domain failure (no sample for that slot)"
    ),
    (
        "history/insights timeouts are execution-domain failures; the monitor sample artifact may still be indexed into history by a later successful run — backfill is reported as a fact and never rewrites the timeout as success"
    ),
    (
        "history/insights stages skipped after a nonzero monitor verdict are designed gating consequences, not execution failures"
    ),
    (
        "incident recovery = first subsequent overall-ok pipeline run; an incident whose recovery run is absent stays open and is never masked"
    ),
    (
        "monitoring status degradation is judged from history samples, independently of pipeline execution state"
    ),
    (
        "this tool is read-only: zero child-process spawns, zero network, zero scheduler mutation, zero env reads, zero wall clock (generated_at derives from input timestamps; outputs are byte-identical across reruns)"
    ),
    (
        "reports contain only statuses/timestamps/counts/fixed stems/sha256 — never raw log lines, secrets, env values, URLs, tokens, or host ids"
    ),
    (
        "an all_clear review is not production readiness; production_ready stays false and release approval remains human-only"
    ),
)


def render_report_markdown(report: dict[str, object]) -> str:
    history = report["history"]
    assert isinstance(history, dict)
    verdict = report["verdict"]
    assert isinstance(verdict, dict)
    inputs = report["inputs"]
    assert isinstance(inputs, dict)
    lines: list[str] = [
        (f"# {report['milestone']} 管道事件复核"
         f"（schema_version={report['schema_version']}）"),
        "",
        (f"- 判定：pipeline_execution_state=**{verdict['pipeline_execution_state']}**"
         f" / monitoring_status_state=**{verdict['monitoring_status_state']}**"
         + (f"（开放项：{', '.join(verdict['open_items'])}）"
            if verdict["open_items"] else "（无开放项）")),
        (f"- 生成锚（零墙钟，取自输入时间戳）：{report['generated_at']}；"
         f"history SHA-256 `{inputs['history_sha256']}`"),
        (f"- 输入：execute 报告 {inputs['execute_report_count']} 份"
         f"（复盘 {inputs['reviewed_run_count']}，省略更早 "
         f"{inputs['run_omitted_older_count']}），plan 报告 "
         f"{inputs['plan_report_count']} 份（只计数）；"
         f"history {history['row_count']} 行（分析 {history['analyzed_row_count']}）"),
        (f"- history 状态面：最新样本 {history['latest_collected_at']} = "
         f"**{history['latest_status']}**，尾随连续 ok "
         f"{history['trailing_consecutive_ok']}"
         + (f"，最后一次非 ok：{history['last_non_ok_collected_at']}"
            f"（{history['last_non_ok_status']}）"
            if history["last_non_ok_collected_at"] else "，全史干净")),
        "",
        "## 逐运行归因（时间升序）",
        "",
        "| 报告 | 开始(UTC) | overall | 域 | 执行失败 | 超时步 | 样本工件 | 入史 | 样本状态 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    counts = report["history"]["tail_status_counts"]
    assert isinstance(counts, dict)
    for run in report["runs"]:
        assert isinstance(run, dict)
        lines.append(
            f"| {run['report_name']} | {run['started_at']} "
            f"| {run['overall_status']} | {run['failure_domain']} "
            f"| {', '.join(run['execution_failure_kinds']) or '-'} "
            f"| {', '.join(run['timeout_stages']) or '-'} "
            f"| {run['monitor_sample_artifact'] or '-'} "
            f"| {'yes' if run['monitor_sample_indexed'] else 'no'} "
            f"| {run['monitor_sample_status'] or '-'} |")
    incidents = report["incidents"]
    lines += ["", "## 事件窗口与恢复", ""]
    if incidents:
        lines += ["| 域 | 首运行 | 末运行 | 起(UTC) | 止(UTC) | 恢复运行 | 恢复于(UTC) |",
                  "|---|---|---|---|---|---|---|"]
        for incident in incidents:
            assert isinstance(incident, dict)
            lines.append(
                f"| {incident['kind']} | {incident['first_run']} "
                f"| {incident['last_run']} | {incident['started_at']} "
                f"| {incident['ended_at']} "
                f"| {incident['recovered_by'] or '开放'} "
                f"| {incident['recovered_at'] or '-'} |")
    else:
        lines.append("（复盘窗口内无失败运行）")
    lines += [
        "",
        "边界：",
        "- monitor 非零但写出样本工件 = 状态域裁决；非零且无工件 = 执行域失败（该槽位零样本）",
        "- history/insights 超时 = 执行域失败；样本事后被补录是事实陈述，绝不改记为成功",
        "- monitor 非零裁决引发的 skipped = 设计内门控后果，非执行失败",
        "- 事件恢复 = 其后首个 overall ok 运行；无恢复运行的事件保持开放，绝不遮蔽",
        "- 状态劣化独立于管道执行面判定（history 样本为准）",
        "- 零子进程/零网络/零计划任务/零 env 读取/零墙钟；输出逐字节可复现",
        "- 报告绝无原始日志行/密钥/secret/env 值/URL/token/主机标识",
        "- all_clear ≠ production readiness（production_ready=false 不变）",
        "- 退出码：0 无开放项 / 1 有开放项（可见结论） / 2 输入拒绝（零输出）",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 写出（原子）


def write_outputs(store: _history.Store, output_dir: Path,
                  report: dict[str, object]) -> tuple[Path, Path]:
    """全部校验与分类完成后才可到达此处；两输出同目录原子落盘。"""
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


def validate_settings(*, runs: int, retention: int) -> str | None:
    if not MIN_RUNS <= runs <= MAX_RUNS:
        return f"runs 必须在 {MIN_RUNS}-{MAX_RUNS}（收到 {runs}）"
    if not MIN_RETENTION <= retention <= MAX_RETENTION:
        return f"retention 必须在 {MIN_RETENTION}-{MAX_RETENTION}（收到 {retention}）"
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline_incident_review.py",
        description=("M14-79 管道事件复核：pipeline 报告 × history 交叉只读复盘，"
                     "区分执行失败与状态劣化，记录超时/补录/恢复语义（零子进程/"
                     "零网络/零墙钟）"),
    )
    parser.add_argument("--pipeline-dir", type=Path, default=DEFAULT_PIPELINE_DIR,
                        help=(f"管道报告目录（默认 {DEFAULT_PIPELINE_DIR}，"
                              f"gitignored；只读）"))
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY_PATH,
                        help=(f"history.jsonl（默认 {DEFAULT_HISTORY_PATH}，"
                              f"gitignored；只读）"))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                        help=(f"输出目录（默认 {DEFAULT_OUTPUT_DIR}，gitignored；"
                              f"自定义路径为操作者显式自选）"))
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS,
                        help=(f"复盘最新 N 份 execute 报告 {MIN_RUNS}-{MAX_RUNS}"
                              f"（默认 {DEFAULT_RUNS}；更早报告只计数省略）"))
    parser.add_argument("--retention", type=int, default=DEFAULT_RETENTION,
                        help=(f"history 分析留存条数 {MIN_RETENTION}-{MAX_RETENTION}"
                              f"（默认 {DEFAULT_RETENTION}，保留最新 N 行）"))
    return parser


def run_review(*, store: _history.Store, pipeline_dir: Path, history: Path,
               output_dir: Path, runs: int, retention: int) -> dict[str, object]:
    """主管道：枚举校验报告 → 解析校验 history → 归因聚合 → 判定 → 写出。"""
    history_path = history
    if history_path.name != "history.jsonl":
        candidate = history_path / "history.jsonl"
        if not store.exists(candidate):
            raise ReviewError("history-path-missing")
        history_path = candidate
    _history.reject_symlinked_path(store, history_path)
    raw = store.read_bytes(history_path)
    history_sha256 = hashlib.sha256(raw).hexdigest()
    history_rows = parse_history(raw)
    validate_history_global(history_rows)
    all_runs, plan_count, _ = collect_execute_reports(store, pipeline_dir)
    validate_runs_global(all_runs)
    all_runs = sorted(all_runs, key=lambda r: r.started_at)
    run_omitted = max(0, len(all_runs) - runs)
    reviewed_runs = (all_runs[len(all_runs) - runs:] if run_omitted
                     else all_runs)
    history_index = {row.collected_at: row for row in history_rows}
    classified = [classify_run(run, history_index) for run in reviewed_runs]
    incidents = aggregate_incidents(classified)
    view = history_status_view(history_rows, retention=retention)
    verdict = derive_verdict(classified, view)
    # 零墙钟：生成锚 = 复盘运行 started_at 与 history 最新样本的较大者
    candidates = [classified[-1].started_at, view.latest_collected_at]
    generated_at = max(candidates)
    settings = {"runs": runs, "retention": retention}
    report = build_report(
        runs=[_classification_entry(c) for c in classified],
        incidents=[_incident_entry(i) for i in incidents],
        history_view=view, verdict=verdict, generated_at=generated_at,
        history_sha256=history_sha256, history_bytes=len(raw),
        plan_report_count=plan_count,
        run_omitted_older_count=run_omitted, settings=settings)
    write_outputs(store, output_dir, report)
    return report


def _classification_entry(c: RunClassification) -> dict[str, object]:
    return {
        "report_name": c.report_name,
        "started_at": c.started_at,
        "overall_status": c.overall_status,
        "failure_domain": c.failure_domain,
        "execution_failure_kinds": list(c.execution_failure_kinds),
        "timeout_stages": list(c.timeout_stages),
        "monitor_sample_artifact": c.monitor_sample_artifact,
        "monitor_sample_collected_at": c.monitor_sample_collected_at,
        "monitor_sample_indexed": c.monitor_sample_indexed,
        "monitor_sample_status": c.monitor_sample_status,
    }


def _incident_entry(i: IncidentWindow) -> dict[str, object]:
    return {
        "kind": i.kind,
        "first_run": i.first_run,
        "last_run": i.last_run,
        "started_at": i.started_at,
        "ended_at": i.ended_at,
        "run_names": list(i.run_names),
        "recovered_by": i.recovered_by,
        "recovered_at": i.recovered_at,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(f"{TAG} 输入: {args.pipeline_dir} + {args.history}（只读）", flush=True)
    problem = validate_settings(runs=args.runs, retention=args.retention)
    if problem is not None:
        print(f"{TAG} 拒绝: {problem}", flush=True)
        return EXIT_REFUSED
    try:
        report = run_review(store=_history.RealStore(),
                            pipeline_dir=args.pipeline_dir,
                            history=args.history, output_dir=args.output_dir,
                            runs=args.runs, retention=args.retention)
    except (ReviewError, _history.HistoryError) as cause:
        print(f"{TAG} 拒绝: {cause}（fail-closed，输出零写入）", flush=True)
        return EXIT_REFUSED
    except OSError as cause:
        print(f"{TAG} 写失败: {type(cause).__name__}（输出保持原子，无 tmp 残留）",
              flush=True)
        return EXIT_REFUSED
    verdict = report["verdict"]
    assert isinstance(verdict, dict)
    history = report["history"]
    assert isinstance(history, dict)
    print(f"{TAG} 判定: pipeline={verdict['pipeline_execution_state']} "
          f"status={verdict['monitoring_status_state']}"
          + (f"（{', '.join(verdict['open_items'])}）"
             if verdict["open_items"] else "（无开放项）"), flush=True)
    print(f"{TAG} history: 最新样本 {history['latest_collected_at']}="
          f"{history['latest_status']}，尾随连续 ok "
          f"{history['trailing_consecutive_ok']}", flush=True)
    print(f"{TAG} 输出: {REPORT_JSON_NAME} / {REPORT_MD_NAME} -> {args.output_dir}",
          flush=True)
    if verdict["all_clear"]:
        return EXIT_OK
    return EXIT_OPEN


if __name__ == "__main__":
    sys.exit(main())
