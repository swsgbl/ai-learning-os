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

## 生产本地版（一条命令）

```bash
docker compose -f infra/docker-compose.yml --profile local up -d --build
```

等全部服务 healthy 后即可使用：API http://127.0.0.1:8000（/health、/docs）、Web http://127.0.0.1:3000。
基础服务（postgres/redis/minio/api/web）不挂 profile 恒启动；livekit（语音基础设施）
挂在 local/hybrid/cloud 三个命名 profile 下。

登录/注册由 API 提供（`POST /api/v1/auth/register` / `login`，compose 已注入 dev AUTH_SECRET，
业务端点需 Bearer token；生产部署覆盖 `AIOS_AUTH_SECRET`）。

验证真实可用（全服务 healthy + 关键端点 + 认证 + 上传对象重启 API 后仍可读）：

```bash
bash infra/smoke_docker.sh
```

语音隐私路由按 profile 切换（`AIOS_VOICE_MODE` 插值注入 VOICE_MODE）：

```bash
# hybrid：ASR 本地（语音原文不出本机）+ TTS 云端（仅文本出站）
AIOS_VOICE_MODE=hybrid docker compose -f infra/docker-compose.yml --profile hybrid up -d
# cloud：语音云端处理（需 .env 提供云端凭据）
AIOS_VOICE_MODE=cloud docker compose -f infra/docker-compose.yml --profile cloud up -d
```

注意：hybrid/cloud 必须显式设置 `AIOS_VOICE_MODE`，未设置时 VOICE_MODE 回退 local
（服务照常启动但语音走本地，不会虚报云端能力；provider 视图会透出实际路由）。

## 版本与回滚（M7-06）

**版本真相源**：仓库根 `VERSION`（`web package.json` 与 `docs/CHANGELOG.md` 由测试守卫同步）。

```bash
cd services/api
python -m app.ops.cli version            # 版本 + git commit + alembic current/head
curl http://127.0.0.1:8000/api/v1/version
```

**数据库回滚**（默认 dry-run 不碰库，--yes 才执行）：

```bash
python -m app.ops.cli db-rollback --steps 1          # dry-run：打印计划
python -m app.ops.cli backup --out backup-dir/       # 回滚前先备份
python -m app.ops.cli db-rollback --steps 1 --yes    # 真正执行 alembic downgrade -1
```

**应用回滚**：compose 以 `AIOS_IMAGE_TAG` 为镜像锚点，发布时固化 tag，回滚即旧 tag 重启：

```bash
# 发布：build 并固化版本 tag
docker compose -f infra/docker-compose.yml build
docker tag aios/api:local aios/api:v0.1.0
docker tag aios/web:local aios/web:v0.1.0
# 回滚：旧 tag + --no-build 重启（数据库先按上面 db-rollback/backup 流程处理）
AIOS_IMAGE_TAG=v0.1.0 docker compose -f infra/docker-compose.yml up -d --no-build
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
