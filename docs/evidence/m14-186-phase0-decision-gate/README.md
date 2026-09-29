# M14-186：Phase 0 完成度与 go/no-go 决策门（只读本地工具切片）

- 切片：worktree `ai-learning-os-worktrees/m14-186-phase0-decision-gate`，
  分支 `ops/m14-186-phase0-decision-gate`，基于 main
  `97e7fd12390c241ab3cc787bbdb5230721aff138`（M14-185 docs-only 提交；
  remote main merge `e6b60231` 的祖先，同一 tree）。
- 定性：**普通 ops 代码切片**——交付 M14-181 设计 §3 Phase 0 的收口
  判定工具 `tools/ops/public_edge_phase0_decision.py` 与 fake 文件契约
  测试 `tests/ops/test_public_edge_phase0_decision.py`。**本切片零网络
  请求、零探针执行、零真实采样、零生产操作**（决策只读既有窗报告）；
  **不授权 Phase 1**（见 §4 当前证据状态）。

## 1. 工具契约（摘要；全文见模块 docstring 与 tools/ops/README.md）

| 面 | 契约 |
| --- | --- |
| 输入 | 一个证据目录（M14-182 编排器产出）；`*.json` 按内容分类：窗报告入账 / 聚合报告（`aios-public-edge-phase0-aggregate/1`）跳过 / 其余计畸形；`.reserve` 预约残留显式忽略（只计数不解析） |
| 只读性 | 零网络请求、零探针执行、零子进程、不改动证据目录任何文件（测试含模块能力断言 + socket 拆除行为级断言） |
| 复用 | 窗报告域校验 = `public_edge_phase0_baseline.parse_window_report`（bool/NaN/inf/域外 config = 畸形）；本地日期归账 = 同一 `_local_date`；分位数 = `monitoring_history.percentile`（nearest-rank，经 `baseline.describe` 单一事实源）；输出碰撞保护 = M14-182 同款 `_reserve_output_path`（O_CREAT\|O_EXCL 独占 `.reserve`）+ 探针原子写 |
| 完成度 | 恰 3 个本地日期 × 每日恰 3 窗 × 每窗恰 8 样本 = 每日 24、总计 72；且逐日 ≤24、总计 ≤72。数量不足 = incomplete；窗数/日期数超计划或预算越界 = violation——都落 inconclusive，绝不 go |
| 质量 | 零畸形文件、零失败样本（`failure_category` 非空）、零缺失 TTFB；"每窗 8 个成功样本" = 结构完成度（每窗恰 8 样本）∧ 零失败 ∧ 零缺失 TTFB 组合等价 |
| 分布成型 | 成功 TTFB 样本 ≥24（一个完整日的量；p50/p95 的最低辩护下限；go 的独立必要条件） |
| 统计口径 | 描述性 only：TTFB p50/p95/max（nearest-rank）、失败数、缺失 TTFB 数、慢样本（TTFB > 2500ms）、慢窗（含 ≥1 个慢样本的窗）、慢样本频率（/成功 TTFB 样本）与慢窗频率（/解析窗数）；**不设 p99、不设 99.5% 成功率门**（键域恒不存在，M14-181 统计诚实边界） |
| 决策规则 | `phase0_go` ⇔ 完整 ∧ 零缺陷 ∧ 分布成型 ∧ 慢窗频率 ≥5%；`phase0_no_go_close` ⇔ 完整 ∧ 零失败/零缺失 ∧ 慢窗频率 <1% ∧ TTFB p95 ≤2500ms；两者之间或任何数据缺陷 = `phase0_inconclusive` + 显式 reason codes |
| 授权字段 | `phase1_sampling_authorized` 恒等于 `decision == phase0_go` |
| 量化注记 | 完成数据恰 9 窗时慢窗频率只能为 0% 或 ≥1/9≈11.1%——[1%,5%) 中间带经真实文件不可达；规则分支仍保留（纯函数 `decide()` 独立单元测试钉住）以防窗数契约演进 |
| 缺目录 | 目录不存在 = 尚未开始采样：**可判定的** inconclusive（reason `evidence_directory_missing`），非运行错误（与 aggregate 拒绝空目录不同——决策门对零证据的诚实回答就是"不完整、不授权"）；路径存在但非目录 = exit 2 |
| 输出 | 默认打印**单个可解析 JSON 文档**到 stdout（信息行走 stderr）；`--output` 按碰撞保护原子落盘（已有报告/预约残留拒绝 exit 2；写出失败保留预约；成功释放）；报告含 schema version、generated_at、输入文件名、counts、metrics、thresholds、completion checks、decision、reason codes、honest boundaries；不含本机绝对路径/secret |
| Exit codes | 0 = 决策已产出（go/no_go_close/inconclusive 都是合法结论）；2 = 运行失败（参数非法、非目录、列举失败、输出碰撞/写出失败） |

## 2. 决策规则表（唯一事实源 = 工具 thresholds 常量，CLI 不可覆写）

| 慢窗频率（完整零缺陷数据） | TTFB p95 | 决策 |
| --- | --- | --- |
| ≥ 5% | 任意 | `phase0_go`（授权 Phase 1 采样启动的**必要条件**，非充分——Phase 1 仍需 supervisor 批准与预算计划） |
| [1%, 5%) | 任意 | `phase0_inconclusive`（中间带；9 窗量化下经文件不可达，纯函数钉住） |
| < 1% | ≤ 2500ms | `phase0_no_go_close`（记录为可接受瞬态，优化终止——M14-181 §3） |
| < 1% | > 2500ms 或不可得 | `phase0_inconclusive` |

不完整（数量不足）/ 越界（窗数、日期数、预算超计划）/ 畸形文件 /
失败样本 / 缺失 TTFB / 分布未成型 → 一律 `phase0_inconclusive` +
对应 reason codes，`phase1_sampling_authorized=false`。

## 3. 本切片的验证（真实执行，零网络）

- `mise exec -- python -m pytest
  tests/ops/test_public_edge_phase0_decision.py`：**24 passed**
  （TDD 先红后绿；全部 fake 文件，零网络零子进程）。覆盖：CLI help、
  缺目录可判定 inconclusive、空目录、**单日两窗 fixture 不完整形态**
  （fixture 语义用例，非 canonical 现状——见 §4）、
  短窗不完整、完整 go（1/9 慢窗频率的 p50/p95/max nearest-rank 数学
  钉住）、完整 no-go 关闭、`decide()` 纯函数中间带与 p95 分支、畸形
  文件（JSON 烂/未知 schema）永不 go、失败样本/缺失 TTFB 阻断、分布
  未成型、逐日预算越界、第 4 日期总预算越界、重复窗文件 = 额外窗、
  跨本地午夜窗归两日历日（日期集以样本归账为准）、聚合报告与
  `.reserve` 忽略、输出原子写 + 预约释放、输出碰撞拒绝
  （既有文件不动、残留预约拒绝）、递归无 p99/success_rate 键、模块
  零网络/子进程能力 + socket 拆除行为级断言、同输入确定性、输出卫生
  （无本机路径/secret）；
- `mise exec -- python -m pytest tests/ops`：**164 passed**（本切片
  24 + 既有 140，零回归）；
- `mise exec -- python -m ruff check`（两个新文件）：All checks
  passed；`compileall`：通过；
- CLI 冒烟（SMOKE-like，fake 证据在 gitignored
  `.verify/m14-186-phase0-decision-gate/smoke/`）：`--help` exit 0；
  单日 2 窗 fake 证据 + 1 个 `.reserve` → `--output` 落盘成功、
  stderr 信息行、决策 `phase0_inconclusive`（reasons 含
  `distinct_dates_below_required` 等 5 项、`reserve_files_ignored=1`、
  `phase1_sampling_authorized=false`）；
- `git diff --check` 干净；新增行 secret/本地绝对路径/U+FFFD 扫描
  0 命中。

## 4. 当前证据状态（实现收口时点，如实声明）

M14-182 交付的是采样编排工具（其 evidence README §3 明确"本切片
未执行"）。**本切片实现期间 supervisor 已在 canonical checkout 开始
真实 Phase 0 采样**
（2026-09-29 三窗 + 2026-09-30 三窗，后者含实现收口前执行完毕的
余下两窗）。截至实现收口，canonical 证据目录（gitignored
`.verify/m14-181-phase0-baseline/`）共有 **6 个完成窗 / 48 个样本，
分布在恰两个本地日期**：

| 本地日期 | 窗数 | 样本 | 失败 | 缺失 TTFB | 慢样本 | TTFB p95 |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-09-29 | 3 | 24 | 0 | 0 | 0 | —（无慢样本） |
| 2026-09-30 | 3 | 24 | 0 | 0 | 6 | 5146.616ms |
| overall | 6 | 48/72 | 0 | 0 | 6 | 5044.404ms |

（表值为 supervisor 落档口径；最新聚合文件
`phase0-aggregate-20260930-025412.json`。本工具对该目录做过一次
**只读复核**，实测一致：decision `phase0_inconclusive`、reasons =
`distinct_dates_below_required` + `total_samples_below_required`、
`phase1_sampling_authorized=false`；另实测 p50 309.668ms /
max 5793.434ms，6 个慢样本集中于 1 个慢窗
`phase0-window-20260930-025330-291702-direct.json`（慢窗频率
1/6≈16.7%），目录内 6 份聚合报告全部按契约跳过、`.reserve` 0 个。）

- 决策边界：**不完整**——仅 2 个本地日期（<3）且总计 48/72；本工具
  对这组证据的报告必须是 `phase0_inconclusive`，**不授权 Phase 1**；
- 真实数据中慢窗已出现（6 慢样本 / 1 慢窗），但 go 从不在不完整数据
  上给出——判定必须等第三日采样补齐 3 日期 × 72 样本后重跑本工具；
- 本切片的 go/no-go 行为证据来自 fake 文件契约测试；对不存在的证据
  目录，工具输出 `phase0_inconclusive` + `evidence_directory_missing`，
  同样不授权。

## 5. Phase 0 收口时的决策落档模板（收口未到：待第三日采样补齐）

1. 窗报告与聚合报告永不入库（gitignored 证据目录）；本 README 是
   唯一入库证据文件；
2. 收口时运行本工具（`--evidence-dir <证据目录>`），把决策 JSON 的
   **决策 + reason codes + counts + metrics 摘要表**回填到本节，逐项
   标注来源窗文件名；
3. `phase0_no_go_close` 时：记录为可接受瞬态、优化终止（写结论文档
   即收口，M14-181 §3）；`phase0_go` 时：Phase 1 的 VPS 侧采样仍需
   supervisor 显式批准与预算计划后才启动——本工具的授权字段只是
   **必要条件**；
4. 任何 `.reserve` 残留、畸形文件或"请求已发出但未入账"的窗都必须
   在边界节如实列出（它们使决策恒为 inconclusive）。

## 6. 诚实边界

1. **本工具不授权 Phase 1**：实现收口时点 canonical 证据 = 6 窗 /
   48 样本、仅两个本地日期——不完整（§4）；对这组真实证据的只读
   复核输出 `phase0_inconclusive`，`phase1_sampling_authorized=false`
   是唯一真实取值。
2. **决策不改变任何生产状态**：只读本地 JSON 计算，零网络零探针；
   VPS/Nginx/frp/Docker/voice 一概未触碰。
3. **样本量边界继承 M14-181**：≤24/日、72 总计只支持描述性结论；
   p99 与 99.5% 成功率门在本工具键域中恒不存在；慢窗频率 ≥5% 的
   go 门是"值得继续调查"的判据，不是稳态 SLO 达标声明。
4. **"每窗 8 个成功样本"的组合实现**：结构完成度（每窗恰 8 样本）+
   零失败 + 零缺失 TTFB 三查组合等价，JSON 中三项独立可见。
5. **窗归期口径**：窗按其首样本本地日期归属、样本逐个按自身本地
   日期入账（与 M14-182 预算门同口径）；"恰 3 个本地日期"与逐日
   预算都以**样本**归账的日期集为准——跨本地午夜的窗会形成"有样本
   无窗"的日历日（该日窗数 0 < 3 → 不完整，绝不 go），单机操作
   假设下如实声明。
6. `production_ready=false` 不变；release-approval 仍是 human-only
   门。本切片交付为 branch + 单 commit；不 push、不开 PR；合并决策
   归 supervisor 审查。
