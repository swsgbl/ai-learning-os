# M13-05 HarmonyOS Study 论文只读列表 — 验收证据归档

- 日期：2026-09-08
- 分支：`feature/m13-05-harmony-study-papers`（基于 `origin/main@7f344bf8251deb88c7ff917ddf8d9d2f2575c3ce`，即 PR #59 merge commit，本地 git 可验证）
- 状态：本地实现、本地模拟器验收与最终 supervisor 复验完成；本 README 记录提交前本地验收快照——实现时点分支未 commit、未 push、未开 PR，远端 PR/CI/合并状态以后续 PROJECT_STATUS 回填为准，不在此预写。
- 原始证据路径：`.verify/m13-05-harmony-study-papers/`（gitignored，不入库）
- 入库证据：本 README（唯一入库文件）
- 结论：本地测试（mock 契约 20/20、pytest 28 passed）、clean HAP 构建、3A 默认地址真实错误态、3B Settings 保存后不重启刷新三篇确定性论文、3C 停服错误→重启 mock 恢复，全部通过；hmharness 稳定性修复后最终 supervisor 复验（fresh install PID 15016）全流程再次通过

## 生产变更摘要

| 文件 | 变更 |
|---|---|
| `apps/harmony/entry/src/main/ets/AiosApi.ets` | 新增第 7 个只读端点 `AiosEndpoint.PAPERS = '/api/v1/papers'`（仍硬编码仅 GET）；新增 `PaperOut` DTO（`id`/`title`/`subtitle`/`source`/`university`/`year`/`subject`/`difficulty`/`duration_minutes`/`tags`/`origin_url`，可空字段与共享 mock 契约精确对齐）并导出 `getPapers` |
| `apps/harmony/entry/src/main/ets/components/StudyPane.ets` | 静态只读占位升级为论文只读列表：单一列表状态机 LOADING/EMPTY/SUCCESS/ERROR（EMPTY=HTTP 成功零条；ERROR 展示原始错误并带「重试」；SUCCESS 渲染存在字段并带「刷新」）；`aboutToAppear` 一次初始加载；订阅 SettingsStore 既有 `aios://settings/url_changed` 事件——稳定回调、先 off 再 on、带回调 `emitter.off` 精确退订、回调内以 `loadBaseUrl` 重读持久化为权威来源；空可选字段省略渲染、存在时完整渲染（`subtitle`/`university`/`origin_url` 及空 `source`/`subject` 等为空或缺失时不渲染该元素，无「—」占位回退）；hmharness 稳定性修复：元数据行 Flex(Wrap) 窄屏自动换行、标题/副题/元数据 maxLines + TextOverflow.Ellipsis、`disposed` 守卫（组件销毁后异步回调立即返回）、请求代际守卫（`requestGeneration` 递增，过期响应丢弃）；显式 null/undefined 检查，无非空断言；无考试/答题/评分/答案缓存/解释/路由/麦克风/存储/凭据逻辑 |
| `apps/harmony/entry/src/main/ets/pages/Index.ets` | 学习 Tab 注释与接线说明更新（无行为变化） |
| `tools/android_smoke/mock_contract.py` | 共享只读 mock 契约新增确定性 3 篇论文 fixture（`_PAPERS`：Attention Is All You Need / BERT / ImageNet Classification，paper-003 `origin_url: null`）；`GET /api/v1/papers` 返回 200 顶层数组，`POST /api/v1/papers` 返回 404 |
| `tools/harmony_mock/server.py` | 允许路径加入 `/api/v1/papers`（仍只暴露白名单 GET 端点），docstring 更新 |
| `tests/android_smoke/test_mock_contract.py` | 新增 papers 端点正例（顶层数组、首条全字段）与 POST 404 负例；移除旧「GET /api/v1/papers 返回 404」断言 |
| `tools/harmony_mock/test_contract.py` | 契约测试 18 → 20 项：新增 papers GET 正例与 POST 404 负例 |

改动面合计 7 files，+449/−41（3 个 Harmony 生产文件 + 2 个 mock 工具 + 2 个测试文件，不含文档；git diff --stat 本地可验证）。

## 验收事实

| 项目 | 结果与依据 |
|---|---|
| 本地测试 | `python tools/harmony_mock/test_contract.py` **20/20 passed**（含 M13-05 papers 正例与 POST 404 负例）；`pytest tests/android_smoke/test_mock_contract.py tests/android_smoke/test_server.py` **28 passed** |
| clean 构建 | `hvigorw.bat clean --no-daemon` 与 `hvigorw.bat assembleHap --no-daemon` 均 exit 0 / BUILD SUCCESSFUL |
| HAP（初始 Stage 3 构建，3A/3B/3C 证据对应） | `entry-default-unsigned.hap`，240231 bytes，SHA256 `E4255F488C06FAB755F0CA844F407E077533332485737E7EDE1D9DD50FD36B3A`（`hap-sha256.txt`）；已知非阻塞警告与 M13-01/M13-02/M13-03 基线一致：无显式 `targetSdkVersion`、无签名配置、`SettingsStore` may-throw 静态提示 |
| HAP（最终复验构建，hmharness 稳定性修复后，`final-*` 证据对应） | 重建产物 255674 bytes，SHA256 `3E3CF7CBB3160F15FE8A78240F24F4D1036AC6771E90C42E962DC532CB958E0D`（本地 `sha256sum` 复核一致）；已知警告仍仅为同一基线三项 |
| 3A 默认地址错误态 | 本地模拟器 `127.0.0.1:5557` 全新安装首启，学习 Tab 在默认 `http://127.0.0.1:8000` 下为真实网络错误态：`网络请求失败: [object Object]` + 「重试」按钮，无任何论文标题渲染（`stage3_study_default_layout.json` / `stage3_study_default.jpeg`） |
| 3B 正向流 | supervisor 仅为本次运行启动 `tools/harmony_mock/server.py --host 0.0.0.0 --port 8766`；真实 Settings UI 保存 `http://192.168.8.3:8766/`（布局含 `已保存: http://192.168.8.3:8766/`）；**App 不重启**，返回进入学习 Tab 显示 `服务地址: http://192.168.8.3:8766/` 并刷新为三篇确定性论文：Attention Is All You Need / BERT: Pre-training of Deep Bidirectional Transformers / ImageNet Classification with Deep Convolutional Neural Networks，学科（Machine Learning / NLP / CV）、难度、时长、标签全渲染；paper-001/002 显示 arxiv 原文链接，paper-003 `origin_url: null` 不渲染链接；无「重试」按钮（`study-after-url-change.json` / `study-after-url-change.jpeg`） |
| 3C 错误与恢复 | 精确停止 mock（进程与 8766 端口释放实证）后点「刷新」→ 错误态（`网络请求失败: [object Object]` + 「重试」，三篇论文消失）；重启 mock（监听 PID 与端口实证）后点「重试」→ 三篇论文恢复（`stage3c-error3.json/.jpeg`、`stage3c-recovered.json/.jpeg`）；收尾全部 mock 进程停止、端口释放实证（`final-port-proof-after-stop.txt`） |
| UI 自动化输入备注 | 坐标式 `uitest uiInput inputText x y <text>` 在 TextInput 上表现为追加/重复畸形文本，不可用；最终成功方法：系统文本菜单「全选/剪切」清空 → 聚焦输入框 → `uitest uiInput text <url>` 整段输入 → dump 核对与目标完全一致 |

## 最终 supervisor 复验（hmharness 稳定性修复后）

Stage 3 验收后，hmharness 稳定性修复进入 `StudyPane.ets` 生产代码（元数据 Flex(Wrap) 换行、`disposed` 守卫、请求代际守卫），故以最终代码重新构建并全流程复验：

- **构建**：全局 `hvigorw.bat`（在 `apps/harmony`，PowerShell 进程内 `DEVECO_SDK_HOME=C:\DevEco-Studio\sdk`），`clean` 与 `assembleHap` 均 BUILD SUCCESSFUL；最终 HAP 255674 bytes，SHA256 `3E3CF7CBB3160F15FE8A78240F24F4D1036AC6771E90C42E962DC532CB958E0D`（本地 `sha256sum` 复核一致）；已知警告仍仅为基线三项（无显式 `targetSdkVersion`、无签名配置、`SettingsStore` may-throw）。
- **复验流程（fresh install，App PID 15016）**：① 默认 URL 真实网络错误态 + 「重试」；② 真实 Settings UI 保存 `http://192.168.8.3:8766/`；③ **App 不重启**，学习 Tab 渲染全部 3 篇论文；④ 停止 mock 后点「刷新」→ 错误态 + 「重试」、论文消失；⑤ 重启 mock 后点「重试」→ 3 篇论文全部恢复。
- **证据文件**（gitignored `.verify/m13-05-harmony-study-papers/`，均不入库）：`final-default.json/.jpeg`（默认错误态）、`final-settings.json`、`final-menu.json`、`final-selected.json`、`final-input.json`、`final-saved.json`（Settings 真实 UI 各步布局）、`final-positive.json/.jpeg`（**弃用中间态**——该 dump 仍为 Settings 页布局，不作正向证据）、`final-positive2.json`（**采信的正向证明**——学习 Tab 三篇论文）、`final-error.json/.jpeg`、`final-error2.json/.jpeg`（错误态）、`final-recovered.json/.jpeg`（恢复态）、`final-port-proof-after-stop.txt`（收尾端口释放，最终复验收尾重采、覆盖 Stage 3 收尾同名文件）。
- 生产范围仍为只读；`production_ready=false` 不变。

## 原始证据清单

以下文件位于 `.verify/m13-05-harmony-study-papers/`，**均不入库**（`.verify/` 在 `.gitignore` 中；布局 JSON/截图体积大且含过程性中间态，仅本地留存，本 README 为唯一入库证据）：

- 构建产物：`hap-sha256.txt`
- Stage 3A：`stage3_study_default_layout.json`、`stage3_study_default.jpeg`（默认地址错误态主证据）；`stage3_after_launch.jpeg`、`stage3_desktop.jpeg`、`emulator_screen.jpeg`、`initial-hilog.txt`（启动过程辅助截图与初始 hilog）
- Stage 3B 关键证据：`recovery-click.json`（Settings 保存成功布局，59755 B）、`study-after-url-change.json`（68777 B）、`study-after-url-change.jpeg`（334762 B）
- Stage 3B 过程转储：`settings-page.json`、`settings-before-positive.json/.jpeg`、`settings-input-positive.json`、`settings-fixed-positive.json`、`settings-saved-positive.json/.jpeg`、`settings-page-positive.json/.jpeg`、`settings-final-before-study.json/.jpeg`、`longclick-menu.json`、`longclick2.json`、`newurl.json`、`typed.json`、`recovery-before.json`、`recovery-study-layout.json`、`study-ready.json`、`stage3_study_after_click.json/.jpeg`（Settings 真实 UI 输入逐步布局与截图，含坐标 inputText 失败尝试的中间态）
- Stage 3C 错误态：`stage3c-error-layout.json`、`stage3c-error.jpeg`、`stage3c-error2.json/.jpeg`（中间步）、`stage3c-error3.json/.jpeg`（最终采信证据）、`process-proof-before-stop.txt`、`port-proof-after-stop.txt`
- Stage 3C 恢复：`stage3c-before-retry.json`、`process-proof-recovery.txt`、`stage3c-recovered.json`（68777 B）、`stage3c-recovered.jpeg`（334826 B）
- mock 日志：`mock-positive.log`、`mock-recovery.log`、`mock_server.log`（仅含启动监听单行消息——本 mock 刻意关闭逐请求日志，验收依据为确定性 UI 状态迁移而非请求日志）
- 收尾实证：`final-process-proof-before-stop.txt`、`final-port-proof-after-stop.txt`
- 最终 supervisor 复验（`final-*` 系列，详见「最终 supervisor 复验」节）：`final-default.json/.jpeg`、`final-settings.json`、`final-menu.json`、`final-selected.json`、`final-input.json`、`final-saved.json`、`final-positive.json/.jpeg`（弃用中间态）、`final-positive2.json`（采信正向证明）、`final-error.json/.jpeg`、`final-error2.json/.jpeg`、`final-recovered.json/.jpeg`、`final-port-proof-after-stop.txt`（最终复验收尾重采）
- 过程报告：`STAGE3A_REPORT.md`、`STAGE3B_REPORT.md`、`STAGE3C_ERROR_REPORT.md`、`STAGE3C_RECOVERY_REPORT.md`、`STAGE3_REPORT.md`（supervisor 总报告）

## 已知边界

- 本验收是本地模拟器 + mock only 口径，不代表 HarmonyOS 真机、真实 provider、生产后端、生产 DB、治理写链路或生产可用；无任何写路径。
- 空态（EMPTY）在代码中存在但未做 UI 覆盖（mock fixture 恒返 3 篇论文）；滚动行为未测（三篇均落首屏视口）。
- mock 逐请求日志刻意关闭，日志仅含启动监听消息；网络证据由确定性 UI 状态迁移推断。
- 错误文案 `网络请求失败: [object Object]` 直接展示 `ApiResult.error` 原始值，用户友好错误文案为 UX 跟进项。
- 未签名 HAP 直装只是本地验收形态，不构成发布形态；未使用 AGC key、签名配置或自动签名；未 commit/未 push/未开 PR、无远端 CI run（CI 无 HarmonyOS job）、未合并、未打 tag、未部署。
- 证据与构建对应关系：初始 Stage 3 证据（3A/3B/3C 及其过程转储）对应初始 HAP 240231 bytes / SHA256 `E4255F…`；`final-*` 系列证据对应 hmharness 稳定性修复后的最终 HAP 255674 bytes / SHA256 `3E3CF7…`。
- `production_ready=false` 语义不变。

## 复现概述

1. 运行本地测试：`python tools/harmony_mock/test_contract.py`（20/20）与 `pytest tests/android_smoke/test_mock_contract.py tests/android_smoke/test_server.py`（28 passed）。
2. 在 `apps/harmony` 所在 PowerShell 进程设置 `DEVECO_SDK_HOME=C:\DevEco-Studio\sdk`，依次执行 `hvigorw.bat clean --no-daemon` 与 `hvigorw.bat assembleHap --no-daemon`，记录 HAP 字节数与 SHA256。
3. `hdc -t 127.0.0.1:5557` 卸载重装 HAP 并启动，进入学习 Tab，核对默认地址下真实网络错误态（无论文标题）。
4. 启动 `python tools/harmony_mock/server.py --host 0.0.0.0 --port 8766`（仅本地验收使用，不要在生产暴露），经真实 Settings UI 清除/输入/保存 `http://192.168.8.3:8766/`，返回学习 Tab（不重启 App）核对三篇确定性论文。
5. 停止 mock 并核对端口释放，学习 Tab 点「刷新」核对错误态与「重试」；重启 mock 后点「重试」核对三篇恢复。
6. 收尾停止全部 mock 进程并核对 8766 端口释放。
