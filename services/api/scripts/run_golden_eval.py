#!/usr/bin/env python3
"""M16-009 黄金集离线评测（升级方案 Phase 7）。

用 SQL 基础黄金集（eval/golden_sql_foundation.json，人工标注）离线评测：
1. 蓝图等值性：每概念 4 题 → pretest/posttest 各 2 题不重叠且难度配平
   （差 ≤1）；全概念 complete；
2. 判分一致性：黄金答案经 grading.grade_answer 全部判对（错选判错）；
3. 掌握度规则黄金场景：前测错→练习多上下文对→延迟对=mastered；
   复测失败=at_risk（与规则文档一致）。

输出：pass/failed 报告；exit 0 = 全过（CI 可重复——纯离线，无网络）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # services/api（app 包根）

from app.domain.assessment_blueprint import CandidateItem, build_blueprint
from app.domain.grading import grade_answer
from app.domain.mastery import AttemptEvidence, derive_mastery

GOLDEN_PATH = Path(__file__).resolve().parents[1] / "eval" / "golden_sql_foundation.json"
KEYS = ("A", "B", "C", "D")


def main() -> int:
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    items = golden["items"]
    failures: list[str] = []

    # 1. 蓝图等值性
    candidates = [
        CandidateItem(
            item_id=entry["id"],
            concept_id=entry["concept"],
            difficulty=entry["difficulty"],
            question_type="mcq",
        )
        for entry in items
    ]
    blueprint = build_blueprint(candidates)
    if not blueprint.complete:
        failures.append(f"蓝图 incomplete：{blueprint.gaps}")
    for plan in blueprint.plans:
        overlap = set(plan.pretest_item_ids) & set(plan.posttest_item_ids)
        if overlap:
            failures.append(f"{plan.concept_id} 前后测重叠：{overlap}")
        by_id = {e["id"]: e for e in items}
        pre_avg = sum(by_id[i]["difficulty"] for i in plan.pretest_item_ids) / max(1, len(plan.pretest_item_ids))
        post_avg = sum(by_id[i]["difficulty"] for i in plan.posttest_item_ids) / max(1, len(plan.posttest_item_ids))
        if abs(pre_avg - post_avg) > 1.01:
            failures.append(
                f"{plan.concept_id} 难度失衡：前测均值 {pre_avg:.1f} vs 后测 {post_avg:.1f}"
            )

    # 2. 判分一致性（黄金答案判对 + 每个错选判错）
    for entry in items:
        correct_letter = KEYS[entry["answer_index"]]
        if grade_answer("mcq", correct_letter, correct_letter) is not True:
            failures.append(f"{entry['id']} 黄金答案未判对")
        wrong_letter = KEYS[(entry["answer_index"] + 1) % 4]
        if grade_answer("mcq", correct_letter, wrong_letter) is not False:
            failures.append(f"{entry['id']} 错选未判错")

    # 3. 掌握度黄金场景
    mastered_flow = derive_mastery(
        [
            AttemptEvidence("b1", "c", correct=False, context="mcq:baseline"),
            AttemptEvidence("p1", "c", correct=True, context="mcq:practice"),
            AttemptEvidence("p2", "c", correct=True, context="essay:practice"),
            AttemptEvidence("p3", "c", correct=True, context="apply:practice"),
            AttemptEvidence("d1", "c", correct=True, delayed=True, context="mcq:delayed"),
        ]
    )
    if mastered_flow.state != "mastered":
        failures.append(f"黄金场景一（学成）应为 mastered，实际 {mastered_flow.state}")
    at_risk_flow = derive_mastery(
        [
            AttemptEvidence("b1", "c", correct=True, context="mcq:baseline"),
            AttemptEvidence("p1", "c", correct=True, context="essay:practice"),
            AttemptEvidence("p2", "c", correct=True, context="apply:practice"),
            AttemptEvidence("d1", "c", correct=True, delayed=True, context="mcq:delayed"),
            AttemptEvidence("d2", "c", correct=False, delayed=True, context="mcq:delayed"),
        ]
    )
    if at_risk_flow.state != "at_risk":
        failures.append(f"黄金场景二（复测失败）应为 at_risk，实际 {at_risk_flow.state}")

    concepts = set(golden["concepts"])
    covered = {plan.concept_id for plan in blueprint.plans}
    if concepts != covered:
        failures.append(f"概念覆盖不全：缺 {concepts - covered}")

    print(f"黄金集：{len(items)} 题 / {len(concepts)} 概念")
    print(f"蓝图等值性：{'pass' if not [f for f in failures if '蓝图' in f or '重叠' in f or '失衡' in f or '覆盖' in f] else 'failed'}")
    print(f"判分一致性：{'pass' if not [f for f in failures if '判' in f] else 'failed'}")
    print(f"掌握度黄金场景：{'pass' if not [f for f in failures if '场景' in f] else 'failed'}")
    if failures:
        print("\nFAILED 明细：")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\n全部 pass（离线评测可重复：纯本地黄金集，无网络/LLM 依赖）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
