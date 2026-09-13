# M14-15 监控历史洞察/告警摘要（第 1 切片）— 交付证据归档

- 日期：2026-09-13（交付）
- 分支：`feat/m14-15-monitoring-insights`（基于 `main@f6f0356`，即 PR #89 merge
  commit `f6f03569aa13b52e07a2e63076f158c587117dfc`，本地 git 可验证）。本
  Claude 开发回合仅做本地 commit；supervisor 审查与 remote 发布（push/PR/
  合并）在其后进行——该表述是开发时点快照，已被下方合并收口取代。
- **合并收口（回填 2026-09-13）**：已随 **PR #90** 合并 main——merged_at
  **2026-09-12T18:10:07Z**，merge commit
  `ce100600c1f13b854129ddaa8d440089a98c3142`，feature head 即本切片唯一
  commit `acdbbb53cae1adb628ae576447a8b5ccfc1a435d`（本地 git 可验证；
  远端 feature 分支截至回填时点仍存在）。PR CI run `34710154644` 与合并后
  main push CI run `34710372281` 均全部 5 job（Web/API/Docker/Android/
  Release tools）SUCCESS。**合并 = 代码入库 + CI 绿；仍不等于外部告警
  接入或 production readiness（见结论边界）**。
- 状态：**工具 + 聚焦契约测试交付（本地 commit）；开发回合零生产执行、
  零 canonical 仓库/`.verify` 触碰（全部验证用合成样本在测试临时目录与
  本 worktree 自身 gitignored `.verify` 完成）**。
- 入库变更：`tools/ops/monitoring_insights.py`（单文件、纯标准库、零第三方
  依赖；schema 单一事实源 = 委托同仓 M14-13 `monitoring_history.py` 的已测
  校验/去重/排序函数）、`services/api/tests/test_monitoring_insights.py`
  （90 项契约测试）、本 README，以及 PROJECT_STATUS / ROADMAP / CHANGELOG /
  `tools/ops/README.md` 同步。
- 结论口径（诚实边界）：**工具交付 ≠ 外部告警接入，更 ≠ production
  readiness——`production_ready=false` 不变**。本切片是「监控历史 → 结构化
  洞察/告警摘要」的第一块本地基础：仅对本地只读工件做派生分析。

## 产品形态

```
python tools/ops/monitoring_insights.py                 # plan（默认，零读取/零写入）
python tools/ops/monitoring_insights.py --execute \
    --confirm "EXECUTE READ-ONLY MONITORING INSIGHTS"   # execute（只读洞察）
```

- **双模式门禁**：默认 plan 完全惰性（零读取/零写入/零 Store 构造——测试
  以「RealStore 构造即失败」工厂锁定）；execute 需 `--execute` + 精确确认
  短语 `EXECUTE READ-ONLY MONITORING INSIGHTS`（一字不差；缺一/近似/大小写
  变体均 EXIT 2 且零读取）。`--event-limit`（默认 50、硬界 1–500）超界在
  plan/execute 两种模式下同样拒绝。
- **输入三形态（固定画像）**：`--source` 可为 ① `history.jsonl` 文件本体；
  ② 含它的目录（**默认 gitignored
  `.verify/artifacts/m14-13-monitor-history-retention/`**——本切片新增的
  指定输入位，仓库现有工具尚无写入者，由操作者放置 M14-13 历史输出或以
  `--source` 指向 M14-13 默认输出目录）；③ M14-12 monitor 工件目录（仅
  `monitor-*.json`：发现/严格校验/SHA-256 去重/确定性排序**全部委托同仓
  M14-13 `monitoring_history.py` 已测函数**，单一 schema 事实源；其固定
  词汇拒绝原因原样透传，本工具零复制校验逻辑）。同目录混入两形态 →
  mixed-inputs 拒绝；空目录/缺源/非 `history.jsonl` 文件名拒绝。
- **严格校验（fail-closed，任何拒绝输出零写入）**：history 行必须逐字段
  符合 M14-13 记录 schema（schema_version==1 / artifact_sha256 64 位十六
  进制 / stem 白名单（含日历合法性）/ UTC 时间戳格式 / project 白名单 /
  overall_status∈{ok,warn,critical}（**incomplete 拒绝**）/ partial 恒
  false / 阈值计数 int 非 bool 非负 / 六服务 health 字符串 + restart 非负
  / 五端点 http_status int + **latency_ms 有限非负**（nan/inf/-inf/负值/
  字符串均拒）/ 六日志 error_total int 非负）；not-json/空行/JSON 数组同
  拒。**行序必须严格递增 (collected_at, source_stem)**——时间乱序、重复
  行、同时间戳逆序拒绝；跨 project 混档拒绝；样本数 1–5000（**超界拒绝，
  绝不静默截断**）。未知额外字段忽略（前向兼容）且其值**绝不进入输出**
  （投毒标记值测试实证）。
- **洞察最小集（全部确定性纯函数）**：时间范围+样本数+时长；
  overall_status 计数；availability（计数+比率）；degraded/critical 事件
  列表（每事件含 collected_at/状态/非 healthy 服务清单/非 200 端点清单；
  有界 `--event-limit`，超界截断计数显式、**保最新 N 条**）；逐端点延迟
  min/p50/p95/max（nearest-rank，与 M14-13 同款实现复用）+ 非 200 计数；
  逐服务 restart/日志 error 总计 + 非 healthy 样本数；连续失败/恢复（当前
  连胜（含当前连续 non-ok）、最长 non-ok 连败区间（首/末时间戳）、失败/
  恢复转移计数+有界时间戳列表、逐端点当前连续失败计数）；最近样本状态
  （时间/overall/阈值计数/六服务 health/五端点 http_status）。
- **输出（默认 gitignored
  `.verify/artifacts/m14-15-monitoring-insights/`，`--output-dir` 为操作者
  显式自选）**：`insights.json`（schema 版本化、源 provenance（source_kind/
  source_sha256/duplicate_count））+ `insights-summary.md`（事件表、端点
  延迟表、服务汇总表、连续失败/恢复节、最近样本节、边界注记）。同目录
  tmp+fsync+os.replace 原子落盘（replace 失败清理 tmp 零残留）；symlink
  拒绝覆盖源文件/源目录/输出文件/输出祖先（mkdir 穿越防御：symlink 目标
  零写入、输出路径零创建）；**仅在全部输入校验通过后才写任何输出**
  （FakeStore 写调用记录实证）。
- **报告卫生（本切片新增要求）**：stdout/输出**绝无绝对本机路径**——路径
  显示恒为仓库相对（POSIX 分隔）或纯名（仓外）；绝无原始日志行/密钥/
  secret/生产容器 ID；被拒值不回显（固定词汇原因）。生成时间戳取自最新
  样本 collected_at——**零墙钟，两次运行输出逐字节相同**。
- **结构安全**：源码零 `subprocess`/`socket`/`urllib`/`http.client`/
  `os.system`/`Popen`/`environ`/`requests`/`getenv`/`docker`/
  `datetime.now`/`utcnow`/`time.time` 字面量（契约测试锁定）；socket+
  subprocess 双阻断下端到端照常成功；一切 I/O 经 Store 注入
  （RealStore/FakeStore）。
- 退出码：0 plan 成功 / execute 成功；2 任何拒绝（门禁、参数超界、源
  缺失/symlink、零样本、malformed、乱序/重复/混档、超 5000、写失败）。

## 本回合验证（2026-09-13，canonical venv Python 3.11.15 / pytest 9.1.1 / ruff 0.16.5）

1. **聚焦契约测试**：`python -m pytest
   services/api/tests/test_monitoring_insights.py -q` → **90 passed**。
   覆盖：结构契约（禁用 token + `import monitoring_history` 委托锁定 +
   双阻断端到端 + 零墙钟逐字节可复现）、plan/execute 门禁（plan 完全惰性
   ×2、确认短语缺一/近似 ×4、event-limit 越界 plan ×4 + execute ×1、
   无旗标 stray confirm = plan）、输入三形态（文件直读+SHA-256 链、目录
   形态、monitor 目录委托 ×5：happy/malformed 透传/源文件逐字节不变/
   同哈希 duplicate_count、混入/空目录/缺源/非许可文件名拒绝）、路径防御
   （源文件 symlink 真实文件面、源目录 symlink FakeStore、输出祖先 symlink
   mkdir 穿越（真实文件面，目标零写入）、输出目标 symlink 零写调用）、
   行校验 fail-closed ×31（not-json/空行/数组/schema/SHA 形态 ×3/stem ×2/
   时间戳 ×2/project/incomplete/partial/计数 ×3/health ×2/restart ×3/
   端点 ×5 含 nan/inf/-inf/字符串/日志 ×2——每例断言输出零写入）、
   乱序/重复行/同时间戳正逆序/混档/零样本/超 5001 样本拒绝、投毒额外字段
   忽略零泄漏、洞察计算（状态计数+availability 比率、时间范围+时长、
   nearest-rank 分位精确断言、事件明细+有界保最新、连胜/最长连败/转移/
   端点当前连败 ×2、最近样本全字段、服务汇总、单样本边界形态）、原子写
   （校验失败零写调用、第 1/第 2 次写失败、os.replace 失败真实文件面零
   tmp 残留 ×2、成功零 tmp 残留）、报告卫生（stdout 零绝对路径、被拒值
   不回显、输出零路径零标记、Markdown 章节与边界、无事件变体）、CLI
   （默认值注册、ops README 文档化、happy path stdout 摘要行）。
2. **M14-13 回归**：`test_monitoring_history.py + test_monitoring_insights.py`
   → **164 passed**（74 + 90，M14-13 套件零回归）。
3. **监控全家族组合**：`test_production_monitor.py + test_monitoring_history.py
   + test_monitoring_pipeline.py + test_monitoring_pipeline_task.py +
   test_monitoring_insights.py` → **484 passed**（189 + 74 + 52 + 79 + 90）。
4. **静态**：`ruff check services/api tools/ops/monitoring_insights.py` 全过、
   `py_compile tools/ops/monitoring_insights.py` 过、`git diff --check` 过。
5. **本地端到端演示（合成数据，本 worktree gitignored `.verify` 内）**：
   合成 3 个 M14-12 形状 monitor 工件（ok/ok/warn——warn 样本 api 非
   healthy + restart 1 + 日志 error 3、funasr-health 503）→ 先跑 M14-13
   `monitoring_history.py` 落 `.verify/artifacts/m14-13-monitor-history-
   retention/`（本切片默认源位）→ 再以**全默认参数**跑本工具：plan 出
   计划（零读取）、execute exit 0 输出 3 样本洞察（ok=2 warn=1
   critical=0、availability=2/3=0.666667、1 事件（api+funasr-health）、
   逐端点 min/p50/p95/max、api 服务 restart 1/日志 error 3、当前连胜
   warn × 1、失败转移 1 次、最近样本 warn）。**连跑两遍输出逐字节相同**
   （`insights.json` SHA-256 `fa6c17bf…93796a`、`insights-summary.md`
   SHA-256 `83602bf6…d792e9`）；stdout 仅含仓库相对路径。这是**合成
   数据演示**，不是真实生产监控结果。

## 安全边界（开发回合零违背）

- 零生产执行：未运行任何 monitor/管道 execute、未调用 Docker/生产 HTTP/
  计划任务/语音/恢复/代理；未读取 canonical 仓库或其 `.verify` 任何
  内容（本回合全部输入为合成样本，输出仅落本 worktree 自身 gitignored
  `.verify`）。
- 本回合不 push、不开 PR、不合并；仅本地 commit；不触碰 untracked
  `.claude/`。
- 零密钥/零 env 原文/零 token/零原始日志行入档；输出仅含计数、状态、
  时间戳与有限延迟数值。

## 结论边界（不过度引申）

- 本切片交付监控/告警收口的**洞察/摘要数据面第一块基础**：对本地
  history/monitor 工件的可复用、fail-closed 派生分析工具及其契约测试；
  **不接任何外部告警系统（无发送、无通知、无 webhook）**。
- 输入依赖现实：canonical 真实历史当前仍只有单样本（M14-13 回填实测
  1 行）——趋势列 min/p50/p95/max 为单点值、availability/连胜语义在单
  样本下退化形态如实呈现（测试锁定），不粉饰。
- 默认源位 `.verify/artifacts/m14-13-monitor-history-retention/` 为本切片
  指定的新输入位，仓库现有工具尚无写入者（M14-13 默认输出
  `m14-13-monitoring-history/`，M14-14 管道亦写后者）——操作者需放置
  history.jsonl 于默认源位或以 `--source` 显式指向；后续切片可考虑让
  管道/调度器直写或复制到该位。
- 未覆盖（后续切片范围）：外部告警接入（邮件/IM/webhook）、洞察随新样本
  的增量计算、多 project/多机聚合、阈值随时间标定、洞察产物的留存策略。
- **`production_ready=false` 不变**；本地工件洞察不构成生产就绪或外部
  告警宣称。
