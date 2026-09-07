"""Minimal adb client for the Android smoke harness.

Standard library only. Commands are always executed as argument lists via
``subprocess.run`` (never ``shell=True``), so injected values such as package
names or remote paths cannot be interpreted as shell syntax.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence, Tuple


@dataclass(frozen=True)
class AdbCommandResult:
    """Outcome of one adb invocation."""

    args: tuple  # sanitized argument tuple, safe to log
    returncode: int
    stdout: bytes
    stderr: bytes
    timeout: bool = False
    duration_ms: int = 0


class AdbError(Exception):
    """Raised when an adb command fails or times out."""

    def __init__(self, message: str, result: Optional[AdbCommandResult] = None):
        super().__init__(message)
        self.result = result


# ``wm size`` 输出行："Physical size: 720x1600" / "Override size: 1080x2400"
_SCREEN_SIZE_RE = re.compile(
    r"^(Physical|Override) size:\s*(\d+)\s*x\s*(\d+)\s*$", re.MULTILINE
)


def parse_screen_size(text: str) -> Tuple[int, int]:
    """解析 ``wm size`` 输出为 ``(width, height)``；Override 优先于 Physical。

    设有显示尺寸覆盖（``wm size WxH``）时 adb 仍会打印 Physical size，
    但设备实际渲染按 Override——滚动坐标必须按实际渲染尺寸换算。
    无任何可解析行（如设备脱机时的错误输出）抛 :class:`AdbError`。
    """
    if not isinstance(text, str):
        raise AdbError(
            "wm size output must be a string, got %s" % type(text).__name__
        )
    override: Optional[Tuple[int, int]] = None
    physical: Optional[Tuple[int, int]] = None
    for kind, width, height in _SCREEN_SIZE_RE.findall(text):
        size = (int(width), int(height))
        if size[0] <= 0 or size[1] <= 0:
            continue
        if kind == "Override":
            override = size
        else:
            physical = size
    chosen = override if override is not None else physical
    if chosen is None:
        raise AdbError("cannot parse wm size output: %r" % (text[:200],))
    return chosen


def _sanitize(args: Sequence[str]) -> tuple:
    """Return a log-safe copy of the argument tuple (no secrets expected)."""
    return tuple(args)


def _default_runner(args: Sequence[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args),
        capture_output=True,
        timeout=timeout,
        check=False,
    )


@dataclass
class AdbClient:
    """Thin, testable wrapper around the adb binary."""

    adb_path: str = "adb"
    serial: str = ""
    timeout_seconds: int = 30
    # runner(args, timeout) -> object with returncode/stdout/stderr
    runner: Callable[[Sequence[str], int], object] = field(default=_default_runner, repr=False)

    def run(self, args: Sequence[str], check: bool = True) -> AdbCommandResult:
        argv = [self.adb_path]
        if self.serial:
            argv += ["-s", self.serial]
        argv += [str(a) for a in args]
        sanitized = _sanitize(argv)
        start = time.monotonic()
        timed_out = False
        stdout = b""
        stderr = b""
        returncode = -1
        try:
            proc = self.runner(argv, self.timeout_seconds)
            returncode = proc.returncode
            stdout = proc.stdout or b""
            stderr = proc.stderr or b""
        except subprocess.TimeoutExpired:
            timed_out = True
            stderr = b"adb command timed out"
        duration_ms = int((time.monotonic() - start) * 1000)
        result = AdbCommandResult(
            args=sanitized,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            timeout=timed_out,
        )
        if check and (timed_out or returncode != 0):
            raise AdbError(
                "adb failed (returncode=%d, timeout=%s): %s"
                % (returncode, timed_out, sanitized),
                result=result,
            )
        return result

    def run_shell(self, args: Sequence[str], check: bool = True) -> AdbCommandResult:
        """Run ``adb shell`` with an explicit argument list only."""
        if not isinstance(args, (list, tuple)):
            raise TypeError("run_shell expects a list of strings")
        parts = list(args)
        if not parts:
            raise ValueError("run_shell requires at least one argument")
        for a in parts:
            if not isinstance(a, str):
                raise TypeError("run_shell arguments must be strings")
        return self.run(["shell"] + parts, check=check)

    # ---- device operations -------------------------------------------

    def wait_device(self) -> AdbCommandResult:
        return self.run(["wait-for-device"])

    def install_apk(self, apk: str) -> AdbCommandResult:
        return self.run(["install", "-r", "-t", apk])

    def clear_app(self, package: str) -> AdbCommandResult:
        return self.run(["shell", "pm", "clear", package])

    def start_activity(self, package: str, activity: str) -> AdbCommandResult:
        return self.run(["shell", "am", "start", "-W", "-n", "%s/%s" % (package, activity)])

    def reverse_tcp(self, port: int) -> AdbCommandResult:
        """Forward device loopback ``tcp:<port>`` to host loopback (physical runs).

        ``--no-rebind`` fails instead of silently rebinding when a mapping
        already exists, so a stale listener from an earlier run surfaces
        immediately instead of being masked.
        """
        return self.run(
            ["reverse", "--no-rebind", "tcp:%d" % port, "tcp:%d" % port]
        )

    def reverse_remove_tcp(self, port: int, check: bool = True) -> AdbCommandResult:
        """Remove the reverse mapping for ``tcp:<port>`` (cleanup path)."""
        return self.run(["reverse", "--remove", "tcp:%d" % port], check=check)

    def clear_logcat(self) -> AdbCommandResult:
        return self.run(["logcat", "-c"])

    def screen_size(self) -> Tuple[int, int]:
        """实测屏幕渲染尺寸 ``(width, height)``，供滚动坐标按屏换算。

        走 ``adb shell wm size``（参数列表形式），Override 优先于 Physical
        （见 :func:`parse_screen_size`）。
        """
        result = self.run(["shell", "wm", "size"])
        return parse_screen_size(result.stdout.decode("utf-8", errors="replace"))

    def dumpsys_input_method(self) -> bytes:
        """``dumpsys input_method`` 原始输出，供判断软键盘是否打开。"""
        return self.run(["shell", "dumpsys", "input_method"]).stdout

    def dump_logcat(self) -> bytes:
        return self.run(["logcat", "-d"]).stdout

    def capture_screenshot(self) -> bytes:
        result = self.run(["exec-out", "screencap", "-p"])
        return result.stdout

    def dump_ui(self, remote_path: str = "/sdcard/aios_window_dump.xml") -> bytes:
        """Dump the window hierarchy via uiautomator.

        Dumping to /dev/tty only prints a status line on real devices, so we
        dump to a remote file, ``cat`` it back, and always clean it up.
        """
        self.run_shell(["uiautomator", "dump", remote_path])
        try:
            data = self.run_shell(["cat", remote_path]).stdout
            if b"<hierarchy" not in data:
                raise AdbError(
                    "uiautomator dump did not return hierarchy XML: %r" % (data[:200],)
                )
            return data
        finally:
            self.run_shell(["rm", "-f", remote_path], check=False)

    def remove_remote(self, path: str) -> AdbCommandResult:
        return self.run(["shell", "rm", "-f", path])
