# M14-129：Production Drift Watch 独立周期任务 readiness 管理器证据

## 1. 结论与边界

M14-129 交付 `tools/ops/production_drift_watch_task.py`（五命令
plan/generate/status/install/uninstall）与静默 wrapper
`tools/ops/run_production_drift_watch_silent.vbs`：把 M14-127
production drift watch（只读镜像锚点校验器）挂到**独立** Windows Task
Scheduler 周期任务的 readiness 面，为后续 supervisor 显式 install 做准备。

- **本切片只交付 readiness，不安装任务**：本开发回合零 `schtasks` 执行、
  零任务注册、零 Task Scheduler 改动、零真实 `production_drift_watch`
  plan/execute 运行、零 Docker 调用、零生产容器触碰。全部行为经
  FakeSchtasks/Fake repo/Fake 文件在契约测试中锁定（§5）。
- **readiness ≠ 任务已安装 ≠ 生产监控已上线**：截至本切片，任务
  `AIOS-Production-Drift-Watch` 未在任何机器注册，自然调度从未发生；
  `install` 需 supervisor 获准窗口 + 提升令牌 + 精确确认短语
  `EXECUTE PRODUCTION DRIFT WATCH SCHEDULER CHANGE`（一字不差）。
- **独立任务，不动 M14-14**：与 AIOS-Monitoring-Pipeline
  （M14-14 监控管道调度）互不接入、互不改动——不同任务名/URI/描述/
  wrapper 文件，两任务同机并存、各自独立管理（契约测试锁定，§5）。
  本切片未修改 `production_drift_watch.py`、`monitoring_pipeline*.py`、
  compose、任何生产配置与任何既有 evidence。
- 本工具只管理任务注册面，**从不运行 drift watch 本体**——运行是
  VBS→Task Scheduler 侧的 supervisor 获准行为；drift watch 自身的
  只读/secret/数据边界（M14-127 已锁定）不因挂上调度而放宽。

## 2. 任务身份与调度设计

| 项 | 值 |
|----|----|
| TaskName | `AIOS-Production-Drift-Watch`（固定） |
| URI | `urn:aios:m14-129:production-drift-watch`（注册后 Task Scheduler 归一化为 `\<TaskName>`，两形态均接受为「本任务名下」） |
| Description | 持久归属标记，含 `managed by tools/ops/production_drift_watch_task.py`（exact-owned 判定的必要条件） |
| 触发器 | TimeTrigger + Repetition Interval=PT15M（无 Duration=无限期）+ 过去 StartBoundary（注册即生效） |
| 执行时限 | ExecutionTimeLimit=PT10M |
| 多实例 | MultipleInstancesPolicy=IgnoreNew |
| 其他设置 | Hidden=true；StartWhenAvailable=true；电池不禁启不停（DisallowStartIfOnBatteries=false、StopIfGoingOnBatteries=false）；Principal InteractiveToken + LeastPrivilege |
| Action | `wscript.exe //B //Nologo "<repo>\tools\ops\run_production_drift_watch_silent.vbs"`；WorkingDirectory=repo |

### 预算交叉 pin（调度器绝不先于内部超时杀整任务）

drift watch 单轮最坏只读子进程硬顶（常量逐项取自
`tools/ops/production_drift_watch.py`，M14-127）：

```
compose ps 60s（COMPOSE_PS_TIMEOUT_SECONDS）
+ 7 容器 inspect × 30s（STACK_SERVICES 七服务 × INSPECT_TIMEOUT_SECONDS）
+ 2 image inspect × 30s（EXPECTED_ANCHORS 两锚点 × INSPECT_TIMEOUT_SECONDS）
= 330s
```

选择与断言（契约测试 `test_interval_covers_drift_watch_budget` 逐项
锁定，常量漂移即测试失败）：

```
PT15M=900s（间隔） > PT10M=600s（执行时限） > 330s（单轮硬顶）
```

600 − 330 = 270s 恒留报告写入与收尾余量；间隔 > 时限保证调度器侧
不自我重叠（另有 IgnoreNew 兜底）。

## 3. fail-closed 安全设计

与 `monitoring_pipeline_task.py`（M14-14）/ `windows_startup_task.py`
（M14-06 R2–R4 实证）同源的模式：

- **GatedSchtasks 结构性白名单**：只读查询固定两形态（全量列表 CSV / 单
  任务 /XML）；mutation 仅 install 的精确
  `/Create /TN AIOS-Production-Drift-Watch /XML <单 .xml 路径>`（绝不
  /F；`/XML` 后唯一 token 是值位置——POSIX tempfile 路径不误拒，旗标
  形态靠结构拒绝）与 uninstall 的
  `/Delete /TN AIOS-Production-Drift-Watch /F`（唯一允许 /F 的形态，
  仅 exact-owned 分支）。**绝不 /Run、/Change、/End**；非白名单形态在
  任何执行之前拒绝。
- **短语门禁先于一切**：install/uninstall 各需
  `--confirm "EXECUTE PRODUCTION DRIFT WATCH SCHEDULER CHANGE"`（一字
  不差）；短语缺失/近似（含 drift watch 自身的
  `EXECUTE READ-ONLY PRODUCTION DRIFT WATCH` 与 M14-14 的
  `EXECUTE MONITORING SCHEDULER CHANGE`——三个短语绝不互通）→ exit 1
  且**零 schtasks 调用**。
- **绝不覆盖、绝不误删**：install 前只读 query + 二次全量列表复核都
  确认 missing 才 /Create；同名任务存在（无论归属）一律拒绝。uninstall
  仅在 URI + Description 持久标记 + 全部关键字段 exact-owned 时才
  /Delete /F；foreign/missing（幂等 exit 0 零操作）/malformed/unknown
  绝不 force、绝不删除。
- **verify_task_xml 四态**（installed/missing/foreign/malformed）：
  适配 Task Scheduler 注册后归一化（URI 重写 `\<TaskName>`、默认值元素
  省略——仅在父节点在场且其余字段全部精确时按 Windows 默认值认可）；
  XML 解析前拒绝 DOCTYPE/ENTITY（XXE/实体膨胀防护）；归属判不明 =
  foreign（不可归属即非本工具）；字段缺失/漂移逐项报告字段名、不短路。
- **解码严格**：schtasks /XML 原始输出按字节形态严格解码四形态
  （UTF-16LE 无 BOM / LE BOM / BE BOM / ASCII/UTF-8 prolog）；之外按
  unknown fail-closed，绝不猜。
- **generate 产物字节契约**：UTF-16 **with BOM**（与 XML 声明及 install
  临时文件字节完全一致，外部解析器可直接加载——M14-14 R2 实证缺陷形态
  回归锁定）；原子写（tmp+fsync+os.replace）；symlink 输出拒绝零写入；
  落盘后回读磁盘真实字节复核归属；零 schtasks 调用。默认输出
  `.verify/artifacts/m14-129-production-drift-watch-task/scheduled-task.xml`
  （gitignored）。
- **源码/VBS 契约**（测试锁定）：零网络、零 env 读取、零 secret；
  subprocess 恒 list-argv 无 shell=True、Windows 侧恒
  CREATE_NO_WINDOW；源码无 `/Run` 字面量、`/F` 仅出现在 schtasks argv
  定义行；VBS 无盘符硬编码（repo 自脚本位置推导）、隐藏窗口
  `Run(...,0,True)`、退出码原样透传（`WScript.Quit exitCode`）、固定
  canonical `<repo>/.venv/Scripts/python.exe` 调用
  `production_drift_watch.py --execute --confirm "EXECUTE READ-ONLY
  PRODUCTION DRIFT WATCH"`（短语与工具常量交叉 pin）；预检缺失用专用
  退出码（2=venv python 缺失、3=脚本缺失、4=repo 缺失）。

退出码：plan 0=可安装/1 否；generate 0=已导出/2 路径或写拒绝；status
0=installed/1=unknown/2=missing/3=foreign/4=malformed；install 0=成功/
1=拒绝或失败；uninstall 0=已删或本就无（幂等）/3=foreign/4=malformed/
1=unknown 或失败。

## 4. 诚实边界与未实证事项

- **TimeTrigger/Repetition/StartBoundary 的注册后归一化行为未经真实安装
  实证**（M14-06 生产实证覆盖的是 LogonTrigger 形态；归一化容错按
  M14-06 先例条件认可）。首次 supervisor 注册若暴露归一化漂移，按
  malformed fail-closed（绝不弱解放行），处置走 M14-06 R3 同款修正回合。
- `schtasks /Create` 需提升令牌（M14-06 生产实证）；本工具不自动提升。
- 本切片零自然调度、零 drift 结论：没有任何一轮采集发生，没有
  drift=true/false 宣称，不解除/不改变任何 release gate；
  `production_ready=false` 不变。
- readiness 管理器就绪 ≠ 生产监控已上线：上线需要 supervisor 显式
  install + 首轮真实调度观察（后续切片/supervisor 回合）。

## 5. 开发回合验证记录（2026-09-25，本 worktree）

全部用 FakeSchtasks/Fake repo/Fake 文件，零真实 schtasks、零 Task
Scheduler 运行、零 drift watch plan/execute、零 Docker、零生产访问；
canonical venv `D:\AI Learning OS\ai-learning-os\.venv`（Python 3.11.15）；
pytest `--basetemp D:\AI Learning OS\.pytest-tmp\
m14-129-production-drift-watch-scheduler`：

| 套件 | 结果 |
|------|------|
| `services/api/tests/test_production_drift_watch_task.py`（新增） | **82 passed** |
| `services/api/tests/test_production_drift_watch.py`（M14-127 回归） | 96 passed |
| `services/api/tests/test_monitoring_pipeline_task.py`（M14-14 回归） | 79 passed |
| `services/api/tests/test_audit_archive_task.py`（M14-53 回归） | 53 passed |
| `services/api/tests/test_voice_sidecar_watchdog_task.py`（M14-77 回归） | 77 passed |
| 五套件合跑 | **387 passed / 0 skipped / 0 failed** |

静态验证：ruff check（新增 task 脚本 + 新增测试）0 违规；
`py_compile tools/ops/production_drift_watch_task.py` 通过；
`git diff --check` 干净；新增/修改文本 secret 扫描（sk-/AKIA/ghp_/
xoxb_/-----BEGIN）0 命中。

## 6. 改动清单（本切片，恰一个 commit）

| 文件 | 类型 |
|------|------|
| `tools/ops/production_drift_watch_task.py` | 新增（readiness 管理器） |
| `tools/ops/run_production_drift_watch_silent.vbs` | 新增（静默 wrapper） |
| `services/api/tests/test_production_drift_watch_task.py` | 新增（契约测试） |
| `docs/evidence/m14-129-production-drift-watch-task/README.md` | 新增（本证据） |
| `tools/ops/README.md` | 修改（新增 M14-129 段落） |

未修改 PROJECT_STATUS.md、ROADMAP.md、CHANGELOG.md（supervisor 边界）。
