# M13-02 HarmonyOS Home mock 数据与降级 — 验收证据归档

- 日期：2026-09-07
- 分支：`feature/m13-02-harmony-mock-data`（基线 `main@a88ed80a20bbd2d6961d53f29407f9c59aca859f`）
- 状态：本地实现与本地模拟器验收完成；本 README 记录提交前本地验收快照，远端 PR/CI/合并状态以后续 PROJECT_STATUS 回填为准。
- 原始证据路径：`.verify/m13-02-harmony-mock-data/`（gitignored，不入库）
- 入库证据：本 README 与 `fixtures/mock_responses.json`
- 结论：mock 契约、clean HAP 构建/安装、Settings 真实 UI 连接、Home 六区 mock 渲染、停服降级均通过

## 验收事实

| 项目 | 结果与依据 |
|---|---|
| mock 范围 | `tools/harmony_mock/server.py` 复用 `tools.android_smoke.mock_contract.ReadOnlyMockContract`，仅暴露 M13-01 Home 六个 GET 端点；非 GET、未支持 method（如 HEAD/OPTIONS/FOO）、未知路径、audit 非 `limit=100` 或含多余 query 均返回 404 |
| 安全边界 | 纯 Python 标准库；不触网；不读取密钥；不记录请求 header/body；默认绑定 `127.0.0.1`，模拟器验收显式使用 `--host 0.0.0.0` |
| 契约测试 | 实现时首验：`python tools/harmony_mock/test_contract.py --host 127.0.0.1 --port 18765` 与 `--port 28765` 均 **15/15 passed**；Codex 审查发现未支持的 HEAD/OPTIONS/FOO 曾落入 501，补充三个未支持 method 负例并修复 fail-closed 501 缺口后，最终两端口复验均 **18/18 passed**；`python -m py_compile` 通过 |
| 本地模拟器 | HarmonyOS 模拟器 `127.0.0.1:5557`；App `com.ailearningos.app`；宿主地址 `http://192.168.8.3:8765/` |
| Settings 真实 UI | 通过 Settings 页输入并保存 base URL，点击"测试连接"后 layout 显示 `连接成功: aios-mock-android-smoke` |
| clean 构建 | 在单次 PowerShell 进程内设置 `DEVECO_SDK_HOME=C:\DevEco-Studio\sdk` 后 clean `assembleHap` 通过 |
| HAP | `entry-default-unsigned.hap`，194715 bytes，SHA256 `990906DA88FEC6F769705106BE156FDA33F2CD34576B300A3E566BEBF4A1CA30`；未签名 HAP 通过 `hdc -t 127.0.0.1:5557 install` 安装，`aa start` 启动成功，重启后 App PID `22710` |
| Home mock 渲染 | 服务健康 `status: ok` / `service: aios-mock-android-smoke`；认证为本地模式（认证未开启）；隐私模式 local/local/local/关/关；版本 `0.12.5-mock` / `m1205mockgit` / `m1205mockrev`；运维快照 `postgresql`、papers 34、search 456、audit total 1024、worker 是；审计显示 `#1001 paper.publish` 与 `#1002 worker.tick`，before/after 载荷不渲染 |
| 停服降级 | 精确停止 mock 进程并释放 8765 端口后点击"整体刷新"，六区最终显示 `网络请求失败: [object Object]` 并保留各自"重试"按钮；App PID 保持 `22710` 未崩溃，学习/搜索/语音/设置四 Tab 仍可切换 |

## 原始证据清单

以下文件位于 `.verify/m13-02-harmony-mock-data/`，均不入库：

- `hvigor-clean.log`、`hvigor-assembleHap.log`、`hdc-install.log`、`aa-start.log`
- `settings-saved-layout.json`、`settings-test-connection-layout.json`、`settings-test-connection.jpeg`
- `home-mock-layout.json`、`home-mock.jpeg`、`home-mock-scrolled-layout.json`、`home-mock-scrolled.jpeg`
- `home-audit-layout.json`、`home-audit.jpeg`
- `postinstall-home-layout.json`、`postinstall-home.jpeg`
- `home-degraded-final-layout.json`、`home-degraded-final.jpeg`
- `post-degrade-tabs-layout.json`、`post-degrade-tabs.jpeg`
- `mock-server.stdout.log`

## 已知边界与后续优化

- 本验收是本地模拟器 + mock only 口径，不代表真机、真实 provider、生产后端、生产 DB、治理写链路或生产可用。
- 未签名 HAP 直装只是本地验收形态，不构成发布形态；未使用 AGC key、签名配置或自动签名。
- CI 当前无 HarmonyOS job；后续 PR 的 API/Android/Docker/Web 结果不能扩大为 HarmonyOS 远端验证。
- `HomePane` 目前仅在 `aboutToAppear` 读取一次 base URL；Settings 保存后已挂载的 Home 不会自动刷新地址。本切片按当前设计重启 App 完成验证，不改生产源码；该生命周期问题列为后续独立优化项。
- `production_ready=false` 语义不变。

## 复现概述

1. 在仓库根目录分别用两个端口运行契约测试，确认 18/18 passed（含 HEAD/OPTIONS/FOO 未支持 method 404 负例）。
2. 在 `apps/harmony` 所在 PowerShell 进程设置 `DEVECO_SDK_HOME`，执行 clean `assembleHap`。
3. 启动 `python tools/harmony_mock/server.py --host 0.0.0.0 --port 8765`，用真实 Settings UI 保存宿主地址并测试连接。
4. 重启 App，核对 Home 六区 mock 数据与审计区滚动可见性。
5. 停止 mock 监听进程，点击"整体刷新"，核对六区错误态、重试按钮、进程存活与四 Tab 切换。
