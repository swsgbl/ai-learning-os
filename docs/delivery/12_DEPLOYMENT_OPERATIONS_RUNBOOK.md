# 部署与运维手册 v2.2

适用范围：个人本地版、混合版和多用户版。真实环境变量只能保存在部署 secret 或本机 `.env`，不得写入代码、文档、前端或日志。

## 1. 运行模式

| 模式 | 默认路径 | 适用场景 | 关键风险 |
|---|---|---|---|
| Local | Web/API/DB/对象存储/LiveKit/ASR/TTS/LLM 全本地。 | 隐私优先、单用户。 | 强模型质量和推理资源不足。 |
| Hybrid | 私有资料与 ASR 本地，强推理/搜索/TTS 按任务云端。 | 日常推荐模式。 | 必须明示哪些数据离开本机。 |
| Cloud | 多用户云部署，敏感资料加密。 | 家庭/班级/团队。 | 权限、隔离、成本和合规复杂度最高。 |

## 2. 基础命令

```bash
# 开发
docker compose up -d postgres redis minio
npm run dev --workspace apps/web
uvicorn apps.api.main:app --reload

# 本地生产
docker compose --profile local up -d

# 迁移
alembic upgrade head
alembic downgrade -1
alembic check

# 验证
npm run typecheck
npm run lint
npm test
npm run build
```

## 3. 最小环境变量

```env
APP_ENV=local
DATABASE_URL=postgresql://user:password@localhost/ai_learning_os
REDIS_URL=redis://localhost:6379/0
S3_ENDPOINT=http://localhost:9000
S3_BUCKET=ai-learning-os
S3_ACCESS_KEY=change-me
S3_SECRET_KEY=change-me

MODEL_ROUTE=hybrid
VOICE_MODE=hybrid
SEARCH_MODE=hybrid

LIVEKIT_URL=
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=

ASR_PROVIDER=funasr
TTS_PROVIDER=cosyvoice
LLM_PROVIDER=
EMBEDDING_PROVIDER=
SEARCH_PROVIDER=

PRIVACY_STORE_AUDIO=false
PRIVACY_SEND_CONTEXT_TO_CLOUD=true
```

变量含义：

1. `MODEL_ROUTE` 控制课程生成、解释、主观题审阅等模型任务。
2. `VOICE_MODE` 控制语音链路，不能影响考试权威时间。
3. `SEARCH_MODE` 控制搜索 provider 与抓取 executor。
4. `PRIVACY_STORE_AUDIO=false` 时只保留转写、意图和必要事件，不保留原始音频。
5. `PRIVACY_SEND_CONTEXT_TO_CLOUD=false` 时，云端模型只能收到用户明确允许的脱敏上下文。

## 4. 语音部署

### 本地优先

```text
Browser WebRTC
  -> self-hosted LiveKit
  -> voice-agent
  -> FunASR / Qwen3-ASR
  -> local or cloud LLM by policy
  -> CosyVoice
```

建议：

1. MVP 先用 FunASR 做 streaming ASR，Qwen3-ASR 作为高准确率候选单独 benchmark。
2. Qwen3-ASR streaming 依赖 vLLM backend，部署和显存成本更高，不应默认进入个人轻量版。
3. TTS 首选 CosyVoice，但声音克隆必须显式授权，禁止默认克隆教师或用户声音。
4. 语音会话只调用 Learning API，不直接写最终成绩。

### 云端或混合

1. LiveKit Cloud/自托管 LiveKit 均通过同一 adapter 接入。
2. ASR、LLM、TTS 分别配置 provider，不绑定单一厂商 SDK。
3. 每次云端调用记录 destination、data classification、model、latency 和 cost。

## 5. 内容与检索治理

上线前必须启用：

1. Source Registry 和 License State Machine。
2. robots / rate limit / access control 检查。
3. SSRF 防护和 URL reputation gate。
4. 人工审核队列。
5. Evidence 和派生对象关联。

禁止：

1. 登录绕过、伪装身份、绕过付费墙或访问控制。
2. 将 UNKNOWN license 内容放入公共复用池。
3. 将受限试卷正文缓存或再分发。
4. 用爬虫压力影响目标站点正常服务。

## 6. 备份与恢复

每日备份：

```text
PostgreSQL logical dump
MinIO/S3 versioned objects
Alembic schema history
Source Registry export
configuration manifest
```

恢复演练步骤：

1. 在隔离环境恢复数据库 dump。
2. 恢复对象存储版本。
3. 运行 `alembic upgrade head`。
4. 抽查用户、课程、试卷、考试事件、报告、Evidence、Source。
5. 执行 smoke：创建考试、提交、评分、生成报告、语音读题。
6. 记录 RPO/RTO 和失败原因。

## 7. 观测与告警

必看指标：

| 指标 | 告警阈值建议 |
|---|---|
| API 5xx rate | > 1% 或 5 分钟持续异常。 |
| Exam submission conflict | 突增或非测试环境出现。 |
| autosave failure | > 0.5%。 |
| grading queue latency | p95 超过业务阈值。 |
| voice first audio latency | 超过评测目标。 |
| ASR eval WER | 相比基线恶化。 |
| parser failure rate | > 5%。 |
| license gate bypass | 任何出现即阻断发布。 |
| disk/object storage usage | > 80%。 |

日志必须带 request id、session id、workflow node、task id 和 model route。日志不得包含 API key、完整语音原文、试卷未公开答案和用户明文敏感信息。

## 8. 常见故障处理

| 症状 | 优先检查 | 处理 |
|---|---|---|
| 考试时间异常 | 服务器时钟、DB事务、session end_at | 以服务端为准；拒绝客户端提交并恢复状态。 |
| 重复提交冲突 | submission 唯一约束、事件 sequence | 返回既有报告，不生成第二份成绩。 |
| 语音中断 | LiveKit、ASR stream、FSM state | 重连后从服务端 state 恢复，不重做全卷。 |
| 解析卡住 | 队列、parser 进程、文件类型 | 换 parser 重跑；保留失败指标。 |
| 搜索无结果 | query plan、source registry、robots | 展示原因，不自动绕过限制。 |
| 模型输出不合法 | schema 校验、重试、结构化输出 | 进入可恢复错误，不写半成品业务状态。 |
