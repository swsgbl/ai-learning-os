# M14-100 证据:本地 LLM 冒烟超时与生成预算显式化(离线修复切片)

- 切片类型:实现/测试/文档切片(**零 live provider 接触**——不发起任何
  Ollama 生成请求、不跑 GPU 吞吐基准、不重启任何进程、零生产变更)
- worktree:`m14-100-local-llm-timeout-budget`(repo 同级 worktrees 目录),
  分支 `ops/m14-100-local-llm-timeout-budget`,基于 main
  `a583f918fba4ea6223b08afc275d909fefef152b`(PR #186 merge = M14-98 合入,
  本地全 SHA 复核一致;无需 fetch)
- 交付形态:单 local commit,不 push、不建 PR
- 结论先行:LlmGateway 的请求超时与有界生成预算从硬编码变为**显式、可配、
  离线可测**;loopback LLM 请求强制绕过环境/系统代理;httpx 超时/传输异常
  统一映射 `LlmUnavailable`;生产 chat 默认语义(30s / max_tokens=1024 /
  payload 形态)**逐字节零漂移**。provider-smoke llm 步的**真实重跑不在本
  切片**(见 §6 边界)。

## 1. 目标与 M14-98 事实基础

M14-98(docs/evidence/m14-98-current-provider-smoke/,基点 `841b36f`,
PR #186 合入 main)对 current main 真实重跑 provider-smoke 三件套,search
与 local-voice PASS,llm 两跑真实 FAIL。已验收事实(本切片的唯一经验输入):

1. gateway 默认 30s 超时**硬编码**于 `HttpxTransport` 且无任何配置通道;
   饱和 16GB GPU(9B thinking 模型与语音/训练/VMware 共存)上
   max_tokens=2048 生成 >30s → 两跑 `httpx.ReadTimeout`(32402ms/30506ms);
2. max_tokens=8 warm 探针直连 HTTP 200 / 6.4s(响应含 reasoning 字段,
   证明 thinking 生效且小预算可完成请求);
3. attempt1 traceback 含 httpcore http_proxy 帧——shell NO_PROXY 参数展开
   传递异常时,loopback 请求被路由进环境代理(httpx 默认 `trust_env=True`
   消费 HTTP(S)_PROXY);attempt2 字面量直连帧消失但仍超时;
4. `httpx.ReadTimeout` 未被映射为 `LlmUnavailable`——冒烟探针只能以裸
   traceback 形态失败(`except LlmUnavailable` 捕不到传输层异常);
5. M14-98 §4 处置方向明确:"调大 gateway timeout……留 supervisor/运维决策"
   ——本切片把这个决策做成显式配置面 + 离线回归,而不是擅自改全局默认。

## 2. 设计(四项窄修复,全部有界)

### 2.1 超时显式化:`timeout_seconds` 构造参数 + `LLM_TIMEOUT_SECONDS` 槽位

- `LlmGateway(timeout_seconds=...)`(可选 >0 数值;bool/0/负数/字符串
  拒绝;与显式 `transport` 注入互斥——transport 自带超时语义,并存无从
  裁决,静默丢弃配置是更坏的失败形态);
- `Settings.llm_timeout_seconds: float | None = None`(env
  `LLM_TIMEOUT_SECONDS`;compose `AIOS_LLM_TIMEOUT_SECONDS:-` 空串归一
  None,与 `llm_num_ctx` 同款 before-validator + positive-validator);
- **None = gateway 既有默认 30s**——cloud 部署不配置该槽位即零行为漂移
  (默认构造不向 `HttpxTransport` 传任何参数,测试锁定);
- 装配链全透传:`main.py` → `build_llm_judge(config["timeout_seconds"])` →
  `LlmGateway`,与 num_ctx 同一形态;
- 本地拓扑由部署显式调大(如 `LLM_TIMEOUT_SECONDS=300`),**不实测不设
  默认**——本切片禁止 live 探针,任何"推荐值"都只能是未经本机实证的
  猜测,显式留给运维按机器负载决定(M14-98 §4 的原话边界)。

### 2.2 loopback 代理绕过:transport 层按 URL host 判定

`HttpxTransport.post_json` 构造 client 时,URL host 为 loopback
(`localhost` / `127.0.0.0/8` / `::1`,`ipaddress.is_loopback` 判定)则显式
`trust_env=False`;其余(云端域名、内网 IP、容器服务名)不传 trust_env——
保持 httpx 默认(尊重部署环境的代理配置,cloud 语义零漂移)。冒烟 shell
不再依赖调用方 NO_PROXY 手工正确性(M14-98 attempt1 的教训);判定在
transport 层而非 gateway 层,因为 loopback 是**端点拓扑属性**且 transport
是唯一构造 httpx.Client 的地方。

### 2.3 httpx 异常 → `LlmUnavailable` 统一失败语义

`httpx.TimeoutException` → `LlmUnavailable("LLM 请求超时（>{N}s）: ...")`;
其余 `httpx.HTTPError` → `LlmUnavailable("LLM 传输失败: ...")`。消息只含
异常类型名与超时秒数——不含 URL/headers(api_key 在 Authorization header,
绝不进入异常文本;redaction 测试锁定)。判分管线与冒烟脚本的
`except LlmUnavailable` 从此真正覆盖超时形态(此前 ReadTimeout 会裸穿透)。

### 2.4 有界生成预算:既有 OpenAI 兼容字段,不发明规范外字段

- 生产 chat:`max_tokens` 本就是 `chat()` 显式参数(默认 1024,判分调用
  语义不变);payload 形态与既有逐字节一致(零漂移测试锁定);
- 冒烟简单探针:默认预算从 2048 降为 **256**,`LLM_SMOKE_MAX_TOKENS`
  (可选正整数)覆写——2048 已实证超时(M14-98),32 级小预算有
  thinking-only 空 content 风险(M14-71 教训),256 是**未实测折中**,
  真实重跑切片可按实测调整;探针仍要求非空 content(M14-71 语义不变);
- rubric judge 探针不传 max_tokens——保持 gateway 生产默认 1024,
  **冒烟不改变生产 chat 语义**(独立预算只作用于冒烟自有探针,这正是
  "provider-smoke 契约独立暴露有界探针预算"的窄实现与理由:探针的目的
  是证明端点活着且能产出非空 content,不是复刻生产负载)。

## 3. API 契约依据(为什么不换协议/不加 reasoning 字段)

权威事实(Ollama 官方行为):`/v1/chat/completions` 是 OpenAI 兼容端点,
接受 `max_tokens` 与 thinking 模型的 reasoning 控制;native `/api/chat`
支持 stream 与 `think` 控制(如 `"low"`)。

本切片的决策:**留在 OpenAI 兼容 `/v1` + 既有 `max_tokens` 字段**,理由:

1. 仓库既有 gateway 契约就是 OpenAI 兼容 `/v1`(M10-01 起,rubric judge
   生产链路与冒烟探针共用)——切到 native `/api/chat` + `think` 需要
   本地适配器与新响应解包,超出"超时与预算修复"的窄边界;
2. `max_tokens` 是 OpenAI 规范内字段且已被本地端点**实证消费**
   (M14-98:max_tokens=8 探针 200/6.4s,预算直接决定生成时长);
3. 加 reasoning 控制字段(如 OpenAI 生态的 `reasoning_effort`)到生产
   payload 与 M14-71 纪律冲突:options.num_ctx 的教训是 Ollama /v1 对
   额外字段**实证不可靠消费**且本切片禁止 live 验证——未实证的字段
   不进 payload,不据此声称生效。thinking 预算控制因此落在已实证的
   `max_tokens` 有界预算上(推理消耗计入该预算是 OpenAI 兼容端点的
   既定行为);
4. provider 级 think 控制(native `think:"low"`)留待真实重跑切片:若
   有界预算 + 显式超时仍不足以让本地 llm 步通过,由带 live 验证的
   切片评估本地适配器,而不是在本切片预埋未验证代码。

## 4. 变更清单

| 文件 | 变更 |
|---|---|
| `services/api/app/llm/gateway.py` | `_is_loopback_url`(urlsplit+ipaddress);`HttpxTransport` loopback → `trust_env=False`、httpx 异常 → `LlmUnavailable`;`LlmGateway(timeout_seconds=...)`(校验+与 transport 互斥);`chat()` docstring 记录预算契约 |
| `services/api/app/core/config.py` | `llm_timeout_seconds: float \| None = None` + 空串归一/positive 双 validator |
| `services/api/app/llm/rubric_judge.py` | `build_llm_judge` 透传 `timeout_seconds` |
| `services/api/app/main.py` | 装配 dict 透传 `settings.llm_timeout_seconds` |
| `infra/smoke_llm.sh` | 头部 M14-100 说明;`LLM_TIMEOUT_SECONDS`(正数形态校验)与 `LLM_SMOKE_MAX_TOKENS`(正整数校验)bash 校验+透传;两探针构造包 `except ValueError` 干净 FAIL;简单探针预算默认 256 可覆写;judge 探针透传 timeout |
| `infra/docker-compose.yml` | `LLM_TIMEOUT_SECONDS: ${AIOS_LLM_TIMEOUT_SECONDS:-}` 空默认透传(空 = 既有默认,渲染零漂移) |
| `infra/docker-compose.rc-smoke.yml` | 同上(rc-smoke compose 同槽位) |
| `.env.example` | `LLM_TIMEOUT_SECONDS=` 槽位 + 注释(含 LLM_SMOKE_MAX_TOKENS 指引) |
| `services/api/tests/test_llm_gateway_timeout_budget.py` | **新增 27 项**(§5) |
| `services/api/tests/test_llm_gateway.py` | 两个 `_SpyGateway` 签名同步扩展 timeout_seconds(装配契约镜像) |
| `services/api/tests/test_smoke_llm_script.py` | 预算锚点更新(2048→256 可覆写)+ 新增 timeout/校验/干净 FAIL 三锚点测试(7 项) |
| `services/api/tests/test_rc_smoke_rehearsal.py` | **显式更新** `docker-compose.yml` 生产 pin(M14-96 机制:改生产面文件必须显式改 pin;新哈希 d2e4f493…,理由见 §2.1——空默认槽位不改生产行为) |

## 5. 验证(全部离线,零 live provider/GPU)

- 新契约套件 `test_llm_gateway_timeout_budget.py`(**27 passed**):timeout
  构造校验/互斥、默认构造零参数(cloud 30s 零漂移)、显式超时传播
  (gateway→transport→httpx.Client(timeout=…))、settings 空串归一与
  非法值拒绝、build_llm_judge/main 装配透传、max_tokens 预算序列化、
  loopback 四形态(127.0.0.1/localhost/::1/127.0.0.0/8 段)强制
  trust_env=False、云端三形态保持默认 trust_env、httpx ReadTimeout/
  ConnectError/非 JSON/HTTP 500 → LlmUnavailable 映射、全失败形态
  api_key 不泄漏。全部 fake transport / monkeypatched httpx.Client /
  本地构造 httpx.Response——零网络零 GPU;
- 脚本契约 `test_smoke_llm_script.py`(**7 passed**,纯文本断言):预算
  默认 256+env 覆写、timeout 形态校验 fail-closed、两探针 timeout 透传、
  构造 ValueError 干净 FAIL(无裸 traceback);
- 既有回归:`test_llm_gateway.py`(24)+ `test_provider_smoke_evidence.py`
  + `test_config_privacy.py` + `test_compose_profiles.py`/
  `test_compose_restart_policy.py`/`test_rc_smoke_rehearsal.py`(compose
  面与生产 pin)全绿;
- 全量离线套件:**4211 passed, 33 skipped**(290s;唯一改动相关失败是
  M14-96 生产 pin 按机制显式更新后全绿;1 warning 为 starlette testclient
  的 anyio 既有 DeprecationWarning,与本切片无关);
- `ruff check services/api/app services/api/tests` → All checks passed;
- `py_compile`(gateway/rubric_judge/config/main + 三个测试文件)→ OK;
- `bash -n infra/smoke_llm.sh` → 语法 OK;
- `git diff --check` → 干净。

## 6. 范围边界(诚实声明)

1. **本切片没有重跑任何 live LLM/provider/GPU 探针**——llm provider-smoke
   步在当前机器拓扑下仍为未验证状态;M14-98 的失败证据不被本切片"修复",
   被修复的是配置与契约面(超时可配、预算有界、代理绕过、失败语义)。
   真实重跑(GPU 空闲窗口 + 运维选定 `LLM_TIMEOUT_SECONDS`/
   `LLM_SMOKE_MAX_TOKENS`)是后续运维动作,届时按 M14-98 §4 纪律执行并
   重新导出证据。
2. 冒烟探针默认预算 256 是**未实测的工程折中**(8 实证过小风险、2048
   实证超时),不是本机调优结论;`timeout_seconds` 同理不设"推荐默认"。
3. 生产 chat 默认语义零漂移是**离线测试锁定**的声明(默认构造不传超时、
   payload 逐字节一致),不是对云端端点的 live 复测。
4. 零生产触碰:容器/计划任务/DB/MinIO/语音/Ollama/secrets/代理生命周期
   零变更;canonical 仓库与 `.venv` 零触碰(worktree 自建 uv venv
   CPython 3.12 + api/dev requirements)。
5. M14-96 生产文件 pin 的显式更新(docker-compose.yml)只反映新增空默认
   槽位;`build_release_candidate.sh`/`smoke_docker.sh` 两 pin 未动。
