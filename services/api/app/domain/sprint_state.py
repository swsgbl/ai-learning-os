"""M16-001 学习 Sprint 状态机（升级方案 Phase 1 / Prompt 1）。

48 小时学习 Sprint 的领域状态：领域服务（本模块 + API 服务层）独占
推进权；LLM 只能提出下一动作（本阶段无 LLM 写路径）。设计原则：

- 合法转换白名单：只能沿主线前进，不支持跳段与回退（修改范围走
  plan version 递增，不覆盖旧计划）；
- 幂等：advance(同状态) 是合法 no-op——重试不产生重复状态/事件；
- 暂停/恢复独立于主线阶段：pause 可发生在任意非终态，resume 回到
  pause 前阶段，证据（LearningEvent）不因暂停丢失；
- 诚实终态：超出时间窗口不自动「掌握」——report 阶段按证据汇总
  未完成项（调用方负责，状态机只提供窗口判断）。
"""
from __future__ import annotations

from dataclasses import dataclass

SPRINT_PHASES: tuple[str, ...] = (
    "created",
    "contract_confirmed",
    "baseline_assessment",
    "concept_map_review",
    "plan_confirmed",
    "learning",
    "targeted_practice",
    "transfer_check",
    "posttest",
    "report",
    "follow_up",
)

#: 主线合法转换白名单（单向前进；follow_up 为终态）
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"contract_confirmed"}),
    "contract_confirmed": frozenset({"baseline_assessment"}),
    "baseline_assessment": frozenset({"concept_map_review"}),
    "concept_map_review": frozenset({"plan_confirmed"}),
    "plan_confirmed": frozenset({"learning"}),
    "learning": frozenset({"targeted_practice"}),
    "targeted_practice": frozenset({"transfer_check"}),
    "transfer_check": frozenset({"posttest"}),
    "posttest": frozenset({"report"}),
    "report": frozenset({"follow_up"}),
    "follow_up": frozenset(),
}

#: 暂停语义：主线进行中的阶段可暂停；终态与初始 created 不可暂停
PAUSABLE_PHASES: frozenset[str] = frozenset(set(SPRINT_PHASES) - {"created", "follow_up"})

#: 允许修改范围/确认计划的阶段（触发 plan version +1）
REPLANNABLE_PHASES: frozenset[str] = frozenset(
    {"contract_confirmed", "baseline_assessment", "concept_map_review", "plan_confirmed"}
)


class IllegalSprintTransition(ValueError):
    """非法状态转换（含非法暂停/恢复与未知阶段名）。"""


def is_known_phase(phase: str) -> bool:
    return phase in ALLOWED_TRANSITIONS


def can_advance(current: str, target: str) -> bool:
    """主线前进是否合法（同状态 = 幂等 no-op，视为合法）。"""
    if not is_known_phase(current) or not is_known_phase(target):
        return False
    return target == current or target in ALLOWED_TRANSITIONS[current]


def advance(current: str, target: str) -> str:
    """推进主线：非法转换抛 IllegalSprintTransition；同状态幂等返回原值。"""
    if not is_known_phase(current):
        raise IllegalSprintTransition(f"未知当前阶段: {current!r}")
    if not is_known_phase(target):
        raise IllegalSprintTransition(f"未知目标阶段: {target!r}")
    if target == current:
        return current  # 幂等重试：不产生新状态，不重复落事件
    if target not in ALLOWED_TRANSITIONS[current]:
        raise IllegalSprintTransition(
            f"非法转换: {current!r} → {target!r}（主线仅允许单向相邻前进）"
        )
    return target


def check_pausable(phase: str, *, paused: bool) -> None:
    """暂停/恢复的前置校验：非法组合抛 IllegalSprintTransition。"""
    if not is_known_phase(phase):
        raise IllegalSprintTransition(f"未知阶段: {phase!r}")
    if paused:
        return  # 已暂停状态下再 pause 视为幂等
    if phase not in PAUSABLE_PHASES:
        raise IllegalSprintTransition(f"阶段 {phase!r} 不允许暂停（仅进行中阶段可暂停）")


def check_resumable(phase: str, *, paused: bool) -> None:
    if not is_known_phase(phase):
        raise IllegalSprintTransition(f"未知阶段: {phase!r}")
    if not paused:
        raise IllegalSprintTransition("Sprint 未处于暂停状态，无法恢复")


def check_replannable(phase: str) -> None:
    """修改范围/重新确认计划的前置校验（学习进行中不允许——走新 Sprint）。"""
    if not is_known_phase(phase):
        raise IllegalSprintTransition(f"未知阶段: {phase!r}")
    if phase not in REPLANNABLE_PHASES:
        raise IllegalSprintTransition(
            f"阶段 {phase!r} 不允许修改范围（进入学习后请创建新 Sprint）"
        )


@dataclass(frozen=True, slots=True)
class SprintWindow:
    """时间窗口判断：48h 窗口到期不虚构掌握结论（report 如实汇总）。"""

    started_at: float  # epoch 秒
    budget_hours: float

    def is_expired(self, now: float) -> bool:
        return now - self.started_at > self.budget_hours * 3600.0
