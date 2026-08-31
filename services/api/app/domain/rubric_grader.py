"""M2-10 Subjective rubric grader：essay 题结构化判分（prompt pack E）。

- judge 输出必须为结构化 JSON：criteria 逐点判定 + confidence + judge_model + prompt_hash；
  分数只从 criteria 结构化计算，不允许模型自由文本直接决定分数。
- 低 confidence（< RUBRIC_CONFIDENCE_FLOOR）触发 second judge；双审逐点判定不一致
  → 不确定进复核（沿用 ADR 21 三态语义）。
- evidence gate：判定引用 evidence_id 时必须能在 evidence 库中找到；引用不存在的
  依据 → 该点降为不确定，无 evidence 的课程事实绝不进入最终报告。
- 评分版本随 submission 留痕；输入答案事件已在 answer_events 可重放。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Final, Protocol

from app.domain.grading import grade_answer, normalize_type

RUBRIC_RULE_VERSION: Final = "rubric-v1"
RUBRIC_CONFIDENCE_FLOOR: Final = 0.7


@dataclass(frozen=True, slots=True)
class RubricCriterion:
    """单个 rubric 评分点的判定：achieved 三态，evidence_id 为判定依据引用。"""

    point: str
    achieved: bool | None
    evidence_id: str | None = None


@dataclass(frozen=True, slots=True)
class RubricJudgement:
    """单次 judge 的结构化输出（04 号文档 §2.11 + pack E schema）。"""

    criteria: tuple[RubricCriterion, ...]
    confidence: float
    judge_model: str
    prompt_hash: str
    raw: str


@dataclass(frozen=True, slots=True)
class RubricResult:
    """rubric 管线最终结果：correct 沿用三态；criteria_json 随 GradedItem 留痕。"""

    correct: bool | None
    score_ratio: float | None  # 复核时不给分
    needs_second_judge: bool
    rule_version: str
    confidence: float
    judge_model: str
    prompt_hash: str
    evidence_ids: tuple[str, ...]
    criteria_json: str


class RubricJudge(Protocol):
    """judge 协议：LLM judge 部署后实现同一协议替换内置实现。"""

    def judge(
        self, stem: str, rubric_points: tuple[str, ...], given: str
    ) -> RubricJudgement | None: ...


def parse_judgement(text: str) -> RubricJudgement | None:
    """解析并校验 judge 输出 JSON；非法结构返回 None（按复核处理）。

    criteria 每项必须含 point: str 与 achieved: bool|null；confidence ∈ [0, 1]。
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    criteria_raw = data.get("criteria")
    confidence = data.get("confidence")
    if not isinstance(criteria_raw, list) or not criteria_raw:
        return None
    if not isinstance(confidence, int | float) or not 0 <= confidence <= 1:
        return None
    criteria: list[RubricCriterion] = []
    for item in criteria_raw:
        if not isinstance(item, dict) or not isinstance(item.get("point"), str):
            return None
        achieved = item.get("achieved")
        if achieved is not None and not isinstance(achieved, bool):
            return None
        evidence_id = item.get("evidence_id")
        if evidence_id is not None and not isinstance(evidence_id, str):
            return None
        criteria.append(RubricCriterion(item["point"], achieved, evidence_id))
    return RubricJudgement(
        criteria=tuple(criteria),
        confidence=float(confidence),
        judge_model=str(data.get("judge_model") or "unknown"),
        prompt_hash=str(data.get("prompt_hash") or ""),
        raw=text,
    )


def _ratio(judgement: RubricJudgement) -> float:
    decided = [c for c in judgement.criteria if c.achieved is not None]
    if not decided:
        return 0.0
    return sum(1 for c in decided if c.achieved) / len(decided)


def _criteria_json(
    criteria: tuple[RubricCriterion, ...],
    *,
    confidence: float,
    judge_model: str,
    prompt_hash: str,
    score_ratio: float | None,
) -> str:
    return json.dumps(
        {
            "rule_version": RUBRIC_RULE_VERSION,
            "criteria": [
                {"point": c.point, "achieved": c.achieved, "evidence_id": c.evidence_id}
                for c in criteria
            ],
            "score_ratio": score_ratio,
            "confidence": confidence,
            "judge_model": judge_model,
            "prompt_hash": prompt_hash,
        },
        ensure_ascii=False,
    )


def grade_rubric(
    rubric_points: tuple[str, ...],
    first: RubricJudgement | None,
    second: RubricJudgement | None = None,
    *,
    valid_evidence_ids: frozenset[str] | None = None,
) -> RubricResult:
    """rubric 管线：双审收敛 + evidence gate → 三态结果（ADR 21）。

    - 无 judge / 判定非法 / 评分点覆盖不全 → 复核；
    - 低置信度触发双审，双审逐点判定不一致 → 复核；
    - 引用了不存在 evidence 的判定 → 该点降为不确定（不冤判也不虚构依据）。
    """

    def pending(needs_second: bool) -> RubricResult:
        return RubricResult(
            correct=None,
            score_ratio=None,
            needs_second_judge=needs_second,
            rule_version=RUBRIC_RULE_VERSION,
            confidence=first.confidence if first else 0.0,
            judge_model=first.judge_model if first else "",
            prompt_hash=first.prompt_hash if first else "",
            evidence_ids=(),
            criteria_json=_criteria_json(
                first.criteria if first else (),
                confidence=first.confidence if first else 0.0,
                judge_model=first.judge_model if first else "",
                prompt_hash=first.prompt_hash if first else "",
                score_ratio=None,
            ),
        )

    if not rubric_points or first is None:
        return pending(False)

    # pack E「低 confidence 或分差超阈值触发 second judge」：单次判定的分差无对照源，
    # 以低置信度作为触发条件；收敛条件为双审逐点判定一致。
    needs_second = first.confidence < RUBRIC_CONFIDENCE_FLOOR
    if needs_second and second is None:
        return pending(True)
    effective = second if second is not None else first
    if second is not None and (
        {c.point: c.achieved for c in first.criteria} != {c.point: c.achieved for c in second.criteria}
        or abs(_ratio(first) - _ratio(second)) > 1e-9
    ):
        return pending(True)

    if {c.point for c in effective.criteria} != set(rubric_points):
        return pending(needs_second)  # 判定未恰好覆盖评分点：不虚构缺失点结论

    valid = valid_evidence_ids or frozenset()
    gated = tuple(
        replace(c, achieved=None)
        if c.evidence_id and c.evidence_id not in valid
        else c
        for c in effective.criteria
    )
    if any(c.achieved is None for c in gated):
        return pending(needs_second)

    correct = all(c.achieved for c in gated)
    ratio = sum(1 for c in gated if c.achieved) / len(gated)
    return RubricResult(
        correct=correct,
        score_ratio=ratio,
        needs_second_judge=needs_second,
        rule_version=RUBRIC_RULE_VERSION,
        confidence=effective.confidence,
        judge_model=effective.judge_model,
        prompt_hash=effective.prompt_hash,
        evidence_ids=tuple(dict.fromkeys(c.evidence_id for c in gated if c.evidence_id)),
        criteria_json=_criteria_json(
            gated,
            confidence=effective.confidence,
            judge_model=effective.judge_model,
            prompt_hash=effective.prompt_hash,
            score_ratio=ratio,
        ),
    )


_TOKEN_RE = re.compile(r"\w+")


class KeywordRubricJudge:
    """确定性关键词 judge（M2-10 内置零依赖实现，judge_model=keyword-v1）。

    rubric 点的每个 token（≥2 字符）都出现在作答中 → 该点达成；达成比例即置信度
    （全达成 = 1.0，无需双审）。机械字符串比对不引用课程事实，故不产生 evidence_id；
    中文点文本按整串匹配，局限由 LLM judge 替换后消除（协议一致，结果留痕可追溯）。
    """

    judge_model = "keyword-v1"
    prompt_hash = "keyword-v1"

    def judge(
        self, stem: str, rubric_points: tuple[str, ...], given: str
    ) -> RubricJudgement | None:
        answer = given.strip().lower()
        if not answer:
            return None
        criteria: list[RubricCriterion] = []
        for point in rubric_points:
            tokens = [t for t in _TOKEN_RE.findall(point.lower()) if len(t) >= 2]
            achieved = bool(tokens) and all(token in answer for token in tokens)
            criteria.append(RubricCriterion(point=point, achieved=achieved))
        hits = sum(1 for c in criteria if c.achieved)
        confidence = hits / len(criteria) if criteria else 0.0
        raw = json.dumps(
            {
                "criteria": [
                    {"point": c.point, "achieved": c.achieved, "evidence_id": None}
                    for c in criteria
                ],
                "confidence": confidence,
                "judge_model": self.judge_model,
                "prompt_hash": self.prompt_hash,
            },
            ensure_ascii=False,
        )
        return RubricJudgement(
            criteria=tuple(criteria),
            confidence=confidence,
            judge_model=self.judge_model,
            prompt_hash=self.prompt_hash,
            raw=raw,
        )


def make_rubric_judge(name: str | None) -> RubricJudge | None:
    """按部署配置选择 judge 实现：keyword=内置确定性实现；空/未知 → 无 judge（复核）。"""
    if name == "keyword":
        return KeywordRubricJudge()
    return None


def _parse_rubric_points(expected: str) -> tuple[str, ...]:
    """essay answer 两种形态：导入 JSON {"rubric_points": [...]} 或 legacy 裸串单点。"""
    try:
        data = json.loads(expected) if expected.strip().startswith("{") else None
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and isinstance(data.get("rubric_points"), list):
        return tuple(str(point) for point in data["rubric_points"] if str(point).strip())
    return (expected,) if expected.strip() else ()


def judge_question(
    question_type: str,
    stem: str,
    expected: str,
    given: str,
    *,
    rubric_judge: RubricJudge | None = None,
    valid_evidence_ids: frozenset[str] | None = None,
) -> tuple[bool | None, RubricResult | None]:
    """统一判分入口：objective 走 grade_answer；essay 走 rubric 管线（M2-10）。

    返回 (correct, rubric_result)；rubric_result 非 None 表示该题经 rubric 判分。
    """
    if normalize_type(question_type) != "essay":
        return grade_answer(question_type, expected, given), None

    points = _parse_rubric_points(expected)
    first = rubric_judge.judge(stem, points, given) if rubric_judge else None
    second = None
    if first is not None and first.confidence < RUBRIC_CONFIDENCE_FLOOR:
        # 低置信度双审：内置确定性 judge 重审收敛；LLM judge 部署后应换模型/提示再审
        second = rubric_judge.judge(stem, points, given)
    result = grade_rubric(points, first, second, valid_evidence_ids=valid_evidence_ids)
    return result.correct, result
