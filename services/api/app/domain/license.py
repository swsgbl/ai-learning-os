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


class ReuseAdmission(StrEnum):
    """公共复用池准入级别，语义对齐 08 号文档 License Registry 字段。"""

    FULL = "FULL"
    ATTRIBUTION_REQUIRED = "ATTRIBUTION_REQUIRED"
    NON_COMMERCIAL_ONLY = "NON_COMMERCIAL_ONLY"
    NOT_ADMISSIBLE = "NOT_ADMISSIBLE"


class StoragePolicy(StrEnum):
    """正文存储策略；ACCESS_CONTROLLED 及以上限制只允许元数据。"""

    FULL_TEXT = "FULL_TEXT"
    METADATA_ONLY = "METADATA_ONLY"


# 状态迁移矩阵（M1-02）：UNKNOWN 可认定任意状态；PROHIBITED 为吸收态；
# 已认定状态可回 UNKNOWN 重新进入人工审核；其余人工改判允许。
_TRANSITIONS: dict[LicenseState, frozenset[LicenseState]] = {
    LicenseState.UNKNOWN: frozenset(LicenseState) - {LicenseState.UNKNOWN},
    LicenseState.PROHIBITED: frozenset(),
}
for _state in set(LicenseState) - {LicenseState.UNKNOWN, LicenseState.PROHIBITED}:
    _TRANSITIONS[_state] = frozenset(LicenseState) - {_state}


# 公共复用池准入（10 号文档 §6.4：UNKNOWN 只允许出现在人工审核队列）
REUSE_ADMISSION: dict[LicenseState, ReuseAdmission] = {
    LicenseState.PUBLIC_ACCESS: ReuseAdmission.FULL,
    LicenseState.OPEN_LICENSE: ReuseAdmission.ATTRIBUTION_REQUIRED,
    LicenseState.RESTRICTED_NON_COMMERCIAL: ReuseAdmission.NON_COMMERCIAL_ONLY,
    LicenseState.UNKNOWN: ReuseAdmission.NOT_ADMISSIBLE,
    LicenseState.ACCESS_CONTROLLED: ReuseAdmission.NOT_ADMISSIBLE,
    LicenseState.ALL_RIGHTS_RESERVED: ReuseAdmission.NOT_ADMISSIBLE,
    LicenseState.PROHIBITED: ReuseAdmission.NOT_ADMISSIBLE,
}


# 正文存储策略（10 号文档 §6.3：ACCESS_CONTROLLED 只返回入口，不保存正文）
STORAGE_POLICY: dict[LicenseState, StoragePolicy] = {
    LicenseState.PUBLIC_ACCESS: StoragePolicy.FULL_TEXT,
    LicenseState.OPEN_LICENSE: StoragePolicy.FULL_TEXT,
    LicenseState.RESTRICTED_NON_COMMERCIAL: StoragePolicy.FULL_TEXT,
    LicenseState.ACCESS_CONTROLLED: StoragePolicy.METADATA_ONLY,
    LicenseState.UNKNOWN: StoragePolicy.METADATA_ONLY,
    LicenseState.ALL_RIGHTS_RESERVED: StoragePolicy.METADATA_ONLY,
    LicenseState.PROHIBITED: StoragePolicy.METADATA_ONLY,
}


def can_transition(current: LicenseState, target: LicenseState) -> bool:
    return target in _TRANSITIONS[current]


def assert_transition(current: LicenseState, target: LicenseState) -> None:
    if not can_transition(current, target):
        raise ValueError(f"非法 license 迁移: {current.value} -> {target.value}")


def is_admissible_for_reuse(state: LicenseState) -> bool:
    return REUSE_ADMISSION[state] != ReuseAdmission.NOT_ADMISSIBLE


def allows_full_text_storage(state: LicenseState) -> bool:
    return STORAGE_POLICY[state] == StoragePolicy.FULL_TEXT
