# M14-83：current-main 生产只读证据刷新（production read-only evidence）

- 切片：分支 `ops/m14-83-production-evidence-refresh`（独立 worktree
  `m14-83-production-evidence-refresh`，基于 main
  `5829ad9c7dbdfbcf0fc71728dbc2ecec2012135d`（PR #170 merge，M14-82 合入后
  的 current main，精确基点）），单次本地 commit（不推送、不建 PR）。
- 目标：对 current main 刷新**可在不改变生产状态的前提下真实重推导**的生产
  状态证据——production-preflight post-migration、legacy-papers /
  draft-ownership 治理、audit-chain verify-only、本地拓扑 provider 冒烟
  （search + local-voice + llm）。绝不运行 backup-restore、绝不创建/更新任何
  审计锚（锚定动作与备份演练列入 §7 下一步安全计划）、绝不手改 gate JSON、
  绝不合成 pass 状态、绝不伪称 production readiness
  （`release_ready=false` 全程不变，readiness exit 1 如实保留）。
- 零生产突变（本切片全程）：零容器启停/重建/删除、零计划任务操作、零 CC
  Switch / 代理 / FunASR / CosyVoice / Ollama / WSL 进程启停（Ollama 发现
  inactive 后**未启动**——如实记录为失败而非修复）、零生产镜像/部署变更、
  零 DB 写入（全部只读 SELECT/inspector）、零锚文件写入（verify-only）、
  零 soak 历史读写、零发布审批接触；**未读取 `infra/env.production-recovery`
  或任何 secret 文件**，全程未回显任何 key/token/secret 值。

## 1. 基点事实与生产拓扑（全部可复核）

| 项 | 值 | 来源 |
|----|----|------|
| 基点 commit | `5829ad9c7dbdfbcf0fc71728dbc2ecec2012135d`（PR #170 merge） | git |
| 生产 compose project | `aios-m14-03-production-rehearsal`（七容器） | `docker ps`（只读） |
| 生产 API/Web 镜像 | `aios/api:m14-70-production` / `aios/web:m14-70-production`（2026-09-19 切换后未再变更） | `docker ps`（只读） |
| 基础设施容器 | postgres `17-alpine` / redis `7-alpine` / livekit / minio（自建 RELEASE.2025-10-15）/ searxng（digest pin）均 healthy | `docker ps`（只读） |
| 本地语音引擎 | FunASR `127.0.0.1:8010`（/health 200）/ CosyVoice bridge `127.0.0.1:8011`（TCP 可达）——宿主 WSL 部署 | 匿名探测（只读） |
| SearXNG | `127.0.0.1:8878`（/healthz 200；但上游引擎出站全部失败，见 §4.4） | 匿名探测（只读） |
| Ollama | `127.0.0.1:11434` 连接拒绝；WSL Ubuntu 内 `systemctl is-active ollama` = **inactive**（未启动，按边界不代启） | 只读探测 |
| 代码绑定门基线 | ci-main/release-check 的 current 记录仍为 M14-81 canonical（@ e1f128b）；对 `5829ad9` 的重推导不在本切片 scope（非生产状态证据） | §6 |

## 2. 安全命令策略（不读 secret 的执行方式）

1. **执行代码 = current main**：全部命令在 worktree 从零环境执行（`uv venv
   .venv --python 3.12` + `uv pip install -r services/api/requirements.txt
   -r services/api/requirements-dev.txt`，直连未用代理）——生产 API 容器内
   是 m14-70 镜像代码，本切片要刷新的是 current-main 证据，故不进容器执行；
   相应只读工具模块（production_preflight / audit_chain_verify /
   audit_chain_anchor / legacy_papers / draft_ownership /
   governance_evidence / provider_smoke_evidence）在 `976798f2..5829ad9`
   区间**零 diff**（已 `git diff --stat` 核实），代码版本语义无漂移。
2. **生产 DB 只读访问不经任何 secret 读取**：`infra/docker-compose.yml` 第
   100 行将 `DATABASE_URL` 硬编码为仓库公开值
   `postgresql+asyncpg://aios:aios@postgres:5432/ai_learning_os`（无 `${}`
   插值，不受 env 文件覆盖——即生产容器实际使用的就是这条连接串；凭据
   `aios:aios` 为 compose 第 24–27 行的公开默认，**不是 secret**）。宿主侧
   经 compose 固定 loopback 映射 `127.0.0.1:5433:5432`（第 30 行）访问：
   `export DATABASE_URL="postgresql+asyncpg://aios:aios@127.0.0.1:5433/ai_learning_os"`。
   全程**未打开** `infra/env.production-recovery`（其 15 键为部署插值变量，
   与 DB 连接无关，本切片零接触）。
3. **锚定 verify-only**：`--verify-only` 旗标语义保证不追加锚行（`written=false`
   在输出中可复核）；锚文件使用 canonical 副本
   `.verify/artifacts/m14-42-audit-chain-anchor/audit-anchor.jsonl`
   （354 bytes，sha256 `d2bfd877…73aa4e`，与 m14-75 WORM 归档对象同源；
   生产容器 `/tmp` 侧原始锚文件已随 M14-70 容器重建消失——m14-75 亦使用
   副本 `/tmp/m14-75-anchor/audit-anchor.jsonl`，同一模式）。
4. **provider 冒烟只打回环端点**：search 打本地 SearXNG（匿名无 key）；
   local-voice 打 FunASR/CosyVoice（探针自身零 env 需求，ASR 样例用主仓
   CosyVoice 仓库自带官方 asset
   `artifacts/voice/cosyvoice/CosyVoice/asset/zero_shot_prompt.wav`，与脚本
   默认下载 URL 同源文件，避免网络下载）；llm 打 `127.0.0.1:11434/v1`
   （本地 Ollama 无鉴权；`LLM_API_KEY` 注入非敏感占位值
   `local-noauth-placeholder`——该值不是任何系统的凭据，仅为满足脚本
   非空校验；模型用仓库固定别名 `aios-qwen3.5-9b-4096`）。所有冒烟均为
   纯计算/查询请求，不改变生产状态。

## 3. 刷新执行记录（命令与结果，全部真实可复跑；UTC 时间）

### 3.1 audit-chain-verify（DB 链只读校验）

```bash
export DATABASE_URL="postgresql+asyncpg://aios:aios@127.0.0.1:5433/ai_learning_os"
cd <worktree>/services/api
<worktree>/.venv/Scripts/python.exe -m app.ops.cli audit-chain-verify --json
# exit 0
```

结果：`valid=true`、`entries=0`、`audit_rows=0`、`problems=[]`（sha256
算法；2026-09-21T16:30Z 执行）——生产审计链仍为空链（与 m14-75 一致，
生产运行未产生审计事件）。原始 stdout（该 CLI 无 `--output` 参数）原样
归档为 canonical `evidence/audit-chain-verify.json`（105 bytes）。与
m14-75 同名文件的差异：m14-75 版本带 `tool/gate/step` 自标识头三键（运维
侧组装）；本切片保持**工具原始 stdout 字节**，不做手工包装——该文件不是
release-readiness 的门文件（只进 unrecognized 名单），readiness 消费不受
影响。

### 3.2 audit-chain-anchor --verify-only（锚交叉校验，零写入）

```bash
<worktree>/.venv/Scripts/python.exe -m app.ops.cli audit-chain-anchor \
  --anchor-file "D:/AI Learning OS/ai-learning-os/.verify/artifacts/m14-42-audit-chain-anchor/audit-anchor.jsonl" \
  --verify-only --json
# exit 0
```

结果：`status=up-to-date`（DB head sequence=0 / head_hash 全零 genesis 与
最后锚点一致）、锚文件 1 锚点（sequence 0，anchor_hash
`59c672b9be0ea82…af627c02`）、**`written=false`、`problems=[]`**
（2026-09-21T16:31Z）。原始 stdout 归档 `raw/audit-chain-anchor-verify-only.json`。

### 3.3 production-preflight（post-migration 只读汇总预检）

```bash
<worktree>/.venv/Scripts/python.exe -m app.ops.cli production-preflight \
  --phase post-migration \
  --anchor-file "D:/AI Learning OS/ai-learning-os/.verify/artifacts/m14-42-audit-chain-anchor/audit-anchor.jsonl" \
  --json --output <worktree>/artifacts/m14-83-readonly/production-preflight.json
# exit 0
```

结果（generated_at 2026-09-21T16:31:41Z）：**5/5 pass、0 pending、
0 fail、0 not_configured**——

| 检查 | 状态 | 关键数据 |
|------|------|----------|
| db-connect | pass | postgresql / ai_learning_os |
| alembic | pass | current == head == `0027_audit_chain` |
| audit-chain | pass | valid（0 entries / 0 audit rows） |
| legacy-governance | pass | 无归属非 seed 卷=0 / 两类 NULL owner 草稿=0 |
| audit-anchor | pass | verify-only up-to-date（1 锚点，零写入） |

证据形态说明：CLI `--output` 原始报告不含 gate 自标识头
（`_eval_production_preflight` 会 fail-closed 拒绝无 `gate` 键的文件），
本切片按 **M14-78 ci-main 同款模式**程序化转录：一次性脚本读取工具
`--output` 原始文件（归档 `raw/production-preflight-tool-output.json`，
2001 bytes）、前置 `tool/gate/step` 三键后原子写出
`evidence/production-preflight.json`，并逐键断言**除头三键外与工具报告
完全一致**（零值改动）。这与 m14-75 的运维侧组装同形，但组装过程可由
raw 文件逐字节比对复核。

### 3.4 治理报告与门证据推导（legacy-papers / draft-ownership）

```bash
<worktree>/.venv/Scripts/python.exe -m app.ops.cli legacy-paper-report \
  --json --output <raw>/legacy-paper-report.json   # exit 0
<worktree>/.venv/Scripts/python.exe -m app.ops.cli draft-owner-report \
  --json --output <raw>/draft-owner-report.json    # exit 0
<worktree>/.venv/Scripts/python.exe -m app.ops.cli governance-evidence \
  --report <raw>/legacy-paper-report.json \
  --output <…>/legacy-papers.json --json           # exit 0
<worktree>/.venv/Scripts/python.exe -m app.ops.cli governance-evidence \
  --report <raw>/draft-owner-report.json \
  --output <…>/draft-ownership.json --json         # exit 0
```

- 两份报告（2026-09-21T16:34Z）：`summary.total=0`——生产治理仍归零
  （无历史无归属卷、无 NULL owner 草稿；与 m14-75 快照一致，生产自那时
  起未产生新的待治理数据）。报告含生产 ID 明细的位置为空列表，零 ID
  泄露面。
- `governance-evidence`（仓库既有机器推导器，非手工拼装）从报告推导：
  两门均 `pending_count=0`、`batches_executed=0`（报告已归零时零批次是
  合法自然形态）、报告 sha256 内嵌（legacy
  `8d03b75c…caa66c` / draft `118505ea…4e700`）→ evaluator 判 **pass**。

### 3.5 本地拓扑 provider 冒烟（真实端点，如实结果）

```bash
# search（SearXNG 匿名回环）
SEARCH_CLOUD_ENDPOINT="http://127.0.0.1:8878" \
  <py> -m app.ops.cli provider-smoke-export search --output <…>/search-smoke.json --json
# exit 1 → result=fail（证据照常落盘）

# local-voice（FunASR + CosyVoice，零 env 需求）
ASR_SMOKE_AUDIO="D:/AI Learning OS/ai-learning-os/artifacts/voice/cosyvoice/CosyVoice/asset/zero_shot_prompt.wav" \
  <py> -m app.ops.cli provider-smoke-export local-voice --output <…>/local-voice-smoke.json --json
# exit 0 → result=pass

# llm（本地 Ollama 端点 + 非敏感占位 key + 仓库固定模型别名）
LLM_ENDPOINT="http://127.0.0.1:11434/v1" LLM_API_KEY="local-noauth-placeholder" \
LLM_MODEL="aios-qwen3.5-9b-4096" \
  <py> -m app.ops.cli provider-smoke-export llm --output <…>/llm-smoke.json --json
# exit 1 → result=fail（证据照常落盘）

# 聚合（local 拓扑）
<py> -m app.ops.cli provider-smoke-aggregate \
  --search <…>/search-smoke.json --voice <…>/local-voice-smoke.json \
  --llm <…>/llm-smoke.json --voice-mode local \
  --output <…>/provider-smoke.json --json
# exit 1（任一 fail 即 1，证据照常落盘）
```

| 槽位 | 结果 | 证据事实（2026-09-21T16:35–16:37Z） |
|------|------|------|
| local-voice | **pass**（duration 25077ms） | FunASR 真实 ASR：latency 2911ms、transcript 42 bytes（官方样例真实转写）；CosyVoice 真实 TTS：latency 21817ms、RIFF WAV 241,964 bytes（脱敏摘要归档 `raw/provider-smoke-local-voice.stdout.log`） |
| search | **fail**（exit_code 1，duration 1503ms） | 脚本判定「0 条合法结果」；只读诊断复核：SearXNG `/search?format=json` 返回 `unresponsive_engines` = brave / duckduckgo / google cse / wikidata / wikipedia **全部 HTTP connection error**——SearXNG 服务本身存活（/healthz 200）但其上游引擎出站不可达（容器出站网络当前断开）。按边界不动代理/容器配置，如实 fail |
| llm | **fail**（exit_code 1，duration 2242ms） | `WinError 10061` 连接拒绝——host `127.0.0.1:11434` 无监听；WSL Ubuntu 内 Ollama `systemctl is-active` = inactive。按边界**不启动** Ollama 进程，如实 fail（traceback 归档 `raw/provider-smoke-llm.stderr.log`，无 key 值） |

- 聚合 `provider-smoke.json`（voice_mode=local）：voice=pass /
  search=fail / llm=fail——readiness 的 provider-smoke 门判 **blocked**
  （如实：语音本地链路可用，搜索与 LLM 槽位当前不可用，需运维窗口恢复
  SearXNG 出站与 Ollama 后重跑）。

### 3.6 release-readiness 聚合（M14-83 canonical 证据目录）

```bash
<py> -m app.ops.cli release-readiness \
  --evidence-dir "D:/AI Learning OS/ai-learning-os/.verify/artifacts/m14-83-production-read-only-evidence/evidence" \
  --json --output <worktree>/artifacts/m14-83-readonly/readiness/release-readiness.json
# exit 1（release_ready=false，按任务要求如实保留）
```

manifest（generated_at 2026-09-21T16:39:39Z）：

| 门 | required | 状态 |
|----|----------|------|
| production-preflight | ✓ | **pass**（post-migration 放行形态 5/5） |
| legacy-papers | ✓ | **pass**（pending_count=0） |
| draft-ownership | ✓ | **pass**（pending_count=0） |
| provider-smoke | ✓ | **blocked**（voice=pass / search=fail / llm=fail） |
| ci-main | ✓ | missing（对 `5829ad9` 未重推导——代码绑定门非本切片 scope；对 e1f128b 的最新记录在 M14-81 canonical，已随 main 前移 stale） |
| release-check | ✓ | missing（同上） |
| backup-restore | ✓ | missing（本切片明确不运行备份演练，见 §7 计划） |
| audit-chain-anchor | ✓ | missing（verify-only 佐证在 raw/；完整门证据形态含 WORM 归档核验，超出只读边界，见 §6） |
| long-soak | ✓ | missing（24h 审计未发生；权威 soak 锚最早审计时点 2026-09-22T15:00:01Z 未到/未执行） |
| release-approval | ✓ | missing（human-only，从未发生，不代拟） |
| turn-tls | optional | missing（公网语音发布形态才必需） |

**pass=3 / blocked=1 / required missing=6 / optional missing=1；
malformed=0、tampered=0；`release_ready=false`、exit_code=1**——本切片
不授权任何部署。M14-83 证据目录**只放本切片真实重推导的生产状态门**，
不搬运 m14-75/M14-81 旧文件冒充 current（单步冒烟明细与
audit-chain-verify.json 为 provider-smoke 门/审计链的支撑文件，进
unrecognized 名单属预期——与 m14-75 目录形态一致）。

## 4. 聚焦验证与静态检查（真实执行结果）

- 聚焦契约测试（本切片证据链所依赖的全部工具面，worktree venv）：

```bash
cd services/api && <worktree>/.venv/Scripts/python.exe -m pytest -q \
  tests/test_release_readiness.py tests/test_governance_evidence.py \
  tests/test_provider_smoke_evidence.py tests/test_production_preflight.py \
  tests/test_audit_chain_anchor.py tests/test_audit_chain.py \
  tests/test_legacy_paper_governance.py tests/test_draft_ownership.py
# 461 passed, 4 skipped in 33.21s
```

- canonical 全部 JSON `json.load` 解析通过；release-readiness 消费即契约
  校验（malformed=0 / tampered=0）；`evidence/production-preflight.json`
  与 raw 工具报告逐键一致（转录脚本内置断言）。
- 本切片零 Python 源码改动（docs-only），`git diff --check` 干净。
- 分支卫生：单 commit 后 tracked-clean（worktree 侧 `artifacts/` 生成现场
  gitignored 不入库）；未 push、未建 PR。

## 5. canonical 证据清单（主仓 gitignored `.verify`，不入 git；本 README 为唯一入库证据文件）

路径：`.verify/artifacts/m14-83-production-read-only-evidence/`（17 文件）

| 文件 | bytes | sha256 |
|------|-------|--------|
| `evidence/production-preflight.json` | 2103 | `b3a2be66fd99b82c56942449ac32bb34248d290adbaab55438fbcc2ef55e1e51` |
| `evidence/legacy-papers.json` | 574 | `83cd62bdb0a336e485e9ad8a324691f2af9e4f002a53fcb031acd4535d0dbc16` |
| `evidence/draft-ownership.json` | 578 | `e90ae04c7ce34f3deff0a19d99a91674cd9d9e3149906a491d5446212c396aad` |
| `evidence/provider-smoke.json` | 564 | `e8543dd68bd7ea56694e6bf2b49a22735d70ed56d6625ae734510d501ca8c952` |
| `evidence/local-voice-smoke.json` | 308 | `ce9a0a4687626b9805b4122a64adc0d94187af49bf61f4155d07a2b45f027ae7` |
| `evidence/search-smoke.json` | 302 | `9617284672ab3dbc3bc0dbf3d7aa87dcafea28cc5d3ef7931c19496cdae1b537` |
| `evidence/llm-smoke.json` | 299 | `2cb3a20cd6b62720a42946d3142df70c37816e3898d75d4a348a1b75ea2c7447` |
| `evidence/audit-chain-verify.json` | 105 | `129b2ef0eac7910930535cd75decbe24ba4f460f554343753de39789b6c86458` |
| `evidence/release-readiness.json` | 8682 | `7b21668853485ae5439c2947bd296dd069c4804a872c005b4a79e4d1d3d212af` |
| `raw/audit-chain-anchor-verify-only.json` | 705 | `19eb43df04d08b40b92668b219442aee0631e1f0460786e61383172ec09c9c58` |
| `raw/production-preflight-tool-output.json` | 2001 | `8772f61f71778b7caed811de5523db3dfecea710f3b2a6c3083cf2d746074435` |
| `raw/legacy-paper-report.json` | 801 | `8d03b75cfd703afb6911a10cb0042b37711fae69ef4964462ef476acc9caa66c` |
| `raw/draft-owner-report.json` | 1598 | `118505eae510d871edc431341d9cbc884e4edc903c53ed28e7e3ca2d9464e700` |
| `raw/provider-smoke-local-voice.stdout.log` | 852 | `af15e8df7c1085fa82f6291f156886ccf8978fd5616ce1c214cdab260a364556` |
| `raw/provider-smoke-search.stdout.log` | 314 | `26105e40817c438811389192376f070011280e87326514bcb450b3bc48d02f78` |
| `raw/provider-smoke-search.stderr.log` | 222 | `8e049221d7224adaf2a3cbb64b323a7c12877e1604ceaef1e35ac15a186ec542` |
| `raw/provider-smoke-llm.stderr.log` | 5353 | `4b7e27d72083c8577298d5b4aa8a3b010ee3956a3bb75866e4dc23e69ae2a672` |

（worktree 侧 `artifacts/m14-83-readonly/` 为生成现场，gitignored；
canonical evidence 九件为工具产物逐字节复制/转录，哈希逐一复核。）

## 6. 诚实边界与剩余阻塞（不伪称，逐项可执行收口）

1. **provider-smoke 门 blocked（如实）**：本地语音链路 pass，但 search
   （SearXNG 上游引擎出站全部连接错误）与 llm（Ollama inactive、11434 无
   监听）当前不可用。恢复动作超出本切片边界（涉及容器出站网络/代理与
   WSL 服务启动）：运维窗口恢复 SearXNG 出站连通 + 启动 Ollama（含
   `infra/provision_ollama_model.ps1` 幂等供给确认）后重跑 §3.5 三命令
   并重新聚合。
2. **audit-chain-anchor 门 missing（如实）**：verify-only 佐证（链 valid +
   锚 up-to-date + 零写入）已归档 raw/，但该门的完整证据形态（chain/
   anchor/worm 三块，worm 需 MinIO 对象锁在线核验 + 离线副本核验拼装）
   需要运维窗口与 sanctioned 凭据上下文，且 m14-75 形态为运维侧组装——
   本切片不手工拼装 gate JSON，门保持 missing。锚定本身 up-to-date 无需
   追加（head 无前进），WORM 归档核验列入 §7。
3. **backup-restore 门 missing（按任务边界明确不运行）**：备份演练涉及
   生产备份与恢复回灌，必须单独运维窗口执行——精确安全计划见 §7。
4. **ci-main / release-check missing（scope 边界）**：本切片只刷新生产
   状态证据；对 `5829ad9` 的代码绑定门刷新（gh api run 查询 + 隔离
   release-check full 重跑）是独立的 evidence-refresh 切片工作，M14-81
   canonical（@ e1f128b）仍是这两个门的最新真实记录（对 `5829ad9` 已
   stale，如实）。
5. **long-soak missing**：24h 窗口审计未发生；M14-79 soak 权威锚
   （2026-09-21T15:00:01Z）的最早审计时点 2026-09-22T15:00:01Z 届满后
   由 `soak_stability_audit` 真实判定，本切片零 soak 历史读写。
6. **release-approval 从未发生**：human-only，不触碰、不代拟。
7. `release_ready=false`、`production_ready=false` 不变；本切片不授权
   任何部署。
8. 生产仍运行 **m14-70 镜像**（2026-09-19 切换后未再变更）；本切片对
   生产 DB 的全部访问为只读（SELECT/inspector），对生产容器/进程零
   启停零重建。

## 7. 下一步 backup-restore 演练安全计划（独立运维窗口，本切片不执行）

前置事实：m14-75 的 backup-restore 证据（`aios-backup-v1` manifest +
恢复演练 verified 回灌 32 行）对当前生产已 15+ 小时，重演练属生产写
路径（备份读取 + 独立恢复库写入），按边界必须独立切片执行。

1. **只读预检（无写路径）**：先以本切片同款只读方式复核生产 DB 可达
   （§2 策略）与 MinIO 凭据上下文（在已运行容器内执行
   `python -m app.ops.cli backup` 的 dry 语义前先读
   `services/api/app/ops/backup.py` 的 CLI 契约确认三件套备份的 S3
   source 解析；S3 凭据为 compose 公开默认 `aios/aios12345`，经
   `127.0.0.1:9000` 回环可达——仍不回显任何值）。
2. **备份执行（生产读 + 备份目标写，不动生产数据）**：运维窗口内运行
   `app.ops.cli backup --db-url <同 §2> --out <artifacts/temp 新目录>
   --config <逐件列出>`，产物 manifest 记录 sha256——**绝不**覆盖既有
   m14-75 备份目录。
3. **恢复演练（独立一次性 SQLite/临时 PG，绝不连生产库）**：按
   m14-75 模式 `app.ops.cli restore --backup-dir <新备份> --db-url
   <一次性恢复库>`，断言回灌行数 > 0 且与 manifest 一致；恢复库用后
   删除。全程生产 DB 零写入。
4. **证据导出**：`backup_restore_evidence`（M11-04 既有工具）从演练
   结果导出 `backup-restore.json`（verified=true + inserted_rows），
   复制入 M14-83 后继证据目录后重跑 release-readiness。
5. **同窗口顺带项（可选，运维批准后）**：audit-chain-anchor 门闭合
   （WORM 在线核验 + 离线副本核验 + 按 m14-75 形态组装三块证据）与
   provider 恢复（SearXNG 出站 + Ollama 启动）后重跑 §3.5——三者共享
   同一窗口可减少生产触碰次数。
6. **回滚与中止条件**：任一步骤出现非预期写路径（备份目录异常、恢复
   库指向生产 URL、容器重启需求）立即中止并保留现场证据；演练期间
   生产只读监控（`production_monitor`）持续观察，任何生产容器
   restart/error 告警即中止。
