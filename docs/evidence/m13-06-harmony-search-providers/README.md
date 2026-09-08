# M13-06 HarmonyOS Search providers 只读列表 — 验收证据归档

- 日期：2026-09-08
- 分支：`feature/m13-06-harmony-search-providers`（基于 `origin/main@34d30ec4d9afee15dda037713ab9867f3e33d1f8`，即 PR #60 merge commit（M13-05 已合并、其 merge 后 main CI 四项 job 全绿），本地 git 可验证）
- 状态：本地实现、本地模拟器验收与 mock 契约/Android 回归测试完成；本地验收后已随 PR #61 合并——PR CI run 34218137511 四项 success，merge commit 8d4445a，merge 后 main CI run 34238561233 四项 success；本 README 保留本地验收证据与边界
- 原始证据路径：`.verify/m13-06-harmony-search-providers/`（gitignored，不入库；本 README 不复制任何原始工件内容）
- 入库证据：本 README（唯一入库文件）
- 结论：**PASS（本地口径）**——mock 契约 30/30、Android 冒烟单测回归 287 passed / 1 skipped、clean HAP 构建成功、模拟器 Stage A/B/C（默认地址错误态 / Settings 保存后不重启正向 / 停服错误与重启恢复）全流程通过

## 范围（刻意收窄）

- 本切片仅实现 HarmonyOS `GET /api/v1/search/providers` 检索 provider 只读列表——HarmonyOS 各业务域只读接入（评估项「搜索」）的第一切片。
- **不含**：M12-04 其余 search 端点（`plan`/`queries`/`queries/{id}`，对 Harmony 一律 404）、任何搜索发起/预览/回查、认证、真实 provider、生产后端、生产 DB、任何写路径。

## 生产变更摘要

| 文件 | 变更 |
|---|---|
| `apps/harmony/entry/src/main/ets/AiosApi.ets` | 新增第 8 个只读端点 `AiosEndpoint.SEARCH_PROVIDERS = '/api/v1/search/providers'`（仍硬编码仅 GET）；新增 `SearchProviderOut` DTO（`name`/`kind`/`enabled`/`unavailable_reason: string \| null`）与 `SearchProvidersData { items }`，字段与共享 mock 契约精确对齐；导出 `getSearchProviders` |
| `apps/harmony/entry/src/main/ets/components/SearchPane.ets` | 静态只读占位升级为 providers 只读列表：单一列表状态机 LOADING/EMPTY/SUCCESS/ERROR（ERROR 展示原始错误并带「重试」；SUCCESS 渲染 provider 名称/kind/启用态并带「刷新」）；`unavailable_reason` 仅非 null 时展示，`enabled=false` 且原因为空时以「未启用」兜底；订阅 SettingsStore 既有 `aios://settings/url_changed` 事件（稳定回调、先 off 再 on、带回调 `emitter.off` 精确退订、回调内以 `loadBaseUrl` 重读持久化为权威来源）；`disposed` 守卫与请求代际守卫（`requestGeneration` 过期响应丢弃）；元数据 Flex(Wrap) 窄屏换行；无写请求、无凭据、不访问麦克风/存储 |
| `apps/harmony/entry/src/main/ets/pages/Index.ets` | 搜索 Tab 注释与接线说明更新（无行为变化） |
| `tools/harmony_mock/server.py` | 允许路径加入 `/api/v1/search/providers`；fail-closed 收紧（B1）：允许清单 GET 端点拒绝一切查询串，唯一例外 audit 整串须精确为 `?limit=100` 方才委托共享契约 |
| `tools/harmony_mock/test_contract.py` | 契约测试 20 → 30 项：providers 正例（含禁用项按稳定 name/kind 定位校验 `enabled=false` + 非空 `unavailable_reason`）、providers POST/GET-plan/GET-queries/GET-queries-9001 负例、`providers?foo=bar` 与空查询串 `providers?` 负例、audit 精确查询串负例组；usage 补 `--host` |
| `.gitignore` | 新增 `/.hvigor/`（仓库根 hvigor 构建缓存禁止入库） |

改动面合计 6 files，+400/−33（3 个 Harmony 生产文件 + 2 个 mock 工具 + 1 个 `.gitignore`，不含文档；git diff --stat 本地可验证）。

## 验收事实

| 项目 | 结果与依据 |
|---|---|
| 本地测试（mock 契约） | `python tools/harmony_mock/test_contract.py --host 127.0.0.1 --port 18765` **30/30 passed**（exit 0；含 8 正例 + 22 负例） |
| 本地测试（Android 回归） | Android 冒烟 Python 单测 **287 passed / 1 skipped**（skip 为既有 Windows symlink 特权测试；共享 mock 契约未破坏 Android 侧） |
| clean 构建 | `apps/harmony` 下 PowerShell 进程内 `DEVECO_SDK_HOME=C:\DevEco-Studio\sdk`，`hvigorw.bat clean --no-daemon` 与 `hvigorw.bat assembleHap --no-daemon` 均 **BUILD SUCCESSFUL**；最终 HAP `entry-default-unsigned.hap` **298153 bytes**，SHA256 `7D31E3F43F4A2547F2299184D64AA14ADC8735758963F362B1DA46643BF23297` |
| 已知非阻塞警告 | 与 M13-01/02/03/05 基线一致的三项：无显式 `targetSdkVersion`、`SettingsStore.ets` may-throw 静态提示、未签名/无 signingConfig |
| Stage A（默认地址错误态） | 本地模拟器 `127.0.0.1:5557` 全新安装首启，搜索 Tab 在默认 `http://127.0.0.1:8000` 下为真实网络错误态（`网络请求失败: [object Object]` + 「重试」），无任何 provider 渲染（`stage-a-search-default.json` / `stage-a-search-default.jpeg`） |
| Stage B（Settings→Search 正向流） | supervisor 仅为本次运行启动 `tools/harmony_mock/server.py --host 0.0.0.0 --port 8766`；真实 Settings UI 输入并保存 `http://192.168.8.3:8766/`（`settings-url-entered.json` / `settings-url-saved.json`）；**App 不重启**，返回搜索 Tab 即显示 `local-corpus`（enabled）与 `cloud-web`（disabled，如实展示 mock 固定 `unavailable_reason`「未配置云端检索通道（mock 固定禁用，验证不可用原因如实展示）」）（`search-positive2.json` / `search-positive2.jpeg`） |
| Stage C（错误与恢复流） | 停止 mock 后刷新 → 错误态（`网络请求失败: [object Object]` + 「重试」），两个 provider 消失（`search-error-after-stop.json` / `search-error-after-stop.jpeg`）；重启 mock 后点「重试」→ 两个 provider 恢复（`search-recovered.json` / `search-recovered.jpeg`） |
| 进程与端口实证 | 全流程 App PID 保持 **28168** 不变（无重启/崩溃）；收尾 8766 端口监听数 **0**（`port-proof-after-stop.txt`、`final-port-proof-after-stop.txt` 均 `remaining_listeners=0`） |

## 原始证据清单

以下文件位于 `.verify/m13-06-harmony-search-providers/`，**均不入库**（`.verify/` 在 `.gitignore` 中；布局 JSON/截图体积大且含过程性中间态，仅本地留存，本 README 为唯一入库证据）：

- 采信证据（对应上表）：`stage-a-search-default.json/.jpeg`、`settings-url-entered.json`、`settings-url-saved.json`、`search-positive2.json/.jpeg`、`search-error-after-stop.json/.jpeg`、`search-recovered.json/.jpeg`、`port-proof-after-stop.txt`、`final-port-proof-after-stop.txt`
- 其余为过程性中间态（初始布局、Settings 各步转储、早期/复验截图与同名 final 系列等），不作验收依据

## 已知边界

- 本验收是本地模拟器 + mock only 口径，不代表 HarmonyOS 真机、真实 provider、生产后端、生产 DB、治理写链路或生产可用；无任何写路径、无凭据。
- CI 无 HarmonyOS job：本切片全部 HarmonyOS 验证为本地口径，不构成远端 CI 验证；后续 PR 的 API/Android/Docker/Web CI 结果不能扩大为 HarmonyOS 远端验证。
- 仓库状态（回填时点）：PR #61 已合并（merge commit 8d4445a），PR CI run 34218137511 与 merge 后 main CI run 34238561233 四项 success；未打 tag、未部署。
- 错误文案 `网络请求失败: [object Object]` 直接展示原始错误值，用户友好文案为 UX 跟进项；空态（EMPTY）仅代码存在未做 UI 覆盖（mock fixture 恒返 2 个 provider）。
- 未签名 HAP 直装只是本地验收形态，不构成发布形态。
- `production_ready=false` 语义不变。

## 复现概述

1. 运行本地测试：`python tools/harmony_mock/test_contract.py --host 127.0.0.1 --port 18765`（30/30）与 Android 冒烟 Python 单测（287 passed / 1 skipped）。
2. 在 `apps/harmony` 所在 PowerShell 进程设置 `DEVECO_SDK_HOME=C:\DevEco-Studio\sdk`，依次执行 `hvigorw.bat clean --no-daemon` 与 `hvigorw.bat assembleHap --no-daemon`，记录 HAP 字节数与 SHA256。
3. `hdc -t 127.0.0.1:5557` 全新安装 HAP 并启动，进入搜索 Tab，核对默认地址下真实网络错误态（无 provider 渲染）。
4. 启动 `python tools/harmony_mock/server.py --host 0.0.0.0 --port 8766`（仅本地验收使用，不要在生产暴露），经真实 Settings UI 输入/保存 `http://192.168.8.3:8766/`，返回搜索 Tab（不重启 App）核对 local-corpus 启用与 cloud-web 禁用及原因文案。
5. 停止 mock 并核对端口释放，搜索 Tab 点「刷新」核对错误态与「重试」、provider 消失；重启 mock 后点「重试」核对两个 provider 恢复。
6. 收尾停止全部 mock 进程并核对 8766 端口监听数为 0。
