"""M2-11 考试报告：总分、题分、概念分、错题、解析、补救任务和证据链接。

- 题分：答对 = max_score；objective 答错 = 0；rubric 题 = max_score × score_ratio；
  复核题（correct=None）不给分（score=None），沿用 ADR 21 三态语义。
- 概念分：按题目 knowledge（导入卷即 concept_ids）聚合；无概念的题归「未分类」，
  保证概念分区覆盖全卷；复核题计入 total 但不算入正确率分母。
- 错题 + 补救任务：错因（angles.mistake）诊断，补救任务 = 复习关联概念 + 变式练习
  （angles.variant），remediation_task_ids 指向任务下标（04 号文档 §2.13 Mistake 语义）。
- 证据链接：rubric 判定引用的 evidence_id 逐题与全卷汇总透出；无证据不虚构。
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass

from app.domain.models import Paper, SubmissionRecord

_UNCLASSIFIED = "未分类"


@dataclass(frozen=True, slots=True)
class ReportItem:
    """题分级报告：判定 + 题分 + 解析 + 证据。"""

    question_id: str
    sequence: int
    question_type: str
    stem: str
    given: str
    expected: str
    correct: bool | None
    score: float | None
    max_score: float
    explanation: str
    knowledge: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    score_ratio: float | None


@dataclass(frozen=True, slots=True)
class ConceptScore:
    """概念分：该概念下题目的判定聚合。"""

    concept: str
    correct: int
    total: int
    reviewed: int
    ratio: float | None  # 全部复核时为 None


@dataclass(frozen=True, slots=True)
class RemediationTask:
    """补救任务（04 号文档 §2.13 remediation_task_ids 的目标）。"""

    kind: str  # review_concept | variant_practice
    title: str
    detail: str
    question_id: str


@dataclass(frozen=True, slots=True)
class MistakeEntry:
    """错题条目：错因诊断 + 关联补救任务与证据。"""

    question_id: str
    stem: str
    given: str
    expected: str
    explanation: str
    diagnosis: str
    knowledge: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    remediation_task_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ExamReport:
    exam_id: str
    paper_title: str
    mode: str
    score: int  # 百分制（现有三态口径）
    score_earned: float
    score_max: float
    correct_count: int
    total_count: int
    reviewed_count: int
    items: tuple[ReportItem, ...]
    concepts: tuple[ConceptScore, ...]
    mistakes: tuple[MistakeEntry, ...]
    remediation_tasks: tuple[RemediationTask, ...]
    evidence_ids: tuple[str, ...]


def _rubric_of(item_json: str | None) -> dict | None:
    if not item_json:
        return None
    try:
        data = json.loads(item_json)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def build_report(submission: SubmissionRecord, paper: Paper) -> ExamReport:
    """从 submission 判定结果 + 试卷元数据聚合完整报告（纯函数）。"""
    questions = {question.id: question for question in paper.questions}
    items: list[ReportItem] = []
    concept_verdicts: dict[str, list[bool | None]] = defaultdict(list)

    for sequence, item in enumerate(submission.items, start=1):
        question = questions.get(item.question_id)
        max_score = question.score if question else 1.0
        rubric = _rubric_of(item.rubric_json)
        ratio = rubric.get("score_ratio") if rubric else None
        ratio = ratio if isinstance(ratio, int | float) else None
        if item.correct is None:
            score = None
        elif ratio is not None:
            score = round(max_score * ratio, 2)
        else:
            score = max_score if item.correct else 0.0
        evidence_ids = tuple(
            dict.fromkeys(
                criterion.get("evidence_id")
                for criterion in (rubric or {}).get("criteria", [])
                if criterion.get("evidence_id")
            )
        )
        knowledge = tuple(question.knowledge) if question else ()
        items.append(
            ReportItem(
                question_id=item.question_id,
                sequence=sequence,
                question_type=question.type if question else "",
                stem=question.stem if question else "",
                given=item.given,
                expected=item.expected,
                correct=item.correct,
                score=score,
                max_score=max_score,
                explanation=item.explanation,
                knowledge=knowledge,
                evidence_ids=evidence_ids,
                score_ratio=ratio,
            )
        )
        for concept in knowledge or (_UNCLASSIFIED,):
            concept_verdicts[concept].append(item.correct)

    concepts = tuple(
        ConceptScore(
            concept=concept,
            correct=sum(verdict is True for verdict in verdicts),
            total=len(verdicts),
            reviewed=sum(verdict is None for verdict in verdicts),
            ratio=(
                round(sum(verdict is True for verdict in verdicts) / decided, 4)
                if (decided := sum(verdict is not None for verdict in verdicts))
                else None
            ),
        )
        for concept, verdicts in sorted(concept_verdicts.items())
    )

    mistakes: list[MistakeEntry] = []
    tasks: list[RemediationTask] = []
    for item in items:
        if item.correct is not False:
            continue
        question = questions.get(item.question_id)
        task_ids: list[int] = []
        for concept in item.knowledge:
            task_ids.append(len(tasks))
            tasks.append(
                RemediationTask(
                    kind="review_concept",
                    title=f"复习概念「{concept}」",
                    detail=concept,
                    question_id=item.question_id,
                )
            )
        variant = question.angles.variant if question else ""
        if variant:
            task_ids.append(len(tasks))
            tasks.append(
                RemediationTask(
                    kind="variant_practice",
                    title="变式练习",
                    detail=variant,
                    question_id=item.question_id,
                )
            )
        mistakes.append(
            MistakeEntry(
                question_id=item.question_id,
                stem=item.stem,
                given=item.given,
                expected=item.expected,
                explanation=item.explanation,
                diagnosis=question.angles.mistake if question else "",
                knowledge=item.knowledge,
                evidence_ids=item.evidence_ids,
                remediation_task_ids=tuple(task_ids),
            )
        )

    return ExamReport(
        exam_id=submission.exam_id,
        paper_title=submission.paper_title,
        mode=submission.mode,
        score=submission.score,
        score_earned=round(sum(item.score for item in items if item.score is not None), 2),
        score_max=round(sum(item.max_score for item in items), 2),
        correct_count=submission.correct_count,
        total_count=submission.total_count,
        reviewed_count=sum(item.correct is None for item in submission.items),
        items=tuple(items),
        concepts=concepts,
        mistakes=tuple(mistakes),
        remediation_tasks=tuple(tasks),
        evidence_ids=tuple(
            dict.fromkeys(
                evidence_id for item in items for evidence_id in item.evidence_ids
            )
        ),
    )
