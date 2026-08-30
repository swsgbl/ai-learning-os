from __future__ import annotations

from enum import StrEnum


class LicenseState(StrEnum):
    UNKNOWN = "UNKNOWN"
    PUBLIC_ACCESS = "PUBLIC_ACCESS"
    OPEN_LICENSE = "OPEN_LICENSE"
    ACCESS_CONTROLLED = "ACCESS_CONTROLLED"
    ALL_RIGHTS_RESERVED = "ALL_RIGHTS_RESERVED"
    RESTRICTED_NON_COMMERCIAL = "RESTRICTED_NON_COMMERCIAL"
    PROHIBITED = "PROHIBITED"


class TrustTier(StrEnum):
    S = "S"
    A = "A"
    B = "B"
    C = "C"
    U = "U"
