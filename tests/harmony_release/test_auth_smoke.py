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
