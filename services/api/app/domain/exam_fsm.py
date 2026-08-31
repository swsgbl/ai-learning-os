"""M2-03 ExamSession FSM：CREATED 到 REPORT_READY 全状态迁移校验。

迁移矩阵（04 号文档考试生命周期）：
- CREATED -> ACTIVE            开始考试
- ACTIVE -> SUBMITTED | EXPIRED  主动交卷 / 服务端超时翻转
- SUBMITTED -> REPORT_READY    报告生成
- EXPIRED -> SUBMITTED | REPORT_READY  超时按 end_at 结算交卷 / 报告生成
- REPORT_READY 为终态
"""
from __future__ import annotations

from app.domain.models import ExamStatus

_TRANSITIONS: dict[ExamStatus, frozenset[ExamStatus]] = {
    ExamStatus.CREATED: frozenset({ExamStatus.ACTIVE}),
    ExamStatus.ACTIVE: frozenset({ExamStatus.SUBMITTED, ExamStatus.EXPIRED}),
    ExamStatus.SUBMITTED: frozenset({ExamStatus.REPORT_READY}),
    ExamStatus.EXPIRED: frozenset({ExamStatus.SUBMITTED, ExamStatus.REPORT_READY}),
    ExamStatus.REPORT_READY: frozenset(),
}


def can_transition(current: ExamStatus, target: ExamStatus) -> bool:
    return target in _TRANSITIONS[current]


def assert_transition(current: ExamStatus, target: ExamStatus) -> None:
    if not can_transition(current, target):
        raise ValueError(f"非法考试状态迁移: {current.value} -> {target.value}")
