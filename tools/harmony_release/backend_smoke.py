"""HarmonyOS real-API backend smoke wrapper (M14-84).

Plans - and, only with an explicit confirmation flag, executes - one
read-only-against-production smoke run that proves the HarmonyOS app talks to
the **real** FastAPI backend (not the Android mock):

  * a host-side preflight of anonymous read-only GET endpoints on a
    **loopback-only** base URL (never a LAN listener, never a write method,
    never any credential);
  * then, on the emulator only: install the current unsigned HAP, start the
    ability, drive the app's own Settings UI to point it at the fixed device
    URL ``http://10.0.2.2:8000/`` (the emulator's loopback alias of the host),
    then cold-restart onto an IME-free Home and assert the real backend's
    honest answers are rendered;
  * finally force-stop the ability and uninstall the bundle (cleanup).

Anonymous endpoint expectations (fixed, fail-closed):

    GET /health                     -> 200 (JSON with a "status" field)
    GET /api/v1/auth/status         -> 200 (JSON with "auth_enabled")
    GET /api/v1/version             -> 200 (JSON with "version")
    GET /api/v1/system/privacy      -> 401 (honest unauthorized)
    GET /api/v1/system/ops-snapshot -> 401 (honest unauthorized)
    GET /api/v1/audit?limit=100     -> 401 (honest unauthorized)

Any deviation (wrong status, non-JSON 2xx body, network error, non-loopback
base URL) fails closed *before* anything is installed.

Documented child commands (values go to the child only; the JSON records
command *shapes* - program/subcommand/option and operand *names* - never an
argv, never the target string, never an absolute path)::

    <HTTP GET> <LOOPBACK_URL><ENDPOINT>            (host preflight, injectable)
    hdc -t <TARGET> install <HAP>
    hdc -t <TARGET> shell aa start -a <ABILITY> -b <BUNDLE>
    hdc -t <TARGET> shell uitest dumpLayout -p <REMOTE_LAYOUT_PATH>
    hdc -t <TARGET> file recv <REMOTE_LAYOUT_PATH> <LOCAL_LAYOUT_PATH>
    hdc -t <TARGET> shell uitest uiInput click <X> <Y>
    hdc -t <TARGET> shell uitest uiInput keyEvent <BACKSPACE>
    hdc -t <TARGET> shell uitest uiInput inputText <X> <Y> <TEXT>
    hdc -t <TARGET> shell aa force-stop <BUNDLE>
    hdc -t <TARGET> uninstall <BUNDLE>

Steps and their phases (executed in this order)::

    host_preflight read_only   anonymous loopback GETs; any mismatch blocks
                               everything below
    install        mutation    installs the HAP
    start          foreground  starts the ability (aa start)
    settings_ui    foreground  drives the Settings UI: clear the base-URL
                               field, type the fixed device URL, tap 保存,
                               verify the honest 已保存 confirmation
    home_view      read_only   cold-restarts the ability (aa force-stop +
                               aa start) and asserts the real backend's
                               answers on the fresh Home; the restart is
                               required because after the Settings typing
                               the IME panel covers the bottom tab bar,
                               and it also proves the persisted base URL
                               survives a process restart
    background     background  aa force-stop
    uninstall      cleanup     removes the bundle again

Safety contract (inherited from device_smoke and extended):
- ``--target`` is required and must be **one explicit** device target;
  discovery and the "all devices" spellings are rejected outright.
- The host preflight base URL (``--api-base``) must be loopback-only
  (127.0.0.1 / localhost / [::1], http or https). Anything else - including
  the device URL 10.0.2.2 - is rejected before any request or install.
- The device URL is a fixed constant typed into the app's Settings UI; this
  wrapper never performs a request against it and never creates a listener.
- GET only, no auth header, no credential, no write method, no retry.
- Mutation is opt-in: without ``--confirm-mutation`` the run is a pure plan -
  no HTTP request, no hdc process, every step ``not_run``.
- ``runner`` (hdc commands), ``http_get`` (host preflight), ``tool_resolver``,
  ``target_resolver`` and ``bundle_resolver`` are injectable, so unit tests
  never touch a device and never open a socket.
- Never serialized: environment values, secrets, raw argv, absolute paths,
  command stdout/stderr, layout content (only digests and counts), the raw
  target string. The target is recorded as a 12-hex SHA-256 prefix plus a
  loopback/other kind; URLs are recorded as scheme+host+port only for the
  preflight base (loopback by construction).
- Fail closed (exit 2, status ``blocked``) on: request validation failures
  (missing target, forbidden all-devices target, unknown target, missing/
  outside-repo/unreadable HAP, unresolvable bundle, non-loopback or malformed
  ``--api-base``, unusable ``--evidence-dir``). Fail closed (exit 1, status
  ``failure``) on: missing hdc, spawn failure/timeout, a nonzero command exit
  code, any preflight status/shape mismatch, a settings-UI step that cannot
  honestly confirm the saved URL, a Home assertion miss, and cleanup
  (uninstall) failure (surfaced as its own ``cleanup_failed``).
- Cleanup semantics: once ``install`` succeeded, uninstall is attempted even
  when a later step failed.

Honest boundaries: HTTP status codes and JSON field *presence* are the only
preflight evidence (bodies are never recorded); device evidence is exit codes
plus layout dumps that are hashed, counted and text-asserted but never stored
in the JSON (raw dumps go only to ``--evidence-dir`` when supplied); no
hilog/screenshot is read; the HAP filename never implies signedness.

Exit codes: 0 = ok or planned (dry run), 1 = failure (fail-closed),
2 = blocked (fail-closed request validation, nothing executed).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

try:  # package import (pytest, python -m)
    from tools.harmony_release.device_smoke import (
        CommandResult,
        HdcTool,
        make_subprocess_runner,
        resolve_bundle,
        resolve_hdc_tool,
        resolve_target,
        DEFAULT_ABILITY,
        DEFAULT_COMMAND_TIMEOUT_SECONDS,
        DEFAULT_HDC,
        DEFAULT_REPO_ROOT,
        EXIT_BLOCKED,
        EXIT_FAILURE,
        EXIT_OK,
        MUTATION_CONFIRMATION_FLAG,
        SCRUBBED_ENV_VARS,
    )
    from tools.harmony_release.device_smoke import (
        inspect_hap as _inspect_hap_device_smoke,
    )
    from tools.harmony_release.device_smoke import (
        child_env as _child_env_device_smoke,
    )
except ImportError:  # direct script: python tools/harmony_release/backend_smoke.py
    from device_smoke import (  # type: ignore[no-redef]
        CommandResult,
        HdcTool,
        make_subprocess_runner,
        resolve_bundle,
        resolve_hdc_tool,
        resolve_target,
        DEFAULT_ABILITY,
        DEFAULT_COMMAND_TIMEOUT_SECONDS,
        DEFAULT_HDC,
        DEFAULT_REPO_ROOT,
        EXIT_BLOCKED,
        EXIT_FAILURE,
        EXIT_OK,
        MUTATION_CONFIRMATION_FLAG,
        SCRUBBED_ENV_VARS,
    )
    from device_smoke import inspect_hap as _inspect_hap_device_smoke  # type: ignore[no-redef]
    from device_smoke import child_env as _child_env_device_smoke  # type: ignore[no-redef]
SCHEMA_VERSION = 1
TOOL_NAME = "harmony_backend_smoke"

# Fixed coordinates of the run (names only are ever serialized).
REMOTE_LAYOUT_PATH = "/data/local/tmp/aios_backend_smoke_layout.json"
LAYOUT_FILENAME_SETTINGS = "backend_smoke_layout_settings.json"
LAYOUT_FILENAME_HOME = "backend_smoke_layout_home.json"

# The device-side base URL: the emulator's loopback alias of the host. It is
# typed into the app's own Settings UI and never requested by this wrapper.
DEVICE_API_BASE_URL = "http://10.0.2.2:8000/"

# Default host preflight base (loopback only; overridable, still loopback).
DEFAULT_API_BASE = "http://127.0.0.1:8000/"

# Anonymous read-only GET preflight: (name, path, expected status, required
# JSON field for 2xx bodies). 401s are the *honest* expectation: these
# endpoints require a bearer token the smoke never has and never fabricates.
HOST_ENDPOINTS: Tuple[Tuple[str, str, int, Optional[str]], ...] = (
    ("health", "/health", 200, "status"),
    ("auth_status", "/api/v1/auth/status", 200, "auth_enabled"),
    ("version", "/api/v1/version", 200, "version"),
    ("privacy", "/api/v1/system/privacy", 401, None),
    ("ops_snapshot", "/api/v1/system/ops-snapshot", 401, None),
    ("audit", "/api/v1/audit?limit=100", 401, None),
)

# Loopback-only preflight base URL. No LAN listener, no external host.
API_BASE_RE = re.compile(
    r"^(?:http|https)://(?:127\.0\.0\.1|localhost|\[::1\])(?::([0-9]{1,5}))?/$",
    re.IGNORECASE,
)


def _port_is_valid(port_text: Optional[str]) -> bool:
    """0-65535 only; the regex alone accepts 5 digits like 99999."""
    return port_text is None or 0 <= int(port_text) <= 65535

# Home layout assertions: text that must appear at least N times in the
# visible texts of the Home tab after pointing the app at the real backend.
HOME_EXPECT_TEXTS: Tuple[Tuple[str, int], ...] = (
    ("请求失败 (HTTP 401)", 3),  # privacy + ops-snapshot + audit zones
    ("0.1.0", 1),               # real backend version string
)

# Settings UI drive constants. The app launches on the 首页 tab, so the
# settings pane is reached by tapping the 设置 tab first.
SETTINGS_TAB_TEXT = "设置"
# The Settings base-URL field is identified structurally: it is the
# layout's TextInput node. Text labels (Home's 服务地址 line, Settings
# captions) are type Text and must never be selected as the input.
SETTINGS_INPUT_TYPE = "TextInput"
SETTINGS_SAVE_TEXT = "保存"
SETTINGS_SAVED_PREFIX = "已保存: "
# Home is reached by cold restart (aa force-stop + aa start), never by
# tapping a tab: after the Settings typing the IME panel covers the bottom
# tab bar, so no Home-tab constant exists here.
BACKSPACE_KEY_EVENT = "2055"  # HarmonyOS KeyCode.KEYCODE_DEL (backspace)
UI_SETTLE_SECONDS = 1.2
SAVE_SETTLE_SECONDS = 1.5
HOME_SETTLE_SECONDS = 6.0
SETTINGS_MAX_ATTEMPTS = 3

STEP_HOST_PREFLIGHT = "host_preflight"
STEP_INSTALL = "install"
STEP_START = "start"
STEP_SETTINGS_UI = "settings_ui"
STEP_HOME_VIEW = "home_view"
STEP_BACKGROUND = "background"
STEP_UNINSTALL = "uninstall"

STEP_ORDER = (
    (STEP_HOST_PREFLIGHT, "read_only"),
    (STEP_INSTALL, "mutation"),
    (STEP_START, "foreground"),
    (STEP_SETTINGS_UI, "foreground"),
    (STEP_HOME_VIEW, "read_only"),
    (STEP_BACKGROUND, "background"),
    (STEP_UNINSTALL, "cleanup"),
)

REASON_MUTATION_NOT_CONFIRMED = "mutation_not_confirmed"
REASON_REQUEST_INVALID = "request_invalid"
REASON_TOOLCHAIN_UNAVAILABLE = "toolchain_unavailable"
REASON_PREVIOUS_STEP_FAILED = "previous_step_failed"
REASON_INSTALL_NOT_SUCCESSFUL = "install_not_successful"

STATUS_PLANNED = "planned"
STATUS_OK = "ok"
STATUS_FAILURE = "failure"
STATUS_BLOCKED = "blocked"

STEP_STATUS_OK = "ok"
STEP_STATUS_FAILURE = "failure"
STEP_STATUS_NOT_RUN = "not_run"

CLEANUP_NOT_REQUIRED = "not_required"
TARGET_RECORDED_AS = "sha256_prefix"

HttpGetter = Callable[[str, float], Tuple[int, str]]
CommandRunner = Callable[[Sequence[str], Path, Dict[str, str]], CommandResult]
ToolResolver = Callable[[str], Tuple[Optional[HdcTool], List[dict]]]
TargetResolver = Callable[
    [Optional[str], Optional[Sequence[str]]],
    Tuple[Optional[object], List[dict]],
]
BundleResolver = Callable[[Path, Optional[str]], Tuple[Optional[str], List[dict]]]


def real_http_get(url: str, timeout_seconds: float) -> Tuple[int, str]:
    """Real anonymous GET. Never sends credentials, never retries."""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        url, method="GET",
        headers={"accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as resp:
            body = resp.read(1 << 20).decode("utf-8", errors="replace")
            return int(resp.status), body
    except urllib.error.HTTPError as err:  # honest non-2xx is still an answer
        body = err.read(1 << 20).decode("utf-8", errors="replace") if err.fp else ""
        return int(err.code), body


def validate_api_base(raw: Optional[str]) -> Tuple[Optional[str], List[dict]]:
    """Loopback-only validation of the preflight base URL."""
    if raw is None or not str(raw).strip():
        return None, [{
            "code": "api_base_required",
            "detail": {"argument": "--api-base"},
        }]
    token = str(raw).strip()
    match = API_BASE_RE.match(token)
    if token != str(raw) or not match or not _port_is_valid(
        match.group(1)
    ):
        return None, [{
            "code": "api_base_not_loopback",
            "detail": {
                "argument": "--api-base",
                "rule": "scheme http/https, loopback host only, "
                        "trailing slash, no path/query/userinfo",
            },
        }]
    return token, []


def _url_origin(base: str) -> str:
    """scheme://host[:port] of a loopback base (safe to record)."""
    return base.rsplit("/", 1)[0] if base.endswith("/") else base


# ---------------------------------------------------------------- layout ----

def _bounds_center(bounds: str) -> Optional[Tuple[int, int]]:
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", str(bounds))
    if not match:
        return None
    x1, y1, x2, y2 = (int(g) for g in match.groups())
    return (x1 + x2) // 2, (y1 + y2) // 2


def _walk_texts(node: object, acc: List[Tuple[str, str]]) -> None:
    """Collect (text, bounds) pairs - each layout node exactly once.

    A node's own ``attributes`` is read only where the node itself is
    visited, never again from its parent, so no text is ever double-counted.
    """
    if isinstance(node, dict):
        attrs = node.get("attributes")
        if isinstance(attrs, dict):
            text = attrs.get("text")
            bounds = attrs.get("bounds")
            if isinstance(text, str) and text and isinstance(bounds, str):
                acc.append((text, bounds))
        for value in node.values():
            _walk_texts(value, acc)
    elif isinstance(node, list):
        for value in node:
            _walk_texts(value, acc)


def layout_texts(parsed: object) -> List[Tuple[str, str]]:
    texts: List[Tuple[str, str]] = []
    _walk_texts(parsed, texts)
    return texts


def _walk_typed_nodes(node: object, acc: List[Tuple[str, str, str]]) -> None:
    """Collect (type, text, bounds) triples - one entry per layout node."""
    if isinstance(node, dict):
        attrs = node.get("attributes")
        if isinstance(attrs, dict):
            ntype = attrs.get("type")
            text = attrs.get("text")
            bounds = attrs.get("bounds")
            if (isinstance(ntype, str) and ntype
                    and isinstance(bounds, str)):
                acc.append((
                    ntype,
                    text if isinstance(text, str) else "",
                    bounds,
                ))
        for value in node.values():
            _walk_typed_nodes(value, acc)
    elif isinstance(node, list):
        for value in node:
            _walk_typed_nodes(value, acc)


def layout_typed_nodes(parsed: object) -> List[Tuple[str, str, str]]:
    typed: List[Tuple[str, str, str]] = []
    _walk_typed_nodes(parsed, typed)
    return typed


def find_input_node(
    typed: Sequence[Tuple[str, str, str]]
) -> Optional[Tuple[int, int, str]]:
    """The Settings base-URL input: the first node whose type is exactly
    TextInput (M14-84 real dumps: the URL field is the pane's only
    TextInput). Text labels such as Home's 服务地址: http://... are type
    Text and can never be selected, whatever their text contains."""
    for ntype, text, bounds in typed:
        if ntype != SETTINGS_INPUT_TYPE:
            continue
        center = _bounds_center(bounds)
        if center is None:
            continue
        return center[0], center[1], text
    return None


def is_settings_layout(typed: Sequence[Tuple[str, str, str]]) -> bool:
    """True only when the layout exposes an editable TextInput node.

    This is the "we are on the Settings pane" predicate: presence of the
    structural input node, not any caption text (the bottom tab bar shows
    设置 on every page, so text matching cannot distinguish pages).
    """
    return find_input_node(typed) is not None


def find_text(texts: Sequence[Tuple[str, str]], needle: str) -> Optional[Tuple[int, int]]:
    for text, bounds in texts:
        if needle in text:
            center = _bounds_center(bounds)
            if center is not None:
                return center
    return None


def find_text_exact(
    texts: Sequence[Tuple[str, str]], needle: str
) -> Optional[Tuple[int, int]]:
    """Exact-text match first (falls back to substring) - for controls.

    The Settings pane renders a caption like ``仅保存服务基地址...`` that
    *contains* 保存 and appears in the layout before the real 保存 button;
    a plain substring lookup clicks the caption and never saves. Controls
    are therefore matched by exact text first.
    """
    for text, bounds in texts:
        if text == needle:
            center = _bounds_center(bounds)
            if center is not None:
                return center
    return find_text(texts, needle)


# ------------------------------------------------------------- ui driver ----

@dataclass(frozen=True)
class UiDriver:
    """Thin uitest driver over the injected command runner."""

    runner: CommandRunner
    root: Path
    env: Dict[str, str]
    program: str  # hdc path (never serialized)
    target: str   # raw target (never serialized)
    bundle: str   # bundle name (serialized elsewhere, not here)
    ability: str  # ability name (serialized elsewhere, not here)
    timeout: float
    local_layout: Path
    log: List[dict]

    def _hdc(self, *args: str) -> CommandResult:
        argv = [self.program, "-t", self.target, *args]
        try:
            outcome = self.runner(argv, self.root, self.env)
        except subprocess.TimeoutExpired:
            self.log.append({"action": "timeout", "subcommand": args[:2]})
            raise
        except OSError:
            self.log.append({"action": "spawn_failed", "subcommand": args[:2]})
            raise
        self.log.append({
            "action": args[1] if len(args) > 1 else args[0],
            "ok": outcome.returncode == 0,
        })
        return outcome

    def click(self, x: int, y: int) -> CommandResult:
        return self._hdc("shell", "uitest", "uiInput", "click", str(x), str(y))

    def key_backspace(self) -> CommandResult:
        return self._hdc(
            "shell", "uitest", "uiInput", "keyEvent", BACKSPACE_KEY_EVENT
        )

    def restart_ability(self) -> None:
        """Cold-restart the ability: lands on 首页 with no IME panel.

        Used by the Home step because after Settings typing the IME panel
        covers the bottom tab bar, making tab-click navigation unreachable.
        Only command shapes already documented in PLANNED_SHAPES are used.
        """
        self._hdc("shell", "aa", "force-stop", self.bundle)
        self._hdc("shell", "aa", "start", "-a", self.ability,
                  "-b", self.bundle)

    def input_text(self, x: int, y: int, text: str) -> CommandResult:
        return self._hdc(
            "shell", "uitest", "uiInput", "inputText", str(x), str(y), text
        )

    def dump(self) -> Tuple[Optional[object], List[dict]]:
        """dumpLayout + file recv + honest local validation."""
        failures: List[dict] = []
        self.local_layout.unlink(missing_ok=True)
        self._hdc("shell", "uitest", "dumpLayout", "-p", REMOTE_LAYOUT_PATH)
        self._hdc("file", "recv", REMOTE_LAYOUT_PATH, str(self.local_layout))
        if not self.local_layout.is_file():
            return None, [{"code": "layout_pull_failed"}]
        try:
            data = self.local_layout.read_bytes()
        except OSError:
            return None, [{"code": "layout_invalid_json"}]
        if not data:
            return None, [{"code": "layout_file_empty"}]
        try:
            parsed = json.loads(data.decode("utf-8", errors="replace"))
        except (ValueError, UnicodeError):
            return None, [{"code": "layout_invalid_json"}]
        return parsed, failures

    def digest(self) -> Optional[dict]:
        try:
            data = self.local_layout.read_bytes()
        except OSError:
            return None
        if not data:
            return None
        return {
            "filename": self.local_layout.name,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest().upper(),
            "content_recorded": False,
        }


def _drive_settings_url(
    driver: UiDriver,
) -> Tuple[bool, List[dict], List[dict]]:
    """Type the fixed device URL into Settings and save. Honest verification.

    Returns (saved, failures, retry_notes). A captured layout counts as
    "on Settings" only when it exposes an editable TextInput node; any other
    layout (typically the cold-start race still showing 首页, whose 服务地址
    URL line is a Text label) gets a fresh 设置 tab click on every bounded
    attempt - attempt 1's click is never assumed to have landed. Non-
    convergence notes on attempts that a later attempt survives are retries,
    not step failures; they only become failures when every bounded attempt
    is exhausted.
    """
    failures: List[dict] = []
    notes: List[dict] = []
    for attempt in range(1, SETTINGS_MAX_ATTEMPTS + 1):
        parsed, dump_failures = driver.dump()
        if parsed is None:
            failures += dump_failures or [{"code": "settings_layout_unreadable"}]
            continue
        texts = layout_texts(parsed)
        typed = layout_typed_nodes(parsed)
        if not is_settings_layout(typed):
            # Not on the Settings pane: click the 设置 tab again on THIS
            # attempt and re-capture before looking for the input.
            tab = find_text(texts, SETTINGS_TAB_TEXT)
            if tab is None:
                failures.append({
                    "code": "settings_tab_not_found",
                    "detail": {"attempt": attempt},
                })
                time.sleep(UI_SETTLE_SECONDS)
                continue
            driver.click(*tab)
            time.sleep(UI_SETTLE_SECONDS)
            parsed, dump_failures = driver.dump()
            if parsed is None:
                failures += dump_failures or [{"code": "settings_layout_unreadable"}]
                continue
            texts = layout_texts(parsed)
            typed = layout_typed_nodes(parsed)
        field = find_input_node(typed)
        if field is None:
            notes.append({
                "code": "settings_input_not_found",
                "detail": {"attempt": attempt},
            })
            time.sleep(UI_SETTLE_SECONDS)
            continue
        fx, fy, current = field
        driver.click(fx, fy)
        time.sleep(UI_SETTLE_SECONDS)
        # Clear whatever is in the field, then type the fixed URL.
        for _ in range(min(len(current) + 8, 64)):
            driver.key_backspace()
        time.sleep(UI_SETTLE_SECONDS)
        driver.input_text(fx, fy, DEVICE_API_BASE_URL)
        time.sleep(UI_SETTLE_SECONDS)

        verify, verify_failures = driver.dump()
        if verify is None:
            failures += verify_failures or [
                {"code": "settings_verify_layout_unreadable"}]
            continue
        vfield = find_input_node(layout_typed_nodes(verify))
        if vfield is None or DEVICE_API_BASE_URL not in vfield[2]:
            failures.append({
                "code": "settings_input_mismatch",
                "detail": {"attempt": attempt},
            })
            continue
        save = find_text_exact(layout_texts(verify), SETTINGS_SAVE_TEXT)
        if save is None:
            failures.append({
                "code": "settings_save_button_not_found",
                "detail": {"attempt": attempt},
            })
            continue
        driver.click(*save)
        time.sleep(SAVE_SETTLE_SECONDS)
        final, _ = driver.dump()
        if final is None:
            failures.append({"code": "settings_final_layout_unreadable"})
            continue
        joined = "\n".join(t for t, _b in layout_texts(final))
        if SETTINGS_SAVED_PREFIX + DEVICE_API_BASE_URL in joined:
            return True, [], notes
        failures.append({
            "code": "settings_save_not_confirmed",
            "detail": {"attempt": attempt},
        })
    return False, failures + notes, []


def _assert_home(driver: UiDriver) -> Tuple[Optional[dict], List[dict]]:
    """Prove the saved URL works from a clean, IME-free Home screen.

    After the Settings typing the IME panel covers the bottom tab bar (the
    Settings dump has no tab texts at all), so tab-click navigation is not
    reachable. Instead the ability is restarted: a cold start lands on 首页
    with no IME and reads the *persisted* base URL - proving the save
    survived a restart and the real backend's honest answers render.
    """
    failures: List[dict] = []
    driver.restart_ability()
    time.sleep(HOME_SETTLE_SECONDS)
    parsed, dump_failures = driver.dump()
    if parsed is None:
        return None, dump_failures or [{"code": "home_layout_unreadable"}]
    texts = layout_texts(parsed)
    counts = {needle: sum(1 for t, _b in texts if needle in t)
              for needle, _min in HOME_EXPECT_TEXTS}
    for needle, minimum in HOME_EXPECT_TEXTS:
        if counts[needle] < minimum:
            failures.append({
                "code": "home_assertion_missed",
                "detail": {"needle": needle, "expected_min": minimum,
                           "actual": counts[needle]},
            })
    digest = driver.digest()
    record = None
    if digest is not None:
        record = {**digest, "node_text_count": len(texts), "counts": counts}
    return record, failures


# --------------------------------------------------------------- shapes ----

def _shape(subcommand: str, operand_names: Sequence[str]) -> dict:
    return {
        "program": "hdc",
        "subcommand": subcommand,
        "target_option": "-t",
        "target_recorded": False,
        "option_names": [],
        "operand_names": list(operand_names),
        "env_keys": [],
        "stripped_env_keys": list(SCRUBBED_ENV_VARS),
    }


PLANNED_SHAPES: Dict[str, List[dict]] = {
    STEP_HOST_PREFLIGHT: [{
        "program": "http", "subcommand": "GET",
        "url_recorded": "origin_only_loopback",
        "endpoints": [name for name, _p, _s, _f in HOST_ENDPOINTS],
    }],
    STEP_INSTALL: [_shape("install", ["hap"])],
    STEP_START: [_shape("shell aa start", ["ability", "bundle"])],
    STEP_SETTINGS_UI: [
        _shape("shell uitest dumpLayout", ["remote_layout_path"]),
        _shape("shell uitest uiInput click", ["x", "y"]),
        _shape("shell uitest uiInput keyEvent", ["backspace"]),
        _shape("shell uitest uiInput inputText", ["x", "y", "text"]),
        _shape("file recv", ["remote_layout_path", "local_layout_path"]),
    ],
    STEP_HOME_VIEW: [
        _shape("shell aa force-stop", ["bundle"]),
        _shape("shell aa start", ["ability", "bundle"]),
        _shape("shell uitest dumpLayout", ["remote_layout_path"]),
        _shape("file recv", ["remote_layout_path", "local_layout_path"]),
    ],
    STEP_BACKGROUND: [_shape("shell aa force-stop", ["bundle"])],
    STEP_UNINSTALL: [_shape("uninstall", ["bundle"])],
}


def _not_run_step(name: str, phase: str, reason: Optional[str]) -> dict:
    return {
        "name": name,
        "phase": phase,
        "status": STEP_STATUS_NOT_RUN,
        "reason": reason,
        "commands": [
            {**s, "executed": False, "exit_code": None, "error": None}
            for s in PLANNED_SHAPES[name]
        ],
        "failures": [],
    }


# ------------------------------------------------------------------ run ----

def run_backend_smoke(
    repo_root: Path,
    *,
    target: Optional[str] = None,
    hap: Optional[str] = None,
    bundle: Optional[str] = None,
    ability: str = DEFAULT_ABILITY,
    hdc: str = DEFAULT_HDC,
    api_base: Optional[str] = DEFAULT_API_BASE,
    confirm_mutation: bool = False,
    known_targets: Optional[Sequence[str]] = None,
    evidence_dir: Optional[Path] = None,
    timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    runner: Optional[CommandRunner] = None,
    http_get: Optional[HttpGetter] = None,
    tool_resolver: Optional[ToolResolver] = None,
    target_resolver: Optional[TargetResolver] = None,
    bundle_resolver: Optional[BundleResolver] = None,
) -> Tuple[dict, int]:
    root = Path(repo_root).resolve()
    request_failures: List[dict] = []
    toolchain_failures: List[dict] = []
    execution_failures: List[dict] = []
    warnings: List[dict] = []
    steps: List[dict] = []
    preflight_records: List[dict] = []
    settings_record: Optional[dict] = None
    home_record: Optional[dict] = None
    resolved_target = None
    hap_record: Optional[dict] = None
    bundle_name: Optional[str] = None
    tool: Optional[HdcTool] = None
    commands_executed = 0
    hardware_touched = False
    mutation_performed = False
    cleanup_attempted = False

    getter = http_get or real_http_get

    # 1) Request validation. Nothing runs when any of these fails closed.
    resolved_target, target_failures = (target_resolver or resolve_target)(
        target, known_targets
    )
    request_failures += target_failures
    base, base_failures = validate_api_base(api_base)
    request_failures += base_failures
    if not target_failures:
        hap_record, hap_failures = _inspect_hap_device_smoke(root, hap)
        request_failures += hap_failures
    if not request_failures:
        bundle_name, bundle_failures = (bundle_resolver or resolve_bundle)(
            root, bundle
        )
        request_failures += bundle_failures
    evidence: Optional[Path] = None
    if not request_failures and evidence_dir is not None:
        try:
            evidence = Path(evidence_dir)
            evidence.mkdir(parents=True, exist_ok=True)
            if not evidence.is_dir():
                evidence = None
        except OSError:
            evidence = None
        if evidence is None:
            request_failures.append({
                "code": "evidence_dir_unusable",
                "detail": {"argument": "--evidence-dir"},
            })

    if request_failures:
        steps = [
            _not_run_step(name, phase, REASON_REQUEST_INVALID)
            for name, phase in STEP_ORDER
        ]
    elif not confirm_mutation:
        warnings.append({
            "code": REASON_MUTATION_NOT_CONFIRMED,
            "detail": {
                "confirmation_flag": MUTATION_CONFIRMATION_FLAG,
                "not_run": [name for name, _p in STEP_ORDER],
            },
        })
        steps = [
            {**_not_run_step(name, phase, REASON_MUTATION_NOT_CONFIRMED)}
            for name, phase in STEP_ORDER
        ]
    else:
        # 2) Toolchain probe (confirmed runs only).
        tool, toolchain_failures = (tool_resolver or resolve_hdc_tool)(hdc)
        if toolchain_failures:
            steps = [
                {**_not_run_step(name, phase, REASON_TOOLCHAIN_UNAVAILABLE)}
                for name, phase in STEP_ORDER
            ]

    toolchain_record = {
        "probed": bool(confirm_mutation and not request_failures),
        "program_name": Path(hdc).name or DEFAULT_HDC,
        "resolved": tool is not None,
        "source": getattr(tool, "source", None),
    }

    if confirm_mutation and not request_failures and not toolchain_failures:
        run = runner or make_subprocess_runner(timeout_seconds)
        env = _child_env_device_smoke()
        program = str(tool.path)
        hap_path = (
            (root / hap_record["relpath"]) if hap_record else None
        )

        previous_failed = False
        for name, phase in STEP_ORDER:
            if name == STEP_UNINSTALL:
                if not mutation_performed:
                    steps.append(_not_run_step(
                        name, phase, REASON_INSTALL_NOT_SUCCESSFUL))
                    continue
            elif previous_failed:
                steps.append(_not_run_step(
                    name, phase, REASON_PREVIOUS_STEP_FAILED))
                continue

            step_failures: List[dict] = []

            if name == STEP_HOST_PREFLIGHT:
                for ep_name, path, expected, field in HOST_ENDPOINTS:
                    try:
                        status, body = getter(
                            base.rstrip("/") + path, timeout_seconds)
                    except Exception:  # network layer: honest failure
                        preflight_records.append({
                            "name": ep_name, "expected": expected,
                            "actual": None, "matched": False,
                            "body_json_valid": False,
                        })
                        step_failures.append({
                            "code": "preflight_request_failed",
                            "detail": {"endpoint": ep_name},
                        })
                        continue
                    body_json_valid = False
                    if 200 <= status < 300:
                        try:
                            parsed = json.loads(body)
                            body_json_valid = isinstance(parsed, dict)
                            if body_json_valid and field and field not in parsed:
                                step_failures.append({
                                    "code": "preflight_field_missing",
                                    "detail": {
                                        "endpoint": ep_name, "field": field,
                                    },
                                })
                        except (ValueError, UnicodeError):
                            body_json_valid = False
                    # 200 endpoints must also be valid JSON with the field.
                    matched = (status == expected) and (
                        status != 200 or body_json_valid
                    )
                    preflight_records.append({
                        "name": ep_name,
                        "expected": expected,
                        "actual": status,
                        "matched": matched,
                        "body_json_valid": body_json_valid,
                    })
                    if not matched and not any(
                        f["code"] == "preflight_field_missing"
                        and f["detail"]["endpoint"] == ep_name
                        for f in step_failures
                    ):
                        step_failures.append({
                            "code": "preflight_status_mismatch",
                            "detail": {
                                "endpoint": ep_name,
                                "expected": expected, "actual": status,
                            },
                        })
                steps.append({
                    "name": name, "phase": phase,
                    "status": STEP_STATUS_FAILURE if step_failures
                    else STEP_STATUS_OK,
                    "reason": None,
                    "endpoints": preflight_records,
                    "failures": step_failures,
                })
                execution_failures += step_failures
                if step_failures:
                    previous_failed = True
                continue

            try:
                if name == STEP_INSTALL:
                    outcome = run(
                        [program, "-t", resolved_target.raw, "install",
                         str(hap_path)], root, env)
                elif name == STEP_START:
                    outcome = run(
                        [program, "-t", resolved_target.raw, "shell", "aa",
                         "start", "-a", ability, "-b", str(bundle_name)],
                        root, env)
                elif name in (STEP_SETTINGS_UI, STEP_HOME_VIEW):
                    local = Path(tempfile_default()) / (
                        LAYOUT_FILENAME_SETTINGS
                        if name == STEP_SETTINGS_UI else LAYOUT_FILENAME_HOME
                    )
                    driver = UiDriver(
                        runner=run, root=root, env=env, program=program,
                        target=resolved_target.raw, bundle=str(bundle_name),
                        ability=ability, timeout=timeout_seconds,
                        local_layout=local, log=[],
                    )
                    if name == STEP_SETTINGS_UI:
                        ok, ui_failures, retry_notes = (
                            _drive_settings_url(driver))
                        step_failures += ui_failures
                        digest = driver.digest() if ok else None
                        settings_record = {
                            "device_url_typed": DEVICE_API_BASE_URL
                            if ok else None,
                            "saved_confirmed": ok,
                            "layout": digest,
                            "ui_actions": len(driver.log),
                            "convergence_retries": len(retry_notes),
                        }
                    else:
                        record, ui_failures = _assert_home(driver)
                        step_failures += ui_failures
                        home_record = record
                    commands_executed += len(driver.log)
                    hardware_touched = True
                    if evidence is not None and local.is_file():
                        try:
                            target_copy = evidence / local.name
                            target_copy.write_bytes(local.read_bytes())
                        except OSError:
                            warnings.append({
                                "code": "evidence_copy_failed",
                                "detail": {"filename": local.name},
                            })
                    steps.append({
                        "name": name, "phase": phase,
                        "status": STEP_STATUS_FAILURE if step_failures
                        else STEP_STATUS_OK,
                        "reason": None,
                        "failures": step_failures,
                        **({"settings": settings_record}
                           if name == STEP_SETTINGS_UI and settings_record
                           else {}),
                        **({"home": home_record}
                           if name == STEP_HOME_VIEW and home_record
                           else {}),
                    })
                    execution_failures += step_failures
                    if step_failures:
                        previous_failed = True
                    continue
                elif name == STEP_BACKGROUND:
                    outcome = run(
                        [program, "-t", resolved_target.raw, "shell", "aa",
                         "force-stop", str(bundle_name)], root, env)
                else:  # uninstall
                    cleanup_attempted = True
                    outcome = run(
                        [program, "-t", resolved_target.raw, "uninstall",
                         str(bundle_name)], root, env)
            except subprocess.TimeoutExpired:
                hardware_touched = True
                steps.append({
                    "name": name, "phase": phase,
                    "status": STEP_STATUS_FAILURE,
                    "reason": None,
                    "failures": [{"code": f"{name}_timeout"}],
                })
                execution_failures.append({"code": f"{name}_timeout"})
                previous_failed = True
                if name == STEP_INSTALL:
                    mutation_performed = True  # may have half-installed
                continue
            except OSError:
                steps.append({
                    "name": name, "phase": phase,
                    "status": STEP_STATUS_FAILURE,
                    "reason": None,
                    "failures": [{"code": f"{name}_spawn_failed"}],
                })
                execution_failures.append({"code": f"{name}_spawn_failed"})
                previous_failed = True
                continue

            commands_executed += 1
            hardware_touched = True
            rc = int(outcome.returncode)
            if rc != 0:
                step_failures.append({
                    "code": f"{name}_failed",
                    "detail": {"exit_code": rc},
                })
            if name == STEP_INSTALL and rc == 0:
                mutation_performed = True
            steps.append({
                "name": name, "phase": phase,
                "status": STEP_STATUS_FAILURE if step_failures
                else STEP_STATUS_OK,
                "reason": None,
                "exit_code": rc,
                "failures": step_failures,
            })
            execution_failures += step_failures
            if step_failures:
                previous_failed = True

    # 3) Cleanup summary.
    cleanup_failures: List[dict] = []
    if mutation_performed:
        uninstall_step = next(
            (s for s in steps if s["name"] == STEP_UNINSTALL), None)
        uninstall_ok = bool(
            uninstall_step and uninstall_step["status"] == STEP_STATUS_OK)
        cleanup = {
            "required": True,
            "attempted": cleanup_attempted,
            "status": STEP_STATUS_OK if uninstall_ok else STEP_STATUS_FAILURE,
            "reason": None if uninstall_ok else "uninstall_not_successful",
        }
        if not uninstall_ok:
            cleanup_failures = [{
                "code": "cleanup_failed", "detail": {"step": STEP_UNINSTALL}}]
    else:
        cleanup = {
            "required": False,
            "attempted": cleanup_attempted,
            "status": CLEANUP_NOT_REQUIRED,
            "reason": REASON_INSTALL_NOT_SUCCESSFUL,
        }
    execution_failures += cleanup_failures

    failures = sorted(
        request_failures + toolchain_failures + execution_failures,
        key=lambda item: json.dumps(item, sort_keys=True))

    if request_failures and not toolchain_failures and not execution_failures:
        status = STATUS_BLOCKED
    elif failures:
        status = STATUS_FAILURE
    elif confirm_mutation:
        status = STATUS_OK
    else:
        status = STATUS_PLANNED

    exit_code = EXIT_OK
    if status == STATUS_BLOCKED:
        exit_code = EXIT_BLOCKED
    elif status == STATUS_FAILURE:
        exit_code = EXIT_FAILURE

    matched = [r["matched"] for r in preflight_records]
    result = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "status": status,
        "exit_code": exit_code,
        "dry_run": not confirm_mutation,
        "confirmation": {
            "flag": MUTATION_CONFIRMATION_FLAG,
            "confirmed": bool(confirm_mutation),
        },
        "target": ({
            "recorded_as": TARGET_RECORDED_AS,
            "hash": resolved_target.hash,
            "kind": resolved_target.kind,
            "validated_against_device_list": resolved_target.validated,
            "match": resolved_target.match,
            "raw_recorded": False,
        } if resolved_target is not None else None),
        "api_base_origin": _url_origin(base) if base else None,
        "api_base_loopback_only": True,
        "device_url": DEVICE_API_BASE_URL,
        "device_url_requested_by_wrapper": False,
        "hap": hap_record,
        "bundle_name": bundle_name,
        "ability": ability,
        "toolchain": toolchain_record,
        "preflight": {
            "endpoint_count": len(preflight_records),
            "all_matched": bool(matched) and all(matched),
            "records": preflight_records,
        },
        "settings": settings_record,
        "home": home_record,
        "device_access": {
            "commands_executed": commands_executed,
            "hardware_touched": hardware_touched or commands_executed > 0,
        },
        "mutation_performed": mutation_performed,
        "steps": steps,
        "cleanup": cleanup,
        "not_run": [s["name"] for s in steps
                    if s["status"] == STEP_STATUS_NOT_RUN],
        "device_side_state": {
            "bundle_uninstalled": bool(
                mutation_performed and cleanup["attempted"]
                and cleanup["status"] == STEP_STATUS_OK),
        },
        "evidence_boundaries": {
            "http_bodies_recorded": False,
            "credentials_used": False,
            "write_methods_used": False,
            "lan_listener_created": False,
            "layout_content_recorded": False,
            "device_logs_read": False,
            "signedness_verified": False,
            "production_touched": False,
        },
        "warnings": warnings,
        "failures": failures,
    }
    return result, exit_code


def tempfile_default() -> str:
    import tempfile
    return tempfile.gettempdir()


def render_json(result: dict) -> str:
    return json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True)


def render_summary(result: dict) -> str:
    parts = [
        f"{TOOL_NAME}: status={result['status']}",
        f"exit={result['exit_code']}",
        f"dry_run={str(result['dry_run']).lower()}",
        f"preflight={result['preflight']['all_matched']}",
    ]
    if result.get("hap"):
        parts.append(f"hap={result['hap']['relpath']}")
        parts.append(f"sha256={result['hap']['sha256']}")
    parts.append(f"mutation={str(result['mutation_performed']).lower()}")
    parts.append(f"cleanup={result['cleanup']['status']}")
    codes = ",".join(f["code"] for f in result["failures"])
    if codes:
        parts.append(f"failures={codes}")
    return " ".join(parts)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="backend_smoke",
        description="HarmonyOS real-API backend smoke (fail-closed, plan by "
        "default, loopback-only host preflight).",
    )
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument(
        "--target", required=True, default=None,
        help="One explicit device target (e.g. 127.0.0.1:5555).")
    parser.add_argument("--hap", default=None,
                        help="HAP to install (default: newest under "
                             "apps/harmony build output).")
    parser.add_argument("--bundle", default=None)
    parser.add_argument("--ability", default=DEFAULT_ABILITY)
    parser.add_argument("--hdc", default=DEFAULT_HDC)
    parser.add_argument(
        "--api-base", default=DEFAULT_API_BASE,
        help=f"Loopback-only preflight base URL (default {DEFAULT_API_BASE}).")
    parser.add_argument(
        "--device-id", action="append", default=None,
        help="Operator-known device id (repeatable) to cross-check --target.")
    parser.add_argument(MUTATION_CONFIRMATION_FLAG, action="store_true",
                        help="Opt in to device mutation; without it the run "
                             "is a plan that touches nothing.")
    parser.add_argument(
        "--evidence-dir", type=Path, default=None,
        help="Directory for raw layout evidence (created when missing).")
    parser.add_argument("--timeout-seconds", type=float,
                        default=DEFAULT_COMMAND_TIMEOUT_SECONDS)
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    result, exit_code = run_backend_smoke(
        repo_root=args.repo_root.resolve(),
        target=args.target,
        hap=args.hap,
        bundle=args.bundle,
        ability=args.ability,
        hdc=args.hdc,
        api_base=args.api_base,
        confirm_mutation=args.confirm_mutation,
        known_targets=args.device_id,
        evidence_dir=args.evidence_dir,
        timeout_seconds=args.timeout_seconds,
    )
    sys.stdout.write(render_json(result) + "\n")
    if not args.quiet:
        sys.stderr.write(render_summary(result) + "\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
