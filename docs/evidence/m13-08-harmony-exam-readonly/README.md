# M13-08 HarmonyOS 考试会话只读快照 — 验收证据归档

- 日期：2026-09-09（clean 构建于 02:03–02:04；模拟器证据分两轮采集：正向/错误/恢复 02:05–02:11，URL 变更失效/切回恢复 05:26–05:43）
- 分支：`feature/m13-08-harmony-exam-readonly`（基于 PR #63 merge commit `6c72c75dc3f09e9aeb29f683554f00410102075d` 之上的本地 M13-07 状态回填提交 `be97a72`，本地 git 可验证）
- 状态：本地实现、本地模拟器验收完成；分支含两个本地提交（`47ad110` mock 契约/测试先行切片 + 本次生产 UI 与文档提交），**未 push、未开 PR——PR/CI/合并状态待后续回填，不在此预写**
- 原始证据路径：`.verify/m13-08-harmony-exam-readonly/`（gitignored，不入库；本 README 不复制任何原始工件内容）
- 入库证据：本 README（唯一入库文件）
- 结论：**PASS（本地口径）**——mock 契约 54/54、全量 tests/android_smoke 295 passed / 1 skipped、clean + assembleHap exit 0、模拟器正向/错误恢复/URL 变更失效全流程通过

## 范围（刻意收窄）

- 本切片实现 HarmonyOS 考试域只读第一切片：`GET /api/v1/exams/{exam_id}` 考试会话只读快照的拉取与展示（学习 Tab 内、论文区下方独立考试区）。
- **不含**：作答/改答控件、提交、倒计时或计时器、正确答案与解析展示（公共投影本就不含）、其余 exam 端点（`POST /api/v1/papers/{paper_id}/exams`、PUT answers、POST submit、GET submission/report）一律 404、真实考试会话、生产后端、生产 DB、AGC 签名、发布或部署；无麦克风/存储访问、无凭据、无任何写路径。

## 生产变更摘要

| 文件 | 变更 |
|---|---|
| `apps/harmony/entry/src/main/ets/AiosApi.ets` | 新增第 10 个只读端点——首个**动态路径** `GET /api/v1/exams/{exam_id}`（刻意不进 `AiosEndpoint` 固定路径枚举，由 `EXAM_PATH_PREFIX` + 校验后拼接）；新增 `ExamSessionData`（`exam_id`/`paper_id`/`paper_title`/`mode`/`status`/`server_started_at`/`server_end_at`/`server_remaining_seconds`/`questions[]`/`answers`/`next_sequence`）、`PublicQuestionOut`（`id`/`type`/`stem`/`options[]`）、`QuestionOptionOut`（`key`/`text`）DTO，snake_case 与 mock `EXAM_SESSION` 及 Android `ExamDtos.kt` 精确对齐；导出 `getExamSession(baseUrl, examId)`——先白名单校验（trim、非空、≤128 字符、拒绝 C0/DEL/C1 控制字符、拒绝 `/ ? #`、仅允许 `[A-Za-z0-9._:-]`，不合法直接返回失败不发起网络请求），再 `encodeURIComponent` 编码拼入路径；仍硬编码仅 GET、单次请求、无重试、无写入、无凭据处理 |
| `apps/harmony/entry/src/main/ets/components/StudyPane.ets` | 论文区（M13-05）行为保持不变；新增完全独立的考试只读区：独立状态机 `ExamSessionStatus` IDLE/LOADING/SUCCESS/ERROR（与论文区 `ListStatus` 无耦合）；独立 `examRequestGeneration` 代次守卫 + `disposed` 守卫（过期考试响应不得覆盖较新考试状态、不得触碰论文区状态）；每次「查询/重试」恰好一次 `getExamSession`，无自动重试循环；成功先做契约形状校验（标量字段类型 / questions 数组逐题逐选项 / answers 对象逐值，不合格一律 fail closed 转错误态）；成功视图渲染公共元数据（exam_id/paper_id/mode/status/server_remaining_seconds/next_sequence）与全部公共题目/选项；`answers` 仅作「学员已保存作答」展示并**明确标注非正确答案**（未作答如实显示「暂无」）；不渲染正确答案/解析，无作答控件/提交按钮/倒计时（server_remaining_seconds 为静态只读快照）；URL 变更（`aios://settings/url_changed`）时论文区照旧刷新、考试区立即失效——考试代次 +1 并清空数据/状态/错误，展示「地址已变更需重新查询」中文提示，不自动重查；布局调整为整页单个纵向 Scroll（论文区在上、考试区在下），论文区成功列表不再嵌套内层 Scroll |

mock 契约与测试切片（已随本地提交 `47ad110` 先行入库，本切片不含）：`tools/android_smoke/mock_contract.py` 新增 `EXAM_SESSION` 确定性 fixture 与 GET-only 路由；`tools/harmony_mock/server.py` 允许清单新增考试端点（其余 exam 路径/方法一律 404）；`tools/harmony_mock/test_contract.py` 42→54 项；`tests/android_smoke` 契约与 loopback 测试同步扩展。

本切片生产改动面 2 files，+699/−80（`AiosApi.ets` +128/−8、`StudyPane.ets` +571/−72，git diff --numstat 本地可验证）；连同 `47ad110` 合计 7 files（2 Harmony 生产 + 2 mock 工具 + 3 测试，不含文档）。

## 验收事实

| 项目 | 结果与依据 |
|---|---|
| 本地测试（mock 契约） | `python tools/harmony_mock/test_contract.py` **54/54 passed**（exit 0；M13-08 起 42→54，含 exam fixture 整包精确相等正例与 11 项 fail-closed 负例：仅 GET、query strings 拒绝、learning-events、未知 exam id、集合路径、POST 建考/PUT 作答/POST 提交/GET submission/report 全 404） |
| 本地测试（Android 回归） | 全量 `pytest tests/android_smoke` **295 passed / 1 skipped**（skip 为既有 Windows symlink 特权测试；共享契约扩展未破坏 Android 侧，含 Android 侧 exam fixture 精确相等、公共投影不变量、fail-closed 负例与 loopback readonly exam session 测试） |
| clean 构建 | `apps/harmony` 所在 PowerShell 进程内 `DEVECO_SDK_HOME=C:\DevEco-Studio\sdk`，`hvigorw.bat clean --no-daemon`（CLEAN_EXIT=0）与 `assembleHap --no-daemon`（ASSEMBLE_EXIT=0，BUILD SUCCESSFUL in 6s970ms）均 exit 0（`.verify/m13-08-stageb-build.cmd`/`.log`，2026-09-09 02:03–02:04）；产物 `entry-default-unsigned.hap` **418261 bytes**，SHA256 `E8E8E4B1C9D9DC85D8212386ACD4F2DF17373FCE47AD0B7FC702BB6047AB6A3A`（本地 stat/sha256 独立复核，python hashlib 与 certutil 双工具一致）；已知非阻塞警告同基线仅三项：无显式 `targetSdkVersion`、`SettingsStore.ets:56` may-throw 静态提示、`No signingConfig found`（未签名） |
| 模拟器正向流（Settings→Study→考试默认查询） | 模拟器 `127.0.0.1:5557` 全新安装启动，**App PID 27738** 全程稳定；supervisor 仅为本次运行启动 `tools/harmony_mock/server.py --host 0.0.0.0 --port 8766`；真实 Settings UI 保存**精确** `http://192.168.8.3:8766/`（`settings-url.json`/`settings-saved.json`，污染输入先清除——`settings-before.json`/`settings-cleared.json`）；不重启 App，学习 Tab 论文区照旧渲染 M13-05 三篇论文（**论文行为保持不变**，`study-after-url2.json`）；考试区输入框缺省 `exam-m13-08-001`，点「查询」加载态（`exam-loading.json`）后成功视图显示：exam_id=exam-m13-08-001、paper_id=paper-001、试卷 `Attention Is All You Need`、mode=exam、status=active、server_remaining_seconds=900、next_sequence=2，两道 mcq 全部渲染（8 个选项 key/text 逐项可见），q1 已保存作答 A **明确标注「学员已保存，非正确答案」**、q2 如实显示未作答（`exam-positive.json`/`exam-positive.jpeg`/`exam-positive-final.jpeg`，滚动视图 `exam-scrolled.json`） |
| 错误与恢复流 | 停止 mock 并确认 8766 端口监听数 0（`exam-before-error.json` 为停服前对照）后，考试区点「重试」：如实进入网络错误态（原始错误 + 「重试」按钮），考试数据清空、面板消失（`exam-error-12s.json`/`exam-error-scrolled.json`/`exam-error.jpeg`）；重启同一 mock（监听实证）后点「重试」→ 完整考试会话视图恢复，字段与正向流逐项一致（`exam-recovered.json`/`exam-recovered.jpeg`）；App PID 保持 27738 |
| URL 变更失效流 | 真实 Settings UI 把地址改为 `http://192.168.8.3:8767/` 并保存（输入/清除/核对过程证据 `url-8767-*.json`/`url-menu.json`/`url-cut.json`，精确值 `url-8767-exact.json`/`url-8767-saved-exact.json`）：已加载的考试会话**立即失效**——旧地址数据清除、状态回 IDLE，并展示中文提示「服务地址已变更，当前考试会话已失效（旧地址数据已清除），请用新地址重新查询。」，不自动重查（`url-change-invalidated.json`/`url-change-invalidated.jpeg`）；再把地址改回 `http://192.168.8.3:8766/`（`restore-*.json` 过程证据）：论文区照旧自动刷新恢复三篇，**考试区保持 IDLE**（无自动重查，语义正确）（`final-study.json`/`final-exam-idle.json`/`final-exam-idle.jpeg`） |
| 收尾清理 | 全部 mock 进程停止后 8766 端口监听数 0（最终 listeners=0） |

## 原始证据清单

以下文件位于 `.verify/m13-08-harmony-exam-readonly/`，**均不入库**（`.verify/` 在 `.gitignore` 中；布局 JSON/截图体积大且含过程性中间态，仅本地留存，本 README 为唯一入库证据）：

- 采信证据（对应上表）：`settings-url.json`、`settings-saved.json`、`study-after-url2.json`、`exam-loading.json`、`exam-positive.json`/`exam-positive.jpeg`/`exam-positive-final.jpeg`、`exam-scrolled.json`、`exam-error-12s.json`/`exam-error.jpeg`、`exam-recovered.json`/`exam-recovered.jpeg`、`url-8767-exact.json`、`url-8767-saved-exact.json`、`url-change-invalidated.json`/`url-change-invalidated.jpeg`、`final-study.json`、`final-exam-idle.json`/`final-exam-idle.jpeg`
- 辅助实证：`home.json`（首启 Home）、`settings-before.json`/`settings-cleared.json`（输入清除过程）、`keyboard-hidden.json`、`exam-loading-uinput.json`（uinput 触发加载态过程）、`exam-before-error.json`（停服前对照）、`exam-error-scrolled.json`、`url-change-settings.json`、`url-8767-saved.json`/`url-8767-input2.json`、`url-clear3.json`、`url-menu.json`、`url-selected.json`、`url-cut.json`、`restore-settings.json`、`restore-menu.json`/`restore-menu2.json`、`restore-input.json`/`restore-input2.json`、`restore-selected2.json`
- 构建复核：`.verify/m13-08-stageb-build.cmd`/`.verify/m13-08-stageb-build.log`（clean 与 assembleHap 双 exit 0）
- `test-prompt.txt` 为任务提示词副本，非验收证据

## 已知边界

- 本验收是本地模拟器 + mock only 口径，不代表 HarmonyOS 真机、真实考试会话、生产后端、生产 DB、任何写链路或生产可用；无任何写路径、无凭据、不访问麦克风/存储，不渲染正确答案/解析、不提供作答/提交/倒计时。
- 仓库状态（文档时点）：分支 `feature/m13-08-harmony-exam-readonly` 本地提交完成，**未 push、未开 PR**；PR/CI/合并状态待后续状态切片回填，不在此预写。CI 无 HarmonyOS job：本切片全部 HarmonyOS 验证为本地口径，不构成远端 CI 验证；后续 PR 的 API/Android/Docker/Web CI 结果不能扩大为 HarmonyOS 远端验证。
- 未使用 AGC key、签名配置或自动签名；未签名 HAP（`No signingConfig found` 为基线已知警告）直装只是本地验收形态，**不构成发布形态，不声称已签名/可发布**；未打 tag、未部署。
- HAP 为 zip 打包产物，同源重建哈希可能不同；本文记录的 SHA256 以当前本地留存、模拟器验收所用产物为准（stat/sha256 双工具复核）。
- `production_ready=false` 语义不变。

## 复现概述

1. 运行本地测试：`python tools/harmony_mock/test_contract.py`（54/54）与全量 `pytest tests/android_smoke`（295 passed / 1 skipped）。
2. 在 `apps/harmony` 所在 PowerShell 进程设置 `DEVECO_SDK_HOME=C:\DevEco-Studio\sdk`，依次执行 `hvigorw.bat clean --no-daemon` 与 `hvigorw.bat assembleHap --no-daemon`（均 exit 0），记录 HAP 字节数与 SHA256（418261 bytes / `E8E8E4B1…`）。
3. `hdc -t 127.0.0.1:5557` 全新卸载重装 HAP 并启动，进学习 Tab，核对论文区默认地址 `http://127.0.0.1:8000` 下真实网络错误态、考试区 IDLE。
4. 启动 `python tools/harmony_mock/server.py --host 0.0.0.0 --port 8766`（仅本地验收使用，不要在生产暴露），经真实 Settings UI（污染输入须先清除）输入/保存精确 `http://192.168.8.3:8766/`，不重启 App 回学习 Tab：核对三篇论文照旧渲染；考试区用缺省 `exam-m13-08-001` 点「查询」，核对元数据/两题八选项/已保存作答「非正确答案」标注/未作答如实展示。
5. 停止 mock 并核对 8766 监听数 0，考试区点「重试」核对错误态与数据清空；重启 mock 后点「重试」核对完整恢复。
6. Settings 改存 `http://192.168.8.3:8767/`：核对考试立即失效 + 中文重查提示、不自动重查；改回 `http://192.168.8.3:8766/`：核对论文自动恢复、考试保持 IDLE。
7. 收尾停止全部 mock 进程并再次核对 8766 端口监听数为 0。
