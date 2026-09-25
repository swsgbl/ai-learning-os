# M14-126：Harmony current-main 模拟器回归（verification / docs-only）

## 结论（先说结果）

| 项 | 结果 |
|---|---|
| 基点 | `main` = `308b2bcee9c196356b8ad3505d64871e18b0bacd`（`docs(m14-124): record production cutover evidence (#213)`），worktree `m14-126-harmony-current-main-regression`，分支 `harmony/m14-126-current-main-regression` |
| Release 构建 | **通过**：unsigned HAP 220,008 字节，SHA256 `9EFC6F06CEBCD46374538DD5B7CD155CF21A53FFB5415AB78B457B0DAEF94172`（`tools/harmony_release/release_build.py --quiet`，exit 0，clean+assemble 均 BUILD SUCCESSFUL） |
| Auth smoke（attempt 1，真实 execute/mutation） | **失败（fail-closed）**：12 stage 中 3 ok / 1 failure / 8 not_run。host_auth_contract、install、uninstall 均 ok；`start` 因 `focus_window_unreadable`（两次 attempt）失败，后续 8 个 foreground stage 按设计 not_run；`uninstall` 清理成功，无残留 |
| Auth smoke（attempt 2） | **未执行**：步骤 2 的只读复核确认 `127.0.0.1:5555` 当前**不可达**（见下），按任务规则"目标不可达则最终 blocked，不消耗第三次"，不重跑 |
| 聚焦 pytest | **通过**：`524 passed, 1 skipped in 21.29s`（canonical 主仓 `.venv` Python 3.11.15，worktree 根执行 `tests/harmony_release`，零网络） |
| 最终边界 | **设备链路 BLOCKED（外部硬件）**：attempt 1 已证明 install/uninstall 在 attempt 1 时刻真实发生；但复跑时模拟器/hdc 目标已不可达，按任务"模拟器不可用即如实 blocked，不得伪造通过"，**本切片不得宣称设备链路 PASS** |
| 生产边界 | 未触碰生产 API/DB/MinIO/语音/secrets/容器；未执行 docker compose；无 token/key/password 值被读取或输出（launcher JSON 中密码为 `[REDACTED]`，`auth_secret: "<configured>"` 为占位）；一次性 loopback 后端（58937，PID 45644）由 launcher finally 停止，已确认结束 |
| git 边界 | 本 evidence 目录（tracked）+ 本地 commit，不 push、不开 PR；`.verify/`（gitignored）保存全部原始 JSON/布局/构建证据 |

## 步骤 1：模拟器目标只读复核（两次，无启动/停止）

- **initial（attempt 1 前，2026-09-25 会话早期）**：`hdc list targets` 为空、`netstat -ano | findstr ":5555"` 无监听、`harmony_devices` 工具亦报告 no device。当时判断"目标可能已掉线"，但**仍按任务硬边界尝试执行 auth smoke launcher 真实 execute/mutation 路径**——结果：host 契约 7/7 matched、install ok、uninstall ok，证明目标在 attempt 1 窗口内**真实可达**（"netstat 无监听但 hdc install 成功"的矛盾，归因于 hdc loopback 目标在 emulator 运行期才注册、netstat 只反映普通 TCP listener，不代表 hdc 通道状态）。
- **recheck（attempt 2 前置，2026-09-25）**：
  - `hdc -t 127.0.0.1:5555 shell "param get bootevent.boot.completed"` → `[Fail]Not match target founded`
  - `hdc list targets` → `[Empty]`；`netstat -ano | findstr ":5555"` → 无 listener
  - `harmony_devices`（工具）→ No devices connected
  - `hdc tconn 127.0.0.1:5555` → `[Fail]Connect failed`（**仅尝试重新注册已存在目标，非启动模拟器；未启动/停止任何 emulator 进程**）
  - 结论：**`127.0.0.1:5555` 当前不可达**，attempt 2 依规则不消耗。
- 两次判断的矛盾（可达 vs 不可达）是**时间维度上的状态漂移**：attempt 1 窗口内目标在线；到 attempt 2 复核时目标已离线。两条证据都如实保留：`.verify/m14-126-.../auth_smoke_on.json`（install ok）与 `.verify/.../hdc-recheck-5555.txt`（recheck 六条证据）。

## 步骤 2：current-main release 构建（通过）

- 命令（既有工具，未发明新流程）：`python tools/harmony_release/release_build.py --quiet`
  （内部链路：`hvigorw.bat clean --no-daemon` + `hvigorw.bat assembleHap --mode module -p product=default -p buildMode=release --no-daemon`，cwd=`apps/harmony`）
- 结果：exit 0，clean/assemble 均 BUILD SUCCESSFUL
- 工件：`apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap`
  - 尺寸 **220,008 字节**
  - SHA256 **`9EFC6F06CEBCD46374538DD5B7CD155CF21A53FFB5415AB78B457B0DAEF94172`**（release_build.json 与独立 `certutil -hashfile` 复核一致）
- unsigned 边界：`--require-materials` 未启用，build-profile.json5 `signingConfigs: []`，无签名材料参与

## 步骤 3：auth smoke（attempt 1 已保留；attempt 2 因目标不可达 blocked）

- 命令（既有 launcher，真实 execute/mutation 路径，一次性 loopback 后端）：
  `python tools/harmony_release/auth_smoke_launcher.py --repo-root . --execute --confirm-mutation --target 127.0.0.1:5555 --hap apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap --evidence-dir .verify/m14-126-harmony-current-main-regression`
- 一次性后端：`http://127.0.0.1:58937`（OS 分配端口），PID 45644，合成用户 `aiosstudent`，隔离 workdir；launcher finally 已干净停机（本会话步骤 1 复核：`tasklist /FI "PID eq 45644"` 无匹配，`netstat` 仅剩 TIME_WAIT 残留）
- 主机侧 auth 契约：**7/7 matched**（auth_status / gated_privacy_401 / wrong_password_401 / login_200_token / me_200_with_token / gated_privacy_with_token / gated_privacy_wrong_token）
- 12 stage 结果（**attempt 1，已完整保留**）：

| stage | phase | status |
|---|---|---|
| host_auth_contract | read_only | ok |
| install | mutation | ok |
| start | foreground | **failure**（`focus_window_unreadable` × 2） |
| settings_ui / home_401_unauth / wrong_password / login_and_refresh / cold_restart / logout / background | foreground/background | not_run（`previous_step_failed`） |
| auth_off_local | foreground | not_run（`auth_phase_skip`，expect_auth=on 时设计性跳过） |
| uninstall | cleanup | ok |

  summary：`total=12, ok=3, failure=1, not_run=8`；`request_failures=[]`，`warnings=[]`，`toolchain_failures=[]`；`cleanup_attempted=true`
- **为何 attempt 1 失败但 install/uninstall 仍 ok**：`start` 需要读取模拟器前台窗口布局以断言应用真的聚焦；两次 attempt 均返回 `focus_window_unreadable`（B2 轮询对布局 dump 不可读/未落盘的情形 fail-closed）。install/uninstall 不依赖布局断言，所以能独立通过——这是工具 fail-closed 设计的**正确行为**，不是伪造。
- **attempt 2**：因步骤 1 recheck 确认目标不可达，未消耗；若目标恢复，可在后续切片重跑一次。
- 证据：`.verify/m14-126-harmony-current-main-regression/auth_smoke_on.json`（launcher JSON，含 attempt 1 全部 stage/failures/cleanup facts）+ `auth_smoke_launcher.json` + `auth_smoke_launcher.stderr.txt`
- **本切片不得基于 attempt 1 宣称设备链路通过**：start 失败导致 8 个 foreground UI/auth stage 未执行。

## 步骤 4：聚焦 pytest（通过）

- 解释器：`D:\AI Learning OS\ai-learning-os\.venv\Scripts\python.exe`（canonical 主仓 `.venv`，Python 3.11.15 / pytest 9.1.1）
- 命令（worktree 根）：`python -m pytest tests/harmony_release -q`
- 结果：**524 passed, 1 skipped in 21.29s**（本会话复核同结果）；1 skipped = `test_fifo_input_rejected`（Windows 平台 FIFO 不支持，既有 skip 原因）
- 覆盖：`tests/harmony_release` 全部（auth_smoke / auth_smoke_launcher / backend_smoke / device_preflight / device_smoke / preflight / release_build / sign_hap / verify_signature / agc_closure_manifest 契约测试），零网络、零设备交互
- 证据：`.verify/m14-126-harmony-current-main-regression/pytest_harmony_release.txt`

## 模拟器状态判断的矛盾与最终边界（诚实声明）

- **矛盾**：attempt 1 期间 `127.0.0.1:5555` 真实可达（install/uninstall 成功）；attempt 2 复核时同一目标不可达（hdc 六条证据，见 `hdc-recheck-5555.txt`）。这是时间维度的状态漂移，不是工具误报。
- **最终边界**：
  - release 构建与聚焦 pytest：**PASS**（真实执行，证据完整）。
  - 设备 auth/login UI 链路：**BLOCKED（外部硬件——模拟器目标当前不可达）**。attempt 1 的 install/uninstall ok 是真实发生过的部分证据，但 start 失败 + 8 not_run + attempt 2 blocked 意味着**未达成 M14-120 那种 11/12 ok 的全链路真实通过**，不得视为通过。
  - 未签名、unsigned 边界、未触碰生产后端/DB/MinIO/语音/容器/secrets；一次性 loopback 后端已在 finally 停止并复核；`auth_smoke_server.log`（launcher stderr 泄漏到 worktree 根的副产物，757 字节 traceback）已复制进 `.verify` 并删除根目录原件，tracked 文件零污染。

## 证据目录（gitignored，`.verify/m14-126-harmony-current-main-regression/`）

- `release_build.json` / `release_build.stderr.txt`
- `auth_smoke_on.json`（attempt 1 全量 facts）/ `auth_smoke_launcher.json` / `auth_smoke_launcher.stderr.txt`
- `pytest_harmony_release.txt`
- `hdc-recheck-5555.txt`（attempt 2 前置六条只读证据 + 矛盾归因）
- `hdc-list-targets.txt` / `netstat-5555.txt` / `emulator-procs.txt`（initial 侦察快照）
- `root-auth_smoke_server.log.copy`（删除前副本，防 evidence 丢失）
- `workdir/`（launcher 隔离 workdir）

## tracked 改动边界

本切片对 tracked 文件的唯一改动即本 README（新增 `docs/evidence/m14-126-harmony-current-main-regression/README.md`）。`apps/harmony`、`tools/harmony_release` 及任何生产源码零改动。本地 commit，不 push、不开 PR。
