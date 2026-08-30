# AI 开发驱动提示词

## M0-06 PostgreSQL Repository

你是 AI Learning OS 的后端工程师。请在不动 API route 的前提下实现 PostgreSQL Repository。要求：SQLAlchemy async 模型、Alembic migration、唯一约束处理 exam_id 和 answer sequence、pytest 使用 testcontainer 或 SQLite memory 替身。禁止把正确答案下发到考试 active 状态响应。完成后运行 pytest 和 ruff。

## M2-04 服务端权威计时加固

你是考试系统工程师。请把当前内存 exam timeout 扩展为 Redis 延迟队列加数据库事务兜底。验收：重复触发只生成一个 submission；客户端改时间不能延长考试；API 重启后可恢复未完成考试；补齐并发测试。

## M4-01 LiveKit Voice Adapter

你是实时语音工程师。请为 `apps/web` 接入 LiveKit room/token，并在 API 侧抽象 `SpeechProvider`。默认 local profile 只允许浏览器本地语音；hybrid profile 才能调用在线 ASR/TTS。必须记录 latency、interrupted、reconnect 事件，禁止把音频明文长期保存。

## M1-01 Source Registry

你是内容来源治理工程师。请设计 Source Registry 表和 API。每个来源必须有 source tier、license state、robots 策略、rate limit、last_verified_at。UNKNOWN 不允许进入公共复用池；ACCESS_CONTROLLED 不保存正文。用公开高校课程 URL 编写 seed 和测试。
