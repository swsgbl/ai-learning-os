"""Minimal JSON5-tolerant loader (stdlib only).

Accepts plain JSON plus ``//`` and ``/* */`` comments and trailing
commas, while respecting string literals. Unquoted keys are NOT
supported. Anything unparseable returns None so callers fail closed
instead of guessing.
"""

from __future__ import annotations

import json
from typing import List, Optional

_WHITESPACE = " \t\r\n"


def load_json5_relaxed(text: str) -> Optional[object]:
    """Parse JSON; on failure retry after stripping JSON5 extras."""
    try:
        return json.loads(text)
    except ValueError:
        pass
    stripped = _drop_trailing_commas(_strip_comments(text))
    try:
        return json.loads(stripped)
    except ValueError:
        return None


def _strip_comments(text: str) -> str:
    """Remove // and /* */ comments, preserving string literals."""
    out: List[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "\"'":
            i = _copy_string(text, i, out)
            continue
        if text.startswith("//", i):
            i = text.find("\n", i)
            i = n if i < 0 else i
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _drop_trailing_commas(text: str) -> str:
    """Remove commas directly before a closing bracket, ignoring strings."""
    out: List[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "\"'":
            i = _copy_string(text, i, out)
            continue
        if ch in "}]":
            while out and out[-1] in _WHITESPACE:
                out.pop()
            if out and out[-1] == ",":
                out.pop()
        out.append(ch)
        i += 1
    return "".join(out)


def _copy_string(text: str, start: int, out: List[str]) -> int:
    """Copy the string literal opening at ``start``; return next index."""
    quote, j = text[start], start + 1
    out.append(text[start])
    n = len(text)
    while j < n:
        out.append(text[j])
        if text[j] == "\\" and j + 1 < n:
            out.append(text[j + 1])
            j += 2
            continue
        if text[j] == quote:
            return j + 1
        j += 1
    return n
