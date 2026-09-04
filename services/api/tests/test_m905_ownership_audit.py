"""M9-05 私有语料与草稿归属修复 + 审计事务完整性（Alice/Bob 回归）。

1. 语料边界：Bob search 不泄漏 Alice 私有 chunk；public 语料双方可见；
   course generation 不引用他人私有 chunk；admin 普通搜索同样不读他人私有。
2. 四类草稿：Bob list 看不到 Alice；get 404；Bob 用 Alice 资源创建 404。
3. 审核审计：不存在 404 无成功审计；终态重复 409 无第二次成功审计；
   审计写入失败 → 业务 mutation 回滚（同事务 fail-closed）。
4. auth off 语义保持兼容（全部放行、现状行为）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app

SECRET = "m9-05-ownership-audit-secret-0123456789abcdef"
COMPOSE_FILE = Path(__file__).resolve().parents[3] / "infra" / "docker-compose.yml"


@pytest.fixture()
def auth_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_SECRET", SECRET)
    get_settings.cache_clear()
    yield
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    get_settings.cache_clear()


@pytest.fixture()
def stack(tmp_path):
    db_url = "sqlite+aiosqlite:///" + (tmp_path / "m905.db").as_posix()
    with TestClient(create_app(db_url)) as c:
        yield c, db_url


class User:
    def __init__(self, client: TestClient, name: str) -> None:
        body = {"username": name, "password": "password-123"}
        client.post("/api/v1/auth/register", json=body)
        token = client.post("/api/v1/auth/login", json=body).json()["access_token"]
        self.name = name
        self.headers = {"Authorization": f"Bearer {token}"}


def _promote(db_url: str, username: str) -> None:
    import asyncio

    from app.db.session import create_engine, make_sessionmaker
    from app.repositories.users import UserRepository

    async def _do():
        # engine 必须在同一个 event loop 内 dispose（M10-10，同 legacy 治理测试）。
        engine = create_engine(db_url)
        try:
            repo = UserRepository(make_sessionmaker(engine))
            assert await repo.set_role(username, "admin") is not None
        finally:
            await engine.dispose()

    asyncio.run(_do())


def _upload(client: TestClient, headers: dict, marker: str, source_id: str | None = None):
    payload = f'[{{"question": "{marker}", "answer": "a/sinA=b/sinB"}}]'.encode()
    form = {"source_id": (None if source_id is None else source_id)}
    up = client.post(
        "/api/v1/resources/upload",
        files={"file": (f"{marker[:8]}.json", payload, "application/json")},
        data=form,
        headers=headers,
    )
    assert up.status_code == 201, up.text
    rid = up.json()["id"]
    parsed = client.post(f"/api/v1/resources/{rid}/parse", headers=headers)
    assert parsed.status_code == 200, parsed.text
    return rid


def _public_source(client: TestClient, headers: dict, source_id: str) -> None:
    r = client.post(
        "/api/v1/sources",
        json={"id": source_id, "name": source_id, "source_type": "oer",
              "license_state": "OPEN_LICENSE", "trust_tier": "B", "authority_score": 5,
              "homepage": f"https://example.edu/{source_id}"},
        headers=headers,
    )
    assert r.status_code == 201, r.text


# --- 1. 私有语料边界 ---


def test_private_corpus_never_leaks_to_other_users(stack, auth_on) -> None:
    client, db_url = stack
    admin = User(client, "gov_admin")
    _promote(db_url, "gov_admin")
    alice = User(client, "alice_corpus")
    bob = User(client, "bob_corpus")

    _public_source(client, admin.headers, "src_pub_m905")
    _upload(client, alice.headers, "alicePrivateMarkerSinA", source_id=None)  # 私有
    _upload(client, alice.headers, "publicMarkerCosB", source_id="src_pub_m905")  # 公共

    alice_hits = client.post(
        "/api/v1/search/queries", json={"query": "sinA", "limit": 10}, headers=alice.headers
    ).json()
    assert any("alicePrivateMarkerSinA" in r["snippet"] for r in alice_hits["results"])

    bob_hits = client.post(
        "/api/v1/search/queries", json={"query": "sinA", "limit": 10}, headers=bob.headers
    ).json()
    assert all("alicePrivateMarkerSinA" not in r["snippet"] for r in bob_hits["results"]), (
        "他人私有 snippet 绝不能进入结果"
    )

    public_hits_bob = client.post(
        "/api/v1/search/queries", json={"query": "publicMarkerCosB", "limit": 10},
        headers=bob.headers,
    ).json()
    assert any("publicMarkerCosB" in r["snippet"] for r in public_hits_bob["results"]), (
        "public 语料对登录用户可见"
    )

    # admin 的普通搜索同样不读他人私有（治理读取是显式通道而非搜索）
    admin_hits = client.post(
        "/api/v1/search/queries", json={"query": "sinA", "limit": 10}, headers=admin.headers
    ).json()
    assert all("alicePrivateMarkerSinA" not in r["snippet"] for r in admin_hits["results"])


def test_course_generation_excludes_foreign_private_chunks(stack, auth_on, monkeypatch) -> None:
    """课程生成的 Resource 阶段不引用他人私有 chunk（语料边界贯穿生成管线）。"""
    client, db_url = stack
    admin = User(client, "gov_admin2")
    _promote(db_url, "gov_admin2")
    alice = User(client, "alice_gen")

    _public_source(client, admin.headers, "src_pub_gen")
    # M9-07: 预先上传 Alice 私有资源并保存 resource_id —— 生成后做决定性断言
    alice_private_rid = _upload(client, alice.headers, "genPrivateMarkerXyz", source_id=None)

    bob = User(client, "bob_gen")
    # Bob 触发生成：需要 admin 发布 DAG（治理动作）；生成过程检索语料
    dag = client.post(
        "/api/v1/concept-dag/versions",
        json={"nodes": [{"id": "c1", "canonical_name": "genPrivateMarkerXyz", "subject": "math",
                          "difficulty": 1, "aliases": [], "evidence_ids": []}],
              "edges": [], "note": "v1"},
        headers=admin.headers,
    )
    assert dag.status_code == 201, dag.text

    gen = client.post(
        "/api/v1/courses/generation-drafts",
        json={"goal": "genPrivateMarkerXyz"},
        headers=bob.headers,
    )
    assert gen.status_code == 201, gen.text
    body = gen.json()
    # 决定性断言：测试开始前上传的 Alice 私有 resource_id 不得出现在
    # 响应序列化文本与 plan.resources 中（检索阶段已被 owner 过滤）
    assert alice_private_rid not in str(body), "Bob 生成响应不得引用 Alice 私有资源"
    resources_in_plan = body["plan"].get("resources", [])
    assert all(
        ref.get("resource_id") != alice_private_rid for ref in resources_in_plan
    ), "plan.resources 不得引用 Alice 私有资源"

    # generation draft ownership：Alice 创建后 Bob list 不可见、get 404、admin 可读
    alice_gen = client.post(
        "/api/v1/courses/generation-drafts",
        json={"goal": "genPrivateMarkerXyz"},  # 匹配 DAG 概念名
        headers=alice.headers,
    )
    assert alice_gen.status_code == 201, alice_gen.text
    alice_gen_id = alice_gen.json()["id"]
    bob_gen_list = client.get(
        "/api/v1/courses/generation-drafts", headers=bob.headers
    ).json()
    assert all(item["id"] != alice_gen_id for item in bob_gen_list), "Bob list 不得看到 Alice 草稿"
    assert client.get(
        f"/api/v1/courses/generation-drafts/{alice_gen_id}", headers=bob.headers
    ).status_code == 404
    assert client.get(
        f"/api/v1/courses/generation-drafts/{alice_gen_id}", headers=admin.headers
    ).status_code == 200, "admin 治理读取可见"


# --- 2. 四类草稿归属 ---


def test_course_import_drafts_owner_scoped(stack, auth_on) -> None:
    client, db_url = stack
    admin = User(client, "gov_admin3")
    _promote(db_url, "gov_admin3")
    alice = User(client, "alice_draft")
    bob = User(client, "bob_draft")

    _public_source(client, admin.headers, "src_pub_draft")
    alice_rid = _upload(client, alice.headers, "importMarkerAlice", source_id="src_pub_draft")

    # Bob 用 Alice 的资源创建导入草稿 → 404（归属感知读取）
    r = client.post(
        "/api/v1/courses/import-drafts",
        json={"resource_id": alice_rid},
        headers=bob.headers,
    )
    assert r.status_code == 404, r.text

    # Alice 自己创建成功；Bob list 看不到
    ok = client.post("/api/v1/courses/import-drafts", json={"resource_id": alice_rid},
                     headers=alice.headers)
    assert ok.status_code == 201, ok.text
    draft_id = ok.json()["id"]

    bob_list = client.get("/api/v1/courses/import-drafts", headers=bob.headers).json()
    assert all(item["id"] != draft_id for item in bob_list), "普通用户 list 不得看到他人草稿"
    alice_list = client.get("/api/v1/courses/import-drafts", headers=alice.headers).json()
    assert any(item["id"] == draft_id for item in alice_list)

    # get 404；admin 治理读取可见
    assert client.get(f"/api/v1/courses/import-drafts/{draft_id}", headers=bob.headers).status_code == 404
    assert client.get(f"/api/v1/courses/import-drafts/{draft_id}", headers=admin.headers).status_code == 200

    # admin 审核成功 → 真实审计恰好一条；重复审核 409 → 无第二次成功审计
    approve = client.post(f"/api/v1/courses/import-drafts/{draft_id}/approve",
                          json={"note": "ok"}, headers=admin.headers)
    assert approve.status_code == 200, approve.text
    dup = client.post(f"/api/v1/courses/import-drafts/{draft_id}/approve",
                      json={"note": "again"}, headers=admin.headers)
    assert dup.status_code == 409
    entries = [
        e for e in client.get("/api/v1/audit", headers=admin.headers).json()
        if e["target_id"] == draft_id and e["action"] == "course_import.approve"
    ]
    assert len(entries) == 1, "终态重复审核不得出现第二次成功审计"
    assert entries[0]["after"] == {"status": "approved"}


def test_missing_draft_review_writes_no_success_audit(stack, auth_on) -> None:
    client, db_url = stack
    admin = User(client, "gov_admin4")
    _promote(db_url, "gov_admin4")
    r = client.post("/api/v1/courses/import-drafts/pqd_missing/approve",
                    json={"note": None}, headers=admin.headers)
    assert r.status_code == 404
    entries = [
        e for e in client.get("/api/v1/audit", headers=admin.headers).json()
        if e["target_id"] == "pqd_missing"
    ]
    assert entries == [], "MISSING 不得写成功审计（after 不得伪造 approved）"


def test_paper_extractor_owner_scoped(stack, auth_on) -> None:
    client, db_url = stack
    admin = User(client, "gov_admin5")
    _promote(db_url, "gov_admin5")
    alice = User(client, "alice_extract")
    bob = User(client, "bob_extract")

    _public_source(client, admin.headers, "src_pub_ext")
    alice_rid = _upload(client, alice.headers, "extractMarkerAlice", source_id="src_pub_draft"
                        if False else "src_pub_ext")

    # Bob 用 Alice 资源抽取 → 404
    r = client.post("/api/v1/papers/import-drafts", json={"resource_id": alice_rid},
                    headers=bob.headers)
    assert r.status_code == 404, r.text
    # Alice 抽取成功；Bob list 不可见
    ok = client.post("/api/v1/papers/import-drafts", json={"resource_id": alice_rid},
                     headers=alice.headers)
    assert ok.status_code == 201, ok.text
    draft_id = ok.json()["id"]
    bob_list = client.get("/api/v1/papers/import-drafts", headers=bob.headers).json()
    assert all(item["id"] != draft_id for item in bob_list)
    assert client.get(f"/api/v1/papers/import-drafts/{draft_id}", headers=bob.headers).status_code == 404


# --- 3. 审计事务完整性：audit 写入失败 → 业务回滚 ---


def test_audit_failure_rolls_back_business_mutation(stack, auth_on, monkeypatch) -> None:
    client, db_url = stack
    admin = User(client, "gov_admin6")
    _promote(db_url, "gov_admin6")
    alice = User(client, "alice_rollback")

    _public_source(client, admin.headers, "src_pub_rb")
    alice_rid = _upload(client, alice.headers, "rollbackMarker", source_id="src_pub_rb")
    draft_id = client.post("/api/v1/courses/import-drafts",
                           json={"resource_id": alice_rid},
                           headers=alice.headers).json()["id"]

    from app.repositories import course_import_drafts as draft_repo_mod

    async def exploding_append(session, payload, *, clock):  # 审计写入失败
        raise RuntimeError("audit storage broken")

    monkeypatch.setattr(draft_repo_mod, "append_audit", exploding_append)
    failed = False
    try:
        client.post(f"/api/v1/courses/import-drafts/{draft_id}/approve",
                    json={"note": "n"}, headers=admin.headers)
    except RuntimeError:
        failed = True  # TestClient 默认重抛服务端异常 = 审计失败已向外暴露
    assert failed, "审计写入失败必须 fail-closed（拒绝成功响应），而非静默成功"

    # 业务未变（回滚）：草稿仍 pending_review，可再次正常审核
    after = client.get(f"/api/v1/courses/import-drafts/{draft_id}", headers=admin.headers)
    assert after.json()["status"] == "pending_review"
    monkeypatch.undo()
    retry = client.post(f"/api/v1/courses/import-drafts/{draft_id}/approve",
                        json={"note": "n"}, headers=admin.headers)
    assert retry.status_code == 200


# --- 4. auth off 兼容 ---


def test_auth_off_semantics_unchanged(monkeypatch) -> None:
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    get_settings.cache_clear()
    with TestClient(create_app("sqlite+aiosqlite:///:memory:")) as client:
        payload = b'[{"question": "authOffMarker", "answer": "x"}]'
        up = client.post(
            "/api/v1/resources/upload",
            files={"file": ("doc.json", payload, "application/json")},
        ).json()
        assert client.post(f"/api/v1/resources/{up['id']}/parse").status_code == 200
        hits = client.post("/api/v1/search/queries", json={"query": "authOffMarker"}).json()
        assert hits["result_count"] >= 1, "auth off 检索现状不变"
        # 治理端点放行（本地单用户）
        r = client.post(
            "/api/v1/sources",
            json={"id": "src_off", "name": "s", "source_type": "oer",
                  "license_state": "OPEN_LICENSE", "trust_tier": "B", "authority_score": 5,
                  "homepage": "https://example.edu/o"},
        )
        assert r.status_code == 201
        assert client.get("/api/v1/audit").status_code == 200


# --- 5. variant draft ownership（M9-06 补强） ---


def test_variant_draft_ownership(stack, auth_on) -> None:
    """Alice 创建 variant draft 后：Bob list 不可见、get 404、admin 可读。"""
    client, db_url = stack
    admin = User(client, "gov_admin_var")
    _promote(db_url, "gov_admin_var")
    alice = User(client, "alice_variant")
    bob = User(client, "bob_variant")

    q1 = {
        "question_no": 1, "stem": "小红有 5 个苹果，给了同学 2 个，还剩几个？",
        "question_type": "mcq", "score": 3.0, "page_start": 1, "page_end": 1,
        "resource_id": "res_x",
        "options": [
            {"label": "A", "text": "3"}, {"label": "B", "text": "5"},
            {"label": "C", "text": "7"}, {"label": "D", "text": "10"},
        ],
        "concept_ids": ["c_sub"],
    }
    created = client.post(
        "/api/v1/questions/variant-drafts",
        json={"questions": [q1]},
        headers=alice.headers,
    )
    assert created.status_code == 201, created.text
    draft_id = created.json()["id"]

    bob_list = client.get("/api/v1/questions/variant-drafts", headers=bob.headers).json()
    assert all(item["id"] != draft_id for item in bob_list), "Bob list 不得看到 Alice 草稿"
    assert client.get(
        f"/api/v1/questions/variant-drafts/{draft_id}", headers=bob.headers
    ).status_code == 404
    assert client.get(
        f"/api/v1/questions/variant-drafts/{draft_id}", headers=admin.headers
    ).status_code == 200, "admin 治理读取可见"
    alice_list = client.get(
        "/api/v1/questions/variant-drafts", headers=alice.headers
    ).json()
    assert any(item["id"] == draft_id for item in alice_list)


# --- 6. M9-07 部署拓扑与 LiveKit 凭据一致性（compose 渲染） ---


def test_compose_topology_data_services_stay_loopback(stack, auth_on) -> None:
    """AIOS_BIND_IP=0.0.0.0 渲染：api/web/livekit 可公开；postgres/redis/minio 仍 loopback。"""
    import os
    import subprocess

    def render(tag: str) -> dict:
        env = {**os.environ, "AIOS_BIND_IP": tag, "AIOS_WEB_PORT": "3100"}
        env.pop("DATABASE_URL", None)
        proc = subprocess.run(
            ["docker", "compose", "-f", COMPOSE_FILE, "--profile", "local", "config", "--format", "json"],
            capture_output=True, text=True, check=True, env=env,
        )
        import json
        return json.loads(proc.stdout)

    public = render("0.0.0.0")
    loopback = render("127.0.0.1")
    for svc in ("postgres", "redis", "minio"):
        for mode in (public, loopback):
            ports = mode["services"][svc].get("ports", [])
            for port in ports:
                assert port.get("host_ip") == "127.0.0.1", f"{svc} 数据面必须固定 loopback"
    for svc in ("api", "web", "livekit"):
        ports = public["services"][svc].get("ports", [])
        assert ports and all(p.get("host_ip") == "0.0.0.0" for p in ports), (
            f"{svc} 在 AIOS_BIND_IP=0.0.0.0 下应可公开绑定"
        )
    del loopback


def test_compose_livekit_credentials_shared_with_api(stack, auth_on) -> None:
    """生产 key/secret 渲染后：API env 与 livekit server --keys 出现同一组值；默认模式用占位值。"""
    import json
    import os
    import subprocess

    def render(env_extra: dict) -> dict:
        env = {**os.environ, "AIOS_WEB_PORT": "3100", **env_extra}
        env.pop("DATABASE_URL", None)
        proc = subprocess.run(
            ["docker", "compose", "-f", COMPOSE_FILE, "--profile", "local", "config", "--format", "json"],
            capture_output=True, text=True, check=True, env=env,
        )
        return json.loads(proc.stdout)

    prod_key, prod_secret = "prod-lk-key", "prod-lk-secret-0123456789abcdef"
    rendered = render({
        "AIOS_LIVEKIT_API_KEY": prod_key,
        "AIOS_LIVEKIT_API_SECRET": prod_secret,
    })
    api_env = rendered["services"]["api"]["environment"]
    assert api_env["LIVEKIT_API_KEY"] == prod_key
    assert api_env["LIVEKIT_API_SECRET"] == prod_secret
    livekit_cmd = " ".join(rendered["services"]["livekit"]["command"])
    assert prod_key in livekit_cmd and prod_secret in livekit_cmd, (
        "livekit server --keys 必须与 API 使用同一组凭据"
    )

    default = render({})
    api_env = default["services"]["api"]["environment"]
    assert api_env["LIVEKIT_API_KEY"] == "devkey"
    assert api_env["LIVEKIT_API_SECRET"] == "ailos-local-dev-secret-0f4c9a1e7b2d"


def test_compose_web_api_base_url_build_arg(stack, auth_on) -> None:
    """Web build arg：AIOS_PUBLIC_API_BASE_URL 渲染进 build.args（局域网访问入口）。"""
    import json
    import os
    import subprocess

    def render(env_extra: dict) -> dict:
        env = {**os.environ, "AIOS_WEB_PORT": "3100", **env_extra}
        env.pop("DATABASE_URL", None)
        proc = subprocess.run(
            ["docker", "compose", "-f", COMPOSE_FILE, "--profile", "local", "config", "--format", "json"],
            capture_output=True, text=True, check=True, env=env,
        )
        return json.loads(proc.stdout)

    rendered = render({"AIOS_PUBLIC_API_BASE_URL": "http://192.168.1.10:8000"})
    args = rendered["services"]["web"]["build"]["args"]
    assert args["NEXT_PUBLIC_API_BASE_URL"] == "http://192.168.1.10:8000"
    default = render({})
    assert default["services"]["web"]["build"]["args"]["NEXT_PUBLIC_API_BASE_URL"] == "http://127.0.0.1:8000"


# --- 7. M9-08 公开语音连通（渲染/URL/门禁/CORS 精确解析/凭据同源） ---


def test_voice_token_public_ws_url(stack, auth_on, monkeypatch) -> None:
    """公开模式（PUBLIC_LIVEKIT_URL 设置）：token 返回浏览器可达 ws_url。"""
    client, _db_url = stack
    user = User(client, "voice_pub_user")
    monkeypatch.setenv("LIVEKIT_API_KEY", "devkey")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "devsecret-0123456789abcdef012345")
    monkeypatch.setenv("PUBLIC_LIVEKIT_URL", "ws://192.168.1.50:7880")
    get_settings.cache_clear()
    try:
        r = client.post(
            "/api/v1/voice/token",
            json={"room": "room-pub", "identity": "u1", "role": "student"},
            headers=user.headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["ws_url"] == "ws://192.168.1.50:7880"
    finally:
        get_settings.cache_clear()


def test_voice_token_default_loopback_ws_url(stack, auth_on, monkeypatch) -> None:
    """默认本机模式：token 返回 ws://127.0.0.1:7880（不回归）。"""
    client, _db_url = stack
    user = User(client, "voice_local_user")
    monkeypatch.setenv("LIVEKIT_API_KEY", "devkey")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "devsecret-0123456789abcdef012345")
    get_settings.cache_clear()
    r = client.post(
        "/api/v1/voice/token",
        json={"room": "room-local", "identity": "u1", "role": "student"},
        headers=user.headers,
    )
    assert r.status_code == 200
    assert r.json()["ws_url"] == "ws://127.0.0.1:7880"


def test_cors_local_detection_uses_exact_host(stack, auth_on) -> None:
    """_cors_is_local_only 用 urlsplit 精确 host——evil-localhost 子串不误判。"""
    from app.core.security import _cors_is_local_only

    assert _cors_is_local_only("http://localhost:3000,http://127.0.0.1:3000")
    assert not _cors_is_local_only("https://learn.example.com")
    assert not _cors_is_local_only("https://evil-localhost.attacker.com"), (
        "子串误判必须消除：evil-localhost.attacker.com 不是本地源"
    )


def test_exposure_public_binding_requires_reachable_livekit_url(stack, auth_on) -> None:
    """公开绑定缺/误配公开 LiveKit URL：fail-closed（容器内部地址不允许）。"""
    from app.core.security import validate_exposure

    common = {
        "host_bind_ip": "0.0.0.0", "app_env": "production",
        "auth_secret": "prod-auth-secret-0123456789abcdef012345",
        "livekit_api_secret": "prod-lk-secret-0123456789abcdef01",
        "cors_origins": "https://learn.example.com",
    }
    with pytest.raises(RuntimeError, match="PUBLIC_LIVEKIT_URL"):
        validate_exposure(**common, public_livekit_url=None)
    with pytest.raises(RuntimeError, match="PUBLIC_LIVEKIT_URL"):
        validate_exposure(**common, public_livekit_url="ws://livekit:7880")
    with pytest.raises(RuntimeError, match="PUBLIC_LIVEKIT_URL"):
        validate_exposure(**common, public_livekit_url="ws://127.0.0.1:7880")
    # 合法局域网地址通过
    validate_exposure(**common, public_livekit_url="ws://192.168.1.50:7880")


def test_compose_public_mode_injects_public_livekit_url(stack, auth_on) -> None:
    """公开模式渲染：API env 注入 AIOS_PUBLIC_LIVEKIT_URL（显式配置值透传）。"""
    import json
    import os
    import subprocess

    def render(env_extra: dict) -> dict:
        env = {**os.environ, "AIOS_WEB_PORT": "3100", **env_extra}
        env.pop("DATABASE_URL", None)
        proc = subprocess.run(
            ["docker", "compose", "-f", COMPOSE_FILE, "--profile", "local", "config", "--format", "json"],
            capture_output=True, text=True, check=True, env=env,
        )
        return json.loads(proc.stdout)

    rendered = render({"AIOS_PUBLIC_LIVEKIT_URL": "ws://192.168.1.50:7880"})
    assert rendered["services"]["api"]["environment"]["PUBLIC_LIVEKIT_URL"] == "ws://192.168.1.50:7880"


def test_compose_livekit_config_and_credentials_stay_same_source(stack, auth_on) -> None:
    """livekit server command 与 API env 的 key/secret 同源；config 可切换 local/public。"""
    import json
    import os
    import subprocess

    def render(env_extra: dict) -> dict:
        env = {**os.environ, "AIOS_WEB_PORT": "3100", **env_extra}
        env.pop("DATABASE_URL", None)
        proc = subprocess.run(
            ["docker", "compose", "-f", COMPOSE_FILE, "--profile", "local", "config", "--format", "json"],
            capture_output=True, text=True, check=True, env=env,
        )
        return json.loads(proc.stdout)

    rendered = render({"AIOS_LIVEKIT_CONFIG": "/etc/livekit/livekit-public.yaml"})
    livekit_cmd = " ".join(rendered["services"]["livekit"]["command"])
    api_env = rendered["services"]["api"]["environment"]
    assert "/etc/livekit/livekit-public.yaml" in livekit_cmd, "config 可切换到 public"
    # 默认仍是 local config
    default_cmd = " ".join(render({})["services"]["livekit"]["command"])
    assert "/etc/livekit/livekit.yaml" in default_cmd
    # 凭据同源：--keys 与 API env 同一组值
    assert api_env["LIVEKIT_API_KEY"] in livekit_cmd
    assert api_env["LIVEKIT_API_SECRET"] in livekit_cmd
