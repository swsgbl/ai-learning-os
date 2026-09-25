# M14-133 Production Drift Watch 历史审计（实现切片）

- 任务：M14-133 implement Production Drift Watch history audit（Claude 实现切片）
- 分支：`ops/m14-133-drift-watch-history`（独立 worktree
  `ai-learning-os-worktrees/m14-133-drift-watch-history`）
- 基点（parent）：main `a58e8086eb415de200cbfe2a2cb7cd8b410fdc54`
  （PR #220 merge = M14-132 三次自然轮证据回填合入；本地
  `git rev-parse origin/main` 复核一致）
- 单 local commit，不 push、不开 PR、不合并——review/push/PR/CI/merge
  全部由 Codex supervisor 独立执行。
- 本切片 commit SHA 与逐字节 diff 由实现者在完成报告中给出，供
  supervisor 独立复核（README 无法自引用其所属 commit 的最终 SHA）。

## 1. 交付物

| 文件 | 角色 |
|---|---|
| `tools/ops/production_drift_watch_history.py` | M14-133 历史审计器（单文件、纯标准库、Python ≥3.11） |
| `services/api/tests/test_production_drift_watch_history.py` | 契约测试（合成临时 fixtures 专用） |
| `tools/ops/README.md` | 新增 M14-133 段 + 标题行扩展 |
| `docs/evidence/m14-133-drift-watch-history/README.md` | 本文件 |
| `docs/PROJECT_STATUS.md` / `docs/ROADMAP.md` / `docs/CHANGELOG.md` | M14-133 任务条目（只陈述实际执行内容） |

## 2. 实现摘要

`production_drift_watch_history.py` 只读扫描一个 drift-watch 报告目录
（默认 canonical gitignored
`.verify/artifacts/m14-127-production-drift-watch/`），回答六个问题：
哪些报告存在、哪些 malformed、哪些 drifted、期望的 PT15M slot 是否
缺失、最长连续 clean streak 是多少、API/Web digest 是否保持锚定。

关键设计（全部细节见脚本头注释与 `tools/ops/README.md` M14-133 段）：

- **文件名严格白名单**：仅 `drift-watch-YYYYMMDD-HHMMSS.json` 普通文件
  进入解析；伴生 `.md`、`plan-*.json`、其它条目只计入
  `ignored_entries`；输入路径 `..` 组件与 symlink 组件 → fail-closed
  零输出；symlink 报告文件计 invalid。
- **严格 schema 校验**（对齐 M14-127 execute 报告真实字段）：固定
  tool/milestone/mode/schema_version、UTC 时间戳形态、config.anchors
  （api/web tag+digest 形态）、counts 与 checks 实际计数一致、drift 与
  失败检查数一致；文件名 UTC 时间戳对 `started_at_utc` 交叉校验
  （`[stamp, stamp+2s]` 容差，匹配 M14-127 先取 stamp 后取
  started_at 的实现序）。
- **scheduled-run 选择**：minute ∈ {00,15,30,45} 且 second ∈ [00,30]
  入选；manual/off-slot 报告保持可见 excluded（reason
  `manual-off-slot`），绝不静默删除。
- **PT15M slot 审计**：仅首末入选 slot 闭区间、显式缺失清单、绝不
  外推；期望数超 10000 硬顶 → `slot-range-too-large`。
- **重复与 digest**：duplicate started_at、同 slot 双跑、drift=true、
  API/Web expected/running（容器 inspect + tag 解析双通道）跨报告
  不一致或未锚定 → 固定词汇 findings。
- **确定性输出**：零墙钟（输出不含任何当前时刻）、固定输出文件名
  （同输入两次运行逐字节相同）、JSON+MD 同目录 tmp+fsync+os.replace
  原子落盘、写盘前 redact_secrets 终防线；逐报告索引 `runs` 有界
  （5000 硬顶）；报告绝不包含绝对路径。
- **exit 语义**：exit 0 仅当选中报告全部 valid 且 clean、无重复、无
  缺失 slot、digest 锚点三重稳定；零 valid/零 selected 恒为发现；
  审计发现 → 诚实落盘失败报告后 exit 2；参数/路径/写失败 → exit 2
  零输出。

## 3. 命令（实现者实际执行过的）

```
# 仓库根执行（本切片仅运行过 --help 的 plan 级调用，绝不触碰真实工件）
.venv/Scripts/python.exe tools/ops/production_drift_watch_history.py --help

# 测试（canonical venv 解释器；全部合成临时 fixtures）
.venv/Scripts/python.exe -m pytest services/api/tests/test_production_drift_watch_history.py -q
.venv/Scripts/python.exe -m pytest services/api/tests/test_production_drift_watch.py services/api/tests/test_production_drift_watch_task.py -q

# 静态校验
.venv/Scripts/ruff.exe check tools/ops/production_drift_watch_history.py services/api/tests/test_production_drift_watch_history.py
.venv/Scripts/python.exe -m py_compile tools/ops/production_drift_watch_history.py services/api/tests/test_production_drift_watch_history.py
```

真实历史执行（默认输入目录指向 canonical gitignored 真实报告）为
**supervisor-only**：本切片实现者未对真实工件运行过审计本体（仅
`--help`），真实历史结论（如当前 4 份 on-slot + 1 份 manual 报告的
审计输出）由 supervisor 在 review 后自行执行与宣判。

## 4. 合成-only 验证（实际执行结果）

契约测试 47 项全部通过（`47 passed in 0.44s`），邻居回归
`test_production_drift_watch.py` + `test_production_drift_watch_task.py`
178 项全部通过（`178 passed in 0.54s`）；合计 225 passed / 0 failed /
0 skipped（Windows symlink 权限分支按平台可用性条件跳过，本机全跑）。

覆盖面：结构契约（AST import 白名单 + 源码 forbidden tokens：零
subprocess/socket/environ/utcnow/time.time/docker 引用）；快乐路径
（连续 PT15M 自然轮全绿、单轮、原子写无 tmp 残留、写失败 fail-closed）；
scheduled 选择（second=30 上界/31 排除、manual off-slot 可见保留、
伴生 .md/plan-*.json 只计数）；fail-closed 发现（缺失 slot、streak
断链、13 类 schema 拒绝参数化、ended<started、counts/checks 与
drift 矛盾、非 JSON/非 UTF-8、文件名时间戳非法与 ±容差越界、重复
started_at、同 slot 双跑、drift=true、digest expected/running 不一致
与未锚定、空目录、仅 manual、slot 范围超硬顶）；路径拒绝（`..`
traversal、输入缺失、输入是文件、symlink 输入目录/报告文件/输出目录
——真实文件面 + 权限不足时 skip）；确定性与安全（两次运行逐字节
相同、无绝对路径、redact 终防线单元契约、报告顶层 schema 键契约）。

## 5. 诚实边界

- 历史审计**不证明 production readiness**、不证明长期稳定性、不证明
  跨重启（机器/Docker/仓库重建）存活、不证明 drift=true 告警送达；
  `production_ready=false` 不变，release-approval 仍是 human-only 门。
- 本切片**未对真实工件执行审计本体**：工具正确性仅由合成 fixtures
  锁定；真实历史结论属 supervisor 后续显式执行。
- slot 完整性语义受限于入选报告的首末边界：范围之外的槽位（例如
  任务安装前的历史、审计时刻之后的未来）不在完整性宣称范围内。
- `+2s` 文件名容差匹配 M14-127 当前实现序（stamp 先取、started_at
  后取）；若 M14-127 未来改变该序，需同步本工具容差常量与测试。
- clean streak 是"相邻入选 clean 报告恰差一个 PT15M slot"的连续计数，
  不是时长宣称；manual 轮不参与 streak。
- Harmony M14-126 attempt 2 继续 BLOCKED（hdc targets 空、
  127.0.0.1:5555 不可达），与本切片无关，不得虚构通过。

## 6. 变更与验证清单

- 变更文件（7）：`tools/ops/production_drift_watch_history.py`（新增）、
  `services/api/tests/test_production_drift_watch_history.py`（新增）、
  `tools/ops/README.md`（M14-133 段）、
  `docs/evidence/m14-133-drift-watch-history/README.md`（新增，本文件）、
  `docs/PROJECT_STATUS.md`、`docs/ROADMAP.md`、`docs/CHANGELOG.md`
  （各一条 M14-133 条目）。
- 验证：pytest 47 + 178 全过（canonical venv）；ruff All checks
  passed；py_compile 通过；`git diff --check` 干净；新增行秘密扫描
  0 命中；post-commit worktree clean（单 commit）。
- 零生产/零调度器/零设备 mutation：零 Docker、零 compose、零 Task
  Scheduler 任何接触（连只读查询也未执行）、零服务进程启停、零
  DB/MinIO/语音/secret/env 访问；对 canonical gitignored 真实
  drift-watch 工件仅开发早期的只读 schema 理解（1 份 JSON 结构
  阅读），其内容未复制进任何 tracked 文件。
