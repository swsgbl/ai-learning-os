"""Source record for the M1-01 Source Registry."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.domain.license import LicenseState, TrustTier


@dataclass(frozen=True, slots=True)
class SourceRecord:
    id: str
    name: str
    source_type: str
    homepage: str
    authority_score: int = 50
    terms_url: str | None = None
    robots_policy_snapshot: dict = field(default_factory=dict)
    rate_limit: dict = field(default_factory=dict)
    trust_tier: TrustTier = TrustTier.U
    license_state: LicenseState = LicenseState.UNKNOWN
    last_verified_at: datetime | None = None
    notes: str | None = None
