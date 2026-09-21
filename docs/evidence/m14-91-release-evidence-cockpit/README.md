# M14-91：release-evidence-cockpit 跨切片证据驾驶舱（实现/文档切片）

- 切片：分支 `ops/m14-91-release-evidence-cockpit`（独立 worktree
  `m14-91-release-evidence-cockpit`，基于 main
  `650b02dbf2372afb4617fd6b77b01ecf0f46382f`（PR #177 merge，精确基点）），
  单次本地 commit（不推送、不建 PR）。
- 目标：降低发布门证据的人工拼装风险。M14 系列各证据切片的 canonical 门
  证据分散在各 worktree 的 gitignored `.verify/artifacts/<slice>/` 目录，
  发布前的门拼装长期依赖人工复制与一次性脚本（M14-83 §3.3 转录、M14-86
  §2.1 derive_ci_main、M14-87 §3.5 组装器）——本切片把该流程固化为仓库内
  确定性工具 `evidence-cockpit`（零 Harmony 文件）。
- 零生产触碰（本切片全程）：零容器/DB/MinIO/语音/计划任务/进程接触、零
  secrets/env.production-recovery 读取、零部署、零 release-approval 接触。
  全部验证只读既有 canonical 证据文件 + 临时/staging 目录（gitignored）。

## 1. 实现内容（全部 current main 既有模式复用）

### 1.1 新模块 `services/api/app/ops/evidence_cockpit.py`（约 420 行）

只读聚合器 + 一次性 staging，六项任务要求逐条落位：

1. **显式输入**：逐门声明 canonical 证据文件（`--gate-source GATE=PATH`
   可重复）；未知门/重复声明 fail-closed；**release-approval 传入即拒绝**
   （`FORBIDDEN_GATES`——审批 human-only，驾驶舱不是审批人）。
2. **来源登记零改动（单次读盘纪律）**：每源文件字节**恰好读取一次**，
   JSON 解析（模块内 `_parse_json_object`，与共享层同语义但消费已读
   bytes）、gate 自声明校验、来源登记（字节/SHA-256）与后续 staging
   **全部消费同一 bytes 对象**——解析通过的版本与落盘的版本不可能来自
   不同文件状态（TOCTOU 防护，回归测试以 read_bytes 计数证明单次读）；
   symlink/非常规文件拒绝（复用 evidence_kit `check_regular_file`）；
   source 必须是合法 JSON 对象且**自声明 gate 与声明的 gate_id 一致**
   （错位拒绝——evaluator `_require_gate_self_id` 防线前移）。
3. **字节一致一次性 staging（失败即清理）**：按 evaluator 的 gate→文件名
   映射（`GATE_FILE_OF` ← `GATES`）原子写（tmp + os.replace）进**必须不
   存在**的新目录；写后重读断言；`--anchor-companion`（audit-anchor.jsonl）
   同规则 stage；绝不覆盖既有内容、staging 内绝不出现 release-approval.json。
   staging 目录创建后任何失败（写入/evaluator/报告落盘）都会移除**本次
   新建目录**（含 .tmp 残留，`shutil.rmtree`）；清理本身失败时改抛携带
   残留路径的事实性错误——**绝不虚称零 staging 产出**（回归测试注入
   写入中途 IO 失败，断言失败且目录不留）。
4. **复用既有 evaluator**：对 staging 目录原样 `run_release_readiness`——
   门语义/malformed/tampered/unrecognized 全由既有工具承载，本模块零重复
   实现。
5. **code-bound vs production-state + stale 判定**：`CODE_BOUND_GATES =
   {ci-main, release-check}`（证据只对执行时点的树成立——M14-81/86/90
   口径），其余为 production-state（绑定生产状态执行时点）。声明来源 =
   文件内嵌（ci-main `merge_commit`）或显式旗标（`--gate-declared-head`，
   声明该证据执行/绑定的树）；两处同时且不一致 → fail-closed。声明 ==
   current HEAD → current；≠ → stale；无 → undeclared。**stale 一律计入
   blocker**；code-bound undeclared 计入 blocker（保守）；production-state
   undeclared 只如实呈现不计 blocker（M14-83/85/87 canonical 均无内嵌
   commit 载体，其效力绑定生产时点而非代码树——是否随 main 前移失效由
   supervisor 判断，本工具不代答）。未 stage 门 not-staged。
6. **审批与生产就绪硬边界**：永不接受/stage/生成 release-approval（其
   not-staged 按策略呈现、**不计入 blocker**——工具不是审批人）；
   `production_ready` 恒 false；evaluator `release_ready` 因 approval 恒
   missing 而恒 false，原样透传。

**cockpit_ready 语义（supervisor rework 收紧）**：不是「staged 子集
干净」——**required 门未 stage 即阻断**（`<gate>:not-staged-required`
blocker + 报告 `required_not_staged` 列表 + 摘要显式标注「required 未
stage → blocker」）；唯一例外 release-approval（按策略永不接受，非
blocker）；turn-tls 是 optional 门（本机/LAN 发布范围不阻断，摘要显式
标注——公网语音发布需另行要求 turn-tls=pass）。

退出码：cockpit_ready=true=0（全部必需门（approval 除外）staged 且
pass 且无 stale/undeclared-code-bound/not-staged-required blocker）；
有 blocker=1（报告照常产出，如实记录缺口）；输入/路径/护栏=2
（fail-closed，零 staging 产出——staging 目录仅在全部 source 校验通过
后才创建；创建后失败则整目录清理）。

### 1.2 CLI 子命令 `evidence-cockpit`（`services/api/app/ops/cli.py`）

`--gate-source/--current-head/--staging-dir/--gate-declared-head/
--anchor-companion/--json/--output`（output 复用 `is_safe_artifact_path`
gitignored artifacts/temp 护栏 + 原子落盘 + 已存在拒绝覆盖）；无 --yes
执行形态（纯汇总器）。dispatch 插在 release-readiness 之后。

## 2. 聚焦测试 `services/api/tests/test_evidence_cockpit.py`（23 项）

任务验收面全覆盖：pass/missing/blocked/stale 四态（含 embedded 与 flag
两种 stale、flag/embedded 冲突拒绝、非 40-hex current-head 拒绝）；分类
口径（code-bound undeclared 阻断、production-state undeclared 不阻断、
not-staged）；**required 未 stage 即阻断**（少 stage 一个 required 门 →
`not-staged-required` blocker + `required_not_staged` 列表 + exit 1；
approval 例外与 turn-tls optional 在摘要显式标注）；**单次读盘 TOCTOU
回归**（read_bytes 计数恰为一次——解析/校验/登记/staging 共用同一次
读的字节，中途替换文件不可能分裂解析与落盘版本）；**staging IO 失败
清理回归**（注入至少一次成功写入后的 IO 失败 → 调用失败且本次新建
staging 目录被完整移除）；**来源零改动**（运行前后字节/哈希不变）；
**字节一致 staging**（staged 与 source 逐字节相等 + 文件名集合精确）；
**审批拒绝**（release-approval 传入即 CockpitInputError 且零 staging
产出；报告 approval.accepted 恒 false / production_ready 恒 false /
readiness.release_ready 恒 false）；**malformed**（非 JSON 对象、gate
自声明错位、staging 目录已存在、symlink 源（无特权环境 skip）、未知门）；
anchor companion 字节一致；CLI 注册/dispatch/无 --yes（单门 CLI 端到端
exit 1 + not-staged-required blocker 断言）。全部零网络零 DB。

## 3. 真实 canonical 冒烟（工具价值实证，2026-09-22T05:5xZ 本地）

对五个真实切片 canonical 证据运行（current HEAD = 本切片基点 `650b02d`）：
ci-main + release-check ← M14-90（`--gate-declared-head release-check=
7cca73f…` 按其 README 声明的执行树）；audit-chain-anchor + 伴生锚文件 ←
M14-87；production-preflight / legacy-papers / draft-ownership ← M14-83；
backup-restore ← M14-85；provider-smoke ← M14-88。结果（报告归档
canonical `cockpit-smoke.json`，sha256 `a1c33a23…fca4fd`）：

- **evaluator pass=8 / missing=3（long-soak/release-approval/turn-tls
  not-staged）/ blocked=0 / malformed=0 / tampered=0**；release_ready=
  False（approval 永不接受）。
- **blockers = `ci-main:stale` + `release-check:stale` +
  `long-soak:not-staged-required`**（M14-90 证据 @ 7cca73f 对现 main
  650b02d 已 stale——此前需要 supervisor 多轮人工判断的事实，现由工具
  一条命令诚实呈现；long-soak 为 required 未 stage，按收紧语义如实
  阻断）→ cockpit_ready=False、exit 1。摘要三类区分显式呈现：
  release-approval「按策略永不接受，非 blocker」、turn-tls「optional：
  本机/LAN 范围不阻断」、required 未 stage「→ blocker」。
- production-state 五门 undeclared 如实呈现（不 block，按 §1.1 第 5 条
  口径）。
- **staged 9 文件与 source 逐字节一致**：工具内建写后重读断言 + 外部
  `cmp` 独立复核 9/9 IDENTICAL；staged 哈希与各切片 README 登记值交叉
  一致（audit-chain-anchor `a8c54c5e…` / audit-anchor `d2bfd877…` /
  backup-restore `ed0fc5b4…` / preflight `b3a2be66…` / legacy `83cd62bd…`
  / draft `e90ae04c…` 等，见 canonical `staged-inventory.txt`）——来源
  零改动 + 逐字节 staging 的端到端实证。

## 4. 验证与静态检查（真实执行结果）

- 聚焦 pytest：`tests/test_evidence_cockpit.py` **23 passed**；
  加回归 `tests/test_release_readiness.py`（75）+
  `tests/test_release_closure_manifest.py`（40）→ **138 passed in 1.35s**
  （CLI 新子命令未破坏既有注册/契约面）。
- `ruff check services/api`：**All checks passed**（新增两文件 + cli.py
  改动零告警）。
- `git diff --check`：干净。
- 分支卫生：单 commit 后 tracked-clean（staging/报告在 gitignored
  `services/artifacts/temp/`，不入库）；未 push、未建 PR。

## 5. canonical 证据清单（主仓 gitignored `.verify`，不入 git；本 README 为唯一入库证据文件）

路径：`.verify/artifacts/m14-91-release-evidence-cockpit/`（2 文件）

| 文件 | 内容 |
|------|------|
| `cockpit-smoke.json` | §3 真实冒烟完整 cockpit 报告（返工后语义；sha256 `50608293c449b71cf8b0cef76ef8bc3ad6d998bce9436c106867323cfd816e36`） |
| `staged-inventory.txt` | staged 9 文件 sha256/bytes 清单（外部 cmp IDENTICAL 复核记录） |

worktree 侧生成现场（gitignored）：`services/artifacts/temp/
m14-91-smoke-staging/`（一次性 staging 目录）与 `m14-91-cockpit-smoke.json`。

## 6. 诚实边界与后续

1. cockpit 是**编排/汇总器**：stale 判定只消费「声明元数据 + current
   HEAD」的机械比较，不重跑任何门、不连任何生产面；code-bound 门 stale
   的收口动作（真实重推导）仍是独立 evidence-refresh 切片的工作（冒烟
   所报 ci-main/release-check stale 即当前真实缺口：M14-90 证据对
   650b02d 需刷新）。
2. production-state 门 undeclared 不 block 的口径是**呈现性选择**而非
   有效性判定：这些门对 current 生产状态的效力（尤其 M14-83 三门距
   2026-09-21T16:3xZ 已逾时）由 supervisor 结合生产变更记录判断——工具
   提供声明通道（`--gate-declared-head`）让调用方显式表达。
3. long-soak / release-approval / turn-tls 未 stage（not-staged 如实）：
   long-soak 24h 审计未发生；release-approval human-only 永不接受；
   turn-tls 公网语音发布形态才必需。
4. `production_ready=false` 恒成立；本切片不授权任何部署；生产仍运行
   m14-70 镜像。
