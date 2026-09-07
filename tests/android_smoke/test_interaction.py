"""Unit tests for tools.android_smoke.interaction — no real device required."""

import unittest

from tools.android_smoke.interaction import (
    AndroidUiController,
    UiElement,
    UiError,
    UiNotFoundError,
    UiTimeoutError,
    find,
    parse_ui_dump,
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

    def test_invalid_bounds_rejected(self):
        bad = (
            b'<hierarchy><node text="x" bounds="[10,20][5,60]"/></hierarchy>'
        )
        with self.assertRaises(UiError):
            parse_ui_dump(bad)

    def test_malformed_bounds_rejected(self):
        bad = b'<hierarchy><node text="x" bounds="10,20,30,40"/></hierarchy>'
        with self.assertRaises(UiError):
            parse_ui_dump(bad)

    def test_root_without_bounds_ok(self):
        els = parse_ui_dump(b'<hierarchy><node text="x" bounds="[0,0][10,10]"/></hierarchy>')
        self.assertEqual(len(els), 1)

    def test_node_without_bounds_rejected(self):
        bad = b'<hierarchy><node text="x"/></hierarchy>'
        with self.assertRaises(UiError):
            parse_ui_dump(bad)


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


if __name__ == "__main__":
    unittest.main()
