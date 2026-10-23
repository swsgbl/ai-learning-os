"""M18-003 用户私有导入 API（审计升级包 Prompt 3 / Phase 2）。

端点：
- POST /api/v1/library/imports —— 从目录条目发起私有导入（Mode B）：
  用户显式声明使用权限（permission_statement 必填 true——审计 02
  §3「用户确认其具有相应使用权限」）；服务端按目录条目的 pin-commit
  raw URL 下载分片（**不接受用户传 URL**——SSRF 面收敛到目录事实源），
  数值序 SHA256 逐片+整体双算，二进制拼接校验 PDF 魔数，落 ObjectStore
  用户命名空间；同 (owner, resource) 重复导入幂等返回既有 job。
- GET  /api/v1/library/imports/{job_id} —— 任务状态（含失败原因如实）。

安全边界：
- 每次读取校验 owner（blob 私有——key 命名空间不是授权依据）；
- 下载串行 + 大小上限（DEFAULT_MAX_TOTAL_BYTES，env 可收紧）；
- 失败态如实（incomplete_parts/failed 不折算成功）。
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.routes.auth import current_owner_id, require_user
from app.db.orm import ContentBlobRow, IngestJobRow, ResourceRecordRow
from app.domain.ingest import (
    PartManifest,
    assemble_pdf,
    enforce_size_limit,
)
from app.storage.objectstore import MemoryObjectStore

router = APIRouter(prefix="/api/v1/library", tags=["library"])

_RAW_BASE = "https://raw.githubusercontent.com/TapXWorld/ChinaTextbook"


def _now() -> datetime:
    return datetime.now(tz=UTC)


class ImportRequest(BaseModel):
    resource_id: str = Field(min_length=1, max_length=64)
    #: Mode B 权利声明（审计 02 §3）：用户确认对所选内容有合法使用权
    permission_statement: bool


class ImportJobOut(BaseModel):
    id: str
    resource_id: str
    status: str
    parts_total: int
    parts_fetched: int
    sha256: str
    size_bytes: int
    error: str


def _job_out(row: IngestJobRow) -> ImportJobOut:
    return ImportJobOut(
        id=row.id,
        resource_id=row.resource_id,
        status=row.status,
        parts_total=row.parts_total,
        parts_fetched=row.parts_fetched,
        sha256=row.sha256,
        size_bytes=row.size_bytes,
        error=row.error,
    )


def _download_parts(
    commit_sha: str, manifest: PartManifest, *, transport: httpx.BaseTransport | None = None
) -> list[bytes]:
    """按数值序串行下载分片（raw URL 由服务端构造——不接受外部 URL）。

    出网代理：LIBRARY_OUTBOUND_PROXY 显式优先，回落 LLM_OUTBOUND_PROXY
    （同一部署隧道——国内直连 raw.githubusercontent 实测不可达，live
    2026-10-23：SSL connect error）。域固定 raw.githubusercontent.com
    ——代理只用于该 allowlist 域，不作用于任意目标。
    """
    import os

    proxy = os.environ.get("LIBRARY_OUTBOUND_PROXY") or os.environ.get("LLM_OUTBOUND_PROXY")
    chunks: list[bytes] = []
    accumulated = 0
    with httpx.Client(
        transport=transport, timeout=120.0, trust_env=True, follow_redirects=True, proxy=proxy
    ) as client:
        for index, part_path in manifest.entries:
            url = f"{_RAW_BASE}/{commit_sha}/{part_path}"
            resp = client.get(url)
            resp.raise_for_status()
            accumulated += len(resp.content)
            # 逐片累积限流（先下后限=可先耗尽内存——安全审查修复）：
            # 超限立即中止，不等全部片落内存
            enforce_size_limit(accumulated)
            chunks.append(resp.content)
            _ = index
    return chunks


@router.post("/imports", response_model=ImportJobOut, status_code=201)
async def create_import(payload: ImportRequest, request: Request) -> ImportJobOut:
    await require_user(request)
    owner = current_owner_id(request)
    if not payload.permission_statement:
        raise HTTPException(
            status_code=422,
            detail="私有导入需先确认你对所选内容具有合法使用权（permission_statement=true）",
        )
    async with request.app.state.sessionmaker() as session:
        resource = await session.get(ResourceRecordRow, payload.resource_id)
        if resource is None:
            raise HTTPException(status_code=404, detail="目录条目不存在")
        # 幂等：同 (owner, resource) 已有 job → 返回既有（含失败态——如实）
        existing = (
            await session.execute(
                select(IngestJobRow).where(
                    (IngestJobRow.owner_id == owner)
                    & (IngestJobRow.resource_id == payload.resource_id)
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return _job_out(existing)
        job = IngestJobRow(
            id=f"ij_{uuid.uuid4().hex[:22]}",
            owner_id=owner,
            resource_id=resource.id,
            status="queued",
            parts_total=len(resource.parts_json or []),
            created_at=_now(),
            updated_at=_now(),
        )
        session.add(job)
        await session.commit()
    job_id = job.id

    manifest = PartManifest(
        entries=tuple(
            (int(p["part_index"]), p["source_path"])
            for p in (resource.parts_json or [{"part_index": 1, "source_path": resource.source_path}])
        )
    )
    try:
        manifest.validate_continuity()
    except ValueError as cause:
        await _fail_job(request, job_id, "incomplete_parts", str(cause))
        raise HTTPException(status_code=409, detail=str(cause)) from cause
    try:
        # 下载/拼接为同步阻塞 IO——线程池执行（事件循环不阻塞：
        # 与 LLM 调用同款 asyncio.to_thread 模式）
        parts = await asyncio.to_thread(_download_parts, resource.source_commit_sha, manifest)
        total = sum(len(p) for p in parts)
        enforce_size_limit(total)
        assembled, digest = await asyncio.to_thread(assemble_pdf, parts)
    except (ValueError, httpx.HTTPError) as cause:
        detail = str(cause) or type(cause).__name__
        status = "incomplete_parts" if "片" in detail else "failed"
        await _fail_job(request, job_id, status, detail[:500])
        raise HTTPException(status_code=502, detail=f"导入失败：{detail[:200]}") from cause

    store = getattr(request.app.state, "objects", None) or MemoryObjectStore()
    object_key = f"private/{owner}/{resource.id}/{digest}.pdf"
    store.put(object_key, assembled, content_type="application/pdf")
    async with request.app.state.sessionmaker() as session:
        row = await session.get(IngestJobRow, job_id)
        assert row is not None
        row.status = "ready"
        row.parts_fetched = len(parts)
        row.sha256 = digest
        row.size_bytes = len(assembled)
        row.assembled_object_key = object_key
        row.updated_at = _now()
        session.add(
            ContentBlobRow(
                id=f"cb_{uuid.uuid4().hex[:22]}",
                owner_id=owner,
                resource_id=resource.id,
                job_id=job_id,
                owner_scope="user_private",
                object_key=object_key,
                sha256=digest,
                size_bytes=len(assembled),
                mime_type="application/pdf",
                created_at=_now(),
            )
        )
        await session.commit()
        return _job_out(row)


async def _fail_job(request: Request, job_id: str, status: str, detail: str) -> None:
    async with request.app.state.sessionmaker() as session:
        row = await session.get(IngestJobRow, job_id)
        assert row is not None
        row.status = status
        row.error = detail[:500]
        row.updated_at = _now()
        await session.commit()


@router.get("/imports/{job_id}", response_model=ImportJobOut)
async def get_import(job_id: str, request: Request) -> ImportJobOut:
    await require_user(request)
    owner = current_owner_id(request)
    async with request.app.state.sessionmaker() as session:
        row = await session.get(IngestJobRow, job_id)
        if row is None or row.owner_id != owner:
            raise HTTPException(status_code=404, detail="导入任务不存在")
        return _job_out(row)

