"""M6-01 Golden grading set：覆盖所有题型的黄金判分集 + 可重复生成的 agreement report。

- GOLDEN_GRADING_CASES 全部 expected_verdict 均经探针验证后固化（不虚报预期）；
- 覆盖：8 种 QuestionSpec 题型 + seed 别名（tf/choice/short）+ legacy/JSON expected
  两种形态 + 大小写/空白归一 + 集合等价 + 多候选 + 三态（True/False/None）；
- agreement report 确定性：无时间戳/无随机——同 golden set + 同规则版本恒同报告
  （可重复生成）；mismatch 全量透出（不吞不瞒）；
- 评测运行可落库回查（eval_runs 表，ADR 系列「记录可审计」延续）。
"""
from __future__ import annotations

from typing import Final

from app.domain.grading import (
    OBJECTIVE_RULE_VERSION,
    grade_answer,
    grade_math,
    grade_numeric,
    normalize_type,
)
from app.domain.rubric_grader import (
    RUBRIC_RULE_VERSION,
    KeywordRubricJudge,
    judge_question,
)

EVAL_RULE_VERSION: Final = "eval-v1"

ALL_QUESTION_TYPES: Final = (
    "mcq",
    "multiple_select",
    "true_false",
    "short_answer",
    "numeric",
    "math",
    "coding",
    "essay",
)


def _case(case_id, question_type, expected, given, expected_verdict, note=""):
    return {
        "case_id": case_id,
        "question_type": question_type,
        "stem": "",
        "expected": expected,
        "given": given,
        "expected_verdict": expected_verdict,
        "note": note,
    }


GOLDEN_GRADING_CASES: Final = (
    # mcq
    _case("mcq-exact", "mcq", "A", "A", True, "精确命中"),
    _case("mcq-norm", "mcq", "A", " a ", True, "大小写空白归一"),
    _case("mcq-wrong", "mcq", "A", "B", False, "选错"),
    _case("mcq-empty", "mcq", "A", "", False, "空答案判错"),
    _case("mcq-json", "mcq", '{"option_index": 1}', "B", True, "JSON expected 形态"),
    # multiple_select
    _case("ms-set-eq", "multiple_select", "ABD", "ADB", True, "集合等价"),
    _case("ms-wrong", "multiple_select", "ABD", "ABC", False, "漏选/错选"),
    _case("ms-json", "multiple_select", '{"option_indices": [0, 2]}', "AC", True, "JSON 索引形态"),
    # true_false
    _case("tf-legacy", "true_false", "T", "对", True, "中文对错归一"),
    _case("tf-json", "true_false", '{"value": true}', "yes", True, "JSON + 英文归一"),
    _case("tf-json-false", "true_false", '{"value": false}', "F", True, "JSON false 形态"),
    _case("tf-wrong", "true_false", "T", "F", False, "判断相反"),
    # short_answer
    _case("short-multi", "short_answer", "北京|京城", "京城", True, "多候选 legacy 形态"),
    _case("short-json", "short_answer", '{"accepted": ["x", "y"]}', "Y", True, "JSON accepted 形态"),
    _case("short-wrong", "short_answer", "北京", "上海", False, "答案不符"),
    # numeric
    _case("numeric-exact", "numeric", "3.14", "3.14", True, "数值精确"),
    _case("numeric-unit-mismatch", "numeric", "100 米", "100m", None, "单位不匹配进复核"),
    _case("numeric-unparseable", "numeric", "3.14", "abc", None, "无法解析进复核"),
    # math
    _case("math-exact", "math", "x^2", "x^2", True, "表达式一致"),
    _case("math-wrong", "math", "x^2", "x^3", False, "表达式不同"),
    # coding
    _case("coding-not-gradable", "coding", '{"reference_solution": "def f(): pass", "tests": ["t1"]}', "def f(): pass", False, "objective 管线判不出按 False（sandbox 执行属 M6-07 范围）"),
    # essay（rubric 管线）
    _case("essay-full", "essay",
          '{"rubric_points": ["切线斜率", "瞬时变化率"]}',
          "导数是切线斜率，也即瞬时变化率。", True, "全部评分点命中"),
    _case("essay-partial", "essay",
          '{"rubric_points": ["切线斜率", "瞬时变化率"]}',
          "导数是切线斜率。", False, "部分命中不给满分"),
    _case("essay-none", "essay",
          '{"rubric_points": ["切线斜率", "瞬时变化率"]}',
          "不知道。", False, "零命中"),
    # seed 别名 + unknown
    _case("alias-tf", "tf", "T", "TRUE", True, "seed 别名 tf"),
    _case("alias-choice", "choice", "B", "b", True, "seed 别名 choice"),
    _case("alias-short", "short", "答案", "答案", True, "seed 别名 short"),
    _case("unknown-type", "mystery", "A", "A", False, "未知题型判错不崩溃"),
)

_RUBRIC_JUDGE = KeywordRubricJudge()


def judge_case(case: dict) -> bool | None:
    """单样例判分：按规范题型路由到对应 grader（与 judge_question 同款路由）。"""
    kind = normalize_type(case["question_type"])
    if kind == "numeric":
        return grade_numeric(case["expected"], case["given"])
    if kind == "math":
        return grade_math(case["expected"], case["given"])
    if kind == "essay":
        correct, _ = judge_question(
            "essay", case["stem"], case["expected"], case["given"],
            rubric_judge=_RUBRIC_JUDGE,
        )
        return correct
    return grade_answer(kind, case["expected"], case["given"])


def run_grading_eval(cases: tuple[dict, ...] = GOLDEN_GRADING_CASES) -> dict:
    """运行评测生成 agreement report（确定性：无时间戳/无随机，可重复生成）。

    报告含：规则版本、样例覆盖（题型全集校验）、总分 agreement、逐题型 agreement、
    mismatch 全量明细。agreement < 1.0 不隐藏——报告本身如实透出。
    """
    if not cases:
        raise ValueError("golden set 为空")
    mismatches: list[dict] = []
    per_type: dict[str, dict] = {}
    for case in cases:
        actual = judge_case(case)
        agreed = actual == case["expected_verdict"]
        bucket = per_type.setdefault(normalize_type(case["question_type"]), {
            "total": 0, "agreed": 0,
        })
        bucket["total"] += 1
        if agreed:
            bucket["agreed"] += 1
        else:
            mismatches.append({
                "case_id": case["case_id"],
                "question_type": case["question_type"],
                "expected_verdict": case["expected_verdict"],
                "actual_verdict": actual,
            })
    covered = sorted(per_type)
    # 覆盖校验：8 种规范题型全部出现即完整（别名桶/未知题型桶不影响覆盖结论）
    coverage_complete = set(ALL_QUESTION_TYPES).issubset(set(covered))
    total = sum(b["total"] for b in per_type.values())
    agreed_total = sum(b["agreed"] for b in per_type.values())
    return {
        "rule_versions": {
            "objective": OBJECTIVE_RULE_VERSION,
            "rubric": RUBRIC_RULE_VERSION,
            "eval": EVAL_RULE_VERSION,
        },
        "case_count": total,
        "covered_types": covered,
        "coverage_complete": coverage_complete,
        "agreement": round(agreed_total / total, 4) if total else None,
        "per_type": [
            {
                "question_type": qtype,
                "total": per_type[qtype]["total"],
                "agreed": per_type[qtype]["agreed"],
                "agreement": round(per_type[qtype]["agreed"] / per_type[qtype]["total"], 4),
            }
            for qtype in covered
        ],
        "mismatches": mismatches,
    }
