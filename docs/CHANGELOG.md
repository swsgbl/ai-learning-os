# Changelog

All notable changes to the AI Learning OS project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/), versions follow semver.

## [Unreleased]

### Added
- M9-01 多用户与认证基座：users 表（alembic 0022）+ bcrypt 密码哈希 + JWT（HS256）
  + register/login/me/status 端点 + 全业务路径 Bearer 门禁；
  AUTH_SECRET 未配置 = 认证关闭且 status 如实透出（存量客户端零破坏）
- M8-00 冒烟脚本与 Docker CI 门禁
- M10-01 LLM 接入：OpenAI 兼容 gateway + rubric LLM judge（fail-closed 进复核，
  默认关闭保持确定性判分；真实端点冒烟脚本 infra/smoke_llm.sh）

### Security
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
