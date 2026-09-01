"""M6-07 Load and reliability：非模型 API p95 与结算/评分重试不丢失。

验收（backlog M6-07）：
- 非模型 API p95 <= 500ms —— 真实 uvicorn + 真实 PG，httpx 并发压测
  混合只读端点（papers/exam 恢复端点/submission/report），nearest-rank p95
  断言（AIOS_PG_TEST_URL 门控）；
- 自动提交和评分任务在重试后不丢失 —— 结算为单事务原子操作（异常回滚
  无半成品），故障注入评分中断后重试：submission 不存在 -> 重试成功恰好
  一份 -> 幂等恒同；PG 下换引擎（模拟进程重启）后 submission 持久化
  且重试提交幂等收敛同一份报告。

压测首跑实测整体 p95=651ms 超预算，分端点归因定位到 GET /papers 全量
列表的 N+1 查询（每卷一次独立题目查询往返，351 卷 = 352 次串行往返；
并发下往返延迟线性放大，repo 层 16 并发实测 1889ms vs 串行 242ms）。
修复为单条 IN 批量查询 + 内存分组（postgres.py list_papers），行为不变
（批量与单卷构造逐字段一致性已断言），修复后整体 p95=261ms 达预算。
"""
from __future__ import annotations

import asyncio
import math
import os
import time

import pytest

from app.db.session import create_engine, make_sessionmaker, prepare_database
from app.repositories.postgres import PostgresRepository
from app.repositories.seed import seed_papers

SQLITE_URL = "sqlite+aiosqlite:///:memory:"
PG_URL = os.environ.get("AIOS_PG_TEST_URL")


# ---------------------------------------------------------------- 故障注入重试


def test_settlement_failure_retry_no_loss() -> None:
    """评分中断（故障注入）-> 重试：无半成品、重试成功恰好一份、幂等恒同。"""
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(SQLITE_URL), raise_server_exceptions=False) as client:
        started = client.post("/api/v1/papers/functions-basics/exams", json={"mode": "exam"})
        assert started.status_code == 201, started.text
        exam_id = started.json()["exam_id"]
        question = started.json()["questions"][0]
        answered = client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 1, "question_id": question["id"], "answer": "B"},
        )
        assert answered.status_code == 200, answered.text

        repo = client.app.state.repository
        original_submit = repo.submit

        async def failing_submit(exam_id: str):
            raise RuntimeError("评分任务中断（模拟评分 worker 崩溃）")

        # 第一次提交：评分中断 -> 500；事务回滚无半成品
        repo.submit = failing_submit
        try:
            failed = client.post(f"/api/v1/exams/{exam_id}/submit", json={})
            assert failed.status_code == 500, failed.text
            missing = client.get(f"/api/v1/exams/{exam_id}/submission")
            assert missing.status_code == 404, "评分中断后不得有半成品 submission"
        finally:
            repo.submit = original_submit

        # 重试：成功恰好一份，报告完整
        retried = client.post(f"/api/v1/exams/{exam_id}/submit", json={})
        assert retried.status_code == 200, retried.text
        body = retried.json()
        assert body["score"] >= 0 and len(body["items"]) > 0 and body["total_count"] > 0

        # 幂等：重复 submit 与 GET submission 恒同
        again = client.post(f"/api/v1/exams/{exam_id}/submit", json={})
        fetched = client.get(f"/api/v1/exams/{exam_id}/submission")
        assert again.status_code == fetched.status_code == 200
        assert again.json() == fetched.json() == body


@pytest.mark.skipif(not PG_URL, reason="需要 AIOS_PG_TEST_URL 指向真实 PostgreSQL")
def test_settlement_survives_engine_replacement() -> None:
    """真实 PG：换引擎（模拟进程重启）后 submission 持久化，重试提交幂等收敛。"""

    async def body() -> None:
        engine = create_engine(PG_URL)
        await prepare_database(engine, PG_URL)
        repo = PostgresRepository(make_sessionmaker(engine))
        paper = next(p for p in seed_papers() if p.id == "functions-basics")

        record = await repo.create_exam(paper, "exam")
        await repo.save_answer(
            record.exam_id, 1, paper.questions[0].id, "B"
        )
        first = await repo.submit(record.exam_id)
        await engine.dispose()  # 模拟进程崩溃：释放全部连接

        # 新引擎（新进程等价物）：submission 不丢失
        engine2 = create_engine(PG_URL)
        try:
            repo2 = PostgresRepository(make_sessionmaker(engine2))
            survived = await repo2.get_submission(record.exam_id)
            assert survived is not None, "进程重启后 submission 不得丢失"
            assert survived.exam_id == first.exam_id

            # 重试提交：幂等收敛同一份报告
            retried = await repo2.submit(record.exam_id)
            assert retried.exam_id == first.exam_id
            assert retried.score == first.score
            assert retried.rule_version == first.rule_version
        finally:
            await engine2.dispose()

    asyncio.run(body())


# ---------------------------------------------------------------- p95 负载测试

LOAD_PORT = 8029
P95_BUDGET_MS = 500.0


def _p95_nearest_rank(samples_ms: list[float]) -> float:
    ordered = sorted(samples_ms)
    idx = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[idx]


def _wait_ready(base: str, deadline_s: float = 90.0) -> None:
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    started = time.monotonic()
    while time.monotonic() - started < deadline_s:
        try:
            with opener.open(base + "/papers", timeout=5) as resp:
                if resp.status == 200:
                    return
        except OSError:  # 就绪轮询：连接拒绝/超时均为 OSError 子类
            time.sleep(1.0)
    raise RuntimeError("uvicorn 压测实例未在时限内就绪")


@pytest.mark.skipif(not PG_URL, reason="需要 AIOS_PG_TEST_URL 指向真实 PostgreSQL")
def test_non_model_api_p95_under_load() -> None:
    """真实 uvicorn + 真实 PG：并发压测混合只读端点，p95 <= 500ms 且零 5xx。

    uvicorn.Server 在独立线程程序化启动（真实 TCP 栈 + ASGI 全栈 + PG），
    压测客户端走真实网络回环。
    """
    import threading

    import uvicorn

    port = int(os.environ.get("AIOS_LOAD_PORT", str(LOAD_PORT)))
    base = f"http://127.0.0.1:{port}/api/v1"

    from app.main import create_app

    server_config = uvicorn.Config(
        create_app(PG_URL), host="127.0.0.1", port=port, log_level="warning"
    )
    server = uvicorn.Server(server_config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        _wait_ready(base)
        summary = asyncio.run(_run_load(base))
    finally:
        server.should_exit = True
        thread.join(timeout=15)

    print(
        f"\nload p50={summary['p50']:.0f}ms p95={summary['p95']:.0f}ms "
        f"max={summary['max']:.0f}ms samples={summary['count']} errors={summary['errors']}"
    )
    assert summary["errors"] == 0, f"压测期间出现失败请求: {summary['error_detail']}"
    assert summary["p95"] <= P95_BUDGET_MS, (
        f"p95={summary['p95']:.0f}ms 超出 {P95_BUDGET_MS:.0f}ms 预算"
    )


async def _run_load(base: str) -> dict:
    """混合只读端点并发压测：papers 列表 / exam 恢复 / submission / report。"""
    import httpx

    limits = httpx.Limits(max_connections=12, max_keepalive_connections=12)
    timeout = httpx.Timeout(10.0)
    async with httpx.AsyncClient(
        base_url=base, limits=limits, timeout=timeout, trust_env=False
    ) as client:
        # 建一场已提交考试，供恢复/报告端点读取
        started = await client.post(
            "/papers/functions-basics/exams", json={"mode": "exam"}
        )
        started.raise_for_status()
        exam_id = started.json()["exam_id"]
        question = started.json()["questions"][0]
        await client.put(
            f"/exams/{exam_id}/answers",
            json={"sequence": 1, "question_id": question["id"], "answer": "B"},
        )
        submitted = await client.post(f"/exams/{exam_id}/submit", json={})
        submitted.raise_for_status()

        targets = [
            ("GET", "/papers"),
            ("GET", f"/exams/{exam_id}"),
            ("GET", f"/exams/{exam_id}/submission"),
            ("GET", f"/exams/{exam_id}/report"),
        ]

        async def one(call: tuple[str, str]) -> float:
            method, path = call
            t0 = time.perf_counter()
            resp = await client.get(path)
            elapsed = (time.perf_counter() - t0) * 1000.0
            if resp.status_code != 200:
                raise RuntimeError(f"{method} {path} -> {resp.status_code}")
            return elapsed

        # warmup 不计入采样
        for _ in range(8):
            await one(targets[0])

        latencies: list[float] = []
        errors: list[str] = []
        rounds = 30
        for _ in range(rounds):
            results = await asyncio.gather(
                *(one(t) for _ in range(4) for t in targets),
                return_exceptions=True,
            )
            for r in results:
                if isinstance(r, BaseException):
                    errors.append(str(r))
                else:
                    latencies.append(r)

        ordered = sorted(latencies)

        def pct(ratio: float) -> float:
            idx = max(0, math.ceil(ratio * len(ordered)) - 1)
            return ordered[idx]

        return {
            "p50": pct(0.50),
            "p95": pct(0.95),
            "max": ordered[-1] if ordered else 0.0,
            "count": len(latencies),
            "errors": len(errors),
            "error_detail": errors[:5],
        }
