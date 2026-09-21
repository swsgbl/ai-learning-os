# M14-79：生产 soak 恢复与发布门证据推进（实现/文档切片）

- 切片：分支 `ops/m14-79-soak-recovery-gates`（独立 worktree
  `m14-79-soak-recovery-gates`，基于 main `376c4ee9f6797b2e96ec97270f738662ffcb7000`
  （PR #165 merge，精确基点）），单次本地 commit（不推送、不建 PR）。
- 目标：在**零生产触碰**约束下推进 M14-78 之后的剩余发布门证据路径——
  把 2026-09-21 监控管道超时/恢复事件变成可审计、可归因、可复盘的记录，
  并把「何时允许重启一个全新 24h soak 窗口」固化为 fail-closed 门禁。
- 约束（全程遵守）：零容器启停/重建、零计划任务操作、零生产日志写入、
  零 secrets/env 读取（未读取 `infra/env.production-recovery`，未输出任何
  token/key）、零发布审批接触、零部署。`release_ready=false` /
  `production_ready=false` 全程不变；本切片不签署、不代拟任何审批。
- 唯一被读取的生产面数据是既有 gitignored 监控工件（M14-14 管道报告、
  M14-13 history.jsonl、M14-12 monitor 工件、M14-79 soak 基线）——全部
  只读；新证据输出至主仓 gitignored
  `.verify/artifacts/m14-79-pipeline-incident-review/` 与
  `.verify/artifacts/m14-79-soak-window-anchor/`。

## 1. 事实基线：2026-09-21 管道超时与恢复事件（全部可从工件复核）

时间全部 UTC（本地 = UTC+8）。来源：M14-14 管道报告 JSON、M14-12 monitor
工件、M14-13 history.jsonl、M14-79 soak 基线——逐文件核对，非推断。

| 时刻（UTC / 本地） | 事件 | 语义 |
|---|---|---|
| 03:15:01 / 11:15 | 管道 ok（monitor 0.794s / history 0.332s / insights 0.143s） | 正常 |
| 03:30 / 11:30 | 该槽位管道报告、monitor 工件、history 行**全部缺失** | 数据空档（原因不可从工件定证） |
| 03:44:45 与 03:45:01 / 11:44–11:45 | 两次管道运行 monitor exit 2 但**写出了样本工件**（`monitor-…-034447/034501`，overall=critical：六服务 container-recreated/started_at_changed 告警） | **状态域裁决**：监控正常工作，系统确实劣化；history/insights skipped 是设计内门控后果 |
| 04:00:01 / 12:00 | 管道 exit 1：monitor ok（样本 04:00:01=warn，postgres error_total=5）、**history 超时 45.206s vs 45s**、insights skipped | **执行域瞬态失败**：样本工件已落盘，行由 04:15 运行补录 |
| 04:15:01 / 12:15 | 管道 ok（history 顺带补录 04:00:01 行） | 恢复 |
| ≈04:29 / ≈12:29 | 六容器被**外部重建**（后续样本的 restart_evaluation 告警佐证） | 生产事件（非本切片操作） |
| 04:30:02 / 12:30 | 管道 exit 1：monitor exit 2 且**未写出样本工件**、history/insights skipped | **执行域失败**：该槽位零样本、数据真空（任务简报称「12:30 因 history 超时返回 1」与工件不符——12:30 是 monitor 无工件，history 超时发生在 12:00 与 12:45 两轮，如实修正） |
| 04:45:25 / 12:45 | 管道 exit 1：monitor ok（15.171s，样本 04:48:13=warn：六服务 container-recreated 告警 + postgres error_total=6）、**history 超时 46.955s vs 45s**、insights skipped | **执行域瞬态失败**：样本行由 05:00 运行补录 |
| 05:00:01 / 13:00 | 管道全链恢复（monitor 1.454s / history 7.185s / insights 0.158s） | 管道执行面恢复 |

- 后续健康检查 healthy；但 **history 尾部样本持续 warn**（restart/recreate
  告警消退后余 postgres error_total=6 的 log-errors warn）——监控状态面
  **未恢复**，与管道执行面恢复是两件事。
- M14-79 soak 基线（`.verify/artifacts/m14-79-soak-baseline/`，锚点
  2026-09-21T04:15:01Z，输入 sha256
  `323bc37175698fbfc627fd95cc215cfa65d340ffba7e7f9f3adb66de71770340`）：
  **blocked**——non-ok-status-in-window，窗口内 55 ok / 2 warn /
  3 critical，最大间隔 403.45 分钟（sidecar 断档期遗留）。
- 长稳门 long-soak（M14-73）需要真实连续 24h 干净窗口；在被 warn/critical
  污染的历史上**不可能**通过——必须等尾部干净后重启窗口起算。

## 2. 交付一：`tools/ops/pipeline_incident_review.py`（执行失败 vs 状态劣化）

把「pipeline exit 1」一个数字拆成三类可审计语义（背景即 §1 时间线）：

- **monitor 非零退出但写出样本工件 = 状态域裁决**（监控正常、系统劣化；
  其引发的 skipped 不计执行失败）；**非零且无工件 = 执行域失败**（槽位
  零样本）；**history/insights 超时 = 执行域瞬态失败**（样本工件在盘，
  history 行由后续成功运行增量补录——补录事实如实呈现，超时绝不改记
  成功）。
- 逐运行归因（固定词汇 failure_domain ∈ none/execution/status/mixed、
  execution_failure_kinds、超时步、样本入史反查）→ 事件窗口聚合（恢复 =
  其后首个 overall ok 运行；无恢复保持开放）→ 双面判定
  （pipeline_execution_state × monitoring_status_state）。
- 与 soak_stability_audit 同款纪律：单文件纯标准库、I/O 经 Store 注入、
  零子进程/零网络/零计划任务/零 env 读取/零墙钟（generated_at 取自输入
  时间戳，输出逐字节可复现）、原子落盘、symlink/越界拒绝、输入拒绝零
  输出。退出码 0 无开放项 / 1 有开放项（可见结论）/ 2 拒绝。

canonical 真实取证运行（2026-09-21，主仓根，repo venv Python 3.11）：

```bash
python tools/ops/pipeline_incident_review.py \
  --pipeline-dir .verify/artifacts/m14-14-monitoring-pipeline \
  --history .verify/artifacts/m14-13-monitoring-history/history.jsonl \
  --output-dir .verify/artifacts/m14-79-pipeline-incident-review --runs 20
# exit 1（有开放项，如实）
```

- 判定：`pipeline_execution_state=recovered`（05:00 全链恢复）、
  `monitoring_status_state=degraded`（最新样本 05:30:01Z=warn，尾随连续
  ok=0）——开放项 `history-latest-sample-non-ok`。与 §1 事实逐一吻合。
- 复盘 20 份 execute 报告（目录共 822 份）逐运行归因正确：04:30 运行 =
  execution / monitor-no-artifact（无样本）；04:00 与 04:45 运行 =
  execution / history-timeout 且样本 indexed=true/warn（超时+补录并列
  陈述）；03:44/03:45 运行 = status（critical 裁决）；事件窗口
  execution（04:30–04:45，recovered_by=05:00 运行）。
- 输出（gitignored，本 README 为唯一入库证据文件）：
  `pipeline-incident-review.json` 12514 bytes sha256
  `d8627e49bdd86ef9f4b20998e1663e9fe3e0db553ac3bb00c60c8c0c9c102b66`；
  `pipeline-incident-review.md` 4647 bytes sha256
  `73c0a7dbaa94a4bf90d0bd41a9b158293b90641122dbe6a429aab94d134ed00b`。
  输入 history.jsonl 当时 662059 bytes sha256
  `25c81aff185ea6d6c7f16f300b5298c1716c7a49697813252d9cde8667b9e8b0`
  （**history 是活文件**——PT15M 持续追加，上述哈希是取证时点快照）。

## 3. 交付二：`tools/ops/soak_window_gate.py`（soak 锚定/重启门）

把「何时允许重启干净 24h 窗口」固化为 fail-closed 门禁：

- 门条件（全满足才 open）：尾部 `--consecutive-ok N`（默认 8，1–5000）
  个连续样本全部 ok 且 partial=false；尾部相邻间隔 ≤
  `--max-gap-minutes`（默认 20，与 soak 审计同口径）；总行数 ≥ N。
  **warn/critical/partial 残留即 closed，并逐条列出**（collected_at +
  overall_status + partial——绝不遮蔽）。
- `--anchor`：门 open 时原子写出锚定记录 `soak-window-anchor.json/.md`
  （锚点 = 最新样本 collected_at——零墙钟，绝不取系统钟；最早可判定
  时间 = 锚点 + 窗口；前置指纹 + 输入 sha256；后续审计固定指引）。
  锚定记录已存在 → `anchor-exists` 拒绝（重启窗口须操作者显式归档旧
  记录，防静默重锚掩盖已破坏的窗口）。门 closed 时只落门报告、锚定
  记录零写出。
- 诚实边界：**锚定 ≠ soak 通过**——门只证明开窗时尾部干净；窗口结局
  由 24h 后 `tools/ops/soak_stability_audit.py` 判定（其报告逐字节复制
  为 `long-soak.json` 才构成门证据）。

canonical 真实取证运行（检查模式，未锚定——门未开）：

```bash
python tools/ops/soak_window_gate.py \
  --history .verify/artifacts/m14-13-monitoring-history/history.jsonl \
  --output-dir .verify/artifacts/m14-79-soak-window-anchor
# exit 1（closed，如实）
```

- 门状态 **closed**，原因 `non-ok-in-tail, excessive-gap-in-tail`；锚点
  （最新样本）2026-09-21T05:30:01Z；尾部 8 行**全部非干净**，逐条清单：
  03:44:47Z=critical、03:45:01Z=critical、04:00:01Z=warn、04:15:01Z=warn、
  04:48:13Z=warn、05:00:01Z=warn、05:15:01Z=warn、05:30:01Z=warn（另
  03:15→03:44:47 间隔 29.78 分钟 > 20）。**当前拒绝重启 soak 窗口的原因
  即此清单**——正是任务要求的行为：尾部 warn/critical 仍在，绝不带病
  开窗。
- 重启工作流（供 supervisor 在获准窗口执行）：①持续观察至 warn 消退
  （postgres log-errors 告警阈值回落）；②`soak_window_gate.py`（检查
  模式）至 open；③`--anchor` 锚定；④锚点 + 24h 后运行
  `soak_stability_audit.py` 判定；⑤通过才把报告复制为 long-soak 证据。
- 输出：`soak-window-gate.json` 2567 bytes sha256
  `67118b61f8e9fa7360a35f0cc52187821d923e5bf746dfce4473e617c7e84dba`；
  `soak-window-gate.md` 1750 bytes sha256
  `584d6a1be7bde9a8d3336bb9d77c8901ea8c19c546c333ba0ff854df5716c261`。

## 4. 测试与静态验证（真实执行结果，follow-up 轮最终口径）

- 新增契约测试两件套（worktree，repo venv pytest 9.1.1）：

```bash
cd services/api && python -m pytest -q \
  tests/test_pipeline_incident_review.py tests/test_soak_window_gate.py
# 69 passed
```

  覆盖：结构契约（源码零子进程/零网络/零 env/零墙钟 token，socket+
  subprocess 双阻断端到端）；2026-09-21 事件形态回放（状态域/无工件
  真空/超时+补录两态/skipped 不计失败/mixed 窗口/最新失败保持开放）；
  门 open/closed 全原因面（warn/critical/partial 残留逐条清单、间隔
  超限、行数不足、N=1 边界、尾部外 warn 不阻断）；--anchor 写出/拒绝/
  anchor-exists 重启保护；零墙钟（两次运行逐字节相同、锚定记录
  generated_at == 锚样本）；输入拒绝全矩阵（未知 stem、stage 越词汇、
  monitor skipped 结构不变量、schema 漂移、started_at 重复/非时序、
  history malformed/重复/非时序/项目冲突/空文件/行数超顶、参数越界、
  consecutive-ok > retention、真实文件面 symlink）；隐私（标记 token
  绝不进入输出）；CLI 默认值与 ops README 文档化。

- 聚焦回归九件套（本切片触碰面 + 依赖面，follow-up 轮最终口径）：

```bash
cd services/api && python -m pytest -q \
  tests/test_production_monitor.py tests/test_monitoring_pipeline.py \
  tests/test_monitoring_pipeline_task.py tests/test_monitoring_history.py \
  tests/test_monitoring_insights.py tests/test_pipeline_incident_review.py \
  tests/test_soak_window_gate.py tests/test_soak_stability_audit.py \
  tests/test_release_readiness.py
# 831 passed in 11.01s
```

  （分件实测：production_monitor **243**（含 M14-79 日志时间界 15 项
  新增用例）、monitoring_pipeline **70**（含超时上调 pin 1 项新增）、
  monitoring_pipeline_task **79**、monitoring_history **119**、
  monitoring_insights **125**、pipeline_incident_review **38**、
  soak_window_gate **31**、soak_stability_audit **51**、release_readiness
  **75**——合计 831。首轮两件套 69 + 五件套 314 为 follow-up 前口径。）

- 静态：`ruff check`（含 CI 口径 `--select ISC` 复核）全部触碰文件
  All checks passed；`py_compile` 全部触碰 .py 通过；`git diff --check`
  干净。全量 pytest 未跑（3939+ 用例本机
  WSL-bash 环境存在与本切片无关的已知环境失败；聚焦面全绿，完整回归
  由 CI 承担）。

## 4b. follow-up 轮（supervisor 复核 8931545 后，单 follow-up commit）

supervisor 独立复核发现三类问题，全部修复（详见本节）：

1. **test lint**：`test_pipeline_incident_review.py` parametrize 三处
   ISC004（隐式字符串拼接）——改为单行字符串字面量。
2. **root cause 不自愈**：`production_monitor` 的 log-errors 对
   `docker logs --tail` 全尾计数无时间界（supervisor 只读核查：postgres
   tail 78 行、同 6 条陈旧错误、最新 2026-09-21T04:29:19Z；13:15/13:30
   无新 postgres 错误仍 warn）。修复 = **时间戳感知的当前区间记账**：
   `docker logs` 恒带 `--timestamps`（白名单放行，布尔/带值旗标拆分校验）；
   仅计时间戳 ≥ 基线（最新合法 prior 完整工件 started_at_utc，与 M14-23
   restart 基线同源、先于采集解析）的错误行；早于基线计入
   `stale_error_lines` 显式入档；**时间戳不可解析 → fail-closed 恒计入
   当前区间**（`unparsed_error_lines` 计数）；基线缺失/不可用 → 回退
   `full-tail`（= 修复前保守口径）。`error_total_tail` / `latest_error_at`
   / `accounting{basis,window_start_at}` 照实入档；阈值与退出码零变更；
   阈值/日志/容器/计划任务/history 均未改动。
   - **评审中发现的实现 bug 一并修复**：`DOCKER_LOG_TS_RE` 捕获组不含
     `Z` 却按含 `Z` 的 `TIMESTAMP_FORMAT` 解析——一切合法 docker 时间戳
     都会不可解析（fail-closed 全计入，恢复语义退化为 full-tail）。
     修复 = 专用无 Z 格式 `DOCKER_LOG_TS_FORMAT` 解析后补 UTC。
   - **fail-closed e2e 期望不一致修正**：无前缀错误行 2 条不可能达
     warn 阈值 5——测试改为 6 条（更强而非更弱），实证未解析行触发
     可见 warn。
   - e2e 验收（supervisor 实证形态回放）：同 6 条陈旧错误（04:29:19Z）
     在基线（05:15:01Z）之后 → postgres error_total=0、tail=6、stale=6、
     overall **ok**——恢复不再被陈旧错误永久阻塞；基线后新错误照常
     warn（无遮蔽反向证明）。
3. **history 超时边界漂移**（13:45 轮 45.522s vs 45s，加上 12:00/12:45
   两轮 45.206/46.955s 共三次刚过界即杀）：采用**有依据的有界上调**——
   `monitoring_pipeline.HISTORY_TIMEOUT_DEFAULT` 45s → **90s**（≈1.9×
   最坏观测 46.955s；正常完成轮实测 0.3–7.2s；硬顶 120 不变；默认总和
   480+90+15=585s < PT12M=720s 执行时限，硬顶和 710s < 720s 亦不变）。
   调度器内层超时仍恒先于外层 ExecutionTimeLimit（防调度器击杀留 stale
   lock 的原设计保持）；**超时事实照常入档**（status=timeout /
   failure_detail=stage-killed-after-timeout，pipeline_incident_review
   照常归因为执行域瞬态）——不隐藏、不改记成功、不动计划任务（任务
   调用的是管道脚本，管道默认值生效）。新增 pin 测试
   `test_history_timeout_default_raised_45_to_90_m1479`。

## 5. 诚实边界与剩余阻塞（不伪称，逐项可执行收口）

1. **生产仍运行 m14-70 镜像**（2026-09-19 M14-70 切换后未再变更）；
   本切片零生产触碰，不构成任何部署。
2. **log-errors 时间界修复与 history 超时上调均为代码变更，待 supervisor
   复核合并后才对真实调度生效**：当前生产计划任务仍运行 main 的
   `production_monitor`（全尾计数）与 45s 管道默认——warn 尾部与超时
   事实在合并前**不会自愈**；合并后首两轮（重建基线 + 陈旧错误出界）
   即按新语义恢复。本切片未运行任何真实 execute。
3. **long-soak 门仍 missing/blocked**：当前历史尾部 warn 未消退，门
   `soak_window_gate` 如实 closed——必须等尾部出现 8 连续干净样本后
   才能锚定新窗口，再等真实 24h 干净后由 soak 审计判定。本切片交付的
   是这条路径的**门禁与证据形态**，不是 soak 通过本身。
4. **03:30 槽位缺失原因不可定证**（无报告/无工件/无行；可能的调度
   未触发或机器休眠均无工件佐证）——如实记录为数据空档，不猜测。
5. **12:30 运行与任务简报的差异**：简报称 12:30/12:45 均「history 超时
   46.955s」；工件实证 12:30 是 monitor exit 2 无工件、12:00（45.206s）、
   12:45（46.955s）与 13:45（45.522s）才是 history 超时——证据 README
   以工件为准。
6. 其余发布门（production-preflight / backup-restore / audit-chain /
   governance / provider-smoke / release-approval / turn-tls）状态与
   M14-78 §6 相同，全部待生产运维窗口或人工审批，本切片不触碰。
7. history.jsonl 是活文件：§2/§3 哈希是取证时点快照，重跑工具会得到
   更新样本的等价结构（工具输出确定性以输入为锚，零墙钟）。
8. `release_ready=false`、`production_ready=false` 不变；发布审批永远
   人工（human-only），本切片不签署、不代拟、不合成任何审批。
