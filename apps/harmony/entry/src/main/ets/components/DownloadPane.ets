/**
 * DownloadPane.ets — 下载与安装只读面板(M14-174)
 *
 * 功能(刻意收窄):
 *   - 三个只读区块:
 *     1) 渠道状态(静态事实,不发请求):PWA 网页版可用;Harmony 原生待发布
 *        (AGC 发布证书/Profile 未落地);Android 原生待发布(手机浏览器
 *        访问下载页,暂无 APK 直链);
 *     2) 服务版本(GET /api/v1/version,复用既有 AiosApi.getVersion,
 *        不新增后端契约;带 LOADING/SUCCESS/ERROR 状态机与重试);
 *     3) 下载页入口:由规范化服务地址推导为 base + '/download',
 *        纯文本展示,不自动打开浏览器、不写剪贴板(剪贴板 API 不引入);
 *   - /download 是网页入口而非 API 端点:mock 允许清单刻意不含它
 *     (见 tools/harmony_mock/server.py 与 test_contract.py 负断言),
 *     本面板也绝不向 /download 发起请求;
 *   - 使用 disposed 标志防止组件卸载后状态写入;
 *   - 请求代次(versionGeneration)防止竞态:过期响应不得覆盖较新状态;
 *   - URL 来自 SettingsStore.loadBaseUrl,监听 EVENT_ID_URL_CHANGED
 *     自动刷新:回调先 off 再 on 防重复注册;收到事件后重新读取持久化
 *     URL(不信任 payload),更新服务地址/下载页入口并刷新服务版本;
 *     aboutToDisappear 先设 disposed=true 再精确退订;
 *   - 诚实边界:不出现 原生安装包直链,不声明生产就绪承诺;Harmony
 *     原生渠道如实标注待发布原因(AGC 发布证书/Profile 未落地);
 *   - 只读边界:仅 1 个只读 GET(/api/v1/version),无任何写操作,
 *     不保存任何凭据,不申请任何新权限,无签名材料。
 */

import { common } from '@kit.AbilityKit';
import { emitter } from '@kit.BasicServicesKit';
import { ApiResult, VersionData, getVersion } from '../AiosApi';
import {
  DEFAULT_API_BASE_URL,
  EVENT_ID_URL_CHANGED,
  loadBaseUrl
} from './SettingsStore';

/** 区块状态机 */
enum SectionStatus {
  LOADING = 'LOADING',
  SUCCESS = 'SUCCESS',
  ERROR = 'ERROR'
}

/** key/value 行(渠道状态与服务版本共用行模型) */
interface KvRow {
  key: string;
  value: string;
}

/** 安全字符串化(缺字段/空串显示 '—') */
function safeStr(v: string | null | undefined): string {
  if (v === null || v === undefined || `${v}`.length === 0) {
    return '—';
  }
  return `${v}`;
}

/**
 * 下载页入口推导:规范化 base URL(可能带尾随 '/')剥掉尾部斜杠后
 * 拼接 '/download'。纯字符串推导,不发请求、不打开浏览器。
 */
function deriveDownloadEntry(baseUrl: string): string {
  let base: string = baseUrl;
  while (base.length > 0 && base.charAt(base.length - 1) === '/') {
    base = base.substring(0, base.length - 1);
  }
  return `${base}/download`;
}

@Component
export struct DownloadPane {
  @State baseUrl: string = DEFAULT_API_BASE_URL;
  @State ctxError: string = '';

  // ── 区块 2:服务版本 ─────────────────────────────────
  @State versionStatus: SectionStatus = SectionStatus.LOADING;
  @State versionData: VersionData | null = null;
  @State versionError: string = '';

  private hostContext: common.Context | undefined = undefined;
  private disposed: boolean = false;
  /** 请求代次:过期响应不得覆盖较新状态 */
  private versionGeneration: number = 0;
  /** 唯一回调引用,用于精确退订(emitter.off(eventId, callback)) */
  private urlChangeListener: (event: emitter.EventData) => void = (event: emitter.EventData) => {
    if (this.disposed) {
      return;
    }
    // 忽略事件 payload,重新读取持久化 URL(防御跨进程/其他写入者)
    const ctx: common.Context | undefined = this.hostContext;
    if (ctx === undefined || ctx === null) {
      return;
    }
    loadBaseUrl(ctx).then((freshUrl: string) => {
      if (this.disposed) {
        return;
      }
      this.baseUrl = freshUrl;
      this.loadVersion();
    }).catch((e: Error) => {
      if (this.disposed) {
        return;
      }
      // 读取配置失败:版本区如实进入错误态,不沿用旧数据冒充新地址结果
      this.versionData = null;
      this.versionError = `读取配置失败: ${e}`;
      this.versionStatus = SectionStatus.ERROR;
    });
  };

  async aboutToAppear(): Promise<void> {
    const ctx: common.Context | undefined =
      this.getUIContext().getHostContext() as common.Context | undefined;
    if (ctx === undefined || ctx === null) {
      this.ctxError = '无法获取应用上下文(getHostContext 为空),面板暂不可用';
      this.versionError = this.ctxError;
      this.versionStatus = SectionStatus.ERROR;
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
    this.loadVersion();
  }

  aboutToDisappear(): void {
    // 先标记销毁,再取消订阅,阻止后续回调和异步响应写入状态
    this.disposed = true;
    emitter.off(EVENT_ID_URL_CHANGED, this.urlChangeListener);
  }

  /** 服务版本响应形状校验(失败关闭):四个标量字段都必须为字符串 */
  private validateVersionShape(data: VersionData): string {
    if (typeof data.version !== 'string') {
      return '响应格式错误: version 必须为字符串';
    }
    if (typeof data.git_commit !== 'string') {
      return '响应格式错误: git_commit 必须为字符串';
    }
    if (typeof data.alembic_current !== 'string') {
      return '响应格式错误: alembic_current 必须为字符串';
    }
    if (typeof data.alembic_head !== 'string') {
      return '响应格式错误: alembic_head 必须为字符串';
    }
    return '';
  }

  private async loadVersion(): Promise<void> {
    // 若组件已销毁,立即返回,阻止任何状态写入
    if (this.disposed) {
      return;
    }
    // 记录本次请求的代次,用于后续判断是否为最新请求
    const generation: number = ++this.versionGeneration;
    this.versionStatus = SectionStatus.LOADING;
    this.versionError = '';
    const r: ApiResult<VersionData> = await getVersion(this.baseUrl);
    // 若已销毁或非最新代次,忽略响应,防止竞态导致的状态回退
    if (this.disposed || generation !== this.versionGeneration) {
      return;
    }
    if (!r.ok || r.data === null) {
      this.versionData = null;
      this.versionError = r.error;
      this.versionStatus = SectionStatus.ERROR;
      return;
    }
    // 入库前先做契约形状校验,不合格一律失败关闭
    const invalidReason: string = this.validateVersionShape(r.data);
    if (invalidReason !== '') {
      this.versionData = null;
      this.versionError = invalidReason;
      this.versionStatus = SectionStatus.ERROR;
      return;
    }
    this.versionData = r.data;
    this.versionStatus = SectionStatus.SUCCESS;
  }

  build() {
    Column({ space: 12 }) {
      // ── 面板标题与只读说明 ───────────────────────────
      Column({ space: 8 }) {
        Text('下载与安装(只读)')
          .fontSize(18)
          .fontWeight(FontWeight.Bold)
          .width('100%')

        Text('渠道状态 + 服务版本 + 下载页入口;纯文本展示,不自动打开浏览器,不写剪贴板')
          .fontSize(12)
          .fontColor('#666666')
          .width('100%')
          .maxLines(2)
          .textOverflow({ overflow: TextOverflow.Ellipsis })

        Text(`服务地址: ${this.baseUrl}`)
          .fontSize(12)
          .fontColor('#666666')
          .width('100%')
          .maxLines(2)
          .textOverflow({ overflow: TextOverflow.Ellipsis })

        // 上下文错误(拿不到宿主上下文时面板不可用)
        if (this.ctxError.length > 0) {
          Text(this.ctxError)
            .fontSize(13)
            .fontColor('#c62828')
            .width('100%')
            .maxLines(3)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
        }
      }
      .width('100%')
      .alignItems(HorizontalAlign.Start)

      // ── 区块 1:渠道状态(静态事实,不发请求)─────────
      Column({ space: 8 }) {
        Text('渠道状态')
          .fontSize(15)
          .fontWeight(FontWeight.Bold)
          .width('100%')
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })

        this.KvList([
          { key: 'PWA 网页版', value: '可用:浏览器访问服务地址即可使用' },
          { key: 'Harmony 原生', value: '待发布:AGC 发布证书/Profile 未落地,暂无安装包' },
          { key: 'Android 原生', value: '待发布:请用手机浏览器访问下载页,暂无 APK 直链' }
        ])
      }
      .width('100%')
      .alignItems(HorizontalAlign.Start)
      .padding(12)
      .backgroundColor('#fafafa')
      .borderRadius(8)
      .border({ width: { bottom: 1 }, color: '#eeeeee' })

      // ── 区块 2:服务版本(GET /api/v1/version)────────
      Column({ space: 8 }) {
        Row({ space: 8 }) {
          Text('服务版本')
            .fontSize(15)
            .fontWeight(FontWeight.Bold)
            .layoutWeight(1)
            .maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
          Button('刷新')
            .fontSize(11)
            .height(26)
            .enabled(this.versionStatus !== SectionStatus.LOADING)
            .onClick(() => {
              this.loadVersion();
            })
        }
        .width('100%')

        if (this.versionStatus === SectionStatus.LOADING) {
          Text('加载中…')
            .fontSize(13)
            .fontColor('#888888')
        } else if (this.versionStatus === SectionStatus.SUCCESS && this.versionData !== null) {
          this.KvList([
            { key: '版本', value: safeStr(this.versionData.version) },
            { key: 'Git 提交', value: safeStr(this.versionData.git_commit) },
            { key: 'Alembic current', value: safeStr(this.versionData.alembic_current) },
            { key: 'Alembic head', value: safeStr(this.versionData.alembic_head) }
          ])
        } else if (this.versionStatus === SectionStatus.ERROR) {
          Text(this.versionError)
            .fontSize(13)
            .fontColor('#c62828')
            .width('100%')
            .maxLines(3)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
          Button('重试')
            .fontSize(12)
            .height(30)
            .onClick(() => {
              this.loadVersion();
            })
        }
      }
      .width('100%')
      .alignItems(HorizontalAlign.Start)
      .padding(12)
      .backgroundColor('#fafafa')
      .borderRadius(8)
      .border({ width: { bottom: 1 }, color: '#eeeeee' })

      // ── 区块 3:下载页入口(纯文本推导)───────────────
      Column({ space: 8 }) {
        Text('下载页入口(纯文本)')
          .fontSize(15)
          .fontWeight(FontWeight.Bold)
          .width('100%')
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })

        Text(deriveDownloadEntry(this.baseUrl))
          .fontSize(13)
          .width('100%')
          .maxLines(3)
          .textOverflow({ overflow: TextOverflow.Ellipsis })

        Text('入口由规范化服务地址推导;请手动复制到浏览器打开。应用不代为打开浏览器,也不写剪贴板。')
          .fontSize(12)
          .fontColor('#666666')
          .width('100%')
          .maxLines(3)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
      }
      .width('100%')
      .alignItems(HorizontalAlign.Start)
      .padding(12)
      .backgroundColor('#fafafa')
      .borderRadius(8)
      .border({ width: { bottom: 1 }, color: '#eeeeee' })
    }
    .width('100%')
    .alignItems(HorizontalAlign.Start)
  }

  /** key/value 行列表(固定行模型) */
  @Builder
  KvList(rows: Array<KvRow>) {
    ForEach(rows, (row: KvRow) => {
      Row({ space: 8 }) {
        Text(row.key)
          .fontSize(13)
          .fontColor('#666666')
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
          .width(110)
        Text(row.value)
          .fontSize(13)
          .layoutWeight(1)
          .maxLines(3)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
      }
      .width('100%')
      .alignItems(VerticalAlign.Top)
    }, (row: KvRow) => `${row.key}=${row.value}`)
  }
}
