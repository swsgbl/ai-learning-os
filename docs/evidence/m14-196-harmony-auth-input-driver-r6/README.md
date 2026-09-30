# M14-196：Harmony 认证冒烟输入驱动修复（R6，真实冒烟收尾段）

## 结论

| 项 | 结果 |
|---|---|
| 分支 / 基线 | `harmony/m14-196-input-driver`，HEAD `fc3df0c`（全段未变，未 pull/rebase） |
| 代码修复 | `tools/harmony_release/auth_smoke.py`：Settings URL 替换改为 longClick 菜单 → 全选 → 剪切 → 布局证明已清空 → 聚焦后 `uitest uiInput text`（非坐标 `inputText`）→ 保存前**精确等值**校验；一次不匹配即 fail-closed 返回（不再默认五连试） |
| 聚焦测试 | `pytest tests/harmony_release/test_auth_smoke.py -q` **73 passed**（含锁定坐标 `inputText` 禁用、菜单/动作选择、精确等值、一次失配即失败等新契约）。出处（更正）：73 passed 来自实现段 / fail-closed 收敛轮（hmharness 线程 d249629c）对最终代码态的验证，非本收尾段；仓内日志链为止于 71 passed（`supervisor-round1/pytest_focused.log`，此前 closeout 轮为 70 passed） |
| 全量 Harmony 发布套件 | `pytest tests/harmony_release -q`：**739 passed, 1 skipped**（证据 `debug/stage-a/pytest_harmony_release.log`，实现段产物）。聚焦 73 与全量 739+1skip 均出自实现/fail-closed 静态验证证据，并非本真实冒烟收尾段（本段唯一一次 execute 已失败，见下） |
| Ruff 对照 | 对 `fc3df0c` 基线：216 → 216，**0 新增**；`git diff --check` 通过；`compileall` 通过 |
| 真实冒烟（计划） | plan-only 一次：exit 0，`status: plan`，后端未启动 |
| 真实冒烟（执行） | `--execute --confirm-mutation` 一次：**失败于 `settings_ui`**（`settings_foreground_lost` ×5，首败为 attempt 1 stage=menu）；按任务规则立即停止，未重试、未扩大范围 |
| 卸载/清理 | uninstall 步骤 ok；`bm dump -n com.ailearningos.app` 报 not-exist；后端进程退出、端口无 LISTENING（仅 TIME_WAIT 尾巴） |
| HAP | 仅复制不重建：M14-195 产物 SHA256 `609F058AA49AA376C7353BA410085412A5D0B1817E486209AE09680FEACBCAD4`（源=目的=任务锚点，271774 字节） |
| 提交 | **阻塞**：唯一一次真实 execute 失败于 `settings_ui`（`settings_foreground_lost`×5），按任务规则本段未重试、未改代码；未提交、未推送，等待新的授权修复轮后再议 |

## 执行阶段表（唯一一次 execute，12 步）

| # | 步骤 | 状态 | 说明 |
|---|---|---|---|
| 1 | host_auth_contract | ok | 任务自有后端（OS 分配回环端口） |
| 2 | install | ok | 上述签名 HAP，目标 `127.0.0.1:5555` |
| 3 | start | ok | EntryAbility 前台 |
| 4 | settings_ui | **failure** | `settings_foreground_lost`×5；首败 attempt 1、stage=menu |
| 5-11 | auth_off_local … background | not_run | previous_step_failed / auth 阶段跳过 |
| 12 | uninstall | ok | 已发生 mutation，清理执行 |

汇总：ok 4 / failure 1 / not_run 7。

失败现场证据（保留在 gitignored `.verify/m14-196-harmony-auth-input-driver-r6/formal/execute/`）：
执行期 layout dump（79,682 字节）同时包含 Settings URL `TextInput` 与系统文本菜单"全选"入口 —— 菜单确实弹出，但驱动的前台窗口守卫在菜单浮层抢占焦点后判定 Settings 失去前台，五次尝试同因失败。该守卫与菜单浮层的交互是下一轮修复的首选候选根因；本段按规则未改代码、未二次运行。

## 默认 base 冷启动匿名只读 GET（公开网络例外）

默认 base 为 `https://ndtool.cn/aios/`（M14-191）。**口径更正（R7）：本段没有网络捕获，无法证明"恰好 6 个"——只能证明静态上界。** 冷启动（默认停在"主页"Tab）HomePane `aboutToAppear → refreshAll` 最多发出 6 个匿名只读 GET：

1. `GET /health`
2. `GET /api/v1/auth/status`
3. `GET /api/v1/system/privacy`
4. `GET /api/v1/version`
5. `GET /api/v1/system/ops-snapshot`
6. `GET /api/v1/audit?limit=100`

此外首次打开"设置"Tab 时 AuthPane 挂载会再查询 1 个 `GET /api/v1/auth/status`（M14-89 已观测），故**静态上界为 7 个 GET**（HomePane 6 + AuthPane 1）。两条诚实性警告：(a) 本次安装是替换安装（replace），设备本地可能残留先前保存的本地 base URL——若残留存在，HomePane/AuthPane 会改用本地 URL，真实公网 GET 可能为 **0**；设备侧preferences 目录不可读，无法证伪；(b) 应用侧无请求日志（AiosApi.ets 无 hilog），`hilog -x` 中无 `ndtool` 行，本计数来自静态调用路径 + UI 状态观测，不是网络抓包。**没有网络捕获时，本口径只能写"上界/不确定"，不得声称精确值。**

依据：`HomePane.ets`（refreshAll 顺序调用六个只读 getter）、`AiosApi.ets`（AiosEndpoint 枚举）；与 M14-195 证据 §5.1/§8（health/version 200，privacy/ops/audit 未认证 401，全部为只读 GET）及 M14-191 README（默认 base 与 Settings 持久化契约）一致。

Tabs 声明（源码核实，定稿表述）：`Index.ets` 以静态 `TabContent` 声明 **6 个 Tab**（首页/学习/搜索/语音/设置/治理；下载面板 DownloadPane 挂在"设置"Tab 内，不是独立 Tab）。本证据只证明：冷启动期间 HomePane `aboutToAppear → refreshAll` 发起上列 6 个只读 GET（HomePane `aboutToAppear` 是其确定来源）。其余 TabContent 子 Pane（StudyPane/SearchPane/VoicePane/SettingsPane/AuthPane/GovernancePane/DownloadPane）的构建时机与请求发起时机**未由本证据证明**——本文不对其做任何 lazy/eager 或"不发请求"的断言。特别说明：VoicePane 源码在 `aboutToAppear` 中调用 `loadProviders()`（`VoicePane.ets`），任何情况下都不得写成"语音 Pane 不发任何请求"。

## 输入方法修复要点（对照 R5 根因）

R5 证明坐标式 `uitest uiInput inputText` 可在 Settings TextInput 残留 `/http://10.0.2.2:52034/`，应用正确拒绝协议 `/http`，驱动随即等待不可能出现的确认。R6 修复链：

1. longClick 打开系统文本菜单（每阶段以 layout 证据证明）；
2. 全选 → 剪切，并以 fresh dumpLayout 证明字段已空；
3. 重新聚焦字段，用 `uitest uiInput text`（非坐标）输入 URL；
4. 保存前校验字段文本与期望 URL **精确相等**（非子串）；
5. 任一阶段不可证明即 fail-closed：记录一次失配后立即返回，不再默认五连试（supervisor round-1 修正 1）。

## 证据（gitignored `.verify/m14-196-harmony-auth-input-driver-r6/`）

- `formal/`：本收尾段正式证据 —— HAP 溯源（`hap_provenance.md`、`hap_sha256.txt`）、只读预检（`preflight_readonly.txt`）、plan（`plan/`：command/stdout/stderr/exit/JSON）、execute（`execute/`：command/stdout/stderr/exit/duration/launcher JSON/smoke JSON/后端日志与 DB/清理证明/执行期 layout dump）、本索引 `README.md`。
- `debug/`：实现段与收尾段的调试脚本（patch_*.py、早期 pytest/ruff 日志、stage-a、PowerShell 运行器）；其中 `stage-a/pytest_harmony_release.log` 为全量套件 739 passed, 1 skipped 的出处。
- `closeout/`、`supervisor-round1/`：静态卫生轮证据（聚焦 pytest 日志演进：closeout 70 passed → supervisor 轮 71 passed，两份日志均原样保留；Ruff 0 新增、diff-check、compileall）。最终聚焦 73 passed 出自实现/fail-closed 验证线程（d249629c），不在本目录日志内。
- 截图限制（如实记录）：驱动只记录 layout dump；execute 的 uninstall 清理在其后到达前已移除应用，补截图需二次安装（第二次 mutation），为任务所禁止 —— Settings 状态以执行期 layout dump + JSON 为准。

## 真实性边界

- 全程未重启模拟器、CC Switch、代理、Hermes、CodexHost 或无关进程；只清理本任务应用/后端/端口。
- 唯一 Harmony 目标 `127.0.0.1:5555`；公开网络口径：无网络捕获，无法给出精确计数；源码/UI 推导的静态上界为 **7 个匿名只读 GET**（HomePane 6 + AuthPane 首开"设置"Tab 时 `/api/v1/auth/status` 1），若替换安装残留先前本地 base URL，真实公网 GET 可能为 **0**（详见上文"默认 base 冷启动匿名只读 GET"节）。
- 证据不含任何 token/密钥/口令；绝对私有路径仅保留在 gitignored 原始日志中，不入 tracked 文档。
- 本段未新增/修改任何已跟踪代码；唯一 tracked 新增为本 README。
- 测试数字出处声明：结论表中聚焦 73 passed 与全量 739 passed + 1 skipped 均为**静态实现/fail-closed 验证证据**，不是真实冒烟结果；本收尾段唯一一次真实 execute 失败于 `settings_ui`，未产生任何通过的动态冒烟结果，因此 commit/push 保持阻塞，等待新的授权修复轮。
