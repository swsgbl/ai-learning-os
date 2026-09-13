r"""M14-15 tools/ops/monitoring_insights.py 契约测试：监控历史洞察/告警
摘要的安全边界（零真实容器面/零网络/零子进程/零计划任务/零 env 读取）。

覆盖（全部 I/O 经真实临时目录或 FakeStore 注入；绝不触碰 canonical 仓库
与真实 .verify 目录）：
- 结构契约：源码零子进程/零网络/零 env/零墙钟 token；schema 单一事实源
  （委托同仓 monitoring_history）；socket+subprocess 双阻断下端到端照常
  成功；零墙钟逐字节可复现；
- plan/execute 门禁：默认 plan 完全惰性（Store 零构造、零读取/零写入）；
  execute 需旗标 + 精确确认短语（缺一/近似 → EXIT 2 零读取）；event-limit
  超界 plan/execute 同样拒绝；
- 输入三形态：history.jsonl 文件 / 含它的目录（SHA-256 同源）/ M14-12
  monitor 工件目录（委托 M14-13 已测管道：malformed 透传拒绝、源文件
  逐字节不变、同哈希去重 duplicate_count 显式）；混入两形态 → mixed
  拒绝；空目录/缺源/非 history.jsonl 文件名拒绝；
- 路径防御：源文件/源目录/输出目标/输出祖先 symlink 拒绝（含 mkdir 穿越
  防御：symlink 目标零写入、输出路径零创建）；
- 行校验 fail-closed（输出零写入）：not-json/空行/数组/schema_version/
  sha 形态/stem 白名单/时间戳格式/project/overall_status(incomplete)/
  partial/阈值计数/服务 health/restart/端点 http_status+有限非负延迟
  （nan/inf/-inf）/日志 error_total；时间乱序/重复行/同时间戳逆序/跨
  project 混档/零样本/超 5000 样本拒绝；未知额外字段忽略且投毒标记值
  绝不进入输出；
- 洞察计算：状态计数+availability（计数+比率）、时间范围+时长、逐端点
  延迟 min/p50/p95/max（nearest-rank）+非 200 计数、事件列表内容
  （非 healthy 服务/非 200 端点）与有界截断（保最新 N）、当前连胜/最长
  non-ok 连败区间/失败恢复转移（计数+时间戳）/逐端点当前连续失败、
  服务 restart/日志 error/非 healthy 样本汇总、最近样本全字段、单样本
  边界形态；
- 原子写与顺序：校验失败 FakeStore 零写调用；第 1/第 2 次写失败可见拒绝；
  真实文件面 os.replace 失败零 tmp 残留；成功后零 tmp 残留；
- 报告卫生：stdout 绝无绝对本机路径；被拒值不回显；输出绝无投毒标记/
  路径；Markdown 表与边界注记（不构成 production readiness 宣称）；
- CLI：默认值注册、ops README 文档化、happy path stdout 摘要行。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import socket
import subprocess as subprocess_module
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ops" / "monitoring_insights.py"
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


mi = _load_module(SCRIPT, "monitoring_insights_under_test")


# ---------------------------------------------------------------- 样本工厂


def stem_for(iso: str) -> str:
    date, clock = iso.rstrip("Z").split("T")
    return "monitor-" + date.replace("-", "") + "-" + clock.replace(":", "")


def _spread(value_or_map: object, keys: tuple[str, ...], default: object) -> dict[str, object]:
    if isinstance(value_or_map, dict):
        return {key: value_or_map.get(key, default) for key in keys}
    return {key: value_or_map for key in keys}


def _drop_section_key(row: dict[str, object], section: str, key: str) -> dict[str, object]:
    inner = row[section]
    assert isinstance(inner, dict)
    row[section] = {k: v for k, v in inner.items() if k != key}
    return row


def _row(iso: str, *, project: str = PROJECT, overall: str = "ok", partial: bool = False,
         stem: str | None = None, schema_version: int = 1,
         counts: dict[str, int] | None = None, sha: str | None = None,
         health: object = "healthy", restarts: object = 0, log_errors: object = 0,
         http_status: object = 200, latency_ms: object = 10.0,
         drop: str | None = None, extra: dict[str, object] | None = None) -> dict[str, object]:
    """canonical 形状的 M14-13 history 记录（与 monitoring_history.build_record 同构）。"""
    statuses = _spread(http_status, ENDPOINTS, 200)
    latencies = _spread(latency_ms, ENDPOINTS, 10.0)
    row: dict[str, object] = {
        "schema_version": schema_version,
        "artifact_sha256": sha if sha is not None
        else hashlib.sha256((stem or stem_for(iso)).encode()).hexdigest(),
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


def _write_history(directory: Path, rows: list[dict[str, object]],
                   name: str = "history.jsonl", *, text: str | None = None) -> Path:
    content = text if text is not None else "".join(
        json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return path


def _report(iso: str, *, overall: str = "ok", project: str = PROJECT) -> dict[str, object]:
    """canonical 形状的合成 M14-12 monitor 报告（与 test_monitoring_history 同构）。"""
    services = {s: {"health": "healthy", "state": "running"} for s in SERVICES}
    containers = {s: {"status": "ok", "failure_category": None, "error_class": None,
                      "name": f"{PROJECT}-{s}-1", "state": "running", "health": "healthy",
                      "restart_count": 0, "image": f"aios/{s}:tag",
                      "started_at": "2026-09-11T00:00:00Z"} for s in SERVICES}
    endpoints = {e: {"status": "ok", "failure_category": None, "error_class": None,
                     "http_status": 200, "latency_ms": 10.0} for e in ENDPOINTS}
    logs = {s: {"status": "ok", "failure_category": None, "error_class": None,
                "lines_scanned": 5, "levels": {"fatal": 0, "error": 0, "critical": 0,
                                               "warning": 0, "traceback": 0, "panic": 0},
                "error_total": 0} for s in SERVICES}
    return {
        "schema_version": 1, "tool": "tools/ops/production_monitor.py",
        "milestone": "M14-12", "mode": "execute",
        "started_at_utc": iso, "ended_at_utc": iso,
        "config": {"project": project, "profile": "local"},
        "boundaries": ["read-only collection"],
        "collectors": {
            "compose_ps": {"status": "ok", "failure_category": None, "error_class": None,
                           "services": services},
            "containers": {"status": "ok", "per_service": containers},
            "endpoints": {"status": "ok", "per_endpoint": endpoints},
            "logs": {"status": "ok", "per_service": logs},
        },
        "partial": False,
        "threshold_results": {"counts": {"ok": 34, "warn": 0, "critical": 0}},
        "overall_status": overall,
        "monitoring_ready": overall == "ok",
    }


def _run_cli(source: Path, output: Path, *extra: str) -> int:
    return mi.main(["--source", str(source), "--output-dir", str(output), *extra])


def _execute(source: Path, output: Path, *extra: str) -> int:
    return _run_cli(source, output, "--execute", "--confirm", mi.CONFIRM_PHRASE, *extra)


def _insights(output: Path) -> dict[str, object]:
    return json.loads((output / mi.INSIGHTS_JSON_NAME).read_text(encoding="utf-8"))


def _block_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("subprocess is forbidden in this test")

    monkeypatch.setattr(subprocess_module, "run", _forbidden)

    def _no_socket(*args: object, **kwargs: object) -> None:
        raise AssertionError("network is forbidden in this test")

    monkeypatch.setattr(socket, "socket", _no_socket)


def _no_store_construction(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """RealStore 构造即失败（plan/拒绝路径零读取的结构性证明）。"""
    calls: list[str] = []

    def _forbidden() -> None:
        calls.append("constructed")
        raise AssertionError("store must not be constructed on this path")

    monkeypatch.setattr(mi, "RealStore", _forbidden)
    return calls


# ---------------------------------------------------------------- FakeStore


class FakeStore:
    """伪文件面：basename 键控；可注入 symlink 与写失败。"""

    def __init__(self, *, files: dict[str, bytes] | None = None,
                 dirs: tuple[str, ...] = (), symlinks: tuple[str, ...] = (),
                 fail_write_index: int | None = None) -> None:
        self.files: dict[str, bytes] = dict(files or {})
        self.dirs: set[str] = set(dirs)
        self.symlinks: set[str] = set(symlinks)
        self.fail_write_index = fail_write_index
        self.write_calls: list[str] = []
        self._write_count = 0

    def exists(self, path: Path) -> bool:
        return path.name in self.files or path.name in self.dirs or path.name in self.symlinks

    def is_dir(self, path: Path) -> bool:
        return path.name in self.dirs

    def is_symlink(self, path: Path) -> bool:
        return path.name in self.symlinks

    def list_dir(self, directory: Path) -> list[str]:
        return sorted(self.files)

    def read_bytes(self, path: Path) -> bytes:
        return self.files[path.name]

    def mkdirs(self, directory: Path) -> None:
        self.dirs.add(directory.name)

    def write_atomic(self, path: Path, text: str) -> None:
        self._write_count += 1
        self.write_calls.append(path.name)
        if self.fail_write_index is not None and self._write_count == self.fail_write_index:
            raise OSError("simulated write failure")
        self.files[path.name] = text.encode("utf-8")


def _history_text(rows: list[dict[str, object]]) -> bytes:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode("utf-8")


def _fake_execute(monkeypatch: pytest.MonkeyPatch, store: FakeStore, source: Path,
                  output: Path, *extra: str) -> int:
    monkeypatch.setattr(mi, "RealStore", lambda: store)
    return _execute(source, output, *extra)


# ---------------------------------------------------------------- 结构契约


def test_source_contract_forbidden_tokens() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for token in ("subprocess", "socket", "urllib", "http.client", "os.system",
                  "Popen", "environ", "requests", "urlopen", "getenv", "docker",
                  "datetime.now", "utcnow", "time.time"):
        assert token not in source, f"禁止出现的字面量: {token}"
    assert "os.replace" in source  # 唯一落盘机制：原子替换
    assert "import monitoring_history" in source  # schema 单一事实源（M14-13 委托）


def test_no_subprocess_no_network_end_to_end(monkeypatch, tmp_path) -> None:
    _block_side_effects(monkeypatch)
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_history(source, [_row("2026-09-11T17:39:20Z")])
    rc = _execute(source, output)
    assert rc == mi.EXIT_OK
    assert (output / mi.INSIGHTS_JSON_NAME).is_file()
    assert (output / mi.INSIGHTS_MD_NAME).is_file()


def test_zero_wall_clock_byte_reproducible(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    _write_history(source, [_row("2026-09-11T17:00:00Z"), _row("2026-09-11T18:00:00Z")])
    out1, out2 = tmp_path / "o1", tmp_path / "o2"
    assert _execute(source, out1) == mi.EXIT_OK
    assert _execute(source, out2) == mi.EXIT_OK
    for name in (mi.INSIGHTS_JSON_NAME, mi.INSIGHTS_MD_NAME):
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes()
    data = _insights(out1)
    assert data["generated_from_newest_at"] == "2026-09-11T18:00:00Z"


# ---------------------------------------------------------------- plan / execute 门禁


def test_plan_default_fully_lazy_zero_reads_zero_writes(monkeypatch, capsys) -> None:
    calls = _no_store_construction(monkeypatch)
    rc = mi.main([])
    assert rc == mi.EXIT_OK
    assert calls == []
    out = capsys.readouterr().out
    assert "plan" in out
    assert mi.CONFIRM_PHRASE in out  # 公开门禁常量提示
    assert str(mi.DEFAULT_SOURCE) not in out  # 绝无绝对路径


@pytest.mark.parametrize("bad_confirm", [
    "", "EXECUTE READ-ONLY MONITORING INSIGHT", "execute read-only monitoring insights",
    "EXECUTE READ-ONLY MONITORING INSIGHTS ",
])
def test_execute_wrong_confirm_refused_zero_reads(monkeypatch, capsys, tmp_path,
                                                  bad_confirm: str) -> None:
    calls = _no_store_construction(monkeypatch)
    rc = _run_cli(tmp_path / "src", tmp_path / "out", "--execute", "--confirm", bad_confirm)
    assert rc == mi.EXIT_REFUSED
    assert calls == []
    assert not (tmp_path / "out").exists()
    out = capsys.readouterr().out
    # stdout 提示公开门禁常量（与 M14-12/M14-14 同款）；被拒值仅当其本身
    # 是正确短语的子串时才可能随之出现——除此之外绝不回显。
    if bad_confirm not in mi.CONFIRM_PHRASE:
        assert bad_confirm not in out
    assert mi.CONFIRM_PHRASE in out


def test_stray_confirm_without_execute_is_plan(monkeypatch, capsys, tmp_path) -> None:
    calls = _no_store_construction(monkeypatch)
    rc = _run_cli(tmp_path / "src", tmp_path / "out", "--confirm", mi.CONFIRM_PHRASE)
    assert rc == mi.EXIT_OK
    assert calls == []
    assert "plan" in capsys.readouterr().out


@pytest.mark.parametrize("bad_limit", [0, -1, 501, 10000])
def test_event_limit_out_of_bounds_refused_in_plan(monkeypatch, capsys, bad_limit: int) -> None:
    calls = _no_store_construction(monkeypatch)
    rc = _run_cli(mi.DEFAULT_SOURCE, Path("out"), "--event-limit", str(bad_limit))
    assert rc == mi.EXIT_REFUSED
    assert calls == []


def test_event_limit_out_of_bounds_refused_in_execute(monkeypatch, tmp_path) -> None:
    calls = _no_store_construction(monkeypatch)
    rc = _run_cli(tmp_path / "src", tmp_path / "out", "--event-limit", "0",
                  "--execute", "--confirm", mi.CONFIRM_PHRASE)
    assert rc == mi.EXIT_REFUSED
    assert calls == []
    assert not (tmp_path / "out").exists()


# ---------------------------------------------------------------- 输入三形态


def test_source_missing_refused_zero_writes(tmp_path) -> None:
    rc = _execute(tmp_path / "missing", tmp_path / "out")
    assert rc == mi.EXIT_REFUSED
    assert not (tmp_path / "out").exists()


def test_source_file_wrong_name_refused(tmp_path) -> None:
    rogue = tmp_path / "other.jsonl"
    rogue.write_text("", encoding="utf-8")
    rc = _execute(rogue, tmp_path / "out")
    assert rc == mi.EXIT_REFUSED
    assert not (tmp_path / "out").exists()


def test_history_file_direct(tmp_path) -> None:
    history = _write_history(tmp_path, [_row("2026-09-11T17:39:20Z")])
    output = tmp_path / "out"
    assert _execute(history, output) == mi.EXIT_OK
    data = _insights(output)
    assert data["source_kind"] == "history-file"
    assert data["source_sha256"] == hashlib.sha256(history.read_bytes()).hexdigest()


def test_history_dir_with_sha_chain(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    history = _write_history(source, [_row("2026-09-11T17:39:20Z")])
    output = tmp_path / "out"
    assert _execute(source, output) == mi.EXIT_OK
    data = _insights(output)
    assert data["source_kind"] == "history-dir"
    assert data["source_sha256"] == hashlib.sha256(history.read_bytes()).hexdigest()


def test_mixed_inputs_refused(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    _write_history(source, [_row("2026-09-11T17:39:20Z")])
    (source / "monitor-20260911-173920.json").write_text("{}", encoding="utf-8")
    rc = _execute(source, tmp_path / "out")
    assert rc == mi.EXIT_REFUSED
    assert not (tmp_path / "out").exists()


def test_empty_source_dir_refused(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "plan-20260911-173920.json").write_text("{}", encoding="utf-8")
    (source / "README.md").write_text("note", encoding="utf-8")
    rc = _execute(source, tmp_path / "out")
    assert rc == mi.EXIT_REFUSED
    assert not (tmp_path / "out").exists()


def test_monitor_dir_delegation_happy(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "monitor-20260911-170000.json").write_text(
        json.dumps(_report("2026-09-11T17:00:00Z")), encoding="utf-8")
    (source / "monitor-20260911-180000.json").write_text(
        json.dumps(_report("2026-09-11T18:00:00Z", overall="warn")), encoding="utf-8")
    output = tmp_path / "out"
    rc = _execute(source, output)
    assert rc == mi.EXIT_OK
    data = _insights(output)
    assert data["source_kind"] == "monitor-artifacts"
    assert data["project"] == PROJECT
    assert data["sample_count"] == 2
    assert data["status_counts"] == {"ok": 1, "warn": 1, "critical": 0}


def test_monitor_dir_malformed_refused_transparently(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "monitor-20260911-170000.json").write_text("not json", encoding="utf-8")
    rc = _execute(source, tmp_path / "out")
    assert rc == mi.EXIT_REFUSED
    assert not (tmp_path / "out").exists()


def test_monitor_dir_source_files_unchanged(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    first = source / "monitor-20260911-170000.json"
    first.write_text(json.dumps(_report("2026-09-11T17:00:00Z")), encoding="utf-8")
    before = hashlib.sha256(first.read_bytes()).hexdigest()
    assert _execute(source, tmp_path / "out") == mi.EXIT_OK
    assert hashlib.sha256(first.read_bytes()).hexdigest() == before
    assert sorted(p.name for p in source.iterdir()) == ["monitor-20260911-170000.json"]


def test_monitor_dir_duplicate_count_explicit(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    payload = json.dumps(_report("2026-09-11T17:00:00Z"))
    (source / "monitor-20260911-170000.json").write_text(payload, encoding="utf-8")
    (source / "monitor-20260911-170001.json").write_text(payload, encoding="utf-8")
    assert _execute(source, tmp_path / "out") == mi.EXIT_OK
    data = _insights(tmp_path / "out")
    assert data["duplicate_count"] == 1
    assert data["sample_count"] == 1


def test_monitor_dir_historical_incomplete_skipped_via_delegation(tmp_path) -> None:
    """M14-20 回归：monitor 工件目录混有历史 incomplete 工件（M14-12 采集
    不完整时期，partial=true + overall_status=incomplete 共现）时，经
    M14-13 委托管道同样跳过不入档——完整 warn 样本照常产出洞察。"""
    source = tmp_path / "src"
    source.mkdir()
    incomplete = _report("2026-09-12T17:37:46Z")
    incomplete["partial"] = True
    incomplete["overall_status"] = "incomplete"
    incomplete["monitoring_ready"] = False
    (source / "monitor-20260912-173746.json").write_text(
        json.dumps(incomplete), encoding="utf-8")
    (source / "monitor-20260913-010414.json").write_text(
        json.dumps(_report("2026-09-13T01:04:14Z", overall="warn")), encoding="utf-8")
    rc = _execute(source, tmp_path / "out")
    assert rc == mi.EXIT_OK
    data = _insights(tmp_path / "out")
    assert data["source_kind"] == "monitor-artifacts"
    assert data["sample_count"] == 1
    assert data["status_counts"] == {"ok": 0, "warn": 1, "critical": 0}


# ---------------------------------------------------------------- symlink / 路径防御


def test_symlinked_history_file_refused_real_fs(tmp_path) -> None:
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    real = _write_history(real_dir, [_row("2026-09-11T17:39:20Z")])
    source = tmp_path / "src"
    source.mkdir()
    link = source / "history.jsonl"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlink 不可用（权限/平台）")
    rc = _execute(source, tmp_path / "out")
    assert rc == mi.EXIT_REFUSED
    assert not (tmp_path / "out").exists()


def test_source_dir_symlink_refused_via_fake_store(monkeypatch) -> None:
    store = FakeStore(files={"history.jsonl": _history_text([_row("2026-09-11T17:39:20Z")])},
                      dirs=("src",), symlinks=("src",))
    rc = _fake_execute(monkeypatch, store, Path("/fake/src"), Path("/fake/out"))
    assert rc == mi.EXIT_REFUSED
    assert store.write_calls == []


def test_output_ancestor_symlink_mkdirs_not_followed_real_fs(tmp_path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    guarded = tmp_path / "guarded"
    guarded.mkdir()
    try:
        (guarded / "out").symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink 不可用（权限/平台）")
    source = tmp_path / "src"
    source.mkdir()
    _write_history(source, [_row("2026-09-11T17:39:20Z")])
    rc = _execute(source, guarded / "out")
    assert rc == mi.EXIT_REFUSED
    assert list(target.iterdir()) == []          # symlink 目标零写入
    assert not (guarded / "out").exists() or (guarded / "out").is_symlink()


def test_output_file_symlink_refused_via_fake_store(monkeypatch) -> None:
    store = FakeStore(files={"history.jsonl": _history_text([_row("2026-09-11T17:39:20Z")])},
                      dirs=("src", "out"), symlinks=(mi.INSIGHTS_JSON_NAME,))
    rc = _fake_execute(monkeypatch, store, Path("/fake/src"), Path("/fake/out"))
    assert rc == mi.EXIT_REFUSED
    assert store.write_calls == []  # 零写入
    assert mi.INSIGHTS_MD_NAME not in store.files


# ---------------------------------------------------------------- 行校验 fail-closed


def _mutated_row(**kwargs) -> dict[str, object]:
    return _row("2026-09-11T17:39:20Z", **kwargs)


BAD_ROWS: list[tuple[str, object]] = [
    ("not-json", "token {oops"),
    ("blank-line", None),  # 特判：行间空行
    ("json-array", json.dumps([_row("2026-09-11T17:39:20Z")])),
    ("schema-version", _mutated_row(schema_version=2)),
    ("sha-missing", _mutated_row(drop="artifact_sha256")),
    ("sha-not-hex", _mutated_row(sha="ZZ" + "0" * 62)),
    ("sha-short", _mutated_row(sha="0" * 63)),
    ("stem-bad", _mutated_row(stem="monitor-evil")),
    ("stem-missing", _mutated_row(drop="source_stem")),
    ("timestamp-format", _mutated_row() | {"collected_at": "2026-09-11 17:39:20"}),
    ("timestamp-type", _mutated_row() | {"collected_at": 12345}),
    ("project-bad", _mutated_row(project="bad project!")),
    ("overall-incomplete", _mutated_row(overall="incomplete")),
    ("partial-true", _mutated_row(partial=True)),
    ("counts-missing", _mutated_row(counts={"ok": 34, "warn": 0})),
    ("counts-bool", _mutated_row(counts={"ok": True, "warn": 0, "critical": 0})),
    ("counts-negative", _mutated_row(counts={"ok": -1, "warn": 0, "critical": 0})),
    ("health-missing-service",
     _drop_section_key(_mutated_row(), "compose_service_health", "livekit")),
    ("health-not-str", _mutated_row(health={s: 1 for s in SERVICES})),
    ("restart-bool", _mutated_row(restarts={s: False for s in SERVICES})),
    ("restart-negative", _mutated_row(restarts={s: -1 for s in SERVICES})),
    ("restart-missing", _mutated_row(drop="restart_counts")),
    ("endpoint-missing", _drop_section_key(_mutated_row(), "endpoints", "cosyvoice-health")),
    ("endpoint-status-str", _mutated_row(http_status={e: "200" for e in ENDPOINTS})),
    ("latency-nan", _mutated_row(latency_ms=float("nan"))),
    ("latency-inf", _mutated_row(latency_ms=float("inf"))),
    ("latency-negative", _mutated_row(latency_ms=-0.5)),
    ("latency-str", _mutated_row(latency_ms="10.0")),
    ("log-errors-float", _mutated_row(log_errors={s: 1.5 for s in SERVICES})),
    ("log-errors-missing-service",
     _drop_section_key(_mutated_row(), "log_error_totals", "minio")),
]


@pytest.mark.parametrize("reason,payload", BAD_ROWS,
                         ids=[case[0] for case in BAD_ROWS])
def test_row_validation_refused_zero_writes(tmp_path, reason: str, payload: object) -> None:
    source = tmp_path / "src"
    source.mkdir()
    if reason == "blank-line":
        text = json.dumps(_row("2026-09-11T17:39:20Z")) + "\n\n" \
               + json.dumps(_row("2026-09-11T17:40:20Z")) + "\n"
        _write_history(source, [], text=text)
    elif reason == "not-json":
        _write_history(source, [], text=payload + "\n")
    else:
        _write_history(source, [payload])  # type: ignore[list-item]
    output = tmp_path / "out"
    rc = _execute(source, output)
    assert rc == mi.EXIT_REFUSED
    assert not output.exists()


def test_out_of_order_rows_refused(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    _write_history(source, [_row("2026-09-11T18:00:00Z"), _row("2026-09-11T17:00:00Z")])
    rc = _execute(source, tmp_path / "out")
    assert rc == mi.EXIT_REFUSED
    assert not (tmp_path / "out").exists()


def test_duplicate_row_refused(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    row = _row("2026-09-11T17:00:00Z")
    _write_history(source, [row, dict(row)])
    rc = _execute(source, tmp_path / "out")
    assert rc == mi.EXIT_REFUSED
    assert not (tmp_path / "out").exists()


def test_equal_timestamp_ascending_stems_accepted_descending_refused(tmp_path) -> None:
    source_ok = tmp_path / "ok"
    source_ok.mkdir()
    _write_history(source_ok, [
        _row("2026-09-11T17:00:00Z", stem="monitor-20260911-170001"),
        _row("2026-09-11T17:00:00Z", stem="monitor-20260911-170002"),
    ])
    assert _execute(source_ok, tmp_path / "out-ok") == mi.EXIT_OK
    source_bad = tmp_path / "bad"
    source_bad.mkdir()
    _write_history(source_bad, [
        _row("2026-09-11T17:00:00Z", stem="monitor-20260911-170002"),
        _row("2026-09-11T17:00:00Z", stem="monitor-20260911-170001"),
    ])
    assert _execute(source_bad, tmp_path / "out-bad") == mi.EXIT_REFUSED


def test_mixed_project_refused(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    _write_history(source, [
        _row("2026-09-11T17:00:00Z", project="project-a"),
        _row("2026-09-11T18:00:00Z", project="project-b"),
    ])
    rc = _execute(source, tmp_path / "out")
    assert rc == mi.EXIT_REFUSED
    assert not (tmp_path / "out").exists()


def test_zero_samples_refused(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    _write_history(source, [], text="")
    rc = _execute(source, tmp_path / "out")
    assert rc == mi.EXIT_REFUSED
    assert not (tmp_path / "out").exists()


def test_too_many_samples_refused(tmp_path) -> None:
    from datetime import datetime, timedelta, timezone

    base = datetime(2026, 9, 11, 0, 0, 0, tzinfo=timezone.utc)
    rows = [_row((base + timedelta(seconds=i)).strftime("%Y-%m-%dT%H:%M:%SZ"))
            for i in range(5001)]
    source = tmp_path / "src"
    source.mkdir()
    _write_history(source, rows)
    assert len(rows) == 5001
    rc = _execute(source, tmp_path / "out")
    assert rc == mi.EXIT_REFUSED
    assert not (tmp_path / "out").exists()


def test_extras_ignored_and_marker_never_leaks(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    _write_history(source, [
        _row("2026-09-11T17:00:00Z", extra={"poison": MARK_TOKEN, "raw_log": MARK_TOKEN}),
    ])
    output = tmp_path / "out"
    assert _execute(source, output) == mi.EXIT_OK
    for name in (mi.INSIGHTS_JSON_NAME, mi.INSIGHTS_MD_NAME):
        assert MARK_TOKEN not in (output / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- 洞察计算


def _scenario_rows() -> list[dict[str, object]]:
    """ok, ok, warn(api unhealthy+funasr 503), critical, ok, ok —— 6 样本。"""
    return [
        _row("2026-09-11T17:00:00Z"),
        _row("2026-09-11T17:15:00Z"),
        _row("2026-09-11T17:30:00Z", overall="warn",
             health={"api": "unhealthy"}, http_status={"funasr-health": 503},
             restarts={"api": 1}, log_errors={"api": 7}),
        _row("2026-09-11T17:45:00Z", overall="critical",
             health={"redis": "unhealthy"}, http_status={"api-health": 500},
             restarts={"redis": 5}, log_errors={"redis": 30}),
        _row("2026-09-11T18:00:00Z"),
        _row("2026-09-11T18:15:00Z"),
    ]


def _scenario_insights(tmp_path) -> dict[str, object]:
    source = tmp_path / "src"
    source.mkdir()
    _write_history(source, _scenario_rows())
    output = tmp_path / "out"
    assert _execute(source, output) == mi.EXIT_OK
    return _insights(output)


def test_status_counts_and_availability(tmp_path) -> None:
    data = _scenario_insights(tmp_path)
    assert data["status_counts"] == {"ok": 4, "warn": 1, "critical": 1}
    assert data["availability_count"] == 4
    assert data["availability_ratio"] == 0.666667
    assert data["degraded_count"] == 1
    assert data["critical_count"] == 1


def test_time_range_and_duration(tmp_path) -> None:
    data = _scenario_insights(tmp_path)
    assert data["sample_count"] == 6
    assert data["first_collected_at"] == "2026-09-11T17:00:00Z"
    assert data["last_collected_at"] == "2026-09-11T18:15:00Z"
    assert data["duration_seconds"] == 4500
    assert data["project"] == PROJECT


def test_endpoint_latency_percentiles_nearest_rank(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    latencies = [5.0, 10.0, 15.0, 20.0, 25.0]
    rows = [_row(f"2026-09-11T17:{i * 10:02d}:00Z", latency_ms={"api-health": latencies[i]})
            for i in range(5)]
    _write_history(source, rows)
    assert _execute(source, tmp_path / "out") == mi.EXIT_OK
    stats = _insights(tmp_path / "out")["endpoint_latency_ms"]
    assert isinstance(stats, dict)
    api = stats["api-health"]
    assert isinstance(api, dict)
    assert (api["min"], api["p50"], api["p95"], api["max"]) == (5.0, 15.0, 25.0, 25.0)
    assert api["samples"] == 5


def test_event_details_list_unhealthy_services_and_failing_endpoints(tmp_path) -> None:
    data = _scenario_insights(tmp_path)
    events = data["degraded_critical_events"]
    assert isinstance(events, dict)
    assert events["events_total"] == 2
    listed = events["events"]
    assert isinstance(listed, list) and len(listed) == 2
    warn_event, critical_event = listed
    assert isinstance(warn_event, dict) and isinstance(critical_event, dict)
    assert warn_event["overall_status"] == "warn"
    assert warn_event["unhealthy_services"] == ["api"]
    assert warn_event["failing_endpoints"] == ["funasr-health"]
    assert critical_event["overall_status"] == "critical"
    assert critical_event["unhealthy_services"] == ["redis"]
    assert critical_event["failing_endpoints"] == ["api-health"]


def test_event_list_bounded_keeps_newest(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    rows = [_row("2026-09-11T17:00:00Z"),
            _row("2026-09-11T17:15:00Z", overall="warn", http_status={"web-root": 500}),
            _row("2026-09-11T17:30:00Z", overall="warn", http_status={"web-login": 500}),
            _row("2026-09-11T17:45:00Z", overall="critical", http_status={"api-health": 500}),
            _row("2026-09-11T18:00:00Z")]
    _write_history(source, rows)
    output = tmp_path / "out"
    assert _execute(source, output, "--event-limit", "2") == mi.EXIT_OK
    events = _insights(output)["degraded_critical_events"]
    assert isinstance(events, dict)
    assert (events["events_total"], events["events_listed"], events["events_truncated"]) == (3, 2, 1)
    listed = events["events"]
    assert isinstance(listed, list)
    assert [event["collected_at"] for event in listed] == ["2026-09-11T17:30:00Z",
                                                           "2026-09-11T17:45:00Z"]


def test_streaks_current_longest_and_transitions(tmp_path) -> None:
    data = _scenario_insights(tmp_path)
    streaks = data["streaks"]
    assert isinstance(streaks, dict)
    assert streaks["current"] == {"status": "ok", "length": 2}
    assert streaks["current_non_ok_length"] == 0
    assert streaks["longest_non_ok"] == {
        "length": 2, "first_at": "2026-09-11T17:30:00Z", "last_at": "2026-09-11T17:45:00Z"}
    assert streaks["failure_transition_count"] == 1
    assert streaks["recovery_transition_count"] == 1
    assert streaks["failure_transitions"] == [{"at": "2026-09-11T17:30:00Z", "to_status": "warn"}]
    assert streaks["recovery_transitions"] == [{"at": "2026-09-11T18:00:00Z",
                                                "from_status": "critical"}]


def test_endpoint_current_failure_streaks(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    _write_history(source, [
        _row("2026-09-11T17:00:00Z"),
        _row("2026-09-11T17:15:00Z", http_status={"funasr-health": 503}),
        _row("2026-09-11T17:30:00Z", http_status={"funasr-health": 503,
                                                  "cosyvoice-health": 503}),
        _row("2026-09-11T17:45:00Z"),
    ])
    output = tmp_path / "out"
    assert _execute(source, output) == mi.EXIT_OK
    streaks = _insights(output)["streaks"]
    assert isinstance(streaks, dict)
    assert streaks["endpoint_current_failure_streaks"] == {
        "web-root": 0, "web-login": 0, "api-health": 0,
        "funasr-health": 0, "cosyvoice-health": 0,
    }
    stats = _insights(output)["endpoint_latency_ms"]
    assert isinstance(stats, dict)
    assert stats["funasr-health"]["non_ok_count"] == 2  # type: ignore[index]


def test_endpoint_open_failure_streak_counts_trailing(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    _write_history(source, [
        _row("2026-09-11T17:00:00Z"),
        _row("2026-09-11T17:15:00Z", http_status={"funasr-health": 503}),
        _row("2026-09-11T17:30:00Z", http_status={"funasr-health": 503}),
    ])
    output = tmp_path / "out"
    assert _execute(source, output) == mi.EXIT_OK
    streaks = _insights(output)["streaks"]
    assert isinstance(streaks, dict)
    assert streaks["endpoint_current_failure_streaks"]["funasr-health"] == 2


def test_latest_sample_fields(tmp_path) -> None:
    data = _scenario_insights(tmp_path)
    latest = data["latest_sample"]
    assert isinstance(latest, dict)
    assert latest["collected_at"] == "2026-09-11T18:15:00Z"
    assert latest["overall_status"] == "ok"
    assert latest["threshold_counts"] == {"ok": 34, "warn": 0, "critical": 0}
    assert latest["service_health"] == {s: "healthy" for s in SERVICES}
    assert latest["endpoint_http_status"] == {e: 200 for e in ENDPOINTS}


def test_service_summary_totals(tmp_path) -> None:
    data = _scenario_insights(tmp_path)
    services = data["service_summary"]
    assert isinstance(services, dict)
    assert services["api"] == {"restart_total": 1, "log_error_total": 7, "unhealthy_samples": 1}
    assert services["redis"] == {"restart_total": 5, "log_error_total": 30, "unhealthy_samples": 1}
    assert services["postgres"] == {"restart_total": 0, "log_error_total": 0, "unhealthy_samples": 0}


def test_single_sample_edge_shape(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    _write_history(source, [_row("2026-09-11T17:39:20Z", latency_ms=12.5)])
    output = tmp_path / "out"
    assert _execute(source, output) == mi.EXIT_OK
    data = _insights(output)
    assert data["duration_seconds"] == 0
    assert data["availability_ratio"] == 1.0
    stats = data["endpoint_latency_ms"]
    assert isinstance(stats, dict)
    assert stats["web-root"] == {"samples": 1, "non_ok_count": 0, "min": 12.5,
                                 "p50": 12.5, "p95": 12.5, "max": 12.5}
    streaks = data["streaks"]
    assert isinstance(streaks, dict)
    assert streaks["current"] == {"status": "ok", "length": 1}
    assert streaks["longest_non_ok"] == {"length": 0, "first_at": None, "last_at": None}


# ---------------------------------------------------------------- 原子写与顺序


def test_validation_failure_zero_write_calls(monkeypatch) -> None:
    store = FakeStore(files={"history.jsonl": b"garbage\n"}, dirs=("src",))
    rc = _fake_execute(monkeypatch, store, Path("/fake/src"), Path("/fake/out"))
    assert rc == mi.EXIT_REFUSED
    assert store.write_calls == []


def test_first_write_failure_visible_refusal(monkeypatch) -> None:
    store = FakeStore(files={"history.jsonl": _history_text([_row("2026-09-11T17:00:00Z")])},
                      dirs=("src",), fail_write_index=1)
    rc = _fake_execute(monkeypatch, store, Path("/fake/src"), Path("/fake/out"))
    assert rc == mi.EXIT_REFUSED
    assert store.write_calls == [mi.INSIGHTS_JSON_NAME]


def test_second_write_failure_visible_refusal(monkeypatch) -> None:
    store = FakeStore(files={"history.jsonl": _history_text([_row("2026-09-11T17:00:00Z")])},
                      dirs=("src",), fail_write_index=2)
    rc = _fake_execute(monkeypatch, store, Path("/fake/src"), Path("/fake/out"))
    assert rc == mi.EXIT_REFUSED
    assert store.write_calls == [mi.INSIGHTS_JSON_NAME, mi.INSIGHTS_MD_NAME]
    assert mi.INSIGHTS_JSON_NAME in store.files  # 第一输出已原子落盘
    assert mi.INSIGHTS_MD_NAME not in store.files


@pytest.mark.parametrize("fail_on", [1, 2])
def test_os_replace_failure_no_tmp_residue_real_fs(monkeypatch, tmp_path, fail_on: int) -> None:
    source = tmp_path / "src"
    source.mkdir()
    _write_history(source, [_row("2026-09-11T17:00:00Z")])
    output = tmp_path / "out"
    real_replace = os.replace
    counter = {"n": 0}

    def _failing_replace(src, dst):  # type: ignore[no-untyped-def]
        counter["n"] += 1
        if counter["n"] == fail_on:
            raise OSError("simulated replace failure")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _failing_replace)
    rc = _execute(source, output)
    assert rc == mi.EXIT_REFUSED
    assert not any(name.startswith(".") and name.endswith(".tmp")
                   for name in os.listdir(output))
    if fail_on == 1:
        assert not (output / mi.INSIGHTS_JSON_NAME).exists()


def test_success_no_tmp_residue(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    _write_history(source, [_row("2026-09-11T17:00:00Z")])
    output = tmp_path / "out"
    assert _execute(source, output) == mi.EXIT_OK
    assert sorted(p.name for p in output.iterdir()) == sorted(
        [mi.INSIGHTS_JSON_NAME, mi.INSIGHTS_MD_NAME])


# ---------------------------------------------------------------- 报告卫生


def test_stdout_no_absolute_paths(tmp_path, capsys) -> None:
    source = tmp_path / "src"
    source.mkdir()
    _write_history(source, [_row("2026-09-11T17:00:00Z")])
    rc = _execute(source, tmp_path / "out")
    assert rc == mi.EXIT_OK
    out = capsys.readouterr().out
    assert str(tmp_path) not in out
    assert str(REPO_ROOT) not in out
    assert ":\\" not in out and ":/" not in out  # 无盘符形态


def test_refusal_does_not_echo_bad_values(tmp_path, capsys) -> None:
    source = tmp_path / "src"
    source.mkdir()
    bad_stem = "monitor-ZZevil"
    _write_history(source, [_row("2026-09-11T17:00:00Z", stem=bad_stem)])
    rc = _execute(source, tmp_path / "out")
    assert rc == mi.EXIT_REFUSED
    assert bad_stem not in capsys.readouterr().out


def test_outputs_free_of_paths_and_secrets(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    _write_history(source, [_scenario_rows()[2]])  # warn 样本
    output = tmp_path / "out"
    assert _execute(source, output) == mi.EXIT_OK
    for name in (mi.INSIGHTS_JSON_NAME, mi.INSIGHTS_MD_NAME):
        text = (output / name).read_text(encoding="utf-8")
        assert str(tmp_path) not in text
        assert str(REPO_ROOT) not in text
        assert ":\\" not in text
        assert MARK_TOKEN not in text


def test_markdown_renders_sections_and_boundaries(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    _write_history(source, _scenario_rows())
    output = tmp_path / "out"
    assert _execute(source, output) == mi.EXIT_OK
    md = (output / mi.INSIGHTS_MD_NAME).read_text(encoding="utf-8")
    for section in ("## degraded/critical 事件", "## 端点延迟", "## 服务汇总",
                    "## 连续失败/恢复", "## 最近样本", "边界："):
        assert section in md, section
    assert "不构成 production readiness 宣称" in md
    assert "零子进程" in md and "零网络" in md
    assert "availability=4/6" in md
    assert "api-health" in md and "redis" in md
    assert "无 degraded/critical 事件" not in md


def test_markdown_no_events_variant(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    _write_history(source, [_row("2026-09-11T17:00:00Z")])
    output = tmp_path / "out"
    assert _execute(source, output) == mi.EXIT_OK
    md = (output / mi.INSIGHTS_MD_NAME).read_text(encoding="utf-8")
    assert "无 degraded/critical 事件" in md
    assert "6 服务全 healthy" in md and "5 端点全 200" in md


# ---------------------------------------------------------------- CLI 与文档


def test_cli_defaults_registered() -> None:
    parser = mi.build_parser()
    args = parser.parse_args([])
    assert args.source == mi.DEFAULT_SOURCE
    assert str(mi.DEFAULT_SOURCE).endswith("m14-13-monitor-history-retention")
    assert args.output_dir == mi.DEFAULT_OUTPUT_DIR
    assert args.event_limit == mi.DEFAULT_EVENT_LIMIT
    assert args.execute is False
    assert args.confirm == ""


def test_ops_readme_documents_tool() -> None:
    text = OPS_README.read_text(encoding="utf-8")
    assert "monitoring_insights.py" in text
    assert mi.CONFIRM_PHRASE in text
    assert "m14-13-monitor-history-retention" in text
    assert "m14-15-monitoring-insights" in text


def test_execute_happy_path_stdout_summary(tmp_path, capsys) -> None:
    source = tmp_path / "src"
    source.mkdir()
    _write_history(source, _scenario_rows())
    rc = _execute(source, tmp_path / "out")
    assert rc == mi.EXIT_OK
    out = capsys.readouterr().out
    assert "样本: 6" in out
    assert "ok=4 warn=1 critical=1" in out
    assert "共 2 条" in out
    assert mi.INSIGHTS_JSON_NAME in out and mi.INSIGHTS_MD_NAME in out
