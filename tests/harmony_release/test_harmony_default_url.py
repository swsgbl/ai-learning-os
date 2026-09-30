"""Source contracts for the Harmony public default service URL.

ArkTS sources are scanned rather than imported because the repository's
Harmony unit-test boundary is source-contract based and does not execute
ArkTS from pytest. These tests pin only the M14-191 behavior: the fresh
default, the persistence/override path, the URL policy used by that path,
and the download entry derived from the active base URL.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ETS_DIR = REPO_ROOT / "apps" / "harmony" / "entry" / "src" / "main" / "ets"
SETTINGS_STORE = ETS_DIR / "components" / "SettingsStore.ets"
SETTINGS_PANE = ETS_DIR / "components" / "SettingsPane.ets"
URL_POLICY = ETS_DIR / "UrlPolicy.ets"
DOWNLOAD_PANE = ETS_DIR / "components" / "DownloadPane.ets"

PUBLIC_DEFAULT = "https://ndtool.cn/aios/"


def read(path: Path) -> str:
    assert path.is_file(), f"missing {path}"
    return path.read_text(encoding="utf-8")


class TestPublicDefault:
    def test_settings_store_default_is_public_service(self):
        text = read(SETTINGS_STORE)
        match = re.search(
            r"export const DEFAULT_API_BASE_URL: string = '([^']+)'", text
        )
        assert match is not None
        assert match.group(1) == PUBLIC_DEFAULT
        assert "http://127.0.0.1:8000" not in text

    def test_settings_store_keeps_validated_local_override(self):
        text = read(SETTINGS_STORE)
        assert "const KEY_API_BASE_URL: string = 'api_base_url'" in text
        assert "validateBaseUrl(raw)" in text
        assert "validateBaseUrl(input)" in text
        assert "prefs.put(KEY_API_BASE_URL, check.url)" in text
        assert "prefs.flush()" in text

    def test_settings_pane_uses_default_for_initial_value_and_placeholder(self):
        text = read(SETTINGS_PANE)
        assert "@State urlInput: string = DEFAULT_API_BASE_URL" in text
        assert "TextInput({ text: this.urlInput, placeholder: DEFAULT_API_BASE_URL })" in text


class TestUrlPolicyContract:
    def test_policy_accepts_both_https_public_and_http_local_schemes(self):
        text = read(URL_POLICY)
        assert "const ALLOWED_SCHEMES: string[] = ['http', 'https'];" in text
        assert "export function validateBaseUrl" in text

    def test_policy_preserves_fail_closed_boundaries(self):
        text = read(URL_POLICY)
        for source in (
            "raw.indexOf('\\\\') >= 0",
            "authority.indexOf('@') >= 0",
            "path.indexOf('?') >= 0 || path.indexOf('#') >= 0",
            "const normalized: string = path.length > 0 ? raw : `${raw}/`",
        ):
            assert source in text


class TestDownloadEntryDerivation:
    def test_download_entry_derives_from_active_url_not_hardcoded_link(self):
        text = read(DOWNLOAD_PANE)
        assert "@State baseUrl: string = DEFAULT_API_BASE_URL" in text
        assert "Text(deriveDownloadEntry(this.baseUrl))" in text
        assert "base.charAt(base.length - 1) === '/'" in text
        assert "return `${base}/download`" in text
        assert PUBLIC_DEFAULT not in text
