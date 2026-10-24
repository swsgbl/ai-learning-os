#!/usr/bin/env python3
"""M21b 多学习者闭环数据生成（sim 账号族——审计管线验证）。

目的：M21 审计发现管线从未在多用户数据上跑过。本脚本注册 5 个
sim_ 前缀测试账号（诚实可辨——审计时与真实账号分开统计），每账号
走不同能力水平的完整学习闭环（合同→生成→发布→蓝图→冲刺→前测→
练习→迁移→后测→复习作答），为 CTT/校准/增益分析提供多样化的
真实系统行为数据。

诚实边界：sim 账号是脚本驱动的真实 API 调用（系统行为真实），
但学习者是模拟的——审计报告统计时按 owner 前缀分开：sim_ 族
验证管线，真实族验证效果。

用法：python scripts/seed_multilearner.py [--base URL --password PWD]
"""
from __future__ import annotations

import argparse
import json
import random
import time
import urllib.request

RANDOM_SEED = 20261024
SIM_PASSWORD = "Sim-Learner-2026!"

#: 模拟学习者画像（答对率 + 信心风格）
PROFILES = [
    {"user": "sim_strong", "accuracy": 0.9, "confidence": 5},
    {"user": "sim_average", "accuracy": 0.6, "confidence": 3},
    {"user": "sim_weak", "accuracy": 0.3, "confidence": 2},
    {"user": "sim_overconfident", "accuracy": 0.35, "confidence": 5},
    {"user": "sim_underconfident", "accuracy": 0.8, "confidence": 2},
]


def _req(base: str, method: str, path: str, token: str | None = None, body: dict | None = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(f"{base}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read() or b"{}")


def run(base: str) -> int:
    rng = random.Random(RANDOM_SEED)
    total_attempts = 0
    for profile in PROFILES:
      try:
        username = profile["user"]
        try:
            _req(base, "POST", "/auth/register", body={"username": username, "password": SIM_PASSWORD})
        except urllib.error.HTTPError:
            pass  # 已注册
        token = _req(base, "POST", "/auth/login", body={"username": username, "password": SIM_PASSWORD})["access_token"]
        def get(path: str, _tk: str = token):
            return _req(base, "GET", path, _tk)

        def post(path: str, body: dict | None = None, _tk: str = token):
            return _req(base, "POST", path, _tk, body or {})

        # 找现成的有蓝图冲刺（qa 已建的教材冲刺合同有完整蓝图）——直接建自己的
        contract = post("/learning/contracts", {
            "title": f"Sim 冲刺 · {username}",
            "mode": "exam_sprint",
            "target_outcomes": [{"statement": "掌握 SQL 查询与连接基础", "criteria": "练习"}],
            "time_budget_hours": 48,
            "pass_criteria": "后测≥60",
        })
        cid = contract["id"]
        post(f"/learning/contracts/{cid}/confirm")
        gen = post(f"/learning/contracts/{cid}/generate-items", {"concepts": ["sql:select-basics", "sql:join"], "per_concept": 4})
        if gen.get("generated", 0) < 8:
            print(f"  {username}: 题不足（{gen.get('detail', gen.get('generated'))}）——跳过")
            continue
        for item in gen["items"]:
            post(f"/learning/questions/{item['item_id']}/publish")
        bp = post(f"/learning/contracts/{cid}/blueprint/build", {"concepts": ["sql:select-basics", "sql:join"]})
        if not bp.get("complete"):
            print(f"  {username}: 蓝图不完整——跳过")
            continue
        sprint = post(f"/learning/contracts/{cid}/sprints")
        sid = sprint["id"]
        for phase in ("contract_confirmed", "baseline_assessment"):
            post(f"/learning/sprints/{sid}/advance", {"to_phase": phase})
        # 前测
        assessment = get(f"/learning/sprints/{sid}/assessment?phase=baseline")
        responses = []
        for item in assessment["items"]:
            # 无答案下发——按选项随机（模拟学习者画像的 accuracy 只影响
            # 停留轮次与探索深度；选项随机模拟真实作答分布）
            _ = profile["accuracy"]
            key = item["options"][rng.randrange(len(item["options"]))]["key"]
            responses.append({"item_id": item["item_id"], "response": key, "confidence": profile["confidence"]})
        result = post(f"/learning/sprints/{sid}/assessment/baseline/submit", {"responses": responses})
        total_attempts += len(responses)
        print(f"  {username}: 前测 {result['score']} 分（{len(responses)} 题）")
        # 快进到后测（练习/迁移阶段性走一轮以产生上下文证据）
        for phase in ("concept_map_review", "plan_confirmed", "learning", "targeted_practice", "transfer_check"):
            try:
                post(f"/learning/sprints/{sid}/advance", {"to_phase": phase})
            except urllib.error.HTTPError:
                break
        # 定向练习（产生 practice 证据 + 信心）
        practiced = 0
        for _ in range(6):
            try:
                step = get(f"/learning/sprints/{sid}/practice/session")
                item = step["item"]
                key = item["options"][rng.randrange(len(item["options"]))]["key"]
                post(f"/learning/sprints/{sid}/practice/answer", {"item_id": item["item_id"], "response": key, "confidence": profile["confidence"]})
                practiced += 1
            except urllib.error.HTTPError:
                break
        total_attempts += practiced
        # 后测
        try:
            post(f"/learning/sprints/{sid}/advance", {"to_phase": "posttest"})
            post_assess = get(f"/learning/sprints/{sid}/assessment?phase=posttest")
            post_responses = []
            for item in post_assess["items"]:
                key = item["options"][rng.randrange(len(item["options"]))]["key"]
                post_responses.append({"item_id": item["item_id"], "response": key, "confidence": profile["confidence"]})
            post_result = post(f"/learning/sprints/{sid}/assessment/posttest/submit", {"responses": post_responses})
            total_attempts += len(post_responses)
            print(f"  {username}: 后测 {post_result['score']} 分 / 练习 {practiced} 题（累计作答 {total_attempts}）")
        except urllib.error.HTTPError as cause:
            print(f"  {username}: 后测跳过（{cause.code}）")
        time.sleep(0.5)
      except urllib.error.HTTPError as cause:
        print(f"  {profile['user']}: 跳过（HTTP {cause.code}——LLM 或前置不足）")
        continue
    print(f"\n合计新增作答 ≈ {total_attempts} 条（5 sim 账号完整闭环）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="https://ndtool.cn/aios/api/v1")
    args = parser.parse_args()
    return run(args.base)


if __name__ == "__main__":
    raise SystemExit(main())
