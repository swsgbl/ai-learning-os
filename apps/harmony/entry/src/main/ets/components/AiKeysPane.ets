/**
 * AiKeysPane.ets — AI 密钥管理面板(hm-parity v3 缺口 A,对齐 Android BYOK)
 *
 * 服务端契约(me_ai_keys.py):单槽 "llm";GET /me/ai-keys 返回 {keys,profiles}。
 * 安全铁律:
 *   - api_key 只进请求体,零持久化、零日志;输入框 Password 不回显;
 *   - 槽位只显 configured/last4(服务端本就不回显密钥本体);
 *   - 删除两段确认;探测密钥只在本次请求内使用不落库;
 *   - 全部经 V3Api(bearer fail-closed,detail 透出)。
 * 未登录:仅提示,不发任何请求。
 */

import { common } from '@kit.AbilityKit';
import { ApiResult, hasBearerToken } from '../AiosApi';
import {
  AiKeySlot, AiKeysData, LlmProfile, activateLlmProfile, createLlmProfile,
  deleteAiKey, deleteLlmProfile, getAiKeys, probeLlmModels, putAiKey
} from '../V3Api';
import { DEFAULT_API_BASE_URL, loadBaseUrl } from './SettingsStore';

@Component
export struct AiKeysPane {
  @State baseUrl: string = DEFAULT_API_BASE_URL;
  @State loggedIn: boolean = hasBearerToken();
  @State keys: Array<AiKeySlot> = [];
  @State profiles: Array<LlmProfile> = [];
  @State loading: boolean = false;
  @State loadError: string = '';
  @State notice: string = '';
  @State error: string = '';
  /** 保存密钥表单 */
  @State keyInput: string = '';
  @State keyEndpoint: string = '';
  @State keyModel: string = '';
  @State saveBusy: boolean = false;
  /** 待确认删除('slot' | profileId | '') */
  @State confirmDel: string = '';
  /** 探测 */
  @State probeBusy: boolean = false;
  @State probeModels: Array<string> = [];
  /** Profile 表单 */
  @State profLabel: string = '';
  @State profKey: string = '';
  @State profEndpoint: string = '';
  @State profModel: string = '';
  @State profBusy: boolean = false;

  private disposed: boolean = false;

  async aboutToAppear(): Promise<void> {
    const ctx: common.Context | undefined =
      this.getUIContext().getHostContext() as common.Context | undefined;
    if (ctx !== undefined && ctx !== null) {
      this.baseUrl = await loadBaseUrl(ctx);
    }
    if (this.disposed) {
      return;
    }
    this.loggedIn = hasBearerToken();
    if (this.loggedIn) {
      this.reload();
    }
  }

  aboutToDisappear(): void {
    this.disposed = true;
  }

  private async reload(): Promise<void> {
    if (this.disposed || !hasBearerToken()) {
      return;
    }
    this.loading = true;
    this.loadError = '';
    const r: ApiResult<AiKeysData> = await getAiKeys(this.baseUrl);
    if (this.disposed) {
      return;
    }
    this.loading = false;
    if (!r.ok || r.data === null) {
      this.loadError = r.error;
      return;
    }
    this.keys = r.data.keys !== undefined ? r.data.keys : [];
    this.profiles = r.data.profiles !== undefined ? r.data.profiles : [];
  }

  /** 保存密钥到 llm 槽 */
  private async saveKey(): Promise<void> {
    const key: string = this.keyInput.trim();
    if (key.length < 8) {
      this.error = '密钥至少 8 个字符';
      return;
    }
    this.saveBusy = true;
    this.error = '';
    this.notice = '';
    const r = await putAiKey(this.baseUrl, 'llm', key, this.keyEndpoint.trim(), this.keyModel.trim());
    if (this.disposed) {
      return;
    }
    this.saveBusy = false;
    if (!r.ok) {
      this.error = r.error;
      return;
    }
    this.keyInput = '';
    this.notice = `密钥已保存(尾号 ${r.data !== null ? r.data.last4 : '?'})`;
    this.reload();
  }

  /** 两段确认后删除槽位密钥 */
  private async doDeleteSlot(): Promise<void> {
    this.confirmDel = '';
    this.error = '';
    this.notice = '';
    const r = await deleteAiKey(this.baseUrl, 'llm');
    if (this.disposed) {
      return;
    }
    if (!r.ok) {
      this.error = r.error;
      return;
    }
    this.notice = 'llm 槽位密钥已删除';
    this.reload();
  }

  /** 探测模型(密钥/地址可空=用已保存配置) */
  private async doProbe(): Promise<void> {
    this.probeBusy = true;
    this.error = '';
    this.notice = '';
    this.probeModels = [];
    const r = await probeLlmModels(this.baseUrl, this.keyInput.trim(), this.keyEndpoint.trim());
    if (this.disposed) {
      return;
    }
    this.probeBusy = false;
    if (!r.ok || r.data === null) {
      this.error = r.error;
      return;
    }
    this.probeModels = r.data.models !== undefined ? r.data.models : [];
    this.notice = `探测到 ${this.probeModels.length} 个模型`;
  }

  /** 建厂商配置(首条自动激活) */
  private async doCreateProfile(): Promise<void> {
    const label: string = this.profLabel.trim();
    const key: string = this.profKey.trim();
    if (label.length === 0 || key.length < 8) {
      this.error = '名称与密钥(≥8 字符)均必填';
      return;
    }
    this.profBusy = true;
    this.error = '';
    this.notice = '';
    const r = await createLlmProfile(this.baseUrl, label, key,
      this.profEndpoint.trim(), this.profModel.trim(), true);
    if (this.disposed) {
      return;
    }
    this.profBusy = false;
    if (!r.ok) {
      this.error = r.error;
      return;
    }
    this.profKeyInputClear();
    this.notice = `厂商配置「${label}」已创建并激活`;
    this.reload();
  }

  private profKeyInputClear(): void {
    this.profKey = '';
  }

  /** 激活 profile */
  private async doActivate(profileId: string): Promise<void> {
    this.error = '';
    this.notice = '';
    const r = await activateLlmProfile(this.baseUrl, profileId);
    if (this.disposed) {
      return;
    }
    if (!r.ok) {
      this.error = r.error;
      return;
    }
    this.notice = '已切换激活';
    this.reload();
  }

  /** 两段确认后删除 profile */
  private async doDeleteProfile(profileId: string): Promise<void> {
    this.confirmDel = '';
    this.error = '';
    this.notice = '';
    const r = await deleteLlmProfile(this.baseUrl, profileId);
    if (this.disposed) {
      return;
    }
    if (!r.ok) {
      this.error = r.error;
      return;
    }
    this.notice = '厂商配置已删除';
    this.reload();
  }

  build() {
    Column({ space: 12 }) {
      // 标题栏
      Row({ space: 8 }) {
        Text('AI 密钥管理(BYOK)')
          .fontSize(18)
          .fontWeight(FontWeight.Bold)
          .layoutWeight(1)
        Button('刷新')
          .fontSize(12)
          .height(28)
          .onClick(() => {
            this.reload();
          })
      }
      .width('100%')

      if (!this.loggedIn) {
        Text('登录后可配置个人 AI 密钥(密钥加密存于服务端,本机零留存)。')
          .fontSize(13)
          .fontColor('#8c6d1f')
          .backgroundColor('#fff8e1')
          .borderRadius(8)
          .padding(12)
          .width('100%')
      } else {
        if (this.notice.length > 0) {
          Text(this.notice).fontSize(13).fontColor('#2e7d32').width('100%').maxLines(2)
        }
        if (this.error.length > 0) {
          Text(this.error)
            .fontSize(13)
            .fontColor('#c62828')
            .backgroundColor('#fff3f3')
            .borderRadius(6)
            .padding(8)
            .width('100%')
            .maxLines(4)
        }

        // ── 槽位状态 ──
        Column({ space: 8 }) {
          Text('密钥槽位(个人层仅 LLM,优先级:个人 > 全局 > 部署)')
            .fontSize(13)
            .fontWeight(FontWeight.Bold)
            .width('100%')
          if (this.loading) {
            Text('加载中…').fontSize(13).fontColor('#888888')
          } else if (this.loadError.length > 0) {
            Text(this.loadError).fontSize(13).fontColor('#c62828').maxLines(3)
          } else if (this.keys.length === 0) {
            Text('暂无槽位数据').fontSize(13).fontColor('#888888')
          } else {
            ForEach(this.keys, (s: AiKeySlot) => {
              Column({ space: 4 }) {
                Row({ space: 8 }) {
                  Text(`槽位 ${s.slot}`)
                    .fontSize(14)
                    .fontWeight(FontWeight.Bold)
                    .layoutWeight(1)
                  Text(s.configured ? `已配置 ····${s.last4 !== null ? s.last4 : ''}` : '未配置')
                    .fontSize(12)
                    .fontColor(s.configured ? '#2e7d32' : '#999999')
                    .backgroundColor(s.configured ? '#f6ffed' : '#f5f5f5')
                    .borderRadius(4)
                    .padding({ left: 6, right: 6, top: 2, bottom: 2 })
                  if (s.configured) {
                    if (this.confirmDel === `slot:${s.slot}`) {
                      Text('确认删除?')
                        .fontSize(12)
                        .fontColor('#c62828')
                      Button('是')
                        .fontSize(12)
                        .height(26)
                        .backgroundColor('#c62828')
                        .onClick(() => {
                          this.doDeleteSlot();
                        })
                      Button('否')
                        .fontSize(12)
                        .height(26)
                        .onClick(() => {
                          this.confirmDel = '';
                        })
                    } else {
                      Button('删除')
                        .fontSize(12)
                        .height(26)
                        .backgroundColor('#eeeeee')
                        .fontColor('#c62828')
                        .onClick(() => {
                          this.confirmDel = `slot:${s.slot}`;
                        })
                    }
                  }
                }
                .width('100%')
                if (s.updated_at !== null && s.updated_at !== undefined) {
                  Text(`更新于 ${s.updated_at} · 生效: ${s.effect}`)
                    .fontSize(11)
                    .fontColor('#999999')
                    .width('100%')
                }
              }
              .width('100%')
              .padding(8)
              .backgroundColor('#ffffff')
              .borderRadius(6)
            }, (s: AiKeySlot) => `${s.slot}-${s.configured}`)
          }
        }
        .width('100%')
        .padding(12)
        .backgroundColor('#f5f5f5')
        .borderRadius(8)
        .alignItems(HorizontalAlign.Start)

        // ── 保存密钥 + 探测 ──
        Column({ space: 8 }) {
          Text('保存 LLM 密钥').fontSize(15).fontWeight(FontWeight.Bold).width('100%')
          TextInput({ placeholder: 'API 密钥(仅发送到服务端加密存储,本机不留存)' })
            .type(InputType.Password)
            .fontSize(13)
            .width('100%')
            .height(40)
            .onChange((v: string) => {
              this.keyInput = v;
            })
          TextInput({ placeholder: '接口地址 endpoint(可留空=用服务端默认)' })
            .fontSize(13)
            .width('100%')
            .height(40)
            .onChange((v: string) => {
              this.keyEndpoint = v;
            })
          TextInput({ placeholder: '模型 model(可留空)' })
            .fontSize(13)
            .width('100%')
            .height(40)
            .onChange((v: string) => {
              this.keyModel = v;
            })
          Row({ space: 8 }) {
            Button(this.saveBusy ? '保存中…' : '保存密钥')
              .fontSize(13)
              .height(36)
              .layoutWeight(1)
              .backgroundColor('#1890ff')
              .enabled(!this.saveBusy)
              .onClick(() => {
                this.saveKey();
              })
            Button(this.probeBusy ? '探测中…' : '探测模型')
              .fontSize(13)
              .height(36)
              .layoutWeight(1)
              .enabled(!this.probeBusy)
              .onClick(() => {
                this.doProbe();
              })
          }
          .width('100%')
          if (this.probeModels.length > 0) {
            Text(`可用模型(${this.probeModels.length} 个,最新优先):`)
              .fontSize(12)
              .fontColor('#666666')
              .width('100%')
            Scroll() {
              Column({ space: 2 }) {
                ForEach(this.probeModels.slice(0, 20), (m: string) => {
                  Text(`· ${m}`)
                    .fontSize(12)
                    .fontColor('#333333')
                    .width('100%')
                }, (m: string) => m)
              }
              .width('100%')
            }
            .height(120)
            .scrollBar(BarState.Off)
          }
        }
        .width('100%')
        .padding(12)
        .backgroundColor('#f5f5f5')
        .borderRadius(8)
        .alignItems(HorizontalAlign.Start)

        // ── 厂商配置 ──
        Column({ space: 8 }) {
          Text('LLM 厂商配置(多套密钥一键切换,判分即时走激活项)')
            .fontSize(13)
            .fontWeight(FontWeight.Bold)
            .width('100%')
          TextInput({ placeholder: '配置名称(如 我的 DeepSeek)' })
            .fontSize(13)
            .width('100%')
            .height(40)
            .onChange((v: string) => {
              this.profLabel = v;
            })
          TextInput({ placeholder: 'API 密钥' })
            .type(InputType.Password)
            .fontSize(13)
            .width('100%')
            .height(40)
            .onChange((v: string) => {
              this.profKey = v;
            })
          TextInput({ placeholder: '接口地址(可留空)' })
            .fontSize(13)
            .width('100%')
            .height(40)
            .onChange((v: string) => {
              this.profEndpoint = v;
            })
          TextInput({ placeholder: '模型(可留空)' })
            .fontSize(13)
            .width('100%')
            .height(40)
            .onChange((v: string) => {
              this.profModel = v;
            })
          Button(this.profBusy ? '创建中…' : '创建并激活')
            .fontSize(13)
            .height(36)
            .width('100%')
            .backgroundColor('#1890ff')
            .enabled(!this.profBusy)
            .onClick(() => {
              this.doCreateProfile();
            })

          if (this.profiles.length > 0) {
            ForEach(this.profiles, (p: LlmProfile) => {
              Column({ space: 4 }) {
                Row({ space: 8 }) {
                  Text(p.label)
                    .fontSize(13)
                    .fontWeight(FontWeight.Bold)
                    .layoutWeight(1)
                    .maxLines(1)
                    .textOverflow({ overflow: TextOverflow.Ellipsis })
                  if (p.active) {
                    Text('使用中')
                      .fontSize(11)
                      .fontColor('#2e7d32')
                      .backgroundColor('#f6ffed')
                      .borderRadius(4)
                      .padding({ left: 6, right: 6, top: 2, bottom: 2 })
                  }
                  Text(`····${p.last4}`)
                    .fontSize(11)
                    .fontColor('#999999')
                  if (!p.active) {
                    Button('激活')
                      .fontSize(11)
                      .height(24)
                      .backgroundColor('#1890ff')
                      .onClick(() => {
                        this.doActivate(p.id);
                      })
                  }
                  if (this.confirmDel === `prof:${p.id}`) {
                    Text('确认?')
                      .fontSize(11)
                      .fontColor('#c62828')
                    Button('是')
                      .fontSize(11)
                      .height(24)
                      .backgroundColor('#c62828')
                      .onClick(() => {
                        this.doDeleteProfile(p.id);
                      })
                    Button('否')
                      .fontSize(11)
                      .height(24)
                      .onClick(() => {
                        this.confirmDel = '';
                      })
                  } else {
                    Button('删除')
                      .fontSize(11)
                      .height(24)
                      .backgroundColor('#eeeeee')
                      .fontColor('#c62828')
                      .onClick(() => {
                        this.confirmDel = `prof:${p.id}`;
                      })
                  }
                }
                .width('100%')
                if (p.model !== null && p.model !== undefined) {
                  Text(`模型: ${p.model}${p.endpoint !== null && p.endpoint !== undefined ? ` · ${p.endpoint}` : ''}`)
                    .fontSize(11)
                    .fontColor('#999999')
                    .maxLines(1)
                    .textOverflow({ overflow: TextOverflow.Ellipsis })
                    .width('100%')
                }
              }
              .width('100%')
              .padding(8)
              .backgroundColor('#ffffff')
              .borderRadius(6)
            }, (p: LlmProfile) => `${p.id}-${p.active}-${p.last4}`)
          }
        }
        .width('100%')
        .padding(12)
        .backgroundColor('#f5f5f5')
        .borderRadius(8)
        .alignItems(HorizontalAlign.Start)
      }
    }
    .width('100%')
    .alignItems(HorizontalAlign.Start)
  }
}
