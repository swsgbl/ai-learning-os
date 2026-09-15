# M14-29：sidecar status 路径契约修复（目录 → 精确文件）

- 切片：`fix/m14-29-sidecar-status-path`，基于 `d19c296`（PR #107 merge，
  本地 git 可验证）。确定性生产缺陷修复，由 M14-28 受控生产验收发现；
  变更面 2 文件（控制器 `tools/voice/voice_health_sidecar_control.py` +
  测试 `services/api/tests/test_voice_health_sidecar.py`），零改动 sidecar
  本体、引擎、relay、M14-27 monitor/pipeline 与保护逻辑。
- 状态：**已收口（2026-09-15 回填）**——本修复已随 **PR #108 合并 main**
  （feature head `527c454`、merge commit `1b6d862`，本地 git 可验证；PR CI
  run `34933123493` 与合并后 main CI run `34946366049` 均 5/5 job
  SUCCESS）；其遗留前提「真实 M14-28 验收须在合并后重跑」已闭环——
  2026-09-15 16:38:56–16:59:43 +08:00 在 merge commit `1b6d862` 上整体
  复跑 **9/9 PASS**（sidecar 切换/生命周期/监控集成范围；本修复经
  `/proc/<pid>/cmdline` live 验证——argv 恒传精确 status 文件），证据
  `docs/evidence/m14-28-voice-health-production-cutover/README.md` 与
  gitignored `.verify/m14-28-voice-health-production-cutover-rerun-1b6d862/`；
  `production_ready=false` 不变。

## 1. 缺陷（M14-28 受控验收实证，2026-09-15）

M14-28 按五步指引执行受控生产验收：步骤 4（`start`）两次确定性失败——
sidecar 进程未存活、20s 落档等待超时、控制器 rc 1；验收按纪律在步骤 4
中止（步骤 5–12 未执行）。原始生产证据（traceback、argv 捕获、状态快照）
在主仓库 gitignored `.verify/m14-28-voice-health-production-cutover/`
（ACCEPTANCE-REPORT.md / EXECUTION-LOG.md + 8 份原始捕获），**按纪律不入
git、不在本文复制原始日志**。

## 2. 根因（确定性，非环境性）

`voice_health_sidecar_control.py::status_file_relpath(artifacts, repo_root)`
返回的是 artifacts **目录**（`.verify/artifacts/m14-26-voice-health-sidecar`），
而 `cmd_start` 把该值直接作为 `--status-file` 传给 sidecar：

- sidecar 的 `write_status_file` fail-closed 守卫（已存在且非普通文件的
  目标 → `StatusFileError("unsafe-status-target")`）正确拒绝目录目标，
  sidecar 在监听前退出——**守卫行为正确，调用方契约错误**；
- 控制器 20s `wait_for_status` 超时 → 回收 wsl.exe 句柄 → rc 1。
- 同一 `rel` 还流向 `wait_for_status` / `evaluate_target` / probe argv
  （目录无法 `json.load`）与 manifest.log / 人类可读路径（目录口径碰巧
  正确）。`cmd_stop`/`cmd_status` 的 probe 调用同样收到目录。
- M14-26 的 105 项测试漏检原因：生命周期 fake `_LifecycleRunner.probe`
  只记录 `pid_or_dash`、从不记录 relpath；argv 断言与被测函数用同一
  `status_file_relpath` 计算（同义反复）。

## 3. 修复（修调用契约，绝不绕过 unsafe-status-target）

`tools/voice/voice_health_sidecar_control.py`：

- 拆分路径助手：`artifacts_dir_relpath`（保留原目录语义，仅锚定日志/
  manifest 提示路径）与 `status_file_relpath`（恒返回
  `<目录路径>/sidecar-status.json` 精确文件——仓库内 repo-relative /
  仓库外绝对 posix，两种形态都以 `STATUS_NAME` 结尾）。
- `cmd_start` 双路径分用：`--status-file`、`wait_for_status`、
  `evaluate_target`/身份核验 probe、Linux 侧补回收核验全部走**文件**路径；
  manifest.log 字段、`排查日志:` 提示、`manifest:` 提示仍锚定**目录**
  （绝不出现 `.../sidecar-status.json/sidecar-control.log` 嵌套）。
- `cmd_stop`/`cmd_status`：全部 `rel` 用点均为 probe 契约，随助手修正
  自动收口；**信号语义与保护边界零改动**（SIGTERM/SIGKILL 白名单、
  PROTECTED_PIDS/PROTECTED_MARKERS、TERM→宽限→单次 KILL 原样）。

## 4. 验证（2026-09-15，本 worktree 实测）

解释器：`D:\AI Learning OS\ai-learning-os\.venv\Scripts\python.exe`。

| 项 | 修复前（RED） | 修复后（GREEN） |
|----|--------------|----------------|
| `test_voice_health_sidecar.py` | 8 failed / 105 passed（新增 8 项全红，diff 即目录 vs 文件） | **113 passed**（105 既有全绿，零削弱） |
| `test_m14_27_voice_health_cutover.py` | — | **35 passed** |
| `ruff check` 控制器 + 测试 | — | **All checks passed** |
| `py_compile` 控制器 | — | **通过** |
| `git diff --check` | — | **通过** |

新增 8 项回归锁定的契约：仓库内精确
`.verify/artifacts/m14-26-voice-health-sidecar/sidecar-status.json`（含
真实 `REPO_ROOT` 生产形态）；仓库外绝对路径以 `sidecar-status.json` 结尾；
spawn argv `--status-file` 后随该精确文件；manifest.log / `manifest:`
提示仍为目录 + 文件名（不嵌套进 status 文件之下）；start 全链路
（归属核验/`-` 落档等待/身份核验）、stop（含 wait_dead）、status 的
每个 probe 调用均只收文件路径（`_RelRecordingRunner` 包装记录）。

## 5. 边界（诚实口径）

- **零真实 WSL/零 HTTP/零 Docker/零生产进程/零计划任务**：全部行为经
  fake/临时文件测试锁定；本回合未启动 sidecar、未触碰任何生产进程。
- **不宣称生产就绪**：`production_ready=false`；M14-28 验收已按本节要求
  在修复合并后**整体重跑闭环**（2026-09-15，merge commit `1b6d862`，
  9/9 PASS，不复用任何 halted 步骤结论——含真实 sidecar 启动、监控
  切换与观察）。
- 不修改 sidecar 本体（守卫保持原样——本修复只修 Windows 侧调用契约），
  不修改引擎服务、relay、M14-27 monitor 与保护逻辑。
