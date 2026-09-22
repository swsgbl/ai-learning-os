# M14-102 监控自然窗口证据回填（natural scheduled monitoring evidence backfill）

- 切片性质：**docs-only 回填**——零代码/零测试/零工具/零工作流改动，不 push、
  不开 PR；分支 `docs/m14-102-monitoring-natural-evidence`（独立 worktree
  `m14-102-monitoring-natural-evidence`，基于 main
  `3bb95a17898d3e4aea71570deee9d173945b5f80`（PR #188 merge = M14-101
  合入，精确基点），单 local commit）。
- 本回填回合零生产触碰：**未运行、未查询、未修改计划任务
  `AIOS-Monitoring-Pipeline`，未触发任何监控执行**；仅对 canonical
  gitignored 工件做只读核验（哈希/字节/字段），零写入。

## 1. 定位：M14-101 合入后的自然窗口观测

M14-101（PR #188，合并提交时间 2026-09-23 06:45:08 +0800 =
2026-09-22T22:45:08Z）合入 15 分钟后，监控管道的下一次自然调度窗口
（PT15M 节奏的 :00 边界）于 **2026-09-22T23:00:01Z**（本地 2026-09-23
07:00:01 GMT+8）自行发生。本切片把该**自然轮**的生产工件事实回填为可审计
仓库证据，兑现 M14-101 证据 README §7 的后续观测项：「下一次获准窗口让
真实计划任务自然运行一轮，观测 history 步时长是否回到秒级（M14-79 观测的
正常轮 0.3–7.2s）」。

历史事实保持不变（互补而非改写）：

- M14-101 切片本身未运行真实计划任务（其诚实边界原文不动）——本切片是
  其合入**之后**的自然窗口观测；
- 本切片同样没有触发运行——运行由调度器自然发生（自然性佐证见 §2）。

## 2. 工件核验（canonical 只读，双轨复核）

- 路径（canonical 主仓 gitignored 证据历史，不入库、不复制，本 README
  仅记录哈希/字节）：`D:\AI Learning OS\ai-learning-os\.verify\
  artifacts\m14-14-monitoring-pipeline\pipeline-20260922-230004.json`
- 字节数 **5871**、SHA-256
  **F913CEE7078DC308F7E77DB8954011FC865B7C94C3933981B107DFDCE01BFC04**
  ——回填回合以 `wc -c` + `sha256sum` 与 Python `hashlib` 双轨独立重算，
  与任务宣告值一致。
- **自然性佐证**：文件 mtime `2026-09-23 07:00:04.288571700 +0800`
  与报告 `ended_at_utc=2026-09-22T23:00:04Z` 精确一致——文件由该轮
  运行自身写出，早于本回填回合开始读取约 30 分钟；同目录存在长期积累的
  历次 pipeline 报告（自 2026-09-12 起），为调度器的常态产物目录。
- JSON 字段断言（Python `json.load` 逐键断言，全部通过）：见 §3。

## 3. 工件事实（pipeline 报告，schema_version 1）

报告自述：tool `tools/ops/monitoring_pipeline.py`、milestone `M14-14`、
mode `execute`。

| 项 | 值 |
|---|---|
| started / ended（UTC） | 2026-09-22T23:00:01Z → 2026-09-22T23:00:04Z |
| overall_status | **ok** |
| monitor 步 | ok / exit 0 / **1.375s**（预算 480s）/ 未超时 / 产物 `monitor-20260922-230001.json`（19,009 bytes，带 SHA-256） |
| history 步 | ok / exit 0 / **1.263s**（预算 90s）/ 未超时 / `history.jsonl`（755,804 bytes）+ `history-summary.md`（1,894 bytes），均带 SHA-256 引用 |
| insights 步 | ok / exit 0 / **0.208s**（预算 15s）/ 未超时 / `insights.json`（14,585 bytes）+ `insights-summary.md`（5,467 bytes），均带 SHA-256 引用 |
| lock | `pipeline.lock` acquired=true（23:00:01Z）/ released=true |

三步 status/exit_code/timed_out 逐键断言：`ok`/`0`/`false` 全部成立；
固定名 history/insights 产物以 SHA-256 引用（非
`stale-preexisting-not-cited`）。

## 4. 与 M14-101 的接续判定

- **该轮运行的是合入后（含 M14-101）的管道代码**：报告 `boundaries`
  数组包含 M14-101 新增的同轮 provenance 边界原文（「fixed-name stage
  artifacts (history/insights) are cited with a SHA-256 only when the
  same run provably wrote them (pre-step (size, mtime_ns) fingerprint
  baseline …); preexisting unchanged files are recorded as
  stale-preexisting-not-cited」——该文本只存在于 M14-101 之后的
  `PIPELINE_BOUNDARIES`）；且 PR #188 合并时间（22:45:08Z）早于运行
  15 分钟。
- **history 步回到秒级**：1.263s 落在 M14-79 记录的正常轮量级
  （0.3–7.2s）内，M14-101 针对的 ~90s 冷缓存超时形态（生产观测
  90.202s、LastTaskResult=1）未再出现。注意：这是**单轮自然观测**，
  不是修复效果的统计证明——冷缓存条件（计划任务唤醒后 OS 文件缓存被
  换出）是否在所有后续窗口均消除，由后续自然轮继续观察。
- **超时数值未放宽**：history 预算仍为 90s（M14-79 口径），M14-101
  「零超时数值变更」的承诺在自然轮得到保持。

## 5. 诚实边界

- 本切片是**被动观测回填**：没有触发任何新的监控执行；工件由调度器
  自然轮产生（§2 佐证）。
- 零生产触碰：容器/计划任务/DB/MinIO/语音/secrets/代理生命周期零变更；
  canonical `.verify` 只读零写入；本 worktree 不复制工件入库。
- **单轮 `overall_status=ok` ≠ production readiness**：不证明生产就绪、
  不授权任何 cutover——工件自身 boundaries 亦声明「single pipeline
  success is not production readiness; scheduled registration is
  supervisor-only; this tool never claims production ready」。
- `release_ready=false` / `production_ready=false` 不变；发布门状态
  不因本切片改变（M14-97 口径：blockers 仅剩
  `long-soak:not-staged-required`，其到期审计/导出对真实 history 的
  执行仍是独立运维动作）。
- **生产仍运行 m14-70 镜像**；除非 current main 的生产切换另行完成，
  该事实不变。
- M14-101 的历史事实不变：M14-101 切片本身未运行计划任务（本切片是其
  合入后的自然窗口观测）。

## 6. 后续（留 supervisor 决策）

- 继续观察后续自然轮的 history 步时长（尤其冷缓存唤醒窗口）是否持续
  秒级，累积对 M14-101 修复的多轮佐证；
- long-soak 到期审计/导出（M14-93 runner 对真实 history 的执行）仍是
  独立运维动作；
- current main 生产镜像切换（如获准）是独立切片，不因单轮 ok 自动发生。
