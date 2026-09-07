"""tools.android_smoke.runner 的纯单元测试（4a/4b/4c 小步，全依赖注入）。"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

import tools.android_smoke.runner as runner_module
import tools.android_smoke.server as server_module
from tools.android_smoke.interaction import swipe_up_args
from tools.android_smoke.runner import (
    ACTIVITY_NAME,
    AUDIT_PATH,
    CLEAR_TEXT_BACKSPACE_COUNT,
    DEFAULT_HOST,
    DEFAULT_PORT,
    EXPECTED_ENDPOINTS,
    GOVERNANCE_ENTRY_TEXT,
    GOVERNANCE_HOME_TEXT,
    GOVERNANCE_SECTIONS,
    HOME_API_HEALTH_TEXT,
    HOME_API_OK_TEXT,
    HOME_TEXT,
    HOME_WAIT_TIMEOUT,
    KEYCODE_DEL,
    KEYCODE_MOVE_END,
    LOOKUP_INPUT_HINT,
    LOOKUP_KEYBOARD_DISMISS_SECONDS,
    LOOKUP_QUERY,
    LOOKUP_RESULT_TEXT,
    LOOKUP_RUN_TEXT,
    LOOKUP_SCROLL_SETTLE_SECONDS,
    PACKAGE_NAME,
    PHYSICAL_BASE_URL_TEMPLATE,
    PLAN_PREVIEW_TEXT,
    PREVIEW_PLAN_TEXT,
    RunnerConfig,
    RunnerError,
    SCROLL_DURATION_MS,
    SCROLL_FROM_Y_PERCENT,
    SCROLL_TO_Y_PERCENT,
    SCROLL_X_PERCENT,
    SEARCH_INPUT_HINT,
    SEARCH_PROVIDERS_TEXT,
    SEARCH_QUERY,
    SEARCH_QUERY_ID_TEXT,
    SEARCH_RUN_TEXT,
    SEARCH_SUMMARY_TEXT,
    SEARCH_TAB_TEXT,
    SETTINGS_API_SECTION_TEXT,
    SETTINGS_FIELD_LABEL,
    SETTINGS_SAVED_NOTICE_TEXT,
    SETTINGS_SAVE_TEXT,
    SETTINGS_TAB_TEXT,
    STAGE_WAIT_TIMEOUT,
    SmokeRunner,
    StageRecorder,
    analyze_mock_requests,
    assert_mock_contract,
    main,
    parse_mock_log,
    prepare_output_directory,
    sha256_file,
    write_json_atomic,
    append_text_atomic,
    build_config,
)


def make_config(**overrides) -> RunnerConfig:
    """构造一个最小合法配置；默认 skip-install 以便免 APK。"""
    kwargs = dict(
        serial="emulator-5554",
        apk=None,
        output=None,
        skip_install=True,
        device_type="emulator",
    )
    kwargs.update(overrides)
    return RunnerConfig(**kwargs)


# r2：滚动坐标按实测屏幕尺寸换算（FakeAdb 默认 1080x2400 = 模拟器）。
# 百分比映射必须逐值复现 M12-06 验证过的 (540,1800,540,600,400)；
# 720x1600 物理屏上则换算为全程落屏内的 (360,1200,360,400,400)。
EMULATOR_SWIPE = swipe_up_args(
    1080, 2400, SCROLL_X_PERCENT, SCROLL_FROM_Y_PERCENT, SCROLL_TO_Y_PERCENT, SCROLL_DURATION_MS
)
PHYSICAL_SWIPE = swipe_up_args(
    720, 1600, SCROLL_X_PERCENT, SCROLL_FROM_Y_PERCENT, SCROLL_TO_Y_PERCENT, SCROLL_DURATION_MS
)


class TestBuildConfig:
    def test_defaults(self):
        config = build_config(["--serial", "emulator-5554", "--skip-install"])
        assert config.serial == "emulator-5554"
        assert config.apk is None
        assert config.output is None
        assert config.host == DEFAULT_HOST
        assert config.port == DEFAULT_PORT
        assert config.skip_install is True
        assert config.keep_output is False

    def test_all_flags(self, tmp_path: Path):
        apk = tmp_path / "app.apk"
        apk.write_bytes(b"fake")
        config = build_config(
            [
                "--serial", "S",
                "--apk", str(apk),
                "--output", str(tmp_path / "out"),
                "--host", "127.0.0.1",
                "--port", "9000",
                "--skip-install",
                "--keep-output",
            ]
        )
        assert config.serial == "S"
        assert config.apk == str(apk)
        assert config.output == str(tmp_path / "out")
        assert config.port == 9000
        assert config.keep_output is True

    def test_serial_required(self):
        with pytest.raises(RunnerError):
            build_config(["--skip-install"])

    def test_serial_must_be_non_empty(self):
        with pytest.raises(RunnerError):
            build_config(["--serial", "  ", "--skip-install"])

    def test_non_loopback_host_rejected(self):
        with pytest.raises(RunnerError):
            build_config(["--serial", "S", "--host", "0.0.0.0", "--skip-install"])

    def test_invalid_port_rejected(self):
        for port in ("0", "65536", "-1"):
            with pytest.raises(RunnerError):
                build_config(["--serial", "S", "--port", port, "--skip-install"])

    def test_apk_optional_with_skip_install(self, tmp_path: Path):
        config = build_config(["--serial", "S", "--skip-install"])
        assert config.apk is None

    def test_missing_apk_rejected_without_skip_install(self):
        with pytest.raises(RunnerError):
            build_config(["--serial", "S", "--apk", "no/such/app.apk"])

    def test_apk_must_be_regular_file(self, tmp_path: Path):
        with pytest.raises(RunnerError):
            build_config(["--serial", "S", "--apk", str(tmp_path)])

    def test_apk_none_rejected_without_skip_install(self):
        with pytest.raises(RunnerError):
            make_config(skip_install=False, apk=None)


class TestRunnerConfigValidation:
    def test_frozen(self):
        config = make_config()
        with pytest.raises(Exception):
            config.serial = "other"


class TestPrepareOutputDirectory:
    def test_explicit_output_created(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = make_config(output=str(tmp_path / "nested" / "run1"))
        result = prepare_output_directory(config)
        assert result.is_dir()
        assert result == tmp_path / "nested" / "run1"

    def test_explicit_output_existing_rejected(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        existing = tmp_path / "already"
        existing.mkdir()
        with pytest.raises(RunnerError):
            prepare_output_directory(make_config(output=str(existing)))

    def test_default_output_created_when_missing(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = prepare_output_directory(make_config())
        assert result == tmp_path / ".verify" / "android-smoke"
        assert result.is_dir()

    def test_default_output_existing_yields_new_timestamped_dir(
        self, tmp_path: Path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        existing = tmp_path / ".verify" / "android-smoke"
        existing.mkdir(parents=True)
        (existing / "summary.json").write_text("{}", encoding="utf-8")
        result = prepare_output_directory(make_config())
        assert result.is_dir()
        assert result != existing
        assert result.parent == tmp_path / ".verify"
        assert result.name != "android-smoke"

    def test_timestamp_conflict_gets_suffix(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        verify = tmp_path / ".verify"
        existing = verify / "android-smoke"
        existing.mkdir(parents=True)
        # 同秒内两次调用会撞上同一个时间戳，第二次应得到带后缀的新目录
        first = prepare_output_directory(make_config())
        second = prepare_output_directory(make_config())
        assert first != second
        assert first.is_dir() and second.is_dir()

    def test_existing_evidence_never_deleted(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        existing = tmp_path / ".verify" / "android-smoke"
        existing.mkdir(parents=True)
        evidence = existing / "summary.json"
        evidence.write_text('{"keep": true}', encoding="utf-8")
        prepare_output_directory(make_config())
        assert evidence.read_text(encoding="utf-8") == '{"keep": true}'
        assert existing.is_dir()

    def test_repository_root_rejected(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(RunnerError):
            prepare_output_directory(make_config(output=str(tmp_path)))

    def test_filesystem_root_rejected(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(RunnerError):
            prepare_output_directory(make_config(output=os.path.abspath(os.sep)))

    def test_bare_relative_output_rejected(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(RunnerError):
            prepare_output_directory(make_config(output="out"))

    @pytest.mark.skipif(
        os.name == "nt" and not os.environ.get("AI_SMOKE_SYMLINK_TESTS"),
        reason="Windows 建符号链接需要特权，仅显式开启时运行",
    )
    def test_symlink_output_rejected(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        try:
            link.symlink_to(real, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlink not supported on this platform")
        with pytest.raises(RunnerError):
            prepare_output_directory(make_config(output=str(link / "run")))


class TestAtomicWrites:
    def test_write_json_atomic_roundtrip(self, tmp_path: Path):
        target = tmp_path / "summary.json"
        write_json_atomic(target, {"ok": True, "n": 1})
        assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True, "n": 1}
        # 不残留临时文件
        leftovers = [p for p in tmp_path.iterdir() if p.name != target.name]
        assert leftovers == []

    def test_write_json_atomic_unserializable_leaves_no_file(self, tmp_path: Path):
        target = tmp_path / "summary.json"

        class Bad:
            def __init__(self):
                self.x = self  # 自引用导致递归溢出

        with pytest.raises(Exception):
            write_json_atomic(target, {"bad": Bad()})
        assert not target.exists()
        assert list(tmp_path.iterdir()) == []

    def test_append_text_atomic(self, tmp_path: Path):
        target = tmp_path / "mock_requests.log"
        append_text_atomic(target, "GET /health\n")
        append_text_atomic(target, "GET /version\n")
        assert target.read_text(encoding="utf-8") == "GET /health\nGET /version\n"


# ---------------------------------------------------------------------------
# 4b：核心编排（全部依赖注入，不访问真机/真实 server）
# ---------------------------------------------------------------------------


class FakeAdbResult:
    """reverse_remove_tcp 的返回对象（check=False 不抛错，返回结果）。"""

    def __init__(self, returncode=0, timeout=False):
        self.returncode = returncode
        self.timeout = timeout


class FakeAdb:
    def __init__(
        self,
        fail_at=None,
        logcat_text="",
        remove_returncode=0,
        screen=(1080, 2400),
        ime_shown=True,
    ):
        self.fail_at = fail_at or set()
        self.logcat_text = logcat_text
        self.remove_returncode = remove_returncode
        # 实测屏幕尺寸（wm size 默认输出模拟器 1080x2400）与 IME 状态
        self.screen = screen
        self.ime_shown = ime_shown
        self.calls = []

    def _call(self, name, *args):
        self.calls.append((name,) + args)
        if name in self.fail_at:
            raise RuntimeError("adb %s failed" % name)

    def wait_device(self):
        self._call("wait_device")

    def install_apk(self, apk):
        self._call("install_apk", apk)

    def clear_app(self, package):
        self._call("clear_app", package)

    def clear_logcat(self):
        self._call("clear_logcat")

    def start_activity(self, package, activity):
        self._call("start_activity", package, activity)

    def reverse_tcp(self, port):
        self._call("reverse_tcp", port)

    def reverse_remove_tcp(self, port, check=True):
        self._call("reverse_remove_tcp", port, check)
        return FakeAdbResult(returncode=self.remove_returncode)

    def screen_size(self):
        self._call("screen_size")
        return self.screen

    def dumpsys_input_method(self):
        self._call("dumpsys_input_method")
        # 真实 dumpsys input_method 含 mInputShown 标记（API 34 实测格式）
        flag = "true" if self.ime_shown else "false"
        return ("  mInputShown=%s\n" % flag).encode("utf-8")

    def dump_ui(self):
        self._call("dump_ui")
        return b"<?xml version='1.0'?><hierarchy><node text='' /></hierarchy>"

    def capture_screenshot(self):
        self._call("capture_screenshot")
        return b"\x89PNG-fake"

    def dump_logcat(self):
        self._call("dump_logcat")
        return self.logcat_text.encode("utf-8")


class FakeUi:
    def __init__(self, fail_tab=None, fail_wait=None):
        self.fail_tab = fail_tab
        self.fail_wait = fail_wait
        self.clicked = []
        self.occurrence_clicked = []
        self.waited_for = []
        self.typed = []
        self.swipes = []
        # 每次 swipe 时已等待文本的快照，用于断言“滚动发生在哪些等待之间”
        self.swipe_wait_snapshots = []
        self.backs = 0
        # key_events 序列（如 Settings 清空输入框的 MOVE_END + DEL*64）
        self.key_event_sequences = []
        # 按发生顺序记录 swipe/back，用于断言“back 在两次 swipe 之前”
        self.actions = []

    def wait_for(self, text, timeout=60.0):
        self.waited_for.append((text, timeout))
        if text == self.fail_wait:
            raise RuntimeError("wait_for %s timed out" % text)
        return True

    def tap_text(self, text):
        self.clicked.append(text)
        if text == self.fail_tab:
            raise RuntimeError("tab %s not found" % text)

    def tap_text_occurrence(self, text, occurrence, contains=False):
        # 与真实 AndroidUiController.tap_text_occurrence 对齐：不落入
        # tap_text，确保 runner 不会退回点击第一个同名元素（如页面标题）。
        self.occurrence_clicked.append((text, occurrence, contains))
        self.clicked.append((text, occurrence))
        if text == self.fail_tab:
            raise RuntimeError("tab %s not found" % text)

    def type_text(self, text):
        self.typed.append(text)

    def swipe(self, start_x, start_y, end_x, end_y, duration_ms):
        args = (start_x, start_y, end_x, end_y, duration_ms)
        self.swipes.append(args)
        self.swipe_wait_snapshots.append(list(self.waited_for))
        self.actions.append(("swipe", args))

    def back(self):
        self.backs += 1
        self.actions.append(("back",))

    def key_events(self, codes):
        self.key_event_sequences.append(list(codes))


class FakeServer:
    def __init__(self, stop_error=None):
        self.stop_error = stop_error
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True
        if self.stop_error is not None:
            raise self.stop_error


def make_runner(
    tmp_path,
    *,
    adb=None,
    ui=None,
    server=None,
    skip_install=True,
    apk=None,
    sleep=lambda seconds: None,
    device_type="emulator",
    serial="emulator-5554",
):
    output = tmp_path / "out"
    output.mkdir()
    config = make_config(
        skip_install=skip_install, apk=apk, device_type=device_type, serial=serial
    )
    holder = {}

    def server_factory(log_path, port):
        holder["log_path"] = log_path
        holder["port"] = port
        return server if server is not None else FakeServer()

    runner = SmokeRunner(
        config=config,
        adb=adb or FakeAdb(),
        ui=ui or FakeUi(),
        server_factory=server_factory,
        output_dir=output,
        now=lambda: datetime(2026, 9, 7, tzinfo=timezone.utc),
        sleep=sleep,
    )
    runner.server_holder = holder
    return runner


def load_summary(runner):
    return json.loads(
        (runner.output_dir / "summary.json").read_text(encoding="utf-8")
    )


def stage_by_name(summary, name):
    matches = [s for s in summary["stages"] if s["name"] == name]
    assert len(matches) == 1
    return matches[0]


class TestStageRecorder:
    def test_lifecycle_and_failure_keeps_artifacts(self):
        recorder = StageRecorder()
        stage = recorder.start("wait-device")
        assert stage.status == "running"
        recorder.record_artifact(stage, Path("out/01-x.xml"))
        recorder.succeed(stage)
        failed = recorder.start("install-apk")
        recorder.record_artifact(failed, Path("out/02-x.xml"))
        recorder.fail(failed, "boom")
        recorder.skip("search", "后续小步实现")
        stages = recorder.stages()
        assert [s["status"] for s in stages] == [
            "passed", "failed", "skipped",
        ]
        assert stages[1]["artifacts"] == ["out/02-x.xml"]
        assert stages[1]["detail"] == "boom"
        assert recorder.has_failed() is True

    def test_no_failed_means_not_failed(self):
        recorder = StageRecorder()
        recorder.succeed(recorder.start("a"))
        recorder.skip("b", "later")
        assert recorder.has_failed() is False


class TestSmokeRunnerSuccess:
    def test_full_success_path(self, tmp_path):
        (tmp_path / "app.apk").write_bytes(b"apk")
        adb = FakeAdb()
        ui = FakeUi()
        server = FakeServer()
        runner = make_runner(
            tmp_path, adb=adb, ui=ui, server=server, skip_install=False,
            apk=str(tmp_path / "app.apk"),
        )
        summary = runner.run()

        assert server.started and server.stopped
        assert (runner.output_dir / "mock_requests.log").is_file()
        assert (runner.output_dir / "logcat-full.txt").is_file()
        assert (runner.output_dir / "summary.json").is_file()
        assert summary["overall_status"] == "passed"
        assert summary["exit_code"] == 0
        assert summary["has_blocking_issue"] is False
        assert summary["package_name"] == PACKAGE_NAME
        assert summary["serial"] == "emulator-5554"
        assert summary["request_stats"] == {"expected": 0, "unexpected": 0, "total": 0}
        names = [s["name"] for s in summary["stages"]]
        assert names[:6] == [
            "wait-device", "install-apk", "clear-app",
            "clear-logcat", "start-activity", "wait-home",
        ]
        assert (HOME_TEXT, HOME_WAIT_TIMEOUT) in ui.waited_for
        assert ui.clicked[:5] == ["首页", "学习", "搜索", "语音", "设置"]
        # 搜索/治理阶段真实执行且 passed，mock-contract 阶段 passed
        for name in ("search", "governance", "mock-contract"):
            stage = stage_by_name(summary, name)
            assert stage["status"] == "passed", (name, stage)
        # server_factory 收到 log 路径与配置端口
        assert runner.server_holder["port"] == DEFAULT_PORT
        assert runner.server_holder["log_path"] == runner.output_dir / "mock_requests.log"
        # adb 收到正确的包名/Activity
        assert ("start_activity", PACKAGE_NAME, ACTIVITY_NAME) in adb.calls
        # summary 不携带 apk 摘要（该 runner 未注入）
        assert summary["apk_sha256"] is None

    def test_skip_install_does_not_call_install(self, tmp_path):
        adb = FakeAdb()
        runner = make_runner(tmp_path, adb=adb, skip_install=True)
        summary = runner.run()
        assert not any(call[0] == "install_apk" for call in adb.calls)
        assert all(s["status"] in ("passed", "skipped") for s in summary["stages"])

    def test_stage_numbering_and_artifact_names_stable(self, tmp_path):
        runner = make_runner(tmp_path)
        summary = runner.run()
        artifacts = [
            artifact
            for s in summary["stages"]
            for artifact in s["artifacts"]
        ]
        assert artifacts == [
            "01-tab-home.xml", "01-tab-home.png",
            "02-tab-study.xml", "02-tab-study.png",
            "03-tab-search.xml", "03-tab-search.png",
            "04-tab-voice.xml", "04-tab-voice.png",
            "05-tab-settings.xml", "05-tab-settings.png",
            "06-search.xml", "06-search.png",
            "07-governance.xml", "07-governance.png",
        ]
        for artifact in artifacts:
            assert "\\" not in artifact
            assert (runner.output_dir / artifact).is_file()
        # XML 证据是 uiautomator 的原始字节，不是 JSON
        xml_text = (runner.output_dir / "01-tab-home.xml").read_text(encoding="utf-8")
        assert xml_text.lstrip().startswith("<?xml")


class TestSmokeRunnerFailures:
    def test_install_failure_marks_failed_and_keeps_evidence(self, tmp_path):
        (tmp_path / "app.apk").write_bytes(b"apk")
        adb = FakeAdb(fail_at={"install_apk"})
        server = FakeServer()
        runner = make_runner(
            tmp_path, adb=adb, server=server, skip_install=False,
            apk=str(tmp_path / "app.apk"),
        )
        with pytest.raises(RuntimeError, match="install_apk failed"):
            runner.run()
        summary = load_summary(runner)
        assert summary["overall_status"] == "failed"
        assert summary["exit_code"] == 1
        assert summary["has_blocking_issue"] is True
        stage = stage_by_name(summary, "install-apk")
        assert stage["status"] == "failed"
        assert "install_apk failed" in stage["detail"]
        # 失败时仍采集了当前 UI/XML/logcat/summary 证据
        assert (runner.output_dir / "logcat-full.txt").is_file()
        xml = [p for p in runner.output_dir.glob("*failure*.xml")]
        assert xml and stage["artifacts"]

    def test_ui_failure_still_stops_server_and_writes_summary(self, tmp_path):
        server = FakeServer()
        ui = FakeUi(fail_tab="语音")
        runner = make_runner(tmp_path, ui=ui, server=server)
        with pytest.raises(RuntimeError, match="tab 语音 not found"):
            runner.run()
        assert server.stopped is True
        summary = load_summary(runner)
        assert summary["overall_status"] == "failed"
        # 失败 tab 也已记录 clicked —— “尝试过点击”是事实
        assert ui.clicked == ["首页", "学习", "搜索", "语音"]
        stage = stage_by_name(summary, "tab-voice")
        assert stage["status"] == "failed"

    def test_server_stop_error_not_swallowed(self, tmp_path):
        server = FakeServer(stop_error=RuntimeError("port leak"))
        runner = make_runner(tmp_path, server=server)
        with pytest.raises(RunnerError, match="server stop failed"):
            runner.run()
        assert server.stopped is True
        summary = load_summary(runner)
        assert summary["overall_status"] == "passed"
        assert "port leak" in runner.server_stop_error

    def test_logcat_blocking_issue_fails_overall(self, tmp_path):
        adb = FakeAdb(logcat_text="...FATAL EXCEPTION: java.lang.NullPointerException...")
        runner = make_runner(tmp_path, adb=adb)
        summary = runner.run()
        assert summary["overall_status"] == "failed"
        assert summary["exit_code"] == 1
        # logcat_stats 是 analyze_logcat() 的完整 dict
        assert summary["logcat"] == {
            "total_lines": 1,
            "fatal_exception_count": 1,
            "anr_count": 0,
            "androidruntime_crash_count": 0,
            "package_crash_count": 0,
            "has_blocking_issue": True,
        }
        assert stage_by_name(summary, "dump-logcat")["status"] == "failed"

    def test_clean_logcat_passes(self, tmp_path):
        adb = FakeAdb(logcat_text="I/ActivityManager: displayed com.ailearningos.app")
        runner = make_runner(tmp_path, adb=adb)
        summary = runner.run()
        assert summary["overall_status"] == "passed"
        assert summary["logcat"]["has_blocking_issue"] is False


# ---------------------------------------------------------------------------
# 4c：mock_requests.log 解析与契约断言
# ---------------------------------------------------------------------------


def make_entry(**overrides):
    entry = {
        "timestamp_utc": "2026-09-07T00:00:00+00:00",
        "method": "GET",
        "path": "/health",
        "query_keys": [],
        "body_len": 0,
    }
    entry.update(overrides)
    return entry


def dump_lines(*entries):
    return [json.dumps(e, ensure_ascii=False) for e in entries]


def write_mock_log(output_dir, lines):
    (output_dir / "mock_requests.log").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


ALL_EXPECTED_ENTRIES = [
    make_entry(),
    make_entry(method="GET", path="/api/v1/auth/status"),
    make_entry(method="GET", path="/api/v1/system/privacy"),
    make_entry(method="GET", path="/api/v1/search/providers"),
    make_entry(
        method="POST", path="/api/v1/search/plan", body_len=48
    ),
    make_entry(
        method="POST", path="/api/v1/search/queries", body_len=48
    ),
    make_entry(method="GET", path="/api/v1/search/queries/9001"),
    make_entry(method="GET", path="/api/v1/version"),
    make_entry(method="GET", path="/api/v1/system/ops-snapshot"),
    # Tab 巡检触发的只读生命周期预取（mock 404 属预期）
    make_entry(method="GET", path="/api/v1/papers"),
    make_entry(method="GET", path="/api/v1/voice/providers"),
    make_entry(method="GET", path="/api/v1/audit", query_keys=["limit"]),
]


class TestParseMockLog:
    def test_valid_log_roundtrip(self):
        entries = parse_mock_log(dump_lines(*ALL_EXPECTED_ENTRIES))
        assert len(entries) == len(EXPECTED_ENDPOINTS)
        assert entries[-1]["query_keys"] == ["limit"]

    def test_blank_lines_skipped(self):
        lines = ["", json.dumps(make_entry()), "  ", ""]
        assert len(parse_mock_log(lines)) == 1

    def test_duplicate_requests_kept(self):
        lines = dump_lines(make_entry(), make_entry(), make_entry())
        assert len(parse_mock_log(lines)) == 3

    def test_bad_json_rejected(self):
        with pytest.raises(RunnerError, match="line 1.*invalid JSON"):
            parse_mock_log(["{not json"])

    def test_missing_field_rejected(self):
        bad = make_entry()
        del bad["query_keys"]
        with pytest.raises(RunnerError, match="missing required field"):
            parse_mock_log([json.dumps(bad)])

    def test_extra_field_rejected(self):
        bad = make_entry(extra="x")
        with pytest.raises(RunnerError, match="unexpected field"):
            parse_mock_log([json.dumps(bad)])

    def test_wrong_type_rejected(self):
        cases = [
            make_entry(method=123),
            make_entry(path=None),
            make_entry(timestamp_utc=""),
            make_entry(query_keys="limit"),
            make_entry(query_keys=[1, 2]),
            make_entry(body_len="0"),
            make_entry(body_len=True),
            make_entry(body_len=-1),
        ]
        for bad in cases:
            with pytest.raises(RunnerError):
                parse_mock_log([json.dumps(bad)])

    def test_non_object_line_rejected(self):
        with pytest.raises(RunnerError, match="must be a JSON object"):
            parse_mock_log(["[1, 2]"])

    def test_relative_path_rejected(self):
        with pytest.raises(RunnerError, match="path must start"):
            parse_mock_log([json.dumps(make_entry(path="health"))])


class TestMockContract:
    def test_all_expected_endpoints_pass(self):
        stats = assert_mock_contract(ALL_EXPECTED_ENTRIES)
        assert stats == {
            "expected": len(EXPECTED_ENDPOINTS),
            "unexpected": 0,
            "total": len(EXPECTED_ENDPOINTS),
        }

    def test_duplicate_requests_counted_multiple_times(self):
        stats, problems = analyze_mock_requests(
            [make_entry(), make_entry(), make_entry()]
        )
        assert stats == {"expected": 3, "unexpected": 0, "total": 3}
        assert problems == []

    def test_unknown_path_is_unexpected(self):
        stats, problems = analyze_mock_requests(
            [make_entry(), make_entry(path="/api/v1/unknown")]
        )
        assert stats == {"expected": 1, "unexpected": 1, "total": 2}
        assert any("/api/v1/unknown" in p for p in problems)
        with pytest.raises(RunnerError, match="unexpected method/path"):
            assert_mock_contract([make_entry(path="/api/v1/unknown")])

    def test_unexpected_method_is_unexpected(self):
        stats, problems = analyze_mock_requests(
            [make_entry(method="POST", path=AUDIT_PATH, body_len=3)]
        )
        assert stats["unexpected"] == 1
        assert any("POST /api/v1/audit" in p for p in problems)

    def test_audit_requires_exactly_limit(self):
        for keys in ([], ["offset"], ["limit", "offset"]):
            stats, problems = analyze_mock_requests(
                [make_entry(path=AUDIT_PATH, query_keys=keys)]
            )
            assert stats == {"expected": 0, "unexpected": 1, "total": 1}
            assert any(AUDIT_PATH in p for p in problems)
        # 精确 ["limit"] 才算 expected
        ok = analyze_mock_requests(
            [make_entry(path=AUDIT_PATH, query_keys=["limit"])]
        )
        assert ok[0]["expected"] == 1

    def test_expected_matches_mock_contract_surface(self):
        # 契约面与 mock_contract 声明的端点一一对应（含 2 个 Tab 巡检
        # 只读预取端点，共 12 个 method/path）
        assert len(EXPECTED_ENDPOINTS) == 12
        assert ("POST", "/api/v1/search/plan") in EXPECTED_ENDPOINTS
        # Tab 巡检触发的只读生命周期预取必须纳入契约（只读，允许 mock 404）
        assert ("GET", "/api/v1/papers") in EXPECTED_ENDPOINTS
        assert ("GET", "/api/v1/voice/providers") in EXPECTED_ENDPOINTS
        # 预取端点只允许 GET，不允许任何写请求
        assert ("POST", "/api/v1/papers") not in EXPECTED_ENDPOINTS
        assert ("POST", "/api/v1/voice/providers") not in EXPECTED_ENDPOINTS
        assert ("DELETE", "/api/v1/version") not in EXPECTED_ENDPOINTS

    def test_tab_patrol_prefetch_readonly_requests_expected(self):
        stats, problems = analyze_mock_requests(
            [
                make_entry(method="GET", path="/api/v1/papers"),
                make_entry(method="GET", path="/api/v1/voice/providers"),
            ]
        )
        assert stats == {"expected": 2, "unexpected": 0, "total": 2}
        assert problems == []


class TestRunnerContractStage:
    def test_expected_log_yields_stats_in_summary(self, tmp_path):
        runner = make_runner(tmp_path)
        write_mock_log(runner.output_dir, dump_lines(*ALL_EXPECTED_ENTRIES))
        summary = runner.run()
        assert summary["overall_status"] == "passed"
        assert summary["request_stats"] == {
            "expected": len(EXPECTED_ENDPOINTS),
            "unexpected": 0,
            "total": len(EXPECTED_ENDPOINTS),
        }
        assert stage_by_name(summary, "mock-contract")["status"] == "passed"

    def test_unexpected_request_fails_overall(self, tmp_path):
        runner = make_runner(tmp_path)
        write_mock_log(
            runner.output_dir,
            dump_lines(
                make_entry(),
                make_entry(path="/api/v1/search/queries/9002"),
            ),
        )
        summary = runner.run()
        assert summary["overall_status"] == "failed"
        assert summary["exit_code"] == 1
        assert summary["request_stats"] == {
            "expected": 1,
            "unexpected": 1,
            "total": 2,
        }
        stage = stage_by_name(summary, "mock-contract")
        assert stage["status"] == "failed"
        assert "9002" in stage["detail"]

    def test_bad_json_log_fails_contract_stage_without_raising(self, tmp_path):
        runner = make_runner(tmp_path)
        write_mock_log(runner.output_dir, ["{broken", ""])
        summary = runner.run()
        assert summary["overall_status"] == "failed"
        stage = stage_by_name(summary, "mock-contract")
        assert stage["status"] == "failed"
        assert "invalid JSON" in stage["detail"]

    def test_audit_wrong_query_keys_fails_contract(self, tmp_path):
        runner = make_runner(tmp_path)
        write_mock_log(
            runner.output_dir,
            dump_lines(make_entry(path=AUDIT_PATH, query_keys=["limit", "offset"])),
        )
        summary = runner.run()
        assert summary["overall_status"] == "failed"
        assert summary["request_stats"]["unexpected"] == 1


# ---------------------------------------------------------------------------
# 4c：搜索 / 治理 UI 流程（fake ui 记录动作序列）
# ---------------------------------------------------------------------------


class TestSearchFlow:
    def test_full_search_action_sequence(self, tmp_path):
        ui = FakeUi()
        runner = make_runner(tmp_path, ui=ui)
        summary = runner.run()
        assert stage_by_name(summary, "search")["status"] == "passed"
        # 点击序列：tab 之后的搜索流程点击（治理阶段在其后，另行断言）
        clicks = ui.clicked[5:-2]
        assert clicks == [
            SEARCH_TAB_TEXT,
            SEARCH_INPUT_HINT,
            PREVIEW_PLAN_TEXT,
            (SEARCH_RUN_TEXT, 1),
            LOOKUP_INPUT_HINT,
            LOOKUP_RUN_TEXT,
        ]
        # 搜索执行按钮必须走 tap_text_occurrence 且 occurrence=1（第 2 个
        # exact "搜索"），不能退回 tap_text 点到页面标题或底部导航。
        assert ui.occurrence_clicked == [(SEARCH_RUN_TEXT, 1, False)]
        # 执行搜索后先按需收起软键盘（查询框仍持有焦点，直接 swipe 会落在
        # 输入法区域被当成手势输入），再把结果卡滚进视口、等待结果概要，
        # 随后第二次滚动到回查区域（顺序见 test_search_scroll_ordering）。
        assert ui.actions[:3] == [
            ("back",),
            ("swipe", EMULATOR_SWIPE),
            ("swipe", EMULATOR_SWIPE),
        ]
        # 回查输入 9001 后键盘仍打开，「回查记录」按钮坐标被 IME 覆盖，
        # 点击会被输入法转换成字符（r6 证据：输入框出现 "9001g"）；先
        # back() + sleep 等 IME 收起再点按钮（顺序见 timing 测试）。
        assert ui.actions.count(("back",)) == 3
        assert ui.swipes == [
            EMULATOR_SWIPE,
            EMULATOR_SWIPE,
            EMULATOR_SWIPE,
        ]
        assert ui.backs == 3  # 搜索收键盘 + 回查收键盘 + 治理返回
        # 输入只含 ASCII，先查询后回查
        assert ui.typed == [SEARCH_QUERY, LOOKUP_QUERY]
        for ch in ui.typed[0]:
            assert 0x20 <= ord(ch) <= 0x7E
        # 等待序列覆盖关键界面状态（前 6 个：首页 + 搜索 5 个）
        waited = [text for text, _timeout in ui.waited_for]
        assert waited[:6] == [
            HOME_TEXT,
            SEARCH_PROVIDERS_TEXT,
            PLAN_PREVIEW_TEXT,
            SEARCH_SUMMARY_TEXT,
            SEARCH_QUERY_ID_TEXT,
            LOOKUP_RESULT_TEXT,
        ]
        assert all(
            t == HOME_TEXT or timeout == STAGE_WAIT_TIMEOUT
            for t, timeout in ui.waited_for
        )

    def test_lookup_scroll_settle_timing(self, tmp_path):
        # back -> sleep(0.8) 等 IME 收起 -> swipe -> sleep(0.8) 等
        # LazyColumn settle -> swipe；回查输入后再 back -> sleep(0.8) 等
        # IME 收起，才能点到被键盘覆盖的「回查记录」按钮。
        ui = FakeUi()
        sleeps = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            ui.actions.append(("sleep", seconds))

        runner = make_runner(tmp_path, ui=ui, sleep=fake_sleep)
        summary = runner.run()
        assert stage_by_name(summary, "search")["status"] == "passed"
        assert sleeps == [
            LOOKUP_KEYBOARD_DISMISS_SECONDS,
            LOOKUP_SCROLL_SETTLE_SECONDS,
            LOOKUP_KEYBOARD_DISMISS_SECONDS,
        ]
        assert LOOKUP_KEYBOARD_DISMISS_SECONDS == 0.8
        assert LOOKUP_SCROLL_SETTLE_SECONDS == 0.8
        assert ui.actions[:5] == [
            ("back",),
            ("sleep", 0.8),
            ("swipe", EMULATOR_SWIPE),
            ("sleep", 0.8),
            ("swipe", EMULATOR_SWIPE),
        ]
        # 回查输入后：back -> sleep(0.8) 收起 IME，之后才点「回查记录」。
        # FakeUi 不记录 tap 进 actions，故第二个 back 之后紧跟 sleep；
        # actions[7:] 是治理阶段（swipe + back，无 sleep），切片必须截止
        # 于 [5:7] 以免误取。
        assert ui.actions[5:7] == [("back",), ("sleep", 0.8)]
        # 回查收键盘的 sleep 之后再无 sleep；治理阶段只有 swipe 与 back
        assert not any(a[0] == "sleep" for a in ui.actions[7:])
        assert ui.actions[7:] == [
            ("swipe", EMULATOR_SWIPE),
            ("back",),
        ]

    def test_search_wait_failure_marks_stage_failed(self, tmp_path):
        ui = FakeUi(fail_wait=SEARCH_SUMMARY_TEXT)
        runner = make_runner(tmp_path, ui=ui)
        with pytest.raises(RuntimeError, match="timed out"):
            runner.run()
        summary = load_summary(runner)
        assert summary["overall_status"] == "failed"
        stage = stage_by_name(summary, "search")
        assert stage["status"] == "failed"
        # 失败仍保留证据
        assert (runner.output_dir / "summary.json").is_file()
        assert (runner.output_dir / "logcat-full.txt").is_file()


class TestGovernanceFlow:
    def test_governance_readonly_sequence(self, tmp_path):
        ui = FakeUi()
        runner = make_runner(tmp_path, ui=ui)
        summary = runner.run()
        assert stage_by_name(summary, "governance")["status"] == "passed"
        # 回首页 -> 进入治理；三个只读区块逐个等待；随后 back 返回
        assert ui.clicked[-2:] == [GOVERNANCE_HOME_TEXT, GOVERNANCE_ENTRY_TEXT]
        waited = [text for text, _t in ui.waited_for]
        for section in GOVERNANCE_SECTIONS:
            assert section in waited
        assert ui.backs == 3  # 搜索收键盘 + 回查收键盘 + 治理返回
        # 治理阶段没有任何输入（只读，无写操作）
        assert ui.typed == [SEARCH_QUERY, LOOKUP_QUERY]
        # 成功流程 swipe 总数：搜索两次（结果卡 + 回查区）+ 治理一次，
        # 坐标全部由实测屏幕尺寸按百分比换算
        assert ui.swipes == [
            EMULATOR_SWIPE,
            EMULATOR_SWIPE,
            EMULATOR_SWIPE,
        ]

    def test_governance_scroll_between_sections(self, tmp_path):
        # r7 证据：治理页可滚动，审计留痕在下方。治理滚动必须发生在
        # 发布版本 / 运行快照等待之后、审计留痕等待之前。
        ui = FakeUi()
        runner = make_runner(tmp_path, ui=ui)
        summary = runner.run()
        assert stage_by_name(summary, "governance")["status"] == "passed"
        assert ui.swipes[-1] == EMULATOR_SWIPE
        snapshot = ui.swipe_wait_snapshots[-1]
        # 滚动时前两个治理区块已等待出现（快照元素是 (text, timeout) 元组）
        assert any(text == "发布版本" for text, _timeout in snapshot)
        assert any(text == "运行快照" for text, _timeout in snapshot)
        # 滚动时审计留痕尚未等待（滚动后才等待它）
        assert not any(text == "审计留痕" for text, _timeout in snapshot)
        # 审计留痕的等待发生在滚动之后（最终 waited_for 含它）
        assert "审计留痕" in [text for text, _t in ui.waited_for]

    def test_governance_missing_section_fails(self, tmp_path):
        ui = FakeUi(fail_wait=GOVERNANCE_SECTIONS[-1])
        runner = make_runner(tmp_path, ui=ui)
        with pytest.raises(RuntimeError, match="timed out"):
            runner.run()
        summary = load_summary(runner)
        assert stage_by_name(summary, "governance")["status"] == "failed"
        # 失败发生在治理返回 back 之前（仅剩搜索/回查各收键盘的 2 次 back）
        assert ui.backs == 2
        # 治理滚动已发生（在前两个区块等待后、审计等待前）
        assert ui.swipes[-1] == EMULATOR_SWIPE


# ---------------------------------------------------------------------------
# M13-04 r2：屏幕相对滚动 / IME 按需收起 / finalize 韧性
# （r8 物理首跑：720x1600 上结果卡在视口外 + 设备掉线后 summary 缺席）
# ---------------------------------------------------------------------------


class TestRelativeScrolling:
    def test_percent_mapping_reproduces_emulator_coordinates(self):
        # 模拟器（1080x2400）逐值复现 M12-06 硬编码坐标：零行为变化
        assert EMULATOR_SWIPE == (540, 1800, 540, 600, 400)
        # 物理机（720x1600）换算后全程落屏内；旧 y=1800 越界
        assert PHYSICAL_SWIPE == (360, 1200, 360, 400, 400)
        assert max(PHYSICAL_SWIPE[1], PHYSICAL_SWIPE[3]) < 1600

    def test_scroll_uses_measured_screen_size(self, tmp_path):
        adb = FakeAdb(screen=(720, 1600))
        ui = FakeUi()
        runner = make_runner(tmp_path, adb=adb, ui=ui)
        summary = runner.run()
        assert ("screen_size",) in adb.calls
        assert summary["overall_status"] == "passed"
        # 搜索 2 次 + 治理 1 次，全部按 720x1600 换算
        assert ui.swipes == [PHYSICAL_SWIPE, PHYSICAL_SWIPE, PHYSICAL_SWIPE]

    def test_screen_size_queried_once_and_cached(self, tmp_path):
        adb = FakeAdb()
        make_runner(tmp_path, adb=adb).run()
        assert adb.calls.count(("screen_size",)) == 1

    def test_screen_size_failure_fails_search_stage(self, tmp_path):
        adb = FakeAdb(fail_at={"screen_size"})
        runner = make_runner(tmp_path, adb=adb)
        with pytest.raises(RuntimeError, match="screen_size failed"):
            runner.run()
        summary = load_summary(runner)
        assert summary["overall_status"] == "failed"
        stage = stage_by_name(summary, "search")
        assert stage["status"] == "failed"
        assert "screen_size failed" in stage["detail"]


class TestSearchScrollOrdering:
    def test_first_scroll_between_execute_and_result_waits(self, tmp_path):
        # r8 物理首跑教训：执行搜索后结果卡在计划卡下方视口外——第一滚
        # 必须发生在点击「执行搜索」(occurrence=1) 之后、等待「搜索结果
        # 概要」/「#9001」之前；第二滚（回查区域）在两者之后。
        ui = FakeUi()
        make_runner(tmp_path, ui=ui).run()
        assert ui.occurrence_clicked == [(SEARCH_RUN_TEXT, 1, False)]
        snap1 = [text for text, _timeout in ui.swipe_wait_snapshots[0]]
        assert PLAN_PREVIEW_TEXT in snap1  # 预览计划已等过（执行已点击）
        assert SEARCH_SUMMARY_TEXT not in snap1
        assert SEARCH_QUERY_ID_TEXT not in snap1
        snap2 = [text for text, _timeout in ui.swipe_wait_snapshots[1]]
        assert SEARCH_SUMMARY_TEXT in snap2
        assert SEARCH_QUERY_ID_TEXT in snap2
        assert LOOKUP_RESULT_TEXT not in snap2

    def test_ime_dismiss_skipped_when_dumpsys_says_closed(self, tmp_path):
        adb = FakeAdb(ime_shown=False)
        ui = FakeUi()
        runner = make_runner(tmp_path, adb=adb, ui=ui)
        summary = runner.run()
        assert summary["overall_status"] == "passed"
        # 两处收键盘动作都被跳过：只剩治理返回的 1 次 back；滚动照常
        assert ui.backs == 1
        assert ui.swipes == [EMULATOR_SWIPE, EMULATOR_SWIPE, EMULATOR_SWIPE]

    def test_ime_dismiss_conservative_when_dumpsys_fails(self, tmp_path):
        adb = FakeAdb(fail_at={"dumpsys_input_method"})
        ui = FakeUi()
        runner = make_runner(tmp_path, adb=adb, ui=ui)
        summary = runner.run()
        assert summary["overall_status"] == "passed"
        # 判断失败按「打开」保守处理：2 次收键盘 + 1 次治理返回
        assert ui.backs == 3
        assert ui.swipes == [EMULATOR_SWIPE, EMULATOR_SWIPE, EMULATOR_SWIPE]


class TestLogcatUnavailableFinalize:
    def test_dump_logcat_failure_still_writes_summary(self, tmp_path):
        # 设备阶段全部通过、收尾 dump logcat 失败（如 USB 掉线）：
        # summary 必须照常写出，dump-logcat 记 failed，不得隐性通过。
        adb = FakeAdb(fail_at={"dump_logcat"})
        runner = make_runner(tmp_path, adb=adb)
        summary = runner.run()
        assert summary["overall_status"] == "failed"
        assert summary["exit_code"] == 1
        assert summary["has_blocking_issue"] is True
        stage = stage_by_name(summary, "dump-logcat")
        assert stage["status"] == "failed"
        assert "logcat dump unavailable" in stage["detail"]
        assert summary["logcat"]["unavailable"] is True
        assert summary["logcat"]["total_lines"] == 0
        assert summary["logcat"]["has_blocking_issue"] is False
        # logcat 文件未产出（不能伪装成通过），summary 与其余证据都在
        assert not (runner.output_dir / "logcat-full.txt").exists()
        assert (runner.output_dir / "summary.json").is_file()
        assert stage_by_name(summary, "search")["status"] == "passed"

    def test_offline_after_search_failure_keeps_full_summary(self, tmp_path):
        # r8 物理首跑完整复现：搜索等待超时 -> 设备掉线 -> reverse 移除
        # 失败 + dump logcat 失败；所有失败阶段都必须在 summary 里可见。
        adb = FakeAdb(fail_at={"dump_logcat"}, remove_returncode=1)
        ui = FakeUi(fail_wait=SEARCH_SUMMARY_TEXT)
        runner = make_physical_runner(tmp_path, adb=adb, ui=ui)
        with pytest.raises(RuntimeError, match="timed out"):
            runner.run()
        summary = load_summary(runner)
        assert summary["overall_status"] == "failed"
        assert summary["exit_code"] == 1
        assert stage_by_name(summary, "search")["status"] == "failed"
        remove_stage = stage_by_name(summary, "remove-adb-reverse")
        assert remove_stage["status"] == "failed"
        assert "returncode=1" in remove_stage["detail"]
        assert stage_by_name(summary, "dump-logcat")["status"] == "failed"
        assert summary["logcat"]["unavailable"] is True

    def test_detail_sanitized_single_line_and_bounded(self):
        from tools.android_smoke.runner import _sanitize_detail

        noisy = "line1\nline2\twith\ttabs\x00\x1b[31m and " + "x" * 300
        cleaned = _sanitize_detail(noisy)
        assert "\n" not in cleaned
        assert "\t" not in cleaned
        assert "\x00" not in cleaned
        assert "\x1b" not in cleaned
        assert len(cleaned) <= 200


# ---------------------------------------------------------------------------
# M13-04：--device-type 配置校验
# ---------------------------------------------------------------------------


class TestDeviceTypeConfig:
    def test_default_is_emulator(self):
        config = build_config(["--serial", "emulator-5554", "--skip-install"])
        assert config.device_type == "emulator"

    def test_explicit_flag_parsed(self):
        config = build_config(
            ["--serial", "USB12345", "--skip-install", "--device-type", "physical"]
        )
        assert config.device_type == "physical"
        assert config.serial == "USB12345"

    def test_invalid_device_type_rejected(self):
        for bad in ("phone", "", "EMULATOR", "real"):
            with pytest.raises(RunnerError, match="device_type"):
                build_config(
                    ["--serial", "S", "--skip-install", "--device-type", bad]
                )

    def test_invalid_device_type_rejected_in_config(self):
        with pytest.raises(RunnerError, match="device_type"):
            make_config(device_type="phone")

    def test_physical_rejects_emulator_serial(self):
        # physical 模式必须使用显式 USB 物理 serial；emulator-* 属误用
        with pytest.raises(RunnerError, match="physical expects a USB"):
            make_config(device_type="physical", serial="emulator-5554")

    def test_physical_accepts_usb_serial(self):
        config = make_config(device_type="physical", serial="USB12345")
        assert config.device_type == "physical"

    def test_emulator_accepts_emulator_serial(self):
        config = make_config(device_type="emulator", serial="emulator-5554")
        assert config.device_type == "emulator"


# ---------------------------------------------------------------------------
# M13-04：物理模式阶段顺序 / 证据编号 / reverse 清理
# ---------------------------------------------------------------------------


PHYSICAL_STAGE_NAMES = [
    "wait-device",
    "setup-adb-reverse",
    "clear-app",
    "clear-logcat",
    "start-activity",
    "wait-home",
    "configure-base-url",
    "tab-home",
    "tab-study",
    "tab-search",
    "tab-voice",
    "tab-settings",
    "search",
    "governance",
    "remove-adb-reverse",
    "mock-contract",
    "dump-logcat",
]

EMULATOR_STAGE_NAMES = [
    "wait-device",
    "install-apk",
    "clear-app",
    "clear-logcat",
    "start-activity",
    "wait-home",
    "tab-home",
    "tab-study",
    "tab-search",
    "tab-voice",
    "tab-settings",
    "search",
    "governance",
    "mock-contract",
    "dump-logcat",
]


def make_physical_runner(tmp_path, *, adb=None, ui=None, server=None, **kwargs):
    return make_runner(
        tmp_path,
        adb=adb,
        ui=ui,
        server=server,
        device_type="physical",
        serial="USB12345",
        **kwargs,
    )


class TestPhysicalMode:
    def test_stage_order_and_all_passed(self, tmp_path):
        adb = FakeAdb()
        runner = make_physical_runner(tmp_path, adb=adb)
        summary = runner.run()
        names = [s["name"] for s in summary["stages"]]
        assert names == PHYSICAL_STAGE_NAMES
        assert all(s["status"] == "passed" for s in summary["stages"])
        assert summary["overall_status"] == "passed"
        assert summary["exit_code"] == 0
        assert summary["device_type"] == "physical"

    def test_reverse_setup_before_app_start_remove_at_teardown(self, tmp_path):
        adb = FakeAdb()
        runner = make_physical_runner(tmp_path, adb=adb)
        summary = runner.run()
        assert stage_by_name(summary, "setup-adb-reverse")["status"] == "passed"
        assert stage_by_name(summary, "remove-adb-reverse")["status"] == "passed"
        call_names = [c[0] for c in adb.calls]
        # reverse 建立在 wait_device 之后、start_activity 之前（App 启动前）
        assert call_names.index("reverse_tcp") > call_names.index("wait_device")
        assert call_names.index("reverse_tcp") < call_names.index("start_activity")
        # reverse 移除发生在成功路径收尾（start_activity 之后）
        assert call_names.index("reverse_remove_tcp") > call_names.index(
            "start_activity"
        )
        assert ("reverse_tcp", DEFAULT_PORT) in adb.calls
        assert ("reverse_remove_tcp", DEFAULT_PORT, False) in adb.calls

    def test_physical_evidence_numbering_shifted(self, tmp_path):
        runner = make_physical_runner(tmp_path)
        summary = runner.run()
        # artifacts 按阶段顺序列出：configure 阶段先于 tab 巡检，但其证据
        # 编号占用 06（tab 1..5 之后的下一个编号），search/governance 顺延
        artifacts = [
            artifact
            for s in summary["stages"]
            for artifact in s["artifacts"]
        ]
        assert artifacts == [
            "06-configure-base-url.xml", "06-configure-base-url.png",
            "01-tab-home.xml", "01-tab-home.png",
            "02-tab-study.xml", "02-tab-study.png",
            "03-tab-search.xml", "03-tab-search.png",
            "04-tab-voice.xml", "04-tab-voice.png",
            "05-tab-settings.xml", "05-tab-settings.png",
            "07-search.xml", "07-search.png",
            "08-governance.xml", "08-governance.png",
        ]
        for artifact in artifacts:
            assert (runner.output_dir / artifact).is_file()

    def test_device_failure_still_removes_reverse(self, tmp_path):
        # 设备阶段失败（如启动 Activity 失败）时，finally 仍移除 reverse
        adb = FakeAdb(fail_at={"start_activity"})
        runner = make_physical_runner(tmp_path, adb=adb)
        with pytest.raises(RuntimeError, match="start_activity failed"):
            runner.run()
        summary = load_summary(runner)
        assert summary["overall_status"] == "failed"
        assert stage_by_name(summary, "setup-adb-reverse")["status"] == "passed"
        assert stage_by_name(summary, "remove-adb-reverse")["status"] == "passed"
        assert ("reverse_remove_tcp", DEFAULT_PORT, False) in adb.calls

    def test_setup_reverse_failure_marks_stage_and_still_cleans(self, tmp_path):
        adb = FakeAdb(fail_at={"reverse_tcp"})
        runner = make_physical_runner(tmp_path, adb=adb)
        with pytest.raises(RuntimeError, match="reverse_tcp failed"):
            runner.run()
        summary = load_summary(runner)
        assert stage_by_name(summary, "setup-adb-reverse")["status"] == "failed"
        # 清理仍执行且成功
        assert stage_by_name(summary, "remove-adb-reverse")["status"] == "passed"

    def test_reverse_remove_nonzero_marks_stage_failed(self, tmp_path):
        # 移除返回非零（如设备已离线）：不抛出，阶段 failed，overall failed
        adb = FakeAdb(remove_returncode=1)
        runner = make_physical_runner(tmp_path, adb=adb)
        summary = runner.run()
        stage = stage_by_name(summary, "remove-adb-reverse")
        assert stage["status"] == "failed"
        assert "returncode=1" in stage["detail"]
        assert summary["overall_status"] == "failed"
        assert summary["exit_code"] == 1

    def test_reverse_remove_exception_marks_stage_failed(self, tmp_path):
        adb = FakeAdb(fail_at={"reverse_remove_tcp"})
        runner = make_physical_runner(tmp_path, adb=adb)
        summary = runner.run()
        stage = stage_by_name(summary, "remove-adb-reverse")
        assert stage["status"] == "failed"
        assert "RuntimeError" in stage["detail"]
        assert summary["overall_status"] == "failed"


# ---------------------------------------------------------------------------
# M13-04：物理模式 Settings URL 编辑序列
# ---------------------------------------------------------------------------


class TestConfigureBaseUrlFlow:
    def test_settings_editing_sequence(self, tmp_path):
        ui = FakeUi()
        runner = make_physical_runner(tmp_path, ui=ui)
        summary = runner.run()
        base_url = PHYSICAL_BASE_URL_TEMPLATE % DEFAULT_PORT
        assert stage_by_name(summary, "configure-base-url")["status"] == "passed"
        # 点击序列：进设置 -> 点输入框（label 即输入区）-> 保存 -> 回首页，
        # 之后才是 5 个 tab 巡检（首页/学习/搜索/语音/设置）
        assert ui.clicked[:4] == [
            SETTINGS_TAB_TEXT,
            SETTINGS_FIELD_LABEL,
            SETTINGS_SAVE_TEXT,
            HOME_TEXT,
        ]
        assert ui.clicked[4:9] == ["首页", "学习", "搜索", "语音", "设置"]
        # 输入：先回环 URL，再搜索查询与回查编号
        assert ui.typed == [base_url, SEARCH_QUERY, LOOKUP_QUERY]
        # 清空序列：光标移到末尾（MOVE_END）后连发 DEL 覆盖既有/默认值
        assert ui.key_event_sequences == [
            [KEYCODE_MOVE_END] + [KEYCODE_DEL] * CLEAR_TEXT_BACKSPACE_COUNT
        ]
        # 等待序列：首页就绪 -> API 地址卡片 -> 已保存提示 -> 当前生效 URL
        # -> 首页 /health 探测为「正常」（App 在用新地址）
        waited = [text for text, _timeout in ui.waited_for]
        assert waited[:6] == [
            HOME_TEXT,
            SETTINGS_API_SECTION_TEXT,
            SETTINGS_SAVED_NOTICE_TEXT,
            base_url,
            HOME_API_HEALTH_TEXT,
            HOME_API_OK_TEXT,
        ]
        for text, timeout in ui.waited_for[1:6]:
            assert timeout == STAGE_WAIT_TIMEOUT

    def test_clear_backspace_count_covers_known_urls(self):
        # DEL 次数必须覆盖 App 可能持有的默认/已存值长度（10.0.2.2 默认与
        # 回环 URL 均 22 字符），多余 DEL 在空输入框上是 no-op
        base_url = PHYSICAL_BASE_URL_TEMPLATE % DEFAULT_PORT
        assert CLEAR_TEXT_BACKSPACE_COUNT >= len("http://10.0.2.2:8000/")
        assert CLEAR_TEXT_BACKSPACE_COUNT >= len(base_url)
        assert CLEAR_TEXT_BACKSPACE_COUNT >= 32

    def test_custom_port_flows_into_base_url(self, tmp_path):
        ui = FakeUi()
        output = tmp_path / "out"
        output.mkdir()
        config = make_config(
            device_type="physical", serial="USB12345", port=9123
        )
        runner = SmokeRunner(
            config=config,
            adb=FakeAdb(),
            ui=ui,
            server_factory=lambda log_path, port: FakeServer(),
            output_dir=output,
            now=lambda: datetime(2026, 9, 7, tzinfo=timezone.utc),
        )
        summary = runner.run()
        assert summary["overall_status"] == "passed"
        assert ui.typed[0] == "http://127.0.0.1:9123/"
        assert ("reverse_tcp", 9123) in runner.adb.calls
        assert ("reverse_remove_tcp", 9123, False) in runner.adb.calls

    def test_configure_failure_marks_stage_failed(self, tmp_path):
        # 保存成功提示未出现（保存失败/校验拒绝）时阶段 failed
        ui = FakeUi(fail_wait=SETTINGS_SAVED_NOTICE_TEXT)
        runner = make_physical_runner(tmp_path, ui=ui)
        with pytest.raises(RuntimeError, match="timed out"):
            runner.run()
        summary = load_summary(runner)
        assert summary["overall_status"] == "failed"
        assert stage_by_name(summary, "configure-base-url")["status"] == "failed"
        # 清理仍执行
        assert stage_by_name(summary, "remove-adb-reverse")["status"] == "passed"

    def test_home_health_not_ok_fails_configure(self, tmp_path):
        # 回首页后 /health 探测未显示「正常」＝App 未用上新地址
        ui = FakeUi(fail_wait=HOME_API_OK_TEXT)
        runner = make_physical_runner(tmp_path, ui=ui)
        with pytest.raises(RuntimeError, match="timed out"):
            runner.run()
        summary = load_summary(runner)
        assert stage_by_name(summary, "configure-base-url")["status"] == "failed"


# ---------------------------------------------------------------------------
# M13-04：emulator 模式无回归
# ---------------------------------------------------------------------------


class TestEmulatorNoRegression:
    def test_emulator_never_calls_reverse(self, tmp_path):
        adb = FakeAdb()
        runner = make_runner(tmp_path, adb=adb)
        summary = runner.run()
        assert summary["device_type"] == "emulator"
        reverse_calls = [
            c for c in adb.calls if c[0] in ("reverse_tcp", "reverse_remove_tcp")
        ]
        assert reverse_calls == []
        names = [s["name"] for s in summary["stages"]]
        for physical_only in (
            "setup-adb-reverse",
            "configure-base-url",
            "remove-adb-reverse",
        ):
            assert physical_only not in names

    def test_emulator_stage_list_unchanged_15_stages(self, tmp_path):
        # 带 install 的完整 15 阶段与 M12-06 完全一致（无物理阶段插入）
        apk = tmp_path / "app.apk"
        apk.write_bytes(b"apk")
        runner = make_runner(
            tmp_path, skip_install=False, apk=str(apk)
        )
        summary = runner.run()
        names = [s["name"] for s in summary["stages"]]
        assert names == EMULATOR_STAGE_NAMES
        assert len(names) == 15

    def test_emulator_never_touches_settings_url(self, tmp_path):
        # 模拟器走 10.0.2.2 默认 base URL：不进 Settings 配置 URL
        ui = FakeUi()
        runner = make_runner(tmp_path, ui=ui)
        summary = runner.run()
        assert summary["overall_status"] == "passed"
        assert ui.typed == [SEARCH_QUERY, LOOKUP_QUERY]
        assert ui.key_event_sequences == []
        assert SETTINGS_FIELD_LABEL not in ui.clicked
        assert SETTINGS_SAVE_TEXT not in ui.clicked
        # 「设置」只作为 tab 巡检第 5 个 tab 出现
        assert ui.clicked[:5] == ["首页", "学习", "搜索", "语音", "设置"]

    def test_emulator_evidence_numbering_unchanged(self, tmp_path):
        runner = make_runner(tmp_path)
        summary = runner.run()
        artifacts = [
            artifact
            for s in summary["stages"]
            for artifact in s["artifacts"]
        ]
        assert artifacts == [
            "01-tab-home.xml", "01-tab-home.png",
            "02-tab-study.xml", "02-tab-study.png",
            "03-tab-search.xml", "03-tab-search.png",
            "04-tab-voice.xml", "04-tab-voice.png",
            "05-tab-settings.xml", "05-tab-settings.png",
            "06-search.xml", "06-search.png",
            "07-governance.xml", "07-governance.png",
        ]


# ---------------------------------------------------------------------------
# 4c：CLI main（模块级依赖全部 monkeypatch 为 fake）
# ---------------------------------------------------------------------------


def patch_cli_dependencies(monkeypatch, tmp_path, adb=None, ui=None, server=None):
    adb = adb or FakeAdb()
    ui = ui or FakeUi()
    server = server or FakeServer()

    def fake_adb_client(serial):
        assert isinstance(serial, str) and serial
        return adb

    def fake_server_cls(log_path, port):
        assert Path(log_path).name == "mock_requests.log"
        return server

    monkeypatch.setattr(runner_module, "AdbClient", fake_adb_client)
    monkeypatch.setattr(runner_module, "AndroidUiController", lambda adb=None: ui)
    monkeypatch.setattr(runner_module, "LoopbackMockServer", fake_server_cls)
    return adb, ui, server


class TestMain:
    def test_success_returns_zero_and_prints_summary_path(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)
        adb, ui, server = patch_cli_dependencies(monkeypatch, tmp_path)
        code = main(["--serial", "S", "--skip-install"])
        assert code == 0
        assert server.started and server.stopped
        out = capsys.readouterr().out
        assert "summary.json" in out
        summary = json.loads(
            (tmp_path / ".verify" / "android-smoke" / "summary.json").read_text(
                encoding="utf-8"
            )
        )
        assert summary["overall_status"] == "passed"
        # 搜索与治理真实执行（搜索收键盘 + 回查收键盘 + 治理返回，共 3 次 back）
        assert ui.backs == 3
        assert any(c[0] == "start_activity" for c in adb.calls)

    def test_apk_sha256_written_to_summary(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        apk = tmp_path / "app.apk"
        apk.write_bytes(b"fake-apk-bytes")
        patch_cli_dependencies(monkeypatch, tmp_path)
        assert main(
            ["--serial", "S", "--apk", str(apk), "--output", str(tmp_path / "out")]
        ) == 0
        summary = json.loads(
            (tmp_path / "out" / "summary.json").read_text(encoding="utf-8")
        )
        assert summary["apk_sha256"] == sha256_file(apk)
        assert summary["apk_sha256"] == hashlib.sha256(b"fake-apk-bytes").hexdigest()
        # summary 不泄漏本机绝对路径
        assert tmp_path.as_posix() not in json.dumps(summary)

    def test_failure_keeps_evidence_and_returns_summary_code(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)
        adb = FakeAdb(fail_at={"install_apk"})
        apk = tmp_path / "app.apk"
        apk.write_bytes(b"apk")
        patch_cli_dependencies(
            monkeypatch, tmp_path, adb=adb, server=FakeServer()
        )
        code = main(
            ["--serial", "S", "--apk", str(apk), "--output", str(tmp_path / "out")]
        )
        assert code == 1
        err = capsys.readouterr().err
        assert "evidence kept in" in err
        out_dir = tmp_path / "out"
        assert (out_dir / "summary.json").is_file()
        assert (out_dir / "logcat-full.txt").is_file()
        summary = json.loads(
            (out_dir / "summary.json").read_text(encoding="utf-8")
        )
        assert summary["overall_status"] == "failed"
        assert summary["exit_code"] == 1

    def test_config_error_returns_one_without_output_dir(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)
        patch_cli_dependencies(monkeypatch, tmp_path)
        code = main(["--skip-install"])  # 缺 --serial
        assert code == 1
        assert "android-smoke failed" in capsys.readouterr().err
        assert not (tmp_path / ".verify").exists()

    def test_missing_loopback_server_is_clear_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(runner_module, "LoopbackMockServer", None)
        monkeypatch.setattr(
            runner_module,
            "AdbClient",
            lambda serial: FakeAdb(fail_at={"wait_device"}),
        )
        code = main(["--serial", "S", "--skip-install"])
        assert code == 1
        # wait_device 先失败，尚未触达 server factory；直接调用 factory 验证报错
        # 把模块缓存置为 None 以模拟 ImportError，确保覆盖“不可导入”分支
        monkeypatch.setitem(sys.modules, "tools.android_smoke.server", None)
        with pytest.raises(RunnerError, match="LoopbackMockServer is not available"):
            runner_module._make_server_factory()(Path("x.log"), 8000)

    def test_make_server_factory_loads_from_server_module(self, tmp_path, monkeypatch):
        """真实惰性导入必须指向 tools/android_smoke/server.py。"""
        created = []

        class FakeLoopbackServer:
            def __init__(self, log_path, port):
                created.append((log_path, port))

        monkeypatch.setattr(runner_module, "LoopbackMockServer", None)
        monkeypatch.setattr(
            server_module, "LoopbackMockServer", FakeLoopbackServer
        )
        instance = runner_module._make_server_factory()(Path("x.log"), 8123)
        assert isinstance(instance, FakeLoopbackServer)
        assert created == [(Path("x.log"), 8123)]

    def test_physical_device_type_flows_through_cli(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        adb, ui, server = patch_cli_dependencies(monkeypatch, tmp_path)
        code = main(
            ["--serial", "USB12345", "--skip-install", "--device-type", "physical"]
        )
        assert code == 0
        assert server.started and server.stopped
        summary = json.loads(
            (tmp_path / ".verify" / "android-smoke" / "summary.json").read_text(
                encoding="utf-8"
            )
        )
        assert summary["device_type"] == "physical"
        assert summary["overall_status"] == "passed"
        names = [s["name"] for s in summary["stages"]]
        assert names == PHYSICAL_STAGE_NAMES
        assert ("reverse_tcp", DEFAULT_PORT) in adb.calls
        assert ("reverse_remove_tcp", DEFAULT_PORT, False) in adb.calls
        # Settings URL 编辑序列真实执行
        assert ui.typed[0] == PHYSICAL_BASE_URL_TEMPLATE % DEFAULT_PORT

    def test_cli_rejects_physical_with_emulator_serial(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        patch_cli_dependencies(monkeypatch, tmp_path)
        code = main(
            ["--serial", "emulator-5554", "--skip-install", "--device-type", "physical"]
        )
        assert code == 1
        assert "physical expects a USB" in capsys.readouterr().err
        # 配置校验在输出目录创建之前失败，不产出任何证据目录
        assert not (tmp_path / ".verify").exists()
