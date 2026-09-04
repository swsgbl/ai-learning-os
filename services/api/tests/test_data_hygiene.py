"""M10-03 data inventory and conservative acceptance cleanup."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.db.orm import (
    AuditLogRow,
    ChunkRow,
    CourseImportDraftRow,
    EvidenceRow,
    ExamSessionRow,
    PaperRow,
    ParseJobRow,
    ResourceRow,
    SearchQueryRow,
    UserRow,
    VariantQuestionDraftRow,
    VoiceAnswerEventRow,
    VoiceSessionRow,
    VoiceTraceSpanRow,
    VoiceTranscriptRow,
)
from app.db.session import create_engine, make_sessionmaker
from app.main import create_app
from app.ops import cli as cli_module
from app.ops.data_hygiene import (
    _build_cleanup_plan,
    build_data_inventory,
    run_acceptance_clean,
)

SECRET = "m10-03-data-hygiene-secret-0123456789"


@pytest.fixture()
def auth_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_SECRET", SECRET)
    get_settings.cache_clear()
    yield
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    get_settings.cache_clear()


PAPER = {
    "title": "data hygiene paper",
    "duration_seconds": 300,
    "questions": [
        {
            "question": {
                "question_type": "mcq",
                "stem": "1+1=?",
                "options": ["1", "2"],
                "answer": {"option_index": 1},
                "explanation": "base arithmetic",
                "concept_ids": ["data_hygiene"],
                "difficulty": 1,
            },
            "score": 1.0,
        }
    ],
}


def _register_login(client: TestClient, username: str) -> tuple[dict[str, str], str]:
    body = {"username": username, "password": "password-123"}
    created = client.post("/api/v1/auth/register", json=body)
    assert created.status_code == 201, created.text
    token = client.post("/api/v1/auth/login", json=body).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}, created.json()["id"]


def _promote(db_path, username: str) -> None:
    # engine 必须在同一个 event loop 内 dispose（M10-10，同 legacy 治理测试）。
    async def run() -> None:
        from app.repositories.users import UserRepository

        engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
        try:
            repo = UserRepository(make_sessionmaker(engine))
            assert await repo.set_role(username, "admin") is not None
        finally:
            await engine.dispose()

    asyncio.run(run())


def _import_and_submit(client: TestClient, headers: dict, title: str) -> tuple[str, str]:
    paper = dict(PAPER)
    paper["title"] = title
    imported = client.post("/api/v1/papers/import", json=[paper], headers=headers)
    assert imported.status_code == 201, imported.text
    paper_id = imported.json()["imported"][0]
    started = client.post(
        f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"}, headers=headers
    )
    assert started.status_code == 201, started.text
    exam = started.json()
    answer = client.put(
        f"/api/v1/exams/{exam['exam_id']}/answers",
        json={
            "sequence": 1,
            "question_id": exam["questions"][0]["id"],
            "answer": "B",
        },
        headers=headers,
    )
    assert answer.status_code == 200, answer.text
    submitted = client.post(
        f"/api/v1/exams/{exam['exam_id']}/submit", json={}, headers=headers
    )
    assert submitted.status_code == 200, submitted.text
    return paper_id, exam["exam_id"]


def _seed_direct_rows(db_url: str, owner_id: str, exam_id: str) -> None:
    now = datetime.now(UTC)

    async def run() -> None:
        # engine 必须在同一个 event loop 内 dispose（M10-10，同 legacy 治理测试）。
        engine = create_engine(db_url)
        try:
            sessions = make_sessionmaker(engine)
            async with sessions() as session, session.begin():
                session.add(
                    ResourceRow(
                        id="res_smoke",
                        source_id=None,
                        url=None,
                        media_type="application/json",
                        title="smoke resource",
                        language="zh",
                        access_state="private",
                        license_state="OPEN_LICENSE",
                        owner_id=owner_id,
                        content_hash="a" * 64,
                        storage_key="uploads/aa/" + "a" * 64,
                        size_bytes=1,
                        content_type="application/json",
                        parse_status="parsed",
                        parser_name="json",
                        parse_metrics={},
                        parse_error=None,
                        fetched_at=now,
                    )
                )
                session.add(
                    ChunkRow(
                        id="chunk_smoke",
                        resource_id="res_smoke",
                        chunk_index=0,
                        text="smoke",
                        chunk_hash="b" * 64,
                        page_start=1,
                        page_end=1,
                        slide=None,
                        block_types=[],
                        embedding_status="pending",
                    )
                )
                session.add(
                    EvidenceRow(
                        id="evidence_smoke",
                        chunk_id="chunk_smoke",
                        resource_id="res_smoke",
                        source_id=None,
                        url=None,
                        parser_name="json",
                        locator={"page": 1},
                        snippet_hash="c" * 64,
                        license_state="OPEN_LICENSE",
                        retrieved_at=now,
                    )
                )
                session.add(
                    ParseJobRow(
                        id="job_smoke",
                        resource_id="res_smoke",
                        parser_name="json",
                        status="succeeded",
                        attempts=1,
                        max_attempts=3,
                        last_error=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.add(
                    CourseImportDraftRow(
                        id="cid_smoke",
                        owner_id=owner_id,
                        title="smoke course",
                        status="pending_review",
                        source_resource_id="res_smoke",
                        source_license_state="OPEN_LICENSE",
                        reuse_admission="ADMISSIBLE",
                        concepts=[],
                        resource_refs=["res_smoke"],
                        extraction_note="smoke",
                        review_note=None,
                        reviewed_at=None,
                        created_at=now,
                    )
                )
                session.add(
                    VariantQuestionDraftRow(
                        id="vqd_smoke",
                        owner_id=owner_id,
                        status="pending_review",
                        variants={},
                        variant_count=0,
                        generation_note="smoke",
                        review_note=None,
                        reviewed_at=None,
                        created_at=now,
                    )
                )
                session.add(
                    SearchQueryRow(
                        query="smoke query",
                        providers_requested=["local"],
                        providers_skipped=[],
                        result_count=0,
                        duration_ms=1,
                        results=[],
                        owner_id=owner_id,
                        created_at=now,
                    )
                )
                session.add(
                    VoiceTranscriptRow(
                        provider="local",
                        text="smoke transcript",
                        confidence=1.0,
                        latency_ms=1,
                        audio_bytes=1,
                        audio_object_key=None,
                        owner_id=owner_id,
                        audio_stored=False,
                        exam_id=exam_id,
                        question_id=None,
                        created_at=now,
                    )
                )
                session.add(
                    VoiceSessionRow(
                        id="voice_smoke",
                        exam_id=exam_id,
                        status="completed",
                        question_index=0,
                        revision=1,
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.add(
                    VoiceAnswerEventRow(
                        event_id="voice_event_smoke",
                        session_id="voice_smoke",
                        exam_id=exam_id,
                        question_id="q",
                        normalized_answer="B",
                        intent="choose",
                        transcript="choose B",
                        confidence=1.0,
                        accepted=True,
                        created_at=now,
                    )
                )
                session.add(
                    VoiceTraceSpanRow(
                        stage="asr",
                        duration_ms=1,
                        source="server",
                        session_id="voice_smoke",
                        exam_id=exam_id,
                        question_id=None,
                        created_at=now,
                    )
                )
        finally:
            await engine.dispose()
    asyncio.run(run())


def _rows(db_path, model, *conditions):
    # engine 必须在同一个 event loop 内 dispose（M10-10，同 legacy 治理测试）。
    async def run():
        engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
        try:
            sessions = make_sessionmaker(engine)
            async with sessions() as session:
                rows = (
                    await session.execute(select(model).where(*conditions))
                ).scalars().all()
                # Detach plain ORM rows for synchronous assertions.
                for row in rows:
                    session.expunge(row)
                return rows
        finally:
            await engine.dispose()

    return asyncio.run(run())


def test_inventory_and_acceptance_clean_are_conservative(auth_on, tmp_path) -> None:
    db_path = tmp_path / "data-hygiene.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    with TestClient(create_app(db_url)) as client:
        smoke_headers, smoke_id = _register_login(client, "smoke_test")
        normal_headers, _ = _register_login(client, "normal_user")
        _, _ = _register_login(client, "smoke_admin")
        admin_headers, _ = _register_login(client, "ops_admin")
        _promote(db_path, "smoke_admin")
        _promote(db_path, "ops_admin")
        smoke_paper, smoke_exam = _import_and_submit(
            client, smoke_headers, "smoke private paper"
        )
        normal_paper, _ = _import_and_submit(
            client, normal_headers, "normal private paper"
        )
        source = client.post(
            "/api/v1/sources",
            json={
                "id": "src_hygiene",
                "name": "hygiene source",
                "source_type": "oer",
                "license_state": "OPEN_LICENSE",
                "trust_tier": "B",
                "authority_score": 5,
                "homepage": "https://example.edu/",
            },
            headers=admin_headers,
        )
        assert source.status_code == 201, source.text

    _seed_direct_rows(db_url, smoke_id, smoke_exam)
    before = asyncio.run(build_data_inventory(db_url))
    assert before["users"]["total"] == 4
    assert before["users"]["acceptance_candidates"] == 1
    assert before["papers"]["system_seed"] == 2
    assert before["papers"]["private_owned"] == 2
    assert before["risk_summary"]["acceptance_candidate_users"] == 1

    dry_run = asyncio.run(run_acceptance_clean(db_url, ["smoke_"], execute=False))
    assert dry_run["dry_run"] is True
    assert [user["username"] for user in dry_run["matched_users"]] == ["smoke_test"]
    assert dry_run["skipped_admin_usernames"] == ["smoke_admin"]
    assert dry_run["planned_deletions"]["users"] == 1
    assert dry_run["planned_deletions"]["papers"] == 1
    assert dry_run["planned_deletions"]["exam_sessions"] == 1
    assert dry_run["planned_deletions"]["resources"] == 1
    assert dry_run["planned_deletions"]["course_import_drafts"] == 1
    assert dry_run["planned_deletions"]["variant_question_drafts"] == 1

    executed = asyncio.run(run_acceptance_clean(db_url, ["smoke_"], execute=True))
    assert executed["dry_run"] is False
    assert executed["deleted"]["users"] == 1
    assert executed["deleted"]["papers"] == 1
    assert executed["deleted"]["resources"] == 1
    assert executed["object_store"]["attempted"] == 0

    usernames = {row.username for row in _rows(db_path, UserRow)}
    assert usernames == {"normal_user", "smoke_admin", "ops_admin"}
    paper_ids = {row.id for row in _rows(db_path, PaperRow)}
    assert {"functions-basics", "algorithms-basics", normal_paper} <= paper_ids
    assert smoke_paper not in paper_ids
    assert not _rows(db_path, ExamSessionRow, ExamSessionRow.exam_id == smoke_exam)
    assert not _rows(db_path, ResourceRow, ResourceRow.id == "res_smoke")
    assert not _rows(db_path, ChunkRow, ChunkRow.resource_id == "res_smoke")
    assert not _rows(db_path, EvidenceRow, EvidenceRow.resource_id == "res_smoke")
    assert not _rows(db_path, ParseJobRow, ParseJobRow.resource_id == "res_smoke")
    assert not _rows(
        db_path, CourseImportDraftRow, CourseImportDraftRow.id == "cid_smoke"
    )
    assert not _rows(
        db_path, VariantQuestionDraftRow, VariantQuestionDraftRow.id == "vqd_smoke"
    )
    assert not _rows(db_path, SearchQueryRow, SearchQueryRow.query == "smoke query")
    assert not _rows(db_path, VoiceSessionRow, VoiceSessionRow.id == "voice_smoke")
    assert not _rows(
        db_path, VoiceAnswerEventRow, VoiceAnswerEventRow.event_id == "voice_event_smoke"
    )
    assert not _rows(
        db_path, VoiceTraceSpanRow, VoiceTraceSpanRow.session_id == "voice_smoke"
    )
    audits = _rows(db_path, AuditLogRow, AuditLogRow.action == "ops.acceptance_clean")
    assert len(audits) == 1
    assert audits[0].actor_username == "cli-operator"


def test_acceptance_clean_cli_requires_db_and_rejects_wildcards(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    missing = SimpleNamespace(db_url=None)
    assert cli_module._run_data_inventory(missing) == 2
    assert "拒绝盘点" in capsys.readouterr().out

    invalid = SimpleNamespace(db_url=None, marker=["smoke*"], yes=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    assert cli_module._run_acceptance_clean(invalid) == 2
    assert "不得包含 * 或 %" in capsys.readouterr().out


def test_cli_acceptance_clean_prints_dry_run_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "empty.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    with TestClient(create_app(db_url)):
        pass
    monkeypatch.delenv("DATABASE_URL", raising=False)
    args = SimpleNamespace(db_url=db_url, marker=["smoke_"], yes=False)
    assert cli_module._run_acceptance_clean(args) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["dry_run"] is True


def _seed_resource(session, *, rid: str, owner_id: str, storage_key: str, digest: str, now) -> None:
    session.add(
        ResourceRow(
            id=rid,
            source_id=None,
            url=None,
            media_type="application/json",
            title=f"resource {rid}",
            language="zh",
            access_state="private",
            license_state="OPEN_LICENSE",
            owner_id=owner_id,
            content_hash=digest,
            storage_key=storage_key,
            size_bytes=1,
            content_type="application/json",
            parse_status="parsed",
            parser_name="json",
            parse_metrics={},
            parse_error=None,
            fetched_at=now,
        )
    )


def _seed_transcript(session, *, owner_id: str, audio_key: str | None, now) -> None:
    session.add(
        VoiceTranscriptRow(
            provider="local",
            text="transcript",
            confidence=1.0,
            latency_ms=1,
            audio_bytes=1,
            audio_object_key=audio_key,
            owner_id=owner_id,
            audio_stored=audio_key is not None,
            exam_id=None,
            question_id=None,
            created_at=now,
        )
    )


def test_object_store_keys_shared_across_rows_are_never_deleted(auth_on, tmp_path) -> None:
    """对象存储删除资格（M10-03 核心安全不变量）的直接单元测试：

    - smoke 与 normal 的 resource 共享同一 storage_key（跨用户同 content-hash
      去重语义，schema 允许）→ 该 key 不得进入删除集；
    - smoke 独占的 resource key / 转写音频 key → 进入删除集；
    - smoke 转写音频与 normal 转写音频共享同一 key → 不得删除。
    """
    db_path = tmp_path / "object-keys.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    with TestClient(create_app(db_url)) as client:
        _, smoke_id = _register_login(client, "smoke_keys")
        _, normal_id = _register_login(client, "normal_keys")

    now = datetime.now(UTC)

    async def seed_and_plan() -> dict:
        # engine 必须在同一个 event loop 内 dispose（M10-10，同 legacy 治理测试）。
        engine = create_engine(db_url)
        try:
            sessions = make_sessionmaker(engine)
            async with sessions() as session, session.begin():
                _seed_resource(
                    session,
                    rid="res_smoke_shared",
                    owner_id=smoke_id,
                    storage_key="uploads/shared-payload",
                    digest="1" * 64,
                    now=now,
                )
                _seed_resource(
                    session,
                    rid="res_normal_shared",
                    owner_id=normal_id,
                    storage_key="uploads/shared-payload",
                    digest="1" * 64,
                    now=now,
                )
                _seed_resource(
                    session,
                    rid="res_smoke_exclusive",
                    owner_id=smoke_id,
                    storage_key="uploads/smoke-only",
                    digest="2" * 64,
                    now=now,
                )
                _seed_transcript(session, owner_id=smoke_id, audio_key="voice/shared-audio", now=now)
                _seed_transcript(session, owner_id=normal_id, audio_key="voice/shared-audio", now=now)
                _seed_transcript(session, owner_id=smoke_id, audio_key="voice/smoke-only", now=now)
            async with sessions() as session:
                return await _build_cleanup_plan(session, ("smoke_",))
        finally:
            await engine.dispose()

    plan = asyncio.run(seed_and_plan())
    assert [user["username"] for user in plan["matched_users"]] == ["smoke_keys"]
    assert plan["object_keys"] == ["uploads/smoke-only", "voice/smoke-only"]
    assert plan["counts"]["resources"] == 2
    assert plan["counts"]["voice_transcripts"] == 2

    dry_run = asyncio.run(run_acceptance_clean(db_url, ["smoke_"], execute=False))
    assert dry_run["unique_object_keys_eligible_for_delete"] == 2
