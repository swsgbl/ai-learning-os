# M14-196H R10/R11：menu 阶段 tab-only 捷径移除 + fixture 强化收口

## 背景与根因

R7 引入的 `_owns_settings_menu_stage` 在强组合判定之外保留了一条
tab-only 捷径：`if _owns_layout(texts): return True`。这意味着一个
**仅显示 设置/首页 tab 文本**、既无 AIOS Settings 强证据、也无 IME
菜单动作的 caret-menu dump 会被直接放行为"我们自己的前台"。该捷径与
menu 阶段"必须同时看到强 Settings 证据 + 可见菜单动作"的设计意图
相悖（R7 README 自己写明"外来屏幕仅提及标题不可能通过"，但 tab-only
捷径绕过了这一保证）。R10 删除该捷径；R11 强化既有 fixtures 并补齐
回归覆盖。

## 代码修复（tools/harmony_release/auth_smoke.py，R10）

`_owns_settings_menu_stage` 移除 `_owns_layout` tab-only 捷径：menu
阶段现在**必须**满足强 AIOS Settings 证据（`_has_settings_strong_evidence`：
pane 标题 + 真实 TextInput + 动作按钮）**加上**可见 IME 菜单动作
（全选 或 剪切）。tab bar 可见本身不再足以放行 menu 阶段；
`_owns_layout` 与 pane/input 阶段语义不变，外来屏幕保持 fail-closed。
附带将三个 ownership 纯函数的注解现代化为 PEP 585
（`Sequence[Tuple[...]]` → `Sequence[tuple[...]]`）。

## 测试（tests/harmony_release/test_auth_smoke.py，R11）

- **旧 fixtures 强化**：menu 阶段的 fake dumps（happy path、
  fails-closed-when-field-not-empty、full-flow、nonforeground-clear）原先
  只有 tab 文本，现在都携带强 pane 证据（`AIOS 服务地址` 标题 + 保存
  按钮 + URL TextInput）与菜单动作，真实反映 R10 规则下的合法 dump
  形态；`fpc_without_select_all` 断言的 `node_text_count` 相应 1 → 5。
- **新增三个回归**：
  1. `test_r10_menu_stage_tab_only_layout_without_strong_evidence`
     （tab-only 单元级）：仅 tab 文本的布局 `_owns_layout` 为 True、
     但强证据缺失、`_owns_settings_menu_stage` 为 False——被删捷径
     曾放行正是这种形态；
  2. `test_r11_menu_stage_tab_only_now_foreground_lost`
     （tab-only 流程级）：caret-menu dump 持续 tab-only 时，每次尝试
     均在 stage=menu 失败 `settings_foreground_lost`（而非抵达
     `select_all_not_found`），仅 longClick、无输入、无 restart；
  3. `test_r11_select_all_not_found_branch_reachable`
     （可达性单元级）：强 pane 证据 + 剪切可见但无 全选 的布局通过
     menu 阶段判定、随后精确匹配 全选 失败——该分支不被 R10 误伤。

## 验证（Supervisor 验证产物，.verify/m14-196-harmony-auth-input-driver-r11/）

| 命令 | 结果 |
|---|---|
| `pytest tests/harmony_release/test_auth_smoke.py -q` | **82 passed**（R7 时 79） |
| `pytest tests/harmony_release -q`（完整 Harmony release Python suite） | **748 passed, 1 skipped**（R7 时 745+1skip） |
| Ruff **default 统计**对照 canonical main，同一对文件（auth_smoke.py + test_auth_smoke.py） | 两边均 **216 total**（UP006×143 / UP045×61 / UP035×7 / I001×2 / SIM102×1 / UP007×1 / UP037×1），逐 code 完全一致，**0 新增、0 减少** |
| `git diff --check` | 通过（无空白错误） |
| HAP 完整性 | 未重建/签名/修改；SHA256 `609F058AA49AA376C7353BA410085412A5D0B1817E486209AE09680FEACBCAD4`、271774 字节（复核通过） |

## 约束遵守

- **更正后的 Stage B 冒烟未运行**——保持独立的受控步骤，本轮仅静态
  实现 + 测试 + 证据收口；
- 未重建/签名/修改 entry-default-signed.hap（指纹如上，未触碰）；
- 未启动模拟器/设备/网络/Docker/服务命令；
- 未输出 secret；未推送；本 commit 之后工作树应恢复干净，供 Supervisor 复核。

---

# M14-196H R13：更正后 Stage B 冒烟证据收口（docs-only）

本节由 R12 更正后 Stage B 受控复跑的 raw evidence（gitignored 目录
`.verify/m14-196-harmony-auth-input-driver-r12/corrected-stage-b/`，含
EVIDENCE.md 与 launcher/smoke/layout JSON artifacts）整理而成。本轮**未**
重跑 auth_smoke / auth_smoke_launcher，未重建/重签/修改 HAP，未启停模拟器。

## 授权变更运行（仅一次）

- auth_smoke_launcher 以 `--execute --confirm-mutation` 执行**恰好一次**：
  无重试、无第二次变更运行、无范围扩大；
- launcher 退出码 **0**，stderr 为空，运行时长约 **147.4s**（约 2 分 27 秒）。

## 阶段汇总与 M14-89 根因闭环

- 阶段汇总：**12 total / 11 ok / 1 not_run（预期跳过）/ 0 failure**；
  唯一 not_run 为 `auth_off_local`，reason=`auth_phase_skip`
  （`--expect-auth on` 下的设计内跳过）；
- **`settings_ui=ok` 与 `wrong_password=ok` 在同一次运行中通过**：R12
  失败的 settings_ui 修复后通过，历史 flaky 的 wrong_password 同轮通过，
  正式关闭 M14-89 记录的 stale-layout（远端旧布局文件被复用）+
  blind-swipe（`_fill_login_form` 盲滑）根因——修复（每次 dump 前删除
  远端布局文件；QUERY_FAILED 时点击 重试 而非盲滑）在本轮被证实有效，
  各阶段布局 sha256/size/节点文本数彼此独立且自洽。

## 契约与工件指纹

- 宿主侧 auth contract **7/7 matched**（auth_status；gated_privacy
  401/带 token 200/错 token 401；wrong_password_401；login_200_token；
  me_200_with_token）；`request_failures`、`toolchain_failures`、
  `warnings` 均为空；
- HAP 指纹不变：SHA256
  `609F058AA49AA376C7353BA410085412A5D0B1817E486209AE09680FEACBCAD4`，
  **271774** 字节（本轮未触碰）；
- 最终拉取的设备布局 `auth_smoke_layout.json`：**62447 字节**，SHA256
  `31AB311FD1ED4B6E3B159A9697897323581A2424445C52B7D0D3F8ECAD2BF4E2`，
  与 smoke JSON 中 post_logout_home 阶段元数据（size/sha256/节点数）
  完全一致。

## 清理证明（运行后只读探针）

- bundle 已卸载（bm dump 报未安装）；设备无 ailearningos 进程；
- 后端进程 PID 已消亡，端口 61200 无 LISTENING（仅 OS 级 TIME_WAIT
  残留，自行衰减）；模拟器仍在线（目标在列、boot completed）；
  git 工作树干净。

## 诚实边界

- 仅回环模拟器目标（127.0.0.1:5555），无真机；签名验证为文件名级
  （`signedness_verified: false`），未走 release/AGC 签名链路；
- 布局内容未逐阶段归档（`content_recorded: false`），仅保留各阶段
  sha256/size/节点数元数据与最终一次布局拉取；
- auth-off 分支本轮未覆盖（`auth_off_local` 按设计跳过）；
- 冷启动仅触及已文档化的匿名只读公开 GET 上限 7（Settings URL 切换前），
  未新增任何网络探测。
