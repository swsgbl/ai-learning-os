/**
 * SettingsPane.ets — 设置面板组件(M13-01 第 3b-1 小步)
 *
 * 功能(刻意收窄):
 *   - TextInput 编辑 AIOS 服务基地址;aboutToAppear 经 getUIContext().getHostContext()
 *     取上下文,回填已保存地址(缺省 DEFAULT_API_BASE_URL);
 *   - 「保存」-> SettingsStore.saveBaseUrl(内部先白名单校验,结果如实展示);
 *   - 「测试连接」-> 先 validateBaseUrl,再 getHealth 只读 GET /health;
 *   - 状态机 idle/loading/success/error,error 态提供「重试」按钮;
 *   - 错误文案一律如实透传(校验文案 / HTTP 状态 / 网络层错误),不虚构成功;
 *   - 全程不涉及 token/password/key,不调用任何写 API。
 */

import { common } from '@kit.AbilityKit';
import { ApiResult, HealthData, getHealth } from '../AiosApi';
import { UrlPolicyCode, validateBaseUrl, urlPolicyMessage } from '../UrlPolicy';
import { DEFAULT_API_BASE_URL, SaveResult, loadBaseUrl, saveBaseUrl } from './SettingsStore';

/** 连接测试状态机 */
enum TestStatus {
  IDLE = 0,
  LOADING = 1,
  SUCCESS = 2,
  ERROR = 3
}

@Component
export struct SettingsPane {
  @State urlInput: string = DEFAULT_API_BASE_URL;
  @State testStatus: TestStatus = TestStatus.IDLE;
  @State message: string = '';
  @State messageIsError: boolean = false;
  @State saving: boolean = false;
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

  /** 保存当前输入(先校验后写盘,结果如实展示) */
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

  /** 连接测试:先 URL 白名单校验,再只读 GET /health;任何失败如实展示 */
  private async doTest(): Promise<void> {
    const check = validateBaseUrl(this.urlInput);
    if (check.code !== UrlPolicyCode.OK) {
      this.testStatus = TestStatus.ERROR;
      this.messageIsError = true;
      this.message = urlPolicyMessage(check);
      return;
    }
    if (this.hostContext === undefined) {
      this.testStatus = TestStatus.ERROR;
      this.messageIsError = true;
      this.message = '应用上下文不可用,无法测试连接';
      return;
    }
    this.testStatus = TestStatus.LOADING;
    this.message = '';
    const r: ApiResult<HealthData> = await getHealth(check.url);
    if (r.ok && r.data !== null && r.data.status === 'ok') {
      this.testStatus = TestStatus.SUCCESS;
      this.messageIsError = false;
      this.message = `连接成功: ${r.data.service}`;
      return;
    }
    this.testStatus = TestStatus.ERROR;
    this.messageIsError = true;
    if (r.ok && r.data !== null) {
      // HTTP 2xx 但业务状态不是 "ok":如实展示,不虚构成功
      this.message = `服务响应异常: status="${r.data.status}" service="${r.data.service}"`;
    } else {
      this.message = r.error;
    }
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
          .enabled(!this.saving && this.testStatus !== TestStatus.LOADING)
          .onClick(() => {
            this.doSave();
          })

        Button(this.testStatus === TestStatus.LOADING ? '测试中…' : '测试连接')
          .enabled(!this.saving && this.testStatus !== TestStatus.LOADING)
          .onClick(() => {
            this.doTest();
          })
      }

      if (this.testStatus === TestStatus.LOADING) {
        Text('正在连接服务…')
          .fontSize(13)
          .fontColor('#888888')
      } else if (this.message.length > 0) {
        Text(this.message)
          .fontSize(13)
          .fontColor(this.messageIsError ? '#c62828' : '#2e7d32')

        if (this.testStatus === TestStatus.ERROR) {
          Button('重试')
            .fontSize(13)
            .height(32)
            .onClick(() => {
              this.doTest();
            })
        }
      }
    }
    .alignItems(HorizontalAlign.Start)
    .padding(16)
    .width('100%')
  }
}
