"""HarmonyOS authenticated-session UI smoke wrapper (M14-89).

Plans - and, only with an explicit confirmation flag, executes - one
authenticated smoke run against the **task-owned mock server**
(tools/harmony_mock/server.py, started separately with ``--auth``):

  * a host-side auth-contract preflight (login / me / gated GET / 401s)
    against the loopback mock URL - no credential is ever recorded;
  * an emulator UI flow driven through the app's own screens:
      - auth-off local mode (no login controls rendered),
      - auth-on: wrong-password 401 (honest error, no half session),
      - auth-on: successful login (validated by /auth/me),
      - cold-restart secure persistence (vault token + /me revalidate),
      - logout cleanup and the honest 401 fallback afterwards.

The mock auth phase (off/on) is controlled by how the server was
started, so the wrapper asks ``--expect-auth`` (off|on). With ``off``
the flow stops after the auth-off local-mode step; with ``on`` the full
login lifecycle runs. Anything else fails closed.

Honest fail-closed contract (inherited from device_smoke / backend_smoke):
- ``--target`` is required and must be **one explicit** device target;
  discovery and "all devices" spellings are rejected outright.
- The mock server must already be listening on the loopback host URL
  (``--api-base``, default http://127.0.0.1:8765/). This wrapper never
  starts, stops or reconfigures a server, and never touches production.
- Mutation is opt-in: without ``--confirm-mutation`` the run is a pure
  plan - no HTTP request, no hdc process, every step ``not_run``.
- ``--hap`` must point at a repository HAP (typically the signed debug
  HAP); install/uninstall wrap the flow (cleanup).
- Never serialized: credentials, tokens, Authorization header values,
  raw argv, absolute paths, stdout/stderr, layout content (only digests,
  node-text counts and matched/missed needle names). The fixed synthetic
  mock credentials are typed into the emulator's own input fields and
  never appear in any record this tool writes.
- The tool writes to the repository only when ``--evidence-dir`` is
  given, and even then only JSON evidence it produced itself.

Exit codes follow device_smoke: 0 ok, 1 failure, 2 blocked.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

try:
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
    )
    from tools.harmony_release.device_smoke import inspect_hap as _inspect_hap
    from tools.harmony_release.device_smoke import child_env as _child_env
except ImportError:  # direct script: python tools/harmony_release/auth_smoke.py
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
    )
    from device_smoke import inspect_hap as _inspect_hap  # type: ignore[no-redef]
    from device_smoke import child_env as _child_env  # type: ignore[no-redef]

SCHEMA_VERSION = 1
TOOL_NAME = "harmony_auth_smoke"

DEFAULT_API_BASE = "http://127.0.0.1:8765/"
DEVICE_API_BASE_URL = "http://10.0.2.2:8765/"


def device_api_base(api_base: str) -> str:
    """Derive the Android-emulator device URL from a validated loopback
    host URL (M14-95): host 127.0.0.1/localhost -> 10.0.2.2, same port,
    scheme and trailing slash preserved (default -> http://10.0.2.2:8765/).
    Input must come from validate_api_base (exact loopback, empty-or-/
    path); anything else is rejected fail-closed.
    """
    parts = urllib.parse.urlsplit(api_base)
    if (parts.scheme != "http"
            or parts.hostname not in ("127.0.0.1", "localhost")
            or parts.username is not None or parts.password is not None
            or parts.query or parts.fragment
            or parts.path not in ("", "/")):
        raise ValueError("api_base_not_validated_loopback")
    host = "10.0.2.2"
    port = parts.port
    if port is not None:
        host += ":" + str(port)
    return "http://" + host + "/"

# Fixed synthetic credentials (mock-only; never a real secret). They are
# typed into the emulator's own input fields and are never recorded.
MOCK_LOGIN_USER = "aiosstudent"
MOCK_LOGIN_PASS = "aios-pass-1234"
MOCK_WRONG_PASS = "wrong-password-89"
MOCK_TOKEN_HINT = "aios-mock-token"

# UI text anchors (Settings tab / AuthPane / HomePane).
SETTINGS_TAB_TEXT = "设置"
HOME_TAB_TEXT = "首页"
AUTH_PANE_TITLE = "认证登录"
LOCAL_MODE_TEXT = "本地模式(认证未开启)"
USERNAME_LABEL_TEXT = "用户名"
PASSWORD_LABEL_TEXT = "密码"
USERNAME_PLACEHOLDER_PREFIX = "用户名("
PASSWORD_PLACEHOLDER_PREFIX = "密码("
LOGIN_BUTTON_TEXT = "登录"
LOGOUT_BUTTON_TEXT = "登出"
CURRENT_USER_TEXT = "当前登录用户"
LOGIN_SUCCESS_TEXT = "登录成功"
LOGIN_FAIL_401_TEXT = "用户名或密码错误 (HTTP 401)"
RESTORED_TEXT = "已从安全存储恢复登录"
LOGGED_OUT_TEXT = "已登出"
HOME_REFRESH_BUTTON_TEXT = "整体刷新"
HOME_AUTH_ON_TEXT = "远程模式(认证已开启)"
HOME_401_TEXT = "HTTP 401"
HOME_PRIVACY_KV = "模型路由"
# HomePane renders this exact text (Unicode ellipsis …, U+2026)
# in every zone while its ZoneStatus is LOADING (M14-99 B1).
HOME_ZONE_LOADING_TEXT = "加载中\u2026"
# Bounded polling limits for the readiness helper (M14-99 B1).
HOME_ZONE_LOADING_DEADLINE_SECONDS = 15.0
HOME_ZONE_LOADING_POLL_INTERVAL_SECONDS = 0.5
SETTINGS_SAVE_TEXT = "保存"
SETTINGS_SAVED_PREFIX = "已保存: "
QUERY_LOADING_TEXT = "正在查询认证状态"
QUERY_FAILED_TEXT = "认证状态查询失败"
AUTH_RETRY_BUTTON_TEXT = "重试"
LOGGING_IN_TEXT = "正在登录"

REMOTE_LAYOUT_PATH = "/data/local/tmp/aios_auth_smoke_layout.json"
BACKSPACE_KEY_EVENT = "2055"  # HarmonyOS KeyCode.KEYCODE_DEL
BACK_KEY_EVENT = "2"         # HarmonyOS KeyCode.KEYCODE_BACK (dismiss IME)
# Window-name prefixes that belong to the system shell (SCB*) or the IME
# (softKeyboard*) - they may legally hold focus while our app stays on top.
SYSTEM_WINDOW_PREFIXES = ("SCB", "softKeyboard")
UI_SETTLE_SECONDS = 1.2
LONG_SETTLE_SECONDS = 4.0
HOME_SETTLE_SECONDS = 6.0
SETTINGS_MAX_ATTEMPTS = 5

STEP_HOST_AUTH_CONTRACT = "host_auth_contract"
STEP_INSTALL = "install"
STEP_START = "start"
STEP_SETTINGS_UI = "settings_ui"
STEP_AUTH_OFF_LOCAL = "auth_off_local"
STEP_HOME_401_UNAUTH = "home_401_unauth"
STEP_WRONG_PASSWORD = "wrong_password"
STEP_LOGIN_AND_REFRESH = "login_and_refresh"
STEP_COLD_RESTART = "cold_restart"
STEP_LOGOUT = "logout"
STEP_BACKGROUND = "background"
STEP_UNINSTALL = "uninstall"

STEP_ORDER = (
    (STEP_HOST_AUTH_CONTRACT, "read_only"),
    (STEP_INSTALL, "mutation"),
    (STEP_START, "foreground"),
    (STEP_SETTINGS_UI, "foreground"),
    (STEP_AUTH_OFF_LOCAL, "foreground"),
    (STEP_HOME_401_UNAUTH, "foreground"),
    (STEP_WRONG_PASSWORD, "foreground"),
    (STEP_LOGIN_AND_REFRESH, "foreground"),
    (STEP_COLD_RESTART, "foreground"),
    (STEP_LOGOUT, "foreground"),
    (STEP_BACKGROUND, "background"),
    (STEP_UNINSTALL, "cleanup"),
)

REASON_MUTATION_NOT_CONFIRMED = "mutation_not_confirmed"
REASON_REQUEST_INVALID = "request_invalid"
REASON_TOOLCHAIN_UNAVAILABLE = "toolchain_unavailable"
REASON_PREVIOUS_STEP_FAILED = "previous_step_failed"
REASON_INSTALL_NOT_SUCCESSFUL = "install_not_successful"
REASON_AUTH_PHASE_SKIP = "auth_phase_skip"
STEP_STATUS_OK = "ok"
STEP_STATUS_FAILURE = "failure"
STEP_STATUS_NOT_RUN = "not_run"


# ------------------------------------------------------------ http layer ----

HttpGetter = Callable[[str, Optional[Dict[str, str]]], Tuple[int, str]]
HttpPoster = Callable[
    [str, Dict[str, object], Optional[Dict[str, str]]], Tuple[int, str]
]


def real_http_get(
    url: str, headers: Optional[Dict[str, str]] = None
) -> Tuple[int, str]:
    hdrs: Dict[str, str] = {"accept": "application/json"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, headers=hdrs, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def real_http_post_json(
    url: str,
    body: Dict[str, object],
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, str]:
    data = json.dumps(body).encode("utf-8")
    hdrs: Dict[str, str] = {
        "content-type": "application/json",
        "accept": "application/json",
    }
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def validate_api_base(raw: Optional[str]) -> Tuple[Optional[str], List[dict]]:
    """Parse the loopback mock URL instead of prefix-matching (R16).

    Contract: scheme ``http``, host exactly ``127.0.0.1`` or
    ``localhost`` (any port), no userinfo, no query, no fragment, and
    an empty-or-``/`` path; the accepted value keeps the normalized
    trailing slash. Failures never echo the raw URL - the detail
    carries only the argument name and a reason code.
    """
    if not raw or not isinstance(raw, str):
        return None, [{"code": "api_base_missing",
                       "detail": {"argument": "--api-base"}}]
    text = raw.strip()
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError:
        return None, [{"code": "api_base_invalid",
                       "detail": {"argument": "--api-base",
                                  "reason": "unparsable"}}]
    if parts.scheme != "http":
        return None, [{"code": "api_base_invalid",
                       "detail": {"argument": "--api-base",
                                  "reason": "scheme_not_http"}}]
    if parts.username is not None or parts.password is not None:
        return None, [{"code": "api_base_invalid",
                       "detail": {"argument": "--api-base",
                                  "reason": "userinfo_not_allowed"}}]
    host = parts.hostname
    if host not in ("127.0.0.1", "localhost"):
        return None, [{"code": "api_base_not_loopback",
                       "detail": {"argument": "--api-base",
                                  "reason": "host_not_exact_loopback"}}]
    if parts.query:
        return None, [{"code": "api_base_invalid",
                       "detail": {"argument": "--api-base",
                                  "reason": "query_not_allowed"}}]
    if parts.fragment:
        return None, [{"code": "api_base_invalid",
                       "detail": {"argument": "--api-base",
                                  "reason": "fragment_not_allowed"}}]
    if parts.path not in ("", "/"):
        return None, [{"code": "api_base_invalid",
                       "detail": {"argument": "--api-base",
                                  "reason": "path_not_allowed"}}]
    return text if text.endswith("/") else text + "/", []

def _parse_json(body: str) -> Optional[object]:
    if not body.strip():
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


# ----------------------------------------------------------- layout ops ----

def _bounds_center(bounds: str) -> Optional[Tuple[int, int]]:
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", str(bounds))
    if not match:
        return None
    x1, y1, x2, y2 = (int(g) for g in match.groups())
    return (x1 + x2) // 2, (y1 + y2) // 2


def _walk_texts(node: object, acc: List[Tuple[str, str]]) -> None:
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
    acc: List[Tuple[str, str]] = []
    _walk_texts(parsed, acc)
    return acc


def find_text(
    texts: Sequence[Tuple[str, str]], needle: str
) -> Optional[Tuple[int, int]]:
    for text, bounds in texts:
        if needle in text:
            center = _bounds_center(bounds)
            if center is not None:
                return center
    return None


def find_text_exact(
    texts: Sequence[Tuple[str, str]], needle: str
) -> Optional[Tuple[int, int]]:
    for text, bounds in texts:
        if text == needle:
            center = _bounds_center(bounds)
            if center is not None:
                return center
    return find_text(texts, needle)


def find_url_input(
    texts: Sequence[Tuple[str, str]]
) -> Optional[Tuple[int, int, str]]:
    """The Settings base-URL TextInput: longest text that looks like a URL."""
    best: Optional[Tuple[str, str]] = None
    for text, bounds in texts:
        if "http://" in text or "https://" in text:
            if best is None or len(text) > len(best[0]):
                best = (text, bounds)
    if best is None:
        return None
    center = _bounds_center(best[1])
    if center is None:
        return None
    return center[0], center[1], best[0]


def find_placeholder_field(
    texts: Sequence[Tuple[str, str]], prefix: str
) -> Optional[Tuple[int, int]]:
    """An empty TextInput renders its placeholder as the node text."""
    for text, bounds in texts:
        if text.startswith(prefix):
            center = _bounds_center(bounds)
            if center is not None:
                return center
    return None


def _walk_typed(node: object, acc: List[Tuple[str, str, str]]) -> None:
    """Collect (type, text, bounds) triples from a dumped layout tree.

    Placeholder-based location proved wrong on the emulator (M14-89
    round 5): an EMPTY TextInput renders no placeholder text node at
    all, so fields must be located geometrically from their labels -
    which requires node types, not just texts.
    """
    if isinstance(node, dict):
        attrs = node.get("attributes")
        if isinstance(attrs, dict):
            ntype = attrs.get("type")
            text = attrs.get("text")
            bounds = attrs.get("bounds")
            if isinstance(ntype, str) and isinstance(bounds, str):
                acc.append(
                    (ntype, text if isinstance(text, str) else "", bounds))
        for value in node.values():
            _walk_typed(value, acc)
    elif isinstance(node, list):
        for value in node:
            _walk_typed(value, acc)


def layout_typed(parsed: object) -> List[Tuple[str, str, str]]:
    acc: List[Tuple[str, str, str]] = []
    _walk_typed(parsed, acc)
    return acc


def find_input_below_label(
    typed: Sequence[Tuple[str, str, str]], label_text: str
) -> Optional[Tuple[int, int]]:
    """First TextInput geometrically below the exact label Text node.

    The login fields sit directly under their 用户名/密码 labels
    (observed: label bottom -> input top ~42px), while empty inputs
    carry no text - so geometry is the only reliable locator.
    """
    label_center: Optional[Tuple[int, int]] = None
    for ntype, text, bounds in typed:
        if ntype == "Text" and text == label_text:
            center = _bounds_center(bounds)
            if center is not None:
                label_center = center
                break
    if label_center is None:
        return None
    best: Optional[Tuple[int, int]] = None
    best_dy: Optional[int] = None
    for ntype, _text, bounds in typed:
        if ntype != "TextInput":
            continue
        center = _bounds_center(bounds)
        if center is None:
            continue
        dy = center[1] - label_center[1]
        if 0 < dy <= 320 and abs(center[0] - label_center[0]) < 900:
            if best_dy is None or dy < best_dy:
                best = center
                best_dy = dy
    return best


def find_login_fields(
    typed: Sequence[Tuple[str, str, str]]
) -> Tuple[Optional[Tuple[int, int]], Optional[Tuple[int, int]]]:
    return (
        find_input_below_label(typed, USERNAME_LABEL_TEXT),
        find_input_below_label(typed, PASSWORD_LABEL_TEXT),
    )


def match_needles(
    texts: Sequence[Tuple[str, str]], needles: Sequence[str]
) -> Tuple[List[str], List[str]]:
    joined = "\n".join(t for t, _b in texts)
    matched = [n for n in needles if n in joined]
    missed = [n for n in needles if n not in joined]
    return matched, missed


# ------------------------------------------------------ window forensics ----

WINDOW_ROW_RE = re.compile(
    r"^(\S+)\s+0\s+(\d+)\s+(\d+)\s+(\d+)\s+\d+\s+\d+\s+(-?\d+)\s+0"
    r"\s+\[\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*\]")


def _window_table_rows(text: str) -> List[Dict[str, int]]:
    """Parse hidumper WindowManagerService window rows.

    Row shape (observed on emulator API 24): name, displayId, pid, winId,
    type, mode, flag, zord, orientation, [x y w h]. Only rows with a
    numeric pid are kept; system-surface filtering happens at the call
    site so this stays a pure parser.
    """
    rows: List[Dict[str, int]] = []
    for line in text.splitlines():
        match = WINDOW_ROW_RE.match(line)
        if match:
            rows.append({
                "name": match.group(1),
                "pid": int(match.group(2)),
                "win_id": int(match.group(3)),
                "zord": int(match.group(5)),
                "w": int(match.group(8)),
            })
    return rows


def _bundle_for_pid(ps_text: str, pid: int) -> Optional[str]:
    """Map a PID to its bundle name from ``ps -ef`` output.

    ps rows: UID PID PPID C STIME TTY TIME CMD - the last field is the
    bundle for app processes. Window names like app0/poc0 are not
    bundle names, so this is the authoritative mapping.
    """
    target = str(pid)
    for line in ps_text.splitlines():
        parts = line.split()
        if len(parts) >= 8 and parts[1] == target:
            return " ".join(parts[7:]) or None
    return None


# ------------------------------------------------------------- ui driver ----

class UiDriver:
    """Thin uitest driver over the injected command runner (M14-89).

    Same shape as backend_smoke.UiDriver; kept local so the M14-89 tool
    has no cross-tool UI coupling. Typed input (URL, username, password)
    goes to the child process argv only and is never recorded anywhere.
    """

    def __init__(
        self,
        runner: Callable[
            [Sequence[str], Path, Dict[str, str]], CommandResult
        ],
        program: str,
        target: str,
        bundle: str,
        ability: str,
        timeout: float,
        local_layout: Path,
    ) -> None:
        self.runner = runner
        self.root = Path(".")
        self.env: Dict[str, str] = {}
        self.program = program
        self.target = target
        self.bundle = bundle
        self.ability = ability
        self.timeout = timeout
        self.local_layout = local_layout
        self.log: List[dict] = []

    def _hdc(self, *args: str) -> CommandResult:
        argv = [self.program, "-t", self.target, *args]
        outcome = self.runner(argv, self.root, self.env)
        self.log.append({
            "action": args[1] if len(args) > 1 else args[0],
            "ok": outcome.returncode == 0,
        })
        return outcome

    def click(self, x: int, y: int) -> CommandResult:
        return self._hdc(
            "shell", "uitest", "uiInput", "click", str(x), str(y))

    def key_backspace(self) -> CommandResult:
        return self._hdc(
            "shell", "uitest", "uiInput", "keyEvent", BACKSPACE_KEY_EVENT)

    def key_backspaces(self, count: int) -> CommandResult:
        """One on-device keyEvent loop instead of N host round-trips (R8).

        Clearing a Settings/login field one keyEvent per hdc call opens
        a ~10s window in which the shared emulator's other occupant
        (net.uniterm.poc, never to be touched) re-takes the foreground,
        so every later keystroke lands in ITS inputs. A single device-
        side loop closes that exposure to ~1s.
        """
        if count <= 0:
            return CommandResult(
                argv=[], returncode=0, stdout="", stderr="",
                timed_out=False)
        script = (
            "n=" + str(count) + "; "
            "while [ $n -gt 0 ]; do "
            "uitest uiInput keyEvent " + BACKSPACE_KEY_EVENT + "; "
            "n=$((n-1)); done"
        )
        return self._hdc("shell", script)

    def type_url_field(
        self, x: int, y: int, text: str, clear_count: int
    ) -> CommandResult:
        """Click -> settle -> focused clear -> input -> settle in ONE hdc call.

        R9: R8 proved the batched on-device backspace loop alone is not
        enough - the three host round trips (click / clear / inputText)
        still opened a ~5s window in which the concurrent net.uniterm.poc
        driver re-took the foreground mid-action, so keystrokes landed
        in ITS inputs (r8b: settings_foreground_lost x3 at stage=verify,
        settings_input_mismatch x1). Collapsing the whole action into a
        single ``hdc shell`` script keeps every uitest call and the
        short settles inside one device-side process: no host round
        trip between focus acquisition and text entry. Quoting follows
        mksh rules (' -> '\''  inside single quotes); integer sleeps
        only (the device shell sleep has no sub-second support).
        """
        clear_count = max(clear_count, 0)
        quoted = "'" + text.replace("'", "'\''") + "'"
        script = (
            "uitest uiInput click " + str(x) + " " + str(y) + "; "
            "sleep 1; "
            "n=" + str(clear_count) + "; "
            "while [ $n -gt 0 ]; do "
            "uitest uiInput keyEvent " + BACKSPACE_KEY_EVENT + "; "
            "n=$((n-1)); done; "
            "sleep 1; "
            "uitest uiInput inputText " + str(x) + " " + str(y)
            + " " + quoted + "; "
            "sleep 1"
        )
        return self._hdc("shell", script)

    def key_back(self) -> CommandResult:
        """Press BACK to dismiss a raised IME after text entry."""
        return self._hdc(
            "shell", "uitest", "uiInput", "keyEvent", BACK_KEY_EVENT)

    def ensure_foreground(self) -> List[dict]:
        """Fail-closed foreground guard (M14-89 round 4).

        Parses the hidumper window table for the top full-screen app
        window, maps its PID to a bundle via ps -ef, and requires it to
        be ours. SCB*/softKeyboard* surfaces legally cover our app and
        are ignored. On a foreign top window (e.g. another app stealing
        the foreground) the guard recovers once by relaunching our
        ability, then reports the failure honestly.
        """
        failures: List[dict] = []
        for attempt in range(1, 3):
            text = self._hdc(
                "shell", "hidumper", "-s", "WindowManagerService",
                "-a", "'-a'").stdout or ""
            rows = _window_table_rows(text)
            focused = re.search(r"Focus window:\s*(\d+)", text)
            if not rows or not focused:
                failures.append({"code": "focus_window_unreadable",
                                 "detail": {"attempt": attempt}})
                self.restart_ability()
                time.sleep(HOME_SETTLE_SECONDS)
                continue
            win_id = int(focused.group(1))
            app_rows = [
                r for r in rows
                if r["name"] != "SCBKeyboardPanel"
                and not r["name"].startswith(SYSTEM_WINDOW_PREFIXES)
            ]
            full = [r for r in app_rows if r["w"] >= 1000]
            if not full:
                failures.append({"code": "app_window_missing",
                                 "detail": {"attempt": attempt,
                                            "app_window_rows":
                                                len(app_rows)}})
                self.restart_ability()
                time.sleep(HOME_SETTLE_SECONDS)
                continue
            top = max(full, key=lambda r: r["zord"])
            ps = self._hdc("shell", "ps", "-ef").stdout or ""
            bundle = _bundle_for_pid(ps, top["pid"])
            if bundle == self.bundle:
                return []
            failures.append({
                "code": "foreign_foreground_detected",
                "detail": {"attempt": attempt, "window": top["name"],
                           "focused_win": win_id,
                           "bundle_at_top": bundle},
            })
            # recover: relaunch our ability and let it settle
            self.restart_ability()
            time.sleep(HOME_SETTLE_SECONDS)
        return failures

    def swipe_up(self) -> CommandResult:
        """Scroll the current pane down (reveal content below the fold)."""
        return self._hdc(
            "shell", "uitest", "uiInput", "swipe",
            "660", "2000", "660", "900", "600")

    def dismiss_ime(self) -> CommandResult:
        """Press BACK to dismiss a raised IME after text entry."""
        return self.key_back()

    def input_text(self, x: int, y: int, text: str) -> CommandResult:
        return self._hdc(
            "shell", "uitest", "uiInput", "inputText",
            str(x), str(y), text)

    def restart_ability(self) -> None:
        self._hdc("shell", "aa", "force-stop", self.bundle)
        self._hdc("shell", "aa", "start", "-a", self.ability,
                  "-b", self.bundle)

    def window_snapshot(self) -> Dict[str, object]:
        """One-shot hidumper window-table snapshot (M14-89 R11).

        Diagnostic probe for the settings_foreground_lost
        (stage=verify) path: a SINGLE hdc invocation, no restart,
        no retry, no change to any success semantics. Returns the
        raw facts needed to compare our app window against a
        foreign one (poc0 / net.uniterm.poc) at the moment of the
        loss: every window row the table parser keeps (name/pid/
        win_id/zord/w - system SCB* rows included so zord ordering
        stays comparable), the Focus window id, and the hidumper
        exit status.
        """
        outcome = self._hdc(
            "shell", "hidumper", "-s", "WindowManagerService",
            "-a", "'-a'")
        text = outcome.stdout or ""
        rows = _window_table_rows(text)
        focused = re.search(r"Focus window:\s*(\d+)", text)
        return {
            "hidumper_returncode": outcome.returncode,
            "focused_win": (
                int(focused.group(1)) if focused else None),
            "rows": rows,
        }

    def dump(self) -> Tuple[Optional[object], List[dict]]:
        failures: List[dict] = []
        # The device may still be flushing the remote file when recv
        # starts; retry the dump+pull pair on a partial read.
        for attempt in range(3):
            self.local_layout.unlink(missing_ok=True)
            # Remove any stale remote dump FIRST: when a dumpLayout call
            # fails or times out, `file recv` must not silently succeed
            # against a leftover file from an earlier screen (M14-89
            # stale-layout failure mode observed on the emulator).
            self._hdc("shell", "rm", "-f", REMOTE_LAYOUT_PATH)
            self._hdc("shell", "uitest", "dumpLayout", "-p",
                      REMOTE_LAYOUT_PATH)
            time.sleep(UI_SETTLE_SECONDS if attempt else 0.5)
            self._hdc("file", "recv", REMOTE_LAYOUT_PATH,
                      str(self.local_layout))
            if not self.local_layout.is_file():
                failures.append({"code": "layout_pull_failed"})
                continue
            try:
                data = self.local_layout.read_bytes()
                parsed = json.loads(data.decode("utf-8", errors="replace"))
            except (OSError, ValueError, UnicodeError):
                failures.append({"code": "layout_invalid_json"})
                continue
            if not data:
                failures.append({"code": "layout_file_empty"})
                continue
            return parsed, []
        return None, failures

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


# --------------------------------------------------- host contract step ----

def _host_auth_contract(
    getter: HttpGetter,
    poster: HttpPoster,
    base: str,
    expect_auth_on: bool,
) -> Tuple[List[dict], List[dict]]:
    """Host-side auth-contract check against the loopback mock.

    Tokens/passwords are used in-process for the HTTP calls only; the
    records carry status codes and boolean facts, never values.
    """
    failures: List[dict] = []
    records: List[dict] = []

    def check(name: str, ok: bool, expected: str, actual: str) -> None:
        records.append({
            "name": name, "expected": expected,
            "actual": actual, "matched": ok,
        })
        if not ok:
            failures.append({
                "code": "contract_" + name + "_mismatch",
                "detail": {"expected": expected, "actual": actual},
            })

    status, body = getter(base + "api/v1/auth/status", None)
    payload = _parse_json(body)
    auth_on = isinstance(payload, dict) and payload.get("auth_enabled") is True
    check(
        "auth_status",
        status == 200 and auth_on == expect_auth_on,
        "200 auth_enabled=" + str(expect_auth_on),
        str(status) + " auth_enabled=" + str(auth_on),
    )
    if not auth_on:
        status, body = getter(base + "api/v1/system/privacy", None)
        check(
            "ungated_privacy",
            status == 200 and _parse_json(body) is not None,
            "200 json",
            str(status) + " json=" + str(_parse_json(body) is not None),
        )
        return records, failures

    # auth on: protected GET without a token -> honest 401
    status, _body = getter(base + "api/v1/system/privacy", None)
    check("gated_privacy_401", status == 401, "401", str(status))

    # wrong password -> honest 401
    status, _body = poster(
        base + "api/v1/auth/login",
        {"username": MOCK_LOGIN_USER, "password": MOCK_WRONG_PASS},
        None,
    )
    check("wrong_password_401", status == 401, "401", str(status))

    # right password -> 200 + access_token (value never recorded)
    status, body = poster(
        base + "api/v1/auth/login",
        {"username": MOCK_LOGIN_USER, "password": MOCK_LOGIN_PASS},
        None,
    )
    parsed = _parse_json(body)
    token = (
        parsed.get("access_token")
        if isinstance(parsed, dict) else None
    )
    token_usable = isinstance(token, str) and bool(token.strip()) and (
        not any(ch.isspace() for ch in token))
    token_type_ok = (
        isinstance(parsed, dict)
        and parsed.get("token_type") == "bearer"
    )
    check(
        "login_200_token",
        status == 200 and token_usable and token_type_ok,
        "200 usable bearer access_token",
        str(status) + " usable_token=" + str(token_usable)
        + " token_type_bearer=" + str(token_type_ok),
    )
    if not token_type_ok:
        # R16: token_type 契约不符时,后续 token 使用性检查无意义,
        # 直接返回(会话绝不该建立;fail-closed)。
        return records, failures

    if token_usable and isinstance(token, str):
        auth_hdr = {"authorization": "Bearer " + token}
        status, body = getter(base + "api/v1/auth/me", auth_hdr)
        parsed_me = _parse_json(body)
        me_ok = (
            status == 200
            and isinstance(parsed_me, dict)
            and parsed_me.get("username") == MOCK_LOGIN_USER
        )
        check(
            "me_200_with_token",
            me_ok,
            "200 username",
            str(status) + " json=" + str(isinstance(parsed_me, dict)),
        )
        # gated endpoint WITH token -> 200 (bearer actually unlocks)
        status, _body = getter(base + "api/v1/system/privacy", auth_hdr)
        check("gated_privacy_with_token", status == 200,
              "200", str(status))
        # wrong bearer -> still 401 (fail-closed)
        status, _body = getter(
            base + "api/v1/system/privacy",
            {"authorization": "Bearer not-the-token"},
        )
        check("gated_privacy_wrong_token", status == 401,
              "401", str(status))
    return records, failures


# ------------------------------------------------------- foreground steps ----

def _goto_settings(
    driver: UiDriver,
    scroll_for: Optional[str] = None,
) -> Tuple[bool, List[dict]]:
    """Navigate to the Settings tab (idempotent: click only if visible).

    Fail-closed foreground guard first: a foreign app at the top (e.g.
    an unrelated restored app stealing the foreground) must abort the
    step instead of letting later dumps read the wrong UI. When
    ``scroll_for`` is given, the pane is scrolled down (up to 4 pages)
    until a node containing that needle is visible - the auth login
    form sits below the fold in the Settings tab.
    """
    failures: List[dict] = list(driver.ensure_foreground())
    if failures:
        return False, failures
    parsed, dump_failures = driver.dump()
    if parsed is None:
        return False, dump_failures or [{"code": "layout_unreadable"}]
    texts = layout_texts(parsed)
    tab = find_text(texts, SETTINGS_TAB_TEXT)
    if tab is not None:
        driver.click(*tab)
        time.sleep(UI_SETTLE_SECONDS)
    if scroll_for is None:
        return True, []
    for _page in range(4):
        parsed, dump_failures = driver.dump()
        if parsed is None:
            return False, dump_failures or [
                {"code": "layout_unreadable"}]
        texts = layout_texts(parsed)
        if find_text(texts, scroll_for) is not None:
            return True, []
        driver.swipe_up()
        time.sleep(UI_SETTLE_SECONDS)
    return False, [{
        "code": "settings_scroll_target_not_found",
        "detail": {"needle": scroll_for},
    }]


def _owns_layout(texts: Sequence[Tuple[str, str]]) -> bool:
    """True when the dumped layout is OUR app (tab bar visible).

    A foreign app stealing the foreground mid-step (observed:
    net.uniterm.poc on the shared emulator) makes dumpLayout
    capture ITS screen; url-field reads and clicks must not run
    against such a layout (M14-89 round 6: Ubuntu doc URLs were
    matched as "the URL field"). The tab bar is rendered by our
    Index on every tab, so it is a stable ownership marker.
    """
    joined = "\n".join(t for t, _b in texts)
    return (SETTINGS_TAB_TEXT in joined
            or HOME_TAB_TEXT in joined)


def _drive_settings_url(
    driver: UiDriver,
    device_base: str,
) -> Tuple[bool, List[dict]]:
    """Type the device mock URL (derived from --api-base) into
    Settings and save.

    Input handling (proven on emulator, M14-89 round 4): inputText
    APPENDS to the field, so existing content is cleared first with a
    bounded backspace run derived from the observed content length; the
    typed result is verified from a fresh dump before saving; the IME
    raised by typing is dismissed with BACK before the Save button is
    clicked so the click cannot land on the keyboard.
    """
    failures: List[dict] = []
    for attempt in range(1, SETTINGS_MAX_ATTEMPTS + 1):
        ok, nav_failures = _goto_settings(driver)
        failures += nav_failures
        if not ok:
            continue
        parsed, dump_failures = driver.dump()
        if parsed is None:
            failures += dump_failures or [
                {"code": "settings_layout_unreadable"}]
            continue
        texts = layout_texts(parsed)
        if not _owns_layout(texts):
            failures.append({
                "code": "settings_foreground_lost",
                "detail": {"attempt": attempt},
            })
            # Recovery (round 7, documented alternative): the foreign
            # foreground (net.uniterm.poc driven by a concurrent agent
            # on the shared emulator) re-takes the top on every remote
            # keystroke, so settling alone never wins. Restart OUR
            # ability - while the other session is idle this reliably
            # brings us back on top - and let the fresh start settle
            # before the retry re-guards via ensure_foreground.
            driver.restart_ability()
            time.sleep(HOME_SETTLE_SECONDS)
            continue
        field = find_url_input(texts)
        if field is None:
            failures.append({
                "code": "settings_input_not_found",
                "detail": {"attempt": attempt},
            })
            time.sleep(UI_SETTLE_SECONDS)
            continue
        fx, fy, current = field
        # Single device-side invocation (R9): click -> settle -> focused
        # clear -> inputText -> settle all run inside ONE hdc shell round
        # trip. R8 proved the batched-backspace loop alone is not enough -
        # the three host round trips (click / clear / input) still opened
        # a ~5s window in which the concurrent net.uniterm.poc driver
        # re-took the foreground mid-action and the keystrokes landed in
        # ITS inputs (r8b: settings_foreground_lost x3 at stage=verify,
        # settings_input_mismatch x1). With no host round trip between
        # focus acquisition and text entry the whole action is atomic.
        driver.type_url_field(
            fx, fy, device_base, min(len(current) + 8, 64))
        time.sleep(UI_SETTLE_SECONDS)

        # Dismiss the IME BEFORE verification (R12). R11 proved at
        # all 5 verify failures that app0 keeps Focus (win 313) at
        # zord=102 while the softKeyboard1 window raised by this
        # typing sits at zord=104 and covers the bottom tab bar, so
        # the verify dump legitimately reads our own Settings page
        # but cannot find the tab-bar ownership markers and
        # _owns_layout misnames it "foreground lost" (poc0 stayed
        # zord=-1 throughout). One BACK here lowers the keyboard
        # before any dump; the old post-verify dismiss below was
        # removed so BACK is sent exactly once.
        driver.dismiss_ime()
        time.sleep(UI_SETTLE_SECONDS)

        # Pre-verify foreground guard (R10): with the input now atomic
        # (R9 removed settings_input_mismatch) the remaining loss point
        # is the verify dump itself - its 3 host hdc calls (rm /
        # dumpLayout / recv) open a multi-second window in which the
        # concurrent net.uniterm.poc driver on the shared emulator
        # re-takes the top and the dump lands on ITS screen (r9: 5/5
        # settings_foreground_lost at stage=verify). ensure_foreground
        # re-launches OUR ability to the top FIRST (2 hdc calls), so
        # the dump that follows reads our screen; _owns_layout still
        # catches any later cover honestly. Guard failures take the
        # existing restart/retry path (appended verbatim so the JSON
        # keeps its exact per-attempt signature).
        guard_failures = driver.ensure_foreground()
        if guard_failures:
            failures += guard_failures
            driver.restart_ability()
            time.sleep(HOME_SETTLE_SECONDS)
            continue

        verify, verify_failures = driver.dump()
        if verify is None:
            failures += verify_failures or [
                {"code": "settings_verify_layout_unreadable"}]
            continue
        vtexts = layout_texts(verify)
        if not _owns_layout(vtexts):
            # R11 mid-loss window probe: one immediate hidumper
            # snapshot taken here - BEFORE the restart below - so
            # the JSON evidence records whether poc0 (net.uniterm.
            # poc) was actually above our window at the moment
            # dumpLayout landed on the foreign screen, or invisible
            # to the zord-based top-window pick the guard uses.
            failures.append({
                "code": "settings_foreground_lost",
                "detail": {
                    "attempt": attempt, "stage": "verify",
                    "window_snapshot": driver.window_snapshot(),
                },
            })
            # Recovery (round 7, documented alternative): the foreign
            # foreground (net.uniterm.poc driven by a concurrent agent
            # on the shared emulator) re-takes the top on every remote
            # keystroke, so settling alone never wins. Restart OUR
            # ability - while the other session is idle this reliably
            # brings us back on top - and let the fresh start settle
            # before the retry re-guards via ensure_foreground.
            driver.restart_ability()
            time.sleep(HOME_SETTLE_SECONDS)
            continue
        vfield = find_url_input(vtexts)
        if vfield is None or device_base not in vfield[2]:
            failures.append({
                "code": "settings_input_mismatch",
                "detail": {"attempt": attempt},
            })
            continue
        # The IME was already dismissed BEFORE the verify dump
        # (R12); a second BACK here could travel into backward
        # navigation instead of closing a keyboard no longer open.
        save = find_text_exact(layout_texts(verify), SETTINGS_SAVE_TEXT)
        if save is None:
            failures.append({
                "code": "settings_save_button_not_found",
                "detail": {"attempt": attempt},
            })
            continue
        driver.click(*save)
        time.sleep(UI_SETTLE_SECONDS)
        final, _ = driver.dump()
        if final is None:
            failures.append(
                {"code": "settings_final_layout_unreadable"})
            continue
        final_texts = layout_texts(final)
        if not _owns_layout(final_texts):
            failures.append({
                "code": "settings_foreground_lost",
                "detail": {"attempt": attempt, "stage": "confirm"},
            })
            # Recovery (round 7, documented alternative): the foreign
            # foreground (net.uniterm.poc driven by a concurrent agent
            # on the shared emulator) re-takes the top on every remote
            # keystroke, so settling alone never wins. Restart OUR
            # ability - while the other session is idle this reliably
            # brings us back on top - and let the fresh start settle
            # before the retry re-guards via ensure_foreground.
            driver.restart_ability()
            time.sleep(HOME_SETTLE_SECONDS)
            continue
        joined = "\n".join(t for t, _b in final_texts)
        if SETTINGS_SAVED_PREFIX + device_base in joined:
            # Cold-restart so AuthPane re-queries auth/status with the
            # persisted URL (it only queries on aboutToAppear); a fresh
            # start also lands IME-free on the Home tab.
            driver.restart_ability()
            time.sleep(HOME_SETTLE_SECONDS)
            return True, failures
        failures.append({
            "code": "settings_save_not_confirmed",
            "detail": {"attempt": attempt},
        })
    return False, failures


def _layout_record(
    driver: UiDriver, texts: List[Tuple[str, str]], **extra: object
) -> Optional[dict]:
    digest = driver.digest()
    if digest is None:
        return None
    record: Dict[str, object] = {
        **digest, "node_text_count": len(texts)}
    record.update(extra)
    return record


def _assert_no_secret_leak(
    texts: Sequence[Tuple[str, str]]
) -> List[dict]:
    """Fail closed if any credential/token value appears in the layout."""
    failures: List[dict] = []
    joined = "\n".join(t for t, _b in texts)
    for label, secret in (
        ("password", MOCK_LOGIN_PASS),
        ("wrong_password", MOCK_WRONG_PASS),
        ("token", MOCK_TOKEN_HINT),
    ):
        if secret in joined:
            failures.append({
                "code": "secret_leaked_in_layout",
                "detail": {"kind": label},
            })
    return failures


def _step_auth_off_local(
    driver: UiDriver,
) -> Tuple[Optional[dict], List[dict]]:
    failures: List[dict] = []
    ok, nav_failures = _goto_settings(driver)
    failures += nav_failures
    if not ok:
        return None, failures
    parsed, dump_failures = driver.dump()
    if parsed is None:
        return None, dump_failures or [{"code": "layout_unreadable"}]
    texts = layout_texts(parsed)
    _matched, missed = match_needles(
        texts, [AUTH_PANE_TITLE, LOCAL_MODE_TEXT])
    if missed:
        failures.append({
            "code": "auth_off_texts_missing",
            "detail": {"missed": missed},
        })
    # auth-off must NOT render login controls: the 用户名/密码 labels
    # and their input rows are only rendered when auth_enabled=true.
    # (Placeholder text never appears for empty inputs on this SDK, so
    # the check rides on the label Texts instead.)
    typed = layout_typed(parsed)
    user_f, pass_f = find_login_fields(typed)
    if user_f is not None or pass_f is not None:
        failures.append({"code": "auth_off_login_controls_rendered"})
    failures += _assert_no_secret_leak(texts)
    return _layout_record(driver, texts), failures


def _step_home_401_unauth(
    driver: UiDriver,
) -> Tuple[Optional[dict], List[dict]]:
    """Auth-on, not yet logged in: Home must show honest 401, no data."""
    failures: List[dict] = []
    home_tab = None
    parsed, dump_failures = driver.dump()
    if parsed is not None:
        home_tab = find_text(layout_texts(parsed), HOME_TAB_TEXT)
    if home_tab is None:
        failures.append({"code": "home_tab_not_found"})
        return None, failures
    driver.click(*home_tab)
    time.sleep(HOME_SETTLE_SECONDS)
    parsed, dump_failures = driver.dump()
    if parsed is None:
        return None, dump_failures or [{"code": "home_layout_unreadable"}]
    texts = layout_texts(parsed)
    joined = "\n".join(t for t, _b in texts)
    if HOME_AUTH_ON_TEXT not in joined:
        failures.append({"code": "home_auth_on_text_missing"})
    if HOME_401_TEXT not in joined:
        failures.append({"code": "pre_login_401_missing"})
    if HOME_PRIVACY_KV in joined:
        failures.append({"code": "pre_login_privacy_rendered"})
    failures += _assert_no_secret_leak(texts)
    return _layout_record(driver, texts), failures


def _fill_login_form(
    driver: UiDriver,
    typed: List[Tuple[str, str, str]],
    password: str,
) -> Tuple[bool, List[dict]]:
    """Fill username + password inputs located geometrically.

    Locator truth (proven by the round-5 failure layout): an EMPTY
    TextInput renders NO placeholder text node, but the 用户名/密码
    label Texts sit ~42px above their inputs - so fields are found as
    "first TextInput directly below the exact label". Placeholder
    matching is kept only as a fallback for filled fields.

    AuthPane also only queries auth/status on mount (aboutToAppear),
    so the form may still be LOADING on first visit; each retry first
    waits for the pane to settle (AUTH_PANE_TITLE visible, no loading
    text), scrolling if needed to bring it into view.
    """
    failures: List[dict] = []
    for _attempt in range(4):
        user_field, pass_field = find_login_fields(typed)
        if user_field is not None and pass_field is not None:
            break
        joined = "\n".join(t for _t, t, _b in typed)
        if AUTH_PANE_TITLE not in joined:
            # pane not mounted/scrolled into view yet
            driver.swipe_up()
            time.sleep(UI_SETTLE_SECONDS)
        elif QUERY_FAILED_TEXT in joined:
            # auth/status query failed (e.g. a transient network error
            # at mount): the pane stays in its failed state and renders
            # a 重试 button instead of the form. Click it once and wait
            # out the retry round-trip instead of blind-scrolling away
            # (M14-89: blind swiping never recovers this state).
            retry = find_text_exact(
                [(t, b) for _ty, t, b in typed], AUTH_RETRY_BUTTON_TEXT)
            if retry is not None:
                driver.click(*retry)
            time.sleep(LONG_SETTLE_SECONDS)
        elif QUERY_LOADING_TEXT in joined:
            # auth/status round-trip still in flight: wait
            time.sleep(LONG_SETTLE_SECONDS)
        else:
            driver.swipe_up()
            time.sleep(UI_SETTLE_SECONDS)
        parsed, dump_failures = driver.dump()
        if parsed is None:
            failures += dump_failures or [
                {"code": "login_scroll_layout_unreadable"}]
            return False, failures
        typed = layout_typed(parsed)
    else:
        failures.append({
            "code": "login_fields_not_found",
            "detail": {
                "username_field": user_field is not None,
                "password_field": pass_field is not None,
            },
        })
        return False, failures
    # a field still holding a previous username is cleared first:
    # inputText APPENDS, so stale content would corrupt the entry.
    stale = find_placeholder_field(
        [(t, b) for _ty, t, b in typed], USERNAME_PLACEHOLDER_PREFIX)
    if stale is None and user_field is not None:
        filled = find_text(
            [(t, b) for _ty, t, b in typed], MOCK_LOGIN_USER)
        if filled is not None:
            driver.click(*user_field)
            time.sleep(UI_SETTLE_SECONDS)
            # batched device-side clear (R8, same rationale as Settings)
            driver.key_backspaces(len(MOCK_LOGIN_USER) + 4)
            time.sleep(UI_SETTLE_SECONDS)
    driver.click(*user_field)
    time.sleep(UI_SETTLE_SECONDS)
    driver.input_text(user_field[0], user_field[1], MOCK_LOGIN_USER)
    time.sleep(UI_SETTLE_SECONDS)
    driver.click(*pass_field)
    time.sleep(UI_SETTLE_SECONDS)
    driver.input_text(pass_field[0], pass_field[1], password)
    time.sleep(UI_SETTLE_SECONDS)
    # dismiss the IME before pressing the login button
    driver.dismiss_ime()
    time.sleep(UI_SETTLE_SECONDS)
    return True, failures


def _submit_login(
    driver: UiDriver,
    password: str,
) -> Tuple[Optional[List[Tuple[str, str]]], List[dict]]:
    """Locate the form fresh, fill it, press 登录, return the post-dump.

    The login button is re-located from a post-fill dump: the IME
    dismissal can reflow the layout, and stale coordinates would click
    the wrong node. The post-login dump additionally waits out the
    in-flight auth round-trip (login + /me) before asserting.
    """
    failures: List[dict] = []
    parsed, dump_failures = driver.dump()
    if parsed is None:
        return None, dump_failures or [{"code": "layout_unreadable"}]
    typed = layout_typed(parsed)
    filled, fill_failures = _fill_login_form(driver, typed, password)
    failures += fill_failures
    if not filled:
        return None, failures
    # re-locate the button from a fresh dump (layout may reflow after IME)
    post_fill, dump_failures = driver.dump()
    if post_fill is None:
        return None, dump_failures or [{"code": "layout_unreadable"}]
    login_btn = find_text_exact(layout_texts(post_fill), LOGIN_BUTTON_TEXT)
    if login_btn is None:
        failures.append({"code": "login_button_not_found"})
        return None, failures
    driver.click(*login_btn)
    # login => /me validation => vault write: wait out the round-trip
    time.sleep(LONG_SETTLE_SECONDS)
    parsed, dump_failures = driver.dump()
    if parsed is None:
        return None, dump_failures or [{"code": "layout_unreadable"}]
    joined = "\n".join(t for t, _b in layout_texts(parsed))
    if LOGGING_IN_TEXT in joined:
        # still busy: one more bounded wait, then assert on a fresh dump
        time.sleep(LONG_SETTLE_SECONDS)
        parsed, dump_failures = driver.dump()
        if parsed is None:
            return None, dump_failures or [
                {"code": "layout_unreadable"}]
    return layout_texts(parsed), failures


def _step_wrong_password(
    driver: UiDriver,
) -> Tuple[Optional[dict], List[dict]]:
    failures: List[dict] = []
    ok, nav_failures = _goto_settings(driver)
    failures += nav_failures
    if not ok:
        return None, failures
    texts, submit_failures = _submit_login(driver, MOCK_WRONG_PASS)
    failures += submit_failures
    if texts is None:
        return None, failures
    _matched, missed = match_needles(texts, [LOGIN_FAIL_401_TEXT])
    if missed:
        failures.append({
            "code": "wrong_password_error_missing",
            "detail": {"missed": missed},
        })
    # no half session: the current-user block must NOT be rendered
    _m2, user_block = match_needles(texts, [CURRENT_USER_TEXT])
    if not user_block:
        failures.append({"code": "half_session_rendered"})
    failures += _assert_no_secret_leak(texts)
    return _layout_record(driver, texts), failures


def _poll_home_zones_settled(
    driver: "UiDriver",
) -> Tuple[Optional[List[Tuple[str, str]]], List[dict]]:
    """Bounded readiness poll: dump Home until no zone still shows 加载中….

    Returns (texts, failures) where:
      * texts is the post-settle layout text list, or None on timeout;
      * failures is an empty list on success or a single
        {"code": "home_zones_still_loading"} on timeout.
    Terminal error states (zone ERROR, 401, 重试 buttons, etc.)
    are NOT treated as "still loading" - polling stops immediately
    when HOME_ZONE_LOADING_TEXT is absent, so an error state resolves
    in a single dump. The deadline guards against a perpetual spin.
    """
    import time as _t
    deadline = _t.monotonic() + HOME_ZONE_LOADING_DEADLINE_SECONDS
    interval = HOME_ZONE_LOADING_POLL_INTERVAL_SECONDS
    while True:
        parsed, dump_failures = driver.dump()
        if parsed is None:
            # Dump is unreadable: report the actual failures now -
            # waiting for the deadline would mislabel this as
            # "zones still loading" (M14-99 B2).
            return None, dump_failures or [{"code": "layout_unreadable"}]
        texts = layout_texts(parsed)
        joined = "\n".join(t for t, _b in texts)
        if HOME_ZONE_LOADING_TEXT not in joined:
            return texts, []
        if _t.monotonic() >= deadline:
            return None, [{"code": "home_zones_still_loading"}]
        _t.sleep(interval)


def _refresh_home_and_dump(
    driver: UiDriver,
) -> Tuple[Optional[List[Tuple[str, str]]], List[dict]]:
    """Go to Home, press 整体刷新, return the settled post-refresh dump.

    Home keeps last-known data in state; without an explicit refresh
    the zones would show stale content - asserting on that would prove
    nothing about the live session. The refresh button re-locates from
    a fresh dump (the IME may have reflowed the layout).
    """
    failures: List[dict] = []
    parsed, dump_failures = driver.dump()
    if parsed is None:
        return None, dump_failures or [{"code": "layout_unreadable"}]
    home_tab = find_text(layout_texts(parsed), HOME_TAB_TEXT)
    if home_tab is None:
        failures.append({"code": "home_tab_not_found"})
        return None, failures
    driver.click(*home_tab)
    time.sleep(HOME_SETTLE_SECONDS)
    parsed, dump_failures = driver.dump()
    if parsed is None:
        return None, dump_failures or [{"code": "layout_unreadable"}]
    refresh_btn = find_text_exact(
        layout_texts(parsed), HOME_REFRESH_BUTTON_TEXT)
    if refresh_btn is None:
        failures.append({"code": "home_refresh_button_not_found"})
        return None, failures
    driver.click(*refresh_btn)
    # Six zones re-fetch in parallel; wait for all to leave LOADING
    # state via bounded readiness polling (M14-99 B1 - replaces the
    # old fixed-sleep + single "正在" check that let post_login_
    # privacy_missing fire while a zone still showed 加载中…).
    settled_texts, poll_failures = _poll_home_zones_settled(driver)
    if poll_failures:
        failures.extend(poll_failures)
    if settled_texts is None:
        return None, failures
    return settled_texts, failures


def _step_login_and_refresh(
    driver: UiDriver,
) -> Tuple[Optional[dict], List[dict]]:
    failures: List[dict] = []
    ok, nav_failures = _goto_settings(driver)
    failures += nav_failures
    if not ok:
        return None, failures
    parsed, dump_failures = driver.dump()
    if parsed is None:
        return None, dump_failures or [{"code": "layout_unreadable"}]
    texts, submit_failures = _submit_login(driver, MOCK_LOGIN_PASS)
    failures += submit_failures
    if texts is None:
        return None, failures
    _matched, missed = match_needles(
        texts, [LOGIN_SUCCESS_TEXT, CURRENT_USER_TEXT, MOCK_LOGIN_USER])
    if missed:
        failures.append({
            "code": "login_success_texts_missing",
            "detail": {"missed": missed},
        })
    failures += _assert_no_secret_leak(texts)
    record = _layout_record(driver, texts)
    if record is None:
        return None, failures
    # prove the bearer actually unlocks protected reads on Home
    home_texts, home_failures = _refresh_home_and_dump(driver)
    failures += home_failures
    if home_texts is not None:
        joined = "\n".join(t for t, _b in home_texts)
        if HOME_AUTH_ON_TEXT not in joined:
            failures.append({"code": "home_auth_on_text_missing"})
        if HOME_PRIVACY_KV not in joined:
            failures.append({"code": "post_login_privacy_missing"})
        if HOME_401_TEXT in joined:
            failures.append({"code": "post_login_401_rendered"})
        failures += _assert_no_secret_leak(home_texts)
    return record, failures


def _step_cold_restart(
    driver: UiDriver,
) -> Tuple[Optional[dict], List[dict]]:
    """Cold-restart the ability and prove the vault session restored.

    AuthPane (and thus restoreSession) only runs when the Settings tab
    is mounted, so the honest sequence is: restart -> Home (no session
    yet, protected zones fail with 401 on refresh) -> visit Settings
    (AuthPane restores the vault session via /me) -> back to Home and
    refresh again: now the protected zones must render data.
    """
    failures: List[dict] = []
    driver.restart_ability()
    time.sleep(HOME_SETTLE_SECONDS)
    # 1) Settings first: AuthPane mounts and restores the vault session
    ok, nav_failures = _goto_settings(driver)
    failures += nav_failures
    if not ok:
        return None, failures
    parsed, dump_failures = driver.dump()
    if parsed is None:
        return None, dump_failures or [{"code": "layout_unreadable"}]
    # the restore round-trip (vault read + /me) may still be in flight
    texts = layout_texts(parsed)
    for _wait in range(3):
        joined = "\n".join(t for t, _b in texts)
        if (QUERY_LOADING_TEXT in joined
                or LOGGING_IN_TEXT in joined
                or RESTORED_TEXT in joined):
            break
        time.sleep(LONG_SETTLE_SECONDS)
        parsed, dump_failures = driver.dump()
        if parsed is None:
            return None, dump_failures or [
                {"code": "layout_unreadable"}]
        texts = layout_texts(parsed)
    _matched2, missed2 = match_needles(
        texts, [RESTORED_TEXT, CURRENT_USER_TEXT, MOCK_LOGIN_USER])
    if missed2:
        failures.append({
            "code": "session_restore_texts_missing",
            "detail": {"missed": missed2},
        })
    failures += _assert_no_secret_leak(texts)
    # 2) Home + explicit refresh: the restored bearer must unlock reads
    home_texts, home_failures = _refresh_home_and_dump(driver)
    failures += home_failures
    if home_texts is not None:
        joined = "\n".join(t for t, _b in home_texts)
        if HOME_AUTH_ON_TEXT not in joined:
            failures.append({"code": "home_auth_on_text_missing"})
        if HOME_PRIVACY_KV not in joined:
            failures.append({"code": "home_privacy_missing_after_restore"})
        if HOME_401_TEXT in joined:
            failures.append({"code": "home_privacy_401_after_restore"})
        failures += _assert_no_secret_leak(home_texts)
    return _layout_record(
        driver, texts, home_asserted=True), failures


def _step_logout(
    driver: UiDriver,
) -> Tuple[Optional[dict], List[dict]]:
    failures: List[dict] = []
    # cold_restart ends on the Home tab, but the logout button lives
    # in the auth form below the Settings fold: navigate for real
    # (foreground guard -> Settings tab -> scroll until the button is
    # visible) instead of assuming the layout is already there.
    ok, nav_failures = _goto_settings(
        driver, scroll_for=LOGOUT_BUTTON_TEXT)
    failures += nav_failures
    if not ok:
        return None, failures
    parsed, dump_failures = driver.dump()
    if parsed is None:
        return None, dump_failures or [{"code": "layout_unreadable"}]
    texts = layout_texts(parsed)
    logout_btn = find_text_exact(texts, LOGOUT_BUTTON_TEXT)
    if logout_btn is None:
        failures.append({"code": "logout_button_not_found"})
        return None, failures
    driver.click(*logout_btn)
    time.sleep(LONG_SETTLE_SECONDS)
    parsed, dump_failures = driver.dump()
    if parsed is None:
        return None, dump_failures or [{"code": "layout_unreadable"}]
    texts = layout_texts(parsed)
    _matched, missed = match_needles(texts, [LOGGED_OUT_TEXT])
    if missed:
        failures.append({
            "code": "logout_text_missing",
            "detail": {"missed": missed},
        })
    _m2, user_block = match_needles(texts, [CURRENT_USER_TEXT])
    if not user_block:
        failures.append({"code": "user_state_not_cleared"})
    failures += _assert_no_secret_leak(texts)
    record = _layout_record(driver, texts)
    if record is None:
        return None, failures
    return record, failures


def _step_logout_401_fallback(
    driver: UiDriver,
) -> Tuple[Optional[dict], List[dict]]:
    """After logout, Home protected zones must fall back to honest 401.

    Home keeps stale data in state after a token drop, so the zone
    must be refreshed explicitly (整体刷新) before the 401 assertion
    means anything.
    """
    failures: List[dict] = []
    home_texts, home_failures = _refresh_home_and_dump(driver)
    failures += home_failures
    if home_texts is None:
        return None, failures
    joined = "\n".join(t for t, _b in home_texts)
    if HOME_401_TEXT not in joined:
        failures.append({"code": "post_logout_401_missing"})
    if HOME_PRIVACY_KV in joined:
        failures.append({"code": "post_logout_privacy_still_rendered"})
    failures += _assert_no_secret_leak(home_texts)
    return _layout_record(driver, home_texts), failures


def _not_run_step(name: str, phase: str, reason: Optional[str]) -> dict:
    step: dict = {"name": name, "phase": phase, "status": STEP_STATUS_NOT_RUN}
    if reason:
        step["reason"] = reason
    return step


# ------------------------------------------------------------ orchestrator ----

def run_auth_smoke(
    repo_root: Path,
    *,
    target: Optional[str] = None,
    hap: Optional[str] = None,
    bundle: Optional[str] = None,
    ability: str = DEFAULT_ABILITY,
    hdc: str = DEFAULT_HDC,
    api_base: Optional[str] = DEFAULT_API_BASE,
    expect_auth: str = "off",
    confirm_mutation: bool = False,
    known_targets: Optional[Sequence[str]] = None,
    evidence_dir: Optional[Path] = None,
    timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    runner: Optional[Callable] = None,
    http_get: Optional[HttpGetter] = None,
    http_post: Optional[HttpPoster] = None,
    tool_resolver: Optional[Callable] = None,
    target_resolver: Optional[Callable] = None,
    bundle_resolver: Optional[Callable] = None,
) -> Tuple[dict, int]:
    root = Path(repo_root).resolve()
    request_failures: List[dict] = []
    toolchain_failures: List[dict] = []
    warnings: List[dict] = []
    steps: List[dict] = []
    contract_records: List[dict] = []
    layout_records: Dict[str, Optional[dict]] = {}
    resolved_target: Optional[str] = None
    hap_record: Optional[dict] = None
    bundle_name: Optional[str] = None
    tool: Optional[HdcTool] = None

    getter = http_get or real_http_get
    poster = http_post or real_http_post_json

    expect_auth_on = expect_auth == "on"

    # 1) Request validation (fail closed before anything runs).
    resolved_target, target_failures = (target_resolver or resolve_target)(
        target, known_targets)
    request_failures += target_failures
    base, base_failures = validate_api_base(api_base)
    request_failures += base_failures
    device_base: Optional[str] = None
    if base is not None:
        try:
            device_base = device_api_base(base)
        except ValueError:
            request_failures.append({
                "code": "device_url_derivation_failed",
                "detail": {"argument": "--api-base"},
            })
    if not target_failures:
        hap_record, hap_failures = _inspect_hap(root, hap)
        request_failures += hap_failures
    if not request_failures:
        bundle_name, bundle_failures = (bundle_resolver or resolve_bundle)(
            root, bundle)
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
            "code": "mutation_not_confirmed",
            "detail": {
                "confirmation_flag": MUTATION_CONFIRMATION_FLAG,
                "not_run": [name for name, _p in STEP_ORDER],
            },
        })
        steps = [
            _not_run_step(name, phase, "mutation_not_confirmed")
            for name, phase in STEP_ORDER
        ]
    else:
        tool, toolchain_failures = (tool_resolver or resolve_hdc_tool)(hdc)
        if toolchain_failures:
            steps = [
                _not_run_step(name, phase, "toolchain_unavailable")
                for name, phase in STEP_ORDER
            ]

    toolchain_record = {
        "probed": bool(confirm_mutation and not request_failures),
        "program_name": Path(hdc).name or DEFAULT_HDC,
        "resolved": tool is not None,
        "source": getattr(tool, "source", None),
    }

    commands_executed = 0
    mutation_performed = False
    cleanup_attempted = False

    if confirm_mutation and not request_failures and not toolchain_failures:
        run = runner or make_subprocess_runner(timeout_seconds)
        env = _child_env()
        program = str(tool.path)
        target_raw = resolved_target.raw if resolved_target else ""
        hap_path = (
            (root / hap_record["relpath"]) if hap_record else None)

        driver = UiDriver(
            runner=run,
            program=program,
            target=target_raw,
            bundle=bundle_name or "",
            ability=ability,
            timeout=timeout_seconds,
            local_layout=root / ".verify" / "artifacts" /
            "m14-89-harmony-auth-session" / "auth_smoke_layout.json",
        )
        driver.env = env
        driver.local_layout.parent.mkdir(parents=True, exist_ok=True)

        previous_failed = False
        for name, phase in STEP_ORDER:
            # auth-off flow stops after the local-mode step; the auth-on
            # flow skips the auth-off-only step (its assertions - no
            # login controls, local-mode text - are auth-off facts).
            if (
                not expect_auth_on
                and name in (STEP_HOME_401_UNAUTH, STEP_WRONG_PASSWORD,
                             STEP_LOGIN_AND_REFRESH, STEP_COLD_RESTART,
                             STEP_LOGOUT)
            ):
                steps.append(_not_run_step(name, phase, REASON_AUTH_PHASE_SKIP))
                continue
            if expect_auth_on and name == STEP_AUTH_OFF_LOCAL:
                steps.append(_not_run_step(name, phase, REASON_AUTH_PHASE_SKIP))
                continue
            if name == STEP_UNINSTALL:
                cleanup_attempted = True
                if not mutation_performed:
                    steps.append(_not_run_step(
                        name, phase, REASON_INSTALL_NOT_SUCCESSFUL))
                    continue
            elif previous_failed:
                steps.append(_not_run_step(
                    name, phase, REASON_PREVIOUS_STEP_FAILED))
                continue

            step_failures: List[dict] = []
            record: Optional[dict] = None
            status = STEP_STATUS_OK

            if name == STEP_HOST_AUTH_CONTRACT:
                contract_records, step_failures = _host_auth_contract(
                    getter, poster, base or "", expect_auth_on)
            elif name == STEP_INSTALL:
                assert hap_path is not None
                outcome = run(
                    [program, "-t", target_raw, "install",
                     str(hap_path)],
                    root, env)
                commands_executed += 1
                if outcome.returncode != 0:
                    step_failures.append({"code": "install_failed"})
                else:
                    mutation_performed = True
            elif name == STEP_START:
                outcome = run(
                    [program, "-t", target_raw, "shell", "aa",
                     "start", "-a", ability, "-b", bundle_name or ""],
                    root, env)
                commands_executed += 1
                time.sleep(HOME_SETTLE_SECONDS)
                if outcome.returncode != 0:
                    step_failures.append({"code": "ability_start_failed"})
                else:
                    step_failures += driver.ensure_foreground()
            elif name == STEP_SETTINGS_UI:
                ok, step_failures = _drive_settings_url(driver, device_base)
                if not ok and not step_failures:
                    step_failures.append(
                        {"code": "settings_drive_failed"})
            elif name == STEP_AUTH_OFF_LOCAL:
                record, step_failures = _step_auth_off_local(driver)
            elif name == STEP_WRONG_PASSWORD:
                record, step_failures = _step_wrong_password(driver)
            elif name == STEP_LOGIN_AND_REFRESH:
                record, step_failures = _step_login_and_refresh(driver)
            elif name == STEP_COLD_RESTART:
                record, step_failures = _step_cold_restart(driver)
            elif name == STEP_LOGOUT:
                record, step_failures = _step_logout(driver)
                if not step_failures:
                    fb_record, fb_failures = _step_logout_401_fallback(driver)
                    step_failures += fb_failures
                    layout_records["post_logout_home"] = fb_record
            elif name == STEP_BACKGROUND:
                outcome = run(
                    [program, "-t", target_raw, "shell", "aa",
                     "force-stop", bundle_name or ""],
                    root, env)
                commands_executed += 1
                if outcome.returncode != 0:
                    step_failures.append({"code": "force_stop_failed"})
            elif name == STEP_UNINSTALL:
                outcome = run(
                    [program, "-t", target_raw, "uninstall",
                     bundle_name or ""],
                    root, env)
                commands_executed += 1
                if outcome.returncode != 0:
                    step_failures.append({"code": "uninstall_failed"})

            if step_failures:
                status = STEP_STATUS_FAILURE
                previous_failed = True
            step_entry: Dict[str, object] = {
                "name": name, "phase": phase, "status": status}
            if record is not None:
                layout_records[name] = record
            if step_failures:
                step_entry["failures"] = step_failures
            steps.append(step_entry)

    ok_steps = [s for s in steps if s.get("status") == STEP_STATUS_OK]
    failed_steps = [s for s in steps if s.get("status") == STEP_STATUS_FAILURE]
    not_run_steps = [
        s for s in steps if s.get("status") == STEP_STATUS_NOT_RUN]

    result: Dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "expect_auth": expect_auth,
        "api_base": base,
        "device_api_base_typed": device_base,
        "bundle": bundle_name,
        "ability": ability,
        "hap": hap_record,
        "toolchain": toolchain_record,
        "commands_executed": commands_executed,
        "mutation_performed": mutation_performed,
        "cleanup_attempted": cleanup_attempted,
        "warnings": warnings,
        "request_failures": request_failures,
        "toolchain_failures": toolchain_failures,
        "contract": contract_records,
        "steps": steps,
        "layouts": layout_records,
        "driver_log": None,
        "summary": {
            "total": len(steps),
            "ok": len(ok_steps),
            "failure": len(failed_steps),
            "not_run": len(not_run_steps),
        },
    }

    if evidence is not None:
        try:
            out = evidence / (
                "auth_smoke_" + ("on" if expect_auth_on else "off")
                + ".json")
            out.write_text(
                json.dumps(result, indent=2, ensure_ascii=False,
                           sort_keys=True) + "\n",
                encoding="utf-8")
            result["evidence_file"] = out.name
        except OSError:
            result["evidence_error"] = "evidence_write_failed"

    exit_code = EXIT_FAILURE if (
        request_failures or toolchain_failures or failed_steps
    ) else EXIT_OK
    if request_failures:
        exit_code = EXIT_BLOCKED
    return result, exit_code


# ------------------------------------------------------------------ cli ----

def render_json(result: dict) -> str:
    return json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True)


def render_summary(result: dict) -> str:
    summary = result.get("summary", {})
    lines = [
        "auth smoke: " + result.get("expect_auth", "?"),
        "  steps ok/failure/not_run: {}/{}".format(
            summary.get("ok", 0), summary.get("failure", 0))
        + "/{}".format(summary.get("not_run", 0)),
    ]
    for failure in result.get("request_failures", []):
        lines.append("  request failure: " + failure.get("code", "?"))
    for failure in result.get("toolchain_failures", []):
        lines.append("  toolchain failure: " + failure.get("code", "?"))
    for step in result.get("steps", []):
        if step.get("status") != "ok":
            lines.append(
                "  step {}: {} {}".format(
                    step.get("name", "?"), step.get("status", "?"),
                    step.get("reason", "")))
            for failure in step.get("failures", []) or []:
                lines.append(
                    "    failure: " + failure.get("code", "?"))
    return "\n".join(lines)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="HarmonyOS authenticated-session UI smoke (M14-89)")
    parser.add_argument("--repo-root", default=str(DEFAULT_REPO_ROOT))
    parser.add_argument("--target", required=True,
                        help="one explicit hdc target (e.g. 127.0.0.1:5555)")
    parser.add_argument("--hap",
                        default="apps/harmony/entry/build/default/outputs/"
                                "default/entry-default-signed.hap")
    parser.add_argument("--bundle", default=None)
    parser.add_argument("--ability", default=DEFAULT_ABILITY)
    parser.add_argument("--hdc", default=DEFAULT_HDC)
    parser.add_argument("--api-base", default=DEFAULT_API_BASE,
                        help="loopback mock URL (must already be running)")
    parser.add_argument("--expect-auth", choices=["off", "on"],
                        default="off",
                        help="mock auth phase the server was started with")
    parser.add_argument(MUTATION_CONFIRMATION_FLAG, action="store_true",
                        help="opt in to install/UI mutation on the device")
    parser.add_argument("--evidence-dir", default=None)
    parser.add_argument("--timeout-seconds", type=float,
                        default=DEFAULT_COMMAND_TIMEOUT_SECONDS)
    parser.add_argument("--json", action="store_true",
                        help="print the full JSON result")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    result, exit_code = run_auth_smoke(
        Path(args.repo_root),
        target=args.target,
        hap=args.hap,
        bundle=args.bundle,
        ability=args.ability,
        hdc=args.hdc,
        api_base=args.api_base,
        expect_auth=args.expect_auth,
        confirm_mutation=getattr(args, MUTATION_CONFIRMATION_FLAG
                                 .lstrip("-").replace("-", "_")),
        evidence_dir=Path(args.evidence_dir) if args.evidence_dir else None,
        timeout_seconds=args.timeout_seconds,
    )
    if args.json:
        print(render_json(result))
    else:
        print(render_summary(result))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

