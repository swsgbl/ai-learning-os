/**
 * VoiceExamPage.ets — 语音作答页面(M14-277)
 *
 * 会话流(服务端权威,本页不推演状态):
 *   选卷 → createVoiceSession → resume 读题 → 每题:录音(WavRecorder)→
 *   transcribeWav → 展示转写 → postAnswerProposed → resume 下一题 →
 *   完卷(question null)→ getVoiceReport 报告。
 *
 * 竞态防护(VoicePane 同款):disposed + requestGeneration;
 * 音频仅在内存短暂存在(上传后即弃,不落盘);
 * 麦克风权限拒绝 = 功能不可用态(如实展示,不重试)。
 * 视图组件(选卷/题目/操作/报告)拆至 voiceexam/VoiceExamViews.ets。
 */

import { common } from '@kit.AbilityKit';
import { PaperOut, getPapers } from '../AiosApi';
import { submitExam } from '../ExamApi';
import {
  VoiceResumeOut, VoiceReportOut, createVoiceSession, resumeVoiceSession,
  postAnswerProposed, getVoiceReport, transcribeWav
} from '../voiceexam/VoiceExamApi';
import { WavRecorderSession, requestMicPermission } from '../voiceexam/WavRecorder';
import { VoicePickPane, VoiceReportPane, UtterancePane, QuestionPane } from '../voiceexam/VoiceExamViews';
import { Phase, TurnState, statusLabel, hintFor } from '../voiceexam/VoiceExamModels';
import { VoiceCommandPane, VoiceResumeCard } from '../voiceexam/VoiceExamViews';
import {
  VOICE_CMD_START_READING, VOICE_CMD_COMMIT_CONFIRMED, VOICE_CMD_REPORT_READY,
  VOICE_CMD_SKIP, VOICE_CMD_END, sendVoiceCommand
} from '../voiceexam/VoiceExamApi';
import {
  VoiceResumeSlot, loadVoiceResumeSlot, saveVoiceResumeSlot, clearVoiceResumeSlot
} from '../voiceexam/VoiceResumeStore';
import { DEFAULT_API_BASE_URL, loadBaseUrl } from '../components/SettingsStore';

@Entry
@Component
struct VoiceExamPage {
  @State phase: Phase = Phase.PICK;
  @State turn: TurnState = TurnState.IDLE;
  @State papers: PaperOut[] = [];
  @State pickError: string = '';
  @State pickLoading: boolean = false;
  @State baseUrl: string = DEFAULT_API_BASE_URL;
  /** 麦克风权限不可用原因('' = 可用) */
  @State micUnavailable: string = '';
  @State sessionId: string = '';
  @State examId: string = '';
  /** 当前题投影与进度 */
  @State questionStem: string = '';
  @State questionOptions: string = '';
  @State questionIndex: number = 0;
  @State questionTotal: number = 0;
  /** 当前题 id(R1:answer_proposed 需要服务端契约 question_id) */
  @State questionId: string = '';
  @State sessionStatus: string = '';
  /** 当前题转写草稿(待提交) */
  @State transcriptDraft: string = '';
  @State lastCommitted: string = '';
  @State flowError: string = '';
  @State busyHint: string = '';
  @State report: VoiceReportOut | null = null;
  // ── hm-parity v5:会话恢复槽(本地只存 sessionId+examId 两键,服务端唯一真相) ──
  @State resumeSlot: VoiceResumeSlot | null = null;
  @State resuming: boolean = false;

  private disposed: boolean = false;
  private requestGeneration: number = 0;
  private recorder: WavRecorderSession | null = null; // 录音会话(null=未在录)

  async aboutToAppear(): Promise<void> {
    const ctx: common.Context | undefined =
      this.getUIContext().getHostContext() as common.Context | undefined;
    if (ctx === undefined || ctx === null) {
      this.micUnavailable = '无法获取应用上下文,语音作答不可用';
      return;
    }
    // 并行:权限申请 + 配置读取;权限拒绝如实进入不可用态
    this.micUnavailable = await requestMicPermission(ctx);
    const url: string = await loadBaseUrl(ctx);
    if (this.disposed) {
      return;
    }
    this.baseUrl = url;
    // hm-parity v5:读取恢复槽(有未收口会话时选卷区显示「继续上次」入口)
    this.resumeSlot = await loadVoiceResumeSlot(ctx);
    if (this.micUnavailable.length === 0) {
      this.loadPapers();
    }
  }

  aboutToDisappear(): void {
    this.disposed = true;
    this.releaseRecorder();
  }

  private releaseRecorder(): void {
    if (this.recorder !== null) {
      const r: WavRecorderSession = this.recorder;
      this.recorder = null;
      r.abort().catch(() => {
      });
    }
  }

  /** 加载试卷列表(每次进入选卷阶段可刷新) */
  private async loadPapers(): Promise<void> {
    if (this.disposed) {
      return;
    }
    const gen: number = ++this.requestGeneration;
    this.pickLoading = true;
    this.pickError = '';
    const r = await getPapers(this.baseUrl);
    if (this.disposed || gen !== this.requestGeneration) {
      return;
    }
    this.pickLoading = false;
    if (!r.ok || r.data === null) {
      this.pickError = r.error;
      return;
    }
    this.papers = r.data;
  }

  /** 开始某卷的语音会话:create → resume 对齐 → ACTIVE */
  private async startExam(paperId: string): Promise<void> {
    if (this.disposed) {
      return;
    }
    const gen: number = ++this.requestGeneration;
    this.flowError = '';
    this.busyHint = '正在创建语音会话...';
    const created = await createVoiceSession(this.baseUrl, paperId);
    if (this.disposed || gen !== this.requestGeneration) {
      return;
    }
    if (!created.ok || created.data === null) {
      this.busyHint = '';
      this.flowError = created.error;
      return;
    }
    this.sessionId = created.data.session_id;
    this.examId = created.data.exam_id;
    // hm-parity v5:开新会话成功 → 覆盖保存恢复槽
    const slotCtx: common.Context | undefined =
      this.getUIContext().getHostContext() as common.Context | undefined;
    if (slotCtx !== undefined && slotCtx !== null) {
      await saveVoiceResumeSlot(slotCtx, { sessionId: this.sessionId, examId: this.examId });
      this.resumeSlot = { sessionId: this.sessionId, examId: this.examId };
    }
    await this.alignResume(gen);
  }

  /** resume 对齐:读当前题 + 进度;完卷(question null)自动转 REPORT */
  private async alignResume(gen: number): Promise<void> {
    this.busyHint = '正在同步当前题目...';
    const r = await resumeVoiceSession(this.baseUrl, this.sessionId);
    if (this.disposed || gen !== this.requestGeneration) {
      return;
    }
    this.busyHint = '';
    if (!r.ok || r.data === null) {
      this.flowError = r.error;
      // hm-parity v5:404(会话不存在)/409(已终态)→ 清除恢复槽
      if (r.status === 404 || r.status === 409) {
        await this.clearSlot();
        if (this.phase === Phase.PICK) {
          return; // 选卷区恢复失败:槽已清,留在选卷
        }
      }
      return;
    }
    const d: VoiceResumeOut = r.data;
    this.sessionStatus = d.session.status;
    this.questionIndex = d.session.question_index;
    this.questionTotal = d.question_total;
    this.lastCommitted = d.committed_answer !== null ? d.committed_answer : '';
    if (d.question === null) {
      // 无当前题:完卷,取报告
      this.phase = Phase.REPORT;
      await this.loadReport(gen);
      return;
    }
    this.questionStem = d.question.stem;
    this.questionId = d.question.id;
    const opts: string[] = [];
    for (const o of d.question.options) {
      opts.push(`${o.key}. ${o.text}`);
    }
    this.questionOptions = opts.join('\n');
    this.transcriptDraft = '';
    this.turn = TurnState.IDLE;
    this.phase = Phase.ACTIVE;
  }

  /** 开始录音 */
  private async startRecording(): Promise<void> {
    if (this.recorder !== null || this.turn !== TurnState.IDLE) {
      return;
    }
    this.flowError = '';
    this.recorder = new WavRecorderSession();
    try {
      await this.recorder.start();
      if (this.disposed) {
        this.releaseRecorder();
        return;
      }
      this.turn = TurnState.RECORDING;
    } catch (e) {
      this.recorder = null;
      this.flowError = `${e instanceof Error ? e.message : `${e}`}`;
    }
  }

  /** 停止录音 → 上传转写 → DRAFT */
  private async stopAndTranscribe(): Promise<void> {
    if (this.turn !== TurnState.RECORDING || this.recorder === null) {
      return;
    }
    const gen: number = ++this.requestGeneration;
    this.turn = TurnState.TRANSCRIBING;
    this.busyHint = '正在识别...';
    let wav: Uint8Array;
    try {
      wav = await this.recorder.stop();
    } catch (e) {
      this.recorder = null;
      this.turn = TurnState.IDLE;
      this.busyHint = '';
      this.flowError = `${e instanceof Error ? e.message : `${e}`}`;
      return;
    }
    this.recorder = null;
    const tr = await transcribeWav(this.baseUrl, wav);
    wav = new Uint8Array(0);
    if (this.disposed || gen !== this.requestGeneration) {
      return;
    }
    this.busyHint = '';
    if (!tr.ok || tr.data === null) {
      this.turn = TurnState.IDLE;
      this.flowError = `识别失败,可重录: ${tr.error}`;
      return;
    }
    this.transcriptDraft = tr.data.text;
    this.turn = TurnState.DRAFT;
  }

  /** 提交答案(answer_proposed)→ 展示新状态 → resume 下一题 */
  private async submitAnswer(): Promise<void> {
    if (this.turn !== TurnState.DRAFT) {
      return;
    }
    const gen: number = ++this.requestGeneration;
    this.turn = TurnState.SUBMITTING;
    this.busyHint = '正在提交答案...';
    const r = await postAnswerProposed(this.baseUrl, this.sessionId, this.questionId, this.transcriptDraft);
    if (this.disposed || gen !== this.requestGeneration) {
      return;
    }
    this.busyHint = '';
    if (!r.ok || r.data === null) {
      this.turn = TurnState.DRAFT;
      this.flowError = `提交失败(HTTP ${r.status}),可重试`;
      return;
    }
    this.sessionStatus = r.data.session.status;
    await this.alignResume(gen);
  }

  /** 手动交卷(用户点「交卷」):直接取报告(已答内容以服务端为准) */
  private async finishExam(): Promise<void> {
    const gen: number = ++this.requestGeneration;
    this.releaseRecorder();
    this.phase = Phase.REPORT;
    await this.loadReport(gen);
  }

  private async loadReport(gen: number): Promise<void> {
    this.busyHint = '正在提交判分...';
    // hm-parity v5:voice report 前置考试已判分——先 submit(已提交则 409,忽略)
    await submitExam(this.baseUrl, this.examId);
    if (this.disposed || gen !== this.requestGeneration) {
      return;
    }
    this.busyHint = '正在生成报告...';
    const r = await getVoiceReport(this.baseUrl, this.sessionId);
    if (this.disposed || gen !== this.requestGeneration) {
      return;
    }
    this.busyHint = '';
    if (!r.ok || r.data === null) {
      this.flowError = `报告获取失败: ${r.error}`;
      return;
    }
    this.report = r.data;
    // hm-parity v5:报告已获取 → 清除恢复槽(会话收口)
    await this.clearSlot();
  }

  // ───────────────── hm-parity v5:恢复槽动作 ─────────────────
  /** 清除恢复槽(404/409/报告已取;静默失败——槽只是提示,不阻塞主流程) */
  private async clearSlot(): Promise<void> {
    const ctx: common.Context | undefined =
      this.getUIContext().getHostContext() as common.Context | undefined;
    if (ctx === undefined || ctx === null) {
      return;
    }
    await clearVoiceResumeSlot(ctx);
    this.resumeSlot = null;
  }

  /** 「继续上次语音作答」:本地槽只是提示,GET resume 对齐服务端真相 */
  private async resumeFromSlot(): Promise<void> {
    if (this.resumeSlot === null || this.resuming) {
      return;
    }
    this.resuming = true;
    this.flowError = '';
    const gen: number = ++this.requestGeneration;
    this.sessionId = this.resumeSlot.sessionId;
    this.examId = this.resumeSlot.examId;
    await this.alignResume(gen);
    if (!this.disposed && gen === this.requestGeneration) {
      this.resuming = false;
      // 恢复成功(进入 ACTIVE/REPORT)→ 槽位保持(仍是活跃会话),下次收口时清
    }
  }

  /** 放弃恢复:清槽留在选卷(不动服务端会话——用户可稍后再处理) */
  private async dismissSlot(): Promise<void> {
    await this.clearSlot();
  }

  // ───────────── hm-parity v5:FSM 命令(服务端权威,8 命令透传) ─────────────
  /** 通用命令发送:成功更新状态,409/404 detail 如实展示后 resume 对齐 */
  private async sendCommand(type: string): Promise<void> {
    if (this.busyHint.length > 0 || this.sessionId.length === 0) {
      return;
    }
    const gen: number = ++this.requestGeneration;
    this.flowError = '';
    this.busyHint = '命令发送中...';
    const r = await sendVoiceCommand(this.baseUrl, this.sessionId, { type: type });
    if (this.disposed || gen !== this.requestGeneration) {
      return;
    }
    this.busyHint = '';
    if (!r.ok || r.data === null) {
      this.flowError = r.error;
      return;
    }
    this.sessionStatus = r.data.session.status;
    this.questionIndex = r.data.session.question_index;
    if (type === VOICE_CMD_END || type === VOICE_CMD_REPORT_READY) {
      // 收口命令:直接取报告(REPORT_READY 终态;end 亦到终态)
      this.phase = Phase.REPORT;
      await this.loadReport(gen);
      return;
    }
    // 其余命令:resume 对齐当前题(读题推进后题面/进度会变)
    await this.alignResume(gen);
  }

  build() {
    Column({ space: 12 }) {
      // 标题栏
      Row({ space: 12 }) {
        Button('返回')
          .fontSize(12)
          .height(28)
          .onClick(() => {
            this.releaseRecorder();
            this.getUIContext().getRouter().back();
          })
        Text('语音作答')
          .fontSize(20)
          .fontWeight(FontWeight.Bold)
          .layoutWeight(1)
        Text(this.sessionId.length > 0 ? `#${this.questionIndex + 1}/${this.questionTotal}` : '')
          .fontSize(12)
          .fontColor('#666666')
      }
      .width('100%')

      // 权限不可用态(整页遮断)
      if (this.micUnavailable.length > 0) {
        Text(this.micUnavailable)
          .fontSize(14)
          .fontColor('#c62828')
          .width('100%')
          .padding(16)
          .backgroundColor('#fff3f3')
          .borderRadius(8)
      } else if (this.phase === Phase.PICK) {
        this.PickView()
      } else if (this.phase === Phase.ACTIVE) {
        this.ActiveView()
      } else {
        this.ReportView()
      }
    }
    .width('100%')
    .height('100%')
    .padding(16)
    .alignItems(HorizontalAlign.Start)
  }
  @Builder
  PickView() {
    Column({ space: 12 }) {
      // hm-parity v5:恢复入口(有未收口会话槽时显示;服务端唯一真相)
      if (this.resumeSlot !== null) {
        VoiceResumeCard({
          sessionId: this.resumeSlot.sessionId,
          busy: this.resuming,
          onResume: () => {
            this.resumeFromSlot();
          },
          onDismiss: () => {
            this.dismissSlot();
          }
        })
      }
      VoicePickPane({
      papers: this.papers,
      loading: this.pickLoading,
      errorText: this.pickError,
      onPick: (paperId: string) => {
        this.startExam(paperId);
      },
      onRetry: () => {
        this.loadPapers();
      }
    })
    }
    .width('100%')
    .height('100%')
  }
  @Builder
  ActiveView() {
    Scroll() {
      Column({ space: 12 }) {
        QuestionPane({
          stem: this.questionStem,
          optionsText: this.questionOptions,
          index: this.questionIndex,
          total: this.questionTotal,
          status: statusLabel(this.sessionStatus) + (hintFor(this.sessionStatus).length > 0 ? '\n' + hintFor(this.sessionStatus) : ''),
          committed: this.lastCommitted
        })

        UtterancePane({
          draft: this.transcriptDraft,
          turnState: this.turn === TurnState.IDLE ? 0 : this.turn === TurnState.RECORDING ? 1
            : this.turn === TurnState.TRANSCRIBING ? 2 : this.turn === TurnState.DRAFT ? 3 : 4,
          onStartRecord: () => {
            this.startRecording();
          },
          onStopAndTranscribe: () => {
            this.stopAndTranscribe();
          },
          onDiscardDraft: () => {
            this.transcriptDraft = '';
            this.turn = TurnState.IDLE;
          },
          onSubmit: () => {
            this.submitAnswer();
          }
        })

        // hm-parity v5:FSM 命令按钮区(显隐矩阵对齐 Android AdvanceControls)
        VoiceCommandPane({
          status: this.sessionStatus,
          busy: this.busyHint.length > 0,
          onStartReading: () => {
            this.sendCommand(VOICE_CMD_START_READING);
          },
          onCommitConfirmed: () => {
            this.sendCommand(VOICE_CMD_COMMIT_CONFIRMED);
          },
          onReportReady: () => {
            this.sendCommand(VOICE_CMD_REPORT_READY);
          },
          onSkip: () => {
            this.sendCommand(VOICE_CMD_SKIP);
          },
          onEnd: () => {
            this.sendCommand(VOICE_CMD_END);
          },
          onRequestReport: () => {
            const gen: number = ++this.requestGeneration;
            this.phase = Phase.REPORT;
            this.loadReport(gen);
          }
        })

        if (this.busyHint.length > 0) {
          Text(this.busyHint)
            .fontSize(13)
            .fontColor('#1890ff')
            .width('100%')
        }
        if (this.flowError.length > 0) {
          Text(this.flowError)
            .fontSize(13)
            .fontColor('#c62828')
            .width('100%')
            .padding(10)
            .backgroundColor('#fff3f3')
            .borderRadius(6)
        }

        Button('交卷')
          .fontSize(13)
          .backgroundColor('#888888')
          .margin({ top: 12 })
          .onClick(() => {
            this.finishExam();
          })
      }
      .width('100%')
    }
    .layoutWeight(1)
  }
  @Builder
  ReportView() {
    VoiceReportPane({
      report: this.report,
      busyHint: this.busyHint,
      errorText: this.flowError,
      onRetry: () => {
        const gen: number = ++this.requestGeneration;
        this.loadReport(gen);
      },
      onNewExam: () => {
        this.report = null;
        this.sessionId = '';
        this.examId = '';
        this.flowError = '';
        this.phase = Phase.PICK;
        this.loadPapers();
      }
    })
  }
}
