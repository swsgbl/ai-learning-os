# M14-207 证据：current provider-smoke refresh（preflight blocked，诚实失败收口）

## 0. 结论与边界

- 本切片是 provider-smoke 证据刷新尝试，不是部署、回滚、发布审批或生产变更。
  未执行任何 provider 恢复动作，未触碰容器、env、DB、MinIO、模型加载/卸载或
  provider 生命周期；`release_ready=false` / `production_ready=false` 语义不变。
- Workspace：`ai-learning-os-worktrees/m14-207-provider-smoke-refresh`；
  branch `ops/m14-207-current-provider-smoke-refresh`；base/HEAD
  `32b15be61646416cfa553cc62b9066659610636b`。执行前 tracked-clean，
  branch/HEAD/porcelain 与 UTC 边界记录于 `pre-run-metadata.json`。
- 只读 `provider-smoke-preflight --voice-mode local --json` 结果为
  **overall `blocked`，exit 1**：
  - voice：`ready`（ASR/TTS 子检查均 `ready`）；
  - search：`not_ready` / `upstream_failure` /
    `repair_upstream_network_externally`；
  - llm：`not_ready` / `model_absent` /
    `load_model_externally_then_rerun`。
- 按任务书 fail-closed 规则，三个 `provider-smoke-export` 与
  `provider-smoke-aggregate` 均未执行；没有新的单步证据、没有新的
  `provider-smoke.json`，也没有 provider corrective rerun。运行目录文件数为 0。

## 1. 执行窗口与命令形状

- Preflight：2026-10-01T05:41:59.0470919Z 到
  2026-10-01T05:42:19.7142009Z，20663 ms，exit 1，结果 `blocked`。
  命令形状：`python -m app.ops.cli provider-smoke-preflight --voice-mode local --json`。
- Search export：命令形状为 `provider-smoke-export search --output
  artifacts/temp/provider-smoke/m14-207-current-provider-smoke-refresh/search-smoke.json --json`；
  **未执行**，因此没有 exit code、duration、UTC timestamp 或 pass/fail 结果。
- Local voice export：命令形状为 `provider-smoke-export local-voice --output
  artifacts/temp/provider-smoke/m14-207-current-provider-smoke-refresh/local-voice-smoke.json --json`；
  **未执行**，因此没有 exit code、duration、UTC timestamp 或 pass/fail 结果。
- LLM export：命令形状为 `provider-smoke-export llm --output
  artifacts/temp/provider-smoke/m14-207-current-provider-smoke-refresh/llm-smoke.json --json`；
  **未执行**，因此没有 exit code、duration、UTC timestamp 或 pass/fail 结果。
- Aggregate：命令形状为 `provider-smoke-aggregate --search ... --voice ...
  --llm ... --voice-mode local --output .../provider-smoke.json --json`；
  **未执行**，因此没有 exit code、duration、UTC timestamp 或 pass/fail 结果。

## 2. 与 M14-148 旧聚合对比

- 复核旧源 `artifacts/temp/provider-smoke/20260926T0106/provider-smoke.json`：
  schema `provider-smoke-evidence-v1`，gate `provider-smoke`，拓扑
  `voice_mode=local`，voice/search/llm 三槽位均 `executed=true`、
  `result=pass`；生成时间 `2026-09-26T01:14:14.270193+00:00`。
- 三份旧输入均为工具导出且通过 exact-schema 校验；原始执行窗口为
  `2026-09-26T01:12:09.691519+00:00` 到
  `2026-09-26T01:13:56.873274+00:00`。
- 本轮 preflight 的时间为 2026-10-01T05:42Z，旧聚合已老化约 5 天 4 小时；
  更关键的是，当前 search/llm 前置事实已不满足。因此旧聚合不能被搬运为
  当前新鲜证据，本切片没有产生替代聚合。

## 3. 独立校验、测试与完整性

- 本轮 preflight raw JSON 与 stdout 归档逐字节一致；工具/schema、provider
  键序、状态与 reason/recommendation 闭集、UTC 时间、exit 1、恒定
  `production_ready=false` / `release_readiness_evidence=false` 均断言通过。
  旧聚合与三份输入也按模块 exact-schema 与时间自洽断言通过。
- 独立断言的第一次调用误将 stdin 程序传给 `python -c -`，产生校验器
  SyntaxError；该失败已留档，属于证据校验器调用错误，不是 provider 执行。
  用正确 stdin 解释器形态重跑后断言全过。
- 离线契约测试：`test_provider_smoke_preflight.py`、
  `test_provider_smoke_evidence.py`、`test_release_readiness.py`、
  `test_searxng_local_provider.py`、`test_smoke_search_script.py`、
  `test_smoke_voice_local_script.py`、`test_smoke_llm_script.py`，使用仓库外
  预建 pytest basetemp，结果 **306 passed in 7.93s**，exit 0。
- 本切片未触碰 Python/script 源码，因此 py_compile/ruff 的“触及文件”范围
  为空；`git diff --check` 与新增 tracked 行泄漏扫描结果见收尾记录。

核心 gitignored 证据位于 `.verify/artifacts/m14-207-current-provider-smoke-refresh/`：

| 文件 | bytes | SHA-256 |
|---|---:|---|
| `pre-run-metadata.json` | 1580 | `f7e8a692a78a2286d8f50e9be0ed0256569f3121e739d5590a81141ef357a379` |
| `provider-smoke-preflight.json` | 2608 | `1f23cc25e2b2783d87c7fadcf5a4e788b5b36c71973e5b34a0cc383ee046212a` |
| `provider-smoke-preflight.stdout.json` | 2608 | `1f23cc25e2b2783d87c7fadcf5a4e788b5b36c71973e5b34a0cc383ee046212a` |
| `provider-smoke-preflight.stderr.log` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `provider-smoke-preflight.window.txt` | 118 | `1f193ffd8fb75913e4299f7d07d5b9d6d2d578393d04534dbdc3a34e4323b515` |
| `focused-tests.stdout.log` | 426 | `8f926ba8be1d74bee1a52ed6e66247b804fa4b67586d6f76998d2a88fca4d616` |
| `focused-tests.window.txt` | 117 | `f5d218f2f66bd8209473b9b96dc30d5db491d9e948772c736283474131825f68` |
| `contract-assertions.stdout.log` | 309 | `bc9872f9f8788adf9e5dec25af28e8fdf5d5f76d236ef7089f2bc7160c6cf4ba` |
| `failure-closeout.json` | 939 | `cc67d868eb91b2ca2a0ccfd6bf85e9388efb672ac82fc8516f2558e86f4e5b7d` |

完整最终工件（含校验器失败留档与泄漏扫描日志）由同目录 `SHA256SUMS`
 索引并复验；索引不包含自身。

## 4. 未解决 blocker

1. search：`upstream_failure`，工具建议固定为
   `repair_upstream_network_externally`。
2. llm：`model_absent`，工具建议固定为
   `load_model_externally_then_rerun`。

以上恢复动作均超出本切片授权。 supervisor 审查时不应把本切片解释为
provider-smoke pass、生产恢复或发布放行；若要刷新该门，需先在外部恢复
两个 not-ready 前置，再重新下达独立证据切片。
