# M14-237: Public mobile release gate（公网移动发布聚合门——只读、fail-closed、聚合专用）

## Scope

本切片交付 `tools/android_release/public_mobile_release_gate.py`——一个
**只读、fail-closed 的聚合门**，把既有工具机器导出的证据与一份受约束的
人工 attestation 合成为一个专门结论：`public_mobile_ready`。目的：把
"公网移动发布是否就绪"从人工拼装多个 JSON 报告的结论，固化为一个可
审计、可回放、blockers 与下一步只读命令逐条列出的机器判定。**本门绝不
重复任何子工具的探测语义**：零网络、零设备、零 Docker/生产动作、零
子进程、零 env/secret 读取。评估时刻（`evaluation_time`）在 CLI 为真实
当前 UTC、在库调用/契约测试中显式注入固定 UTC——报告顶层
`generated_at` 语义为**真实评估时间**；`reference.evidence_frontier`
语义为**机器证据最大时间戳**，两者分离。

交付物：

- `tools/android_release/public_mobile_release_gate.py`（单文件、纯
  标准库、一切 I/O 经 `monitoring_history.Store` 注入、tmp+fsync+
  `os.replace` 原子写 + 写后字节级重读校验）；
- `tests/android_release/test_public_mobile_release_gate.py`
  （39 项契约测试，全部合成 fixture，评估时刻注入固定 UTC）；
- `tools/android_release/public_device_smoke.py` 的**最小增量**：报告
  顶层新增 `generated_at`（ISO UTC）时间锚——纯加法字段，不改变任何
  探测/判定语义，历史证据不受影响（旧证据从不改写）；
- 本 README 与三本台账（`docs/PROJECT_STATUS.md` / `docs/ROADMAP.md` /
  `docs/CHANGELOG.md`）。

## 输入契约（显式分类；全部显式提供，绝无隐式发现）

| 输入 | 分类 | 消费的既有工具报告 | 本门校验（只消费机器导出字段） |
| --- | --- | --- | --- |
| `--restore-preflight` | machine-report | `tools/ops/production_restore_preflight.py` | schema `aios-production-restore-preflight/2` + tool 自标识 + `generated_at` + `verdict=healthy`（`restore-required`/`blocked` 均为 blocker——发布门语义：栈不在场时公网移动不可能 ready；同时 `blockers` 非空即浮出） |
| `--edge-preflight` | machine-report | `tools/ops/public_edge_preflight.py` | schema `aios-public-edge-preflight/1` + tool + `generated_at` + `exit_code=0` 且 `summary.failed=0` 且 `mobile_attestation=attested` 三者一致；提取 canonical 端点 host 供跨报告一致性检查 |
| `--device-smoke` | machine-report | `tools/android_release/public_device_smoke.py`（其输出目录的 `report.json`） | `schema_version=1` + tool + `generated_at` + `status=passed`；**其自带 `evidence.files` 清单与同目录实际文件逐字节 SHA-256 复核**（文件名拒绝路径穿越） |
| `--cloudflare-preflight`（可选） | machine-report | `tools/ops/cloudflare_ingress_preflight.py` | schema `aios-cloudflare-ingress-preflight/1` + `status=pass`（execute 真实通过）+ `zone_name` 与 edge 报告全部公网 host 归属域一致（`host == zone` 或 `host.endswith("."+zone)`）。**本门只消费该工具已脱敏的报告 JSON，绝不读凭据文件**——可选凭据预检不变成秘密读取 |
| `--release-evidence` | evidence-status | **仓库既有权威导出器**二选一（审计于本切片，精确白名单，拒绝任意其他 tool 字符串——不发明未来导出器）：① `provider-smoke-aggregate`（`services/api/app/ops/provider_smoke_evidence.py`）：tool `provider-smoke-evidence` + schema_version `provider-smoke-evidence-v1` + gate `provider-smoke`，voice/search/llm 三槽位全部 `executed=true` 且 `result=pass`；② `release-check`（`services/api/app/ops/release_check.py`）：tool/gate `release-check` + `all_green=true` | 白名单外 tool、schema/gate 错位、槽位未执行/失败、`all_green` 非 true 一律 blocker |
| `--attestation` | human-attestation | 受约束人工签认（非自声明） | `schema_version=1` + `kind=public-mobile-attestation` + `observed_by` + `observed_at`/`valid_until`（ISO UTC）+ `conclusion=pass` + `covered_items ⊇` 五项必需（`mobile-4g5g-open` 实网 4G/5G、`mobile-cross-origin-cookie` 跨源 cookie、`mobile-voice-connect` 真实语音、`mobile-turn-relay` TURN relay、`mobile-android-apk` APK 下载安装）+ `evidence_files` **至少一项与本门实际消费的输入文件 (basename, SHA-256) 全等锚定**——锚不上（自声明）或哈希不符即 blocked |

## 判定与时间语义（评估时刻为锚；generated_at=评估时间、frontier=证据前沿）

- **评估时刻 `evaluation_time`**：CLI 为真实当前 UTC（`run_gate` 库调用
  显式注入——契约测试用固定 UTC，杜绝日期相关测试）。报告顶层
  `generated_at` = 评估时刻（真实评估时间）；`reference.evidence_frontier`
  = 全部可解析**机器**报告 `generated_at` 的最大值（`Z` 与 `+00:00`
  两种 ISO UTC 尾形均接受；非 UTC 偏移拒绝）。人工观察时间不参与前沿。
- 三重新鲜度（全部以评估时刻为锚，`--freshness-hours` 默认 24，1–720
  可调，容差 300s）：
  1. 每份机器报告时间戳距 `frontier` ≤ freshness → 超限
     `stale-input:*`；
  2. 机器时间戳 > 评估时刻+300s → `future-timestamp:*`（真实未来时间
     拒绝）；`评估时刻 − frontier` ≤ freshness → 超限 `stale-frontier`
     （防止整批陈旧证据互相背书绕过新鲜度）；
  3. `attestation.observed_at` ≤ 评估时刻+300s（违者
     `attestation-invalid:observed-in-future`）、距评估时刻 ≤ freshness
     （超限 `attestation-stale`）、`valid_until ≥ 评估时刻`（否则
     `attestation-expired`）。
- 时间戳 malformed/缺失的机器输入自带 blocker（`timestamp-missing:*`
  /`timestamp-format:*`），**不参与前沿计算也不可能放行**——不存在绕过
  路径（契约测试显式覆盖）。
- 缺失/损坏（malformed JSON/非对象/空文件）/schema 错/tool 错/时间戳缺
  失或格式错/状态非通过（blocked/partial/failed/pending/槽位 fail/
  all_green 非 true）/smoke 内部证据哈希不符/attestation 锚定失败/
  cloudflare-zone 与 edge-host 矛盾——**一律 blocked**（可见结论：报告
  照写、blockers 与下一步只读命令逐条列出，exit 1）。
- 拒绝（exit 2、零写入）：CLI 参数缺失/越界、评估时刻非 tz-aware、
  输入路径为 symlink/Windows reparse point/非常规文件（复用
  `tools/android_release/path_safety.py` 的可移植 reparse 检测与
  `monitoring_history.reject_symlinked_path` 的祖先链检查）、连一个
  机器时间锚都无法解析（无法定义证据前沿）、输出写出或重读校验失败
  （失败时清理半成品，不留部分输出）。
- 输出：`<output>/public-mobile-release-gate.json` 与 `.md`（原子写 +
  写后字节级重读校验）；每个输入条目记录
  classification/present/basename/bytes/SHA-256/schema/tool/时间戳/
  reasons——**绝不携带文件内容、绝对路径、密钥或 env 值**。

## 与 release authorization 的保守分离

`public_mobile_ready=true` 只表示上述证据在同一证据前沿下全部满足。输出
固定携带 `release_authorization.human_release_approval="not-asserted"` 与
`release_authorization.production_readiness="not-asserted"`——**本门绝不
伪造人工发布批准或整体生产就绪**；相关结论由各自的既有 ledger/流程持有。

## 诚实边界

- 本门是聚合器：结论质量受限于输入证据的质量与新鲜度；它不预示前沿
  之后的任何时点状态——任何时点状态须由各子工具重新只读导出后再聚合。
- 未提供 cloudflare 报告时不做该维度校验（可选项缺失不是 blocker）；
  提供即严格校验。
- 既有 `public_edge_preflight` MANUAL_CHECKLIST 中的
  `mobile-exam-flow` / `mobile-harmony-pwa` 不属于本门必需覆盖项（本门
  范围是公网移动发布五项核心观察）；attestation 可以超额覆盖，缺项只按
  五项判定。

## 变更面（change surface）

- 新增 `tools/android_release/public_mobile_release_gate.py`、
  `tests/android_release/test_public_mobile_release_gate.py`、本 README；
- `tools/android_release/public_device_smoke.py`：+2 行（datetime import
  与报告顶层 `generated_at` 字段），零探测/判定语义变化；
- 三本台账追加条目（不改写旧证据）。

## Verification（全部合成 fixture；零网络/设备/Docker/secret）

工作目录：worktree `ai-learning-os-worktrees/m14-237-public-mobile-release-gate`，
分支 `ops/m14-237-public-mobile-release-gate`，基于
`e96640ab6d0780b07c5072c6db52c3cf62d1ec95`（PR #323 merge commit）。
pytest basetemp 置于仓库外
`D:\AI Learning OS\.pytest-tmp\m14-237-public-mobile-release-gate\`。

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 聚焦测试 | `python -m pytest tests/android_release/test_public_mobile_release_gate.py -p no:cacheprovider --basetemp=<repo外basetemp> -q` | 39 passed |
| 邻居回归（android_release 全包） | `python -m pytest tests/android_release/ -p no:cacheprovider -q` | 298 passed, 4 skipped |
| 邻居回归（ops 全包） | `python -m pytest tests/ops/ -p no:cacheprovider -q` | 229 passed |
| 全 tests 树 | `python -m pytest tests/ -p no:cacheprovider -q` | 1621 passed, 6 skipped |
| ruff | `ruff check <三个变更 py 文件>` | All checks passed |
| py_compile / compileall | `python -m py_compile ...` / `python -m compileall -q ...` | OK |
| `git diff --check` | `git diff --check <base>..HEAD` | 干净（无空白错误） |
| secret / 本机绝对路径 / U+FFFD 扫描 | 对新增/修改 diff 的 grep 扫描 | 干净（详见最终交付报告） |

契约测试覆盖矩阵（39 项）：全 pass ready（generated_at=评估时刻、
evidence_frontier=机器前沿，语义分离）+ 原子输出重读、可选
cloudflare 通过、缺 attestation 文件 blocked+下一步命令、零机器时间锚
拒绝零写入、malformed JSON blocked、schema/tool 错、attestation 哈希
错+未锚定、smoke 内部证据篡改、attestation 未来时间（相对评估时刻）/
陈旧/过期/窗口可调、机器未来时间戳、stale 输入、stale-frontier（整批
陈旧互相背书不放行）、malformed 时间戳不可绕过、naive 评估时刻拒绝、
状态 blocked/pending/槽位 fail/all_green 非 true、restore blockers 浮出、
cloudflare zone 矛盾/子域匹配、attestation 缺项/结论 fail、release
evidence 白名单（任意 tool 拒绝 / schema·gate 错位 / 槽位未执行 /
release-check 契约接受与拒绝 / next-action 指向真实导出器命令）、
下一步命令路由到子工具（参数取自既有报告）、symlink 拒绝（fake store
结构性证明）、目录输入拒绝、写失败/重读校验失败拒绝且无半成品、CLI
缺参 fail-closed、freshness 越界拒绝、CLI blocked 打印 blockers（真实
评估时刻 now 锚定、日期无关）、源码静态断言无 network/subprocess/
env/secret 面。

## 与既有证据的关系

不改写、不弱化、不取代任何既有证据（M14-160/163/166/180/182/186/198/
202/204/205/214/226/227/229/231/234/235/236 等照旧成立）。本门是它们
之上的**只读聚合层**：每份输入证据在其自己的观察时间为真；本门只在
同一证据前沿下判定"这些证据是否共同支撑 public mobile ready"。
