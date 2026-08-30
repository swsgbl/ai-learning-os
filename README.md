# AI Learning OS

AI Learning OS 的生产工程骨架。当前版本优先落地三条主线：

1. 服务端权威限时考试与幂等提交。
2. 语音陪练交互，底层后续切换 LiveKit ASR/TTS adapter。
3. 公开课与题库来源治理，先保留来源目录，不把静态题库打进前端。

## 目录

```text
apps/web        Next.js Web App，保留 Grok 原型的界面风格
services/api    FastAPI Exam API 与领域状态机
infra           Docker Compose 与部署环境
docs            工程决策、开发路线和迁移边界
docs/delivery   2026 定版调研、PRD、架构、开发计划和来源登记
```

## 本地启动

```powershell
cd "D:\AI Learning OS\ai-learning-os"
copy .env.example .env
npm install

docker compose -f infra/docker-compose.yml up -d postgres

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r services\api\requirements.txt -r services\api\requirements-dev.txt
uvicorn app.main:app --app-dir services/api --reload

npm run dev
```

API 启动时读取 `.env` 的 `DATABASE_URL`：已配置则自动执行 Alembic migration 并使用 PostgreSQL
仓储；未配置时回退内存仓储（仅用于契约调试）。数据随 Docker 卷持久化。

默认地址：

- Web: http://localhost:3000
- API: http://127.0.0.1:8000/docs

## 当前架构边界

- 考试开始和结束时间由 API 服务器写入，客户端只根据 `server_end_at` 显示倒计时。
- 正确答案、解析和评分规则只存在于 API 侧，交卷后才返回。
- 答案以事件序号写入仓储接口，重复事件幂等处理。
- 当前仓储已提供 PostgreSQL（SQLAlchemy async + Alembic）实现；无 `DATABASE_URL` 时回退内存实现。
- 语音先保留浏览器本地听写/朗读能力，LiveKit 与 FunASR/CosyVoice adapter 是后续里程碑。
