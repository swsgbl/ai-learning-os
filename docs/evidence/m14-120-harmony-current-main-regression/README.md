# M14-120：current-main 模拟器回归 —— 执行状态（诚实部分完成）

- 工作树：`ai-learning-os-worktrees/m14-120-harmony-current-main-regression`
- 分支：`harmony/m14-120-current-main-regression`（基于 `origin/main` = `2b1c2dd`，
  "Merge pull request #206 from swsgbl/ops/m14-119-monitor-alert-dispatch"）
- 执行者：实现 worker（hmh）；监督：Codex
- 日期：2026-09-18（本地）

## 结论一句话

模拟器门禁（步骤 2）**已真实通过**；构建（步骤 3）与 auth/login 回归（步骤 4）
**not_run** —— worker 回合预算在动手构建前耗尽，**未声称任何回归结论**。
本文件是状态/诊断工件，不是回归通过证明。

## 步骤 2：模拟器门禁核验（已执行，通过）

初始状态（三角验证，2026-09-18）：

| 检查 | 命令 | 结果 |
|---|---|---|
| hdc 目标列表 | `hdc list targets` | 仅 `127.0.0.1:15566`（经 `param get` 鉴别 = Kaihong BotBook, KaihongOS 5.0.2.57, API 14 —— **非本任务目标**） |
| 模拟器进程 | `tasklist \| findstr /i "Emulator.exe qemu"` | 仅 `qemu-system-x86_64.exe`（BotBook 的 QEMU）；无 Emulator.exe |
| 5555 监听 | `netstat -ano \| findstr ":5555"` | 空 —— **`127.0.0.1:5555` 当时未连接**（Pura 90 处于 stopped） |

处置：启动（非 stop/restart，硬边界允许）Pura 90 模拟器后门禁通过：

| 检查 | 命令 | 结果 |
|---|---|---|
| 启动 | `harmony_emulator_start Pura 90` | started (pid 52340)，新目标 `127.0.0.1:5555` |
| 真实 boot | `hdc -t 127.0.0.1:5555 shell "param get bootevent.boot.completed"` | `true` |
| 身份/版本 | `hdc -t 127.0.0.1:5555 shell "param get const.product.model && param get const.product.name && param get const.ohos.apiversion && param get const.product.software.version"` | `emulator` / `emulator` / `24` / `emulator 6.1.0.117(SP37DEVC00E115R4P11)` |

模拟器保持运行（任务边界：不得 stop/restart）。

## 步骤 3–6：not_run 清单（诚实申报）

| 步骤 | 状态 | 原因 |
|---|---|---|
| 3. current-main 构建 unsigned HAP | not_run | worker 上下文/回合预算耗尽，未执行 `release_build.py`；无 HAP SHA256/size 可报 |
| 4. auth/login 回归（`auth_smoke_launcher.py --execute --confirm-mutation` 对一次性 loopback 后端） | not_run | 同上；无 layout/PID/请求/清理证据可报 |
| 5. 状态/roadmap/changelog 最小更新 | not_run | 回归未跑，无可登记的回归结论 |
| 6. 聚焦测试 / py_compile / ruff / `git diff --check` / 提交 | 部分完成 | 仅提交本状态工件；聚焦 pytest 未跑 |

## 已知入口（供续跑者）

- 构建链：`tools/harmony_release/release_build.py`（无签名材料边界）
- 回归 launcher：`tools/harmony_release/auth_smoke_launcher.py`
  （`--execute --confirm-mutation --target 127.0.0.1:5555 --hap <unsigned-hap>`，
  其一次性后端 = `AuthSmokeServer`，loopback + 隔离 SQLite + 合成用户）
- 原始证据落 `.verify/artifacts/`（gitignored），仅本 README 入库。

## 边界声明

- 未触碰任何生产容器/DB/MinIO/语音/密钥；未使用签名材料（本次根本未构建/未签名）。
- 未改动 Android 设备；未 stop/restart 模拟器（仅执行了一次启动使其可用）。
- 未 reset/revert 任何无关变更；主 checkout 与其他工作树未被编辑。
- git fetch（经 VPN）删除了 3 个远端已合并的 `origin/ops/*` 陈旧引用（fetch --prune 正常行为，非本任务改动）。
