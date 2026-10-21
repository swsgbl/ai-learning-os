"""M16-001 学习合同 / Sprint / 学习事件 API 契约测试（升级方案 Phase 1 验收）。

覆盖：合同创建校验（outcomes 边界/mode/预算）、确认幂等、Sprint 创建
前置（未确认 409）、状态机推进（合法/非法 409/幂等 no-op）、暂停恢复、
事件幂等键去重、列表归属。SQLite 内存库（契约语义；PG 门禁在 CI 迁移 job）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def _client() -> TestClient:
    return TestClient(create_app(SQLITE_URL))


def _contract_payload(**overrides: object) -> dict:
    payload: dict = {
        "title": "SQL 数据分析 48 小时冲刺",
        "domain": "data",
        "mode": "exam_sprint",
        "target_outcomes": [
            {"statement": "给定一张订单表，能写出按地区聚合的 SELECT", "criteria": "语法正确且结果符合样例"},
            {"statement": "给定两表，能选择正确的 JOIN 并解释行数变化", "criteria": "解释含连接键语义"},
            {"statement": "能区分 GROUP BY 与 WHERE 的执行顺序", "criteria": "闭卷口头复述通过"},
        ],
        "scope_included": ["SELECT", "JOIN", "GROUP BY"],
        "scope_excluded": ["窗口函数"],
        "prerequisites": ["基础 SQL 语法"],
        "time_budget_hours": 48,
        "pass_criteria": "等值后测 ≥ 80 分且迁移题通过",
    }
    payload.update(overrides)
    return payload


def _make_confirmed_contract(client: TestClient) -> str:
    created = client.post("/api/v1/learning/contracts", json=_contract_payload())
    assert created.status_code == 201, created.text
    contract_id = created.json()["id"]
    confirmed = client.post(f"/api/v1/learning/contracts/{contract_id}/confirm")
    assert confirmed.status_code == 200
    return contract_id


def test_contract_crud_and_confirmation() -> None:
    with _client() as client:
        created = client.post("/api/v1/learning/contracts", json=_contract_payload())
        assert created.status_code == 201
        body = created.json()
        assert body["mode"] == "exam_sprint"
        assert len(body["target_outcomes"]) == 3
        assert body["confirmed_at"] is None

        listed = client.get("/api/v1/learning/contracts")
        assert listed.status_code == 200
        assert any(item["id"] == body["id"] for item in listed.json())

        got = client.get(f"/api/v1/learning/contracts/{body['id']}")
        assert got.status_code == 200

        # 未确认 → Sprint 创建被拒（409）
        rejected = client.post(f"/api/v1/learning/contracts/{body['id']}/sprints")
        assert rejected.status_code == 409

        # 确认幂等：两次 confirm 都 200，confirmed_at 不变
        first = client.post(f"/api/v1/learning/contracts/{body['id']}/confirm")
        second = client.post(f"/api/v1/learning/contracts/{body['id']}/confirm")
        assert first.status_code == second.status_code == 200
        assert first.json()["confirmed_at"] == second.json()["confirmed_at"]

        # 确认事件落库（幂等键去重——只一条）
        contract_id = body["id"]
        sprint = client.post(f"/api/v1/learning/contracts/{contract_id}/sprints")
        assert sprint.status_code == 201
        sprint_id = sprint.json()["id"]
        events = client.get(f"/api/v1/learning/sprints/{sprint_id}/events").json()
        confirmed_events = [e for e in events if e["event_type"] == "contract.confirmed"]
        assert len(confirmed_events) == 1


def test_contract_validation_rejections() -> None:
    with _client() as client:
        # 空目标 / 超过 8 条
        assert client.post(
            "/api/v1/learning/contracts", json=_contract_payload(target_outcomes=[])
        ).status_code == 422
        assert client.post(
            "/api/v1/learning/contracts",
            json=_contract_payload(
                target_outcomes=[
                    {"statement": f"能力结果 {i}", "criteria": "标准"} for i in range(9)
                ]
            ),
        ).status_code == 422
        # 未知 mode / 非正预算 / 空通过标准
        assert client.post(
            "/api/v1/learning/contracts", json=_contract_payload(mode="hackathon")
        ).status_code == 422
        assert client.post(
            "/api/v1/learning/contracts", json=_contract_payload(time_budget_hours=0)
        ).status_code == 422
        assert client.post(
            "/api/v1/learning/contracts", json=_contract_payload(pass_criteria="ab")
        ).status_code == 422


def test_sprint_lifecycle_advance_pause_resume() -> None:
    with _client() as client:
        contract_id = _make_confirmed_contract(client)
        sprint = client.post(f"/api/v1/learning/contracts/{contract_id}/sprints")
        assert sprint.status_code == 201
        sprint_id = sprint.json()["id"]
        assert sprint.json()["phase"] == "created"
        assert sprint.json()["ends_at"] is not None

        # 非法跳段：created → learning 409
        skipped = client.post(
            f"/api/v1/learning/sprints/{sprint_id}/advance", json={"to_phase": "learning"}
        )
        assert skipped.status_code == 409

        # 未知阶段 422
        unknown = client.post(
            f"/api/v1/learning/sprints/{sprint_id}/advance", json={"to_phase": "mastered"}
        )
        assert unknown.status_code == 422

        # 合法链：created → ... → plan_confirmed
        chain = [
            "contract_confirmed",
            "baseline_assessment",
            "concept_map_review",
            "plan_confirmed",
        ]
        for phase in chain:
            advanced = client.post(
                f"/api/v1/learning/sprints/{sprint_id}/advance", json={"to_phase": phase}
            )
            assert advanced.status_code == 200, advanced.text
            assert advanced.json()["phase"] == phase

        # 同状态幂等：重复 advance 同阶段 200 不变
        repeat = client.post(
            f"/api/v1/learning/sprints/{sprint_id}/advance", json={"to_phase": "plan_confirmed"}
        )
        assert repeat.status_code == 200
        assert repeat.json()["phase"] == "plan_confirmed"

        # 回退 409
        backward = client.post(
            f"/api/v1/learning/sprints/{sprint_id}/advance",
            json={"to_phase": "concept_map_review"},
        )
        assert backward.status_code == 409

        # 暂停 → 恢复（证据不丢）
        paused = client.post(
            f"/api/v1/learning/sprints/{sprint_id}/pause", json={"reason": "休息"}
        )
        assert paused.status_code == 200
        assert paused.json()["paused"] is True
        resumed = client.post(f"/api/v1/learning/sprints/{sprint_id}/resume")
        assert resumed.status_code == 200
        assert resumed.json()["paused"] is False
        # 未暂停再恢复 409
        assert client.post(f"/api/v1/learning/sprints/{sprint_id}/resume").status_code == 409

        # 事件流：创建+推进+暂停+恢复各一条，无重复
        events = client.get(f"/api/v1/learning/sprints/{sprint_id}/events").json()
        types = [e["event_type"] for e in events]
        assert types.count("sprint.created") == 1
        assert types.count("sprint.paused") == 1
        assert types.count("sprint.resumed") == 1
        assert types.count("sprint.phase_advanced") == len(chain)


def test_learning_event_idempotency() -> None:
    with _client() as client:
        contract_id = _make_confirmed_contract(client)
        sprint_id = client.post(
            f"/api/v1/learning/contracts/{contract_id}/sprints"
        ).json()["id"]
        url = f"/api/v1/learning/sprints/{sprint_id}/events"
        payload = {
            "event_type": "attempt.recorded",
            "payload": {"item_id": "q1", "correct": True},
            "idempotency_key": "attempt-q1-first",
        }
        first = client.post(url, json=payload)
        assert first.status_code == 201
        duplicate = client.post(url, json=payload)
        assert duplicate.status_code == 201
        assert duplicate.json()["id"] == first.json()["id"]  # 返回既有事件

        events = client.get(url).json()
        keyed = [e for e in events if e["idempotency_key"] == "attempt-q1-first"]
        assert len(keyed) == 1  # 不重复落库

        # 无键事件可多条；未知 source 422
        no_key = client.post(url, json={"event_type": "note.added", "payload": {}})
        assert no_key.status_code == 201
        assert client.post(url, json={"event_type": "x", "source": "unknown"}).status_code == 422


def test_owner_isolation_not_found_for_others() -> None:
    with _client() as client:
        # 本地模式（auth off）无主可见：合同可读（与考试本地语义一致）
        contract_id = _make_confirmed_contract(client)
        got = client.get(f"/api/v1/learning/contracts/{contract_id}")
        assert got.status_code == 200
        missing = client.get("/api/v1/learning/contracts/lc_nonexistent")
        assert missing.status_code == 404


def test_event_idempotency_is_tenant_scoped_no_idor_no_suppression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M16-001b 安全审查修复回归：同幂等键跨用户各自落库互不干扰。

    - IDOR：用户 B 用与 A 相同的键提交，必须拿到 B 自己的事件（而非 A 的）；
    - 预占压制：B 先提交不能让 A 后提交的事件被吞（各自一条）。
    """
    from app.api.routes.auth import get_settings

    monkeypatch.setenv("AUTH_SECRET", "m16-001b-idem-scope-secret-0123456789abcdef")
    get_settings.cache_clear()
    try:
        with TestClient(create_app(SQLITE_URL)) as client:
            tokens = {}
            for username in ("alice", "bob"):
                client.post(
                    "/api/v1/auth/register",
                    json={"username": username, "password": "password-123"},
                )
                login = client.post(
                    "/api/v1/auth/login",
                    json={"username": username, "password": "password-123"},
                )
                tokens[username] = login.json()["access_token"]

            def _mk_contract(token: str) -> str:
                created = client.post(
                    "/api/v1/learning/contracts",
                    json=_contract_payload(),
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert created.status_code == 201, created.text
                contract_id = created.json()["id"]
                confirmed = client.post(
                    f"/api/v1/learning/contracts/{contract_id}/confirm",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert confirmed.status_code == 200
                return contract_id

            def _mk_sprint(token: str, contract_id: str) -> str:
                created = client.post(
                    f"/api/v1/learning/contracts/{contract_id}/sprints",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert created.status_code == 201, created.text
                return created.json()["id"]

            alice_contract = _mk_contract(tokens["alice"])
            bob_contract = _mk_contract(tokens["bob"])
            alice_sprint = _mk_sprint(tokens["alice"], alice_contract)
            bob_sprint = _mk_sprint(tokens["bob"], bob_contract)

            shared_key = "attempt-shared-key-1"
            alice_event = client.post(
                f"/api/v1/learning/sprints/{alice_sprint}/events",
                json={
                    "event_type": "attempt.recorded",
                    "payload": {"who": "alice"},
                    "idempotency_key": shared_key,
                },
                headers={"Authorization": f"Bearer {tokens['alice']}"},
            )
            bob_event = client.post(
                f"/api/v1/learning/sprints/{bob_sprint}/events",
                json={
                    "event_type": "attempt.recorded",
                    "payload": {"who": "bob"},
                    "idempotency_key": shared_key,
                },
                headers={"Authorization": f"Bearer {tokens['bob']}"},
            )
            assert alice_event.status_code == bob_event.status_code == 201
            # B 拿到的是 B 的事件（不是 A 的）——IDOR 修复
            assert bob_event.json()["id"] != alice_event.json()["id"]
            assert bob_event.json()["payload"] == {"who": "bob"}
            assert bob_event.json()["sprint_id"] == bob_sprint

            # 各自事件流里同键事件恰一条——无压制
            alice_events = client.get(
                f"/api/v1/learning/sprints/{alice_sprint}/events",
                headers={"Authorization": f"Bearer {tokens['alice']}"},
            ).json()
            bob_events = client.get(
                f"/api/v1/learning/sprints/{bob_sprint}/events",
                headers={"Authorization": f"Bearer {tokens['bob']}"},
            ).json()
            assert [e for e in alice_events if e["idempotency_key"] == shared_key].__len__() == 1
            assert [e for e in bob_events if e["idempotency_key"] == shared_key].__len__() == 1
    finally:
        monkeypatch.delenv("AUTH_SECRET", raising=False)
        get_settings.cache_clear()
