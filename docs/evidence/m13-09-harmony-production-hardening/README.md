# M13-09 HarmonyOS 生产加固 — 验收证据归档

- 日期：2026-09-09（hmharness 实现与构建验证 06:58–07:30；集成方独立审计/Android 回归/运行时冒烟/复检 07:30–07:52）
- 分支：`feature/m13-09-harmony-production-hardening`（基于 `main@de5f41a`（M13-08 状态回填提交），本地 git 可验证）
- 状态：本地实现（hmharness）、本地模拟器验收（集成方 Claude）完成；已随 **PR #67**「feat(harmony): harden production readiness」（https://github.com/swsgbl/ai-learning-os/pull/67）合并 main——PR head `998f701a6ccf8e4d2293807c892644266b8965b5`（tree `18bebe450952b9cf8598a260b7a31b4dcfb36abf`，本地 git 可验证），PR CI run `34293394598` 四项 job（API/Android/Docker/Web）全部 success；merge commit `4572551f8ce88b150c5cb9bf6a2d910af2ab18d6`，merge 后 main CI run `34293722342` 四项 job 全部 success（Web 1m15s、Docker 2m27s、API 3m49s、Android 3m26s）；远端功能分支 `feature/m13-09-harmony-production-hardening` 已删除
- 原始证据路径：`.verify/m13-09-harmony-production-hardening/`（gitignored，不入库；本 README 不复制任何原始工件内容）
- 入库证据：本 README（唯一入库文件）
- 结论：**PASS（本地口径）**——mock 契约 54/54、全量 tests/android_smoke 295 passed / 1 skipped（与基线一致）、clean + assembleHap exit 0、最终警告 3→1、模拟器默认错误态/Settings 保存订阅刷新/停服错误恢复全流程通过、HAP 哈希运行时验收前后一致

## 范围（刻意收窄）

- 本切片为交付前生产加固：显式 targetSdkVersion、消灭 `[object Object]` 错误文案、非 2xx 响应体不透出 UI、SettingsStore may-throw 告警消除与通知失败包含。**不新增任何业务能力、不改任何契约**。
- **不含**：AGC 签名/签名材料、真机验证、真实 provider、生产后端/生产 DB、任何写链路、HarmonyOS CI、发布或部署；mock fixture 与共享契约零改动。

## 生产变更摘要

| 文件 | 变更 |
|---|---|
| `apps/harmony/build-profile.json5` | default product 显式新增 `"targetSdkVersion": "6.1.1(24)"`（与既有 `compatibleSdkVersion` 一致），消除基线告警 `WARN: ArkTS:CHECK Missing targetSdkVersion`；**未添加任何 signingConfig/签名材料/密钥**（`signingConfigs` 仍为空数组） |
| `apps/harmony/entry/src/main/ets/ErrorText.ets`（新增） | 异常→用户可读文本的统一收敛（纯函数、无状态、无 IO，~100 行）：`safeErrorText(e)` 白名单提取 `code`（仅 number）/`message`（仅 string），200 字符有界截断；裸对象/空串/`toString === '[object Object]'` 落固定中文兜底「未知错误」；**绝不 `JSON.stringify` 整个异常对象**（不随对象字段把 URL/令牌/响应体带出到 UI）；自身任何提取步骤失败均有静态兜底、绝不抛出 |
| `apps/harmony/entry/src/main/ets/AiosApi.ets` | 删除旧 `errToString`（`${e}` 对裸对象产生 `[object Object]` 的根因）；网络层/JSON 解析失败文案经 `safeErrorText` 收敛；**非 2xx 响应体不再透出 UI**——5xx 固定「服务内部错误 (HTTP 5xx):请稍后重试或联系管理员」（响应体可能含栈/内部路径）、400 固定「请求被服务拒绝 (HTTP 400):请检查请求参数」（阻断 FastAPI detail 对注入探针的回显）、其余（401/403/404 等）仅「请求失败 (HTTP <status>)」；只读 GET、零重试、零持久化、零凭据、`destroy()` in finally 等边界全部不变 |
| `apps/harmony/entry/src/main/ets/components/SettingsStore.ets` | 消除 `SettingsStore.ets:56` may-throw 静态告警——删除「返回 Promise 的 openPrefs」间接层（间接层函数体内的 await 不在调用方 try/catch 词法范围内），`preferences.getPreferences/getSync/put/flush` 全部词法地位于调用方 try/catch 块内；保存失败文案经 `safeErrorText` 收敛；`emitter.emit` 单独包 try/catch（通知失败不影响已成功的持久化结果，failure containment）；preferences 名称/键名（`aios_settings`/`api_base_url`）、默认值、UrlPolicy 白名单校验、事件语义（put+flush 全部成功才 emit `EVENT_ID_URL_CHANGED`）全部不变；`loadBaseUrl` 任何异常仍回退 `DEFAULT_API_BASE_URL` 不抛出 |

本切片生产改动面 4 files，+62/−45（git diff --numstat 本地可验证）；`module.json5` 零改动（仍仅 `ohos.permission.INTERNET`）。

## 验收事实

| 项目 | 结果与依据 |
|---|---|
| 本地测试（mock 契约） | `python tools/harmony_mock/test_contract.py` **54/54 passed**（hmharness Round A/B 双轮一致；本切片零契约改动） |
| 本地测试（Android 回归，集成方独立执行） | 全量 `pytest tests/android_smoke` **295 passed / 1 skipped in 20.21s**（canonical venv `D:\AI Learning OS\ai-learning-os\.venv\Scripts\python.exe`；与 M13-08 基线完全一致，skip 为既有 Windows symlink 特权测试） |
| 集成方独立 diff 审计 | 仅 4 个预期 Harmony 文件变更（3 M + 1 新增）；`module.json5` 不在 diff 中且仍仅 `ohos.permission.INTERNET`；全量扫描无 signingConfig 材料引用（仅既有空数组）、无 token/key/password 实质内容（仅边界注释）、无响应体透出（`body` 仅用于空检查与成功路径 JSON.parse）、`errToString` 全仓已删除、`[object Object]` 仅存在于「绝不输出」的文档注释；`git diff --check` 干净（exit 0，仅 CRLF 提示） |
| clean 构建（hmharness） | `apps/harmony` 下、进程环境 `DEVECO_SDK_HOME=C:\DevEco-Studio\sdk`（注意 `set "VAR=value"` 引号形式，尾随空格会致 `00303217 Invalid value`），`hvigorw.bat clean --no-daemon`（BUILD SUCCESSFUL in 1s283ms）与 `hvigorw.bat assembleHap --no-daemon`（BUILD SUCCESSFUL，clean 后首跑 6s960ms）均 exit 0（`build_evidence/` 全日志） |
| 最终警告清单 | **仅 1 项**：`WARN: No signingConfig found for product default`（`:entry:default@SignHap`）——刻意保留的诚实未签名边界；基线 3 项中的 `Missing targetSdkVersion` 与 `SettingsStore.ets:56` may-throw 均已消除（`build_evidence/hvigor_assembleHap_final.log` 全 35 行，无其它任何告警） |
| HAP 产物 | `apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap` **422721 bytes、SHA256 `D382D5969EF79513220E2741196EEEAAF920260268F4ED9E12CC9A311979B919`**；运行时验收后未重建，集成方独立复检 size/hash 逐字节一致（`runtime/23_hap_hash_recheck.txt`） |
| 运行时冒烟（集成方，`hdc -t 127.0.0.1:5557`） | 全新卸载重装上述未签名 HAP（`01_uninstall.log`/`02_install.log`），`aa start` 启动成功（`03_launch.log`），**App PID 25301** 全程稳定至收尾（`04_pid_initial.txt`/`21_final_pid.txt`）。四个验证点全部通过（下表） |

### 运行时验证点明细

| # | 验证点 | 结果与关键证据 |
|---|---|---|
| 0 | 默认地址错误文案（加固生效的直接对照） | 首启默认 `http://127.0.0.1:8000`，Home 六区错误态文案 `网络请求失败: 2300007 Failed to connect to the server`——M13-05/M13-03 同场景的 `[object Object]` **已消灭**，code+message 白名单提取可见（`runtime/05_home_initial.json`/`05_home_initial.jpeg`） |
| 1 | Settings 加载 + 保存 + 订阅者免重启刷新 | 真实 Settings UI：长按输入框 → 系统菜单全选 → 剪切清除污染串 → 输入 `http://192.168.8.3:8766/` → 点「保存」→ 布局显示 `已保存: http://192.168.8.3:8766/`（`07`–`12` 系列）；**App 不重启**切学习 Tab：显示 `服务地址: http://192.168.8.3:8766/` 且三篇确定性论文全部渲染（`15_study_after_url_change.json`/`.jpeg`）——`emitter` 订阅刷新链路（M13-09 重构后的 SettingsStore emit 包裹）完好 |
| 2 | 只读数据面渲染 mock 数据 | 学习 Tab 三篇论文全字段渲染：Attention Is All You Need（2017/NeurIPS/ML/medium/45 分钟/transformer·attention·NLP/arxiv 链接）、BERT（2019/NAACL/Google Research/NLP/hard/60 分钟/arxiv 链接）、ImageNet CNN（2012/NeurIPS/UofT/CV/medium/50 分钟/CNN·ImageNet·deep learning）（`15_study_after_url_change.json`） |
| 3 | 停服错误态 | 停止 mock 并实证 8766 LISTENING=0（`16_mock_stopped_port.txt`）后学习 Tab 点「刷新」：先加载态（`17_study_error_after_mock_stop.json`，此时旧列表已清空）约 15 秒超时后错误态 `网络请求失败: 2300028 Operation timeout` + 「重试」按钮——中文可读、无 `[object Object]`、旧论文数据已清空、App 不崩溃（PID 25301 不变）（`18_study_error_resolved.json`/`.jpeg`） |
| 4 | 恢复流 | 重启同一 mock（`19_mock_restart.log`，监听实证）后点「重试」：三篇论文完整恢复、错误文案消失、无重试按钮残留（`20_study_recovered.json`/`.jpeg`，三篇论文标题逐项核对 FOUND） |
| 5 | 收尾清理 | 全部 mock 停止后 8766 端口任意状态 socket 数 0、LISTENING 数 0、无 harmony_mock 命令行进程（`22_final_cleanup_proof.txt`）；App PID 25301 收尾仍存活 |

## 原始证据清单

以下文件位于 `.verify/m13-09-harmony-production-hardening/`，**均不入库**（`.verify/` 在 `.gitignore` 中；布局 JSON/截图体积大且含过程性中间态，仅本地留存，本 README 为唯一入库证据）：

- hmharness 报告：`HMHARNESS_ROUND_A.md`（实现与构建验证）、`HMHARNESS_ROUND_B.md`（验证-only 复核，ALL CHECKS PASS）
- 构建证据：`build_evidence/assembleHap_final.log`（完整 35 行含最终唯一 WARN）、`build_evidence/hvigor_clean.log`、`build_evidence/hvigor_assembleHap_final.log`、`build_evidence/clean_baseline.txt`（含 `set VAR=` 尾随空格失败原始输出）、`build_evidence/clean_final.txt`
- 运行时证据（`runtime/`）：`01_uninstall.log`–`04_pid_initial.txt`（全新安装/启动/PID 25301）、`05_home_initial.json`/`.jpeg`（默认地址错误文案——`[object Object]` 消灭直接对照）、`06_mock_server.log`（mock 启动）、`07_settings_before.json`–`12_settings_saved.json`（Settings 保存全过程含污染输入清除）、`13_study_after_save.json`/`14_keyboard_hidden.json`（IME 遮挡中间态与收起）、`15_study_after_url_change.json`/`.jpeg`（免重启订阅刷新 + 三篇论文）、`16_mock_stopped_port.txt`（停服监听数 0）、`17_study_error_after_mock_stop.json`（加载态、旧数据已清空）、`18_study_error_resolved.json`/`.jpeg`（错误态文案）、`19_mock_restart.log`、`20_study_recovered.json`/`.jpeg`（恢复）、`21_final_pid.txt`、`22_final_cleanup_proof.txt`（端口/进程清理）、`23_hap_hash_recheck.txt`（运行时后哈希复检）、`layout_find.py`（布局 JSON 解析辅助脚本，非验收工件）
- 集成方最终报告：`CLAUDE_REPORT.md`

## 复现概述

1. 本地测试：`python tools/harmony_mock/test_contract.py`（54/54）与全量 `pytest tests/android_smoke -q`（295 passed / 1 skipped，canonical venv）。
2. 在 `apps/harmony` 所在进程设置 `DEVECO_SDK_HOME=C:\DevEco-Studio\sdk`（引号形式），`hvigorw.bat clean --no-daemon` 与 `hvigorw.bat assembleHap --no-daemon`，核对警告清单仅剩未签名一项并记录 HAP 字节数/SHA256。
3. `hdc -t 127.0.0.1:5557` 全新卸载重装该 HAP 并启动（记录 PID），首屏核对默认地址错误文案无 `[object Object]`。
4. 启动 `python tools/harmony_mock/server.py --host 0.0.0.0 --port 8766`（仅本地验收），真实 Settings UI 清除污染输入后保存 `http://192.168.8.3:8766/`，不重启 App 切学习 Tab 核对订阅刷新与三篇论文。
5. 停 mock（核对 8766 listeners=0）后学习 Tab 点「刷新」，等待超时后核对错误态文案/数据清空/不崩溃；重启 mock 点「重试」核对完整恢复。
6. 收尾停全部 mock，核对 8766 监听数 0 与无残留进程，复检 HAP size/hash。

## 已知边界

- 本验收是本地模拟器 + mock only 口径，不代表 HarmonyOS 真机、真实 provider、生产后端、生产 DB、任何写链路或生产可用；加固不改变任何业务能力边界。
- 未使用 AGC key、签名配置或自动签名；未签名 HAP（`No signingConfig found` 为唯一保留告警）直装只是本地验收形态，**不构成发布形态，不声称已签名/可发布**；未打 tag、未部署。
- HAP 为 zip 打包产物，同源重建哈希可能不同；本文记录的 SHA256 以当前本地留存、模拟器验收所用产物为准（运行时验收后独立复检一致）。
- CI 无 HarmonyOS job：本切片全部 HarmonyOS 验证为本地口径，不构成远端 CI 验证；PR #67 的 PR CI run `34293394598` 与 merge 后 main CI run `34293722342`（各四项 job API/Android/Docker/Web 全部 success）不能扩大为 HarmonyOS 远端验证。
- 仓库状态（已回填 2026-09-09）：**PR #67 已合并 main**——PR head `998f701a6ccf8e4d2293807c892644266b8965b5`（tree `18bebe450952b9cf8598a260b7a31b4dcfb36abf`），PR CI run `34293394598` 四项 job（API/Android/Docker/Web）全部 success；merge commit `4572551f8ce88b150c5cb9bf6a2d910af2ab18d6`（本地 git 可验证：parents 为 PR #66 merge commit `7f250e1` 与 PR head `998f701`，merge tree 与 PR head tree 同为 `18bebe4`），merge 后 main CI run `34293722342` 四项 job 全部 success（Web 1m15s、Docker 2m27s、API 3m49s、Android 3m26s）；远端功能分支 `feature/m13-09-harmony-production-hardening` 已删除；`production_ready=false` 语义不变。
- 剩余生产阻塞（均待运维显式授权评估）：AGC 签名与发布流程、真机验证、真实 provider 冒烟、生产后端/生产 DB 接入、HarmonyOS CI 缺位。
