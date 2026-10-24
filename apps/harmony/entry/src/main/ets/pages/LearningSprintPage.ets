/**
 * LearningSprintPage.ets — 学习冲刺页面(hm-parity v1,对齐 Android LearningSprintScreen)
 *
 * 用户旅程:合同列表 → 未确认合同点「确认」→ 有冲刺显示 11 阶段链 →
 * 「推进」逐阶段推进 → 前测/后测阶段「开始前测/后测」拉题作答(选项 +
 * 1-5 信心)→ 提交服务端判分显示分数。
 *
 * 不变式:
 *   - 答案隔离:题目投影不含 answer;判分只在提交后来自服务端;
 *   - token 零持久化(内存 bearer,由 AiosApi 模块持有);
 *   - 错误如实:409/404/503 服务端 detail 文案直接展示,不吞错;
 *   - 竞态防护:disposed + requestGeneration(参照 StudyPane/VoiceExamPage)。
 */

import { common } from '@kit.AbilityKit';
import { ApiResult, hasBearerToken } from '../AiosApi';
import {
  AssessmentItem, AssessmentOption, AssessmentResponse, AssessmentResult,
  AssessmentSubmitItem, LearningContract, LearningSprint,
  SPRINT_PHASES, advanceSprint, confirmLearningContract, getContractSprints,
  getLearningContracts, getSprintAssessment, nextSprintPhase,
  sprintPhaseLabel, submitSprintAssessment
} from '../LearningApi';
import { DEFAULT_API_BASE_URL, loadBaseUrl } from '../components/SettingsStore';

/** 合同+冲刺行模型(对齐 Android ContractWithSprint) */
interface ContractRow {
  contract: LearningContract;
  /** null = 未开冲刺 */
  sprint: LearningSprint | null;
}

/** 进行中的作答会话(前测/后测)——答案本地暂存,提交后服务端判分 */
interface AnswerDraft {
  itemId: string;
  /** '' = 未选 */
  answer: string;
  /** 0 = 未选信心 */
  confidence: number;
}

enum LoadStatus {
  LOADING = 'LOADING',
  EMPTY = 'EMPTY',
  SUCCESS = 'SUCCESS',
  ERROR = 'ERROR'
}

@Entry
@Component
struct LearningSprintPage {
  @State status: LoadStatus = LoadStatus.LOADING;
  @State rows: Array<ContractRow> = [];
  @State loadError: string = '';
  @State baseUrl: string = DEFAULT_API_BASE_URL;
  @State busy: boolean = false;
  @State notice: string = '';
  @State actionError: string = '';
  /** 作答会话(null=未在作答) */
  @State session: AssessmentResponse | null = null;
  @State sessionDrafts: Array<AnswerDraft> = [];
  @State lastResult: AssessmentResult | null = null;

  private disposed: boolean = false;
  private requestGeneration: number = 0;

  async aboutToAppear(): Promise<void> {
    const ctx: common.Context | undefined =
      this.getUIContext().getHostContext() as common.Context | undefined;
    if (ctx === undefined || ctx === null) {
      this.status = LoadStatus.ERROR;
      this.loadError = '无法获取应用上下文,面板暂不可用';
      return;
    }
    this.baseUrl = await loadBaseUrl(ctx);
    if (this.disposed) {
      return;
    }
    this.refresh();
  }

  aboutToDisappear(): void {
    this.disposed = true;
  }

  /** 未登录提示(只读判定,不触碰 token 本体) */
  private loginHint(): string {
    return hasBearerToken() ? '' : '未登录——请先在「设置」Tab 登录后使用学习冲刺。';
  }

  /** 刷新合同列表 + 每合同首个冲刺(单合同失败不阻塞整体) */
  private async refresh(): Promise<void> {
    if (this.disposed) {
      return;
    }
    const gen: number = ++this.requestGeneration;
    this.status = LoadStatus.LOADING;
    this.loadError = '';
    const r = await getLearningContracts(this.baseUrl);
    if (this.disposed || gen !== this.requestGeneration) {
      return;
    }
    if (!r.ok || r.data === null) {
      this.status = LoadStatus.ERROR;
      this.loadError = r.error;
      return;
    }
    if (r.data.length === 0) {
      this.status = LoadStatus.EMPTY;
      return;
    }
    const out: Array<ContractRow> = [];
    for (let i: number = 0; i < r.data.length; i++) {
      const contract: LearningContract = r.data[i];
      let sprint: LearningSprint | null = null;
      const s = await getContractSprints(this.baseUrl, contract.id);
      if (this.disposed || gen !== this.requestGeneration) {
        return;
      }
      if (s.ok && s.data !== null && s.data.length > 0) {
        sprint = s.data[0];
      }
      out.push({ contract: contract, sprint: sprint });
    }
    this.rows = out;
    this.status = LoadStatus.SUCCESS;
  }

  /** 确认合同 → 静默刷新 */
  private async confirmContract(contractId: string): Promise<void> {
    await this.runBusy(async () => {
      const r = await confirmLearningContract(this.baseUrl, contractId);
      if (!r.ok) {
        this.actionError = r.error;
        return;
      }
      this.notice = '合同已确认——可以开始冲刺';
      await this.refresh();
    });
  }

  /** 推进到下一阶段(服务端 409 如实展示) */
  private async advance(sprint: LearningSprint): Promise<void> {
    await this.runBusy(async () => {
      const next: string = nextSprintPhase(sprint.phase);
      if (next === '') {
        this.actionError = '已在最后阶段';
        return;
      }
      const r = await advanceSprint(this.baseUrl, sprint.id, next);
      if (!r.ok || r.data === null) {
        this.actionError = r.error;
        return;
      }
      this.notice = `已推进到「${sprintPhaseLabel(r.data.phase)}」`;
      await this.refresh();
    });
  }

  /** 开始前测/后测:拉题 → 作答会话 */
  private async startAssessment(sprint: LearningSprint): Promise<void> {
    await this.runBusy(async () => {
      const phase: string = sprint.phase === 'baseline_assessment' ? 'baseline' : 'posttest';
      const r = await getSprintAssessment(this.baseUrl, sprint.id, phase);
      if (!r.ok || r.data === null) {
        this.actionError = r.error;
        return;
      }
      if (r.data.items.length === 0) {
        this.actionError = '暂无题目(服务端未下发题库)';
        return;
      }
      this.lastResult = null;
      this.session = r.data;
      const drafts: Array<AnswerDraft> = [];
      for (let i: number = 0; i < r.data.items.length; i++) {
        drafts.push({ itemId: r.data.items[i].item_id, answer: '', confidence: 0 });
      }
      this.sessionDrafts = drafts;
    });
  }

  /** 选择答案 */
  private selectAnswer(itemId: string, key: string): void {
    for (let i: number = 0; i < this.sessionDrafts.length; i++) {
      if (this.sessionDrafts[i].itemId === itemId) {
        this.sessionDrafts[i].answer = key;
        break;
      }
    }
    this.sessionDrafts = this.sessionDrafts.slice();
  }

  /** 选择信心 */
  private setConfidence(itemId: string, level: number): void {
    for (let i: number = 0; i < this.sessionDrafts.length; i++) {
      if (this.sessionDrafts[i].itemId === itemId) {
        this.sessionDrafts[i].confidence = level;
        break;
      }
    }
    this.sessionDrafts = this.sessionDrafts.slice();
  }

  /** 全部作答完毕判定 */
  private allAnswered(): boolean {
    if (this.session === null || this.sessionDrafts.length === 0) {
      return false;
    }
    for (let i: number = 0; i < this.sessionDrafts.length; i++) {
      if (this.sessionDrafts[i].answer.length === 0) {
        return false;
      }
    }
    return true;
  }

  /** 提交 → 服务端判分 → 显示分数 */
  private async submitAssessment(): Promise<void> {
    if (this.session === null) {
      return;
    }
    await this.runBusy(async () => {
      const responses: Array<AssessmentSubmitItem> = [];
      for (let i: number = 0; i < this.sessionDrafts.length; i++) {
        const d: AnswerDraft = this.sessionDrafts[i];
        responses.push({ item_id: d.itemId, response: d.answer, confidence: d.confidence });
      }
      const r = await submitSprintAssessment(this.baseUrl, this.session!.sprint_id, this.session!.phase, responses);
      if (!r.ok || r.data === null) {
        this.actionError = r.error;
        return;
      }
      this.session = null;
      this.sessionDrafts = [];
      this.lastResult = r.data;
      this.notice = `已提交:服务端判分 ${r.data.score}分(${r.data.correct_count}/${r.data.question_count})`;
      await this.refresh();
    });
  }

  /** busy 包裹:动作期间禁用全部按钮,错误/通知生命周期由调用方管理 */
  private async runBusy(action: () => Promise<void>): Promise<void> {
    if (this.disposed || this.busy) {
      return;
    }
    this.busy = true;
    this.actionError = '';
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
        Button('返回')
          .fontSize(12)
          .height(28)
          .onClick(() => {
            this.getUIContext().getRouter().back();
          })
        Text('学习冲刺')
          .fontSize(20)
          .fontWeight(FontWeight.Bold)
        Text('48 小时可验证闭环')
          .fontSize(11)
          .fontColor('#888888')
          .layoutWeight(1)
          .maxLines(1)
      }
      .width('100%')

      // 未登录提示
      if (this.loginHint().length > 0) {
        Text(this.loginHint())
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
          .maxLines(3)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
      }
      if (this.actionError.length > 0) {
        Text(this.actionError)
          .fontSize(13)
          .fontColor('#c62828')
          .width('100%')
          .padding(10)
          .backgroundColor('#fff3f3')
          .borderRadius(6)
          .maxLines(4)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
      }

      // 判分结果卡(提交后)
      if (this.lastResult !== null) {
        this.ResultCard(this.lastResult)
      }

      // 主体
      if (this.status === LoadStatus.LOADING) {
        Column({ space: 8 }) {
          Text('⟳').fontSize(24).fontColor('#1890ff')
          Text('正在加载学习合同…').fontSize(14).fontColor('#666666')
        }
        .width('100%')
        .alignItems(HorizontalAlign.Center)
        .padding(32)
      } else if (this.status === LoadStatus.ERROR) {
        Column({ space: 10 }) {
          Text(this.loadError)
            .fontSize(13)
            .fontColor('#c62828')
            .maxLines(4)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
          Button('重试')
            .fontSize(13)
            .height(32)
            .onClick(() => {
              this.refresh();
            })
        }
        .width('100%')
        .padding(16)
        .backgroundColor('#fff3f3')
        .borderRadius(8)
      } else if (this.status === LoadStatus.EMPTY) {
        Column({ space: 8 }) {
          Text('还没有学习合同').fontSize(15).fontColor('#666666')
          Text('在 Web 端「学习冲刺」页创建并确认合同后,可以在这里推进阶段与作答前后测。')
            .fontSize(12)
            .fontColor('#999999')
            .textAlign(TextAlign.Center)
        }
        .width('100%')
        .alignItems(HorizontalAlign.Center)
        .padding(32)
      } else {
        Scroll() {
          Column({ space: 12 }) {
            ForEach(this.rows, (row: ContractRow) => {
              this.ContractCard(row)
            }, (row: ContractRow) => row.contract.id)
          }
          .width('100%')
        }
        .layoutWeight(1)
        .scrollBar(BarState.Off)
      }

      // 作答会话(固定底部区,覆盖在列表上方独立展示)
      if (this.session !== null) {
        this.AssessmentSessionView()
      }
    }
    .width('100%')
    .height('100%')
    .padding(16)
    .alignItems(HorizontalAlign.Start)
  }

  /** 判分结果卡 */
  @Builder
  ResultCard(result: AssessmentResult) {
    Column({ space: 6 }) {
      Text(result.phase === 'baseline' ? '前测得分' : '后测得分')
        .fontSize(15)
        .fontWeight(FontWeight.Bold)
      Text(`${result.score}分 · ${result.correct_count}/${result.question_count} 对` +
        (result.ungraded_count > 0 ? `(另有 ${result.ungraded_count} 题未判分)` : ''))
        .fontSize(14)
      Text(`阶段已推进到「${sprintPhaseLabel(result.sprint_phase)}」`)
        .fontSize(12)
        .fontColor('#888888')
    }
    .width('100%')
    .padding(12)
    .backgroundColor('#f6ffed')
    .borderRadius(8)
  }

  /** 合同+冲刺卡 */
  @Builder
  ContractCard(row: ContractRow) {
    Column({ space: 10 }) {
      Text(row.contract.title)
        .fontSize(16)
        .fontWeight(FontWeight.Bold)
        .width('100%')
        .maxLines(2)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
      Text(`${row.contract.target_outcomes.length} 个能力目标 · ${row.contract.time_budget_hours} 小时 · ${row.contract.pass_criteria}`)
        .fontSize(12)
        .fontColor('#888888')
        .width('100%')
        .maxLines(2)
        .textOverflow({ overflow: TextOverflow.Ellipsis })

      if (row.contract.confirmed_at === null) {
        Button(this.busy ? '处理中…' : '确认合同')
          .fontSize(13)
          .height(36)
          .enabled(!this.busy)
          .onClick(() => {
            this.confirmContract(row.contract.id);
          })
      } else if (row.sprint === null) {
        Text('合同已确认——在 Web 端构建蓝图并开始冲刺后,这里会展示进度。')
          .fontSize(12)
          .fontColor('#888888')
          .width('100%')
      } else {
        // 阶段链:当前及已过阶段高亮(单向链,客户端只投影)
        Flex({ wrap: FlexWrap.Wrap }) {
          ForEach(SPRINT_PHASES.slice(1), (phase: string) => {
            Text(sprintPhaseLabel(phase))
              .fontSize(10)
              .fontColor(this.phaseReached(row.sprint!.phase, phase) ? '#ffffff' : '#999999')
              .backgroundColor(this.phaseReached(row.sprint!.phase, phase) ? '#1890ff' : '#f5f5f5')
              .padding({ left: 6, right: 6, top: 3, bottom: 3 })
              .borderRadius(10)
              .margin({ right: 4, bottom: 4 })
          }, (phase: string) => phase)
        }
        .width('100%')

        // 当前阶段 + 操作按钮
        Text(`当前阶段: ${sprintPhaseLabel(row.sprint!.phase)}`)
          .fontSize(13)
          .fontColor('#333333')
          .width('100%')
        if (row.sprint!.phase === 'baseline_assessment' || row.sprint!.phase === 'posttest') {
          Button(row.sprint!.phase === 'baseline_assessment' ? '开始前测' : '开始后测')
            .fontSize(13)
            .height(36)
            .backgroundColor('#1890ff')
            .enabled(!this.busy)
            .onClick(() => {
              this.startAssessment(row.sprint!);
            })
        } else {
          Button(`推进到 ${sprintPhaseLabel(nextSprintPhase(row.sprint!.phase))}`)
            .fontSize(13)
            .height(36)
            .enabled(!this.busy && nextSprintPhase(row.sprint!.phase) !== '')
            .onClick(() => {
              this.advance(row.sprint!);
            })
        }
        if (row.sprint!.paused) {
          Text(`已暂停:${row.sprint!.pause_reason}(学习证据保留)`)
            .fontSize(12)
            .fontColor('#8c6d1f')
            .width('100%')
            .maxLines(2)
        }
      }
    }
    .width('100%')
    .padding(12)
    .backgroundColor('#fafafa')
    .borderRadius(8)
  }

  /** 阶段已达判定:目标阶段在链上的位置 <= 当前阶段位置 */
  private phaseReached(current: string, target: string): boolean {
    const ci: number = SPRINT_PHASES.indexOf(current);
    const ti: number = SPRINT_PHASES.indexOf(target);
    return ci >= ti;
  }

  /** 作答会话视图(题目 + 选项 + 信心 + 提交) */
  @Builder
  AssessmentSessionView() {
    Scroll() {
      Column({ space: 12 }) {
        Text((this.session!.phase === 'baseline' ? '前测' : '后测') + '(独立作答,先提交后见判分)')
          .fontSize(15)
          .fontWeight(FontWeight.Bold)
          .width('100%')
        ForEach(this.session!.items, (item: AssessmentItem) => {
          this.AssessmentItemView(item)
        }, (item: AssessmentItem) => item.item_id)
        Button(this.busy ? '判分中…' : '提交(服务端判分)')
          .fontSize(14)
          .height(40)
          .backgroundColor('#2e7d32')
          .width('100%')
          .enabled(!this.busy && this.allAnswered())
          .onClick(() => {
            this.submitAssessment();
          })
        Button('放弃本次作答')
          .fontSize(12)
          .height(32)
          .backgroundColor('#999999')
          .enabled(!this.busy)
          .onClick(() => {
            this.session = null;
            this.sessionDrafts = [];
          })
      }
      .width('100%')
    }
    .layoutWeight(1)
    .scrollBar(BarState.Off)
  }

  /** 单题视图 */
  @Builder
  AssessmentItemView(item: AssessmentItem) {
    Column({ space: 8 }) {
      Text(item.stem)
        .fontSize(14)
        .width('100%')
        .maxLines(6)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
      Text(`概念 ${item.concept_id} · ${item.question_type}`)
        .fontSize(11)
        .fontColor('#888888')
        .width('100%')
        .maxLines(1)
        .textOverflow({ overflow: TextOverflow.Ellipsis })

      // 选项(竖排按钮,选中高亮)
      if (item.options !== null) {
        ForEach(item.options!, (option: AssessmentOption) => {
          Button(`${option.key}. ${option.text}`)
            .fontSize(13)
            .height(38)
            .width('100%')
            .backgroundColor(this.draftOf(item.item_id).answer === option.key ? '#1890ff' : '#f0f0f0')
            .fontColor(this.draftOf(item.item_id).answer === option.key ? '#ffffff' : '#333333')
            .alignSelf(ItemAlign.Start)
            .onClick(() => {
              this.selectAnswer(item.item_id, option.key);
            })
        }, (option: AssessmentOption) => option.key)
      }

      // 信心 1-5
      Row({ space: 6 }) {
        Text('信心:')
          .fontSize(12)
          .fontColor('#888888')
        ForEach([1, 2, 3, 4, 5], (level: number) => {
          Text(`${level}`)
            .fontSize(12)
            .fontColor(this.draftOf(item.item_id).confidence === level ? '#ffffff' : '#666666')
            .backgroundColor(this.draftOf(item.item_id).confidence === level ? '#1890ff' : '#f5f5f5')
            .padding({ left: 8, right: 8, top: 4, bottom: 4 })
            .borderRadius(10)
            .onClick(() => {
              this.setConfidence(item.item_id, level);
            })
        }, (level: number) => `${level}`)
      }
      .width('100%')
    }
    .width('100%')
    .padding(12)
    .backgroundColor('#ffffff')
    .borderRadius(8)
  }

  /** 取某题作答草稿(无会话返回空草稿) */
  private draftOf(itemId: string): AnswerDraft {
    for (let i: number = 0; i < this.sessionDrafts.length; i++) {
      if (this.sessionDrafts[i].itemId === itemId) {
        return this.sessionDrafts[i];
      }
    }
    return { itemId: itemId, answer: '', confidence: 0 };
  }
}
