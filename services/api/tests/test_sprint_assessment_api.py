"""M16-004 Sprint 前后测编排 API 契约测试（升级方案 Phase 4 验收）。

覆盖：蓝图构建（等值不重叠/覆盖缺口如实）；前测下发答案隔离（answer/
explanation 绝不出现在响应）；阶段前置 409；提交判分三态→attempts 落库
→自动推进；重复提交 409（不覆盖证据）；后测独立题集；报告（前后增益/
掌握快照/未完成项诚实汇总）。
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.db.orm import PaperRow, QuestionRow
from app.main import create_app


@pytest.fixture()
def sqlite_url(tmp_path):
    return f"sqlite+aiosqlite:///{tmp_path / 'learning.db'}"

#: 测试题池：2 概念 × 5 题（客观 mcq，answer 服务端权威）
POOL = {
    "c_select": [("SELECT 基础", i) for i in range(5)],
    "c_join": [("JOIN 连接", i) for i in range(5)],
}


def _seed_pool(client: TestClient, sqlite_url: str) -> None:
    """独立 engine 写文件库（与 TestClient 引擎共享同一 SQLite 文件）。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.base import Base

    async def _seed() -> None:
        engine = create_async_engine(sqlite_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            paper = PaperRow(
                id="pap_test_pool",
                title="蓝图测试卷",
                subtitle="",
                source="test",
                subject="sql",
                difficulty="medium",
                duration_minutes=10,
                tags=[],
                license="test",
            )
            session.add(paper)
            seq = 0
            for concept, items in POOL.items():
                for stem, index in items:
                    session.add(
                        QuestionRow(
                            id=f"q_{concept}_{index}",
                            paper_id=paper.id,
                            question_type="mcq",
                            stem=f"{stem}-{index}",
                            options=[{"key": "A", "text": "甲"}, {"key": "B", "text": "乙"}],
                            answer="A",  # 服务端权威；绝不下发
                            explanation="解释（机密）",
                            knowledge=[concept],
                            difficulty=1 + index % 3,
                            sort_order=seq,
                            score=1.0,
                        )
                    )
                    seq += 1
            await session.commit()
        await engine.dispose()

    asyncio.run(_seed())


def _contract_payload() -> dict:
    return {
        "title": "SQL 冲刺",
        "mode": "exam_sprint",
        "target_outcomes": [
            {"statement": "能写出基础 SELECT", "criteria": "样例通过"},
            {"statement": "能选择正确 JOIN", "criteria": "解释行数变化"},
        ],
        "time_budget_hours": 48,
        "pass_criteria": "后测≥80",
    }


def _make_sprint_ready_for_baseline(client: TestClient) -> str:
    created = client.post("/api/v1/learning/contracts", json=_contract_payload())
    assert created.status_code == 201
    contract_id = created.json()["id"]
    assert client.post(f"/api/v1/learning/contracts/{contract_id}/confirm").status_code == 200
    blueprint = client.post(
        f"/api/v1/learning/contracts/{contract_id}/blueprint/build",
        json={"concepts": ["c_select", "c_join"]},
    )
    assert blueprint.status_code == 201, blueprint.text
    assert blueprint.json()["complete"] is True  # 2 概念 ×5 题 → 每概念 4 题够
    sprint = client.post(f"/api/v1/learning/contracts/{contract_id}/sprints")
    assert sprint.status_code == 201
    sprint_id = sprint.json()["id"]
    for phase in ("contract_confirmed", "baseline_assessment"):
        advanced = client.post(
            f"/api/v1/learning/sprints/{sprint_id}/advance", json={"to_phase": phase}
        )
        assert advanced.status_code == 200
    return sprint_id


def test_blueprint_equivalent_no_overlap(sqlite_url) -> None:
    with TestClient(create_app(sqlite_url)) as client:
        _seed_pool(client, sqlite_url)
        created = client.post("/api/v1/learning/contracts", json=_contract_payload())
        contract_id = created.json()["id"]
        client.post(f"/api/v1/learning/contracts/{contract_id}/confirm")
        bp = client.post(
        f"/api/v1/learning/contracts/{contract_id}/blueprint/build",
        json={"concepts": ["c_select", "c_join"]},
    )
        assert bp.status_code == 201
        body = bp.json()
        assert body["complete"] is True
        for plan in body["plans"]:
            assert set(plan["pretest_item_ids"]).isdisjoint(plan["posttest_item_ids"])
            assert len(plan["pretest_item_ids"]) == 2
            assert len(plan["posttest_item_ids"]) == 2
        # 重复构建幂等（重建不残留旧行——GET 恰一套）
        rebuilt = client.post(
        f"/api/v1/learning/contracts/{contract_id}/blueprint/build",
        json={"concepts": ["c_select", "c_join"]},
    )
        assert rebuilt.status_code == 201
        got = client.get(f"/api/v1/learning/contracts/{contract_id}/blueprint")
        assert len(got.json()["plans"]) == 2


def test_blueprint_insufficient_pool_reports_partial(sqlite_url) -> None:
    with TestClient(create_app(sqlite_url)) as client:
        _seed_pool(client, sqlite_url)
        created = client.post("/api/v1/learning/contracts", json=_contract_payload())
        contract_id = created.json()["id"]
        client.post(f"/api/v1/learning/contracts/{contract_id}/confirm")
        bp = client.post(
            f"/api/v1/learning/contracts/{contract_id}/blueprint/build",
            json={"concepts": ["c_thin"]},  # 概念无题——空 plans → 409 如实
        )
        assert bp.status_code == 409


def test_baseline_flow_answer_isolation_and_grading(sqlite_url) -> None:
    with TestClient(create_app(sqlite_url)) as client:
        _seed_pool(client, sqlite_url)
        sprint_id = _make_sprint_ready_for_baseline(client)

        # 阶段前置：posttest 在 baseline 阶段 409
        wrong_phase = client.get(
            f"/api/v1/learning/sprints/{sprint_id}/assessment", params={"phase": "posttest"}
        )
        assert wrong_phase.status_code == 409

        assessment = client.get(
            f"/api/v1/learning/sprints/{sprint_id}/assessment", params={"phase": "baseline"}
        )
        assert assessment.status_code == 200
        items = assessment.json()["items"]
        assert len(items) == 4  # 2 概念 × 2 前测题
        # 答案隔离不变式：响应不含 answer/explanation
        raw = assessment.text
        assert "answer" not in raw and "explanation" not in raw

        # 全对提交（answer=A）
        responses = [{"item_id": item["item_id"], "response": "A"} for item in items]
        submitted = client.post(
            f"/api/v1/learning/sprints/{sprint_id}/assessment/baseline/submit",
            json={"responses": responses},
        )
        assert submitted.status_code == 200, submitted.text
        result = submitted.json()
        assert result["score"] == 100.0
        assert result["correct_count"] == 4
        assert result["sprint_phase"] == "concept_map_review"  # 自动推进

        # 重复提交 409（证据不可覆盖）
        dup = client.post(
            f"/api/v1/learning/sprints/{sprint_id}/assessment/baseline/submit",
            json={"responses": responses},
        )
        assert dup.status_code == 409

        # attempts 已落库（掌握度证据源）
        attempts = client.get("/api/v1/learning/attempts")
        assert len(attempts.json()) == 4


def test_posttest_uses_disjoint_items_and_report_shows_gain(sqlite_url) -> None:
    with TestClient(create_app(sqlite_url)) as client:
        _seed_pool(client, sqlite_url)
        sprint_id = _make_sprint_ready_for_baseline(client)
        baseline_items = client.get(
            f"/api/v1/learning/sprints/{sprint_id}/assessment", params={"phase": "baseline"}
        ).json()["items"]
        baseline_ids = {item["item_id"] for item in baseline_items}
        # 前测全错（增益对照）
        client.post(
            f"/api/v1/learning/sprints/{sprint_id}/assessment/baseline/submit",
            json={"responses": [{"item_id": i, "response": "B"} for i in baseline_ids]},
        )

        # 推进到 posttest
        for phase in ("concept_map_review", "plan_confirmed", "learning",
                      "targeted_practice", "transfer_check", "posttest"):
            advanced = client.post(
                f"/api/v1/learning/sprints/{sprint_id}/advance", json={"to_phase": phase}
            )
            assert advanced.status_code == 200, f"{phase}: {advanced.text}"

        posttest = client.get(
            f"/api/v1/learning/sprints/{sprint_id}/assessment", params={"phase": "posttest"}
        )
        assert posttest.status_code == 200
        post_ids = {item["item_id"] for item in posttest.json()["items"]}
        # 等值验收：后测不复用前测原题
        assert post_ids.isdisjoint(baseline_ids)

        # 后测全对 → 增益 +100
        submitted = client.post(
            f"/api/v1/learning/sprints/{sprint_id}/assessment/posttest/submit",
            json={"responses": [{"item_id": i, "response": "A"} for i in post_ids]},
        )
        assert submitted.status_code == 200
        assert submitted.json()["score"] == 100.0

        report = client.get(f"/api/v1/learning/sprints/{sprint_id}/report")
        assert report.status_code == 200
        body = report.json()
        assert body["baseline"]["score"] == 0.0
        assert body["posttest"]["score"] == 100.0
        assert body["gain"] == 100.0
        # 掌握快照来自 attempts 重算（前测错+后测对 → 中间态）
        assert len(body["mastery_snapshot"]) == 2
        # 未完成项只含 follow_up 之外的后续（report 已到 → 只 follow_up 被排除）
        assert "posttest" not in body["incomplete"]
        assert body["incomplete"] == []


def test_report_rejects_when_not_ready(sqlite_url) -> None:
    with TestClient(create_app(sqlite_url)) as client:
        _seed_pool(client, sqlite_url)
        sprint_id = _make_sprint_ready_for_baseline(client)
        # 停在 baseline_assessment 未提交 → 报告 409（诚实：不虚构结果）
        report = client.get(f"/api/v1/learning/sprints/{sprint_id}/report")
        assert report.status_code == 409


def test_follow_up_review_lifecycle(sqlite_url, monkeypatch) -> None:
    """M16-005 复习闭环（模板调度器）：follow_up 建队列 → present → attempt
    判分 → completed + 下一轮 scheduled；复习作答落 delayed attempt。

    M17-003 起部署缺省调度器为 fsrs（首复习间隔语义不同）——本测试
    显式锁 intervals-mvp 保留模板档语义回归；FSRS 见专属测试。
    """
    from app.core.config import get_settings

    monkeypatch.setenv("REVIEW_ALGORITHM", "intervals-mvp")
    get_settings.cache_clear()
    try:
        _run_lifecycle(sqlite_url)
    finally:
        monkeypatch.delenv("REVIEW_ALGORITHM", raising=False)
        get_settings.cache_clear()


def _run_lifecycle(sqlite_url: str) -> None:
    with TestClient(create_app(sqlite_url)) as client:
        _seed_pool(client, sqlite_url)
        sprint_id = _make_sprint_ready_for_baseline(client)
        # 走完全程到 posttest 提交
        baseline = client.get(
            f"/api/v1/learning/sprints/{sprint_id}/assessment", params={"phase": "baseline"}
        ).json()["items"]
        client.post(
            f"/api/v1/learning/sprints/{sprint_id}/assessment/baseline/submit",
            json={"responses": [{"item_id": i["item_id"], "response": "A"} for i in baseline]},
        )
        for phase in ("concept_map_review", "plan_confirmed", "learning",
                      "targeted_practice", "transfer_check", "posttest"):
            client.post(f"/api/v1/learning/sprints/{sprint_id}/advance", json={"to_phase": phase})
        posttest = client.get(
            f"/api/v1/learning/sprints/{sprint_id}/assessment", params={"phase": "posttest"}
        ).json()["items"]
        client.post(
            f"/api/v1/learning/sprints/{sprint_id}/assessment/posttest/submit",
            json={"responses": [{"item_id": i["item_id"], "response": "A"} for i in posttest]},
        )
        assert client.get(f"/api/v1/learning/sprints/{sprint_id}/report").status_code == 200

        # report 阶段建复测队列
        queue = client.post(f"/api/v1/learning/sprints/{sprint_id}/follow-up/schedule")
        assert queue.status_code == 201, queue.text
        reviews = queue.json()
        assert len(reviews) == 2  # 蓝图 2 概念
        assert all(r["status"] == "scheduled" for r in reviews)

        # scheduled 直接 attempt 409（必须先 presented）
        direct = client.post(
            f"/api/v1/learning/review-queue/{reviews[0]['id']}/attempt",
            json={"item_id": "q_c_select_4", "response": "A"},
        )
        assert direct.status_code == 409

        presented = client.post(f"/api/v1/learning/review-queue/{reviews[0]['id']}/present")
        assert presented.status_code == 200
        assert presented.json()["status"] == "presented"

        first_concept = reviews[0]["concept_id"]
        other_concept = "c_join" if first_concept == "c_select" else "c_select"
        # 概念不匹配的题 422
        mismatch = client.post(
            f"/api/v1/learning/review-queue/{reviews[0]['id']}/attempt",
            json={"item_id": f"q_{other_concept}_0", "response": "A"},
        )
        assert mismatch.status_code == 422

        # 正确复习作答 → completed + delayed attempt 落库 + 下一轮 scheduled
        attempted = client.post(
            f"/api/v1/learning/review-queue/{reviews[0]['id']}/attempt",
            json={"item_id": f"q_{first_concept}_4", "response": "A", "confidence": 4},
        )
        assert attempted.status_code == 200, attempted.text
        assert attempted.json()["status"] == "completed"
        assert attempted.json()["interval_days"] > 1  # 高信心独立对 → 增长

        attempts = client.get("/api/v1/learning/attempts").json()
        review_attempts = [a for a in attempts if a["context"].endswith(":review")]
        assert len(review_attempts) == 1
        assert review_attempts[0]["delayed"] is True

        # 未复习概念的 scheduled 仍在队列；已复习概念下一轮因间隔增长（>1 天）
        # 移出 24h 窗口——这正是间隔复习语义（不是每天重复同概念）
        queue_now = client.get("/api/v1/learning/review-queue").json()
        queue_concepts = {r["concept_id"] for r in queue_now}
        other = "c_join" if first_concept == "c_select" else "c_select"
        assert other in queue_concepts  # 未复习概念仍到期可见
        assert first_concept not in queue_concepts  # 复习过的按新间隔排期


def test_follow_up_schedule_idempotent(sqlite_url) -> None:
    """M16-005b：重复 schedule 返回既有活跃队列（不翻倍）。"""
    with TestClient(create_app(sqlite_url)) as client:
        _seed_pool(client, sqlite_url)
        sprint_id = _make_sprint_ready_for_baseline(client)
        baseline = client.get(
            f"/api/v1/learning/sprints/{sprint_id}/assessment", params={"phase": "baseline"}
        ).json()["items"]
        client.post(
            f"/api/v1/learning/sprints/{sprint_id}/assessment/baseline/submit",
            json={"responses": [{"item_id": i["item_id"], "response": "A"} for i in baseline]},
        )
        for phase in ("concept_map_review", "plan_confirmed", "learning",
                      "targeted_practice", "transfer_check", "posttest"):
            client.post(f"/api/v1/learning/sprints/{sprint_id}/advance", json={"to_phase": phase})
        posttest = client.get(
            f"/api/v1/learning/sprints/{sprint_id}/assessment", params={"phase": "posttest"}
        ).json()["items"]
        client.post(
            f"/api/v1/learning/sprints/{sprint_id}/assessment/posttest/submit",
            json={"responses": [{"item_id": i["item_id"], "response": "A"} for i in posttest]},
        )
        first = client.post(f"/api/v1/learning/sprints/{sprint_id}/follow-up/schedule")
        assert first.status_code == 201
        second = client.post(f"/api/v1/learning/sprints/{sprint_id}/follow-up/schedule")
        assert second.status_code == 201
        assert {r["id"] for r in second.json()} == {r["id"] for r in first.json()}
        assert len(second.json()) == 2  # 仍是 2 条，未翻倍


def test_generate_items_then_blueprint_usable(sqlite_url, monkeypatch) -> None:
    """M16-007 内容层闭环：生成客观题入题池卷 → 蓝图可构建 → 前测有题。

    LLM 打桩（不依赖真实网关——live 供应商验证属运维 smoke）。
    """
    import json as jsonlib

    import app.domain.item_generator as ig

    class _StubGateway:
        def __init__(self, **kwargs: object) -> None:
            pass

        def chat(self, messages: object, temperature: float = 0.0, max_tokens: int = 1024) -> str:
            items = []
            for concept in ("c_select", "c_join"):
                for index in range(4):  # 每概念 4 题（蓝图需 4）
                    items.append(
                        {
                            "concept": concept,
                            "stem": f"{concept} 第 {index} 题题干",
                            "options": ["甲", "乙", "丙", "丁"],
                            "answer_index": index % 4,
                            "difficulty": 2,
                            "why": "解析",
                        }
                    )
            return jsonlib.dumps({"items": items}, ensure_ascii=False)

    monkeypatch.setattr(ig, "LlmGateway", _StubGateway)
    # 全局 LLM 槽位配置（网关已打桩——这里只是让配置检查通过）
    from app.core.config import get_settings

    monkeypatch.setenv("LLM_ENDPOINT", "http://stub.local/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-stub-0123456789")
    monkeypatch.setenv("LLM_MODEL", "stub-model")
    get_settings.cache_clear()
    try:
        _run_generate_items_flow(sqlite_url)
    finally:
        monkeypatch.delenv("LLM_ENDPOINT", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        get_settings.cache_clear()


def _run_generate_items_flow(sqlite_url: str) -> None:
    with TestClient(create_app(sqlite_url)) as client:
        _seed_pool(client, sqlite_url)
        created = client.post("/api/v1/learning/contracts", json=_contract_payload())
        contract_id = created.json()["id"]
        client.post(f"/api/v1/learning/contracts/{contract_id}/confirm")

        # 未确认前 409 已由确认前置；未传概念且无蓝图 → 422
        gen = client.post(f"/api/v1/learning/contracts/{contract_id}/generate-items", json={})
        assert gen.status_code == 422

        # 指定概念生成
        gen = client.post(
            f"/api/v1/learning/contracts/{contract_id}/generate-items",
            json={"concepts": ["c_select", "c_join"], "per_concept": 4},
        )
        assert gen.status_code == 201, gen.text
        body = gen.json()
        assert body["generated"] == 8
        assert len(body["items"]) == 8
        paper_id = body["paper_id"]

        # 重复调用追加（同卷）
        again = client.post(
            f"/api/v1/learning/contracts/{contract_id}/generate-items",
            json={"concepts": ["c_select"], "per_concept": 2},
        )
        assert again.status_code == 201
        assert again.json()["paper_id"] == paper_id

        # 蓝图现在可构建（生成题入池）
        bp = client.post(
            f"/api/v1/learning/contracts/{contract_id}/blueprint/build",
            json={"concepts": ["c_select", "c_join"]},
        )
        assert bp.status_code == 201
        assert bp.json()["complete"] is True

        # Sprint 前测有生成题（答案隔离）
        sprint = client.post(f"/api/v1/learning/contracts/{contract_id}/sprints").json()
        for phase in ("contract_confirmed", "baseline_assessment"):
            client.post(
                f"/api/v1/learning/sprints/{sprint['id']}/advance", json={"to_phase": phase}
            )
        assessment = client.get(
            f"/api/v1/learning/sprints/{sprint['id']}/assessment", params={"phase": "baseline"}
        )
        assert assessment.status_code == 200
        assert len(assessment.json()["items"]) == 4
        assert "answer" not in assessment.text

        # M16-012b 回归：生成题入池后 /papers 列表不因 angles 空 dict 解包 500
        papers = client.get("/api/v1/papers")
        assert papers.status_code == 200, papers.text
        assert any(p["id"] == paper_id for p in papers.json())


def test_cold_start_generate_derives_concepts(sqlite_url, monkeypatch) -> None:
    """M17-001 冷启动：全新概念合同（无蓝图/题池无此概念）→ generate-items
    空概念触发 LLM 推导（第一次调用）→ 出题（第二次调用）→ 蓝图 → 前测。

    此前语义：空概念且无蓝图 → 422 死锁（全新概念无源头）。
    """
    import json as jsonlib

    calls: list[str] = []

    class _SeqStubGateway:
        def __init__(self, **kwargs: object) -> None:
            pass

        def chat(self, messages: object, temperature: float = 0.0, max_tokens: int = 1024) -> str:
            calls.append("chat")
            if len(calls) == 1:
                # 第一次：概念推导
                return jsonlib.dumps(
                    {
                        "concepts": [
                            {"id": "k8s:rollout", "name": "滚动更新"},
                            {"id": "k8s:probe", "name": "健康探针"},
                        ]
                    },
                    ensure_ascii=False,
                )
            # 第二次起：出题（concept 按出题协议回显示名——concepts 映射的 key）
            items = []
            for name in ("滚动更新", "健康探针"):
                for index in range(4):
                    items.append(
                        {
                            "concept": name,
                            "stem": f"{name} 第 {index} 题",
                            "options": ["甲", "乙", "丙", "丁"],
                            "answer_index": index % 4,
                            "difficulty": 2,
                            "why": "解析",
                        }
                    )
            return jsonlib.dumps({"items": items}, ensure_ascii=False)

    import app.domain.concept_derivation as cd
    import app.domain.item_generator as ig

    monkeypatch.setattr(cd, "LlmGateway", _SeqStubGateway)
    monkeypatch.setattr(ig, "LlmGateway", _SeqStubGateway)

    from app.core.config import get_settings

    monkeypatch.setenv("LLM_ENDPOINT", "http://stub.local/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-stub-0123456789")
    monkeypatch.setenv("LLM_MODEL", "stub-model")
    get_settings.cache_clear()
    try:
        with TestClient(create_app(sqlite_url)) as client:
            _seed_pool(client, sqlite_url)  # 题池只有 c_select/c_join——k8s:* 是全新概念
            created = client.post("/api/v1/learning/contracts", json=_contract_payload())
            contract_id = created.json()["id"]
            client.post(f"/api/v1/learning/contracts/{contract_id}/confirm")

            # 冷启动：空概念 → LLM 推导（call #1）→ 出题（call #2）
            gen = client.post(
                f"/api/v1/learning/contracts/{contract_id}/generate-items",
                json={"per_concept": 4},
            )
            assert gen.status_code == 201, gen.text
            body = gen.json()
            assert body["generated"] == 8
            assert len(calls) == 2
            derived_concepts = {item["concept_id"] for item in body["items"]}
            assert derived_concepts == {"k8s:rollout", "k8s:probe"}

            # 蓝图可构建（推导概念随题入库，聚合可见）
            bp = client.post(
                f"/api/v1/learning/contracts/{contract_id}/blueprint/build",
                json={"concepts": ["k8s:rollout", "k8s:probe"]},
            )
            assert bp.status_code == 201
            assert bp.json()["complete"] is True

            # 前测有题（答案隔离）
            sprint = client.post(f"/api/v1/learning/contracts/{contract_id}/sprints").json()
            for phase in ("contract_confirmed", "baseline_assessment"):
                client.post(
                    f"/api/v1/learning/sprints/{sprint['id']}/advance", json={"to_phase": phase}
                )
            assessment = client.get(
                f"/api/v1/learning/sprints/{sprint['id']}/assessment",
                params={"phase": "baseline"},
            )
            assert assessment.status_code == 200
            assert len(assessment.json()["items"]) == 4
            assert "answer" not in assessment.text
    finally:
        monkeypatch.delenv("LLM_ENDPOINT", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        get_settings.cache_clear()


def test_review_queue_item_serves_isolated_question(sqlite_url) -> None:
    """M17-002 复习出题端点：present 后取题——概念匹配/客观题/答案隔离。"""
    with TestClient(create_app(sqlite_url)) as client:
        _seed_pool(client, sqlite_url)
        sprint_id = _make_sprint_ready_for_baseline(client)
        baseline = client.get(
            f"/api/v1/learning/sprints/{sprint_id}/assessment", params={"phase": "baseline"}
        ).json()["items"]
        client.post(
            f"/api/v1/learning/sprints/{sprint_id}/assessment/baseline/submit",
            json={"responses": [{"item_id": i["item_id"], "response": "A"} for i in baseline]},
        )
        for phase in ("concept_map_review", "plan_confirmed", "learning",
                      "targeted_practice", "transfer_check", "posttest"):
            client.post(f"/api/v1/learning/sprints/{sprint_id}/advance", json={"to_phase": phase})
        posttest = client.get(
            f"/api/v1/learning/sprints/{sprint_id}/assessment", params={"phase": "posttest"}
        ).json()["items"]
        client.post(
            f"/api/v1/learning/sprints/{sprint_id}/assessment/posttest/submit",
            json={"responses": [{"item_id": i["item_id"], "response": "A"} for i in posttest]},
        )
        reviews = client.post(f"/api/v1/learning/sprints/{sprint_id}/follow-up/schedule").json()

        review_id = reviews[0]["id"]
        client.post(f"/api/v1/learning/review-queue/{review_id}/present")

        item = client.get(f"/api/v1/learning/review-queue/{review_id}/item")
        assert item.status_code == 200, item.text
        body = item.json()
        # 概念匹配 + 客观题 + 答案隔离（answer/explanation 绝不下发）
        assert body["concept_id"] == reviews[0]["concept_id"]
        assert body["options"] and len(body["options"]) >= 2
        assert "answer" not in body and "explanation" not in body
        assert "answer" not in item.text

        # 该题可直接 attempt（概念匹配客观题）→ completed
        attempted = client.post(
            f"/api/v1/learning/review-queue/{review_id}/attempt",
            json={"item_id": body["item_id"], "response": body["options"][0]["key"], "confidence": 3},
        )
        assert attempted.status_code == 200, attempted.text
        assert attempted.json()["status"] == "completed"


def test_review_attempt_uses_fsrs_scheduler(sqlite_url, monkeypatch) -> None:
    """M17-003：缺省 fsrs 调度器——algorithm_version 记录 + memory_state 持久。"""
    from app.core.config import get_settings

    monkeypatch.setenv("REVIEW_ALGORITHM", "fsrs")
    get_settings.cache_clear()
    try:
        with TestClient(create_app(sqlite_url)) as client:
            _seed_pool(client, sqlite_url)
            sprint_id = _make_sprint_ready_for_baseline(client)
            baseline = client.get(
                f"/api/v1/learning/sprints/{sprint_id}/assessment", params={"phase": "baseline"}
            ).json()["items"]
            client.post(
                f"/api/v1/learning/sprints/{sprint_id}/assessment/baseline/submit",
                json={"responses": [{"item_id": i["item_id"], "response": "A"} for i in baseline]},
            )
            for phase in ("concept_map_review", "plan_confirmed", "learning",
                          "targeted_practice", "transfer_check", "posttest"):
                client.post(f"/api/v1/learning/sprints/{sprint_id}/advance", json={"to_phase": phase})
            posttest = client.get(
                f"/api/v1/learning/sprints/{sprint_id}/assessment", params={"phase": "posttest"}
            ).json()["items"]
            client.post(
                f"/api/v1/learning/sprints/{sprint_id}/assessment/posttest/submit",
                json={"responses": [{"item_id": i["item_id"], "response": "A"} for i in posttest]},
            )
            reviews = client.post(f"/api/v1/learning/sprints/{sprint_id}/follow-up/schedule").json()
            review_id = reviews[0]["id"]
            client.post(f"/api/v1/learning/review-queue/{review_id}/present")
            item = client.get(f"/api/v1/learning/review-queue/{review_id}/item").json()

            attempted = client.post(
                f"/api/v1/learning/review-queue/{review_id}/attempt",
                json={"item_id": item["item_id"], "response": item["options"][0]["key"], "confidence": 4},
            )
            assert attempted.status_code == 200, attempted.text
            # algorithm_version 随行记录（fsrs）——复习数据可重算、算法可溯
            assert attempted.json()["status"] == "completed"
            # 直接查库验证 memory_state 持久（ReviewOut 不含内部状态）
            from sqlalchemy import select
            from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

            from app.db.orm import LearningReviewRow

            engine = create_async_engine(sqlite_url)

            async def _fetch() -> LearningReviewRow:
                async with async_sessionmaker(engine)() as session:
                    return (
                        await session.execute(
                            select(LearningReviewRow).where(LearningReviewRow.id == review_id)
                        )
                    ).scalar_one()

            import asyncio

            row = asyncio.run(_fetch())
            asyncio.run(engine.dispose())
            assert row.algorithm_version == "fsrs-py-6.3"
            assert isinstance(row.memory_state, dict) and "stability" in row.memory_state
    finally:
        monkeypatch.delenv("REVIEW_ALGORITHM", raising=False)
        get_settings.cache_clear()
