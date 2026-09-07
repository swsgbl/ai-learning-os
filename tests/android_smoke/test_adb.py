"""Unit tests for tools.android_smoke.adb — no real device required."""

import inspect
import subprocess
import unittest

from tools.android_smoke.adb import AdbClient, AdbCommandResult, AdbError


class FakeProc:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRunner:
    """Records invocations; can be scripted to fail or time out."""

    def __init__(self, proc=None, timeout=False):
        self.proc = proc or FakeProc()
        self.timeout = timeout
        self.calls = []

    def __call__(self, args, timeout):
        self.calls.append((list(args), timeout))
        if self.timeout:
            raise subprocess.TimeoutExpired(cmd=args, timeout=timeout)
        return self.proc


class SequentialFakeRunner:
    """Returns one scripted result per call, in order; records all calls."""

    def __init__(self, *procs):
        self.procs = list(procs)
        self.calls = []

    def __call__(self, args, timeout):
        self.calls.append((list(args), timeout))
        return self.procs.pop(0)


def make_client(runner, serial="XYZ123", timeout_seconds=30):
    return AdbClient(
        adb_path="adb",
        serial=serial,
        timeout_seconds=timeout_seconds,
        runner=runner,
    )


class TestRun(unittest.TestCase):
    def test_prefixes_adb_and_serial(self):
        runner = FakeRunner()
        client = make_client(runner)
        client.run(["devices"])
        self.assertEqual(runner.calls, [(["adb", "-s", "XYZ123", "devices"], 30)])

    def test_no_serial_when_empty(self):
        runner = FakeRunner()
        client = AdbClient(runner=runner, serial="")
        client.run(["devices"])
        self.assertEqual(runner.calls, [(["adb", "devices"], 30)])

    def test_custom_timeout_forwarded(self):
        runner = FakeRunner()
        client = make_client(runner, timeout_seconds=5)
        client.run(["devices"])
        self.assertEqual(runner.calls[0][1], 5)

    def test_result_fields(self):
        runner = FakeRunner(FakeProc(0, b"out", b"err"))
        result = make_client(runner).run(["devices"])
        self.assertIsInstance(result, AdbCommandResult)
        self.assertEqual(result.args, ("adb", "-s", "XYZ123", "devices"))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"out")
        self.assertEqual(result.stderr, b"err")
        self.assertFalse(result.timeout)
        self.assertIsInstance(result.duration_ms, int)

    def test_check_error_on_nonzero(self):
        runner = FakeRunner(FakeProc(1, b"", b"failure"))
        client = make_client(runner)
        with self.assertRaises(AdbError) as ctx:
            client.run(["install", "app.apk"])
        self.assertIn("returncode=1", str(ctx.exception))
        self.assertEqual(ctx.exception.result.returncode, 1)

    def test_check_false_returns_result(self):
        runner = FakeRunner(FakeProc(1))
        result = make_client(runner).run(["devices"], check=False)
        self.assertEqual(result.returncode, 1)

    def test_timeout_raises_and_flags(self):
        runner = FakeRunner(timeout=True)
        client = make_client(runner)
        with self.assertRaises(AdbError) as ctx:
            client.run(["devices"])
        self.assertTrue(ctx.exception.result.timeout)

    def test_default_runner_never_shell(self):
        # Sanity: default runner is subprocess.run with list args.
        from tools.android_smoke import adb as adb_mod

        src = inspect.getsource(adb_mod._default_runner)
        self.assertIn("subprocess.run", src)
        self.assertNotIn("shell=True", src)


class TestRunShell(unittest.TestCase):
    def test_shell_list_args(self):
        runner = FakeRunner()
        client = make_client(runner)
        client.run_shell(["pm", "clear", "com.example"])
        self.assertEqual(
            runner.calls,
            [(["adb", "-s", "XYZ123", "shell", "pm", "clear", "com.example"], 30)],
        )

    def test_rejects_string(self):
        client = make_client(FakeRunner())
        with self.assertRaises(TypeError):
            client.run_shell("pm clear com.example")

    def test_rejects_empty(self):
        client = make_client(FakeRunner())
        with self.assertRaises(ValueError):
            client.run_shell([])

    def test_rejects_non_string_items(self):
        client = make_client(FakeRunner())
        with self.assertRaises(TypeError):
            client.run_shell(["pm", 123])


class TestDeviceOperations(unittest.TestCase):
    def test_wait_device(self):
        runner = FakeRunner()
        make_client(runner).wait_device()
        self.assertEqual(runner.calls[0][0], ["adb", "-s", "XYZ123", "wait-for-device"])

    def test_install_apk(self):
        runner = FakeRunner()
        make_client(runner).install_apk(r"D:\builds\app.apk")
        self.assertEqual(
            runner.calls[0][0],
            ["adb", "-s", "XYZ123", "install", "-r", "-t", r"D:\builds\app.apk"],
        )

    def test_clear_app(self):
        runner = FakeRunner()
        make_client(runner).clear_app("com.example.app")
        self.assertEqual(
            runner.calls[0][0],
            ["adb", "-s", "XYZ123", "shell", "pm", "clear", "com.example.app"],
        )

    def test_start_activity(self):
        runner = FakeRunner()
        make_client(runner).start_activity("com.example.app", ".MainActivity")
        self.assertEqual(
            runner.calls[0][0],
            [
                "adb", "-s", "XYZ123", "shell", "am", "start", "-W", "-n",
                "com.example.app/.MainActivity",
            ],
        )

    def test_clear_logcat(self):
        runner = FakeRunner()
        make_client(runner).clear_logcat()
        self.assertEqual(runner.calls[0][0], ["adb", "-s", "XYZ123", "logcat", "-c"])

    def test_dump_logcat(self):
        runner = FakeRunner(FakeProc(0, b"log line\n"))
        data = make_client(runner).dump_logcat()
        self.assertEqual(data, b"log line\n")
        self.assertEqual(runner.calls[0][0], ["adb", "-s", "XYZ123", "logcat", "-d"])

    def test_capture_screenshot(self):
        runner = FakeRunner(FakeProc(0, b"\x89PNG..."))
        data = make_client(runner).capture_screenshot()
        self.assertEqual(data, b"\x89PNG...")
        self.assertEqual(
            runner.calls[0][0],
            ["adb", "-s", "XYZ123", "exec-out", "screencap", "-p"],
        )

    def test_dump_ui_three_steps_with_cleanup(self):
        runner = SequentialFakeRunner(
            FakeProc(0, b"UI hierchary dumped to: /sdcard/aios_window_dump.xml\n"),
            FakeProc(0, b'<?xml version="1.0"?><hierarchy rotation="0"/>'),
            FakeProc(0, b""),
        )
        data = make_client(runner).dump_ui()
        self.assertEqual(data, b'<?xml version="1.0"?><hierarchy rotation="0"/>')
        self.assertEqual(
            [c[0] for c in runner.calls],
            [
                ["adb", "-s", "XYZ123", "shell", "uiautomator", "dump",
                 "/sdcard/aios_window_dump.xml"],
                ["adb", "-s", "XYZ123", "shell", "cat", "/sdcard/aios_window_dump.xml"],
                ["adb", "-s", "XYZ123", "shell", "rm", "-f",
                 "/sdcard/aios_window_dump.xml"],
            ],
        )

    def test_dump_ui_custom_remote_path(self):
        runner = SequentialFakeRunner(
            FakeProc(0, b""),
            FakeProc(0, b"<hierarchy/>"),
            FakeProc(0, b""),
        )
        make_client(runner).dump_ui(remote_path="/sdcard/custom.xml")
        self.assertEqual(runner.calls[0][0][-1], "/sdcard/custom.xml")
        self.assertEqual(runner.calls[2][0][-1], "/sdcard/custom.xml")

    def test_dump_ui_cleanup_runs_even_when_cleanup_fails(self):
        runner = SequentialFakeRunner(
            FakeProc(0, b""),
            FakeProc(0, b"<hierarchy/>"),
            FakeProc(1, b"", b"rm failed"),  # rm fails but check=False
        )
        data = make_client(runner).dump_ui()
        self.assertEqual(data, b"<hierarchy/>")
        self.assertEqual(len(runner.calls), 3)

    def test_dump_ui_rejects_non_xml_output(self):
        runner = SequentialFakeRunner(
            FakeProc(0, b"UI hierchary dumped to: /dev/tty\n"),
            FakeProc(0, b"UI hierchary dumped to: /dev/tty\n"),  # cat got no XML
            FakeProc(0, b""),
        )
        with self.assertRaises(AdbError) as ctx:
            make_client(runner).dump_ui()
        self.assertIn("hierarchy XML", str(ctx.exception))
        # cleanup still ran
        self.assertEqual(
            runner.calls[2][0],
            ["adb", "-s", "XYZ123", "shell", "rm", "-f", "/sdcard/aios_window_dump.xml"],
        )

    def test_dump_ui_cleanup_runs_when_cat_fails(self):
        runner = SequentialFakeRunner(
            FakeProc(0, b""),
            FakeProc(1, b"", b"cat: no such file"),
            FakeProc(0, b""),
        )
        with self.assertRaises(AdbError):
            make_client(runner).dump_ui()
        self.assertEqual(len(runner.calls), 3)

    def test_remove_remote(self):
        runner = FakeRunner()
        make_client(runner).remove_remote("/sdcard/tmp.png")
        self.assertEqual(
            runner.calls[0][0],
            ["adb", "-s", "XYZ123", "shell", "rm", "-f", "/sdcard/tmp.png"],
        )


if __name__ == "__main__":
    unittest.main()
