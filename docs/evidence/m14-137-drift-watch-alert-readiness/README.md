# M14-137：production drift watch 告警分发调度桥 readiness（工具切片）

- 切片：分支 `ops/m14-137-drift-watch-alert-readiness`（独立 worktree
  `m14-137-drift-watch-alert-readiness`），**基于 main
  `f530ac385ca04d515bd40e903afa4814776a67a7`**（PR #225 merge =
  M14-138 合入，fetch 后 `git rev-parse origin/main` 逐字核对；R1 返工
  rebase 同步基座，原实现基点 `5eba63bc`（PR #224 merge）），单次 local commit（不推送、不建 PR；supervisor 审查与
  remote 发布在其后进行）。
- 目标：闭合 M14-129 计划任务自然累积的 drift-watch 报告
  （M14-127 产出）与 M14-135 告警分发 CLI 之间的**工具级**缺口——
  新增 fail-closed 桥接工具 `tools/ops/production_drift_watch_alert_task.py`
  + 聚焦契约测试，**不改任何既有工具语义**（M14-127 watcher /
  M14-135 dispatcher / M14-129 scheduler / VBS wrapper / 既有计划任务
  零触碰）。
- **边界性质（置顶）：本切片只交付调度桥工具 readiness——零计划任务
  安装/改动、零生产执行、零真实 webhook 外发、零 Docker/DB/MinIO/
  语音/secret 接触。`production_ready=false` 恒不变；本工具不构成
  alert delivery 的生产就绪或真实送达宣称。**

## 1. 工具设计（`tools/ops/production_drift_watch_alert_task.py`）

- **默认 plan（只读、零网络、零子进程、零写入）**：仅读取并校验报告，
  stdout 报告结论；本工具自身绝不构造 Transport、绝不读写 M14-135
  台账、绝不落盘任何文件（成功/拒绝都不写工件——契约测试以目录树
  前后快照锁定）。
- **校验单一事实源复用（绝不发明平行 schema）**：单报告读取 +
  schema/自洽校验直接复用 M14-135 `load_drift_report` /
  `validate_drift_report`，目录路径安全复用 M14-133
  `reject_path_problems`，文件名白名单/stamp 形态沿用 M14-135 常量，
  stdout 终防线复用 M14-119 `redact_secrets`——契约测试对被加载模块
  自身 import 的兄弟模块属性逐一 `is` 锁定。
- **最新报告选择（fail-closed，绝不回退）**：候选 = 文件名严格匹配
  `drift-watch-YYYYMMDD-HHMMSS.json`（伴生 `.md`/`plan-*.json` 只忽略
  只计数）；任一候选 stamp 非真实日历时刻（无法定序，如月 13）→
  拒绝；**最新候选必须完整通过 M14-135 校验**——malformed / plan
  模式 / 任何 schema 破损 → 可见拒绝且零移交，**绝不回退更旧报告**
  （过期的 drift=true 结论绝不冒充最新证据分发）；目录缺失/非目录/
  零候选 → 拒绝；`..` 组件与 symlink 目录组件拒绝（M14-133 语义）；目录枚举 OSError/权限（含 PermissionError）→ 固定 reason `artifacts-dir-unreadable` 拒绝（R1 修正——异常文本/本地路径零外泄，exit 2）。
  `--report` 精确指定单份报告时跳过扫描（与 `--artifacts-dir` 显式
  同给 → 拒绝，绝不猜来源）。
- **drift 判定只依据报告自身布尔**（绝不重判 Docker 状态）：
  drift=false → exit 0 + 显式 skipped-no-alerts（plan 与 execute 均
  零网络、零台账变更；execute 亦**不构造 dispatch 子进程**）；
  plan 且 drift=true → **exit 3** + stdout 报告选中报告的**精确
  SHA-256** 与 dispatch-would-be-required（零网络零移交；socket+
  subprocess 双阻断下照常——plan 零网络零子进程的结构性证明）。
- **execute 三重门禁（先于一切报告读取与子进程构造）**：
  `--execute` + `--confirm "EXECUTE PRODUCTION DRIFT WATCH ALERT
  TASK"`（一字不差；与 M14-127 watcher / M14-129 scheduler /
  M14-135 dispatcher 既有短语互不通用——契约测试四短语交叉 pin）+
  `--secret-file`（本地仅查存在性，内容不读取不回显，校验全留给
  M14-135）。缺一/近似 → exit 2 且零子进程构造。plan 带
  `--secret-file` → 拒绝（plan 零 secret 读取，防「plan 已校验过
  secret」错觉）。
- **移交 = 既有 M14-135 CLI + 其既有门禁（零削弱、零复制、零旁路）**：
  唯一放行的子进程形态经**结构性白名单门**（GatedDispatchRunner）
  逐 token 校验——`<sys.executable> <repo>/tools/ops/
  production_drift_watch_alert_dispatch.py --report <选中报告>
  --secret-file <操作者文件> [--artifact-dir <dir>] --execute
  --confirm "EXECUTE PRODUCTION DRIFT WATCH ALERT DISPATCH"`
  （确认短语是 **M14-135 自有常量**，非本工具短语）；错解释器/错
  工具/缺 --execute/任务短语冒充/追加旗标/值位置旗标形态/顺序错乱
  一律在任何执行之前拒绝。子进程退出码**原样透传**（成功 0 / 一切
  拒绝 2，含 duplicate-dispatch 与分发失败）；幂等（同报告 SHA-256
  重复分发在发送之前拒绝）与 sanitized 台账全部由 M14-135 既有语义
  承担。子进程墙钟预算 300s（M14-135 单次 POST 超时上界 30s + 启动/
  校验/落盘余量），超时按固定类别 fail-closed。
- **脱敏**：本工具 stdout 恒经 `redact_secrets` 终防线（M14-119 同一
  实现）；子进程 stdout/stderr 逐行**预脱敏后**再回显（不依赖 log
  汇自身）；日志只出现 stem/SHA-256/计数/固定词汇类别——**绝无
  URL/token/绝对本地路径**（契约测试以 tmp 目录字面量正反斜杠双检）。
- 退出码口径：0 = plan/execute 无需分发（skipped-no-alerts）或
  execute 移交成功；3 = plan 且 drift=true（需要分发——与拒绝严格
  区分的正向发现信号，供未来调度面「上次运行结果」可见）；2 = 一切
  fail-closed 拒绝（门禁/路径/选择/校验/子进程不可执行）与 M14-135
  移交拒绝透传。

## 2. 契约测试（29 项，0.3s 量级，纯合成 fixtures + 注入 FakeRunner）

`services/api/tests/test_production_drift_watch_alert_task.py`——
**构造上零真实子进程、零网络**：plan 路径在 socket+subprocess 双阻断
下照常成功；execute 移交只到注入 FakeRunner 为止（真实 M14-135 CLI
与真实网络绝不触达）；报告全部合成临时 fixtures（文件名 stamp 与
started 交叉校验 [stamp, stamp+2s] 自洽）。覆盖：

- 结构契约（1 项）：源码 import 白名单（纯标准库 + 两兄弟模块；零
  Docker/网络 token；`subprocess.run` 恰一次 = RealRunner 唯一触达点）、
  单一事实源 `is` 锁定、默认工件目录 == M14-133 `DEFAULT_INPUT_DIR`、
  四工具确认短语互不通用、移交目标为仓库内 M14-135 CLI、退出码口径。
- 移交白名单门（1 项）：9/11 token 精确形态放行（POSIX 绝对路径值
  合法——M14-14 PR #89 R3 同款结构判定）；12 种越界形态逐项拒绝。
- 选择 fail-closed（8 项）：最新定序 + 精确 SHA-256 + 忽略项计数；
  `--report` 精确覆盖与互斥拒绝；目录缺失/非目录/零候选（参数化 3
  形态）；最新 malformed（invalid-json / plan-mode / counts 破损，
  参数化 3 形态）**绝不回退**且零移交（旧份 drift=true 也不冒充）；
  候选 stamp 非真实日历时刻拒绝；`..` 组件拒绝（目录与 --report 双
  面）；symlink 工件目录拒绝（主机受限则 skip）；目录枚举 PermissionError（monkeypatch os.scandir）→ 固定 reason artifacts-dir-unreadable、exit 2、零 runner、异常文本/本地路径零外泄（R1）。
- drift=false / plan 零面（2 项）：plan 与 execute 双路 exit 0 +
  skipped-no-alerts + 零 runner 调用 + 目录树快照零写入（台账面
  绝无触碰）；socket+subprocess 双阻断下 plan 照常 exit 3。
- execute 门禁（2 项）：六种缺一/近似形态（缺 confirm / M14-135
  短语冒充 / M14-127 短语冒充 / 大小写近似 / 缺 secret / secret
  文件不存在）参数化——全部 exit 2 零 runner 调用；plan 带
  `--secret-file` 拒绝。
- 移交传播（4 项）：成功（恰一次白名单 argv、最新报告与 secret 值
  原样、M14-135 自有短语收尾、`--artifact-dir` 透传、stdout 回显、
  退出码 0）；分发失败与 duplicate-dispatch 拒绝（退出码 2 原样
  透传，各恰一次调用）；RunnerError 固定类别 exit 2；默认执行面
  GatedDispatchRunner 对非白名单 argv 在内层执行之前拒绝。
- 脱敏与路径安全（2 项）：投毒 report detail（secret 形态 marker）
  绝不入 stdout；子进程回显预脱敏（marker → `[REDACTED:token]`）；
  stdout 绝无绝对本地路径（tmp 字面量正反斜杠双检）；plan 成功与
  拒绝两种结局零写入（含 M14-135 台账目录零创建）。

## 3. 验证命令与结果（canonical venv）

- 基点核对：`git fetch origin && git rev-parse origin/main` →
  `5eba63bcb3fb1056a6c12764373ab197a21487e2`（PR #224 merge）；
  worktree 初始自原实现基点创建，R1 rebase 后单提交基于
  `f530ac38`（`git log --format=%P -1` parent 逐字等于该 SHA）。
- 新套件：`"D:/AI Learning OS/ai-learning-os/.venv/Scripts/python.exe"
  -m pytest services/api/tests/test_production_drift_watch_alert_task.py -q`
  → **29 passed**（含 R1 回归：monkeypatch os.scandir 抛 PermissionError
  → 固定 reason、exit 2、零 runner、无异常文本/本地路径泄露；重复跑稳定）。
- 邻居回归（六套合跑，同 venv）：M14-127 `test_production_drift_watch.py`
  （96）+ M14-129 `test_production_drift_watch_task.py`（82）+ M14-133
  `test_production_drift_watch_history.py`（47）+ M14-135
  `test_production_drift_watch_alert_dispatch.py`（127）+ M14-136
  `test_production_drift_watch_alert_dispatch_runtime.py`（4，回环受限
  主机 skip 语义不变）+ M14-137 新套件（29）→ **385 passed in 3.92s**。
- ruff：默认规则 + `--select F,E9` 双跑（工具 + 测试）→ All checks
  passed；`py_compile` 通过；`git diff --check` 干净；新增行秘密扫描
  0 命中（唯一 marker 为测试源码内合成投毒 sentinel
  `sk-ZXmarker0123456789`，仅存在于测试文件、断言其被脱敏，非真实
  secret）。
- 手工冒烟（pytest 之外，合成临时 fixtures、用毕即删）：plan 多候选
  定序 exit 3 + 精确 SHA-256；`--report` 旧报告 exit 0
  skipped-no-alerts；近似短语 exit 2；缺失目录 exit 2——与契约测试
  口径一致。

## 4. 诚实边界

- **零调度集成**：未安装/修改任何计划任务（`AIOS-Production-Drift-Watch`
  与其它任务零触碰）；把本桥挂入调度（drift=true 自动触发分发）仍是
  后续切片，且属 supervisor 获准窗口。
- **零生产执行、零真实外发**：execute 移交只被测试到注入 FakeRunner
  为止；真实 M14-135 子进程 + 真实 webhook（TLS 面/远端 3xx/限流）
  未在本切片实证（M14-136 已实证回环 HTTP 面；首次真实外发仍留待
  supervisor 获准窗口）。
- **不证明 alert delivery 生产就绪**：本工具只是桥接 readiness；
  `production_ready=false` 不变，release-approval 仍是 human-only 门。
- 选择语义的刻意取舍（已在工具 docstring 与测试锁定）：最新候选
  invalid 时**绝不回退**更旧报告（宁可拒绝、绝不以过期 drift 结论
  分发）；更旧候选文件自身 malformed 不阻断选择（只有当它新到无法
  定序或成为最新候选时才 fail-closed）。
