# M14-112 证据:provider-smoke 前置只读预检与失败归因面(provider-smoke-preflight)

- 切片类型:实现/测试/文档切片(ops 工具 + CLI + 契约测试 + 文档;零生产触碰)
- worktree:`m14-112-provider-smoke-preflight`(repo 同级 worktrees 目录),分支
  `ops/m14-112-provider-smoke-preflight`,基于 main
  `2b81ec841a9a9f30917961a2058f0f33688cd5b4`(PR #199 merge = M14-111 合入,
  精确基点)
- 交付形态:单 local commit,不 push、不建 PR
- 本 README 为唯一入库证据文件;真实运行的原始证据(gitignored)位于 worktree
  `.verify/artifacts/m14-112-provider-smoke-preflight/`(6 文件 + SHA256SUMS
  索引,自不含自哈希;§5)
- 结论先行:**新工具交付并在当前 main 基点的真实本机环境完成一次只读预检**
  ——真实预检结果如实为 **blocked**(search: SearXNG 在线但五个上游引擎全数
  `HTTP connection error` -> `upstream_failure`/`repair_upstream_network_externally`;
  llm: `127.0.0.1:11434` 连接被拒 -> `endpoint_absent`/
  `start_externally_then_rerun`;voice: FunASR/CosyVoice `/health` 双 200 ->
  ready)——与 M14-104 记录的 release blocker 归因一致且现在**先于冒烟执行**
  即可得。本切片**未运行任何冒烟、未生成 provider-smoke.json、未触碰任何
  release gate**;预检结论不是 provider-smoke 结论;`production_ready=false`
  与 `release_ready=false` 维持不变。

## 1. 目标与背景

provider-smoke 门(release-readiness)自 M14-98/M14-104 起 blocked。M14-104
实证:三步冒烟的失败大多**先于冒烟脚本本身**即可判定——SearXNG 服务在线但
上游境外引擎不可达(冒烟必然 0 结果)、Ollama 端点进程缺席(ConnectError)、
Windows 注册表系统代理劫持 loopback 健康探测(attempt1 假失败)——但运维
只能先跑冒烟再从失败 stdout 反推。本切片交付 `provider-smoke-preflight`
(app.ops CLI 新子命令):对三个门 provider(voice/search/llm)做**有界只读
HTTP 预检**并给出固定词汇的失败归因(reason)与外部动作建议
(recommendation),使下一次生产恢复决策在**不运行任何冒烟、不启停任何
服务**的前提下可检视。

勘察结论(切片起点,均在 main 基点):仓库无既有等价实现(provider-smoke
export/aggregate 只编排真实冒烟执行并导出结论证据;production-preflight 是
DB/alembic/审计链预检;无任何工具探测 provider 端点)——无重叠,全新面。
M14-106(loopback 语音 trust_env=False)/M14-100(LLM gateway loopback
trust_env=False + 超时)/M14-104(注册表代理劫持实证)的代理边界全部内化为
本工具的探测契约。

## 2. 设计与实现

### 2.1 模块与命令

- 模块:`services/api/app/ops/provider_smoke_preflight.py`(新);CLI 子命令
  `provider-smoke-preflight` 注册进 `services/api/app/ops/cli.py`
  (处理函数 `_run_provider_smoke_preflight` + parser `p_ppf` + dispatch,
  与既有 provider-smoke-export/aggregate 同风格)。
- 既有 provider-smoke 行为零改动:`provider_smoke_evidence.py`、三个冒烟
  脚本、release-readiness 门评估器均零触碰。

### 2.2 三个门 provider 的检查(topology-aware)

| provider | 检查 | 契约来源 |
|---|---|---|
| search/SearXNG | base URL(默认 `http://127.0.0.1:8878` = compose `--profile search` 宿主绑定)+ 只读 `GET /search?q=AI Learning OS GitHub&format=json`;results 非空 -> ready;0 结果且端点自报 `unresponsive_engines` -> `upstream_failure`(引擎名+错误类透出,新/旧两种自报形态都解析);0 结果无自报 -> `empty_results` | CloudWebProvider 同 JSON 契约(M10-12) |
| voice(local 拓扑) | ASR/TTS base URL(默认 8010/8011 `/v1`,冒烟脚本同款)+ 只读 `GET {服务根}/health`;HTTP 200 -> listener ready(body JSON 形状是观测不是门,与 smoke_local_voice 口径一致) | tools/voice/smoke_local_voice.py |
| voice(cloud/hybrid 拓扑) | **不检凭据、不探测外部端点**:槽位如实 `not_probed`/`external_credentials_not_inspected`——云端端点+凭据是冒烟调用时外部注入的 precondition,本工具不读 secret | 任务安全边界 |
| llm/Ollama 兼容网关 | base URL(默认 `http://127.0.0.1:11434/v1`,剥 `/v1` 到服务根)+ 只读 `GET /api/ps`;模型驻留(name 按 `:` 前段与请求模型名比对) -> ready;在线但无该模型 -> `model_absent` | /api/ps 既有健康契约(M14-104 复核用过) |

### 2.3 代理边界(M14-104/106/100 教训内化)

- **探测**:loopback URL 的 httpx client 恒 `trust_env=False`(注册表/环境
  系统代理不得劫持本机探测);非 loopback URL 保持 httpx 默认(部署代理照常
  生效,与 LLM gateway/语音 provider 同口径)。探测恒 `follow_redirects=False`、
  超时固定 10s(与既有 smoke 健康探测同契约,不开放覆写)。
- **观测**:报告 `ambient_proxy` 字段显式探测环境/注册表代理压力
  (`urllib.request.getproxies` + `proxy_bypass`)是否覆盖被探测的 loopback
  host——只输出布尔与 host 名,**绝不输出代理 URL**(可能内嵌代理凭据);
  即使检出压力,探测本身因 trust_env=False 也不受影响。

### 2.4 固定词汇与确定性

- status 闭集:`ready` / `not_ready` / `not_probed`;
  overall 闭集:`pass`(全 ready)/ `blocked`(存在 not_ready)/
  `partial`(无 not_ready 但存在 not_probed)。
- reason 闭集(11):`not_configured`/`malformed_url`/`endpoint_absent`/
  `endpoint_timeout`/`http_failure`/`malformed_response`/`upstream_failure`/
  `empty_results`/`model_absent`/`transport_error`/
  `external_credentials_not_inspected`;recommendation 闭集(6)全部指向
  **外部**动作:`fix_endpoint_config`/`start_externally_then_rerun`/
  `investigate_endpoint_externally_then_rerun`/`repair_upstream_network_externally`/
  `load_model_externally_then_rerun`/`verify_external_preconditions_then_run_smoke`;
  reason->recommendation 确定映射(测试锁定)。本工具永远不执行、不指导
  本工具去变更任何服务。
- providers 键序恒 `SMOKE_PROVIDERS`(voice/search/llm);provider 键与语音
  拓扑枚举**直接 import** release-readiness/provider-smoke-evidence 的权威
  常量(不复制第二份,防漂移;测试交叉锁定)。
- 非法 `--voice-mode` argparse exit 2;核心函数非法 voice_mode 先于一切探测
  fail-closed;malformed URL 不崩溃——归因 `not_ready/malformed_url` +
  `fix_endpoint_config` 建议(归因面优先,同时该 provider 诚实 not_ready、
  overall blocked)。

### 2.5 只读与诚实边界(硬约束实现)

- 命令 stdout-only:零子进程、零文件写入、零服务生命周期变更;无路径参数
  (「unsafe paths fail closed」自然满足——没有路径面);源码级 ast 守卫
  测试锁定(无 subprocess/os.environ/getenv/写文件调用)。
- 零 secret:不读取任何 API key/凭据槽位;环境访问仅限 getproxies/
  proxy_bypass 的**布尔**压力判定;**endpoint URL 含 userinfo(user-only
  与 user:password 两形态)、query 或 fragment(含尾随裸 `?`/`#` 分隔符)
  一律 fail-closed 拒绝**(`malformed_url`,先于一切探测——凭据绝不进入
  探测请求/不从 URL 构造 Basic Auth,未通过校验的输入也不回显进报告,
  endpoint 恒 None;supervisor 修正 Round 1);毒化代理值/凭据扫描测试
  零命中。
- 远端自报字符串边界(supervisor 修正 Round 2):unresponsive 引擎名/
  错误类与驻留模型名进入报告前逐项剔除 Unicode Cc 控制字符并截断到
  128 Unicode 码点(`MAX_REMOTE_ITEM_CODEPOINTS`;全控制字符条目丢弃),
  叠加既有名单条数上限(32 引擎/16 模型)与排序确定——任意外形/体量的
  provider 响应都有确定的报告体量上界,控制序列不得注入终端/JSON。
  匹配语义走原始名(既有行为零漂移)。
- 不产生证据:报告恒携带 `production_ready=false` 与
  `release_readiness_evidence=false` 自声明字段;把预检报告喂给
  release-readiness 的 provider-smoke 门评估器必须 `MalformedEvidence`
  拒收(测试锁定);人类摘要含「不是 release-readiness 证据」「预检 pass
  不代表 provider-smoke 已通过」固定边界文案。
- 退出码:pass=0 / blocked·partial=1(如实不通过)/ 参数问题=2。

## 3. 真实只读预检执行(修正后代码 2026-09-23T14:54:06Z–14:54:11Z;首轮 14:23:59Z–14:26:20Z 归因一致被等价复现)

调用形态:`services/api` cwd 下 `<worktree>/.venv/Scripts/python.exe -m
app.ops.cli provider-smoke-preflight [--json] [--voice-mode cloud]`,全部
默认端点(8878/8010/8011/11434),零环境注入。结果(generated_at
`2026-09-23T14:54:06Z`,修正后代码):

| provider | 结果 | 关键事实 |
|---|---|---|
| voice | **ready**(exit 贡献 0) | asr/tts `/health` 双 HTTP 200、`json_body=true`(FunASR/CosyVoice 在线) |
| search | **not_ready** `upstream_failure` | SearXNG `/search?format=json` HTTP 200 但 `results=0`,自报 `unresponsive_engines` = brave/duckduckgo/google cse/wikidata/wikipedia 全部 `HTTP connection error` -> 建议 `repair_upstream_network_externally` |
| llm | **not_ready** `endpoint_absent` | `127.0.0.1:11434/api/ps` 连接被拒 -> 建议 `start_externally_then_rerun` |
| overall | **blocked**(exit 1) | 与 M14-104 记录的 release blocker 归因一致 |

- 附加运行:`--voice-mode cloud` -> voice `not_probed`/
  `external_credentials_not_inspected`(不检凭据如实声明),search/llm 同上
  -> overall `blocked`(可探测面仍有 not_ready;若 search/llm 就绪则为
  `partial`)。
- 环境观测:执行窗口内 `getproxies()` 可见本机代理配置(host 127.0.0.1,
  端口略),但 `proxy_bypass("127.0.0.1")=true`(loopback 在豁免名单)——
  报告 `ambient_proxy.pressure_detected=false` 如实;探测本身恒
  trust_env=False 不受代理影响。
- 全程零服务生命周期变更:未启动/停止/重启 Ollama/SearXNG/FunASR/
  CosyVoice/代理/Docker/CC Switch/计划任务;未读取任何 key;探测全部为
  只读 GET。真实 SearXNG 搜索探测与 M14-104 的只读诊断查询
  (precheck-endpoints.txt)同类。

## 4. 验证(聚焦离线,worktree venv,不依赖 live provider;含 supervisor 修正 Round 1/2 复验)

- 新契约测试:`services/api` 下
  `python -m pytest tests/test_provider_smoke_preflight.py -q`
  -> **54 passed**(修正后)——覆盖矩阵:CLI 注册/分发/参数校验、全 ready
  确定性、endpoint_absent/timeout/http_failure/malformed_response/
  transport_error/upstream_failure(新+旧自报形态)/empty_results/
  model_absent/not_configured/malformed_url/cloud 拓扑 not_probed 归因、
  拓扑与 provider-key 无漂移(SMOKE_PROVIDERS/VOICE_MODES 三方交叉锁定)、
  loopback trust_env=False/非 loopback 默认、ambient 代理检出/豁免/无代理
  三态与代理值零泄漏、**endpoint userinfo(user:password 与 user-only)/
  query/fragment(含尾随裸分隔符)先于探测 fail-closed 拒绝且不回显
  (修正 Round 1)**、**远端自报名单逐项 128 码点截断 + 控制字符剔除 +
  全控制条目丢弃 + 条数上限保持 + 敌意超大响应报告体量上界(修正
  Round 2)**、源码级只读 ast 守卫、CLI 处理零文件写入、报告非门证据
  (评估器拒收 + 自声明字段 + 摘要边界文案)、退出码词汇、默认端点契约。
  全部测试零网络/零子进程(fake get 替身 + httpx.MockTransport + socket
  拨号禁令)。
- 邻域回归(provider-smoke/release-readiness/LLM/voice/search 十一套件):
  `python -m pytest tests/test_provider_smoke_preflight.py
  tests/test_provider_smoke_evidence.py tests/test_release_readiness.py
  tests/test_llm_gateway.py tests/test_llm_gateway_timeout_budget.py
  tests/test_voice_loopback_proxy_trust.py tests/test_smoke_search_script.py
  tests/test_smoke_llm_script.py tests/test_smoke_voice_local_script.py
  tests/test_smoke_voice_cloud_script.py tests/test_search.py -q`
  -> **359 passed, 1 warning**(7.53s,修正后)。
- CLI 依赖面补跑:`tests/test_production_preflight.py
  tests/test_voice_providers.py tests/test_voice_local_providers.py
  tests/test_voice_local_scripts.py tests/test_rc_smoke_rehearsal.py
  tests/test_evidence_cockpit.py` -> **234 passed, 2 skipped**(34.18s,修正后复跑)。
- `ruff check .`(services/api,默认规则)与 `ruff check --select F,E9 .`
  -> All checks passed;`py_compile` 三触碰文件 -> OK;
  `git diff --check` -> 干净。
- 生产容器/DB/MinIO/secrets/release-approval/release gate 零接触;
  Ollama/FunASR/CosyVoice/SearXNG/代理/Docker/计划任务零生命周期变更;
  canonical 仓库与 `.venv` 零触碰(worktree 从零 `uv venv` 3.12.14)。

## 5. 证据文件与完整性锚点(worktree `<EV>` = `.verify/artifacts/m14-112-provider-smoke-preflight/`,gitignored)

```
553829991929cabdda03c9cd8dfe12c87e0947ce058cd41f6a50534252dcf5f2  window.txt
28f2848cf14e58eb49dce0beba879136eee002eeb1f622ce6b964f2811492af1  preflight-local-default.exit.txt
ca0db6dbb46963fcef64f07ed3e6899b634d7171aed1ec05763de55d65f767df  preflight-local-default.json
0c50f48f894af7e244a31307e5a46d2172e3394799e5fdbe9ba81b0129582786  preflight-local-summary.txt
be1f0c54c51db06564ea937b2631e692de8c0a3479ae6fe9cc275c4453a03eb0  preflight-cloud-partial.json
7d6dda8e969e01dba0fc64bb06df4cb0924234882c9693ef42a10706744a4715  preflight-cloud-partial.exit.txt
```

另含 `SHA256SUMS`(上述 6 文件索引,自不含自哈希)。JSON 均为命令 stdout
原始字节;exit 文本为 shell `$?`;入库 README 不含 secret 或真实凭据。

## 6. 范围边界(不冒充的部分)

1. **预检 pass != provider-smoke 通过**:预检只判定前置可观测就绪;真实
   provider-smoke 仍须由运维按拓扑执行三个冒烟脚本并经
   provider-smoke-export/aggregate 产出门证据。本切片未运行任何冒烟、
   未生成 provider-smoke.json、未刷新任何 release gate。
2. **本切片的真实预检结果 = blocked**:search 上游网络与 llm 端点进程的
   解除(修复出站网络 / 启动 Ollama / GPU 空闲窗口)是后续**外部运维动作**,
   留 supervisor 决策——本工具与本切片都不执行任何变更。
3. **cloud/hybrid 语音面不可由本工具观测**(不检凭据是有意边界,不是缺漏):
   该拓扑下最好结果是 `partial`——如实,不建议解读为可探测面的失败。
4. **search 探测不带鉴权头**(不读 key):需要 key 的 SearXNG 部署会得到
   `http_failure`(如 401),归因按 investigate 处理;本地 `--profile search`
   栈无鉴权,当前 blocker 拓扑不受影响。
5. `/api/ps` 只反映**当前驻留**模型:模型存在但未加载会如实归因
   `model_absent` -> `load_model_externally_then_rerun`(这正是 M14-104
   需要的区分);驻留不保证生成不超时(GPU 争抢边界是 M14-98/100 的另一
   维度,预检不预测)。
6. ambient 代理压力观测是**布尔观测**不是门:getproxies/proxy_bypass 的
   语义在 Windows 上覆盖注册表 ProxyOverride 与环境双通道,但探测因
   trust_env=False 恒不受影响——观测只用于提示环境状态。
