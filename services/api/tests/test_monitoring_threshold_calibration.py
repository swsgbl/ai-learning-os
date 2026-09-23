r"""M14-109 tools/ops/monitoring_threshold_calibration.py 契约测试：canonical
history.jsonl 的离线只读监控阈值标定/评估面（零网络/零子进程/零 env 读取/
零落盘/零计划任务/零生产容器面接触——本工具只读输入文件、只写 stdout）。

覆盖（全部 I/O 经真实临时目录或 FakeStore 注入；绝不触碰 canonical 仓库
与真实 .verify 目录）：
- 结构契约：源码零子进程/零网络/零 env/零墙钟/零落盘 token；socket+
  subprocess 双阻断下端到端照常成功且输入目录零新增文件；ops README
  文档化；单一事实源常量复用（monitoring_history 画像 + insights 行级
  校验委托 + production_monitor 既有阈值基准）；
- 参数 fail-closed（先于任何读取）：samples 超界（0/5001/负数/非整数）、
  latency 阈值非法（低于/高于配置界、warn>=critical、nan/inf、非数值）、
  log-error 阈值非法（负数/超界/warn>=critical/非整数）；
- 输入面与路径防御：history 缺失、目录形态、文件 symlink（真实面）、
  祖先 symlink（FakeStore）一律拒绝；
- 行校验透传（fail-closed，零部分输出）：not-json、schema_version、
  partial=true、缺关键字段、时间乱序、重复行、跨 project 混档、零样本、
  超 5000 样本——拒绝原因固定词汇透传 monitoring_insights 已测语义；
- 标定语义：nearest-rank 分位数精确值；建议规则（warn=p95/critical=p99）
  与 applicable 判定（degenerate-window/below-minimum/above-maximum）；
  评估语义（>= 含边界告警/覆盖率/告警率/critical 率/
  ok_status_conflict——阈值评估结果与历史 overall_status==ok 的代理分歧，
  不是误报率/真值标注；分母 = 窗口样本总数）；默认评估配置 = 既有
  production_monitor 阈值（source=existing-defaults）；显式候选评估
  （source=explicit）；样本窗口取最新 N 条（全文件校验先行、截断计数
  显式、窗口大于文件时如实用全部）；建议阈值评估仅在 applicable 时在场；
  restart 累计计数绝不进入标定面；
- 输出卫生：两次同参数运行 stdout 逐字节相同（零墙钟）；拒绝时 stdout
  绝无 JSON 正文/摘要正文（零部分输出）；投毒标记值绝不进入输出；
  summary 与 json 双形态均含边界注记。
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
SCRIPT = REPO_ROOT / "tools" / "ops" / "monitoring_threshold_calibration.py"
HISTORY_SCRIPT = REPO_ROOT / "tools" / "ops" / "monitoring_history.py"
MONITOR_SCRIPT = REPO_ROOT / "tools" / "ops" / "production_monitor.py"
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


mtc = _load_module(SCRIPT, "monitoring_threshold_calibration_under_test")
mh = _load_module(HISTORY_SCRIPT, "monitoring_history_for_calibration_factory")
pm = _load_module(MONITOR_SCRIPT, "production_monitor_for_calibration_baseline")


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
        "threshold_counts": {"ok": 34, "warn": 0, "critical": 0},
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


def _iso(base: datetime, index: int, *, minutes: int = 5) -> str:
    return (base + timedelta(minutes=minutes * index)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _three_rows() -> list[dict[str, object]]:
    return [
        _row("2026-09-11T10:00:00Z", overall="ok",
             latency_ms={"api-health": 60.0}, log_errors={"api": 0}),
        _row("2026-09-11T10:05:00Z", overall="warn",
             latency_ms={"api-health": 100.0}, log_errors={"api": 4}),
        _row("2026-09-11T10:10:00Z", overall="ok",
             latency_ms={"api-health": 200.0}, log_errors={"api": 9}),
    ]


def _run_cli(history: Path, *extra: str) -> int:
    return mtc.main(["--history", str(history), *extra])


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
    monkeypatch.setattr(mtc, "RealStore", lambda: store)
    return mtc.main(["--history", str(history), *extra])


# ---------------------------------------------------------------- 结构契约


def test_source_contract_forbidden_tokens() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for token in ("subprocess", "socket", "urllib", "http.client", "os.system",
                  "Popen", "environ", "requests", "urlopen", "getenv", "docker",
                  "datetime.now", "utcnow", "time.time"):
        assert token not in source, f"禁止出现的字面量: {token}"
    # 零落盘面：只读标定工具绝不写任何文件
    for token in ("os.replace", "write_atomic", "mkdirs", "def write"):
        assert token not in source, f"只读工具禁止出现的写面 token: {token}"


def test_no_subprocess_no_network_end_to_end_and_zero_writes(
        monkeypatch, tmp_path, capsys) -> None:
    _block_side_effects(monkeypatch)
    history = _write_history(tmp_path, _three_rows())
    before = sorted(p.name for p in tmp_path.iterdir())
    rc = _run_cli(history)
    out = capsys.readouterr().out
    assert rc == mtc.EXIT_OK
    assert "[calibrate] 窗口: 扫描 3 / 使用 3" in out
    assert sorted(p.name for p in tmp_path.iterdir()) == before  # 零新增文件


def test_ops_readme_documents_tool() -> None:
    text = OPS_README.read_text(encoding="utf-8")
    assert "monitoring_threshold_calibration.py" in text


def test_single_source_of_truth_reuse() -> None:
    """常量复用单一事实源：画像/schema/输入面 = monitoring_history 系；
    既有阈值基准与配置界 = production_monitor（仅常量导入）。"""
    assert mtc.STACK_SERVICES == mh.STACK_SERVICES == SERVICES
    assert mtc.ENDPOINT_IDS == mh.ENDPOINT_IDS == ENDPOINTS
    assert mtc.HISTORY_SCHEMA_VERSION == mh.HISTORY_SCHEMA_VERSION == 1
    assert mtc.EXIT_OK == 0 and mtc.EXIT_REFUSED == 2
    assert mtc.DEFAULT_HISTORY_PATH == mh.DEFAULT_OUTPUT_DIR / mh.HISTORY_OUTPUT_NAME
    assert mtc.parse_history_text is not None  # 委托 insights 已测校验面
    assert mtc.EXISTING_LATENCY_WARN_MS == pm.DEFAULT_LATENCY_WARN_MS
    assert mtc.EXISTING_LATENCY_CRITICAL_MS == pm.DEFAULT_LATENCY_CRITICAL_MS
    assert mtc.EXISTING_LOG_ERROR_WARN == pm.DEFAULT_LOG_ERROR_WARN
    assert mtc.EXISTING_LOG_ERROR_CRITICAL == pm.DEFAULT_LOG_ERROR_CRITICAL
    assert mtc.LATENCY_MIN_MS == pm.MIN_LATENCY_MS
    assert mtc.LATENCY_MAX_MS == pm.MAX_LATENCY_MS
    assert mtc.LOG_ERROR_MIN == pm.MIN_LOG_ERROR_THRESHOLD
    assert mtc.LOG_ERROR_MAX == pm.MAX_LOG_ERROR_THRESHOLD


# ---------------------------------------------------------------- 参数 fail-closed（零读取）


def _forbid_store(monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_store() -> None:
        raise AssertionError("参数拒绝路径必须零读取（RealStore 不得构造）")

    monkeypatch.setattr(mtc, "RealStore", _no_store)


@pytest.mark.parametrize("bad_samples", ["0", "5001", "-1", "abc", "2.5"])
def test_samples_out_of_bounds_refused(monkeypatch, tmp_path, bad_samples: str) -> None:
    _forbid_store(monkeypatch)
    history = _write_history(tmp_path, _three_rows())
    assert _run_cli(history, "--samples", bad_samples) == mtc.EXIT_REFUSED


@pytest.mark.parametrize("bad_latency", [
    str(float(pm.MIN_LATENCY_MS) - 0.1),       # 低于配置下界
    str(float(pm.MAX_LATENCY_MS) + 1),         # 高于配置上界
    "nan", "inf", "abc",                       # 非有限/非数值
])
def test_latency_threshold_bounds_refused(monkeypatch, tmp_path, bad_latency: str) -> None:
    _forbid_store(monkeypatch)
    history = _write_history(tmp_path, _three_rows())
    assert _run_cli(history, "--latency-warn-ms", bad_latency
                    ) == mtc.EXIT_REFUSED
    assert _run_cli(history, "--latency-critical-ms", bad_latency
                    ) == mtc.EXIT_REFUSED


@pytest.mark.parametrize("warn,critical", [
    ("5000", "1000"),    # warn >= critical（倒序）
    ("1000", "1000"),    # 相等（须严格小于）
])
def test_latency_threshold_order_refused(monkeypatch, tmp_path, warn: str,
                                         critical: str) -> None:
    _forbid_store(monkeypatch)
    history = _write_history(tmp_path, _three_rows())
    assert _run_cli(history, "--latency-warn-ms", warn,
                    "--latency-critical-ms", critical) == mtc.EXIT_REFUSED


@pytest.mark.parametrize("bad_log", ["-1", str(pm.MAX_LOG_ERROR_THRESHOLD + 1), "abc", "2.5"])
def test_log_error_threshold_bounds_refused(monkeypatch, tmp_path, bad_log: str) -> None:
    _forbid_store(monkeypatch)
    history = _write_history(tmp_path, _three_rows())
    assert _run_cli(history, "--log-error-warn", bad_log) == mtc.EXIT_REFUSED
    assert _run_cli(history, "--log-error-critical", bad_log) == mtc.EXIT_REFUSED


def test_log_error_threshold_order_refused(monkeypatch, tmp_path) -> None:
    _forbid_store(monkeypatch)
    history = _write_history(tmp_path, _three_rows())
    assert _run_cli(history, "--log-error-warn", "20",
                    "--log-error-critical", "5") == mtc.EXIT_REFUSED
    assert _run_cli(history, "--log-error-warn", "5",
                    "--log-error-critical", "5") == mtc.EXIT_REFUSED


# ---------------------------------------------------------------- 输入面与路径防御


def test_history_missing_refused(tmp_path, capsys) -> None:
    assert _run_cli(tmp_path / "history.jsonl") == mtc.EXIT_REFUSED
    assert "拒绝" in capsys.readouterr().out


def test_history_dir_refused(tmp_path) -> None:
    assert _run_cli(tmp_path) == mtc.EXIT_REFUSED


def test_symlinked_history_file_refused_real_fs(tmp_path, capsys) -> None:
    target = tmp_path / "real.jsonl"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "history.jsonl"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink 不可用（权限/平台）")
    assert _run_cli(link) == mtc.EXIT_REFUSED
    out = capsys.readouterr().out
    assert "symlink" in out
    assert target.read_text(encoding="utf-8") == "{}"  # 目标零改动


def test_symlinked_ancestor_refused_via_fake_store(monkeypatch) -> None:
    rows = _three_rows()
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    store = FakeStore(files={"history.jsonl": text.encode("utf-8")},
                      symlinks=("evidence",))
    assert _fake_run(monkeypatch, store, Path("evidence") / "history.jsonl"
                     ) == mtc.EXIT_REFUSED


def test_symlinked_history_file_refused_via_fake_store(monkeypatch) -> None:
    rows = _three_rows()
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    store = FakeStore(files={"history.jsonl": text.encode("utf-8")},
                      symlinks=("history.jsonl",))
    assert _fake_run(monkeypatch, store, Path("history.jsonl")) == mtc.EXIT_REFUSED


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
    assert rc == mtc.EXIT_REFUSED
    assert reason in out  # 固定词汇拒绝原因透传（不回显被拒内容）
    assert '"schema_version"' not in out  # 零部分输出


def test_out_of_order_rows_refused(tmp_path, capsys) -> None:
    rows = _three_rows()
    history = _write_history(tmp_path, [rows[1], rows[0], rows[2]])
    assert _run_cli(history) == mtc.EXIT_REFUSED
    assert "history-order" in capsys.readouterr().out


def test_duplicate_row_refused(tmp_path) -> None:
    rows = _three_rows()
    history = _write_history(tmp_path, [rows[0], rows[0]])
    assert _run_cli(history) == mtc.EXIT_REFUSED


def test_equal_timestamp_descending_stems_refused(tmp_path) -> None:
    history = _write_history(tmp_path, [
        _row("2026-09-11T10:00:00Z", stem="monitor-20260911-100002"),
        _row("2026-09-11T10:00:00Z", stem="monitor-20260911-100001"),
    ])
    assert _run_cli(history) == mtc.EXIT_REFUSED


def test_mixed_project_refused(tmp_path, capsys) -> None:
    history = _write_history(tmp_path, [
        _row("2026-09-11T10:00:00Z"),
        _row("2026-09-11T10:05:00Z", project="another-project"),
    ])
    assert _run_cli(history) == mtc.EXIT_REFUSED
    assert "mixed-project" in capsys.readouterr().out


def test_zero_samples_refused(tmp_path) -> None:
    history = _write_history(tmp_path, [], text="")
    assert _run_cli(history) == mtc.EXIT_REFUSED


def test_too_many_samples_refused(tmp_path) -> None:
    base = datetime(2026, 9, 11, 0, 0, 0, tzinfo=timezone.utc)
    rows = [_row((base + timedelta(seconds=i)).strftime("%Y-%m-%dT%H:%M:%SZ"))
            for i in range(5001)]
    history = _write_history(tmp_path, rows)
    assert _run_cli(history) == mtc.EXIT_REFUSED


# ---------------------------------------------------------------- 标定语义（分布 / 建议 / 评估 / 窗口）


def test_default_summary_happy_path(tmp_path, capsys) -> None:
    history = _write_history(tmp_path, _three_rows())
    rc = _run_cli(history)
    out = capsys.readouterr().out
    assert rc == mtc.EXIT_OK
    assert "[calibrate] 输入: history.jsonl" in out
    assert "[calibrate] 窗口: 扫描 3 / 使用 3（窗口=最新 200 条，省略更早 0）" in out
    assert "2026-09-11T10:00:00Z" in out and "2026-09-11T10:10:00Z" in out
    assert "[calibrate] 状态: ok=2 warn=1 critical=0" in out
    assert "来源: existing-defaults" in out
    assert "nearest-rank" in out
    assert "production_ready=false 不变" in out  # 边界注记恒在场


def test_json_format_structure(tmp_path, capsys) -> None:
    history = _write_history(tmp_path, _three_rows())
    rc = _run_cli(history, "--format", "json")
    out = capsys.readouterr().out
    assert rc == mtc.EXIT_OK
    payload = json.loads(out)  # json 模式 stdout 为单一 JSON 文档
    assert set(payload) == {"schema_version", "tool", "project", "query", "window",
                            "quantile_method", "alarm_semantics",
                            "ok_status_conflict_reference", "endpoint_latency_ms",
                            "log_error_totals", "boundaries"}
    assert payload["schema_version"] == 1
    assert payload["project"] == PROJECT
    assert payload["quantile_method"] == "nearest-rank"
    query = payload["query"]
    assert isinstance(query, dict)
    assert query["history_file"] == "history.jsonl"  # 纯名——绝无绝对路径
    assert query["samples"] == 200
    thresholds = query["evaluation_thresholds"]
    assert isinstance(thresholds, dict)
    assert thresholds["latency_warn_ms"] == pytest.approx(
        pm.DEFAULT_LATENCY_WARN_MS)  # 缺省评估配置 = 既有阈值
    assert thresholds["latency_critical_ms"] == pytest.approx(
        pm.DEFAULT_LATENCY_CRITICAL_MS)
    assert thresholds["log_error_warn"] == pm.DEFAULT_LOG_ERROR_WARN
    assert thresholds["log_error_critical"] == pm.DEFAULT_LOG_ERROR_CRITICAL
    assert thresholds["source"] == "existing-defaults"
    window = payload["window"]
    assert isinstance(window, dict)
    assert window["records_scanned"] == 3
    assert window["records_used"] == 3
    assert window["truncated_older_count"] == 0
    assert window["oldest_used_at"] == "2026-09-11T10:00:00Z"
    assert window["newest_used_at"] == "2026-09-11T10:10:00Z"
    assert window["duration_seconds"] == 600
    assert window["status_counts"] == {"ok": 2, "warn": 1, "critical": 0}
    assert set(payload["endpoint_latency_ms"]) == set(ENDPOINTS)
    assert set(payload["log_error_totals"]) == set(SERVICES)
    assert isinstance(payload["boundaries"], list) and payload["boundaries"]
    for block in list(payload["endpoint_latency_ms"].values()) + list(
            payload["log_error_totals"].values()):
        assert set(block) == {"samples", "distribution", "suggestion",
                              "evaluation_configured", "evaluation_suggested"}
        assert set(block["distribution"]) == {"min", "p50", "p90", "p95",
                                              "p99", "max"}


def test_nearest_rank_quantile_values(tmp_path, capsys) -> None:
    """nearest-rank 口径：rank = ceil(fraction·n)，升序取第 rank 个。"""
    base = datetime(2026, 9, 11, 11, 0, 0, tzinfo=timezone.utc)
    rows = [_row(_iso(base, i), latency_ms={"api-health": float(100 + i)},
                 log_errors={"api": i})
            for i in range(20)]  # 延迟 100..119；日志错误 0..19
    history = _write_history(tmp_path, rows)
    rc = _run_cli(history, "--format", "json")
    payload = json.loads(capsys.readouterr().out)
    assert rc == mtc.EXIT_OK
    api_latency = payload["endpoint_latency_ms"]["api-health"]["distribution"]
    assert api_latency["min"] == pytest.approx(100.0)
    assert api_latency["p50"] == pytest.approx(109.0)   # rank 10 → 第 10 小
    assert api_latency["p90"] == pytest.approx(117.0)   # rank 18
    assert api_latency["p95"] == pytest.approx(118.0)   # rank 19
    assert api_latency["p99"] == pytest.approx(119.0)   # rank 20
    assert api_latency["max"] == pytest.approx(119.0)
    api_logs = payload["log_error_totals"]["api"]["distribution"]
    assert api_logs["min"] == 0
    assert api_logs["p50"] == 9    # rank 10 → 第 10 小 = 9
    assert api_logs["p99"] == 19   # rank 20


def test_suggestion_rule_and_applicable(tmp_path, capsys) -> None:
    """建议规则 warn=p95/critical=p99；窗口内适用时 applicable + 双评估在场。"""
    base = datetime(2026, 9, 11, 11, 0, 0, tzinfo=timezone.utc)
    rows = [_row(_iso(base, i), latency_ms={"api-health": float(100 + i)})
            for i in range(20)]  # p95=118 < p99=119，且双双在配置界内
    history = _write_history(tmp_path, rows)
    rc = _run_cli(history, "--format", "json")
    payload = json.loads(capsys.readouterr().out)
    assert rc == mtc.EXIT_OK
    block = payload["endpoint_latency_ms"]["api-health"]
    suggestion = block["suggestion"]
    assert suggestion["rule"] == "p95-warn/p99-critical"
    assert suggestion["warn"] == pytest.approx(118.0)
    assert suggestion["critical"] == pytest.approx(119.0)
    assert suggestion["applicable"] is True
    assert suggestion["reason"] is None
    assert suggestion["bounds"] == {"min": pm.MIN_LATENCY_MS,
                                    "max": pm.MAX_LATENCY_MS}
    suggested = block["evaluation_suggested"]
    assert isinstance(suggested, dict)  # applicable → 建议阈值评估在场
    # 建议阈值评估语义：>= 含边界——118/119 各命中恰 2/1 个样本
    assert suggested["warn"] == pytest.approx(118.0)
    assert suggested["warn_count"] == 2  # 118 与 119
    assert suggested["critical_count"] == 1  # 119
    assert suggested["alarm_rate"] == pytest.approx(round(2 / 20, 6))
    assert suggested["coverage_rate"] == pytest.approx(round(18 / 20, 6))


def test_suggestion_degenerate_window_not_applicable(tmp_path, capsys) -> None:
    """全同值窗口：p95 == p99 → degenerate-window，建议评估不在场。"""
    base = datetime(2026, 9, 11, 11, 0, 0, tzinfo=timezone.utc)
    rows = [_row(_iso(base, i), latency_ms={"api-health": 42.0})
            for i in range(5)]
    history = _write_history(tmp_path, rows)
    rc = _run_cli(history, "--format", "json")
    payload = json.loads(capsys.readouterr().out)
    assert rc == mtc.EXIT_OK
    block = payload["endpoint_latency_ms"]["api-health"]
    suggestion = block["suggestion"]
    assert suggestion["warn"] == pytest.approx(42.0)
    assert suggestion["critical"] == pytest.approx(42.0)
    assert suggestion["applicable"] is False
    assert suggestion["reason"] == "degenerate-window"
    assert block["evaluation_suggested"] is None  # 不可用对子不评估
    assert isinstance(block["evaluation_configured"], dict)  # 配置评估照常


def test_suggestion_below_minimum_not_applicable(tmp_path, capsys) -> None:
    """真实生产形态：低延迟窗口 p95 < production_monitor 配置下界 →
    below-minimum（建议值如实保留，绝不抬升/修正）。"""
    base = datetime(2026, 9, 11, 11, 0, 0, tzinfo=timezone.utc)
    rows = [_row(_iso(base, i), latency_ms={"api-health": float(7.0 + i)})
            for i in range(20)]  # p95=25.0 < 50.0
    history = _write_history(tmp_path, rows)
    rc = _run_cli(history, "--format", "json")
    payload = json.loads(capsys.readouterr().out)
    assert rc == mtc.EXIT_OK
    block = payload["endpoint_latency_ms"]["api-health"]
    suggestion = block["suggestion"]
    assert suggestion["warn"] == pytest.approx(25.0)
    assert suggestion["applicable"] is False
    assert suggestion["reason"] == "below-minimum"


def test_suggestion_above_maximum_not_applicable(tmp_path, capsys) -> None:
    """极端窗口 p95 在界内而 p99 > 配置上界 → above-maximum。"""
    base = datetime(2026, 9, 11, 11, 0, 0, tzinfo=timezone.utc)
    rows = [_row(_iso(base, i), latency_ms={"api-health": float(100 + i)})
            for i in range(19)]  # 100..118（低 19 个）
    rows.append(_row(_iso(base, 19), latency_ms={"api-health": 700000.0}))
    history = _write_history(tmp_path, rows)
    rc = _run_cli(history, "--format", "json")
    payload = json.loads(capsys.readouterr().out)
    assert rc == mtc.EXIT_OK
    block = payload["endpoint_latency_ms"]["api-health"]
    suggestion = block["suggestion"]
    assert suggestion["warn"] == pytest.approx(118.0)   # rank 19，界内
    assert suggestion["critical"] == pytest.approx(700000.0)  # rank 20 > 600000
    assert suggestion["applicable"] is False
    assert suggestion["reason"] == "above-maximum"


def test_evaluation_semantics_inclusive_boundary(tmp_path, capsys) -> None:
    """评估语义：>= 含边界（与 production_monitor 同口径）；
    ok_status_conflict = 告警 ∧ overall==ok 的代理分歧（不是误报率/
    真值标注），分母 = 窗口样本总数。"""
    history = _write_history(tmp_path, _three_rows())
    # api-health 延迟 [60.0, 100.0, 200.0]，overall [ok, warn, ok]
    rc = _run_cli(history, "--format", "json",
                  "--latency-warn-ms", "100", "--latency-critical-ms", "150")
    payload = json.loads(capsys.readouterr().out)
    assert rc == mtc.EXIT_OK
    thresholds = payload["query"]["evaluation_thresholds"]
    assert thresholds["latency_warn_ms"] == pytest.approx(100.0)
    assert thresholds["latency_critical_ms"] == pytest.approx(150.0)
    assert thresholds["source"] == "explicit"
    evaluation = payload["endpoint_latency_ms"]["api-health"]["evaluation_configured"]
    assert evaluation["warn_count"] == 2  # 100.0 与 200.0（含边界）
    assert evaluation["critical_count"] == 1  # 200.0
    assert evaluation["coverage_rate"] == pytest.approx(round(1 / 3, 6))
    assert evaluation["alarm_rate"] == pytest.approx(round(2 / 3, 6))
    assert evaluation["critical_rate"] == pytest.approx(round(1 / 3, 6))
    # ok 分歧：告警 ∧ overall==ok → 仅 200.0 样本（100.0 样本 overall=warn
    # 非分歧）
    assert evaluation["ok_status_conflict_count"] == 1
    assert evaluation["ok_status_conflict_rate"] == pytest.approx(round(1 / 3, 6))
    # 日志错误评估同面（未显式提供 → 既有 5/20）：api 错误 [0, 4, 9] →
    # 9 告警（该样本 overall ok → ok 分歧 1）
    log_eval = payload["log_error_totals"]["api"]["evaluation_configured"]
    assert log_eval["warn_count"] == 1  # 9 >= 5
    assert log_eval["ok_status_conflict_count"] == 1


def test_evaluation_default_existing_thresholds_zero_alarm(tmp_path, capsys) -> None:
    """缺省评估配置 = 既有 production_monitor 阈值；低值窗口上告警恒 0。"""
    history = _write_history(tmp_path, _three_rows())  # 延迟 ≤30ms、错误 ≤9
    rc = _run_cli(history, "--format", "json")
    payload = json.loads(capsys.readouterr().out)
    assert rc == mtc.EXIT_OK
    for endpoint_id in ENDPOINTS:
        evaluation = payload["endpoint_latency_ms"][endpoint_id]["evaluation_configured"]
        assert evaluation["warn"] == pytest.approx(pm.DEFAULT_LATENCY_WARN_MS)
        assert evaluation["critical"] == pytest.approx(pm.DEFAULT_LATENCY_CRITICAL_MS)
        assert evaluation["warn_count"] == 0
        assert evaluation["alarm_rate"] == 0.0
        assert evaluation["coverage_rate"] == 1.0
    for service in SERVICES:
        evaluation = payload["log_error_totals"][service]["evaluation_configured"]
        assert evaluation["warn"] == pm.DEFAULT_LOG_ERROR_WARN
        assert evaluation["critical"] == pm.DEFAULT_LOG_ERROR_CRITICAL


def test_log_error_degenerate_zero_window(tmp_path, capsys) -> None:
    """零错误窗口（常见形态）：p95=p99=0 → degenerate-window，评估照常。"""
    history = _write_history(tmp_path, _three_rows())  # 非 api 服务错误恒 0
    rc = _run_cli(history, "--format", "json")
    payload = json.loads(capsys.readouterr().out)
    assert rc == mtc.EXIT_OK
    block = payload["log_error_totals"]["postgres"]
    assert block["distribution"]["max"] == 0
    assert block["suggestion"]["applicable"] is False
    assert block["suggestion"]["reason"] == "degenerate-window"
    assert block["evaluation_configured"]["warn_count"] == 0


def test_sample_window_keeps_newest_and_validates_globally(tmp_path, capsys) -> None:
    """窗口取最新 N 条（截断显式）；全文件行级校验先行——窗口外交替 malformed
    行仍整体拒绝（绝不静默截断掉坏行）。"""
    base = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)
    rows = [_row(_iso(base, i), latency_ms={"api-health": float(10 + i)})
            for i in range(10)]
    history = _write_history(tmp_path, rows)
    rc = _run_cli(history, "--format", "json", "--samples", "3")
    payload = json.loads(capsys.readouterr().out)
    assert rc == mtc.EXIT_OK
    window = payload["window"]
    assert window["records_scanned"] == 10
    assert window["records_used"] == 3
    assert window["truncated_older_count"] == 7
    assert window["oldest_used_at"] == _iso(base, 7)   # 最新 3 条 = 7/8/9
    assert window["newest_used_at"] == _iso(base, 9)
    # 窗口语义只作用于选择面：被截断行绝不进入分布
    api_latency = payload["endpoint_latency_ms"]["api-health"]["distribution"]
    assert api_latency["min"] == pytest.approx(17.0)  # 10+7
    assert api_latency["max"] == pytest.approx(19.0)
    # 全文件校验：早期 malformed 行（将落入窗口外）仍 fail-closed
    bad = _row(_iso(base, 10), schema_version=2)
    _write_history(tmp_path, [*rows, bad])
    assert _run_cli(history, "--samples", "3") == mtc.EXIT_REFUSED


def test_window_larger_than_file_uses_all(tmp_path, capsys) -> None:
    history = _write_history(tmp_path, _three_rows())
    rc = _run_cli(history, "--format", "json", "--samples", "5000")
    payload = json.loads(capsys.readouterr().out)
    assert rc == mtc.EXIT_OK
    assert payload["window"]["records_used"] == 3
    assert payload["window"]["truncated_older_count"] == 0


def test_ok_status_conflict_zero_when_no_ok_reference(tmp_path, capsys) -> None:
    """全 non-ok 窗口：告警照常、ok 分歧恒 0（ok 参考缺失不虚构）。"""
    rows = [
        _row("2026-09-11T10:00:00Z", overall="warn",
             latency_ms={"api-health": 2000.0}),
        _row("2026-09-11T10:05:00Z", overall="critical",
             latency_ms={"api-health": 3000.0}),
    ]
    history = _write_history(tmp_path, rows)
    rc = _run_cli(history, "--format", "json")  # 既有阈值 1000/5000
    payload = json.loads(capsys.readouterr().out)
    assert rc == mtc.EXIT_OK
    evaluation = payload["endpoint_latency_ms"]["api-health"]["evaluation_configured"]
    assert evaluation["warn_count"] == 2
    assert evaluation["ok_status_conflict_count"] == 0
    assert evaluation["ok_status_conflict_rate"] == 0.0


def test_restart_counts_not_a_calibration_surface(tmp_path, capsys) -> None:
    """restart 累计计数绝不进入标定面（M14-23 增量语义——诚实排除）。"""
    rows = [
        _row("2026-09-11T10:00:00Z", restarts={"api": 500}),
        _row("2026-09-11T10:05:00Z", restarts={"api": 600}),
    ]
    history = _write_history(tmp_path, rows)
    rc = _run_cli(history, "--format", "json")
    payload = json.loads(capsys.readouterr().out)
    assert rc == mtc.EXIT_OK
    assert "restart" not in set(payload["log_error_totals"]["api"])
    block = payload["log_error_totals"]["api"]
    assert block["distribution"]["max"] == 0  # restart 未冒充日志错误指标


# ---------------------------------------------------------------- 输出卫生


def test_zero_wall_clock_byte_reproducible(tmp_path, capsys) -> None:
    history = _write_history(tmp_path, _three_rows())
    _run_cli(history, "--format", "json")
    first = capsys.readouterr().out
    _run_cli(history, "--format", "json")
    second = capsys.readouterr().out
    assert first == second  # 零墙钟：两次运行 stdout 逐字节相同
    _run_cli(history)  # summary 同样逐字节可复现
    first_summary = capsys.readouterr().out
    _run_cli(history)
    assert capsys.readouterr().out == first_summary


def test_extras_ignored_and_marker_never_leaks(tmp_path, capsys) -> None:
    rows = [_row("2026-09-11T10:00:00Z", extra={"poison": MARK_TOKEN,
                                                "raw_log": MARK_TOKEN})]
    history = _write_history(tmp_path, rows)
    rc = _run_cli(history, "--format", "json")
    out = capsys.readouterr().out
    assert rc == mtc.EXIT_OK
    assert MARK_TOKEN not in out


def test_summary_and_json_both_carry_boundaries(tmp_path, capsys) -> None:
    history = _write_history(tmp_path, _three_rows())
    _run_cli(history)
    summary = capsys.readouterr().out
    assert "[calibrate] 边界:" in summary
    assert "不改变生产阈值" in summary
    assert "不授权任何部署" in summary
    rc = _run_cli(history, "--format", "json")
    payload = json.loads(capsys.readouterr().out)
    assert rc == mtc.EXIT_OK
    joined = " ".join(payload["boundaries"])
    assert "不改变生产阈值" in joined
    assert "production_ready=false 不变" in joined
    assert "零文件写入" in joined


def test_refusal_emits_no_body(tmp_path, capsys) -> None:
    history = _write_history(tmp_path, [], text="oops not json\n")
    rc = _run_cli(history, "--format", "json")
    out = capsys.readouterr().out
    assert rc == mtc.EXIT_REFUSED
    assert '"schema_version"' not in out
    assert "[calibrate] 窗口" not in out  # 摘要正文零输出
