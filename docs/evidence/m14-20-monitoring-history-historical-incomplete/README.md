# M14-20 监控历史索引历史 incomplete 工件修复 — 交付证据归档

- 日期：2026-09-13（开发切片交付）
- 分支：`fix/m14-20-monitoring-history-fix`（基于 `main@4c9458f`，即 PR #95
  merge commit `4c9458f1f6d05857edb209f463887b48b7774ba4`，本地 git 可
  验证）。本 Claude 开发回合独占 worktree
  `m14-20-monitoring-history-fix`，仅做**一个本地 commit**；supervisor
  审查与 remote 发布（push/PR/合并）在其后进行——该表述是开发时点快照。
- 状态：**索引契约修复 + 聚焦契约测试交付（本地）**；本回合仅额外运行
  history 工具只读面对真实 canonical 源目录做端到端实证（见下）——
  **零 M14-14 管道执行、零生产触碰**（未启停任何服务/容器/计划任务/
  voice 进程/模拟器，未读取 env/密钥）。
- 入库变更：`tools/ops/monitoring_history.py`（识别窄类跳过 + 计数显式
  + `no-complete-sources`，其余语义不变）、
  `services/api/tests/test_monitoring_history.py`（+9 项，74→83）、
  `services/api/tests/test_monitoring_insights.py`（+1 项委托回归，90→91）、
  本 README，以及 PROJECT_STATUS / ROADMAP / CHANGELOG /
  `tools/ops/README.md` 同步。
- 结论口径（诚实边界）：**修复的是「历史 incomplete 工件毒化整批索引」
  这一契约缺陷并经真实源目录实证；管道下一轮定时触发的实际 exit 0 由
  supervisor 合并后观测；`production_ready=false` 不变**。

## 根因（真实工件面逐件核实，2026-09-13）

- 默认源目录 `.verify/artifacts/m14-12-production-monitoring/`（canonical
  仓库，gitignored）时点 33 份 `monitor-*.json`，其中 **31 份**为
  2026-09-12 17:37 ~ 2026-09-13 01:00（栈修复期间，M14-16..19 era）monitor
  自产 incomplete 工件：逐件核对全部具完整 monitor 身份
  （`schema_version==1` / `tool==tools/ops/production_monitor.py` /
  `milestone==M14-12` / `mode==execute`）且 `partial=true` +
  `overall_status=incomplete`，其 `endpoints` 采集器 `status=failed`
  （`cosyvoice-health` 端点 failed、`http_status`/`latency_ms` 为 null）。
- monitor 契约（`tools/ops/production_monitor.py:744-745, 863-865`）：
  任一采集器失败 → `partial=true` → `overall_status=incomplete`——二者
  **恒共现**；ok/warn/critical 恒 `partial=false`。故这些工件的采集器
  事实**合法**含 failed，无法经 M14-13 完整校验（其要求全部采集器 ok），
  不存在「校验后入档」路径。
- M14-19 栈修复 + PR #95 合并后，monitor 恢复产出完整 warn 报告并
  exit 0（如 `monitor-20260913-010414.json`），M14-14 管道据此进入
  「monitor exit 0 → history 运行」路径；但 `monitoring_history.py`
  `build_samples` 逐件加载、任一违规即抛错——第一份历史 incomplete
  即令整批 EXIT 2（`overall-status`）零输出，**监控历史永久零索引**
  （每轮 PT15M 重复失败）。

## 修法（最小 robust：识别窄类 → 整件跳过）

`tools/ops/monitoring_history.py`：

- 新增纯谓词 `is_recognized_incomplete(data)`：识别类 = **完整 monitor
  身份**（`schema_version==1` ∧ tool ∧ `milestone==M14-12` ∧
  `mode==execute`）+ `overall_status=="incomplete"` ∧ `partial is True`
  共现对（`is True` 严格同一性）。命中 → `load_sample` 返回 None 跳过
  不入档；`skipped_incomplete_count` 计数显式于 summary dict、
  `history-summary.md` 新增 bullet（「历史不完整工件：跳过 N 条…」）、
  stdout 新增行（仅 N>0 时；既有行逐字节不变）。**跳过件内容绝不进入
  任何输出——仅计数**；symlink 防御仍覆盖全部候选（含跳过件）；源文件
  绝不改动/删除。
- **fail-closed 面零放宽**：完整 ok/warn/critical 样本走原样严格校验
  （malformed 完整报告仍整体拒绝）；其余任何 incomplete/partial 形态
  ——身份不符（tool/milestone/schema_version/mode 任一）、
  incomplete+partial=false、完整状态+partial=true 等矛盾组合——仍
  fail-closed（既有 74 项契约测试原样通过）；候选全为 incomplete（零
  完整样本）→ 新固定词汇 `no-complete-sources` 拒绝（输出零写入）。
- **委托面稳定性**：`build_samples(store, source) -> list` 签名不变
  （薄 shim 委托新 `discover_and_classify`）——`monitoring_insights.py`
  monitor 工件目录形态委托调用零改动，跳过语义同样生效（固定词汇拒绝
  原因原样透传）；`history.jsonl` 记录 schema 零改动
  （`HISTORY_SCHEMA_VERSION` 仍 1）。

## 本回合验证（canonical venv Python 3.11.15 / pytest 9.1.1 / ruff 0.16.5）

1. **TDD**：新增行为测试在实现前全失败于根因症状（EXIT 2 +
   `overall-status`）——history 3 项 + insights 委托 1 项；实现后全绿。
2. **聚焦契约测试**：`test_monitoring_history.py` **83 passed**（74 既有
   零回归 + 9 新增：真实形状混档入档/跳过计数/源逐字节不变、全
   incomplete 拒绝、识别窄类 ×5、跳过件不参与去重、留存仅作用完整样本）。
3. **邻居回归**：`test_monitoring_insights.py` **91 passed**（含新增
   委托回归）、`test_monitoring_pipeline.py` **52 passed**（含
   `test_regression_history_refuses_empty_source` 空=零候选仍拒）、
   `test_monitoring_pipeline_task.py` **79 passed**、
   `test_production_monitor.py` **189 passed**——四套件合计 **411
   passed** 零回归。
4. **静态**：`ruff check services/api tools/ops/monitoring_history.py`
   全过；`py_compile tools/ops/monitoring_history.py` 过；
   `git diff --check` 过。
5. **真实 canonical 源目录端到端实证**（M14-13 回填回合同款形态：本
   worktree 以 `--source-dir` 只读 canonical
   `.verify/artifacts/m14-12-production-monitoring/`、输出仅落本 worktree
   gitignored `.verify/artifacts/m14-13-monitoring-history/`；运行时点
   源目录已增至 **39 工件 = 31 历史 incomplete + 8 完整（1 ok + 7 warn，
   管道每 PT15M 新增完整 warn）**）：

   | 验证项 | 结果 |
   |---|---|
   | 修复前症状 | 同目录同参数 → EXIT 2 `overall-status` 零输出（根因复现） |
   | 修复后退出码 | **exit 0** |
   | 入档记录 | 恰 **8 条**完整样本（时间升序；`ok=1 warn=7 critical=0`；全 `partial=false`；输出零 incomplete 内容） |
   | 跳过计数 | stdout `[history] 跳过历史 incomplete 工件: 31`；摘要 MD「历史不完整工件：跳过 31 条」 |
   | 源只读性 | 39 份源文件 SHA-256 运行前后**逐字节不变**（含 31 份跳过件）、零删除 |
   | 可复现性 | 连跑两遍输出 `cmp` **逐字节相同**（零墙钟设计保持；`history.jsonl` SHA-256 `6b15c296bbb29773…`、`history-summary.md` `c881b0c321930b1d…`） |
   | discovered 口径 | 「发现 8 / 保留 8」——records_discovered 仅计完整样本，跳过件单列 |

## 安全边界（本回合零违背）

- 零生产执行：未运行 M14-14 管道（monitoring_pipeline.py）本体、未运行
  monitor execute、未调用 Docker/HTTP/计划任务/语音/恢复；未触碰任何
  生产服务、容器、计划任务、voice 进程、模拟器或用户进程。
- canonical 仓库与真实 `.verify` **只读**（源工件 SHA-256 前后不变实证）；
  输出只写本切片 worktree 的 gitignored `.verify`，绝不入库。
- 跳过件内容绝不进入任何输出（仅计数）；零密钥/零 env 原文/零 token/
  零原始日志行入档；不触碰 untracked `.claude/`。

## 边界（诚实口径，不过度引申）

- **不删除/不改写任何历史源工件**：跳过而非清理是有意设计——31 份
  incomplete 工件是 M14-12 只读证据面，如实记录栈修复期；
  `skipped_incomplete_count` 让它们**可见而非消失**。未来若 supervisor
  决定归档/清理源目录，属独立生产变更决策，与本契约修复解耦。
- 管道 history 步骤自此修复的实际生效（下一轮 PT15M 触发 exit 0 →
  pipeline exit 0）由 supervisor 合并后观测；本回合不宣称管道恢复。
- `production_ready=false` 不变；监控历史可用性不构成 production
  readiness 宣称。
