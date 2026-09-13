# M14-22 生产监控三步管道验收回填 — 证据归档

- 日期：2026-09-13（supervisor 真实生产验收 + docs-only 回填；含 supervisor
  R1 修正：12:45 第二轮调度成功，验收面由「首轮成功」增强为「两轮连续
  成功」）
- 分支：`docs/m14-22-monitoring-pipeline-production-acceptance`（基于
  `origin/main@1b89d91`，即 PR #98 merge commit
  `1b89d9182566ba0ff4afe8f02893acefae94fb43`，本地 git 可验证）。本 Claude
  开发回合独占 worktree，仅做**一个本地 commit**（R1 经 amend 并入同一
  commit）；supervisor 审查与 remote 发布（push/PR/合并）在其后进行。
- 切片性质：**docs-only 回填**——零代码/零测试/零 workflow 改动；不触碰任何
  生产服务、计划任务、容器、voice 进程；不运行 `monitoring_pipeline`
  execute；不复制任何 gitignored 原始工件入库（本文件只记录文件名、
  SHA-256、字节数等安全摘要）；不含绝对本机路径、生产容器 ID、密钥或
  env 值。
- 状态：**M14-21 遗留边界「管道真实三步运行与下一轮观察」闭环为已验收**——
  真实计划任务**两轮连续调度成功**（12:30 与 12:45，均 Last Result=0、
  monitor → history → insights 三步全 ok），insights 产物首轮产出、第二轮
  刷新；`production_ready=false` 不变。

## 1. 合并与 CI 事实（supervisor 验收，2026-09-13）

- PR #98（M14-21 `feat/m14-21-monitoring-insights-pipeline`）已合并 main：
  merge commit `1b89d91`（完整
  `1b89d9182566ba0ff4afe8f02893acefae94fb43`，parents `9977ece`（main 侧）+
  `1f131aa`（feature head），本地 git 可验证）。
- 合并后 main CI run `34737912550`：最终 **completed/success，5/5 job
  success**（Web、Android、Release tools、Docker、API）。

## 2. 计划任务事实（canonical 工具只读 status 核对）

- 计划任务 `AIOS-Monitoring-Pipeline`：canonical 工具
  （`tools/ops/monitoring_pipeline_task.py status`，只读五态）返回
  **installed**。
- Action / 参数 / cwd / Hidden / 触发器 / 间隔 / 时限**逐项匹配** canonical
  注册画像（`tools/ops/README.md` 留档：PT15M 重复间隔、PT12M 执行时限）。
- **两轮连续真实调度成功**（均为 PR #98 合并后）：
  - 第一轮 Last Run Time **2026-09-13 12:30:01（+08:00）**，Last
    Result=**0**（管道报告 started_at `2026-09-13T04:30:01Z` 与之逐秒
    对应）；
  - 第二轮 Last Run Time **2026-09-13 12:45:01（+08:00）**，Last
    Result=**0**（管道报告 started_at `2026-09-13T04:45:01Z` 与之逐秒
    对应）；下一轮 13:00。

## 3. canonical 管道报告（两轮真实调度产物，gitignored 不入库）

两轮报告位于 gitignored `.verify/artifacts/m14-14-monitoring-pipeline/`：
`pipeline-20260913-043007.{json,md}`（12:30）与
`pipeline-20260913-044503.{json,md}`（12:45）；此处仅记录安全摘要：

- 两轮均 `overall_status=ok`、三步序列 **monitor → history → insights**
  全部 status=ok、exit 0，lock `pipeline.lock` acquired/released =
  **true**（无 stale lock）：
  - 第一轮（`2026-09-13T04:30:01Z`–`04:30:07Z`）：monitor 1.756s /
    history 3.055s / insights 0.432s；
  - 第二轮（`2026-09-13T04:45:01Z`–`04:45:03Z`）：monitor 1.27s /
    history 0.13s / insights 0.143s。
- 各步产物（文件名 + SHA-256 + 字节数，均 gitignored 不入库）：

| 轮次 | 步骤 | 产物 | SHA-256 | 字节 |
|---|---|---|---|---:|
| 12:30 | monitor | `monitor-20260913-043002.json` | `56d4e5e44e5db1e7cd137ae8ea3fa80d511390f20cbcdb496aaf0fadb66192aa` | 14336 |
| 12:30 | history | `history.jsonl` | `e9751e4546980169561c80423eb4008cb043d6d23745866dcff5bc95dfc2f416` | 16725 |
| 12:30 | history | `history-summary.md` | `5bc5d2159e202a312e4e8e406b75d4585cfca21360f0fa37da297f8a3e029af7` | 1693 |
| 12:30 | insights | `insights.json` | `d45c0bccff754eeaa474ab2c6665864ab74385c61f6b2ea6c9941b83b1f6b17b` | 6112 |
| 12:30 | insights | `insights-summary.md` | `89e438d1ff5ca47b13660c28fe03e1fa999d0cde5712acf025f687b7c8ac75b9` | 2740 |
| 12:45 | monitor | `monitor-20260913-044501.json` | `6006ce2d59de6ee8ebf9d43c6771b791329813c78e442fdac0aa16e3267411d1` | 14346 |
| 12:45 | history | `history.jsonl` | `51d4ea5b2c566ddaf8fff5f70a83f63927ccd7ec3ac2856c5256866a92ce06d2` | 17711 |
| 12:45 | history | `history-summary.md` | `32039df3f5a2bb5a494dc0381a6f402ce823d0a18d8be942dcfdce49711f6396` | 1693 |
| 12:45 | insights | `insights.json` | `8c82612d87c2bc511dee38cf7b2955c2c83395a3307b7ba95b54929cea6f1cea` | 6277 |
| 12:45 | insights | `insights-summary.md` | `ac661d11fd6edd591332e0e4465e6227aad9012aead8f6d9092435d32b1179c6` | 2780 |

- **insights 产物首次由真实计划任务产出（12:30），并在第二轮（12:45）
  刷新**（history.jsonl 17711 字节 = 新样本入档后重写，insights 18 样本
  口径见第 5 节）——M14-15 insights 目录自此获得真实写入者并被持续管道
  持续刷新，M14-21 修复的「insights 默认输入 = history canonical 输出」
  链路端到端生效。

## 4. monitor 工件事实（warn 定性，两轮同构）

gitignored `.verify/artifacts/m14-12-production-monitoring/` 下两轮工件：
`monitor-20260913-043002.json`（12:30）与 `monitor-20260913-044501.json`
（12:45）（SHA-256 见上表），两轮结论同构：

- `overall_status=warn`，`partial=false`；阈值计数 ok=33 / warn=1 /
  critical=0；`monitoring_ready=false`。
- **6/6 服务 healthy/running**；**5/5 端点 HTTP 200**，延迟全部正常
  （两轮最大 4.59ms，远低于 latency_warn=1000ms）。
- **唯一 warn**：api 容器 `restart_count=1` 达到 `restart_warn=1` 阈值
  （`container-restarts` 检查）——两轮均为同一静态累计值。
- **定性（重要）**：这不是服务故障——当前事实支持「**静态累计
  restart_count 阈值触发持续 warn，需后续阈值演进/恢复语义处理**」（见
  第 7 节 M14-23 建议）；不得把该 warn 宣称为生产服务异常。

## 5. insights 历史样本事实（12:45 第二轮刷新后）

gitignored `.verify/artifacts/m14-15-monitoring-insights/`
`insights-summary.md`（SHA-256 见第 3 节表，生成时间戳取自最新样本
`2026-09-13T04:45:01Z`）：

- **18 个历史样本**（项目 `aios-m14-03-production-rehearsal`，较首轮
  +1 = 12:45 新样本入档）：**ok=1 / warn=17 / critical=0**
  （availability=1/18）。
- 最新样本 `2026-09-13T04:45:01Z` 为 warn：**服务全 healthy、端点全
  200**——与第 4 节 12:45 monitor 工件逐项一致。
- **当前 warn 连续 17 条**（`2026-09-13T01:04:14Z` ~ `04:45:01Z`，恢复
  转移 0 次）；唯一阈值告警来自 api `restart_count=1`（最近样本阈值计数
  ok=33 warn=1）。
- **历史样本本身是本地只读工件，不可改动/删除**（M14-13 起的既定边界：
  源工件是只读证据面）。

## 6. 验证口径（全部只读，零生产触碰）

supervisor 验收（2026-09-13）与本次回填（含 R1 修正）的核对命令口径：

- **计划任务**：canonical 工具 `python tools/ops/monitoring_pipeline_task.py
  status`（只读五态查询，零 mutation；本回填回合未再运行——事实为
  supervisor 验收时点结果）。
- **CI**：按 run ID 只读查询 main CI run `34737912550`（completed/success
  5/5；本回填回合未发起网络查询，记录 supervisor 验收事实）。
- **工件**：直接读取 gitignored `.verify/artifacts/` 下 canonical 报告与
  产物；回填回合已独立重算 SHA-256 与字节数——首轮（12:30）
  `insights.json` / `insights-summary.md` / `monitor-20260913-043002.json`
  与第二轮（12:45）五份产物（`monitor-20260913-044501.json` /
  `history.jsonl` / `history-summary.md` / `insights.json` /
  `insights-summary.md`）均与两轮管道报告及本文件留档值**逐项一致**。
- **git**：本地只读核对 `origin/main == 1b89d91`、merge parents
  （`9977ece` + `1f131aa`）、`Merge pull request #98 …` 提交主题。
- 本切片**明确不运行**：`monitoring_pipeline` execute / 计划任务
  install·uninstall·触发 / 任何生产服务、容器、voice 进程的启停或改动 /
  任何会触碰生产的命令。

## 7. 结论、边界与下一步

**结论**：

- M14-14（管道 + 调度 readiness）、M14-20（history 修复）、M14-21（三步
  序列 + insights 输入对齐）自此经**真实计划任务两轮连续端到端验收**：
  注册态 installed 且逐项匹配，12:30 与 12:45 两轮 Last Result=0、三步
  全 ok，insights 产物首轮产出、第二轮刷新且哈希入档。
- M14-21 开发时点遗留边界「管道真实三步运行与 PT15M 下一轮观察」**闭环为
  已验收**（本切片 M14-22）。
- **口径精确性**：两轮成功证明的是**重复调度执行（两轮）**，**不构成
  长期稳定性证明，也不构成 production readiness 宣称**。

**边界（诚实口径，`production_ready=false` 不变）**——仍未完成：

- 外部告警接入（当前仅本地工件洞察，不接任何外部告警系统）；
- 指标时序存储/查询（当前 history.jsonl 有界留存 ≠ 时序数据库）；
- **阈值随时间标定 / 告警语义演进**：静态累计 `restart_count` 与新增
  restart 不区分、无恢复态语义——健康栈（服务全 healthy、端点全 200）
  因一次历史 restart 长期停留 warn（已 17 连）；
- 真实客户端验收；>60s / 真实负载 / 跨机长稳；AGC 发布；更长时间窗口的
  调度连续性观察（当前实证窗口为两轮）。

**下一步建议（M14-23，不在本切片实施）**：

- **M14-23「监控告警语义修复/阈值演进」**：区分静态累计 `restart_count`
  与新增 restart、引入恢复态语义，目标是不让健康栈长期停留在 warn；
  仍不接外部告警的前提下可先修语义。
- 依据：第 5 节 17 连 warn 的生产事实（服务全 healthy、端点全 200、唯一
  告警为 api `restart_count=1` 静态阈值）——持续 warn 是告警语义问题而非
  栈故障信号。
