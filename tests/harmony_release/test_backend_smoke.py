"""Focused tests for the M14-84 real-API backend smoke wrapper.

All tests use injected fakes: no device is touched, no socket is opened, no
HTTP request leaves the process, no HAP is installed. The home_view step is
a cold restart (aa force-stop + aa start) because the IME panel covers the
bottom tab bar after Settings typing; fakes replay that command sequence. The fakes replay the
honest answers the real backend gave during reconnaissance (200/200/200,
401/401/401) or deliberately lie, and the wrapper must fail closed on every
lie.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from tools.harmony_release.backend_smoke import (
    EXIT_BLOCKED,
    EXIT_FAILURE,
    EXIT_OK,
    SETTINGS_MAX_ATTEMPTS,
    api_base_mode,
    find_input_node,
    is_settings_layout,
    layout_typed_nodes,
    parse_args,
    run_backend_smoke,
    validate_api_base,
    validate_device_api_base,
)

# ------------------------------------------------------------------ fakes ---

# ---------------------------------------------------- structural fakes --
# M14-142: fakes carry the REAL uitest dumpLayout shape - every node has a
# "type". Home's 服务地址 URL line and Settings captions are type Text;
# the only editable node on Settings is type TextInput. Selection logic
# must be structural, so the fakes must be structural too.

TAB_BAR = [
    {"attributes": {"type": "Text", "text": "首页",
                    "bounds": "[10,2700][110,2760]"}},
    {"attributes": {"type": "Text", "text": "设置",
                    "bounds": "[110,2700][210,2760]"}},
]
SETTINGS_TAB_CENTER = ("160", "2730")   # 设置 tab
INPUT_CENTER = ("660", "469")           # TextInput [56,399][1264,539]
HOME_URL_LABEL_CENTER = ("370", "330")  # 服务地址 Text [40,300][700,360]


def home_layout(service_url="http://127.0.0.1:8000/"):
    """Structural Home: every URL-ish line is a Text label (the trap)."""
    return {
        "attributes": {"type": "Page", "bounds": "[0,0][100,100]"},
        "children": [
            *TAB_BAR,
            {"attributes": {"type": "Text",
                            "text": f"服务地址: {service_url}",
                            "bounds": "[40,300][700,360]"}},
            {"attributes": {"type": "Text", "text": "请求失败 (HTTP 401)",
                            "bounds": "[40,400][700,460]"}},
            {"attributes": {"type": "Text", "text": "请求失败 (HTTP 401)",
                            "bounds": "[40,500][700,560]"}},
            {"attributes": {"type": "Text", "text": "请求失败 (HTTP 401)",
                            "bounds": "[40,600][700,660]"}},
            {"attributes": {"type": "Text", "text": "0.1.0",
                            "bounds": "[40,700][700,760]"}},
        ],
    }


def settings_layout(input_url="http://127.0.0.1:8000/", saved=None):
    """Structural Settings: one TextInput (the base-URL field) + Texts."""
    children = [
        *TAB_BAR,
        {"attributes": {
            "type": "Text",
            "text": "仅保存服务基地址(URL);不保存任何账号、令牌或密码。",
            "bounds": "[56,308][1102,357]"}},
        {"attributes": {"type": "TextInput", "text": input_url,
                        "bounds": "[56,399][1264,539]"}},
        {"attributes": {"type": "Text", "text": "保存",
                        "bounds": "[56,581][280,721]"}},
        {"attributes": {"type": "Text", "text": "测试连接",
                        "bounds": "[322,581][658,721]"}},
    ]
    if saved:
        children.append({"attributes": {
            "type": "Text", "text": f"已保存: {saved}",
            "bounds": "[56,763][642,816]"}})
    return {"attributes": {"type": "Page", "bounds": "[0,0][100,100]"},
            "children": children}


def make_fake_hdc(
    layouts: list[dict],
    *,
    exit_codes: list[int] | None = None,
) -> tuple:
    """A fake runner that answers layout dumps with canned trees."""
    calls: list[tuple[str, ...]] = []

    @dataclass(frozen=True)
    class _Result:
        returncode: int
        stdout: str = ""
        stderr: str = ""

    state = {"layout_index": 0, "exit_index": 0}

    def runner(argv, cwd, env):
        calls.append(tuple(argv))
        rc = 0
        if exit_codes:
            rc = exit_codes[
                min(state["exit_index"], len(exit_codes) - 1)]
            state["exit_index"] += 1
        subcommand = argv[3] if len(argv) > 3 else ""
        if subcommand in ("uitest", "file", "shell"):
            if "dumpLayout" in argv:
                index = min(state["layout_index"], len(layouts) - 1)
                state["layout_index"] += 1
                if layouts:
                    _DUMMY_STORE["next"] = json.dumps(layouts[index])
            elif "recv" in argv:
                local = Path(argv[argv.index("recv") + 2])
                local.write_text(_DUMMY_STORE.get("next", "{}"),
                                 encoding="utf-8")
        return _Result(returncode=rc)

    return runner, calls


_DUMMY_STORE: dict = {}


def make_fake_getter(
    statuses: dict[str, tuple[int, str]] | None = None,
    *,
    raise_for: set[str] | None = None,
) -> tuple:
    """A fake HTTP getter replaying honest answers (or lies)."""
    honest = {
        "/health": (200, json.dumps({"status": "ok"})),
        "/api/v1/auth/status": (200, json.dumps({"auth_enabled": True})),
        "/api/v1/version": (200, json.dumps({"version": "0.1.0"})),
        "/api/v1/system/privacy": (401, ""),
        "/api/v1/system/ops-snapshot": (401, ""),
        "/api/v1/audit?limit=100": (401, ""),
    }
    answers = dict(honest)
    if statuses:
        for k, v in statuses.items():
            answers[k] = v
    calls: list[str] = []
    raise_for = raise_for or set()

    def getter(url: str, timeout: float):
        calls.append(url)
        # urlsplit-based parsing: keep path + query so both loopback
        # bases (http://127.0.0.1:8000/health) and public bases with a
        # path prefix (https://ndtool.cn/aios/health) resolve to the
        # existing fake answer keys (unique-suffix match).
        parts = urlsplit(url)
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        if path not in answers:
            suffix_matches = [k for k in answers if path.endswith(k)]
            if len(suffix_matches) == 1:
                path = suffix_matches[0]
        if any(r in url for r in raise_for):
            raise OSError("connection refused")
        status, body = answers[path]
        return status, body

    return getter, calls


def fake_resolvers(target="127.0.0.1:5555", bundle="com.example.app"):
    def target_resolver(raw, known):
        @dataclass(frozen=True)
        class _T:
            raw: str
            hash: str
            kind: str
            validated: bool
            match: str | None
        return _T(target, "ABCDEF123456", "loopback", False, None), []

    def tool_resolver(program):
        from tools.harmony_release.device_smoke import HdcTool

        return HdcTool(Path("C:/fake/hdc.exe"), "path_lookup"), []

    def bundle_resolver(root, raw):
        return bundle, []

    return target_resolver, tool_resolver, bundle_resolver


def make_hap(root: Path) -> Path:
    hap = root / "entry-default-unsigned.hap"
    hap.write_bytes(b"FAKE-HAP")
    return hap


# ------------------------------------------------------------- api base -----

class TestValidateApiBase:
    def test_accepts_loopback_127(self):
        base, failures = validate_api_base("http://127.0.0.1:8000/")
        assert base == "http://127.0.0.1:8000/"
        assert failures == []

    def test_accepts_localhost_https(self):
        base, failures = validate_api_base("https://localhost:8000/")
        assert failures == []

    def test_rejects_device_url_10_0_2_2(self):
        base, failures = validate_api_base("http://10.0.2.2:8000/")
        assert base is None
        assert failures and failures[0]["code"] == "api_base_not_loopback"

    def test_rejects_lan_address(self):
        base, failures = validate_api_base("http://192.168.1.5:8000/")
        assert base is None
        assert failures[0]["code"] == "api_base_not_loopback"

    def test_rejects_public_internet(self):
        base, failures = validate_api_base("http://example.com/")
        assert base is None

    def test_rejects_missing_trailing_slash(self):
        base, failures = validate_api_base("http://127.0.0.1:8000")
        assert base is None
        assert failures[0]["code"] == "api_base_not_loopback"

    def test_rejects_empty(self):
        base, failures = validate_api_base(None)
        assert base is None
        assert failures[0]["code"] == "api_base_required"

    def test_rejects_path_or_query(self):
        for raw in ("http://127.0.0.1:8000/api", "http://127.0.0.1:8000/?x=1"):
            base, _f = validate_api_base(raw)
            assert base is None

    def test_rejects_userinfo_and_port_too_big(self):
        for raw in ("http://user@127.0.0.1:8000/", "http://127.0.0.1:99999/"):
            base, _f = validate_api_base(raw)
            assert base is None


# ------------------------------------- M14-169A2a strict public https ------

class TestValidateApiBaseStrictPublic:
    """M14-169A1a: the strict public https host-base shape."""

    def test_accepts_public_https_root(self):
        base, failures = validate_api_base("https://example.com/")
        assert base == "https://example.com/"
        assert failures == []

    def test_accepts_public_https_with_path_prefix(self):
        base, failures = validate_api_base("https://ndtool.cn/aios/")
        assert base == "https://ndtool.cn/aios/"
        assert failures == []

    def test_accepts_public_https_with_explicit_port(self):
        base, failures = validate_api_base("https://example.com:8443/")
        assert failures == []

    def test_accepts_ascii_punycode_host(self):
        base, failures = validate_api_base("https://xn--r8jz45g.jp/")
        assert base == "https://xn--r8jz45g.jp/"
        assert failures == []

    def test_rejects_public_plain_http(self):
        for raw in ("http://example.com/", "http://ndtool.cn/aios/"):
            base, failures = validate_api_base(raw)
            assert base is None
            assert failures[0]["code"] == "api_base_not_loopback"

    def test_rejects_missing_trailing_slash(self):
        for raw in ("https://example.com", "https://ndtool.cn/aios",
                    "https://example.com:8443"):
            base, _f = validate_api_base(raw)
            assert base is None

    def test_rejects_query_fragment_userinfo_backslash(self):
        for raw in ("https://example.com/?q=1",
                    "https://example.com/#sec",
                    "https://user:pw@example.com/",
                    "https://example.com\\/aios/"):
            base, _f = validate_api_base(raw)
            assert base is None

    def test_rejects_invalid_public_port(self):
        base, failures = validate_api_base("https://example.com:99999/")
        assert base is None
        assert failures[0]["code"] == "api_base_not_loopback"

    def test_rejects_non_ascii_or_unsafe_hosts(self):
        for raw in ("https://例え.jp/",          # non-ASCII host
                    "https://exa_mple.com/",     # underscore in host
                    "  https://example.com/"):   # whitespace padding
            base, _f = validate_api_base(raw)
            assert base is None


class TestApiBaseMode:
    """Accepted bases classify into exactly one mode."""

    def test_loopback_bases_classify_loopback(self):
        assert api_base_mode("http://127.0.0.1:8000/") == "loopback"
        assert api_base_mode("https://localhost:8443/") == "loopback"

    def test_public_bases_classify_public_https(self):
        assert api_base_mode("https://example.com/") == "public_https"
        assert api_base_mode("https://example.com:8443/") == "public_https"

    def test_public_base_with_prefix_classifies_public_https(self):
        assert api_base_mode("https://ndtool.cn/aios/") == "public_https"


# ------------------------------------------ M14-169A1b1 device-side base ---

class TestValidateDeviceApiBase:
    """The app's Settings value: emulator alias or strict public https."""

    def test_accepts_emulator_alias_default_port(self):
        base, failures = validate_device_api_base("http://10.0.2.2:8000/")
        assert base == "http://10.0.2.2:8000/"
        assert failures == []

    def test_accepts_emulator_alias_other_explicit_port(self):
        base, failures = validate_device_api_base("http://10.0.2.2:9000/")
        assert base == "http://10.0.2.2:9000/"
        assert failures == []

    def test_accepts_strict_public_https(self):
        base, failures = validate_device_api_base("https://ndtool.cn/aios/")
        assert base == "https://ndtool.cn/aios/"
        assert failures == []

    def test_rejects_missing_value(self):
        for raw in (None, "", "   "):
            base, failures = validate_device_api_base(raw)
            assert base is None
            assert failures[0]["code"] == "device_api_base_required"

    def test_rejects_loopback_names_other_than_alias(self):
        for raw in ("http://127.0.0.1:8000/", "http://localhost:8000/",
                    "https://localhost:8443/"):
            base, failures = validate_device_api_base(raw)
            assert base is None
            assert failures[0]["code"] == "device_api_base_not_allowed"

    def test_rejects_lan_and_private_hosts(self):
        for raw in ("http://192.168.1.5:8000/", "http://172.16.0.1:8000/",
                    "http://10.0.0.1:8000/"):
            base, _f = validate_device_api_base(raw)
            assert base is None

    def test_rejects_public_plain_http(self):
        base, failures = validate_device_api_base("http://example.com/")
        assert base is None
        assert failures[0]["code"] == "device_api_base_not_allowed"

    def test_rejects_malformed_values(self):
        for raw in ("http://10.0.2.2:8000",        # missing trailing slash
                    "http://10.0.2.2:99999/",      # port out of range
                    "http://10.0.2.2:8000/?q=1",   # query on the alias
                    "http://user@10.0.2.2:8000/",  # userinfo
                    "  http://10.0.2.2:8000/  ",   # whitespace padding
                    "://broken"):                  # not a URL at all
            base, _f = validate_device_api_base(raw)
            assert base is None

    def test_rejects_non_ascii_or_unsafe_input(self):
        for raw in ("https://例え.jp/",             # non-ASCII host
                    "https://exa_mple.com/",        # unsafe host character
                    "http://10.0.2.2:8000/中文"):    # non-ASCII on the alias
            base, _f = validate_device_api_base(raw)
            assert base is None

    def test_failures_never_echo_raw_value(self):
        _base, failures = validate_device_api_base("http://10.0.2.2:9000/?x=9")
        blob = json.dumps(failures, ensure_ascii=False)
        assert "10.0.2.2:9000" not in blob
        assert "?x=9" not in blob


class TestParseArgsDeviceApiBase:
    """--device-api-base: fixed emulator-alias default, explicit override."""

    def test_default_device_api_base_is_emulator_alias(self):
        args = parse_args(["--target", "127.0.0.1:5555"])
        assert args.device_api_base == "http://10.0.2.2:8000/"

    def test_explicit_device_api_base_overrides_default(self):
        args = parse_args(["--target", "127.0.0.1:5555",
                           "--device-api-base", "https://ndtool.cn/aios/"])
        assert args.device_api_base == "https://ndtool.cn/aios/"


# ------------------- M14-169A2b1 plan serialization + device-base gate -----

class TestApiBaseSerializationAndGate:
    """Plan mode must serialize the api-base decision (mode, loopback
    narrowing, resolved device URL) without side effects, and a rejected
    device-side base must block the run before any probe, hdc command,
    or evidence write."""

    def test_default_loopback_plan_serializes_mode_and_device_url(
            self, tmp_path):
        hap = make_hap(tmp_path)
        tr, to, br = fake_resolvers()
        getter, get_calls = make_fake_getter()
        runner, hdc_calls = make_fake_hdc([])
        result, code = run_backend_smoke(
            tmp_path, target="127.0.0.1:5555", hap=str(hap),
            confirm_mutation=False,
            runner=runner, http_get=getter,
            target_resolver=tr, tool_resolver=to, bundle_resolver=br,
        )
        assert code == EXIT_OK
        assert result["status"] == "planned"
        assert result["api_base_mode"] == "loopback"
        assert result["api_base_loopback_only"] is True
        # the default device-side base is the emulator alias
        assert result["device_url"] == "http://10.0.2.2:8000/"
        assert get_calls == []          # no HTTP request
        assert hdc_calls == []          # no hdc command

    def test_public_https_plan_serializes_mode_and_device_url(
            self, tmp_path):
        hap = make_hap(tmp_path)
        tr, to, br = fake_resolvers()
        getter, get_calls = make_fake_getter()
        runner, hdc_calls = make_fake_hdc([])
        result, code = run_backend_smoke(
            tmp_path, target="127.0.0.1:5555", hap=str(hap),
            api_base="https://ndtool.cn/aios/",
            device_api_base="https://ndtool.cn/aios/",
            confirm_mutation=False,
            runner=runner, http_get=getter,
            target_resolver=tr, tool_resolver=to, bundle_resolver=br,
        )
        assert code == EXIT_OK
        assert result["status"] == "planned"
        assert result["api_base_mode"] == "public_https"
        assert result["api_base_loopback_only"] is False
        assert result["device_url"] == "https://ndtool.cn/aios/"
        assert get_calls == []          # no HTTP request
        assert hdc_calls == []          # no hdc command

    def test_rejected_device_base_blocks_before_any_probe_or_command(
            self, tmp_path):
        hap = make_hap(tmp_path)
        tr, _to, br = fake_resolvers()
        evidence_dir = tmp_path / "evidence"

        def _must_not_run(*_args, **_kwargs):
            raise AssertionError("must not run on a blocked request")

        result, code = run_backend_smoke(
            tmp_path, target="127.0.0.1:5555", hap=str(hap),
            device_api_base="http://192.168.1.5:8000/",
            confirm_mutation=True,
            evidence_dir=evidence_dir,
            runner=_must_not_run, http_get=_must_not_run,
            target_resolver=tr, tool_resolver=_must_not_run,
            bundle_resolver=br,
        )
        assert code == EXIT_BLOCKED
        assert result["status"] == "blocked"
        codes = {f["code"] for f in result["failures"]}
        assert "device_api_base_not_allowed" in codes
        # nothing resolved, nothing probed, nothing executed
        assert result["device_url"] is None
        assert result["toolchain"]["probed"] is False
        assert result["device_access"]["commands_executed"] == 0
        assert result["device_access"]["hardware_touched"] is False
        # every step stayed not_run with the request-invalid reason
        for step in result["steps"]:
            assert step["status"] == "not_run"
            assert step["reason"] == "request_invalid"
        # no evidence directory was created and the rejected raw value
        # never leaks into the serialized JSON
        assert not evidence_dir.exists()
        blob = json.dumps(result, ensure_ascii=False)
        assert "192.168.1.5" not in blob


# ---------------------------------------------------------------- plan ------

class TestPlanMode:
    def test_plan_only_touches_nothing(self, tmp_path):
        hap = make_hap(tmp_path)
        tr, to, br = fake_resolvers()
        getter, get_calls = make_fake_getter()
        runner, hdc_calls = make_fake_hdc([])
        result, code = run_backend_smoke(
            tmp_path, target="127.0.0.1:5555", hap=str(hap),
            confirm_mutation=False,
            runner=runner, http_get=getter,
            target_resolver=tr, tool_resolver=to, bundle_resolver=br,
        )
        assert code == EXIT_OK
        assert result["status"] == "planned"
        assert result["dry_run"] is True
        assert get_calls == []          # no HTTP request
        assert hdc_calls == []          # no hdc command
        assert result["mutation_performed"] is False
        assert all(s["status"] == "not_run" for s in result["steps"])
        assert result["not_run"] == list(STEP_ORDER_NAMES)

    def test_plan_records_shapes_only(self, tmp_path):
        hap = make_hap(tmp_path)
        tr, to, br = fake_resolvers()
        result, _ = run_backend_smoke(
            tmp_path, target="127.0.0.1:5555", hap=str(hap),
            confirm_mutation=False,
            runner=make_fake_hdc([])[0], http_get=make_fake_getter()[0],
            target_resolver=tr, tool_resolver=to, bundle_resolver=br,
        )
        blob = json.dumps(result)
        assert "127.0.0.1:5555" not in blob  # target never serialized
        assert "C:/fake/hdc.exe" not in blob
        assert "raw_recorded: false" in blob.replace('"', "").lower()


STEP_ORDER_NAMES = [
    "host_preflight", "install", "start", "settings_ui",
    "home_view", "background", "uninstall",
]


# ------------------------------------------------------------- preflight ----

class TestHostPreflight:
    def _run(self, tmp_path, statuses=None, raise_for=None):
        hap = make_hap(tmp_path)
        tr, to, br = fake_resolvers()
        getter, _calls = make_fake_getter(statuses, raise_for=raise_for)
        result, code = run_backend_smoke(
            tmp_path, target="127.0.0.1:5555", hap=str(hap),
            confirm_mutation=True,
            runner=make_fake_hdc([])[0], http_get=getter,
            target_resolver=tr, tool_resolver=to, bundle_resolver=br,
        )
        return result, code

    def test_honest_answers_pass(self, tmp_path):
        result, code = self._run(tmp_path)
        assert result["preflight"]["all_matched"] is True
        assert [r["matched"] for r in result["preflight"]["records"]] == [
            True] * 6
        pre = next(s for s in result["steps"] if s["name"] == "host_preflight")
        assert pre["status"] == "ok"
        assert code == EXIT_FAILURE or code == EXIT_OK
        # install/start ran (mutation attempted) but later UI steps failed
        # against the fake hdc: that is an honest failure, not a pass.
        assert result["mutation_performed"] is True

    def test_wrong_status_on_health_fails_closed(self, tmp_path):
        result, code = self._run(tmp_path, statuses={
            "/health": (500, ""),
        })
        assert code == EXIT_FAILURE
        codes = {f["code"] for f in result["failures"]}
        assert "preflight_status_mismatch" in codes
        # Nothing was installed: preflight blocks the mutation.
        assert result["mutation_performed"] is False
        assert result["not_run"] == [
            "install", "start", "settings_ui", "home_view", "background",
            "uninstall"]

    def test_200_without_json_body_fails_closed(self, tmp_path):
        result, code = self._run(tmp_path, statuses={
            "/health": (200, "not json at all"),
        })
        assert code == EXIT_FAILURE
        codes = {f["code"] for f in result["failures"]}
        assert "preflight_status_mismatch" in codes

    def test_200_without_required_field_fails_closed(self, tmp_path):
        result, code = self._run(tmp_path, statuses={
            "/api/v1/auth/status": (200, json.dumps({"wrong": 1})),
        })
        assert code == EXIT_FAILURE
        codes = {f["code"] for f in result["failures"]}
        assert "preflight_field_missing" in codes

    def test_unexpected_200_on_privacy_fails_closed(self, tmp_path):
        # privacy must honestly answer 401; a 200 without auth is a contract
        # break and must block the run.
        result, code = self._run(tmp_path, statuses={
            "/api/v1/system/privacy": (200, json.dumps({"ok": True})),
        })
        assert code == EXIT_FAILURE
        assert result["preflight"]["all_matched"] is False

    def test_network_error_fails_closed(self, tmp_path):
        result, code = self._run(tmp_path, raise_for={"/health"})
        assert code == EXIT_FAILURE
        codes = {f["code"] for f in result["failures"]}
        assert "preflight_request_failed" in codes
        assert result["mutation_performed"] is False

    def test_non_loopback_base_blocks_before_any_request(self, tmp_path):
        hap = make_hap(tmp_path)
        tr, to, br = fake_resolvers()
        getter, get_calls = make_fake_getter()
        result, code = run_backend_smoke(
            tmp_path, target="127.0.0.1:5555", hap=str(hap),
            api_base="http://10.0.2.2:8000/",
            confirm_mutation=True,
            runner=make_fake_hdc([])[0], http_get=getter,
            target_resolver=tr, tool_resolver=to, bundle_resolver=br,
        )
        assert code == EXIT_BLOCKED
        assert result["status"] == "blocked"
        assert get_calls == []
        assert result["mutation_performed"] is False
        codes = {f["code"] for f in result["failures"]}
        assert "api_base_not_loopback" in codes


# ------------------------------------------------------------ device steps --

class TestDeviceSteps:
    def _make(self, tmp_path, layouts):
        hap = make_hap(tmp_path)
        tr, to, br = fake_resolvers()
        runner, calls = make_fake_hdc(layouts)
        getter, _ = make_fake_getter()
        return hap, tr, to, br, runner, getter, calls

    def test_settings_saved_and_home_asserted_ok(self, tmp_path, monkeypatch):
        # Speed: no real sleeps in unit tests.
        monkeypatch.setattr("tools.harmony_release.backend_smoke.time.sleep",
                            lambda _s: None)
        saved_layout = settings_layout(
            input_url="http://10.0.2.2:8000/", saved="http://10.0.2.2:8000/")
        home = home_layout("http://10.0.2.2:8000/")
        # First dump already shows Settings (TextInput present): no tab
        # click needed; three settings dumps + one Home dump.
        _hap, tr, to, br, runner, getter, calls = self._make(
            tmp_path, [saved_layout] * 3 + [home])
        result, code = run_backend_smoke(
            tmp_path, target="127.0.0.1:5555", hap=str(_hap),
            confirm_mutation=True,
            runner=runner, http_get=getter,
            target_resolver=tr, tool_resolver=to, bundle_resolver=br,
        )
        assert code == EXIT_OK, json.dumps(result["failures"], ensure_ascii=False)
        assert result["status"] == "ok"
        assert result["settings"]["saved_confirmed"] is True
        assert result["settings"]["convergence_retries"] == 0
        assert result["home"]["counts"]["请求失败 (HTTP 401)"] == 3
        assert result["home"]["counts"]["0.1.0"] == 1
        # cleanup happened
        assert result["cleanup"]["status"] == "ok"
        assert result["device_side_state"]["bundle_uninstalled"] is True
        # the uninstall command was really the last hdc call
        assert calls[-1][-1] == "com.example.app"
        assert calls[-1][3] == "uninstall"
        # home_view = cold restart (force-stop + aa start), no Home tab click
        assert any(c[3:6] == ("shell", "aa", "force-stop") for c in calls)
        assert any(c[3:6] == ("shell", "aa", "start") for c in calls)
        clicks = [c[-2:] for c in calls if "click" in c]
        assert ("60", "2730") not in clicks  # 首页 tab center, never clicked

    def test_settings_save_click_skips_caption_containing_save(
            self, tmp_path, monkeypatch):
        # Real M14-84 emulator layout: the Settings caption
        # "仅保存服务基地址(URL);不保存任何账号、令牌或密码。" contains
        # 保存 and appears BEFORE the real 保存 button. A substring-only
        # lookup clicks the caption and never saves; exact-first matching
        # must find the button. Structural fakes: the app starts on Home,
        # Settings exposes one TextInput + the caption + two buttons.
        monkeypatch.setattr("tools.harmony_release.backend_smoke.time.sleep",
                            lambda _s: None)
        saved = settings_layout(input_url="http://127.0.0.1:8000/")
        typed = settings_layout(input_url="http://10.0.2.2:8000/")
        confirmed = settings_layout(input_url="http://10.0.2.2:8000/",
                                    saved="http://10.0.2.2:8000/")
        home = home_layout()
        _hap, tr, to, br, runner, getter, calls = self._make(
            tmp_path, [home, saved, typed, confirmed, home])
        result, code = run_backend_smoke(
            tmp_path, target="127.0.0.1:5555", hap=str(_hap),
            confirm_mutation=True,
            runner=runner, http_get=getter,
            target_resolver=tr, tool_resolver=to, bundle_resolver=br,
        )
        assert code == EXIT_OK, json.dumps(result["failures"], ensure_ascii=False)
        assert result["settings"]["saved_confirmed"] is True
        # the click before the confirmation dump hit the real button,
        # not the caption: y 651 (button center) not 332 (caption center)
        clicks = [c for c in calls if "click" in c]
        # clicks in order: settings tab, input field, save button
        # (no Home tab click anywhere: Home is a cold restart)
        assert len(clicks) == 3, calls
        save_click = clicks[2]
        assert save_click[-2:] == ("168", "651"), save_click

    def test_home_assertion_missed_fails_closed(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tools.harmony_release.backend_smoke.time.sleep",
                            lambda _s: None)
        saved = settings_layout(input_url="http://127.0.0.1:8000/")
        typed = settings_layout(input_url="http://10.0.2.2:8000/")
        confirmed = settings_layout(input_url="http://10.0.2.2:8000/",
                                    saved="http://10.0.2.2:8000/")
        # Home without any expected text -> the assertion must fail.
        broken = json.loads(json.dumps(home_layout()))
        broken["children"] = [
            c for c in broken["children"]
            if "请求失败" not in c["attributes"]["text"]
            and c["attributes"]["text"] != "0.1.0"
        ]
        _hap, tr, to, br, runner, getter, calls = self._make(
            tmp_path, [saved, typed, confirmed, broken, broken])
        result, code = run_backend_smoke(
            tmp_path, target="127.0.0.1:5556", hap=str(_hap),
            confirm_mutation=True,
            runner=runner, http_get=getter,
            target_resolver=tr, tool_resolver=to, bundle_resolver=br,
        )
        assert code == EXIT_FAILURE
        codes = {f["code"] for f in result["failures"]}
        assert "home_assertion_missed" in codes
        # cleanup still attempted after the failure
        assert result["cleanup"]["attempted"] is True
        assert result["cleanup"]["status"] == "ok"
        assert calls[-1][3] == "uninstall"

    def test_install_failure_skips_ui_and_still_cleans_up(self, tmp_path):
        _hap, tr, to, br, runner, getter, calls = self._make(tmp_path, [])
        runner_failing = _failing_install_runner()
        result, code = run_backend_smoke(
            tmp_path, target="127.0.0.1:5555", hap=str(_hap),
            confirm_mutation=True,
            runner=runner_failing, http_get=getter,
            target_resolver=tr, tool_resolver=to, bundle_resolver=br,
        )
        assert code == dump_exit_failure
        steps = {s["name"]: s for s in result["steps"]}
        assert steps["install"]["status"] == "failure"
        assert steps["settings_ui"]["status"] == "not_run"
        assert steps["uninstall"]["status"] == "not_run"
        # install never succeeded: mutation_performed False, cleanup not
        # required, uninstall not attempted.
        assert result["mutation_performed"] is False
        assert result["cleanup"]["required"] is False
        assert result["cleanup"]["attempted"] is False


def _failing_install_runner():
    def runner(argv, cwd, env):
        class _R:
            returncode = 1
        return _R()
    return runner


dump_exit_failure = EXIT_FAILURE

# --------------------------- M14-169A2b2 public https integration ----------

PUBLIC_BASE = "https://ndtool.cn/aios/"


class TestPublicHttpsIntegration:
    """One deterministic public-HTTPS full-chain run: host preflight
    probes the public base with its path prefix, Settings types and
    confirms the same public URL, Home asserts it, and the wrapper
    itself never issues a device-side request for that URL (the app is
    the only party that talks to the server)."""

    def test_public_https_full_chain_ok(self, tmp_path, monkeypatch):
        # Speed: no real sleeps in unit tests.
        monkeypatch.setattr("tools.harmony_release.backend_smoke.time.sleep",
                            lambda _s: None)
        saved = settings_layout(input_url="http://10.0.2.2:8000/")
        typed = settings_layout(input_url=PUBLIC_BASE)
        confirmed = settings_layout(input_url=PUBLIC_BASE, saved=PUBLIC_BASE)
        home = home_layout(PUBLIC_BASE)
        # First dump already shows Settings (TextInput present): no tab
        # click needed; three settings dumps + one Home dump.
        hap = make_hap(tmp_path)
        tr, to, br = fake_resolvers()
        runner, calls = make_fake_hdc([saved, typed, confirmed, home])
        getter, get_calls = make_fake_getter()
        result, code = run_backend_smoke(
            tmp_path, target="127.0.0.1:5555", hap=str(hap),
            api_base=PUBLIC_BASE, device_api_base=PUBLIC_BASE,
            confirm_mutation=True,
            runner=runner, http_get=getter,
            target_resolver=tr, tool_resolver=to, bundle_resolver=br,
        )
        assert code == EXIT_OK, json.dumps(result["failures"],
                                           ensure_ascii=False)
        assert result["status"] == "ok"
        # every preflight probe went to the public base (prefix intact,
        # so the urlsplit-based fake parsing is really exercised)
        assert get_calls and all(c.startswith(PUBLIC_BASE)
                                 for c in get_calls)
        # Settings typed, saved and confirmed the public URL
        assert result["settings"]["saved_confirmed"] is True
        assert result["settings"]["device_url_typed"] == PUBLIC_BASE
        # Home showed the same URL and the honest answers
        assert result["home"]["counts"]["请求失败 (HTTP 401)"] == 3
        assert result["home"]["counts"]["0.1.0"] == 1
        # the resolved device-side URL is the public base itself, taken
        # from the validated request (never probed off the device)
        assert result["device_url"] == PUBLIC_BASE
        assert result["device_url_requested_by_wrapper"] is False
        # cleanup uninstalled as the final hdc command
        assert result["cleanup"]["status"] == "ok"
        assert calls[-1][3] == "uninstall"

# ----------------------------------------- M14-142 convergence regressions --

class TestSettingsConvergence:
    """The cold-start race: attempt 1 still sees Home; the Settings tab
    click must be retried on every bounded non-converged attempt, the
    Home 服务地址 label must never be selected as the input, and a
    layout without an editable TextInput must stay a failure."""

    def _run(self, tmp_path, monkeypatch, layouts):
        monkeypatch.setattr("tools.harmony_release.backend_smoke.time.sleep",
                            lambda _s: None)
        hap = make_hap(tmp_path)
        tr, to, br = fake_resolvers()
        runner, calls = make_fake_hdc(layouts)
        getter, _ = make_fake_getter()
        result, code = run_backend_smoke(
            tmp_path, target="127.0.0.1:5555", hap=str(hap),
            confirm_mutation=True,
            runner=runner, http_get=getter,
            target_resolver=tr, tool_resolver=to, bundle_resolver=br,
        )
        return result, code, calls

    def test_attempt1_home_attempt2_settings_succeeds(
            self, tmp_path, monkeypatch):
        saved = settings_layout(input_url="http://127.0.0.1:8000/")
        typed = settings_layout(input_url="http://10.0.2.2:8000/")
        confirmed = settings_layout(input_url="http://10.0.2.2:8000/",
                                    saved="http://10.0.2.2:8000/")
        home = home_layout()
        # dumps: home (attempt1), home (after tab click), home (attempt2
        # start - the cold-start race), settings (after retry click),
        # typed, confirmed, then Home for the home_view step.
        result, code, calls = self._run(
            tmp_path, monkeypatch,
            [home, home, home, saved, typed, confirmed, home])
        assert code == EXIT_OK, json.dumps(result["failures"],
                                           ensure_ascii=False)
        assert result["settings"]["saved_confirmed"] is True
        # attempt 1's non-convergence is recorded as a retry, not a lie
        assert result["settings"]["convergence_retries"] == 1
        clicks = [c for c in calls if "click" in c]
        tab_clicks = [c for c in clicks
                      if tuple(c[-2:]) == SETTINGS_TAB_CENTER]
        assert len(tab_clicks) == 2, clicks  # attempt 1 AND attempt 2
        # the input click hit the TextInput center ...
        assert sum(1 for c in clicks
                   if tuple(c[-2:]) == INPUT_CENTER) == 1
        # ... and never Home's 服务地址 label center
        assert all(tuple(c[-2:]) != HOME_URL_LABEL_CENTER
                   for c in clicks)
        # no click ever landed on the 首页 tab (Home = cold restart)
        assert all(tuple(c[-2:]) != ("60", "2730") for c in clicks)

    def test_never_converges_fails_closed_and_retries_every_attempt(
            self, tmp_path, monkeypatch):
        home = home_layout()
        result, code, calls = self._run(tmp_path, monkeypatch, [home, home])
        assert code == EXIT_FAILURE
        settings = next(s for s in result["steps"]
                        if s["name"] == "settings_ui")
        assert settings["status"] == "failure"
        assert result["settings"]["saved_confirmed"] is False
        codes = {f["code"] for f in result["failures"]}
        assert "settings_input_not_found" in codes
        # every bounded attempt retried the 设置 tab click
        clicks = [c for c in calls if "click" in c]
        tab_clicks = [c for c in clicks
                      if tuple(c[-2:]) == SETTINGS_TAB_CENTER]
        assert len(tab_clicks) == SETTINGS_MAX_ATTEMPTS, clicks
        # nothing was ever typed: no input click anywhere, and never on
        # the 服务地址 label either
        assert all(tuple(c[-2:]) != INPUT_CENTER for c in clicks)
        assert all(tuple(c[-2:]) != HOME_URL_LABEL_CENTER
                   for c in clicks)
        # install succeeded, so cleanup still ran
        assert result["cleanup"]["attempted"] is True
        assert calls[-1][3] == "uninstall"


class TestStructuralInputSelection:
    """The input node is chosen structurally (type TextInput), never by
    URL-ish text - Home's 服务地址 label must be unselectable."""

    def test_home_service_label_is_never_the_input(self):
        typed = layout_typed_nodes(home_layout())
        assert find_input_node(typed) is None
        assert is_settings_layout(typed) is False

    def test_url_text_node_is_not_an_input(self):
        typed = [("Text", "服务地址: http://127.0.0.1:8000/",
                  "[40,300][700,360]")]
        assert find_input_node(typed) is None
        assert is_settings_layout(typed) is False

    def test_settings_layout_is_structurally_detected(self):
        typed = layout_typed_nodes(settings_layout())
        assert is_settings_layout(typed) is True
        assert find_input_node(typed) == (
            660, 469, "http://127.0.0.1:8000/")

    def test_textinput_with_unparsable_bounds_fails_closed(self):
        assert find_input_node([("TextInput", "x", "garbage")]) is None
        assert is_settings_layout([("TextInput", "x", "garbage")]) is False

    def test_first_structural_input_wins_over_labels(self):
        typed = [
            ("Text", "服务地址: http://127.0.0.1:8000/", "[40,300][700,360]"),
            ("TextInput", "http://127.0.0.1:8000/", "[0,0][10,10]"),
            ("TextInput", "other", "[0,0][20,20]"),
        ]
        assert find_input_node(typed) == (5, 5, "http://127.0.0.1:8000/")
