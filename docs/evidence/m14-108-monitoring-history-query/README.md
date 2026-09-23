# M14-108：监控历史时序查询工具（实现/文档切片）

- 切片：分支 `ops/m14-108-monitoring-history-query`（独立 worktree
  `m14-108-monitoring-history-query`，基于 main `9729fa5b1b5bddba1331ef599911e9d2c832c15a`
  （PR #195 merge = M14-107 合入，精确基点）），单次 local commit
  （不推送、不建 PR）。
- 目标：把 `monitoring_history.py`（M14-13）已产出的 canonical
  `history.jsonl` 变成**安全、离线、可测试的只读查询面**——新工具
  `tools/ops/monitoring_history_query.py` + 契约测试
  `services/api/tests/test_monitoring_history_query.py`。
  **这是查询工具切片，不是生产查询服务**；不宣称解除 provider-smoke
  或任何 release blocker，不构成 production readiness 宣称。
- 零生产触碰（本切片全程）：零网络、零子进程、零计划任务、零 env
  读取、零生产容器/DB/MinIO/secrets 接触、零服务启停、**零文件写入**
  （工具只读输入文件、只写 stdout）。本切片**从未对真实生产
  history.jsonl 执行过本工具**——全部验证基于合成 fixture（tmp_path /
  FakeStore / producer 函数生成的 canonical 形状行）；canonical 仓库
  与真实 `.verify` 目录零触碰。

## 1. 工具契约（tools/ops/monitoring_history_query.py）

纯标准库、单文件；一切 I/O 经只读 Store 协议注入（`exists`/`is_dir`/
`is_symlink`/`read_bytes`——**协议层面即无写面**）。

1. **单一事实源（零平行 schema）**：画像/schema 常量复用同仓
   `monitoring_history`（`STACK_SERVICES`/`ENDPOINT_IDS`/
   `HISTORY_SCHEMA_VERSION`/时间戳解析/`percentile`/退出码）；canonical
   行级校验**委托** `monitoring_insights.parse_history_text`（已测语义：
   行 schema 严格校验、行序严格递增 `(collected_at, source_stem)`、
   单一 project、样本量界 1–5000；固定词汇拒绝原因原样透传）。本切片
   **未改动** `monitoring_history.py` 与 `monitoring_insights.py` 的
   任何行为——复用全部经模块导入，无共享代码复制或移仓。
2. **有界参数（fail-closed，全部先于任何读取）**：`--start`/`--end`
   （UTC `%Y-%m-%dT%H:%M:%SZ`；时间窗**两端均含边界**
   `start <= collected_at <= end`；`start > end` 拒绝；可单边）；
   `--status`（逗号分隔，恒 ∈ {ok,warn,critical}；空串/空段/重复/
   词汇外拒绝）；`--service`/`--endpoint`（**维度选择**——切片聚合与
   记录投影面，非记录过滤面（canonical 记录是完整栈样本）；恒 ∈
   六服务/五端点固定画像）；`--limit`（默认 50、1–500；非整数/超界
   固定词汇拒绝）。参数拒绝路径 RealStore 零构造（契约测试结构性证明）。
3. **路径防御**：symlink 目标 + 现存 symlink 祖先组件（复用 insights
   已测语义）、输入缺失、目录形态一律拒绝。
4. **查询语义**：过滤（时间窗含边界 + 状态）→ limit（保留**最新** N
   条，`truncated_older_count` 显式）→ 聚合（**恒全窗口口径**——limit
   只界记录列表，绝不扭曲聚合）→ 有界记录投影（白名单字段 + 所选维度
   切片；行级未知额外字段被结构性丢弃）。零命中是合法查询结果（如实
   输出零计数），不是拒绝。
5. **输出卫生（stdout only，零落盘）**：`--format summary`（默认人读）
   / `--format json`（单文档机器可读）；仅从合法历史记录派生的聚合与
   投影，绝无原始日志行/密钥/secret/env 值/绝对本机路径（输入显示恒
   纯名或仓库相对）；任何拒绝 → 固定词汇拒绝行 + exit 2，JSON/摘要
   正文**零部分输出**；零墙钟（无生成时间戳，同参数两次运行 stdout
   逐字节相同——契约测试断言）。
6. 退出码：0 查询成功（含零命中）/ 2 任何拒绝（参数超界、输入缺失/
   symlink/目录、malformed、乱序、重复、混档、超 5000、读失败）。

## 2. 验证（全部基于合成 fixture）

- 聚焦契约测试：
  `python -m pytest services/api/tests/test_monitoring_history_query.py`
  → **57 passed**（结构契约 4：源码禁止副作用 token（subprocess/
  socket/environ/墙钟/落盘面）+ 双阻断端到端 + 零新增文件 + ops README
  文档化 + 常量单一事实源；参数 fail-closed 21（limit/status/service/
  endpoint/时间窗格式与顺序参数化）；路径防御 6（缺失/目录/真实面
  symlink/FakeStore symlink×2）；行校验透传 13（malformed×7 参数化/
  乱序/重复/同时戳降序 stem/混档/零样本/超 5000）；查询语义 14
  （happy/json 结构/含边界/窗外交集/status/组合/limit 截断/空命中/
  restart_evaluation 合法与非法/投毒不泄漏）；输出卫生 2（逐字节
  可复现/拒绝零正文））。
- 监控家族回归：`test_monitoring_history_query.py` +
  `test_monitoring_history.py` + `test_monitoring_insights.py` →
  300 passed（两既有套件零回归，证明复用零行为改动）。
- 真实 CLI 端到端 smoke（canonical venv Python 直接运行脚本）：
  fixture 经 producer 函数（`monitoring_history.validate_report` +
  `build_record`）从合成 monitor 报告生成——查询面与产出面同构实证；
  summary 与 `--format json --status warn --service redis,api
  --endpoint api-health` 双形态输出正确，rc=0。
- ruff：`ruff check tools/ops/monitoring_history_query.py
  services/api/tests/test_monitoring_history_query.py` → All checks
  passed。py_compile 新脚本通过。`git diff --check` 干净。

## 3. 诚实边界

- 本切片**从未对真实生产 history.jsonl 执行查询**（canonical
  `.verify` 只读纪律：本切片未读取真实工件目录内容）；工具默认输入
  指向 canonical 输出路径（常量派生），真实执行留待 supervisor/后续
  运维切片。
- 行级未知额外键被忽略（与 insights 既有已测语义一致——非本切片新设
  的收紧面）；「未知关键字段」按既有语义理解为关键身份字段的未知值
  （schema_version/overall_status 等，一律拒绝），额外键不拒绝但
  **绝不进入输出**（结构性丢弃 + 投毒测试实证）。
- limit 只界记录列表；聚合恒全窗口口径——若历史样本超 5000 行由
  insights 既有量界拒绝（fail-closed，不静默截断）。
- 不构成 provider-smoke、release-check、long-soak 或任何 release
  blocker 的解除；`production_ready=false` 不变；不授权任何部署。
