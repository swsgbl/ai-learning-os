# M14-218 证据：provider-smoke LLM 冒烟预算误判面修复（search 诚实 blocked 不变）

## 0. 结论与边界

- 本切片只解决 LLM 冒烟的一个已知误判面：简单探针未显式设置
  `LLM_SMOKE_MAX_TOKENS` 时默认输出预算 256 过小，thinking 模型把
  预算耗在推理上返回空 content，脚本按契约判 fail——这是「工具预算
  误判」而非「端点不可用」。
- 修复：`infra/smoke_llm.sh` 简单探针默认 256 → 1024。显式覆写
  （`LLM_SMOKE_MAX_TOKENS`）、timeout（`LLM_TIMEOUT_SECONDS`）、
  num_ctx（`LLM_NUM_CTX`）、错误脱敏与两段 probe（简单补全 + rubric
  judge）语义均不变。
- search 仍按 M14-209 如实 blocked：真实 cloud-web 请求网络/超时失败，
  根因在外部上游/网络/容器环境。本切片不处理 Docker Desktop/WSL/
  SearXNG/容器，不改变 search 冒烟脚本的任何行为。
- 本轮零真实 provider 请求：未发任何 search/LLM/voice 探针，未触碰
  生产 env、容器、Ollama 或任何用户进程；全部验证为离线契约测试。
- 本 README 不表示 release readiness / release approval，
  `production_ready=false` 不变。

## 1. 为什么是 1024（引证链）

- M14-115（`docs/evidence/m14-115-provider-smoke-current-recovery/`）：
  LLM 冒烟第 1 次按默认 256 tokens 返回 thinking-only 空 content，
  脚本正确 fail；第 2 次仅把 `LLM_SMOKE_MAX_TOKENS` 提到 1024，得到
  非空正文（12 chars）并通过 rubric（achieved=[True, True]，
  confidence=1.0）。
- M14-209（`docs/evidence/m14-209-provider-smoke/`）：llm 冒烟按默认
  256 再现空 content fail，未做加预算重试——证实该误判面是默认值
  问题，不是端点状态问题。
- M14-98/M14-100（`docs/evidence/m14-100-local-llm-timeout-budget/`）：
  饱和 16GB GPU 上 thinking 模型 max_tokens=2048 生成 >30s，两跑
  `httpx.ReadTimeout`——不能盲目放大到 2048。
- rubric judge 探针一直走 gateway 生产默认 1024 且已多次通过；简单
  探针默认 1024 与之对齐，是 M14-209 后的最小已知合理预算。

## 2. 刻意不做的事（诚实边界）

- 不加自动重试/fallback/预算自适应二次请求：探针保持单轮请求，
  thinking-only 空 content 一律 fail。M14-115 的第二次通过是运维
  显式改预算后的人工重跑，不是脚本自动行为；脚本不得把失败「修成」
  通过。
- 不改 gateway 生产 chat 语义（生产默认 1024 不变）；不改 timeout
  默认（30s，运维按机器负载显式覆写）；不改 num_ctx 语义（兼容性
  不保证的请求 Hint）。
- 不修 search：search 的 fail 根因在外部上游/网络/容器环境，本切片
  范围外（Docker/WSL/SearXNG 均为硬边界，未触碰）。
- 不 push、不 PR、不合并；单 local commit。

## 3. 变更清单

| 文件 | 变更 |
|---|---|
| `infra/smoke_llm.sh` | 简单探针默认 `max_tokens` 256 → 1024；头部注释与探针内注释更新为 M14-218 决策（1024 与 rubric judge/gateway 生产默认一致；2048 有超时实证不采用；单轮请求、空 content 一律 fail） |
| `services/api/tests/test_smoke_llm_script.py` | 契约锁定：默认 1024、禁止默认回退 256、禁止 2048/32 调用形态、新增禁止 retry/fallback/预算自适应二次请求的契约；显式覆写（`LLM_SMOKE_MAX_TOKENS`）、timeout/num_ctx 透传契约保持 |
| `docs/CHANGELOG.md` / `docs/PROJECT_STATUS.md` / `docs/ROADMAP.md` | M14-218 状态回填（措辞不夸大） |

显式覆写语义未变的自查：`LLM_SMOKE_MAX_TOKENS` 仍是可选正整数、
bash 侧形态校验 fail-closed、设置时探针以
`int(_budget_raw)` 构造请求——契约测试
`test_script_validates_timeout_and_budget_env_shapes` 未改动且通过。

## 4. 本地验证（canonical venv，全部离线）

解释器：`D:\AI Learning OS\ai-learning-os\.venv\Scripts\python.exe`
（主 checkout canonical venv；本 worktree 无 `.venv`）。

| 命令 | 结果 |
|---|---|
| `python -m pytest services/api/tests/test_smoke_llm_script.py -q` | **8 passed**（含新增单轮请求契约） |
| `python -m pytest services/api/tests/test_provider_smoke_evidence.py services/api/tests/test_smoke_search_script.py services/api/tests/test_smoke_voice_local_script.py services/api/tests/test_provider_smoke_container.py -q` | **163 passed**（邻居 provider-smoke 契约无回归） |
| `python -m ruff check services/api/app services/api/tests` | **All checks passed** |
| `git diff --check` | 通过（无空白错误） |
| 新增行敏感值扫描 | 通过（无 endpoint/key/token/password 值，仅非敏感槽位名） |

本轮未运行 `smoke_llm.sh` 本体：脚本需要真实 `LLM_ENDPOINT` /
`LLM_API_KEY` / `LLM_MODEL`，属真实 provider 冒烟，按切片边界禁止。
脚本行为由离线文本契约锁定（与 M14-71/M14-100 同一口径）。

## 5. 后续真实重跑的前置条件

- LLM 冒烟的真实重跑（验证 1024 默认在实际端点上消除 thinking-only
  误判）必须在外部环境修复后由显式的 provider-smoke 切片单独执行，
  不得由本切片自动推导或顺手执行。
- search 恢复同样在范围外：其前置是外部上游/网络/容器环境修复，
  本切片未做任何相关变更。
- 真实重跑切片应执行 fresh search/local-voice/llm 三 export 与
  aggregate（M14-209 口径），不得复用旧 JSON。
