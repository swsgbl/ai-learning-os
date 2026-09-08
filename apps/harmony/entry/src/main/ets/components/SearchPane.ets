/**
 * SearchPane.ets — 搜索 providers 只读列表(M13-06)
 *
 * 功能(刻意收窄):
 *   - 通过 getSearchProviders(baseUrl) 加载并展示 /api/v1/search/providers 返回的
 *     SearchProviderOut[] 列表;
 *   - 只读展示:provider 名称、类型(kind)、是否启用、服务基地址(来自 baseUrl);
 *   - unavailable_reason 仅在非 null 时展示;enabled=false 时若 unavailable_reason 为空,
 *     显示「未启用」作为兜底文案;
 *   - 不发起任何写请求,不保存任何凭据,不访问麦克风/存储;
 *   - URL 来自 SettingsStore.loadBaseUrl,监听 EVENT_ID_URL_CHANGED 自动刷新;
 *   - 状态机:LOADING / EMPTY / SUCCESS / ERROR,带刷新/重试按钮;
 *   - 使用 disposed 标志防止组件卸载后状态写入;
 *   - 使用 requestGeneration 防止竞态(stale-response guard);
 *   - emitter 回调在 aboutToAppear 先 off 再 on,防止多次注册堆叠;
 *   - aboutToDisappear 先设 disposed=true 再退订,确保后续回调和异步响应被丢弃;
 *   - 元数据行使用 Flex(Wrap) 支持窄屏自动换行。
 */

import { common } from '@kit.AbilityKit';
import { emitter } from '@kit.BasicServicesKit';
import { ApiResult, SearchProviderOut, SearchProvidersData, getSearchProviders } from '../AiosApi';
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
export struct SearchPane {
  @State listStatus: ListStatus = ListStatus.LOADING;
  @State providers: Array<SearchProviderOut> = [];
  @State listError: string = '';
  @State baseUrl: string = DEFAULT_API_BASE_URL;
  @State ctxError: string = '';

  private hostContext: common.Context | undefined = undefined;
  private disposed: boolean = false;
  private requestGeneration: number = 0;
  /** 唯一回调引用,用于精确退订(emitter.off(eventId, callback)) */
  private urlChangeListener: (event: emitter.EventData) => void = (event: emitter.EventData) => {
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
      this.loadProviders();
    }).catch((e: Error) => {
      if (this.disposed) {
        return;
      }
      this.listStatus = ListStatus.ERROR;
      this.listError = `读取配置失败: ${e}`;
    });
  };

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
    this.loadProviders();
  }

  aboutToDisappear(): void {
    // 先标记销毁,再取消订阅,阻止后续回调和异步响应写入状态
    this.disposed = true;
    emitter.off(EVENT_ID_URL_CHANGED, this.urlChangeListener);
  }

  private async loadProviders(): Promise<void> {
    // 若组件已销毁,立即返回,阻止任何状态写入
    if (this.disposed) {
      return;
    }
    // 记录本次请求的代次,用于后续判断是否为最新请求
    const generation: number = ++this.requestGeneration;
    this.listStatus = ListStatus.LOADING;
    this.listError = '';
    const r: ApiResult<SearchProvidersData> = await getSearchProviders(this.baseUrl);
    // 若已销毁或非最新代次,忽略响应,防止竞态导致的状态回退
    if (this.disposed || generation !== this.requestGeneration) {
      return;
    }
    if (!r.ok || r.data === null) {
      this.listStatus = ListStatus.ERROR;
      this.listError = r.error;
      return;
    }
    // items 必须为数组,否则视为响应格式错误
    if (!Array.isArray(r.data.items)) {
      this.listStatus = ListStatus.ERROR;
      this.listError = '响应格式错误: items 必须为数组';
      return;
    }
    if (r.data.items.length === 0) {
      this.listStatus = ListStatus.EMPTY;
      return;
    }
    this.providers = r.data.items;
    this.listStatus = ListStatus.SUCCESS;
  }

  build() {
    Column({ space: 12 }) {
      // 标题栏
      Row({ space: 12 }) {
        Text('搜索 Providers')
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
            this.loadProviders();
          })
      }
      .width('100%')

      // 上下文错误
      if (this.ctxError.length > 0) {
        Text(this.ctxError)
          .fontSize(14)
          .fontColor('#c62828')
          .width('100%')
      }

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
              this.loadProviders();
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
          Text('暂无搜索 providers')
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
            ForEach(this.providers, (provider: SearchProviderOut, index: number) => {
              ProviderCard({ provider: provider })
            }, (provider: SearchProviderOut, index: number) => `${provider.name}-${provider.kind}-${index}`)
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
struct ProviderCard {
  @Prop provider: SearchProviderOut;

  build() {
    Column({ space: 8 }) {
      // 名称 + 类型行
      Row({ space: 8 }) {
        Text(this.provider.name)
          .fontSize(16)
          .fontWeight(FontWeight.Bold)
          .layoutWeight(1)
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
        // 状态标签
        if (this.provider.enabled) {
          Text('已启用')
            .fontSize(11)
            .fontColor('#2e7d32')
            .backgroundColor('#e8f5e9')
            .padding({ left: 6, right: 6, top: 2, bottom: 2 })
            .borderRadius(4)
        } else {
          Text('未启用')
            .fontSize(11)
            .fontColor('#c62828')
            .backgroundColor('#ffebee')
            .padding({ left: 6, right: 6, top: 2, bottom: 2 })
            .borderRadius(4)
        }
      }
      .width('100%')

      // 元数据行(使用 Flex Wrap 支持窄屏换行)
      Flex({ wrap: FlexWrap.Wrap }) {
        Text(`类型: ${this.provider.kind}`)
          .fontSize(12)
          .fontColor('#888888')
          .margin({ right: 8 })
      }
      .width('100%')

      // unavailable_reason:仅当非 null 且非空时展示
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
    .padding(12)
    .backgroundColor('#fafafa')
    .borderRadius(8)
    .border({ width: { bottom: 1 }, color: '#eeeeee' })
  }
}
