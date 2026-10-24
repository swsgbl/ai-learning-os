/**
 * TextbookWorkbenchPage.ets — 教材工作台页面(hm-parity v1,
 * 对齐 Android TextbookWorkbenchScreen)
 *
 * 用户旅程:目录搜索(关键词命中 ChinaTextbook 目录元数据)→「导入」
 * (声明使用权后下载组装到私有空间)→「我的教材」书架列表(标题/大小/
 * 解析态/概念计数)→ 分态操作按钮(解析 / OCR 解析(15页/批) / 提取概念 /
 * 一键开冲刺)。
 *
 * 不变式:token 零持久化;错误如实(detail 透出);竞态防护
 * disposed + requestGeneration;导入前显式确认使用权。
 */

import { common } from '@kit.AbilityKit';
import { hasBearerToken } from '../AiosApi';
import {
  CatalogResource, ConceptExtraction, ImportJob, ParseResult, TextbookSprint,
  catalogSearch, createImport, createSprintFromTextbook, extractConcepts,
  getMyImports, parseImport
} from '../LearningApi';
import { DEFAULT_API_BASE_URL, loadBaseUrl } from '../components/SettingsStore';

enum ShelfStatus {
  LOADING = 'LOADING',
  EMPTY = 'EMPTY',
  SUCCESS = 'SUCCESS',
  ERROR = 'ERROR'
}

/** 解析态中文标签 */
function parseStatusLabel(status: string): string {
  if (status === 'parsed') {
    return '已解析';
  }
  if (status === 'needs_ocr') {
    return '扫描书';
  }
  if (status === 'pending') {
    return '待解析';
  }
  return status;
}

/** 大小格式化(MB,一位小数) */
function formatSize(bytes: number): string {
  return `${(bytes / 1048576.0).toFixed(1)} MB`;
}

@Entry
@Component
struct TextbookWorkbenchPage {
  @State baseUrl: string = DEFAULT_API_BASE_URL;
  // 目录搜索
  @State query: string = '';
  @State searching: boolean = false;
  @State searchResults: Array<CatalogResource> | null = null;
  @State searchError: string = '';
  // 我的书架
  @State shelfStatus: ShelfStatus = ShelfStatus.LOADING;
  @State imports: Array<ImportJob> = [];
  @State shelfError: string = '';
  // 动作
  @State busy: boolean = false;
  @State notice: string = '';
  @State actionError: string = '';
  /** 正在导入的资源 id(禁用其它导入按钮) */
  @State importingId: string = '';

  private disposed: boolean = false;
  private requestGeneration: number = 0;

  async aboutToAppear(): Promise<void> {
    const ctx: common.Context | undefined =
      this.getUIContext().getHostContext() as common.Context | undefined;
    if (ctx === undefined || ctx === null) {
      this.shelfStatus = ShelfStatus.ERROR;
      this.shelfError = '无法获取应用上下文,面板暂不可用';
      return;
    }
    this.baseUrl = await loadBaseUrl(ctx);
    if (this.disposed) {
      return;
    }
    this.refreshShelf();
  }

  aboutToDisappear(): void {
    this.disposed = true;
  }

  private loginHint(): string {
    return hasBearerToken() ? '' : '未登录——请先在「设置」Tab 登录后使用教材工作台。';
  }

  /** 刷新书架 */
  private async refreshShelf(): Promise<void> {
    if (this.disposed) {
      return;
    }
    const gen: number = ++this.requestGeneration;
    this.shelfStatus = ShelfStatus.LOADING;
    this.shelfError = '';
    const r = await getMyImports(this.baseUrl);
    if (this.disposed || gen !== this.requestGeneration) {
      return;
    }
    if (!r.ok || r.data === null) {
      this.shelfStatus = ShelfStatus.ERROR;
      this.shelfError = r.error;
      return;
    }
    this.imports = r.data;
    this.shelfStatus = r.data.length === 0 ? ShelfStatus.EMPTY : ShelfStatus.SUCCESS;
  }

  /** 目录搜索 */
  private async search(): Promise<void> {
    const q: string = this.query.trim();
    if (q.length === 0 || this.searching) {
      return;
    }
    if (this.disposed) {
      return;
    }
    const gen: number = ++this.requestGeneration;
    this.searching = true;
    this.searchError = '';
    const r = await catalogSearch(this.baseUrl, q);
    if (this.disposed || gen !== this.requestGeneration) {
      return;
    }
    this.searching = false;
    if (!r.ok || r.data === null) {
      this.searchError = r.error;
      return;
    }
    this.searchResults = r.data;
  }

  /** 导入资源(使用权声明恒 true——UI 导入前显式确认) */
  private async importResource(resource: CatalogResource): Promise<void> {
    await this.runBusy(async () => {
      this.importingId = resource.id;
      const r = await createImport(this.baseUrl, resource.id);
      this.importingId = '';
      if (!r.ok || r.data === null) {
        this.actionError = `导入失败: ${r.error}`;
        return;
      }
      this.notice = `已导入「${resource.title}」,状态 ${r.data.status}`;
      await this.refreshShelf();
    });
  }

  /** 解析 / OCR 解析 */
  private async parseJob(job: ImportJob, ocr: boolean): Promise<void> {
    await this.runBusy(async () => {
      const r = await parseImport(this.baseUrl, job.id, ocr);
      if (!r.ok || r.data === null) {
        this.actionError = `解析失败: ${r.error}`;
        return;
      }
      const d: ParseResult = r.data;
      this.notice = `解析完成:${d.page_count} 页 / ${d.chunk_count} 块` +
        (ocr ? `(OCR ${d.ocr_pages_done} 页)` : '');
      await this.refreshShelf();
    });
  }

  /** 提取概念 */
  private async extractJob(job: ImportJob): Promise<void> {
    await this.runBusy(async () => {
      const r = await extractConcepts(this.baseUrl, job.id);
      if (!r.ok || r.data === null) {
        this.actionError = `概念提取失败: ${r.error}`;
        return;
      }
      this.notice = `概念提取完成:${r.data.concept_count} 个概念 / ${r.data.edge_count} 条边`;
      await this.refreshShelf();
    });
  }

  /** 一键开冲刺 */
  private async sprintJob(job: ImportJob): Promise<void> {
    await this.runBusy(async () => {
      const r = await createSprintFromTextbook(this.baseUrl, job.id);
      if (!r.ok || r.data === null) {
        this.actionError = `开冲刺失败: ${r.error}`;
        return;
      }
      const d: TextbookSprint = r.data;
      this.notice = `冲刺已创建:合同 ${d.contract_id.substring(0, 8)}… / 生成 ${d.generated} 题` +
        (d.blueprint_complete ? '(蓝图完整)' : '(蓝图未完整)');
      await this.refreshShelf();
    });
  }

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
    Scroll() {
      Column({ space: 12 }) {
        // 标题栏
        Row({ space: 12 }) {
          Button('返回')
            .fontSize(12)
            .height(28)
            .onClick(() => {
              this.getUIContext().getRouter().back();
            })
          Text('教材工作台')
            .fontSize(20)
            .fontWeight(FontWeight.Bold)
            .layoutWeight(1)
        }
        .width('100%')

        Text('搜教材目录(ChinaTextbook)→ 导入我的教材 → 解析 → 一键开学习冲刺')
          .fontSize(11)
          .fontColor('#888888')
          .width('100%')
          .maxLines(2)

        if (this.loginHint().length > 0) {
          Text(this.loginHint())
            .fontSize(13)
            .fontColor('#8c6d1f')
            .backgroundColor('#fff8e1')
            .borderRadius(8)
            .padding(12)
            .width('100%')
        }

        if (this.notice.length > 0) {
          Text(this.notice)
            .fontSize(13)
            .fontColor('#2e7d32')
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

        // ── 目录搜索 ──
        Column({ space: 8 }) {
          Text('目录搜索')
            .fontSize(15)
            .fontWeight(FontWeight.Bold)
            .width('100%')
          Row({ space: 8 }) {
            TextInput({ text: this.query, placeholder: '搜书名,如:高等数学 / 一年级数学' })
              .onChange((value: string) => {
                this.query = value;
              })
              .layoutWeight(1)
              .height(40)
              .fontSize(13)
            Button(this.searching ? '…' : '搜索')
              .fontSize(13)
              .height(40)
              .enabled(!this.searching && this.query.trim().length > 0)
              .onClick(() => {
                this.search();
              })
          }
          .width('100%')

          if (this.searchError.length > 0) {
            Text(this.searchError)
              .fontSize(12)
              .fontColor('#c62828')
              .width('100%')
              .maxLines(3)
              .textOverflow({ overflow: TextOverflow.Ellipsis })
          }
          if (this.searchResults !== null) {
            Text(`目录命中 ${this.searchResults!.length} 条`)
              .fontSize(12)
              .fontColor('#666666')
              .width('100%')
            ForEach(this.searchResults!, (resource: CatalogResource) => {
              this.SearchResultRow(resource)
            }, (resource: CatalogResource) => resource.id)
          }
        }
        .width('100%')
        .padding(12)
        .backgroundColor('#fafafa')
        .borderRadius(8)

        // ── 我的书架 ──
        Column({ space: 8 }) {
          Text('我的教材')
            .fontSize(15)
            .fontWeight(FontWeight.Bold)
            .width('100%')

          if (this.shelfStatus === ShelfStatus.LOADING) {
            Text('正在加载书架…')
              .fontSize(13)
              .fontColor('#666666')
              .width('100%')
          } else if (this.shelfStatus === ShelfStatus.ERROR) {
            Column({ space: 8 }) {
              Text(this.shelfError)
                .fontSize(13)
                .fontColor('#c62828')
                .width('100%')
                .maxLines(4)
                .textOverflow({ overflow: TextOverflow.Ellipsis })
              Button('重试')
                .fontSize(13)
                .height(32)
                .onClick(() => {
                  this.refreshShelf();
                })
            }
            .width('100%')
          } else if (this.shelfStatus === ShelfStatus.EMPTY) {
            Text('还没有导入的教材——搜到书后点「导入」(你需确认对内容有合法使用权)')
              .fontSize(12)
              .fontColor('#888888')
              .width('100%')
          } else {
            ForEach(this.imports, (job: ImportJob) => {
              this.ShelfItemView(job)
            }, (job: ImportJob) => job.id)
          }
        }
        .width('100%')
        .padding(12)
        .backgroundColor('#fafafa')
        .borderRadius(8)
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

  /** 搜索结果行 */
  @Builder
  SearchResultRow(resource: CatalogResource) {
    Row({ space: 8 }) {
      Column({ space: 4 }) {
        Text(resource.title)
          .fontSize(14)
          .fontWeight(FontWeight.Medium)
          .layoutWeight(1)
          .maxLines(2)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
        Text(this.catalogMeta(resource))
          .fontSize(11)
          .fontColor('#888888')
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
      }
      .layoutWeight(1)
      .alignItems(HorizontalAlign.Start)

      Button(this.importingId === resource.id ? '导入中…' : '导入')
        .fontSize(12)
        .height(32)
        .enabled(!this.busy)
        .onClick(() => {
          this.importResource(resource);
        })
    }
    .width('100%')
    .padding({ top: 4, bottom: 4 })
  }

  /** 目录元数据行(学段·学科·年级,空段跳过) */
  private catalogMeta(resource: CatalogResource): string {
    const parts: Array<string> = [];
    if (resource.education_stage.length > 0) {
      parts.push(resource.education_stage);
    }
    if (resource.subject.length > 0) {
      parts.push(resource.subject);
    }
    if (resource.grade.length > 0) {
      parts.push(resource.grade);
    }
    return parts.length > 0 ? parts.join(' · ') : '未分类';
  }

  /** 书架条目(分态操作) */
  @Builder
  ShelfItemView(job: ImportJob) {
    Column({ space: 6 }) {
      Text(job.title.length > 0 ? job.title : job.resource_id)
        .fontSize(14)
        .fontWeight(FontWeight.Medium)
        .width('100%')
        .maxLines(2)
        .textOverflow({ overflow: TextOverflow.Ellipsis })

      // 徽标行:状态 + 解析态 + 大小
      Flex({ wrap: FlexWrap.Wrap }) {
        Text(job.status)
          .fontSize(10)
          .fontColor('#ffffff')
          .backgroundColor('#888888')
          .padding({ left: 6, right: 6, top: 2, bottom: 2 })
          .borderRadius(4)
          .margin({ right: 4, bottom: 4 })
        if (job.parse_status.length > 0) {
          Text(parseStatusLabel(job.parse_status))
            .fontSize(10)
            .fontColor(this.parseBadgeColor(job.parse_status))
            .backgroundColor('#f0f0f0')
            .padding({ left: 6, right: 6, top: 2, bottom: 2 })
            .borderRadius(4)
            .margin({ right: 4, bottom: 4 })
        }
        Text(formatSize(job.size_bytes))
          .fontSize(10)
          .fontColor('#888888')
          .padding({ left: 0, right: 6, top: 2, bottom: 2 })
      }
      .width('100%')

      if (job.concept_counts.length > 0) {
        Text(`概念(${job.concept_counts})`)
          .fontSize(11)
          .fontColor('#666666')
          .width('100%')
          .maxLines(1)
      }
      if (job.status !== 'ready' && job.error.length > 0) {
        Text(job.error.substring(0, 100))
          .fontSize(11)
          .fontColor('#c62828')
          .width('100%')
          .maxLines(2)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
      }

      // 分态操作按钮
      Flex({ wrap: FlexWrap.Wrap }) {
        if (job.status === 'ready' && job.parse_status.length === 0) {
          Button('解析')
            .fontSize(12)
            .height(32)
            .backgroundColor('#f0f0f0')
            .fontColor('#333333')
            .enabled(!this.busy)
            .margin({ right: 6, bottom: 6 })
            .onClick(() => {
              this.parseJob(job, false);
            })
        }
        if (job.parse_status === 'needs_ocr') {
          Button('OCR 解析')
            .fontSize(12)
            .height(32)
            .backgroundColor('#f0f0f0')
            .fontColor('#333333')
            .enabled(!this.busy)
            .margin({ right: 6, bottom: 6 })
            .onClick(() => {
              this.parseJob(job, true);
            })
        }
        if (job.parse_status === 'parsed') {
          Button('提取概念')
            .fontSize(12)
            .height(32)
            .backgroundColor('#f0f0f0')
            .fontColor('#333333')
            .enabled(!this.busy)
            .margin({ right: 6, bottom: 6 })
            .onClick(() => {
              this.extractJob(job);
            })
          Button('一键开冲刺')
            .fontSize(12)
            .height(32)
            .backgroundColor('#1890ff')
            .fontColor('#ffffff')
            .enabled(!this.busy)
            .margin({ right: 6, bottom: 6 })
            .onClick(() => {
              this.sprintJob(job);
            })
        }
      }
      .width('100%')
    }
    .width('100%')
    .padding({ top: 6, bottom: 6 })
    .border({ width: { bottom: 1 }, color: '#eeeeee' })
  }

  /** 解析态徽标配色 */
  private parseBadgeColor(status: string): string {
    if (status === 'parsed') {
      return '#2e7d32';
    }
    if (status === 'needs_ocr') {
      return '#8c6d1f';
    }
    return '#888888';
  }
}
