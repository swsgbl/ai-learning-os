r"""M14-13 tools/ops/monitoring_history.py 契约测试：监控历史索引 + 有界留存
+ 趋势摘要的安全边界（零真实容器面/零网络/零计划任务/零 env 读取）。

覆盖（全部 I/O 经真实临时目录或 FakeStore 注入；绝不触碰 canonical 仓库
与真实 .verify 目录）：
- 结构契约：源码零子进程/零网络/零 env/零墙钟 token；socket+subprocess
  双阻断下端到端照常成功；
- 发现与路径防御：仅 monitor-*.json 入选（plan-*、md、无关名忽略）；
  glob 命中但 stem 不合规（含日历非法日期/多余段/缺位）fail-closed 且
  被拒名不回显；源文件/源目录/输出路径 symlink 拒绝（真实文件面：mkdir
  先于拒绝会穿越 symlinked 输出祖先建目录——symlink 目标零写入、输出
  路径零创建）；源目录缺失/零源拒绝且输出零写入；
- 严格校验（fail-closed，输出零写入）：not-json、schema_version/tool/
  milestone/mode、时间戳格式 ×4 与顺序、project 白名单、overall
  incomplete、partial、阈值计数类型、compose 服务缺失、容器事实
  （restart 非法 ×3）、端点缺失/failed/非有限延迟 ×2/负延迟、日志
  error_total 非法；
- 接受面：ok/warn/critical 完整样本全部入档；单样本端到端（记录字段
  全集 + SHA-256 与源字节一致）；多样本确定性排序；同哈希去重（保留
  (collected_dt, stem) 最小者，duplicate_count 显式）；同 (project,
  collected_at) 不同哈希 → conflicting-duplicate 拒绝；
- 历史 incomplete 工件（M14-20）：识别 M14-12 monitor 自产的历史
  incomplete 类（完整 monitor 身份 + overall_status=incomplete +
  partial=true 共现对）→ 跳过不入档（计数显式于摘要与 stdout；源文件
  逐字节不变）；完整 ok/warn/critical 样本照常入档；候选全为
  incomplete → no-complete-sources 拒绝；识别要求完整 monitor 身份
  （tool/milestone/schema_version/mode 任一不符仍 fail-closed）；跳过
  件不参与去重/留存计数；
- 留存：保留最新 N + omitted_older_count + oldest/newest 边界；N 大于
  样本数全保留；CLI 超界（0/5001）拒绝；源文件与目录在运行前后逐字节
  不变（绝不改动/删除源工件）；
- 摘要（确定性、零墙钟）：状态计数/availability/degraded/critical、
  first/last、逐端点延迟 min/p50/p95/max（nearest-rank）、逐服务
  restart/日志 error 总计、duplicate/omitted 计数、生成时间戳取自最新
  源样本；两次运行输出逐字节相同；Markdown 表与边界注记；原始日志行/
  密钥形态标记值绝不进入输出（源 boundaries 字段投毒实证）；
- 原子写与顺序：全部输入校验通过前零写入（FakeStore 记录）；写失败
  （第 1/第 2 次）可见拒绝、无 tmp 残留（含 os.replace 失败的真实文件
  面）；成功后无 tmp 残留；
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
SCRIPT = REPO_ROOT / "tools" / "ops" / "monitoring_history.py"
OPS_README = REPO_ROOT / "tools" / "ops" / "README.md"

#: 标记值（注入源工件 boundaries 字段，断言绝不进入任何输出）
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


mh = _load_module(SCRIPT, "monitoring_history_under_test")


# ---------------------------------------------------------------- 样本工厂


def stem_for(iso: str) -> str:
    date, clock = iso.rstrip("Z").split("T")
    return "monitor-" + date.replace("-", "") + "-" + clock.replace(":", "")


def _report(collected_at: str, *, project: str = PROJECT, overall: str = "ok",
            partial: bool = False, latency_ms: float = 10.0, http_status: int = 200,
            restarts: int = 0, log_errors: int = 0, health: str = "healthy",
            schema_version: int = 1, tool: str = "tools/ops/production_monitor.py",
            milestone: str = "M14-12", mode: str = "execute",
            started_at: str | None = None, ended_at: str | None = None,
            counts: dict[str, int] | None = None,
            drop_service: str | None = None, drop_endpoint: str | None = None,
            boundaries: list[str] | None = None,
            restart_evaluation: dict[str, object] | None = None) -> dict[str, object]:
    """canonical 形状的合成 monitor 报告（与 monitor-20260911-173920.json 同构）。"""
    services = {s: {"health": health, "state": "running"} for s in SERVICES if s != drop_service}
    containers = {s: {"status": "ok", "failure_category": None, "error_class": None,
                      "name": f"{PROJECT}-{s}-1", "state": "running", "health": health,
                      "restart_count": restarts, "image": f"aios/{s}:tag",
                      "started_at": "2026-09-11T00:00:00Z"}
                  for s in SERVICES if s != drop_service}
    endpoints = {e: {"status": "ok", "failure_category": None, "error_class": None,
                     "http_status": http_status, "latency_ms": latency_ms}
                 for e in ENDPOINTS if e != drop_endpoint}
    logs = {s: {"status": "ok", "failure_category": None, "error_class": None,
                "lines_scanned": 5,
                "levels": {"fatal": 0, "error": log_errors, "critical": 0,
                           "warning": 0, "traceback": 0, "panic": 0},
                "error_total": log_errors}
            for s in SERVICES if s != drop_service}
    threshold: dict[str, object] = {"counts": counts if counts is not None
                                    else {"ok": 34, "warn": 0, "critical": 0}}
    if restart_evaluation is not None:  # M14-23 加法字段（在场即校验，缺省=旧工件）
        threshold["restart_evaluation"] = restart_evaluation
    return {
        "schema_version": schema_version, "tool": tool, "milestone": milestone,
        "mode": mode,
        "started_at_utc": started_at if started_at is not None else collected_at,
        "ended_at_utc": ended_at if ended_at is not None else collected_at,
        "config": {"project": project, "profile": "local"},
        "boundaries": boundaries if boundaries is not None else ["read-only collection"],
        "collectors": {
            "compose_ps": {"status": "ok", "failure_category": None, "error_class": None,
                           "services": services},
            "containers": {"status": "ok", "per_service": containers},
            "endpoints": {"status": "ok", "per_endpoint": endpoints},
            "logs": {"status": "ok", "per_service": logs},
        },
        "partial": partial,
        "threshold_results": threshold,
        "overall_status": overall,
        "monitoring_ready": overall == "ok",
    }


def _write_sample(directory: Path, iso: str, *, stem: str | None = None, **kwargs) -> Path:
    payload = kwargs.pop("payload", None)
    text = payload if payload is not None else json.dumps(_report(iso, **kwargs))
    path = directory / f"{stem or stem_for(iso)}.json"
    path.write_text(text, encoding="utf-8")
    return path


def _run_cli(monkeypatch: pytest.MonkeyPatch | None, source: Path, output: Path,
             *extra: str) -> int:
    return mh.main(["--source-dir", str(source), "--output-dir", str(output), *extra])


def _records(output: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in
            (output / "history.jsonl").read_text(encoding="utf-8").splitlines()]


def _block_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("subprocess is forbidden in this test")

    monkeypatch.setattr(subprocess_module, "run", _forbidden)

    def _no_socket(*args: object, **kwargs: object) -> None:
        raise AssertionError("network is forbidden in this test")

    monkeypatch.setattr(socket, "socket", _no_socket)


# ---------------------------------------------------------------- FakeStore


class FakeStore:
    """伪文件面：basename 键控（源/输出同一命名空间）；可注入 symlink 与写失败。"""

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


def _fake_run(monkeypatch: pytest.MonkeyPatch, store: FakeStore, source: Path,
              output: Path, retention: int = 500) -> int:
    monkeypatch.setattr(mh, "RealStore", lambda: store)
    return mh.main(["--source-dir", str(source), "--output-dir", str(output),
                    "--retention", str(retention)])


# ---------------------------------------------------------------- 结构契约


def test_source_contract_forbidden_tokens() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for token in ("subprocess", "socket", "urllib", "http.client", "os.system",
                  "Popen", "environ", "requests", "urlopen", "getenv", "docker",
                  "datetime.now", "utcnow", "time.time"):
        assert token not in source, f"禁止出现的字面量: {token}"
    assert "os.replace" in source  # 唯一落盘机制：原子替换


def test_no_subprocess_no_network_end_to_end(monkeypatch, tmp_path) -> None:
    _block_side_effects(monkeypatch)
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_sample(source, "2026-09-11T17:39:20Z")
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_OK
    assert (output / "history.jsonl").is_file()


# ---------------------------------------------------------------- 发现 / 路径防御


def test_discovers_only_monitor_json_and_ignores_others(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_sample(source, "2026-09-11T17:39:20Z")
    (source / "plan-20260911-173920.json").write_text("{}", encoding="utf-8")
    (source / "monitor-20260911-173920.md").write_text("md", encoding="utf-8")
    (source / "README.md").write_text("note", encoding="utf-8")
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_OK
    records = _records(output)
    assert len(records) == 1
    assert records[0]["source_stem"] == "monitor-20260911-173920"


@pytest.mark.parametrize("bad_name", [
    "monitor-evil.json",
    "monitor-20260911.json",
    "monitor-20260911-1739.json",
    "monitor-20261311-173920.json",   # 日历非法（13 月）
    "monitor-20260911-246020.json",   # 日历非法（24:60）
    "monitor-x2026091-173920.json",
])
def test_nonallowlisted_name_refused_no_echo(monkeypatch, tmp_path, capsys,
                                             bad_name: str) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_sample(source, "2026-09-11T17:39:20Z")
    (source / bad_name).write_text("{}", encoding="utf-8")
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_REFUSED
    assert not output.exists()  # 输出零写入
    assert bad_name not in capsys.readouterr().out  # 被拒名不回显


def test_symlink_source_file_refused(monkeypatch, tmp_path) -> None:
    real = tmp_path / "real.json"
    real.write_text(json.dumps(_report("2026-09-11T17:39:20Z")), encoding="utf-8")
    link = tmp_path / "monitor-20260911-173920.json"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlink 不可用（权限/平台）")
    rc = _run_cli(None, tmp_path, tmp_path / "out")
    assert rc == mh.EXIT_REFUSED
    assert not (tmp_path / "out").exists()


def test_symlinked_source_dir_refused_via_fake_store(monkeypatch, tmp_path) -> None:
    store = FakeStore(files={"monitor-20260911-173920.json": b"{}"},
                      dirs=("src",), symlinks=("src",))
    rc = _fake_run(monkeypatch, store, Path("/fake/src"), Path("/fake/out"))
    assert rc == mh.EXIT_REFUSED
    assert store.write_calls == []


def test_source_dir_missing_refused(monkeypatch) -> None:
    store = FakeStore(dirs=())
    rc = _fake_run(monkeypatch, store, Path("/fake/missing"), Path("/fake/out"))
    assert rc == mh.EXIT_REFUSED
    assert store.write_calls == []


def test_zero_sources_refused(monkeypatch) -> None:
    store = FakeStore(files={"plan-20260911-173920.json": b"{}"}, dirs=("src",))
    rc = _fake_run(monkeypatch, store, Path("/fake/src"), Path("/fake/out"))
    assert rc == mh.EXIT_REFUSED
    assert store.write_calls == []


def test_output_symlink_refused_via_fake_store(monkeypatch) -> None:
    payload = json.dumps(_report("2026-09-11T17:39:20Z")).encode("utf-8")
    store = FakeStore(files={"monitor-20260911-173920.json": payload},
                      dirs=("src", "out"), symlinks=("history.jsonl",))
    rc = _fake_run(monkeypatch, store, Path("/fake/src"), Path("/fake/out"))
    assert rc == mh.EXIT_REFUSED
    assert store.write_calls == []


def test_output_symlink_refused_real_filesystem(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    _write_sample(source, "2026-09-11T17:39:20Z")
    output = tmp_path / "out"
    output.mkdir()
    target = tmp_path / "real-target.md"
    target.write_text("x", encoding="utf-8")
    link = output / "history.jsonl"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink 不可用（权限/平台）")
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_REFUSED
    assert target.read_text(encoding="utf-8") == "x"  # symlink 目标零改写
    assert not list(output.glob("*.tmp"))


def test_ancestor_symlink_in_output_path_refused(monkeypatch, tmp_path) -> None:
    payload = json.dumps(_report("2026-09-11T17:39:20Z")).encode("utf-8")
    store = FakeStore(files={"monitor-20260911-173920.json": payload},
                      dirs=("src", "out"), symlinks=("out",))
    rc = _fake_run(monkeypatch, store, Path("/fake/src"), Path("/fake/out"))
    assert rc == mh.EXIT_REFUSED
    assert store.write_calls == []


def test_ancestor_symlink_output_mkdirs_not_followed_real_filesystem(tmp_path) -> None:
    """真实文件面：symlinked 输出祖先 + 尚不存在的 output_dir——mkdir 绝不
    穿越 symlink 建目录（拒绝时 symlink 目标零写入、输出路径零创建）。"""
    source = tmp_path / "src"
    source.mkdir()
    _write_sample(source, "2026-09-11T17:39:20Z")
    target = tmp_path / "elsewhere"
    target.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink 不可用（权限/平台）")
    rc = _run_cli(None, source, link / "out")
    assert rc == mh.EXIT_REFUSED
    assert list(target.iterdir()) == []  # symlink 目标零写入（未穿越建目录）
    assert not (link / "out").exists()  # 输出路径零创建
    assert link.is_symlink()  # 链接本身原样保留


# ---------------------------------------------------------------- 严格校验（fail-closed）


def _refused_for_payload(monkeypatch, tmp_path, payload: str) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    (source / "monitor-20260911-173920.json").write_text(payload, encoding="utf-8")
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_REFUSED
    assert not output.exists()  # 校验失败输出零写入


def test_not_json_refused(monkeypatch, tmp_path) -> None:
    _refused_for_payload(monkeypatch, tmp_path, "not json at all")


def test_json_array_not_object_refused(monkeypatch, tmp_path) -> None:
    _refused_for_payload(monkeypatch, tmp_path, "[1, 2, 3]")


@pytest.mark.parametrize("overrides", [
    {"schema_version": 2},
    {"tool": "tools/ops/other.py"},
    {"milestone": "M14-99"},
    {"mode": "plan"},
    {"project": "../evil"},
    {"project": ""},
    {"partial": True},
    {"overall": "incomplete"},
])
def test_schema_contract_violations_refused(monkeypatch, tmp_path, overrides: dict) -> None:
    _refused_for_payload(monkeypatch, tmp_path, json.dumps(_report("2026-09-11T17:39:20Z", **overrides)))


@pytest.mark.parametrize("started,ended", [
    ("2026-09-11 17:39:20", "2026-09-11T17:39:21Z"),   # 格式（无 T/Z）
    ("2026-09-11T17:39:20", "2026-09-11T17:39:21Z"),   # 缺 Z
    ("2026-09-11T17:39:20Z", 12345),                   # 非字符串
    ("2026-09-11T17:39:21Z", "2026-09-11T17:39:20Z"),  # 顺序（ended < started）
])
def test_timestamp_violations_refused(monkeypatch, tmp_path, started: str, ended: str) -> None:
    _refused_for_payload(monkeypatch, tmp_path,
                         json.dumps(_report("2026-09-11T17:39:20Z", started_at=started, ended_at=ended)))


@pytest.mark.parametrize("counts", [
    {"ok": -1, "warn": 0, "critical": 0},
    {"ok": "34", "warn": 0, "critical": 0},
    {"ok": True, "warn": 0, "critical": 0},   # bool 不是 int 计数
    {"ok": 34, "warn": 0},                    # 缺 critical
])
def test_threshold_count_violations_refused(monkeypatch, tmp_path, counts: dict) -> None:
    _refused_for_payload(monkeypatch, tmp_path,
                         json.dumps(_report("2026-09-11T17:39:20Z", counts=counts)))


def test_missing_compose_service_refused(monkeypatch, tmp_path) -> None:
    _refused_for_payload(monkeypatch, tmp_path,
                         json.dumps(_report("2026-09-11T17:39:20Z", drop_service="livekit")))


def test_missing_endpoint_refused(monkeypatch, tmp_path) -> None:
    _refused_for_payload(monkeypatch, tmp_path,
                         json.dumps(_report("2026-09-11T17:39:20Z", drop_endpoint="web-login")))


@pytest.mark.parametrize("restarts", [-1, True, "0"])
def test_restart_count_violations_refused(monkeypatch, tmp_path, restarts: object) -> None:
    report = _report("2026-09-11T17:39:20Z")
    report["collectors"]["containers"]["per_service"]["api"]["restart_count"] = restarts  # type: ignore[index]
    _refused_for_payload(monkeypatch, tmp_path, json.dumps(report))


@pytest.mark.parametrize("log_errors", [-3, True])
def test_log_error_total_violations_refused(monkeypatch, tmp_path, log_errors: object) -> None:
    report = _report("2026-09-11T17:39:20Z")
    report["collectors"]["logs"]["per_service"]["web"]["error_total"] = log_errors  # type: ignore[index]
    _refused_for_payload(monkeypatch, tmp_path, json.dumps(report))


def test_endpoint_failed_status_refused(monkeypatch, tmp_path) -> None:
    report = _report("2026-09-11T17:39:20Z")
    report["collectors"]["endpoints"]["per_endpoint"]["api-health"] = {  # type: ignore[index]
        "status": "failed", "failure_category": "connection-refused",
        "error_class": "ConnectionRefusedError", "http_status": None, "latency_ms": None,
    }
    _refused_for_payload(monkeypatch, tmp_path, json.dumps(report))


@pytest.mark.parametrize("latency", [float("inf"), float("nan"), -1.0, "10.0", True])
def test_nonfinite_or_bad_latency_refused(monkeypatch, tmp_path, latency: object) -> None:
    report = _report("2026-09-11T17:39:20Z")
    report["collectors"]["endpoints"]["per_endpoint"]["web-root"]["latency_ms"] = latency  # type: ignore[index]
    _refused_for_payload(monkeypatch, tmp_path, json.dumps(report))


def test_collector_not_ok_refused(monkeypatch, tmp_path) -> None:
    report = _report("2026-09-11T17:39:20Z", partial=True)
    report["collectors"]["compose_ps"]["status"] = "failed"  # type: ignore[index]
    _refused_for_payload(monkeypatch, tmp_path, json.dumps(report))


# ---------------------------------------------------------------- 历史 incomplete 工件（M14-20）


def _incomplete_report(collected_at: str, *, schema_version: int = 1,
                       tool: str = "tools/ops/production_monitor.py",
                       milestone: str = "M14-12", mode: str = "execute",
                       partial: bool = True) -> dict[str, object]:
    """canonical 形状的历史 incomplete 工件（与 2026-09-12 时期真实工件
    同构：cosyvoice-health 端点失败 → endpoints 采集器 failed →
    partial=true + overall_status=incomplete——monitor 契约中二者恒共现，
    且采集器事实可合法含 failed，故无法经完整校验，只能整件识别跳过）。"""
    services = {s: {"health": "healthy", "state": "running"} for s in SERVICES}
    containers = {s: {"status": "ok", "failure_category": None, "error_class": None,
                      "name": f"{PROJECT}-{s}-1", "state": "running", "health": "healthy",
                      "restart_count": 0, "image": f"aios/{s}:tag",
                      "started_at": "2026-09-11T00:00:00Z"}
                  for s in SERVICES}
    endpoints = {e: {"status": "ok", "failure_category": None, "error_class": None,
                     "http_status": 200, "latency_ms": 10.0}
                 for e in ENDPOINTS}
    endpoints["cosyvoice-health"] = {
        "status": "failed", "failure_category": "endpoint-unhealthy",
        "error_class": "ConnectError", "http_status": None, "latency_ms": None,
    }
    logs = {s: {"status": "ok", "failure_category": None, "error_class": None,
                "lines_scanned": 5,
                "levels": {"fatal": 0, "error": 0, "critical": 0,
                           "warning": 0, "traceback": 0, "panic": 0},
                "error_total": 0}
            for s in SERVICES}
    return {
        "schema_version": schema_version, "tool": tool, "milestone": milestone,
        "mode": mode,
        "started_at_utc": collected_at, "ended_at_utc": collected_at,
        "config": {"project": PROJECT, "profile": "local"},
        "boundaries": ["read-only collection"],
        "collectors": {
            "compose_ps": {"status": "ok", "failure_category": None, "error_class": None,
                           "services": services},
            "containers": {"status": "ok", "per_service": containers},
            "endpoints": {"status": "failed", "failure_category": "endpoint-unhealthy",
                          "error_class": None, "per_endpoint": endpoints},
            "logs": {"status": "ok", "per_service": logs},
        },
        "partial": partial,
        "threshold_results": {"counts": {"ok": 33, "warn": 0, "critical": 1}},
        "overall_status": "incomplete",
        "monitoring_ready": False,
    }


def test_historical_incomplete_skipped_complete_samples_indexed(tmp_path, capsys) -> None:
    """M14-20 根因回归：默认源目录混有 2026-09-12 时期历史 incomplete 工件
    时，完整 ok/warn 样本必须照常入档（跳过计数显式；源文件逐字节不变）。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    incomplete_paths = [
        _write_sample(source, "2026-09-12T17:37:46Z",
                      payload=json.dumps(_incomplete_report("2026-09-12T17:37:46Z"))),
        _write_sample(source, "2026-09-12T18:00:01Z",
                      payload=json.dumps(_incomplete_report("2026-09-12T18:00:01Z"))),
    ]
    _write_sample(source, "2026-09-11T17:39:20Z", overall="ok")
    _write_sample(source, "2026-09-13T01:04:14Z", overall="warn", latency_ms=1500.0)
    before = {p.name: p.read_bytes() for p in sorted(source.iterdir())}
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_OK
    records = _records(output)
    assert [r["overall_status"] for r in records] == ["ok", "warn"]  # 时间升序，仅完整样本
    assert all(r["partial"] is False for r in records)
    summary_text = (output / "history-summary.md").read_text(encoding="utf-8")
    assert "历史不完整工件：跳过 2 条" in summary_text
    assert "发现 2 / 保留 2" in summary_text  # discovered 仅计完整样本
    out = capsys.readouterr().out
    assert "跳过历史 incomplete 工件: 2" in out
    after = {p.name: p.read_bytes() for p in sorted(source.iterdir())}
    assert before == after  # 含跳过件在内，源文件逐字节不变、零删除
    assert len(incomplete_paths) == 2


def test_all_incomplete_sources_refused_no_complete_samples(tmp_path, capsys) -> None:
    """候选全为历史 incomplete（零完整样本）→ no-complete-sources 拒绝，
    输出零写入（fail-closed，绝不从空集构建历史）。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_sample(source, "2026-09-12T17:37:46Z",
                  payload=json.dumps(_incomplete_report("2026-09-12T17:37:46Z")))
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_REFUSED
    assert not output.exists()
    assert "no-complete-sources" in capsys.readouterr().out


@pytest.mark.parametrize("overrides", [
    {"tool": "tools/ops/other.py"},
    {"milestone": "M14-99"},
    {"schema_version": 2},
    {"mode": "plan"},
    {"partial": False},   # incomplete + partial=false = 矛盾形态，不属识别类
])
def test_recognition_requires_full_monitor_identity(monkeypatch, tmp_path,
                                                    overrides: dict) -> None:
    """识别类要求完整 monitor 身份 + 共现对：任一不符的 incomplete 工件
    仍走严格校验 fail-closed（即使同目录存在完整样本也整体拒绝）。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_sample(source, "2026-09-12T17:37:46Z",
                  payload=json.dumps(_incomplete_report("2026-09-12T17:37:46Z", **overrides)))
    _write_sample(source, "2026-09-11T17:39:20Z")
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_REFUSED
    assert not output.exists()  # 未识别类 = malformed，输出零写入


def test_skipped_incomplete_not_in_dedup_or_discovered(tmp_path) -> None:
    """跳过件不参与去重/冲突/发现计数：两份逐字节相同的 incomplete 工件
    各自跳过（计 2），duplicate_count 恒 0（去重仅作用于完整样本）。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    payload = json.dumps(_incomplete_report("2026-09-12T17:37:46Z"))
    _write_sample(source, "2026-09-12T17:37:46Z", payload=payload)
    _write_sample(source, "2026-09-12T23:59:59Z", payload=payload,
                  stem="monitor-20260912-235959")
    _write_sample(source, "2026-09-13T01:04:14Z")
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_OK
    assert len(_records(output)) == 1
    summary_text = (output / "history-summary.md").read_text(encoding="utf-8")
    assert "历史不完整工件：跳过 2 条" in summary_text
    assert "重复内容 0 条" in summary_text


def test_retention_bounds_apply_to_complete_samples_only(tmp_path) -> None:
    """留存仅作用于完整样本：3 完整 + 2 incomplete，retention=2 → 保留最新
    2 条完整、省略 1、跳过 2（三个计数互不混计）。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    for hour in (0, 1, 2):
        _write_sample(source, f"2026-09-13T0{hour}:00:00Z")
    _write_sample(source, "2026-09-12T17:37:46Z",
                  payload=json.dumps(_incomplete_report("2026-09-12T17:37:46Z")))
    _write_sample(source, "2026-09-12T18:00:01Z",
                  payload=json.dumps(_incomplete_report("2026-09-12T18:00:01Z")))
    rc = _run_cli(None, source, output, "--retention", "2")
    assert rc == mh.EXIT_OK
    records = _records(output)
    assert [r["collected_at"] for r in records] == [
        "2026-09-13T01:00:00Z", "2026-09-13T02:00:00Z",
    ]
    summary_text = (output / "history-summary.md").read_text(encoding="utf-8")
    assert "省略更早 1 条" in summary_text
    assert "历史不完整工件：跳过 2 条" in summary_text


# ---------------------------------------------------------------- 接受面 / 排序 / 去重


def test_valid_single_sample_record_fields_and_hash(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    path = _write_sample(source, "2026-09-11T17:39:20Z", latency_ms=7.088, restarts=0)
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_OK
    records = _records(output)
    assert len(records) == 1
    record = records[0]
    assert set(record) == {
        "schema_version", "artifact_sha256", "source_stem", "collected_at", "project",
        "overall_status", "partial", "threshold_counts", "compose_service_health",
        "restart_counts", "endpoints", "log_error_totals",
    }
    assert record["artifact_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert record["collected_at"] == "2026-09-11T17:39:20Z"
    assert record["partial"] is False
    assert record["compose_service_health"] == {s: "healthy" for s in SERVICES}
    assert record["endpoints"]["web-root"] == {"http_status": 200, "latency_ms": 7.088}
    assert not list(output.glob("*.tmp"))


def test_valid_multiple_samples_sorted_by_collected_time(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    # 文件名（字典序）与时间序故意交叉：时间序 16:00 < 17:39 < 18:00
    for iso in ("2026-09-11T18:00:00Z", "2026-09-11T16:00:00Z", "2026-09-11T17:39:20Z"):
        _write_sample(source, iso)
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_OK
    assert [r["collected_at"] for r in _records(output)] == [
        "2026-09-11T16:00:00Z", "2026-09-11T17:39:20Z", "2026-09-11T18:00:00Z",
    ]


def test_warn_and_critical_samples_accepted_into_history(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_sample(source, "2026-09-11T16:00:00Z", overall="ok")
    _write_sample(source, "2026-09-11T17:00:00Z", overall="warn", latency_ms=1500.0)
    _write_sample(source, "2026-09-11T18:00:00Z", overall="critical", http_status=503)
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_OK
    statuses = [r["overall_status"] for r in _records(output)]
    assert statuses == ["ok", "warn", "critical"]


def test_duplicate_identical_hash_deduped_deterministic_replica(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    payload = json.dumps(_report("2026-09-11T17:39:20Z"))
    _write_sample(source, "2026-09-11T17:39:20Z", payload=payload)
    _write_sample(source, "2026-09-11T17:39:20Z",
                  payload=payload, stem="monitor-20260911-235959")
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_OK
    records = _records(output)
    assert len(records) == 1
    # 去重保留 (collected_dt, stem) 最小者——确定性代表
    assert records[0]["source_stem"] == "monitor-20260911-173920"


def test_conflicting_duplicate_same_slot_different_hash_refused(monkeypatch, tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_sample(source, "2026-09-11T17:39:20Z", latency_ms=7.0)
    _write_sample(source, "2026-09-11T17:39:20Z", latency_ms=9.0,
                  stem="monitor-20260911-175900")
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_REFUSED
    assert not output.exists()


# ---------------------------------------------------------------- 留存


def test_retention_keeps_newest_with_omission_and_boundaries(tmp_path, capsys) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    times = [f"2026-09-11T1{hour}:00:00Z" for hour in (0, 1, 2, 3, 4)]
    for iso in times:
        _write_sample(source, iso)
    rc = _run_cli(None, source, output, "--retention", "2")
    assert rc == mh.EXIT_OK
    records = _records(output)
    assert [r["collected_at"] for r in records] == [times[3], times[4]]
    summary_text = (output / "history-summary.md").read_text(encoding="utf-8")
    assert "省略更早 3 条" in summary_text
    assert times[3] in summary_text and times[4] in summary_text
    out = capsys.readouterr().out
    assert "省略更早" in out and "保留 2 条" in out


def test_retention_larger_than_samples_keeps_all(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_sample(source, "2026-09-11T16:00:00Z")
    _write_sample(source, "2026-09-11T17:00:00Z")
    rc = _run_cli(None, source, output, "--retention", "500")
    assert rc == mh.EXIT_OK
    summary_text = (output / "history-summary.md").read_text(encoding="utf-8")
    assert "省略更早 0 条" in summary_text and "保留 2" in summary_text


@pytest.mark.parametrize("bad", ["0", "5001", "-1"])
def test_retention_bounds_refused_cli(tmp_path, bad: str) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_sample(source, "2026-09-11T16:00:00Z")
    rc = _run_cli(None, source, output, "--retention", bad)
    assert rc == mh.EXIT_REFUSED
    assert not output.exists()


def test_retention_hard_max_boundary_accepted(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_sample(source, "2026-09-11T16:00:00Z")
    rc = _run_cli(None, source, output, "--retention", "5000")  # 硬顶本身合法
    assert rc == mh.EXIT_OK


def test_sources_never_mutated_or_deleted(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    paths = [_write_sample(source, f"2026-09-11T1{hour}:00:00Z") for hour in (0, 1, 2)]
    before = {p.name: p.read_bytes() for p in paths}
    rc = _run_cli(None, source, output, "--retention", "1")  # 即使省略旧样本也不动源
    assert rc == mh.EXIT_OK
    after = {p.name: p.read_bytes() for p in sorted(source.iterdir())}
    assert before == after  # 逐字节不变、零删除


# ---------------------------------------------------------------- 摘要（确定性 / 零墙钟）


def test_summary_stats_exact_nearest_rank(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_sample(source, "2026-09-11T16:00:00Z", overall="ok", latency_ms=5.0,
                  restarts=1, log_errors=2)
    _write_sample(source, "2026-09-11T17:00:00Z", overall="warn", latency_ms=10.0,
                  restarts=0, log_errors=0)
    _write_sample(source, "2026-09-11T18:00:00Z", overall="critical", latency_ms=20.0,
                  restarts=2, log_errors=4)
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_OK
    summary_text = (output / "history-summary.md").read_text(encoding="utf-8")
    assert "ok=1 warn=1 critical=1（availability=1，degraded=1）" in summary_text
    # nearest-rank：3 样本 p50=10.0、p95=max=20.0、min=5.0
    assert "| web-root | 3 | 5.000 | 10.000 | 20.000 | 20.000 |" in summary_text
    # restart/error 总计 = 六服务之和：restart (1+0+2)×6=18；日志 error (2+0+4)×6=36
    assert "restart 18；日志 error 36" in summary_text
    assert "2026-09-11T18:00:00Z" in summary_text  # 生成时间戳取自最新源


def test_percentile_nearest_rank_unit() -> None:
    assert mh.percentile([1, 2, 3, 4], 0.50) == 2
    assert mh.percentile([1, 2, 3, 4], 0.95) == 4
    assert mh.percentile([5], 0.99) == 5


def test_outputs_byte_identical_on_rerun(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_sample(source, "2026-09-11T16:00:00Z")
    _write_sample(source, "2026-09-11T17:00:00Z")
    assert _run_cli(None, source, output) == mh.EXIT_OK
    first = {p.name: p.read_bytes() for p in output.iterdir()}
    assert _run_cli(None, source, output) == mh.EXIT_OK
    second = {p.name: p.read_bytes() for p in output.iterdir()}
    assert first == second  # 零墙钟：逐字节可复现
    assert not list(output.glob("*.tmp"))


def test_generation_timestamp_from_newest_source_not_wall_clock(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_sample(source, "2026-09-11T16:00:00Z")
    _write_sample(source, "2026-09-12T09:15:00Z")
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_OK
    text = (output / "history-summary.md").read_text(encoding="utf-8")
    assert "生成时间戳（取自最新源样本 collected_at）：2026-09-12T09:15:00Z" in text


def test_summary_markdown_tables_and_boundaries(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_sample(source, "2026-09-11T16:00:00Z")
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_OK
    markdown = (output / "history-summary.md").read_text(encoding="utf-8")
    assert "# M14-13 监控历史摘要（schema_version=1）" in markdown
    assert "| 端点 | 样本数 | min(ms) | p50(ms) | p95(ms) | max(ms) |" in markdown
    assert "| 服务 | restart 总计 | 日志 error 总计 |" in markdown
    assert "原始工件永不改动/删除" in markdown
    assert "不构成 production readiness 宣称" in markdown


def test_marker_poison_in_boundaries_never_reaches_outputs(tmp_path) -> None:
    """源 boundaries 字段投毒标记值——记录/摘要绝不复制未知字段。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_sample(source, "2026-09-11T16:00:00Z",
                  boundaries=["leak " + MARK_TOKEN, "password=ZXpassmarker12345678"])
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_OK
    for name in ("history.jsonl", "history-summary.md"):
        assert MARK_TOKEN not in (output / name).read_text(encoding="utf-8")
        assert "ZXpassmarker" not in (output / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- 原子写 / 写序


def test_validation_happens_before_any_write(monkeypatch) -> None:
    payload = json.dumps(_report("2026-09-11T17:39:20Z", schema_version=7))
    store = FakeStore(files={"monitor-20260911-173920.json": payload.encode("utf-8")},
                      dirs=("src",))
    rc = _fake_run(monkeypatch, store, Path("/fake/src"), Path("/fake/out"))
    assert rc == mh.EXIT_REFUSED
    assert store.write_calls == []  # 校验失败零写入


def test_write_failure_first_call_refused(monkeypatch) -> None:
    payload = json.dumps(_report("2026-09-11T17:39:20Z")).encode("utf-8")
    store = FakeStore(files={"monitor-20260911-173920.json": payload}, dirs=("src",),
                      fail_write_index=1)
    rc = _fake_run(monkeypatch, store, Path("/fake/src"), Path("/fake/out"))
    assert rc == mh.EXIT_REFUSED
    assert store.write_calls == ["history.jsonl"]  # 第一个输出即失败
    assert "history-summary.md" not in store.files


def test_write_failure_second_call_leaves_first_complete(monkeypatch) -> None:
    payload = json.dumps(_report("2026-09-11T17:39:20Z")).encode("utf-8")
    store = FakeStore(files={"monitor-20260911-173920.json": payload}, dirs=("src",),
                      fail_write_index=2)
    rc = _fake_run(monkeypatch, store, Path("/fake/src"), Path("/fake/out"))
    assert rc == mh.EXIT_REFUSED
    assert store.write_calls == ["history.jsonl", "history-summary.md"]
    # 第一个输出为完整 JSONL（单文件原子性），第二个未落盘
    assert b"monitor-20260911-173920" in store.files["history.jsonl"]
    assert "history-summary.md" not in store.files


def test_real_store_replace_failure_no_tmp_leftover(monkeypatch, tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_sample(source, "2026-09-11T17:39:20Z")
    output.mkdir()
    real_replace = os.replace
    calls = {"n": 0}

    def _failing_replace(src: object, dst: object) -> None:
        calls["n"] += 1
        if calls["n"] == 2:  # 第二个输出（summary）替换失败
            raise OSError("simulated replace failure")
        return real_replace(src, dst)  # type: ignore[call-arg]

    monkeypatch.setattr(mh.os, "replace", _failing_replace)
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_REFUSED
    assert (output / "history.jsonl").is_file()      # 第一个输出完整落盘
    assert not (output / "history-summary.md").exists()
    assert not list(output.glob("*.tmp"))            # 零 tmp 残留


# ---------------------------------------------------------------- CLI


def test_cli_registration_defaults() -> None:
    parser = mh.build_parser()
    assert parser.prog == "monitoring_history.py"
    args = parser.parse_args([])
    assert args.source_dir == mh.DEFAULT_SOURCE_DIR
    assert args.output_dir == mh.DEFAULT_OUTPUT_DIR
    assert args.retention == mh.DEFAULT_RETENTION == 500
    assert mh.MAX_RETENTION == 5000 and mh.MIN_RETENTION == 1


def test_main_happy_path_stdout(tmp_path, capsys) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_sample(source, "2026-09-11T17:39:20Z")
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_OK
    out = capsys.readouterr().out
    assert "保留 1 条（发现 1，重复 0，省略更早 0）" in out
    assert "输出: history.jsonl / history-summary.md" in out


def test_ops_readme_documents_tool() -> None:
    text = OPS_README.read_text(encoding="utf-8")
    assert "monitoring_history.py" in text


# ---------------------------------------------------------------- M14-23 restart 增量评估入档


def _restart_eval(*, baseline_status: str = "ok", baseline_reason: str | None = None,
                  source_stem: str | None = "monitor-20260910-000000",
                  collected: str | None = "2026-09-10T00:00:00Z",
                  invalid_skipped: int = 0,
                  states: dict[str, str] | None = None,
                  reasons: dict[str, str] | None = None,
                  deltas: dict[str, int | None] | None = None) -> dict[str, object]:
    """monitor 形状的 restart_evaluation（M14-23 加法字段合成器）。"""
    return {
        "baseline": {"status": baseline_status, "reason": baseline_reason,
                     "source_stem": source_stem, "collected_at": collected,
                     "invalid_skipped_count": invalid_skipped},
        "per_service": {
            svc: {"state": (states or {}).get(svc, "ok"),
                  "reason": (reasons or {}).get(svc, "stable"),
                  "delta": (deltas or {}).get(svc, 0)}
            for svc in SERVICES
        },
    }


def test_record_includes_normalized_restart_evaluation(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_sample(source, "2026-09-13T01:00:00Z", restarts=3,
                  restart_evaluation=_restart_eval(
                      states={"api": "warn"}, reasons={"api": "delta"},
                      deltas={"api": 2}))
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_OK
    record = _records(output)[0]
    assert record["restart_evaluation"] == {
        "baseline_status": "ok",
        "baseline_reason": None,
        "baseline_source_stem": "monitor-20260910-000000",
        "baseline_collected_at": "2026-09-10T00:00:00Z",
        "baseline_invalid_skipped_count": 0,
        "states": {svc: "warn" if svc == "api" else "ok" for svc in SERVICES},
        "reasons": {svc: "delta" if svc == "api" else "stable" for svc in SERVICES},
        "deltas": {svc: 2 if svc == "api" else 0 for svc in SERVICES},
    }


def test_record_preserves_recreated_and_reset_reasons(tmp_path) -> None:
    """重建/重置事件（M14-23 一轮可见语义）在记录 reasons 中清晰留痕。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_sample(source, "2026-09-13T01:00:00Z",
                  restart_evaluation=_restart_eval(
                      states={"web": "warn", "livekit": "warn"},
                      reasons={"web": "container-recreated", "livekit": "counter-reset",
                               "api": "baseline-missing"},
                      deltas={"web": None, "livekit": None, "api": None}))
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_OK
    evaluation = _records(output)[0]["restart_evaluation"]
    assert isinstance(evaluation, dict)
    assert evaluation["reasons"]["web"] == "container-recreated"
    assert evaluation["reasons"]["livekit"] == "counter-reset"
    assert evaluation["reasons"]["api"] == "baseline-missing"
    assert evaluation["deltas"]["web"] is None  # 不可比增量绝不静默归零


def test_legacy_artifact_without_restart_evaluation_keeps_record_shape(tmp_path) -> None:
    """v1 旧工件（无 restart_evaluation）照常入档且记录不带新键——
    schema 向后兼容（旧记录/旧消费者零破坏）。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_sample(source, "2026-09-11T17:39:20Z")
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_OK
    record = _records(output)[0]
    assert "restart_evaluation" not in record


def _baseline_dict(status: object, reason: object, stem: object,
                   collected: object, invalid: int = 0) -> dict[str, object]:
    """monitor 形状的 baseline 元数据（status/reason/stem/collected 联动
    一致性测试的构造器）。"""
    return {"status": status, "reason": reason, "source_stem": stem,
            "collected_at": collected, "invalid_skipped_count": invalid}


_VALID_STEM = "monitor-20260910-000000"
_VALID_COLLECTED = "2026-09-10T00:00:00Z"


def _mutate_evaluation(*, baseline_status=None, baseline_not_dict=False,
                       source_stem=None, invalid_skipped=None, collected=None,
                       baseline_override: dict[str, object] | None = None,
                       drop_per_service=False, drop_service=None,
                       state=None, reason=None, delta=None):
    """构造「就地把 restart_evaluation 改坏」的 mutator（返回改坏后的 payload）。"""
    def apply(evaluation: dict[str, object]) -> object:
        if baseline_not_dict:
            evaluation["baseline"] = "not-a-dict"
            return evaluation
        if drop_per_service:
            evaluation.pop("per_service")
            return evaluation
        if baseline_override is not None:  # 整体替换 baseline（一致性用例）
            evaluation["baseline"] = dict(baseline_override)
            return evaluation
        baseline = evaluation["baseline"]
        assert isinstance(baseline, dict)
        if baseline_status is not None:
            baseline["status"] = baseline_status
        if source_stem is not None:
            baseline["source_stem"] = source_stem
        if invalid_skipped is not None:
            baseline["invalid_skipped_count"] = invalid_skipped
        if collected is not None:
            baseline["collected_at"] = collected
        per_service = evaluation["per_service"]
        assert isinstance(per_service, dict)
        if drop_service is not None:
            per_service.pop(drop_service)
            return evaluation
        item = per_service["api"]
        assert isinstance(item, dict)
        if state is not None:
            item["state"] = state
        if reason is not None:
            item["reason"] = reason
        if delta is not None:
            item["delta"] = delta
        return evaluation
    return apply


BAD_RESTART_EVALUATIONS = [
    ("baseline-status-vocab", _mutate_evaluation(baseline_status="bogus")),
    ("baseline-status-unhashable", _mutate_evaluation(baseline_status=["ok"])),
    ("baseline-not-dict", _mutate_evaluation(baseline_not_dict=True)),
    ("baseline-stem-allowlist", _mutate_evaluation(source_stem="monitor-evil")),
    ("invalid-skipped-negative", _mutate_evaluation(invalid_skipped=-1)),
    ("baseline-collected-format", _mutate_evaluation(collected="2026-09-10 00:00:00")),
    # supervisor R1：baseline 元数据一致性（status ↔ reason/stem/collected 联动）
    ("ok-with-reason", _mutate_evaluation(baseline_override=_baseline_dict(
        "ok", "nope", _VALID_STEM, _VALID_COLLECTED))),
    ("ok-missing-stem", _mutate_evaluation(baseline_override=_baseline_dict(
        "ok", None, None, _VALID_COLLECTED))),
    ("ok-missing-collected", _mutate_evaluation(baseline_override=_baseline_dict(
        "ok", None, _VALID_STEM, None))),
    ("missing-bad-reason", _mutate_evaluation(baseline_override=_baseline_dict(
        "missing", "bogus", None, None))),
    ("missing-none-reason", _mutate_evaluation(baseline_override=_baseline_dict(
        "missing", None, None, None))),
    ("missing-unhashable-reason", _mutate_evaluation(baseline_override=_baseline_dict(
        "missing", ["no-prior-artifacts"], None, None))),
    ("missing-with-stem", _mutate_evaluation(baseline_override=_baseline_dict(
        "missing", "no-prior-artifacts", _VALID_STEM, None))),
    ("missing-with-collected", _mutate_evaluation(baseline_override=_baseline_dict(
        "missing", "no-prior-artifacts", None, _VALID_COLLECTED))),
    ("unusable-bad-reason", _mutate_evaluation(baseline_override=_baseline_dict(
        "unusable", "bogus", None, None))),
    ("unusable-none-reason", _mutate_evaluation(baseline_override=_baseline_dict(
        "unusable", None, None, None))),
    ("unusable-with-stem", _mutate_evaluation(baseline_override=_baseline_dict(
        "unusable", "no-usable-prior-artifacts", _VALID_STEM, None))),
    ("unusable-with-collected", _mutate_evaluation(baseline_override=_baseline_dict(
        "unusable", "no-usable-prior-artifacts", None, _VALID_COLLECTED))),
    ("per-service-missing", _mutate_evaluation(drop_per_service=True)),
    ("service-facts-missing", _mutate_evaluation(drop_service="api")),
    ("state-vocab", _mutate_evaluation(state="bogus")),
    ("state-unhashable", _mutate_evaluation(state=["ok"])),
    ("reason-vocab", _mutate_evaluation(reason="bogus")),
    ("delta-negative", _mutate_evaluation(delta=-1)),
    ("delta-string", _mutate_evaluation(delta="2")),
    ("string-payload", lambda evaluation: "restart-evaluation-string"),
]


@pytest.mark.parametrize("name,mutator", BAD_RESTART_EVALUATIONS,
                         ids=[case[0] for case in BAD_RESTART_EVALUATIONS])
def test_malformed_restart_evaluation_refused_zero_writes(tmp_path, name: str,
                                                          mutator) -> None:
    """在场即严格校验（fail-closed）：任何违规形态输出零写入。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    payload = mutator(_restart_eval())
    _write_sample(source, "2026-09-13T01:00:00Z", restart_evaluation=payload)
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_REFUSED
    assert not output.exists()


@pytest.mark.parametrize("baseline_reason", [
    "artifact-dir-missing", "artifact-dir-unreadable", "no-prior-artifacts",
    "baseline-not-resolved",
])
def test_consistent_missing_status_baseline_accepted(tmp_path, baseline_reason: str) -> None:
    """supervisor R1 一致性接受面：status=missing + 固定词汇 reason + 空元
    数据（首轮/缺目录形态）照常入档。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_sample(source, "2026-09-13T01:00:00Z", restarts=1, overall="warn",
                  restart_evaluation=_restart_eval(
                      baseline_status="missing", baseline_reason=baseline_reason,
                      source_stem=None, collected=None,
                      states={"api": "warn"}, reasons={"api": "baseline-missing"},
                      deltas={"api": None}))
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_OK
    evaluation = _records(output)[0]["restart_evaluation"]
    assert isinstance(evaluation, dict)
    assert evaluation["baseline_status"] == "missing"
    assert evaluation["baseline_reason"] == baseline_reason
    assert evaluation["baseline_source_stem"] is None
    assert evaluation["baseline_collected_at"] is None


def test_consistent_unusable_status_baseline_accepted(tmp_path) -> None:
    """supervisor R1 一致性接受面：status=unusable + 固定 reason + 空元数据
    + 显式 invalid 计数照常入档。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_sample(source, "2026-09-13T01:00:00Z",
                  restart_evaluation=_restart_eval(
                      baseline_status="unusable",
                      baseline_reason="no-usable-prior-artifacts",
                      source_stem=None, collected=None, invalid_skipped=2))
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_OK
    evaluation = _records(output)[0]["restart_evaluation"]
    assert isinstance(evaluation, dict)
    assert evaluation["baseline_status"] == "unusable"
    assert evaluation["baseline_invalid_skipped_count"] == 2


def test_summary_restart_delta_and_event_totals(tmp_path) -> None:
    """摘要清晰区分累计 restart_totals（口径不变）与当轮增量/事件计数。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_sample(source, "2026-09-13T01:00:00Z", restarts=5, overall="warn",
                  restart_evaluation=_restart_eval(
                      states={"api": "warn"}, reasons={"api": "delta"},
                      deltas={"api": 2}))
    _write_sample(source, "2026-09-13T01:15:00Z", restarts=5,
                  restart_evaluation=_restart_eval(
                      source_stem="monitor-20260913-010000",
                      collected="2026-09-13T01:00:00Z"))
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_OK
    summary_text = (output / "history-summary.md").read_text(encoding="utf-8")
    # 累计口径不变：5+5 每服务 → 六服务合计 60
    assert "restart 60；日志 error 0" in summary_text
    # M14-23 增量/事件口径：仅 api 增量 2（样本 1 警报 + 样本 2 稳定）
    assert "增量数据样本 2/2" in summary_text
    assert "增量合计 2" in summary_text
    assert "事件合计 1" in summary_text
    assert "与上方累计 restart 总计口径不同" in summary_text


def test_summary_delta_totals_zero_for_legacy_only_sources(tmp_path) -> None:
    """全部为 v1 旧工件：增量样本计数 0、增量/事件合计 0（诚实区分
    「无数据」与「测得为零」），累计口径照常。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_sample(source, "2026-09-11T16:00:00Z", restarts=1)
    _write_sample(source, "2026-09-11T17:00:00Z", restarts=1)
    rc = _run_cli(None, source, output)
    assert rc == mh.EXIT_OK
    summary_text = (output / "history-summary.md").read_text(encoding="utf-8")
    assert "增量数据样本 0/2" in summary_text
    assert "增量合计 0" in summary_text and "事件合计 0" in summary_text
    assert "restart 12；日志 error 0" in summary_text  # 累计口径不受影响
