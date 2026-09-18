# M14-54 审计归档调度器生产验收——证据

本 README 为 M14-54 唯一入库证据文件；原始验收过程产物位于 gitignored
canonical 工件目录（`.verify\artifacts\m14-53-audit-archive-readiness\`），
不入库。本回填 docs-only：零代码/测试/工作流改动，本回填回合自身零
调度器、零生产操作。

## 任务事实（task facts）

- 验收对象：M14-53 交付的隐藏每日 Windows 计划任务
  `AIOS-Audit-Archive-Readiness`
  （URI `urn:aios:m14-53:audit-archive-readiness`），
  经 `tools/ops/audit_archive_task.py` +
  `run_audit_archive_readiness_silent.vbs` 调度 M14-51 就绪 CLI。
- 验收环境：supervisor 于 2026-09-18（GMT+8）在 canonical main 检出
  `main@5ab05c3`（PR #136 merge）执行；merge 后 main CI run
  `35362172745` 五项 job 全部 success。
- 验收性质：真实 Task Scheduler 注册 + 两条真实调度路径（负/正），
  非开发回合的 FakeSchtasks 注入。
- 回填回合：分支 `docs/m14-54-audit-archive-scheduler-production`，
  单 local commit，不 push、不建 PR。

## 执行序列（execution sequence）

1. `plan` 全部 repo/VBS/venv/readiness 预检通过；初始 `status` 为
   `missing`（同名任务不存在）。
2. `install` 以 `--confirm "EXECUTE AUDIT ARCHIVE READINESS SCHEDULER
   CHANGE"` 一字不差的确认短语执行，且从**非提权** supervisor shell
   成功完成 exact-owned 安装；安装后 `status=installed`、State `Ready`、
   初始 `LastTaskResult=267011`（0x41303，任务尚未运行）、
   `NextRunTime=2026-09-19T00:00:00+08:00`。
3. 真实调度**负路径**（2026-09-18T23:31:30+08:00，state/policy 缺席）：
   `LastTaskResult=2`；仅创建 canonical gitignored 工件目录；无报告
   产出——fail-closed 缺输入行为验证。
4. 手工物化 canonical schema-v1 输入 `state.json`/`policy.json`
   （内容源自已验证的 M14-42/M14-43/M14-49 事实，见下节）。
5. 真实调度**正路径**（2026-09-18T23:32:07+08:00）：`LastTaskResult=0`；
   生成 `readiness.json` 与匹配 sidecar；overall `fresh`、problems 空、
   全部任务 fresh。
6. 终态：任务 `installed` / exact-owned；canonical main tracked 树
   clean（验收未改动任何 tracked 文件）。

## 输入/报告完整性（input/report integrity）

- `state.json`：896 bytes，SHA-256
  `88f344bcd568696eaeeca8352fa776311823aa78c6c193308668b67504dcb680`；
  audit-anchor 链 source SHA-256
  `d2bfd877aa94632e6f68932a4d4bef8d963a6eb61aca12de6429c5fb7873aa4e`；
  上次成功锚点 2026-09-17T02:28:50.131481Z；WORM 归档
  2026-09-17T19:09:09Z；离线副本 2026-09-18T02:52:06Z。
- `policy.json`：164 bytes，SHA-256
  `0b9010c342abddfdf49d506e7ff670c34d36ad2536f233b50fa1ba9df7303d28`；
  三任务间隔 30/90/180 天，宽限 24h。
- `readiness.json`：955 bytes，SHA-256
  `8f5ca05ca19306a8cf7dc01492306387a3c1c1c8265a97ced276b80779d99544`；
  `.sha256` sidecar 匹配；`generated_for=2026-09-18T15:32:07.466339Z`
  （即正路径运行的 UTC 时刻）；下一到期：锚点
  2026-10-17T02:28:50.131481Z、WORM 2026-12-16T19:09:09Z、离线副本
  2027-03-17T02:52:06Z。

## 验证矩阵（verification matrix）

| # | 检查 | 观测 | 判定 |
|---|------|------|------|
| 1 | `plan` 预检（repo/VBS/venv/readiness） | 全过；初始 status `missing` | 通过 |
| 2 | exact-owned `install`（确认短语，非提权 shell） | 成功注册 | 通过 |
| 3 | 安装后任务状态 | installed / Ready / LastTaskResult 267011 / NextRunTime 2026-09-19T00:00:00+08:00 | 通过 |
| 4 | 真实调度负路径（state/policy 缺席，23:31:30+08:00） | LastTaskResult=2；仅建 canonical gitignored 目录；无报告 | fail-closed 验证 |
| 5 | 输入物化（手工，源自已验证事实） | state 896 bytes / policy 164 bytes，SHA-256 在档 | 通过 |
| 6 | 真实调度正路径（23:32:07+08:00） | LastTaskResult=0；readiness.json+sidecar 匹配；overall fresh、problems 空、全部任务 fresh | 通过 |
| 7 | 终态复查 | 任务 installed/exact-owned；tracked main 树 clean | 通过 |

## 诚实边界（honest boundary）

- `state.json`/`policy.json` 为**手工物化**样本（内容锚定已验证的
  M14-42/M14-43/M14-49 事实），不是自动化 updater 的产物；后续真实
  运维仍需要该 updater，本验收不交付它。
- 本验收不声称审计归档本身已执行：负/正路径均只运行就绪评估 CLI，
  未跑任何归档、未触碰 WORM/离线存储/S3/provider/DB/Docker/网络/
  生产数据。
- 验收为单机单日执行：不构成多日连续运行、告警联动或长期无人值守
  证明；`production_ready=false` 不变。
- M14-54 回填本身 docs-only：五份 tracked 文档改动，零代码、零测试、
  零 CI 工作流改动；单 local commit，不 push。
