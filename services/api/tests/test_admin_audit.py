"""M9-04 管理员授权、治理审计与泄漏修复。

覆盖矩阵：
1. learner 对治理端点 403 / admin 可用（sources verify/license、DAG publish、
   课程生成/导入、变式、试卷抽取 approve/reject）；
2. 搜索记录归属：B 读 A 的 query_id 404、本人 200、admin 可读全部（治理语义）；
3. auth off 本地模式回归（治理端点放行 + 审计可读——存量语义零破坏）；
4. 审计内容断言（actor/action/target/before/after/request_id）与 learner 403；
5. admin CLI promote/demote/list 真实执行（文件 SQLite 库）；
6. compose CORS 与 AIOS_WEB_PORT 联动渲染断言。
"""
from __future__ import annotations

import os

import pytest
import yaml
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.security import validate_auth_secret, validate_exposure
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"
SECRET = "m9-04-admin-audit-secret-0123456789abcdef"
COMPOSE_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "infra", "docker-compose.yml")
)


@pytest.fixture()
def auth_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_SECRET", SECRET)
    get_settings.cache_clear()
    yield
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    get_settings.cache_clear()


class User:
    def __init__(self, client: TestClient, name: str) -> None:
        body = {"username": name, "password": "password-123"}
        client.post("/api/v1/auth/register", json=body)
        token = client.post("/api/v1/auth/login", json=body).json()["access_token"]
        self.name = name
        self.headers = {"Authorization": f"Bearer {token}"}


def _promote_to_admin(db_url: str, username: str) -> None:
    """测试内模拟运维提升（等效于 CLI 的 repo.set_role 路径）。"""
    import asyncio

    from app.db.session import create_engine, make_sessionmaker
    from app.repositories.users import UserRepository

    async def _do_real() -> None:
        # engine 必须在同一个 event loop 内 dispose（M10-10，同 legacy 治理测试）。
        engine = create_engine(db_url)
        try:
            repo = UserRepository(make_sessionmaker(engine))
            updated = await repo.set_role(username, "admin")
            assert updated is not None
        finally:
            await engine.dispose()

    asyncio.run(_do_real())


# --- 治理端点权限矩阵 ---


PAPER = {
    "title": "Admin Paper",
    "duration_seconds": 1800,
    "questions": [
        {
            "question": {
                "question_type": "mcq",
                "stem": "治理卷",
                "options": ["a", "b"],
                "answer": {"option_index": 1},
                "explanation": "x",
                "concept_ids": ["c"],
                "difficulty": 1,
            },
            "score": 1.0,
        }
    ],
}


def _paper_id(client: TestClient, headers: dict) -> str:
    client.post("/api/v1/papers/import", json=[PAPER], headers=headers)
    papers = client.get("/api/v1/papers", headers=headers).json()
    return next(p["id"] for p in papers if p["title"] == "Admin Paper")


@pytest.fixture()
def client_and_db(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path/'admin_audit.db'}"
    with TestClient(create_app(db_url)) as c:
        yield c, db_url


def test_learner_forbidden_on_governance_endpoints(client_and_db, auth_on) -> None:
    client, _db = client_and_db
    learner = User(client, "gov_learner")

    # source verify / license
    r = client.post(
        "/api/v1/sources",
        json={"id": "src_x", "name": "x", "source_type": "oer", "license_state": "OPEN_LICENSE",
              "trust_tier": "B", "authority_score": 5, "homepage": "https://example.edu/x"},
        headers=learner.headers,
    )
    assert r.status_code == 403, r.text
    r = client.post("/api/v1/sources/src_gov1/verify", headers=learner.headers)
    assert r.status_code in (403,), r.text  # 门禁先于 404 —— 存在性不泄露
    r = client.post("/api/v1/sources/src_gov1/license", json={"state": "PUBLIC_ACCESS"}, headers=learner.headers)
    assert r.status_code == 403

    # DAG publish
    r = client.post(
        "/api/v1/concept-dag/versions",
        json={"nodes": [{"id": "c9", "canonical_name": "c", "subject": "math",
                          "difficulty": 1, "aliases": [], "evidence_ids": []}],
              "edges": [], "note": "x"},
        headers=learner.headers,
    )
    assert r.status_code == 403

    # 草稿审核（四类）
    for path in (
        "/api/v1/courses/generation-drafts/d1/approve",
        "/api/v1/courses/generation-drafts/d1/reject",
        "/api/v1/questions/variant-drafts/d1/approve",
        "/api/v1/papers/import-drafts/d1/approve",
        "/api/v1/courses/import-drafts/d1/approve",
    ):
        r = client.post(path, json={"note": "n"}, headers=learner.headers)
        assert r.status_code == 403, f"{path} -> {r.status_code}"


def test_admin_can_perform_governance_and_audits(client_and_db, auth_on, tmp_path) -> None:
    client, db_url = client_and_db
    admin_user = User(client, "gov_admin")
    _promote_to_admin(db_url, "gov_admin")

    _paper_id(client, admin_user.headers)  # 导入公共卷供治理链路用

    # source create + verify + license（admin 全通）
    r = client.post(
        "/api/v1/sources",
        json={"id": "src_gov1", "name": "x", "source_type": "oer", "license_state": "UNKNOWN",
              "trust_tier": "B", "authority_score": 5, "homepage": "https://example.edu/src"},
        headers=admin_user.headers,
    )
    assert r.status_code == 201, r.text
    assert client.post("/api/v1/sources/src_gov1/verify", headers=admin_user.headers).status_code == 200
    lic = client.post(
        "/api/v1/sources/src_gov1/license", json={"state": "PUBLIC_ACCESS"},
        headers=admin_user.headers,
    )
    assert lic.status_code == 200, lic.text

    # DAG publish
    dag = client.post(
        "/api/v1/concept-dag/versions",
        json={"nodes": [{"id": "c1", "canonical_name": "概念1", "subject": "math",
                          "difficulty": 2, "aliases": [], "evidence_ids": []}],
              "edges": [], "note": "v1"},
        headers=admin_user.headers,
    )
    assert dag.status_code == 201, dag.text

    # 审计内容断言（admin 读）
    audit = client.get("/api/v1/audit", headers=admin_user.headers)
    assert audit.status_code == 200
    entries = audit.json()
    actions = {e["action"] for e in entries}
    assert {"source.create", "source.verify", "source.license_change", "dag.publish"} <= actions
    lic_entry = next(e for e in entries if e["action"] == "source.license_change")
    assert lic_entry["actor_username"] == "gov_admin"
    assert lic_entry["before"] == {"license_state": "UNKNOWN"}
    assert lic_entry["after"] == {"license_state": "PUBLIC_ACCESS"}
    assert lic_entry["request_id"]


def test_learner_cannot_read_audit_log(client_and_db, auth_on) -> None:
    client, _db = client_and_db
    learner = User(client, "audit_learner")
    r = client.get("/api/v1/audit", headers=learner.headers)
    assert r.status_code == 403


def test_learner_cannot_read_others_search_records(client_and_db, auth_on) -> None:
    client, _db = client_and_db
    alice = User(client, "search_alice")
    bob = User(client, "search_bob")

    executed = client.post(
        "/api/v1/search/queries",
        json={"query": "快速排序", "limit": 5},
        headers=alice.headers,
    )
    assert executed.status_code == 200, executed.text
    query_id = executed.json()["query_id"]

    assert client.get(f"/api/v1/search/queries/{query_id}", headers=alice.headers).status_code == 200
    assert client.get(f"/api/v1/search/queries/{query_id}", headers=bob.headers).status_code == 404


def test_admin_reads_all_search_records(client_and_db, auth_on) -> None:
    client, db_url = client_and_db
    admin_user = User(client, "search_admin")
    _promote_to_admin(db_url, "search_admin")
    alice = User(client, "search_alice2")

    executed = client.post(
        "/api/v1/search/queries",
        json={"query": "动态规划", "limit": 5},
        headers=alice.headers,
    )
    query_id = executed.json()["query_id"]
    assert client.get(
        f"/api/v1/search/queries/{query_id}", headers=admin_user.headers
    ).status_code == 200


def test_auth_off_keeps_local_mode_semantics(client_and_db) -> None:
    """auth off：治理端点放行 + 审计可读（本地单用户兼容，存量语义零破坏）。"""
    client, _db = client_and_db
    r = client.post(
        "/api/v1/sources",
        json={"id": "src_local", "name": "x", "source_type": "oer", "license_state": "UNKNOWN",
              "trust_tier": "B", "authority_score": 5, "homepage": "https://example.edu/local"},
    )
    assert r.status_code == 201
    assert client.get("/api/v1/audit").status_code == 200


# --- admin CLI（真实执行，文件 SQLite 库）---


def test_admin_cli_promote_demote_list(auth_on, tmp_path, monkeypatch, capsys) -> None:
    from app.ops import cli as cli_mod

    db_path = tmp_path / "cli_users.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    # 预建表（CLI 不跑 migration；等效生产 DB 已迁移态）
    import asyncio

    from app.db.base import Base
    from app.db.session import create_engine

    async def _create():
        engine = create_engine(db_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(_create())

    monkeypatch.setenv("DATABASE_URL", db_url)
    get_settings.cache_clear()

    class _A:
        action = "list"
        username = None
        db_url = None

    # 先造一个用户（经 CLI 同款仓储）
    from app.db.session import make_sessionmaker
    from app.repositories.users import UserRepository

    async def _seed():
        # engine 必须在同一个 event loop 内 dispose（M10-10，同 legacy 治理测试）。
        engine = create_engine(db_url)
        try:
            repo = UserRepository(make_sessionmaker(engine))
            await repo.create("cli_user", "x")
        finally:
            await engine.dispose()

    asyncio.run(_seed())

    assert cli_mod._run_admin(type("A", (), {"action": "list", "username": None, "db_url": None})()) == 0
    out = capsys.readouterr().out
    assert "cli_user" in out

    assert cli_mod._run_admin(type("A", (), {"action": "promote", "username": "cli_user", "db_url": None})()) == 0
    out = capsys.readouterr().out
    assert "cli_user: learner -> admin" in out

    # 幂等：重复 promote 明示无变更
    assert cli_mod._run_admin(type("A", (), {"action": "promote", "username": "cli_user", "db_url": None})()) == 0
    assert "无需变更" in capsys.readouterr().out

    assert cli_mod._run_admin(type("A", (), {"action": "demote", "username": "cli_user", "db_url": None})()) == 0
    assert "cli_user: admin -> learner" in capsys.readouterr().out

    # 未知用户
    assert cli_mod._run_admin(type("A", (), {"action": "promote", "username": "ghost", "db_url": None})()) == 2


# --- 部署安全 ---


def test_validate_auth_secret_production_fail_closed() -> None:
    import pytest as _pytest

    with _pytest.raises(RuntimeError):
        validate_auth_secret(None, app_env="production")
    with _pytest.raises(RuntimeError):
        validate_auth_secret("short", app_env="production")
    with _pytest.raises(RuntimeError):
        validate_auth_secret("aios-local-dev-secret-7d21b9e4c8a3", app_env="production")
    validate_auth_secret("x" * 32, app_env="production")  # 合规通过
    # 非生产：不配置放行；配置了但太短明确拒绝
    validate_auth_secret(None, app_env="development")
    with _pytest.raises(RuntimeError):
        validate_auth_secret("short", app_env="development")


def test_compose_cors_follows_web_port() -> None:
    with open(COMPOSE_FILE, encoding="utf-8") as fh:
        compose = yaml.safe_load(fh)
    cors = compose["services"]["api"]["environment"]["CORS_ORIGINS"]
    assert "AIOS_CORS_ORIGINS" in cors, "必须支持完整覆盖"
    assert "${AIOS_WEB_PORT:-3000}" in cors, "自定义 Web 端口时 CORS 默认联动"


def test_compose_exposes_auth_cookie_switches() -> None:
    """HTTPS 部署必须能把 Secure/SameSite 传入 API 容器。"""
    with open(COMPOSE_FILE, encoding="utf-8") as fh:
        compose = yaml.safe_load(fh)
    env = compose["services"]["api"]["environment"]
    assert env["AUTH_COOKIE_SECURE"] == "${AIOS_AUTH_COOKIE_SECURE:-false}"
    assert env["AUTH_COOKIE_SAMESITE"] == "${AIOS_AUTH_COOKIE_SAMESITE:-lax}"


# --- M9-06 公开暴露 fail-closed（四类路径） ---


def test_exposure_loopback_is_safe_by_default() -> None:
    """loopback 绑定：任何 APP_ENV/secret 组合都放行（本机开发体验不受影响）。"""
    validate_exposure(
        host_bind_ip="127.0.0.1", app_env="development",
        auth_secret=None, livekit_api_secret=None,
    )
    validate_exposure(
        host_bind_ip="localhost", app_env="docker",
        auth_secret="aios-local-dev-secret-7d21b9e4c8a3",
        livekit_api_secret="ailos-local-dev-secret-0f4c9a1e7b2d",
    )


def test_exposure_public_binding_requires_production_env() -> None:
    with pytest.raises(RuntimeError, match="APP_ENV=production"):
        validate_exposure(
            host_bind_ip="0.0.0.0", app_env="docker",
            auth_secret=None, livekit_api_secret=None,
        )


def test_exposure_public_binding_requires_strong_secrets() -> None:
    """公开绑定 + production：弱/缺/占位 secret 一律拒绝。"""
    common = {"host_bind_ip": "0.0.0.0", "app_env": "production"}
    with pytest.raises(RuntimeError, match="AUTH_SECRET"):
        validate_exposure(**common, auth_secret=None, livekit_api_secret="x" * 40)
    with pytest.raises(RuntimeError, match="AUTH_SECRET"):
        validate_exposure(**common, auth_secret="short", livekit_api_secret="x" * 40)
    with pytest.raises(RuntimeError, match="AUTH_SECRET"):
        validate_exposure(
            **common,
            auth_secret="aios-local-dev-secret-7d21b9e4c8a3",
            livekit_api_secret="x" * 40,
        )
    with pytest.raises(RuntimeError, match="LIVEKIT_API_SECRET"):
        validate_exposure(**common, auth_secret="y" * 40, livekit_api_secret=None)
    with pytest.raises(RuntimeError, match="LIVEKIT_API_SECRET"):
        validate_exposure(
            **common, auth_secret="y" * 40,
            livekit_api_secret="ailos-local-dev-secret-0f4c9a1e7b2d",
        )


def test_exposure_valid_production_config_passes() -> None:
    validate_exposure(
        host_bind_ip="0.0.0.0", app_env="production",
        auth_secret="prod-auth-secret-0123456789abcdef012345",
        livekit_api_secret="prod-livekit-secret-0123456789abcdef01",
        cors_origins="https://learn.example.com",
        public_livekit_url="wss://voice.example.com",
    )


def test_exposure_public_binding_rejects_local_only_cors() -> None:
    """M9-07: 公开绑定但 CORS 仍 localhost-only → 明确报错要求 AIOS_CORS_ORIGINS。"""
    with pytest.raises(RuntimeError, match="AIOS_CORS_ORIGINS"):
        validate_exposure(
            host_bind_ip="0.0.0.0", app_env="production",
            auth_secret="prod-auth-secret-0123456789abcdef012345",
            livekit_api_secret="prod-livekit-secret-0123456789abcdef01",
            cors_origins="http://localhost:3000,http://127.0.0.1:3000",
        )


def test_compose_livekit_ports_bound_to_loopback_by_default() -> None:
    with open(COMPOSE_FILE, encoding="utf-8") as fh:
        compose = yaml.safe_load(fh)
    ports = compose["services"]["livekit"]["ports"]
    assert "${AIOS_BIND_IP:-127.0.0.1}:7881:7881" in ports, "LiveKit TCP 必须显式绑定 IP"
    assert "${AIOS_BIND_IP:-127.0.0.1}:7882-7892:7882-7892/udp" in ports, (
        "LiveKit UDP 必须显式绑定 IP"
    )
    assert not any(p_.startswith(("7881", "7882-")) for p_ in ports), (
        "不得存在裸宿主绑定的 LiveKit 端口"
    )
    # APP_ENV 支持部署覆盖（fail-closed 校验的入口）
    assert compose["services"]["api"]["environment"]["APP_ENV"] == "${AIOS_APP_ENV:-docker}"
    env = compose["services"]["api"]["environment"]
    assert env.get("HOST_BIND_IP") == "${AIOS_BIND_IP:-127.0.0.1}", (
        "宿主绑定意图必须以 HOST_BIND_IP 传入 API 容器（对齐 settings.host_bind_ip）"
    )


# --- M9-07 Source verify 回归 ---


def test_source_verify_single_mutation_single_audit(client_and_db, auth_on) -> None:
    """admin verify：审计 source.verify 恰好一条；verified 状态只变更一次；404 无审计。"""
    client, db_url = client_and_db
    admin_user = User(client, "verify_admin")
    _promote_to_admin(db_url, "verify_admin")

    r = client.post(
        "/api/v1/sources",
        json={"id": "src_v", "name": "s", "source_type": "oer", "license_state": "UNKNOWN",
              "trust_tier": "B", "authority_score": 5, "homepage": "https://example.edu/v"},
        headers=admin_user.headers,
    )
    assert r.status_code == 201

    # 404 verify：不产生任何成功审计
    missing = client.post("/api/v1/sources/src_absent/verify", headers=admin_user.headers)
    assert missing.status_code == 404

    ok = client.post("/api/v1/sources/src_v/verify", headers=admin_user.headers)
    assert ok.status_code == 200
    first_verified_at = ok.json()["last_verified_at"]

    # 审计中 source.verify 对该 source 恰好一条，且 actor/request_id 真实
    entries = [
        e for e in client.get("/api/v1/audit", headers=admin_user.headers).json()
        if e["action"] == "source.verify" and e["target_id"] == "src_v"
    ]
    assert len(entries) == 1, f"source.verify 应恰好一条审计，实际 {len(entries)}"
    assert entries[0]["after"] == {"verified": True}
    assert entries[0]["actor_username"] == "verify_admin"
    assert entries[0]["request_id"]

    # 重复 verify 是幂等动作（仍 200），但审计条数保持 1 条/次——再验一次计 2 条
    ok2 = client.post("/api/v1/sources/src_v/verify", headers=admin_user.headers)
    assert ok2.status_code == 200
    entries_after = [
        e for e in client.get("/api/v1/audit", headers=admin_user.headers).json()
        if e["action"] == "source.verify" and e["target_id"] == "src_v"
    ]
    assert len(entries_after) == 2, "每次成功 verify 恰好一条审计（不重复不缺失）"
    # 状态时间戳：第二次 verify 更新了 last_verified_at（审计/状态各只反映一次调用）
    assert ok2.json()["last_verified_at"] >= first_verified_at

    # 404 仍无审计（src_absent 不出现）
    assert not [
        e for e in client.get("/api/v1/audit", headers=admin_user.headers).json()
        if e["target_id"] == "src_absent"
    ]
