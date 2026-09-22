r"""M14-101 回归/性能契约测试：monitoring history 聚合的算法浪费消除
（run 内路径安全检查去重）+ monitoring pipeline 固定名产物的**同轮新鲜度**
（per-run 指纹基线——预存固定名文件绝不被引用为本轮产物）。

纪律（与既有 monitoring 契约测试同款）：零子进程（pipeline 侧经 FakeRunner
注入）、零网络、零 env 读取/变更、零计划任务、零真实 .verify 目录、零墙钟
（不 sleep、不依赖真实时间推进；规模用固定种子乱序、时间戳为合成常量）。

覆盖：
- 合成规模（真实临时文件面，1000+ 合成 monitor JSON）：
  - 1200 文件端到端：全部入档、严格升序、摘要计数正确；
  - 1200 合法 + 1 malformed 混入 → fail-closed 且输出零写入；
  - 1000 唯一 + 200 同内容副本（不同 stem）→ 去重 200、入档 1000；
  - 同 (project, collected_at) 不同哈希 → conflicting-duplicate 拒绝。
- 有界操作计数（计数 FakeStore，纯内存）：
  - N=300 与 N=900 两组：exists 调用次数**完全相等**（祖先检查不随候选
    数重复——与 N 无关）且 ≤ 常数上界；PathSafetyCache.ancestor_checks
    两组相等；is_symlink 调用增量 ≤ 每文件 2 次（自身 symlink 检查）+
    常数（内容 read 是每文件必经 I/O，不在计数面）。
  - fail-closed symlink 语义在缓存路径下保持：祖先 symlink →
    symlink-in-path；文件 symlink → symlink-source；源目录 symlink →
    symlink-target。
- 同轮产物 provenance（monitoring_pipeline，FakeRunner + 真实临时目录）：
  - history 步超时 + 预存旧固定名产物 → 两 entry
    note=stale-preexisting-not-cited、sha256=None（超时报告绝不引用旧
    history.jsonl 为本轮产物——M14-101 修复的生产歧义）；
  - history ok 重写两文件 → 带 SHA-256 引用（created-this-run/无 stale）；
  - history 超时但已写出一半（单文件刷新）→ 刷新者 cited、未刷新者
    stale（如实区分）；
  - history 失败（rc=2）+ 预存 → 同样零引用；
  - insights 失败 + 预存 → 同样零引用；
  - 步中删除预存文件 → removed-this-run note；
  - 基线不可读（stat OSError 注入）→ 全部现存固定名
    baseline-unreadable-not-cited（零哈希引用）；
  - 步骤后固定名为 symlink → symlink-not-hashed（零读取零哈希）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HISTORY_SCRIPT = REPO_ROOT / "tools" / "ops" / "monitoring_history.py"
PIPELINE_SCRIPT = REPO_ROOT / "tools" / "ops" / "monitoring_pipeline.py"


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


mh = _load_module(HISTORY_SCRIPT, "monitoring_history_m14_101_under_test")
mp = _load_module(PIPELINE_SCRIPT, "monitoring_pipeline_m14_101_under_test")

SERVICES = ("postgres", "redis", "minio", "api", "web", "livekit")
ENDPOINTS = ("web-root", "web-login", "api-health", "funasr-health", "cosyvoice-health")
PROJECT = "aios-m14-101-scale-rehearsal"

#: 合成时间戳基点（固定常量——零墙钟）；步长 37s 与 60 互质，分钟/秒位分布均匀
_SCALE_BASE = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
_SCALE_STEP_SECONDS = 37

#: 操作计数上界（与候选数 N 无关的常数；祖先链深度按 4 层构造 + 余量）
_EXISTS_CALLS_BOUND = 32
_IS_SYMLINK_PER_FILE_BOUND = 2


def _iso_at(index: int) -> str:
    stamp = _SCALE_BASE + timedelta(seconds=_SCALE_STEP_SECONDS * index)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def _stem_for(iso: str) -> str:
    date, clock = iso.rstrip("Z").split("T")
    return "monitor-" + date.replace("-", "") + "-" + clock.replace(":", "")


def _report(iso: str, *, overall: str = "ok", restarts: int = 0,
            log_errors: int = 0) -> dict[str, object]:
    """canonical 形状的合成 monitor 报告（与既有 history 契约测试同构，
    参数化字段使批量样本内容可微区分）。"""
    services = {s: {"health": "healthy", "state": "running"} for s in SERVICES}
    containers = {s: {"status": "ok", "failure_category": None, "error_class": None,
                      "name": f"{PROJECT}-{s}-1", "state": "running", "health": "healthy",
                      "restart_count": restarts, "image": f"aios/{s}:tag",
                      "started_at": "2026-01-01T00:00:00Z"}
                  for s in SERVICES}
    endpoints = {e: {"status": "ok", "failure_category": None, "error_class": None,
                     "http_status": 200, "latency_ms": 10.0}
                 for e in ENDPOINTS}
    logs = {s: {"status": "ok", "failure_category": None, "error_class": None,
                "lines_scanned": 5,
                "levels": {"fatal": 0, "error": log_errors, "critical": 0,
                           "warning": 0, "traceback": 0, "panic": 0},
                "error_total": log_errors}
            for s in SERVICES}
    return {
        "schema_version": 1, "tool": "tools/ops/production_monitor.py",
        "milestone": "M14-12", "mode": "execute",
        "started_at_utc": iso, "ended_at_utc": iso,
        "config": {"project": PROJECT, "profile": "local"},
        "boundaries": ["read-only collection"],
        "collectors": {
            "compose_ps": {"status": "ok", "failure_category": None,
                           "error_class": None, "services": services},
            "containers": {"status": "ok", "per_service": containers},
            "endpoints": {"status": "ok", "per_endpoint": endpoints},
            "logs": {"status": "ok", "per_service": logs},
        },
        "partial": False,
        "threshold_results": {"counts": {"ok": 34, "warn": 0, "critical": 0}},
        "overall_status": overall,
        "monitoring_ready": overall == "ok",
    }


def _write_scale_sources(directory: Path, count: int, *, shuffle_seed: int = 20260923
                         ) -> None:
    """count 个合成工件（时间戳严格递增派生；写入顺序固定种子乱序——
    文件系统枚举序不构成排序假设）。"""
    order = list(range(count))
    random.Random(shuffle_seed).shuffle(order)
    for index in order:
        iso = _iso_at(index)
        payload = json.dumps(_report(iso))
        (directory / f"{_stem_for(iso)}.json").write_text(payload, encoding="utf-8")


def _run_history_cli(source: Path, output: Path, *extra: str) -> int:
    return mh.main(["--source-dir", str(source), "--output-dir", str(output), *extra])


def _records(output: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in
            (output / "history.jsonl").read_text(encoding="utf-8").splitlines()]


# ---------------------------------------------------------------- 合成规模（真实文件面）


def test_scale_1200_synthetic_files_end_to_end(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_scale_sources(source, 1200)
    rc = _run_history_cli(source, output, "--retention", "5000")
    assert rc == mh.EXIT_OK
    records = _records(output)
    assert len(records) == 1200  # retention 5000 > 1200：全部保留
    collected = [str(record["collected_at"]) for record in records]
    assert collected == sorted(collected)  # 严格升序（合成时间戳唯一 → 无并列）
    assert collected[0] == _iso_at(0) and collected[-1] == _iso_at(1199)
    summary = (output / "history-summary.md").read_text(encoding="utf-8")
    assert "保留 1200" in summary  # 摘要计数与规模一致


def test_scale_one_malformed_among_1200_refused_zero_writes(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_scale_sources(source, 1200)
    (source / f"{_stem_for(_iso_at(6000))}.json").write_text(
        "{not-json", encoding="utf-8")
    rc = _run_history_cli(source, output, "--retention", "5000")
    assert rc == mh.EXIT_REFUSED
    assert not output.exists()  # fail-closed：输出零写入（目录级）


def test_scale_200_duplicate_replicas_deduped(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_scale_sources(source, 1000)
    # 200 份同内容副本（合法 stem 不同名——同哈希去重路径）
    payload = json.dumps(_report(_iso_at(0)))
    for index in range(200):
        iso = _iso_at(2000 + index)
        assert _stem_for(iso) != _stem_for(_iso_at(0))
        (source / f"{_stem_for(iso)}.json").write_text(payload, encoding="utf-8")
    rc = _run_history_cli(source, output, "--retention", "5000")
    assert rc == mh.EXIT_OK
    records = _records(output)
    assert len(records) == 1000
    summary = (output / "history-summary.md").read_text(encoding="utf-8")
    assert "重复内容 200 条" in summary  # duplicate_count 显式


def test_scale_conflicting_duplicate_refused(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_scale_sources(source, 1000)
    # 同 (project, collected_at) 不同哈希 → conflicting-duplicate
    conflicting = json.dumps(_report(_iso_at(0), restarts=5))
    (source / "monitor-20990101-000000.json").write_text(conflicting, encoding="utf-8")
    rc = _run_history_cli(source, output, "--retention", "5000")
    assert rc == mh.EXIT_REFUSED
    assert not output.exists()


# ---------------------------------------------------------------- 有界操作计数（纯内存）


class CountingFakeStore:
    """basename 键控伪文件面 + exists/is_symlink 调用计数（操作数断言面）。"""

    def __init__(self, *, files: dict[str, bytes] | None = None,
                 dirs: tuple[str, ...] = (), symlinks: tuple[str, ...] = ()) -> None:
        self.files: dict[str, bytes] = dict(files or {})
        self.dirs: set[str] = set(dirs)
        self.symlinks: set[str] = set(symlinks)
        self.exists_calls = 0
        self.is_symlink_calls = 0

    def exists(self, path: Path) -> bool:
        self.exists_calls += 1
        return path.name in self.files or path.name in self.dirs or path.name in self.symlinks

    def is_symlink(self, path: Path) -> bool:
        self.is_symlink_calls += 1
        return path.name in self.symlinks

    def list_dir(self, directory: Path) -> list[str]:
        return sorted(self.files)

    def read_bytes(self, path: Path) -> bytes:
        return self.files[path.name]

    def mkdirs(self, directory: Path) -> None:
        self.dirs.add(directory.name)

    def write_atomic(self, path: Path, text: str) -> None:
        self.files[path.name] = text.encode("utf-8")


def _fake_source_paths() -> tuple[Path, Path]:
    """(source_dir, output_dir)：共享 4 层祖先（驱动器根在 FakeStore 语义
    中不存在——验证不存在祖先永不缓存、后续仍可短路）。"""
    root = Path("D:/aios-scale/root")
    return root / "src", root / "out"


def _discover_at_scale(count: int, *, symlinks: tuple[str, ...] = ()
                       ) -> CountingFakeStore:
    source, _output = _fake_source_paths()
    files = {f"{_stem_for(_iso_at(index))}.json":
             json.dumps(_report(_iso_at(index))).encode("utf-8")
             for index in range(count)}
    store = CountingFakeStore(files=files, dirs=("src", "out", "root", "D:"),
                              symlinks=symlinks)
    safety = mh.PathSafetyCache(store)
    mh.discover_and_classify(store, source, safety=safety)
    return store, safety  # type: ignore[return-value]


def test_ancestor_checks_not_repeated_per_candidate() -> None:
    """核心性能契约：祖先路径安全检查的 exists 次数与候选数 N **无关**
    （N=300 与 N=900 完全相等）；每文件 is_symlink ≤ 2 次（自身检查）。"""
    store_300, safety_300 = _discover_at_scale(300)
    store_900, safety_900 = _discover_at_scale(900)
    # 祖先检查不随 N 重复：exists 调用次数两组完全相等且 ≤ 常数上界
    assert store_300.exists_calls == store_900.exists_calls
    assert store_900.exists_calls <= _EXISTS_CALLS_BOUND
    # PathSafetyCache 祖先检查计数同样与 N 无关（首文件链 walk 后全短路）
    assert safety_300.ancestor_checks == safety_900.ancestor_checks
    assert safety_900.ancestor_checks <= _EXISTS_CALLS_BOUND
    # 每文件自身 symlink 检查（发现层 + load_sample 防御层）≤ 2 次 + 常数
    delta_files, delta_calls = 900 - 300, (
        store_900.is_symlink_calls - store_300.is_symlink_calls)
    assert delta_calls <= _IS_SYMLINK_PER_FILE_BOUND * delta_files + 4


def test_fail_closed_symlink_semantics_preserved_under_cache() -> None:
    """缓存路径下 fail-closed symlink 拒绝语义逐项保持。"""
    # 祖先 symlink → symlink-in-path
    with pytest.raises(mh.HistoryError, match="symlink-in-path"):
        _discover_at_scale(4, symlinks=("root",))
    # 源目录自身 symlink → symlink-target
    with pytest.raises(mh.HistoryError, match="symlink-target"):
        _discover_at_scale(4, symlinks=("src",))
    # 候选文件 symlink：discover 层（checker）先拒 symlink-target；
    # load_sample 直调防御层独立拒绝 symlink-source（两层都 fail-closed）
    name = f"{_stem_for(_iso_at(0))}.json"
    source, _output = _fake_source_paths()
    store = CountingFakeStore(
        files={name: b"{}"}, dirs=("src", "root"), symlinks=(name,))
    with pytest.raises(mh.HistoryError, match="symlink-target"):
        mh.discover_and_classify(store, source)
    direct = CountingFakeStore(
        files={name: b"{}"}, dirs=("src", "root"), symlinks=(name,))
    with pytest.raises(mh.HistoryError, match="symlink-source"):
        mh.load_sample(direct, source, name)


# ---------------------------------------------------------------- pipeline 同轮产物 provenance


FAKE_PY = "C:/fake/python.exe"


class FakeRunner:
    """伪子进程面（与既有 pipeline 契约测试同款形态）。"""

    def __init__(self, *, monitor_rc: int = 0, history_rc: int = 0,
                 insights_rc: int = 0, history_exc: BaseException | None = None,
                 insights_exc: BaseException | None = None, on_call=None) -> None:
        self._rc = {"monitor": monitor_rc, "history": history_rc,
                    "insights": insights_rc}
        self._exc = {"monitor": None, "history": history_exc, "insights": insights_exc}
        self._on_call = on_call
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv, *, timeout: float = 60.0, encoding: str | None = None):
        tokens = tuple(str(item) for item in argv)
        self.calls.append(tokens)
        script = tokens[1]
        if script.endswith("production_monitor.py"):
            step = "monitor"
        elif script.endswith("monitoring_history.py"):
            step = "history"
        else:
            step = "insights"
        if self._on_call is not None:
            self._on_call(step)
        exc = self._exc[step]
        if exc is not None:
            raise exc
        return mp.CommandResult(tokens, self._rc[step], "", "")


class FakeClock:
    def __init__(self) -> None:
        self._perf = 0

    def utc_now_iso(self) -> str:
        return "2026-09-23T00:00:00Z"

    def stamp(self) -> str:
        return "20260923-000000"

    def perf(self) -> float:
        self._perf += 1
        return float(self._perf)


class _BaselineFailFs(mp.RealFs):
    """stat_fingerprint 恒抛 OSError——基线不可读分支注入。"""

    def stat_fingerprint(self, path: Path) -> tuple[int, int] | None:
        raise OSError(5, "simulated stat failure")


def _patch_stage_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path
                      ) -> tuple[Path, Path, Path]:
    monitor_dir = tmp_path / "m14-12-artifacts"
    history_dir = tmp_path / "m14-13-artifacts"
    insights_dir = tmp_path / "m14-15-artifacts"
    monitor_dir.mkdir()
    history_dir.mkdir()
    insights_dir.mkdir()
    monkeypatch.setattr(mp, "MONITOR_ARTIFACT_DIR", monitor_dir)
    monkeypatch.setattr(mp, "HISTORY_OUTPUT_DIR", history_dir)
    monkeypatch.setattr(mp, "INSIGHTS_OUTPUT_DIR", insights_dir)
    return monitor_dir, history_dir, insights_dir


def _execute(artifact_dir: Path, runner, *, clock=None, fs=None) -> int:
    return mp.main(["--execute", "--confirm", mp.CONFIRM_PHRASE,
                    "--artifact-dir", str(artifact_dir)],
                   runner=runner, clock=clock or FakeClock(), fs=fs,
                   python_exe=FAKE_PY)


def _pipeline_report(artifact_dir: Path) -> dict[str, object]:
    return json.loads((artifact_dir / "pipeline-20260923-000000.json")
                      .read_text(encoding="utf-8"))


def _write_monitor_artifact(monitor_dir: Path) -> None:
    (monitor_dir / "monitor-20260923-000000.json").write_text("{}", encoding="utf-8")


def test_history_timeout_preexisting_fixed_names_not_cited(
        monkeypatch, tmp_path) -> None:
    """M14-101 生产歧义修复回归：history 步超时被杀，上轮遗留的固定名
    history.jsonl/summary **绝不**被引用为本轮产物（stale note、零哈希）。"""
    monitor_dir, history_dir, _insights_dir = _patch_stage_dirs(monkeypatch, tmp_path)
    # 预存上轮产物（本轮开始前早已在盘上——指纹基线捕获后保持不变）
    (history_dir / "history.jsonl").write_text("OLD-ROWS-2026-09-22\n", encoding="utf-8")
    (history_dir / "history-summary.md").write_text("# OLD\n", encoding="utf-8")

    def on_call(step: str) -> None:
        if step == "monitor":
            _write_monitor_artifact(monitor_dir)
        # history 步被 timeout 杀死——零写入

    fake = FakeRunner(history_exc=mp.RunnerTimeout("stage-killed"), on_call=on_call)
    rc = _execute(tmp_path / "out", mp.StepRunner(fake, FAKE_PY))
    assert rc == mp.EXIT_STAGE_FAILED
    report = _pipeline_report(tmp_path / "out")
    history = report["stages"]["history"]
    assert history["status"] == "timeout"
    artifacts = history["artifacts"]
    assert isinstance(artifacts, list) and len(artifacts) == 2
    by_name = {entry["name"]: entry for entry in artifacts}
    assert set(by_name) == {"history.jsonl", "history-summary.md"}
    for entry in artifacts:
        assert entry["sha256"] is None  # 零哈希引用
        assert entry["note"] == mp.NOTE_STALE_PREEXISTING
    # 旧文件本体未被改动（事实保留，仅不引用）
    assert (history_dir / "history.jsonl").read_text(encoding="utf-8") == "OLD-ROWS-2026-09-22\n"


def test_history_failure_with_preexisting_fixed_names_not_cited(
        monkeypatch, tmp_path) -> None:
    """失败轮（rc=2）与超时轮同款零引用语义。"""
    monitor_dir, history_dir, _insights = _patch_stage_dirs(monkeypatch, tmp_path)
    (history_dir / "history.jsonl").write_text("OLD\n", encoding="utf-8")
    (history_dir / "history-summary.md").write_text("# OLD\n", encoding="utf-8")

    def on_call(step: str) -> None:
        if step == "monitor":
            _write_monitor_artifact(monitor_dir)

    fake = FakeRunner(history_rc=2, on_call=on_call)
    rc = _execute(tmp_path / "out", mp.StepRunner(fake, FAKE_PY))
    assert rc == mp.EXIT_STAGE_FAILED
    history = _pipeline_report(tmp_path / "out")["stages"]["history"]
    assert history["status"] == "failed"
    for entry in history["artifacts"]:
        assert entry["sha256"] is None
        assert entry["note"] == mp.NOTE_STALE_PREEXISTING


def test_history_ok_refreshed_fixed_names_cited_with_hash(
        monkeypatch, tmp_path) -> None:
    """同轮真实重写（内容/大小变化）→ 带 SHA-256 引用（零 stale note）。"""
    monitor_dir, history_dir, _insights = _patch_stage_dirs(monkeypatch, tmp_path)
    # 预存旧产物；本轮 on_call 重写为不同长度内容（size+mtime 指纹必变）
    (history_dir / "history.jsonl").write_text("OLD\n", encoding="utf-8")
    (history_dir / "history-summary.md").write_text("# OLD\n", encoding="utf-8")

    def on_call(step: str) -> None:
        if step == "monitor":
            _write_monitor_artifact(monitor_dir)
        elif step == "history":
            # write_bytes：显式字节——hash 断言不受平台换行转换影响
            (history_dir / "history.jsonl").write_bytes(
                b"NEW-ROWS-REFRESHED-THIS-RUN-2026-09-23\n")
            (history_dir / "history-summary.md").write_bytes(
                b"# NEW SUMMARY 2026-09-23\n")

    fake = FakeRunner(on_call=on_call)
    rc = _execute(tmp_path / "out", mp.StepRunner(fake, FAKE_PY))
    assert rc == mp.EXIT_OK
    history = _pipeline_report(tmp_path / "out")["stages"]["history"]
    assert history["status"] == "ok"
    by_name = {entry["name"]: entry for entry in history["artifacts"]}
    assert set(by_name) == {"history.jsonl", "history-summary.md"}
    assert by_name["history.jsonl"]["sha256"] == hashlib.sha256(
        b"NEW-ROWS-REFRESHED-THIS-RUN-2026-09-23\n").hexdigest()
    assert by_name["history-summary.md"]["sha256"] == hashlib.sha256(
        b"# NEW SUMMARY 2026-09-23\n").hexdigest()
    for entry in by_name.values():
        assert entry.get("note") != mp.NOTE_STALE_PREEXISTING  # 无 stale 引用


def test_history_timeout_partial_refresh_cited_and_stale_split(
        monkeypatch, tmp_path) -> None:
    """超时中途已原子落盘一半（jsonl 已刷新、summary 未动）→ 如实区分：
    刷新者 cited（带哈希），未刷新者 stale（零引用）。"""
    monitor_dir, history_dir, _insights = _patch_stage_dirs(monkeypatch, tmp_path)
    (history_dir / "history.jsonl").write_text("OLD\n", encoding="utf-8")
    (history_dir / "history-summary.md").write_text("# OLD\n", encoding="utf-8")

    def on_call(step: str) -> None:
        if step == "monitor":
            _write_monitor_artifact(monitor_dir)
        elif step == "history":
            # 被杀前已原子写出 jsonl（tmp+replace 完成），summary 未及写出
            (history_dir / "history.jsonl").write_bytes(
                b"PARTIAL-REFRESH-NEW-ROWS\n")

    fake = FakeRunner(history_exc=mp.RunnerTimeout("stage-killed"), on_call=on_call)
    rc = _execute(tmp_path / "out", mp.StepRunner(fake, FAKE_PY))
    assert rc == mp.EXIT_STAGE_FAILED
    history = _pipeline_report(tmp_path / "out")["stages"]["history"]
    by_name = {entry["name"]: entry for entry in history["artifacts"]}
    assert by_name["history.jsonl"]["sha256"] == hashlib.sha256(
        b"PARTIAL-REFRESH-NEW-ROWS\n").hexdigest()
    assert by_name["history-summary.md"]["sha256"] is None
    assert by_name["history-summary.md"]["note"] == mp.NOTE_STALE_PREEXISTING


def test_insights_failure_preexisting_fixed_names_not_cited(
        monkeypatch, tmp_path) -> None:
    monitor_dir, history_dir, insights_dir = _patch_stage_dirs(monkeypatch, tmp_path)
    (insights_dir / "insights.json").write_text('{"old": true}', encoding="utf-8")
    (insights_dir / "insights-summary.md").write_text("# OLD\n", encoding="utf-8")

    def on_call(step: str) -> None:
        if step == "monitor":
            _write_monitor_artifact(monitor_dir)
        elif step == "history":
            (history_dir / "history.jsonl").write_text("rows\n", encoding="utf-8")
            (history_dir / "history-summary.md").write_text("# s\n", encoding="utf-8")

    fake = FakeRunner(insights_exc=mp.RunnerTimeout("stage-killed"), on_call=on_call)
    rc = _execute(tmp_path / "out", mp.StepRunner(fake, FAKE_PY))
    assert rc == mp.EXIT_STAGE_FAILED
    insights = _pipeline_report(tmp_path / "out")["stages"]["insights"]
    assert insights["status"] == "timeout"
    for entry in insights["artifacts"]:
        assert entry["sha256"] is None
        assert entry["note"] == mp.NOTE_STALE_PREEXISTING


def test_preexisting_file_removed_during_run_noted(
        monkeypatch, tmp_path) -> None:
    """基线在场、步骤后消失 → removed-this-run（如实入档，零引用）。"""
    monitor_dir, history_dir, _insights = _patch_stage_dirs(monkeypatch, tmp_path)
    (history_dir / "history.jsonl").write_text("OLD\n", encoding="utf-8")
    (history_dir / "history-summary.md").write_text("# OLD\n", encoding="utf-8")

    def on_call(step: str) -> None:
        if step == "monitor":
            _write_monitor_artifact(monitor_dir)
        elif step == "history":
            (history_dir / "history-summary.md").unlink()  # 步中被移除
            (history_dir / "history.jsonl").write_bytes(b"NEW\n")

    fake = FakeRunner(on_call=on_call)
    rc = _execute(tmp_path / "out", mp.StepRunner(fake, FAKE_PY))
    assert rc == mp.EXIT_OK
    history = _pipeline_report(tmp_path / "out")["stages"]["history"]
    by_name = {entry["name"]: entry for entry in history["artifacts"]}
    assert by_name["history-summary.md"]["note"] == mp.NOTE_REMOVED_THIS_RUN
    assert by_name["history-summary.md"]["sha256"] is None
    assert by_name["history.jsonl"]["sha256"] == hashlib.sha256(b"NEW\n").hexdigest()


def test_baseline_unreadable_fixed_names_zero_citation(
        monkeypatch, tmp_path) -> None:
    """基线 stat 不可读（整体 None）→ 同轮产出不可证：全部现存固定名零
    哈希引用（baseline-unreadable-not-cited）。"""
    monitor_dir, history_dir, _insights = _patch_stage_dirs(monkeypatch, tmp_path)
    (history_dir / "history.jsonl").write_text("OLD\n", encoding="utf-8")
    (history_dir / "history-summary.md").write_text("# OLD\n", encoding="utf-8")

    def on_call(step: str) -> None:
        if step == "monitor":
            _write_monitor_artifact(monitor_dir)
        elif step == "history":
            (history_dir / "history.jsonl").write_text("NEW\n", encoding="utf-8")

    fake = FakeRunner(on_call=on_call)
    rc = _execute(tmp_path / "out", mp.StepRunner(fake, FAKE_PY), fs=_BaselineFailFs())
    assert rc == mp.EXIT_OK
    history = _pipeline_report(tmp_path / "out")["stages"]["history"]
    for entry in history["artifacts"]:
        assert entry["sha256"] is None  # 基线不可读——即便重写也零引用
        assert entry["note"] == mp.NOTE_BASELINE_UNREADABLE


def test_fixed_name_replaced_by_symlink_not_hashed(
        monkeypatch, tmp_path) -> None:
    """步骤后固定名被换成 symlink → 不读不哈希（symlink-not-hashed）。"""
    monitor_dir, history_dir, _insights = _patch_stage_dirs(monkeypatch, tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("external", encoding="utf-8")

    def on_call(step: str) -> None:
        if step == "monitor":
            _write_monitor_artifact(monitor_dir)
        elif step == "history":
            (history_dir / "history-summary.md").write_text("# s\n", encoding="utf-8")
            import os
            os.symlink(outside, history_dir / "history.jsonl")

    try:
        import os
        os.symlink(outside, tmp_path / "probe-link")
        (tmp_path / "probe-link").unlink()
    except OSError:
        pytest.skip("symlink unavailable on this host")

    fake = FakeRunner(on_call=on_call)
    rc = _execute(tmp_path / "out", mp.StepRunner(fake, FAKE_PY))
    assert rc == mp.EXIT_OK
    history = _pipeline_report(tmp_path / "out")["stages"]["history"]
    by_name = {entry["name"]: entry for entry in history["artifacts"]}
    assert by_name["history.jsonl"]["note"] == mp.NOTE_SYMLINK_NOT_HASHED
    assert by_name["history.jsonl"]["sha256"] is None
    assert outside.read_text(encoding="utf-8") == "external"  # 目标零读取零改写


# ---------------------------------------------------------------- 结构 pin


def test_pipeline_boundary_declares_same_run_artifact_provenance() -> None:
    """报告边界注记声明同轮产物 provenance 语义（读者可见的诚实边界）。"""
    assert any("stale-preexisting-not-cited" in item for item in mp.PIPELINE_BOUNDARIES)


def test_freshness_note_vocabulary_fixed() -> None:
    """固定词汇 note（报告 schema 稳定性 pin）。"""
    assert mp.NOTE_STALE_PREEXISTING == "stale-preexisting-not-cited"
    assert mp.NOTE_CREATED_THIS_RUN == "created-this-run"
    assert mp.NOTE_REMOVED_THIS_RUN == "removed-this-run"
    assert mp.NOTE_SYMLINK_NOT_HASHED == "symlink-not-hashed"
    assert mp.NOTE_BASELINE_UNREADABLE == "baseline-unreadable-not-cited"
    assert mp.NOTE_UNREADABLE == "unreadable-not-hashed"
