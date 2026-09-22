/**
 * AuthPane.ets — 登录/登出面板(M14-89 第 1 切片)
 *
 * 功能(刻意收窄,挂在设置 tab 的 SettingsPane 之下):
 *   - 查询 GET /api/v1/auth/status:auth_enabled=false 显示「本地模式」,
 *     不渲染任何登录控件(既有只读面板行为完全不变);
 *   - auth_enabled=true 渲染:用户名/密码输入、登录按钮、错误状态、
 *     当前用户(username/role/created_at)、登出按钮;
 *   - 密码输入框 type Password;提交后立即清空 —— 任何状态下密码字段
 *     不保留已输入内容,不回显;
 *   - 绝不显示/回显 token;错误文案来自 AuthSession(不含凭据);
 *   - 登录成功/登出后 AuthSession 广播 EVENT_ID_SESSION_CHANGED,
 *     各只读面板自行订阅刷新(受保护 GET 由 AiosApi 自动附带 bearer);
 *   - 启动时尝试冷恢复(vault token + /me 验证),恢复结果如实展示。
 */

import { common } from '@kit.AbilityKit';
import { ApiResult, AuthStatusData, getAuthStatus } from '../AiosApi';
import { LoginOutcome, SimpleOutcome, getSessionState, login, logout, restoreSession } from './AuthSession';
import { loadBaseUrl } from './SettingsStore';

enum AuthUiStatus {
  IDLE = 0,
  LOADING = 1,
  SUCCESS = 2,
  ERROR = 3
}

@Component
export struct AuthPane {
  @State statusPhase: AuthUiStatus = AuthUiStatus.IDLE;
  @State authEnabled: boolean = false;
  @State authQueried: boolean = false;
  @State queryFailed: boolean = false;
  @State message: string = '';
  @State messageIsError: boolean = false;
  @State usernameInput: string = '';
  @State passwordInput: string = '';
  @State busy: boolean = false;
  @State loggedIn: boolean = false;
  @State loggedInUser: string = '';
  @State loggedInRole: string = '';
  @State loggedInSince: string = '';
  private hostContext: common.Context | undefined = undefined;

  async aboutToAppear(): Promise<void> {
    const ctx: common.Context | undefined =
      this.getUIContext().getHostContext() as common.Context | undefined;
    if (ctx === undefined || ctx === null) {
      this.statusPhase = AuthUiStatus.ERROR;
      this.messageIsError = true;
      this.message = '无法获取应用上下文(getHostContext 为空),登录面板暂不可用';
      return;
    }
    this.hostContext = ctx;
    await this.refreshAuthStatus();
  }

  /** 查询后端认证开关 + 尝试冷恢复会话 */
  private async refreshAuthStatus(): Promise<void> {
    this.statusPhase = AuthUiStatus.LOADING;
    this.message = '';
    this.queryFailed = false;
    const baseUrl: string = await loadBaseUrl(this.hostContext!);
    const r: ApiResult<AuthStatusData> = await getAuthStatus(baseUrl);
    this.authQueried = true;
    if (!r.ok || r.data === null) {
      this.authEnabled = false;
      this.queryFailed = true;
      this.statusPhase = AuthUiStatus.ERROR;
      this.messageIsError = true;
      this.message = '认证状态查询失败: ' + r.error;
      return;
    }
    this.authEnabled = r.data.auth_enabled;
    if (!this.authEnabled) {
      this.statusPhase = AuthUiStatus.SUCCESS;
      this.message = '';
      return;
    }
    // auth 开启:尝试从 vault 冷恢复(内部 /me 验证,失效即清)
    const restored: SimpleOutcome = await restoreSession(baseUrl);
    if (restored.restored) {
      this.applySession();
      this.statusPhase = AuthUiStatus.SUCCESS;
      this.messageIsError = false;
      this.message = '已从安全存储恢复登录';
    } else {
      this.statusPhase = AuthUiStatus.SUCCESS;
      this.messageIsError = false;
      this.message = restored.error.length > 0 ? '会话恢复失败: ' + restored.error : '';
    }
  }

  /** 把 AuthSession 状态投影到本地 UI 状态(不投影 token —— 无此字段) */
  private applySession(): void {
    const st = getSessionState();
    this.loggedIn = st.phase === 'authenticated' && st.user !== null;
    if (st.user !== null) {
      this.loggedInUser = st.user.username;
      this.loggedInRole = st.user.role;
      this.loggedInSince = st.user.created_at;
    } else {
      this.loggedInUser = '';
      this.loggedInRole = '';
      this.loggedInSince = '';
    }
  }

  private async doLogin(): Promise<void> {
    if (this.busy) {
      return;
    }
    const user: string = this.usernameInput.trim();
    const pass: string = this.passwordInput;
    // 提交即清空密码字段:任何后续状态(成功/失败)都不保留密码
    this.passwordInput = '';
    if (user.length === 0 || pass.length === 0) {
      this.statusPhase = AuthUiStatus.ERROR;
      this.messageIsError = true;
      this.message = '用户名与密码均不能为空';
      return;
    }
    this.busy = true;
    this.statusPhase = AuthUiStatus.LOADING;
    this.message = '';
    const baseUrl: string = await loadBaseUrl(this.hostContext!);
    const outcome: LoginOutcome = await login(baseUrl, user, pass);
    this.busy = false;
    this.applySession();
    if (outcome.ok) {
      this.statusPhase = AuthUiStatus.SUCCESS;
      this.messageIsError = false;
      this.message = '登录成功: ' + (outcome.user !== null ? outcome.user.username : '');
      if (outcome.error.length > 0) {
        this.message = this.message + ' (' + outcome.error + ')';
        this.messageIsError = true;
      }
    } else {
      this.statusPhase = AuthUiStatus.ERROR;
      this.messageIsError = true;
      this.message = outcome.error;
    }
  }

  private async doLogout(): Promise<void> {
    if (this.busy) {
      return;
    }
    this.busy = true;
    const r: SimpleOutcome = await logout();
    this.busy = false;
    this.applySession();
    this.usernameInput = '';
    this.passwordInput = '';
    if (r.ok) {
      this.statusPhase = AuthUiStatus.SUCCESS;
      this.messageIsError = false;
      this.message = '已登出(会话与安全存储已清除)';
    } else {
      this.statusPhase = AuthUiStatus.ERROR;
      this.messageIsError = true;
      this.message = r.error;
    }
  }

  build() {
    Column({ space: 12 }) {
      Text('认证登录')
        .fontSize(18)
        .fontWeight(FontWeight.Bold)

      if (!this.authQueried) {
        Text('正在查询认证状态…')
          .fontSize(13)
          .fontColor('#888888')
      } else if (this.queryFailed) {
        // 查询失败:如实展示错误 —— 绝不谎称「本地模式(认证未开启)」
        // (M14-89 诚实边界:查不到 auth/status ≠ auth 关闭)
        Text(this.message)
          .fontSize(13)
          .fontColor('#c62828')
        Button('重试')
          .fontSize(13)
          .height(32)
          .onClick(() => {
            this.refreshAuthStatus();
          })
      } else if (!this.authEnabled) {
        Text('本地模式(认证未开启)——只读面板无需登录,行为保持不变。')
          .fontSize(13)
          .fontColor('#666666')
      } else {
        if (this.loggedIn) {
          Column({ space: 6 }) {
            Text('当前登录用户')
              .fontSize(14)
              .fontWeight(FontWeight.Bold)
            Text('用户名: ' + this.loggedInUser)
              .fontSize(13)
            Text('角色: ' + this.loggedInRole)
              .fontSize(13)
            Text('创建时间: ' + this.loggedInSince)
              .fontSize(12)
              .fontColor('#666666')
            Button(this.busy ? '登出中…' : '登出')
              .enabled(!this.busy)
              .onClick(() => {
                this.doLogout();
              })
          }
          .alignItems(HorizontalAlign.Start)
          .padding(12)
          .borderRadius(8)
          .backgroundColor('#e8f5e9')
          .width('100%')
        } else {
          Text('用户名')
            .fontSize(13)
          TextInput({ text: this.usernameInput, placeholder: '用户名(3-32 位字母数字下划线)' })
            .onChange((value: string) => {
              this.usernameInput = value;
            })
            .width('100%')

          Text('密码')
            .fontSize(13)
          TextInput({ text: this.passwordInput, placeholder: '密码(至少 8 位)' })
            .type(InputType.Password)
            .onChange((value: string) => {
              this.passwordInput = value;
            })
            .width('100%')

          Button(this.busy ? '登录中…' : '登录')
            .enabled(!this.busy)
            .onClick(() => {
              this.doLogin();
            })
        }

        if (this.statusPhase === AuthUiStatus.LOADING) {
          if (!this.loggedIn) {
            Text('正在登录…')
              .fontSize(13)
              .fontColor('#888888')
          }
        } else if (this.message.length > 0) {
          Text(this.message)
            .fontSize(13)
            .fontColor(this.messageIsError ? '#c62828' : '#2e7d32')
          if (this.statusPhase === AuthUiStatus.ERROR) {
            Button('重试')
              .fontSize(13)
              .height(32)
              .onClick(() => {
                this.refreshAuthStatus();
              })
          }
        }
      }

      Text('密码仅本次登录使用,不保存在设备上;令牌存于系统安全存储(Asset Store Kit)。')
        .fontSize(11)
        .fontColor('#999999')
    }
    .alignItems(HorizontalAlign.Start)
    .padding(16)
    .width('100%')
  }
}
