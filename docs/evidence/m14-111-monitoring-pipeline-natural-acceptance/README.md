# M14-111：监控管道四步序列自然调度验收（docs-only 回填）

- 切片：分支 `ops/m14-111-monitoring-pipeline-natural-acceptance`（独立 worktree
  `m14-111-monitoring-pipeline-natural-acceptance`，基于 main
  `196da05a6e1e3c0971c1f93c84d9bcb1d46f6e62`（PR #198 merge = M14-110
  合入，精确基点）），单次 local commit（不推送、不建 PR）。
- 性质：**docs-only 证据回填**。零生产代码/测试/CI/调度配置/脚本/tracked
  JSON 工件改动；零生产触碰（容器/服务/进程/调度器/DB/MinIO/secrets）；
  本切片**未运行 monitoring_pipeline execute、未调用任何生产工具**——
  自然轮由既有计划任务 `AIOS-Monitoring-Pipeline` 自身完成，本切片只对
  supervisor 已验收的事实做只读复核与文档回填。
- 事实来源：Codex supervisor 的独立自然轮验收；本切片在 canonical
  gitignored 证据目录（`D:\AI Learning OS\ai-learning-os\.verify\artifacts\`）
  上**只读**复核（逐字节 SHA-256 + 尺寸 + JSON 字段逐键断言），全部一致。

## 1. 自然调度轮事实（supervisor 验收 + 本切片只读复核一致）

- 自然轮报告：`.verify/artifacts/m14-14-monitoring-pipeline/
  pipeline-20260923-130003.json` 与同名 `.md`（gitignored canonical，
  只读核验，不入库、不复制进本仓库）。
- UTC 窗口：`started_at_utc=2026-09-23T13:00:01Z` →
  `ended_at_utc=2026-09-23T13:00:03Z`；`mode=execute`（PT15M :00 边界
  自然触发，非人工调用）。
- `schema_version=1`；`config.sequence` 精确为
  `[monitor, history, insights, calibration]`；`overall_status=ok`。
- 各步 status / exit_code / duration：
  - monitor：ok / 0 / 1.210s（默认预算 480s）
  - history：ok / 0 / 0.297s（默认预算 90s）
  - insights：ok / 0 / 0.146s（默认预算 15s）
  - calibration：ok / 0 / 0.138s（默认预算 5s，M14-110 新增）
- 管道锁 acquired + released（`pipeline.lock` 创建于
  2026-09-23T13:00:01Z）。
- calibration 命令身份精确为
  `['<python>', 'tools/ops/monitoring_threshold_calibration.py', '--format', 'json']`
  （M14-110 固定形态白名单，恒不带 `--history`/`--samples`/阈值参数——
  零校准注入面）。
- 首轮判定：13:00 轮是该 canonical 目录中**首个 `stages` 含 calibration
  的报告**（12:45 及更早各轮 `config.sequence` 均为三步
  `[monitor, history, insights]`）——即 M14-110 合入后第一个自然四步轮。

## 2. 工件核验（13:00 轮绑定，复核时点早于 13:15 后续轮）

| 工件（canonical 只读路径） | bytes | SHA-256 |
|---|---|---|
| `m14-12-production-monitoring/monitor-20260923-130001.json` | 19005 | `76aed8fe8ed42fa1ac8f92dec65f52d4de9ea609a3fb99348ec457e8e8d069e4` |
| `m14-13-monitoring-history/history.jsonl` | 755728 | `8edcf9d451c896f3966a62d58897a2bc778fd7480da6312fd36a096518e014a2` |
| `m14-13-monitoring-history/history-summary.md` | 1892 | `4912daa07c82f8443b0a37f0b87f896482fd13c7355842d119a5e68c5ad8385d` |
| `m14-15-monitoring-insights/insights.json` | 12180 | `51fb790043bec519ef263ec4354f80575efca7df92de39827bb37a581958d0a1` |
| `m14-15-monitoring-insights/insights-summary.md` | 4704 | `378b57d2fc93a81a29ba3465f4644bce366dc334256c6e9c11ce7e0d24249af3` |
| `m14-14-monitoring-pipeline/calibration.json`（13:00 轮绑定） | 8909 | `2b53fe9b82eea5534b44f4d31128b5c28ea78563a442bd7238da71b3d07ed9f0` |

- **核验时点**：以上与 13:00 管道报告内引用的 SHA-256/bytes 逐一
  一致，并由本切片在磁盘上独立重算复核——六项全部 MATCH；复核完成于
  2026-09-23T13:0xZ，**早于 13:15 后续自然轮**。上表因此是 **13:00
  首轮产物的权威绑定**（= 13:00 报告内引用值），而非任一时点的
  固定名文件现值承诺。
- **固定名最新产物语义（重要）**：`history.jsonl` /
  `history-summary.md` / `insights.json` / `insights-summary.md` /
  `calibration.json` 五个固定名工件均为**最新成功轮产物**——每个
  后续成功轮按设计覆写（失败/跳过轮绝不写、绝不引用旧产物，
  M14-101 同轮新鲜度语义）；仅 monitor 工件为时间戳命名、逐轮
  不可变。**后续 2026-09-23T13:15:01Z–13:15:03Z 自然轮也已全步
  ok**（monitor 1.192s / history 0.296s / insights 0.195s /
  calibration 0.096s，报告 `pipeline-20260923-131503.json`）**并按
  设计覆写了全部固定名工件**（该轮报告对其 calibration.json 的
  绑定 = 8907 字节 / SHA-256
  `fc6bf568d0ba92e48cfbf9b49fed0006fed830ffb3d0bd1e4704c2750f23c62e`，
  当前固定名文件与之一致、已不同于上表 13:00 轮值——本切片复核
  确认）。因此**未来审计者读取当前固定名文件看到与上表不同的哈希
  属预期行为，不构成篡改**：任一时点的固定名文件应与**最新成功轮
  报告**内的绑定比对；13:00 首轮产物以 13:00 报告引用为准。
  本切片验收口径仍为**首轮（13:00）**——13:15 轮仅作事实性附注，
  不扩大为第二轮验收。
- `calibration.json`（13:00 轮）为**第四步首次由真实调度轮产出**：
  报告标注 `persisted-from-stage-stdout`（管道独占持久化，校准工具
  自身零文件写入契约不变）；8909 字节，**ASCII-safe**（逐字节复核
  零非 ASCII——M14-110 的 ensure_ascii 修复在真实调度链路下按设计
  生效；13:15 轮覆写后的当前文件亦复核为 ASCII-only）。

## 3. calibration.json 内容口径

- 样本窗口：`records_scanned=500`，取最新 `records_used=200`
  （`truncated_older_count=300`，`--samples` 既有默认值 200 生效）。
  窗口状态计数：**ok=200 / warn=0 / critical=0**；
  `newest_used_at=2026-09-23T13:00:01Z`。
- 候选阈值**建议仅为咨询性（advisory）**：本轮各建议多呈
  `applicable=false`（固定词汇原因如 `below-minimum`——真实延迟远低于
  production_monitor 配置下界时如实呈现）；**任何阈值未改变**——阈值
  变更仍走 production_monitor 显式配置 + supervisor 获准窗口。

## 4. 诚实边界

- 本证据仅证明 **M14-110 合入后的第一个自然调度四步轮端到端 ok**
  （含第四步首次产出并持久化 calibration.json）。单轮 ok ≠ 长期稳定性，
  不证明更长窗口的调度连续性、重建/重置/baseline-missing 路径。
- **不解除 provider-smoke、release approval 或任何 release gate**；
  `production_ready=false` 不变；不授权任何部署。
- M14-110 切片自身零真实管道 execute、零调度注册/改动的历史边界**保持
  不变**（该切片未运行管道）；本自然轮由既有计划任务在其合入后自行
  产生，两事实互补而非改写。
- **固定名工件（含 calibration.json）是最新成功轮产物**：后续成功轮
  按设计覆写——本 README 工件表锚定 13:00 报告引用值，不承诺任一时点
  固定名文件的现值；当前文件哈希与首轮绑定不一致是预期而非篡改
  （详见 §2）。
- 本 README 只含安全哈希/尺寸/固定词汇/UTC 时间戳：零 secrets、零原始
  子进程输出、零生产 ID、零工件副本入库（canonical 工件 gitignored
  只读核验，不入库）。

## 5. 切片验证

- 只读复核：自然轮报告 JSON 字段逐键断言（schema/sequence/各步
  status·exit·duration/锁/命令身份/窗口）+ 六工件 SHA-256/bytes 独立
  重算全 MATCH（复核时点早于 13:15 轮；固定名语义见 §2）+
  `calibration.json` ASCII-only 逐字节复核 + 首轮判定（13:00 前各轮
  均为三步）+ 13:15 后续自然轮事实附注只读复核（全步 ok、其报告
  calibration.json 绑定与当前固定名文件一致）。
- `git diff --check` 干净；`git diff --name-only 196da05a…` 仅含五个
  预期 docs 路径（本 README + PROJECT_STATUS/ROADMAP/CHANGELOG/
  tools/ops/README）。
- 单次 local commit，不 push、不开 PR。
