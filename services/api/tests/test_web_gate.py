"""M5-03 Web fetch gate：SSRF / 危险协议 / robots disallow / 频率限制。

- 域层：协议白名单、内网/保留地址拒绝（resolver 注入确定性测试）、
  无法解析即拒绝（不虚报）、robots 前缀匹配与空规则语义、固定窗口限流；
- API：POST /api/v1/web/fetch-check —— allowed 200、拒绝 403（原因可见）、
  超限 429；无 DB 依赖（create_app(None) 仍可用）。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.domain.web_gate import RateLimiter, evaluate_fetch, robots_disallows
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def _public_resolver(hostname: str) -> list[str]:
    return ["93.184.216.34"]


def _identity_resolver(hostname: str) -> list[str]:
    # 字面 IP 主机的真实解析行为（getaddrinfo 对 IP 字面量原样返回）
    return [hostname]


# ---------- 域层：URL 安全 ----------


def test_https_public_allowed() -> None:
    verdict = evaluate_fetch("https://example.edu/exams.pdf", resolver=_public_resolver)
    assert verdict == {"allowed": True, "reason": "OK"}


def test_dangerous_schemes_rejected() -> None:
    """file/ftp/javascript/data 等危险协议一律拒绝。"""
    for url in (
        "file:///etc/passwd",
        "ftp://example.edu/a.pdf",
        "javascript:alert(1)",
        "data:text/html;base64,PGI+",
        "mailto:a@b.c",
    ):
        verdict = evaluate_fetch(url, resolver=_public_resolver)
        assert verdict["allowed"] is False
        assert "危险协议" in verdict["reason"]


def test_loopback_rejected() -> None:
    """环回地址拒绝（字面 IP 主机）。"""
    verdict = evaluate_fetch("http://127.0.0.1:8000/admin", resolver=_identity_resolver)
    assert verdict["allowed"] is False
    assert "内网/保留地址" in verdict["reason"]


def test_private_v4_rejected() -> None:
    """私网 v4：10/8、192.168/16、172.16-31（is_private）、169.254 链路本地。"""
    for ip in ("10.0.0.5", "192.168.1.1", "172.16.0.9", "169.254.10.10", "0.0.0.0"):
        verdict = evaluate_fetch(f"http://{ip}/a", resolver=_identity_resolver)
        assert verdict["allowed"] is False, ip
        assert "内网/保留地址" in verdict["reason"]


def test_private_v6_rejected() -> None:
    """IPv6 保留段：::1 / fc00::/7 / fe80::/10。"""
    for host in ("[::1]", "[fc00::1]", "[fe80::1]"):
        verdict = evaluate_fetch(f"http://{host}/a", resolver=_identity_resolver)
        assert verdict["allowed"] is False, host
        assert "内网/保留地址" in verdict["reason"]


def test_unresolvable_hostname_rejected() -> None:
    """解析失败 = 拒绝（不虚报放行）。"""
    verdict = evaluate_fetch("https://nonexistent.invalid/a", resolver=lambda h: [])
    assert verdict["allowed"] is False
    assert "无法解析" in verdict["reason"]


def test_empty_url_rejected() -> None:
    assert evaluate_fetch("", resolver=_public_resolver)["allowed"] is False


# ---------- 域层：robots ----------


def test_robots_disallow_prefix_match() -> None:
    """Disallow 前缀匹配：/admin 命中 /admin/login。"""
    robots = "User-agent: *\nDisallow: /admin\n"
    assert robots_disallows(robots, "/admin/login") is True
    assert robots_disallows(robots, "/public/a.pdf") is False


def test_robots_empty_rule_allows_all() -> None:
    """空 Disallow: = 全允许（robots 协议语义）。"""
    robots = "User-agent: *\nDisallow:\n"
    assert robots_disallows(robots, "/anything") is False


def test_robots_only_star_group() -> None:
    """仅解析 User-agent: * 组；其他 agent 的 disallow 不影响判定。"""
    robots = "User-agent: Googlebot\nDisallow: /\nUser-agent: *\nDisallow: /private\n"
    assert robots_disallows(robots, "/private/x") is True
    assert robots_disallows(robots, "/open") is False


def test_robots_comments_and_case_insensitive() -> None:
    """注释行跳过；指令大小写不敏感。"""
    robots = "# comment\nuser-agent: *\nDISALLOW: /tmp\n"
    assert robots_disallows(robots, "/tmp/f") is True


# ---------- 域层：限流 ----------


def test_rate_limiter_fixed_window() -> None:
    """max=2：前两次放行第三次拒绝；窗口滚动后重新放行；不同键独立。"""
    t = {"now": 1000.0}
    limiter = RateLimiter(max_per_minute=2, clock=lambda: t["now"])
    assert limiter.allow("c1") is True
    assert limiter.allow("c1") is True
    assert limiter.allow("c1") is False
    t["now"] += 61.0
    assert limiter.allow("c1") is True
    assert limiter.allow("c2") is True


# ---------- API ----------


def test_fetch_check_api() -> None:
    """200/403：公网 https 放行；内网与 disallow 路径拒绝且原因可见。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        ok = client.post("/api/v1/web/fetch-check", json={"url": "https://example.edu/a.pdf"})
        assert ok.status_code == 200 and ok.json()["allowed"] is True

        bad = client.post("/api/v1/web/fetch-check", json={"url": "http://10.0.0.5/a"})
        assert bad.status_code == 403
        assert "内网/保留地址" in bad.json()["detail"]

        robots = "User-agent: *\nDisallow: /admin\n"
        disallowed = client.post(
            "/api/v1/web/fetch-check",
            json={"url": "https://example.edu/a", "robots_txt": robots, "path": "/admin/x"},
        )
        assert disallowed.status_code == 403
        assert "disallow" in disallowed.json()["detail"]


def test_fetch_check_rate_limit_429() -> None:
    """超限频率被拒绝：连发超上限（默认 30/min）后 429。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        codes = [
            client.post("/api/v1/web/fetch-check", json={"url": "https://example.edu/a"}).status_code
            for _ in range(31)
        ]
        assert codes[:30] == [200] * 30
        assert codes[30] == 429


def test_fetch_check_no_db_still_works() -> None:
    """无 DB 依赖：create_app(None) 下 fetch-check 照常工作。"""
    with TestClient(create_app(None)) as client:
        r = client.post("/api/v1/web/fetch-check", json={"url": "https://example.edu/a"})
        assert r.status_code == 200
