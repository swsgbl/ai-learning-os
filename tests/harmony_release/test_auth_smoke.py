"""Focused tests for tools/harmony_release/auth_smoke.py (M14-89 R16).

Covers the supervisor's three corrections:
  1. plan-only mode: no HTTP request, no hdc subprocess, all 12 steps
     ``not_run`` with reason ``mutation_not_confirmed``;
  2. ``validate_api_base`` parses the URL - exact loopback hosts only,
     userinfo/query/fragment/path bypasses rejected, normalized
     trailing slash preserved, raw URL never echoed;
  3. auth-on redaction contract: no credential, token, Authorization
     value, raw argv, stdout/stderr, absolute path or layout text is
     ever serialized - the records carry status codes and booleans only;
  4. mutation gating and request/toolchain fail-closed paths.

Honesty rules inherited from the sibling suites: credentials and bearer
values never appear in these tests either - only synthetic values the
tool itself defines as mock-only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import pytest

from tools.harmony_release import auth_smoke
from tools.harmony_release.device_smoke import CommandResult, HdcTool, ResolvedTarget

RouteValue = Union[Tuple[int, object], callable]


# ------------------------------------------------------------ fakes ----

class FakeRunner:
    """Command runner that records invocations and never executes."""

    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.calls: List[Tuple[str, ...]] = []

    def __call__(self, argv, cwd: Path, env: Dict[str, str]):
        argv_t = tuple(str(a) for a in argv)
        self.calls.append(argv_t)
        return CommandResult(
            argv=argv_t,
            cwd=Path(cwd),
            returncode=self.returncode,
            stdout="",
            stderr="",
        )


class FakeHttp:
    """HTTP layer that records request URLs and serves canned responses.

    Values may be ``(status, payload)`` tuples or callables taking the
    request headers (GET) / decoded body (POST) so routes can behave
    realistically (401 without bearer, 200 with).
    """

    def __init__(
        self,
        gets: Optional[Dict[str, RouteValue]] = None,
        posts: Optional[Dict[str, RouteValue]] = None,
    ) -> None:
        self.gets: List[str] = []
        self.posts: List[str] = []
        self._gets = gets or {}
        self._posts = posts or {}

    def get(self, url: str, headers: Optional[Dict[str, str]] = None):
        self.gets.append(url)
        status, body = self._resolve(self._gets[url], headers)
        return status, json.dumps(body)

    def post(
        self,
        url: str,
        body: Dict[str, object],
        headers: Optional[Dict[str, str]] = None,
    ):
        self.posts.append(url)
        status, payload = self._resolve(self._posts[url], body)
        return status, json.dumps(payload)

    @staticmethod
    def _resolve(value: RouteValue, arg) -> Tuple[int, object]:
        if callable(value):
            return value(arg)
        return value


def ok_target(_target, _known=None):
    return ResolvedTarget(
        raw="127.0.0.1:5555",
        hash="0" * 8,
        kind="tcp",
        known_list_size=0,
        validated=False,
        match=None,
    ), []


def ok_bundle(_root, _bundle=None):
    return "com.example.fake", []


def ok_tool(_program: str):
    return HdcTool(Path("hdc"), "path_lookup"), []


def down_tool(_program: str):
    return None, [{"code": "hdc_not_found",
                   "detail": {"source": "path_lookup",
                              "program_name": "hdc"}}]


MOCK_TOKEN = "synthetic-token-xyz"


def auth_on_http() -> FakeHttp:
    """Full happy-path auth-on mock: gated 401s, honest login/me."""
    base = auth_smoke.DEFAULT_API_BASE

    def privacy(h):
        if h and h.get("authorization") == "Bearer " + MOCK_TOKEN:
            return 200, {"model_route": "local"}
        return 401, {"detail": "unauthenticated"}

    def login(b):
        if b.get("password") != auth_smoke.MOCK_LOGIN_PASS:
            return 401, {"detail": "wrong credentials"}
        return 200, {"access_token": MOCK_TOKEN, "token_type": "bearer"}

    def me(h):
        if h and h.get("authorization") == "Bearer " + MOCK_TOKEN:
            return 200, {"username": auth_smoke.MOCK_LOGIN_USER,
                         "role": "student", "created_at": "2026-01-01"}
        return 401, {"detail": "bad bearer"}

    return FakeHttp(
        gets={
            base + "api/v1/auth/status": (200, {"auth_enabled": True}),
            base + "api/v1/system/privacy": privacy,
            base + "api/v1/auth/me": me,
        },
        posts={base + "api/v1/auth/login": login},
    )


def auth_off_http() -> FakeHttp:
    base = auth_smoke.DEFAULT_API_BASE
    return FakeHttp(
        gets={
            base + "api/v1/auth/status": (200, {"auth_enabled": False}),
            base + "api/v1/system/privacy": (200, {"model_route": "local"}),
        },
    )


@pytest.fixture()
def fake_repo(tmp_path: Path) -> Path:
    """Tmp repository containing one fake signed HAP (request-valid)."""
    hap = tmp_path / "entry-default-signed.hap"
    hap.write_bytes(b"fake-hap-bytes")
    return tmp_path


# ---------------------------------------------- 1. plan-only contract ----

def test_plan_only_marks_all_steps_not_run_without_any_io(fake_repo):
    runner = FakeRunner()
    http = auth_on_http()
    result, exit_code = auth_smoke.run_auth_smoke(
        fake_repo,
        target="127.0.0.1:5555",
        hap="entry-default-signed.hap",
        confirm_mutation=False,
        runner=runner,
        http_get=http.get,
        http_post=http.post,
        tool_resolver=ok_tool,
        target_resolver=ok_target,
        bundle_resolver=ok_bundle,
    )
    steps = result["steps"]
    assert len(steps) == 12
    assert all(s["status"] == "not_run" for s in steps)
    assert all(s["reason"] == "mutation_not_confirmed" for s in steps)
    assert [s["name"] for s in steps] == [
        n for n, _p in auth_smoke.STEP_ORDER]
    # pure plan: zero HTTP requests, zero hdc subprocess invocations
    assert http.gets == [] and http.posts == []
    assert runner.calls == []
    assert result["commands_executed"] == 0
    assert result["mutation_performed"] is False
    assert result["toolchain"]["probed"] is False
    assert exit_code == auth_smoke.EXIT_OK
    # the warning names the confirmation flag, never any raw argv
    warn = result["warnings"][0]
    assert warn["code"] == "mutation_not_confirmed"
    assert warn["detail"]["confirmation_flag"] == \
        auth_smoke.MUTATION_CONFIRMATION_FLAG
    assert len(warn["detail"]["not_run"]) == 12


def test_plan_only_never_probes_toolchain(fake_repo):
    """Request validation passes, mutation unconfirmed -> no tool probe."""
    result, _ = auth_smoke.run_auth_smoke(
        fake_repo,
        target="127.0.0.1:5555",
        hap="entry-default-signed.hap",
        confirm_mutation=False,
        runner=FakeRunner(),
        http_get=auth_on_http().get,
        http_post=auth_on_http().post,
        tool_resolver=down_tool,  # would fail hard if probed
        target_resolver=ok_target,
        bundle_resolver=ok_bundle,
    )
    assert result["toolchain"]["probed"] is False
    assert result["toolchain_failures"] == []
    assert result["summary"]["not_run"] == 12
    assert result["request_failures"] == []


# ------------------------------------- 2. validate_api_base contract ----

@pytest.mark.parametrize("raw,expected", [
    ("http://127.0.0.1:8765", "http://127.0.0.1:8765/"),
    ("http://127.0.0.1:8765/", "http://127.0.0.1:8765/"),
    (" http://localhost:8765 ", "http://localhost:8765/"),
    ("http://localhost:8765/", "http://localhost:8765/"),
    ("http://localhost/", "http://localhost/"),
])
def test_validate_api_base_accepts_exact_loopback(raw, expected):
    base, failures = auth_smoke.validate_api_base(raw)
    assert failures == []
    assert base == expected


@pytest.mark.parametrize("raw,code", [
    # lookalike hosts must fail closed (exact-host rule)
    ("http://127.0.0.1.evil.example/", "api_base_not_loopback"),
    ("http://127.0.0.1.attacker.net:8765/", "api_base_not_loopback"),
    ("http://localhost.evil.example/", "api_base_not_loopback"),
    ("http://212.0.0.1:8765/", "api_base_not_loopback"),
    ("http://127.0.0.999:8765/", "api_base_not_loopback"),
    # userinfo bypass (parsed before the host, still rejected)
    ("http://127.0.0.1@evil.example/", "api_base_invalid"),
    ("http://user:pass@127.0.0.1:8765/", "api_base_invalid"),
    # query / fragment / path bypasses
    ("http://evil.example/?next=http://127.0.0.1", "api_base_not_loopback"),
    ("http://127.0.0.1:8765/?x=1", "api_base_invalid"),
    ("http://127.0.0.1:8765/#http://127.0.0.1", "api_base_invalid"),
    ("http://127.0.0.1:8765/api", "api_base_invalid"),
    ("http://127.0.0.1:8765//", "api_base_invalid"),
    # scheme
    ("https://127.0.0.1:8765/", "api_base_invalid"),
    ("//127.0.0.1:8765/", "api_base_invalid"),
    ("file://127.0.0.1/x", "api_base_invalid"),
])
def test_validate_api_base_rejects_lookalikes(raw, code):
    base, failures = auth_smoke.validate_api_base(" " + raw + " ")
    assert base is None
    assert len(failures) == 1
    assert failures[0]["code"] == code
    # never echo the raw URL (nor any host from it) in the failure detail
    assert raw not in json.dumps(failures)
    assert "127.0.0.1" not in json.dumps(failures[0]["detail"])
    assert "evil.example" not in json.dumps(failures[0]["detail"])


def test_validate_api_base_missing_and_empty():
    assert auth_smoke.validate_api_base(None)[1][0]["code"] == \
        "api_base_missing"
    assert auth_smoke.validate_api_base("   ")[0] is None


# --------------------------------- 3. redaction / auth-on contract ----

REDACTION_FORBIDDEN_VALUES = [
    auth_smoke.MOCK_LOGIN_PASS,
    auth_smoke.MOCK_WRONG_PASS,
    auth_smoke.MOCK_TOKEN_HINT,
]


def test_auth_on_contract_records_facts_without_any_secret():
    """Auth-on contract: 401/login/me/unlock facts, values never kept."""
    http = auth_on_http()
    records, failures = auth_smoke._host_auth_contract(
        http.get, http.post, auth_smoke.DEFAULT_API_BASE,
        expect_auth_on=True)
    assert failures == []
    names = [r["name"] for r in records]
    assert names == [
        "auth_status", "gated_privacy_401", "wrong_password_401",
        "login_200_token", "me_200_with_token",
        "gated_privacy_with_token", "gated_privacy_wrong_token",
    ]
    dumped = json.dumps(records)
    # no credential / token / Authorization value is ever serialized
    for secret in REDACTION_FORBIDDEN_VALUES:
        assert secret not in dumped
    assert MOCK_TOKEN not in dumped
    assert "Bearer " not in dumped
    assert "authorization" not in dumped.lower()
    # every record carries matched/expected/actual facts only
    for record in records:
        assert set(record.keys()) == {"name", "expected", "actual",
                                      "matched"}
        assert isinstance(record["matched"], bool)


def test_auth_on_contract_requires_bearer_token_type():
    """Login payload with a wrong token_type fails the contract."""
    base = auth_smoke.DEFAULT_API_BASE
    http = auth_on_http()

    def bad_login(b):
        if b.get("password") != auth_smoke.MOCK_LOGIN_PASS:
            return 401, {"detail": "wrong credentials"}
        return 200, {"access_token": MOCK_TOKEN, "token_type": "mac"}

    http._posts[base + "api/v1/auth/login"] = bad_login
    records, failures = auth_smoke._host_auth_contract(
        http.get, http.post, base, expect_auth_on=True)
    codes = [f["code"] for f in failures]
    assert "contract_login_200_token_mismatch" in codes
    # fail-closed: the follow-up token checks are skipped entirely
    names = [r["name"] for r in records]
    assert "me_200_with_token" not in names
    assert "gated_privacy_with_token" not in names
    assert "gated_privacy_wrong_token" not in names
    # the mismatch detail carries booleans, never the token or its type
    dumped = json.dumps(failures) + json.dumps(records)
    assert MOCK_TOKEN not in dumped
    assert "mac" not in dumped


def test_auth_on_contract_missing_token_type_fails_too():
    base = auth_smoke.DEFAULT_API_BASE
    http = auth_on_http()

    def no_type_login(b):
        if b.get("password") != auth_smoke.MOCK_LOGIN_PASS:
            return 401, {"detail": "wrong credentials"}
        return 200, {"access_token": MOCK_TOKEN}

    http._posts[base + "api/v1/auth/login"] = no_type_login
    records, failures = auth_smoke._host_auth_contract(
        http.get, http.post, base, expect_auth_on=True)
    assert "contract_login_200_token_mismatch" in [f["code"] for f in failures]
    assert "me_200_with_token" not in [r["name"] for r in records]


def test_auth_off_contract_records_ungated_facts_only():
    http = auth_off_http()
    records, failures = auth_smoke._host_auth_contract(
        http.get, http.post, auth_smoke.DEFAULT_API_BASE,
        expect_auth_on=False)
    assert failures == []
    assert [r["name"] for r in records] == ["auth_status",
                                            "ungated_privacy"]


def test_plan_only_serialization_is_secret_free(fake_repo):
    """The whole JSON result of a plan-only run carries no secrets."""
    result, _ = auth_smoke.run_auth_smoke(
        fake_repo,
        target="127.0.0.1:5555",
        hap="entry-default-signed.hap",
        expect_auth="on",
        confirm_mutation=False,
        runner=FakeRunner(),
        http_get=auth_on_http().get,
        http_post=auth_on_http().post,
        tool_resolver=ok_tool,
        target_resolver=ok_target,
        bundle_resolver=ok_bundle,
    )
    serialized = auth_smoke.render_json(result)
    for secret in REDACTION_FORBIDDEN_VALUES:
        assert secret not in serialized
    assert MOCK_TOKEN not in serialized
    # no raw argv / stdout / stderr keys are ever serialized
    assert '"argv"' not in serialized
    assert '"stdout"' not in serialized
    assert '"stderr"' not in serialized
    # no absolute host paths: the fake repo tmp path must not leak
    assert str(fake_repo) not in serialized


# ---------------------------------- 4. fail-closed orchestrator ----

def test_request_invalid_blocks_every_step_before_any_io():
    runner = FakeRunner()
    http = auth_on_http()
    result, exit_code = auth_smoke.run_auth_smoke(
        Path("."),
        target=None,  # missing target -> request failure
        hap=None,
        expect_auth="on",
        confirm_mutation=True,
        runner=runner,
        http_get=http.get,
        http_post=http.post,
        tool_resolver=ok_tool,
        target_resolver=auth_smoke.resolve_target,  # real: rejects None
        bundle_resolver=ok_bundle,
    )
    assert exit_code == auth_smoke.EXIT_BLOCKED
    assert all(s["status"] == "not_run" for s in result["steps"])
    assert all(s["reason"] == "request_invalid" for s in result["steps"])
    assert http.gets == [] and http.posts == []
    assert runner.calls == []
    assert result["commands_executed"] == 0


def test_non_loopback_api_base_blocks_as_request_invalid(fake_repo):
    runner = FakeRunner()
    http = auth_on_http()
    result, exit_code = auth_smoke.run_auth_smoke(
        fake_repo,
        target="127.0.0.1:5555",
        hap="entry-default-signed.hap",
        api_base="http://127.0.0.1.evil.example/",
        confirm_mutation=True,
        runner=runner,
        http_get=http.get,
        http_post=http.post,
        tool_resolver=ok_tool,
        target_resolver=ok_target,
        bundle_resolver=ok_bundle,
    )
    assert exit_code == auth_smoke.EXIT_BLOCKED
    codes = [f["code"] for f in result["request_failures"]]
    assert "api_base_not_loopback" in codes
    assert all(s["reason"] == "request_invalid" for s in result["steps"])
    assert runner.calls == []
    # the rejected URL is never echoed back
    assert "evil.example" not in auth_smoke.render_json(result)


def test_toolchain_unavailable_marks_steps_not_run_fail_closed(fake_repo):
    runner = FakeRunner()
    result, exit_code = auth_smoke.run_auth_smoke(
        fake_repo,
        target="127.0.0.1:5555",
        hap="entry-default-signed.hap",
        confirm_mutation=True,
        runner=runner,
        http_get=auth_on_http().get,
        http_post=auth_on_http().post,
        tool_resolver=down_tool,
        target_resolver=ok_target,
        bundle_resolver=ok_bundle,
    )
    assert exit_code == auth_smoke.EXIT_FAILURE
    assert [f["code"] for f in result["toolchain_failures"]] == \
        ["hdc_not_found"]
    assert all(s["status"] == "not_run" for s in result["steps"])
    assert all(s["reason"] == "toolchain_unavailable" for s in result["steps"])
    assert runner.calls == []
    assert result["commands_executed"] == 0
    assert result["mutation_performed"] is False


def test_confirmed_run_fails_closed_when_install_fails(fake_repo):
    """Mutation confirmed + install fails -> later steps not_run."""
    runner = FakeRunner(returncode=1)
    result, exit_code = auth_smoke.run_auth_smoke(
        fake_repo,
        target="127.0.0.1:5555",
        hap="entry-default-signed.hap",
        expect_auth="off",
        confirm_mutation=True,
        runner=runner,
        http_get=auth_off_http().get,
        http_post=auth_off_http().post,
        tool_resolver=ok_tool,
        target_resolver=ok_target,
        bundle_resolver=ok_bundle,
    )
    assert exit_code == auth_smoke.EXIT_FAILURE
    by_name = {s["name"]: s for s in result["steps"]}
    assert by_name["host_auth_contract"]["status"] == "ok"
    assert by_name["install"]["status"] == "failure"
    # fail-closed: every later step honestly not_run, nothing "ok" after
    for name in ("start", "settings_ui", "auth_off_local", "background"):
        assert by_name[name]["status"] == "not_run", name
    assert by_name["uninstall"]["status"] == "not_run"
    assert result["mutation_performed"] is False
    assert result["cleanup_attempted"] is True


def test_auth_off_flow_skips_auth_on_steps_in_plan(fake_repo):
    result, _ = auth_smoke.run_auth_smoke(
        fake_repo,
        target="127.0.0.1:5555",
        hap="entry-default-signed.hap",
        expect_auth="off",
        confirm_mutation=False,
        runner=FakeRunner(),
        http_get=auth_off_http().get,
        http_post=auth_off_http().post,
        tool_resolver=ok_tool,
        target_resolver=ok_target,
        bundle_resolver=ok_bundle,
    )
    assert result["expect_auth"] == "off"
    assert result["summary"]["not_run"] == 12


def test_step_order_is_the_documented_twelve():
    assert len(auth_smoke.STEP_ORDER) == 12
    assert auth_smoke.STEP_ORDER[0][0] == auth_smoke.STEP_HOST_AUTH_CONTRACT
    assert auth_smoke.STEP_ORDER[-1][0] == auth_smoke.STEP_UNINSTALL


# -------------------------------- M14-95: dynamic device URL derivation ----

@pytest.mark.parametrize("host_url,device_url", [
    # default: exact same port, scheme and trailing slash preserved
    ("http://127.0.0.1:8765/", "http://10.0.2.2:8765/"),
    # non-default dynamic port follows the host URL
    ("http://127.0.0.1:9123/", "http://10.0.2.2:9123/"),
    ("http://127.0.0.1:9123", "http://10.0.2.2:9123/"),
    # localhost loopback also maps to the emulator gateway
    ("http://localhost:8765/", "http://10.0.2.2:8765/"),
    # explicit default port normalizes the same as the implicit one
    ("http://127.0.0.1:80/", "http://10.0.2.2:80/"),
])
def test_device_api_base_derives_same_port(host_url, device_url):
    assert auth_smoke.device_api_base(host_url) == device_url
    # default contract is unchanged
    assert auth_smoke.device_api_base(auth_smoke.DEFAULT_API_BASE) == \
        "http://10.0.2.2:8765/"


@pytest.mark.parametrize("url", [
    "http://evil.example:8765/",
    "https://127.0.0.1:8765/",
    "http://127.0.0.1:8765/?x=1",
    "http://127.0.0.1:8765/#f",
    "http://127.0.0.1:8765/api",
    "http://u:p@127.0.0.1:8765/",
])
def test_device_api_base_rejects_unvalidated_urls(url):
    with pytest.raises(ValueError):
        auth_smoke.device_api_base(url)


def test_run_reports_derived_device_url_for_dynamic_port(fake_repo):
    """A non-default port flows into the settings typing/report fields."""
    http = FakeHttp(
        gets={
            "http://127.0.0.1:9123/api/v1/auth/status":
                (200, {"auth_enabled": False}),
        },
    )
    result, exit_code = auth_smoke.run_auth_smoke(
        fake_repo,
        target="127.0.0.1:5555",
        hap="entry-default-signed.hap",
        api_base="http://127.0.0.1:9123/",
        confirm_mutation=False,
        runner=FakeRunner(),
        http_get=http.get,
        http_post=http.post,
        tool_resolver=ok_tool,
        target_resolver=ok_target,
        bundle_resolver=ok_bundle,
    )
    assert exit_code == auth_smoke.EXIT_OK
    assert result["api_base"] == "http://127.0.0.1:9123/"
    assert result["device_api_base_typed"] == "http://10.0.2.2:9123/"


def test_run_reports_default_device_url(fake_repo):
    result, _ = auth_smoke.run_auth_smoke(
        fake_repo,
        target="127.0.0.1:5555",
        hap="entry-default-signed.hap",
        confirm_mutation=False,
        runner=FakeRunner(),
        http_get=auth_on_http().get,
        http_post=auth_on_http().post,
        tool_resolver=ok_tool,
        target_resolver=ok_target,
        bundle_resolver=ok_bundle,
    )
    assert result["api_base"] == auth_smoke.DEFAULT_API_BASE
    assert result["device_api_base_typed"] == "http://10.0.2.2:8765/"


def test_non_loopback_api_base_leaves_device_url_none(fake_repo):
    """Fail-closed: a rejected host URL blocks the run and the derived
    device URL is never invented."""
    result, exit_code = auth_smoke.run_auth_smoke(
        fake_repo,
        target="127.0.0.1:5555",
        hap="entry-default-signed.hap",
        api_base="http://127.0.0.1.evil.example/",
        confirm_mutation=True,
        runner=FakeRunner(),
        http_get=auth_on_http().get,
        http_post=auth_on_http().post,
        tool_resolver=ok_tool,
        target_resolver=ok_target,
        bundle_resolver=ok_bundle,
    )
    assert exit_code == auth_smoke.EXIT_BLOCKED
    assert "api_base_not_loopback" in [
        f["code"] for f in result["request_failures"]]
    assert result["device_api_base_typed"] is None
    serialized = auth_smoke.render_json(result)
    assert "10.0.2.2" not in serialized
    assert "evil.example" not in serialized


def test_report_and_serialization_stay_secret_free_with_dynamic_port(
        fake_repo):
    """Dynamic-port result: redaction contract still holds end-to-end."""
    result, _ = auth_smoke.run_auth_smoke(
        fake_repo,
        target="127.0.0.1:5555",
        hap="entry-default-signed.hap",
        api_base="http://127.0.0.1:9123/",
        expect_auth="on",
        confirm_mutation=False,
        runner=FakeRunner(),
        http_get=auth_on_http().get,
        http_post=auth_on_http().post,
        tool_resolver=ok_tool,
        target_resolver=ok_target,
        bundle_resolver=ok_bundle,
    )
    serialized = auth_smoke.render_json(result)
    for secret in REDACTION_FORBIDDEN_VALUES:
        assert secret not in serialized
    assert MOCK_TOKEN not in serialized
    assert str(fake_repo) not in serialized
    # host URL appears in the report; device URL is derived, same port
    assert "http://127.0.0.1:9123/" in serialized
    assert "http://10.0.2.2:9123/" in serialized

# ------------------------- M14-99 B2: dump-failure honesty in poll ------


class _FakePollDriver:
    """Minimal driver with only dump(): successive (parsed, failures)
    pairs from the queue; after the queue is empty the last pair
    repeats. No emulator, no hdc."""

    def __init__(self, queue):
        self._queue = list(queue)
        self._last = queue[-1] if queue else (None, [{"code": "layout_unreadable"}])
        self.calls = 0

    def click(self, x, y):
        """No-op: the fake driver does not drive real hdc."""

    def dump(self):
        self.calls += 1
        if self._queue:
            self._last = self._queue.pop(0)
        return self._last


def _poll_layout(texts):
    """Minimal parsed layout: one Root whose Text children carry the
    given strings. layout_texts() only collects entries that live
    under an ``attributes`` dict, so each node is wrapped that way."""
    return {
        "attributes": {"type": "Root"},
        "children": [
            {"attributes": {"type": "Text", "text": t,
                           "bounds": "[0,0][100,30]"}}
            for t in texts
        ],
    }


def test_b2_poll_loading_then_settled_stops_and_returns_settled(
        monkeypatch):
    """B2-1: first dump still loading, second dump settled ->
    settled texts returned, polling stops after exactly 2 dumps."""
    monkeypatch.setattr(
        auth_smoke, "HOME_ZONE_LOADING_DEADLINE_SECONDS", 1.0)
    monkeypatch.setattr(
        auth_smoke, "HOME_ZONE_LOADING_POLL_INTERVAL_SECONDS", 0.0)
    driver = _FakePollDriver([
        (_poll_layout(["加载中…", "首页"]), []),
        (_poll_layout(["模型路由", "首页", "远程模式(认证已开启)"]), []),
    ])
    texts, failures = auth_smoke._poll_home_zones_settled(driver)
    assert driver.calls == 2
    assert failures == []
    assert texts is not None
    assert "模型路由" in [t for t, _b in texts]


def test_b2_poll_persistent_loading_yields_still_loading_code(
        monkeypatch):
    """B2-2: 加载中… on every dump reaches the (fake) deadline and
    yields exactly home_zones_still_loading."""
    monkeypatch.setattr(
        auth_smoke, "HOME_ZONE_LOADING_DEADLINE_SECONDS", 0.0)
    monkeypatch.setattr(
        auth_smoke, "HOME_ZONE_LOADING_POLL_INTERVAL_SECONDS", 0.0)
    driver = _FakePollDriver([(_poll_layout(["加载中…"]), [])])
    texts, failures = auth_smoke._poll_home_zones_settled(driver)
    assert texts is None
    assert failures == [{"code": "home_zones_still_loading"}]


def test_b2_poll_dump_failure_returns_immediately_with_real_code(
        monkeypatch):
    """B2-3: a failed dump returns at once with its own failure
    code - the (deliberately huge) deadline is never consulted."""
    monkeypatch.setattr(
        auth_smoke, "HOME_ZONE_LOADING_DEADLINE_SECONDS", 999.0)
    monkeypatch.setattr(
        auth_smoke, "HOME_ZONE_LOADING_POLL_INTERVAL_SECONDS", 0.0)
    driver = _FakePollDriver(
        [(None, [{"code": "layout_invalid_json"}])])
    texts, failures = auth_smoke._poll_home_zones_settled(driver)
    assert driver.calls == 1
    assert texts is None
    assert failures == [{"code": "layout_invalid_json"}]
    assert "home_zones_still_loading" not in [f["code"] for f in failures]


def test_b2_poll_dump_failure_empty_list_labels_layout_unreadable(
        monkeypatch):
    """B2-3b: failed dump with an EMPTY failure list is labeled
    layout_unreadable (the documented fallback), not still-loading."""
    monkeypatch.setattr(
        auth_smoke, "HOME_ZONE_LOADING_DEADLINE_SECONDS", 999.0)
    monkeypatch.setattr(
        auth_smoke, "HOME_ZONE_LOADING_POLL_INTERVAL_SECONDS", 0.0)
    driver = _FakePollDriver([(None, [])])
    texts, failures = auth_smoke._poll_home_zones_settled(driver)
    assert driver.calls == 1
    assert texts is None
    assert failures == [{"code": "layout_unreadable"}]


def test_b2_poll_terminal_401_or_error_resolves_on_first_dump(
        monkeypatch):
    """B2-4: terminal non-loading text (401/ERROR) stops polling
    on the first dump."""
    monkeypatch.setattr(
        auth_smoke, "HOME_ZONE_LOADING_DEADLINE_SECONDS", 1.0)
    monkeypatch.setattr(
        auth_smoke, "HOME_ZONE_LOADING_POLL_INTERVAL_SECONDS", 0.0)
    driver = _FakePollDriver(
        [(_poll_layout(["HTTP 401", "重试", "首页"]), [])])
    texts, failures = auth_smoke._poll_home_zones_settled(driver)
    assert driver.calls == 1
    assert failures == []
    assert "HTTP 401" in [t for t, _b in texts]


def test_b2_refresh_home_and_dump_propagates_dump_failure(
        monkeypatch):
    """B2-5: _refresh_home_and_dump honestly propagates the real
    dump failure from the poll instead of a bare None."""
    monkeypatch.setattr(
        auth_smoke, "HOME_ZONE_LOADING_DEADLINE_SECONDS", 0.0)
    monkeypatch.setattr(
        auth_smoke, "HOME_ZONE_LOADING_POLL_INTERVAL_SECONDS", 0.0)
    home_ok = _poll_layout(
        ["首页", "设置", "整体刷新", "远程模式(认证已开启)"])
    driver = _FakePollDriver([
        (home_ok, []),
        (None, [{"code": "layout_pull_failed"}]),
    ])
    texts, failures = auth_smoke._refresh_home_and_dump(driver)
    assert texts is None
    codes = [f["code"] for f in failures]
    assert "layout_pull_failed" in codes
    assert "home_zones_still_loading" not in codes

# -------------------- M14-187 R2: typed URL-input locator --------------------

def _settings_node(ntype, text, bounds):
    return {"attributes": {"type": ntype, "text": text,
                               "bounds": bounds}}


def _m14_187_settings_layout():
    """Attempt-1 real-world Settings layout (diag attempt 2): a
    TextInput holding the base URL PLUS DownloadPane readonly
    Text that is LONGER than the input content."""
    return {
        "attributes": {"type": "Root"},
        "children": [
            _settings_node("Text", "设置", "[912,1322][1008,1358]"),
            _settings_node(
                "TextInput", "http://10.0.2.2:60880/",
                "[36,1136][1008,1224]"),
            _settings_node(
                "Text", "服务地址: http://10.0.2.2:60880/",
                "[36,844][1008,880]"),
        ],
    }


def test_find_url_input_prefers_typed_textinput_over_longer_readonly_text():
    """M14-187 attempt-1 regression: with a real TextInput and a
    LONGER readonly Text both carrying URLs, the TextInput must win -
    the old longest-text heuristic picked the Text and the whole
    settings_ui step failed settings_input_mismatch x5."""
    typed = auth_smoke.layout_typed(_m14_187_settings_layout())
    field = auth_smoke.find_url_input(typed)
    assert field is not None
    x, y, content = field
    assert content == "http://10.0.2.2:60880/"
    # the TextInput center, NOT the readonly Text center
    assert (x, y) == ((36 + 1008) // 2, (1136 + 1224) // 2)
    # and never the readonly Text bounds
    assert (x, y) != ((36 + 1008) // 2, (844 + 880) // 2)


def test_find_url_input_returns_none_without_typed_textinput():
    """No TextInput in the tree -> None (fail-closed), even when a
    readonly Text carries a long URL: the old heuristic would have
    returned the Text and steered keystrokes onto it."""
    layout = {
        "attributes": {"type": "Root"},
        "children": [
            _settings_node(
                "Text", "服务地址: http://10.0.2.2:60880/",
                "[36,844][1008,880]"),
        ],
    }
    field = auth_smoke.find_url_input(auth_smoke.layout_typed(layout))
    assert field is None


def test_find_url_input_empty_input_falls_back_to_first_textinput():
    """Empty TextInput renders no text content: the compatibility
    fallback returns the first TextInput in tree order (the URL
    field is the pane topmost input) so a pre-existing URL never
    blocks locating the field."""
    layout = {
        "attributes": {"type": "Root"},
        "children": [
            _settings_node("TextInput", "", "[36,1136][1008,1224]"),
            _settings_node("TextInput", "", "[36,1420][1008,1508]"),
        ],
    }
    field = auth_smoke.find_url_input(auth_smoke.layout_typed(layout))
    assert field is not None
    x, y, content = field
    assert content == ""
    assert (x, y) == ((36 + 1008) // 2, (1136 + 1224) // 2)


def test_find_url_input_ignores_url_text_after_textinput():
    """Tree order must not matter: the readonly Text may come AFTER
    the TextInput; it still never steals the locator."""
    layout = {
        "attributes": {"type": "Root"},
        "children": [
            _settings_node("TextInput", "http://10.0.2.2:8765/",
                           "[36,1136][1008,1224]"),
            _settings_node(
                "Text", "服务地址: http://10.0.2.2:60880/extra/path",
                "[36,844][1008,880]"),
        ],
    }
    field = auth_smoke.find_url_input(auth_smoke.layout_typed(layout))
    assert field is not None
    assert field[2] == "http://10.0.2.2:8765/"


# -------------------- M14-196 R6: caret-menu URL clear + focused text ----

import ast as _ast


def _func_source(name, cls=None):
    """Verbatim source of a module function (or method of UiDriver)."""
    src = Path(auth_smoke.__file__).read_text(encoding="utf-8")
    tree = _ast.parse(src)
    scopes = [tree]
    if cls is not None:
        scopes = [
            n for n in tree.body
            if isinstance(n, _ast.ClassDef) and n.name == cls]
    for scope in scopes:
        for node in scope.body:
            if isinstance(node, _ast.FunctionDef) and node.name == name:
                seg = _ast.get_source_segment(src, node)
                assert seg is not None
                return seg
    raise AssertionError("function not found: " + str(name))


def test_r6_type_url_field_removed_and_no_coordinate_inputtext_in_flow():
    """R5 root cause locked out: the coordinate-inputText typing path
    is gone from the module and from every function of the Settings
    URL flow."""
    assert not hasattr(auth_smoke.UiDriver, "type_url_field")
    for func in (
        ("_drive_settings_url", None),
        ("_clear_url_field_via_menu", None),
        ("focused_text", "UiDriver"),
        ("clear_url_field_via_menu", "UiDriver"),
    ):
        src = _func_source(*func)
        assert '"inputText"' not in src, func


def test_r6_verify_requires_exact_equality_not_substring():
    """The verify gate must demand byte-for-byte equality; the old
    substring acceptance that let the R5 corrupted value through
    is banned from the flow."""
    src = _func_source("_drive_settings_url")
    assert "vfield[2] != device_base" in src
    assert "device_base not in vfield[2]" not in src
    assert "exact_match_required" in src


def test_r6_focused_text_uses_coordinate_free_uiinput_text():
    """Focused entry routes through uiInput text with NO x/y args."""
    runner = FakeRunner()
    driver = auth_smoke.UiDriver(
        runner=runner, program="hdc", target="127.0.0.1:5555",
        bundle="com.example.fake", ability="EntryAbility",
        timeout=1.0, local_layout=Path("layout.json"))
    url = "http://10.0.2.2:8765/"
    driver.focused_text(url)
    assert len(runner.calls) == 1
    argv = runner.calls[0]
    assert argv[-5:] == ("shell", "uitest", "uiInput", "text", url)
    assert "inputText" not in argv


def test_r6_focused_text_chunks_long_input():
    """>64 chars: chunked shell calls, concatenation exact, still no
    coordinates anywhere in any chunk argv."""
    runner = FakeRunner()
    driver = auth_smoke.UiDriver(
        runner=runner, program="hdc", target="127.0.0.1:5555",
        bundle="com.example.fake", ability="EntryAbility",
        timeout=1.0, local_layout=Path("layout.json"))
    text = "http://10.0.2.2:" + "9" * 150 + "/"
    driver.focused_text(text)
    chunks = [a[-1] for a in runner.calls]
    assert "".join(chunks) == text
    assert all(len(c) <= auth_smoke.TEXT_INPUT_CHUNK for c in chunks)
    for argv in runner.calls:
        assert argv[-5:-1] == ("shell", "uitest", "uiInput", "text")
        assert "inputText" not in argv


def test_r6_clear_url_field_longclick_command():
    """Stage-1 command is exactly a longClick at the field center."""
    runner = FakeRunner()
    driver = auth_smoke.UiDriver(
        runner=runner, program="hdc", target="127.0.0.1:5555",
        bundle="com.example.fake", ability="EntryAbility",
        timeout=1.0, local_layout=Path("layout.json"))
    driver.clear_url_field_via_menu(660, 469)
    argv = runner.calls[0]
    assert argv[-6:] == ("shell", "uitest", "uiInput",
                         "longClick", "660", "469")


class _FakeSettingsDriver:
    """UiDriver stand-in scripted by a dump queue; records every
    driving action so tests can prove the executed flow."""

    def __init__(self, dumps):
        self._queue = list(dumps)
        self._last = dumps[-1] if dumps else None
        self.actions: list[tuple[str, object]] = []

    def dump(self):
        if self._queue:
            self._last = self._queue.pop(0)
        return self._last, []

    def _act(self, name, arg=None):
        self.actions.append((name, arg))

    def click(self, x, y):
        self._act("click", (x, y))

    def clear_url_field_via_menu(self, x, y):
        self._act("longClick", (x, y))

    def focused_text(self, text):
        self._act("focused_text", text)

    def dismiss_ime(self):
        self._act("dismiss_ime")

    def restart_ability(self):
        self._act("restart_ability")

    def ensure_foreground(self):
        return []

    def window_snapshot(self):
        return {}

    def digest(self):
        return {"filename": "l.json", "size_bytes": 1,
                "sha256": "A" * 8, "content_recorded": False}


def _r6_layout(nodes):
    return {"attributes": {"type": "Root"},
            "children": [_settings_node(*n) for n in nodes]}


URL_FIELD = ("TextInput", "http://10.0.2.2:60880/",
             "[36,1136][1008,1224]")
SETTINGS_TAB = ("Text", "设置", "[912,1322][1008,1358]")
# R10/R11: menu-stage dumps must carry STRONG Settings pane
# evidence (header + action button) alongside the IME menu
# action itself.
SETTINGS_HEADER = ("Text", "AIOS 服务地址",
                   "[36,180][640,226]")
SETTINGS_SAVE = ("Text", "保存", "[36,1240][200,1276]")
MENU_EVIDENCE = [SETTINGS_HEADER, SETTINGS_SAVE]


def test_r6_clear_via_menu_happy_path(monkeypatch):
    """longClick -> 全选 -> 剪切 -> dump proves the field EMPTY; the
    actions are exactly the three clicks at the layout centers."""
    monkeypatch.setattr(auth_smoke.time, "sleep", lambda s: None)
    driver = _FakeSettingsDriver([
        _r6_layout([*MENU_EVIDENCE, SETTINGS_TAB, URL_FIELD,
                    ("Text", "全选", "[174,310][273,367]")]),
        _r6_layout([*MENU_EVIDENCE, SETTINGS_TAB, URL_FIELD,
                    ("Text", "剪切", "[119,254][218,311]")]),
        _r6_layout([SETTINGS_TAB,
                    ("TextInput", "", "[36,1136][1008,1224]")]),
    ])
    cleared, failures = auth_smoke._clear_url_field_via_menu(
        driver, 522, 1180, 1)
    assert cleared is True
    assert failures == []
    assert driver.actions == [
        ("longClick", (522, 1180)),
        ("click", ((174 + 273) // 2, (310 + 367) // 2)),
        ("click", ((119 + 218) // 2, (254 + 311) // 2)),
    ]


def test_r6_clear_via_menu_fails_closed_without_select_all(monkeypatch):
    monkeypatch.setattr(auth_smoke.time, "sleep", lambda s: None)
    driver = _FakeSettingsDriver([
        # strong pane evidence + visible 剪切 but NO 全选:
        # menu-stage ownership passes, the exact-match search for
        # 全选 then fails -> select_all_not_found stays reachable
        _r6_layout([*MENU_EVIDENCE, SETTINGS_TAB, URL_FIELD,
                    ("Text", "剪切", "[119,254][218,311]")]),
    ])
    cleared, failures = auth_smoke._clear_url_field_via_menu(
        driver, 522, 1180, 1)
    assert cleared is False
    codes = [f["code"] for f in failures]
    assert codes == ["settings_menu_select_all_not_found"]
    detail = failures[0]["detail"]
    assert detail["layout_digest"]["content_recorded"] is False
    assert detail["node_text_count"] == 5
    # nothing was typed, no cut attempted
    assert [a[0] for a in driver.actions] == ["longClick"]


def test_r6_clear_via_menu_fails_closed_when_field_not_empty(monkeypatch):
    monkeypatch.setattr(auth_smoke.time, "sleep", lambda s: None)
    driver = _FakeSettingsDriver([
        _r6_layout([*MENU_EVIDENCE, SETTINGS_TAB, URL_FIELD,
                    ("Text", "全选", "[174,310][273,367]")]),
        _r6_layout([*MENU_EVIDENCE, SETTINGS_TAB, URL_FIELD,
                    ("Text", "剪切", "[119,254][218,311]")]),
        # cut tapped but the field still holds stale text
        _r6_layout([SETTINGS_TAB, URL_FIELD]),
    ])
    cleared, failures = auth_smoke._clear_url_field_via_menu(
        driver, 522, 1180, 1)
    assert cleared is False
    assert [f["code"] for f in failures] == ["settings_clear_not_proven"]
    assert failures[0]["detail"]["cleared_text_empty"] is False
    assert failures[0]["detail"]["layout_digest"][
           "content_recorded"] is False


def test_r6_clear_via_menu_fails_closed_on_foreground_loss(monkeypatch):
    monkeypatch.setattr(auth_smoke.time, "sleep", lambda s: None)
    driver = _FakeSettingsDriver([
        # a foreign app screen: no ownership markers at all
        _r6_layout([("Text", "Unrelated App", "[0,0][100,40]")]),
    ])
    cleared, failures = auth_smoke._clear_url_field_via_menu(
        driver, 522, 1180, 1)
    assert cleared is False
    assert failures[0]["code"] == "settings_foreground_lost"
    assert failures[0]["detail"]["stage"] == "menu"


def _r6_full_flow_dumps(final_url, verify_url=None):
    """Dump queue for one full _drive_settings_url attempt."""
    verify_url = final_url if verify_url is None else verify_url
    return [
        # _goto_settings: Settings tab dump
        _r6_layout([SETTINGS_TAB, URL_FIELD]),
        # step-2 ownership dump
        _r6_layout([SETTINGS_TAB, URL_FIELD]),
        # menu dumps 1-3 (strong pane evidence + typed menu action)
        _r6_layout([*MENU_EVIDENCE, SETTINGS_TAB, URL_FIELD,
                    ("Text", "全选", "[174,310][273,367]")]),
        _r6_layout([*MENU_EVIDENCE, SETTINGS_TAB, URL_FIELD,
                    ("Text", "剪切", "[119,254][218,311]")]),
        _r6_layout([SETTINGS_TAB,
                    ("TextInput", "", "[36,1136][1008,1224]"),
                    ("Text", "保存", "[36,1240][200,1276]")]),
        # verify dump
        _r6_layout([SETTINGS_TAB,
                    ("TextInput", verify_url, "[36,1136][1008,1224]"),
                    ("Text", "保存", "[500,1240][560,1276]")]),
        # final dump after Save
        _r6_layout([SETTINGS_TAB,
                    ("Text", "已保存: " + final_url,
                     "[36,1240][1008,1276]"),
                    ("Text", "保存", "[36,640][200,690]")]),
    ]


def test_r6_drive_settings_url_full_flow_no_coordinate_inputtext(
        monkeypatch):
    """End-to-end over the fake: menu clear -> focused text -> exact
    verify -> Save confirmed; no coordinate inputText is ever part
    of the executed action list."""
    monkeypatch.setattr(auth_smoke.time, "sleep", lambda s: None)
    driver = _FakeSettingsDriver(_r6_full_flow_dumps(
        "http://10.0.2.2:8765/"))
    ok, failures = auth_smoke._drive_settings_url(
        driver, "http://10.0.2.2:8765/")
    assert ok is True
    assert failures == []
    names = [a[0] for a in driver.actions]
    assert "longClick" in names
    assert "focused_text" in names
    typed = [a[1] for a in driver.actions if a[0] == "focused_text"]
    assert typed == ["http://10.0.2.2:8765/"]
    assert "dismiss_ime" in names
    assert "restart_ability" in names  # cold restart after save


def test_r6_drive_settings_url_rejects_r5_corrupted_prefix(
        monkeypatch):
    """R5 regression: the verify field reads "/http://10.0.2.2:8765/"
    - the OLD substring check accepted it; exact equality must
    fail the attempt AT ONCE with settings_input_mismatch
    (SETTINGS_MAX_ATTEMPTS untouched - fail closed after one cycle)."""
    monkeypatch.setattr(auth_smoke.time, "sleep", lambda s: None)
    driver = _FakeSettingsDriver(_r6_full_flow_dumps(
        "http://10.0.2.2:8765/",
        verify_url="/http://10.0.2.2:8765/"))
    ok, failures = auth_smoke._drive_settings_url(
        driver, "http://10.0.2.2:8765/")
    assert ok is False
    mismatch = [f for f in failures
                if f["code"] == "settings_input_mismatch"]
    assert len(mismatch) == 1
    detail = mismatch[0]["detail"]
    assert detail["exact_match_required"] is True
    assert detail["field_matches"] is False
    assert detail["layout_digest"]["content_recorded"] is False


def test_r6_mismatch_fails_closed_after_one_clear_type_cycle(
        monkeypatch):
    """Supervisor R1: with SETTINGS_MAX_ATTEMPTS at its DEFAULT value
    (5, unpatched), one exact-equality mismatch ends the step - exactly
    one clear/type cycle executes, no blind malformed retries."""
    monkeypatch.setattr(auth_smoke.time, "sleep", lambda s: None)
    assert auth_smoke.SETTINGS_MAX_ATTEMPTS == 5  # default, untouched
    driver = _FakeSettingsDriver(_r6_full_flow_dumps(
        "http://10.0.2.2:8765/",
        verify_url="/http://10.0.2.2:8765/"))
    ok, failures = auth_smoke._drive_settings_url(
        driver, "http://10.0.2.2:8765/")
    assert ok is False
    mismatch = [f for f in failures
                if f["code"] == "settings_input_mismatch"]
    assert len(mismatch) == 1
    assert mismatch[0]["detail"]["attempt"] == 1
    names = [a[0] for a in driver.actions]
    # exactly ONE caret-menu clear and ONE typed entry ever executed
    assert names.count("longClick") == 1
    assert names.count("focused_text") == 1
    # nothing ran past the mismatch: no save confirmation path taken
    assert "restart_ability" not in names


def test_r6_drive_settings_url_nonforeground_clear_failure_stops_at_once(
        monkeypatch):
    """Supervisor R2: a NON-foreground clear failure (caret menu
    never opened) is deterministic - the step fails after ONE cycle
    with the existing evidence; no blind SETTINGS_MAX_ATTEMPTS
    retries, nothing is ever typed."""
    monkeypatch.setattr(auth_smoke.time, "sleep", lambda s: None)
    assert auth_smoke.SETTINGS_MAX_ATTEMPTS == 5  # default, untouched
    driver = _FakeSettingsDriver([
        # navigation + ownership dumps: our Settings screen
        _r6_layout([SETTINGS_TAB, URL_FIELD]),
        _r6_layout([SETTINGS_TAB, URL_FIELD]),
        # caret menu dump: strong pane evidence + 剪切 visible
        # but NO 全选 (R11 shape) -> select_all_not_found,
        # the deterministic one-cycle stop supervisor R2 guards
        _r6_layout([*MENU_EVIDENCE, SETTINGS_TAB, URL_FIELD,
                    ("Text", "剪切", "[119,254][218,311]")]),
    ])
    ok, failures = auth_smoke._drive_settings_url(
        driver, "http://10.0.2.2:8765/")
    assert ok is False
    assert [f["code"] for f in failures] == [
        "settings_menu_select_all_not_found"]
    names = [a[0] for a in driver.actions]
    # exactly one clear attempt, nothing typed, IME dismissed, no restart
    assert names.count("longClick") == 1
    assert "focused_text" not in names
    assert "dismiss_ime" in names
    assert "restart_ability" not in names


def test_r6_drive_settings_url_foreground_clear_failure_still_retries(
        monkeypatch):
    """Foreground clear losses KEEP the retry loop (R6 contract):
    the clear-stage settings_foreground_lost does not abort the step
    at once - later attempts re-guard and recover via restart_ability
    exactly like every other foreground loss."""
    monkeypatch.setattr(auth_smoke.time, "sleep", lambda s: None)
    driver = _FakeSettingsDriver([
        _r6_layout([SETTINGS_TAB, URL_FIELD]),
        _r6_layout([SETTINGS_TAB, URL_FIELD]),
        # caret-menu dump lands on a foreign screen
        _r6_layout([("Text", "Unrelated App", "[0,0][100,40]")]),
    ])
    ok, failures = auth_smoke._drive_settings_url(
        driver, "http://10.0.2.2:8765/")
    assert ok is False
    clear_stage = [f for f in failures
                   if f["code"] == "settings_foreground_lost"
                   and f.get("detail", {}).get("stage") == "menu"]
    assert len(clear_stage) == 1
    # the loop continued: the repeated foreign ownership dump drove
    # the documented restart/retry path on later attempts
    assert "restart_ability" in [a[0] for a in driver.actions]

    assert "restart_ability" in [a[0] for a in driver.actions]


# ------------- M14-196 R7: strong-evidence Settings ownership ----------

REAL_LAYOUT_PATH = Path(__file__).parent / "fixtures" / (
    "m14_196_r6_ime_selectmenu_layout.json")


def _load_real_dump():
    """The R6 stage-b real dump (gitignored .verify copy); skipped
    when the worktree evidence dir is absent (e.g. CI)."""
    if not REAL_LAYOUT_PATH.is_file():
        pytest.skip("real stage-b layout fixture not present")
    return json.loads(REAL_LAYOUT_PATH.read_text(encoding="utf-8"))


def test_r7_real_dump_huawei_ime_selectmenu_ownership(monkeypatch):
    """Stage B real-layout regression (task goal 4): the REAL R6
    stage-b dump - Huawei IME SelectMenu over our own Settings pane,
    bottom tab bar covered - must be judged OURS by the new
    menu-stage and pane ownership, while the old tab-only check
    fails on it (that false negative WAS the settings_foreground_lost
    x5 root cause)."""
    real = _load_real_dump()
    texts = auth_smoke.layout_texts(real)
    typed = auth_smoke.layout_typed(real)
    joined = "\n".join(t for t, _b in texts)
    # root-cause shape: our Settings pane fully rendered...
    assert "AIOS 服务地址" in joined
    assert auth_smoke.find_url_input(typed) is not None
    assert "保存" in joined and "测试连接" in joined
    # ...the IME SelectMenu is open (typed menu actions)...
    assert "全选" in joined and "剪切" in joined
    # ...and the bottom tab bar is NOT visible (the misjudge cause)
    assert auth_smoke.SETTINGS_TAB_TEXT not in joined
    assert auth_smoke.HOME_TAB_TEXT not in joined
    # old check fails on our own pane (the R6 defect)...
    assert auth_smoke._owns_layout(texts) is False
    # ...the new R7 checks own it (strong combination holds)
    assert auth_smoke._owns_settings_menu_stage(texts, typed) is True
    assert auth_smoke._owns_settings_pane(texts, typed) is True


def test_r7_foreign_app_layout_fails_closed():
    """A foreign app screen (no Settings evidence at all) must never
    pass any R7 ownership check - fail-closed preserved."""
    layout = _r6_layout([("Text", "Unrelated App", "[0,0][100,40]")])
    texts = auth_smoke.layout_texts(layout)
    typed = auth_smoke.layout_typed(layout)
    assert auth_smoke._owns_layout(texts) is False
    assert auth_smoke._has_settings_strong_evidence(texts, typed) is False
    assert auth_smoke._owns_settings_pane(texts, typed) is False
    assert auth_smoke._owns_settings_menu_stage(texts, typed) is False


def test_r7_weak_single_text_never_passes():
    """Weak evidence alone (header only / menu action only / header
    + menu action without a TextInput) must NOT prove ownership."""
    header_only = _r6_layout(
        [("Text", "AIOS 服务地址", "[0,100][400,150]")])
    ht, hd = auth_smoke.layout_texts(header_only), \
        auth_smoke.layout_typed(header_only)
    assert auth_smoke._has_settings_strong_evidence(ht, hd) is False
    assert auth_smoke._owns_settings_pane(ht, hd) is False

    menu_only = _r6_layout([("Text", "全选", "[0,100][400,150]")])
    mt, md = auth_smoke.layout_texts(menu_only), \
        auth_smoke.layout_typed(menu_only)
    assert auth_smoke._owns_settings_menu_stage(mt, md) is False

    # header + menu action but NO TextInput node: still weak
    no_input = _r6_layout([
        ("Text", "AIOS 服务地址", "[0,100][400,150]"),
        ("Text", "全选", "[0,200][400,250]")])
    nt, nd = auth_smoke.layout_texts(no_input), \
        auth_smoke.layout_typed(no_input)
    assert auth_smoke._has_settings_strong_evidence(nt, nd) is False
    assert auth_smoke._owns_settings_menu_stage(nt, nd) is False


def test_r7_real_settings_pane_without_tab_bar_passes():
    """Task goal 3: a REAL Settings pane dump with NO bottom tab bar
    (IME covering it) passes via the strong combination: header +
    URL TextInput + 保存 (and 测试连接)."""
    layout = _r6_layout([
        ("Text", "AIOS 服务地址", "[56,252][640,298]"),
        ("TextInput", "https://ndtool.cn/aios/", "[56,399][1264,539]"),
        ("Text", "保存", "[56,640][300,690]"),
        ("Text", "测试连接", "[420,640][700,690]"),
    ])
    texts = auth_smoke.layout_texts(layout)
    typed = auth_smoke.layout_typed(layout)
    assert auth_smoke.SETTINGS_TAB_TEXT not in \
        "\n".join(t for t, _b in texts)
    assert auth_smoke._owns_layout(texts) is False
    assert auth_smoke._has_settings_strong_evidence(texts, typed) is True
    assert auth_smoke._owns_settings_pane(texts, typed) is True


def test_r7_initial_ownership_uses_strong_evidence(monkeypatch):
    """Task goal 3 (flow level): _drive_settings_url's INITIAL
    ownership check accepts a no-tab-bar Settings pane via strong
    evidence and proceeds through the full flow; only the caret-menu
    dumps use the menu-stage rule."""
    monkeypatch.setattr(auth_smoke.time, "sleep", lambda s: None)
    driver = _FakeSettingsDriver([
        # _goto_settings dump: pane without tab bar (strong evidence)
        _r6_layout([
            ("Text", "AIOS 服务地址", "[56,252][640,298]"),
            ("TextInput", "http://10.0.2.2:60880/",
             "[36,1136][1008,1224]"),
            ("Text", "保存", "[36,1240][200,1276]"),
        ]),
        # step-2 ownership dump: same no-tab pane
        _r6_layout([
            ("Text", "AIOS 服务地址", "[56,252][640,298]"),
            ("TextInput", "http://10.0.2.2:60880/",
             "[36,1136][1008,1224]"),
            ("Text", "保存", "[36,1240][200,1276]"),
        ]),
        # caret-menu dump 1: SelectMenu over the pane (no tab bar)
        _r6_layout([
            ("Text", "AIOS 服务地址", "[56,252][640,298]"),
            ("TextInput", "http://10.0.2.2:60880/",
             "[36,1136][1008,1224]"),
            ("Text", "保存", "[36,1240][200,1276]"),
            ("Text", "全选", "[174,310][273,367]"),
        ]),
        # caret-menu dump 2: 剪切 visible
        _r6_layout([
            ("Text", "AIOS 服务地址", "[56,252][640,298]"),
            ("TextInput", "http://10.0.2.2:60880/",
             "[36,1136][1008,1224]"),
            ("Text", "保存", "[36,1240][200,1276]"),
            ("Text", "剪切", "[119,254][218,311]"),
        ]),
        # cleared-field dump (pane evidence, no tab bar)
        _r6_layout([
            ("Text", "AIOS 服务地址", "[56,252][640,298]"),
            ("TextInput", "", "[36,1136][1008,1224]"),
            ("Text", "保存", "[36,1240][200,1276]"),
        ]),
        # verify dump: typed URL exact + Save button
        _r6_layout([
            ("Text", "AIOS 服务地址", "[56,252][640,298]"),
            ("TextInput", "http://10.0.2.2:8765/",
             "[36,1136][1008,1224]"),
            ("Text", "保存", "[36,1240][200,1276]"),
        ]),
        # final dump: save confirmed (pane keeps the URL input
        # rendered - strong evidence holds without a tab bar)
        _r6_layout([
            ("Text", "AIOS 服务地址", "[56,252][640,298]"),
            ("TextInput", "http://10.0.2.2:8765/",
             "[36,1136][1008,1224]"),
            ("Text", "已保存: http://10.0.2.2:8765/",
             "[36,1240][1008,1276]"),
            ("Text", "保存", "[36,640][200,690]"),
        ]),
    ])
    ok, failures = auth_smoke._drive_settings_url(
        driver, "http://10.0.2.2:8765/")
    assert ok is True
    assert failures == []
    names = [a[0] for a in driver.actions]
    assert names.count("longClick") == 1
    assert "focused_text" in names
    assert "restart_ability" in names  # cold restart after save


def test_r7_menu_stage_requires_menu_action():
    """Menu stage without a typed IME menu action (全选/剪切 both
    absent) fails closed even when the pane itself is strong."""
    layout = _r6_layout([
        ("Text", "AIOS 服务地址", "[56,252][640,298]"),
        ("TextInput", "https://ndtool.cn/aios/", "[56,399][1264,539]"),
        ("Text", "保存", "[56,640][300,690]"),
        ("Text", "测试连接", "[420,640][700,690]"),
    ])
    texts = auth_smoke.layout_texts(layout)
    typed = auth_smoke.layout_typed(layout)
    # pane-level passes (strong evidence, no menu open)...
    assert auth_smoke._owns_settings_pane(texts, typed) is True
    # ...but the menu stage demands a visible menu action
    assert auth_smoke._owns_settings_menu_stage(texts, typed) is False


# ------------- M14-196 R10: menu-stage shortcut removal ----------

def test_r10_menu_stage_tab_only_layout_without_strong_evidence():
    """R10: a TAB-ONLY layout (设置 tab visible) with NO strong
    Settings evidence and NO typed menu action must NOT pass the
    menu-stage ownership check - the deleted _owns_layout shortcut
    used to wave exactly such screens through. Fail-closed restored."""
    layout = _r6_layout([SETTINGS_TAB])
    texts = auth_smoke.layout_texts(layout)
    typed = auth_smoke.layout_typed(layout)
    # the plain tab-bar check alone would own this layout...
    assert auth_smoke._owns_layout(texts) is True
    # ...but strong Settings evidence is absent (no header, no
    # TextInput, no action button) and no menu action is visible
    assert auth_smoke._has_settings_strong_evidence(texts, typed) \
        is False
    assert auth_smoke._owns_settings_menu_stage(texts, typed) \
        is False


# ------------- M14-196 R11: menu-stage evidence fixtures ---------

def test_r11_menu_stage_tab_only_now_foreground_lost(monkeypatch):
    """R11 flow-level regression: a caret-menu dump showing ONLY
    the tab bar and URL field (no strong Settings evidence, no
    IME menu action) no longer passes the R10 menu-stage check -
    each attempt fails settings_foreground_lost at stage=menu
    instead of reaching select_all_not_found; nothing is typed."""
    monkeypatch.setattr(auth_smoke.time, "sleep", lambda s: None)
    dumps = []
    for _attempt in range(auth_smoke.SETTINGS_MAX_ATTEMPTS):
        dumps += [
            # navigation + ownership dumps: our Settings screen
            # (the pane stage still owns this via the tab bar)
            _r6_layout([SETTINGS_TAB, URL_FIELD]),
            _r6_layout([SETTINGS_TAB, URL_FIELD]),
            # caret-menu dump stays TAB-ONLY: the pre-R10
            # _owns_layout shortcut waved exactly this shape
            # through; R10 demands strong evidence + menu
            # action, so ownership now fails at every attempt
            _r6_layout([SETTINGS_TAB]),
        ]
    driver = _FakeSettingsDriver(dumps)
    ok, failures = auth_smoke._drive_settings_url(
        driver, "http://10.0.2.2:8765/")
    assert ok is False
    codes = [f["code"] for f in failures]
    assert codes == ["settings_foreground_lost"] * \
        auth_smoke.SETTINGS_MAX_ATTEMPTS
    assert all(f["detail"]["stage"] == "menu" for f in failures)
    names = [a[0] for a in driver.actions]
    assert names.count("longClick") == \
        auth_smoke.SETTINGS_MAX_ATTEMPTS
    assert "focused_text" not in names
    assert "restart_ability" not in names


def test_r11_select_all_not_found_branch_reachable():
    """R11 unit: the select_all_not_found branch stays reachable
    under the R10 menu-stage rule - a layout with STRONG Settings
    pane evidence plus a visible IME menu action (剪切) but no
    全选 passes menu-stage ownership, then fails the exact-match
    search for 全选."""
    layout = _r6_layout([
        SETTINGS_HEADER,
        SETTINGS_SAVE,
        SETTINGS_TAB,
        URL_FIELD,
        ("Text", "剪切", "[119,254][218,311]"),
    ])
    texts = auth_smoke.layout_texts(layout)
    typed = auth_smoke.layout_typed(layout)
    assert auth_smoke._owns_settings_menu_stage(texts, typed) is True
    assert auth_smoke.find_text_exact(
        texts, auth_smoke.SETTINGS_MENU_SELECT_ALL_TEXT) is None
