"""Tests for tools.harmony_release.foreground_guard (M14-170 R2a).

Pure, deterministic contract tests: every fixture is synthetic text
built from the row shapes observed on the shared emulator (M14-89).
No process is spawned, no device is contacted, no file is read, no
environment variable or clock is touched. Identical inputs must give
byte-identical JSON.
"""

from __future__ import annotations

import json

import pytest

from tools.harmony_release.foreground_guard import (
    CLOSED_REASONS,
    EXIT_FAILURE,
    EXIT_OK,
    FULLSCREEN_MIN_WIDTH,
    REASON_APP_WINDOW_MISSING,
    REASON_BUNDLE_MAP_MISSING,
    REASON_FOREIGN_FOREGROUND,
    REASON_FOCUS_WINDOW_UNREADABLE,
    REASON_HIDUMPER_UNREADABLE,
    STATUS_FAILURE,
    STATUS_OK,
    SYSTEM_WINDOW_PREFIXES,
    TOOL_NAME,
    WindowRow,
    evaluate_foreground,
    filter_app_windows,
    filter_fullscreen,
    is_system_surface,
    parse_bundle_map,
    parse_focus_window,
    parse_window_rows,
    render_json,
    select_top_window,
)

BUNDLE = "com.ailearningos.app"

# Row shape observed on emulator API 24:
# name displayId pid winId type mode flag zord orientation [ x y w h ]
SCB_SURFACE = (
    "SCBStatusBar        0     871     44    1   0   0    11     0"
    "    [0   0   1268  176]"
)
IME_SURFACE = (
    "softKeyboard0       0     902     45    2   0   0     4     0"
    "    [0   900  1268  368]"
)
OUR_FULL = (
    "app0                0     1234     10    1   0   0    15     0"
    "    [0   0   1268  2400]"
)
FOREIGN_FULL = (
    "poc0                0     5678     20    1   0   0    16     0"
    "    [0   0   1268  2400]"
)
# Same window name family as ours, different pid/bundle underneath.
LOOKALIKE_FULL = (
    "app0                0     9999     30    1   0   0    17     0"
    "    [0   0   1268  2400]"
)
SPLIT_HALF = (
    "app1                0     2345     11    1   0   0    12     0"
    "    [0   0   634   2400]"
)
NEGATIVE_ZORD = (
    "app2                0     3456     12    1   0   0    -3     0"
    "    [0   0   1268  100]"
)
HEADER_LINES = (
    "WindowName           DisplayId   Pid   WinId    Type   Mode   Flag"
    "   Zord   Orientation    [x y w h]"
    "\n------------------------------"
)

PS_HEADER = (
    "UID          PID  PPID  C   STIME  TTY    TIME     CMD"
)
PS_OURS = (
    "u0_a321      1234  560  12  09:00:12  ?  00:00:03  " + BUNDLE
)
PS_FOREIGN = (
    "u0_a777      5678  561  5   09:05:33  ?  00:00:01  net.uniterm.poc"
)
PS_LOOKALIKE = (
    "u0_a111      9999  562  9   09:06:44  ?  00:00:02  "
    "net.other.app0ish"
)


def window_table(*rows: str, focus: int = 10) -> str:
    """Synthesize a WindowManagerService dump from row lines."""
    lines = [HEADER_LINES, ""]
    lines.extend(rows)
    lines.append("")
    lines.append(f"Focus window: {focus}")
    return "\n".join(lines)


def ps_table(*rows: str) -> str:
    return "\n".join([PS_HEADER, *rows]) + "\n"


def ok_table() -> str:
    return window_table(SCB_SURFACE, IME_SURFACE, SPLIT_HALF, OUR_FULL)


def ok_ps() -> str:
    return ps_table(PS_OURS)


# ---------------------------------------------------------- parsing ----

class TestParseWindowRows:
    def test_parses_app_row_facts(self):
        rows = parse_window_rows(ok_table())
        ours = [r for r in rows if r.pid == 1234][0]
        assert ours.name == "app0"
        assert ours.win_id == 10
        assert ours.zord == 15
        assert ours.w == 1268

    def test_negative_zord_is_kept(self):
        rows = parse_window_rows(window_table(NEGATIVE_ZORD))
        assert [r.zord for r in rows] == [-3]

    def test_headers_and_prose_are_ignored(self):
        rows = parse_window_rows(
            HEADER_LINES + "\nsome prose line without numbers\n")
        assert rows == []

    def test_crlf_text_parses(self):
        rows = parse_window_rows(ok_table().replace("\n", "\r\n"))
        assert len(rows) == 4

    def test_empty_and_none_safe(self):
        assert parse_window_rows("") == []
        assert parse_window_rows(None) == []  # type: ignore[arg-type]

    def test_deterministic_order(self):
        once = [r.to_record() for r in parse_window_rows(ok_table())]
        twice = [r.to_record() for r in parse_window_rows(ok_table())]
        assert once == twice


class TestParseFocusWindow:
    def test_reads_focus_id(self):
        assert parse_focus_window(ok_table()) == 10

    def test_absent_line_is_none(self):
        assert parse_focus_window("no focus line here") is None

    def test_empty_and_none_safe(self):
        assert parse_focus_window("") is None
        assert parse_focus_window(None) is None  # type: ignore[arg-type]


class TestSystemSurfaceFiltering:
    @pytest.mark.parametrize("name", [
        "SCBStatusBar", "SCBKeyboardPanel", "SCBLauncher", "softKeyboard0",
    ])
    def test_system_prefixes_filtered(self, name):
        assert is_system_surface(name) is True

    def test_app_names_kept(self):
        for name in ("app0", "poc0", "SCBapp", "keyboard0"):
            assert is_system_surface(name) is (
                name.startswith(SYSTEM_WINDOW_PREFIXES)
            )

    def test_filter_app_windows_drops_system_rows(self):
        rows = parse_window_rows(ok_table())
        names = [r.name for r in filter_app_windows(rows)]
        assert names == ["app1", "app0"]


class TestFullscreenFilter:
    def test_boundary_width_included(self):
        row = WindowRow(name="app0", pid=1, win_id=2, zord=3,
                        w=FULLSCREEN_MIN_WIDTH)
        assert filter_fullscreen([row]) == [row]

    def test_below_boundary_excluded(self):
        row = WindowRow(name="app1", pid=1, win_id=2, zord=3,
                        w=FULLSCREEN_MIN_WIDTH - 1)
        assert filter_fullscreen([row]) == []

    def test_split_window_not_fullscreen(self):
        rows = parse_window_rows(window_table(SPLIT_HALF))
        assert filter_fullscreen(filter_app_windows(rows)) == []


class TestSelectTopWindow:
    def test_max_zord_wins(self):
        rows = parse_window_rows(ok_table())
        top = select_top_window(filter_fullscreen(filter_app_windows(rows)))
        assert top is not None and top.pid == 1234

    def test_negative_zord_only_still_selects(self):
        rows = parse_window_rows(window_table(NEGATIVE_ZORD))
        top = select_top_window(filter_app_windows(rows))
        assert top is not None and top.zord == -3

    def test_tie_first_table_row_wins(self):
        first = WindowRow(name="a0", pid=1, win_id=2, zord=7, w=1200)
        second = WindowRow(name="a1", pid=2, win_id=3, zord=7, w=1200)
        assert select_top_window([first, second]) is first

    def test_empty_is_none(self):
        assert select_top_window([]) is None


class TestParseBundleMap:
    def test_maps_pid_to_bundle_tail(self):
        mapping = parse_bundle_map(ok_ps())
        assert mapping[1234] == BUNDLE

    def test_header_row_skipped(self):
        mapping = parse_bundle_map(ps_table())
        assert mapping == {}

    def test_short_rows_skipped(self):
        mapping = parse_bundle_map("u0_a1 12\njust prose\n")
        assert mapping == {}

    def test_duplicate_pid_first_wins(self):
        text = ps_table(
            "u0_a1  100  1  1  09:00  ?  00:00:01  com.first.app",
            "u0_a2  100  1  1  09:00  ?  00:00:01  com.second.app",
        )
        assert parse_bundle_map(text)[100] == "com.first.app"

    def test_bundle_tail_keeps_spaces(self):
        text = ("root  100  1  1  09:00  ?  00:00:01  "
                "com.x.y --flag value")
        assert parse_bundle_map(text)[100] == "com.x.y --flag value"

    def test_empty_and_none_safe(self):
        assert parse_bundle_map("") == {}
        assert parse_bundle_map(None) == {}  # type: ignore[arg-type]


# -------------------------------------------------------- verdicts ----

class TestEvaluateForegroundOk:
    def test_our_bundle_on_top_is_ok(self):
        result, code = evaluate_foreground(
            BUNDLE, ok_table(), ok_ps())
        assert code == EXIT_OK
        assert result["status"] == STATUS_OK
        assert result["reasons"] == []
        assert result["primary_reason"] is None

    def test_facts_recorded(self):
        result, _ = evaluate_foreground(BUNDLE, ok_table(), ok_ps())
        facts = result["facts"]
        assert facts["window_rows_total"] == 4
        assert facts["system_window_rows"] == 2
        assert facts["app_window_rows"] == 2
        assert facts["fullscreen_rows"] == 1
        assert facts["focus_window_present"] is True
        assert facts["top_zord"] == 15
        assert facts["top_w"] == 1268

    def test_system_surface_on_top_is_still_ok(self):
        # SCB surfaces may legally hold the highest zord while our app
        # stays the top APP window - filtering happens before selection.
        table = window_table(OUR_FULL, SCB_SURFACE)
        result, code = evaluate_foreground(BUNDLE, table, ok_ps())
        assert code == EXIT_OK
        assert result["facts"]["top_zord"] == 15

    def test_exact_bundle_proof_requires_equality(self):
        result, code = evaluate_foreground(
            BUNDLE, ok_table(),
            ps_table("u0_a1  1234  1  1  09:00  ?  00:00:01  " + BUNDLE
                     + ".evil"))
        assert code == EXIT_FAILURE
        assert result["reasons"] == [REASON_FOREIGN_FOREGROUND]


class TestEvaluateForegroundClosedReasons:
    def test_hidumper_nonzero_exit(self):
        result, code = evaluate_foreground(
            BUNDLE, ok_table(), ok_ps(), hidumper_returncode=1)
        assert code == EXIT_FAILURE
        assert result["reasons"] == [REASON_HIDUMPER_UNREADABLE]
        assert result["facts"]["top_zord"] is None

    def test_hidumper_empty_output(self):
        result, code = evaluate_foreground(BUNDLE, "", ok_ps())
        assert code == EXIT_FAILURE
        assert result["reasons"] == [REASON_HIDUMPER_UNREADABLE]

    def test_hidumper_whitespace_output(self):
        result, code = evaluate_foreground(BUNDLE, "  \n \t", ok_ps())
        assert code == EXIT_FAILURE
        assert result["reasons"] == [REASON_HIDUMPER_UNREADABLE]

    def test_focus_line_missing(self):
        table = "\n".join([HEADER_LINES, OUR_FULL])
        result, code = evaluate_foreground(BUNDLE, table, ok_ps())
        assert code == EXIT_FAILURE
        assert result["reasons"] == [REASON_FOCUS_WINDOW_UNREADABLE]

    def test_zero_window_rows(self):
        table = "Focus window: 10\n" + HEADER_LINES
        result, code = evaluate_foreground(BUNDLE, table, ok_ps())
        assert code == EXIT_FAILURE
        assert result["reasons"] == [REASON_FOCUS_WINDOW_UNREADABLE]

    def test_app_window_missing_system_only(self):
        table = window_table(SCB_SURFACE, IME_SURFACE)
        result, code = evaluate_foreground(BUNDLE, table, ok_ps())
        assert code == EXIT_FAILURE
        assert result["reasons"] == [REASON_APP_WINDOW_MISSING]
        assert result["facts"]["app_window_rows"] == 0

    def test_app_window_missing_no_fullscreen(self):
        # Only a split-width app window: not full-screen, so the guard
        # must fail closed rather than trust a half-screen row.
        table = window_table(SPLIT_HALF)
        result, code = evaluate_foreground(BUNDLE, table, ok_ps())
        assert code == EXIT_FAILURE
        assert result["reasons"] == [REASON_APP_WINDOW_MISSING]
        assert result["facts"]["fullscreen_rows"] == 0

    def test_bundle_map_missing(self):
        # ps -ef knows nothing about the top window's PID.
        result, code = evaluate_foreground(
            BUNDLE, ok_table(), ps_table(PS_FOREIGN))
        assert code == EXIT_FAILURE
        assert result["reasons"] == [REASON_BUNDLE_MAP_MISSING]
        assert result["facts"]["top_zord"] == 15

    def test_foreign_foreground(self):
        table = window_table(OUR_FULL, FOREIGN_FULL)
        result, code = evaluate_foreground(
            BUNDLE, table, ps_table(PS_OURS, PS_FOREIGN))
        assert code == EXIT_FAILURE
        assert result["reasons"] == [REASON_FOREIGN_FOREGROUND]
        assert result["facts"]["top_zord"] == 16

    def test_lookalike_window_name_fails_exact_proof(self):
        # Window name app0 with a DIFFERENT pid/bundle at the top must
        # not pass just because the row name looks like ours.
        table = window_table(OUR_FULL, LOOKALIKE_FULL)
        result, code = evaluate_foreground(
            BUNDLE, table, ps_table(PS_OURS, PS_LOOKALIKE))
        assert code == EXIT_FAILURE
        assert result["reasons"] == [REASON_FOREIGN_FOREGROUND]
        assert result["facts"]["top_zord"] == 17

    def test_exactly_one_reason_per_failure(self):
        for table, ps in (
            ("", ok_ps()),
            (window_table(SCB_SURFACE), ok_ps()),
            (ok_table(), ps_table(PS_FOREIGN)),
        ):
            result, _ = evaluate_foreground(BUNDLE, table, ps)
            assert len(result["reasons"]) == 1

    def test_all_five_reasons_are_covered(self):
        seen = set()
        cases = [
            ("", ok_ps(), 0, REASON_HIDUMPER_UNREADABLE),
            ("Focus window: 3\nprose", ok_ps(), 0,
             REASON_FOCUS_WINDOW_UNREADABLE),
            (window_table(SCB_SURFACE), ok_ps(), 0,
             REASON_APP_WINDOW_MISSING),
            (ok_table(), ps_table(PS_FOREIGN), 0,
             REASON_BUNDLE_MAP_MISSING),
            (window_table(OUR_FULL, FOREIGN_FULL),
             ps_table(PS_OURS, PS_FOREIGN), 0, REASON_FOREIGN_FOREGROUND),
        ]
        for table, ps, rc, expected in cases:
            result, code = evaluate_foreground(BUNDLE, table, ps, rc)
            assert code == EXIT_FAILURE
            assert result["reasons"] == [expected]
            seen.add(expected)
        assert seen == set(CLOSED_REASONS)


class TestEvaluateForegroundContract:
    def test_requested_bundle_required(self):
        with pytest.raises(ValueError):
            evaluate_foreground("", ok_table(), ok_ps())

    def test_non_string_bundle_rejected(self):
        with pytest.raises(ValueError):
            evaluate_foreground(None, ok_table(), ok_ps())  # type: ignore

    def test_result_envelope(self):
        result, code = evaluate_foreground(BUNDLE, ok_table(), ok_ps())
        assert code == EXIT_OK
        assert result["schema_version"] == 1
        assert result["tool"] == TOOL_NAME
        assert "requested_bundle" not in result
        assert result["boundaries"] == {
            "pure_parser": True,
            "io_performed": False,
            "process_spawned": False,
            "device_contacted": False,
            "recovery_attempted": False,
            "backend_integration": False,
        }

    def test_deterministic_byte_identical_json(self):
        first = render_json(
            evaluate_foreground(BUNDLE, ok_table(), ok_ps())[0])
        second = render_json(
            evaluate_foreground(BUNDLE, ok_table(), ok_ps())[0])
        assert first == second
        assert json.loads(first)["status"] == STATUS_OK

    def test_failure_result_serializes(self):
        result, code = evaluate_foreground(
            BUNDLE, ok_table(), ps_table(PS_FOREIGN))
        assert code == EXIT_FAILURE
        assert json.loads(render_json(result))["status"] == STATUS_FAILURE
# ---------------------------------------- no raw identifiers --

class TestNoRawIdentifiersInDetails:
    """M14-170 R2a safety contract: the returned details carry only
    closed-set reasons, counts and geometry - never a raw window name,
    pid, window id, or a device-side bundle string, whatever the verdict
    and whichever gate fires."""

    @pytest.mark.parametrize("ps_rows", [
        (PS_OURS,),                       # ok verdict
        (PS_FOREIGN,),                    # bundle_map_missing
        (PS_OURS, PS_FOREIGN),            # foreign_foreground
    ], ids=["ok", "no_map", "foreign"])
    def test_serialized_verdict_never_leaks_identifiers(self, ps_rows):
        table = window_table(SCB_SURFACE, IME_SURFACE, OUR_FULL,
                             FOREIGN_FULL, LOOKALIKE_FULL)
        result, code = evaluate_foreground(BUNDLE, table, ps_table(*ps_rows))
        assert code in (EXIT_OK, EXIT_FAILURE)
        blob = render_json(result)
        for leak in (
            BUNDLE,                # requested bundle name
            "net.uniterm.poc",     # foreign bundle at top
            "net.other.app0ish",   # lookalike bundle
            "app0", "poc0",        # window names
            "SCBStatusBar", "softKeyboard0",   # system surface names
            "1234", "5678", "9999",            # pids
            "871", "902",                       # system surface pids
        ):
            assert leak not in blob, leak

    def test_failed_gates_never_leak_identifiers_either(self):
        cases = [
            ("", ok_ps()),                                  # hidumper
            ("Focus window: 3\nprose", ok_ps()),            # no rows
            (window_table(SCB_SURFACE), ok_ps()),           # no app row
            (window_table(SPLIT_HALF), ok_ps()),            # not fullscreen
        ]
        for table, ps in cases:
            result, _ = evaluate_foreground(BUNDLE, table, ps)
            blob = render_json(result)
            assert "app0" not in blob
            assert "1234" not in blob
            assert "2345" not in blob
            assert BUNDLE not in blob

    def test_focus_id_is_never_recorded(self):
        # The focus window id is required evidence but must not be
        # echoed back: only its presence bit is recorded.
        result, _ = evaluate_foreground(BUNDLE, ok_table(), ok_ps())
        assert result["facts"]["focus_window_present"] is True
        assert "10" not in render_json(result)
