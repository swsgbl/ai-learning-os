/**
 * SearchPane.ets — 搜索 Tab 全流程(hm-parity v4,对齐 Android SearchScreen)
 *
 * 功能面(与 Android M12-04 SearchScreen.kt 对齐):
 *   1. 搜索源注册表(GET /providers):名称/kind 标签/可用性/不可用原因;
 *   2. 两段式搜索:输入查询 → 「预览计划」(POST /plan 槽位识别+计划,未执行)
 *      → 「搜索」(POST /queries 执行);
 *   3. 执行结果:概要(query_id/结果数/耗时/请求源) + 结果列表 + 被跳过的源;
 *   4. 记录查证:输入 query_id → GET /queries/{id} 回查执行记录。
 *
 * 呈现边界(与 Android 一致):全部字段原样来自服务端,零结果如实显示空态,
 * 错误不静默不虚报;unavailable_reason/rank_reason/skipped reason 原样透出。
 *
 * 工程纪律(v1-v3 沿用):requestJson 复用(bearer fail-closed + detail 透出);
 * disposed + generation 竞态防护(每类请求独立 generation);URL 监听刷新;
 * 密钥/token 零持久化零日志。
 */

import { common } from '@kit.AbilityKit';
import { emitter } from '@kit.BasicServicesKit';
import { ApiResult, SearchProviderOut, SearchProvidersData, getSearchProviders } from '../AiosApi';
import {
  SearchPlanData,
  SearchPlanItemData,
  SearchQueryData,
  SearchRecordData,
  SearchSkippedData,
  SearchResultItemData,
  getSearchRecord,
  postSearchPlan,
  postSearchQueries
} from './SearchApi';
import {
  DEFAULT_API_BASE_URL,
  EVENT_ID_URL_CHANGED,
  loadBaseUrl
} from './SettingsStore';

/** 单个异步操作的状态(各请求独立跟踪,互不干扰) */
enum OpStatus {
  IDLE = 'IDLE',
  LOADING = 'LOADING',
  DONE = 'DONE',
  ERROR = 'ERROR'
}

/** kind → 中文标签(local-corpus→本地语料, web→网页, 其余原样) */
function providerKindLabel(kind: string): string {
  if (kind === 'local-corpus') {
    return '本地语料';
  }
  if (kind === 'web') {
    return '网页';
  }
  return kind;
}

/** authority → 中文标签(official→官方来源, community→社区来源, 其余原样) */
function authorityLabel(authority: string): string {
  if (authority === 'official') {
    return '官方来源';
  }
  if (authority === 'community') {
    return '社区来源';
  }
  return authority;
}

@Component
export struct SearchPane {
  // ---------- providers(只读列表,v1 原样保留) ----------
  @State provStatus: OpStatus = OpStatus.LOADING;
  @State providers: Array<SearchProviderOut> = [];
  @State provError: string = '';

  // ---------- 查询输入 ----------
  @State queryInput: string = '';

  // ---------- 计划预览(两段式第一步) ----------
  @State planStatus: OpStatus = OpStatus.IDLE;
  @State planData: SearchPlanData | null = null;
  @State planError: string = '';

  // ---------- 执行搜索(两段式第二步) ----------
  @State searchStatus: OpStatus = OpStatus.IDLE;
  @State searchData: SearchQueryData | null = null;
  @State searchError: string = '';

  // ---------- 记录回查 ----------
  @State recordInput: string = '';
  @State recordStatus: OpStatus = OpStatus.IDLE;
  @State recordData: SearchRecordData | null = null;
  @State recordError: string = '';

  @State baseUrl: string = DEFAULT_API_BASE_URL;
  @State ctxError: string = '';

  private hostContext: common.Context | undefined = undefined;
  private disposed: boolean = false;
  private provGen: number = 0;
  private planGen: number = 0;
  private searchGen: number = 0;
  private recordGen: number = 0;


  /** 唯一回调引用,用于精确退订(emitter.off(eventId, callback)) */
  private urlChangeListener: (event: emitter.EventData) => void = (event: emitter.EventData) => {
    if (this.disposed) {
      return;
    }
    const ctx: common.Context | undefined = this.hostContext;
    if (ctx === undefined || ctx === null) {
      return;
    }
    loadBaseUrl(ctx).then((freshUrl: string) => {
      if (this.disposed) {
        return;
      }
      this.baseUrl = freshUrl;
      this.loadProviders();
    }).catch((e: Error) => {
      if (this.disposed) {
        return;
      }
      this.provStatus = OpStatus.ERROR;
      this.provError = `读取配置失败: ${e}`;
    });
  };

  async aboutToAppear(): Promise<void> {
    const ctx: common.Context | undefined =
      this.getUIContext().getHostContext() as common.Context | undefined;
    if (ctx === undefined || ctx === null) {
      this.ctxError = '无法获取应用上下文(getHostContext 为空),面板暂不可用';
      this.provStatus = OpStatus.ERROR;
      this.provError = this.ctxError;
      return;
    }
    this.hostContext = ctx;
    const persistedUrl: string = await loadBaseUrl(this.hostContext);
    if (this.disposed) {
      return;
    }
    this.baseUrl = persistedUrl;
    emitter.off(EVENT_ID_URL_CHANGED, this.urlChangeListener);
    emitter.on(EVENT_ID_URL_CHANGED, this.urlChangeListener);
    this.loadProviders();
  }

  aboutToDisappear(): void {
    this.disposed = true;
    emitter.off(EVENT_ID_URL_CHANGED, this.urlChangeListener);
  }

  // ---------- 动作 ----------

  private async loadProviders(): Promise<void> {
    if (this.disposed) {
      return;
    }
    const gen: number = ++this.provGen;
    this.provStatus = OpStatus.LOADING;
    this.provError = '';
    const r: ApiResult<SearchProvidersData> = await getSearchProviders(this.baseUrl);
    if (this.disposed || gen !== this.provGen) {
      return;
    }
    if (!r.ok || r.data === null) {
      this.provStatus = OpStatus.ERROR;
      this.provError = r.error;
      return;
    }
    if (!Array.isArray(r.data.items)) {
      this.provStatus = OpStatus.ERROR;
      this.provError = '响应格式错误: items 必须为数组';
      return;
    }
    this.providers = r.data.items;
    this.provStatus = OpStatus.DONE;
  }

  private async previewPlan(): Promise<void> {
    if (this.disposed || this.planStatus === OpStatus.LOADING || this.searchStatus === OpStatus.LOADING) {
      return;
    }
    const q: string = this.queryInput.trim();
    if (q.length === 0) {
      this.planStatus = OpStatus.ERROR;
      this.planError = '请输入查询词';
      return;
    }
    const gen: number = ++this.planGen;
    this.planStatus = OpStatus.LOADING;
    this.planError = '';
    const r: ApiResult<SearchPlanData> = await postSearchPlan(this.baseUrl, q, null);
    if (this.disposed || gen !== this.planGen) {
      return;
    }
    if (!r.ok || r.data === null) {
      this.planStatus = OpStatus.ERROR;
      this.planError = r.error;
      return;
    }
    this.planData = r.data;
    this.planStatus = OpStatus.DONE;
  }

  private async runSearch(): Promise<void> {
    if (this.disposed || this.searchStatus === OpStatus.LOADING || this.planStatus === OpStatus.LOADING) {
      return;
    }
    const q: string = this.queryInput.trim();
    if (q.length === 0) {
      this.searchStatus = OpStatus.ERROR;
      this.searchError = '请输入查询词';
      return;
    }
    const gen: number = ++this.searchGen;
    this.searchStatus = OpStatus.LOADING;
    this.searchError = '';
    const r: ApiResult<SearchQueryData> = await postSearchQueries(this.baseUrl, q, null, 10);
    if (this.disposed || gen !== this.searchGen) {
      return;
    }
    if (!r.ok || r.data === null) {
      this.searchStatus = OpStatus.ERROR;
      this.searchError = r.error;
      return;
    }
    this.searchData = r.data;
    this.searchStatus = OpStatus.DONE;
  }

  private async lookupRecord(): Promise<void> {
    if (this.disposed || this.recordStatus === OpStatus.LOADING) {
      return;
    }
    const raw: string = this.recordInput.trim();
    const id: number = Number(raw);
    if (raw.length === 0 || !Number.isFinite(id) || id <= 0) {
      this.recordStatus = OpStatus.ERROR;
      this.recordError = '请输入正整数查询编号';
      return;
    }
    const gen: number = ++this.recordGen;
    this.recordStatus = OpStatus.LOADING;
    this.recordError = '';
    const r: ApiResult<SearchRecordData> = await getSearchRecord(this.baseUrl, id);
    if (this.disposed || gen !== this.recordGen) {
      return;
    }
    if (!r.ok || r.data === null) {
      this.recordStatus = OpStatus.ERROR;
      this.recordError = r.error;
      return;
    }
    this.recordData = r.data;
    this.recordStatus = OpStatus.DONE;
  }


  // ---------- UI 构建辅助 ----------

  /** 状态行: 标签 + 值(横向铺满) */
  @Builder
  statusRow(label: string, value: string) {
    Row({ space: 8 }) {
      Text(label)
        .fontSize(13)
        .fontColor('#666666')
      Text(value.length > 0 ? value : '—')
        .fontSize(13)
        .layoutWeight(1)
        .maxLines(2)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
    }
    .width('100%')
  }

  /** 操作区标题(小节卡片头) */
  @Builder
  sectionTitle(title: string) {
    Text(title)
      .fontSize(16)
      .fontWeight(FontWeight.Bold)
      .width('100%')
  }

  build() {
    Scroll() {
      Column({ space: 12 }) {
        // 顶部: 标题 + 服务地址 + 刷新
        Row({ space: 8 }) {
          Text('搜索')
            .fontSize(20)
            .fontWeight(FontWeight.Bold)
          Text(this.baseUrl)
            .fontSize(11)
            .fontColor('#666666')
            .layoutWeight(1)
            .maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
          Button('刷新')
            .fontSize(12)
            .height(28)
            .onClick(() => {
              this.loadProviders();
            })
        }
        .width('100%')

        if (this.ctxError.length > 0) {
          Text(this.ctxError)
            .fontSize(14)
            .fontColor('#c62828')
            .width('100%')
        }

        // ===== 1. 搜索源注册表 =====
        Column({ space: 8 }) {
          this.sectionTitle('搜索源')
          if (this.provStatus === OpStatus.LOADING) {
            Text('正在获取搜索源...')
              .fontSize(14)
              .fontColor('#666666')
          } else if (this.provStatus === OpStatus.ERROR) {
            Column({ space: 8 }) {
              Text(this.provError)
                .fontSize(13)
                .fontColor('#c62828')
                .maxLines(3)
                .textOverflow({ overflow: TextOverflow.Ellipsis })
              Button('重试')
                .fontSize(13)
                .height(32)
                .onClick(() => {
                  this.loadProviders();
                })
            }
            .width('100%')
          } else if (this.providers.length === 0) {
            Text('暂无搜索 providers')
              .fontSize(14)
              .fontColor('#666666')
          } else {
            ForEach(this.providers, (provider: SearchProviderOut, index: number) => {
              ProviderCard({ provider: provider })
            }, (provider: SearchProviderOut, index: number) => `${provider.name}-${provider.kind}-${index}`)
          }
        }
        .width('100%')
        .padding(12)
        .backgroundColor('#fafafa')
        .borderRadius(8)

        // ===== 2. 查询输入(两段式) =====
        Column({ space: 8 }) {
          this.sectionTitle('查询')
          TextInput({ placeholder: '如：2024 清华大学 高等数学 选择题', text: this.queryInput })
            .onChange((value: string) => {
              this.queryInput = value;
            })
            .fontSize(14)
            .height(44)
            .width('100%')
          Row({ space: 10 }) {
            Button('预览计划')
              .fontSize(14)
              .height(38)
              .layoutWeight(1)
              .enabled(this.planStatus !== OpStatus.LOADING && this.searchStatus !== OpStatus.LOADING)
              .onClick(() => {
                this.previewPlan();
              })
            Button('搜索')
              .fontSize(14)
              .height(38)
              .layoutWeight(1)
              .enabled(this.searchStatus !== OpStatus.LOADING && this.planStatus !== OpStatus.LOADING)
              .onClick(() => {
                this.runSearch();
              })
          }
          .width('100%')
        }
        .width('100%')
        .padding(12)
        .backgroundColor('#fafafa')
        .borderRadius(8)


        // ===== 3. 计划预览(两段式第一步结果) =====
        if (this.planStatus === OpStatus.LOADING) {
          Column({ space: 6 }) {
            Text('⟳')
              .fontSize(22)
              .fontColor('#1890ff')
            Text('正在生成查询计划...')
              .fontSize(13)
              .fontColor('#666666')
          }
          .width('100%')
          .alignItems(HorizontalAlign.Center)
          .padding(16)
        }
        if (this.planStatus === OpStatus.ERROR) {
          Column({ space: 6 }) {
            Text(this.planError)
              .fontSize(13)
              .fontColor('#c62828')
              .maxLines(3)
              .textOverflow({ overflow: TextOverflow.Ellipsis })
          }
          .width('100%')
          .padding(12)
          .backgroundColor('#fff3f3')
          .borderRadius(8)
        }
        if (this.planStatus === OpStatus.DONE && this.planData !== null) {
          PlanCard({ plan: this.planData })
        }

        // ===== 4. 执行结果 =====
        if (this.searchStatus === OpStatus.LOADING) {
          Column({ space: 6 }) {
            Text('⟳')
              .fontSize(22)
              .fontColor('#1890ff')
            Text('正在搜索...')
              .fontSize(13)
              .fontColor('#666666')
          }
          .width('100%')
          .alignItems(HorizontalAlign.Center)
          .padding(16)
        }
        if (this.searchStatus === OpStatus.ERROR) {
          Column({ space: 6 }) {
            Text(this.searchError)
              .fontSize(13)
              .fontColor('#c62828')
              .maxLines(4)
              .textOverflow({ overflow: TextOverflow.Ellipsis })
            Button('重试')
              .fontSize(13)
              .height(32)
              .onClick(() => {
                this.runSearch();
              })
          }
          .width('100%')
          .padding(12)
          .backgroundColor('#fff3f3')
          .borderRadius(8)
        }
        if (this.searchStatus === OpStatus.DONE && this.searchData !== null) {
          OutcomeHeaderCard({ data: this.searchData })
          if (this.searchData.results.length === 0) {
            Text('没有找到结果。可以换个说法，或补充学科、年份等关键词。')
              .fontSize(13)
              .fontColor('#666666')
              .width('100%')
              .padding(12)
          } else {
            ForEach(this.searchData.results, (item: SearchResultItemData, index: number) => {
              ResultCard({ item: item })
            }, (item: SearchResultItemData, index: number) => `${item.url}-${index}`)
          }
          if (this.searchData.skipped.length > 0) {
            SkippedCard({ skipped: this.searchData.skipped })
          }
        }


        // ===== 5. 记录回查 =====
        Column({ space: 8 }) {
          this.sectionTitle('按编号回查')
          TextInput({ placeholder: '输入查询编号，如 42', text: this.recordInput })
            .onChange((value: string) => {
              this.recordInput = value;
            })
            .type(InputType.Number)
            .fontSize(14)
            .height(44)
            .width('100%')
          Button('回查记录')
            .fontSize(14)
            .height(38)
            .width('100%')
            .enabled(this.recordStatus !== OpStatus.LOADING)
            .onClick(() => {
              this.lookupRecord();
            })
          if (this.recordStatus === OpStatus.LOADING) {
            Text('正在回查...')
              .fontSize(13)
              .fontColor('#666666')
          }
          if (this.recordStatus === OpStatus.ERROR) {
            Text(this.recordError)
              .fontSize(13)
              .fontColor('#c62828')
              .maxLines(3)
              .textOverflow({ overflow: TextOverflow.Ellipsis })
          }
          if (this.recordStatus === OpStatus.DONE && this.recordData !== null) {
            RecordCard({ record: this.recordData })
          }
        }
        .width('100%')
        .padding(12)
        .backgroundColor('#fafafa')
        .borderRadius(8)
      }
      .width('100%')
      .padding(16)
    }
    .width('100%')
    .height('100%')
  }
}

// ==================== 子组件 ====================

/** 搜索源卡片(名称/kind 标签/可用性/不可用原因) */
@Component
struct ProviderCard {
  @Prop provider: SearchProviderOut;

  build() {
    Column({ space: 6 }) {
      Row({ space: 8 }) {
        Text(this.provider.name)
          .fontSize(15)
          .fontWeight(FontWeight.Bold)
          .layoutWeight(1)
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
        if (this.provider.enabled) {
          Text('可用')
            .fontSize(11)
            .fontColor('#2e7d32')
            .backgroundColor('#e8f5e9')
            .padding({ left: 6, right: 6, top: 2, bottom: 2 })
            .borderRadius(4)
        } else {
          Text('不可用')
            .fontSize(11)
            .fontColor('#c62828')
            .backgroundColor('#ffebee')
            .padding({ left: 6, right: 6, top: 2, bottom: 2 })
            .borderRadius(4)
        }
      }
      .width('100%')

      Text(`类型: ${providerKindLabel(this.provider.kind)}`)
        .fontSize(12)
        .fontColor('#888888')
        .width('100%')

      if (this.provider.unavailable_reason !== null &&
          this.provider.unavailable_reason !== undefined &&
          this.provider.unavailable_reason.length > 0) {
        Text(`不可用原因: ${this.provider.unavailable_reason}`)
          .fontSize(12)
          .fontColor('#c62828')
          .maxLines(2)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
          .width('100%')
      }
    }
    .width('100%')
    .padding(10)
    .backgroundColor('#ffffff')
    .borderRadius(6)
    .border({ width: 1, color: '#eeeeee' })
  }
}


/** 计划预览卡片:槽位识别 + 各源执行计划(未执行) */
@Component
struct PlanCard {
  @Prop plan: SearchPlanData;

  build() {
    Column({ space: 8 }) {
      Text('查询计划（预览，未执行）')
        .fontSize(15)
        .fontWeight(FontWeight.Bold)
        .width('100%')

      // 槽位识别:六槽位,未识别不显示(不虚报)
      if (this.plan.slots.subject === null && this.plan.slots.school === null &&
          this.plan.slots.year === null && this.plan.slots.course === null &&
          this.plan.slots.question_type === null && this.plan.slots.publicity === null) {
        Text(`未识别出结构化信息，将按原词「${this.plan.query}」搜索。`)
          .fontSize(13)
          .fontColor('#666666')
          .width('100%')
      } else {
        Column({ space: 4 }) {
          Text('识别到的信息')
            .fontSize(13)
            .fontWeight(FontWeight.Medium)
            .width('100%')
          if (this.plan.slots.subject !== null) {
            SlotRow({ label: '学科', value: this.plan.slots.subject })
          }
          if (this.plan.slots.school !== null) {
            SlotRow({ label: '学校', value: this.plan.slots.school })
          }
          if (this.plan.slots.year !== null) {
            SlotRow({ label: '年份', value: this.plan.slots.year })
          }
          if (this.plan.slots.course !== null) {
            SlotRow({ label: '课程', value: this.plan.slots.course })
          }
          if (this.plan.slots.question_type !== null) {
            SlotRow({ label: '题型', value: this.plan.slots.question_type })
          }
          if (this.plan.slots.publicity !== null) {
            SlotRow({ label: '公开范围', value: this.plan.slots.publicity })
          }
        }
        .width('100%')
        .padding(8)
        .backgroundColor('#f0f7ff')
        .borderRadius(6)
      }

      // 各源计划
      ForEach(this.plan.plan, (item: SearchPlanItemData, index: number) => {
        Column({ space: 2 }) {
          Row({ space: 8 }) {
            Text(item.provider)
              .fontSize(13)
              .fontWeight(FontWeight.Medium)
              .layoutWeight(1)
              .maxLines(1)
              .textOverflow({ overflow: TextOverflow.Ellipsis })
            if (item.enabled) {
              Text(`将搜索「${item.query}」`)
                .fontSize(12)
                .fontColor('#1890ff')
                .maxLines(1)
                .textOverflow({ overflow: TextOverflow.Ellipsis })
            } else {
              Text('不会执行')
                .fontSize(12)
                .fontColor('#c62828')
            }
          }
          .width('100%')
          if (!item.enabled && item.unavailable_reason !== null &&
              item.unavailable_reason !== undefined &&
              item.unavailable_reason.length > 0) {
            Text(item.unavailable_reason)
              .fontSize(11)
              .fontColor('#999999')
              .maxLines(2)
              .textOverflow({ overflow: TextOverflow.Ellipsis })
              .width('100%')
          }
        }
        .width('100%')
      }, (item: SearchPlanItemData, index: number) => `${item.provider}-${item.query}-${index}`)
    }
    .width('100%')
    .padding(12)
    .backgroundColor('#fafafa')
    .borderRadius(8)
  }
}

/** 槽位行(计划卡内) */
@Component
struct SlotRow {
  @Prop label: string;
  @Prop value: string;

  build() {
    Row({ space: 8 }) {
      Text(this.label)
        .fontSize(12)
        .fontColor('#666666')
      Text(this.value)
        .fontSize(12)
        .layoutWeight(1)
        .maxLines(1)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
    }
    .width('100%')
  }
}

/** 执行结果概要卡(query_id/结果数/耗时/请求源) */
@Component
struct OutcomeHeaderCard {
  @Prop data: SearchQueryData;

  build() {
    Column({ space: 4 }) {
      Text('搜索结果概要')
        .fontSize(15)
        .fontWeight(FontWeight.Bold)
        .width('100%')
      Row({ space: 8 }) {
        Text('查询编号')
          .fontSize(12)
          .fontColor('#666666')
        Text(`#${this.data.query_id}`)
          .fontSize(12)
          .fontWeight(FontWeight.Medium)
      }
      .width('100%')
      Row({ space: 8 }) {
        Text('结果数')
          .fontSize(12)
          .fontColor('#666666')
        Text(`${this.data.result_count} 条`)
          .fontSize(12)
      }
      .width('100%')
      Row({ space: 8 }) {
        Text('耗时')
          .fontSize(12)
          .fontColor('#666666')
        Text(`${this.data.duration_ms} ms`)
          .fontSize(12)
      }
      .width('100%')
      Row({ space: 8 }) {
        Text('请求的源')
          .fontSize(12)
          .fontColor('#666666')
          .maxLines(1)
        Text(this.data.providers_requested.join('、'))
          .fontSize(12)
          .layoutWeight(1)
          .maxLines(2)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
      }
      .width('100%')
    }
    .width('100%')
    .padding(12)
    .backgroundColor('#f0f7ff')
    .borderRadius(8)
  }
}


/** 单条结果卡片:标题/摘要/来源·provider·authority/排序理由/URL */
@Component
struct ResultCard {
  @Prop item: SearchResultItemData;

  build() {
    Column({ space: 4 }) {
      Text(this.item.title)
        .fontSize(14)
        .fontWeight(FontWeight.Bold)
        .maxLines(2)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
        .width('100%')
      Text(this.item.snippet)
        .fontSize(12)
        .fontColor('#444444')
        .maxLines(4)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
        .width('100%')
      Row({ space: 4 }) {
        Text(this.item.source)
          .fontSize(11)
          .fontColor('#888888')
        if (this.item.provider !== this.item.source) {
          Text(`·${this.item.provider}`)
            .fontSize(11)
            .fontColor('#888888')
        }
        if (this.item.authority !== null && this.item.authority !== undefined &&
            this.item.authority.length > 0) {
          Text(`·${authorityLabel(this.item.authority)}`)
            .fontSize(11)
            .fontColor('#888888')
        }
      }
      .width('100%')
      Text(`排序理由: ${this.item.rank_reason}`)
        .fontSize(11)
        .fontColor('#999999')
        .maxLines(2)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
        .width('100%')
      Text(this.item.url)
        .fontSize(10)
        .fontColor('#1890ff')
        .maxLines(2)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
        .width('100%')
    }
    .width('100%')
    .padding(10)
    .backgroundColor('#ffffff')
    .borderRadius(6)
    .border({ width: 1, color: '#eeeeee' })
  }
}

/** 被跳过的源卡片(provider + 原因如实) */
@Component
struct SkippedCard {
  @Prop skipped: Array<SearchSkippedData>;

  build() {
    Column({ space: 6 }) {
      Text('被跳过的源')
        .fontSize(14)
        .fontWeight(FontWeight.Bold)
        .width('100%')
      ForEach(this.skipped, (entry: SearchSkippedData, index: number) => {
        Column({ space: 2 }) {
          Text(entry.provider)
            .fontSize(13)
            .fontWeight(FontWeight.Medium)
            .width('100%')
          Text(`原因: ${entry.reason}`)
            .fontSize(11)
            .fontColor('#999999')
            .maxLines(2)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
            .width('100%')
        }
        .width('100%')
      }, (entry: SearchSkippedData, index: number) => `${entry.provider}-${entry.reason}-${index}`)
    }
    .width('100%')
    .padding(12)
    .backgroundColor('#fff8e1')
    .borderRadius(8)
  }
}

/** 回查记录卡片:原查询/结果数/耗时/请求源/记录时间/跳过源/结果 */
@Component
struct RecordCard {
  @Prop record: SearchRecordData;

  build() {
    Column({ space: 4 }) {
      Text(`记录 #${this.record.id}`)
        .fontSize(14)
        .fontWeight(FontWeight.Bold)
        .width('100%')
      Row({ space: 8 }) {
        Text('原查询词')
          .fontSize(12)
          .fontColor('#666666')
        Text(this.record.query)
          .fontSize(12)
          .layoutWeight(1)
          .maxLines(2)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
      }
      .width('100%')
      Row({ space: 8 }) {
        Text('结果数')
          .fontSize(12)
          .fontColor('#666666')
        Text(`${this.record.result_count} 条`)
          .fontSize(12)
      }
      .width('100%')
      Row({ space: 8 }) {
        Text('耗时')
          .fontSize(12)
          .fontColor('#666666')
        Text(`${this.record.duration_ms} ms`)
          .fontSize(12)
      }
      .width('100%')
      Row({ space: 8 }) {
        Text('请求的源')
          .fontSize(12)
          .fontColor('#666666')
        Text(this.record.providers_requested.join('、'))
          .fontSize(12)
          .layoutWeight(1)
          .maxLines(2)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
      }
      .width('100%')
      Row({ space: 8 }) {
        Text('记录时间')
          .fontSize(12)
          .fontColor('#666666')
        Text(this.record.created_at)
          .fontSize(11)
          .layoutWeight(1)
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
      }
      .width('100%')
      if (this.record.providers_skipped.length > 0) {
        Text('被跳过的源')
          .fontSize(12)
          .fontWeight(FontWeight.Medium)
          .width('100%')
        ForEach(this.record.providers_skipped, (entry: SearchSkippedData, index: number) => {
          Text(`${entry.provider}: ${entry.reason}`)
            .fontSize(11)
            .fontColor('#999999')
            .maxLines(2)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
            .width('100%')
        }, (entry: SearchSkippedData, index: number) => `${entry.provider}-${entry.reason}-${index}`)
      }
      if (this.record.results.length > 0) {
        Text('结果')
          .fontSize(12)
          .fontWeight(FontWeight.Medium)
          .width('100%')
        ForEach(this.record.results, (item: SearchResultItemData, index: number) => {
          Column({ space: 2 }) {
            Text(item.title)
              .fontSize(12)
              .fontWeight(FontWeight.Medium)
              .maxLines(2)
              .textOverflow({ overflow: TextOverflow.Ellipsis })
              .width('100%')
            Text(item.snippet)
              .fontSize(11)
              .fontColor('#444444')
              .maxLines(3)
              .textOverflow({ overflow: TextOverflow.Ellipsis })
              .width('100%')
            Text(`${item.provider}·排序理由: ${item.rank_reason}`)
              .fontSize(10)
              .fontColor('#999999')
              .maxLines(2)
              .textOverflow({ overflow: TextOverflow.Ellipsis })
              .width('100%')
          }
          .width('100%')
          .padding({ top: 4, bottom: 4 })
        }, (item: SearchResultItemData, index: number) => `${item.title}-${index}`)
      } else {
        Text('该次搜索没有结果。')
          .fontSize(11)
          .fontColor('#999999')
          .width('100%')
      }
    }
    .width('100%')
    .padding(10)
    .backgroundColor('#f5f5f5')
    .borderRadius(6)
  }
}
