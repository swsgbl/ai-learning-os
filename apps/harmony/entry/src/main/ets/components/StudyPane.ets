/**
 * StudyPane.ets — 论文列表只读面板(M13-05)
 *
 * 功能(刻意收窄):
 *   - 通过 getPapers(baseUrl) 加载并展示 AiosEndpoint.PAPERS 返回的 PaperOut 数组;
 *   - 只读展示:标题/副标题/来源/院校/年份/学科/难度/时长/标签/原文链接;
 *   - 不发起任何写请求,不保存任何凭据,不访问麦克风/存储;
 *   - URL 来自 SettingsStore.loadBaseUrl,监听 EVENT_ID_URL_CHANGED 自动刷新;
 *   - 状态机:LOADING / EMPTY / SUCCESS / ERROR,带刷新/重试按钮;
 *   - 元数据行使用 Flex(Wrap) 支持窄屏自动换行。
 */

import { common } from '@kit.AbilityKit';
import { emitter } from '@kit.BasicServicesKit';
import { ApiResult, PaperOut, getPapers } from '../AiosApi';
import {
  DEFAULT_API_BASE_URL,
  EVENT_ID_URL_CHANGED,
  loadBaseUrl
} from './SettingsStore';

enum ListStatus {
  LOADING = 'LOADING',
  EMPTY = 'EMPTY',
  SUCCESS = 'SUCCESS',
  ERROR = 'ERROR'
}

@Component
export struct StudyPane {
  @State listStatus: ListStatus = ListStatus.LOADING;
  @State listData: Array<PaperOut> = [];
  @State listError: string = '';
  @State baseUrl: string = DEFAULT_API_BASE_URL;
  @State ctxError: string = '';

  private hostContext: common.Context | undefined = undefined;
  private disposed: boolean = false;
  private requestGeneration: number = 0;

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
    }).catch((e: Error) => {
      if (this.disposed) {
        return;
      }
      this.listStatus = ListStatus.ERROR;
      this.listError = `读取配置失败: ${e}`;
    });
  };

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

  build() {
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

      // 成功列表
      if (this.listStatus === ListStatus.SUCCESS) {
        Scroll() {
          Column({ space: 12 }) {
            ForEach(this.listData, (paper: PaperOut, index: number) => {
              PaperCard({ paper: paper })
            }, (paper: PaperOut, index: number) => paper.id)
          }
          .padding(16)
        }
        .layoutWeight(1)
      }
    }
    .width('100%')
    .height('100%')
    .padding(16)
    .alignItems(HorizontalAlign.Start)
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
