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
import {
  VoiceResumeOut, VoiceReportOut, createVoiceSession, resumeVoiceSession,
  postAnswerProposed, getVoiceReport, transcribeWav
} from '../voiceexam/VoiceExamApi';
import { WavRecorderSession, requestMicPermission } from '../voiceexam/WavRecorder';
import { VoicePickPane, VoiceReportPane, UtterancePane, QuestionPane } from '../voiceexam/VoiceExamViews';
import { Phase, TurnState } from '../voiceexam/VoiceExamModels';
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
  @Builder
  ActiveView() {
    Scroll() {
      Column({ space: 12 }) {
        QuestionPane({
          stem: this.questionStem,
          optionsText: this.questionOptions,
          index: this.questionIndex,
          total: this.questionTotal,
          status: this.sessionStatus,
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
