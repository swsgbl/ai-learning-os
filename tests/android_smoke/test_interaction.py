"""Unit tests for tools.android_smoke.interaction — no real device required."""

import unittest

from tools.android_smoke.interaction import (
    AndroidUiController,
    UiElement,
    UiError,
    UiNotFoundError,
    UiTimeoutError,
    find,
    ime_shown_from_dumpsys,
    parse_ui_dump,
    swipe_up_args,
)

XML = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<hierarchy>'
    b'<node text="Settings" content-desc="" resource-id="android:id/title" '
    b'class="android.widget.TextView" bounds="[10,20][110,60]">'
    b'<node text="" content-desc="More options" resource-id="" '
    b'class="android.widget.ImageView" bounds="[0,0][50,50]"/>'
    b'</node>'
    b'<node text="Sign in" content-desc="login button" resource-id="com.app:id/login" '
    b'class="android.widget.Button" bounds="[100,200][300,260]"/>'
    b'</hierarchy>'
)


class FakeTap:
    def __init__(self):
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        return None


def make_controller(dump, tap):
    return AndroidUiController(ui_dump=lambda: dump, tap=tap)


class TestParseUiDump(unittest.TestCase):
    def test_parses_all_nodes_recursively(self):
        els = parse_ui_dump(XML)
        # The <hierarchy> root carries no bounds and is not an element.
        self.assertEqual(len(els), 3)
        self.assertIsInstance(els[0], UiElement)

    def test_center_is_bounds_midpoint(self):
        els = parse_ui_dump(XML)
        sign_in = find(els, text="Sign in")
        self.assertEqual(sign_in.center, (200, 230))
        self.assertEqual(sign_in.resource_id, "com.app:id/login")
        self.assertEqual(sign_in.klass, "android.widget.Button")
        self.assertEqual(sign_in.content_desc, "login button")

    def test_invalid_bounds_skipped_not_fatal(self):
        # r9 真机证据：退化 bounds 节点（零面积/倒置）不可见不可点，
        # 跳过即可；同 dump 的正常节点必须照常解析出来。
        dump = (
            b'<hierarchy>'
            b'<node text="bad" bounds="[10,20][5,60]"/>'
            b'<node text="ok" bounds="[0,0][50,50]"/>'
            b'</hierarchy>'
        )
        els = parse_ui_dump(dump)
        self.assertEqual([el.text for el in els], ["ok"])

    def test_zero_size_bounds_skipped(self):
        # EMUI 治理页 'pending：3' 实测携带 [0,0][0,0]（r9）：
        # 整个 dump 不能因此解析失败
        dump = (
            '<hierarchy>'
            '<node text="pending：3" bounds="[0,0][0,0]"/>'
            '<node text="审计留痕" bounds="[0,100][200,180]"/>'
            '</hierarchy>'
        ).encode("utf-8")
        els = parse_ui_dump(dump)
        self.assertEqual([el.text for el in els], ["审计留痕"])

    def test_malformed_bounds_skipped(self):
        dump = b'<hierarchy><node text="x" bounds="10,20,30,40"/></hierarchy>'
        self.assertEqual(parse_ui_dump(dump), [])

    def test_root_without_bounds_ok(self):
        els = parse_ui_dump(b'<hierarchy><node text="x" bounds="[0,0][10,10]"/></hierarchy>')
        self.assertEqual(len(els), 1)

    def test_node_without_bounds_skipped(self):
        dump = (
            b'<hierarchy>'
            b'<node text="gone"/>'
            b'<node text="present" bounds="[0,0][10,10]"/>'
            b'</hierarchy>'
        )
        els = parse_ui_dump(dump)
        self.assertEqual([el.text for el in els], ["present"])


class TestFind(unittest.TestCase):
    def setUp(self):
        self.els = parse_ui_dump(XML)

    def test_exact_text(self):
        el = find(self.els, text="Settings")
        self.assertEqual(el.text, "Settings")

    def test_contains(self):
        el = find(self.els, text="Sign", contains=True)
        self.assertEqual(el.text, "Sign in")

    def test_exact_does_not_match_substring(self):
        with self.assertRaises(UiNotFoundError):
            find(self.els, text="Sign")

    def test_content_desc(self):
        el = find(self.els, content_desc="More options")
        self.assertEqual(el.klass, "android.widget.ImageView")

    def test_not_found_raises(self):
        with self.assertRaises(UiNotFoundError):
            find(self.els, text="Missing")

    def test_requires_criteria(self):
        with self.assertRaises(ValueError):
            find(self.els)


DUPLICATE_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<hierarchy>'
    # 同一 exact 文案出现三次：页面标题 / 卡片执行按钮 / 底部导航
    b'<node text="OK" content-desc="" resource-id="title" '
    b'class="android.widget.TextView" bounds="[10,10][200,60]"/>'
    b'<node text="OK" content-desc="" resource-id="run" '
    b'class="android.widget.Button" bounds="[100,200][300,260]"/>'
    b'<node text="OK" content-desc="" resource-id="nav" '
    b'class="android.widget.Button" bounds="[10,900][100,960]"/>'
    b'</hierarchy>'
)


class TestOccurrenceSelection(unittest.TestCase):
    def test_find_selects_nth_match_in_document_order(self):
        els = parse_ui_dump(DUPLICATE_XML)
        self.assertEqual(find(els, text="OK").resource_id, "title")
        self.assertEqual(
            find(els, text="OK", occurrence=1).resource_id, "run"
        )
        self.assertEqual(
            find(els, text="OK", occurrence=2).resource_id, "nav"
        )

    def test_find_default_occurrence_is_first(self):
        els = parse_ui_dump(DUPLICATE_XML)
        first = find(els, text="OK")
        self.assertEqual(find(els, text="OK", occurrence=0).center, first.center)

    def test_find_occurrence_out_of_range_raises(self):
        els = parse_ui_dump(DUPLICATE_XML)
        with self.assertRaises(UiNotFoundError) as ctx:
            find(els, text="OK", occurrence=3)
        self.assertIn("only 3 match(es) found", str(ctx.exception))

    def test_find_occurrence_no_match_at_all(self):
        els = parse_ui_dump(DUPLICATE_XML)
        with self.assertRaises(UiNotFoundError):
            find(els, text="Missing", occurrence=0)

    def test_tap_text_occurrence_taps_nth_center(self):
        tap = FakeTap()
        ctl = make_controller(DUPLICATE_XML, tap)
        ctl.tap_text_occurrence("OK", 1)
        self.assertEqual(tap.calls, [["input", "tap", "200", "230"]])
        ctl.tap_text_occurrence("OK", 2)
        self.assertEqual(tap.calls[-1], ["input", "tap", "55", "930"])

    def test_tap_text_occurrence_out_of_range_raises(self):
        ctl = make_controller(DUPLICATE_XML, FakeTap())
        with self.assertRaises(UiNotFoundError):
            ctl.tap_text_occurrence("OK", 3)

    def test_tap_text_occurrence_contains_mode(self):
        tap = FakeTap()
        ctl = make_controller(DUPLICATE_XML, tap)
        ctl.tap_text_occurrence("O", 2, contains=True)
        self.assertEqual(tap.calls, [["input", "tap", "55", "930"]])

    def test_tap_text_still_taps_first_match(self):
        tap = FakeTap()
        ctl = make_controller(DUPLICATE_XML, tap)
        ctl.tap_text("OK")
        self.assertEqual(tap.calls, [["input", "tap", "105", "35"]])


class TestTap(unittest.TestCase):
    def test_tap_element_uses_center(self):
        tap = FakeTap()
        ctl = make_controller(XML, tap)
        ctl.tap_text("Sign in")
        self.assertEqual(tap.calls, [["input", "tap", "200", "230"]])

    def test_tap_arguments_are_strings(self):
        tap = FakeTap()
        ctl = make_controller(XML, tap)
        ctl.tap_text("Settings")
        for arg in tap.calls[0]:
            self.assertIsInstance(arg, str)

    def test_default_tap_bound_to_adb_run_shell(self):
        from tools.android_smoke.adb import AdbClient

        adb = AdbClient(runner=lambda args, timeout: None)
        shell_calls = []
        adb.run_shell = lambda args, check=True: shell_calls.append((list(args), check))
        ctl = AndroidUiController(adb=adb, ui_dump=lambda: XML)
        ctl.tap_text("Sign in")
        self.assertEqual(shell_calls, [(["input", "tap", "200", "230"], True)])


class TestWaitFor(unittest.TestCase):
    def test_success_on_first_poll(self):
        tap = FakeTap()
        ctl = make_controller(XML, tap)
        el = ctl.wait_for("Sign in", timeout_seconds=1)
        self.assertEqual(el.text, "Sign in")

    def test_success_after_retries(self):
        dumps = [XML.replace(b"Sign in", b"Loading"), XML]
        ctl = AndroidUiController(
            ui_dump=lambda: dumps.pop(0) if dumps else XML, tap=FakeTap()
        )
        el = ctl.wait_for("Sign in", timeout_seconds=2, interval_seconds=0.01)
        self.assertEqual(el.text, "Sign in")

    def test_timeout_raises_with_summary(self):
        ctl = make_controller(XML, FakeTap())
        with self.assertRaises(UiTimeoutError) as ctx:
            ctl.wait_for("Missing", timeout_seconds=0.05, interval_seconds=0.01)
        summary = ctx.exception.last_xml_summary
        self.assertIn("hierarchy", summary)
        # Summary must not leak attribute values such as text or ids.
        self.assertNotIn("Sign in", summary)
        self.assertNotIn("com.app:id/login", summary)


class TestTypeText(unittest.TestCase):
    def test_plain_ascii(self):
        tap = FakeTap()
        ctl = make_controller(XML, tap)
        ctl.type_text("hello123")
        self.assertEqual(tap.calls, [["input", "text", "hello123"]])

    def test_space_becomes_placeholder(self):
        tap = FakeTap()
        ctl = make_controller(XML, tap)
        ctl.type_text("hello world")
        self.assertEqual(tap.calls, [["input", "text", "hello%sworld"]])

    def test_percent_rejected(self):
        ctl = make_controller(XML, FakeTap())
        with self.assertRaises(ValueError):
            ctl.type_text("50%off")

    def test_percent_before_placeholder_rejected(self):
        # Raw '%' must be rejected even when it looks like the %s placeholder
        # that type_text itself would produce for spaces.
        ctl = make_controller(XML, FakeTap())
        with self.assertRaises(ValueError):
            ctl.type_text("50%s off")
        with self.assertRaises(ValueError):
            ctl.type_text("%s")

    def test_non_ascii_rejected(self):
        ctl = make_controller(XML, FakeTap())
        with self.assertRaises(ValueError):
            ctl.type_text("héllo")

    def test_control_character_rejected(self):
        ctl = make_controller(XML, FakeTap())
        with self.assertRaises(ValueError):
            ctl.type_text("line\nbreak")


class TestSwipe(unittest.TestCase):
    def test_swipe_sends_explicit_coordinates_and_duration(self):
        tap = FakeTap()
        ctl = make_controller(XML, tap)
        ctl.swipe(540, 1800, 540, 600, 400)
        self.assertEqual(
            tap.calls,
            [["input", "swipe", "540", "1800", "540", "600", "400"]],
        )

    def test_swipe_arguments_must_be_strings(self):
        tap = FakeTap()
        ctl = make_controller(XML, tap)
        ctl.swipe(0, 0, 0, 0, 0)
        for arg in tap.calls[0]:
            self.assertIsInstance(arg, str)

    def test_swipe_rejects_non_int_coordinates(self):
        ctl = make_controller(XML, FakeTap())
        for bad in ("540", 1.5, None):
            with self.assertRaises(ValueError):
                ctl.swipe(bad, 1800, 540, 600, 400)

    def test_swipe_rejects_negative_values(self):
        ctl = make_controller(XML, FakeTap())
        with self.assertRaises(ValueError):
            ctl.swipe(-1, 1800, 540, 600, 400)
        with self.assertRaises(ValueError):
            ctl.swipe(540, 1800, 540, 600, -400)

    def test_swipe_rejects_bool(self):
        ctl = make_controller(XML, FakeTap())
        with self.assertRaises(ValueError):
            ctl.swipe(True, 1800, 540, 600, 400)


class TestBack(unittest.TestCase):
    def test_back_sends_keyevent_4(self):
        tap = FakeTap()
        ctl = make_controller(XML, tap)
        ctl.back()
        self.assertEqual(tap.calls, [["input", "keyevent", "4"]])


class TestKeyEvents(unittest.TestCase):
    def test_single_and_multiple_codes_in_one_invocation(self):
        tap = FakeTap()
        ctl = make_controller(XML, tap)
        ctl.key_events(["123"])
        ctl.key_events(["123", "67", "67"])
        self.assertEqual(
            tap.calls,
            [
                ["input", "keyevent", "123"],
                ["input", "keyevent", "123", "67", "67"],
            ],
        )

    def test_rejects_empty(self):
        ctl = make_controller(XML, FakeTap())
        with self.assertRaises(ValueError):
            ctl.key_events([])

    def test_rejects_non_string_and_empty_string_codes(self):
        ctl = make_controller(XML, FakeTap())
        with self.assertRaises(TypeError):
            ctl.key_events(["123", 67])
        with self.assertRaises(TypeError):
            ctl.key_events(["123", ""])


class TestSwipeUpArgs(unittest.TestCase):
    def test_emulator_size_reproduces_proven_coordinates(self):
        # 1080x2400（模拟器实测）：默认百分比映射必须逐值复现
        # M12-06 手工验证过的滚动坐标——模拟器行为零变化。
        self.assertEqual(swipe_up_args(1080, 2400), (540, 1800, 540, 600, 400))

    def test_physical_720x1600_stays_in_screen(self):
        # r8 物理首跑证据：旧硬编码 y=1800 超出 1600 高度的屏幕；
        # 百分比换算后起止点都落在屏幕内。
        args = swipe_up_args(720, 1600)
        self.assertEqual(args, (360, 1200, 360, 400, 400))
        self.assertTrue(0 <= args[1] < 1600)
        self.assertTrue(0 <= args[3] < 1600)

    def test_custom_percent_and_duration(self):
        self.assertEqual(
            swipe_up_args(1000, 2000, 0.5, 0.8, 0.2, 300),
            (500, 1600, 500, 400, 300),
        )

    def test_invalid_dimensions_raise(self):
        for bad in (
            lambda: swipe_up_args(0, 1600),
            lambda: swipe_up_args(1080, 0),
            lambda: swipe_up_args(-1080, 2400),
            lambda: swipe_up_args("1080", 2400),
            lambda: swipe_up_args(True, 2400),
        ):
            with self.assertRaises(ValueError):
                bad()

    def test_invalid_percents_raise(self):
        for bad in (
            lambda: swipe_up_args(1080, 2400, 0.0, 0.75, 0.25),
            lambda: swipe_up_args(1080, 2400, 1.0, 0.75, 0.25),
            lambda: swipe_up_args(1080, 2400, 0.5, 0.0, 0.25),
            lambda: swipe_up_args(1080, 2400, 0.5, 1.0, 0.25),
            lambda: swipe_up_args(1080, 2400, 0.5, 0.75, 0.0),
            lambda: swipe_up_args(1080, 2400, 0.5, 0.75, 1.0),
            lambda: swipe_up_args(1080, 2400, 1, 0.75, 0.25),  # int 不是 float
        ):
            with self.assertRaises(ValueError):
                bad()

    def test_non_upward_direction_rejected(self):
        # from <= to 意味着不向上滚（甚至倒退），拒绝以免静默点错区域
        with self.assertRaises(ValueError):
            swipe_up_args(1080, 2400, 0.5, 0.25, 0.75)
        with self.assertRaises(ValueError):
            swipe_up_args(1080, 2400, 0.5, 0.5, 0.5)

    def test_invalid_duration_rejected(self):
        for bad in (-1, True, "400"):
            with self.assertRaises(ValueError):
                swipe_up_args(1080, 2400, 0.5, 0.75, 0.25, bad)


class TestImeShownFromDumpsys(unittest.TestCase):
    def test_true_flag(self):
        self.assertTrue(ime_shown_from_dumpsys("  mInputShown=true\n"))

    def test_false_flag(self):
        self.assertFalse(ime_shown_from_dumpsys("  mInputShown=false\n"))

    def test_input_view_variant(self):
        self.assertTrue(ime_shown_from_dumpsys("mInputViewShown=true"))
        self.assertFalse(ime_shown_from_dumpsys("mInputViewShown=false"))

    def test_any_true_wins(self):
        # IMMS 与 InputMethodService 两段输出都带标记时任一 true 即打开
        text = "mInputViewShown=false\n  mInputShown=true\n"
        self.assertTrue(ime_shown_from_dumpsys(text))

    def test_all_false_means_closed(self):
        text = "mInputViewShown=false\n  mInputShown=false\n"
        self.assertFalse(ime_shown_from_dumpsys(text))

    def test_no_flag_conservative_true(self):
        # 厂商/版本差异导致标记缺失时，保守视为打开（调用方会收键盘）
        self.assertTrue(ime_shown_from_dumpsys("Current Input Method Manager state:\n"))

    def test_non_string_conservative_true(self):
        self.assertTrue(ime_shown_from_dumpsys(None))
        self.assertTrue(ime_shown_from_dumpsys(b"mInputShown=false"))


if __name__ == "__main__":
    unittest.main()
