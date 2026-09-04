"""M2-02 试卷 JSON 导入：事务落库，题目顺序/分值/答案/解析原样保持。"""
from __future__ import annotations

import json
import math
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.orm import PaperRow, QuestionRow
from app.domain.questions import PaperSpec

_MCQ_KEYS = "ABCDEFGH"
_EMPTY_ANGLES = {"concept": "", "method": "", "mistake": "", "variant": ""}


def _answer_to_legacy(q) -> str:
    """QuestionSpec.answer -> 既有考试流的 answer 字符串表示。"""
    data = q.answer.model_dump()
    if q.question_type == "mcq":
        return _MCQ_KEYS[data["option_index"]]
    if q.question_type == "multiple_select":
        return "".join(_MCQ_KEYS[i] for i in sorted(data["option_indices"]))
    if q.question_type == "true_false":
        return "T" if data["value"] else "F"
    return json.dumps(data, ensure_ascii=False)


def _options_to_legacy(q) -> list[dict]:
    return [{"key": _MCQ_KEYS[i], "text": text} for i, text in enumerate(q.options)]


async def import_papers(
    sessionmaker: async_sessionmaker[AsyncSession],
    specs: list[PaperSpec],
    owner_id: str | None = None,
) -> list[str]:
    """全部合法的 specs 一次性落库；返回 paper ids。

    M10-03: owner_id 给定时导入卷归属该用户（跨用户不可见）；None = 系统公共语义
    （auth off 本地调试模式）。
    """
    ids: list[str] = []
    async with sessionmaker() as session, session.begin():
        for spec in specs:
            paper_id = f"pap_{uuid.uuid4().hex}"
            ids.append(paper_id)
            session.add(PaperRow(
                id=paper_id,
                title=spec.title,
                subtitle="",
                source="imported",
                university=None,
                year=None,
                subject="imported",
                difficulty="unknown",
                duration_minutes=max(1, math.ceil(spec.duration_seconds / 60)),
                tags=[spec.policy],
                origin_url=None,
                license="UNKNOWN",
                owner_id=owner_id,
            ))
            for order, ref in enumerate(spec.questions, start=1):
                session.add(QuestionRow(
                    id=f"q_{uuid.uuid4().hex}",
                    paper_id=paper_id,
                    question_type=ref.question.question_type,
                    stem=ref.question.stem,
                    options=_options_to_legacy(ref.question),
                    answer=_answer_to_legacy(ref.question),
                    explanation=ref.question.explanation,
                    angles=dict(_EMPTY_ANGLES),
                    knowledge=list(ref.question.concept_ids),
                    score=ref.score,
                    difficulty=ref.question.difficulty,
                    sort_order=order,
                ))
    return ids
