# M14-208 证据：provider recovery（SearXNG 直连恢复 + Ollama 驻留，preflight ready）

## 0. 结论与边界

- 本切片是已执行 supervisor 生产恢复动作的 docs-only 证据收口：恢复发生在
  M14-208 文档回合之前；本回合只新增/更新文档、只读核对两个 preflight
  JSON 状态并计算字节数与 SHA-256，不重放任何生产变更，不读取或输出 env
  值，不触碰 secrets、容器、Ollama 或其他 worktree。
- Workspace：`ai-learning-os-worktrees/m14-208-provider-recovery`；branch
  `ops/m14-208-provider-recovery`；base `9ffe7232075419030c0aee282e04003b8f56a556`。
  本地 commit 后，supervisor 审查与 remote 发布（push/PR/合并）在其后进行。
- 恢复范围是 provider 前置能力：SearXNG 出站直连恢复与 Ollama 目标模型驻留。
  两次 provider-smoke preflight 分别证明 search 恢复后的 LLM 缺口、以及
  Ollama 恢复后的三槽位 ready。
- **preflight pass 不是 provider-smoke export/aggregate 证据**：本切片未执行
  search/local-voice/llm export，未生成新的 `provider-smoke.json`，也没有把
  任何旧聚合搬运为当前证据。`production_ready=false`、
  `release_readiness_evidence=false` 与 release readiness 的语义不变；
  M14-209 必须执行新的三 export 与 aggregate。
- 容器生命周期边界：仅生产 SearXNG 容器被 recreate；API、Web、DB、MinIO
  与 voice 容器均未被 recreate 或 stopped。

## 1. CI 基线与部署 env 完整性

- 恢复前 main CI run **36823521906** 绑定 head
  `9ffe7232075419030c0aee282e04003b8f56a556`，completed，5/5 jobs success。
  该 run 是恢复前代码/测试基线，不把本 docs-only 回合扩大为新的 CI 证据。
- supervisor 在恢复前先备份 canonical gitignored
  `infra/env.production-recovery`，备份路径为同目录
  `infra/env.production-recovery.before-m14-208`。两份文件完整性事实：

| 文件状态 | SHA-256 |
|---|---|
| 备份（恢复前 canonical env） | `4FA46A4DD214FAAB54E6BCA32B9720385C1F74920F11D563F174B1BCA9AFEC24` |
| enforce 后（恢复直连默认） | `DE7D2D57371FC8F0DA71FB1B1D631EAEAD0265DAE35EFBCC91933415B2C4BA64` |

本表格只记录摘要；env 键值、代理地址与其余行内容均不读取、不回显、不入库。

## 2. SearXNG 出站恢复与容器事实

### 2.1 代理槽位恢复

命令形状（仓库根，默认指向 canonical env 路径）：

```powershell
python tools/ops/searxng_egress_recovery.py --dry-run
python tools/ops/searxng_egress_recovery.py
python tools/ops/searxng_egress_recovery.py --dry-run
```

- 第一次 `--dry-run`：production recovery 九个 `PIN_KEYS` 键名验证齐全；
  计划仅禁用两个激活且非空的代理槽位：
  - `AIOS_SEARXNG_HTTP_PROXY`：第 12 行；
  - `AIOS_SEARXNG_HTTPS_PROXY`：第 13 行。
- enforce：只把上述两行转为禁用注释形态；`PIN_KEYS` 全部保持，写后重验
  通过。`AIOS_SEARXNG_NO_PROXY` 未在禁用计划中，未被该 helper 改动。
- 第二次 `--dry-run`：报告无激活且非空的代理槽位，确认 compose 渲染回
  直连默认。三次输出均不包含代理值。

### 2.2 两次失败尝试与误建 project 清理

第一次尝试同时遗漏 `-f` 与 `-p`，实际命令为：

```powershell
docker compose --env-file infra\env.production-recovery --profile local up -d --no-deps --no-build --force-recreate searxng
```

该命令失败，错误为 `no configuration file provided: not found`；
**未创建任何 container 或 volume**。

第二次尝试补上 `-f` 但仍遗漏 `-p`，实际命令为：

```powershell
docker compose -f infra\docker-compose.yml --env-file infra\env.production-recovery --profile local up -d --no-deps --no-build --force-recreate searxng
```

该命令选择默认 project `ai-learning-os`，创建：

- failed project container：
  `a1f821c42dfb8566acf442badb4965d5949b7eae619155962f45d8c082723685`
  （`Created` 状态，**从未启动**）；
- volume：`ai-learning-os_searxng-cache`。

随后启动失败，错误为
`Bind for 127.0.0.1:8878 failed: port is already allocated`；
端口由既有生产 searxng 容器持有。

清理命令形状为 `docker rm <failed-container-id>` 与
`docker volume rm ai-learning-os_searxng-cache`；二者只移除上述从未启动的
container 与其 volume，生产容器当时保持 healthy。该失误如实留档，不扩大为
生产服务影响。

### 2.3 正确的 searxng recreate

修正后的命令显式指定 compose file、生产 project 与 local profile：

```powershell
docker compose -f infra\docker-compose.yml -p aios-m14-03-production-rehearsal --env-file infra\env.production-recovery --profile local up -d --no-deps --no-build --force-recreate searxng
```

结果：

| 事实 | 值 |
|---|---|
| service | `searxng` |
| old container ID | `8228aa70ddd81dd4c6bf5a2e795284757554407560900ce25f3f9c895204fbd0` |
| new container ID | `a2ebab6e0a2e3370fd50bde3211fc881f2a39f42a6a186a60368c0b3459579be` |
| started at (UTC) | `2026-10-01T06:19:23.080184427Z` |
| health | reached `healthy` |

## 3. Ollama 模型供给与驻留

幂等供给命令形状：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File infra/provision_ollama_model.ps1
```

验证事实：

- Ollama version：`0.33.2`；
- base model：`qwen3.5:9b`；
- alias：`aios-qwen3.5-9b-4096`；
- `num_ctx`：`4096`；
- provision check：idempotent `PASS`。

随后 supervisor 以 24h keepalive 加载该 alias。只读 `ollama ps` 显示：

| 字段 | 值 |
|---|---|
| model | `aios-qwen3.5-9b-4096:latest` |
| size | `5.5 GB` |
| GPU | `100%` |
| context | `4096` |

模型名、大小、GPU 占比与 context 是非敏感运行时事实；本回合未触碰
Ollama 进程，也未输出任何凭据或请求内容。

## 4. Preflight 复核与工件指纹

命令形状（stdout JSON 另存为下表 artifact）：

```powershell
python -m app.ops.cli provider-smoke-preflight --voice-mode local --json
```

| artifact（本 worktree 相对路径） | generated_at (UTC) | exit | overall | provider 状态 | bytes | SHA-256 |
|---|---|---:|---|---|---:|---|
| `artifacts/temp/provider-smoke/m14-208-provider-recovery/preflight-after-searxng-recovery.json` | `2026-10-01T06:20:29.558818Z` | 1 | `blocked` | voice `ready`；search `ready`（9 results）；llm `model_absent` | 2541 | `B494A310512AA02369409A0537E8B028593A272B5ED1F3D1B1EC541717044C81` |
| `artifacts/temp/provider-smoke/m14-208-provider-recovery/preflight-after-ollama-recovery.json` | `2026-10-01T06:21:35.986879Z` | 0 | `pass` | voice/search/llm 均 `ready` | 2540 | `0385B30450B73A941B3C7CECD467B57BD24B8CB166F2D6F72438C791347A3790` |

两个 JSON 的 `production_ready=false`、`release_readiness_evidence=false`
与工具 note 均保持 fail-closed 语义。第二份 pass 只说明当时三个 provider
preflight 可观测且 ready；它不证明 search 查询、local voice ASR/TTS 或 LLM
生成的完整 smoke 已通过，也不生成 provider-smoke gate 证据。

## 5. 诚实边界与下一步

- 本 README 记录的是 supervisor 已执行恢复；文档实现回合未重放 env 写入、
  Docker/Ollama 生命周期、健康检查或 preflight。
- 未执行 API/Web/DB/MinIO/voice 容器 recreate、stop、restart 或 rollback。
- 未读取/输出任何 env 值、token、secret、代理地址或请求内容。
- M14-209 必须在新的证据窗口执行 fresh `provider-smoke-export search`、
  `provider-smoke-export local-voice`、`provider-smoke-export llm`，再执行
  `provider-smoke-aggregate`；不得把本切片 preflight 或 M14-148 旧聚合
  冒充为新 provider-smoke 证据。

## 6. 文档收口验证

- Canonical venv（主 checkout `.venv`，测试目标/工作目录均为本 worktree）
  执行 `services/api/tests/test_production_monitor.py::test_r1_docs_commit_wording_sweep`：
  **1 passed**，exit 0。
- `git diff --check`：exit 0（仅 Git 在 Windows 提示后续触碰可能做 LF→CRLF
  转换，无 whitespace error）。
- 对最终 staged 新增行执行 secret/token 值形态扫描：0 hits；同时检查
  代理值形态，0 hits。
