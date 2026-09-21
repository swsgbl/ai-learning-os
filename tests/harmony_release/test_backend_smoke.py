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

from tools.harmony_release.backend_smoke import (
    EXIT_BLOCKED,
    EXIT_FAILURE,
    EXIT_OK,
    run_backend_smoke,
    validate_api_base,
)

# ------------------------------------------------------------------ fakes ---

FAKE_LAYOUT_TABS = {
    "attributes": {"bounds": "[0,0][100,100]"},
    "children": [
        {"attributes": {"text": "首页", "bounds": "[10,2700][110,2760]"}},
        {"attributes": {"text": "设置", "bounds": "[110,2700][210,2760]"}},
        {"attributes": {
            "text": "http://127.0.0.1:8000/", "bounds": "[40,300][700,360]"}},
        {"attributes": {"text": "保存", "bounds": "[500,500][640,560]"}},
        {"attributes": {"text": "已保存: http://10.0.2.2:8000/",
                         "bounds": "[40,620][700,680]"}},
    ],
}


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
        path = url.split("8000", 1)[-1] if "8000" in url else url
        path = path if path.startswith("/") else "/" + path
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
        saved_layout = {
            "attributes": {"bounds": "[0,0][100,100]"},
            "children": [
                {"attributes": {"text": "首页", "bounds": "[10,2700][110,2760]"}},
                {"attributes": {"text": "设置", "bounds": "[110,2700][210,2760]"}},
                {"attributes": {"text": "http://10.0.2.2:8000/",
                                "bounds": "[40,300][700,360]"}},
                {"attributes": {"text": "保存", "bounds": "[500,500][640,560]"}},
                {"attributes": {"text": "已保存: http://10.0.2.2:8000/",
                                "bounds": "[40,620][700,680]"}},
            ],
        }
        home_layout = {
            "attributes": {"bounds": "[0,0][100,100]"},
            "children": [
                {"attributes": {"text": "首页", "bounds": "[10,2700][110,2760]"}},
                {"attributes": {"text": "设置", "bounds": "[110,2700][210,2760]"}},
                {"attributes": {"text": "请求失败 (HTTP 401)",
                                "bounds": "[40,300][700,360]"}},
                {"attributes": {"text": "请求失败 (HTTP 401)",
                                "bounds": "[40,400][700,460]"}},
                {"attributes": {"text": "请求失败 (HTTP 401)",
                                "bounds": "[40,500][700,560]"}},
                {"attributes": {"text": "0.1.0",
                                "bounds": "[40,600][700,660]"}},
            ],
        }
        _hap, tr, to, br, runner, getter, calls = self._make(
            tmp_path, [saved_layout] * 4 + [home_layout] * 4)
        result, code = run_backend_smoke(
            tmp_path, target="127.0.0.1:5555", hap=str(_hap),
            confirm_mutation=True,
            runner=runner, http_get=getter,
            target_resolver=tr, tool_resolver=to, bundle_resolver=br,
        )
        assert code == EXIT_OK, json.dumps(result["failures"], ensure_ascii=False)
        assert result["status"] == "ok"
        assert result["settings"]["saved_confirmed"] is True
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
        # must find the button.
        monkeypatch.setattr("tools.harmony_release.backend_smoke.time.sleep",
                            lambda _s: None)
        caption = "仅保存服务基地址(URL);不保存任何账号、令牌或密码。"
        saved_layout = {
            "attributes": {"bounds": "[0,0][100,100]"},
            "children": [
                {"attributes": {"text": "首页", "bounds": "[10,2700][110,2760]"}},
                {"attributes": {"text": "设置", "bounds": "[110,2700][210,2760]"}},
                {"attributes": {"text": caption,
                                "bounds": "[56,308][1102,357]"}},
                {"attributes": {"text": "http://127.0.0.1:8000/",
                                "bounds": "[56,399][1264,539]"}},
                {"attributes": {"text": "保存", "bounds": "[56,581][280,721]"}},
                {"attributes": {"text": "测试连接", "bounds": "[322,581][658,721]"}},
            ],
        }
        typed = json.loads(json.dumps(saved_layout))
        typed["children"] = [c for c in typed["children"]
                             if c["attributes"]["text"] != "http://127.0.0.1:8000/"]
        typed["children"].append({"attributes": {
            "text": "http://10.0.2.2:8000/", "bounds": "[56,399][1264,539]"}})
        confirmed = json.loads(json.dumps(typed))
        confirmed["children"].append({"attributes": {
            "text": "已保存: http://10.0.2.2:8000/",
            "bounds": "[56,763][642,816]"}})
        home_layout = {
            "attributes": {"bounds": "[0,0][100,100]"},
            "children": [
                {"attributes": {"text": "首页", "bounds": "[10,2700][110,2760]"}},
                {"attributes": {"text": "设置", "bounds": "[110,2700][210,2760]"}},
                {"attributes": {"text": "请求失败 (HTTP 401)",
                                "bounds": "[40,300][700,360]"}},
                {"attributes": {"text": "请求失败 (HTTP 401)",
                                "bounds": "[40,400][700,460]"}},
                {"attributes": {"text": "请求失败 (HTTP 401)",
                                "bounds": "[40,500][700,560]"}},
                {"attributes": {"text": "0.1.0",
                                "bounds": "[40,600][700,660]"}},
            ],
        }
        _hap, tr, to, br, runner, getter, calls = self._make(
            tmp_path,
            [saved_layout, typed, typed, confirmed] + [home_layout] * 2)
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
        assert len(clicks) >= 3, calls
        save_click = clicks[2]
        assert save_click[-2:] == ("168", "651"), save_click

    def test_home_assertion_missed_fails_closed(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tools.harmony_release.backend_smoke.time.sleep",
                            lambda _s: None)
        saved_layout = {
            "attributes": {"bounds": "[0,0][100,100]"},
            "children": [
                {"attributes": {"text": "首页", "bounds": "[10,2700][110,2760]"}},
                {"attributes": {"text": "设置", "bounds": "[110,2700][210,2760]"}},
                {"imported": 1},
            ],
        }
        # First dump: the URL field is found with the old value; after
        # clearing + typing + saving the confirmation appears.
        step1 = json.loads(json.dumps(saved_layout))
        step1["children"].append({"attributes": {
            "text": "http://127.0.0.1:8000/", "bounds": "[40,300][700,360]"}})
        step1["children"].append({"attributes": {
            "text": "保存", "bounds": "[500,500][640,560]"}})
        step2 = json.loads(json.dumps(step1))
        step2["children"].append({"attributes": {
            "text": "已保存: http://10.0.2.2:8000/",
            "bounds": "[40,620][700,680]"}})
        # Home: no 401 texts at all -> assertion must fail.
        home = {
            "attributes": {"bounds": "[0,0][100,100]"},
            "children": [
                {"attributes": {"text": "首页", "bounds": "[10,2700][110,2760]"}},
                {"attributes": {"text": "设置", "bounds": "[110,2700][210,2760]"}},
                {"attributes": {"text": "连接超时", "bounds": "[40,300][700,360]"}},
            ],
        }
        _hap, tr, to, br, runner, getter, calls = self._make(
            tmp_path, [step1, step1, step2, step2, home, home])
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
