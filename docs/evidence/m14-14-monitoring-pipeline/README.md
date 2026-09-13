# M14-14 持续/定时监控采集 + 历史管道 readiness — 交付证据归档

- 日期：2026-09-13（开发切片交付）
- 分支：`feat/m14-14-monitoring-scheduler`（基于 `main@1da4410`，即 PR #88
  merge commit `1da44100ec83adc496520bed6dc9dd660d0b7129`，本地 git 可验证；
  其树等于合并后的 remote main）。本 Claude 开发回合独占 worktree
  `m14-14-monitoring-scheduler`，仅做**一个本地 commit**；supervisor 审查与
  remote 发布（push/PR/合并）在其后进行——该表述是开发时点快照，已被
  下方合并收口取代。
- **合并收口（回填 2026-09-13）**：已随 **PR #89** 合并 main——merged_at
  **2026-09-12T17:28:55Z**，merge commit
  `f6f03569aa13b52e07a2e63076f158c587117dfc`，feature head
  `23a03903a9d4ea8909521fa4ecf2504227f4adda`（本地 git 可验证；远端
  feature 分支截至回填时点仍存在）。PR CI run `34708110486`（含本 README
  所述 PR #89 CI R3 白名单修正后的最终形态）与合并后 main push CI run
  `34708350434` 均全部 5 job（Web/API/Docker/Android/Release tools）
  SUCCESS。**合并 = 代码入库 + CI 绿；持续/定时运行仍为零（见边界）**。
- 状态：**工具 + 计划任务 readiness 管理器 + 聚焦契约测试交付（本地）；
  开发回合零真实管道执行（execute 模式从未运行）、零计划任务注册/改动、
  零 Docker/零生产 HTTP/零 env 读取**。本回合实际运行过的命令面仅有：
  管道 plan 模式（零执行，落 gitignored plan 报告）、管道门禁拒绝路径
  （缺短语/近似短语/nan——均零执行）、任务管理器 status（只读全量列表
  查询，确认 `AIOS-Monitoring-Pipeline` 未注册）与 install/uninstall 的
  短语门禁拒绝（零 schtasks 调用）；supervisor R2 修正回合追加真实执行
  `generate` 一次（仅写 gitignored XML 工件、零调度器改动——产物
  `FF FE` BOM 起始、3098 字节、声明与字节一致、可解析）。
- 入库变更：`tools/ops/monitoring_pipeline.py`（管道本体）、
  `tools/ops/monitoring_pipeline_task.py`（计划任务 readiness 管理器）、
  `tools/ops/run_monitoring_pipeline_silent.vbs`（静默 wscript 入口）、
  `services/api/tests/test_monitoring_pipeline.py`（**52 项**契约测试）、
  `services/api/tests/test_monitoring_pipeline_task.py`（**79 项**契约测试，
  含 VBS wrapper 契约与对 windows_startup_task 的身份不冲突 pin）、本
  README，以及 PROJECT_STATUS / ROADMAP / CHANGELOG / `tools/ops/README.md`
  同步。
- 结论口径（诚实边界）：**readiness 交付 ≠ 持续运行证明。本开发回合不证明
  任何持续/定时运行；`production_ready=false` 不变**。真实执行（单次管道
  execute 与计划任务注册）均 supervisor-only，且需其各自的精确确认短语。

## 产品形态

### 管道本体（monitoring_pipeline.py）

```
python tools/ops/monitoring_pipeline.py                        # plan（默认，零执行）
python tools/ops/monitoring_pipeline.py --execute \
    --confirm "EXECUTE READ-ONLY MONITORING PIPELINE"          # execute（单次组合）
```

- **双模式**：默认 plan 完全惰性（零 subprocess/零网络/零生产读取/零调度器
  改动，Runner **零构造**——计数工厂测试锁定）；execute 需 `--execute` 旗标
  + 精确确认短语 `EXECUTE READ-ONLY MONITORING PIPELINE`（一字不差），缺一
  或近似形态（大小写/多空格/前后缀/用错到 monitor 的短语）即 EXIT 2 且零
  Runner 构造/调用（fail-closed）。数值面（两步超时）非有限浮点（nan/inf/
  -inf）与超硬顶同样 EXIT 2（plan 同样校验，先于任何报告写入）。
- **固定命令白名单门（结构性）**：execute 仅允许两个**精确固定形态**——
  `<python> tools/ops/production_monitor.py --execute --confirm "EXECUTE
  READ-ONLY PRODUCTION MONITORING"`（monitor 自身门禁短语，与
  `production_monitor.CONFIRM_PHRASE` 逐字一致，回归测试锁定）与
  `<python> tools/ops/monitoring_history.py`（全部默认参数）。任何其它 argv
  （追加旗标/换脚本/换短语/顺序错乱/`--retention` 注入/任意路径）在任何
  执行之前拒绝（`CommandNotAllowedError`，内层 runner 零调用）；子进程
  恒 list-argv（**无 shell=True**，源码契约锁定）、capture、Windows 侧恒
  `CREATE_NO_WINDOW`；子进程 stdout/stderr **绝不持久化/回显**（只取
  returncode——标记值投毒测试实证零外泄）。
- **序列语义**：恒为 monitor → history；**history 仅在 monitor exit 0 后
  运行**（monitor ok|warn 才有完整可索引样本）；monitor 失败（非零退出/
  超时/执行错误）→ history 状态 `skipped` + 固定词汇原因
  （`monitor-status-failed|timeout|error`），monitor 的退出码/类别**如实
  保留绝不遮蔽**；history 失败同理。退出码：0 两步全 ok；1 已执行但有
  可见失败（步骤失败/锁释放失败）；2 门禁/数值/锁/路径/报告写入拒绝。
- **超时预算（有界 + 保守硬顶，与计划任务 XML 交叉 pin）**：monitor
  60–540s（默认 480s——覆盖 monitor 内部最坏预算 ~445s + 启动余量）、
  history 10–120s（默认 45s）；两步硬顶之和 540+120=660s < 计划任务
  ExecutionTimeLimit PT12M=720s < 重复间隔 PT15M=900s——**调度器绝不先于
  内部超时杀整任务**（避免调度器击杀留下 stale lock；间隔测试锁定该
  预算链）。
- **重叠保护（fail-closed）**：gitignored
  `.verify/artifacts/m14-14-monitoring-pipeline/pipeline.lock`——
  `O_CREAT|O_EXCL` 原子创建；锁已存在 → 可见拒绝 EXIT 2 且零步骤执行；
  **本轮零破坏性 stale-lock 清理**（陈旧锁由操作者人工处置，工具绝不
  代删——锁内容原样保留测试锁定）；锁体仅安全事实（schema/tool/UTC/
  pid）；锁/目录/祖先 symlink 一律拒绝（真实文件面：symlink 目标零写入、
  不穿越 symlinked 祖先建目录）。
- **证据报告（schema v1 JSON + MD 原子写）**：tmp + fsync + os.replace；
  仅安全事实——模式、逐步状态与退出码、逐步 UTC 起止/时长、固定命令身份
  （仓内相对身份 + `<python>` 占位，**绝无绝对本机路径**——测试断言报告
  全文不含 worktree 路径与 sys.executable）、有界脱敏错误类别/异常类名/
  固定词汇 detail、步骤产物名 + SHA-256 + 字节数（monitor 产物按步骤前后
  目录差集发现、非 `monitor-*.json` 忽略、每步至多 hash 8 个 + 超界显式
  记数；history 两个固定名；不可得时如实记 `unavailable_reason`）、锁
  事实、边界注记；写前 `redact_secrets` 终防线（secret 形态**异常类名**
  投毒实证被脱敏）。报告写入失败 = 证据不可失 → EXIT 2。

### 计划任务 readiness 管理器（monitoring_pipeline_task.py）

```
python tools/ops/monitoring_pipeline_task.py plan      # 只读预检 + 注册计划（零写）
python tools/ops/monitoring_pipeline_task.py generate  # 导出任务 XML（零调度器改动）
python tools/ops/monitoring_pipeline_task.py status    # 只读五态
python tools/ops/monitoring_pipeline_task.py install   --confirm "EXECUTE MONITORING SCHEDULER CHANGE"
python tools/ops/monitoring_pipeline_task.py uninstall --confirm "EXECUTE MONITORING SCHEDULER CHANGE"
```

- **固定任务身份 + 保守重复间隔**：`AIOS-Monitoring-Pipeline` /
  `urn:aios:m14-14:monitoring-pipeline` + Description 持久归属标记；Hidden
  / TimeTrigger（固定 StartBoundary，过去时刻——注册即生效）+ Repetition
  Interval **PT15M**（无 Duration=无限期，每小时 4 次只读采集）/
  InteractiveToken+LeastPrivilege / IgnoreNew / StartWhenAvailable /
  ExecutionTimeLimit PT12M / 电池不禁启不停；Action=
  `wscript.exe //B //Nologo "<repo>\tools\ops\run_monitoring_pipeline_silent.vbs"`；
  WorkingDirectory=repo（与 M14-06 `AIOS-Production-Recovery` 任务**零身份
  冲突**——测试 pin 任务名/URI/Description/VBS 互不相同）。
- **install/uninstall 各自需精确确认短语** `EXECUTE MONITORING SCHEDULER
  CHANGE`（一字不差），缺一即拒绝且零 schtasks 调用；**本开发回合零安装/
  零卸载/零注册——实际注册 supervisor-only**（获准窗口 + 提升令牌——
  M14-06 生产实证 schtasks /Create 仅 elevated token 下成功）。
- **结构性白名单门（GatedSchtasks）**：读路径（plan/status）恒
  `allow_mutation=False`——仅两查询形态（全量列表 `/Query /FO CSV /NH` 与
  单任务 `/Query /TN <固定名> /XML`）放行，一切 mutation 形态（含 /Run/
  /Change//End、带 /F 的 create）在任何执行之前拒绝；mutation 子命令仅在
  短语门禁通过后构造放行门，且仅放行 `(/Create /TN <固定名> /XML <单个
  .xml 临时文件>)` 与 `(/Delete /TN <固定名> /F)` 两精确形态（create 绝不
  /F；任务名恒固定；argv 形态恒定——精确前缀 + 长度 6——故 `/XML` 之后的
  **唯一 token 是值位置**：POSIX 绝对 tempfile 路径的 `/` 起头是路径前缀
  而非旗标，旗标防护靠结构（`.xml` 后缀 + 非 `-` 起头 + 无多余 token）
  ——**PR #89 CI R3 修正**：旧「`/` 起头即拒」黑名单误拒 Linux CI 的
  `/tmp/.../aios-monitoring-pipeline-*.xml` 致 install happy-path 测试在
  API job 失败，修正后 POSIX/Windows 两形态 tempfile 路径均放行、
  `-F`/`/F`/`x.xml/F`/非 .xml/多余 token/错位 token 仍逐项拒绝）。
- 绝不覆盖同名任务（install 前只读 query + 二次全量列表复核）；uninstall
  仅 exact-owned 才 `/Delete /F`（foreign/missing/malformed/unknown/查询
  失败一律零删除）；归属判定适配 M14-06 实证的 Task Scheduler 归一化
  （URI 两形态 + Description 逐字 + 全字段精确；省略的默认值元素仅在其余
  字段全部精确时认可——漂移时省略同样计 mismatch）；/XML 按字节形态严格
  解码四形态（UTF-16LE 无 BOM / LE BOM / BE BOM / UTF-8 `<?xml` 起始），
  之外/失败按 unknown fail-closed；XML 解析前拒绝 DOCTYPE/ENTITY（XXE/
  实体膨胀加固）；生成侧路径/Arguments 经 xml.sax.saxutils.escape 转义。
- generate：任务 XML 原子导出到 gitignored
  `.verify/artifacts/m14-14-monitoring-pipeline/scheduled-task.xml` 供审查
  （零调度器改动；输出路径 symlink 拒绝零写入）。**字节编码 = UTF-16
  with BOM**（`text.encode("utf-16")`——与 `build_task_xml` 的
  `encoding="UTF-16"` 声明及 install 临时文件字节完全一致，外部解析器
  如 System.Xml 可直接加载；supervisor R2 阻断缺陷修正：旧实现误以
  UTF-8 写出，声明与字节不符致 `XmlDocument.Load` 报「no Unicode byte
  order mark」拒载）；落盘后**回读原始字节**经字节形态严格解码 + 归属
  校验复核——校验的是磁盘真实工件而非仅内存文本。

### 静默入口（run_monitoring_pipeline_silent.vbs）

与 `run_production_recovery_silent.vbs` 同款纪律：仓库根自脚本位置推导
（无盘符硬编码——测试锁定）；`WScript.Shell.Run(..., 0, True)` 隐藏窗口
运行并等待；调用目标恒 `<repo>\.venv\Scripts\python.exe <repo>\tools\ops\
monitoring_pipeline.py --execute --confirm "EXECUTE READ-ONLY MONITORING
PIPELINE"`（短语是公开安全门禁常量，非 secret；计划任务是管道门禁的
授权载体）；退出码经 `WScript.Quit` 透传（Task Scheduler「上次运行结果」
可见）；不弹窗/不开浏览器/不写 secret/不另落盘。预检失败专用退出码：
2=venv python 缺失、3=管道脚本缺失、4=仓库根缺失。

## 验证（开发回合，全部注入/临时目录，零真实执行）

- 聚焦契约测试：`test_monitoring_pipeline.py` **52 passed**（plan 惰性
  socket+subprocess 双阻断、门禁 ×6 近似短语、白名单拒绝 ×11 形态、序列/
  跳过/不遮蔽、超时/执行错误、重叠锁 ×7、原子报告/脱敏/产物边界、
  monitor/history 既有契约回归 pin ×5）；
  `test_monitoring_pipeline_task.py` **79 passed**（XML 关键项/转义/
  预算链交叉 pin、verify 四态 ×12 漂移字段名、解码四形态/拒绝 ×4、
  白名单门 ×24 形态、plan/generate/status、**generate 产物 UTF-16 BOM
  字节级回归（supervisor R2 阻断缺陷：旧实现 UTF-8 字节 + UTF-16 声明，
  System.Xml 拒载——修正后逐字节断言 BOM 起始/声明一致/解码与归属校验
  通过，纯文本 round-trip 不足以锁定）**、install/uninstall 短语门禁
  与 exact-only 删除、VBS 契约、与 M14-06 身份不冲突、源码契约）。
- 邻居回归（零回归）：`test_monitoring_history.py` **74 passed**、
  `test_production_monitor.py` **189 passed**、
  `test_windows_startup_task.py` **66 passed**；五套件合并运行
  **460 passed**（52+79+74+189+66）。
- `ruff check services/api` 全过（含新测试文件）；新工具 + 新测试显式
  `ruff check` 全过；`py -X utf8 -m py_compile` 两新工具过；
  `git diff --check` 过。运行环境：canonical 仓库根 venv Python 3.11.15
  + pytest（`py` 启动器无 pytest，按既有惯例用 canonical venv +
  `-X utf8`，basetemp 用 gitignored `.pytest-tmp-m14-14/`）。

## 边界（诚实口径）

- **本回合不证明持续/定时运行**：交付的是 readiness（管道工具 + 调度
  readiness 管理器 + 静默入口），零真实管道 execute、零注册。管道在
  真实栈上的首跑（monitor 实采集 + history 实索引 + 产物哈希链）与
  计划任务首次注册均待 supervisor 获准窗口。
- **TimeTrigger/Repetition/StartBoundary 的注册后归一化行为未经真实安装
  实证**（M14-06 实证覆盖 LogonTrigger 形态）：首次 supervisor 注册若暴露
  归一化漂移，status/uninstall 按 malformed fail-closed（绝不弱解放行），
  处置走 M14-06 R3 同款修正回合。
- stale-lock 清理本轮零实现（设计决定）：进程被外部强杀（而非内部超时）
  仍可能留锁 → 下次运行可见拒绝，操作者人工删除 `pipeline.lock`。后续
  轮次可评估带保守条件的锁龄检测，破坏性清理永不默认。
- 计划任务触发仅覆盖「用户登录会话内」的时间触发（InteractiveToken）；
  无人登录/跨机/告警外发仍未开放。监控数据仍是本地 JSONL/摘要（M14-13
  契约），指标时序存储/查询、阈值随时间标定、外部告警接入未决。
- `production_ready=false` 不变；本目录工具绝不宣称生产就绪。
