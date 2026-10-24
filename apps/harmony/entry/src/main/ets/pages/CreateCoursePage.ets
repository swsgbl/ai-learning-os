/**
 * CreateCoursePage.ets — 定制课程页(hm-parity v3 缺口 B,对齐 Android CreateCourseScreen)
 *
 * 流程:输入学习目标 → POST generation-drafts(请求体 { goal },服务端
 * 同步跑完八阶段管线后 201 直接返回完整草稿,LLM 兜底可达数十秒,无
 * pending→ready 异步流转,不轮询)→ 渲染 plan['outline'] 大纲 →
 * materialize 固化(201 带 question_count)→ 回学习 Tab。
 * 竞态防护:disposed;未登录如实提示;422 等错误 detail 原样透出。
 */

import { common } from '@kit.AbilityKit';
import { hasBearerToken } from '../AiosApi';
import {
  GenerationDraft, MaterializeResult, createGenerationDraft, materializeDraft
} from '../V3Api';
import { DEFAULT_API_BASE_URL, loadBaseUrl } from '../components/SettingsStore';

/** 页面阶段 */
enum CoursePhase {
  /** 输入学习目标(生成中也停留在此阶段,busy 态提示等待) */
  INPUT = 'INPUT',
  /** 大纲就绪,待固化 */
  READY = 'READY',
  /** 固化完成 */
  DONE = 'DONE'
}

/** plan['outline'] 章节行(以 plan 实际键为准,防御式取) */
interface OutlineChapter {
  chapter_no: number;
  title: string;
}

@Entry
@Component
struct CreateCoursePage {
  @State phase: CoursePhase = CoursePhase.INPUT;
  @State baseUrl: string = DEFAULT_API_BASE_URL;
  @State topic: string = '';
  @State error: string = '';
  @State busy: boolean = false;
  @State draftId: string = '';
  @State draft: GenerationDraft | null = null;
  @State doneTitle: string = '';
  @State donePaperId: string = '';
  @State doneQuestionCount: number = 0;

  private disposed: boolean = false;

  async aboutToAppear(): Promise<void> {
    const ctx: common.Context | undefined =
      this.getUIContext().getHostContext() as common.Context | undefined;
    if (ctx !== undefined && ctx !== null) {
      this.baseUrl = await loadBaseUrl(ctx);
    }
  }

  aboutToDisappear(): void {
    this.disposed = true;
  }

  /** 提交学习目标,一次 POST 同步取回完整草稿 */
  private async submit(): Promise<void> {
    const t: string = this.topic.trim();
    if (t.length === 0) {
      this.error = '请先输入学习目标';
      return;
    }
    if (!hasBearerToken()) {
      this.error = '未登录——请先在「设置」Tab 登录后再定制课程';
      return;
    }
    if (this.busy) {
      return;
    }
    this.busy = true;
    this.error = '';
    const r = await createGenerationDraft(this.baseUrl, t);
    if (this.disposed) {
      return;
    }
    this.busy = false;
    if (!r.ok || r.data === null) {
      // 422(概念图未覆盖+LLM 失败)等 detail 服务端已写好引导文案,原样透出
      this.error = r.error;
      return;
    }
    this.draftId = r.data.id;
    this.draft = r.data;
    this.phase = CoursePhase.READY;
  }

  /** 固化入库 */
  private async doMaterialize(): Promise<void> {
    if (this.busy || this.draftId.length === 0) {
      return;
    }
    this.busy = true;
    this.error = '';
    const r = await materializeDraft(this.baseUrl, this.draftId);
    if (this.disposed) {
      return;
    }
    this.busy = false;
    if (!r.ok || r.data === null) {
      this.error = r.error;
      return;
    }
    const m: MaterializeResult = r.data;
    this.doneTitle = m.title;
    this.donePaperId = m.paper_id;
    this.doneQuestionCount = m.question_count;
    this.phase = CoursePhase.DONE;
  }

  /** 重新开始 */
  private restart(): void {
    this.phase = CoursePhase.INPUT;
    this.topic = '';
    this.error = '';
    this.draftId = '';
    this.draft = null;
    this.doneTitle = '';
    this.donePaperId = '';
    this.doneQuestionCount = 0;
  }

  /** 防御式取 plan['outline'];取不到返回空数组(展示 generation_note 兜底) */
  private outlineChapters(): Array<OutlineChapter> {
    if (this.draft === null || this.draft.plan === null || this.draft.plan === undefined) {
      return [];
    }
    const outline: Object | undefined = this.draft.plan['outline'];
    if (outline === undefined || outline === null) {
      return [];
    }
    return outline as Array<OutlineChapter>;
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
        Text('定制课程')
          .fontSize(20)
          .fontWeight(FontWeight.Bold)
          .layoutWeight(1)
          .maxLines(1)
      }
      .width('100%')

      // 未登录提示
      if (!hasBearerToken()) {
        Text('未登录——定制课程需要登录(生成与固化均需登录态)。')
          .fontSize(13)
          .fontColor('#8c6d1f')
          .backgroundColor('#fff8e1')
          .borderRadius(8)
          .padding(12)
          .width('100%')
      }

      if (this.error.length > 0) {
        Text(this.error)
          .fontSize(13)
          .fontColor('#c62828')
          .backgroundColor('#fff3f3')
          .borderRadius(6)
          .padding(8)
          .width('100%')
          .maxLines(6)
      }

      // ── 阶段 1:输入学习目标(生成中=busy 态) ──
      if (this.phase === CoursePhase.INPUT) {
        Column({ space: 10 }) {
          Text('描述你的学习目标,AI 将为你定制一份课程大纲并生成试卷。')
            .fontSize(13)
            .fontColor('#666666')
            .width('100%')
          TextArea({ placeholder: '例如:两周内掌握线性代数第一章(矩阵与行列式),目标是能独立解出期末难度的计算题' })
            .fontSize(13)
            .width('100%')
            .height(120)
            .enabled(!this.busy)
            .onChange((v: string) => {
              this.topic = v;
            })
          Button(this.busy ? '生成中…' : '生成课程草稿')
            .fontSize(14)
            .height(40)
            .width('100%')
            .backgroundColor('#1890ff')
            .enabled(!this.busy && this.topic.trim().length > 0)
            .onClick(() => {
              this.submit();
            })
          if (this.busy) {
            Text('生成中…LLM 动态生成可达数十秒,请勿退出')
              .fontSize(13)
              .fontColor('#1890ff')
              .width('100%')
              .textAlign(TextAlign.Center)
          }
        }
        .width('100%')
        .padding(12)
        .backgroundColor('#f5f5f5')
        .borderRadius(8)
        .alignItems(HorizontalAlign.Start)
      }

      // ── 阶段 2:大纲就绪 ──
      if (this.phase === CoursePhase.READY) {
        Column({ space: 10 }) {
          Text('课程大纲已生成')
            .fontSize(15)
            .fontWeight(FontWeight.Bold)
            .width('100%')
          if (this.draft !== null) {
            Text(`共 ${this.draft.chapter_count} 章 · ${this.draft.generation_note}`)
              .fontSize(12)
              .fontColor('#666666')
              .width('100%')
              .maxLines(3)
          }
          Scroll() {
            Column({ space: 6 }) {
              if (this.outlineChapters().length > 0) {
                ForEach(this.outlineChapters(), (ch: OutlineChapter) => {
                  Text(`第${ch.chapter_no}章 ${ch.title}`)
                    .fontSize(13)
                    .fontColor('#333333')
                    .width('100%')
                }, (ch: OutlineChapter) => `${ch.chapter_no}-${ch.title}`)
              } else if (this.draft !== null) {
                // 取不到 outline 时显示 generation_note,不硬编码「(大纲为空)」
                Text(this.draft.generation_note)
                  .fontSize(13)
                  .fontColor('#333333')
                  .width('100%')
              }
            }
            .width('100%')
            .alignItems(HorizontalAlign.Start)
          }
          .layoutWeight(1)
          .scrollBar(BarState.Off)
          .backgroundColor('#ffffff')
          .borderRadius(6)
          .padding(8)
          Button(this.busy ? '固化中…' : '固化入库(生成试卷)')
            .fontSize(14)
            .height(40)
            .width('100%')
            .backgroundColor('#2e7d32')
            .enabled(!this.busy)
            .onClick(() => {
              this.doMaterialize();
            })
        }
        .width('100%')
        .layoutWeight(1)
        .alignItems(HorizontalAlign.Start)
      }

      // ── 阶段 3:完成 ──
      if (this.phase === CoursePhase.DONE) {
        Column({ space: 10 }) {
          Text('✓ 课程已入库')
            .fontSize(18)
            .fontWeight(FontWeight.Bold)
            .fontColor('#2e7d32')
          Text(this.doneTitle)
            .fontSize(15)
            .fontWeight(FontWeight.Bold)
            .maxLines(2)
          Text(`已固化 ${this.doneQuestionCount} 题练习卷 · 试卷 ID: ${this.donePaperId}`)
            .fontSize(12)
            .fontColor('#888888')
          Text('已生成到你的试卷库,可到「学习」Tab 开始考试。')
            .fontSize(13)
            .fontColor('#666666')
          Button('回到学习 Tab')
            .fontSize(14)
            .height(40)
            .width('100%')
            .backgroundColor('#1890ff')
            .onClick(() => {
              this.getUIContext().getRouter().back();
            })
          Button('再定制一门')
            .fontSize(13)
            .height(34)
            .width('100%')
            .backgroundColor('#eeeeee')
            .onClick(() => {
              this.restart();
            })
        }
        .width('100%')
        .alignItems(HorizontalAlign.Center)
        .padding(24)
      }
    }
    .width('100%')
    .height('100%')
    .padding(16)
    .alignItems(HorizontalAlign.Start)
  }
}
