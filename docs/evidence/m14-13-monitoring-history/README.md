# M14-13 监控历史索引 + 有界留存 + 趋势摘要 — 交付证据归档

- 日期：2026-09-12（交付）/ 2026-09-12（合并 + 首次真实历史构建结果回填）
- 分支：`feat/m14-13-monitor-history-retention`（基于 `main@d1d5019`，即
  PR #86 merge commit `d1d5019a69eb0837de02471d6b3b07f2018a3317`，本地
  git 可验证）。本 Claude 开发回合仅做本地 commit；supervisor 审查与
  remote 发布（push/PR/合并）在其后进行。交付（含同分支第二个独立
  commit——M14-13 CI R2 MinIO 自建镜像修复 `b1201d6`）已随 **PR #87**
  合并 main（merged_at **2026-09-12T08:13:50Z**，merge commit
  `d2ffc49b2770891227b52aea0307fb969969c7e2`，feature head
  `b1201d6d041ae3ea492ee918d4572584a6c47b02`，本地 git 与 origin/main
  双重可验证；远端 feature 分支已删除）；PR CI run `34682507203` 与
  合并后 main push CI run `34682734884` 均全部 5 job（Web/API/Docker/
  Android/Release tools）SUCCESS（同分支更早 run `34670572870` 为
  MinIO 镜像拉取失败的 CI R1 形态，R2 修复后转绿）。本回填切片分支
  `docs/m14-13-history-results`（基于 main@d2ffc49，docs-only）。
- 状态：**工具 + 聚焦契约测试交付并合并；开发回合零生产执行、零
  canonical `.verify` 写入（全部验证用合成样本在测试临时目录完成）；
  本回填回合已对真实 canonical 工件目录独立执行首次真实历史构建
  （连跑两遍输出逐字节相同，见「首次真实历史构建结果」节）**。
- 入库变更：`tools/ops/monitoring_history.py`（单文件、纯标准库、零第
  三方依赖，与 production_monitor.py 同款纪律：注入式 Store、schema 版
  本化、原子写、fail-closed、零子进程/零网络/零墙钟）、
  `services/api/tests/test_monitoring_history.py`（73 项契约测试）、本
  README，以及 PROJECT_STATUS / ROADMAP / CHANGELOG /
  `tools/ops/README.md` 同步；本回填切片 docs-only（仅上述文档，
  零代码/零测试改动）。
- 结论口径（诚实边界，回填更新）：**工具交付并合并；开发回合未执行
  真实历史构建（本回填回合已独立执行首次真实构建，见下）；
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

## 安全边界（开发回合零违背；回填回合边界见下节后注）

- 零生产执行：未运行任何 monitor execute、未调用 Docker/HTTP/计划任务/
  语音/恢复/代理；未读取真实生产工件目录内容入档（开发时只读核对过
  canonical 工件 schema 以对齐校验，未改动 canonical 与 `.verify` 任何
  文件——本回合所有验证输出均在测试临时目录）。
- 开发回合不 push、不开 PR、不合并；本 Claude 开发回合仅做本地 commit
  （supervisor 审查与 remote 发布在其后进行）；不触碰 untracked
  `.claude/`。
- 零密钥/零 env 原文/零 token/零原始日志行入档；输出仅含计数、状态、
  时间戳与有限延迟数值。

## 首次真实历史构建结果（2026-09-12，本回填回合独立执行）

**执行形态**：从 canonical main `d2ffc49`（origin/main 已核对同步）拉
干净 worktree（分支 `docs/m14-13-history-results`），独立运行本工具
**两遍**：`py -X utf8 tools/ops/monitoring_history.py --source-dir
<canonical 仓库>/.verify/artifacts/m14-12-production-monitoring
--output-dir <本回填 worktree>/.verify/artifacts/m14-13-monitoring-history`。
两遍均 exit 0；输出落 gitignored 目录**绝不入库**（本节仅引用文件名、
哈希与指标）。这不是 supervisor 结果的转述——是本回填回合自己执行的
独立复现，并与 supervisor 给定的期望值逐项比对。

**逐项验证（全部通过）**：

| 验证项 | 结果 |
|---|---|
| 源工件只读性 | `monitor-20260911-173920.json` SHA-256 `740c031e…4a458` 两次运行前后**逐字节不变**（运行前基线 = 运行后复核） |
| `history.jsonl` SHA-256 | `a71ff7e2816e55b1a2c964024cdeb8b00ba4fff6de482b3165ae86556dac5ae0`（985 bytes；与 supervisor 期望值一致） |
| `history-summary.md` SHA-256 | `80535c3a41699137bd52fdb37ed06b2c5c57f3cd1bbdafdab7bb306c351a7be`（1378 bytes；与 supervisor 期望值一致） |
| 两遍可复现性 | run 1 与 run 2 输出 `cmp` **逐字节相同**（零墙钟设计实证） |
| JSONL 行数与合法性 | 恰 **1 行**，`json.loads` 解析合法 |
| 行内容 | `overall_status=ok`；`partial=false`；行内 `artifact_sha256` == 源工件 SHA-256（**哈希链匹配**）；`collected_at=2026-09-11T17:39:20Z`；`source_stem=monitor-20260911-173920` |
| 指标（摘自该行） | threshold_counts ok=34/warn=0/critical=0；六 compose 服务全 healthy、RestartCount 全 0；五端点全 200（延迟 ms web-root 7.088/web-login 12.909/api-health 12.388/funasr-health 24.444/cosyvoice-health 27.641）；六日志 error_total 全 0 |

**回填回合自身边界**：docs-only（零代码/零测试改动）；除本工具只读运行
外零生产执行——未启动/停止/重启任何容器、未触碰计划任务/env/密钥/生产
数据；源工件与 canonical `.verify` 只读（SHA-256 前后不变实证），输出只
写本回填 worktree 的 gitignored `.verify`；按 supervisor 明确指令完成
commit/push/开 PR，但**不合并**；不触碰 untracked `.claude/`。

## 结论边界（不过度引申）

- 本切片交付监控/告警收口的**历史数据面第一块基础**：可复用、
  fail-closed 的工件索引 + 有界留存 + 趋势摘要工具及其契约测试；
  首次真实历史构建（canonical 单工件 → 1 行全绿历史）已由本回填回合
  独立执行并逐项验证。
- 未覆盖（后续切片范围）：持续/定时采集与调度（历史随每次
  monitor execute 增长，本工具按需重建索引；当前历史仅含**单样本**，
  趋势列 min/p50/p95/max 为单点值）、外部告警接入、指标时序存储/查询
  API、阈值随时间的自动标定、跨机监控。
- **`production_ready=false` 不变**；历史趋势可用性不构成生产就绪宣称。
