r"""M14-108 tools/ops/monitoring_history_query.py 契约测试：canonical
history.jsonl 的安全只读时序查询面（零网络/零子进程/零 env 读取/零落盘/
零计划任务/零生产容器面接触——本工具只读输入文件、只写 stdout）。

覆盖（全部 I/O 经真实临时目录或 FakeStore 注入；绝不触碰 canonical 仓库
与真实 .verify 目录）：
- 结构契约：源码零子进程/零网络/零 env/零墙钟/零落盘 token；socket+
  subprocess 双阻断下端到端照常成功且输入目录零新增文件；ops README
  文档化；
- 参数 fail-closed（先于任何读取）：limit 超界（0/501/负数/非整数）、
  status/service/endpoint 过滤词汇外值（未知/重复/空段）、时间窗格式、
  start>end 顺序；
- 输入面与路径防御：history 缺失、目录形态、文件 symlink（真实面）、
  祖先 symlink（FakeStore）一律拒绝；
- 行校验透传（fail-closed，零部分输出）：not-json、schema_version、
  partial=true、缺关键字段、时间乱序、重复行、跨 project 混档、零样本、
  超 5000 样本——拒绝原因固定词汇透传 monitoring_insights 已测语义
  （单一事实源，零重复实现）；
- 查询语义：默认 summary happy path；--format json 机器可读结构
  （query echo/window 计数/状态计数/端点延迟/服务聚合/有界记录投影）；
  时间窗含边界（start==end==样本时间戳命中）；status 过滤；组合过滤；
  limit 截断（返回最新 N、省略更早显式计数、聚合仍全窗口口径）；
  --service/--endpoint 维度选择（聚合切片 + 记录投影切片）；空命中为
  合法零结果（非拒绝）；restart_evaluation 行照常可查；行级未知额外键
  被忽略且投毒标记值绝不进入输出；
- 输出卫生：两次同参数运行 stdout 逐字节相同（零墙钟）；拒绝时 stdout
  绝无 JSON 正文/摘要正文（零部分输出）。
"""
from __future__ import annotations

import importlib.util
import json
import socket
import subprocess as subprocess_module
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ops" / "monitoring_history_query.py"
HISTORY_SCRIPT = REPO_ROOT / "tools" / "ops" / "monitoring_history.py"
OPS_README = REPO_ROOT / "tools" / "ops" / "README.md"

#: 标记值（注入 history 行额外字段，断言绝不进入任何输出）
MARK_TOKEN = "sk-ZXmarker0123456789"

SERVICES = ("postgres", "redis", "minio", "api", "web", "livekit")
ENDPOINTS = ("web-root", "web-login", "api-health", "funasr-health", "cosyvoice-health")
PROJECT = "aios-m14-03-production-rehearsal"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


mhq = _load_module(SCRIPT, "monitoring_history_query_under_test")
mh = _load_module(HISTORY_SCRIPT, "monitoring_history_for_query_factory")


# ---------------------------------------------------------------- 样本工厂


def stem_for(iso: str) -> str:
    date, clock = iso.rstrip("Z").split("T")
    return "monitor-" + date.replace("-", "") + "-" + clock.replace(":", "")


def _spread(value_or_map: object, keys: tuple[str, ...], default: object) -> dict[str, object]:
    if isinstance(value_or_map, dict):
        return {key: value_or_map.get(key, default) for key in keys}
    return {key: value_or_map for key in keys}


def _row(iso: str, *, project: str = PROJECT, overall: str = "ok", partial: bool = False,
         stem: str | None = None, schema_version: int = 1,
         counts: dict[str, int] | None = None,
         health: object = "healthy", restarts: object = 0, log_errors: object = 0,
         http_status: object = 200, latency_ms: object = 10.0,
         drop: str | None = None, extra: dict[str, object] | None = None
         ) -> dict[str, object]:
    """canonical 形状的 M14-13 history 记录（与 monitoring_history.build_record 同构）。"""
    statuses = _spread(http_status, ENDPOINTS, 200)
    latencies = _spread(latency_ms, ENDPOINTS, 10.0)
    row: dict[str, object] = {
        "schema_version": schema_version,
        "artifact_sha256": _sha_for(stem if stem is not None else stem_for(iso)),
        "source_stem": stem if stem is not None else stem_for(iso),
        "collected_at": iso,
        "project": project,
        "overall_status": overall,
        "partial": partial,
        "threshold_counts": counts if counts is not None else {"ok": 34, "warn": 0, "critical": 0},
        "compose_service_health": _spread(health, SERVICES, "healthy"),
        "restart_counts": _spread(restarts, SERVICES, 0),
        "endpoints": {e: {"http_status": statuses[e], "latency_ms": latencies[e]}
                      for e in ENDPOINTS},
        "log_error_totals": _spread(log_errors, SERVICES, 0),
    }
    if drop is not None:
        row.pop(drop, None)
    if extra is not None:
        row.update(extra)
    return row


def _sha_for(token: str) -> str:
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _write_history(directory: Path, rows: list[dict[str, object]],
                   name: str = "history.jsonl", *, text: str | None = None) -> Path:
    content = text if text is not None else "".join(
        json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return path


def _restart_eval_record(*, baseline_status: str = "ok",
                         baseline_reason: str | None = None,
                         source_stem: str | None = "monitor-20260911-160000",
                         collected: str | None = "2026-09-11T16:00:00Z",
                         invalid_skipped: int = 0,
                         states: dict[str, str] | None = None,
                         reasons: dict[str, str] | None = None,
                         deltas: dict[str, int | None] | None = None) -> dict[str, object]:
    """M14-13 记录形态的 restart_evaluation（与 monitoring_history.build_record 同构）。"""
    return {
        "baseline_status": baseline_status,
        "baseline_reason": baseline_reason,
        "baseline_source_stem": source_stem,
        "baseline_collected_at": collected,
        "baseline_invalid_skipped_count": invalid_skipped,
        "states": {s: (states or {}).get(s, "ok") for s in SERVICES},
        "reasons": {s: (reasons or {}).get(s, "stable") for s in SERVICES},
        "deltas": {s: (deltas or {}).get(s, 0) for s in SERVICES},
    }


def _three_rows() -> list[dict[str, object]]:
    return [
        _row("2026-09-11T10:00:00Z", overall="ok",
             latency_ms={"api-health": 12.0}, restarts={"redis": 1}, log_errors={"api": 2}),
        _row("2026-09-11T10:05:00Z", overall="warn",
             latency_ms={"api-health": 20.0}),
        _row("2026-09-11T10:10:00Z", overall="critical",
             latency_ms={"api-health": 40.0}, log_errors={"api": 3}),
    ]


def _run_cli(history: Path, *extra: str) -> int:
    return mhq.main(["--history", str(history), *extra])


def _block_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("subprocess is forbidden in this test")

    monkeypatch.setattr(subprocess_module, "run", _forbidden)

    def _no_socket(*args: object, **kwargs: object) -> None:
        raise AssertionError("network is forbidden in this test")

    monkeypatch.setattr(socket, "socket", _no_socket)


# ---------------------------------------------------------------- FakeStore（只读）


class FakeStore:
    """伪文件面：basename 键控，只读四方法——本工具的 Store 协议无写面。"""

    def __init__(self, *, files: dict[str, bytes] | None = None,
                 dirs: tuple[str, ...] = (), symlinks: tuple[str, ...] = ()) -> None:
        self.files: dict[str, bytes] = dict(files or {})
        self.dirs: set[str] = set(dirs)
        self.symlinks: set[str] = set(symlinks)

    def exists(self, path: Path) -> bool:
        return path.name in self.files or path.name in self.dirs or path.name in self.symlinks

    def is_dir(self, path: Path) -> bool:
        return path.name in self.dirs

    def is_symlink(self, path: Path) -> bool:
        return path.name in self.symlinks

    def read_bytes(self, path: Path) -> bytes:
        return self.files[path.name]


def _fake_run(monkeypatch: pytest.MonkeyPatch, store: FakeStore, history: Path,
              *extra: str) -> int:
    monkeypatch.setattr(mhq, "RealStore", lambda: store)
    return mhq.main(["--history", str(history), *extra])


# ---------------------------------------------------------------- 结构契约


def test_source_contract_forbidden_tokens() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for token in ("subprocess", "socket", "urllib", "http.client", "os.system",
                  "Popen", "environ", "requests", "urlopen", "getenv", "docker",
                  "datetime.now", "utcnow", "time.time"):
        assert token not in source, f"禁止出现的字面量: {token}"
    # 零落盘面：只读查询工具绝不写任何文件
    for token in ("os.replace", "write_atomic", "mkdirs", "def write"):
        assert token not in source, f"只读工具禁止出现的写面 token: {token}"


def test_no_subprocess_no_network_end_to_end_and_zero_writes(
        monkeypatch, tmp_path, capsys) -> None:
    _block_side_effects(monkeypatch)
    history = _write_history(tmp_path, _three_rows())
    before = sorted(p.name for p in tmp_path.iterdir())
    rc = _run_cli(history)
    out = capsys.readouterr().out
    assert rc == mhq.EXIT_OK
    assert "[query] 状态: ok=1 warn=1 critical=1" in out
    assert sorted(p.name for p in tmp_path.iterdir()) == before  # 零新增文件


def test_ops_readme_documents_tool() -> None:
    text = OPS_README.read_text(encoding="utf-8")
    assert "monitoring_history_query.py" in text


def test_reuses_history_and_insights_single_source_of_truth() -> None:
    """常量复用单一事实源：六服务/五端点/schema 版本/退出码/默认输入。"""
    assert mhq.STACK_SERVICES == mh.STACK_SERVICES == SERVICES
    assert mhq.ENDPOINT_IDS == mh.ENDPOINT_IDS == ENDPOINTS
    assert mhq.HISTORY_SCHEMA_VERSION == mh.HISTORY_SCHEMA_VERSION == 1
    assert mhq.EXIT_OK == 0 and mhq.EXIT_REFUSED == 2
    assert mhq.DEFAULT_HISTORY_PATH == mh.DEFAULT_OUTPUT_DIR / mh.HISTORY_OUTPUT_NAME
    assert mhq.parse_history_text is not None  # 委托 insights 已测校验面


# ---------------------------------------------------------------- 参数 fail-closed（零读取）


@pytest.mark.parametrize("bad_limit", ["0", "501", "-1", "abc"])
def test_limit_out_of_bounds_refused(monkeypatch, tmp_path, bad_limit: str) -> None:
    def _no_store() -> None:
        raise AssertionError("参数拒绝路径必须零读取（RealStore 不得构造）")

    monkeypatch.setattr(mhq, "RealStore", _no_store)
    history = _write_history(tmp_path, _three_rows())
    assert _run_cli(history, "--limit", bad_limit) == mhq.EXIT_REFUSED


@pytest.mark.parametrize("bad_status", ["bogus", "ok,ok", "ok,,warn", ",", "OK"])
def test_status_filter_vocabulary_refused(monkeypatch, tmp_path, bad_status: str) -> None:
    def _no_store() -> None:
        raise AssertionError("参数拒绝路径必须零读取（RealStore 不得构造）")

    monkeypatch.setattr(mhq, "RealStore", _no_store)
    history = _write_history(tmp_path, _three_rows())
    assert _run_cli(history, "--status", bad_status) == mhq.EXIT_REFUSED


@pytest.mark.parametrize("bad_service", ["bogus", "redis,redis", "redis,,api", "Postgres"])
def test_service_filter_vocabulary_refused(monkeypatch, tmp_path, bad_service: str) -> None:
    def _no_store() -> None:
        raise AssertionError("参数拒绝路径必须零读取（RealStore 不得构造）")

    monkeypatch.setattr(mhq, "RealStore", _no_store)
    history = _write_history(tmp_path, _three_rows())
    assert _run_cli(history, "--service", bad_service) == mhq.EXIT_REFUSED


@pytest.mark.parametrize("bad_endpoint", ["bogus", "api-health,api-health", "api-health,,"])
def test_endpoint_filter_vocabulary_refused(monkeypatch, tmp_path, bad_endpoint: str) -> None:
    def _no_store() -> None:
        raise AssertionError("参数拒绝路径必须零读取（RealStore 不得构造）")

    monkeypatch.setattr(mhq, "RealStore", _no_store)
    history = _write_history(tmp_path, _three_rows())
    assert _run_cli(history, "--endpoint", bad_endpoint) == mhq.EXIT_REFUSED


@pytest.mark.parametrize("bad_time", [
    "2026-09-11 10:00:00", "not-a-time", "2026-09-11T10:00:00+00:00", "2026-09-11T10:00:00",
    "2026-13-01T00:00:00Z",
])
def test_time_window_format_refused(monkeypatch, tmp_path, bad_time: str) -> None:
    def _no_store() -> None:
        raise AssertionError("参数拒绝路径必须零读取（RealStore 不得构造）")

    monkeypatch.setattr(mhq, "RealStore", _no_store)
    history = _write_history(tmp_path, _three_rows())
    assert _run_cli(history, "--start", bad_time) == mhq.EXIT_REFUSED
    assert _run_cli(history, "--end", bad_time) == mhq.EXIT_REFUSED


def test_time_window_order_refused(monkeypatch, tmp_path) -> None:
    def _no_store() -> None:
        raise AssertionError("参数拒绝路径必须零读取（RealStore 不得构造）")

    monkeypatch.setattr(mhq, "RealStore", _no_store)
    history = _write_history(tmp_path, _three_rows())
    assert _run_cli(history, "--start", "2026-09-11T10:10:00Z",
                    "--end", "2026-09-11T10:00:00Z") == mhq.EXIT_REFUSED


# ---------------------------------------------------------------- 输入面与路径防御


def test_history_missing_refused(tmp_path, capsys) -> None:
    assert _run_cli(tmp_path / "history.jsonl") == mhq.EXIT_REFUSED
    assert "拒绝" in capsys.readouterr().out


def test_history_dir_refused(tmp_path) -> None:
    assert _run_cli(tmp_path) == mhq.EXIT_REFUSED


def test_symlinked_history_file_refused_real_fs(tmp_path, capsys) -> None:
    target = tmp_path / "real.jsonl"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "history.jsonl"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink 不可用（权限/平台）")
    assert _run_cli(link) == mhq.EXIT_REFUSED
    out = capsys.readouterr().out
    assert "symlink" in out
    assert target.read_text(encoding="utf-8") == "{}"  # 目标零读取副作用之外零改动


def test_symlinked_ancestor_refused_via_fake_store(monkeypatch) -> None:
    rows = _three_rows()
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    store = FakeStore(files={"history.jsonl": text.encode("utf-8")},
                      symlinks=("evidence",))
    assert _fake_run(monkeypatch, store, Path("evidence") / "history.jsonl"
                     ) == mhq.EXIT_REFUSED


def test_symlinked_history_file_refused_via_fake_store(monkeypatch) -> None:
    rows = _three_rows()
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    store = FakeStore(files={"history.jsonl": text.encode("utf-8")},
                      symlinks=("history.jsonl",))
    assert _fake_run(monkeypatch, store, Path("history.jsonl")) == mhq.EXIT_REFUSED


# ---------------------------------------------------------------- 行校验拒绝（透传单一事实源语义）


def _refused_payload_rows() -> list[tuple[str, object]]:
    return [
        ("not-json", "definitely not json {"),
        ("schema-version", _row("2026-09-11T10:00:00Z", schema_version=2)),
        ("partial", _row("2026-09-11T10:00:00Z", partial=True)),
        ("overall-status", _row("2026-09-11T10:00:00Z", overall="incomplete")),
        ("endpoint-facts", {k: v for k, v in _row("2026-09-11T10:00:00Z").items()
                            if k != "endpoints"}),
        ("restart-counts", _row("2026-09-11T10:00:00Z",
                                restarts={"redis": -1})),
        ("history-row", [1, 2, 3]),
    ]


@pytest.mark.parametrize("reason,payload", _refused_payload_rows())
def test_row_validation_refused_zero_output(tmp_path, capsys, reason: str,
                                            payload: object) -> None:
    line = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    history = _write_history(tmp_path, [], text=line + "\n")
    rc = _run_cli(history, "--format", "json")
    out = capsys.readouterr().out
    assert rc == mhq.EXIT_REFUSED
    assert reason in out  # 固定词汇拒绝原因透传（不回显被拒内容）
    assert '"schema_version"' not in out  # 零部分输出


def test_out_of_order_rows_refused(tmp_path, capsys) -> None:
    rows = _three_rows()
    history = _write_history(tmp_path, [rows[1], rows[0], rows[2]])
    assert _run_cli(history) == mhq.EXIT_REFUSED
    assert "history-order" in capsys.readouterr().out


def test_duplicate_row_refused(tmp_path) -> None:
    rows = _three_rows()
    history = _write_history(tmp_path, [rows[0], rows[0]])
    assert _run_cli(history) == mhq.EXIT_REFUSED


def test_equal_timestamp_descending_stems_refused(tmp_path) -> None:
    history = _write_history(tmp_path, [
        _row("2026-09-11T10:00:00Z", stem="monitor-20260911-100002"),
        _row("2026-09-11T10:00:00Z", stem="monitor-20260911-100001"),
    ])
    assert _run_cli(history) == mhq.EXIT_REFUSED


def test_mixed_project_refused(tmp_path, capsys) -> None:
    history = _write_history(tmp_path, [
        _row("2026-09-11T10:00:00Z"),
        _row("2026-09-11T10:05:00Z", project="another-project"),
    ])
    assert _run_cli(history) == mhq.EXIT_REFUSED
    assert "mixed-project" in capsys.readouterr().out


def test_zero_samples_refused(tmp_path) -> None:
    history = _write_history(tmp_path, [], text="")
    assert _run_cli(history) == mhq.EXIT_REFUSED


def test_too_many_samples_refused(tmp_path) -> None:
    base = datetime(2026, 9, 11, 0, 0, 0, tzinfo=timezone.utc)
    rows = [_row((base + timedelta(seconds=i)).strftime("%Y-%m-%dT%H:%M:%SZ"))
            for i in range(5001)]
    history = _write_history(tmp_path, rows)
    assert _run_cli(history) == mhq.EXIT_REFUSED


# ---------------------------------------------------------------- 查询语义（happy / 过滤 / limit / 维度）


def test_default_summary_happy_path(tmp_path, capsys) -> None:
    history = _write_history(tmp_path, _three_rows())
    rc = _run_cli(history)
    out = capsys.readouterr().out
    assert rc == mhq.EXIT_OK
    assert "[query] 输入: history.jsonl" in out
    assert "[query] 窗口: 扫描 3 / 匹配 3 / 返回 3" in out
    assert "[query] 状态: ok=1 warn=1 critical=1" in out
    assert "2026-09-11T10:00:00Z" in out and "2026-09-11T10:10:00Z" in out
    assert "不构成 production readiness 宣称" in out


def test_json_format_structure(tmp_path, capsys) -> None:
    history = _write_history(tmp_path, _three_rows())
    rc = _run_cli(history, "--format", "json")
    out = capsys.readouterr().out
    assert rc == mhq.EXIT_OK
    payload = json.loads(out)  # json 模式 stdout 为单一 JSON 文档
    assert set(payload) == {"schema_version", "tool", "project", "query", "window",
                            "status_counts", "endpoint_latency_ms", "restart_totals",
                            "log_error_totals", "records"}
    assert payload["schema_version"] == 1
    assert payload["project"] == PROJECT
    assert payload["status_counts"] == {"ok": 1, "warn": 1, "critical": 1}
    window = payload["window"]
    assert isinstance(window, dict)
    assert window["records_scanned"] == 3
    assert window["records_matched"] == 3
    assert window["records_returned"] == 3
    assert window["truncated_older_count"] == 0
    assert window["oldest_matched_at"] == "2026-09-11T10:00:00Z"
    assert window["newest_matched_at"] == "2026-09-11T10:10:00Z"
    query = payload["query"]
    assert isinstance(query, dict)
    assert query["history_file"] == "history.jsonl"  # 纯名——绝无绝对路径
    assert query["start"] is None and query["end"] is None
    assert query["statuses"] is None
    assert query["services"] == list(SERVICES)
    assert query["endpoints"] == list(ENDPOINTS)
    assert query["limit"] == 50
    latency = payload["endpoint_latency_ms"]
    assert isinstance(latency, dict)
    assert set(latency) == set(ENDPOINTS)
    api_health = latency["api-health"]
    assert isinstance(api_health, dict)
    assert api_health["samples"] == 3
    assert api_health["min"] == pytest.approx(12.0)
    assert api_health["p50"] == pytest.approx(20.0)  # nearest-rank
    assert api_health["max"] == pytest.approx(40.0)
    restarts = payload["restart_totals"]
    assert isinstance(restarts, dict)
    assert set(restarts) == set(SERVICES)
    assert restarts["redis"] == 1
    logs = payload["log_error_totals"]
    assert isinstance(logs, dict)
    assert logs["api"] == 5
    records = payload["records"]
    assert isinstance(records, list)
    assert [r["collected_at"] for r in records] == [
        "2026-09-11T10:00:00Z", "2026-09-11T10:05:00Z", "2026-09-11T10:10:00Z"]
    for record in records:
        assert set(record) == {"source_stem", "collected_at", "overall_status",
                               "threshold_counts", "compose_service_health",
                               "restart_counts", "log_error_totals", "endpoints"}


def test_time_window_inclusive_boundaries(tmp_path, capsys) -> None:
    history = _write_history(tmp_path, _three_rows())
    rc = _run_cli(history, "--format", "json",
                  "--start", "2026-09-11T10:00:00Z", "--end", "2026-09-11T10:05:00Z")
    payload = json.loads(capsys.readouterr().out)
    assert rc == mhq.EXIT_OK
    assert payload["window"]["records_matched"] == 2  # start/end 均含边界
    assert [r["collected_at"] for r in payload["records"]] == [
        "2026-09-11T10:00:00Z", "2026-09-11T10:05:00Z"]


def test_time_window_excludes_outside(tmp_path, capsys) -> None:
    history = _write_history(tmp_path, _three_rows())
    rc = _run_cli(history, "--format", "json",
                  "--start", "2026-09-11T10:00:01Z", "--end", "2026-09-11T10:09:59Z")
    payload = json.loads(capsys.readouterr().out)
    assert rc == mhq.EXIT_OK
    assert payload["window"]["records_matched"] == 1
    assert payload["records"][0]["collected_at"] == "2026-09-11T10:05:00Z"


def test_status_filter(tmp_path, capsys) -> None:
    history = _write_history(tmp_path, _three_rows())
    rc = _run_cli(history, "--format", "json", "--status", "warn,critical")
    payload = json.loads(capsys.readouterr().out)
    assert rc == mhq.EXIT_OK
    assert payload["window"]["records_matched"] == 2
    assert {r["overall_status"] for r in payload["records"]} == {"warn", "critical"}
    assert payload["status_counts"] == {"ok": 0, "warn": 1, "critical": 1}


def test_combined_filters(tmp_path, capsys) -> None:
    history = _write_history(tmp_path, _three_rows())
    rc = _run_cli(history, "--format", "json", "--status", "ok,warn",
                  "--start", "2026-09-11T10:01:00Z", "--end", "2026-09-11T10:10:00Z",
                  "--service", "redis", "--endpoint", "api-health")
    payload = json.loads(capsys.readouterr().out)
    assert rc == mhq.EXIT_OK
    assert payload["window"]["records_matched"] == 1  # 仅 10:05 warn 命中
    assert payload["query"]["statuses"] == ["ok", "warn"]
    assert payload["query"]["services"] == ["redis"]
    assert payload["query"]["endpoints"] == ["api-health"]
    assert set(payload["restart_totals"]) == {"redis"}
    assert set(payload["endpoint_latency_ms"]) == {"api-health"}
    assert set(payload["records"][0]["restart_counts"]) == {"redis"}
    assert set(payload["records"][0]["endpoints"]) == {"api-health"}


def _seven_rows() -> list[dict[str, object]]:
    base = datetime(2026, 9, 11, 11, 0, 0, tzinfo=timezone.utc)
    return [_row((base + timedelta(minutes=5 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 log_errors={"api": i}) for i in range(7)]


def test_limit_truncates_keeping_newest(tmp_path, capsys) -> None:
    rows = _seven_rows()
    history = _write_history(tmp_path, rows)
    rc = _run_cli(history, "--format", "json", "--limit", "3")
    payload = json.loads(capsys.readouterr().out)
    assert rc == mhq.EXIT_OK
    window = payload["window"]
    assert window["records_matched"] == 7
    assert window["records_returned"] == 3
    assert window["truncated_older_count"] == 4
    collected = [r["collected_at"] for r in payload["records"]]
    assert collected[-1] == "2026-09-11T11:30:00Z"  # 最新一条保留
    assert len(collected) == 3
    # 聚合仍为全窗口口径（7 样本），limit 只界记录列表
    assert payload["endpoint_latency_ms"]["api-health"]["samples"] == 7
    assert payload["log_error_totals"]["api"] == sum(range(7))


def test_empty_match_is_valid_zero_result(tmp_path, capsys) -> None:
    history = _write_history(tmp_path, _three_rows())
    rc = _run_cli(history, "--format", "json",
                  "--start", "2026-09-12T00:00:00Z", "--end", "2026-09-13T00:00:00Z")
    payload = json.loads(capsys.readouterr().out)
    assert rc == mhq.EXIT_OK  # 空命中是合法查询结果（非拒绝）
    assert payload["window"]["records_matched"] == 0
    assert payload["window"]["oldest_matched_at"] is None
    assert payload["records"] == []
    assert payload["status_counts"] == {"ok": 0, "warn": 0, "critical": 0}
    api_health = payload["endpoint_latency_ms"]["api-health"]
    assert api_health["samples"] == 0
    assert api_health["min"] is None and api_health["p95"] is None


def test_restart_evaluation_row_queryable(tmp_path, capsys) -> None:
    rows = [
        _row("2026-09-11T17:00:00Z"),
        _row("2026-09-11T17:15:00Z", extra={
            "restart_evaluation": _restart_eval_record(
                states={"api": "warn"}, reasons={"api": "delta"}, deltas={"api": 2})}),
    ]
    history = _write_history(tmp_path, rows)
    rc = _run_cli(history, "--format", "json")
    payload = json.loads(capsys.readouterr().out)
    assert rc == mhq.EXIT_OK
    assert payload["window"]["records_matched"] == 2  # M14-23 行照常可查


def test_restart_evaluation_malformed_row_refused(tmp_path) -> None:
    rows = [
        _row("2026-09-11T17:00:00Z"),
        _row("2026-09-11T17:15:00Z", extra={
            "restart_evaluation": _restart_eval_record(
                baseline_status="missing", baseline_reason="bogus-reason")}),
    ]
    history = _write_history(tmp_path, rows)
    assert _run_cli(history) == mhq.EXIT_REFUSED


def test_extras_ignored_and_marker_never_leaks(tmp_path, capsys) -> None:
    rows = [_row("2026-09-11T10:00:00Z", extra={"poison": MARK_TOKEN,
                                                "raw_log": MARK_TOKEN})]
    history = _write_history(tmp_path, rows)
    rc = _run_cli(history, "--format", "json")
    out = capsys.readouterr().out
    assert rc == mhq.EXIT_OK
    assert MARK_TOKEN not in out


# ---------------------------------------------------------------- 输出卫生


def test_zero_wall_clock_byte_reproducible(tmp_path, capsys) -> None:
    history = _write_history(tmp_path, _three_rows())
    _run_cli(history, "--format", "json")
    first = capsys.readouterr().out
    _run_cli(history, "--format", "json")
    second = capsys.readouterr().out
    assert first == second  # 零墙钟：两次运行 stdout 逐字节相同


def test_refusal_emits_no_json_body(tmp_path, capsys) -> None:
    history = _write_history(tmp_path, [], text="oops not json\n")
    rc = _run_cli(history, "--format", "json")
    out = capsys.readouterr().out
    assert rc == mhq.EXIT_REFUSED
    assert '"schema_version"' not in out
    assert "[query] 窗口" not in out  # 摘要正文零输出
