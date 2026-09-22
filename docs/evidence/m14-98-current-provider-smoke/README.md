# M14-98 证据:current-main provider-smoke 门刷新(search/local-voice 真实通过,llm 本地资源边界失败)

- 切片类型:证据刷新切片(真实重跑 + 诊断 + 文档;零应用代码/配置/工具改动)
- worktree:`m14-98-current-provider-smoke`(repo 同级 worktrees 目录),分支
  `ops/m14-98-current-provider-smoke`,基于 main
  `841b36f48378108a1dd51d39896922ab58c1eb82`(PR #185 merge = M14-95 合入,
  精确基点,fetch 后 origin/main 全 SHA 复核一致)
- 交付形态:单 local commit,不 push、不建 PR
- 本 README 为唯一入库证据文件;原始证据(gitignored)位于 worktree
  `.verify/artifacts/m14-98-current-provider-smoke/`(13 文件 + 工具生成的
  SHA256SUMS.txt 索引,自不含自哈希;§5)
- 结论先行:**provider-smoke 门本轮未通过**——search 与 local-voice 两步真实
  PASS,llm 两跑真实 FAIL(本地 GPU 资源/30s gateway 超时边界);按纪律
  **未运行 provider-smoke-aggregate**(无 provider-smoke.json);本切片不刷新
  ci-main/release-check,**不宣称 release_ready/production_ready**。

## 1. 目标与背景

M14-88(2026-09-21)恢复了本地 provider-smoke 三件套并产出当时证据。main 已前移
(M14-89/90/91/92/93/94/95/96/97 相继合入,含真实代码与测试集变更),易失门
provider-smoke 需对 current main 重新真实执行。本切片目标:在精确基点
`841b36f` 的独立 worktree 从零重建环境,以仓库既有工具(provider-smoke-export /
provider-smoke-aggregate)真实重跑三件套并产出**当前**证据;绝不复用 M14-88
JSON 冒充当前、绝不手改 gate JSON、绝不合成 pass。任何 provider 步真实失败
即停止并如实记录——本切片正是以 llm 真实失败 + 停止收尾。

## 2. 环境(全部真实,当前执行)

- worktree 从零 `uv venv .venv --python 3.12`(CPython 3.12.14)+
  `uv pip install -r services/api/requirements.txt -r services/api/requirements-dev.txt`;
  canonical `.venv` 零触碰。
- 调用形态:`services/api` cwd 下
  `<worktree>/.venv/Scripts/python.exe -m app.ops.cli provider-smoke-export <provider>
  --output <EV>/<file>`;冒烟脚本经 `PYTHON=.venv/Scripts/python.exe`(相对仓库根)
  复用同一 worktree venv;stdout/stderr 全程 tee 留存、exit code 与起止时间戳
  逐条落盘(`stdout-*.txt` / `window-*.txt`)。
- 端点可用性预检(执行窗口内只读复核,详见 `precheck-endpoints.txt`):生产
  SearXNG `127.0.0.1:8878` HTTP 200;FunASR `127.0.0.1:8010/health` 与 CosyVoice
  `127.0.0.1:8011/health` HTTP 200;Ollama `127.0.0.1:11434/api/tags` HTTP 200
  且别名 `aios-qwen3.5-9b-4096:latest` 在场;回环代理 7892 LISTENING;canonical
  gitignored ASR 样例 `<canonical>/artifacts/voice/smoke/asr_sample_zh.wav`
  在场(334138 bytes)。零服务生命周期变更。

## 3. 真实执行结果(2026-09-22T16:54:20Z–17:07:08Z,本地 2026-09-23 00:54–01:07 GMT+8)

| 步 | 结果 | 关键事实(工具自身输出) |
|---|---|---|
| search(`provider-smoke-export search`,`SEARCH_CLOUD_ENDPOINT=http://127.0.0.1:8878`) | **pass**,exit 0,duration 6449ms | `provider=cloud-web results=5 query_len=21`,authorities=community(当前真实返回;M14-88 时为 openai.com 等——非旧证据重用的直接佐证);started 16:54:2xZ,completed `2026-09-22T16:54:29.521979+00:00` |
| local-voice(`provider-smoke-export local-voice`,`ASR_SMOKE_AUDIO=<canonical>/artifacts/voice/smoke/asr_sample_zh.wav`,ASR/TTS 用既有本地端点默认值) | **pass**,exit 0,duration 97934ms | ASR `provider=local-funasr latency_ms=5102 transcript_bytes=42`(真实中文转写);TTS `provider=local-cosyvoice latency_ms=92410 audio_bytes=218924 riff_wav=yes`;`asr=PASS tts=PASS`;completed `2026-09-22T16:56:25.952776+00:00`(TTS 显著慢于 M14-88 的 3166ms——GPU 争抢先兆,如实记录) |
| llm attempt1(`provider-smoke-export llm`,`LLM_ENDPOINT=http://127.0.0.1:11434/v1`,`LLM_MODEL=aios-qwen3.5-9b-4096`,`LLM_API_KEY=<非敏感占位>`) | **fail**,exit 1,duration 32402ms | `httpx.ReadTimeout`:首跑触发 9B 模型冷加载(>300s,见 §4),gateway 默认 timeout 30s;traceback 含 httpcore http_proxy 帧(当次 shell 以参数展开形态注入 NO_PROXY 传递异常,复跑改字面量后帧消失);失败证据如实落盘(留档 `llm-smoke-attempt1-fail.json`/`stdout-llm-attempt1.txt`);completed `2026-09-22T16:57:18.733641+00:00` |
| llm attempt2(同命令,NO_PROXY/no_proxy 以字面量 `127.0.0.1,localhost` 注入直连;此时模型已驻留 VRAM) | **fail**,exit 1,duration 30506ms | 纯直连(traceback 无代理帧)仍 ~30s ReadTimeout:max_tokens=2048 的 thinking 模型生成在 GPU 100% 争抢下超 30s;warm 旁证:max_tokens=8 直连 curl HTTP 200/6.4s;失败证据如实落盘;completed `2026-09-22T17:07:08.164927+00:00` |
| 聚合(`provider-smoke-aggregate --voice-mode local`) | **未运行** | provider 链未全 pass,按任务纪律停止;不产出 provider-smoke.json,不伪造门通过 |

四个证据 JSON 均为仓库工具程序化生成(schema `provider-smoke-evidence-v1`,
九键白名单),无任何手改;失败 JSON 与成功 JSON 同等留存。

## 4. llm 失败诊断(本地资源/超时边界;详见 `diagnosis-llm-boundary.md`)

1. **冷加载**:attempt1 时模型未驻留;16GB RTX 5070 Ti 上 Ollama(llama-server)
   与语音服务(3× uv python 进程)、训练负载、VMware mksSandbox 共存,
   15884/16303 MiB、util 100%,模型加载 >300s(旁证:窗口内 max_tokens=8 直连
   curl 300s 超时 http=000)→ 30s gateway 超时必然先到。
2. **热模型仍超时**:~17:05Z 模型驻留(/api/ps size_vram=5490081790)后,
   max_tokens=8 探针 200/6.4s(响应含 reasoning 字段=thinking 模型);但冒烟
   第一步 `max_tokens=2048` 在 GPU 持续 100% 争抢下生成 >30s → attempt2
   ReadTimeout。吞吐多档复测探针被 supervisor 中止(避免额外 GPU 占用),
   每秒 token 数未获测量,如实记录为未完成测量。
3. **判定**:本地资源/超时边界,不是 Ollama 不可用(tags/ps/warm 探针均 200),
   不是仓库工具或脚本缺陷(判定逻辑零改动、失败如实落盘),非代理阻断
   (直连复跑同超时;attempt1 代理帧为当次 shell NO_PROXY 参数展开传递异常)。
4. **处置**:未重启/停止/重建 Ollama、语音、训练或 VMware 任何进程;未改系统
   代理/VPN;服务不可用/资源不足是 blocker,不是重启用户/生产进程的理由。
   缓解方向(超出本切片):GPU 空闲窗口重跑 llm 步后补跑 aggregate;或运维
   决策调大 gateway timeout/固定模型驻留策略——留 supervisor/运维决策。

## 5. 证据文件与完整性锚点(worktree `<EV>` = `.verify/artifacts/m14-98-current-provider-smoke/`,gitignored)

```
bf3772adf751dbc0d72631cc0702dd1b21a6cf7faf6bf0c1001322cd78b4a30c  diagnosis-llm-boundary.md
8aa3d215eb894b8ab6c4fd7131242ce9432df7f63bf528157c5fe70995608e87  llm-smoke-attempt1-fail.json
7c62f7a3cd3a7d165221beb0ded3e04fcd4d20ecde754561f8bc6db144409a7d  llm-smoke.json
3a0d3f4ec3625ccb0d7757873a14fb47637d377aec2db6527a9be0381caa66f0  local-voice-smoke.json
4ed1f73a59a8a3d2afdca77b8edeaba260a4f21724e415669667ba609e1b22b3  precheck-endpoints.txt
4ea73ee4293beb5eca0d9b67660b96d746fbc73f14c0c7de5b95984aa89fcb94  search-smoke.json
39603a408b063cab841166502e44fcc3eee4c36cb772d0b0072a241a58fd0e9d  stdout-llm-attempt1.txt
a2605df177439df49044a10a07785dd8e39430ac8d38d992ce034b3611dabe0c  stdout-llm.txt
2d91d6c541c134286b73cf8d6057953ff08f9d3ebd272ef493ef2734cc84ece6  stdout-local-voice.txt
f5e70e3b5edb821a22f45e957e153b5c4b09cd54f823216ca9002efa2fb246c5  stdout-search.txt
f5ff8910e738ed68602317e4be3668c009ae16a22018ab3c18e7574f528e9673  window-llm.txt
9bdf2b148ff920eb90f43d60a3df02d8b560ce0e4605827cac86ea581fb26daf  window-local-voice.txt
868639f3b8fd53f5928ad02d92fa10a2c8e3db0320ef202433ac8794a6efa75d  window-search.txt
```

另含 `SHA256SUMS.txt`(上述 13 文件索引,自不含自哈希)。`stdout-llm*.txt`
为工具原始输出留档(含当次 traceback 原文,其中出现的进程路径均为工具自身
打印,未含任何凭据);入库 README 与诊断/预检记录均不含 secret 与绝对私有路径。

## 6. 验证

- 聚焦契约测试(本切片证据链依赖的全部工具面,worktree venv,全部离线、
  不依赖 live provider):
  `test_provider_smoke_evidence.py + test_release_readiness.py +
  test_searxng_local_provider.py + test_smoke_search_script.py +
  test_smoke_voice_local_script.py + test_provision_ollama_model_script.py`
  → **235 passed**(4.61s)。
- `ruff check .`(services/api,worktree venv)→ All checks passed(本切片零
  Python 变更,ruff 为基线复核)。
- py_compile:本切片零 Python 文件变更,无触及范围(如实说明)。
- `git diff --check` → 干净。
- 生产容器(api/web/searxng/redis/postgres/livekit/minio)生命周期零变更;
  零 DB/MinIO/secret/env.production 接触;canonical 仓库与 `.venv` 零触碰。

## 7. 范围边界(不冒充的部分)

1. **本切片只刷新 provider-smoke 相关执行**:不刷新 ci-main/release-check 门
   (代码绑定门仍以 M14-97 于 `b374aa5` 的证据为最近刷新,对 `841b36f` 已
   stale),不以任何形式重新聚合 evidence-cockpit。
2. **provider-smoke 门本轮未通过**:llm 真实失败 + aggregate 未运行 +
   provider-smoke.json 缺位;`release_ready=false`/`production_ready=false`
   维持不变,发布审批 human-only。
3. **search/local-voice 的 pass 只代表执行窗口内本机拓扑状态**:SearXNG 出站
   依赖宿主回环代理 7892 有活进程;语音服务与 Ollama 均为用户级进程,窗口外
   状态不承诺(与 M14-88 §6 同口径的易失性边界)。
4. llm 失败的根因是**本地 GPU 资源争抢下的冷加载/生成超时**,不是 provider
   逻辑失败;但按纪律它就是本切片的真实 blocker,不以"环境原因"降级记录,
   也不通过重启任何进程"修复"。GPU 空闲窗口的重跑与 aggregate 补跑是后续
   运维动作。
5. 吞吐多档测量未完成(supervisor 中止探针以免 GPU 占用);本 README 数字
   (6.4s/8 tokens、>30s/2048 预算)为窗口内实测旁证,非基准测试结论。
