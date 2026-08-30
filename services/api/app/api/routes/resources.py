"""M1-03 resource upload API: hash dedup + license guard."""
from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from app.domain.license import allows_full_text_storage
from app.domain.resource import ResourceRecord

router = APIRouter(prefix="/api/v1/resources", tags=["resources"])

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
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


async def _enforce_license(request: Request, source_id: str | None) -> None:
    """M1-02 联动：来源不允许存正文（UNKNOWN/ACCESS_CONTROLLED/R 级/PROHIBITED）时拒绝上传。"""
    if not source_id:
        return
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
    deduplicated: bool = False


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 50MB 上限")
    if not data:
        raise HTTPException(status_code=422, detail="空文件")
    return data


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
    await _enforce_license(request, source_id)
    data = await _read_upload(file)
    kind = _media_type(file.filename, media_type)
    content_hash = hashlib.sha256(data).hexdigest()
    storage_key = f"uploads/{content_hash[:2]}/{content_hash}"

    store = _store(request)
    if not store.exists(storage_key):
        store.put(storage_key, data, content_type=file.content_type)

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
    )
