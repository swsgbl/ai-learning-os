# M14-120：current-main 模拟器回归 —— 执行状态（已完成）

- 工作树：`ai-learning-os-worktrees/m14-120-harmony-current-main-regression`
- 分支：`harmony/m14-120-current-main-regression`（基于 `origin/main` = `2b1c2dd`，
  "Merge pull request #206 from swsgbl/ops/m14-119-monitor-alert-dispatch"）
- 执行者：实现 worker（hmh）；监督：Codex
- 日期：2026-09-24（本地；前一轮部分执行的原始日期为 2026-09-18，
  当时回合预算耗尽，步骤 3-6 诚实标记 not_run —— 见下方保留的门禁历史）

## 结论一句话

current-main 在 Pura 90 模拟器（127.0.0.1:5555）上的 auth/login 回归
**全链路真实通过**：release 构建（unsigned HAP）+ 真实 FastAPI 一次性
loopback 后端 + 模拟器 UI 全流程（12 步中 11 步 ok，1 步为设计性跳过）。
**未签名、未触碰真机、未使用生产后端、不代表生产就绪。**

## 步骤 1：模拟器门禁复核（2026-09-24，通过，未 stop/restart）

| 检查 | 命令 | 结果 |
|---|---|---|
| 目标列表 | `hdc list targets` | `127.0.0.1:15566`（Kaihong BotBook，**禁用，未使用**）与 `127.0.0.1:5555`（本任务目标） |
| 真实 boot | `hdc -t 127.0.0.1:5555 shell "param get bootevent.boot.completed"` | `true` |
| 身份/版本 | `param get const.product.model/name/const.ohos.apiversion/const.product.software.version` | `emulator` / `emulator` / `24` / `emulator 6.1.0.117(SP37DEVC00E115R4P11)` |
| 响应性 | shell echo 探测 | 正常返回 |

与前一轮（2026-09-18）记录一致：同一台 Pura 90 模拟器持续运行。

## 步骤 3：current-main 构建 unsigned HAP（2026-09-24，通过）

- 精确命令：`python tools/harmony_release/release_build.py --quiet`
  （等效链：`hvigorw.bat clean --no-daemon` + `hvigorw.bat assembleHap --mode module -p product=default -p buildMode=release --no-daemon`，cwd=`apps/harmony`）
- 退出状态：exit 0（steps clean/assemble 均 exit 0 且含 BUILD SUCCESSFUL）
- 工件：`apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap`
- 尺寸：220,008 字节
- SHA256：`64F344BE0DAFC143EABCCD19B4CD51AF13278939C710268671DC324E4D51C4D1`
  （release_build.py 输出与独立 `certutil -hashfile` 复核一致）
- 无签名材料参与（`--require-materials` 未启用；signingConfigs 为空，unsigned 边界）

## 步骤 4：auth/login 回归（2026-09-24，通过）

- 精确命令：`python tools/harmony_release/auth_smoke_launcher.py --repo-root . --execute --confirm-mutation --target 127.0.0.1:5555 --hap apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap --evidence-dir .verify/artifacts/m14-120-auth-smoke`
- 退出状态：launcher exit 0；`status=executed`；`reasons=[]`
- 后端（一次性 loopback）：AuthSmokeServer，`http://127.0.0.1:62849`，
  OS 分配端口、隔离 SQLite、合成用户 `aiosstudent`，pid 52360，
  `stop_errors=[]`（运行全程存活并在 finally 干净停机）
- 主机侧 auth 契约 7/7 matched（status/401 门禁/错密码 401/登录发 token/
  me/带 token 解锁/错 token 401）
- 模拟器 UI 步骤：12 步中 11 ok（install/start/settings_ui/home_401_unauth/
  wrong_password/login_and_refresh/cold_restart/logout/background/uninstall）；
  唯一 not_run = `auth_off_local`（expect_auth=on 时的设计性跳过，
  reason=auth_phase_skip）
- 布局证据：5 份 layout 摘要（仅 digest/节点计数，无内容入库），如
  login_and_refresh sha256 `75FEFA4B…1F46BB9B`（51,624 B / 24 节点文本）
- 请求失败/警告：`request_failures=[]`、`warnings=[]`、`toolchain_failures=[]`
- 清理：`cleanup_attempted=true`，uninstall ok（设备已还原，未残留安装）
- PID 说明（诚实声明）：工具不输出应用进程 PID 遥测；进程活性由
  start/cold_restart/background 三步 ok 佐证，后端 PID 见上。
- 原始 JSON：`.verify/artifacts/`（gitignored）：
  `m14-120-build.json`、`m14-120-auth-smoke-stdout.json`、
  `m14-120-auth-smoke/auth_smoke_launcher.json`、`auth_smoke_on.json`

## 步骤 5-6：登记与验证（2026-09-24，实测）

- 登记：PROJECT_STATUS（当前任务首位）/ ROADMAP（`### M14-120 状态更新`）/
  CHANGELOG（Unreleased/Added）最小条目——与本提交实际改动文件一一对应。
- 解释器：`D:\AI Learning OS\ai-learning-os\.venv\Scripts\python.exe`
  （canonical 主仓库 .venv，Python 3.11.15 / pytest 9.1.1；主仓库 mise Python 3.12.13
  非本项目规范解释器——原文误记，已按监督者修正）。
- 聚焦 pytest（canonical `.venv\Scripts\python.exe` -m pytest `tests/harmony_release -q
  --basetemp=.verify/pytest-basetemp-m14-120`）→ exit 0，
  **524 passed, 1 skipped**（21.79s，修正轮同解释器复跑 20.99s；auth smoke / launcher / release-build /
  preflight / sign / verify / device / backend / AGC closure 全套契约测试，零网络）。
- py_compile（canonical `.venv` 同解释器）：`python -m compileall -q tools\harmony_release tests\harmony_release`
  → 通过（exit 0）。
- ruff 0.16.8（经 uvx）：`--select E4,E7,E9,F`（仓库惯例规则集）→
  `tools/harmony_release` 全净；`tests/harmony_release` 仅 1 处 E401
  （`test_auth_smoke_launcher.py:536`，git blame 证实系 main 已合入的 M14-95
  既有代码，本切片零 Python 变更、不越界代修）。
- `git diff --check` → 干净（无空白错误）。
- 单一新本地提交（`docs: M14-120 complete regression evidence and registry`，
  未推送（待 Codex 复核）；监督者修正轮对 interpreter 措辞修正后 amend 原提交）。

## 保留：前一轮（2026-09-18）门禁历史

初始状态（三角验证）：

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
| 身份/版本 | `param get const.product.model && ...` | `emulator` / `emulator` / `24` / `emulator 6.1.0.117(SP37DEVC00E115R4P11)` |

## 边界声明

- 未触碰任何生产容器/DB/MinIO/语音/密钥；未使用签名材料（全程 unsigned）。
- 未改动 Android 设备；未 stop/restart 模拟器；`127.0.0.1:15566`（BotBook）从未作为目标。
- 未 reset/revert 任何变更；前一轮提交 158ddcc（诚实 not_run 记录）保留在历史中。
- 本证据不构成签名/真机/生产后端/生产就绪的任何声明。
