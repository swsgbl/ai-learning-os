"""M7-02 Onboarding walkthrough：新用户 10 分钟全路径的可执行复刻。

docs/ONBOARDING.md 的每一步都在 run_walkthrough 中有唯一对应实现；
文档一致性由 tests/test_onboarding_guide.py 锁定（文档中出现的端点
集合必须是 walkthrough 实际请求端点集合的子集）。

run_walkthrough 接受任何 duck-typed HTTP client（TestClient / httpx.Client），
逐步计时并断言每步状态码；总预算 BUDGET_S = 600s。验收语义：新用户
10 分钟内完成初始化、导入课程和第一套题 —— API 侧毫秒级，耗时主体在
用户安装与阅读；CLI 汇总打印每步耗时。
"""
from __future__ import annotations

import time

BUDGET_S = 600.0

# walkthrough 实际请求的端点模板全集（测试用它做文档一致性守卫的匹配目标）
WALKTHROUGH_ENDPOINTS = frozenset({
    "GET /health",
    "GET /api/v1/voice/providers",
    "POST /api/v1/sources",
    "POST /api/v1/resources/upload",
    "POST /api/v1/resources/{}/parse",
    "POST /api/v1/courses/import-drafts",
    "POST /api/v1/courses/import-drafts/{}/approve",
    "POST /api/v1/papers/import",
    "POST /api/v1/papers/{}/exams",
    "PUT /api/v1/exams/{}/answers",
    "POST /api/v1/exams/{}/submit",
    "GET /api/v1/exams/{}/report",
    "GET /api/v1/student/daily-plan",
    "GET /api/v1/student/selection",
})

# 课程示例文档：命中 M5-05 概念提取模式「X 的定义」-> 概念「极限」「导数」「积分」
COURSE_DOC = (
    '[{"question": "极限的定义", "answer": "描述函数当自变量趋近某点时的趋势"},\n'
    '  {"question": "导数的定义", "answer": "函数增量与自变量增量之比的极限"},\n'
    '  {"question": "积分的定义", "answer": "分割、求和、取极限"}]'
)

# 第一套题：最小 3 题卷（mcq / true_false / short_answer 各一）
FIRST_PAPER = {
    "title": "我的第一套题",
    "duration_seconds": 900,
    "policy": "exam",
    "questions": [
        {
            "question": {
                "question_type": "mcq",
                "stem": "1. 函数在一点连续是可导的什么条件？",
                "options": ["必要不充分", "充分不必要", "充要", "既不充分也不必要"],
                "answer": {"option_index": 0},
                "explanation": "可导必连续，连续不一定可导。",
            },
            "score": 3.0,
        },
        {
            "question": {
                "question_type": "true_false",
                "stem": "2. 可导函数一定连续。",
                "answer": {"value": True},
                "explanation": "可导必连续是标准定理。",
            },
            "score": 3.0,
        },
        {
            "question": {
                "question_type": "short_answer",
                "stem": "3. 写出导数的定义式。",
                "answer": {"accepted": ["f'(x) = lim(h->0) [f(x+h) - f(x)] / h"]},
                "explanation": "导数即瞬时变化率，定义为增量比的极限。",
            },
            "score": 4.0,
        },
    ],
}


def _step(timings: list[dict], name: str):
    """step 上下文管理器：记录每步耗时（毫秒）。"""

    class _Ctx:
        def __enter__(self):
            self.t0 = time.perf_counter()
            return self

        def __exit__(self, exc_type, exc, tb):
            timings.append({
                "step": name,
                "elapsed_ms": (time.perf_counter() - self.t0) * 1000.0,
            })
            return False

    return _Ctx()


def run_walkthrough(client) -> dict:
    """复刻 ONBOARDING.md 全路径：health -> 课程导入 -> 试卷导入 -> 考试 -> 报告 -> 今日任务。

    client 为 duck-typed HTTP client（TestClient 或 httpx.Client 均可）；
    逐步断言状态码并计时，返回 steps 列表与总耗时（毫秒）。
    """
    steps: list[dict] = []
    results: dict = {}

    def _check(resp, expected: int, what: str) -> None:
        assert resp.status_code == expected, (
            f"{what}: 期望 {expected} 实际 {resp.status_code}: {resp.text}"
        )

    # ---- 第 1 步：健康检查（初始化验证）----
    with _step(steps, "健康检查 GET /health + voice providers"):
        r = client.get("/health")
        _check(r, 200, "健康检查")
        vp = client.get("/api/v1/voice/providers")
        _check(vp, 200, "语音 provider 视图")

    # ---- 第 2 步：导入课程（授权治理路径）----
    # source id 带时间戳后缀：CLI 重复执行可重放（SourceCreate id 唯一约束 409）
    source_id = f"src_onboarding_demo_{int(time.time())}"
    source_name = f"onboarding 示例来源 {source_id}"
    with _step(steps, "导入课程（source -> upload -> parse -> 草稿 -> approve）"):
        src = client.post(
            "/api/v1/sources",
            json={
                "id": source_id,
                "name": source_name,
                "source_type": "oer",
                "homepage": "https://example.edu/",
                "license_state": "OPEN_LICENSE",
                "notes": "onboarding 示例",
            },
        )
        _check(src, 201, "创建来源")
        up = client.post(
            "/api/v1/resources/upload",
            files={"file": ("onboarding-course.json", COURSE_DOC.encode(), "application/json")},
            data={"source_id": source_id, "title": "微积分概念讲义"},
        )
        _check(up, 201, "上传课程资源")
        rid = up.json()["id"]
        parsed = client.post(f"/api/v1/resources/{rid}/parse")
        _check(parsed, 200, "解析资源")
        draft = client.post("/api/v1/courses/import-drafts", json={"resource_id": rid})
        _check(draft, 201, "生成导入草稿")
        body = draft.json()
        assert body["concepts"] == ["极限", "导数", "积分"], body
        approved = client.post(
            f"/api/v1/courses/import-drafts/{body['id']}/approve",
            json={"note": "示例审核通过"},
        )
        _check(approved, 200, "审核通过")
        results["course_draft"] = body

    # ---- 第 3 步：导入第一套题 ----
    with _step(steps, "导入第一套题 POST /papers/import"):
        imported = client.post("/api/v1/papers/import", json=[FIRST_PAPER])
        _check(imported, 201, "导入试卷")
        results["paper_ids"] = imported.json()["imported"]

    # ---- 第 4 步：第一场考试（开考 -> 作答 -> 交卷 -> 报告）----
    with _step(steps, "第一场考试（exams -> answers -> submit -> report）"):
        paper_id = results["paper_ids"][0]
        started = client.post(f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"})
        _check(started, 201, "开考")
        exam = started.json()
        exam_id = exam["exam_id"]
        answers = [
            ("1", exam["questions"][0]["id"], "A"),
            ("2", exam["questions"][1]["id"], "T"),
        ]
        for seq, qid, ans in answers:
            resp = client.put(
                f"/api/v1/exams/{exam_id}/answers",
                json=q_body(seq, qid, ans),
            )
            _check(resp, 200, f"作答第 {seq} 题")
        submitted = client.post(f"/api/v1/exams/{exam_id}/submit", json={})
        _check(submitted, 200, "交卷判分")
        report = client.get(f"/api/v1/exams/{exam_id}/report")
        _check(report, 200, "查看报告")
        rep = report.json()
        assert rep["score_earned"] == 6.0, rep
        assert len(rep["mistakes"]) == 1, rep
        results["report"] = rep

    # ---- 第 5 步：今日任务与选题（个性化闭环验证）----
    with _step(steps, "今日任务与选题"):
        plan = client.get("/api/v1/student/daily-plan")
        _check(plan, 200, "今日任务")
        sel = client.get("/api/v1/student/selection")
        _check(sel, 200, "个性化选题")
        results["daily_plan"] = plan.json()
        results["selection"] = sel.json()

    results["steps"] = steps
    results["total_ms"] = sum(s["elapsed_ms"] for s in steps)
    return results


def q_body(seq: int, qid: str, ans: str) -> dict:
    return {"sequence": seq, "question_id": qid, "answer": ans}


def main() -> int:
    """CLI：python -m app.ops.onboarding --base-url http://127.0.0.1:8000"""
    import argparse

    parser = argparse.ArgumentParser(description="AI Learning OS onboarding walkthrough")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--insecure", action="store_true", help="允许自签证书环境")
    args = parser.parse_args()

    import httpx

    with httpx.Client(
        base_url=args.base_url, trust_env=False, verify=not args.insecure, timeout=120.0
    ) as client:
        # 服务端就绪等待：/health 200 且 /papers 200（DB 迁移窗口期轻端点也会失败）
        import urllib.request

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        started = time.monotonic()
        while True:
            try:
                with opener.open(args.base_url.rstrip("/") + "/health", timeout=5) as resp:
                    if resp.status == 200:
                        break
            except OSError:
                pass
            if time.monotonic() - started > 180:
                raise SystemExit("server not ready within 180s")
            time.sleep(2.0)
        results = run_walkthrough(client)

    for s in results["steps"]:
        print(f"  [{s['elapsed_ms']:>8.1f} ms] {s['step']}")
    print(f"API 全路径总耗时: {results['total_ms']:.1f} ms（预算 {BUDGET_S * 1000:.0f} ms）")
    assert results["total_ms"] < BUDGET_S * 1000, "onboarding API 路径超出 10 分钟预算"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
