"""M14-95 micro-task A：一次性真实 FastAPI 认证后端启动器的聚焦测试。

覆盖（全部走真端口/真 socket，不靠 TestClient）：
- 状态（/api/v1/auth/status auth_enabled=true）；
- 匿名 401（/me 与受保护 GET 无 token）；
- 登录（合成用户 bcrypt 校验，200 + access_token）；
- /me（Authorization: Bearer <access_token>，仓库 login 契约的 JSON token）；
- 受保护 GET（无 token 401 → Bearer token 200）；
- 冷重启持久化（stop → 同库+同 secret 新进程：/me 仍 200）；
- logout 清理（204，清除 cookie 后无凭据 /me 401）；
- fail-closed 校验（缺 secret / 弱 secret 拒绝启动）；
- 脱敏（redacted_status 与 redact 永不回显 secret/口令/token）。

契约：测试只用 Authorization: Bearer（repo auth.py TokenOut 的 access_token）；
cookie 行为不解析、不校验。不跑 emulator；不 commit；库为 tmp_path 隔离 SQLite。
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from app.ops.auth_smoke_server import AuthSmokeServer, _child_argv, free_loopback_port

_SECRET = "m14-95-auth-smoke-secret-0123456789abcdef-32B"  # 38 字节，过 M9-04 门禁
_PASSWORD = "m14-95-smoke-password-abcdef0123456789"
_USER = "m1495_smoke_user"


@pytest.fixture(autouse=True)
def _launcher_uses_test_interpreter(monkeypatch):
    """launcher 子进程必须与 pytest 宿主同一 venv 解释器（宿主自带全部依赖）。

    默认 sys.executable 在 Windows 上可能是系统 Python（无 aiosqlite）——
    fail-closed 启动会误报。测试环境下显式锁定当前解释器。
    """
    monkeypatch.setenv("AIOS_AUTH_SMOKE_PYTHON", sys.executable)


def _http(
    method: str, url: str, *, token: str | None = None, body: dict | None = None
) -> tuple[int, dict, bytes]:
    """最小真实 HTTP 客户端（urllib）：返回 (status, headers, raw body)。

    token 走 Authorization: Bearer；body 走 JSON POST。
    401/404 等 4xx/5xx 不抛（按 status 返回），仅网络错误才 raise。
    """
    req = urllib.request.Request(url, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    payload = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=payload) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _post_json(
    url: str, body: dict | None = None, token: str | None = None
) -> tuple[int, dict, bytes]:
    return _http("POST", url, token=token, body=body)


def _login(base: str) -> str:
    """login 契约（repo auth.py TokenOut）：200 + JSON access_token（Bearer 用）。"""
    status, _, raw = _post_json(
        f"{base}/api/v1/auth/login", {"username": _USER, "password": _PASSWORD}
    )
    assert status == 200, f"login {status}: {raw.decode(errors='replace')}"
    return json.loads(raw)["access_token"]


@pytest.fixture()
def server(tmp_path):
    srv = AuthSmokeServer(tmp_path, username=_USER)
    try:
        srv.start(_SECRET, _PASSWORD)
        yield srv
    finally:
        srv.stop()


def test_status_enabled(server) -> None:
    status, _, raw = _http("GET", f"{server.info['base_url']}/api/v1/auth/status")
    assert status == 200, raw.decode()
    assert json.loads(raw) == {"auth_enabled": True}


def test_anonymous_me_returns_401(server) -> None:
    status, _, raw = _http("GET", f"{server.info['base_url']}/api/v1/auth/me")
    assert status == 401, f"anonymous /me expected 401, got {status}: {raw.decode()}"


def test_anonymous_protected_get_returns_401(server) -> None:
    base = server.info["base_url"]
    status, headers, _ = _http("GET", f"{base}/api/v1/smoke/protected")
    assert status == 401, f"protected GET without token expected 401, got {status}"
    assert headers.get("www-authenticate") == "Bearer"


def test_login_returns_bearer_access_token(server) -> None:
    base = server.info["base_url"]
    status, _, raw = _post_json(
        f"{base}/api/v1/auth/login", {"username": _USER, "password": _PASSWORD}
    )
    assert status == 200, raw.decode()
    body = json.loads(raw)
    assert body["access_token"], "TokenOut.access_token 缺失"
    assert body.get("token_type", "bearer") == "bearer"


def test_login_wrong_password_returns_401(server) -> None:
    base = server.info["base_url"]
    status, _, raw = _post_json(
        f"{base}/api/v1/auth/login", {"username": _USER, "password": "wrong-password-9999"}
    )
    assert status == 401, f"wrong password expected 401, got {status}: {raw.decode()}"


def test_me_with_bearer_token(server) -> None:
    base = server.info["base_url"]
    token = _login(base)
    status, _, raw = _http("GET", f"{base}/api/v1/auth/me", token=token)
    assert status == 200, raw.decode()
    body = json.loads(raw)
    assert body["username"] == _USER
    assert body["role"] == "learner"
    assert body["id"]


def test_protected_get_with_bearer_token(server) -> None:
    base = server.info["base_url"]
    token = _login(base)
    status, _, raw = _http("GET", f"{base}/api/v1/smoke/protected", token=token)
    assert status == 200, raw.decode()
    assert json.loads(raw) == {"status": "ok", "endpoint": "smoke-protected"}


def test_cold_restart_persistence(tmp_path) -> None:
    """stop → 同库+同 secret 新进程：合成用户仍在（Bearer /me 仍 200）。"""
    srv1 = AuthSmokeServer(tmp_path, username=_USER)
    info1 = srv1.start(_SECRET, _PASSWORD)
    db = info1["db_path"]
    token1 = _login(info1["base_url"])
    assert _http("GET", f"{info1['base_url']}/api/v1/auth/me", token=token1)[0] == 200
    srv1.stop()

    srv2 = AuthSmokeServer(tmp_path, username=_USER, db_path=db)
    info2 = srv2.start(_SECRET, _PASSWORD)
    try:
        assert info2["db_path"] == db
        # 冷重启后重新 login（同用户），Bearer /me 仍 200
        token2 = _login(info2["base_url"])
        status, _, raw = _http("GET", f"{info2['base_url']}/api/v1/auth/me", token=token2)
        assert status == 200, raw.decode()
        assert json.loads(raw)["username"] == _USER
        # 冷重启后旧 token（同 secret 签发）仍有效（库持久 + secret 不变）
        status, _, raw = _http("GET", f"{info2['base_url']}/api/v1/auth/me", token=token1)
        assert status == 200, raw.decode()
    finally:
        srv2.stop()


def test_logout_cleanup_returns_204(server) -> None:
    base = server.info["base_url"]
    status, _, raw = _http("POST", f"{base}/api/v1/auth/logout")
    assert status == 204, f"logout expected 204, got {status}: {raw.decode()}"
    # logout 仅清 cookie（仓库契约：无需有效凭据，幂等）；
    # 无 Bearer token 时 /me 仍 401（Bearer 不受 logout 影响）
    status, _, raw = _http("GET", f"{base}/api/v1/auth/me")
    assert status == 401, f"no-credential /me expected 401 after logout, got {status}"


def test_fail_closed_missing_secret(tmp_path) -> None:
    srv = AuthSmokeServer(tmp_path, username=_USER)
    with pytest.raises(RuntimeError, match="AUTH_SECRET"):
        srv.start("", _PASSWORD)


def test_fail_closed_weak_secret(tmp_path) -> None:
    srv = AuthSmokeServer(tmp_path, username=_USER)
    with pytest.raises(RuntimeError, match="32 字节|至少 32"):
        srv.start("short-secret", _PASSWORD)


def test_redacted_status_never_leaks_secret_or_password(server) -> None:
    info = server.info
    blob = json.dumps(info, ensure_ascii=False)
    assert _SECRET not in blob
    assert _PASSWORD not in blob
    assert info["auth_secret"] == "<configured>"
    assert info["seed_password"] == "[REDACTED]"


def test_redact_scrubs_secret_password_and_token(server) -> None:
    token = _login(server.info["base_url"])
    raw = f"Bearer {token} pw={_PASSWORD} s={_SECRET}"
    out = server.redact(raw, [token])
    assert _SECRET not in out
    assert _PASSWORD not in out
    assert token not in out
    assert "[REDACTED]" in out


def test_free_loopback_port_is_task_owned_and_distinct() -> None:
    p1, p2 = free_loopback_port(), free_loopback_port()
    assert p1 != p2
    assert 0 < p1 < 65536 and 0 < p2 < 65536


# ------------------------------------------------ db_path 解析（B4 回归）----
# 根因：launcher 传相对 db_path（"m14-95-auth-smoke.db"）时，宿主种子按
# 当前 cwd 写 worktree 根，而子进程 uvicorn 的 cwd 是 services/api，
# 实际打开 services/api/m14-95-auth-smoke.db —— 宿主种子与子进程数据库
# 错位，登录 401。修复契约：相对 db_path 一律解析到 work_dir 下；绝对
# db_path 保持原语义。


def test_relative_db_path_resolves_under_work_dir(tmp_path) -> None:
    """相对 db_path：db_path 属性与 info db_path 均为 work_dir 绝对路径。"""
    srv = AuthSmokeServer(tmp_path, username=_USER, db_path="rel-smoke.db")
    try:
        info = srv.start(_SECRET, _PASSWORD)
    finally:
        srv.stop()
    expected = tmp_path / "rel-smoke.db"
    assert srv.db_path == str(expected)
    assert info["db_path"] == str(expected)
    # work_dir 绝对路径，且绝不在 services/api 下
    assert expected.is_absolute()
    assert "services" not in expected.parts
    assert "api" not in expected.parts
    # 库文件实际落在 work_dir（种子写过的同一个文件）
    assert (tmp_path / "rel-smoke.db").is_file()


def test_relative_db_path_child_db_url_uses_work_dir(tmp_path) -> None:
    """子进程 argv 的 DATABASE_URL 必须指向 work_dir 绝对路径。

    直接断言 _child_argv 产出的 import_code 里的 create_app(database_url=...)
    —— 即 uvicorn 子进程真正打开的库文件。"""
    srv = AuthSmokeServer(tmp_path, username=_USER, db_path="rel-smoke.db")
    # 与 AuthSmokeServer.start 一致：db_url 用 as_posix() 拼 sqlite URL
    db_url = f"sqlite+aiosqlite:///{srv.db_path.replace(chr(92), '/')}"
    argv = _child_argv(12345, tmp_path, db_url)
    import_code = argv[2]
    posix_db = Path(srv.db_path).as_posix()
    # db URL 里的库路径是 work_dir 绝对路径（as_posix 形式，同 start 逻辑）
    assert posix_db in import_code
    # 且不以裸相对路径出现（不会落到子进程 cwd services/api 下）
    assert "sqlite+aiosqlite:///rel-smoke.db" not in import_code
    assert "services/api/rel-smoke.db" not in import_code


def test_relative_db_path_e2e_login_works(tmp_path) -> None:
    """端到端：相对 db_path 下宿主种子与子进程登录看到同一库（登录 200）。

    这就是修复前 401 的场景：子进程 cwd=services/api，若 db_url 保持相对，
    子进程会打开 services/api/rel-smoke.db（无种子），登录 401。"""
    srv = AuthSmokeServer(tmp_path, username=_USER, db_path="e2e-rel.db")
    try:
        info = srv.start(_SECRET, _PASSWORD)
        assert info["db_path"] == str(tmp_path / "e2e-rel.db")
        token = _login(info["base_url"])
        assert token
    finally:
        srv.stop()


def test_relative_db_path_never_lands_in_services_api(tmp_path) -> None:
    """显式断言解析后路径不在 services/api（回归：宿主/子进程错位根因）。"""
    srv = AuthSmokeServer(tmp_path, username=_USER, db_path="m14-95-auth-smoke.db")
    resolved = srv.db_path
    assert Path(resolved).is_absolute()
    api_dir = Path("services/api").resolve()
    assert resolved.startswith(str(tmp_path))
    assert not resolved.startswith(str(api_dir))
    assert str(tmp_path) in resolved


def test_absolute_db_path_kept_as_is(tmp_path) -> None:
    """绝对 db_path 保持原语义：原样使用（解析为同一绝对路径），不二次拼接。"""
    abs_db = (tmp_path / "elsewhere" / "abs-smoke.db")
    abs_db.parent.mkdir(parents=True)
    srv = AuthSmokeServer(tmp_path, username=_USER, db_path=str(abs_db))
    try:
        info = srv.start(_SECRET, _PASSWORD)
    finally:
        srv.stop()
    assert srv.db_path == str(abs_db)
    assert info["db_path"] == str(abs_db)
    assert (abs_db).is_file()
    # 没有误写到 work_dir 下
    assert not (tmp_path / "abs-smoke.db").exists()


def test_relative_db_path_after_work_dir_is_file(tmp_path) -> None:
    """构造后 db_path 属性已解析（无需 start），与 work_dir 拼接结果一致。"""
    srv = AuthSmokeServer(tmp_path, username=_USER, db_path="post-con.db")
    assert srv.db_path == str(tmp_path / "post-con.db")
    assert Path(srv.db_path).is_absolute()


def test_default_db_path_stays_in_work_dir(tmp_path) -> None:
    """无 db_path 时的随机默认库仍在 work_dir 下（既有行为不变）。"""
    srv = AuthSmokeServer(tmp_path, username=_USER)
    try:
        info = srv.start(_SECRET, _PASSWORD)
        db = Path(info["db_path"])
        assert db.is_absolute()
        assert str(db).startswith(str(tmp_path))
        assert "m14-95-auth-smoke-" in db.name
        assert db.is_file()
    finally:
        srv.stop()


# --------------------------------- 相对 work_dir（B5/B6 回归）----
# 漂移根因：AuthSmokeServer 只把相对 db_path 解析到 work_dir，但没有把
# work_dir 自身绝对化。launcher 传相对 work_dir（如 "work"）时，宿主
# 种子在宿主 cwd（仓库根）下打开 work/<db>；而子进程 uvicorn 的 cwd
# 是 services/api，同一个裸相对 work_dir 会解析成
# services/api/work/<db> —— 宿主种子与子进程数据库错位，登录 401。
# 修复契约（B6）：构造时 Path(work_dir).resolve() 归一为绝对路径，
# 相对 db_path 继续落在该绝对 work_dir 下；db_url 用 as_posix()。


def test_relative_work_dir_resolves_to_absolute_cwd_based(tmp_path, monkeypatch) -> None:
    """相对 work_dir + chdir 到临时目录：work_dir 与 db_path 均为绝对，
    且指向 tmp/workdir，绝不在 services/api 下。"""
    monkeypatch.chdir(tmp_path)
    work_dir_rel = "workdir"
    srv = AuthSmokeServer(work_dir_rel, username=_USER, db_path="rel-wd.db")
    expected_dir = (tmp_path / "workdir").resolve()
    expected_db = expected_dir / "rel-wd.db"
    # work_dir 已绝对化且落在临时目录（相对解析基于当前 cwd）
    assert srv.work_dir == expected_dir
    assert srv.work_dir.is_absolute()
    assert str(srv.work_dir) == str(expected_dir)
    # 相对 db_path 落在该绝对 work_dir 下
    assert Path(srv.db_path) == expected_db
    assert Path(srv.db_path).is_absolute()
    # 绝不落到子进程 cwd services/api 下
    api_dir = Path("services/api").resolve()
    assert not str(srv.db_path).startswith(str(api_dir))
    # 库文件真正在绝对 work_dir 里（start 后 db_url 用 as_posix）
    try:
        info = srv.start(_SECRET, _PASSWORD)
    finally:
        srv.stop()
    assert info["db_path"] == str(expected_db)
    assert expected_db.is_file()
    # 子进程 argv 的 db URL 必须包含该绝对路径的 as_posix 形式
    db_url = f"sqlite+aiosqlite:///{expected_db.as_posix()}"
    argv = _child_argv(12345, srv.work_dir, db_url)
    import_code = argv[2]
    assert expected_db.as_posix() in import_code
    # 绝不含裸相对路径
    assert "sqlite+aiosqlite:///rel-wd.db" not in import_code
    assert "sqlite+aiosqlite:///workdir/rel-wd.db" not in import_code


def test_relative_work_dir_child_db_url_absolute_posix(tmp_path, monkeypatch) -> None:
    """子进程 db URL 恒为绝对 POSIX 形式（不随宿主 cwd 漂移）。"""
    monkeypatch.chdir(tmp_path)
    srv = AuthSmokeServer("workdir", username=_USER, db_path="rel-wd.db")
    expected_db = (tmp_path / "workdir" / "rel-wd.db").resolve()
    # 与 AuthSmokeServer.start 一致：db_url 用 as_posix() 拼 sqlite URL
    db_url = f"sqlite+aiosqlite:///{expected_db.as_posix()}"
    argv = _child_argv(12345, srv.work_dir, db_url)
    import_code = argv[2]
    # 绝对路径的 POSIX 形式出现在 URL 里
    assert expected_db.as_posix() in import_code
    # 且 db_url 字符串本身是绝对 POSIX（as_posix 输出以 / 分隔，无 \\）
    assert "\\" not in db_url
    assert db_url.startswith("sqlite+aiosqlite:///")
    # 不以裸相对 / 子进程 cwd services/api 形式出现
    assert "rel-wd.db" not in db_url.replace(expected_db.as_posix(), "")


def test_relative_work_dir_e2e_start_login(tmp_path, monkeypatch) -> None:
    """端到端（真实 start/login）：相对 work_dir + 相对 db_path 构造，
    子进程 cwd=services/api 时宿主种子与子进程看到同一库，登录 200。"""
    monkeypatch.chdir(tmp_path)
    srv = AuthSmokeServer("workdir", username=_USER, db_path="e2e-rel-wd.db")
    try:
        info = srv.start(_SECRET, _PASSWORD)
        expected_db = (tmp_path / "workdir" / "e2e-rel-wd.db").resolve()
        assert Path(info["db_path"]) == expected_db
        token = _login(info["base_url"])
        assert token
    finally:
        srv.stop()
