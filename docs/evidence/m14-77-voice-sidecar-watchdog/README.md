# M14-77：语音健康 sidecar 看护计划任务 readiness（韧性修复开发切片）

- 切片：分支 `ops/m14-77-voice-sidecar-resilience`（独立 worktree
  `m14-77-voice-sidecar-resilience`，基于 main `2a0e911`（PR #162 merge）），
  单次本地 commit（不推送）。
- 变更面（全部**加法式**）：`tools/voice/voice_sidecar_watchdog_task.py`
  （看护计划任务 readiness 管理器，742 行）+
  `tools/voice/run_voice_sidecar_watchdog_silent.vbs`（静默幂等 ensure 入口，
  52 行）+
  `services/api/tests/test_voice_sidecar_watchdog_task.py`（77 项契约测试）+
  本 README + `tools/voice/README.md`（M14-77 节）+ `docs/ROADMAP.md` +
  `docs/PROJECT_STATUS.md` + `docs/CHANGELOG.md`。**零改动**
  `production_monitor.py` / `monitoring_pipeline.py` /
  `monitoring_pipeline_task.py` / `voice_health_sidecar.py` /
  `voice_health_sidecar_control.py` / 既有 VBS / 既有任务 XML 契约。
- **零生产触碰（本开发回合）**：零计划任务注册/安装/卸载（工具短语门禁 +
  仅 FakeSchtasks 注入测试）；零 wsl.exe 调用；零 Docker/WSL/引擎/sidecar
  启停（sidecar 从未启动）；零真实网络/健康端点访问；零 schtasks 写路径。

## 1. 触发事实与故障机制链（离线审计复原）

生产事实（任务陈述，2026-09-21）：FunASR/CosyVoice 引擎均
managed-running、health 200；voice_health_sidecar PID 6660 于 01:00 后
静默退出；15 分钟监控管道连续约 7 小时 monitor exit 2，long-soak 被污染。

机制链（全部环节经仓库代码/证据离线核实，非推测）：

```
Task Scheduler AIOS-Monitoring-Pipeline（PT15M，M14-22 注册）
  → run_monitoring_pipeline_silent.vbs → monitoring_pipeline.py --execute
    → 固定 argv 步骤 1：production_monitor.py --execute
      --voice-health-source sidecar        ← monitoring_pipeline.py:237（M14-27 切换）
      → load_sidecar_manifest()：仅静态文件校验（schema/pid/ports/bind 格式，
        不核验进程存活——production_monitor.py:262-303）
      → sidecar 已死但 manifest 残留且静态合法 → 校验通过 → 派生
        http://<bind>:18010/health、:18011/health
      → GET 连接失败 → 2×endpoint-status critical → overall=critical
      → monitor exit 2（EXIT_CRITICAL——fail-closed 正确、不伪造健康）
    → history/insights 按序列契约 skipped（monitor 非 0 不执行）
  → pipeline rc=1，15 分钟一轮持续失败
```

**根本缺陷**：sidecar 是「spawn 一次、无看护」的**未托管**进程——生产拓扑
中唯一无自恢复能力的组件（引擎属托管生命周期 `managed-running`，重启后可
经受管路径恢复；sidecar 由控制器经 `wsl.exe --exec` 一次性 spawn，无人拉起）。
控制器的幂等自愈早已存在（`cmd_start`：stale manifest → 清理 → 全新启动，
全部生产保护核验保留，113 项测试覆盖），**但生产中无任何周期性调用者**——
唯一触发是人工，这就是 7 小时断档的成因。旁证：sidecar PID 漂移
701（M14-28，2026-09-15）→ 6660（本次），说明历史上已发生过至少一次
死亡-重启循环。

## 2. 根因假设（01:00 死亡的直接诱因——离线不可定证，如实列举）

| 假设 | 机制 | 旁证/反证 |
|------|------|-----------|
| H1（最可能）：WSL VM/发行版维护性重启或 wsl.exe 中继回收 | 一次性 spawn 的 sidecar 随 WSL 会话终止；引擎经托管路径/后续干预恢复 managed-running，sidecar 无人拉起 | 引擎存活 + sidecar 死亡的**托管不对称**正是断档形态；01:00 属系统维护高发窗口；PID 漂移史 |
| H2：sidecar 进程被内核/OOM 终止 | 单进程死，WSL 未重启 | 无法离线取证（dmesg/日志在 WSL 内，本回合只读约束不采证） |
| H3：异常/信号退出 | SIGTERM 默认终止无日志＝「静默」；serve_forever 主循环一般不抛，handler 线程异常被 ThreadingHTTPServer.handle_error 吞 | sidecar 控制日志（sidecar-control.log）在 WSL 侧 artifacts，本回合未读取 |

三个假设均**不可离线定证**，但都收敛于同一修复面：死亡不可避免（单进程
无自愈能力），必须由**存活于 Windows 侧的周期性 ensure** 把暴露窗收敛到
有界值。后续获准窗口可用 `sidecar-control.log` 与 WSL 侧系统日志复核 H1-H3。

## 3. 方案与边界（最小安全）

**交付**：看护计划任务 readiness（Windows 侧周期性幂等 ensure），把
sidecar 死亡暴露窗从「人工介入前无限」收敛到 **≤5 分钟**（PT5M 看护周期
< PT15M 监控周期——15 分钟管道最多污染一轮）。

- `voice_sidecar_watchdog_task.py`（M14-14 `monitoring_pipeline_task.py`
  同款纪律移植）：任务 `AIOS-Voice-Sidecar-Watchdog`
  （`urn:aios:m14-77:voice-sidecar-watchdog`），plan/generate/status/
  install/uninstall 五子命令；GatedSchtasks 结构性白名单（四形态精确放行，
  `/Run`//`/Change`//`/End` 等任何执行前拒绝）；install/uninstall 精确确认
  短语（`EXECUTE VOICE SIDECAR WATCHDOG SCHEDULER CHANGE`，与 M14-14
  短语隔离）；绝不覆盖同名（前置 query + 二次列表复核）；归属判定
  fail-closed（URI 两形态 + Description 持久标记 + 全字段精确；归一化省略
  默认值按 M14-06/M14-14 先例条件认可）；/XML 字节形态严格四形态解码；
  DOCTYPE/ENTITY 解析前拒绝；generate 产物 UTF-16 with BOM + 回读复核。
- `run_voice_sidecar_watchdog_silent.vbs`（与 M14-14 VBS diff 结构同构）：
  静默调用 `voice_health_sidecar_control.py start`——幂等 ensure 语义完全
  复用既有控制器（活着跳过/死了以全部生产保护核验重启/WSL 不可用 rc 3
  可见失败），退出码原样透传给 Task Scheduler。
- 预算链（测试交叉 pin，常量级锁定）：PT5M 间隔 ＞ PT4M 执行时限 ＞ 90s
  单轮 ensure 预算（控制器 `start` 最坏推算 probe×2 + status 落档等待 +
  幂等分支双端口健康探测 + 启动余量）；**独立任务独立时限**，不挤占监控
  管道 PT12M 执行预算（管道三步硬顶之和 710s 的既有预算链不动）；
  IgnoreNew + 控制器 ControlLock 双重防重叠。

**明确不做（边界）**：

- **production_monitor 零改动**：只读、fail-closed、不伪造健康的语义原样
  保留——sidecar 死亡时 monitor 照样如实 exit 2（manifest 静态校验不加
  存活探测；这是监控侧 fail-closed 的既有设计而非缺陷）。本切片只消灭
  「长期」断档，不消灭「单轮」如实失败。
- **monitoring_pipeline / 既有 VBS / 既有任务零改动**：不在监控管道内嵌
  ensure 步骤（会破坏管道只读采集契约与 PT12M 执行预算链 710s＜720s）。
- **不做 WSL 内 supervisor 循环**：stop 语义/身份标记契约复杂化、不覆盖
  WSL VM 重启场景、非最小。
- **本回合零安装零注册**：install 是 supervisor-only（提升令牌 + 获准窗口
  + 确认短语）；工具默认 plan/generate/status 全只读。
- 运维语义：看护在线期间 sidecar 期望恒运行——人工维护前先
  `uninstall`（或 schtasks /Change /Disable），否则 stop 后 ≤5 分钟内会被
  幂等 ensure 重新拉起（设计意图）。
- `production_ready=false` 不变；本切片不构成 24h soak 完成或语音链路
  生产就绪宣称。

## 4. 真实测试结果（2026-09-21，开发回合实测）

解释器：`D:\AI Learning OS\ai-learning-os\.venv\Scripts\python.exe`
（主仓 canonical venv；Python 3.11，pytest 9.1.1，ruff 0.16.5）。
工作目录：本 worktree 仓库根。

| 命令 | 结果 |
|------|------|
| `python -m pytest services/api/tests/test_voice_sidecar_watchdog_task.py` | **77 passed**（新增契约测试） |
| `python -m pytest services/api/tests/test_voice_health_sidecar.py` | **113 passed**（sidecar/控制器零破坏） |
| `python -m pytest services/api/tests/test_monitoring_pipeline_task.py` | **79 passed**（监控任务管理器零破坏） |
| `python -m pytest services/api/tests/test_monitoring_pipeline.py` | **69 passed**（管道零破坏） |
| `python -m pytest services/api/tests/test_production_monitor.py` | **228 passed**（monitor 零破坏） |
| 五件套聚合 | **566 passed in 6.49s** |
| `python -m ruff check tools/voice/voice_sidecar_watchdog_task.py services/api/tests/test_voice_sidecar_watchdog_task.py` | **All checks passed!**（初跑 3 errors 均为测试文件未用变量，窄修正后全过） |
| `python -m py_compile`（两 Python 文件） | exit 0 |
| `git diff --check` | exit 0（无空白错误） |
| `diff run_monitoring_pipeline_silent.vbs run_voice_sidecar_watchdog_silent.vbs` | 代码体结构同构（仅注释/变量名/调用目标差异——M14-14 生产实证形态复制） |

新测试覆盖面（77 项）：Task XML 固定身份与关键设置逐项断言；预算链交叉
pin（含控制器常量同源推算与监控管道间隔对比）；verify 四态（round-trip/
归一化条件认可/字段漂移逐项报字段名/garbage/DOCTYPE-ENTITY/foreign URI/
Command/Arguments）；decode 四形态与拒绝面；白名单门（只读两形态/mutation
精确形态/`/Run`//`/Change`//`/End`/带 `/F` create/POSIX tempfile 值位置
回归）；plan（missing+预检过/无 venv/已存在/事实不完整）；generate
（UTF-16 BOM 字节断言+回读 installed+零 schtasks+默认目录+symlink 拒绝）；
status 五态；install/uninstall 短语门禁（零调用）+ happy path（临时 XML
字节=build_task_xml 的 UTF-16、用后即删）+ 拒绝同名 + exact-owned 才删 +
missing 幂等 + foreign/malformed/unknown 零删除；VBS 契约（无盘符/隐藏
窗口/rc 透传/venv python/controller 目标/恒带 `start`/零 secret/零
http-powershell-cmd）；与 M14-14、M14-06 任务零身份冲突 + 确认短语隔离；
源码契约（零 `"/Run"`、`"/Create"` 行零 `/F`、零 shell=、零 secret 形态）；
repo paths pin。

## 5. 后续生产注册步骤（supervisor-only，获准窗口 + 提升令牌）

按序执行；每步零生产代理/引擎/容器触碰，全部经本切片工具或既有控制器：

1. **预检**（只读）：`python tools/voice/voice_sidecar_watchdog_task.py
   plan`——预期输出「任务状态: missing」+ 预检全过（repo 根/sidecar
   控制器/sidecar 脚本/VBS/venv python 五项在位）。
2. **导出审查**（零调度器改动）：`python tools/voice/
   voice_sidecar_watchdog_task.py generate`——产物
   `.verify/artifacts/m14-77-voice-sidecar-watchdog/scheduled-task.xml`
   （UTF-16 with BOM），回读 state=installed；供人工审查 PT5M/PT4M/
   IgnoreNew/Hidden/LeastPrivilege/Action。
3. **安装**（提升令牌）：`python tools/voice/voice_sidecar_watchdog_task.py
   install --confirm "EXECUTE VOICE SIDECAR WATCHDOG SCHEDULER CHANGE"`
   ——预期 rc 0；安装后工具自动复查（归一化回读逐字段）。
4. **状态核对**（只读）：`python tools/voice/
   voice_sidecar_watchdog_task.py status`——预期 state=installed。
5. **首轮自愈验证**（受控破坏-恢复演练，需协调）：人工
   `voice_health_sidecar_control.py stop` → 等待 ≤5 分钟看护轮 →
   `status` 应回升 running-healthy（或 running-degraded——取决于引擎
   状态，如实）+ sidecar-control.log 出现全新启动记录；同时观察监控管道
   下一轮 monitor 是否恢复（若引擎健康则 200/200）。**演练前确认
   long-soak 窗口不受该轮污染（或选择性地把演练窗口记为已知事件）**。
6. **登记后续观察**：Task Scheduler「上次运行结果」非零（rc 1/3）= 可见
   失败不遮蔽；若出现连续 rc 3（WSL 管理面不可用）按 M14-25 同源环境现象
   记录，不据此变更看护语义。
7. **回滚**（如需）：`python tools/voice/voice_sidecar_watchdog_task.py
   uninstall --confirm "EXECUTE VOICE SIDECAR WATCHDOG SCHEDULER CHANGE"`
   （仅删本工具精确拥有的任务）。

## 6. 诚实边界

- 01:00 死亡的直接诱因（H1/H2/H3）本回合未定证——离线只读约束下不采
  WSL 侧证据；§2 假设清单供获准窗口复核。
- 本切片交付的是 **readiness**（工具+wrapper+测试+文档）；真实注册、
  自愈演练、长期稳定性观察均未发生——看护当前**不在生产运行**，sidecar
  断档风险在注册前仍然存在。
- 监控管道在 sidecar 死亡轮次仍会如实 exit 2（单轮失败是有意的
  fail-closed 语义，非本切片修复目标）；本切片的成就是断档窗口
  「人工介入前无限 → 注册后 ≤5 分钟」。
- PT5M/PT4M 预算是从控制器常量推导的最坏推算（测试 pin），未经真实
  注册后的长窗口实证；首次注册若暴露 Task Scheduler 归一化漂移，按
  malformed fail-closed（绝不弱解放行），处置走 M14-06 R3 同款修正回合。
- `production_ready=false` 不变；不构成 24h soak、语音链路生产就绪、
  或任何监控语义变更的宣称。
