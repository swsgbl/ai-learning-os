# M14-196H R7：Settings 前台判定修复 + 证据口径更正

## 背景与根因

R6 Stage B 真实冒烟失败于 `settings_ui`（`settings_foreground_lost` ×5，首败
attempt 1 stage=menu）。R7 复现根因：读取
`.verify/m14-196-harmony-auth-input-driver-r6/stage-b/last_layout_dump.json`
（82645 字节，SHA256 `FB42C6DFEC124D6441D3B5A1132FBE0C95368DA1D22ED9CF0AA442E444A5E7D3`）：

- 我们的 Settings pane **本体完整渲染**：`AIOS 服务地址` 标题、隐私提示、
  预填默认 URL 的 `TextInput`（`https://ndtool.cn/aios/`）、保存/测试连接按钮；
- 华为 IME 的 `SelectMenu`（剪切/复制/全选/自动填充/翻译）覆盖在该输入框上
  ——longClick 清空路径打开的就是它；
- softKeyboard/IME 窗口覆盖屏幕下部（`com.huawei.hmos.inputmethod` 窗口
  `[0,1729][1320,2856]`），**底部 tab bar（设置/首页）在 dump 中完全不可见**；
- R6 的 `_owns_layout`（要求 设置/首页 tab 文本）因此把我们自己的 pane 误判
  为外来前台 —— 5 次尝试全部同因失败。这是驱动判定缺陷，不是应用缺陷。

实证（`auth_smoke._owns_layout` 对该真实 dump = False；新判定 = True）。

## 代码修复（tools/harmony_release/auth_smoke.py）

新增三个纯函数（不触碰、不启动任何东西）：

1. `_has_settings_strong_evidence(texts, typed)`：强 Settings 证据**组合**判定
   ——必须同时有 (a) pane 标题 `AIOS 服务地址`、(b) 至少一个真实 `TextInput`
   节点、(c) 至少一个 Settings 动作按钮（保存 或 测试连接）。单一弱文本
   （只有"全选"、只有标题）永不放行。
2. `_owns_settings_pane(texts, typed)`：Settings URL 流程的 pane/input/verify/
   confirm 阶段 ownership——tab-bar 标记可见时照旧优先；tab bar 被 IME 遮挡时
   （R6 stage-b 现实）强组合可证明归属。**初始 Settings ownership 因此也支持
   没有底部 tab 的 Settings pane**（任务目标 3）。
3. `_owns_settings_menu_stage(texts, typed)`：仅 menu 阶段——在强组合之上额外
   要求可见 IME 菜单动作（全选 或 剪切），外来屏幕仅提及标题不可能通过。

调用点替换（4 处）：`_clear_url_field_via_menu` 的 menu 阶段检查、
`_drive_settings_url` 的 step-2 初始检查、verify 阶段、confirm 阶段。
外来应用、空布局、缺强证据仍 fail-closed（`_owns_layout` 本身未动，其他
pane 的 ownership 语义不变）。

## 测试（tests/harmony_release/test_auth_smoke.py）

新增 6 个 R7 回归测试（含 Stage B real-layout regression，任务目标 4）：

- `test_r7_real_dump_huawei_ime_selectmenu_ownership`：直接加载真实 dump
  fixture（已复制到 `tests/harmony_release/fixtures/
  m14_196_r6_ime_selectmenu_layout.json`，字节一致 FB42…E7D3）——断言旧判定
  False（根因在案）+ 新 menu/pane 判定 True；证据目录缺失时 skip（CI 安全）。
- `test_r7_foreign_app_layout_fails_closed`：外来应用屏幕四级判定全 False。
- `test_r7_weak_single_text_never_passes`：仅标题/仅全选/标题+全选但无
  TextInput——全不放行。
- `test_r7_real_settings_pane_without_tab_bar_passes`：无 tab bar 的真实
  pane 形态（标题+TextInput+保存+测试连接）通过 pane 判定。
- `test_r7_initial_ownership_uses_strong_evidence`：流程级——整条
  `_drive_settings_url` 在无 tab bar 的 pane dump 序列上完整走通
  （longClick→全选→剪切→聚焦输入→精确校验→保存确认）。
- `test_r7_menu_stage_requires_menu_action`：无菜单动作时 pane 判定通过但
  menu 阶段判定不放行。

## 证据口径更正（docs/evidence/m14-196-harmony-auth-input-driver-r6/README.md）

R6 README 曾声称冷启动"恰好 6 个"匿名只读 GET——**虚假精确，已更正**：

- 现表述：**静态上界 7 个 GET**（HomePane refreshAll 6 + AuthPane 首开
  设置 Tab 时的 auth/status 1）；
- 替换安装可能保留旧本地 base URL → 真实公网 GET 可能为 **0**；
- 无网络捕获（app 无请求日志、hilog 无 ndtool 行）→ 只能写"上界/不确定"，
  不得声称精确值。
- Stage B README 本就写的是 "7 GETs, upper bound"（诚实），无需改。

同轮 docs 跟进（supervisor 审查发现的同源缺陷，一并更正 R6 README）：

- 删除"Tabs 为惰性（lazy）行为，非 eager"断言——冷启动仅观测到 6 个 Home GET
  不能证明其余 TabContent 的构建时机；
- 删除"语音 VoicePane 为只读占位、不发任何请求"——与源码矛盾：`VoicePane.ets`
  在 `aboutToAppear` 中调用 `loadProviders()`；
- 现表述仅保留可证事实：`Index.ets` 静态声明 6 个 TabContent；DownloadPane
  挂在"设置"Tab 内；HomePane `aboutToAppear → refreshAll` 是 6 个只读 GET
  的来源；其余 Pane 的构建与请求时机未由本证据证明。

## 验证（本轮实际执行，静态范围）

| 命令 | 结果 |
|---|---|
| `pytest tests/harmony_release/test_auth_smoke.py -q` | **79 passed**（R6 时 73） |
| `pytest tests/harmony_release -q`（完整 Harmony release Python suite） | **745 passed, 1 skipped**（R6 时 739+1skip） |
| Ruff 对照基线 fc3df0c（F,E9,W605，code 级规范化） | 0 → 0，**0 新增**（全仓存量 4 项均为非本任务文件的既有 F401/F541；本任务两文件 ruff 全过） |
| `git diff --check` | 通过（无空白错误） |
| HAP 完整性 | 未重建/签名/修改；SHA256 `609F058AA49AA376C7353BA410085412A5D0B1817E486209AE09680FEACBCAD4`、271774 字节（复核通过） |

（上表中的全量套件/Ruff/diff-check 命令输出保存于
`.verify/m14-196-harmony-auth-input-driver-r7/`。）

## 约束遵守

- 未运行 `--confirm-mutation` 或任何 Stage B corrected smoke；本轮纯静态
  实现与测试。
- 未重建/签名/修改 entry-default-signed.hap（指纹复核一致）。
- 未启动/停止模拟器或 mock 后端；未触碰网络采样/VPS/生产服务。
- 未输出任何 secret；未合并/未推送；本地共两个 commit（实现 d106dae7793 + 本文档更正），均未推送；工作树保留供 Supervisor 复核。
