# M14-13 监控历史索引 + 有界留存 + 趋势摘要 — 交付证据归档

- 日期：2026-09-12（交付）
- 分支：`feat/m14-13-monitor-history-retention`（基于 `main@d1d5019`，即
  PR #86 merge commit `d1d5019a69eb0837de02471d6b3b07f2018a3317`，本地
  git 可验证）。本 Claude 开发回合仅做本地 commit；supervisor 审查与
  remote 发布（push/PR/合并）在其后进行。
- 状态：**工具 + 聚焦契约测试交付；开发回合零生产执行、零 canonical
  `.verify` 写入**——全部验证用合成样本在测试临时目录完成（不读取真实
  生产工件目录、不向真实 `.verify/artifacts/m14-13-monitoring-history/`
  写任何输出；真实历史构建由 supervisor 决定何时运行）。
- 入库变更：`tools/ops/monitoring_history.py`（单文件、纯标准库、零第
  三方依赖，与 production_monitor.py 同款纪律：注入式 Store、schema 版
  本化、原子写、fail-closed、零子进程/零网络/零墙钟）、
  `services/api/tests/test_monitoring_history.py`（73 项契约测试）、本
  README，以及 PROJECT_STATUS / ROADMAP / CHANGELOG /
  `tools/ops/README.md` 同步。
- 结论口径（诚实边界）：**工具交付、未执行真实历史构建、
  `production_ready=false` 不变**。历史可用性趋势不构成 production
  readiness 宣称。

## 产品形态

```
python tools/ops/monitoring_history.py                 # 默认源/输出目录
python tools/ops/monitoring_history.py --retention 200
```

- **输入面（固定画像）**：默认 gitignored
  `.verify/artifacts/m14-12-production-monitoring/`（`--source-dir` 覆
  盖，**只读——源工件永不改动/删除**）。仅发现 `monitor-*.json`；stem
  严格白名单 `monitor-YYYYMMDD-HHMMSS`（正则 + 日历合法性）——glob 命
  中但 stem 不合规一律 fail-closed 且**被拒名不回显**；`plan-*`、
  `.md`、无关文件天然忽略（不在发现面）。
- **严格校验（fail-closed，任何违规输出零写入）**：schema_version==1、
  tool==`tools/ops/production_monitor.py`、milestone==M14-12、
  mode==execute、project 白名单（与 monitor 同款）、started/ended UTC
  时间戳格式与顺序、overall_status∈{ok,warn,critical}（**incomplete
  拒绝**）、**partial 恒 false**、阈值计数（int 且非 bool）、六 compose
  服务 health、六容器 RestartCount、五端点 status/http_status/latency_ms
  （**有限数值、非负**）、六日志 error_total；not-json/JSON 数组同拒。
  校验与 canonical 工件 `monitor-20260911-173920.json` 的真实 schema
  逐字段对齐（开发时只读核对，未改动 canonical）。
- **去重/排序/留存**：逐文件 SHA-256；同哈希 = 同内容 → 去重（保留
  (collected_at, stem) 最小者为确定性代表，`duplicate_count` 显式）；
  同 (project, collected_at) 不同哈希 = **conflicting-duplicate 拒绝**；
  唯一样本按 (collected_at, stem) 确定性排序；保留最新 N 条（默认 500、
  硬顶 5000、下限 1，CLI 超界 fail-closed），显式 `omitted_older_count`
  与 oldest/newest retained 边界。
- **输出**：默认 gitignored
  `.verify/artifacts/m14-13-monitoring-history/`（`--output-dir` 为操作
  者显式自选，位置与 gitignore 状态由操作者负责）——`history.jsonl`
  （每行一条紧凑记录：`artifact_sha256`/`source_stem`/`collected_at`/
  `project`/`overall_status`/`partial`/`threshold_counts`/compose 服务
  health/restart 计数/端点状态+延迟/日志 error 总计——**绝无原始日志
  行/密钥/secret**，源 `boundaries` 等未知字段投毒实证绝不进入输出）+
  `history-summary.md`（schema 版本化：记录/状态计数、
  availability(ok)/degraded(warn)/critical、first/last 时间戳、逐端点
  延迟 min/p50/p95/max（nearest-rank）、逐服务 restart/日志 error 总
  计、duplicate/omitted 计数、留存边界）。**生成时间戳取自最新源样本
  的 collected_at——全程零墙钟，两次运行输出逐字节相同**。
- **写出安全**：两文件同目录 tmp+fsync+os.replace 原子落盘（replace
  失败清理 tmp 零残留）；symlink 拒绝覆盖源文件/源目录/输出文件/输出
  祖先目录；**仅在全部输入校验通过后才写任何输出**（FakeStore 写调用
  记录实证：校验失败时 write_calls 为空）。
- **结构安全**：源码零 `subprocess`/`socket`/`urllib`/`http.client`/
  `os.system`/`Popen`/`environ`/`getenv`/`docker`/`datetime.now`/
  `utcnow`/`time.time` 字面量（契约测试锁定）；socket+subprocess 双阻断
  下端到端照常成功；一切 I/O 经 Store 注入（RealStore/FakeStore）。
- 退出码：0 成功；2 任何拒绝（参数超界、源目录缺失、零源、malformed、
  partial/incomplete、conflicting-duplicate、symlink/路径拒绝、写失败）。

## 本回合验证（2026-09-12，canonical venv Python 3.11.15 / pytest 9.1.1 / ruff 0.16.5）

1. **聚焦契约测试**：`python -m pytest
   services/api/tests/test_monitoring_history.py -q` → **73 passed**。
   覆盖：结构契约（禁用 token + 双阻断端到端）、发现/路径防御（仅
   monitor-*.json、非许可名 ×6 不回显、源文件/源目录/输出/祖先 symlink
   拒绝、源目录缺失/零源）、严格校验（not-json/数组、schema 契约 ×7、
   时间戳 ×4、阈值计数 ×4、compose 缺服务、端点缺失/failed/延迟非法
   ×5、restart ×3、日志 ×2、collector 非 ok）、接受面（单样本字段全集
   + SHA-256 对源字节、多样本确定性排序、ok/warn/critical 全入档）、
   去重（同哈希确定性代表 + duplicate_count）、冲突重复拒绝、留存
   （保留最新 + omitted/边界、N 超样本全保留、CLI 越界 ×3、硬顶合法、
   **源文件逐字节不变零删除**）、摘要（nearest-rank 精确断言、单位
   函数、逐字节可复现、零墙钟生成时间戳、Markdown 表与边界、投毒
   标记值零泄漏）、原子写（校验先于写入、第 1/第 2 次写失败、真实
   os.replace 失败零 tmp 残留）、CLI 注册与 README 文档化。
2. **组合回归**：`python -m pytest services/api/tests/test_monitoring_history.py
   services/api/tests/test_production_monitor.py -q` → **262 passed**
   （73 + 189，M14-12 套件零回归）。
3. **静态**：`ruff check services/api` 全过（工具与测试文件单独跑亦全
   过）、`py_compile tools/ops/monitoring_history.py` 过、
   `git diff --check` 过。

## 安全边界（本回合零违背）

- 零生产执行：未运行任何 monitor execute、未调用 Docker/HTTP/计划任务/
  语音/恢复/代理；未读取真实生产工件目录内容入档（开发时只读核对过
  canonical 工件 schema 以对齐校验，未改动 canonical 与 `.verify` 任何
  文件——本回合所有验证输出均在测试临时目录）。
- 不 push、不开 PR、不合并；本 Claude 开发回合仅做本地 commit（supervisor
  审查与 remote 发布在其后进行）；不触碰 untracked `.claude/`。
- 零密钥/零 env 原文/零 token/零原始日志行入档；输出仅含计数、状态、
  时间戳与有限延迟数值。

## 结论边界（不过度引申）

- 本切片交付监控/告警收口的**历史数据面第一块基础**：可复用、
  fail-closed 的工件索引 + 有界留存 + 趋势摘要工具及其契约测试。
- 未覆盖（后续切片范围）：持续/定时采集与调度（历史随每次
  monitor execute 增长，本工具按需重建索引）、外部告警接入、指标
  时序存储/查询 API、阈值随时间的自动标定、跨机监控、对真实
  canonical 工件目录的首次实际历史构建（supervisor 决定）。
- **`production_ready=false` 不变**；历史趋势可用性不构成生产就绪宣称。
