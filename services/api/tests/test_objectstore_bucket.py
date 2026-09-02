"""M8-00 release-reality: MinIO-backed object store must survive restarts.

Docker audit blocker 3: compose started MinIO but the API had no S3_*
config, so make_object_store silently fell back to MemoryObjectStore --
uploads vanished on restart. Fixes under test:

1. bucket initialization - MinioObjectStore creates a missing bucket
   idempotently at startup (fresh MinIO volumes ship with no buckets,
   so the first upload would otherwise fail);
2. backend selection - S3_* fully configured selects the MinIO backend
   (boto3 stubbed at sys.modules, no network); partial config falls
   back to the in-memory store;
3. compose wiring - the api service receives S3_* pointing at the minio
   service and waits for it to be healthy.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import yaml

from app.storage.objectstore import (
    MemoryObjectStore,
    MinioObjectStore,
    make_object_store,
)

COMPOSE_FILE = Path(__file__).resolve().parents[3] / "infra" / "docker-compose.yml"


class _FakeExceptions:
    ClientError = type("ClientError", (Exception,), {})


class _FakeS3Client:
    """head_bucket 不存在 -> ClientError -> create_bucket 被调用（记录后不再抛）。"""

    def __init__(self, existing: set[str] | None = None) -> None:
        self.existing = existing or set()
        self.created: list[str] = []
        self.exceptions = _FakeExceptions()

    def head_bucket(self, Bucket: str) -> None:
        if Bucket not in self.existing:
            raise self.exceptions.ClientError({"Error": {"Code": "404"}}, "HeadBucket")

    def create_bucket(self, Bucket: str) -> None:
        self.created.append(Bucket)
        self.existing.add(Bucket)


def _settings(**overrides):
    class _S:
        s3_endpoint = None
        s3_bucket = None
        s3_access_key = None
        s3_secret_key = None

    s = _S()
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def test_missing_bucket_is_created_idempotently() -> None:
    """全新 MinIO 卷无 bucket -> 启动时创建；已存在 -> 不重复创建。"""
    fresh = _FakeS3Client()
    MinioObjectStore("http://minio:9000", "aios-objects", "k", "s", client=fresh)
    assert fresh.created == ["aios-objects"]

    existing = _FakeS3Client(existing={"aios-objects"})
    MinioObjectStore("http://minio:9000", "aios-objects", "k", "s", client=existing)
    assert existing.created == []


def test_make_object_store_selects_minio_when_configured(monkeypatch) -> None:
    """S3_* 四项齐全 -> MinIO 后端（boto3 桩掉，无网络）；缺任一 -> 内存替身。"""
    fake = _FakeS3Client(existing={"aios-objects"})
    monkeypatch.setitem(
        sys.modules, "boto3", types.SimpleNamespace(client=lambda *_a, **_kw: fake)
    )
    store = make_object_store(
        _settings(
            s3_endpoint="http://minio:9000",
            s3_bucket="aios-objects",
            s3_access_key="aios",
            s3_secret_key="s",
        ),
    )
    assert isinstance(store, MinioObjectStore)
    assert not isinstance(store, MemoryObjectStore)

    fallback = make_object_store(_settings(s3_endpoint="http://minio:9000"))
    assert isinstance(fallback, MemoryObjectStore)


def test_compose_wires_s3_and_minio_dependency() -> None:
    """compose api 服务注入 S3_*（指向 minio）且 depends_on minio healthy。"""
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    api = compose["services"]["api"]
    env = api["environment"]
    assert env["S3_ENDPOINT"] == "http://minio:9000"
    assert env["S3_BUCKET"]
    assert env["S3_ACCESS_KEY"] and env["S3_SECRET_KEY"]
    assert api["depends_on"]["minio"]["condition"] == "service_healthy"
    # minio 自身必须有健康门（api 等 healthy 才有意义）
    assert "healthcheck" in compose["services"]["minio"]
