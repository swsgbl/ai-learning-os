"""M10-14 运行观测快照：admin-only 只读聚合端点。

覆盖矩阵：
1. route 注册（openapi）与 response schema（字段恰好为白名单、类型稳定）；
2. auth on：未登录 401 / learner 403 / admin 200；auth off 本地单用户放行；
3. 聚合计数正确：受控 seed 后 users/papers/resources/parse jobs/四类 pending
   draft/voice sessions/search queries/audit entries 与独立直查逐一相等；
4. 只读性：调用前后 SQLite 文件字节（sha256）与全部业务表行集合完全不变
   （选择说明：字节级锁定物理零写入，行集合锁定语义零写入，双断言互为佐证）；
5. MemoryRepository / 无 sessionmaker -> 503 固定 detail；
6. 数据库故障 -> 503 固定脱敏 detail，不含 URL/密码/异常链文本；
7. 敏感 marker（title/query/audit 正文/用户名）注入后不被回显；
8. PRIVACY.md 如实披露 admin-only 聚合观测（披露存在性守卫）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app

SECRET = "m10-14-ops-snapshot-secret-0123456789abcdef"
PRIVACY_DOC = Path(__file__).resolve().parents[3] / "docs" / "PRIVACY.md"

SNAPSHOT_FIELDS = {
    "generated_at",
    "database_backend",
    "users_by_role",
    "papers_total",
    "resources_by_parse_status",
    "parse_jobs_by_status",
    "pending_review_drafts",
    "voice_sessions_by_status",
    "search_queries_total",
    "audit_entries_total",
    "worker_running",
}
PENDING_DRAFT_FIELDS = {"course_import", "course_generation", "paper_question", "variant_question"}

# 注入用敏感 marker（响应全文不得出现任何一个）
MARKERS = (
    "MARKER-TITLE-m10x14",
    "MARKER-QUERY-m10x14",
    "MARKER-AUDIT-m10x14",
    "MARKER-NOTE-m10x14",
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
    """测试内模拟运维提升（等效 CLI 的 repo.set_role 路径，同 test_admin_audit）。"""
    from app.db.session import create_engine, make_sessionmaker
    from app.repositories.users import UserRepository

    async def _do_real() -> None:
        # engine 必须在同一个 event loop 内 dispose（M10-10，同治理测试口径）。
        engine = create_engine(db_url)
        try:
            repo = UserRepository(make_sessionmaker(engine))
            updated = await repo.set_role(username, "admin")
            assert updated is not None
        finally:
            await engine.dispose()

    asyncio.run(_do_real())


@pytest.fixture()
def client_and_db(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'ops_snapshot.db'}"
    with TestClient(create_app(db_url)) as c:
        yield c, db_url, tmp_path / "ops_snapshot.db"


def _seed_controlled_rows(db_url: str, *, with_markers: bool = False) -> None:
    """受控 seed：resources/parse jobs/四类 draft/voice sessions/search queries。

    绕过业务 API 直插 ORM 行——本文件只测聚合计数与只读性，不测写入路径；
    audit 走真实 AuditRepository.record（哈希链一致）。
    """
    from app.db.orm import (
        CourseGenerationDraftRow,
        CourseImportDraftRow,
        PaperQuestionDraftRow,
        ParseJobRow,
        ResourceRow,
        SearchQueryRow,
        VariantQuestionDraftRow,
        VoiceSessionRow,
    )
    from app.db.session import create_engine, make_sessionmaker
    from app.repositories.audit import AuditRepository

    now = datetime.now(UTC)
    marker = {f"m{i}": m for i, m in enumerate(MARKERS)}

    async def _do_seed() -> None:
        engine = create_engine(db_url)
        try:
            sessionmaker = make_sessionmaker(engine)
            async with sessionmaker() as session, session.begin():
                # resources：pending / parsed / failed 各一
                for i, status in enumerate(("pending", "parsed", "failed")):
                    session.add(
                        ResourceRow(
                            id=f"res_m10x14_{i}",
                            media_type="application/pdf",
                            title=marker["m0"] if with_markers else f"Ops Resource {i}",
                            content_hash=f"hash-m10x14-{i}",
                            storage_key=f"storage/m10x14/{i}",
                            parse_status=status,
                            fetched_at=now,
                        )
                    )
                # parse jobs：pending / running / succeeded（引用已插 resource）
                for i, status in enumerate(("pending", "running", "succeeded")):
                    session.add(
                        ParseJobRow(
                            id=f"job_m10x14_{i}",
                            resource_id=f"res_m10x14_{i}",
                            status=status,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                # 四类 draft：各 2 条 pending_review + 1 条 approved（approved 不计入）
                session.add(
                    CourseImportDraftRow(
                        id="cid_m10x14_p1",
                        title=marker["m3"] if with_markers else "CID pending 1",
                        status="pending_review",
                        source_resource_id="res_m10x14_0",
                        source_license_state="OPEN_LICENSE",
                        reuse_admission="ALLOWED",
                        extraction_note="seed",
                        created_at=now,
                    )
                )
                session.add(
                    CourseImportDraftRow(
                        id="cid_m10x14_p2",
                        title="CID pending 2",
                        status="pending_review",
                        source_resource_id="res_m10x14_1",
                        source_license_state="OPEN_LICENSE",
                        reuse_admission="ALLOWED",
                        extraction_note="seed",
                        created_at=now,
                    )
                )
                session.add(
                    CourseImportDraftRow(
                        id="cid_m10x14_a1",
                        title="CID approved",
                        status="approved",
                        source_resource_id="res_m10x14_2",
                        source_license_state="OPEN_LICENSE",
                        reuse_admission="ALLOWED",
                        extraction_note="seed",
                        created_at=now,
                    )
                )
                session.add(
                    CourseGenerationDraftRow(
                        id="cgd_m10x14_p1",
                        goal="goal",
                        status="pending_review",
                        dag_version=1,
                        chapter_count=1,
                        generation_note="seed",
                        created_at=now,
                    )
                )
                session.add(
                    CourseGenerationDraftRow(
                        id="cgd_m10x14_p2",
                        goal="goal",
                        status="pending_review",
                        dag_version=1,
                        chapter_count=1,
                        generation_note="seed",
                        created_at=now,
                    )
                )
                session.add(
                    CourseGenerationDraftRow(
                        id="cgd_m10x14_a1",
                        goal="goal",
                        status="approved",
                        dag_version=1,
                        chapter_count=1,
                        generation_note="seed",
                        created_at=now,
                    )
                )
                session.add(
                    PaperQuestionDraftRow(
                        id="pqd_m10x14_p1",
                        resource_id="res_m10x14_0",
                        status="pending_review",
                        questions=[],
                        question_count=0,
                        extraction_note="seed",
                        created_at=now,
                    )
                )
                session.add(
                    PaperQuestionDraftRow(
                        id="pqd_m10x14_p2",
                        resource_id="res_m10x14_1",
                        status="pending_review",
                        questions=[],
                        question_count=0,
                        extraction_note="seed",
                        created_at=now,
                    )
                )
                session.add(
                    PaperQuestionDraftRow(
                        id="pqd_m10x14_a1",
                        resource_id="res_m10x14_2",
                        status="approved",
                        questions=[],
                        question_count=0,
                        extraction_note="seed",
                        created_at=now,
                    )
                )
                session.add(
                    VariantQuestionDraftRow(
                        id="vqd_m10x14_p1",
                        status="pending_review",
                        variants={},
                        variant_count=0,
                        generation_note="seed",
                        created_at=now,
                    )
                )
                session.add(
                    VariantQuestionDraftRow(
                        id="vqd_m10x14_p2",
                        status="pending_review",
                        variants={},
                        variant_count=0,
                        generation_note="seed",
                        created_at=now,
                    )
                )
                session.add(
                    VariantQuestionDraftRow(
                        id="vqd_m10x14_a1",
                        status="approved",
                        variants={},
                        variant_count=0,
                        generation_note="seed",
                        created_at=now,
                    )
                )
                # voice sessions：两个不同 FSM 状态
                session.add(
                    VoiceSessionRow(
                        id="vs_m10x14_1",
                        exam_id="exam_m10x14_1",
                        status="SESSION_READY",
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.add(
                    VoiceSessionRow(
                        id="vs_m10x14_2",
                        exam_id="exam_m10x14_2",
                        status="WAITING_ANSWER",
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.add(
                    SearchQueryRow(
                        query=marker["m1"] if with_markers else "ops snapshot query",
                        result_count=0,
                        created_at=now,
                    )
                )
            # audit：真实哈希链路径写 3 条（action 带 marker 时验证不回显）
            audit = AuditRepository(sessionmaker)
            for i in range(3):
                await audit.record(
                    action=marker["m2"] if with_markers else "ops.snapshot.seed",
                    target_type="test",
                    target_id=f"t{i}",
                    request_id=f"req-m10x14-{i}",
                )
        finally:
            await engine.dispose()

    asyncio.run(_do_seed())


def _independent_counts(db_url: str) -> dict:
    """独立连接直查各维度计数（快照值必须与之逐一相等——自校准，不受 seed 卷数影响）。"""
    from sqlalchemy import func, select

    from app.db.orm import (
        AuditLogRow,
        CourseGenerationDraftRow,
        CourseImportDraftRow,
        PaperQuestionDraftRow,
        PaperRow,
        ParseJobRow,
        ResourceRow,
        SearchQueryRow,
        UserRow,
        VariantQuestionDraftRow,
        VoiceSessionRow,
    )
    from app.db.session import create_engine, make_sessionmaker

    async def _do_count() -> dict:
        engine = create_engine(db_url)
        try:
            async with make_sessionmaker(engine)() as session:
                users = dict(
                    (await session.execute(select(UserRow.role, func.count()).group_by(UserRow.role))).all()
                )
                resources = dict(
                    (
                        await session.execute(
                            select(ResourceRow.parse_status, func.count()).group_by(ResourceRow.parse_status)
                        )
                    ).all()
                )
                jobs = dict(
                    (
                        await session.execute(
                            select(ParseJobRow.status, func.count()).group_by(ParseJobRow.status)
                        )
                    ).all()
                )
                voice = dict(
                    (
                        await session.execute(
                            select(VoiceSessionRow.status, func.count()).group_by(VoiceSessionRow.status)
                        )
                    ).all()
                )

                async def pending(entity) -> int:
                    return (
                        await session.execute(
                            select(func.count()).select_from(entity).where(entity.status == "pending_review")
                        )
                    ).scalar_one()

                return {
                    "users_by_role": users,
                    "papers_total": (
                        await session.execute(select(func.count()).select_from(PaperRow))
                    ).scalar_one(),
                    "resources_by_parse_status": resources,
                    "parse_jobs_by_status": jobs,
                    "course_import": await pending(CourseImportDraftRow),
                    "course_generation": await pending(CourseGenerationDraftRow),
                    "paper_question": await pending(PaperQuestionDraftRow),
                    "variant_question": await pending(VariantQuestionDraftRow),
                    "voice_sessions_by_status": voice,
                    "search_queries_total": (
                        await session.execute(select(func.count()).select_from(SearchQueryRow))
                    ).scalar_one(),
                    "audit_entries_total": (
                        await session.execute(select(func.count()).select_from(AuditLogRow))
                    ).scalar_one(),
                }
        finally:
            await engine.dispose()

    return asyncio.run(_do_count())


def _db_row_fingerprint(db_url: str) -> dict:
    """全部用户表的行集合指纹：{表名: 行元组 frozenset}（只读性断言用）。

    行序不影响判定（集合语义）；行唯一性由各表主键保证，无需按列名排序
    （不同表主键列名不一，如 audit_chain_entries 是 audit_id）。
    """
    from sqlalchemy import text

    from app.db.session import create_engine

    async def _do_fingerprint() -> dict:
        engine = create_engine(db_url)
        try:
            async with engine.connect() as conn:
                tables = (
                    (
                        await conn.execute(
                            text(
                                "SELECT name FROM sqlite_master "
                                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                fingerprint = {}
                for table in tables:
                    rows = (await conn.execute(text(f"SELECT * FROM {table}"))).fetchall()
                    fingerprint[table] = frozenset(tuple(row) for row in rows)
                return fingerprint
        finally:
            await engine.dispose()

    return asyncio.run(_do_fingerprint())


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- route 注册与 response schema ---


def test_route_registered_in_openapi(client_and_db) -> None:
    client, *_ = client_and_db
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/system/ops-snapshot" in paths
    assert paths["/api/v1/system/ops-snapshot"]["get"]["responses"]["200"]


def test_response_schema_whitelist_and_types(client_and_db) -> None:
    client, *_ = client_and_db
    body = client.get("/api/v1/system/ops-snapshot").json()
    assert set(body) == SNAPSHOT_FIELDS, f"字段集合漂移: {set(body) ^ SNAPSHOT_FIELDS}"
    assert set(body["pending_review_drafts"]) == PENDING_DRAFT_FIELDS
    # 类型稳定：计数恒 int、分布恒 {str: int}、backend 恒类别枚举
    assert body["database_backend"] == "sqlite"
    for field in (
        "users_by_role",
        "resources_by_parse_status",
        "parse_jobs_by_status",
        "voice_sessions_by_status",
    ):
        assert isinstance(body[field], dict)
        assert all(isinstance(k, str) and isinstance(v, int) for k, v in body[field].items())
    for field in ("papers_total", "search_queries_total", "audit_entries_total"):
        assert isinstance(body[field], int)
    assert isinstance(body["worker_running"], bool)
    # generated_at：服务器 UTC，ISO-8601 可解析且带 +00:00
    parsed = datetime.fromisoformat(body["generated_at"])
    assert parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0


# --- auth 门禁（与治理端点同口径）---


def test_auth_on_learner_403_admin_200_anonymous_401(client_and_db, auth_on) -> None:
    client, db_url, *_ = client_and_db
    assert client.get("/api/v1/system/ops-snapshot").status_code == 401

    learner = User(client, "ops_learner")
    r = client.get("/api/v1/system/ops-snapshot", headers=learner.headers)
    assert r.status_code == 403, r.text

    admin = User(client, "ops_admin")
    _promote_to_admin(db_url, "ops_admin")
    r = client.get("/api/v1/system/ops-snapshot", headers=admin.headers)
    assert r.status_code == 200, r.text
    assert r.json()["users_by_role"] == {"learner": 1, "admin": 1}


def test_auth_off_local_single_user_allowed(client_and_db) -> None:
    """auth off：与既有治理端点口径一致（本地单用户放行）。"""
    client, *_ = client_and_db
    assert client.get("/api/v1/system/ops-snapshot").status_code == 200


# --- 聚合计数正确（快照值 == 独立直查值）---


def test_counts_match_independent_queries(client_and_db, auth_on) -> None:
    client, db_url, *_ = client_and_db
    # 3 用户（2 learner + 1 admin），导入 1 张治理卷（seed 卷数不硬编码）
    User(client, "ops_learner_a")
    User(client, "ops_learner_b")
    admin = User(client, "ops_admin")
    _promote_to_admin(db_url, "ops_admin")
    paper = {
        "title": "Ops Snapshot Paper",
        "duration_seconds": 600,
        "questions": [
            {
                "question": {
                    "question_type": "mcq",
                    "stem": "ops",
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
    assert client.post("/api/v1/papers/import", json=[paper], headers=admin.headers).status_code == 201
    _seed_controlled_rows(db_url)

    body = client.get("/api/v1/system/ops-snapshot", headers=admin.headers).json()
    expected = _independent_counts(db_url)
    assert body["users_by_role"] == expected["users_by_role"] == {"learner": 2, "admin": 1}
    assert body["papers_total"] == expected["papers_total"] >= 1  # seed 卷 + 导入卷
    assert body["resources_by_parse_status"] == expected["resources_by_parse_status"]
    assert expected["resources_by_parse_status"].get("parsed") == 1
    assert body["parse_jobs_by_status"] == expected["parse_jobs_by_status"]
    assert expected["parse_jobs_by_status"] == {"pending": 1, "running": 1, "succeeded": 1}
    drafts = body["pending_review_drafts"]
    assert drafts == {
        "course_import": expected["course_import"],
        "course_generation": expected["course_generation"],
        "paper_question": expected["paper_question"],
        "variant_question": expected["variant_question"],
    }
    assert drafts == {"course_import": 2, "course_generation": 2, "paper_question": 2, "variant_question": 2}
    assert body["voice_sessions_by_status"] == expected["voice_sessions_by_status"]
    assert expected["voice_sessions_by_status"] == {"SESSION_READY": 1, "WAITING_ANSWER": 1}
    assert body["search_queries_total"] == expected["search_queries_total"] == 1
    assert body["audit_entries_total"] == expected["audit_entries_total"] == 3


def test_empty_tables_yield_stable_zero_structure(client_and_db) -> None:
    """空表：分布为空 dict / 计数 0——结构稳定，不伪造非零。"""
    client, *_ = client_and_db
    body = client.get("/api/v1/system/ops-snapshot").json()
    assert body["users_by_role"] == {}
    assert body["resources_by_parse_status"] == {}
    assert body["parse_jobs_by_status"] == {}
    assert body["voice_sessions_by_status"] == {}
    assert body["pending_review_drafts"] == {
        "course_import": 0, "course_generation": 0, "paper_question": 0, "variant_question": 0
    }
    assert body["search_queries_total"] == 0
    assert body["audit_entries_total"] == 0


def test_worker_running_false_on_sqlite_test_double(client_and_db) -> None:
    """SQLite 测试替身不启动消费循环（main.py 既有口径）——快照如实反映 False。"""
    client, *_ = client_and_db
    body = client.get("/api/v1/system/ops-snapshot").json()
    assert body["worker_running"] is False


# --- 只读性 ---


def test_snapshot_is_readonly_rows_and_bytes_unchanged(client_and_db, auth_on) -> None:
    """调用前后：SQLite 文件字节（sha256）与全部用户表行集合完全不变。

    判定方式说明：行集合指纹是语义层只读判据（任何 INSERT/UPDATE/DELETE 必然改变
    某表行元组）；文件 sha256 是物理层补充判据（排除未被行集合覆盖的页级写入）。
    双断言同时成立 => 端点零写入。
    """
    client, db_url, db_path = client_and_db
    admin = User(client, "ro_admin")
    _promote_to_admin(db_url, "ro_admin")
    _seed_controlled_rows(db_url)

    assert client.get("/api/v1/system/ops-snapshot", headers=admin.headers).status_code == 200
    before_rows = _db_row_fingerprint(db_url)
    before_bytes = _file_sha256(db_path)

    for _ in range(3):  # 多次调用排除偶发
        assert client.get("/api/v1/system/ops-snapshot", headers=admin.headers).status_code == 200

    after_rows = _db_row_fingerprint(db_url)
    after_bytes = _file_sha256(db_path)
    assert after_rows == before_rows, "快照调用改变了表行集合——违反只读边界"
    assert after_bytes == before_bytes, "快照调用改变了 SQLite 文件字节——违反只读边界"


# --- 不可用与故障语义 ---


def test_memory_mode_returns_503(monkeypatch) -> None:
    """MemoryRepository（无 sessionmaker）——503 固定脱敏 detail。"""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AIOS_PG_TEST_URL", raising=False)
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as c:
            r = c.get("/api/v1/system/ops-snapshot")
            assert r.status_code == 503
            assert r.json() == {"detail": "Ops snapshot requires a database"}
    finally:
        get_settings.cache_clear()


def test_db_failure_returns_sanitized_503(client_and_db) -> None:
    """数据库故障——503 固定脱敏 detail：不含 URL/路径/密码/异常链文本。"""
    client, *_ = client_and_db
    from app.db.session import create_engine, make_sessionmaker

    bad_dir = "C:/nonexistent_dir_m10x14"
    client.app.state.sessionmaker = make_sessionmaker(
        create_engine(f"sqlite+aiosqlite:///{bad_dir}/ops.db")
    )
    r = client.get("/api/v1/system/ops-snapshot")
    assert r.status_code == 503
    assert r.json() == {"detail": "Ops snapshot temporarily unavailable"}
    text = json.dumps(r.json())
    for leaked in (bad_dir, "sqlite", "aiosqlite", "OperationalError", "unable to open", "Traceback"):
        assert leaked not in text, f"503 响应泄漏异常细节: {leaked}"


# --- 敏感信息不回显 ---


def test_sensitive_markers_not_echoed(client_and_db, auth_on) -> None:
    """注入 marker（title/query/audit action/note）后，响应全文零回显。"""
    client, db_url, *_ = client_and_db
    learner = User(client, "marker_opsuser")  # username 也作为 marker
    _seed_controlled_rows(db_url, with_markers=True)

    r = client.get("/api/v1/system/ops-snapshot", headers=learner.headers)
    assert r.status_code == 403  # learner 先被门禁拦下——正文根本不产出
    admin = User(client, "ops_admin2")
    _promote_to_admin(db_url, "ops_admin2")
    r = client.get("/api/v1/system/ops-snapshot", headers=admin.headers)
    assert r.status_code == 200
    text = json.dumps(r.json(), ensure_ascii=False)
    for marker in (*MARKERS, "marker_opsuser", "ops_admin2"):
        assert marker not in text, f"快照回显敏感 marker: {marker}"


# --- 隐私披露同步 ---


def test_privacy_doc_discloses_ops_snapshot() -> None:
    """PRIVACY.md 必须披露 admin-only 聚合观测端点（无出站请求、无学习内容）。"""
    doc = PRIVACY_DOC.read_text(encoding="utf-8")
    assert "ops-snapshot" in doc, "PRIVACY.md 未披露运行观测快照端点"
