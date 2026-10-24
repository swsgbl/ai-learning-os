/**
 * ExamPage.ets — 考试全流程页面(hm-parity v2,对齐 Android ExamScreen/ExamViewModel)
 *
 * 流程(router 参数 paperId/paperTitle,或无参进入选卷态):
 *   开考(POST /papers/{id}/exams) → 拉题(GET /exams/{id}) →
 *   逐题作答(PUT /exams/{id}/answers,append-only,服务端权威 sequence)→
 *   交卷确认(POST /exams/{id}/submit,服务端判分) →
 *   报告页(GET /exams/{id}/report,逐题对错+正确答案对比;expected 交卷后才下发)。
 *
 * 不变式:
 *   - 答案隔离:作答页绝无正确答案;报告页 expected 来自服务端交卷后数据;
 *   - 服务端权威时间:server_remaining_seconds 只读展示,本地不做倒计时;
 *   - sequence 权威:PUT 失败不消耗序号,重试沿用;每次作答=新事件(修订即追加);
 *   - 单选=选项 key(如 "A");多选={"option_indices":[...]} JSON 串(升序 0 基);
 *   - 无 options 题型(essay/short_answer/numeric)= 文本作答;
 *   - 交卷幂等:重复 submit 返回已有结果(不是 409);
 *   - 409/404/422 服务端 detail 原样展示;
 *   - 竞态防护:disposed + requestGeneration。
 */

import { common } from '@kit.AbilityKit';
import { PaperOut, getPapers, hasBearerToken } from '../AiosApi';
import {
  ExamOption, ExamQuestion, ExamReport, ExamSession, ReportItem,
  SubmissionResult, encodeMultiSelectAnswer, getExam, getExamReport,
  isMultipleSelect, isSingleChoice, putAnswer, readableAnswer, startExam,
  submitExam
} from '../ExamApi';
import { DEFAULT_API_BASE_URL, loadBaseUrl } from '../components/SettingsStore';

/** 页面阶段 */
enum ExamPhase {
  /** 选卷(无参进入) */
  PICK = 'PICK',
  /** 作答中 */
  EXAM = 'EXAM',
  /** 交卷确认弹层 */
  CONFIRM = 'CONFIRM',
  /** 报告 */
  REPORT = 'REPORT'
}

/** 单题本地草稿(已保存到服务端前的暂存) */
interface LocalDraft {
  questionId: string;
  /** 单选 key / 多选 indices 串 / 文本 */
  answer: string;
  /** 多选选中索引(0 基) */
  multiIndices: Array<number>;
  saved: boolean;
}

/** 概念掌握行(报告 concepts[],ArkTS 禁匿名对象类型) */
interface ConceptRow {
  concept: string;
  correct: number;
  total: number;
  reviewed: number;
  ratio: number | null;
}

/** 交卷判分条目(降级视图 ForEach 用,ArkTS 禁匿名对象类型) */
interface GradedRow {
  question_id: string;
  given: string;
  correct: boolean | null;
  expected: string;
  explanation: string;
}

@Entry
@Component
struct ExamPage {
  @State phase: ExamPhase = ExamPhase.PICK;
  @State baseUrl: string = DEFAULT_API_BASE_URL;
  @State busy: boolean = false;
  @State error: string = '';
  @State notice: string = '';

  // 选卷态
  @State papers: Array<PaperOut> = [];
  @State papersLoading: boolean = false;

  // 考试态
  @State session: ExamSession | null = null;
  /** 当前题索引(0 基) */
  @State currentIdx: number = 0;
  /** 本地草稿(questionId → draft;未保存服务端前暂存) */
  @State drafts: Array<LocalDraft> = [];
  /** 文本作答暂存 */
  @State textDraft: string = '';

  // 报告态
  @State report: ExamReport | null = null;
  @State submission: SubmissionResult | null = null;

  /** 试卷 id(从 router 参数或选卷获得) */
  private paperId: string = '';
  private paperTitle: string = '';
  private examId: string = '';

  private disposed: boolean = false;
  private requestGeneration: number = 0;

  async aboutToAppear(): Promise<void> {
    const ctx: common.Context | undefined =
      this.getUIContext().getHostContext() as common.Context | undefined;
    if (ctx === undefined || ctx === null) {
      this.error = '无法获取应用上下文,考试暂不可用';
      return;
    }
    this.baseUrl = await loadBaseUrl(ctx);
    if (this.disposed) {
      return;
    }
    // router 参数:paperId/paperTitle(StudyPane 卷卡「直接考试」进入)
    const params = this.getUIContext().getRouter().getParams() as Record<string, Object> | undefined;
    if (params !== undefined && params !== null) {
      const pid: Object | undefined = params['paperId'];
      if (typeof pid === 'string' && pid.length > 0) {
        this.paperId = pid;
      }
      const pt: Object | undefined = params['paperTitle'];
      if (typeof pt === 'string' && pt.length > 0) {
        this.paperTitle = pt;
      }
    }
    if (this.paperId.length > 0) {
      // 直接开考
      this.beginExam();
    } else {
      // 无参:选卷态
      this.loadPapers();
    }
  }

  aboutToDisappear(): void {
    this.disposed = true;
  }

  /** 选卷态:加载试卷列表 */
  private async loadPapers(): Promise<void> {
    if (this.disposed) {
      return;
    }
    const gen: number = ++this.requestGeneration;
    this.papersLoading = true;
    this.error = '';
    const r = await getPapers(this.baseUrl);
    if (this.disposed || gen !== this.requestGeneration) {
      return;
    }
    this.papersLoading = false;
    if (!r.ok || r.data === null) {
      this.error = r.error;
      return;
    }
    this.papers = r.data;
  }

  /** 开考:POST /papers/{id}/exams → 拉题进入作答 */
  private async beginExam(): Promise<void> {
    if (this.paperId.length === 0) {
      return;
    }
    await this.runBusy(async () => {
      const r = await startExam(this.baseUrl, this.paperId);
      if (!r.ok || r.data === null) {
        this.error = r.error;
        // 开考失败回选卷态(用户可重选)
        if (this.phase === ExamPhase.PICK && this.papers.length === 0) {
          this.loadPapers();
        }
        return;
      }
      this.examId = r.data.exam_id;
      this.applySession(r.data);
      this.phase = ExamPhase.EXAM;
    });
  }

  /** 应用服务端会话快照(开考/逐题作答响应共用;对齐本地草稿与服务端 answers) */
  private applySession(s: ExamSession): void {
    this.session = s;
    this.textDraft = '';
    const drafts: Array<LocalDraft> = [];
    for (let i: number = 0; i < s.questions.length; i++) {
      const q: ExamQuestion = s.questions[i];
      const saved: string | undefined = s.answers[q.id];
      drafts.push({
        questionId: q.id,
        answer: saved !== undefined ? saved : '',
        multiIndices: saved !== undefined ? this.decodeMulti(saved) : [],
        saved: saved !== undefined
      });
    }
    this.drafts = drafts;
    if (this.currentIdx >= s.questions.length) {
      this.currentIdx = Math.max(0, s.questions.length - 1);
    }
  }

  /** 解码 {"option_indices":[...]} → 索引数组(失败返回空) */
  private decodeMulti(raw: string): Array<number> {
    if (!raw.startsWith('{"option_indices"')) {
      return [];
    }
    try {
      const parsed = JSON.parse(raw) as Record<string, Object>;
      const idx: Object | undefined = parsed['option_indices'];
      if (Array.isArray(idx)) {
        const out: Array<number> = [];
        const arr: Array<Object> = idx as Array<Object>;
        for (let i: number = 0; i < arr.length; i++) {
          out.push(arr[i] as number);
        }
        return out;
      }
    } catch (e) {
    }
    return [];
  }

  /** 当前题 */
  private currentQuestion(): ExamQuestion | null {
    if (this.session === null || this.currentIdx >= this.session.questions.length) {
      return null;
    }
    return this.session.questions[this.currentIdx];
  }

  /** 当前题草稿 */
  private currentDraft(): LocalDraft | null {
    if (this.currentQuestion() === null) {
      return null;
    }
    const qid: string = this.currentQuestion()!.id;
    for (let i: number = 0; i < this.drafts.length; i++) {
      if (this.drafts[i].questionId === qid) {
        return this.drafts[i];
      }
    }
    return null;
  }

  /** 单选:点选即存草稿并保存 */
  private selectSingle(key: string): void {
    const q: ExamQuestion | null = this.currentQuestion();
    const d: LocalDraft | null = this.currentDraft();
    if (q === null || d === null) {
      return;
    }
    for (let i: number = 0; i < this.drafts.length; i++) {
      if (this.drafts[i].questionId === q.id) {
        this.drafts[i].answer = key;
        this.drafts[i].saved = false;
        break;
      }
    }
    this.drafts = this.drafts.slice();
    this.saveCurrentAnswer(key);
  }

  /** 多选:切换勾选(保存时编码) */
  private toggleMulti(index: number): void {
    const q: ExamQuestion | null = this.currentQuestion();
    const d: LocalDraft | null = this.currentDraft();
    if (q === null || d === null) {
      return;
    }
    const indices: Array<number> = d.multiIndices.slice();
    const pos: number = indices.indexOf(index);
    if (pos >= 0) {
      indices.splice(pos, 1);
    } else {
      indices.push(index);
    }
    for (let i: number = 0; i < this.drafts.length; i++) {
      if (this.drafts[i].questionId === q.id) {
        this.drafts[i].multiIndices = indices;
        this.drafts[i].answer = indices.length > 0 ? encodeMultiSelectAnswer(indices) : '';
        this.drafts[i].saved = false;
        break;
      }
    }
    this.drafts = this.drafts.slice();
  }

  /** 文本作答草稿变更 */
  private onTextChange(v: string): void {
    this.textDraft = v;
  }

  /** 文本作答保存 */
  private saveTextAnswer(): void {
    const answer: string = this.textDraft.trim();
    if (answer.length === 0) {
      this.error = '作答内容为空,请输入后再保存';
      return;
    }
    const q: ExamQuestion | null = this.currentQuestion();
    if (q === null) {
      return;
    }
    for (let i: number = 0; i < this.drafts.length; i++) {
      if (this.drafts[i].questionId === q.id) {
        this.drafts[i].answer = answer;
        this.drafts[i].saved = false;
        break;
      }
    }
    this.drafts = this.drafts.slice();
    this.saveCurrentAnswer(answer);
  }

  /** 保存当前题作答到服务端(PUT answers;失败不消耗序号,如实展示错误) */
  private async saveCurrentAnswer(answer: string): Promise<void> {
    const q: ExamQuestion | null = this.currentQuestion();
    if (q === null || this.session === null || answer.length === 0) {
      return;
    }
    await this.runBusy(async () => {
      const seq: number = this.session !== null ? this.session!.next_sequence : 1;
      const r = await putAnswer(this.baseUrl, this.examId, seq, q!.id, answer);
      if (!r.ok || r.data === null) {
        this.error = r.error;
        return;
      }
      this.applySession(r.data);
      this.notice = '作答已保存';
    });
  }

  /** 上一题/下一题(边界 clamp) */
  private goPrev(): void {
    if (this.currentIdx > 0) {
      this.currentIdx -= 1;
      this.syncTextDraft();
    }
  }

  private goNext(): void {
    if (this.session !== null && this.currentIdx < this.session.questions.length - 1) {
      this.currentIdx += 1;
      this.syncTextDraft();
    }
  }

  /** 切题时同步文本草稿(已保存值回显) */
  private syncTextDraft(): void {
    const d: LocalDraft | null = this.currentDraft();
    this.textDraft = d !== null ? d.answer : '';
  }

  /** 已答完题数 */
  private answeredCount(): number {
    let n: number = 0;
    for (let i: number = 0; i < this.drafts.length; i++) {
      if (this.drafts[i].saved && this.drafts[i].answer.length > 0) {
        n += 1;
      }
    }
    return n;
  }

  /** 全部已保存判定 */
  private allSaved(): boolean {
    if (this.session === null || this.drafts.length === 0) {
      return false;
    }
    return this.answeredCount() === this.session.questions.length;
  }

  /** 交卷确认 → 提交判分 → 拉报告 */
  private async confirmSubmit(): Promise<void> {
    this.phase = ExamPhase.EXAM;
    await this.runBusy(async () => {
      const r = await submitExam(this.baseUrl, this.examId);
      if (!r.ok || r.data === null) {
        this.error = r.error;
        return;
      }
      this.submission = r.data;
      // 报告优先;404(审阅报告尚未生成)降级为 submission 概要
      const rep = await getExamReport(this.baseUrl, this.examId);
      if (rep.ok && rep.data !== null) {
        this.report = rep.data;
      } else {
        this.report = null;
      }
      this.phase = ExamPhase.REPORT;
    });
  }

  /** busy 包裹 */
  private async runBusy(action: () => Promise<void>): Promise<void> {
    if (this.disposed || this.busy) {
      return;
    }
    this.busy = true;
    this.error = '';
    this.notice = '';
    try {
      await action();
    } finally {
      if (!this.disposed) {
        this.busy = false;
      }
    }
  }

  build() {
    Column({ space: 12 }) {
      // 标题栏
      Row({ space: 12 }) {
        if (this.phase !== ExamPhase.REPORT) {
          Button('退出')
            .fontSize(12)
            .height(28)
            .onClick(() => {
              this.getUIContext().getRouter().back();
            })
        }
        Text(this.phase === ExamPhase.REPORT ? '考试报告' :
          (this.phase === ExamPhase.PICK ? '选择试卷' : '考试作答'))
          .fontSize(20)
          .fontWeight(FontWeight.Bold)
          .layoutWeight(1)
          .maxLines(1)
        if (this.phase === ExamPhase.EXAM && this.session !== null) {
          Text(`剩余 ${this.formatRemain(this.session.server_remaining_seconds)}`)
            .fontSize(13)
            .fontColor(this.session.server_remaining_seconds < 60 ? '#c62828' : '#666666')
        }
      }
      .width('100%')

      // 未登录提示
      if (!hasBearerToken() && this.phase === ExamPhase.PICK) {
        Text('未登录——请先在「设置」Tab 登录后开考(开考/作答/交卷均需登录态)。')
          .fontSize(13)
          .fontColor('#8c6d1f')
          .backgroundColor('#fff8e1')
          .borderRadius(8)
          .padding(12)
          .width('100%')
      }

      // 通知/错误
      if (this.notice.length > 0) {
        Text(this.notice)
          .fontSize(13)
          .fontColor('#1890ff')
          .width('100%')
          .maxLines(2)
      }
      if (this.error.length > 0) {
        Text(this.error)
          .fontSize(13)
          .fontColor('#c62828')
          .width('100%')
          .padding(10)
          .backgroundColor('#fff3f3')
          .borderRadius(6)
          .maxLines(4)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
      }

      // 主体
      if (this.phase === ExamPhase.PICK) {
        this.PickView()
      } else if (this.phase === ExamPhase.EXAM) {
        this.ExamView()
      } else if (this.phase === ExamPhase.REPORT) {
        this.ReportView()
      }
    }
    .width('100%')
    .height('100%')
    .padding(16)
    .alignItems(HorizontalAlign.Start)
  }

  /** 剩余秒数格式化 mm:ss(只读快照,不倒计时) */
  private formatRemain(sec: number): string {
    if (sec < 0) {
      sec = 0;
    }
    const m: number = Math.floor(sec / 60);
    const s: number = sec % 60;
    return `${m}:${s < 10 ? '0' : ''}${s}`;
  }

  /** 试卷类型中文 */
  private typeLabel(type: string): string {
    if (type === 'multiple_select') {
      return '多选题';
    }
    if (type === 'essay') {
      return '简答/论述';
    }
    if (type === 'short_answer') {
      return '简答题';
    }
    if (type === 'numeric') {
      return '数值题';
    }
    if (type === 'true_false') {
      return '判断题';
    }
    if (type === 'mcq' || type === 'single') {
      return '单选题';
    }
    return type;
  }

  // ───────────────────── 选卷视图 ─────────────────────

  @Builder
  PickView() {
    Column({ space: 12 }) {
      if (this.papersLoading) {
        Column({ space: 8 }) {
          Text('⟳').fontSize(24).fontColor('#1890ff')
          Text('正在加载试卷…').fontSize(14).fontColor('#666666')
        }
        .width('100%')
        .alignItems(HorizontalAlign.Center)
        .padding(32)
      } else if (this.papers.length === 0) {
        Text('暂无可用试卷')
          .fontSize(14)
          .fontColor('#888888')
          .padding(24)
      } else {
        Scroll() {
          Column({ space: 10 }) {
            ForEach(this.papers, (paper: PaperOut) => {
              Column({ space: 6 }) {
                Text(paper.title)
                  .fontSize(15)
                  .fontWeight(FontWeight.Bold)
                  .maxLines(2)
                  .textOverflow({ overflow: TextOverflow.Ellipsis })
                Text(`时长 ${paper.duration_minutes} 分钟 · ${paper.subject.length > 0 ? paper.subject : '综合'}`)
                  .fontSize(12)
                  .fontColor('#888888')
                Button('开始考试')
                  .fontSize(13)
                  .height(34)
                  .backgroundColor('#1890ff')
                  .enabled(!this.busy)
                  .onClick(() => {
                    this.paperId = paper.id;
                    this.paperTitle = paper.title;
                    this.beginExam();
                  })
              }
              .width('100%')
              .padding(12)
              .backgroundColor('#fafafa')
              .borderRadius(8)
              .alignItems(HorizontalAlign.Start)
            }, (paper: PaperOut) => paper.id)
          }
          .width('100%')
        }
        .layoutWeight(1)
        .scrollBar(BarState.Off)
      }
    }
    .width('100%')
    .layoutWeight(1)
  }

  // ───────────────────── 作答视图 ─────────────────────

  @Builder
  ExamView() {
    if (this.session === null) {
      Column({ space: 8 }) {
        Text('⟳').fontSize(24).fontColor('#1890ff')
        Text('正在开考…').fontSize(14).fontColor('#666666')
      }
      .width('100%')
      .alignItems(HorizontalAlign.Center)
      .padding(32)
    } else {
      Column({ space: 12 }) {
        // 题号指示条:每题一格,已保存高亮
        Scroll() {
          Row({ space: 6 }) {
            ForEach(this.session.questions, (q: ExamQuestion, idx: number) => {
              Text(`${idx + 1}`)
                .fontSize(12)
                .width(28)
                .height(28)
                .textAlign(TextAlign.Center)
                .fontColor(idx === this.currentIdx ? '#ffffff' :
                  (this.draftSaved(q.id) ? '#2e7d32' : '#999999'))
                .backgroundColor(idx === this.currentIdx ? '#1890ff' :
                  (this.draftSaved(q.id) ? '#f6ffed' : '#f5f5f5'))
                .borderRadius(14)
                .onClick(() => {
                  this.currentIdx = idx;
                  this.syncTextDraft();
                })
            }, (q: ExamQuestion, idx: number) => `${q.id}-${idx}`)
          }
          .padding({ top: 2, bottom: 2 })
        }
        .scrollable(ScrollDirection.Horizontal)
        .scrollBar(BarState.Off)
        .width('100%')

        // 进度 + 试卷名
        Text(`${this.paperTitle.length > 0 ? this.paperTitle : this.session.paper_title} · ` +
          `第 ${this.currentIdx + 1}/${this.session.questions.length} 题 · 已保存 ${this.answeredCount()} 题`)
          .fontSize(12)
          .fontColor('#888888')
          .width('100%')
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })

        // 当前题卡
        if (this.currentQuestion() !== null) {
          this.QuestionCard(this.currentQuestion()!)
        }

        // 上一题/下一题
        Row({ space: 12 }) {
          Button('上一题')
            .fontSize(14)
            .height(40)
            .layoutWeight(1)
            .enabled(this.currentIdx > 0 && !this.busy)
            .onClick(() => {
              this.goPrev();
            })
          Button(this.currentIdx < this.session.questions.length - 1 ? '下一题' : '交卷')
            .fontSize(14)
            .height(40)
            .layoutWeight(1)
            .backgroundColor(this.currentIdx < this.session.questions.length - 1 ? '#1890ff' : '#c62828')
            .enabled(!this.busy)
            .onClick(() => {
              if (this.currentIdx < this.session!.questions.length - 1) {
                this.goNext();
              } else {
                // 交卷确认弹层
                this.confirmSubmit();
              }
            })
        }
        .width('100%')

        // 直接交卷入口(未到最后一题也可交)
        Button(`直接交卷(已保存 ${this.answeredCount()}/${this.session.questions.length})`)
          .fontSize(12)
          .height(32)
          .backgroundColor('#999999')
          .enabled(!this.busy)
          .onClick(() => {
            this.confirmSubmit();
          })
      }
      .width('100%')
      .layoutWeight(1)
    }
  }

  /** 判定某题已保存到服务端 */
  private draftSaved(questionId: string): boolean {
    for (let i: number = 0; i < this.drafts.length; i++) {
      if (this.drafts[i].questionId === questionId) {
        return this.drafts[i].saved;
      }
    }
    return false;
  }

  /** 题目作答卡(单选/多选/文本三分支) */
  @Builder
  QuestionCard(q: ExamQuestion) {
    Scroll() {
      Column({ space: 10 }) {
        Text(`【${this.typeLabel(q.type)}】`)
          .fontSize(12)
          .fontColor('#1890ff')
        Text(q.stem)
          .fontSize(15)
          .width('100%')
          .maxLines(8)
          .textOverflow({ overflow: TextOverflow.Ellipsis })

        // 单选:点选即保存
        if (isSingleChoice(q.type, q.options)) {
          ForEach(q.options!, (option: ExamOption) => {
            Button(`${option.key}. ${option.text}`)
              .fontSize(13)
              .height(38)
              .width('100%')
              .backgroundColor(this.draftAnswerOf(q.id) === option.key ? '#1890ff' : '#f0f0f0')
              .fontColor(this.draftAnswerOf(q.id) === option.key ? '#ffffff' : '#333333')
              .alignSelf(ItemAlign.Start)
              .enabled(!this.busy)
              .onClick(() => {
                this.selectSingle(option.key);
              })
          }, (option: ExamOption) => option.key)
        } else if (isMultipleSelect(q.type)) {
          // 多选:勾选 + 保存按钮
          ForEach(q.options!, (option: ExamOption, oIdx: number) => {
            Button(`${this.multiChecked(q.id, oIdx) ? '☑' : '☐'} ${option.key}. ${option.text}`)
              .fontSize(13)
              .height(38)
              .width('100%')
              .backgroundColor(this.multiChecked(q.id, oIdx) ? '#e6f7ff' : '#f0f0f0')
              .fontColor(this.multiChecked(q.id, oIdx) ? '#1890ff' : '#333333')
              .alignSelf(ItemAlign.Start)
              .enabled(!this.busy)
              .onClick(() => {
                this.toggleMulti(oIdx);
              })
          }, (option: ExamOption, oIdx: number) => `${q.id}-${option.key}`)
          Button(this.busy ? '保存中…' : '保存多选')
            .fontSize(13)
            .height(36)
            .backgroundColor('#1890ff')
            .width('100%')
            .enabled(!this.busy && this.multiIndicesOf(q.id).length > 0)
            .onClick(() => {
              this.saveCurrentAnswer(encodeMultiSelectAnswer(this.multiIndicesOf(q.id)));
            })
        } else {
          // 文本作答
          TextInput({ text: this.textDraft, placeholder: '输入你的作答…' })
            .onChange((v: string) => {
              this.onTextChange(v);
            })
            .width('100%')
            .height(80)
            .fontSize(13)
            .enabled(!this.busy)
          Button(this.busy ? '保存中…' : '保存作答')
            .fontSize(13)
            .height(36)
            .backgroundColor('#1890ff')
            .width('100%')
            .enabled(!this.busy && this.textDraft.trim().length > 0)
            .onClick(() => {
              this.saveTextAnswer();
            })
        }
      }
      .width('100%')
      .padding(12)
      .backgroundColor('#ffffff')
      .borderRadius(8)
      .alignItems(HorizontalAlign.Start)
    }
    .layoutWeight(1)
    .scrollBar(BarState.Off)
  }

  /** 取某题草稿 answer */
  private draftAnswerOf(questionId: string): string {
    for (let i: number = 0; i < this.drafts.length; i++) {
      if (this.drafts[i].questionId === questionId) {
        return this.drafts[i].answer;
      }
    }
    return '';
  }

  /** 多选勾选判定 */
  private multiChecked(questionId: string, index: number): boolean {
    return this.multiIndicesOf(questionId).indexOf(index) >= 0;
  }

  /** 取某题多选索引 */
  private multiIndicesOf(questionId: string): Array<number> {
    for (let i: number = 0; i < this.drafts.length; i++) {
      if (this.drafts[i].questionId === questionId) {
        return this.drafts[i].multiIndices;
      }
    }
    return [];
  }

  // ───────────────────── 报告视图 ─────────────────────

  @Builder
  ReportView() {
    Scroll() {
      Column({ space: 12 }) {
        // 报告头:得分
        if (this.report !== null) {
          Column({ space: 6 }) {
            Text(this.report.paper_title)
              .fontSize(16)
              .fontWeight(FontWeight.Bold)
              .maxLines(2)
              .textOverflow({ overflow: TextOverflow.Ellipsis })
            Text(`${this.report.score} 分 · 答对 ${this.report.correct_count}/${this.report.total_count}` +
              (this.report.reviewed_count > 0 ? ` · 待复核 ${this.report.reviewed_count} 题` : ''))
              .fontSize(15)
            Text(`得分 ${this.report.score_earned}/${this.report.score_max}`)
              .fontSize(12)
              .fontColor('#888888')
          }
          .width('100%')
          .padding(12)
          .backgroundColor('#f6ffed')
          .borderRadius(8)
          .alignItems(HorizontalAlign.Start)

          // 概念掌握
          if (this.report.concepts.length > 0) {
            Column({ space: 6 }) {
              Text('概念掌握')
                .fontSize(14)
                .fontWeight(FontWeight.Bold)
              ForEach(this.report.concepts, (c: ConceptRow) => {
                Text(`${c.concept}: ${c.correct}/${c.total}` +
                  (c.ratio !== null ? `(${Math.round(c.ratio * 100)}%)` : '(待复核)'))
                  .fontSize(12)
                  .fontColor('#666666')
              }, (c: ConceptRow) => c.concept)
            }
            .width('100%')
            .padding(12)
            .backgroundColor('#fafafa')
            .borderRadius(8)
            .alignItems(HorizontalAlign.Start)
          }

          // 逐题对错
          Text('逐题判分(交卷后服务端下发正确答案)')
            .fontSize(14)
            .fontWeight(FontWeight.Bold)
            .width('100%')
          ForEach(this.report.items, (item: ReportItem, idx: number) => {
            this.ReportItemCard(item, idx)
          }, (item: ReportItem, idx: number) => item.question_id)
        } else if (this.submission !== null) {
          // 404 降级:submission 概要
          Column({ space: 6 }) {
            Text(this.submission.paper_title)
              .fontSize(16)
              .fontWeight(FontWeight.Bold)
            Text(`${this.submission.score} 分 · 答对 ${this.submission.correct_count}/${this.submission.total_count} · 用时 ${this.submission.duration_seconds}s`)
              .fontSize(14)
            Text('(审阅报告尚未生成,以上为交卷判分概要)')
              .fontSize(12)
              .fontColor('#888888')
          }
          .width('100%')
          .padding(12)
          .backgroundColor('#f6ffed')
          .borderRadius(8)
          .alignItems(HorizontalAlign.Start)

          ForEach(this.submission.items, (g: GradedRow, idx: number) => {
            Column({ space: 6 }) {
              Text(`第 ${idx + 1} 题: ${g.correct === true ? '✓ 答对' :
                (g.correct === false ? '✗ 答错' : '⏳ 待复核')}`)
                .fontSize(14)
                .fontWeight(FontWeight.Bold)
                .fontColor(g.correct === true ? '#2e7d32' :
                  (g.correct === false ? '#c62828' : '#8c6d1f'))
              Text(`你的作答: ${g.given.length > 0 ? g.given : '(未作答)'}`)
                .fontSize(13)
                .maxLines(3)
                .textOverflow({ overflow: TextOverflow.Ellipsis })
              Text(`正确答案: ${g.expected}`)
                .fontSize(13)
                .fontColor('#2e7d32')
                .maxLines(3)
                .textOverflow({ overflow: TextOverflow.Ellipsis })
              if (g.explanation.length > 0) {
                Text(`解析: ${g.explanation}`)
                  .fontSize(12)
                  .fontColor('#888888')
                  .maxLines(6)
                  .textOverflow({ overflow: TextOverflow.Ellipsis })
              }
            }
            .width('100%')
            .padding(12)
            .backgroundColor('#ffffff')
            .borderRadius(8)
            .alignItems(HorizontalAlign.Start)
          }, (g: GradedRow) => g.question_id)
        } else {
          Text('报告加载中…')
            .fontSize(14)
            .fontColor('#888888')
        }
      }
      .width('100%')
    }
    .layoutWeight(1)
    .scrollBar(BarState.Off)
  }

  /** 报告逐题卡 */
  @Builder
  ReportItemCard(item: ReportItem, idx: number) {
    Column({ space: 6 }) {
      Text(`第 ${idx + 1} 题 【${this.typeLabel(item.question_type)}】 ${item.correct === true ? '✓ 答对' :
        (item.correct === false ? '✗ 答错' : '⏳ 待复核')}`)
        .fontSize(14)
        .fontWeight(FontWeight.Bold)
        .fontColor(item.correct === true ? '#2e7d32' :
          (item.correct === false ? '#c62828' : '#8c6d1f'))
      Text(item.stem)
        .fontSize(13)
        .maxLines(4)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
      Text(`你的作答: ${readableAnswer(item.given, null)}`)
        .fontSize(13)
        .maxLines(3)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
      Text(`正确答案: ${readableAnswer(item.expected, this.optionsOf(item.question_id))}`)
        .fontSize(13)
        .fontColor('#2e7d32')
        .maxLines(3)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
      if (item.explanation.length > 0) {
        Text(`解析: ${item.explanation}`)
          .fontSize(12)
          .fontColor('#888888')
          .maxLines(6)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
      }
      if (item.knowledge.length > 0) {
        Text(`知识点: ${item.knowledge.join('、')}`)
          .fontSize(11)
          .fontColor('#1890ff')
          .maxLines(2)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
      }
    }
    .width('100%')
    .padding(12)
    .backgroundColor('#ffffff')
    .borderRadius(8)
    .alignItems(HorizontalAlign.Start)
  }

  /** 从会话取题 options(报告回显字母用) */
  private optionsOf(questionId: string): Array<ExamOption> | null {
    if (this.session === null) {
      return null;
    }
    for (let i: number = 0; i < this.session.questions.length; i++) {
      if (this.session.questions[i].id === questionId) {
        return this.session.questions[i].options;
      }
    }
    return null;
  }
}
