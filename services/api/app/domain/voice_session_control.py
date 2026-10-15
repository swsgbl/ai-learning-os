"""M14-271：VAD/barge-in/pause-resume 服务端权威会话控制（纯域，fake-only）。

M14-270 之后的下一个窄控制件：在 M4-03 VoiceSession FSM（既有
权威迁移矩阵）之上包一层**服务端权威会话控制器**——打断
（barge-in）与恢复（pause/resume）只经服务端 ``submit`` 发生，
每次迁移带 revision 乐观并发与有界审计；VAD 语音活动信号
（speech_start/speech_end）映射为 FSM 事件（检测器本身——能量
阈值/模型——属真实接线切片，本模块只定义信号→事件契约）。
**客户端只消费状态**（snapshot 投影——本模块零客户端概念、
零 HTTP 路由、不进 lifespan）；Web/Android/Harmony 对齐同一
API 契约属后续 HTTP 接线切片，三端零触碰。

设计契约：
- **服务端权威**：状态迁移唯一入口 ``submit(event, *,
  revision=None)``——迁移判据完全复用 M4-03 ``transition``
  （零重复矩阵）；非法迁移确定性拒绝（状态不变、零审计残留）；
- **事件幂等**：同 (状态, 事件) 恒同输出（FSM 确定性 + 自环
  事件每次记审计——重复 pause 状态稳定）；VAD speech_start 在
  倾听期为幂等自环、在播报期为 barge-in 迁移；speech_end 恒零
  迁移（只记审计——答案提交属 ASR/transcript 链）；
- **revision 乐观并发**：提交可带期望 revision——不匹配拒绝
  （状态/revision 均不变、零审计残留）；缺省直接应用；应用
  成功 revision +1；
- **审计有界净化**：环形保留最新 N 条（全量计数含被淘汰）；
  事件白名单恰 {seq, event, from_state, to_state, revision}——
  无凭据/无音频/无文本；seq 单调；快照全新拷贝；
- **终态封闭**：REPORT_READY 后一切事件与 VAD 信号确定性拒绝；
- **零副作用**：纯同步、零事件循环/线程/进程/定时器/网络/
  磁盘；运行时导入限定纯标准库 + 域内 FSM 模块。
"""
from __future__ import annotations

from collections import deque

from app.domain.voice_session_fsm import (
    EVENTS,
    REPORT_READY,
    VOICE_SESSION_STATES,
    transition,
)

#: 审计环形缓冲上限默认值。
DEFAULT_MAX_AUDIT_EVENTS = 64

#: VAD 语音活动信号常量（检测器实现属真实接线切片）。
VAD_SPEECH_START = "speech_start"
VAD_SPEECH_END = "speech_end"
_VAD_SIGNALS = (VAD_SPEECH_START, VAD_SPEECH_END)

#: 通用净化消息白名单（绝不携带状态外值/异常文本）。
_MESSAGE_INITIAL_STATE_INVALID = "初始状态非法：必须是已知 VoiceSession 状态"
_MESSAGE_MAX_AUDIT = "max_audit_events 必须为正"
_MESSAGE_EVENT_UNKNOWN = "事件非法：必须是已知 VoiceSession 事件"
_MESSAGE_VAD_SIGNAL_UNKNOWN = "VAD 信号非法：speech_start 或 speech_end"
_MESSAGE_REVISION_INVALID = "revision 必须为正整数"
_MESSAGE_REVISION_MISMATCH = "revision 不匹配：会话已被并发更新（请刷新状态重试）"
_MESSAGE_TRANSITION_REJECTED = "当前状态不接受该事件（迁移拒绝）"


class VoiceSessionControlError(RuntimeError):
    """会话控制错误（消息为通用白名单文案）。"""


class VoiceSessionController:
    """服务端权威 VoiceSession 控制器（FSM 包装 + revision + 审计）。

    ``submit`` 应用一个 FSM 事件；``submit_vad`` 把 VAD 信号映射
    为事件语义（speech_start → barge_in 判据 / speech_end → 零迁移
    审计）；``snapshot`` 给客户端消费的有界净化投影。
    """

    __slots__ = (
        "_audit",
        "_audit_count",
        "_audit_max",
        "_revision",
        "_seq",
        "_state",
    )

    def __init__(
        self,
        initial_state: str = "SESSION_READY",
        *,
        max_audit_events: int = DEFAULT_MAX_AUDIT_EVENTS,
    ) -> None:
        if initial_state not in VOICE_SESSION_STATES:
            raise VoiceSessionControlError(_MESSAGE_INITIAL_STATE_INVALID)
        if (
            not isinstance(max_audit_events, int)
            or isinstance(max_audit_events, bool)
            or max_audit_events <= 0
        ):
            raise VoiceSessionControlError(_MESSAGE_MAX_AUDIT)
        self._state = initial_state
        self._revision = 1
        self._audit_max = max_audit_events
        self._audit: deque[dict[str, object]] = deque(maxlen=max_audit_events)
        self._audit_count = 0
        self._seq = 0

    # --- 内部：审计 ---

    def _record(self, event: str, from_state: str, to_state: str) -> None:
        self._seq += 1
        self._audit_count += 1
        self._audit.append(
            {
                "seq": self._seq,
                "event": event,
                "from_state": from_state,
                "to_state": to_state,
                "revision": self._revision,
            }
        )

    def _check_revision(self, revision: int | None) -> None:
        if revision is None:
            return
        if (
            not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision <= 0
        ):
            raise VoiceSessionControlError(_MESSAGE_REVISION_INVALID)
        if revision != self._revision:
            raise VoiceSessionControlError(_MESSAGE_REVISION_MISMATCH)

    def _apply(self, event: str, *, audit_name: str | None = None) -> dict[str, object]:
        """经 FSM 权威迁移应用事件（拒绝时状态不变、零审计残留）。

        audit_name 允许 VAD 信号路径在审计中记录信号语义
        （vad_speech_start）而非映射后的事件名（barge_in）。
        """
        from_state = self._state
        try:
            to_state = transition(from_state, event)
        except ValueError:
            raise VoiceSessionControlError(_MESSAGE_TRANSITION_REJECTED) from None
        self._state = to_state
        self._revision += 1
        self._record(audit_name or event, from_state, to_state)
        return {"state": self._state, "revision": self._revision, "applied": True}

    # --- 公共接口 ---

    def submit(self, event: str, *, revision: int | None = None) -> dict[str, object]:
        """应用一个 FSM 事件（服务端权威唯一入口）。

        事件名先经 EVENTS 白名单校验（未知事件确定性拒绝）；
        revision 可选乐观并发（不匹配拒绝且零残留）；终态封闭
        （REPORT_READY 一切拒绝）。
        """
        if event not in EVENTS:
            raise VoiceSessionControlError(_MESSAGE_EVENT_UNKNOWN)
        if self._state == REPORT_READY:
            raise VoiceSessionControlError(_MESSAGE_TRANSITION_REJECTED)
        self._check_revision(revision)
        return self._apply(event)

    def submit_vad(self, signal: str) -> dict[str, object]:
        """VAD 语音活动信号 → FSM 事件语义映射（零检测器逻辑）。

        - ``speech_start``：播报期等价 ``barge_in``（停止播报进
          倾听）；倾听期为幂等自环（仍记审计）；
        - ``speech_end``：恒零状态迁移（只记审计——答案提交属
          ASR/transcript 链，VAD 不携带答案语义）；
        - 终态封闭：REPORT_READY 一切信号拒绝。
        """
        if signal not in _VAD_SIGNALS:
            raise VoiceSessionControlError(_MESSAGE_VAD_SIGNAL_UNKNOWN)
        if self._state == REPORT_READY:
            raise VoiceSessionControlError(_MESSAGE_TRANSITION_REJECTED)
        if signal == VAD_SPEECH_START:
            return self._apply("barge_in", audit_name="vad_speech_start")
        # speech_end：零迁移审计（from == to）
        self._seq += 1
        self._audit_count += 1
        self._audit.append(
            {
                "seq": self._seq,
                "event": "vad_speech_end",
                "from_state": self._state,
                "to_state": self._state,
                "revision": self._revision,
            }
        )
        return {"state": self._state, "revision": self._revision, "applied": False}

    def snapshot(self) -> dict[str, object]:
        """客户端消费的有界净化投影（全新拷贝）。"""
        return {
            "state": self._state,
            "revision": self._revision,
            "is_terminal": self._state == REPORT_READY,
            "max_audit_events": self._audit_max,
            "audit_event_count": self._audit_count,
            "audit_events": [dict(event) for event in self._audit],
        }


__all__ = [
    "DEFAULT_MAX_AUDIT_EVENTS",
    "VAD_SPEECH_END",
    "VAD_SPEECH_START",
    "VoiceSessionControlError",
    "VoiceSessionController",
]
