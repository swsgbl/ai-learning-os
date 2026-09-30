# M14-198：公网边缘 Phase 0 第三日采样与决策收口（docs-only，非代码）

- 切片：worktree `ai-learning-os-worktrees/m14-198-phase0-third-date`，
  分支 `docs/m14-198-phase0-third-date`，基于 main
  `fc3df0c5281b657374ecc4424ac7aeb4ec58c877`（PR #286 merge）。
- 定性：**docs-only 证据落档切片**——第三本地日期（2026-10-01）的
  Phase 0 采样在 canonical checkout 用 main 既有工具
  （`tools/ops/public_edge_phase0_baseline.py` 与
  `tools/ops/public_edge_phase0_decision.py`，本切片零代码变更）执行，
  本 README 按 M14-186 §5 模板落档决策结果；窗/聚合/决策原始 JSON
  永不入库（gitignored 证据目录 `.verify/m14-181-phase0-baseline/`），
  本 README 是唯一入库证据文件。
- 采样边界：本轮仅 3 个 direct 窗 × 8 样本 = 24 个 manifest 请求
  （目标 URL `https://ndtool.cn/aios/download-manifest.json`，全部
  plan 预检 8 请求 → 单次 execute，样本间隔 1s、超时 15s、恒
  HTTP/1.1、无代理、无 APK/大资产、无 `--ssl-no-revoke`）；零重试、
  零额外请求、零生产/VPS/Docker 操作、零凭据。

## 1. 采样前状态（2026-10-01 本地，第三日执行前）

- canonical 证据目录：6 个完成 direct 窗 / 48 个成功样本，分布恰两个
  本地日期（2026-09-29 三窗、2026-09-30 三窗）；零畸形、零失败样本、
  零缺失 TTFB、零 `.reserve` 残留；
- 采样前决策工具只读复核一次（未改动既有证据）：
  `phase0_inconclusive`，reason codes =
  `distinct_dates_below_required` + `total_samples_below_required`
  （6 窗 / 48 样本 < 3 日期 / 72），`phase1_sampling_authorized=false`；
  时点慢窗频率 1/6≈16.7%（唯一慢窗
  `phase0-window-20260930-025330-291702-direct.json`）。

## 2. 第三日三窗执行记录（2026-10-01，全部 direct）

三窗均为「plan（零请求，实测预测 8 个 manifest 样本）→ 单次
execute（逐字符确认短语）」，每窗 exit 0、零失败、零缺失 TTFB、
成功后零 `.reserve` 残留：

| 窗文件名 | 样本 | 失败 | 缺失 TTFB | 慢样本(>2500ms) | TTFB p50 | TTFB max |
| --- | --- | --- | --- | --- | --- | --- |
| `phase0-window-20261001-015850-240679-direct.json` | 8 | 0 | 0 | 3 | 236.234ms | 12283.814ms |
| `phase0-window-20261001-020125-367462-direct.json` | 8 | 0 | 0 | 1 | 257.409ms | 11490.181ms |
| `phase0-window-20261001-020247-290863-direct.json` | 8 | 0 | 0 | 0 | 286.648ms | 363.968ms |

执行事实：聚合器首次调用因 `--aggregate` 与 `--url` 互斥被 argparse
拒绝（exit 2，参数解析层，零请求零写入零证据改动），按正确参数重跑
一次成功——聚合落盘恰一次；三窗之外零 manifest 请求。

## 3. 最终聚合与 Phase 0 决策（M14-186 §5 模板回填）

聚合器单次运行（`phase0-aggregate-20261001-020334.json`，仅存
gitignored 目录）+ 决策工具单次运行（stdout JSON 摘要如下，不整段
复制）：

| 本地日期 | 窗数 | 样本 | 失败 | 缺失 TTFB | 慢样本 | TTFB p50 | TTFB p95 | TTFB max |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-09-29 | 3 | 24 | 0 | 0 | 0 | 215.871ms | 2011.132ms | 2054.566ms |
| 2026-09-30 | 3 | 24 | 0 | 0 | 6 | 888.485ms | 5146.616ms | 5793.434ms |
| 2026-10-01 | 3 | 24 | 0 | 0 | 4 | 279.955ms | 11490.181ms | 12283.814ms |
| overall（全 direct） | 9 | 72/72 | 0 | 0 | 10 | 286.648ms | 5793.434ms | 12283.814ms |

- **决策：`phase0_go`**；reason codes =
  `slow_window_frequency_ge_go_threshold`；
- 慢窗频率 **3/9≈33.3% ≥ 5%** go 门限；慢样本频率 10/72≈13.9%；
  慢窗 3 个 = `phase0-window-20260930-025330-291702-direct.json`、
  `phase0-window-20261001-015850-240679-direct.json`、
  `phase0-window-20261001-020125-367462-direct.json`；
- completion checks 全真：恰 3 本地日期 × 每日 3 窗 × 每窗 8 样本、
  每日恰 24、总计恰 72、逐日 ≤24、总计 ≤72、零畸形/零失败/零缺失
  TTFB、分布成型（72 ≥ 24）；目录内 7 份聚合报告按契约跳过、
  `.reserve` 0 个；
- **预算账：本日 24/24，总计 72/72**——Phase 0 请求预算全额消耗且
  未越界，预算门自此拒绝任何新窗；
- `phase1_sampling_authorized=true`。

## 4. 诚实边界

1. **`phase0_go` 只是必要条件，Phase 1 未启动**：本决策是 M14-181
   §3 Phase 0 门的收口结论；Phase 1 的 VPS 侧采样仍需 supervisor
   显式批准与预算计划后才可启动，本切片零 Phase 1 动作。
2. **三窗时间形态如实声明**：第三日三窗是**同一会话内背靠背执行的
   清晨本地时段（约 01:58–02:03 +08:00）direct 窗**，并非分布在
   当日不同时段；前两日三窗同样是同会话簇（文件名时间戳：
   09-29 为 20:47/20:56/21:15、09-30 为 02:15/02:33/02:54）。
   "每日 3 窗"契约满足的是计数与预算边界，不是日内时段覆盖。
3. **统计口径继承 M14-181/M14-186**：≤24/日、72 总计只支持描述性
   结论（nearest-rank p50/p95/max）；不设 p99、不设 99.5% 成功率门；
   慢窗频率 ≥5% 的 go 门是"值得继续调查"的判据，不是稳态 SLO 达标
   声明。
4. **原始工件不入库**：窗报告、聚合报告、决策 stdout 只以本 README
   摘要表 + 逐项来源文件名入档；gitignored 证据目录是唯一原始存放
   地；本 README 无 secret、无凭据、无 Cookie、无本机绝对路径、无
   整段原始 JSON。
5. 本切片 docs-only（证据 README + 两处台账）；验证 = version-sync
   与 R1 措辞守卫、`git diff --check`、新增行 secret/本地绝对路径/
   U+FFFD 扫描 0 真实命中（自指性关键词除外）。单 local commit 并
   推送远端分支；supervisor 审查与 remote 发布（PR 开合/合并/release
   门禁）在其后进行。
