"""M5-03 Web fetch gate：URL 安全检查 + robots.txt disallow + 频率限制。

- 协议白名单：仅 http/https——file/ftp/javascript/data 等危险协议一律拒绝；
- SSRF 防护：主机解析为 IP 后命中私网/环回/链路本地保留段即拒绝
  （127/8、10/8、172.16/12、192.168/16、169.254/16、0.0.0.0、::1、fc00::/7、fe80::/10）；
  resolver 可注入（测试无需真实网络，确定性恒同）；
- robots.txt disallow：解析 User-agent: * 组的 Disallow 前缀规则（含空规则=允许）；
- RateLimiter：固定窗口 60s 计数，同键超限拒绝——判定与拒绝原因明确、可审计。
"""
from __future__ import annotations

import ipaddress
import re
import socket
import time
from collections.abc import Callable
from urllib.parse import urlsplit

ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})

MAX_URL_LEN = 2048

# SSRF 保留网络（v4 + v6）：命中即拒绝
FORBIDDEN_V4: tuple[tuple[str, int], ...] = (
    ("0.0.0.0", 8),
    ("10.0.0.0", 8),
    ("127.0.0.0", 8),
    ("169.254.0.0", 16),
    ("192.168.0.0", 16),
)
FORBIDDEN_V6: tuple[tuple[str, int], ...] = (
    ("::1", 128),
    ("fc00::", 7),
    ("fe80::", 10),
)


def _is_forbidden_ip(ip_text: str) -> bool:
    """IP 落入任一保留段即 True（无法解析的畸形 IP 一律拒绝）。"""
    try:
        addr = ipaddress.ip_address(ip_text)
    except ValueError:
        return True
    if addr.version == 4:
        return any(addr in ipaddress.ip_network(f"{net}/{prefix}") for net, prefix in FORBIDDEN_V4) or addr.is_private
    return any(addr in ipaddress.ip_network(f"{net}/{prefix}") for net, prefix in FORBIDDEN_V6) or addr.is_private


def default_resolver(hostname: str) -> list[str]:
    """默认解析器：返回主机的全部 IP（v4+v6）。"""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError:
        return []
    return [info[4][0] for info in infos]


def check_url_safety(url: str, resolver: Callable[[str], list[str]] = default_resolver) -> dict:
    """URL 预检：危险协议 / SSRF 保留地址 / 超长 URL 均拒绝，返回 {allowed, reason}。

    resolver 可注入——传入受控映射即可确定性测试，不虚报（解析失败=拒绝）。
    """
    if not url or len(url) > MAX_URL_LEN:
        return {"allowed": False, "reason": "URL 为空或超过长度上限"}
    split = urlsplit(url)
    if split.scheme.lower() not in ALLOWED_SCHEMES:
        return {"allowed": False, "reason": f"危险协议被拒绝: {split.scheme or '(空)'}"}
    hostname = (split.hostname or "").lower()
    if not hostname:
        return {"allowed": False, "reason": "URL 缺少主机名"}
    ips = resolver(hostname)
    if not ips:
        return {"allowed": False, "reason": "主机名无法解析（拒绝放行）"}
    for ip_text in ips:
        if _is_forbidden_ip(ip_text):
            return {"allowed": False, "reason": f"内网/保留地址被拒绝: {ip_text}"}
    return {"allowed": True, "reason": "OK"}


_DISALLOW_RE = re.compile(r"^Disallow:\s*(.*)$", re.IGNORECASE)


def robots_disallows(robots_txt: str, path: str) -> bool:
    """robots.txt disallow 判定：User-agent: * 组的 Disallow 前缀规则。

    - 只解析 `*` 组（本项目 fetcher 统一身份）；空 Disallow: = 允许全部；
    - 前缀匹配（robots 协议语义）；空路径匹配空规则。
    """
    in_star_group = False
    matched = False
    for raw_line in robots_txt.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        lower = line.lower()
        if lower.startswith("user-agent:"):
            agent = line.split(":", 1)[1].strip()
            in_star_group = agent == "*"
        elif in_star_group and lower.startswith("disallow:"):
            rule = line.split(":", 1)[1].strip()
            if rule == "":
                matched = False  # 空规则 = 全允许，重置此前命中
            elif path.startswith(rule):
                matched = True
    return matched


class RateLimiter:
    """固定窗口频率限制（60s 窗口，同键计数）。"""

    def __init__(
        self,
        max_per_minute: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_per_minute
        self._clock = clock
        self._window_start: dict[str, float] = {}
        self._counts: dict[str, int] = {}

    def allow(self, key: str) -> bool:
        """同键 60s 窗口内超过 max_per_minute 即拒绝。"""
        now = self._clock()
        start = self._window_start.get(key)
        if start is None or now - start >= 60.0:
            self._window_start[key] = now
            self._counts[key] = 1
            return True
        self._counts[key] += 1
        return self._counts[key] <= self._max


def evaluate_fetch(
    url: str,
    *,
    path: str = "/",
    robots_txt: str | None = None,
    resolver: Callable[[str], list[str]] = default_resolver,
) -> dict:
    """综合预检：URL 安全 + robots disallow（robots 提供时）。"""
    verdict = check_url_safety(url, resolver=resolver)
    if not verdict["allowed"]:
        return verdict
    if robots_txt is not None and robots_disallows(robots_txt, path):
        return {"allowed": False, "reason": f"路径被 robots.txt disallow: {path}"}
    return {"allowed": True, "reason": "OK"}
