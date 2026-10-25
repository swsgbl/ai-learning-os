/** VoiceExam 页面阶段状态模型(M14-277,从页面拆出保持单文件行数约束;
 *  hm-parity v5 增补 FSM 状态中文文案,逐条对齐 Android VoiceScreen.kt) */
/** 页面大阶段 */
export enum Phase {
  PICK = 'PICK',
  ACTIVE = 'ACTIVE',
  REPORT = 'REPORT'
}

/** 作答中的子阶段(一次 utterance 的生命周期) */
export enum TurnState {
  IDLE = 'IDLE',
  RECORDING = 'RECORDING',
  TRANSCRIBING = 'TRANSCRIBING',
  /** 转写完成,等待用户确认提交 */
  DRAFT = 'DRAFT',
  SUBMITTING = 'SUBMITTING'
}

/**
 * FSM 会话状态中文标签(hm-parity v5:照抄 Android VoiceScreen.kt
 * statusLabel,8 状态一一对应 services/api voice_session_fsm.py)
 */
export function statusLabel(status: string): string {
  switch (status) {
    case 'SESSION_READY':
      return '准备就绪';
    case 'READING_QUESTION':
      return '正在读题';
    case 'READING_OPTIONS':
      return '正在读选项';
    case 'WAITING_ANSWER':
      return '等待作答';
    case 'CLARIFYING':
      return '请再确认答案';
    case 'ANSWER_COMMITTED':
      return '已提交答案';
    case 'NEXT_QUESTION':
      return '本题完成';
    case 'REPORT_READY':
      return '全卷完成';
    default:
      return '进行中';
  }
}

/**
 * FSM 会话状态操作提示(hm-parity v5:照抄 Android VoiceScreen.kt
 * hintFor;6 个有操作语义的状态 + 其余空串)
 */
export function hintFor(status: string): string {
  switch (status) {
    case 'SESSION_READY':
      return '点击下方「开始读题」，按题听读作答。';
    case 'WAITING_ANSWER':
      return '口述、点选选项，或输入文字作答。';
    case 'CLARIFYING':
      return '直接点选项，或更明确地说出选项字母。';
    case 'ANSWER_COMMITTED':
      return '可改答案，或确认后进入下一题。';
    case 'NEXT_QUESTION':
      return '读下一题，或完成全卷生成报告。';
    case 'REPORT_READY':
      return '点击下方「生成报告」查看本次结果。';
    default:
      return '';
  }
}
