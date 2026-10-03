# M14-223 证据：provider-smoke search「预检 ready / 冒烟 fail」差异根因切片

## 0. 结论（根因分类 + 精确边界）

- **根因分类：代码侧探针超时契约不对称（工具性假阴性），由外部上游引擎劣化触发。**
  同一 SearXNG 端点（`http://127.0.0.1:8878`）、同一查询词（`AI Learning OS
  GitHub`，preflight `SEARCH_PROBE_QUERY` 与 `infra/smoke_search.sh` 默认值同款）、
  同一 JSON API 契约（`/search?q=..&format=json`），两条路径唯一实质差异是
  **客户端等待上限**：
  - preflight search 探针：`SEARCH_PROBE_TIMEOUT_SECONDS = 30.0`
    （`services/api/app/ops/provider_smoke_preflight.py`；M14-115 依据真实聚合
    延迟实测 10.3s/12.6s/18.5s 从 10s 提高，固定常量不开放覆写）；
  - 冒烟/生产路径：`CloudWebProvider` 默认 `timeout_seconds=10.0`
    （`services/api/app/search/providers.py`），`infra/smoke_search.sh` 探针
    构造不覆写。
- **触发条件（外部）**：SearXNG 上游引擎劣化——M14-222 preflight 证据自报
  `unresponsive_engines = [brave, duckduckgo, google cse, wikidata]`、错误类
  `[CAPTCHA, SSL error, timeout, too many requests]`；`infra/searxng/settings.yml`
  的 bing 引擎（cn.bing.com）`timeout: 20.0`，聚合响应时间受最慢引擎支配，
  可达 ~20s。聚合延迟落在 **(10s, 30s] 窗口**时，preflight 30s 界等到结果
  判 ready，冒烟 10s 界必然超时判 fail——「预检就绪 ⇒ 冒烟可执行」的语义
  被破坏。M14-115 只修了 preflight 侧同款问题（当时冒烟恰好请求 <10s 通过，
  盲区未暴露）；M14-209（10693ms）与 M14-222（11041ms）两次同构真实失败
  均为该窗口命中（10s httpx 超时 + ~0.7–1.0s bash/python 进程开销，脱敏文案
  「网络错误或超时」对应 `httpx.TimeoutException → httpx.HTTPError` 分支；
  连接拒绝形态为亚秒级失败，与本时长不符，已排除）。
- **精确边界（本次修复）**：`infra/smoke_search.sh` 探针构造改为
  `CloudWebProvider(..., timeout_seconds=SEARCH_PROBE_TIMEOUT_SECONDS)`，
  常量 import 自 `app.ops.provider_smoke_preflight`（单一事实源，防两处字面量
  漂移回归）；新增契约测试锁定 import 形态与传参形态、拒绝任何字面量硬编码。
  **生产 API 路径（`build_search_registry` 构造的 `CloudWebProvider` 默认
  10s）不在本切片范围**——生产搜索的响应上限是独立的 API 行为决策（worker
  占用/用户体验），本切片只对齐「preflight 探针界 = 冒烟探针界」的门内语义；
  上游劣化本身（引擎 CAPTCHA/SSL/限流）属外部运维面，代码侧不修。
- **剩余外部阻塞**：SearXNG 上游引擎健康（brave/duckduckgo/google cse/
  wikidata 的 CAPTCHA/证书/限流/超时）需外部运维修复；修复前聚合延迟与
  0 结果形态会持续。本切片后该劣化在 preflight（unresponsive_engines 归因）
  与冒烟（真实结果门 ≥1 条）两侧**一致可见**，不再产生「ready 但超时 fail」
  的矛盾归因。

## 1. 命令与时间戳（2026-10-03T01:14Z，单次真实请求，零重试）

修复验证使用任务边界许可的**唯一一次**真实 search 冒烟（现有项目工具链、
现有端点仍可用、零重试）：

```bash
# cwd = 本 worktree 根；PYTHON 为主 checkout canonical venv（非敏感路径值不入库）
date -u +"start=%Y-%m-%dT%H:%M:%S.%3NZ"
PYTHON=<canonical-python> SEARCH_CLOUD_ENDPOINT=http://127.0.0.1:8878 \
  bash infra/smoke_search.sh
date -u +"end=%Y-%m-%dT%H:%M:%S.%3NZ"
```

- start=`2026-10-03T01:14:45.578Z`，end=`2026-10-03T01:15:06.142Z`，exit=1，
  总耗时 20604ms；输出（脱敏）：
  `[smoke-search] FAIL: 0 条合法结果（需要至少 1 条 http/https 结果）`。
- **该结果是根因假设的直接实证**：
  1. 请求**未超时**——30s 界内等到 SearXNG 完整聚合响应，聚合实际耗时
     ~19.6s（20604ms − ~1s 进程开销），落在 M14-115 实测区间（10.3–18.5s）
     延长线上且 < bing 引擎 20s 超时、< 30s 探针界；**同一请求在修复前
     （10s 界）必然超时**，精确复现 M14-222 的 11041ms 失败形态；
  2. 失败形态从「客户端超时（工具性假阴性）」变为「0 条合法结果（真实
     外部上游劣化）」——归因与 preflight 的 `unresponsive_engines` 自报
     一致（诚实 fail，不重试、不修数据）。
- 失败被如实保留：本切片**不**声称 search 冒烟已恢复 pass；上游 0 结果
  仍是外部阻塞（§0）。
- 其余验证全部离线（零网络）：`http://127.0.0.1:1`（必拒绝回环端口）的
  fail-closed 路径自检（亚秒级失败 + 脱敏文案 + exit 1，同时排除「连接
  拒绝可产生 ~11s 失败」的替代假设）；探针 import 链与超时构造的零网络
  断言（`p._timeout == 30.0`）。

## 2. 证据基础（全部只读，未修改、未重放）

- M14-222 tracked 证据：`docs/evidence/m14-222-current-provider-smoke/README.md`
  （current main `1971f2b7`）。
- M14-222 raw 证据（gitignored，只读）：
  `m14-222-current-main-provider-smoke-refresh` worktree 的
  `artifacts/temp/provider-smoke/m14-222/20261003T002318298Z/`——
  `provider-smoke-preflight.stdout.json`（00:24:04.545Z：search ready，
  200/8 结果，probe_timeout_seconds=30.0，4 引擎 unresponsive）、
  `search-smoke.json`/`search-smoke.stderr.log`（00:25:56.444Z–
  00:26:07.485Z，11041ms，exit 1，「网络错误或超时」）。
- 历史同构样本：M14-209（2026-10-01T11:55Z）search 冒烟 fail 10693ms，
  同一脱敏超时文案（`docs/evidence/m14-209-provider-smoke/README.md`）；
  M14-115（2026-09-23）聚合延迟实测 10.3s/12.6s/18.5s 与 preflight 10s→30s
  修复史（`docs/evidence/m14-115-provider-smoke-current-recovery/README.md`）。
- 配置面：`infra/searxng/settings.yml` bing 引擎 `timeout: 20.0`；
  `infra/smoke_search.sh` M14-66 回环代理绕过（NO_PROXY/no_proxy）——
  ambient proxy pressure=false（preflight 自报）且拒绝形态为亚秒级，
  代理劫持假设与 11s 时长不符，排除。

## 3. 变更与验证（离线聚焦 + 邻居 + 静态）

- 变更：`infra/smoke_search.sh`（探针超时对齐 preflight search 档，注释
  补根因依据）；`services/api/tests/test_smoke_search_script.py`（新增
  `test_script_probe_timeout_aligns_with_preflight_search_tier`：锁定
  `from app.ops.provider_smoke_preflight import SEARCH_PROBE_TIMEOUT_SECONDS`
  与 `timeout_seconds=SEARCH_PROBE_TIMEOUT_SECONDS` 传参形态，拒绝
  `timeout_seconds=30`/`timeout_seconds=10` 字面量漂移）；本 README 与
  三本台账。生产 Python 模块零改动。
- 测试（canonical venv，basetemp 置于仓库外）：
  `tests/test_smoke_search_script.py`（修改文件聚焦）→ **8 passed**；
  邻居 provider-smoke 套件 `tests/test_provider_smoke_evidence.py` +
  `tests/test_provider_smoke_preflight.py` + `tests/test_smoke_search_script.py` +
  `tests/test_smoke_voice_local_script.py` + `tests/test_smoke_llm_script.py`
  → **202 passed**（2.85s；M14-222 基线 201 + 本切片新增 1，精确吻合）；
  `tests/test_search.py`（CloudWebProvider 契约邻居）→ **25 passed**（6.70s）。
- ruff（Python 变更文件）→ All checks passed；`git diff --check` → 干净；
  新增 tracked 行（35 行）secret 形态/本地绝对路径/U+FFFD 扫描 → 全部
  无命中。零服务 stop/start/recreate、零生产 DB/secret 接触、真实请求仅
  §1 单次。
- 1 个本地 commit，基于 `465326d2`；不 push、不开 PR、不合并。

## 4. 边界声明（不变项）

- 本切片是根因分析与最小工具修复，**不**产生新的 `provider-smoke.json`、
  **不**改变 release-readiness 任何结论；`release_ready=false`/
  `production_ready=false`/`public_ready=false` 不变；M14-209 仍是最近一次
  完整聚合。search 恢复 pass 需外部上游修复后由显式刷新切片验证。
- §1 的单次真实失败不重试、不美化；preflight 语义不扩大（预检 ready 仍
  只代表前置可观测就绪）。
