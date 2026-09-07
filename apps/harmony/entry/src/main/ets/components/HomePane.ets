/**
 * HomePane.ets — 首页六区只读面板(M13-01 第 3c-1 小步, M13-03 使用官方 emitter API)
 *
 * 功能(刻意收窄):
 *   - 六个只读区块:服务健康 / 认证状态 / 隐私模式 / 服务版本 / 运维快照 / 审计日志;
 *   - aboutToAppear 经 getUIContext().getHostContext() 取上下文,loadBaseUrl 读
 *     已保存地址(缺省 DEFAULT_API_BASE_URL),然后整体刷新一次(仅一次);
 *   - 通过 emitter.on 订阅 SettingsStore.saveBaseUrl 成功通知:收到新 URL 后重新读取
 *     持久化 URL(防御跨进程/其他写入者),更新服务地址显示,并刷新六区;
 *   - aboutToDisappear 退订以避免重复触发和内存泄漏;
 *   - 每区独立 idle/loading/success/error 状态机 + 独立重试按钮;失败如实展示
 *     原始错误(HTTP 状态 / 网络层错误 / URL 校验文案),绝不虚构数据;
 *   - 只调用 6 个只读 GET:getHealth / getAuthStatus / getPrivacy / getVersion /
 *     getOpsSnapshot / getAudit;无任何写 API;
 *   - auth_enabled=false 时显示「本地模式(认证未开启)」;
 *   - 审计条目只渲染 id/action/actor_username/target_type/target_id/created_at,
 *     绝不渲染 before/after(UI 契约不投影载荷字段);
 *   - Record<string,number> 经 Object.keys 转成 key/value 行;空表显示「暂无数据」;
 *   - 全程无 token/password/key,不做路由接线(接线由后续小步负责)。
 *
 * M13-03 Supervisor Feedback 修正:
 *   1. 事件 ID 由 SettingsStore 统一管理导出,HomePane 只导入
 *   2. 使用回调专用 off 重载,避免移除其他订阅者
 *   3. 无非空断言,this.hostContext 用闭包捕获或显式检查
 *   4. HomePane 始终以 loadBaseUrl 为权威来源,不依赖 emit payload
 *   5. aboutToAppear 先 off 再 on,防止多次注册堆叠回调
 */

import { common } from '@kit.AbilityKit';
import { emitter } from '@kit.BasicServicesKit';
import {
  ApiResult,
  AuditEntry,
  AuthStatusData,
  HealthData,
  OpsSnapshotData,
  PrivacyData,
  VersionData,
  getAudit,
  getAuthStatus,
  getHealth,
  getOpsSnapshot,
  getPrivacy,
  getVersion
} from '../AiosApi';
import {
  DEFAULT_API_BASE_URL,
  EVENT_ID_URL_CHANGED,
  loadBaseUrl
} from './SettingsStore';

/** 区块状态机(六区共用同一枚举,各自持有一份状态) */
enum ZoneStatus {
  IDLE = 0,
  LOADING = 1,
  SUCCESS = 2,
  ERROR = 3
}

/** key/value 行(Record<string,number> 展开后的行模型) */
interface KvRow {
  key: string;
  value: string;
}

/** Record<string,number> -> key/value 行;空值返回空数组 */
function toKvRows(map: Record<string, number> | null | undefined): Array<KvRow> {
  if (map === null || map === undefined) {
    return [];
  }
  const rows: Array<KvRow> = [];
  const keys: Array<string> = Object.keys(map);
  for (let i = 0; i < keys.length; i++) {
    const k: string = keys[i];
    rows.push({ key: k, value: `${map[k]}` });
  }
  return rows;
}

/** 安全字符串化(缺字段显示 '—') */
function safeStr(v: string | null | undefined): string {
  if (v === null || v === undefined || `${v}`.length === 0) {
    return '—';
  }
  return `${v}`;
}

@Component
export struct HomePane {
  @State baseUrl: string = DEFAULT_API_BASE_URL;
  @State ctxError: string = '';
  @State healthStatus: ZoneStatus = ZoneStatus.IDLE;
  @State healthData: HealthData | null = null;
  @State healthError: string = '';
  @State authSt: ZoneStatus = ZoneStatus.IDLE;
  @State authData: AuthStatusData | null = null;
  @State authError: string = '';
  @State privacyStatus: ZoneStatus = ZoneStatus.IDLE;
  @State privacyData: PrivacyData | null = null;
  @State privacyError: string = '';
  @State versionStatus: ZoneStatus = ZoneStatus.IDLE;
  @State versionData: VersionData | null = null;
  @State versionError: string = '';
  @State opsStatus: ZoneStatus = ZoneStatus.IDLE;
  @State opsData: OpsSnapshotData | null = null;
  @State opsError: string = '';
  @State auditStatus: ZoneStatus = ZoneStatus.IDLE;
  @State auditData: Array<AuditEntry> = [];
  @State auditError: string = '';
  private hostContext: common.Context | undefined = undefined;
  /** 唯一回调引用,用于精确退订(emitter.off(eventId, callback)) */
  private urlChangeListener: (event: emitter.EventData) => void = (event: emitter.EventData) => {
    const ctx: common.Context | undefined = this.hostContext;
    if (ctx === undefined || ctx === null) {
      return;
    }
    loadBaseUrl(ctx).then((freshUrl: string) => {
      this.baseUrl = freshUrl;
      this.refreshAll();
    });
  };

  async aboutToAppear(): Promise<void> {
    const ctx: common.Context | undefined =
      this.getUIContext().getHostContext() as common.Context | undefined;
    if (ctx === undefined || ctx === null) {
      this.ctxError = '无法获取应用上下文(getHostContext 为空),面板暂不可用';
      return;
    }
    this.hostContext = ctx;
    // 初始化加载一次持久化 URL
    this.baseUrl = await loadBaseUrl(this.hostContext);
    // 注册 URL 变更订阅:SettingsStore.saveBaseUrl 成功后触发刷新
    // 先 off 再 on:防止 aboutToAppear 被多次调用时堆叠回调
    emitter.off(EVENT_ID_URL_CHANGED, this.urlChangeListener);
    emitter.on(EVENT_ID_URL_CHANGED, this.urlChangeListener);
    // 初始加载后刷新一次
    this.refreshAll();
  }

  aboutToDisappear(): void {
    // 组件销毁时退订,防止重复触发和内存泄漏
    // 使用带 callback 的 off 重载,仅移除本组件的订阅,不影响其他订阅者
    emitter.off(EVENT_ID_URL_CHANGED, this.urlChangeListener);
  }

  /** 整体刷新:六区并行发起,各自独立推进状态机 */
  refreshAll(): void {
    this.loadHealth();
    this.loadAuthStatus();
    this.loadPrivacy();
    this.loadVersion();
    this.loadOpsSnapshot();
    this.loadAudit();
  }

  private async loadHealth(): Promise<void> {
    this.healthStatus = ZoneStatus.LOADING;
    this.healthError = '';
    const r: ApiResult<HealthData> = await getHealth(this.baseUrl);
    if (r.ok && r.data !== null) {
      this.healthData = r.data;
      this.healthStatus = ZoneStatus.SUCCESS;
    } else {
      this.healthData = null;
      this.healthError = r.error;
      this.healthStatus = ZoneStatus.ERROR;
    }
  }

  private async loadAuthStatus(): Promise<void> {
    this.authSt = ZoneStatus.LOADING;
    this.authError = '';
    const r: ApiResult<AuthStatusData> = await getAuthStatus(this.baseUrl);
    if (r.ok && r.data !== null) {
      this.authData = r.data;
      this.authSt = ZoneStatus.SUCCESS;
    } else {
      this.authData = null;
      this.authError = r.error;
      this.authSt = ZoneStatus.ERROR;
    }
  }

  private async loadPrivacy(): Promise<void> {
    this.privacyStatus = ZoneStatus.LOADING;
    this.privacyError = '';
    const r: ApiResult<PrivacyData> = await getPrivacy(this.baseUrl);
    if (r.ok && r.data !== null) {
      this.privacyData = r.data;
      this.privacyStatus = ZoneStatus.SUCCESS;
    } else {
      this.privacyData = null;
      this.privacyError = r.error;
      this.privacyStatus = ZoneStatus.ERROR;
    }
  }

  private async loadVersion(): Promise<void> {
    this.versionStatus = ZoneStatus.LOADING;
    this.versionError = '';
    const r: ApiResult<VersionData> = await getVersion(this.baseUrl);
    if (r.ok && r.data !== null) {
      this.versionData = r.data;
      this.versionStatus = ZoneStatus.SUCCESS;
    } else {
      this.versionData = null;
      this.versionError = r.error;
      this.versionStatus = ZoneStatus.ERROR;
    }
  }

  private async loadOpsSnapshot(): Promise<void> {
    this.opsStatus = ZoneStatus.LOADING;
    this.opsError = '';
    const r: ApiResult<OpsSnapshotData> = await getOpsSnapshot(this.baseUrl);
    if (r.ok && r.data !== null) {
      this.opsData = r.data;
      this.opsStatus = ZoneStatus.SUCCESS;
    } else {
      this.opsData = null;
      this.opsError = r.error;
      this.opsStatus = ZoneStatus.ERROR;
    }
  }

  private async loadAudit(): Promise<void> {
    this.auditStatus = ZoneStatus.LOADING;
    this.auditError = '';
    const r: ApiResult<Array<AuditEntry>> = await getAudit(this.baseUrl);
    if (r.ok && r.data !== null) {
      this.auditData = r.data;
      this.auditStatus = ZoneStatus.SUCCESS;
    } else {
      this.auditData = [];
      this.auditError = r.error;
      this.auditStatus = ZoneStatus.ERROR;
    }
  }

  build() {
    Scroll() {
      Column({ space: 12 }) {
        Text('AIOS 只读面板')
          .fontSize(20)
          .fontWeight(FontWeight.Bold)

        Text(`服务地址: ${this.baseUrl}`)
          .fontSize(12)
          .fontColor('#666666')

        Button('整体刷新')
          .onClick(() => {
            this.refreshAll();
          })

        if (this.ctxError.length > 0) {
          Text(this.ctxError)
            .fontSize(13)
            .fontColor('#c62828')
        }

        // ── 区块 1:服务健康 ─────────────────────────────
        Column({ space: 6 }) {
          Text('服务健康')
            .fontSize(16)
            .fontWeight(FontWeight.Bold)
          if (this.healthStatus === ZoneStatus.LOADING) {
            Text('加载中…')
              .fontSize(13)
              .fontColor('#888888')
          } else if (this.healthStatus === ZoneStatus.SUCCESS && this.healthData !== null) {
            Text(`status: ${safeStr(this.healthData.status)}  service: ${safeStr(this.healthData.service)}`)
              .fontSize(13)
          } else if (this.healthStatus === ZoneStatus.ERROR) {
            Text(this.healthError)
              .fontSize(13)
              .fontColor('#c62828')
            Button('重试')
              .fontSize(13)
              .height(32)
              .onClick(() => {
                this.loadHealth();
              })
          }
        }
        .alignItems(HorizontalAlign.Start)
        .padding(12)
        .borderRadius(8)
        .backgroundColor('#f5f5f5')
        .width('100%')

        // ── 区块 2:认证状态 ─────────────────────────────
        Column({ space: 6 }) {
          Text('认证状态')
            .fontSize(16)
            .fontWeight(FontWeight.Bold)
          if (this.authSt === ZoneStatus.LOADING) {
            Text('加载中…')
              .fontSize(13)
              .fontColor('#888888')
          } else if (this.authSt === ZoneStatus.SUCCESS && this.authData !== null) {
            Text(this.authData.auth_enabled ? '远程模式(认证已开启)' : '本地模式(认证未开启)')
              .fontSize(13)
              .fontColor(this.authData.auth_enabled ? '#2e7d32' : '#666666')
          } else if (this.authSt === ZoneStatus.ERROR) {
            Text(this.authError)
              .fontSize(13)
              .fontColor('#c62828')
            Button('重试')
              .fontSize(13)
              .height(32)
              .onClick(() => {
                this.loadAuthStatus();
              })
          }
        }
        .alignItems(HorizontalAlign.Start)
        .padding(12)
        .borderRadius(8)
        .backgroundColor('#f5f5f5')
        .width('100%')

        // ── 区块 3:隐私模式 ─────────────────────────────
        Column({ space: 6 }) {
          Text('隐私模式')
            .fontSize(16)
            .fontWeight(FontWeight.Bold)
          if (this.privacyStatus === ZoneStatus.LOADING) {
            Text('加载中…')
              .fontSize(13)
              .fontColor('#888888')
          } else if (this.privacyStatus === ZoneStatus.SUCCESS && this.privacyData !== null) {
            this.KvList([
              { key: '模型路由', value: safeStr(this.privacyData.model_route) },
              { key: '语音模式', value: safeStr(this.privacyData.voice_mode) },
              { key: '搜索模式', value: safeStr(this.privacyData.search_mode) },
              { key: '存储音频', value: this.privacyData.store_audio ? '开' : '关' },
              { key: '发送上下文到云端', value: this.privacyData.send_context_to_cloud ? '开' : '关' }
            ])
          } else if (this.privacyStatus === ZoneStatus.ERROR) {
            Text(this.privacyError)
              .fontSize(13)
              .fontColor('#c62828')
            Button('重试')
              .fontSize(13)
              .height(32)
              .onClick(() => {
                this.loadPrivacy();
              })
          }
        }
        .alignItems(HorizontalAlign.Start)
        .padding(12)
        .borderRadius(8)
        .backgroundColor('#f5f5f5')
        .width('100%')

        // ── 区块 4:服务版本 ─────────────────────────────
        Column({ space: 6 }) {
          Text('服务版本')
            .fontSize(16)
            .fontWeight(FontWeight.Bold)
          if (this.versionStatus === ZoneStatus.LOADING) {
            Text('加载中…')
              .fontSize(13)
              .fontColor('#888888')
          } else if (this.versionStatus === ZoneStatus.SUCCESS && this.versionData !== null) {
            this.KvList([
              { key: '版本', value: safeStr(this.versionData.version) },
              { key: 'Git 提交', value: safeStr(this.versionData.git_commit) },
              { key: 'Alembic current', value: safeStr(this.versionData.alembic_current) },
              { key: 'Alembic head', value: safeStr(this.versionData.alembic_head) }
            ])
          } else if (this.versionStatus === ZoneStatus.ERROR) {
            Text(this.versionError)
              .fontSize(13)
              .fontColor('#c62828')
            Button('重试')
              .fontSize(13)
              .height(32)
              .onClick(() => {
                this.loadVersion();
              })
          }
        }
        .alignItems(HorizontalAlign.Start)
        .padding(12)
        .borderRadius(8)
        .backgroundColor('#f5f5f5')
        .width('100%')

        // ── 区块 5:运维快照 ─────────────────────────────
        Column({ space: 6 }) {
          Text('运维快照')
            .fontSize(16)
            .fontWeight(FontWeight.Bold)
          if (this.opsStatus === ZoneStatus.LOADING) {
            Text('加载中…')
              .fontSize(13)
              .fontColor('#888888')
          } else if (this.opsStatus === ZoneStatus.SUCCESS && this.opsData !== null) {
            this.KvList([
              { key: '生成时间', value: safeStr(this.opsData.generated_at) },
              { key: '数据库后端', value: safeStr(this.opsData.database_backend) },
              { key: '论文总数', value: `${this.opsData.papers_total}` },
              { key: '搜索查询总数', value: `${this.opsData.search_queries_total}` },
              { key: '审计条目总数', value: `${this.opsData.audit_entries_total}` },
              { key: 'Worker 运行', value: this.opsData.worker_running ? '是' : '否' }
            ])
            Text('用户(按角色)')
              .fontSize(13)
              .fontWeight(FontWeight.Bold)
            this.KvRowsView(toKvRows(this.opsData.users_by_role))
            Text('资源(按解析状态)')
              .fontSize(13)
              .fontWeight(FontWeight.Bold)
            this.KvRowsView(toKvRows(this.opsData.resources_by_parse_status))
            Text('解析任务(按状态)')
              .fontSize(13)
              .fontWeight(FontWeight.Bold)
            this.KvRowsView(toKvRows(this.opsData.parse_jobs_by_status))
            Text('待审草稿')
              .fontSize(13)
              .fontWeight(FontWeight.Bold)
            this.KvRowsView(toKvRows(this.opsData.pending_review_drafts))
            Text('语音会话(按状态)')
              .fontSize(13)
              .fontWeight(FontWeight.Bold)
            this.KvRowsView(toKvRows(this.opsData.voice_sessions_by_status))
          } else if (this.opsStatus === ZoneStatus.ERROR) {
            Text(this.opsError)
              .fontSize(13)
              .fontColor('#c62828')
            Button('重试')
              .fontSize(13)
              .height(32)
              .onClick(() => {
                this.loadOpsSnapshot();
              })
          }
        }
        .alignItems(HorizontalAlign.Start)
        .padding(12)
        .borderRadius(8)
        .backgroundColor('#f5f5f5')
        .width('100%')

        // ── 区块 6:审计日志 ─────────────────────────────
        Column({ space: 6 }) {
          Text('审计日志(最近 100 条)')
            .fontSize(16)
            .fontWeight(FontWeight.Bold)
          if (this.auditStatus === ZoneStatus.LOADING) {
            Text('加载中…')
              .fontSize(13)
              .fontColor('#888888')
          } else if (this.auditStatus === ZoneStatus.SUCCESS) {
            if (this.auditData.length === 0) {
              Text('暂无数据')
                .fontSize(13)
                .fontColor('#888888')
            } else {
              ForEach(this.auditData, (entry: AuditEntry) => {
                Column({ space: 2 }) {
                  Text(`#${entry.id} ${safeStr(entry.action)}`)
                    .fontSize(13)
                    .fontWeight(FontWeight.Bold)
                  Text(`${safeStr(entry.actor_username)} → ${safeStr(entry.target_type)} #${safeStr(entry.target_id)}`)
                    .fontSize(12)
                    .fontColor('#666666')
                  Text(safeStr(entry.created_at))
                    .fontSize(11)
                    .fontColor('#999999')
                }
                .alignItems(HorizontalAlign.Start)
                .padding(6)
                .borderRadius(6)
                .backgroundColor('#ffffff')
                .width('100%')
              }, (entry: AuditEntry) => `${entry.id}`)
            }
          } else if (this.auditStatus === ZoneStatus.ERROR) {
            Text(this.auditError)
              .fontSize(13)
              .fontColor('#c62828')
            Button('重试')
              .fontSize(13)
              .height(32)
              .onClick(() => {
                this.loadAudit();
              })
          }
        }
        .alignItems(HorizontalAlign.Start)
        .padding(12)
        .borderRadius(8)
        .backgroundColor('#f5f5f5')
        .width('100%')
      }
      .alignItems(HorizontalAlign.Start)
      .padding(16)
    }
    .width('100%')
    .height('100%')
    .align(Alignment.TopStart)
    .scrollBar(BarState.Off)
  }

  /** key/value 行列表(固定行模型) */
  @Builder
  KvList(rows: Array<KvRow>) {
    ForEach(rows, (row: KvRow) => {
      Row({ space: 8 }) {
        Text(row.key)
          .fontSize(13)
          .fontColor('#666666')
        Text(row.value)
          .fontSize(13)
      }
      .width('100%')
    }, (row: KvRow) => `${row.key}=${row.value}`)
  }

  /** Record<string,number> 展开后的 key/value 行视图;空表显示「暂无数据」 */
  @Builder
  KvRowsView(rows: Array<KvRow>) {
    if (rows.length === 0) {
      Text('暂无数据')
        .fontSize(12)
        .fontColor('#999999')
    } else {
      ForEach(rows, (row: KvRow) => {
        Row({ space: 8 }) {
          Text(row.key)
            .fontSize(12)
            .fontColor('#666666')
          Text(row.value)
            .fontSize(12)
        }
        .width('100%')
      }, (row: KvRow) => `${row.key}=${row.value}`)
    }
  }
}
