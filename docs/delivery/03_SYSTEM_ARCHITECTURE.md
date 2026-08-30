# 系统架构设计 v2

## 1. 总体架构

```text
Experience Layer
  Web Exam UI / Daily Plan / Voice UI / Course Workspace / Reports
        |
        | HTTPS / WebSocket / WebRTC
        v
API Gateway
  Auth / Rate limit / Request logging / Route policy
        |
        v
Learning Runtime
  Session Manager
  Exam Engine
  Voice Session FSM
  Learning Planner
  LangGraph Workflows
        |
        +------------------+------------------+
        |                  |                  |
   Student Model       Course Model      Question Model
   mastery/mistakes    concepts/DAG      difficulty/bonds
        |                  |                  |
        +------------------+------------------+
                           |
                     Learning Policy
                           |
        +------------------+------------------+
        |                  |                  |
   Tutor Policy       Assessment       Research Planner
        |                  |                  |
        v                  v                  v
  Evidence / Tools / Model Gateway / Governance
```

数据平面：

- PostgreSQL：核心业务对象、事件、概念图、权限、任务。
- pgvector：文档 chunk、题目和概念 embedding。
- Redis：会话缓存、锁、队列、限流、自动提交调度。
- MinIO/S3：原始文件、音频、生成课程对象、派生文件。

语音平面：

```text
Client microphone/speaker
        |
     WebRTC
        |
   LiveKit Server
        |
Voice Agent
  VAD -> ASR -> Intent -> FSM -> Policy -> LLM(optional) -> TTS
        |
Learning API
```

治理平面：

- Source Registry
- License State Machine
- Evidence Store
- Audit Log
- Privacy Router
- Tool Permission Matrix

## 2. 架构原则

1. Stateful over Chatbot：学习和考试都有持久化状态。
2. Deterministic First：时间、客观判分、权限、事务由代码决定。
3. Evidence First：教学结论必须能回到材料、题目或规则。
4. Workflow over Free Agent：LLM 只在明确状态和权限内执行。
5. Adapter First：供应商能力可替换，业务代码不直接绑定 SDK。
6. Event Sourcing for Critical State：答案、评分、学习事件追加写入。
7. Privacy by Route：本地、云端和混合路由显式配置并可审计。

## 3. 服务拆分

### 3.1 Core API

职责：

- Auth 和用户偏好。
- Course、Resource、Question、Paper CRUD。
- ExamSession、LearningSession、VoiceSession 管理。
- 报告查询。

不直接调用供应商 SDK，不实现复杂评分逻辑。

### 3.2 Learning Engine

职责：

- StudentConceptState。
- Mastery update。
- Misconception candidate。
- Review scheduler。
- Daily planner。

MVP 采用可解释规则 + BKT-like 更新 + FSRS 复习调度；深度 KT 模型只作为离线实验。

### 3.3 Exam Engine

职责：

- 服务器权威时间。
- 考试状态机。
- 答案事件存储。
- 自动提交。
- 幂等 submission。
- 评分任务编排。

关键事务必须使用数据库锁或唯一约束，不允许仅靠前端或 Redis。

### 3.4 Grading Service

评分路由：

```text
Question
  ├─ MCQ / multiple select / true-false -> deterministic matcher
  ├─ numeric -> tolerance + unit verifier
  ├─ math expression -> symbolic verifier
  ├─ coding -> sandbox + tests
  └─ subjective -> rubric + evidence + LLM judge
```

主观题输出结构化 JSON，并保留 judge model、prompt hash、confidence 和 evidence IDs。

### 3.5 Content Pipeline

流程：

```text
Upload/Register URL
  -> license/access gate
  -> fetch/scan
  -> type detect
  -> parser adapter
  -> layout normalize
  -> chunk
  -> evidence generation
  -> embed
  -> question extraction
  -> human review queue
```

解析任务必须幂等、可恢复、可重试，并保留每个阶段的错误与指标。

### 3.6 Search Service

职责：

- Query planning。
- Source expansion。
- 多源搜索。
- 去重与排序。
- License/access classification。
- 受控抓取。

禁止默认绕过 robots、登录墙、访问控制和站点频率限制。

### 3.7 Voice Service

职责：

- LiveKit room/token。
- ASR/TTS adapter。
- VAD 和 turn detection。
- Barge-in。
- 语音意图解析。
- 延迟 tracing。

语音服务不直接写最终成绩，只能提交规范化答案事件，由 Exam/Learning API 决定是否接受。

### 3.8 Agent Runtime

使用 LangGraph 保存显式 graph state：

- current node
- allowed tools
- evidence ids
- learner snapshot version
- model route
- retries
- human approval point

每个节点输出必须通过 schema 校验，失败进入可恢复错误状态。

## 4. 核心状态机

### 4.1 Exam State

```text
CREATED
  -> READY
  -> STARTED
  -> IN_PROGRESS
     ├-> PAUSED(optional, policy limited)
     ├-> AUTOSAVED
     ├-> SUBMIT_REQUESTED
     └-> TIMEOUT_SUBMIT_REQUESTED
  -> GRADING
  -> REPORT_READY
  -> CLOSED

Any terminal error -> FAILED / CANCELLED
```

不变量：

1. `start_at`、`end_at` 由服务器生成。
2. 每个答案事件携带 `exam_version` 和 `occurred_at`。
3. `submission_id` 唯一，重复提交返回同一结果。
4. 超时触发只允许一次。
5. 评分报告只能引用已提交答案事件。

### 4.2 Voice Quiz State

```text
IDLE
  -> SESSION_READY
  -> ANNOUNCING_INSTRUCTION
  -> READING_QUESTION
  -> READING_OPTIONS
  -> WAITING_ANSWER
     ├-> CONFIRMING_ANSWER
     ├-> CLARIFYING
     ├-> REPEATING
     └-> COMMAND
  -> ANSWER_COMMITTED
  -> NEXT_QUESTION
  -> REPORT_GENERATING
  -> REVIEWING_MISTAKES
  -> ENDED
```

语音命令：

- repeat_question
- repeat_options
- slow_down
- skip
- choose_option
- change_answer
- pause
- resume
- end

答案修改策略：

1. Practice 模式可在进入下一题前覆盖。
2. Exam 模式按考试策略决定是否允许修改。
3. 每次覆盖都写入新事件，不物理删除旧事件。

### 4.3 Search Workflow

```text
INTENT_PARSE
  -> ENTITY_RESOLUTION
  -> QUERY_PLAN
  -> MULTI_SOURCE_SEARCH
  -> NORMALIZE
  -> DEDUPLICATE
  -> CLASSIFY_SOURCE
  -> LICENSE_CHECK
     ├-> LINK_ONLY
     ├-> PRIVATE_IMPORT
     ├-> OPEN_LICENSE_IMPORT
     └-> BLOCK_IMPORT
  -> FETCH_AND_PARSE
  -> EXTRACT_LEARNING_OBJECTS
  -> HUMAN_REVIEW
  -> INDEX
```

### 4.4 Course Generation Workflow

```text
GOAL_PARSE
  -> COMPETENCY_EXTRACTION
  -> PREREQUISITE_CHECK
  -> CONCEPT_DAG
  -> RESOURCE_SELECTION
  -> OUTLINE_DRAFT
  -> HUMAN_APPROVAL
  -> LESSON_GENERATION
  -> ASSESSMENT_GENERATION
  -> REMEDIATION_PLAN
  -> PUBLISH
```

## 5. 模型与能力路由

### 5.1 Model Gateway 接口

```text
chat(messages, policy) -> stream/text
structured_output(schema, prompt, evidence) -> JSON
embed(text) -> vector
rerank(query, candidates) -> ranked list
transcribe(audio_stream) -> text_stream
synthesize(text_stream, voice_policy) -> audio_stream
```

### 5.2 路由维度

| 任务 | 偏好 | 原因 |
|---|---|---|
| 客观题判分 | 不使用 LLM | 确定性。 |
| 错题解释 | 强推理 + 课程证据 | 需要准确与教学性。 |
| 大纲生成 | 结构化输出 | 需要稳定 JSON。 |
| 口语澄清 | 低延迟小模型 | 交互体验。 |
| 主观题审阅 | 强模型 + rubric | 评分一致性。 |
| Embedding | 多语言稳定模型 | 中英文混合资料。 |

## 6. 部署拓扑

### 6.1 个人本地版

```text
Docker Compose
  web
  api
  worker
  postgres
  redis
  minio
  livekit
  voice-agent
  optional: local ASR/TTS/LLM containers
```

适用：单用户或家庭使用，文档和音频可留在本机。

### 6.2 混合版

- 本地：PostgreSQL、MinIO、ASR、部分个人上下文。
- 云端：强 LLM、搜索、可选 TTS/realtime。
- 策略：默认本地；需要强推理或公开网络检索时按任务升级，并在 UI 显示隐私模式。

### 6.3 多用户版

- Kubernetes 或托管容器。
- PostgreSQL 主备。
- Redis/queue 集群。
- 对象存储生命周期策略。
- LiveKit 自托管或云。
- 全链路 OpenTelemetry。

## 7. 可观测性

必埋指标：

- API latency/status。
- Exam autosave latency。
- Submission conflict count。
- Grading duration/agreement。
- Voice end-to-end latency by stage。
- ASR word error rate on eval set。
- TTS first audio latency。
- Search result authority distribution。
- License classification confidence。
- Parser success/page locator retention。
- Model cost/token/latency。

Trace 粒度：

1. User action。
2. Workflow node。
3. Tool call。
4. Model call。
5. DB transaction。
6. Voice stage。

## 8. 故障设计

| 故障 | 系统行为 |
|---|---|
| 客户端刷新 | 从服务端恢复 exam/voice state。 |
| 断网 | 本地暂存答案，恢复后按 event sequence 提交。 |
| 重复提交 | 幂等返回同一 submission。 |
| Worker 崩溃 | 任务 lease 到期后重试，评分不重复写最终结果。 |
| 语音断线 | VoiceSession 状态保留，重连后继续当前题。 |
| LLM 超时 | 降级为规则解释或标记 pending，不虚构结果。 |
| 文档解析失败 | 保留原始文件和失败阶段，允许换 parser。 |
| 搜索来源不可访问 | 只记录入口和失败原因，不缓存受限正文。 |

