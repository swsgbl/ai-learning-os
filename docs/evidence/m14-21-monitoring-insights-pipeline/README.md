# M14-21 监控洞察接入持续管道 — 交付证据归档

- 日期：2026-09-13（开发切片交付）
- 分支：`feat/m14-21-monitoring-insights-pipeline`（基于 `main@9977ece`，即
  PR #97 merge commit `9977ece81bc9a4b265935a3132cfe7d115704687`，本地 git
  可验证）。本 Claude 开发回合独占 worktree
  `m14-21-monitoring-insights-pipeline`，仅做**一个本地 commit**；supervisor
  审查与 remote 发布（push/PR/合并）在其后进行——该表述是开发时点快照。
- 状态：**管道契约三步化 + 双路径事实源修复 + 聚焦契约测试交付（本地）**；
  本回合全部验证用 fake runner / 合成样本 / 临时目录——**零生产执行、零
  scheduler mutation、零 canonical `.verify` 触碰**（未启停任何服务/容器/
  计划任务/voice 进程/模拟器，未运行 M14-14 管道本体，未读取 env/密钥）。
- 入库变更：`tools/ops/monitoring_pipeline.py`（三步序列 + 白名单第三形态
  + insights 超时 + 报告加 insights stage）、`tools/ops/monitoring_insights.py`
  （默认 source 改引 history canonical 常量）、
  `tools/ops/monitoring_pipeline_task.py`（预算注释三步化）、
  `tools/ops/run_monitoring_pipeline_silent.vbs`（注释步数口径，行为零改动）、
  `services/api/tests/test_monitoring_pipeline.py`（52→63）、
  `services/api/tests/test_monitoring_insights.py`（91→93）、
  `services/api/tests/test_monitoring_pipeline_task.py`（预算 pin 三步化）、
  本 README，以及 PROJECT_STATUS / ROADMAP / CHANGELOG / `tools/ops/README.md`
  同步。
- 结论口径（诚实边界）：**修复的是「定时管道永不产出洞察 + insights 默认
  输入无写入者」这一契约缺口并经合成端到端实证；管道真实三步运行与
  PT15M 下一轮观察（insights 产物首次由管道产出）由 supervisor 合并后
  在获准窗口进行；`production_ready=false` 不变**。

## 问题事实（复核于 `main@9977ece`）

- `tools/ops/monitoring_pipeline.py` 固定 sequence 只有 monitor → history
  （`build_config` 的 `["monitor", "history"]`、`render_markdown`/
  `_planned_stages`/plan 打印的 `("monitor", "history")` 循环、
  `allowed_step_argv` 仅两形态）——定时管道每轮 PT15M 更新 history，
  **永不更新 insights**。
- `tools/ops/monitoring_insights.py` 默认 `DEFAULT_SOURCE =
  .verify/artifacts/m14-13-monitor-history-retention/`（M14-15 切片指定的
  新输入位，当时明确留档「仓库现有工具尚无写入者」）；而
  `monitoring_history.py` 的 canonical 输出是
  `.verify/artifacts/m14-13-monitoring-history/`（`DEFAULT_OUTPUT_DIR`，
  亦即 pipeline 的 `HISTORY_OUTPUT_DIR`）——**双路径事实源**：定时管道
  持续写入后者，insights 却默认读前者且前者无任何写入者——insights
  永久无新鲜输入。

## 修法

### 1) 路径对齐（单一事实源，`tools/ops/monitoring_insights.py`）

`DEFAULT_SOURCE` 由平行字符串路径改为**直接引用同仓
`monitoring_history.DEFAULT_OUTPUT_DIR` 常量**：

```python
DEFAULT_SOURCE = _history.DEFAULT_OUTPUT_DIR
```

`import monitoring_history as _history` 是该工具既有共享模式
（STACK_SERVICES / HISTORY_SCHEMA_VERSION / ARTIFACT_STEM_RE 等常量同源），
零新增机制。契约测试锁定三方 resolve 全等：
`monitoring_pipeline.HISTORY_OUTPUT_DIR` == `monitoring_history.DEFAULT_OUTPUT_DIR`
== `monitoring_insights.DEFAULT_SOURCE`，以及
`monitoring_pipeline.INSIGHTS_OUTPUT_DIR` == `monitoring_insights.DEFAULT_OUTPUT_DIR`。

### 2) 三步序列（`tools/ops/monitoring_pipeline.py`）

- sequence 恒为 monitor → history → insights；insights 仅在 **history
  status=ok** 后运行；任一前置非 ok → 后续 skipped + 固定词汇原因
  （`skipped_step` 泛化为 `(spec, prev_step_id, prev_stage)`，产出
  `<prev>-status-<status>`；history 的既有原因字符串逐字不变——兼容面）。
- 前置步骤退出码/类别如实保留绝不遮蔽；insights 失败/超时不改变
  monitor/history 已入档事实（仅 overall_status=failed + EXIT 1）。

### 3) 白名单第三形态（结构性）

```python
"insights": (python_exe, str(INSIGHTS_SCRIPT),
             "--execute", "--confirm", INSIGHTS_CONFIRM_PHRASE),
```

- `INSIGHTS_CONFIRM_PHRASE = "EXECUTE READ-ONLY MONITORING INSIGHTS"` 与
  `monitoring_insights.CONFIRM_PHRASE` 逐字一致（回归测试锁定；与管道/
  monitor 门禁短语互不通用）。
- **恒不带 `--source`/`--output-dir`/`--event-limit`**：canonical 输入经
  已修正的工具默认值生效——CLI 无任何用户可注入进入命令的参数；
  `--source` 注入、追加旗标、缺/错 confirm 一律 `CommandNotAllowedError`
  且内层 runner 零调用（测试参数化覆盖）。
- 无 shell=True；子进程输出仅取 returncode，stdout/stderr 绝不持久化/回显。

### 4) 超时预算（三步化）

| 步骤 | 界（秒） | 默认 | 说明 |
|---|---|---:|---|
| monitor | 60–540 | 480 | 不变（覆盖内部最坏 ~445s + 启动余量） |
| history | 10–120 | 45 | 不变 |
| insights | **5–50** | **15** | 新增：纯本地只读工件处理秒级完成即兜底杀停 |

三步硬顶之和 540+120+50 = **710s < PT12M=720s** 执行时限 < PT15M 重复
间隔（调度器绝不先于内部超时杀整任务，避免击杀留 stale lock）。
`monitoring_pipeline_task.py` 头注释与
`test_monitoring_pipeline_task.py::test_interval_covers_pipeline_budget`
交叉 pin 同步三步口径（`limit > hard_sum` 严格小于）。

### 5) 报告面（加法演进）

- config 新增 `insights_tool` / `insights_timeout_seconds` /
  `insights_confirm_phrase`；`sequence` 三步。
- stages 新增 insights 条目（与 monitor/history 同构：状态/退出码/UTC
  起止/时长/固定命令身份（`<python>` 占位，无绝对本机路径）/脱敏错误
  类别/产物两固定名 `insights.json` + `insights-summary.md` + SHA-256 +
  字节数；目录缺失 → `artifact-dir-unreadable` 如实入档）。
- Markdown 表三行 + 序列描述更新；`REPORT_SCHEMA_VERSION` 仍 1
  （加法演进，既有字段零改动）；安全面不变——无绝对本机路径/无子进程
  原文/无 env/token，写前 redact_secrets 终防线。

## 契约测试（TDD RED 先行后 GREEN）

RED 实证（实现前）：`test_monitoring_insights.py` 默认源/README pin 与
`test_monitoring_pipeline_task.py` 预算链共 **4 项失败**，
`test_monitoring_pipeline.py` **collection 即失败**于缺失 `INSIGHTS_SCRIPT`
常量——全因功能缺失非 typo。

| 套件 | 数量 | 本切片覆盖 |
|---|---:|---|
| `test_monitoring_pipeline.py` | 52→**63** | 三步序列+产物三组发现与 hash；monitor 失败→history+insights 双 skipped；history 失败→insights skipped 原因可见且 history exit 2 如实保留；insights 失败/超时不改变 monitor/history 事实；白名单第三形态精确 + `--source`/`--event-limit`/缺 confirm/错短语注入拒绝；insights 短语常量 pin；路径三方一致 + insights 步不带 `--source`；三步预算 710<720 + monitor/history 界不变；insights 产物目录缺失如实；Markdown 三步渲染；insights 超时边界 4/51 拒 5/50 放行；CLI 默认；配置超时三步透传；报告写失败步数 2→3 |
| `test_monitoring_insights.py` | 91→**93** | 默认源 == history canonical 输出（常量直接引用）；CLI 默认与 ops README pin 更新（旧 retention 路径不再出现） |
| `test_monitoring_pipeline_task.py` | 79（口径更新） | 预算交叉 pin 三步化：`interval > limit > hard_sum` |

## 验证（开发回合，零生产触碰）

```bash
# 监控家族三套件（RED→GREEN 全程）
.venv/Scripts/python.exe -m pytest services/api/tests/test_monitoring_pipeline.py \
    services/api/tests/test_monitoring_insights.py \
    services/api/tests/test_monitoring_pipeline_task.py -q
# → 238 passed
# 监控全家族五套件（含 test_monitoring_history.py / test_production_monitor.py 邻接回归）
# → 510 passed

# ruff / py_compile / git diff --check
.venv/Scripts/python.exe -m ruff check services/api \
    tools/ops/monitoring_pipeline.py tools/ops/monitoring_insights.py   # All checks passed
.venv/Scripts/python.exe -m py_compile tools/ops/monitoring_pipeline.py \
    tools/ops/monitoring_insights.py                                     # OK
git diff --check                                                          # OK

# 全量 services/api 套件：2496 passed / 34 failed / 34 skipped——34 失败
# 与 main@9977ece 基线对照（主仓同 commit 复跑同 15 文件 = 同样
# 34 failed / 801 passed）完全一致，全部位于 evidence/release/governance/
# voice 等本切片零触碰的环境敏感域 = 基线既有失败，非本切片引入。
```

### 合成端到端（fake runner + 真实只读工具链 + 临时目录，40/40 PASS）

脚本形态：`.verify/tmp/m14-21-synth/e2e.py`（开发回合产物，gitignored
不入库；三段结构 A/B/C）。

- **A. pipeline fake-runner 四场景**（FakeRunner 按 argv[1] 判步注入
  rc/产物，FakeClock 确定性时间戳，全临时目录）：
  - 全 ok → **EXIT 0**，sequence 三步、三 stage ok、insights 产物两固定
    名 + SHA-256；
  - monitor rc=2 → **EXIT 1**，history/insights 双 skipped（原因
    `monitor-status-failed` / `history-status-skipped`）；
  - history rc=2 → **EXIT 1**，insights skipped（原因
    `history-status-failed`），monitor ok、history exit 2 如实保留；
  - insights rc=2 → **EXIT 1**，monitor/history ok 不变、insights failed；
  - 四场景报告均无绝对本机路径、无子进程 stdout 原文。
- **B. 真实只读工具链接力**（合成合法 M14-13 记录 ×3 → history canonical
  目录形态（history.jsonl + history-summary.md 同目录）→ 真实
  `monitoring_insights.py --execute --confirm ...`）：**EXIT 0**，
  `insights.json` + `insights-summary.md` 落盘、3 样本入洞察（ok=2
  warn=1）、输入逐字节零改动；`--source C:/evil` 注入被白名单
  `CommandNotAllowedError` 拒绝。
- **C. 常量现场断言**：三方路径 resolve 全等 + 预算 710 < 720。

## 边界（诚实口径）

- 本切片开发回合**零生产执行**：未运行真实 monitor/history/insights 于
  canonical 目录、未运行 M14-14 管道本体于真实调度、未触碰任何生产
  服务/容器/voice 进程/模拟器。
- **零 scheduler mutation**：`monitoring_pipeline_task.py` 仅改注释与被测
  常量口径，无任何 schtasks 调用；vbs wrapper 仅注释口径更新、调用形态
  零改动（它只透传调用管道，不传 timeout——管道默认值即三步预算）。
- 零 canonical `.verify` 触碰：合成端到端全程临时目录。
- 管道真实三步运行与 PT15M 下一轮观察（insights 产物首次由管道产出、
  `m14-15-monitoring-insights/` 目录开始有写入者）由 supervisor 合并后
  在获准窗口进行；`production_ready=false` 不变。
