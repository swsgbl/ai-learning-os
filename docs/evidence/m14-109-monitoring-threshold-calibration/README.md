# M14-109：监控阈值标定/评估工具（实现/文档切片）

- 切片：分支 `ops/m14-109-monitoring-threshold-calibration`（独立 worktree
  `m14-109-monitoring-threshold-calibration`，基于 main
  `ef95ab699f900c416850a62762521b69681a549f`（PR #196 merge = M14-108 合入，
  精确基点）），单次 local commit（不推送、不建 PR）。
- 目标：填补 monitoring 家族「阈值随时间的标定」工程缺口——把
  `monitoring_history.py`（M14-13）已产出的 canonical `history.jsonl` 变成
  **离线、只读、fail-closed 的监控阈值标定/评估面**——新工具
  `tools/ops/monitoring_threshold_calibration.py` + 契约测试
  `services/api/tests/test_monitoring_threshold_calibration.py`。
  **这是离线标定/评估工具，不改变生产阈值、不接外部告警、不解除
  provider-smoke/long-soak/release gates**，不构成 production readiness
  宣称，不授权任何部署。
- 零生产触碰（本切片全程）：零网络、零子进程、零计划任务、零 env
  读取、零生产容器/DB/MinIO/secrets 接触、零服务启停、**零文件写入**
  （工具只读输入文件、只写 stdout）。本切片**从未对真实生产
  history.jsonl 执行过本工具**——全部验证基于合成 fixture（tmp_path /
  FakeStore / producer 函数生成的 canonical 形状行）；canonical 仓库
  与真实 `.verify` 目录零触碰。

## 1. 工具契约（tools/ops/monitoring_threshold_calibration.py）

纯标准库、单文件；一切 I/O 经只读 Store 协议注入（`exists`/`is_dir`/
`is_symlink`/`read_bytes`——**协议层面即无写面**）。

1. **单一事实源（零平行 schema）**：画像/schema 常量与行级加载复用同仓
   `monitoring_history`（`STACK_SERVICES`/`ENDPOINT_IDS`/
   `HISTORY_SCHEMA_VERSION`/nearest-rank `percentile`/退出码）+
   `monitoring_insights.parse_history_text`（已测语义：行 schema 严格
   校验、行序严格递增 `(collected_at, source_stem)`、单一 project、样本
   量界 1–5000；固定词汇拒绝原因原样透传）；**既有阈值基准与配置界
   复用同仓 `production_monitor` 常量**（延迟 warn/critical 默认
   1000/5000 ms、日志错误 5/20 及其 MIN/MAX 配置界——仅导入常量定义，
   从不调用其任何采集/网络/子进程面）。本切片**未改动**
   M14-13/M14-15/M14-108 任何既有行为（监控家族回归 300 passed 实证）。
2. **有界参数（fail-closed，全部先于任何读取）**：`--samples`（默认
   200、1–5000；窗口 = **最新 N 条**实际观测样本，`truncated_older_count`
   显式；**全文件行级校验恒先行**——窗口外交替 malformed 行仍整体拒绝，
   绝不静默截断掉坏行；不伪造时序、不填充缺失点）；评估配置
   `--latency-warn-ms`/`--latency-critical-ms`/`--log-error-warn`/
   `--log-error-critical`（字符串解析 + 固定词汇拒绝；缺省 =
   production_monitor 既有默认，echo `source=existing-defaults`；显式
   提供 = `explicit`；有限性/界/严格 warn<critical 校验）。参数拒绝
   路径 RealStore 零构造（契约测试结构性证明）。
3. **路径防御**：symlink 目标 + 现存 symlink 祖先组件（复用 insights
   已测语义）、输入缺失、目录形态一律拒绝。
4. **标定/评估口径（全部输出显式声明）**：
   - 分位数 min/p50/p90/p95/p99/max，**nearest-rank**（rank =
     ceil(fraction·n)，升序取第 rank 个，与 monitoring_history 同款）；
   - 候选阈值建议 warn=窗口 p95 / critical=窗口 p99（规则字面 echo）；
     `applicable` 机械判定（严格 p95<p99 且双双落在 production_monitor
     配置界内），不适用固定词汇原因（`degenerate-window`/
     `below-minimum`/`above-maximum`）——建议值如实保留、绝不静默抬升；
   - 阈值评估（配置阈值与建议阈值同面并排 = 建议与既有阈值对比）：
     告警语义 **`value >= warn` / `value >= critical`（含边界，与
     production_monitor 同口径）**；coverage_rate = (value < warn) 占比、
     alarm_rate/critical_rate 同理；**ok_status_conflict = 阈值评估结果
     与历史 overall_status==ok 的代理分歧**（overall_status 由既有受
     监控阈值生成，不是独立事件真值——故此指标**不是误报率/真值
     标注**），ok_status_conflict_rate 分母 = 窗口样本总数。
   - 标定域 = 五端点 latency_ms + 六服务 log_error_totals（每样本可观测
     gauge 且既有阈值存在）；**restart 阈值为 M14-23 增量语义，累计
     restart_counts 不作标定输入——诚实排除**。
5. **输出卫生（stdout only，零落盘）**：`--format summary`（默认人读）/
   `--format json`（单文档机器可读：query echo / window 计数+状态计数 /
   逐端点+逐服务分布·建议·双评估 / 边界注记双形态恒包含）；绝无原始
   日志行/密钥/secret/env 值/绝对本机路径；任何拒绝 → 固定词汇拒绝行 +
   exit 2，正文**零部分输出**；零墙钟（无生成时间戳，同参数两次运行
   stdout 逐字节相同——契约测试断言）。
6. 退出码：0 标定/评估成功 / 2 任何拒绝（参数超界/非法、输入缺失/
   symlink/目录、malformed、乱序、重复、混档、超 5000、读失败）。

## 2. 验证（全部基于合成 fixture）

- 聚焦契约测试：
  `python -m pytest services/api/tests/test_monitoring_threshold_calibration.py`
  → **57 passed**（结构契约 4：源码禁止副作用 token（subprocess/
  socket/environ/墙钟/落盘面）+ 双阻断端到端 + 零新增文件 + ops README
  文档化 + 单一事实源常量复用（含 production_monitor 阈值基准）；参数
  fail-closed 17 参数化（samples 超界/非整数、latency 界外/非有限/
  warn≥critical、log-error 界外/倒序——参数拒绝路径 RealStore 零构造）；
  路径防御 5（缺失/目录/真实面 symlink/FakeStore symlink×2）；行校验
  透传 13（malformed×7 参数化/乱序/重复/同时戳降序 stem/混档/零样本/
  超 5000）；标定语义 14（happy/json 结构/nearest-rank 精确值/建议规则
  与 applicable（degenerate/below-minimum/above-maximum）/含边界评估
  语义/默认评估=既有阈值/显式候选评估/窗口取最新+全文件校验先行/
  窗口大于文件如实全用/无 ok 参考时 ok 分歧恒 0/restart 不入标定面）；
  输出卫生 4（summary 与 json 逐字节可复现/投毒不泄漏/边界注记双形态/
  拒绝零正文））。
- 监控家族回归：`test_monitoring_threshold_calibration.py` +
  `test_monitoring_history.py` + `test_monitoring_insights.py` +
  `test_monitoring_history_query.py` → **357 passed**（三既有套件零回归，
  证明复用零行为改动）。
- 真实 CLI 端到端 smoke（canonical venv Python 直接运行脚本）：fixture
  经 producer 函数（`monitoring_history.validate_report` + `build_record`）
  从合成 monitor 报告生成（20 样本，延迟 55–74ms 单调爬升）——标定面与
  产出面同构实证；summary 与 `--format json` 双形态 rc=0：分布
  p50=64/p95=73/p99=74（nearest-rank 精确对账）、建议 warn=73/critical=74
  `applicable=true`、既有阈值（1000/5000ms）评估告警 0/20、建议阈值评估
  告警 2/20（critical 1/20、ok 分歧率 0.100000——代理分歧口径）——
  建议与既有阈值对比完整可见。
- ruff：`ruff check tools/ops/monitoring_threshold_calibration.py
  services/api/tests/test_monitoring_threshold_calibration.py` 与
  `ruff check --select F,E9`（同两文件）→ All checks passed。
  `py_compile` 新脚本通过。`git diff --check` 干净。

## 3. 诚实边界

- 本切片**从未对真实生产 history.jsonl 执行标定/评估**（canonical
  `.verify` 只读纪律：本切片未读取真实工件目录内容）；工具默认输入指向
  canonical 输出路径（常量派生），真实执行留待 supervisor/后续运维切片。
- ok_status_conflict 参考 `overall_status==ok` 是**阈值评估结果与历史
  overall_status==ok 的代理分歧，不是误报率/真值标注**——
  overall_status 本身由既有受监控阈值生成（聚合含全部检查面），不是
  独立事件真值；评估结果只说明「候选阈值在历史窗口上的行为」，不
  证明任何阈值的正确性。
- 真实生产环境端点延迟（7–28ms 量级）低于 production_monitor 配置下界
  （50ms）时，延迟建议恒 `below-minimum`（不适用）——工具如实呈现，
  绝不抬升值以制造「可用建议」。
- 建议是分位数派生的候选值，`applicable=false` 时不可直接配置；
  applicable=true 也不构成部署授权——任何生产阈值变更走
  production_monitor 显式配置 + supervisor 获准窗口。
- 不构成 provider-smoke、release-check、long-soak 或任何 release
  blocker 的解除；`production_ready=false` 不变；不授权任何部署。
