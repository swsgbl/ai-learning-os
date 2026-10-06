# M14-244 证据：current-main provider-smoke 新鲜刷新（preflight blocked；search 真实 pass；llm/local-voice 诚实未执行；aggregate 诚实未执行）

## 0. 结论与边界

- 本切片在 current main `6756ba9b3d7b70da4945f460f4ff3d9f60457c03`（PR #330
  merge，即 M14-243）上做 M14-242 遗留 provider-smoke 阻塞面的新鲜重跑：
  全新 preflight → 按预检就绪状态选择性执行 provider-smoke export →
  满足契约才聚合（未达条件则诚实不执行并落 failure-closeout）。
- 本轮诚实结果：preflight **overall blocked**（voice `not_ready`/
  `endpoint_absent`；search `ready`；llm `not_ready`/`transport_error`）→
  `provider-smoke-export search` 真实执行 **pass**（exit 0，单次未重试，
  5 条合法 http 结果——**M14-207 以来 search 冒烟的首次真实 pass**，
  M14-223 探针超时对齐（10s→30s）与上游部分恢复的共同结果）；
  `provider-smoke-export llm` **未执行**（预检 llm `transport_error`，
  `http://127.0.0.1:11434/v1` `/api/ps` 传输层失败、模型不驻留；任务
  边界禁止启动/重启 Ollama 或调查修复端点）；`provider-smoke-export
  local-voice` **未执行**（预检 voice `endpoint_absent`，ASR 8010 / TTS
  8011 health 均不可达；任务边界禁止启动 ASR/TTS/WSL/voice 服务）；
  `provider-smoke-aggregate --voice-mode local` **未执行**（聚合契约
  要求恰好三份单步证据输入，llm 与 local-voice 单步证据因预检
  not_ready 未产生，一份输入不构成可执行聚合形态）。
- 未修改任何 smoke 脚本/工具源码（本轮零 Python 变更）；未复用任何
  旧 JSON（本 worktree 证据目录建于空 artifacts 之上）；未重试任何
  provider（search 恰一次、其余零次）；未手工编辑任何 gate JSON；未
  调整任何预算/超时（search 探针沿用 M14-223 对齐后的脚本默认 30s，
  LLM 预算/超时变量本轮根本未注入——llm 未执行）。
- 本证据只描述本轮 provider-smoke 执行窗口，不表示 preflight 语义
  扩大、不表示 release readiness / release approval；
  `production_ready=false`、`release_ready=false`、`public_ready=false`
  边界全部不变。provider-smoke 门对 release-readiness 的当前权威结论
  仍由完整聚合承载：本轮没有新的 `provider-smoke.json`，**M14-209
  聚合（voice pass / search fail / llm fail，2026-10-01T11:57Z）仍是
  最近一次完整聚合，provider-smoke 门整体仍 blocked**。
- 未读取或输出任何 secret/token/key/password 值（LLM key 占位符本轮
  未注入——llm export 未执行）；未触碰生产 DB 或生产状态；未
  stop/start/recreate 任何 Docker/WSL/Ollama/SearXNG/ASR/TTS/voice
  服务；真实 provider 请求只经仓库 provider-smoke 工具发出（恰一次
  search 真实请求 + preflight 只读探针）。

## 1. 执行环境与调用形状

- base：`6756ba9b3d7b70da4945f460f4ff3d9f60457c03`（PR #330 merge）。
  worktree
  `ai-learning-os-worktrees/m14-244-current-provider-smoke-refresh`、
  分支 `ops/m14-244-current-provider-smoke-refresh` 建于该提交，执行前
  porcelain 为空（`pre-run-metadata.json`）。
- 证据目录（gitignored）：
  `artifacts/temp/provider-smoke/m14-244/20261006T102600582Z/`，建于
  执行前；本 README 表 2/表 3 的全部工件都在该目录且未离开过
  gitignored 区域（同目录 `SHA256SUMS` 索引、索引不含自身）。
- 解释器：主 checkout canonical venv `python.exe`（Python 3.11.15）；本
  worktree 无 `.venv`，冒烟 wrapper 经非敏感 `PYTHON` 环境变量名接收该
  解释器路径（值不写入本 README）。
- 端点全部使用仓库文档化默认 loopback 值
  （`provider_smoke_preflight.py` `DEFAULT_*`）：search
  `http://127.0.0.1:8878`、ASR `http://127.0.0.1:8010/v1`、TTS
  `http://127.0.0.1:8011/v1`、LLM `http://127.0.0.1:11434/v1`，LLM
  模型别名 `aios-qwen3.5-9b-4096`。search 冒烟按脚本契约显式注入
  `SEARCH_CLOUD_ENDPOINT`（loopback 值，无鉴权路径不设 key）；llm 冒烟
  所需 `LLM_ENDPOINT`/`LLM_MODEL`/`LLM_API_KEY`（非敏感非空占位符）
  本轮未注入——llm 预检 not_ready 故未执行；预算/超时保持脚本默认
  （`LLM_SMOKE_MAX_TOKENS`/`LLM_TIMEOUT_SECONDS`/`LLM_NUM_CTX` 均未
  设置）。
- 官方命令形状（cwd=preflight/export 均 `services/api/`，省略非敏感
  环境赋值与绝对路径展开）：

```bash
<canonical-python> -m app.ops.cli provider-smoke-preflight --voice-mode local --json
PYTHON=<canonical-python> SEARCH_CLOUD_ENDPOINT=http://127.0.0.1:8878 \
  <canonical-python> -m app.ops.cli provider-smoke-export search --output <EV>/search-smoke.json --json
PYTHON=<canonical-python> <canonical-python> -m app.ops.cli provider-smoke-export local-voice --output <EV>/local-voice-smoke.json --json   # 未执行（见 0/2）
PYTHON=<canonical-python> LLM_ENDPOINT=… LLM_MODEL=… LLM_API_KEY=<非敏感占位符> \
  <canonical-python> -m app.ops.cli provider-smoke-export llm --output <EV>/llm-smoke.json --json   # 未执行（见 0/2）
<canonical-python> -m app.ops.cli provider-smoke-aggregate --search <EV>/search-smoke.json --voice <EV>/local-voice-smoke.json --llm <EV>/llm-smoke.json --voice-mode local --output <EV>/provider-smoke.json --json   # 未执行（见 0/2）
```

`<EV>` = 上文新 UTC 证据目录（绝对路径传入；llm 行的
`LLM_ENDPOINT`/`LLM_MODEL` 为文档化默认 loopback 值，本轮未实际注入）。

## 2. 官方执行结果（live 结果矩阵）

| 命令 | exit | result | started_at (UTC) | completed_at/generated_at (UTC) | duration_ms | 关键事实 |
|---|---:|---|---|---|---:|---|
| `provider-smoke-preflight --voice-mode local --json` | 1 | overall `blocked` | `2026-10-06T10:26:45.903Z`（shell 窗口起） | `2026-10-06T10:27:17.686828+00:00` | 31871（shell 窗口） | voice `not_ready`/`endpoint_absent`（ASR/TTS health `http_status=null`）；search `ready`（200，results=9，上游 brave/duckduckgo/google cse unresponsive，错误类 [CAPTCHA, timeout]，`probe_timeout_seconds=30.0`）；llm `not_ready`/`transport_error`（`/api/ps` 传输层失败，`model_resident=false`）；ambient proxy pressure=false；`production_ready=false`/`release_readiness_evidence=false` 恒定 |
| `provider-smoke-export search` | 0 | **pass** | `2026-10-06T10:27:59.110573+00:00` | `2026-10-06T10:28:10.148338+00:00` | 11037（工具自报） | 真实 SearXNG-compatible 请求返回 5 条合法 http 结果（`results=5`，authority=community），脚本全检查 PASS；单次执行未重试；shell 窗口 10:27:58.790Z–10:28:10.251Z（11461ms） |
| `provider-smoke-export llm` | 不适用 | **未执行** | 不适用 | 不适用 | 不适用 | 预检 llm `transport_error`（11434 `/api/ps` 不可达、模型不驻留）；任务边界禁止启动/重启 Ollama 与端点外部调查（blocked 原因与证据见 `failure-closeout.json` 与 preflight JSON） |
| `provider-smoke-export local-voice` | 不适用 | **未执行** | 不适用 | 不适用 | 不适用 | 预检 voice `not_ready`/`endpoint_absent`；任务边界禁止启动/重启 ASR/TTS/WSL/voice 服务 |
| `provider-smoke-aggregate --voice-mode local` | 不适用 | **未执行** | 不适用 | 不适用 | 不适用 | 聚合契约要求恰好三份单步证据输入；llm 与 local-voice 证据未产生，仅一份 search 输入不构成可执行形态，不伪造聚合 |

编排说明：本轮无编排笔误（无重定向路径错误、无误建目录、无证据迁移），
preflight 与 search export 各恰好一次真实调用；未发生任何 provider
失败重试。

## 3. 与既有证据的关系（不搬运旧结论）

- M14-209（`docs/evidence/m14-209-provider-smoke/`）仍是最近一次**完整**
  三输入聚合：voice pass / search fail / llm fail（2026-10-01T11:57Z），
  provider-smoke 门的权威结论未变：**仍 blocked**。
- 本轮 search 单步真实 pass 与 M14-222（11041ms 超时 fail）、M14-223
  （30s 界内 0 结果 fail）的演进链一致：M14-223 把冒烟探针界从 10s
  对齐到 preflight 30s 后，本轮同一端点同一查询在 30s 界内返回 5 条
  合法结果——即「工具性假阴性已消除 + 上游劣化部分缓解」的首次真实
  通过。这是**单步证据**，聚合门不变。
- 本轮 llm 预检 `transport_error` 与 M14-222 时 llm `ready`（`/api/ps`
  200、模型驻留、export pass）相比是**环境回退**（Ollama 端点本轮不可
  达）；M14-218 预算修复（256→1024）与 M14-222 的 llm 真实 pass 结论
  不被推翻，但本轮无法复验——llm 单步证据 absent，聚合条件不满足。
- 本轮没有产生新的 `provider-smoke.json`，也没有修改任何旧聚合；后续
  聚合必须等 voice 与 llm 预检同时就绪后由显式切片以全新三输入执行。

## 4. 官方证据完整性（bytes + SHA-256）

目录 `<EV>` =
`artifacts/temp/provider-smoke/m14-244/20261006T102600582Z/`
（gitignored；同目录 `SHA256SUMS` 索引下列全部文件，索引不含自身）：

| artifact（`<EV>` 相对路径） | bytes | SHA-256 |
|---|---:|---|
| `pre-run-metadata.json` | 610 | `e7e4da77e4e2685e79efe942e8d69e9124ae9706479dead0ccfe19ef3eaf0fb4` |
| `provider-smoke-preflight.stdout.json` | 2662 | `9354761adbb979748605abe6b5c709dba98960f002390571e456e39602e6b0f3` |
| `provider-smoke-preflight.stderr.log` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `provider-smoke-preflight.window.txt` | 70 | `93c64630d5eb239912b561e49d7aa528c48f6e3ec2e4722889098547dacef82a` |
| `search-smoke.json` | 303 | `25b4bb64d07ea1a96fc818e1d97c02ed18547eb7fb891b2630696a68f20203a1` |
| `search-smoke.stdout.json` | 465 | `b5c5cc1820b84fb7ede844a7618a0cee2aede8b4b843d1cd46631e62a06746d8` |
| `search-smoke.stderr.log` | 176 | `9034a0f751991eed476efeeabf5a744c0ae2ce38533ccd693a7f48f4e1021a24` |
| `search-smoke.window.txt` | 70 | `3dae76e3929abf86f65f3ddb296494d6a1ce7c9fb79e692dfbf1b50ea607c338` |
| `failure-closeout.json` | 2787 | `b2cca2134eeeb205620fb86da86c9e44f9ddb099aa15a27da47c5294022a2c0d` |

## 5. 验证与卫生

- 聚焦 provider-smoke 契约套件（canonical venv，cwd=`services/api`）：
  `test_provider_smoke_evidence.py` +
  `test_provider_smoke_preflight.py` + `test_smoke_search_script.py` +
  `test_smoke_voice_local_script.py` + `test_smoke_llm_script.py`
  → **202 passed**（M14-223 基线 202，本轮零代码变更精确吻合）。
- ruff：本轮零 Python 变更文件（`git diff --name-only` 仅 docs/），
  无可检查对象（如实记录为 0 个变更 .py 文件）；`compileall` 不适用
  同理。`git diff --check` → 干净。
- 新增 tracked 行卫生扫描：credential 赋值形态扫描仅 1 处命中——命令
  矩阵行的 `LLM_API_KEY` 变量名后接文档占位符（非真实值，非敏感）；
  Unicode replacement char（U+FFFD）0 命中；绝对本地盘符/家目录路径
  形态 0 命中。
- 单本地 commit，不 push、不开 PR、不合并。
