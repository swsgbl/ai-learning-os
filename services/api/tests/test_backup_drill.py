"""M6-06 Backup/restore drill：三件套备份、完整性校验与恢复演练。

验收（backlog M6-06）：数据库、对象存储和配置可备份；恢复后考试与报告仍可读。

- SQLite roundtrip：造数据（考试->作答->提交->报告）-> 备份三件套 -> 数据全毁 ->
  恢复 -> 考试/报告逐字一致 + 配置文件逐字一致；
- manifest 篡改 fail-closed：恢复前校验先行，目标库未被触碰；
- MinIO 对象 roundtrip：独立 bucket 隔离（AIOS_S3_TEST_ENDPOINT 门控）；
- 真实 PG drill：备份源库 -> 独立 drill 库（CREATE DATABASE）恢复 -> 考试与报告
  逐字一致（AIOS_PG_TEST_URL 安全门控：源库必须是隔离测试库 ai_learning_os_test，
  drill 库用后即删，不碰任何主库/共享库数据）。
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db.session import create_engine
from app.db.test_gate import pg_test_gate_from_env
from app.main import create_app
from app.ops.backup import (
    BackupIntegrityError,
    MinioBackupSource,
    dump_database,
    dump_objects,
    restore_objects,
    run_backup,
    run_restore,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DRILL_DB = "ai_learning_os_drill"
PG_GATE = pg_test_gate_from_env()
PG_URL = PG_GATE.url
S3_ENDPOINT = os.environ.get("AIOS_S3_TEST_ENDPOINT")



def _seeded_exam(client) -> str:
    """开考 + 全卷答 B + 提交；返回 exam_id（seed 卷 functions-basics）。"""
    papers = client.get("/api/v1/papers").json()
    assert papers
    paper_id = papers[0]["id"]
    started = client.post(f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"})
    assert started.status_code == 201, started.text
    exam = started.json()
    exam_id = exam["exam_id"]
    for index, question in enumerate(exam["questions"], start=1):
        res = client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": index, "question_id": question["id"], "answer": "B"},
        )
        assert res.status_code == 200, res.text
    submitted = client.post(f"/api/v1/exams/{exam_id}/submit", json={})
    assert submitted.status_code == 200, submitted.text
    return exam_id


def _wipe_rows(url: str) -> None:
    """破坏模拟：清空全部表行（schema 保留）。"""
    def _wipe(sync_conn):
        from sqlalchemy import MetaData

        meta = MetaData()
        meta.reflect(bind=sync_conn)
        for table in reversed(meta.sorted_tables):
            sync_conn.execute(table.delete())

    async def _run():
        engine = create_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(_wipe)
        await engine.dispose()

    asyncio.run(_run())

DRILL_TS = "2026-09-01T00:00:00+00:00"


def test_sqlite_roundtrip_exam_and_report_survive(tmp_path):
    """全链路 drill：造数据->备份三件套->数据全毁->恢复->考试与报告逐字一致。"""
    db_path = tmp_path / "drill.db"
    url = "sqlite+aiosqlite:///" + str(db_path)
    config_paths = [
        REPO_ROOT / "infra" / "docker-compose.yml",
        REPO_ROOT / "infra" / "livekit" / "livekit.yaml",
        REPO_ROOT / ".env.example",
    ]
    with TestClient(create_app(url)) as client:
        exam_id = _seeded_exam(client)
        report_before = client.get(f"/api/v1/exams/{exam_id}/report").json()
        assert report_before["total_count"] >= 1
        active_before = client.get(f"/api/v1/exams/{exam_id}").json()

    out_dir = tmp_path / "backup"
    manifest = asyncio.run(
        run_backup(url, out_dir, None, config_paths, created_at=DRILL_TS)
    )
    assert manifest["schema_version"] == "aios-backup-v1"
    assert manifest["created_at"] == DRILL_TS
    assert manifest["tables"]["papers"] >= 1
    assert manifest["tables"]["exam_sessions"] >= 1
    assert manifest["tables"]["answer_events"] >= 3
    assert manifest["tables"]["submissions"] >= 1
    files = manifest["files"]
    assert "database.json" in files
    assert "configs/infra/docker-compose.yml" in files
    assert "configs/.env.example" in files

    _wipe_rows(url)
    engine = create_engine(url)
    dumped = asyncio.run(dump_database(engine))
    assert all(len(rows) == 0 for rows in dumped.values())
    asyncio.run(engine.dispose())

    restored_target = tmp_path / "configs-restored"
    inserted = asyncio.run(
        run_restore(out_dir, url, None, restored_target)
    )
    assert inserted >= 5
    with TestClient(create_app(url)) as client:
        report_after = client.get(f"/api/v1/exams/{exam_id}/report").json()
        active_after = client.get(f"/api/v1/exams/{exam_id}").json()
        assert report_after == report_before
        # remaining_seconds 随墙钟自然流逝（恢复需耗时），排除后逐字段全等
        for key in ("remaining_seconds", "server_remaining_seconds"):
            active_before.pop(key, None)
            active_after.pop(key, None)
        assert active_after == active_before
    restored_cfg = restored_target / "infra" / "docker-compose.yml"
    expected_cfg = (REPO_ROOT / "infra" / "docker-compose.yml").read_bytes()
    assert restored_cfg.read_bytes() == expected_cfg
    expected_env = (REPO_ROOT / ".env.example").read_bytes()
    assert (restored_target / ".env.example").read_bytes() == expected_env


def test_tampered_backup_rejected_and_target_untouched(tmp_path):
    """manifest 篡改 fail-closed：校验先行——目标库在拒绝时未被改动。"""
    db_path = tmp_path / "tamper.db"
    url = "sqlite+aiosqlite:///" + str(db_path)
    with TestClient(create_app(url)) as client:
        _seeded_exam(client)
    out_dir = tmp_path / "backup"
    asyncio.run(run_backup(url, out_dir, None, []))
    target_file = out_dir / "database.json"
    tampered = target_file.read_text(encoding="utf-8") + "\nTAMPERED"
    target_file.write_text(tampered, encoding="utf-8")
    engine = create_engine(url)
    before = asyncio.run(dump_database(engine))
    before_rows = {name: len(rows) for name, rows in before.items()}
    asyncio.run(engine.dispose())
    with pytest.raises(BackupIntegrityError):
        asyncio.run(run_restore(out_dir, url, None, None))
    after = asyncio.run(dump_database(engine))
    after_rows = {name: len(rows) for name, rows in after.items()}
    assert before_rows == after_rows
    asyncio.run(engine.dispose())

def _minio_source(bucket: str) -> MinioBackupSource:
    """AIOS_S3_TEST_* 环境变量装配 MinioBackupSource（drill 独立 bucket）。"""
    return MinioBackupSource(
        endpoint=S3_ENDPOINT,
        bucket=bucket,
        access_key=os.environ.get("AIOS_S3_TEST_ACCESS_KEY", "aios"),
        secret_key=os.environ.get("AIOS_S3_TEST_SECRET_KEY", "aios12345"),
    )


@pytest.mark.skipif(not S3_ENDPOINT, reason="需要 AIOS_S3_TEST_ENDPOINT 指向真实 MinIO")
def test_minio_object_backup_roundtrip(tmp_path):
    """对象存储 drill：独立 bucket 内 dump->清空->restore->内容逐字一致。"""
    bucket = "aios-drill-" + uuid.uuid4().hex[:8]
    source = _minio_source(bucket)
    client = source._client
    client.create_bucket(Bucket=bucket)
    try:
        source.put("voice/aa/obj1.bin", b"alpha-payload")
        source.put("voice/bb/obj2.bin", b"beta-payload")
        out_dir = tmp_path / "objects-backup"
        hashes = dump_objects(source, out_dir)
        assert hashes["voice/aa/obj1.bin"] == hashlib.sha256(b"alpha-payload").hexdigest()
        client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": "voice/aa/obj1.bin"}, {"Key": "voice/bb/obj2.bin"}]},
        )
        assert source.list_keys() == []
        restored = restore_objects(source, out_dir)
        assert restored == 2
        assert source.get("voice/aa/obj1.bin") == b"alpha-payload"
        assert source.get("voice/bb/obj2.bin") == b"beta-payload"
    finally:
        keys = source.list_keys()
        if keys:
            client.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in keys]})
        client.delete_bucket(Bucket=bucket)


def _recreate_drill_database() -> str:
    """DROP + CREATE 独立 drill 库；返回其 asyncpg URL（不碰主库数据）。"""
    admin_url = PG_URL.rsplit("/", 1)[0] + "/postgres"
    drill_url = PG_URL.rsplit("/", 1)[0] + "/" + DRILL_DB

    async def _run():
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine as _cae

        admin = _cae(admin_url, isolation_level="AUTOCOMMIT")
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{DRILL_DB}" WITH (FORCE)'))
            await conn.execute(text(f'CREATE DATABASE "{DRILL_DB}"'))
        await admin.dispose()

    asyncio.run(_run())
    return drill_url


def _drop_drill_database() -> None:
    admin_url = PG_URL.rsplit("/", 1)[0] + "/postgres"

    async def _run():
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine as _cae

        admin = _cae(admin_url, isolation_level="AUTOCOMMIT"
        )
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{DRILL_DB}" WITH (FORCE)'))
        await admin.dispose()

    asyncio.run(_run())


@pytest.mark.skipif(not PG_GATE.enabled, reason=PG_GATE.reason)
def test_pg_backup_restore_drill_preserves_exam_and_report():
    """真实 PG drill：隔离测试库备份 -> 独立 drill 库恢复 -> 考试与报告逐字一致。"""
    with TestClient(create_app(PG_URL)) as client:
        exam_id = _seeded_exam(client)
        report_before = client.get(f"/api/v1/exams/{exam_id}/report").json()
        assert report_before["total_count"] >= 1
        active_before = client.get(f"/api/v1/exams/{exam_id}").json()

    out_dir = Path(tempfile.mkdtemp(prefix="pg-drill-"))
    manifest = asyncio.run(
        run_backup(PG_URL, out_dir, None, [REPO_ROOT / "infra" / "docker-compose.yml"])
    )
    assert manifest["tables"]["exam_sessions"] >= 1
    assert manifest["tables"]["submissions"] >= 1

    drill_url = _recreate_drill_database()
    try:
        # 损坏态：drill 库先由 create_app 建表 + seed（模拟脏库），再被恢复覆盖
        with TestClient(create_app(drill_url)) as dirty:
            assert dirty.get("/api/v1/papers").json()
        inserted = asyncio.run(run_restore(out_dir, drill_url, None, None))
        assert inserted >= 5
        with TestClient(create_app(drill_url)) as client2:
            report_after = client2.get(f"/api/v1/exams/{exam_id}/report").json()
            active_after = client2.get(f"/api/v1/exams/{exam_id}").json()
            assert report_after == report_before
            for key in ("remaining_seconds", "server_remaining_seconds"):
                active_before.pop(key, None)
                active_after.pop(key, None)
            assert active_after == active_before
    finally:
        _drop_drill_database()