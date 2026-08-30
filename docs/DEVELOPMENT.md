# 开发指南

## 里程碑状态

### M0 当前完成

- npm workspace monorepo。
- Next.js Web App 与 FastAPI API 分离。
- 服务端权威考试 API 契约。
- 答案事件序列与幂等提交的内存实现。
- Docker Compose 中的 PostgreSQL、Redis、MinIO。

### 尚未完成

- PostgreSQL persistence 与 Alembic migration。
- Redis timeout scheduler。
- LiveKit room/token 与 ASR/TTS adapter。
- Source Registry、License State Machine、Evidence Store。
- LangGraph Agent Runtime 与 Model Gateway。

## API 契约

```text
GET    /api/v1/papers
POST   /api/v1/papers/{paper_id}/exams
GET    /api/v1/exams/{exam_id}
PUT    /api/v1/exams/{exam_id}/answers
POST   /api/v1/exams/{exam_id}/submit
GET    /api/v1/exams/{exam_id}/submission
```

规则：

1. `server_started_at` 和 `server_end_at` 只由 API 写入。
2. `questions` 不包含 `answer`、`explanation`、`angles`。
3. `sequence` 必须从 1 开始并递增。
4. 重复提交返回同一 submission。
5. 交卷后答案事件被拒绝。

## 本地验证

```powershell
npm run typecheck
npm run lint
npm run build
pytest
ruff check services/api/app
```
