/**
 * GovernancePane.ets — 治理只读面板(M13-11)
 *
 * 功能(刻意收窄):
 *   - 三个独立只读区块:服务版本(GET /api/v1/version)/ 运维快照
 *     (GET /api/v1/system/ops-snapshot)/ 审计日志(GET /api/v1/audit?limit=100),
 *     复用既有 AiosApi 封装 getVersion / getOpsSnapshot / getAudit,不新增后端契约;
 *   - 每区独立状态机(LOADING / EMPTY / SUCCESS / ERROR)+ 独立刷新/重试按钮,
 *     三区互不影响,顶部「整体刷新」一次刷新三区;
 *   - 成功先做契约形状校验(标量字段类型 / 计数表逐值 number / 审计逐条逐字段),
 *     不合格一律失败关闭(fail closed)并给出中文错误,绝不猜测渲染;
 *   - 使用 disposed 标志防止组件卸载后状态写入;
 *   - 每区独立请求代次(versionGeneration / opsGeneration / auditGeneration)
 *     防止竞态(stale-response guard):过期响应不得覆盖较新区块状态;
 *   - URL 来自 SettingsStore.loadBaseUrl,监听 EVENT_ID_URL_CHANGED 自动刷新:
 *     回调先 off 再 on 防重复注册;收到事件后重新读取持久化 URL(不信任 payload),
 *     更新服务地址显示并刷新全部治理区块;aboutToDisappear 先设 disposed=true
 *     再精确退订(带 callback 的 off 重载,不影响其他订阅者);
 *   - 空态如实展示:审计列表为空显示「暂无审计条目」,计数表为空显示「暂无数据」;
 *   - 只读边界:仅 3 个只读 GET,无任何 POST/PUT/PATCH/DELETE,不保存任何
 *     token/key/password,不访问生产端点,无签名材料,无任何运行时写操作;
 *   - 审计条目只渲染 id/action/actor_username/target_type/target_id/created_at
 *     —— 服务端虽会下发 before/after 载荷与 request body,UI 契约不投影、
 *     不渲染、不校验(AuditEntry 接口本就不含这些字段);
 *   - Record<string,number> 计数表经 Object.keys 展开为 key/value 行;
 *   - 多行文本设 maxLines 上限 + Ellipsis,窄屏不溢出;
 *   - 布局(M13-14 结构可达性修正):固定外层头部区域(标题「治理(只读)」/
 *     「整体刷新」按钮/服务地址/只读边界说明/上下文错误)始终位于 Scroll 之外,
 *     与滚动偏移无关地参与布局并暴露于无障碍树;Scroll 内只保留三个治理区块
 *     (服务版本 / 运维快照 / 审计日志)。运行期证据显示合成滑动、长按拖动、
 *     已选 tab 点击均无法复位 Scroll 偏移(见 evidence README),因此顶层控件
 *     必须结构性可达,而非依赖手势回滚。状态机、请求代次、disposed 守卫、
 *     API、文案、错误处理与 loading-disabled 语义全部保持不变。
 */

import { common } from '@kit.AbilityKit';
import { emitter } from '@kit.BasicServicesKit';
import {
  ApiResult,
  AuditEntry,
  OpsSnapshotData,
  VersionData,
  getAudit,
  getOpsSnapshot,
  getVersion
} from '../AiosApi';
import {
  DEFAULT_API_BASE_URL,
  EVENT_ID_URL_CHANGED,
  loadBaseUrl
} from './SettingsStore';

/** 区块状态机(三区共用同一枚举,各自持有一份状态) */
enum SectionStatus {
  LOADING = 'LOADING',
  EMPTY = 'EMPTY',
  SUCCESS = 'SUCCESS',
  ERROR = 'ERROR'
}

/** key/value 行(Record<string,number> 计数表展开后的行模型) */
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

/** 安全字符串化(缺字段/空串显示 '—') */
function safeStr(v: string | null | undefined): string {
  if (v === null || v === undefined || `${v}`.length === 0) {
    return '—';
  }
  return `${v}`;
}

/**
 * 计数表形状校验:必须是对象(非数组非 null)且每个取值为 number。
 * 返回 '' 表示通过;否则返回中文错误信息(不入库、fail closed)。
 */
function validateCountMap(owner: string, map: Object | undefined): string {
  if (typeof map !== 'object' || map === null || Array.isArray(map)) {
    return `响应格式错误: ${owner} 必须为计数表对象`;
  }
  const rec: Record<string, Object> = map as Record<string, Object>;
  const keys: Array<string> = Object.keys(rec);
  for (let i = 0; i < keys.length; i++) {
    if (typeof rec[keys[i]] !== 'number') {
      return `响应格式错误: ${owner}.${keys[i]} 必须为数字`;
    }
  }
  return '';
}

@Component
export struct GovernancePane {
  @State baseUrl: string = DEFAULT_API_BASE_URL;
  @State ctxError: string = '';

  // ── 区块 1:服务版本 ─────────────────────────────────
  @State versionStatus: SectionStatus = SectionStatus.LOADING;
  @State versionData: VersionData | null = null;
  @State versionError: string = '';

  // ── 区块 2:运维快照 ─────────────────────────────────
  @State opsStatus: SectionStatus = SectionStatus.LOADING;
  @State opsData: OpsSnapshotData | null = null;
  @State opsError: string = '';

  // ── 区块 3:审计日志 ─────────────────────────────────
  @State auditStatus: SectionStatus = SectionStatus.LOADING;
  @State auditData: Array<AuditEntry> = [];
  @State auditError: string = '';

  private hostContext: common.Context | undefined = undefined;
  private disposed: boolean = false;
  /** 各区块独立请求代次:过期响应不得覆盖较新区块状态 */
  private versionGeneration: number = 0;
  private opsGeneration: number = 0;
  private auditGeneration: number = 0;
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
      this.refreshAll();
    }).catch((e: Error) => {
      if (this.disposed) {
        return;
      }
      // 读取配置失败:三区都如实进入错误态,绝不沿用旧数据冒充新地址结果
      this.versionData = null;
      this.versionError = `读取配置失败: ${e}`;
      this.versionStatus = SectionStatus.ERROR;
      this.opsData = null;
      this.opsError = `读取配置失败: ${e}`;
      this.opsStatus = SectionStatus.ERROR;
      this.auditData = [];
      this.auditError = `读取配置失败: ${e}`;
      this.auditStatus = SectionStatus.ERROR;
    });
  };

  async aboutToAppear(): Promise<void> {
    const ctx: common.Context | undefined =
      this.getUIContext().getHostContext() as common.Context | undefined;
    if (ctx === undefined || ctx === null) {
      this.ctxError = '无法获取应用上下文(getHostContext 为空),面板暂不可用';
      this.versionError = this.ctxError;
      this.versionStatus = SectionStatus.ERROR;
      this.opsError = this.ctxError;
      this.opsStatus = SectionStatus.ERROR;
      this.auditError = this.ctxError;
      this.auditStatus = SectionStatus.ERROR;
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
    this.refreshAll();
  }

  aboutToDisappear(): void {
    // 先标记销毁,再取消订阅,阻止后续回调和异步响应写入状态
    this.disposed = true;
    emitter.off(EVENT_ID_URL_CHANGED, this.urlChangeListener);
  }

  /** 整体刷新:三区并行发起,各自独立推进状态机 */
  refreshAll(): void {
    this.loadVersion();
    this.loadOpsSnapshot();
    this.loadAudit();
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

  /**
   * 运维快照响应形状校验(失败关闭):
   *   - generated_at/database_backend 为字符串;
   *   - papers_total/search_queries_total/audit_entries_total 为数字;
   *   - worker_running 为布尔;
   *   - 五张计数表逐值校验为数字(见 validateCountMap)。
   */
  private validateOpsShape(data: OpsSnapshotData): string {
    if (typeof data.generated_at !== 'string') {
      return '响应格式错误: generated_at 必须为字符串';
    }
    if (typeof data.database_backend !== 'string') {
      return '响应格式错误: database_backend 必须为字符串';
    }
    if (typeof data.papers_total !== 'number') {
      return '响应格式错误: papers_total 必须为数字';
    }
    if (typeof data.search_queries_total !== 'number') {
      return '响应格式错误: search_queries_total 必须为数字';
    }
    if (typeof data.audit_entries_total !== 'number') {
      return '响应格式错误: audit_entries_total 必须为数字';
    }
    if (typeof data.worker_running !== 'boolean') {
      return '响应格式错误: worker_running 必须为布尔';
    }
    const mapChecks: Array<string> = [
      validateCountMap('users_by_role', data.users_by_role),
      validateCountMap('resources_by_parse_status', data.resources_by_parse_status),
      validateCountMap('parse_jobs_by_status', data.parse_jobs_by_status),
      validateCountMap('pending_review_drafts', data.pending_review_drafts),
      validateCountMap('voice_sessions_by_status', data.voice_sessions_by_status)
    ];
    for (let i = 0; i < mapChecks.length; i++) {
      if (mapChecks[i] !== '') {
        return mapChecks[i];
      }
    }
    return '';
  }

  /**
   * 审计日志响应形状校验(失败关闭):
   *   - 顶层数组,逐条:id 为数字,action/target_type/target_id/created_at 为字符串,
   *     actor_username 为字符串或 null;
   *   - 刻意不校验也不读取 before/after 载荷:服务端会下发,但 UI 契约不投影。
   */
  private validateAuditShape(data: Array<AuditEntry>): string {
    if (!Array.isArray(data)) {
      return '响应格式错误: 审计日志必须为数组';
    }
    for (let i = 0; i < data.length; i++) {
      const entry: Object = data[i] as Object;
      if (typeof entry !== 'object' || Array.isArray(entry)) {
        return `响应格式错误: 审计条目 ${i} 必须为对象`;
      }
      const rec: Record<string, Object> = entry as Record<string, Object>;
      if (typeof rec['id'] !== 'number') {
        return `响应格式错误: 审计条目 ${i}.id 必须为数字`;
      }
      if (typeof rec['action'] !== 'string') {
        return `响应格式错误: 审计条目 ${i}.action 必须为字符串`;
      }
      const actor: Object = rec['actor_username'];
      if (actor !== null && typeof actor !== 'string') {
        return `响应格式错误: 审计条目 ${i}.actor_username 必须为字符串或 null`;
      }
      if (typeof rec['target_type'] !== 'string') {
        return `响应格式错误: 审计条目 ${i}.target_type 必须为字符串`;
      }
      if (typeof rec['target_id'] !== 'string') {
        return `响应格式错误: 审计条目 ${i}.target_id 必须为字符串`;
      }
      if (typeof rec['created_at'] !== 'string') {
        return `响应格式错误: 审计条目 ${i}.created_at 必须为字符串`;
      }
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

  private async loadOpsSnapshot(): Promise<void> {
    if (this.disposed) {
      return;
    }
    const generation: number = ++this.opsGeneration;
    this.opsStatus = SectionStatus.LOADING;
    this.opsError = '';
    const r: ApiResult<OpsSnapshotData> = await getOpsSnapshot(this.baseUrl);
    if (this.disposed || generation !== this.opsGeneration) {
      return;
    }
    if (!r.ok || r.data === null) {
      this.opsData = null;
      this.opsError = r.error;
      this.opsStatus = SectionStatus.ERROR;
      return;
    }
    const invalidReason: string = this.validateOpsShape(r.data);
    if (invalidReason !== '') {
      this.opsData = null;
      this.opsError = invalidReason;
      this.opsStatus = SectionStatus.ERROR;
      return;
    }
    this.opsData = r.data;
    this.opsStatus = SectionStatus.SUCCESS;
  }

  private async loadAudit(): Promise<void> {
    if (this.disposed) {
      return;
    }
    const generation: number = ++this.auditGeneration;
    this.auditStatus = SectionStatus.LOADING;
    this.auditError = '';
    const r: ApiResult<Array<AuditEntry>> = await getAudit(this.baseUrl);
    if (this.disposed || generation !== this.auditGeneration) {
      return;
    }
    if (!r.ok || r.data === null) {
      this.auditData = [];
      this.auditError = r.error;
      this.auditStatus = SectionStatus.ERROR;
      return;
    }
    const invalidReason: string = this.validateAuditShape(r.data);
    if (invalidReason !== '') {
      this.auditData = [];
      this.auditError = invalidReason;
      this.auditStatus = SectionStatus.ERROR;
      return;
    }
    // 空数组是合法的诚实空态,由 EMPTY 状态如实展示,不虚构数据
    this.auditData = r.data;
    this.auditStatus =
      r.data.length === 0 ? SectionStatus.EMPTY : SectionStatus.SUCCESS;
  }

  build() {
    // 外层固定布局:头部区域(标题/整体刷新/服务地址/只读说明/上下文错误)
    // 位于 Scroll 之外,不参与滚动 —— 与滚动偏移无关地布局并暴露于无障碍树
    Column() {
      // ── 固定头部 ─────────────────────────────────────
      Column({ space: 12 }) {
        Row({ space: 12 }) {
          Text('治理(只读)')
            .fontSize(20)
            .fontWeight(FontWeight.Bold)
          Button('整体刷新')
            .fontSize(12)
            .height(28)
            .enabled(this.versionStatus !== SectionStatus.LOADING &&
                     this.opsStatus !== SectionStatus.LOADING &&
                     this.auditStatus !== SectionStatus.LOADING)
            .onClick(() => {
              this.refreshAll();
            })
        }
        .width('100%')

        Text(`服务地址: ${this.baseUrl}`)
          .fontSize(12)
          .fontColor('#666666')
          .width('100%')
          .maxLines(2)
          .textOverflow({ overflow: TextOverflow.Ellipsis })

        // 只读边界说明(始终展示,不因加载状态而消失)
        Text('只读视图:版本 / 运维快照 / 审计日志,无任何写操作,不展示载荷内容')
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
      .padding(16)
      .border({ width: { bottom: 1 }, color: '#eeeeee' })

      // ── 可滚动内容:仅三个治理区块 ──────────────────
      // Scroll 独占剩余空间并裁剪自身内容,固定头部不被其包含、不随偏移滚动
      Scroll() {
        Column({ space: 12 }) {
          // ── 区块 1:服务版本 ─────────────────────────
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

          // ── 区块 2:运维快照 ─────────────────────────
          Column({ space: 8 }) {
            Row({ space: 8 }) {
              Text('运维快照')
                .fontSize(15)
                .fontWeight(FontWeight.Bold)
                .layoutWeight(1)
                .maxLines(1)
                .textOverflow({ overflow: TextOverflow.Ellipsis })
              Button('刷新')
                .fontSize(11)
                .height(26)
                .enabled(this.opsStatus !== SectionStatus.LOADING)
                .onClick(() => {
                  this.loadOpsSnapshot();
                })
            }
            .width('100%')

            if (this.opsStatus === SectionStatus.LOADING) {
              Text('加载中…')
                .fontSize(13)
                .fontColor('#888888')
            } else if (this.opsStatus === SectionStatus.SUCCESS && this.opsData !== null) {
              this.KvList([
                { key: '生成时间', value: safeStr(this.opsData.generated_at) },
                { key: '数据库后端', value: safeStr(this.opsData.database_backend) },
                { key: '论文总数', value: `${this.opsData.papers_total}` },
                { key: '搜索查询总数', value: `${this.opsData.search_queries_total}` },
                { key: '审计条目总数', value: `${this.opsData.audit_entries_total}` },
                { key: 'Worker 运行', value: this.opsData.worker_running ? '是' : '否' }
              ])
              this.CountMapBlock('用户(按角色)', toKvRows(this.opsData.users_by_role))
              this.CountMapBlock('资源(按解析状态)', toKvRows(this.opsData.resources_by_parse_status))
              this.CountMapBlock('解析任务(按状态)', toKvRows(this.opsData.parse_jobs_by_status))
              this.CountMapBlock('待审草稿', toKvRows(this.opsData.pending_review_drafts))
              this.CountMapBlock('语音会话(按状态)', toKvRows(this.opsData.voice_sessions_by_status))
            } else if (this.opsStatus === SectionStatus.ERROR) {
              Text(this.opsError)
                .fontSize(13)
                .fontColor('#c62828')
                .width('100%')
                .maxLines(3)
                .textOverflow({ overflow: TextOverflow.Ellipsis })
              Button('重试')
                .fontSize(12)
                .height(30)
                .onClick(() => {
                  this.loadOpsSnapshot();
                })
            }
          }
          .width('100%')
          .alignItems(HorizontalAlign.Start)
          .padding(12)
          .backgroundColor('#fafafa')
          .borderRadius(8)
          .border({ width: { bottom: 1 }, color: '#eeeeee' })

          // ── 区块 3:审计日志 ─────────────────────────
          Column({ space: 8 }) {
            Row({ space: 8 }) {
              Text('审计日志(最近 100 条)')
                .fontSize(15)
                .fontWeight(FontWeight.Bold)
                .layoutWeight(1)
                .maxLines(1)
                .textOverflow({ overflow: TextOverflow.Ellipsis })
              Button('刷新')
                .fontSize(11)
                .height(26)
                .enabled(this.auditStatus !== SectionStatus.LOADING)
                .onClick(() => {
                  this.loadAudit();
                })
            }
            .width('100%')

            if (this.auditStatus === SectionStatus.LOADING) {
              Text('加载中…')
                .fontSize(13)
                .fontColor('#888888')
            } else if (this.auditStatus === SectionStatus.EMPTY) {
              // 诚实空态:服务明确返回空列表,不虚构任何条目
              Text('暂无审计条目')
                .fontSize(13)
                .fontColor('#888888')
            } else if (this.auditStatus === SectionStatus.SUCCESS) {
              // 只渲染 id/action/actor_username/target_type/target_id/created_at,
              // 绝不渲染 before/after 载荷或请求体
              ForEach(this.auditData, (entry: AuditEntry) => {
                Column({ space: 2 }) {
                  Text(`#${entry.id} ${safeStr(entry.action)}`)
                    .fontSize(13)
                    .fontWeight(FontWeight.Bold)
                    .maxLines(2)
                    .textOverflow({ overflow: TextOverflow.Ellipsis })
                    .width('100%')
                  Text(`${safeStr(entry.actor_username)} → ${safeStr(entry.target_type)} #${safeStr(entry.target_id)}`)
                    .fontSize(12)
                    .fontColor('#666666')
                    .maxLines(2)
                    .textOverflow({ overflow: TextOverflow.Ellipsis })
                    .width('100%')
                  Text(safeStr(entry.created_at))
                    .fontSize(11)
                    .fontColor('#999999')
                    .maxLines(1)
                    .textOverflow({ overflow: TextOverflow.Ellipsis })
                    .width('100%')
                }
                .alignItems(HorizontalAlign.Start)
                .padding(6)
                .borderRadius(6)
                .backgroundColor('#ffffff')
                .width('100%')
              }, (entry: AuditEntry) => `${entry.id}`)
            } else if (this.auditStatus === SectionStatus.ERROR) {
              Text(this.auditError)
                .fontSize(13)
                .fontColor('#c62828')
                .width('100%')
                .maxLines(3)
                .textOverflow({ overflow: TextOverflow.Ellipsis })
              Button('重试')
                .fontSize(12)
                .height(30)
                .onClick(() => {
                  this.loadAudit();
                })
            }
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
        .padding(16)
      }
      .layoutWeight(1)
      .align(Alignment.TopStart)
      .scrollBar(BarState.Off)
    }
    .width('100%')
    .height('100%')
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
          .layoutWeight(1)
          .maxLines(2)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
      }
      .width('100%')
    }, (row: KvRow) => `${row.key}=${row.value}`)
  }

  /** 计数表区块:标题 + key/value 行;空表如实显示「暂无数据」 */
  @Builder
  CountMapBlock(title: string, rows: Array<KvRow>) {
    Column({ space: 4 }) {
      Text(title)
        .fontSize(13)
        .fontWeight(FontWeight.Bold)
        .maxLines(1)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
        .width('100%')
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
              .maxLines(1)
              .textOverflow({ overflow: TextOverflow.Ellipsis })
              .layoutWeight(1)
            Text(row.value)
              .fontSize(12)
          }
          .width('100%')
        }, (row: KvRow) => `${row.key}=${row.value}`)
      }
    }
    .width('100%')
    .alignItems(HorizontalAlign.Start)
    .padding({ top: 4 })
  }
}
