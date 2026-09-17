# M14-42 生产审计链初始锚定记录

## 任务性质

- docs-only 回填：本回合零代码 / 零测试 / 零生产操作，只把 supervisor 已于 2026-09-17 执行的生产审计链初始锚定事实入库
- 分支/worktree `docs/m14-42-audit-anchor-record` 基于 `origin/main@8172229`（PR #119 merge）
- 唯一源证据（主仓只读，gitignored 不入库）：`.verify/artifacts/m14-42-audit-chain-anchor/{audit-anchor.jsonl, verify-only.json, production-preflight.json}`

## 工具背景（已在 main，非本回合交付）

- `services/api/app/ops/audit_chain_anchor.py`（M10-06 交付）：治理审计链库外锚定工具——DB 哈希链 head 定期追加到库外 append-only JSONL 锚文件并自身成链，交叉核对可发现持库写权限者整链重算
- 默认 dry-run 只提案不落盘；`--yes` 才写入；`--verify-only` 只读复核（与 `--yes` 互斥）；写入前完整校验既有锚文件 + 全部历史锚点与 DB 交叉核对
- 锚行字段集固定且不含任何敏感值：`schema_version` / `algorithm` / `sequence` / `head_hash` / `anchored_at` / `previous_anchor_hash` / `anchor_hash`
- Alembic 迁移 `0027_audit_chain` 已在 main，生产 current == head

## 生产锚定事实（supervisor 执行，2026-09-17，UTC）

| 时刻 | 操作 | 结果 |
|---|---|---|
| 02:28:14Z | audit-chain-anchor dry-run | valid——DB 链 0 entries / 0 audit rows（全零 genesis），提案 sequence 0 创世锚，未写入 |
| 02:28:50Z | 同命令 `--yes` 写入库外锚文件（生产侧 `/tmp/aios-audit-anchor.jsonl`） | 落盘 sequence 0 创世锚，anchor_hash `59c672b9be0ea82dddd0d01e2f899b217674a4796b4f91f2381900e0af627c02`，anchored_at `2026-09-17T02:28:50.131481+00:00` |
| 02:29:48Z | production-preflight post-migration（只读） | 5/5 pass、0 fail / 0 pending / 0 not_configured，exit 0（工件 generated_at `2026-09-17T02:30:16.065512+00:00`） |

- 创世锚行内容（字段级）：`schema_version=1`、`algorithm=sha256`、`sequence=0`、`head_hash` 与 `previous_anchor_hash` 均为 64 位全零 genesis 常量——与 DB 侧 0 entries 状态自洽
- preflight 五项检查：db-connect（postgresql / ai_learning_os）、alembic current == head == `0027_audit_chain`、audit-chain 只读校验 valid（0 entries / 0 audit rows）、legacy-governance 治理三项计数均为 0（`unowned_non_seed_papers=0`、`course_generation_null_owner_drafts=0`、`variant_question_null_owner_drafts=0`）、audit-anchor verify-only up-to-date（DB head 与最后锚点一致，共 1 锚点）
- 写入后 verify-only 复核快照：status `up-to-date`、锚文件 1 锚点（last_sequence 0）、DB valid、`proposed_anchor=null`、`written=false`、`problems=[]`

## 入库证据

- 本目录 `audit-anchor.jsonl`：生产锚文件原样复制，354 bytes，SHA-256 `d2bfd877aa94632e6f68932a4d4bef8d963a6eb61aca12de6429c5fb7873aa4e`（与源逐字节一致；该文件只有 schema/algorithm/sequence/hash/time 字段，无敏感值，故按任务授权入库）
- 哈希验证口径：上述 SHA-256 对应 LF 行尾字节形态——即 git 对象内容（`git cat-file -p HEAD:docs/evidence/m14-42-audit-chain-anchor/audit-anchor.jsonl | sha256sum` 可复算）与当前工作区形态；仓库 `core.autocrlf=true` 下未来 Windows checkout 可能把工作区副本转为 CRLF 致本地 `sha256sum` 不同——彼时以 git 对象内容为权威（与 M9-04/M10-05 在 `.gitattributes` 已声明的同类行尾敏感场景同因；本任务按边界未改 `.gitattributes`）
- `verify-only.json` 与 `production-preflight.json`：留在主仓 gitignored `.verify/`，不入库——本 README 上述事实即其安全摘要（时间戳 / 计数 / 哈希 / 状态，无绝对本机路径外泄、无凭据）

## 边界

- 本回合零生产操作：以上全部为 supervisor 已执行事实的记录，本回合未触碰任何数据库 / 锚文件 / 容器 / 服务 / env / 远程
- tracked anchor + merge commit 只能作为 Git 库外见证 / 哈希存证，不等同严格 WORM / 对象锁 / 离线介质——锚文件专项归档仍开放（工具 docstring 明示：本机锚文件必须另行复制到 WORM/对象锁/离线介质才构成对持库写权限者的防御）
- provider 冒烟与发布审批仍未完成
- 单次锚定 + 单次 preflight 快照不构成 production readiness：`production_ready=false` 不变
