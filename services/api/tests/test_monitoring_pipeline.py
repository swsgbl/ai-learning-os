r"""M14-14/M14-21/M14-110 tools/ops/monitoring_pipeline.py 契约测试：
monitor(M14-12) → history(M14-13) → insights(M14-15) → calibration(M14-109)
单次组合管道的安全边界（零真实容器面/零网络/零生产读取/零计划任务改动）。

覆盖（全部 I/O 经真实临时目录或注入 Fake；绝不触碰 canonical 仓库与真实
.verify 目录；子进程经 FakeRunner 注入——绝不真实调用 production_monitor /
monitoring_history / monitoring_insights / monitoring_threshold_calibration）：
- 结构契约：源码零网络/零 env/零容器面 token；唯一 subprocess 执行点无
  shell=；Windows 侧 CREATE_NO_WINDOW；os.replace 唯一落盘机制；
- plan 惰性：socket+subprocess 双阻断下照常出四步计划与 plan 报告；Runner
  零构造（计数工厂）；plan 报告零状态宣称（四步全 planned）；仅 --confirm
  （缺 --execute）仍是 plan；
- 门禁（fail-closed，零 Runner 构造/调用）：--execute 无 confirm、近似
  短语 ×5；四步超时超硬顶/非有限浮点 ×N（plan 同样拒绝，零报告写入）；
- 固定命令白名单门：StepRunner 对一切非精确形态（追加旗标/错误脚本/
  错误短语/顺序错乱/--source 注入/calibration --history、--samples、阈值
  注入）在任何执行之前拒绝且内层零调用；四固定形态全等；insights 形态
  不带 --source（依赖 canonical history 默认输入——单一事实源，无用户
  可注入 argv）；calibration 形态仅 --format json（输入面全部经校准工具
  既有默认值生效）；管道 CLI 不暴露任何校准 argv/源/阈值参数；
- 序列语义：monitor → history → insights → calibration 顺序执行（调用
  序断言）；monitor exit 0 才运行 history；history exit 0 才运行 insights；
  insights status=ok 才运行 calibration；monitor 非零退出 → 后续三步
  skipped + 固定词汇原因 + monitor 退出码如实保留（不遮蔽）；history
  失败 → insights/calibration skipped 同理；insights 失败不改变
  monitor/history 事实，calibration 失败不改变前三步事实；
- calibration 步产物纪律（M14-110）：stdout 捕获 → JSON 校验（非 JSON/
  非对象/缺 schema_version = 可见失败 calibration-output-not-json，零
  写入）→ redact 终防线 + 原子写固定名 calibration.json（sha256/bytes
  与磁盘一致）；非零退出/超时/执行错误/写入拒绝 = 可见失败且预存旧
  calibration.json 原样保留、零引用（同轮新鲜度）；secret 形态经终防线
  脱敏入产物文件；
- 超时/执行错误：RunnerTimeout → status=timeout + 后续步 skipped；
  RunnerError → status=error；配置超时逐字传给 runner；四步超时硬顶
  总和 < 计划任务执行时限 PT12M=720s（与 monitoring_pipeline_task 交叉
  pin；三步既有硬顶 540+120+50=710 pin 不变，calibration 吃余量 9s）；
  insights/calibration 默认输入与同仓工具 canonical 常量三方一致；
- 重叠锁：锁已存在 → 可见拒绝 EXIT 2 零步骤执行（且零 stale-lock 清理
  ——不代删）；成功/步骤失败后锁释放；锁体仅安全事实；锁路径/输出祖先
  symlink 拒绝（真实文件面，目标零写入）；锁释放失败可见 EXIT 1 且入档；
- 报告：schema 版本化、原子写（无 tmp 残留）、写失败 = 证据不可失
  EXIT 2；仅安全事实（无绝对本机路径/无子进程 stdout/stderr 原文/
  secret 形态经终防线脱敏——异常类名投毒实证）；产物名+SHA-256+字节数
  （monitor 差集发现/非 monitor-*.json 忽略/hash 上限边界；history 与
  insights 各两固定名；calibration 一固定名由管道持久化）；
- 回归 pin（管道组合所依赖的既有工具契约）：monitor/insights 门禁短语
  常量逐字一致、monitor 退出码映射（ok|warn→0，incomplete|critical→2）、
  monitor 门禁拒绝零采集、monitor plan 惰性、history 零源拒绝 EXIT 2、
  insights 默认 source == history canonical 输出目录、calibration 默认
  history == 同一 canonical 输出（三方 resolve 全等）。
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
INSIGHTS_SCRIPT = REPO_ROOT / "tools" / "ops" / "monitoring_insights.py"
CALIBRATION_SCRIPT = REPO_ROOT / "tools" / "ops" / "monitoring_threshold_calibration.py"

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
mi = _load_module(INSIGHTS_SCRIPT, "monitoring_insights_for_pipeline_regression")
mtc = _load_module(CALIBRATION_SCRIPT,
                   "monitoring_threshold_calibration_for_pipeline_regression")

FAKE_PY = "C:/fake/python.exe"

#: FakeRunner 默认 calibration stdout：合法 JSON 产物（顶层对象 +
#: schema_version 键——与管道 validate_calibration_stdout 契约一致）
CALIBRATION_FAKE_STDOUT = json.dumps(
    {"schema_version": mtc.CALIBRATION_SCHEMA_VERSION,
     "tool": mtc.TOOL_NAME, "project": "fake-project",
     "window": {"records_used": 3}}, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------- Fake 注入


class FakeRunner:
    """伪子进程面：按 argv[1] 判步（monitor/history/insights/calibration），
    可注入 rc/异常/回调；calibration 默认返回合法 JSON 产物 stdout（第四步
    产物契约），其余步 stdout 恒为非产物占位文本。"""

    def __init__(self, *, monitor_rc: int = 0, history_rc: int = 0,
                 insights_rc: int = 0, calibration_rc: int = 0,
                 monitor_exc: BaseException | None = None,
                 history_exc: BaseException | None = None,
                 insights_exc: BaseException | None = None,
                 calibration_exc: BaseException | None = None,
                 calibration_stdout: str = CALIBRATION_FAKE_STDOUT,
                 on_call=None) -> None:
        self._rc = {"monitor": monitor_rc, "history": history_rc,
                    "insights": insights_rc, "calibration": calibration_rc}
        self._exc = {"monitor": monitor_exc, "history": history_exc,
                     "insights": insights_exc, "calibration": calibration_exc}
        self._calibration_stdout = calibration_stdout
        self._on_call = on_call
        self.calls: list[tuple[tuple[str, ...], float]] = []

    def run(self, argv, *, timeout: float = 60.0, encoding: str | None = None):
        tokens = tuple(str(item) for item in argv)
        self.calls.append((tokens, float(timeout)))
        script = tokens[1]
        if script.endswith("production_monitor.py"):
            step = "monitor"
        elif script.endswith("monitoring_history.py"):
            step = "history"
        elif script.endswith("monitoring_threshold_calibration.py"):
            step = "calibration"
        else:
            step = "insights"
        if self._on_call is not None:
            self._on_call(step)
        exc = self._exc[step]
        if exc is not None:
            raise exc
        if step == "calibration":
            stdout = self._calibration_stdout
        else:
            stdout = f"stdout-{step}"
        return mp.CommandResult(tokens, self._rc[step], stdout, f"stderr-{step}")


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
                      *, pre_make: bool = True) -> tuple[Path, Path, Path]:
    monitor_dir = tmp_path / "m14-12-artifacts"
    history_dir = tmp_path / "m14-13-artifacts"
    insights_dir = tmp_path / "m14-15-artifacts"
    if pre_make:
        monitor_dir.mkdir()
        history_dir.mkdir()
        insights_dir.mkdir()
    monkeypatch.setattr(mp, "MONITOR_ARTIFACT_DIR", monitor_dir)
    monkeypatch.setattr(mp, "HISTORY_OUTPUT_DIR", history_dir)
    monkeypatch.setattr(mp, "INSIGHTS_OUTPUT_DIR", insights_dir)
    return monitor_dir, history_dir, insights_dir


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
    assert report["config"]["sequence"] == ["monitor", "history", "insights",
                                            "calibration"]
    for step in ("monitor", "history", "insights", "calibration"):
        assert report["stages"][step]["status"] == "planned"
    # plan 零锁 + 零 calibration 产物
    assert not (tmp_path / "out" / mp.LOCK_NAME).exists()
    assert not (tmp_path / "out" / mp.CALIBRATION_OUTPUT_NAME).exists()


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
    ("--insights-timeout-seconds", ["4", "51", "nan", "inf", "-inf"]),
    ("--calibration-timeout-seconds", ["0", "6", "nan", "inf", "-inf"]),
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
    ("--insights-timeout-seconds", ["5", "50"]),
    ("--calibration-timeout-seconds", ["1", "5"]),
])
def test_timeout_bounds_inclusive_edges_accepted(tmp_path, option: str,
                                                 value: list[str]) -> None:
    for edge in value:
        rc = mp.main([option, edge, "--artifact-dir", str(tmp_path)])
        assert rc == mp.EXIT_OK, f"{option}={edge} 边界应放行"


# ---------------------------------------------------------------- 固定命令白名单门


def test_allowed_forms_exact() -> None:
    forms = mp.allowed_step_argv(FAKE_PY)
    assert set(forms) == {"monitor", "history", "insights", "calibration"}
    assert forms["monitor"] == (FAKE_PY, str(mp.MONITOR_SCRIPT),
                                "--execute", "--confirm", mp.MONITOR_CONFIRM_PHRASE,
                                "--voice-health-source", "sidecar")
    assert forms["history"] == (FAKE_PY, str(mp.HISTORY_SCRIPT))
    assert forms["insights"] == (FAKE_PY, str(mp.INSIGHTS_SCRIPT),
                                 "--execute", "--confirm", mp.INSIGHTS_CONFIRM_PHRASE)
    # M14-110 第四步：仅 --format json（工具既有输出形态选项——JSON 产物
    # 契约所必需）；输入面恒经校准工具既有默认值，绝无注入面
    assert forms["calibration"] == (FAKE_PY, str(mp.CALIBRATION_SCRIPT),
                                    "--format", "json")
    # insights 形态恒不带 --source：输入恒为 canonical history 默认目录
    # （单一事实源，绝无用户可注入 argv 面）
    assert "--source" not in forms["insights"]
    # calibration 形态恒不带源/窗口/阈值参数（全部经工具既有默认值生效）
    for token in ("--history", "--samples", "--latency-warn-ms",
                  "--latency-critical-ms", "--log-error-warn",
                  "--log-error-critical"):
        assert token not in forms["calibration"]
    for form in forms.values():
        assert mp.is_allowed_step_command(form, FAKE_PY)


@pytest.mark.parametrize("argv", [
    (FAKE_PY, str(mp.MONITOR_SCRIPT)),                                # 缺 --execute
    (FAKE_PY, str(mp.MONITOR_SCRIPT), "--execute"),                   # 缺 confirm
    (FAKE_PY, str(mp.MONITOR_SCRIPT), "--execute", "--confirm", "WRONG"),
    (FAKE_PY, str(mp.MONITOR_SCRIPT), "--execute", "--confirm",
     mp.MONITOR_CONFIRM_PHRASE, "--extra"),                           # 追加旗标
    (FAKE_PY, str(mp.MONITOR_SCRIPT), "--confirm", mp.MONITOR_CONFIRM_PHRASE, "--execute"),
    # M14-27：monitor 语音来源恒为 sidecar——loopback/缺值形态一律拒绝
    (FAKE_PY, str(mp.MONITOR_SCRIPT), "--execute", "--confirm",
     mp.MONITOR_CONFIRM_PHRASE, "--voice-health-source", "loopback"),  # 显式回退 loopback
    (FAKE_PY, str(mp.MONITOR_SCRIPT), "--execute", "--confirm",
     mp.MONITOR_CONFIRM_PHRASE, "--voice-health-source"),               # 旗标缺值
    (FAKE_PY, str(mp.HISTORY_SCRIPT), "--retention", "10"),           # 非默认参数
    (FAKE_PY, str(mp.HISTORY_SCRIPT), "--source-dir", "C:/evil"),     # 任意路径注入
    (FAKE_PY, str(mp.INSIGHTS_SCRIPT)),                               # 缺 --execute
    (FAKE_PY, str(mp.INSIGHTS_SCRIPT), "--execute"),                  # 缺 confirm
    (FAKE_PY, str(mp.INSIGHTS_SCRIPT), "--execute", "--confirm", "WRONG"),
    (FAKE_PY, str(mp.INSIGHTS_SCRIPT), "--execute", "--confirm",
     mp.INSIGHTS_CONFIRM_PHRASE, "--source", "C:/evil"),              # source 注入
    (FAKE_PY, str(mp.INSIGHTS_SCRIPT), "--execute", "--confirm",
     mp.INSIGHTS_CONFIRM_PHRASE, "--event-limit", "500"),             # 非默认参数
    # M14-110：calibration 注入面一律拒绝——裸脚本（缺 --format json）、
    # summary 形态、任何源/窗口/阈值注入、追加旗标
    (FAKE_PY, str(mp.CALIBRATION_SCRIPT)),                            # 缺 --format json
    (FAKE_PY, str(mp.CALIBRATION_SCRIPT), "--format", "summary"),     # 非 json 形态
    (FAKE_PY, str(mp.CALIBRATION_SCRIPT), "json"),                    # 非旗标形态
    (FAKE_PY, str(mp.CALIBRATION_SCRIPT), "--format", "json",
     "--history", "C:/evil"),                                         # 源注入
    (FAKE_PY, str(mp.CALIBRATION_SCRIPT), "--format", "json",
     "--samples", "10"),                                              # 窗口注入
    (FAKE_PY, str(mp.CALIBRATION_SCRIPT), "--format", "json",
     "--latency-warn-ms", "800"),                                     # 阈值注入
    (FAKE_PY, str(mp.CALIBRATION_SCRIPT), "--format", "json",
     "--log-error-critical", "30"),                                   # 阈值注入
    (FAKE_PY, str(mp.CALIBRATION_SCRIPT), "--samples", "10"),         # 缺 format + 窗口注入
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


def test_sequence_monitor_history_insights_calibration_ok(monkeypatch, tmp_path) -> None:
    monitor_dir, history_dir, insights_dir = _patch_stage_dirs(monkeypatch, tmp_path)

    def on_call(step: str) -> None:
        if step == "monitor":
            (monitor_dir / "monitor-20260912-000000.json").write_text("{}", encoding="utf-8")
        elif step == "history":
            (history_dir / "history.jsonl").write_text("rows\n", encoding="utf-8")
            (history_dir / "history-summary.md").write_text("# s\n", encoding="utf-8")
        elif step == "insights":
            (insights_dir / "insights.json").write_bytes(b"{}")
            (insights_dir / "insights-summary.md").write_bytes(b"# i\n")
        # calibration：产物由管道写（子进程零文件写入），无 on_call 副作用

    fake = FakeRunner(on_call=on_call)
    rc = _execute(tmp_path / "out", _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_OK
    # 顺序断言：monitor → history → insights → calibration，且各自为固定白名单形态
    assert [argv[1] for argv, _ in fake.calls] == [
        str(mp.MONITOR_SCRIPT), str(mp.HISTORY_SCRIPT), str(mp.INSIGHTS_SCRIPT),
        str(mp.CALIBRATION_SCRIPT)]
    for argv, _timeout in fake.calls:
        assert mp.is_allowed_step_command(argv, FAKE_PY)
    report = _report(tmp_path / "out")
    assert report["config"]["sequence"] == ["monitor", "history", "insights",
                                            "calibration"]
    for step in ("monitor", "history", "insights", "calibration"):
        assert report["stages"][step]["status"] == "ok"
        assert report["stages"][step]["exit_code"] == 0
    assert report["overall_status"] == "ok"
    # 产物发现 + 哈希
    monitor_artifacts = report["stages"]["monitor"]["artifacts"]
    assert isinstance(monitor_artifacts, list) and len(monitor_artifacts) == 1
    assert monitor_artifacts[0]["name"] == "monitor-20260912-000000.json"
    assert monitor_artifacts[0]["sha256"] == hashlib.sha256(b"{}").hexdigest()
    assert monitor_artifacts[0]["bytes"] == 2
    history_artifacts = report["stages"]["history"]["artifacts"]
    assert {entry["name"] for entry in history_artifacts} == {"history.jsonl", "history-summary.md"}
    insights_artifacts = report["stages"]["insights"]["artifacts"]
    assert isinstance(insights_artifacts, list)
    assert {entry["name"] for entry in insights_artifacts} == {"insights.json", "insights-summary.md"}
    assert {entry["sha256"] for entry in insights_artifacts} == {
        hashlib.sha256(b"{}").hexdigest(), hashlib.sha256(b"# i\n").hexdigest()}
    # calibration 产物：管道持久化的固定名 calibration.json——磁盘字节
    # 与报告 sha256/bytes 一致，note 固定词汇
    calibration = report["stages"]["calibration"]
    assert isinstance(calibration["artifacts"], list)
    entry = calibration["artifacts"][0]
    assert entry["name"] == mp.CALIBRATION_OUTPUT_NAME
    assert entry["note"] == mp.NOTE_PERSISTED_FROM_STAGE
    on_disk = (tmp_path / "out" / mp.CALIBRATION_OUTPUT_NAME).read_bytes()
    assert entry["bytes"] == len(on_disk)
    assert entry["sha256"] == hashlib.sha256(on_disk).hexdigest()
    assert json.loads(on_disk.decode("utf-8"))["schema_version"] == (
        mtc.CALIBRATION_SCHEMA_VERSION)
    assert not (tmp_path / "out" / mp.LOCK_NAME).exists()  # 成功后锁释放


def test_monitor_failure_skips_history_insights_calibration(monkeypatch, tmp_path,
                                                             capsys) -> None:
    _patch_stage_dirs(monkeypatch, tmp_path)
    fake = FakeRunner(monitor_rc=2)
    rc = _execute(tmp_path / "out", _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_STAGE_FAILED
    assert len(fake.calls) == 1  # history/insights/calibration 零调用
    report = _report(tmp_path / "out")
    monitor = report["stages"]["monitor"]
    assert monitor["status"] == "failed"
    assert monitor["exit_code"] == 2  # 退出码如实保留，绝不遮蔽
    assert monitor["failure_category"] == "stage-exit-nonzero"
    history = report["stages"]["history"]
    assert history["status"] == "skipped"
    assert history["skipped_reason"] == "monitor-status-failed"
    insights = report["stages"]["insights"]
    assert insights["status"] == "skipped"
    assert insights["skipped_reason"] == "history-status-skipped"
    calibration = report["stages"]["calibration"]
    assert calibration["status"] == "skipped"
    assert calibration["skipped_reason"] == "insights-status-skipped"
    assert report["overall_status"] == "failed"
    assert "monitor status=failed" in capsys.readouterr().out


def test_history_failure_skips_insights_and_calibration_reason_visible(
        monkeypatch, tmp_path, capsys) -> None:
    _patch_stage_dirs(monkeypatch, tmp_path)
    fake = FakeRunner(monitor_rc=0, history_rc=2)
    rc = _execute(tmp_path / "out", _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_STAGE_FAILED
    assert len(fake.calls) == 2  # insights/calibration 零调用
    report = _report(tmp_path / "out")
    assert report["stages"]["monitor"]["status"] == "ok"
    assert report["stages"]["monitor"]["exit_code"] == 0
    assert report["stages"]["history"]["status"] == "failed"
    assert report["stages"]["history"]["exit_code"] == 2  # history 事实不遮蔽
    insights = report["stages"]["insights"]
    assert insights["status"] == "skipped"
    assert insights["skipped_reason"] == "history-status-failed"
    assert insights["exit_code"] is None
    calibration = report["stages"]["calibration"]
    assert calibration["status"] == "skipped"
    assert calibration["skipped_reason"] == "insights-status-skipped"
    assert report["overall_status"] == "failed"
    assert "insights: skipped" in capsys.readouterr().out


def test_insights_failure_skips_calibration_preserves_facts(
        monkeypatch, tmp_path) -> None:
    _patch_stage_dirs(monkeypatch, tmp_path)
    fake = FakeRunner(monitor_rc=0, history_rc=0, insights_rc=2)
    rc = _execute(tmp_path / "out", _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_STAGE_FAILED
    assert len(fake.calls) == 3  # calibration 零调用（insights 失败不回滚事实）
    report = _report(tmp_path / "out")
    assert report["stages"]["monitor"]["status"] == "ok"
    assert report["stages"]["monitor"]["exit_code"] == 0
    assert report["stages"]["history"]["status"] == "ok"
    assert report["stages"]["history"]["exit_code"] == 0
    insights = report["stages"]["insights"]
    assert insights["status"] == "failed"
    assert insights["exit_code"] == 2
    assert insights["failure_category"] == "stage-exit-nonzero"
    calibration = report["stages"]["calibration"]
    assert calibration["status"] == "skipped"
    assert calibration["skipped_reason"] == "insights-status-failed"
    assert report["overall_status"] == "failed"
    # calibration 未执行 → 零产物写入
    assert not (tmp_path / "out" / mp.CALIBRATION_OUTPUT_NAME).exists()


def test_insights_timeout_categorized(monkeypatch, tmp_path) -> None:
    _patch_stage_dirs(monkeypatch, tmp_path)
    fake = FakeRunner(insights_exc=mp.RunnerTimeout("secret " + MARK_TOKEN))
    rc = _execute(tmp_path / "out", _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_STAGE_FAILED
    report = _report(tmp_path / "out")
    insights = report["stages"]["insights"]
    assert insights["status"] == "timeout"
    assert insights["timed_out"] is True
    assert insights["failure_category"] == "command-timeout"
    # 前置步骤事实不受 insights 超时影响；calibration 随之 skipped
    assert report["stages"]["monitor"]["status"] == "ok"
    assert report["stages"]["history"]["status"] == "ok"
    assert report["stages"]["calibration"]["skipped_reason"] == "insights-status-timeout"


# ---------------------------------------------------------------- calibration 步行为（M14-110）


def test_calibration_nonzero_exit_visible_failure_no_write(monkeypatch, tmp_path) -> None:
    """校准工具自身拒绝（rc=2，如 history malformed）= 可见 calibration 失败：
    退出码如实保留、零产物写入、预存旧 calibration.json 原样保留且零引用。"""
    _patch_stage_dirs(monkeypatch, tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    stale = out / mp.CALIBRATION_OUTPUT_NAME
    stale.write_text('{"old": true}', encoding="utf-8")
    fake = FakeRunner(calibration_rc=2)
    rc = _execute(out, _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_STAGE_FAILED
    calibration = _report(out)["stages"]["calibration"]
    assert calibration["status"] == "failed"
    assert calibration["exit_code"] == 2  # 校准退出码如实保留
    assert calibration["failure_category"] == "stage-exit-nonzero"
    assert calibration["failure_detail"] == "exit-code-2"
    assert calibration["artifacts"] is None  # 零引用
    assert stale.read_text(encoding="utf-8") == '{"old": true}'  # 旧文件原样保留
    # 前三步事实不受影响
    report = _report(out)
    for step in ("monitor", "history", "insights"):
        assert report["stages"][step]["status"] == "ok"


def test_calibration_timeout_categorized_visible(monkeypatch, tmp_path) -> None:
    _patch_stage_dirs(monkeypatch, tmp_path)
    fake = FakeRunner(calibration_exc=mp.RunnerTimeout("secret " + MARK_TOKEN))
    rc = _execute(tmp_path / "out", _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_STAGE_FAILED
    calibration = _report(tmp_path / "out")["stages"]["calibration"]
    assert calibration["status"] == "timeout"
    assert calibration["timed_out"] is True
    assert calibration["exit_code"] is None
    assert calibration["failure_category"] == "command-timeout"
    assert calibration["failure_detail"] == "stage-killed-after-timeout"
    assert not (tmp_path / "out" / mp.CALIBRATION_OUTPUT_NAME).exists()


def test_calibration_exec_error_categorized(monkeypatch, tmp_path) -> None:
    _patch_stage_dirs(monkeypatch, tmp_path)
    fake = FakeRunner(calibration_exc=mp.RunnerError("boom " + MARK_TOKEN))
    rc = _execute(tmp_path / "out", _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_STAGE_FAILED
    calibration = _report(tmp_path / "out")["stages"]["calibration"]
    assert calibration["status"] == "error"
    assert calibration["failure_category"] == "command-exec-error"
    assert not (tmp_path / "out" / mp.CALIBRATION_OUTPUT_NAME).exists()


def test_calibration_artifact_overwritten_atomically_on_success(
        monkeypatch, tmp_path) -> None:
    """成功轮覆盖预存旧 calibration.json（原子替换）；落盘字节 = 捕获
    stdout 原文；报告哈希 = 新落盘字节。"""
    _patch_stage_dirs(monkeypatch, tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / mp.CALIBRATION_OUTPUT_NAME).write_text('{"old": true}', encoding="utf-8")
    fresh_stdout = json.dumps(
        {"schema_version": mtc.CALIBRATION_SCHEMA_VERSION,
         "tool": mtc.TOOL_NAME, "fresh": True}) + "\n"
    fake = FakeRunner(calibration_stdout=fresh_stdout)
    rc = _execute(out, _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_OK
    on_disk = (out / mp.CALIBRATION_OUTPUT_NAME).read_bytes()
    assert on_disk == fresh_stdout.encode("utf-8")  # stdout 原文逐字节持久化
    entry = _report(out)["stages"]["calibration"]["artifacts"][0]
    assert entry["sha256"] == hashlib.sha256(on_disk).hexdigest()
    assert entry["bytes"] == len(on_disk)
    assert _no_tmp_residue(out)  # 原子写零残渣


def test_calibration_artifact_symlink_refused(monkeypatch, tmp_path) -> None:
    """预存 calibration.json 为 symlink → 持久化拒绝（可见失败），
    symlink 目标零写入。"""
    _patch_stage_dirs(monkeypatch, tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    target = tmp_path / "calib-target.json"
    target.write_text("sentinel", encoding="utf-8")
    try:
        os.symlink(target, out / mp.CALIBRATION_OUTPUT_NAME)
    except OSError:
        pytest.skip("symlink unavailable on this host")
    fake = FakeRunner()
    rc = _execute(out, _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_STAGE_FAILED
    calibration = _report(out)["stages"]["calibration"]
    assert calibration["status"] == "failed"
    assert calibration["failure_category"] == mp.CALIBRATION_ARTIFACT_WRITE_ERROR
    assert calibration["artifacts"] is None
    assert target.read_text(encoding="utf-8") == "sentinel"  # 目标零写入


def test_calibration_step_never_touches_history_inputs(monkeypatch, tmp_path) -> None:
    """管道持久化面仅限自身工件目录：history.jsonl 与三步产物目录零写入
    （校准工具零文件写入 + 管道独占持久化 = 双重结构性证明）。"""
    monitor_dir, history_dir, insights_dir = _patch_stage_dirs(monkeypatch, tmp_path)
    (history_dir / "history.jsonl").write_text("rows\n", encoding="utf-8")
    history_before = (history_dir / "history.jsonl").read_bytes()
    fake = FakeRunner()
    rc = _execute(tmp_path / "out", _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_OK
    assert (history_dir / "history.jsonl").read_bytes() == history_before
    assert sorted(p.name for p in history_dir.iterdir()) == ["history.jsonl"]  # 目录零新增
    assert sorted(p.name for p in monitor_dir.iterdir()) == []
    assert sorted(p.name for p in insights_dir.iterdir()) == []
    # calibration.json 是唯一新增文件，且落在管道自身工件目录
    assert set(os.listdir(tmp_path / "out")) >= {
        mp.CALIBRATION_OUTPUT_NAME, "pipeline-20260912-000000.json",
        "pipeline-20260912-000000.md"}


# ---------------------------------------------------------------- 超时 / 执行错误


def test_monitor_timeout_skips_history_and_insights(monkeypatch, tmp_path) -> None:
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
    assert report["stages"]["insights"]["skipped_reason"] == "history-status-skipped"
    assert report["stages"]["calibration"]["skipped_reason"] == "insights-status-skipped"


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
                         "--history-timeout-seconds", "45",
                         "--insights-timeout-seconds", "10",
                         "--calibration-timeout-seconds", "4"))
    assert rc == mp.EXIT_OK
    timeouts = [timeout for _argv, timeout in fake.calls]
    assert timeouts == [300.0, 45.0, 10.0, 4.0]


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
            if str(argv[1]).endswith("monitoring_threshold_calibration.py"):
                # calibration stdout 是产物本体：合法 JSON（契约键齐备）内嵌
                # secret 形态标记值（断言持久化前经终防线脱敏）
                stdout = json.dumps({"schema_version": mtc.CALIBRATION_SCHEMA_VERSION,
                                     "tool": mtc.TOOL_NAME,
                                     "note": f"raw {MARK_TOKEN} http://127.0.0.1:8000/x"})
            else:
                stdout = f"raw {MARK_TOKEN} http://127.0.0.1:8000/x"
            return mp.CommandResult(result.argv, result.returncode,
                                    stdout, f"err {MARK_TOKEN}")

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
    # calibration 产物文件同样脱敏（终防线覆盖 stdout 持久化路径）
    artifact_text = (tmp_path / "out" / mp.CALIBRATION_OUTPUT_NAME).read_text(encoding="utf-8")
    assert MARK_TOKEN not in artifact_text
    assert "[REDACTED:token]" in artifact_text
    entry = _report(tmp_path / "out")["stages"]["calibration"]["artifacts"][0]
    assert entry["sha256"] == hashlib.sha256(
        artifact_text.encode("utf-8")).hexdigest()  # 哈希 = 脱敏后落盘字节


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
    assert len(fake.calls) == 4  # 步骤确实已执行（拒绝仅因报告不可落盘）
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
    monitor_dir, _history_dir, _insights_dir = _patch_stage_dirs(
        monkeypatch, tmp_path, pre_make=False)

    def on_call(step: str) -> None:
        if step == "monitor":
            monitor_dir.mkdir(parents=True)
            (monitor_dir / "monitor-20260912-000000.json").write_text("{}", encoding="utf-8")
        elif step == "history":
            pass  # history 目录不创建（模拟该步未产出）
        elif step == "insights":
            _insights_dir.mkdir(parents=True)  # insights 仍执行（history rc=0）
            (_insights_dir / "insights.json").write_text("{}", encoding="utf-8")
        # calibration：产物由管道写（子进程零文件写入），无 on_call 副作用

    fake = FakeRunner(on_call=on_call)
    rc = _execute(tmp_path / "out", _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_OK
    artifacts = _report(tmp_path / "out")["stages"]["history"]["artifacts"]
    assert artifacts == {"unavailable_reason": "artifact-dir-unreadable"}  # 如实不可得


def test_insights_artifact_unavailable_when_dir_missing(monkeypatch, tmp_path) -> None:
    _monitor_dir, _history_dir, _insights_dir = _patch_stage_dirs(
        monkeypatch, tmp_path, pre_make=False)

    def on_call(step: str) -> None:
        if step == "monitor":
            _monitor_dir.mkdir(parents=True)
            (_monitor_dir / "monitor-20260912-000000.json").write_text("{}", encoding="utf-8")
        elif step == "history":
            _history_dir.mkdir(parents=True)
            (_history_dir / "history.jsonl").write_text("rows\n", encoding="utf-8")
        # insights 目录不创建（模拟该步未产出）

    fake = FakeRunner(on_call=on_call)
    rc = _execute(tmp_path / "out", _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_OK
    artifacts = _report(tmp_path / "out")["stages"]["insights"]["artifacts"]
    assert artifacts == {"unavailable_reason": "artifact-dir-unreadable"}  # 如实不可得


# ---------------------------------------------------------------- 回归 pin（既有工具契约）


def test_regression_monitor_confirm_phrase_constant() -> None:
    """管道固定命令内嵌的 monitor 门禁短语与 production_monitor 恒一致。"""
    assert mp.MONITOR_CONFIRM_PHRASE == pm.CONFIRM_PHRASE
    assert pm.CONFIRM_PHRASE == "EXECUTE READ-ONLY PRODUCTION MONITORING"
    assert mp.CONFIRM_PHRASE != pm.CONFIRM_PHRASE  # 管道自身门禁独立


def test_regression_insights_confirm_phrase_constant() -> None:
    """管道固定命令内嵌的 insights 门禁短语与 monitoring_insights 恒一致。"""
    assert mp.INSIGHTS_CONFIRM_PHRASE == mi.CONFIRM_PHRASE
    assert mi.CONFIRM_PHRASE == "EXECUTE READ-ONLY MONITORING INSIGHTS"
    # 与管道/monitor 门禁短语各司其职，互不通用
    assert mp.INSIGHTS_CONFIRM_PHRASE != mp.CONFIRM_PHRASE
    assert mp.INSIGHTS_CONFIRM_PHRASE != mp.MONITOR_CONFIRM_PHRASE


def test_insights_default_source_matches_history_canonical_output() -> None:
    """M14-21 双路径事实源修复：insights 默认输入 == history canonical 输出 ==
    管道 history 步产物发现目录（三方 resolve 全等）。"""
    assert mi.DEFAULT_SOURCE.resolve() == mh.DEFAULT_OUTPUT_DIR.resolve()
    assert mp.HISTORY_OUTPUT_DIR.resolve() == mh.DEFAULT_OUTPUT_DIR.resolve()
    assert str(mh.DEFAULT_OUTPUT_DIR).endswith("m14-13-monitoring-history")
    # 管道 insights 步产物发现目录 == insights 工具自身默认输出目录
    assert mp.INSIGHTS_OUTPUT_DIR.resolve() == mi.DEFAULT_OUTPUT_DIR.resolve()


def test_insights_step_uses_canonical_default_source(monkeypatch, tmp_path) -> None:
    """insights 固定命令形态不带 --source：canonical 输入经已修正的工具默认值
    生效（真实管道中 monitoring_insights 读 history canonical 输出目录）。"""
    _patch_stage_dirs(monkeypatch, tmp_path)
    fake = FakeRunner()
    rc = _execute(tmp_path / "out", _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_OK
    insights_argv = fake.calls[2][0]
    assert "--source" not in insights_argv
    assert "--output-dir" not in insights_argv
    # M14-110：calibration 第四 argv 同样零输入注入面（仅 --format json，
    # 输入全部经校准工具既有默认值生效）
    calibration_argv = fake.calls[3][0]
    assert calibration_argv == (FAKE_PY, str(mp.CALIBRATION_SCRIPT),
                                "--format", "json")
    for token in ("--history", "--samples", "--latency-warn-ms",
                  "--latency-critical-ms", "--log-error-warn",
                  "--log-error-critical"):
        assert token not in calibration_argv


def test_calibration_default_history_matches_canonical_output() -> None:
    """M14-110 单一事实源：calibration 步固定形态不带 --history——其输入经
    校准工具既有默认值生效，且默认输入 == history canonical 输出 == 管道
    history 步产物发现目录（三方 resolve 全等）。"""
    assert mtc.DEFAULT_HISTORY_PATH.resolve() == mh.DEFAULT_OUTPUT_DIR.resolve() / mh.HISTORY_OUTPUT_NAME
    assert mp.HISTORY_OUTPUT_DIR.resolve() == mh.DEFAULT_OUTPUT_DIR.resolve()
    assert mp.CALIBRATION_SCRIPT.resolve() == CALIBRATION_SCRIPT.resolve()


def test_pipeline_cli_exposes_no_calibration_injection_params() -> None:
    """管道 CLI 结构性零校准注入面：不暴露任何能进入校准 argv 的参数
    （源/窗口/阈值/format）——仅第四步超时（有界数值）可调。"""
    parser = mp.build_parser()
    option_strings = {token
                      for action in parser._actions
                      for token in action.option_strings}
    for token in ("--history", "--samples", "--format",
                  "--latency-warn-ms", "--latency-critical-ms",
                  "--log-error-warn", "--log-error-critical"):
        assert token not in option_strings, f"管道 CLI 不得暴露 {token}"
    assert "--calibration-timeout-seconds" in option_strings  # 唯一第四步参数


def test_three_stage_timeout_budget_below_task_limit() -> None:
    """三步既有超时硬顶 pin 不变（M14-110 前兼容面）：monitor 540 +
    history 120 + insights 50 = 710s < PT12M=720s。"""
    limit = 12 * 60  # PT12M = 720s
    for name in ("MONITOR", "HISTORY", "INSIGHTS"):
        default = getattr(mp, f"{name}_TIMEOUT_DEFAULT")
        minimum = getattr(mp, f"{name}_TIMEOUT_MIN")
        maximum = getattr(mp, f"{name}_TIMEOUT_MAX")
        assert minimum < default <= maximum, f"{name} 默认值应落在界内"
    hard_sum = (mp.MONITOR_TIMEOUT_MAX + mp.HISTORY_TIMEOUT_MAX
                + mp.INSIGHTS_TIMEOUT_MAX)
    assert hard_sum < limit, f"三步硬顶之和 {hard_sum}s 必须小于 {limit}s"
    default_sum = (mp.MONITOR_TIMEOUT_DEFAULT + mp.HISTORY_TIMEOUT_DEFAULT
                   + mp.INSIGHTS_TIMEOUT_DEFAULT)
    assert default_sum < limit
    # 边界同款：monitor 540 + history 120 不变（兼容面），insights 硬顶吃余量
    assert (mp.MONITOR_TIMEOUT_MAX, mp.HISTORY_TIMEOUT_MAX) == (540.0, 120.0)


def test_four_stage_timeout_budget_below_task_limit_m14_110() -> None:
    """M14-110 四步预算（supervisor 边界）：前三步硬顶 540+120+50=710 pin
    不动，calibration 1–5s（默认 5s——与 insights 同型本地只读
    history.jsonl 处理，秒级完成）；四步硬顶之和 715 < PT12M=720s 且恒留
    ≥5s 给管道自身开销（启动、锁、证据报告原子写与调度器余量——调度器
    绝不先于内部超时+收尾杀整任务，避免击杀留 stale lock/半写报告）；
    默认总和 590s 同样 < 720s。"""
    limit = 12 * 60  # PT12M = 720s
    assert (mp.CALIBRATION_TIMEOUT_MIN, mp.CALIBRATION_TIMEOUT_DEFAULT,
            mp.CALIBRATION_TIMEOUT_MAX) == (1.0, 5.0, 5.0)
    assert mp.CALIBRATION_TIMEOUT_MIN < mp.CALIBRATION_TIMEOUT_DEFAULT <= mp.CALIBRATION_TIMEOUT_MAX
    hard_sum = (mp.MONITOR_TIMEOUT_MAX + mp.HISTORY_TIMEOUT_MAX
                + mp.INSIGHTS_TIMEOUT_MAX + mp.CALIBRATION_TIMEOUT_MAX)
    assert hard_sum == 715.0
    assert hard_sum < limit, f"四步硬顶之和 {hard_sum}s 必须小于 {limit}s"
    assert limit - hard_sum >= 5  # 恒留 ≥5s 管道自身开销（supervisor 边界）
    default_sum = (mp.MONITOR_TIMEOUT_DEFAULT + mp.HISTORY_TIMEOUT_DEFAULT
                   + mp.INSIGHTS_TIMEOUT_DEFAULT + mp.CALIBRATION_TIMEOUT_DEFAULT)
    assert default_sum == 590.0
    assert default_sum < limit


def test_calibration_artifact_contract_constants_cross_pinned() -> None:
    """管道侧独立定义的校准产物契约常量与 standalone 工具常量交叉 pin
    （管道不导入兄弟工具——单一事实源由本测试锁定）：schema_version 与
    tool 身份精确相等，validate 只接受这一对组合。"""
    assert mp.CALIBRATION_SCHEMA_VERSION == mtc.CALIBRATION_SCHEMA_VERSION == 1
    assert mp.CALIBRATION_TOOL_NAME == mtc.TOOL_NAME
    assert mp.CALIBRATION_TOOL_NAME == "tools/ops/monitoring_threshold_calibration.py"
    # 既有 fake stdout 形态（standalone json 顶层契约键）通过管道校验
    assert mp.validate_calibration_stdout(CALIBRATION_FAKE_STDOUT) is True


@pytest.mark.parametrize("stdout_text", [
    "definitely not json {",                                    # 非 JSON
    "[1, 2, 3]\n",                                              # JSON 但非顶层对象
    '{"no_schema_version": true}\n',                            # 缺契约键
    "",                                                         # 空 stdout
    '{"schema_version": 2, "tool": "tools/ops/monitoring_threshold_calibration.py"}\n',  # 版本不兼容
    '{"schema_version": 1, "tool": "tools/ops/other_tool.py"}\n',  # 工具身份不符
])
def test_calibration_output_contract_mismatch_visible_failure(
        monkeypatch, tmp_path, stdout_text: str) -> None:
    """退出 0 但 stdout 不满足校准产物契约（malformed/非对象/缺键/版本或
    工具身份不匹配）= 可见失败 calibration-output-not-json（退出码 0 如实
    入档），绝不静默接受、零写入。"""
    _patch_stage_dirs(monkeypatch, tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    stale = out / mp.CALIBRATION_OUTPUT_NAME
    stale.write_text('{"old": true}', encoding="utf-8")
    fake = FakeRunner(calibration_stdout=stdout_text)
    rc = _execute(out, _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_STAGE_FAILED
    calibration = _report(out)["stages"]["calibration"]
    assert calibration["status"] == "failed"
    assert calibration["exit_code"] == 0  # 子进程确实退出 0——事实不遮蔽
    assert calibration["failure_category"] == mp.CALIBRATION_OUTPUT_NOT_JSON
    assert calibration["failure_detail"] == "stage-stdout-not-valid-json"
    assert calibration["artifacts"] is None
    # 零写入：旧文件原样保留（同轮新鲜度——绝不引用为本轮产物）
    assert stale.read_text(encoding="utf-8") == '{"old": true}'


def test_history_timeout_default_raised_45_to_90_m1479() -> None:
    """M14-79：history 步默认超时 45s → 90s——2026-09-21 生产三次实测
    （45.206s / 45.522s / 46.955s）刚过 45s 即被杀；90s ≈ 1.9× 最坏观测
    （46.955s），正常完成轮实测 0.3–7.2s。上调只改默认值：硬顶 120 不变、
    默认总和仍 < PT12M=720s。超时事实入档语义（RunnerTimeout →
    status=timeout + 后续步 skipped 固定词汇原因）不随上调改变——由既有
    test_monitor_timeout_skips_history_and_insights 与
    test_insights_timeout_categorized 真实绑定，本测试零冗余重述。"""
    assert mp.HISTORY_TIMEOUT_DEFAULT == 90.0
    assert mp.HISTORY_TIMEOUT_MIN < 90.0 <= mp.HISTORY_TIMEOUT_MAX
    worst_observed_kill = 46.955  # 2026-09-21T04:45:25Z 运行实测
    assert mp.HISTORY_TIMEOUT_DEFAULT >= 1.9 * worst_observed_kill - 1e-9
    default_sum = (mp.MONITOR_TIMEOUT_DEFAULT + mp.HISTORY_TIMEOUT_DEFAULT
                   + mp.INSIGHTS_TIMEOUT_DEFAULT)
    assert default_sum == 585.0
    assert default_sum < 720  # PT12M 执行时限


def test_markdown_renders_all_four_stages(monkeypatch, tmp_path) -> None:
    _patch_stage_dirs(monkeypatch, tmp_path)
    fake = FakeRunner(monitor_rc=0, history_rc=0, insights_rc=2)
    rc = _execute(tmp_path / "out", _gated(fake), clock=FakeClock())
    assert rc == mp.EXIT_STAGE_FAILED
    md_text = (tmp_path / "out" / "pipeline-20260912-000000.md").read_text(encoding="utf-8")
    assert "insights" in md_text
    assert "monitor → history → insights → calibration" in md_text
    for tool_line in (mp.MONITOR_TOOL_NAME, mp.HISTORY_TOOL_NAME,
                      mp.INSIGHTS_TOOL_NAME, mp.CALIBRATION_TOOL_NAME):
        assert tool_line in md_text


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
    assert args.insights_timeout_seconds == mp.INSIGHTS_TIMEOUT_DEFAULT
    assert args.calibration_timeout_seconds == mp.CALIBRATION_TIMEOUT_DEFAULT
    assert args.artifact_dir == mp.ARTIFACT_DIR
