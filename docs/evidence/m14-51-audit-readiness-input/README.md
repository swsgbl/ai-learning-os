# M14-51 审计归档就绪报告 CLI（开发切片证据）

## 任务性质

- 开发切片（实现 + 契约测试）：交付 `tools/ops/audit_archive_readiness.py` +
  `services/api/tests/test_audit_archive_readiness.py`（合计 978 行），本 Claude
  开发回合独占 worktree 单 local commit 不 push（remote 发布/CI/合并由 Codex 负责）
- 分支/worktree `ops/m14-51-audit-readiness-input` 基于 `main@965eb91`
  （PR #133 merge，本地 git 可验证）
- 定位：M14-50 已合并的纯评估器 `audit_archive_scheduler.evaluate_readiness`
  （PR #132）的薄 CLI 封装——把「读两个本地 schema-v1 文档 → 逐任务确定性
  分类 → 写就绪报告」变成可执行命令，评估语义零新增零改动
- **local-only**：只做本地文件输入与报告输出；全部验证为合成文档 +
  临时文件（`tmp_path` 真实盘 + 注入 FakeFS 双轨），零真实调度器/生产触碰
- supervisor review 已通过；review 提出的输出守卫缺口（tmp 文件路径同样
  不得落在任一输入上，关掉 input-overwrite 面）已落实并有回归测试锁定

## 工具契约（`tools/ops/audit_archive_readiness.py`，单文件纯标准库）

- 单命令，退出码统一 `0` 成功生成报告（含 readiness 判为 `due`/`overdue`/
  `blocked`——报告 `overall` 字段携带该状态，工具本身不算失败）/ `2` 一切
  usage/输入/输出失败（fail-closed，绝不静默降级）：

  ```
  python tools/ops/audit_archive_readiness.py \
      --state <schema-v1 state JSON> --policy <schema-v1 policy JSON> \
      --output <readiness 报告路径> [--now <tz-aware ISO 8601>]
  ```

- 输入纪律（fail-closed 只读）：仅普通文件——缺失/目录/symlink/未解析或不
  安全路径一律拒绝；单文档保守 1 MiB 上限且读前后双检（读中途增长的文件
  仍被拒）；严格 UTF-8 + 严格 JSON——重复键、`NaN`/`Infinity`、非 JSON
  object 全部拒绝；两个输入绝不变异
- `--now` 只解析 tz-aware ISO 8601（naive 时间戳拒绝）；缺省取当前 UTC，
  供确定性测试复现
- 报告纪律：canonical JSON（排序键、紧凑分隔符、UTF-8、恰好一个尾随
  换行）二进制模式写盘（免疫 Windows `\n`→`\r\n` 翻译）——同输入 + 同
  `--now` 产出字节相同报告；报告只记输入的逻辑 role + basename + 字节数 +
  SHA-256，**绝不记录绝对本地路径**
- 输出工件：报告 + sidecar `<output>.sha256`（内容
  `<report-sha256>  <output-basename>\n`）；两者经 temp 文件 + `os.replace`
  原子替换，写失败清理 temp 非零退出零残骸；输出与 sidecar（含其 tmp
  中间路径）**绝不覆盖任一输入**，输出已存在时也必须先行原子替换而非
  部分写；工具绝不创建目录（输出目录必须已存在）
- 共同纪律：FS 与时钟全注入（开发回合零真实时钟依赖）、零网络、零环境
  变量访问、零子进程、零 DB/S3；M14-50 评估器经 `importlib` 定位加载，
  语义与上游同版本

## 开发验证事实（全部合成/临时文件）

- 契约测试 `services/api/tests/test_audit_archive_readiness.py` **44 passed**
  （canonical venv，0.17s），FakeFS 注入与 `tmp_path` 真实盘双轨，覆盖
  成功/report 形态、输入拒绝（含 symlink/超限/malformed JSON/重复键）、
  输出守卫（含 tmp 路径 input-collision 回归）、原子写与幂等、`--now`
  校验、wrapper 契约、CLI 退出码、源码纪律（零网络/零 env/零子进程）
- scheduler + readiness 联跑 **78 passed**（0.21s）；worm 邻居回归
  `test_audit_anchor_archive.py` + `test_audit_worm_offline_copy.py`
  **270 passed**（0.51s，零回归）
- `ruff check services/api` All checks passed；两实现/测试文件
  `py_compile` 通过；`git diff --check` 干净；staged 新增行 secret 扫描
  0 命中
- 开发过程 TDD：先契约测试后实现；supervisor review 落实的 tmp 路径
  input-collision 守卫有专项回归测试

## 边界

- **本切片仅文件输入/报告输出**：不自动发现证据文件（state/policy 由
  操作者显式传路径）、不集成 Windows Task Scheduler（零调度器读改）、
  不接触 WORM/离线存储、不执行任何生产动作、不访问任何 provider
- 合成文档 + 临时文件验证 ≠ 真实调度链路验证：未在真实计划任务、真实
  锚链状态或真实归档管线上运行过；`--state`/`--policy` 的真实数据形态
  由后续 supervisor 验收承载
- `pass` 不等于 production ready：全局 `production_ready=false` 不变
