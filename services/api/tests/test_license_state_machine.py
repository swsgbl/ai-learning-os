"""M1-02 License State Machine：迁移矩阵、复用池准入、存储策略与 API 行为。"""
from __future__ import annotations

from app.domain.license import (
    REUSE_ADMISSION,
    STORAGE_POLICY,
    LicenseState,
    ReuseAdmission,
    StoragePolicy,
    allows_full_text_storage,
    can_transition,
    is_admissible_for_reuse,
)

ALL_STATES = list(LicenseState)
# 可迁出状态：UNKNOWN 与吸收态 PROHIBITIVE 之外
MIGRATABLE = [s for s in ALL_STATES if s not in (LicenseState.UNKNOWN, LicenseState.PROHIBITED)]


def test_unknown_transitions_to_every_state():
    for target in ALL_STATES:
        if target is LicenseState.UNKNOWN:
            continue
        assert can_transition(LicenseState.UNKNOWN, target)


def test_prohibited_is_absorbing():
    for target in ALL_STATES:
        assert not can_transition(LicenseState.PROHIBITED, target)


def test_known_states_can_return_to_unknown():
    for state in MIGRATABLE:
        assert can_transition(state, LicenseState.UNKNOWN), state


def test_known_states_can_downgrade_to_prohibited():
    for state in MIGRATABLE:
        assert can_transition(state, LicenseState.PROHIBITED), state


def test_known_states_can_be_rejudged():
    for current in MIGRATABLE:
        for target in MIGRATABLE:
            if current is not target:
                assert can_transition(current, target), (current, target)


def test_unknown_never_enters_reuse_pool():
    """backlog M1-02：UNKNOWN 不能进入公共复用池。"""
    assert not is_admissible_for_reuse(LicenseState.UNKNOWN)
    assert REUSE_ADMISSION[LicenseState.UNKNOWN] is ReuseAdmission.NOT_ADMISSIBLE


def test_reuse_pool_admission_levels():
    assert is_admissible_for_reuse(LicenseState.PUBLIC_ACCESS)
    assert is_admissible_for_reuse(LicenseState.OPEN_LICENSE)
    assert is_admissible_for_reuse(LicenseState.RESTRICTED_NON_COMMERCIAL)
    for blocked in (
        LicenseState.ACCESS_CONTROLLED,
        LicenseState.ALL_RIGHTS_RESERVED,
        LicenseState.PROHIBITED,
    ):
        assert not is_admissible_for_reuse(blocked), blocked


def test_access_controlled_never_stores_full_text():
    """backlog M1-02：ACCESS_CONTROLLED 不保存正文（只存元数据/入口）。"""
    assert STORAGE_POLICY[LicenseState.ACCESS_CONTROLLED] is StoragePolicy.METADATA_ONLY
    assert not allows_full_text_storage(LicenseState.ACCESS_CONTROLLED)
    assert not allows_full_text_storage(LicenseState.UNKNOWN)
    assert allows_full_text_storage(LicenseState.OPEN_LICENSE)


def test_every_state_has_policy():
    assert set(REUSE_ADMISSION) == set(ALL_STATES)
    assert set(STORAGE_POLICY) == set(ALL_STATES)


# ---------- API 层（SQLite 内存库全链路） ----------
from fastapi.testclient import TestClient

from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def test_api_recognize_and_reuse_pool():
    """UNKNOWN 来源认定 OPEN_LICENSE 后进入复用池。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        created = client.post("/api/v1/sources", json={
            "id": "src_test_a", "name": "Test A", "source_type": "oer",
            "homepage": "https://example.org/a",
        }).json()
        assert created["license_state"] == "UNKNOWN"

        pool_before = client.get("/api/v1/sources", params={"reuse_pool": "true"}).json()
        assert all(item["id"] != "src_test_a" for item in pool_before)
        assert all(item["license_state"] != "UNKNOWN" for item in pool_before)

        updated = client.post("/api/v1/sources/src_test_a/license",
                              json={"state": "OPEN_LICENSE"}).json()
        assert updated["license_state"] == "OPEN_LICENSE"
        assert updated["last_verified_at"] is not None

        pool_after = client.get("/api/v1/sources", params={"reuse_pool": "true"}).json()
        assert any(item["id"] == "src_test_a" for item in pool_after)


def test_api_prohibited_is_absorbing_via_409():
    with TestClient(create_app(SQLITE_URL)) as client:
        client.post("/api/v1/sources", json={
            "id": "src_test_b", "name": "Test B", "source_type": "community",
            "homepage": "https://example.org/b",
        })
        assert client.post("/api/v1/sources/src_test_b/license",
                           json={"state": "PROHIBITED"}).status_code == 200
        for target in ("UNKNOWN", "OPEN_LICENSE", "PUBLIC_ACCESS"):
            resp = client.post("/api/v1/sources/src_test_b/license", json={"state": target})
            assert resp.status_code == 409, target


def test_api_unknown_source_404():
    with TestClient(create_app(SQLITE_URL)) as client:
        resp = client.post("/api/v1/sources/src_missing/license",
                           json={"state": "OPEN_LICENSE"})
        assert resp.status_code == 404
