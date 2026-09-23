#!/usr/bin/env python
"""M14-108 监控历史时序查询：M14-13 canonical history.jsonl → 安全只读查询面。

设计（与 tools/ops/monitoring_history.py / monitoring_insights.py 同款纪律：
单文件、纯标准库、零第三方依赖；本工具是**纯只读查询面**——零网络、零
子进程、零计划任务、零 env 读取、零生产容器/DB/对象存储接触、**零文件
写入**（只读输入文件、只写 stdout；绝不改动/删除任何输入工件）：

- 单一事实源：schema/画像常量与行级校验**复用同仓既有已测面**——
  ``monitoring_history``（六服务/五端点画像、HISTORY_SCHEMA_VERSION、
  时间戳解析、nearest-rank percentile、退出码）与
  ``monitoring_insights.parse_history_text``（canonical history 行级
  严格校验 + 行序严格递增 + 单一 project + 样本量界 1–5000——其固定
  词汇拒绝原因原样透传，本工具零平行 schema、零重复校验实现）。
- 输入面：默认 = ``monitoring_history`` canonical 输出
  ``.verify/artifacts/m14-13-monitoring-history/history.jsonl``（单一
  事实源常量派生）；``--history`` 可指定其他文件。symlink 目标/现存
  symlink 祖先组件、缺失、目录形态一律拒绝（fail-closed）。
- 有界查询参数（全部先于任何读取校验；超界/非法一律拒绝且零读取）：
  - ``--start``/``--end``：UTC 时间戳（与 canonical 同格式
    ``%Y-%m-%dT%H:%M:%SZ``）；时间窗**两端均含边界**
    （start <= collected_at <= end）；start > end 拒绝；只给一端即单边窗。
  - ``--status``：逗号分隔，取值恒 ∈ {ok, warn, critical}；空段/重复/
    词汇外拒绝；缺省 = 不过滤。
  - ``--service``/``--endpoint``：**维度选择**（聚合与记录投影的切片面
    ——canonical 记录是完整栈样本，维度不是记录过滤面）；取值恒 ∈
    六服务/五端点固定画像；缺省 = 全集。
  - ``--limit``：记录列表界，默认 50、1–500（与 insights 事件列表界
    同款保守口径）；保留**最新** N 条（时间升序展示），省略更早条数
    显式 ``truncated_older_count``；**聚合恒为全窗口口径**——limit 只
    界记录列表，绝不扭曲聚合。
- 输出（stdout only，零落盘）：``--format summary``（默认，人读摘要）
  或 ``--format json``（单文档机器可读 JSON：query echo / window 计数 /
  状态计数 / 逐所选端点延迟 min-p50-p95-max（nearest-rank，单一事实
  源）/ 逐所选服务 restart 与日志 error 总计 / 有界记录投影——仅从
  合法历史记录派生的聚合与投影，绝无原始日志行/密钥/secret/env 值；
  行级未知额外字段被忽略且绝不进入输出）。任何拒绝 → 固定词汇拒绝行
  + exit 2，**JSON/摘要正文零输出（零部分输出）**。
- 零墙钟：输出不含任何生成/查询时间戳——同参数两次运行 stdout 逐字节
  相同（时间边界取自数据本身）。
- 边界：本切片是查询工具，不是生产查询服务；不构成 provider-smoke 或
  任何 release blocker 的解除；不构成 production readiness 宣称。
- 退出码：0 查询成功（含零命中——空窗口是合法查询结果，如实输出零
  计数）；2 任何拒绝（参数超界、输入缺失/symlink/目录形态、malformed、
  乱序、重复、混档、超 5000、读失败）。

用法（仓库根）：
  python tools/ops/monitoring_history_query.py                       # 全量摘要
  python tools/ops/monitoring_history_query.py --status critical \
      --start 2026-09-11T00:00:00Z --end 2026-09-12T00:00:00Z --limit 20
  python tools/ops/monitoring_history_query.py --format json \
      --service redis,api --endpoint api-health
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

try:  # 直接脚本运行：tools/ops 自身在 sys.path
    import monitoring_history as _history
    import monitoring_insights as _insights
except ImportError:  # 经 importlib 按路径加载（契约测试形态）
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import monitoring_history as _history
    import monitoring_insights as _insights

EXIT_OK = 0
EXIT_REFUSED = 2

TAG = "[query]"
QUERY_SCHEMA_VERSION = 1
TOOL_NAME = "tools/ops/monitoring_history_query.py"

#: 画像与 schema 常量：单一事实源 = 同仓 M14-13 工具
STACK_SERVICES: tuple[str, ...] = _history.STACK_SERVICES
ENDPOINT_IDS: tuple[str, ...] = _history.ENDPOINT_IDS
HISTORY_SCHEMA_VERSION = _history.HISTORY_SCHEMA_VERSION
ACCEPTED_OVERALL_STATUSES: frozenset[str] = frozenset(
    _history.ACCEPTED_OVERALL_STATUSES)

#: canonical history 输入面（单一事实源 = monitoring_history 输出目录）
DEFAULT_HISTORY_PATH = _history.DEFAULT_OUTPUT_DIR / _history.HISTORY_OUTPUT_NAME

#: 记录列表界：默认 50，1–500（保守口径，与 insights 事件列表界同款）
DEFAULT_LIMIT = 50
MIN_LIMIT = 1
MAX_LIMIT = 500

#: 行级校验委托面（单一事实源 = monitoring_insights 已测校验器）
parse_history_text = _insights.parse_history_text


class QueryError(RuntimeError):
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
class QueryOptions:
    """已校验查询参数（时间窗含边界；statuses None = 不过滤）。"""

    start: datetime | None
    end: datetime | None
    statuses: tuple[str, ...] | None
    services: tuple[str, ...]
    endpoints: tuple[str, ...]
    limit: int


def _parse_bound(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return _history.parse_timestamp(value)
    except _history.HistoryError:
        raise QueryError("time-window-format") from None


def _parse_csv(value: str, allowed: tuple[str, ...] | frozenset[str],
               reason: str) -> tuple[str, ...]:
    """逗号分隔子集解析：空串/空段/重复/词汇外一律固定词汇拒绝（保序）。"""
    items = value.split(",")
    ordered: list[str] = []
    for item in items:
        if item == "" or item not in allowed or item in ordered:
            raise QueryError(reason)
        ordered.append(item)
    return tuple(ordered)


def parse_options(args: argparse.Namespace) -> QueryOptions:
    try:
        limit = int(args.limit)
    except (TypeError, ValueError):
        raise QueryError("limit") from None
    if not MIN_LIMIT <= limit <= MAX_LIMIT:
        raise QueryError("limit")
    statuses = (None if args.status is None else
                _parse_csv(args.status, ACCEPTED_OVERALL_STATUSES, "status-filter"))
    services = (STACK_SERVICES if args.service is None else
                _parse_csv(args.service, STACK_SERVICES, "service-filter"))
    endpoints = (ENDPOINT_IDS if args.endpoint is None else
                 _parse_csv(args.endpoint, ENDPOINT_IDS, "endpoint-filter"))
    start = _parse_bound(args.start)
    end = _parse_bound(args.end)
    if start is not None and end is not None and start > end:
        raise QueryError("time-window-order")
    return QueryOptions(start=start, end=end, statuses=statuses, services=services,
                        endpoints=endpoints, limit=limit)


# ---------------------------------------------------------------- 加载与行校验（委托单一事实源）


def load_history(store: Store, path: Path) -> list[dict[str, object]]:
    """只读加载：symlink 防御（复用 insights 已测语义）+ canonical 行级校验
    （parse_history_text 透传——固定词汇拒绝原因原样透传）。"""
    try:
        _insights.reject_symlinked_path(store, path)  # type: ignore[arg-type]
    except _insights.InsightsError as cause:
        raise QueryError(str(cause)) from None
    if not store.exists(path):
        raise QueryError("history-missing")
    if store.is_dir(path):
        raise QueryError("history-kind")
    try:
        text = store.read_bytes(path).decode("utf-8")
    except UnicodeDecodeError:
        raise QueryError("not-json") from None
    try:
        return parse_history_text(text)
    except _insights.InsightsError as cause:
        raise QueryError(str(cause)) from None


# ---------------------------------------------------------------- 查询管道（纯）


def _matches(row: dict[str, object], options: QueryOptions) -> bool:
    collected = row["collected_dt"]
    assert isinstance(collected, datetime)
    if options.start is not None and collected < options.start:
        return False
    if options.end is not None and collected > options.end:
        return False
    return options.statuses is None or row["overall_status"] in options.statuses


def _endpoint_latency(rows: list[dict[str, object]], endpoint_id: str
                      ) -> dict[str, object]:
    """nearest-rank 逐端点延迟（percentile 单一事实源 = monitoring_history）。"""
    if not rows:
        return {"samples": 0, "min": None, "p50": None, "p95": None, "max": None}
    values = sorted(
        float(row["endpoints"][endpoint_id]["latency_ms"])  # type: ignore[index,arg-type]
        for row in rows)
    return {
        "samples": len(values),
        "min": values[0],
        "p50": _history.percentile(values, 0.50),
        "p95": _history.percentile(values, 0.95),
        "max": values[-1],
    }


def project_record(row: dict[str, object], *, services: tuple[str, ...],
                   endpoints: tuple[str, ...]) -> dict[str, object]:
    """有界记录投影：仅白名单字段 + 所选维度切片（绝无原始日志/密钥/secret；
    行级未知额外字段在此被结构性丢弃）。"""
    return {
        "source_stem": row["stem"],
        "collected_at": row["collected_at"],
        "overall_status": row["overall_status"],
        "threshold_counts": dict(row["counts"]),  # type: ignore[arg-type]
        "compose_service_health": {s: row["healths"][s] for s in services},  # type: ignore[index]
        "restart_counts": {s: row["restarts"][s] for s in services},  # type: ignore[index]
        "log_error_totals": {s: row["log_errors"][s] for s in services},  # type: ignore[index]
        "endpoints": {e: dict(row["endpoints"][e]) for e in endpoints},  # type: ignore[index]
    }


def build_result(*, rows: list[dict[str, object]], options: QueryOptions,
                 history_path: Path) -> dict[str, object]:
    """过滤（时间窗含边界 + 状态）→ limit（保留最新 N）→ 聚合（恒全窗口
    口径）→ 有界记录投影。零墙钟：无任何生成/查询时间戳。"""
    matched = [row for row in rows if _matches(row, options)]
    truncated = max(0, len(matched) - options.limit)
    returned = matched[truncated:] if truncated else matched
    status_counts = {"ok": 0, "warn": 0, "critical": 0}
    for row in matched:
        status_counts[row["overall_status"]] += 1  # type: ignore[index]
    restart_totals = {
        service: sum(int(row["restarts"][service]) for row in matched)  # type: ignore[index]
        for service in options.services}
    log_totals = {
        service: sum(int(row["log_errors"][service]) for row in matched)  # type: ignore[index]
        for service in options.services}
    return {
        "schema_version": QUERY_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "project": rows[0]["project"],
        "query": {
            "history_file": _insights.display_path(history_path),
            "start": options.start.strftime(_history.TIMESTAMP_FORMAT)
            if options.start is not None else None,
            "end": options.end.strftime(_history.TIMESTAMP_FORMAT)
            if options.end is not None else None,
            "statuses": list(options.statuses) if options.statuses is not None else None,
            "services": list(options.services),
            "endpoints": list(options.endpoints),
            "limit": options.limit,
        },
        "window": {
            "records_scanned": len(rows),
            "records_matched": len(matched),
            "records_returned": len(returned),
            "truncated_older_count": truncated,
            "oldest_matched_at": matched[0]["collected_at"] if matched else None,
            "newest_matched_at": matched[-1]["collected_at"] if matched else None,
        },
        "status_counts": status_counts,
        "endpoint_latency_ms": {endpoint_id: _endpoint_latency(matched, endpoint_id)
                                for endpoint_id in options.endpoints},
        "restart_totals": restart_totals,
        "log_error_totals": log_totals,
        "records": [project_record(row, services=options.services,
                                   endpoints=options.endpoints) for row in returned],
    }


def run_query(*, store: Store, history_path: Path,
              options: QueryOptions) -> dict[str, object]:
    rows = load_history(store, history_path)
    return build_result(rows=rows, options=options, history_path=history_path)


# ---------------------------------------------------------------- 摘要渲染


def _fmt_ms(value: object) -> str:
    return f"{value:.3f}" if isinstance(value, (int, float)) else "-"


def render_summary(result: dict[str, object]) -> str:
    """人读摘要（stdout only；固定词汇 + 边界注记；零墙钟）。"""
    query = result["query"]
    window = result["window"]
    assert isinstance(query, dict) and isinstance(window, dict)
    start = query["start"] if query["start"] is not None else "（无下界）"
    end = query["end"] if query["end"] is not None else "（无上界）"
    statuses = ",".join(query["statuses"]) if query["statuses"] is not None else "不过滤"
    counts = result["status_counts"]
    assert isinstance(counts, dict)
    lines: list[str] = [
        f"{TAG} 输入: {query['history_file']}（只读，绝不改动）",
        (f"{TAG} 查询: 窗口 {start} ~ {end}（两端含边界）；状态 {statuses}；"
         f"服务 {len(query['services'])}/{len(STACK_SERVICES)}；"
         f"端点 {len(query['endpoints'])}/{len(ENDPOINT_IDS)}；"
         f"limit {query['limit']}（保留最新 N，聚合恒全窗口口径）"),
        (f"{TAG} 窗口: 扫描 {window['records_scanned']} / "
         f"匹配 {window['records_matched']} / 返回 {window['records_returned']}"
         f"（省略更早 {window['truncated_older_count']}）"),
        (f"{TAG} 边界: {window['oldest_matched_at']} ~ {window['newest_matched_at']}"
         "（匹配集 oldest/newest）"),
        f"{TAG} 状态: ok={counts['ok']} warn={counts['warn']} critical={counts['critical']}",
        f"{TAG} project: {result['project']}",
    ]
    latency = result["endpoint_latency_ms"]
    assert isinstance(latency, dict)
    for endpoint_id, stats in latency.items():
        assert isinstance(stats, dict)
        lines.append(
            f"{TAG} 端点 {endpoint_id}: samples={stats['samples']} "
            f"min={_fmt_ms(stats['min'])} p50={_fmt_ms(stats['p50'])} "
            f"p95={_fmt_ms(stats['p95'])} max={_fmt_ms(stats['max'])} (ms)")
    restarts = result["restart_totals"]
    logs = result["log_error_totals"]
    assert isinstance(restarts, dict) and isinstance(logs, dict)
    for service, restart in restarts.items():
        lines.append(f"{TAG} 服务 {service}: restart={restart} "
                     f"log_error={logs[service]}（全窗口总计）")
    lines.append(
        f"{TAG} 边界: 只读查询面——零网络/零子进程/零 env 读取/零文件写入；"
        "输出仅聚合与有界记录投影，绝无原始日志/密钥/secret；零墙钟；"
        "不构成 production readiness 宣称")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monitoring_history_query.py",
        description="M14-108 监控历史时序查询：canonical history.jsonl 只读查询面"
                    "（零网络/零子进程/零文件写入/零墙钟）",
    )
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY_PATH,
                        help=f"history.jsonl 路径（默认 canonical {DEFAULT_HISTORY_PATH}，只读）")
    parser.add_argument("--start", default=None,
                        help="时间窗下界（UTC，含边界，格式 %Y-%m-%dT%H:%M:%SZ）")
    parser.add_argument("--end", default=None,
                        help="时间窗上界（UTC，含边界，格式 %Y-%m-%dT%H:%M:%SZ）")
    parser.add_argument("--status", default=None,
                        help="状态过滤：逗号分隔子集（ok/warn/critical，无重复/空段）")
    parser.add_argument("--service", default=None,
                        help="服务维度选择：逗号分隔子集（六服务画像；切片聚合与记录投影）")
    parser.add_argument("--endpoint", default=None,
                        help="端点维度选择：逗号分隔子集（五端点画像；切片聚合与记录投影）")
    parser.add_argument("--limit", default=str(DEFAULT_LIMIT),
                        help=f"记录列表界 {MIN_LIMIT}-{MAX_LIMIT}（默认 {DEFAULT_LIMIT}，"
                             "保留最新 N 条；聚合恒为全窗口口径；非法值固定词汇拒绝）")
    parser.add_argument("--format", choices=("summary", "json"), default="summary",
                        help="输出形态：summary（默认，人读）或 json（单文档机器可读）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        options = parse_options(args)  # 全参数校验先于任何读取（零读取拒绝）
        result = run_query(store=RealStore(), history_path=args.history,
                           options=options)
    except QueryError as cause:
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
