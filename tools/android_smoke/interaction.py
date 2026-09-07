"""UI interaction helpers for the Android smoke harness.

Standard library only. ``AndroidUiController`` wraps uiautomator XML dumps
and adb ``input`` commands so tests can locate and drive UI elements
without shell injection: every adb shell command is built as a list of
plain string arguments (see ``AdbClient.run_shell``).
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from .adb import AdbClient

_BOUNDS_RE = re.compile(r"^\[(\d+),(\d+)\]\[(\d+),(\d+)\]$")


class UiError(Exception):
    """Base class for UI interaction errors."""


class UiNotFoundError(UiError):
    """Raised when no element matches the given criteria."""


class UiTimeoutError(UiError):
    """Raised when ``wait_for`` exceeds its deadline."""

    def __init__(self, message: str, last_xml_summary: str = ""):
        super().__init__(message)
        self.last_xml_summary = last_xml_summary


def _parse_bounds(raw: str):
    """Parse ``[x1,y1][x2,y2]`` into ((x1,y1),(x2,y2)) or None if invalid."""
    if not isinstance(raw, str):
        return None
    m = _BOUNDS_RE.match(raw.strip())
    if not m:
        return None
    x1, y1, x2, y2 = (int(v) for v in m.groups())
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1), (x2, y2)


@dataclass(frozen=True)
class UiElement:
    """One node of a uiautomator window dump."""

    text: str = ""
    content_desc: str = ""
    resource_id: str = ""
    klass: str = ""
    bounds: str = ""
    center: tuple = ()

    @classmethod
    def from_node(cls, node) -> "UiElement":
        bounds_raw = node.get("bounds", "")
        parsed = _parse_bounds(bounds_raw)
        if parsed is None:
            raise UiError("invalid or missing bounds: %r" % (bounds_raw,))
        (x1, y1), (x2, y2) = parsed
        return cls(
            text=node.get("text", "") or "",
            content_desc=node.get("content-desc", "") or "",
            resource_id=node.get("resource-id", "") or "",
            klass=node.get("class", "") or "",
            bounds=bounds_raw,
            center=((x1 + x2) // 2, (y1 + y2) // 2),
        )


def parse_ui_dump(xml_bytes: bytes) -> List[UiElement]:
    """Decode (utf-8) and parse a uiautomator dump into ``UiElement``s."""
    if isinstance(xml_bytes, bytes):
        text = xml_bytes.decode("utf-8")
    else:
        text = xml_bytes
    root = ET.fromstring(text)
    elements: List[UiElement] = []

    def walk(node, is_root=False):
        # The XML root (<hierarchy>) carries no bounds; only <node> elements
        # are UI elements and must have valid bounds.
        if not (is_root and node.get("bounds") is None):
            elements.append(UiElement.from_node(node))
        for child in node:
            walk(child)

    walk(root, is_root=True)
    return elements


def find(
    elements: Sequence[UiElement],
    text: Optional[str] = None,
    content_desc: Optional[str] = None,
    contains: bool = False,
    occurrence: int = 0,
) -> UiElement:
    """Return the element matching text and/or content-desc.

    ``contains=False`` requires an exact match; ``contains=True`` matches
    substrings. ``occurrence`` is a zero-based index into the matches in
    document order (default 0 keeps the historical "first match" behavior).
    Raises ``UiNotFoundError`` when there is no such match.
    """
    if text is None and content_desc is None:
        raise ValueError("find requires text or content_desc")

    def match(value: str, wanted: str) -> bool:
        if contains:
            return wanted in value
        return value == wanted

    seen = 0
    for el in elements:
        if text is not None and not match(el.text, text):
            continue
        if content_desc is not None and not match(el.content_desc, content_desc):
            continue
        if seen == occurrence:
            return el
        seen += 1
    raise UiNotFoundError(
        "no element matching text=%r content_desc=%r contains=%s occurrence=%d "
        "(only %d match(es) found)"
        % (text, content_desc, contains, occurrence, seen)
    )


def _summarize_xml(xml_bytes: bytes, limit: int = 400) -> str:
    """Compact, log-safe summary of the last dump (no attribute values)."""
    try:
        root = ET.fromstring(xml_bytes.decode("utf-8", errors="replace"))
    except ET.ParseError:
        return "<unparseable dump, %d bytes>" % len(xml_bytes)
    names = [el.tag for el in root.iter()]
    return "root=%s nodes=%d %s" % (
        root.tag,
        len(names),
        "first=%s last=%s" % (names[0], names[-1]) if names else "",
    )[:limit]


class AndroidUiController:
    """Drive the device UI via dumped XML plus adb ``input`` commands."""

    def __init__(
        self,
        adb: Optional[AdbClient] = None,
        ui_dump: Optional[Callable[[], bytes]] = None,
        tap: Optional[Callable[[Sequence[str]], object]] = None,
    ):
        self.adb = adb
        if ui_dump is None:
            if adb is None:
                raise ValueError("ui_dump or adb is required")
            ui_dump = adb.dump_ui
        if tap is None:
            if adb is None:
                raise ValueError("tap or adb is required")
            adb_for_tap = adb

            def tap(args: Sequence[str]) -> object:
                for a in args:
                    if not isinstance(a, str):
                        raise TypeError("tap arguments must be strings")
                return adb_for_tap.run_shell(list(args))

        self.ui_dump = ui_dump
        self.tap = tap

    # ---- element helpers --------------------------------------------

    def dump_elements(self) -> List[UiElement]:
        return parse_ui_dump(self.ui_dump())

    def find_element(
        self,
        text: Optional[str] = None,
        content_desc: Optional[str] = None,
        contains: bool = False,
        occurrence: int = 0,
    ) -> UiElement:
        return find(
            self.dump_elements(),
            text=text,
            content_desc=content_desc,
            contains=contains,
            occurrence=occurrence,
        )

    def wait_for(
        self,
        text: str,
        timeout_seconds: float = 5,
        interval_seconds: float = 0.25,
    ) -> UiElement:
        """Poll dump+find until the element appears or the deadline passes."""
        deadline = time.monotonic() + timeout_seconds
        last_xml = b""
        while True:
            last_xml = self.ui_dump()
            try:
                return find(parse_ui_dump(last_xml), text=text)
            except UiNotFoundError:
                if time.monotonic() >= deadline:
                    raise UiTimeoutError(
                        "timed out after %ss waiting for text=%r" % (timeout_seconds, text),
                        last_xml_summary=_summarize_xml(last_xml),
                    )
                time.sleep(interval_seconds)

    # ---- actions ------------------------------------------------------

    def tap_element(self, element: UiElement) -> object:
        x, y = element.center
        return self.tap(["input", "tap", str(x), str(y)])

    def tap_text(self, text: str, contains: bool = False) -> object:
        return self.tap_element(self.find_element(text=text, contains=contains))

    def tap_text_occurrence(
        self, text: str, occurrence: int, contains: bool = False
    ) -> object:
        """Tap the ``occurrence``-th (zero-based) element matching ``text``.

        用于同一文案出现多次的界面（如页面标题与同名按钮）；
        越界时由 :func:`find` 抛出带匹配数信息的 ``UiNotFoundError``。
        """
        return self.tap_element(
            self.find_element(text=text, contains=contains, occurrence=occurrence)
        )

    def type_text(self, text: str) -> object:
        """Type ASCII printable text; spaces become ``%s`` for adb input."""
        if not isinstance(text, str):
            raise TypeError("type_text expects a str")
        for ch in text:
            if not (0x20 <= ord(ch) <= 0x7E):
                raise ValueError("type_text only allows ASCII printable characters: %r" % text)
        # Reject any raw '%' before replacing spaces with %s, so callers
        # cannot smuggle placeholders through.
        if "%" in text:
            raise ValueError("type_text does not allow %% characters: %r" % text)
        encoded = text.replace(" ", "%s")
        return self.tap(["input", "text", encoded])

    def swipe(
        self, start_x: int, start_y: int, end_x: int, end_y: int, duration_ms: int
    ) -> object:
        """受控 swipe：坐标/时长必须显式传参，逐个校验为非负整数。

        与 :meth:`tap` 一致走参数列表形式的 ``adb shell input swipe``，
        不拼接任意 shell 字符串。
        """
        values = (start_x, start_y, end_x, end_y, duration_ms)
        for value in values:
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(
                    "swipe arguments must be non-negative ints: %r" % (values,)
                )
        return self.tap(
            [
                "input",
                "swipe",
                str(start_x),
                str(start_y),
                str(end_x),
                str(end_y),
                str(duration_ms),
            ]
        )

    def back(self) -> object:
        return self.tap(["input", "keyevent", "4"])
