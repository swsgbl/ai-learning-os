"""M2-01 题型 schema：八种题型结构化校验（04 号文档 §2.6 question_type 枚举）。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

QUESTION_TYPES = (
    "mcq",
    "multiple_select",
    "true_false",
    "short_answer",
    "numeric",
    "math",
    "coding",
    "essay",
)


class McqAnswer(BaseModel):
    option_index: int = Field(ge=0)


class MultipleSelectAnswer(BaseModel):
    option_indices: list[int] = Field(min_length=2)


class TrueFalseAnswer(BaseModel):
    value: bool


class ShortAnswerAnswer(BaseModel):
    accepted: list[str] = Field(min_length=1)


class NumericAnswer(BaseModel):
    value: float
    tolerance: float = Field(default=0.0, ge=0)


class MathAnswer(BaseModel):
    latex: str = Field(min_length=1)


class CodingAnswer(BaseModel):
    reference_solution: str = Field(min_length=1)
    tests: list[str] = Field(min_length=1)


class EssayAnswer(BaseModel):
    rubric_points: list[str] = Field(min_length=1)


class QuestionSpec(BaseModel):
    """题目 schema：answer 结构随 question_type 判别校验。"""

    question_type: Literal[
        "mcq",
        "multiple_select",
        "true_false",
        "short_answer",
        "numeric",
        "math",
        "coding",
        "essay",
    ]
    stem: str = Field(min_length=1, max_length=4096)
    options: list[str] = Field(default_factory=list)
    difficulty: int = Field(default=3, ge=1, le=5)
    estimated_seconds: int = Field(default=60, ge=5, le=7200)
    concept_ids: list[str] = Field(default_factory=list)
    answer: McqAnswer | MultipleSelectAnswer | TrueFalseAnswer | ShortAnswerAnswer | NumericAnswer | MathAnswer | CodingAnswer | EssayAnswer
    explanation: str = Field(default="", max_length=4096)

    @model_validator(mode="after")
    def _check_options_match_type(self) -> QuestionSpec:
        if self.question_type in {"mcq", "multiple_select"}:
            if len(self.options) < 2:
                raise ValueError(f"{self.question_type} 至少需要 2 个选项")
        elif self.options:
            raise ValueError(f"{self.question_type} 不应有选项")
        if self.question_type == "mcq":
            if not isinstance(self.answer, McqAnswer):
                raise ValueError("mcq 答案必须是 option_index")
            if self.answer.option_index >= len(self.options):
                raise ValueError("option_index 超出选项范围")
        if self.question_type == "multiple_select":
            if not isinstance(self.answer, MultipleSelectAnswer):
                raise ValueError("multiple_select 答案必须是 option_indices")
            if any(i >= len(self.options) for i in self.answer.option_indices):
                raise ValueError("option_indices 超出选项范围")
        if self.question_type == "true_false" and not isinstance(self.answer, TrueFalseAnswer):
            raise ValueError("true_false 答案必须是 value: bool")
        if self.question_type == "short_answer" and not isinstance(self.answer, ShortAnswerAnswer):
            raise ValueError("short_answer 答案必须是 accepted 列表")
        if self.question_type == "numeric" and not isinstance(self.answer, NumericAnswer):
            raise ValueError("numeric 答案必须是 value + tolerance")
        if self.question_type == "math" and not isinstance(self.answer, MathAnswer):
            raise ValueError("math 答案必须是 latex")
        if self.question_type == "coding" and not isinstance(self.answer, CodingAnswer):
            raise ValueError("coding 答案必须是 reference_solution + tests")
        if self.question_type == "essay" and not isinstance(self.answer, EssayAnswer):
            raise ValueError("essay 答案必须是 rubric_points")
        return self


class PaperQuestionRef(BaseModel):
    """试卷内的题目引用（带分值与排序）。"""

    question: QuestionSpec
    score: float = Field(gt=0)


class PaperSpec(BaseModel):
    """试卷 schema（04 号文档 §2.7）：总分校验 + policy 枚举。"""

    title: str = Field(min_length=1, max_length=512)
    duration_seconds: int = Field(ge=60, le=86400)
    policy: Literal["practice", "exam", "adaptive"] = "exam"
    shuffle_policy: str = Field(default="none")
    generation_method: Literal["manual", "imported", "ai_generated", "hybrid"] = "manual"
    questions: list[PaperQuestionRef] = Field(min_length=1)
    total_score: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _check_total_score(self) -> PaperSpec:
        computed = sum(q.score for q in self.questions)
        if self.total_score is not None and abs(self.total_score - computed) > 1e-6:
            raise ValueError(
                f"total_score({self.total_score}) 与题目分值之和({computed})不一致"
            )
        return self
