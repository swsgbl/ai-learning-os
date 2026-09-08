"""tools/android_smoke/logcat_analysis.py 的单元测试（不依赖 adb）。"""

from tools.android_smoke.logcat_analysis import analyze_logcat


def test_empty_input():
    result = analyze_logcat([], "com.example.app")
    assert result == {
        "total_lines": 0,
        "fatal_exception_count": 0,
        "anr_count": 0,
        "androidruntime_crash_count": 0,
        "package_crash_count": 0,
        "has_blocking_issue": False,
    }


def test_none_package():
    lines = [
        "09-07 12:00:00.000 E/AndroidRuntime: FATAL EXCEPTION: main",
    ]
    result = analyze_logcat(lines, None)
    assert result["fatal_exception_count"] == 1
    assert result["androidruntime_crash_count"] == 1
    assert result["package_crash_count"] == 0
    assert result["has_blocking_issue"] is True


def test_empty_package():
    result = analyze_logcat(["E/AndroidRuntime: com.example.app"], "   ")
    assert result["package_crash_count"] == 0


def test_fatal_exception_case_insensitive():
    lines = ["fatal exception: main", "FATAL EXCEPTION: main"]
    result = analyze_logcat(lines, "com.example.app")
    assert result["fatal_exception_count"] == 2


def test_anr_variants():
    lines = [
        "09-07 12:00:00.000 E/ActivityManager: ANR in com.example.app (com.example.app/.MainActivity)",
        "09-07 12:00:01.000 I/WindowManager: Input dispatching timed out (Waiting to send key event because the focused window has not finished processing all input events)",
        "09-07 12:00:02.000 W/anr: some trace",
    ]
    result = analyze_logcat(lines, "com.example.app")
    assert result["anr_count"] == 2  # 第 1/3 行是独立 "anr" 词元；第 2 行本就不含


def test_anr_word_boundary_no_substring_false_positive():
    # r9 真机证据：EMUI 日志的普通词内嵌 "anr" 子串（fileCanRead 的
    # "CanRead"、com.huawei.antivirus 附近的标识符），裸子串匹配把它们
    # 判成 ANR 导致 dump-logcat 阶段假阳性失败——必须词边界匹配。
    lines = [
        "09-08 04:19:42.106  2427  5940 I ThermalTraceTool: "
        "file:/proc/wifi/wifi_tem_stat,fileExists:false,fileCanRead:false",
        "09-08 04:19:09.210 25667 25692 W ContextImpl: Calling a method in "
        "the system process without a qualified user: "
        "android.app.ContextImpl.bindService:1777 com.huawei.antivirus.helper",
    ]
    result = analyze_logcat(lines, "com.ailearningos.app")
    assert result["anr_count"] == 0
    assert result["has_blocking_issue"] is False


def test_anr_standalone_token_still_detected():
    # 词边界收紧后真实 ANR 标记必须仍然命中：tag / "ANR in" / data/anr/ 路径
    lines = [
        "E/anr: writing ANR trace to /data/anr/traces.txt",
        "E/ActivityManager: ANR in com.ailearningos.app",
    ]
    result = analyze_logcat(lines, "com.ailearningos.app")
    assert result["anr_count"] == 2
    assert result["has_blocking_issue"] is True


def test_anr_in_keyword():
    result = analyze_logcat(["ANR in com.other.app"], "com.example.app")
    assert result["anr_count"] == 1
    assert result["has_blocking_issue"] is True


def test_androidruntime_and_package_crash():
    lines = [
        "09-07 12:00:00.000 E/AndroidRuntime: FATAL EXCEPTION: main",
        "09-07 12:00:00.001 E/AndroidRuntime: Process: com.example.app, PID: 12345",
        "09-07 12:00:00.002 E/AndroidRuntime: java.lang.NullPointerException",
    ]
    result = analyze_logcat(lines, "com.example.app")
    assert result["androidruntime_crash_count"] == 3
    assert result["package_crash_count"] == 1
    assert result["fatal_exception_count"] == 1


def test_uiautomator_androidruntime_startup_lines_not_blocking():
    # 真实失败复盘：每次 uiautomator dump 都产生多条普通 AndroidRuntime
    # 启动行（共 155 条），不能单独判定 blocking。
    lines = [
        "09-07 12:00:00.000 I/AndroidRuntime: Starting a new process for uiautomator",
        "09-07 12:00:00.001 D/AndroidRuntime: com.example.app loaded",
        "09-07 12:00:00.002 I/AndroidRuntime: Shutting down VM",
    ] * 10
    result = analyze_logcat(lines, "com.example.app")
    assert result["androidruntime_crash_count"] == 30
    assert result["package_crash_count"] == 10
    assert result["fatal_exception_count"] == 0
    assert result["anr_count"] == 0
    assert result["has_blocking_issue"] is False


def test_fatal_exception_still_blocking_alongside_androidruntime_noise():
    lines = [
        "09-07 12:00:00.000 I/AndroidRuntime: uiautomator startup line",
    ] * 20
    lines.append("09-07 12:00:01.000 E/AndroidRuntime: FATAL EXCEPTION: main")
    result = analyze_logcat(lines, "com.example.app")
    assert result["androidruntime_crash_count"] == 21
    assert result["fatal_exception_count"] == 1
    assert result["has_blocking_issue"] is True


def test_package_crash_requires_androidruntime():
    lines = [
        "09-07 12:00:00.000 I/System.out: com.example.app started",
    ]
    result = analyze_logcat(lines, "com.example.app")
    assert result["package_crash_count"] == 0
    assert result["androidruntime_crash_count"] == 0
    assert result["has_blocking_issue"] is False


def test_control_characters_stripped():
    line = "E/AndroidRuntime\x00: FATAL EXCEPTION\x1b: com.example.app"
    result = analyze_logcat([line], "com.example.app")
    assert result["fatal_exception_count"] == 1
    assert result["package_crash_count"] == 1


def test_total_lines_counts_empty_and_blank():
    lines = ["", "   ", "I/System: hello"]
    result = analyze_logcat(lines, "com.example.app")
    assert result["total_lines"] == 3
    assert result["has_blocking_issue"] is False


def test_clean_log_no_issues():
    lines = [
        "09-07 12:00:00.000 I/com.example.app: onCreate",
        "09-07 12:00:00.100 D/com.example.app: resumed",
    ]
    result = analyze_logcat(lines, "com.example.app")
    assert result["total_lines"] == 2
    assert result["has_blocking_issue"] is False


def test_generator_input():
    def gen():
        yield "E/AndroidRuntime: FATAL EXCEPTION: main"

    result = analyze_logcat(gen(), "com.example.app")
    assert result["total_lines"] == 1
    assert result["fatal_exception_count"] == 1


def test_non_string_lines_ignored_for_matching():
    lines = [b"E/AndroidRuntime: FATAL EXCEPTION", "E/AndroidRuntime: FATAL EXCEPTION"]
    result = analyze_logcat(lines, "com.example.app")
    assert result["total_lines"] == 2
    assert result["fatal_exception_count"] == 1


def test_package_match_case_insensitive():
    lines = ["E/AndroidRuntime: Process: COM.EXAMPLE.APP"]
    result = analyze_logcat(lines, "com.example.app")
    assert result["package_crash_count"] == 1


def test_mixed_multiline_crash_session():
    lines = [
        "09-07 12:00:00.000 E/AndroidRuntime: FATAL EXCEPTION: main",
        "09-07 12:00:00.001 E/AndroidRuntime: Process: com.example.app, PID: 1",
        "09-07 12:00:00.002 E/AndroidRuntime: java.lang.IllegalStateException",
        "09-07 12:00:00.003 W/ActivityManager: ANR in com.example.app",
        "09-07 12:00:00.004 I/chatty: uid=1000 expire 2 lines",
    ]
    result = analyze_logcat(lines, "com.example.app")
    assert result["total_lines"] == 5
    assert result["fatal_exception_count"] == 1
    assert result["anr_count"] == 1
    assert result["androidruntime_crash_count"] == 3
    assert result["package_crash_count"] == 1
    assert result["has_blocking_issue"] is True
