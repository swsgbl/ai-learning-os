"""M16-004 评估蓝图：等值前后测分配（升级方案 Phase 4 / Prompt 4）。

核心验收（方案 Phase 4）：**后测不可复用练习/前测原题作为成功证据**——
同一概念的前测与后测使用不同题目（等值性 MVP = 同概念同难度带内
不重叠分配；题目足够时按难度配平）。

纯函数无 IO：候选题池（concept_id → items[{item_id, difficulty, type}]）
→ 蓝图（pretest/posttest 分配 + 覆盖/缺口如实报告——题量不足标
partial 并列缺口，不虚报完整）。
"""
from __future__ import annotations

from dataclasses import dataclass

#: 每概念前测/后测各取的默认题量
ITEMS_PER_PHASE_PER_CONCEPT = 2
#: 参考前测/后测的题型（客观可确定性判分）。
#: M16-004b（安全审查修复）：以 domain/grading.normalize_type 为**单一
#: 事实源**——先归一（choice→mcq、tf→true_false、short→short_answer 等
#: alias）再比对规范集，消除蓝图白名单与判分器的 parser differential
#: （此前 "single"/"multiple" 不被 grading 认识会落入 return False 兜底，
#: 造成"不可判题型被记为答错"的虚假证据）。
#: essay/写作走 rubric 复核不在蓝图 MVP。
from app.domain.grading import normalize_type as _normalize_question_type

OBJECTIVE_TYPES: frozenset[str] = frozenset(
    {"mcq", "multiple_select", "true_false", "short_answer"}
)


def is_objective_type(question_type: str) -> bool:
    """题型是否客观可判（以 grading.normalize_type 归一后判定）。"""
    return _normalize_question_type(question_type) in OBJECTIVE_TYPES


@dataclass(frozen=True, slots=True)
class CandidateItem:
    item_id: str
    concept_id: str
    difficulty: int = 3
    question_type: str = "single"


@dataclass(frozen=True, slots=True)
class ConceptPlan:
    concept_id: str
    pretest_item_ids: tuple[str, ...]
    posttest_item_ids: tuple[str, ...]
    # 等值性/覆盖如实报告：True = 前后测齐全且不重叠
    complete: bool
    note: str = ""


@dataclass(frozen=True, slots=True)
class Blueprint:
    plans: tuple[ConceptPlan, ...]
    complete: bool
    gaps: tuple[str, ...]  # 覆盖不足的概念说明（如 "c_x：候选仅 3 题不足 4")


def build_blueprint(
    candidates: list[CandidateItem],
    per_phase: int = ITEMS_PER_PHASE_PER_CONCEPT,
) -> Blueprint:
    """按概念分配等值前后测（不重叠；难度带内配平）。

    规则：
    1. 只收客观题型；
    2. 每概念需要 2×per_phase 道题（前测 per_phase + 后测 per_phase）；
    3. 难度配平：两阶段各取难度中位数最接近的半数（MVP：排序后奇偶
       交错分配——两阶段难度分布等值）；
    4. 题量不足 → 尽量平均分配并标 partial（complete=False，gaps 记录），
       绝不跨阶段复用同一题。
    """
    by_concept: dict[str, list[CandidateItem]] = {}
    for item in candidates:
        if not is_objective_type(item.question_type):
            continue
        by_concept.setdefault(item.concept_id, []).append(item)

    plans: list[ConceptPlan] = []
    gaps: list[str] = []
    for concept_id, items in sorted(by_concept.items()):
        needed = per_phase * 2
        if len(items) < needed:
            note = f"{concept_id}：候选 {len(items)} 题不足 {needed}"
            gaps.append(note)
        # 难度排序后奇偶交错分配（两阶段难度分布等值——MVP 等值策略）
        ordered = sorted(items, key=lambda i: (i.difficulty, i.item_id))
        pretest: list[str] = []
        posttest: list[str] = []
        for index, item in enumerate(ordered):
            if index % 2 == 0:
                target = pretest if len(pretest) < per_phase else posttest
            else:
                target = posttest if len(posttest) < per_phase else pretest
            if len(target) < per_phase:
                target.append(item.item_id)
        # 尾部溢出（奇偶约束下可能一边超 per_phase）：截齐不重叠
        pretest = pretest[:per_phase]
        posttest = posttest[:per_phase]
        complete = len(pretest) == per_phase and len(posttest) == per_phase
        plans.append(
            ConceptPlan(
                concept_id=concept_id,
                pretest_item_ids=tuple(pretest),
                posttest_item_ids=tuple(posttest),
                complete=complete,
                note="" if complete else f"覆盖不足（前测 {len(pretest)}/后测 {len(posttest)}）",
            )
        )

    return Blueprint(
        plans=tuple(plans),
        complete=bool(plans) and all(p.complete for p in plans),
        gaps=tuple(gaps),
    )


def blueprint_items(bp: Blueprint, phase: str) -> tuple[str, ...]:
    """取某阶段（pretest/posttest）的全部题 id（按概念稳定序）。"""
    if phase not in {"pretest", "posttest"}:
        raise ValueError(f"phase 必须是 pretest/posttest: {phase!r}")
    field_name = f"{phase}_item_ids"
    ids: list[str] = []
    for plan in bp.plans:
        ids.extend(getattr(plan, field_name))
    return tuple(ids)
