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
from app.llm.gateway import ChatMessage, LlmGateway, LlmUnavailable
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


# ---------------------------------------------------------------- 课程结构（M18-005）


class CourseUnitOut(BaseModel):
    id: str
    sequence: int
    title: str
    unit_type: str
    level: int
    status: str
    page: int
    parent_id: str | None


class StructureOut(BaseModel):
    job_id: str
    resource_id: str
    unit_count: int
    confirmed_count: int
    units: list[CourseUnitOut]


def _unit_out(row) -> CourseUnitOut:
    return CourseUnitOut(
        id=row.id,
        sequence=row.sequence,
        title=row.title,
        unit_type=row.unit_type,
        level=row.level,
        status=row.status,
        page=int((row.anchor or {}).get("page", 0)),
        parent_id=row.parent_id,
    )


async def _owned_ready_job(request: Request, job_id: str, owner):
    from app.db.orm import IngestJobRow as _Job

    async with request.app.state.sessionmaker() as session:
        job = await session.get(_Job, job_id)
        if job is None or job.owner_id != owner:
            raise HTTPException(status_code=404, detail="导入任务不存在")
        if job.status != "ready":
            raise HTTPException(status_code=409, detail=f"导入未就绪（{job.status}）")
        return job


@router.post("/imports/{job_id}/structure", response_model=StructureOut, status_code=201)
async def build_course_structure(job_id: str, request: Request) -> StructureOut:
    """从已解析教材的 outline 生成章节树候选（重跑=整树替换幂等）。

    候选全部 status=candidate——人工确认（confirm 端点）后才成为正式
    结构（审计 Prompt 5：候选不经校验不进入正式路径）。
    """
    await require_user(request)
    owner = current_owner_id(request)
    job = await _owned_ready_job(request, job_id, owner)
    from app.db.orm import CourseUnitRow, ResourceRow
    from app.domain.course_structure import CourseStructureError, toc_to_candidates

    async with request.app.state.sessionmaker() as session:
        record = (
            await session.execute(
                select(ResourceRow).where(
                    (ResourceRow.content_hash == job.sha256)
                    & (ResourceRow.owner_id == owner)
                )
            )
        ).scalar_one_or_none()
        if record is None or record.parse_status not in {"parsed", "needs_ocr"}:
            raise HTTPException(status_code=409, detail="先解析（/parse）再提取结构")
        toc = list((record.parse_metrics or {}).get("toc", []))
        resource_id = record.id
    if not toc:
        raise HTTPException(status_code=409, detail="该教材无 outline（扫描书或无书签）——结构提取需要文本型解析产出")
    try:
        candidates = toc_to_candidates(toc)
    except CourseStructureError as cause:
        raise HTTPException(status_code=422, detail=str(cause)) from cause

    now = _now()
    async with request.app.state.sessionmaker() as session:
        # 整树替换（重提取幂等——旧 confirmed 状态如实丢失，重提取=重建候选）
        olds = (
            await session.execute(select(CourseUnitRow).where(CourseUnitRow.resource_id == resource_id))
        ).scalars().all()
        for row in olds:
            await session.delete(row)
        await session.flush()
        id_by_seq: dict[int, str] = {}
        rows = []
        for cand in candidates:
            unit_id = f"cu_{uuid.uuid4().hex[:22]}"
            id_by_seq[cand.sequence] = unit_id
            rows.append(
                CourseUnitRow(
                    id=unit_id,
                    resource_id=resource_id,
                    parent_id=id_by_seq.get(cand.parent_sequence) if cand.parent_sequence else None,
                    sequence=cand.sequence,
                    title=cand.title[:255],
                    unit_type=cand.unit_type,
                    level=cand.level,
                    status="candidate",
                    anchor=dict(cand.anchor),
                    created_at=now,
                )
            )
        for row in rows:
            session.add(row)
        await session.commit()
        for row in rows:
            await session.refresh(row)
    return StructureOut(
        job_id=job_id,
        resource_id=resource_id,
        unit_count=len(rows),
        confirmed_count=0,
        units=[_unit_out(row) for row in rows],
    )


@router.get("/imports/{job_id}/structure", response_model=StructureOut)
async def get_course_structure(job_id: str, request: Request) -> StructureOut:
    await require_user(request)
    owner = current_owner_id(request)
    job = await _owned_ready_job(request, job_id, owner)
    from app.db.orm import CourseUnitRow, ResourceRow

    async with request.app.state.sessionmaker() as session:
        record = (
            await session.execute(
                select(ResourceRow).where(
                    (ResourceRow.content_hash == job.sha256)
                    & (ResourceRow.owner_id == owner)
                )
            )
        ).scalar_one_or_none()
        if record is None:
            raise HTTPException(status_code=404, detail="结构未提取——先 POST structure")
        rows = (
            await session.execute(
                select(CourseUnitRow)
                .where(CourseUnitRow.resource_id == record.id)
                .order_by(CourseUnitRow.sequence)
            )
        ).scalars().all()
        return StructureOut(
            job_id=job_id,
            resource_id=record.id,
            unit_count=len(rows),
            confirmed_count=sum(1 for r in rows if r.status == "confirmed"),
            units=[_unit_out(r) for r in rows],
        )


@router.post("/imports/{job_id}/structure/{unit_id}/confirm", response_model=CourseUnitOut)
async def confirm_course_unit(job_id: str, unit_id: str, request: Request) -> CourseUnitOut:
    """人工确认候选单元（candidate→confirmed——校验路径）。"""
    await require_user(request)
    owner = current_owner_id(request)
    await _owned_ready_job(request, job_id, owner)
    from app.db.orm import CourseUnitRow

    async with request.app.state.sessionmaker() as session:
        row = await session.get(CourseUnitRow, unit_id)
        if row is None:
            raise HTTPException(status_code=404, detail="单元不存在")
        row.status = "confirmed"
        await session.commit()
        await session.refresh(row)
        return _unit_out(row)


# ---------------------------------------------------------------- 概念候选（M18-006）


class ConceptCandidateOut(BaseModel):
    id: str
    concept_id: str
    canonical_name: str
    subject: str
    description: str
    difficulty: int
    confidence: float
    status: str
    page: int | None


class EdgeCandidateOut(BaseModel):
    id: str
    from_concept: str
    to_concept: str
    relation: str
    confidence: float
    status: str


class ConceptExtractionOut(BaseModel):
    job_id: str
    concept_count: int
    edge_count: int
    dropped: int


_CONCEPT_SYSTEM_PROMPT = (
    "你是课程设计师。根据教材章节树与章节文本样本，提取这本书教授的核心知识概念。"
    "概念是可测试的知识单元（如：一元二次方程、集合的交集）。"
    '输出 JSON：{"concepts": [{"id": "学科:主题", "canonical_name": "中文名", "subject": "学科", '
    '"description": "一句话定义", "difficulty": 1, "confidence": 0.8, "page": 12}], '
    '"edges": [{"from": "概念id", "to": "概念id", "relation": "prerequisite_of", "confidence": 0.7}]}。'
    "id 规则：全小写，学科与主题用冒号分隔，多词连字符（math:quadratic-equation）。"
    "relation 只能是 prerequisite_of/part_of/similar_to/often_confused_with/applies_to；"
    "prerequisite_of 表示 from 是 to 的先修概念。最多 15 个概念。只输出 JSON。"
)


@router.post("/imports/{job_id}/concepts", response_model=ConceptExtractionOut, status_code=201)
async def extract_concept_candidates(job_id: str, request: Request) -> ConceptExtractionOut:
    """LLM 从已解析教材提取概念/边候选（人工确认后经既有 DAG 管道进正式图）。

    幂等：重提取不覆盖已 confirmed/rejected 的候选（确认不被静默丢失）。
    """
    await require_user(request)
    owner = current_owner_id(request)
    job = await _owned_ready_job(request, job_id, owner)
    from app.db.orm import (
        ConceptCandidateRow,
        ConceptEdgeCandidateRow,
        CourseUnitRow,
        ResourceRow,
    )

    async with request.app.state.sessionmaker() as session:
        record = (
            await session.execute(
                select(ResourceRow).where(
                    (ResourceRow.content_hash == job.sha256) & (ResourceRow.owner_id == owner)
                )
            )
        ).scalar_one_or_none()
        if record is None or record.parse_status != "parsed":
            raise HTTPException(status_code=409, detail="先解析（parsed 状态）再提取概念")
        units = (
            await session.execute(
                select(CourseUnitRow)
                .where(CourseUnitRow.resource_id == record.id)
                .order_by(CourseUnitRow.sequence)
                .limit(40)
            )
        ).scalars().all()
        chunks_repo = getattr(request.app.state, "chunks", None)
        chunks = await chunks_repo.list_chunks(record.id) if chunks_repo else []
        resource_id = record.id
    if not chunks:
        raise HTTPException(status_code=409, detail="无解析文本（chunks 空）——概念提取需要文本型解析产出")

    section_lines = [
        f"- {u.title}（第{int((u.anchor or {}).get('page', 0))}页）" for u in units
    ] or ["（无章节树——按文本顺序）"]
    sample_chars = 0
    samples: list[str] = []
    for chunk in chunks[:40]:
        text = str(chunk.get("text", ""))[:400]
        if sample_chars + len(text) > 6000:
            break
        samples.append(text)
        sample_chars += len(text)
    prompt = "章节目录：\n" + "\n".join(section_lines) + "\n\n文本样本：\n" + "\n".join(samples)

    from app.api.routes.me_ai_keys import resolve_user_llm_config
    from app.core.config import get_settings

    settings = get_settings()
    endpoint, api_key, model = settings.llm_endpoint, settings.llm_api_key, settings.llm_model
    personal = await resolve_user_llm_config(request.app, owner)
    if personal:
        endpoint = personal["endpoint"] or endpoint
        api_key = personal["api_key"]
        model = personal["model"] or model
    if not (endpoint and api_key and model):
        raise HTTPException(status_code=422, detail="未配置 LLM——无法提取概念候选")
    gateway = LlmGateway(
        endpoint=endpoint, api_key=api_key, model=model,
        timeout_seconds=settings.llm_timeout_seconds or 30.0,
        ollama_native=settings.llm_provider == "ollama",
    )
    messages = (
        ChatMessage(role="system", content=_CONCEPT_SYSTEM_PROMPT),
        ChatMessage(role="user", content=prompt),
    )
    try:
        raw = await asyncio.to_thread(gateway.chat, messages, temperature=0.2, max_tokens=3000)
    except LlmUnavailable as cause:
        raise HTTPException(status_code=503, detail=f"LLM 调用失败：{cause}") from cause

    from app.domain.concept_candidates import (
        ConceptCandidateError,
        validate_concept_candidate,
        validate_edge_candidate,
    )
    from app.domain.concept_derivation import _parse_json

    parsed = _parse_json(raw)
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail="LLM 输出不可解析（可重试）")
    dropped = 0
    valid_concepts = []
    for entry in parsed.get("concepts", [])[:30]:
        try:
            valid_concepts.append(validate_concept_candidate(entry))
        except ConceptCandidateError:
            dropped += 1
    known = {c.concept_id for c in valid_concepts}
    valid_edges = []
    for entry in parsed.get("edges", [])[:40]:
        try:
            valid_edges.append(validate_edge_candidate(entry, known))
        except ConceptCandidateError:
            dropped += 1
    if not valid_concepts:
        raise HTTPException(status_code=502, detail=f"概念候选全部未通过校验（丢弃 {dropped}）")

    now = _now()
    async with request.app.state.sessionmaker() as session:
        existing = {
            row.concept_id: row
            for row in (
                await session.execute(
                    select(ConceptCandidateRow).where(ConceptCandidateRow.resource_id == resource_id)
                )
            ).scalars()
        }
        for cand in valid_concepts:
            prior = existing.get(cand.concept_id)
            if prior is not None:
                if prior.status == "candidate":  # 确认/拒绝不被覆盖
                    prior.canonical_name = cand.canonical_name
                    prior.subject = cand.subject
                    prior.description = cand.description
                    prior.difficulty = cand.difficulty
                    prior.confidence = cand.confidence
                    prior.provenance = dict(cand.provenance)
                    prior.updated_at = now
                continue
            session.add(
                ConceptCandidateRow(
                    id=f"cc_{uuid.uuid4().hex[:22]}",
                    resource_id=resource_id,
                    owner_id=owner,
                    concept_id=cand.concept_id,
                    canonical_name=cand.canonical_name,
                    subject=cand.subject,
                    description=cand.description,
                    difficulty=cand.difficulty,
                    confidence=cand.confidence,
                    provenance=dict(cand.provenance),
                    status="candidate",
                    created_at=now,
                    updated_at=now,
                )
            )
        existing_edges = {
            (row.from_concept, row.to_concept, row.relation): row
            for row in (
                await session.execute(
                    select(ConceptEdgeCandidateRow).where(ConceptEdgeCandidateRow.resource_id == resource_id)
                )
            ).scalars()
        }
        for edge in valid_edges:
            key = (edge.from_concept, edge.to_concept, edge.relation)
            if key in existing_edges:
                continue
            session.add(
                ConceptEdgeCandidateRow(
                    id=f"ce_{uuid.uuid4().hex[:22]}",
                    resource_id=resource_id,
                    owner_id=owner,
                    from_concept=edge.from_concept,
                    to_concept=edge.to_concept,
                    relation=edge.relation,
                    confidence=edge.confidence,
                    provenance=dict(edge.provenance),
                    status="candidate",
                    created_at=now,
                    updated_at=now,
                )
            )
        await session.commit()
    return ConceptExtractionOut(
        job_id=job_id,
        concept_count=len(valid_concepts),
        edge_count=len(valid_edges),
        dropped=dropped,
    )


@router.get("/imports/{job_id}/concepts")
async def list_concept_candidates(job_id: str, request: Request) -> dict:
    await require_user(request)
    owner = current_owner_id(request)
    job = await _owned_ready_job(request, job_id, owner)
    from app.db.orm import ConceptCandidateRow, ConceptEdgeCandidateRow, ResourceRow

    async with request.app.state.sessionmaker() as session:
        record = (
            await session.execute(
                select(ResourceRow).where(
                    (ResourceRow.content_hash == job.sha256) & (ResourceRow.owner_id == owner)
                )
            )
        ).scalar_one_or_none()
        if record is None:
            raise HTTPException(status_code=404, detail="候选未提取——先 POST concepts")
        concepts = (
            await session.execute(
                select(ConceptCandidateRow)
                .where(ConceptCandidateRow.resource_id == record.id)
                .order_by(ConceptCandidateRow.confidence.desc())
            )
        ).scalars().all()
        edges = (
            await session.execute(
                select(ConceptEdgeCandidateRow).where(ConceptEdgeCandidateRow.resource_id == record.id)
            )
        ).scalars().all()
        return {
            "concepts": [
                ConceptCandidateOut(
                    id=row.id, concept_id=row.concept_id, canonical_name=row.canonical_name,
                    subject=row.subject, description=row.description, difficulty=row.difficulty,
                    confidence=row.confidence, status=row.status,
                    page=(row.provenance or {}).get("page"),
                ).model_dump()
                for row in concepts
            ],
            "edges": [
                EdgeCandidateOut(
                    id=row.id, from_concept=row.from_concept, to_concept=row.to_concept,
                    relation=row.relation, confidence=row.confidence, status=row.status,
                ).model_dump()
                for row in edges
            ],
        }


async def _set_candidate_status(request: Request, job_id: str, table, row_id: str, owner, status: str):
    job = await _owned_ready_job(request, job_id, owner)
    from app.db.orm import ResourceRow

    async with request.app.state.sessionmaker() as session:
        record = (
            await session.execute(
                select(ResourceRow).where(
                    (ResourceRow.content_hash == job.sha256) & (ResourceRow.owner_id == owner)
                )
            )
        ).scalar_one_or_none()
        if record is None:
            raise HTTPException(status_code=404, detail="候选未提取")
        row = await session.get(table, row_id)
        if row is None or row.resource_id != record.id:
            raise HTTPException(status_code=404, detail="候选不存在")
        row.status = status
        row.updated_at = _now()
        await session.commit()
        await session.refresh(row)
        return row


@router.post("/imports/{job_id}/concepts/{candidate_id}/confirm")
async def confirm_concept_candidate(job_id: str, candidate_id: str, request: Request) -> dict:
    await require_user(request)
    from app.db.orm import ConceptCandidateRow

    row = await _set_candidate_status(
        request, job_id, ConceptCandidateRow, candidate_id, current_owner_id(request), "confirmed"
    )
    return {"id": row.id, "concept_id": row.concept_id, "status": row.status}


@router.post("/imports/{job_id}/concepts/{candidate_id}/reject")
async def reject_concept_candidate(job_id: str, candidate_id: str, request: Request) -> dict:
    await require_user(request)
    from app.db.orm import ConceptCandidateRow

    row = await _set_candidate_status(
        request, job_id, ConceptCandidateRow, candidate_id, current_owner_id(request), "rejected"
    )
    return {"id": row.id, "concept_id": row.concept_id, "status": row.status}


@router.get("/imports/{job_id}/concepts/dag-payload")
async def confirmed_candidates_as_dag_payload(job_id: str, request: Request) -> dict:
    """确认候选 → 既有 DAG 发布端点（admin 全量快照）的入参格式桥。

    返回 {nodes, edges, note}——可直接喂 POST /api/v1/concept-dag/publish；
    边只含 prerequisite_of（正式 DAG 本期唯一消费的类型——其余类型
    候选留待 DAG 模型扩展，如实不混入）。
    """
    await require_user(request)
    owner = current_owner_id(request)
    job = await _owned_ready_job(request, job_id, owner)
    from app.db.orm import ConceptCandidateRow, ConceptEdgeCandidateRow, ResourceRow

    async with request.app.state.sessionmaker() as session:
        record = (
            
                await session.execute(
                    select(ResourceRow).where(
                        (ResourceRow.content_hash == job.sha256) & (ResourceRow.owner_id == owner)
                    )
                )
            
        ).scalar_one_or_none()
        if record is None:
            raise HTTPException(status_code=404, detail="候选未提取")
        confirmed = (
            
                await session.execute(
                    select(ConceptCandidateRow).where(
                        (ConceptCandidateRow.resource_id == record.id)
                        & (ConceptCandidateRow.status == "confirmed")
                    )
                )
            
        ).scalars().all()
        confirmed_edges = (
            
                await session.execute(
                    select(ConceptEdgeCandidateRow).where(
                        (ConceptEdgeCandidateRow.resource_id == record.id)
                        & (ConceptEdgeCandidateRow.status == "confirmed")
                    )
                )
            
        ).scalars().all()
        if not confirmed:
            raise HTTPException(status_code=409, detail="无已确认候选——先 confirm 再构造 DAG 入参")
    confirmed_ids = {row.concept_id for row in confirmed}
    nodes = [
        {
            "id": row.concept_id,
            "canonical_name": row.canonical_name,
            "aliases": [],
            "subject": row.subject,
            "description": row.description,
            "difficulty": row.difficulty,
            "parent_id": None,
            "evidence_ids": [],
        }
        for row in confirmed
    ]
    edges = [
        {
            "prerequisite_id": row.from_concept,
            "concept_id": row.to_concept,
        }
        for row in confirmed_edges
        if row.relation == "prerequisite_of"
        and row.from_concept in confirmed_ids
        and row.to_concept in confirmed_ids
    ]
    return {
        "nodes": nodes,
        "edges": edges,
        "note": f"从教材候选确认生成（{len(nodes)} 概念 / {len(edges)} 先修边）",
    }
