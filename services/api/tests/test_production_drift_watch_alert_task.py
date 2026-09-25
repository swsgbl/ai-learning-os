r"""M14-137 tools/ops/production_drift_watch_alert_task.py 契约测试：
production drift-watch 报告 → M14-135 告警分发的 fail-closed 调度桥
readiness（零调度器、零生产执行、零真实 webhook；本切片只交付工具）。

覆盖（全部合成 fixtures + 注入 FakeRunner——**构造上零真实子进程、
零网络**：plan 路径在 socket+subprocess 双阻断下照常成功；execute 的
移交只到注入 FakeRunner 为止，真实 M14-135 CLI 与真实网络绝不触达）：

- 结构契约：纯标准库 + 两兄弟模块 import（零 Docker/网络 token）、
  校验单一事实源**同一实现对象复用**（load_drift_report/
  validate_drift_report/reject_path_problems/redact_secrets/SOURCE_FILE_
  NAME_RE 逐一 is/相等锁定；默认工件目录 == M14-133 DEFAULT_INPUT_DIR）、
  确认短语与 M14-127/129/135 既有短语互不通用（交叉 pin）、移交目标
  为仓库内 M14-135 CLI；
- 移交白名单门（结构性）：9/11 token 精确形态放行（POSIX 绝对路径值
  合法）；错 python/错工具/缺 --execute/任务短语冒充 dispatch 短语/
  追加旗标/值位置旗标形态/顺序错乱逐项拒绝；
- 选择 fail-closed：最新有效 execute 报告选中（多候选定序 + 精确
  sha256 报告）；--report 精确覆盖（与 --artifacts-dir 显式同给拒）；
  目录缺失/非目录/零候选（仅 plan-*.json 与 .md）拒绝；**最新候选
  malformed/plan 模式/schema 破损 → 拒绝且绝不回退更旧报告**；候选
  文件名时间戳非真实日历时刻（无法定序）拒绝；.. 组件拒绝；
- drift 判定只依据报告：drift=false → exit 0 + skipped-no-alerts
  （plan 与 execute 均**零 runner 调用**、零写入、台账零触碰）；
  plan 且 drift=true → exit 3 + stdout 报告精确 SHA-256 与
  dispatch-would-be-required（零 runner 调用；双阻断下照常）；
- execute 门禁：--execute 缺确认/近似短语（含 M14-135 DISPATCH 短语
  与 M14-127 watcher 短语）/缺 --secret-file/secret 文件不存在 →
  exit 2 且零 runner 调用；--secret-file 无 --execute（plan 不读
  secret）拒绝；
- 移交传播：成功（rc0 → 透传 0、恰一次白名单 argv、报告/secret 值
  原样、M14-135 自有短语收尾、stdout 脱敏回显）；分发失败（rc2 →
  透传 2）；duplicate-dispatch（rc2 → 透传 2）；子进程不可执行/
  超时（RunnerError → exit 2 固定类别）；
- 脱敏与路径安全：投毒 report detail（secret 形态 marker）绝不入
  stdout；子进程回显经 redact_secrets 终防线；本工具 stdout 绝无
  绝对本地路径（tmp 目录字面量正反斜杠双检）；plan 零写入（目录树
  前后快照不变）；symlinked 工件目录拒绝（可用性受限主机 skip）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ops" / "production_drift_watch_alert_task.py"
SOURCE = SCRIPT.read_text(encoding="utf-8")

#: 投毒 marker（注入报告 detail / 子进程回显，断言绝不进入任何 stdout）
MARK_TOKEN = "sk-ZXmarker0123456789"

API_DIGEST = "sha256:" + "a" * 64
WEB_DIGEST = "sha256:" + "b" * 64


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


task = _load("production_drift_watch_alert_task", SCRIPT)
dispatch_mod = _load(
    "production_drift_watch_alert_dispatch",
    REPO_ROOT / "tools" / "ops" / "production_drift_watch_alert_dispatch.py")
history_mod = _load(
    "production_drift_watch_history",
    REPO_ROOT / "tools" / "ops" / "production_drift_watch_history.py")
watcher_mod = _load(
    "production_drift_watch", REPO_ROOT / "tools" / "ops" / "production_drift_watch.py")
sched_mod = _load(
    "production_drift_watch_task",
    REPO_ROOT / "tools" / "ops" / "production_drift_watch_task.py")


# ---------------------------------------------------------------- fixtures


def make_check(status: str = "pass", check_id: str = "compose-service-present",
               subject: str = "api", detail: str = "present") -> dict:
    return {"check_id": check_id, "subject": subject, "status": status,
            "detail": detail}


def make_report(*, drift: bool = True, started: str, ended: str,
                mode: str = "execute", detail: str = "state=running health=ok",
                checks: list[dict] | None = None) -> dict:
    if checks is None:
        if drift:
            checks = [make_check(), make_check(subject="web"),
                      make_check(status="fail", check_id="container-health",
                                 subject="api", detail="state=running health=unhealthy")]
        else:
            checks = [make_check(), make_check(subject="web"),
                      make_check(check_id="container-health", detail=detail)]
    return {
        "schema_version": 1,
        "tool": "tools/ops/production_drift_watch.py",
        "milestone": "M14-127",
        "mode": mode,
        "started_at_utc": started,
        "ended_at_utc": ended,
        "config": {
            "project": "aios-m14-03-production-rehearsal",
            "profiles": ["local", "search"],
            "compose_file": "docker-compose.yml",
            "services": ["postgres", "redis", "minio", "api", "web",
                         "livekit", "searxng"],
            "anchors": {
                "api": {"expected_tag": "aios/api:m14-124-production",
                        "expected_digest": API_DIGEST},
                "web": {"expected_tag": "aios/web:m14-124-production",
                        "expected_digest": WEB_DIGEST},
            },
        },
        "boundaries": ["read-only collection only"],
        "collectors": {"compose_ps": {"status": "ok"},
                       "containers": {"per_service": {}}, "image_refs": {}},
        "checks": checks,
        "counts": {"pass": sum(1 for c in checks if c["status"] == "pass"),
                   "fail": sum(1 for c in checks if c["status"] == "fail")},
        "drift": drift,
        "drift_reasons": ["container-not-healthy:api"] if drift else [],
    }


def write_report(directory: Path, report: dict, stem: str) -> Path:
    path = directory / f"{stem}.json"
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return path


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def seed_dir(tmp_path: Path, *, newest_drift: bool = True) -> tuple[Path, Path]:
    """两份合法 execute 报告：010000 drift=false、020000 drift=<newest>；
    返回 (目录, 最新报告路径)。"""
    directory = tmp_path / "artifacts"
    directory.mkdir()
    write_report(directory, make_report(
        drift=False, started="2026-09-25T01:00:00Z", ended="2026-09-25T01:00:01Z"),
        "drift-watch-20260925-010000")
    newest = write_report(directory, make_report(
        drift=newest_drift, started="2026-09-25T02:00:00Z",
        ended="2026-09-25T02:00:01Z"), "drift-watch-20260925-020000")
    return directory, newest


class FakeRunner:
    """注入 Runner：记录 argv；可注入退出码/输出/异常。"""

    def __init__(self, *, returncode: int = 0, stdout: str = "",
                 stderr: str = "", error: Exception | None = None) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.error = error
        self.calls: list[list[str]] = []

    def run(self, argv, *, timeout: float = 300.0):
        self.calls.append([str(item) for item in argv])
        if self.error is not None:
            raise self.error
        return SimpleNamespace(argv=tuple(str(i) for i in argv),
                               returncode=self.returncode,
                               stdout=self.stdout, stderr=self.stderr)


class Collector:
    """捕获 stdout 的 log 注入缝（不打印）。"""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def say(self, message: str) -> None:
        self.lines.append(message)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def write_secret(tmp_path: Path) -> Path:
    path = tmp_path / "secret.json"
    path.write_text(json.dumps({"url": "https://hooks.example.invalid/x",
                                "token": "tk-value-0123456789"}), encoding="utf-8")
    return path


def run_main(argv: list[str], *, runner=None) -> tuple[int, Collector]:
    log = Collector()
    code = task.main(argv, runner=runner, log=log)
    return code, log


def tree_snapshot(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in sorted(root.rglob("*"))}


# ---------------------------------------------------------------- 结构契约


def test_source_contract_single_source_of_truth() -> None:
    """纯标准库 + 兄弟模块；校验/脱敏/路径安全同一实现对象复用；短语
    交叉 pin；移交目标为仓库内 M14-135 CLI；默认目录 = M14-133 常量。"""
    for banned in ("import socket", "import requests", "urllib", "http.client",
                   "docker", "psycopg", "os.environ", "getenv"):
        assert banned not in SOURCE, banned
    assert SOURCE.count("subprocess.run") == 1  # 唯一触达点（RealRunner）
    # 单一事实源：经本工具自身 import 的兄弟模块属性做 is 锁定
    assert task.dispatch.__name__ == "production_drift_watch_alert_dispatch"
    assert task.history.__name__ == "production_drift_watch_history"
    assert task.load_drift_report is task.dispatch.load_drift_report
    assert task.validate_drift_report is task.dispatch.validate_drift_report
    assert task.reject_path_problems is task.history.reject_path_problems
    assert task.redact_secrets is task.dispatch.redact_secrets
    assert task.SOURCE_FILE_NAME_RE is task.dispatch.SOURCE_FILE_NAME_RE
    assert task.FILE_STAMP_FORMAT == dispatch_mod.FILE_STAMP_FORMAT
    assert task.SOURCE_FILE_NAME_RE.pattern == dispatch_mod.SOURCE_FILE_NAME_RE.pattern
    assert task.DEFAULT_ARTIFACTS_DIR == history_mod.DEFAULT_INPUT_DIR
    assert task.DISPATCH_TOOL == (REPO_ROOT / "tools" / "ops"
                                  / "production_drift_watch_alert_dispatch.py")
    assert task.DISPATCH_TOOL.is_file()
    # 确认短语四工具互不通用（近似短语绝不放行）
    phrases = {task.TASK_CONFIRM_PHRASE, dispatch_mod.CONFIRM_PHRASE,
               watcher_mod.CONFIRM_PHRASE, sched_mod.TASK_CONFIRM_PHRASE}
    assert len(phrases) == 4
    assert task.TASK_CONFIRM_PHRASE == "EXECUTE PRODUCTION DRIFT WATCH ALERT TASK"
    # 退出码口径：0 成功/跳过；2 拒绝（含 M14-135 透传）；3 plan 需分发
    assert (task.EXIT_OK, task.EXIT_REFUSED, task.EXIT_DISPATCH_REQUIRED) == (0, 2, 3)


def test_dispatch_argv_whitelist() -> None:
    """结构性白名单：恰两形态放行（9/11 token）；越界形态逐项拒绝。"""
    good = [sys.executable, str(task.DISPATCH_TOOL), "--report", "D:/r/x.json",
            "--secret-file", "D:/s.json", "--execute", "--confirm",
            dispatch_mod.CONFIRM_PHRASE]
    assert task.is_allowed_dispatch_argv(good)
    good_posix = [sys.executable, str(task.DISPATCH_TOOL), "--report",
                  "/tmp/r/x.json", "--secret-file", "/tmp/s.json",
                  "--execute", "--confirm", dispatch_mod.CONFIRM_PHRASE]
    assert task.is_allowed_dispatch_argv(good_posix)
    with_artifact = good[:6] + ["--artifact-dir", "/tmp/out"] + good[6:]
    assert task.is_allowed_dispatch_argv(with_artifact)
    rejects: list[list[str]] = [
        good + ["--extra"],                                    # 多余 token
        good[:-1],                                             # 缺短语
        ["python", *good[1:]],                                 # 错解释器
        [sys.executable, str(REPO_ROOT / "tools" / "ops"
                             / "production_drift_watch.py"), *good[2:]],  # 错工具
        [sys.executable, str(task.DISPATCH_TOOL), "--secret-file",
         "D:/s.json", "--report", "D:/r.json", "--execute",
         "--confirm", dispatch_mod.CONFIRM_PHRASE],           # 旗标顺序错乱
        good[:-1] + [task.TASK_CONFIRM_PHRASE],               # 任务短语冒充
        good[:-1] + [watcher_mod.CONFIRM_PHRASE],             # watcher 短语冒充
        good[:-1] + ["EXECUTE PRODUCTION DRIFT WATCH ALERT DISPATCH "],  # 尾空格近似
        [sys.executable, str(task.DISPATCH_TOOL), "--report", "-F",
         "--secret-file", "D:/s.json", "--execute", "--confirm",
         dispatch_mod.CONFIRM_PHRASE],                        # 值位置旗标形态
        good[:6] + ["--artifact-dir"],                        # artifact-dir 缺值
        good[:6] + ["--artifact-dir", "-x"] + good[6:],       # artifact-dir 值旗标形态
        [sys.executable, str(task.DISPATCH_TOOL), "--report", "D:/r.json",
         "--secret-file", "D:/s.json", "--confirm",
         dispatch_mod.CONFIRM_PHRASE],                        # 缺 --execute
    ]
    for argv in rejects:
        assert not task.is_allowed_dispatch_argv(argv), argv


# ---------------------------------------------------------------- 选择 fail-closed


def test_newest_selection_and_drift_true_plan(tmp_path: Path) -> None:
    """多候选定序选最新；plan 且 drift=true → exit 3 + 精确 SHA-256。"""
    directory, newest = seed_dir(tmp_path, newest_drift=True)
    write_report(directory, make_report(
        drift=False, started="2026-09-24T23:45:00Z", ended="2026-09-24T23:45:01Z"),
        "drift-watch-20260924-234500")  # 更旧候选（跨日定序）
    (directory / "plan-20260925-030000.json").write_text("{}", encoding="utf-8")
    (directory / "drift-watch-20260925-020000.md").write_text("md", encoding="utf-8")
    runner = FakeRunner()
    code, log = run_main(["--artifacts-dir", str(directory)], runner=runner)
    assert code == task.EXIT_DISPATCH_REQUIRED
    assert sha256_of(newest) in log.text
    assert "drift-watch-20260925-020000" in log.text
    assert "drift-watch-20260925-010000" not in log.text.replace(
        "drift-watch-20260925-020000", "")
    assert "dispatch-would-be-required" in log.text
    assert "候选 3 份" in log.text  # plan-*.json 与 .md 只忽略不计候选
    assert runner.calls == []


def test_exact_report_override(tmp_path: Path) -> None:
    """--report 精确指定旧报告 → 恰选该份；与 --artifacts-dir 显式同给拒。"""
    directory, _ = seed_dir(tmp_path, newest_drift=True)
    older = directory / "drift-watch-20260925-010000.json"
    code, log = run_main(["--report", str(older)])
    assert code == task.EXIT_OK
    assert "drift-watch-20260925-010000" in log.text
    assert "skipped-no-alerts" in log.text
    code, log = run_main(["--report", str(older), "--artifacts-dir", str(directory)])
    assert code == task.EXIT_REFUSED
    assert "互斥" in log.text


@pytest.mark.parametrize("setup", ["missing-dir", "not-a-dir", "only-ignored"])
def test_missing_or_empty_source_refused(tmp_path: Path, setup: str) -> None:
    runner = FakeRunner()
    if setup == "missing-dir":
        argv = ["--artifacts-dir", str(tmp_path / "nope")]
        expect = "artifacts-dir-input-dir-missing"
    elif setup == "not-a-dir":
        filepath = tmp_path / "afile"
        filepath.write_text("x", encoding="utf-8")
        argv = ["--artifacts-dir", str(filepath)]
        expect = "artifacts-dir-input-dir-not-a-directory"
    else:
        directory = tmp_path / "empty"
        directory.mkdir()
        (directory / "plan-20260925-010000.json").write_text("{}", encoding="utf-8")
        (directory / "notes.txt").write_text("x", encoding="utf-8")
        argv = ["--artifacts-dir", str(directory)]
        expect = "no-drift-watch-reports"
    code, log = run_main(argv, runner=runner)
    assert code == task.EXIT_REFUSED
    assert expect in log.text
    assert runner.calls == []


@pytest.mark.parametrize("kind", ["invalid-json", "plan-mode", "drift-mismatch"])
def test_newest_invalid_never_falls_back(tmp_path: Path, kind: str) -> None:
    """最新候选 malformed/plan/自洽破损 → 拒绝；绝不回退更旧（可 drift=true
    的旧报告绝不冒充最新证据）。"""
    directory, _ = seed_dir(tmp_path, newest_drift=True)  # 旧份 drift=false
    # 旧份改写为合法 drift=true（若回退即会触发移交——必须零发生）
    write_report(directory, make_report(
        drift=True, started="2026-09-25T01:00:00Z", ended="2026-09-25T01:00:01Z"),
        "drift-watch-20260925-010000")
    newest = directory / "drift-watch-20260925-020000.json"
    if kind == "invalid-json":
        newest.write_text("{ not json", encoding="utf-8")
        expect = "report-invalid-json"
    elif kind == "plan-mode":
        write_report(directory, make_report(
            drift=True, started="2026-09-25T02:00:00Z", ended="2026-09-25T02:00:01Z",
            mode="plan"), "drift-watch-20260925-020000")
        expect = "mode 非 execute"
    else:
        report = make_report(drift=True, started="2026-09-25T02:00:00Z",
                             ended="2026-09-25T02:00:01Z")
        report["counts"] = {"pass": 9, "fail": 9}  # counts↔checks 破损
        write_report(directory, report, "drift-watch-20260925-020000")
        expect = "report-counts-mismatch"
    runner = FakeRunner()
    code, log = run_main(["--artifacts-dir", str(directory),
                          "--secret-file", str(write_secret(tmp_path)),
                          "--execute", "--confirm", task.TASK_CONFIRM_PHRASE],
                         runner=runner)
    assert code == task.EXIT_REFUSED
    assert expect in log.text
    assert "绝不回退" in log.text
    assert runner.calls == []  # 零移交（含零 Transport 面构造）


def test_candidate_stamp_invalid_refused(tmp_path: Path) -> None:
    """候选文件名时间戳非真实日历时刻（20261301 = 月 13）→ 无法定序拒绝。"""
    directory, _ = seed_dir(tmp_path, newest_drift=True)
    (directory / "drift-watch-20261301-000000.json").write_text("{}", encoding="utf-8")
    runner = FakeRunner()
    code, log = run_main(["--artifacts-dir", str(directory)], runner=runner)
    assert code == task.EXIT_REFUSED
    assert "candidate-filename-timestamp-invalid" in log.text
    assert runner.calls == []


def test_traversal_refused(tmp_path: Path) -> None:
    directory, _ = seed_dir(tmp_path, newest_drift=True)
    runner = FakeRunner()
    code, log = run_main(["--artifacts-dir", str(directory / ".." / directory.name)],
                         runner=runner)
    assert code == task.EXIT_REFUSED
    assert "path-traversal" in log.text
    code, log = run_main(["--report", str(tmp_path / ".." / tmp_path.name
                                           / "x.json")], runner=runner)
    assert code == task.EXIT_REFUSED
    assert "path-traversal" in log.text
    assert runner.calls == []


def test_symlinked_artifacts_dir_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlink 不可用（权限/平台）")
    code, log = run_main(["--artifacts-dir", str(link)])
    assert code == task.EXIT_REFUSED
    assert "artifacts-dir-symlink-target" in log.text


def test_artifacts_dir_unreadable_fail_closed(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """R1 回归：os.scandir 抛 PermissionError → 固定 reason + exit 2 +
    零 runner 调用；异常文本与本地路径绝不外泄。"""

    def _denied(*args, **kwargs):
        raise PermissionError(13, "Permission denied", f"{tmp_path}\\系统目录")

    monkeypatch.setattr(os, "scandir", _denied)
    directory, _ = seed_dir(tmp_path, newest_drift=True)
    runner = FakeRunner()
    code, log = run_main(["--artifacts-dir", str(directory)], runner=runner)
    assert code == task.EXIT_REFUSED
    assert "artifacts-dir-unreadable" in log.text
    assert runner.calls == []
    for leaked in ("Permission denied", "[Errno 13]", str(tmp_path),
                   str(tmp_path).replace("\\", "/"), "系统目录"):
        assert leaked not in log.text


# ---------------------------------------------------------------- drift=false / plan 零面


def test_drift_false_plan_and_execute_skip(tmp_path: Path) -> None:
    """drift=false：plan 与 execute 均 exit 0 + skipped-no-alerts +
    零 runner 调用 + 零写入（目录树快照不变——台账面绝无触碰）。"""
    directory, _ = seed_dir(tmp_path, newest_drift=False)
    secret = write_secret(tmp_path)
    before = tree_snapshot(tmp_path)
    runner = FakeRunner()
    code, log = run_main(["--artifacts-dir", str(directory)], runner=runner)
    assert code == task.EXIT_OK
    assert "skipped-no-alerts" in log.text
    code, log = run_main(["--artifacts-dir", str(directory),
                          "--secret-file", str(secret), "--execute",
                          "--confirm", task.TASK_CONFIRM_PHRASE], runner=runner)
    assert code == task.EXIT_OK
    assert "零 dispatch 子进程" in log.text
    assert runner.calls == []  # execute 且 drift=false：不构造子进程
    assert tree_snapshot(tmp_path) == before  # plan/skip 零写入


def test_plan_zero_network_zero_subprocess(tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """socket+subprocess 双阻断下 plan（drift=true）照常 exit 3——plan
    路径零网络构造、零子进程的结构性证明。"""
    directory, _ = seed_dir(tmp_path, newest_drift=True)

    def _blocked(*args, **kwargs):
        raise AssertionError("plan 绝不触达 network/subprocess")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    import subprocess as subprocess_module
    monkeypatch.setattr(subprocess_module, "run", _blocked)
    monkeypatch.setattr(subprocess_module, "Popen", _blocked)
    code, log = run_main(["--artifacts-dir", str(directory)])
    assert code == task.EXIT_DISPATCH_REQUIRED
    assert "dispatch-would-be-required" in log.text


# ---------------------------------------------------------------- execute 门禁


@pytest.mark.parametrize("argv_extra,expect", [
    (["--execute"], "必配 --confirm"),
    (["--execute", "--confirm", dispatch_mod.CONFIRM_PHRASE], "精确匹配"),
    (["--execute", "--confirm", watcher_mod.CONFIRM_PHRASE], "精确匹配"),
    (["--execute", "--confirm", "execute production drift watch alert task"], "精确匹配"),
    (["--execute", "--confirm", task.TASK_CONFIRM_PHRASE], "需 --secret-file"),
    (["--execute", "--confirm", task.TASK_CONFIRM_PHRASE,
      "--secret-file", "{secret-missing}"], "secret-file-missing"),
])
def test_execute_gates_fail_closed(tmp_path: Path, argv_extra: list[str],
                                   expect: str) -> None:
    """三重门禁缺一/近似 → exit 2 零 runner 调用（先于一切读取与构造）。"""
    directory, _ = seed_dir(tmp_path, newest_drift=True)
    argv = ["--artifacts-dir", str(directory)]
    write_secret(tmp_path)  # 确保真实 secret 存在：缺失场景走占位路径
    argv_extra = [str(tmp_path / "no-such-secret.json")
                  if item == "{secret-missing}" else item
                  for item in argv_extra]
    runner = FakeRunner()
    code, log = run_main(argv + argv_extra, runner=runner)
    assert code == task.EXIT_REFUSED
    assert expect in log.text
    assert runner.calls == []


def test_secret_file_without_execute_refused(tmp_path: Path) -> None:
    """plan 不读取 secret：--secret-file 无 --execute → 拒绝（防错觉）。"""
    directory, _ = seed_dir(tmp_path, newest_drift=True)
    code, log = run_main(["--artifacts-dir", str(directory),
                          "--secret-file", str(write_secret(tmp_path))])
    assert code == task.EXIT_REFUSED
    assert "仅 execute" in log.text


# ---------------------------------------------------------------- 移交传播


def test_execute_handoff_success(tmp_path: Path) -> None:
    """drift=true execute → 恰一次白名单 argv 移交 M14-135；退出码与
    stdout 脱敏回显；--artifact-dir 透传。"""
    directory, newest = seed_dir(tmp_path, newest_drift=True)
    secret = write_secret(tmp_path)
    out_dir = tmp_path / "dispatch-out"
    runner = FakeRunner(returncode=0, stdout="[drift-dispatch] 分发: http_status=200 → sent")
    code, log = run_main(["--artifacts-dir", str(directory),
                          "--secret-file", str(secret),
                          "--dispatch-artifact-dir", str(out_dir),
                          "--execute", "--confirm", task.TASK_CONFIRM_PHRASE],
                         runner=runner)
    assert code == task.EXIT_OK
    assert len(runner.calls) == 1
    argv = runner.calls[0]
    assert task.is_allowed_dispatch_argv(argv)
    assert argv[3] == str(newest)          # 最新报告精确移交
    assert argv[5] == str(secret)          # secret 原样透传（不读取）
    assert argv[-1] == dispatch_mod.CONFIRM_PHRASE  # M14-135 自有短语收尾
    assert "http_status=200" in log.text   # 子进程 stdout 回显
    _ = newest


def test_execute_dispatch_failure_and_duplicate_propagation(tmp_path: Path) -> None:
    """M14-135 拒绝（分发失败 / duplicate-dispatch）退出码原样透传 2；
    幂等门由 M14-135 承担——本工具零复制零旁路。"""
    directory, _ = seed_dir(tmp_path, newest_drift=True)
    secret = write_secret(tmp_path)
    argv = ["--artifacts-dir", str(directory), "--secret-file", str(secret),
            "--execute", "--confirm", task.TASK_CONFIRM_PHRASE]
    runner = FakeRunner(returncode=2, stdout="[drift-dispatch] 分发失败: http_status=500")
    code, log = run_main(list(argv), runner=runner)
    assert code == task.EXIT_REFUSED
    assert "分发失败" in log.text
    assert len(runner.calls) == 1
    runner = FakeRunner(returncode=2, stdout="[drift-dispatch] 拒绝: duplicate-dispatch")
    code, log = run_main(list(argv), runner=runner)
    assert code == task.EXIT_REFUSED
    assert "duplicate-dispatch" in log.text
    assert len(runner.calls) == 1


def test_execute_runner_error(tmp_path: Path) -> None:
    """子进程不可执行/超时 → RunnerError 固定类别、exit 2。"""
    directory, _ = seed_dir(tmp_path, newest_drift=True)
    secret = write_secret(tmp_path)
    runner = FakeRunner(error=task.RunnerError("dispatch-subprocess-unavailable"))
    code, log = run_main(["--artifacts-dir", str(directory),
                          "--secret-file", str(secret), "--execute",
                          "--confirm", task.TASK_CONFIRM_PHRASE], runner=runner)
    assert code == task.EXIT_REFUSED
    assert "dispatch-subprocess-unavailable" in log.text


def test_execute_gated_runner_wraps_real_runner() -> None:
    """默认执行面 = GatedDispatchRunner(RealRunner)：非白名单 argv 在
    内层执行之前拒绝（结构性门禁，不依赖调用方自觉）。"""
    gate = task.GatedDispatchRunner(FakeRunner())
    with pytest.raises(task.RunnerError, match="not-whitelisted"):
        gate.run(["schtasks.exe", "/Create", "/TN", "x", "/F"])
    good = [sys.executable, str(task.DISPATCH_TOOL), "--report", "D:/r.json",
            "--secret-file", "D:/s.json", "--execute", "--confirm",
            dispatch_mod.CONFIRM_PHRASE]
    result = gate.run(good)
    assert result.returncode == 0


# ---------------------------------------------------------------- 脱敏与路径安全


def test_sanitization_stdout(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """投毒 detail（secret 形态 marker）绝不入本工具 stdout；子进程回显
    经 redact 终防线；stdout 绝无绝对本地路径（含正反斜杠双检）。"""
    directory, _ = seed_dir(tmp_path, newest_drift=True)
    newest = directory / "drift-watch-20260925-020000.json"
    report = make_report(drift=True, started="2026-09-25T02:00:00Z",
                         ended="2026-09-25T02:00:01Z")
    report["checks"][2]["detail"] = f"state=running {MARK_TOKEN}"
    write_report(directory, report, "drift-watch-20260925-020000")
    _ = newest
    code, log = run_main(["--artifacts-dir", str(directory)])
    assert code == task.EXIT_DISPATCH_REQUIRED
    assert MARK_TOKEN not in log.text
    # execute 回显脱敏：子进程 stdout 携带 secret 形态 marker → 回显前清除
    secret = write_secret(tmp_path)
    runner = FakeRunner(returncode=0, stdout=f"echo {MARK_TOKEN}")
    code, log = run_main(["--artifacts-dir", str(directory),
                          "--secret-file", str(secret), "--execute",
                          "--confirm", task.TASK_CONFIRM_PHRASE], runner=runner)
    assert code == task.EXIT_OK
    assert MARK_TOKEN not in log.text
    assert "[REDACTED:token]" in log.text
    # stdout 绝无绝对本地路径（本工具 log 行只含 stem/hash/计数）
    printed = capsys.readouterr().out
    for text in (log.text, printed):
        for variant in {str(tmp_path), str(tmp_path).replace("\\", "/")}:
            assert variant not in text


def test_plan_writes_nothing_anywhere(tmp_path: Path) -> None:
    """plan 成功/拒绝两种结局都零写入（含 M14-135 台账目录零创建）。"""
    directory, _ = seed_dir(tmp_path, newest_drift=True)
    watch_root = tmp_path / "watch"
    watch_root.mkdir()
    before = tree_snapshot(tmp_path)
    run_main(["--artifacts-dir", str(directory)])
    run_main(["--artifacts-dir", str(tmp_path / "nope")])
    assert tree_snapshot(tmp_path) == before
    assert list(watch_root.iterdir()) == []
