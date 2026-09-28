"""Source-scan tests pinning DownloadPane honesty boundaries (M14-174).

These tests read the actual ArkTS sources under apps/harmony (no device,
no build, no network) and pin two honesty contracts for the M14-174
download-status pane:

1. No native-package links: DownloadPane (and its mount site in
   Index.ets) must never carry ``.apk`` or ``.hap`` download links —
   the Harmony native channel stays honestly "pending" (AGC release
   certificate/Profile not landed) and the Android channel only points
   at the /download web entry. The derivation of the /download entry
   must be pure text derived from the normalized base URL (base +
   '/download'), never an artifact URL.

2. No production-readiness overclaim: neither file may claim the
   Harmony native channel (or the app as a whole) is production-ready
   ("生产可用" / "正式发布" / "可上线" and friends). The pending state
   and its reason must be stated instead.

These mirrors exist because a UI pane about "downloads" is exactly
where an overclaim (a fabricated APK link, a "production ready"
statement) would be both easy to add and harmful to ship.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Repo root = tests/harmony_release/../../..
REPO_ROOT = Path(__file__).resolve().parents[2]
HARMONY_ETS_DIR = REPO_ROOT / "apps" / "harmony" / "entry" / "src" / "main" / "ets"
DOWNLOAD_PANE = HARMONY_ETS_DIR / "components" / "DownloadPane.ets"
INDEX_PAGE = HARMONY_ETS_DIR / "pages" / "Index.ets"
SETTINGS_STORE = HARMONY_ETS_DIR / "components" / "SettingsStore.ets"

# Phrases that must never appear in DownloadPane / its mount comments.
# Each entry is (needle, why it is forbidden).
FORBIDDEN_PACKAGE_LINKS = [
    # native artifact extensions used as download links
    ".apk",
    ".hap",
    # common host shapes for fabricated artifact URLs
    "https://",
    "http://",
]

FORBIDDEN_OVERCLAIMS = [
    "生产可用",
    "正式发布",
    "正式上线",
    "可上线",
    "已发布",
    "已上架",
    "production ready",
    "production-ready",
    "prod-ready",
    "GA ",
]


@pytest.fixture(scope="module")
def download_pane_text() -> str:
    assert DOWNLOAD_PANE.is_file(), f"missing {DOWNLOAD_PANE}"
    return DOWNLOAD_PANE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index_text() -> str:
    assert INDEX_PAGE.is_file(), f"missing {INDEX_PAGE}"
    return INDEX_PAGE.read_text(encoding="utf-8")


# ------------------------------------- honesty: no package links ----

class TestNoPackageLinks:
    def test_download_pane_has_no_apk_or_hap_link(
            self, download_pane_text):
        for needle in FORBIDDEN_PACKAGE_LINKS:
            assert needle not in download_pane_text, (
                f"DownloadPane must not contain {needle!r}: the native "
                "channels are pending, no artifact links may be shown"
            )

    def test_download_pane_mentions_both_pending_channels(
            self, download_pane_text):
        """The pane must state both native channels are pending."""
        assert "待发布" in download_pane_text
        assert "Harmony" in download_pane_text
        assert "Android" in download_pane_text

    def test_download_pane_states_harmony_pending_reason(
            self, download_pane_text):
        """The Harmony pending reason must be the AGC cert/Profile gap."""
        assert "AGC" in download_pane_text
        assert "证书" in download_pane_text

    def test_download_entry_is_derived_not_hardcoded(
            self, download_pane_text):
        """The /download entry must be derived from the base URL via a
        dedicated pure function, not hardcoded as a full URL."""
        assert "deriveDownloadEntry" in download_pane_text
        # the derivation itself: strip trailing slashes then append
        assert "'/download'" in download_pane_text or \
            '"/download"' in download_pane_text

    def test_index_mount_comment_has_no_package_link(self, index_text):
        for needle in FORBIDDEN_PACKAGE_LINKS:
            assert needle not in index_text, (
                f"Index.ets must not contain {needle!r}"
            )

    def test_pwa_channel_is_available(self, download_pane_text):
        """PWA web channel is the only available channel today."""
        assert "PWA" in download_pane_text
        assert "可用" in download_pane_text


# ----------------------------------- honesty: no overclaims ----

class TestNoOverclaims:
    def test_download_pane_never_claims_production_ready(
            self, download_pane_text):
        for needle in FORBIDDEN_OVERCLAIMS:
            assert needle not in download_pane_text, (
                f"DownloadPane must not claim {needle!r}: the Harmony "
                "native channel is pending, never production-ready"
            )

    def test_index_never_claims_production_ready(self, index_text):
        for needle in FORBIDDEN_OVERCLAIMS:
            assert needle not in index_text, (
                f"Index.ets must not claim {needle!r}"
            )

    def test_settings_store_never_claims_production_ready(self):
        """Adjacent settings source stays honest too (guard against
        the overclaim migrating to a neighboring file)."""
        text = SETTINGS_STORE.read_text(encoding="utf-8")
        for needle in FORBIDDEN_OVERCLAIMS:
            assert needle not in text, (
                f"SettingsStore.ets must not claim {needle!r}"
            )


# ------------------------------------------- mock allow-list pin ----

class TestMockAllowListPinsDownloadOut:
    """/download is a web entry, not an API endpoint: the Harmony mock
    server must keep it out of the GET allow-list forever."""

    SERVER = REPO_ROOT / "tools" / "harmony_mock" / "server.py"
    CONTRACT = REPO_ROOT / "tools" / "harmony_mock" / "test_contract.py"

    def test_allow_list_has_no_download(self):
        text = self.SERVER.read_text(encoding="utf-8")
        # crude but effective: the allow-list block must not mention
        # /download anywhere between ALLOWED_GET_PATHS and PROTECTED
        start = text.find("ALLOWED_GET_PATHS")
        end = text.find("PROTECTED_GET_PATHS")
        assert start != -1 and end != -1
        allow_block = text[start:end]
        assert "/download" not in allow_block
        # and not anywhere else in the server either
        assert '"/download"' not in text

    def test_contract_has_download_negative_assertions(self):
        text = self.CONTRACT.read_text(encoding="utf-8")
        for case in ('("GET", "/download")',
                     '("GET", "/download/")',
                     '("GET", "/download?foo=bar")',
                     '("POST", "/download")'):
            assert case in text, (
                f"test_contract.py must keep the negative assertion {case}"
            )


# ---------------------------------------- no clipboard / browser ----

class TestNoClipboardOrBrowserAutomation:
    """The pane must stay pure-text: no clipboard writes, no browser
    launches, no new permissions."""

    def test_no_clipboard_api(self, download_pane_text):
        for needle in ("pasteboard", "clipboard", "setClipboard"):
            assert needle not in download_pane_text, (
                f"DownloadPane must not use {needle!r} — pure text only"
            )

    def test_no_browser_launch(self, download_pane_text):
        for needle in ("openLink", "startAbility", "router.pushUrl",
                       "webview", "Web(", "want"):
            assert needle not in download_pane_text, (
                f"DownloadPane must not launch a browser via {needle!r}"
            )

    def test_no_new_permissions_in_module_json(self):
        """The entry module must request exactly INTERNET, nothing else.

        Parses the requestPermissions block (from the keyword up to the
        first closing bracket) and asserts the extracted
        ohos.permission.* set is exactly {"ohos.permission.INTERNET"}.
        A missing requestPermissions block fails: the module is
        expected to declare INTERNET explicitly. Parse failures are
        never swallowed (no try/except).
        """
        module_json = (REPO_ROOT / "apps" / "harmony" / "entry" /
                       "src" / "main" / "module.json5")
        text = module_json.read_text(encoding="utf-8")
        start = text.find("requestPermissions")
        assert start != -1, (
            "entry module.json5 lost its requestPermissions block "
            "(expected exactly ohos.permission.INTERNET)"
        )
        end = text.find("]", start)
        assert end != -1, "requestPermissions block has no closing ']'"
        block = text[start:end]
        perms = set(re.findall(r"ohos\.permission\.[A-Za-z0-9_.]+", block))
        assert perms == {"ohos.permission.INTERNET"}, (
            "entry module must request exactly "
            f"[ohos.permission.INTERNET], got {sorted(perms)}"
        )
