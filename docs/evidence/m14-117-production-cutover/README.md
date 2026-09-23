# M14-117：生产切换与运行验收证据

## 1. 结论与边界

M14-117 是一次真实生产运行时切换与验收收口，不是新的功能开发切片。

- 生产发布源：`2619ea77f3db291901e48eacdeea064b6f9b6fdb`
- 本证据分支基点：`6cc20dfe9656c20f6663433c72c5441a62c9b574`
- `6cc20df` 相对 `2619ea7` 仅 docs-only 变化：`docs/CHANGELOG.md`、`docs/PROJECT_STATUS.md`、`docs/ROADMAP.md`、M14-116 README，共 4 文件 +412 行；运行时代码与 `2619ea7` 一致。
- 生产栈已切换到 `m14-117-production`，API/Web 均为 healthy，Postgres、Redis、LiveKit、MinIO、SearXNG 未重建且 healthy。
- FunASR/CosyVoice 全程保持 managed-running；本轮未停止、未重启、未修改两个语音引擎。
- 本切片 tracked 变更仅文档；不修改应用代码、测试、工作流、生产 env 原文或服务配置。

必须保留的诚实语义：

- `release-approval` 是 human-only 门，本切片从未发生，也不代拟。
- `release_ready=false` 与 `production_ready=false` 的仓库契约不变。
- 生产已经实际运行 `m14-117-production` 不等同于发布审批已经完成，也不等同于公网生产就绪。
- provider/browser/monitor 是时点证据，不承诺窗口外永远健康。

## 2. 切换前保护

### 2.1 只读生产 preflight

产物：`artifacts/ops/m14-117-production-cutover/production-preflight.json`

结果：5/5 pass，0 fail / 0 pending / 0 not_configured。

| 检查 | 结果 |
|------|------|
| 数据库连通 | pass，PostgreSQL `ai_learning_os` |
| Alembic | pass，current == head == `0027_audit_chain` |
| 审计链 | pass，valid，0 entries / 0 audit rows |
| 历史治理 | pass，三类归属计数全 0 |
| 库外锚定 | pass，verify-only up-to-date，1 个锚点 |

该 preflight 全程只读，未执行 upgrade/downgrade，未写审计锚。

### 2.2 切换前备份

备份目录：`artifacts/ops/m14-117-production-cutover/pre-cutover-backup/`

- 格式：`aios-backup-v1`
- 表：30 个
- 文件：2 个（`database.json` 与一个上传对象）
- 备份 manifest SHA256：`09dd1d834db8ae77fd71a337a69819805e82ea380ed4edb59fce3fe5d5716aa9`
- manifest 记录 `database.json` SHA256：`0bc69ca5c5b03c95fadb887fd4c732043fc4ec7037e87762d1b057f478d61c3b`
- 上传对象路径与对象 SHA256 在 manifest 中逐项绑定。

### 2.3 env 手术证明

产物：`artifacts/ops/m14-117-production-cutover/env-cutover-proof.json`

仅修改两个键：

- `AIOS_IMAGE_TAG`
- `AIOS_WEB_IMAGE_TAG`

值均为 `m14-70-production -> m14-117-production`。16 行、键集合不变，仅第 2、3 行变化；compose config 校验 pass。

| 状态 | SHA256 |
|------|--------|
| 术前备份/术前文件 | `1090e1c1a4db73719d489de8869062c95659efac787152e89cd2109588d5d1dc` |
| 术后文件 | `258db8c2725722f59627f8b23dbc84d538c51cbc8a351e5d346aca8d1306e7bb` |

env 原文与备份不入库，值不在本 README 回显。

## 3. 生产运行锚点

| 对象 | 锚点 |
|------|------|
| API 镜像 | `aios/api:m14-117-production` = `sha256:a10b4b626b8ac8d7690607c5f2f543a1d5bd2a78aacfd3f5500f0912b8db37b5` |
| Web 镜像 | `aios/web:m14-117-production` = `sha256:95aff24f17fd7268b26ce16d4e87d64f3e0cede2aa72ab068d94d2012aacd03c` |
| API 容器 | `c240a73667ec67342d46c777d05c81704fadc4c5cdf07dcdd3868b88716b8bfd`，healthy |
| Web 容器 | `11cb1203d2872f45e80de14ad16b9bc3563567d707578906ae8634f8660bf7b0`，healthy |

切换范围仅 API/Web。Postgres、Redis、LiveKit、MinIO、SearXNG 容器 ID 保持不变并 healthy。切换后 `docker inspect` 复核 API/Web 容器镜像 digest 与上表一致。

## 4. Provider 冒烟

原始目录：worktree `.verify/artifacts/m14-117-production-cutover/provider-smoke/`

首轮 preflight blocked 的原因是 LLM 模型未驻留。随后通过 Ollama `/api/generate` 只加载 `aios-qwen3.5-9b-4096`，未改配置、未产出正文；模型 5.5 GB，GPU 100%，context 4096。复查 voice/search/llm 全 ready。

最终聚合：`provider-smoke.json`，local voice 拓扑，voice/search/llm 三槽位均 executed=true、result=pass，生成时间 `2026-09-24T07:29:35.556863+08:00`。

| 槽位 | 最终结果 | 关键事实 |
|------|----------|----------|
| search | pass | 查询 `Python`，4783 ms，results=5 |
| local voice | pass | ASR 1312 ms / transcript 42 bytes；TTS 3079 ms / 226604 bytes / RIFF WAV |
| LLM | pass | 11854 ms，正文 12 chars，rubric `[True, True]`，confidence 1.0 |

失败尝试全部保留，不改写、不删除：

- search attempt1：误用 WSL bash，环境未透传，70 ms fail。
- search attempt2/3：固定查询聚合 10.6 秒 / 10.5 秒，超过 10 秒默认界。
- local voice attempt1：官方 WAV 样例外网下载 `RemoteDisconnected`，后续改用本地 `asr_sample_zh.wav`。

## 5. 浏览器验收

产物目录：主仓 `artifacts/ops/m14-117-production-cutover/browser/`

结果：`OVERALL=PASS`

| 视口 | 路由 | 结果 |
|------|------|------|
| desktop 1440x900 | `/` | 200，标题含 AI Learning OS，无横向溢出 |
| desktop 1440x900 | `/login` | 200，标题含 AI Learning OS，无横向溢出 |
| mobile 390x844 | `/` | 200，无横向溢出 |
| mobile 390x844 | `/login` | 200，无横向溢出 |

四个页面均无 page error、无意外 console error。匿名访问受保护资源出现的 401 是预期行为，单独归类，不计为意外错误。截图与 `BROWSER-REPORT.txt` 已归档。

## 6. 端点、监控与日志

### 6.1 端点

2026-09-24 复测首轮把 SearXNG 误写为 `127.0.0.1:8888`，该失败 attempt 已保留；容器实际映射为 `127.0.0.1:8878->8080`，容器健康检查 healthy。正式复测 7/7 pass：

| 端点 | 状态 |
|------|------|
| API `/health` | 200 |
| API `/api/v1/version` | 200 |
| Web `/` | 200 |
| Web `/login` | 200 |
| FunASR `/health` | 200 |
| CosyVoice `/health` | 200 |
| SearXNG `127.0.0.1:8878/healthz` | 200 |

### 6.2 生产监控

两轮只读监控均保留：

1. 第一轮 `overall_status=warn`：无可用基线时按保守 full-tail 计入 Postgres 历史 7 条错误。
2. 第二轮 `monitor-20260923-233127.json`：`overall_status=ok`，34 ok / 0 warn / 0 critical，partial=false，monitoring_ready=true。

`monitoring_ready=true` 只表示该轮采集与阈值判断通过，不等于 `production_ready=true`。

### 6.3 日志与秘密扫描

API/Web 最近 1000 行快照已落盘：

- `artifacts/ops/m14-117-production-cutover/logs/api-tail-1000.log`
- `artifacts/ops/m14-117-production-cutover/logs/web-tail-1000.log`

复核窗口内未见错误、异常栈或密钥类命中。`scan_log_for_secrets.py` 对 API/Web 各检查 20 个 secret 值，`leaked_keys=NONE`。完整日志原文与 env 原文不入库。

## 7. RC 构建与本地隔离彩排

### 7.1 GitHub RC workflow

- Run：`35930556891`
- Workflow：`Release Candidate (manual only)`
- Branch：`release/2619ea7-rc`
- Head：`2619ea77f3db291901e48eacdeea064b6f9b6fdb`
- Conclusion：success
- Artifact ID：`10780298803`
- Artifact digest：`sha256:e86f929830e4b400b6f10b5008e51001f736edc18f109daa8acbc8a5dab9727d`

构建包下载未完成，不阻塞；后续改走本地隔离构建验证。

### 7.2 本地 RC 隔离彩排

产物目录：worktree `.verify/artifacts/m14-117-production-cutover-rc-smoke/`

- 构建基点：`2619ea7`，tracked 输入 clean。
- 一次性 task tag，不覆盖生产 tag。
- 5 个服务 healthy。
- 8/8 loopback probes pass。
- API/Web 端口分别为 `127.0.0.1:18096`、`127.0.0.1:13096`。
- teardown 后容器/卷残留为 0。

报告最终 `status=failed` 的唯一原因是 `external-container-drift`：无关 `moneyprinterturbo-api` 的状态文本在快照间从 “Restarting (1) 5 seconds ago” 变为 “31 seconds ago”。这是外部容器自身漂移，非 RC 栈失败；按边界未停止、未修改该无关进程。报告保留 failed，不改写为 pass。

## 8. 恢复 dry-run

2026-09-24 显式使用主仓 canonical env 重跑：

```powershell
& "D:\AI Learning OS\ai-learning-os\.venv\Scripts\python.exe" `
  "D:\AI Learning OS\ai-learning-os\tools\ops\production_recovery.py" `
  --dry-run --no-log-file
```

结果 OK：

- Docker engine ready，compose config pass。
- profile local 服务集合 api/livekit/minio/postgres/redis/web。
- 9/9 pin 一致，值不回显。
- 6/6 编排服务 healthy/running，健康栈无需 up。
- FunASR/CosyVoice 均 managed-running + `/health` 200，按边界 leave。

此 dry-run 全程只读，未触发恢复重建。

## 9. 回滚路径

未执行回滚演练，不得宣称回滚已验证。可用回滚材料与路径如下：

1. 使用切换前 env 备份恢复 `AIOS_IMAGE_TAG` / `AIOS_WEB_IMAGE_TAG` 为 `m14-70-production`。
2. 以固化旧 tag + no-build 方式最小范围重建 API/Web，禁止重新 build。
3. 使用本 README §2.2 的 `aios-backup-v1` 备份作为数据侧保护材料。
4. 恢复后重跑 production preflight、端点 7 项、browser 验收与 monitor。

## 10. canonical 证据索引

canonical 目录：worktree `.verify/artifacts/m14-117-production-cutover/canonical/`

该目录 gitignored，不入库；`SHA256SUMS` 索引 25 个安全证据文件，自不含自哈希。完整日志原文、env 原文、env 备份、数据库 JSON 与对象内容不入库。

| 分组 | 文件数 | 内容 |
|------|--------|------|
| provider-smoke | 8 | 聚合 + 3 个正式 pass + 4 个失败尝试 |
| monitor | 4 | 两轮 JSON/MD |
| browser | 5 | report + 4 张截图 |
| rc-smoke | 2 | JSON/MD 报告 |
| cutover | 3 | preflight、env proof、备份 manifest |
| endpoints | 2 | 正式 7/7 pass + 错误端口 attempt |
| recovery | 1 | canonical env dry-run log |

关键 SHA256：

| 文件 | SHA256 |
|------|--------|
| `cutover/production-preflight.json` | `49635fab66584b7debd123aec63057c74cbde9a6054e0b4b269a38385ac20fd9` |
| `cutover/env-cutover-proof.json` | `ec78caafc2112c0b2029a81ed4d3d8d6bdd98c73da8f979d6bbfcba51a0fb92e` |
| `cutover/manifest.json` | `09dd1d834db8ae77fd71a337a69819805e82ea380ed4edb59fce3fe5d5716aa9` |
| `provider-smoke/provider-smoke.json` | `029ee84f671457e5a379f56be39cebd16b306ba4711e0717aa3a5f5bdcfd163b` |
| `monitor/monitor-20260923-233127.json` | `59e5186a1b70f9e65e3096f1a95d8a09d2253c1e2c0a3e7c3323f561736f9b3e` |
| `browser/BROWSER-REPORT.txt` | `3856f0499b5ba11ab4d4d342e4c37dc4946eef918e7a8616ea4fe341460b0f87` |
| `rc-smoke/rc-smoke-report.json` | `6265cdf05f6dad6366ea03786a332083782e1f57786c341db7ee23fd0d3583d9` |
| `recovery/recovery-dry-run-20260924.log` | `3a7e31a803abe0b184c6d88eee55493856dd02005de6998c538f4490e8cac5dc` |

## 11. 本地验证与入库形态

本分支为 docs-only：

- tracked 变更仅 `docs/evidence/m14-117-production-cutover/README.md`、`docs/PROJECT_STATUS.md`、`docs/ROADMAP.md`、`docs/CHANGELOG.md`。
- 不修改代码、测试、工作流、compose、生产 env 或任何 provider 配置。
- 验证要求：`git diff --check`、docs secret 扫描、canonical SHA256 校验；由于零代码变更，不运行无关全量测试。
