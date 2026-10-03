# M14-224 证据：local 语音恢复诊断切片（M14-222 voice 阻塞的 fail-closed 机器可读恢复指令面）

## 0. 结论与边界

- M14-222（`docs/evidence/m14-222-current-provider-smoke/README.md`）实证了
  provider-smoke-preflight（M14-112）对 local 语音槽位的输出缺口：voice
  `not_ready`/`endpoint_absent` 时 preflight 只能给出每引擎泛化建议
  `start_externally_then_rerun`——**不告诉操作者该走哪条既有生命周期路径、
  前置是什么、恢复后按什么顺序重跑 provider-smoke**。操作者只能人工翻
  M14-04/M14-31/M14-209/M14-218/M14-222 文档拼恢复步骤。
- 本切片交付 `python -m app.ops.cli voice-recovery-diagnostic`：把该阻塞
  转换为**确定性、fail-closed、机器可读**（`--json` 纯 JSON）的恢复诊断
  报告——每引擎 listener 状态 + manifest 事实源只读快照 + 选定恢复路径
  与必需下一步动作（全部闭集枚举）+ provider-smoke 为何仍被阻塞的固定
  因果链 + 恢复后的完整重跑序列（preflight → 三 export → aggregate）。
- 本轮真实运行（默认 loopback 端点，2026-10-03T04:40Z）：ASR 8010 /
  TTS 8011 仍无监听、manifest 均不在场——**M14-222 阻塞形态仍在**，
  双引擎 `controlled_start`、overall `voice_recovery_required`、exit 1
  （如实不通过）。本工具**不执行任何恢复动作**。
- 只读边界：零子进程、零文件写入、零服务生命周期变更（不启动/停止/
  重启 Docker/WSL/ASR/TTS/Ollama/CC Switch/代理/生产服务）、不读环境
  变量/secret（endpoint 经 preflight `_classify_url` 同款校验，userinfo
  fail-closed 拒绝且凭据绝不进入探测与报告）、不执行 WSL `/proc` 探活
  （manifest PID 存活/归属核验让渡给 `voice_service_control status`）、
  不发真实语音请求（仅复用 preflight 的 loopback `/health` 只读 GET
  探测，`trust_env=False`）。
- 本工具 stdout-only、不产生证据：不写 provider-smoke.json、不触碰
  release-readiness 证据；报告恒携带自声明 `production_ready=false`、
  `release_readiness_evidence=false`（测试锁定喂给 release-readiness
  的 provider-smoke 门评估器必须 `MalformedEvidence` 拒收）。listener
  ready 只代表前置可观测就绪，不代表 provider-smoke 已通过。
- 未修改任何既有判定逻辑：`provider_smoke_preflight.py` /
  `voice_service_control.py` / `production_recovery.py` 与三个冒烟脚本
  零改动——新工具只读取、只引用、只推荐。不发明第二套生命周期管理器
  （引擎规格经 importlib 从 `voice_service_control.ENGINE_SPECS` 派生，
  与 `production_recovery` 的 `_load_voice_module` 同款复用模式；探测
  直接复用 preflight `_voice_health_check`，测试交叉锁定两者输出全等）。

## 1. 缺口与设计（复用既有工具与词汇）

| 需求（任务口径） | 实现面 | 复用来源 |
|---|---|---|
| listener 状态（每引擎） | `engines.<slot>.listener` 子报告 | preflight `_voice_health_check`（同 `/health` 契约、同 10s 有界只读探测、同 loopback `trust_env=False`、同 status/reason/recommendation 闭集）——零第二套探测语义 |
| manifest 事实（每引擎，只读） | `engines.<slot>.manifest`：在场/解析/记录端口匹配/PID/启动时间/bootstrap | 生命周期管理器 `voice_service_control.py`（M14-04）的事实源 `artifacts/voice/<engine>/service/manifest.json`；引擎规格（funasr=ASR 8010 / cosyvoice=TTS 8011、artifacts 子目录、bootstrap 脚本名）从工具本体 `ENGINE_SPECS` 派生；工具文件缺席（容器 `/app` 布局，M14-211/M14-212 口径）时 fail-closed 降级 builtin 回退并如实报告 `engine_specs_source=builtin_fallback` |
| 选定配置路径（哪条既有生命周期） | `lifecycle.configured_paths`：`tools/voice/voice_service_control.py`（语音槽位单独恢复——M14-222 当前阻塞形态）与 `tools/ops/production_recovery.py`（整机/整栈重启后恢复，含 `decide_voice_action` 语音调和） | 两条都是既有工具，报告原样引用；`lifecycle.prerequisites` 为固定清单（WSL2 可用、bootstrap 脚本在场、模型缓存/首下耗时预期、恢复后 preflight ready 才允许 local-voice export——M14-218 §5/M14-222 §0 执行纪律） |
| 必需下一步动作 + 为何仍阻塞 | `selected_path`/`next_action` 闭集映射（§2）+ `provider_smoke.blocked`/`why_blocked` 固定因果链 | stopped（无监听无 manifest，M14-222/M14-145 实证形态）→ `controlled_start`（先 status 权威核验再受控 start，与 `production_recovery.decide_voice_action` 的唯一放行动作同轨）；无监听但有 manifest → `status_verification_required`（不盲目 start）；其余 listener 失败 → `external_investigation_required` |
| 恢复后怎么重跑 | `provider_smoke.rerun_sequence`：preflight → local-voice/search/llm 三 export → aggregate | M14-209 口径：全新 UTC 证据目录、三份 export 全部重新执行、不得复用旧 JSON |

不做的面（fail-closed 刻意让渡）：不探活（`liveness_probed` 恒 false——
manifest PID 归属核验是 `voice_service_control status` 的既有语义）；不
清理 stale manifest（status 的既有语义）；不探 search/llm 槽位（那是
preflight 的职责，本工具只诊断 voice 恢复面）；不做第二个生命周期
管理器（builtin 回退仅为容器布局降级的文档化默认值，与真实
`ENGINE_SPECS` 测试交叉锁定零漂移）。

## 2. 恢复路径闭集映射（确定性决策表）

| listener 事实 | manifest 事实 | selected_path | next_action | action_commands |
|---|---|---|---|---|
| `ready` | 任意 | `no_recovery_needed` | `rerun_provider_smoke_preflight` | preflight 命令 |
| `not_ready`/`endpoint_absent` | 不在场 | `controlled_start` | `run_voice_status_then_start` | status → start --engine <funasr\|cosyvoice> |
| `not_ready`/`endpoint_absent` | 在场 | `status_verification_required` | `run_voice_status_only` | status |
| 其余失败（timeout/http_failure/malformed_url/…） | 任意 | `external_investigation_required` | `run_voice_status_and_inspect_log` | status（+ `log_hint` 指向 `service/service.log`） |

闭集枚举：`SELECTED_PATHS`（4 值）、`NEXT_ACTIONS`（4 值）、
`OVERALL_STATUSES`（`voice_listeners_ready`/`voice_recovery_required`）、
`MANIFEST_PARSE_STATES`（`ok`/`invalid`）——reason/recommendation 复用
preflight 既有闭集，运行期不拼装新词。manifest 记录端口与请求端口不符
时附 `port_guidance`（口径同生命周期工具自身的 port-mismatch 拒绝提示，
防误杀/双实例）；manifest 损坏时 `parse=invalid`，保持
`status_verification_required`（不降级为 controlled_start——在场事实
就是「先核验」）。退出码：listeners ready=0 / 恢复或核验 required=1 /
参数问题=2（argparse，不做任何探测）。

## 3. 变更清单

| 文件 | 变更 |
|---|---|
| `services/api/app/ops/voice_recovery_diagnostic.py` | 新增（615 行）：诊断模块——preflight 探测复用、manifest 只读事实、闭集决策表、报告组装、人类摘要、退出码；docstring 固化全部诚实边界 |
| `services/api/app/ops/cli.py` | `+82` 行：`voice-recovery-diagnostic` 子命令注册（`--asr-endpoint`/`--tts-endpoint`/`--json`，无 `--yes` 执行旗标——只读面）与分发；既有子命令零改动 |
| `services/api/tests/test_voice_recovery_diagnostic.py` | 新增（670 行，31 项契约测试，零网络零子进程零 WSL）：CLI 注册/分发/exit 2；M14-222 形态核心用例；路径闭集矩阵（ready/manifest 在场/端口不符/manifest 损坏/非 absent 失败）；builtin 回退与真实 `ENGINE_SPECS` 交叉锁定；listener 子报告与 preflight voice checks **全等**交叉锁定（同 FakeGet）；默认端点探测服务根 `/health`、`trust_env=False`、userinfo 凭据零泄漏；ast 源码级只读守卫（零 subprocess/零 os.environ/零文件写入面）+ CLI 处理函数零文件写入 + 测试期零真实 socket；非门证据自声明 + `_eval_provider_smoke` 拒收；重跑序列 M14-209 形态；退出码与确定性（同输入两次 JSON 全等） |

`docs/CHANGELOG.md`/`docs/PROJECT_STATUS.md`/`docs/ROADMAP.md` 同步回填
（措辞不夸大）。

## 4. 官方执行结果

### 4.1 真实运行（live，默认 loopback 端点，零 secret）

| 命令（cwd=本 worktree `services/api/`） | exit | 关键事实 |
|---|---:|---|
| `python -m app.ops.cli voice-recovery-diagnostic --json` | 1 | overall `voice_recovery_required`；asr(funasr)/tts(cosyvoice) listener 均 `not_ready`/`endpoint_absent`（`http_status=null`）；双 manifest 不在场；双引擎 `controlled_start`（status → start --engine）；`provider_smoke.blocked=true`、blockers=[asr, tts]、`why_blocked` 含完整因果链；`production_ready=false`/`release_readiness_evidence=false` 恒定；`engine_specs_source=voice_service_control`（真实生命周期词汇） |
| `python -m app.ops.cli voice-recovery-diagnostic`（人类摘要） | 1 | 同事实的固定文案摘要：RESULT: BLOCKED + 逐引擎选定路径/动作命令 + 重跑序列 + 边界声明 |

真实运行仅对 `http://127.0.0.1:8010/health`、`http://127.0.0.1:8011/health`
发只读 GET（复用 preflight 语义，非真实语音请求——无音频、无转写、
无合成），读取 worktree `artifacts/voice/<engine>/service/manifest.json`
的在场性（不存在，如实报告）。结论：**M14-222 的 voice 阻塞在当前时刻
仍未解除**；本切片不启动任何服务去解除它（任务硬边界），只把恢复路径
变成机器可读指令。

### 4.2 离线演示三形态（零网络：FakeGet 替身，`offline_demo.py` 复现）

| 形态 | 输入替身 | overall | 关键断言 |
|---|---|---|---|
| M14-222 当前阻塞 | 双 `endpoint_absent`、无 manifest | `voice_recovery_required`（exit 1 语义） | 双引擎 `controlled_start`，commands=[status, start --engine …] |
| manifest 在场（M14-145 managed-starting 形态） | asr 有 manifest（port 8010 匹配）、tts 无 | `voice_recovery_required` | asr `status_verification_required`（只推荐 status，不盲目 start）；tts `controlled_start` |
| 双 listener ready（恢复后形态） | 双 `/health` 200 | `voice_listeners_ready`（exit 0 语义） | 双引擎 `no_recovery_needed`；rerun_sequence = preflight → 三 export → aggregate |

## 5. 与既有证据的关系（不搬运旧结论）

- **M14-222**：本切片的阻塞输入。其 §0 的「local-voice/aggregate 诚实
  未执行 + 操作者需人工拼恢复步骤」即本工具要消灭的缺口；本工具的
  live 运行证明该阻塞形态（双 endpoint_absent、无 manifest）至今仍在。
- **M14-112（preflight）**：探测与词汇的复用源。本工具 listener 子报告
  与 preflight voice checks 测试锁定全等；本工具不是 preflight 的替代
  （不探 search/llm、不给 overall pass/fail），是 preflight not_ready 后
  的恢复指令面。
- **M14-04/M14-31（voice_service_control 及受控恢复验收）**：本工具
  推荐的全部 start 动作都指向该工具；`controlled_start` 的「先 status
  权威核验」次序与 M14-31 验收的受控启动路径一致。
- **M14-145（voice-recovery-evidence）**：manifest 在场但无监听的
  `status_verification_required` 分支对应其 managed-starting 形态
  （bootstrap 进行中/stale/归属待核），裁决让渡给 status。
- **M14-209/M14-218**：重跑序列与执行纪律口径（全新证据、三份 export、
  不复用旧 JSON；preflight ready 是 local-voice export 前置）。
- 本轮无新的 `provider-smoke.json`，M14-209 仍是最近一次完整聚合；
  voice 恢复后的完整三输入重跑聚合仍属后续显式切片，不得由本工具
  输出推导。

## 6. 官方证据完整性（bytes + SHA-256）

目录 `<EV>` = `.verify/artifacts/m14-224-voice-recovery-diagnostic/`
（gitignored；同目录 `SHA256SUMS` 索引下列全部文件，索引不含自身）：

| artifact（`<EV>` 相对路径） | bytes | SHA-256 |
|---|---:|---|
| `offline_demo.py` | 3639 | `0b8979c926dd9261dee466c32c6228ce22be1d341344b3992422f1974c46311f` |
| `report-m14-222-shape.json` | 6516 | `5f67b98f022ce3b150d92b4f1d0536667e608fd1c56ffb808b8c6ad05d757774` |
| `report-manifest-present.json` | 6503 | `2d3897f97f975ad4e80d6ec25030a1b3462312ab22c96959889fc11d808ab209` |
| `report-listeners-ready.json` | 5842 | `427c960808a4244c3d871abdcff9f69f841b5b68662f37abdb7bcfa9e5665bdf` |
| `summary-m14-222-shape.txt` | 2250 | `164b5732f065d3ec03580c44ed753457c491276a2eb9ce34f1fab9f3c782cde1` |
| `summary-listeners-ready.txt` | 1114 | `15f16d8434b48b4748640ed405148c4906c90e0725c7fffe98baef526b0e1a75` |
| `run-live.json` | 6419 | `9002e058e6368ab7f5ace940c4fc63d5158143abe255a1ab5717ee232f83eb2a` |
| `run-live.stderr.log` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `summary-live.txt` | 2257 | `e2ffd2edd2640418fa23d8df9558469f2cb245921d70cfa23c9699b1902a5c31` |
| `summary-live.stderr.log` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `manifests-present/funasr/service/manifest.json` | 357 | `07ce96edcaaa33e787c69bb35ba58d5b10df31eece55562f6e1512db69ff526f` |

`report-*.json`/`summary-*.txt` 为 `offline_demo.py`（本切片零网络演示
生成脚本，随证据目录留档可复现）产物；`run-live.*`/`summary-live.*` 为
§4.1 真实运行的 stdout/stderr 归档（`--json` 报告 `generated_at`=
`2026-10-03T04:40:17.170521+00:00`）；`manifests-present/…/manifest.json`
为形态 2 的样例 manifest（写入 gitignored 临时目录，非真实服务记录）。

## 7. 验证与收口

- 聚焦契约测试（主 checkout canonical venv，Python 3.11.15，worktree
  pytest rootdir）：`tests/test_voice_recovery_diagnostic.py` →
  **31 passed**（1.07s，零网络/零子进程/零 WSL——套件内含 ast 源码级
  只读守卫与真实 socket 拨号禁令）。
- 邻居测试：`test_provider_smoke_preflight.py` +
  `test_production_recovery.py` → **118 passed**；
  `test_provider_smoke_evidence.py` + `test_smoke_voice_local_script.py`
  + `test_smoke_voice_cloud_script.py` → **136 passed**（cli.py 注册区
  插入与模块新增零回归）。
- `ruff check`（新模块 + cli.py + 新测试）通过；`git diff --check`
  通过（无空白错误）；新增 tracked 行敏感值扫描干净（仅非敏感 loopback
  端点、工具名与既有文档路径，无凭据类值、无 secret 值）。
- 零服务 stop/start/recreate（Docker/WSL/ASR/TTS/Ollama/CC Switch/
  代理/生产服务）、零生产 DB/secret 访问、零真实语音请求、零 push/
  PR/合并；1 个本地 commit 基于 `465326d2`（PR #310 merge）。tracked
  文件收口干净，原始证据仅存 gitignored `.verify/artifacts/`。
- 后续切片（不由本证据推导）：运维经 `voice_service_control status →
  start` 恢复引擎后，重跑本诊断确认 listeners ready，再按 M14-209 口径
  执行 preflight → 三 export → aggregate 完整三输入重跑。
