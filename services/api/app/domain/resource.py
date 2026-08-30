"""M1-03 resource domain record (04 号文档 §2.4 字段子集，上传场景)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.domain.license import LicenseState


@dataclass(frozen=True)
class ResourceRecord:
    id: str
    media_type: str
    title: str
    content_hash: str
    storage_key: str
    size_bytes: int
    fetched_at: datetime
    source_id: str | None = None
    url: str | None = None
    language: str | None = None
    access_state: str = "unknown"
    license_state: LicenseState = LicenseState.UNKNOWN
    content_type: str | None = None
    parse_status: str = "pending"
    parser_name: str | None = None
    parse_metrics: dict | None = None
    parse_error: str | None = None
