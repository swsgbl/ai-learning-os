# M14-124：预审批生产构建证据（pre-approval build，未切换、未审批）

## 1. 结论与边界

M14-124 是一次**审批前**（pre-approval）生产镜像构建的证据回填切片，
不是生产切换，不是发布审批，不是功能开发。

- 构建产物：`aios/api:m14-124-production` 与 `aios/web:m14-124-production`
  两枚本地镜像（digest 双锚见 §3）。
- 构建全程零生产触碰：未启停/重建任何运行容器，未接触生产 DB、MinIO、
  FunASR/CosyVoice 语音引擎与任何 secret/env 值。
- **生产切换未发生**：生产栈仍运行 `m14-117-production`（见 §5），
  本切片不包含、不预告、不授权任何切换动作。
- `release_ready=false` / `production_ready=false` 契约不变。
  hash-bound human-only release-approval 仍是唯一 required 发布门，
  从未发生；本切片不创建、不模拟任何审批证据。

## 2. 基点事实（git 可复核）

| 事实 | 值 |
|------|-----|
| canonical main HEAD | `684865d1212ca565182fd04edcb3f6a21c9ef88b`（PR #210 merge = M14-123 证据合入；本回填切片 fetch 后 origin/main 全 SHA 复核一致） |
| 干净构建 worktree | `D:\AI Learning OS\ai-learning-os-worktrees\m14-124-production-build`（detached HEAD @ `684865d`） |
| 运行中生产的发布源 | `2619ea77f3db291901e48eacdeea064b6f9b6fdb`（M14-117 README §1 登记，`m14-117-production` 栈） |

`2619ea7..684865d` 共 17 个文件、全部不在两镜像构建输入面内：
11 个 docs、3 个 `services/api/tests/` 测试文件、2 个主机侧
`tools/ops/` 运维工具（`monitoring_alert_dispatch.py`、
`post_cutover_watch.py`）与 `tools/ops/README.md`。镜像输入面
（`services/api/app`、`services/api/alembic*`、`requirements.txt`、
`VERSION`、`apps/web`、`package.json`/`package-lock.json`、
两个 Dockerfile、`.dockerignore`、compose）**零变更**——这是
§4 运行时等价结论的 git 侧依据（测试与主机侧工具不入镜像：
api Dockerfile 仅 COPY `VERSION`/`services/api/app`/alembic，
web Dockerfile 仅消费 `apps/web` 与两份 package 清单）。

## 3. 构建事实（digest 双锚，逐字登记）

| 对象 | 值 |
|------|-----|
| API 镜像 | `aios/api:m14-124-production` = `sha256:c99e28c905208bffbc1576f0c5c9e042af18fd356881cee967078ee38323781f` |
| Web 镜像 | `aios/web:m14-124-production` = `sha256:d596f0c726ab690359b196a3c3911f9842b94218b4361ee452413d420a38194b` |
| Web build arg | `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000`（loopback 部署口径，M14-70/M14-117 生产镜像同款默认值先例） |
| 构建方式 | 干净 `684865d` worktree 内 `docker build`（先例 = M14-70 build log 与 `infra/build_release_candidate.sh` 构建段；`--tag vX.Y.Z` 形态的 RC 构建器不适用 `m14-XXX-production` tag，未使用） |

构建边界（逐项成立）：未 push 任何镜像仓库、未打 git tag、未发
GitHub Release；未触碰 compose 项目 `aios-m14-03-production-rehearsal`
的任何容器/卷/网络；MinIO 本地镜像（`aios/minio:RELEASE.2025-10-15T17-29-55Z`）
不在本次构建面、未重建。

## 4. supervisor 运行时等价复核（独立对比，本切片如实转述）

supervisor 对 `m14-124-production` 与运行中的 `m14-117-production`
两代镜像做了独立对比，结论（每服务分别成立）：

- **运行时 Config、History、RootFS layers 逐项一致**；
- 唯一差异为 **BuildKit identity/build ref 元数据与 image descriptor**。

该结论支持两件事同时成立：**运行时等价**（`2619ea7..684865d`
运行时零变更，见 §2，切换后行为面预期无差异）与**全新构建的
provenance 锚**（镜像确实构建自 `684865d` 树，非对旧 tag 的
re-tag——re-tag 路径会使两代镜像共享同一 image Id，实测 Id 不同）。

本回填切片的只读旁证（`docker image inspect`，非权威判定）：
web 镜像 `Created=2026-09-19T02:57:53Z`、api 镜像
`Created=2026-09-23T23:11:30Z`——BuildKit 全层缓存命中保留原始
层链时间戳（与 M14-70 README 记录的 web 全缓存命中同形态），
与「RootFS 层一致、仅 identity 元数据不同」的对比结论互洽。

## 5. 本回填切片只读复核（2026-09-24，零触碰）

- `docker image inspect`：两枚新镜像本地 Id 与 §3 登记 digest
  **逐一一致**。
- `docker ps`（compose 项目 `aios-m14-03-production-rehearsal`）：
  **7 容器全部 healthy，API/Web 仍运行 `aios/api:m14-117-production`
  （`a10b4b626b8a`）与 `aios/web:m14-117-production`
  （`95aff24f17fd`）**，与 M14-117 README §3 digest 双锚一致；
  postgres/redis/livekit/minio/searxng 五基础设施容器 healthy 未重建。
- 以上均为只读观察（与 `production_monitor` 同类只读面），未执行
  任何容器/DB/MinIO/语音/secrets 操作。

## 6. 切换窗口检查单（未执行；仅记录未来获批窗口的精确步骤）

以下检查单为**待审批材料**，本切片未执行其中任何步骤。真实切换
须先获得绑定 `684865d` 全 SHA 与 §3 两 digest 的 hash-bound
human-only release-approval（唯一 required 发布门）。

1. **审批**：supervisor 书面审批，绑定构建基点 `684865d1212ca565182fd04edcb3f6a21c9ef88b`
   与 API/Web 两镜像 digest。
2. **baseline 捕获**：git HEAD/status、`docker ps -a`、`docker images`
   落证据目录。
3. **切换前备份**：`aios-backup-v1`（`python -m app.ops.cli backup
   --db-url <生产URL> --out <证据目录>/pre-cutover-backup`，S3 凭据
   经环境变量注入、值不回显）。
4. **只读生产 preflight**：`python -m app.ops.cli production-preflight
   --db-url <生产URL> --phase post-migration --anchor-file <运维保管
   路径>/audit-anchor.jsonl --json --output <证据目录>`——放行门
   5/5 pass、exit 0（M14-117 §2.1 同款）。
5. **env 手术**：先备份 `infra/env.production-recovery`，再仅改
   `AIOS_IMAGE_TAG` / `AIOS_WEB_IMAGE_TAG` 两键
   `m14-117-production → m14-124-production`（等长替换、键集合/
   行数不变式、`compose config --quiet` 校验 pass、值零回显——
   M14-70 `env_surgery.py` 先例）。
6. **最小范围切换**（api 先过健康门再放行 web；`--no-build` 禁止
   重建）：`docker compose -p aios-m14-03-production-rehearsal
   -f docker-compose.yml --env-file env.production-recovery
   --profile local --profile search up -d --no-build --no-deps api`
   → 健康轮询 ≤120s → 同款 `web`（M14-70/M14-117 精确命令形态）。
7. **切换后验收**：容器镜像 digest 复核 == §3；端点矩阵 7/7
   （API `8000/health`+`/api/v1/version`、Web `3011/`+`/login`、
   FunASR `8010/health`、CosyVoice `8011/health`、SearXNG
   `127.0.0.1:8878/healthz`）；真实浏览器验收（desktop/mobile ×
   `/`+`/login`，OVERALL=PASS）；`production_monitor.py --execute`
   一轮 ok；`production_recovery.py --dry-run --no-log-file` OK；
   API/Web 日志 tail + secret 扫描 `leaked_keys=NONE`。
8. **（可选）provider 冒烟刷新**：LLM 先经 Ollama `/api/generate`
   只加载驻留模型，三步冒烟 + `provider-smoke-aggregate
   --voice-mode local`；是否刷新由 supervisor 决定（运行时等价
   下 M14-117 切换后聚合按「源哈希与语义仍有效」口径亦可复用）。

全程硬边界：FunASR/CosyVoice 保持 managed-running 不触碰；
`up` 恒 `--no-build --no-deps`；五基础设施容器不在切换面；
`production_recovery.py` 不用于切换本身（env 已改、容器未切的
重叠窗口其 pin check 按设计拒绝），仅事后 dry-run 与日常自愈。

## 7. 回滚锚（未演练，不得宣称已验证）

- **回滚 tag**：`m14-117-production`（镜像在库：API
  `sha256:a10b4b62…8db37b5` / Web `sha256:95aff24f…aacd03c`，
  M14-117 README §3 登记；本切片 §5 只读复核在库）。
- **回滚路径**（M14-117 §9 同款）：env 两键回写
  `m14-117-production`（用切换前 env 备份逐字节恢复或反向等长
  手术）→ `up -d --no-build --no-deps api web` 最小范围重建 →
  重跑 preflight + 端点 7 项 + 浏览器验收 + monitor。
- **数据侧保护材料**：切换窗口第 3 步的 `aios-backup-v1` 备份
  （恢复经 `python -m app.ops.cli restore --backup-dir <目录>
  --db-url <生产URL>`）。

## 8. 诚实边界（不伪称）

1. **未切换**：生产仍运行 `m14-117-production`；本切片不构成、
   不预告任何部署。
2. **未审批**：release-approval 是 required 且 human-only 的门，
   从未发生；本切片零审批证据、不代拟、绝不合成。`release_ready=false`
   / `production_ready=false` 恒不变。
3. **运行时等价 ≠ 无需审批**：§4 等价结论只说明切换的运行时风险
   面（预期无行为差异），不降低也不替代任何发布门；是否切换、
   何时切换、「不部署仅登记差异」均是 supervisor 决策面。
4. **M14-123 code-bound 证据的 docs-only 漂移**：ci-main/release-check
   绑定 `d7072fd`，`d7072fd..684865d` 为 PR #210 docs-only 增量；
   按仓库契约「main 前移即再 stale」，接受漂移或先做证据刷新切片
   由 supervisor 判断（非本切片范围）。
5. provider/browser/monitor 为时点证据，不承诺窗口外状态；切换后
   24h soak 窗口不存在，是否开窗留 supervisor。
6. 本切片为 docs-only：唯一入库证据文件即本 README；不修改运行时
   代码、测试、compose、env 文件或任何 gitignored 证据（supervisor
   构建工作目录 `m14-124-production-build` 零触碰）。
