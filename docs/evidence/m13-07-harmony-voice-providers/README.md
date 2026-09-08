# M13-07 HarmonyOS Voice providers 只读面板 — 验收证据归档

- 日期：2026-09-09（Stage A/B/C 于 2026-09-08 23:50 – 2026-09-09 00:02 采集；当前产物复验于 2026-09-09 00:15–00:16）
- 分支：`feature/m13-07-harmony-voice-providers`（基于 `origin/main@5ef95b29872bf5e50d839ef480f98093f8c195ec`（PR #62 merge commit，本地 git 可验证））
- 状态：本地实现、本地模拟器验收、mock 契约/Android 回归测试与当前产物重装复验完成；已随 **PR #63** 合并 main（merged_at `2026-09-08T16:45:25Z`，PR head `d201b062f86a2179fa5d8421053e4ece478971a7`、merge commit `6c72c75dc3f09e9aeb29f683554f00410102075d`，本地 git 可验证；PR CI run `34252437713` 与 merge 后 main CI run `34253004195` 四项 job 全部 success；本地 worktree 提交 `4bdab4c` 与 PR head 的 tree 一致、commit SHA 不同）
- 原始证据路径：`.verify/m13-07-harmony-voice-providers/`（gitignored，不入库；本 README 不复制任何原始工件内容）
- 入库证据：本 README（唯一入库文件）
- 结论：**PASS（本地口径）**——mock 契约 42/42、Android 冒烟单测回归 32 passed、clean 构建 + 双重独立 `assembleHap` 复验成功、模拟器 Stage A/B/C（默认地址错误态 / Settings 保存后不重启正向 / 停服约 33 秒错误与重启恢复）全流程通过、当前产物 HAP 重装正向复验通过

## 范围（刻意收窄）

- 本切片仅实现 HarmonyOS `GET /api/v1/voice/providers` 语音 provider 只读视图——HarmonyOS 各业务域只读接入（评估项「语音」）的第一切片，也是纯展示切片。
- **不含**：麦克风访问、任何语音识别（ASR）或合成（TTS）的执行、语音会话创建/管理、任何写路径（其余 voice 端点 `token`/`sessions`/`transcribe`/`synthesize`/`trace` 对 Harmony 一律 404，含其 GET 形式）、真实 provider、生产后端、生产 DB、AGC 签名、发布或部署。本切片**不构成任何真实语音能力**。

## 生产变更摘要

| 文件 | 变更 |
|---|---|
| `apps/harmony/entry/src/main/ets/AiosApi.ets` | 新增第 9 个只读端点 `AiosEndpoint.VOICE_PROVIDERS = '/api/v1/voice/providers'`（仍硬编码仅 GET）；新增 `VoiceProviderViewOut`（`requested: string \| null`/`provider`/`fallback`）与 `VoiceProvidersData { voice_mode, asr, tts, privacy_store_audio, privacy_send_context_to_cloud }` DTO，snake_case 与 Android `VoiceDtos.kt VoiceProvidersResponse` 精确对齐；导出 `getVoiceProviders` |
| `apps/harmony/entry/src/main/ets/components/VoicePane.ets` | 语音 Tab 静态只读占位升级为 providers 只读面板：LOADING/SUCCESS/ERROR 状态机（响应是单对象，无有意义空态；ERROR 展示原始错误并带「重试」，成功视图带「刷新」）；成功视图渲染 voice_mode、ASR/TTS 卡片（`fallback=true` 展示「已回退」徽标，`requested` 仅非 null 时展示）、隐私开关事实标签（无任何开关控件）；响应字段类型校验（voice_mode/asr/tts/privacy 布尔任一缺失或类型不符即如实报错）；订阅既有 `aios://settings/url_changed` 事件（稳定回调、先 off 再 on、带回调 `emitter.off` 精确退订、以 `loadBaseUrl` 重读持久化为权威来源）；`disposed` 守卫与请求代际守卫；元数据 Flex(Wrap) 窄屏换行 |
| `tools/android_smoke/mock_contract.py` | 共享只读契约新增 `VOICE_PROVIDERS` 确定性快照（voice_mode=hybrid、ASR=fake 无回退、TTS 请求 cloud-openai-tts 回退 tone、privacy_store_audio=False、privacy_send_context_to_cloud=True），GET 200 / 写方法 404 |
| `tools/harmony_mock/server.py` | 允许路径加入 `/api/v1/voice/providers`（其余 voice 路径一律 404，含 GET 形式） |
| `tools/harmony_mock/test_contract.py` | 契约测试 30 → 42 项：voice providers 正例（整包与 fixture 精确相等）+ 11 项负例（providers 仅 GET、`?foo=bar`/空查询串拒绝、token/sessions（含列表与详情 GET）/transcribe/synthesize/trace（含 GET 形式）全部 404） |
| `tests/android_smoke/test_mock_contract.py` | +3 项 M13-07 测试（fixture 精确相等、providers 写方法 404、其余 voice 路径 404） |
| `tests/android_smoke/test_server.py` | +1 项 loopback 服务器级测试（GET 200 快照逐字段断言 + POST 404） |

改动面合计 7 files，+525/−31（2 个 Harmony 生产文件 + 2 个 mock 工具 + 3 个测试文件，不含文档；git diff --stat 本地可验证）。

## 验收事实

| 项目 | 结果与依据 |
|---|---|
| 本地测试（mock 契约） | `python tools/harmony_mock/test_contract.py` **42/42 passed**（exit 0；9 正例 + 33 负例，全 fail-closed） |
| 本地测试（Android 回归） | `pytest tests/android_smoke/test_mock_contract.py tests/android_smoke/test_server.py` **32 passed**（21+11 个测试函数全通过，共享契约变更未破坏 Android 侧） |
| clean 构建与产物演进 | `apps/harmony` 下 PowerShell 进程内 `DEVECO_SDK_HOME=C:\DevEco-Studio\sdk`，`hvigorw.bat clean --no-daemon` 与 `hvigorw.bat assembleHap --no-daemon` 均 **BUILD SUCCESSFUL**（初始产物 347823 bytes，SHA256 `1BF9B0BC1DED9B855F083AA66EB2228B15BFCBF0ED874B6204F7EEBF66F6AF45`，构建于 2026-09-08 23:42）。构建后一次源码编辑曾在 `VoicePane.ets loadProviders` 留下同作用域重复的 `const d` 声明（未进入任何已验证产物）；该行删除后 hmharness 与 Codex **各自独立重跑 `assembleHap` 均 exit 0 / BUILD SUCCESSFUL / 0 compiler errors**，最终产物 `entry-default-unsigned.hap` **348946 bytes**，SHA256 `970241F92ECE4736D90D8A7ACE9025FD9D2548FA7991911172353DB54CC5C283`（构建于 2026-09-09 00:10；字节数与 SHA256 均经 finisher 会话 stat/sha256sum 独立复核一致） |
| Stage A（默认地址错误态） | 模拟器 `127.0.0.1:5557` 全新卸载重装（`stage-a-uninstall.log`/`stage-a-install.log`，安装源即上述 347823 bytes 初始产物路径，exit 0）首启，**App PID 19960**（`stage-a-app-pid.txt`）；语音 Tab 在默认 `http://127.0.0.1:8000` 下为真实网络错误态（`网络请求失败: [object Object]` + 「重试」），无任何 provider/voice_mode 渲染（`stage-a-voice-default.json`/`.jpeg`） |
| Stage B（Settings→Voice 正向流） | supervisor 仅为本次运行启动 `tools/harmony_mock/server.py --host 0.0.0.0 --port 8766`（stdout 仅监听单行）；真实 Settings UI 输入时先出现污染串 `http://127.0.0.1:8000http://192.168.8.3:8766/`（`stage-b-settings-entered.json`，过程证据），以 HarmonyOS `KEYCODE_DEL=2055` 逐字清除（`stage-b-del1.json`/`stage-b-del10.json`/`stage-b-cleared.json`）后整段输入并核对为**精确** `http://192.168.8.3:8766/`（`stage-b-url.json`），保存成功（`stage-b-saved.json`「已保存: http://192.168.8.3:8766/」）、收起键盘（`stage-b-keyboard-hidden.json`）；**App 不重启（PID 保持 19960）**，切到语音 Tab 即显示：标题栏 `服务地址: http://192.168.8.3:8766/`、`语音模式 (voice_mode) hybrid`、ASR 卡片 `Provider: fake`（requested 为 null 不展示、无回退徽标）、TTS 卡片「已回退」徽标 + `Provider: tone` + `Requested: cloud-openai-tts`、隐私开关 `存储音频 (privacy_store_audio): 关` 与 `发送上下文到云端 (privacy_send_context_to_cloud): 开`（**采信证据 `stage-b-voice-positive2.json`/`.png`**；早期 `stage-b-*` 与 `stage-b-voice-positive.json` 为过程证据，含输入自动化残留的尾部「。」未采信） |
| Stage C（错误与恢复流） | 仅停止已核验的 M13-07 mock 进程树并确认 8766 监听数 0 后，语音 Tab 点「刷新」：短暂保持加载态，**约 33 秒后**显示 `网络请求失败: [object Object]` + 「重试」，provider 面板全部消失（**采信证据 `stage-c-voice-error-33s.json`/`.png`**；`stage-c-voice-error.json`/`.png` 为紧邻过程证据）；App PID 保持 19960。重启同一 mock（监听实证）后点「重试」→ 完整 provider 面板恢复（`stage-c-voice-recovered.json`/`.png`，字段与 Stage B 采信证据逐项一致）；收尾再次全停 mock，8766 监听数 0（finisher 会话另以 netstat 独立复核当前 listeners=0） |
| 当前产物复验（348946 bytes HAP） | Codex 将上述最终产物在 `127.0.0.1:5557` 卸载重装并启动成功（**App PID 8444**）；同一 UI 路径 Settings 精确设置 `http://192.168.8.3:8766/`（`rebuild-url.json`）后语音 Tab 渲染全部预期字段：voice_mode=hybrid、ASR Provider fake、TTS Provider tone、Requested cloud-openai-tts、「已回退」、privacy_store_audio=关、privacy_send_context_to_cloud=开（`rebuild-positive.json`/`.png`，字段由 finisher 会话从 JSON 布局转储独立复核逐项一致）；App PID 保持 8444；复验后 mock 清理 8766 listeners=0 |

## 原始证据清单

以下文件位于 `.verify/m13-07-harmony-voice-providers/`，**均不入库**（`.verify/` 在 `.gitignore` 中；布局 JSON/截图体积大且含过程性中间态，仅本地留存，本 README 为唯一入库证据）：

- 采信证据（对应上表）：`stage-a-voice-default.json/.jpeg`、`stage-b-voice-positive2.json/.png`、`stage-c-voice-error-33s.json/.png`、`stage-c-voice-recovered.json/.png`、`rebuild-url.json`、`rebuild-positive.json/.png`
- 辅助实证：`stage-a-precheck.txt`、`stage-a-uninstall.log`、`stage-a-install.log`、`stage-a-app-pid.txt`、`stage-a-aa-start.log`、`stage-a-click-voice.log`、`stage-a-dump.log`、`stage-a-screenshot.log`、`mock-8766.pid`、`mock-8766.stdout.log`
- 其余 `stage-b-settings-*` / `stage-b-del*` / `stage-b-url` / `stage-b-saved` / `stage-b-cleared` / `stage-b-keyboard-hidden` / `stage-b-voice-positive.json` / `stage-c-voice-error.json/.png` 等为过程性中间态（含输入污染、逐字清除、紧邻错误捕获等），不作验收依据

## UX 跟进项（明确不计为已通过的 UX 质量项）

- 停止 mock 后点「刷新」，面板保持加载态**约 33 秒**才转入错误态（底层 HTTP 失败时延），且错误文案为原始值直出的 `网络请求失败: [object Object]`（Stage A 默认地址错误态同为该文案）。二者合并记为**非阻塞 UX 加固跟进项**：错误态时延与文案可读性未达产品级质量，本验收仅确认「最终能如实进入错误态并可恢复」，**不声称 UX 质量通过**。

## 已知边界

- 本验收是本地模拟器 + mock only 口径，不代表 HarmonyOS 真机、真实 provider、生产后端、生产 DB、任何写链路或生产可用；无任何写路径、无凭据、不访问麦克风/音频/存储，**不构成任何真实语音能力（ASR/TTS 执行）**。
- CI 无 HarmonyOS job：本切片全部 HarmonyOS 验证为本地口径，不构成远端 CI 验证；后续 PR 的 API/Android/Docker/Web CI 结果不能扩大为 HarmonyOS 远端验证。
- 未使用 AGC key、签名配置或自动签名；未签名 HAP 直装只是本地验收形态，不构成发布形态；未打 tag、未部署。
- 仓库状态（已回填 2026-09-09）：**PR #63 已合并**——PR head `d201b062f86a2179fa5d8421053e4ece478971a7`、merge commit `6c72c75dc3f09e9aeb29f683554f00410102075d`（merged_at `2026-09-08T16:45:25Z`，本地 git 可验证），PR CI run `34252437713` 与 merge 后 main CI run `34253004195` 四项 job（API/Android/Docker/Web）全部 success；未打 tag、未部署。
- `production_ready=false` 语义不变。

## 非阻塞代码加固跟进项（另行跟踪，不影响本次验收结论）

1. `VoicePane.ets` 的隐私/嵌套字段类型校验可更完整（当前已校验 voice_mode/asr/tts/provider/requested/fallback/两个 privacy 布尔的类型；可进一步覆盖未来契约扩展的嵌套 privacy 结构与未知字段策略）。
2. 避免把缺失布尔渲染为「关」：当前顶层 privacy 布尔有类型守卫（缺失/类型不符进入错误态），但渲染层 `${boolean ? '开' : '关'}` 的三元形式对未来新增未守卫布尔字段存在把缺失值显示为「关」的风险，宜改为显式区分「关」与「未提供」。
3. `AiosApi.ets` 中 `VoiceProvidersData` 的 mock 示例注释过时：注释写 `privacy_send_context_to_cloud: false`（且 voice_mode 示例为 "local"），而 M13-07 fixture 实际为 `privacy_send_context_to_cloud: true`、`voice_mode: "hybrid"`，宜修正注释使其与 fixture 一致。

## 复现概述

1. 运行本地测试：`python tools/harmony_mock/test_contract.py`（42/42）与 `pytest tests/android_smoke/test_mock_contract.py tests/android_smoke/test_server.py`（32 passed）。
2. 在 `apps/harmony` 所在 PowerShell 进程设置 `DEVECO_SDK_HOME=C:\DevEco-Studio\sdk`，依次执行 `hvigorw.bat clean --no-daemon` 与 `hvigorw.bat assembleHap --no-daemon`，记录 HAP 字节数与 SHA256（当前产物 348946 bytes / `970241F9…`）。
3. `hdc -t 127.0.0.1:5557` 全新卸载重装 HAP 并启动，进入语音 Tab，核对默认地址 `http://127.0.0.1:8000` 下真实网络错误态（无 provider 渲染）。
4. 启动 `python tools/harmony_mock/server.py --host 0.0.0.0 --port 8766`（仅本地验收使用，不要在生产暴露），经真实 Settings UI（污染输入须先清除，可用 `KEYCODE_DEL` 逐字删除）输入/保存精确 `http://192.168.8.3:8766/`，不重启 App 切回语音 Tab，核对 voice_mode/ASR/TTS/回退徽标/隐私开关逐项渲染。
5. 停止 mock 并核对 8766 监听数为 0，语音 Tab 点「刷新」，核对约 33 秒后错误态与「重试」、面板消失；重启 mock 后点「重试」核对完整面板恢复。
6. 收尾停止全部 mock 进程并再次核对 8766 端口监听数为 0。
