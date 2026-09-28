"""Pure HarmonyOS foreground-guard evidence parser (M14-170 R2a).

Turns the raw text evidence of two read-only device probes into a single
fail-closed foreground verdict for one requested bundle:

- ``hidumper -s WindowManagerService -a '-a'`` output -> window table rows
  plus the ``Focus window:`` line;
- ``ps -ef`` output -> the authoritative PID-to-bundle mapping.

Verdict semantics (the exact contract proven on the shared emulator in
M14-89 and now frozen here as pure, deterministic logic):

1. ``hidumper_unreadable`` - the hidumper probe failed (nonzero exit code
   or no readable output); no further evidence is interpreted.
2. ``focus_window_unreadable`` - the dump carries no parsable
   ``Focus window:`` line or no window rows at all.
3. ``app_window_missing`` - after filtering system surfaces (``SCB*`` /
   ``softKeyboard*`` legally cover our app) no app window row is
   full-screen (``w >= 1000`` in the ``[x y w h]`` rect).
4. ``bundle_map_missing`` - the top full-screen app window (max ``zord``)
   has a PID that ``ps -ef`` does not map to any bundle.
5. ``foreign_foreground`` - the bundle at the top is not EXACTLY the
   requested bundle (prefix/substring look-alikes fail; this is the
   requested-bundle proof).

Purity contract: this module performs **no** I/O, spawns no process,
touches no device, reads no environment and reads no clock. It only
parses the texts it is handed and returns a deterministic verdict dict
(zero wall-clock: identical inputs produce byte-identical JSON). Wiring
into a real smoke (runner, retries, recovery) is a later round's job;
nothing here integrates with any backend.

Exit-code convention (mirrors the release tools): 0 = ok (our bundle
holds the foreground), 1 = failure (one closed reason above). The
requested bundle must be a non-empty string; anything else raises
``ValueError`` before any evidence is interpreted (caller contract
violation, not device evidence).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

SCHEMA_VERSION = 1
TOOL_NAME = "harmony_foreground_guard"

# System-surface window-name prefixes observed on emulator API 24: the
# system shell (SCB*, incl. SCBKeyboardPanel) and the IME may legally
# hold focus/cover the screen while OUR app stays the top app window.
SYSTEM_WINDOW_PREFIXES = ("SCB", "softKeyboard")

# A full-screen app window on the shared-emulator class of devices is at
# least this wide (screen width 1268 observed; portrait split windows are
# far narrower). Boundary value itself is included.
FULLSCREEN_MIN_WIDTH = 1000

# Row shape (observed on emulator API 24):
# name displayId pid winId type mode flag zord orientation [ x y w h ]
# Only rows with a numeric pid and a complete rect are kept; every other
# line (headers, separators, prose) is ignored. zord may be negative.
WINDOW_ROW_RE = re.compile(
    r"^(\S+)\s+0\s+(\d+)\s+(\d+)\s+(\d+)\s+\d+\s+\d+\s+(-?\d+)\s+0"
    r"\s+\[\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*\]"
)
FOCUS_WINDOW_RE = re.compile(r"Focus window:\s*(\d+)")

# ps -ef row shape: UID PID PPID C STIME TTY TIME CMD... - the joined
# CMD tail is the bundle name for app processes. Window names such as
# app0/poc0 are NOT bundle names, so this mapping is authoritative.
PS_MIN_FIELDS = 8
PS_PID_FIELD = 1
PS_CMD_FIELD = 7

REASON_HIDUMPER_UNREADABLE = "hidumper_unreadable"
REASON_FOCUS_WINDOW_UNREADABLE = "focus_window_unreadable"
REASON_APP_WINDOW_MISSING = "app_window_missing"
REASON_BUNDLE_MAP_MISSING = "bundle_map_missing"
REASON_FOREIGN_FOREGROUND = "foreign_foreground"

CLOSED_REASONS = (
    REASON_HIDUMPER_UNREADABLE,
    REASON_FOCUS_WINDOW_UNREADABLE,
    REASON_APP_WINDOW_MISSING,
    REASON_BUNDLE_MAP_MISSING,
    REASON_FOREIGN_FOREGROUND,
)

STATUS_OK = "ok"
STATUS_FAILURE = "failure"

EXIT_OK = 0
EXIT_FAILURE = 1


@dataclass(frozen=True)
class WindowRow:
    """One parsed window-table row. Facts only; never device identity."""

    name: str
    pid: int
    win_id: int
    zord: int
    w: int

    def to_record(self) -> dict:
        return {
            "name": self.name,
            "pid": self.pid,
            "win_id": self.win_id,
            "zord": self.zord,
            "w": self.w,
        }


def parse_window_rows(text: str) -> list[WindowRow]:
    """Parse every window-table row the strict row regex accepts.

    Pure: same text in, same rows out. Non-matching lines (headers,
    prose, rows without a numeric pid or a complete rect) are skipped.
    """
    rows: list[WindowRow] = []
    for line in (text or "").splitlines():
        match = WINDOW_ROW_RE.match(line)
        if match:
            rows.append(WindowRow(
                name=match.group(1),
                pid=int(match.group(2)),
                win_id=int(match.group(3)),
                zord=int(match.group(5)),
                w=int(match.group(8)),
            ))
    return rows


def parse_focus_window(text: str) -> Optional[int]:
    """Return the focused window id, or None when the line is absent."""
    match = FOCUS_WINDOW_RE.search(text or "")
    return int(match.group(1)) if match else None


def is_system_surface(name: str) -> bool:
    """True for SCB*/softKeyboard* surfaces that legally cover our app."""
    return name.startswith(SYSTEM_WINDOW_PREFIXES)


def filter_app_windows(rows: list[WindowRow]) -> list[WindowRow]:
    """Drop system surfaces; keep app windows (table order preserved)."""
    return [row for row in rows if not is_system_surface(row.name)]


def filter_fullscreen(
    rows: list[WindowRow],
    min_width: int = FULLSCREEN_MIN_WIDTH,
) -> list[WindowRow]:
    """Keep full-screen rows only (width boundary included)."""
    return [row for row in rows if row.w >= min_width]


def select_top_window(rows: list[WindowRow]) -> Optional[WindowRow]:
    """Highest zord wins; on a tie the first table row wins (stable)."""
    if not rows:
        return None
    return max(rows, key=lambda row: row.zord)


def parse_bundle_map(ps_text: str) -> dict[int, str]:
    """Parse ``ps -ef`` output into a PID -> bundle (CMD tail) map.

    Rows shorter than 8 fields or without a numeric PID field (the
    header, prose) are skipped. On duplicate PIDs the FIRST row wins,
    mirroring a first-match ps scan; the CMD tail keeps its spaces.
    """
    mapping: dict[int, str] = {}
    for line in (ps_text or "").splitlines():
        parts = line.split()
        if len(parts) < PS_MIN_FIELDS:
            continue
        token = parts[PS_PID_FIELD]
        if not token.isdigit():
            continue
        pid = int(token)
        if pid not in mapping:
            mapping[pid] = " ".join(parts[PS_CMD_FIELD:])
    return mapping


def _require_bundle(requested_bundle: str) -> str:
    if not isinstance(requested_bundle, str) or not requested_bundle:
        raise ValueError("requested_bundle must be a non-empty string")
    return requested_bundle


def evaluate_foreground(
    requested_bundle: str,
    hidumper_text: str,
    ps_text: str,
    hidumper_returncode: int = 0,
) -> tuple[dict, int]:
    """One fail-closed foreground verdict; returns (result, exit code).

    Exactly one closed reason is reported (the first gate that fails,
    in the documented order); an ok verdict reports none. Recorded
    facts are counts and geometry only (row counts, z-order, rect
    width); raw identifiers - window names, pids, window ids, device
    bundle strings - never enter the returned details.
    """
    bundle = _require_bundle(requested_bundle)
    text = hidumper_text or ""

    facts: dict = {
        "hidumper_returncode": hidumper_returncode,
        "hidumper_output_readable": bool(text.strip()),
        "window_rows_total": 0,
        "system_window_rows": 0,
        "app_window_rows": 0,
        "fullscreen_rows": 0,
        "focus_window_present": False,
        "top_zord": None,
        "top_w": None,
    }
    reasons: list[str] = []

    if hidumper_returncode != 0 or not text.strip():
        reasons.append(REASON_HIDUMPER_UNREADABLE)
    else:
        rows = parse_window_rows(text)
        app_rows = filter_app_windows(rows)
        fullscreen_rows = filter_fullscreen(app_rows)
        top = select_top_window(fullscreen_rows)
        focused_win = parse_focus_window(text)
        facts.update({
            "window_rows_total": len(rows),
            "system_window_rows": len(rows) - len(app_rows),
            "app_window_rows": len(app_rows),
            "fullscreen_rows": len(fullscreen_rows),
            "focus_window_present": focused_win is not None,
            "top_zord": top.zord if top is not None else None,
            "top_w": top.w if top is not None else None,
        })
        if focused_win is None or not rows:
            reasons.append(REASON_FOCUS_WINDOW_UNREADABLE)
        elif not fullscreen_rows:
            reasons.append(REASON_APP_WINDOW_MISSING)
        else:
            bundle_at_top = parse_bundle_map(ps_text).get(top.pid)
            if bundle_at_top is None:
                reasons.append(REASON_BUNDLE_MAP_MISSING)
            elif bundle_at_top != bundle:
                reasons.append(REASON_FOREIGN_FOREGROUND)

    status = STATUS_FAILURE if reasons else STATUS_OK
    result = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "status": status,
        "exit_code": EXIT_FAILURE if reasons else EXIT_OK,
        "reasons": reasons,
        "primary_reason": reasons[0] if reasons else None,
        "facts": facts,
        "boundaries": {
            "pure_parser": True,
            "io_performed": False,
            "process_spawned": False,
            "device_contacted": False,
            "recovery_attempted": False,
            "backend_integration": False,
        },
    }
    return result, result["exit_code"]


def render_json(result: dict) -> str:
    """Deterministic serialization (sorted keys, ASCII-safe)."""
    return json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True)
