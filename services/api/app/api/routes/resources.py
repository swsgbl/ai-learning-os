"""M1-03 resource upload API: hash dedup + license guard."""
from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from app.domain.license import allows_full_text_storage
from app.domain.resource import ResourceRecord
from app.parsing.base import ParsedDocument, ParserError, ParserUnavailable
from app.parsing.normalize import normalize_blocks

router = APIRouter(prefix="/api/v1/resources", tags=["resources"])

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_CHUNK_SIZE = 1024 * 1024
MEDIA_TYPES = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "slide",
    ".mp4": "video",
    ".mp3": "audio",
    ".wav": "audio",
    ".csv": "dataset",
    ".json": "dataset",
}


def _store(request: Request):
    store = getattr(request.app.state, "objects", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Object storage unavailable")
    return store


def _repo(request: Request):
    repo = getattr(request.app.state, "resources", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Resource registry requires a database")
    return repo


async def _resolve_source(
    request: Request, source_id: str | None
) -> tuple[str, str | None]:
    """M1-02 联动：返回 (access_state, license_state) 快照。

    无 source_id = 用户私有文档（access_state=unknown，不经公共池）；
    有 source 时校验 allows_full_text_storage，并把来源 license 快照到资源上。
    """
    if not source_id:
        return "unknown", None
    sources = getattr(request.app.state, "sources", None)
    if sources is None:
        raise HTTPException(status_code=503, detail="Source registry requires a database")
    source = await sources.get(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="来源不存在")
    if not allows_full_text_storage(source.license_state):
        raise HTTPException(
            status_code=403,
            detail=f"来源 {source_id} license={source.license_state.value} 不允许保存正文",
        )
    return "public", source.license_state.value


class ResourceOut(BaseModel):
    id: str
    media_type: str
    title: str
    content_hash: str
    storage_key: str
    size_bytes: int
    content_type: str | None
    license_state: str
    parse_status: str
    parser_name: str | None = None
    parse_error: str | None = None
    deduplicated: bool = False


async def _read_upload(file: UploadFile) -> bytes:
    """分块读入并增量限额，避免超大请求体整体进内存。"""
    buf = bytearray()
    while chunk := await file.read(_CHUNK_SIZE):
        buf.extend(chunk)
        if len(buf) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="文件超过 50MB 上限")
    if not buf:
        raise HTTPException(status_code=422, detail="空文件")
    return bytes(buf)


def _media_type(filename: str | None, declared: str | None) -> str:
    if declared:
        if declared not in {"pdf", "docx", "slide", "video", "audio", "dataset"}:
            raise HTTPException(status_code=422, detail=f"不支持的 media_type: {declared}")
        return declared
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if filename and "." in filename else ""
    if suffix not in MEDIA_TYPES:
        raise HTTPException(status_code=422, detail=f"不支持的文件类型: {suffix or '未知'}")
    return MEDIA_TYPES[suffix]


@router.post("/upload", response_model=ResourceOut, status_code=201)
async def upload_resource(
    request: Request,
    file: UploadFile = File(...),
    title: str | None = Form(None),
    media_type: str | None = Form(None),
    source_id: str | None = Form(None),
) -> ResourceOut:
    """上传学习资源；同内容（SHA-256）重复上传幂等返回既有记录。"""
    access_state, license_state = await _resolve_source(request, source_id)
    data = await _read_upload(file)
    kind = _media_type(file.filename, media_type)
    content_hash = hashlib.sha256(data).hexdigest()
    storage_key = f"uploads/{content_hash[:2]}/{content_hash}"

    store = _store(request)
    if not store.exists(storage_key):
        store.put(storage_key, data, content_type=file.content_type)

    from app.domain.license import LicenseState

    record = ResourceRecord(
        id=f"res_{uuid.uuid4().hex}",
        source_id=source_id,
        media_type=kind,
        title=title or file.filename or "untitled",
        content_hash=content_hash,
        storage_key=storage_key,
        size_bytes=len(data),
        fetched_at=datetime.now(UTC),
        content_type=file.content_type,
        access_state=access_state,
        license_state=LicenseState(license_state) if license_state else LicenseState.UNKNOWN,
    )
    saved, deduplicated = await _repo(request).create(record)
    return ResourceOut(
        id=saved.id,
        media_type=saved.media_type,
        title=saved.title,
        content_hash=saved.content_hash,
        storage_key=saved.storage_key,
        size_bytes=saved.size_bytes,
        content_type=saved.content_type,
        license_state=saved.license_state.value,
        parse_status=saved.parse_status,
        parser_name=saved.parser_name,
        parse_error=saved.parse_error,
        deduplicated=deduplicated,
    )


@router.get("/{resource_id}", response_model=ResourceOut)
async def get_resource(resource_id: str, request: Request) -> ResourceOut:
    record = await _repo(request).get(resource_id)
    if not record:
        raise HTTPException(status_code=404, detail="资源不存在")
    return ResourceOut(
        id=record.id,
        media_type=record.media_type,
        title=record.title,
        content_hash=record.content_hash,
        storage_key=record.storage_key,
        size_bytes=record.size_bytes,
        content_type=record.content_type,
        license_state=record.license_state.value,
        parse_status=record.parse_status,
        parser_name=record.parser_name,
        parse_error=record.parse_error,
    )


def _normalized(doc: ParsedDocument) -> ParsedDocument:
    """M1-05：parser 输出统一过 layout normalize（类型归一/公式 LaTeX/页码继承/表格结构化）。"""
    doc.blocks = normalize_blocks(doc.blocks)
    return doc


class ParseOut(BaseModel):
    resource_id: str
    status: str
    parser_name: str | None
    block_count: int
    page_count: int
    table_count: int
    formula_count: int


@router.post("/{resource_id}/parse", response_model=ParseOut)
async def parse_resource(
    resource_id: str, request: Request, parser: str | None = None
) -> ParseOut:
    """同步解析（M1-07 前的桥接）；失败保留错误并允许 ?parser= 换一个重跑。"""
    repo = _repo(request)
    store = _store(request)
    registry = getattr(request.app.state, "parsers", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="Parser registry unavailable")
    record = await repo.get(resource_id)
    if not record:
        raise HTTPException(status_code=404, detail="资源不存在")
    data = store.get(record.storage_key)
    try:
        chosen = registry.select(record.media_type, prefer=parser)
        doc = chosen.parse(data, record.media_type)
        doc = _normalized(doc)
    except ParserError as cause:
        await repo.set_parse_status(resource_id, "failed", error=str(cause), parser_name=parser)
        raise HTTPException(status_code=422, detail=f"解析失败: {cause}") from cause
    except ParserUnavailable as cause:
        await repo.set_parse_status(resource_id, "failed", error=str(cause), parser_name=parser)
        raise HTTPException(status_code=503, detail=f"无可用 parser: {cause}") from cause
    from app.parsing.chunking import chunk_blocks

    chunks = chunk_blocks(doc.blocks)
    chunk_repo = getattr(request.app.state, "chunks", None)
    if chunk_repo is not None:
        await chunk_repo.replace_chunks(
            resource_id,
            chunks,
            parser_name=doc.parser_name,
            license_state=record.license_state.value,
            source_id=record.source_id,
            url=record.url,
        )
    metrics = {
        "block_count": len(doc.blocks),
        "normalized": True,
        "chunk_count": len(chunks),
        "page_count": doc.page_count,
        "table_count": doc.table_count,
        "formula_count": doc.formula_count,
    }
    if "ocr_confidence" in doc.metrics:
        metrics["ocr_confidence"] = doc.metrics["ocr_confidence"]
    await repo.set_parse_status(
        resource_id, "parsed", parser_name=doc.parser_name, metrics=metrics
    )
    return ParseOut(
        resource_id=resource_id,
        status="parsed",
        parser_name=doc.parser_name,
        **metrics,
    )


@router.get("/{resource_id}/chunks")
async def get_chunks(resource_id: str, request: Request) -> list[dict]:
    """每个 chunk 带页码/slide locator（M1-06 验收）。"""
    repo = _repo(request)
    chunk_repo = getattr(request.app.state, "chunks", None)
    if chunk_repo is None:
        raise HTTPException(status_code=503, detail="Chunk store unavailable")
    if not await repo.get(resource_id):
        raise HTTPException(status_code=404, detail="资源不存在")
    return await chunk_repo.list_chunks(resource_id)


@router.get("/{resource_id}/evidence")
async def get_evidence(resource_id: str, request: Request) -> list[dict]:
    """Evidence 记录 parser、hash、locator 与 license 快照（M1-06 验收）。"""
    repo = _repo(request)
    chunk_repo = getattr(request.app.state, "chunks", None)
    if chunk_repo is None:
        raise HTTPException(status_code=503, detail="Chunk store unavailable")
    if not await repo.get(resource_id):
        raise HTTPException(status_code=404, detail="资源不存在")
    return await chunk_repo.list_evidence(resource_id)


class ParseJobOut(BaseModel):
    id: str
    resource_id: str
    parser_name: str | None
    status: str
    attempts: int
    max_attempts: int
    last_error: str | None


def _job_repo(request: Request):
    repo = getattr(request.app.state, "parse_jobs", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Job queue unavailable")
    return repo


@router.post("/{resource_id}/jobs", response_model=ParseJobOut, status_code=202)
async def enqueue_parse(
    resource_id: str, request: Request, parser: str | None = None
) -> ParseJobOut:
    """异步解析入队（幂等）；同资源同 parser 重复入队返回既有任务。"""
    if not await _repo(request).get(resource_id):
        raise HTTPException(status_code=404, detail="资源不存在")
    job, _created = await _job_repo(request).enqueue(resource_id, parser)
    return ParseJobOut(**{**job, "last_error": job["last_error"]})


@router.get("/{resource_id}/jobs", response_model=list[ParseJobOut])
async def list_parse_jobs(resource_id: str, request: Request) -> list[ParseJobOut]:
    if not await _repo(request).get(resource_id):
        raise HTTPException(status_code=404, detail="资源不存在")
    return [ParseJobOut(**job) for job in await _job_repo(request).list_for_resource(resource_id)]


@router.get("/{resource_id}/quality")
async def get_quality(resource_id: str, request: Request) -> dict:
    """解析质量报告：页数/块数/公式/表格/OCR 置信度/异常页（M1-08）。"""
    repo = _repo(request)
    chunk_repo = getattr(request.app.state, "chunks", None)
    if chunk_repo is None:
        raise HTTPException(status_code=503, detail="Chunk store unavailable")
    record = await repo.get(resource_id)
    if not record:
        raise HTTPException(status_code=404, detail="资源不存在")
    from app.parsing.quality import build_quality_report

    chunks = await chunk_repo.list_chunks(resource_id)
    return build_quality_report(
        record.parse_metrics,
        chunks,
        parser_name=record.parser_name,
        parse_status=record.parse_status,
    )
