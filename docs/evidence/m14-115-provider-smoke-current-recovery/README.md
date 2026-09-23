# M14-115 证据:provider-smoke 当前生产恢复固化与 preflight search 超时缺陷修复

- 切片类型:恢复证据固化/缺陷修复/文档切片(ops 工具超时修正 + 契约测试 +
  证据文档;零生产触碰)
- worktree:`m14-115-provider-smoke-current-recovery`(repo 同级 worktrees
  目录),分支 `ops/m14-115-provider-smoke-current-recovery`,基于 main
  `cf0a512`(PR #201 merge = M14-114 China Bing 直连引擎合入,精确基点)
- 交付形态:单 local commit,不 push、不建 PR
- 本 README 为唯一入库证据文件;真实运行的原始证据(gitignored)位于
  worktree `.verify/artifacts/m14-115-provider-smoke-current-recovery/`
  (8 个 JSON + SHA256SUMS.txt 索引,§6)
- 结论先行:**supervisor 生产恢复(searxng 单容器 recreate + China Bing
  聚合恢复)之后,三个真实 provider 冒烟(voice/search/llm)全部 pass 并
  由 `provider-smoke-aggregate` 聚合为 provider-smoke.json 三项 pass**——
  本切片把该恢复与冒烟事实固化为入库证据,并修复同期暴露的 preflight
  工具缺陷(search 预检固定 10s 超时把真实上游聚合延迟 10.3s/12.6s/18.5s
  误报为 endpoint_timeout;search 档提高到 30s,voice/LLM 语义不变)。
  **本切片不声称 release_ready/production_ready**:provider-smoke 门当前
  证据已闭合,但 long-soak、审批链与生产就绪宣称不在本切片范围;
  `production_ready=false` 维持不变。

## 1. 目标与背景

provider-smoke 门(release-readiness)长期 blocked 于 search 上游引擎
不可达(M14-104/M14-112 归因:五引擎全 HTTP connection error)。supervisor
在 M14-114(China Bing 直连引擎,PR #201)合入后执行了一次生产恢复并完成
真实三步冒烟(全 pass)。本切片两件事:

1. 把该恢复与真实冒烟证据(含三次环境失败 attempt 与一次 LLM
   empty-content 失败 attempt——均如实保留)固化为入库 README + ledger
   条目,gitignored 原始 JSON 以 SHA256 锚定(§6);
2. 修复恢复期暴露的 preflight 缺陷:`provider-smoke-preflight`(M14-112)
   对三个 provider 用统一 10s 探测超时,而 search 预检是唯一触发**真实
   上游聚合**的预检——恢复后真实聚合延迟为 10.3s/12.6s/18.5s(§3),
   固定 10s 把慢聚合误报为 `endpoint_timeout`(voice ready、LLM ready、
   search blocked 的**工具性误报**)。修复:search 档 30s 保守覆盖,
   voice/LLM 维持 10s 不变(§4)。

## 2. supervisor 生产恢复事实(如实记录)

以下为 supervisor 恢复操作的既成事实(本切片未执行、未复现任何生产操作,
只固化记录):

1. **配置合入**:PR #201(China Bing 直连引擎)已合并 main
   `cf0a512`,CI run `35905222519` 5/5 success;SearXNG 配置启用
   China Bing `https://cn.bing.com` 直连引擎。
2. **生产变更范围(最小)**:只 recreate `searxng` 一个容器;API/Web/
   Postgres/Redis/MinIO/LiveKit 容器 ID 未变。新 searxng 容器 healthy,
   settings 挂载回到 canonical main 配置,出站代理为
   `socks5h host-gateway:7892`(代理细节不含任何 secret,本 README 不
   回显 secret)。
3. **手工真实查询(恢复验证)**:查询「Python asyncio tutorial」返回
   40 条(其中 bing 10 条),耗时 12664ms;查询「清华大学」返回 22 条,
   耗时 18502ms——聚合可用但延迟显著(10s 界必然误报,§4 缺陷实证)。
4. **preflight 当时状态**:`provider-smoke-preflight` 真实运行——voice
   ready、LLM ready/model resident、search 因 10s timeout 误报
   blocked(`endpoint_timeout`)。这是本切片修复的工具缺陷,不是
   provider 不就绪。
5. **search 前 3 次冒烟失败是调用环境问题,不是 provider 失败**:
   Windows→WSL 的 WSLENV 透传漏项(逐次补齐 SEARCH_SMOKE_* 变量:
   attempt1 缺环境变量、attempt2 缺查询参数、attempt3 缺端点——文件名
   envmiss/querymiss/endpointmiss);最终 WSLENV 完整透传后 pass,
   `results=5 query_len=21`。
6. **local voice 冒烟(真实执行)**:ASR latency 2682ms / transcript
   42 bytes;TTS latency 22490ms / audio 241964 bytes(RIFF WAV)。
7. **LLM 冒烟(真实执行,两次)**:第 1 次 256 tokens 返回 thinking-only
   空 content,脚本**正确 fail**(empty-content 不是 pass 证据);第 2 次
   仅把 `LLM_SMOKE_MAX_TOKENS` 提到 1024(timeout 仍 300s),正文 12
   chars,rubric achieved=[True,True] confidence=1.0。
8. **诚实边界**:本切片只闭合 provider-smoke 当前证据并修 preflight
   工具缺陷;不伪造 long-soak、审批或生产就绪——不声称
   release_ready/production_ready。

## 3. 真实 provider-smoke 执行与证据(2026-09-23T18:54Z–18:57Z)

全部 8 个 JSON 为 `provider-smoke-evidence-v1` schema 的工具生成产物,
时间线(UTC,来自各文件 started_at/completed_at):

| # | 文件 | 结果 | 耗时 | 说明 |
|---|---|---|---|---|
| 1 | search-smoke-attempt1-envmiss.json | fail exit 1 | 67ms | WSLENV 透传缺环境变量(§2.5) |
| 2 | search-smoke-attempt2-querymiss.json | fail exit 1 | 943ms | WSLENV 透传缺查询参数(§2.5) |
| 3 | search-smoke-attempt3-endpointmiss.json | fail exit 1 | 60ms | WSLENV 透传缺端点(§2.5) |
| 4 | search-smoke.json | **pass** exit 0 | **10342ms** | 完整透传后真实聚合:results=5 query_len=21 |
| 5 | local-voice-smoke.json | **pass** exit 0 | **25532ms** | ASR 2682ms/42 bytes;TTS 22490ms/241964 bytes RIFF WAV |
| 6 | llm-smoke-attempt1-empty-content.json | fail exit 1 | 2640ms | 256 tokens thinking-only 空 content,脚本正确 fail(§2.7) |
| 7 | llm-smoke.json | **pass** exit 0 | **11945ms** | MAX_TOKENS=1024、timeout 300s;正文 12 chars,rubric [True,True] conf=1.0 |
| 8 | provider-smoke.json | 三项 pass | — | aggregate(voice_mode=local)聚合,generated_at 18:57:57Z |

关键点:search 冒烟真实耗时 10342ms + 手工查询 12664ms/18502ms(§2.3)
构成三个 >10s 的真实聚合延迟样本——正是 §4 缺陷的直接实证。失败
attempt 不删除、不美化:它们是环境调试与脚本 fail-closed 正确性的
证据(尤其 #6:empty-content fail 证明冒烟门不会被 thinking-only 输出
糊弄)。

## 4. preflight search 超时缺陷与修复(本切片代码变更)

- **缺陷**:`provider_smoke_preflight.py` 用单一
  `PROBE_TIMEOUT_SECONDS = 10.0` 覆盖全部探测;search 预检
  (`/search?format=json`)是唯一触发真实上游聚合的探测,China Bing
  聚合延迟实测 10.3s/12.6s/18.5s > 10s → `endpoint_timeout` 误报
  (provider 实际就绪)。
- **修复(最小改动,常量+测试,不新增配置面)**:
  - 新常量 `SEARCH_PROBE_TIMEOUT_SECONDS = 30.0`(search 档;30s 保守
    覆盖观测到的最大聚合延迟 18.5s 仍有界);
  - `_probe_json` 增加 keyword-only `timeout_seconds` 参数(默认
    `PROBE_TIMEOUT_SECONDS`,voice/LLM 调用点零改动、语义不变);
  - `_check_search` 传 `SEARCH_PROBE_TIMEOUT_SECONDS`,报告 search 槽位
    新增 `probe_timeout_seconds` 字段透出实际探测界(报告透明,
    endpoint_timeout 归因可检视;与既有 `probe_trust_env` 同风格);
  - 两档均固定常量、不开放 CLI/环境覆写(有界性是契约的一部分,
    M14-112 既有原则)。
- **文案同步**:模块 docstring、CLI `provider-smoke-preflight --help`、
  `docs/DEVELOPMENT.md` 预检章节的超时描述均更新为分档表述。
- **契约测试**:新增
  `test_search_probe_timeout_30s_voice_llm_10s`——锁定 search 探测
  收到 30s、voice/LLM 收到 10s、报告透出 `probe_timeout_seconds=30.0`
  (FakeGet 调用记录断言,零网络)。
- **不改动**:reason/recommendation 闭集、`endpoint_timeout` 归因语义、
  GET-only、不追 redirects、loopback trust_env=False、零 secret、
  stdout-only、非门自声明(`production_ready=false`/
  `release_readiness_evidence=false`)——全部维持 M14-112 契约。

## 5. 验证(canonical venv `D:\AI Learning OS\ai-learning-os\.venv`)

- `pytest tests/test_provider_smoke_preflight.py`:**55 passed**(M14-112
  基线 54 + 本切片新增 1,零网络零子进程);
- `pytest tests/test_searxng_local_provider.py`:**17 passed**;
- `pytest tests/test_provider_smoke_evidence.py`:**128 passed**;
- `ruff check`(默认规则集 + `--select F,E9`)干净;
- `py_compile` 三个触碰的 Python 文件通过;
- `git diff --check` 干净。

## 6. 证据文件与完整性锚点

原始证据 gitignored,位于 worktree
`.verify/artifacts/m14-115-provider-smoke-current-recovery/`
(`EV`);`SHA256SUMS.txt` 为索引(只索引 8 个 JSON,自不含自哈希):

```
d008c4131582c872b0929ebdd4bf752383c078d75417dc6227e94e5cb4b7d7b7  EV/llm-smoke-attempt1-empty-content.json  (299 bytes)
1baa2f23887e2b5fa071df9eba40ceaa611f8587ff0a27c0c3dd99e76a706ab2  EV/llm-smoke.json  (300 bytes)
fb5224df2fe83f11ebdbd17923be55cae8d6582c84ed6ae47854c17476999abf  EV/local-voice-smoke.json  (308 bytes)
899feecb67285ccc4bd940462c4dc1944afa6dcfb6bbc5cc864ad9ddaf7ecdf6  EV/provider-smoke.json  (564 bytes)
8a4028530f1213ab94b9b873d3986e9b45393cf18342982a277ea9de7fa8393f  EV/search-smoke-attempt1-envmiss.json  (300 bytes)
67c52a955ffb12e8480965f7f8b806122faa9afe7fa582e08e91fee9b006fb80  EV/search-smoke-attempt2-querymiss.json  (301 bytes)
a5b7c1bcf10d31ae2c40a983fdc7646c19d9e1395cb2badbb9541fea06168248  EV/search-smoke-attempt3-endpointmiss.json  (300 bytes)
067a2a9fbc3995bf2cfd394e8b2164a21283a03269ef9048ed5acd708693b7ee  EV/search-smoke.json  (303 bytes)
```

## 7. 范围边界(不冒充的部分)

- 本 README 固化的生产恢复操作(searxng recreate、容器 ID 核对、手工
  查询)是 **supervisor 既成事实的记录**,本切片未触碰任何生产容器/
  计划任务/DB/MinIO/voice/Ollama/代理生命周期,也未读取任何 secret。
- provider-smoke.json 三项 pass 只代表**该时点**(2026-09-23T18:57Z)
  三个 provider 冒烟通过;volatility 由后续刷新切片覆盖(M14-98 先例),
  不构成对未来的承诺。
- preflight 修复后未在本切片内对真实生产 searxng 重跑预检(本切片
  零生产触碰边界);30s 档的正确性由契约测试 + §3 真实延迟样本
  (10.3s/12.6s/18.5s < 30s)论证。
- 不声称 release_ready/production_ready:long-soak、审批链、发布材料
  均缺位;`production_ready=false` 不变。
