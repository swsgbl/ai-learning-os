# M13-03 HarmonyOS Home 地址变更自动刷新 — 验收证据归档

- 日期：2026-09-08
- 分支：`feature/m13-03-harmony-home-refresh`（基于 `origin/main@0b125319f8b2ef4f05428d75925e8293c3e8d4a6`，即 PR #56 merge commit，本地 git 可验证）
- 状态：本地实现与本地模拟器验收完成；本 README 记录提交前本地验收快照，远端 PR/CI/合并状态以后续 PROJECT_STATUS 回填为准。
- 原始证据路径：`.verify/m13-03-harmony-home-refresh/`（gitignored，不入库）
- 入库证据：本 README（唯一入库文件）
- 结论：clean 构建、正向流（Settings 保存成功后不重启 App、已挂载 Home 自动重读地址并刷新六区）、负向流（非法保存被拒且不触发事件）、Stage A 干净首屏全部通过

## 生产变更摘要

| 文件 | 变更 |
|---|---|
| `apps/harmony/entry/src/main/ets/components/SettingsStore.ets` | 导入官方 `@kit.BasicServicesKit` `emitter`；集中导出事件 ID `EVENT_ID_URL_CHANGED = 'aios://settings/url_changed'`（订阅方只导入不自持）；`saveBaseUrl` 仅在 UrlPolicy 校验 + preferences `put` + `flush` 全部成功后 `emitter.emit`——非法保存不写盘不发事件；emit 附带 URL payload 仅作观测用途 |
| `apps/harmony/entry/src/main/ets/components/HomePane.ets` | 导入 `emitter` 与 `EVENT_ID_URL_CHANGED`；新增单一稳定回调 `urlChangeListener`（类字段）——回调内以 `loadBaseUrl(context)` 重读持久化为权威来源（不信任事件 payload），更新地址显示并整体刷新六区；`aboutToAppear` 仍只做一次初始 URL 加载 + 先 `off` 再 `on` 订阅 + 一次初始刷新；`aboutToDisappear` 用带回调的 `emitter.off` 精确退订（仅移除本组件订阅，不影响其他订阅者）；回调内对 `hostContext` 显式 undefined/null 检查，无非空断言 |

## 验收事实

| 项目 | 结果与依据 |
|---|---|
| clean 构建 | `hvigorw.bat clean --no-daemon` 与 `hvigorw.bat assembleHap --no-daemon` 均 exit 0 / BUILD SUCCESSFUL |
| HAP | `entry-default-unsigned.hap`，197,338 bytes，SHA256 `C06327CED5973DD5A634E8A974C2DF9C26D6991CBF815F2BE9416BC8C1938960`；已知非阻塞警告：无显式 `targetSdkVersion`、无签名配置、`SettingsStore` may-throw 静态提示（与 M13-01/M13-02 同基线） |
| 正向流 | 本地模拟器 `127.0.0.1:5557`；fresh install 默认 `http://127.0.0.1:8000`，Home 六区真实网络错误态（非 mock 数据）；supervisor 仅为本运行在 `0.0.0.0:8766` 启动 `tools/harmony_mock/server.py`，模拟器侧使用 `http://192.168.8.3:8766/`；真实 Settings UI 经系统文本菜单清除旧地址、输入 mock URL、点击「保存」→ `settings-saved-layout.json` 含 `已保存: http://192.168.8.3:8766/`；**App 不重启**，返回已挂载 Home 后 `home-after-back-layout.json` 显示 `服务地址: http://192.168.8.3:8766/` 且六区刷新为 mock 数据：`status: ok` / `service: aios-mock-android-smoke`、本地模式（认证未开启）、privacy local/local/local/关/关、`0.12.5-mock` / `m1205mockgit` / `m1205mockrev`、ops `postgresql` / papers 34 / search 456 / audit total 1024 / worker running、审计 `#1001 paper.publish`（before/after 载荷不渲染） |
| 负向流 | supervisor 停止其启动的 mock 进程且端口无残留监听；Settings 清除有效地址输入 `javascript:alert` → `settings-invalid-saved-layout.json` 显示 `URL 校验失败 [4]: 缺少 scheme:// (仅允许 http/https)`；**App 不重启**，`home-after-invalid-layout.json` 仍显示先前加载的 mock 版本/运维/审计数据、未刷新为网络错误态——证明被拒保存未写盘、未 emit URL 变更事件、Home 未被无效触发 |
| Stage A 干净首屏 | 中途一份 hmharness 报告误以 Settings 页布局（8,931 bytes）覆盖首采 Home 布局、不能作 Stage A 证据，故从同一 M13-03 HAP 卸载重装后干净重采（`hdc-uninstall-final.log` / `hdc-install-final.log` / `aa-start-final.log` 均 success，最终 PID 见 `home-initial-pid.txt`）；`home-initial-layout.json`（27,715 bytes）首屏含 `AIOS 只读面板`、`服务地址: http://127.0.0.1:8000`、`整体刷新`、`服务健康`、`网络请求失败: [object Object]`、`重试`、`认证状态`、`隐私模式`、`服务版本`；`home-initial-scrolled-layout.json`（28,363 bytes）另含 `运维快照`、`审计日志(最近 100 条)`；两视口合覆盖全部六区，无 mock 数据渲染；配图 `home-initial.jpeg` / `home-initial-scrolled.jpeg`（1320x2856） |

## 原始证据清单

以下文件位于 `.verify/m13-03-harmony-home-refresh/`，均不入库：

- 构建：`hvigor-clean.log`、`hvigor-assembleHap.log`
- 安装/启动（验收主链）：`hdc-install.log`、`aa-start.log`
- 安装/启动（Stage A 干净重装）：`hdc-uninstall-final.log`、`hdc-install-final.log`、`aa-start-final.log`、`home-initial-pid.txt`
- 正向流：`settings-before-save-layout.json`、`settings-url-valid-layout.json`、`settings-url-valid.jpeg`、`settings-saved-layout.json`、`settings-saved.jpeg`、`home-after-back-layout.json`、`home-refreshed.jpeg`、`home-refreshed-scrolled-layout.json`、`home-refreshed-scrolled.jpeg`、`home-audit-layout.json`、`home-audit.jpeg`
- 负向流：`settings-negative-before-layout.json`、`url-negative-menu-layout.json`、`url-invalid-entered-layout.json`、`url-invalid-entered-retry-layout.json`、`settings-invalid-saved-layout.json`、`settings-invalid-saved.jpeg`、`home-after-invalid-layout.json`、`home-after-invalid.jpeg`
- Stage A：`home-initial-layout.json`、`home-initial.jpeg`、`home-initial-scrolled-layout.json`、`home-initial-scrolled.jpeg`
- 过程报告：`REPORT.md`、`SUPERVISOR_FEEDBACK.md`、`SUPERVISOR_FEEDBACK_2.md`、`SUPERVISOR_FEEDBACK_3.md`、`UI_STAGE_A_TASK.md`、`UI_STAGE_A_REPORT.md`、`UI_VERIFICATION_REPORT.md`
- 其余过程性 layout JSON 与 `uitest-*.log` 交互步骤转储（点击/输入/截图接收/滚动等逐步日志）

## 已知边界

- 本验收是本地模拟器 + mock only 口径，不代表真机、真实 provider、生产后端、生产 DB、治理写链路或生产可用。
- 未签名 HAP 直装只是本地验收形态，不构成发布形态；未使用 AGC key、签名配置或自动签名。
- CI 当前无 HarmonyOS job；后续 PR 的 API/Android/Docker/Web 结果不能扩大为 HarmonyOS 远端验证。
- `production_ready=false` 语义不变。

## 复现概述

1. 在 `apps/harmony` 所在 PowerShell 进程设置 `DEVECO_SDK_HOME=C:\DevEco-Studio\sdk`，依次执行 `hvigorw.bat clean --no-daemon` 与 `hvigorw.bat assembleHap --no-daemon`。
2. `hdc -t 127.0.0.1:5557 install entry-default-unsigned.hap`，`aa start` 启动，确认默认地址下 Home 六区真实网络错误态（无 mock）。
3. 启动 `python tools/harmony_mock/server.py --host 0.0.0.0 --port 8766`（仅本地模拟器验收使用，不要在生产暴露），通过真实 Settings UI 经系统文本菜单清除旧地址、输入 `http://192.168.8.3:8766/` 并点击「保存」。
4. 不重启 App，返回 Home，核对 `服务地址` 与六区刷新为 mock 数据；滚动核对运维快照与审计区。
5. 停止 mock 进程并释放端口；在 Settings 输入 `javascript:alert` 保存被拒（URL 校验失败）；返回 Home 确认保留旧数据、未刷新为错误态。
6. （可选 Stage A）卸载后从同一 HAP 重装，核对全新首屏六区真实错误态与滚动视口。
