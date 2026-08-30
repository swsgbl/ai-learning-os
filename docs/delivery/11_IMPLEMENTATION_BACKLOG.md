# 可执行开发 Backlog v2.2

用法：一次只分配一个任务给 coding agent。任务提示词来自 `07_AI_AGENT_PROMPT_PACK.md`，验收以本文为准。不要在 M0 未完成时并行开发语音和搜索。

## 1. M0 Foundation

| ID | 任务 | 依赖 | 提示词 | 验收 |
|---|---|---|---|---|
| M0-01 | 建立生产 monorepo | 无 | B | `apps/web`、`apps/api`、`services/*`、`packages/schemas`、`infra` 存在；根脚本可统一启动。 |
| M0-02 | Docker Compose 基础设施 | M0-01 | B | PostgreSQL、Redis、MinIO 健康检查通过；卷持久化；重启后数据仍在。 |
| M0-03 | FastAPI skeleton | M0-01 | B | health/readiness、structured log、request id、settings、异常处理可用。 |
| M0-04 | Next.js skeleton | M0-01 | B | 登录、今日学习、考试、语音、课程五个路由可渲染空状态。 |
| M0-05 | Alembic migration 基线 | M0-03 | B | migration up/down/dry-run 通过；schema 版本表可用。 |
| M0-06 | CI 门禁 | M0-01 | B | lint、typecheck、unit、migration check 全绿；PR 中必须显示结果。 |
| M0-07 | 密钥与隐私配置 | M0-03 | B | `.env.example` 完整；真实 key 不入库；隐私模式可配置 local/cloud/hybrid。 |

## 2. M1 Content Ingestion

| ID | 任务 | 依赖 | 提示词 | 验收 |
|---|---|---|---|---|
| M1-01 | Source Registry | M0-05 | H | S/A/B/C/U 分层、robots、rate limit、license state 和 last_verified_at 可落库。 |
| M1-02 | License State Machine | M1-01 | H | UNKNOWN 不能进入公共复用池；ACCESS_CONTROLLED 不保存正文；状态迁移有测试。 |
| M1-03 | 文件上传与 hash 去重 | M0-02 | C | 同一 PDF/DOCX/PPTX 重复上传不产生重复资源；文件进入 MinIO/S3。 |
| M1-04 | Parser adapter 框架 | M1-03 | C | Docling/MinerU/Marker/olmOCR 以 adapter 接入；fake parser 可注入测试。 |
| M1-05 | Layout normalize | M1-04 | C | 标题、段落、表格、公式、页码/slide 定位进入 blocks；公式转 LaTeX。 |
| M1-06 | Chunk 与 Evidence | M1-05 | C | 每个 chunk 可回到页码或 slide；Evidence 记录 parser、hash 和 locator。 |
| M1-07 | 解析任务队列 | M1-04 | C | 任务幂等、可重试、可恢复；失败后能换 parser 重跑。 |
| M1-08 | 解析质量报告 | M1-05 | C | 输出页数、成功块数、公式数、表格数、OCR 置信度和异常页。 |

## 3. M2 Exam + Grading

| ID | 任务 | 依赖 | 提示词 | 验收 |
|---|---|---|---|---|
| M2-01 | Question/Paper schema | M0-05 | D | MCQ、多选、判断、填空、数值、公式、编程、主观题 schema 可校验。 |
| M2-02 | 试卷 JSON 导入 | M2-01 | D | 导入失败逐行报错；成功后题目顺序、分值、答案和解析不变。 |
| M2-03 | ExamSession FSM | M2-01 | D | CREATED 到 REPORT_READY 全状态迁移有单元测试；非法迁移全部拒绝。 |
| M2-04 | 服务端权威计时 | M2-03 | D | start/end 由服务器写入；客户端改时间、刷新、重连不能延长考试。 |
| M2-05 | 答案 append-only event | M2-03 | D | sequence 唯一；重复/乱序/并发冲突按明确规则处理。 |
| M2-06 | 自动保存与断线恢复 | M2-05 | D | 刷新和断网后返回服务端答案与 server_remaining_seconds。 |
| M2-07 | 幂等提交与超时提交 | M2-04 | D | 重复提交返回同一 submission；超时只触发一次；过期提交拒绝。 |
| M2-08 | Objective grader | M2-05 | E | golden set 判分 100%；判分记录规则版本和输入事件。 |
| M2-09 | Numeric/math grader | M2-08 | E | 单位、容差、等价表达式有测试；不确定项进入复核。 |
| M2-10 | Subjective rubric grader | M2-08 | E | 输出结构化 JSON；低置信度双审；无 evidence 的课程事实不得作为依据。 |
| M2-11 | 考试报告 | M2-08 | D | 总分、题分、概念分、错题、解析、补救任务和证据链接完整。 |

## 4. M3 Student Model

| ID | 任务 | 依赖 | 提示词 | 验收 |
|---|---|---|---|---|
| M3-01 | LearningEvent 标准化 | M2-05 | F | 答案、耗时、提示、题目难度、概念映射和 attempt 可重放。 |
| M3-02 | Concept DAG | M1-06 | I | 课程概念、先修关系和能力要求可版本化。 |
| M3-03 | StudentConceptState | M3-01 | F | mastery、confidence、forgetting_risk 可从事件重算且更新幂等。 |
| M3-04 | Misconception candidate | M3-03 | F | 单次错误只生成 candidate；多次独立证据才提升置信度。 |
| M3-05 | FSRS-like scheduler | M3-03 | F | 错题生成 next_review_at；提前/延迟复习策略可测试。 |
| M3-06 | Daily planner | M3-05 | F | 今日任务包含新学、复习和错题重测；解释为什么被选中。 |
| M3-07 | 选题策略 | M3-03 | F | 第二次任务受弱概念、难度和历史错题影响。 |

## 5. M4 Voice

| ID | 任务 | 依赖 | 提示词 | 验收 |
|---|---|---|---|---|
| M4-01 | LiveKit server/token | M0-02 | G | 浏览器可加入房间；token 过期和权限边界可测试。 |
| M4-02 | ASR/TTS adapter | M4-01 | G | 在线、本地、混合 provider 可配置切换；语音原文按隐私策略落盘。 |
| M4-03 | VoiceSession FSM | M2-03 | G | 读题、读选项、等待答案、澄清、确认、下一题、报告状态完整。 |
| M4-04 | Intent parser | M4-03 | G | “选 B/第二个/改成 C/重复一遍/跳过/暂停/结束”解析准确。 |
| M4-05 | Answer normalizer | M4-04 | G | 语音答案只生成规范化答案事件，最终提交由服务端判定。 |
| M4-06 | Barge-in 与播报控制 | M4-03 | G | 打断不提交半成品答案；重复、放慢、跳过不破坏状态。 |
| M4-07 | 断线恢复 | M4-03 | G | 重连后从当前题、当前选项和服务端状态继续。 |
| M4-08 | Voice report | M4-05 | G | 全卷完成后播报总分、错题摘要和补救建议，并生成书面报告。 |
| M4-09 | Latency tracing | M4-02 | G | VAD、ASR、意图、FSM、LLM、TTS、首音频各阶段耗时可观测。 |

## 6. M5 Search + Course Generation

| ID | 任务 | 依赖 | 提示词 | 验收 |
|---|---|---|---|---|
| M5-01 | Search provider abstraction | M0-03 | H | 多搜索源可插拔；查询计划、结果和弃用原因可记录。 |
| M5-02 | Query planner | M5-01 | H | 识别学科、学校、年份、课程、题型和公开范围；生成多源查询。 |
| M5-03 | SSRF/rate limit/robots gate | M5-01 | H | 内网地址、危险协议、超限频率和 disallow 路径被拒绝。 |
| M5-04 | Result dedup/rank | M5-02 | H | 官方 > OER > 平台 > 社区；排序理由可见。 |
| M5-05 | Course importer | M1-02 | H | 从授权资源生成 Course/Concept/Resource 草稿并进入人工审核。 |
| M5-06 | Paper extractor | M1-05 | H | 从试卷 PDF 抽取题目草稿；原文页码、题型和分值保留。 |
| M5-07 | Course generation workflow | M3-02 | I | Goal -> Competency -> DAG -> Resource -> Outline -> Lessons -> Assessments -> Remediation。 |
| M5-08 | Variant question generator | M2-08 | I | 变式题保留概念映射和原题 evidence；不会只换表面数字导致无解。 |

## 7. M6 Evaluation / Hardening

| ID | 任务 | 依赖 | 提示词 | 验收 |
|---|---|---|---|---|
| M6-01 | Golden grading set | M2-10 | K | 覆盖所有题型；agreement report 可重复生成。 |
| M6-02 | Voice eval set | M4-04 | K | 覆盖噪声、口音、打断、数字、公式和命令；意图准确率可计算。 |
| M6-03 | Citation eval | M5-07 | K | 抽样解释的 evidence 有效率 >= 90%。 |
| M6-04 | Prompt injection suite | M5-03 | J | 恶意网页/文档/语音命令不能改变权限、时间、成绩和 license 状态。 |
| M6-05 | Security suite | M0-07 | J | 越权、SSRF、密钥泄露、注入和 sandbox escape 全部有回归测试。 |
| M6-06 | Backup/restore drill | M0-02 | K | 数据库、对象存储和配置可备份；恢复后考试与报告仍可读。 |
| M6-07 | Load and reliability | M2-07 | K | 非模型 API p95 <= 500ms；自动提交和评分任务在重试后不丢失。 |

## 8. M7 Release

| ID | 任务 | 依赖 | 提示词 | 验收 |
|---|---|---|---|---|
| M7-01 | Production Compose profile | M6-06 | K | 一条命令启动生产本地版；profile 分离 local/hybrid/cloud。 |
| M7-02 | Onboarding guide | M7-01 | K | 新用户 10 分钟内完成初始化、导入课程和第一套题。 |
| M7-03 | Privacy disclosure | M7-01 | K | 明确本地保存、云端处理、检索和语音数据的边界与开关。 |
| M7-04 | License report | M6-05 | K | 输出依赖、模型、内容源和派生对象的授权清单。 |
| M7-05 | Release checklist | M6-* | K | lint/typecheck/test/build/E2E/migration/backup/voice/license 全绿。 |
| M7-06 | Versioning and rollback | M7-05 | K | 版本、changelog、数据库回滚和应用回滚方案可执行。 |
