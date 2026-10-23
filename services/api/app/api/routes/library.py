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
from app.parsing.chunking import chunk_blocks
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

    catalog_title = resource.title  # session 存活期读取（提交后 detached）
    catalog_url = resource.source_url
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
        # M18-004：导入完成即登记 resources 体系（内容寻址去重 + chunks/
        # evidence 挂载点 + parse_status 生命周期）；幂等 by (owner, hash)
        from app.domain.license import LicenseState
        from app.domain.resource import ResourceRecord
        from app.repositories.resources import ResourceRepository

        repo = ResourceRepository(request.app.state.sessionmaker)
        await repo.create(
            ResourceRecord(
                id=f"res_{uuid.uuid4().hex[:20]}",
                media_type="pdf",
                title=catalog_title or resource.id,
                content_hash=digest,
                storage_key=object_key,
                size_bytes=len(assembled),
                fetched_at=_now(),
                url=catalog_url,
                access_state="private_import",
                license_state=LicenseState.UNKNOWN,  # 用户声明权利——许可状态未知如实
                content_type="application/pdf",
            ),
            owner_id=owner,
        )
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



# ---------------------------------------------------------------- 教材解析（M18-004）


class ParseResultOut(BaseModel):
    job_id: str
    status: str  # parsed / needs_ocr / failed
    parser_name: str
    page_count: int
    chunk_count: int
    low_text_pages: int
    #: PDF outline（章节树候选——Prompt 5 course_unit 输入；空 = 无书签）
    toc: list[dict] = []


@router.post("/imports/{job_id}/parse", response_model=ParseResultOut)
async def parse_import(job_id: str, request: Request) -> ParseResultOut:
    """解析私有导入的教材（文本型 PDF 首选 PyMuPDF；重跑=整资源幂等替换）。

    扫描页（低文本）如实计入 low_text_pages——占比过半标 needs_ocr
    （OCR 属后续基建，不虚构文本）；outline 透出章节树候选。
    """
    await require_user(request)
    owner = current_owner_id(request)
    async with request.app.state.sessionmaker() as session:
        job = await session.get(IngestJobRow, job_id)
        if job is None or job.owner_id != owner:
            raise HTTPException(status_code=404, detail="导入任务不存在")
        if job.status != "ready" or not job.assembled_object_key:
            raise HTTPException(status_code=409, detail=f"导入未就绪（{job.status}）——无法解析")
        digest = job.sha256
        object_key = job.assembled_object_key

    store = getattr(request.app.state, "objects", None)
    if store is None:
        raise HTTPException(status_code=503, detail="对象存储未配置（本部署无 DATABASE_URL 语义）")
    try:
        data = store.get(object_key)
    except KeyError as cause:
        raise HTTPException(status_code=410, detail="内容对象不存在（可能已删除）") from cause

    registry = getattr(request.app.state, "parsers", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="解析器注册表未配置")
    try:
        parser = registry.select("pdf", prefer="pymupdf")
    except Exception as cause:
        raise HTTPException(status_code=503, detail=f"解析器不可用：{cause}") from cause
    try:
        parsed = await asyncio.to_thread(parser.parse, data, "pdf")
    except Exception as cause:  # ParserError/ParserUnavailable 如实透出
        raise HTTPException(status_code=422, detail=f"解析失败：{type(cause).__name__}: {cause}") from cause

    chunks = chunk_blocks(parsed.blocks)
    from app.domain.license import LicenseState
    from app.domain.resource import ResourceRecord
    from app.repositories.chunks import ChunkRepository
    from app.repositories.resources import ResourceRepository

    resources = ResourceRepository(request.app.state.sessionmaker)
    record = await resources.get_by_hash(digest, owner_id=owner, scoped=True)
    if record is None:
        # 兜底：历史 job（资源登记逻辑上线前导入）即时补登（幂等 by hash）
        record, _dedup = await resources.create(
            ResourceRecord(
                id=f"res_{uuid.uuid4().hex[:20]}",
                media_type="pdf",
                title=job.resource_id,
                content_hash=digest,
                storage_key=object_key,
                size_bytes=job.size_bytes,
                fetched_at=_now(),
                access_state="private_import",
                license_state=LicenseState.UNKNOWN,
                content_type="application/pdf",
            ),
            owner_id=owner,
        )
    repo = ChunkRepository(request.app.state.sessionmaker)
    await repo.replace_chunks(
        record.id,
        chunks,
        parser_name=parsed.parser_name,
        license_state="UNKNOWN",
        url=record.url,
    )
    low_text = int(parsed.metrics.get("low_text_pages", 0))
    needs_ocr = low_text * 2 > max(1, parsed.page_count)
    status = "needs_ocr" if needs_ocr else "parsed"
    await resources.set_parse_status(
        record.id, status, parser_name=parsed.parser_name, metrics=dict(parsed.metrics)
    )
    return ParseResultOut(
        job_id=job_id,
        status=status,
        parser_name=parsed.parser_name,
        page_count=parsed.page_count,
        chunk_count=len(chunks),
        low_text_pages=low_text,
        toc=list(parsed.metrics.get("toc", [])),
    )
