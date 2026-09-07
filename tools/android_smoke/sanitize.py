"""Request sanitization for Android smoke harness logging.

Produces redacted request descriptors safe to write to logs: no headers,
no body content, no tokens or passwords.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional, Tuple

_VALID_METHOD = re.compile(r"^[A-Z]+$")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

_METHODS_WITHOUT_BODY = frozenset({"GET", "HEAD", "DELETE", "OPTIONS"})


def sanitize_request(
    method: str, path: str, body_bytes: Optional[bytes]
) -> dict:
    """Return a redacted, log-safe descriptor of an HTTP request.

    Only structural metadata is kept: timestamp, method, path (without
    query string), query parameter names, and body length.
    """
    timestamp = datetime.now(timezone.utc).isoformat()

    safe_method = _sanitize_method(method)
    safe_path = _sanitize_path(path)
    query_keys = _extract_query_keys(path)
    body_len = _body_len(method, body_bytes)

    return {
        "timestamp_utc": timestamp,
        "method": safe_method,
        "path": safe_path,
        "query_keys": query_keys,
        "body_len": body_len,
    }


def _sanitize_method(method: str) -> str:
    if not isinstance(method, str):
        return "INVALID"
    cleaned = _CONTROL_CHARS.sub("", method).upper()
    if not cleaned or not _VALID_METHOD.match(cleaned):
        return "INVALID"
    return cleaned


def _sanitize_path(path: str) -> str:
    if not isinstance(path, str):
        return "/INVALID"
    cleaned = _CONTROL_CHARS.sub("", path.split("?", 1)[0])
    return cleaned if cleaned else "/INVALID"


def _extract_query_keys(path: str) -> Tuple[str, ...]:
    if not isinstance(path, str) or "?" not in path:
        return ()
    query = path.split("?", 1)[1]
    keys = []
    for pair in query.split("&"):
        if not pair:
            continue
        key = pair.split("=", 1)[0]
        key = _CONTROL_CHARS.sub("", key)
        if key:
            keys.append(key)
    return tuple(keys)


def _body_len(method: str, body_bytes: Optional[bytes]) -> int:
    if isinstance(method, str) and method.upper() in _METHODS_WITHOUT_BODY:
        return 0
    if isinstance(body_bytes, (str, bytes)):
        return len(body_bytes)
    return 0
