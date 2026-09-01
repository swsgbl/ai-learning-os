"""M4-03 VoiceSession FSM：语音会话 8 状态全迁移校验（G 提示词）。

状态：SESSION_READY → READING_QUESTION → READING_OPTIONS → WAITING_ANSWER
     →（answer_proposed 确定性提交）→ ANSWER_COMMITTED →（确认）NEXT_QUESTION
     →（start_reading 读下题循环 / report_ready 全卷完成）→ REPORT_READY 终态。
CLARIFYING：答案含糊时进入（answer_clarify），学生回应后重新 propose。

边界（与 M2-03 exam FSM 衔接）：
- 只有 WAITING_ANSWER / CLARIFYING / ANSWER_COMMITTED 接受 answer_proposed——
  读题/读选项播报期收到答案一律拒绝（打断不提交半成品答案）；
- ANSWER_COMMITTED 后 answer_proposed = 覆盖提交（「我改成 C」→ 追加覆盖事件，
  exam answer_events 的追加式序号天然支持覆盖）；
- repeat_question / repeat_options / slow_down 为播报控制自环，不破坏状态；
- pause / resume 为播报控制自环（M4-06）：任意非终态可暂停/恢复，状态不变
  （TTS 停播是客户端行为，服务端只留 revision 审计痕迹）；
- barge_in（M4-06）：显式打断事件——播报期（READING_QUESTION / READING_OPTIONS）
  → WAITING_ANSWER（停止 TTS、保留当前题、进入倾听）；倾听期自环；其余拒绝。
  提交必须走 propose 路径，打断信号本身绝不携带/提交答案；
- end 命令与全卷完成（report_ready）均进 REPORT_READY——报告内容由 M4-08 生成。

同 (状态, 事件) 恒同输出——与 ADR 27/29/30/31 同款幂等语义。
"""
from __future__ import annotations

SESSION_READY = "SESSION_READY"
READING_QUESTION = "READING_QUESTION"
READING_OPTIONS = "READING_OPTIONS"
WAITING_ANSWER = "WAITING_ANSWER"
CLARIFYING = "CLARIFYING"
ANSWER_COMMITTED = "ANSWER_COMMITTED"
NEXT_QUESTION = "NEXT_QUESTION"
REPORT_READY = "REPORT_READY"

VOICE_SESSION_STATES = (
    SESSION_READY,
    READING_QUESTION,
    READING_OPTIONS,
    WAITING_ANSWER,
    CLARIFYING,
    ANSWER_COMMITTED,
    NEXT_QUESTION,
    REPORT_READY,
)

EV_START_READING = "start_reading"
EV_QUESTION_READ = "question_read"
EV_OPTIONS_READ = "options_read"
EV_ANSWER_PROPOSED = "answer_proposed"  # 明确答案：服务端确定性落库
EV_ANSWER_CLARIFY = "answer_clarify"  # 含糊答案：进澄清环节
EV_COMMIT_CONFIRMED = "commit_confirmed"  # 确认所答，推进下一题环节
EV_SKIP = "skip"
EV_REPEAT_QUESTION = "repeat_question"
EV_REPEAT_OPTIONS = "repeat_options"
EV_SLOW_DOWN = "slow_down"
EV_END = "end"
EV_PAUSE = "pause"  # M4-06 播报控制：任意非终态自环
EV_RESUME = "resume"  # M4-06 播报控制：任意非终态自环
EV_BARGE_IN = "barge_in"  # M4-06 显式打断：播报期 → 倾听期
EV_REPORT_READY = "report_ready"  # 全卷完成（无下题）

EVENTS = (
    EV_START_READING,
    EV_QUESTION_READ,
    EV_OPTIONS_READ,
    EV_ANSWER_PROPOSED,
    EV_ANSWER_CLARIFY,
    EV_COMMIT_CONFIRMED,
    EV_SKIP,
    EV_REPEAT_QUESTION,
    EV_REPEAT_OPTIONS,
    EV_SLOW_DOWN,
    EV_END,
    EV_PAUSE,
    EV_RESUME,
    EV_BARGE_IN,
    EV_REPORT_READY,
)

# 迁移矩阵：状态 × 事件 → 目标状态（自环=播报控制/澄清等待/覆盖提交）。
_TRANSITIONS: dict[str, dict[str, str]] = {
    SESSION_READY: {
        EV_START_READING: READING_QUESTION,
        EV_END: REPORT_READY,
        EV_PAUSE: SESSION_READY,
        EV_RESUME: SESSION_READY,
    },
    READING_QUESTION: {
        EV_QUESTION_READ: READING_OPTIONS,
        EV_REPEAT_QUESTION: READING_QUESTION,
        EV_SLOW_DOWN: READING_QUESTION,
        EV_BARGE_IN: WAITING_ANSWER,  # 打断：停止播报进倾听，当前题保留
        EV_PAUSE: READING_QUESTION,
        EV_RESUME: READING_QUESTION,
        EV_END: REPORT_READY,
    },
    READING_OPTIONS: {
        EV_OPTIONS_READ: WAITING_ANSWER,
        EV_REPEAT_OPTIONS: READING_OPTIONS,
        EV_SLOW_DOWN: READING_OPTIONS,
        EV_BARGE_IN: WAITING_ANSWER,  # 打断：停止播报进倾听，当前题保留
        EV_PAUSE: READING_OPTIONS,
        EV_RESUME: READING_OPTIONS,
        EV_END: REPORT_READY,
    },
    WAITING_ANSWER: {
        EV_ANSWER_PROPOSED: ANSWER_COMMITTED,
        EV_ANSWER_CLARIFY: CLARIFYING,
        EV_SKIP: NEXT_QUESTION,
        EV_REPEAT_QUESTION: WAITING_ANSWER,
        EV_REPEAT_OPTIONS: WAITING_ANSWER,
        EV_SLOW_DOWN: WAITING_ANSWER,
        EV_BARGE_IN: WAITING_ANSWER,  # 已在倾听：自环
        EV_PAUSE: WAITING_ANSWER,
        EV_RESUME: WAITING_ANSWER,
        EV_END: REPORT_READY,
    },
    CLARIFYING: {
        EV_ANSWER_PROPOSED: ANSWER_COMMITTED,  # 澄清回应明确 → 提交
        EV_ANSWER_CLARIFY: CLARIFYING,  # 仍含糊 → 继续澄清
        EV_SKIP: NEXT_QUESTION,
        EV_BARGE_IN: CLARIFYING,  # 打断澄清播报：继续倾听
        EV_PAUSE: CLARIFYING,
        EV_RESUME: CLARIFYING,
        EV_END: REPORT_READY,
    },
    ANSWER_COMMITTED: {
        EV_COMMIT_CONFIRMED: NEXT_QUESTION,
        EV_ANSWER_PROPOSED: ANSWER_COMMITTED,  # 覆盖提交（追加覆盖事件）
        EV_ANSWER_CLARIFY: CLARIFYING,  # committed 后反悔说含糊 → 回澄清
        EV_SKIP: NEXT_QUESTION,
        EV_REPEAT_QUESTION: ANSWER_COMMITTED,
        EV_REPEAT_OPTIONS: ANSWER_COMMITTED,
        EV_SLOW_DOWN: ANSWER_COMMITTED,
        EV_PAUSE: ANSWER_COMMITTED,
        EV_RESUME: ANSWER_COMMITTED,
        EV_END: REPORT_READY,
    },
    NEXT_QUESTION: {
        EV_START_READING: READING_QUESTION,
        EV_REPORT_READY: REPORT_READY,
        EV_END: REPORT_READY,
        EV_PAUSE: NEXT_QUESTION,
        EV_RESUME: NEXT_QUESTION,
    },
    REPORT_READY: {},  # 终态
}


def transition(state: str, event: str) -> str:
    """应用事件返回新状态；非法迁移抛 ValueError（调用方转 409）。

    幂等语义：同 (状态, 事件) 恒同输出；非法迁移不改状态直接报错。
    """
    table = _TRANSITIONS.get(state)
    if table is None:
        raise ValueError(f"未知语音会话状态: {state}")
    target = table.get(event)
    if target is None:
        raise ValueError(f"语音会话状态 {state} 不接受事件 {event}")
    return target


def is_terminal(state: str) -> bool:
    return state == REPORT_READY
