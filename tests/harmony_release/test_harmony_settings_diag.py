"""M14-230H Settings one-shot connection diagnostics source contracts.

ArkTS sources are scanned rather than executed (the repository's Harmony
test boundary is source-contract based). These tests pin only the
M14-230H behavior of the Settings pane's explicit 测试连接 diagnostic:

- explicit-action gate: diagnostics run ONLY from the button handler;
- endpoint usage: exactly one getHealth + one getAuthStatus per test,
  bounded by the existing AiosApi timeouts (no local timeout/retry
  logic is added);
- outcome mapping: the four-value closed set ok / http_path_mismatch /
  network_unreachable / mixed with the agreed classification table;
- no retry/polling and no auto-run on mount/save/URL change;
- disabled state: both controls disabled while testing, restored after;
- leakage boundary: only the closed-set Chinese label (plus a numeric
  HTTP status) is ever displayed - never a raw error body, exception
  object, token, stack, or internal path.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ETS_DIR = REPO_ROOT / "apps" / "harmony" / "entry" / "src" / "main" / "ets"
SETTINGS_PANE = ETS_DIR / "components" / "SettingsPane.ets"
AIOS_API = ETS_DIR / "AiosApi.ets"


def read(path: Path) -> str:
    assert path.is_file(), f"missing {path}"
    return path.read_text(encoding="utf-8")


PANE = read(SETTINGS_PANE)
API = read(AIOS_API)

# The doTest body (single flat block) is the unit under contract.
_DO_TEST_START = "private async doTest(): Promise<void> {"
_m = re.search(
    re.escape(_DO_TEST_START) + r"(.*?)\n  }\n",
    PANE,
    re.DOTALL,
)
assert _m is not None, "doTest body not found in SettingsPane.ets"
DO_TEST = _m.group(1)


class TestExplicitActionGate:
    def test_diagnostic_only_reachable_from_button_handler(self):
        # No other method references doTest: after removing the body,
        # only the definition and the button onClick remain.
        others = PANE.replace(DO_TEST, "")
        assert others.count("doTest") == 2
        assert others.count("this.doTest()") == 1  # the button onClick
        # ...and aboutToAppear / doSave / onChange never call it.
        about = re.search(r"async aboutToAppear.*?\n  }\n", PANE, re.DOTALL)
        assert about is not None
        assert "doTest" not in about.group(0)
        save = re.search(r"private async doSave.*?\n  }\n", PANE, re.DOTALL)
        assert save is not None
        assert "doTest" not in save.group(0)
        change = re.search(r"onChange\(\(value: string\) => \{.*?\}\)", PANE,
                           re.DOTALL)
        assert change is not None
        assert "doTest" not in change.group(0)

    def test_no_emitter_subscription_wires_url_change_to_diagnostic(self):
        assert "emitter" not in PANE

    def test_running_guard_prevents_reentry(self):
        assert "if (this.testing) {" in DO_TEST
        assert "this.testing = true;" in DO_TEST


class TestEndpointUsage:
    def test_exactly_one_health_and_one_auth_status_call_per_test(self):
        assert DO_TEST.count("getHealth(") == 1
        assert DO_TEST.count("getAuthStatus(") == 1
        assert DO_TEST.count("await getHealth") == 1
        assert DO_TEST.count("await getAuthStatus") == 1

    def test_calls_use_the_validated_url_once_not_in_a_loop(self):
        assert "getHealth(check.url)" in DO_TEST
        assert "getAuthStatus(check.url)" in DO_TEST
        for keyword in ("for (", "while (", "setInterval", "setTimeout"):
            assert keyword not in DO_TEST, keyword

    def test_aios_api_still_bounds_requests_with_existing_timeouts(self):
        match = re.search(r"const TIMEOUT_MS: number = (\d+);", API)
        assert match is not None
        assert int(match.group(1)) == 10000
        # Single request per endpoint, no retry primitive in the layer.
        assert "usingCache: false" in API
        assert "connectTimeout: TIMEOUT_MS" in API
        assert "readTimeout: TIMEOUT_MS" in API

    def test_no_other_endpoint_is_touched_by_the_diagnostic(self):
        for fn in ("getPrivacy", "getVersion", "getOpsSnapshot", "getAudit",
                   "getPapers", "login", "getExamSession"):
            assert fn not in DO_TEST, fn


class TestOutcomeMapping:
    def test_closed_set_has_exactly_four_values(self):
        assert "OK = 'ok'" in PANE
        assert "HTTP_PATH_MISMATCH = 'http_path_mismatch'" in PANE
        assert "NETWORK_UNREACHABLE = 'network_unreachable'" in PANE
        assert "MIXED = 'mixed'" in PANE
        # The enum carries no other members.
        enum = re.search(r"enum DiagOutcome \{(.*?)\}", PANE, re.DOTALL)
        assert enum is not None
        assert enum.group(1).count("=") == 4

    def test_classification_table_is_the_agreed_one(self):
        # good + good -> ok
        assert "if (h === 'good' && a === 'good')" in PANE
        # http_error + http_error -> http_path_mismatch
        assert "if (h === 'http_error' && a === 'http_error')" in PANE
        # no_conn + no_conn -> network_unreachable
        assert "if (h === 'no_conn' && a === 'no_conn')" in PANE
        # everything else -> mixed (the fall-through return)
        assert "return DiagOutcome.MIXED;" in PANE

    def test_endpoint_verdict_classification(self):
        # ok && shapeValid -> good
        assert "if (ok && shapeValid) {" in PANE
        assert "return 'good';" in PANE
        # non-2xx real HTTP answer -> http_error
        assert "if (!ok && status >= 100 && !(status >= 200 && status < 300))" in PANE
        assert "return 'http_error';" in PANE
        # transport failure (-1) or shape-invalid 2xx -> no_conn
        assert "return 'no_conn';" in PANE

    def test_url_policy_failure_maps_to_network_unreachable(self):
        # Invalid URL never reaches the network: closed-set label only.
        assert "DIAG_UNREACHABLE_TEXT" in DO_TEST
        match = re.search(
            r"const check = validateBaseUrl\(this\.urlInput\);(.*?)"
            r"this\.testing = true;",
            DO_TEST,
            re.DOTALL,
        )
        assert match is not None
        early = match.group(1)
        assert "UrlPolicyCode.OK" in early
        assert "return;" in early

    def test_labels_are_chinese_and_closed_set(self):
        for const in ("DIAG_OK_TEXT", "DIAG_MISMATCH_TEXT",
                      "DIAG_UNREACHABLE_TEXT", "DIAG_MIXED_TEXT"):
            match = re.search(const + r": string = '([^']+)'", PANE)
            assert match is not None, const
            # Chinese-only label (no URLs, no codes, no English payload).
            assert re.fullmatch(r"[\u4e00-\u9fff0-9a-z_():]+",
                                match.group(1)), const

    def test_http_status_only_decorates_mismatch_as_a_number(self):
        assert "HTTP ${code}" in PANE
        assert "health.status >= 100 ? health.status : auth.status" in PANE


class TestNoRetryNoPolling:
    def test_pane_declares_no_retry_or_timer_primitives(self):
        for keyword in ("setInterval", "setTimeout", "Promise.all",
                        "retry", "poll"):
            assert keyword not in PANE, keyword

    def test_diagnostic_does_not_save_or_mutate_the_url(self):
        for banned in ("saveBaseUrl", "loadBaseUrl(this", "prefs"):
            assert banned not in DO_TEST, banned

    def test_diagnostic_does_not_run_on_mount(self):
        about = re.search(r"async aboutToAppear.*?\n  }\n", PANE, re.DOTALL)
        assert about is not None
        for banned in ("getHealth", "getAuthStatus", "doTest"):
            assert banned not in about.group(0), banned


class TestDisabledState:
    def test_both_buttons_disabled_while_testing(self):
        # Save button: disabled while saving OR testing.
        save_btn = re.search(r"Button\(this\.saving \? '保存中…' : '保存'\)"
                             r"(.*?)\.onClick", PANE, re.DOTALL)
        assert save_btn is not None
        assert ".enabled(!this.saving && !this.testing)" in save_btn.group(1)
        # Test button: same guard.
        test_btn = re.search(r"Button\(this\.testing \? '测试中…' : '测试连接'\)"
                             r"(.*?)\.onClick", PANE, re.DOTALL)
        assert test_btn is not None
        assert ".enabled(!this.saving && !this.testing)" in test_btn.group(1)

    def test_running_flag_restored_in_all_paths(self):
        # testing=true is set exactly once...
        assert DO_TEST.count("this.testing = true;") == 1
        # ...and reset exactly once, on the single straight-line path
        # after both awaits (no early return between them and the reset,
        # guaranteed by the order of the two statements).
        set_at = DO_TEST.index("this.testing = true;")
        reset_at = DO_TEST.index("this.testing = false;")
        health_at = DO_TEST.index("await getHealth")
        auth_at = DO_TEST.index("await getAuthStatus")
        assert set_at < health_at < auth_at < reset_at
        # No return between the two awaits and the reset.
        between = DO_TEST[auth_at:reset_at]
        assert "return;" not in between


class TestLeakageBoundary:
    def test_diagnostic_displays_never_raw_error_payloads(self):
        # r.error (the transport/HTTP text from AiosApi) is NOT used.
        assert ".error" not in DO_TEST
        # No body, exception, token or stack rendering anywhere in doTest.
        for banned in (".data.status", ".data.service", "JSON.stringify",
                       "safeErrorText", "Bearer", "token"):
            assert banned not in DO_TEST, banned

    def test_pane_has_no_logging_or_console_output(self):
        for banned in ("console.", "hilog", "Logger"):
            assert banned not in PANE, banned

    def test_message_state_receives_only_label_or_label_plus_status(self):
        # The only writers of this.message inside doTest are the closed-set
        # assignment (early-return label), the reset to '' while running,
        # and the final closed-set text (label / label + numeric status).
        writes = re.findall(r"this\.message = ([^;]+);", DO_TEST)
        assert len(writes) == 3
        assert "DIAG_UNREACHABLE_TEXT" in writes[0]
        assert writes[1] == "''"
        assert "text" in writes[2]
        # All other message writes live outside the diagnostic and use the
        # plain save/context paths (no endpoint payload can reach them).
        outside = PANE.replace(DO_TEST, "")
        for write in re.findall(r"this\.message = ([^;]+);", outside):
            assert write in ("'无法获取应用上下文(getHostContext 为空),设置暂不可用'",
                             "'应用上下文不可用,无法保存'", "`已保存: ${r.url}`",
                             "r.error")
