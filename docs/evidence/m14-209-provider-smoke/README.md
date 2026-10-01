# M14-209 证据：provider smoke 新鲜执行与聚合（local voice 通过，search/LLM 失败）

## 0. 结论与边界

- 本切片只新增本 README，原始 JSON 与脱敏日志留在 gitignored
  `artifacts/temp/provider-smoke/m14-209/20261001T092342607Z/`。
- worktree：`ai-learning-os-worktrees/m14-209-provider-smoke-evidence`；branch
  `ops/m14-209-provider-smoke-evidence`；base
  `0647186f58bf58fc3982a8508abb16bf524f086b`。
- 四条 provider-smoke 命令均在新的 UTC 证据目录中执行；没有复用 M14-208
  preflight、M14-148 证据或任何旧 aggregate，也没有手写/修改 gate JSON。
- 本轮结论：`voice=pass`，`search=fail`，`llm=fail`；聚合 exit 1，
  provider-smoke 门证据如实为失败状态。
- 本证据只描述 provider-smoke 执行窗口，不扩大为 preflight 通过语义，不表示
  release readiness / release approval，`production_ready=false`。
- 未修改生产 env，未 stop/restart/recreate 容器，未改变 Ollama 或任何用户
  进程生命周期；仅运行真实 smoke 探针并读取其结果。

## 1. 执行环境与调用形状

- cwd：本 worktree 的 `services/api/`。
- interpreter：主 checkout canonical venv 的 `python.exe`（Python 3.11.15）。
  本 worktree 不含 `.venv`，因此 smoke wrapper 通过非敏感的 `PYTHON`
  环境变量名接收该解释器路径。
- exporter 在 Windows 上经 WSL bash 调 wrapper，wrapper 再启动 Windows
  Python；所需变量名通过 `WSLENV` 桥接，值均不写入本 README。
- smoke 所需变量只按名称外部注入：search endpoint/query/key 槽位、LLM
  endpoint/key/model/timeout/output-budget 槽位，以及显式 ASR 音频路径。
  LLM key 是本地 no-auth 路径所需的非敏感非空占位符；未输出真实凭据。
- local voice 使用 canonical gitignored 官方中文 WAV 样例（334,138 bytes）。
  该输入是官方音频资产，不是旧 provider-smoke JSON；三份聚合输入 JSON 均由
  本轮在新目录生成。

官方命令形状如下（省略所有环境赋值）：

```powershell
<canonical-python> -m app.ops.cli provider-smoke-export search --output <EV>/search-smoke.json --json
<canonical-python> -m app.ops.cli provider-smoke-export local-voice --output <EV>/local-voice-smoke.json --json
<canonical-python> -m app.ops.cli provider-smoke-export llm --output <EV>/llm-smoke.json --json
<canonical-python> -m app.ops.cli provider-smoke-aggregate --search <EV>/search-smoke.json --voice <EV>/local-voice-smoke.json --llm <EV>/llm-smoke.json --voice-mode local --output <EV>/provider-smoke.json --json
```

`<EV>` 是上文新 UTC 目录。search 的 attempt 1/2 分别因 endpoint/query 变量
未跨 WSL/Windows 边界到达 wrapper 而失败，均未发出 provider 请求；两份失败
JSON 与日志保留为 `search-smoke-attempt1*` / `search-smoke-attempt2*`。表中
search 采用已进入真实请求的 attempt 3 产物，文件名保持契约要求的
`search-smoke.json`。

## 2. 官方执行结果

| 命令 | exit | result | started_at (UTC) | completed_at/generated_at (UTC) | duration_ms | 关键事实 |
|---|---:|---|---|---|---:|---|
| `provider-smoke-export search` | 1 | fail | `2026-10-01T11:55:13.561645+00:00` | `2026-10-01T11:55:24.254975+00:00` | 10693 | 真实 cloud-web 请求失败，脚本固定脱敏文案为网络错误或超时 |
| `provider-smoke-export local-voice` | 0 | pass | `2026-10-01T11:55:47.368094+00:00` | `2026-10-01T11:56:35.430646+00:00` | 48062 | ASR health 200、非空中文转写；TTS health 200、RIFF/WAV 音频非空 |
| `provider-smoke-export llm` | 1 | fail | `2026-10-01T11:56:48.418147+00:00` | `2026-10-01T11:56:52.868867+00:00` | 4450 | 模型响应为空 content；thinking-only/0 字节按脚本判 fail，未重试加预算 |
| `provider-smoke-aggregate` | 1 | fail | 不适用（聚合 schema 无该字段） | `2026-10-01T11:57:12.307728+00:00` | 不适用（聚合 schema 无该字段） | 三份输入均 executed；任一 fail 则聚合 exit 1 |

local voice 的脚本事实：ASR `provider=local-funasr`、2981ms、转写 42 UTF-8
bytes；TTS `provider=local-cosyvoice`、44771ms、241,964 bytes、RIFF/WAV=yes。
LLM attempt 只有一次；未因失败调整输出预算或重启模型。search 只在修正调用
边界后执行一次真实请求，未对 provider 失败做重试。

聚合输出拓扑为 `voice_mode=local`：

| provider | executed | result | evidence_step |
|---|---|---|---|
| voice | true | pass | `local-voice-smoke` |
| search | true | fail | `search-smoke` |
| llm | true | fail | `llm-smoke` |

## 3. 官方证据完整性

| artifact（`<EV>` 相对路径） | bytes | SHA-256 |
|---|---:|---|
| `search-smoke.json` | 303 | `464A64A98D64A48CFF1951BF25F67B3C2E09930D514A2D2C323D517C74B4F584` |
| `local-voice-smoke.json` | 308 | `9A42D23D81213362E5C5127805AB73D67A3D204A54BC30ABCDD7D6B6E1C02190` |
| `llm-smoke.json` | 299 | `333D4A5D940D8A69D07E4FC7FF1D50C6C99462A1F38FC8C98DD6D0E72C62B36B` |
| `provider-smoke.json` | 564 | `1C8C395970E052DA518C3196236F05E65EF659C691818C2DB44BC5220473756B` |

三份 aggregate 输入的 `started_at` 均晚于本目录创建时间，且都由本工具写入
新目录；`provider-smoke.json` 仅消费上述三份新输入。原始 JSON 与日志保持
工具输出原样，未手工编辑。

## 4. 验证与收口

- 聚焦测试（canonical venv，全部离线，不重放 live provider）：
  `services/api/tests/test_provider_smoke_evidence.py`、
  `test_smoke_search_script.py`、`test_smoke_voice_local_script.py`、
  `test_smoke_llm_script.py` → **145 passed**，5.89s。
- `git diff --check` 与新增行敏感赋值/代理值扫描见 commit 前收口；本 README
  不包含环境变量值、secret、token 或代理值。
- 本切片不做 push、PR 或合并；失败 provider 的恢复/重跑属于后续显式任务，
  不得由本证据自动推导。
