# M14-87：audit-chain-anchor current gate 闭合（WORM 在线 + 离线副本核验刷新）

- 切片：分支 `ops/m14-87-audit-chain-current-gate`（独立 worktree
  `m14-87-audit-chain-current-gate`，真实执行/代码基点 = main
  `ddcaa229d308a8e5a46e46dae3a9d7a10ac6640e`（PR #173 merge，M14-86 合入后
  的当时 current main，精确基点——本 README 全部核验/测试/证据均在该树上
  真实执行）），单次本地 commit（不推送、不建 PR）；该提交后分支已 rebase
  到 current main `5ff1e685c52f3111efa3d508b17ca16558e72171`（PR #174
  merge，M14-84 harmony 切片合入后）——rebase 仅解决三台账与 M14-84 的
  docs 冲突（倒序排序与前一任务结构）并同步本 README 基点措辞，零代码
  变更、零生产/WORM/离线核验重跑、canonical raw 证据零改动（证据时点
  2026-09-21T18:15Z 不变；rebase 后仅复跑 docs 级验证：冲突标记零残留、
  canonical JSON 解析、tests/test_release_readiness.py、git diff --check）。
- 目标：按 M14-83 §6.2/§7.5 遗留的精确计划，对 current main 诚实闭合
  **audit-chain-anchor 发布门**——用仓库既有工具真实重跑四步核验
  （DB 链只读校验、锚定 verify-only、M14-43 WORM 在线核验、M14-49 离线
  副本核验），并由**断言驱动的可审计脚本**（归档于 canonical raw/）从
  真实报告逐键程序化组装门证据；绝不手改 gate JSON、绝不合成 pass、
  绝不搬运 m14-75 旧证据冒充 current、绝不伪称 production readiness
  （`release_ready=false` 全程不变，readiness exit 1 如实保留）。
- 零生产突变（本切片全程）：零容器启停/重建/删除/部署（七容器 StartedAt
  全程 2026-09-21T04:29:17–22Z 未动，§3.7 对照）；零计划任务操作；零
  FunASR/CosyVoice/Ollama/WSL/代理进程触碰；生产 DB 只读（SELECT 侧
  链校验）；**MinIO 只读**（head/get 版本定向读，零 put——本切片不归档
  新对象，锚定 head 无前进故既有归档即 current）；零锚文件写入
  （verify-only，`written=false` 输出复核）；零 soak 历史/发布审批接触；
  **未读取 `infra/env.production-recovery` 或任何 secret 文件**，零
  key/token/secret 值回显（WORM 凭据取自 compose 第 66–67 行公开默认值，
  经 `AIOS_AUDIT_ARCHIVE_ACCESS_KEY`/`AIOS_AUDIT_ARCHIVE_SECRET_KEY`
  环境变量注入，值不出现在命令行与输出——M14-43 工具契约本身即如此
  要求）。

## 1. 基点事实（全部可复核）

| 项 | 值 | 来源 |
|----|----|------|
| 基点 commit | `ddcaa229d308a8e5a46e46dae3a9d7a10ac6640e`（PR #173 merge） | git |
| 生产 compose project | `aios-m14-03-production-rehearsal`（七容器全 healthy） | `docker ps`（只读） |
| canonical 锚文件 | `.verify/artifacts/m14-42-audit-chain-anchor/audit-anchor.jsonl`（354 bytes，sha256 `d2bfd877…73aa4e`，1 锚点 sequence 0） | 主仓 `.verify` |
| WORM 对象 | bucket `aios-audit-worm`（MinIO `127.0.0.1:9000`，versioning+Object Lock enabled），key `audit-anchor/d2bfd877…/audit-anchor.jsonl`，version `dc704b8d-6ebd-4acb-adb2-2135f89bb703`，COMPLIANCE 至 2036-09-17T19:10:00Z | M14-43 archive 报告 + 本次在线 head/get 复核 |
| 离线副本根 | `G:\AI-LearningOS-Audit-Offline\worm-root-v1`（独立物理盘，marker 字节精确；三文件 manifest 绑定 2026-09-17 verify 报告 `verify-20260917-190909-….json` sha256 `ff2494a8…fc7ddd`） | M14-49 布局 + 本次逐字节重算 |
| 既有 archive 成功报告 | `.verify/artifacts/m14-43-audit-worm-archive/archive-20260917-190858-92c42ed43b4ebf2dedfd0e3d7b287090.json`（status=pass、worm_verified=true、sidecar sha256 `66703e41…dc4b96` 匹配） | 主仓 `.verify`（M14-43 真实归档执行产物，未改动） |

## 2. 安全命令策略（不读 secret 的执行方式）

1. **执行代码 = current main**：worktree 从零环境（`uv venv .venv
   --python 3.12`（CPython 3.12.14）+ `uv pip install -r
   services/api/requirements.txt -r services/api/requirements-dev.txt`，
   直连）。只读工具模块与 tools/ops 两工具均为 main 既有实现，本切片
   零代码改动（docs-only）。
2. **生产 DB 只读**：连接串用 compose 第 100 行公开硬编码值经宿主固定
   loopback `127.0.0.1:5433`（M14-83 §2 同款口径，凭据为 compose 公开
   默认非 secret）。`audit-chain-verify` 与 `audit-chain-anchor
   --verify-only` 均为只读。
3. **WORM 在线核验不经任何 secret 文件**：`AIOS_AUDIT_ARCHIVE_*` 凭据值
   从 `infra/docker-compose.yml` 第 66–67 行（公开默认，tracked 文件
   字面量）程序化提取注入环境变量；`--endpoint http://127.0.0.1:9000`
   合法（loopback 可 HTTP，M14-43 endpoint 策略）。verify 命令本身零
   put/零写——按报告记录 version 定向 head/get + 逐字节 SHA-256 +
   COMPLIANCE/retain-until/content-type/size 精确核验。
4. **M14-43 verify 的 `--report` 契约**：只接受工件目录内裸文件名。
   既有 archive 成功报告 + sidecar 字节精确复制进 worktree 工件目录
   （复制后 sha256 复核一致，主仓原件零改动）——verify 工具核验其
   sidecar 匹配 + source/target/key/对象事实与当前调用一致后放行。
5. **M14-49 verify 的 `--verify-report` 绑定链**：离线根 manifest 是
   确定性函数（锚 + 报告字节 + 报告名），故必须使用与 G: 既有 manifest
   `source_name` 绑定的同一份 2026-09-17 verify 报告字节（主仓原件，
   sha256 与 manifest 记录一致复核）——用任何其他报告（含本次新 verify
   报告）都会因 manifest 逐字节复算不符被 fail-closed 拒绝，这正是工具
   防跨参数副本的设计意图。离线根零写入（verify 只读重算）。
6. **门 JSON 程序化组装**：组装器 `assemble_audit_chain_anchor_gate.py`
   （7506 bytes，归档 canonical `raw/`）从四份真实报告逐键提取，内置
   14 组成功断言 + 跨报告一致性断言（锚点数/对象哈希/桶/key/version/
   retain_until 在 M14-43/M14-49/锚定 stdout 三方全等，锚文件哈希与
   报告 source 块一致），任一断言失败即非零退出、不产出任何文件；
   输出键除头三键自标识（tool/gate/step，readiness `_require_gate_self_id`
   契约）与调用事实（verify_only/companion_file/source_reports 规范路径）
   外全部为源报告键逐字复制，与 m14-75 运维侧组装同形但组装过程可由
   raw 文件逐键比对复核（M14-83 §3.3/M14-86 §2.1 同款纪律）。

## 3. 执行记录（命令与结果，全部真实可复跑；UTC 时间）

### 3.1 audit-chain-verify（DB 链只读校验）

```bash
export DATABASE_URL="postgresql+asyncpg://aios:aios@127.0.0.1:5433/ai_learning_os"
cd <worktree>/services/api
<worktree>/.venv/Scripts/python.exe -m app.ops.cli audit-chain-verify --json
# exit 0
```

结果：`valid=true`、`entries=0`、`audit_rows=0`、`problems=[]`（sha256
算法）——生产审计链仍为空链（与 m14-75/M14-83/M14-85 一致，生产运行
未产生审计事件；输出无时间戳字段故与 M14-83 raw 同名文件
byte-identical，属确定性输出预期）。原始 stdout 归档
`raw/audit-chain-verify.json`。

### 3.2 audit-chain-anchor --verify-only（锚交叉校验，零写入）

```bash
<worktree>/.venv/Scripts/python.exe -m app.ops.cli audit-chain-anchor \
  --anchor-file "D:/AI Learning OS/ai-learning-os/.verify/artifacts/m14-42-audit-chain-anchor/audit-anchor.jsonl" \
  --verify-only --json
# exit 0（首次缺 DATABASE_URL 被 fail-closed 拒绝——工具要求 DB head
#        交叉核对所需的只读连接，补 env 后通过，如实记录）
```

结果：`status=up-to-date`（DB head sequence=0 / head_hash 全零 genesis
与最后锚点一致）、锚文件 1 锚点（sequence 0，anchor_hash
`59c672b9be0ea82…af627c02`）、**`written=false`、`problems=[]`**。
原始 stdout 归档 `raw/audit-chain-anchor-verify-only.json`。

### 3.3 M14-43 WORM 在线核验（真实 MinIO 对象锁复核）

```bash
export AIOS_AUDIT_ARCHIVE_ACCESS_KEY=<compose 第 66 行程序化提取，值不回显>
export AIOS_AUDIT_ARCHIVE_SECRET_KEY=<compose 第 67 行程序化提取，值不回显>
<worktree>/.venv/Scripts/python.exe tools/ops/audit_anchor_archive.py verify \
  --anchor-file "D:/AI Learning OS/ai-learning-os/.verify/artifacts/m14-42-audit-chain-anchor/audit-anchor.jsonl" \
  --endpoint http://127.0.0.1:9000 --bucket aios-audit-worm \
  --report archive-20260917-190858-92c42ed43b4ebf2dedfd0e3d7b287090.json
# exit 0（2026-09-21T18:15:02–03Z）
```

结果（新报告 `verify-20260921-181503-e319ffd5c020241d488298086483a667`）：
**`status=pass`、`worm_verified=true`、`problems=[]`**——核验链全过：archive
报告 sidecar 匹配（sha256 `66703e41…dc4b96`）、source 块与当前锚文件
重算事实一致、endpoint host/bucket/key 一致、bucket versioning+Object
Lock enabled、按报告 version `dc704b8d…` 定向 head/get：对象字节
SHA-256 = 锚文件 `d2bfd877…73aa4e`、COMPLIANCE、retain_until
2036-09-17T19:10:00Z、content-type `application/x-ndjson`、size 354。
三件套（json/md/.json.sha256）归档 canonical `raw/`。

### 3.4 M14-49 离线第二副本核验（G: 独立物理盘）

```bash
<worktree>/.venv/Scripts/python.exe tools/ops/audit_worm_offline_copy.py verify \
  --anchor-file "D:/AI Learning OS/ai-learning-os/.verify/artifacts/m14-42-audit-chain-anchor/audit-anchor.jsonl" \
  --verify-report "D:/AI Learning OS/ai-learning-os/.verify/artifacts/m14-43-audit-worm-archive/verify-20260917-190909-84587d5101ad3daea43472cd03938890.json" \
  --offline-root "G:/AI-LearningOS-Audit-Offline/worm-root-v1"
# exit 0（2026-09-21T18:15:50Z）
```

结果（新报告 `verify-20260921-181550-365f36e6ad6b51ecfc0f0626a580cfef`）：
**`status=pass`、`offline_verified=true`、`problems=[]`、existing
`state=matching`**——2026-09-17 verify 报告字节（sha256 `ff2494a8…fc7ddd`
与 manifest 记录一致复核）绑定当前锚文件事实全过，离线根 marker 契约
通过，三文件（audit-anchor.jsonl / verify-report.json / manifest.json）
与确定性期望逐字节一致。离线根零写入。三件套归档 canonical `raw/`。

### 3.5 门证据程序化组装（断言驱动，非手工拼装）

```bash
<py> "D:/AI Learning OS/ai-learning-os/.verify/artifacts/m14-87-audit-chain-current-gate/raw/assemble_audit_chain_anchor_gate.py"
# assembled evidence/audit-chain-anchor.json (1418 bytes)
# companion evidence/audit-anchor.jsonl (354 bytes, sha256 d2bfd877…73aa4e)
# exit 0（任一断言失败即非零退出、不产出）
```

产出（m14-75 同形、时间戳为本次真实核验时点）：
`evidence/audit-chain-anchor.json`（1418 bytes，sha256 `a8c54c5e…77b816a`）
——chain 块（valid/entries=0/audit_rows=0）← §3.1 stdout 逐键；anchor 块
（up-to-date/1 锚点/last_anchor_hash `59c672b9…af627c02`/written=false）←
§3.2 stdout 逐键；worm 块（archived=true + 对象事实 + **online_verified_at
`2026-09-21T18:15:03Z` / offline_verified_at `2026-09-21T18:15:50Z`** +
source_reports 指向本切片两份新报告 canonical 路径）← §3.3/§3.4 报告
逐键。伴生 `evidence/audit-anchor.jsonl` 为 canonical 锚文件逐字节副本
（companion，readiness 侧独立校验锚链自洽 + 锚点计数对账）。

### 3.6 release-readiness 聚合（M14-87 canonical 证据目录）

```bash
<py> -m app.ops.cli release-readiness \
  --evidence-dir "D:/AI Learning OS/ai-learning-os/.verify/artifacts/m14-87-audit-chain-current-gate/evidence" \
  --json --output <worktree>/artifacts/m14-87-gate/release-readiness.json
# exit 1（release_ready=false，按任务要求如实保留）
```

manifest（generated_at 2026-09-21T18:17:19Z）：

| 门 | required | 状态 |
|----|----------|------|
| **audit-chain-anchor** | ✓ | **pass**（链 valid（0 entries）+ 锚定 up-to-date（1 锚点）+ WORM 已归档；锚文件副本 1 锚点校验自洽；证据 sha256 `a8c54c5e…77b816a` + supporting `d2bfd877…73aa4e`） |
| ci-main / release-check | ✓ | missing（代码绑定门——M14-86 canonical（@ f1dfcbb，run 35632399209 + release-check 10/10）为最新真实记录，对 `ddcaa22`（docs-only 增量）未重推导，非本切片 scope，如实 missing 不搬运） |
| production-preflight / legacy-papers / draft-ownership | ✓ | missing（生产状态门——M14-83 canonical（@ 5829ad9，preflight 5/5、治理两门归零）为最新真实记录，本切片 scope 只 audit-chain-anchor） |
| backup-restore | ✓ | missing（M14-85 canonical（@ f47a1e4，verified=true 回灌 32 行）为最新真实记录） |
| provider-smoke | ✓ | missing（M14-83 canonical 记录 blocked（search/llm fail）——需运维窗口恢复 SearXNG 出站与 Ollama 后重跑） |
| long-soak | ✓ | missing（24h 审计未发生；M14-79 权威 soak 锚最早审计时点 2026-09-22T15:00:01Z 届满后由 soak_stability_audit 真实判定） |
| release-approval | ✓ | missing（human-only，从未发生，不代拟） |
| turn-tls | optional | missing（公网语音发布形态才必需） |

**pass=1 / blocked=0 / required missing=9 / optional missing=1；
malformed=0、tampered=0；`release_ready=false`、exit_code=1**——本切片
不授权任何部署。M14-87 证据目录只放本切片真实重推导的 audit-chain-anchor
门（evidence 两件 + readiness 报告），unrecognized=0。

### 3.7 演练后生产健康复核（对照 §1 基线）

`docker ps` + `docker inspect StartedAt/RestartCount`（18:2xZ）：七容器
ID/镜像/StartedAt 与本切片开始前逐项相同（均为 2026-09-21T04:29:17–22Z，
minio restarts=0）——**零重启、零重建**，全部 healthy；归档
`raw/production-health-post-verify.txt`。

## 4. 聚焦验证与静态检查（真实执行结果）

- 聚焦契约测试（本切片证据链所依赖的全部工具面，worktree venv）：

```bash
cd services/api && <worktree>/.venv/Scripts/python.exe -m pytest -q \
  tests/test_release_readiness.py tests/test_audit_chain.py \
  tests/test_audit_chain_anchor.py tests/test_audit_anchor_archive.py \
  tests/test_audit_worm_offline_copy.py
# 410 passed, 3 skipped in 13.33s
```

（3 skipped = 真实 PG 端到端门控项——未设 `AIOS_PG_TEST_URL` 时按设计
skip；真实 PG/MinIO/G: 全链路已由本切片 §3 在生产只读面上真跑。）

- canonical 全部 JSON `json.load` 解析通过；release-readiness 消费即契约
  校验（malformed=0/tampered=0）；两份新 verify 报告的 `.json.sha256`
  sidecar 与报告实际字节哈希逐一匹配复核。
- 组装器 ruff 检查通过（`ruff check` 零告警；本切片 tracked Python 零
  改动，组装器为归档证据件）。
- `git diff --check` 干净（docs-only）。
- 分支卫生：单 commit 后 tracked-clean（worktree 侧 `artifacts/` 与
  `.verify/` 生成现场 gitignored 不入库）；未 push、未建 PR。

## 5. canonical 证据清单（主仓 gitignored `.verify`，不入 git；本 README 为唯一入库证据文件）

路径：`.verify/artifacts/m14-87-audit-chain-current-gate/`（13 文件）

| 文件 | bytes | sha256 |
|------|-------|--------|
| `evidence/audit-chain-anchor.json` | 1418 | `a8c54c5e316ea573ab10532a6f234eb60cba8463f724b6e52b2b14f2877b816a` |
| `evidence/audit-anchor.jsonl` | 354 | `d2bfd877aa94632e6f68932a4d4bef8d963a6eb61aca12de6429c5fb7873aa4e` |
| `evidence/release-readiness.json` | 6858 | `831c44cafb20c3f13881c34fa1bbd40ace7f3a3e8c5f5a48382bcc040efcb76a` |
| `raw/assemble_audit_chain_anchor_gate.py` | 7506 | `7fc58087ae9cc6f400719d3bf14a37bc2ddb4e2e40931f977c1149f5f29c288b` |
| `raw/audit-chain-anchor-verify-only.json` | 705 | `19eb43df04d08b40b92668b219442aee0631e1f0460786e61383172ec09c9c58` |
| `raw/audit-chain-verify.json` | 105 | `129b2ef0eac7910930535cd75decbe24ba4f460f554343753de39789b6c86458` |
| `raw/production-health-post-verify.txt` | 1259 | `6415ea420fc57cdb7d68b87eb789065f1c35891d3b387ae5d3ccabc08d3cdf12` |
| `raw/verify-20260921-181503-e319ffd5c020241d488298086483a667.json` | 1591 | `b5e84841c6715249dd8eea774e602dc6b52ddce071e298da9ecc014d6f856524` |
| `raw/verify-20260921-181503-e319ffd5c020241d488298086483a667.json.sha256` | 127 | `dd072e178040a0adecba080a754af52743b4f68bfa650228e822c91c4e469f66` |
| `raw/verify-20260921-181503-e319ffd5c020241d488298086483a667.md` | 1103 | `96fce932b36143dbbebeb73c0fb8731cb93ac07316dad703307660082fc7e4be` |
| `raw/verify-20260921-181550-365f36e6ad6b51ecfc0f0626a580cfef.json` | 1984 | `1c7a44d3cb39cfc10021e67cf2455f9110b8f4d1c4c462c3860ba39f4cf068f0` |
| `raw/verify-20260921-181550-365f36e6ad6b51ecfc0f0626a580cfef.json.sha256` | 127 | `9e6b8b487736c2ff904391876bb84882db1a0243720ddc09f61108ddb8b23da7` |
| `raw/verify-20260921-181550-365f36e6ad6b51ecfc0f0626a580cfef.md` | 1422 | `c8e2ace47a2dbe504ab45c44498098b6c5ae531b28105c68c5e44779e90dd2c3` |

（worktree 侧 `artifacts/m14-87-gate/` 与 `.verify/artifacts/m14-43…/`、
`m14-49…/` 为生成现场，gitignored；canonical 证据为工具产物逐字节复制/
程序化组装，哈希逐一复核；主仓既有 M14-42/M14-43/M14-49 历史工件与本切片
生成现场均未改动。）

## 6. 诚实边界与剩余阻塞（不伪称，逐项可执行收口）

1. **本门证据是对核验时点（2026-09-21T18:15Z）的事实快照**：worm 块的
   online/offline verified_at 证明「该时点在线对象与离线副本逐字节完好」，
   不代表持续完好；生产后续产生审计事件（DB head 前进）后需重新锚定 +
   归档 + 本切片流程重跑，届时本证据对新的 head 不再成立（如实）。
2. **锚定 head 无前进故零归档是合法形态**：生产审计链 entries=0（空链）
   与既有锚点 sequence 0（genesis head）一致——无需追加锚点、无需归档
   新对象；WORM bucket 中既有对象即 current 归档。若未来 head 前进，
   锚定（写操作）+ 归档（supervisor 获准窗口）是独立运维动作，超出
   本切片只读边界。
3. **M14-49 离线副本绑定的是 2026-09-17 verify 报告字节**（工具确定性
   manifest 契约）：其证明力 = 「该报告所记录的归档事实 + 当前锚文件 +
   离线副本三方一致」；在线对象今日完好的独立证明由 §3.3 新 M14-43
   verify 报告给出（时间戳 2026-09-21T18:15:03Z），两份报告共同构成
   worm 块证据链。若要离线副本绑定今日新报告，需在获准窗口重跑 M14-49
   `copy`（写离线根，本切片不做，G: 零写入）。
4. **其余九个 required 门 + optional turn-tls 在 M14-87 目录仍 missing**
   （§3.6 表）：各门最新真实记录见 M14-83/M14-85/M14-86 canonical（对
   current main `ddcaa22` 已 stale 如实——ddcaa22 相对 f1dfcbb 为 docs-only
   增量，但各门证据只对其执行时点成立，不以「仅文档」为由搬运冒充）；
   provider-smoke 恢复（SearXNG 出站 + Ollama）、long-soak 届满审计、
   release-approval 人工审批为发布前剩余运维/人工动作。
5. **release-approval 从未发生**：human-only，不触碰、不代拟。
6. `release_ready=false`、`production_ready=false` 不变；生产仍运行
   **m14-70 镜像**（2026-09-19 切换后未再变更）；本切片对生产的全部
   访问为只读（DB SELECT 侧链校验 + MinIO head/get + docker ps/inspect），
   不授权任何部署。
