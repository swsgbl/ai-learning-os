# M14-182：公网边缘 Phase 0 基线采样编排器（工具切片 + 证据模板）

- 切片：worktree `ai-learning-os-worktrees/m14-180-public-edge-stability-probe`，
  分支 `ops/m14-182-public-edge-phase0-baseline`，基于
  `aaf74b54b12270fe35acd7424fc856f56cfde688`（M14-183 docs-only 提交，
  rebase 后精确基点；原始开发基点 main
  `7b1e68366e5238d340e8a5338d419f4df5b876ea` = PR #272 merge）。
- 定性：**普通 ops 代码切片**——交付 M14-181 设计的 Phase 0 落地工具
  `tools/ops/public_edge_phase0_baseline.py` 与 fake-subprocess 契约测试
  `tests/ops/test_public_edge_phase0_baseline.py`。**本切片零真实采样、
  零网络请求、零生产操作**（plan/execute/aggregate 全部未真实运行）；
  本 README 同时是 Phase 0 真实运行后的**证据落档模板**。

## 1. 工具契约（摘要；全文见模块 docstring 与 tools/ops/README.md）

| 面 | 契约 |
| --- | --- |
| 模式 | `plan`（默认，零请求零写入）/ `execute`（`--execute` + 逐字符精确短语 `EXECUTE PUBLIC EDGE PHASE0 WINDOW`）/ `--aggregate`（只读，互斥） |
| 有界窗 | 每窗 manifest 样本默认且上限 8、interval ≥1s、timeout ≤15s、恒 HTTP/1.1；零重试；绝不 `--large-asset-*`（APK 独立预算路径）、绝不 `--ssl-no-revoke` |
| 预算门 | 历史窗报告逐字段域校验（bool/NaN/inf 冒充数值 = 畸形拒绝，零探针调用）；**总 ≤72 且同本地日期 ≤24**（直连+代理合计） |
| 缺目录语义 | plan/execute：目录不存在 = 零历史（首个窗合法起点）；aggregate：拒绝不存在目录（不编造空聚合） |
| 外层子进程超时 | 窗计划派生 fail-safe 上界（M14-184）：30（`curl --version`）+ samples×(timeout+10) + (samples−1)×interval + 30 工具余量；默认窗 267s、interval=60 窗 680s（原 M14-182 固定 120s 会误杀合法慢窗——默认窗合法最坏 237s、上限窗 650s） |
| 碰撞保护 | `O_CREAT|O_EXCL` 独占预约 `<output>.reserve`；已有报告/预约残留零请求拒绝；失败保留预约（同路径不可复用），成功删除；stamp 微秒级 |
| 统计口径 | 描述性：样本数/失败数/慢窗（TTFB>2500ms）频率/p50/p95/max（nearest-rank，单一事实源 = monitoring_history.percentile），overall + direct/proxy + 本地日期分组；**不设 p99/99.5% 成功率门**（键域不存在，note 显式声明） |
| 输出 | 原子写 JSON 到调用者指定 gitignored 目录；无本机绝对路径、无 secret；聚合报告独立 schema 留目录内不入预算账 |
| 非目标 | 不是调度器（零计划任务注册）；不改生产/VPS/Nginx/frp/Docker/voice |

## 2. 本切片的验证（真实执行，零网络）

- `mise exec -- python -m pytest tests/ops`：**138 passed**
  （phase0 编排器 37 + M14-180 探针 101），fake ProbeInvoker 注入点
  零真实子进程零网络；覆盖：plan/错短语/预算越界/畸形历史全部**零
  探针调用**、成功 execute 的 argv 域（恒 1.1/样本 8/无大资产/无
  ssl-no-revoke）、`.reserve` 碰撞保护（预存在报告与预约残留拒绝、
  失败保留、成功释放、报告不可入账保留）、缺目录 allow-missing 双
  语义（aggregate 拒绝且不建目录）、数值域（bool/NaN/±inf 拒绝）、
  聚合 nearest-rank 数学与**递归无 p99/success_rate 键**断言、CLI
  help 四必需选项、输出卫生（无本地路径/secret）；
- `ruff check`：All checks passed；`compileall`（新工具）：通过；
- `git diff --check` 干净；新增行 secret/本地绝对路径扫描 0 命中。

## 3. Phase 0 真实运行后的证据落档模板（本切片未执行）

真实 Phase 0 由 supervisor/操作者按 M14-181 §3 预算逐窗显式执行
（例：每日 3 窗 × 8 样本 × 3 天，直连为主、代理窗可选）：

1. **原始工件永不入库**：所有窗报告（`phase0-window-*.json`）、失败
   预约残留（`*.reserve`）与聚合报告（`phase0-aggregate-*.json`）
   留在 gitignored 的 `.verify/m14-182-public-edge-phase0-baseline/`
   （或同约定的专属证据目录）；本 README 是唯一入库证据文件；
2. **仅摘要证据入库**：Phase 0 收口时，把最终聚合的**描述性统计表**
   （overall + 分模式/分日期：样本数/失败数/慢窗频率/p50/p95/max）
   与 go/no-go 结论（慢窗口频率 ≥5% → go；<1% 且 p95 达提案水平 →
   关闭）回填到本 README 新增章节，逐项标注来源窗文件名；
3. **如实记录预算消耗**：入档时注明 total N/72 与逐日 n/24 的最终
   计数；任何 `.reserve` 残留与"请求已发出但未入账"的窗都必须在
   边界节显式列出（工具的失败路径会保留预约并拒绝同路径复用）；
4. **不做门禁升级**：除非样本量达到 M14-181 统计诚实边界的要求
   （连续多周或生产遥测），摘要只描述、不引申 p99/成功率结论。

## 4. 诚实边界

1. **零真实采样**：本切片未执行任何 plan→execute 的真实网络路径，
   全部行为证据来自 fake-subprocess 契约测试；真实 transport 行为
   以 M14-180 已落档证据为参照。
2. **无调度**：不注册任何计划任务；"每日 3 窗"的节奏由操作者人工
   保证，工具只保证单窗有界与预算拒绝。
3. **预算账依赖窗报告可入账**：探针报告写出失败（极端路径）时该窗
   请求可能已发出但未记账——工具保留预约并要求人工核查，不自动
   补账；此类事件必须按 §3.3 如实入档。
4. **时区依赖**：本地日期预算按操作者本机时区归账；跨时区多机操作
   会把"同本地日"算到不同日历日（当前单机操作假设，如实声明）。
5. 本切片交付为 branch + 单 commit；PR 创建即止；合并决策归
   supervisor 审查（supervisor 审查与 remote 发布在其后进行）。
