/**
 * WelcomeCard.ets — 首次使用引导卡(M15-004)
 *
 * 场景:首次打开(从未保存过服务器地址)或健康检查失败且从未配置地址时,
 * 在首页顶部渲染一张引导卡,代替「满屏 404 错误」的裸奔体验:
 *   - 欢迎语 + 一句话产品价值(定制课程/练习/报告);
 *   - 内联服务器地址输入 + 保存(复用 SettingsStore 校验/持久化/事件广播,
 *     零重复校验逻辑;保存成功后 HomePane 经 URL_CHANGED 事件自动刷新);
 *   - 下一步提示:连上后到「设置」页注册/登录,再到 Web 端定制课程。
 *
 * 边界:只做引导,不做连接测试轮询(保存后整体刷新即为测试);
 * 不重复 SettingsPane 的完整表单(那是显式设置入口,这里是首启引导)。
 */
import { common } from '@kit.AbilityKit';
import { saveBaseUrl } from './SettingsStore';

@Component
export struct WelcomeCard {
  /** 是否显示(由 HomePane 判定:未保存地址 或 健康 ERROR 且未保存) */
  show: boolean = false;
  onSaved: () => void = () => {
  };
  @State urlInput: string = '';
  @State busy: boolean = false;
  @State message: string = '';
  @State messageIsError: boolean = false;
  private hostContext: common.Context | undefined = undefined;

  aboutToAppear(): void {
    const ctx: common.Context | undefined =
      this.getUIContext().getHostContext() as common.Context | undefined;
    this.hostContext = ctx;
  }

  private async doSave(): Promise<void> {
    if (this.busy || this.hostContext === undefined) {
      return;
    }
    const input: string = this.urlInput.trim();
    if (input.length === 0) {
      this.message = '请输入服务器地址,例如 http://192.168.8.3:8000';
      this.messageIsError = true;
      return;
    }
    this.busy = true;
    this.message = '';
    const result = await saveBaseUrl(this.hostContext, input);
    this.busy = false;
    if (result.ok) {
      this.message = '已连接:' + result.url;
      this.messageIsError = false;
      this.onSaved();
    } else {
      this.message = result.error;
      this.messageIsError = true;
    }
  }

  build() {
    if (this.show) {
      Column({ space: 10 }) {
        Text('欢迎使用砚席')
          .fontSize(20)
          .fontWeight(FontWeight.Bold)
        Text('说出学习目标即可定制专属课程,练完看错因报告。先连接你的砚席服务器开始。')
          .fontSize(13)
          .fontColor('#666666')

        TextInput({ placeholder: '服务器地址,例如 http://192.168.8.3:8000' })
          .onChange((value: string) => {
            this.urlInput = value;
          })
          .width('100%')

        Button(this.busy ? '连接中…' : '连接')
          .enabled(!this.busy)
          .onClick(() => {
            this.doSave();
          })

        if (this.message.length > 0) {
          Text(this.message)
            .fontSize(13)
            .fontColor(this.messageIsError ? '#c62828' : '#2e7d32')
        }

        Text('连上后:到「设置」页注册/登录账号;课程定制在 Web 端「定制课程」页完成,移动端即可开练。')
          .fontSize(11)
          .fontColor('#999999')
      }
      .alignItems(HorizontalAlign.Start)
      .padding(16)
      .borderRadius(12)
      .backgroundColor('#e8f0fe')
      .width('100%')
    }
  }
}
