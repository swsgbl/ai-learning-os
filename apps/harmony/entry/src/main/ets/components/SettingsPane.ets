/**
 * SettingsPane.ets — 设置面板组件(M13-01 第 3b-1 小步; M14-230H 连接诊断)
 *
 * 功能(刻意收窄):
 *   - TextInput 编辑 AIOS 服务基地址;aboutToAppear 经 getUIContext().getHostContext()
 *     取上下文,回填已保存地址(缺省 DEFAULT_API_BASE_URL);
 *   - 「保存」-> SettingsStore.saveBaseUrl(内部先白名单校验,结果如实展示);
 *   - 「测试连接」(M14-230H):唯一的诊断触发入口,绝不随挂载/保存/改 URL 自动运行;
 *     每次测试恰好调用一次 getHealth + 一次 getAuthStatus(各受既有 10s 超时约束,
 *     无重试、无轮询),结果映射到四值闭集(ok / http_path_mismatch /
 *     network_unreachable / mixed)并以固定中文文案展示;
 *   - 运行期间两个按钮均禁用,结束后恢复;诊断不读取/写入/保存基地址;
 *   - 展示边界:诊断文案只允许「闭集中文标签 + 数字 HTTP 状态码」,
 *     绝不展示原始响应体、异常对象、令牌、堆栈或内部路径。
 */

import { common } from '@kit.AbilityKit';
import { ApiResult, AuthStatusData, HealthData, getAuthStatus, getHealth } from '../AiosApi';
import { UrlPolicyCode, validateBaseUrl } from '../UrlPolicy';
import { DEFAULT_API_BASE_URL, SaveResult, loadBaseUrl, saveBaseUrl } from './SettingsStore';

/** 连接诊断结果闭集(M14-230H;四值,严禁扩展携带载荷) */
enum DiagOutcome {
  OK = 'ok',
  HTTP_PATH_MISMATCH = 'http_path_mismatch',
  NETWORK_UNREACHABLE = 'network_unreachable',
  MIXED = 'mixed'
}

/** 闭集中文文案(唯一展示来源;仅允许再附加数字 HTTP 状态码) */
const DIAG_OK_TEXT: string = '连接正常:服务健康与认证状态接口均可用';
const DIAG_MISMATCH_TEXT: string = '服务可达但接口路径不匹配';
const DIAG_UNREACHABLE_TEXT: string = '网络不可达或地址校验失败';
const DIAG_MIXED_TEXT: string = '两个接口结果不一致(一个成功一个失败)';

/** 连接测试状态机(仅用于文案着色;运行态由 testing 布尔权威表达) */
enum TestStatus {
  IDLE = 0,
  SUCCESS = 1,
  ERROR = 2
}

/** 单端点判决闭集:good(2xx+形状合法)/ http_error(非 2xx 应答)/ no_conn(传输或校验失败) */
type EndpointVerdict = 'good' | 'http_error' | 'no_conn';

/**
 * 单端点判决(纯函数):
 *   - good:!ok 不可能为 good;要求 ok=true 且响应形状合法;
 *   - http_error:拿到真实非 2xx HTTP 应答(服务在应答,路径/网关不对);
 *   - no_conn:URL 校验失败 / 传输层失败(status=-1),或 2xx 但响应体
 *     无法解析/形状非法(归为校验失败)。
 */
function classifyEndpoint(ok: boolean, status: number, shapeValid: boolean): EndpointVerdict {
  if (ok && shapeValid) {
    return 'good';
  }
  if (!ok && status >= 100 && !(status >= 200 && status < 300)) {
    return 'http_error';
  }
  return 'no_conn';
}

/** /health 响应形状校验:status 必须是 string */
function healthShapeValid(d: HealthData | null): boolean {
  return d !== null && typeof d.status === 'string';
}

/** /api/v1/auth/status 响应形状校验:auth_enabled 必须是 boolean */
function authShapeValid(d: AuthStatusData | null): boolean {
  return d !== null && typeof d.auth_enabled === 'boolean';
}

/** 两个端点判决 -> 四值闭集(M14-230H 映射表) */
function combineVerdicts(h: EndpointVerdict, a: EndpointVerdict): DiagOutcome {
  if (h === 'good' && a === 'good') {
    return DiagOutcome.OK;
  }
  if (h === 'http_error' && a === 'http_error') {
    return DiagOutcome.HTTP_PATH_MISMATCH;
  }
  if (h === 'no_conn' && a === 'no_conn') {
    return DiagOutcome.NETWORK_UNREACHABLE;
  }
  return DiagOutcome.MIXED;
}

/** 闭集 -> 中文文案(不携带任何端点数据) */
function diagLabel(outcome: DiagOutcome): string {
  if (outcome === DiagOutcome.OK) {
    return DIAG_OK_TEXT;
  }
  if (outcome === DiagOutcome.HTTP_PATH_MISMATCH) {
    return DIAG_MISMATCH_TEXT;
  }
  if (outcome === DiagOutcome.NETWORK_UNREACHABLE) {
    return DIAG_UNREACHABLE_TEXT;
  }
  return DIAG_MIXED_TEXT;
}

@Component
export struct SettingsPane {
  @State urlInput: string = DEFAULT_API_BASE_URL;
  @State testStatus: TestStatus = TestStatus.IDLE;
  @State message: string = '';
  @State messageIsError: boolean = false;
  @State saving: boolean = false;
  /** 连接诊断运行态:运行中两个按钮全部禁用,结束后恢复 */
  @State testing: boolean = false;
  private hostContext: common.Context | undefined = undefined;

  async aboutToAppear(): Promise<void> {
    const ctx: common.Context | undefined =
      this.getUIContext().getHostContext() as common.Context | undefined;
    if (ctx === undefined || ctx === null) {
      this.testStatus = TestStatus.ERROR;
      this.messageIsError = true;
      this.message = '无法获取应用上下文(getHostContext 为空),设置暂不可用';
      return;
    }
    this.hostContext = ctx;
    this.urlInput = await loadBaseUrl(this.hostContext);
  }

  /** 保存当前输入(先校验后写盘,结果如实展示);不触发连接诊断 */
  private async doSave(): Promise<void> {
    if (this.hostContext === undefined) {
      this.testStatus = TestStatus.ERROR;
      this.messageIsError = true;
      this.message = '应用上下文不可用,无法保存';
      return;
    }
    this.saving = true;
    const r: SaveResult = await saveBaseUrl(this.hostContext, this.urlInput);
    this.saving = false;
    if (r.ok) {
      this.testStatus = TestStatus.IDLE;
      this.messageIsError = false;
      this.message = `已保存: ${r.url}`;
    } else {
      this.testStatus = TestStatus.ERROR;
      this.messageIsError = true;
      this.message = r.error;
    }
  }

  /**
   * 连接诊断(M14-230H):仅由「测试连接」按钮显式触发。
   *   - 每次测试恰好一次 getHealth + 一次 getAuthStatus,无重试/轮询;
   *   - 结果映射四值闭集,文案仅「闭集标签 + 数字 HTTP 状态码」;
   *   - 不读取/写入/保存基地址;不展示响应体/异常对象/令牌/堆栈/内部路径;
   *   - URL 白名单校验失败按闭集归入 network_unreachable(校验失败)。
   */
  private async doTest(): Promise<void> {
    if (this.testing) {
      return;
    }
    const check = validateBaseUrl(this.urlInput);
    if (check.code !== UrlPolicyCode.OK) {
      this.testStatus = TestStatus.ERROR;
      this.messageIsError = true;
      this.message = DIAG_UNREACHABLE_TEXT;
      return;
    }
    this.testing = true;
    this.testStatus = TestStatus.IDLE;
    this.message = '';
    const health: ApiResult<HealthData> = await getHealth(check.url);
    const auth: ApiResult<AuthStatusData> = await getAuthStatus(check.url);
    this.testing = false;
    const hVerdict: EndpointVerdict =
      classifyEndpoint(health.ok, health.status, healthShapeValid(health.data));
    const aVerdict: EndpointVerdict =
      classifyEndpoint(auth.ok, auth.status, authShapeValid(auth.data));
    const outcome: DiagOutcome = combineVerdicts(hVerdict, aVerdict);
    let text: string = diagLabel(outcome);
    if (outcome === DiagOutcome.HTTP_PATH_MISMATCH) {
      const code: number = health.status >= 100 ? health.status : auth.status;
      if (code >= 100) {
        text = `${text} (HTTP ${code})`;
      }
    }
    this.testStatus = outcome === DiagOutcome.OK ? TestStatus.SUCCESS : TestStatus.ERROR;
    this.messageIsError = outcome !== DiagOutcome.OK;
    this.message = text;
  }

  build() {
    Column({ space: 12 }) {
      Text('AIOS 服务地址')
        .fontSize(18)
        .fontWeight(FontWeight.Bold)

      Text('仅保存服务基地址(URL);不保存任何账号、令牌或密码。')
        .fontSize(12)
        .fontColor('#666666')

      TextInput({ text: this.urlInput, placeholder: DEFAULT_API_BASE_URL })
        .onChange((value: string) => {
          this.urlInput = value;
        })
        .width('100%')

      Row({ space: 12 }) {
        Button(this.saving ? '保存中…' : '保存')
          .enabled(!this.saving && !this.testing)
          .onClick(() => {
            this.doSave();
          })

        Button(this.testing ? '测试中…' : '测试连接')
          .enabled(!this.saving && !this.testing)
          .onClick(() => {
            this.doTest();
          })
      }

      if (this.testing) {
        Text('正在连接服务…')
          .fontSize(13)
          .fontColor('#888888')
      } else if (this.message.length > 0) {
        Text(this.message)
          .fontSize(13)
          .fontColor(this.messageIsError ? '#c62828' : '#2e7d32')
      }
    }
    .alignItems(HorizontalAlign.Start)
    .padding(16)
    .width('100%')
  }
}
