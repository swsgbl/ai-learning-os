/**
 * VoicePane.ets — 语音 providers 只读面板(M13-07)
 *
 * 功能(刻意收窄):
 *   - 通过 getVoiceProviders(baseUrl) 加载并展示 /api/v1/voice/providers 返回的
 *     VoiceProvidersData(voice_mode、ASR/TTS provider、隐私开关);
 *   - 只读边界:本面板不访问麦克风、不申请音频权限、不调用语音识别/合成、
 *     不创建语音会话、不发起任何 POST/写请求、不保存任何凭据;
 *     语音交互本身未实现,此处仅展示服务端 provider 配置;
 *   - URL 来自 SettingsStore.loadBaseUrl,监听 EVENT_ID_URL_CHANGED 自动刷新;
 *   - 状态机:LOADING / SUCCESS / ERROR(响应是一个对象,无有意义的空态);
 *   - 使用 disposed 标志防止组件卸载后状态写入;
 *   - 使用 requestGeneration 防止竞态(stale-response guard);
 *   - emitter 回调在 aboutToAppear 先 off 再 on,防止多次注册堆叠;
 *   - aboutToDisappear 先设 disposed=true 再退订,确保后续回调和异步响应被丢弃;
 *   - 成功视图展示 voice_mode;ASR/TTS 卡片展示 provider/fallback,
 *     requested 仅在非 null 时展示;隐私开关仅作事实标签展示;
 *   - 卡片内部元数据行使用 Flex(Wrap) 支持窄屏自动换行。
 */

import { common } from '@kit.AbilityKit';
import { emitter } from '@kit.BasicServicesKit';
import { ApiResult, VoiceProviderViewOut, VoiceProvidersData, getVoiceProviders } from '../AiosApi';
import {
  DEFAULT_API_BASE_URL,
  EVENT_ID_URL_CHANGED,
  loadBaseUrl
} from './SettingsStore';

enum LoadStatus {
  LOADING = 'LOADING',
  SUCCESS = 'SUCCESS',
  ERROR = 'ERROR'
}

@Component
export struct VoicePane {
  @State loadStatus: LoadStatus = LoadStatus.LOADING;
  @State providers: VoiceProvidersData | null = null;
  @State loadError: string = '';
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
      this.loadStatus = LoadStatus.ERROR;
      this.loadError = `读取配置失败: ${e}`;
    });
  };

  async aboutToAppear(): Promise<void> {
    const ctx: common.Context | undefined =
      this.getUIContext().getHostContext() as common.Context | undefined;
    if (ctx === undefined || ctx === null) {
      this.ctxError = '无法获取应用上下文(getHostContext 为空),面板暂不可用';
      this.loadStatus = LoadStatus.ERROR;
      this.loadError = this.ctxError;
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
    this.loadStatus = LoadStatus.LOADING;
    this.loadError = '';
    const r: ApiResult<VoiceProvidersData> = await getVoiceProviders(this.baseUrl);
    // 若已销毁或非最新代次,忽略响应,防止竞态导致的状态回退
    if (this.disposed || generation !== this.requestGeneration) {
      return;
    }
    if (!r.ok || r.data === null) {
      this.loadStatus = LoadStatus.ERROR;
      this.loadError = r.error;
      return;
    }
    // 基本形状校验:关键字段缺失/类型不符视为响应格式错误,如实报错不猜测
    const d: VoiceProvidersData = r.data;
    // 完整契约校验:voice_mode 为字符串;asr/tts 各自存在、requested 为 null 或字符串、
    // provider 为字符串、fallback 为布尔;privacy_store_audio / privacy_send_context_to_cloud
    // 为布尔。任一字段缺失或类型不符视为响应格式错误,如实报错不猜测
    const validAsr: boolean = d.asr !== null && d.asr !== undefined &&
      typeof d.asr.provider === 'string' &&
      (d.asr.requested === null || typeof d.asr.requested === 'string') &&
      typeof d.asr.fallback === 'boolean';
    const validTts: boolean = d.tts !== null && d.tts !== undefined &&
      typeof d.tts.provider === 'string' &&
      (d.tts.requested === null || typeof d.tts.requested === 'string') &&
      typeof d.tts.fallback === 'boolean';
    if (typeof d.voice_mode !== 'string' || !validAsr || !validTts ||
        typeof d.privacy_store_audio !== 'boolean' ||
        typeof d.privacy_send_context_to_cloud !== 'boolean') {
      this.loadStatus = LoadStatus.ERROR;
      this.loadError = '响应格式错误: voice_mode/asr/tts/privacy 字段缺失或类型不符';
      return;
    }
    this.providers = d;
    this.loadStatus = LoadStatus.SUCCESS;
  }

  build() {
    Column({ space: 12 }) {
      // 标题栏
      Row({ space: 12 }) {
        Text('语音 Providers')
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

      // 只读边界说明(始终展示,不因加载状态而消失)
      Text('只读视图:仅展示服务端 provider 配置,不访问麦克风,不进行语音识别或合成')
        .fontSize(12)
        .fontColor('#666666')
        .width('100%')

      // 错误状态
      if (this.loadStatus === LoadStatus.ERROR) {
        Column({ space: 12 }) {
          Text(this.loadError)
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
      if (this.loadStatus === LoadStatus.LOADING) {
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

      // 成功视图
      if (this.loadStatus === LoadStatus.SUCCESS && this.providers !== null) {
        Scroll() {
          Column({ space: 12 }) {
            // voice_mode 卡片
            Column({ space: 8 }) {
              Row({ space: 8 }) {
                Text('语音模式 (voice_mode)')
                  .fontSize(15)
                  .fontWeight(FontWeight.Bold)
                  .layoutWeight(1)
                  .maxLines(1)
                  .textOverflow({ overflow: TextOverflow.Ellipsis })
                Text(this.providers.voice_mode)
                  .fontSize(12)
                  .fontColor('#2e7d32')
                  .backgroundColor('#e8f5e9')
                  .padding({ left: 6, right: 6, top: 2, bottom: 2 })
                  .borderRadius(4)
              }
              .width('100%')
            }
            .width('100%')
            .padding(12)
            .backgroundColor('#fafafa')
            .borderRadius(8)
            .border({ width: { bottom: 1 }, color: '#eeeeee' })

            // ASR provider 卡片
            ProviderCard({
              title: 'ASR (语音识别)',
              providerView: this.providers.asr
            })

            // TTS provider 卡片
            ProviderCard({
              title: 'TTS (语音合成)',
              providerView: this.providers.tts
            })

            // 隐私开关卡片(仅事实标签,无任何开关控件)
            Column({ space: 8 }) {
              Text('隐私开关 (服务端配置,只读)')
                .fontSize(15)
                .fontWeight(FontWeight.Bold)
                .maxLines(1)
                .textOverflow({ overflow: TextOverflow.Ellipsis })
                .width('100%')
              Flex({ wrap: FlexWrap.Wrap }) {
                Text(`存储音频 (privacy_store_audio): ${this.providers.privacy_store_audio ? '开' : '关'}`)
                  .fontSize(12)
                  .fontColor('#888888')
                  .margin({ right: 8 })
                Text(`发送上下文到云端 (privacy_send_context_to_cloud): ${this.providers.privacy_send_context_to_cloud ? '开' : '关'}`)
                  .fontSize(12)
                  .fontColor('#888888')
              }
              .width('100%')
            }
            .width('100%')
            .padding(12)
            .backgroundColor('#fafafa')
            .borderRadius(8)
            .border({ width: { bottom: 1 }, color: '#eeeeee' })
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

/** ASR/TTS provider 单项卡片(requested 仅在非 null 时展示) */
@Component
struct ProviderCard {
  @Prop title: string;
  @Prop providerView: VoiceProviderViewOut;

  build() {
    Column({ space: 8 }) {
      // 标题 + 回退标记
      Row({ space: 8 }) {
        Text(this.title)
          .fontSize(15)
          .fontWeight(FontWeight.Bold)
          .layoutWeight(1)
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
        if (this.providerView.fallback) {
          Text('已回退')
            .fontSize(11)
            .fontColor('#e65100')
            .backgroundColor('#fff3e0')
            .padding({ left: 6, right: 6, top: 2, bottom: 2 })
            .borderRadius(4)
        }
      }
      .width('100%')

      // 元数据行(使用 Flex Wrap 支持窄屏换行)
      Flex({ wrap: FlexWrap.Wrap }) {
        Text(`Provider: ${this.providerView.provider}`)
          .fontSize(12)
          .fontColor('#888888')
          .margin({ right: 8 })
      }
      .width('100%')

      // requested:仅当非 null 时展示
      if (this.providerView.requested !== null &&
          this.providerView.requested !== undefined &&
          this.providerView.requested.length > 0) {
        Text(`Requested: ${this.providerView.requested}`)
          .fontSize(12)
          .fontColor('#666666')
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
