"""Public course source seeds for the Source Registry (M1-01).

All seeds start at license_state=UNKNOWN: clues only until verified (M1-02).
"""
from __future__ import annotations

from app.domain.license import LicenseState, TrustTier
from app.domain.source import SourceRecord

DEFAULT_RATE_LIMIT = {"requests_per_minute": 10, "max_concurrent": 1, "cooldown_seconds": 6}


def seed_sources() -> list[SourceRecord]:
    common = {"robots_policy_snapshot": {}, "last_verified_at": None}
    return [
        SourceRecord(
            id="src_mit_ocw",
            name="MIT OpenCourseWare",
            source_type="oer",
            homepage="https://ocw.mit.edu",
            authority_score=98,
            terms_url="https://ocw.mit.edu/terms/",
            rate_limit=dict(DEFAULT_RATE_LIMIT),
            trust_tier=TrustTier.S,
            license_state=LicenseState.UNKNOWN,
            notes="CC BY-NC-SA per course; verify each course before import",
            **common,
        ),
        SourceRecord(
            id="src_mit_ocw_6_006",
            name="MIT OCW 6.006 Introduction to Algorithms",
            source_type="oer",
            homepage="https://ocw.mit.edu/courses/6-006-introduction-to-algorithms-spring-2020/",
            authority_score=97,
            terms_url="https://ocw.mit.edu/terms/",
            rate_limit=dict(DEFAULT_RATE_LIMIT),
            trust_tier=TrustTier.S,
            license_state=LicenseState.UNKNOWN,
            notes="Public lecture notes and assignments",
            **common,
        ),
        SourceRecord(
            id="src_cs61a",
            name="UC Berkeley CS 61A",
            source_type="university",
            homepage="https://cs61a.org",
            authority_score=92,
            rate_limit=dict(DEFAULT_RATE_LIMIT),
            trust_tier=TrustTier.A,
            license_state=LicenseState.UNKNOWN,
            notes="Term-specific course site; human review before import",
            **common,
        ),
        SourceRecord(
            id="src_hku_cs",
            name="HKU Department of Computer Science",
            source_type="university",
            homepage="https://www.cs.hku.hk",
            authority_score=90,
            rate_limit=dict(DEFAULT_RATE_LIMIT),
            trust_tier=TrustTier.A,
            license_state=LicenseState.UNKNOWN,
            notes="Course pages public; past papers usually access-controlled",
            **common,
        ),
        SourceRecord(
            id="src_arxiv",
            name="arXiv",
            source_type="oer",
            homepage="https://arxiv.org",
            authority_score=95,
            terms_url="https://info.arxiv.org/help/license/",
            rate_limit={"requests_per_minute": 5, "max_concurrent": 1, "cooldown_seconds": 12},
            trust_tier=TrustTier.S,
            license_state=LicenseState.UNKNOWN,
            notes="Per-paper license varies; check before any reuse",
            **common,
        ),
        SourceRecord(
            id="src_github",
            name="GitHub",
            source_type="github",
            homepage="https://github.com",
            authority_score=60,
            rate_limit={"requests_per_minute": 20, "max_concurrent": 2, "cooldown_seconds": 3},
            trust_tier=TrustTier.B,
            license_state=LicenseState.UNKNOWN,
            notes="Per-repository license; community tier needs human review",
            **common,
        ),
    ]
