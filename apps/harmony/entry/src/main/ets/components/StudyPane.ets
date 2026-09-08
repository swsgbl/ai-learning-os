/**
 * StudyPane.ets — 论文列表只读面板(M13-05)+ 考试会话只读区(M13-08 Stage B)
 *
 * 功能(刻意收窄):
 *   【论文区,M13-05 原行为保持不变】
 *   - 通过 getPapers(baseUrl) 加载并展示 AiosEndpoint.PAPERS 返回的 PaperOut 数组;
 *   - 只读展示:标题/副标题/来源/院校/年份/学科/难度/时长/标签/原文链接;
 *   - URL 来自 SettingsStore.loadBaseUrl,监听 EVENT_ID_URL_CHANGED 自动刷新;
 *   - 状态机:LOADING / EMPTY / SUCCESS / ERROR,带刷新/重试按钮;
 *   - 元数据行使用 Flex(Wrap) 支持窄屏自动换行。
 *
 *   【考试区,M13-08 Stage B,与论文区完全独立】
 *   - 通过 getExamSession(baseUrl, examId) 只读拉取 GET /api/v1/exams/{exam_id};
 *   - 独立状态机 ExamSessionStatus:IDLE / LOADING / SUCCESS / ERROR,与论文区
 *     ListStatus 无任何耦合;
 *   - 独立 examRequestGeneration 代次守卫 + disposed 守卫:过期考试响应不得
 *     覆盖较新考试状态,也不得触碰论文区任何状态;
 *   - 每次「查询/重试」恰好发起一次 getExamSession,无自动重试循环;
 *   - 失败(网络/HTTP/examId 校验)如实展示原始中文错误,只动考试区状态,
 *     绝不变更论文列表状态;
 *   - 成功先做契约形状校验(标量字段类型 / questions 数组 / answers 对象,
 *     逐题逐选项校验),不合格一律失败关闭(fail closed)并给出中文错误;
 *   - 成功视图展示 exam_id/paper_id/paper_title/mode/status/
 *     server_remaining_seconds/next_sequence 与全部公共题目(id/type/stem/
 *     每个选项 key/text);answers 仅作为「学员已保存作答」展示并明确标注
 *     非正确答案;
 *   - 不渲染正确答案/解析(公共投影本就不含),不提供作答选择控件、提交按钮、
 *     倒计时或计时器控件,无任何 POST/PUT/DELETE 写路径、凭据、麦克风/存储访问;
 *   - 服务地址变更(EVENT_ID_URL_CHANGED):论文区照旧刷新;考试区立即失效
 *     —— 考试代次 +1 并清空考试数据/状态/错误,展示「地址已变更需重新查询」
 *     提示,绝不保留旧地址加载的会话,也不自动重查(由用户显式重新查询)。
 *
 * 布局:整页单个纵向 Scroll,论文区在上、考试区在下,两区各自完整可用;
 * 多行文本均设 maxLines 上限 + Ellipsis,元数据行 Flex(Wrap),防止窄屏溢出、
 * 遮挡或截断关键契约字段。
 */

import { common } from '@kit.AbilityKit';
import { emitter } from '@kit.BasicServicesKit';
import {
  ApiResult,
  ExamSessionData,
  PaperOut,
  PublicQuestionOut,
  QuestionOptionOut,
  getExamSession,
  getPapers
} from '../AiosApi';
import {
  DEFAULT_API_BASE_URL,
  EVENT_ID_URL_CHANGED,
  loadBaseUrl
} from './SettingsStore';

/** 论文区状态机(仅论文区使用) */
enum ListStatus {
  LOADING = 'LOADING',
  EMPTY = 'EMPTY',
  SUCCESS = 'SUCCESS',
  ERROR = 'ERROR'
}

/** 考试区状态机(仅考试区使用,与论文区 ListStatus 无耦合) */
enum ExamSessionStatus {
  IDLE = 'IDLE',
  LOADING = 'LOADING',
  SUCCESS = 'SUCCESS',
  ERROR = 'ERROR'
}

/** 考试 ID 输入框缺省值(与 mock 契约 exam-m13-08-001 一致) */
const DEFAULT_EXAM_ID: string = 'exam-m13-08-001';

/** 已保存作答行模型(Record<string,string> 展开后的稳定行序) */
interface SavedAnswerRow {
  questionId: string;
  answerKey: string;
}

/**
 * answers 展开为稳定行序:先按题目顺序,再补题目列表之外的键(不静默丢弃)。
 */
function toSavedAnswerRows(session: ExamSessionData): Array<SavedAnswerRow> {
  const answers: Record<string, string> = session.answers;
  const rows: Array<SavedAnswerRow> = [];
  const questions: Array<PublicQuestionOut> = session.questions;
  for (let i: number = 0; i < questions.length; i++) {
    const qid: string = questions[i].id;
    const v: string | undefined = answers[qid];
    if (v !== undefined) {
      rows.push({ questionId: qid, answerKey: v });
    }
  }
  const keys: Array<string> = Object.keys(answers);
  for (let i: number = 0; i < keys.length; i++) {
    const qid: string = keys[i];
    let seen: boolean = false;
    for (let j: number = 0; j < questions.length; j++) {
      if (questions[j].id === qid) {
        seen = true;
        break;
      }
    }
    if (!seen) {
      const v: string | undefined = answers[qid];
      if (v !== undefined) {
        rows.push({ questionId: qid, answerKey: v });
      }
    }
  }
  return rows;
}

@Component
export struct StudyPane {
  @State listStatus: ListStatus = ListStatus.LOADING;
  @State listData: Array<PaperOut> = [];
  @State listError: string = '';
  @State baseUrl: string = DEFAULT_API_BASE_URL;
  @State ctxError: string = '';

  // ── 考试区状态(与论文区完全独立)──────────────────────────────
  @State examStatus: ExamSessionStatus = ExamSessionStatus.IDLE;
  @State examData: ExamSessionData | null = null;
  @State examError: string = '';
  @State examIdInput: string = DEFAULT_EXAM_ID;
  @State urlChangeNotice: string = '';

  private hostContext: common.Context | undefined = undefined;
  private disposed: boolean = false;
  private requestGeneration: number = 0;
  /** 考试请求代次:独立于论文区 requestGeneration,只守护考试响应 */
  private examRequestGeneration: number = 0;

  async aboutToAppear(): Promise<void> {
    const ctx: common.Context | undefined =
      this.getUIContext().getHostContext() as common.Context | undefined;
    if (ctx === undefined || ctx === null) {
      this.ctxError = '无法获取应用上下文(getHostContext 为空),面板暂不可用';
      this.listStatus = ListStatus.ERROR;
      this.listError = this.ctxError;
      return;
    }
    this.hostContext = ctx;
    // 初始化加载一次持久化 URL
    const persistedUrl: string = await loadBaseUrl(this.hostContext);
    // 若在 await 期间组件已销毁,立即返回,不注册监听器也不发起请求
    if (this.disposed) {
      return;
    }
    this.baseUrl = persistedUrl;
    // 注册 URL 变更订阅:SettingsStore.saveBaseUrl 成功后触发刷新
    // 先 off 再 on:防止 aboutToAppear 被多次调用时堆叠回调
    emitter.off(EVENT_ID_URL_CHANGED, this.urlChangeListener);
    emitter.on(EVENT_ID_URL_CHANGED, this.urlChangeListener);
    // 初始加载后刷新一次
    this.loadPapers();
  }

  aboutToDisappear(): void {
    // 先标记销毁,再取消订阅,阻止后续回调和异步响应写入状态
    this.disposed = true;
    emitter.off(EVENT_ID_URL_CHANGED, this.urlChangeListener);
  }

  /**
   * URL 变更回调:论文区照旧刷新;考试区立即失效(考试代次 +1 并清空全部
   * 考试数据/状态/错误),绝不保留旧地址加载的会话。
   */
  private urlChangeListener = (event: emitter.EventData): void => {
    if (this.disposed) {
      return;
    }
    // 忽略事件 payload,重新读取持久化 URL
    const ctx: common.Context | undefined = this.hostContext;
    if (ctx === undefined || ctx === null) {
      return;
    }
    loadBaseUrl(ctx).then((freshUrl: string) => {
      if (this.disposed) {
        return;
      }
      this.baseUrl = freshUrl;
      this.loadPapers();
      this.invalidateExamSession();
    }).catch((e: Error) => {
      if (this.disposed) {
        return;
      }
      this.listStatus = ListStatus.ERROR;
      this.listError = `读取配置失败: ${e}`;
    });
  };

  /** 考试区失效:代次 +1 使在途考试回调作废,并清空考试数据/状态/错误 */
  private invalidateExamSession(): void {
    this.examRequestGeneration = this.examRequestGeneration + 1;
    this.examData = null;
    this.examStatus = ExamSessionStatus.IDLE;
    this.examError = '';
    this.urlChangeNotice =
      '服务地址已变更,当前考试会话已失效(旧地址数据已清除),请用新地址重新查询。';
  }

  private async loadPapers(): Promise<void> {
    // 若组件已销毁,立即返回,阻止任何状态写入
    if (this.disposed) {
      return;
    }
    // 记录本次请求的代次,用于后续判断是否为最新请求
    const generation: number = ++this.requestGeneration;
    this.listStatus = ListStatus.LOADING;
    this.listError = '';
    const r: ApiResult<Array<PaperOut>> = await getPapers(this.baseUrl);
    // 若已销毁或非最新代次,忽略响应,防止竞态导致的状态回退
    if (this.disposed || generation !== this.requestGeneration) {
      return;
    }
    if (!r.ok || !r.data) {
      this.listStatus = ListStatus.ERROR;
      this.listError = r.error;
      return;
    }
    if (r.data.length === 0) {
      this.listStatus = ListStatus.EMPTY;
      return;
    }
    this.listData = r.data;
    this.listStatus = ListStatus.SUCCESS;
  }

  /**
   * 查询考试会话:每次用户动作恰好调用一次 getExamSession,无自动重试。
   * 失败(网络/HTTP/examId 校验/契约形状)如实写入 examError,只动考试区状态,
   * 绝不变更论文列表状态。
   */
  private async loadExamSession(): Promise<void> {
    if (this.disposed) {
      return;
    }
    // 独立代次:过期考试响应(含 URL 变更作废的请求)一律丢弃
    const generation: number = ++this.examRequestGeneration;
    this.examStatus = ExamSessionStatus.LOADING;
    this.examError = '';
    this.urlChangeNotice = '';
    const r: ApiResult<ExamSessionData> =
      await getExamSession(this.baseUrl, this.examIdInput);
    // 已销毁或非最新考试代次:丢弃响应,防止过期数据覆盖较新考试状态
    if (this.disposed || generation !== this.examRequestGeneration) {
      return;
    }
    if (!r.ok || r.data === null) {
      this.examData = null;
      this.examError = r.error;
      this.examStatus = ExamSessionStatus.ERROR;
      return;
    }
    // 入库前先做契约形状校验,不合格一律失败关闭
    const invalidReason: string = this.validateExamSessionShape(r.data);
    if (invalidReason !== '') {
      this.examData = null;
      this.examError = invalidReason;
      this.examStatus = ExamSessionStatus.ERROR;
      return;
    }
    this.examData = r.data;
    this.examStatus = ExamSessionStatus.SUCCESS;
  }

  /**
   * 响应形状校验(失败关闭):
   *   - 标量字段:exam_id/paper_id/paper_title/mode/status/server_started_at/
   *     server_end_at 为字符串;server_remaining_seconds/next_sequence 为数字;
   *   - questions 为数组,逐题校验 id/type/stem 为字符串、options 为数组,
   *     且每个选项 key/text 为字符串;
   *   - answers 为对象(非数组非 null),每个取值为字符串。
   * 返回 '' 表示通过;否则返回中文错误信息(不入库)。
   */
  private validateExamSessionShape(data: Object): string {
    const record: Record<string, Object> = data as Record<string, Object>;
    const scalarStringFields: Array<string> = [
      'exam_id', 'paper_id', 'paper_title', 'mode', 'status',
      'server_started_at', 'server_end_at'
    ];
    for (let i: number = 0; i < scalarStringFields.length; i++) {
      const name: string = scalarStringFields[i];
      if (typeof record[name] !== 'string') {
        return `响应格式错误: ${name} 必须为字符串`;
      }
    }
    if (typeof record['server_remaining_seconds'] !== 'number') {
      return '响应格式错误: server_remaining_seconds 必须为数字';
    }
    if (typeof record['next_sequence'] !== 'number') {
      return '响应格式错误: next_sequence 必须为数字';
    }
    const questions: Object = record['questions'];
    if (!Array.isArray(questions)) {
      return '响应格式错误: questions 必须为数组';
    }
    const questionsArr: Array<Object> = questions as Array<Object>;
    for (let i: number = 0; i < questionsArr.length; i++) {
      const qItem: Object = questionsArr[i];
      if (typeof qItem !== 'object' || Array.isArray(qItem)) {
        return `响应格式错误: questions[${i}] 必须为对象`;
      }
      const q: Record<string, Object> = qItem as Record<string, Object>;
      if (typeof q['id'] !== 'string') {
        return `响应格式错误: questions[${i}].id 必须为字符串`;
      }
      if (typeof q['type'] !== 'string') {
        return `响应格式错误: questions[${i}].type 必须为字符串`;
      }
      if (typeof q['stem'] !== 'string') {
        return `响应格式错误: questions[${i}].stem 必须为字符串`;
      }
      const opts: Object = q['options'];
      if (!Array.isArray(opts)) {
        return `响应格式错误: questions[${i}].options 必须为数组`;
      }
      const optsArr: Array<Object> = opts as Array<Object>;
      for (let j: number = 0; j < optsArr.length; j++) {
        const optItem: Object = optsArr[j];
        if (typeof optItem !== 'object' || Array.isArray(optItem)) {
          return `响应格式错误: questions[${i}].options[${j}] 必须为对象`;
        }
        const opt: Record<string, Object> = optItem as Record<string, Object>;
        if (typeof opt['key'] !== 'string') {
          return `响应格式错误: questions[${i}].options[${j}].key 必须为字符串`;
        }
        if (typeof opt['text'] !== 'string') {
          return `响应格式错误: questions[${i}].options[${j}].text 必须为字符串`;
        }
      }
    }
    const answers: Object = record['answers'];
    if (typeof answers !== 'object' || Array.isArray(answers)) {
      return '响应格式错误: answers 必须为对象';
    }
    const answersMap: Record<string, Object> = answers as Record<string, Object>;
    const answerKeys: Array<string> = Object.keys(answersMap);
    for (let i: number = 0; i < answerKeys.length; i++) {
      const v: Object = answersMap[answerKeys[i]];
      if (typeof v !== 'string') {
        return `响应格式错误: answers.${answerKeys[i]} 必须为字符串`;
      }
    }
    return '';
  }

  build() {
    // 整页单 Scroll:论文区在上、考试区在下,两区内容完整平铺、均可滚动访问
    Scroll() {
      Column({ space: 16 }) {
        // ── 论文区(M13-05 原行为保持)──────────────────────────
        Column({ space: 12 }) {
          // 标题栏
          Row({ space: 12 }) {
            Text('论文列表')
              .fontSize(20)
              .fontWeight(FontWeight.Bold)
            Text(`服务地址: ${this.baseUrl}`)
              .fontSize(12)
              .fontColor('#666666')
              .textAlign(TextAlign.Start)
              .layoutWeight(1)
              .maxLines(1)
              .textOverflow({ overflow: TextOverflow.Ellipsis })
            Button('刷新')
              .fontSize(12)
              .height(28)
              .onClick(() => {
                this.loadPapers();
              })
          }
          .width('100%')

          // 错误状态
          if (this.listStatus === ListStatus.ERROR) {
            Column({ space: 12 }) {
              Text(this.listError)
                .fontSize(14)
                .fontColor('#c62828')
                .maxLines(3)
                .textOverflow({ overflow: TextOverflow.Ellipsis })
              Button('重试')
                .fontSize(14)
                .height(36)
                .onClick(() => {
                  this.loadPapers();
                })
            }
            .width('100%')
            .padding(16)
            .backgroundColor('#fff3f3')
            .borderRadius(8)
          }

          // 加载中
          if (this.listStatus === ListStatus.LOADING) {
            Column({ space: 8 }) {
              Text('⟳')
                .fontSize(24)
                .fontColor('#1890ff')
              Text('加载中...')
                .fontSize(14)
                .fontColor('#666666')
            }
            .width('100%')
            .alignItems(HorizontalAlign.Center)
            .padding(32)
          }

          // 空状态
          if (this.listStatus === ListStatus.EMPTY) {
            Column({ space: 8 }) {
              Text('暂无论文')
                .fontSize(16)
                .fontColor('#666666')
            }
            .width('100%')
            .alignItems(HorizontalAlign.Center)
            .padding(32)
          }

          // 成功列表(整页统一滚动,不再嵌套内层 Scroll)
          if (this.listStatus === ListStatus.SUCCESS) {
            Column({ space: 12 }) {
              ForEach(this.listData, (paper: PaperOut, index: number) => {
                PaperCard({ paper: paper })
              }, (paper: PaperOut, index: number) => paper.id)
            }
            .width('100%')
          }
        }
        .width('100%')

        // ── 考试区(M13-08 Stage B,只读)────────────────────────
        Column({ space: 12 }) {
          // 标题行
          Row({ space: 12 }) {
            Text('考试会话(只读)')
              .fontSize(20)
              .fontWeight(FontWeight.Bold)
          }
          .width('100%')

          // 查询输入行:考试 ID + 查询按钮(只读 GET,无作答/提交入口)
          Column({ space: 8 }) {
            Text('考试 ID(只读查询,不提供作答/提交)')
              .fontSize(12)
              .fontColor('#666666')
              .width('100%')
            Row({ space: 8 }) {
              TextInput({ text: this.examIdInput, placeholder: DEFAULT_EXAM_ID })
                .onChange((value: string) => {
                  this.examIdInput = value;
                })
                .layoutWeight(1)
                .height(40)
                .fontSize(13)
              Button(this.examStatus === ExamSessionStatus.LOADING ? '查询中…' : '查询')
                .fontSize(13)
                .height(40)
                .enabled(this.examStatus !== ExamSessionStatus.LOADING)
                .onClick(() => {
                  // 每次点击恰好一次请求,无自动重试
                  this.loadExamSession();
                })
            }
            .width('100%')
          }
          .width('100%')

          // 服务地址变更失效提示(不自动重查,由用户显式重新查询)
          if (this.urlChangeNotice.length > 0) {
            Text(this.urlChangeNotice)
              .fontSize(13)
              .fontColor('#8c6d1f')
              .backgroundColor('#fff8e1')
              .borderRadius(8)
              .padding(12)
              .width('100%')
              .maxLines(4)
              .textOverflow({ overflow: TextOverflow.Ellipsis })
          }

          // 空闲引导(无失效提示时)
          if (this.examStatus === ExamSessionStatus.IDLE &&
              this.urlChangeNotice.length === 0) {
            Text('输入考试 ID 后点「查询」,读取 GET /api/v1/exams/{exam_id} 只读快照。')
              .fontSize(13)
              .fontColor('#888888')
              .width('100%')
              .maxLines(2)
              .textOverflow({ overflow: TextOverflow.Ellipsis })
          }

          // 加载中
          if (this.examStatus === ExamSessionStatus.LOADING) {
            Column({ space: 8 }) {
              Text('⟳')
                .fontSize(24)
                .fontColor('#1890ff')
              Text('加载中…')
                .fontSize(13)
                .fontColor('#666666')
            }
            .width('100%')
            .alignItems(HorizontalAlign.Center)
            .padding(24)
          }

          // 失败:如实展示原始错误,重试按钮仅再次发起单次请求
          if (this.examStatus === ExamSessionStatus.ERROR) {
            Column({ space: 10 }) {
              Text(this.examError)
                .fontSize(13)
                .fontColor('#c62828')
                .width('100%')
                .maxLines(6)
                .textOverflow({ overflow: TextOverflow.Ellipsis })
              Button('重试')
                .fontSize(13)
                .height(32)
                .onClick(() => {
                  // 重试 = 再一次单次请求,仍无自动重试循环
                  this.loadExamSession();
                })
            }
            .width('100%')
            .alignItems(HorizontalAlign.Start)
            .padding(12)
            .backgroundColor('#fff3f3')
            .borderRadius(8)
          }

          // 成功:只读快照视图
          if (this.examStatus === ExamSessionStatus.SUCCESS && this.examData !== null) {
            this.ExamSessionView(this.examData)
          }
        }
        .width('100%')
        .alignItems(HorizontalAlign.Start)
      }
      .width('100%')
      .alignItems(HorizontalAlign.Start)
      .padding(16)
    }
    .width('100%')
    .height('100%')
    .align(Alignment.TopStart)
    .scrollBar(BarState.Off)
  }

  /** 考试会话成功视图(全部只读,无任何作答控件) */
  @Builder
  ExamSessionView(session: ExamSessionData) {
    Column({ space: 10 }) {
      // 会话元数据(Flex Wrap:窄屏自动换行,不截断关键字段)
      Flex({ wrap: FlexWrap.Wrap }) {
        Text(`exam_id: ${session.exam_id}`)
          .fontSize(12)
          .fontColor('#555555')
          .margin({ right: 8, bottom: 4 })
        Text(`paper_id: ${session.paper_id}`)
          .fontSize(12)
          .fontColor('#555555')
          .margin({ right: 8, bottom: 4 })
        Text(`mode: ${session.mode}`)
          .fontSize(12)
          .fontColor('#555555')
          .margin({ right: 8, bottom: 4 })
        Text(`status: ${session.status}`)
          .fontSize(12)
          .fontColor('#555555')
          .margin({ right: 8, bottom: 4 })
        Text(`server_remaining_seconds: ${session.server_remaining_seconds}`)
          .fontSize(12)
          .fontColor('#555555')
          .margin({ right: 8, bottom: 4 })
        Text(`next_sequence: ${session.next_sequence}`)
          .fontSize(12)
          .fontColor('#555555')
          .margin({ right: 8, bottom: 4 })
      }
      .width('100%')

      // 试卷标题(多行有界)
      Text(`试卷: ${session.paper_title}`)
        .fontSize(14)
        .fontWeight(FontWeight.Bold)
        .width('100%')
        .maxLines(4)
        .textOverflow({ overflow: TextOverflow.Ellipsis })

      // 时间窗说明:只读快照,不做倒计时/计时
      Text('server_remaining_seconds 为服务端时间窗剩余秒数的只读快照,页面不做倒计时。')
        .fontSize(11)
        .fontColor('#888888')
        .width('100%')
        .maxLines(2)
        .textOverflow({ overflow: TextOverflow.Ellipsis })

      // 公共题目列表:id/type/stem + 每个选项 key/text(不含正确答案/解析)
      ForEach(session.questions, (question: PublicQuestionOut, qIndex: number) => {
        Column({ space: 6 }) {
          Row({ space: 8 }) {
            Text(`#${qIndex + 1}`)
              .fontSize(12)
              .fontColor('#888888')
            Text(question.id)
              .fontSize(12)
              .fontColor('#1890ff')
              .layoutWeight(1)
              .maxLines(1)
              .textOverflow({ overflow: TextOverflow.Ellipsis })
          }
          .width('100%')

          Text(`类型: ${question.type}`)
            .fontSize(12)
            .fontColor('#666666')
            .width('100%')
            .maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis })

          Text(question.stem)
            .fontSize(14)
            .width('100%')
            .maxLines(6)
            .textOverflow({ overflow: TextOverflow.Ellipsis })

          ForEach(question.options, (option: QuestionOptionOut, oIndex: number) => {
            Row({ space: 8 }) {
              Text(option.key)
                .fontSize(12)
                .fontColor('#666666')
              Text(option.text)
                .fontSize(13)
                .layoutWeight(1)
                .maxLines(3)
                .textOverflow({ overflow: TextOverflow.Ellipsis })
            }
            .width('100%')
          }, (option: QuestionOptionOut, oIndex: number) => `${question.id}-${option.key}`)

          // 该题已保存作答(学员已保存,非正确答案;未作答时如实展示)
          Text(session.answers[question.id] === undefined ?
            '已保存作答: 暂无(未作答)' :
            `已保存作答(学员已保存,非正确答案): ${session.answers[question.id]}`)
            .fontSize(12)
            .fontColor('#2e7d32')
            .width('100%')
            .maxLines(2)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
        }
        .width('100%')
        .alignItems(HorizontalAlign.Start)
        .padding(12)
        .backgroundColor('#ffffff')
        .borderRadius(8)
      }, (question: PublicQuestionOut, qIndex: number) => question.id)

      // 已保存作答汇总(Record 展开,明确标注非正确答案)
      this.SavedAnswersView(toSavedAnswerRows(session))
    }
    .width('100%')
    .alignItems(HorizontalAlign.Start)
    .padding(12)
    .backgroundColor('#fafafa')
    .borderRadius(8)
  }

  /** 已保存作答汇总视图:仅学员已保存答案,明确非正确答案 */
  @Builder
  SavedAnswersView(rows: Array<SavedAnswerRow>) {
    Column({ space: 6 }) {
      Text('已保存作答(学员已保存的答案,非正确答案)')
        .fontSize(13)
        .fontWeight(FontWeight.Bold)
        .width('100%')
      if (rows.length === 0) {
        Text('暂无已保存作答')
          .fontSize(12)
          .fontColor('#888888')
          .width('100%')
      } else {
        ForEach(rows, (row: SavedAnswerRow) => {
          Row({ space: 8 }) {
            Text(row.questionId)
              .fontSize(12)
              .fontColor('#666666')
              .layoutWeight(1)
              .maxLines(1)
              .textOverflow({ overflow: TextOverflow.Ellipsis })
            Text(`已选: ${row.answerKey}`)
              .fontSize(12)
              .fontColor('#2e7d32')
          }
          .width('100%')
        }, (row: SavedAnswerRow) => `${row.questionId}=${row.answerKey}`)
      }
    }
    .width('100%')
    .alignItems(HorizontalAlign.Start)
    .padding(12)
    .backgroundColor('#f6ffed')
    .borderRadius(8)
  }
}

@Component
struct PaperCard {
  @Prop paper: PaperOut;

  build() {
    Column({ space: 8 }) {
      // 标题 + 年份
      Row({ space: 8 }) {
        Text(this.paper.title)
          .fontSize(16)
          .fontWeight(FontWeight.Bold)
          .layoutWeight(1)
          .maxLines(2)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
        if (this.paper.year !== null && this.paper.year !== undefined) {
          Text(`${this.paper.year}`)
            .fontSize(13)
            .fontColor('#666666')
        }
      }
      .width('100%')

      // 副标题
      if (this.paper.subtitle && this.paper.subtitle.length > 0) {
        Text(this.paper.subtitle)
          .fontSize(13)
          .fontColor('#555555')
          .maxLines(2)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
          .width('100%')
      }

      // 元数据行(使用 Flex Wrap 支持窄屏换行)
      Flex({ wrap: FlexWrap.Wrap }) {
        if (this.paper.source && this.paper.source.length > 0) {
          Text(`来源: ${this.paper.source}`)
            .fontSize(12)
            .fontColor('#888888')
            .margin({ right: 8 })
        }
        if (this.paper.university && this.paper.university.length > 0) {
          Text(`院校: ${this.paper.university}`)
            .fontSize(12)
            .fontColor('#888888')
            .margin({ right: 8 })
        }
        if (this.paper.subject && this.paper.subject.length > 0) {
          Text(`学科: ${this.paper.subject}`)
            .fontSize(12)
            .fontColor('#888888')
            .margin({ right: 8 })
        }
        if (this.paper.difficulty && this.paper.difficulty.length > 0) {
          Text(`难度: ${this.paper.difficulty}`)
            .fontSize(12)
            .fontColor('#888888')
            .margin({ right: 8 })
        }
        if (this.paper.duration_minutes !== null && this.paper.duration_minutes !== undefined) {
          Text(`时长: ${this.paper.duration_minutes} 分钟`)
            .fontSize(12)
            .fontColor('#888888')
            .margin({ right: 8 })
        }
      }
      .width('100%')

      // 标签
      if (this.paper.tags && this.paper.tags.length > 0) {
        Flex({ wrap: FlexWrap.Wrap }) {
          ForEach(this.paper.tags, (tag: string, index: number) => {
            Text(tag)
              .fontSize(11)
              .fontColor('#1890ff')
              .backgroundColor('#e6f7ff')
              .padding({ left: 6, right: 6, top: 2, bottom: 2 })
              .borderRadius(4)
              .margin({ right: 4, bottom: 4 })
          }, (tag: string, index: number) => tag)
        }
        .width('100%')
      }

      // 原文链接
      if (this.paper.origin_url && this.paper.origin_url.length > 0) {
        Text(`原文: ${this.paper.origin_url}`)
          .fontSize(11)
          .fontColor('#1890ff')
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
          .width('100%')
      }
    }
    .width('100%')
    .padding(12)
    .backgroundColor('#fafafa')
    .borderRadius(8)
    .border({ width: { bottom: 1 }, color: '#eeeeee' })
  }
}
