# M14-138：Harmony current-main 模拟器恢复与 auth/login 回归收口

## 结论（先说结果）

| 项 | 结果 |
|---|---|
| 基点 | `origin/main` = `5eba63bcb3fb1056a6c12764373ab197a21487e2`（PR #224 merge，fetch 后 `git rev-parse origin/main` 复核一致），worktree `m14-138-harmony-current-main-regression`，分支 `harmony/m14-138-current-main-regression` |
| 模拟器恢复 | **成功**：既有部署实例 Pura 90（HarmonyOS 6.1.1(24) Beta1，phone_all_x86，1320x2856）以受支持启动器无头启动（`harmony_emulator_start`，Emulator.exe PID 53556），hdc 目标 `127.0.0.1:5555` 恢复；`param get bootevent.boot.completed` = `true`（真实启动完成，非仅目标出现） |
| 目标身份 | `const.product.model`=emulator / `const.product.name`=emulator / API 24 / `const.product.software.version`=emulator 6.1.0.117(SP37DEVC00E115R4P11) |
| Release 构建 | **通过**：`tools/harmony_release/release_build.py --quiet` exit 0（clean+assemble 均 BUILD SUCCESSFUL），unsigned HAP 220,008 字节，SHA256 `8F5797869F1E3275B885E0B8C439A2483362850A6CD309A401235C900CF08A31`（release_build.json 与独立 `certutil -hashfile` 复核一致）；unsigned 边界：`--require-materials` 未启用，无签名材料参与 |
| Auth smoke（真实 execute/mutation，attempt 1 一次性通过） | **通过**：12 stage = **11 ok / 0 failure / 1 not_run**（唯一 `auth_off_local` 为 `expect_auth=on` 设计性跳过，reason=`auth_phase_skip`）；`request_failures=[]`、`warnings=[]`、`toolchain_failures=[]`；`cleanup_attempted=true`，收尾 uninstall ok |
| 主机侧 auth 契约 | **7/7 matched**（auth_status / gated_privacy_401 / wrong_password_401 / login_200_token / me_200_with_token / gated_privacy_with_token / gated_privacy_wrong_token） |
| `focus_window_unreadable` | **未复现**：M14-126 attempt 1 曾因 start 阶段布局不可读 fail-closed；本次 attempt 1 的 start/settings_ui/home_401_unauth/wrong_password/login_and_refresh/cold_restart/logout 全部 foreground stage 均 ok，5 份布局 dump（各 22–35 节点文本、SHA256 各异）成功读取并断言，无任何 focus 相关失败 |
| attempt 2 | **未消耗**：attempt 1 一次性满足全部验收（12 stage / 11 ok / 0 failure / 1 预期 not_run / 0 warnings / 0 request failures / 0 toolchain failures / cleanup + uninstall），按任务规则无需重试 |
| 聚焦 pytest | **通过**：`tests/harmony_release` **524 passed, 1 skipped in 21.76s**（canonical 主仓 `.venv` Python，worktree 根执行；1 skipped = `test_fifo_input_rejected` Windows FIFO 既有跳过，与 M14-126 基线一致） |
| mock 契约 | **通过**：`tools/harmony_mock/test_contract.py` **81/81 passed, 0 failed**（M13 suite 54/54 + M14-89 auth suite 27/27） |
| ruff / py_compile | **未运行**：本切片未修改任何 Python 工具源码（任务书仅要求修改时运行）；tracked 改动仅 docs/evidence 与三台账 |
| 最终口径 | current-main 设备回归 **PASS**：M14-126 遗留的两项缺口（目标离线 BLOCKED + focus_window_unreadable）均闭合——模拟器恢复后同一 unsigned HAP 真实走完 install→start→UI auth 全链路→uninstall |
| 生产边界 | 未触碰 SDK 配置、签名材料、Android USB 设备、生产容器、DB、MinIO、语音、secrets；一次性 loopback 后端 `http://127.0.0.1:56747`（PID 58804，合成用户 `aiosstudent`，隔离 workdir）由 launcher finally 停止并复核无进程残留（端口仅 TIME_WAIT） |
| git 边界 | 本 evidence README + PROJECT_STATUS/ROADMAP/CHANGELOG 三台账条目（PASS 为决定性结果），单 local commit，不 push、不开 PR；`.verify/m14-138-harmony-current-main-regression/`（gitignored）保存全部原始证据 |

## 步骤 1：模拟器恢复与目标身份证明

- 侦察（先只读）：`harmony_emulator_list` → deployed device **Pura 90 [phone · HarmonyOS 6.1.1(24) Beta1 · 1320x2856] stopped**；`hdc list targets` → `[Empty]`；无 Emulator.exe 进程——确认 M14-126 BLOCKED 根因即本地模拟器实例停机。
- 恢复（任务授权的受支持启动器）：`harmony_emulator_start --name "Pura 90"` → started（PID 53556），hdc targets 出现 `127.0.0.1:5555`。
- **真实启动验证**：`hdc -t 127.0.0.1:5555 shell "param get bootevent.boot.completed"` → **`true`**（不信任目标字符串出现，按既有教训验证 boot 事件）。
- 目标身份与进程证据全量落盘 `.verify/.../target-identity.txt`（hdc targets / boot / 四项 param / tasklist Emulator.exe + emulator-crash-service.exe）。

## 步骤 2：current-main release 构建（通过）

- 命令：`python tools/harmony_release/release_build.py --quiet`（canonical venv）
- 结果：exit 0；工件 `apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap`
  - 尺寸 **220,008 字节**；SHA256 **`8F5797869F1E3275B885E0B8C439A2483362850A6CD309A401235C900CF08A31`**（certutil 独立复核一致）
- unsigned 边界：`--require-materials` 未启用，build-profile signingConfigs 空，无签名材料参与——与 M14-120/M14-126 同一边界口径
- 证据：`release_build.json` / `release_build.stderr.txt`（空）

## 步骤 3：auth smoke（真实 execute/mutation，一次性通过）

- 命令：
  `python tools/harmony_release/auth_smoke_launcher.py --repo-root . --execute --confirm-mutation --target 127.0.0.1:5555 --hap apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap --evidence-dir .verify/m14-138-harmony-current-main-regression`
- 一次性 loopback 后端：`http://127.0.0.1:56747`（OS 分配端口），PID 58804，合成用户 `aiosstudent`，隔离 workdir；设备侧经 `http://10.0.2.2:56747/` 访问
- 主机侧 auth 契约 **7/7 matched**
- **12 stage 结果（attempt 1，一次性全绿）**：

| stage | phase | status |
|---|---|---|
| host_auth_contract | read_only | ok |
| install | mutation | ok |
| start | foreground | ok |
| settings_ui | foreground | ok |
| auth_off_local | foreground | **not_run（`auth_phase_skip`，expect_auth=on 设计性跳过——任务书预期的唯一 not_run）** |
| home_401_unauth | foreground | ok |
| wrong_password | foreground | ok |
| login_and_refresh | foreground | ok |
| cold_restart | foreground | ok |
| logout | foreground | ok |
| background | background | ok |
| uninstall | cleanup | ok |

  summary：`total=12, ok=11, failure=0, not_run=1`；`request_failures=[]`、`warnings=[]`、`toolchain_failures=[]`；`cleanup_attempted=true`；`mutation_performed=true`
- **布局断言证据**（对照 M14-126 focus_window_unreadable）：5 个 foreground 布局 dump 成功读取并断言——wrong_password（24 节点文本）、login_and_refresh（24）、cold_restart（24，home_asserted=true）、logout（22）、post_logout_home（35），各自 SHA256/字节数登记于 launcher JSON `layouts` 段，`content_recorded=false` 仅指不落全文（hash 锚定），非读取失败。
- **清理复核**（`cleanup-verification.txt`）：后端 PID 58804 无进程匹配；端口 56747 仅 TIME_WAIT 残留；`bm dump -n com.ailearningos.app` → error（bundle 已卸载无残留）；模拟器仍存活（hdc targets = 127.0.0.1:5555）
- 副产物处理：launcher 在 worktree 根遗留 `auth_smoke_server.log`（与 M14-126 同款先例），复制入 `.verify` 后删除原件，post-commit worktree clean
- 证据：`auth_smoke_attempt1.json`（launcher 全量输出）/ `auth_smoke_on.json`（launcher 自落盘 facts）/ `auth_smoke_launcher.json` / `auth_smoke_attempt1.stderr.txt`（空）

## 步骤 4：聚焦 pytest 与 mock 契约（通过）

- `python -m pytest tests/harmony_release -q`（canonical venv，worktree 根）→ **524 passed, 1 skipped in 21.76s**
- `python tools/harmony_mock/test_contract.py` → **81/81 passed**（54/54 M13 + 27/27 M14-89 auth）
- ruff / py_compile 未运行（零 Python 源码改动，任务书条件不触发）
- 证据：`pytest_harmony_release.txt` / `harmony_mock_contract.txt`

## M14-126 两项遗留的闭合说明

1. **hdc 目标消失 → 已恢复**：根因是本地 Pura 90 模拟器实例停机（非 SDK/配置问题）；受支持启动器重启后 `127.0.0.1:5555` 恢复且 boot completed=true。
2. **focus_window_unreadable → 未复现且被正面证据反驳**：本次 attempt 1 在同一镜像、同一 unsigned HAP、同一 launcher 下，start 阶段布局读取与断言全部成功，5 份布局 dump 均可读（22–35 节点文本 + SHA256 锚定）。M14-126 的失败更符合模拟器会话末期的临时性故障（该会话随后即目标离线），非确定性缺陷；本次无需弱化任何断言即全绿。

## 诚实边界

- 未签名 HAP、模拟器非真机、loopback 后端非生产后端——本 PASS **不构成签名/真机/生产就绪声明**；`production_ready=false` 不变，release-approval 仍是 human-only 门。
- 单次模拟器会话内的单次全链路通过（attempt 1）；不证明跨会话/长期稳定性，不覆盖 AGC 签名发布与真机链路（维持既有未决口径）。
- 模拟器恢复仅启动既有部署实例（受支持启动器），未改 SDK 配置、未新建/删除实例、未触碰任何生产面。
- 未修改任何工具源码；auth/login 断言零弱化。

## 证据目录（gitignored，`.verify/m14-138-harmony-current-main-regression/`）

- `target-identity.txt`（目标身份/启动证明/进程）
- `release_build.json` / `release_build.stderr.txt`
- `auth_smoke_attempt1.json` / `auth_smoke_attempt1.stderr.txt` / `auth_smoke_launcher.json` / `auth_smoke_on.json`
- `cleanup-verification.txt`（清理四项复核）
- `pytest_harmony_release.txt` / `harmony_mock_contract.txt`
- `auth_smoke_server.log`（worktree 根副产物删除前副本）

## tracked 改动边界

本切片 tracked 改动：本 README（新增）+ `docs/PROJECT_STATUS.md` / `docs/ROADMAP.md` / `docs/CHANGELOG.md`（各一条 M14-138 条目，PASS 为决定性结果）。`apps/harmony`、`tools/harmony_release`、`tools/harmony_mock` 及任何生产源码零改动。单 local commit，不 push、不开 PR。
