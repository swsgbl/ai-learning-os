# M14-245 证据:能力路线图真相对账(docs-only)

## 0. 结论与边界

- 动因:Codex 评审发现 `docs/ROADMAP.md` 顶层 M3/M4/M5 勾选状态与当前
  实现明显脱节。本切片对 `docs/delivery/11_IMPLEMENTATION_BACKLOG.md`
  M0–M7 全部 63 项任务逐项审计,以**当前代码、测试、alembic 迁移与
  docs/evidence 既有事实**为准(不以任何文档的 checkbox 或 ✅ 声明为准),
  建立 capability truth matrix,并同步修正 ROADMAP / PROJECT_STATUS /
  CHANGELOG 顶层状态。
- 审计基点:current main `748b522d0a0bf9a747963351d113641a55517640`
  (PR #331 merge,即 M14-244)。worktree 复用
  `ai-learning-os-worktrees/m14-244-current-provider-smoke-refresh`,
  分支 `docs/m14-245-capability-roadmap-truth`,执行前 porcelain 为空。
- **矩阵总判定(63 项)**:implemented **59** / partial **4**(M0-01、
  M0-03、M1-04、M4-09)/ not-implemented **0** / externally-blocked
  **0**(任务粒度上无外部阻塞项;外部阻塞集中在生产验收层,见 §4/§6)。
  (**2026-10-07 M14-246 更新**:M0-03 三项缺项——独立 readiness 端点、
  结构化 JSON 日志、全局 exception handler——已由 M14-246 实现并以契约
  测试锚定,本矩阵 M0-03 行同步改判 ✅,现行口径 implemented 60 /
  partial 3;证据 `docs/evidence/m14-246-api-observability/README.md`。)
- **发现的两处文档互相矛盾,本切片一并修正**:ROADMAP 顶层 M1 五项全
  未勾、M3 四项全未勾、M5 四项全未勾、M4 仅 1/5、M0 的「远程仓库/CI/
  PostgreSQL repository」未勾——而代码与测试均已实现(详见 §2/§3);
  反向地,`docs/PROJECT_STATUS.md` 顶部「当前里程碑」行的
  「M1 ✅ 8/8、M2 ✅ 11/11、M3 ✅ 7/7」整段完成声明在 M1-04 为
  partial 的口径下属于**过度声明**,本切片同步改为如实表述。
- **边界(不可越界解释)**:本切片一切「implemented/partial」判定仅指
  **代码能力与仓库内测试证据**,不等于、不可引用为
  `production_ready` / `release_ready` / `public_ready`。生产/公网/真机/
  真实 provider 证据的边界单独保留在 §4,权威结论不变:provider-smoke
  门整体 blocked(M14-209 聚合仍为最近完整聚合),cockpit_ready=false,
  production_ready=false、release_ready=false、public_ready=false
  (M14-242/M14-244 口径)。
- docs-only:零 Python/TypeScript/ArkTS/配置/CI 变更;未运行真实
  provider、未启停 Docker/WSL/Ollama/ASR/TTS、未触碰 DB/MinIO/设备/
  模拟器/生产服务/凭据。gitignored artifacts(`.verify/`、
  `artifacts/`)允许存在,不删除、不纳入提交。

## 1. 审计方法与判定口径

- 判定四类:
  - **implemented**:实现文件与测试均存在,且满足 backlog 验收条款
    (测试断言直接覆盖验收语义)。允许存在不违反验收的次要边界,
    边界照记。
  - **partial**:实现/测试存在但验收条款有实质缺项,或计划面
    (backlog 字面列举的组成部分)显著缺失。
  - **not-implemented**:无实现无测试。
  - **externally-blocked**:代码/工具就绪但验收依赖外部条件
    (材料、凭据、设备、人工审批)。
- 证据来源优先级:源码文件 > 测试文件(实测 `def test_` 计数)>
  alembic 迁移 > `docs/evidence/*/README.md` 已入库证据 > CI 记录
  (以 ROADMAP/CHANGELOG 已入库的 run 编号为准,本切片未重跑 CI)。
- 测试规模事实:`services/api/tests/` 166 文件 / 4019 个测试函数;
  `apps/web` vitest 11 文件 / 140 例;`tests/`(根)为 android/harmony/
  ops 契约套件。CI 五 job(web/api/docker/android/release-tools)与
  release-candidate.yml 存在且被 M14-243 钉定 ubuntu-24.04。
- 本切片审计方式:六路并行只读取证 + 主审计抽查复核关键推翻性结论
  (math grader 与 sympy 依赖、livekit-api 依赖、FSRS/docstring 验收
  锚定、docling 懒导入)均亲验属实。

## 2. 能力真相矩阵(M0–M7,63 项)

分类列取值:✅ implemented / 🟡 partial / ❌ not-implemented /
⛔ externally-blocked。测试数为该任务主证据文件的 `def test_` 实测数
(括号为关联文件)。

### M0 Foundation(7 项:6 ✅ / 1 🟡;M0-03 已由 M14-246 改判 ✅)

| ID | 任务 | 判定 | 证据(代码 / 测试) | 理由与边界 |
|---|---|---|---|---|
| M0-01 | 生产 monorepo | 🟡 | `apps/{web,android,harmony}`、`services/api`、`infra/`、根 `package.json` | 骨架在且 compose 统一启动;但 backlog 字面的 `packages/schemas` 不存在,根脚本 workspaces 仅 `apps/web`(统一启动靠 `infra/docker-compose.yml` 而非 npm 脚本) |
| M0-02 | Docker Compose 基础设施 | ✅ | `infra/docker-compose.yml` / `test_compose_profiles.py`(17)、`test_compose_restart_policy.py`(7) | postgres/redis/minio/api/web/livekit/searxng 七服务全 healthcheck+restart 策略;三数据卷持久化;livekit 挂 local/hybrid/cloud 三 profile、searxng 挂 search;CI docker job 真实构建冒烟 |
| M0-03 | FastAPI skeleton | ✅ | `services/api/app/main.py`、`app/core/{config,security,errors,logging}.py`、`app/ops/readiness.py`、`app/api/error_handlers.py` / `test_api_observability.py`(21) | `/health`、request-id 中间件(X-Request-ID 透传+格式校验)、Settings+validators、错误类型在;**M14-246(2026-10-07)补齐三项缺项并改判 ✅**:`/readyz` readiness(检查可注入,与 liveness 语义分离;reason 经安全面净化+空注册表 fail-closed)、bootstrap 路径结构化 JSON 日志(单行 JSON+request_id 关联+敏感键脱敏+异常只落类型与净化栈位置+级别语义保留)、全局 exception handler(DomainError→400/404/409 映射+未捕获异常 500 固定脱敏兜底,HTTPException/422 契约不变);uvicorn 自身 logger 不在本切片范围(见 m14-246 证据边界) |
| M0-04 | Next.js skeleton | ✅ | `apps/web/src/app/`(login/首页今日学习/exam/voice/review/library/progress/governance/download) / vitest 11 文件 140 例(含 `public-routes.test.ts` 路由契约) | 登录/今日学习/考试/语音四路由独立可渲染;「课程」无独立 /courses 路由,由 /library+/progress 承担(产品形态差异,非缺失) |
| M0-05 | Alembic 基线 | ✅ | `services/api/alembic/versions/`(27 个迁移)、`alembic/env.py`(offline dry-run) / `test_migrations.py`(4) | up/down/dry-run 具备;CI api job 实证 upgrade head→downgrade base→upgrade head |
| M0-06 | CI 门禁 | ✅ | `.github/workflows/ci.yml`(5 job)、`release-candidate.yml` | lint/typecheck/unit/migration 四门禁全在;另有 docker 构建冒烟、android、release-tools 与手动 RC 流水线;M14-243 已钉定 ubuntu-24.04 |
| M0-07 | 密钥与隐私配置 | ✅ | `.env.example`(107 行)、`app/core/config.py`(PrivacyMode 三档)、`app/api/routes/system.py` / `test_config_privacy.py`(4) | 隐私模式 local/cloud/hybrid 可配置+fail-closed;`GET /api/v1/system/privacy`;抽查无真实凭据入库(仅测试自造哨兵 fixture) |

### M1 Content Ingestion(8 项:7 ✅ / 1 🟡)

| ID | 任务 | 判定 | 证据 | 理由与边界 |
|---|---|---|---|---|
| M1-01 | Source Registry | ✅ | `app/domain/source.py`、`app/repositories/{sources,seed_sources}.py`、`app/api/routes/sources.py`、alembic `0002_sources` / `test_source_registry.py`(6) | S/A/B/C/U 分层、robots/rate_limit 快照、license_state、last_verified_at 全落库;边界:robots/限流的**执行**在 M5-03 web gate,sources 表字段仅快照 |
| M1-02 | License State Machine | ✅ | `app/domain/license.py`(7 态迁移矩阵)、`routes/sources.py` /license、`routes/resources.py` 上传守卫 / `test_license_state_machine.py`(12) | UNKNOWN 不入复用池、ACCESS_CONTROLLED 不存正文(403)、PROHIBITED 吸收态全有矩阵测试 |
| M1-03 | 上传与 hash 去重 | ✅ | `routes/resources.py` /upload、`app/repositories/resources.py`、`app/storage/objectstore.py`(MinioObjectStore 真实 boto3) / `test_resource_upload.py`(9)、`test_objectstore_bucket.py`(3) | SHA-256 内容寻址去重幂等;MinIO 真实实现(懒导入+幂等建桶),单测桩化,部署级另有集成证据 |
| M1-04 | Parser adapter 框架 | 🟡 | `app/parsing/{base,docling,jsonparser,fake,registry}.py` / `test_parser_adapters.py`(8) | 框架(Protocol/注册表/Unavailable 跳过/注入)完整;Docling 真实调用但依赖默认未装(requirements 未含);**MinerU/Marker/olmOCR 全库零命中——未实现**;另有 JsonDatasetParser 与 FakeParser |
| M1-05 | Layout normalize | ✅ | `app/parsing/normalize.py` / `test_layout_normalize.py`(5) | heading/paragraph/table/formula/page_marker 五类归一、页码继承、slide 透传、公式转 LaTeX(符号表级)、表格结构化;边界:LaTeX 化为符号映射级非完整数学转换 |
| M1-06 | Chunk 与 Evidence | ✅ | `app/parsing/chunking.py`、`app/repositories/chunks.py`、alembic `0005_chunks_evidence` / `test_chunk_evidence.py`(4) | chunk 带页码区间/slide;Evidence 记 parser/hash/locator/license 快照;重解析全量幂等替换 |
| M1-07 | 解析任务队列 | ✅ | `app/repositories/parsejobs.py`、`app/parsing/worker.py`、`app/main.py` lifespan、alembic `0006_parse_jobs` / `test_parse_job_queue.py`(5) | 幂等/重试(3 次)/恢复(启动 recover_stale_running)/换 parser 重跑全落地;形态为 PG 表状态机+进程内 asyncio 轮询(无外部 broker,单进程假设) |
| M1-08 | 解析质量报告 | ✅ | `app/parsing/quality.py`、`routes/resources.py` /quality / `test_quality_report.py`(4) | 页数/块数/公式/表格/OCR 置信度/异常页六项齐全;边界:ocr_confidence 目前仅 fake parser 产出 |

### M2 Exam + Grading(11 项:11 ✅)

| ID | 任务 | 判定 | 证据 | 理由与边界 |
|---|---|---|---|---|
| M2-01 | Question/Paper schema | ✅ | `app/domain/questions.py`(8 题型判别 answer 模型) / `test_question_schema.py`(5) | mcq/多选/判断/填空/数值/公式/编程/主观 8 题型 schema 校验齐全;边界:运行时 legacy 模型 seed 卷仅用 3 题型,8 题型经导入路径落地 |
| M2-02 | 试卷 JSON 导入 | ✅ | `app/api/routes/papers.py` /import、`app/repositories/paper_importer.py` / `test_paper_import.py`(2) | 逐行报错全量 422 回滚;顺序/分值/答案/解析保真有专测;仅 DB 模式(无 DB 503) |
| M2-03 | ExamSession FSM | ✅ | `app/domain/exam_fsm.py`、`app/repositories/{postgres,memory}.py`、alembic `0001_initial_schema`(exam_sessions 表) / `test_exam_fsm.py`(4)+`test_pg_integration.py` | 迁移矩阵穷举+非法拒绝;**PG 持久化自 0001 即在**(ROADMAP 顶层「PostgreSQL FSM 持久化」未勾为过期信息);边界:书面考试域状态实际停在 SUBMITTED,REPORT_READY 物理翻转仅 voice 域使用,不影响验收 |
| M2-04 | 服务端权威计时 | ✅ | `repositories/postgres.py`(服务端写 started_at/end_at、min(now,end_at) 封顶)、懒翻转 EXPIRED / `test_idempotent_submit.py`(3)等 7 项 | 客户端无法传时间;过期后作答 409;无后台 timeout worker——由读时懒翻转+提交时结算替代(见 §5 缺口 11) |
| M2-05 | 答案 append-only event | ✅ | `repositories/postgres.py` save_answer、`uq_answer_events_exam_sequence` / `test_answer_events.py`(5)+真 PG 并发测试 | 同序同内容幂等/同序异内容 409/乱序 409/并发 IntegrityError→显式 409,规则全测 |
| M2-06 | 自动保存与断线恢复 | ✅ | `routes/exams.py` PUT answers、`routes/papers.py` exam_out(answers+server_remaining_seconds+next_sequence) / `test_disconnect_recovery.py`(3) | 刷新恢复服务端答案+权威续号;自动保存为客户端驱动 PUT+全量回读 |
| M2-07 | 幂等/超时提交 | ✅ | `repositories/postgres.py` _submit_once、`uq_submissions_exam_session_id` / `test_idempotent_submit.py`(3,memory+PG 参数化) | 重复提交同结果、超时结算恰一次、并发单份 submission |
| M2-08 | Objective grader | ✅ | `app/domain/grading.py`(objective-v2) / `test_objective_grader.py`(5) | golden set 判分、别名归一、rule_version 留痕、事件可重放;coding/essay 显式不判(转 M2-09/10 语义) |
| M2-09 | Numeric/math grader | ✅ | `app/domain/grading.py` grade_numeric(纲量表换算/容差)/grade_math(sympy 恒等;`requirements.txt` 含 sympy>=1.13) / `test_numeric_math_grader.py`(5) | 单位同量纲互转、跨纲量判错、等价表达式 sympy 化简、不可信字符永不 eval、不确定进复核不计分——**ROADMAP 顶层「数学/主观题 grader」未勾为过期信息**;边界:见 §5 缺口 2 |
| M2-10 | Subjective rubric grader | ✅ | `app/domain/rubric_grader.py`(269 行)、`app/llm/rubric_judge.py` / `test_rubric_grader.py`(14) | 结构化 JSON、confidence<0.7 双审、evidence gate(不存在 evidence_id 降不确定);边界:内置 KeywordRubricJudge 双审为同 judge 重跑(自一致),异构双审需配 LLM judge |
| M2-11 | 考试报告 | ✅ | `app/domain/report.py`、`routes/exams.py` /report / `test_exam_report.py`(6)+真 PG 流程 | 总分/题分(rubric 部分给分)/概念分/错题诊断/解析/补救任务/证据链接全字段 |

### M3 Student Model(7 项:7 ✅)

| ID | 任务 | 判定 | 证据 | 理由与边界 |
|---|---|---|---|---|
| M3-01 | LearningEvent 标准化 | ✅ | `app/domain/learning_events.py`、`routes/exams.py` /learning-events / `test_learning_events.py`(9)+PG | latency/difficulty/concept_ids/attempt/correctness(复用判分管线)可重放幂等;无独立表——answer_events 为事件源、LearningEvent 为投影(ADR 3 设计语义) |
| M3-02 | Concept DAG | ✅ | `app/domain/concept_dag.py`、`app/repositories/concept_dag.py`、alembic `0009_concept_dag` / `test_concept_dag.py`(10)+PG | 版本化不可变快照(versions 唯一)、悬空/自环/环校验、admin 发布+审计链 |
| M3-03 | StudentConceptState | ✅ | `app/domain/student_state.py`、alembic `0010_student_concept_state` / `test_student_state.py`(15)+真 PG 幂等重算 | BKT-like 增量、confidence、forgetting_risk 显式锚定 now;重算=全量 replace 幂等;边界:重算由端点显式触发非自动 |
| M3-04 | Misconception candidate | ✅ | `app/domain/misconceptions.py`、alembic `0011_misconceptions` / `test_misconceptions.py`(10)+PG | 单次错误只出 candidate、独立证据=不同题、≥3 才 confirmed |
| M3-05 | FSRS-like scheduler | ✅ | `app/domain/review_scheduler.py` / `test_review_scheduler.py`(10) | 错题生成 next_review_at;提前复习增长打折/延迟加成/难度调制均有专项测试;不落库为 ADR 29 显式决策(投影幂等) |
| M3-06 | Daily planner | ✅ | `app/domain/daily_planner.py` / `test_daily_planner.py`(8)+PG | 新学/复习/错题重测三通道+强制可解释 reason(「昨日错题今日出现」有专测) |
| M3-07 | 选题策略 | ✅ | `app/domain/selection.py` / `test_selection.py`(7)+PG | retry/weak(mastery<0.6)/advanced 三因素+难度双向影响+逐条 reason |

### M4 Voice(9 项:8 ✅ / 1 🟡)

| ID | 任务 | 判定 | 证据 | 理由与边界 |
|---|---|---|---|---|
| M4-01 | LiveKit server/token | ✅ | `app/domain/voice_tokens.py`(livekit-api 签发/验签,requirements 含 livekit-api)、`routes/voice.py` /token、`infra/livekit/*.yaml`、web `livekit-client`+`livekit-connect-card.tsx` / `test_voice_tokens.py`(10)、`test_verify_web_livekit_client.py`(46) | grants 边界/TTL 过期可精确断言;真容器冒烟脚本在;m14-35/37/38 证据:LAN cutover 生产彩排栈真实执行通过(默认拓扑存在间歇性 ICE 失败,受控 flag 3/3 过) |
| M4-02 | ASR/TTS adapter | ✅ | `app/voice/providers.py`(402 行:CloudOpenAi ASR/TTS、LocalFunAsr、LocalCosyVoice 真实 HTTP;FakeAsr/ToneTts 显式降级替身)、`app/voice/routing.py`(三模式+fallback 透出) / `test_voice_providers.py`(31)+`test_voice_local_providers.py`(26)+4 文件(55) | 在线/本地/混合可配置切换;transcript 恒落 voice_transcripts、音频按 PRIVACY_STORE_AUDIO;**无 Whisper provider**;fake/tone 不虚报;真实引擎部署链 tools/voice/bootstrap_* + m14-33 双引擎冒烟证据 |
| M4-03 | VoiceSession FSM | ✅ | `app/domain/voice_session_fsm.py`(8 状态×15 事件矩阵)、`app/repositories/voice_sessions.py`(revision 乐观锁)、alembic `0013` / `test_voice_session_fsm.py`(27) | 读题/读选项/等待/澄清/确认/下一题/报告全状态+非法迁移 409 |
| M4-04 | Intent parser | ✅ | `app/domain/intent_parser.py`(零 LLM 确定性)、`routes/voice.py` /intents / `test_intent_parser.py`(17) | 选B/第二个/改成C/重复/慢一点/跳过/暂停/继续/结束全覆盖+含费澄清;另有 30 案金标评测集(`test_voice_eval.py` 9) |
| M4-05 | Answer normalizer | ✅ | `app/domain/answer_normalizer.py`、`routes/voice.py` /answers / `test_answer_normalizer.py`(18) | 服务端从 transcript 重新规范化(不信任客户端)、event_id 幂等、无效给 reason 供澄清、绝不猜答案 |
| M4-06 | Barge-in 与播报控制 | ✅ | FSM 迁移矩阵(EV_BARGE_IN/播报控制自环)+intent 映射 / `test_voice_session_fsm.py` 专项 6+2 | 打断不提交半成品(播报期 answer_proposed 一律 409)、pause/resume 任意非终态自环;TTS 实际停播为客户端行为(web 端 speechSynthesis.cancel 在) |
| M4-07 | 断线恢复 | ✅ | `routes/voice.py` resume/list(凭 exam_id 找回) / `test_voice_resume.py`(6) | resume 返回服务端状态+当前题公开字段+已提交答案(exam answer_events 权威值);幂等只读、终态 409 |
| M4-08 | Voice report | ✅ | `app/domain/voice_report.py`、`routes/voice.py` /report / `test_voice_report.py`(10) | 分层播报(短结论/总分/错题摘要/概念级补救)+书面报告 URL;纯投影不判定、终态+判分前置 |
| M4-09 | Latency tracing | 🟡 | `app/domain/voice_trace.py`(七阶段白名单 vad/asr/intent/fsm/llm/tts/first_audio)、alembic `0015_voice_trace_spans`、`routes/voice.py` /trace+/trace/summary / `test_voice_trace.py`(12) | 服务端 asr/tts/intent/fsm 自动埋点+p50/p95 聚合完整;**缺口:apps/web 无任何调用 /voice/trace 的代码——vad/llm/first_audio 客户端上报通道存在但仓库内无 Web 客户端埋点,端到端时延闭环未打通** |

### M5 Search + Course Generation(8 项:8 ✅)

| ID | 任务 | 判定 | 证据 | 理由与边界 |
|---|---|---|---|---|
| M5-01 | Search provider abstraction | ✅ | `app/search/providers.py`(SearchProvider Protocol+ProviderEntry)、`routes/search.py` 四端点、alembic `0016_search_queries` / `test_search.py`(25)+searxng/smoke(25) | 可插拔+弃用原因落库;实际 provider=local-corpus(chunks 检索)+cloud-web(SearXNG-compatible,fail-closed);brave/duckduckgo/google cse 非一等 provider(仅 SearXNG 上游);真实外部验证:m14-66 本地 SearXNG 栈 live PASS + **m14-244 search 单步真实 pass(M14-207 以来首次)** |
| M5-02 | Query planner | ✅ | `app/domain/query_planner.py` / `test_query_planner.py`(14) | 学科/学校/年份/课程/题型/公开范围 6 槽位纯规则识别;计划/执行分离、不可用源带 unavailable_reason;无 LLM |
| M5-03 | SSRF/rate limit/robots gate | ✅ | `app/domain/web_gate.py`、`routes/web.py` /fetch-check、`app/main.py` per-IP RateLimiter / `test_web_gate.py`(15)+security suite SSRF 回绑 | 危险协议/保留段 v4+v6/不可解析/超长 URL/robots disallow/固定窗口限流全拒且有因;**边界:该门为预检 API,仓库内无真实页面抓取器消费它(见 §5 缺口 5;不影响本任务字面验收)** |
| M5-04 | Result dedup/rank | ✅ | `app/domain/result_ranker.py`、集成于 /search/queries / `test_result_ranker.py`(13) | official>oer>platform>community 分层+URL 规范化去重+每条 rank_reason 可审计;不猜权威(未标注兜底 community) |
| M5-05 | Course importer | ✅ | `app/domain/course_importer.py`、`routes/courses.py` 审核队列、alembic `0017_course_import_drafts` / `test_course_importer.py`(15) | 授权门禁(license 快照→REUSE_ADMISSION,NOT_ADMISSIBLE 一律 403 带因)+admin approve/reject 人工审核 |
| M5-06 | Paper extractor | ✅ | `app/domain/paper_extractor.py`、alembic `0018_paper_question_drafts` / `test_paper_extractor.py`(11) | 页码区间/题型映射/分值保留、无题不虚报、pending_review 审核流;PDF 经解析管线(需 parse_status=parsed) |
| M5-07 | Course generation workflow | ✅ | `app/domain/course_workflow.py`(八阶段)、alembic `0019_course_generation_drafts` / `test_course_workflow.py`(10) | Goal→Competency(含 alias)→DAG 闭包+拓扑→Resource→Outline→Lessons→Assessments→Remediation 全链;**零 LLM 纯确定性管线(显式设计);Assessments 为模板非真题;资源仅本地语料** |
| M5-08 | Variant question generator | ✅ | `app/domain/variant_generator.py`、alembic `0020_variant_question_drafts` / `test_variant_generator.py`(13) | 解保持白名单变换(context_swap/numeric_scale)+概念映射与 evidence 完整保留+0 变式不虚报;无 LLM |

### M6 Evaluation / Hardening(7 项:7 ✅)

| ID | 任务 | 判定 | 证据 | 理由与边界 |
|---|---|---|---|---|
| M6-01 | Golden grading set | ✅ | `app/domain/eval_framework.py`(28 案×8 题型三态)、alembic `0021_eval_runs`、`routes/eval.py` / `test_eval_framework.py`(9) | agreement report 可重复生成+落库;边界:agreement 是对同一判分器固化预期,非独立双评 |
| M6-02 | Voice eval set | ✅ | `app/domain/voice_eval.py`(30 案:噪声/口音/打断/数字/公式/命令) / `test_voice_eval.py`(9) | 意图准确率可计算;边界:评测对象为文本转写,**无真实音频/ASR/口音录音语料**(见 §5 缺口 9) |
| M6-03 | Citation eval | ✅ | `app/domain/citation_eval.py` / `test_citation_eval.py`(10) | 有效率>=0.9 阈值判定真实实现(边界值含);契约测试+种子语料 |
| M6-04 | Prompt injection suite | ✅ | `test_prompt_injection_suite.py`(11)+`test_web_gate.py`(15) | 文档/自审批/语音/伪造时间/提交 payload/判分字面化/搜索惰性;「恶意网页」经本地解析文档间接覆盖,无在线恶意页抓取回归 |
| M6-05 | Security suite | ✅ | `test_security_suite.py`(11) | 越权/SSRF(含回绑)/密钥静态扫描/SQL 元字符/路径穿越/sympy 沙箱;m14-05 为真实 npm audit 修复证据 |
| M6-06 | Backup/restore drill | ✅ | `app/ops/backup.py`(322 行) / `test_backup_drill.py`(4) + **真实演练**:`docs/evidence/m14-85-backup-restore/`(生产库只读备份 30 表+隔离库恢复 30/30 全等 verified=true) | 契约+真实演练双全;毁库恢复逐字一致/篡改 fail-closed 全测 |
| M6-07 | Load and reliability | ✅ | `test_load_reliability.py`(3) | 真压测:uvicorn 真实 TCP+真实 PG,12 并发×480 样本 nearest-rank p95<=500ms 断言(docstring 记录实测 651ms→修 N+1→261ms);PG 门控;重试不丢失为故障注入契约+PG 持久化(门控) |

### M7 Release(6 项:6 ✅)

| ID | 任务 | 判定 | 证据 | 理由与边界 |
|---|---|---|---|---|
| M7-01 | Production Compose profile | ✅ | `infra/docker-compose.yml` / `test_compose_profiles.py`(17,含 AIOS_COMPOSE_SMOKE=1 门控真启动冒烟) | 一条命令生产启动;local/hybrid/cloud(+search)profile 分离;README 有官方命令 |
| M7-02 | Onboarding guide | ✅ | `docs/ONBOARDING.md`(200 行 5 步) / `test_onboarding_guide.py`(2,端到端回放+600s 预算+文档-实现漂移守卫) | 10 分钟口径有测试锚定;TestClient 进程内,非新机人工计时 |
| M7-03 | Privacy disclosure | ✅ | `docs/PRIVACY.md`(89 行) / `test_privacy_disclosure.py`(4) | 本地/云端/检索/语音数据边界与开关成文且与配置互相守卫 |
| M7-04 | License report | ✅ | `app/ops/license_report.py` / `test_license_report.py`(9) | API 依赖实测安装元数据、web 读 node_modules、诚实 UNKNOWN;模型区仅槽位来源 |
| M7-05 | Release checklist | ✅ | `app/ops/release_check.py`(九门:lint/typecheck/test/build/E2E/migration/backup/voice/license)、`infra/build_release_candidate.sh`、`release-candidate.yml` / `test_release_checklist.py`(29)+`test_release_candidate.py`(66) | command 项真子进程与 CI 同构,live 项如实 fail;m14-63/96 为真实 RC 验证证据 |
| M7-06 | Versioning and rollback | ✅ | `VERSION`、`docs/CHANGELOG.md`、README 回滚节 / `test_versioning_rollback.py`(13)+`test_migrations.py` | 三源版本同步守卫;db-rollback CLI dry-run/--yes;alembic 真 up-down 往返;应用回滚=compose 镜像 tag 锚点(M14-09 渲染实测) |

## 3. ROADMAP 顶层粗粒度项对账(M0–M5)

ROADMAP 顶层行与 backlog 粒度不同,单独对账(勾选=代码+测试已实现;
本表驱动本次 ROADMAP 顶层修订):

| ROADMAP 顶层行 | 原状态 | 对账后 | 依据 |
|---|---|---|---|
| M0 远程仓库 | [ ] | **[x]** | origin=github.com/swsgbl/ai-learning-os(本基点即 PR #331 merge,已入库 331+ PR 记录) |
| M0 CI | [ ] | **[x]** | ci.yml 五 job+release-candidate.yml;M14-243 钉定;M14-242 ci-main 5/5 success |
| M0 PostgreSQL repository | [ ] | **[x]** | `app/repositories/postgres.py`(513 行全量持久化);exam_sessions 等 27 迁移;部署默认 PG |
| M1 Source Registry | [ ] | **[x]** | M1-01 |
| M1 License State Machine | [ ] | **[x]** | M1-02 |
| M1 文件上传与 hash 去重 | [ ] | **[x]** | M1-03 |
| M1 Docling/MinerU/Marker/olmOCR adapter | [ ] | **[ ](部分)** | 框架+Docling(真实但依赖默认未装)+JSON 已实现;MinerU/Marker/olmOCR 未实现——保持未勾,行内注明部分 |
| M1 Evidence 与页码定位 | [ ] | **[x]** | M1-05/M1-06 |
| M2 PostgreSQL FSM 持久化 | [ ] | **[x]** | M2-03:0001 起 exam_sessions/answer_events/submissions 全 PG |
| M2 Redis timeout worker | [ ] | **[ ](有意替代)** | 未实现 Redis worker;由读时懒翻转 EXPIRED+提交时 min(now,end_at) 结算替代,M2-04 验收已满足;后台 worker 语义仍是可选开发缺口 |
| M2 数学/主观题 grader | [ ] | **[x]** | M2-09(numeric 纲量+sympy math)/M2-10(rubric 双审+evidence gate) |
| M3 四项(LearningEvent/Concept DAG/StudentConceptState/FSRS) | [ ] | **[x]×4** | M3-01/02/03/05 全实现 |
| M4 LiveKit server/token | [ ] | **[x]** | M4-01(livekit-api+web 客户端+LAN cutover 证据) |
| M4 FunASR/CosyVoice 本地 adapter | [ ] | **[x]** | M4-02(LocalFunAsr/LocalCosyVoice 真实 HTTP;tools/voice bootstrap;生产栈曾 managed-running) |
| M4 在线 provider fallback | [ ] | **[x]** | M4-02(cloud-openai ASR/TTS+routing 三模式 fallback 透出) |
| M4 VoiceSession FSM 与打断恢复 | [ ] | **[x]** | M4-03/M4-06/M4-07 |
| M5 多源搜索 | [ ] | **[x]** | M5-01(local-corpus+cloud-web;m14-244 search 真实 pass) |
| M5 去重排序 | [ ] | **[x]** | M5-04 |
| M5 受控抓取 | [ ] | **[ ](部分)** | SSRF/robots/rate 预检门已实现(fetch-check API);真实抓取管线未落地,门暂无消费者——保持未勾,行内注明 |
| M5 课程/试卷导入审核 | [ ] | **[x]** | M5-05/M5-06 双人工审核队列 |

## 4. 产品能力与生产验收边界

三层口径必须分开,任何上层不得引用下层结论:

1. **代码+仓库内测试层(本矩阵判定层)**:63 项中 59 implemented / 4
   partial。4019 pytest + 140 vitest + CI 五 job 绿(M14-242 ci-main
   5/5)。这层只证明「能力存在且验收语义有测试锚定」。
2. **本地真实栈层**:compose 全栈(含 SearXNG digest pin、LiveKit、
   MinIO)、LiveKit LAN cutover 彩排栈实证(m14-38)、FunASR/CosyVoice
   双引擎 WSL 部署+端到端冒烟(m14-33)、本地 SearXNG live(m14-66)、
   search provider-smoke 单步真实 pass(m14-244)。这层证明「本机真实
   组件可跑通」。
3. **生产/公网/真机层(权威口径)**:生产栈曾真实切换并 7/7 healthy
   (m14-117/124,当时 release-check 10/10);**当前**权威结论:
   provider-smoke 门整体 **blocked**(M14-209 聚合 voice pass /
   search fail / llm fail 仍为最近完整聚合;m14-244 仅 search 单步
   pass,voice/llm 预检 not_ready)、cockpit_ready=false、
   production_ready=false、release_ready=false、public_ready=false
   (M14-242)。release-approval 为 human-only 门,从未发生,本切片
   亦不代拟。

**不可越界解释清单**:测试通过≠生产就绪;单步 provider pass≠聚合门
通过;m14-124 时点 release_ready=true 为历史时点证据,不延续;真机/
公网/AGC 签名/真实 provider 边界一律以 §6 外部缺口为准。

## 5. 真正剩余的产品开发缺口(代码侧,按影响排序)

1. **多 parser 接入缺口(M1-04)**:MinerU/Marker/olmOCR 三 adapter
   未实现;Docling adapter 真实但依赖未进 requirements(默认部署自动
   降级为仅 json-dataset)。PDF 主链路当前依赖按需安装 Docling。
2. **coding 题 grader 缺失**:schema 支持 8 题型,但 coding 判分恒
   False(`test_objective_grader.py` 明确断言此行为)。不在 M2-08/09
   字面验收内,但为题型覆盖的产品缺口。
3. **Web 语音 UI 与服务端语音链路未合并**:web voice-studio 仍用浏览器
   原生 SpeechRecognition/speechSynthesis;LiveKit+服务端 ASR/TTS/
   intent/FSM 链路真实存在(m12-03 Android 已接)但未进入 Web 同一 UI。
4. **M4-09 客户端时延埋点缺失**:vad/llm/first_audio 上报通道在,
   apps/web 零调用,端到端时延闭环未打通。
5. **受控抓取管线未落地**:web_gate(fetch-check)预检门无消费者,
   「多源搜索→受控抓取→解析入库」链路缺中段。
6. **M0-03 API 可观测性缺项**:独立 readiness 端点、结构化 JSON 日志、
   全局 exception handler。(**2026-10-07 已由 M14-246 关闭**,见
   `docs/evidence/m14-246-api-observability/README.md`。)
7. **M0-01 monorepo 统一编排缺项**:packages/schemas 不存在;根脚本
   仅编排 web workspace。
8. **rubric 异构双审**:内置 KeywordRubricJudge 双审自一致;异构双审
   需配置 LLM judge(rubric_judge=llm 代码在,配置面未成文档化默认)。
9. **M6-02 真实音频评测集**:voice eval 为文本转写层金标,无真实噪声/
   口音/打断录音语料。
10. **M5 provider 广度**:一等 provider 仅 local-corpus+cloud-web;
    多源广度完全依赖 SearXNG 上游配置(当前上游部分 unresponsive)。
11. **后台超时 worker(可选)**:Redis timeout worker 未实现;读时懒翻转
    +提交时结算已满足 M2-04 验收;若需服务端主动超时收卷/通知语义,
    仍是开发缺口。
12. **exam 域 REPORT_READY 物理写路径**:书面考试状态实际停在
    SUBMITTED(报告按需读取);REPORT_READY 翻转仅 voice 域使用。

## 6. 外部验收缺口(非代码能解决,须授权/材料/环境)

1. **Harmony AGC 签名与真机**:AGC 发布材料(证书/profile)缺位;无已
   签名 HAP;未验真机。发布链五阶段工具已交付(M13-16)但止于未签名。
2. **Android 公网真机冒烟**:公网真机链路曾 blocked(m14-226/229
   focus 探针修复后 launch 门真机实证通过,受控 remediation 仍有后续)。
3. **provider-smoke 聚合三输入环境就绪**:ASR 8010/TTS 8011 health、
   Ollama 11434 /api/ps 模型驻留(m14-244 预检均 not_ready;任务边界
   禁止本切片启动)。聚合门解锁后需全新三输入重跑。
4. **release-approval human-only 门**:从未发生,不可代拟。
5. **公网边缘与公网分发(public_ready)**:公网边缘证据链已收口
   (m14-160/163/166)但 public_ready=false 维持;公网分发待授权。
6. **生产环境复验窗口**:M14-124 时点生产证据后需新的生产只读
   preflight/发布证据刷新(代码已绑定门在,M14-242 已刷新至
   `82a8d02` 后继链)。

## 7. 下一步可执行的代码切片候选

(均以本矩阵缺口为据,排序按价值/风险;每片可独立 PR)

1. **parser-mineru-adapter(M1-04)**:`app/parsing/mineru.py`+
  registry 注册+requirements 可选 extra+`test_parser_adapters.py`
  扩展;验收=MinerUUnavailable 注入测试+真实文档形状契约测试。
2. **coding-grader(M2 缺口 2)**:`app/domain/grading.py` 增确定性
  coding 判分(输出比对或白名单测试执行)+golden set 扩展;验收=
  coding 案不再恒 False。
3. **web-voice-livekit-unify(缺口 3)**:voice-studio 接
  livekit-connect-card 管道+服务端 /transcribe /synthesize 回退链+
  intent/answers 端点;验收=Web 端到端语音陪练走服务端权威链。
4. **web-voice-trace-client(缺口 4)**:web 侧 vad/llm/first_audio
  埋点→POST /voice/trace;验收=/trace/summary 七阶段非零样本。
5. **web-fetch-pipeline(缺口 5)**:受控抓取器消费 fetch-check
  (gate 通过才抓)+内容进解析管线;验收=SSRF 负例端到端拒绝。
6. **api-readiness-logging(M0-03)**:/readyz+JSON 日志 formatter+
  全局 exception handler;验收=对应契约测试。(**2026-10-07 已由 M14-246
  交付**,21 项契约测试,见 `docs/evidence/m14-246-api-observability/`。)
7. **llm-rubric-judge-default(缺口 8)**:rubric_judge=llm 配置文档
  化+异构双审集成测试(注入假 LLM judge 断言不一致进复核)。

## 8. 文档变更清单(本切片)

- 新增:`docs/evidence/m14-245-capability-roadmap-truth/README.md`(本文件)
- 更新:`docs/ROADMAP.md`(顶层 M0–M5 勾选如实化+指向本证据)
- 更新:`docs/PROJECT_STATUS.md`(当前里程碑行如实化+当前任务快照链)
- 更新:`docs/CHANGELOG.md`(新增 M14-245 节)

## 9. 验证记录

- docs-only:零 Python/TypeScript/ArkTS/配置/CI 变更
  (`git diff --stat` 仅 4 个 docs 文件)。
- `git diff --check`:干净(无空白错误)。
- 新增 tracked 行卫生扫描(三文件 diff `+` 行+本文件全量,共 421 行):
  secret/token/password/key/bearer/credential 赋值形态 **0 命中**(宽松
  复查的全部命中均为扫描说明文字本身与能力名词引用——如 LiveKit
  server/token 端点、`voice_tokens.py` 文件名,非赋值形态)、U+FFFD
  **0**、绝对本地路径(盘符形态)**0**、Windows 用户名 **0**。
- 无代码测试适用:本切片为零代码变更,按任务边界如实说明——不运行
  任何测试套件,不引入任何依赖;纯文本/结构核对(表格列数、路径存在
  性抽查)通过。
