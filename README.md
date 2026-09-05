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

登录/注册由 API 提供（`POST /api/v1/auth/register` / `login`，compose 已注入 dev AUTH_SECRET）。
浏览器登录态走 HttpOnly cookie；CLI/API 客户端仍使用 Bearer token。生产部署覆盖
`AIOS_AUTH_SECRET`，HTTPS 反代同时设置 `AIOS_AUTH_COOKIE_SECURE=true`。

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

**生产数据盘点与验收清理**（M10-03；inventory 只读；清理默认 dry-run，必须 `--yes`）：

```bash
python -m app.ops.cli data-inventory --db-url ...    # 只读：用户/试卷/资源/语音/草稿/审计风险盘点
python -m app.ops.cli acceptance-clean --db-url ...                # dry-run：按用户名前缀列出待删计划
python -m app.ops.cli acceptance-clean --db-url ... --yes          # 执行（learner 精确行 ID 单事务删除）
```

清理只按显式字面量前缀（默认 `smoke_` / `voice_smoke_`，拒绝 `*`/`%`）匹配 learner 账号；
admin 与普通用户、系统 seed 卷、无归属历史数据、append-only 审计不进入删除集；
对象存储只删「仅被待删行引用」的 key（共享 key 保留）。

**历史无归属试卷治理**（M10-04 第一切片；报告只读；迁移默认 dry-run，必须 `--yes`）：

```bash
python -m app.ops.cli legacy-paper-report --db-url ...                # 只读摘要（不含生产 ID）
python -m app.ops.cli legacy-paper-report --db-url ... --json         # 完整明细 JSON（stdout）
python -m app.ops.cli legacy-paper-report --db-url ... --output artifacts/legacy-report.json
python -m app.ops.cli legacy-paper-migrate keep-public --db-url ... --paper-id pap_xxx            # dry-run 计划
python -m app.ops.cli legacy-paper-migrate assign-owner --db-url ... --to <用户名或ID> --paper-id pap_xxx --yes
python -m app.ops.cli legacy-paper-migrate export-delete --db-url ... --paper-id pap_xxx --export artifacts/legacy-export.jsonl --yes
```

报告范围是 `owner_id IS NULL` 且非 seed 的历史公共卷；每卷给出题量/总分/是否被历史
考试引用/引用次数/最近引用时间与建议路径（keep_public/assign_owner/export_review）。
papers 表没有时间戳列，创建/更新时间如实为 null，时间证据以考试引用时间派生。
迁移只接受精确 paper ID（`--paper-id` 可重复或 `--ids-file`，拒绝 `*`/`%`），未知 ID
整体拒绝；assign-owner 只迁报告确认的行，事务内先锁定目标用户再复核行数与行状态，
不符即回滚；export-delete 排他创建导出档案（目标已存在，含 dangling symlink，一律
拒绝覆盖、不动 DB），回读做完整归档校验（与导出前内存快照逐字段确定性全等、paper
ID 精确集合、无缺行/重复/错行，IO 失败稳定退出码 1 不删库），删除事务内再次证明
即将删除的题目与已验证档案完全一致（同数量换内容也拒绝），被历史考试引用的卷一律
拒绝删除（人工处理，不级联删考试）；keep-public 在审计事务内复核行事实后才写决策，
不改试卷。报告/导出文件含生产 ID，只能写入 gitignore 的 `artifacts/`、`temp/`
目录（其他路径退出码 2）。

**应用回滚**：compose 以 `AIOS_IMAGE_TAG` 为镜像锚点，发布时固化 tag，回滚即旧 tag 重启：

```bash
# 发布：build 并固化版本 tag
docker compose -f infra/docker-compose.yml build
docker tag aios/api:local aios/api:v0.1.0
docker tag aios/web:local aios/web:v0.1.0
# 回滚：旧 tag + --no-build 重启（数据库先按上面 db-rollback/backup 流程处理）
AIOS_IMAGE_TAG=v0.1.0 docker compose -f infra/docker-compose.yml up -d --no-build
```

## 本地 Release Candidate 包（M10-17）

在干净 worktree 上打一个**本地** RC 包（镜像归档 + manifest + 校验和），一条命令：

```bash
# 前置：VERSION 已是目标版本、git worktree 干净（脚本会逐一 fail-closed 校验）
bash infra/build_release_candidate.sh --tag v0.1.0 --output-dir artifacts/rc-v0.1.0
```

脚本自动完成：tag/VERSION 逐字一致校验 → 记录干净 worktree 的完整 commit SHA →
同源构建 `aios/api:<tag>`、`aios/web:<tag>` → `AIOS_IMAGE_TAG=<tag>` + `--no-build`
起 compose local profile 并跑 `infra/smoke_docker.sh`（结束/失败都
`down --remove-orphans`，绝不带 `-v` 删卷）→ `docker save` 两个独立归档 → 原子写
`release-manifest.json` + `SHA256SUMS` → 最后独立 verify。

对既有包做独立校验（不加载 Docker 镜像，只重算文件哈希）：

```bash
cd services/api
python -m app.ops.cli release-candidate verify --package-dir ../artifacts/rc-v0.1.0 \
    --version-file ../VERSION      # 退出码 0=通过 / 1=校验失败 / 2=输入问题
```

GitHub Actions 侧是手动 workflow：Actions →「Release Candidate (manual only)」→
Run workflow（tag 留空则取 VERSION）——仅 `workflow_dispatch` 触发（push/PR/schedule
永不触发），产物作为 workflow artifact 上传（保留 14 天）。

**边界**：产出是**本地** Release Candidate，不是 production readiness 声明，不授权
部署；不打 git 标签、不发 GitHub Release、不推镜像到任何 registry、不碰生产
DB/服务/主机。详见 docs/DEVELOPMENT.md「本地 Release Candidate 包（M10-17）」。

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

## 可选：LLM 主观题判分

`RUBRIC_JUDGE=llm` + `LLM_ENDPOINT/LLM_API_KEY/LLM_MODEL`（OpenAI 兼容，key 放 .env）
启用模型判分；模型不可用时 essay 自动进复核（fail-closed），默认 `keyword` 确定性判分。
详见 docs/DEVELOPMENT.md「LLM 接入」。

## 当前架构边界

- 考试开始和结束时间由 API 服务器写入，客户端只根据 `server_end_at` 显示倒计时。
- 正确答案、解析和评分规则只存在于 API 侧，交卷后才返回。
- 答案以事件序号写入仓储接口，重复事件幂等处理。
- 当前仓储已提供 PostgreSQL（SQLAlchemy async + Alembic）实现；无 `DATABASE_URL` 时回退内存实现。
- 语音先保留浏览器本地听写/朗读能力，LiveKit 与 FunASR/CosyVoice adapter 是后续里程碑。
