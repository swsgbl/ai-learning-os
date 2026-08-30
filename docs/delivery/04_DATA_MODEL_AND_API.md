# 数据模型与 API 契约 v2

## 1. 设计原则

1. 课程、题目、学生状态共享 `concept_id`。
2. 答案、评分、学习事件 append-only。
3. 所有外部内容和生成结论可追溯到 Evidence。
4. License 状态是内容入库的前置字段。
5. API 返回结构统一，错误可机器判断。

## 2. 核心实体

### 2.1 User

```text
id
display_name
locale
timezone
privacy_mode: local | cloud | hybrid
learning_goal
daily_minutes
created_at
updated_at
```

### 2.2 Course

```text
id
title
provider
source_url
language
description
difficulty
concept_ids[]
module_ids[]
license_state
status: draft | ready | archived
created_at
updated_at
```

### 2.3 Concept

```text
id
canonical_name
aliases[]
subject
description
difficulty
prerequisite_ids[]
parent_id
evidence_ids[]
```

MVP 用 PostgreSQL 关系表和 `concept_edges` 表达图，不先引入图数据库。

### 2.4 Resource

```text
id
source_id
url
media_type: page | pdf | docx | video | audio | slide | dataset
title
language
access_state: public | restricted | unknown
license_state
content_right: link | private_copy | derive | redistribute | train
fetched_at
hash
storage_key
parse_status
```

### 2.5 Source

```text
id
name
source_type: university | library | oer | government | github | community | vendor
authority_score
homepage
terms_url
robots_policy_snapshot
rate_limit
trust_tier: S | A | B | C
```

### 2.6 Question

```text
id
course_id
stem
stem_rich_text
options[]
answer_key
answer_explanation
question_type: mcq | multiple_select | true_false | short_answer | numeric | math | coding | essay
difficulty
discrimination
concept_ids[]
skill_ids[]
estimated_seconds
source_id
evidence_ids[]
license_state
version
```

### 2.7 Paper

```text
id
title
course_id
question_ids[]
total_score
duration_seconds
policy: practice | exam | adaptive
shuffle_policy
generation_method: manual | imported | ai_generated | hybrid
review_status
```

### 2.8 ExamSession

```text
id
user_id
paper_id
status
server_started_at
server_end_at
submitted_at
submission_id
timezone
client_fingerprint
revision
lock_version
```

### 2.9 AnswerEvent

```text
id
exam_session_id
question_id
event_type: draft | answer | change | flag | submit
payload
client_occurred_at
server_received_at
sequence
voice_transcript_id
```

唯一约束：`(exam_session_id, sequence)`。

### 2.10 Submission

```text
id
exam_session_id
answers[]
submitted_at
submit_reason: user | timeout | admin
grading_status
total_score
report_id
idempotency_key
```

唯一约束：`exam_session_id` 或 `(exam_session_id, idempotency_key)`。

### 2.11 GradingResult

```text
id
submission_id
question_id
is_correct
score
max_score
method: deterministic | numeric_verifier | symbolic | sandbox | llm_rubric
criteria[]
confidence
judge_model
judge_prompt_hash
evidence_ids[]
needs_second_judge
created_at
```

### 2.12 StudentConceptState

```text
user_id
concept_id
mastery
confidence
attempt_count
correct_count
avg_response_time
last_seen_at
forgetting_risk
next_review_at
misconception_candidates[]
version
```

### 2.13 Mistake

```text
id
user_id
question_id
concept_id
error_type
diagnosis
confidence
remediation_task_ids[]
evidence_ids[]
created_at
```

### 2.14 LearningEvent

```text
id
user_id
session_id
event_type
concept_ids[]
question_id
payload
latency_ms
created_at
```

### 2.15 Evidence

```json
{
  "id": "ev_...",
  "source_id": "src_...",
  "resource_id": "res_...",
  "url": "https://example.edu/course/final.pdf",
  "locator": {
    "page": 17,
    "section": "Self-Attention",
    "bbox": [0.1, 0.2, 0.8, 0.4]
  },
  "snippet_hash": "sha256:...",
  "retrieved_at": "2026-08-30T00:00:00Z",
  "license_state": "OPEN_LICENSE"
}
```

### 2.16 VoiceSession

```text
id
user_id
learning_session_id
exam_session_id
mode: practice | exam | tutor
current_question_id
state
asr_provider
tts_provider
privacy_route
latency_trace_id
resumable_until
```

## 3. License State Machine

```text
UNKNOWN
  -> verify_source
PUBLIC_ACCESS
  -> inspect_terms
     -> OPEN_LICENSE
     -> ACCESS_CONTROLLED
     -> ALL_RIGHTS_RESERVED
     -> RESTRICTED_NON_COMMERCIAL
     -> PROHIBITED
```

| 状态 | 可链接 | 可私有解析 | 可派生 | 可再分发 | 可训练 |
|---|---:|---:|---:|---:|---:|
| UNKNOWN | yes | no | no | no | no |
| OPEN_LICENSE | yes | yes | per terms | per terms | per terms |
| ACCESS_CONTROLLED | yes | only authorized user | no default | no | no |
| ALL_RIGHTS_RESERVED | yes | no | no | no | no |
| RESTRICTED_NON_COMMERCIAL | yes | per terms | per terms | usually no | no default |
| PROHIBITED | entry only | no | no | no | no |

## 4. REST API

### 4.1 统一响应

```json
{
  "data": {},
  "error": null,
  "meta": {
    "request_id": "...",
    "trace_id": "..."
  }
}
```

错误：

```json
{
  "data": null,
  "error": {
    "code": "EXAM_ALREADY_SUBMITTED",
    "message": "Exam has already been submitted.",
    "retryable": false,
    "details": {}
  }
}
```

### 4.2 学习计划

```http
GET /api/v1/learning/today
POST /api/v1/learning/sessions
POST /api/v1/learning/sessions/{id}/events
GET /api/v1/learning/reports/daily?date=2026-08-30
```

### 4.3 课程与资源

```http
GET /api/v1/courses
POST /api/v1/courses
GET /api/v1/courses/{id}
POST /api/v1/courses/{id}/generate-outline
POST /api/v1/courses/{id}/publish
POST /api/v1/resources/import-url
POST /api/v1/resources/upload
GET /api/v1/resources/{id}
POST /api/v1/resources/{id}/reparse
```

### 4.4 题库与试卷

```http
GET /api/v1/questions?concept_id=&difficulty=&type=
POST /api/v1/questions
POST /api/v1/questions/generate
POST /api/v1/papers
POST /api/v1/papers/import
GET /api/v1/papers/{id}
```

生成接口只返回 `draft` 状态，必须审核后可用于正式考试。

### 4.5 考试

```http
POST /api/v1/exams
POST /api/v1/exams/{id}/start
GET /api/v1/exams/{id}/state
PUT /api/v1/exams/{id}/answers
POST /api/v1/exams/{id}/submit
POST /api/v1/exams/{id}/timeout-submit
GET /api/v1/exams/{id}/report
```

`GET /state` 返回：

```json
{
  "status": "IN_PROGRESS",
  "server_now": "2026-08-30T10:00:00Z",
  "server_remaining_seconds": 1200,
  "current_answers": {},
  "revision": 32
}
```

### 4.6 语音

```http
POST /api/v1/voice/sessions
GET /api/v1/voice/sessions/{id}
POST /api/v1/voice/sessions/{id}/commands
POST /api/v1/voice/sessions/{id}/answers
POST /api/v1/voice/sessions/{id}/end
GET /api/v1/voice/sessions/{id}/report
```

语音答案提交：

```json
{
  "question_id": "q_001",
  "transcript": "我选第二个",
  "normalized_answer": "B",
  "confidence": 0.94,
  "intent": "choose_option",
  "event_id": "client-uuid"
}
```

服务端根据考试策略决定接受、澄清或拒绝，不信任客户端分数。

### 4.7 搜索

```http
POST /api/v1/search/plan
POST /api/v1/search/execute
GET /api/v1/search/jobs/{id}
POST /api/v1/search/jobs/{id}/import
GET /api/v1/search/results?job_id=
```

搜索结果：

```json
{
  "items": [
    {
      "title": "Final Examination",
      "url": "https://example.edu/final.pdf",
      "source_tier": "S",
      "license_state": "ACCESS_CONTROLLED",
      "import_action": "LINK_ONLY",
      "reason": "Requires institutional authentication."
    }
  ]
}
```

## 5. WebSocket / SSE 事件

### Exam channel

```text
exam.started
exam.answer_saved
exam.answer_rejected
exam.time_warning
exam.timeout
exam.submitted
exam.grading_progress
exam.report_ready
```

### Voice channel

```text
voice.session_ready
voice.question_reading
voice.listening
voice.transcript_partial
voice.intent_detected
voice.answer_accepted
voice.answer_needs_clarification
voice.report_ready
```

### Pipeline channel

```text
pipeline.queued
pipeline.running
pipeline.parse_completed
pipeline.failed
pipeline.needs_review
```

## 6. 关键数据库约束

1. `exam_sessions.server_end_at` 不可由客户端更新。
2. `answer_events.sequence` 单调且唯一。
3. `submissions.exam_session_id` 唯一。
4. `grading_results(submission_id, question_id)` 唯一。
5. `student_concept_states(user_id, concept_id)` 唯一。
6. `resources(url_hash)` 可唯一或按来源唯一。
7. 所有软删除必须保留 source 和 license 快照。

## 7. API 错误码

| Code | 场景 | 客户端行为 |
|---|---|---|
| EXAM_NOT_STARTED | 未开始就保存答案 | 引导开始考试。 |
| EXAM_EXPIRED | 已超过服务器结束时间 | 触发/等待 timeout submit。 |
| EXAM_ALREADY_SUBMITTED | 重复提交 | 展示既有报告。 |
| ANSWER_SEQUENCE_CONFLICT | 事件乱序 | 按 server revision 同步。 |
| LICENSE_BLOCKED | 内容不允许导入 | 只显示链接和原因。 |
| ACCESS_CONTROLLED | 需要授权 | 引导用户自行访问。 |
| VOICE_ANSWER_AMBIGUOUS | 答案不确定 | 语音追问。 |
| MODEL_SCHEMA_INVALID | 模型输出不合规 | 重试或降级。 |
| SSRF_BLOCKED | 内网/危险 URL | 拒绝抓取。 |

