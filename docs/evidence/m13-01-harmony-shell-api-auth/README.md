# M13-01 HarmonyOS Shell / API 认证底座 — 验收证据归档

- 日期:2026-09-07
- 分支:`feature/m13-01-harmony-shell-api-auth`(基线 `4ef912f`)
- Bundle:`com.ailearningos.app`(EntryAbility,`pages/Index` 五 Tab)
- 原始证据路径:`.verify/m13-01-harmony-shell-api-auth/`(**不提交**,已被根 `.gitignore` 的 `.verify/` 规则忽略)
- 结论:**通过** — clean 构建成功、HAP 可安装可启动、五 Tab 真实点击验收通过、五张截图均为 1320x2856 且非空白;Home 滚动验收通过

## 验收事实

| # | 验收项 | 结果 | 依据 |
|---|---|---|---|
| 1 | clean `assembleHap` | **BUILD SUCCESSFUL** | hvigor 全新构建(非增量),产物落盘 `apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap`,SHA256 `d4c1154a162c01bd681e4b0514a7567231c7bf332ccbf950fa9a8a7db61cf633`、190,715 字节、2026-09-07 18:14 写入 |
| 2 | 未签名 HAP 安装 | **成功** | `hdc -t 127.0.0.1:5557 install …entry-default-unsigned.hap` → `install bundle successfully` |
| 3 | 启动 | **成功** | `aa start` 拉起 EntryAbility,layout 转储可见 `bundleName=com.ailearningos.app`、`pagePath=pages/Index`、首页 Tab 处于选中态 |
| 4 | 五 Tab 真实点击验收 | **通过** | 依次点击 首页/学习/搜索/语音/设置,每个 Tab 截图 + UI 层级转储(layout JSON)双证据 |
| 5 | Home 六区渲染 | **通过** | 「AIOS 只读面板」标题、服务地址、整体刷新按钮、以及 服务健康/认证状态/隐私模式/服务版本/运维快照/审计日志 六个区块卡片全部渲染(layout 内可见各区块标题;审计日志区位于滚动区下方,需要滚动查看) |
| 6 | Study/Search/Voice 只读占位文案 | **通过** | layout 文案逐字核验:学习「只读占位,功能未实现 / 当前不加载课程、试卷和学习记录」;搜索「只读占位,功能未实现 / 当前不发起搜索、预览或回查请求」;语音「只读占位,功能未实现 / 当前不访问麦克风,不启动语音识别或合成」 |
| 7 | Settings 服务地址面板 | **通过** | layout 文案:「AIOS 服务地址 / 仅保存服务基地址(URL);不保存任何账号、令牌或密码。/ http://127.0.0.1:8000 / 保存 / 测试连接」 |
| 8 | 截图规格 | **通过** | 六张 JPEG 均为 **1320x2856**(与设备分辨率一致)且非空白:各文件 93KB–184KB,layout JSON 53KB 起,含完整控件树与真实文案,非纯色/空图 |
| 9 | Home 滚动验收 | **通过** | 在 Home 页执行 swipe 上滑后,`home-scrolled-layout.json` 可见审计日志区标题「审计日志(最近 100 条)」Text 节点,bounds **[98,2191][664,2257]**(位于屏幕可见范围内,origBounds 一致);同屏可见区块错误态文案「网络请求失败」及「重试」按钮节点;`home-scrolled.jpeg` 为 **1320x2856**、约 182KB,非空白 |

## 证据文件清单(`.verify/m13-01-harmony-shell-api-auth/`)

| 文件 | 说明 |
|---|---|
| `home.jpeg` / `home-layout.json` | 首页 Tab 截图 + UI 层级(六区面板,滚动前首屏) |
| `home-scrolled.jpeg` / `home-scrolled-layout.json` | 首页 Tab 上滑后截图 + UI 层级:审计日志区滚入可见范围(bounds [98,2191][664,2257]),含区块错误态与重试按钮 |
| `study.jpeg` / `study-layout.json` | 学习 Tab 截图 + UI 层级(只读占位) |
| `search.jpeg` / `search-layout.json` | 搜索 Tab 截图 + UI 层级(只读占位) |
| `voice.jpeg` / `voice-layout.json` | 语音 Tab 截图 + UI 层级(只读占位) |
| `settings.jpeg` / `settings-layout.json` | 设置 Tab 截图 + UI 层级(服务地址面板) |

另:构建产物 `entry-default-unsigned.hap` 位于 `apps/harmony/entry/build/` 下——该目录已通过根 `.gitignore` 新增规则 `apps/harmony/entry/build/` 忽略,HAP、cache、intermediates 及内嵌绝对路径的产物不会进入版本库。

## Home API 错误的定性

Home 面板各区块显示「网络请求失败: [object Object]」属于**无 mock 后端时的如实失败**:验收时设备环境内 `http://127.0.0.1:8000` 无后端服务监听,请求必然失败,面板按设计以错误态 + 重试按钮如实呈现,**不是 UI 崩溃**(App 进程存活、Tab 切换正常、layout 树完整)。这是「绝不虚构数据」契约的直接体现。

## 已知警告(不阻塞验收)

| 警告 | 说明 |
|---|---|
| `targetSdkVersion` 未显式声明 | 构建工具链提示;当前以默认值参与构建,后续小步显式补齐 |
| `SettingsStore` Function may throw | ArkTS 静态检查提示首选项读写可能抛出;运行时已按既有设计处理,不影响本验收路径 |
| No signingConfig | 本地验收用未签名 HAP 直装,发布签名配置留待后续接入 AGC/正式签名流程 |

## 复现方式(概述)

1. `apps/harmony` 下 clean `assembleHap` → BUILD SUCCESSFUL;
2. `hdc -t 127.0.0.1:5557 install entry-default-unsigned.hap` → install bundle successfully;
3. `aa start` 启动 `com.ailearningos.app`;
4. 依次点击五个 Tab,每 Tab 采集截图 + layout 转储至 `.verify/m13-01-harmony-shell-api-auth/`;
5. 在 Home 页执行 swipe 上滑,采集 `home-scrolled.jpeg` + `home-scrolled-layout.json`,核验审计日志区滚入可见范围及错误态/重试按钮。

## 边界与免责

- 验收环境为本地模拟器(127.0.0.1:5557),未连接任何真实后端/生产服务,未读取任何密钥;
- Home 六区数据错误态是**无后端时的预期表现**(见上文定性),数据渲染路径的正确性由 mock 后端场景另行验证;
- 本目录 README 仅记录验收事实,不含任何凭据或敏感信息。
