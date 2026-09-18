# M14-55 审计归档输入更新器（plan 只读 / apply 原子落盘）——开发切片证据

- 分支/worktree：`ops/m14-55-audit-input-updater`，基于
  `main@0999dee3745eb1a1b10282bbbcf431dbdd31a3d0`。
- 未提交状态仅适用于本委托开发交接（开发回合不 commit、不 push、
  不建 PR）；remote 发布/CI/PR/合并归 supervisor 所有，开发交接
  完成后由 supervisor 执行。
- 交付物：`tools/ops/audit_archive_input.py`（单文件纯标准库，Stage A1
  `plan` 零写入 + Stage A2 `apply` 原子落盘）、
  `services/api/tests/test_audit_archive_input.py`（聚焦契约测试，
  合成输入全部落 tmp_path 真实文件）。

## apply 安全契约（测试锁定的要点）

- `--confirm` 必须逐字为 `EXECUTE AUDIT ARCHIVE INPUT UPDATE`，任何
  其他值在任何输出路径被访问之前拒绝（7 个必填参数缺一由 argparse
  拒绝）。
- 先按与 plan 完全相同的校验链构建同一载荷（now tz-aware 门 →
  五文件门 → 恰一行 sequence=0 genesis 锚 → verify 报告语义 +
  sidecar 摘要绑定 + 三方同锚 SHA → 时序）；校验失败零输出写入。
- 输出路径门：非 symlink、非目录、父目录必须已存在，resolved 路径
  不得与任何输入/sidecar/另一输出相同；输出与 problem detail 零路径
  序列化。
- 既有 policy 输出字节漂移即拒绝，绝不静默覆盖；既有 state 以同一
  strict JSON 规则装载（拒重复键/NaN/Infinity），要求 schema 1 恰三
  任务形状，任一任务时刻回滚即拒绝。
- 写入：每目标一个位于目标父目录的临时文件 → flush+fsync →
  `os.replace` 原子替换，失败路径清理 tmp；逐文件原子，**诚实不承诺
  跨文件事务**（输出 JSON `cross_file_transaction: false`）。
- 全程零网络/零 env/零子进程/零 DB/零 S3/零 WORM/零离线根/零调度器
  访问；源级纪律测试锁定写盘原语只出现在 apply 段。

## 验证（主仓库 canonical venv 真实执行；全部通过）

```text
.venv\Scripts\python.exe -m pytest services/api/tests/test_audit_archive_input.py -q
  → 76 passed in 0.46s（含 apply 全部 10 项必做场景）
.venv\Scripts\python.exe -m pytest services/api/tests/test_audit_archive_scheduler.py \
    services/api/tests/test_audit_archive_readiness.py -q
  → 78 passed in 0.80s（邻居零回归）
.venv\Scripts\python.exe -m py_compile tools/ops/audit_archive_input.py \
    services/api/tests/test_audit_archive_input.py
  → 通过（零输出）
.venv\Scripts\python.exe -m ruff check tools/ops/audit_archive_input.py \
    services/api/tests/test_audit_archive_input.py
  → All checks passed!
git diff --check → 干净
git status --short → 仅两个未跟踪交付文件（scheduler/readiness 零改动）
```

聚焦 diff 检查（supervisor 要求项）：对两交付文件全文扫描绝对路径/
秘密/subprocess/网络/env/DB/S3/WORM/离线根访问 token——工具文件零
匹配；测试文件唯一命中是源级纪律测试自身的禁止 token 清单（断言
这些 token 在工具源码中不存在，良性）。

## Real-input / temporary-output smoke（supervisor 冒烟）

- 直接以真实 M14-42 锚 / M14-43 WORM verify 报告 / M14-49 离线
  verify 报告跑 `plan`：**正确拒绝（exit 2，code
  `worm-sidecar-missing`）**——M14-43 verify 报告没有配套
  `.json.sha256` sidecar。这是历史证据链缺口，**不是放宽门的
  理由**（fail-closed 按设计生效）。
- 随后为**明确标注 smoke-only 的临时 staging 副本**（全部位于
  `.verify` 内）：真实报告输入**逐字节相同**，仅新计算一个 WORM
  sidecar（只存在于 `.verify`）。结果：`plan` 通过；`apply` r1
  通过/写入（written）；`apply` r2 通过/no-op（already_matched，
  零重写）。
- SHA-256（小写 hex 规范形式）：
  - 锚（M14-42）：
    `d2bfd877aa94632e6f68932a4d4bef8d963a6eb61aca12de6429c5fb7873aa4e`
  - WORM 报告（M14-43，规范化小写）：
    `ff2494a8e922203f2f6ceb140e06922279ec966b47e4ffd9311c844d38fc7ddd`
  - 离线报告（M14-49）：
    `cea9864cce9d21268bc5f2ef356e3f92600d3ed3377b1b960cca759256070ccf`
- 原始输入**零修改**；临时生成的 sidecar **不是生产证据**，仅
  证明校验链与 apply 通路在真实字节上按契约工作。

## 诚实边界

- 本切片全部输入为 tmp_path 下**合成数据**（锚/报告/sidecar 由测试
  独立构造，不 import 被测实现的构造逻辑）；未访问任何真实生产
  输入、真实 WORM/离线介质/S3/DB/调度器。
- apply 的原子性仅经注入失败验证（monkeypatch `os.replace` 单点
  失败 → tmp 清零、终防线 exit 2、policy 已写 state 未落的非事务
  事实如实断言）；未做真实断电/崩溃注入。
- symlink 相关拒绝测试在无法创建 symlink 的平台自动 skip。
- 未运行任何真实 M14-43/M14-49/M14-50 生产链路；scheduler 与
  readiness 仅作邻居回归测试，零代码改动。
- 两输出逐文件原子，跨文件一致性不承诺（后写者失败时先写者可能
  已生效，输出 JSON 明示）。
- 工作树保持未提交状态。
