# M14-222 证据：current-main provider-smoke 新鲜刷新（preflight blocked；search 真实 fail；llm 真实 pass；voice/aggregate 诚实未执行）

## 0. 结论与边界

- 本切片在 current main `1971f2b742fca1ee94219f1b3c79b1fddd6e0e1b`（PR #309
  merge）上做 M14-218 第 5 节预告的真实重跑：全新 preflight → 按预检就绪
  状态选择性执行 provider-smoke export → 聚合（未达条件则诚实不执行）。
- 本轮诚实结果：preflight **overall blocked**（voice `not_ready`/
  `endpoint_absent`；search/llm `ready`）→ `provider-smoke-export search`
  真实执行 **fail**（exit 1，单次未重试）；`provider-smoke-export llm`
  真实执行 **pass**（exit 0，M14-218 预算修复后在真实端点的首次验证）；
  `provider-smoke-export local-voice` **未执行**（预检 voice not_ready，
  任务边界禁止启动 ASR/TTS/WSL/voice 服务）；
  `provider-smoke-aggregate --voice-mode local` **未执行**（聚合契约要求
  恰好三份单步证据输入，local-voice 单步证据因预检 not_ready 未产生，
  两份输入不构成可执行聚合形态）。
- 未修改任何 smoke 脚本/工具源码；未复用任何旧 JSON；未重试把失败修成
  通过；未手工编辑任何 gate JSON。
- 本证据只描述本轮 provider-smoke 执行窗口，不表示 preflight 语义扩大、
  不表示 release readiness / release approval；`production_ready=false`、
  `release_ready=false`、`public_ready=false` 边界全部不变。provider-smoke
  门对 release-readiness 的当前权威结论仍由后续聚合切片产生；本轮没有
  新的 `provider-smoke.json`，M14-209 聚合仍是最近一次完整聚合。
- 未读取或输出任何 secret/token/key/password 值（LLM key 为本地 no-auth
  路径的非敏感非空占位符，值不入库）；未触碰生产 DB 或生产状态；未
  stop/start/recreate 任何 Docker/WSL/Ollama/ASR/TTS/voice 服务；真实
  provider 请求只经仓库 smoke 工具发出。

## 1. 执行环境与调用形状

- base：`1971f2b742fca1ee94219f1b3c79b1fddd6e0e1b`（PR #309 merge）。从
  m14-221 worktree（tracked-clean）经 SSH
  `git fetch git@github.com:swsgbl/ai-learning-os.git main` 取得，
  `FETCH_HEAD` 与要求精确一致；新 worktree
  `ai-learning-os-worktrees/m14-222-current-main-provider-smoke-refresh`、
  分支 `ops/m14-222-current-main-provider-smoke-refresh` 建于该提交，
  执行前 porcelain 为空（`pre-run-metadata.json`）。
- 证据目录（gitignored）：
  `artifacts/temp/provider-smoke/m14-222/20261003T002318298Z/`，建于执行前；
  本 README 表 2 的全部工件都在该目录且未离开过 gitignored 区域。
- 解释器：主 checkout canonical venv `python.exe`（Python 3.11.15）；本
  worktree 无 `.venv`，冒烟 wrapper 经非敏感 `PYTHON` 环境变量名接收该
  解释器路径（值不写入本 README）。exporter 子进程经 PATH 解析到
  Git Bash（本机 Git for Windows 自带 bash，非 WSL bash），故 smoke
  wrapper 的 Windows 解释器选择链直接生效、无需 WSL 桥接。
- 端点全部使用仓库文档化默认 loopback 值（`provider_smoke_preflight.py`
  `DEFAULT_*_ENDPOINT`，与 `docs/evidence/m14-112-provider-smoke-preflight/`
  一致）：search `http://127.0.0.1:8878`、ASR `http://127.0.0.1:8010/v1`、
  TTS `http://127.0.0.1:8011/v1`、LLM `http://127.0.0.1:11434/v1`，LLM
  模型别名 `aios-qwen3.5-9b-4096`。search 冒烟按脚本契约显式注入
  `SEARCH_CLOUD_ENDPOINT`（上述 loopback 值，无鉴权路径不设 key）；LLM
  冒烟注入 `LLM_ENDPOINT`/`LLM_MODEL` 与非敏感非空 key 占位符，预算/
  超时保持脚本默认（`LLM_SMOKE_MAX_TOKENS` 未设置 → M14-218 默认 1024；
  `LLM_TIMEOUT_SECONDS` 未设置 → 默认 30s）。
- 官方命令形状（cwd=本 worktree `services/api/`，省略非敏感环境赋值）：

```bash
<canonical-python> -m app.ops.cli provider-smoke-preflight --voice-mode local --json
<canonical-python> -m app.ops.cli provider-smoke-export search --output <EV>/search-smoke.json --json
<canonical-python> -m app.ops.cli provider-smoke-export local-voice --output <EV>/local-voice-smoke.json --json   # 未执行（见 0/2）
<canonical-python> -m app.ops.cli provider-smoke-export llm --output <EV>/llm-smoke.json --json
<canonical-python> -m app.ops.cli provider-smoke-aggregate --search <EV>/search-smoke.json --voice <EV>/local-voice-smoke.json --llm <EV>/llm-smoke.json --voice-mode local --output <EV>/provider-smoke.json --json   # 未执行（见 0/2）
```

`<EV>` = 上文新 UTC 证据目录（绝对路径传入）。

## 2. 官方执行结果（live 结果矩阵）

| 命令 | exit | result | started_at (UTC) | completed_at/generated_at (UTC) | duration_ms | 关键事实 |
|---|---:|---|---|---|---:|---|
| `provider-smoke-preflight --voice-mode local --json` | 1 | overall `blocked` | `2026-10-03T00:23:46.427Z`（shell 窗口起） | `2026-10-03T00:24:04.545153+00:00` | 18186（shell 窗口） | voice `not_ready`/`endpoint_absent`（ASR/TTS health `http_status=null`）；search `ready`（200，results=8，上游 brave/duckduckgo/google cse/wikidata unresponsive 但端点按契约就绪）；llm `ready`（`/api/ps` 200，`aios-qwen3.5-9b-4096` 驻留）；ambient proxy pressure=false；`production_ready=false`/`release_readiness_evidence=false` 恒定 |
| `provider-smoke-export search` | 1 | fail | `2026-10-03T00:25:56.444333+00:00` | `2026-10-03T00:26:07.485622+00:00` | 11041 | 真实 SearXNG-compatible 请求失败，脚本固定脱敏文案「cloud-web 搜索不可用：cloud-web 搜索请求失败（网络错误或超时）」；单次执行未重试 |
| `provider-smoke-export local-voice` | 不适用 | **未执行** | 不适用 | 不适用 | 不适用 | 预检 voice `not_ready`/`endpoint_absent`；任务边界禁止启动/重启 ASR/TTS/WSL/voice 服务（blocked 原因与证据见 `failure-closeout.json` 与 preflight JSON） |
| `provider-smoke-export llm` | 0 | pass | `2026-10-03T00:27:52.560733+00:00` | `2026-10-03T00:28:05.603953+00:00` | 13043 | 简单探针非空正文（12 chars）+ rubric judge `achieved=[True, True]`/`confidence=1.0`，全脚本检查 PASS；默认预算 1024（M14-218）下无 thinking-only 误判——该修复在真实端点的首次验证；单次执行未重试、未调参 |
| `provider-smoke-aggregate --voice-mode local` | 不适用 | **未执行** | 不适用 | 不适用 | 不适用 | 聚合契约要求恰好三份单步证据输入；local-voice 证据未产生，两份输入不构成可执行形态，不伪造聚合 |

编排层两次调用笔误的诚实记录（均非 provider 失败重试，详见
`failure-closeout.json` orchestration_notes）：

1. search 第一次 shell 调用因输出重定向路径少一级 `../` 被 bash 拒绝，
   命令未执行、未发出任何 provider 请求；修正重定向后执行表内唯一一次
   真实请求。
2. llm export 的 `--output` 因调用方构造绝对路径时子 shell `cd` 少上
   一级，被工具写到 `<repo>/services/artifacts/temp/...`；工具行为与
   真实请求正常，证据文件字节原样迁移至 `<EV>`（迁移前后 SHA256 一致
   `675a18e3…`），误建目录已删除（tracked 树不受影响，artifacts 全程
   gitignored）。

## 3. 与既有证据的关系（不搬运旧结论）

- M14-209（`docs/evidence/m14-209-provider-smoke/`）仍是最近一次**完整**
  三输入聚合：voice pass / search fail / llm fail（2026-10-01T11:57Z）。
  本轮 llm 单步在真实端点 pass 与 M14-209 llm fail 的差异由 M14-218
  默认预算修复（256→1024）解释——M14-218 当时只改脚本与离线契约，
  未做真实请求，本轮是该修复的首次真实端点验证。
- 本轮 search 真实 fail 与 M14-207/M14-209 以来 search 的外部上游/
  网络根因口径一致：预检端点 ready（200/8 结果）不等于冒烟脚本端到端
  pass；本轮未对 search 做任何重试或环境修复。
- 本轮没有产生新的 `provider-smoke.json`，也没有修改任何旧聚合；后续
  聚合必须等 voice 预检就绪后由显式切片以全新三输入执行。

## 4. 官方证据完整性（bytes + SHA-256）

目录 `<EV>` = `artifacts/temp/provider-smoke/m14-222/20261003T002318298Z/`
（gitignored；同目录 `SHA256SUMS` 索引下列全部文件，索引不含自身）：

| artifact（`<EV>` 相对路径） | bytes | SHA-256 |
|---|---:|---|
| `pre-run-metadata.json` | 442 | `5cded7047fde41cbe23310acd05c821b6edd0c352c51943f3cedf9eaaac1d52e` |
| `provider-smoke-preflight.stdout.json` | 2758 | `831071118614515ded031a9504c6b889043c5aa11e19d964a11822f885cc2368` |
| `provider-smoke-preflight.stderr.log` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `provider-smoke-preflight.window.txt` | 69 | `742aa38061832c8e97f63798b337eb77b9589823d217abcd7458bd20a38145cd` |
| `search-smoke.json` | 303 | `50071eb142684b3795e057748d93165c7f80d68d1593b491ec1806a9e08bd5e0` |
| `search-smoke.stdout.json` | 315 | `542af5b464e8591055df2bdcc096cfd3c0b9958e3a9d7d599d3ba616cd720e50` |
| `search-smoke.stderr.log` | 199 | `0926fd892cbf54b75f8c250efea6ae220751915ca49adc57fbe37908ab7eb0f3` |
| `search-smoke.window.txt` | 69 | `4ec86d07ff0d48a146a7b8a9ff7eda7d93aefb6e57831a2f2415c388a812bc71` |
| `llm-smoke.json` | 300 | `675a18e3618b83f76917b50be178f266d527e3d7ba292811b002b8acd6703c34` |
| `llm-smoke.stdout.json` | 532 | `99d1728f28a48bfb5fec351c1d4f3d51fb3e609b6521020913966f7298283dba` |
| `llm-smoke.stderr.log` | 187 | `3c6ec513e2b8a573eccb2ff39a5974e64c35bf52afa71c2649feadb11020caa3` |
| `llm-smoke.window.txt` | 69 | `e774e580b4c186279216c09eb5f432f8dfe7fe5ce521fc84b5693a1fda1e8984` |
| `failure-closeout.json` | 2424 | `c19265c3294cb1836b7854b50a5f53909866b398024d5a70290dfea8321edfe6` |

两份工具导出的单步证据（`search-smoke.json`/`llm-smoke.json`）为本轮
工具原子写入原样字节（llm 证据目录迁移前后 SHA256 一致）；`*.stdout.json`
为对应命令 `--json` stdout 归档，`*.stderr.log` 为脱敏 stderr，
`*.window.txt` 为调用方记录的 shell 起止/exit 窗口。

## 5. 验证与收口

- 聚焦离线契约测试（canonical venv，basetemp 置于仓库外，不重放 live
  provider）：`test_provider_smoke_evidence.py`、
  `test_provider_smoke_preflight.py`、`test_smoke_search_script.py`、
  `test_smoke_voice_local_script.py`、`test_smoke_llm_script.py` →
  **201 passed**，2.94s，exit 0。
- `git diff --check` 通过（无空白错误）；新增 tracked 行 secret/本地路径/
  U+FFFD 扫描见收口记录（仅非敏感 loopback 端点与工具名，无凭据类值）。
- 本切片只新增本 README 并更新三本台账；证据原始文件保持 gitignored。
- 不 push、不开 PR、不合并；单本地 commit 基于 `1971f2b7`。voice 预检
  就绪后的完整三输入重跑聚合属于后续显式切片，不得由本证据推导。
