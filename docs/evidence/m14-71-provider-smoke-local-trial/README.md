# M14-71 本地真实 provider 冒烟闭环（provider smoke local trial）

## 结论

M14-71 在工作树 `ops/m14-71-provider-smoke-local-trial` 完成本地真实
provider 冒烟闭环：**search / local-voice / llm 三槽位全部真实执行并通过**，
聚合 `provider-smoke.json`（topology `voice_mode=local`）被
`release-readiness` 消费后 **provider-smoke 门禁 = pass**（其余门禁未在本
目录执行，release_ready 全局结论为 false——如实，不冒充）。

时间：2026-09-20 GMT+8。LLM 冒烟为冷启动（导出前 `ollama stop` 该别名，
无预热模型驻留）。

### 关键事实（诚实边界）

- **Ollama /v1 不可靠消费顶层 `options.num_ctx`**（OpenAI 规范外字段，
  兼容性不保证）：LLM_NUM_CTX 保持 provider 特定的可选请求 Hint 语义，
  注释/文档全部改为「兼容性不保证」措辞，不据此声称生效。M14-71 本地
  路径 **LLM_NUM_CTX 未设置**（payload 不带 options）。
- **本地可靠路径 = 仓库所有固定模型别名** `aios-qwen3.5-9b-4096`
  （FROM qwen3.5:9b + 模型层 PARAMETER num_ctx 4096），由
  `infra/provision_ollama_model.ps1` 幂等、fail-closed 供给（本机已执行：
  create 成功 + 幂等重跑 PASS + `ollama show --parameters` 确认
  num_ctx 4096；上下文随模型定义，不依赖请求 Hint 或预热）。
- **qwen3:4b 已知不可用**，供给脚本显式拒绝其为 base（不修复、不下载）。
- **thinking 模型空响应防护**：qwen3.5 经 /v1 将推理写入独立 reasoning
  字段，max_tokens 不足时 content 为空——smoke_llm.sh 新增空 content 守卫
  （0 字节/thinking-only 不算通过），简单探针 max_tokens=2048，rubric
  judge 走 gateway 默认 1024。
- **生产容器/服务零触碰**：仅对本机 Ollama HTTP API 与既有本地服务
  （SearXNG 8878 / FunASR 8010 / CosyVoice 8011）做匿名 HTTP 探测；
  未读取/打印任何密钥，证据文件不含 endpoint 之外的敏感细节。

### 冒烟结果（真实执行）

- **search**：SearXNG 真实端点，`provider=cloud-web results=5`，
  duration 3650ms → pass。
- **local-voice**：FunASR 真实 ASR（latency 245ms，transcript 42 bytes）+
  CosyVoice 真实 TTS（latency 3503ms，RIFF WAV 226,604 bytes）→ pass。
- **llm**：`aios-qwen3.5-9b-4096` 冷启动：简单补全 content 12 chars
  （空 content 守卫通过）+ rubric judge 结构化 JSON 判定
  `achieved=[True, True] confidence=1.0`，duration 16171ms → pass。
- **聚合**：`provider-smoke-aggregate --voice-mode local`，
  voice/search/llm 三 providers 均 executed=true result=pass。
- **消费**：`release-readiness --evidence-dir <本目录>` 中
  gate `provider-smoke` **status=pass**（reason 如实记录 local 拓扑）。

## 证据文件与完整性锚点

源证据（gitignored，不入库；位于工作树
`artifacts/ops/m14-71-provider-smoke-local-trial/`）：**5 文件**。
此处转录 sha256 / bytes 锚点：

```
f15db9742ca2d7a2a89a86ac16a1d34649ea59ca0b3855cc3339af2a58b6f8ab  300   llm-smoke.json
a509be4fb95bcec3a08531087248455ca0d4b47a9aaf9234ab816047de698d6a  307   local-voice-smoke.json
5f3befb8adb707c53ab9875a45a7ee1be7fc415d08bf4f46142748bd6ca6c996  564   provider-smoke.json
732693596fcf4749003fb40466389ccf2bc74e411c7aa61ff62347f299dbb52d  6981  release-readiness.json
68686e7a881dbf26318129e16cf7f6c966a4dff19a68e684f06990cd653af152  302   search-smoke.json
```

四个 provider-smoke 证据文件均由本仓库工具生成
（`provider-smoke-export` / `provider-smoke-aggregate` /
`release-readiness`），schema `provider-smoke-evidence-v1` / release
readiness manifest；均为脱敏结果文件，不含 key/query/audio 路径。

## 范围边界（不冒充的部分）

- 本证据只覆盖**本地** provider 冒烟轨道（voice 拓扑 local）；公网语音
  仍受 turn-tls 可选门约束，cloud 语音冒烟未在本任务执行。
- `release-readiness.json` 中其余 9 个门（ci-main、release-check、
  production-preflight、backup-restore、audit-chain-anchor、
  legacy-papers、draft-ownership、release-approval、turn-tls）在本目录
  无证据文件，状态 missing——`release_ready=false` 是如实结论。
- 生产替换/切换不在本里程碑范围；生产容器与 env 全程零触碰。
