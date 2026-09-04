"""M9-01 多用户与认证基座：注册/登录/me/status + 全局门禁两态行为。

门禁语义（ADR 62 之后的 M9-01 决策）：
- AUTH_SECRET 未配置 = 认证关闭，status 如实透出 auth_enabled=false，
  业务路径直通——存量测试与本地面证调试零破坏；
- 配置后业务路径必须 Bearer token（register/login/status/version/health/docs
  豁免），坏 token/过期 token/缺 token 一律 401；
- 登录失败统一文案防用户名枚举；密码只存 bcrypt 哈希（72 字节上限显式拒绝）。
- M10-03 起浏览器登录态走 HttpOnly cookie；Bearer 仍保留给 CLI/API 客户端。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password, verify_password
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"
SECRET = "m9-auth-test-secret-0123456789abcdef"


@pytest.fixture()
def auth_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_SECRET", SECRET)
    get_settings.cache_clear()
    yield
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    get_settings.cache_clear()


@pytest.fixture()
def auth_off(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    get_settings.cache_clear()
    yield


def _register(client: TestClient, username="learner", password="password-123"):
    return client.post(
        "/api/v1/auth/register", json={"username": username, "password": password}
    )


def _login(client: TestClient, username="learner", password="password-123"):
    return client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )


# --- status 两态如实透出 ---


def test_status_disabled_by_default(auth_off) -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        body = client.get("/api/v1/auth/status").json()
        assert body == {"auth_enabled": False}


def test_status_enabled_with_secret(auth_on) -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        body = client.get("/api/v1/auth/status").json()
        assert body == {"auth_enabled": True}


# --- 门禁两态 ---


def test_disabled_gate_leaves_business_paths_open(auth_off) -> None:
    """认证关闭 = 存量行为不变（无 token 直通业务端点）。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        assert client.get("/api/v1/papers").status_code == 200


def test_enabled_gate_blocks_business_paths_without_token(auth_on) -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        r = client.get("/api/v1/papers")
        assert r.status_code == 401
        assert r.headers.get("www-authenticate") == "Bearer"
        # 豁免路径仍可达
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/version").status_code == 200
        assert client.get("/api/v1/auth/status").status_code == 200


def test_enabled_gate_rejects_forged_and_expired_tokens(auth_on) -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        forged = client.get(
            "/api/v1/papers", headers={"Authorization": "Bearer not-a-real-jwt"}
        )
        assert forged.status_code == 401

        # 正确 secret、过期时间在过去 → 仍拒绝
        expired = create_access_token("someone", secret=SECRET, expires_minutes=-1)
        stale = client.get("/api/v1/papers", headers={"Authorization": f"Bearer {expired}"})
        assert stale.status_code == 401


def test_cors_preflight_allows_credentials_for_configured_local_origin(auth_on) -> None:
    """Web 与 API 跨端口：credentials cookie 必须由显式 CORS allowlist 放行。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        response = client.options(
            "/api/v1/auth/me",
            headers={
                "Origin": "http://127.0.0.1:3010",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3010"
        assert response.headers["access-control-allow-credentials"] == "true"


# --- 注册 / 登录 / me 闭环 ---


def test_register_login_me_roundtrip(auth_on) -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        reg = _register(client)
        assert reg.status_code == 201, reg.text
        body = reg.json()
        assert body["username"] == "learner"
        assert body["id"]

        dup = _register(client)
        assert dup.status_code == 409

        tok = _login(client)
        assert tok.status_code == 200, tok.text
        token = tok.json()["access_token"]
        assert tok.json()["token_type"] == "bearer"

        me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["username"] == "learner"
        assert me.json()["id"] == body["id"]


def test_login_sets_http_only_cookie_and_cookie_authenticates_requests(auth_on) -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        _register(client)
        login = _login(client)

        cookie = login.headers["set-cookie"].lower()
        assert "aios_auth=" in cookie
        assert "httponly" in cookie
        assert "samesite=lax" in cookie
        assert "path=/" in cookie

        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200, me.text
        assert me.json()["username"] == "learner"

        papers = client.get("/api/v1/papers")
        assert papers.status_code == 200, papers.text


def test_invalid_auth_cookie_is_rejected_like_invalid_bearer(auth_on) -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        client.cookies.set("aios_auth", "not-a-real-jwt")
        assert client.get("/api/v1/auth/me").status_code == 401
        assert client.get("/api/v1/papers").status_code == 401


def test_logout_clears_cookie_and_requires_login_again(auth_on) -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        _register(client)
        _login(client)
        assert client.get("/api/v1/auth/me").status_code == 200

        logout = client.post("/api/v1/auth/logout")
        assert logout.status_code == 204
        assert "aios_auth=" in logout.headers["set-cookie"].lower()
        assert "max-age=0" in logout.headers["set-cookie"].lower()
        assert client.get("/api/v1/auth/me").status_code == 401
        assert client.get("/api/v1/papers").status_code == 401


def test_auth_cookie_same_site_none_requires_secure(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import Settings

    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", "none")
    monkeypatch.delenv("AUTH_COOKIE_SECURE", raising=False)
    with pytest.raises(ValueError, match="AUTH_COOKIE_SECURE=true"):
        Settings(_env_file=None)


def test_cors_wildcard_origin_rejected_under_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """M10-03 allow_credentials=True 后，"*" 会被反射成任意 Origin + 凭据放行 → 启动即拒。"""
    from app.core.config import Settings

    for bad in ("*", "http://localhost:3000,*", "https://*.example.com"):
        monkeypatch.setenv("CORS_ORIGINS", bad)
        with pytest.raises(ValueError, match="CORS_ORIGINS 不允许通配符"):
            Settings(_env_file=None)
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    assert Settings(_env_file=None).cors_origin_list == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


def test_login_fails_with_identical_message_for_wrong_password_and_missing_user(auth_on) -> None:
    """防枚举：密码错与用户不存在 → 同状态码同文案。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        _register(client)
        wrong_pw = _login(client, password="wrong-password")
        no_user = _login(client, username="ghost_user")
        assert wrong_pw.status_code == no_user.status_code == 401
        assert wrong_pw.json()["detail"] == no_user.json()["detail"]


def test_me_requires_token_even_when_registered_user_exists(auth_on) -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        _register(client)
        assert client.get("/api/v1/auth/me").status_code == 401


def test_me_rejects_token_of_deleted_user(auth_on) -> None:
    """token 有效但用户已不在 → 401（不因签名有效而放行幽灵身份）。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        # 正确 secret 签发、sub 指向不存在的用户 id —— 等价于用户被删除后的存量 token
        ghost_token = create_access_token("nonexistent-user-id", secret=SECRET, expires_minutes=30)
        ghost = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {ghost_token}"})
        assert ghost.status_code == 401


# --- 输入边界 ---


@pytest.mark.parametrize(
    "payload",
    [
        {"username": "ab", "password": "password-123"},  # 用户名过短
        {"username": "has space", "password": "password-123"},  # 非法字符
        {"username": "learner", "password": "short"},  # 密码过短
        {"username": "learner", "password": "x" * 129},  # 密码超 pydantic 上限
    ],
)
def test_register_rejects_invalid_credentials(auth_on, payload) -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        assert _register(client, **payload).status_code == 422


def test_register_rejects_password_over_bcrypt_72_bytes(auth_on) -> None:
    """40 个多字节字符 = 80 字节：过 pydantic（chars<=128），被 72 字节边界显式拒绝。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        r = _register(client, password="ü" * 40)
        assert r.status_code == 422
        assert "72" in r.json()["detail"]


# --- 密码哈希属性 ---


def test_password_hashes_are_salted_and_verifiable() -> None:
    h1, h2 = hash_password("same-password"), hash_password("same-password")
    assert h1 != h2, "bcrypt 盐必须让同密码两次哈希不同"
    assert h1 != "same-password"
    assert verify_password("same-password", h1)
    assert not verify_password("other-password", h1)


def test_verify_password_survives_corrupted_hash() -> None:
    """哈希字段损坏按校验失败处理，不向调用方泄内部异常。"""
    assert not verify_password("pw", "not-a-bcrypt-hash")


# --- M10-02 me 角色披露（前端渲染治理入口用；安全边界仍在 require_admin）---


def _me_role_client(tmp_path):
    """文件库 TestClient：promote 需要第二个 engine 连接，:memory: 不共享。"""
    db_url = f"sqlite+aiosqlite:///{tmp_path/'me_role.db'}"
    return TestClient(create_app(db_url))


def _promote(db_path, username: str) -> None:
    import asyncio

    from app.db.session import create_engine, make_sessionmaker
    from app.repositories.users import UserRepository

    async def _do() -> None:
        # engine 必须在同一个 event loop 内 dispose（M10-10，同 legacy 治理测试）。
        engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
        try:
            repo = UserRepository(make_sessionmaker(engine))
            assert await repo.set_role(username, "admin") is not None
        finally:
            await engine.dispose()

    asyncio.run(_do())


def test_me_returns_learner_role_by_default(auth_on, tmp_path) -> None:
    with _me_role_client(tmp_path) as client:
        _register(client)
        token = _login(client).json()["access_token"]
        me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["role"] == "learner"


def test_register_discloses_default_learner_role(auth_on) -> None:
    """注册响应同样带 role——新用户默认 learner。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        reg = _register(client)
        assert reg.status_code == 201
        assert reg.json()["role"] == "learner"


def test_me_reflects_admin_role_immediately_after_promote(auth_on, tmp_path) -> None:
    """角色实时读库（M9-04）：提升后旧 token 不重签，me 立即透出 admin。"""
    with _me_role_client(tmp_path) as client:
        _register(client)
        token = _login(client).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/v1/auth/me", headers=headers).json()["role"] == "learner"
        _promote(tmp_path / "me_role.db", "learner")
        assert client.get("/api/v1/auth/me", headers=headers).json()["role"] == "admin"


def test_cookie_admin_role_controls_governance_after_promote(auth_on, tmp_path) -> None:
    """治理授权实时读库：旧 cookie 不重签，但提升后立即可用、 learner 先 403。"""
    with _me_role_client(tmp_path) as client:
        _register(client)
        _login(client)
        assert client.get("/api/v1/audit").status_code == 403
        _promote(tmp_path / "me_role.db", "learner")
        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["role"] == "admin"
        assert client.get("/api/v1/audit").status_code == 200


def test_me_role_off_semantics_unchanged(auth_off) -> None:
    """auth off：me 仍要求 Bearer token（无用户上下文可披露），401 语义不变。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        assert client.get("/api/v1/auth/me").status_code == 401
