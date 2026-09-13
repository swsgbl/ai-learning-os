# M14-23 restart 增量语义生产验收回填 — 证据归档

- 日期：2026-09-13（supervisor 真实计划任务验收 + docs-only 回填）
- 分支：`docs/m14-23-restart-delta-production-acceptance`（基于
  `main@6efcdfd`，即 PR #100 merge commit
  `6efcdfdf46ff1638e41ee59f5329de544cdeb858`，本地 git 可验证）。本 Claude
  开发回合独占 worktree，仅做**一个本地 commit**；supervisor 审查与 remote
  发布（push/PR/合并）在其后进行。
- 切片性质：**docs-only 回填**——零代码/零测试/零 workflow 改动；不触碰任何
  生产服务、计划任务、容器、voice 进程；不运行 `monitoring_pipeline`
  execute / monitor / history / insights 任何工具执行面；不复制任何
  gitignored 原始工件入库（本文件只记录文件名、SHA-256、字节数等安全
  摘要）；不含绝对本机路径、生产容器 ID、密钥或 env 值。
- 状态：**M14-23 遗留边界「真实计划任务调度下的新语义验证」闭环为已验收**
  ——PR #100 合并后计划任务 `AIOS-Monitoring-Pipeline` **自然调度**两轮
  （18:00 与 18:15（+08:00），均 Last Result=0、无任何手动生产触发），
  **37 连 warn 在新代码首轮（18:00）按设计恢复 ok 并在第二轮（18:15）
  保持**；`production_ready=false` 不变。

## 1. 合并与 CI 事实（supervisor 验收，2026-09-13）

- PR #100（M14-23 `feat/m14-23-restart-delta-monitoring`）已合并 main：
  merge commit `6efcdfd`（完整
  `6efcdfdf46ff1638e41ee59f5329de544cdeb858`，parents `73d0443`（main 侧，
  PR #99 merge）+ `5926172`（feature head，完整
  `592617286cbd90b8a803ae0b8cfd6cc1511d025f`，含 supervisor R1 加固
  amend），本地 git 可验证；回填回合已核对 `merge-base --is-ancestor
  5926172 6efcdfd` 为真）。
- PR CI run `34750025828` 与合并后 main push CI run `34750207530`：均最终
  **completed/success，5/5 job success**（Web、Android、Release tools、
  Docker、API）——supervisor 验收事实（本回填回合未发起网络 CI 查询）。

## 2. 计划任务事实（supervisor 验收；两轮均为自然调度）

- 计划任务 `AIOS-Monitoring-Pipeline`（M14-22 起注册态 installed，PT15M
  重复间隔）于 PR #100 合并、canonical main 检出更新后**自然触发**：
  - 第一轮 Last Run Time **2026-09-13 18:00:01（+08:00）**，Last
    Result=**0**（管道报告 started_at `2026-09-13T10:00:02Z` 与之逐秒
    对应）；
  - 第二轮 Last Run Time **2026-09-13 18:15:01（+08:00）**，Last
    Result=**0**（管道报告 started_at `2026-09-13T10:15:02Z`）。
- **无任何手动生产触发**：supervisor 未运行 `schtasks /Run`、未手动执行
  管道 execute——两轮均为调度器按 PT15M 间隔自然执行（与 M14-22 验收的
  12:30/12:45 两轮同源同机制）。
- 17:45 一轮（工件 `monitor-20260913-094502.json`，started
  `2026-09-13T09:45:02Z`）仍为**合并前旧代码/旧语义**（见第 3 节）——
  canonical main 检出在 09:45:05Z（旧轮结束）与 10:00:02Z（新轮开始）
  之间完成更新；18:00 起两轮为 PR #100 合并后代码。

## 3. 语义翻面核心证据（三轮 monitor 工件逐项对照）

gitignored `.verify/artifacts/m14-12-production-monitoring/` 下三轮工件
（SHA-256/字节数见第 4 节表；均为只读核对，未改动）：

| 轮次（+08） | 工件 | 代码/语义 | overall | counts(ok/warn/crit) | ready | api 累计 restart | api 评估 |
|---|---|---|---|---|---|---|---|
| 17:45 | `monitor-20260913-094502.json` | 旧（v1，无 restart_evaluation） | **warn** | 33/1/0 | false | 1 | 检查 `restart_count=1` → **warn** |
| 18:00 | `monitor-20260913-100002.json` | 新（M14-23） | **ok** | 34/0/0 | **true** | 1（不变） | state ok / reason **stable** / **delta 0** |
| 18:15 | `monitor-20260913-101502.json` | 新（M14-23） | **ok** | 34/0/0 | **true** | 1（不变） | state ok / reason **stable** / **delta 0** |

- 三轮 api 容器 `started_at` 逐字相同（同一容器实例，未重建/未重置）；
  api 累计 `restart_count=1` 三轮不变——**变化的是评估语义，不是栈状态**。
- 18:00 轮 `restart_evaluation.baseline` = `{status: ok, reason: null,
  source_stem: monitor-20260913-094502, collected_at:
  2026-09-13T09:45:02Z, invalid_skipped_count: 0}`——**基线取自 17:45 旧
  语义 v1 工件**（无 restart_evaluation 字段），M14-23 的「v1 旧工件合法
  充当基线」向后兼容路径在真实生产首次生效；18:15 轮基线链推进为
  `monitor-20260913-100002`（滚动基线）。
- 检查明细：旧轮 `container-restarts` api detail = `restart_count=1`
  （severity warn）；新两轮 detail = `restart_count=1 delta=0
  baseline=<基线 stem>`（severity ok）——**六服务 state 全 ok / reason
  stable / delta 0，`monitoring_ready=true`**。
- **结论（本节核心）**：M14-22 定性的「健康栈因静态累计 restart_count
  长期 warn」问题（至 17:45 已 **37 连 warn**，见第 5 节）在新代码首轮
  按设计恢复 ok（同累计值、同实例 → 当轮增量 0），第二轮保持 ok（无
  抖动、无重建/重置误报）。

## 4. canonical 管道报告（两轮自然调度产物，gitignored 不入库）

两轮报告位于 gitignored `.verify/artifacts/m14-14-monitoring-pipeline/`：
`pipeline-20260913-100006.{json,md}`（18:00）与
`pipeline-20260913-101507.{json,md}`（18:15）；此处仅记录安全摘要：

- 两轮均 `overall_status=ok`、三步序列 **monitor → history → insights**
  全部 status=ok、exit 0，lock `pipeline.lock` acquired/released =
  **true**（无 stale lock）：
  - 第一轮（`2026-09-13T10:00:02Z`–`10:00:06Z`）：monitor **4.068s** /
    history **0.364s** / insights **0.282s**；
  - 第二轮（`2026-09-13T10:15:02Z`–`10:15:07Z`）：monitor **4.947s** /
    history **0.31s** / insights **0.201s**。
- monitor 步时长较 M14-22 两轮（1.27–1.756s）上升至 4.07–4.95s——与
  M14-23 新增的基线解析（读取 prior 工件目录）一致，仍远低于 PT12M
  执行时限与 540s monitor 内部超时预算。
- 各步产物（文件名 + SHA-256 + 字节数，均 gitignored 不入库）：

| 轮次 | 步骤 | 产物 | SHA-256 | 字节 |
|---|---|---|---|---:|
| 18:00 | （报告） | `pipeline-20260913-100006.json` | `53153f6ad35534c5af880836c7ba29b8e9972ee96c8e481cd3142a6f066547b5` | 5243 |
| 18:00 | monitor | `monitor-20260913-100002.json` | `d4fe08fcb1f00a7481003bac1860cb240f31eda04654913683f33c2259233775` | 15631 |
| 18:00 | history | `history.jsonl` | `8e072430d434dd77213433495d8adaabf4c7d793756f1dcda87a4dc10a8e7bb0` | 38909 |
| 18:00 | history | `history-summary.md` | `95edb211976248c3fd60dbe01e3debb4012198474c2ec12980a96e4ab756e180` | 1867 |
| 18:00 | insights | `insights.json` | `3b2bb8d721bac0f0fca5868c50329c1afe8354cfb21bb7e4deb990f8c0e8057c` | 10458 |
| 18:00 | insights | `insights-summary.md` | `16bed0834c5fc4c57ddd9f4ec6784cb7a99dcb0f5309219d458b51949898029b` | 4057 |
| 18:15 | （报告） | `pipeline-20260913-101507.json` | `98d92eedf4942e517da06e67eed951cbdf40e14867f36980a17c8d1940536985` | 5242 |
| 18:15 | monitor | `monitor-20260913-101502.json` | `33599ac5e3ea530b81ffa0562fc078095ac3c79f81af4ad43c2931db4ad5f1bc` | 15643 |
| 18:15 | history | `history.jsonl` | `64b98d1c77299836d0cf1fee352bc4d302fec2d34f1b8482388d14aeaa4f3924` | 40421 |
| 18:15 | history | `history-summary.md` | `d27319b9bf80515d0295872df38a5deb799805ee104d534a5bf9dbdae4ef2116` | 1867 |
| 18:15 | insights | `insights.json` | `39fa08e8bff5d6b8995f5a8d5b7114b18614bbaaf14599dba530b5159d10887b` | 10454 |
| 18:15 | insights | `insights-summary.md` | `8a06d441715d2b6d51383e71db94d4e394a996d1bd3e72e31f71d13b2a7cdaf6` | 4057 |

- 另：17:45 旧语义轮 `monitor-20260913-094502.json`（SHA-256
  `517556fe2f696607be944a9af2a78b97e111c0fbebc9857d702d01379eb5965b`，
  14346 字节）。
- **哈希链独立复核**：回填回合对两轮 monitor JSON 与 18:15 轮
  `history.jsonl` / `insights.json` 独立重算 SHA-256 与字节数——与两轮
  管道报告留档值**逐项一致**。

## 5. history / insights 事实（18:15 第二轮刷新后）

gitignored `.verify/artifacts/m14-13-monitoring-history/history.jsonl`
（SHA-256/字节数见第 4 节表）与
`.verify/artifacts/m14-15-monitoring-insights/insights.json`（同表）：

- **history 40 个历史样本**（`2026-09-11T17:39:20Z` ~ `2026-09-13T10:15:02Z`）：
  **ok=3 / warn=37 / critical=0**；其中 **2 行携带 restart_evaluation**
  （18:00 与 18:15 两轮）；最新行 `2026-09-13T10:15:02Z` overall
  **ok**——api 累计 `restart_counts=1` 与 `restart_evaluation
  {baseline_status: ok, baseline_source_stem: monitor-20260913-100002,
  state: ok, reason: stable, delta: 0}` 并存（累计口径与增量口径同档）。
- **insights 40 样本、ok=3**（availability=3/40）：**当前 ok 连胜 ×2**
  （10:00:02Z、10:15:02Z）；**最长 non-ok 连败 37 条**
  （`2026-09-13T01:04:14Z` ~ **`09:45:02Z`**——恰终止于最后一个旧语义
  轮）；**overall 恢复转移恰 1 次 @ `2026-09-13T10:00:02Z`（warn→ok）**
  （失败转移 1 次 @ 01:04:14Z，为既有历史）。
- **api 服务面增量/事件/恢复计数**：`restart_total=39`（**累计**口径，
  40 样本求和——与增量口径明确区分）、`restart_delta_total=0`、
  `restart_delta_samples=2`、`restart_event_count=0`、
  `restart_recovery_count=0`——**新语义两轮零 restart 事件**（无增量
  告警、无重建/重置、无 baseline-missing）。
- 历史样本本身是本地只读工件，不可改动/删除（M14-13 起既定边界）。

## 6. 验证口径（全部只读，零生产触碰）

supervisor 验收（2026-09-13）与本次回填的核对命令口径：

- **git**：本地只读核对 canonical `main == 6efcdfd`、merge parents
  （`73d0443` + `5926172`）、`Merge pull request #100 …` 提交主题、
  `merge-base --is-ancestor`。
- **工件**：直接读取 gitignored `.verify/artifacts/` 下 canonical 报告与
  产物（monitor 三轮 / pipeline 两轮 / history.jsonl / insights.json）；
  回填回合独立重算全部留档 SHA-256 与字节数并逐项比对（见第 4 节）。
- **CI**：run `34750025828` / `34750207530` 为 supervisor 验收事实（本
  回填回合未发起网络查询）。
- **计划任务**：Last Run/Last Result 为 supervisor 验收时点事实（本回填
  回合未运行任何计划任务命令——含只读 status）。
- 本切片**明确不运行**：monitor / history / insights / pipeline 任何执行
  面、计划任务 install·uninstall·触发·status、任何生产服务、容器、
  voice 进程的启停或改动、Docker。

## 7. 结论、边界与下一步

**结论**：

- M14-23（restart 增量语义 + supervisor R1 加固）自此经**真实计划任务
  自然调度两轮端到端验收**：合并后 18:00 / 18:15 两轮 Last Result=0、
  三步全 ok；**37 连 warn 在新代码首轮按设计恢复 ok**（同累计值、同容器
  实例、当轮增量 0、基线取自旧 v1 工件——向后兼容路径实证）并在第二轮
  保持（滚动基线、零事件、零误报）。
- M14-23 开发时点遗留边界「真实计划任务调度下的新语义验证尚未运行」
  **闭环为已验收**（本切片）。
- **口径精确性**：两轮自然调度验证的是**恢复语义正确性与短期（两轮
  窗口）稳定性**——不构成长期稳定性证明，也不构成 production readiness
  宣称。

**边界（诚实口径，`production_ready=false` 不变）**——仍未完成：

- 外部告警接入（当前仅本地工件洞察，不接任何外部告警系统）；
- 指标时序存储/查询（history.jsonl 有界留存 ≠ 时序数据库）；
- 阈值随时间标定（增量阈值默认 warn≥1 / critical≥5 尚无时间维度标定）；
- 真实客户端验收；>60s / 真实负载 / 跨机长稳；AGC 发布；**更长时间窗口
  的调度连续性与增量语义观察（当前实证窗口为两轮）**；重建/重置/
  baseline-missing 路径的真实生产触发尚未发生（仅契约测试覆盖）。
