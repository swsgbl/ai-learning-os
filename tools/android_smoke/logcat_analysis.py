"""logcat 分析工具：从 logcat 行流中提取机器可读的崩溃/ANR 指标。

用于 Android 冒烟测试 harness，不依赖 adb —— 调用方负责采集 logcat 行，
本模块只做纯文本分析。
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

# FATAL EXCEPTION 行由 ART 打印，大小写固定，但为稳妥按大小写不敏感匹配。
_FATAL_EXCEPTION_RE = re.compile(r"fatal\s+exception", re.IGNORECASE)

# ANR 相关行："ANR in <pkg>"、"-anr-"、或 input dispatch 超时等 bionic/ActivityManager 输出。
_ANR_RE = re.compile(r"anr", re.IGNORECASE)

_ANDROID_RUNTIME_RE = re.compile(r"androidruntime", re.IGNORECASE)

# 行内控制字符（除 \t 外）会在匹配前清洗，避免日志被转义序列污染时误判。
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def analyze_logcat(
    lines: Iterable[str], package_name: Optional[str]
) -> dict:
    """分析 logcat 行流，返回统计 dict。

    返回字段：
      - total_lines: 输入行数（含空行）
      - fatal_exception_count: 匹配 "FATAL EXCEPTION" 的行数
      - anr_count: 包含 "ANR" 的行数（大小写不敏感）
      - androidruntime_crash_count: 包含 "AndroidRuntime" 的行数（仅诊断计数）
      - package_crash_count: 同时包含 "AndroidRuntime" 与 package_name 的行数（仅诊断计数）
      - has_blocking_issue: 仅由 FATAL EXCEPTION / ANR 触发

    普通 "AndroidRuntime" 行（例如每次 uiautomator dump 产生的启动日志）
    只计入诊断计数，不单独判定 blocking；package_crash_count 同理 ——
    uiautomator 自身的 AndroidRuntime 启动行可能携带设备上所有包名，不能
    作为崩溃证据。

    package_name 为 None 或空串时，package_crash_count 恒为 0，其余统计不受影响。
    """
    fatal_exception_count = 0
    anr_count = 0
    androidruntime_crash_count = 0
    package_crash_count = 0
    total_lines = 0

    pkg = package_name.strip() if isinstance(package_name, str) else ""
    pkg_lower = pkg.lower()

    for raw_line in lines:
        total_lines += 1
        if not isinstance(raw_line, str):
            continue
        line = _CONTROL_CHARS_RE.sub(" ", raw_line).lower()

        if _FATAL_EXCEPTION_RE.search(line):
            fatal_exception_count += 1
        if _ANR_RE.search(line):
            anr_count += 1
        if _ANDROID_RUNTIME_RE.search(line):
            androidruntime_crash_count += 1
            if pkg_lower and pkg_lower in line:
                package_crash_count += 1

    return {
        "total_lines": total_lines,
        "fatal_exception_count": fatal_exception_count,
        "anr_count": anr_count,
        "androidruntime_crash_count": androidruntime_crash_count,
        "package_crash_count": package_crash_count,
        "has_blocking_issue": bool(fatal_exception_count or anr_count),
    }
