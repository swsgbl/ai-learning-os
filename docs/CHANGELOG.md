# Changelog

All notable changes to the AI Learning OS project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/), versions follow semver.

## [Unreleased]

### Added
- M14-13 监控历史索引 + 有界留存 + 趋势摘要（`tools/ops/monitoring_history.py`，
  **工具交付、未执行真实历史构建**；本 Claude 开发回合仅做本地 commit，
  supervisor 审查与 remote 发布在其后进行）：对既有 M14-12 monitor JSON
  工件（默认 gitignored `.verify/artifacts/m14-12-production-monitoring/`，
  `--source-dir` 可覆盖）建立**只读**安全索引——仅发现 `monitor-*.json`
  （stem 严格白名单 `monitor-YYYYMMDD-HHMMSS` 含日历合法性，被拒名不
  回显）；严格 schema 校验（version/tool/milestone/mode/project 白名单/
  双 UTC 时间戳格式与顺序/overall_status∈{ok,warn,critical}/partial 恒
  false/阈值计数类型/六 compose 服务/六容器 RestartCount/五端点状态+
  延迟（有限数值）/六日志 error_total；incomplete/partial/malformed 一律
  fail-closed 输出零写入）；SHA-256 逐文件去重（同哈希保留 (collected_at,
  stem) 最小者，duplicate_count 显式；同 (project, collected_at) 不同
  哈希 = conflicting-duplicate 拒绝）；(collected_at, stem) 确定性排序；
  保留最新 N 条（默认 500、硬顶 5000）+ 显式 omitted_older_count 与
  oldest/newest 边界，**源工件永不改动/删除**；输出原子
  `history.jsonl`（紧凑记录：hash/stem/collected_at/project/
  overall_status/partial/threshold_counts/compose health/restart/端点
  状态+延迟/日志 error 总计——绝无原始日志行/密钥）+
  `history-summary.md`（状态计数/availability/degraded/critical、
  first/last、逐端点延迟 min/p50/p95/max（nearest-rank）、逐服务
  restart/error 总计、duplicate/omitted 计数），**生成时间戳取自最新
  源样本 collected_at——零墙钟、输出逐字节可复现**；symlink 全路径
  拒绝（源文件/源目录/输出/祖先）；tmp+fsync+os.replace 原子写（失败
  清 tmp 零残留）；零子进程/零网络/零容器面/零计划任务/零 env 读取
  （源码契约锁定）；一切 I/O 经 Store 注入。73 项契约测试 + 组合回归
  262 passed（M14-12 套件零回归）；开发回合零生产执行、零 canonical
  `.verify` 写入（全部验证用合成样本临时目录）。**仅闭环历史数据面
  工具证据——持续/定时采集与调度、外部告警接入、指标时序存储/查询、
  阈值随时间的标定、跨机监控、真实历史首次构建仍属未决；
  `production_ready=false` 不变**；证据见
  docs/evidence/m14-13-monitoring-history/
- M14-12 生产监控/告警 readiness 开发切片 + 首次真实只读生产监控结果：
  只读采集 + 阈值判定 + 证据
  报告（`tools/ops/production_monitor.py`，**工具交付并合并（PR #85）；
  开发回合未执行生产采集——supervisor 已于合并后执行首次真实只读
  监控（见本条末尾结果）**；
  本回合不接外部告警系统；本 Claude 开发回合仅做本地 commit，supervisor
  审查与 remote 发布在其后进行）：默认 plan 零
  subprocess/零网络/零生产读取（plan 报告零状态宣称）；execute 需
  `--execute` 旗标 + 精确确认短语 `EXECUTE READ-ONLY PRODUCTION
  MONITORING` + 全部阈值在硬顶内（R1 起非有限浮点 nan/inf/-inf 拒绝；
  `--project` 严格白名单——ASCII 字母数字开头、仅字母数字/连字符/
  下划线、≤64，被拒值不回显；均在 plan 报告写入/采集之前），缺一即
  EXIT 2 零采集；只读采集面
  固定画像（compose project `aios-m14-03-production-rehearsal`：compose
  ps + 六受管容器 inspect 五事实（state/health/RestartCount/image/
  started）+ 五默认端点 GET（Web 3011 `/`+`/login`、API 8000 `/health`、
  FunASR 8010/CosyVoice 8011 `/health`）状态+延迟 + `docker logs
  --tail` 容器日志安全错误摘要——只记匹配计数/级别/安全类别，原文绝不
  持久化）；一切 docker 命令经只读白名单门（仅 compose ps / inspect
  --format / logs --tail 三形态，stop/rm/kill/restart/down/exec/up/
  logs -f 等在任何执行之前拒绝；Windows 恒 CREATE_NO_WINDOW）；
  loopback 字面 IP/GET-only/`http.client` 直连零代理面与 M14-11 同款；
  采集器部分失败 `partial=true` + 安全类别（仅类别+异常类名）→
  `overall_status=incomplete`，缺失绝不当作 healthy；阈值全部含边界且
  warn 恒可见（compose 6/6 healthy、五端点恒 200、容器 health、
  RestartCount、日志错误计数、端点延迟 warn/critical）；schema v1
  JSON+Markdown 原子写（同目录 tmp+fsync+os.replace，拒绝 symlink
  组件/越界 stem）默认落 gitignored
  `.verify/artifacts/m14-12-production-monitoring/`（`--artifact-dir`
  自定义路径为操作者显式自选覆盖，位置与 gitignore 状态由操作者负责）；
  `monitoring_ready`
  仅采集完整且零 warn/critical 时 true——**≠ production ready，本工具
  绝不宣称生产就绪**；189 项契约测试锁定上述边界（含 supervisor 评审
  R1 修正回归：非有限浮点双路径、项目名白名单与零回显、artifact-dir
  口径、状态文档措辞 sweep；邻居回归 soak 61 /
  recovery 45 / startup 66 全绿）。交付随 **PR #85** 合并 main
  （merged_at 2026-09-11T17:34:05Z，merge commit
  `52980c637f4e39fec196a6563dbab1875faa6a8f`，feature head
  `80e599d07fb448766d97a105c99d83275a50784a`，远端 feature 分支已删除；
  PR CI run `34627846208` 与合并后 main push CI run `34628419347` 均全部
  5 job（Web/API/Docker/Android/Release tools）SUCCESS——CI 真实运行
  全绿）。**supervisor 于 2026-09-11T17:39:20Z–17:39:21Z 在 canonical
  main 执行首次真实只读生产监控**（gitignored
  `monitor-20260911-173920.json`（14185B）/`.md`（3330B），不入库）：
  compose `aios-m14-03-production-rehearsal` 六服务全部 running healthy、
  restart_count 全 0，五端点 GET 全 200（延迟 ms web-root 7.088/
  web-login 12.909/api-health 12.388/funasr-health 24.444/
  cosyvoice-health 27.641），逐容器日志 error_total 全 0，
  `partial=false`，阈值计数 ok=34/warn=0/critical=0，
  `overall_status=ok`，`monitoring_ready=true`——**单次只读快照全绿
  ≠ production ready**。本条目结果部分为 supervisor 给定事实的 docs-only
  回填（本 Claude 回合未执行监控/未部署/未重启服务/未触碰计划任务/
  未改 env/未读密钥/未写生产数据）。**仅闭环单次只读监控证据——持续/
  定时采集与调度、外部告警接入、指标历史与留存、阈值随时间的标定、
  跨机监控仍属未决；`production_ready=false` 不变**；证据
  见 docs/evidence/m14-12-production-monitoring/
- M14-11 生产 soak/并发彩排 harness + 有界只读生产 soak 执行结果
  （`tools/ops/soak_rehearsal.py`，开发回合不执行生产负载）：默认 plan
  零网络；execute 五要素门禁（`--execute` + 精确确认短语 +
  duration≤120s + concurrency≤8 + 总请求≤2000，超顶 fail-closed 零请求）；
  固定目标画像（Web 3011 `/`+`/login`、API 8000 `/health`，
  `--include-voice` 才加 8010/8011 低频 GET `/health`）；
  GET-only/unauthenticated/仅字面 loopback IP（零 DNS）/`http.client`
  直连零代理面；schema 版本化 JSON+MD 报告落 gitignored
  `.verify/artifacts/m14-11-production-soak/`（含每目标计数/分位延迟/
  吞吐/安全归类错误/deadline 语义，绝无 header/body/query/凭据/env 值
  入档）；61 项契约测试锁定上述边界。交付随 **PR #83** 合并 main（merge
  `14d7e2f`、feature `bc3b5ce`）；supervisor 于 2026-09-11 获准窗口执行
  三轮有界只读生产 soak **全部零失败**（120/300/2000 请求，concurrency
  2/4/4，p99 12.929–13.362ms，吞吐 18.462–36.644 rps，零
  `completed_after_deadline`，每目标全 200）；执行前后基线逐项一致
  （compose 6/6 healthy、五端点 200、api/web 容器 ID 与 started 不变、
  voice PID 1183/2061 不变、零恢复任务触发），语音面零负载（不宣称语音
  soak 或真实用户负载）；PR/main CI（run `34612290177`/`34612382356`）
  为已知外部 0-step 形态失败。仅闭环有界本地彩排 soak 证据——>60s 长稳、
  真实客户端负载形态、跨机等仍属未决；production_ready=false 不变；
  证据见 docs/evidence/m14-11-production-soak/
- M9-01 多用户与认证基座：users 表（alembic 0022）+ bcrypt 密码哈希 + JWT（HS256）
  + register/login/me/status 端点 + 全业务路径 Bearer 门禁；
  AUTH_SECRET 未配置 = 认证关闭且 status 如实透出（存量客户端零破坏）
- M8-00 冒烟脚本与 Docker CI 门禁
- M10-01 LLM 接入：OpenAI 兼容 gateway + rubric LLM judge（fail-closed 进复核，
  默认关闭保持确定性判分；真实端点冒烟脚本 infra/smoke_llm.sh）

### Fixed
- M14-13 CI R2：MinIO 社区版自 2025-10 起停止分发官方 Docker 镜像
  （source-only 分发），`minio/minio:latest` 拉取失败使 CI docker job 在项目
  构建之前即挂——改为本地自建官方 pin 源码镜像：新增 `infra/minio/Dockerfile`
  （源码 = codeload 官方不可变 commit URL
  `…/tar.gz/9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a`（= tag
  `RELEASE.2025-10-15T17-29-55Z`）、builder
  `golang:1.24.8-alpine3.22@sha256:3d78beb1…cc0ae5`、runtime
  `alpine:3.22@sha256:14358309…5dce` 全 digest pin；`CGO_ENABLED=0` +
  kqueue/trimpath + 显式 release/commit ldflags；依赖完整性只靠源码树内官方
  go.sum（`-mod=readonly`、GOSUMDB 默认开，零 bypass）；`GOTOOLCHAIN=local`；
  全文件零 `apk add`（BusyBox wget/tar 取源）；非 root（minio 1000:1000）+
  可写 /data；runtime 只含 minio 二进制）；compose minio 切到该本地构建
  （服务名/端口/env/卷/restart 不变，镜像锚点
  `aios/minio:RELEASE.2025-10-15T17-29-55Z`），healthcheck 由 `mc ready local`
  换 BusyBox wget 探 `/minio/health/cluster`（`mc ready` 消费的同一就绪信号
  源）；api depends_on minio healthy 与六服务集合不变。21 项静态契约测试
  （`services/api/tests/test_minio_selfbuild.py`）+ 邻居 compose 契约套件零回归
  （265 passed / 6 skipped）。本回合零镜像构建/零上游工件下载/零容器操作/
  零生产触碰（镜像首次真实构建在下一次 CI），production_ready=false 不变；
  供应链 rationale/pins/limitations/长期替代见
  docs/evidence/m14-13-minio-selfbuild/README.md

### Security
- M14-10 生产 Web 安全镜像上产（M14-05 + M14-09 的生产落地，与代码/配置合并
  分开记录）：代码/配置面已先行合并——M14-05（next 16.3.4）随 **PR #76**
  （head `539dbe1`、merge `ebbb700`）、M14-09（独立 `AIOS_WEB_IMAGE_TAG` 锚点）
  随 **PR #81**（feature `321fb50`、merge `433d018`）；2026-09-11 生产 env 补
  `AIOS_WEB_IMAGE_TAG=m14-05-security`（`AIOS_IMAGE_TAG=m14-03-prod-rehearsal`
  不变，supervisor 先仓库外备份 gitignored 真实 env，仅此一次非密钥键变更），
  canonical compose `up -d --no-build --no-deps web` 单独替换 web 容器
  （m14-03-prod-rehearsal → m14-05-security，运行时自报 Next.js 16.3.4），
  api/数据面/语音引擎零触碰（api 容器 ID 与启动时间不变；FunASR/CosyVoice
  owner PID 不变、两次恢复均 untouched，未重启）；升级后 Web `/`、`/login` 与
  API/FunASR/CosyVoice `/health` 全 200、compose 6/6 healthy；恢复任务 dry-run
  与真实 `Start-ScheduledTask` 双绿（六键 pin 6/6、`up` 幂等 no-op、语音
  untouched、LastTaskResult=0）。PR #81 CI run `34582538887` 为外部 0-step
  形态失败（非代码回归，CI 未运行不隐藏）。回滚锚点：`AIOS_WEB_IMAGE_TAG`
  改回 `m14-03-prod-rehearsal` 重跑同命令。单机生产栈口径，
  production_ready=false；证据见 docs/evidence/m14-10-production-web-upgrade/
- M14-09 Web 镜像 tag 独立发布/回滚锚点：compose web 镜像改读独立
  `AIOS_WEB_IMAGE_TAG`（默认 local，不再跟随 `AIOS_IMAGE_TAG`——只设
  AIOS_IMAGE_TAG 不改变 Web tag，消除 Web-only 升级时同 tag 混用两代镜像/
  破坏 recovery pin 的隐患）；production_recovery pin 扩为六键（新增
  AIOS_WEB_IMAGE_TAG，web 容器镜像独立在线事实，仍键名-only 不回显值）；
  build_release_candidate 同 tag 显式双变量（发布包语义不变）；env 模板与
  文档同步。**不改变当前生产容器**；合并后真实 recovery env 须补
  AIOS_WEB_IMAGE_TAG 键（否则 enforce 按缺必需键可见拒绝）。
- M14-05 Web 生产依赖安全修复：next 15.5.24 → 16.3.4（连带 eslint-config-next
  16.3.4），消除 next 内嵌 postcss@8.4.31 的 1 high（≤8.5.22 系列 GHSA：XSS/
  sourceMappingURL 任意文件读取/路径穿越）+ next 自身 1 moderate；
  `npm audit --omit=dev --registry=https://registry.npmjs.org` 归零，
  无 overrides/忽略脚本/手工篡改 lock；eslint.config.mjs 迁移 flat config、
  react-hooks v7 新诊断降 warn（业务组件零改动）；本地验证清单新增依赖安全
  门禁命令，证据见 docs/evidence/m14-05-web-security/；
  2026-09-11 rebase 至 main@cae7aa0 后全门禁复验通过（audit 0 漏洞 /
  install 无锁漂移 / lint / typecheck / build 全 exit 0，next 16.3.4 +
  嵌套 postcss 8.5.23 复核，standalone 产物与 Dockerfile 吻合）

## [0.1.0] - 2026-09-02

### Added — Foundation (M0)
- Production monorepo: FastAPI + Next.js + PostgreSQL + Redis + MinIO + LiveKit compose stack
- PostgreSQL repository + Alembic migrations (upgrade / downgrade roundtrip in CI)
- CI gates (GitHub Actions): web typecheck/lint/build + api ruff/pytest (real PG service container + migration roundtrip)
- Secrets & privacy config surface (.env.example, MinIO healthcheck, compose stack healthy + data survives restart)

### Added — Content (M1)
- Source Registry: seed 6-tier sources, CRUD + verify, duplicate 409, migration roundtrip
- License state machine: migration matrix, admission + storage guards (UNKNOWN/PROHIBITED fail-closed)
- Upload dedup by SHA-256 content hash, content-addressed object storage, license snapshot on upload
- Parser adapter framework + security fixes (license snapshot, chunked upload, dedup re-bind)
- Layout normalize pipeline (LaTeX symbols, tables, slides, formula blocks)
- Chunk & Evidence with page-level traceability, re-parse idempotent replacement
- Parse task queue (DB table + asyncio worker, retry/reset-restart recovery)
- Parse quality report (pages/blocks/formulas/tables/OCR/anomalous pages)

### Added — Exam + Grading (M2)
- QuestionSpec/PaperSpec discriminated-union schema + paper JSON import (all-or-nothing)
- ExamSession FSM (6 legal edges, 19 illegal rejections, dual-repo wiring)
- Server-authoritative timing (spoofed client time fields dropped)
- Append-only answer events (sequential + idempotent + concurrent-safe)
- Autosave & reconnect recovery (next_sequence authority)
- Idempotent submit & timeout settle (unique constraint convergence)
- Objective grader v2 (33 golden cases, rule_version provenance)
- Numeric/math grader (tolerance, same-dimension conversion, sympy whitelist, review tri-state)
- Subjective rubric pipeline (keyword-v1 judge, dual-review, evidence gate)
- Exam report aggregation (per-question four-branch scoring, concept scores, remediation)

### Added — Student Model (M3)
- LearningEvent projection from append-only events (attempt/time/concept/difficulty)
- Concept DAG versioned snapshots (publish/validation/version backtracking)
- StudentConceptState BKT-like explainable state (recompute idempotent)
- Misconception candidate → confirmed lifecycle (distinct-question evidence)
- FSRS-like review scheduler (difficulty modulated, early/late review factors)
- Daily planner (review/mistake_retry/new_learning with explainable reasons)
- Selection strategy (retry/weak/advanced, difficulty & history aware)

### Added — Voice (M4)
- LiveKit server/token boundary (exp required, student grants fixed, expiry testable)
- ASR/TTS adapter protocol + VOICE_MODE local/hybrid/cloud routing
- VoiceSession FSM (8-state matrix, barge-in never commits half answers)
- Deterministic intent parser (slots/commands/ambiguity, zero LLM)
- Answer normalizer (server re-parses transcript, event_id idempotency)
- Barge-in & playback control (pause/resume self-loops, semantic refusal matrix)
- Disconnect recovery (server state authority, committed answers preserved)
- Voice report (projects M2-11 judgment, layered playback)
- Latency tracing (7 stages, client/server source, honest absence)

### Added — Search & Governance (M5)
- Search provider abstraction (local-corpus + cloud-web, unavailable honestly disclosed)
- Query planner (slot recognition + multi-source plan/execute separation)
- SSRF/robots/rate-limit gate (private ranges, dangerous protocols, 30/min default)
- Result dedup/rank (official > oer > platform > community, reasons visible)
- Course importer (admission gate + concept extraction + human review queue)
- Paper extractor (section rules, page/score provenance, review queue)
- Course generation workflow (8-stage deterministic pipeline)
- Variant question generator (solution-preserving whitelist transforms)

### Added — Eval & Safety (M6)
- Golden grading set (28 cases, byte-identical reports, transparent mismatch)
- Voice eval set (30 cases, six categories, parser gap fixes)
- Citation eval (sampled evidence validity >= 90% gate)
- Prompt injection suite (license/time/score/voice invariants)
- Security suite (SSRF metadata/DNS-rebind, sandbox escape, injection fail-closed, secrets scan, privilege)
- Backup/restore drill (three-piece backup, manifest-verified restore, independent drill DB)
- Load & reliability (p95 <= 500ms budget, N+1 fix, crash-safe submit retry)

### Added — Release (M7)
- Production compose profile (--profile local one-command stack)
- Onboarding guide + executable walkthrough (<10min path, doc-implementation consistency guard)
- Privacy disclosure (docs/PRIVACY.md + config-item bidirectional guard)
- License report (deps/web/models/content sources/derived objects with honest UNKNOWN)
- Release checklist (nine acceptance gates, live checks fail honestly without a running API)
- Versioning & rollback (VERSION single source, /version endpoint, CHANGELOG, db-rollback CLI, AIOS_IMAGE_TAG anchor)

### Security
- sympy 字符白名单防 eval 注入；SSRF 私网/元数据端点全拒；上传内容寻址无路径成分；
  语音 event_id 跨会话隔离；判分 fail-closed 不清洗注入尾巴；.providers 视图无密钥形态
