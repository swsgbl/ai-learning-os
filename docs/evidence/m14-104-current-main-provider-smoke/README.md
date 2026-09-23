# M14-104 证据:current-main provider-smoke 门刷新(local-voice 真实通过,search/llm 本机环境边界失败)

- 切片类型:证据刷新切片(真实重跑 + 诊断 + 文档;零应用代码/配置/工具改动)
- worktree:`m14-104-current-main-provider-smoke`(repo 同级 worktrees 目录),分支
  `ops/m14-104-current-main-provider-smoke`,基于 main
  `aa4bb55397dba7317f632cfae485867360b85d49`(PR #191 merge = M14-99 合入,
  精确基点)
- 交付形态:单 local commit,不 push、不建 PR
- 本 README 为唯一入库证据文件;原始证据(gitignored)位于 worktree
  `.verify/artifacts/m14-104-current-main-provider-smoke/`(28 文件 + 工具生成的
  SHA256SUMS 索引,自不含自哈希;§5)
- 结论先行:**provider-smoke 门本轮未通过**——local-voice 一步真实 PASS(第 2
  次尝试,重试理由 §3/§4),search 与 llm 两步真实 FAIL(均为本机外部环境边界,
  非仓库工具缺陷:search 上游引擎全数不可达、llm 端点进程未运行);按纪律
  **未运行 provider-smoke-aggregate**(无 provider-smoke.json);本切片不刷新
  ci-main/release-check,**不宣称 release_ready/production_ready**。

## 1. 目标与背景

M14-98(2026-09-22)对 `841b36f` 重跑 provider-smoke 三件套:search/local-voice
PASS、llm 两跑 FAIL(GPU 争抢下 thinking 模型 >30s 超时)。M14-100 已把该边界
修复为显式可配契约(`LLM_TIMEOUT_SECONDS` / `LLM_SMOKE_MAX_TOKENS` + gateway
loopback `trust_env=False`),但**未重跑任何 live 探针**——llm 步自 M14-98 起
一直处于未验证状态。main 已前移(PR #186/#187/#188/#189/#190/#191 合入),本
切片目标:在精确基点 `aa4bb55` 的独立 worktree 从零重建环境,以仓库既有工具
(provider-smoke-export)按 M14-98 同拓扑真实重跑三件套,llm 步显式套用 M14-100
控制(`LLM_TIMEOUT_SECONDS=300`、`LLM_SMOKE_MAX_TOKENS=256`);绝不复用
M14-88/M14-98 JSON 冒充当前、绝不手改 gate JSON、绝不合成 pass。任何 provider
步真实失败即停止并如实记录——本切片以 search/llm 真实失败 + aggregate 停止
收尾。

## 2. 环境(全部真实,当前执行)

- worktree 从零 `uv venv .venv --python 3.12`(CPython 3.12.14)+
  `uv pip install -r services/api/requirements.txt -r
  requirements-dev.txt`(本机 uv 配置走系统代理隧道失效,以
  `NO_PROXY='*'` 绕过代理直连清华镜像完成安装);canonical `.venv` 零触碰。
- 调用形态:`services/api` cwd 下
  `<worktree>/.venv/Scripts/python.exe -m app.ops.cli provider-smoke-export
  <provider> --output <绝对/正确相对路径>`;冒烟脚本经仓库根相对
  `.venv/Scripts/python.exe` 复用同一 worktree venv;stdout/stderr 全程留档、
  exit code 与起止时间戳逐条落盘。
- 运维注记(诚实记录,非 gate 内容变更):首轮三条 export 以
  `--output ../<EV>/…`(自 `services/api` 出发)调用,相对路径只回退一层、
  三个 gate JSON 实际落盘于 `services/.verify/…`(仍被 `.verify/` gitignore
  覆盖);发现后以 `mv` **字节原样**迁至 `<EV>/` 并删除误置树——三个 gate
  JSON 内容零编辑(哈希见 §5),无 .tmp 残留。
- 端点可用性预检(执行窗口内只读,详见 `precheck-endpoints.txt`):
  - SearXNG `127.0.0.1:8878` HTTP 200;
  - FunASR `127.0.0.1:8010/health` HTTP 200(`sensevoice` 已载);
  - CosyVoice `127.0.0.1:8011/health` HTTP 200(`Fun-CosyVoice3-0.5B-2512`);
  - **Ollama `127.0.0.1:11434` 连接被拒**(netstat 0 监听、tasklist 0 进程
    ——服务未运行);
  - GPU(只读 nvidia-smi):RTX 5070 Ti util 100%、15837/16303 MiB
    (VMware mksSandbox ×3 + python 训练进程占用);
  - 外网刻画:直连 `https://www.google.com` 超时(000)、
    `https://www.baidu.com` 200——境外网络不可达,VPN/代理进程未运行。
  - canonical gitignored ASR 样例
    `<canonical>/artifacts/voice/smoke/asr_sample_zh.wav` 在场(334138
    bytes)。零服务生命周期变更。

## 3. 真实执行结果(2026-09-23T00:37:12Z–00:49:05Z,本地 08:37–08:49 GMT+8)

| 步 | 结果 | 关键事实(工具自身输出) |
|---|---|---|
| search(`provider-smoke-export search`,`SEARCH_CLOUD_ENDPOINT=http://127.0.0.1:8878`) | **fail**,exit 1,duration 5407ms | `[smoke-search] FAIL: 0 条合法结果`;started `00:42:03.876210Z`,completed `00:42:09.283615Z`。只读旁证(00:43:23Z):SearXNG `/search?format=json` HTTP 200 但 `results=[]`、`unresponsive_engines` = brave/duckduckgo/google cse/wikidata/wikipedia 全部 `HTTP connection error`——服务在线、上游境外引擎不可达。无重试:全部引擎为境外源,外网不可达时重跑必然复现 |
| local-voice attempt1(`ASR_SMOKE_AUDIO=<canonical>/artifacts/voice/smoke/asr_sample_zh.wav`,ASR/TTS 既有本地端点默认值) | **fail**,exit 1,duration 4376ms | `asr health: 不可达`+`tts health: 不可达`,而前后 curl 直连两 `/health` 均 200——诊断为 Windows 注册表系统代理(`127.0.0.1:7892`,进程未运行)经 `urllib.getproxies()` 回退劫持 httpx `trust_env` 回环调用(httpx 默认 3/3 ConnectError 10061 vs `trust_env=False` 3/3 200;python 环境变量无任何代理项)。理由与复现全录 `local-voice-retry-rationale.md` |
| local-voice attempt2(同命令,调用环境显式注入 `NO_PROXY=127.0.0.1,localhost` 回环绕过——与 M14-66(smoke_search.sh)/M14-100(gateway loopback trust_env=False)同类既有控制,零代码/服务/判定变更) | **pass**,exit 0,duration 29284ms | ASR `provider=local-funasr latency_ms=8926 transcript_bytes=42`(真实中文转写);TTS `provider=local-cosyvoice latency_ms=19769 audio_bytes=226604 riff_wav=yes`;`asr=PASS tts=PASS`;started `00:48:04.158797Z`,completed `00:48:33.443440Z`(较 M14-98 的 97934ms 显著更快——本窗口 TTS 无重度 GPU 争抢)。attempt1 失败 JSON 被原子写覆盖,失败体原文留存于 `local-voice-stdout.txt` |
| llm attempt1(`LLM_ENDPOINT=http://127.0.0.1:11434/v1`,`LLM_MODEL=aios-qwen3.5-9b-4096`,`LLM_API_KEY=<非敏感占位>`,**M14-100 控制显式**:`LLM_TIMEOUT_SECONDS=300`+`LLM_SMOKE_MAX_TOKENS=256`,stdout 回显两覆写) | **fail**,exit 1,duration 2575ms | `[smoke-llm] FAIL: LLM 端点不可用: LLM 传输失败: ConnectError`;started `00:49:02.916428Z`,completed `00:49:05.492124Z`。前置只读复核对 `/api/ps` 亦连接失败(http_code=000)。**无第 2 次尝试**:重试前置条件是只读核验模型已驻留,而服务整体未运行、`/api/ps` 不可达,前置条件无法满足;启动 Ollama 为切片安全约束所禁 |
| 聚合(`provider-smoke-aggregate --voice-mode local`) | **未运行** | provider 链未全 pass,按任务纪律停止;不产出 provider-smoke.json,不伪造门通过 |

三个 gate JSON 均为仓库工具程序化生成(schema `provider-smoke-evidence-v1`,
九键白名单),无任何手改;失败 JSON 与成功 JSON 同等留存。

## 4. 失败诊断与重试纪律(详见 `diagnosis-boundaries.md`)

1. **llm——端点进程未运行**(与 M14-98 的"资源争抢超时"不同类):11434 无
   监听无进程,ConnectError 即服务缺席;M14-100 控制已正确生效(两覆写在探针
   前回显),无超时/预算问题可调。处置:单次真实尝试、失败证据落盘、不重试
   (前置条件不满足)、不启动/不重启任何用户进程(约束);GPU 100%/15.8GiB
   争抢背景下即便服务在场亦需空闲窗口(与 M14-98 §4 同口径)。
2. **search——上游引擎境外网络不可达**:SearXNG 自身 200,五引擎全
   `HTTP connection error`;宿主直连 google 超时/baidu 200。M14-98 时回环
   代理 7892 有活进程(其 PASS 的出站前提),本轮该代理未运行且约束禁止
   启动。处置:单次真实尝试、不重试(确定性复现)、不改引擎配置。
3. **local-voice attempt1——Windows 注册表代理劫持 httpx 回环**(新发现
   陷阱,与 M14-98 attempt1 的"环境变量代理帧"不同源):python 环境零代理
   变量,但 Windows 下 `urllib.request.getproxies()` 回退读注册表
   (ProxyServer=127.0.0.1:7892),httpx `trust_env=True` 客户端
   (smoke_local_voice.py 健康探针与 app/voice/providers.py 的
   AsyncClient——均未设 trust_env=False)回环请求被导向死代理 → 10061。
   同机 curl 不读注册表故 200。处置:attempt2 仅以 `NO_PROXY` 环境绕过
   (M14-66/M14-100 既有控制类,理由先录后跑);**未改任何代码**——本地
   语音探针的 loopback trust_env 加固留待后续实现切片评估。
4. **判定**:search/llm 失败均为本机外部环境边界(SearXNG 出站依赖、Ollama
   进程缺席),不是仓库工具或脚本缺陷(判定逻辑零改动、失败如实落盘);
   local-voice 的 pass 证明本机语音拓扑(8010/8011)与证据链真实可用。

## 5. 证据文件与完整性锚点(worktree `<EV>` = `.verify/artifacts/m14-104-current-main-provider-smoke/`,gitignored)

```
ac7ff2ab3b7c66077848638dc2b13d3ee7a80dfef69e02ba0f3265d00ec53726  diagnosis-boundaries.md        3038 B
362933ec322cb55afba4b88c53079885e819f5f94e6f2a3ac293b64f31ab9f27  llm-end-utc.txt                 21 B
cf205dbb8cea84897b488abcc281bf96698d5e94b1096b16657b4caba9082a22  llm-exit.txt                     7 B
94fe05713cc86ae116e958e4c940f5798ef114e7ffaace62cd7a48b0ddcad304  llm-pre-attempt-residency.txt 135 B
2d47df864f2673ebca1d12ed8bb1932f82b5b593e1dd9d0b0a841d3084d91014  llm-smoke.json                 299 B
bed566252e22bb411bb9e7d99dfb9bf9fadb1e5ced7565bbcb10c87f31b65c05  llm-start-utc.txt               21 B
7a6ea590e4a96bd7322aa023e6e72c61e4b978af65b697b7af0ce26d18b8ae90  llm-stderr.txt                 161 B
3594a05529dc40a1696a189761f005fef780d796885a6cbb0951ef9ed02c1ca8  llm-stdout.txt                 488 B
a49a3b5ff7b3eb48ec781f2631caeccadd950170a38115cc4f5348751702d725  local-voice-end-utc.txt         21 B
9832faf12e94f13b6cd0d1bef9ba4a777fb29389c0afd0d0a1bd3ed52d95e589  local-voice-end2-utc.txt        21 B
cf205dbb8cea84897b488abcc281bf96698d5e94b1096b16657b4caba9082a22  local-voice-exit.txt             7 B
19eaf43821a7660ec323a87c8457bf74823beb296c39f5e01aa8a683aa50f061  local-voice-exit2.txt           7 B
3d43052a6c020f73481ce266d67a9dc23f539d5750df6dc0c3d8e787beeed875  local-voice-retry-rationale.md 1333 B
af58729bb0e3434932c60467276b9c488da00e232945eff5cdef38730bc86845  local-voice-smoke.json         308 B
f5edb15a1304720aea5c3f16a39a86ab6fc1d0ebdc020d6abb30043ba980316c  local-voice-start-utc.txt       21 B
a7663e52b4608583ff746b3316c808662be361e5c81ded1c1e6eb05b9e277772  local-voice-start2-utc.txt      21 B
7e63bc82f8ac68b96ef83746187565174c9eb6296f78abc0ed490b11f0557fd4  local-voice-stderr.txt         192 B
926e0d186210b8c24c1da120a9479f463e748500267b5dbfd6d22375101d9699  local-voice-stderr2.txt        98 B
2091d83754dd052b908ab86b7727744332f58ec129e5d78ed974eacc29c2e829  local-voice-stdout.txt        558 B
2a125bfc41c7d77cc5cb6084d7877d14829e8aa8e6c92c3b8ca635e84e24a352  local-voice-stdout2.txt       849 B
fd8e847c1a1a0419c005b0e913c082fd29bd034edfb6877b7cdae71d14e28bca  precheck-endpoints.txt       2279 B
cc9ec4eed890c5596adeb9126a7f1df5eba78fa0393c1e84b9dbed818d44b3b9  precheck-start-utc.txt          21 B
a56a8784ec4367157e7b71dbb1a89a9268f8f7c3c919c343f913305424f09465  search-end-utc.txt              21 B
cf205dbb8cea84897b488abcc281bf96698d5e94b1096b16657b4caba9082a22  search-exit.txt                  7 B
5f352ec1b45bb244912da50a2343eb1c03e9d09e78030fa880229a6cedef759c  search-smoke.json             302 B
633d2edabe66a50410aca59fc60450cabe0fe675ebac528419fb8f2bed38d3ca  search-start-utc.txt            21 B
17fe518994b384e0b6dada05448b717e1693d59fedc0ba2090209ab1060a334e  search-stderr.txt             175 B
4f81a61678a73f0bff78d5898a60e54e69ba9bb599d3cd1e545abb414a85dbc7  search-stdout.txt             314 B
```

另含 `SHA256SUMS`(上述 28 文件索引,自不含自哈希)。stdout/stderr 为工具
原始输出留档;入库 README 与诊断/预检记录均不含 secret、真实凭据或绝对私有
路径(LLM_API_KEY 为非敏感占位符,未打印真实凭据)。

## 6. 验证(聚焦离线,worktree venv,不依赖 live provider)

- 聚焦契约测试(本切片证据链依赖的全部工具面):
  `services/api` 下
  `python -m pytest tests/test_provider_smoke_evidence.py
  tests/test_release_readiness.py tests/test_smoke_llm_script.py
  tests/test_smoke_search_script.py tests/test_smoke_voice_local_script.py
  tests/test_provision_ollama_model_script.py -q`
  → **226 passed**(5.14s)。
- `ruff check services/api`(worktree venv)→ All checks passed(本切片零
  Python 变更,ruff 为基线复核)。
- `git diff --check` → 干净;tracked 树仅 docs 四文件(README 新增 + 三台账
  更新),`.verify/` 全部 gitignored。
- 生产容器/DB/MinIO/secrets/release-approval/long-soak/deployment 零接触;
  Ollama/FunASR/CosyVoice/SearXNG/代理/VMware/计划任务零生命周期变更;
  canonical 仓库与 `.venv` 零触碰。

## 7. 范围边界(不冒充的部分)

1. **本切片只刷新 provider-smoke 相关执行**:不刷新 ci-main/release-check 门
   (代码绑定门仍以最近一次于更早基点的证据为最近刷新,对 `aa4bb55` 已
   stale),不重新聚合 evidence-cockpit。
2. **provider-smoke 门本轮未通过**:search/llm 真实失败 + aggregate 未运行 +
   provider-smoke.json 缺位;`release_ready=false`/`production_ready=false`
   维持不变,发布审批 human-only。
3. **local-voice 的 pass 只代表执行窗口内本机拓扑状态**:FunASR/CosyVoice
   均为用户级进程,窗口外状态不承诺;attempt2 依赖调用侧 `NO_PROXY` 回环
   绕过(注册表代理在场且未运行时必需)。
4. llm 失败根因是**端点进程缺席**(本轮),叠加 GPU 100%/15.8GiB 争抢背景
   (M14-98 实证的空闲窗口需求不变);不以"环境原因"降级记录,也不通过
   启动任何进程"修复"。search 失败根因是**SearXNG 上游境外网络不可达**
   (出站代理未运行)。两者的解除(启动 Ollama / 恢复出站网络 + GPU 空闲
   窗口重跑三件套或仅缺步、随后 aggregate)是后续运维动作,留 supervisor
   决策。
5. **新记录的工具面缺口**:本地语音探针与 app/voice/providers.py 的 httpx
   客户端未对 loopback 设 `trust_env=False`(M14-100 只加固了 LLM gateway),
   Windows 注册表代理可劫持其回环调用——本切片以环境绕过通过,代码加固
   留待后续实现切片(带离线契约测试)评估,不在证据切片内改代码。
6. M14-100 的 256 预算/300s 超时在本轮**未被实测**(端点缺席,2575ms 即
   ConnectError)——两者仍是"未实测折中",llm 真实重跑切片仍需实测校准。
