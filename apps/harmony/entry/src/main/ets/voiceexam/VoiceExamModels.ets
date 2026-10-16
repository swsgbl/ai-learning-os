/** VoiceExam 页面阶段状态模型(M14-277,从页面拆出保持单文件行数约束) */
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
