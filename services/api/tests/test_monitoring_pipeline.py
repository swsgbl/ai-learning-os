r"""M14-14 tools/ops/monitoring_pipeline.py 契约测试：monitor(M14-12) →
history(M14-13) 单次组合管道的安全边界（零真实容器面/零网络/零生产读取/
零计划任务改动）。

覆盖（全部 I/O 经真实临时目录或注入 Fake；绝不触碰 canonical 仓库与真实
.verify 目录；子进程经 FakeRunner 注入——绝不真实调用 production_monitor /
monitoring_history）：
- 结构契约：源码零网络/零 env/零容器面 token；唯一 subprocess 执行点无
  shell=；Windows 侧 CREATE_NO_WINDOW；os.replace 唯一落盘机制；
- plan 惰性：socket+subprocess 双阻断下照常出计划与 plan 报告；Runner
  零构造（计数工厂）；plan 报告零状态宣称；仅 --confirm（缺 --execute）
  仍是 plan；
- 门禁（fail-closed，零 Runner 构造/调用）：--execute 无 confirm、近似
  短语 ×5；两步超时超硬顶/非有限浮点 ×N（plan 同样拒绝，零报告写入）；
- 固定命令白名单门：StepRunner 对一切非精确形态（追加旗标/错误脚本/
  错误短语/顺序错乱）在任何执行之前拒绝且内层零调用；两固定形态全等；
- 序列语义：monitor → history 顺序执行（调用序断言）；monitor exit 0
  才运行 history；monitor 非零退出 → history skipped + 固定词汇原因 +
  monitor 退出码如实保留（不遮蔽）；history 失败同理；
- 超时/执行错误：RunnerTimeout → status=timeout + history skipped；
  RunnerError → status=error；配置超时逐字传给 runner；
- 重叠锁：锁已存在 → 可见拒绝 EXIT 2 零步骤执行（且零 stale-lock 清理
  ——不代删）；成功/步骤失败后锁释放；锁体仅安全事实；锁路径/输出祖先
  symlink 拒绝（真实文件面，目标零写入）；锁释放失败可见 EXIT 1 且入档；
- 报告：schema 版本化、原子写（无 tmp 残留）、写失败 = 证据不可失
  EXIT 2；仅安全事实（无绝对本机路径/无子进程 stdout/stderr 原文/
  secret 形态经终防线脱敏——异常类名投毒实证）；产物名+SHA-256+字节数
  （monitor 差集发现/非 monitor-*.json 忽略/hash 上限边界；history 两
  固定名）；
- 回归 pin（管道组合所依赖的既有工具契约）：monitor 门禁短语常量逐字
  一致、monitor 退出码映射（ok|warn→0，incomplete|critical→2）、
  monitor 门禁拒绝零采集、monitor plan 惰性、history 零源拒绝 EXIT 2。
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
SCRIPT = REPO_ROOT / "tools" / "ops" / "monitoring_pipeline.py"
MONITOR_SCRIPT = REPO_ROOT / "tools" / "ops" / "production_monitor.py"
HISTORY_SCRIPT = REPO_ROOT / "tools" / "ops" / "monitoring_history.py"

#: 标记值（注入 FakeRunner 输出/异常类名，断言绝不进入报告与 stdout）
MARK_TOKEN = "sk-ZXmarker0123456789"


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


mp = _load_module(SCRIPT, "monitoring_pipeline_under_test")
pm = _load_module(MONITOR_SCRIPT, "production_monitor_for_pipeline_regression")
mh = _load_module(HISTORY_SCRIPT, "monitoring_history_for_pipeline_regression")

FAKE_PY = "C:/fake/python.exe"


# ---------------------------------------------------------------- Fake 注入


class FakeRunner:
    """伪子进程面：按 argv[1] 判步（monitor/history），可注入 rc/异常/回调。"""

    def __init__(self, *, monitor_rc: int = 0, history_rc: int = 0,
                 monitor_exc: BaseException | None = None,
                 history_exc: BaseException | None = None,
                 on_call=None) -> None:
        self._rc = {"monitor": monitor_rc, "history": history_rc}
        self._exc = {"monitor": monitor_exc, "history": history_exc}
        self._on_call = on_call
        self.calls: list[tuple[tuple[str, ...], float]] = []

    def run(self, argv, *, timeout: float = 60.0, encoding: str | None = None):
        tokens = tuple(str(item) for item in argv)
        self.calls.append((tokens, float(timeout)))
        step = "monitor" if tokens[1].endswith("production_monitor.py") else "history"
        if self._on_call is not None:
            self._on_call(step)
        exc = self._exc[step]
        if exc is not None:
            raise exc
        return mp.CommandResult(tokens, self._rc[step], f"stdout-{step}", f"stderr-{step}")


class FakeClock:
    """确定性时钟：UTC 恒定、perf 每次 +1（时长可断言）。"""

    def __init__(self) -> None:
        self._perf = 0

    def utc_now_iso(self) -> str:
        return "2026-09-12T00:00:00Z"

    def stamp(self) -> str:
        return "20260912-000000"

    def perf(self) -> float:
        self._perf += 1
        return float(self._perf)


class _ReleaseFailFs(mp.RealFs):
    def lock_release(self, path: Path) -> None:
        raise OSError(13, "simulated release failure")


class _WriteFailFs(mp.RealFs):
    def write_atomic(self, path: Path, text: str) -> None:
        raise OSError(28, "simulated write failure")


def _gated(fake: FakeRunner) -> mp.StepRunner:
    return mp.StepRunner(fake, FAKE_PY)


def _patch_real_runner(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """把 main 的 RealRunner 构造替换为计数工厂（门禁前零构造断言用）。"""
    constructions = {"runner": 0}

    def make_runner():
        constructions["runner"] += 1
        return FakeRunner()

    monkeypatch.setattr(mp, "RealRunner", make_runner)
    return constructions


def _block_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("subprocess is forbidden in this test")

    monkeypatch.setattr(subprocess_module, "run", _forbidden)

    def _no_socket(*args: object, **kwargs: object) -> None:
        raise AssertionError("network is forbidden in this test")

    monkeypatch.setattr(socket, "socket", _no_socket)


def _execute(artifact_dir: Path, runner, *, clock=None, fs=None,
             extra: tuple[str, ...] = ()) -> int:
    return mp.main(
        ["--execute", "--confirm", mp.CONFIRM_PHRASE,
         "--artifact-dir", str(artifact_dir), *extra],
        runner=runner, clock=clock, fs=fs, python_exe=FAKE_PY)


def _report(artifact_dir: Path) -> dict[str, object]:
    return json.loads((artifact_dir / "pipeline-20260912-000000.json")
                      .read_text(encoding="utf-8"))


def _patch_stage_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                      *, pre_make: bool = True) -> tuple[Path, Path]:
    monitor_dir = tmp_path / "m14-12-artifacts"
    history_dir = tmp_path / "m14-13-artifacts"
    if pre_make:
        monitor_dir.mkdir()
        history_dir.mkdir()
    monkeypatch.setattr(mp, "MONITOR_ARTIFACT_DIR", monitor_dir)
    monkeypatch.setattr(mp, "HISTORY_OUTPUT_DIR", history_dir)
    return monitor_dir, history_dir


def _no_tmp_residue(directory: Path) -> bool:
    return not any(name.startswith(".") and name.endswith(".tmp")
                   for name in os.listdir(directory))


# ---------------------------------------------------------------- 结构契约


def test_source_contract_forbidden_tokens() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for token in ("socket", "urllib", "http.client", "os.system", "Popen",
                  "environ", "getenv", "requests", "urlopen", "docker"):
        assert token not in source, f"禁止出现的字面量: {token}"
    # 唯一 subprocess 执行点（RealRunner.run）：list-argv、无 shell=、恒 CREATE_NO_WINDOW
    assert source.count("subprocess.run(") == 1
    region = source[source.index("subprocess.run("):]
    assert "shell" not in region[:200]
    assert "CREATE_NO_WINDOW" in source
    # 唯一落盘替换机制：原子替换
    assert "os.replace" in source


# ---------------------------------------------------------------- plan 惰性


def test_plan_mode_zero_side_effects(monkeypatch, tmp_path) -> None:
    _block_side_effects(monkeypatch)
    constructions = _patch_real_runner(monkeypatch)
    rc = mp.main(["--artifact-dir", str(tmp_path / "out")])
    assert rc == mp.EXIT_OK
    assert constructions == {"runner": 0}  # 门禁前零构造
    reports = sorted((tmp_path / "out").glob("plan-*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["mode"] == "plan"
    assert report["overall_status"] == "planned"
    assert report["stages"]["monitor"]["status"] == "planned"
    assert report["stages"]["history"]["status"] == "planned"
    # plan 零锁
    assert not (tmp_path / "out" / mp.LOCK_NAME).exists()


def test_plan_report_no_status_claims(tmp_path) -> None:
    rc = mp.main(["--artifact-dir", str(tmp_path)])
    assert rc == mp.EXIT_OK
    report = json.loads(max(tmp_path.glob("plan-*.json")).read_text(encoding="utf-8"))
    for stage in report["stages"].values():
        assert stage["status"] == "planned"
        assert stage["exit_code"] is None
        assert stage["failure_category"] is None


def test_confirm_without_execute_flag_still_plan(monkeypatch, tmp_path) -> None:
    _block_side_effects(monkeypatch)
    constructions = _patch_real_runner(monkeypatch)
    rc = mp.main(["--confirm", mp.CONFIRM_PHRASE, "--artifact-dir", str(tmp_path)])
    assert rc == mp.EXIT_OK
    assert constructions == {"runner": 0}


# ---------------------------------------------------------------- 门禁（fail-closed）


def test_execute_gate_missing_confirm(monkeypatch, tmp_path) -> None:
    constructions = _patch_real_runner(monkeypatch)
    rc = mp.main(["--execute", "--artifact-dir", str(tmp_path)])
    assert rc == mp.EXIT_USAGE
    assert constructions == {"runner": 0}  # 零 Runner 构造（门禁先于构造）


@pytest.mark.parametrize("bad_phrase", [
    "",
    "execute read-only monitoring pipeline",
    "EXECUTE READ-ONLY MONITORING PIPELINES",
    " EXECUTE READ-ONLY MONITORING PIPELINE",
    "EXECUTE READ-ONLY MONITORING  PIPELINE",
    "EXECUTE READ-ONLY PRODUCTION MONITORING",  # 与 monitor 短语不同（各司其门）
])
def test_execute_gate_wrong_phrase(monkeypatch, tmp_path, bad_phrase: str) -> None:
    constructions = _patch_real_runner(monkeypatch)
    rc = mp.main(["--execute", "--confirm", bad_phrase, "--artifact-dir", str(tmp_path)])
    assert rc == mp.EXIT_USAGE
    assert constructions == {"runner": 0}


@pytest.mark.parametrize("option,bad", [
    ("--monitor-timeout-seconds", ["59", "541", "nan", "inf", "-inf"]),
    ("--history-timeout-seconds", ["9", "121", "nan", "inf"]),
])
def test_timeout_bounds_refused_in_plan_too(monkeypatch, tmp_path,
                                            option: str, bad: list[str]) -> None:
    constructions = _patch_real_runner(monkeypatch)
    for value in bad:
        rc = mp.main([f"{option}={value}", "--artifact-dir", str(tmp_path)])
        assert rc == mp.EXIT_USAGE, f"{option}={value} 应拒绝"
        assert constructions == {"runner": 0}
        assert not any(tmp_path.iterdir())  # 零报告写入（校验先于写入）


@pytest.mark.parametrize("option,value", [
    ("--monitor-timeout-seconds", ["60", "540"]),
    ("--history-timeout-seconds", ["10", "120"]),
])
def test_timeout_bounds_inclusive_edges_accepted(tmp_path, option: str,
                                                 value: list[str]) -> None:
    for edge in value:
        rc = mp.main([option, edge, "--artifact-dir", str(tmp_path)])
        assert rc == mp.EXIT_OK, f"{option}={edge} 边界应放行"


# ---------------------------------------------------------------- 固定命令白名单门


def test_allowed_forms_exact() -> None:
    forms = mp.allowed_step_argv(FAKE_PY)
    assert set(forms) == {"monitor", "history"}
    assert forms["monitor"] == (FAKE_PY, str(mp.MONITOR_SCRIPT),
                                "--execute", "--confirm", mp.MONITOR_CONFIRM_PHRASE)
    assert forms["history"] == (FAKE_PY, str(mp.HISTORY_SCRIPT))
    for form in forms.values():
        assert mp.is_allowed_step_command(form, FAKE_PY)


@pytest.mark.parametrize("argv", [
    (FAKE_PY, str(mp.MONITOR_SCRIPT)),                                # 缺 --execute
    (FAKE_PY, str(mp.MONITOR_SCRIPT), "--execute"),                   # 缺 confirm
    (FAKE_PY, str(mp.MONITOR_SCRIPT), "--execute", "--confirm", "WRONG"),
    (FAKE_PY, str(mp.MONITOR_SCRIPT), "--execute", "--confirm",
     mp.MONITOR_CONFIRM_PHRASE, "--extra"),                           # 追加旗标
    (FAKE_PY, str(mp.MONITOR_SCRIPT), "--confirm", mp.MONITOR_CONFIRM_PHRASE, "--execute"),
    (FAKE_PY, str(mp.HISTORY_SCRIPT), "--retention", "10"),           # 非默认参数
    (FAKE_PY, str(mp.HISTORY_SCRIPT), "--source-dir", "C:/evil"),     # 任意路径注入
    (FAKE_PY, "tools/ops/other_tool.py"),                             # 非许可脚本
    ("cmd.exe", "/c", "anything"),                                    # 非 python 形态
    (FAKE_PY,),
    (),
])
def test_step_runner_rejects_nonallowlisted(argv: tuple[str, ...]) -> None:
    inner = FakeRunner()
    gated = mp.StepRunner(inner, FAKE_PY)
    with pytest.raises(mp.CommandNotAllowedError):
        gated.run(argv)
    assert inner.calls == []  # 拒绝发生在任何执行之前


def test_step_runner_passes_allowed_forms_with_timeout() -> None:
    inner = FakeRunner()
    gated = mp.StepRunner(inner, FAKE_PY)
    result = gated.run(mp.allowed_step_argv(FAKE_PY)["monitor"], timeout=123.0)
    assert result.returncode == 0
    assert inner.calls[0] == (mp.allowed_step_argv(FAKE_PY)["monitor"], 123.0)


# ---------------------------------------------------------------- 序列语义


def test_sequence_monitor_then_history_ok(monkeypatch, tmp_path) -> None:
    monitor_dir, history_dir = _patch_stage_dirs(monkeypatch, tmp_path)

    def on_call(step: str) -> None:
        if step == "monitor":
            (monitor_dir / "monitor-20260912-000000.json").write_text("{}", encoding="utf-8")
        else:
            (history_dir / "history.jsonl").write_text("rows\n", encoding="utf-8")
            (history_dir / "history-summary.md").write_text("# s\n", encoding="utf-8")

    fake = FakeRunner(on_call=on_call)
    rc = _execute(tmp_path / "out", _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_OK
    # 顺序断言：monitor 先、history 后，且各自为固定白名单形态
    assert [argv[1] for argv, _ in fake.calls] == [str(mp.MONITOR_SCRIPT), str(mp.HISTORY_SCRIPT)]
    for argv, _timeout in fake.calls:
        assert mp.is_allowed_step_command(argv, FAKE_PY)
    report = _report(tmp_path / "out")
    assert report["stages"]["monitor"]["status"] == "ok"
    assert report["stages"]["monitor"]["exit_code"] == 0
    assert report["stages"]["history"]["status"] == "ok"
    assert report["stages"]["history"]["exit_code"] == 0
    assert report["overall_status"] == "ok"
    # 产物发现 + 哈希
    monitor_artifacts = report["stages"]["monitor"]["artifacts"]
    assert isinstance(monitor_artifacts, list) and len(monitor_artifacts) == 1
    assert monitor_artifacts[0]["name"] == "monitor-20260912-000000.json"
    assert monitor_artifacts[0]["sha256"] == hashlib.sha256(b"{}").hexdigest()
    assert monitor_artifacts[0]["bytes"] == 2
    history_artifacts = report["stages"]["history"]["artifacts"]
    assert {entry["name"] for entry in history_artifacts} == {"history.jsonl", "history-summary.md"}
    assert not (tmp_path / "out" / mp.LOCK_NAME).exists()  # 成功后锁释放


def test_monitor_failure_skips_history_and_preserves_exit_code(
        monkeypatch, tmp_path, capsys) -> None:
    _patch_stage_dirs(monkeypatch, tmp_path)
    fake = FakeRunner(monitor_rc=2)
    rc = _execute(tmp_path / "out", _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_STAGE_FAILED
    assert len(fake.calls) == 1  # history 零调用
    report = _report(tmp_path / "out")
    monitor = report["stages"]["monitor"]
    assert monitor["status"] == "failed"
    assert monitor["exit_code"] == 2  # 退出码如实保留，绝不遮蔽
    assert monitor["failure_category"] == "stage-exit-nonzero"
    history = report["stages"]["history"]
    assert history["status"] == "skipped"
    assert history["skipped_reason"] == "monitor-status-failed"
    assert report["overall_status"] == "failed"
    assert "monitor status=failed" in capsys.readouterr().out


def test_history_failure_preserved(monkeypatch, tmp_path) -> None:
    _patch_stage_dirs(monkeypatch, tmp_path)
    fake = FakeRunner(monitor_rc=0, history_rc=2)
    rc = _execute(tmp_path / "out", _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_STAGE_FAILED
    report = _report(tmp_path / "out")
    assert report["stages"]["monitor"]["status"] == "ok"
    assert report["stages"]["history"]["status"] == "failed"
    assert report["stages"]["history"]["exit_code"] == 2
    assert report["overall_status"] == "failed"


# ---------------------------------------------------------------- 超时 / 执行错误


def test_monitor_timeout_skips_history(monkeypatch, tmp_path) -> None:
    _patch_stage_dirs(monkeypatch, tmp_path)
    fake = FakeRunner(monitor_exc=mp.RunnerTimeout("secret " + MARK_TOKEN))
    rc = _execute(tmp_path / "out", _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_STAGE_FAILED
    assert len(fake.calls) == 1
    report = _report(tmp_path / "out")
    monitor = report["stages"]["monitor"]
    assert monitor["status"] == "timeout"
    assert monitor["timed_out"] is True
    assert monitor["exit_code"] is None
    assert monitor["failure_category"] == "command-timeout"
    assert monitor["failure_detail"] == "stage-killed-after-timeout"
    assert report["stages"]["history"]["skipped_reason"] == "monitor-status-timeout"


def test_runner_exec_error_categorized_not_persisted(monkeypatch, tmp_path) -> None:
    _patch_stage_dirs(monkeypatch, tmp_path)
    fake = FakeRunner(monitor_exc=mp.RunnerError("boom " + MARK_TOKEN))
    rc = _execute(tmp_path / "out", _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_STAGE_FAILED
    report_text = (tmp_path / "out" / "pipeline-20260912-000000.json").read_text(encoding="utf-8")
    assert MARK_TOKEN not in report_text  # 异常文本绝不入档
    report = json.loads(report_text)
    assert report["stages"]["monitor"]["status"] == "error"
    assert report["stages"]["monitor"]["failure_category"] == "command-exec-error"
    assert report["stages"]["monitor"]["error_class"] == "RunnerError"


def test_configured_timeouts_passed_to_runner(monkeypatch, tmp_path) -> None:
    _patch_stage_dirs(monkeypatch, tmp_path)
    fake = FakeRunner()
    rc = _execute(tmp_path / "out", _gated(fake), clock=FakeClock(),
                  extra=("--monitor-timeout-seconds", "300",
                         "--history-timeout-seconds", "45"))
    assert rc == mp.EXIT_OK
    timeouts = [timeout for _argv, timeout in fake.calls]
    assert timeouts == [300.0, 45.0]


# ---------------------------------------------------------------- 重叠锁


def test_existing_lock_refuses_zero_steps_zero_cleanup(monkeypatch, tmp_path,
                                                        capsys) -> None:
    _patch_stage_dirs(monkeypatch, tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    stale = out / mp.LOCK_NAME
    stale.write_text('{"stale": true}', encoding="utf-8")
    fake = FakeRunner()
    rc = _execute(out, _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_USAGE
    assert fake.calls == []  # 零步骤执行
    # 零 stale-lock 清理：内容原样保留（操作者人工处置）
    assert stale.read_text(encoding="utf-8") == '{"stale": true}'
    assert "重叠保护" in capsys.readouterr().out
    assert not list(out.glob("pipeline-*.json"))  # 拒绝路径零报告


def test_lock_released_after_stage_failure(monkeypatch, tmp_path) -> None:
    _patch_stage_dirs(monkeypatch, tmp_path)
    fake = FakeRunner(monitor_rc=2)
    rc = _execute(tmp_path / "out", _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_STAGE_FAILED
    assert not (tmp_path / "out" / mp.LOCK_NAME).exists()


def test_lock_body_safe_facts_only(monkeypatch, tmp_path) -> None:
    _patch_stage_dirs(monkeypatch, tmp_path)
    seen: dict[str, object] = {}

    def on_call(step: str) -> None:
        if step == "monitor" and not seen:
            body = json.loads((tmp_path / "out" / mp.LOCK_NAME).read_text(encoding="utf-8"))
            seen.update(body)

    fake = FakeRunner(on_call=on_call)
    rc = _execute(tmp_path / "out", _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_OK
    assert set(seen) == {"schema_version", "tool", "created_at_utc", "pid"}
    assert seen["tool"] == mp.TOOL_NAME
    assert seen["created_at_utc"] == "2026-09-12T00:00:00Z"


def test_lock_release_failure_visible(monkeypatch, tmp_path) -> None:
    _patch_stage_dirs(monkeypatch, tmp_path)
    fake = FakeRunner()
    rc = _execute(tmp_path / "out", _gated(fake), clock=FakeClock(),
                  fs=_ReleaseFailFs())
    assert rc == mp.EXIT_STAGE_FAILED  # 可见失败，不遮蔽步骤事实
    report = _report(tmp_path / "out")
    assert report["stages"]["monitor"]["status"] == "ok"
    assert report["stages"]["history"]["status"] == "ok"
    assert report["lock"]["released"] is False
    assert report["lock"]["release_failure"] == "PermissionError"
    assert (tmp_path / "out" / mp.LOCK_NAME).exists()  # 未释放的锁如实留存


def test_symlinked_lock_path_refused(monkeypatch, tmp_path) -> None:
    _patch_stage_dirs(monkeypatch, tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    target = tmp_path / "lock-target.json"
    target.write_text("sentinel", encoding="utf-8")
    try:
        os.symlink(target, out / mp.LOCK_NAME)
    except OSError:
        pytest.skip("symlink unavailable on this host")
    fake = FakeRunner()
    rc = _execute(out, _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_USAGE
    assert fake.calls == []
    assert target.read_text(encoding="utf-8") == "sentinel"  # symlink 目标零写入
    assert not list(out.glob("pipeline-*.json"))


def test_symlinked_artifact_ancestor_refused(monkeypatch, tmp_path) -> None:
    _patch_stage_dirs(monkeypatch, tmp_path)
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link = tmp_path / "link"
    try:
        os.symlink(real_dir, link)
    except OSError:
        pytest.skip("symlink unavailable on this host")
    fake = FakeRunner()
    rc = _execute(link / "out", _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_USAGE
    assert fake.calls == []
    assert not (real_dir / "out").exists()  # 不穿越 symlink 建目录


# ---------------------------------------------------------------- 报告：原子 / 脱敏 / 产物


def test_report_atomic_no_tmp_residue(monkeypatch, tmp_path) -> None:
    _patch_stage_dirs(monkeypatch, tmp_path)
    rc = _execute(tmp_path / "out", _gated(FakeRunner()), clock=FakeClock())
    assert rc == mp.EXIT_OK
    out = tmp_path / "out"
    assert _no_tmp_residue(out)
    assert (out / "pipeline-20260912-000000.json").is_file()
    assert (out / "pipeline-20260912-000000.md").is_file()


def test_report_contains_no_local_paths_or_child_output(
        monkeypatch, tmp_path, capsys) -> None:
    _patch_stage_dirs(monkeypatch, tmp_path)

    class MarkerRunner(FakeRunner):
        def run(self, argv, *, timeout: float = 60.0, encoding=None):
            result = super().run(argv, timeout=timeout, encoding=encoding)
            return mp.CommandResult(result.argv, result.returncode,
                                    f"raw {MARK_TOKEN} http://127.0.0.1:8000/x",
                                    f"err {MARK_TOKEN}")

    rc = _execute(tmp_path / "out", _gated(MarkerRunner()), clock=FakeClock())
    assert rc == mp.EXIT_OK
    report_text = (tmp_path / "out" / "pipeline-20260912-000000.json").read_text(encoding="utf-8")
    md_text = (tmp_path / "out" / "pipeline-20260912-000000.md").read_text(encoding="utf-8")
    console = capsys.readouterr().out
    for text in (report_text, md_text, console):
        assert MARK_TOKEN not in text          # 子进程原文绝不外泄
        assert "stdout-monitor" not in text
        assert str(tmp_path) not in text       # 绝无绝对本机路径
        assert sys.executable not in text


def test_report_redacts_secret_shaped_class_name(monkeypatch, tmp_path) -> None:
    """异常类名是唯一可携带外部文本的入档字段——终防线脱敏实证。"""
    _patch_stage_dirs(monkeypatch, tmp_path)

    class ghp_MARKER01234(RuntimeError):  # secret 形态类名投毒（非命名规范问题）
        pass

    fake = FakeRunner(monitor_exc=ghp_MARKER01234())
    rc = _execute(tmp_path / "out", _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_STAGE_FAILED
    report_text = (tmp_path / "out" / "pipeline-20260912-000000.json").read_text(encoding="utf-8")
    assert "ghp_MARKER01234" not in report_text
    assert "[REDACTED:token]" in report_text


def test_report_write_failure_evidence_exit(monkeypatch, tmp_path) -> None:
    _patch_stage_dirs(monkeypatch, tmp_path)
    fake = FakeRunner()
    rc = _execute(tmp_path / "out", _gated(fake), clock=FakeClock(), fs=_WriteFailFs())
    assert rc == mp.EXIT_USAGE  # 证据不可失——按拒绝处理
    assert len(fake.calls) == 2  # 步骤确实已执行（拒绝仅因报告不可落盘）
    assert not (tmp_path / "out" / mp.LOCK_NAME).exists()  # 锁在报告前已释放


def test_monitor_artifact_discovery_ignores_nonmonitor_and_bounded(
        monkeypatch, tmp_path) -> None:
    _patch_stage_dirs(monkeypatch, tmp_path)

    def on_call(step: str) -> None:
        if step == "monitor":
            for index in range(1, 10):  # 9 个新 monitor-*.json
                (mp.MONITOR_ARTIFACT_DIR / f"monitor-20260912-00000{index}.json").write_text(
                    f'{{"i":{index}}}', encoding="utf-8")
            (mp.MONITOR_ARTIFACT_DIR / "plan-20260912-000000.json").write_text("{}", encoding="utf-8")
            (mp.MONITOR_ARTIFACT_DIR / "monitor-20260912-000000.md").write_text("md", encoding="utf-8")

    fake = FakeRunner(on_call=on_call)
    rc = _execute(tmp_path / "out", _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_OK
    artifacts = _report(tmp_path / "out")["stages"]["monitor"]["artifacts"]
    assert isinstance(artifacts, list)
    hashed = [entry for entry in artifacts if entry["sha256"] is not None]
    assert len(hashed) == mp.MAX_ARTIFACTS_HASHED_PER_STAGE  # 有界：恰 8 个入哈希
    assert all(entry["name"].startswith("monitor-") and entry["name"].endswith(".json")
               for entry in hashed)
    assert not any("plan-" in str(entry["name"]) or ".md" in str(entry["name"])
                   for entry in artifacts)  # 非 monitor-*.json 忽略
    limit_entry = artifacts[-1]
    assert limit_entry["note"] == "hash-limit-reached"  # 超界显式记数


def test_history_artifact_unavailable_when_dir_missing(monkeypatch, tmp_path) -> None:
    monitor_dir, _history_dir = _patch_stage_dirs(monkeypatch, tmp_path, pre_make=False)

    def on_call(step: str) -> None:
        if step == "monitor":
            monitor_dir.mkdir(parents=True)
            (monitor_dir / "monitor-20260912-000000.json").write_text("{}", encoding="utf-8")
        # history 目录不创建（模拟该步未产出）

    fake = FakeRunner(on_call=on_call)
    rc = _execute(tmp_path / "out", _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_OK
    artifacts = _report(tmp_path / "out")["stages"]["history"]["artifacts"]
    assert artifacts == {"unavailable_reason": "artifact-dir-unreadable"}  # 如实不可得


# ---------------------------------------------------------------- 回归 pin（既有工具契约）


def test_regression_monitor_confirm_phrase_constant() -> None:
    """管道固定命令内嵌的 monitor 门禁短语与 production_monitor 恒一致。"""
    assert mp.MONITOR_CONFIRM_PHRASE == pm.CONFIRM_PHRASE
    assert pm.CONFIRM_PHRASE == "EXECUTE READ-ONLY PRODUCTION MONITORING"
    assert mp.CONFIRM_PHRASE != pm.CONFIRM_PHRASE  # 管道自身门禁独立


def test_regression_monitor_exit_code_map() -> None:
    """管道「monitor exit 0 ⇒ ok|warn 才放行 history」的前提：退出码映射不变。"""
    assert pm._exit_code_for({"overall_status": "ok"}) == 0
    assert pm._exit_code_for({"overall_status": "warn"}) == 0
    assert pm._exit_code_for({"overall_status": "incomplete"}) == 2
    assert pm._exit_code_for({"overall_status": "critical"}) == 2


def test_regression_monitor_gate_refusal_zero_collection(monkeypatch, tmp_path) -> None:
    constructions = _patch_real_runner(monkeypatch)

    def make_pm_runner() -> FakeRunner:
        constructions["runner"] += 1
        return FakeRunner()

    monkeypatch.setattr(pm, "RealRunner", make_pm_runner)
    rc = pm.main(["--execute", "--confirm", "EXECUTE READ-ONLY PRODUCTION MONITORING!",
                  "--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_USAGE
    assert constructions == {"runner": 0}  # 零采集


def test_regression_monitor_plan_inert(monkeypatch, tmp_path) -> None:
    _block_side_effects(monkeypatch)
    rc = pm.main(["--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_OK


def test_regression_history_refuses_empty_source(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    rc = mh.main(["--source-dir", str(source), "--output-dir", str(output)])
    assert rc == mh.EXIT_REFUSED  # 管道把 history rc≠0 视为 failed 步骤的前提
    assert not output.exists()


def test_cli_defaults_registered() -> None:
    parser = mp.build_parser()
    args = parser.parse_args([])
    assert args.execute is False
    assert args.confirm == ""
    assert args.monitor_timeout_seconds == mp.MONITOR_TIMEOUT_DEFAULT
    assert args.history_timeout_seconds == mp.HISTORY_TIMEOUT_DEFAULT
    assert args.artifact_dir == mp.ARTIFACT_DIR
