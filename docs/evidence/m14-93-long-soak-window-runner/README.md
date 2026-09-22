# M14-93：长稳到期审计/导出 runner（实现/文档切片）

- 切片：分支 `ops/m14-93-long-soak-window-runner`（独立 worktree
  `m14-93-long-soak-window-runner`，基于 main
  `0bfd560`（PR #179 merge = M14-92 证据合入，精确基点）），单次 local
  commit（不推送、不建 PR）。
- 目标：把 M14-79 权威锚定窗口到期后的「重跑 soak 审计 + 逐字节导出
  long-soak 门证据」从人工命令拼装固化为单一 fail-closed 仓库工具
  `tools/ops/long_soak_release_window.py`——**移除手工拼装风险，而不是
  移除判定**：本 runner 绝不伪造 pass、绝不改写/重序列化门证据。
- 零生产触碰（本切片全程）：零容器启停、零计划任务操作、零
  DB/MinIO/语音引擎接触、零生产日志写入、零 secrets/env 读取、零
  soak 历史/锚定读写（工具输入默认指向 canonical gitignored 路径，但
  本切片**从未对真实生产 history.jsonl 或真实锚定记录执行过本工具**——
  全部验证基于合成 fixture）、零发布审批接触、零部署、零网络。

## 1. 工具契约（tools/ops/long_soak_release_window.py）

输入 `--anchor`（soak-window-anchor.json）/ `--history`
（history.jsonl 或目录）/ `--output-dir`（必须不存在的全新目录）；
纯标准库、一切 I/O 经 Store 注入、零子进程/零网络/零计划任务/零 env
读取/零墙钟。

1. **锚定记录严格校验**（固定词汇拒绝）：九键全集恰等于
   `soak_window_gate.build_anchor_record` 写出面；schema_version/tool/
   follow_up_audit_tool 逐字匹配产锚工具自声明；window_minutes 必须
   等于审计策略窗口 1440；earliest_audit_collected_at 必须恰等于
   锚点 + 窗口、generated_at 必须等于锚点（零墙钟产锚不变式——手改锚
   提前到期即拒绝）；precondition 取值域严格（tail_non_ok_count 必须
   为 0——锚只可能产自 open 门；tail_sample_count 必须等于
   consecutive_ok；input_sha256 必须 64 位小写 hex）；boundaries 必须
   与产锚工具固定边界逐字一致。
2. **到期判定零墙钟**：最新历史样本 collected_at 对比锚定
   earliest_audit_collected_at（绝不读系统钟）。样本早于锚点本身 →
   history-before-anchor 拒绝；锚点 ≤ 最新样本 < 最早可判定时间 →
   **not-due**（exit 1，只落 runner 报告，零审计零证据导出）；到期
   → 执行审计。
3. **审计语义零重复实现**：历史路径解析/逐行严格解析/全局时序校验/
   留存契约/分类全部直接调用既有 `soak_stability_audit` 函数；策略固定
   为 release-readiness 接受口径 1440/15/20/500（工具默认值），不暴露
   任何策略参数——策略漂移即证据不可用。
4. **导出纪律**：pending 拒绝导出（exit 2）；只有 pass/blocked 才把
   soak-audit-report.json **逐字节复制**为 `evidence/long-soak.json`
   （utf-8 解码-再编码往返预检 + 写后重读复核，任何字节差异拒绝并移除
   坏副本）；runner 报告登记源/副本双 sha256（必须相等）+ 防御性后置
   断言（审计报告自声明输入哈希 == 本轮读取哈希）。
5. 退出码：0 到期 pass 且证据已导出 / 1 not-due / 2 到期 pending（证据
   导出拒绝）/ 3 到期 blocked（blocked 证据照常逐字节导出——诚实
   结论非崩溃）/ 4 输入拒绝（零输出）。
6. 输出仅安全元数据（状态/固定词汇原因/时间戳/计数/sha256/字节
   数/固定边界）；**CLI stdout 与 argparse help 同纪律（R2 修正）**：
   help 只作通用输入描述、绝不插值 DEFAULT_* 绝对路径值；stdout 只打印
   文件名/状态/时间戳/固定词汇原因，绝不回显 args 锚定/历史/输出目录
   路径——默认路径行为不变（默认值仍注册在 parser 上，测试仍断言）；
   生成时间戳取自最新历史样本——零墙钟，同输入重跑输出逐字节相同；
   输出目录必须全新（拒绝与旧证据混装）+ symlink 一律拒绝。

## 2. 测试覆盖（services/api/tests/test_long_soak_release_window.py）

45 项契约测试全绿（0.96s）。锚定记录 fixture 由**真实
`soak_window_gate --anchor`** 在合成干净历史上产出（测试锚定记录的
唯一样本来源即产锚工具本身）；全部 I/O 经真实临时目录，绝不触碰
canonical 仓库与真实 `.verify`：

- 结构契约：源码零子进程/零网络/零 env/零墙钟 token；socket+subprocess
  双阻断下端到端照常成功；ops README 文档化；
- 四态：not-due（exit 1、零审计零证据、固定词汇 window-not-due）/
  due-pass（恰在到期边界 latest == earliest、exit 0、证据与审计报告
  **逐字节相同**、双哈希相等、键集与 release-readiness long-soak 门
  接受的 v2 报告键集 `SOAK_REPORT_KEYS` 一致）/ due-blocked（exit 3、
  blocked 证据照常逐字节导出）/ due-pending（exit 2、审计报告落盘、
  证据导出拒绝 evidence-export-refused-pending）；
- 篡改/哈希防御：锚 schema_version/tool/follow-up 工具名错位、窗口
  漂移、earliest 手改提前、generated_at 偏移、precondition 六字段
  篡改（含 tail_non_ok_count>0）、boundaries 篡改、键集多余/缺失、
  非 JSON/非对象/路径缺失/symlink；注入写损坏 Store（evidence 写入
  翻转字节）→ read-back 复核拒绝并移除坏副本、runner 报告零写出；
- 历史拒绝面：malformed 行/非法字段/重复/非时序/项目冲突/空文件/
  行数超硬顶/路径缺失/history-before-anchor（均零输出 exit 4）；
- 输出目录护栏：已存在拒绝（目录保持空）、dangling symlink 拒绝；
- 零墙钟：同输入两次运行（各自全新输出目录）runner JSON/MD 逐字节
  相同；generated_at == 最新样本 collected_at；
- 隐私：行内多余字段携带标记 token 绝不进入任何输出；CLI 默认值
  注册（canonical gitignored 三路径）；
- R2 回归（supervisor correction）：成功路径与拒绝路径的 CLI stdout
  （stdout+stderr）与 argparse help 逐字扫描——无盘符/绝对路径形态
  文本、无 DEFAULT_* 绝对路径插值、无 REPO_ROOT；help 仍含三个选项
  且默认值仍注册（默认路径行为不变）。

## 3. 验证记录（全部真实可复跑）

```bash
# canonical venv（CPython 3.11.15）
python -m pytest services/api/tests/test_long_soak_release_window.py -q
#   → 45 passed
python -m pytest services/api/tests/test_soak_window_gate.py \
    services/api/tests/test_soak_stability_audit.py \
    services/api/tests/test_release_readiness.py -q
#   → 157 passed（邻域：产锚门 + 被复用审计 + long-soak 消费门，零回归）
python -m py_compile tools/ops/long_soak_release_window.py   # OK
ruff check tools/ops/long_soak_release_window.py             # All checks passed
ruff check services/api/tests/test_long_soak_release_window.py  # 同上
git diff --check                                            # 干净
```

（合计聚焦 + 邻域 202 passed。）

## 4. 诚实边界

- **本 runner 不使 long-soak 通过**：blocked 窗口导出的就是 blocked
  证据；pending 拒绝导出；pass 只能源于 soak_stability_audit 既有分类
  语义在真实干净窗口上的判定。
- **真实 24h 长稳审计在本切片中未发生**：全部验证基于合成 fixture；
  M14-79 权威锚（canonical
  `.verify/artifacts/m14-79-soak-window-anchor/`，锚点
  2026-09-21T15:00:01Z，最早可判定 2026-09-22T15:00:01Z）到期后的
  真实执行是后续运维动作，本切片不预宣称其结果。
- `release_ready=false` / `production_ready=false` 不变；发布审批仍是
  human-only（本工具与 release-approval 无任何交集）。
- 到期后的真实运行仍需操作者显式执行（本工具不含任何调度/计划任务/
  守护面）；到期时生产历史若在锚定窗口内出现非干净样本，runner 将
  如实产出 blocked 证据而非任何形式的 pass。
- 导出的 `evidence/long-soak.json` 仍需经 evidence-cockpit
  staging/release-readiness 消费才进入发布门汇总——本工具只负责
  「到期后忠实执行审计并逐字节导出」这一步。
