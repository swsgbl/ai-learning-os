"""Tests for tools.android_smoke.sanitize."""

from __future__ import annotations

from datetime import datetime

import pytest

from tools.android_smoke.sanitize import sanitize_request

ALLOWED_FIELDS = {
    "timestamp_utc",
    "method",
    "path",
    "query_keys",
    "body_len",
}


class TestFieldContract:
    def test_returns_only_allowed_fields(self):
        result = sanitize_request("GET", "/v1/items?page=1", None)
        assert set(result.keys()) == ALLOWED_FIELDS

    def test_timestamp_is_utc_iso(self):
        result = sanitize_request("GET", "/v1/items", None)
        parsed = datetime.fromisoformat(result["timestamp_utc"])
        assert parsed.tzinfo is not None
        assert parsed.utcoffset().total_seconds() == 0


class TestPathAndQuery:
    def test_path_strips_query(self):
        result = sanitize_request("GET", "/v1/items?a=1&b=2", None)
        assert result["path"] == "/v1/items"

    def test_query_keys_extracted_without_values(self):
        result = sanitize_request("GET", "/v1/items?token=secret&limit=10", None)
        assert result["query_keys"] == ("token", "limit")

    def test_query_value_never_logged(self):
        result = sanitize_request("GET", "/v1/items?password=hunter2", None)
        assert "hunter2" not in str(result)

    def test_no_query_yields_empty_keys(self):
        result = sanitize_request("GET", "/v1/items", None)
        assert result["query_keys"] == ()

    def test_valueless_query_params_kept_as_keys(self):
        result = sanitize_request("GET", "/v1/items?flag&x=1", None)
        assert result["query_keys"] == ("flag", "x")


class TestMethod:
    @pytest.mark.parametrize("method", ["GET", "POST", "PATCH", "DELETE"])
    def test_valid_methods_pass_through(self, method):
        assert sanitize_request(method, "/v1/x", None)["method"] == method

    def test_lowercase_method_normalized(self):
        assert sanitize_request("post", "/v1/x", None)["method"] == "POST"

    def test_invalid_method(self):
        assert sanitize_request("POST /v1/x", "/v1/x", None)["method"] == "INVALID"

    def test_empty_method(self):
        assert sanitize_request("", "/v1/x", None)["method"] == "INVALID"

    def test_non_string_method(self):
        assert sanitize_request(None, "/v1/x", None)["method"] == "INVALID"

    def test_method_with_control_characters_cleaned(self):
        assert sanitize_request("PO\x00ST", "/v1/x", None)["method"] == "POST"


class TestControlCharacters:
    def test_control_characters_stripped_from_path(self):
        result = sanitize_request("GET", "/v1/it\x00ems\x1f?ok=1", None)
        assert result["path"] == "/v1/items"

    def test_control_characters_stripped_from_query_keys(self):
        result = sanitize_request("GET", "/v1/items?to\x7fken=1", None)
        assert result["query_keys"] == ("token",)


class TestBody:
    def test_body_none(self):
        assert sanitize_request("POST", "/v1/x", None)["body_len"] == 0

    def test_body_bytes_length(self):
        assert sanitize_request("POST", "/v1/x", b"hello")["body_len"] == 5

    def test_body_empty_bytes(self):
        assert sanitize_request("POST", "/v1/x", b"")["body_len"] == 0

    def test_body_content_never_logged(self):
        result = sanitize_request("POST", "/v1/x", b"password=hunter2")
        assert "hunter2" not in str(result)
        assert result["body_len"] == 16

    def test_bodyless_method_ignores_body(self):
        assert sanitize_request("GET", "/v1/x", b"ignored")["body_len"] == 0
